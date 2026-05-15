#!/usr/bin/env python3
"""MIPS32 子集模拟器，用于测试 SNL 编译器生成的汇编代码。

支持的指令集：算术、访存、分支跳转、系统调用（I/O 和退出）。
内存模型：数据段从 0x10010000 开始，栈从 0x7FFFEFFC 向下增长。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


class RunnerError(RuntimeError):
    pass


class MIPSRunner:
    """MIPS32 子集解释器。逐条执行指令，模拟寄存器和内存。"""

    def __init__(self, assembly: str, inputs: list[str]) -> None:
        self.assembly = assembly
        self.inputs = inputs          # 预设的输入值队列（供 read syscall 消费）
        self.output: list[str] = []   # 输出缓冲区
        self.regs: dict[str, int] = {name: 0 for name in self.register_names()}
        self.regs["$sp"] = 0x7FFFEFFC  # 栈指针初始值
        self.regs["$fp"] = self.regs["$sp"]
        self.regs["$zero"] = 0
        self.memory: dict[int, int] = {}       # 稀疏内存（地址→字值）
        self.data_labels: dict[str, int] = {}  # 数据段标签→地址
        self.text_labels: dict[str, int] = {}  # 代码段标签→指令索引
        self.instructions: list[str] = []
        self.parse_assembly()

    @staticmethod
    def register_names() -> list[str]:
        return (
            ["$zero", "$at", "$v0", "$v1", "$a0", "$a1", "$a2", "$a3"]
            + [f"$t{i}" for i in range(10)]
            + [f"$s{i}" for i in range(8)]
            + ["$sp", "$fp", "$ra"]
        )

    def parse_assembly(self) -> None:
        section = ""
        data_addr = 0x10010000
        for raw in self.assembly.splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            if line == ".data":
                section = "data"
                continue
            if line == ".text":
                section = "text"
                continue
            if line.startswith(".globl"):
                continue
            if section == "data":
                if ":" not in line:
                    continue
                label, rest = [part.strip() for part in line.split(":", 1)]
                self.data_labels[label] = data_addr
                if rest.startswith(".word"):
                    values = rest[len(".word") :].strip()
                    words = [int(v.strip()) for v in values.split(",")] if values else [0]
                    for value in words:
                        self.memory[data_addr] = value
                        data_addr += 4
                elif rest.startswith(".space"):
                    size = int(rest[len(".space") :].strip())
                    for addr in range(data_addr, data_addr + size, 4):
                        self.memory[addr] = 0
                    data_addr += size
            elif section == "text":
                if line.endswith(":"):
                    self.text_labels[line[:-1]] = len(self.instructions)
                else:
                    self.instructions.append(line)

    def reg(self, name: str) -> int:
        return self.regs.get(name, 0)

    def set_reg(self, name: str, value: int) -> None:
        """设置寄存器值，$zero 寄存器始终为 0。所有值截断为 32 位。"""
        if name != "$zero":
            self.regs[name] = value & 0xFFFFFFFF

    def signed(self, value: int) -> int:
        value &= 0xFFFFFFFF
        return value - 0x100000000 if value & 0x80000000 else value

    def run(self) -> str:
        pc = self.text_labels.get("main", 0)
        steps = 0
        while 0 <= pc < len(self.instructions):
            steps += 1
            if steps > 100000:
                raise RunnerError("MIPS runner exceeded 100000 steps")
            next_pc = pc + 1
            inst = self.instructions[pc]
            op, args = self.split_inst(inst)

            if op == "li":
                self.set_reg(args[0], int(args[1]))
            elif op == "la":
                self.set_reg(args[0], self.data_labels[args[1]])
            elif op == "move":
                self.set_reg(args[0], self.reg(args[1]))
            elif op == "lw":
                self.set_reg(args[0], self.memory.get(self.address(args[1]), 0))
            elif op == "sw":
                self.memory[self.address(args[1])] = self.reg(args[0])
            elif op == "add":
                self.set_reg(args[0], self.reg(args[1]) + self.reg(args[2]))
            elif op == "addi":
                self.set_reg(args[0], self.reg(args[1]) + int(args[2]))
            elif op == "sub":
                self.set_reg(args[0], self.reg(args[1]) - self.reg(args[2]))
            elif op == "mul":
                self.set_reg(args[0], self.reg(args[1]) * self.reg(args[2]))
            elif op == "div":
                divisor = self.signed(self.reg(args[2]))
                if divisor == 0:
                    raise RunnerError("MIPS runtime division by zero")
                self.set_reg(args[0], int(self.signed(self.reg(args[1])) / divisor))
            elif op in {"beq", "bne", "bge", "blt"}:
                left = self.signed(self.reg(args[0]))
                right = self.signed(self.reg(args[1]))
                jump = (
                    (op == "beq" and left == right)
                    or (op == "bne" and left != right)
                    or (op == "bge" and left >= right)
                    or (op == "blt" and left < right)
                )
                if jump:
                    next_pc = self.text_labels[args[2]]
            elif op == "j":
                next_pc = self.text_labels[args[0]]
            elif op == "jal":
                self.set_reg("$ra", next_pc)
                next_pc = self.text_labels[args[0]]
            elif op == "jr":
                next_pc = self.reg(args[0])
            elif op == "syscall":
                if self.handle_syscall():
                    break
            else:
                raise RunnerError(f"unsupported MIPS instruction: {inst}")

            self.regs["$zero"] = 0
            pc = next_pc
        return "".join(self.output)

    @staticmethod
    def split_inst(inst: str) -> tuple[str, list[str]]:
        if " " not in inst:
            return inst, []
        op, rest = inst.split(None, 1)
        return op, [arg.strip() for arg in rest.split(",")]

    def address(self, operand: str) -> int:
        operand = operand.strip()
        if operand in self.data_labels:
            return self.data_labels[operand]
        match = re.fullmatch(r"(-?\d+)\((\$[A-Za-z0-9]+)\)", operand)
        if not match:
            raise RunnerError(f"unsupported memory operand: {operand}")
        return self.reg(match.group(2)) + int(match.group(1))

    def handle_syscall(self) -> bool:
        code = self.reg("$v0")
        if code == 1:
            self.output.append(str(self.signed(self.reg("$a0"))))
        elif code == 5:
            self.set_reg("$v0", int(self.inputs.pop(0)) if self.inputs else 0)
        elif code == 10:
            return True
        elif code == 11:
            self.output.append(chr(self.reg("$a0") & 0xFF))
        elif code == 12:
            if self.inputs:
                item = self.inputs.pop(0)
                value = ord(item[0]) if not item.lstrip("-").isdigit() else int(item)
            else:
                value = 0
            self.set_reg("$v0", value)
        else:
            raise RunnerError(f"unsupported syscall code {code}")
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run MIPS assembly emitted by the SNL compiler.")
    parser.add_argument("assembly", type=Path, help="MIPS assembly file")
    parser.add_argument("--input", nargs="*", default=[], help="values consumed by READ syscalls")
    args = parser.parse_args(argv)

    try:
        print(MIPSRunner(args.assembly.read_text(encoding="utf-8"), list(args.input)).run(), end="")
    except (OSError, RunnerError, ValueError) as exc:
        print(f"snl_runner.py: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

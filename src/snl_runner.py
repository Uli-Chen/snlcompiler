#!/usr/bin/env python3
"""Run the MIPS subset emitted by the SNL compiler."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path


class RunnerError(RuntimeError):
    pass


class MIPSRunner:
    def __init__(self, assembly: str, inputs: list[str], max_steps: int = 100000) -> None:
        self.assembly = assembly
        self.inputs = inputs
        self.max_steps = max_steps
        self.output: list[str] = []
        self.regs: dict[str, int] = {name: 0 for name in self.register_names()}
        self.initial_sp = 0x7FFFEFFC
        self.min_sp = self.initial_sp
        self.regs["$sp"] = self.initial_sp
        self.regs["$fp"] = self.regs["$sp"]
        self.regs["$zero"] = 0
        self.memory: dict[int, int] = {}
        self.data_labels: dict[str, int] = {}
        self.text_labels: dict[str, int] = {}
        self.pc_labels: dict[int, list[str]] = {}
        self.instructions: list[str] = []
        self.op_counts: Counter[str] = Counter()
        self.label_counts: Counter[str] = Counter()
        self.branch_taken: Counter[str] = Counter()
        self.branch_not_taken: Counter[str] = Counter()
        self.taken_conditional_branches = 0
        self.not_taken_conditional_branches = 0
        self.steps = 0
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
                    label = line[:-1]
                    pc = len(self.instructions)
                    self.text_labels[label] = pc
                    self.pc_labels.setdefault(pc, []).append(label)
                else:
                    self.instructions.append(line)

    def reg(self, name: str) -> int:
        return self.regs.get(name, 0)

    def set_reg(self, name: str, value: int) -> None:
        if name != "$zero":
            self.regs[name] = value & 0xFFFFFFFF
            if name == "$sp":
                self.min_sp = min(self.min_sp, self.regs[name])

    def signed(self, value: int) -> int:
        value &= 0xFFFFFFFF
        return value - 0x100000000 if value & 0x80000000 else value

    def run(self) -> str:
        pc = self.text_labels.get("main", 0)
        while 0 <= pc < len(self.instructions):
            self.steps += 1
            if self.steps > self.max_steps:
                raise RunnerError(f"MIPS runner exceeded {self.max_steps} steps")
            for label in self.pc_labels.get(pc, []):
                self.label_counts[label] += 1
            next_pc = pc + 1
            inst = self.instructions[pc]
            op, args = self.split_inst(inst)
            self.op_counts[op] += 1

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
            elif op == "sll":
                self.set_reg(args[0], self.reg(args[1]) << int(args[2]))
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
                    self.taken_conditional_branches += 1
                    self.branch_taken[args[2]] += 1
                    next_pc = self.text_labels[args[2]]
                else:
                    self.not_taken_conditional_branches += 1
                    self.branch_not_taken[args[2]] += 1
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

    def stats(self) -> dict[str, int]:
        memory_loads = self.op_counts["lw"]
        memory_stores = self.op_counts["sw"]
        arithmetic_ops = sum(self.op_counts[op] for op in ("add", "addi", "sub", "mul", "div"))
        branch_ops = sum(self.op_counts[op] for op in ("beq", "bne", "bge", "blt", "j", "jal", "jr"))
        conditional_branch_ops = sum(self.op_counts[op] for op in ("beq", "bne", "bge", "blt"))
        return {
            "static_instructions": len(self.instructions),
            "dynamic_steps": self.steps,
            "memory_loads": memory_loads,
            "memory_stores": memory_stores,
            "memory_ops": memory_loads + memory_stores,
            "arithmetic_ops": arithmetic_ops,
            "branch_ops": branch_ops,
            "conditional_branch_ops": conditional_branch_ops,
            "taken_conditional_branches": self.taken_conditional_branches,
            "syscalls": self.op_counts["syscall"],
            "max_stack_words": (self.initial_sp - self.min_sp) // 4,
        }

    def profile(self) -> dict[str, object]:
        labels = {label: count for label, count in sorted(self.label_counts.items())}
        branches = {
            label: {
                "taken": self.branch_taken[label],
                "not_taken": self.branch_not_taken[label],
            }
            for label in sorted(set(self.branch_taken) | set(self.branch_not_taken))
        }
        return {
            "format": "snl-mips-profile-v1",
            "labels": labels,
            "branches": branches,
            "stats": self.stats(),
        }

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
        """处理 MIPS syscall 指令，返回 True 表示程序应退出。

        支持的 syscall 编号（与 SPIM/MARS 兼容）：
          1  — 打印整数（$a0）
          5  — 读取整数 → $v0
          10 — 退出程序
          11 — 打印字符（$a0 低 8 位）
          12 — 读取字符 → $v0
        """
        code = self.reg("$v0")
        if code == 1:
            self.output.append(str(self.signed(self.reg("$a0"))))
        elif code == 5:
            # 读取整数输入；输入为空时返回 0，输入非法整数时抛出友好错误
            if self.inputs:
                raw = self.inputs.pop(0)
                try:
                    self.set_reg("$v0", int(raw))
                except ValueError:
                    raise RunnerError(f"read syscall: expected integer input, got {raw!r}")
            else:
                self.set_reg("$v0", 0)
        elif code == 10:
            return True
        elif code == 11:
            self.output.append(chr(self.reg("$a0") & 0xFF))
        elif code == 12:
            # 读取字符输入：若输入是纯数字则当作字符编码，否则取第一个字符的 ASCII 值
            if self.inputs:
                item = self.inputs.pop(0)
                value = int(item) if item.lstrip("-").isdigit() else ord(item[0])
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
    parser.add_argument("--max-steps", type=int, default=100000, help="stop after this many executed instructions")
    parser.add_argument("--stats", action="store_true", help="print execution statistics to stderr")
    parser.add_argument("--profile-out", type=Path, help="write execution profile as JSON")
    args = parser.parse_args(argv)

    try:
        runner = MIPSRunner(args.assembly.read_text(encoding="utf-8"), list(args.input), max_steps=args.max_steps)
        print(runner.run(), end="")
        if args.stats:
            for name, value in runner.stats().items():
                print(f"{name}: {value}", file=sys.stderr)
        if args.profile_out:
            args.profile_out.parent.mkdir(parents=True, exist_ok=True)
            args.profile_out.write_text(json.dumps(runner.profile(), indent=2, sort_keys=True), encoding="utf-8")
    except (OSError, RunnerError, ValueError) as exc:
        print(f"snl_runner.py: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

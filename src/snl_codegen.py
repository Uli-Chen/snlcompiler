#!/usr/bin/env python3
"""SNL to MIPS32 code generation through quadruple IR."""

from __future__ import annotations

import argparse
import math
import re
import sys
from pathlib import Path

from snl_ir import IRProcedure, IRProgram, IRUnit, Operand, Quad, flatten_procedures, is_temp
from snl_irgen import SNLIRGenerator
from snl_lexer import DEFAULT_GRAMMAR, SNLLexer, load_grammar
from snl_optimizer import optimize_program
from snl_parser import Program, SNLParser, Token
from snl_semantic import CHAR, INTEGER, UNKNOWN, SNLSemanticAnalyzer, Symbol, TypeInfo


class CodegenError(RuntimeError):
    pass


# 栈帧布局常量（相对于 $fp 的偏移）
WORD_SIZE = 4
FP_SAVE_OFFSET = 0       # 保存调用者 $fp
RA_SAVE_OFFSET = 4       # 保存 $ra
STATIC_LINK_OFFSET = 8   # 静态链（指向词法父作用域的 $fp）
FRAME_HEADER_SIZE = 8    # prologue 保存的字节数（$fp + $ra）


class RegisterPool:
    def __init__(self) -> None:
        self.available = [f"$t{i}" for i in range(9, -1, -1)]

    def alloc(self) -> str:
        if not self.available:
            raise CodegenError("temporary register pool exhausted")
        return self.available.pop()

    def free(self, reg: str | None) -> None:
        if reg and reg.startswith("$t") and reg not in self.available:
            self.available.append(reg)


class MIPSProgram:
    def __init__(self) -> None:
        self.data: list[str] = [".data"]
        self.text: list[str] = [".text", ".globl main", "j main"]

    def emit_data(self, line: str) -> None:
        self.data.append(line)

    def emit(self, line: str = "") -> None:
        self.text.append(line)

    def render(self, *, optimize: bool = True) -> str:
        text = peephole_optimize(self.text) if optimize else self.text
        return "\n".join(self.data + [""] + text) + "\n"


def peephole_optimize(lines: list[str]) -> list[str]:
    optimized = list(lines)
    changed = True
    while changed:
        changed = False
        next_pass: list[str] = []
        index = 0
        while index < len(optimized):
            line = optimized[index]
            if is_useless_self_move(line) or is_useless_addi_zero(line):
                changed = True
                index += 1
                continue
            if is_jump_to_next_label(line, optimized, index):
                changed = True
                index += 1
                continue
            folded = fold_immediate_binary(line, optimized, index)
            if folded is not None:
                changed = True
                next_pass.extend(folded)
                index += 2
                continue
            if is_overwritten_immediate_load(line, optimized, index):
                changed = True
                index += 1
                continue
            next_pass.append(line)
            index += 1
        optimized = next_pass
    return optimized


def split_mips_inst(line: str) -> tuple[str, list[str]] | None:
    stripped = line.strip()
    if not stripped or stripped.endswith(":") or stripped.startswith("."):
        return None
    if " " not in stripped:
        return stripped, []
    op, rest = stripped.split(None, 1)
    return op, [arg.strip() for arg in rest.split(",")]


def is_useless_self_move(line: str) -> bool:
    parsed = split_mips_inst(line)
    return parsed is not None and parsed[0] == "move" and len(parsed[1]) == 2 and parsed[1][0] == parsed[1][1]


def is_useless_addi_zero(line: str) -> bool:
    parsed = split_mips_inst(line)
    return (
        parsed is not None
        and parsed[0] == "addi"
        and len(parsed[1]) == 3
        and parsed[1][0] == parsed[1][1]
        and parsed[1][2] == "0"
    )


def is_jump_to_next_label(line: str, lines: list[str], index: int) -> bool:
    parsed = split_mips_inst(line)
    if parsed is None or parsed[0] != "j" or len(parsed[1]) != 1:
        return False
    next_index = index + 1
    while next_index < len(lines) and not lines[next_index].strip():
        next_index += 1
    return next_index < len(lines) and lines[next_index].strip() == f"{parsed[1][0]}:"


def fold_immediate_binary(line: str, lines: list[str], index: int) -> list[str] | None:
    first = split_mips_inst(line)
    if first is None or first[0] != "li" or len(first[1]) != 2:
        return None
    if index + 1 >= len(lines):
        return None
    second = split_mips_inst(lines[index + 1])
    if second is None or len(second[1]) != 3:
        return None

    imm_reg, imm_text = first[1]
    try:
        imm = int(imm_text)
    except ValueError:
        return None

    op, args = second
    dest, left, right = args
    if imm_reg not in {left, right}:
        return None

    other = right if left == imm_reg else left
    if op == "add" and imm == 0:
        return [f"move {dest}, {other}"]
    if op == "sub" and imm == 0 and right == imm_reg:
        return [f"move {dest}, {left}"]
    if op == "mul":
        if imm == 0:
            return [f"li {dest}, 0"]
        if imm == 1:
            return [f"move {dest}, {other}"]
    if op == "div" and imm == 1 and right == imm_reg:
        return [f"move {dest}, {left}"]
    return None


def is_overwritten_immediate_load(line: str, lines: list[str], index: int) -> bool:
    parsed = split_mips_inst(line)
    if parsed is None or parsed[0] not in {"li", "la"} or len(parsed[1]) != 2:
        return False
    next_index = index + 1
    while next_index < len(lines) and not lines[next_index].strip():
        next_index += 1
    if next_index >= len(lines):
        return False
    next_parsed = split_mips_inst(lines[next_index])
    return (
        next_parsed is not None
        and next_parsed[0] in {"li", "la"}
        and len(next_parsed[1]) == 2
        and next_parsed[1][0] == parsed[1][0]
    )


def type_size_words(type_info: TypeInfo) -> int:
    if type_info.kind in {"integer", "char", "bool", "unknown"}:
        return 1
    if type_info.kind == "array":
        low = type_info.low if type_info.low is not None else 0
        high = type_info.high if type_info.high is not None else low
        return max(0, high - low + 1) * type_size_words(type_info.element or UNKNOWN)
    if type_info.kind == "record":
        return sum(type_size_words(field_type) for field_type in type_info.fields.values())
    return 1


def field_offset_words(record_type: TypeInfo, field_name: str) -> int:
    offset = 0
    for name, field_type in record_type.fields.items():
        if name == field_name:
            return offset
        offset += type_size_words(field_type)
    raise CodegenError(f"record has no field {field_name!r}")


def mangle(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z_]", "_", text)


def is_power_of_two(value: int) -> bool:
    return value > 0 and value & (value - 1) == 0


class IRMIPSGenerator:
    def __init__(self, ir: IRProgram, *, optimize: bool = True) -> None:
        self.ir = ir
        self.optimize = optimize
        self.program = MIPSProgram()
        self.regs = RegisterPool()
        self.current_unit: IRUnit | None = None
        self.current_end_label: str | None = None
        self.current_temp_offsets: dict[str, int] = {}
        self.main_temp_labels: dict[str, str] = {}
        self.pending_params: list[Quad] = []

    def generate(self) -> str:
        self.assign_labels()
        self.emit_global_storage()
        self.emit_main_temp_storage()
        for proc in self.ir.procedures:
            self.emit_procedure(proc)
        self.current_unit = self.ir.main
        self.current_temp_offsets = {}
        self.program.emit("main:")
        self.emit_quads(self.ir.main.quads, "main")
        self.program.emit("li $v0, 10")
        self.program.emit("syscall")
        return self.program.render(optimize=self.optimize)

    def assign_labels(self) -> None:
        for symbol in self.ir.globals:
            symbol.storage = "global"
            symbol.label = f"g_{mangle(symbol.name)}"
        for proc in walk_procedures_with_path(self.ir.procedures):
            proc.symbol.label = f"proc_{mangle(proc.scope_path)}"

    def emit_global_storage(self) -> None:
        for symbol in self.ir.globals:
            words = max(1, type_size_words(symbol.type_info))
            if words == 1:
                self.program.emit_data(f"{symbol.label}: .word 0")
            else:
                self.program.emit_data(f"{symbol.label}: .space {words * 4}")

    def emit_main_temp_storage(self) -> None:
        for temp in self.ir.main.temp_types:
            label = f"tmp_main_{temp}"
            self.main_temp_labels[temp] = label
            self.program.emit_data(f"{label}: .word 0")

    def emit_procedure(self, proc: IRProcedure) -> None:
        # 第一次调用：在生成子过程之前分配存储，确保外层局部变量的偏移量
        # 在子过程通过静态链访问时已经确定。
        local_bytes = self.assign_procedure_storage(proc)

        for child in proc.children:
            self.emit_procedure(child)

        self.current_unit = proc
        self.current_end_label = self.label_name(proc, proc.end_label)
        # 第二次调用：子过程编译会覆盖 current_temp_offsets（共享的实例变量），
        # 切换回当前过程前必须重新恢复其临时变量偏移表。
        self.assign_procedure_storage(proc)
        self.program.emit(f"{proc.symbol.label}:")
        self.emit_prologue(local_bytes)
        self.emit_quads(proc.quads, proc.name)
        self.emit_epilogue()
        self.current_end_label = None

    def assign_procedure_storage(self, proc: IRProcedure) -> int:
        """为过程的参数、局部变量和临时变量分配栈帧偏移量，返回局部区域总字节数。

        栈帧布局（相对于 $fp，地址从高到低）：
          $fp + 0        : 保存的 $fp（调用者帧指针）
          $fp + 4        : 保存的 $ra（返回地址）
          $fp + 8        : 静态链（调用者传入的词法父帧指针）
          $fp + 12, +16… : 参数（按声明顺序，每个 4 字节）
          $fp - 4, -8…   : 局部变量（按声明顺序）
          $fp - N…       : 临时变量（编译器生成的 t0、t1…）
        """
        next_offset = 0
        for index, symbol in enumerate(proc.params):
            symbol.storage = "param"
            # 参数从 $fp+12 开始：+0 是保存的 $fp，+4 是 $ra，+8 是静态链
            symbol.offset = 12 + index * 4
        for symbol in proc.locals:
            words = max(1, type_size_words(symbol.type_info))
            next_offset -= words * 4
            symbol.storage = "local"
            symbol.offset = next_offset
        self.current_temp_offsets = {}
        for temp in proc.temp_types:
            next_offset -= 4
            self.current_temp_offsets[temp] = next_offset
        return -next_offset

    def emit_prologue(self, local_bytes: int) -> None:
        """生成过程入口代码：保存 $fp/$ra，建立新帧，分配局部变量空间。"""
        self.program.emit(f"addi $sp, $sp, -{FRAME_HEADER_SIZE}")
        self.program.emit(f"sw $fp, {FP_SAVE_OFFSET}($sp)")
        self.program.emit(f"sw $ra, {RA_SAVE_OFFSET}($sp)")
        self.program.emit("move $fp, $sp")
        if local_bytes:
            self.program.emit(f"addi $sp, $sp, -{local_bytes}")

    def emit_epilogue(self) -> None:
        """生成过程出口代码：恢复 $fp/$ra，弹出栈帧，返回调用者。"""
        self.program.emit("move $sp, $fp")
        self.program.emit(f"lw $fp, {FP_SAVE_OFFSET}($sp)")
        self.program.emit(f"lw $ra, {RA_SAVE_OFFSET}($sp)")
        self.program.emit(f"addi $sp, $sp, {FRAME_HEADER_SIZE}")
        self.program.emit("jr $ra")

    def emit_quads(self, quads: list[Quad], unit_name: str) -> None:
        for quad in quads:
            if quad.op == "label":
                self.program.emit(f"{self.label_name(self.current_unit, str(quad.result))}:")
            elif quad.op == "goto":
                self.program.emit(f"j {self.label_name(self.current_unit, str(quad.result))}")
            elif quad.op.startswith("if_false_"):
                self.emit_conditional_branch(quad)
            elif quad.op in {"+", "-", "*", "/"}:
                self.emit_binary(quad)
            elif quad.op == "assign":
                value = self.load_operand(quad.arg1)
                self.store_temp(require_temp(quad.result), value)
                self.regs.free(value)
            elif quad.op == "addr":
                addr = self.address_of_symbol(require_symbol(quad.symbol))
                self.store_temp(require_temp(quad.result), addr)
                self.regs.free(addr)
            elif quad.op == "index_addr":
                self.emit_index_addr(quad)
            elif quad.op == "field_addr":
                self.emit_field_addr(quad)
            elif quad.op == "load":
                addr = self.load_operand(quad.arg1)
                value = self.regs.alloc()
                self.program.emit(f"lw {value}, 0({addr})")
                self.store_temp(require_temp(quad.result), value)
                self.regs.free(addr)
                self.regs.free(value)
            elif quad.op == "store":
                value = self.load_operand(quad.arg1)
                addr = self.load_operand(quad.result)
                self.program.emit(f"sw {value}, 0({addr})")
                self.regs.free(value)
                self.regs.free(addr)
            elif quad.op == "param":
                self.pending_params.append(quad)
            elif quad.op == "call":
                self.emit_call(quad)
            elif quad.op == "tail_call":
                self.emit_tail_call(quad)
            elif quad.op == "read":
                self.emit_read(quad)
            elif quad.op == "write":
                self.emit_write(quad)
            elif quad.op == "return":
                self.emit_return(quad)
            else:
                raise CodegenError(f"unsupported IR op in {unit_name}: {quad.format()}")

    def emit_binary(self, quad: Quad) -> None:
        left = self.load_operand(quad.arg1)
        right = self.load_operand(quad.arg2)
        if quad.op == "+":
            self.program.emit(f"add {left}, {left}, {right}")
        elif quad.op == "-":
            self.program.emit(f"sub {left}, {left}, {right}")
        elif quad.op == "*":
            self.program.emit(f"mul {left}, {left}, {right}")
        elif quad.op == "/":
            self.program.emit(f"div {left}, {left}, {right}")
        self.store_temp(require_temp(quad.result), left)
        self.regs.free(left)
        self.regs.free(right)

    def emit_conditional_branch(self, quad: Quad) -> None:
        left = self.load_operand(quad.arg1)
        right = self.load_operand(quad.arg2)
        target = self.label_name(self.current_unit, str(quad.result))
        if quad.op == "if_false_<":
            self.program.emit(f"bge {left}, {right}, {target}")
        elif quad.op == "if_false_=":
            self.program.emit(f"bne {left}, {right}, {target}")
        else:
            raise CodegenError(f"unsupported conditional IR op: {quad.op}")
        self.regs.free(left)
        self.regs.free(right)

    def emit_index_addr(self, quad: Quad) -> None:
        base = self.load_operand(quad.arg1)
        index = self.load_operand(quad.arg2)
        array_type = require_type(quad.type_info)
        low = array_type.low or 0
        element_words = type_size_words(array_type.element or UNKNOWN)
        self.program.emit(f"addi {index}, {index}, {-low}")
        byte_scale = element_words * 4
        if self.optimize and is_power_of_two(byte_scale):
            shift = int(math.log2(byte_scale))
            if shift:
                self.program.emit(f"sll {index}, {index}, {shift}")
        else:
            scale = self.regs.alloc()
            self.program.emit(f"li {scale}, {byte_scale}")
            self.program.emit(f"mul {index}, {index}, {scale}")
            self.regs.free(scale)
        self.program.emit(f"add {base}, {base}, {index}")
        self.store_temp(require_temp(quad.result), base)
        self.regs.free(base)
        self.regs.free(index)

    def emit_field_addr(self, quad: Quad) -> None:
        base = self.load_operand(quad.arg1)
        record_type = require_type(quad.type_info)
        offset = field_offset_words(record_type, str(quad.arg2))
        if offset:
            self.program.emit(f"addi {base}, {base}, {offset * 4}")
        self.store_temp(require_temp(quad.result), base)
        self.regs.free(base)

    def emit_call(self, quad: Quad) -> None:
        for param in reversed(self.pending_params):
            value = self.load_operand(param.arg1)
            self.program.emit("addi $sp, $sp, -4")
            self.program.emit(f"sw {value}, 0($sp)")
            self.regs.free(value)
        # Push static link: the $fp of the lexically enclosing scope for the callee
        symbol = require_symbol(quad.symbol or quad.arg1)
        static_link = self.compute_static_link(symbol)
        self.program.emit("addi $sp, $sp, -4")
        self.program.emit(f"sw {static_link}, 0($sp)")
        self.regs.free(static_link)
        self.program.emit(f"jal {symbol.label}")
        # Clean up: params + static link
        cleanup = (len(self.pending_params) + 1) * 4
        self.program.emit(f"addi $sp, $sp, {cleanup}")
        self.pending_params.clear()

    def emit_tail_call(self, quad: Quad) -> None:
        params = quad.arg1 if isinstance(quad.arg1, list) else []
        if self.current_unit is None or not isinstance(self.current_unit, IRProcedure):
            raise CodegenError("tail_call is only valid inside procedures")
        if len(params) != len(self.current_unit.params):
            raise CodegenError("tail_call argument count does not match current procedure")

        for param in params:
            if not isinstance(param, Quad):
                raise CodegenError("tail_call arguments must be param quads")
            value = self.load_operand(param.arg1)
            self.program.emit("addi $sp, $sp, -4")
            self.program.emit(f"sw {value}, 0($sp)")
            self.regs.free(value)

        for symbol in reversed(self.current_unit.params):
            value = self.regs.alloc()
            self.program.emit(f"lw {value}, 0($sp)")
            self.program.emit("addi $sp, $sp, 4")
            self.program.emit(f"sw {value}, {symbol.offset}($fp)")
            self.regs.free(value)

        self.program.emit(f"j {self.label_name(self.current_unit, str(quad.result))}")

    def emit_read(self, quad: Quad) -> None:
        addr = self.load_operand(quad.result)
        syscall = 12 if getattr(quad.type_info, "kind", "") == "char" else 5
        self.program.emit(f"li $v0, {syscall}")
        self.program.emit("syscall")
        self.program.emit(f"sw $v0, 0({addr})")
        self.regs.free(addr)

    def emit_write(self, quad: Quad) -> None:
        value = self.load_operand(quad.arg1)
        syscall = 11 if getattr(quad.type_info, "kind", "") == "char" else 1
        self.program.emit(f"move $a0, {value}")
        self.program.emit(f"li $v0, {syscall}")
        self.program.emit("syscall")
        self.program.emit("li $a0, 10")
        self.program.emit("li $v0, 11")
        self.program.emit("syscall")
        self.regs.free(value)

    def emit_return(self, quad: Quad) -> None:
        # SNL 的 return(expr) 是控制流语句，expr 仅用于副作用求值（如触发读取），
        # 不通过寄存器向调用方传递返回值。因此只需跳转到过程结束标签即可。
        value = self.load_operand(quad.arg1)
        self.regs.free(value)
        if self.current_end_label is None:
            # 在主程序中遇到 return，直接退出
            self.program.emit("li $v0, 10")
            self.program.emit("syscall")
        else:
            self.program.emit(f"j {self.current_end_label}")

    def load_operand(self, operand: Operand) -> str:
        reg = self.regs.alloc()
        if isinstance(operand, int):
            self.program.emit(f"li {reg}, {operand}")
            return reg
        if is_temp(operand):
            self.load_temp(str(operand), reg)
            return reg
        raise CodegenError(f"unsupported IR operand: {operand!r}")

    def address_of_symbol(self, symbol: Symbol) -> str:
        reg = self.regs.alloc()
        if symbol.storage == "global":
            self.program.emit(f"la {reg}, {symbol.label}")
        elif symbol.storage in {"param", "local"}:
            # Determine how many static links to follow
            current_level = self.current_scope_level()
            target_level = symbol.scope_level
            if target_level == current_level:
                # Same scope — use $fp directly
                if symbol.storage == "param" and symbol.mode == "var":
                    self.program.emit(f"lw {reg}, {symbol.offset}($fp)")
                else:
                    self.program.emit(f"addi {reg}, $fp, {symbol.offset}")
            else:
                # Outer scope — follow static links
                self.program.emit(f"lw {reg}, {STATIC_LINK_OFFSET}($fp)")  # first static link
                hops = current_level - target_level - 1
                for _ in range(hops):
                    self.program.emit(f"lw {reg}, {STATIC_LINK_OFFSET}({reg})")  # follow chain
                if symbol.storage == "param" and symbol.mode == "var":
                    self.program.emit(f"lw {reg}, {symbol.offset}({reg})")
                else:
                    self.program.emit(f"addi {reg}, {reg}, {symbol.offset}")
        else:
            raise CodegenError(f"unknown storage type '{symbol.storage}' for symbol '{symbol.name}'")
        return reg

    def compute_static_link(self, callee_symbol: Symbol) -> str:
        """Compute the static link to pass to the callee.

        The static link should point to the $fp of the callee's lexically
        enclosing scope. The callee is declared at scope_level L, meaning
        its body runs at level L+1, and its parent scope is at level L.
        We need to pass the $fp of the frame at level L.
        """
        reg = self.regs.alloc()
        # The callee's parent scope level is where the callee was declared
        parent_level = callee_symbol.scope_level
        current_level = self.current_scope_level()

        if parent_level == 0:
            # Callee's parent is global — static link is unused but must be pushed
            self.program.emit(f"move {reg}, $fp")
        elif parent_level == current_level:
            # Callee is our direct child — pass our own $fp
            self.program.emit(f"move {reg}, $fp")
        elif parent_level < current_level:
            # Callee's parent is an ancestor — follow static links up
            self.program.emit(f"lw {reg}, {STATIC_LINK_OFFSET}($fp)")  # one hop up
            hops = current_level - parent_level - 1
            for _ in range(hops):
                self.program.emit(f"lw {reg}, {STATIC_LINK_OFFSET}({reg})")
        else:
            # Shouldn't happen in valid programs
            self.program.emit(f"move {reg}, $fp")
        return reg

    def current_scope_level(self) -> int:
        """Return the scope level of the current procedure being compiled."""
        if self.current_unit is self.ir.main:
            return 0
        if isinstance(self.current_unit, IRProcedure):
            return self.current_unit.scope_level
        return 0

    def load_temp(self, temp: str, reg: str) -> None:
        if self.current_unit is self.ir.main:
            addr = self.regs.alloc()
            self.program.emit(f"la {addr}, {self.main_temp_labels[temp]}")
            self.program.emit(f"lw {reg}, 0({addr})")
            self.regs.free(addr)
            return
        self.program.emit(f"lw {reg}, {self.current_temp_offsets[temp]}($fp)")

    def store_temp(self, temp: str, reg: str) -> None:
        if self.current_unit is self.ir.main:
            addr = self.regs.alloc()
            self.program.emit(f"la {addr}, {self.main_temp_labels[temp]}")
            self.program.emit(f"sw {reg}, 0({addr})")
            self.regs.free(addr)
            return
        self.program.emit(f"sw {reg}, {self.current_temp_offsets[temp]}($fp)")

    def label_name(self, unit: IRUnit | None, label: str) -> str:
        prefix = mangle(unit.name if unit is not None else "main")
        return f"{prefix}_{label}"


def walk_procedures_with_path(procedures: list[IRProcedure], prefix: str = "") -> list[IRProcedure]:
    """按后序遍历过程树，同时为每个过程设置 scope_path 属性（用于生成唯一标签）。

    scope_path 是从根到当前过程的名称路径，如 "outer_inner"，
    确保同名嵌套过程在汇编中不会产生标签冲突。
    """
    result: list[IRProcedure] = []
    for proc in procedures:
        path = f"{prefix}_{proc.name}" if prefix else proc.name
        proc.scope_path = path
        result.extend(walk_procedures_with_path(proc.children, path))
        result.append(proc)
    return result


def require_temp(value: Operand) -> str:
    if not is_temp(value):
        raise CodegenError(f"expected IR temp, got {value!r}")
    return str(value)


def require_symbol(value: object | None) -> Symbol:
    if not isinstance(value, Symbol):
        raise CodegenError("internal error: expected semantic symbol")
    return value


def require_type(value: object | None) -> TypeInfo:
    if not isinstance(value, TypeInfo):
        raise CodegenError("internal error: expected type info")
    return value


def parse_and_check(source: Path) -> Program:
    """词法 → 语法 → 语义，三阶段前端检查。

    任意阶段出错时立即停止后续阶段，避免在残缺 AST 上运行语义分析
    产生大量误报，干扰用户定位真正的错误。
    """
    source_text = source.read_text(encoding="utf-8")
    lexer_tokens = SNLLexer(load_grammar(DEFAULT_GRAMMAR)).tokenize(source_text, include_eof=True)

    # 阶段一：词法错误
    lex_errors = [
        f"line {token.line_show}: lexical error: {token.sem}"
        for token in lexer_tokens
        if token.lex == "ERROR"
    ]
    if lex_errors:
        raise CodegenError("front-end checks failed:\n" + "\n".join(lex_errors))

    # 阶段二：语法分析
    tokens = [Token(token.line_show, token.lex, token.sem) for token in lexer_tokens]
    parser = SNLParser(tokens)
    program_ast = parser.parse()
    if parser.errors:
        raise CodegenError("front-end checks failed:\n" + "\n".join(parser.errors))

    # 阶段三：语义分析（仅在 AST 完整时运行，避免误报）
    semantic = SNLSemanticAnalyzer(program_ast)
    semantic.analyze()
    if semantic.errors:
        raise CodegenError("front-end checks failed:\n" + "\n".join(semantic.errors))

    return program_ast


def compile_source(
    source: Path,
    output: Path,
    *,
    optimize: bool = True,
    peephole: bool | None = None,
    enabled_passes: set[str] | None = None,
    emit_raw_ir: Path | None = None,
    emit_ir: Path | None = None,
) -> str:
    if peephole is None:
        peephole = optimize
    program_ast = parse_and_check(source)
    ir = SNLIRGenerator(program_ast).generate()
    if emit_raw_ir is not None:
        emit_raw_ir.parent.mkdir(parents=True, exist_ok=True)
        emit_raw_ir.write_text(ir.format(), encoding="utf-8")
    if optimize:
        # 优化只作用于四元式 IR，保持 AST 和语义信息稳定，便于调试和回归。
        optimize_program(ir, enabled_passes=enabled_passes)
    if emit_ir is not None:
        emit_ir.parent.mkdir(parents=True, exist_ok=True)
        emit_ir.write_text(ir.format(), encoding="utf-8")
    assembly = IRMIPSGenerator(ir, optimize=peephole).generate()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(assembly, encoding="utf-8")
    return assembly


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compile SNL source to 32-bit MIPS assembly.")
    parser.add_argument("source", type=Path, help="SNL source program")
    parser.add_argument("-o", "--output", type=Path, required=True, help="write MIPS assembly to this file")
    parser.add_argument("--no-opt", action="store_true", help="skip IR optimization")
    parser.add_argument("--emit-raw-ir", type=Path, help="write quadruple IR before optimization")
    parser.add_argument("--emit-ir", type=Path, help="write quadruple IR after optimization")
    args = parser.parse_args(argv)

    try:
        compile_source(
            args.source,
            args.output,
            optimize=not args.no_opt,
            emit_raw_ir=args.emit_raw_ir,
            emit_ir=args.emit_ir,
        )
    except (OSError, CodegenError) as exc:
        print(f"snl_codegen.py: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

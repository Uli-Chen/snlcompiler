#!/usr/bin/env python3
"""SNL 目标代码生成器：四元式 IR → MIPS32 汇编。

栈帧布局（从高地址到低地址）：
  调用者压入的实参（从右到左）
  静态链指针（8($fp)）
  保存的 $fp（0($fp)）← $fp 指向此处
  保存的 $ra（4($fp)）
  局部变量区
  临时变量区 ← $sp 指向栈底
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

try:
    from playground.snlcompiler.src.snl_ir import IRProcedure, IRProgram, IRUnit, Operand, Quad, is_temp
    from playground.snlcompiler.src.snl_irgen import SNLIRGenerator
    from playground.snlcompiler.src.snl_lexer import DEFAULT_GRAMMAR, SNLLexer, load_grammar
    from playground.snlcompiler.src.snl_optimizer import optimize_program
    from playground.snlcompiler.src.snl_parser import Program, SNLParser, Token
    from playground.snlcompiler.src.snl_semantic import CHAR, INTEGER, UNKNOWN, SNLSemanticAnalyzer, Symbol, TypeInfo
except ModuleNotFoundError:
    from snl_ir import IRProcedure, IRProgram, IRUnit, Operand, Quad, is_temp
    from snl_irgen import SNLIRGenerator
    from snl_lexer import DEFAULT_GRAMMAR, SNLLexer, load_grammar
    from snl_optimizer import optimize_program
    from snl_parser import Program, SNLParser, Token
    from snl_semantic import CHAR, INTEGER, UNKNOWN, SNLSemanticAnalyzer, Symbol, TypeInfo


class CodegenError(RuntimeError):
    pass


class RegisterPool:
    """临时寄存器池，管理 $t0-$t9 的分配与回收。"""

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

    def render(self) -> str:
        return "\n".join(self.data + [""] + self.text) + "\n"


def type_size_words(type_info: TypeInfo) -> int:
    """计算类型占用的字数（每字 4 字节）。"""
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
    """计算记录类型中指定字段的偏移字数。"""
    offset = 0
    for name, field_type in record_type.fields.items():
        if name == field_name:
            return offset
        offset += type_size_words(field_type)
    raise CodegenError(f"record has no field {field_name!r}")


def mangle(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z_]", "_", text)


STATIC_LINK_OFFSET = 8    # 静态链在栈帧中的偏移（相对 $fp）
PARAM_BASE_OFFSET = 12    # 第一个参数在栈帧中的偏移（相对 $fp）


class IRMIPSGenerator:
    def __init__(self, ir: IRProgram) -> None:
        self.ir = ir
        self.program = MIPSProgram()
        self.regs = RegisterPool()
        self.current_unit: IRUnit | None = None
        self.current_end_label: str | None = None
        self.current_temp_offsets: dict[str, int] = {}
        self.main_temp_labels: dict[str, str] = {}
        self.pending_params: list[Quad] = []
        self.procedure_temp_offsets: dict[int, dict[str, int]] = {}
        self.procedure_local_bytes: dict[int, int] = {}

    def generate(self) -> str:
        self.assign_labels()
        self.assign_all_procedure_storage()
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
        return self.program.render()

    def assign_labels(self) -> None:
        for symbol in self.ir.globals:
            symbol.storage = "global"
            symbol.label = f"g_{mangle(symbol.name)}"
        for index, proc in enumerate(walk_procedures(self.ir.procedures)):
            proc.symbol.label = f"proc_{index}_{mangle(proc.name)}"

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
        for child in proc.children:
            self.emit_procedure(child)

        self.current_unit = proc
        self.current_temp_offsets = self.procedure_temp_offsets[id(proc)]
        self.current_end_label = self.label_name(proc, proc.end_label)
        self.program.emit(f"{proc.symbol.label}:")
        self.emit_prologue(self.procedure_local_bytes[id(proc)])
        self.emit_quads(proc.quads, proc.name)
        self.emit_epilogue()
        self.current_end_label = None

    def assign_all_procedure_storage(self) -> None:
        for proc in self.ir.procedures:
            self.assign_procedure_storage_tree(proc)

    def assign_procedure_storage_tree(self, proc: IRProcedure) -> None:
        # 先为父过程分配偏移，再处理子过程。这样子过程通过静态链访问
        # 外层局部变量时，外层 symbol 已经带有稳定的 frame offset。
        self.assign_procedure_storage(proc)
        for child in proc.children:
            self.assign_procedure_storage_tree(child)

    def assign_procedure_storage(self, proc: IRProcedure) -> int:
        next_offset = 0
        for index, symbol in enumerate(proc.params):
            symbol.storage = "param"
            symbol.offset = PARAM_BASE_OFFSET + index * 4
        for symbol in proc.locals:
            words = max(1, type_size_words(symbol.type_info))
            next_offset -= words * 4
            symbol.storage = "local"
            symbol.offset = next_offset
        temp_offsets: dict[str, int] = {}
        for temp in proc.temp_types:
            next_offset -= 4
            temp_offsets[temp] = next_offset
        self.procedure_temp_offsets[id(proc)] = temp_offsets
        self.procedure_local_bytes[id(proc)] = -next_offset
        return -next_offset

    def emit_prologue(self, local_bytes: int) -> None:
        self.program.emit("addi $sp, $sp, -8")
        self.program.emit("sw $fp, 0($sp)")
        self.program.emit("sw $ra, 4($sp)")
        self.program.emit("move $fp, $sp")
        if local_bytes:
            self.program.emit(f"addi $sp, $sp, -{local_bytes}")

    def emit_epilogue(self) -> None:
        self.program.emit("move $sp, $fp")
        self.program.emit("lw $fp, 0($sp)")
        self.program.emit("lw $ra, 4($sp)")
        self.program.emit("addi $sp, $sp, 8")
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
        if element_words != 1:
            scale = self.regs.alloc()
            self.program.emit(f"li {scale}, {element_words}")
            self.program.emit(f"mul {index}, {index}, {scale}")
            self.regs.free(scale)
        scale4 = self.regs.alloc()
        self.program.emit(f"li {scale4}, 4")
        self.program.emit(f"mul {index}, {index}, {scale4}")
        self.regs.free(scale4)
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
        symbol = require_symbol(quad.symbol or quad.arg1)
        for param in reversed(self.pending_params):
            value = self.load_operand(param.arg1)
            self.program.emit("addi $sp, $sp, -4")
            self.program.emit(f"sw {value}, 0($sp)")
            self.regs.free(value)
        static_link = self.load_static_link_for_call(symbol)
        self.program.emit("addi $sp, $sp, -4")
        self.program.emit(f"sw {static_link}, 0($sp)")
        self.regs.free(static_link)
        self.program.emit(f"jal {symbol.label}")
        self.program.emit(f"addi $sp, $sp, {(len(self.pending_params) + 1) * 4}")
        self.pending_params.clear()

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
        value = self.load_operand(quad.arg1)
        self.regs.free(value)
        if self.current_end_label is None:
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
        if symbol.storage == "global":
            reg = self.regs.alloc()
            self.program.emit(f"la {reg}, {symbol.label}")
            return reg

        frame = self.load_frame_for_level(symbol.scope_level)
        if symbol.storage == "param" and symbol.mode == "var":
            self.program.emit(f"lw {frame}, {symbol.offset}({frame})")
            return frame

        self.program.emit(f"addi {frame}, {frame}, {symbol.offset}")
        return frame

    def current_level(self) -> int:
        return self.current_unit.lexical_level if self.current_unit is not None else 0

    def load_frame_for_level(self, target_level: int) -> str:
        current_level = self.current_level()
        if target_level <= 0 or target_level > current_level:
            raise CodegenError(
                f"cannot access lexical level {target_level} from current level {current_level}"
            )

        frame = self.regs.alloc()
        self.program.emit(f"move {frame}, $fp")
        # 静态链位于每个过程栈帧的 8($fp)。沿链向外走，
        # 可以拿到声明该 symbol 的词法外层活动记录。
        for _ in range(current_level - target_level):
            self.program.emit(f"lw {frame}, {STATIC_LINK_OFFSET}({frame})")
        return frame

    def load_static_link_for_call(self, callee: Symbol) -> str:
        parent_level = callee.parent_level
        static_link = self.regs.alloc()
        if parent_level == 0:
            self.program.emit(f"li {static_link}, 0")
            return static_link

        current_level = self.current_level()
        if current_level < parent_level:
            raise CodegenError(
                f"cannot call procedure '{callee.name}' needing lexical parent level "
                f"{parent_level} from current level {current_level}"
            )

        self.program.emit(f"move {static_link}, $fp")
        # 调用过程时传入"被调过程的父词法层"的活动记录地址。
        # 直接调用子过程时 steps=0，传当前 $fp；兄弟/递归调用则沿静态链回退。
        for _ in range(current_level - parent_level):
            self.program.emit(f"lw {static_link}, {STATIC_LINK_OFFSET}({static_link})")
        return static_link

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
        if isinstance(unit, IRProcedure):
            prefix = mangle(str(unit.symbol.label))
        else:
            prefix = mangle(unit.name if unit is not None else "main")
        return f"{prefix}_{label}"


def walk_procedures(procedures: list[IRProcedure]) -> list[IRProcedure]:
    result: list[IRProcedure] = []
    for proc in procedures:
        result.extend(walk_procedures(proc.children))
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
    lexer_tokens = SNLLexer(load_grammar(DEFAULT_GRAMMAR)).tokenize(source.read_text(encoding="utf-8"), include_eof=True)
    errors = [f"line {token.line_show}: lexical error {token.sem}" for token in lexer_tokens if token.lex == "ERROR"]
    tokens = [Token(token.line_show, token.lex, token.sem) for token in lexer_tokens]

    parser = SNLParser(tokens)
    program_ast = parser.parse()
    errors.extend(parser.errors)

    if not parser.errors:
        semantic = SNLSemanticAnalyzer(program_ast)
        semantic.analyze()
        errors.extend(semantic.errors)

    if errors:
        raise CodegenError("front-end checks failed:\n" + "\n".join(errors))
    return program_ast


def compile_source(
    source: Path,
    output: Path,
    *,
    optimize: bool = True,
    emit_raw_ir: Path | None = None,
    emit_ir: Path | None = None,
) -> str:
    program_ast = parse_and_check(source)
    ir = SNLIRGenerator(program_ast).generate()
    if emit_raw_ir is not None:
        emit_raw_ir.parent.mkdir(parents=True, exist_ok=True)
        emit_raw_ir.write_text(ir.format(), encoding="utf-8")
    if optimize:
        # 优化只作用于四元式 IR，保持 AST 和语义信息稳定，便于调试和回归。
        optimize_program(ir)
    if emit_ir is not None:
        emit_ir.parent.mkdir(parents=True, exist_ok=True)
        emit_ir.write_text(ir.format(), encoding="utf-8")
    assembly = IRMIPSGenerator(ir).generate()
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

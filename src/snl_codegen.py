#!/usr/bin/env python3
"""SNL target-code generator.

The generator accepts an SNL source program, runs the existing lexical,
syntax, and semantic analyzers, then emits 32-bit MIPS assembly.  It also
contains a small MIPS interpreter for the generated subset so the course
pipeline can produce a program running result without requiring MARS.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from snl_lexer import DEFAULT_GRAMMAR, SNLLexer, format_text, load_grammar
from snl_parser import SNLParser, Token, format_parse_result
from snl_semantic import (
    BOOL,
    CHAR,
    INTEGER,
    UNKNOWN,
    ParamInfo,
    SNLSemanticAnalyzer,
    TypeInfo,
    format_semantic_result,
)


TYPE_START = {"INTEGER", "CHAR", "ARRAY", "RECORD", "ID"}
FIELD_TYPE_START = {"INTEGER", "CHAR", "ARRAY"}
STMT_START = {"IF", "WHILE", "READ", "WRITE", "RETURN", "ID"}
ADD_OPS = {"PLUS": "+", "MINUS": "-"}
MULT_OPS = {"TIMES": "*", "OVER": "/"}
CMP_OPS = {"LT": "<", "EQ": "="}
STATIC_LINK_OFFSET = 8
FIRST_PARAM_OFFSET = 12


class CodegenError(RuntimeError):
    pass


@dataclass
class CGSymbol:
    name: str
    kind: str
    type_info: TypeInfo
    label: str
    line: int
    params: list[ParamInfo] = field(default_factory=list)
    param_symbols: list["CGSymbol"] = field(default_factory=list)
    mode: str = ""
    storage: str = "global"
    offset: int = 0
    scope_level: int = 0
    static_parent_level: int = 0


@dataclass
class CGScope:
    name: str
    prefix: str
    level: int = 0
    symbols: dict[str, CGSymbol] = field(default_factory=dict)
    types: dict[str, TypeInfo] = field(default_factory=dict)
    next_local_offset: int = 0

    @property
    def local_bytes(self) -> int:
        return -self.next_local_offset


@dataclass
class ExprReg:
    reg: str
    type_info: TypeInfo
    const_int: int | None = None


@dataclass
class LValue:
    addr_reg: str
    type_info: TypeInfo
    name: str


@dataclass
class FrameSlot:
    name: str
    kind: str
    type_text: str
    offset: int
    size_bytes: int
    mode: str = ""


@dataclass
class FrameInfo:
    name: str
    label: str
    local_bytes: int
    param_bytes: int
    slots: list[FrameSlot] = field(default_factory=list)


@dataclass
class ActualArg:
    kind: str
    size_bytes: int
    reg: str | None = None
    addr_reg: str | None = None


@dataclass
class FrontendArtifacts:
    tokens: list[Token]
    token_text: str
    parse_report: str
    semantic_report: str
    errors: list[str]


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
        self.label_counter = 0
        self.source_line_provider: Callable[[], int | None] | None = None

    def emit_data(self, line: str) -> None:
        self.data.append(line)

    def emit(self, line: str = "") -> None:
        if line and not line.endswith(":") and not line.startswith(".") and self.source_line_provider is not None:
            source_line = self.source_line_provider()
            if source_line is not None:
                line = f"{line} #@L{source_line}"
        self.text.append(line)

    def new_label(self, prefix: str) -> str:
        self.label_counter += 1
        return f"{prefix}_{self.label_counter}"

    def render(self) -> str:
        optimized_text: list[str] = []
        text = self.text
        for index, line in enumerate(text):
            stripped = line.split("#", 1)[0].strip()
            if stripped:
                if re.fullmatch(r"move\s+(\$[A-Za-z0-9]+)\s*,\s*(\$[A-Za-z0-9]+)", stripped):
                    match = re.fullmatch(r"move\s+(\$[A-Za-z0-9]+)\s*,\s*(\$[A-Za-z0-9]+)", stripped)
                    if match and match.group(1) == match.group(2):
                        continue
                if re.fullmatch(r"addi\s+(\$[A-Za-z0-9]+)\s*,\s*(\$[A-Za-z0-9]+)\s*,\s*0", stripped):
                    match = re.fullmatch(r"addi\s+(\$[A-Za-z0-9]+)\s*,\s*(\$[A-Za-z0-9]+)\s*,\s*0", stripped)
                    if match and match.group(1) == match.group(2):
                        continue
                jump_match = re.fullmatch(r"j\s+([A-Za-z0-9_]+)", stripped)
                if jump_match:
                    target_label = jump_match.group(1)
                    next_index = index + 1
                    while next_index < len(text):
                        next_stripped = text[next_index].split("#", 1)[0].strip()
                        if not next_stripped:
                            next_index += 1
                            continue
                        if next_stripped == f"{target_label}:":
                            line = ""
                        break
                    if not line:
                        continue
            optimized_text.append(line)
        return "\n".join(self.data + [""] + optimized_text) + "\n"


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


def param_slot_bytes(param: ParamInfo) -> int:
    if param.mode == "var":
        return 4
    return max(1, type_size_words(param.type_info)) * 4


def mangle(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z_]", "_", text)


class SNLCodeGenerator:
    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.index = 0
        self.program = MIPSProgram()
        self.active_source_line: int | None = None
        self.program.source_line_provider = lambda: self.active_source_line
        self.regs = RegisterPool()
        self.scopes: list[CGScope] = []
        self.all_scopes: list[CGScope] = []
        self.frame_infos: list[FrameInfo] = []
        self.current_proc_end: str | None = None
        self.proc_symbols: dict[str, CGSymbol] = {}

    @property
    def current(self) -> Token:
        if self.index < len(self.tokens):
            return self.tokens[self.index]
        return self.tokens[-1]

    def at(self, *lex_types: str) -> bool:
        return self.current.lex in lex_types

    def advance(self) -> Token:
        token = self.current
        if self.index < len(self.tokens) - 1:
            self.index += 1
        return token

    def expect(self, lex_type: str) -> Token:
        if self.current.lex == lex_type:
            return self.advance()
        raise CodegenError(f"line {self.current.line}: expected {lex_type}, found {self.current.display()}")

    @property
    def scope(self) -> CGScope:
        return self.scopes[-1]

    def enter_scope(self, name: str) -> None:
        if not self.scopes:
            prefix = "g"
            level = 0
        else:
            prefix = f"{self.scope.prefix}_p_{mangle(name)}"
            level = self.scope.level + 1
        scope = CGScope(name, prefix, level=level)
        self.scopes.append(scope)
        self.all_scopes.append(scope)

    def leave_scope(self) -> None:
        self.scopes.pop()

    def declare_symbol(self, symbol: CGSymbol) -> None:
        self.scope.symbols[symbol.name] = symbol

    def declare_type(self, name: str, type_info: TypeInfo) -> None:
        self.scope.types[name] = type_info

    def lookup_symbol(self, name: str) -> CGSymbol:
        for scope in reversed(self.scopes):
            if name in scope.symbols:
                return scope.symbols[name]
        raise CodegenError(f"internal error: symbol {name!r} not found after semantic analysis")

    def lookup_type(self, name: str) -> TypeInfo:
        for scope in reversed(self.scopes):
            if name in scope.types:
                return scope.types[name]
        raise CodegenError(f"internal error: type {name!r} not found after semantic analysis")

    def generate(self) -> str:
        self.parse_program()
        return self.program.render()

    def parse_program(self) -> None:
        self.expect("PROGRAM")
        self.expect("ID")
        self.enter_scope("global")
        self.parse_declare_part(emit_procedure_text=False)
        self.program.emit("main:")
        self.parse_program_body()
        self.expect("DOT")
        self.program.emit("li $v0, 10")
        self.program.emit("syscall")
        self.leave_scope()

    def parse_declare_part(self, emit_procedure_text: bool) -> None:
        self.parse_type_dec()
        self.parse_var_dec()
        self.parse_proc_dec(emit_procedure_text)

    def parse_type_dec(self) -> None:
        if not self.at("TYPE"):
            return
        self.advance()
        while self.at("ID"):
            name = self.advance()
            self.expect("EQ")
            type_info = self.parse_type_name()
            self.expect("SEMI")
            self.declare_type(name.sem, type_info)

    def parse_type_name(self) -> TypeInfo:
        if self.at("INTEGER"):
            self.advance()
            return INTEGER
        if self.at("CHAR"):
            self.advance()
            return CHAR
        if self.at("ARRAY"):
            return self.parse_array_type()
        if self.at("RECORD"):
            return self.parse_record_type()
        if self.at("ID"):
            return self.lookup_type(self.advance().sem)
        raise CodegenError(f"line {self.current.line}: expected type name")

    def parse_array_type(self) -> TypeInfo:
        self.expect("ARRAY")
        self.expect("LMIDPAREN")
        low = int(self.expect("INTC").sem)
        self.expect("UNDERANGE")
        high = int(self.expect("INTC").sem)
        self.expect("RMIDPAREN")
        self.expect("OF")
        element = self.parse_base_type()
        return TypeInfo("array", low, high, element)

    def parse_base_type(self) -> TypeInfo:
        if self.at("INTEGER"):
            self.advance()
            return INTEGER
        if self.at("CHAR"):
            self.advance()
            return CHAR
        raise CodegenError(f"line {self.current.line}: expected base type")

    def parse_record_type(self) -> TypeInfo:
        self.expect("RECORD")
        fields: dict[str, TypeInfo] = {}
        while self.current.lex in FIELD_TYPE_START:
            field_type = self.parse_array_type() if self.at("ARRAY") else self.parse_base_type()
            for name, _line in self.parse_id_list():
                fields[name] = field_type
            self.expect("SEMI")
        self.expect("END")
        return TypeInfo("record", fields=fields)

    def parse_id_list(self) -> list[tuple[str, int]]:
        token = self.expect("ID")
        ids = [(token.sem, token.line)]
        while self.at("COMMA"):
            self.advance()
            token = self.expect("ID")
            ids.append((token.sem, token.line))
        return ids

    def parse_var_dec(self) -> None:
        if not self.at("VAR"):
            return
        self.advance()
        while self.current.lex in TYPE_START:
            type_info = self.parse_type_name()
            ids = self.parse_id_list()
            self.expect("SEMI")
            for name, line in ids:
                self.declare_storage(name, "var", type_info, line)

    def declare_storage(self, name: str, kind: str, type_info: TypeInfo, line: int, mode: str = "") -> CGSymbol:
        storage = "global" if len(self.scopes) == 1 else "local"
        if kind == "param":
            storage = "param"
        label = f"{self.scope.prefix}_{mangle(name)}" if storage == "global" else ""
        symbol = CGSymbol(
            name,
            kind,
            type_info,
            label,
            line,
            mode=mode,
            storage=storage,
            scope_level=self.scope.level,
        )
        self.declare_symbol(symbol)
        words = max(1, type_size_words(type_info))
        if storage == "global":
            if words <= 1:
                self.program.emit_data(f"{label}: .word 0")
            else:
                self.program.emit_data(f"{label}: .space {words * 4}")
        else:
            self.scope.next_local_offset -= words * 4
            symbol.offset = self.scope.next_local_offset
        return symbol

    def parse_proc_dec(self, emit_procedure_text: bool) -> None:
        while self.at("PROCEDURE"):
            self.parse_proc_declaration()

    def parse_proc_declaration(self) -> None:
        self.expect("PROCEDURE")
        name = self.expect("ID")
        self.expect("LPAREN")
        params = self.parse_param_list()
        self.expect("RPAREN")
        child_level = self.scope.level + 1
        child_prefix = f"{self.scope.prefix}_p_{mangle(name.sem)}"
        label = f"proc_{child_prefix}"
        proc_symbol = CGSymbol(
            name.sem,
            "procedure",
            UNKNOWN,
            label,
            name.line,
            params=params,
            scope_level=child_level,
            static_parent_level=self.scope.level,
        )
        self.declare_symbol(proc_symbol)
        self.proc_symbols[name.sem] = proc_symbol
        self.expect("SEMI")

        saved_scope = self.scope
        self.enter_scope(name.sem)
        param_symbols: list[CGSymbol] = []
        param_offset = FIRST_PARAM_OFFSET
        for param in params:
            symbol = CGSymbol(
                param.name,
                "param",
                param.type_info,
                "",
                param.line,
                mode=param.mode,
                storage="param",
                offset=param_offset,
                scope_level=self.scope.level,
            )
            self.declare_symbol(symbol)
            param_symbols.append(symbol)
            param_offset += param_slot_bytes(param)
        proc_symbol.param_symbols = param_symbols

        proc_text_start = len(self.program.text)
        proc_end = self.program.new_label(f"{label}_end")
        previous_end = self.current_proc_end
        self.current_proc_end = proc_end
        self.parse_declare_part(emit_procedure_text=True)
        self.frame_infos.append(self.build_frame_info(name.sem, label, param_symbols, self.scope))
        self.program.emit(f"{label}:")
        self.emit_prologue(self.scope.local_bytes)
        self.parse_program_body()
        self.program.emit(f"{proc_end}:")
        self.emit_epilogue()
        self.current_proc_end = previous_end
        self.leave_scope()

        # Procedure text must appear before main.  It already does because
        # global declarations are parsed before main is emitted.
        assert proc_text_start < len(self.program.text)
        assert saved_scope is self.scope

    def build_frame_info(
        self,
        name: str,
        label: str,
        param_symbols: list[CGSymbol],
        scope: CGScope,
    ) -> FrameInfo:
        slots = [
            FrameSlot("saved_fp", "runtime", "word", 0, 4),
            FrameSlot("saved_ra", "runtime", "word", 4, 4),
            FrameSlot("static_link", "runtime", "word", STATIC_LINK_OFFSET, 4),
        ]
        for symbol in param_symbols:
            slots.append(
                FrameSlot(
                    symbol.name,
                    "param",
                    symbol.type_info.display(),
                    symbol.offset,
                    4 if symbol.mode == "var" else max(1, type_size_words(symbol.type_info)) * 4,
                    symbol.mode,
                )
            )
        for symbol in scope.symbols.values():
            if symbol.storage == "local":
                slots.append(
                    FrameSlot(
                        symbol.name,
                        "local",
                        symbol.type_info.display(),
                        symbol.offset,
                        type_size_words(symbol.type_info) * 4,
                        symbol.mode,
                    )
                )
        return FrameInfo(
            name=name,
            label=label,
            local_bytes=scope.local_bytes,
            param_bytes=4
            + sum(4 if symbol.mode == "var" else max(1, type_size_words(symbol.type_info)) * 4 for symbol in param_symbols),
            slots=slots,
        )

    def parse_param_list(self) -> list[ParamInfo]:
        params: list[ParamInfo] = []
        if self.at("RPAREN"):
            return params
        params.extend(self.parse_param())
        while self.at("SEMI"):
            self.advance()
            params.extend(self.parse_param())
        return params

    def parse_param(self) -> list[ParamInfo]:
        mode = "var" if self.at("VAR") else "value"
        if self.at("VAR"):
            self.advance()
        type_info = self.parse_type_name()
        return [ParamInfo(name, mode, type_info, line) for name, line in self.parse_id_list()]

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

    def frame_reg_for_level(self, target_level: int) -> str:
        if target_level <= 0:
            raise CodegenError("global scope is not addressed through a frame register")
        if self.scope.level < target_level:
            raise CodegenError(
                f"cannot access lexical level {target_level} from current level {self.scope.level}"
            )
        reg = self.regs.alloc()
        self.program.emit(f"move {reg}, $fp")
        level = self.scope.level
        while level > target_level:
            self.program.emit(f"lw {reg}, {STATIC_LINK_OFFSET}({reg})")
            level -= 1
        return reg

    def static_link_reg_for_call(self, symbol: CGSymbol) -> str:
        if symbol.static_parent_level <= 0:
            return "$zero"
        if symbol.static_parent_level == self.scope.level:
            return "$fp"
        return self.frame_reg_for_level(symbol.static_parent_level)

    def parse_program_body(self) -> None:
        self.expect("BEGIN")
        self.parse_stm_list({"END"})
        self.expect("END")

    def parse_stm_list(self, terminators: set[str]) -> None:
        while not self.at("EOF") and self.current.lex not in terminators:
            if self.current.lex in STMT_START:
                self.parse_stm()
                if self.at("SEMI"):
                    self.advance()
                continue
            raise CodegenError(f"line {self.current.line}: unexpected token {self.current.display()}")

    def parse_stm(self) -> None:
        line = self.current.line
        previous_line = self.active_source_line
        self.active_source_line = line
        try:
            if self.at("IF"):
                self.parse_if()
            elif self.at("WHILE"):
                self.parse_while()
            elif self.at("READ"):
                self.parse_read()
            elif self.at("WRITE"):
                self.parse_write()
            elif self.at("RETURN"):
                self.parse_return()
            elif self.at("ID"):
                name = self.advance()
                if self.at("LPAREN"):
                    self.parse_call(name)
                else:
                    self.parse_assignment(name)
            else:
                raise CodegenError(f"line {self.current.line}: expected statement")
        finally:
            self.active_source_line = previous_line

    def parse_assignment(self, name: Token) -> None:
        target = self.finish_lvalue(name)
        self.expect("ASSIGN")
        if target.type_info.kind in {"array", "record"}:
            source = self.parse_copy_source()
            self.emit_copy_words(target.addr_reg, source.addr_reg, max(1, type_size_words(target.type_info)))
            self.regs.free(source.addr_reg)
            self.regs.free(target.addr_reg)
            return
        expr = self.parse_exp()
        self.program.emit(f"sw {expr.reg}, 0({target.addr_reg})")
        self.regs.free(expr.reg)
        self.regs.free(target.addr_reg)

    def parse_call(self, name: Token) -> None:
        symbol = self.lookup_symbol(name.sem)
        self.expect("LPAREN")
        actual_index = 0
        actual_args: list[ActualArg] = []
        if not self.at("RPAREN"):
            while True:
                formal_info = symbol.params[actual_index]
                if formal_info.mode == "var":
                    actual = self.parse_actual_lvalue()
                    actual_args.append(ActualArg("var", 4, addr_reg=actual.addr_reg))
                elif formal_info.type_info.kind in {"array", "record"}:
                    actual = self.parse_copy_source()
                    actual_args.append(
                        ActualArg(
                            "aggregate_value",
                            max(1, type_size_words(formal_info.type_info)) * 4,
                            addr_reg=actual.addr_reg,
                        )
                    )
                else:
                    actual_expr = self.parse_exp()
                    actual_args.append(ActualArg("value", 4, reg=actual_expr.reg))
                actual_index += 1
                if not self.at("COMMA"):
                    break
                self.advance()
        self.expect("RPAREN")
        actual_bytes = 0
        for actual in reversed(actual_args):
            actual_bytes += actual.size_bytes
            if actual.kind == "aggregate_value":
                self.program.emit(f"addi $sp, $sp, -{actual.size_bytes}")
                self.emit_copy_words("$sp", actual.addr_reg, actual.size_bytes // 4)
                self.regs.free(actual.addr_reg)
                continue
            self.program.emit("addi $sp, $sp, -4")
            source_reg = actual.addr_reg if actual.kind == "var" else actual.reg
            self.program.emit(f"sw {source_reg}, 0($sp)")
            self.regs.free(source_reg)
        static_link_reg = self.static_link_reg_for_call(symbol)
        self.program.emit("addi $sp, $sp, -4")
        self.program.emit(f"sw {static_link_reg}, 0($sp)")
        self.regs.free(static_link_reg)
        self.program.emit(f"jal {symbol.label}")
        self.program.emit(f"addi $sp, $sp, {actual_bytes + 4}")

    def parse_if(self) -> None:
        self.expect("IF")
        else_label = self.program.new_label("else")
        end_label = self.program.new_label("endif")
        self.emit_false_branch(else_label)
        self.expect("THEN")
        self.parse_stm_list({"ELSE"})
        self.expect("ELSE")
        self.program.emit(f"j {end_label}")
        self.program.emit(f"{else_label}:")
        self.parse_stm_list({"FI"})
        self.expect("FI")
        self.program.emit(f"{end_label}:")

    def parse_while(self) -> None:
        self.expect("WHILE")
        start_label = self.program.new_label("while")
        end_label = self.program.new_label("endwhile")
        self.program.emit(f"{start_label}:")
        self.emit_false_branch(end_label)
        self.expect("DO")
        self.parse_stm_list({"ENDWH"})
        self.expect("ENDWH")
        self.program.emit(f"j {start_label}")
        self.program.emit(f"{end_label}:")

    def parse_read(self) -> None:
        self.expect("READ")
        self.expect("LPAREN")
        name = self.expect("ID")
        target = self.finish_lvalue(name)
        self.expect("RPAREN")
        syscall = 12 if target.type_info.kind == "char" else 5
        self.program.emit(f"li $v0, {syscall}")
        self.program.emit("syscall")
        self.program.emit(f"sw $v0, 0({target.addr_reg})")
        self.regs.free(target.addr_reg)

    def parse_write(self) -> None:
        self.expect("WRITE")
        self.expect("LPAREN")
        expr = self.parse_exp()
        self.expect("RPAREN")
        self.program.emit(f"move $a0, {expr.reg}")
        self.program.emit(f"li $v0, {11 if expr.type_info.kind == 'char' else 1}")
        self.program.emit("syscall")
        self.program.emit("li $a0, 10")
        self.program.emit("li $v0, 11")
        self.program.emit("syscall")
        self.regs.free(expr.reg)

    def parse_return(self) -> None:
        self.expect("RETURN")
        self.expect("LPAREN")
        self.skip_exp()
        self.expect("RPAREN")
        if self.current_proc_end is None:
            self.program.emit("li $v0, 10")
            self.program.emit("syscall")
        else:
            self.program.emit(f"j {self.current_proc_end}")

    def skip_exp(self) -> None:
        self.skip_term()
        if self.current.lex in ADD_OPS:
            self.advance()
            self.skip_exp()

    def skip_term(self) -> None:
        self.skip_factor()
        if self.current.lex in MULT_OPS:
            self.advance()
            self.skip_term()

    def skip_factor(self) -> None:
        if self.at("LPAREN"):
            self.advance()
            self.skip_exp()
            self.expect("RPAREN")
            return
        if self.at("INTC", "CHARC"):
            self.advance()
            return
        if self.at("ID"):
            self.advance()
            while True:
                if self.at("LMIDPAREN"):
                    self.advance()
                    self.skip_exp()
                    self.expect("RMIDPAREN")
                    continue
                if self.at("DOT"):
                    self.advance()
                    self.expect("ID")
                    continue
                break
            return
        raise CodegenError(f"line {self.current.line}: expected expression factor")

    def emit_false_branch(self, target_label: str) -> None:
        left = self.parse_exp()
        op = self.advance()
        right = self.parse_exp()
        if op.lex == "LT":
            self.program.emit(f"bge {left.reg}, {right.reg}, {target_label}")
        elif op.lex == "EQ":
            self.program.emit(f"bne {left.reg}, {right.reg}, {target_label}")
        else:
            raise CodegenError(f"line {op.line}: expected comparison operator")
        self.regs.free(left.reg)
        self.regs.free(right.reg)

    def parse_exp(self) -> ExprReg:
        left = self.parse_term()
        if self.current.lex in ADD_OPS:
            op = self.advance()
            right = self.parse_exp()
            if op.lex == "PLUS":
                self.program.emit(f"add {left.reg}, {left.reg}, {right.reg}")
            else:
                self.program.emit(f"sub {left.reg}, {left.reg}, {right.reg}")
            self.regs.free(right.reg)
            return ExprReg(left.reg, INTEGER)
        return left

    def parse_term(self) -> ExprReg:
        left = self.parse_factor()
        if self.current.lex in MULT_OPS:
            op = self.advance()
            right = self.parse_term()
            if op.lex == "TIMES":
                self.program.emit(f"mul {left.reg}, {left.reg}, {right.reg}")
            else:
                self.program.emit(f"div {left.reg}, {left.reg}, {right.reg}")
            self.regs.free(right.reg)
            return ExprReg(left.reg, INTEGER)
        return left

    def parse_factor(self) -> ExprReg:
        if self.at("LPAREN"):
            self.advance()
            expr = self.parse_exp()
            self.expect("RPAREN")
            return expr
        if self.at("INTC"):
            token = self.advance()
            reg = self.regs.alloc()
            self.program.emit(f"li {reg}, {int(token.sem)}")
            return ExprReg(reg, INTEGER, int(token.sem))
        if self.at("CHARC"):
            token = self.advance()
            reg = self.regs.alloc()
            value = ord(token.sem[0]) if token.sem else 0
            self.program.emit(f"li {reg}, {value}")
            return ExprReg(reg, CHAR)
        if self.at("ID"):
            target = self.finish_lvalue(self.advance())
            reg = self.regs.alloc()
            self.program.emit(f"lw {reg}, 0({target.addr_reg})")
            self.regs.free(target.addr_reg)
            return ExprReg(reg, target.type_info)
        raise CodegenError(f"line {self.current.line}: expected expression factor")

    def parse_actual_lvalue(self) -> LValue:
        return self.finish_lvalue(self.expect("ID"))

    def parse_copy_source(self) -> LValue:
        if self.at("LPAREN"):
            self.advance()
            source = self.parse_copy_source()
            self.expect("RPAREN")
            return source
        return self.finish_lvalue(self.expect("ID"))

    def emit_copy_words(self, dest_addr: str, source_addr: str, word_count: int) -> None:
        temp = self.regs.alloc()
        for offset in range(0, word_count * 4, 4):
            self.program.emit(f"lw {temp}, {offset}({source_addr})")
            self.program.emit(f"sw {temp}, {offset}({dest_addr})")
        self.regs.free(temp)

    def finish_lvalue(self, name: Token) -> LValue:
        symbol = self.lookup_symbol(name.sem)
        addr = self.regs.alloc()
        if symbol.storage == "global":
            self.program.emit(f"la {addr}, {symbol.label}")
        else:
            if symbol.scope_level == self.scope.level:
                frame_reg = "$fp"
                owns_frame_reg = False
            else:
                frame_reg = self.frame_reg_for_level(symbol.scope_level)
                owns_frame_reg = True
            if symbol.storage == "param" and symbol.mode == "var":
                self.program.emit(f"lw {addr}, {symbol.offset}({frame_reg})")
            else:
                self.program.emit(f"addi {addr}, {frame_reg}, {symbol.offset}")
            if owns_frame_reg:
                self.regs.free(frame_reg)
        type_info = symbol.type_info

        if self.at("LMIDPAREN"):
            type_info = self.apply_array_index(name, addr, type_info)
        elif self.at("DOT"):
            self.advance()
            field = self.expect("ID")
            offset = field_offset_words(type_info, field.sem)
            if offset:
                self.program.emit(f"addi {addr}, {addr}, {offset * 4}")
            type_info = type_info.fields[field.sem]
            if self.at("LMIDPAREN"):
                type_info = self.apply_array_index(field, addr, type_info)
        return LValue(addr, type_info, name.sem)

    def apply_array_index(self, name: Token, base_addr: str, array_type: TypeInfo) -> TypeInfo:
        self.expect("LMIDPAREN")
        index = self.parse_exp()
        self.expect("RMIDPAREN")
        low = array_type.low or 0
        element_type = array_type.element or UNKNOWN
        element_words = type_size_words(element_type)
        if low:
            self.program.emit(f"addi {index.reg}, {index.reg}, {-low}")
        byte_scale = element_words * 4
        if byte_scale > 1 and byte_scale & (byte_scale - 1) == 0:
            # Optimization: when byte_scale is a power of 2, use shift-left
            # instead of multiplication (e.g. *4 => sll 2).
            shift = int(math.log2(byte_scale))
            self.program.emit(f"sll {index.reg}, {index.reg}, {shift}")
        elif byte_scale != 1:
            scale = self.regs.alloc()
            self.program.emit(f"li {scale}, {byte_scale}")
            self.program.emit(f"mul {index.reg}, {index.reg}, {scale}")
            self.regs.free(scale)
        self.program.emit(f"add {base_addr}, {base_addr}, {index.reg}")
        self.regs.free(index.reg)
        return element_type


def run_frontend(source: Path, work_dir: Path) -> FrontendArtifacts:
    source_text = source.read_text(encoding="utf-8")
    lexer_tokens = SNLLexer(load_grammar(DEFAULT_GRAMMAR)).tokenize(source_text, include_eof=True)
    lex_errors = [t for t in lexer_tokens if t.lex == "ERROR"]
    token_text = format_text(lexer_tokens)
    tokens = [Token(t.line_show, t.lex, t.sem) for t in lexer_tokens]

    parser = SNLParser(tokens)
    tree = parser.parse()
    parse_report = format_parse_result(parser.errors, tree)

    semantic = SNLSemanticAnalyzer(tokens)
    semantic.analyze()
    semantic_report = format_semantic_result(semantic)

    errors: list[str] = []
    errors.extend(f"line {t.line_show}: lexical error {t.sem}" for t in lex_errors)
    errors.extend(parser.errors)
    errors.extend(semantic.errors)
    return FrontendArtifacts(tokens, token_text, parse_report, semantic_report, errors)


class MIPSRunner:
    def __init__(self, assembly: str, inputs: list[str], max_steps: int = 100000) -> None:
        self.assembly = assembly
        self.inputs = inputs
        if max_steps <= 0:
            raise CodegenError("MIPS runner max_steps must be positive")
        self.max_steps = max_steps
        self.output: list[str] = []
        self.regs: dict[str, int] = {name: 0 for name in self.register_names()}
        self.regs["$sp"] = 0x7FFFEFFC
        self.regs["$fp"] = self.regs["$sp"]
        self.regs["$zero"] = 0
        self.memory: dict[int, int] = {}
        self.data_labels: dict[str, int] = {}
        self.data_layout: list[dict[str, int | str]] = []
        self.text_labels: dict[str, int] = {}
        self.instructions: list[str] = []
        self.instruction_labels: list[list[str]] = []
        self.instruction_source_lines: list[int | None] = []
        self.call_stack: list[str] = []
        self.call_events: list[dict[str, int | str]] = []
        self.execution_trace: list[dict[str, object]] = []
        self.memory_checkpoints: dict[str, list[list[int]]] = {}
        self.max_call_depth = 0
        self.checkpoint_interval = 40
        self._trace_step = 0
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
        pending_labels: list[str] = []
        for raw in self.assembly.splitlines():
            source_line: int | None = None
            source_match = re.search(r"#@L(\d+)", raw)
            if source_match:
                source_line = int(source_match.group(1))
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
                start_addr = data_addr
                self.data_labels[label] = start_addr
                if rest.startswith(".word"):
                    values = rest[len(".word") :].strip()
                    words = [int(v.strip()) for v in values.split(",")] if values else [0]
                    for value in words:
                        self.memory[data_addr] = value
                        data_addr += 4
                    self.data_layout.append(
                        {
                            "label": label,
                            "start": start_addr,
                            "size_bytes": len(words) * 4,
                            "words": len(words),
                        }
                    )
                elif rest.startswith(".space"):
                    size = int(rest[len(".space") :].strip())
                    for addr in range(data_addr, data_addr + size, 4):
                        self.memory[addr] = 0
                    data_addr += size
                    self.data_layout.append(
                        {
                            "label": label,
                            "start": start_addr,
                            "size_bytes": size,
                            "words": max(1, size // 4),
                        }
                    )
            elif section == "text":
                if line.endswith(":"):
                    label = line[:-1]
                    self.text_labels[label] = len(self.instructions)
                    pending_labels.append(label)
                else:
                    self.instructions.append(line)
                    self.instruction_labels.append(pending_labels)
                    self.instruction_source_lines.append(source_line)
                    pending_labels = []

    def reg(self, name: str) -> int:
        return self.regs.get(name, 0)

    def set_reg(self, name: str, value: int, changed_regs: set[str] | None = None) -> None:
        if name != "$zero":
            normalized = value & 0xFFFFFFFF
            if self.regs.get(name, 0) != normalized and changed_regs is not None:
                changed_regs.add(name)
            self.regs[name] = normalized

    def signed(self, value: int) -> int:
        value &= 0xFFFFFFFF
        return value - 0x100000000 if value & 0x80000000 else value

    def run(self) -> str:
        pc = self.text_labels.get("main", 0)
        steps = 0
        self.capture_snapshot(pc, None, None, [], [], None)
        while 0 <= pc < len(self.instructions):
            steps += 1
            if steps > self.max_steps:
                raise CodegenError(f"MIPS runner exceeded {self.max_steps} steps")
            next_pc = pc + 1
            inst = self.instructions[pc]
            op, args = self.split_inst(inst)
            changed_regs: set[str] = set()
            memory_writes: list[list[int]] = []
            current_event: dict[str, int | str] | None = None

            if op == "li":
                self.set_reg(args[0], int(args[1]), changed_regs)
            elif op == "la":
                self.set_reg(args[0], self.data_labels[args[1]], changed_regs)
            elif op == "move":
                self.set_reg(args[0], self.reg(args[1]), changed_regs)
            elif op == "lw":
                self.set_reg(args[0], self.memory.get(self.address(args[1]), 0), changed_regs)
            elif op == "sw":
                address = self.address(args[1])
                self.memory[address] = self.reg(args[0])
                memory_writes.append([address, self.reg(args[0])])
            elif op == "add":
                self.set_reg(args[0], self.reg(args[1]) + self.reg(args[2]), changed_regs)
            elif op == "addi":
                self.set_reg(args[0], self.reg(args[1]) + int(args[2]), changed_regs)
            elif op == "sub":
                self.set_reg(args[0], self.reg(args[1]) - self.reg(args[2]), changed_regs)
            elif op == "mul":
                self.set_reg(args[0], self.reg(args[1]) * self.reg(args[2]), changed_regs)
            elif op == "sll":
                self.set_reg(args[0], self.reg(args[1]) << int(args[2]), changed_regs)
            elif op == "div":
                divisor = self.signed(self.reg(args[2]))
                if divisor == 0:
                    raise CodegenError("MIPS runtime division by zero")
                self.set_reg(args[0], int(self.signed(self.reg(args[1])) / divisor), changed_regs)
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
                self.call_stack.append(args[0])
                self.max_call_depth = max(self.max_call_depth, len(self.call_stack))
                current_event = {
                    "event": "call",
                    "target": args[0],
                    "depth": len(self.call_stack),
                    "sp": self.reg("$sp"),
                    "fp": self.reg("$fp"),
                }
                self.call_events.append(current_event)
                self.set_reg("$ra", next_pc, changed_regs)
                next_pc = self.text_labels[args[0]]
            elif op == "jr":
                if args[0] == "$ra":
                    target = self.call_stack.pop() if self.call_stack else "<unknown>"
                    current_event = {
                        "event": "return",
                        "target": target,
                        "depth": len(self.call_stack),
                        "sp": self.reg("$sp"),
                        "fp": self.reg("$fp"),
                    }
                    self.call_events.append(current_event)
                next_pc = self.reg(args[0])
            elif op == "syscall":
                current_event, should_halt = self.handle_syscall(changed_regs)
                if should_halt:
                    self.capture_snapshot(
                        next_pc,
                        pc,
                        inst,
                        sorted(changed_regs),
                        memory_writes,
                        current_event,
                    )
                    break
            else:
                raise CodegenError(f"unsupported MIPS instruction: {inst}")

            self.regs["$zero"] = 0
            self.capture_snapshot(
                next_pc,
                pc,
                inst,
                sorted(changed_regs),
                memory_writes,
                current_event,
            )
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
            raise CodegenError(f"unsupported memory operand: {operand}")
        return self.reg(match.group(2)) + int(match.group(1))

    def handle_syscall(self, changed_regs: set[str]) -> tuple[dict[str, int | str], bool]:
        code = self.reg("$v0")
        if code == 1:
            self.output.append(str(self.signed(self.reg("$a0"))))
            return (
                {
                    "event": "syscall",
                    "code": 1,
                    "detail": f"print-int {self.signed(self.reg('$a0'))}",
                },
                False,
            )
        elif code == 5:
            value = int(self.inputs.pop(0)) if self.inputs else 0
            self.set_reg("$v0", value, changed_regs)
            return ({"event": "syscall", "code": 5, "detail": f"read-int {value}"}, False)
        elif code == 10:
            return ({"event": "syscall", "code": 10, "detail": "exit"}, True)
        elif code == 11:
            self.output.append(chr(self.reg("$a0") & 0xFF))
            return (
                {
                    "event": "syscall",
                    "code": 11,
                    "detail": f"print-char {chr(self.reg('$a0') & 0xFF)!r}",
                },
                False,
            )
        elif code == 12:
            if self.inputs:
                item = self.inputs.pop(0)
                value = ord(item[0]) if not item.lstrip("-").isdigit() else int(item)
            else:
                value = 0
            self.set_reg("$v0", value, changed_regs)
            return ({"event": "syscall", "code": 12, "detail": f"read-char/int {value}"}, False)
        else:
            raise CodegenError(f"unsupported syscall code {code}")
 
    def capture_snapshot(
        self,
        pc: int,
        last_pc: int | None,
        last_instruction: str | None,
        changed_regs: list[str],
        memory_writes: list[list[int]],
        event: dict[str, int | str] | None,
    ) -> None:
        snapshot = {
            "step": self._trace_step,
            "pc": pc,
            "label": self.function_label_for_pc(pc),
            "instruction": self.instructions[pc] if 0 <= pc < len(self.instructions) else "(halt)",
            "last_pc": last_pc,
            "last_label": self.function_label_for_pc(last_pc) if last_pc is not None else "",
            "last_instruction": last_instruction or "(start)",
            "registers": {name: self.regs[name] for name in self.register_names()},
            "call_stack": list(self.call_stack),
            "changed_registers": changed_regs,
            "memory_writes": memory_writes,
            "event": event,
            "output": "".join(self.output),
        }
        self.execution_trace.append(snapshot)
        if self._trace_step % self.checkpoint_interval == 0:
            self.memory_checkpoints[str(self._trace_step)] = self.serialize_memory()
        self._trace_step += 1

    def serialize_memory(self) -> list[list[int]]:
        return [[address, value] for address, value in sorted(self.memory.items())]

    def function_label_for_pc(self, pc: int | None) -> str:
        if pc is None or pc < 0 or pc >= len(self.instructions):
            return ""
        for index in range(pc, -1, -1):
            for label in reversed(self.instruction_labels[index]):
                if label == "main" or label.startswith("proc_"):
                    return label
        return "main"


def compile_source(
    source: Path,
    out_dir: Path,
    input_values: list[str],
    run_target: bool = True,
    max_steps: int = 100000,
) -> tuple[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    frontend = run_frontend(source, out_dir)
    stem = source.stem
    (out_dir / f"{stem}.tokens").write_text(frontend.token_text + "\n", encoding="utf-8")
    (out_dir / f"{stem}.tree").write_text(frontend.parse_report + "\n", encoding="utf-8")
    (out_dir / f"{stem}.semantic").write_text(frontend.semantic_report + "\n", encoding="utf-8")

    if frontend.errors:
        result_text = "Front End\n" + "\n".join(frontend.errors)
        (out_dir / f"{stem}.result").write_text(
            result_text + ("\n" if not result_text.endswith("\n") else ""),
            encoding="utf-8",
        )
        raise CodegenError("front-end checks failed:\n" + "\n".join(frontend.errors))

    assembly = SNLCodeGenerator(frontend.tokens).generate()
    asm_path = out_dir / f"{stem}.asm"
    asm_path.write_text(assembly, encoding="utf-8")
    if run_target:
        try:
            result = MIPSRunner(assembly, input_values, max_steps=max_steps).run()
        except CodegenError as exc:
            result_text = (
                "Front End\n"
                "No lexical, syntax, or semantic errors.\n\n"
                f"MIPS Assembly\n{asm_path}\n\n"
                f"Runtime Error\n{exc}"
            )
            (out_dir / f"{stem}.result").write_text(
                result_text + ("\n" if not result_text.endswith("\n") else ""),
                encoding="utf-8",
            )
            raise
    else:
        result = ""
    result_text = (
        "Front End\n"
        "No lexical, syntax, or semantic errors.\n\n"
        f"MIPS Assembly\n{asm_path}\n\n"
        + ("Program Output\n" f"{result}" if run_target else "Program Output\n<not run>")
    )
    (out_dir / f"{stem}.result").write_text(result_text + ("\n" if not result_text.endswith("\n") else ""), encoding="utf-8")
    return assembly, result_text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compile SNL source to 32-bit MIPS assembly and run the generated target code."
    )
    parser.add_argument("source", type=Path, help="SNL source program")
    parser.add_argument("-o", "--output", type=Path, help="write MIPS assembly to this file")
    parser.add_argument("--out-dir", type=Path, default=Path("test/out"))
    parser.add_argument("--input", nargs="*", default=[], help="input values consumed by READ syscalls")
    parser.add_argument("--no-run", action="store_true", help="only generate assembly")
    parser.add_argument("--max-steps", type=int, default=100000, help="maximum MIPS instructions to execute")
    args = parser.parse_args(argv)

    try:
        out_dir = args.out_dir
        assembly, result_text = compile_source(
            args.source,
            out_dir,
            list(args.input),
            run_target=not args.no_run,
            max_steps=args.max_steps,
        )
        if args.output:
            args.output.write_text(assembly, encoding="utf-8")
        if args.no_run:
            print(assembly)
        else:
            print(result_text)
    except (OSError, CodegenError, json.JSONDecodeError) as exc:
        print(f"snl_codegen.py: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

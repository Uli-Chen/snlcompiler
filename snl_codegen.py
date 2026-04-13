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
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

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


@dataclass
class CGScope:
    name: str
    prefix: str
    symbols: dict[str, CGSymbol] = field(default_factory=dict)
    types: dict[str, TypeInfo] = field(default_factory=dict)


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

    def emit_data(self, line: str) -> None:
        self.data.append(line)

    def emit(self, line: str = "") -> None:
        self.text.append(line)

    def new_label(self, prefix: str) -> str:
        self.label_counter += 1
        return f"{prefix}_{self.label_counter}"

    def render(self) -> str:
        return "\n".join(self.data + [""] + self.text) + "\n"


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


class SNLCodeGenerator:
    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.index = 0
        self.program = MIPSProgram()
        self.regs = RegisterPool()
        self.scopes: list[CGScope] = []
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
        prefix = "g" if not self.scopes else f"p_{mangle(name)}"
        self.scopes.append(CGScope(name, prefix))

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
        label = f"{self.scope.prefix}_{mangle(name)}"
        symbol = CGSymbol(name, kind, type_info, label, line, mode=mode)
        self.declare_symbol(symbol)
        words = type_size_words(type_info)
        if words <= 1:
            self.program.emit_data(f"{label}: .word 0")
        else:
            self.program.emit_data(f"{label}: .space {words * 4}")
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
        label = f"proc_{mangle(name.sem)}"
        proc_symbol = CGSymbol(name.sem, "procedure", UNKNOWN, label, name.line, params=params)
        self.declare_symbol(proc_symbol)
        self.proc_symbols[name.sem] = proc_symbol
        self.expect("SEMI")

        saved_scope = self.scope
        self.enter_scope(name.sem)
        param_symbols: list[CGSymbol] = []
        for param in params:
            storage_type = INTEGER if param.mode == "var" else param.type_info
            symbol = self.declare_storage(param.name, "param", storage_type, param.line, param.mode)
            symbol.type_info = param.type_info
            param_symbols.append(symbol)
        proc_symbol.param_symbols = param_symbols

        proc_text_start = len(self.program.text)
        self.program.emit(f"{label}:")
        self.program.emit("addi $sp, $sp, -4")
        self.program.emit("sw $ra, 0($sp)")
        proc_end = self.program.new_label(f"{label}_end")
        previous_end = self.current_proc_end
        self.current_proc_end = proc_end
        self.parse_declare_part(emit_procedure_text=True)
        self.parse_program_body()
        self.program.emit(f"{proc_end}:")
        self.program.emit("lw $ra, 0($sp)")
        self.program.emit("addi $sp, $sp, 4")
        self.program.emit("jr $ra")
        self.current_proc_end = previous_end
        self.leave_scope()

        # Procedure text must appear before main.  It already does because
        # global declarations are parsed before main is emitted.
        assert proc_text_start < len(self.program.text)
        assert saved_scope is self.scope

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

    def parse_assignment(self, name: Token) -> None:
        target = self.finish_lvalue(name)
        self.expect("ASSIGN")
        expr = self.parse_exp()
        self.program.emit(f"sw {expr.reg}, 0({target.addr_reg})")
        self.regs.free(expr.reg)
        self.regs.free(target.addr_reg)

    def parse_call(self, name: Token) -> None:
        symbol = self.lookup_symbol(name.sem)
        self.expect("LPAREN")
        actual_index = 0
        if not self.at("RPAREN"):
            while True:
                formal_symbol = symbol.param_symbols[actual_index]
                formal_info = symbol.params[actual_index]
                if formal_info.mode == "var":
                    actual_name = self.expect("ID")
                    actual = self.finish_lvalue(actual_name)
                    self.program.emit(f"sw {actual.addr_reg}, {formal_symbol.label}")
                    self.regs.free(actual.addr_reg)
                else:
                    actual_expr = self.parse_exp()
                    self.program.emit(f"sw {actual_expr.reg}, {formal_symbol.label}")
                    self.regs.free(actual_expr.reg)
                actual_index += 1
                if not self.at("COMMA"):
                    break
                self.advance()
        self.expect("RPAREN")
        self.program.emit(f"jal {symbol.label}")

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
        expr = self.parse_exp()
        self.expect("RPAREN")
        self.regs.free(expr.reg)
        if self.current_proc_end is None:
            self.program.emit("li $v0, 10")
            self.program.emit("syscall")
        else:
            self.program.emit(f"j {self.current_proc_end}")

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

    def finish_lvalue(self, name: Token) -> LValue:
        symbol = self.lookup_symbol(name.sem)
        addr = self.regs.alloc()
        if symbol.kind == "param" and symbol.mode == "var":
            self.program.emit(f"lw {addr}, {symbol.label}")
        else:
            self.program.emit(f"la {addr}, {symbol.label}")
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
        self.program.emit(f"addi {index.reg}, {index.reg}, {-low}")
        if element_words != 1:
            scale = self.regs.alloc()
            self.program.emit(f"li {scale}, {element_words}")
            self.program.emit(f"mul {index.reg}, {index.reg}, {scale}")
            self.regs.free(scale)
        scale4 = self.regs.alloc()
        self.program.emit(f"li {scale4}, 4")
        self.program.emit(f"mul {index.reg}, {index.reg}, {scale4}")
        self.regs.free(scale4)
        self.program.emit(f"add {base_addr}, {base_addr}, {index.reg}")
        self.regs.free(index.reg)
        return element_type


def run_frontend(source: Path, work_dir: Path) -> tuple[list[Token], str, str, str]:
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
    if errors:
        raise CodegenError("front-end checks failed:\n" + "\n".join(errors))
    return tokens, token_text, parse_report, semantic_report


class MIPSRunner:
    def __init__(self, assembly: str, inputs: list[str]) -> None:
        self.assembly = assembly
        self.inputs = inputs
        self.output: list[str] = []
        self.regs: dict[str, int] = {name: 0 for name in self.register_names()}
        self.regs["$sp"] = 0x7FFFEFFC
        self.regs["$zero"] = 0
        self.memory: dict[int, int] = {}
        self.data_labels: dict[str, int] = {}
        self.text_labels: dict[str, int] = {}
        self.instructions: list[str] = []
        self.parse_assembly()

    @staticmethod
    def register_names() -> list[str]:
        return (
            ["$zero", "$at", "$v0", "$v1", "$a0", "$a1", "$a2", "$a3"]
            + [f"$t{i}" for i in range(10)]
            + [f"$s{i}" for i in range(8)]
            + ["$sp", "$ra"]
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
                raise CodegenError("MIPS runner exceeded 100000 steps")
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
                    raise CodegenError("MIPS runtime division by zero")
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
                raise CodegenError(f"unsupported MIPS instruction: {inst}")

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
            raise CodegenError(f"unsupported memory operand: {operand}")
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
            raise CodegenError(f"unsupported syscall code {code}")
        return False


def compile_source(source: Path, out_dir: Path, input_values: list[str], run_target: bool = True) -> tuple[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    tokens, token_text, parse_report, semantic_report = run_frontend(source, out_dir)
    stem = source.stem
    (out_dir / f"{stem}.tokens").write_text(token_text + "\n", encoding="utf-8")
    (out_dir / f"{stem}.tree").write_text(parse_report + "\n", encoding="utf-8")
    (out_dir / f"{stem}.semantic").write_text(semantic_report + "\n", encoding="utf-8")

    assembly = SNLCodeGenerator(tokens).generate()
    asm_path = out_dir / f"{stem}.asm"
    asm_path.write_text(assembly, encoding="utf-8")
    result = MIPSRunner(assembly, input_values).run() if run_target else ""
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
    parser.add_argument("--out-dir", type=Path, default=Path("playground/snlcompiler/test/out"))
    parser.add_argument("--input", nargs="*", default=[], help="input values consumed by READ syscalls")
    parser.add_argument("--no-run", action="store_true", help="only generate assembly")
    args = parser.parse_args(argv)

    try:
        out_dir = args.out_dir
        assembly, result_text = compile_source(args.source, out_dir, list(args.input), run_target=not args.no_run)
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

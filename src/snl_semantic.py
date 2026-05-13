#!/usr/bin/env python3
"""Semantic analysis for the SNL AST."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from snl_parser import *


@dataclass
class TypeInfo:
    kind: str
    low: int | None = None
    high: int | None = None
    element: "TypeInfo | None" = None
    fields: dict[str, "TypeInfo"] = field(default_factory=dict)

    def display(self) -> str:
        if self.kind == "array":
            element = self.element.display() if self.element else "unknown"
            return f"array[{self.low}..{self.high}] of {element}"
        if self.kind == "record":
            if not self.fields:
                return "record{}"
            fields = ", ".join(f"{name}: {typ.display()}" for name, typ in self.fields.items())
            return f"record{{{fields}}}"
        return self.kind


INTEGER = TypeInfo("integer")
CHAR = TypeInfo("char")
BOOL = TypeInfo("bool")
UNKNOWN = TypeInfo("unknown")
PROCEDURE_TYPE = TypeInfo("procedure")


@dataclass
class ParamInfo:
    name: str
    mode: str
    type_info: TypeInfo
    line: int
    symbol: "Symbol | None" = None

    def display(self) -> str:
        return f"{self.mode} {self.name}: {self.type_info.display()}"


@dataclass
class Symbol:
    name: str
    kind: str
    type_info: TypeInfo
    line: int
    params: list[ParamInfo] = field(default_factory=list)
    mode: str = ""
    label: str = ""
    storage: str = ""
    offset: int = 0
    param_symbols: list["Symbol"] = field(default_factory=list)

    def type_display(self) -> str:
        if self.kind == "procedure":
            return "procedure(" + ", ".join(param.display() for param in self.params) + ")"
        return self.type_info.display()

    def other_display(self) -> str:
        if self.kind == "param":
            return f"mode={self.mode}"
        if self.kind == "procedure":
            return f"params={len(self.params)}"
        return ""


@dataclass
class Scope:
    number: int
    name: str
    level: int
    parent: int | None
    symbols: dict[str, Symbol] = field(default_factory=dict)


class SemanticError(RuntimeError):
    pass


class SymbolTable:
    def __init__(self) -> None:
        self.scopes: list[Scope] = []
        self.stack: list[Scope] = []

    def enter(self, name: str) -> Scope:
        parent = self.stack[-1].number if self.stack else None
        scope = Scope(len(self.scopes), name, len(self.stack), parent)
        self.scopes.append(scope)
        self.stack.append(scope)
        return scope

    def leave(self) -> None:
        if self.stack:
            self.stack.pop()

    @property
    def current(self) -> Scope:
        return self.stack[-1]

    def declare(self, symbol: Symbol) -> Symbol | None:
        if symbol.name in self.current.symbols:
            return self.current.symbols[symbol.name]
        self.current.symbols[symbol.name] = symbol
        return None

    def lookup(self, name: str) -> Symbol | None:
        for scope in reversed(self.stack):
            symbol = scope.symbols.get(name)
            if symbol is not None:
                return symbol
        return None


class SNLSemanticAnalyzer:
    def __init__(self, program: Program) -> None:
        self.program = program
        self.errors: list[str] = []
        self.table = SymbolTable()

    def analyze(self) -> None:
        self.table.enter("global")
        self.declare(Symbol(self.program.name, "program", UNKNOWN, self.program.line))
        self.analyze_declare_part(self.program.type_decls, self.program.var_decls, self.program.proc_decls)
        self.analyze_statements(self.program.body)
        self.table.leave()

    def analyze_declare_part(
        self,
        type_decls: list,
        var_decls: list[VarDecl],
        proc_decls: list[ProcDecl],
    ) -> None:
        for decl in type_decls:
            type_info = self.resolve_type(decl.type_node)
            self.declare(Symbol(decl.name, "type", type_info, decl.line))

        for decl in var_decls:
            var_type = self.resolve_type(decl.type_node)
            for name, line in decl.names:
                symbol = Symbol(name, "var", var_type, line)
                self.declare(symbol)
                decl.symbols.append(symbol)

        for proc in proc_decls:
            params = [self.build_param_info(param) for param in proc.params]
            symbol = Symbol(proc.name, "procedure", PROCEDURE_TYPE, proc.line, params=params)
            proc.symbol = symbol
            self.declare(symbol)

        for proc in proc_decls:
            self.analyze_proc(proc)

    def analyze_proc(self, proc: ProcDecl) -> None:
        self.table.enter(f"procedure {proc.name}")
        proc_symbol = proc.symbol if isinstance(proc.symbol, Symbol) else None
        param_symbols: list[Symbol] = []
        for param, param_info in zip(proc.params, proc_symbol.params if proc_symbol else []):
            symbol = Symbol(param.name, "param", param_info.type_info, param.line, mode=param.mode)
            param.symbol = symbol
            param_info.symbol = symbol
            self.declare(symbol)
            param_symbols.append(symbol)
        if proc_symbol is not None:
            proc_symbol.param_symbols = param_symbols

        self.analyze_declare_part(proc.type_decls, proc.var_decls, proc.proc_decls)
        self.analyze_statements(proc.body)
        self.table.leave()

    def build_param_info(self, param: Param) -> ParamInfo:
        type_info = self.resolve_type(param.type_node)
        if param.mode == "value" and is_aggregate(type_info):
            self.error(param.line, "unsupported aggregate value operation: array/record value parameters are not supported")
        return ParamInfo(param.name, param.mode, type_info, param.line)

    def resolve_type(self, type_node: TypeNode) -> TypeInfo:
        if type_node.kind == "integer":
            type_node.type_info = INTEGER
            return INTEGER
        if type_node.kind == "char":
            type_node.type_info = CHAR
            return CHAR
        if type_node.kind == "array":
            element = self.resolve_type(type_node.element or TypeNode("unknown", type_node.line))
            low, high = type_node.low, type_node.high
            if low is not None and high is not None and low > high:
                self.error(type_node.line, f"array lower bound {low} is greater than upper bound {high}")
            type_info = TypeInfo("array", low, high, element)
            type_node.type_info = type_info
            return type_info
        if type_node.kind == "record":
            fields: dict[str, TypeInfo] = {}
            for field_decl in type_node.fields:
                self.resolve_field_decl(field_decl, fields)
            type_info = TypeInfo("record", fields=fields)
            type_node.type_info = type_info
            return type_info
        if type_node.kind == "alias":
            symbol = self.table.lookup(type_node.name)
            if symbol is None:
                self.error(type_node.line, f"undeclared type identifier '{type_node.name}'")
                type_node.type_info = UNKNOWN
                return UNKNOWN
            if symbol.kind != "type":
                self.error(type_node.line, f"identifier '{type_node.name}' is {symbol.kind}, expected type identifier")
                type_node.type_info = UNKNOWN
                return UNKNOWN
            type_node.type_info = symbol.type_info
            return symbol.type_info
        type_node.type_info = UNKNOWN
        return UNKNOWN

    def resolve_field_decl(self, field_decl: FieldDecl, fields: dict[str, TypeInfo]) -> None:
        field_type = self.resolve_type(field_decl.type_node)
        for name, line in field_decl.names:
            if name in fields:
                self.error(line, f"duplicate field identifier '{name}' in record type")
            else:
                fields[name] = field_type

    def analyze_statements(self, statements: list[Stmt]) -> None:
        for stmt in statements:
            if isinstance(stmt, AssignStmt):
                self.check_assignment(stmt)
            elif isinstance(stmt, CallStmt):
                self.check_call(stmt)
            elif isinstance(stmt, IfStmt):
                self.check_condition(stmt.line, stmt.condition, "if")
                self.analyze_statements(stmt.then_body)
                self.analyze_statements(stmt.else_body)
            elif isinstance(stmt, WhileStmt):
                self.check_condition(stmt.line, stmt.condition, "while")
                self.analyze_statements(stmt.body)
            elif isinstance(stmt, ReadStmt):
                self.check_read(stmt)
            elif isinstance(stmt, WriteStmt):
                expr_type = self.check_expr(stmt.expr)
                if is_aggregate(expr_type):
                    self.error(stmt.line, "unsupported aggregate value operation: write expects a scalar expression")
            elif isinstance(stmt, ReturnStmt):
                self.check_expr(stmt.expr)

    def check_assignment(self, stmt: AssignStmt) -> None:
        left = self.check_var_ref(stmt.target, require_variable=True)
        right = self.check_expr(stmt.expr)
        if not stmt.target.assignable:
            self.error(stmt.line, f"left side of assignment is not an assignable variable '{stmt.target.name}'")
        if is_aggregate(left) or is_aggregate(right):
            self.error(stmt.line, "unsupported aggregate value operation: assign array/record elements or fields instead")
            return
        if not same_type(left, right):
            self.error(stmt.line, f"assignment type mismatch: left is {left.display()}, right is {right.display()}")

    def check_call(self, stmt: CallStmt) -> None:
        symbol = self.table.lookup(stmt.name)
        if symbol is None:
            self.error(stmt.line, f"undeclared procedure identifier '{stmt.name}'")
            for arg in stmt.args:
                self.check_expr(arg)
            return
        if symbol.kind != "procedure":
            self.error(stmt.line, f"identifier '{stmt.name}' is {symbol.kind}, expected procedure identifier")
            for arg in stmt.args:
                self.check_expr(arg)
            return

        stmt.symbol = symbol
        if len(symbol.params) != len(stmt.args):
            self.error(stmt.line, f"procedure '{stmt.name}' expects {len(symbol.params)} argument(s), got {len(stmt.args)}")

        for index, arg in enumerate(stmt.args):
            actual_type = self.check_expr(arg)
            if index >= len(symbol.params):
                continue
            formal = symbol.params[index]
            assignable = isinstance(arg, VarExpr) and arg.ref is not None and arg.ref.assignable
            if formal.mode == "var" and not assignable:
                self.error(stmt.line, f"argument {index + 1} for var parameter '{formal.name}' must be an assignable variable")
            if formal.mode == "value" and is_aggregate(actual_type):
                self.error(stmt.line, "unsupported aggregate value operation: array/record value arguments are not supported")
            if not same_type(formal.type_info, actual_type):
                self.error(
                    stmt.line,
                    f"argument {index + 1} type mismatch for parameter '{formal.name}': "
                    f"expected {formal.type_info.display()}, got {actual_type.display()}",
                )

    def check_condition(self, line: int, expr: Expr, kind: str) -> None:
        condition_type = self.check_expr(expr)
        if not same_type(condition_type, BOOL):
            self.error(line, f"{kind} condition must be bool")

    def check_read(self, stmt: ReadStmt) -> None:
        target_type = self.check_var_ref(stmt.target, require_variable=True)
        if not stmt.target.assignable:
            self.error(stmt.line, f"read target '{stmt.target.name}' must be an assignable variable")
        if is_aggregate(target_type):
            self.error(stmt.line, f"read target '{stmt.target.name}' must be scalar, got {target_type.display()}")

    def check_expr(self, expr: Expr) -> TypeInfo:
        if isinstance(expr, ConstExpr):
            expr.type_info = INTEGER
            expr.const_int = expr.value
            return INTEGER
        if isinstance(expr, CharExpr):
            expr.type_info = CHAR
            return CHAR
        if isinstance(expr, VarExpr):
            if expr.ref is None:
                expr.type_info = UNKNOWN
                return UNKNOWN
            expr_type = self.check_var_ref(expr.ref, require_variable=False)
            expr.type_info = expr_type
            return expr_type
        if isinstance(expr, BinaryExpr):
            left = self.check_expr(expr.left) if expr.left else UNKNOWN
            right = self.check_expr(expr.right) if expr.right else UNKNOWN
            if expr.op in {"+", "-", "*", "/"}:
                if left.kind not in {"integer", "unknown"}:
                    self.error(expr.line, f"left operand of '{expr.op}' must be integer")
                if right.kind not in {"integer", "unknown"}:
                    self.error(expr.line, f"right operand of '{expr.op}' must be integer")
                expr.type_info = INTEGER
                return INTEGER
            if expr.op in {"<", "="}:
                if not same_type(left, right):
                    self.error(expr.line, f"comparison operands have incompatible types: {left.display()} and {right.display()}")
                if is_aggregate(left) or is_aggregate(right):
                    self.error(expr.line, "unsupported aggregate value operation: compare scalar values only")
                expr.type_info = BOOL
                return BOOL
        expr.type_info = UNKNOWN
        return UNKNOWN

    def check_var_ref(self, ref: VarRef, require_variable: bool) -> TypeInfo:
        symbol = self.table.lookup(ref.name)
        if symbol is None:
            self.error(ref.line, f"undeclared identifier '{ref.name}'")
            ref.type_info = UNKNOWN
            ref.assignable = False
            return UNKNOWN
        if symbol.kind not in {"var", "param"}:
            if require_variable:
                self.error(ref.line, f"identifier '{ref.name}' is {symbol.kind}, expected variable identifier")
            ref.symbol = symbol
            ref.type_info = symbol.type_info
            ref.assignable = False
            return symbol.type_info

        ref.symbol = symbol
        ref.assignable = True
        current_type = symbol.type_info
        for selector in ref.selectors:
            if isinstance(selector, IndexSelector):
                index_type = self.check_expr(selector.expr)
                if index_type.kind not in {"integer", "unknown"}:
                    self.error(selector.expr.line, f"array index for '{ref.name}' must be integer")
                if current_type.kind != "array":
                    if current_type.kind != "unknown":
                        self.error(selector.expr.line, f"identifier '{ref.name}' is not an array")
                    current_type = UNKNOWN
                else:
                    if (
                        selector.expr.const_int is not None
                        and current_type.low is not None
                        and current_type.high is not None
                        and not current_type.low <= selector.expr.const_int <= current_type.high
                    ):
                        self.error(
                            selector.expr.line,
                            f"array index {selector.expr.const_int} out of bounds "
                            f"[{current_type.low}..{current_type.high}] for '{ref.name}'",
                        )
                    current_type = current_type.element or UNKNOWN
            elif isinstance(selector, FieldSelector):
                if current_type.kind != "record":
                    if current_type.kind != "unknown":
                        self.error(selector.line, f"identifier '{ref.name}' is not a record")
                    current_type = UNKNOWN
                else:
                    field_type = current_type.fields.get(selector.name)
                    if field_type is None:
                        self.error(selector.line, f"record '{ref.name}' has no field '{selector.name}'")
                        current_type = UNKNOWN
                    else:
                        current_type = field_type
                if selector.index is not None:
                    index_type = self.check_expr(selector.index.expr)
                    if index_type.kind not in {"integer", "unknown"}:
                        self.error(selector.index.expr.line, f"array index for '{selector.name}' must be integer")
                    if current_type.kind != "array":
                        if current_type.kind != "unknown":
                            self.error(selector.index.expr.line, f"field '{selector.name}' is not an array")
                        current_type = UNKNOWN
                    else:
                        if (
                            selector.index.expr.const_int is not None
                            and current_type.low is not None
                            and current_type.high is not None
                            and not current_type.low <= selector.index.expr.const_int <= current_type.high
                        ):
                            self.error(
                                selector.index.expr.line,
                                f"array index {selector.index.expr.const_int} out of bounds "
                                f"[{current_type.low}..{current_type.high}] for field '{selector.name}'",
                            )
                        current_type = current_type.element or UNKNOWN

        ref.type_info = current_type
        return current_type

    def declare(self, symbol: Symbol) -> None:
        duplicate = self.table.declare(symbol)
        if duplicate is not None:
            self.error(
                symbol.line,
                f"duplicate definition of identifier '{symbol.name}' in scope "
                f"'{self.table.current.name}', first defined at line {duplicate.line}",
            )

    def error(self, line: int, message: str) -> None:
        self.errors.append(f"line {line}: {message}")


def is_aggregate(type_info: TypeInfo) -> bool:
    return type_info.kind in {"array", "record"}


def same_type(left: TypeInfo, right: TypeInfo) -> bool:
    if left.kind == "unknown" or right.kind == "unknown":
        return True
    if left.kind != right.kind:
        return False
    if left.kind in {"integer", "char", "bool"}:
        return True
    if left.kind == "array":
        return (
            left.low == right.low
            and left.high == right.high
            and left.element is not None
            and right.element is not None
            and same_type(left.element, right.element)
        )
    if left.kind == "record":
        if left.fields.keys() != right.fields.keys():
            return False
        return all(same_type(left.fields[name], right.fields[name]) for name in left.fields)
    return True


def format_semantic_errors(errors: list[str]) -> str:
    return "\n".join(errors or ["No semantic errors."])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Analyze SNL token semantics.")
    parser.add_argument("tokens", type=Path, help="token file generated by snl_lexer.py")
    args = parser.parse_args(argv)

    try:
        tokens = load_tokens(args.tokens)
        snl_parser = SNLParser(tokens)
        program = snl_parser.parse()
        if snl_parser.errors:
            print("\n".join(snl_parser.errors), file=sys.stderr)
            return 1
        analyzer = SNLSemanticAnalyzer(program)
        analyzer.analyze()
        print(format_semantic_errors(analyzer.errors))
        return 1 if analyzer.errors else 0
    except (OSError, json.JSONDecodeError) as exc:
        print(f"snl_semantic.py: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

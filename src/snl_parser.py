#!/usr/bin/env python3
"""Recursive-descent parser for SNL.

This module is the single syntactic entry point for the compiler.  It turns a
token stream into a typed AST; semantic analysis and code generation walk that
AST instead of reparsing tokens.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


TYPE_START = {"INTEGER", "CHAR", "ARRAY", "RECORD", "ID"}
FIELD_TYPE_START = {"INTEGER", "CHAR", "ARRAY"}
STMT_START = {"IF", "WHILE", "READ", "WRITE", "RETURN", "ID"}
DECL_START = {"TYPE", "VAR", "PROCEDURE", "BEGIN"}
EXPR_START = {"LPAREN", "INTC", "CHARC", "ID"}
EXPR_FOLLOW = {"SEMI", "COMMA", "RPAREN", "RMIDPAREN", "THEN", "DO", "ELSE", "FI", "ENDWH", "END", "EOF"}
ADD_OPS = {"PLUS": "+", "MINUS": "-"}
MULT_OPS = {"TIMES": "*", "OVER": "/"}
CMP_OPS = {"LT": "<", "EQ": "="}
TOKEN_DISPLAY = {
    "PROGRAM": "program",
    "TYPE": "type",
    "VAR": "var",
    "PROCEDURE": "procedure",
    "BEGIN": "begin",
    "END": "end",
    "IF": "if",
    "THEN": "then",
    "ELSE": "else",
    "FI": "fi",
    "WHILE": "while",
    "DO": "do",
    "ENDWH": "endwh",
    "READ": "read",
    "WRITE": "write",
    "RETURN": "return",
    "INTEGER": "integer",
    "CHAR": "char",
    "ARRAY": "array",
    "RECORD": "record",
    "OF": "of",
    "ID": "identifier",
    "INTC": "integer constant",
    "CHARC": "character constant",
    "ASSIGN": ":=",
    "EQ": "=",
    "LT": "<",
    "PLUS": "+",
    "MINUS": "-",
    "TIMES": "*",
    "OVER": "/",
    "LPAREN": "(",
    "RPAREN": ")",
    "LMIDPAREN": "[",
    "RMIDPAREN": "]",
    "UNDERANGE": "..",
    "SEMI": ";",
    "COMMA": ",",
    "DOT": ".",
    "EOF": "end of file",
}


@dataclass(frozen=True)
class Token:
    line: int
    lex: str
    sem: str = ""

    def display(self) -> str:
        return f"{self.lex}({self.sem})" if self.sem else self.lex


@dataclass
class TypeNode:
    kind: str
    line: int
    name: str = ""
    low: int | None = None
    high: int | None = None
    element: "TypeNode | None" = None
    fields: list["FieldDecl"] = field(default_factory=list)
    type_info: object | None = None


@dataclass
class FieldDecl:
    type_node: TypeNode
    names: list[tuple[str, int]]


@dataclass
class TypeDecl:
    name: str
    type_node: TypeNode
    line: int


@dataclass
class VarDecl:
    type_node: TypeNode
    names: list[tuple[str, int]]
    symbols: list[object] = field(default_factory=list)


@dataclass
class Param:
    name: str
    mode: Literal["value", "var"]
    type_node: TypeNode
    line: int
    symbol: object | None = None


@dataclass
class IndexSelector:
    expr: "Expr"


@dataclass
class FieldSelector:
    name: str
    line: int
    index: IndexSelector | None = None


Selector = IndexSelector | FieldSelector


@dataclass
class VarRef:
    name: str
    line: int
    selectors: list[Selector] = field(default_factory=list)
    symbol: object | None = None
    type_info: object | None = None
    assignable: bool = False


@dataclass
class Expr:
    line: int
    type_info: object | None = None
    const_int: int | None = None


@dataclass
class ConstExpr(Expr):
    value: int = 0


@dataclass
class CharExpr(Expr):
    value: str = ""


@dataclass
class VarExpr(Expr):
    ref: VarRef | None = None


@dataclass
class BinaryExpr(Expr):
    op: str = ""
    left: Expr | None = None
    right: Expr | None = None


@dataclass
class Stmt:
    line: int


@dataclass
class AssignStmt(Stmt):
    target: VarRef
    expr: Expr


@dataclass
class CallStmt(Stmt):
    name: str
    args: list[Expr]
    symbol: object | None = None


@dataclass
class IfStmt(Stmt):
    condition: Expr
    then_body: list[Stmt]
    else_body: list[Stmt]


@dataclass
class WhileStmt(Stmt):
    condition: Expr
    body: list[Stmt]


@dataclass
class ReadStmt(Stmt):
    target: VarRef


@dataclass
class WriteStmt(Stmt):
    expr: Expr


@dataclass
class ReturnStmt(Stmt):
    expr: Expr


@dataclass
class ProcDecl:
    name: str
    line: int
    params: list[Param]
    type_decls: list[TypeDecl]
    var_decls: list[VarDecl]
    proc_decls: list["ProcDecl"]
    body: list[Stmt]
    symbol: object | None = None


@dataclass
class Program:
    name: str
    line: int
    type_decls: list[TypeDecl]
    var_decls: list[VarDecl]
    proc_decls: list[ProcDecl]
    body: list[Stmt]


class ParserError(RuntimeError):
    pass


def load_tokens(path: Path) -> list[Token]:
    text = path.read_text(encoding="utf-8")
    stripped = text.lstrip()
    if stripped.startswith("["):
        data = json.loads(text)
        tokens = [
            Token(
                int(item.get("line_show", item.get("line", 0))),
                str(item["lex"]),
                str(item.get("sem", "")),
            )
            for item in data
        ]
    else:
        tokens = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("LineShow") or line.startswith("--------"):
                continue
            parts = line.split(maxsplit=2)
            if len(parts) < 2:
                continue
            try:
                line_show = int(parts[0])
            except ValueError:
                continue
            tokens.append(Token(line_show, parts[1], parts[2].strip() if len(parts) == 3 else ""))

    if not tokens:
        raise ParserError(f"{path}: no tokens found")
    if tokens[-1].lex != "EOF":
        tokens.append(Token(tokens[-1].line, "EOF"))
    return tokens


class SNLParser:
    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.index = 0
        self.errors: list[str] = []

    @property
    def current(self) -> Token:
        return self.tokens[self.index] if self.index < len(self.tokens) else self.tokens[-1]

    def at(self, *lex_types: str) -> bool:
        return self.current.lex in lex_types

    def peek(self, offset: int = 1) -> Token:
        index = min(self.index + offset, len(self.tokens) - 1)
        return self.tokens[index]

    def advance(self) -> Token:
        token = self.current
        if self.index < len(self.tokens) - 1:
            self.index += 1
        return token

    def expect(
        self,
        lex_type: str,
        *,
        context: str = "",
        recover_to: set[str] | None = None,
    ) -> Token:
        if self.current.lex == lex_type:
            return self.advance()
        token = self.current
        detail = f"expected {self.token_name(lex_type)}"
        if context:
            detail += f" {context}"
        detail += f", found {token.display()}"
        self.error_at(token.line, "missing-token", detail)
        if recover_to is not None and token.lex not in recover_to:
            self.recover({lex_type} | recover_to)
            if self.current.lex == lex_type:
                return self.advance()
        return Token(token.line, lex_type)

    def error(self, message: str) -> None:
        self.error_at(self.current.line, "syntax", message)

    def error_at(self, line: int, kind: str, message: str) -> None:
        self.errors.append(f"line {line}: {kind}: {message}")

    @staticmethod
    def token_name(lex_type: str) -> str:
        return TOKEN_DISPLAY.get(lex_type, lex_type)

    def parse(self) -> Program:
        program = self.parse_program()
        if not self.at("EOF"):
            self.error_at(self.current.line, "unexpected-token", f"unexpected token after program end: {self.current.display()}")
        return program

    def parse_program(self) -> Program:
        self.expect("PROGRAM", context="at start of program", recover_to={"ID", *DECL_START, "EOF"})
        name = self.expect("ID", context="as program name", recover_to=DECL_START | {"EOF"})
        type_decls, var_decls, proc_decls = self.parse_declare_part()
        body = self.parse_program_body()
        self.expect("DOT", context="after program body", recover_to={"EOF"})
        return Program(name.sem, name.line, type_decls, var_decls, proc_decls, body)

    def parse_declare_part(self) -> tuple[list[TypeDecl], list[VarDecl], list[ProcDecl]]:
        return self.parse_type_dec(), self.parse_var_dec(), self.parse_proc_dec()

    def parse_type_dec(self) -> list[TypeDecl]:
        decls: list[TypeDecl] = []
        if not self.at("TYPE"):
            return decls
        self.advance()
        if not self.at("ID"):
            self.error_at(self.current.line, "missing-declaration", "expected at least one type declaration after type")
        while not self.at("EOF", "VAR", "PROCEDURE", "BEGIN"):
            if not self.at("ID"):
                self.error_at(self.current.line, "unexpected-token", f"expected type identifier in type declaration, found {self.current.display()}")
                self.recover({"ID", "VAR", "PROCEDURE", "BEGIN", "EOF"})
                continue
            name = self.advance()
            self.expect("EQ", context=f"after type identifier '{name.sem}'", recover_to=TYPE_START | {"SEMI", "VAR", "PROCEDURE", "BEGIN", "EOF"})
            type_node = self.parse_type_name()
            self.expect("SEMI", context=f"after type declaration '{name.sem}'", recover_to={"ID", "VAR", "PROCEDURE", "BEGIN", "EOF"})
            decls.append(TypeDecl(name.sem, type_node, name.line))
        return decls

    def parse_type_name(self) -> TypeNode:
        token = self.current
        if self.at("INTEGER"):
            self.advance()
            return TypeNode("integer", token.line)
        if self.at("CHAR"):
            self.advance()
            return TypeNode("char", token.line)
        if self.at("ARRAY"):
            return self.parse_array_type()
        if self.at("RECORD"):
            return self.parse_record_type()
        if self.at("ID"):
            self.advance()
            return TypeNode("alias", token.line, name=token.sem)

        self.error_at(token.line, "missing-type", f"expected type name, found {token.display()}")
        self.recover(TYPE_START | {"SEMI", "RPAREN", "BEGIN", "PROCEDURE", "EOF"})
        return TypeNode("unknown", token.line)

    def parse_array_type(self) -> TypeNode:
        start = self.expect("ARRAY")
        self.expect("LMIDPAREN", context="after array", recover_to={"INTC", "UNDERANGE", "RMIDPAREN", "OF", "SEMI", "EOF"})
        low = self.expect("INTC", context="as array lower bound", recover_to={"UNDERANGE", "INTC", "RMIDPAREN", "OF", "SEMI", "EOF"})
        self.expect("UNDERANGE", context="between array bounds", recover_to={"INTC", "RMIDPAREN", "OF", "SEMI", "EOF"})
        high = self.expect("INTC", context="as array upper bound", recover_to={"RMIDPAREN", "OF", "SEMI", "EOF"})
        self.expect("RMIDPAREN", context="after array upper bound", recover_to={"OF", "SEMI", "EOF"})
        self.expect("OF", context="after array bounds", recover_to={"INTEGER", "CHAR", "SEMI", "EOF"})
        return TypeNode(
            "array",
            start.line,
            low=self.to_int(low),
            high=self.to_int(high),
            element=self.parse_base_type(),
        )

    def parse_base_type(self) -> TypeNode:
        token = self.current
        if self.at("INTEGER"):
            self.advance()
            return TypeNode("integer", token.line)
        if self.at("CHAR"):
            self.advance()
            return TypeNode("char", token.line)
        self.error_at(token.line, "missing-type", f"expected base type integer or char, found {token.display()}")
        return TypeNode("unknown", token.line)

    def parse_record_type(self) -> TypeNode:
        start = self.expect("RECORD")
        fields: list[FieldDecl] = []
        if self.at("END"):
            self.error_at(self.current.line, "missing-declaration", "record type must contain at least one field declaration")
        while not self.at("END", "EOF"):
            if self.current.lex not in FIELD_TYPE_START:
                self.error_at(self.current.line, "unexpected-token", f"expected record field declaration, found {self.current.display()}")
                self.recover(FIELD_TYPE_START | {"END", "EOF"})
                continue
            field_type = self.parse_array_type() if self.at("ARRAY") else self.parse_base_type()
            names = self.parse_id_list(context="as record field name", recover_to={"SEMI", "END", "EOF"})
            self.expect("SEMI", context="after record field declaration", recover_to=FIELD_TYPE_START | {"END", "EOF"})
            fields.append(FieldDecl(field_type, names))
        self.expect("END", context="to close record type", recover_to={"SEMI", "VAR", "PROCEDURE", "BEGIN", "EOF"})
        return TypeNode("record", start.line, fields=fields)

    def parse_id_list(self, *, context: str = "in identifier list", recover_to: set[str] | None = None) -> list[tuple[str, int]]:
        token = self.expect("ID", context=context, recover_to=(recover_to or {"SEMI", "EOF"}) | {"COMMA"})
        ids = [(token.sem, token.line)]
        while self.at("COMMA"):
            self.advance()
            token = self.expect("ID", context="after comma", recover_to=recover_to or {"SEMI", "EOF"})
            ids.append((token.sem, token.line))
        return ids

    def parse_var_dec(self) -> list[VarDecl]:
        decls: list[VarDecl] = []
        if not self.at("VAR"):
            return decls
        self.advance()
        if not self.starts_var_declaration():
            self.error_at(self.current.line, "missing-declaration", "expected at least one variable declaration after var")
        while not self.at("EOF", "PROCEDURE", "BEGIN"):
            if not self.starts_var_declaration():
                if self.current.lex in STMT_START:
                    break
                self.error_at(self.current.line, "unexpected-token", f"expected variable declaration, found {self.current.display()}")
                self.recover(TYPE_START | {"PROCEDURE", "BEGIN", "EOF"})
                continue
            type_node = self.parse_type_name()
            names = self.parse_id_list(context="as variable name", recover_to={"SEMI", "PROCEDURE", "BEGIN", "EOF"})
            self.expect("SEMI", context="after variable declaration", recover_to=TYPE_START | {"PROCEDURE", "BEGIN", "EOF"})
            decls.append(VarDecl(type_node, names))
        return decls

    def starts_var_declaration(self) -> bool:
        if self.at("INTEGER", "CHAR", "ARRAY", "RECORD"):
            return True
        return self.at("ID") and self.peek().lex == "ID"

    def parse_proc_dec(self) -> list[ProcDecl]:
        procedures: list[ProcDecl] = []
        while self.at("PROCEDURE"):
            procedures.append(self.parse_proc_declaration())
        return procedures

    def parse_proc_declaration(self) -> ProcDecl:
        self.expect("PROCEDURE")
        name = self.expect("ID", context="as procedure name", recover_to={"LPAREN", "SEMI", *DECL_START, "EOF"})
        self.expect("LPAREN", context=f"after procedure name '{name.sem}'", recover_to=TYPE_START | {"VAR", "RPAREN", "SEMI", "EOF"})
        params = self.parse_param_list()
        self.expect("RPAREN", context="after procedure parameter list", recover_to={"SEMI", *DECL_START, "EOF"})
        self.expect("SEMI", context="after procedure header", recover_to=DECL_START | {"EOF"})
        type_decls, var_decls, proc_decls = self.parse_declare_part()
        body = self.parse_program_body()
        return ProcDecl(name.sem, name.line, params, type_decls, var_decls, proc_decls, body)

    def parse_param_list(self) -> list[Param]:
        params: list[Param] = []
        if self.at("RPAREN"):
            return params
        if self.current.lex not in TYPE_START | {"VAR"}:
            self.error_at(self.current.line, "unexpected-token", f"expected parameter declaration, found {self.current.display()}")
            self.recover(TYPE_START | {"VAR", "RPAREN", "SEMI", "EOF"})
            if self.at("RPAREN", "EOF"):
                return params
        params.extend(self.parse_param())
        while self.at("SEMI"):
            self.advance()
            if self.at("RPAREN"):
                self.error_at(self.current.line, "unexpected-token", "trailing semicolon in parameter list")
                break
            params.extend(self.parse_param())
        return params

    def parse_param(self) -> list[Param]:
        mode: Literal["value", "var"] = "var" if self.at("VAR") else "value"
        if self.at("VAR"):
            self.advance()
        type_node = self.parse_type_name()
        return [
            Param(name, mode, type_node, line)
            for name, line in self.parse_id_list(context="as formal parameter name", recover_to={"SEMI", "RPAREN", "EOF"})
        ]

    def parse_program_body(self) -> list[Stmt]:
        self.expect("BEGIN", context="before statement list", recover_to=STMT_START | {"END", "DOT", "EOF"})
        body = self.parse_stm_list({"END", "DOT"})
        self.expect("END", context="to close program body", recover_to={"DOT", "SEMI", "EOF"})
        return body

    def parse_stm_list(self, terminators: set[str]) -> list[Stmt]:
        statements: list[Stmt] = []
        if self.current.lex in terminators:
            self.error_at(self.current.line, "missing-statement", f"expected statement before {self.current.display()}")
            return statements

        while not self.at("EOF") and self.current.lex not in terminators:
            if self.current.lex in STMT_START:
                statements.append(self.parse_stm())
                if self.at("SEMI"):
                    self.advance()
                    continue
                if self.current.lex in terminators:
                    break
                if self.current.lex in STMT_START:
                    self.error_at(self.current.line, "missing-token", "expected ; between statements")
                    continue
                self.error_at(self.current.line, "unexpected-token", f"expected ; or statement-list terminator, found {self.current.display()}")
                self.recover(STMT_START | terminators | {"SEMI"})
                if self.at("SEMI"):
                    self.advance()
                continue

            self.error_at(self.current.line, "unexpected-token", f"expected statement, found {self.current.display()}")
            self.recover(STMT_START | terminators | {"SEMI"})
            if self.at("SEMI"):
                self.advance()

        return statements

    def parse_stm(self) -> Stmt:
        if self.at("IF"):
            return self.parse_conditional_stm()
        if self.at("WHILE"):
            return self.parse_loop_stm()
        if self.at("READ"):
            return self.parse_input_stm()
        if self.at("WRITE"):
            return self.parse_output_stm()
        if self.at("RETURN"):
            return self.parse_return_stm()
        if self.at("ID"):
            name = self.advance()
            if self.at("LPAREN"):
                return self.parse_call_stm_rest(name)
            return self.parse_assignment_rest(name)

        token = self.advance()
        self.error_at(token.line, "unexpected-token", f"expected statement, found {token.display()}")
        return ReturnStmt(token.line, ConstExpr(token.line, value=0))

    def parse_assignment_rest(self, name: Token) -> AssignStmt:
        target = self.finish_variable(name)
        self.expect("ASSIGN", context=f"in assignment to '{name.sem}'", recover_to=EXPR_START | EXPR_FOLLOW)
        return AssignStmt(name.line, target, self.parse_exp())

    def parse_call_stm_rest(self, name: Token) -> CallStmt:
        self.expect("LPAREN", context=f"after procedure name '{name.sem}'", recover_to=EXPR_START | {"RPAREN", "SEMI", "EOF"})
        args = [] if self.at("RPAREN") else self.parse_act_param_list()
        self.expect("RPAREN", context=f"after arguments for '{name.sem}'", recover_to={"SEMI", "END", "ELSE", "FI", "ENDWH", "EOF"})
        return CallStmt(name.line, name.sem, args)

    def parse_conditional_stm(self) -> IfStmt:
        token = self.expect("IF")
        condition = self.parse_rel_exp()
        self.expect("THEN", context="after if condition", recover_to=STMT_START | {"ELSE", "FI", "EOF"})
        then_body = self.parse_stm_list({"ELSE", "FI"})
        if self.at("ELSE"):
            self.advance()
            else_body = self.parse_stm_list({"FI"})
        else:
            self.error_at(self.current.line, "missing-token", "expected else before fi")
            else_body = []
        self.expect("FI", context="to close if statement", recover_to={"SEMI", "END", "ENDWH", "EOF"})
        return IfStmt(token.line, condition, then_body, else_body)

    def parse_loop_stm(self) -> WhileStmt:
        token = self.expect("WHILE")
        condition = self.parse_rel_exp()
        self.expect("DO", context="after while condition", recover_to=STMT_START | {"ENDWH", "END", "EOF"})
        body = self.parse_stm_list({"ENDWH", "END"})
        self.expect("ENDWH", context="to close while statement", recover_to={"SEMI", "END", "EOF"})
        return WhileStmt(token.line, condition, body)

    def parse_input_stm(self) -> ReadStmt:
        token = self.expect("READ")
        self.expect("LPAREN", context="after read", recover_to={"ID", "RPAREN", "SEMI", "EOF"})
        name = self.expect("ID", context="as read target", recover_to={"RPAREN", "SEMI", "EOF"})
        self.expect("RPAREN", context="after read target", recover_to={"SEMI", "END", "ELSE", "FI", "ENDWH", "EOF"})
        return ReadStmt(token.line, VarRef(name.sem, name.line))

    def parse_output_stm(self) -> WriteStmt:
        token = self.expect("WRITE")
        self.expect("LPAREN", context="after write", recover_to=EXPR_START | {"RPAREN", "SEMI", "EOF"})
        expr = self.parse_exp()
        self.expect("RPAREN", context="after write expression", recover_to={"SEMI", "END", "ELSE", "FI", "ENDWH", "EOF"})
        return WriteStmt(token.line, expr)

    def parse_return_stm(self) -> ReturnStmt:
        token = self.expect("RETURN")
        self.expect("LPAREN", context="after return", recover_to=EXPR_START | {"RPAREN", "SEMI", "EOF"})
        expr = self.parse_exp()
        self.expect("RPAREN", context="after return expression", recover_to={"SEMI", "END", "ELSE", "FI", "ENDWH", "EOF"})
        return ReturnStmt(token.line, expr)

    def parse_act_param_list(self) -> list[Expr]:
        params = [self.parse_exp()]
        while self.at("COMMA"):
            self.advance()
            if self.at("RPAREN"):
                self.error_at(self.current.line, "missing-expression", "expected actual parameter after comma")
                break
            params.append(self.parse_exp())
        return params

    def parse_rel_exp(self) -> Expr:
        left = self.parse_exp()
        if self.current.lex in CMP_OPS:
            op = self.advance()
            right = self.parse_exp()
            return BinaryExpr(op.line, op=CMP_OPS[op.lex], left=left, right=right)
        self.error_at(self.current.line, "missing-operator", f"expected comparison operator < or =, found {self.current.display()}")
        return left

    def parse_exp(self) -> Expr:
        left = self.parse_term()
        if self.current.lex in ADD_OPS:
            op = self.advance()
            if self.current.lex in EXPR_FOLLOW:
                self.error_at(self.current.line, "missing-expression", f"expected expression after '{ADD_OPS[op.lex]}'")
                return BinaryExpr(op.line, op=ADD_OPS[op.lex], left=left, right=ConstExpr(self.current.line, value=0))
            right = self.parse_exp()
            return BinaryExpr(op.line, op=ADD_OPS[op.lex], left=left, right=right)
        return left

    def parse_term(self) -> Expr:
        left = self.parse_factor()
        if self.current.lex in MULT_OPS:
            op = self.advance()
            if self.current.lex in EXPR_FOLLOW:
                self.error_at(self.current.line, "missing-expression", f"expected expression after '{MULT_OPS[op.lex]}'")
                return BinaryExpr(op.line, op=MULT_OPS[op.lex], left=left, right=ConstExpr(self.current.line, value=1))
            right = self.parse_term()
            return BinaryExpr(op.line, op=MULT_OPS[op.lex], left=left, right=right)
        return left

    def parse_factor(self) -> Expr:
        if self.at("LPAREN"):
            self.advance()
            expr = self.parse_exp()
            self.expect("RPAREN", context="after parenthesized expression", recover_to=EXPR_FOLLOW)
            return expr
        if self.at("INTC"):
            token = self.advance()
            value = self.to_int(token) or 0
            return ConstExpr(token.line, const_int=value, value=value)
        if self.at("CHARC"):
            token = self.advance()
            return CharExpr(token.line, value=token.sem)
        if self.at("ID"):
            token = self.advance()
            return VarExpr(token.line, ref=self.finish_variable(token))

        if self.current.lex in EXPR_FOLLOW:
            self.error_at(self.current.line, "missing-expression", f"expected expression before {self.current.display()}")
            return ConstExpr(self.current.line, value=0)

        token = self.advance()
        self.error_at(token.line, "unexpected-token", f"expected expression factor, found {token.display()}")
        self.recover(EXPR_FOLLOW | set(ADD_OPS) | set(MULT_OPS))
        return ConstExpr(token.line, value=0)

    def finish_variable(self, name: Token) -> VarRef:
        ref = VarRef(name.sem, name.line)
        if self.at("LMIDPAREN"):
            self.advance()
            ref.selectors.append(IndexSelector(self.parse_exp()))
            self.expect("RMIDPAREN", context=f"after index of '{name.sem}'", recover_to=EXPR_FOLLOW | {"ASSIGN"})
        elif self.at("DOT"):
            self.advance()
            field = self.expect("ID", context=f"as field name after '{name.sem}.'", recover_to={"LMIDPAREN", "ASSIGN", *EXPR_FOLLOW})
            selector = FieldSelector(field.sem, field.line)
            if self.at("LMIDPAREN"):
                self.advance()
                selector.index = IndexSelector(self.parse_exp())
                self.expect("RMIDPAREN", context=f"after index of field '{field.sem}'", recover_to=EXPR_FOLLOW | {"ASSIGN"})
            ref.selectors.append(selector)
        return ref

    def recover(self, stop_set: set[str]) -> None:
        while not self.at("EOF") and self.current.lex not in stop_set:
            self.advance()

    @staticmethod
    def to_int(token: Token) -> int | None:
        try:
            return int(token.sem)
        except (TypeError, ValueError):
            return None


def format_ast(program: Program) -> str:
    lines = [f"Program {program.name}"]
    lines.append(f"  types={len(program.type_decls)} vars={len(program.var_decls)} procs={len(program.proc_decls)}")
    lines.append(f"  statements={len(program.body)}")
    return "\n".join(lines)


def format_parse_result(errors: list[str], program: Program) -> str:
    lines = ["Syntax Errors"]
    lines.extend(errors or ["No syntax errors."])
    lines.append("")
    lines.append("AST Summary")
    lines.append(format_ast(program))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Parse an SNL token sequence and print an AST summary.")
    parser.add_argument("tokens", type=Path, help="token file generated by snl_lexer.py")
    args = parser.parse_args(argv)

    try:
        tokens = load_tokens(args.tokens)
        snl_parser = SNLParser(tokens)
        program = snl_parser.parse()
        print(format_parse_result(snl_parser.errors, program))
        return 1 if snl_parser.errors else 0
    except (OSError, ParserError, json.JSONDecodeError) as exc:
        print(f"snl_parser.py: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

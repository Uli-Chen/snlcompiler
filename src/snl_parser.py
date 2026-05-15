#!/usr/bin/env python3
"""SNL 递归下降语法分析器。

本模块是编译器唯一的语法入口：将 Token 流转换为带类型的 AST。
后续的语义分析和代码生成均遍历此 AST，不再重新解析 Token。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


# ─── FIRST 集合定义 ───
TYPE_START = {"INTEGER", "CHAR", "ARRAY", "RECORD", "ID"}  # 类型声明的起始 Token
FIELD_TYPE_START = {"INTEGER", "CHAR", "ARRAY"}             # record 字段允许的类型
STMT_START = {"IF", "WHILE", "READ", "WRITE", "RETURN", "ID"}  # 语句的起始 Token
ADD_OPS = {"PLUS": "+", "MINUS": "-"}   # 加法级运算符
MULT_OPS = {"TIMES": "*", "OVER": "/"}  # 乘法级运算符
CMP_OPS = {"LT": "<", "EQ": "="}       # 关系运算符


@dataclass(frozen=True)
class Token:
    line: int
    lex: str
    sem: str = ""

    def display(self) -> str:
        return f"{self.lex}({self.sem})" if self.sem else self.lex


@dataclass
class TypeNode:
    """类型 AST 节点。kind 取值：integer/char/array/record/alias/unknown"""
    kind: str
    line: int
    name: str = ""                              # alias 类型引用的名称
    low: int | None = None                      # array 下界
    high: int | None = None                     # array 上界
    element: "TypeNode | None" = None           # array 元素类型
    fields: list["FieldDecl"] = field(default_factory=list)  # record 字段列表
    type_info: object | None = None             # 语义分析阶段填充的 TypeInfo


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
    """变量引用，包含名称和选择器链（数组下标、记录字段）。"""
    name: str
    line: int
    selectors: list[Selector] = field(default_factory=list)
    symbol: object | None = None     # 语义分析后绑定的 Symbol
    type_info: object | None = None  # 经过选择器解析后的最终类型
    assignable: bool = False         # 是否可作为赋值目标


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
    """LL(1) 递归下降解析器。

    通过 FIRST/FOLLOW 集合预测产生式，遇到错误时进行恢复而非立即终止。
    """

    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.index = 0
        self.errors: list[str] = []

    @property
    def current(self) -> Token:
        return self.tokens[self.index] if self.index < len(self.tokens) else self.tokens[-1]

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
        token = self.current
        self.error(f"expected {lex_type}, found {token.display()}")
        return Token(token.line, lex_type)

    def error(self, message: str) -> None:
        self.errors.append(f"line {self.current.line}: {message}")

    def parse(self) -> Program:
        program = self.parse_program()
        if not self.at("EOF"):
            self.error(f"unexpected token after program end: {self.current.display()}")
        return program

    def parse_program(self) -> Program:
        self.expect("PROGRAM")
        name = self.expect("ID")
        type_decls, var_decls, proc_decls = self.parse_declare_part()
        body = self.parse_program_body()
        self.expect("DOT")
        return Program(name.sem, name.line, type_decls, var_decls, proc_decls, body)

    def parse_declare_part(self) -> tuple[list[TypeDecl], list[VarDecl], list[ProcDecl]]:
        return self.parse_type_dec(), self.parse_var_dec(), self.parse_proc_dec()

    def parse_type_dec(self) -> list[TypeDecl]:
        decls: list[TypeDecl] = []
        if not self.at("TYPE"):
            return decls
        self.advance()
        while self.at("ID"):
            name = self.advance()
            self.expect("EQ")
            type_node = self.parse_type_name()
            self.expect("SEMI")
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

        self.error(f"expected type name, found {token.display()}")
        self.recover(TYPE_START | {"SEMI", "RPAREN", "BEGIN", "PROCEDURE", "EOF"})
        return TypeNode("unknown", token.line)

    def parse_array_type(self) -> TypeNode:
        start = self.expect("ARRAY")
        self.expect("LMIDPAREN")
        low = self.expect("INTC")
        self.expect("UNDERANGE")
        high = self.expect("INTC")
        self.expect("RMIDPAREN")
        self.expect("OF")
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
        self.error(f"expected base type INTEGER or CHAR, found {token.display()}")
        return TypeNode("unknown", token.line)

    def parse_record_type(self) -> TypeNode:
        start = self.expect("RECORD")
        fields: list[FieldDecl] = []
        while self.current.lex in FIELD_TYPE_START:
            field_type = self.parse_array_type() if self.at("ARRAY") else self.parse_base_type()
            names = self.parse_id_list()
            self.expect("SEMI")
            fields.append(FieldDecl(field_type, names))
        self.expect("END")
        return TypeNode("record", start.line, fields=fields)

    def parse_id_list(self) -> list[tuple[str, int]]:
        token = self.expect("ID")
        ids = [(token.sem, token.line)]
        while self.at("COMMA"):
            self.advance()
            token = self.expect("ID")
            ids.append((token.sem, token.line))
        return ids

    def parse_var_dec(self) -> list[VarDecl]:
        decls: list[VarDecl] = []
        if not self.at("VAR"):
            return decls
        self.advance()
        while self.current.lex in TYPE_START:
            type_node = self.parse_type_name()
            names = self.parse_id_list()
            self.expect("SEMI")
            decls.append(VarDecl(type_node, names))
        return decls

    def parse_proc_dec(self) -> list[ProcDecl]:
        procedures: list[ProcDecl] = []
        while self.at("PROCEDURE"):
            procedures.append(self.parse_proc_declaration())
        return procedures

    def parse_proc_declaration(self) -> ProcDecl:
        self.expect("PROCEDURE")
        name = self.expect("ID")
        self.expect("LPAREN")
        params = self.parse_param_list()
        self.expect("RPAREN")
        self.expect("SEMI")
        type_decls, var_decls, proc_decls = self.parse_declare_part()
        body = self.parse_program_body()
        return ProcDecl(name.sem, name.line, params, type_decls, var_decls, proc_decls, body)

    def parse_param_list(self) -> list[Param]:
        params: list[Param] = []
        if self.at("RPAREN"):
            return params
        params.extend(self.parse_param())
        while self.at("SEMI"):
            self.advance()
            params.extend(self.parse_param())
        return params

    def parse_param(self) -> list[Param]:
        mode: Literal["value", "var"] = "var" if self.at("VAR") else "value"
        if self.at("VAR"):
            self.advance()
        type_node = self.parse_type_name()
        return [Param(name, mode, type_node, line) for name, line in self.parse_id_list()]

    def parse_program_body(self) -> list[Stmt]:
        self.expect("BEGIN")
        body = self.parse_stm_list({"END"})
        self.expect("END")
        return body

    def parse_stm_list(self, terminators: set[str]) -> list[Stmt]:
        statements: list[Stmt] = []
        if self.current.lex in terminators:
            self.error("expected statement before statement-list terminator")
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
                    self.error("missing SEMI between statements")
                    continue
                self.error(f"expected SEMI or terminator, found {self.current.display()}")
                self.recover(STMT_START | terminators | {"SEMI"})
                if self.at("SEMI"):
                    self.advance()
                continue

            self.error(f"expected statement, found {self.current.display()}")
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
        self.error(f"expected statement, found {token.display()}")
        return ReturnStmt(token.line, ConstExpr(token.line, value=0))

    def parse_assignment_rest(self, name: Token) -> AssignStmt:
        target = self.finish_variable(name)
        self.expect("ASSIGN")
        return AssignStmt(name.line, target, self.parse_exp())

    def parse_call_stm_rest(self, name: Token) -> CallStmt:
        self.expect("LPAREN")
        args = [] if self.at("RPAREN") else self.parse_act_param_list()
        self.expect("RPAREN")
        return CallStmt(name.line, name.sem, args)

    def parse_conditional_stm(self) -> IfStmt:
        token = self.expect("IF")
        condition = self.parse_rel_exp()
        self.expect("THEN")
        then_body = self.parse_stm_list({"ELSE"})
        self.expect("ELSE")
        else_body = self.parse_stm_list({"FI"})
        self.expect("FI")
        return IfStmt(token.line, condition, then_body, else_body)

    def parse_loop_stm(self) -> WhileStmt:
        token = self.expect("WHILE")
        condition = self.parse_rel_exp()
        self.expect("DO")
        body = self.parse_stm_list({"ENDWH"})
        self.expect("ENDWH")
        return WhileStmt(token.line, condition, body)

    def parse_input_stm(self) -> ReadStmt:
        token = self.expect("READ")
        self.expect("LPAREN")
        name = self.expect("ID")
        self.expect("RPAREN")
        return ReadStmt(token.line, VarRef(name.sem, name.line))

    def parse_output_stm(self) -> WriteStmt:
        token = self.expect("WRITE")
        self.expect("LPAREN")
        expr = self.parse_exp()
        self.expect("RPAREN")
        return WriteStmt(token.line, expr)

    def parse_return_stm(self) -> ReturnStmt:
        token = self.expect("RETURN")
        self.expect("LPAREN")
        expr = self.parse_exp()
        self.expect("RPAREN")
        return ReturnStmt(token.line, expr)

    def parse_act_param_list(self) -> list[Expr]:
        params = [self.parse_exp()]
        while self.at("COMMA"):
            self.advance()
            params.append(self.parse_exp())
        return params

    def parse_rel_exp(self) -> Expr:
        left = self.parse_exp()
        if self.current.lex in CMP_OPS:
            op = self.advance()
            right = self.parse_exp()
            return BinaryExpr(op.line, op=CMP_OPS[op.lex], left=left, right=right)
        self.error(f"expected comparison operator, found {self.current.display()}")
        return left

    def parse_exp(self) -> Expr:
        left = self.parse_term()
        # 加减法按常见语言规则处理为左结合：
        # 2 - 1 - 1 应解析为 (2 - 1) - 1，而不是 2 - (1 - 1)。
        while self.current.lex in ADD_OPS:
            op = self.advance()
            right = self.parse_term()
            left = BinaryExpr(op.line, op=ADD_OPS[op.lex], left=left, right=right)
        return left

    def parse_term(self) -> Expr:
        left = self.parse_factor()
        # 乘除法同样左结合，避免 20 / 5 / 2 被解析成 20 / (5 / 2)。
        while self.current.lex in MULT_OPS:
            op = self.advance()
            right = self.parse_factor()
            left = BinaryExpr(op.line, op=MULT_OPS[op.lex], left=left, right=right)
        return left

    def parse_factor(self) -> Expr:
        if self.at("LPAREN"):
            self.advance()
            expr = self.parse_exp()
            self.expect("RPAREN")
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

        token = self.advance()
        self.error(f"expected expression factor, found {token.display()}")
        return ConstExpr(token.line, value=0)

    def finish_variable(self, name: Token) -> VarRef:
        """解析变量引用的选择器部分。

        限制：当前只支持一层选择器（arr[i] 或 rec.field 或 rec.field[i]），
        不支持多层链式访问如 arr[i].field.sub[j]。
        """
        ref = VarRef(name.sem, name.line)
        if self.at("LMIDPAREN"):
            self.advance()
            ref.selectors.append(IndexSelector(self.parse_exp()))
            self.expect("RMIDPAREN")
        elif self.at("DOT"):
            self.advance()
            field = self.expect("ID")
            selector = FieldSelector(field.sem, field.line)
            if self.at("LMIDPAREN"):
                self.advance()
                selector.index = IndexSelector(self.parse_exp())
                self.expect("RMIDPAREN")
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

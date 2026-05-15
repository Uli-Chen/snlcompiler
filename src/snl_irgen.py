#!/usr/bin/env python3
"""从语义检查后的 SNL AST 生成四元式 IR。

主要职责：
  - 遍历 AST，为每条语句和表达式生成对应的四元式序列
  - 通过 UnitBuilder 管理临时变量（t0、t1…）和标签（L0、L1…）的分配
  - 左值（lvalue）统一拆成 address-producing 四元式（addr/index_addr/field_addr），
    后端只需理解地址临时量，无需感知变量的存储位置
  - 过程调用通过 param + call 四元式序列表达，参数顺序与声明顺序一致
"""

from __future__ import annotations

from snl_ir import IRProcedure, IRProgram, IRUnit, Operand, Quad
from snl_parser import (
    AssignStmt,
    BinaryExpr,
    CallStmt,
    CharExpr,
    ConstExpr,
    Expr,
    FieldSelector,
    IfStmt,
    IndexSelector,
    ProcDecl,
    Program,
    ReadStmt,
    ReturnStmt,
    Stmt,
    VarDecl,
    VarExpr,
    VarRef,
    WhileStmt,
    WriteStmt,
)
from snl_semantic import BOOL, CHAR, INTEGER, UNKNOWN, Symbol, TypeInfo


class IRGenError(RuntimeError):
    pass


class UnitBuilder:
    def __init__(self, unit: IRUnit) -> None:
        self.unit = unit
        self.temp_counter = 0
        self.label_counter = 0

    def temp(self, type_info: TypeInfo = UNKNOWN) -> str:
        name = f"t{self.temp_counter}"
        self.temp_counter += 1
        self.unit.temp_types[name] = type_info
        return name

    def label(self, prefix: str = "L") -> str:
        name = f"{prefix}{self.label_counter}"
        self.label_counter += 1
        return name

    def emit(self, op: str, arg1: Operand = None, arg2: Operand = None, result: Operand = None, **kwargs: object) -> Quad:
        quad = Quad(op, arg1, arg2, result, **kwargs)
        self.unit.quads.append(quad)
        return quad


class SNLIRGenerator:
    def __init__(self, program: Program) -> None:
        self.ast = program

    def generate(self) -> IRProgram:
        ir = IRProgram()
        ir.globals = collect_decl_symbols(self.ast.var_decls)
        ir.procedures = [self.emit_procedure(proc) for proc in self.ast.proc_decls]
        self.emit_statements(self.ast.body, UnitBuilder(ir.main))
        return ir

    def emit_procedure(self, proc: ProcDecl) -> IRProcedure:
        symbol = require_symbol(proc.symbol, proc.name)
        unit = IRProcedure(
            name=proc.name,
            symbol=symbol,
            params=symbol.param_symbols,
            locals=collect_decl_symbols(proc.var_decls),
            end_label=f"{proc.name}_return",
            scope_level=symbol.scope_level + 1,  # procedure body is one level deeper than where it's declared
        )
        unit.children = [self.emit_procedure(child) for child in proc.proc_decls]
        builder = UnitBuilder(unit)
        self.emit_statements(proc.body, builder)
        builder.emit("label", result=unit.end_label)
        return unit

    def emit_statements(self, statements: list[Stmt], builder: UnitBuilder) -> None:
        for stmt in statements:
            if isinstance(stmt, AssignStmt):
                value = self.emit_expr(stmt.expr, builder)
                address = self.emit_lvalue(stmt.target, builder)
                builder.emit("store", value, None, address, type_info=stmt.target.type_info)
            elif isinstance(stmt, CallStmt):
                self.emit_call(stmt, builder)
            elif isinstance(stmt, IfStmt):
                self.emit_if(stmt, builder)
            elif isinstance(stmt, WhileStmt):
                self.emit_while(stmt, builder)
            elif isinstance(stmt, ReadStmt):
                address = self.emit_lvalue(stmt.target, builder)
                builder.emit("read", None, None, address, type_info=stmt.target.type_info)
            elif isinstance(stmt, WriteStmt):
                value = self.emit_expr(stmt.expr, builder)
                builder.emit("write", value, None, None, type_info=stmt.expr.type_info)
            elif isinstance(stmt, ReturnStmt):
                value = self.emit_expr(stmt.expr, builder)
                builder.emit("return", value)

    def emit_if(self, stmt: IfStmt, builder: UnitBuilder) -> None:
        else_label = builder.label("Lelse")
        end_label = builder.label("Lendif")
        self.emit_false_branch(stmt.condition, else_label, builder)
        self.emit_statements(stmt.then_body, builder)
        builder.emit("goto", result=end_label)
        builder.emit("label", result=else_label)
        self.emit_statements(stmt.else_body, builder)
        builder.emit("label", result=end_label)

    def emit_while(self, stmt: WhileStmt, builder: UnitBuilder) -> None:
        start_label = builder.label("Lwhile")
        end_label = builder.label("Lendwhile")
        builder.emit("label", result=start_label)
        self.emit_false_branch(stmt.condition, end_label, builder)
        self.emit_statements(stmt.body, builder)
        builder.emit("goto", result=start_label)
        builder.emit("label", result=end_label)

    def emit_false_branch(self, condition: Expr, false_label: str, builder: UnitBuilder) -> None:
        if not isinstance(condition, BinaryExpr) or condition.op not in {"<", "="}:
            raise IRGenError(f"line {condition.line}: expected relational condition after semantic analysis")
        left = self.emit_expr(require_expr(condition.left), builder)
        right = self.emit_expr(require_expr(condition.right), builder)
        builder.emit(f"if_false_{condition.op}", left, right, false_label, type_info=BOOL)

    def emit_call(self, stmt: CallStmt, builder: UnitBuilder) -> None:
        symbol = require_symbol(stmt.symbol, stmt.name)
        for formal, arg in zip(symbol.params, stmt.args):
            if formal.mode == "var":
                if not isinstance(arg, VarExpr) or arg.ref is None:
                    raise IRGenError(f"line {stmt.line}: var argument was not a variable after semantic analysis")
                operand = self.emit_lvalue(arg.ref, builder)
            else:
                operand = self.emit_expr(arg, builder)
            builder.emit("param", operand, formal.mode, None, type_info=formal.type_info)
        builder.emit("call", symbol, len(stmt.args), None, symbol=symbol)

    def emit_expr(self, expr: Expr, builder: UnitBuilder) -> Operand:
        if isinstance(expr, ConstExpr):
            return expr.value
        if isinstance(expr, CharExpr):
            return ord(expr.value[0]) if expr.value else 0
        if isinstance(expr, VarExpr) and expr.ref is not None:
            address = self.emit_lvalue(expr.ref, builder)
            result = builder.temp(expr.type_info or UNKNOWN)
            builder.emit("load", address, None, result, type_info=expr.type_info)
            return result
        if isinstance(expr, BinaryExpr):
            if expr.op in {"<", "="}:
                raise IRGenError(f"line {expr.line}: relational expression cannot be used as scalar")
            left = self.emit_expr(require_expr(expr.left), builder)
            right = self.emit_expr(require_expr(expr.right), builder)
            result = builder.temp(expr.type_info or INTEGER)
            builder.emit(expr.op, left, right, result, type_info=expr.type_info)
            return result
        raise IRGenError(f"line {expr.line}: unsupported expression in IR generation")

    def emit_lvalue(self, ref: VarRef, builder: UnitBuilder) -> Operand:
        symbol = require_symbol(ref.symbol, ref.name)
        current_type = symbol.type_info
        address = builder.temp(UNKNOWN)
        builder.emit("addr", None, None, address, type_info=current_type, symbol=symbol, note=f"&{symbol.name}")

        # 左值寻址统一拆成 address-producing 四元式，后端只需要理解地址临时量。
        for selector in ref.selectors:
            if isinstance(selector, IndexSelector):
                index = self.emit_expr(selector.expr, builder)
                next_address = builder.temp(UNKNOWN)
                builder.emit("index_addr", address, index, next_address, type_info=current_type)
                address = next_address
                current_type = current_type.element or UNKNOWN
            elif isinstance(selector, FieldSelector):
                next_address = builder.temp(UNKNOWN)
                builder.emit("field_addr", address, selector.name, next_address, type_info=current_type)
                address = next_address
                current_type = current_type.fields[selector.name]
                if selector.index is not None:
                    index = self.emit_expr(selector.index.expr, builder)
                    indexed_address = builder.temp(UNKNOWN)
                    builder.emit("index_addr", address, index, indexed_address, type_info=current_type)
                    address = indexed_address
                    current_type = current_type.element or UNKNOWN
        return address


def collect_decl_symbols(declarations: list[VarDecl]) -> list[Symbol]:
    symbols: list[Symbol] = []
    for decl in declarations:
        symbols.extend(symbol for symbol in decl.symbols if isinstance(symbol, Symbol))
    return symbols


def require_symbol(value: object | None, name: str) -> Symbol:
    if not isinstance(value, Symbol):
        raise IRGenError(f"internal error: symbol {name!r} missing after semantic analysis")
    return value


def require_expr(expr: Expr | None) -> Expr:
    if expr is None:
        raise IRGenError("internal error: missing expression")
    return expr

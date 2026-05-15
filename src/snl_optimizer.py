#!/usr/bin/env python3
"""SNL IR 级优化。

当前实现两种基本块内优化：
1. 常量折叠：编译期计算常量算术表达式和条件跳转
2. 公共子表达式消除（CSE）：复用已计算的相同表达式结果
"""

from __future__ import annotations

try:
    from playground.snlcompiler.src.snl_ir import IRProcedure, IRProgram, IRUnit, Operand, Quad, is_temp
except ModuleNotFoundError:
    from snl_ir import IRProcedure, IRProgram, IRUnit, Operand, Quad, is_temp


PURE_EXPR_OPS = {"+", "-", "*", "/", "addr", "index_addr", "field_addr", "load"}  # 无副作用的表达式操作
COMMUTATIVE_OPS = {"+", "*"}  # 交换律成立的操作（CSE 时规范化操作数顺序）
BLOCK_END_OPS = {"goto", "return", "call", "read"}  # 基本块终结操作


def optimize_program(program: IRProgram) -> IRProgram:
    for proc in walk_procedures(program.procedures):
        optimize_unit(proc)
    optimize_unit(program.main)
    return program


def walk_procedures(procedures: list[IRProcedure]) -> list[IRProcedure]:
    result: list[IRProcedure] = []
    for proc in procedures:
        result.extend(walk_procedures(proc.children))
        result.append(proc)
    return result


def optimize_unit(unit: IRUnit) -> None:
    unit.quads = fold_constants(unit.quads)
    unit.quads = eliminate_common_subexpressions(unit.quads)


def fold_constants(quads: list[Quad]) -> list[Quad]:
    """常量折叠优化。

    维护一个 constants 字典追踪已知为常量的临时变量。
    当算术操作的两个操作数都是常量时，直接计算结果替换为赋值。
    当条件跳转的两个操作数都是常量时，直接决定是否跳转。
    """
    constants: dict[str, int] = {}
    optimized: list[Quad] = []

    for quad in quads:
        quad = replace_known_constants(quad, constants)

        if quad.op in {"+", "-", "*", "/"} and isinstance(quad.arg1, int) and isinstance(quad.arg2, int):
            folded = eval_arithmetic(quad.op, quad.arg1, quad.arg2)
            if folded is not None and isinstance(quad.result, str):
                constants[quad.result] = folded
                optimized.append(Quad("assign", folded, None, quad.result, type_info=quad.type_info, note="常量折叠"))
                continue

        if quad.op in {"if_false_<", "if_false_="} and isinstance(quad.arg1, int) and isinstance(quad.arg2, int):
            condition_true = quad.arg1 < quad.arg2 if quad.op == "if_false_<" else quad.arg1 == quad.arg2
            if condition_true:
                continue
            optimized.append(Quad("goto", None, None, quad.result, note="常量条件折叠"))
            continue

        if quad.op == "assign" and isinstance(quad.result, str):
            if isinstance(quad.arg1, int):
                constants[quad.result] = quad.arg1
            else:
                constants.pop(quad.result, None)
        elif isinstance(quad.result, str):
            constants.pop(quad.result, None)

        if quad.op in {"store", "read", "call"}:
            constants.clear()
        optimized.append(quad)

    return optimized


def replace_known_constants(quad: Quad, constants: dict[str, int]) -> Quad:
    arg1 = constants.get(quad.arg1, quad.arg1) if isinstance(quad.arg1, str) else quad.arg1
    arg2 = constants.get(quad.arg2, quad.arg2) if isinstance(quad.arg2, str) else quad.arg2
    return Quad(quad.op, arg1, arg2, quad.result, quad.type_info, quad.symbol, quad.note)


def eval_arithmetic(op: str, left: int, right: int) -> int | None:
    if op == "+":
        return left + right
    if op == "-":
        return left - right
    if op == "*":
        return left * right
    if op == "/" and right != 0:
        return int(left / right)
    return None


def eliminate_common_subexpressions(quads: list[Quad]) -> list[Quad]:
    """公共子表达式消除。

    在基本块内追踪已计算的表达式。当遇到相同表达式时，
    用赋值替代重复计算。遇到 store/read/call 时清除表达式缓存
    （因为内存状态可能改变）。
    """
    aliases: dict[str, Operand] = {}
    expressions: dict[tuple, str] = {}
    optimized: list[Quad] = []

    for quad in quads:
        if starts_new_block(quad):
            aliases.clear()
            expressions.clear()

        normalized = normalize_quad(quad, aliases)
        if normalized.op in PURE_EXPR_OPS and isinstance(normalized.result, str):
            key = expression_key(normalized, aliases)
            if key in expressions:
                aliases[normalized.result] = expressions[key]
                optimized.append(
                    Quad(
                        "assign",
                        expressions[key],
                        None,
                        normalized.result,
                        type_info=normalized.type_info,
                        note="公共子表达式消除",
                    )
                )
                continue
            expressions[key] = normalized.result
        elif normalized.op == "assign" and isinstance(normalized.result, str):
            aliases[normalized.result] = canonical_operand(normalized.arg1, aliases)

        optimized.append(normalized)

        # store/read/call 可能改变内存或外部状态，后续 load 和表达式不能复用旧结果。
        if normalized.op in {"store", "read", "call"}:
            expressions.clear()

        if ends_block(normalized):
            aliases.clear()
            expressions.clear()

    return optimized


def starts_new_block(quad: Quad) -> bool:
    return quad.op == "label"


def ends_block(quad: Quad) -> bool:
    return quad.op in BLOCK_END_OPS or quad.op.startswith("if_false_")


def normalize_quad(quad: Quad, aliases: dict[str, Operand]) -> Quad:
    arg1 = canonical_operand(quad.arg1, aliases)
    arg2 = canonical_operand(quad.arg2, aliases)
    return Quad(quad.op, arg1, arg2, quad.result, quad.type_info, quad.symbol, quad.note)


def expression_key(quad: Quad, aliases: dict[str, Operand]) -> tuple:
    arg1 = operand_key(quad.arg1, aliases)
    arg2 = operand_key(quad.arg2, aliases)
    if quad.op in COMMUTATIVE_OPS and repr(arg2) < repr(arg1):
        arg1, arg2 = arg2, arg1
    if quad.op == "addr":
        return (quad.op, getattr(quad.symbol, "name", None))
    return (quad.op, arg1, arg2, type_key(quad.type_info))


def canonical_operand(value: Operand, aliases: dict[str, Operand]) -> Operand:
    seen: set[str] = set()
    while is_temp(value) and value in aliases and value not in seen:
        seen.add(value)
        value = aliases[value]
    return value


def operand_key(value: Operand, aliases: dict[str, Operand]) -> tuple:
    value = canonical_operand(value, aliases)
    if isinstance(value, int):
        return ("const", value)
    if isinstance(value, str):
        return ("temp", value)
    name = getattr(value, "name", None)
    if name:
        return ("symbol", name)
    return ("other", repr(value))


def type_key(type_info: object) -> str:
    display = getattr(type_info, "display", None)
    return display() if callable(display) else repr(type_info)

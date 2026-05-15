#!/usr/bin/env python3
"""SNL 四元式中间表示（IR）数据结构。

四元式格式：(操作符, 操作数1, 操作数2, 结果)
操作数可以是：整数常量、字符串（临时变量名/标签名）、Symbol 对象、None
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# 操作数类型：整数字面量 | 临时变量/标签名 | Symbol 对象 | 空
Operand = int | str | Any | None


@dataclass
class Quad:
    """单条四元式指令。"""
    op: str              # 操作符：+, -, *, /, load, store, addr, goto, label, call 等
    arg1: Operand = None
    arg2: Operand = None
    result: Operand = None
    type_info: Any = None  # 关联的类型信息（用于代码生成判断大小）
    symbol: Any = None     # 关联的 Symbol（用于 addr/call 指令）
    note: str = ""         # 调试注释

    def format(self) -> str:
        parts = [self.op, fmt_operand(self.arg1), fmt_operand(self.arg2), fmt_operand(self.result)]
        text = "(" + ", ".join(parts) + ")"
        return f"{text}  # {self.note}" if self.note else text


@dataclass
class IRUnit:
    """IR 编译单元（主程序或过程）。"""
    name: str
    quads: list[Quad] = field(default_factory=list)
    temp_types: dict[str, Any] = field(default_factory=dict)  # 临时变量名→类型
    lexical_level: int = 0  # 词法嵌套层级


@dataclass
class IRProcedure(IRUnit):
    """过程的 IR 表示，支持嵌套子过程树。"""
    symbol: Any = None
    params: list[Any] = field(default_factory=list)       # 形参 Symbol 列表
    locals: list[Any] = field(default_factory=list)       # 局部变量 Symbol 列表
    children: list["IRProcedure"] = field(default_factory=list)  # 嵌套子过程
    end_label: str = ""  # 过程返回跳转目标标签


@dataclass
class IRProgram:
    globals: list[Any] = field(default_factory=list)
    procedures: list[IRProcedure] = field(default_factory=list)
    main: IRUnit = field(default_factory=lambda: IRUnit("main"))

    def format(self) -> str:
        lines: list[str] = []
        for proc in flatten_procedures(self.procedures):
            lines.append(f"proc {proc.name}:")
            lines.extend(f"  {quad.format()}" for quad in proc.quads)
            lines.append("")
        lines.append("main:")
        lines.extend(f"  {quad.format()}" for quad in self.main.quads)
        return "\n".join(lines).rstrip() + "\n"


def flatten_procedures(procedures: list[IRProcedure]) -> list[IRProcedure]:
    """将嵌套过程树展平为列表（子过程在前，父过程在后）。"""
    result: list[IRProcedure] = []
    for proc in procedures:
        result.extend(flatten_procedures(proc.children))
        result.append(proc)
    return result


def is_temp(value: Operand) -> bool:
    """判断操作数是否为临时变量（以 't' 开头的字符串）。"""
    return isinstance(value, str) and value.startswith("t")


def is_label(value: Operand) -> bool:
    """判断操作数是否为标签（以 'L' 开头的字符串）。"""
    return isinstance(value, str) and value.startswith("L")


def fmt_operand(value: Operand) -> str:
    if value is None:
        return "_"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return value
    name = getattr(value, "name", None)
    if name:
        return str(name)
    return repr(value)

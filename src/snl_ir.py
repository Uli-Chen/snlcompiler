#!/usr/bin/env python3
"""SNL 编译器的四元式中间表示（Quadruple IR）。

四元式格式：(op, arg1, arg2, result)
  - op     : 操作符字符串，如 "+"、"store"、"call"、"label" 等
  - arg1   : 第一操作数（整数常量、临时变量名、Symbol 对象或 None）
  - arg2   : 第二操作数（同上，或字段名字符串）
  - result : 结果目标（临时变量名、标签名或 None）

附加字段：
  - type_info : 操作数/结果的类型信息（TypeInfo 对象），供后端使用
  - symbol    : 关联的语义符号（Symbol 对象），用于 addr/call 等操作
  - note      : 可选的调试注释，在 IR 文本输出中显示
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Union

# 避免循环导入：Symbol 和 TypeInfo 仅在类型检查时引入
if TYPE_CHECKING:
    from snl_semantic import Symbol, TypeInfo

# Operand 是四元式操作数的联合类型：
#   int          — 编译期整数常量
#   str          — 临时变量名（"t0"、"t1"…）或标签名（"L0"、"Lwhile0"…）
#   list[Quad]   — tail_call 的参数列表（每项是一个 param 四元式）
#   Symbol       — 语义符号对象（addr/call 操作中使用）
#   None         — 该操作数不存在
Operand = Union[int, str, "list[Quad]", "Symbol", None]


@dataclass
class Quad:
    """单条四元式指令。"""

    op: str
    arg1: Operand = None
    arg2: Operand = None
    result: Operand = None
    type_info: Any = None   # TypeInfo | None，用 Any 避免循环导入
    symbol: Any = None      # Symbol | None，用 Any 避免循环导入
    note: str = ""

    def format(self) -> str:
        """返回人类可读的四元式文本，如 (+, t0, t1, t2)。"""
        parts = [self.op, fmt_operand(self.arg1), fmt_operand(self.arg2), fmt_operand(self.result)]
        text = "(" + ", ".join(parts) + ")"
        return f"{text}  # {self.note}" if self.note else text


@dataclass
class IRUnit:
    """IR 的基本编译单元，持有一组四元式和临时变量类型表。"""

    name: str
    quads: list[Quad] = field(default_factory=list)
    # 临时变量名 → TypeInfo，供后端分配存储时查询类型大小
    temp_types: dict[str, Any] = field(default_factory=dict)


@dataclass
class IRProcedure(IRUnit):
    """对应一个 SNL 过程的 IR 单元，包含参数、局部变量和嵌套子过程。"""

    symbol: Any = None                              # 对应的 Symbol 对象
    params: list[Any] = field(default_factory=list) # 参数 Symbol 列表（有序）
    locals: list[Any] = field(default_factory=list) # 局部变量 Symbol 列表
    children: list["IRProcedure"] = field(default_factory=list)  # 嵌套子过程
    end_label: str = ""     # 过程结束标签，return 语句跳转到此处
    scope_level: int = 0    # 过程体的词法嵌套深度（全局=0，顶层过程体=1，…）


@dataclass
class IRProgram:
    """整个程序的 IR，包含全局变量、所有过程和主程序体。"""

    globals: list[Any] = field(default_factory=list)           # 全局变量 Symbol 列表
    procedures: list[IRProcedure] = field(default_factory=list) # 顶层过程列表
    main: IRUnit = field(default_factory=lambda: IRUnit("main")) # 主程序体

    def format(self) -> str:
        """将整个 IR 程序格式化为可读文本（用于 --emit-ir 调试输出）。"""
        lines: list[str] = []
        for proc in flatten_procedures(self.procedures):
            lines.append(f"proc {proc.name}:")
            lines.extend(f"  {quad.format()}" for quad in proc.quads)
            lines.append("")
        lines.append("main:")
        lines.extend(f"  {quad.format()}" for quad in self.main.quads)
        return "\n".join(lines).rstrip() + "\n"


def flatten_procedures(procedures: list[IRProcedure]) -> list[IRProcedure]:
    """按后序（children 先于 parent）展平嵌套过程树。

    后序保证被调用者的代码在调用者之前出现，符合 MIPS 汇编的惯例。
    snl_optimizer.py 和 snl_codegen.py 中的同名函数均应使用此版本。
    """
    result: list[IRProcedure] = []
    for proc in procedures:
        result.extend(flatten_procedures(proc.children))
        result.append(proc)
    return result


def is_temp(value: Operand) -> bool:
    """判断操作数是否为编译器生成的临时变量（以 't' 开头的字符串）。"""
    return isinstance(value, str) and value.startswith("t")


def is_label(value: Operand) -> bool:
    """判断操作数是否为跳转标签（以 'L' 开头的字符串）。"""
    return isinstance(value, str) and value.startswith("L")


def fmt_operand(value: Operand) -> str:
    """将操作数格式化为字符串，用于 IR 文本输出。"""
    if value is None:
        return "_"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "[" + ", ".join(fmt_list_operand(item) for item in value) + "]"
    # Symbol 对象或其他带 name 属性的对象
    name = getattr(value, "name", None)
    if name:
        return str(name)
    return repr(value)


def fmt_list_operand(value: Operand) -> str:
    """格式化 tail_call 参数列表中的单个元素。"""
    if isinstance(value, Quad):
        if value.op == "param":
            return f"param({fmt_operand(value.arg1)}, {fmt_operand(value.arg2)})"
        return value.format()
    return fmt_operand(value)

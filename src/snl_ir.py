#!/usr/bin/env python3
"""Quadruple intermediate representation for SNL."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


Operand = int | str | Any | None


@dataclass
class Quad:
    op: str
    arg1: Operand = None
    arg2: Operand = None
    result: Operand = None
    type_info: Any = None
    symbol: Any = None
    note: str = ""

    def format(self) -> str:
        parts = [self.op, fmt_operand(self.arg1), fmt_operand(self.arg2), fmt_operand(self.result)]
        text = "(" + ", ".join(parts) + ")"
        return f"{text}  # {self.note}" if self.note else text


@dataclass
class IRUnit:
    name: str
    quads: list[Quad] = field(default_factory=list)
    temp_types: dict[str, Any] = field(default_factory=dict)


@dataclass
class IRProcedure(IRUnit):
    symbol: Any = None
    params: list[Any] = field(default_factory=list)
    locals: list[Any] = field(default_factory=list)
    children: list["IRProcedure"] = field(default_factory=list)
    end_label: str = ""


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
    result: list[IRProcedure] = []
    for proc in procedures:
        result.extend(flatten_procedures(proc.children))
        result.append(proc)
    return result


def is_temp(value: Operand) -> bool:
    return isinstance(value, str) and value.startswith("t")


def is_label(value: Operand) -> bool:
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

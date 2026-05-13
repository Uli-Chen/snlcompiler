#!/usr/bin/env python3
"""Root command-line entry for the SNL compiler."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


def command_compile(args: argparse.Namespace) -> int:
    from src.snl_codegen import CodegenError, compile_source

    try:
        compile_source(
            args.source,
            args.output,
            optimize=not args.no_opt,
            emit_raw_ir=args.emit_raw_ir,
            emit_ir=args.emit_ir,
        )
    except (OSError, CodegenError) as exc:
        print(f"compiler.py: {exc}", file=sys.stderr)
        return 2
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SNL compiler: compile source to MIPS assembly.")
    subcommands = parser.add_subparsers(dest="command", required=True)

    compile_cmd = subcommands.add_parser("compile", help="compile SNL source to MIPS assembly")
    compile_cmd.add_argument("source", type=Path, help="SNL source file")
    compile_cmd.add_argument("-o", "--output", type=Path, required=True, help="write MIPS assembly to this file")
    compile_cmd.add_argument("--no-opt", action="store_true", help="skip quadruple IR optimization")
    compile_cmd.add_argument("--emit-raw-ir", type=Path, help="write quadruple IR before optimization")
    compile_cmd.add_argument("--emit-ir", type=Path, help="write quadruple IR after optimization")
    compile_cmd.set_defaults(func=command_compile)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

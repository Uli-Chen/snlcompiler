#!/usr/bin/env python3
"""Compare optimized and unoptimized MIPS output on the same SNL workload."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from snl_codegen import compile_source
from snl_runner import MIPSRunner


METRICS = [
    "static_instructions",
    "dynamic_steps",
    "memory_ops",
    "memory_loads",
    "memory_stores",
    "arithmetic_ops",
    "branch_ops",
    "conditional_branch_ops",
    "taken_conditional_branches",
    "syscalls",
    "max_stack_words",
]


def run_variant(
    source: Path,
    out_dir: Path,
    optimize: bool,
    inputs: list[str],
    max_steps: int,
    *,
    peephole: bool | None = None,
) -> dict[str, object]:
    name = "opt" if optimize else "no_opt"
    if peephole is False:
        name += "_no_peephole"
    asm_path = out_dir / f"{source.stem}_{name}.asm"
    raw_ir_path = out_dir / f"{source.stem}_{name}.ir"
    assembly = compile_source(
        source,
        asm_path,
        optimize=optimize,
        peephole=peephole,
        emit_ir=raw_ir_path,
    )
    runner = MIPSRunner(assembly, list(inputs), max_steps=max_steps)
    output = runner.run()
    stats = runner.stats()
    return {"name": name, "asm": asm_path, "ir": raw_ir_path, "output": output, **stats}


def improvement(before: int, after: int) -> str:
    if before == 0:
        return "n/a"
    saved = before - after
    return f"{saved} ({saved / before:.2%})"


def format_table(no_opt: dict[str, object], opt: dict[str, object]) -> str:
    lines = [
        "| metric | no-opt baseline | optimized | saved |",
        "|---|---:|---:|---:|",
    ]
    for metric in METRICS:
        before = int(no_opt[metric])
        after = int(opt[metric])
        lines.append(f"| {metric} | {before} | {after} | {improvement(before, after)} |")
    return "\n".join(lines)


def format_projection(no_opt: dict[str, object], opt: dict[str, object], measured_input: int, projected_input: int) -> str:
    lines = [
        f"Projected savings for input {projected_input} using measured input {measured_input}:",
        "| metric | projected saved |",
        "|---|---:|",
    ]
    for metric in ("dynamic_steps", "memory_ops", "memory_loads", "arithmetic_ops", "max_stack_words"):
        saved = int(no_opt[metric]) - int(opt[metric])
        projected_saved = round(saved * projected_input / measured_input)
        lines.append(f"| {metric} | {projected_saved} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build an optimization baseline for one SNL benchmark.")
    parser.add_argument(
        "source",
        nargs="?",
        type=Path,
        default=PROJECT_ROOT / "test/in/optimization/optimization_extreme_loop.snl",
        help="SNL benchmark source",
    )
    parser.add_argument("--input", nargs="*", default=["10000"], help="values consumed by read statements")
    parser.add_argument("--max-steps", type=int, default=5000000, help="runner instruction limit")
    parser.add_argument("--out-dir", type=Path, default=Path("/private/tmp/snl_opt_baseline"), help="artifact directory")
    parser.add_argument("--project-input", type=int, help="estimate savings for a larger first input value")
    parser.add_argument("--no-peephole", action="store_true", help="disable MIPS peephole on the optimized variant")
    args = parser.parse_args(argv)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    no_opt = run_variant(args.source, args.out_dir, optimize=False, inputs=args.input, max_steps=args.max_steps)
    opt = run_variant(
        args.source,
        args.out_dir,
        optimize=True,
        inputs=args.input,
        max_steps=args.max_steps,
        peephole=False if args.no_peephole else None,
    )

    if no_opt["output"] != opt["output"]:
        print("optimization_baseline.py: optimized output differs from no-opt output", file=sys.stderr)
        print(f"no-opt output: {no_opt['output']!r}", file=sys.stderr)
        print(f"optimized output: {opt['output']!r}", file=sys.stderr)
        return 1

    print(f"source: {args.source}")
    print(f"input: {' '.join(args.input)}")
    print(f"output: {opt['output']!r}")
    print(f"artifacts: {args.out_dir}")
    print()
    print(format_table(no_opt, opt))
    if args.project_input is not None:
        try:
            measured_input = int(args.input[0])
        except (IndexError, ValueError):
            print("\nprojection skipped: first input value is not an integer", file=sys.stderr)
        else:
            if measured_input > 0:
                print()
                print(format_projection(no_opt, opt, measured_input, args.project_input))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Train and compare profile-guided optimization for one SNL workload."""

from __future__ import annotations

import argparse
import json
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


def run_assembly(assembly: str, inputs: list[str], max_steps: int) -> tuple[str, dict[str, int], dict[str, object]]:
    runner = MIPSRunner(assembly, list(inputs), max_steps=max_steps)
    output = runner.run()
    return output, runner.stats(), runner.profile()


def improvement(before: int, after: int) -> str:
    if before == 0:
        return "n/a"
    saved = before - after
    return f"{saved} ({saved / before:.2%})"


def format_table(baseline: dict[str, int], pgo: dict[str, int]) -> str:
    lines = [
        "| metric | optimized baseline | PGO optimized | saved |",
        "|---|---:|---:|---:|",
    ]
    for metric in METRICS:
        before = int(baseline[metric])
        after = int(pgo[metric])
        lines.append(f"| {metric} | {before} | {after} | {improvement(before, after)} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train and compare profile-guided optimization.")
    parser.add_argument("source", type=Path, help="SNL benchmark source")
    parser.add_argument("--train-input", nargs="*", default=["10000"], help="values used to collect profile")
    parser.add_argument("--eval-input", nargs="*", help="values used for final comparison; defaults to train input")
    parser.add_argument("--max-steps", type=int, default=10000000, help="runner instruction limit")
    parser.add_argument("--out-dir", type=Path, default=Path("/private/tmp/snl_pgo_baseline"), help="artifact directory")
    args = parser.parse_args(argv)

    eval_input = args.eval_input if args.eval_input is not None else args.train_input
    args.out_dir.mkdir(parents=True, exist_ok=True)

    baseline_asm_path = args.out_dir / f"{args.source.stem}_opt.asm"
    baseline_ir_path = args.out_dir / f"{args.source.stem}_opt.ir"
    profile_path = args.out_dir / f"{args.source.stem}.profile.json"
    pgo_asm_path = args.out_dir / f"{args.source.stem}_pgo.asm"
    pgo_ir_path = args.out_dir / f"{args.source.stem}_pgo.ir"

    baseline_assembly = compile_source(args.source, baseline_asm_path, optimize=True, emit_ir=baseline_ir_path)
    _, _, profile = run_assembly(baseline_assembly, args.train_input, args.max_steps)
    profile_path.write_text(json.dumps(profile, indent=2, sort_keys=True), encoding="utf-8")

    baseline_output, baseline_stats, _ = run_assembly(baseline_assembly, eval_input, args.max_steps)
    pgo_assembly = compile_source(args.source, pgo_asm_path, optimize=True, profile=profile, emit_ir=pgo_ir_path)
    pgo_output, pgo_stats, _ = run_assembly(pgo_assembly, eval_input, args.max_steps)

    if baseline_output != pgo_output:
        print("pgo_baseline.py: PGO output differs from optimized baseline", file=sys.stderr)
        print(f"baseline output: {baseline_output!r}", file=sys.stderr)
        print(f"PGO output: {pgo_output!r}", file=sys.stderr)
        return 1

    print(f"source: {args.source}")
    print(f"train input: {' '.join(args.train_input)}")
    print(f"eval input: {' '.join(eval_input)}")
    print(f"output: {pgo_output!r}")
    print(f"artifacts: {args.out_dir}")
    print(f"profile: {profile_path}")
    print()
    print(format_table(baseline_stats, pgo_stats))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

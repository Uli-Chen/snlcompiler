#!/usr/bin/env python3
"""Show the incremental contribution of each optimization pass.

Produces a "staircase" table: starting from no optimization, each row adds
one more pass and shows the cumulative improvement over the baseline.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from snl_codegen import compile_source
from snl_optimizer import ALL_PASSES
from snl_runner import MIPSRunner


# The order in which passes are incrementally enabled.
# This reflects the logical dependency order in the optimizer.
PASS_ORDER = ["fold", "algebra", "cse", "copy_prop", "dce", "licm", "tail_rec", "pgo_unroll"]

PASS_DISPLAY = {
    "fold": "常量折叠",
    "algebra": "代数化简",
    "cse": "公共子表达式消除",
    "copy_prop": "复写传播",
    "dce": "死代码消除",
    "licm": "循环不变式外提",
    "tail_rec": "尾递归优化",
    "pgo_unroll": "PGO循环展开",
}

KEY_METRICS = [
    "static_instructions",
    "dynamic_steps",
    "memory_ops",
    "arithmetic_ops",
    "branch_ops",
    "max_stack_words",
]


def run_with_passes(
    source: Path,
    out_dir: Path,
    enabled: set[str],
    inputs: list[str],
    max_steps: int,
    label: str,
) -> dict[str, object]:
    asm_path = out_dir / f"{source.stem}_{label}.asm"
    assembly = compile_source(
        source,
        asm_path,
        optimize=bool(enabled),
        enabled_passes=enabled if enabled else None,
    )
    runner = MIPSRunner(assembly, list(inputs), max_steps=max_steps)
    output = runner.run()
    stats = runner.stats()
    return {"label": label, "output": output, **stats}


def improvement(baseline: int, current: int) -> str:
    if baseline == 0:
        return "—"
    saved = baseline - current
    pct = saved / baseline
    return f"-{pct:.1%}" if saved > 0 else (f"+{-pct:.1%}" if saved < 0 else "0%")


def format_staircase(results: list[dict[str, object]], baseline: dict[str, object]) -> str:
    lines: list[str] = []

    # Header
    header = "| 累计启用的优化 |"
    sep = "|---|"
    for metric in KEY_METRICS:
        header += f" {metric} |"
        sep += "---:|"
    lines.append(header)
    lines.append(sep)

    # Baseline row
    row = "| (无优化 baseline) |"
    for metric in KEY_METRICS:
        row += f" {baseline[metric]} |"
    lines.append(row)

    # Incremental rows
    for result in results:
        row = f"| + {PASS_DISPLAY.get(result['added_pass'], result['added_pass'])} |"
        for metric in KEY_METRICS:
            value = result[metric]
            imp = improvement(int(baseline[metric]), int(value))
            row += f" {value} ({imp}) |"
        lines.append(row)

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Show incremental optimization pass contributions."
    )
    parser.add_argument(
        "source",
        nargs="?",
        type=Path,
        default=PROJECT_ROOT / "test/in/optimization/optimization_extreme_loop.snl",
        help="SNL benchmark source",
    )
    parser.add_argument("--input", nargs="*", default=["100"], help="values consumed by read statements")
    parser.add_argument("--max-steps", type=int, default=5000000, help="runner instruction limit")
    parser.add_argument("--out-dir", type=Path, default=Path("/tmp/snl_incremental"), help="artifact directory")
    args = parser.parse_args(argv)

    args.out_dir.mkdir(parents=True, exist_ok=True)

    # Run baseline (no optimization)
    baseline = run_with_passes(
        args.source, args.out_dir, set(), args.input, args.max_steps, "no_opt"
    )

    # Incrementally add passes
    enabled: set[str] = set()
    results: list[dict[str, object]] = []
    for pass_name in PASS_ORDER:
        enabled = enabled | {pass_name}
        label = f"step_{len(results) + 1}_{pass_name}"
        result = run_with_passes(
            args.source, args.out_dir, set(enabled), args.input, args.max_steps, label
        )
        if result["output"] != baseline["output"]:
            print(
                f"WARNING: output changed after adding {pass_name}: "
                f"{baseline['output']!r} -> {result['output']!r}",
                file=sys.stderr,
            )
        result["added_pass"] = pass_name
        results.append(result)

    print(f"source: {args.source}")
    print(f"input: {' '.join(args.input)}")
    print(f"output: {baseline['output']!r}")
    print()
    print(format_staircase(results, baseline))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

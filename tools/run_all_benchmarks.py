#!/usr/bin/env python3
"""Run all optimization benchmarks and produce a summary report.

Outputs a Markdown table comparing no-opt vs optimized for every benchmark,
plus a PGO comparison for applicable workloads.
"""

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


# Each benchmark: (source_file, inputs, description, pgo_eligible)
BENCHMARKS = [
    ("test/in/optimization/optimization_extreme_loop.snl", ["100"], "极端循环 (CSE+LICM)", True),
    ("test/in/optimization/array_stride_benchmark.snl", ["100"], "数组步进 (LICM+CSE)", True),
    ("test/in/optimization/tail_recursion_benchmark.snl", ["100"], "尾递归优化", False),
    ("test/in/optimization/pgo_hot_loop_benchmark.snl", ["100"], "PGO热循环展开", True),
    ("test/in/optimization/pgo_compute_intensive.snl", ["100"], "PGO计算密集循环", True),
    ("test/in/optimization/constant_folding_test.snl", [], "常量折叠", False),
    ("test/in/optimization/cse_test.snl", [], "公共子表达式消除", False),
]

KEY_METRICS = [
    ("dynamic_steps", "动态指令数"),
    ("memory_ops", "访存次数"),
    ("arithmetic_ops", "算术指令"),
    ("branch_ops", "分支指令"),
    ("max_stack_words", "最大栈深(字)"),
]


def run_variant(source: Path, out_dir: Path, optimize: bool, inputs: list[str], max_steps: int, tag: str, **kwargs) -> dict:
    asm_path = out_dir / f"{source.stem}_{tag}.asm"
    assembly = compile_source(source, asm_path, optimize=optimize, **kwargs)
    runner = MIPSRunner(assembly, list(inputs), max_steps=max_steps)
    output = runner.run()
    stats = runner.stats()
    return {"output": output, **stats}


def pct(before: int, after: int) -> str:
    if before == 0:
        return "—"
    saved = (before - after) / before
    return f"-{saved:.1%}" if saved > 0 else (f"+{-saved:.1%}" if saved < 0 else "0%")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run all optimization benchmarks.")
    parser.add_argument("--max-steps", type=int, default=5000000, help="runner instruction limit")
    parser.add_argument("--out-dir", type=Path, default=Path("/tmp/snl_all_benchmarks"), help="artifact directory")
    parser.add_argument("--json", type=Path, help="write raw results as JSON to this file")
    args = parser.parse_args(argv)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    all_results: list[dict] = []

    # === Part 1: no-opt vs optimized ===
    print("# SNL 编译器优化效果汇总报告\n")
    print("## 一、Baseline vs 全优化对比\n")

    header = "| Benchmark | 描述 |"
    sep = "|---|---|"
    for _, display in KEY_METRICS:
        header += f" {display} (baseline→opt) |"
        sep += "---|"
    print(header)
    print(sep)

    for source_rel, inputs, desc, pgo_eligible in BENCHMARKS:
        source = PROJECT_ROOT / source_rel
        if not source.exists():
            print(f"| {source.stem} | {desc} | (文件不存在) |", " |" * (len(KEY_METRICS) - 1))
            continue

        no_opt = run_variant(source, args.out_dir, False, inputs, args.max_steps, "noopt")
        opt = run_variant(source, args.out_dir, True, inputs, args.max_steps, "opt")

        correct = "✅" if no_opt["output"] == opt["output"] else "❌"
        row = f"| {source.stem} | {desc} {correct} |"
        for metric, _ in KEY_METRICS:
            before = int(no_opt[metric])
            after = int(opt[metric])
            row += f" {before}→{after} ({pct(before, after)}) |"
        print(row)

        all_results.append({
            "source": source_rel,
            "desc": desc,
            "inputs": inputs,
            "no_opt": {m: int(no_opt[m]) for m, _ in KEY_METRICS},
            "opt": {m: int(opt[m]) for m, _ in KEY_METRICS},
            "correct": no_opt["output"] == opt["output"],
            "pgo_eligible": pgo_eligible,
        })

    # === Part 2: PGO comparison ===
    print("\n## 二、PGO 优化额外收益\n")
    print("| Benchmark | 描述 | dynamic_steps (opt→PGO) | branch_ops (opt→PGO) | memory_ops (opt→PGO) |")
    print("|---|---|---|---|---|")

    for source_rel, inputs, desc, pgo_eligible in BENCHMARKS:
        if not pgo_eligible:
            continue
        source = PROJECT_ROOT / source_rel
        if not source.exists():
            continue

        # Train profile
        opt_asm_path = args.out_dir / f"{source.stem}_opt_pgo_train.asm"
        opt_assembly = compile_source(source, opt_asm_path, optimize=True)
        train_runner = MIPSRunner(opt_assembly, list(inputs), max_steps=args.max_steps)
        train_runner.run()
        profile = train_runner.profile()

        # Run with PGO
        opt_stats = run_variant(source, args.out_dir, True, inputs, args.max_steps, "opt_base")
        pgo_stats = run_variant(source, args.out_dir, True, inputs, args.max_steps, "pgo", profile=profile)

        correct = "✅" if opt_stats["output"] == pgo_stats["output"] else "❌"
        row = f"| {source.stem} | {desc} {correct} |"
        for metric in ("dynamic_steps", "branch_ops", "memory_ops"):
            before = int(opt_stats[metric])
            after = int(pgo_stats[metric])
            row += f" {before}→{after} ({pct(before, after)}) |"
        print(row)

    # === Part 3: Tail recursion highlight ===
    print("\n## 三、尾递归优化栈空间对比\n")
    tail_source = PROJECT_ROOT / "test/in/optimization/tail_recursion_benchmark.snl"
    if tail_source.exists():
        for n in [10, 100, 500]:
            no_opt = run_variant(tail_source, args.out_dir, False, [str(n)], args.max_steps, f"tail_noopt_{n}")
            opt = run_variant(tail_source, args.out_dir, True, [str(n)], args.max_steps, f"tail_opt_{n}")
            print(
                f"| n={n} | 栈深: {no_opt['max_stack_words']}→{opt['max_stack_words']} "
                f"({pct(int(no_opt['max_stack_words']), int(opt['max_stack_words']))}) | "
                f"步数: {no_opt['dynamic_steps']}→{opt['dynamic_steps']} "
                f"({pct(int(no_opt['dynamic_steps']), int(opt['dynamic_steps']))}) |"
            )

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(all_results, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\n原始数据已写入: {args.json}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

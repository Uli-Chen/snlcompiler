#!/usr/bin/env python3
"""Run a small golden regression suite for the SNL compiler."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from snl_codegen import CodegenError, compile_source


@dataclass(frozen=True)
class PositiveCase:
    name: str
    expected_output: list[str]
    inputs: list[str]


@dataclass(frozen=True)
class NegativeCase:
    name: str
    expected_error: str


POSITIVE_CASES = [
    PositiveCase("codegen_test.snl", ["9", "1"], []),
    PositiveCase("complex_calls_test.snl", ["8", "120", "128", "256"], []),
    PositiveCase("fibonacci_sequence_test.snl", ["0", "1", "1", "2", "3", "5", "8", "13"], ["8"]),
    PositiveCase("nested_scope_capture_test.snl", ["3"], []),
    PositiveCase("nested_param_capture_test.snl", ["9"], []),
    PositiveCase("deep_nested_capture_test.snl", ["10"], []),
    PositiveCase("recursive_frame_test.snl", ["12"], []),
    PositiveCase("recursive_sum_test.snl", ["15"], []),
    PositiveCase("var_element_param_test.snl", ["27"], []),
    PositiveCase("record_value_param_test.snl", ["17", "29", "7", "9"], []),
    PositiveCase("array_value_param_test.snl", ["111", "116", "11", "1"], []),
    PositiveCase("record_assignment_test.snl", ["1", "2", "9", "2"], []),
    PositiveCase("array_assignment_test.snl", ["4", "6", "99", "8"], []),
    PositiveCase("stress_loop_test.snl", ["2000"], []),
]

NEGATIVE_CASES = [
    NegativeCase("semantic_error_test.snl", "front-end checks failed"),
    NegativeCase("aggregate_write_error_test.snl", "write expression must be scalar"),
    NegativeCase("array_bounds_error_test.snl", "array index 0 out of bounds"),
    NegativeCase("record_field_error_test.snl", "has no field"),
    NegativeCase("param_count_error_test.snl", "expects 2 argument(s), got 1"),
    NegativeCase("var_param_error_test.snl", "must be an assignable variable"),
]


def parse_program_output(result_text: str) -> list[str]:
    lines = result_text.splitlines()
    try:
        start = lines.index("Program Output")
    except ValueError:
        return []
    return [line.strip() for line in lines[start + 1 :] if line.strip()]


def run_positive(case: PositiveCase, source_dir: Path, out_dir: Path) -> str | None:
    source = source_dir / case.name
    case_out_dir = out_dir / source.stem
    try:
        _assembly, result_text = compile_source(source, case_out_dir, case.inputs, run_target=True)
    except CodegenError as exc:
        return f"{case.name}: unexpected compile failure: {exc}"
    actual = parse_program_output(result_text)
    if actual != case.expected_output:
        return f"{case.name}: expected {case.expected_output}, got {actual}"
    return None


def run_negative(case: NegativeCase, source_dir: Path, out_dir: Path) -> str | None:
    source = source_dir / case.name
    case_out_dir = out_dir / source.stem
    try:
        compile_source(source, case_out_dir, [], run_target=True)
    except CodegenError as exc:
        message = str(exc)
        if case.expected_error not in message:
            return f"{case.name}: expected error containing {case.expected_error!r}, got {message!r}"
        return None
    return f"{case.name}: expected compilation to fail"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run golden regression tests for the SNL compiler.")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=PROJECT_ROOT / "test" / "out" / "regression_suite",
        help="directory to store per-case compiler outputs",
    )
    args = parser.parse_args(argv)

    source_dir = PROJECT_ROOT / "test" / "in"
    args.out_dir.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []

    for case in POSITIVE_CASES:
        failure = run_positive(case, source_dir, args.out_dir)
        if failure:
            failures.append(failure)

    for case in NEGATIVE_CASES:
        failure = run_negative(case, source_dir, args.out_dir)
        if failure:
            failures.append(failure)

    if failures:
        print("FAIL")
        for failure in failures:
            print(failure)
        return 1

    print("PASS")
    print(f"positive={len(POSITIVE_CASES)} negative={len(NEGATIVE_CASES)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

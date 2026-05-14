# 优化分析工具

本目录包含用于量化和展示 SNL 编译器优化效果的工具脚本。

## 使用前提

所有脚本需要 Python 3.10+（推荐 3.13），从项目根目录运行。

## 工具列表

### `optimization_baseline.py` — 单 Benchmark 对比

对比同一个 SNL 程序在 **无优化** 和 **全优化** 下的 MIPS 执行指标。

```bash
python3.13 tools/optimization_baseline.py test/in/optimization/optimization_extreme_loop.snl --input 100
```

支持 `--project-input N` 估算更大输入下的节省量，`--no-peephole` 关闭后端窥孔优化。

### `incremental_passes.py` — 逐步叠加优化阶梯对比

从无优化开始，逐个开启优化 pass，展示每一步的累计改进。适合答辩展示"每个优化技术的独立贡献"。

```bash
python3.13 tools/incremental_passes.py test/in/optimization/optimization_extreme_loop.snl --input 100
python3.13 tools/incremental_passes.py test/in/optimization/tail_recursion_benchmark.snl --input 100
```

### `pgo_baseline.py` — Profile-Guided 优化对比

先用训练输入收集 profile，再对比 PGO 优化 vs 普通优化的效果。

```bash
python3.13 tools/pgo_baseline.py test/in/optimization/pgo_hot_loop_benchmark.snl --train-input 100 --eval-input 100
```

### `run_all_benchmarks.py` — 全量 Benchmark 汇总报告

一键运行所有 benchmark，输出 Markdown 格式的汇总表，包含：
1. Baseline vs 全优化对比
2. PGO 额外收益
3. 尾递归栈空间对比

```bash
python3.13 tools/run_all_benchmarks.py
python3.13 tools/run_all_benchmarks.py --json results/latest.json  # 同时输出 JSON
```

## 优化 Pass 名称

在 `incremental_passes.py` 中按以下顺序逐步启用：

| Pass 名称 | 说明 |
|-----------|------|
| `fold` | 常量折叠 |
| `algebra` | 代数化简 (x+0, x*1 等) |
| `cse` | 公共子表达式消除 |
| `copy_prop` | 复写传播 |
| `dce` | 死代码消除 |
| `licm` | 循环不变式外提 |
| `tail_rec` | 尾递归优化 |
| `pgo_unroll` | PGO 热循环展开 |

## Benchmark 用例

所有优化测试用例位于 `test/in/optimization/` 目录下。

| 文件 | 考察重点 |
|------|---------|
| `optimization_extreme_loop.snl` | 循环内大量重复表达式 → CSE + LICM |
| `array_stride_benchmark.snl` | 数组访问 → LICM + 地址计算优化 |
| `tail_recursion_benchmark.snl` | 递归 → 尾递归消除，栈 O(n)→O(1) |
| `pgo_hot_loop_benchmark.snl` | 简单热循环 → PGO 展开减少分支 |
| `pgo_compute_intensive.snl` | 计算密集循环 → PGO 展开后分支占比下降 |
| `constant_folding_test.snl` | 编译期常量计算 + 死分支消除 |
| `cse_test.snl` | (a+b)*(a+b) → 复用中间结果 |

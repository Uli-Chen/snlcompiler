# SNL Compiler

一个面向编译原理课程设计的精简 SNL 编译器。

当前公开能力只保留一条主路径：读取 `.snl` 源程序，完成词法、语法、语义检查，生成四元式中间代码，进行 IR 优化，最后生成 MIPS32 汇编目标代码。词法分析、语法分析、语义分析和 IR 调试输出仍作为内部/开发能力存在，但不再作为默认用户输出。

## 目录结构

```text
.
├── compiler.py              # 公开 CLI：compile
├── src/
│   ├── grammar.txt          # 词法规则
│   ├── snl_lexer.py         # 词法分析
│   ├── snl_parser.py        # token -> typed AST
│   ├── snl_semantic.py      # AST 语义分析和类型标注
│   ├── snl_ir.py            # 四元式 IR 数据结构
│   ├── snl_irgen.py         # typed AST -> 四元式 IR
│   ├── snl_optimizer.py     # IR 优化（8 个 pass）
│   ├── snl_codegen.py       # 四元式 IR -> MIPS32
│   └── snl_runner.py        # 独立 MIPS 子集运行器
├── docs/
│   ├── grammar.md           # 语法规则文档
│   ├── snl_lexer.md         # 词法分析器设计文档
│   └── snl_optimizer.md     # IR 优化器设计文档
├── test/in/
│   ├── functional/          # 功能正确性测试
│   ├── error/               # 错误检测测试（应报错）
│   └── optimization/        # 优化效果 benchmark
└── tools/                   # 优化效果分析工具
```

## 编译

```bash
python3 compiler.py compile test/in/functional/codegen_test.snl -o /tmp/codegen_test.asm
```

成功时只写出指定的 `.asm` 文件。失败时在 `stderr` 输出前端诊断并返回非零退出码。

默认开启 IR 优化。关闭优化：

```bash
python3 compiler.py compile test/in/functional/codegen_test.snl -o /tmp/codegen_test.asm --no-opt
```

导出优化前后四元式 IR：

```bash
python3 compiler.py compile test/in/optimization/cse_test.snl \
  -o /tmp/cse_test.asm \
  --emit-raw-ir /tmp/cse_raw.ir \
  --emit-ir /tmp/cse_opt.ir
```

## 编译流程

```text
SNL 源码 → Token → typed AST → 语义标注 AST → 四元式 IR → IR 优化 → MIPS32 汇编
```

## IR 优化

优化器包含 8 个 pass，详细设计见 [docs/snl_optimizer.md](docs/snl_optimizer.md)：

| Pass | 说明 |
|------|------|
| `fold` | 常量折叠 — 编译期计算常量表达式 |
| `algebra` | 代数化简 — 消除 x+0, x*1 等恒等运算 |
| `cse` | 公共子表达式消除 — 复用重复计算结果 |
| `copy_prop` | 复写传播 — 用源操作数替换临时变量 |
| `dce` | 死代码消除 — 删除未使用的临时赋值 |
| `licm` | 循环不变式外提 — 将不变计算移出循环 |
| `tail_rec` | 尾递归优化 — 递归调用转循环，栈 O(n)→O(1) |
| `pgo_unroll` | PGO 热循环展开 — 根据 profile 展开热循环体 |

## 运行目标代码

```bash
python3 src/snl_runner.py /tmp/codegen_test.asm
python3 src/snl_runner.py /tmp/read_test.asm --input 1 2 3
```

## 语言支持边界

当前实现支持：

- `integer`、`char`、类型别名
- 数组和记录类型、数组元素读写、记录字段读写
- 过程调用、值参数、`var` 参数、递归过程调用
- `if`、`while`、`read`、`write`、`return`

不支持数组或记录的整体值语义（整体赋值、作为 value 参数传递、`read`/`write` 整个数组/记录）。需要传递数组或记录时，请使用 `var` 参数传地址。

## 测试用例

测试用例按用途分为三类，分别存放在 `test/in/` 的子目录中。

### functional/ — 功能正确性测试

验证编译器各阶段的正确性，编译运行后应产生预期输出。

| 文件 | 说明 |
|------|------|
| `lexer_test.snl` | 覆盖所有 token 类型：关键字、标识符、整数、字符、符号、注释 |
| `codegen_test.snl` | 综合功能：数组、记录、过程调用、条件分支 |
| `ir_array_record_test.snl` | 数组和记录的 IR 地址计算 |
| `recursive_sum_test.snl` | 递归过程调用和栈帧管理 |
| `recursive_frame_test.snl` | 递归中局部变量的栈帧隔离 |
| `complex_calls_test.snl` | 多过程嵌套调用、fibonacci、factorial |

### error/ — 错误检测测试

验证编译器的错误诊断能力，编译时应报错并返回非零退出码。

| 文件 | 说明 |
|------|------|
| `syntax_error_test.snl` | 语法错误：缺少分号、不完整表达式、括号未闭合 |
| `semantic_error_test.snl` | 语义错误：重复声明、类型不匹配、未定义变量、参数错误 |
| `aggregate_error_test.snl` | 聚合类型错误：数组/记录整体赋值和值传递 |
| `division_by_zero_error.snl` | 编译期除零检测 |

### optimization/ — 优化效果 benchmark

验证优化器各 pass 的效果，配合 `tools/` 下的分析脚本使用。

| 文件 | 考察重点 |
|------|---------|
| `constant_folding_test.snl` | 编译期常量计算 `1+2*3`、常量条件折叠 `if 1<2` |
| `cse_test.snl` | 公共子表达式 `(a+b)*(a+b)` 复用中间结果 |
| `optimization_extreme_loop.snl` | 循环内 16 个重复表达式 → CSE + LICM |
| `array_stride_benchmark.snl` | 循环内数组访问 → LICM 外提地址计算 |
| `tail_recursion_benchmark.snl` | 尾递归累加 → 栈空间 O(n)→O(1) |
| `pgo_hot_loop_benchmark.snl` | 简单热循环 → PGO 展开减少分支开销 |
| `pgo_compute_intensive.snl` | 计算密集循环 → PGO 展开后分支占比下降 |

## 开发验证

### 功能正确性

```bash
python3 compiler.py compile test/in/functional/codegen_test.snl -o /tmp/codegen_test.asm
python3 src/snl_runner.py /tmp/codegen_test.asm

python3 compiler.py compile test/in/functional/recursive_sum_test.snl -o /tmp/recursive_sum_test.asm
python3 src/snl_runner.py /tmp/recursive_sum_test.asm

python3 compiler.py compile test/in/functional/complex_calls_test.snl -o /tmp/complex_calls_test.asm
python3 src/snl_runner.py /tmp/complex_calls_test.asm

python3 compiler.py compile test/in/functional/ir_array_record_test.snl -o /tmp/ir_array_record_test.asm
python3 src/snl_runner.py /tmp/ir_array_record_test.asm
```

### 错误检测（应报错）

```bash
python3 compiler.py compile test/in/error/semantic_error_test.snl -o /tmp/semantic_error_test.asm
python3 compiler.py compile test/in/error/aggregate_error_test.snl -o /tmp/aggregate_error_test.asm
python3 compiler.py compile test/in/error/division_by_zero_error.snl -o /tmp/division_by_zero_error.asm
```

### 优化效果

```bash
# 常量折叠
python3 compiler.py compile test/in/optimization/constant_folding_test.snl \
  -o /tmp/constant_folding_test.asm --emit-ir /tmp/constant_opt.ir

# CSE
python3 compiler.py compile test/in/optimization/cse_test.snl \
  -o /tmp/cse_test.asm --emit-raw-ir /tmp/cse_raw.ir --emit-ir /tmp/cse_opt.ir

# 循环优化（对比 --no-opt）
python3 compiler.py compile test/in/optimization/optimization_extreme_loop.snl -o /tmp/loop_opt.asm
python3 compiler.py compile test/in/optimization/optimization_extreme_loop.snl -o /tmp/loop_noopt.asm --no-opt

# 尾递归
python3 compiler.py compile test/in/optimization/tail_recursion_benchmark.snl -o /tmp/tail.asm
python3 src/snl_runner.py /tmp/tail.asm --input 1000
```

### 优化分析工具

```bash
# 单 benchmark 对比
python3 tools/optimization_baseline.py test/in/optimization/optimization_extreme_loop.snl --input 100

# 逐步叠加 pass 对比
python3 tools/incremental_passes.py test/in/optimization/optimization_extreme_loop.snl --input 100

# PGO 对比
python3 tools/pgo_baseline.py test/in/optimization/pgo_hot_loop_benchmark.snl --train-input 100 --eval-input 100

# 全量 benchmark 汇总
python3 tools/run_all_benchmarks.py
```

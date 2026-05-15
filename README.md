# SNL Compiler

一个面向编译原理课程设计的 SNL 语言编译器，将 `.snl` 源程序编译为 MIPS32 汇编代码。

## 编译流程

```text
SNL 源码 → 词法分析 → 语法分析 → 语义分析 → 四元式 IR → IR 优化 → MIPS32 汇编
         (Lexer)   (Parser)  (Semantic)  (IRGen)   (Optimizer) (Codegen)
```

## 目录结构

```text
.
├── compiler.py              # CLI 入口：compile 命令
├── src/
│   ├── grammar.txt          # 词法规则配置文件
│   ├── snl_lexer.py         # 词法分析器（配置驱动，贪心最长匹配）
│   ├── snl_parser.py        # 递归下降语法分析器（Token → typed AST）
│   ├── snl_semantic.py      # 语义分析器（类型检查、符号表、作用域）
│   ├── snl_ir.py            # 四元式 IR 数据结构定义
│   ├── snl_irgen.py         # IR 生成器（typed AST → 四元式 IR）
│   ├── snl_optimizer.py     # IR 优化器（8 个 pass，三层架构）
│   ├── snl_codegen.py       # 代码生成器（四元式 IR → MIPS32 汇编）
│   └── snl_runner.py        # MIPS32 子集模拟运行器
├── docs/
│   ├── grammar.md           # SNL 语法规则（BNF）
│   ├── snl_lexer.md         # 词法分析器设计文档
│   ├── snl_parser.md        # 语法分析器设计文档
│   ├── snl_semantic.md      # 语义分析器设计文档
│   ├── snl_irgen.md         # IR 生成器设计文档
│   ├── snl_optimizer.md     # IR 优化器设计文档
│   ├── snl_codegen.md       # 代码生成器设计文档
│   └── snl_runner.md        # MIPS 运行器设计文档
├── test/in/
│   ├── functional/          # 功能正确性测试
│   ├── error/               # 错误检测测试（应报错）
│   └── optimization/        # 优化效果 benchmark
└── tools/                   # 优化效果分析工具
```

## 快速开始

### 编译 SNL 程序

```bash
python3 compiler.py compile test/in/functional/codegen_test.snl -o /tmp/codegen_test.asm
```

成功时写出 `.asm` 文件，失败时在 stderr 输出诊断信息并返回非零退出码。

### 运行目标代码

```bash
python3 src/snl_runner.py /tmp/codegen_test.asm
python3 src/snl_runner.py /tmp/read_test.asm --input 1 2 3
```

### 关闭优化

```bash
python3 compiler.py compile test/in/functional/codegen_test.snl -o /tmp/out.asm --no-opt
```

### 导出中间表示

```bash
python3 compiler.py compile test/in/optimization/cse_test.snl \
  -o /tmp/cse.asm \
  --emit-raw-ir /tmp/cse_raw.ir \
  --emit-ir /tmp/cse_opt.ir
```

### PGO 流程

```bash
# 1. 编译
python3 compiler.py compile test/in/optimization/pgo_hot_loop_benchmark.snl -o /tmp/pgo.asm

# 2. 收集 profile
python3 src/snl_runner.py /tmp/pgo.asm --input 10000 --profile /tmp/pgo.profile.json

# 3. 用 profile 重编译
python3 compiler.py compile test/in/optimization/pgo_hot_loop_benchmark.snl \
  -o /tmp/pgo_opt.asm --profile-in /tmp/pgo.profile.json
```

## 各模块概述

| 模块 | 职责 | 设计文档 |
|------|------|---------|
| `snl_lexer.py` | 配置驱动的流式词法扫描，贪心最长匹配 | [docs/snl_lexer.md](docs/snl_lexer.md) |
| `snl_parser.py` | 递归下降分析，生成 typed AST，含错误恢复 | [docs/snl_parser.md](docs/snl_parser.md) |
| `snl_semantic.py` | 符号表管理、类型检查、作用域分析 | [docs/snl_semantic.md](docs/snl_semantic.md) |
| `snl_ir.py` | 四元式 IR 数据结构（Quad、IRUnit、IRProgram） | — |
| `snl_irgen.py` | AST 到四元式 IR 的翻译 | [docs/snl_irgen.md](docs/snl_irgen.md) |
| `snl_optimizer.py` | 8 个优化 pass，三层架构 | [docs/snl_optimizer.md](docs/snl_optimizer.md) |
| `snl_codegen.py` | 四元式 IR 到 MIPS32 汇编，含窥孔优化 | [docs/snl_codegen.md](docs/snl_codegen.md) |
| `snl_runner.py` | MIPS32 子集模拟器，支持 profile 收集 | [docs/snl_runner.md](docs/snl_runner.md) |

## IR 优化

优化器包含 8 个 pass，按三层架构组织：

| 层次 | Pass | 说明 |
|------|------|------|
| 清理层 | `fold` | 常量折叠 — 编译期计算常量表达式 |
| 清理层 | `algebra` | 代数化简 — 消除 x+0, x*1 等恒等运算 |
| 局部精简层 | `cse` | 公共子表达式消除 — 复用重复计算结果 |
| 局部精简层 | `copy_prop` | 复写传播 — 用源操作数替换临时变量 |
| 局部精简层 | `dce` | 死代码消除 — 删除未使用的临时赋值 |
| 结构优化层 | `licm` | 循环不变式外提 — 将不变计算移出循环 |
| 结构优化层 | `tail_rec` | 尾递归优化 — 递归调用转循环，栈 O(n)→O(1) |
| 结构优化层 | `pgo_unroll` | PGO 热循环展开 — 根据 profile 展开热循环体 |

详细设计见 [docs/snl_optimizer.md](docs/snl_optimizer.md)。

## 语言支持

支持的特性：

- 基本类型：`integer`、`char`、类型别名
- 复合类型：数组、记录，元素/字段读写
- 控制流：`if-then-else-fi`、`while-do-endwh`
- 过程：过程声明与调用、值参数、`var` 引用参数、递归
- I/O：`read`、`write`、`return`

不支持：数组或记录的整体值语义（整体赋值、作为 value 参数传递）。需要传递数组或记录时，使用 `var` 参数传地址。

## 测试用例

### functional/ — 功能正确性

| 文件 | 说明 |
|------|------|
| `lexer_test.snl` | 覆盖所有 token 类型 |
| `codegen_test.snl` | 数组、记录、过程调用、条件分支 |
| `ir_array_record_test.snl` | 数组和记录的 IR 地址计算 |
| `recursive_sum_test.snl` | 递归过程调用和栈帧管理 |
| `recursive_frame_test.snl` | 递归中局部变量的栈帧隔离 |
| `complex_calls_test.snl` | 多过程嵌套调用、fibonacci、factorial |

### error/ — 错误检测

| 文件 | 说明 |
|------|------|
| `syntax_error_test.snl` | 缺少分号、括号未闭合等语法错误 |
| `semantic_error_test.snl` | 重复声明、类型不匹配、未定义变量 |
| `aggregate_error_test.snl` | 数组/记录整体赋值和值传递 |
| `division_by_zero_error.snl` | 编译期除零检测 |

### optimization/ — 优化 benchmark

| 文件 | 考察重点 |
|------|---------|
| `constant_folding_test.snl` | 编译期常量计算、常量条件折叠 |
| `cse_test.snl` | 公共子表达式复用 |
| `optimization_extreme_loop.snl` | 循环内重复表达式 → CSE + LICM |
| `array_stride_benchmark.snl` | 循环内数组访问 → LICM 外提 |
| `tail_recursion_benchmark.snl` | 尾递归 → 栈空间 O(n)→O(1) |
| `pgo_hot_loop_benchmark.snl` | 热循环 → PGO 展开 |
| `pgo_compute_intensive.snl` | 计算密集循环 → PGO 展开 |

## 优化分析工具

```bash
# 单 benchmark 优化前后对比
python3 tools/optimization_baseline.py test/in/optimization/optimization_extreme_loop.snl --input 100

# 逐步叠加 pass 对比
python3 tools/incremental_passes.py test/in/optimization/optimization_extreme_loop.snl --input 100

# PGO 对比
python3 tools/pgo_baseline.py test/in/optimization/pgo_hot_loop_benchmark.snl --train-input 100 --eval-input 100

# 全量 benchmark 汇总
python3 tools/run_all_benchmarks.py
```

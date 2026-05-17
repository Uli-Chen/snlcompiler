# SNL IR 优化器说明书

## 概述

`src/snl_optimizer.py` 是 SNL 编译器的四元式 IR 优化阶段。它位于前端语义检查和后端 MIPS 代码生成之间，只改写 `IRProgram` 中的四元式序列，不改 AST、符号表和类型信息。

完整编译流水线如下：

```text
SNL 源程序
  -> 词法分析
  -> 语法分析
  -> 语义分析
  -> SNLIRGenerator.generate() 生成四元式 IR
  -> optimize_program(ir) 优化四元式 IR
  -> IRMIPSGenerator.generate() 生成 MIPS32 汇编
```

命令行默认启用优化：

```bash
python compiler.py compile input.snl -o out.asm
```

可以用 `--no-opt` 跳过优化，也可以用 `--emit-raw-ir` 和 `--emit-ir` 分别输出优化前后的 IR：

```bash
python compiler.py compile input.snl -o out.asm \
  --emit-raw-ir raw.ir \
  --emit-ir optimized.ir
```

## IR 输入与输出

优化器处理的数据结构来自 `src/snl_ir.py`：

- `IRProgram`：整个程序，包含全局变量、过程树和主程序。
- `IRProcedure`：单个过程，继承自 `IRUnit`，还保存形参、局部变量、嵌套子过程和返回标签。
- `IRUnit`：主程序或过程的四元式序列。
- `Quad`：单条四元式，格式为 `(op, arg1, arg2, result)`，并附带 `type_info`、`symbol`、`note`。

优化器原地更新每个 `IRUnit.quads`：

```python
unit.quads = fold_constants(unit.quads)
unit.quads = simplify_algebra(unit.quads)
...
```

优化后，后端仍然看到同一种四元式接口，只是其中的冗余计算、死临时变量、循环不变式和尾递归调用已经被改写。

## 总入口：`optimize_program`

```python
def optimize_program(program: IRProgram, *, enabled_passes: set[str] | None = None) -> IRProgram:
    passes = set(ALL_PASSES) if enabled_passes is None else enabled_passes
    for proc in flatten_procedures(program.procedures):
        optimize_unit(proc, passes)
        if "tail_rec" in passes:
            eliminate_tail_recursion(proc)
            optimize_unit(proc, passes)
    optimize_unit(program.main, passes)
    return program
```

入口做三件事：

1. 确定启用的 pass 集合。默认启用 `ALL_PASSES`。
2. 按 `flatten_procedures()` 的顺序优化所有过程。这个顺序是子过程在前、父过程在后。
3. 对每个过程先跑普通优化，再做尾递归消除，然后再跑一轮普通优化清理尾递归改写暴露的冗余。主程序没有尾递归入口，只跑普通优化。

当前 pass 名称：

```python
ALL_PASSES = (
    "fold",
    "algebra",
    "cse",
    "dce",
    "licm",
    "tail_rec",
)
```

## 单元优化流程：`optimize_unit`

单个 `IRUnit` 内部的优化顺序是固定的：

```text
第一轮：
  fold -> algebra -> cse -> dce -> licm

第二轮清理：
  dce -> fold -> dce
```

这样安排的原因是：

- `fold` 和 `algebra` 先把表达式变简单，给后续 CSE 和 DCE 暴露机会。
- `cse` 会把重复表达式改成 `assign`，随后 DCE 可以清理不再使用的临时变量。
- `dce` 删除已经不再使用的临时变量定义，降低后续 pass 的噪声。
- `licm` 会移动四元式，可能产生新的别名、常量和死临时变量，所以后面再跑一轮清理。

## 核心配置

优化器用几个操作集合表达安全边界：

```python
PURE_EXPR_OPS = {"+", "-", "*", "/", "addr", "index_addr", "field_addr", "load"}
REMOVABLE_TEMP_OPS = PURE_EXPR_OPS | {"assign"}
HOISTABLE_OPS = {"+", "-", "*", "/", "assign", "addr", "index_addr", "field_addr", "load"}
COMMUTATIVE_OPS = {"+", "*"}
BLOCK_END_OPS = {"goto", "return", "call", "tail_call", "read"}
```

其中最重要的是 `PURE_EXPR_OPS` 和 `REMOVABLE_TEMP_OPS`：

- 只有纯表达式才允许被 CSE 复用。
- 只有纯表达式和 `assign` 生成的死临时变量才允许被 DCE 删除。
- `store`、`read`、`call`、`tail_call` 默认视为有副作用，优化器不会删除它们。

## 常量折叠：`fold_constants`

常量折叠维护一个 `constants: dict[str, int]`，记录当前已知为整数常量的临时变量。

处理步骤：

1. 用 `replace_known_constants()` 把四元式参数中的已知临时变量替换为整数。
2. 如果 `+ - * /` 的两个操作数都是整数，在编译期计算结果。
3. 如果 `if_false_<` 或 `if_false_=` 的两个操作数都是整数，直接化简控制流。
4. 如果遇到 `assign 常量 -> 临时变量`，记录该临时变量的常量值。
5. 如果某条四元式重新定义了一个临时变量，清除它旧的常量记录。
6. 遇到 `store`、`read`、`call`、`tail_call` 时清空常量表。

示例：

```text
优化前：
  (*, 3, 4, t0)
  (+, t0, 1, t1)

优化后：
  (assign, 12, _, t0)  # 常量折叠
  (assign, 13, _, t1)  # 常量折叠
```

除法使用 `int(left / right)`，与 MIPS `div` 的向零截断语义一致。除数为 0 时不折叠，保留到运行期处理。

## 代数化简：`simplify_algebra`

代数化简只处理算术四元式，把恒等运算改成简单赋值。

典型规则：

```text
x + 0 -> x
0 + x -> x
x - 0 -> x
x * 0 -> 0
x * 1 -> x
1 * x -> x
x / 1 -> x
0 / c -> 0    # c 是非零整数
```

示例：

```text
优化前：
  (+, t0, 0, t1)
  (*, t1, 1, t2)

优化后：
  (assign, t0, _, t1)  # 代数化简
  (assign, t1, _, t2)  # 代数化简
```

这些 `assign` 随后会被死代码删除继续压缩。

## 公共表达式消除：`common_subexpression_elimination`

当前实现是基本块内公共表达式消除：只在同一个基本块内复用已经计算过的表达式，一旦遇到 `label` 或基本块结束指令就清空状态。

它维护两个表：

- `aliases`：临时变量别名表，例如 `t2 -> t0`。
- `expressions`：表达式键到首次结果临时变量的映射。

表达式键由 `expression_key()` 生成：

- `+` 和 `*` 会按规范顺序排列操作数，因此 `a + b` 和 `b + a` 视为同一个表达式。
- `addr` 使用 `symbol.name` 作为键。
- 其他表达式使用 `(op, arg1_key, arg2_key, type_key)`。

示例：

```text
优化前：
  (+, t0, t1, t2)
  (+, t1, t0, t3)

优化后：
  (+, t0, t1, t2)
  (assign, t2, _, t3)  # CSE
```

控制流处理比较保守：

- 遇到任何 `label` 都认为新基本块开始，清空 `aliases` 和 `expressions`。
- `goto`、`return`、`call`、`tail_call`、`read`、条件跳转结束基本块，清空状态。
- `store`、`read`、`call`、`tail_call` 可能改变内存，因此只清除 `load` 相关表达式，保留纯算术表达式。

## 死临时变量删除：`eliminate_dead_temp_assignments`

DCE 从后向前扫描四元式，维护 `live` 集合。

如果一条四元式定义了某个临时变量，并且：

- 这个临时变量之后不再被使用；
- 该四元式属于 `REMOVABLE_TEMP_OPS`，即纯表达式或 `assign`；

那么这条四元式可以删除。

示例：

```text
优化前：
  (+, t0, t1, t2)
  (assign, 1, _, t3)
  (write, t2, _, _)

优化后：
  (+, t0, t1, t2)
  (write, t2, _, _)
```

DCE 不删除 `store`、`read`、`write`、`writeln`、`param`、`call`、`tail_call` 等副作用或调用相关四元式。

## 循环识别：`find_simple_loops`

循环优化依赖一个简单循环识别器：

```text
label L
  ...
goto L
```

`find_simple_loops()` 先记录所有 label 的位置，再寻找向前跳转的 `goto`。如果 `goto` 的目标 label 出现在当前指令之前，就认为 `(label_index, goto_index)` 是一个简单循环。

这个识别方式足够覆盖 IR 生成器常见的 while/repeat 风格循环，但不做完整控制流图和自然循环分析。

## 循环不变式外提：`hoist_loop_invariants`

LICM 对每个简单循环做以下处理：

1. 收集循环体内定义过的临时变量。
2. 检查每条四元式是否可外提。
3. 可外提的四元式必须是纯表达式，且操作数都不是循环体内定义的临时变量。
4. 将这些四元式移动到循环 label 之前。

示例：

```text
优化前：
  label L0
  (+, a, b, t0)      # a、b 在循环中不变
  (*, t0, i, t1)
  ...
  goto L0

优化后：
  (+, a, b, t0)      # 循环外提
  label L0
  (*, t0, i, t1)
  ...
  goto L0
```

实现的安全边界是保守的：只有 `PURE_EXPR_OPS` 且结果为临时变量的四元式会被考虑外提。

当前识别的归纳变量模式比较具体：

- 循环体中对变量地址有唯一一次 `store`。
- 存入值来自 `load(该变量地址) + 常量` 或 `load(该变量地址) - 常量`。
- `index_addr` 的索引来自该归纳变量的 `load`。

## 尾递归消除：`eliminate_tail_recursion`

尾递归 pass 只作用于过程，不作用于主程序。它识别过程对自身的尾调用：

```text
param ...
call proc
return
```

或：

```text
param ...
call proc
# 后面只有 label，直到过程结束
```

识别成功后，将 `call` 改写为 `tail_call`：

```text
(call, proc, _, _) -> (tail_call, proc, _, _)  # 尾递归消除
```

后端 `IRMIPSGenerator.emit_tail_call()` 会把 `tail_call` 翻译成：

1. 用待传参数覆盖当前栈帧中的参数槽。
2. 清空 `pending_params`。
3. 直接跳转到当前过程的 `_body` 标签，而不是 `jal` 创建新栈帧。

这样尾递归在目标代码中等价于循环，可以避免深递归不断消耗栈空间。

## 辅助规范化函数

几个辅助函数贯穿多个 pass：

- `canonical_operand()`：沿别名链追溯临时变量的最终来源。
- `operand_key()`：为常量和临时变量生成稳定比较键（元组格式），用于交换律排序。
- `type_key()`：将类型信息纳入表达式键，避免不同类型对象误判为同一表达式。
- `normalize_quad()`：按别名表规范化 `arg1` 和 `arg2`。
- `defined_temp()`：判断一条四元式是否定义了临时变量。
- `temp_uses()`：收集一条四元式使用到的临时变量。

这些函数的核心目标是：让优化器能识别等价表达式，同时不要把控制流、内存写入和过程调用优化坏。

## 一个完整运行例子

假设 IR 中有：

```text
(*, 2, 3, t0)
(+, t0, 0, t1)
(+, t1, x, t2)
(+, x, t1, t3)
(write, t3, _, _)
```

优化过程大致为：

```text
fold:
  t0 = 6
  t1 = 6
  后续表达式中的 t1 被替换为 6

cse:
  识别 (+, 6, x) 与 (+, x, 6) 等价
  t3 = t2

dce:
  删除不再被使用的早期常量和临时定义
```

最终保留的 IR 更短，后端生成的 MIPS 指令也更少。

## 与代码生成器的关系

优化器不会直接生成 MIPS，但它会影响后端看到的四元式形态：

- 常量折叠和代数化简减少算术指令。
- CSE 减少重复 `addr`、`load`、算术表达式。
- DCE 减少临时变量存储和载入。
- LICM 把循环不变表达式移出循环体。
- 尾递归消除把递归调用改写为 `tail_call`，后端复用当前栈帧并跳转到过程体。

因此，优化器是 IR 到 MIPS 之间的“瘦身和改形”阶段。

## 安全性与限制

当前实现有意保持简单和保守：

1. **没有完整 CFG**：循环识别基于 `label` 和回跳 `goto`，不构建控制流图和支配关系。
2. **CSE 是基本块内版本**：遇到 `label` 或块结束指令会清空状态，不跨基本块复用表达式。
3. **内存模型保守**：`store`、`read`、`call`、`tail_call` 会清除 load 相关表达式，避免错误复用旧内存值。
4. **DCE 只删临时变量定义**：不会删除用户变量写入、I/O、调用或参数准备。
5. **LICM 只外提纯表达式**：不外提 store、call、read、write 等副作用指令。
6. **尾递归消除只处理自递归尾调用**：不处理互递归，也不处理调用后仍有有效计算的情况。

这些限制换来的是实现清晰、调试容易，并且更适合课程设计规模的 SNL 编译器。

## 优化效果量化对比

以下数据基于项目中全部 12 个测试用例，对比优化前后的 IR 四元式数量、MIPS 指令数量和运行步数：

| 测试文件 | IR（前） | IR（后） | IR 削减 | MIPS（前） | MIPS（后） | MIPS 削减 | 步数（前） | 步数（后） | 步数削减 |
|---------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| test | 7 | 4 | 42.9% | 38 | 23 | 39.5% | 37 | 22 | 40.5% |
| associativity_test | 14 | 8 | 42.9% | 73 | 43 | 41.1% | 72 | 42 | 41.7% |
| static_link_test | 14 | 14 | 0.0% | 70 | 68 | 2.9% | 69 | 67 | 2.9% |
| static_link_sibling_test | 16 | 16 | 0.0% | 85 | 83 | 2.4% | 84 | 82 | 2.4% |
| static_link_param_test | 30 | 26 | 13.3% | 136 | 120 | 11.8% | 135 | 119 | 11.9% |
| static_link_record_test | 18 | 17 | 5.6% | 78 | 74 | 5.1% | 77 | 73 | 5.2% |
| static_link_recursive_test | 26 | 26 | 0.0% | 103 | 95 | 7.8% | 233 | 179 | 23.2% |
| **opt_fold_heavy** | 10 | 4 | **60.0%** | 56 | 23 | **58.9%** | 55 | 22 | **60.0%** |
| **opt_cse_heavy** | 38 | 30 | **21.1%** | 174 | 150 | **13.8%** | 173 | 149 | **13.9%** |
| **opt_licm_heavy** | 36 | 35 | 2.8% | 145 | 144 | 0.7% | 1038 | 860 | **17.1%** |
| **opt_tailrec_heavy** | 28 | 26 | 7.1% | 98 | 86 | 12.2% | 5660 | 3460 | **38.9%** |
| **opt_combined_heavy** | 45 | 34 | **24.4%** | 206 | 164 | **20.4%** | 205 | 163 | **20.5%** |
| **合计** | **282** | **240** | **14.9%** | **1262** | **1073** | **15.0%** | — | — | — |

分析：

- **常量折叠主导**（opt_fold_heavy）：所有计算在编译期完成，IR 削减 60%，MIPS 指令削减 59%。这是优化效果的上界——当程序全是常量表达式时，优化器几乎可以把所有计算消除。
- **CSE 主导**（opt_cse_heavy）：大量重复的 `a+b` 表达式被识别并复用，IR 削减 21%。
- **LICM 主导**（opt_licm_heavy）：循环不变式 `base*4` 被外提到循环前，IR 只减少 1 条（外提是移动而非删除），但运行步数削减 17%——循环执行 10 次，不变式从执行 10 次变为 1 次。
- **尾递归主导**（opt_tailrec_heavy）：100 层递归变为循环，运行步数削减 39%。普通递归每次调用需要 prologue/epilogue 开销，尾递归消除后这些开销全部省去。
- **综合优化**（opt_combined_heavy）：常量折叠 + CSE + DCE 联合作用，IR 削减 24%，MIPS 削减 20%。死代码（`dead1`、`dead2`）被 DCE 完全删除。

所有测试用例在优化前后的运行结果完全一致，验证了优化的语义正确性。

# SNL IR 优化器设计文档

本文梳理 `src/snl_optimizer.py` 的设计思路和关键实现细节。

## 设计出发点：为什么是这些优化

优化器不是教科书 pass 的堆砌。选择哪些优化、以什么顺序组合，完全由 **SNL 语言本身的特征** 和 **IR 生成阶段的产出特点** 驱动。

### SNL 语言给优化带来的机会

1. **无指针、无动态分配、无函数指针**。变量的地址在编译期完全确定，`store`/`load` 的目标不存在别名歧义。这意味着我们可以做比 C 编译器更激进的优化而不需要复杂的别名分析——只要追踪 `addr` 四元式的 symbol 字段就够了。

2. **循环只有 `while`，递归是唯一的"高级控制流"**。没有 `for`、没有 `break`/`continue`、没有 `goto`。循环在 IR 层面呈现为干净的 `label...goto` 回边结构，识别和变换都很简单。性能瓶颈集中在两个地方：循环体和递归调用。

3. **数组/记录访问生成大量地址计算链**。一次 `a[i]` 的读取在 IR 层面展开为 `addr → index_addr → load` 三条四元式。循环内重复访问同一数组元素会产生大量冗余的地址计算——这是 SNL 程序最大的优化空间。

4. **所有变量都是栈上的，作用域严格嵌套**。没有全局可变状态的干扰，过程内的优化可以放心进行。

### IR 生成阶段的产出特点

IR 生成器（`snl_irgen.py`）采用最简单的"每个子表达式一个临时变量"策略，不做任何优化。这意味着：

- 常量表达式 `1 + 2 * 3` 会生成 3 条四元式而不是直接算出 7
- 同一个表达式出现两次会生成两套完全独立的四元式
- `x + 0`、`x * 1` 这类恒等运算不会被消除
- 循环内不变的计算每次迭代都重新执行

这是有意为之的设计——让 IR 生成保持简单正确，把优化的职责完全交给优化器。

## 优化路径的三层架构

基于上述分析，我们将优化组织为三个层次，每层解决一类问题：

```
┌─────────────────────────────────────────────────────┐
│  第一层：清理层（fold + algebra）                      │
│  目标：消除 IR 生成的机械冗余，给后续 pass 干净输入      │
├─────────────────────────────────────────────────────┤
│  第二层：局部精简层（cse + copy_prop + dce）           │
│  目标：基本块内消除重复计算，三者形成协作闭环            │
├─────────────────────────────────────────────────────┤
│  第三层：结构优化层（licm + tail_rec + pgo_unroll）    │
│  目标：针对循环和递归——SNL 的两大性能瓶颈              │
└─────────────────────────────────────────────────────┘
```

### 为什么是这个顺序

层次之间存在严格的依赖关系：

- **清理层必须在前**：fold 把 `1+2*3` 折叠为 `7` 后，CSE 才能发现两个 `assign 7` 是相同的；algebra 把 `x+0` 简化为 `assign x` 后，copy_prop 才能传播这个复写。如果不先清理，后续 pass 看到的是被噪声淹没的 IR。

- **局部精简层必须在 LICM 之前**：LICM 判断"循环不变式"时需要知道哪些临时变量是等价的。如果 CSE 没有先合并重复表达式，LICM 会认为它们是不同的计算而无法外提。

- **LICM 之后需要再跑一轮 copy_prop + dce**：外提操作会在循环前插入新的赋值，这些赋值可能产生新的复写传播机会和死代码。

- **尾递归在标准 pass 之后**：尾递归变换会引入 `tail_call` 和新的 `label`，变换后需要再跑一轮标准 pass 清理。

- **PGO 展开在最后**：它依赖 profile 数据（运行时信息），且展开后会产生大量冗余的 store-load 对，需要专门的 store-load 转发 + 标准 pass 来清理。

### 完整的 pass 执行序列

```
fold → algebra → cse → copy_prop → dce → licm → copy_prop → dce → fold → dce
                                                  ↑ 第二轮清理：处理 LICM 产生的新机会
```

注意 copy_prop 和 dce 出现了两次——这不是重复，而是因为 LICM 会创造新的优化机会。

## 第一层：清理层

### 常量折叠 fold_constants

**解决的问题**：IR 生成器对 `x := 1 + 2 * 3` 会生成：

```
* 2, 3, t1
+ 1, t1, t2
assign t2, _, x
```

折叠后变为：

```
assign 7, _, x
```

**算法**：维护 `constants: dict[str, int]`，线性扫描时将已知常量替换到后续四元式的操作数中。当两个操作数都是常量时，直接计算结果。

**条件折叠**：`if 1 < 2 then ... else ... fi` 中的条件在编译期可判定，直接删除 `if_false_` 或替换为 `goto`，消除死分支。

**保守策略**：遇到 `store`/`read`/`call` 时清空常量表。因为这些操作可能改变内存中的值，之前记录的常量可能失效。在 SNL 中这个策略偏保守（SNL 没有指针别名问题），但保证了正确性且实现简单。

### 代数化简 simplify_algebra

**解决的问题**：IR 生成器不做强度削减。`while i < limit do ... i := i + 1` 中如果 limit 恰好是 0，会产生 `x + 0` 这类无意义运算。更常见的是数组下标计算中的 `(idx - low) * size`，当 `low = 0` 或 `size = 1` 时可以简化。

**规则集**（完整列举）：

| 模式 | 简化为 | 来源场景 |
|------|--------|---------|
| `x + 0` / `0 + x` | `x` | 数组下标偏移为 0 |
| `x - 0` | `x` | 同上 |
| `x * 1` / `1 * x` | `x` | 元素大小为 1（char 数组） |
| `x * 0` / `0 * x` | `0` | 常量折叠后暴露 |
| `x / 1` | `x` | 元素大小为 1 |
| `0 / x` (x≠0) | `0` | 常量折叠后暴露 |

这些规则不是随意选的——每条都对应 SNL 数组/记录地址计算中的实际模式。

## 第二层：局部精简层

这三个 pass 形成一个**协作闭环**，缺一不可：

```
CSE 发现重复表达式 → 产生 "t2 := t1" 复写
    ↓
copy_prop 传播复写 → 后续使用 t2 的地方直接用 t1
    ↓
dce 发现 t2 不再被引用 → 删除 "t2 := t1"
```

如果只有 CSE 没有 copy_prop，代码中会残留大量无意义的 `assign`；如果没有 dce，这些 `assign` 会一直留在 IR 中浪费寄存器。

### 公共子表达式消除 eliminate_common_subexpressions

**解决的问题**：SNL 数组访问 `a[i]` 在循环内每次迭代都生成完整的 `addr → index_addr → load` 链。如果循环体内多次读取 `a[i]`，这些链条完全重复。

**作用域**：基本块内。遇到 `label`（块起始）或 `goto`/`call`/`if_false_*`（块终止）时清空表达式表。不跨越控制流边界是为了保证正确性——跨块的 CSE 需要数据流分析，对 SNL 的收益不值得这个复杂度。

**交换律处理**：`a + b` 和 `b + a` 产生相同的 key。对 `+` 和 `*` 操作数按字典序排列。

**内存操作的处理**：`store` 后清空表达式表（之前的 `load` 结果可能失效），但保留 aliases（临时变量间的等价关系不受内存写入影响）。这个区分很重要——如果 store 后连 aliases 都清空，CSE 的效果会大打折扣。

### 复写传播 propagate_copies

**解决的问题**：CSE 将 `t2 := a + b` 替换为 `t2 := t1`（t1 是之前计算过 a+b 的结果）。后续使用 t2 的四元式应该直接使用 t1，减少不必要的间接引用。

**实现**：维护 `aliases: dict[str, Operand]`，通过 `canonical_operand` 追溯等价链到终点。用 `seen` 集合防止循环引用。

### 死代码消除 eliminate_dead_temp_assignments

**解决的问题**：经过 copy_prop 后，很多临时变量的赋值不再被任何后续四元式引用。

**算法**：反向扫描，维护 live 集合。只删除**临时变量**的纯计算赋值——用户变量的赋值即使"看起来没用"也必须保留（可能通过 `var` 参数被外部观察到）。

**可删除操作**：`+`、`-`、`*`、`/`、`addr`、`index_addr`、`field_addr`、`load`、`assign`。`store`、`write`、`call`、`param` 等有副作用的操作永远保留。

## 第三层：结构优化层

前两层处理的是"IR 生成的机械冗余"。第三层针对的是**程序结构本身的低效**——循环和递归。

### 循环不变式外提 hoist_loop_invariants

**解决的问题**：这是 SNL 优化中收益最大的单个 pass。考虑：

```snl
while i < limit do
  acc := acc + values[1] + values[2] + values[3];
  i := i + 1
endwh
```

`values[1]`、`values[2]`、`values[3]` 的地址计算（`addr values → index_addr 1 → load`）在每次迭代都重复执行，但结果从不改变。外提后这些计算只执行一次。

**为什么 CSE 不够**：CSE 只在基本块内工作。循环体跨越了多个基本块（至少有循环头的 label 和循环尾的 goto），CSE 无法跨迭代复用结果。LICM 专门解决这个问题。

**循环识别**：`goto` 跳转到前面的 `label` 即构成回边。SNL 的 `while` 编译后恰好是这个结构，不需要复杂的支配树分析。

**不变式判定（迭代求解）**：

一条四元式是循环不变式，当且仅当：
1. 操作属于可外提类型（纯计算）
2. 所有操作数要么不在循环内定义，要么已被判定为不变式
3. 对 `load`：加载地址对应的符号在循环内未被 `store`/`read` 修改

条件 2 意味着不变式判定是传递的——需要迭代直到不动点。例如：

```
t1 := addr values        ← 不变（values 地址固定）
t2 := index_addr t1, 1   ← 不变（t1 是不变式，1 是常量）
t3 := load t2            ← 不变（t2 是不变式，values 在循环内未被写入）
```

三条都可以外提，但必须按依赖顺序逐步发现。

**地址符号追踪**：为了判断 `load` 是否安全外提，需要知道加载地址对应哪个源级变量。通过追踪 `addr` 四元式的 symbol 字段，沿 `index_addr`/`field_addr` 链传播。这正是 SNL "无指针别名"特性带来的便利——在 C 中这个分析要复杂得多。

### 尾递归优化 eliminate_tail_recursion

**解决的问题**：SNL 没有循环变量（没有 `for`），很多算法自然写成递归形式。例如累加：

```snl
procedure sumTo(integer n; var integer acc);
begin
  if n < 1 then acc := acc
  else acc := acc + n; sumTo(n - 1, acc)
  fi;
  return(acc)
end
```

`sumTo(100000, result)` 会产生 100000 层栈帧。尾递归优化将其转为循环，栈空间 O(n) → O(1)。

**识别条件**：

1. `call` 调用的是当前过程自身
2. 参数数量匹配
3. 调用之后到过程结束之间只有收尾操作（`label`、`return`、`goto end_label`）

条件 3 是"尾位置"的判定——调用结果不被进一步使用，直接返回。

**变换方式**：

```
原始:                              变换后:
                                   label sumTo_tail_entry    ← 新增入口
if_false_< n, 1, L_else            if_false_< n, 1, L_else
...                                ...
call sumTo(n-1, acc)               tail_call [params], sumTo_tail_entry
...                                ...
```

代码生成阶段将 `tail_call` 翻译为：重写参数到形参位置 + `goto tail_entry`。

**为什么不在 AST 层做**：在 IR 层做尾递归优化有两个好处：(1) 可以利用前面 pass 的结果——fold 和 copy_prop 可能简化了调用参数；(2) 变换后可以再跑一轮标准 pass 清理引入的冗余。

### PGO 热循环展开 unroll_hot_simple_loops

**解决的问题**：前面的优化都是静态的——不需要运行程序就能做。但有一个问题静态分析无法回答：**哪个循环是热点？**

一个只执行 3 次的循环，展开它只会增加代码体积。一个执行 10000 次的循环，展开后分支开销减少 75%，收益显著。

**Profile-Guided 的设计选择**：

我们选择 PGO 而非盲目展开，原因是：
1. SNL 程序通常很小，代码膨胀的代价相对较高
2. 运行器（`snl_runner.py`）天然可以收集 label 执行计数，零额外成本
3. 阈值（50 次）和展开因子（4 倍）可以根据实际 profile 调整

**可展开条件**（保守策略）：

- 循环体内无嵌套 label（结构简单）
- 无 call/return/read（无副作用）
- 恰好一个 `if_false_` 条件跳转（单一退出条件）

不满足条件的循环不展开——宁可放弃优化也不冒语义错误的风险。

**展开后的清理——Store-Load 转发**：

展开后最大的问题是相邻迭代之间的冗余访存。原始循环：

```
store acc_value → acc_addr    ← 迭代末尾写回
load acc_addr → acc_value     ← 下一迭代开头读取
```

展开 4 次后，中间 3 个 store-load 对是纯冗余。`forward_store_to_load` 专门处理这个模式：如果 store 之后没有其他写入同一地址，后续的 load 直接用 store 的值替换。

关键设计：`if_false_` 不清空 store_map。条件跳转不修改内存，展开体内的条件判断不应该打断 store-load 转发链。这是针对展开后 IR 结构的特定优化。

## 协作效果示例

以 `optimization_extreme_loop.snl` 为例，展示各层如何协作：

```snl
while i < limit do
  acc := acc + limit * 1 + limit * 1 + ... (16 个 limit*1)
  i := i + 1
endwh
```

1. **algebra** 将所有 `limit * 1` 简化为 `assign limit`
2. **fold** 无额外效果（limit 不是编译期常量）
3. **cse** 发现 16 个 `assign limit` 的结果等价，合并为 1 个
4. **copy_prop** 传播合并后的临时变量
5. **dce** 删除 15 个不再被引用的赋值
6. **licm** 发现 `addr limit → load` 是循环不变式，外提到循环前
7. 第二轮 **copy_prop + dce** 清理 LICM 产生的新复写

最终效果：循环体从 ~50 条四元式精简到 ~5 条，动态指令数减少 90%+。

## CLI 使用

```bash
# 默认全优化
python3 compiler.py compile test/in/functional/codegen_test.snl -o /tmp/out.asm

# 关闭优化（对比用）
python3 compiler.py compile test/in/functional/codegen_test.snl -o /tmp/out.asm --no-opt

# 导出优化前后 IR
python3 compiler.py compile test/in/optimization/cse_test.snl \
  -o /tmp/cse.asm --emit-raw-ir /tmp/cse_raw.ir --emit-ir /tmp/cse_opt.ir

# PGO 流程：编译 → 收集 profile → 用 profile 重编译
python3 compiler.py compile test/in/optimization/pgo_hot_loop_benchmark.snl -o /tmp/pgo.asm
python3 src/snl_runner.py /tmp/pgo.asm --input 10000 --profile /tmp/pgo.profile.json
python3 compiler.py compile test/in/optimization/pgo_hot_loop_benchmark.snl \
  -o /tmp/pgo_opt.asm --profile-in /tmp/pgo.profile.json
```

## 设计决策总结

| 决策 | 选择 | 理由 |
|------|------|------|
| CSE 作用域 | 基本块内 | SNL 程序小，跨块 CSE 需要数据流分析，收益不值得复杂度 |
| LICM 循环识别 | 回边检测 | SNL 只有 while，编译后恰好是 label...goto，不需要支配树 |
| 别名分析 | 追踪 addr 的 symbol 字段 | SNL 无指针，地址来源完全确定 |
| 尾递归在 IR 层做 | 而非 AST 层 | 可利用前序 pass 结果，变换后可再优化 |
| PGO 而非盲目展开 | profile 驱动 | 避免小循环的无意义膨胀 |
| store/call 后清空状态 | 保守策略 | 正确性优先，SNL 程序小，损失的优化机会有限 |
| 展开后 store-load 转发 | 专用 pass | 针对展开产生的特定冗余模式，标准 pass 无法处理 |
| Pass 多次执行 | copy_prop+dce 跑两轮 | LICM 创造新机会，一轮不够 |

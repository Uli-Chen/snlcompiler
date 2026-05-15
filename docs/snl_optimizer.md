# SNL IR 优化器说明书

## 概述

`snl_optimizer.py` 对四元式 IR 执行基本块内的局部优化。当前实现两种经典优化：

1. **常量折叠（Constant Folding）**：在编译期计算常量表达式
2. **公共子表达式消除（CSE）**：复用已计算的相同表达式结果

两种优化都是**基本块内**的局部优化，不跨越控制流边界。

---

## 核心常量

```python
PURE_EXPR_OPS = {"+", "-", "*", "/", "addr", "index_addr", "field_addr", "load"}
COMMUTATIVE_OPS = {"+", "*"}
BLOCK_END_OPS = {"goto", "return", "call", "read"}
```

- `PURE_EXPR_OPS`：无副作用的操作，其结果可以被 CSE 复用
- `COMMUTATIVE_OPS`：满足交换律的操作，CSE 时规范化操作数顺序
- `BLOCK_END_OPS`：终结基本块的操作

---

## 常量折叠：`fold_constants(quads)`

### 算法

维护一个 `constants: dict[str, int]` 字典，追踪当前已知为常量值的临时变量。

对每条四元式：

1. **替换已知常量**：如果 arg1 或 arg2 是已知常量的临时变量，替换为其常量值
2. **折叠算术**：如果算术操作的两个操作数都是整数常量，直接计算结果
   - 例：`(+, 3, 5, t0)` → `(assign, 8, _, t0)`
3. **折叠条件跳转**：如果条件跳转的两个操作数都是常量，直接决定是否跳转
   - 条件为真 → 删除跳转指令（fall through）
   - 条件为假 → 替换为无条件跳转 `goto`
4. **追踪常量**：`assign` 指令如果源是常量，记录目标为常量
5. **清除常量**：`store`、`read`、`call` 可能改变内存，清除所有常量追踪

### 示例

```
输入：
  (*, 3, 4, t0)
  (+, t0, 1, t1)

输出：
  (assign, 12, _, t0)   # 3*4 折叠为 12
  (assign, 13, _, t1)   # 12+1 折叠为 13
```

### 安全性

- 除法除数为 0 时不折叠（`eval_arithmetic` 返回 None）
- `store`/`read`/`call` 后清除所有常量（保守策略，因为内存可能被修改）

---

## 公共子表达式消除：`eliminate_common_subexpressions(quads)`

### 算法

维护两个字典：
- `aliases: dict[str, Operand]`：临时变量的别名映射（用于规范化）
- `expressions: dict[tuple, str]`：已计算的表达式 → 结果临时变量

对每条四元式：

1. **基本块边界检测**：遇到 `label` 时清除所有状态
2. **规范化操作数**：通过 aliases 将操作数追溯到其"规范形式"
3. **查找已有表达式**：如果当前操作是纯表达式且已有相同计算，替换为赋值
4. **记录新表达式**：否则将当前表达式记录到 expressions 中
5. **副作用处理**：`store`/`read`/`call` 后清除 expressions（内存可能变化）
6. **块结束处理**：`goto`/`return`/`call`/`read`/`if_false_*` 后清除所有状态

### 表达式键（expression_key）

用于判断两个表达式是否"相同"：

```python
def expression_key(quad, aliases) -> tuple:
    # 对交换律操作，规范化操作数顺序
    # 对 addr 操作，用 symbol.name 作为键
    # 其他操作，用 (op, arg1_key, arg2_key, type_key) 作为键
```

### 操作数规范化（canonical_operand）

沿 aliases 链追溯到最终值，避免因中间赋值导致相同表达式被认为不同：

```python
# 如果 t1 = t0，t2 = t0，那么使用 t1 和 t2 的表达式应该被识别为相同
```

### 示例

```
输入：
  (addr, _, _, t0)  # &x
  (load, t0, _, t1)
  (addr, _, _, t2)  # &x  （同一个变量）
  (load, t2, _, t3)

输出：
  (addr, _, _, t0)  # &x
  (load, t0, _, t1)
  (assign, t0, _, t2)  # CSE: 复用 t0
  (assign, t1, _, t3)  # CSE: 复用 t1
```

---

## 优化入口：`optimize_program(program)`

```python
def optimize_program(program: IRProgram) -> IRProgram:
    for proc in walk_procedures(program.procedures):
        optimize_unit(proc)
    optimize_unit(program.main)
    return program

def optimize_unit(unit: IRUnit) -> None:
    unit.quads = fold_constants(unit.quads)
    unit.quads = eliminate_common_subexpressions(unit.quads)
```

优化顺序：先常量折叠，再 CSE。常量折叠可能暴露更多 CSE 机会。

---

## 已知限制

1. **仅基本块内优化**：不进行跨块的数据流分析，循环不变量外提等全局优化
2. **保守的内存模型**：任何 store/read/call 都会清除所有表达式缓存，即使它们操作的是不同变量
3. **不做死代码消除**：折叠后产生的无用赋值不会被删除
4. **不做强度削减**：如 `x * 2` 不会被替换为 `x + x` 或移位
5. **编译期除零不报警**：`10 / 0` 不折叠但也不产生编译警告

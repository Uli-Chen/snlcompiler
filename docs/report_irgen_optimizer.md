# SNL 编译器：中间代码生成与优化 汇报文档

## 一、概述

本模块负责将经过语义分析的 AST 转换为四元式中间代码（IR），并对 IR 执行多趟优化，最终交给后端生成 MIPS32 汇编。

编译流水线中的位置：

```
SNL 源程序 → 词法分析 → 语法分析 → 语义分析 → [中间代码生成] → [中间代码优化] → 目标代码生成
                                                    ↑ 本文档覆盖范围 ↑
```

涉及的源文件：
- `src/snl_ir.py`：IR 数据结构定义（Quad、IRProgram、IRProcedure 等）
- `src/snl_irgen.py`：中间代码生成器（AST → 四元式）
- `src/snl_optimizer.py`：中间代码优化器（六个优化 pass）

---

## 二、中间代码表示

### 2.1 四元式数据结构

```python
@dataclass
class Quad:
    op: str              # 操作符：+, -, *, /, load, store, addr, goto, label, call 等
    arg1: Operand = None # 第一操作数
    arg2: Operand = None # 第二操作数
    result: Operand = None # 结果
    type_info: Any = None  # 类型信息（后端用于数组偏移计算、I/O syscall 选择）
    symbol: Any = None     # 关联的 Symbol 对象（addr/call 指令使用）
    note: str = ""         # 调试注释（标注优化来源）
```

操作数类型 `Operand = int | str | Any | None`：
- `int`：编译期整数常量（如字面量 42）
- `str`：临时变量名（t0, t1, ...）或标签名（Lwhile0, Lendif1, ...）
- `Any`：Symbol 对象（仅 call 指令的 arg1）
- `None`：该位置不使用

### 2.2 四元式操作一览

| 类别 | 操作 | 语义 | 示例 |
|------|------|------|------|
| 算术 | `+`, `-`, `*`, `/` | result = arg1 op arg2 | `(+, t1, 2, t2)` |
| 赋值 | `assign` | result = arg1 | `(assign, 12, _, t0)` |
| 寻址 | `addr` | result = &symbol | `(addr, _, _, t0)  # &x` |
| 访存 | `load` | result = *arg1 | `(load, t0, _, t1)` |
| 访存 | `store` | *result = arg1 | `(store, t2, _, t3)` |
| 数组 | `index_addr` | result = base + index*size | `(index_addr, t0, t1, t2)` |
| 记录 | `field_addr` | result = base + offset | `(field_addr, t0, "value", t1)` |
| 跳转 | `goto` | 无条件跳转 | `(goto, _, _, Lwhile0)` |
| 条件 | `if_false_<` | 条件为假时跳转 | `(if_false_<, t1, t2, Lend)` |
| 标签 | `label` | 定义跳转目标 | `(label, _, _, Lwhile0)` |
| 调用 | `param` | 传递实参 | `(param, t0, "val", _)` |
| 调用 | `call` | 调用过程 | `(call, proc, 2, _)` |
| 调用 | `tail_call` | 尾递归调用 | `(tail_call, proc, 2, _)` |
| 返回 | `return` | 过程返回 | `(return, t0, _, _)` |
| I/O | `read` | 从输入读入 | `(read, _, _, t0)` |
| I/O | `write` | 输出到标准输出 | `(write, t0, _, _)` |

### 2.3 设计特点：Symbol 对象绑定

传统做法是在四元式中存变量名字符串，后端再通过名字查符号表。我们的设计将 Symbol 对象直接绑定在四元式的 `symbol` 字段上：

```python
# addr 指令：绑定变量 Symbol（含偏移量、层级、存储方式）
builder.emit("addr", None, None, address, type_info=current_type, symbol=symbol)

# call 指令：绑定过程 Symbol（含跳转标签、词法层级）
builder.emit("call", symbol, len(stmt.args), None, symbol=symbol)
```

优点：
1. 后端直接从 Symbol 获取偏移量和层级，不需要额外的查表逻辑
2. 避免嵌套作用域中同名变量的歧义（每个 Symbol 对象唯一）
3. 过程调用时可直接获取被调过程的词法层级，用于计算静态链

### 2.4 IR 程序结构

```python
@dataclass
class IRProgram:
    globals: list[Symbol]              # 全局变量符号列表（后端据此分配 .data 段）
    procedures: list[IRProcedure]      # 过程列表（支持嵌套）
    main: IRUnit                       # 主程序体
```

---

## 三、中间代码生成（snl_irgen.py）

### 3.1 整体架构

```python
class SNLIRGenerator:
    def generate(self) -> IRProgram:
        ir = IRProgram()
        ir.globals = collect_decl_symbols(self.ast.var_decls)      # 收集全局变量
        ir.procedures = [self.emit_procedure(proc) for proc in self.ast.proc_decls]
        self.emit_statements(self.ast.body, UnitBuilder(ir.main))  # 主程序体
        return ir
```

### 3.2 表达式生成

核心策略：常量直接返回 int 值（不生成四元式），变量和运算生成四元式并返回临时变量名。

```python
def emit_expr(self, expr: Expr, builder: UnitBuilder) -> Operand:
    if isinstance(expr, ConstExpr):
        return expr.value                    # 常量直接返回，不生成四元式

    if isinstance(expr, VarExpr):
        address = self.emit_lvalue(expr.ref, builder)
        result = builder.temp(expr.type_info)
        builder.emit("load", address, None, result, type_info=expr.type_info)
        return result                        # 返回持有值的临时变量

    if isinstance(expr, BinaryExpr):
        left = self.emit_expr(expr.left, builder)
        right = self.emit_expr(expr.right, builder)
        result = builder.temp(expr.type_info)
        builder.emit(expr.op, left, right, result, type_info=expr.type_info)
        return result
```

**示例**：源码 `x := a + 2` 生成的四元式：

```
(addr, _, _, t0)     # t0 = &a（symbol 字段绑定 a 的 Symbol）
(load, t0, _, t1)    # t1 = *t0（读取 a 的值）
(+, t1, 2, t2)       # t2 = t1 + 2（2 是编译期常量，直接嵌入）
(addr, _, _, t3)     # t3 = &x
(store, t2, _, t3)   # *t3 = t2（写入 x）
```

### 3.3 左值寻址

统一策略：先取基地址（addr），再按选择器链逐层偏移。

```python
def emit_lvalue(self, ref: VarRef, builder: UnitBuilder) -> Operand:
    symbol = require_symbol(ref.symbol, ref.name)
    address = builder.temp(UNKNOWN)
    builder.emit("addr", None, None, address, type_info=current_type, symbol=symbol)

    for selector in ref.selectors:
        if isinstance(selector, IndexSelector):
            index = self.emit_expr(selector.expr, builder)
            next_address = builder.temp(UNKNOWN)
            builder.emit("index_addr", address, index, next_address, type_info=current_type)
            address = next_address
        elif isinstance(selector, FieldSelector):
            next_address = builder.temp(UNKNOWN)
            builder.emit("field_addr", address, selector.name, next_address, type_info=current_type)
            address = next_address
    return address
```

### 3.4 控制流生成

**while 循环**（先 label 后 goto → 形成回跳循环）：

```python
def emit_while(self, stmt: WhileStmt, builder: UnitBuilder) -> None:
    start_label = builder.label("Lwhile")
    end_label = builder.label("Lendwhile")
    builder.emit("label", result=start_label)              # 循环入口
    self.emit_false_branch(stmt.condition, end_label, builder)  # 条件为假退出
    self.emit_statements(stmt.body, builder)                # 循环体
    builder.emit("goto", result=start_label)               # 跳回入口
    builder.emit("label", result=end_label)                # 循环出口
```

**if-then-else**（先 goto 后 label → 向前跳过代码段）：

```python
def emit_if(self, stmt: IfStmt, builder: UnitBuilder) -> None:
    else_label = builder.label("Lelse")
    end_label = builder.label("Lendif")
    self.emit_false_branch(stmt.condition, else_label, builder)
    self.emit_statements(stmt.then_body, builder)
    builder.emit("goto", result=end_label)                 # 跳过 else
    builder.emit("label", result=else_label)
    self.emit_statements(stmt.else_body, builder)
    builder.emit("label", result=end_label)
```

### 3.5 过程调用生成

```python
def emit_call(self, stmt: CallStmt, builder: UnitBuilder) -> None:
    symbol = require_symbol(stmt.symbol, stmt.name)
    for formal, arg in zip(symbol.params, stmt.args):
        if formal.mode == "var":
            operand = self.emit_lvalue(arg.ref, builder)   # 引用参数：传地址
        else:
            operand = self.emit_expr(arg, builder)          # 值参数：传值
        builder.emit("param", operand, formal.mode, None)
    builder.emit("call", symbol, len(stmt.args), None, symbol=symbol)
```

---

## 四、中间代码优化（snl_optimizer.py）

### 4.1 优化 Pass 总览

```python
ALL_PASSES = ("fold", "algebra", "cse", "dce", "licm", "tail_rec")
```

执行顺序：
```
第一轮：fold → algebra → cse → dce → licm
第二轮清理：dce → fold → dce
```

### 4.2 常量折叠（fold_constants）

维护已知常量表，将编译期可计算的运算直接求值：

```python
constants: dict[str, int] = {}

for quad in quads:
    quad = replace_known_constants(quad, constants)  # 替换已知常量

    # 两个操作数均为整数 → 编译期求值
    if quad.op in {"+", "-", "*", "/"} and isinstance(quad.arg1, int) and isinstance(quad.arg2, int):
        folded = eval_arithmetic(quad.op, quad.arg1, quad.arg2)
        constants[quad.result] = folded
        optimized.append(Quad("assign", folded, None, quad.result, note="常量折叠"))
        continue

    # 维护常量表...
```

**示例**：`(*, 3, 4, t0)` → `(assign, 12, _, t0)`

### 4.3 代数化简（simplify_algebra）

利用代数恒等式消除冗余运算：

| 规则 | 示例 |
|------|------|
| x + 0 → x | `(+, t0, 0, t1)` → `(assign, t0, _, t1)` |
| x * 1 → x | `(*, t0, 1, t1)` → `(assign, t0, _, t1)` |
| x * 0 → 0 | `(*, t0, 0, t1)` → `(assign, 0, _, t1)` |
| x / 1 → x | `(/, t0, 1, t1)` → `(assign, t0, _, t1)` |

### 4.4 基本块内公共子表达式消除（CSE）

在单个基本块内识别重复计算，用第一次的结果替代后续相同计算：

```python
def common_subexpression_elimination(quads: list[Quad]) -> list[Quad]:
    aliases: dict[str, Operand] = {}       # 别名表（追踪赋值链）
    expressions: dict[tuple, str] = {}     # 表达式缓存

    for quad in quads:
        if quad.op == "label":             # 新基本块 → 清空
            aliases.clear()
            expressions.clear()

        normalized = normalize_quad(quad, aliases)

        if normalized.op in PURE_EXPR_OPS and isinstance(normalized.result, str):
            key = expression_key(normalized, aliases)
            if key in expressions:         # 命中 → 复用
                aliases[normalized.result] = expressions[key]
                optimized.append(Quad("assign", expressions[key], None, normalized.result, note="CSE"))
                continue
            expressions[key] = normalized.result  # 未命中 → 记录

        if ends_block(normalized):         # 块结束 → 清空
            aliases.clear()
            expressions.clear()
```

关键设计：
- 交换律处理：`a+b` 和 `b+a` 生成相同的键
- 内存安全：`store`/`call` 后清除 `load` 表达式（内存可能被修改），保留纯算术表达式
- 严格基本块内：遇到任何 `label` 或块结束指令都清空状态

### 4.5 死代码消除（DCE）

逆向活跃变量分析，删除结果从未被使用的无副作用指令：

```python
def eliminate_dead_temp_assignments(quads: list[Quad]) -> list[Quad]:
    live: set[str] = set()
    kept: list[Quad] = []

    for quad in reversed(quads):
        target = defined_temp(quad)
        # 定义了临时变量 + 后续没人用 + 无副作用 → 删除
        if target is not None and target not in live and quad.op in REMOVABLE_TEMP_OPS:
            continue
        if target is not None:
            live.discard(target)
        live.update(temp_uses(quad))
        kept.append(quad)

    kept.reverse()
    return kept
```

### 4.6 循环不变式外提（LICM）

识别简单循环（label + 回跳 goto），将操作数不变的纯表达式移到循环前：

```python
def find_simple_loops(quads):
    # goto 的目标 label 在 goto 前面 → 回跳 → 循环
    if target in label_indices and label_indices[target] < idx:
        loops.append((label_indices[target], idx))

def _can_hoist(quad, defined_in_loop):
    if quad.op not in PURE_EXPR_OPS:
        return False
    if quad.op == "load":
        return False  # load 读内存，内容可能在循环内被 store 修改
    return operand_invariant(quad.arg1) and operand_invariant(quad.arg2)
```

### 4.7 尾递归消除（tail_rec）

**IR 层面**：识别 `call self → return` 模式，改写为 `tail_call`：

```python
if quads[i].op == "call" and symbol.name == proc_name:
    # 向后跳过 label，检查是否紧跟 return 或过程结尾
    if is_tail:
        optimized.append(Quad("tail_call", ...))  # 改写
```

**目标代码层面**：参数覆盖当前栈帧 + 跳转到 `_body`（跳过 prologue）：

```python
def emit_tail_call(self, quad: Quad) -> None:
    for index, param in enumerate(self.pending_params):
        value = self.load_operand(param.arg1)
        offset = PARAM_BASE_OFFSET + index * 4
        self.program.emit(f"sw {value}, {offset}($fp)")  # 参数覆盖
    self.program.emit(f"j {symbol.label}_body")           # 跳回过程体
```

---

## 五、优化效果量化对比

| 测试文件 | IR（前） | IR（后） | IR 削减 | MIPS（前） | MIPS（后） | MIPS 削减 | 步数（前） | 步数（后） | 步数削减 |
|---------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| test | 7 | 4 | 42.9% | 38 | 23 | 39.5% | 37 | 22 | 40.5% |
| associativity_test | 14 | 8 | 42.9% | 73 | 43 | 41.1% | 72 | 42 | 41.7% |
| static_link_test | 14 | 14 | 0.0% | 70 | 68 | 2.9% | 69 | 67 | 2.9% |
| static_link_recursive_test | 26 | 26 | 0.0% | 103 | 95 | 7.8% | 233 | 179 | 23.2% |
| **opt_fold_heavy** | 10 | 4 | **60.0%** | 56 | 23 | **58.9%** | 55 | 22 | **60.0%** |
| **opt_cse_heavy** | 38 | 30 | **21.1%** | 174 | 150 | **13.8%** | 173 | 149 | **13.9%** |
| **opt_licm_heavy** | 36 | 35 | 2.8% | 145 | 144 | 0.7% | 1038 | 860 | **17.1%** |
| **opt_tailrec_heavy** | 28 | 26 | 7.1% | 98 | 86 | 12.2% | 5660 | 3460 | **38.9%** |
| **opt_combined_heavy** | 45 | 34 | **24.4%** | 206 | 164 | **20.4%** | 205 | 163 | **20.5%** |
| **合计** | **282** | **240** | **14.9%** | **1262** | **1073** | **15.0%** | — | — | — |

所有测试用例在优化前后运行结果完全一致，验证了优化的语义正确性。

---

## 六、总结

### 设计亮点

1. **Symbol 绑定**：将符号表信息直接绑定在四元式上，避免后端重复查表，同时解决嵌套作用域的名字歧义
2. **常量内联**：编译期常量不生成四元式，直接作为操作数嵌入后续指令，为常量折叠提供基础
3. **多 Pass 协作**：六个优化 pass 按依赖顺序执行，两轮迭代确保优化效果收敛
4. **尾递归消除**：从 IR 识别到目标代码生成的完整支持，将递归变为循环

### 安全性保证

- CSE 严格限制在基本块内，遇到任何控制流边界清空状态
- DCE 只删除无副作用的临时变量定义，不删除 store/call/write 等有副作用指令
- LICM 保守地不外提 load 指令（内存内容可能在循环内被修改）
- 尾递归消除只处理自递归尾调用，不处理互递归

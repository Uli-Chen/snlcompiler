# SNL IR 生成器设计文档

本文梳理 `src/snl_irgen.py` 的设计思路和关键实现细节。

## 整体架构

IR 生成器遍历语义分析后的 typed AST，为每条语句和表达式生成四元式（Quadruple）序列。生成的 IR 是线性的三地址码，后续由优化器和代码生成器消费。

核心设计原则：

- **左值统一为地址**：所有变量访问（简单变量、数组元素、记录字段）统一拆成 address-producing 四元式，后端只需理解"地址临时量"
- **过程调用为 param + call 序列**：参数按声明顺序逐个 push，最后一条 call 触发跳转
- **临时变量无限**：不做寄存器分配，由后端负责

## 数据结构

### IRProgram

```python
class IRProgram:
    main: IRUnit           # 主程序的四元式
    globals: list[Symbol]  # 全局变量符号列表
    procedures: list[IRProcedure]  # 过程列表（树形嵌套）
```

### IRProcedure（继承 IRUnit）

```python
class IRProcedure(IRUnit):
    name: str              # 过程名
    symbol: Symbol         # 过程符号
    params: list[Symbol]   # 形参符号列表
    locals: list[Symbol]   # 局部变量符号列表
    end_label: str         # 过程结束标签
    scope_level: int       # 作用域深度
    children: list[IRProcedure]  # 嵌套子过程
```

### IRUnit

```python
class IRUnit:
    quads: list[Quad]              # 四元式序列
    temp_types: dict[str, TypeInfo]  # 临时变量类型表
```

### Quad（四元式）

```python
class Quad:
    op: str          # 操作符
    arg1: Operand    # 第一操作数
    arg2: Operand    # 第二操作数
    result: Operand  # 结果
    type_info        # 类型信息（供后端使用）
    symbol           # 关联符号（addr/call 操作）
    note             # 调试注释
```

## 四元式操作符一览

### 算术运算

| op | arg1 | arg2 | result | 含义 |
|----|------|------|--------|------|
| `+` | left | right | temp | temp = left + right |
| `-` | left | right | temp | temp = left - right |
| `*` | left | right | temp | temp = left * right |
| `/` | left | right | temp | temp = left / right |

### 地址计算

| op | arg1 | arg2 | result | 含义 |
|----|------|------|--------|------|
| `addr` | — | — | temp | temp = &symbol（取变量地址） |
| `index_addr` | base | index | temp | temp = base + (index - low) * elem_size |
| `field_addr` | base | field_name | temp | temp = base + field_offset |

### 内存操作

| op | arg1 | arg2 | result | 含义 |
|----|------|------|--------|------|
| `load` | addr | — | temp | temp = *addr |
| `store` | value | — | addr | *addr = value |

### 控制流

| op | arg1 | arg2 | result | 含义 |
|----|------|------|--------|------|
| `label` | — | — | name | 定义标签 |
| `goto` | — | — | name | 无条件跳转 |
| `if_false_<` | left | right | label | if !(left < right) goto label |
| `if_false_=` | left | right | label | if !(left = right) goto label |

### 过程调用

| op | arg1 | arg2 | result | 含义 |
|----|------|------|--------|------|
| `param` | value | mode | — | 压入参数（mode = "value" 或 "var"） |
| `call` | symbol | argc | — | 调用过程 |
| `tail_call` | params[] | — | label | 尾调用优化（由优化器生成） |
| `return` | value | — | — | 返回 |

### I/O

| op | arg1 | arg2 | result | 含义 |
|----|------|------|--------|------|
| `read` | — | — | addr | 从输入读取值存入 *addr |
| `write` | value | — | — | 输出 value |

## 生成流程

### 入口 generate()

```python
def generate(self) -> IRProgram:
    ir = IRProgram()
    ir.globals = collect_decl_symbols(self.ast.var_decls)
    ir.procedures = [self.emit_procedure(proc) for proc in self.ast.proc_decls]
    self.emit_statements(self.ast.body, UnitBuilder(ir.main))
    return ir
```

### 过程生成 emit_procedure

递归处理嵌套过程，每个过程生成独立的 IRProcedure：

```python
def emit_procedure(self, proc: ProcDecl) -> IRProcedure:
    unit = IRProcedure(name=..., symbol=..., params=..., locals=...)
    unit.children = [self.emit_procedure(child) for child in proc.proc_decls]
    builder = UnitBuilder(unit)
    self.emit_statements(proc.body, builder)
    builder.emit("label", result=unit.end_label)  # 过程结束标签
    return unit
```

### 语句生成 emit_statements

按语句类型分派：

| 语句类型 | 生成策略 |
|---------|---------|
| 赋值 | emit_expr(rhs) → emit_lvalue(lhs) → store |
| 调用 | 逐个 param → call |
| if | emit_false_branch → then → goto end → else label → else → end label |
| while | start label → emit_false_branch → body → goto start → end label |
| read | emit_lvalue → read |
| write | emit_expr → write |
| return | emit_expr → return |

### 表达式生成 emit_expr

返回一个 Operand（整数常量或临时变量名）：

| 表达式类型 | 生成策略 |
|-----------|---------|
| `ConstExpr` | 直接返回整数值 |
| `CharExpr` | 返回 ord(char) |
| `VarExpr` | emit_lvalue → load → 返回临时变量 |
| `BinaryExpr` | emit_expr(left) → emit_expr(right) → 运算 → 返回临时变量 |

### 左值生成 emit_lvalue

统一的地址计算策略：

```python
def emit_lvalue(self, ref: VarRef, builder: UnitBuilder) -> Operand:
    # 1. 取变量基地址
    builder.emit("addr", ..., symbol=symbol)
    
    # 2. 逐个处理选择器
    for selector in ref.selectors:
        if isinstance(selector, IndexSelector):
            builder.emit("index_addr", base, index, new_addr)
        elif isinstance(selector, FieldSelector):
            builder.emit("field_addr", base, field_name, new_addr)
            if selector.index:  # a.f[i] 的情况
                builder.emit("index_addr", ...)
    
    return address
```

这种设计的优势：

- 后端不需要知道变量是全局、局部还是参数
- 数组和记录的嵌套访问自然展开为线性四元式
- 优化器可以对地址计算做 CSE 和 LICM

### 条件分支 emit_false_branch

SNL 的条件只有 `<` 和 `=` 两种比较。生成"条件为假时跳转"的四元式：

```python
def emit_false_branch(self, condition, false_label, builder):
    left = self.emit_expr(condition.left, builder)
    right = self.emit_expr(condition.right, builder)
    builder.emit(f"if_false_{condition.op}", left, right, false_label)
```

### 过程调用 emit_call

```python
def emit_call(self, stmt, builder):
    for formal, arg in zip(symbol.params, stmt.args):
        if formal.mode == "var":
            operand = self.emit_lvalue(arg.ref, builder)  # 传地址
        else:
            operand = self.emit_expr(arg, builder)         # 传值
        builder.emit("param", operand, formal.mode)
    builder.emit("call", symbol, len(stmt.args))
```

var 参数传递地址（emit_lvalue），value 参数传递值（emit_expr）。

## UnitBuilder

管理临时变量和标签的分配：

```python
class UnitBuilder:
    def temp(self, type_info) -> str:   # 分配 "t0", "t1", ...
    def label(self, prefix) -> str:     # 分配 "L0", "Lwhile1", ...
    def emit(self, op, ...) -> Quad:    # 追加四元式到当前 unit
```

临时变量名全局唯一（在同一个 IRUnit 内），类型信息记录在 `unit.temp_types` 中供后端使用。

## 设计特点总结

| 特点 | 实现方式 |
|------|---------|
| 左值统一 | 所有变量访问拆为 addr/index_addr/field_addr |
| 三地址码 | 每条四元式最多一个赋值目标 |
| 无限临时 | 不做寄存器分配，后端负责 |
| 类型标注 | 每条四元式携带 type_info，供后端确定操作宽度 |
| 符号关联 | addr/call 四元式携带 Symbol，供后端确定存储位置 |
| 过程树 | 嵌套过程保持树形结构，支持静态链计算 |
| 条件反转 | 使用 if_false 语义，简化 if/while 的代码模板 |

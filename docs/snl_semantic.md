# SNL 语义分析器设计文档

本文梳理 `src/snl_semantic.py` 的设计思路和关键实现细节。

## 整体架构

单遍 AST 遍历，在语法分析生成的 AST 上进行类型检查和符号解析。分析完成后，AST 节点上被标注了类型信息（`type_info`）和符号引用（`symbol`），供后续 IR 生成直接使用。

三个核心组件：

```
TypeInfo        → 类型表示（integer/char/array/record/bool/procedure）
Symbol          → 符号记录（名称、种类、类型、作用域层级、存储信息）
SymbolTable     → 作用域栈（嵌套作用域的进入/退出/查找）
```

## 类型系统

### TypeInfo 数据结构

```python
@dataclass
class TypeInfo:
    kind: str                          # "integer" | "char" | "bool" | "array" | "record" | "procedure" | "unknown"
    low: int | None = None             # 数组下界
    high: int | None = None            # 数组上界
    element: TypeInfo | None = None    # 数组元素类型
    fields: dict[str, TypeInfo]        # 记录字段类型表
```

预定义的类型单例：

| 常量 | 含义 |
|------|------|
| `INTEGER` | 整数类型 |
| `CHAR` | 字符类型 |
| `BOOL` | 布尔类型（比较表达式的结果） |
| `UNKNOWN` | 未知类型（错误恢复用） |
| `PROCEDURE_TYPE` | 过程类型标记 |

### 类型等价判定 same_type

SNL 使用结构等价（structural equivalence）：

- 基本类型：kind 相同即等价
- 数组类型：上下界相同且元素类型等价
- 记录类型：字段名集合相同且对应字段类型等价
- `unknown` 与任何类型等价（容错，避免级联报错）

### 聚合类型限制

SNL 不支持数组/记录的整体值语义。`is_aggregate()` 检测聚合类型，在以下场景报错：

- 赋值语句的左右两侧是聚合类型
- `write` 的参数是聚合类型
- `read` 的目标是聚合类型
- 过程的 value 参数是聚合类型

## 符号表

### Symbol 数据结构

```python
@dataclass
class Symbol:
    name: str              # 标识符名
    kind: str              # "program" | "type" | "var" | "param" | "procedure"
    type_info: TypeInfo    # 关联类型
    line: int              # 声明行号
    params: list[ParamInfo]  # 过程的形参列表
    mode: str              # 参数传递模式："value" | "var"
    label: str             # 代码生成用的标签
    storage: str           # 存储类别
    offset: int            # 栈帧偏移
    scope_level: int       # 声明所在的作用域深度
    param_symbols: list[Symbol]  # 形参对应的 Symbol 列表
```

### 作用域管理 SymbolTable

```python
class SymbolTable:
    scopes: list[Scope]    # 所有已创建的作用域（平坦存储）
    stack: list[Scope]     # 当前活跃的作用域栈
```

操作：

| 方法 | 行为 |
|------|------|
| `enter(name)` | 创建新作用域并压栈 |
| `leave()` | 弹出当前作用域 |
| `declare(symbol)` | 在当前作用域声明符号，重复则返回已有符号 |
| `lookup(name)` | 从栈顶向下查找，返回最近的匹配 |
| `is_outer_local(name)` | 判断变量是否来自外层过程（非当前、非全局） |

查找策略是**从内向外**遍历作用域栈，实现了 SNL 的词法作用域规则。

### 作用域嵌套示例

```snl
program p              → enter("global")
  var integer x;       →   declare x in global
  procedure foo();     →   declare foo in global
    var integer y;     →   enter("procedure foo"), declare y
    procedure bar();   →     declare bar in foo
      ...              →     enter("procedure bar")
      ...              →     leave()
    begin ... end      →   leave()
  begin ... end        → leave()
```

## 分析流程

### 入口 analyze()

```python
def analyze(self):
    self.table.enter("global")
    self.declare(Symbol(self.program.name, "program", ...))
    self.analyze_declare_part(type_decls, var_decls, proc_decls)
    self.analyze_statements(self.program.body)
    self.table.leave()
```

### 声明分析 analyze_declare_part

处理顺序：类型声明 → 变量声明 → 过程声明。

关键设计：过程声明分两遍处理：

1. **第一遍**：为所有过程创建 Symbol 并注册到当前作用域（支持同级过程互相调用）
2. **第二遍**：逐个进入过程体进行深度分析

```python
for proc in proc_decls:
    symbol = Symbol(proc.name, "procedure", ...)
    self.declare(symbol)          # 第一遍：注册

for proc in proc_decls:
    self.analyze_proc(proc)       # 第二遍：深入分析
```

### 类型解析 resolve_type

将 AST 中的 `TypeNode` 转换为 `TypeInfo`：

| TypeNode.kind | 处理 |
|---------------|------|
| `integer`/`char` | 返回预定义单例 |
| `array` | 递归解析元素类型，检查上下界合法性 |
| `record` | 解析所有字段，检查字段名不重复 |
| `alias` | 查符号表，要求目标是 type 类型的符号 |

### 语句分析 analyze_statements

逐条分析语句，按类型分派：

- **赋值**：检查左值可赋值性、左右类型匹配、禁止聚合赋值
- **调用**：查找过程符号、检查参数数量、逐个检查参数类型和模式
- **条件/循环**：检查条件表达式为 bool 类型，递归分析子语句
- **read**：检查目标可赋值、标量类型
- **write**：检查表达式为标量类型
- **return**：检查表达式类型

### 表达式类型检查 check_expr

返回表达式的类型，同时在节点上标注 `type_info` 和 `const_int`：

| 表达式类型 | 返回类型 | 附加检查 |
|-----------|---------|---------|
| `ConstExpr` | INTEGER | 设置 const_int |
| `CharExpr` | CHAR | — |
| `VarExpr` | 变量的解析类型 | 通过 check_var_ref |
| `BinaryExpr` 算术 | INTEGER | 操作数必须为 integer，检查除零 |
| `BinaryExpr` 比较 | BOOL | 操作数类型必须兼容，禁止聚合比较 |

### 编译期常量折叠 fold_const_int

在语义分析阶段就计算常量表达式的值：

```python
def fold_const_int(op, left, right) -> int | None:
    if left.const_int is None or right.const_int is None:
        return None
    # 执行计算，除法使用向零截断（与 MIPS div 一致）
```

这个值存储在 `Expr.const_int` 字段中，后续被 IR 优化器的常量折叠 pass 利用。

### 变量引用检查 check_var_ref

处理变量访问链 `a`、`a[i]`、`a.f`、`a.f[i]`：

1. 查符号表找到变量的 Symbol
2. 确认是 var 或 param 类型
3. 逐个处理选择器，逐层剥离类型：
   - `IndexSelector`：检查当前类型是 array，返回元素类型
   - `FieldSelector`：检查当前类型是 record，返回字段类型
4. 设置 `ref.assignable`、`ref.symbol`、`ref.type_info`

额外检查：

- 数组下标必须是 integer
- 编译期可确定的下标进行越界检查
- 字段名必须存在于记录类型中

## 过程调用检查

```python
def check_call(self, stmt: CallStmt):
    symbol = self.table.lookup(stmt.name)
    # 1. 检查标识符存在且为 procedure
    # 2. 检查实参数量与形参匹配
    # 3. 逐个检查：
    #    - var 参数要求实参是可赋值变量
    #    - value 参数禁止聚合类型
    #    - 类型匹配
```

## 错误处理策略

- **不中断**：遇到错误记录诊断信息，继续分析后续代码
- **UNKNOWN 容错**：未知类型与任何类型兼容，避免一个错误引发级联报错
- **精确定位**：每条错误包含行号和具体描述
- **重复声明检测**：报告首次声明的位置，方便定位

错误格式：`line N: message`

## 输出

分析完成后，AST 节点上被标注：

| 字段 | 标注位置 | 用途 |
|------|---------|------|
| `Expr.type_info` | 所有表达式 | IR 生成时确定操作数宽度 |
| `Expr.const_int` | 常量表达式 | IR 优化的常量折叠 |
| `VarRef.symbol` | 变量引用 | IR 生成时确定地址 |
| `VarRef.assignable` | 变量引用 | 区分左值和右值 |
| `CallStmt.symbol` | 过程调用 | IR 生成时确定调用目标 |
| `ProcDecl.symbol` | 过程声明 | 代码生成时确定标签和栈帧 |
| `Param.symbol` | 形参 | 代码生成时确定参数位置 |

## 设计特点总结

| 特点 | 实现方式 |
|------|---------|
| 单遍分析 | 一次 AST 遍历完成所有检查和标注 |
| 结构类型等价 | 递归比较类型结构，非名称等价 |
| 词法作用域 | 作用域栈从内向外查找 |
| 前向引用 | 同级过程两遍处理，支持互相调用 |
| 编译期求值 | 常量表达式在语义阶段即计算 |
| 错误容错 | UNKNOWN 类型阻断级联错误 |
| AST 标注 | 分析结果直接写入 AST 节点，无需额外数据结构 |

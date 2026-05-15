# SNL 语义分析器说明书

## 概述

`snl_semantic.py` 实现 SNL 语言的语义分析阶段。它遍历由语法分析器生成的 AST，执行以下核心任务：

1. **符号表管理**：为每个声明的标识符创建 Symbol 条目，管理嵌套作用域
2. **类型检查**：验证表达式和语句中的类型兼容性
3. **名称解析**：将标识符引用绑定到其声明的 Symbol
4. **语义约束验证**：检查重复定义、未声明标识符、参数匹配等

---

## 类定义

### TypeInfo

```python
@dataclass
class TypeInfo:
    kind: str           # "integer" | "char" | "bool" | "array" | "record" | "procedure" | "unknown"
    low: int | None     # array 下界
    high: int | None    # array 上界
    element: TypeInfo | None    # array 元素类型
    fields: dict[str, TypeInfo] # record 字段名→类型映射
```

**设计说明**：
- `TypeInfo` 是运行时类型的统一描述结构，所有类型（基本类型、数组、记录）都用同一个类表示
- 预定义的单例对象 `INTEGER`、`CHAR`、`BOOL`、`UNKNOWN`、`PROCEDURE_TYPE` 避免重复创建
- `UNKNOWN` 类型用于错误恢复：当类型推导失败时返回 UNKNOWN，后续检查遇到 UNKNOWN 不再报错，避免级联错误

### ParamInfo

```python
@dataclass
class ParamInfo:
    name: str           # 形参名
    mode: str           # "value"（值传递）或 "var"（引用传递）
    type_info: TypeInfo # 形参类型
    line: int           # 声明行号
    symbol: Symbol | None  # 分析后绑定的 Symbol
```

**用途**：描述过程的一个形式参数，在过程声明时创建，在调用检查时用于匹配实参。

### Symbol

```python
@dataclass
class Symbol:
    name: str               # 标识符名称
    kind: str               # "var" | "param" | "type" | "procedure" | "program"
    type_info: TypeInfo     # 关联的类型信息
    line: int               # 声明行号
    params: list[ParamInfo] # 过程的形参列表（仅 kind="procedure"）
    mode: str               # 参数传递方式（仅 kind="param"）
    label: str              # 代码生成阶段的汇编标签
    storage: str            # "global" | "local" | "param"
    offset: int             # 栈帧内偏移（字节）
    param_symbols: list[Symbol]  # 形参对应的 Symbol 列表
    scope_level: int        # 声明所在的词法层级
    parent_level: int       # 过程的父词法层级（用于静态链计算）
```

**设计说明**：
- Symbol 同时服务于语义分析和代码生成两个阶段
- `scope_level` 和 `parent_level` 是静态链机制的关键：
  - 普通变量的 `scope_level` = 当前作用域层级
  - 过程的 `scope_level` = 当前层级 + 1（过程体运行在下一层）
  - 过程的 `parent_level` = 当前层级（调用时需要传入父层的活动记录）

### Scope

```python
@dataclass
class Scope:
    number: int         # 全局唯一编号
    name: str           # 作用域名称（如 "global"、"procedure foo"）
    level: int          # 嵌套深度（global=0）
    parent: int | None  # 父作用域编号
    symbols: dict[str, Symbol]  # 本作用域内的符号
```

### SymbolTable

```python
class SymbolTable:
    scopes: list[Scope]  # 所有已创建的作用域
    stack: list[Scope]   # 当前活跃的作用域栈
```

**核心方法**：
- `enter(name)` → 创建并压入新作用域
- `leave()` → 弹出当前作用域
- `declare(symbol)` → 在当前作用域声明符号，返回重复定义（如有）
- `lookup(name)` → 从内向外逐层查找符号

**查找策略**：`lookup` 遍历 `stack`（从栈顶到栈底），实现词法作用域的名称解析。内层同名符号遮蔽外层。

---

## 分析流程

### 入口：`analyze()`

```
1. 进入 global 作用域
2. 声明程序名符号
3. 分析声明部分（类型声明 → 变量声明 → 过程声明）
4. 分析语句体
5. 离开 global 作用域
```

### 声明分析：`analyze_declare_part()`

处理顺序严格为：类型声明 → 变量声明 → 过程声明。

**两遍处理过程声明**：
1. 第一遍：为所有过程创建 Symbol 并声明到当前作用域（支持同层过程互相调用）
2. 第二遍：逐个进入过程体分析

### 过程分析：`analyze_proc()`

```
1. 进入新作用域
2. 声明所有形参为 Symbol
3. 递归分析过程内的声明部分和语句体
4. 离开作用域
```

### 类型解析：`resolve_type()`

将 AST 的 `TypeNode` 转换为运行时 `TypeInfo`：
- `integer` / `char` → 返回预定义单例
- `array` → 递归解析元素类型，检查上下界合法性
- `record` → 解析所有字段，检查字段名唯一性
- `alias` → 查找符号表中的类型定义

### 语句检查

| 语句类型 | 检查内容 |
|---------|---------|
| AssignStmt | 左值可赋值性、左右类型匹配、禁止聚合类型整体赋值 |
| CallStmt | 过程存在性、参数数量匹配、各参数类型匹配、var 参数必须是可赋值变量 |
| IfStmt | 条件必须是 bool 类型 |
| WhileStmt | 条件必须是 bool 类型 |
| ReadStmt | 目标必须是可赋值的标量变量 |
| WriteStmt | 表达式必须是标量类型 |
| ReturnStmt | 检查表达式类型（当前不验证返回类型匹配） |

### 表达式类型推导：`check_expr()`

- `ConstExpr` → INTEGER
- `CharExpr` → CHAR
- `VarExpr` → 通过 `check_var_ref` 解析变量引用的最终类型
- `BinaryExpr`：
  - 算术运算（+、-、*、/）→ 操作数必须是 integer，结果为 INTEGER
  - 关系运算（<、=）→ 操作数类型必须兼容且为标量，结果为 BOOL

### 变量引用检查：`check_var_ref()`

```
1. 查找符号表中的 Symbol
2. 验证 Symbol 是变量或参数
3. 逐个处理选择器链：
   - IndexSelector：检查当前类型是数组，下标是整数，推进到元素类型
   - FieldSelector：检查当前类型是记录，字段存在，推进到字段类型
     - 如果字段后还有 IndexSelector，继续处理数组下标
4. 返回最终类型
```

---

## 类型比较：`same_type()`

采用**结构等价**策略：
- UNKNOWN 与任何类型兼容（返回 True）
- 基本类型按 kind 比较
- 数组类型要求上下界和元素类型都相同
- 记录类型要求字段名集合相同且各字段类型相同

---

## 错误处理策略

- 错误不中断分析，而是收集到 `self.errors` 列表
- 遇到类型错误时返回 `UNKNOWN`，后续检查遇到 UNKNOWN 不再报错
- 这种"错误恢复"策略能在一次分析中报告尽可能多的错误

---

## 已知限制

1. **return 语句不验证返回类型**：过程没有声明返回类型，return 的值实际上被丢弃
2. **不支持聚合类型的值语义**：数组和记录不能整体赋值、不能作为值参数传递
3. **类型别名是透明的**：`type t = integer` 后，t 和 integer 完全等价，没有名义类型区分

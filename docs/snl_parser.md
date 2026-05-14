# SNL 语法分析器详解（从零开始）

## 0. 前言：语法分析器是做什么的？

词法分析器把字符流变成 Token 流，语法分析器（Parser）则把 Token 流变成**抽象语法树（AST）**。

```
Token 流: PROGRAM ID("hello") BEGIN WRITE LPAREN INTC("1") RPAREN END DOT EOF
    │
    ▼  语法分析器（Parser）
    │
    ▼
AST (抽象语法树):
Program("hello")
├── body: [
│   └── WriteStmt
│       └── ConstExpr(1)
└── ]
```

**Token 流是一维的线性序列**，而程序本质上是**树形结构**（语句包含子语句，表达式包含子表达式）。Parser 就是从线性 Token 流中恢复出树形结构的过程。

**为什么叫"语法"分析？** 词法分析关心的是"字符如何组成单词"，语法分析关心的是"单词如何组成句子"。比如 `if x < 5 then ...` —— parser 需要理解 `if` 后面跟的是条件表达式，`then` 后面跟的是语句体，整个结构是一个 if 语句。

---

## 1. SNL 语言的语法全景

在深入了解 parser 之前，先要理解 SNL 语言支持哪些语法结构。

### 程序结构

```
program <name>
  [type 类型定义...]
  [var 变量声明...]
  [procedure 过程声明...]
begin
  语句...
end.
```

### 类型系统

```
基本类型: integer, char
数组:      array [low..high] of integer|char
记录:      record 字段名: 类型; ... end
类型别名:  TYPE 名称 = 类型;
```

### 语句

```
赋值:      变量 := 表达式
过程调用:  过程名(参数, ...)
条件:      if 关系表达式 then 语句... else 语句... fi
循环:      while 关系表达式 do 语句... endwh
输入:      read(变量)
输出:      write(表达式)
返回:      return(表达式)
```

### 表达式

```
关系:      表达式 < 表达式  |  表达式 = 表达式
算术加减:  表达式 + 项  |  表达式 - 项
算术乘除:  项 * 因子  |  项 / 因子
因子:      (表达式)  |  整数常量  |  字符字面量  |  变量
变量引用:  名字 [下标] .字段 [下标]
```

### 过程

```
procedure 名字(参数; 参数; ...);
  [type 类型定义...]
  [var 变量声明...]
  [procedure 过程声明...]
begin
  语句...
end;
```

参数格式：`[var] 类型名 标识符, ...`

---

## 2. AST 节点体系

AST 是 parser 的输出，是一棵完全反映程序结构的树。SNL 的 AST 节点全部定义在 `snl_parser.py` 中，以 `@dataclass` 形式组织，形成一个**继承层次**：

### Program — 程序根节点

```python
@dataclass
class Program:
    name: str
    line: int
    type_decls: list[TypeDecl]
    var_decls: list[VarDecl]
    proc_decls: list[ProcDecl]
    body: list[Stmt]
```

### ProcDecl — 过程声明

```python
@dataclass
class ProcDecl:
    name: str
    line: int
    params: list[Param]
    type_decls: list[TypeDecl]
    var_decls: list[VarDecl]
    proc_decls: list["ProcDecl"]   # 可嵌套
    body: list[Stmt]
    symbol: object | None = None    # 语义阶段填入
```

`Program` 和 `ProcDecl` 结构类似——都有类型/变量/过程声明 + 语句体，这是 SNL 语言的特点（过程内部可以嵌套声明）。

### TypeNode — 类型节点

```python
@dataclass
class TypeNode:
    kind: str                    # "integer" | "char" | "array" | "record" | "alias"
    line: int
    name: str = ""               # 类型别名的名称
    low: int | None = None       # 数组下界
    high: int | None = None      # 数组上界
    element: TypeNode | None = None  # 数组元素类型
    fields: list[FieldDecl] = field(default_factory=list)  # 记录字段
    type_info: object | None = None   # 语义阶段填入
```

### Stmt 继承体系

```python
@dataclass
class Stmt:
    line: int                    # 基类，仅含行号

@dataclass
class AssignStmt(Stmt):          # 赋值
    target: VarRef
    expr: Expr

@dataclass
class CallStmt(Stmt):            # 过程调用
    name: str
    args: list[Expr]
    symbol: object | None = None

@dataclass
class IfStmt(Stmt):              # 条件
    condition: Expr
    then_body: list[Stmt]
    else_body: list[Stmt]

@dataclass
class WhileStmt(Stmt):           # 循环
    condition: Expr
    body: list[Stmt]

@dataclass
class ReadStmt(Stmt):            # 输入
    target: VarRef

@dataclass
class WriteStmt(Stmt):           # 输出
    expr: Expr

@dataclass
class ReturnStmt(Stmt):          # 返回
    expr: Expr
```

### Expr 继承体系

```python
@dataclass
class Expr:
    line: int
    type_info: object | None = None
    const_int: int | None = None

@dataclass
class ConstExpr(Expr):           # 整数常量
    value: int = 0

@dataclass
class CharExpr(Expr):            # 字符常量
    value: str = ""

@dataclass
class VarExpr(Expr):             # 变量引用
    ref: VarRef | None = None

@dataclass
class BinaryExpr(Expr):          # 二元运算
    op: str = ""
    left: Expr | None = None
    right: Expr | None = None
```

### VarRef 与 Selector — 变量引用

```python
@dataclass
class VarRef:
    name: str
    line: int
    selectors: list[Selector] = field(default_factory=list)
    symbol: object | None = None
    type_info: object | None = None
    assignable: bool = False

@dataclass
class IndexSelector:
    expr: Expr                     # 数组下标表达式

@dataclass
class FieldSelector:
    name: str
    line: int
    index: IndexSelector | None = None   # 字段本身是数组时的下标

Selector = IndexSelector | FieldSelector
```

`VarRef` 包含一个变量名和一系列选择器（数组下标 `[i]` 或字段访问 `.f`），形成 `a[i].f[j]` 这样的访问链。

### 关键设计决策

- **所有 AST 节点都是 dataclass**，简洁且可直接存取字段
- **`Stmt` 和 `Expr` 使用继承**，每个具体语句/表达式有自己的字段
- **语义字段初始为空**：`symbol`、`type_info`、`assignable` 等字段先在 parser 中声明但设为 `None`/`False`，等语义分析阶段再填入

---

## 3. 递归下降解析

### 3.1 什么是递归下降？

递归下降解析（Recursive-Descent Parsing）是**手写 parser 最常用的方法**。它的核心思想是：

> **为语法中的每个非终结符（语法成分）编写一个解析函数，函数之间通过相互调用（包括递归调用）来解析整个程序。**

例如，对于这个简化的文法：

```
程序 → PROGRAM ID 声明部分 语句体 DOT
声明部分 → 类型声明 变量声明 过程声明
类型声明 → TYPE (ID = 类型 ;)* | ε
```

对应的解析函数就是：

```python
def parse_program(self):              # 程序 → PROGRAM ID 声明部分 语句体 DOT
    self.expect("PROGRAM")
    name = self.expect("ID")
    self.parse_declare_part()
    self.parse_program_body()
    self.expect("DOT")

def parse_declare_part(self):         # 声明部分 → 类型声明 变量声明 过程声明
    self.parse_type_dec()
    self.parse_var_dec()
    self.parse_proc_dec()
```

每个函数的命名对应语法中的非终结符，函数体按文法的产生式顺序依次调用 `expect()` 消耗 Token 或调用其他解析函数。

### 3.2 Parser 的内部状态

```python
class SNLParser:
    def __init__(self, tokens: list[Token]):
        self.tokens = tokens    # Token 序列
        self.index = 0          # 当前 Token 位置指针
        self.errors: list[str] = []  # 错误信息列表
```

核心状态就三样东西：

- **`tokens`**：词法分析器输出的 Token 数组
- **`index`**：当前处理到的 Token 位置（指向下一个要消耗的 Token）
- **`errors`**：解析过程中发现的错误，不立即抛出异常，而是收集起来统一报告

这种设计（收集错误而非立即抛出）称为**错误容忍**——parser 希望能尽可能多地发现错误，而不是遇到第一个错误就停止。

### 3.3 辅助方法

#### current — 查看当前 Token

```python
@property
def current(self) -> Token:
    return self.tokens[self.index] if self.index < len(self.tokens) else self.tokens[-1]
```

返回当前位置的 Token。如果已经越界（正常不会发生，因为有 EOF 哨兵），则返回最后一个 Token。

#### at(*lex_types) — 判断当前 Token 类型

```python
def at(self, *lex_types: str) -> bool:
    return self.current.lex in lex_types
```

检查当前 Token 是否属于给定的类型集合。例如 `self.at("IF", "WHILE")` 检查当前是否是 if 或 while。

#### advance() — 消耗当前 Token 并前进

```python
def advance(self) -> Token:
    token = self.current
    if self.index < len(self.tokens) - 1:
        self.index += 1
    return token
```

返回当前 Token，然后将指针后移一位（除非已在末尾）。

#### expect(lex_type) — 期望一个特定类型的 Token

```python
def expect(self, lex_type: str) -> Token:
    if self.current.lex == lex_type:
        return self.advance()
    token = self.current
    self.error(f"expected {lex_type}, found {token.display()}")
    return Token(token.line, lex_type)
```

**这是 parser 中最核心的辅助方法**。它做了两件事：
1. 如果当前 Token 类型符合预期，消耗它并返回
2. 如果不符合，记录错误，但**返回一个虚构的 Token**（类型为期望的类型），让解析可以继续

这种"出错了但继续解析"的策略就是**错误恢复**的一种形式——用虚构 Token 填补空缺，让上层解析函数能继续正常工作。

---

## 4. 程序结构的解析

### parse_program — 顶层入口

```python
def parse_program(self) -> Program:
    self.expect("PROGRAM")                    # 程序以 program 开头
    name = self.expect("ID")                  # 程序名
    type_decls, var_decls, proc_decls = self.parse_declare_part()
    body = self.parse_program_body()          # 语句体 BEGIN ... END
    self.expect("DOT")                        # 以 . 结束
    return Program(name.sem, name.line, type_decls, var_decls, proc_decls, body)
```

对应文法：`program <name> [声明部分] begin 语句 end .`

1. 期望 `PROGRAM` 关键字
2. 期望 `ID`，记录程序名
3. 解析声明部分（类型、变量、过程）
4. 解析语句体（`BEGIN ... END`）
5. 期望 `DOT`（句点），SNL 程序的结束标志
6. 组装 `Program` AST 节点并返回

### parse_declare_part — 声明部分

```python
def parse_declare_part(self):
    return self.parse_type_dec(), self.parse_var_dec(), self.parse_proc_dec()
```

声明部分由三部分组成：类型声明、变量声明、过程声明。它们都是"可有可无"（即可空的），所以每个解析函数都会处理"不存在"的情况并返回空列表。

### parse_type_dec — 类型声明

```python
def parse_type_dec(self) -> list[TypeDecl]:
    decls: list[TypeDecl] = []
    if not self.at("TYPE"):           # FIRST 集判断
        return decls
    self.advance()
    while self.at("ID"):
        name = self.advance()
        self.expect("EQ")
        type_node = self.parse_type_name()
        self.expect("SEMI")
        decls.append(TypeDecl(name.sem, type_node, name.line))
    return decls
```

对应文法：`TYPE 名字 = 类型 ; 名字 = 类型 ; ...`

`self.at("TYPE")` 是一个 **FIRST 集判断**——检查当前 Token 是否属于某个非终结符的 FIRST 集来决定如何解析。`TYPE` 是"类型声明块"的 FIRST token。

### parse_var_dec — 变量声明

```python
TYPE_START = {"INTEGER", "CHAR", "ARRAY", "RECORD", "ID"}

def parse_var_dec(self) -> list[VarDecl]:
    decls: list[VarDecl] = []
    if not self.at("VAR"):
        return decls
    self.advance()
    while self.current.lex in TYPE_START:
        type_node = self.parse_type_name()
        names = self.parse_id_list()
        self.expect("SEMI")
        decls.append(VarDecl(type_node, names))
    return decls
```

对应文法：`VAR 类型 名字, 名字, ... ; 类型 名字, 名字, ... ; ...`

`TYPE_START` 定义了哪些 Token 可以作为"类型"的开始：`INTEGER`、`CHAR` 是基本类型；`ARRAY` 是数组；`RECORD` 是记录；`ID` 是类型别名。

### parse_proc_dec — 过程声明

```python
def parse_proc_dec(self) -> list[ProcDecl]:
    procedures: list[ProcDecl] = []
    while self.at("PROCEDURE"):
        procedures.append(self.parse_proc_declaration())
    return procedures
```

### parse_proc_declaration — 单个过程声明

```python
def parse_proc_declaration(self) -> ProcDecl:
    self.expect("PROCEDURE")
    name = self.expect("ID")
    self.expect("LPAREN")
    params = self.parse_param_list()
    self.expect("RPAREN")
    self.expect("SEMI")
    type_decls, var_decls, proc_decls = self.parse_declare_part()  # 可嵌套！
    body = self.parse_program_body()
    return ProcDecl(name.sem, name.line, params, type_decls, var_decls, proc_decls, body)
```

对应文法：`PROCEDURE 名字(参数; 参数; ...); [声明部分] BEGIN 语句 END;`

关键：**过程内部也包含声明部分**。`parse_declare_part()` 在 `parse_proc_declaration()` 中被调用，而 `parse_proc_declaration()` 又在 `parse_declare_part()` 中被调用，形成了**递归调用**：

```
parse_declare_part
  └── parse_proc_dec
       └── parse_proc_declaration
            └── parse_declare_part
                 └── parse_proc_dec
                      └── parse_proc_declaration  ← 再次递归
```

这就对应了 SNL 语言支持**过程嵌套定义**的特性。

### parse_param_list — 参数列表

```python
def parse_param_list(self) -> list[Param]:
    params: list[Param] = []
    if self.at("RPAREN"):              # 空参数列表
        return params
    params.extend(self.parse_param())
    while self.at("SEMI"):             # 分号分隔的参数组
        self.advance()
        params.extend(self.parse_param())
    return params

def parse_param(self) -> list[Param]:
    mode: Literal["value", "var"] = "var" if self.at("VAR") else "value"
    if self.at("VAR"):
        self.advance()
    type_node = self.parse_type_name()
    return [Param(name, mode, type_node, line) for name, line in self.parse_id_list()]
```

对应文法：`[var] 类型名 标识符, ... ; [var] 类型名 标识符, ...`

参数传递有两种模式：
- **值传递（value）**：默认，形参是实参的副本
- **引用传递（var）**：形参是实参的别名，修改形参影响实参

### parse_program_body — 语句体

```python
def parse_program_body(self) -> list[Stmt]:
    self.expect("BEGIN")
    body = self.parse_stm_list({"END"})
    self.expect("END")
    return body
```

---

## 5. 类型系统的解析

### parse_type_name — 类型名称

```python
def parse_type_name(self) -> TypeNode:
    token = self.current
    if self.at("INTEGER"):
        self.advance()
        return TypeNode("integer", token.line)
    if self.at("CHAR"):
        self.advance()
        return TypeNode("char", token.line)
    if self.at("ARRAY"):
        return self.parse_array_type()
    if self.at("RECORD"):
        return self.parse_record_type()
    if self.at("ID"):
        self.advance()
        return TypeNode("alias", token.line, name=token.sem)

    self.error(f"expected type name, found {token.display()}")
    self.recover(TYPE_START | {"SEMI", "RPAREN", "BEGIN", "PROCEDURE", "EOF"})
    return TypeNode("unknown", token.line)
```

根据当前 Token 选择不同的解析路径：

- `INTEGER` → `TypeNode("integer")`
- `CHAR` → `TypeNode("char")`
- `ARRAY` → 数组类型
- `RECORD` → 记录类型
- `ID` → 类型别名
- 其他 → 错误 + 恢复

### parse_array_type — 数组类型

```python
def parse_array_type(self) -> TypeNode:
    start = self.expect("ARRAY")
    self.expect("LMIDPAREN")               # [
    low = self.expect("INTC")              # 下界
    self.expect("UNDERANGE")               # ..
    high = self.expect("INTC")             # 上界
    self.expect("RMIDPAREN")               # ]
    self.expect("OF")
    return TypeNode(
        "array",
        start.line,
        low=self.to_int(low),
        high=self.to_int(high),
        element=self.parse_base_type(),
    )
```

对应文法：`ARRAY [low..high] OF baseType`

`self.to_int()` 将 INTC Token 的 sem 字符串转为整数：

```python
@staticmethod
def to_int(token: Token) -> int | None:
    try:
        return int(token.sem)
    except (TypeError, ValueError):
        return None
```

### parse_record_type — 记录类型

```python
FIELD_TYPE_START = {"INTEGER", "CHAR", "ARRAY"}

def parse_record_type(self) -> TypeNode:
    start = self.expect("RECORD")
    fields: list[FieldDecl] = []
    while self.current.lex in FIELD_TYPE_START:
        field_type = self.parse_array_type() if self.at("ARRAY") else self.parse_base_type()
        names = self.parse_id_list()
        self.expect("SEMI")
        fields.append(FieldDecl(field_type, names))
    self.expect("END")
    return TypeNode("record", start.line, fields=fields)
```

对应文法：`RECORD 字段类型 字段名, ... ; ... END`

注意 `FIELD_TYPE_START` **不包含 `ID`**——记录内部字段的类型不能是类型别名，只能是基本类型或数组。这是 SNL 语言的简化设计。

### parse_id_list — 标识符列表

```python
def parse_id_list(self) -> list[tuple[str, int]]:
    token = self.expect("ID")
    ids = [(token.sem, token.line)]
    while self.at("COMMA"):
        self.advance()
        token = self.expect("ID")
        ids.append((token.sem, token.line))
    return ids
```

将 `x, y, z` 解析为 `[("x", line), ("y", line), ("z", line)]`。

---

## 6. 语句的解析

### stmt 分发器

```python
STMT_START = {"IF", "WHILE", "READ", "WRITE", "RETURN", "ID"}

def parse_stm(self) -> Stmt:
    if self.at("IF"):
        return self.parse_conditional_stm()
    if self.at("WHILE"):
        return self.parse_loop_stm()
    if self.at("READ"):
        return self.parse_input_stm()
    if self.at("WRITE"):
        return self.parse_output_stm()
    if self.at("RETURN"):
        return self.parse_return_stm()
    if self.at("ID"):                # 需要二选一：赋值 vs 过程调用
        name = self.advance()
        if self.at("LPAREN"):
            return self.parse_call_stm_rest(name)     # 过程调用
        return self.parse_assignment_rest(name)       # 赋值

    token = self.advance()
    self.error(f"expected statement, found {token.display()}")
    return ReturnStmt(token.line, ConstExpr(token.line, value=0))
```

根据第一个 Token 分发到不同的语句解析函数。`ID` 的情况需要**二选一**：

```
标识符 := 表达式    → 赋值语句
标识符 (参数, ...)  → 过程调用语句
```

方法：先消耗 ID，然后看下一个 Token——是 `LPAREN` 则为调用，否则为赋值。

### 赋值语句

```python
def parse_assignment_rest(self, name: Token) -> AssignStmt:
    target = self.finish_variable(name)
    self.expect("ASSIGN")                    # :=
    return AssignStmt(name.line, target, self.parse_exp())
```

### 过程调用

```python
def parse_call_stm_rest(self, name: Token) -> CallStmt:
    self.expect("LPAREN")
    args = [] if self.at("RPAREN") else self.parse_act_param_list()
    self.expect("RPAREN")
    return CallStmt(name.line, name.sem, args)
```

### If 语句

```python
def parse_conditional_stm(self) -> IfStmt:
    token = self.expect("IF")
    condition = self.parse_rel_exp()
    self.expect("THEN")
    then_body = self.parse_stm_list({"ELSE"})
    self.expect("ELSE")
    else_body = self.parse_stm_list({"FI"})
    self.expect("FI")
    return IfStmt(token.line, condition, then_body, else_body)
```

对应文法：`IF 关系表达式 THEN 语句... ELSE 语句... FI`

注意 `parse_stm_list` 的 `terminators` 参数——不同的终止符控制语句列表的边界：
- then 分支：遇到 `ELSE` 停止
- else 分支：遇到 `FI` 停止

### While 语句

```python
def parse_loop_stm(self) -> WhileStmt:
    token = self.expect("WHILE")
    condition = self.parse_rel_exp()
    self.expect("DO")
    body = self.parse_stm_list({"ENDWH"})
    self.expect("ENDWH")
    return WhileStmt(token.line, condition, body)
```

对应文法：`WHILE 关系表达式 DO 语句... ENDWH`

### Read / Write / Return 语句

```python
def parse_input_stm(self) -> ReadStmt:
    token = self.expect("READ")
    self.expect("LPAREN")
    name = self.expect("ID")
    self.expect("RPAREN")
    return ReadStmt(token.line, VarRef(name.sem, name.line))

def parse_output_stm(self) -> WriteStmt:
    token = self.expect("WRITE")
    self.expect("LPAREN")
    expr = self.parse_exp()
    self.expect("RPAREN")
    return WriteStmt(token.line, expr)

def parse_return_stm(self) -> ReturnStmt:
    token = self.expect("RETURN")
    self.expect("LPAREN")
    expr = self.parse_exp()
    self.expect("RPAREN")
    return ReturnStmt(token.line, expr)
```

对应文法：
- `READ(变量)`
- `WRITE(表达式)`
- `RETURN(表达式)`

---

## 7. 表达式的解析

表达式解析是递归下降 parser 中最经典的部分，难点在于处理**运算符优先级**。

### 运算符优先级

SNL 的运算符优先级分三层：

| 优先级 | 运算符 | 结合性 | 对应函数 |
|--------|--------|--------|----------|
| 低 | `<` `=`（关系比较） | 左结合 | `parse_rel_exp` |
| 中 | `+` `-`（加减） | 左结合 | `parse_exp` |
| 高 | `*` `/`（乘除） | 左结合 | `parse_term` |
| 最高 | 常量、变量、括号 | — | `parse_factor` |

### 经典递归下降表达式的模式

这种"一层函数对应一层优先级"的模式是递归下降解析中最经典的表达式处理方式：

```
parse_rel_exp:  关系表达式（ <  = ）          ← 最低优先级，最先调用
  └── parse_exp:   加减表达式（ +  - ）
        └── parse_term:   乘除表达式（ *  / ）
              └── parse_factor:  原子表达式（常量、变量、括号）  ← 最高优先级
```

每个函数首先调用下一级优先级更高的解析函数获取左操作数，然后检查是否有当前优先级的运算符，有则继续向右扩展。

```python
CMP_OPS = {"LT": "<", "EQ": "="}
ADD_OPS = {"PLUS": "+", "MINUS": "-"}
MULT_OPS = {"TIMES": "*", "OVER": "/"}

def parse_rel_exp(self) -> Expr:
    left = self.parse_exp()
    if self.current.lex in CMP_OPS:
        op = self.advance()
        right = self.parse_exp()
        return BinaryExpr(op.line, op=CMP_OPS[op.lex], left=left, right=right)
    return left

def parse_exp(self) -> Expr:
    left = self.parse_term()
    if self.current.lex in ADD_OPS:
        op = self.advance()
        right = self.parse_exp()
        return BinaryExpr(op.line, op=ADD_OPS[op.lex], left=left, right=right)
    return left

def parse_term(self) -> Expr:
    left = self.parse_factor()
    if self.current.lex in MULT_OPS:
        op = self.advance()
        right = self.parse_term()
        return BinaryExpr(op.line, op=MULT_OPS[op.lex], left=left, right=right)
    return left
```

### 为什么这种模式能正确处理优先级？

考虑表达式 `a + b * c`：

```
parse_rel_exp
  └── parse_exp
       ├── parse_term → parse_factor → VarExpr(a)   ← 返回 a
       ├── 看到 +，advance()
       └── parse_exp                                  ← 递归！
            ├── parse_term
            │    ├── parse_factor → VarExpr(b)       ← 返回 b
            │    ├── 看到 *，advance()
            │    └── parse_term
            │         └── parse_factor → VarExpr(c)  ← 返回 c
            │    └── BinaryExpr(*, b, c)              ← 先算出 b * c
            └── BinaryExpr(+, a, b*c)                 ← 再算 a + (b*c)
```

因为 `*` 的解析函数 `parse_term` 在 `parse_exp` 中被调用时，`*` 及其右操作数会被 `parse_term` 先"吃掉"，形成了 `a + [b * c]` 的分组——乘法优先级高于加法，正确。

注意 `parse_exp` 的递归调用是 `self.parse_exp()` 而不是 `self.parse_term()`，这实现了**左结合**——`a + b + c` 会解析为 `(a + b) + c`。

### 因子解析

```python
def parse_factor(self) -> Expr:
    if self.at("LPAREN"):
        self.advance()
        expr = self.parse_exp()
        self.expect("RPAREN")
        return expr
    if self.at("INTC"):
        token = self.advance()
        value = self.to_int(token) or 0
        return ConstExpr(token.line, const_int=value, value=value)
    if self.at("CHARC"):
        token = self.advance()
        return CharExpr(token.line, value=token.sem)
    if self.at("ID"):
        token = self.advance()
        return VarExpr(token.line, ref=self.finish_variable(token))

    token = self.advance()
    self.error(f"expected expression factor, found {token.display()}")
    return ConstExpr(token.line, value=0)
```

因子（factor）是表达式中"不可分割"的最小单元：
- `(表达式)` → 用括号显式控制优先级
- `42` → 整数常量
- `'A'` → 字符字面量
- `x`、`a[i]`、`r.f` → 变量引用（含下标/字段选择器）

---

## 8. 变量引用的解析：finish_variable

对于 `a[i].f[j]` 这样的变量引用，需要解析出完整的选择器链。

```python
def finish_variable(self, name: Token) -> VarRef:
    ref = VarRef(name.sem, name.line)
    if self.at("LMIDPAREN"):            # [ → 数组下标
        self.advance()
        ref.selectors.append(IndexSelector(self.parse_exp()))
        self.expect("RMIDPAREN")
    elif self.at("DOT"):                # . → 字段访问
        self.advance()
        field = self.expect("ID")
        selector = FieldSelector(field.sem, field.line)
        if self.at("LMIDPAREN"):        # .字段[下标]
            self.advance()
            selector.index = IndexSelector(self.parse_exp())
            self.expect("RMIDPAREN")
        ref.selectors.append(selector)
    return ref
```

SNL 的设计中，变量引用**只支持一层选择器**。数组的元素必须是基本类型，记录的字段可以是基本类型或数组。所以 `a[i]` 或 `r.f` 足够了，不支持 `a[i].f` 这样的链式多维访问。`r.f[i]` 是通过 `FieldSelector.index` 字段处理的——只支持记录的字段本身是数组的情况。

---

## 9. 语句列表解析与错误恢复

### parse_stm_list — 语句列表

```python
def parse_stm_list(self, terminators: set[str]) -> list[Stmt]:
    statements: list[Stmt] = []
    if self.current.lex in terminators:
        self.error("expected statement before statement-list terminator")
        return statements

    while not self.at("EOF") and self.current.lex not in terminators:
        if self.current.lex in STMT_START:
            statements.append(self.parse_stm())

            if self.at("SEMI"):
                self.advance()
                continue
            if self.current.lex in terminators:
                break
            if self.current.lex in STMT_START:
                self.error("missing SEMI between statements")
                continue
            self.error(f"expected SEMI or terminator, found {self.current.display()}")
            self.recover(STMT_START | terminators | {"SEMI"})
            if self.at("SEMI"):
                self.advance()
            continue

        self.error(f"expected statement, found {self.current.display()}")
        self.recover(STMT_START | terminators | {"SEMI"})
        if self.at("SEMI"):
            self.advance()

    return statements
```

### 错误恢复策略

| 场景 | 检测条件 | 处理方式 |
|------|----------|----------|
| 缺分号 | 当前 Token 是语句开始但不是分号 | 记录"缺少分号"，继续解析 |
| 非法 Token | 当前 Token 不是语句开始也不是终止符 | 调用 `recover()` 跳过直到安全的 Token |
| 空体 | 第一个 Token 就是终止符 | 记录错误但返回空语句列表 |

```python
def recover(self, stop_set: set[str]) -> None:
    while not self.at("EOF") and self.current.lex not in stop_set:
        self.advance()
```

错误恢复的**核心原则**：尽可能多地发现正确代码中的错误，而不是被一个错误卡死。跳过无法识别的 Token，到达安全的恢复点后继续解析。

### Token 格式与加载

```python
def load_tokens(path: Path) -> list[Token]:
    text = path.read_text(encoding="utf-8")
    stripped = text.lstrip()
    if stripped.startswith("["):
        # JSON 格式加载
        ...
    else:
        # 表格文本格式加载
        ...
    if tokens[-1].lex != "EOF":
        tokens.append(Token(tokens[-1].line, "EOF"))
    return tokens
```

支持两种 Token 输入格式：
- **JSON**：由 `snl_lexer.py --json` 生成
- **文本表格**：由 `snl_lexer.py` 默认生成

无论输入格式，最后都确保有一个 EOF Token。

---

## 10. 完整的解析示例

输入 Token 流：`PROGRAM ID("hello") BEGIN WRITE LPAREN INTC("1") RPAREN END DOT EOF`

解析过程：

```
parse_program()
├── expect("PROGRAM")           → 消耗 PROGRAM
├── expect("ID")                 → 消耗 ID("hello"), name="hello"
├── parse_declare_part()
│   ├── parse_type_dec()
│   │   └── at("TYPE")? No      → 返回 []
│   ├── parse_var_dec()
│   │   └── at("VAR")? No       → 返回 []
│   └── parse_proc_dec()
│       └── at("PROCEDURE")? No → 返回 []
├── parse_program_body()
│   ├── expect("BEGIN")         → 消耗 BEGIN
│   └── parse_stm_list({"END"})
│       ├── at("WRITE")
│       │   └── parse_output_stm()
│       │       ├── expect("WRITE")
│       │       ├── expect("LPAREN")
│       │       ├── parse_exp()
│       │       │   └── parse_term()
│       │       │       └── parse_factor()
│       │       │           └── at("INTC") → ConstExpr(1)
│       │       └── expect("RPAREN")
│       ├── at("END") → break
│       └── return [WriteStmt(ConstExpr(1))]
├── expect("DOT")               → 消耗 DOT
└── return Program("hello", ...)
```

输出 AST：

```
Program(name="hello")
├── type_decls: []
├── var_decls: []
├── proc_decls: []
└── body: [
    └── WriteStmt
        └── ConstExpr(value=1)
    ]
```

---

## 11. 总结

| 概念 | 实现方式 |
|------|----------|
| **解析方法** | 手写递归下降（Recursive-Descent），LL(k) 风格 |
| **AST 节点** | `@dataclass` 继承体系：`Stmt`/`Expr`/`TypeNode`/`VarRef`/`Selector` |
| **前瞻** | 大部分 LL(1)（1 个 Token 前瞻），赋值 vs 调用需 2 个 Token |
| **运算符优先级** | 分层函数调用：`parse_rel_exp` → `parse_exp` → `parse_term` → `parse_factor` |
| **错误恢复** | 错误容忍收集（不抛异常）+ 跳过 Token 到安全位置 + 用虚构 Token 填补 |
| **嵌套过程** | `parse_declare_part` ↔ `parse_proc_declaration` 相互递归调用 |
| **FIRST 集** | 用常量集合（`TYPE_START`、`STMT_START`、`FIELD_TYPE_START`）判断分支 |
| **分号处理** | 语句之间的分号由 `parse_stm_list` 管理，而不是在 `parse_stm` 中消耗 |
| **模块独立** | 有独立 `main()`，可作为独立工具运行 |

# SNL 语法分析器设计文档

本文梳理 `src/snl_parser.py` 的设计思路和关键实现细节。

## 整体架构

递归下降分析器，手写而非工具生成。每个非终结符对应一个 `parse_*` 方法，方法之间的调用关系直接映射 SNL 的 BNF 文法。

核心设计选择：

- **LL(1) + 有限前看**：大部分产生式通过当前 token 的 lex 类型即可选择。唯一需要 `peek(1)` 的地方是区分变量声明中的类型别名（`ID ID`）和语句中的赋值/调用（`ID :=` / `ID (`）。
- **直接构建 typed AST**：不生成 CST 再转换，解析过程中直接构造 `Program`、`ProcDecl`、`Stmt`、`Expr` 等节点。
- **错误恢复不中断**：遇到语法错误时记录诊断信息，跳过 token 到安全同步点，继续解析后续内容。

## AST 节点体系

```
Program
├── TypeDecl[]          类型声明
├── VarDecl[]           变量声明
├── ProcDecl[]          过程声明（递归嵌套）
│   ├── Param[]         形参列表
│   ├── TypeDecl[]      局部类型声明
│   ├── VarDecl[]       局部变量声明
│   ├── ProcDecl[]      嵌套过程
│   └── Stmt[]          过程体
└── Stmt[]              主程序体
```

语句节点：

| 节点类型 | 对应语法 |
|---------|---------|
| `AssignStmt` | `ID VariMore := Exp` |
| `CallStmt` | `ID ( ActParamList )` |
| `IfStmt` | `IF RelExp THEN StmList ELSE StmList FI` |
| `WhileStmt` | `WHILE RelExp DO StmList ENDWH` |
| `ReadStmt` | `READ ( ID )` |
| `WriteStmt` | `WRITE ( Exp )` |
| `ReturnStmt` | `RETURN ( Exp )` |

表达式节点：

| 节点类型 | 说明 |
|---------|------|
| `ConstExpr` | 整数字面量 |
| `CharExpr` | 字符字面量 |
| `VarExpr` | 变量引用（含数组下标、记录字段选择） |
| `BinaryExpr` | 二元运算（算术 + 比较） |

## 核心解析机制

### Token 流操作

```python
current     → 当前 token（不消费）
at(*types)  → 判断当前 token 是否属于给定类型集
peek(n)     → 前看第 n 个 token
advance()   → 消费当前 token 并返回
expect(lex) → 期望并消费指定类型，失败则报错并尝试恢复
```

### expect 的错误恢复

`expect` 是错误恢复的核心入口：

```python
def expect(self, lex_type, *, context="", recover_to=None) -> Token:
    if self.current.lex == lex_type:
        return self.advance()
    # 报错
    self.error_at(token.line, "missing-token", detail)
    # 尝试恢复：跳到期望 token 或同步集
    if recover_to is not None:
        self.recover({lex_type} | recover_to)
        if self.current.lex == lex_type:
            return self.advance()
    # 返回虚拟 token，让解析继续
    return Token(token.line, lex_type)
```

关键设计：

1. **context 参数**：提供人类可读的错误上下文，如 `"after procedure name 'foo'"`
2. **recover_to 集合**：指定同步点，不同调用位置传入不同的安全 token 集
3. **虚拟 token 返回**：即使缺失也返回一个合法结构的 Token，后续代码不需要处理 None

### recover 方法

```python
def recover(self, stop_set: set[str]) -> None:
    while not self.at("EOF") and self.current.lex not in stop_set:
        self.advance()
```

简单的 panic-mode 恢复：跳过 token 直到遇到同步集中的元素或 EOF。

## 表达式解析

表达式按优先级分层，每层一个方法：

```
parse_rel_exp()   → 比较表达式（最低优先级）
  parse_exp()     → 加减表达式
    parse_term()  → 乘除表达式
      parse_factor() → 原子：括号、整数、字符、变量
```

左结合通过 while 循环实现：

```python
def parse_exp(self) -> Expr:
    left = self.parse_term()
    while self.current.lex in ADD_OPS:
        op = self.advance()
        right = self.parse_term()
        left = BinaryExpr(op.line, op=ADD_OPS[op.lex], left=left, right=right)
    return left
```

### 表达式错误恢复

表达式解析中的错误恢复比较特殊——不能简单跳过，否则会吞掉后续语句。策略：

- 运算符后缺操作数：插入默认值（`+` 后插 `0`，`*` 后插 `1`），保证 AST 结构完整
- 遇到 EXPR_FOLLOW 集合中的 token：说明表达式已结束，返回默认 `ConstExpr(0)`
- 其他非法 token：报错，恢复到 EXPR_FOLLOW 或运算符

## 变量引用解析

`finish_variable` 处理变量后的选择器链：

```python
def finish_variable(self, name: Token) -> VarRef:
    ref = VarRef(name.sem, name.line)
    if self.at("LMIDPAREN"):       # a[expr]
        ...
    elif self.at("DOT"):           # a.field 或 a.field[expr]
        ...
    return ref
```

SNL 的变量访问最多两层：`a[i]`、`a.f`、`a.f[i]`。不支持链式选择（如 `a.f.g`），这与语言规范一致。

## 语句列表解析

`parse_stm_list` 是最复杂的控制逻辑，需要处理：

1. 语句之间的分号分隔
2. 缺少分号时的诊断和恢复
3. 空语句列表的检测
4. 终止符集合的参数化（`END`/`ELSE`/`FI`/`ENDWH` 等）

```python
def parse_stm_list(self, terminators: set[str]) -> list[Stmt]:
    while not self.at("EOF") and self.current.lex not in terminators:
        if self.current.lex in STMT_START:
            statements.append(self.parse_stm())
            if self.at("SEMI"):
                self.advance()
            elif self.current.lex in STMT_START:
                self.error("expected ; between statements")
            ...
```

## ID 开头的歧义消解

SNL 中 `ID` 开头可能是赋值语句或过程调用。消解方式：

```python
def parse_stm(self) -> Stmt:
    if self.at("ID"):
        name = self.advance()
        if self.at("LPAREN"):
            return self.parse_call_stm_rest(name)  # 过程调用
        return self.parse_assignment_rest(name)     # 赋值
```

先消费 `ID`，再看下一个 token 是 `(` 还是其他（`:=`、`[`、`.`）。这是 LL(1) 无法直接处理的，通过提取公共前缀解决。

## 错误诊断格式

所有错误统一格式：`line N: kind: message`

错误类型：

| kind | 含义 |
|------|------|
| `missing-token` | 期望的 token 缺失 |
| `missing-type` | 期望类型名 |
| `missing-declaration` | 期望声明 |
| `missing-statement` | 期望语句 |
| `missing-expression` | 期望表达式 |
| `missing-operator` | 期望运算符 |
| `unexpected-token` | 出现在不该出现的位置 |
| `syntax` | 通用语法错误 |

## 设计特点总结

| 特点 | 实现方式 |
|------|---------|
| 递归下降 | 每个非终结符一个方法，调用关系映射文法 |
| 直接构建 AST | 不经过 CST，解析即构造 |
| 错误恢复 | panic-mode + 虚拟 token + 同步集参数化 |
| 优先级处理 | 分层递归（rel > add > mul > factor） |
| 歧义消解 | 提取公共前缀 + 前看一个 token |
| 诊断质量 | context 参数提供精确错误位置和上下文 |
| 输入灵活 | 支持 JSON 和文本两种 token 文件格式 |

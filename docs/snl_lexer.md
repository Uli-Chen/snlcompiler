# SNL 词法分析器详解（从零开始）

## 0. 前言：词法分析器是做什么的？

词法分析器（Lexer / Scanner / Tokenizer）是编译器的**第一个阶段**。它的输入是一串**字符流**（源代码文本），输出是一个 **Token 序列**（词法单元序列）。每个 Token 是一个有结构的小数据块，代表源代码中的一个有意义的最小单位。

举例说明。编译器前端看到的原始字符串是这样的：

```
x := 42 + y
```

这其实是一个字符数组：`['x', ' ', ':', '=', ' ', '4', '2', ' ', '+', ' ', 'y']`。人眼能看出这是一个赋值语句，但计算机需要把字符**分组分类**后才能理解。词法分析器把它变成：

```
行号    类别       语义值
─────────────────────────
1       ID         x
1       ASSIGN
1       INTC       42
1       PLUS
1       ID         y
```

对比字符流和 Token 流：

- **字符流**：`'x' ':' '=' ' ' '4' '2' ' ' '+' ' ' 'y'` —— 杂乱无章
- **Token 流**：`ID(x)` `ASSIGN` `INTC(42)` `PLUS` `ID(y)` —— 结构化，有意义

词法分析器做了两件事：
1. **分组**：把相邻字符组合成词素（lexeme），例如把 `42` 组合成一个整数常量而不是当作两个字符。
2. **分类**：给每个词素贴上类别标签，例如 `x` 是标识符（ID），`:=` 是赋值符号（ASSIGN）。

---

## 1. Token：词法分析器的输出单位

Token 是词法分析器的产物，也是编译器中流通的最基础数据单元。在 SNL 中，Token 是一个不可变的数据类（`snl_lexer.py:22-27`）：

```python
@dataclass(frozen=True)
class Token:
    line_show: int    # 行号
    lex: str           # 词法类型（如 "ID", "INTC", "ASSIGN", "IF"）
    sem: str = ""      # 语义值（如标识符名字 "x"，整数字面量 "42"）
```

### 三个字段的含义

- **`line_show`**：记录该 Token 出现在源码的第几行（从 1 开始）。这个信息是为了后续阶段（语法分析、语义分析、代码生成）报错时，能告诉用户错误发生在哪一行。

- **`lex`**：Token 的类别标签，用大写字符串表示。所有标识符（不管叫 `x`、`y` 还是 `counter`）的 lex 都是 `"ID"`；所有整数常量（不管是 `0`、`42` 还是 `1024`）的 lex 都是 `"INTC"`。lex 用于后续阶段做模式匹配，比如语法分析器看到 `IF` 就知道这是一个 if 语句的开始。

- **`sem`**：Token 的附加信息（语义值），大部分时候是空串。只有标识符、整数常量、字符字面量这类需要保留原始值的 Token 才填充它：
  - `ID` → sem 存变量名，如 `"x"`、`"counter"`
  - `INTC` → sem 存数字字符串，如 `"42"`、`"0"`
  - `CHARC` → sem 存字符值（去掉引号），如 `"A"`、`"+"`
  - 关键字和符号的 sem 始终为空

### 为什么设计为不可变？

`@dataclass(frozen=True)` 意味着 Token 一旦创建就不能修改。这带来两个好处：
- **安全性**：Token 在词法分析→语法分析→语义分析→代码生成的多阶段传递过程中，不会被意外修改。
- **可哈希**：不可变对象可以放入集合或作为字典键（虽然当前实现未使用这个特性）。

---

## 2. 可配置的词法文法（grammar.txt）

SNL 词法分析器有一个独特的设计：**词法规则不是硬编码在代码里，而是通过 `grammar.txt` 文件配置的**。

### grammar.txt 格式

文件位于 `src/grammar.txt`，定义了 SNL 语言的完整词法规范：

```
# 1. 关键字 —— 保留字，有特殊语法意义
KEYWORD program PROGRAM
KEYWORD procedure PROCEDURE
KEYWORD type TYPE
KEYWORD var VAR
KEYWORD if IF
KEYWORD then THEN
KEYWORD else ELSE
KEYWORD fi FI
KEYWORD while WHILE
KEYWORD do DO
KEYWORD endwh ENDWH
KEYWORD begin BEGIN
KEYWORD end END
KEYWORD read READ
KEYWORD write WRITE
KEYWORD array ARRAY
KEYWORD of OF
KEYWORD record RECORD
KEYWORD return RETURN
KEYWORD integer INTEGER
KEYWORD char CHAR

# 2. 符号 —— 运算符和分隔符
SYMBOL := ASSIGN
SYMBOL .. UNDERANGE
SYMBOL + PLUS
SYMBOL - MINUS
SYMBOL * TIMES
SYMBOL / OVER
SYMBOL < LT
SYMBOL = EQ
SYMBOL ( LPAREN
SYMBOL ) RPAREN
SYMBOL [ LMIDPAREN
SYMBOL ] RMIDPAREN
SYMBOL ; SEMI
SYMBOL , COMMA
SYMBOL . DOT

# 3. 注释定界符
COMMENT { }

# 4. 正则表达式（有默认值，可覆盖）
IDENTIFIER [A-Za-z][A-Za-z0-9]*
INTEGER [0-9]+
CHAR_LITERAL '[^'\n]'
```

每行的格式为 `KIND 参数1 参数2`：
- `KIND` = `KEYWORD` / `SYMBOL` / `COMMENT` / `IDENTIFIER` / `INTEGER` / `CHAR_LITERAL`
- 参数数量依 KIND 而定

### 为什么关键字要放在 grammar.txt 而不是硬编码？

- **关注点分离**：词法规则由配置文件管理，代码只关心扫描逻辑。如果语言新增关键字（如 `const`），只需在 grammar.txt 加一行，无需改动代码。
- **可扩展性**：假如要做一个 SNL 方言，只需要换一个 grammar.txt 即可。

### grammar.txt 的解析过程

`load_grammar()` 函数（`snl_lexer.py:43-88`）逐行解析 grammar.txt：

```python
def load_grammar(path: Path) -> LexicalGrammar:
    keywords: dict[str, str] = {}
    symbols: list[tuple[str, str]] = []
    comments: list[tuple[str, str]] = []
    identifier_pattern = r"[A-Za-z][A-Za-z0-9]*"     # 默认值
    integer_pattern = r"[0-9]+"                       # 默认值
    char_literal_pattern = r"'[^'\n]'"                # 默认值

    for line_no, raw_line in enumerate(path.read_text().splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue                                  # 跳过空行和注释

        parts = line.split(maxsplit=2)                # 最多拆三部分
        kind = parts[0].upper()

        if kind in {"KEYWORD", "SYMBOL"}:
            source, lex_type = parts[1], parts[2]
            if kind == "KEYWORD":
                keywords[source.lower()] = lex_type   # 关键字存小写
            else:
                symbols.append((source, lex_type))    # 符号保持原样
        elif kind == "COMMENT":
            comments.append((parts[1], parts[2]))
        elif kind == "IDENTIFIER":
            identifier_pattern = parts[1]
        elif kind == "INTEGER":
            integer_pattern = parts[1]
        elif kind == "CHAR_LITERAL":
            char_literal_pattern = parts[1]
        else:
            raise LexerError(...)

    # 符号按长度降序排序（关键！）
    symbols.sort(key=lambda item: len(item[0]), reverse=True)
    return LexicalGrammar(keywords, symbols, comments,
                          re.compile(identifier_pattern),
                          re.compile(integer_pattern),
                          re.compile(char_literal_pattern))
```

这里有一个关键的细节：**关键字存为小写**，符号排序后返回。

---

## 3. LexicalGrammar：内存中的规则表示

grammar.txt 被解析后，存入 `LexicalGrammar` 数据类（`snl_lexer.py:29-37`）：

```python
@dataclass
class LexicalGrammar:
    keywords: dict[str, str]            # {"program": "PROGRAM", "if": "IF", ...}
    symbols: list[tuple[str, str]]      # [(":=", "ASSIGN"), ("..", "UNDERANGE"), ...]
    comments: list[tuple[str, str]]     # [("{", "}")]
    identifier_re: re.Pattern[str]      # /[A-Za-z][A-Za-z0-9]*/
    integer_re: re.Pattern[str]         # /[0-9]+/
    char_literal_re: re.Pattern[str]    # /'[^'\n]'/
```

### 各字段的存储选型

| 字段 | 容器类型 | 原因 |
|------|----------|------|
| `keywords` | `dict[str,str]` | 标识符匹配后需要 O(1) 查表区分是否为关键字 |
| `symbols` | `list[tuple]` | 符号存在前缀重叠（如 `:=` 与 `.`、`..`），必须按长度降序线性遍历保证最长匹配 |
| `comments` | `list[tuple]` | 可扩展多种注释风格，一对一的起止串 |
| `identifier_re` | 编译后正则 | 标识符和关键字共用同一正则，匹配后查 keywords 表区分 |
| `integer_re` | 编译后正则 | 纯数字序列 |
| `char_literal_re` | 编译后正则 | 单引号括住恰好一个非引号非换行字符 |

### 关键字与标识符：为何共用正则

关键字（`program`、`if`、`while` 等）**本身就是合法的标识符**。它们都满足正则 `[A-Za-z][A-Za-z0-9]*`——以字母开头，后跟零个或多个字母数字。

做法是：
1. 用 `identifier_re` 匹配出词素（比如匹配到了 `"if"`）
2. 查 `keywords` 字典：`keywords.get("if")` → 返回 `"IF"`，说明是关键字
3. 如果查不到，就是普通标识符

**优点是避免了为每个关键字写独立正则**，否则需要 20 多条正则逐一尝试，效率低且冗余。

### 符号排序：为什么必须按长度降序

这是因为符号之间存在**前缀重叠**。SNL 中有三对重叠符号：

| 较长符号 | 较短符号 | 重叠关系 |
|----------|----------|----------|
| `:=` (ASSIGN) | — | 不存在 `:` 单字符符号，问题不大 |
| `..` (UNDERANGE) | `.` (DOT) | `..` 以 `.` 开头 |

如果不排序，假设 `..` 排在 `.` 后面，遇到输入 `1..10` 时：

1. 当前位置匹配 `.` → 匹配成功！产出 DOT Token
2. 剩下 `.10`，再次匹配 `.` → 又产出 DOT Token
3. 剩下 `10` → 匹配 INTC

结果：`1 . . 10` 被拆成 `INTC(1)` `DOT` `DOT` `INTC(10)` —— **错误！** 本意是范围 `1..10`。

按长度降序后：

1. 当前位置匹配 `..`（因为 `..` 比 `.` 长，排在前面）→ 匹配成功！产出 UNDERANGE Token
2. 剩下 `1 .. 10` 变成 `INTC(1)` `UNDERANGE` `INTC(10)` —— **正确！**

这就是**最长匹配（贪心匹配）原则**在词法分析中的应用。排序在 `load_grammar()` 末尾完成：

```python
symbols.sort(key=lambda item: len(item[0]), reverse=True)
```

---

## 4. SNLLexer 的核心扫描循环

这是词法分析器的执行引擎（`snl_lexer.py:91-160`）。它是一个**基于状态的手写有限自动机**，在源代码字符串上维护一个指针 `i`，逐个位置尝试匹配。

### 主循环结构

```python
class SNLLexer:
    def __init__(self, grammar: LexicalGrammar):
        self.grammar = grammar

    def tokenize(self, source: str, include_eof: bool = False) -> list[Token]:
        tokens: list[Token] = []
        i = 0                        # 当前扫描位置（字符索引）
        line = 1                     # 当前行号
        length = len(source)

        while i < length:            # 主循环
            ch = source[i]

            # 第1步：跳过空白符
            if ch.isspace():
                if ch == "\n":
                    line += 1        # 换行时维护行号
                i += 1
                continue             # 空白不产生 Token

            # 第2步：检查是否为注释开始
            comment_match = self._match_comment_start(source, i)
            if comment_match:
                ...                  # 跳过整个注释
                continue

            # 第3步：尝试匹配标识符/关键字
            identifier = self._match(self.grammar.identifier_re, source, i)
            if identifier:
                lex_type = self.grammar.keywords.get(identifier.lower())
                if lex_type is None:
                    tokens.append(Token(line, "ID", identifier))
                else:
                    tokens.append(Token(line, lex_type))
                i += len(identifier)
                continue

            # 第4步：尝试匹配整数常量
            integer = self._match(self.grammar.integer_re, source, i)
            if integer:
                tokens.append(Token(line, "INTC", integer))
                i += len(integer)
                continue

            # 第5步：尝试匹配字符字面量
            if ch == "'":
                char_token, consumed = self._scan_char_literal(source, i, line)
                tokens.append(char_token)
                i += consumed
                continue

            # 第6步：尝试匹配符号
            symbol = self._match_symbol(source, i)
            if symbol:
                lexeme, lex_type = symbol
                tokens.append(Token(line, lex_type))
                i += len(lexeme)
                continue

            # 第7步：以上都不匹配 → 词法错误
            tokens.append(Token(line, "ERROR", ch))
            i += 1

        if include_eof:
            tokens.append(Token(line, "EOF"))

        return tokens
```

### 为什么是这个匹配顺序？

这是一个精心设计的 if-elif 链，匹配优先级至关重要：

| 优先级 | 匹配项 | 原因 |
|--------|--------|------|
| **1** | 空白符 | 不产生 Token，只维护行号。必须最先处理，否则空格会被当作非法字符报错 |
| **2** | 注释 | 注释内部可包含任何字符（包括看似标识符、数字的东西），必须整体跳过 |
| **3** | 标识符/关键字 | 在整数之前。标识符必须以字母开头，不会与整数冲突 |
| **4** | 整数常量 | 纯数字序列 |
| **5** | 字符字面量 | 以 `'` 开头，必须在符号之前，否则 `'` 本身可能被匹配为未知符号 |
| **6** | 符号 | 固定字符串匹配，兜底 |
| **7** | 错误 | 所有规则都不匹配，产生 ERROR Token，前进一个字符确保不死锁 |

### 逐步骤详解

#### 步骤 1：空白符处理

```python
if ch.isspace():
    if ch == "\n":
        line += 1
    i += 1
    continue
```

空白符（空格、制表符、换行符）**不产生 Token**，只是扫描器的"润滑剂"。唯一要做的是一行行号计数，其他什么也不做，继续扫描。

#### 步骤 2：注释处理

```python
comment_match = self._match_comment_start(source, i)
if comment_match:
    begin, end = comment_match
    start_line = line
    i += len(begin)              # 跳过注释开始定界符
    while i < length and not source.startswith(end, i):
        if source[i] == "\n":
            line += 1            # 注释内的换行也要计数
        i += 1
    if i >= length:              # 到了文件尾还没找到结束定界符
        tokens.append(Token(start_line, "ERROR", f"unclosed comment starts with {begin!r}"))
        break                    # 未闭合注释 → 终止扫描
    i += len(end)               # 跳过注释结束定界符
    continue
```

注释处理有三个要点：

- **注释内的所有内容都被忽略**，注释不产生任何 Token。这是词法分析的语义——注释是给程序员看的，编译器不需要理解。
- **注释内的换行需要计数**，否则注释跨行后，注释后面的代码会报错在错误的行号上。
- **未闭合注释是一个致命错误**，直接 `break` 终止扫描。为什么不像其他错误那样继续？因为如果注释未闭合，后面的所有代码都可能被错误地当作注释内容吞噬掉，继续扫描只会产生大量无意义的错误消息。所以不如立即停止。

#### 步骤 3：标识符/关键字识别

```python
identifier = self._match(self.grammar.identifier_re, source, i)
if identifier:
    lex_type = self.grammar.keywords.get(identifier.lower())  # 查关键字表
    if lex_type is None:
        tokens.append(Token(line, "ID", identifier))          # 普通标识符
    else:
        tokens.append(Token(line, lex_type))                  # 关键字
    i += len(identifier)
    continue
```

这是词法分析中最巧妙的一个设计。**标识符和关键字共用同一个匹配路径**：

1. 先用 `identifier_re` 正则匹配出词素（如 `"if"`、`"myVar"`、`"program"`）
2. 将词素**转为小写**后查 `keywords` 字典
3. 查到 → 是关键字，lex 设为对应的类型名，sem 留空
4. 查不到 → 是普通标识符，lex 设为 `"ID"`，sem 存原始名称

注意 `identifier.lower()` 和 `self.grammar.keywords` 中存的是小写形式（`"program" → "PROGRAM"`），这样 **关键字是大小写不敏感的**，`Program`、`PROGRAM`、`program` 都会被识别为关键字。但标识符的 sem 保留原始大小写，所以 `MyVar` 和 `myvar` 是不同的标识符。

#### 步骤 4：整数常量识别

```python
integer = self._match(self.grammar.integer_re, source, i)
if integer:
    tokens.append(Token(line, "INTC", integer))
    i += len(integer)
    continue
```

整数正则 `[0-9]+` 匹配一个或多个数字。所有整数常量统一标记为 `"INTC"`（INTeger Constant），语义值存数字字符串。**注意语义值是字符串 `"42"` 而不是整数 `42`**，真正的类型转换在语义分析或代码生成阶段完成。

为什么标识符匹配在整数之前？因为标识符必须以字母开头，而整数以数字开头，两者没有重叠。如果在标识符之前匹配整数，`x42` 这种变量名不会被干扰（标识符仍然匹配整个 `x42`），但反过来，如果整数在标识符之前，输入 `42x`（不合法的标识符）会被识别为 `INTC(42)` + `ID(x)`——虽然仍然不对，但至少不会把 `42` 吞掉。当前顺序在合法代码上没有区别。

#### 步骤 5：字符字面量识别

```python
if ch == "'":
    char_token, consumed = self._scan_char_literal(source, i, line)
    tokens.append(char_token)
    i += consumed
    continue
```

单引号 `'` 是字符字面量的触发条件。这里没有用正则去"试"（不像标识符和整数），而是先用 `ch == "'"` 做 O(1) 的快速判断，只有命中后才进入更复杂的 `_scan_char_literal` 逻辑。这是一种**两级过滤**的优化思路。

#### 步骤 6：符号匹配

```python
symbol = self._match_symbol(source, i)
if symbol:
    lexeme, lex_type = symbol
    tokens.append(Token(line, lex_type))
    i += len(lexeme)
    continue
```

按长度降序遍历 `symbols` 列表，用 `source.startswith(lexeme, i)` 检查当前位置是否以该符号开头。由于已排序，`..` 会在 `.` 之前尝试，保证最长匹配。

#### 步骤 7：错误处理

```python
tokens.append(Token(line, "ERROR", ch))
i += 1
```

如果以上所有规则都不匹配，说明遇到了词法分析器无法识别的字符。产生一个 `ERROR` Token（lex=`"ERROR"`，sem=非法字符本身），然后**前进一个字符继续扫描**。这种"吞一个字符继续"的策略保证了扫描器永远不会陷入死循环，且不会因为一个错误而丢失后续所有 Token。

### 参数 include_eof

```python
def tokenize(self, source: str, include_eof: bool = False) -> list[Token]:
```

当 `include_eof=True` 时，在 Token 序列末尾追加一个哨兵 Token：`Token(line, "EOF")`。这个哨兵供语法分析器使用——递归下降解析器需要知道源代码在何处结束，以便检测程序是否正常收尾（例如检查程序末尾是否有多余的 Token）。

---

## 5. 四个辅助匹配函数详解

### 5.1 `_match(pattern, source, index)` — 通用正则匹配

```python
@staticmethod
def _match(pattern: re.Pattern[str], source: str, index: int) -> str:
    match = pattern.match(source, index)
    return match.group(0) if match else ""
```

行为：
- 使用 `re.match(source, index)` 从指定位置尝试匹配（不是 `re.search`——search 会在整个字符串中搜索，而我们需要精确控制扫描位置）
- 匹配成功返回匹配到的字符串，失败返回空字符串 `""`
- 返回 `""` 而非 `None`，因为空串在布尔上下文中为 False，可以直接作条件判断

标注 `@staticmethod` 是因为这个函数不访问 `self` 的任何属性——它只依赖于传入的参数，是一个纯函数。

### 5.2 `_match_comment_start(source, index)` — 注释入口检测

```python
def _match_comment_start(self, source: str, index: int):
    for begin, end in self.grammar.comments:
        if source.startswith(begin, index):
            return begin, end
    return None
```

只做一件事：检查当前位置是否匹配某个注释的开始定界符。匹配成功返回 `(开始串, 结束串)` 对，匹配失败返回 `None`。

这个函数**只负责识别注释的入口**，注释内容的消耗和换行计数由主循环负责。职责分离，各司其职。

### 5.3 `_match_symbol(source, index)` — 符号匹配

```python
def _match_symbol(self, source: str, index: int):
    for lexeme, lex_type in self.grammar.symbols:       # 已按长度降序
        if source.startswith(lexeme, index):
            return lexeme, lex_type
    return None
```

因为 `symbols` 列表已经在 `load_grammar()` 中按长度降序排好，所以只需要顺序遍历，找到第一个匹配的符号即可。这保证了最长匹配原则——较长的符号（如 `:=`、`..`）优先被尝试。

### 5.4 `_scan_char_literal(source, i, line)` — 字符字面量扫描

这是最复杂的单 Token 匹配逻辑。当扫描主循环遇到 `'` 时触发。

#### 第一层：正常匹配

```python
match = self.grammar.char_literal_re.match(source, index)
if match:
    literal = match.group(0)            # 如 "'A'"（3个字符）
    return Token(line, "CHARC", literal[1:-1]), len(literal)
```

正则 `'[^'\n]'` 的含义：
- `'` — 开头的单引号
- `[^'\n]` — 一个字符类：非引号、非换行的任意字符（精确匹配一个字符，无星号/加号量词）
- `'` — 结尾的单引号

所以匹配成功的字符串一定是 **3 个字符**：`'` + 一个字符 + `'`。语义值取中间的字符，去掉首尾引号：`literal[1:-1]`。

#### 第二层：异常诊断

当正则匹配失败时，说明遇到了不合法的字符字面量。此时不是逐字符报错，而是**尝试一次性吞掉整个出问题的片段**：

```python
next_quote = source.find("'", index + 1)      # 从 index+1 开始找下一个引号
next_newline = source.find("\n", index + 1)   # 找下一个换行符
stops = [pos for pos in (next_quote, next_newline) if pos != -1]
stop = min(stops) if stops else len(source) - 1
consumed = max(1, stop - index + 1)
return Token(line, "ERROR", source[index : index + consumed]), consumed
```

策略：取 **下一个引号** 和 **下一个换行** 中较近的一个作为错误边界。如果两者都不存在（已到文件尾），边界取最后一个字符。`max(1, ...)` 保证至少消耗一个字符防止死循环。

#### 各种情况演示

| 源码 | 正则匹配结果 | 探测器位置 | consumed | 输出 |
|------|-------------|-----------|----------|------|
| `'A'` | 命中 `'A'` | — | 3 | `CHARC("A")` |
| `'AB'` | 失败（B 不是结束引号） | next_quote=3 | 4 | `ERROR("'AB'")` |
| `''` | 失败（中间无字符） | next_quote=1 | 2 | `ERROR("''")` |
| `'\n` | 失败（换行符被 `\n` 排除） | next_newline=1 | 2 | `ERROR("'\\n")` |
| `'abc`(EOF) | 失败 | 两者都-1，stop=len-1 | 4 | `ERROR("'abc")` |
| `'''`（三个引号） | 失败（位置 1 还是引号） | next_quote=1，consumed=2 | 2+1 | 两轮：`ERROR("''")` + `ERROR("'")` |

---

## 6. 关键字 vs 标识符的完整对比

整个词法分析器中最核心的一个设计问题：**如何区分关键字和标识符？**

在 SNL 中，关键字（`if`、`while`、`program` 等）和标识符（`x`、`counter`、`myProc` 等）的词法形式是完全一样的——都匹配 `[A-Za-z][A-Za-z0-9]*`。`if` 可以是一个关键字，也可以是一个变量名（虽然这个语言不允许）。这是语言设计者的选择：**关键字是保留字**，不能被用作标识符。

实现上，区分流程是：

```
输入: "if"           输入: "x"
  │                     │
  ▼                     ▼
identifier_re.match()  identifier_re.match()
  │                     │
  ▼ (匹配 "if")         ▼ (匹配 "x")
  │                     │
  ▼                     ▼
keywords.get("if")     keywords.get("x")
  │                     │
  ▼ (返回 "IF")         ▼ (返回 None)
  │                     │
  ▼                     ▼
Token(line, "IF")     Token(line, "ID", "x")
```

核心要点：
- **"一个正则，两次使用"**：标识符正则既用于匹配标识符，也用于匹配关键字
- **查表在匹配之后**：先用正则找出词素，再查关键字表下结论
- **关键字大小写不敏感**：因为查表时转小写，`If`、`IF`、`if` 都命中
- **标识符大小写敏感**：因为 sem 存原始值，`myVar` 和 `myvar` 视为不同变量（由后续语义分析决定）

---

## 7. 错误恢复策略总结

词法分析器在面对错误时需要平衡两个目标：**在何处报错** 和 **如何继续扫描**。

| 错误类型 | 处理方式 | 理由 |
|----------|----------|------|
| **未知字符** | 产生 ERROR Token，**跳过一个字符**继续 | 单个字符错误不应影响后续代码的正常分析 |
| **未闭合注释** | 产生 ERROR Token，**break 终止**扫描 | 注释未闭合意味着后面所有代码都可能被误吞，继续分析没有意义 |
| **非法字符字面量** | 产生 ERROR Token，**一次性吞到下一个引号/换行** | 避免将 `'AB'` 拆成多个无意义错误的碎片 |

---

## 8. 模块独立性

`snl_lexer.py` 设计为既可被编译器流水线调用，也可作为独立工具运行。每个模块都有自己的 `main()` 函数：

```bash
# 作为独立工具使用：扫描 SNL 源码并输出 Token 序列
python src/snl_lexer.py source.snl

# 输出为 JSON 格式（方便其他工具处理）
python src/snl_lexer.py source.snl --json

# 写入文件并包含 EOF 哨兵
python src/snl_lexer.py source.snl -o tokens.out --with-eof
```

这种设计使得各模块可以独立测试和调试，也符合编译器教材中经典的"pass 式"架构。

---

## 9. 完整示例

假设 SNL 源码文件 `hello.snl` 内容为：

```
program hello
begin
  write(1)
end.
```

词法分析过程：

| 步骤 | 当前位置 | 匹配规则 | 产生 Token | 行号 |
|------|---------|----------|------------|------|
| 1 | 0: `p` | identifier_re → `"program"`, keywords → `"PROGRAM"` | `PROGRAM` | 1 |
| 2 | 8: ` ` | 空白 → 跳过 | — | 1 |
| 3 | 9: `h` | identifier_re → `"hello"`, 非关键字 | `ID("hello")` | 1 |
| 4 | 14: `\n` | 空白 → 跳过 | — | 2 |
| 5 | 15: `b` | identifier_re → `"begin"`, keywords → `"BEGIN"` | `BEGIN` | 2 |
| 6 | 20: `\n` | 空白 → 跳过 | — | 3 |
| 7 | 21: ` ` | 空白 → 跳过 | — | 3 |
| 8 | 22: `w` | identifier_re → `"write"`, keywords → `"WRITE"` | `WRITE` | 3 |
| 9 | 27: `(` | symbol → `(`, symbols → LPAREN | `LPAREN` | 3 |
| 10 | 28: `1` | integer_re → `"1"` | `INTC("1")` | 3 |
| 11 | 29: `)` | symbol → `)`, symbols → RPAREN | `RPAREN` | 3 |
| 12 | 30: `\n` | 空白 → 跳过 | — | 4 |
| 13 | 31: `e` | identifier_re → `"end"`, keywords → `"END"` | `END` | 4 |
| 14 | 34: `.` | symbol → `.`, symbols → DOT | `DOT` | 4 |
| 15 | 35: `\n` | 空白 → 跳过 | — | 5 |
| 16 | (EOF) | include_eof=True | `EOF` | 5 |

最终 Token 序列：
```
LineShow  Lex          Sem
--------  -----------  ---
1         PROGRAM
1         ID           hello
2         BEGIN
3         WRITE
3         LPAREN
3         INTC         1
3         RPAREN
4         END
4         DOT
5         EOF
```

---

## 10. 设计特点总结

| 特点 | 实现方式 |
|------|---------|
| **配置驱动** | 词法规则从 grammar.txt 读取，不硬编码在代码中 |
| **贪心最长匹配** | 符号按长度降序排列，`:=` 优先于 `.`，`..` 优先于 `.` |
| **关键字复用标识符正则** | 匹配后 O(1) 查表区分，节省正则数量 |
| **两级过滤优化** | 先用 `ch == "'"` 判断再进入字符字面量扫描，避免不必要正则调用 |
| **不中断的错误恢复** | 未知字符标 ERROR 跳过；注释未闭合直接 break |
| **单 pass 无回溯** | 指针线性扫描，每个字符只处理一次，无回溯，O(n) 时间复杂度 |
| **行号精确维护** | 空白和注释内都维护 line 计数 |
| **模块可独立运行** | 每个模块都有 `main()` 和 CLI，可单独测试或使用 |

# SNL 词法分析器设计文档

本文梳理 `src/snl_lexer.py` 的设计思路和关键实现细节。

## 整体架构

单 pass 流式扫描器，非 lex/flex 生成。维护一个指针 `i` 在源代码字符串上滑动，每步从当前位置尝试匹配一种 token，命中则产出 token 并前进指针，全部规则失败则报 ERROR。无回溯。

分三层：

```
grammar.txt          → 配置层（声明语言词汇）
LexicalGrammar       → 模型层（内存中的规则表示）
SNLLexer.tokenize()  → 执行层（匹配循环）
```

## Token 数据结构

```python
@dataclass(frozen=True)
class Token:
    line_show: int    # 行号，1-based（human-readable）
    lex: str           # 词法类型："ID", "INTC", "CHARC", "ASSIGN", "IF" 等
    sem: str = ""      # 语义值：标识符名、整数值、字符值；关键字/符号为空
```

设计要点：
- `frozen=True`：一旦生成不可变，后续 parser/semantic/codegen 多层传递不会意外修改
- `line_show` 而非 offset：报错时用户关心行号，在扫描过程中由换行符递增维护
- `sem` 带默认空串：关键字和符号无需额外信息，`ID`/`INTC`/`CHARC` 才存具体值，一个字段统一表示

## 语法规则表示 LexicalGrammar

```python
@dataclass
class LexicalGrammar:
    keywords: dict[str, str]            # {"program" → "PROGRAM", ...}
    symbols: list[tuple[str, str]]      # [(":=", "ASSIGN"), ("..", "UNDERANGE"), ...]
    comments: list[tuple[str, str]]     # [("{", "}")]
    identifier_re: re.Pattern[str]      # 编译好的正则 [A-Za-z][A-Za-z0-9]*
    integer_re: re.Pattern[str]         # [0-9]+
    char_literal_re: re.Pattern[str]    # '[^'\n]'
```

六种字段的存储选型：

| 字段 | 容器 | 原因 |
|------|------|------|
| `keywords` | `dict[str,str]` | 复用标识符正则，匹配后 O(1) 查表转换为关键字 token |
| `symbols` | `list[(str,str)]` | 符号存在前缀重叠（如 `:=` 与 `.`、`..`），必须按长度降序线性遍历以保证最长匹配 |
| `comments` | `list[(str,str)]` | 可扩展多种注释风格，一对一的起止串 |
| `identifier_re` | 编译后的正则 | 标识符和关键字共用，匹配后区分 |
| `integer_re` | 编译后的正则 | 纯数字序列 |
| `char_literal_re` | 编译后的正则 | 单引号括住恰好一个非引号非换行字符 |

### 关键字为何不用独立正则

关键字（`program`、`if`、`while` 等）本身就是标识符的子集，都满足 `[A-Za-z][A-Za-z0-9]*`。没必要写 20 条独立正则。策略：先用 `identifier_re` 匹配词素，再查 `keywords` 字典，命中为关键字，未命中为普通 `ID`。且查表时用 `.lower()` 实现关键字大小写不敏感。

### 符号为何必须按长度降序

考虑重叠关系：`:=`（ASSIGN）、`..`（UNDERANGE）、`.`（DOT）。

如果按字典或任意序匹配，`.` 会先于 `..` 命中，导致 `..` 被错误拆成两个 `DOT`。按长度降序后，`load_grammar()` 末尾执行：

```python
symbols.sort(key=lambda item: len(item[0]), reverse=True)
```

保证 `:=` 在 `.` 之前被检测，`..` 在 `.` 之前被检测，贪心最长匹配正确。

### 注释为何是 list

当前只定义了一种注释风格 `COMMENT { }`。用列表预留扩展空间——要加 `/* */` 或 `-- \n`，只需在 `grammar.txt` 加一行，不需要改代码。

## grammar.txt 加载机制

词法规则不是硬编码的，从 `grammar.txt` 文件读取。格式为 `KIND 参数1 参数2`：

```
KEYWORD program PROGRAM        → keywords["program"] = "PROGRAM"
SYMBOL  := ASSIGN             → symbols.append((":=", "ASSIGN"))
COMMENT { }                   → comments.append(("{", "}"))
IDENTIFIER [A-Za-z][A-Za-z0-9]*
INTEGER [0-9]+
CHAR_LITERAL '[^'\n]'
```

要加新关键字、改符号、换注释风格，只改文本文件即可。

## 核心扫描循环 tokenize()

```
while i < length:
    ch = source[i]

    ├─ 空白？     换行加行号，跳过
    ├─ 注释开始？  吞到结束串；未闭合则报 ERROR 并 break
    ├─ 匹配到 identifier_re？ 查 keywords 字典
    │   ├─ 命中    → 关键字 token
    │   └─ 未命中  → ID(token.sem)
    ├─ 匹配到 integer_re？    → INTC(token.sem)
    ├─ 当前字符是 ' ？        → 进入 _scan_char_literal
    ├─ 匹配到 symbol？        → 对应符号 token
    └─ 全不命中              → ERROR(token.sem=ch)，前进一格
```

### 匹配优先级

这个 if-elif 链的次序就是匹配优先级：

1. **空白** — 最高，不产生 token 只维护行号
2. **注释** — 次高，吞掉注释内所有内容
3. **标识符/关键字** — 在整数之前，因标识符必须以字母开头，不会与整数冲突
4. **整数** — 纯数字
5. **字符字面量** — 以 `'` 开头，优先于符号匹配（否则 `'` 本身可能被误匹配）
6. **符号** — 兜底的固定字符串匹配
7. **ERROR** — 全不命中时跳过单个字符，保证扫描器永不死锁

### include_eof 参数

```python
def tokenize(self, source: str, include_eof: bool = False) -> list[Token]:
```

当 `include_eof=True` 时，在 token 序列末尾追加一个 `Token(line, "EOF")` 哨兵。给 parser 使用——parser 需要明确的 EOF 来检测程序是否正常结束。

## _match 函数

```python
@staticmethod
def _match(pattern: re.Pattern[str], source: str, index: int) -> str:
    match = pattern.match(source, index)
    return match.group(0) if match else ""
```

从 source 的 index 位置尝试匹配正则。`re.match(source, index)` 指定了起始位置（注意 `^` 锚仍指串首，所以这些正则都不使用 `^$`）。返回空串而非 `None`，调用方直接用返回值作为布尔条件判断。

`@staticmethod`：不访问 self，纯函数。

## _match_comment_start 函数

```python
def _match_comment_start(self, source: str, index: int) -> tuple[str, str] | None:
    for begin, end in self.grammar.comments:
        if source.startswith(begin, index):
            return begin, end
    return None
```

只负责任何当前位置是否属于注释入口。是则返回 `(开始串, 结束串)`，不是返回 `None`。注释内的内容由主循环消费。职责分离：此函数只识别入口，主循环负责消耗内容。

## _scan_char_literal 函数

最复杂的单 token 匹配逻辑。当前字符已经是 `'` 时进入。

### 两层匹配

**第一层：正常情况**

```python
match = self.grammar.char_literal_re.match(source, index)
if match:
    literal = match.group(0)
    return Token(line, "CHARC", literal[1:-1]), len(literal)
```

正则 `'[^'\n]'` 匹配：`'` + 恰好一个非引号非换行字符 + `'`。命中后剥离首尾引号，产出 `CHARC` token，consumed = 3。

**第二层：异常诊断**

正则失败后，不逐字符报错，而是一次性吞掉整个有问题片段。查找逻辑：

```python
next_quote = source.find("'", index + 1)      # 跳过开引号
next_newline = source.find("\n", index + 1)
stops = [pos for pos in (next_quote, next_newline) if pos != -1]
stop = min(stops) if stops else len(source) - 1
consumed = max(1, stop - index + 1)
return Token(line, "ERROR", source[index : index + consumed]), consumed
```

取下一个 `'` 和 `\n` 中较近者作为错误片段终点。若都不存在（EOF 前未闭合），终点取 `len(source)-1`。`max(1, ...)` 保证至少消费一个字符，防止死循环。错误源文本完整保留在 ERROR token 的 sem 字段中。

### 各 case 的处理

| 源码 | 正则 | 探测器 | consumed | 结果 |
|------|------|--------|----------|------|
| `'A'` | 命中 | — | 3 | `CHARC("A")` |
| `'AB'` | 失败（位置 1 是 A，位置 2 是 B，预期 `'`） | `next_quote` 在位置 3 | 4 | `ERROR("'AB'")` |
| `'\n` | 失败（换行不在 `[^'\n]` 内） | `next_newline` 在 +1 | 2 | `ERROR("'\\n")` |
| `'abc`(EOF) | 失败 | 两者都 -1，stop=len-1 | 4 | `ERROR("'abc")` |
| `'''`（三个引号） | 失败（位置 1 是 `'`） | `next_quote` 在位置 1，consumed=2 | 2+1 | 两个 ERROR：`ERROR("''")` 和 `ERROR("'")` |

关键语义：SNL 字符字面量严格限制长度为 1。正则 `[^'\n]` 不带量词，只匹配恰好一个字符。多字符、零字符、跨行均报错。

## 错误恢复策略

- **未知字符**：标记为 ERROR，跳过单个字符，继续扫描后续内容。保证不会因为一个不认识的字导致整段代码无法分析
- **注释未闭合**：标记 ERROR，**break 终止**扫描。因为注释后的内容可能全是注释内文本，继续会产生大量垃圾错误
- **异常字符字面量**：一次性吞到下一个引号或换行为止，整体标记为一个 ERROR。避免把 `'AB'` 拆成 `ERROR("'")` + `ID("AB")` 这样的误报

## 设计特点总结

| 特点 | 实现方式 |
|------|---------|
| 配置驱动 | 词法规则从 grammar.txt 读取，不硬编码 |
| 贪心最长匹配 | 符号按长度降序，`:=` 优先于 `.` |
| 关键字复用标识符正则 | 匹配后 O(1) 查字典区分 |
| 不中断的错误恢复 | 未知字符标 ERROR 跳过；注释未闭合 break |
| 单 pass 无缓冲 | 指针直接扫描源代码字符串，不预读 |
| 行号精确 | 空白和注释内都维护 line 计数 |
| 配置与逻辑分离 | 语法文件独立，词汇变更无需改代码 |

# 词法分析，语法分析，语义分析实验记录

## 词法分析

对token的定义：
‘’‘
class Token:
line_show: int # 源码行号
lex: str # 词法类型，如 "ID"、"INTC"、"PROGRAM"
sem: str = "" # 语义值，如标识符名称或整数字面量
’‘’

语法的定义：
‘’‘
@dataclass
class LexicalGrammar:
"""从 grammar.txt 加载的词法规则集合。"""
keywords: dict[str, str] # 关键字映射，如 {"program": "PROGRAM", "if": "IF"}
symbols: list[tuple[str, str]] # 符号列表，按长度降序排列以支持最长匹配
comments: list[tuple[str, str]] # 注释定界符对，如 [("{", "}")]
identifier_re: re.Pattern[str] # 标识符正则
integer_re: re.Pattern[str] # 整数正则
char_literal_re: re.Pattern[str] # 字符字面量正则
’‘’

解析过程：9状态dfa，
返回tokenlist

## 语法分析

类型的内部表示：

‘’‘python
@dataclass
class TypeNode:
"""类型的内部表示。kind 取值：integer/char/array/record/alias/unknown"""
kind: str
line: int
name: str = "" # alias 类型引用的名称
low: int | None = None # array 下界
high: int | None = None # array 上界
element: "TypeNode | None" = None # array 元素类型
fields: list["FieldDecl"] = field(default_factory=list) # record 字段列表
type_info: object | None = None # 语义分析阶段填充的 TypeInfo
‘’‘

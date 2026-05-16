#!/usr/bin/env python3
"""SNL 词法分析器。

输入：SNL 源文件文本
输出：Token 序列（行号 + 词法类型 + 语义值）
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from enum import Enum, auto
from pathlib import Path
from typing import Iterable


DEFAULT_GRAMMAR = Path(__file__).with_name("grammar.txt")


@dataclass(frozen=True)
class Token:
    """词法单元，一旦生成不可变。"""
    line_show: int  # 源码行号
    lex: str        # 词法类型，如 "ID"、"INTC"、"PROGRAM"
    sem: str = ""   # 语义值，如标识符名称或整数字面量


@dataclass
class LexicalGrammar:
    """从 grammar.txt 加载的词法规则集合。"""
    keywords: dict[str, str]          # 关键字映射，如 {"program": "PROGRAM", "if": "IF"}
    symbols: list[tuple[str, str]]    # 符号列表，按长度降序排列以支持最长匹配
    comments: list[tuple[str, str]]   # 注释定界符对，如 [("{", "}")]
    identifier_re: re.Pattern[str]    # 标识符正则
    integer_re: re.Pattern[str]       # 整数正则
    char_literal_re: re.Pattern[str]  # 字符字面量正则


class LexerError(RuntimeError):
    pass


class LexerState(Enum):
    """Textbook 9-state DFA used by the SNL scanner.

    The names mirror 图 4.7 in the course textbook.  Some accepting behavior
    remains project-compatible, most notably INCHAR accepting any single
    non-quote, non-newline character instead of only letters/digits.
    """

    START = auto()
    INID = auto()
    INNUM = auto()
    DONE = auto()
    INASSIGN = auto()
    INCOMMENT = auto()
    INRANGE = auto()
    INCHAR = auto()
    ERROR = auto()


def load_grammar(path: Path) -> LexicalGrammar:
    """解析 grammar.txt 配置文件，构建词法规则。

    配置文件每行格式为：指令 参数1 参数2
    支持的指令：KEYWORD、SYMBOL、COMMENT、IDENTIFIER、INTEGER、CHAR_LITERAL
    """
    keywords: dict[str, str] = {}
    symbols: list[tuple[str, str]] = []
    comments: list[tuple[str, str]] = []
    identifier_pattern = r"[A-Za-z][A-Za-z0-9]*"
    integer_pattern = r"[0-9]+"
    char_literal_pattern = r"'[^'\n]'"

    for line_no, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        parts = line.split(maxsplit=2)
        kind = parts[0].upper()

        if kind in {"KEYWORD", "SYMBOL"}:
            if len(parts) != 3:
                raise LexerError(f"{path}:{line_no}: {kind} needs two fields")
            source, lex_type = parts[1], parts[2]
            if kind == "KEYWORD":
                keywords[source.lower()] = lex_type
            else:
                symbols.append((source, lex_type))
        elif kind == "COMMENT":
            if len(parts) != 3:
                raise LexerError(f"{path}:{line_no}: COMMENT needs begin and end delimiters")
            comments.append((parts[1], parts[2]))
        elif kind == "IDENTIFIER":
            identifier_pattern = parts[1] if len(parts) > 1 else identifier_pattern
        elif kind == "INTEGER":
            integer_pattern = parts[1] if len(parts) > 1 else integer_pattern
        elif kind == "CHAR_LITERAL":
            char_literal_pattern = parts[1] if len(parts) > 1 else char_literal_pattern
        else:
            raise LexerError(f"{path}:{line_no}: unknown grammar directive {parts[0]!r}")

    # 符号按长度降序排列，确保 ":=" 优先于 ":"、".." 优先于 "."
    symbols.sort(key=lambda item: len(item[0]), reverse=True)
    return LexicalGrammar(
        keywords=keywords,
        symbols=symbols,
        comments=comments,
        identifier_re=re.compile(identifier_pattern),
        integer_re=re.compile(integer_pattern),
        char_literal_re=re.compile(char_literal_pattern),
    )


class SNLLexer:
    """SNL 词法扫描器。单遍 9 状态 DFA，O(n) 复杂度。"""

    def __init__(self, grammar: LexicalGrammar) -> None:
        self.grammar = grammar

    def tokenize(self, source: str, include_eof: bool = False) -> list[Token]:
        """将源码文本扫描为 Token 列表。

        扫描流程对应教材图 4.7：START 先按当前字符分派到 8 个
        后续状态之一，每个状态负责消费当前单词并回到 START。
        """
        tokens: list[Token] = []
        i = 0
        line = 1
        length = len(source)

        while i < length:
            state = LexerState.START
            token_line = line
            ch = source[i]

            if ch.isspace():
                if ch == "\n":
                    line += 1
                i += 1
                continue

            if ch.isalpha():
                state = LexerState.INID
            elif ch.isdigit():
                state = LexerState.INNUM
            elif ch == ":":
                state = LexerState.INASSIGN
            elif self._match_comment_start(source, i):
                state = LexerState.INCOMMENT
            elif ch == ".":
                state = LexerState.INRANGE
            elif ch == "'":
                state = LexerState.INCHAR
            elif self._is_single_delimiter(ch):
                state = LexerState.DONE
            else:
                state = LexerState.ERROR

            if state == LexerState.INID:
                identifier, consumed = self._scan_identifier(source, i)
                lex_type = self.grammar.keywords.get(identifier.lower())
                tokens.append(Token(token_line, lex_type or "ID", "" if lex_type else identifier))
                i += consumed
            elif state == LexerState.INNUM:
                integer, consumed = self._scan_integer(source, i)
                tokens.append(Token(token_line, "INTC", integer))
                i += consumed
            elif state == LexerState.DONE:
                tokens.append(Token(token_line, self._single_symbol_type(ch)))
                i += 1
            elif state == LexerState.INASSIGN:
                token, consumed = self._scan_assign(source, i, token_line)
                tokens.append(token)
                i += consumed
            elif state == LexerState.INCOMMENT:
                token, consumed, new_line = self._scan_comment(source, i, token_line, line)
                if token is not None:
                    tokens.append(token)
                    break
                i += consumed
                line = new_line
            elif state == LexerState.INRANGE:
                token, consumed = self._scan_range(source, i, token_line)
                tokens.append(token)
                i += consumed
            elif state == LexerState.INCHAR:
                token, consumed = self._scan_char_literal(source, i, token_line)
                tokens.append(token)
                i += consumed
            elif state == LexerState.ERROR:
                tokens.append(Token(token_line, "ERROR", ch))
                i += 1

        if include_eof:
            tokens.append(Token(line, "EOF"))

        return tokens

    @staticmethod
    def _scan_identifier(source: str, index: int) -> tuple[str, int]:
        end = index + 1
        while end < len(source) and source[end].isalnum():
            end += 1
        return source[index:end], end - index

    @staticmethod
    def _scan_integer(source: str, index: int) -> tuple[str, int]:
        end = index + 1
        while end < len(source) and source[end].isdigit():
            end += 1
        return source[index:end], end - index

    def _scan_assign(self, source: str, index: int, line: int) -> tuple[Token, int]:
        if index + 1 < len(source) and source[index + 1] == "=":
            return Token(line, "ASSIGN"), 2
        return Token(line, "ERROR", ":"), 1

    def _scan_comment(self, source: str, index: int, token_line: int, line: int) -> tuple[Token | None, int, int]:
        comment_match = self._match_comment_start(source, index)
        if comment_match is None:
            return Token(token_line, "ERROR", source[index]), 1, line

        begin, end = comment_match
        i = index + len(begin)
        current_line = line
        while i < len(source) and not source.startswith(end, i):
            if source[i] == "\n":
                current_line += 1
            i += 1
        if i >= len(source):
            return Token(token_line, "ERROR", f"unclosed comment starts with {begin!r}"), i - index, current_line
        i += len(end)
        return None, i - index, current_line

    def _scan_range(self, source: str, index: int, line: int) -> tuple[Token, int]:
        if index + 1 < len(source) and source[index + 1] == ".":
            return Token(line, "UNDERANGE"), 2
        return Token(line, self._single_symbol_type(".")), 1

    def _match_comment_start(self, source: str, index: int) -> tuple[str, str] | None:
        for begin, end in self.grammar.comments:
            if source.startswith(begin, index):
                return begin, end
        return None

    def _is_single_delimiter(self, ch: str) -> bool:
        return any(lexeme == ch for lexeme, _ in self.grammar.symbols)

    def _single_symbol_type(self, ch: str) -> str:
        for lexeme, lex_type in self.grammar.symbols:
            if lexeme == ch:
                return lex_type
        return "ERROR"

    def _scan_char_literal(self, source: str, index: int, line: int) -> tuple[Token, int]:
        # Textbook 图 4.7 labels the middle character as letter/digit.  The
        # project already accepted any single non-quote, non-newline character,
        # so we preserve that downstream-compatible contract here.
        if (
            index + 2 < len(source)
            and source[index + 2] == "'"
            and source[index + 1] not in {"'", "\n"}
        ):
            return Token(line, "CHARC", source[index + 1]), 3

        next_quote = source.find("'", index + 1)
        next_newline = source.find("\n", index + 1)
        stops = [pos for pos in (next_quote, next_newline) if pos != -1]
        stop = min(stops) if stops else len(source) - 1
        consumed = max(1, stop - index + 1)
        return Token(line, "ERROR", source[index : index + consumed]), consumed
    
def format_text(tokens: Iterable[Token]) -> str:
    rows = ["LineShow  Lex          Sem", "--------  -----------  ---"]
    for token in tokens:
        rows.append(f"{token.line_show:<8}  {token.lex:<11}  {token.sem}")
    return "\n".join(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scan an SNL source file and output a Token sequence.")
    parser.add_argument("source", type=Path, help="SNL source file")
    parser.add_argument("-g", "--grammar", type=Path, default=DEFAULT_GRAMMAR, help="lexical grammar file")
    parser.add_argument("-o", "--output", type=Path, help="write tokens to this file")
    parser.add_argument("--json", action="store_true", help="output JSON instead of a text table")
    parser.add_argument("--with-eof", action="store_true", help="append an EOF token")
    args = parser.parse_args(argv)

    try:
        grammar = load_grammar(args.grammar)
        source_text = args.source.read_text(encoding="utf-8")
        tokens = SNLLexer(grammar).tokenize(source_text, include_eof=args.with_eof)
    except (OSError, LexerError) as exc:
        print(f"snl_lexer.py: {exc}", file=sys.stderr)
        return 2

    if args.json:
        output = json.dumps([asdict(token) for token in tokens], ensure_ascii=False, indent=2)
    else:
        output = format_text(tokens)

    if args.output:
        args.output.write_text(output + "\n", encoding="utf-8")
    else:
        print(output)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

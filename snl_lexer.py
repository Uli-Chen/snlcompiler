#!/usr/bin/env python3
"""Lexical analyzer for the SNL teaching language.

Input:  an SNL source file
Output: a Token sequence with LineShow, Lex, and Sem fields
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


DEFAULT_GRAMMAR = Path(__file__).with_name("grammar.txt")


@dataclass(frozen=True)
class Token:
    line_show: int
    lex: str
    sem: str = ""


@dataclass
class LexicalGrammar:
    keywords: dict[str, str]
    symbols: list[tuple[str, str]]
    comments: list[tuple[str, str]]
    identifier_re: re.Pattern[str]
    integer_re: re.Pattern[str]
    char_literal_re: re.Pattern[str]


class LexerError(RuntimeError):
    pass


def load_grammar(path: Path) -> LexicalGrammar:
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
    def __init__(self, grammar: LexicalGrammar) -> None:
        self.grammar = grammar

    def tokenize(self, source: str, include_eof: bool = False) -> list[Token]:
        tokens: list[Token] = []
        i = 0
        line = 1
        length = len(source)

        while i < length:
            ch = source[i]

            if ch.isspace():
                if ch == "\n":
                    line += 1
                i += 1
                continue

            comment_match = self._match_comment_start(source, i)
            if comment_match:
                begin, end = comment_match
                start_line = line
                i += len(begin)
                while i < length and not source.startswith(end, i):
                    if source[i] == "\n":
                        line += 1
                    i += 1
                if i >= length:
                    tokens.append(Token(start_line, "ERROR", f"unclosed comment starts with {begin!r}"))
                    break
                i += len(end)
                continue

            identifier = self._match(self.grammar.identifier_re, source, i)
            if identifier:
                lex_type = self.grammar.keywords.get(identifier.lower())
                if lex_type is None:
                    tokens.append(Token(line, "ID", identifier))
                else:
                    tokens.append(Token(line, lex_type))
                i += len(identifier)
                continue

            integer = self._match(self.grammar.integer_re, source, i)
            if integer:
                tokens.append(Token(line, "INTC", integer))
                i += len(integer)
                continue

            if ch == "'":
                char_token, consumed = self._scan_char_literal(source, i, line)
                tokens.append(char_token)
                i += consumed
                continue

            symbol = self._match_symbol(source, i)
            if symbol:
                lexeme, lex_type = symbol
                tokens.append(Token(line, lex_type))
                i += len(lexeme)
                continue

            tokens.append(Token(line, "ERROR", ch))
            i += 1

        if include_eof:
            tokens.append(Token(line, "EOF"))

        return tokens

    def _match_comment_start(self, source: str, index: int) -> tuple[str, str] | None:
        for begin, end in self.grammar.comments:
            if source.startswith(begin, index):
                return begin, end
        return None

    @staticmethod
    def _match(pattern: re.Pattern[str], source: str, index: int) -> str:
        match = pattern.match(source, index)
        return match.group(0) if match else ""

    def _match_symbol(self, source: str, index: int) -> tuple[str, str] | None:
        for lexeme, lex_type in self.grammar.symbols:
            if source.startswith(lexeme, index):
                return lexeme, lex_type
        return None

    def _scan_char_literal(self, source: str, index: int, line: int) -> tuple[Token, int]:
        match = self.grammar.char_literal_re.match(source, index)
        if match:
            literal = match.group(0)
            return Token(line, "CHARC", literal[1:-1]), len(literal)

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

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from snl_lexer import DEFAULT_GRAMMAR, LexerState, SNLLexer, load_grammar


class LexerDFATest(unittest.TestCase):
    def setUp(self) -> None:
        self.lexer = SNLLexer(load_grammar(DEFAULT_GRAMMAR))

    def tokens(self, source: str, include_eof: bool = False) -> list[tuple[int, str, str]]:
        return [
            (token.line_show, token.lex, token.sem)
            for token in self.lexer.tokenize(source, include_eof=include_eof)
        ]

    def test_declares_textbook_nine_dfa_states(self) -> None:
        self.assertEqual(
            [state.name for state in LexerState],
            [
                "START",
                "INID",
                "INNUM",
                "DONE",
                "INASSIGN",
                "INCOMMENT",
                "INRANGE",
                "INCHAR",
                "ERROR",
            ],
        )

    def test_tokenizes_core_dfa_paths_without_changing_output_contract(self) -> None:
        source = "program P\nx := 42 + y; a[1..10].f < 'A'"

        self.assertEqual(
            self.tokens(source, include_eof=True),
            [
                (1, "PROGRAM", ""),
                (1, "ID", "P"),
                (2, "ID", "x"),
                (2, "ASSIGN", ""),
                (2, "INTC", "42"),
                (2, "PLUS", ""),
                (2, "ID", "y"),
                (2, "SEMI", ""),
                (2, "ID", "a"),
                (2, "LMIDPAREN", ""),
                (2, "INTC", "1"),
                (2, "UNDERANGE", ""),
                (2, "INTC", "10"),
                (2, "RMIDPAREN", ""),
                (2, "DOT", ""),
                (2, "ID", "f"),
                (2, "LT", ""),
                (2, "CHARC", "A"),
                (2, "EOF", ""),
            ],
        )

    def test_comments_preserve_following_token_line_numbers(self) -> None:
        self.assertEqual(
            self.tokens("program p\n{ hidden\ncomment }\nwrite"),
            [
                (1, "PROGRAM", ""),
                (1, "ID", "p"),
                (4, "WRITE", ""),
            ],
        )

    def test_char_literals_keep_existing_wide_single_character_compatibility(self) -> None:
        self.assertEqual(
            self.tokens("'A' '7' '+'"),
            [
                (1, "CHARC", "A"),
                (1, "CHARC", "7"),
                (1, "CHARC", "+"),
            ],
        )

    def test_error_paths_match_existing_downstream_contract(self) -> None:
        self.assertEqual(self.tokens(":"), [(1, "ERROR", ":")])
        self.assertEqual(self.tokens("'AB'"), [(1, "ERROR", "'AB'")])
        self.assertEqual(
            self.tokens("{ never closes\nstill comment"),
            [(1, "ERROR", "unclosed comment starts with '{'")],
        )


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Visualization utilities for SNL compiler side information.

The utility runs the compiler front end, emits the normal side files, and
creates browser-friendly visualizations for syntax trees, symbol tables,
call graphs, and recursive stack frames.  It intentionally uses only the
Python standard library so it works in lab environments without Graphviz.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    from playground.snlcompiler.src.snl_codegen import (
        CodegenError,
        FrameInfo,
        MIPSRunner,
        SNLCodeGenerator,
    )
    from playground.snlcompiler.src.snl_lexer import DEFAULT_GRAMMAR, SNLLexer, format_text, load_grammar
    from playground.snlcompiler.src.snl_parser import SNLParser, Token, TreeNode, format_parse_result
    from playground.snlcompiler.src.snl_semantic import SNLSemanticAnalyzer, format_semantic_result
except ModuleNotFoundError:
    from snl_codegen import CodegenError, FrameInfo, MIPSRunner, SNLCodeGenerator
    from snl_lexer import DEFAULT_GRAMMAR, SNLLexer, format_text, load_grammar
    from snl_parser import SNLParser, Token, TreeNode, format_parse_result
    from snl_semantic import SNLSemanticAnalyzer, format_semantic_result


@dataclass
class VisualNode:
    label: str
    children: list["VisualNode"] = field(default_factory=list)
    x: float = 0
    y: float = 0


def tokenize_source(source: Path) -> tuple[list[Token], str, list[str]]:
    lexer_tokens = SNLLexer(load_grammar(DEFAULT_GRAMMAR)).tokenize(
        source.read_text(encoding="utf-8"),
        include_eof=True,
    )
    token_text = format_text(lexer_tokens)
    tokens = [Token(token.line_show, token.lex, token.sem) for token in lexer_tokens]
    errors = [f"line {token.line_show}: lexical error {token.sem}" for token in lexer_tokens if token.lex == "ERROR"]
    return tokens, token_text, errors


def build_reports(tokens: list[Token]) -> tuple[TreeNode, str, SNLSemanticAnalyzer, str, list[str]]:
    parser = SNLParser(tokens)
    tree = parser.parse()
    parse_report = format_parse_result(parser.errors, tree)

    semantic = SNLSemanticAnalyzer(tokens)
    semantic.analyze()
    semantic_report = format_semantic_result(semantic)
    return tree, parse_report, semantic, semantic_report, parser.errors + semantic.errors


def tree_to_visual(node: TreeNode) -> VisualNode:
    return VisualNode(node.label(), [tree_to_visual(child) for child in node.children])


def layout_tree(root: VisualNode, x_gap: int = 155, y_gap: int = 88) -> tuple[int, int]:
    leaf_index = 0
    max_depth = 0

    def visit(node: VisualNode, depth: int) -> None:
        nonlocal leaf_index, max_depth
        max_depth = max(max_depth, depth)
        node.y = 48 + depth * y_gap
        if not node.children:
            node.x = 80 + leaf_index * x_gap
            leaf_index += 1
            return
        for child in node.children:
            visit(child, depth + 1)
        node.x = sum(child.x for child in node.children) / len(node.children)

    visit(root, 0)
    width = max(900, 160 + max(1, leaf_index) * x_gap)
    height = max(180, 120 + (max_depth + 1) * y_gap)
    return width, height


def render_syntax_tree_svg(root: TreeNode) -> str:
    visual_root = tree_to_visual(root)
    width, height = layout_tree(visual_root)
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        "<style>",
        ".edge{stroke:#5b6472;stroke-width:1.4}",
        ".node{fill:#f8fbff;stroke:#2b6cb0;stroke-width:1.4}",
        ".label{font-family:Menlo,Consolas,monospace;font-size:12px;fill:#111827;text-anchor:middle;dominant-baseline:middle}",
        ".title{font-family:Arial,sans-serif;font-size:18px;font-weight:700;fill:#111827}",
        "</style>",
        '<text class="title" x="24" y="28">SNL Syntax Tree</text>',
    ]

    def edges(node: VisualNode) -> None:
        for child in node.children:
            lines.append(f'<line class="edge" x1="{node.x:.1f}" y1="{node.y + 18:.1f}" x2="{child.x:.1f}" y2="{child.y - 18:.1f}"/>')
            edges(child)

    def nodes(node: VisualNode) -> None:
        label = html.escape(node.label)
        width_px = min(210, max(58, 8 * len(node.label) + 24))
        lines.append(
            f'<rect class="node" x="{node.x - width_px / 2:.1f}" y="{node.y - 18:.1f}" '
            f'width="{width_px:.1f}" height="36" rx="6"/>'
        )
        lines.append(f'<text class="label" x="{node.x:.1f}" y="{node.y:.1f}">{label}</text>')
        for child in node.children:
            nodes(child)

    edges(visual_root)
    nodes(visual_root)
    lines.append("</svg>")
    return "\n".join(lines)


def render_symbol_tables_html(semantic: SNLSemanticAnalyzer) -> str:
    sections = []
    for scope in semantic.table.scopes:
        parent = "-" if scope.parent is None else str(scope.parent)
        rows = []
        for symbol in scope.symbols.values():
            rows.append(
                "<tr>"
                f"<td>{html.escape(symbol.kind)}</td>"
                f"<td>{html.escape(symbol.name)}</td>"
                f"<td>{html.escape(symbol.type_display())}</td>"
                f"<td>{symbol.line}</td>"
                f"<td>{html.escape(symbol.other_display())}</td>"
                "</tr>"
            )
        body = "\n".join(rows) or '<tr><td colspan="5">&lt;empty&gt;</td></tr>'
        sections.append(
            f"""
            <section class="card">
              <h2>Scope #{scope.number}: {html.escape(scope.name)}</h2>
              <p>level={scope.level}, parent={parent}</p>
              <table>
                <thead><tr><th>Kind</th><th>Name</th><th>Type</th><th>Line</th><th>Other</th></tr></thead>
                <tbody>{body}</tbody>
              </table>
            </section>
            """
        )
    return wrap_html("SNL Symbol Tables", "\n".join(sections))


def extract_call_edges(assembly: str) -> list[tuple[str, str]]:
    edges: list[tuple[str, str]] = []
    current = "main"
    for raw in assembly.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.endswith(":"):
            label = line[:-1]
            if label == "main" or label.startswith("proc_"):
                current = label
        elif line.startswith("jal "):
            edges.append((current, line.split()[1]))
    return edges


def render_call_graph_svg(edges: list[tuple[str, str]]) -> str:
    nodes = sorted({"main"} | {item for edge in edges for item in edge})
    width = max(620, len(nodes) * 190)
    height = 260
    positions = {node: (95 + index * 180, 120) for index, node in enumerate(nodes)}
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        "<style>",
        ".edge{stroke:#6b7280;stroke-width:1.6;fill:none;marker-end:url(#arrow)}",
        ".self{stroke:#dc2626;stroke-width:1.8;fill:none;marker-end:url(#arrow-red)}",
        ".node{fill:#f9fafb;stroke:#047857;stroke-width:1.5}",
        ".main{fill:#ecfeff;stroke:#0891b2}",
        ".label{font-family:Menlo,Consolas,monospace;font-size:13px;text-anchor:middle;dominant-baseline:middle;fill:#111827}",
        ".title{font-family:Arial,sans-serif;font-size:18px;font-weight:700;fill:#111827}",
        "</style>",
        '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#6b7280"/></marker>',
        '<marker id="arrow-red" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#dc2626"/></marker></defs>',
        '<text class="title" x="24" y="30">Call Graph</text>',
    ]
    for caller, callee in edges:
        x1, y1 = positions[caller]
        x2, y2 = positions[callee]
        if caller == callee:
            lines.append(f'<path class="self" d="M{x1 - 35},{y1 - 24} C{x1 - 90},{y1 - 95} {x1 + 90},{y1 - 95} {x1 + 35},{y1 - 24}"/>')
        else:
            control_y = y1 - 60
            lines.append(f'<path class="edge" d="M{x1 + 48},{y1} C{x1 + 80},{control_y} {x2 - 80},{control_y} {x2 - 48},{y2}"/>')
    for node, (x, y) in positions.items():
        cls = "node main" if node == "main" else "node"
        lines.append(f'<rect class="{cls}" x="{x - 58}" y="{y - 24}" width="116" height="48" rx="7"/>')
        lines.append(f'<text class="label" x="{x}" y="{y}">{html.escape(node)}</text>')
    lines.append("</svg>")
    return "\n".join(lines)


def render_stack_frames_html(
    frames: list[FrameInfo],
    edges: list[tuple[str, str]],
    call_events: list[dict[str, int | str]] | None = None,
    max_call_depth: int = 0,
) -> str:
    recursive_labels = {callee for caller, callee in edges if caller == callee}
    sections = []
    for frame in frames:
        rows = []
        for slot in sorted(frame.slots, key=lambda item: item.offset, reverse=True):
            offset = f"{slot.offset:+d}($fp)" if slot.offset else "0($fp)"
            rows.append(
                "<tr>"
                f"<td>{offset}</td>"
                f"<td>{html.escape(slot.name)}</td>"
                f"<td>{html.escape(slot.kind)}</td>"
                f"<td>{html.escape(slot.type_text)}</td>"
                f"<td>{slot.size_bytes}</td>"
                f"<td>{html.escape(slot.mode)}</td>"
                "</tr>"
            )
        recursive_note = (
            '<p class="warn">Recursive procedure: every call receives a fresh copy of this frame.</p>'
            if frame.label in recursive_labels
            else ""
        )
        sections.append(
            f"""
            <section class="card">
              <h2>{html.escape(frame.name)} <span>{html.escape(frame.label)}</span></h2>
              <p>argument bytes={frame.param_bytes}, local bytes={frame.local_bytes}</p>
              {recursive_note}
              <table>
                <thead><tr><th>Offset</th><th>Name</th><th>Kind</th><th>Type</th><th>Bytes</th><th>Mode</th></tr></thead>
                <tbody>{"".join(rows)}</tbody>
              </table>
              {render_frame_stack_div(frame)}
            </section>
            """
        )
    if not sections:
        sections.append('<section class="card"><p>No procedure frames: this program has no procedures.</p></section>')
    sections.append(render_recursive_edges(edges))
    if call_events is not None:
        sections.append(render_call_trace(call_events, max_call_depth))
    return wrap_html("SNL Stack Frames", "\n".join(sections))


def render_frame_stack_div(frame: FrameInfo) -> str:
    rows = []
    for slot in sorted(frame.slots, key=lambda item: item.offset, reverse=True):
        offset = f"{slot.offset:+d}" if slot.offset else "0"
        css = "runtime" if slot.kind == "runtime" else ("param" if slot.kind == "param" else "local")
        rows.append(
            f'<div class="slot {css}"><strong>{html.escape(slot.name)}</strong>'
            f'<span>{html.escape(slot.kind)} {html.escape(slot.mode)}</span><em>{offset}($fp)</em></div>'
        )
    return '<div class="frame-diagram">' + "\n".join(rows) + "</div>"


def render_recursive_edges(edges: list[tuple[str, str]]) -> str:
    recursive = sorted({callee for caller, callee in edges if caller == callee})
    if not recursive:
        return '<section class="card"><h2>Recursion Analysis</h2><p>No direct recursive calls detected in generated MIPS.</p></section>'
    items = "".join(f"<li>{html.escape(label)} calls itself; nested invocations are separated by $fp frames.</li>" for label in recursive)
    return f"""
    <section class="card">
      <h2>Recursion Analysis</h2>
      <ul>{items}</ul>
      <p>The prologue stores the caller frame pointer and return address, then moves $fp to the new frame.
      The epilogue restores both values, so each recursive level keeps independent parameters and locals.</p>
    </section>
    """


def render_call_trace(call_events: list[dict[str, int | str]], max_call_depth: int) -> str:
    if not call_events:
        return '<section class="card"><h2>Dynamic Call Trace</h2><p>No procedure calls executed.</p></section>'
    rows = []
    for index, event in enumerate(call_events[:120], 1):
        rows.append(
            "<tr>"
            f"<td>{index}</td>"
            f"<td>{html.escape(str(event['event']))}</td>"
            f"<td>{html.escape(str(event['target']))}</td>"
            f"<td>{event['depth']}</td>"
            f"<td>0x{int(event['sp']):08x}</td>"
            f"<td>0x{int(event['fp']):08x}</td>"
            "</tr>"
        )
    truncated = "<p>Trace truncated to first 120 events.</p>" if len(call_events) > 120 else ""
    return f"""
    <section class="card">
      <h2>Dynamic Call Trace</h2>
      <p>max procedure-call depth={max_call_depth}</p>
      {truncated}
      <table>
        <thead><tr><th>#</th><th>Event</th><th>Target</th><th>Depth</th><th>$sp</th><th>$fp</th></tr></thead>
        <tbody>{"".join(rows)}</tbody>
      </table>
    </section>
    """


def wrap_html(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{html.escape(title)}</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 28px; color: #111827; background: #f3f4f6; }}
    h1 {{ margin: 0 0 18px; }}
    h2 {{ margin: 0 0 8px; font-size: 18px; }}
    h2 span {{ color: #6b7280; font-family: Menlo, Consolas, monospace; font-size: 13px; }}
    .card {{ background: white; border: 1px solid #d1d5db; border-radius: 8px; padding: 18px; margin: 0 0 18px; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 14px; }}
    th, td {{ border-bottom: 1px solid #e5e7eb; padding: 8px 10px; text-align: left; vertical-align: top; }}
    th {{ background: #f9fafb; }}
    .warn {{ color: #b91c1c; font-weight: 700; }}
    .frame-diagram {{ margin-top: 14px; max-width: 420px; border: 1px solid #9ca3af; border-radius: 8px; overflow: hidden; }}
    .slot {{ display: grid; grid-template-columns: 1fr 1fr 80px; gap: 8px; padding: 8px 10px; border-bottom: 1px solid #e5e7eb; font-family: Menlo, Consolas, monospace; font-size: 13px; }}
    .slot:last-child {{ border-bottom: 0; }}
    .slot.param {{ background: #eff6ff; }}
    .slot.runtime {{ background: #fefce8; }}
    .slot.local {{ background: #ecfdf5; }}
    .slot em {{ color: #4b5563; font-style: normal; text-align: right; }}
    a {{ color: #0369a1; }}
  </style>
</head>
<body>
  <h1>{html.escape(title)}</h1>
  {body}
</body>
</html>
"""


def build_side_info_json(
    semantic: SNLSemanticAnalyzer,
    frames: list[FrameInfo],
    edges: list[tuple[str, str]],
    call_events: list[dict[str, int | str]] | None = None,
    max_call_depth: int = 0,
) -> dict:
    return {
        "scopes": [
            {
                "number": scope.number,
                "name": scope.name,
                "level": scope.level,
                "parent": scope.parent,
                "symbols": [
                    {
                        "kind": symbol.kind,
                        "name": symbol.name,
                        "type": symbol.type_display(),
                        "line": symbol.line,
                        "other": symbol.other_display(),
                    }
                    for symbol in scope.symbols.values()
                ],
            }
            for scope in semantic.table.scopes
        ],
        "frames": [
            {
                "name": frame.name,
                "label": frame.label,
                "local_bytes": frame.local_bytes,
                "param_bytes": frame.param_bytes,
                "slots": [slot.__dict__ for slot in frame.slots],
            }
            for frame in frames
        ],
        "call_edges": [{"caller": caller, "callee": callee} for caller, callee in edges],
        "dynamic_call_trace": call_events or [],
        "max_call_depth": max_call_depth,
    }


def render_index(out_dir: Path, generated: dict[str, Path], errors: list[str]) -> str:
    links = "\n".join(
        f'<li><a href="{html.escape(path.name)}">{html.escape(label)}</a></li>'
        for label, path in generated.items()
    )
    error_block = (
        "<section class='card'><h2>Front-End Errors</h2><pre>"
        + html.escape("\n".join(errors))
        + "</pre></section>"
        if errors
        else "<section class='card'><h2>Front-End</h2><p>No lexical, syntax, or semantic errors.</p></section>"
    )
    return wrap_html(
        "SNL Visualization Index",
        f"{error_block}<section class='card'><h2>Generated Files</h2><ul>{links}</ul></section>",
    )


def visualize_source(source: Path, out_dir: Path, inputs: list[str]) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = source.stem
    tokens, token_text, lex_errors = tokenize_source(source)
    tree, parse_report, semantic, semantic_report, frontend_errors = build_reports(tokens)
    errors = lex_errors + frontend_errors

    generated: dict[str, Path] = {}
    token_path = out_dir / f"{stem}.tokens"
    tree_path = out_dir / f"{stem}.tree"
    semantic_path = out_dir / f"{stem}.semantic"
    syntax_svg_path = out_dir / "syntax_tree.svg"
    symbols_html_path = out_dir / "symbol_tables.html"

    token_path.write_text(token_text + "\n", encoding="utf-8")
    tree_path.write_text(parse_report + "\n", encoding="utf-8")
    semantic_path.write_text(semantic_report + "\n", encoding="utf-8")
    syntax_svg_path.write_text(render_syntax_tree_svg(tree), encoding="utf-8")
    symbols_html_path.write_text(render_symbol_tables_html(semantic), encoding="utf-8")

    generated["Tokens"] = token_path
    generated["Syntax Report"] = tree_path
    generated["Semantic Report"] = semantic_path
    generated["Syntax Tree SVG"] = syntax_svg_path
    generated["Symbol Tables HTML"] = symbols_html_path

    frames: list[FrameInfo] = []
    edges: list[tuple[str, str]] = []
    if not errors:
        generator = SNLCodeGenerator(tokens)
        assembly = generator.generate()
        frames = generator.frame_infos
        edges = extract_call_edges(assembly)
        runner = MIPSRunner(assembly, inputs)
        result = runner.run()

        asm_path = out_dir / f"{stem}.asm"
        result_path = out_dir / f"{stem}.result"
        stack_path = out_dir / "stack_frames.html"
        graph_path = out_dir / "call_graph.svg"
        side_json_path = out_dir / "side_info.json"

        asm_path.write_text(assembly, encoding="utf-8")
        result_path.write_text(
            "Front End\nNo lexical, syntax, or semantic errors.\n\n"
            f"MIPS Assembly\n{asm_path}\n\nProgram Output\n{result}",
            encoding="utf-8",
        )
        stack_path.write_text(
            render_stack_frames_html(frames, edges, runner.call_events, runner.max_call_depth),
            encoding="utf-8",
        )
        graph_path.write_text(render_call_graph_svg(edges), encoding="utf-8")
        side_json_path.write_text(
            json.dumps(
                build_side_info_json(
                    semantic,
                    frames,
                    edges,
                    runner.call_events,
                    runner.max_call_depth,
                ),
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        generated["MIPS Assembly"] = asm_path
        generated["Run Result"] = result_path
        generated["Stack Frames HTML"] = stack_path
        generated["Call Graph SVG"] = graph_path
        generated["Side Info JSON"] = side_json_path

    index_path = out_dir / "index.html"
    index_path.write_text(render_index(out_dir, generated, errors), encoding="utf-8")
    generated["Index"] = index_path
    return generated


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate SNL compiler side-information visualizations.")
    parser.add_argument("source", type=Path, help="SNL source program")
    parser.add_argument("--out-dir", type=Path, default=Path("playground/snlcompiler/test/out/visualize"))
    parser.add_argument("--input", nargs="*", default=[], help="input values consumed by READ syscalls")
    args = parser.parse_args(argv)

    try:
        generated = visualize_source(args.source, args.out_dir, list(args.input))
    except (OSError, CodegenError, json.JSONDecodeError) as exc:
        print(f"snl_visualize.py: {exc}", file=sys.stderr)
        return 2

    print(f"Visualization index: {generated['Index']}")
    for label, path in generated.items():
        if label != "Index":
            print(f"{label}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

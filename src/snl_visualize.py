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


def build_assembly_listing(runner: MIPSRunner) -> list[dict[str, object]]:
    listing: list[dict[str, object]] = []
    for pc, instruction in enumerate(runner.instructions):
        listing.append(
            {
                "pc": pc,
                "labels": runner.instruction_labels[pc],
                "instruction": instruction,
                "source_line": runner.instruction_source_lines[pc],
            }
        )
    return listing


def render_execution_tutor_html(side_info: dict) -> str:
    payload = json.dumps(side_info, ensure_ascii=False).replace("</", "<\\/")
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SNL Execution Tutor</title>
  <style>
    :root {
      --bg: #fcfaf6;
      --panel: #ffffff;
      --line: #d8d6d0;
      --line-soft: #ece9e2;
      --text: #1f2328;
      --muted: #69707a;
      --executed: #9fd4aa;
      --next: #ec3c82;
      --highlight: #eef4ff;
      --active-frame: #dbe7f6;
      --shadow: 0 12px 30px rgba(28, 34, 39, 0.08);
      --mono: "SFMono-Regular", "Cascadia Code", Menlo, Consolas, monospace;
      --sans: "Avenir Next", "Segoe UI", sans-serif;
      --serif: "Iowan Old Style", Georgia, serif;
    }
    html, body { height: 100%; }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: var(--sans);
      min-height: 100vh;
      overflow: hidden;
    }
    .shell {
      max-width: 1680px;
      margin: 0 auto;
      padding: 18px 18px 24px;
      height: 100vh;
      display: grid;
      grid-template-rows: auto 1fr;
    }
    .topline {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 16px;
      margin-bottom: 14px;
      font-size: 14px;
      color: var(--muted);
    }
    .topline h1 {
      margin: 0;
      font-family: var(--serif);
      font-size: 34px;
      color: var(--text);
    }
    .topline .meta {
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      justify-content: flex-end;
    }
    .main {
      display: grid;
      grid-template-columns: minmax(430px, 0.9fr) minmax(430px, 1.1fr);
      gap: 18px;
      align-items: start;
      min-height: 0;
    }
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      box-shadow: var(--shadow);
      min-height: 0;
      overflow: hidden;
    }
    .left-panel {
      display: grid;
      grid-template-rows: auto 1fr auto;
      height: 100%;
    }
    .left-head {
      padding: 14px 16px 8px;
      border-bottom: 1px solid var(--line-soft);
      text-align: center;
    }
    .left-head h2 {
      margin: 0;
      font-size: 16px;
      font-family: var(--serif);
      font-weight: 600;
    }
    .left-head p {
      margin: 4px 0 0;
      color: var(--muted);
      font-size: 12px;
    }
    .code-pane {
      overflow: auto;
      padding: 8px 0 0;
      overscroll-behavior: contain;
    }
    .code-row {
      display: grid;
      grid-template-columns: 30px 38px 1fr;
      gap: 8px;
      align-items: center;
      min-height: 30px;
      padding: 0 12px;
      font-family: var(--mono);
      font-size: 12px;
      line-height: 1.45;
      white-space: pre-wrap;
    }
    .code-row.current {
      background: #fff5fa;
    }
    .code-row.last {
      background: #f6fbf6;
    }
    .arrow {
      display: flex;
      align-items: center;
      justify-content: center;
      height: 20px;
    }
    .arrow-mark {
      width: 24px;
      height: 8px;
      position: relative;
      border-radius: 999px;
      background: transparent;
    }
    .arrow-mark::after {
      content: "";
      position: absolute;
      top: -4px;
      right: -1px;
      border-top: 8px solid transparent;
      border-bottom: 8px solid transparent;
      border-left: 10px solid currentColor;
    }
    .arrow-mark.executed { color: var(--executed); background: currentColor; }
    .arrow-mark.next { color: var(--next); background: currentColor; }
    .line-no {
      color: #9aa0a6;
      text-align: right;
      user-select: none;
    }
    .code-text {
      color: var(--text);
      word-break: break-word;
    }
    .control-pane {
      padding: 12px 14px 14px;
      border-top: 1px solid var(--line-soft);
      display: grid;
      gap: 10px;
    }
    .legend {
      display: flex;
      flex-wrap: wrap;
      gap: 18px;
      font-size: 13px;
      color: var(--muted);
    }
    .legend-item {
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .legend .arrow-mark {
      width: 18px;
      height: 7px;
    }
    .scrubber input {
      width: 100%;
      accent-color: #4b6ea9;
    }
    .button-row {
      display: flex;
      gap: 8px;
      justify-content: center;
      flex-wrap: wrap;
    }
    button {
      border: 1px solid #9fa6ad;
      background: #ffffff;
      color: var(--text);
      padding: 7px 12px;
      font-size: 13px;
      border-radius: 8px;
      cursor: pointer;
    }
    button:hover {
      background: #f4f6f8;
    }
    .step-caption {
      text-align: center;
      font-size: 14px;
      color: var(--text);
    }
    .step-caption strong {
      font-size: 20px;
      margin-right: 6px;
    }
    .right-panel {
      display: grid;
      grid-template-rows: auto auto 1fr auto;
      height: 100%;
    }
    .runtime-toolbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 14px 18px 12px;
      border-bottom: 1px solid var(--line-soft);
      background: rgba(255, 255, 255, 0.72);
    }
    .runtime-toolbar h3 {
      margin: 0;
      font-size: 14px;
      font-weight: 600;
      color: #4e555e;
    }
    .runtime-tabs {
      display: inline-flex;
      gap: 8px;
      flex-wrap: wrap;
    }
    .runtime-tab {
      padding: 7px 12px;
      border-radius: 999px;
      border: 1px solid #b8bec5;
      background: #fff;
      color: var(--muted);
      font-size: 12px;
      letter-spacing: 0.02em;
      text-transform: uppercase;
    }
    .runtime-tab.active {
      background: #1f2937;
      border-color: #1f2937;
      color: #fff;
    }
    .io-grid {
      display: grid;
      grid-template-columns: 1fr;
      gap: 16px;
      padding: 14px 18px 16px;
      border-top: 1px solid var(--line-soft);
      background: rgba(255, 255, 255, 0.52);
    }
    .io-box h3,
    .runtime-head h3 {
      margin: 0 0 8px;
      font-size: 14px;
      font-weight: 600;
      color: #4e555e;
    }
    .io-view {
      min-height: 46px;
      max-height: 120px;
      overflow: auto;
      resize: vertical;
      border: 1px solid #c9c9c9;
      background: #fff;
      padding: 10px 12px;
      font-family: var(--mono);
      font-size: 14px;
      white-space: pre-wrap;
    }
    .runtime {
      padding: 10px 18px 18px;
      overflow: auto;
      min-height: 0;
      overscroll-behavior: contain;
    }
    .runtime-panel {
      display: none;
      min-height: 100%;
    }
    .runtime-panel.active {
      display: block;
    }
    .stack-column,
    .heap-column {
      min-height: 560px;
    }
    .stack-list {
      display: grid;
      gap: 16px;
      align-content: start;
    }
    .frame {
      border-left: 2px solid #c7cfd8;
      padding-left: 10px;
      background: transparent;
    }
    .frame.active {
      background: var(--active-frame);
      border: 1px solid #bfd0ea;
      padding: 10px 10px 10px 12px;
    }
    .frame-name {
      font-family: var(--mono);
      font-size: 14px;
      margin-bottom: 10px;
    }
    .slot-box {
      display: inline-grid;
      grid-template-columns: 1fr;
      gap: 2px;
      min-width: 96px;
      padding-left: 14px;
      border-left: 2px solid #c7c7c7;
      margin-left: 8px;
      background: transparent;
    }
    .slot-row {
      display: grid;
      grid-template-columns: auto 1fr;
      gap: 10px;
      align-items: baseline;
      margin-bottom: 10px;
      font-family: var(--mono);
      font-size: 14px;
    }
    .slot-name {
      color: var(--text);
    }
    .slot-type {
      color: var(--muted);
      font-size: 12px;
    }
    .slot-value {
      border-left: 2px solid #c7c7c7;
      padding-left: 8px;
      min-height: 22px;
    }
    .slot-lines {
      display: grid;
      gap: 3px;
    }
    .slot-main {
      color: var(--text);
    }
    .slot-raw,
    .slot-ref,
    .slot-current {
      color: var(--muted);
      font-size: 12px;
      line-height: 1.35;
    }
    .heap-card {
      border: 1px solid var(--line);
      margin-bottom: 14px;
      background: #fff;
    }
    .heap-card h4 {
      margin: 0;
      padding: 10px 12px;
      font-size: 14px;
      background: var(--highlight);
      border-bottom: 1px solid var(--line-soft);
      font-family: var(--mono);
    }
    .heap-table,
    .raw-table {
      width: 100%;
      border-collapse: collapse;
      font-family: var(--mono);
      font-size: 12px;
    }
    .heap-table td,
    .raw-table td,
    .raw-table th {
      padding: 7px 9px;
      border-bottom: 1px solid var(--line-soft);
      text-align: left;
      vertical-align: top;
    }
    .heap-table tr:last-child td,
    .raw-table tr:last-child td {
      border-bottom: 0;
    }
    .changed {
      background: #eef5ff;
    }
    .event-bar {
      margin: 0 18px 12px;
      padding: 10px 12px;
      border: 1px solid var(--line);
      background: #faf7f1;
      font-family: var(--mono);
      font-size: 12px;
      color: #4d545d;
    }
    .muted {
      color: var(--muted);
    }
    @media (max-width: 980px) {
      body {
        overflow: auto;
      }
      .shell {
        height: auto;
        min-height: 100vh;
      }
      .main {
        grid-template-columns: 1fr;
      }
      .left-panel,
      .right-panel {
        height: auto;
      }
    }
    @media (max-width: 760px) {
      .shell {
        padding: 12px;
      }
      .code-row {
        grid-template-columns: 30px 40px 1fr;
        gap: 8px;
        padding: 0 10px;
      }
    }
  </style>
</head>
<body>
  <div class="shell">
    <div class="topline">
      <h1>SNL Tutor</h1>
      <div class="meta" id="meta-strip"></div>
    </div>
    <div class="main">
      <section class="panel left-panel">
        <div class="left-head">
          <h2 id="program-title">SNL Program</h2>
          <p id="instruction-caption">current instruction</p>
        </div>
        <div class="code-pane" id="code-pane"></div>
        <div class="control-pane">
          <div class="legend">
            <div class="legend-item"><span class="arrow-mark executed"></span><span>line that just executed</span></div>
            <div class="legend-item"><span class="arrow-mark next"></span><span>next line to execute</span></div>
          </div>
          <div class="scrubber"><input id="step-range" type="range" min="0" value="0"></div>
          <div class="button-row">
            <button id="first-step" type="button">&lt;&lt; First</button>
            <button id="prev-step" type="button">&lt; Prev</button>
            <button id="play-toggle" type="button">Play</button>
            <button id="next-step" type="button">Next &gt;</button>
            <button id="last-step" type="button">Last &gt;&gt;</button>
          </div>
          <div class="step-caption"><strong id="step-number">0</strong><span id="step-caption">Step 0 of 0</span></div>
        </div>
      </section>
      <section class="panel right-panel">
        <div class="runtime-toolbar">
          <h3>Runtime View</h3>
          <div class="runtime-tabs" id="runtime-tabs">
            <button class="runtime-tab active" type="button" data-view="stack">Stack</button>
            <button class="runtime-tab" type="button" data-view="heap">Heap</button>
          </div>
        </div>
        <div class="event-bar" id="event-bar"></div>
        <div class="runtime">
          <div class="runtime-panel active" id="runtime-panel-stack">
            <div class="stack-column">
              <div class="stack-list" id="stack-list"></div>
            </div>
          </div>
          <div class="runtime-panel" id="runtime-panel-heap">
            <div class="heap-column">
              <div id="heap-list"></div>
              <table class="raw-table">
                <thead><tr><th>Address</th><th>Signed</th><th>Hex</th></tr></thead>
                <tbody id="raw-memory-body"></tbody>
              </table>
            </div>
          </div>
        </div>
        <div class="io-grid">
          <div class="io-box">
            <h3>User input processed so far:</h3>
            <div class="io-view" id="input-box"></div>
          </div>
          <div class="io-box">
            <h3>Print output:</h3>
            <div class="io-view" id="output-box"></div>
          </div>
        </div>
      </section>
    </div>
  </div>
  <script id="snl-trace-data" type="application/json">__DATA__</script>
  <script>
    const data = JSON.parse(document.getElementById("snl-trace-data").textContent);
    const trace = data.execution_trace || [];
    const assembly = data.assembly_listing || [];
    const sourceLines = data.source_lines || [];
    const frameIndex = new Map((data.frames || []).map((frame) => [frame.label, frame]));
    const state = { step: 0, playing: false, timer: null, runtimeView: "stack" };
    const memoryCache = new Map();

    const metaStrip = document.getElementById("meta-strip");
    const programTitle = document.getElementById("program-title");
    const instructionCaption = document.getElementById("instruction-caption");
    const codePane = document.getElementById("code-pane");
    const stepRange = document.getElementById("step-range");
    const stepNumber = document.getElementById("step-number");
    const stepCaption = document.getElementById("step-caption");
    const inputBox = document.getElementById("input-box");
    const outputBox = document.getElementById("output-box");
    const eventBar = document.getElementById("event-bar");
    const runtimeTabs = document.getElementById("runtime-tabs");
    const stackList = document.getElementById("stack-list");
    const heapList = document.getElementById("heap-list");
    const rawMemoryBody = document.getElementById("raw-memory-body");
    const playToggle = document.getElementById("play-toggle");

    stepRange.max = Math.max(0, trace.length - 1);

    function toUnsigned(value) { return Number(BigInt.asUintN(32, BigInt(value || 0))); }
    function toSigned(value) { return Number(BigInt.asIntN(32, BigInt(value || 0))); }
    function hex32(value) { return "0x" + toUnsigned(value).toString(16).padStart(8, "0"); }
    function escapeHtml(text) {
      return String(text).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
    }

    function buildMeta() {
      const bits = [
        "source: " + (data.source_name || "snippet.snl"),
        "steps: " + Math.max(0, trace.length - 1),
        "max depth: " + (data.max_call_depth || 0),
        "globals: " + ((data.data_layout || []).length),
      ];
      metaStrip.innerHTML = bits.map((item) => '<span>' + escapeHtml(item) + '</span>').join("");
      programTitle.textContent = data.source_name || "SNL Program";
    }

    function updateRuntimeView() {
      document.querySelectorAll(".runtime-tab").forEach((button) => {
        button.classList.toggle("active", button.dataset.view === state.runtimeView);
      });
      document.querySelectorAll(".runtime-panel").forEach((panel) => {
        panel.classList.toggle("active", panel.id === "runtime-panel-" + state.runtimeView);
      });
    }

    function buildCodePane() {
      codePane.innerHTML = sourceLines.map((lineText, index) => {
        const lineNumber = index + 1;
        return (
          '<div class="code-row" id="code-row-' + lineNumber + '">' +
            '<div class="arrow"></div>' +
            '<div class="line-no">' + lineNumber + '</div>' +
            '<div class="code-text">' + escapeHtml(lineText || " ") + '</div>' +
          '</div>'
        );
      }).join("");
    }

    function nearestCheckpoint(step) {
      const checkpoints = Object.keys(data.memory_checkpoints || {}).map(Number).sort((a, b) => a - b);
      let best = 0;
      for (const checkpoint of checkpoints) {
        if (checkpoint <= step) {
          best = checkpoint;
        } else {
          break;
        }
      }
      return best;
    }

    function getMemory(step) {
      if (memoryCache.has(step)) {
        return memoryCache.get(step);
      }
      const checkpoint = nearestCheckpoint(step);
      const base = new Map((data.memory_checkpoints[String(checkpoint)] || []).map(([address, value]) => [address, value]));
      for (let index = checkpoint + 1; index <= step; index += 1) {
        const snapshot = trace[index];
        if (!snapshot) {
          continue;
        }
        for (const [address, value] of snapshot.memory_writes || []) {
          base.set(address, value);
        }
      }
      memoryCache.set(step, base);
      return base;
    }

    function frameAddresses(snapshot, memory) {
      const addresses = [];
      const seen = new Set();
      let fp = toUnsigned(snapshot.registers["$fp"]);
      while (fp && memory.has(fp) && memory.has(fp + 4) && !seen.has(fp) && addresses.length < snapshot.call_stack.length) {
        addresses.push(fp);
        seen.add(fp);
        fp = toUnsigned(memory.get(fp) || 0);
      }
      return addresses;
    }

    function buildRuntimeFrames(snapshot, memory) {
      const addresses = frameAddresses(snapshot, memory);
      const labels = snapshot.call_stack.slice(0, addresses.length);
      return addresses.map((fp, index) => {
        const label = labels[labels.length - 1 - index];
        const meta = frameIndex.get(label);
        const slots = (meta?.slots || []).filter((slot) => slot.kind !== "runtime").map((slot) => ({
          ...slot,
          address: fp + slot.offset,
          value: memory.has(fp + slot.offset) ? memory.get(fp + slot.offset) : null,
        }));
        return {
          label,
          name: meta?.name || label,
          fp,
          slots,
        };
      });
    }

    function lookupDataLabel(address) {
      for (const item of data.data_layout || []) {
        const start = Number(item.start);
        const end = start + Number(item.size_bytes);
        if (address >= start && address < end) {
          const offset = address - start;
          return item.label + (offset ? " +" + offset : "");
        }
      }
      return "";
    }

    function buildConsumedInputs(step) {
      const values = [];
      for (let index = 0; index <= step; index += 1) {
        const event = trace[index]?.event;
        if (!event || event.event !== "syscall") {
          continue;
        }
        if (event.code === 5 && String(event.detail).startsWith("read-int ")) {
          values.push(String(event.detail).replace("read-int ", ""));
        }
        if (event.code === 12 && String(event.detail).startsWith("read-char/int ")) {
          values.push(String(event.detail).replace("read-char/int ", ""));
        }
      }
      return values;
    }

    function sourceLineForPc(pc) {
      if (pc === null || pc === undefined || pc < 0 || pc >= assembly.length) {
        return null;
      }
      for (let distance = 0; distance < assembly.length; distance += 1) {
        const forward = pc + distance;
        if (forward < assembly.length && assembly[forward] && assembly[forward].source_line) {
          return Number(assembly[forward].source_line);
        }
        const backward = pc - distance;
        if (distance > 0 && backward >= 0 && assembly[backward] && assembly[backward].source_line) {
          return Number(assembly[backward].source_line);
        }
      }
      return null;
    }

    function keepRowVisible(row) {
      if (!row) {
        return;
      }
      const padding = 48;
      const rowTop = row.offsetTop;
      const rowBottom = rowTop + row.offsetHeight;
      const viewTop = codePane.scrollTop;
      const viewBottom = viewTop + codePane.clientHeight;
      if (rowTop < viewTop + padding) {
        codePane.scrollTop = Math.max(0, rowTop - padding);
        return;
      }
      if (rowBottom > viewBottom - padding) {
        codePane.scrollTop = Math.max(0, rowBottom - codePane.clientHeight + padding);
      }
    }

    function renderCode(snapshot) {
      document.querySelectorAll(".code-row").forEach((row) => {
        row.classList.remove("current");
        row.classList.remove("last");
        const arrow = row.querySelector(".arrow");
        arrow.innerHTML = "";
      });
      const currentLine = sourceLineForPc(snapshot.pc);
      const lastLine = sourceLineForPc(snapshot.last_pc);
      const current = currentLine !== null ? document.getElementById("code-row-" + currentLine) : null;
      const last = lastLine !== null ? document.getElementById("code-row-" + lastLine) : null;
      if (last) {
        last.classList.add("last");
        last.querySelector(".arrow").innerHTML = '<span class="arrow-mark executed"></span>';
      }
      if (current) {
        current.classList.add("current");
        current.querySelector(".arrow").innerHTML = '<span class="arrow-mark next"></span>';
        keepRowVisible(current);
      }
      const currentInstruction = snapshot.instruction || "<no instruction>";
      instructionCaption.textContent = currentLine === null
        ? "runtime event mapped from MIPS: " + currentInstruction
        : "line " + currentLine + " mapped from MIPS: " + currentInstruction;
    }

    function renderStack(snapshot, memory) {
      const frames = buildRuntimeFrames(snapshot, memory);
      if (!frames.length) {
        stackList.innerHTML = '<div class="muted">No active procedure frames yet.</div>';
        return;
      }
      const changed = new Set((snapshot.memory_writes || []).map(([address]) => address));
      stackList.innerHTML = frames.map((frame, index) => {
        const rows = frame.slots.map((slot) => {
          let valueHtml = "";
          if (slot.mode === "var" && slot.value !== null && slot.value !== undefined) {
            const targetAddress = toUnsigned(slot.value);
            const targetLabel = lookupDataLabel(targetAddress) || hex32(targetAddress);
            const targetValue = memory.has(targetAddress) ? memory.get(targetAddress) : null;
            const targetText = targetValue === null ? "unavailable" : String(toSigned(targetValue));
            const targetHex = targetValue === null ? "" : " " + hex32(targetValue);
            valueHtml =
              '<div class="slot-lines">' +
                '<div class="slot-main">ref ' + escapeHtml(hex32(slot.value)) + "</div>" +
                '<div class="slot-ref">-&gt; ' + escapeHtml(targetLabel) + "</div>" +
                '<div class="slot-current">value ' + escapeHtml(targetText + targetHex) + "</div>" +
              "</div>";
          } else {
            const value = slot.value === null || slot.value === undefined ? "unset" : String(toSigned(slot.value));
            valueHtml =
              '<div class="slot-lines">' +
                '<div class="slot-main">' + escapeHtml(value) + "</div>" +
                (
                  slot.value === null || slot.value === undefined
                    ? ""
                    : '<div class="slot-raw">' + escapeHtml(hex32(slot.value)) + "</div>"
                ) +
              "</div>";
          }
          const changedClass = changed.has(slot.address) ? " changed" : "";
          return (
            '<div class="slot-row' + changedClass + '">' +
              '<div><div class="slot-name">' + escapeHtml(slot.name) + '</div><div class="slot-type">' + escapeHtml(slot.type_text) + '</div></div>' +
              '<div class="slot-value">' + valueHtml + '</div>' +
            '</div>'
          );
        }).join("");
        return (
          '<div class="frame' + (index === 0 ? " active" : "") + '">' +
            '<div class="frame-name">' + escapeHtml(frame.name) + "</div>" +
            '<div class="slot-box">' + rows + '</div>' +
          "</div>"
        );
      }).join("");
    }

    function renderHeap(snapshot, memory) {
      const changed = new Set((snapshot.memory_writes || []).map(([address]) => address));
      const cards = [];
      for (const item of data.data_layout || []) {
        const rows = [];
        const words = Number(item.words || 0);
        for (let index = 0; index < words; index += 1) {
          const address = Number(item.start) + index * 4;
          const value = memory.has(address) ? memory.get(address) : 0;
          rows.push(
            '<tr class="' + (changed.has(address) ? "changed" : "") + '">' +
              '<td>' + escapeHtml(words > 1 ? "[" + index + "]" : "value") + '</td>' +
              '<td>' + escapeHtml(String(toSigned(value))) + '</td>' +
              '<td>' + escapeHtml(hex32(value)) + '</td>' +
            '</tr>'
          );
        }
        cards.push(
          '<div class="heap-card">' +
            '<h4>' + escapeHtml(item.label) + '</h4>' +
            '<table class="heap-table"><tbody>' + rows.join("") + '</tbody></table>' +
          '</div>'
        );
      }
      heapList.innerHTML = cards.length ? cards.join("") : '<div class="muted">No global data segment entries.</div>';

      const stackTop = toUnsigned(snapshot.registers["$sp"]);
      const rawRows = Array.from(memory.entries())
        .filter(([address]) => address >= stackTop - 48 && address <= 0x80000000)
        .sort((a, b) => b[0] - a[0])
        .slice(0, 18)
        .map(([address, value]) =>
          '<tr class="' + (changed.has(address) ? "changed" : "") + '">' +
            '<td>' + escapeHtml(hex32(address)) + '</td>' +
            '<td>' + escapeHtml(String(toSigned(value))) + '</td>' +
            '<td>' + escapeHtml(hex32(value)) + '</td>' +
          '</tr>'
        );
      rawMemoryBody.innerHTML = rawRows.length ? rawRows.join("") : '<tr><td colspan="3" class="muted">No nearby raw memory.</td></tr>';
    }

    function renderEvent(snapshot) {
      const pieces = [
        "current: " + snapshot.instruction,
        "pc=" + snapshot.pc,
      ];
      if (snapshot.last_instruction) {
        pieces.push("just executed: " + snapshot.last_instruction);
      }
      if (snapshot.event) {
        if (snapshot.event.event === "call" || snapshot.event.event === "return") {
          pieces.push(snapshot.event.event + " " + snapshot.event.target + " depth=" + snapshot.event.depth);
        } else if (snapshot.event.event === "syscall") {
          pieces.push("syscall " + snapshot.event.code + " " + snapshot.event.detail);
        }
      }
      eventBar.textContent = pieces.join(" | ");
    }

    function render() {
      const snapshot = trace[state.step];
      if (!snapshot) {
        return;
      }
      const memory = getMemory(state.step);
      const consumedInputs = buildConsumedInputs(state.step);

      stepRange.value = state.step;
      stepNumber.textContent = String(state.step);
      stepCaption.textContent = "Step " + state.step + " of " + Math.max(0, trace.length - 1);
      inputBox.textContent = consumedInputs.length ? consumedInputs.join("\\n") : "<no input consumed yet>";
      outputBox.textContent = snapshot.output || "<no output yet>";

      renderCode(snapshot);
      renderEvent(snapshot);
      renderStack(snapshot, memory);
      renderHeap(snapshot, memory);
    }

    function setStep(nextStep) {
      const pageScrollTop = window.scrollY;
      state.step = Math.max(0, Math.min(trace.length - 1, nextStep));
      render();
      if (window.scrollY !== pageScrollTop) {
        window.scrollTo(0, pageScrollTop);
      }
    }

    function togglePlay() {
      state.playing = !state.playing;
      playToggle.textContent = state.playing ? "Pause" : "Play";
      if (!state.playing) {
        clearInterval(state.timer);
        state.timer = null;
        return;
      }
      state.timer = setInterval(() => {
        if (state.step >= trace.length - 1) {
          togglePlay();
          return;
        }
        setStep(state.step + 1);
      }, 420);
    }

    document.getElementById("first-step").addEventListener("click", () => setStep(0));
    document.getElementById("prev-step").addEventListener("click", () => setStep(state.step - 1));
    document.getElementById("next-step").addEventListener("click", () => setStep(state.step + 1));
    document.getElementById("last-step").addEventListener("click", () => setStep(trace.length - 1));
    playToggle.addEventListener("click", togglePlay);
    stepRange.addEventListener("input", (event) => setStep(Number(event.target.value)));
    runtimeTabs.querySelectorAll(".runtime-tab").forEach((button) => {
      button.addEventListener("click", () => {
        state.runtimeView = button.dataset.view || "stack";
        updateRuntimeView();
      });
    });

    buildMeta();
    buildCodePane();
    updateRuntimeView();
    render();
    try {
      if (window.parent && window.parent !== window) {
        window.parent.postMessage(
          {
            type: "snl-tutor-ready",
            source_name: data.source_name || "snippet.snl",
            source_lines: sourceLines.length,
            steps: Math.max(0, trace.length - 1),
          },
          "*",
        );
      }
    } catch (error) {
      console.warn("Failed to notify parent tutor host", error);
    }
  </script>
</body>
</html>
""".replace("__DATA__", payload)


def build_side_info_json(
    source: Path,
    semantic: SNLSemanticAnalyzer,
    runner: MIPSRunner,
    assembly: str,
    frames: list[FrameInfo],
    edges: list[tuple[str, str]],
) -> dict:
    source_text = source.read_text(encoding="utf-8")
    return {
        "source_name": source.name,
        "source_text": source_text,
        "source_lines": source_text.splitlines(),
        "assembly": assembly,
        "assembly_listing": build_assembly_listing(runner),
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
        "data_layout": runner.data_layout,
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
        "dynamic_call_trace": runner.call_events,
        "execution_trace": runner.execution_trace,
        "memory_checkpoints": runner.memory_checkpoints,
        "program_output": "".join(runner.output),
        "max_call_depth": runner.max_call_depth,
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


def visualize_source(source: Path, out_dir: Path, inputs: list[str], max_steps: int = 100000) -> dict[str, Path]:
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
        runner = MIPSRunner(assembly, inputs, max_steps=max_steps)
        result = runner.run()

        asm_path = out_dir / f"{stem}.asm"
        result_path = out_dir / f"{stem}.result"
        stack_path = out_dir / "stack_frames.html"
        graph_path = out_dir / "call_graph.svg"
        side_json_path = out_dir / "side_info.json"
        tutor_path = out_dir / "execution_tutor.html"

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
        side_info = build_side_info_json(
            source,
            semantic,
            runner,
            assembly,
            frames,
            edges,
        )
        side_json_path.write_text(
            json.dumps(side_info, ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
        tutor_path.write_text(render_execution_tutor_html(side_info), encoding="utf-8")

        generated["MIPS Assembly"] = asm_path
        generated["Run Result"] = result_path
        generated["Stack Frames HTML"] = stack_path
        generated["Call Graph SVG"] = graph_path
        generated["Side Info JSON"] = side_json_path
        generated["Execution Tutor HTML"] = tutor_path

    index_path = out_dir / "index.html"
    index_path.write_text(render_index(out_dir, generated, errors), encoding="utf-8")
    generated["Index"] = index_path
    return generated


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate SNL compiler side-information visualizations.")
    parser.add_argument("source", type=Path, help="SNL source program")
    parser.add_argument("--out-dir", type=Path, default=Path("test/out/visualize"))
    parser.add_argument("--input", nargs="*", default=[], help="input values consumed by READ syscalls")
    parser.add_argument("--max-steps", type=int, default=100000, help="maximum MIPS instructions to execute")
    args = parser.parse_args(argv)

    try:
        generated = visualize_source(args.source, args.out_dir, list(args.input), max_steps=args.max_steps)
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

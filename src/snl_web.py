#!/usr/bin/env python3
"""Local web UI for the SNL compiler."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from snl_codegen import CodegenError
from snl_visualize import visualize_source


@dataclass(frozen=True)
class ExampleProgram:
    name: str
    content: str


def collect_examples(project_root: Path) -> list[ExampleProgram]:
    examples_dir = project_root / "test" / "in"
    examples: list[ExampleProgram] = []
    for path in sorted(examples_dir.glob("*.snl")):
        examples.append(ExampleProgram(path.name, path.read_text(encoding="utf-8")))
    return examples


def parse_inputs(raw: str) -> list[str]:
    return [item for item in raw.replace("\r", "\n").split() if item]


def read_optional(path: Path | None) -> str:
    if path is None or not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def make_run_id() -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{uuid.uuid4().hex[:8]}"


def relative_asset_url(run_id: str, path: Path | None) -> str | None:
    if path is None or not path.exists():
        return None
    return f"/runs/{run_id}/{path.name}"


def trim_old_runs(runs_dir: Path, keep: int = 25) -> None:
    run_dirs = [path for path in runs_dir.iterdir() if path.is_dir()]
    run_dirs.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    for stale in run_dirs[keep:]:
        shutil.rmtree(stale, ignore_errors=True)


def render_app(examples: list[ExampleProgram]) -> str:
    examples_payload = json.dumps(
        [{"name": example.name, "content": example.content} for example in examples],
        ensure_ascii=False,
    ).replace("</", "<\\/")
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SNL Compiler Web</title>
  <style>
    :root {
      --bg: #f6f1e7;
      --panel: rgba(255, 250, 244, 0.94);
      --panel-strong: #fffdf8;
      --line: #d9c7ae;
      --text: #1c2429;
      --muted: #66727a;
      --accent: #0f766e;
      --accent-soft: #ddf3ee;
      --accent-2: #b45309;
      --shadow: 0 20px 44px rgba(69, 46, 21, 0.12);
      --mono: "SFMono-Regular", "Cascadia Code", Menlo, Consolas, monospace;
      --serif: "Iowan Old Style", "Palatino Linotype", Georgia, serif;
    }

    * { box-sizing: border-box; }

    body {
      margin: 0;
      color: var(--text);
      background:
        radial-gradient(circle at top left, rgba(15, 118, 110, 0.18), transparent 28%),
        radial-gradient(circle at top right, rgba(180, 83, 9, 0.13), transparent 22%),
        linear-gradient(180deg, #fbf7f0 0%, var(--bg) 100%);
      font-family: "Avenir Next", "Segoe UI", sans-serif;
    }

    .shell {
      max-width: 1760px;
      margin: 0 auto;
      padding: 24px;
    }

    .hero {
      display: grid;
      gap: 16px;
      padding: 24px 28px;
      border: 1px solid rgba(255, 255, 255, 0.55);
      border-radius: 28px;
      background:
        linear-gradient(135deg, rgba(255, 252, 248, 0.96), rgba(250, 244, 235, 0.92)),
        linear-gradient(135deg, rgba(15, 118, 110, 0.07), rgba(180, 83, 9, 0.05));
      box-shadow: var(--shadow);
    }

    .hero h1 {
      margin: 0;
      font-family: var(--serif);
      font-size: clamp(32px, 4vw, 54px);
      line-height: 1;
      letter-spacing: -0.025em;
    }

    .hero p {
      margin: 0;
      max-width: 980px;
      color: var(--muted);
      font-size: 15px;
      line-height: 1.7;
    }

    .toolbar {
      display: grid;
      grid-template-columns: 1.2fr 0.8fr;
      gap: 18px;
      margin-top: 20px;
    }

    .card {
      border: 1px solid rgba(217, 199, 174, 0.95);
      border-radius: 24px;
      background: var(--panel);
      box-shadow: var(--shadow);
      overflow: hidden;
    }

    .card-head {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      padding: 16px 18px;
      border-bottom: 1px solid rgba(217, 199, 174, 0.8);
      background: linear-gradient(180deg, rgba(255,255,255,0.76), rgba(255,250,244,0.66));
    }

    .card-head h2 {
      margin: 0;
      font-size: 16px;
      font-family: var(--serif);
    }

    .controls {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      padding: 16px 18px 18px;
      align-items: center;
    }

    select, input, textarea, button {
      font: inherit;
    }

    select, input[type="text"] {
      width: 100%;
      padding: 10px 12px;
      border-radius: 14px;
      border: 1px solid var(--line);
      background: rgba(255,255,255,0.9);
      color: var(--text);
    }

    button {
      border: 1px solid rgba(15, 118, 110, 0.26);
      background: linear-gradient(180deg, #fff, #eef9f6);
      color: var(--text);
      border-radius: 999px;
      padding: 10px 14px;
      font-weight: 600;
      cursor: pointer;
    }

    button.primary {
      background: linear-gradient(180deg, #14b8a6, #0f766e);
      color: white;
      border-color: rgba(15, 118, 110, 0.5);
    }

    button:hover {
      border-color: rgba(15, 118, 110, 0.5);
    }

    .grid-two {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
      width: 100%;
    }

    .main {
      display: grid;
      grid-template-columns: 1.05fr 0.95fr;
      gap: 18px;
      margin-top: 20px;
      align-items: start;
    }

    .editor-wrap {
      display: grid;
      gap: 12px;
      padding: 16px 18px 18px;
    }

    textarea {
      width: 100%;
      min-height: 650px;
      resize: vertical;
      border-radius: 20px;
      border: 1px solid var(--line);
      background: #171f25;
      color: #f8fafc;
      padding: 18px;
      line-height: 1.6;
      font-family: var(--mono);
      font-size: 14px;
      tab-size: 2;
    }

    .status-box {
      padding: 14px 16px;
      border-radius: 18px;
      background: linear-gradient(180deg, rgba(15, 118, 110, 0.09), rgba(15, 118, 110, 0.04));
      border: 1px solid rgba(15, 118, 110, 0.14);
      color: var(--muted);
      line-height: 1.6;
      min-height: 84px;
    }

    .status-box.error {
      background: linear-gradient(180deg, rgba(185, 28, 28, 0.08), rgba(185, 28, 28, 0.04));
      border-color: rgba(185, 28, 28, 0.16);
      color: #991b1b;
    }

    .status-box.ok {
      background: linear-gradient(180deg, rgba(15, 118, 110, 0.12), rgba(15, 118, 110, 0.05));
      border-color: rgba(15, 118, 110, 0.22);
      color: #0f766e;
    }

    .asset-links {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-top: 10px;
    }

    .asset-links a {
      text-decoration: none;
      color: var(--accent);
      font-size: 13px;
      padding: 7px 10px;
      border-radius: 999px;
      background: var(--accent-soft);
      border: 1px solid rgba(15, 118, 110, 0.15);
    }

    .results {
      display: grid;
      gap: 18px;
    }

    .preview {
      padding: 12px 12px 16px;
      display: grid;
      gap: 10px;
    }

    iframe {
      width: 100%;
      height: 520px;
      border: 1px solid var(--line);
      border-radius: 20px;
      background: white;
    }

    .preview-meta {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.6;
    }

    .preview-meta a {
      color: var(--accent);
      text-decoration: none;
      font-weight: 600;
    }

    .tabs {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      padding: 12px 18px 0;
    }

    .tab-btn.active {
      background: linear-gradient(180deg, #14b8a6, #0f766e);
      color: white;
      border-color: rgba(15, 118, 110, 0.46);
    }

    .tab-panel {
      display: none;
      padding: 14px 18px 18px;
    }

    .tab-panel.active {
      display: block;
    }

    pre {
      margin: 0;
      white-space: pre-wrap;
      word-break: break-word;
      font-family: var(--mono);
      font-size: 13px;
      line-height: 1.6;
      border-radius: 18px;
      padding: 16px;
      background: #171f25;
      color: #eef2ff;
      max-height: 520px;
      overflow: auto;
    }

    .hint {
      color: var(--muted);
      font-size: 13px;
      line-height: 1.6;
    }

    .meta {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }

    @media (max-width: 1380px) {
      .main, .toolbar {
        grid-template-columns: 1fr;
      }
    }

    @media (max-width: 760px) {
      .shell {
        padding: 14px;
      }

      .grid-two {
        grid-template-columns: 1fr;
      }

      textarea {
        min-height: 460px;
      }
    }
  </style>
</head>
<body>
  <div class="shell">
    <section class="hero">
      <h1>SNL Compiler Web</h1>
      <p>本地零依赖 Web 调试台。左侧输入 SNL 代码会自动防抖编译，右侧同步显示词法、语法、语义、MIPS 结果，并嵌入执行调试页，方便你后续做优化和回归验证。</p>
      <div class="meta">
        <span>Auto Run</span>
        <span>Syntax Tree</span>
        <span>Semantic Table</span>
        <span>MIPS Runner</span>
        <span>Execution Tutor</span>
      </div>
    </section>

    <section class="toolbar">
      <section class="card">
        <div class="card-head">
          <h2>运行控制</h2>
          <span class="hint">修改代码后约 700ms 自动触发</span>
        </div>
        <div class="controls">
          <div class="grid-two">
            <label>
              <div class="hint">样例程序</div>
              <select id="example-select"></select>
            </label>
            <label>
              <div class="hint">READ 输入值</div>
              <input id="input-values" type="text" placeholder="例如: 1 2 3 或 A">
            </label>
          </div>
          <button class="primary" id="run-btn" type="button">立即运行</button>
          <button id="reset-btn" type="button">重载当前样例</button>
        </div>
      </section>

      <section class="card">
        <div class="card-head">
          <h2>运行状态</h2>
          <span class="hint">最近一次编译</span>
        </div>
        <div class="controls" style="display:block">
          <div id="status-box" class="status-box">等待输入。</div>
          <div id="asset-links" class="asset-links"></div>
        </div>
      </section>
    </section>

    <section class="main">
      <section class="card">
        <div class="card-head">
          <h2>代码编辑器</h2>
          <span class="hint">直接粘贴或改样例</span>
        </div>
        <div class="editor-wrap">
          <textarea id="source-editor" spellcheck="false"></textarea>
        </div>
      </section>

      <section class="results">
        <section class="card">
          <div class="card-head">
            <h2>执行调试页</h2>
            <span class="hint">直接载入生成的 tutor 页面，而不是样式模拟</span>
          </div>
          <div class="preview">
            <div class="preview-meta">
              <span id="preview-status">等待运行结果。</span>
              <a id="preview-link" href="about:blank" target="_blank" rel="noreferrer">新窗口打开 Tutor</a>
            </div>
            <iframe id="preview-frame" title="SNL execution preview"></iframe>
          </div>
        </section>

        <section class="card">
          <div class="card-head">
            <h2>编译结果</h2>
            <span class="hint">切换标签查看各阶段输出</span>
          </div>
          <div class="tabs" id="tabs"></div>
          <div class="tab-panel active" data-tab="result"><pre id="panel-result">Program Output will appear here.</pre></div>
          <div class="tab-panel" data-tab="tokens"><pre id="panel-tokens"></pre></div>
          <div class="tab-panel" data-tab="tree"><pre id="panel-tree"></pre></div>
          <div class="tab-panel" data-tab="semantic"><pre id="panel-semantic"></pre></div>
          <div class="tab-panel" data-tab="asm"><pre id="panel-asm"></pre></div>
        </section>
      </section>
    </section>
  </div>

  <script id="snl-examples" type="application/json">__EXAMPLES__</script>
  <script>
    const examples = JSON.parse(document.getElementById("snl-examples").textContent);
    const exampleSelect = document.getElementById("example-select");
    const sourceEditor = document.getElementById("source-editor");
    const inputValues = document.getElementById("input-values");
    const runBtn = document.getElementById("run-btn");
    const resetBtn = document.getElementById("reset-btn");
    const statusBox = document.getElementById("status-box");
    const assetLinks = document.getElementById("asset-links");
    const previewFrame = document.getElementById("preview-frame");
    const previewStatus = document.getElementById("preview-status");
    const previewLink = document.getElementById("preview-link");
    const tabs = document.getElementById("tabs");
    const panels = {
      result: document.getElementById("panel-result"),
      tokens: document.getElementById("panel-tokens"),
      tree: document.getElementById("panel-tree"),
      semantic: document.getElementById("panel-semantic"),
      asm: document.getElementById("panel-asm"),
    };

    const tabLabels = {
      result: "运行结果",
      tokens: "Tokens",
      tree: "Syntax Tree",
      semantic: "Semantic",
      asm: "MIPS ASM",
    };

    const state = {
      timer: null,
      inFlight: false,
      rerunRequested: false,
      activeExample: examples[0]?.name || "",
      previewUrl: "",
      previewReadyTimer: null,
    };

    function setStatus(message, isError = false) {
      statusBox.textContent = message;
      statusBox.classList.toggle("error", isError);
      statusBox.classList.toggle("ok", !isError);
    }

    function buildTabs() {
      tabs.innerHTML = Object.entries(tabLabels).map(([id, label], index) => (
        '<button class="tab-btn' + (index === 0 ? ' active' : '') + '" type="button" data-tab="' + id + '">' + label + '</button>'
      )).join("");
      tabs.querySelectorAll(".tab-btn").forEach((button) => {
        button.addEventListener("click", () => activateTab(button.dataset.tab));
      });
    }

    function activateTab(id) {
      tabs.querySelectorAll(".tab-btn").forEach((button) => {
        button.classList.toggle("active", button.dataset.tab === id);
      });
      document.querySelectorAll(".tab-panel").forEach((panel) => {
        panel.classList.toggle("active", panel.dataset.tab === id);
      });
    }

    function fillExamples() {
      exampleSelect.innerHTML = examples.map((example) => (
        '<option value="' + example.name + '">' + example.name + '</option>'
      )).join("");
      if (examples.length) {
        sourceEditor.value = examples[0].content;
      }
    }

    function loadSelectedExample() {
      const example = examples.find((item) => item.name === exampleSelect.value);
      if (!example) {
        return;
      }
      state.activeExample = example.name;
      sourceEditor.value = example.content;
      scheduleRun();
    }

    function loadPreview(previewUrl) {
      state.previewUrl = previewUrl || "";
      clearTimeout(state.previewReadyTimer);
      if (!state.previewUrl) {
        previewLink.href = "about:blank";
        previewFrame.src = "about:blank";
        previewStatus.textContent = "当前没有可载入的 Tutor 页面。";
        return;
      }
      const runtimeUrl = state.previewUrl + (state.previewUrl.includes("?") ? "&" : "?") + "t=" + Date.now();
      previewLink.href = runtimeUrl;
      previewStatus.textContent = "正在载入 Tutor...";
      previewFrame.src = runtimeUrl;
      state.previewReadyTimer = setTimeout(() => {
        previewStatus.textContent = "Tutor 页面已打开，但还没有返回执行数据。你也可以点右侧链接单独打开核对。";
      }, 2500);
    }

    function updateAssets(assets) {
      const entries = Object.entries(assets || {}).filter(([, url]) => url);
      assetLinks.innerHTML = entries.map(([label, url]) => (
        '<a href="' + url + '" target="_blank" rel="noreferrer">' + label + '</a>'
      )).join("");
      const previewUrl = assets.execution_tutor || assets.index || "";
      loadPreview(previewUrl);
    }

    function updatePanels(result) {
      panels.tokens.textContent = result.reports.tokens || "";
      panels.tree.textContent = result.reports.tree || "";
      panels.semantic.textContent = result.reports.semantic || "";
      panels.asm.textContent = result.reports.asm || "";
      panels.result.textContent = result.reports.result || result.message || "";
    }

    async function checkBackend() {
      try {
        const response = await fetch("/api/health");
        const payload = await response.json();
        if (!response.ok || payload.status !== "ok") {
          throw new Error(payload.message || "backend unavailable");
        }
        setStatus("后端已连接：" + payload.message);
        return true;
      } catch (error) {
        setStatus("后端未连接。请用 `python3 compiler.py web --host 127.0.0.1 --port 8000` 启动，再访问当前页面。", true);
        return false;
      }
    }

    async function runCompiler() {
      if (state.inFlight) {
        state.rerunRequested = true;
        return;
      }
      state.inFlight = true;
      state.rerunRequested = false;
      setStatus("编译中，请稍候...");
      try {
        const response = await fetch("/api/run", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            source: sourceEditor.value,
            inputs: inputValues.value,
            example_name: state.activeExample,
          }),
        });
        const payload = await response.json();
        if (!response.ok || payload.status !== "ok") {
          throw new Error(payload.message || "Compiler request failed");
        }
        updatePanels(payload);
        updateAssets(payload.assets);
        setStatus("编译完成。运行目录: " + payload.run_dir);
      } catch (error) {
        panels.result.textContent = String(error.message || error);
        setStatus("编译失败: " + String(error.message || error), true);
        updateAssets({});
      } finally {
        state.inFlight = false;
        if (state.rerunRequested) {
          runCompiler();
        }
      }
    }

    function scheduleRun() {
      clearTimeout(state.timer);
      state.timer = setTimeout(runCompiler, 700);
    }

    window.addEventListener("message", (event) => {
      const payload = event.data || {};
      if (payload.type !== "snl-tutor-ready") {
        return;
      }
      if (previewFrame.contentWindow && event.source !== previewFrame.contentWindow) {
        return;
      }
      clearTimeout(state.previewReadyTimer);
      previewStatus.textContent =
        "Tutor 已就绪：" +
        String(payload.source_name || "snippet.snl") +
        "，" +
        String(payload.source_lines || 0) +
        " 行源码，" +
        String(payload.steps || 0) +
        " 步执行轨迹。";
    });

    exampleSelect.addEventListener("change", loadSelectedExample);
    sourceEditor.addEventListener("input", scheduleRun);
    inputValues.addEventListener("input", scheduleRun);
    runBtn.addEventListener("click", runCompiler);
    resetBtn.addEventListener("click", loadSelectedExample);

    buildTabs();
    fillExamples();
    checkBackend().then((ok) => {
      if (ok) {
        runCompiler();
      }
    });
  </script>
</body>
</html>
""".replace("__EXAMPLES__", examples_payload)


class SNLWebHandler(BaseHTTPRequestHandler):
    server_version = "SNLCompilerWeb/1.0"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self.serve_html(self.server.app_html)
            return
        if parsed.path == "/api/health":
            self.send_json({"status": "ok", "message": "SNL compiler backend is running"})
            return
        if parsed.path.startswith("/runs/"):
            self.serve_run_asset(parsed.path)
            return
        self.send_error(HTTPStatus.NOT_FOUND, "unknown path")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/run":
            self.handle_run()
            return
        self.send_error(HTTPStatus.NOT_FOUND, "unknown path")

    def handle_run(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            source_code = str(payload.get("source", ""))
            if not source_code.strip():
                self.send_json({"status": "error", "message": "source code is empty"}, HTTPStatus.BAD_REQUEST)
                return

            inputs = parse_inputs(str(payload.get("inputs", "")))
            run_id = make_run_id()
            out_dir = self.server.runs_dir / run_id
            out_dir.mkdir(parents=True, exist_ok=True)
            source_path = out_dir / "snippet.snl"
            source_path.write_text(source_code, encoding="utf-8")

            generated = visualize_source(source_path, out_dir, inputs)
            stem = source_path.stem
            response = {
                "status": "ok",
                "run_id": run_id,
                "run_dir": str(out_dir),
                "reports": {
                    "tokens": read_optional(out_dir / f"{stem}.tokens"),
                    "tree": read_optional(out_dir / f"{stem}.tree"),
                    "semantic": read_optional(out_dir / f"{stem}.semantic"),
                    "asm": read_optional(out_dir / f"{stem}.asm"),
                    "result": read_optional(out_dir / f"{stem}.result"),
                },
                "assets": {
                    "index": relative_asset_url(run_id, generated.get("Index")),
                    "execution_tutor": relative_asset_url(run_id, generated.get("Execution Tutor HTML")),
                    "stack_frames": relative_asset_url(run_id, generated.get("Stack Frames HTML")),
                    "syntax_tree": relative_asset_url(run_id, generated.get("Syntax Tree SVG")),
                    "symbol_tables": relative_asset_url(run_id, generated.get("Symbol Tables HTML")),
                    "call_graph": relative_asset_url(run_id, generated.get("Call Graph SVG")),
                },
            }
            trim_old_runs(self.server.runs_dir)
            self.send_json(response)
        except (CodegenError, OSError, RuntimeError, json.JSONDecodeError, ValueError) as exc:
            self.send_json(
                {"status": "error", "message": str(exc)},
                HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    def serve_run_asset(self, raw_path: str) -> None:
        relative = raw_path.removeprefix("/runs/")
        target = (self.server.runs_dir / relative).resolve()
        runs_root = self.server.runs_dir.resolve()
        if runs_root not in target.parents and target != runs_root:
            self.send_error(HTTPStatus.FORBIDDEN, "invalid asset path")
            return
        if not target.exists() or not target.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "asset not found")
            return
        mime = guess_mime_type(target)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(target.stat().st_size))
        self.send_no_cache_headers()
        self.end_headers()
        self.wfile.write(target.read_bytes())

    def serve_html(self, html_text: str) -> None:
        body = html_text.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_no_cache_headers()
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_no_cache_headers()
        self.end_headers()
        self.wfile.write(body)

    def send_no_cache_headers(self) -> None:
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write(f"[snl-web] {self.address_string()} - {fmt % args}\n")


def guess_mime_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".html":
        return "text/html; charset=utf-8"
    if suffix == ".json":
        return "application/json; charset=utf-8"
    if suffix == ".svg":
        return "image/svg+xml"
    if suffix in {".txt", ".tree", ".tokens", ".semantic", ".asm", ".result", ".snl"}:
        return "text/plain; charset=utf-8"
    return "application/octet-stream"


class SNLWebServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], handler_cls: type[BaseHTTPRequestHandler], project_root: Path):
        super().__init__(server_address, handler_cls)
        self.project_root = project_root
        self.runs_dir = project_root / "test" / "out" / "web_runs"
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.examples = collect_examples(project_root)
        self.app_html = render_app(self.examples)


def serve_web(project_root: Path, host: str, port: int) -> None:
    server = SNLWebServer((host, port), SNLWebHandler, project_root)
    url = f"http://{host}:{port}"
    print(f"SNL web UI running at {url}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping web server...")
    finally:
        server.server_close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the local web UI for the SNL compiler.")
    parser.add_argument("--host", default="127.0.0.1", help="host to bind the local server")
    parser.add_argument("--port", type=int, default=8000, help="port to bind the local server")
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="path to the snlcompiler project root",
    )
    args = parser.parse_args(argv)

    try:
        serve_web(args.project_root, args.host, args.port)
    except OSError as exc:
        print(f"snl_web.py: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

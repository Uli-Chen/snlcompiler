# SNL 编译器项目文档

## 1. 项目概述

本项目实现了面向编译原理课程实验的 SNL（Small Nested Language）编译器。系统以 Python 语言编写，提供统一命令行入口 `compiler.py`，覆盖词法分析、语法分析、语义分析、目标代码生成、目标程序运行与可视化调试等功能。

根据课程 PPT《编译原理课程实验》的要求，实验小组必须完成词法分析、语法分析和语义分析模块；争取优秀成绩的实验组需要完成目标代码生成模块，目标代码优先采用 32 位 MIPS 汇编。本项目不仅完成了必做模块，也实现了 MIPS 目标代码生成和内置 MIPS 子集执行器，并提供本地 Web Playground 与多种可视化结果。

## 2. 课程要求对照

课程 PPT 中的核心要求如下：

| 课程要求 | 项目完成情况 | 结论 |
| --- | --- | --- |
| 使用 SNL 文法 | 项目实现了 SNL 词法规则、递归下降语法分析和相关文档，文法整理见 `docs/grammar.md`，词法规则见 `src/grammar.txt`。 | 已满足 |
| 完成词法分析模块 | `src/snl_lexer.py` 可读取 SNL 源程序并输出 `LineShow / Lex / Sem` Token 序列，支持关键字、标识符、整数、字符、符号和注释处理。 | 已满足 |
| 完成语法分析模块 | `src/snl_parser.py` 基于递归下降方法分析 Token 序列，输出语法错误信息和层次文本语法树。 | 已满足 |
| 完成语义分析模块 | `src/snl_semantic.py` 建立局部化符号表，检查重复定义、未声明标识符、类型不匹配、过程调用参数、数组和记录访问等语义错误。 | 已满足 |
| 优秀档完成目标代码生成 | `src/snl_codegen.py` 可生成 32 位 MIPS 汇编，并能运行生成代码。 | 已满足 |
| 目标代码优先采用 MIPS | 项目生成 MIPS32 风格汇编，使用 `$sp/$fp/$ra` 管理栈帧，支持过程调用和递归。 | 已满足 |
| 合理使用临时寄存器 | 代码生成器实现 `RegisterPool`，使用 `$t0` 到 `$t9` 分配临时寄存器。 | 已满足 |
| 输出程序运行结果 | `compile` 命令会生成 `.asm` 和 `.result`，内置 MIPS runner 运行目标代码并输出结果。 | 已满足 |
| 实验报告和分工 | 项目文档已补充，成员分工需由小组填写。 | 部分满足 |

总体判断：项目已经达到课程必做要求，并达到“完成目标代码生成”的优秀档核心要求。若课程要求必须在 MARS 中运行汇编，还需要补充一次 MARS 仿真器人工验证记录；当前项目已经提供内置 MIPS 子集执行器作为自动化验证手段。

## 3. 系统功能

### 3.1 词法分析

词法分析模块读取 SNL 源程序，输出 Token 序列。Token 输出格式包括：

- `LineShow`：源程序行号。
- `Lex`：词法类别。
- `Sem`：语义值，例如标识符名、整数值、字符值。

支持的词法元素包括：

- 关键字：`program`、`type`、`var`、`procedure`、`begin`、`end`、`if`、`then`、`else`、`fi`、`while`、`do`、`endwh`、`read`、`write`、`return`、`array`、`record`、`integer`、`char` 等。
- 标识符和整数常量。
- 字符常量。
- 运算符和界符。
- `{ ... }` 注释。
- 词法错误 Token。

运行示例：

```bash
python3 compiler.py lex test/in/complex_calls_test.snl --with-eof -o test/out/complex_calls_test.tokens
```

### 3.2 语法分析

语法分析模块以 Token 序列为输入，采用递归下降分析方法，检查语法错误并输出层次文本语法树。

支持的主要语法结构包括：

- 程序头、声明部分和程序体。
- 类型声明、变量声明和过程声明。
- 整型、字符型、数组类型、记录类型和类型别名。
- 条件语句、循环语句、读写语句、返回语句、赋值语句和过程调用语句。
- 算术表达式、关系表达式、数组下标和记录域访问。

运行示例：

```bash
python3 compiler.py parse test/out/complex_calls_test.tokens -o test/out/complex_calls_test.tree
```

### 3.3 语义分析

语义分析模块建立局部化符号表，并在声明部分和语句部分进行语义检查。

已实现的语义检查包括：

- 标识符重复定义。
- 未声明标识符。
- 标识符类别不符合预期，例如把变量当作过程调用。
- 数组下界大于上界。
- 常量数组下标越界。
- 数组、记录访问不合法。
- 记录域不存在。
- 赋值语句左右类型不相容。
- 赋值语句左端不是可赋值变量。
- 过程调用实参数量不匹配。
- 过程调用形实参类型不匹配。
- `var` 参数要求传入可赋值变量。
- `if` 和 `while` 条件必须为布尔结果。
- 算术运算符两侧必须为整型。
- `read`、`write`、`return` 的对象必须为标量。

运行示例：

```bash
python3 compiler.py semantic test/out/complex_calls_test.tokens -o test/out/complex_calls_test.semantic
```

### 3.4 MIPS 目标代码生成与运行

目标代码生成模块读取通过前端检查的 SNL 源程序，生成 32 位 MIPS 汇编，并可通过项目内置的 MIPS 子集执行器运行。

已支持的代码生成能力包括：

- 全局变量和局部变量存储分配。
- 整型和字符型标量。
- 数组与记录的地址计算。
- 数组和记录整体赋值。
- 值参数和 `var` 参数。
- 过程调用、嵌套过程和递归调用。
- `$sp/$fp/$ra` 栈帧管理。
- 静态链访问外层作用域变量。
- 临时寄存器池分配。
- `read`、`write`、`return` 相关目标代码。
- 内置 MIPS 子集执行器，支持常用算术、访存、分支、跳转和 syscall。

运行示例：

```bash
python3 compiler.py compile test/in/complex_calls_test.snl --out-dir test/out/complex_calls_test
```

示例输出：

```text
Front End
No lexical, syntax, or semantic errors.

MIPS Assembly
test/out/complex_calls_test/complex_calls_test.asm

Program Output
8
120
128
256
```

### 3.5 可视化与 Web Playground

项目提供可视化命令，可生成课程报告和调试需要的 HTML/SVG/JSON 文件。

可视化输出包括：

- `index.html`：可视化总览入口。
- `syntax_tree.svg`：语法树图。
- `symbol_tables.html`：符号表。
- `stack_frames.html`：栈帧布局和调用轨迹。
- `call_graph.svg`：过程调用图。
- `execution_tutor.html`：类 Python Tutor 的单步执行页。
- `side_info.json`：机器可读的中间信息。

运行示例：

```bash
python3 compiler.py visualize test/in/complex_calls_test.snl --out-dir test/out/visual_complex_calls
```

本地 Web Playground 启动命令：

```bash
python3 compiler.py web --host 127.0.0.1 --port 8000
```

浏览器访问：

```text
http://127.0.0.1:8000
```

## 4. 项目结构

```text
.
├── compiler.py                  # 统一 CLI 入口
├── README.md                    # 快速使用说明
├── docs/
│   ├── grammar.md               # SNL 文法整理
│   └── project_documentation.md # 项目文档
├── src/
│   ├── grammar.txt              # 词法规则配置
│   ├── snl_lexer.py             # 词法分析器
│   ├── snl_parser.py            # 递归下降语法分析器
│   ├── snl_semantic.py          # 语义分析器
│   ├── snl_codegen.py           # MIPS 代码生成器和 MIPS runner
│   ├── snl_visualize.py         # 可视化生成工具
│   └── snl_web.py               # 本地 Web Playground
└── test/
    ├── in/                      # SNL 测试输入
    ├── out/                     # 编译输出和可视化产物
    └── run_regression.py        # 回归测试脚本
```

## 5. 统一命令说明

项目根目录提供统一入口：

```bash
python3 compiler.py <command> ...
```

常用命令如下：

```bash
# 词法分析
python3 compiler.py lex test/in/complex_calls_test.snl --with-eof

# 语法分析
python3 compiler.py parse test/out/complex_calls_test.tokens

# 语义分析
python3 compiler.py semantic test/out/complex_calls_test.tokens

# 完整编译并运行
python3 compiler.py compile test/in/complex_calls_test.snl --out-dir test/out/complex_calls_test

# 只生成 MIPS，不运行
python3 compiler.py compile test/in/complex_calls_test.snl --no-run --out-dir test/out/complex_calls_test

# 生成可视化
python3 compiler.py visualize test/in/complex_calls_test.snl --out-dir test/out/visual_complex_calls

# 启动 Web Playground
python3 compiler.py web --host 127.0.0.1 --port 8000
```

## 6. 测试与验证

项目内置回归测试脚本：

```bash
python3 test/run_regression.py
```

当前验证结果：

```text
PASS
positive=14 negative=2
```

回归用例覆盖了：

- 基础代码生成。
- 复杂过程调用。
- 递归求和、递归栈帧。
- Fibonacci 序列。
- 嵌套作用域变量捕获。
- 嵌套参数捕获。
- 深层嵌套过程访问。
- `var` 参数传递数组元素和记录域。
- 数组值参数。
- 记录值参数。
- 数组整体赋值。
- 记录整体赋值。
- 循环压力测试。
- 语义错误负例。
- 聚合类型 `write` 错误负例。

静态语法检查命令：

```bash
python3 -m py_compile compiler.py src/*.py test/run_regression.py
```

当前检查通过。

Web 后端健康检查：

```bash
curl http://127.0.0.1:8000/api/health
```

返回：

```json
{"status": "ok", "message": "SNL compiler backend is running"}
```

## 7. 与课程要求的结论

项目已经满足课程 PPT 中的必做要求：

- 已完成 SNL 词法分析模块。
- 已完成 SNL 语法分析模块。
- 已完成 SNL 语义分析模块。
- 已使用 SNL 文法。
- 已提供错误检查信息和语法树输出。
- 已建立和管理局部化符号表。

项目也满足优秀档核心要求：

- 已完成 32 位 MIPS 目标代码生成。
- 已实现临时寄存器池。
- 已实现栈帧和过程调用。
- 已支持嵌套过程和递归调用。
- 已输出目标程序运行结果。

因此，从功能完成度看，本项目已经达到课程要求，并具备争取优秀成绩的关键条件。

## 8. 当前不足与风险

虽然项目功能完整，但仍有一些可以继续完善的地方：

1. MARS 兼容性验证尚未形成记录。项目生成的是 MIPS32 风格汇编，并提供内置 runner 自动运行；如果老师明确要求在 MARS 中运行，需要补充 MARS 手工运行截图或说明。

2. 数组下标越界主要检查常量下标。对于运行期表达式下标，当前语义分析无法静态确定是否越界，也没有插入运行时边界检查。

3. 仓库工程化还有收口空间。目前存在部分生成产物、缓存目录和未跟踪文件，建议补充 `.gitignore` 并清理提交范围。

4. 代码模块较大。`snl_codegen.py` 和 `snl_visualize.py` 承担职责较多，后续继续扩展时建议拆分。

5. Web Playground 的错误响应可以更细化。当前部分前端编译错误会以接口错误形式返回，后续可区分用户源程序错误和系统运行错误。

6. 小组分工信息尚未填写。课程要求三人小组分工明确，项目文档应在提交前补充成员姓名、学号和负责模块。

## 9. 后续优化方向

建议按以下优先级继续优化：

1. 项目收口与交付整理。补充 `.gitignore`，清理 `__pycache__`、`.DS_Store` 和临时输出目录，确认需要纳入版本控制的测试用例和 Web 文件。

2. 完善实验报告材料。补充分工表、整体设计图、模块流程图、测试截图、MARS 运行截图和典型错误示例。

3. 增强测试体系。为语义错误清单补充更多负例，如数组越界、记录域错误、实参个数错误、实参类型错误、未声明变量、重复定义等。

4. 拆分代码生成模块。将 MIPS runner、栈帧布局、寄存器池、代码生成主逻辑拆分到独立模块，降低维护成本。

5. 提升可视化体验。优化执行调试页面中的源代码、汇编、寄存器、栈帧和内存联动展示，增强课程答辩展示效果。

6. 提供 MARS 兼容模式。检查生成汇编在 MARS 中的伪指令、数据段、syscall 和标签格式兼容性，必要时增加 `--mars` 输出模式。

## 10. 小组分工

提交前请补充以下信息：

> **⚠️ 重要：课程要求必须填写小组分工信息，提交前务必完成此表！**

| 成员 | 学号 | 负责内容 | 主要文件 |
| --- | --- | --- | --- |
| 待填写 | 待填写 | 词法分析、词法规则和 Token 输出 | `src/snl_lexer.py`, `src/grammar.txt` |
| 待填写 | 待填写 | 语法分析、语法树输出和文法整理 | `src/snl_parser.py`, `docs/grammar.md` |
| 待填写 | 待填写 | 语义分析、代码生成、运行器和可视化 | `src/snl_semantic.py`, `src/snl_codegen.py`, `src/snl_visualize.py`, `src/snl_web.py` |

## 11. 附录：典型测试程序

`test/in/complex_calls_test.snl` 覆盖了递归 `fib`、递归 `factorial`、普通过程调用、数组、记录、`var` 参数和多次输出。

期望输出：

```text
8
120
128
256
```

运行命令：

```bash
python3 compiler.py compile test/in/complex_calls_test.snl --out-dir test/out/complex_calls_test
```

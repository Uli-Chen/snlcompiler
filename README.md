# SNL Compiler

面向编译原理课程设计的 SNL 编译器实现。
实现了词法分析、递归下降语法分析、语义分析、MIPS32 目标代码
生成、内置 MIPS 子集执行器，以及用于课程报告和调试的可视化工具。

根目录提供统一入口：

```bash
python3 compiler.py <command> ...
```

项目不依赖第三方 Python 包。可视化文件使用标准库生成 HTML/SVG，可以直接
用浏览器打开。

## 功能概览

- 词法分析：读取 SNL 源程序，输出 `LineShow / Lex / Sem` Token 序列。
- 语法分析：递归下降分析，输出语法错误信息和层次文本语法树。
- 语义分析：建立局部化符号表，检查类型、作用域、过程调用和变量引用。
- 目标代码生成：生成 32 位 MIPS 汇编。
- 运行结果：内置 MIPS 子集执行器运行生成的目标代码并输出结果。
- 递归支持：过程调用使用 `$fp/$sp` 栈帧，支持递归过程的独立参数和局部变量。
- 可视化：生成语法树 SVG、符号表 HTML、调用图 SVG、栈帧 HTML、动态调用轨迹和 JSON side information。
- 执行调试：生成类 Python Tutor 的单步执行页面，联动展示汇编时间轴、调用栈、寄存器、全局区和栈内存。
- 本地 Web：启动浏览器友好的在线 playground，输入代码可实时编译、运行并查看调试页。

## 目录结构

```text
.
├── compiler.py                  # 根目录统一 CLI
├── README.md
├── .gitignore
├── docs/
│   ├── grammar.md               # SNL 上下文无关文法整理（105 条产生式）
│   ├── project_documentation.md # 项目文档（课程要求对照、分工表）
│   └── 编译原理课程设计.pptx
├── src/
│   ├── grammar.txt              # 词法规则配置
│   ├── snl_lexer.py             # 词法分析器
│   ├── snl_parser.py            # 递归下降语法分析器
│   ├── snl_semantic.py          # 语义分析器
│   ├── snl_codegen.py           # MIPS 目标代码生成器 + MIPS runner
│   ├── snl_visualize.py         # 可视化生成工具
│   └── snl_web.py               # 本地 Web Playground
└── test/
    ├── in/                      # SNL 测试输入（21 个用例）
    ├── out/                     # 编译输出、报告和可视化结果
    └── run_regression.py        # 回归测试脚本
```

## 快速开始

在项目根目录运行：

```bash
python3 compiler.py compile test/in/complex_calls_test.snl --out-dir test/out/complex_calls_test
```

成功时会看到：

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

这个测试程序同时覆盖递归 `fib`、递归 `factorial`、非递归过程调用、数组、
记录、`var` 参数和多次输出。

## 统一命令

### 1. 词法分析

```bash
python3 compiler.py lex test/in/complex_calls_test.snl --with-eof -o test/out/complex_calls_test.tokens
```

输出格式：

```text
LineShow  Lex          Sem
--------  -----------  ---
1         PROGRAM
1         ID           complexCalls
```

### 2. 语法分析

语法分析输入是 lexer 生成的 token 文件：

```bash
python3 compiler.py parse test/out/complex_calls_test.tokens -o test/out/complex_calls_test.tree
```

输出包含：

- `Syntax Errors`
- `Syntax Tree`
- 层次文本语法树，例如 `ProK`、`ProcDecK`、`StmtK`、`ExpK`

### 3. 语义分析

```bash
python3 compiler.py semantic test/out/complex_calls_test.tokens -o test/out/complex_calls_test.semantic
```

输出包含：

- 语义错误信息
- 全局符号表
- 每个过程的局部符号表
- 标识符种类、类型、名字、声明行号和参数模式等属性

### 4. 完整编译并运行

```bash
python3 compiler.py compile test/in/complex_calls_test.snl --out-dir test/out/complex_calls_test
```

如果程序含有 `read`，可以传入输入值：

```bash
python3 compiler.py compile test/in/lexer_test.snl --input 0 --out-dir test/out/lexer_test
```

只生成 MIPS，不运行：

```bash
python3 compiler.py compile test/in/complex_calls_test.snl --no-run --out-dir test/out/complex_calls_test
```

调整内置 MIPS runner 的执行步数上限：

```bash
python3 compiler.py compile test/in/stress_loop_test.snl --max-steps 200000 --out-dir test/out/stress_loop_test
```

### 5. 生成可视化

```bash
python3 compiler.py visualize test/in/complex_calls_test.snl --out-dir test/out/visual_complex_calls
```

打开生成的入口页：

```text
test/out/visual_complex_calls/index.html
```

### 6. 启动本地 Web Playground

```bash
python3 compiler.py web --host 127.0.0.1 --port 8000
```

打开：

```text
http://127.0.0.1:8000
```

页面支持：

- 直接编辑 SNL 代码
- 自动防抖编译和手动运行
- 查看 tokens / 语法树 / 语义结果 / MIPS 汇编
- 预览执行调试页 `execution_tutor.html`
- 切换 `test/in` 下的示例程序

## 完整编译输出

`compile` 命令会在输出目录生成：

```text
<program>.tokens       # Token 序列
<program>.tree         # 语法错误信息和语法树
<program>.semantic     # 语义错误信息和符号表
<program>.asm          # 32 位 MIPS 汇编
<program>.result       # 前端检查结果和程序运行输出
```

如果前端检查失败，代码生成会停止，但仍然会保留：

- `<program>.tokens`
- `<program>.tree`
- `<program>.semantic`
- `<program>.result`

这样可以直接在输出目录里复盘词法、语法和语义阶段的问题；只有 `<program>.asm` 不会生成。

如果运行阶段失败，例如触发了 `--max-steps` 上限，输出目录仍然会保留：

- `<program>.asm`
- `<program>.result`

其中 `<program>.result` 会写出 `Runtime Error` 和具体错误信息。

## 回归测试

仓库内置了一组 golden regression case，可以一键回归：

```bash
python3 test/run_regression.py
```

当前验证结果：

```text
PASS
positive=14 negative=6
```

- **14 个正例**：覆盖基础代码生成、复杂过程调用、递归求和、递归栈帧、Fibonacci、
  嵌套作用域变量捕获、嵌套参数捕获、深层嵌套过程、var 参数传数组/记录元素、
  数组值参数、记录值参数、数组整体赋值、记录整体赋值、循环压力测试。
- **6 个负例**：覆盖综合语义错误、聚合类型 write 错误、常量数组下标越界、
  记录域不存在、过程调用实参数量不匹配、var 参数传入非左值。

每条用例的中间产物写到：

```text
test/out/regression_suite/
```

## 可视化输出

`visualize` 命令会生成：

```text
index.html             # 可视化总览入口
syntax_tree.svg        # 语法树结构图
symbol_tables.html     # 符号表可视化
stack_frames.html      # 栈帧布局和动态调用轨迹
execution_tutor.html   # 单步执行调试页（汇编 / 栈 / 内存 / 寄存器）
call_graph.svg         # 过程调用图
side_info.json         # 机器可读 side information
```

对于含递归的程序，`stack_frames.html` 会展示：

- 每个过程的参数区
- 保存的 `$fp` 和 `$ra`
- 局部变量区
- 每个槽位相对 `$fp` 的偏移
- 是否存在直接递归调用
- 动态调用轨迹
- 最大过程调用深度

`execution_tutor.html` 会额外提供：

- 时间轴拖动、逐步前进和自动播放
- 当前 MIPS 指令高亮
- 基于 `$fp` 链重建的运行时调用栈
- 寄存器变化和最近内存改写高亮
- 全局数据区和栈区内存窗口
- 程序输出随步骤联动显示

示例 `side_info.json` 片段：

```json
{
  "frames": [
    {
      "name": "mirrorSum",
      "label": "proc_mirrorSum",
      "local_bytes": 4,
      "param_bytes": 8,
      "slots": [
        { "name": "n", "kind": "param", "offset": 8, "mode": "value" },
        { "name": "acc", "kind": "param", "offset": 12, "mode": "var" },
        { "name": "local", "kind": "local", "offset": -4 }
      ]
    }
  ],
  "max_call_depth": 4
}
```

## 递归调用支持

过程调用采用运行时栈帧，不再使用过程级静态参数槽。调用约定如下：

- 调用者将实参逆序压入栈。
- 值参数压入值。
- `var` 参数压入实参地址。
- 被调过程入口保存旧 `$fp` 和 `$ra`。
- 被调过程设置新的 `$fp`。
- 局部变量按 `$fp` 负偏移分配。
- 参数按 `$fp` 正偏移访问。
- 返回时恢复 `$sp`、`$fp`、`$ra`。

典型 MIPS 过程入口：

```asm
proc_example:
addi $sp, $sp, -8
sw $fp, 0($sp)
sw $ra, 4($sp)
move $fp, $sp
addi $sp, $sp, -4
```

典型过程出口：

```asm
move $sp, $fp
lw $fp, 0($sp)
lw $ra, 4($sp)
addi $sp, $sp, 8
jr $ra
```

因此递归调用时，每一层调用都有独立参数、返回地址和局部变量。

## 测试程序

当前 `test/in/` 包含 21 个测试文件，分为正例和负例两组：

### 正例（14 个）

| 文件 | 说明 | 期望输出 |
|---|---|---|
| `codegen_test.snl` | 基础代码生成 | `9 1` |
| `complex_calls_test.snl` | 递归+非递归混合调用、数组、记录、var参数 | `8 120 128 256` |
| `fibonacci_sequence_test.snl` | Fibonacci 序列，需 READ 输入 | `0 1 1 2 3 5 8 13` |
| `recursive_sum_test.snl` | 直接递归求和 | `15` |
| `recursive_frame_test.snl` | 递归过程使用局部变量，验证栈帧独立 | `12` |
| `nested_scope_capture_test.snl` | 嵌套作用域变量捕获 | `3` |
| `nested_param_capture_test.snl` | 嵌套参数捕获 | `9` |
| `deep_nested_capture_test.snl` | 深层嵌套过程变量访问 | `10` |
| `var_element_param_test.snl` | var 参数传递数组元素和记录域 | `27` |
| `array_value_param_test.snl` | 数组值参数传递 | `111 116 11 1` |
| `record_value_param_test.snl` | 记录值参数传递 | `17 29 7 9` |
| `array_assignment_test.snl` | 数组整体赋值 | `4 6 99 8` |
| `record_assignment_test.snl` | 记录整体赋值 | `1 2 9 2` |
| `stress_loop_test.snl` | 循环压力测试 | `2000` |

### 负例（6 个）

| 文件 | 预期语义错误 |
|---|---|
| `semantic_error_test.snl` | 综合语义错误（重复定义、未声明、类型不匹配等） |
| `aggregate_write_error_test.snl` | write 表达式必须为标量 |
| `array_bounds_error_test.snl` | 常量数组下标越界 |
| `record_field_error_test.snl` | 记录域不存在 |
| `param_count_error_test.snl` | 过程调用实参数量不匹配 |
| `var_param_error_test.snl` | var 参数传入常量（非左值） |

### 其他测试文件

- `lexer_test.snl`：覆盖常见词法、语法结构，用于 lex/parse 手工验证。

### 推荐一键回归

```bash
python3 test/run_regression.py
```

## 支持的 SNL 子集

当前实现支持：

- `program ... begin ... end.`
- `type`、`var`、`procedure` 声明
- `integer`、`char`
- 数组类型
- 记录类型
- 赋值语句
- `read`
- `write`
- `return`
- `if ... then ... else ... fi`
- `while ... do ... endwh`
- 过程调用
- 值参数和 `var` 参数
- 递归过程调用
- 整数算术：`+`、`-`、`*`、`/`
- 关系表达式：`<`、`=`
- 数组下标和记录域访问

## 语义检查

语义分析器会检查：

- 标识符重复定义
- 未声明标识符
- 标识符类别不匹配
- 类型标识符、变量标识符、过程标识符混用
- 数组下标类型
- 常量数组下标越界
- 非数组对象使用下标
- 非记录对象使用域访问
- 不存在的记录域
- 赋值左右类型不相容
- 赋值左端不是可赋值变量
- 过程调用实参数量不匹配
- 过程调用形实参类型不匹配
- `var` 参数传入非左值
- `read` 目标不是变量或不是标量
- `if` / `while` 条件类型

## 目标 MIPS 子集

生成器输出 32 位 MIPS 风格汇编，主要使用：

- 数据段：`.data`、`.word`、`.space`
- 文本段：`.text`、`.globl main`
- 访存：`la`、`lw`、`sw`
- 运算：`li`、`move`、`add`、`addi`、`sub`、`mul`、`div`、`sll`
- 分支和调用：`j`、`jal`、`jr`、`bge`、`bne`
- 系统调用：打印整数、打印字符、读整数、读字符、退出

### 代码优化

代码生成器实现了以下窥孔优化：

- **冗余 `move` 消除**：跳过 `move $r, $r` 指令。
- **冗余 `addi` 消除**：跳过 `addi $r, $r, 0` 指令。
- **跳转到下一行消除**：跳过目标是紧邻下一条指令的 `j` 指令。
- **移位代替乘法**：当数组元素大小是 2 的幂时，用 `sll` 移位代替 `mul` 乘法。

内置 runner 只实现了本项目生成器会用到的 MIPS 子集。生成的 `.asm` 仍然是
标准 MIPS 风格文本，可用于课程报告中展示目标代码。

## 已知边界

- 目前过程没有真正的函数返回值；`return(exp)` 用作提前返回，表达式会被解析和生成，但过程调用不能作为表达式使用。
- 语义分析当前支持直接递归。若要支持互递归，需要先扫描同一层所有过程头，再分析过程体。
- 字符按 32 位 word 存储，简化了 MIPS 内存模型。
- 内置 MIPS runner 是课程项目用的轻量执行器，不是完整 MIPS/MARS 替代品，可以使用课程提供的jar进行验证。

## 直接运行底层脚本

也可以绕过 `compiler.py` 直接运行 `src/` 下脚本：

```bash
python3 src/snl_lexer.py test/in/complex_calls_test.snl --with-eof
python3 src/snl_parser.py test/out/complex_calls_test.tokens
python3 src/snl_semantic.py test/out/complex_calls_test.tokens
python3 src/snl_codegen.py test/in/complex_calls_test.snl --out-dir test/out/complex_calls_test
python3 src/snl_visualize.py test/in/complex_calls_test.snl --out-dir test/out/visual_complex_calls
```

# SNL Compiler

一个面向编译原理课程设计的精简 SNL 编译器。

当前公开能力只保留一条主路径：读取 `.snl` 源程序，完成词法、语法、语义检查，生成四元式中间代码，进行 IR 优化，最后生成 MIPS32 汇编目标代码。词法分析、语法分析、语义分析和 IR 调试输出仍作为内部/开发能力存在，但不再作为默认用户输出。

## 目录结构

```text
.
├── compiler.py              # 公开 CLI：compile
├── src/
│   ├── grammar.txt          # 词法规则
│   ├── snl_lexer.py         # 词法分析
│   ├── snl_parser.py        # token -> typed AST
│   ├── snl_semantic.py      # AST 语义分析和类型标注
│   ├── snl_ir.py            # 四元式 IR 数据结构
│   ├── snl_irgen.py         # typed AST -> 四元式 IR
│   ├── snl_optimizer.py     # IR 常量折叠和公共子表达式消除
│   ├── snl_codegen.py       # 四元式 IR -> MIPS32
│   └── snl_runner.py        # 独立 MIPS 子集运行器
└── test/
    └── in/                  # SNL 示例输入
```

## 编译

```bash
python3 compiler.py compile test/in/codegen_test.snl -o /private/tmp/codegen_test.asm
```

成功时不会生成 token、语法树、语义报告、运行结果或可视化文件，只写出指定的 `.asm` 文件。

失败时会在 `stderr` 输出前端诊断，例如词法错误、语法错误或语义错误，并返回非零退出码。

默认会开启 IR 优化。需要对照未优化目标代码时可以关闭：

```bash
python3 compiler.py compile test/in/codegen_test.snl -o /private/tmp/codegen_test.asm --no-opt
```

需要观察四元式时，显式传入调试输出路径：

```bash
python3 compiler.py compile test/in/cse_test.snl \
  -o /private/tmp/cse_test.asm \
  --emit-raw-ir /private/tmp/cse_raw.ir \
  --emit-ir /private/tmp/cse_opt.ir
```

## 编译流程

```text
SNL 源码
  -> Token
  -> typed AST
  -> 语义标注 AST
  -> 四元式 IR
  -> IR 优化
  -> MIPS32 汇编
```

当前 IR 优化包括：

- 常量折叠：折叠整数常量表达式和常量关系条件。
- 基本块内公共子表达式消除：在不跨越 `label/goto/if/call/read/return` 等边界的前提下复用重复表达式。

## 运行目标代码

内置 MIPS 子集运行器已经从主编译器中拆出，作为独立脚本使用：

```bash
python3 src/snl_runner.py /private/tmp/codegen_test.asm
```

如果 SNL 程序包含 `read`，通过 `--input` 传入输入值：

```bash
python3 src/snl_runner.py /private/tmp/read_test.asm --input 1 2 3
```

## 语言支持边界

当前实现支持：

- `integer`、`char`
- 类型别名
- 数组和记录类型
- 数组元素读写
- 记录字段读写
- 过程调用、值参数、`var` 参数
- 递归过程调用
- `if`、`while`、`read`、`write`、`return`

当前实现明确不支持数组或记录的整体值语义：

- 不支持数组/记录整体赋值
- 不支持数组/记录作为 value 参数传递
- 不支持 `read` 或 `write` 整个数组/记录

需要传递数组或记录时，请使用 `var` 参数传地址，并在过程内部访问元素或字段。

## 开发验证

建议的 smoke test：

```bash
python3 compiler.py --help
python3 compiler.py compile test/in/codegen_test.snl -o /private/tmp/codegen_test.asm
python3 src/snl_runner.py /private/tmp/codegen_test.asm
python3 compiler.py compile test/in/constant_folding_test.snl -o /private/tmp/constant_folding_test.asm --emit-ir /private/tmp/constant_opt.ir
python3 compiler.py compile test/in/cse_test.snl -o /private/tmp/cse_test.asm --emit-raw-ir /private/tmp/cse_raw.ir --emit-ir /private/tmp/cse_opt.ir
python3 compiler.py compile test/in/ir_array_record_test.snl -o /private/tmp/ir_array_record_test.asm
python3 compiler.py compile test/in/recursive_sum_test.snl -o /private/tmp/recursive_sum_test.asm
python3 src/snl_runner.py /private/tmp/recursive_sum_test.asm
python3 compiler.py compile test/in/complex_calls_test.snl -o /private/tmp/complex_calls_test.asm
python3 src/snl_runner.py /private/tmp/complex_calls_test.asm
python3 compiler.py compile test/in/semantic_error_test.snl -o /private/tmp/semantic_error_test.asm
```

`semantic_error_test.snl` 应失败并输出语义错误。

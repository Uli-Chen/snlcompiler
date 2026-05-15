# SNL 编译器问题与限制报告

## 一、实际 Bug（可能导致错误行为）

### 1. 编译期除零无警告

**位置**：`snl_optimizer.py:fold_constants()` + `snl_semantic.py`

**问题**：当源码中出现 `x := 10 / 0` 时，优化器正确地不折叠（`eval_arithmetic` 返回 None），但语义分析器也不报告任何警告。程序会编译成功，运行时才在模拟器中崩溃。

**影响**：用户得不到编译期反馈，需要运行才能发现错误。

**建议修复**：在 `check_expr` 中检测除法右操作数为常量 0 时报错。

---

### 2. `is_temp()` 判断过于宽松

**位置**：`snl_ir.py:is_temp()`

**问题**：`is_temp` 只检查字符串是否以 `t` 开头。如果 Symbol 的 name 恰好以 `t` 开头（如变量名 `total`），在某些边界情况下可能被误判为临时变量。

**当前安全性**：实际上 IR 中的操作数要么是 int、要么是 temp 字符串（如 `t0`）、要么是 Symbol 对象（不是 str），所以目前不会触发。但如果未来 IR 格式变化，这是一个隐患。

**建议修复**：改为正则匹配 `r"^t\d+$"`。

---

### 3. `return` 语句值被丢弃

**位置**：`snl_codegen.py:emit_return()` + `snl_irgen.py`

**问题**：`return(expr)` 语句会计算表达式的值并加载到寄存器，但随后只是跳转到过程末尾，值没有通过 `$v0` 或其他机制传递给调用者。调用者无法获取返回值。

**影响**：`return` 语句实际上只起到提前退出过程的作用，不能用于函数式的值返回。

**建议修复**：在 `emit_return` 中将值存入 `$v0`，调用者在 `jal` 后从 `$v0` 读取。

---

## 二、语法/语义限制（设计决策，非 Bug）

### 4. `read` 不支持带选择器的变量

**位置**：`snl_parser.py:parse_input_stm()`

**问题**：`read(arr[2])` 或 `read(rec.field)` 会报语法错误。解析器只接受 `read(identifier)`，不调用 `finish_variable` 解析选择器。

**影响**：无法直接读取数组元素或记录字段，必须先读到临时变量再赋值。

**建议修复**：将 `parse_input_stm` 中的 `VarRef(name.sem, name.line)` 改为调用 `finish_variable(name)`。

---

### 5. `finish_variable` 只支持一层选择器

**位置**：`snl_parser.py:finish_variable()`

**问题**：变量引用只能有一层选择器（`arr[i]` 或 `rec.field` 或 `rec.field[i]`），不支持链式访问如 `arr[i].field` 或 `rec.field1.field2`。

**影响**：嵌套数据结构的深层访问需要拆分为多步。

**原因**：SNL 教学语言的语法规范本身就限制了选择器深度。

---

### 6. record 字段类型不支持类型别名

**位置**：`snl_parser.py:parse_record_type()`

**问题**：`FIELD_TYPE_START = {"INTEGER", "CHAR", "ARRAY"}`，不包含 `"ID"`。因此 record 字段不能使用类型别名（如 `type point = record ... end; type line = record point start; ... end`）。

**影响**：无法构建嵌套的自定义类型记录。

---

### 7. 数组元素类型只能是基本类型

**位置**：`snl_parser.py:parse_array_type()` 调用 `parse_base_type()`

**问题**：`array [1..N] of T` 中的 T 只能是 `integer` 或 `char`，不能是 record 或另一个 array。

**影响**：不支持多维数组或数组嵌套记录。

---

## 三、代码质量问题

### 8. 优化器 CSE 对 `addr` 的 key 不够精确

**位置**：`snl_optimizer.py:expression_key()`

**问题**：`addr` 操作的 key 只用 `symbol.name`，不包含 symbol 的作用域信息。如果不同作用域有同名变量，理论上可能错误地 CSE。

**当前安全性**：由于每个过程独立优化（`optimize_unit` 逐个处理），同一个 unit 内不会有同名变量，所以目前安全。

---

### 9. `walk_procedures` 在多处重复定义

**位置**：`snl_ir.py:flatten_procedures()`、`snl_codegen.py:walk_procedures()`、`snl_optimizer.py:walk_procedures()`

**问题**：三个模块各自定义了功能相同的过程树遍历函数。

**建议**：统一使用 `snl_ir.py` 中的 `flatten_procedures`。

---

### 10. `snl_irgen.py` 的 import 使用 try/except 双路径

**位置**：`snl_irgen.py`、`snl_codegen.py`、`snl_optimizer.py`

**问题**：每个模块都有 `try: from playground.snlcompiler.src... except: from ...` 的双路径导入。这是为了同时支持包导入和直接运行，但增加了维护负担。

**建议**：统一使用相对导入或配置 `sys.path`。

---

## 四、潜在运行时风险

### 11. 寄存器池耗尽

**位置**：`snl_codegen.py:RegisterPool`

**场景**：如果一条四元式的翻译过程中需要超过 10 个临时寄存器（$t0-$t9），会抛出 `CodegenError`。

**触发条件**：极深的嵌套表达式或复杂的地址计算。当前生成的 IR 每条四元式最多用 3-4 个寄存器，所以实际不会触发。

---

### 12. 模拟器步数限制

**位置**：`snl_runner.py:run()`

**问题**：100000 步的限制对于深度递归或大循环可能不够。

**建议**：可以通过命令行参数配置步数上限。

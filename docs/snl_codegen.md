# SNL 目标代码生成器说明书

## 概述

`snl_codegen.py` 将优化后的四元式 IR 翻译为 MIPS32 汇编代码。它负责：

1. 存储分配（全局变量、局部变量、临时变量的内存布局）
2. 寄存器分配（临时寄存器池管理）
3. 栈帧管理（过程调用的 prologue/epilogue）
4. 静态链实现（嵌套过程访问外层变量）
5. 指令选择（IR 操作到 MIPS 指令的映射）

---

## 栈帧布局

```
高地址
┌─────────────────────────┐
│  调用者压入的实参 N      │  ← 12 + (N-1)*4 ($fp)
│  ...                    │
│  调用者压入的实参 1      │  ← 12($fp)  [PARAM_BASE_OFFSET]
│  静态链指针             │  ← 8($fp)   [STATIC_LINK_OFFSET]
├─────────────────────────┤
│  保存的旧 $fp           │  ← 0($fp)   ← $fp 指向此处
│  保存的 $ra             │  ← 4($fp)
├─────────────────────────┤
│  局部变量区             │  ← 负偏移($fp)
│  临时变量区             │
└─────────────────────────┘  ← $sp
低地址
```

**关键偏移常量**：
- `STATIC_LINK_OFFSET = 8`：静态链指针相对 $fp 的偏移
- `PARAM_BASE_OFFSET = 12`：第一个参数相对 $fp 的偏移

---

## 类定义

### RegisterPool

```python
class RegisterPool:
    available: list[str]  # 可用寄存器栈（$t0-$t9）
```

**方法**：
- `alloc()` → 分配一个临时寄存器，池空时抛出异常
- `free(reg)` → 归还寄存器到池中

**设计**：简单的栈式分配，不做寄存器着色或溢出处理。10 个临时寄存器对于单条四元式的翻译足够。

### MIPSProgram

```python
class MIPSProgram:
    data: list[str]  # .data 段指令
    text: list[str]  # .text 段指令
```

**方法**：
- `emit_data(line)` → 追加数据段声明
- `emit(line)` → 追加代码段指令
- `render()` → 拼接为完整汇编文本

### IRMIPSGenerator

主代码生成器，维护以下状态：
- `current_unit`：当前正在生成代码的 IR 单元
- `current_end_label`：当前过程的返回标签
- `current_temp_offsets`：当前过程的临时变量栈帧偏移
- `main_temp_labels`：主程序临时变量的数据段标签
- `pending_params`：待处理的参数四元式（call 前累积）
- `procedure_temp_offsets`：所有过程的临时变量偏移表
- `procedure_local_bytes`：所有过程的局部空间大小

---

## 生成流程

### 入口：`generate()`

```
1. assign_labels()              — 为全局变量和过程分配汇编标签
2. assign_all_procedure_storage() — 计算所有过程的栈帧布局
3. emit_global_storage()        — 生成 .data 段的全局变量声明
4. emit_main_temp_storage()     — 生成主程序临时变量的数据段声明
5. 为每个过程生成代码
6. 生成 main 标签和主程序体代码
7. 生成程序退出 syscall
```

### 存储分配：`assign_procedure_storage(proc)`

```
偏移从 0 开始向负方向增长：
1. 形参：正偏移（PARAM_BASE_OFFSET + index * 4）
2. 局部变量：负偏移（按类型大小分配）
3. 临时变量：紧接局部变量之后（每个 4 字节）
```

**主程序特殊处理**：主程序的临时变量分配在 .data 段（因为主程序没有栈帧）。

---

## 指令翻译

### 算术运算

```
(+, arg1, arg2, result) →
    load arg1 → $t_left
    load arg2 → $t_right
    add $t_left, $t_left, $t_right
    store $t_left → result 的栈帧位置
```

### 条件跳转

```
(if_false_<, left, right, label) →
    load left → $t_left
    load right → $t_right
    bge $t_left, $t_right, label    # !(left < right) 即 left >= right

(if_false_=, left, right, label) →
    bne $t_left, $t_right, label    # !(left = right) 即 left != right
```

### 数组下标地址：`emit_index_addr(quad)`

```
base = load base_addr
index = load index_value
addi index, index, -low            # 减去数组下界
如果元素大小 > 1 字：
    li scale, element_words
    mul index, index, scale
li scale4, 4
mul index, index, scale4           # 转换为字节偏移
add base, base, index              # 基地址 + 偏移
store base → result
```

### 记录字段地址：`emit_field_addr(quad)`

```
base = load base_addr
offset = field_offset_words(record_type, field_name)
如果 offset > 0：
    addi base, base, offset * 4
store base → result
```

### 过程调用：`emit_call(quad)`

```
1. 逆序压入所有参数（从最后一个到第一个）
2. 计算并压入静态链指针
3. jal 跳转到过程标签
4. 恢复栈指针（弹出参数和静态链）
```

### I/O

```
read:
    load addr → $t
    li $v0, 5 (整数) 或 12 (字符)
    syscall
    sw $v0, 0($t)

write:
    load value → $t
    move $a0, $t
    li $v0, 1 (整数) 或 11 (字符)
    syscall
    # 输出换行
    li $a0, 10
    li $v0, 11
    syscall
```

---

## 静态链机制

### 访问外层变量：`load_frame_for_level(target_level)`

从当前 $fp 出发，沿静态链向外走 `(current_level - target_level)` 步：

```python
frame = $fp
for _ in range(current_level - target_level):
    frame = memory[frame + STATIC_LINK_OFFSET]
```

### 调用时传递静态链：`load_static_link_for_call(callee)`

传入"被调过程的父词法层"的活动记录地址：

- 调用直接子过程（parent_level == current_level）：传当前 $fp
- 调用兄弟过程或递归调用：沿静态链回退到 parent_level

```python
static_link = $fp
for _ in range(current_level - parent_level):
    static_link = memory[static_link + STATIC_LINK_OFFSET]
```

### 全局过程（parent_level == 0）

传入 0 作为静态链（全局过程不需要访问外层变量）。

---

## 临时变量存取

### 过程内

临时变量存储在栈帧的负偏移位置：
```
lw $t, offset($fp)   # 加载
sw $t, offset($fp)   # 存储
```

### 主程序

临时变量存储在 .data 段的全局标签处：
```
la $t_addr, tmp_main_t0
lw $t, 0($t_addr)    # 加载
sw $t, 0($t_addr)    # 存储
```

---

## 编译入口：`compile_source()`

完整的编译流水线：
```
1. parse_and_check(source)  — 词法→语法→语义分析
2. SNLIRGenerator.generate() — AST → IR
3. optimize_program(ir)      — IR 优化（可选）
4. IRMIPSGenerator.generate() — IR → MIPS
5. 写入输出文件
```

---

## 已知限制

1. **寄存器池可能耗尽**：复杂表达式可能需要超过 10 个临时寄存器
2. **return 值被丢弃**：return 语句只跳转到过程末尾，不通过寄存器传递返回值
3. **不支持浮点运算**：所有值都是 32 位整数
4. **主程序无栈帧**：主程序的临时变量使用全局存储，不支持递归主程序
5. **参数传递效率**：所有参数都通过栈传递，不利用 $a0-$a3 寄存器

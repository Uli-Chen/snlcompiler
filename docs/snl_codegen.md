# SNL 代码生成器设计文档

本文梳理 `src/snl_codegen.py` 的设计思路和关键实现细节。

## 整体架构

代码生成器将四元式 IR 翻译为 MIPS32 汇编代码。分为两个阶段：

1. **IR → MIPS 翻译**（`IRMIPSGenerator`）：逐条四元式生成对应的 MIPS 指令序列
2. **窥孔优化**（`peephole_optimize`）：在生成的汇编文本上做局部模式匹配优化

整体编译流水线入口是 `compile_source` 函数：

```
源码 → parse_and_check() → SNLIRGenerator → optimize_program() → IRMIPSGenerator → 窥孔优化 → .asm
```

## 存储模型

### 全局变量

全局变量分配在 `.data` 段，每个变量一个标签：

```mips
.data
g_x: .word 0           # 标量：1 word
g_arr: .space 40       # 数组：10 * 4 bytes
```

### 主程序临时变量

主程序没有栈帧，临时变量也分配在 `.data` 段：

```mips
tmp_main_t0: .word 0
tmp_main_t1: .word 0
```

### 过程栈帧

过程使用栈帧存储参数、局部变量和临时变量：

```
高地址
┌─────────────────────────┐
│ 参数 N (调用者压入)       │ $fp + 12 + (N-1)*4
│ ...                     │
│ 参数 1                   │ $fp + 12
│ 静态链 (调用者压入)       │ $fp + 8
├─────────────────────────┤
│ 保存的 $ra               │ $fp + 4
│ 保存的 $fp (调用者帧指针) │ $fp + 0  ← $fp 指向这里
├─────────────────────────┤
│ 局部变量 1               │ $fp - 4
│ 局部变量 2               │ $fp - 8
│ ...                     │
│ 临时变量 t0              │ $fp - M
│ 临时变量 t1              │ $fp - M - 4
│ ...                     │ ← $sp 指向这里
└─────────────────────────┘
低地址
```

常量定义：

```python
WORD_SIZE = 4
FP_SAVE_OFFSET = 0       # $fp 保存位置
RA_SAVE_OFFSET = 4       # $ra 保存位置
STATIC_LINK_OFFSET = 8   # 静态链位置
FRAME_HEADER_SIZE = 8    # prologue 保存的字节数
```

## 寄存器使用

### RegisterPool

使用 `$t0`–`$t9` 作为临时寄存器池，简单的栈式分配/释放：

```python
class RegisterPool:
    def alloc(self) -> str    # 分配一个空闲寄存器
    def free(self, reg)       # 归还寄存器
```

### 约定寄存器

| 寄存器 | 用途 |
|--------|------|
| `$fp` | 帧指针，指向当前栈帧的 saved-$fp 位置 |
| `$sp` | 栈指针，指向栈顶 |
| `$ra` | 返回地址 |
| `$v0` | syscall 编号 / 返回值 |
| `$a0` | syscall 参数 |
| `$t0`–`$t9` | 临时寄存器（编译器自由使用） |

## 代码生成策略

### 过程 prologue / epilogue

```mips
# prologue
addi $sp, $sp, -8        # 分配帧头
sw $fp, 0($sp)           # 保存调用者 $fp
sw $ra, 4($sp)           # 保存返回地址
move $fp, $sp            # 建立新帧
addi $sp, $sp, -N        # 分配局部变量和临时变量空间

# epilogue
move $sp, $fp            # 释放局部空间
lw $fp, 0($sp)           # 恢复调用者 $fp
lw $ra, 4($sp)           # 恢复返回地址
addi $sp, $sp, 8         # 弹出帧头
jr $ra                   # 返回
```

### 过程调用

调用序列（caller 侧）：

```mips
# 1. 逆序压入参数
addi $sp, $sp, -4
sw $t0, 0($sp)           # param N
...
addi $sp, $sp, -4
sw $t1, 0($sp)           # param 1

# 2. 压入静态链
addi $sp, $sp, -4
sw $fp, 0($sp)           # 或跟随静态链计算

# 3. 跳转
jal proc_label

# 4. 清理参数空间
addi $sp, $sp, (N+1)*4
```

### 静态链计算

SNL 支持嵌套过程，通过静态链实现词法作用域的变量访问：

```python
def compute_static_link(self, callee_symbol):
    parent_level = callee_symbol.scope_level  # 被调用者声明所在层级
    current_level = self.current_scope_level()
    
    if parent_level == current_level:
        # 调用自己的子过程 → 传自己的 $fp
        move reg, $fp
    elif parent_level < current_level:
        # 调用祖先的子过程 → 沿静态链向上找
        lw reg, 8($fp)           # 第一跳
        for _ in range(hops):
            lw reg, 8(reg)       # 继续跟随
```

### 变量地址计算

```python
def address_of_symbol(self, symbol):
    if symbol.storage == "global":
        la reg, g_label          # 全局变量：加载标签地址
    elif same_scope:
        addi reg, $fp, offset    # 当前作用域：$fp + offset
    else:
        # 外层作用域：沿静态链找到目标帧
        lw reg, 8($fp)
        ... (follow chain)
        addi reg, reg, offset
```

var 参数特殊处理：参数位置存的是地址，需要额外一次 `lw` 解引用。

### 数组下标计算

```mips
# index_addr: base + (index - low) * element_size
addi $t1, $t1, -low          # 减去下界
sll $t1, $t1, shift          # 乘以元素大小（2的幂用移位）
add $t0, $t0, $t1            # 加到基地址
```

当元素大小是 2 的幂时使用 `sll` 移位，否则使用 `mul`。

### 记录字段计算

```mips
# field_addr: base + field_offset
addi $t0, $t0, offset*4      # 直接加偏移（编译期已知）
```

### 条件分支

```mips
# if_false_<: if !(left < right) goto label
bge $t0, $t1, label

# if_false_=: if !(left = right) goto label
bne $t0, $t1, label
```

### I/O

```mips
# read integer
li $v0, 5
syscall
sw $v0, 0($t0)

# read char
li $v0, 12
syscall
sw $v0, 0($t0)

# write integer
move $a0, $t0
li $v0, 1
syscall
li $a0, 10          # 换行
li $v0, 11
syscall

# write char
move $a0, $t0
li $v0, 11
syscall
li $a0, 10
li $v0, 11
syscall
```

### 尾调用优化 (tail_call)

由优化器生成的 `tail_call` 四元式，代码生成器将其翻译为参数覆写 + 跳转：

```mips
# 1. 将新参数压入临时栈空间
addi $sp, $sp, -4
sw $t0, 0($sp)

# 2. 从临时空间弹出，覆写当前帧的参数位置
lw $t0, 0($sp)
addi $sp, $sp, 4
sw $t0, offset($fp)

# 3. 跳转到过程开头（不是 jal，不建新帧）
j proc_start_label
```

## 窥孔优化

在生成的 MIPS 文本上做多轮模式匹配优化：

| 优化 | 模式 | 替换 |
|------|------|------|
| 自移动消除 | `move $t0, $t0` | 删除 |
| 零加消除 | `addi $t0, $t0, 0` | 删除 |
| 跳转到下一标签 | `j L1` 后紧跟 `L1:` | 删除跳转 |
| 立即数折叠 | `li $t0, 0` + `add $t1, $t1, $t0` | `move $t1, $t1` |
| 乘 0 折叠 | `li $t0, 0` + `mul $t1, $t1, $t0` | `li $t1, 0` |
| 乘 1 折叠 | `li $t0, 1` + `mul $t1, $t1, $t0` | `move $t1, $t1` |
| 除 1 折叠 | `li $t0, 1` + `div $t1, $t1, $t0` | `move $t1, $t1` |
| 覆写消除 | `li $t0, 5` + `li $t0, 10` | 删除第一条 |

多轮迭代直到不再有变化（fixed-point）。

## 标签命名

为避免嵌套过程中的标签冲突，使用 scope_path 作为前缀：

```python
def label_name(self, unit, label):
    prefix = mangle(unit.name)
    return f"{prefix}_{label}"
```

嵌套过程的 scope_path 示例：`outer_inner`，生成标签如 `proc_outer_inner`。

## 编译入口 compile_source

```python
def compile_source(source, output, *, optimize=True, peephole=None, 
                   enabled_passes=None, emit_raw_ir=None, emit_ir=None):
    program_ast = parse_and_check(source)       # 前端三阶段
    ir = SNLIRGenerator(program_ast).generate() # IR 生成
    if emit_raw_ir: write(ir.format())          # 导出原始 IR
    if optimize: optimize_program(ir)           # IR 优化
    if emit_ir: write(ir.format())              # 导出优化后 IR
    assembly = IRMIPSGenerator(ir).generate()   # 代码生成 + 窥孔
    output.write_text(assembly)
    return assembly
```

### parse_and_check

前端三阶段检查，任一阶段出错即停止：

1. 词法分析 → 检查 ERROR token
2. 语法分析 → 检查 parser.errors
3. 语义分析 → 检查 semantic.errors

这避免了在残缺 AST 上运行语义分析产生大量误报。

## 设计特点总结

| 特点 | 实现方式 |
|------|---------|
| 栈式寄存器分配 | RegisterPool 简单分配/释放 $t0–$t9 |
| 统一地址模型 | 全局用标签，局部用 $fp+offset，外层用静态链 |
| 静态链支持 | 嵌套过程通过静态链访问外层变量 |
| 两级优化 | IR 级优化 + 汇编级窥孔优化 |
| 窥孔多轮 | fixed-point 迭代直到无变化 |
| 类型感知 I/O | 根据 type_info 选择 integer/char syscall |
| 尾调用支持 | tail_call 四元式翻译为参数覆写 + 跳转 |
| 标签唯一性 | scope_path 前缀避免嵌套过程标签冲突 |

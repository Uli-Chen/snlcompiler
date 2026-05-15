# SNL MIPS 模拟器说明书

## 概述

`snl_runner.py` 是一个 MIPS32 子集解释器，用于测试 SNL 编译器生成的汇编代码。它模拟了 MIPS 处理器的寄存器、内存和基本指令集，无需真实硬件或完整模拟器即可验证编译结果。

---

## 内存模型

- **数据段**：从 `0x10010000` 开始，存放全局变量和常量
- **栈**：从 `0x7FFFEFFC` 向低地址增长
- **内存实现**：使用 Python 字典（稀疏存储），按字（4 字节）寻址

---

## 类定义

### MIPSRunner

```python
class MIPSRunner:
    assembly: str               # 原始汇编文本
    inputs: list[str]           # 预设输入值队列
    output: list[str]           # 输出缓冲区
    regs: dict[str, int]        # 寄存器文件（32 位无符号值）
    memory: dict[int, int]      # 稀疏内存
    data_labels: dict[str, int] # 数据段标签 → 地址
    text_labels: dict[str, int] # 代码段标签 → 指令索引
    instructions: list[str]     # 指令序列（去除标签后）
```

---

## 初始化流程

### `parse_assembly()`

解析汇编文本为内部表示：

```
1. 逐行扫描，去除注释（# 之后的内容）
2. 识别 .data 和 .text 段切换
3. 数据段：解析标签和 .word/.space 指令，分配内存地址
4. 代码段：分离标签（记录指令索引）和指令（加入指令列表）
```

**数据段指令**：
- `.word value` → 在当前地址存储一个字
- `.space size` → 分配 size 字节的零初始化空间

---

## 执行流程

### `run()`

```python
pc = text_labels["main"]  # 从 main 标签开始
while 0 <= pc < len(instructions):
    执行 instructions[pc]
    pc = next_pc  # 默认 pc+1，分支/跳转时修改
```

**安全限制**：最多执行 100000 步，防止无限循环。

### 指令执行

每条指令先用 `split_inst()` 分解为操作码和参数列表，然后按操作码分派执行。

---

## 支持的指令集

### 数据传送

| 指令 | 格式 | 语义 |
| ---- | ---- | ---- |
| `li` | `li $rd, imm` | `$rd = imm` |
| `la` | `la $rd, label` | `$rd = data_labels[label]` |
| `move` | `move $rd, $rs` | `$rd = $rs` |
| `lw` | `lw $rd, offset($rs)` | `$rd = memory[$rs + offset]` |
| `sw` | `sw $rs, offset($rd)` | `memory[$rd + offset] = $rs` |

### 算术运算

| 指令 | 格式 | 语义 |
| ---- | ---- | ---- |
| `add` | `add $rd, $rs, $rt` | `$rd = $rs + $rt` |
| `addi` | `addi $rd, $rs, imm` | `$rd = $rs + imm` |
| `sub` | `sub $rd, $rs, $rt` | `$rd = $rs - $rt` |
| `mul` | `mul $rd, $rs, $rt` | `$rd = $rs * $rt` |
| `div` | `div $rd, $rs, $rt` | `$rd = $rs / $rt`（带除零检查） |

### 分支跳转

| 指令 | 格式 | 语义 |
| ---- | ---- | ---- |
| `beq` | `beq $rs, $rt, label` | `if $rs == $rt: goto label` |
| `bne` | `bne $rs, $rt, label` | `if $rs != $rt: goto label` |
| `bge` | `bge $rs, $rt, label` | `if $rs >= $rt: goto label` |
| `blt` | `blt $rs, $rt, label` | `if $rs < $rt: goto label` |
| `j` | `j label` | `goto label` |
| `jal` | `jal label` | `$ra = pc+1; goto label` |
| `jr` | `jr $rs` | `goto $rs` |

### 系统调用

| `$v0` 值 | 功能 | 输入/输出 |
| --------- | ---- | --------- |
| 1 | 打印整数 | `$a0` 中的值 |
| 5 | 读取整数 | 结果存入 `$v0` |
| 10 | 退出程序 | - |
| 11 | 打印字符 | `$a0` 的低 8 位 |
| 12 | 读取字符 | 结果存入 `$v0` |

---

## 关键实现细节

### 有符号数处理：`signed(value)`

所有寄存器存储为 32 位无符号值。比较和除法操作前需要转换为有符号解释：

```python
def signed(self, value: int) -> int:
    value &= 0xFFFFFFFF
    return value - 0x100000000 if value & 0x80000000 else value
```

### 地址解析：`address(operand)`

支持两种寻址格式：
- 标签名：直接查 `data_labels`
- `offset($reg)`：计算 `regs[$reg] + offset`

### 输入处理

`inputs` 列表按顺序消费：
- syscall 5（读整数）：弹出一个值转为 int
- syscall 12（读字符）：弹出一个值，如果是字符取其 ASCII 码，如果是数字直接用
- 输入耗尽时返回 0

### $zero 寄存器保护

每条指令执行后强制 `$zero = 0`，防止错误写入。

---

## 使用方式

```bash
# 基本运行
python3 src/snl_runner.py output.asm

# 提供输入值
python3 src/snl_runner.py output.asm --input 5 10 3

# 在编译流水线中使用
python3 compiler.py compile test.snl -o test.asm
python3 src/snl_runner.py test.asm --input 42
```

---

## 已知限制

1. **步数限制**：最多 100000 步，深度递归可能超限
2. **不支持浮点**：没有浮点寄存器和浮点指令
3. **不支持伪指令**：只支持编译器实际生成的指令子集
4. **内存无保护**：不检查栈溢出或非法地址访问
5. **除法截断方向**：使用 Python 的 `int(a/b)` 语义（向零截断）

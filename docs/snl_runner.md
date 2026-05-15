# SNL MIPS 运行器设计文档

本文梳理 `src/snl_runner.py` 的设计思路和关键实现细节。

## 整体架构

`MIPSRunner` 是一个 MIPS32 子集的解释执行器，用于在没有真实 MIPS 硬件或模拟器（SPIM/MARS）的环境下验证编译器输出的正确性。同时支持执行统计和 profile 收集，为 PGO 优化提供数据。

设计目标：

- **正确性验证**：确保编译器生成的汇编能正确执行
- **性能分析**：收集指令计数、分支统计、栈深度等指标，用于评估优化效果

## 内存模型

```
0x10010000+  .data 段（全局变量、临时变量）
0x7FFFEFFC   初始 $sp（栈顶，向低地址增长）
```

- 内存使用 `dict[int, int]` 稀疏表示，每个地址存一个 32 位 word
- 所有值以无符号 32 位存储，需要时通过 `signed()` 转为有符号解释

## 支持的指令集

### 数据传送

| 指令 | 语义 |
|------|------|
| `li rd, imm` | rd = imm |
| `la rd, label` | rd = data_labels[label] |
| `move rd, rs` | rd = rs |
| `lw rd, offset(rs)` | rd = memory[rs + offset] |
| `sw rs, offset(rd)` | memory[rd + offset] = rs |

### 算术运算

| 指令 | 语义 |
|------|------|
| `add rd, rs, rt` | rd = rs + rt |
| `addi rd, rs, imm` | rd = rs + imm |
| `sub rd, rs, rt` | rd = rs - rt |
| `mul rd, rs, rt` | rd = rs * rt |
| `div rd, rs, rt` | rd = rs / rt（向零截断，除零报错） |
| `sll rd, rs, shamt` | rd = rs << shamt |

### 分支跳转

| 指令 | 语义 |
|------|------|
| `beq rs, rt, label` | if rs == rt goto label |
| `bne rs, rt, label` | if rs != rt goto label |
| `bge rs, rt, label` | if rs >= rt goto label |
| `blt rs, rt, label` | if rs < rt goto label |
| `j label` | goto label |
| `jal label` | $ra = pc+1; goto label |
| `jr rs` | goto rs |

### 系统调用

| $v0 | 功能 | 参数 | 返回 |
|-----|------|------|------|
| 1 | 打印整数 | $a0 = 值 | — |
| 5 | 读取整数 | — | $v0 = 值 |
| 10 | 退出程序 | — | — |
| 11 | 打印字符 | $a0 = ASCII | — |
| 12 | 读取字符 | — | $v0 = ASCII |

## 执行流程

```python
def run(self) -> str:
    pc = self.text_labels["main"]
    while 0 <= pc < len(self.instructions):
        # 1. 步数限制检查
        # 2. 标签计数（profile）
        # 3. 解码并执行指令
        # 4. 分支统计
        # 5. 更新 pc
    return "".join(self.output)
```

### 输入处理

输入通过 `--input` 参数提供，按顺序被 read syscall 消费：

- syscall 5（读整数）：直接 `int(input)`
- syscall 12（读字符）：如果输入是纯数字则当作字符编码，否则取第一个字符的 ASCII 值
- 输入耗尽时返回 0

### 安全限制

- `max_steps`：默认 100000 步，防止无限循环
- 除零检测：`div` 指令除数为 0 时抛出 `RunnerError`
- 未知指令：遇到不支持的指令立即报错

## 汇编解析

`parse_assembly` 将汇编文本解析为内部表示：

```python
def parse_assembly(self):
    # .data 段：解析标签和初始化数据
    #   label: .word 0      → data_labels[label] = addr; memory[addr] = 0
    #   label: .space 40    → data_labels[label] = addr; 初始化 10 个 word 为 0
    
    # .text 段：解析标签和指令
    #   label:              → text_labels[label] = pc
    #   add $t0, $t1, $t2  → instructions[pc] = "add $t0, $t1, $t2"
```

## 执行统计 stats()

| 指标 | 含义 |
|------|------|
| `static_instructions` | 静态指令数（代码大小） |
| `dynamic_steps` | 动态执行步数（运行时间） |
| `memory_loads` | lw 执行次数 |
| `memory_stores` | sw 执行次数 |
| `memory_ops` | 总内存操作次数 |
| `arithmetic_ops` | 算术指令执行次数 |
| `branch_ops` | 所有跳转指令执行次数 |
| `conditional_branch_ops` | 条件分支执行次数 |
| `taken_conditional_branches` | 条件分支跳转次数 |
| `syscalls` | syscall 执行次数 |
| `max_stack_words` | 最大栈深度（word 数） |

## Profile 输出

`profile()` 输出 JSON 格式的执行剖面，用于分析程序运行时行为：

```json
{
  "format": "snl-mips-profile-v1",
  "labels": {
    "main": 1,
    "proc_foo": 100,
    "proc_foo_Lwhile0": 1000
  },
  "branches": {
    "proc_foo_Lendwhile0": {
      "taken": 999,
      "not_taken": 1
    }
  },
  "stats": { ... }
}
```

### labels

每个标签的执行次数。循环头标签的计数反映循环迭代次数，可用于识别热循环。

### branches

每个分支目标的 taken/not_taken 计数。用于分析循环行为（taken >> not_taken 说明循环体执行频繁）。

## 命令行接口

```bash
# 基本运行
python3 src/snl_runner.py output.asm

# 提供输入
python3 src/snl_runner.py output.asm --input 10 20 30

# 输出统计信息
python3 src/snl_runner.py output.asm --stats

# 收集 profile
python3 src/snl_runner.py output.asm --input 1000 --profile-out profile.json

# 限制最大步数
python3 src/snl_runner.py output.asm --max-steps 500000
```

## 设计特点总结

| 特点 | 实现方式 |
|------|---------|
| 稀疏内存 | dict 存储，只记录被写入的地址 |
| 32 位模拟 | 所有值 & 0xFFFFFFFF，需要时有符号解释 |
| SPIM 兼容 | syscall 编号与 SPIM/MARS 一致 |
| Profile 收集 | 标签计数 + 分支方向统计 |
| 步数限制 | 防止无限循环，可配置上限 |
| 零开销 | 纯 Python 实现，无外部依赖 |

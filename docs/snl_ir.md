# SNL 中间表示（IR）说明书

## 概述

`snl_ir.py` 定义了 SNL 编译器的四元式中间表示数据结构。四元式 IR 是连接前端（语义分析）和后端（代码生成）的桥梁，也是优化器的操作对象。

---

## 四元式格式

每条四元式的通用格式为：

```
(操作符, 操作数1, 操作数2, 结果)
```

操作数（Operand）可以是以下类型之一：
- `int`：整数字面量常量
- `str`：临时变量名（如 `t0`、`t1`）或标签名（如 `L0`、`Lelse1`）
- `Symbol` 对象：语义分析阶段的符号引用
- `None`：该位置未使用（打印为 `_`）

---

## 类定义

### Quad

```python
@dataclass
class Quad:
    op: str              # 操作符
    arg1: Operand        # 第一操作数
    arg2: Operand        # 第二操作数
    result: Operand      # 结果/目标
    type_info: Any       # 关联的类型信息（代码生成用）
    symbol: Any          # 关联的 Symbol（addr/call 指令用）
    note: str            # 调试注释（不影响语义）
```

### IRUnit

```python
@dataclass
class IRUnit:
    name: str                       # 编译单元名称
    quads: list[Quad]               # 四元式序列
    temp_types: dict[str, Any]      # 临时变量名 → 类型映射
    lexical_level: int              # 词法嵌套层级
```

**说明**：`temp_types` 记录每个临时变量的类型，代码生成阶段用于确定存储大小。

### IRProcedure

```python
@dataclass
class IRProcedure(IRUnit):
    symbol: Any                         # 过程的 Symbol
    params: list[Any]                   # 形参 Symbol 列表
    locals: list[Any]                   # 局部变量 Symbol 列表
    children: list[IRProcedure]         # 嵌套子过程
    end_label: str                      # 过程返回标签
```

**说明**：过程以树形结构组织，`children` 包含直接嵌套的子过程。代码生成时需要展平这棵树。

### IRProgram

```python
@dataclass
class IRProgram:
    globals: list[Any]              # 全局变量 Symbol 列表
    procedures: list[IRProcedure]   # 顶层过程列表
    main: IRUnit                    # 主程序体
```

---

## 操作符一览

### 算术运算
| 操作符 | 含义 | 格式 |
|--------|------|------|
| `+` | 加法 | `(+, arg1, arg2, result)` |
| `-` | 减法 | `(-, arg1, arg2, result)` |
| `*` | 乘法 | `(*, arg1, arg2, result)` |
| `/` | 除法 | `(/, arg1, arg2, result)` |

### 赋值与数据移动
| 操作符 | 含义 | 格式 |
|--------|------|------|
| `assign` | 赋值 | `(assign, value, _, dest_temp)` |
| `load` | 从地址加载值 | `(load, addr_temp, _, result_temp)` |
| `store` | 向地址存储值 | `(store, value, _, addr_temp)` |

### 地址计算
| 操作符 | 含义 | 格式 |
|--------|------|------|
| `addr` | 取变量地址 | `(addr, _, _, result_temp)` + symbol 属性 |
| `index_addr` | 数组元素地址 | `(index_addr, base_addr, index, result_temp)` + type_info |
| `field_addr` | 记录字段地址 | `(field_addr, base_addr, field_name, result_temp)` + type_info |

### 控制流
| 操作符 | 含义 | 格式 |
|--------|------|------|
| `label` | 标签定义 | `(label, _, _, label_name)` |
| `goto` | 无条件跳转 | `(goto, _, _, label_name)` |
| `if_false_<` | 不满足 < 时跳转 | `(if_false_<, left, right, label_name)` |
| `if_false_=` | 不满足 = 时跳转 | `(if_false_=, left, right, label_name)` |

### 过程调用
| 操作符 | 含义 | 格式 |
|--------|------|------|
| `param` | 传递参数 | `(param, value_or_addr, mode, _)` |
| `call` | 调用过程 | `(call, symbol, arg_count, _)` |
| `return` | 返回 | `(return, value, _, _)` |

### I/O
| 操作符 | 含义 | 格式 |
|--------|------|------|
| `read` | 读取输入 | `(read, _, _, addr_temp)` |
| `write` | 输出值 | `(write, value, _, _)` |

---

## 辅助函数

### `flatten_procedures(procedures)`

将嵌套的过程树展平为线性列表。采用后序遍历：子过程在前，父过程在后。这保证代码生成时子过程的代码先于父过程出现。

### `is_temp(value)` / `is_label(value)`

通过字符串前缀判断操作数类型：
- 临时变量以 `t` 开头（如 `t0`、`t12`）
- 标签以 `L` 开头（如 `L0`、`Lelse1`）

### `fmt_operand(value)`

格式化操作数用于打印：
- `None` → `_`
- `int` → 数字字符串
- `str` → 原样输出
- 其他对象 → 取 `name` 属性或 `repr()`

---

## 设计决策

1. **地址统一为临时变量**：所有左值访问都通过 `addr`/`index_addr`/`field_addr` 生成地址临时量，后端只需理解"从地址加载"和"向地址存储"两种操作
2. **类型信息附加在四元式上**：`type_info` 字段让后端无需重新查询符号表即可确定操作数大小
3. **过程树结构**：保留嵌套关系便于静态链计算和存储分配

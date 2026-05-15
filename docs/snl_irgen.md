# SNL 中间代码生成器说明书

## 概述

`snl_irgen.py` 将经过语义检查的 AST 转换为四元式 IR。这是编译器前端到后端的关键转换步骤。

---

## 类定义

### UnitBuilder

```python
class UnitBuilder:
    unit: IRUnit        # 当前构建的 IR 单元
    temp_counter: int   # 临时变量计数器
    label_counter: int  # 标签计数器
```

**职责**：为一个编译单元（主程序或过程）管理临时变量和标签的分配，提供 `emit()` 方法发射四元式。

**方法**：
- `temp(type_info)` → 分配新临时变量 `t0`, `t1`, ...
- `label(prefix)` → 分配新标签 `L0`, `L1`, ...（可自定义前缀如 `Lelse`）
- `emit(op, arg1, arg2, result, **kwargs)` → 创建 Quad 并追加到当前单元

### SNLIRGenerator

```python
class SNLIRGenerator:
    ast: Program  # 输入的 AST
```

**职责**：遍历 AST 生成完整的 IRProgram。

---

## 生成流程

### 入口：`generate()`

```
1. 收集全局变量 Symbol 列表
2. 为每个顶层过程递归生成 IRProcedure
3. 为主程序体生成四元式
4. 返回 IRProgram
```

### 过程生成：`emit_procedure(proc)`

```
1. 获取过程的 Symbol
2. 创建 IRProcedure（包含形参、局部变量、词法层级）
3. 递归处理嵌套子过程
4. 生成过程体的四元式
5. 在末尾发射返回标签
```

---

## 语句生成

### 赋值语句

```
value = emit_expr(expr)       # 计算右值
address = emit_lvalue(target) # 计算左值地址
emit("store", value, _, address)
```

### 过程调用：`emit_call(stmt)`

```
对每个实参：
  如果是 var 参数：计算地址（emit_lvalue）
  如果是 value 参数：计算值（emit_expr）
  emit("param", operand, mode, _)
emit("call", symbol, arg_count, _)
```

### if 语句：`emit_if(stmt)`

```
生成 else_label 和 end_label
emit_false_branch(condition, else_label)  # 条件为假跳到 else
生成 then_body 的代码
emit("goto", _, _, end_label)             # then 结束跳过 else
emit("label", _, _, else_label)
生成 else_body 的代码
emit("label", _, _, end_label)
```

### while 语句：`emit_while(stmt)`

```
生成 start_label 和 end_label
emit("label", _, _, start_label)
emit_false_branch(condition, end_label)   # 条件为假退出循环
生成 body 的代码
emit("goto", _, _, start_label)           # 回到循环开头
emit("label", _, _, end_label)
```

### read 语句

```
address = emit_lvalue(target)
emit("read", _, _, address)
```

### write 语句

```
value = emit_expr(expr)
emit("write", value, _, _)
```

### return 语句

```
value = emit_expr(expr)
emit("return", value, _, _)
```

---

## 表达式生成：`emit_expr(expr)`

返回一个 Operand（整数常量或临时变量名），表示表达式的计算结果。

| 表达式类型 | 生成策略 |
|-----------|---------|
| ConstExpr | 直接返回整数值 |
| CharExpr | 返回字符的 ASCII 码 |
| VarExpr | 计算地址 → load 到临时变量 |
| BinaryExpr（算术） | 递归计算左右操作数 → 发射运算指令 |
| BinaryExpr（关系） | 抛出错误（关系表达式只能出现在条件位置） |

**关键设计**：关系表达式（`<`、`=`）不能作为普通值使用，只能出现在 if/while 的条件位置。这是因为 SNL 没有布尔变量，关系运算直接翻译为条件跳转。

---

## 左值地址生成：`emit_lvalue(ref)`

返回一个临时变量，持有目标内存单元的地址。

```
1. emit("addr", _, _, t)  # 取变量基地址，symbol 属性指向声明
2. 对每个选择器：
   - IndexSelector:
     index = emit_expr(selector.expr)
     emit("index_addr", base, index, new_base)  # 计算数组元素地址
   - FieldSelector:
     emit("field_addr", base, field_name, new_base)  # 计算字段地址
     如果字段后还有 IndexSelector：
       emit("index_addr", new_base, index, final_base)
3. 返回最终地址临时变量
```

---

## 条件跳转：`emit_false_branch(condition, false_label)`

将关系表达式翻译为"条件为假时跳转"的指令：

- `a < b` → `emit("if_false_<", a, b, label)` — 即 `if !(a < b) goto label`
- `a = b` → `emit("if_false_=", a, b, label)` — 即 `if !(a = b) goto label`

---

## 辅助函数

### `collect_decl_symbols(declarations)`

从变量声明列表中收集所有已绑定的 Symbol 对象。

### `require_symbol(value, name)` / `require_expr(expr)`

断言辅助函数，确保语义分析阶段已正确绑定 Symbol 和表达式。如果断言失败说明前端有 bug。

---

## 已知限制

1. **关系表达式不能作为值**：`x := a < b` 会在 IR 生成阶段报错，因为 SNL 没有布尔变量
2. **return 值被丢弃**：`emit_return` 生成了 return 四元式，但后端只是跳转到过程末尾，不传递返回值
3. **不支持多维数组直接访问**：虽然语义分析支持多层选择器，但 IR 生成依赖 AST 的选择器结构

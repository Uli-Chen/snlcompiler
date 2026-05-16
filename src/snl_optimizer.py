#!/usr/bin/env python3
"""IR-level optimizations for SNL."""

from __future__ import annotations

try:
    from playground.snlcompiler.src.snl_ir import IRProcedure, IRProgram, IRUnit, Operand, Quad, flatten_procedures, is_temp
except ModuleNotFoundError:
    from snl_ir import IRProcedure, IRProgram, IRUnit, Operand, Quad, flatten_procedures, is_temp


# ── 常量与配置 ──────────────────────────────────────────────────────────────

PURE_EXPR_OPS = {"+", "-", "*", "/", "addr", "index_addr", "field_addr", "load"}
REMOVABLE_TEMP_OPS = PURE_EXPR_OPS | {"assign"}
HOISTABLE_OPS = {"+", "-", "*", "/", "assign", "addr", "index_addr", "field_addr", "load"}
COMMUTATIVE_OPS = {"+", "*"}
BLOCK_END_OPS = {"goto", "return", "call", "tail_call", "read"}
CALL_PARAM_SETUP_OPS = PURE_EXPR_OPS | {"assign", "param"}

ALL_PASSES = ("fold", "algebra", "gvn", "copy_prop", "dce", "licm", "strength_red", "tail_rec")


# ── 顶层入口 ────────────────────────────────────────────────────────────────

def optimize_program(
    program: IRProgram,
    *,
    enabled_passes: set[str] | None = None,
) -> IRProgram:
    """Optimize the IR program.

    If *enabled_passes* is None, all passes are enabled.
    Otherwise only the named passes run.  Valid names: fold, algebra, gvn,
    copy_prop, dce, licm, strength_red, tail_rec.
    """
    passes = set(ALL_PASSES) if enabled_passes is None else enabled_passes
    for proc in flatten_procedures(program.procedures):
        optimize_unit(proc, passes)
        if "tail_rec" in passes:
            eliminate_tail_recursion(proc)
            optimize_unit(proc, passes)
    optimize_unit(program.main, passes)
    return program


def optimize_unit(unit: IRUnit, passes: set[str] | None = None) -> None:
    """对单个 IR 单元（过程或主程序）运行指定的优化 pass。

    Pass 执行顺序：
      第一轮：fold → algebra → gvn → copy_prop → dce → licm → strength_red
      第二轮：copy_prop → dce → fold → dce
    两轮设计的原因：LICM 将循环不变式外提后，可能暴露新的常量和死代码，
    第二轮负责清理这些残留，使优化效果收敛。
    """
    if passes is None:
        passes = set(ALL_PASSES)

    # 第一轮：主要化简
    if "fold" in passes:
        unit.quads = fold_constants(unit.quads)
    if "algebra" in passes:
        unit.quads = simplify_algebra(unit.quads)
    if "gvn" in passes:
        unit.quads = global_value_numbering(unit.quads)
    if "copy_prop" in passes:
        unit.quads = propagate_copies(unit.quads)
    if "dce" in passes:
        unit.quads = eliminate_dead_temp_assignments(unit.quads)
    if "licm" in passes:
        unit.quads = hoist_loop_invariants(unit.quads)
    if "strength_red" in passes:
        unit.quads = reduce_strength(unit.quads)

    # 第二轮：清理 LICM / strength_red 后暴露的冗余
    if "copy_prop" in passes:
        unit.quads = propagate_copies(unit.quads)
    if "dce" in passes:
        unit.quads = eliminate_dead_temp_assignments(unit.quads)
    if "fold" in passes:
        unit.quads = fold_constants(unit.quads)
    if "dce" in passes:
        unit.quads = eliminate_dead_temp_assignments(unit.quads)


# ── 基础 Pass ────────────────────────────────────────────────────────────────

def fold_constants(quads: list[Quad]) -> list[Quad]:
    constants: dict[str, int] = {}
    optimized: list[Quad] = []

    for quad in quads:
        quad = replace_known_constants(quad, constants)

        if quad.op in {"+", "-", "*", "/"} and isinstance(quad.arg1, int) and isinstance(quad.arg2, int):
            folded = eval_arithmetic(quad.op, quad.arg1, quad.arg2)
            if folded is not None and isinstance(quad.result, str):
                constants[quad.result] = folded
                optimized.append(Quad("assign", folded, None, quad.result, type_info=quad.type_info, note="常量折叠"))
                continue

        if quad.op in {"if_false_<", "if_false_="} and isinstance(quad.arg1, int) and isinstance(quad.arg2, int):
            condition_true = quad.arg1 < quad.arg2 if quad.op == "if_false_<" else quad.arg1 == quad.arg2
            if condition_true:
                continue
            optimized.append(Quad("goto", None, None, quad.result, note="常量条件折叠"))
            continue

        if quad.op == "assign" and isinstance(quad.result, str):
            if isinstance(quad.arg1, int):
                constants[quad.result] = quad.arg1
            else:
                constants.pop(quad.result, None)
        elif isinstance(quad.result, str):
            constants.pop(quad.result, None)

        if quad.op in {"store", "read", "call", "tail_call"}:
            constants.clear()
        optimized.append(quad)

    return optimized


def eval_arithmetic(op: str, left: int, right: int) -> int | None:
    """对两个编译期常量执行算术运算，返回结果或 None（除零时）。

    除法使用 int(a/b) 而非 a//b，以匹配 MIPS div 指令的向零截断语义
    （Python 的 // 是向负无穷截断，对负数结果不同）。
    """
    if op == "+":
        return left + right
    if op == "-":
        return left - right
    if op == "*":
        return left * right
    if op == "/" and right != 0:
        # 向零截断，与 MIPS div 一致：int(-7/2) == -3，而 -7//2 == -4
        return int(left / right)
    return None


def simplify_algebra(quads: list[Quad]) -> list[Quad]:
    optimized: list[Quad] = []
    for quad in quads:
        if quad.op not in {"+", "-", "*", "/"} or not isinstance(quad.result, str):
            optimized.append(quad)
            continue

        simplified = simplify_arithmetic_quad(quad)
        optimized.append(simplified if simplified is not None else quad)
    return optimized


def simplify_arithmetic_quad(quad: Quad) -> Quad | None:
    result = quad.result
    if quad.op == "+":
        if quad.arg1 == 0:
            return Quad("assign", quad.arg2, None, result, quad.type_info, quad.symbol, "代数化简")
        if quad.arg2 == 0:
            return Quad("assign", quad.arg1, None, result, quad.type_info, quad.symbol, "代数化简")
    if quad.op == "-":
        if quad.arg2 == 0:
            return Quad("assign", quad.arg1, None, result, quad.type_info, quad.symbol, "代数化简")
    if quad.op == "*":
        if quad.arg1 == 0 or quad.arg2 == 0:
            return Quad("assign", 0, None, result, quad.type_info, quad.symbol, "代数化简")
        if quad.arg1 == 1:
            return Quad("assign", quad.arg2, None, result, quad.type_info, quad.symbol, "代数化简")
        if quad.arg2 == 1:
            return Quad("assign", quad.arg1, None, result, quad.type_info, quad.symbol, "代数化简")
    if quad.op == "/":
        if quad.arg2 == 1:
            return Quad("assign", quad.arg1, None, result, quad.type_info, quad.symbol, "代数化简")
        if quad.arg1 == 0 and isinstance(quad.arg2, int) and quad.arg2 != 0:
            return Quad("assign", 0, None, result, quad.type_info, quad.symbol, "代数化简")
    return None


def global_value_numbering(quads: list[Quad]) -> list[Quad]:
    """全局值编号（GVN）——CSE 的超集。

    与局部 CSE 不同，GVN 在跨基本块边界时不会无条件清除已知表达式：
      - 仅由 fall-through 到达的标签：保留前驱块的值编号
      - 跳转目标标签（任何 goto/条件跳转的目标）：清除所有值编号
      - store/read/call：仅清除内存相关表达式（load），保留纯算术表达式

    值表键：(op, canonical(arg1), canonical(arg2), type_key) → 首次计算该值的临时变量
    """
    # 找出所有跳转目标标签（goto 或条件跳转的目标）
    jump_targets: set[str] = set()
    for quad in quads:
        if quad.op == "goto" and isinstance(quad.result, str):
            jump_targets.add(quad.result)
        elif quad.op.startswith("if_") and isinstance(quad.result, str):
            jump_targets.add(quad.result)
    aliases: dict[str, Operand] = {}
    expressions: dict[tuple, str] = {}
    optimized: list[Quad] = []

    for quad in quads:
        if quad.op == "label" and isinstance(quad.result, str):
            if quad.result in jump_targets:
                # 跳转目标：多个前驱，必须清除
                aliases.clear()
                expressions.clear()
            # 仅 fall-through 到达的标签：保留值编号

        normalized = normalize_quad(quad, aliases)
        if normalized.op in PURE_EXPR_OPS and isinstance(normalized.result, str):
            key = expression_key(normalized, aliases)
            if key in expressions:
                aliases[normalized.result] = expressions[key]
                optimized.append(
                    Quad(
                        "assign",
                        expressions[key],
                        None,
                        normalized.result,
                        type_info=normalized.type_info,
                        note="GVN",
                    )
                )
                continue
            expressions[key] = normalized.result
        elif normalized.op == "assign" and isinstance(normalized.result, str):
            aliases[normalized.result] = canonical_operand(normalized.arg1, aliases)

        optimized.append(normalized)

        # store/read/call 可能改变内存，清除 load 相关表达式但保留纯算术。
        if normalized.op in {"store", "read", "call", "tail_call"}:
            expressions = {k: v for k, v in expressions.items() if k[0] != "load"}

        if ends_block(normalized):
            aliases.clear()
            expressions.clear()

    return optimized


def propagate_copies(quads: list[Quad]) -> list[Quad]:
    aliases: dict[str, Operand] = {}
    optimized: list[Quad] = []

    for quad in quads:
        if starts_new_block(quad):
            aliases.clear()

        normalized = normalize_quad_for_op(quad, aliases)
        optimized.append(normalized)

        if normalized.op == "assign" and is_temp(normalized.result):
            aliases[str(normalized.result)] = canonical_operand(normalized.arg1, aliases)
        elif is_temp(defined_temp(normalized)):
            aliases.pop(str(normalized.result), None)

        if ends_block(normalized):
            aliases.clear()

    return optimized


def eliminate_dead_temp_assignments(quads: list[Quad]) -> list[Quad]:
    live: set[str] = set()
    kept: list[Quad] = []

    for quad in reversed(quads):
        target = defined_temp(quad)
        if target is not None and target not in live and quad.op in REMOVABLE_TEMP_OPS:
            continue

        if target is not None:
            live.discard(target)
        live.update(temp_uses(quad))
        kept.append(quad)

    kept.reverse()
    return kept


def starts_new_block(quad: Quad) -> bool:
    return quad.op == "label"


def ends_block(quad: Quad) -> bool:
    return quad.op in BLOCK_END_OPS or quad.op.startswith("if_false_")


# ── 循环 Pass（licm）────────────────────────────────────────────────────────

def hoist_loop_invariants(quads: list[Quad]) -> list[Quad]:
    optimized = list(quads)
    loops = find_simple_loops(optimized)
    for start, end in reversed(loops):
        hoisted, body = extract_loop_invariants(optimized[start + 1 : end])
        if hoisted:
            optimized = optimized[:start] + hoisted + [optimized[start]] + body + optimized[end:]
    return optimized


# ── 强度削减 Pass（strength reduction）─────────────────────────────────────

def reduce_strength(quads: list[Quad]) -> list[Quad]:
    """将循环内 index_addr 的乘法替换为循环外初始化 + 循环内加法。"""
    optimized = list(quads)
    loops = find_simple_loops(optimized)
    temp_counter = _max_temp_index(optimized) + 1

    for start, end in reversed(loops):
        body = optimized[start + 1 : end]
        induction_vars = _find_induction_variables(body)
        if not induction_vars:
            continue

        replacements: list[tuple[int, str, str, int]] = []
        for body_idx, quad in enumerate(body):
            if quad.op != "index_addr" or not isinstance(quad.result, str):
                continue
            iv_name = _resolve_iv(quad.arg2, body, induction_vars)
            if iv_name is None:
                continue
            stride = induction_vars[iv_name]
            replacements.append((body_idx, iv_name, str(quad.arg1), stride))

        if not replacements:
            continue

        pre_loop: list[Quad] = []
        replacement_info: list[tuple[int, str, int, int | None]] = []

        for body_idx, iv_name, base_temp, stride in replacements:
            original_quad = body[body_idx]
            sr_temp = f"t{temp_counter}"
            temp_counter += 1

            array_type = original_quad.type_info
            element_words = _type_size_words(array_type)
            byte_stride = stride * element_words * 4

            pre_loop.append(Quad(
                "index_addr", original_quad.arg1, original_quad.arg2,
                sr_temp, type_info=original_quad.type_info,
                symbol=original_quad.symbol, note="强度削减：初始地址"
            ))

            iv_update_idx = _find_iv_update_index(body, iv_name)
            replacement_info.append((body_idx, sr_temp, byte_stride, iv_update_idx))

        insertions: list[tuple[int, Quad]] = []

        for body_idx, sr_temp, byte_stride, iv_update_idx in replacement_info:
            original = body[body_idx]
            body[body_idx] = Quad(
                "assign", sr_temp, None, original.result,
                type_info=original.type_info, symbol=original.symbol,
                note="强度削减：复用地址"
            )
            if iv_update_idx is not None:
                increment_quad = Quad(
                    "+", sr_temp, byte_stride, sr_temp,
                    note="强度削减：地址增量"
                )
                insertions.append((iv_update_idx, increment_quad))

        insertions.sort(key=lambda x: x[0], reverse=True)
        for insert_after, quad in insertions:
            body.insert(insert_after + 1, quad)

        optimized = optimized[:start] + pre_loop + [optimized[start]] + body + optimized[end:]

    return optimized


def _max_temp_index(quads: list[Quad]) -> int:
    """找出四元式列表中最大的临时变量编号。"""
    max_idx = -1
    for quad in quads:
        for operand in (quad.arg1, quad.arg2, quad.result):
            if isinstance(operand, str) and operand.startswith("t"):
                try:
                    idx = int(operand[1:])
                    if idx > max_idx:
                        max_idx = idx
                except ValueError:
                    pass
    return max_idx


def _find_induction_variables(body: list[Quad]) -> dict[str, int]:
    """在循环体中找出归纳变量及其步长。"""
    addr_symbols: dict[str, str] = {}
    for quad in body:
        if quad.op == "addr" and isinstance(quad.result, str):
            name = getattr(quad.symbol, "name", None)
            if name:
                addr_symbols[str(quad.result)] = str(name)

    store_targets: dict[str, list[int]] = {}
    for idx, quad in enumerate(body):
        if quad.op == "store" and isinstance(quad.result, str) and quad.result in addr_symbols:
            store_targets.setdefault(quad.result, []).append(idx)

    induction_vars: dict[str, int] = {}

    for addr_temp, store_indices in store_targets.items():
        if len(store_indices) != 1:
            continue
        store_idx = store_indices[0]
        store_quad = body[store_idx]
        stored_value = store_quad.arg1
        if not is_temp(stored_value):
            continue

        symbol_name = addr_symbols[addr_temp]
        stride = _trace_induction_pattern(body, str(stored_value), symbol_name, addr_symbols)
        if stride is not None:
            induction_vars[addr_temp] = stride

    return induction_vars


def _trace_induction_pattern(
    body: list[Quad], value_temp: str, symbol_name: str, addr_symbols: dict[str, str]
) -> int | None:
    """追踪 value_temp 的定义链，检查是否是 load(symbol) + constant 模式。"""
    add_quad = None
    for quad in body:
        if quad.op in {"+", "-"} and isinstance(quad.result, str) and quad.result == value_temp:
            add_quad = quad
            break
    if add_quad is None:
        return None

    if isinstance(add_quad.arg2, int) and is_temp(add_quad.arg1):
        stride = add_quad.arg2 if add_quad.op == "+" else -add_quad.arg2
        load_temp = str(add_quad.arg1)
    elif isinstance(add_quad.arg1, int) and is_temp(add_quad.arg2) and add_quad.op == "+":
        stride = add_quad.arg1
        load_temp = str(add_quad.arg2)
    else:
        return None

    for quad in body:
        if quad.op == "load" and isinstance(quad.result, str) and quad.result == load_temp:
            if is_temp(quad.arg1):
                source_symbol = addr_symbols.get(str(quad.arg1))
                if source_symbol == symbol_name:
                    return stride
            break

    return None


def _resolve_iv(operand: Operand, body: list[Quad], induction_vars: dict[str, int]) -> str | None:
    """检查 index_addr 的索引操作数是否来自归纳变量的 load。"""
    if not is_temp(operand):
        return None

    load_source = None
    for quad in body:
        if quad.op == "load" and isinstance(quad.result, str) and quad.result == str(operand):
            load_source = quad.arg1
            break

    if load_source is None or not is_temp(load_source):
        return None

    addr_temp = str(load_source)
    if addr_temp in induction_vars:
        return addr_temp
    return None


def _find_iv_update_index(body: list[Quad], iv_addr_temp: str) -> int | None:
    """找到归纳变量更新（store 到 iv 地址）在 body 中的索引。"""
    for idx, quad in enumerate(body):
        if quad.op == "store" and isinstance(quad.result, str) and quad.result == iv_addr_temp:
            return idx
    return None


def _type_size_words(type_info: object) -> int:
    """从 type_info 获取数组元素大小（以 word 为单位）。"""
    if type_info is None:
        return 1
    element = getattr(type_info, "element", None)
    if element is None:
        return 1
    size = getattr(element, "size", None)
    if isinstance(size, int) and size > 0:
        return size
    return 1


def normalize_quad(quad: Quad, aliases: dict[str, Operand]) -> Quad:
    arg1 = canonical_operand(quad.arg1, aliases)
    arg2 = canonical_operand(quad.arg2, aliases)
    return Quad(quad.op, arg1, arg2, quad.result, quad.type_info, quad.symbol, quad.note)


def normalize_quad_for_op(quad: Quad, aliases: dict[str, Operand]) -> Quad:
    arg1 = canonical_operand(quad.arg1, aliases)
    arg2 = quad.arg2
    result = quad.result

    if quad.op in {"+", "-", "*", "/", "index_addr"} or quad.op.startswith("if_false_"):
        arg2 = canonical_operand(quad.arg2, aliases)
    elif quad.op in {"store", "read"}:
        result = canonical_operand(quad.result, aliases)

    return Quad(quad.op, arg1, arg2, result, quad.type_info, quad.symbol, quad.note)


def expression_key(quad: Quad, aliases: dict[str, Operand]) -> tuple:
    """为四元式生成规范化的 CSE 查找键。

    对交换律操作（+、*）将两个操作数排序，使 a+b 和 b+a 映射到同一个键。
    排序基于 operand_key 返回的 tuple，而非 repr() 字符串——
    repr() 的字典序对 "t9" vs "t10" 这类名称会给出错误顺序。
    """
    arg1 = operand_key(quad.arg1, aliases)
    arg2 = operand_key(quad.arg2, aliases)
    if quad.op in COMMUTATIVE_OPS and arg2 < arg1:
        arg1, arg2 = arg2, arg1
    if quad.op == "addr":
        return (quad.op, getattr(quad.symbol, "name", None))
    return (quad.op, arg1, arg2, type_key(quad.type_info))


def canonical_operand(value: Operand, aliases: dict[str, Operand]) -> Operand:
    seen: set[str] = set()
    while is_temp(value) and value in aliases and value not in seen:
        seen.add(value)
        value = aliases[value]
    return value


def operand_key(value: Operand, aliases: dict[str, Operand]) -> tuple:
    value = canonical_operand(value, aliases)
    if isinstance(value, int):
        return ("const", value)
    if isinstance(value, str):
        return ("temp", value)
    name = getattr(value, "name", None)
    if name:
        return ("symbol", name)
    return ("other", repr(value))


def type_key(type_info: object) -> str:
    display = getattr(type_info, "display", None)
    return display() if callable(display) else repr(type_info)


def replace_known_constants(quad: Quad, constants: dict[str, int]) -> Quad:
    arg1 = constants.get(str(quad.arg1), quad.arg1) if is_temp(quad.arg1) else quad.arg1
    arg2 = constants.get(str(quad.arg2), quad.arg2) if is_temp(quad.arg2) else quad.arg2
    if arg1 is quad.arg1 and arg2 is quad.arg2:
        return quad
    return Quad(quad.op, arg1, arg2, quad.result, quad.type_info, quad.symbol, quad.note)


def defined_temp(quad: Quad) -> str | None:
    if quad.op in {"store", "goto", "return", "param", "write", "writeln"}:
        return None
    if quad.op.startswith("if_false_"):
        return None
    if isinstance(quad.result, str) and is_temp(quad.result):
        return quad.result
    return None


def temp_uses(quad: Quad) -> set[str]:
    uses: set[str] = set()
    if is_temp(quad.arg1):
        uses.add(str(quad.arg1))
    if is_temp(quad.arg2):
        uses.add(str(quad.arg2))
    if quad.op in {"store", "param", "write", "writeln", "return"} and is_temp(quad.result):
        uses.add(str(quad.result))
    if quad.op.startswith("if_false_") and is_temp(quad.result):
        uses.add(str(quad.result))
    return uses


# ── 循环工具 ─────────────────────────────────────────────────────────────────

def find_simple_loops(quads: list[Quad]) -> list[tuple[int, int]]:
    """找出所有简单循环：(label_index, goto_index)。

    简单循环定义为：一个 label 后面某处有一个 goto 跳回该 label。
    """
    label_indices: dict[str, int] = {}
    for idx, quad in enumerate(quads):
        if quad.op == "label" and isinstance(quad.result, str):
            label_indices[quad.result] = idx

    loops: list[tuple[int, int]] = []
    for idx, quad in enumerate(quads):
        if quad.op == "goto" and isinstance(quad.result, str):
            target = quad.result
            if target in label_indices and label_indices[target] < idx:
                loops.append((label_indices[target], idx))
    return loops


def extract_loop_invariants(body: list[Quad]) -> tuple[list[Quad], list[Quad]]:
    """从循环体中提取可外提的不变式四元式。"""
    defined_in_loop: set[str] = set()
    for quad in body:
        if isinstance(quad.result, str) and quad.op not in {"goto", "label"}:
            if not quad.op.startswith("if_false_"):
                defined_in_loop.add(quad.result)

    hoisted: list[Quad] = []
    remaining: list[Quad] = []

    for quad in body:
        if _can_hoist(quad, defined_in_loop):
            hoisted.append(Quad(quad.op, quad.arg1, quad.arg2, quad.result,
                                quad.type_info, quad.symbol, "循环外提"))
            if isinstance(quad.result, str):
                defined_in_loop.discard(quad.result)
        else:
            remaining.append(quad)

    return hoisted, remaining


def _can_hoist(quad: Quad, defined_in_loop: set[str]) -> bool:
    """判断一条四元式是否可以外提。"""
    if quad.op not in PURE_EXPR_OPS:
        return False
    if not isinstance(quad.result, str):
        return False

    def operand_invariant(op: Operand) -> bool:
        if op is None or isinstance(op, int):
            return True
        if isinstance(op, str):
            return op not in defined_in_loop
        return True

    return operand_invariant(quad.arg1) and operand_invariant(quad.arg2)


# ── 尾递归消除 ───────────────────────────────────────────────────────────────

def eliminate_tail_calls(quads: list[Quad], proc_name: str) -> list[Quad]:
    """将过程对自身的尾调用转换为 tail_call 四元式。

    识别模式：
      1. call proc_name → return（中间只有 label）
      2. call proc_name → 过程结尾（中间只有 label，无其他有效指令）
    替换为：tail_call proc_name（codegen 将其翻译为参数重写 + 跳转到入口）
    """
    end_label = f"{proc_name}_return"
    optimized: list[Quad] = []
    i = 0
    while i < len(quads):
        if quads[i].op == "call" and getattr(quads[i].symbol, "name", quads[i].arg1) == proc_name:
            # 检查 call 之后是否只有 label 和/或 return（即尾位置）
            j = i + 1
            labels_between: list[Quad] = []
            while j < len(quads) and quads[j].op == "label":
                labels_between.append(quads[j])
                j += 1
            is_tail = False
            if j < len(quads) and quads[j].op == "return":
                is_tail = True
                # 跳过 return
                j += 1
            elif j >= len(quads):
                # call 后面只有 label 直到过程结束
                is_tail = True
            if is_tail:
                optimized.append(Quad(
                    "tail_call", quads[i].arg1, quads[i].arg2, quads[i].result,
                    quads[i].type_info, quads[i].symbol, "尾递归消除"
                ))
                optimized.extend(labels_between)
                i = j
                continue
        optimized.append(quads[i])
        i += 1
    return optimized


def eliminate_tail_recursion(proc: IRProcedure) -> None:
    """对过程执行尾递归消除（原地修改 proc.quads）。"""
    name = getattr(proc, "name", None)
    if name is None:
        return
    proc.quads = eliminate_tail_calls(proc.quads, name)
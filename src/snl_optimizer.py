


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


def replace_known_constants(quad: Quad, constants: dict[str, int]) -> Quad:
    arg1 = constants.get(quad.arg1, quad.arg1) if isinstance(quad.arg1, str) else quad.arg1
    arg2 = constants.get(quad.arg2, quad.arg2) if isinstance(quad.arg2, str) else quad.arg2
    return Quad(quad.op, arg1, arg2, quad.result, quad.type_info, quad.symbol, quad.note)


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
        # 安全性说明：保留纯算术表达式是安全的，因为 expression_key 使用临时变量名
        # 作为操作数标识。load 被清除后，后续对同一内存位置的 load 会生成新的临时变量，
        # 因此不会与旧的算术表达式 key 匹配。
        if normalized.op in {"store", "read", "call", "tail_call"}:
            expressions = {k: v for k, v in expressions.items() if k[0] != "load"}

        if ends_block(normalized):
            # goto/return/条件跳转后的代码只能通过下一个 label 到达
            # 清除状态，等下一个 label 决定是否恢复
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
    """将循环内 index_addr 的乘法替换为循环外初始化 + 循环内加法。

    识别模式：
      循环内存在 (index_addr, base, iv, result)，其中 iv 是归纳变量
      （每次迭代增加常量 stride）。

    转换为：
      循环前：(index_addr, base, iv_init_value, sr_temp)  — 初始地址
      循环内：原 index_addr → (assign, sr_temp, _, result)
              在归纳变量更新后：(+, sr_temp, byte_stride, sr_temp) — 增量更新

    这消除了循环内的乘法运算，用一次加法替代。
    """
    optimized = list(quads)
    loops = find_simple_loops(optimized)
    # 需要一个全局唯一的临时变量计数器
    temp_counter = _max_temp_index(optimized) + 1

    for start, end in reversed(loops):
        body = optimized[start + 1 : end]
        induction_vars = _find_induction_variables(body)
        if not induction_vars:
            continue

        replacements: list[tuple[int, str, str, int]] = []  # (body_idx, iv, base_temp, stride)
        for body_idx, quad in enumerate(body):
            if quad.op != "index_addr" or not isinstance(quad.result, str):
                continue
            # arg2 是索引操作数——检查它是否是归纳变量
            iv_name = _resolve_iv(quad.arg2, body, induction_vars)
            if iv_name is None:
                continue
            stride = induction_vars[iv_name]
            replacements.append((body_idx, iv_name, str(quad.arg1), stride))

        if not replacements:
            continue

        # 为每个替换生成新的临时变量和前置/后置代码
        pre_loop: list[Quad] = []
        # 记录每个替换的信息：(body_idx, sr_temp, byte_stride, iv_update_idx)
        replacement_info: list[tuple[int, str, int, int | None]] = []

        for body_idx, iv_name, base_temp, stride in replacements:
            original_quad = body[body_idx]
            sr_temp = f"t{temp_counter}"
            temp_counter += 1

            # 计算 byte_stride：需要从 type_info 获取元素大小
            array_type = original_quad.type_info
            element_words = _type_size_words(array_type)
            byte_stride = stride * element_words * 4

            # 前置：用原始 index_addr 计算初始地址（保留原始语义）
            pre_loop.append(Quad(
                "index_addr", original_quad.arg1, original_quad.arg2,
                sr_temp, type_info=original_quad.type_info,
                symbol=original_quad.symbol, note="强度削减：初始地址"
            ))

            # 找到归纳变量更新的位置（body 中 iv := iv + stride 的 store）
            iv_update_idx = _find_iv_update_index(body, iv_name)
            replacement_info.append((body_idx, sr_temp, byte_stride, iv_update_idx))

        # 应用替换：从后向前修改 body 以保持索引稳定
        # 先收集所有需要在 iv 更新后插入的增量指令
        insertions: list[tuple[int, Quad]] = []  # (insert_after_idx, quad)

        for body_idx, sr_temp, byte_stride, iv_update_idx in replacement_info:
            # 替换原始 index_addr 为 assign
            original = body[body_idx]
            body[body_idx] = Quad(
                "assign", sr_temp, None, original.result,
                type_info=original.type_info, symbol=original.symbol,
                note="强度削减：复用地址"
            )
            # 在归纳变量更新后插入增量
            if iv_update_idx is not None:
                increment_quad = Quad(
                    "+", sr_temp, byte_stride, sr_temp,
                    note="强度削减：地址增量"
                )
                insertions.append((iv_update_idx, increment_quad))

        # 按位置从后向前插入增量指令
        insertions.sort(key=lambda x: x[0], reverse=True)
        for insert_after, quad in insertions:
            body.insert(insert_after + 1, quad)

        # 重建：pre_loop + label + body + goto
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
    """在循环体中找出归纳变量及其步长。

    归纳变量模式：
      (addr, _, _, t_addr)     # &iv
      (load, t_addr, _, t_val)
      (+, t_val, stride, t_new)  或  (+, stride, t_val, t_new)
      (store, t_new, _, t_addr2)  # 其中 t_addr2 指向同一变量

    返回 {addr_temp: stride} 映射，其中 addr_temp 是存储归纳变量地址的临时变量。
    """
    # 找出所有 addr 操作及其对应的符号
    addr_symbols: dict[str, str] = {}  # temp → symbol_name
    for quad in body:
        if quad.op == "addr" and isinstance(quad.result, str):
            name = getattr(quad.symbol, "name", None)
            if name:
                addr_symbols[str(quad.result)] = str(name)

    # 找出 store 操作：哪些地址临时变量被写入
    store_targets: dict[str, list[int]] = {}  # addr_temp → [body indices of stores]
    for idx, quad in enumerate(body):
        if quad.op == "store" and isinstance(quad.result, str) and quad.result in addr_symbols:
            store_targets.setdefault(quad.result, []).append(idx)

    # 对每个只被 store 一次的地址，检查是否是 iv += stride 模式
    induction_vars: dict[str, int] = {}  # addr_temp → stride

    for addr_temp, store_indices in store_targets.items():
        if len(store_indices) != 1:
            continue
        store_idx = store_indices[0]
        store_quad = body[store_idx]
        stored_value = store_quad.arg1
        if not is_temp(stored_value):
            continue

        # 向前找 stored_value 的定义：应该是 (+, load_result, stride, stored_value)
        symbol_name = addr_symbols[addr_temp]
        stride = _trace_induction_pattern(body, str(stored_value), symbol_name, addr_symbols)
        if stride is not None:
            induction_vars[addr_temp] = stride

    return induction_vars


def _trace_induction_pattern(
    body: list[Quad], value_temp: str, symbol_name: str, addr_symbols: dict[str, str]
) -> int | None:
    """追踪 value_temp 的定义链，检查是否是 load(symbol) + constant 模式。"""
    # 找到 value_temp 的定义
    add_quad = None
    for quad in body:
        if quad.op in {"+", "-"} and isinstance(quad.result, str) and quad.result == value_temp:
            add_quad = quad
            break
    if add_quad is None:
        return None

    # 确定哪个操作数是常量步长，哪个是 load 结果
    if isinstance(add_quad.arg2, int) and is_temp(add_quad.arg1):
        stride = add_quad.arg2 if add_quad.op == "+" else -add_quad.arg2
        load_temp = str(add_quad.arg1)
    elif isinstance(add_quad.arg1, int) and is_temp(add_quad.arg2) and add_quad.op == "+":
        stride = add_quad.arg1
        load_temp = str(add_quad.arg2)
    else:
        return None

    # 验证 load_temp 来自对同一符号的 load
    for quad in body:
        if quad.op == "load" and isinstance(quad.result, str) and quad.result == load_temp:
            # 检查 load 的源地址是否指向同一符号
            if is_temp(quad.arg1):
                source_symbol = addr_symbols.get(str(quad.arg1))
                if source_symbol == symbol_name:
                    return stride
            break

    return None


def _resolve_iv(operand: Operand, body: list[Quad], induction_vars: dict[str, int]) -> str | None:
    """检查 index_addr 的索引操作数是否来自归纳变量的 load。

    返回归纳变量的地址临时变量名，或 None。
    """
    if not is_temp(operand):
        return None

    # 索引操作数应该是某个 load 的结果
    load_source = None
    for quad in body:
        if quad.op == "load" and isinstance(quad.result, str) and quad.result == str(operand):
            load_source = quad.arg1
            break

    if load_source is None or not is_temp(load_source):
        return None

    # 检查 load 的源地址是否是归纳变量
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
    """从 type_info 获取数组元素大小（以 word 为单位）。

    如果无法确定，默认返回 1（单个 integer/char 占 1 word）。
    """
    if type_info is None:
        return 1
    element = getattr(type_info, "element", None)
    if element is None:
        return 1
    size = getattr(element, "size", None)
    if isinstance(size, int) and size > 0:
        return size
    return 1



# ── 尾递归 Pass ──────────────────────────────────────────────────────────────

def eliminate_tail_recursion(proc: IRProcedure) -> None:
    """将过程末尾的自递归调用转换为跳转，消除调用栈增长。

    识别模式：call self → (纯后缀) → return
    转换为：  tail_call self（跳回过程入口标签，复用当前栈帧）

    param 四元式通过索引集合删除，避免用 id() 比较对象身份带来的脆弱性。
    """
    entry_label = f"{proc.name}_tail_entry"
    quads = proc.quads
    optimized: list[Quad] = []
    # 记录需要从 optimized 中删除的 param 四元式的原始索引
    param_indexes_to_remove: set[int] = set()
    changed = False
    index = 0

    while index < len(quads):
        quad = quads[index]
        params = self_tail_call_params(proc, quads, index)
        if params is not None:
            if params:
                # 通过对象 id 定位 param 四元式——params 中的对象与 optimized 中的
                # 是同一个 Python 对象（来自 quads 列表），因此 id() 比较有效。
                param_set = set(id(p) for p in params)
                optimized = [q for q in optimized if id(q) not in param_set]
            optimized.append(Quad("tail_call", list(params), None, entry_label, symbol=proc.symbol, note="尾递归优化"))
            changed = True
            index += 1
            continue
        optimized.append(quad)
        index += 1

    if changed:
        proc.quads = [Quad("label", result=entry_label, note="尾递归入口")] + optimized


def self_tail_call_params(proc: IRProcedure, quads: list[Quad], index: int) -> list[Quad] | None:
    quad = quads[index]
    if quad.op != "call" or called_procedure_name(quad) != proc.name:
        return None
    param_count = len(proc.params)
    if quad.arg2 != param_count:
        return None
    params = collect_call_params(quads, index, param_count)
    if params is None:
        return None
    if not is_tail_suffix(quads[index + 1 :], proc.end_label):
        return None
    return params


def collect_call_params(quads: list[Quad], call_index: int, param_count: int) -> list[Quad] | None:
    if param_count == 0:
        return []

    params: list[Quad] = []
    index = call_index - 1
    while index >= 0 and len(params) < param_count:
        quad = quads[index]
        if quad.op == "param":
            params.append(quad)
        elif quad.op not in CALL_PARAM_SETUP_OPS:
            return None
        index -= 1

    if len(params) != param_count:
        return None
    return list(reversed(params))


def called_procedure_name(quad: Quad) -> str:
    name = getattr(quad.symbol, "name", None)
    if name:
        return str(name)
    name = getattr(quad.arg1, "name", None)
    return str(name or quad.arg1)


def is_tail_suffix(suffix: list[Quad], end_label: str) -> bool:
    allowed_pure = {"label", "addr", "load", "return"}
    for quad in suffix:
        if quad.op in allowed_pure:
            continue
        if quad.op == "goto" and quad.result == end_label:
            continue
        return False
    return any(quad.op == "return" for quad in suffix)


# ── 共享工具函数 ──────────────────────────────────────────────────────────────

def find_simple_loops(quads: list[Quad]) -> list[tuple[int, int]]:
    labels: dict[str, int] = {}
    loops: list[tuple[int, int]] = []
    for index, quad in enumerate(quads):
        if quad.op == "label" and isinstance(quad.result, str):
            labels[quad.result] = index
        elif quad.op == "goto" and isinstance(quad.result, str):
            start = labels.get(quad.result)
            if start is not None and start < index:
                loops.append((start, index))
    return loops


def extract_loop_invariants(body: list[Quad]) -> tuple[list[Quad], list[Quad]]:
    loop_defs = set()
    for quad in body:
        target = defined_temp(quad)
        if target is not None:
            loop_defs.add(target)
    addr_symbols = address_temp_symbols(body)
    modified_symbols = loop_modified_symbols(body, addr_symbols)
    has_unknown_side_effect = any(quad.op in {"call", "tail_call", "read"} for quad in body)
    invariant_temps = set()
    hoisted_indexes: set[int] = set()
    hoisted: list[Quad] = []

    changed = True
    while changed:
        changed = False
        for index, quad in enumerate(body):
            if index in hoisted_indexes or not can_hoist_quad(
                quad,
                loop_defs,
                invariant_temps,
                modified_symbols,
                addr_symbols,
                has_unknown_side_effect,
            ):
                continue
            target = defined_temp(quad)
            if target is None:
                continue
            hoisted_indexes.add(index)
            invariant_temps.add(target)
            hoisted.append(Quad(quad.op, quad.arg1, quad.arg2, quad.result, quad.type_info, quad.symbol, "循环不变式外提"))
            changed = True

    remaining = [quad for index, quad in enumerate(body) if index not in hoisted_indexes]
    return hoisted, remaining


def can_hoist_quad(
    quad: Quad,
    loop_defs: set[str],
    invariant_temps: set[str],
    modified_symbols: set[str],
    addr_symbols: dict[str, str],
    has_unknown_side_effect: bool,
) -> bool:
    if quad.op not in HOISTABLE_OPS or defined_temp(quad) is None:
        return False
    if quad.op == "load":
        symbol_name = addr_symbols.get(str(quad.arg1)) if is_temp(quad.arg1) else None
        if symbol_name is None or has_unknown_side_effect or symbol_name in modified_symbols:
            return False
    return all(operand_is_invariant(operand, loop_defs, invariant_temps) for operand in hoist_operands(quad))


def hoist_operands(quad: Quad) -> list[Operand]:
    if quad.op in {"+", "-", "*", "/", "index_addr"}:
        return [quad.arg1, quad.arg2]
    if quad.op in {"assign", "load", "field_addr"}:
        return [quad.arg1]
    return []


def operand_is_invariant(value: Operand, loop_defs: set[str], invariant_temps: set[str]) -> bool:
    if is_temp(value):
        name = str(value)
        return name not in loop_defs or name in invariant_temps
    return True


def loop_modified_symbols(body: list[Quad], addr_symbols: dict[str, str]) -> set[str]:
    modified: set[str] = set()
    for quad in body:
        if quad.op in {"store", "read"} and is_temp(quad.result):
            symbol_name = addr_symbols.get(str(quad.result))
            if symbol_name is not None:
                modified.add(symbol_name)
    return modified


def address_temp_symbols(body: list[Quad]) -> dict[str, str]:
    symbols: dict[str, str] = {}
    for quad in body:
        if quad.op == "addr" and is_temp(quad.result):
            name = getattr(quad.symbol, "name", None)
            if name:
                symbols[str(quad.result)] = str(name)
        elif quad.op in {"index_addr", "field_addr"} and is_temp(quad.arg1) and is_temp(quad.result):
            base = symbols.get(str(quad.arg1))
            if base is not None:
                symbols[str(quad.result)] = base
    return symbols


def starts_new_block(quad: Quad) -> bool:
    return quad.op == "label"


def ends_block(quad: Quad) -> bool:
    return quad.op in BLOCK_END_OPS or quad.op.startswith("if_false_")


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


def defined_temp(quad: Quad) -> str | None:
    if quad.op in REMOVABLE_TEMP_OPS and is_temp(quad.result):
        return str(quad.result)
    return None


def temp_uses(quad: Quad) -> set[str]:
    uses: set[str] = set()

    def add(value: Operand) -> None:
        if is_temp(value):
            uses.add(str(value))

    if quad.op in {"+", "-", "*", "/", "index_addr"} or quad.op.startswith("if_false_"):
        add(quad.arg1)
        add(quad.arg2)
    elif quad.op in {"assign", "load", "param", "write", "return"}:
        add(quad.arg1)
    elif quad.op == "field_addr":
        add(quad.arg1)
    elif quad.op in {"store", "read"}:
        add(quad.arg1)
        add(quad.result)
    elif quad.op == "tail_call" and isinstance(quad.arg1, list):
        for param in quad.arg1:
            if isinstance(param, Quad):
                add(param.arg1)
    return uses


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

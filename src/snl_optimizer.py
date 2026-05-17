#!/usr/bin/env python3
"""IR-level optimizations for SNL."""

# SNL 编译器的中间代码（IR）优化模块。
# 本模块对四元式（Quad）列表执行一系列经典编译优化 pass，
# 最终目标是在不改变程序语义的前提下减少指令数量、降低运行时开销。

from __future__ import annotations

try:
    from playground.snlcompiler.src.snl_ir import IRProcedure, IRProgram, IRUnit, Operand, Quad, flatten_procedures, is_temp
except ModuleNotFoundError:
    from snl_ir import IRProcedure, IRProgram, IRUnit, Operand, Quad, flatten_procedures, is_temp


# ── 常量与配置 ──────────────────────────────────────────────────────────────

# 纯表达式操作集合：这些操作没有副作用（不写内存、不调用外部），
# 其结果完全由操作数决定，因此可以安全地做 CSE（公共子表达式消除）和 LICM（循环不变式外提）。
PURE_EXPR_OPS = {"+", "-", "*", "/", "addr", "index_addr", "field_addr", "load"}

# 可删除的临时变量赋值操作集合：在死代码消除（DCE）中，
# 如果一条指令的结果（临时变量）从未被后续代码使用，且操作属于此集合，则可以安全删除。
# 在 PURE_EXPR_OPS 基础上加入 "assign"（纯赋值也可删除）。
REMOVABLE_TEMP_OPS = PURE_EXPR_OPS | {"assign"}

# 可外提到循环前的操作集合（供 LICM 使用）。
# 与 PURE_EXPR_OPS 相比多了 "assign"，因为纯赋值同样是循环不变的。
HOISTABLE_OPS = {"+", "-", "*", "/", "assign", "addr", "index_addr", "field_addr", "load"}

# 满足交换律的操作集合：a+b 与 b+a 等价，a*b 与 b*a 等价。
# CSE 在构造表达式键时会对这两类操作的操作数排序，使两种写法命中同一个缓存条目。
COMMUTATIVE_OPS = {"+", "*"}

# 基本块结束操作集合：遇到这些操作后，当前基本块结束，
# 跨块的值编号/别名信息必须清除（因为控制流可能从多个前驱汇入）。
BLOCK_END_OPS = {"goto", "return", "call", "tail_call", "read"}

# call 前参数准备阶段允许出现的操作集合（目前仅供扩展使用，未在主流程中直接引用）。
CALL_PARAM_SETUP_OPS = PURE_EXPR_OPS | {"assign", "param"}

# 所有可用 pass 的名称，按推荐执行顺序排列：
#   fold        — 常量折叠
#   algebra     — 代数化简
#   cse         — 公共表达式消除
#   dce         — 死代码消除
#   licm        — 循环不变式外提
#   tail_rec    — 尾递归消除
ALL_PASSES = ("fold", "algebra", "cse", "dce", "licm", "tail_rec")


# ── 顶层入口 ────────────────────────────────────────────────────────────────

def optimize_program(
    program: IRProgram,
    *,
    enabled_passes: set[str] | None = None,
) -> IRProgram:
    """对整个 IR 程序执行优化。

    参数
    ----
    program       : 待优化的 IR 程序，包含若干子过程（procedures）和主程序（main）。
    enabled_passes: 要启用的 pass 名称集合。为 None 时启用全部 pass。
                    合法名称见 ALL_PASSES。

    执行流程
    --------
    1. 用 flatten_procedures 展开所有子过程（含嵌套定义），逐一优化：
       a. 先跑通用优化 pass（optimize_unit）。
       b. 若启用了 tail_rec，执行尾递归消除，再跑一轮 optimize_unit
          清理消除后暴露的冗余代码。
    2. 对主程序（main）单独跑一轮 optimize_unit。
    3. 返回原地修改后的 program 对象。
    """
    passes = set(ALL_PASSES) if enabled_passes is None else enabled_passes
    for proc in flatten_procedures(program.procedures):
        # 第一轮：对子过程做常规优化
        optimize_unit(proc, passes)
        if "tail_rec" in passes:
            # 尾递归消除：将自身尾调用改写为 tail_call 四元式，
            # 后端可将其翻译为参数重写 + 跳转，避免新建栈帧。
            eliminate_tail_recursion(proc)
            # 消除后再跑一轮，清理新暴露的常量和死代码
            optimize_unit(proc, passes)
    # 主程序不含递归，只需一轮通用优化
    optimize_unit(program.main, passes)
    return program


def optimize_unit(unit: IRUnit, passes: set[str] | None = None) -> None:
    """对单个 IR 单元（过程或主程序）运行指定的优化 pass。

    Pass 执行顺序：
      第一轮：fold → algebra → cse → dce → licm
      第二轮：dce → fold → dce
    两轮设计的原因：LICM 将循环不变式外提后，可能暴露新的常量和死代码，
    第二轮负责清理这些残留，使优化效果收敛。
    """
    if passes is None:
        passes = set(ALL_PASSES)

    # ── 第一轮：主要化简 ──────────────────────────────────────────────────
    # 1. 常量折叠：将编译期可知的常量运算直接求值，替换为 assign 常量。
    #    例：t1 = 3 + 4  →  t1 = 7
    if "fold" in passes:
        unit.quads = fold_constants(unit.quads)

    # 2. 代数化简：利用代数恒等式消除冗余运算。
    #    例：t2 = x * 1  →  t2 = x；t3 = y + 0  →  t3 = y
    if "algebra" in passes:
        unit.quads = simplify_algebra(unit.quads)

    # 3. 公共表达式消除（CSE）：识别并消除重复的纯表达式。
    #    例：t4 = a + b；…；t5 = a + b  →  t5 = t4（复用已有结果）
    if "cse" in passes:
        unit.quads = common_subexpression_elimination(unit.quads)

    # 4. 死代码消除（DCE）：删除结果从未被使用的临时变量赋值指令。
    #    例：t8 = a * b（t8 之后从未读取）→ 整条指令删除
    if "dce" in passes:
        unit.quads = eliminate_dead_temp_assignments(unit.quads)

    # 5. 循环不变式外提（LICM）：将循环体内每次迭代结果相同的计算移到循环前。
    #    例：循环内 t9 = n * 4（n 在循环内不变）→ 移到循环入口前执行一次
    if "licm" in passes:
        unit.quads = hoist_loop_invariants(unit.quads)

    # ── 第二轮：清理 LICM 后暴露的冗余 ───────────────────────────────────
    # LICM 会移动四元式，可能产生新的复制链和死代码，
    # 再跑一遍 dce + fold + dce 将其彻底清除。
    if "dce" in passes:
        unit.quads = eliminate_dead_temp_assignments(unit.quads)
    if "fold" in passes:
        unit.quads = fold_constants(unit.quads)
    if "dce" in passes:
        unit.quads = eliminate_dead_temp_assignments(unit.quads)


# ── 基础 Pass ────────────────────────────────────────────────────────────────

def fold_constants(quads: list[Quad]) -> list[Quad]:
    """常量折叠（Constant Folding）。

    单遍从前向后扫描四元式列表，维护一张"已知常量表" constants：
      key   = 临时变量名（如 "t1"）
      value = 该变量当前持有的编译期整数值

    对每条四元式依次执行：
      1. 用 constants 替换操作数中的已知常量临时变量（replace_known_constants）。
      2. 若替换后两个操作数均为整数字面量，则直接求值（eval_arithmetic），
         将原算术指令改写为 assign 常量，并记录到 constants 表。
      3. 对条件跳转（if_false_< / if_false_=）：若两个操作数均为常量，
         则在编译期判断条件真假：
           - 条件为真（不跳转）→ 直接删除该指令（continue）
           - 条件为假（必然跳转）→ 改写为无条件 goto
      4. 维护 constants 表的一致性：
           - assign 整数常量 → 记录
           - assign 非常量   → 删除旧记录（变量值不再已知）
           - 其他定义目标    → 删除旧记录
           - store/read/call/tail_call → 清空全表（内存/IO 可能改变任何值）
    """
    # 已知常量表：临时变量名 → 整数值
    constants: dict[str, int] = {}
    optimized: list[Quad] = []

    for quad in quads:
        # 步骤 1：将操作数中已知为常量的临时变量替换为字面量
        quad = replace_known_constants(quad, constants)

        # 步骤 2：两个操作数均为整数 → 编译期求值，折叠为 assign 常量
        if quad.op in {"+", "-", "*", "/"} and isinstance(quad.arg1, int) and isinstance(quad.arg2, int):
            folded = eval_arithmetic(quad.op, quad.arg1, quad.arg2)
            if folded is not None and isinstance(quad.result, str):
                constants[quad.result] = folded
                optimized.append(Quad("assign", folded, None, quad.result, type_info=quad.type_info, note="常量折叠"))
                continue  # 原算术指令已被替换，跳过后续处理

        # 步骤 3：常量条件跳转折叠
        if quad.op in {"if_false_<", "if_false_="} and isinstance(quad.arg1, int) and isinstance(quad.arg2, int):
            # if_false_< label：若 arg1 < arg2 为真，则"if_false"不成立 → 不跳转 → 删除指令
            # if_false_= label：若 arg1 == arg2 为真，则"if_false"不成立 → 不跳转 → 删除指令
            condition_true = quad.arg1 < quad.arg2 if quad.op == "if_false_<" else quad.arg1 == quad.arg2
            if condition_true:
                continue  # 条件永真（不跳转），删除该条件跳转指令
            # 条件永假（必然跳转），改写为无条件 goto
            optimized.append(Quad("goto", None, None, quad.result, note="常量条件折叠"))
            continue

        # 步骤 4：维护 constants 表
        if quad.op == "assign" and isinstance(quad.result, str):
            if isinstance(quad.arg1, int):
                # 赋值来源是整数字面量，记录到常量表
                constants[quad.result] = quad.arg1
            else:
                # 赋值来源是变量，目标值不再是已知常量
                constants.pop(quad.result, None)
        elif isinstance(quad.result, str):
            # 其他操作定义了该临时变量，值不可预知，移除旧记录
            constants.pop(quad.result, None)

        # store/read/call/tail_call 可能通过内存或 IO 改变任意变量的值，
        # 必须清空整个常量表，防止后续错误地使用过期常量。
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
    """代数化简（Algebraic Simplification）。

    对每条算术四元式尝试应用代数恒等式，将其简化为更廉价的 assign 指令。
    具体规则见 simplify_arithmetic_quad。
    非算术指令或结果不是临时变量的指令直接保留，不做处理。
    """
    optimized: list[Quad] = []
    for quad in quads:
        # 只处理四则运算且结果为临时变量的指令
        if quad.op not in {"+", "-", "*", "/"} or not isinstance(quad.result, str):
            optimized.append(quad)
            continue

        # 尝试化简；若无法化简（返回 None）则保留原指令
        simplified = simplify_arithmetic_quad(quad)
        optimized.append(simplified if simplified is not None else quad)
    return optimized


def simplify_arithmetic_quad(quad: Quad) -> Quad | None:
    """对单条算术四元式应用代数恒等式，返回化简后的 assign 指令，或 None（无法化简）。

    支持的规则：
      加法：0 + x = x，x + 0 = x
      减法：x - 0 = x
      乘法：0 * x = 0，x * 0 = 0，1 * x = x，x * 1 = x
      除法：x / 1 = x，0 / x = 0（x ≠ 0）
    """
    result = quad.result
    if quad.op == "+":
        if quad.arg1 == 0:
            # 0 + x → assign x
            return Quad("assign", quad.arg2, None, result, quad.type_info, quad.symbol, "代数化简")
        if quad.arg2 == 0:
            # x + 0 → assign x
            return Quad("assign", quad.arg1, None, result, quad.type_info, quad.symbol, "代数化简")
    if quad.op == "-":
        if quad.arg2 == 0:
            # x - 0 → assign x
            return Quad("assign", quad.arg1, None, result, quad.type_info, quad.symbol, "代数化简")
    if quad.op == "*":
        if quad.arg1 == 0 or quad.arg2 == 0:
            # 任意数 * 0 = 0
            return Quad("assign", 0, None, result, quad.type_info, quad.symbol, "代数化简")
        if quad.arg1 == 1:
            # 1 * x → assign x
            return Quad("assign", quad.arg2, None, result, quad.type_info, quad.symbol, "代数化简")
        if quad.arg2 == 1:
            # x * 1 → assign x
            return Quad("assign", quad.arg1, None, result, quad.type_info, quad.symbol, "代数化简")
    if quad.op == "/":
        if quad.arg2 == 1:
            # x / 1 → assign x
            return Quad("assign", quad.arg1, None, result, quad.type_info, quad.symbol, "代数化简")
        if quad.arg1 == 0 and isinstance(quad.arg2, int) and quad.arg2 != 0:
            # 0 / x = 0（x 为非零常量）
            return Quad("assign", 0, None, result, quad.type_info, quad.symbol, "代数化简")
    return None  # 无匹配规则，不化简


def common_subexpression_elimination(quads: list[Quad]) -> list[Quad]:
    """基本块内公共表达式消除（Local Common Subexpression Elimination，CSE）。

    严格在单个基本块内进行 CSE：遇到任何基本块边界（label、goto、条件跳转、
    return、call 等）都会清空所有值编号信息，不跨块保留任何表达式缓存。

    值表键：(op, canonical(arg1), canonical(arg2), type_key) → 首次计算该值的临时变量

    数据结构
    --------
    aliases    : 临时变量 → 其规范操作数（用于追踪 assign 链，消除别名）
    expressions: 表达式键 → 首次计算该表达式的临时变量名（CSE 缓存）

    处理流程（每条四元式）
    ----------------------
    1. 遇到 label → 新基本块开始，清空 aliases 和 expressions。
    2. 用 aliases 规范化操作数（normalize_quad）。
    3. 若是纯表达式（PURE_EXPR_OPS）：
       - 计算表达式键（expression_key），查 expressions 表：
         · 命中 → 将结果临时变量记为已有值的别名，改写为 assign（CSE 复用）
         · 未命中 → 记录到 expressions 表，保留原指令
    4. 若是 assign → 更新 aliases 表（记录复制关系）。
    5. 遇到 store/read/call/tail_call → 清除所有 load 相关表达式（内存可能被修改）。
    6. 遇到块结束指令（goto/return/call/条件跳转等）→ 清空 aliases 和 expressions。
    """
    # aliases：记录临时变量的规范值（用于消除 assign 链中的中间变量）
    aliases: dict[str, Operand] = {}
    # expressions：记录已计算过的表达式，键 → 首次计算该表达式的临时变量名
    expressions: dict[tuple, str] = {}
    optimized: list[Quad] = []

    for quad in quads:
        # 遇到 label：新基本块开始，清空所有值编号信息
        if quad.op == "label":
            aliases.clear()
            expressions.clear()

        # 用 aliases 将操作数替换为规范形式，消除不必要的临时变量引用
        normalized = normalize_quad(quad, aliases)

        if normalized.op in PURE_EXPR_OPS and isinstance(normalized.result, str):
            # 计算该表达式的规范键（交换律操作会对操作数排序）
            key = expression_key(normalized, aliases)
            if key in expressions:
                # 命中：该表达式之前已计算过，直接复用，改写为 assign
                aliases[normalized.result] = expressions[key]
                optimized.append(
                    Quad(
                        "assign",
                        expressions[key],
                        None,
                        normalized.result,
                        type_info=normalized.type_info,
                        note="CSE",
                    )
                )
                continue  # 跳过原指令，已用 assign 替换
            # 未命中：首次计算，记录到表达式缓存
            expressions[key] = normalized.result
        elif normalized.op == "assign" and isinstance(normalized.result, str):
            # 记录赋值关系：result 的规范值 = arg1 的规范值
            aliases[normalized.result] = canonical_operand(normalized.arg1, aliases)

        optimized.append(normalized)

        # store/read/call/tail_call 可能改变内存，清除 load 相关表达式
        if normalized.op in {"store", "read", "call", "tail_call"}:
            expressions = {k: v for k, v in expressions.items() if k[0] != "load"}

        # 块结束指令后，当前基本块结束，清空所有状态
        if ends_block(normalized):
            aliases.clear()
            expressions.clear()

    return optimized

def eliminate_dead_temp_assignments(quads: list[Quad]) -> list[Quad]:
    """死代码消除（Dead Code Elimination，DCE）。

    采用逆向活跃变量分析：从最后一条指令向前扫描，
    维护一个"活跃临时变量"集合 live，记录当前位置之后还会被使用的临时变量。

    对每条指令：
      1. 若该指令定义了一个临时变量（target），且：
           - target 不在 live 中（后续无人使用）
           - 操作属于 REMOVABLE_TEMP_OPS（无副作用，删除安全）
         则跳过该指令（删除）。
      2. 否则保留该指令，并：
           - 从 live 中移除 target（该定义点之前，target 尚未产生）
           - 将该指令使用的所有临时变量加入 live（它们在此处被消费）

    最后将 kept 列表反转，恢复原始顺序。

    注意：只删除临时变量（is_temp）的赋值，用户变量的赋值不在此处理，
    以保证程序可观测行为不变。
    """
    live: set[str] = set()   # 当前位置之后仍然活跃（会被使用）的临时变量集合
    kept: list[Quad] = []    # 保留下来的指令（逆序收集，最后反转）

    for quad in reversed(quads):
        target = defined_temp(quad)  # 该指令定义的临时变量（若有）

        # 若定义的临时变量在后续从未使用，且操作无副作用 → 删除
        if target is not None and target not in live and quad.op in REMOVABLE_TEMP_OPS:
            continue  # 死代码，跳过

        # 保留该指令：更新活跃集合
        if target is not None:
            live.discard(target)   # 该变量在此处被定义，定义点之前它尚不存在
        live.update(temp_uses(quad))  # 该指令使用的临时变量在此处之前必须活跃
        kept.append(quad)

    # 逆序收集后反转，恢复正向顺序
    kept.reverse()
    return kept


def starts_new_block(quad: Quad) -> bool:
    """判断该四元式是否标志着一个新基本块的开始（即 label 指令）。"""
    return quad.op == "label"


def ends_block(quad: Quad) -> bool:
    """判断该四元式是否标志着当前基本块的结束。

    块结束条件：
      - 无条件跳转（goto）、过程返回（return）、函数调用（call/tail_call）、读操作（read）
      - 条件跳转（if_false_* 系列）
    块结束后，控制流可能转向多个目标，跨块的值编号/别名信息必须清除。
    """
    return quad.op in BLOCK_END_OPS or quad.op.startswith("if_false_")


# ── 循环 Pass（licm）────────────────────────────────────────────────────────

def hoist_loop_invariants(quads: list[Quad]) -> list[Quad]:
    """循环不变式外提（Loop-Invariant Code Motion，LICM）。

    对每个识别出的简单循环，将循环体内"不变式"四元式移到循环入口之前，
    使其只执行一次而非每次迭代都执行。

    处理顺序：逆序处理循环（reversed），确保嵌套循环从内层向外层依次外提，
    内层外提完成后，外层再判断是否可以进一步外提。

    参数 start：循环入口 label 的索引
    参数 end  ：循环回跳 goto 的索引

    外提后的四元式结构：
      [循环前代码] [hoisted 不变式] [label（循环入口）] [循环体剩余] [goto]
    """
    optimized = list(quads)
    loops = find_simple_loops(optimized)
    for start, end in reversed(loops):
        # 提取循环体（label 之后、goto 之前的指令）中的不变式
        hoisted, body = extract_loop_invariants(optimized[start + 1 : end])
        if hoisted:
            # 将不变式插入到循环入口 label 之前，循环体替换为剩余指令
            optimized = optimized[:start] + hoisted + [optimized[start]] + body + optimized[end:]
    return optimized


def normalize_quad(quad: Quad, aliases: dict[str, Operand]) -> Quad:
    """将四元式的 arg1 和 arg2 替换为其规范操作数（用于 CSE）。

    规范操作数：沿 aliases 链追踪到最终的非别名值，
    使得后续的表达式键计算能识别出等价表达式。
    result 字段不替换（它是定义点，不是使用点）。
    """
    arg1 = canonical_operand(quad.arg1, aliases)
    arg2 = canonical_operand(quad.arg2, aliases)
    return Quad(quad.op, arg1, arg2, quad.result, quad.type_info, quad.symbol, quad.note)


def expression_key(quad: Quad, aliases: dict[str, Operand]) -> tuple:
    """为四元式生成规范化的 CSE 查找键。

    对交换律操作（+、*）将两个操作数排序，使 a+b 和 b+a 映射到同一个键。
    排序基于 operand_key 返回的 tuple，而非 repr() 字符串——
    repr() 的字典序对 "t9" vs "t10" 这类名称会给出错误顺序。

    addr 指令的键只包含符号名（不含操作数），因为同一变量的地址唯一。
    其他指令的键包含：(op, arg1_key, arg2_key, type_key)。
    """
    arg1 = operand_key(quad.arg1, aliases)
    arg2 = operand_key(quad.arg2, aliases)
    # 交换律操作：对操作数排序，使 a op b 和 b op a 命中同一缓存条目
    if quad.op in COMMUTATIVE_OPS and arg2 < arg1:
        arg1, arg2 = arg2, arg1
    if quad.op == "addr":
        # addr 的结果只取决于符号，与操作数无关
        return (quad.op, getattr(quad.symbol, "name", None))
    return (quad.op, arg1, arg2, type_key(quad.type_info))


def canonical_operand(value: Operand, aliases: dict[str, Operand]) -> Operand:
    """沿 aliases 链追踪操作数，返回其最终规范值。

    例：aliases = {"t1": "t0", "t2": "t1"}
      canonical_operand("t2", aliases) → "t0"

    使用 seen 集合防止循环别名（理论上不应出现，但作为安全保障）。
    """
    seen: set[str] = set()
    while is_temp(value) and value in aliases and value not in seen:
        seen.add(value)
        value = aliases[value]
    return value


def operand_key(value: Operand, aliases: dict[str, Operand]) -> tuple:
    """将操作数转换为可比较的元组键，用于 expression_key 中的排序和比较。

    返回格式：
      整数常量  → ("const", 整数值)
      临时变量  → ("temp", 变量名)（已规范化）

    CSE 只处理 PURE_EXPR_OPS 中的纯表达式，其操作数只会是 int 或 str（临时变量名）。
    使用元组而非裸值，确保 int 和 str 之间可以安全比较（用于交换律排序）。
    """
    value = canonical_operand(value, aliases)
    if isinstance(value, int):
        return ("const", value)
    return ("temp", value)


def type_key(type_info: object) -> str:
    """将类型信息转换为字符串键，用于 expression_key 中区分不同类型的同名表达式。

    优先使用 type_info.display() 方法（若存在），否则使用 repr()。
    类型不同的表达式（如 integer 加法 vs array 加法）不应被视为同一表达式。
    """
    display = getattr(type_info, "display", None)
    return display() if callable(display) else repr(type_info)


def replace_known_constants(quad: Quad, constants: dict[str, int]) -> Quad:
    """将四元式操作数中已知为常量的临时变量替换为整数字面量。

    只替换 arg1 和 arg2（使用点），不替换 result（定义点）。
    若两个操作数均无需替换，直接返回原 quad 对象（避免不必要的对象创建）。
    """
    arg1 = constants.get(str(quad.arg1), quad.arg1) if is_temp(quad.arg1) else quad.arg1
    arg2 = constants.get(str(quad.arg2), quad.arg2) if is_temp(quad.arg2) else quad.arg2
    if arg1 is quad.arg1 and arg2 is quad.arg2:
        return quad  # 无需替换，返回原对象
    return Quad(quad.op, arg1, arg2, quad.result, quad.type_info, quad.symbol, quad.note)


def defined_temp(quad: Quad) -> str | None:
    """返回该四元式定义（写入）的临时变量名，若无则返回 None。

    以下操作不定义临时变量：
      - store、goto、return、param、write、writeln：无结果或结果是内存地址
      - if_false_* 系列：条件跳转，result 是跳转目标标签而非临时变量

    其他操作若 result 是临时变量（is_temp），则返回该变量名。
    """
    if quad.op in {"store", "goto", "return", "param", "write", "writeln", "read"}:
        return None
    if quad.op.startswith("if_false_"):
        return None
    if isinstance(quad.result, str) and is_temp(quad.result):
        return quad.result
    return None


def temp_uses(quad: Quad) -> set[str]:
    """返回该四元式使用（读取）的所有临时变量名集合。

    通常情况下，arg1 和 arg2 是使用点。
    但对于以下操作，result 也是使用点（而非定义点）：
      - store：result 是目标地址（读取地址，写入内存）
      - param：result 是传递的参数值
      - write/writeln：result 是要输出的值
      - return：result 是返回值
      - if_false_*：result 是跳转目标标签（字符串，通常不是临时变量，但统一处理）
    """
    uses: set[str] = set()
    if is_temp(quad.arg1):
        uses.add(str(quad.arg1))
    if is_temp(quad.arg2):
        uses.add(str(quad.arg2))
    # 这些操作的 result 是"使用"而非"定义"
    if quad.op in {"store", "param", "write", "writeln", "return", "read"} and is_temp(quad.result):
        uses.add(str(quad.result))
    if quad.op.startswith("if_false_") and is_temp(quad.result):
        uses.add(str(quad.result))
    return uses


# ── 循环工具 ─────────────────────────────────────────────────────────────────

def find_simple_loops(quads: list[Quad]) -> list[tuple[int, int]]:
    """找出所有简单循环：(label_index, goto_index)。

    简单循环定义为：一个 label 后面某处有一个 goto 跳回该 label。
    即满足：quads[label_index].op == "label" 且
            quads[goto_index].op == "goto" 且
            goto 的目标 == label 的名称 且
            label_index < goto_index（向前跳转）

    返回的列表按 label 出现顺序排列，调用方通常需要逆序处理（从内层到外层）。
    """
    # 第一遍：记录所有 label 的位置（label 名 → 索引）
    label_indices: dict[str, int] = {}
    for idx, quad in enumerate(quads):
        if quad.op == "label" and isinstance(quad.result, str):
            label_indices[quad.result] = idx

    # 第二遍：找出所有向前跳转的 goto（即回跳，构成循环）
    loops: list[tuple[int, int]] = []
    for idx, quad in enumerate(quads):
        if quad.op == "goto" and isinstance(quad.result, str):
            target = quad.result
            # 目标 label 存在且在当前 goto 之前 → 这是一个循环回跳
            if target in label_indices and label_indices[target] < idx:
                loops.append((label_indices[target], idx))
    return loops


def extract_loop_invariants(body: list[Quad]) -> tuple[list[Quad], list[Quad]]:
    """从循环体中提取可外提的不变式四元式。

    算法
    ----
    1. 收集循环体内所有被定义（写入）的变量名，存入 defined_in_loop。
       这些变量在循环内可能每次迭代都会改变，不能视为不变量。
    2. 遍历循环体，对每条指令调用 _can_hoist 判断是否可外提：
       - 可外提：加入 hoisted 列表，并从 defined_in_loop 中移除其结果
         （移除后，依赖该结果的后续指令可能也变得可外提，但本函数只做单遍扫描）
       - 不可外提：加入 remaining 列表

    返回 (hoisted, remaining) 两个列表。
    """
    # 收集循环体内所有被定义的变量（排除控制流指令的"结果"，如 goto 的目标标签）
    defined_in_loop: set[str] = set()
    for quad in body:
        if isinstance(quad.result, str) and quad.op not in {"goto", "label"}:
            if not quad.op.startswith("if_false_"):
                defined_in_loop.add(quad.result)

    hoisted: list[Quad] = []   # 可外提到循环前的不变式指令
    remaining: list[Quad] = [] # 必须留在循环体内的指令

    for quad in body:
        if _can_hoist(quad, defined_in_loop):
            # 外提：标注 note，加入 hoisted 列表
            hoisted.append(Quad(quad.op, quad.arg1, quad.arg2, quad.result,
                                quad.type_info, quad.symbol, "循环外提"))
            if isinstance(quad.result, str):
                # 该变量已外提，不再是"循环内定义"的变量，
                # 后续依赖它的指令可能因此也满足外提条件
                defined_in_loop.discard(quad.result)
        else:
            remaining.append(quad)

    return hoisted, remaining


def _can_hoist(quad: Quad, defined_in_loop: set[str]) -> bool:
    """判断一条四元式是否可以外提到循环前。

    外提条件（同时满足）：
      1. 操作属于 PURE_EXPR_OPS 且不是 load（load 读取的内存内容可能在循环内被 store 修改）
      2. result 是字符串（临时变量或用户变量），而非 None
      3. 两个操作数均为"循环不变"：
           - None 或整数字面量：显然不变
           - 字符串变量：不在 defined_in_loop 中（循环内不会被修改）
           - 其他类型（符号对象等）：视为不变
    """
    if quad.op not in PURE_EXPR_OPS:
        return False  # 有副作用的操作不能外提
    if quad.op == "load":
        return False  # load 读取内存，内存内容可能在循环内被 store 修改，保守不外提
    if not isinstance(quad.result, str):
        return False  # 结果不是变量，无意义

    def operand_invariant(op: Operand) -> bool:
        """判断单个操作数是否循环不变。"""
        if op is None or isinstance(op, int):
            return True  # 常量，显然不变
        if isinstance(op, str):
            return op not in defined_in_loop  # 变量：不在循环内定义则不变
        return True  # 符号对象等，视为不变

    return operand_invariant(quad.arg1) and operand_invariant(quad.arg2)


# ── 尾递归消除 ───────────────────────────────────────────────────────────────

def eliminate_tail_calls(quads: list[Quad], proc_name: str) -> list[Quad]:
    """将过程对自身的尾调用转换为 tail_call 四元式。

    识别模式：
      1. call proc_name → return（中间只有 label）
      2. call proc_name → 过程结尾（中间只有 label，无其他有效指令）
    替换为：tail_call proc_name（codegen 将其翻译为参数重写 + 跳转到入口）

    为什么要做尾递归消除？
    ----------------------
    普通递归调用每次都会新建一个栈帧，深度递归会导致栈溢出。
    尾递归（调用后立即 return，不再使用当前栈帧的任何数据）可以安全地
    复用当前栈帧：将参数原地更新，然后跳转回过程入口，等价于循环。

    算法
    ----
    线性扫描四元式列表，遇到 call proc_name 时：
      1. 向后跳过所有 label 指令（label 不影响控制流语义）。
      2. 检查紧随其后的指令：
         - 是 return → 尾位置，标记为 is_tail，跳过 return
         - 已到列表末尾 → 也是尾位置（过程自然结束）
      3. 若是尾位置，将 call 改写为 tail_call，保留中间的 label，
         跳过原来的 return（若有）。
      4. 否则保留原 call 指令。
    """
    optimized: list[Quad] = []
    i = 0
    while i < len(quads):
        # 检查是否是对自身的调用（通过 symbol.name 或 arg1 匹配过程名）
        if quads[i].op == "call" and getattr(quads[i].symbol, "name", quads[i].arg1) == proc_name:
            # 向后扫描，跳过 call 之后的所有 label 指令
            j = i + 1
            labels_between: list[Quad] = []
            while j < len(quads) and quads[j].op == "label":
                labels_between.append(quads[j])
                j += 1

            is_tail = False
            if j < len(quads) and quads[j].op == "return":
                # 模式 1：call → (labels) → return，是尾调用
                is_tail = True
                j += 1  # 跳过 return，后续不再输出它
            elif j >= len(quads):
                # 模式 2：call → (labels) → 过程结束，也是尾调用
                is_tail = True

            if is_tail:
                # 将 call 改写为 tail_call，保留中间的 label
                optimized.append(Quad(
                    "tail_call", quads[i].arg1, quads[i].arg2, quads[i].result,
                    quads[i].type_info, quads[i].symbol, "尾递归消除"
                ))
                optimized.extend(labels_between)  # 保留中间的 label（可能是跳转目标）
                i = j  # 跳过已处理的指令（包括原 return）
                continue

        # 非尾调用或非自身调用，保留原指令
        optimized.append(quads[i])
        i += 1
    return optimized


def eliminate_tail_recursion(proc: IRProcedure) -> None:
    """对过程执行尾递归消除（原地修改 proc.quads）。

    从 proc 对象获取过程名，调用 eliminate_tail_calls 处理其四元式列表，
    并将结果写回 proc.quads。
    若过程名无法获取（匿名过程），则跳过不处理。
    """
    name = getattr(proc, "name", None)
    if name is None:
        return  # 匿名过程，无法匹配自身调用，跳过
    proc.quads = eliminate_tail_calls(proc.quads, name)

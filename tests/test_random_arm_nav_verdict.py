r"""`scripts/measure_random_arm_nav.py` 的守卫。

## 这个文件在守什么

Y5 问的是**相对位置**（账户年化在 100 次随机抽样的第几分位），
而 §18.1 已判 `|c|/M = 0.4656 < 1` ⇒ 绝对水平连符号都定不了。
⇒ 本守卫只守**相对位置**这一条判决链，一个字都不碰绝对水平。

## §19.3 预注册的三条守卫要求（实现时落地）

| # | 要求 | 本文件 |
|---|---|---|
| ① | **真值表逐格穷举**（四个分支各至少一个构造出来的输入） | `_TRUTH_TABLE` |
| ② | **四条全部可达**（尤其 `E-下`，§19.1 那条缺陷的专属守卫） | `test_every_branch_is_reachable` |
| ③ | **换序变异** | `tests/mutation_specs/test_random_arm_nav.mut` 的 Y1 |

## 🚨 判别力全部来自**构造出来的分岔夹具**

第 146 层实测：靠随机数据巧合成立的守卫，38 条全绿而变异存活。
⇒ 本文件**没有一条**断言依赖「真实面板上那个数恰好落在某处」。

## 🚨 两处「顺序是判据」的地方都必须被钉住

① `_BRANCH_ORDER` —— §19.3 的四级优先级；
② `decide_branch` **只读 `conds` 一个条件都不重算** ——
   若它偷偷重算，把 `conds` 改成相反的值仍会给出同一个分支，
   而产物里的 `branch_conditions` 与 `branch` 就会互相打脸。

## 🚨 不用子串的四处

`_BRANCH_ORDER` / `make_shuffle_score` / `make_shuffle_list` /
`DAYS_PER_YEAR` 四串在本文件 docstring 里都出现过（第 13 种形状：
子串断言 + 文档 = 守卫可被文档满足）⇒ 一律走 **AST**，锚在**实参与
赋值右端**。
"""
from __future__ import annotations

import ast
import copy
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "scripts" / "measure_random_arm_nav.py"

# 与被测脚本同一套导入路径（照抄脚本自己的头部，否则守卫自带
# 仓库根而脚本那份 sys.path.insert 失效 ⇒ 第 24 种形状）
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("mrnav_under_test", SRC)
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)


# ─────────────────────────────────────────────────────────────
# 合成夹具：全部手打，不读任何真实产物、不碰 GB 级面板
# ─────────────────────────────────────────────────────────────

def _func_src(src: str, name: str) -> str:
    """æ**åå­**ä» AST åå½æ°æºç ã

    🚨🚨 **`ast.get_source_segment(src, <å½æ°å¯¹è±¡>)` ä¸å¾è¿å `None`**
    ââ é£ä¸ª API è¦çæ¯ **AST èç¹**ï¼å¸¦ `lineno`/`col_offset`ï¼ï¼
    èå½æ°**å¯¹è±¡**èº«ä¸æ²¡æ ⇒ `or ""` ä¹å `"x" not in ""` **æç**ã

    æ¬æ¡æ¯ç¬¬ 23 å±çç¬¬åä¸ªå®ä¾ï¼èä¸åå®³èæ¯**æèªå·±ä¸ä¸æ®µåçå®å«**ï¼
    `test_the_strategy_own_mde_is_recorded_but_never_enters_the_branch`
    çååæ­£æ§ä¸ç´æ¯æç»¿ç ⇒ ãå®å«å·²éªãè¿å¥è¯å¯¹å®ä¸ç´æ¯åçã
    ⇒ **æç»¿ä¸ä¼èªå·±æ´é²**ï¼åå¼å°ä¹ä¸åï¼å¿é¡»æä¸æ¡ãåæ³æ¬èº«éªè¿äºãçå®å«ã
    """
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            seg = ast.get_source_segment(src, node)
            assert seg is not None, f"AST éæ {name} å´åä¸å°æºç æ®µï¼åæ³æ¬èº«åäºï¼"
            return seg
    raise AssertionError(f"æºç éæ²¡æå½æ° {name} ââ å®å«éçå½æ°è¢«æ¹åæå é¤äº")


def test_the_source_extraction_helper_actually_extracts() -> None:
    """🚨 **åå®å«**ï¼ä¸é¢é£ä¸ªåæ³æ¬èº«å¿é¡»åè¯æå®åå°äºä¸è¥¿ã

    æ²¡æè¿ä¸æ¡ï¼æ `_func_src` æ¹å `get_source_segment(..., å½æ°å¯¹è±¡)`
    ä¼è®©**æ¯ä¸æ¡**ç¨å®çå°æ¹éé»éåææçï¼èå°å­ä¸æ¡é½ä¸ä¼å
    ââ è¿æ­£æ¯ååå¨ `own_mde` ååæ­£æ§ä¸çå®åççäºã
    """
    src = SRC.read_text(encoding="utf-8")
    got = _func_src(src, "same_date_axis")
    assert "def same_date_axis" in got, "_func_src æ²¡åå°å½æ°å®ä¹æ¬èº«"
    assert len(got) > 100, f"_func_src åªåå° {len(got)} å­ç¬¦ ⇒ åçä¸æ¯å®æ´å½æ°ä½"
    # ååï¼æ¥ä¸ä¸ªä¸å­å¨çå½æ°å¿é¡»åï¼ä¸è®¸éé»è¿å ""ï¼
    try:
        _func_src(src, "definitely_not_a_real_function")
    except AssertionError:
        pass
    else:
        raise AssertionError("_func_src å¯¹ä¸å­å¨çå½æ°éé»è¿åäº ⇒ æçå®å«çéæ¹")


def _func_code(src: str, name: str) -> str:
    """取函数的**可执行语句**，🚨 **刻意不含 docstring**。

    为什么必须剥：`_func_src` 返回整段（含 docstring），而
    `same_date_axis` 的 docstring 里**逐字讨论了 4 处 `market`** ——
    那正是它在解释「为什么**不**读 market」。
    ⇒ 拿 `_func_src` 的结果做 `"market" not in src` 会被**文档本身**判红
      （第 13 层：子串能告诉你「有什么」，不能告诉你「在哪用」，
      而 CLAUDE.md 明写「写完守卫通读一遍，确认每个断言串都不在自己写的注释里」）。
    ⇒ 本辅助把判据落到**可执行语句**上 —— 那才是「读了 market」真正发生的地方。

    与 `_func_src` 一样按**名字**取（第 23 层：函数对象上没有 `lineno`）。
    """
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            body = list(node.body)
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                body = body[1:]              # ← 剥掉 docstring
            clone = copy.copy(node)          # 浅拷贝：args/decorators 原样保留
            clone.body = body
            return ast.unparse(clone)
    raise AssertionError(f"源码里没有函数 {name} —— 守卫钉的函数被改名或删除了")


def test_the_random_arm_uses_the_domain_preserving_shuffle() -> None:
    """随机臂必须用**保域**的 `make_shuffle_score`，不许换成 `make_shuffle_list`。

    🚨 Y5 变异**曾经存活**，因为没有任何一条守卫钉住这个选择 ——
    而它是 §10 抽样域的承重前提：`make_shuffle_list` 给每行赋有限值
    ⇒ `notna` 剔不掉 ⇒ 域 ⊇ 策略域
    ⇒ 随机臂与策略臂**不在同一个域上比**（§10.8：不可比的对照臂比没有更坏）。

    🚨 第一版那条变异体写的是 `import make_shuffle_score as make_shuffle_list`
    —— 那只**换了个名字**，函数没换 ⇒ 行为逐位等价 ⇒ 存活是**必然**的
    （第 172 层：等价变异体是「存活」的第四种解释，与「守卫有洞」证据一模一样）。
    ⇒ 换成真换函数后，只有下面 ① 能杀掉它 ⇒ ① 这条判据是**这次新增**的。

    判据钉**两处接线**（定义点 + 用点），缺一不可（第 189 层：
    变异必须打在「用点」上，不许打在定义点上）：
    ① `from placebo_control import …` 的 alias 名单；
    ② `_run()` 里实际调用的名字。
    """
    src = SRC.read_text(encoding="utf-8")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.ImportFrom) and node.module == "placebo_control":
            imported |= {a.name for a in node.names}
    assert "make_shuffle_score" in imported, (
        f"placebo_control 的 import 名单里没有 make_shuffle_score，"
        f"实际是 {sorted(imported)}")
    assert "make_shuffle_list" not in imported, (
        "随机臂换成了 make_shuffle_list —— 它不保域 ⇒ 域 ⊇ 策略域 ⇒ 不可比的对照臂")

    run = _func_src(src, "_run")
    # ⚠️ 断言串**带左括号**：caliber 正文里逐字出现过不带括号的
    #   「不许用 `make_shuffle_list`」，带括号才不会撞上那句说明（第 13 层）。
    assert "make_shuffle_score(" in run, (
        "_run() 没有调用 make_shuffle_score ⇒ 随机臂根本没被抽到，"
        "或提取出来的东西是死代码（第 119 层）")
    assert "make_shuffle_list(" not in run, (
        "_run() 调用了 make_shuffle_list ⇒ 同上：不保域的抽样")


def test_the_docstring_stripping_helper_actually_strips() -> None:
    """🚨 **元守卫**：`_func_code` 必须先证明它真的剥掉了 docstring。

    没有这一条，把 `body = body[1:]` 那行删掉会让下面那条
    「不许出现 market」的守卫**从红变绿**（docstring 剥不剥决定它判什么），
    而台子一条都不会喊 —— 这与刚刚在 `own_mde` 反向正控上发生的事完全同型
    （那次是取法恒真，这次是剥除失效；方向相反，取源码的姿势一样）。
    """
    src = SRC.read_text(encoding="utf-8")
    full = _func_src(src, "same_date_axis")
    code = _func_code(src, "same_date_axis")
    assert "market" in full, (
        "正控失效：docstring 里已经没有 market 了 ⇒ 这条元守卫钉不住剥除行为")
    assert "market" not in code, (
        "_func_code 没有剥掉 docstring ⇒「不许出现 market」那条在扫文档"
        "（第 13 层：文档满足守卫）")
    assert "def same_date_axis" in code, "_func_code 连函数头都没保住"
    assert code.strip(), "_func_code 剥完是空的 ⇒ 它什么都没取到"


def _rec(s: float, p5: float, p95: float, res: float) -> dict:
    """构造一条判决记录。四个字段正是 `branch_conditions` 读的全部字段。"""
    return {"strategy_cagr": s, "p5": p5, "p95": p95, "mde_resolution": res}


def _branch_of(rec: dict) -> str:
    return M.decide_branch(rec, M.branch_conditions(rec))


# (标签, rec, 期望分支)
# 🚨 判别力来源说明（这四条**互不相同**，不是同一份夹具的四种说法）：
#   A 带外高位  → 只有 `s > p95` 真      ⇒ 证明 `E-上` 靠的是**上**尾不是下尾
#   B 带外低位  → 只有 `s < p5`  真      ⇒ §19.1 那条缺陷的专属反证
#   C 带内      → 三条位置条件全假      ⇒ 证明 `E-内` 是**兜底**不是「等于某值」
#   D 带宽不足  → `E-判不出` ∧ `E-下` **同真** ⇒ 顺序是唯一分界线
_TRUTH_TABLE = [
    ("A 带外高位", _rec(s=0.30, p5=0.01, p95=0.20, res=0.01), "E-上"),
    ("B 带外低位", _rec(s=-0.05, p5=0.01, p95=0.20, res=0.01), "E-下"),
    ("C 带内", _rec(s=0.10, p5=0.01, p95=0.20, res=0.01), "E-内"),
    ("D 带宽不足（E-判不出 ∧ E-下 同真）",
     _rec(s=-0.05, p5=0.01, p95=0.0011, res=0.05), "E-判不出"),
]


@pytest.mark.parametrize("label,rec,expect", _TRUTH_TABLE,
                         ids=[c[0] for c in _TRUTH_TABLE])
def test_the_truth_table_cell_lands_on_its_branch(label, rec, expect) -> None:
    """§19.3 守卫要求 ①：真值表逐格。**逐格**——不许 `all()` 把四格压成一句。"""
    assert _branch_of(rec) == expect, (
        f"{label}：期望 {expect}，实测 {_branch_of(rec)}"
        f"（conds={M.branch_conditions(rec)}）")


@pytest.mark.parametrize("label,rec,expect", _TRUTH_TABLE,
                         ids=[c[0] for c in _TRUTH_TABLE])
def test_the_truth_table_cells_that_hide_a_branch_say_so(label, rec,
                                                        expect) -> None:
    """🚨 被顺序遮住的分支必须**落盘**，不许 `if/elif` 一样都不留
    （§19.3：四条件全求值 ⇒ 被遮住的记进产物）。"""
    conds = M.branch_conditions(rec)
    hidden = [k for k, v in conds.items() if v and k != expect]
    assert all(conds[k] for k in hidden), "被遮住的分支条件本身必须为真"
    # 反向：没被遮住的条件，一条都不许出现在 hidden 里
    assert len(hidden) + 1 == sum(1 for v in conds.values() if v), (
        f"{label}：hidden={hidden} 与 conds={conds} 不自洽")


def test_every_branch_is_reachable() -> None:
    """§19.3 守卫要求 ②：**四条全部可达**。

    🚨 这条专门守着 §19.1 那条缺陷——原判据 `p95 − p5 < |s − p95|` 让
    `E-下` **永不可达**（代数证明：`s < p5` 时 `|s−p95| = p95−s > p95−p5` 恒真）。
    """
    got = {_branch_of(rec) for _, rec, _ in _TRUTH_TABLE}
    assert got == set(M._BRANCH_ORDER), (
        f"可达分支 {sorted(got)} ≠ 声明的 {sorted(M._BRANCH_ORDER)}"
        f" ⇒ 有分支永不可达，否证出口已被吃掉")


def test_the_fourth_cell_proves_that_order_is_the_only_tiebreaker() -> None:
    """🚨 第 D 格必须真的**同时**命中两条 ⇒ 顺序是承重件，不是洁癖。

    若这条红了，说明真值表退化成了嵌套链（每次只有一条为真），
    那 `_BRANCH_ORDER` 就不承载判据 —— 而它**看起来**一模一样。
    """
    rec = _TRUTH_TABLE[3][1]
    conds = M.branch_conditions(rec)
    assert conds["E-判不出"] and conds["E-下"], (
        f"第 D 格本该是两条同真，实测 {conds} ⇒ 真值表不再是「顺序是判据」的证据")
    assert _branch_of(rec) == "E-判不出", "顺序常量被改了（见变异 Y1）"


# ─────────────────────────────────────────────────────────────
# `decide_branch` 只读字段：一个条件都不许重算
# ─────────────────────────────────────────────────────────────

def test_decide_branch_reads_conditions_and_recomputes_nothing(monkeypatch) -> None:
    """🚨 §10.8 落盘契约：**判 `branch` 的代码只许读字段不许重算**。

    造一个 `conds` 与 `rec` **互相矛盾**的输入：
    若 `decide_branch` 重算，它会给 `rec` 的答案；
    只读 `conds` 则给 `conds` 的答案。二者不同 ⇒ 可判别。
    """
    rec = _rec(s=0.30, p5=0.01, p95=0.20, res=0.01)   # rec 自己会判 E-上
    lying = {"E-判不出": False, "E-上": False, "E-下": False, "E-内": True}
    assert M.decide_branch(rec, lying) == "E-内", (
        "decide_branch 重算了条件 ⇒ 产物里 branch_conditions 与 branch 会互相打脸")


def test_decide_branch_raises_when_every_condition_is_false() -> None:
    """四条全假必须**响**，不许静默返回一个分支
    （`if/elif` 链的兜底返回会把「顺序表坏了」说成「E-内」）。"""
    none_of_them = {k: False for k in M._BRANCH_ORDER}
    with pytest.raises(RuntimeError):
        M.decide_branch(_rec(0.0, 0.0, 0.0, 0.0), none_of_them)


def test_branch_conditions_evaluates_all_four_never_short_circuits() -> None:
    """四条**全求值** —— `E-判不出` 为真时后面三条仍须被算出来落盘。"""
    rec = _rec(s=-0.05, p5=0.01, p95=0.0011, res=0.05)   # 第 D 格
    conds = M.branch_conditions(rec)
    assert set(conds) == set(M._BRANCH_ORDER), "条件表必须含全部四条"
    assert conds["E-下"] is True, "被遮住的那条也必须算出来"


# ─────────────────────────────────────────────────────────────
# 外部锚：`mde_resolution` 量的**不是**「策略值离上尾多远」
# ─────────────────────────────────────────────────────────────

def test_mde_resolution_is_the_bootstrap_halfwidth_not_the_distance() -> None:
    """§19.2：`mde_resolution = p95 的 bootstrap CI95 半宽`。

    🚨 判别力来自**两个量的量级不同**：手给的 `boots` 宽度 0.02，
    而同一批随机臂里策略值离 p95 有多远是另一回事。
    """
    boots = np.array([0.190, 0.200, 0.210])
    got = M.mde_resolution(boots)
    expect = (np.percentile(boots, 97.5) - np.percentile(boots, 2.5)) / 2.0
    assert got == pytest.approx(expect), "必须是 CI 半宽本身，不是任何距离"
    assert got < 0.01, f"CI 半宽 {got:.4f} 不该是「离上尾的距离」那个量级"


def test_a_tight_band_can_still_be_unresolvable() -> None:
    """§19.2 的**全部意义**：带宽窄 ≠ 分辨率够。

    🚨 原判据 `band < |s − p95|` 是**逻辑自噬**（要判的那个距离本身
    越大越判不出）。这条用构造数据钉住修好后的行为：
    带宽只有 0.0001，而分辨率是 0.05 ⇒ 必须判「判不出」。
    """
    rec = _rec(s=0.50, p5=0.1000, p95=0.1001, res=0.05)
    assert M.band(rec) < rec["mde_resolution"]
    assert _branch_of(rec) == "E-判不出"


# ─────────────────────────────────────────────────────────────
# 账户净值口径（§18.1：差序列必须先逐日相加）
# ─────────────────────────────────────────────────────────────

def test_account_daily_adds_the_market_leg_to_the_differential_series() -> None:
    """🚨 §18.1 的硬约束：`net_excess_*` 是**差序列**，不是净值序列。

    直接拿差序列当净值算 CAGR，差 **10.01pp**（§18.1 实测）。
    本条用**构造数据**钉住「市场腿真的被加上」——
    差序列恒为 0 时，只有真的加了市场腿才拿得到非零净值。
    """
    tbl = {"market": np.array([0.01, -0.02, 0.03]),
           "net": {"bt003": np.array([0.001, -0.002, 0.003])}}
    got = M.account_daily(tbl, "bt003")
    assert got == pytest.approx([0.011, -0.022, 0.033]), (
        "账户日收益 = market + 该档净超额差序列；漏掉市场腿 ⇒ 抽到一个零均值的数")
    assert got.mean() != pytest.approx(0.0), "恒零说明市场腿没进来"


def test_account_daily_rejects_an_unregistered_cost_tag() -> None:
    """缺档必须**报错**，绝不回落默认（第 90 层：缺低尾档用全截面顶上 = 口径不一致）。"""
    tbl = {"market": np.array([0.0]), "net": {"bt003": np.array([0.0])}}
    with pytest.raises(KeyError):
        M.account_daily(tbl, "hi006")


# ─────────────────────────────────────────────────────────────
# `MIN_BASKET` 不许手打（第 73 层）
# ─────────────────────────────────────────────────────────────

def test_min_basket_is_computed_from_the_production_formula() -> None:
    """🚨 `verify_mod.MIN_BASKET = 3` 曾是**手写字面量**。

    判据锚在**赋值右端**（第 13 层：子串会被注释满足）。
    """
    from factor_width_mde import scaled_min_basket
    src = SRC.read_text(encoding="utf-8")
    tree = ast.parse(src)

    def _is_min_basket(target: ast.expr) -> bool:
        # 生产侧是 `verify_mod.MIN_BASKET = …` ⇒ 属性赋值，不是裸 Name
        return (isinstance(target, ast.Attribute)
                and target.attr == "MIN_BASKET")

    right_sides = [ast.unparse(node.value) for node in ast.walk(tree)
                   if isinstance(node, ast.Assign)
                   and any(_is_min_basket(t) for t in node.targets)]
    assert right_sides, "脚本必须真的写过 MIN_BASKET（否则这条守卫是恒真的）"
    assert any("scaled_min_basket" in r for r in right_sides), (
        f"MIN_BASKET 的赋值右端是 {right_sides} ⇒ 有手写字面量（第 73 层）")
    # 正控：生产公式在合成分辨率下确实给出 3（否则下面的断言在测别的东西）
    assert scaled_min_basket(10) == 3


# ─────────────────────────────────────────────────────────────
# 抽样域规则（§10.2 的先验）
# ─────────────────────────────────────────────────────────────

def test_the_random_arm_uses_a_domain_preserving_shuffle() -> None:
    """🚨 🚨 `make_shuffle_list` 给每行赋**有限值** ⇒ `notna` 剔不掉
    ⇒ 随机臂的域 ⊇ 策略臂的域 ⇒ 两臂不可比（§10.2）。

    断言**用点**（脚本真的调它），不是定义点。
    """
    src = SRC.read_text(encoding="utf-8")
    tree = ast.parse(src)
    called = {n.func.id for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "make_shuffle_score" in called, "随机臂必须用保域置换"
    assert "make_shuffle_list" not in called, (
        "🚨 make_shuffle_list 给每行赋有限值 ⇒ 域 ⊇ 策略域 ⇒ 不再是对照臂"
        "（§10.2；改它等于换判据，第 91 层）")


def test_the_domain_rule_says_what_it_is_not() -> None:
    """产物里必须写清抽样域**是什么与不是什么**（第 7/177 层）。"""
    joined = " ".join([M.DOMAIN_RULE, str(M.BRANCH_TEXT)])
    assert "NaN" in joined
    assert "make_shuffle_list" in joined, (
        "必须写明为什么不用 make_shuffle_list —— 只写「用保域置换」是半句话")


# ─────────────────────────────────────────────────────────────
# 「年化」有两个常数（第 97/93 层的同族）
# ─────────────────────────────────────────────────────────────

def test_days_per_year_is_imported_from_the_single_source() -> None:
    """🚨 仓内有三套年化常数（244 / 250 / 252）⇒ **不是同一个量**。

    Y5 两臂必须同一个函数同一个常数 ⇒ `DAYS_PER_YEAR` 从
    `aplus.hurdle` import，不许自己写 244。
    """
    from aplus.hurdle import DAYS_PER_YEAR as HURDLE_R
    assert M.R == HURDLE_R, "R 必须就是 aplus.hurdle.DAYS_PER_YEAR"


def test_the_script_reports_the_other_two_constants_instead_of_hiding_them() -> None:
    """⚠️ 三套常数并存必须**显式报出来**，否则读者以为「年化」只有一个定义。"""
    # 🚨 2026-10-16 删掉原第 333 行 `assert … != … or True  # 可调用即可`：
    #   `or True` 让它**恒真**（第 131 层我自己犯的形态），而元守卫
    #   `test_generator_names_resolve.py::test_no_guard_in_tests_asserts_a_constant_truth`
    #   当场把它抓了出来 —— 那条元守卫的第一次实战。
    #   ⇒ 删它的理由是**它没有可辩护的内容**，不是「为了让它变绿」：
    #   `!=` 断言的是两个**不同来源**的常数必须不同（相同同样合法，将来可能统一），
    #   而 `or True` 之后残留的唯一意思「可调用」已被下面两行 `isinstance` 覆盖
    #   （`isinstance` 蕴含调用成功且返回值是 int）。
    assert isinstance(M.aplus_costs_days(), int)
    assert isinstance(M.verify_days(), int)


# ─────────────────────────────────────────────────────────────
# 产物装配：键齐全 + 字面量不许进产物
# ─────────────────────────────────────────────────────────────

def _artifact_literal_keys() -> set[str]:
    src = SRC.read_text(encoding="utf-8")
    keys: set[str] = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Dict):
            for k in node.keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    keys.add(k.value)
    return keys


def test_the_artifact_carries_the_section_10_8_contract_keys() -> None:
    """§10.8 落盘契约的键必须**真的在装配里**，不许靠散文声明。"""
    keys = _artifact_literal_keys()
    required = {"kind", "prereg", "window", "reps", "seed_base", "seed_rule",
                "domain_rule", "domain_check", "quantiles",
                "p95_bootstrap_ci95", "mde", "branch_order",
                "branch_conditions", "branch", "branch_text", "cost",
                "window_identity_across_arms", "market_identical_across_arms",
                "external_anchor", "what_this_cannot_prove",
                "branch_computed_but_not_adopted"}
    missing = sorted(required - keys)
    assert not missing, f"§10.8 契约缺键：{missing}"


def test_the_smoke_artifact_is_written_to_a_separate_path() -> None:
    """§19.5：冒烟走**独立路径**且 `verdict = null`、`branch = null`。

    🚨 理由：n=3 的 p95 就是最大值 ⇒ 冒烟分位说出的任何句子
    都是 n=1 的结论穿两层外衣（第 98 层）。
    """
    assert M.N_SMOKE_REPS == 3
    assert M.N_RANDOM_REPS == 100, "正式 reps 是 Y5-R1 预注册的 100，不许改"
    src = SRC.read_text(encoding="utf-8")
    assert "random_arm_nav_smoke" in src, "冒烟产物必须是独立路径"


def test_the_smoke_seeds_are_a_prefix_not_a_second_draw(monkeypatch) -> None:
    """§19.5：冒烟种子与正式跑**完全相同**（前缀，不是另抽）。"""
    smoke = [M.Y5_SEED_BASE + r for r in range(M.N_SMOKE_REPS)]
    formal = [M.Y5_SEED_BASE + r for r in range(M.N_RANDOM_REPS)]
    assert smoke == formal[:M.N_SMOKE_REPS]


def test_the_strategy_arm_is_pinned_to_the_external_anchor() -> None:
    """🚨 判 `branch` 之前必须与外部锚**逐位对拍**
    （`basket_nav_01_2026-10-16.json::verdict_reading`）。"""
    src = SRC.read_text(encoding="utf-8")
    assert "basket_nav_01_2026-10-16.json" in src, (
        "策略臂必须钉在已登记的判决档上，不许自己重算一个「策略值」")
    assert "verdict_reading" in src, "读的是 `verdict_reading` 字段，不是重算"


def test_the_strategy_own_mde_is_recorded_but_never_enters_the_branch() -> None:
    """§19.4：策略臂自身的 MDE **落盘但不进分支**。

    🚨 它是**绝对水平**的 MDE，而本判决问**相对位置**；
    拿绝对量判相对 = 第 94 层。22.67pp 与 100 次抽样给出的带宽
    不在一个量级 ⇒ 若参与计算，Y5 变成一台「永远说判不出」的机器。
    """
    src = SRC.read_text(encoding="utf-8")
    assert "strategy_own_mde_does_not_enter_branch" in src
    # 反向正控：分支函数**不许**读那个字段
    branch_src = _func_src(src, "decide_branch")
    assert "own_mde" not in branch_src
    conds_src = _func_src(src, "branch_conditions")
    assert "own_mde" not in conds_src


def test_the_verdict_never_claims_the_absolute_level_was_measured() -> None:
    """🚨 §19.4 的代价必须写在产物**正文**里：
    本判决**不能**被读成「账户年化 +10.56% 本身测出来了」。

    ⚠️ 判据钉在**运行时可问**的模块级常量上（第 124 层），
    不钉在 `BRANCH_TEXT` 上 —— 分支文案只覆盖被选中那一条，
    而这条限定必须**四条分支都在**的产物正文里。
    """
    joined = " ".join(M.WHAT_THIS_CANNOT_PROVE)
    assert "20%" in joined, "产物正文必须自己写明「不能证明 20% 可达」"
    assert "连符号都定不了" in joined, (
        "绝对水平那一半的既有结论必须逐字带出来，不许靠读者记得 §18")
    assert len(M.WHAT_THIS_CANNOT_PROVE) >= 5, "四条 + 冒烟一条，一条都不能少"


def test_the_branch_text_never_claims_factor_validity() -> None:
    """🚨 第 16 种形状：把「测不到」写成「没有 alpha」。

    `E-内` 的「与随机不可区分」是**本判决范围内的**形状陈述；
    它不许被扩写成关于因子有效性的结论（那要另一次实验）。
    """
    joined = " ".join(str(v) for v in M.BRANCH_TEXT.values())
    assert "因子有效" in joined, "E-上 必须自带「不等于因子有效」"
    # 反向：任何一条都不许声称因子无效
    for name, text in M.BRANCH_TEXT.items():
        assert "没有 alpha" not in text and "因子无效" not in text, (
            f"{name}：把「在本口径下测不到」说成了「因子无效」"
            f"（第 16 种形状：两种情形长得几乎一样）")


# ══════════════════════════════════════════════════════════════════════
# §10.3 日期轴闸（Y6）与 §19.4 判决采纳（Y8）
#
# 这两条的共同形状：**判据原本埋在 `_run()` 的函数体里**，
# 守卫够不着（第 124 层：判据不可问就等于没有守卫）。
# 而变异台跑出 `rc=0 / 29 passed` ⇒ 变异真落盘、语法合法、守卫看不见。
#
# 提取那两个表达式到模块级**不是为了变绿**，是因为一条「不可问的判据」
# 与「没有判据」在台子输出里逐位相同。
# ══════════════════════════════════════════════════════════════════════

def _axis_fixture() -> tuple:
    """三份逐日表，判别力来源**各不相同**（第 146 层：靠随机巧合成立的守卫零判别力）。

    ⚠️ `shift3` 刻意构造成「长度**恰好相同** + `market` **逐位相同**」而**日期不同**
    —— 这正是 §10.3 点名的形态：**靠 market 相等顺带证明日期相同**。
    """
    strat = {"keys": list(range(10)), "market": np.arange(10.0)}
    same = {"keys": list(range(10)), "market": np.arange(10.0)}
    shift3 = {"keys": list(range(3, 13)), "market": np.arange(10.0)}
    diff_m = {"keys": list(range(10)), "market": np.arange(10.0) * 2.0}
    return strat, same, shift3, diff_m


def test_the_shifted_axis_fixture_is_actually_the_hard_case() -> None:
    """守卫自己的夹具必须先证明「它是难的那个」（第 146 层）。

    没有这一条，`shift3` 哪天被改成「长度也不同」，下面两条守卫会立刻退化成
    「比长度」——而**变异台不会喊**：它只看到 29 passed。
    """
    strat, _, shift3, _ = _axis_fixture()
    assert len(shift3["keys"]) == len(strat["keys"]), \
        "夹具失效：长度不再相同 ⇒ 守卫退化成比长度（第 146 层）"
    assert np.array_equal(shift3["market"], strat["market"]), \
        "夹具失效：market 不再逐位相同 ⇒ 守卫不再面临「顺带证明」的那个诱惑"


def test_same_date_axis_compares_the_date_axis_and_nothing_else() -> None:
    """随机臂必须**单比日期轴**。

    🚨 `market` 逐位相等**推不出**日期相同：部分重叠时长度可以恰好相同。
    ⇒ `shift3` 必须判 False，而 `diff_m`（market 不同、日期同）必须判 True ——
       **后者证明函数根本没读 market**。
    """
    strat, same, shift3, diff_m = _axis_fixture()
    strat_keys = list(strat["keys"])
    assert M.same_date_axis(same, strat, strat_keys) is True
    assert M.same_date_axis(shift3, strat, strat_keys) is False, (
        "日期部分重叠被判成相同 ⇒ Y5-A3 被 market 逐位相等顺带证明了")
    assert M.same_date_axis(diff_m, strat, strat_keys) is True, (
        "market 不同竟被判成日期轴不同 ⇒ 本函数读了 market，"
        "那它就不是 §10.3 说的那一条独立闸")


def test_same_date_axis_does_not_mention_market_at_all() -> None:
    """接线层：`same_date_axis` 的源码里**不许**出现 `market`。

    上一条验的是**行为**（三份夹具）；这一条验的是**实现不许偷偷读它**。
    两者不可互换：行为守卫对「读 market 但读错了」零信息量。
    """
    # 🚨 扫 `_func_code`（剥掉 docstring）而不是 `_func_src`：
    #   它的 docstring 里逐字讨论 `market`（解释为什么不读它），
    #   用整段扫会被**文档本身**判红 —— 第 13 层「守卫可被文档满足」。
    code = _func_code(SRC.read_text(encoding="utf-8"), "same_date_axis")
    assert "market" not in code, (
        "same_date_axis 的实现里出现了 market —— §10.3 要求它是**独立**的一条闸")


def test_the_run_loop_actually_calls_the_extracted_date_axis_check() -> None:
    """接线层：`_run()` 必须真的调它（反向正控：钉的是调用点不是定义点）。"""
    run_src = _func_src(SRC.read_text(encoding="utf-8"), "_run")
    assert "same_date_axis(" in run_src, (
        "_run() 没有调用 same_date_axis ⇒ 提取出来的函数是死代码，"
        "守卫在守一个没人用的东西（第 119 层）")


@pytest.mark.parametrize("verdict,adopted", [
    ("E-上｜策略臂 +10.5563%/年", True),
    ("E-内｜策略臂 -0.5%/年", True),
    (None, False),
])
def test_branch_is_adopted_exactly_when_a_verdict_was_issued(verdict, adopted) -> None:
    """`branch` 与 `verdict` **同生共死**。

    🚨 第 179 层：压住判决时若仍写 `branch`，产物里就有一对自相矛盾的键
    （`verdict: null` 说「没说话」、`branch: "E-上"` 说「我判了 E-上」），
    而两条**单独读都成立**。

    🚨 2026-10-16 删掉原第四档 `("", False)` —— 那是我**凭空加的形态**：
    生产里 `verdict` 只有两种来源，`decide_branch()` 返回 `_BRANCH_ORDER` 的
    非空成员，或被压住时显式写 `None`，**空串永不可达**。
    ⇒ 给不可达形态加约束 = 给生产加一条**恒真的要求**（第 112 层：
      过严的守卫和恒真的守卫一样有害），而它唯一的实际效果是让生产代码
      长得像在防一件不会发生的事。
    ⇒ 若将来 `verdict` 真的可能出现 `""`，那时再加这一档 —— 那时它才是判据。
    """
    assert M.verdict_is_adopted("E-上", verdict) is adopted, (
        f"verdict={verdict!r} ⇒ adopted 应为 {adopted}")


def test_the_branch_keys_are_all_gated_by_the_same_adoption_predicate() -> None:
    """三个键（`branch` / `branch_text` / `branch_computed_but_not_adopted`）
    必须由**同一个**采纳判据把门 —— 否则只改其中一个就自相矛盾。"""
    run_src = _func_src(SRC.read_text(encoding="utf-8"), "_run")
    gates = run_src.count("verdict_is_adopted(branch, verdict)")
    assert gates == 3, (
        f"`verdict_is_adopted` 在产物三键处出现 {gates} 次（期望 3）"
        " ⇒ 至少有一个键绕过了采纳判据")


def test_the_not_adopted_block_carries_the_not_adopted_qualifier() -> None:
    """「算出来但没被采用」的那条码必须自带**未被采用**的限定（第 179 层）。

    只把它放进一个叫 `branch_computed_but_not_adopted` 的键里**不够** ——
    这个键名是本脚本自己写的，而产物会被单独截取某一段引用。
    """
    run_src = _func_src(SRC.read_text(encoding="utf-8"), "_run")
    assert "未被采用" in run_src, (
        "落盘的限定词不见了 ⇒ 截取出这一段的人会把它读成已采用的判决")


def test_a_suppressed_verdict_is_never_revived_by_the_scale_note() -> None:
    """🚨 **能力守卫 + 反控**：`verdict` 被压成 `None` 之后加规模标注，
    **必须仍然是 `None`** —— 且那句标注必须落进 `suppressed`（不许丢）。

    这条守的是预注册 §20.5 的**潜伏**缺陷：`verdict = f"{verdict}…"`
    会把 `None` 拼成字面量 `'None　🚨 仅 5 rep…'`，而
    `verdict_is_adopted` 的判据是 `verdict is not None` ⇒ 判 **True**
    ⇒ 产物里 `branch: "E-内"`（说「我判了 E-内」）配
    `verdict: "None 🚨 …"` —— 一对自相矛盾的键（第 179 层）。

    ⚠️ 断言刻意写成 `is None`（**身份**）而不是「内容里没有 'None'」——
    后者**正是原缺陷的形状**：用内容去判断存在性。
    探针实测原代码返回 `'None　🚨 …'`（一个 str），所以这一条**判别力真实存在**。
    """
    sup: list = []
    got = M.append_rep_scale_note(None, sup, "仅 5 rep，不得引用")
    assert got is None, (
        f"被压住的判决被复活成了 {got!r} ⇒ 产物里会同时出现"
        "「verdict 有值」与「verdict 说自己没说话」两种说法（第 179 层）")
    assert len(sup) == 1, (
        f"标注既没写进 verdict 也没落进 suppressed（suppressed={sup!r}）"
        " ⇒ 这条信息在产物里彻底消失了，比自相矛盾更难排查")


def test_a_live_verdict_still_carries_the_scale_note() -> None:
    """**反向臂**：判决还活着时，那条标注**必须真的出现在返回值里**。

    🚨 与上一条**成对**：只有上一条而没有这一条，则
    `append_rep_scale_note` 只要**永远返回 `None`** 就全绿
    —— 而那会把每一个正式判决都抹掉（第 117 层：过严的筛选让 `all()` 退化
    成装饰，症状与「守对了」逐位同形）。
    ⇒ 两条**互斥且都必须有证据**。
    """
    sup: list = []
    got = M.append_rep_scale_note("E-内｜策略臂 +10.5563%/年", sup, "仅 5 rep，不得引用")
    assert got is not None, "活的判决被这个函数吞掉了"
    assert "仅 5 rep" in got, f"标注没被写进去：{got!r}"
    assert got.startswith("E-内"), f"原判决句被覆盖而不是追加：{got!r}"
    assert sup == [], f"判决还活着时不该动 suppressed，实际 {sup!r}"


def test_the_scale_note_helper_is_actually_called_by_the_run_loop() -> None:
    """接线层：`append_rep_scale_note` 必须真被 `_run()` 调到（接线守卫）。

    🚨 行为守卫只证明**这个函数**对，零信息量于它是否被接上（第 119 层：
    提取出来的东西是死代码，两种失效在输出上同形）。
    ⚠️ 断言串**带左括号**：`append_rep_scale_note` 不带括号的写法在上面的
    docstring 与注释里逐字出现过 —— 不带括号会被**文档本身**满足（第 13 层）。
    """
    run_src = _func_src(SRC.read_text(encoding="utf-8"), "_run")
    assert "append_rep_scale_note(" in run_src, (
        "_run() 没有调用 append_rep_scale_note ⇒ 非预注册规模的标注"
        "要么丢失，要么落回那段会把 None 拼成字符串的写法")


def test_the_scale_note_helper_is_not_defined_inside_the_run_loop() -> None:
    """被守的函数必须在**模块级**（守卫能 import 到 ⇒ 才有任何东西能挡它）。

    🚨 第「守卫 import 不到的代码等于零守卫」：判据若长在 `_run()` 内部，
    判决逻辑对**任何**变异都无保护，而台子一条都不会喊。
    ⇒ 这条从 AST 断言 `append_rep_scale_note` 是 `Module.body` 的直接孩子。
    """
    top_level = {n.name for n in ast.parse(SRC.read_text(encoding="utf-8")).body
                 if isinstance(n, ast.FunctionDef)}
    assert "append_rep_scale_note" in top_level, (
        "append_rep_scale_note 不在模块级 ⇒ 它被定义在某个函数内部，"
        "而嵌套函数没有任何东西能挡它")


# ═════════════════════════════════════════════════════════════
# 🚨 `n_ge` 那一行：2026-10-16 真实事故
# ═════════════════════════════════════════════════════════════
#
# 冒烟跑了 **724.6 秒**、3 个 rep 全跑完，在**写盘前最后一刻**炸：
#     AttributeError: module 'numpy' has no attribute 'count_nans'
#   scripts/measure_random_nav.py:595  →  np.count_nans(random_arr >= strat_cagr)
#
# ### 为什么这件事比它看起来严重
#
# ① **它算的不是 `rank_rule` 声称的那个量**。产物里逐字写着
#    `"n_ge = 随机臂中 ≥ 策略值的个数（保守计数）"`，字段名、`rank_rule`、
#    CLI 打印（`:778`）三处都在说「≥ 策略值的个数」，
#    **只有代码在数 NaN**（形状 18 的极端形态：主张说数据、代码做别的）。
#
# ② **45 条守卫 + 12/12 变异台全绿，而这一行零覆盖**。
#    「12/12 全杀」在这一行上**信息量为零** —— 没有一条变异落在它上面
#    （记忆：守卫只有变异后算数）。
#
# ③ 🚨 **它炸了反而是运气好**。若 `count_nans` 碰巧存在，它会返回
#    **NaN 的个数 = 0** ⇒ `n_ge=0`、`rank_pct=0.0`、`rank_rule` 照常说
#    「≥ 策略值的个数」⇒ **产物照常出数、判决照常发，而值是 0**
#    （第 16 层假绿最坏方向：让「没有超额」被当成「一次都赢不了」）。
#
# ### 修法由语义决定，不是由 API 名决定
#    `random_arr >= strat_cagr` 是**布尔数组** ⇒ 要数真值个数 = `count_nonzero`。
#    且 `nan >= x` 为 `False` ⇒ NaN 自动不计入 ⇒ **「保守计数」成立**。
#
# ### 下面三条分别守三层，缺一不可
#
# | # | 层 | 守什么 | 判别力来自 |
# |---|---|---|---|
# | G1 | 性质 | `count_at_least` 数的是「≥ 阈值的个数」 | 合成分岔夹具（含恰好相等 / NaN / 全 NaN） |
# | G2 | 接线 | `_run` **真的调它**，且实参不是字面量 | AST（第 189 层：变异必须打在用点上） |
# | G3 | 全仓 | 全仓不出现**当前 numpy 上不存在**的 `np.*` | `hasattr` 反向枚举 + 正控 |

# 那个被打错的 API 名。它**逐字**出现在本段 docstring 与下面几条守卫的
# docstring 里，而 G3 走 AST（`ast.Attribute`）⇒ 注释与字符串**进不了**它的眼。
# 这既是 G3 的设计前提，也是第 13 层「子串断言可被文档满足」的正面反证。
_BAD_NP_NAME = "count_" + "nans"


def test_count_at_least_counts_values_at_or_above_the_threshold() -> None:
    """G1 性质层：**≥ 的个数**，且 NaN 不计入（「保守计数」）。

    🚨 **判别力全部来自构造出来的分岔**，不来自真实数据：
    `>` 与 `>=` 的差别**只在恰好等于阈值的元素上** ⇒ 夹具里必须有一个
    恰好等于 threshold 的数，否则这条守卫恒真（第 119 层：靠数据巧合
    成立的守卫，38 条全绿而变异存活）。
    """
    count = M.count_at_least                      # 缺它 ⇒ AttributeError = 真红

    # 恰好相等（0.1）**必须**计入 ⇒ 钉住 `>=` 而非 `>`
    assert count(np.array([0.1, 0.2, np.nan, -0.5]), 0.1) == 2
    # 只差一个 ulp 也算 ≥
    assert count(np.array([np.nextafter(0.1, 0.2)]), 0.1) == 1
    # NaN 不计入（`nan >= x` 是 False）
    assert count(np.array([np.nan, np.nan, 0.9]), 0.1) == 1
    # 全 NaN ⇒ 0（**不是** len）
    assert count(np.array([np.nan, np.nan]), 0.1) == 0
    # 空 ⇒ 0
    assert count(np.array([]), 0.1) == 0
    # 全 ≥ / 全 <
    assert count(np.array([1.0, 2.0, 3.0]), 0.1) == 3
    assert count(np.array([-3.0, -2.0, -1.0]), 0.1) == 0
    # 负阈值（阈值本身可以是负的）
    assert count(np.array([-0.5, 0.0, 0.5]), -0.5) == 3


def test_count_at_least_is_a_module_level_function_not_a_nested_one() -> None:
    """G1 的前提：`count_at_least` 必须在**模块级**，守卫才 import 得到。

    「守卫 import 不到的代码等于零守卫」：判据若长在 `_run()` 内部，
    判决逻辑对**任何**变异都无保护，而变异台一条都不会喊。
    """
    top_level = {n.name for n in ast.parse(SRC.read_text(encoding="utf-8")).body
                 if isinstance(n, ast.FunctionDef)}
    assert "count_at_least" in top_level, (
        "count_at_least 不在模块级 ⇒ 它被定义在某个函数内部，"
        "而嵌套函数没有任何东西能挡它")


def test_the_run_loop_actually_calls_count_at_least() -> None:
    """G2 接线层：`_run` 里**真的**调 `count_at_least`，且实参是那两个变量。

    🚨 没有这条，G1 会被完全绕过：把 `:595` 改回内联的
    `np.count_nonzero(...)` 或 `np.count_nans(...)`，
    **性质守卫照样全绿** —— 它只验函数，不验谁在用它。
    ⇒ 变异必须打在**用点**上，不许打在定义点上。

    实参不许是字面量：否则有人把调用改成 `count_at_least([0.1, 0.2], 0.15)`
    也能过，而那量的是夹具不是判决。
    """
    run_src = _func_src(SRC.read_text(encoding="utf-8"), "_run")
    tree = ast.parse(run_src)
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "count_at_least"]
    assert calls, (
        "_run() 没有调 count_at_least ⇒ 排名仍在原地手写，"
        "而那条手写正是 724 秒后炸掉的那一行")
    assert len(calls) == 1, f"_run() 调了 {len(calls)} 次 count_at_least，应当恰好 1 次"
    args = calls[0].args
    assert len(args) == 2, f"count_at_least 收了 {len(args)} 个位置实参，应当是 2 个"
    for a, want in zip(args, ("random_arr", "strat_cagr")):
        assert isinstance(a, ast.Name) and a.id == want, (
            f"count_at_least 的实参是 {ast.unparse(a)!r}，应当是变量 {want!r} —— "
            "字面量实参量的是夹具，不是判决臂的随机臂数组")


def test_no_np_attribute_anywhere_is_missing_from_this_numpy() -> None:
    """G3 同族层：全仓**不得**引用当前 numpy 上不存在的 `np.*` 属性。

    ★ 这条不是为今天这一行写的 —— 它是**扫描**出来的。
    今天之前的「找同族」我用的是 grep 词表（搜 `count_nan`），
    而 grep 词表太窄这件事本身**已经犯过一次**（上一轮怀疑
    `friction_sensitivity.py` 是假取证 ⇒ 实际是词表太窄，不是它零输入）。
    ⇒ 改成 `hasattr` **反向枚举**：不看「找什么」，看「有什么不存在」。

    正控两条（缺了它们，这条守卫在探测器坏掉时**永远绿**）：
      ① 探测器真的能抓到不存在的属性；
      ② 真实存在的属性（`count_nonzero` / `nanmean` / `random`）不误报。
    """
    missing: list[str] = []
    seen_names: set[str] = set()
    for sub in ("aplus", "scripts", "tests"):
        for py in sorted((ROOT / sub).rglob("*.py")):
            try:
                tree = ast.parse(py.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if (isinstance(node, ast.Attribute)
                        and isinstance(node.value, ast.Name)
                        and node.value.id == "np"):
                    seen_names.add(node.attr)
                    if not hasattr(np, node.attr):
                        rel = py.relative_to(ROOT)
                        missing.append(f"{rel}:{node.lineno} np.{node.attr}")

    # 正控 ①：探测器必须看得见「不存在」
    fake = "count_" + "definitely_not_a_numpy_api"
    assert not hasattr(np, fake), "正控失效：那个假名字居然在 numpy 上存在"
    # 正控 ②：真实存在的不得被报（`random` 是子模块，`hasattr` 为真）
    for real in ("count_nonzero", "nanmean", "percentile", "random", "array"):
        assert hasattr(np, real), f"正控失效：np.{real} 不存在？numpy 版本变了？"
    # 正控 ③：探测器至少看得见真代码里的 np.* （防空扫描：正则/遍历坏了 ⇒ 恒真）
    assert len(seen_names) > 20, (
        f"整仓只扫到 {len(seen_names)} 个 np.* 属性 ⇒ 探测器大概率坏了，"
        "此时本守卫会永远绿（第 126 层：探针先被验证，再拿去判别人）")

    assert not missing, (
        "🚨 以下引用了当前 numpy 上不存在的属性（跑到底才炸，或更糟："
        "碰巧存在时静默给出错值）：\n  " + "\n  ".join(sorted(missing)))


def test_the_np_attribute_scanner_is_not_satisfied_by_prose() -> None:
    """G3 的自反正控：本段 docstring **逐字**写着那个坏名字，而 G3 抓不到它。

    第 13 层：子串断言 + 同一文件里存在注释/文档 = **守卫可被文档满足**。
    G3 走 AST（`ast.Attribute`）⇒ 字符串常量与注释都进不了它的眼。
    ⇒ 这条把「它靠的是语法位置而不是文本出现」钉成可测。
    """
    assert _BAD_NP_NAME == "count_" + "nans", "正控失效：拼出来的坏名字不对"
    src = (ROOT / "tests" / "test_random_arm_nav_verdict.py").read_text("utf-8")
    assert _BAD_NP_NAME in src, (
        "本测试文件里根本找不到那个坏名字 ⇒ 「文档满足子串」的正控失效，"
        "G3 那条「不被散文满足」的结论是空转出来的")
    # 而它确实不是 ast.Attribute ⇒ G3 的扫描逐字看不见
    bad_calls = [n for n in ast.walk(ast.parse(src))
                 if isinstance(n, ast.Attribute)
                 and isinstance(n.value, ast.Name)
                 and n.value.id == "np" and n.attr == _BAD_NP_NAME]
    assert not bad_calls, (
        "本文件里居然有一处真实的 np.<坏名字> 调用 ⇒ "
        "G3 应当报它；这说明本正控的前提变了")


# ─────────────────────────────────────────────────────────────
# §21.4：market 身份必须**逐 rep** 落盘（2026-10-16）
#
# 🚨 旧代码在逐 rep 循环里写的是 `if rep == 0:` ⇒ 只有第 0 个 rep
#   被量过，其余 99 个在产物里是 **`None`**。而 `None` 与「算过、
#   结果是 False」**逐位同形**（第 7 层）⇒ 那 99 个 `None` 读起来
#   像「量过了，都没问题」，而真实是**根本没量**。
#   而汇总层的 `checked_on: "rep 0"` 正是把这个事实**写进键名里**，
#   却与产物里那一列 `None` 并排出现 ⇒ **两份副本之间没有守卫**。
#
# 本节守三件事，全部靠**构造出来的分岔夹具**：
#   ① `market_identity` 真能给判决（能力守卫，第 131 层）
#   ② 「不可比」**不许**被压成「不一样」（第 7 层）
#   ③ 汇总层**不许**有一句内建覆盖范围的声称（第 179 层）
# ─────────────────────────────────────────────────────────────


def _mi_fixture() -> list:
    """四个 rep，其中**最大差值不在 rep 0** —— 这是本夹具的全部意义。

    ⇒ 任何「取 `per_rep[0]`」的写法都会读到 rep 0 的
      `market_max_abs_diff` **缺失**，于是汇总值变成 `None` 而非 `5e-18`
      ⇒ 这条夹具直接杀掉第 112 层（两份副本口径）。
    """
    return [
        # rep 0：**日期轴不同 ⇒ 未被比较**（不是「比较了且不同」）
        {"rep": 0, "dates_identical_to_strategy": False,
         "market_not_compared": "日期轴与策略臂不同 ⇒ 不可比"},
        # rep 1：比了，逐位相同
        {"rep": 1, "dates_identical_to_strategy": True,
         "market_identical_to_strategy": True, "market_max_abs_diff": 0.0},
        # rep 2：比了，**逐位不同**，且这是全局最大差值
        {"rep": 2, "dates_identical_to_strategy": True,
         "market_identical_to_strategy": False, "market_max_abs_diff": 5e-18},
        # rep 3：比了，逐位相同
        {"rep": 3, "dates_identical_to_strategy": True,
         "market_identical_to_strategy": True, "market_max_abs_diff": 0.0},
    ]


def test_market_identity_gives_a_verdict_on_equal_series() -> None:
    """能力守卫（第 131 层）：证明它**能**给出判决，而不是恒返 `None`。"""
    a = np.array([0.001, -0.002, 0.0])
    out = M.market_identity(a, a.copy())
    assert out["identical"] is True, "逐位相同的序列必须判 True"
    assert out["max_abs_diff"] == 0.0, "逐位相同 ⇒ 差恒为 0"


def test_market_identity_detects_a_single_ulp_difference() -> None:
    """反向正控：只差**最后一位**也必须看得见（不许用容差把它抹掉）。"""
    a = np.array([0.001, 0.002])
    b = a.copy()
    b[1] = float(np.nextafter(b[1], np.inf))          # 1 ulp
    out = M.market_identity(a, b)
    assert out["identical"] is False, "差 1 ulp 就不许说 identical"
    assert out["max_abs_diff"] > 0.0, "差必须被报成一个正数，而不是四舍五入成 0"


def test_market_identity_reports_nan_as_a_difference_not_as_equality() -> None:
    """🚨 `max_abs_diff == 0` **不蕴含** `identical=True`。

    `nanmax` 会跳过 NaN 位置，而 `array_equal` 对 NaN 判不等
    ⇒ 两臂若在同一位置都是 NaN，最大差值仍是 0.0 而 `identical` 为 False。
    ⇒ **「两个各自正确的数在这里指向不同的东西」**；
    若有人把 `identical` 写成 `max_abs_diff == 0`，这条当场变红。
    """
    a = np.array([0.001, np.nan])
    b = np.array([0.001, np.nan])
    out = M.market_identity(a, b)
    assert out["identical"] is False, "含 NaN ⇒ 逐位相等为假（array_equal 的口径）"
    assert out["max_abs_diff"] == 0.0, "NaN 位置被 nanmax 跳过 ⇒ 差确为 0"


def test_market_identity_refuses_unequal_lengths_loudly() -> None:
    """两臂不等长 ⇒ **报错**，不许返回 `inf`。

    🚨 旧代码那条 `else float("inf")` 在 `same_axis` 为真时**结构上不可达**
    （`chunk_to_daily` 的 `market` 由 `keys` 逐项生成 ⇒ `len(market) ≡ len(keys)`，
    而 `same_date_axis` 就是 `tb["keys"] == strat_keys`）⇒ 它是一段**死代码**。
    而它一旦可达，落盘的会是一个「看起来很正常、实际代表无穷大」的数。
    ⇒ 长度不等是**调用点的 bug**，必须响亮，不许降级成一个可读的数。
    """
    with pytest.raises(ValueError):
        M.market_identity(np.array([1.0, 2.0, 3.0]), np.array([1.0, 2.0]))


def test_the_market_identity_check_runs_on_every_rep_not_only_the_first() -> None:
    """接线守卫：逐 rep 循环里**必须**调 `market_identity`。

    🚨 判据落到**语法位置**上（`_run` 的可执行语句里出现该调用），
    不落全文子串 —— 否则函数名出现在本文档里就能满足它（第 13 层）。
    🚨 同时钉住**旧闸门必须消失**：全仓 `rep == 0` 原本只有一处（`scripts` 第 639 行），
    本守卫是它唯一的对手；留着它 ⇒ 产物里那 99 个 `None` 会被重新造出来。
    """
    assert "market_identity(" in _func_code(SRC.read_text(encoding="utf-8"), "_run"), (
        "_run 的可执行语句里没有 market_identity( 调用 ⇒ "
        "逐 rep 循环根本没在量 market（剥了 docstring 才查，避免文档满足）")
    stale = [n for n in ast.walk(ast.parse(_func_code(SRC.read_text(encoding="utf-8"), "_run")))
             if isinstance(n, ast.Compare)
             and isinstance(n.left, ast.Name) and n.left.id == "rep"
             and any(isinstance(c, ast.Constant) and c.value == 0
                     for c in n.comparators)]
    assert not stale, (
        "_run 里仍有 `rep == 0` 形式的闸门 ⇒ 只有第 0 个 rep 被量过，"
        "其余全部落成 None（第 7 层：None 与 False 逐位同形）")


def test_reps_with_different_dates_are_never_counted_as_market_mismatch() -> None:
    """★ 本节的核心判据：**不可比 ≠ 不一样**（第 7 层）。

    夹具里 rep 0 的日期轴不同 ⇒ 它**根本没被比较**。
    ⇒ 它必须落在 `n_reps_not_compared`，而**不许**进 `n_reps_bitwise_different`。
    🚨 旧写法 `same_axis and np.array_equal(...)` 把两者压成同一个 `False`
      ⇒ 这条守卫对那行代码是红的。
    """
    s = M.summarize_market_identity(_mi_fixture())
    assert s["n_reps_total"] == 4
    assert s["n_reps_compared"] == 3, "只有日期轴相同的 3 个 rep 进入了比较"
    assert s["n_reps_not_compared"] == 1, "日期轴不同的那 1 个必须单列"
    assert s["n_reps_bitwise_identical"] == 2
    assert s["n_reps_bitwise_different"] == 1, (
        "「没法比」的 rep 不许混进「不一样」—— 两者的处置完全不同："
        "前者是窗口没对齐，后者是两臂连基准都变了")


def test_the_summary_max_is_over_every_compared_rep_not_just_the_first() -> None:
    """🚨 夹具把全局最大差值放在 **rep 2**，rep 0 是未比较的那一个。

    ⇒ 任何退回 `per_rep[0]` 的写法都会取到 `None`（rep 0 没有那个键）
    ⇒ 这条守卫直接杀掉「汇总层回指 per_rep[0]」这条副本口径（第 112 层）。
    """
    s = M.summarize_market_identity(_mi_fixture())
    assert s["max_abs_diff_over_compared_reps"] == 5e-18, (
        "最大差值必须取遍**所有**已比较的 rep；夹具里它不在 rep 0，"
        "所以「取第一个」会读成 None")


def test_coverage_is_reported_by_counting_not_by_asserting() -> None:
    """第 179 层：**键名不许内建一个关于覆盖范围的判断**。

    旧键 `checked_on: "rep 0"` 是一个**值在替覆盖范围作保证**的键
    ⇒ 改成 `"all reps"` 仍然是内建保证 ⇒ **删键优于改值**
    （缺键是响亮的，撒谎的键是安静的）。
    ⇒ 覆盖范围改由三个**数出来的**计数承担。
    ⚠️ 两条守恒式是**兜底不是判别力**（在同一份过滤上恒真）；
      本节的判别力在上面两条夹具守卫上。
    """
    s = M.summarize_market_identity(_mi_fixture())
    boastful = [k for k in s if k in {"checked_on", "verified_on", "checked_rep"}]
    assert not boastful, (
        f"汇总层不许有内建覆盖范围声称的键：{boastful} —— "
        "删键优于改值；覆盖范围由计数承担")
    assert s["n_reps_compared"] + s["n_reps_not_compared"] == s["n_reps_total"]
    assert (s["n_reps_bitwise_identical"] + s["n_reps_bitwise_different"]
            == s["n_reps_compared"])

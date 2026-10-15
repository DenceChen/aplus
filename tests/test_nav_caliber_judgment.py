"""G1 判决的钉子：**R 档（门槛松、判决未翻）**必须一直由产物字段推得出来。

## 这份守卫钉的是「判决」，不是「机制」

产物里写着「等权基准几何年化 9.9471%」这件事，机制测一遍是不够的 ——
第 118 层：**代码与落盘产物之间会断链**。
⇒ 所以下面每条都**从 JSON 字段重新算一遍 R 档**，不读文档、不读代码里的常量。

## ⚠️ 量纲：`market_nav_caliber` 与 `factor_width_mde` 的 `*_pct` **不是同一个量纲**

| 字段 | 真值 | 含义 |
|---|---|---|
| `factor_width_mde::widths["10"].gross_annual_pct` | `0.0481` | **分数**（= 4.81%） |
| `market_nav_caliber::equal_weight.geometric_annual_pct` | `9.9471` | **百分数** |
| `market_nav_caliber::equal_weight.arithmetic_daily_mean_pct` | `0.0510` | **日**百分数 |

⇒ 我第一次判 G1 时按分数处理，基准被读成 0.0995%，
**而 R 档的方向恰好不变** ⇒ 一个 100 倍的错被「反正不翻面」吸收掉了。
⇒ **量纲错 + 结论不变 = 彻底静默**，因为没有任何守卫守量纲（它们守结论）。
⇒ 所以量纲必须由**独立推导路径**钉住，不能靠「换算一下看着对不对」。

## ⚠️ 为什么这里的锚是**阶数级**而不是**值级**

§④ 那两条量纲守卫故意只卡**数量级**（3 倍以内）——
它是**量纲锚**，不是**数值锚**；数值由 ① 那条复利恒等式精确钉。
⚠️ 「故意松」不等于「可以恒真」：④ 的反控证明了它**双向都能抓**。
"""
from __future__ import annotations

import ast
import json
import math
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NAV = ROOT / "data" / "reports" / "market_nav_caliber.json"
WM = ROOT / "data" / "reports" / "factor_width_mde.json"
PREREG = ROOT / "docs" / "TODO_nav_vs_excess_caliber_2026-10-15.md"
PROVENANCE = ROOT / "aplus" / "provenance.py"

#: 预注册 §4 G3-quad 的 P 档门槛：**在 G1 出数之前**写死，出数后未改动。
P_GRADE_BENCH_PCT = 22.46
#: 预注册 §0 恒等式里的目标与摩擦口径，都从产物字段读，不写字面量。
TARGET_NET = 0.20


def _nav() -> dict:
    return json.loads(NAV.read_text(encoding="utf-8"))


def _wm() -> dict:
    return json.loads(WM.read_text(encoding="utf-8"))


# ── ① 外部锚：复利终值 → 几何年化。**推导路径与脚本不同** ────────────────

def _geom_annual_pct(final_multiple: float, n_days: int, periods: float = 244.0) -> float:
    return ((final_multiple ** (periods / n_days)) - 1.0) * 100.0


@pytest.mark.parametrize("arm", ["equal_weight", "cap_weight"])
def test_the_geometric_annual_is_pinned_by_the_compounded_multiple(arm):
    """头条数字由 `compounded_final_multiple` + `n_days` 反推出来，必须逐位吻合。

    ⚠️ 这一条是**唯一**能判「9.9471 是百分数还是分数」的锚 ——
    若把值当分数处理，恒等式两边差 **100 倍**，当场红。
    ⚠️ 推导路径必须与脚本不同：脚本自己内部也是这条式子，
    所以若将来脚本改口径而字段忘了改，**这条会红**；
    反过来若只写 `assert value == 9.9471`，它就成了化石（第 134 层）。"""
    a = _nav()[arm]
    assert a["n_days"] > 0 and a["compounded_final_multiple"] > 0
    assert a["geometric_annual_pct"] == pytest.approx(
        _geom_annual_pct(a["compounded_final_multiple"], a["n_days"]), rel=1e-12)


def test_the_geometric_anchor_rejects_the_fraction_reading():
    """★ 反控：把同一个值当**分数**读，锚必须红。

    ⚠️ 只测正控的话，一条 `assert True` 的量纲守卫也能全绿。"""
    a = _nav()["equal_weight"]
    fraction_reading = a["geometric_annual_pct"] / 100.0
    assert fraction_reading != pytest.approx(
        _geom_annual_pct(a["compounded_final_multiple"], a["n_days"]), rel=1e-6), \
        "锚对 100 倍的量纲错不敏感 ⇒ 它不是量纲守卫"


# ── ② 两条基准臂的算术/几何关系是**定理**，不是拟合 ─────────────────────

@pytest.mark.parametrize("arm", ["equal_weight", "cap_weight"])
def test_arithmetic_annual_is_not_below_geometric(arm):
    """算术均值 ≥ 几何均值（石川书里写过两遍那条），是定理不是经验。

    ⚠️ 这一条**只管方向**，不管数值 —— 它在 §④ 里与阶数锚合起来才构成量纲守卫。"""
    a = _nav()[arm]
    arithmetic_annual = a["arithmetic_daily_mean_pct"] * 244.0
    assert arithmetic_annual > a["geometric_annual_pct"], (
        f"{arm}: 算术年化 {arithmetic_annual:.4f}% < 几何年化 "
        f"{a['geometric_annual_pct']:.4f}% ⇒ 必有一个字段的量纲错了")


@pytest.mark.parametrize("arm", ["equal_weight", "cap_weight"])
def test_every_pct_field_declares_its_scale(arm):
    """⚠️ **阶数锚，故意松**（3 倍以内）—— 它守的是**量纲**不是数值。

    为什么必须松：这条要能同时接受「百分数」与「日百分数 × 244」两种**合法**表述，
    只拒绝**数量级错**（100× 或 0.01×）。
    ⚠️ 数值由 ① 精确钉；这里若也钉死，两条守卫会**互相**把对方判死，
    而其中一条被改松时不会有任何东西发现（第 112 层：两份副本之间没有守卫）。"""
    a = _nav()[arm]
    arithmetic_annual = a["arithmetic_daily_mean_pct"] * 244.0
    geo = a["geometric_annual_pct"]
    ratio = arithmetic_annual / geo
    assert 1.0 <= ratio <= 3.0, (
        f"{arm}: 算术年化 / 几何年化 = {ratio:.4g} ⇒ 至少一个 *_pct 字段的量纲错了")


def test_the_scale_anchor_rejects_both_order_of_magnitude_errors():
    """★ 反控：**两个方向**都必须抓住，否则它是装饰。

    ⚠️ 只测一方向的话，一个恒返 `True` 的守卫能全绿；
    而「只测一方向」正是我写那份判决脚本时的实际状态。"""
    real = _nav()["equal_weight"]
    geo = real["geometric_annual_pct"]
    for bad_daily in (real["arithmetic_daily_mean_pct"] * 100.0,     # 百分数被当分数
                      real["arithmetic_daily_mean_pct"] / 100.0):    # 分数被当百分数
        ratio = bad_daily * 244.0 / geo
        assert not (1.0 <= ratio <= 3.0), f"量纲锚漏掉了 {bad_daily!r}"


# ── ③ ★ 判决本身：R 档必须由字段推得出来，且**不许被翻成 P** ──────────────

def _bench_geometric_pct() -> float:
    """G3-bis 的被诊断对象是**本策略那个等权 N=10 账户** ⇒ 基准取等权支。

    🚨 取 `cap_weight` 会是**换了被诊断的对象**（第 119 层）：
    那个支是市值加权基准，不是等权组合的基准。"""
    return _nav()["equal_weight"]["geometric_annual_pct"]


def _as_fraction(pct_value: float) -> float:
    """`*_pct` 字段 → 分数。**全仓唯一一处换算**（第 137 层：两份副本之间没有守卫）。"""
    return pct_value / 100.0


def _bias_is_tolerable(bias_pp: float, gap_pp: float, slack: float = 5.0) -> bool:
    """R 档对抽样噪声是否稳健。

    ⚠️ **这条的真实判别力来自下面的能力守卫，不来自真实数据** ——
    实测偏差 0.95pp/年 vs 间隙 12.51pp，把 `slack` 从 5 改成 0.05 或 50
    在真实数据上**都照样绿** ⇒ 只写「实测通过」的守卫对这条是恒真的，
    它只证明「余量真够」，不证明「余量被守住了」。"""
    return bias_pp < gap_pp / slack


def _gross_and_friction() -> tuple[float, float]:
    """毛超额与摩擦**只许取 `factor_width_mde::widths["10"]`**（预注册 G3-ter）。

    🚨 `cap_weighted_benchmark.json` 的三条臂组合权重 / 基准权重 / `min_basket`
    **三重都不同**，用它判本策略就是换对象。"""
    w10 = _wm()["widths"]["10"]
    return w10["gross_annual_pct"], w10["friction_width_aware_annual_pct"]


def test_the_verdict_is_r_and_not_p_under_the_pre_registered_geometry_caliber():
    """★ G3-bis / G3-quad 的判决，从字段重算。

    P 档要求「基准几何年化 ≥ 22.46%」；实测 9.9471% ⇒ **R 档**。
    ⚠️ 这里的 22.46 是**预注册的字面量，不许从数据推导** ——
    从数据推门槛就是「先看数据再写判据」（第 91 层）。
    ⇒ 所以守卫只做一件事：确认产物落在**预注册写死的那一支**。"""
    bench = _bench_geometric_pct()
    assert bench > 0.0, "G3：基准 ≤ 0 会落在第三支，本守卫的判决不适用"
    assert bench < P_GRADE_BENCH_PCT, (
        f"基准几何年化 {bench:.4f}% 已越过预注册门槛 {P_GRADE_BENCH_PCT}% ⇒ "
        "**判决翻到 P 档（结论翻面）** —— 这是「目标定义被修正」，"
        "**不是「找到了能赚钱的办法」**，两句话必须在文档里分开写")


def test_the_verdict_benchmark_is_the_equal_weight_arm_and_not_the_cap_weighted_one():
    """🚨 G3-ter 的禁令必须**可测**：判 P/R 的基准不许取市值加权支。

    ⚠️ 变异实测：把 `_bench_geometric_pct()` 改读 `cap_weight`，
    **18 条全绿** —— 因为 1.8087% 与 9.9471% **都在 22.46% 之下**，
    判决类别一模一样 ⇒ 「判决没变」把「换了被诊断的对象」完全藏住了。
    ⇒ 所以这条钉的**不是判决，是对象**（第 119 层：
    诊断量必须与被诊断对象的计算路径逐位对齐）。"""
    nav = _nav()
    assert _bench_geometric_pct() == nav["equal_weight"]["geometric_annual_pct"]
    assert _bench_geometric_pct() != nav["cap_weight"]["geometric_annual_pct"], \
        "两条基准在同窗口差 8.14pp/年；若它们相等，本守卫的禁令已无对象"


def test_the_net_caliber_account_value_is_still_below_target():
    """R 档的直接含义：`基准 + 毛超额 − 摩擦 < 20%`。

    ⚠️ 这一条用**算术毛超额**，而 P/R 的分档用**几何**（G3-quad）。
    两者不是同一个量，**不许混引**：算术那条只用来陈述「没达标」这个事实。"""
    gross, friction = _gross_and_friction()
    net = _as_fraction(_bench_geometric_pct()) + gross - friction
    assert net < TARGET_NET, f"净值口径 {net*100:.4f}% 已越过目标 ⇒ 本轮是 P 档不是 R 档"


def test_the_relaxed_hurdle_still_fails_every_width():
    """G3 `> 0` 支规定的动作：**五档毛超额须按新门槛重判**。

    新门槛 = `目标 − 基准 + 摩擦`。五档**一档都不许翻** ——
    这正是 R 档的定义（门槛松了、判决没翻）。"""
    bench = _as_fraction(_bench_geometric_pct())
    _, friction = _gross_and_friction()
    new_hurdle = TARGET_NET - bench + friction
    old_hurdle = TARGET_NET + friction
    assert new_hurdle < old_hurdle, "基准 > 0 时新门槛必须低于旧门槛"
    for key, row in _wm()["widths"].items():
        gross = row["gross_annual_pct"]
        assert gross < new_hurdle, (
            f"N={key} 毛超额 {gross*100:.2f}% ≥ 净值口径门槛 {new_hurdle*100:.2f}% "
            "⇒ 判决类别被翻转，R 档的结论不成立")


def test_no_width_satisfies_the_net_caliber_target_at_any_capital():
    """「没有任何本金能达标」这句结论**在净值口径下同样成立**。

    ⚠️ 只在新门槛下判毛超额是不够的：那句话说的是**本金**，
    而本金只影响摩擦、影响不了毛超额 ⇒ 用 `friction_floor` 下界就够封口。"""
    bench = _as_fraction(_bench_geometric_pct())
    wcf = json.loads((ROOT / "data" / "reports" / "width_capital_feasibility.json")
                     .read_text(encoding="utf-8"))
    floor = max(r["friction_floor"] for r in wcf["widths"])
    best = max(r["gross_annual_pct"] for r in _wm()["widths"].values())
    assert best + bench - floor < TARGET_NET, (
        f"最好一档 {best*100:.2f}% + 基准 {bench*100:.2f}% − 摩擦下界 {floor*100:.2f}% "
        f"= {(best+bench-floor)*100:.2f}% ≥ 目标 ⇒ 「没有本金能达标」这句要改")


# ── ④ 头条基准的**池口径**（§7.1 的债条） ───────────────────────────────

def test_the_headline_benchmark_declares_its_pool():
    """🚨 报出来的 9.9471% 跑在 **150 只抽样**上，全池只算了日均交叉核对。

    ⇒ 所以它**必须带抽样标签**；
    并且偏差量级必须**远小于**判决间隙（12.51pp），否则 R 档的结论会被抽样噪声推翻。"""
    nav = _nav()
    sample, pool = nav["sample"]["size"], nav["sample"]["pool"]
    assert sample < pool, "抽样池 == 全池 ⇒ §7.1 的债条过期，请改判据"
    bias_pp_per_year = abs(
        nav["full_pool_daily_mean_pct"] - nav["equal_weight"]["arithmetic_daily_mean_pct"]) * 244
    gap_pp = P_GRADE_BENCH_PCT - _bench_geometric_pct()
    assert _bias_is_tolerable(bias_pp_per_year, gap_pp), (
        f"抽样偏差 {bias_pp_per_year:.2f}pp/年 已达判决间隙 {gap_pp:.2f}pp 的 1/5 "
        "⇒ R 档对抽样噪声不再稳健，必须先全池重跑再引用")


def test_the_bias_direction_is_not_claimed_as_systematic():
    """⚠️ 本种子下抽样比全池高，**但那不是「抽样必然高估」**。

    守卫钉的是**文档不许把一个种子的结果说成方向性结论** ——
    因为那是第 7 种形状（两个各自都对的数放一起比）的散文版。"""
    doc = PREREG.read_text(encoding="utf-8")
    assert "抽样噪声" in doc and "不是「抽样必然高估」" in doc


# ── ④.1 两条「在真实数据上恒绿」的判据 ⇒ 判别力必须由能力守卫提供 ──────

def test_the_scale_conversion_is_the_only_place_that_divides_by_a_hundred():
    """★ 能力守卫（正向）：`_as_fraction` 确实**双向**都有牙。

    ⚠️ 若它恒真，上面所有按分数算的判决都在裸奔 ——
    而它们的结论方向恰好都是 R 档 ⇒ **恒真在此处不会被发现**。"""
    assert _as_fraction(9.9471) == pytest.approx(0.099471)
    assert _as_fraction(9.9471) != 9.9471
    # 反向：把分数当百分数（×100 而不是 ÷100）必须改变结果
    assert _as_fraction(0.099471) == pytest.approx(0.00099471)


def test_the_bias_tolerance_rejects_both_directions():
    """★ 能力守卫：`slack` 在**真实数据上不可判别**（实测余量 13 倍 ⇒ 改任何倍数都绿），
    所以它的牙必须由合成数据给。

    🚨 这是变异台给的实测：把 `gap_pp / 5.0` 放宽成 `gap_pp * 5.0` ⇒ **存活**。
    ⇒ 「实测通过」这条证据对这条守卫**完全没有信息量**。"""
    gap = 12.51
    assert _bias_is_tolerable(0.95, gap) is True          # 真实数据那一格
    assert _bias_is_tolerable(gap / 2.0, gap) is False     # 偏差吃掉一半间隙
    assert _bias_is_tolerable(gap * 2.0, gap) is False     # 偏差远超间隙
    assert _bias_is_tolerable(0.95, gap, slack=1e9) is False, \
        "把 slack 放大到无穷 ⇒ 变恒真 ⇒ 本守卫失去全部判别力"


# ── ⑤ 预注册的自律：文档不许把 R 读成 P ───────────────────────────────

def test_the_prereg_records_r_and_separates_it_from_being_reached():
    """★ 措辞守卫（第 16 种形状的纪律）：**门槛松了**不许写成**达标了**。"""
    doc = PREREG.read_text(encoding="utf-8")
    assert "R 档" in doc and "判决类别被翻转的档：无" in doc
    assert "门槛松弛 ≠ 达标" in doc, "文档缺「门槛降低 = 目标降低」这句 ⇒ 第 129 层"


def test_the_pre_registered_threshold_was_not_edited_after_the_data_arrived():
    """⚠️ G3-quad 的 22.46% 必须**在出数前**写死。

    判据锚在文档原文那一行的**固定措辞**上；出数后若有人把它改成「刚好在门槛之下」，
    这条会红 ⇒ 第 91 层（先有码后补文档）在这份文档上是**可测的**。"""
    doc = PREREG.read_text(encoding="utf-8")
    assert "★ **P 档门槛（几何口径）：基准几何年化 ≥ 22.46%**" in doc
    assert "若 G1 出数后门槛需要改动：**杀掉进程、先改这一节、再重判**" in doc


# ── ⑥ 产物必须登记并盖章（第 127 层：`unknown` = 不许引用） ──────────────

def test_the_artifact_is_registered_and_stamped():
    tree = ast.parse(PROVENANCE.read_text(encoding="utf-8"))
    reg = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if isinstance(k, ast.Constant) and k.value == NAV.name:
                    if isinstance(v, ast.Constant):
                        reg = v.value
    assert reg == "scripts/market_nav_caliber.py", reg
    assert NAV.with_suffix(".json.provenance.json").exists(), \
        "登记了却没盖章 ⇒ freshness 报 unknown ⇒ 按本仓规矩**不许引用**"


def test_the_artifact_states_its_calibers_and_what_it_cannot_prove():
    """G2：口径标签**必填** —— 几何/算术、等权/市值，两条都要报。"""
    nav = _nav()
    for arm, caliber in (("equal_weight", "equal_weight_benchmark"),
                         ("cap_weight", "cap_weight_benchmark")):
        assert nav[arm]["caliber"] == caliber
    assert nav["what_this_cannot_prove"], "产物不许把自己说得比实际强"
    assert nav["gates"]["no_missing_trading_day"] is True, \
        "G1 要求窗口内**每一个交易日**都有基准收益，缺日就不许出这个数"

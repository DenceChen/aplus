r"""`scripts/deployed_account_nav.py` 的守卫。

## 这个文件在守什么

判决档 `basket_nav_01_*.json` 的 `c=+10.5563%` 量的是**满仓**账户，
而 `run_daily.py:647` 只传 `capital` ⇒ 生产敞口是
`build_allocation(max_positions=5, per_stock_cap=0.10)` 的默认 **0.5**。
⇒ 判决档那个数**不是用户账户会拿到的数**。本脚本补的是这一列。

## 五条锚的判别力来源**互不相同**

同源的两条守卫就是一条的复本（第 112 层：过严/恒真）。这里刻意让每条
用不同的失效通道：

| 锚 | 判别力来自 | 它抓的失效 |
|---|---|---|
| A1 敞口来自签名 | **AST 语法位置**：源码里不许有 `0.5` 字面量 | 手打 W |
| A2 成本腿真的在算 | **AST 语法位置**：函数体调 `daily_cost_of_the_leg` | 换成 `W*account_nav` |
| A3 尺子真的在算 | 行数 + 逐位还原 | 合成表 `legs_per_day` 掉到 `MIN_BASKET` 以下 |
| A4 复利非线性 | **合成夹具**（构造含负收益的序列） | CAGR 被写成 `W × 满仓 CAGR` |
| A5 两把尺子并排 | 产物同时含两块且 `R` 不等 | 删掉 252 那块 / 静默二选一 |

## 🚨 A4 绝不能依赖本仓真实产物

真实产物「碰巧偏离了 W」对判别力**零信息量** ——
第 146 层实测：靠随机数据巧合成立的守卫，38 条全绿而变异存活。
⇒ A4 用**自己造的**含负收益序列，它必须先证明「复利非线性在本仓成立」
（能力守卫），再钉住生产没把它线性化。

## 🚨 A1/A2 不用子串

`"0.5"` 与 `daily_cost_of_the_leg` 都会在本文件的 **docstring 与注释**里
出现（第 13 种形状：子串断言 + 文档 = 守卫可被文档满足）。
⇒ 一律走 **AST**，且断言落在**实参与赋值右端**，不落在文本。
"""
from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "scripts/deployed_account_nav.py"
OUT = ROOT / "data/reports/deployed_account_nav.json"

from scripts.deployed_account_nav import (  # noqa: E402
    LEG,
    SYNTHETIC_LEGS_PER_DAY,
    ci_high_console_line,
    ci_high_label,
    ci_high_verdict,
    daily_cost_of_the_leg,
    deployed_exposure,
    effect_size_label,
    gap_over_mde_note,
    gap_size_label,
    half_exposure_daily,
    not_a_verdict_note,
    single_arm_effect_phrase,
    synthetic_leg_history,
)


@pytest.fixture(scope="module")
def artifact() -> dict:
    if not OUT.exists():
        pytest.skip("产物未生成；先跑 scripts/deployed_account_nav.py")
    return json.loads(OUT.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def daily():
    import pandas as pd
    return pd.read_parquet(ROOT / "data/reports/wf_weights_backtest_k21.daily.parquet")


# ═══════════════════════════════════════════════════════════════
# A1 敞口来自生产签名，不是手打
# ═══════════════════════════════════════════════════════════════
def test_A1_exposure_really_comes_from_the_signature() -> None:
    """能力守卫：`deployed_exposure()` 的每个字段都能对回签名的实际默认值。

    这条**是**自指的（函数自己读签名）⇒ 所以它证明的是「接线通了」，
    杀「手打」的是下面的 **A1b（AST）**。两条缺一不可。
    """
    from aplus.alloc import build_allocation
    params = inspect.signature(build_allocation).parameters
    expo = deployed_exposure()
    assert expo["max_positions"] == params["max_positions"].default
    assert expo["per_stock_cap"] == params["per_stock_cap"].default
    assert expo["equity_weight"] == pytest.approx(
        params["max_positions"].default * params["per_stock_cap"].default)


def test_A1b_the_source_contains_no_hand_typed_exposure_weight() -> None:
    """🚨 AST 守卫：`0.5` **不许作为字面量**出现在本脚本源码里。

    为什么用 AST 而不是 `"0.5" not in src`：这个文件的 docstring 与注释
    **通篇在讲 0.5**（推导、实测、坑）⇒ 子串断言会被自己的文档满足，
    而它要守的正是「有人在计算里手打了 0.5」（第 13 种形状）。
    """
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    offenders = [n.lineno for n in ast.walk(tree)
                 if isinstance(n, ast.Constant)
                 and isinstance(n.value, (int, float))
                 and not isinstance(n.value, bool)
                 and float(n.value) == 0.5]
    assert not offenders, (
        f"源码第 {offenders} 行出现字面量 0.5 —— 敞口必须来自 "
        f"build_allocation 的默认参数。手打它会在默认值被改后仍然绿。")


def test_A1c_the_caller_really_does_pass_only_capital() -> None:
    """钉住「走默认值」这个前提（`deployed_exposure` 的整个读法靠它）。

    AST 锚在 `scripts/run_daily.py` 里 `build_allocation(...)` 那个调用的
    **实参表**上 —— 若哪天生产改成显式传 `max_positions=10`，
    `inspect.signature` 读到的默认值就不再是生产值，而上面两条守卫照样绿。
    """
    tree = ast.parse((ROOT / "scripts/run_daily.py").read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name)
             and n.func.id == "build_allocation"]
    assert calls, "run_daily.py 里没有 build_allocation 调用 —— 前提失效，先查生产"
    for call in calls:
        kwargs = {kw.arg for kw in call.keywords}
        assert "max_positions" not in kwargs, (
            "生产显式传了 max_positions ⇒ deployed_exposure() 读签名是错的敞口")
        assert "per_stock_cap" not in kwargs, (
            "生产显式传了 per_stock_cap ⇒ deployed_exposure() 读签名是错的敞口")


# ═══════════════════════════════════════════════════════════════
# A2 半仓走的是「毛 − 该腿逐日成本」，不是随手乘个 W
# ═══════════════════════════════════════════════════════════════
def test_A2_half_exposure_goes_through_the_cost_leg() -> None:
    """AST：函数体里必须有 `daily_cost_of_the_leg` 的**调用**。

    为什么这条必须存在：`r_half = W×(mkt+gr) − W×(gross−net)` 与
    `W×(mkt+net)` **数学恒等**（实测最大逐位差 8.67e-19）
    ⇒ 任何数值断言都区分不开，只有「语法位置」区分得开。

    ⚠️ 而这正是它的价值所在：恒等意味着**成本腿在不在模型里
    在数值上不可见**，只有代码形状能证明它没有被摘掉。
    """
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef)
              and n.name == "half_exposure_daily")
    called = {n.func.id for n in ast.walk(fn)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "daily_cost_of_the_leg" in called, (
        "half_exposure_daily 不再经过 daily_cost_of_the_leg —— "
        "成本腿从模型里消失了，而它与 W×account_nav 恒等 ⇒ 数值守卫抓不到")


def test_A2b_the_daily_cost_is_the_parquet_own_column_difference(daily) -> None:
    """外部锚：成本 = 产物**自身两列之差**，不是本脚本重算的公式。

    `gross_excess − net_excess_bt003` 是生成器 `at_cost` 写下的那条差
    ⇒ 核对的是「parquet 自己说了什么」，不是「我们认为它该是多少」
    （第 109 层：核对产物只读字段 / 禁止用公式重算「验证」）。
    """
    cost = daily_cost_of_the_leg(daily)
    expected = (daily["gross_excess"].to_numpy(float)
                - daily[LEG].to_numpy(float))
    assert np.array_equal(cost, expected)
    # 反向正控：它确实**不是**零（否则上面那条恒真）
    assert float(np.abs(cost).max()) > 0.0
    # 且它必然非负 —— 净超额不可能高于毛超额
    assert float(cost.min()) >= 0.0


def test_A2c_half_series_matches_W_times_full_to_float_noise(daily) -> None:
    """钉住「route A 塌成 `W × r_full`」这个**已实测**的性质。

    🚨 **这是打印出来的性质，不是判据**：实测最大差 8.67e-19，
    所以断言只能写成「差 ≪ 任何有经济意义的量」，不能写 `== 0`
    （两条不同的浮点运算路径 ⇒ `np.array_equal` 为假；第 178 层：
    没定阈值的「一致」等于没有判据）。
    """
    from scripts.measure_basket_nav_01 import account_nav
    expo = deployed_exposure()
    W = expo["equity_weight"]
    r_half = half_exposure_daily(daily, W)
    naive = account_nav(daily, LEG) * W
    assert len(r_half) == len(naive)
    assert float(np.abs(r_half - naive).max()) < 1e-15, (
        "route A 不再塌成 W×r_full ⇒ 摩擦路线被换掉了，"
        "而那正是文档里「两个模型接缝」的来源")


# ═══════════════════════════════════════════════════════════════
# A3 那把 252 尺子真的在算（合成逐笔表能精确还原日序列）
# ═══════════════════════════════════════════════════════════════
def test_A3_synthetic_ledger_exactly_restores_the_daily_series(daily) -> None:
    """外部锚：还原后**行数相同**且**数值相等** ⇒ 252 那把尺子吃的是真序列。

    🚨 行数这一半是承重的：`daily_portfolio_returns` 会把
    `size < MIN_BASKET(=3)` 的日子**整组丢掉**，而那是**静默**的 ——
    尺子读到更短的样本，照样出数、照样没有一行报错。
    """
    from aplus.verify import daily_portfolio_returns
    expo = deployed_exposure()
    r = half_exposure_daily(daily, expo["equity_weight"])
    hist = synthetic_leg_history(r, daily.index, SYNTHETIC_LEGS_PER_DAY)
    restored = daily_portfolio_returns(hist).to_numpy(float)
    assert len(restored) == len(r), (
        f"还原后 {len(restored)} 行 ≠ 源 {len(r)} 行 ⇒ "
        f"某天被 MIN_BASKET 整组丢掉，尺子读的是更短的样本")
    assert np.allclose(restored, r, rtol=0, atol=1e-15)
    assert not np.array_equal(restored, r), (
        "逐位相等会让这条守卫对『换了一条浮点路径』失明 —— "
        "实测差是 1.735e-18，不是 0")


def test_A3b_legs_per_day_is_above_MIN_BASKET() -> None:
    """钉住合成表每天的行数**必须过 `MIN_BASKET`**，且**是 10 不是 3**。

    10 对应「10 只等权」这个真实交付形态；3 也能过闸门但会把「10 只」
    这个事实藏起来 ⇒ 只能取 10。
    """
    from aplus.verify import MIN_BASKET
    assert SYNTHETIC_LEGS_PER_DAY >= MIN_BASKET
    assert SYNTHETIC_LEGS_PER_DAY == 10


# ═══════════════════════════════════════════════════════════════
# A4 复利非线性 —— **判别力来自合成夹具，不来自本仓产物**
# ═══════════════════════════════════════════════════════════════
def test_A4_capability_compounding_really_is_nonlinear_on_a_synthetic_series() -> None:
    """能力守卫：先证明「CAGR(W·r) ≠ W·CAGR(r)」在本仓成立。

    ⚠️ 这条**不依赖任何产物**。若它哪天红了，那是本仓的 `tier()` 变了，
    而那比产物过期更严重（尺子变了 ⇒ 所有历史读数的口径都变了）。
    """
    from scripts.measure_basket_nav_01 import tier
    r = np.array([0.02, -0.01, 0.03, -0.015, 0.01] * 40)   # 含负收益
    W = 0.5
    c_full = tier("t", r)["cagr"]
    c_half = tier("t", r * W)["cagr"]
    assert c_full is not None and c_half is not None
    assert c_half / c_full != pytest.approx(W, abs=1e-3), (
        "本仓的 CAGR 在合成序列上变成线性的了 ⇒ tier() 口径变了，"
        "所有『半仓不是满仓一半』的结论都要重读")


def test_A4b_the_artifact_reports_the_nonlinearity_and_does_not_linearize_it(
        artifact: dict) -> None:
    """产物层：`cagr_ratio_half_over_full` 必须**实测偏离 W**，且偏离不可忽略。

    🚨 判别力来源是 A4 的合成夹具 + 这里的偏离量级；
    若有人把 `half_t["cagr"]` 写成 `W * full_t["cagr"]`，
    这个比值会**恰好**是 0.5 ⇒ 偏离为 0 ⇒ 红。
    """
    block = artifact["why_half_is_not_simply_half"]
    nl = block["compounding_is_the_real_nonlinearity"]
    ratio = nl["cagr_ratio_half_over_full"]
    W = nl["W"]
    assert ratio != pytest.approx(W, abs=1e-3), (
        f"cagr 比 {ratio} 恰等于 W={W} ⇒ 半仓 CAGR 被线性化了。")


def test_A4c_the_artifact_prints_the_float_gap_instead_of_asserting_zero(
        artifact: dict) -> None:
    """钉住「差是打印出来的字段，不是判据」这条纪律本身。"""
    block = artifact["why_half_is_not_simply_half"]
    gap = block["measured_max_abs_diff_vs_W_times_full"]
    assert gap is not None and gap > 0.0, (
        "max|diff| 必须是一个**正的**浮点差（实测 8.67e-19）。"
        "它是两条运算路径的接缝证据，写成 0 会让读者以为两条路径恒等。")


# ═══════════════════════════════════════════════════════════════
# A5 两把尺子并排印出，且它们的 R 确实不同
# ═══════════════════════════════════════════════════════════════
def test_A5_both_rulers_are_printed_with_their_own_R(artifact: dict) -> None:
    """🚨 不许静默二选一：`compounded_stats` 用 252，判决档用 244。

    两条常数**都不许改**（改 = 判据臂，第 91 层）⇒ 处置是并排印出。
    """
    from aplus.hurdle import DAYS_PER_YEAR
    from aplus.verify import TRADING_DAYS_PER_YEAR
    two = artifact["two_rulers"]
    j = two["ruler_244_judgement_arm"]
    u = two["ruler_252_named_unique"]
    assert j["R"] == DAYS_PER_YEAR == 244
    assert u["R"] == TRADING_DAYS_PER_YEAR == 252
    assert j["R"] != u["R"], "两个年数常数相等 ⇒ 「唯一尺子」的说法此时成立，产物必须改口"
    # 两块都必须带**读数**，不许只印口径
    for block in (j, u):
        assert block["full"]["cagr"] is not None
        assert block["half"]["cagr"] is not None


def test_A5b_the_caliber_conflict_is_measured_not_asserted(artifact: dict) -> None:
    """钉住「冲突必须是被测出来的数」而不是一句手打的差。"""
    c = artifact["two_rulers"]["caliber_conflict_measured"]
    assert c["years_252"] != c["years_244"]
    assert c["cagr_ratio_252_over_244"] is not None
    assert c["cagr_ratio_252_over_244"] > 1.0
    # 244 年数更长 ⇒ 252 尺子年化更高。这是从两个常数的方向推出的，
    # 不是拟合出来的。
    assert c["years_244"] > c["years_252"]
    assert c["cagr_252"] > c["cagr_244"]


# ═══════════════════════════════════════════════════════════════
# 角色纪律：它是推算，不是判决
# ═══════════════════════════════════════════════════════════════
def test_the_gap_note_separates_the_two_ratios(artifact: dict) -> None:
    """🚨 钉住「效应量比」与「缺口比」不许合并读（第 7 层 / 第 142 层）。

    它们的**失效方向相反**：只有缺口比 > 1 时，「缺口超过测量误差」成立，
    而**同一句话里若顺手说「连符号都定不了」就把两个量混了** ——
    「c 是否异于 0」和「c 是否低于 20%」是**两个问题**。

    ⚠️ 判别力来自**方向不同的构造**，不是来自本仓真实产物
    （第 146 层：靠数据巧合的守卫对「判断写反了」零信息量）。
    """
    note = artifact["gap_to_target"]["🚨_gap_over_mde_note"]
    assert "两个比值问两个不同的问题" in note
    assert "|c|/M" in note and "|T−c|/M" in note
    assert "不是同一个量" in note


def test_the_gap_note_direction_is_computed_from_the_numbers_it_reports(
        artifact: dict) -> None:
    """🚨 这条才有牙：把 note 里的方向词与 note 里引用的那两个比值**对上**。

    上一条（缺方向词、合并两个比值）只钉「提到两个比值」，
    另一条钉「不出现手打串」—— 但脚本用的词是 `"两档都大于"`（**不带 1**），
    守卫查的是 `"两档都大于 1"` ⇒ 把它换成字面量 `note` 里就出现了手打方向词，
    而守卫**照样绿**（第 112 层：看着在守，实则不区分）。

    ⇒ 正解是**数值对账**：从 `gap_over_mde_*` / `effect_over_mde_*` 算出
    该出现哪个方向词，断言 note 里出现的是**它**。
    ⚠️ 判别力只来自「note 自己的数 vs note 自己的方向词」——
    **不依赖本仓真实产物**（第 146 层：靠数据巧合的守卫对「判断写反」零信息量）。
    """
    g = artifact["gap_to_target"]
    note = g["🚨_gap_over_mde_note"]
    gaps = (g["gap_over_mde_full"], g["gap_over_mde_half"])
    effs = (g["effect_over_mde_full"], g["effect_over_mde_half"])

    # ① 方向词必须是从数算出来的那个
    if min(gaps) > 1:
        want = "两档都大于"
    elif max(gaps) < 1:
        want = "两档都小于"
    else:
        want = "两档分居 1 的两侧"
    assert want in note, (
        f"缺口比 {gaps} 应当得出方向词 {want!r}，而 note 里没有 ⇒ "
        f"方向与数脱钩（第 142 层）。note = {note!r}")

    # ② 反向正控：**另外两个词都不许出现**（否则上面那条会被「三个词都写」骗过）
    for wrong in {"两档都大于", "两档都小于", "两档分居 1 的两侧"} - {want}:
        assert wrong not in note, (
            f"note 里同时出现了 {wrong!r} 与 {want!r} ⇒ 方向词不是唯一的，"
            f"读者无法判断哪句对应数。")

    # ③ 效应量比那一半同理
    if max(effs) > 1:
        want_e = "测得出符号"
    else:
        want_e = "连正负都定不了"
    assert want_e in note, (
        f"效应量比 {effs} 应当得出 {want_e!r}，而 note 里没有。")


def test_the_gap_note_reports_both_ratios_it_is_verdict_about(
        artifact: dict) -> None:
    """🚨 数字对账：note 里印的两个比值必须与产物字段**逐位相符**。

    为什么要这条：note 是给人读的那一份，字段是机器对账的那一份。
    若哪天有人在 note 里手抄比值，两边就会**同时绿**——
    守卫查的串只在 note 里，字段各查各的。
    """
    g = artifact["gap_to_target"]
    note = g["🚨_gap_over_mde_note"]
    for v in (g["effect_over_mde_full"], g["effect_over_mde_half"],
              g["gap_over_mde_full"], g["gap_over_mde_half"]):
        assert f"{v:.4f}" in note, (
            f"比值 {v:.4f} 不在 note 里 ⇒ note 印的是手抄件，"
            f"而它与字段没有任何通路（第 137 层）。")


def test_the_two_ratios_are_not_merged_anywhere_in_the_note(
        artifact: dict) -> None:
    """🚨 反向正控：句子必须**分开**陈述，而不只是提到两个比值。

    为什么这条不能省：一条守卫钉住「提到两个比值」，另一条只钉住
    「不出现手打判断」—— 若哪天有人写成一句「两档 |c|/M 与缺口比都小于 1，
    所以测不到」，两条**同时绿**，而那正是它们各自要防的错。
    ⇒ 这条钉「每个比值后面必须跟着它问的那个问题」。
    """
    note = artifact["gap_to_target"]["🚨_gap_over_mde_note"]
    assert "c 是否异于 0" in note
    assert "c 是否低于 20%" in note


# ═══════════════════════════════════════════════════════════════
# 🚨🚨 第 96 层的正面修法：**守在真的在跑的那一层**
# ═══════════════════════════════════════════════════════════════
# 上面五条守卫**全部只读落盘产物**。2026-10-16 的实测：变异台 N1/N2 打的是
# `scripts/deployed_account_nav.py` 的源码，而这五条一个字都不会红 ——
# `rc=0 / 20 passed`。根因：**产物没重跑**（第 96 层「读快照的守卫等于没守」）。
#
# ⇒ 修法不是给这五条再加严，而是**让定性词成为模块级函数**并直接调它，
#   喂**合成数**，每条分支各钉一次：
#   ① 分岔夹具（第 146 层：判别力不能来自本仓真实产物的巧合）
#   ② AST 接线守卫（第 13 层：子串能被注释/docstring 满足 ⇒ 锚在 Call 节点上）
#
# ⚠️ 两条**各守一层，不可互换**：
#   ① 守「函数算得对不对」，② 守「调用点是不是真的调了它」。
#   只有 ① ⇒ 把调用点删掉、改成手打常量，函数本身照样对；只有 ② ⇒ 函数算错了没人知道。

def test_gap_size_label_has_all_three_branches() -> None:
    """🚨 `gap_size_label` **必须三条分支**，且实测形态是第三条。

    为什么这条不能省成两条：实测形态**就是「分居 1 的两侧」**
    （满仓 0.42 < 1、半仓 1.22 > 1），而只有两条分支的写法会把它
    折进其中一边 —— 那正是第 142 层「定性词与它印的数打脸」的原形。
    """
    assert gap_size_label(1.2, 1.1) == "两档都大于"
    assert gap_size_label(0.42, 0.30) == "两档都小于"
    assert gap_size_label(0.42, 1.22) == "两档分居 1 的两侧"
    # 🚨 **恰好落在 1 上时**归第三支（`> 1` 与 `< 1` 都是严格不等号）。
    # 若哪天有人把某一条改成 `>=`，实测形态不变而这条会红 —— 那是它有牙。
    assert gap_size_label(1.0, 0.5) == "两档分居 1 的两侧"


def test_effect_size_label_answers_a_different_question() -> None:
    """🚨 效应量比问的是「**c 是否异于 0**」，**目标值不参与**这条判断。

    反向正控：喂一对**缺口比会分居两侧、效应量比两档都 <1** 的数，
    两个函数必须给出**不同**的词 —— 这就是第 7 种形状的可测形态。
    """
    assert effect_size_label(1.5, 0.2) == "测得出符号"
    assert effect_size_label(0.47, 0.54) == "两档都测不出符号，连正负都定不了"
    assert effect_size_label(0.47, 0.54) != gap_size_label(0.47, 0.54)


def test_ci_high_verdict_flips_only_across_the_target() -> None:
    """🚨 CI 上端越线 ⇒ **不许说达不到**；不越线 ⇒ 可以说。

    ⚠️ 「也不许说接近」这个限定词**只挂在越线那一支**：不越线时说
    「不许说接近」是错的（那就是「没说接近」）。
    ⇒ 两条断言一起把这一支钉住，单钉一条会漏。
    """
    over = ci_high_verdict(0.3323, 0.20)
    assert "不许说达不到" in over
    assert "也不许说接近" in over
    under = ci_high_verdict(0.15, 0.20)
    assert "可以说达不到" in under
    assert "不许说接近" not in under
    # 🚨 恰好等于目标**不算越线**（`>` 是严格不等号）。
    assert "可以说达不到" in ci_high_verdict(0.20, 0.20)


def test_ci_high_label_and_verdict_agree_on_the_same_question() -> None:
    """🚨 「高于/不高于」与「不许说/可以说」必须**同步**翻转。

    这两个函数答的是**同一个问题**（CI 上端与目标的关系），
    分居在两个函数里只有当它们被分别变异时才看得见 ⇒ 这条是对拍。
    """
    for ci_hi in (0.05, 0.19, 0.20, 0.21, 0.3323, 0.90):
        label = ci_high_label(ci_hi, 0.20)
        verdict = ci_high_verdict(ci_hi, 0.20)
        assert (label == "高于") == ("不许说达不到" in verdict), (
            f"ci_hi={ci_hi}: label={label!r} 与 verdict={verdict!r} 方向相反 "
            f"⇒ 两个函数各自被改过一处（第 142 层）。")


def test_ci_high_verdict_has_no_default_target() -> None:
    """🚨 `target` **没有默认值**（第 182 层）。

    给了默认值，「哪个是判决臂、哪个是敏感性臂」的区分会被某次调用
    悄悄抹掉 —— 而那个区别**看不出来**，因为参数照样绑上了。
    ⇒ 少传参数必须 `TypeError`，不是悄悄用默认。
    """
    with pytest.raises(TypeError):
        ci_high_verdict(0.33)          # type: ignore[call-arg]
    with pytest.raises(TypeError):
        ci_high_label(0.33)            # type: ignore[call-arg]
    with pytest.raises(TypeError):
        ci_high_console_line(0.33)     # type: ignore[call-arg]


def test_single_arm_effect_phrase_computes_the_comparison_sign() -> None:
    """🚨 连**比较号**（`<` / `≥`）也是算出来的，不是手打的。

    原型是 f-string 内联的两个三元 ⇒ 与上面两个标签同一种病：多一份能打偏的副本。
    """
    assert single_arm_effect_phrase(0.47).startswith("< 1")
    assert "连符号都定不了" in single_arm_effect_phrase(0.47)
    assert single_arm_effect_phrase(1.5).startswith("≥ 1")
    assert "连符号都定不了" not in single_arm_effect_phrase(1.5)


def test_the_note_and_console_render_identical_qualitative_words() -> None:
    """🚨 反向正控：产物与 CLI **共用同一份定性**，不许各自手打。

    这一条同时钉住「CLI 那份内联三元」这个真实存在的失效形态：
    重构前 CLI 有自己的一份方向词，改它时产物层守卫全绿。
    """
    eff_full, eff_half = 0.47, 0.54
    gapm_full, gapm_half = 0.42, 1.22
    ci_hi, target = 0.3323, 0.20

    note = gap_over_mde_note(eff_full, eff_half, gapm_full, gapm_half, ci_hi, target)
    console = ci_high_console_line(ci_hi, target)

    for word in (effect_size_label(eff_full, eff_half),
                 gap_size_label(gapm_full, gapm_half),
                 ci_high_label(ci_hi, target),
                 ci_high_verdict(ci_hi, target)):
        assert word in note, f"产物 note 缺定性词 {word!r} ⇒ 它手打了一份副本。"

    # ⚠️ 只有 ③ 那一问在这一行里。①② 的 CLI 文案在 `main()` 的另外两行 print，
    #   而那两行由 `test_main_prints_through_the_same_label_functions`（AST 接线）
    #   守着 ⇒ **不许在这里要求 ①② 出现在 `ci_high_console_line` 里**：
    #   那是过严的守卫（第 112 层），它会逼着生产去往一行里塞不相关的内容。
    assert ci_high_verdict(ci_hi, target) in console, (
        f"CLI 行缺 {ci_high_verdict(ci_hi, target)!r} ⇒ 它手打了一份副本。")


def test_the_note_still_carries_the_numbers_it_is_about() -> None:
    """🚨 抽成函数**不许**把数字一起丢掉（函数化最容易犯的错）。

    `gap_over_mde_note` 现在是纯函数 + 调用点 ⇒ 守卫从「读产物里的 note」
    变成「调函数」；若哪天函数只返回定性词而不印比值，
    上面几条**照样绿** ⇒ 必须有一条把数字钉回来。
    """
    # ⚠️ `ci_hi` 在 note 里按 `.4%` 印（33.2300%），CLI 按 `.2%` 印（33.23%）
    #   —— 两个精度都是各自的既有口径，**不许**为了让断言好看而统一它们。
    note = gap_over_mde_note(0.4712, 0.5401, 0.4211, 1.2205, 0.3323, 0.20)
    for v in ("0.4712", "0.5401", "0.4211", "1.2205", "33.2300%", "20%"):
        assert v in note, f"note 缺 {v!r} ⇒ 函数化时把数字丢了。"


def test_the_note_refuses_to_merge_the_two_ratios_at_the_source_layer() -> None:
    """🚨 「① 与 ② 不是同一个量」这句话此前**只有产物层在守**（N3 变异存活）。

    `test_the_gap_note_separates_the_two_ratios` 的 `note` 来自 `artifact`
    fixture —— 即落盘的 `data/reports/deployed_account_nav.json`，
    **不是**活的函数调用 ⇒ 把源码里这句话改掉，它**照样绿**。
    这就是第 96/118 层：**产物层守卫对当前代码零覆盖**，
    而它与「守对了」在终端上长得一模一样。

    ⇒ 变异 `N3`（把该句改成「是同一个量的两种写法」）实测 **rc=0 / 34 passed**，
    存活方向恰好是本仓最贵的那一侧（会被读成「这条纪律没人守」）。
    ⇒ 修法是**补源码层**，不是放松产物层，也不是换掉那条变异。

    ⚠️ 变异体**只改第一句**，紧邻的「用 ② 的结论去回答 ① 的问题是第 7 种形状」
    原样保留 ⇒ 变异后的 note 是**自相矛盾**的（说两个是同一个量，紧接着说合并读就是
    第 7 种形状）。所以本守卫只钉第一句的存在即可杀掉它；
    不去断言第二句——那对这条变异零判别力。
    """
    note = gap_over_mde_note(0.4712, 0.5401, 0.4211, 1.2205, 0.3323, 0.20)
    assert "① 与 ② 不是同一个量" in note, (
        "note 不再声明两个比值是两个问题 ⇒ 它允许读者把它们合并成一个量。"
        "（产物层那条对源码变异不可见，第 96 层）")


def test_no_hand_typed_qualitative_word_survives_in_any_f_string() -> None:
    """🚨 AST 守卫：**全文件**任何 f-string 的常量部分都不许再出现定性词。

    为什么必须用 AST 而不是 `"两档都大于" not in src`：
    这个文件的注释与 docstring **通篇在讲这些词**（推导、实测、坑）⇒
    子串断言会被自己的文档满足（第 13 层，A1b 那条守 `0.5` 的同款理由）。
    ⇒ 只查 `ast.JoinedStr` 里的 `ast.Constant` ⇒ 注释与 docstring 天然免疫。

    ⚠️ 这是**接线守卫**：它不验函数算得对（那是上面几条），
    只验「方向词只有一个来源」。

    🚨 **豁免名单 = 那五个定性函数自己的函数体**。
      第一版没豁免 ⇒ 它把 `ci_high_verdict` 的两条 `return` 判成「手打副本」，
      而那**正是唯一真相源** ⇒ 一条**永远红**的守卫（第 112 层：
      过严的守卫会把人推向「改本来正确的代码」——这里它会逼我把
      唯一一份措辞删掉，而定性就彻底没人负责了）。
      ⇒ 豁免的是**这五个名字的子树**，不是「文件里某几行」：
      行号豁免会被任何一次插入打偏，而「谁拥有这个词」是名字的性质。
    """
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    banned = ("测得出符号", "两档都测不出符号", "两档都大于", "两档都小于",
              "两档分居", "不许说达不到", "也不许说接近", "可以说达不到",
              "连符号都定不了", "符号可定", "高于")
    owners = {n.name: n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef)}
    exempted = {id(x) for name in ("effect_size_label", "gap_size_label",
                                   "ci_high_label", "ci_high_verdict",
                                   "single_arm_effect_phrase")
                for x in ast.walk(owners[name])}
    assert set(owners).issuperset(
        {"effect_size_label", "gap_size_label", "ci_high_label",
         "ci_high_verdict", "single_arm_effect_phrase"}), (
        "定性函数被改名/删除 ⇒ 本守卫的豁免名单会悄悄失效（它会变成一条"
        "什么都不查的空转守卫）。**这是探针自身失效**，不是生产有洞。")

    offenders = [
        (node.lineno, tok.value[:60])
        for node in ast.walk(tree)
        if isinstance(node, ast.JoinedStr) and id(node) not in exempted
        for tok in node.values
        if isinstance(tok, ast.Constant) and isinstance(tok.value, str)
        for bad in banned if bad in tok.value
    ]
    assert not offenders, (
        f"这些定性词又出现在 f-string 的常量部分（手打副本）：{offenders}\n"
        f"⇒ 定性必须由 effect_size_label / gap_size_label / ci_high_label / "
        f"ci_high_verdict / single_arm_effect_phrase 从数里算出来。")


def test_the_note_builder_actually_calls_every_label_function() -> None:
    """🚨 AST **接线**守卫：note 的构造必须真的**调**那四个函数。

    为什么不能与上面那条合并：上面那条只说「没有手打副本」，
    而「没有手打副本」+「调的是**别的**函数」是能同时成立的
    （比如把 `gap_size_label(` 误写成 `effect_size_label(`，两支都合法）。
    ⇒ 钉**调用点的名字**。
    """
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "gap_over_mde_note")
    called = {c.func.id for c in ast.walk(fn)
              if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
    for need in ("effect_size_label", "gap_size_label",
                 "ci_high_label", "ci_high_verdict"):
        assert need in called, (
            f"gap_over_mde_note 的函数体内没有 {need}( 的调用点，"
            f"实际调用 = {sorted(called)} ⇒ 接线断了或调错了函数。")

    fn2 = next(n for n in tree.body
               if isinstance(n, ast.FunctionDef) and n.name == "not_a_verdict_note")
    called2 = {c.func.id for c in ast.walk(fn2)
               if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
    assert "single_arm_effect_phrase" in called2, (
        f"not_a_verdict_note 的函数体内没有 single_arm_effect_phrase( 的调用点，"
        f"实际调用 = {sorted(called2)}。")

    fn3 = next(n for n in tree.body
               if isinstance(n, ast.FunctionDef) and n.name == "ci_high_console_line")
    called3 = {c.func.id for c in ast.walk(fn3)
               if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
    assert "ci_high_verdict" in called3, (
        f"ci_high_console_line 没有调 ci_high_verdict( ⇒ CLI 与产物各有一份判决措辞。")


def test_main_prints_through_the_same_label_functions() -> None:
    """🚨 AST 接线守卫：`main()` 的三行 print 也不许手打方向词。

    🚨 这是 2026-10-16 **实测存活**的那两条变异打的位置
    （N1 缺口方向词 / N2 效应量方向词，原来就在这两行 print 里）。
    """
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "main")
    called = {c.func.id for c in ast.walk(fn)
              if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
    for need in ("effect_size_label", "gap_size_label", "ci_high_console_line",
                 "gap_over_mde_note", "not_a_verdict_note"):
        assert need in called, (
            f"main() 里没有 {need}( 的调用点，实际调用 = {sorted(called)}\n"
            f"⇒ 产物/CLI 又多了一份手打副本。")


def test_the_target_is_imported_from_the_criterion_arm_not_typed() -> None:
    """🚨 `0.20` **不许作为字面量**出现在本脚本源码里。

    与 A1b（守 `0.5`）同款理由：AST 查 `ast.Constant`，注释天然免疫。
    🚨 目标值必须**从判据臂 import**（`aplus.hurdle.TARGET_NET`）——
    否则「三处各写一次 0.20」里任何一处被人改掉都只有这一条守卫会响。
    """
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    offenders = [n.lineno for n in ast.walk(tree)
                 if isinstance(n, ast.Constant)
                 and isinstance(n.value, (int, float))
                 and not isinstance(n.value, bool)
                 and float(n.value) == 0.20]
    assert not offenders, (
        f"0.20 这个字面量出现在第 {offenders} 行 ⇒ 目标值不再是单一真相源。")


def test_the_target_is_bound_from_the_criterion_arms_module() -> None:
    """🚨 AST 接线守卫：`TARGET_NET` 必须**从 `aplus.hurdle` import**进来。

    为什么这条不能省：上一条只证明「源码里没有 `0.20` 这个字面量」，
    而那被两种写法同时满足 —— ① 从判据臂 import（对）
    ② 本地另定义一个同值常量（错，但一样没有字面量）。

    ⚠️ import **必须在 `main()` 内部**（形状 24）：`python <脚本>` 时
    `sys.path[0]` 是 `scripts/` 而非仓库根 ⇒ 模块级 `from aplus.hurdle import …`
    会让脚本**命令行直接跑崩**。⇒ 这条守卫钉的是「在 `main()` 里从
    `aplus.hurdle` import」，**不是**「在模块级 import」。
    """
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "main")
    hits = [(a.module, a.names[0].name, a.names[0].asname)
            for n in ast.walk(fn)
            if isinstance(n, ast.ImportFrom) and n.module == "aplus.hurdle"
            for a in [n] if any(x.name == "TARGET_NET" for x in n.names)]
    assert hits, (
        "main() 里没有 `from aplus.hurdle import TARGET_NET` ⇒ "
        "目标值不再是判据臂的单一真相源。")
    assert all(asname in (None, "TARGET_NET") for _, _, asname in hits), (
        f"TARGET_NET 被 import 时改了名：{hits} ⇒ 下面的引用会 NameError 或绑到别的量。")


def test_the_artifact_target_is_the_criterion_arms_value(artifact: dict) -> None:
    """🚨 产品层对拍：产物里那个 `target` 字段必须**等于判据臂的值**。

    🚨 这条是本仓的规矩：**核对产物只读字段，不许用公式重算「验证」**。
    它与上面的 AST 守卫互补：AST 守「代码绑的是谁」，这条守「落盘的是谁」。
    """
    from aplus.hurdle import TARGET_NET as REAL
    got = artifact["gap_to_target"]["target"]
    assert float(got) == float(REAL), (
        f"产物 gap_to_target.target = {got!r} ≠ aplus.hurdle.TARGET_NET = {REAL!r} "
        f"⇒ 产物是用别处的手打值生成的（第 91 层）。")


def test_the_artifact_is_labelled_a_control_arm_not_a_verdict(
        artifact: dict) -> None:
    """守卫 N11 的同款：**必须标 role**，否则读者会把点估计当判决。"""
    assert "对照臂" in artifact["role"]
    assert "不是判决" in artifact["role"]
    assert len(artifact["assumptions"]) >= 4, (
        "四条假设必须全列：敞口来源 / 摩擦线性 / 榜宽假设 / 闲置现金收益")


def test_the_breadth_assumption_is_declared_unmeasurable(artifact: dict) -> None:
    """🚨「5 只子篮子 = 10 只篮子收益」daily.parquet **测不出来**
    ⇒ 这是建模假设，必须显式说出来，不许藏在只读字段里。"""
    joined = " ".join(artifact["assumptions"])
    assert "建模假设" in joined and "不是测量" in joined


def test_it_does_not_suggest_raising_the_exposure(artifact: dict) -> None:
    """`alloc.py:1-33` 逐字：50% 是纸面验证期的**风险敞口封顶**，不是成本优化。

    ⇒ 提高敞口会把「省下的摩擦」误说成「多出来的收益」，
    而半仓 CAGR 比 0.5831 **小于** W ⇒ 提敞口在数学上更差。
    """
    w = artifact["what_this_is_not"]["does_not_suggest_raising_exposure"]
    assert "封顶" in w or "纸面验证" in w

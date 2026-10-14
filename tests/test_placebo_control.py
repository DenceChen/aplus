"""安慰剂对照实验的守卫。

预注册：`docs/TODO_placebo_control_2026-10-15.md`（已冻结）。
三条臂、种子、20 个 rep、四个判决分支全在那份文档里，本文件只钉「代码有没有
按它执行」，**不改文档里的任何阈值**。

## 全文件 autouse 闸门：守卫一律不许碰面板

面板是 GB 级 / ~10 分钟量级，与赛马同跑会 OOM（CLAUDE.md 环境节）。
`factor_width_mde` 的第一条教训就是「守卫测试触发最贵的那一步，
是守卫设计失败」—— 那里为此把活从 `main()` 抽成了 `run_widths()`。
这里再加一层：`build_panel` / `build_cross_sections` / `FactorPanel` / `LocalStore`
在本文件里**全部被替换成会炸的桩**，任何一条守卫误入面板路径当场红，
而不是安静地跑十分钟。
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import aplus.backtest as backtest_mod  # noqa: E402
import aplus.panel as panel_mod  # noqa: E402
import aplus.predict as predict_mod  # noqa: E402
import aplus.store as store_mod  # noqa: E402
import aplus.verify as verify_mod  # noqa: E402
import placebo_control as pc  # noqa: E402
from aplus.factor import FACTOR_NAMES, composite_score  # noqa: E402

PREREG_PATH = ROOT / "docs/TODO_placebo_control_2026-10-15.md"


# ── 闸门：面板 ──────────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def _forbid_panel_loading(monkeypatch):
    """把四个面板入口换成会炸的桩 —— 见文件头。"""

    def boom(*a, **k):
        raise AssertionError("🚨 守卫测试里不许加载面板（GB 级 / ~10 分钟）")

    monkeypatch.setattr(panel_mod, "build_panel", boom)
    monkeypatch.setattr(panel_mod, "FactorPanel", boom)
    monkeypatch.setattr(backtest_mod, "build_cross_sections", boom)
    monkeypatch.setattr(store_mod, "LocalStore", boom)


# ── 合成横截面（**绝不**载入真实面板）─────────────────────────────────
def _universe(n: int = 40, nan_codes: tuple = ()) -> pd.DataFrame:
    """n 只股票的横截面因子表。

    `nan_codes` 里的行把 mom20 设成 NaN ⇒ `composite_score` 出 NaN ⇒
    它们正是 P0 会被 `notna` 过滤剔掉的那些（预注册要求的「池子差异」来源）。
    """
    codes = [f"{600000 + i}.SH" for i in range(n)]
    return pd.DataFrame({
        "code": codes,
        "name": [f"股{i}" for i in range(n)],
        "mom20": [i * 0.1 for i in range(n)],
        "rev5": [0.0] * n,
        "vol20": [0.2] * n,
        "vsur": [1.0] * n,
        "isST": [0] * n,
        "listed_days": [100] * n,
        "volume": [1e6] * n,
    }).assign(mom20=lambda d: d["mom20"].mask(d["code"].isin(nan_codes)))


# ★ 加钩子**之前**的 `build_predictions`（逐字冻结自 `git show HEAD:aplus/predict.py`）。
#   预注册 §4 把「`score_override is None` 时逐位等于原行为」列为硬要求，
#   而「原行为」只能由**旧实现本身**当锚 —— 在测试里重写一遍逻辑，
#   验的是「我重写得对」，不是「钩子没改行为」。
#   ⇒ 一旦有人**有意**改了 build_predictions 的语义，这条例商会红；
#     那正是它该做的：要求显式决定，而不是安静地继承。
_PRE_HOOK_SOURCE = '''
def build_predictions_pre_hook(cross_section, top_n=10, bottom_n=10, weights=None):
    df = cross_section.copy()
    df["score"] = composite_score(df, weights)
    df = df[df["score"].notna()].reset_index(drop=True)
    ranked = df.sort_values("score", ascending=False).reset_index(drop=True)
    up = ranked.head(top_n).copy()
    up["direction"] = "up"
    down = ranked.tail(bottom_n).iloc[::-1].copy()
    down["direction"] = "down"
    keep = ["code", "name", "direction", "score"] + \\
        [c for c in FACTOR_NAMES if c in df.columns]
    return up[keep].reset_index(drop=True), down[keep].reset_index(drop=True)
'''

_ns = {"composite_score": composite_score, "FACTOR_NAMES": FACTOR_NAMES}
exec(compile(_PRE_HOOK_SOURCE, "<pre-hook predict.py>", "exec"), _ns)
build_predictions_pre_hook = _ns["build_predictions_pre_hook"]


def _same(a: pd.DataFrame, b: pd.DataFrame) -> bool:
    """逐位相等（`check_exact=True`：浮点也必须一位不差）。"""
    try:
        pd.testing.assert_frame_equal(a, b, check_exact=True)
    except AssertionError:
        return False
    return True


# ── 1. score_transform=None ⇒ 逐位等于原行为（预注册 §4 硬要求）───────
def test_none_transform_is_bitwise_identical_to_pre_hook():
    """★ 预注册 §4 的硬要求，也是整个钩子成立的前提。

    用**加钩子前的实现**当锚（不是重写一遍），且比到浮点最后一位。
    覆盖两种横截面：有 NaN 因子的（有成分被剔）与全完整的。
    """
    for xs in (_universe(40), _universe(40, nan_codes=("600003.SH", "600007.SH", "600011.SH"))):
        a_up, a_dn = predict_mod.build_predictions(xs, top_n=7, bottom_n=5,
                                                   weights={"mom20": -1.0, "vol20": -0.5})
        b_up, b_dn = build_predictions_pre_hook(xs, top_n=7, bottom_n=5,
                                                weights={"mom20": -1.0, "vol20": -0.5})
        assert _same(a_up, b_up) and _same(a_dn, b_dn), (
            "score_transform=None 与加钩子前的实现不是逐位相等 —— "
            "钩子把 P0（观测臂）也改了")


def test_identity_transform_is_bitwise_identical_to_none():
    """第二条**独立**锚：恒等钩子（原地返回）必须与 None 完全一致。

    单靠上一条不够：若钩子被写成「无论传什么都跑一遍并且默认动点什么」，
    冻结副本那条仍可能过不了，但这条会以另一种方式暴露。
    两条锚指向不同的失效方向，才不是同一个守卫写两遍。
    """
    xs = _universe(40, nan_codes=("600003.SH", "600007.SH"))
    kwargs = dict(top_n=7, bottom_n=5, weights={"mom20": -1.0, "vol20": -0.5})
    a_up, a_dn = predict_mod.build_predictions(xs, **kwargs)
    b_up, b_dn = predict_mod.build_predictions(
        xs, score_transform=lambda day, df: df, as_of="2021-01-04", **kwargs)
    assert _same(a_up, b_up) and _same(a_dn, b_dn)


# ── 2. 钩子的位置与协议 ───────────────────────────────────────────────
def test_hook_runs_before_the_notna_filter():
    """★ 钩子必须落在「算完 score」与「剔除因子不完整者」**之间**。

    判据是**行为**，不是源码位置：让钩子把某一只（本来排第一的）分数设成 NaN。
    钩子在 `notna` 之前 ⇒ 它整行被剔除 ⇒ 两张榜里都没有它；
    钩子在 `notna` 之后 ⇒ 它留在表里，且 `sort_values` 把 NaN 甩到最后
    ⇒ 它会出现在**跌榜**里。两条路径的差别是可观测的。
    """
    xs = _universe(40)
    victim = xs.loc[xs["mom20"].idxmax(), "code"]

    def kill(day, df):
        out = df.copy()
        out.loc[out["code"] == victim, "score"] = np.nan
        return out

    up, down = predict_mod.build_predictions(xs, top_n=5, bottom_n=5,
                                             score_transform=kill, as_of="2021-01-04")
    assert victim not in set(up["code"]) | set(down["code"]), (
        "被钩子置成 NaN 的那只仍在榜上 ⇒ 钩子跑在 notna 过滤**之后**，"
        "「因子不完整者剔除」的语义在安慰剂臂上被改了")


def test_hook_receives_the_signal_day_from_evaluate_weights():
    """★ 置换必须**逐日期**做 ⇒ 钩子拿得到「正在处理哪一天」。

    横截面表本身**不带日期列**（`panel.cross_section` 的列里没有 `date`），
    日期只存在于 `evaluate_weights` 的循环变量里 ——
    钩子拿不到它，就只能做「与日期无关」的改写，那测的是另一个东西。

    这里真跑一遍 `evaluate_weights`（日线表给空 ⇒ `_next_bars` 空 ⇒ 不产出行，
    但**钩子已经调过了**），断言它逐日收到四个不同的日期。
    """
    days = ["2021-01-04", "2021-01-05", "2021-01-06", "2021-01-07"]
    xs = {"_dates": days, "_daily_map": {}, "_market": {}, "_entry_lag": 1}
    for d in days:
        xs[d] = _universe(12)
    seen: list = []
    backtest_mod.evaluate_weights(xs, weights={"mom20": -1.0}, top_n=3, bottom_n=3,
                                  dates=days, rebalance_every=1, execution_gate=True,
                                  score_transform=lambda day, df: (seen.append(day), df)[1])
    assert seen == days, f"钩子收到的日期是 {seen}，而循环处理的是 {days}"


def test_hook_may_not_change_the_number_of_rows():
    """钩子只能换分数，不许换成分 —— 否则下游静默少算几只票。

    ⚠️ 变异验证（2026-10-15）发现的坑：第一版只用 `pytest.raises(ValueError)`，
    而 `df.head(5)` **同时**违反「行数」与「索引」两条校验，
    于是把行数那条校验关掉，索引那条照样抛 ⇒ **守卫为改坏行为而绿**。
    ⇒ 分成两条，各自锚在**自己那一条**校验上（下面 `test_*_index_*` 同源）。
    """
    xs = _universe(20)
    with pytest.raises(ValueError, match="改了行数"):
        predict_mod.build_predictions(xs, top_n=3, bottom_n=3,
                                      score_transform=lambda day, df: df.head(5),
                                      as_of="2021-01-04")


def test_hook_may_not_change_the_row_index():
    """索引错位比少算几只票更隐蔽：榜还是那么长，**但行与持仓对不上**了。

    行数相同、索引不同 —— 这条只有索引校验能拦（行数校验恒真）。
    """
    xs = _universe(20)
    df = xs.copy()
    df.index = [0] * len(df)              # 重复索引：让「行数不同」不再是「索引不同」的推论

    def relabel(day, d):
        out = d.copy()
        out.index = [1] * len(out)        # 行数一样，索引整个换掉
        return out

    with pytest.raises(ValueError, match="换了行索引"):
        predict_mod.build_predictions(df, top_n=3, bottom_n=3,
                                      score_transform=relabel, as_of="2021-01-04")


def test_placebo_and_the_buffer_band_refuse_to_travel_together():
    """★ `buffer_multiple` 那条路径**自己重算 `composite_score`** 重排名 ——
    拿的是**未被置换**的原始 score。于是缓冲带 + 安慰剂 =
    一半的行按打乱后的顺序选、一半按原顺序选，
    而报告里两种结果**长得一模一样**（第 6 种形状：看起来能调，实际调了别的东西）。

    这条必须在**跑之前**炸：不炸的话，安慰剂臂的三个数字都会是
    「置换 + 缓冲带」的混血，且不可辨。
    """
    with pytest.raises(ValueError) as e:
        backtest_mod.evaluate_weights(
            {}, weights={"mom20": -1.0}, top_n=3, bottom_n=3, dates=[],
            rebalance_every=1, buffer_multiple=2, execution_gate=True,
            score_transform=lambda day, df: df)
    assert "buffer_multiple" in str(e.value)


# ── 3. P1：横截面内置换（逐日期独立 / 保序无关 / 同池）────────────────
def test_permutation_is_independent_across_dates():
    """★ 给**两个不同日期**同样的输入，置换结果必须不同。

    否则「逐日期独立置换」是假的：那是「按调用顺序抽一次」，
    同一个信号日在另一个窗口下会得到不同的置换 ——
    于是「不可复现」与「假装逐日」同时成立。
    """
    tf = pc.make_shuffle_score(pc.PLACEBO_SEED)
    xs = _universe(60)
    df = xs.copy()
    df["score"] = composite_score(df, {"mom20": -1.0})
    a = tf("2021-01-04", df)["score"].to_numpy(float)
    b = tf("2021-01-05", df)["score"].to_numpy(float)
    assert not np.array_equal(a, b), (
        "两个不同日期拿到了**同一个**置换 ⇒ 随机流不是按日期分的")


def test_permutation_is_reproducible_for_the_same_seed_and_day():
    """同一个 (种子, 日期) 必须给出同一个置换 —— 否则阴性结果不可复现。

    这是第 2 条的**另一半**：既要「不同日期不同」，也要「同日期相同」。
    只钉前半的话，一个每次都换种子的实现同样能过。
    """
    xs = _universe(60)
    df = xs.copy()
    df["score"] = composite_score(df, {"mom20": -1.0})
    a = pc.make_shuffle_score(pc.PLACEBO_SEED)("2021-03-05", df)["score"].to_numpy(float)
    b = pc.make_shuffle_score(pc.PLACEBO_SEED)("2021-03-05", df)["score"].to_numpy(float)
    c = pc.make_shuffle_score(pc.PLACEBO_SEED + 1)("2021-03-05", df)["score"].to_numpy(float)
    assert np.array_equal(a, b), "同一种子同一天两次结果不同 ⇒ 不可复现"
    assert not np.array_equal(a, c), "换一个 rep 种子结果不变 ⇒ rep 之间不独立"


def test_permutation_preserves_the_score_multiset():
    """★ 置换**只换位置**：置换后的分数多重集与原来逐个相等（含 NaN 的位置）。

    这条是 P1 的定义。「打乱」与「换成随机数」是两件事 ——
    后者把分数分布也改了，测出来的差就分不清是排序还是分布。
    """
    xs = _universe(60, nan_codes=("600003.SH", "600007.SH", "600011.SH"))
    df = xs.copy()
    df["score"] = composite_score(df, {"mom20": -1.0})
    out = pc.make_shuffle_score(pc.PLACEBO_SEED)("2021-01-04", df)["score"].to_numpy(float)
    before = df["score"].to_numpy(float)
    assert np.array_equal(np.sort(out), np.sort(before), equal_nan=True), (
        "置换后分数的多重集变了 ⇒ 这不是「换位置」，是「换分数」")
    # 且 NaN 必须**原地不动**（否则连「谁有资格进池」都被随机化了）
    assert np.array_equal(np.isnan(out), np.isnan(before))


def test_shuffle_keeps_the_scorable_pool_identical_to_p0():
    """★ P1 与 P0 的**池子必须完全相同**，只有顺序不同。

    预注册 §2 的理由原文：「横截面内置换保留了每日池子的成分构成……
    它**只**摧毁『score 把好票排在前面』这件事」。
    NaN 一起搬走 = 顺手把「谁有资格进池子」也随机化了，
    于是 P1 − P0 的差同时含排序与成分两件事，**分不开**。
    """
    xs = _universe(60, nan_codes=("600003.SH", "600007.SH", "600011.SH"))
    # 榜宽开到 60 > 池子 ⇒ 涨榜**本身**就是整个池子，可以逐只比名单。
    # （写成 10 的话每张榜只露 10 行，比出来的是「抽到哪 10 只」而不是池子。）
    kwargs = dict(top_n=60, bottom_n=60, weights={"mom20": -1.0, "vol20": -0.5})
    p0_up, _ = predict_mod.build_predictions(xs, **kwargs)
    p1_up, _ = predict_mod.build_predictions(
        xs, score_transform=pc.make_shuffle_score(pc.PLACEBO_SEED),
        as_of="2021-01-04", **kwargs)
    assert set(p1_up["code"]) == set(p0_up["code"]), (
        "P1 的池子与 P0 不一样 ⇒ 差的不只是排序")
    assert len(p0_up) == 57, f"P0 本该剔掉 3 只 NaN，实得 {len(p0_up)}"
    assert set(xs["code"]) - set(p1_up["code"]) == {"600003.SH", "600007.SH", "600011.SH"}
    # 榜宽=池子时**分数序列必然相同**（同一多重集排序后的结果与置换无关），
    # 可观测的差别在**行顺序**上 —— 比分数只会恒真。
    assert list(p1_up["code"]) != list(p0_up["code"]), (
        "P1 的行顺序与 P0 逐只相同 ⇒ 置换根本没生效")
    assert sorted(p1_up["score"].to_numpy(float)) == \
        sorted(p0_up["score"].to_numpy(float))


def test_p2_covers_stocks_that_p0_drops():
    """★ P2 抽的是「当日**可交易池**」，与 P1 的池子**不同**（预注册原文）。

    因子不完整者也在池里。所以 P2 的可抽样集合比 P0 大 ——
    这个差必须**看得见**，否则读者会把 P1 与 P2 当成同一件事的两个读数。
    """
    xs = _universe(60, nan_codes=("600003.SH", "600007.SH", "600011.SH"))
    kwargs = dict(top_n=60, bottom_n=60, weights={"mom20": -1.0, "vol20": -0.5})
    p0_up, _ = predict_mod.build_predictions(xs, **kwargs)
    p0_codes = set(p0_up["code"])
    # 榜宽开到 60 ⇒ P2 的涨榜就是「随机名次下的整个可交易池」（确定性，非概率）
    p2_up, _ = predict_mod.build_predictions(
        xs, score_transform=pc.make_shuffle_list(pc.PLACEBO_SEED),
        as_of="2021-01-04", **kwargs)
    dropped = set(xs["code"]) - p0_codes
    assert dropped == {"600003.SH", "600007.SH", "600011.SH"}, "前置条件变了"
    assert set(p2_up["code"]) == set(xs["code"]), (
        "P2 的池子不是整个可交易池 ⇒ 它被悄悄改成了 P0 的池子，"
        "P1/P2 之间的那条差异就没了")
    assert dropped <= set(p2_up["code"]), (
        "P2 抽不到 P0 会剔掉的那几只 ⇒ P2 的池子不是「可交易池」")


# ── 4. 四个判决分支：每一条都必须可达 ────────────────────────────────
_CASES = {
    # (t_p0, p1 的 t 列表) → 判决码。构造依据见 `placebo_conditions` 的注释。
    "PLACEBO_NULL": (2.44, [0.10, 1.00, 1.50, 0.80]),
    "PLACEBO_PASSES": (3.00, [0.50, 2.00, 2.30, 0.80]),      # max 显著但 < t(P0)
    "PLACEBO_BENCHMARK": (2.44, [1.96, 2.20, 2.50, 2.10]),
    "PLACEBO_MIXED": (2.50, [0.10, 1.00, 2.60, 0.90]),
}


@pytest.mark.parametrize("expected", sorted(_CASES))
def test_every_preregistered_branch_is_reachable(expected):
    """★ 预注册 §3 的四条分支，**每一条都要能走到**。

    `docs/MISTAKE_LOG_2026-10-14.md` 第 88 层：判决函数读了上一级字典，
    四条冻结分支里三条**不可达**，而全部测试照绿。
    ⇒ 这里逐条喂输入、走进去、看返回的判决码。
    """
    t_p0, ts = _CASES[expected]
    verdict, branch = pc.decide_for_test(t_p0, ts)
    assert verdict == expected, (
        f"输入 (t_p0={t_p0}, p1={ts}) 应落在 {expected}，实得 {verdict}")
    assert branch and "⇒" in branch


def test_the_four_conditions_cover_the_whole_input_space():
    """★ 四条**不互斥**（NULL ⊆ PASSES），但必须**盖满**整个空间。

    取第一条命中做判决码，那前提是「至少有一条会命中」——
    否则会出现「四条全假 ⇒ 兜底成某个分支」的静默路径（第 88 层）。

    ⚠️ 采样要**混着来**：NULL 要求「P0 偏高 **且** 20 个 t 全低于 1.96」，
    均匀采样下那片区域约 1e-7 ⇒ 3000 次里一次都碰不到，
    读起来像「分支不可达」，其实只是没采到（`factor_width_mde` 踩过同一形状）。
    """
    rng = np.random.default_rng(20261015)
    keys = ("PASSES__t_p0_gt_p1_max", "BENCHMARK__p1_median_ge_tcrit",
            "MIXED__median_lt_tcrit_and_some_ge_p0",
            "NULL__all_lt_tcrit_and_max_lt_p0")
    hits = {k: 0 for k in keys}
    for i in range(3000):
        if i % 2:                       # 均匀域
            p = float(rng.uniform(-1.0, 5.0))
            ts = [float(x) for x in rng.uniform(-1.0, 5.0, 20)]
        else:                           # NULL 区：高 P0 + 全不显著
            p = float(rng.uniform(2.0, 5.0))
            ts = [float(x) for x in rng.normal(0.8, 0.5, 20)]
        c = pc.placebo_conditions(p, ts)
        for k in keys:
            hits[k] += bool(c[k])
    assert all(h > 0 for h in hits.values()), f"有分支随机都碰不到：{hits}"
    # 兜底路径也不许存在：任何一组输入都必须至少命中一条
    for p, ts in ((2.44, [0.1, 0.2]), (2.44, [3.0, 3.1]), (-2.0, [0.0, 5.0]),
                  (0.0, [0.0, 0.0, 0.0]), (2.44, [1.0] * 20), (9.0, [-1.0] * 20)):
        c = pc.placebo_conditions(p, ts)
        assert any(c[k] for k in keys), f"({p}, {ts}) 一条都没命中 ⇒ 会走兜底"


def test_decide_refuses_rather_than_guessing_a_missing_p0():
    """P0 缺档 ⇒ **拒绝出判决**，绝不拿 2.44 这类字面量顶上。"""
    verdict, branch = pc.decide_for_test(None, [0.1, 0.2, 0.3])
    assert verdict is None
    assert "拒绝出判决" in branch


def test_decide_refuses_when_a_p1_rep_is_missing():
    """把跑不通的 rep 悄悄剔掉，分布就成了「只报好看的」。"""
    verdict, branch = pc.decide_for_test(2.44, [0.1, None, 0.2])
    assert verdict is None
    assert "拒绝出判决" in branch


def test_the_verdict_follows_the_passed_p0_not_a_literal():
    """★ 同一个 P1 分布、两个不同的 `t_p0` ⇒ 判决必须不同。

    钉住「P0 的 t 是**入参**」：预注册正文写的 2.44 是榜宽实验那个产物上的数，
    硬编码它就等于免掉「P0 必须真的重跑一遍」。

    选 `ts` 时注意 NULL 与 PASSES 的包含关系（见 `decide` 注释）：
    `max < 1.96 < P0` ⇒ 两条同时真，取第一条 ⇒ NULL。
    要拿到 PASSES，必须让 `max ≥ 1.96`（把 NULL 顶掉）而 `median < 1.96`。
    """
    ts = [0.30, 0.50, 2.00, 2.10]          # max 2.10 ≥ 1.96，median 1.25 < 1.96
    assert pc.decide_for_test(2.44, ts)[0] == "PLACEBO_PASSES"
    assert pc.decide_for_test(0.10, ts)[0] == "PLACEBO_MIXED"


# ── 5. 三条臂走同一条代码路径（预注册 §4）────────────────────────────
def test_the_three_arms_share_one_code_path(monkeypatch):
    """★ 三条臂必须调**同一个 `run_year`**，参数逐个相同。

    禁止为安慰剂另写更快的路径 —— 那测的是另一个东西（预注册 §4）。
    判据是**行为**：录下每一次 `run_year` 的实参，三条臂之间只允许
    `score_transform` 不同。
    """
    calls: list = []

    def fake_run_year(xs, weights, dates, top_n, bottom_n, rebalance_every,
                      daily_out=None, score_transform=None):
        calls.append({"weights": weights, "dates": dates, "top_n": top_n,
                      "bottom_n": bottom_n, "k": rebalance_every,
                      "transform": score_transform,
                      "min_basket_seen": verify_mod.MIN_BASKET})
        if daily_out is not None:
            daily_out.append({"dates": ["2021-01-04", "2021-01-05", "2021-01-06"],
                              "excess": [0.001, 0.002, 0.003], "exposure": {}})
        return {"n_rows": 3, "n_days": 3, "real": {"annual_net_pct": 0.0,
                                                    "hit_threshold": False}}

    monkeypatch.setattr(pc, "run_year", fake_run_year)
    wmap = {2021: {"weights": {"mom20": -1.0}}}
    pc.run_arms(None, wmap, [2021], {2021: ["2021-01-04"]}, n=200, k=21, reps=3)

    assert len(calls) == 1 + 3 + 3, f"run_year 被调了 {len(calls)} 次，应为 1+3+3"
    shapes = {(c["top_n"], c["bottom_n"], c["k"],
               tuple(sorted(c["weights"].items())),   # dict 不可哈希
               tuple(c["dates"]), c["min_basket_seen"]) for c in calls}
    assert len(shapes) == 1, f"三条臂的 run_year 入参不一致：{shapes}"
    assert calls[0]["transform"] is None, "P0 必须原样跑（score_transform=None）"
    assert all(c["transform"] is not None for c in calls[1:]), (
        "安慰剂臂必须带钩子跑，否则它和 P0 是同一件事")
    assert all(c["top_n"] == 200 and c["bottom_n"] == 200 and c["k"] == 21
               for c in calls), "主档 N/K 与预注册不符"


def test_min_basket_is_restored_even_when_a_run_explodes(monkeypatch):
    """★ `aplus.verify.MIN_BASKET` 是全局量，改了必须在 `finally` 里还原。

    否则同进程后续的每日任务会静默按 N=200 的成分下限去跑 N=10。

    ⚠️ 变异验证（2026-10-15）发现的坑：**先读现值再比回来是顺序依赖的**。
    第一版写 `original = verify_mod.MIN_BASKET`；把 `finally` 里的还原删掉后，
    单跑这条它**是红的**，而整文件跑它**是绿的** ——
    因为前面那条 `test_the_three_arms_share_one_code_path` 同样调 `run_arms`，
    先把 50 漏了出去，于是「原值」已经是 50，跑完还是 50 ⇒ 断言恒真。
    ⇒ 改成先摆一个**已知的哨兵值**，与它比，别的测试漏不漏都无关。
    """
    sentinel = 7                       # 不是 3 也不是 50 ⇒ 任何默认值都蒙不对
    monkeypatch.setattr(verify_mod, "MIN_BASKET", sentinel)

    def boom(*a, **k):
        raise RuntimeError("故意在跑的过程中炸")

    monkeypatch.setattr(pc, "run_year", boom)
    with pytest.raises(RuntimeError):
        pc.run_arms(None, {2021: {"weights": {}}}, [2021], {2021: []}, 200, 21, 1)
    assert verify_mod.MIN_BASKET == sentinel, (
        f"MIN_BASKET 跑完没还原（现值 {verify_mod.MIN_BASKET}，哨兵 {sentinel}）")


# ── 6. 口径与预注册（产物必须带着口径一起走）───────────────────────────
def _args():
    class A:
        start = pc.JUDGEMENT_START
    return A()


def test_caliber_block_declares_everything_the_preregistration_demands():
    """★ 口径声明块缺一项，数字离开脚本后就没人能查了（第 7 层）。

    预注册点名要的：主档 N / K / 窗口 / 种子 / rep 数 / **每臂同一条代码路径**。
    """
    c = pc.caliber_block(_args(), "2026-10-12", 200, 21, 20)
    assert c["primary_basket_n"] == 200
    assert c["k_rebalance_every"] == 21
    assert c["window"] == ["2021-01-01", "2026-10-12"]
    assert c["window_start_is_the_judgement_window"] is True
    assert c["placebo_seed"] == 20261015
    assert c["n_reps_preregistered"] == 20
    assert c["min_basket"] == 50          # N=200 → max(10, 200//4)
    assert "run_year" in c["code_path"]["shared"]
    assert "evaluate_weights" in c["code_path"]["shared"]
    assert "build_predictions" in c["code_path"]["shared"]
    assert c["code_path"]["only_difference"].startswith("score_transform")
    assert "composite_score" in c["code_path"]["composite_score_untouched"]


def test_preregistered_numbers_live_in_the_prereg_not_only_in_code():
    """★ 先有码、后补文档 = 看完数字再挑阈值（`factor_width_mde` 的血泪）。

    种子、rep 数、主档、K、窗口必须**文档与代码一致**，且代码是引用不是重打。
    """
    doc = PREREG_PATH.read_text(encoding="utf-8")
    assert "20261015" in doc and pc.PLACEBO_SEED == 20261015
    assert "**20**" in doc and pc.N_PLACEBO_REPS == 20
    assert "2021-01-01" in doc and pc.JUDGEMENT_START == "2021-01-01"
    assert pc.rep_seed(0) == pc.PLACEBO_SEED and pc.rep_seed(7) == pc.PLACEBO_SEED + 7
    # MIN_BASKET 与 t_crit 都从**别处** import，不在本脚本重新推导
    assert pc.scaled_min_basket.__module__.startswith("factor_width_mde")
    assert abs(pc.T_CRIT_5PCT - 1.959963985) < 1e-6, (
        "t_crit 必须是双尾 5% 的那个数；在这里重打一遍字面量就等于另立一套口径")
    assert pc.scaled_min_basket(200) == 50


def test_report_distribution_and_verdict_come_from_the_same_numbers():
    """★ 「判断量与被判断量必须同源」（第 15 层）。

    `summarize_reps` 报给读者的 min/中位数/max 与 `placebo_conditions`
    用来判决的那三个数，必须是同一组 —— 否则报告会报出一个与判决不同的分布。
    """
    runs = [{"t_blocked": t} for t in (0.10, 1.00, 1.50, 0.80)]
    s = pc.summarize_reps(runs, t_p0=2.44)
    c = pc.placebo_conditions(2.44, [r["t_blocked"] for r in runs])
    assert (s["min"], s["median"], s["max"]) == (c["p1_min"], c["p1_median"], c["p1_max"])
    assert s["n_ge_p0"] == c["n_ge_p0"] == 0
    # 缺的 rep 必须**报数**，不许静默丢弃
    s2 = pc.summarize_reps([{"t_blocked": 1.0}, {"t_blocked": None}], t_p0=2.44)
    assert s2["n_reps"] == 2 and s2["n_reported"] == 1 and s2["missing_reps"] == 1


def test_the_window_and_the_primary_width_are_refused(tmp_path, capsys):
    """★ 预注册 §3 规则 1：不得在看到 P1 之后改 P0 的档位或窗口。

    守卫在**脚本**里（不是文档里）—— 文档能被满足，代码不能。
    并且拒绝时**不许落盘**：旧口径的产物比没有产物更危险。
    """
    out = tmp_path / "should_not_exist.json"
    assert pc.main(["--start", "2019-01-02", "--out", str(out)]) == 2
    assert pc.main(["--start", "2021-01-01", "--n", "10", "--out", str(out)]) == 2
    assert pc.main(["--start", "2021-01-01", "--reps", "999", "--out", str(out)]) == 2
    assert not out.exists(), "口径不对却还是落了盘"
    assert "不是同一个量" in capsys.readouterr().err


def test_the_width_report_cross_check_is_optional_and_honest(tmp_path):
    """榜宽实验产物是**可选**的交叉核对：缺失即 null，不猜、不抄 2.44 顶上。"""
    assert pc.read_width_artifact_t(tmp_path / "nope.json") is None
    good = tmp_path / "w.json"
    good.write_text(json.dumps({"widths": {"200": {"t_blocked": 2.44}}}),
                    encoding="utf-8")
    assert pc.read_width_artifact_t(good) == 2.44
    broken = tmp_path / "b.json"
    broken.write_text(json.dumps({"widths": {"200": {}}}), encoding="utf-8")
    assert pc.read_width_artifact_t(broken) is None


# ─────────────────────────────────────────────────────────────
# 2026-10-15 事后补上：分支**求值顺序**本身就是判据
# ─────────────────────────────────────────────────────────────

def _verdict_under(order, t_p0, p1_ts):
    c = pc.placebo_conditions(t_p0, p1_ts)
    for key in order:
        if c[key]:
            return pc._VERDICT_OF[key]
    return None


def _exhaustive_cases(n_cases=40000, seed=7):
    """穷举而不是抽样 —— 本条要证明的是「**有**一组同时成立」，
    抽样永远证明不了存在性。随机源固定，失败可复现。"""
    rng = random.Random(seed)
    out = []
    for _ in range(n_cases):
        p = round(rng.uniform(-1, 5), 2)
        ts = [round(rng.uniform(-3, 6), 2) for _ in range(rng.randint(3, 20))]
        out.append((p, ts))
    return out


def test_the_branch_order_is_the_conservative_one():
    """★ **本条是补录，不是实现细节**：四条判决条件**不互斥**，
    而「同时成立时谁赢」从来没有被写进预注册。

    实现的第一版默认继承了预注册的**表格行序**（`PASSES` 在 `BENCHMARK` 之前），
    结果 284 例「安慰剂中位数已经 ≥ 1.96」被判成 `PLACEBO_PASSES` ——
    🚨 **最响的警报（基准坏了、榜宽显著性全部作废）被唯一能报喜的分支静默吃掉。**

    ## 为什么必须单独写一条守卫

    那 22 条变异全都**抓不到**这个缺陷：它们验的是「代码符合它自己声称的规则」，
    而当时的代码与它自己的注释**完全一致**（注释写的就是「NULL 先行」）。
    **变异测试验不了「声称的规则本身对不对」。**

    ⇒ 失败模式不对称：`PASSES` 压过 `BENCHMARK`，压对了只多花一次重测，
      压错了整轮结论作废而没人知道。所以顺序必须按**严厉程度降序**。
    """
    cases = _exhaustive_cases()

    # ① 先证明这两组**确实会同时成立** —— 顺序才有意义。
    both_bench = [(p, ts) for p, ts in cases
                  if (lambda c: c["BENCHMARK__p1_median_ge_tcrit"]
                      and c["PASSES__t_p0_gt_p1_max"])(pc.placebo_conditions(p, ts))]
    both_null = [(p, ts) for p, ts in cases
                 if (lambda c: c["NULL__all_lt_tcrit_and_max_lt_p0"]
                     and c["PASSES__t_p0_gt_p1_max"])(pc.placebo_conditions(p, ts))]
    assert len(both_bench) > 50, (
        f"穷举 {len(cases)} 组只找到 {len(both_bench)} 组 BENCHMARK∧PASSES —— "
        "若这条红了，说明随机源或分布变了，本条的论证前提要重新确认"
    )
    assert both_null, "NULL ⊆ PASSES 是代数恒真，穷举却找不到 ⇒ 前提错了"

    # ② 顺序必须是**严厉程度降序**，且四条**一个都不能少**
    assert pc._BRANCH_ORDER == (
        "BENCHMARK__p1_median_ge_tcrit",
        "MIXED__median_lt_tcrit_and_some_ge_p0",
        "NULL__all_lt_tcrit_and_max_lt_p0",
        "PASSES__t_p0_gt_p1_max",
    ), f"判决顺序变了：{pc._BRANCH_ORDER} —— 必须是最响的结论先匹配"

    # ③ ★ 行为判据：任何「基准坏了」的样本都不许被报成好消息
    shadowed = [(p, ts) for p, ts in both_bench
                if _verdict_under(pc._BRANCH_ORDER, p, ts) != "PLACEBO_BENCHMARK"]
    assert not shadowed, (
        f"{len(shadowed)}/{len(both_bench)} 组「中位数 ≥ 1.96」被判成 "
        f"{_verdict_under(pc._BRANCH_ORDER, *shadowed[0]) if shadowed else ''} —— "
        "警报被压住了"
    )

    # ④ 四个分支都必须**真的能被判出来**（不可达 = 第 88 层）
    tally = {}
    for p, ts in cases:
        v = _verdict_under(pc._BRANCH_ORDER, p, ts)
        tally[v] = tally.get(v, 0) + 1
    for want in ("PLACEBO_BENCHMARK", "PLACEBO_MIXED", "PLACEBO_NULL", "PLACEBO_PASSES"):
        assert tally.get(want, 0) > 0, f"{want} 在穷举里一次都没出现 ⇒ 分支不可达"


def test_mutating_the_branch_order_to_the_table_order_goes_red():
    """★ 变异自证：把顺序改回**预注册的表格行序**，`decide` 的输出必须改变。

    没有这一条，上一条可能只是一次「碰巧通过」的描述性断言。
    本条确认它**真的在守那个顺序**：顺序一换，判决标签就变。
    """
    table_order = (
        "PASSES__t_p0_gt_p1_max",
        "BENCHMARK__p1_median_ge_tcrit",
        "MIXED__median_lt_tcrit_and_some_ge_p0",
        "NULL__all_lt_tcrit_and_max_lt_p0",
    )
    assert table_order != pc._BRANCH_ORDER, "变异前提失效：两个顺序一样了"
    p, ts = _exhaustive_cases(n_cases=40000)[0]
    diffs = 0
    for p, ts in _exhaustive_cases():
        if _verdict_under(table_order, p, ts) != _verdict_under(pc._BRANCH_ORDER, p, ts):
            diffs += 1
    assert diffs > 100, f"换顺序只改了 {diffs} 例判决 —— 本条守的东西可能不存在"

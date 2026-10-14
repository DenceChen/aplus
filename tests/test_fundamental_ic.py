"""BM / ROE 的 IC 检验。

这批测试守的不是算术，是三件容易悄悄错掉的事：

1. **两臂同池**——因子臂与随机臂必须取自同一个覆盖池；
2. **网格不重叠**——否则 t 因为重复计数而虚高；
3. **不造证据**——样本退化时返回 `None`，而不是一个凭空的大 t。
"""
import numpy as np
import pandas as pd
import pytest

from scripts.fundamental_ic import (
    FACTORS, HORIZONS, MIN_CROSS_SECTION, N_COMPARISONS,
    _spearman, ic_series, ic_tstat, portfolio_vs_random,
)


# ── 造一个可控的假面板 ────────────────────────────────────────────────

def _panel(days, n_total=200, n_covered=80, with_bm=True):
    """全池 `n_total` 只，但只有前 `n_covered` 只带 bm。

    两者的差正是「覆盖池 ≠ 全池」这个坑的来源，必须能被测试造出来。
    """
    xs = {"_dates": list(days), "_daily_map": {}, "_factors_map": {}}
    codes = [f"{i:06d}" for i in range(n_total)]
    for d in days:
        df = pd.DataFrame({"code": codes})
        df["bm"] = np.linspace(-1, 1, n_total)
        if not with_bm:
            df["bm"] = np.nan
        else:
            df.loc[n_covered:, "bm"] = np.nan
        xs[d] = df
    return xs


@pytest.fixture
def spy(monkeypatch):
    """把 `forward_return` 换成记录器：记下被询问过的 (day, code)。"""
    seen = []
    seen_under_test = []

    def fake(daily_map, factors_map, adj_cache, code, day, k):
        seen_under_test.append((day, code))
        seen.append((day, code))
        # 必须**逐票不同**。常数收益会让秩相关的分母为 0，
        # `rank_corr` 返回 `None`，于是 IC 一条都算不出来——
        # 测试会看到「空序列」而不是它本来想验的东西。
        return (int(code) % 17) * 0.001

    monkeypatch.setattr("scripts.fundamental_ic.forward_return", fake)
    return seen_under_test


# ── 族大小：事前登记 ────────────────────────────────────────────────

def test_the_family_counts_every_test_that_was_run():
    """族必须是 2 因子 × 2 周期 = 4，一个不多一个不少。

    少算就等于事后挑掉不喜欢的格子再报，临界会偏低。
    """
    assert len(FACTORS) == 2 and len(HORIZONS) == 2
    assert N_COMPARISONS == len(FACTORS) * len(HORIZONS) == 4


def test_family_correction_is_stricter_than_an_uncorrected_gate():
    from aplus.evolve_race import bonferroni_t
    assert bonferroni_t(N_COMPARISONS, df=59) > bonferroni_t(1, df=59)


# ── 同池：刚修掉的那个缺陷 ───────────────────────────────────────────

def test_the_random_arm_draws_from_the_same_pool_as_the_factor_arm(spy):
    """**本脚本最贵的一条。**

    `arm(xs, days, None, seed, k)` 的随机臂给的是**全池**随机分数，
    因子臂走 `composite_score` 时 NaN 会传播并被 `dropna` 剔掉。
    直接复用 `arm` 会让随机臂在全池 200 只里抽、因子臂在有财报的 80 只里选——
    这个 t 度量的是「样本构成」，不是「因子能不能挑出好的」。

    所以断言：被随机臂询问过的每一只票，都必须带 bm。
    """
    days = ["2022-01-05", "2022-01-06", "2022-01-07", "2022-01-10"]
    xs = _panel(days, n_total=200, n_covered=80)
    portfolio_vs_random(xs, days, "bm", k=2, seed=1)

    covered = set(xs[days[0]].loc[xs[days[0]]["bm"].notna(), "code"])
    assert spy, "记录器一次都没被调用——测试是空的，样本根本没进去"
    outside = {c for _d, c in spy} - covered
    assert not outside, (
        f"随机臂问了 {len(outside)} 只**没有财报**的票（如 {sorted(outside)[:3]}）——"
        f"两臂池子不同，组合 t 测的是样本构成而不是选股能力")


def test_the_factor_arm_only_buys_stocks_that_carry_the_factor(spy):
    days = ["2022-01-05", "2022-01-06", "2022-01-07", "2022-01-10"]
    xs = _panel(days, n_total=200, n_covered=80)
    portfolio_vs_random(xs, days, "bm", k=2, seed=1)
    covered = set(xs[days[0]].loc[xs[days[0]]["bm"].notna(), "code"])
    assert all(c in covered for _d, c in spy)


def test_narrow_pool_is_refused_rather_than_silently_used(spy):
    """覆盖池窄于门槛时，两个指标都必须拒绝出数。"""
    days = ["2022-01-05", "2022-01-06", "2022-01-07", "2022-01-10"]
    xs = _panel(days, n_total=200, n_covered=MIN_CROSS_SECTION - 1)
    assert ic_series(xs, days, "bm", k=2).empty
    row = portfolio_vs_random(xs, days, "bm", k=2, seed=1)
    assert row["paired_t"] is None and row["n_days"] == 0
    assert not spy, "池子不够宽却仍然下了单——比不出数更危险"


# ── 网格：不重叠 ─────────────────────────────────────────────────────

def test_the_ic_grid_does_not_overlap(spy):
    """K=3、6 个交易日 → 只在 i=0 和 i=3 上取点，**恰好 2 个 IC**。

    若改成逐日都算，6 个观测里包含 4 个与相邻观测重叠的窗口，
    样本量看起来翻倍，实际信息量没变——t 会虚高。
    """
    days = [f"2022-01-{d:02d}" for d in (5, 6, 7, 10, 11, 12)]
    xs = _panel(days)
    ic = ic_series(xs, days, "bm", k=3)
    assert list(ic.index) == [days[0], days[3]], (
        f"网格取点是 {list(ic.index)}，不是 {{第0, 第3}} 天——重叠了")


def test_k1_uses_every_trading_day(spy):
    days = [f"2022-01-{d:02d}" for d in (5, 6, 7, 10)]
    xs = _panel(days)
    assert len(ic_series(xs, days, "bm", k=1)) == len(days)


# ── 不造证据 ────────────────────────────────────────────────────────

def test_ic_t_is_none_when_the_ic_series_is_constant():
    """常数序列的标准误是 0。除以它会得到 inf 或一个凭空的天文数字。

    「宁可缺证据，不可造证据」——这里必须返回 `None`。
    """
    t, n = ic_tstat(pd.Series([0.03] * 20))
    assert t is None and n == 20


def test_ic_t_is_none_when_there_are_too_few_periods():
    t, n = ic_tstat(pd.Series([0.01, 0.02]))
    assert t is None and n == 2


def test_ic_t_recovers_a_known_mean_and_spread():
    """`t = mean / (sd/√n)`——手算核对，防止口径漂了却没人发现。"""
    v = np.array([0.02, 0.03, 0.04, 0.05])
    want = v.mean() / (v.std(ddof=1) / np.sqrt(len(v)))
    t, _n = ic_tstat(pd.Series(v))
    assert t == pytest.approx(want)


# ── IC 本身 ─────────────────────────────────────────────────────────

def test_spearman_is_one_on_a_perfect_monotone_relationship():
    x = np.linspace(-2, 2, 200)
    y = 3.7 * x + 1.2                    # 严格单调
    assert _spearman(x, y) == pytest.approx(1.0)


def test_spearman_is_minus_one_when_the_factor_is_backwards():
    """因子方向错了 IC 会整体翻号而不是报错——所以方向要有单独测试钉住。"""
    x = np.linspace(-2, 2, 200)
    assert _spearman(x, -x) == pytest.approx(-1.0)


def test_spearman_refuses_a_cross_section_below_the_floor():
    x = np.arange(10.0)
    assert _spearman(x, x) is None, "10 只票上的秩相关被算了出来"


# ── 边界：这族不许进实盘 ────────────────────────────────────────────

def test_the_module_declares_pit_is_not_established_and_cannot_promote():
    """结果必须自带「不能作为采纳依据」的声明，并写明不落 `weights.json`。"""
    import inspect

    import scripts.fundamental_ic as m
    src = inspect.getsource(m)
    assert "no_promotion" in src and "不写 weights.json" in src
    assert "PIT 未建立" in src and "不构成采纳依据" in src


def test_it_registers_why_ic_rather_than_the_portfolio_is_primary():
    """主指标为什么是 IC 必须留在文件里——否则后人会把副指标当主指标读。"""
    import inspect

    import scripts.fundamental_ic as m
    src = inspect.getsource(m)
    assert "为什么主指标是 IC" in src
    assert "0.006" in src, "IC 的标准误量级是选它当主指标的理由，必须写在文件里"

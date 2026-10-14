"""逐因子 IC 诊断（TDD）。

## 这组测试在钉什么

**一条硬性口径一致**：`factor_ic.forward_returns` 是为了跑得快而重写的
取价快路径，它必须与 `backtest._next_bars`（平台真实成交口径）
**逐位相等**。一旦分叉，IC 就在量另一件事 —— 而 IC 表格看上去照样
有均值、有 t 值、有星号，完全看不出量错了。

其余测试钉的是「不诚实输出」的三条防线：

1. 样本不足时返回 `None`，而不是 0 或 NaN —— 0 会被下游当成
   「这个因子没有预测力」，那是结论，不是「测不出来」。
2. 并列值取平均秩：横截面上缩尾后的分位、`turn20` 这类整数因子
   并列极多，按顺序打破并列会系统性污染 IC。
3. 不可成交的样本**剔除而非填 0**：填 0 会把停牌股记成
   「预测正确且赚 0」，凭空稀释 IC。
"""
import numpy as np
import pandas as pd
import pytest

from aplus.backtest import MAX_PLAUSIBLE_RETURN, _next_bars
from aplus.factor_ic import (MIN_OBS, MIN_PAIRS, build_price_cache, daily_ic,
                             daily_ic_ranked, make_ranked_forward,
                             forward_returns, ic_summary, rank_corr)
from aplus.factor_ic import _rankdata


def _dm(**closes):
    """daily_map：代码 → DataFrame（连续日期 + 给定收盘）。"""
    out = {}
    for code, series in closes.items():
        out[code] = pd.DataFrame({
            "date": [f"2026-01-{i + 1:02d}" for i in range(len(series))],
            "open": list(series),
            "close": list(series),
            "volume": [1e6] * len(series),
            "amount": [1e8] * len(series),
        })
    return out


DAY = "2026-01-05"            # 第 5 根 K 线 = D


# ── 口径一致：快路径 == 回测真实成交口径 ──────────────────────────

def test_forward_returns_matches_next_bars():
    """(0, 1) 窗口必须与 `_next_bars(entry_lag=1)` 逐位相等。

    这是整个 IC 模块存在的理由：IC 必须在策略真正捕获的那个
    收益窗口上算，否则「因子有预测力」和「组合超额为负」可以同时
    为真，而两者都在描述同一批交易。
    """
    dm = _dm(A=[10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0],
             B=[20.0, 20.0, 20.0, 20.0, 20.0, 22.0, 20.0, 24.0],
             C=[5.0, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0])
    bars = _next_bars(dm, ["A", "B", "C"], DAY, entry_lag=1)
    expect = (bars.set_index("code")["close"] / bars.set_index("code")["prev_close"] - 1.0)

    got = forward_returns(build_price_cache(dm), ["A", "B", "C"], DAY, 0, 1)

    assert set(got.index) == set(expect.index)
    for code in expect.index:
        assert got[code] == pytest.approx(expect[code], abs=1e-12), code


def test_leaky_window_matches_next_bars_entry_lag_zero():
    """(-1, 0) 必须与 `_next_bars(entry_lag=0)` 相等 —— 泄露探针口径对齐。

    否则「entry_lag=0 到底虚高多少」这个数就是在拿两把不同的尺子比。
    """
    dm = _dm(A=[10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0],
             B=[20.0, 19.0, 21.0, 18.0, 22.0, 17.0, 23.0, 16.0])
    bars = _next_bars(dm, ["A", "B"], DAY, entry_lag=0)
    expect = (bars.set_index("code")["close"] / bars.set_index("code")["prev_close"] - 1.0)

    got = forward_returns(build_price_cache(dm), ["A", "B"], DAY, -1, 0)

    for code in expect.index:
        assert got[code] == pytest.approx(expect[code], abs=1e-12), code


def test_real_window_is_one_day_shifted_from_leaky_window():
    """真实窗口必须整体后移一根 —— 否则「修掉前视」就是嘴上说说。

    用一只单调上涨的股票：泄露窗口量到 D→D+1，真实窗口量到 D+1→D+2。
    """
    dm = _dm(A=[10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0])
    # DAY 落在 index 4（收盘 14.0）；searchsorted(side="right") 给出的 pos = 5
    leaky = forward_returns(build_price_cache(dm), ["A"], DAY, -1, 0)["A"]
    real = forward_returns(build_price_cache(dm), ["A"], DAY, 0, 1)["A"]
    assert leaky == pytest.approx(15.0 / 14.0 - 1)      # D 收盘买 → D+1 收盘卖
    assert real == pytest.approx(16.0 / 15.0 - 1)        # D+1 收盘买 → D+2 收盘卖


# ── 不可成交样本：剔除，不是填 0 ─────────────────────────────────

def test_suspended_bar_excluded_not_zeroed():
    """进场那根 volume=0 → 该样本不得出现在结果里。

    填 0 会把停牌股记成「预测对了、赚 0」，IC 被无声稀释。
    """
    df = pd.DataFrame({
        "date": [f"2026-01-{i + 1:02d}" for i in range(8)],
        "open": [10.0] * 8, "close": [10.0] * 8,
        "volume": [1e6, 1e6, 1e6, 1e6, 1e6, 0.0, 1e6, 1e6],   # 第 6 根停牌
        "amount": [1e8] * 8,
    })
    got = forward_returns(build_price_cache({"A": df}), ["A"], DAY, 0, 1)
    assert "A" not in got.index


def test_implausible_return_excluded():
    """单日 +50% 超过 `MAX_PLAUSIBLE_RETURN` → 剔除。"""
    dm = _dm(A=[10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 15.0, 10.0])
    got = forward_returns(build_price_cache(dm), ["A"], DAY, 0, 1)
    assert "A" not in got.index
    assert MAX_PLAUSIBLE_RETURN == pytest.approx(0.40)


def test_just_under_threshold_is_kept():
    """边界：+30% 必须保留。只卡上限不卡下限会把真实行情误删。"""
    dm = _dm(A=[10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 13.0, 10.0])
    got = forward_returns(build_price_cache(dm), ["A"], DAY, 0, 1)
    assert got["A"] == pytest.approx(0.30)


def test_tail_window_out_of_range_is_dropped_not_clipped():
    """出场超出序列末端 → 不成交。拿更早的收盘凑一个数就是造假。"""
    dm = _dm(A=[10.0, 11.0, 12.0, 13.0, 14.0])
    got = forward_returns(build_price_cache(dm), ["A"], DAY, 0, 4)
    assert got.empty


# ── rank_corr ────────────────────────────────────────────────────

def test_rank_corr_monotone_is_one():
    x = np.arange(100, dtype=float)
    assert rank_corr(x, x) == pytest.approx(1.0)


def test_rank_corr_reversed_is_minus_one():
    x = np.arange(100, dtype=float)
    assert rank_corr(x, -x) == pytest.approx(-1.0)


def test_rank_corr_is_invariant_to_monotone_transform():
    """IC 必须只依赖**序**，不依赖值的尺度。

    因子做过缩尾、z-score 还是原值，IC 都应一样 ——
    否则「缩尾用分位、排名用原值」这条决策就会改变结论。
    """
    x = np.random.default_rng(0).normal(size=200)
    y = np.random.default_rng(1).normal(size=200)
    assert rank_corr(x, y) == pytest.approx(rank_corr(x**3, y), abs=1e-12)


def test_rank_corr_too_few_pairs_returns_none():
    """样本太少必须返回 None —— 0 会被读成「没有预测力」。"""
    assert rank_corr(np.arange(5.0), np.arange(5.0)) is None


def test_rank_corr_nan_pairs_dropped_jointly():
    """NaN 成对剔除；两个序列长度不同时直接报错而不是广播。"""
    x = np.arange(100, dtype=float)
    y = np.arange(100, dtype=float)
    x[:20] = np.nan
    got = rank_corr(x, y)
    assert got == pytest.approx(1.0)          # 剩余 80 对仍是完全单调
    with pytest.raises(ValueError):
        rank_corr(np.arange(10.0), np.arange(9.0))


def test_rank_corr_constant_input_is_none_not_zero():
    """某一侧全并列 → 无秩信息。返回 0 会被当成「测了，没信号」。"""
    x = np.zeros(100)
    y = np.arange(100, dtype=float)
    assert rank_corr(x, y) is None


def test_rankdata_uses_average_rank_for_ties():
    got = _rankdata(np.array([5.0, 5.0, 5.0, 9.0]))
    assert list(got) == [1.0, 1.0, 1.0, 3.0]      # 三个 5 并列 → 平均秩 1.0


def test_rankdata_ties_do_not_correlate_with_order():
    """并列值不得与顺序产生伪相关（`turn20` 这类整数因子全靠这条）。"""
    a = np.array([1.0, 1.0, 2.0, 2.0, 3.0, 3.0])
    assert _rankdata(a).tolist() == [0.5, 0.5, 2.5, 2.5, 4.5, 4.5]


# ── daily_ic 分层 ────────────────────────────────────────────────

def _universe(n=200, seed=0):
    rng = np.random.default_rng(seed)
    codes = [f"{i:06d}" for i in range(n)]
    u = pd.DataFrame({"code": codes})
    u["mom20"] = rng.normal(size=n)
    return u


def test_daily_ic_perfect_signal():
    """因子与收益完全同向 → IC = 1。"""
    u = _universe()
    fwd = pd.Series(u["mom20"].to_numpy() * 0.01, index=u["code"])
    got = daily_ic(u, fwd, "mom20")
    assert got["all"] == pytest.approx(1.0)


def test_daily_ic_tail_high_and_low_both_positive_for_monotone_signal():
    u = _universe()
    fwd = pd.Series(u["mom20"].to_numpy() * 0.01, index=u["code"])
    got = daily_ic(u, fwd, "mom20")
    assert got["tail_high"] is not None and got["tail_high"] > 0.9
    assert got["tail_low"] is not None and got["tail_low"] > 0.9


def test_daily_ic_signal_only_in_middle_is_invisible_to_strategy():
    """**尾部归零、中段完美**时，全截面 IC 漂亮但策略拿不到。

    这正是本模块要暴露的形态：策略在 4900 只里只买 top-10，
    尾部相关性为 0 就意味着选股毫无依据。

    构造：中段（|f|<1）收益与因子完全同向；两端（|f|>=1）纯噪声。
    全截面 IC 因此被中段拉高到 ~0.8，而两个尾部分段都塌到 0 附近。
    """
    n = 2000
    rng = np.random.default_rng(7)
    codes = [f"{i:06d}" for i in range(n)]
    f = rng.normal(size=n)
    noise = rng.normal(scale=5.0, size=n) * (np.abs(f) >= 1.0)
    u = pd.DataFrame({"code": codes, "mom20": f})
    fwd = pd.Series(f + noise, index=codes)

    got = daily_ic(u, fwd, "mom20")

    assert got["all"] is not None and got["all"] > 0.5      # 全截面看着很漂亮
    assert got["tail_high"] is not None and abs(got["tail_high"]) < 0.25
    assert got["tail_low"] is not None and abs(got["tail_low"]) < 0.25


def test_daily_ic_too_small_universe_returns_none():
    u = pd.DataFrame({"code": ["A", "B", "C"], "mom20": [1.0, 2.0, 3.0]})
    got = daily_ic(u, pd.Series([0.01, 0.02, 0.03], index=["A", "B", "C"]), "mom20")
    assert got["all"] is None and got["tail_high"] is None


def test_daily_ic_missing_forward_return_drops_that_code():
    """只有部分股票能成交 → 参与计算的样本变少，但不得错位。"""
    u = _universe(200)
    keep = u["code"].tolist()[:100]
    got = daily_ic(u, pd.Series(0.01, index=keep), "mom20")
    assert got["n"] == 100


# ── ic_summary：样本不足必须诚实 ─────────────────────────────────

def test_ic_summary_reports_not_measurable_when_too_few_days():
    rows = [{"factor": "mom20", "window": "lag1_real", "all": 0.01,
             "tail_high": 0.01, "tail_low": 0.01, "n": 100}]
    got = ic_summary(rows, "mom20", "lag1_real")
    assert got["n_days"] == 1
    assert got["all_t"] is None            # 1 天不足以给 t
    assert got["all_mean"] is None         # 也不给均值 —— 避免下游误用


def test_ic_summary_zero_hit_rate_is_a_real_number_not_none():
    """「测了，100% 没信号」是结论，必须给得出，不能被当样本不足吞掉。

    序列必须有波动：全常数时 sd=0，t 无定义 —— 那属于「测不出来」，
    与「测了没信号」是两回事，测试里要能区分开。
    """
    rows = [{"factor": "mom20", "window": "lag1_real",
             "all": (-0.01 if i % 2 == 0 else -0.02),
             "tail_high": None, "tail_low": None, "n": 100}
            for i in range(MIN_OBS + 6)]        # 偶数行：正负各半，均值恰为 -0.015
    got = ic_summary(rows, "mom20", "lag1_real")
    assert got["all_hit"] == pytest.approx(0.0)
    assert got["all_mean"] == pytest.approx(-0.015)
    assert got["all_t"] is not None and got["all_t"] < 0


def test_ic_summary_constant_series_is_not_measurable():
    """全常数 IC 序列 → sd=0 → t 无定义，必须 None 而不是 ±inf。

    分母 0 时报一个大数字，会被读成「极显著」。
    """
    rows = [{"factor": "mom20", "window": "lag1_real", "all": -0.01,
             "tail_high": None, "tail_low": None, "n": 100}
            for _ in range(MIN_OBS + 5)]
    got = ic_summary(rows, "mom20", "lag1_real")
    assert got["all_t"] is None
    assert got["all_mean"] == pytest.approx(-0.01)      # 均值仍然给得出


def test_ic_summary_empty_rows_is_n_days_zero():
    got = ic_summary([], "ivol", "lag1_real")
    assert got == {"factor": "ivol", "window": "lag1_real", "n_days": 0}


def test_ic_summary_filters_by_factor_and_window():
    rows = [{"factor": "mom20", "window": "lag1_real", "all": 0.01,
             "tail_high": 0.0, "tail_low": 0.0, "n": 10}] * (MIN_OBS + 1)
    rows += [{"factor": "mom20", "window": "lag0_leaky", "all": 0.99,
              "tail_high": 0.0, "tail_low": 0.0, "n": 10}] * (MIN_OBS + 1)
    got = ic_summary(rows, "mom20", "lag1_real")
    assert got["n_days"] == MIN_OBS + 1
    assert got["all_mean"] == pytest.approx(0.01)      # 没把 leaky 窗口混进来


def test_min_pairs_is_larger_than_tiny():
    assert MIN_PAIRS >= 10

# ── 快路径：预排序收益序列必须与单因子入口同结果 ──────────────────

def test_daily_ic_ranked_agrees_with_daily_ic():
    """批量扫描走 `daily_ic_ranked`，单因子入口走 `daily_ic`。

    两条路径给出不同的 IC，比只有一条路径更糟 —— 没人知道该信哪个。
    """
    n = 500
    rng = np.random.default_rng(11)
    codes = [f"{i:06d}" for i in range(n)]
    f = rng.normal(size=n)
    u = pd.DataFrame({"code": codes, "mom20": f, "ivol": rng.normal(size=n)})
    fwd = pd.Series(rng.normal(size=n), index=codes)

    for factor in ("mom20", "ivol"):
        slow = daily_ic(u, fwd, factor)
        fast = daily_ic_ranked(u.set_index("code"),
                               make_ranked_forward(u.set_index("code"), fwd),
                               factor)
        assert fast["n"] == slow["n"], factor
        for field in ("all", "tail_high", "tail_low"):
            assert fast[field] == pytest.approx(slow[field], abs=1e-12), (factor, field)


def test_make_ranked_forward_returns_none_when_forward_all_constant():
    """收益全常数 → 无秩信息 → None。不是「相关性为 0」。"""
    u = _universe(200).set_index("code")
    assert make_ranked_forward(u, pd.Series(0.01, index=u.index)) is None


def test_make_ranked_forward_returns_none_when_too_few_have_returns():
    u = _universe(200).set_index("code")
    assert make_ranked_forward(u, pd.Series(dtype=float)) is None


def test_daily_ic_ranked_handles_none_forward():
    u = _universe(200).set_index("code")
    got = daily_ic_ranked(u, None, "mom20")
    assert got == {"all": None, "tail_high": None, "tail_low": None, "n": 0}


def test_daily_ic_ranked_drops_codes_missing_the_factor():
    """因子缺失的股票不得参与 —— 与 `composite_score` 的 min_count 口径一致。"""
    n = 300
    codes = [f"{i:06d}" for i in range(n)]
    rng = np.random.default_rng(3)
    u = pd.DataFrame({"code": codes, "mom20": rng.normal(size=n)})
    u.loc[:49, "mom20"] = np.nan           # 前 50 只缺该因子
    fwd = pd.Series(rng.normal(size=n), index=codes)

    got = daily_ic_ranked(u.set_index("code"),
                          make_ranked_forward(u.set_index("code"), fwd), "mom20")
    assert got["n"] == n - 50


def test_ranked_forward_is_sorted_by_code_not_by_series_order():
    """`RankedForward.codes` 与 `r` 必须严格同行对齐。

    对齐错位不会报错，只会让 IC 变成两个无关序列的相关性——
    这类 bug 的表现形式是「算出来是个小数」，看起来完全正常。
    """
    codes = [f"{i:06d}" for i in range(100)]
    u = pd.DataFrame({"code": codes, "mom20": np.arange(100.0)}).set_index("code")
    fwd = pd.Series(np.arange(100.0), index=codes)
    rf = make_ranked_forward(u, fwd)
    assert rf is not None
    assert len(rf.codes) == 100
    assert np.all(np.diff(rf.r) >= 0)       # 秩必须单调不减
    assert daily_ic_ranked(u, rf, "mom20")["all"] == pytest.approx(1.0)

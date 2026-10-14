"""因子引擎测试：TDD RED 先行。

设计要点（PLATFORM.md §2）：
- 收益类因子必须用后复权价（不复权 × 因子）计算，跨分红不失真
- mom20 动量 / rev5 反转 / vol20 波动 / vsur 量能突变，全部为窗口统计
- cross_section_z 做横截面标准化，供合成分排名
"""
import numpy as np
import pandas as pd
import pytest

from aplus.factor import (
    composite_score,
    compute_factors,
    cross_section_z,
    momentum,
    reversal,
    turnover_mean,
    volatility,
    volume_surge,
)


def _series(values, name="close"):
    return pd.Series(values, name=name)


# ---------- 单序列因子 ----------

def test_momentum_window_return():
    closes = [10.0] * 6 + [11.0]  # 第 7 天涨到 11
    assert momentum(_series(closes), window=5) == pytest.approx(11.0 / 10.0 - 1)


def test_reversal_is_negative_short_momentum():
    closes = [10.0] * 6 + [11.0]
    assert reversal(_series(closes), window=5) == pytest.approx(-(11.0 / 10.0 - 1))


def test_volatility_of_flat_series_is_zero():
    assert volatility(_series([10.0] * 25)) == pytest.approx(0.0)


def test_volatility_known_alternating_series():
    # 构造精确交替 ±10% 的日收益率序列（价格按 (1+pct) 累乘）
    pcts = [0.1, -0.1] * 15
    closes = [100.0]
    for p in pcts:
        closes.append(closes[-1] * (1 + p))
    assert volatility(_series(closes), window=20) == pytest.approx(0.1, rel=1e-9)


def test_volume_surge_last_vs_prior_mean():
    vols = [100.0] * 21 + [300.0]
    assert volume_surge(_series(vols, "volume"), window=20) == pytest.approx(3.0)


def test_turnover_mean_averages_window():
    turns = [1.0] * 20 + [3.0] * 20  # 后 20 天换手 3%
    assert turnover_mean(_series(turns, "turn"), window=20) == pytest.approx(3.0)


def test_compute_factors_includes_turn20_when_turn_present():
    n = 40
    daily = pd.DataFrame({
        "date": [f"2026-01-{i+1:02d}" for i in range(n)],
        "close": [10.0 + i * 0.1 for i in range(n)],
        "volume": [1000.0 + i for i in range(n)],
        "turn": [1.0 + 0.01 * i for i in range(n)],
    })
    factors = compute_factors(daily)
    assert "turn20" in factors.index
    assert factors["turn20"] == pytest.approx(1.0 + 0.01 * (20 + 39) / 2)  # 后20天均值


def test_compute_factors_turn20_nan_when_turn_absent():
    n = 40
    daily = pd.DataFrame({
        "date": [f"2026-01-{i+1:02d}" for i in range(n)],
        "close": [10.0 + i * 0.1 for i in range(n)],
        "volume": [1000.0 + i for i in range(n)],
    })
    factors = compute_factors(daily)
    import math
    assert math.isnan(factors["turn20"])  # 无 turn 列 → NaN → 该股被排名剔除（宁缺毋滥）


# ---------- 横截面 ----------

def test_cross_section_z_standardizes():
    z = cross_section_z(pd.Series([1.0, 2.0, 3.0]))
    assert z.mean() == pytest.approx(0.0, abs=1e-12)
    assert z.std(ddof=0) == pytest.approx(1.0)
    assert z.iloc[2] > z.iloc[1] > z.iloc[0]


# ---------- 复权正确性（因子引擎的命门） ----------

def test_compute_factors_uses_adjusted_returns_across_dividend():
    """分红除权日：不复权 close 从 10 跳到 8，因子 1.0→1.25。
    后复权序列连续（10...10, 10），动量不应出现 -20% 的假摔。"""
    n = 30
    raw_close = [10.0] * 15 + [8.0] * 15
    factor = [1.0] * 15 + [1.25] * 15
    daily = pd.DataFrame({
        "date": [f"2026-01-{i+1:02d}" for i in range(n)],
        "close": raw_close,
        "volume": [1000.0] * n,
        "adjust_factor": factor,
    })
    factors = compute_factors(daily)
    # 期末后复权价 = 8×1.25 = 10，期初 = 10×1.0 = 10 → mom20 = 0
    assert factors["mom20"] == pytest.approx(0.0)


def test_compute_factors_returns_named_row():
    """因子集合已从 5 个扩到 8 个（+turnratio/ivol/rsi）。

    原断言「全部因子非 NaN」的前提已经不成立，且**它本就该不成立**：
    - `ivol` 需要 mkt（等权市场收益）。没传就该是 NaN，绝不用近似值顶替。
    - `turnratio` 需要 252 日均换手，样本只有 40 天。
    造数据把它们填成非 NaN，等于把「诚实地说算不出来」改成「假装算得出」。

    所以拆成三条断言，合计比原来更强：
    8 个因子都在、5 个老因子在足量输入下非 NaN、新因子的 NaN 有明确理由。
    """
    n = 40
    daily = pd.DataFrame({
        "date": [f"2026-01-{i+1:02d}" for i in range(n)],
        "close": [10.0 + i * 0.1 for i in range(n)],
        "volume": [1000.0 + i for i in range(n)],
        "turn": [1.0 + 0.01 * i for i in range(n)],
    })
    factors = compute_factors(daily)
    from aplus.factor import FACTOR_NAMES
    assert set(FACTOR_NAMES) <= set(factors.index), (
        f"缺因子：{set(FACTOR_NAMES) - set(factors.index)}")
    for col in ("mom20", "rev5", "vol20", "vsur", "turn20", "rsi"):
        assert not pd.isna(factors[col]), f"{col} 在足量输入下不该是 NaN"
    # 缺输入的新因子必须诚实地是 NaN，而不是被默认值填上
    assert pd.isna(factors["ivol"]), "没传 mkt 却算出了 ivol —— 这是编造"
    assert pd.isna(factors["turnratio"]), "40 天样本算不出 252 日比值"


def test_compute_factors_ivol_computed_when_mkt_given():
    """给了 mkt 就必须算出 ivol——上一条不是「永远 NaN」。

    必须用**多只**股票构造市场：单只股票自市场时残差方差恒为 0
    （var_r − cov²/var_m = var_r − var_r = 0），ivol 必然是 0，
    用它去断言 `ivol > 0` 会得到一个永远失败的测试。
    """
    from aplus.panel import market_return_series
    n = 60
    rets = {}
    for i, seed in enumerate([7, 8, 9]):
        rng = np.random.default_rng(seed)
        daily = pd.DataFrame({
            "date": [f"2026-01-{d+1:02d}" for d in range(n)],
            "close": 10.0 * np.exp(np.cumsum(rng.normal(0.001, 0.02, n))),
            "volume": [1e6] * n,
        })
        rets[f"60000{i+1}.SH"] = daily["close"].pct_change().rename(daily["date"])
    mkt = market_return_series(rets)
    f = compute_factors(daily, mkt=mkt)
    assert not pd.isna(f["ivol"]), "给了 mkt 却算不出 ivol"
    assert f["ivol"] > 0, "多只股票的市场下 ivol 应为正"


# ---------- 合成分 ----------

def test_composite_score_equal_weight_average():
    """等权合成 = 各因子标准化值的平均，且关于原点反对称。

    **显式传等权正权重**，不靠 `DEFAULT_WEIGHTS`（2026-10-14 修）。
    本条要验的是「合成的平均/对称性质」，与默认配置的方向无关。
    之前它隐式吃 `DEFAULT_WEIGHTS`，于是默认权重一翻符号，这条就红——
    但它红得毫无道理：反对称性（下一行）依然成立，坏掉的只是第一条断言
    的符号，那本来就依赖默认权重的正负。
    """
    equal = {"mom20": 1.0, "rev5": 1.0, "vol20": 1.0, "vsur": 1.0}
    f1 = pd.Series({"mom20": 1.0, "rev5": 1.0, "vol20": 1.0, "vsur": 1.0})
    f2 = pd.Series({"mom20": -1.0, "rev5": -1.0, "vol20": -1.0, "vsur": -1.0})
    scores = composite_score(pd.DataFrame([f1, f2]), weights=equal)
    assert scores.iloc[0] > 0 > scores.iloc[1]
    assert scores.iloc[0] == pytest.approx(-scores.iloc[1])


def test_composite_score_weights_change_ranking():
    """权重可调（回测调参抓手）：mom 权重拉满时它主导排序。"""
    rows = pd.DataFrame([
        {"mom20": 2.0, "rev5": -1.0, "vol20": 0.0, "vsur": 0.0},
        {"mom20": 1.0, "rev5": 2.0, "vol20": 0.0, "vsur": 0.0},
    ])
    mom_only = composite_score(rows, weights={"mom20": 1.0, "rev5": 0.0, "vol20": 0.0, "vsur": 0.0})
    assert mom_only.iloc[0] > mom_only.iloc[1]  # mom 大者胜

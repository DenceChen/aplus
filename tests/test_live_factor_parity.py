"""实盘慢路径必须能算出**候选池里所有**因子，否则晋级是空的。

## 事故背景（2026-10-11 17:25）

2 年终裁的候选第一名是 `{"ivol": -1.0}`，第二三名是含 `turnratio` 的组合。
但实盘走的 `aplus/snapshot.py: cross_rows_from_map` → `aplus/factor.py: compute_factors`
只产出 `mom20 / rev5 / vol20 / vsur / turn20` 五个因子。

于是 `run_daily.py` 里这行：

    up, down = build_predictions(universe, top_n=10, bottom_n=10, weights=weights)

**一旦赛马晋级 `{"ivol": -1.0}`，实盘当天就会 KeyError。**
回测与实盘口径分裂的老问题，这次分裂在「晋级」这个动作上——
它意味着**赛马在选一个平台根本执行不了的策略**。

## 三条纪律

1. 面板算得出来的因子，慢路径**必须**也算得出来；
2. 慢路径算不出来的，就不该进候选池——两边必须同时改；
3. 本文件用**真实文件里的因子集合**做断言，而不是写死一份列表，
   这样以后有人只改一边，本文件立刻红。
"""
import numpy as np
import pandas as pd
import pytest

from aplus.factor import FACTOR_NAMES, compute_factors
from aplus.panel import FACTOR_COLUMNS


def test_live_factor_set_covers_panel_factor_set():
    """面板（回测）有的因子，实盘路径必须也产出。"""
    live = set(FACTOR_NAMES)
    panel = set(FACTOR_COLUMNS)
    missing = panel - live
    assert not missing, (
        f"面板有、实盘没有的因子：{sorted(missing)} —— "
        f"赛马一旦晋级用到它们的权重，实盘当天就会 KeyError"
    )


def test_every_panel_factor_is_actually_produced():
    """不仅名字在，`compute_factors` 必须真的算得出来。"""
    n = 300
    dates = pd.bdate_range("2025-01-01", periods=n).strftime("%Y-%m-%d")
    rng = pd.Series(rng_noise(n), index=range(n))
    daily = pd.DataFrame({
        "date": dates,
        "open": 10 + rng.cumsum() * 0.01,
        "high": 10.2 + rng.cumsum() * 0.01,
        "low": 9.8 + rng.cumsum() * 0.01,
        "close": 10 + rng.cumsum() * 0.01,
        "volume": 1e6 + np.arange(n),
        "turn": 1.5 + np.arange(n) * 0.001,
    })
    mkt = pd.Series(np.random.default_rng(5).normal(0, 0.01, n), index=daily["date"].to_numpy())
    factors = compute_factors(daily, mkt=mkt)
    for name in FACTOR_COLUMNS:
        assert name in factors.index, f"compute_factors 没有产出 {name}"


def rng_noise(n):
    return np.random.default_rng(7).normal(0, 1, n)


def test_new_factor_is_not_silently_all_nan():
    """新增因子不能是恒为 NaN 的占位——那等于「有名字、没实现」。"""
    n = 300
    dates = pd.bdate_range("2025-01-01", periods=n).strftime("%Y-%m-%d")
    step = np.random.default_rng(3).normal(0, 0.02, n).cumsum()
    daily = pd.DataFrame({
        "date": dates, "open": 10 + step, "high": 10.3 + step,
        "low": 9.7 + step, "close": 10 + step,
        "volume": 1e6 + np.arange(n), "turn": 1.5 + np.arange(n) * 0.001,
    })
    # 必须传市场收益序列：`ivol` 没有它时**按设计**返回 NaN，
    # 不该被断言为「实现」。本测试第一版漏传，于是把正确行为判成了失败。
    mkt = pd.Series(np.random.default_rng(5).normal(0, 0.01, n), index=daily["date"].to_numpy())
    factors = compute_factors(daily, mkt=mkt)
    live = [f for f in FACTOR_COLUMNS if f in factors.index]
    for name in live:
        v = pd.to_numeric(factors[name], errors="coerce")
        assert not np.isnan(v), f"{name} 恒为 NaN"

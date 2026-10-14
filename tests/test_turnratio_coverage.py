"""缺陷 26：`turnratio` 因 `rolling(252)` 要求**零缺失**而丢掉 482 只股票。"""
import numpy as np
import pandas as pd
import pytest

from aplus.panel import TURN_RATIO_LONG, MIN_TURN_COVERAGE


def _frame(turn, start="2024-01-01"):
    n = len(turn)
    dates = pd.bdate_range(start, periods=n).strftime("%Y-%m-%d")
    return pd.DataFrame({"date": dates, "code": "000001.SZ", "close": 10.0,
                         "open": 10.0, "high": 10.0, "low": 10.0,
                         "volume": 1e6, "amount": 1e7,
                         "tradestatus": 1, "isST": 0, "turn": turn})


def test_gap_inside_long_window_must_not_kill_the_factor():
    """缺 1 根的老股票不该被整条剔除。

    实测（2025-03-20 全市场）：已上市满 252 日却因缺失被剔的 482 只里，
    **122 只只缺 1 根**，覆盖率中位 96.0%。`rolling(252)` 的
    `min_periods` 默认等于窗口长度，于是 96% 的完整历史被当成没有历史。
    """
    n = TURN_RATIO_LONG
    turn = np.full(n, 1.0)
    turn[n // 2] = np.nan                      # 只缺 1 根
    out = _build(turn)
    assert np.isfinite(out[-1]), (
        "缺 1 根就把 252 日历史整条作废——这是股票池收窄 6.5% 的主因")


def test_coverage_below_threshold_stays_nan():
    """覆盖率低于门槛仍然作废——门槛不是摆设，不能什么数据都放行。"""
    n = TURN_RATIO_LONG
    turn = np.full(n, 1.0)
    turn[: n // 4] = np.nan                     # 只剩 75% 覆盖
    out = _build(turn)
    assert not np.isfinite(out[-1]), "覆盖率不足时必须仍然是 NaN"


def test_full_coverage_is_unaffected():
    """无缺失时结果必须与原实现**逐位相同**——修复不能动正常样本。"""
    n = TURN_RATIO_LONG
    turn = np.linspace(0.5, 2.0, n)
    got = _build(turn)[-1]
    short = pd.Series(turn).rolling(21).mean().iloc[-1]
    long = pd.Series(turn).rolling(TURN_RATIO_LONG).mean().iloc[-1]
    assert got == pytest.approx(short / long - 1.0, rel=1e-12)


def _build(turn_values):
    from aplus.panel import _factor_series
    df = _frame(np.asarray(turn_values, dtype=float))
    return _factor_series(df)["turnratio"].to_numpy()

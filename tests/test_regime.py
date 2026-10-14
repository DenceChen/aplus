"""市场状态自适应权重测试：TDD RED 先行。

6 个月回测实证（PLATFORM.md 迭代日志）：动量权重在 4-7 月有效、8-9 月失效。
regime_weights 按横截面中位动量切分牛市/调整市权重。
"""
import pandas as pd
import pytest

from aplus.factor import regime_weights
from aplus.backtest import walk_forward
from aplus.store import LocalStore
from tests.test_backtest import _populate, _make_stock


BULL = {"mom20": 1.0, "rev5": 0.3, "vol20": 0.5, "vsur": 0.5}
BEAR = {"mom20": 0.3, "rev5": 0.8, "vol20": 0.9, "vsur": 0.3}  # 调整市：反转+低波为主


def _cross(median_mom):
    n = 20
    return pd.DataFrame({
        "code": [f"6000{i:02d}.SH" for i in range(n)],
        "mom20": [median_mom - 0.01 + i * 0.001 for i in range(n)],  # 中位数≈median_mom
        "rev5": [0.0] * n, "vol20": [0.2] * n, "vsur": [1.0] * n,
        "isST": [0] * n, "volume": [1e6] * n, "listed_days": [100] * n,
        "last_date": ["2026-10-09"] * n,
    })


def test_bull_market_gets_bull_weights():
    assert regime_weights(_cross(0.05), BULL, BEAR) == BULL


def test_bear_market_gets_bear_weights():
    assert regime_weights(_cross(-0.08), BULL, BEAR) == BEAR


def test_threshold_boundary():
    # 中位动量恰在阈值 → 熊（保守侧）
    assert regime_weights(_cross(-0.02), BULL, BEAR, threshold=-0.02) == BEAR


def test_walk_forward_accepts_callable_weights(tmp_path):
    """回测支持按日权重函数（regime 切换的载体）——函数收到当日横截面。"""
    store = LocalStore(tmp_path)
    _populate(store)
    dates = store.read_daily("600001.SH")["date"].tolist()
    seen = []

    def selector(cross):
        seen.append(len(cross))
        return BULL

    results = walk_forward(store, dates[60], dates[63], top_n=3, bottom_n=3, weights=selector)
    assert len(results) > 0
    assert len(seen) == 3  # 每个交易日调用一次

"""换手率比值因子（书 3.8 节口径）——与水平值 `turn20` 并存，由样本外赛马裁决。

## 为什么不直接改 `turn20`

书 3.8 节的构造是

    异常换手率 = mean(turn, 21) / mean(turn, 252)

而不是 `mean(turn, 20)`。两者测的不是同一件事：

- `turn20`（水平值）：当前换手**绝对水平**
- 比值：**当前换手相对自身一年基准的异常程度**

## 一个需要被证伪的说法

子代理审计时写：水平值「天然混入市值暴露」，并据此认为比值更优。

但 `turn` 在源站就是**换手率**（成交量 ÷ 流通股本 × 100%），**已经是尺度无关的比率**。
所以「水平值混入市值暴露」这个前提**站不住**——真正可能是：比值因为除以
自身一年均值而**压缩了信号**（分母把它自己抹平了一部分）。

哪种对，只有样本外能回答。所以本测试**不预设结论**：
新因子以独立列 `turnratio` 加入，与 `turn20` 并存，赛马时各自参赛。

## 硬约束：不得引入未来信息

分母 `mean(turn, 252)` 只用**当前及之前** 252 根 K 线，`rolling` 默认右端对齐，
不含未来项。这条由 `test_turnratio_uses_no_future_data` 钉死。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from aplus.panel import FACTOR_COLUMNS, TURN_RATIO_LONG, _factor_series


def _frame(turns, closes=None, dates=None):
    n = len(turns)
    dates = dates if dates is not None else [f"2026-{i // 28 + 1:02d}-{i % 28 + 1:02d}"
                                              for i in range(n)]
    closes = closes if closes is not None else np.full(n, 10.0)
    return pd.DataFrame({"date": dates, "close": closes, "open": closes,
                         "volume": [1e6] * n, "turn": turns,
                         "adjust_factor": 1.0})


# ── 基本形态 ────────────────────────────────────────────────────────────────

def test_turnratio_is_short_over_long_window():
    """比值 = 近 21 日均换手 ÷ 近 252 日均换手。"""
    n = 400
    turns = np.full(n, 2.0)
    turns[-21:] = 6.0                       # 最近一个月换手翻三倍
    out = _factor_series(_frame(turns))
    r = float(out["turnratio"].dropna().iloc[-1])
    # 本实现减 1 居中：「偏离自身基准多少」。z-score 会消掉常数，下游排序无差异。
    # 分母不是 2.0：被抬升的那 21 天**本身也在 252 日窗口内**，
    # 所以长窗均值 = (231×2 + 21×6) / 252 = 2.333。
    long_mean = turns[-TURN_RATIO_LONG:].mean()
    assert long_mean == pytest.approx(2.3333, abs=1e-3), "夹具算术要自己对上"
    assert r == pytest.approx(6.0 / long_mean - 1.0, rel=1e-6), (
        f"异常度算错：实得 {r:.4f}"
    )


def test_turnratio_is_one_when_turnover_is_steady():
    """长期稳定的换手 → 比值 ≈ 1（无异常）。"""
    turns = np.full(400, 3.0)
    out = _factor_series(_frame(turns))
    r = float(out["turnratio"].dropna().iloc[-1])
    assert r == pytest.approx(0.0, abs=0.02), f"稳定换手的异常度应为 0，实得 {r:.2f}"


def test_turnratio_is_scale_free():
    """**这是水平值与比值的核心区别**，必须钉死。

    同一套换手**形态**，整体放大 10 倍（即流通股本缩到 1/10）：
    - `turn20` 会放大 10 倍
    - `turnratio` 不得变

    若比值也变了，说明分母没有起到归一化作用，那这个因子就没有意义。
    """
    base = np.full(400, 2.0)
    base[-21:] = 6.0
    small = _factor_series(_frame(base))
    big = _factor_series(_frame(base * 10))
    assert float(small["turnratio"].dropna().iloc[-1]) == pytest.approx(
        float(big["turnratio"].dropna().iloc[-1]), rel=1e-9)
    # 水平值确实会跟着放大（这正是两者要分开测量的东西）
    assert float(big["turn20"].dropna().iloc[-1]) == pytest.approx(
        float(small["turn20"].dropna().iloc[-1]) * 10, rel=1e-9)


# ── 防泄露 ──────────────────────────────────────────────────────────────────

def test_turnratio_uses_no_future_data():
    """把 D 之后的换手率全部改掉，D 当天的 `turnratio` 不得有任何变化。"""
    n = 400
    rng = np.random.default_rng(5)
    turns = rng.uniform(1.0, 5.0, n)
    closes = 10.0 * np.cumprod(1 + rng.normal(0, 0.01, n))
    dates = [f"d{i:04d}" for i in range(n)]

    base = _factor_series(_frame(turns, closes, dates))
    mutated = turns.copy()
    mutated[300:] = 999.0                   # 只污染最后 100 根
    after = _factor_series(_frame(mutated, closes, dates))

    assert after["turnratio"].iloc[299] == base["turnratio"].iloc[299], (
        "改未来数据改变了过去的因子值 —— 有前视"
    )


# ── 边界 ────────────────────────────────────────────────────────────────────

def test_turnratio_nan_before_long_window_fills():
    """252 日窗口没满 → NaN，不得用小样本硬算。"""
    out = _factor_series(_frame(np.full(100, 2.0)))
    assert out["turnratio"].isna().all()


def test_turnratio_nan_without_turn_column():
    """没有 turn 列 → NaN（该股退出排名），**不是** 0。"""
    frame = pd.DataFrame({"date": [f"d{i}" for i in range(400)],
                          "close": np.full(400, 10.0), "volume": [1e6] * 400,
                          "adjust_factor": 1.0})
    out = _factor_series(frame)
    assert out["turnratio"].isna().all()


def test_zero_long_window_mean_is_not_infinite():
    """分母为 0（长期零成交）→ NaN，绝不返回 inf。

    `_safe_ratio` 是全项目的防 inf 地雷；新因子必须走同一条路。
    """
    turns = np.zeros(400)
    turns[-21:] = 5.0
    out = _factor_series(_frame(turns))
    v = out["turnratio"].to_numpy(dtype=float)
    assert not np.isinf(v).any(), "长期零成交造出了 inf"
    finite = v[np.isfinite(v)]
    assert len(finite) > 0
    assert np.isfinite(finite).all(), "有非有限值混在里面"


def test_turnratio_registered_as_factor_column():
    """必须注册进 FACTOR_COLUMNS，否则面板不会携带它、赛马会静默看不到。"""
    assert "turnratio" in FACTOR_COLUMNS

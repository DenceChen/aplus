"""回测必须拒绝**价格不可信**的成交——否则一只 −96% 的脏数据就能伪造 alpha。

## 事故经过（2026-10-11）

赛马跑完，18 组候选的测试段涨腿超额**全部为负**（−0.09% ~ −0.96%），
裁决正确地全部否决了。看起来没问题——直到看多空价差那一列：

| 排名 | 权重 | 测试涨腿超额 | 测试多空价差 | 价差 t |
|---|---|---|---|---|
| 4 | `{mom20:1.0, rev5:0.3, ivol:-0.8}` | −0.09% | **+9.09%** | **7.68** |
| 6 | `{mom20:0.5, rev5:0.3, vol20:0.3, ivol:-0.5}` | −0.28% | **+7.08%** | **6.50** |
| 10 | `{mom20:1.4, rev5:0.6, vol20:0.5, vsur:0.5}` | −0.43% | **+7.31%** | **6.35** |
| 11 | `{mom20:1.4, rev5:0.6, vol20:0.5, vsur:0.5}` | −0.48% | **+9.13%** | **7.30** |

**日频多空价差 +9% 在真实市场不存在**（年化 300 倍）。而且 t = 7.68 ——
看起来比任何一个「真 alpha」都显著。

## 根因

全库扫描复权日收益，**173 只股票（3.2%）存在 |单日收益| > 25%**，最大 −96.4%：

    600766.SH  2024-06-05  close 9.84 → 0.35   pctChg -96.4431
    600608.SH  2026-06-08  close 2.07 → 0.20   pctChg -90.3382
    300029.SZ  2026-06-18  close 3.46 → 0.20
    002898.SZ  2026-06-26  close 8.74 → 0.35

形态高度一致：停牌若干日（`tradestatus=0`、`volume=0`、close 冻结）→ 复牌
当日价格直接掉到零头。前一根的复权因子与后一根**完全相同**（3.7392 → 3.7392），
所以这**不是**复权因子没记录，而是源站把一次缩股/送股记成了价格跳空，
连它自己的 `pctChg` 也是按同一对 close 算出来的，不构成独立证据。

这类股票 20 日动量为负 → 被排进**跌篮子** → 一只 −96% 把
`market − avg_down` 顶到 +96%，摊到几天的均值就是 +9%。

## 为什么必须处理

1. **它会伪造 alpha**。上面的 +9% 价差、t=7.68 是纯数据artifact。
   本轮晋级闸用的是涨腿超额（本来就全负），所以**没有**被它骗到；
   但只要哪天有人把「多空价差显著」当成晋级依据，这只股票就会
   把一个垃圾权重推上线。
2. **它污染一切均值类指标**。日均超额、命中率、价差，统统被少数几个
   不可信价格绑架。
3. **它不是真实交易**。你没法按 −96% 的价格成交；那是一个数据缺陷，
   不是一次亏损。

## 处理口径

A 股真实单日涨跌停是有限的：主板 ±10%、创业板/科创板 ±20%、
北交所 ±30%；退市整理期也是 ±10%。**±40% 之外不存在合法的单日收益**。

所以：`MAX_PLAUSIBLE_RETURN = 0.40`。超过即判**价格不可信 → 不成交**
（剔除该 code-day），而不是截断成一个「看起来合理」的值——截断会把
一个 −96% 变成 −40%，仍然污染均值，而且掩盖了数据有问题的真相。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from aplus.backtest import MAX_PLAUSIBLE_RETURN, _next_bars


def _daily(code: str, closes: list) -> pd.DataFrame:
    dates = pd.bdate_range("2024-01-02", periods=len(closes)).strftime("%Y-%m-%d")
    return pd.DataFrame({
        "date": dates, "code": code,
        "open": closes, "high": closes, "low": closes, "close": closes,
        "volume": [1e6] * len(closes), "amount": [1e7] * len(closes),
        "tradestatus": [1] * len(closes), "isST": [0] * len(closes),
        "turn": [1.0] * len(closes), "pctChg": [0.0] * len(closes),
    })


# ── 核心：不可信价格不成交 ──────────────────────────────────────────────────

def test_impossible_price_jump_is_not_traded():
    """复牌日 −96%（9.84 → 0.35）必须被拒绝成交，而不是记成 −96% 的亏损。"""
    assert MAX_PLAUSIBLE_RETURN == 0.40
    daily = _daily("600766.SH", [9.84, 9.84, 0.35, 0.38, 0.42, 0.40])
    bars = _next_bars({"600766.SH": daily}, ["600766.SH"],
                      after_date=daily["date"].iloc[0], entry_lag=1)
    assert len(bars) == 0, (
        "一只单日 −96% 的股票被记成了可成交 —— "
        "它会把日均超额和多空价差同时污染（本轮实测把价差顶到 +9%、t=7.68）"
    )


def test_impossible_price_jump_upward_is_also_rejected():
    """反方向同样要挡：+491% 的虚高动量会把股票塞进涨篮子顶端。"""
    daily = _daily("001914.SZ", [10.80, 9.96, 60.0, 62.0, 63.0, 64.0])
    bars = _next_bars({"001914.SZ": daily}, ["001914.SZ"],
                      after_date=daily["date"].iloc[0], entry_lag=1)
    assert len(bars) == 0


def test_plausible_but_large_move_is_still_traded():
    """防呆：创业板 ±20% 的涨停**必须**保留，不能被这个规则误杀。

    这条测试的价值在于「不要为了挡住 −96% 而把 +20% 也挡了」——
    那样会把真实的大波动全部丢掉，系统性地削平所有信号。
    """
    daily = _daily("300750.SZ", [10.00, 12.00, 14.40, 13.90, 14.50, 15.00])
    bars = _next_bars({"300750.SZ": daily}, ["300750.SZ"],
                      after_date=daily["date"].iloc[0], entry_lag=1)
    assert len(bars) == 1
    got = float(bars["close"].iloc[0]) / float(bars["prev_close"].iloc[0]) - 1
    assert got == pytest.approx(0.20)


def test_normal_small_move_unaffected():
    daily = _daily("600000.SH", [10.00, 10.05, 10.10, 9.98, 10.02, 10.01])
    bars = _next_bars({"600000.SH": daily}, ["600000.SH"],
                      after_date=daily["date"].iloc[0], entry_lag=1)
    assert len(bars) == 1
    got = float(bars["close"].iloc[0]) / float(bars["prev_close"].iloc[0]) - 1
    assert got == pytest.approx(10.10 / 10.05 - 1)


def test_one_bad_pair_does_not_kill_the_rest_of_the_day():
    """只有撞上跳空的那一只被剔除，同日其他标的照常成交。"""
    dm = {
        "600766.SH": _daily("600766.SH", [9.84, 9.84, 0.35, 0.38, 0.42, 0.40]),
        "600000.SH": _daily("600000.SH", [10.00, 10.05, 10.10, 9.98, 10.02, 10.01]),
    }
    bars = _next_bars(dm, ["600766.SH", "600000.SH"],
                      after_date=dm["600000.SH"]["date"].iloc[0], entry_lag=1)
    assert list(bars["code"]) == ["600000.SH"]


def test_zero_or_negative_price_is_rejected():
    """价格 ≤ 0 时比值无意义，直接拒绝（不能用除法「算出」一个数）。"""
    for bad in ([10.0, 10.0, 0.0, 0.0, 0.0, 0.0], [10.0, 10.0, -1.0, -1.0, -1.0, -1.0]):
        daily = _daily("600001.SH", bad)
        bars = _next_bars({"600001.SH": daily}, ["600001.SH"],
                          after_date=daily["date"].iloc[0], entry_lag=1)
        assert len(bars) == 0, f"价格 {bad[2]} 被当成了可成交价"


# ── 因子层同一道闸：实盘横截面原本一道都没有 ─────────────────────────────────

def test_factor_layer_also_voids_implausible_return():
    """`+462.51%` 的复权跳空必须让因子变 NaN，而不是变成 +462% 的动量。

    实测 `302132.SZ` 2025-02-18：因子 1.0 → 5.9757，复权收益 +462.51%。
    它的 20 日动量会因此冲到榜首——与当天真实发生过的
    `mom20=+903.72%` 进入用户建仓清单是同一个失效路径，
    而回测侧有成交闸门、**实盘横截面原本一道都没有**。
    """
    from aplus.panel import _factor_series

    n = 40
    idx = [f"d{i:02d}" for i in range(n)]
    close = np.linspace(100.0, 110.0, n)
    close[20] = close[19] * 5.6                       # 单日 +460%
    frame = pd.DataFrame({"date": idx, "close": close.tolist(),
                          "open": close.tolist(),
                          "volume": [1e6] * n, "adjust_factor": 1.0})
    out = _factor_series(frame)
    mom = out["mom20"]
    assert mom.isna().any(), "跳空日的动量因子没有被作废"
    assert float(mom.max()) < 5.0, (
        f"mom20 最大仍有 {float(mom.max()):.0%} —— 虚高动量会把这只股票"
        f"顶上「预测涨」榜首"
    )


def test_factor_layer_still_keeps_legitimate_limit_up_moves():
    """防呆：创业板 ±20% 的涨停**必须**保留。

    这条测试的意义在于「不要为了挡 +462% 而把 +20% 也挡掉」——
    那样会把真实的大波动全丢掉，系统性地削平所有信号。
    """
    from aplus.panel import _factor_series

    n = 40
    idx = [f"d{i:02d}" for i in range(n)]
    rng = np.random.default_rng(4)
    ret = rng.normal(0.0002, 0.012, n)
    ret[20] = 0.20                                      # 创业板涨停
    assert abs(ret).max() < 0.40
    close = 100.0 * np.cumprod(1 + ret)
    frame = pd.DataFrame({"date": idx, "close": close.tolist(),
                          "open": close.tolist(),
                          "volume": [1e6] * n, "adjust_factor": 1.0})
    out = _factor_series(frame)
    truth = float(close[-1] / close[-21] - 1)          # 真实 20 日累计收益
    # mom20 前 20 行必然是 NaN（shift(20) 需要历史），只关心窗口成形之后
    assert out["mom20"].iloc[20:].notna().all(), "涨停不该导致因子缺失"
    assert float(out["mom20"].iloc[-1]) == pytest.approx(truth, abs=1e-9), (
        f"涨停被误伤：mom20 应等于真实 20 日累计 {truth:.2%}"
    )

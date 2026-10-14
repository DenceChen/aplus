"""复权基准未知的时期必须**作废**，不得按 1.0 填充后继续用。

## 为什么不能填 1.0

`attach_factor` 用 `merge_asof(backward)` 把因子事件贴到日线上，首事件之前没有
值可贴，于是按 **1.0** 填充。当首事件的因子本来就 >1（重组、借壳、缩股之后
基准才重新起算）时，这个 1.0 是一段**尺度未知**的历史——把它当成真值，
就会在第一个事件日凭空造出一次巨幅收益。

实测样本 `001914.SZ`（招商积余）：

    日线首根  2019-01-02   close 10.80   因子填充 1.0000
    首事件    2019-06-06   close  9.96   因子     6.4111
    → 该日复权收益 = +491.25%

`scripts/repair_factors.py` 重取后事件数仍是 11 条不变——源站确实只给这些，
早期基准无法从该源恢复。所以**不能修，只能作废**。

## 为什么不能靠「挡住预测」了事

因子体检是**闸门**：有任何一只损坏就不发布推荐（见 tests/test_factor_gate.py）。
实测损坏率 2/5447 = 0.04%——为了两只股票让整个平台每天出不了名单，
闸门会被绕过、被忽略，最后谁都不信它。**一个天天误报的闸门等于没有闸门。**

## 本测试钉死的方案

尺度未知的时期标为**不可用**（复权价 NaN → 因子 NaN → 该股在该时期
自然退出横截面），而不是「填 1.0 然后小心使用」。

代价是丢掉那两只股票早期的样本；换来的是：
- 闸门只在真正大面积损坏时才拦（它的信誉得以保全）
- 榜单上不会出现动量虚高 491% 的标的
- 作废是**可证伪的**（有明确的 NaN），而填 1.0 是**不可证伪的**

对照：`001872.SZ`（招商港口，首事件 2018-12-26 早于日线首根 2019-01-02）
和 `600018.SH`（上港集团，首事件 2006-10-26）**不受影响**——
它们的基准覆盖了全部日线，没有未知段。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from aplus.adjust import UNKNOWN_SCALE_MIN_FACTOR, attach_factor, hfq_close


def _daily(closes, start="2024-01-02"):
    dates = pd.bdate_range(start, periods=len(closes)).strftime("%Y-%m-%d")
    return pd.DataFrame({"date": dates, "close": closes,
                         "open": closes, "volume": [1e6] * len(closes)})


def _events(pairs):
    return pd.DataFrame({"date": [p[0] for p in pairs],
                         "adjust_factor": [p[1] for p in pairs]})


def _daily_n(n, start="2024-01-02", first=10.0):
    dates = pd.bdate_range(start, periods=n)
    return pd.DataFrame({"date": dates.strftime("%Y-%m-%d"),
                         "close": [first + 0.01 * i for i in range(n)],
                         "open": [first + 0.01 * i for i in range(n)],
                         "volume": [1e6] * n})


# ── 核心：未知尺度期作废 ────────────────────────────────────────────────────

def test_unknown_scale_period_is_nan_not_one():
    """首事件因子 >1 且晚于日线起点 → 该段因子必须是 NaN，不能是 1.0。

    夹具与真实情形同构：日线 40 个交易日，首事件落在第 20 天（因子 6.4111）。
    """
    d = _daily_n(40)
    first_event = d["date"].iloc[20]
    f = _events([(first_event, 6.4111), (d["date"].iloc[30], 6.5)])
    merged = attach_factor(d, f)
    head = merged["adjust_factor"].iloc[:20]
    assert head.isna().all(), (
        f"首事件之前被填成 {merged['adjust_factor'].iloc[0]} —— "
        f"那是尺度未知的编造值，会在首事件日造出 +491% 假收益"
    )
    assert merged["adjust_factor"].iloc[30] == 6.5, "首事件之后必须照常有值"
    assert not merged["adjust_factor"].iloc[20:].isna().any(), "首事件之后不得有缺口"


def test_unknown_scale_produces_no_phantom_return():
    """作废后跨首事件的复权收益必须**消失**（NaN），而不是 +491%。"""
    d = _daily_n(40)
    first_event = d["date"].iloc[20]
    # 首事件当天把价格腰斩，模拟 001914.SZ 的 10.80 → 9.96
    d.loc[20:, "close"] = d.loc[20:, "close"] * 0.92
    f = _events([(first_event, 6.4111), (d["date"].iloc[30], 6.5)])
    adj = hfq_close(d, f)
    ret = adj.pct_change()
    assert np.isnan(ret.iloc[20]), (
        f"首事件日复权收益 {ret.iloc[20]:+.1%} —— 这正是 001914.SZ 的假跳空"
    )
    # 作废后首事件之后的收益是正常的，不再有 5 倍量级的跳变
    tail = ret.iloc[21:].dropna()
    assert tail.abs().max() < 0.10, f"作废后仍残留假跳变：{tail.abs().max():.1%}"


def test_backdoor_listing_with_full_coverage_is_untouched():
    """首事件早于日线首根 → 无未知段 → 一行都不能作废。

    招商港口（首事件 2018-12-26，日线首根 2019-01-02）就是这种：
    它的因子是 3.53 不是 1.0，但基准**覆盖了全部日线**，完全健康。
    若把这类也作废，等于误杀正常数据——比漏检更糟。
    """
    d = _daily([9.84, 9.80, 9.60, 9.50, 9.40, 9.30], start="2019-01-02")
    f = _events([("2018-12-26", 3.526478), ("2019-07-10", 3.549609)])
    merged = attach_factor(d, f)
    assert not merged["adjust_factor"].isna().any(), (
        "基准覆盖全部日线却被作废 —— 误杀"
    )
    assert merged["adjust_factor"].iloc[0] == 3.526478


def test_normal_table_starting_at_one_point_zero_is_untouched():
    """基准为 1.0 的常规表（baostock 上市日基准）不该被当成未知。"""
    d = _daily([10.0, 10.1, 10.2, 10.3, 10.4, 10.5], start="2024-01-02")
    f = _events([("2024-01-02", 1.0), ("2024-03-01", 1.2)])
    merged = attach_factor(d, f)
    assert not merged["adjust_factor"].isna().any()
    assert merged["adjust_factor"].iloc[0] == 1.0


# ── 边界 ────────────────────────────────────────────────────────────────────

def test_small_first_factor_is_not_treated_as_unknown():
    """首事件因子只略大于 1（很可能是真·分红累积）时不得作废。

    阈值 `UNKNOWN_SCALE_MIN_FACTOR` 太大就会把大量正常表打成 NaN，
    那等于用「数据缺失」掩盖一切。宁可不作废，也要留给闸门去报。
    """
    assert 1.0 < UNKNOWN_SCALE_MIN_FACTOR <= 1.5, "阈值必须保守"
    d = _daily([10.0, 10.1, 10.2, 10.3, 10.4, 10.5], start="2024-01-02")
    f = _events([("2024-01-20", 1.02), ("2024-03-01", 1.1)])
    merged = attach_factor(d, f)
    assert not merged["adjust_factor"].isna().any()


def test_event_on_first_daily_bar_is_known_scale():
    """首事件恰好落在日线首根 → 无未知段。"""
    d = _daily([10.0, 10.1, 10.2, 10.3, 10.4, 10.5], start="2024-01-02")
    f = _events([("2024-01-02", 3.5), ("2024-03-01", 3.6)])
    merged = attach_factor(d, f)
    assert not merged["adjust_factor"].isna().any()
    assert merged["adjust_factor"].iloc[0] == 3.5


def test_empty_or_missing_events_still_yields_one_point_zero():
    """无事件时恒为 1.0（老行为不变）——那不是未知，而是真的没有除权。"""
    d = _daily([10.0, 10.1, 10.2])
    assert attach_factor(d, None)["adjust_factor"].tolist() == [1.0, 1.0, 1.0]
    assert attach_factor(d, pd.DataFrame())["adjust_factor"].tolist() == [1.0, 1.0, 1.0]


def test_row_count_and_order_preserved():
    """作废只改因子取值，不得改变行数与行序——横截面按行定位。"""
    d = _daily([10.0, 10.1, 10.2, 10.3, 10.4])
    f = _events([("2024-01-18", 5.0)])
    merged = attach_factor(d, f)
    assert len(merged) == len(d)
    assert merged["date"].tolist() == d["date"].tolist()

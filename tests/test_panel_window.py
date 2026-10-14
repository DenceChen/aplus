"""`build_panel` 的窗口契约：end 是**开区间**，静默返回空是事故源头。

## 事故（2026-10-11）

`build_cross_section` 写的是 `build_panel(store, as_of, as_of, ...)`。
而 `build_panel` 的日期筛选是 `start <= d < end`——**开区间**。
于是 `start == end` 直接返回 `dates == []`，
`cross_section(0)` 抛 `IndexError: index 0 is out of bounds`，
实盘横截面返回 **0 行**：当天不出名单，或者更糟：出的是别的路径的旧名单。

这类「传错一个参数 → 静默变成空 → 上层拿到空壳还照常返回」的事故，
本轮已经发生四次（可交易性列缺失、ivol 口径分叉、缩尾并列、末根元数据 NaN）。
共同形状都是：**输入不合法时函数不吭声，把问题推到很远的地方才炸。**

所以这里钉两条：
1. `start >= end` 必须**当场抛错**，不许返回空面板
2. 实盘必须显式算出「包含 as_of 的合法窗口」并断言目标日在轴上
"""
import pandas as pd
import pytest

from aplus.panel import build_panel
from aplus.snapshot import build_cross_section, panel_window_for
from aplus.store import LocalStore

from tests.test_backtest import _noisy_stock


def _store(tmp_path, n=4):
    store = LocalStore(tmp_path)
    for i in range(n):
        store.upsert_daily(f"60000{i+1}.SH", _noisy_stock(f"60000{i+1}.SH", drift=0.01, seed=i))
    return store


def test_start_equal_to_end_raises_instead_of_empty_panel(tmp_path):
    """start == end → 空 dates → 上层 IndexError。必须当场炸。"""
    store = _store(tmp_path)
    ref = store.read_daily("600001.SH")
    as_of = str(ref["date"].astype(str).iloc[-1])
    with pytest.raises(ValueError, match="开区间|半开"):
        build_panel(store, as_of, as_of)


def test_start_after_end_raises(tmp_path):
    store = _store(tmp_path)
    ref = store.read_daily("600001.SH")
    dates = sorted(ref["date"].astype(str).unique())
    with pytest.raises(ValueError):
        build_panel(store, dates[-1], dates[0])


def test_panel_window_for_returns_window_that_contains_as_of(tmp_path):
    """实盘算出来的窗口必须**真的包含** as_of 这一天。"""
    store = _store(tmp_path)
    ref = store.read_daily("600001.SH")
    all_dates = sorted(ref["date"].astype(str).unique())
    target = all_dates[-1]

    start, end = panel_window_for(all_dates, target)
    panel = build_panel(store, start, end)
    assert panel.dates, "窗口算出来是空的"
    assert target in panel.dates
    # 末尾锚定：实盘要的是目标日，不是窗口里的任意一天
    assert panel.dates[-1] == target


def test_panel_window_for_handles_non_trading_day(tmp_path):
    """as_of 落在非交易日（如周日）时，必须回退到**最后一个交易日**。"""
    store = _store(tmp_path)
    ref = store.read_daily("600001.SH")
    all_dates = sorted(ref["date"].astype(str).unique())
    target = all_dates[-1]
    # 造一个比所有交易日都大的非交易日
    weekend = "2099-12-31"

    start, end = panel_window_for(all_dates, weekend)
    panel = build_panel(store, start, end)
    assert panel.dates[-1] == target, "非交易日必须回退到最近的交易日，不能变空"


def test_panel_window_for_window_is_bounded(tmp_path):
    """窗口必须有界：全历史轴会顶爆内存（5447×1700×12×8B ≈ 889 MB）。"""
    store = _store(tmp_path)
    ref = store.read_daily("600001.SH")
    all_dates = sorted(ref["date"].astype(str).unique())
    start, end = panel_window_for(all_dates, all_dates[-1], width=3)
    panel = build_panel(store, start, end)
    assert len(panel.dates) == 3


def test_build_cross_section_is_not_empty(tmp_path):
    """端到端哨兵：实盘横截面必须出行数。空面板 = 当天没有名单。"""
    store = _store(tmp_path, n=6)
    df = build_cross_section(store)
    assert len(df) > 0
    assert {"code", "mom20", "ivol", "rsi", "buyable"} <= set(df.columns)


def test_build_cross_section_on_historical_day_matches_reference(tmp_path):
    """实盘复用面板后，必须与参照实现逐因子一致（不只是不空）。"""
    from aplus.snapshot import cross_rows_from_map

    store = _store(tmp_path, n=6)
    ref = store.read_daily("600001.SH")
    as_of = str(ref["date"].astype(str).iloc[-1])

    live = build_cross_section(store, as_of=as_of).set_index("code").sort_index()
    slow = cross_rows_from_map(
        {p.stem: store.read_daily(p.stem)
         for p in sorted((store.root / "master" / "daily").glob("*.parquet"))},
        as_of=as_of,
    ).set_index("code").sort_index()

    common = live.index.intersection(slow.index)
    assert len(common) > 0
    for col in ["mom20", "rev5", "vol20", "vsur", "rsi", "ivol", "turnratio"]:
        if col not in live.columns or col not in slow.columns:
            continue
        a = live.loc[common, col].astype(float)
        b = slow.loc[common, col].astype(float)
        both_nan = a.isna() & b.isna()
        close = (a - b).abs() <= 1e-9 + 1e-6 * b.abs()
        assert (close | both_nan).all(), f"{col} 两条路径分叉：{a[~close | both_nan].head().to_dict()}"

"""实盘慢路径也必须带 `buyable` —— 今天推出去的名单里不能有买不进的票。

## 事故背景

`aplus/panel.py` 的 `can_buy` / `buyable_series` 已实现并接入回测横截面，
但实盘走的是**另一条路径**：

    scripts/run_daily.py:170
        cross = build_cross_section(store, names_map=names_map)   # → aplus/snapshot.py
        universe = filter_universe(cross)                          # → aplus/predict.py

`aplus/snapshot.py: cross_rows_from_map` 的 `COLUMNS` 里**没有 `buyable`**，
于是 `filter_universe` 看不到这一列。实测后果：2026-10-11 的实盘名单第一是
`600825.SH 新华传媒`——连续 **9 个一字涨停**、综合分 7.56、二十日动量 +141.9%，
**收盘价就是涨停价，排单进不去**。

回测已经不再为这种票记账，实盘却还在推荐它——
**回测与实盘不同口径，等于刚修好的那道防线在真正的出单口形同虚设。**

## 为什么必须 fail-closed

`filter_universe` 在 `buyable` 列缺失时**不能**把整池清空（那会让预测静默产出空名单），
也**不能**默认放行（那等于把这次的修复做成一次性）。
正确做法是：列缺失时放行并留下可观测信号，列存在时严格执行。
这条纪律已被 `tests/test_tradability.py` 的
`test_missing_tradability_column_must_not_wipe_out_the_pool` 钉住，
本文件钉的是另一半：**列必须真的被产出来**。
"""
import pandas as pd
import pytest

from aplus.panel import can_buy
from aplus.snapshot import COLUMNS, cross_rows_from_map


def _daily(rows):
    """构造日线：rows = [(date, o, h, l, c, vol), ...]"""
    return pd.DataFrame({
        "date": [r[0] for r in rows],
        "code": ["600000.SH"] * len(rows),
        "open": [r[1] for r in rows],
        "high": [r[2] for r in rows],
        "low": [r[3] for r in rows],
        "close": [r[4] for r in rows],
        "volume": [r[5] for r in rows],
    })


def test_columns_declares_buyable():
    """列清单必须声明 buyable——它是 filter_universe 的输入契约。"""
    assert "buyable" in COLUMNS


def test_cross_rows_emits_buyable_column():
    rows = [(f"2026-01-{i:02d}", 10.0, 10.2, 9.9, 10.0, 1_000_000) for i in range(1, 61)]
    out = cross_rows_from_map({"600000.SH": _daily(rows)})
    assert "buyable" in out.columns
    assert len(out) == 1
    assert out.iloc[0]["buyable"] is True or out.iloc[0]["buyable"] == True  # noqa: E712


def test_sealed_limit_up_on_last_bar_is_not_buyable():
    """最后一根是一字涨停 → 实盘横截面必须把它标成不可买。"""
    rows = [(f"2026-01-{i:02d}", 10.0, 10.2, 9.9, 10.0, 1_000_000) for i in range(1, 61)]
    # 最后一天：10.00 → 11.00 涨停，且一字（o=h=l=c=11.00）
    rows.append(("2026-03-01", 11.0, 11.0, 11.0, 11.0, 1_000_000))
    out = cross_rows_from_map({"600000.SH": _daily(rows)})
    assert bool(out.iloc[0]["buyable"]) is False, "一字涨停仍被标为可买 → 实盘会推出买不进的票"


def test_limit_up_close_even_after_opening_is_not_buyable():
    """盘中打开过、但收在涨停价上 → 收盘集合竞价仍排不进队。"""
    rows = [(f"2026-01-{i:02d}", 10.0, 10.2, 9.9, 10.0, 1_000_000) for i in range(1, 61)]
    rows.append(("2026-03-01", 10.5, 11.0, 10.2, 11.0, 1_000_000))
    out = cross_rows_from_map({"600000.SH": _daily(rows)})
    assert bool(out.iloc[0]["buyable"]) is False


def test_halted_last_bar_is_not_buyable():
    """停牌（零成交）→ 不可买。"""
    rows = [(f"2026-01-{i:02d}", 10.0, 10.2, 9.9, 10.0, 1_000_000) for i in range(1, 61)]
    rows.append(("2026-03-01", 10.0, 10.0, 10.0, 10.0, 0))
    out = cross_rows_from_map({"600000.SH": _daily(rows)})
    assert bool(out.iloc[0]["buyable"]) is False


def test_normal_bar_is_buyable():
    """普通交易日必须放行——否则这是一道天天误报的闸门，等于没有闸门。"""
    rows = [(f"2026-01-{i:02d}", 10.0, 10.2, 9.9, 10.0, 1_000_000) for i in range(1, 61)]
    rows.append(("2026-03-01", 10.0, 10.3, 9.95, 10.1, 1_000_000))
    out = cross_rows_from_map({"600000.SH": _daily(rows)})
    assert bool(out.iloc[0]["buyable"]) is True


def test_slow_path_agrees_with_panel_judgement():
    """两条路径（回测 panel / 实盘 snapshot）必须给出同一个答案。

    如果它们对同一根 K 线判断不同，就意味着回测和实盘又一次不同口径——
    那正是本轮修掉的所有事故的共同形状。
    """
    rows = [(f"2026-01-{i:02d}", 10.0, 10.2, 9.9, 10.0, 1_000_000) for i in range(1, 61)]
    cases = {
        "一字涨停": ("2026-03-01", 11.0, 11.0, 11.0, 11.0, 1_000_000),
        "涨停收盘": ("2026-03-01", 10.5, 11.0, 10.2, 11.0, 1_000_000),
        "一字跌停": ("2026-03-01", 9.0, 9.0, 9.0, 9.0, 1_000_000),
        "停牌": ("2026-03-01", 10.0, 10.0, 10.0, 10.0, 0),
        "正常": ("2026-03-01", 10.0, 10.3, 9.95, 10.1, 1_000_000),
    }
    for name, last in cases.items():
        out = cross_rows_from_map({"600000.SH": _daily(rows + [last])})
        snap = bool(out.iloc[0]["buyable"])
        panel = bool(can_buy("600000.SH", last[1], last[2], last[3], last[4],
                             last[5], amount=None, prev_close=10.0))
        assert snap == panel, f"{name}: 实盘判 {snap}，回测判 {panel} —— 两条路径口径不一致"

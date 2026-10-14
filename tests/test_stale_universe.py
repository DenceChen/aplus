"""横截面里的**退市股**必须被剔除，面板不得用预测日冒充它们的末根日期。

## 事故经过（2026-10-11）

样本外赛马跑出一批「惊人」的数字：某些候选的多空价差 **+9.84%，t = 7.75**
（日频 9.84% 相当于年化数百倍，且 t 值比任何真 alpha 都显著）。

顺着挖下去，发现某日跌篮子的 10 只成分里：

| 代码 | 末根 K 线 | 2026-06-04 有 bar 吗 |
|---|---|---|
| 301139.SZ | 2026-10-09 | 有 |
| 300799.SZ | **2024-07-26** | 无 |
| 002336.SZ | **2025-07-03** | 无 |
| 300742.SZ | **2024-07-19** | 无 |
| 000622.SZ | **2025-07-15** | 无 |
| 300297.SZ | **2023-07-28** | 无 |
| 002781.SZ | **2023-07-04** | 无 |
| 300089.SZ | **2023-07-12** | 无 |
| 300208.SZ | **2025-07-18** | 无 |

9 只是**早已退市**的股票。三个环节叠在一起造成了那个假数字：

1. `FactorPanel.cross_section` 把 `last_date` 写死成 **预测日本身**
   → `filter_universe` 的 `max_stale_days=30`（专门为退市股写的）
   在回测路径上**完全是空操作**
2. 退市股照常被选中，但它们没有次日 bar → `actual_pct` 全是 NaN
3. `groupby().mean()` 默认跳过 NaN → 「篮子平均收益」实际是
   **剩下那一只**股票的收益，再被日度汇总平均，噪声被放大成 +9.84%

## 为什么三条都要修

- 第 1 条不修：回测的股票池与实盘不一致，所有历史结论的分母都是错的。
- 第 2 条不修：静默 NaN 跳过，指标的分母随日子变化却不报告。
- 第 3 条不修：指标可以在只有 1 只成分的情况下报出一个漂亮的数字。

一个被自己文档里的过滤器保护不了的过滤器，等于没有过滤器。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from aplus.panel import FactorPanel, build_panel
from aplus.predict import filter_universe
from aplus.store import LocalStore

DATES = pd.bdate_range("2024-01-02", periods=80).strftime("%Y-%m-%d").tolist()


def _daily(code, n, start="2024-01-02", drift=0.0005):
    dates = pd.bdate_range(start, periods=n).strftime("%Y-%m-%d")
    closes = 10.0 * (1 + drift) ** np.arange(n)
    return pd.DataFrame({
        "date": dates, "code": code, "open": closes, "high": closes,
        "low": closes, "close": closes, "volume": [1e6] * n,
        "amount": [1e7] * n, "tradestatus": [1] * n, "isST": [0] * n,
        "turn": [1.0] * n, "pctChg": [0.0] * n,
    })


def _panel(daily_map, factors_map=None):
    return FactorPanel(daily_map, factors_map, DATES)


# ── 第 1 条：last_date 必须是真实末根日期 ──────────────────────────────────

def test_last_date_is_the_stock_real_last_bar_not_the_prediction_day():
    """退市股的 `last_date` 必须是它自己的最后交易日，不能是预测日。

    此前写死 `np.full(..., self.dates[i])`，`max_stale_days` 因此形同虚设。
    """
    # 退市股要有 >41 根 K 线，否则 min_history 会先把它滤掉，测不到本条要点
    dm = {
        "600001.SH": _daily("600001.SH", 80),                 # 活到窗口末尾
        "600002.SH": _daily("600002.SH", 50),                 # 第 50 天退市
    }
    panel = _panel(dm)
    cross = panel.cross_section(len(DATES) - 1)
    last = dict(zip(cross["code"], cross["last_date"]))
    assert last["600001.SH"] == DATES[-1]
    assert last["600002.SH"] == DATES[49], (
        f"退市股的 last_date 被伪造成 {last.get('600002.SH')}，"
        f"真实末根是 {DATES[49]}"
    )


def test_stale_stock_is_filtered_out_of_the_cross_section():
    """本测试的落点：max_stale_days 必须在面板路径上真的生效。"""
    dm = {
        "600001.SH": _daily("600001.SH", 80),
        "600002.SH": _daily("600002.SH", 50, drift=-0.002),
        "600003.SH": _daily("600003.SH", 80, drift=0.001),
    }
    panel = _panel(dm)
    kept = filter_universe(panel.cross_section(len(DATES) - 1))
    assert "600002.SH" not in set(kept["code"]), "退市股没被剔除——过滤器是空操作"
    assert {"600001.SH", "600003.SH"} <= set(kept["code"])


def test_stock_with_no_data_at_all_is_not_in_the_cross_section():
    """上市日在窗口之后 → 横截面里不得出现（idx 全为 −1）。"""
    panel = FactorPanel({"600001.SH": _daily("600001.SH", 80),
                         "600009.SH": _daily("600009.SH", 80, start="2030-01-01")},
                        None, DATES)
    cross = panel.cross_section(len(DATES) - 1)
    assert "600009.SH" not in set(cross["code"])


# ── 第 2、3 条：成分不足必须可测、且被统计丢弃 ─────────────────────────────

def test_basket_counts_are_reported():
    """`rolling_stats` 必须报出篮子的**有效成分数**。

    `.mean()` 默认跳过 NaN，调用方若看不到分母，就无法知道「篮子平均」
    可能只由一只股票构成。
    """
    from aplus.verify import rolling_stats

    rows = []
    for i, day in enumerate(["d1", "d2", "d3"]):
        for k in range(10):
            rows.append({"pred_date": day, "code": f"U{k}", "direction": "up",
                         "actual_pct": 0.01, "market_pct": 0.0, "cost": 0.0,
                         "result": "hit"})
            # 跌篮子每天只有 2 只有效收益（其余 NaN）
            rows.append({"pred_date": day, "code": f"D{k}", "direction": "down",
                         "actual_pct": -0.02 if k < 2 else np.nan,
                         "market_pct": 0.0, "cost": 0.0,
                         "result": "hit" if k < 2 else "no_data"})
    st = rolling_stats(pd.DataFrame(rows))
    assert st["up_basket_n"] == 30
    assert st["down_basket_n"] == 6, "跌篮子的有效成分数没报出来"


def test_incomplete_basket_days_are_dropped_from_tstat():
    """成分不足的日子不得进入 t 检验序列。"""
    from aplus.evolve_race import breadth_tstat, down_excess_tstat
    from aplus.verify import MIN_BASKET

    rows = []
    for i in range(40):
        day = f"d{i:03d}"
        for k in range(10):
            rows.append({"pred_date": day, "code": f"U{k}", "direction": "up",
                         "actual_pct": 0.01, "market_pct": 0.0, "cost": 0.0})
            # 只有最后 1 天成分不足（1 只有效），前 39 天完整
            n_valid = 1 if i == 39 else 10
            rows.append({"pred_date": day, "code": f"D{k}", "direction": "down",
                         "actual_pct": (-0.02 if k < n_valid else np.nan),
                         "market_pct": 0.0, "cost": 0.0})
    df = pd.DataFrame(rows)
    _, days = down_excess_tstat(df)
    assert days == 39, f"成分不足那天（共 {days} 天）没有被剔除（MIN_BASKET={MIN_BASKET}）"
    _, bdays = breadth_tstat(df)
    assert bdays == 39


def test_min_basket_is_not_so_high_it_empties_the_sample():
    """防呆：门槛太高会把正常日子的样本也丢掉。"""
    from aplus.verify import MIN_BASKET

    assert 1 <= MIN_BASKET <= 5, "top_n=10 的篮子，门槛应在 1~5 之间"

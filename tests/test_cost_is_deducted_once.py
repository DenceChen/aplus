"""★ 成本必须**只扣一次** —— 2026-10-15 实测的 P0。

## 现象

`data/reports/k_sweep_summary.json` 里，`毛超额 − 净` 与 `avg_cost_real` 的比值：

| K | `avg_cost_real` | 逐年隐含成本 | 比值 |
|---|---|---|---|
| 5 | 26.84% | 51.83% | **1.93×** |
| 10 | 13.42% | 26.59% | **1.98×** |
| 21 | 6.39% | 12.68% | **1.98×** |
| 42 | 3.20% | 6.26% | **1.96×** |
| 63 | 2.13% | 4.05% | **1.90×** |

**五档一致地约等于 2 倍**，且逐年都如此。这不是巧合，是恒等式在说话。

## 根因（已定位到行）

`scripts/backtest_walk_forward_weights.py`：

```python
daily = _daily(at_cost(res, c))          # ← 成本**已经**扣进 daily
out[tag]["annual_gross_pct"] = mean * DAYS_PER_YEAR   # ← 这个「gross」其实已含成本
out[tag]["annual_net_pct"] = out[tag]["annual_gross_pct"] - out[tag]["annual_cost_pct"]
#                                                                          ↑ 又扣了一次
```

`aplus/evolve_race.py:248-250` 写得清清楚楚：

```python
net = leg["actual_pct"]
if direction == "up" and "cost" in leg.columns:
    net = net - leg["cost"]
```

⇒ `_daily_series` 返回的就是**已扣成本**的序列。
`annual_gross_pct` 这个名字是**骗人的**（它不是 gross，是 net-of-cost 的超额），
而 `annual_net_pct` 又拿它减了一次名义成本。

**第二个缺陷叠在上面**：减掉的是 `rounds_per_year × c` 这个**名义公式**，
不是从 `cost` 列实测出来的实际成本。所以那 1.90–1.98 的偏差就是
「名义公式 ≠ 实际换手成本」的部分 —— 两处错叠在一起，看起来像一个小误差。

## 为什么它能一路绿灯

`hit_threshold` 是用同一个错的 `annual_net_pct` 算的，
判决与被判决的对象用的是同一个错误数，**自洽**。
与第 5 层同族：一致性在给绿。

## 外部锚

会计恒等式，与本仓实现无关：

    无成本毛超额 − 净超额 = **实际**年成本（一次，不是两次）

`tests/test_k_sweep_extract.py` 只把字段搬出来，**全仓没有任何一处检查这条**。
"""

from __future__ import annotations

import pytest


def test_net_is_gross_minus_realized_cost_exactly_once():
    """★ 运行层断言：构造一个已知答案的日序列，让实现算出**具体的数**。

    正确答案（DAYS_PER_YEAR = 244）：
        无成本毛超额日均 0.0015 → 年化 0.3660
        扣成本后日均   0.0010 → 年化 0.2440
        ⇒ 实际年成本 = 0.1220（**只扣一次**）
        ⇒ 净 = 0.3660 − 0.1220 = 0.2440

    错实现（再减一次）会给 0.1220 —— **正好是正确答案的一半**。
    """
    import pandas as pd

    from scripts.backtest_walk_forward_weights import realized_cost_and_net

    gross = pd.Series([0.001, 0.002])        # 日均 0.0015
    with_cost = pd.Series([0.0005, 0.0015])  # 日均 0.0010 ⇒ 日成本 0.0005

    realized, net, drift = realized_cost_and_net(gross, with_cost)

    assert abs(realized - 0.0005 * 244) < 1e-12, (
        f"实际年成本算成 {realized}，正确答案 {0.0005 * 244}"
    )
    assert abs(net - with_cost.mean() * 244) < 1e-12, (
        f"净超额算成 {net}，正确答案 {with_cost.mean() * 244}。"
        "若它等于正确答案的一半，说明成本被扣了**两次**。"
    )
    assert abs(drift) < 1e-12, (
        f"自查漂移 {drift} 非零 ⇒ 「含成本超额 + 实际成本 ≠ 无成本毛超额」"
    )


def test_the_extractor_does_not_reintroduce_a_second_deduction():
    """抽取器不许再减一次 —— 它是最后一道会读到这个数的地方。

    `k_sweep_extract.py` 把 `annual_net_pct` 直接搬进摘要表。
    若它再减一次 `avg_cost_real`，P0 就从生成器搬到了抽取器。
    """
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1]
           / "scripts" / "k_sweep_extract.py").read_text(encoding="utf-8")
    assert "annual_net_pct" not in src or "- annual_cost_pct" not in src, (
        "k_sweep_extract 又在做减法 —— 成本只应在生成器扣一次"
    )


def test_published_k_sweep_net_must_be_gross_minus_cost_once():
    """★ 产物级：已落库的行里，`毛超额 − 净` 必须**约等于**成本，不是两倍。

    这是 §「为什么它能一路绿灯」的直接反制：
    生成器与抽取器如果再错一次，这条会红。
    """
    import json
    from pathlib import Path

    p = (Path(__file__).resolve().parents[1] / "data" / "reports"
         / "k_sweep_summary.json")
    if not p.exists():
        pytest.skip("尚无 K 扫描产物")

    rows = json.loads(p.read_text(encoding="utf-8"))["rows"]
    checked = 0
    for r in rows:
        years = r["per_year_gross"]
        implied = [(years[y] - r["per_year_net_real"][y]) / r["avg_cost_real"]
                   for y in years if r["avg_cost_real"]]
        if not implied:
            continue
        checked += 1
        ratio = sum(implied) / len(implied)
        assert 0.85 < ratio < 1.15, (
            f"K={r['k']} 的隐含成本是名义成本的 {ratio:.2f} 倍。"
            "1.0 = 成本只扣一次；≈2.0 = **扣了两次**（2026-10-15 的 P0）。"
            "修生成器后需要重跑 K 扫描。"
        )
    assert checked, "没有任何一行可供检查 —— 产物形状变了，需要重新评估这条守卫"

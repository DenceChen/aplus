"""独立验证报告的三个口径 bug——**数字看着完全正常，但全部是错的**。

## 事故（2026-10-14，由「查反转族毛超额为负的根因」子代理查出，我复核后确认）

`scripts/_verify_reversal_freq.py` 产出的 `reversal_freq_2019_2023.json`
有 24 行，每行都有 `年成本` / `年超额` / `净` 三列，格式工整，
与同目录其他报告长得一模一样。**三列里有一列是对的、两列是错的。**

### Bug 1：年成本被放大约 10 倍

```python
cost = float(up["cost"].sum()) / n_years
```

`cost` 是**逐日**写在每一行 up 记录上的**篮子级** drag
（`backtest.py` 里 `res.loc[res["direction"] == "up", "cost"] = drag`，
一天一个值，被复制到当天约 10 行 up 记录上）。
`.sum()` 于是把同一个 drag 数了约 10 遍。
实测六行的放大比全部落在 **9.97–9.99**。

正确写法是 `.mean() * DAYS_PER_YEAR`（先日内取均值，再年化），
不是 `.sum() / n_years`。

### Bug 2：净 = 年超额 − 年成本，**重复扣了一次成本**

`rolling_stats` 里的 `up_excess_vs_market` 已经是**净值**口径——
`verify.py:238` 明写 `up_net = actual_pct - cost`，然后
`up_excess = avg_up - market`。所以 `annual_excess` **已经扣过成本**。

而脚本第 127 行的注释断言它是「**毛**超额（未扣成本）」——
**那条注释是错的**，而且它把一个真实的 bug 写成了「已核对的口径说明」。

这是本轮最值得记的一种错误形状：**注释不是描述代码，是替代码辩护**。
我在写它的时候确实以为核过 `rolling_stats`，实际没读到 `avg_up`
是怎么算出来的。实测影响：报告的 `净` 多扣了 11–35 个百分点。

### Bug 3：t 检验没按调仓周期分块

```python
t, _ = excess_tstat(pred)      # 没传 rebalance_every=k
```

K=63 的 1151 个日观测里只有约 **18 次独立决策**。
按 block=1 算等于把每次调仓的 63 天当成 63 个独立样本，
**自由度虚增 63 倍**，t 值被系统性压小。

`evolve_race` 的闸门链一直传的是 `excess_tstat(results, rebalance_every)`，
只有这个独立验证脚本漏了。实测 K=63 的超额 t 从 **−1.37 变成 −2.85**。

## 为什么这个 bug 特别难发现

三个 bug 的**符号方向一致**：都让成本显得更大、净收益显得更差。
所以整张表看起来都在说「反转策略完蛋了」——**结论方向与 bug 方向同向**，
人不会去质疑一个支持自己既有判断的数。

而实际结论**没有变**（24 行 `annual_excess` 全负，这列不受影响），
变的只是**量级**和 **`best` 是哪一行**：`best` 按 `net` 选，
而 `net` 含被污染的成本，于是报告推荐的「最优行」也是错的。

> **一个 bug 恰好强化了我的既有结论，比一个反对我的 bug 危险得多。**
"""
import json
from pathlib import Path

import pytest

import pandas as pd

REPORT = Path("data/reports/reversal_freq_2019_2023.json")


# ── Bug 1：cost 是篮子级逐日值，sum 会数重 ─────────────────────────

def test_a_daily_basket_cost_is_replicated_across_the_basket_rows():
    """这是 bug 的**前提**：同一天的 10 行 up 记录带着同一个 cost 值。
    若它们各不相同，`.sum()` 就没有放大问题——前提错了，整条推理就得重来。"""
    from aplus.verify import verify_predictions
    df = pd.DataFrame({
        "pred_date": ["2020-01-02"] * 10,
        "code": [f"{i:06d}" for i in range(10)],
        "direction": ["up"] * 10,
        "actual_pct": [0.01] * 10,
        "result": ["hit"] * 10,
        "market_pct": [0.005] * 10,
        "cost": [0.003] * 10,          # 一天一个值，复制到 10 行
    })
    assert df["cost"].nunique() == 1
    # sum 与 mean 差整十倍
    assert float(df["cost"].sum()) / float(df["cost"].mean()) == pytest.approx(10.0)


def test_annual_cost_uses_the_daily_mean_not_the_sum():
    """年成本 = 日均 drag × 244。写成 `.sum() / n_years` 就放大成 n_years 倍以上。

    这条钉住的是**算法形状**，不是某个数：数字会随窗口变，
    但「先日内取均值、再年化」这个次序不能变。
    """
    import inspect

    from scripts import _verify_reversal_freq as V

    src = inspect.getsource(V)
    assert 'up["cost"].mean()' in src, "年成本必须用日均 drag 再年化"
    assert 'up["cost"].sum()' not in src, \
        "sum() 会把同一个篮子级 drag 数约 10 遍——这正是 2026-10-14 的 bug"


# ── Bug 2：annual_excess 已经是净值口径 ────────────────────────────

def test_rolling_stats_already_deducts_cost_from_the_up_leg():
    """**这条是 bug 2 的根据**：`up_excess_vs_market` 不是毛超额。

    脚本原来的注释断言它是「毛」的。注释与代码冲突时，
    **以代码为准**，并且要去读产生它的那一行——
    当时我以为核过，实际没读到 `avg_up` 的构造。
    """
    from aplus.verify import rolling_stats

    df = pd.DataFrame({
        "pred_date": ["2020-01-02"] * 10,
        "code": [f"{i:06d}" for i in range(10)],
        "direction": ["up"] * 10,
        "actual_pct": [0.01] * 10,
        "result": ["hit"] * 10,
        "market_pct": [0.0] * 10,
        "cost": [0.003] * 10,
    })
    st = rolling_stats(df)
    # avg_up 必须是 0.01 − 0.003；若成本没扣，它会等于 0.01
    assert st["avg_up_return"] == pytest.approx(0.007)
    assert st["up_excess_vs_market"] == pytest.approx(0.007)


def test_the_report_does_not_subtract_cost_a_second_time():
    """`净 = 年超额 − 年成本` 只有在年超额是**毛**口径时才成立。
    它不是。所以修法是删掉这次减法，而不是换个系数。

    只扫**代码行**：事故说明里必须能原样写出 `ann_x - cost` 来解释
    「曾经错在哪」，否则修复记录本身就得跟着删。"""
    import inspect

    from scripts import _verify_reversal_freq as V

    code = "\n".join(ln for ln in inspect.getsource(V).splitlines()
                     if not ln.lstrip().startswith("#"))
    assert "ann_x - cost" not in code, \
        "annual_excess 已含成本，再减一次是重复扣——2026-10-14 的 bug"


# ── Bug 3：t 必须按调仓周期分块 ─────────────────────────────────────

def test_the_t_statistic_is_computed_with_the_rebalance_block():
    """K=63 的 1151 个日观测只有约 18 次独立决策。
    按 block=1 算等于把每次调仓的 63 天当成 63 个独立样本，
    自由度虚增 63 倍，t 被系统性压小（实测 −1.37 → −2.85）。"""
    import inspect

    from scripts import _verify_reversal_freq as V

    src = inspect.getsource(V)
    assert "excess_tstat(pred, k)" in src, "excess_tstat 必须传 rebalance_every"
    assert "breadth_tstat(pred, k)" in src, "breadth_tstat 必须传 rebalance_every"


def test_a_low_frequency_row_has_far_fewer_degrees_of_freedom():
    """**分块为什么重要，用数字说**：K=63 的独立观测数应是 K=1 的约 1/63。
    若传不传 block 返回同一个 n，说明分块根本没生效。"""
    from aplus.evolve_race import breadth_tstat, excess_tstat

    n_days, n_basket = 20, 10          # 每天 10 只，够 MIN_BASKET
    rng = [0.001, -0.002, 0.003, -0.001] * (n_days // 4)
    rows = []
    for i in range(n_days):
        for j in range(n_basket):
            rows.append({
                "pred_date": f"2020-{i // 20 + 1:02d}-{i % 20 + 1:02d}",
                "code": f"{i:03d}{j:03d}",
                "direction": "up" if j < n_basket // 2 else "down",
                "actual_pct": rng[i] + 0.0001 * j,
                "result": "hit",
                "market_pct": 0.0,
                "cost": 0.001,
            })
    df = pd.DataFrame(rows)

    _, n_block1 = excess_tstat(df)
    _, n_block2 = excess_tstat(df, 2)
    assert n_block1 == n_days
    assert n_block2 == pytest.approx(n_days / 2, abs=1)
    _, b1 = breadth_tstat(df)
    _, b2 = breadth_tstat(df, 2)
    assert (b1, b2) == (n_days, pytest.approx(n_days / 2, abs=1))


# ── 结论方向：三个 bug 都只污染量级，不改变 24 行全负 ───────────────

@pytest.mark.skipif(not REPORT.is_file(), reason="报告尚未生成")
def test_every_row_still_has_negative_annual_excess_after_the_fix():
    """**这条是本次修复的底线**：修 bug 不能改变结论方向。

    若哪天修完出现正超额，那不是「修对了」，而是引入了新错误——
    届时要查的是新错误，不是庆祝。
    """
    rows = json.loads(REPORT.read_text(encoding="utf-8"))["rows"]
    assert all(r["annual_excess"] < 0 for r in rows), \
        "24 行年超额全负是修 bug 前后都不变的结论；出现正值说明引入了新错误"
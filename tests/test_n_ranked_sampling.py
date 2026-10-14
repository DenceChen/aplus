"""`n_ranked` 不能只看窗口第一天——那是喂给安全闸的退化样本。

## 事故（2026-10-11 22:15）

`aplus/backtest.py: evaluate_weights` 结尾：

```python
result.attrs["n_ranked"] = scoreable_count(cross_sections[use_dates[0]], weights)
```

**只看第一天。** 而 `n_ranked` 是第六道闸（股票池可比）的输入。

跑 7.8 年全历史复合赛马时，窗口从 `2019-01-02` 开始——**那是数据集的第一天**。
那一天没有任何股票有可计算的 `mom20`（20 日窗口需要 20 个交易日的历史），
于是 `scoreable_count` 返回 **0**，现任权重训练段的 `n_ranked = 0`。

这次没造成误判（闸门读的是**测试段**的 `n_ranked` = 4805，正常），
但它说明：**闸门的输入可以是一个退化样本，而闸门不会知道。**

这与第 18 号缺陷同一族——独立验证表里 `n_ranked=0` 的 8 行
建立在空股票池上却排在最后、没人先看。

## 修法

跨整个窗口**取若干等距日期的中位数**，而不是单点。
中位数对「少数日子不可算」稳健，而单点不是。

- 中位数而不是平均：空股票池的日子会把平均拉低，
  而中位数只会让那一天的权重变成 0 个
- 取样点数固定（小），避免给 1881 天的窗口做无谓的计算
- `use_dates` 为空时仍返回 0（那确实是没有可测量的东西）
"""

import pandas as pd

from aplus.backtest import sampled_ranked_count


def _panel(days, rows_per_day):
    """days -> 行数列表（0 表示那天因子全 NaN，一只都打不了分）。"""
    xs = {}
    for d, n in zip(days, rows_per_day):
        xs[d] = pd.DataFrame({
            "code": [f"{j:06d}.SZ" for j in range(n)],
            "mom20": [0.1 * j for j in range(n)],
            "adj_next": [10.0 + j for j in range(n)],
            "buyable": [True] * n,
            "close": [10.0 + j for j in range(n)],
        })
    xs["_dates"] = list(days)
    return xs


def test_first_day_degenerate_does_not_report_zero_ranked():
    """第一天一只都打不了分（数据起点没有 20 日历史），
    后面 9 天都有 300 只——报 0 就是把闸门喂了一个退化样本。"""
    days = [f"2024-01-{i:02d}" for i in range(1, 11)]
    xs = _panel(days, [0] + [300] * 9)
    n = sampled_ranked_count(xs, days, {"mom20": 1.0})
    assert n == 300, f"n_ranked={n}，可打分的 9 天各有 300 只"


def test_median_not_mean_is_used():
    """**中位数**而不是平均：前 10 天空、后 10 天各 200 只，
    正确答案应是 200（一半日子不可算，平均会掉到 100）。"""
    days = [f"2024-01-{i:02d}" for i in range(1, 21)]
    xs = _panel(days, [0] * 10 + [200] * 10)
    n = sampled_ranked_count(xs, days, {"mom20": 1.0})
    assert n == 200, f"n_ranked={n}：可打分的那 10 天各有 200 只，取平均会掉到 100"


def test_all_days_degenerate_reports_zero():
    """全都没有可算的 → 0。这是真的「没有可测量的东西」，要如实报。"""
    days = [f"2024-01-{i:02d}" for i in range(1, 11)]
    xs = _panel(days, [0] * 10)
    assert sampled_ranked_count(xs, days, {"mom20": 1.0}) == 0


def test_empty_window_reports_zero():
    xs = {"_dates": [], "_daily_map": {}}
    assert sampled_ranked_count(xs, [], {"mom20": 1.0}) == 0


def test_missing_day_is_skipped_not_counted_as_zero():
    """窗口里有个缺失的日子不能被当成「0 只可打分」拉低中位数。"""
    days = [f"2024-01-{i:02d}" for i in range(1, 11)]
    xs = _panel(days, [300] * 10)
    del xs[days[0]]
    n = sampled_ranked_count(xs, days, {"mom20": 1.0})
    assert n == 300, f"n_ranked={n}：缺失那天应跳过，不是算成 0"

"""调仓频率与缓冲区：把「每天重新选一遍」和「真的换手」拆开。

## 为什么需要这个模块

实测（2026-10-11，5 万元本金 / 10 只持仓 / 单只 5000 元）：

| 调仓频率 | 年成本（占本金） | 要净赚 20% 需要的毛收益 |
|---|---|---|
| 每日（实测换手 24.8%） | 15.3% ~ 18.2% | 35% ~ 38% |
| 每周 | 1.3% ~ 1.5% | 21.3% ~ 21.5% |
| 每月（书 3.1.4 的口径） | 0.30% ~ 0.35% | 20.3% ~ 20.4% |

**日频与月频之间差 5.8 个百分点/年，而平台至今测到的所有因子效应都在 ±1% 以内。**
换句话说：在日频口径下，一个因子哪怕方向完全正确、效应量翻倍，
也填不平成本挖的坑。

这与书并不矛盾。《因子投资：方法与实践》3.1.4 的口径是**每月末调仓**、
**10 档分组**（`docs/factor_spec_cn.md:236` 已标记「⚠️ 平台是日频预测，非月末调仓」）。
书里那些 t=5.94 / 6.67 / 8.64 全部是月度调仓组合的统计量。
平台拿日频 top-10 去检验它们，验的不是同一件事。

## 两个正交的设计轴

1. **`rebalance_every`**：每隔 N 个交易日重新选一次篮子，其余日子**原样持有**。
2. **`buffer_multiple`**：已经持有的票，只要还在 `top_n × buffer_multiple` 之内就
   继续持有，不必挤回前 `top_n` 才留。这是降低换手的标准做法，
   与调仓日历正交——它削减的是「换手幅度」，不是「换手频率」。

## 硬约束

- **绝不引入未来信息**。缓冲区的判据只能用**当日**排名，
  持有决策只用「今天它在不在宽带内」。
- **不可买入的票不能因为在缓冲区里就一直留着**：涨停/停牌由 `aplus.panel`
  的可交易性判定负责剔除，本模块只负责频率与缓冲区，不重复实现。
"""
from __future__ import annotations

import pandas as pd

# 缓冲区倍数。取 3 的依据：A 股日频排名从第 10 名掉到第 30 名通常要数日，
# 3 倍带宽能吸收噪声又不至于把篮子摊薄到失去集中度。
# 该取值必须在看结果之前固定——看结果再调宽度就是过拟合。
BUFFER_MULTIPLE = 3.0


def rebalance_dates(dates: list, every: int) -> list:
    """从有序交易日里挑出「真正调仓」的日子。

    `every=1` 时返回全部日期（等价于当前的日频行为），
    这样默认路径与修复前完全一致，不给历史结论引入任何变化。
    """
    dates = list(dates)
    if every <= 1:
        return dates
    return dates[::every]


def is_rebalance_day(dates: list, day: str, every: int) -> bool:
    """某个具体交易日是否调仓。按位置判定，保证与 `rebalance_dates` 完全一致。

    **未知日期一律返回 False**，哪怕 `every <= 1`。
    短路必须放在成员检查之后：否则一个不在窗口里的日期会被当成调仓日，
    而调用方往往拿它去索引篮子，静默多算一次换手。
    """
    dates = list(dates)
    try:
        i = dates.index(day)
    except ValueError:
        return False
    return True if every <= 1 else i % every == 0


def buffer_hold(current: list, ranked_codes: list, top_n: int,
                buffer_multiple: float = BUFFER_MULTIPLE) -> list:
    """缓冲区持仓：已持有的票只要还在宽带内就留下，再补足新票。

    判据只用当日 `ranked_codes`（已按分数降序排好的代码列表）：
    排名索引 < top_n × buffer_multiple 即视为「仍在带内」。

    绝不能反过来写成「先取宽带前 K 只再截断 top_n」——那等价于没有缓冲区，
    每期还是 top_n，缓冲区会变成一个看起来有用、实则恒等的参数。
    """
    current = list(current or [])
    if not current:
        return []
    band = max(int(round(top_n * float(buffer_multiple))), 1)
    in_band = set(ranked_codes[:band])
    kept = [c for c in current if c in in_band]
    return kept


def fill_to_top_n(kept: list, ranked_codes: list, top_n: int) -> list:
    """把缓冲区保留下来的持仓补足到 `top_n` 只。

    补充顺序 = 当日排名从前往后，跳过已持有的。
    返回顺序保持「已持有的在前、新补的在后」，
    这样 `cost_drag` 的「新进只数」计算才与真实换手一致。
    """
    kept = list(kept)
    if len(kept) >= top_n:
        return kept[:top_n]
    have = set(kept)
    for c in ranked_codes:
        if len(kept) >= top_n:
            break
        if c in have:
            continue
        kept.append(c)
    return kept


def effective_rebalance_every(dates: list, requested: int, train_ratio: float) -> int:
    """训练/测试切分后，每个样本段至少要留得下几次调仓。

    若测试段只剩 1~2 个调仓周期，任何频率对比都没有统计意义——
    2 个观测点无法区分「月度有效」与「月度无效」。
    这种情况下必须显式返回 0 让调用方拒绝比较，而不是给出一个必然无意义的数。
    """
    if requested <= 1:
        return 1
    n_test = int(len(dates) * (1.0 - float(train_ratio)))
    if n_test < 1:
        return 0
    periods = n_test // requested
    return requested if periods >= 3 else 0


def summarize_turnover(baskets: dict, top_n: int) -> dict:
    """按日统计实际换手率——成本估算与频率对比都要用它。

    `baskets`: date -> 该日的涨篮子代码列表（降序即排名）。
    返回 `mean` / `median` / `n_days` / `hold_overlap`（相邻两期持仓重合度）。
    """
    dates = sorted(baskets)
    if len(dates) < 2:
        return {"mean": 0.0, "median": 0.0, "n_days": len(dates), "hold_overlap": 1.0}
    turns, overlap_num, overlap_den = [], 0, 0
    prev = list(baskets[dates[0]])
    for d in dates[1:]:
        cur = list(baskets[d])
        denom = 2.0 * max(top_n, len(cur), len(prev))
        if denom > 0:
            n_in = len(set(cur) - set(prev))
            n_out = len(set(prev) - set(cur))
            turns.append((n_in + n_out) / denom)
        overlap_num += len(set(cur) & set(prev))
        overlap_den += max(len(cur), len(prev))
        prev = cur
    s = pd.Series(turns, dtype="float64")
    return {
        "mean": float(s.mean()) if len(s) else 0.0,
        "median": float(s.median()) if len(s) else 0.0,
        "n_days": len(dates),
        "hold_overlap": float(overlap_num / overlap_den) if overlap_den else 1.0,
    }

"""股票侧交易成本（TDD，2026-10-11）。

## 这是读书带回来的最贵一条

Ernest Chan《Quantitative Trading》第 5 章（p96–100，Example 3.7）证明：一个真实
存在的公开策略，**加上 5bp 成本后从夏普 +0.96 变成 −2.16**。他强调成本必须是
回测的**一等公民**，且只在你实际改变持仓时才收。

核对代码后确认：`aplus/backtest.py`、`aplus/verify.py`、`aplus/portfolio.py`
三个文件里 `fee|cost|commission` **零命中**——股票侧每一笔收益都是零成本算出来的。
而转债侧有完整的 `COST_WAN15 = 0.003` 模型。

我们的股票策略是**每日 Top10 轮动**（接近满换手），日均超额测得 +0.034%。
在零成本口径下这已经与 0 不可区分（t=0.60）。加上成本只会更差。

**所以此前「股票侧有没有 alpha」的讨论，方向可能就是错的——不是「edge 被前视掩盖」，
而是「edge 从来没扣过成本」。**

## 口径（与转债侧对齐）

- `cost` = 一次**完整换手**的双边费率：万15/边 → **0.003**（平台保守档）
- `turnover_ratio = (新进只数 + 退出只数) / (2 × max(top_n, 本期只数, 上期只数))`
- `cost_drag = turnover_ratio × cost`
- **首期建仓只付买侧** → `cost/2`
- 成本只按**涨腿**（我们实际建仓的那一侧）计；跌腿是回避清单，不产生交易

## 红线

成本不得默认为 0。**默认值必须是最保守的那一档**——任何漏传 cost 的调用方
都会静默拿到「策略有 alpha」的乐观结论。
"""
import pandas as pd
import pytest

from aplus.backtest import STOCK_COST_DEFAULT, cost_drag


def _basket(day, codes):
    return pd.DataFrame({"pred_date": [day] * len(codes),
                         "code": codes,
                         "direction": ["up"] * len(codes)})


# ── 基本口径 ──

def test_first_period_pays_buy_side_only():
    """首期建仓没有卖出，只付 cost/2。"""
    d = cost_drag(None, ["A", "B", "C", "D", "E"], top_n=5, cost=0.003)
    assert d == pytest.approx(0.003 / 2)


def test_full_turnover_pays_full_cost():
    """全换手（无一只重叠）→ 付满 cost。"""
    d = cost_drag(["A", "B", "C", "D", "E"], ["F", "G", "H", "I", "J"],
                  top_n=5, cost=0.003)
    assert d == pytest.approx(0.003)


def test_no_turnover_pays_nothing():
    """持仓完全不变 → 零成本。低价池稳定时这正是收益来源。"""
    d = cost_drag(["A", "B", "C"], ["A", "B", "C"], top_n=3, cost=0.003)
    assert d == 0.0


def test_half_turnover_pays_half_cost():
    prev = ["A", "B", "C", "D"]
    cur = ["A", "B", "E", "F"]
    # 2 进 2 出，分母 2×4=8 → 4/8 × cost
    assert cost_drag(prev, cur, top_n=4, cost=0.003) == pytest.approx(0.003 * 4 / 8)


def test_empty_prev_counts_as_initial_position():
    prev = []
    cur = ["A", "B", "C"]
    # 分母取 max(只数, top_n)；首期只买不卖
    assert cost_drag(prev, cur, top_n=3, cost=0.003) == pytest.approx(0.003 / 2)


def test_drag_never_exceeds_full_cost():
    """换手比例不会超过 100%（不可能卖超过持有量）。"""
    prev = ["A", "B", "C", "D", "E"]
    cur = ["X", "Y", "Z", "W", "V"]
    assert cost_drag(prev, cur, top_n=5, cost=0.003) <= 0.003 + 1e-12


def test_top_n_larger_than_basket_uses_top_n_as_denominator():
    """池子只有 2 只但 top_n=10 时，分母应取 10，否则每期成本被低估。"""
    d = cost_drag(["A", "B"], ["C", "D"], top_n=10, cost=0.003)
    assert d == pytest.approx(0.003 * 4 / 20)


# ── 红线：默认值不能是 0 ──

def test_default_cost_is_conservative_not_zero():
    """默认必须是保守档。默认为 0 会让任何漏传 cost 的调用方静默拿到乐观结论——
    这正是 2026-10-10 转债侧「默认 0.0003 而判定用 0.003」那类事故的同构版本。"""
    assert STOCK_COST_DEFAULT > 0
    assert STOCK_COST_DEFAULT == pytest.approx(0.003)


def test_cost_drag_defaults_to_conservative():
    d = cost_drag(["A", "B", "C", "D"], ["W", "X", "Y", "Z"], top_n=4)
    assert d == pytest.approx(STOCK_COST_DEFAULT)
    assert d > 0
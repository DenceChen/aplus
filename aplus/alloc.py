"""资产配置：规则型仓位方案 + 调仓差异 + 风控注记。

规则（PLATFORM.md §4，保守档起步）：
- 只配「预测涨」名单（可按分数阈值过滤弱信号）；最多 max_positions 只
- 单票 per_stock_cap（默认 10%），权益总仓 = 持仓数 × 单票上限（5×10% = 50% 封顶）
- 现金 = 1 - 权益仓（无候选时 100% 现金——不预测不满仓）
- 调仓差异：新名单与现持仓的交集保留、新进加仓、掉出清仓

## 为什么是 50%，以及**不是**为了什么

**50% 不是成本优化。**「少投点省手续费」这个直觉是错的：
¥5 最低佣金是**每笔固定额**，单笔越小它占比例越高。
实测（`aplus/costs.py` 的 `FeeSchedule`，半仓 = 5 只 × ¥5,000）：

    半仓   每轮 ¥138.00   摩擦/已投权益 **6.62%**
    全仓   每轮 ¥226.00   摩擦/已投权益 **5.42%**

绝对额确实省了（¥88/轮），但**分母同时缩了一半**，摩擦率反而高 22%。
省下的每一分钱都对应着同样金额的收益也没赚到——
这不是省钱，是放弃收益，两件事在净值上是同一个减法。
（常驻测试：`tests/test_costs.py::test_deploying_less_capital_does_not_make_the_deployed_part_cheaper`）

**50% 的真实理由是纸面验证期的风险敞口封顶**：信号尚未被证明有正超额
（可信口径 `excess_breadth` 污染前后同为 −4.65，仍为负），
在这种证据状态下把 100% 资本押上去，风险收益比不划算。
**它与成本模型完全正交**，所以调成本模型不会改变这个上限，
反过来也不该用成本理由来解释它。

想降成本只有一个正确方向：**降调仓频率**（月→季省 67%），
降敞口只会抬高摩擦率。
"""
from dataclasses import dataclass, field

import pandas as pd


@dataclass
class AllocationPlan:
    capital: float
    positions: list = field(default_factory=list)    # [{code, name, weight, amount, score}]
    cash_weight: float = 1.0
    rebalance: list = field(default_factory=list)    # [{code, action: keep/add/remove}]
    risk_notes: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "capital": self.capital,
            "positions": self.positions,
            "cash_weight": self.cash_weight,
            "rebalance": self.rebalance,
            "risk_notes": self.risk_notes,
        }


def build_allocation(
    up_predictions: pd.DataFrame,
    capital: float = 50_000.0,
    max_positions: int = 5,
    per_stock_cap: float = 0.10,
    score_threshold: float | None = None,
    current_positions: list | None = None,
) -> AllocationPlan:
    current_positions = current_positions or []
    candidates = up_predictions
    if score_threshold is not None and "score" in candidates.columns:
        candidates = candidates[candidates["score"] >= score_threshold]
    picked = candidates.sort_values("score", ascending=False).head(max_positions)

    positions = [
        {
            "code": row["code"],
            "name": row.get("name", ""),
            "weight": per_stock_cap,
            "amount": round(capital * per_stock_cap, 2),
            "score": float(row["score"]),
        }
        for _, row in picked.iterrows()
    ]
    equity_weight = len(positions) * per_stock_cap
    cash_weight = round(1.0 - equity_weight, 10)

    target_codes = {p["code"] for p in positions}
    current_codes = {p["code"] for p in current_positions}
    rebalance = (
        [{"code": c, "action": "keep"} for c in sorted(target_codes & current_codes)]
        + [{"code": c, "action": "add"} for c in sorted(target_codes - current_codes)]
        + [{"code": c, "action": "remove"} for c in sorted(current_codes - target_codes)]
    )

    risk_notes = [
        "单票硬止损 -8%：收盘价触及即次日卖出，不犹豫、不摊平",
        "组合回撤 -10% 强制权益减半，净值修复前禁止加仓",
        "年化目标 20% 属进取型（沪深300 长期约 5-8%）：先以纸面验证命中率与超额收益，达标前敞口保持最保守档",
    ]
    return AllocationPlan(capital, positions, cash_weight, rebalance, risk_notes)

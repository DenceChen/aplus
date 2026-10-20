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
    rebalance: list = field(default_factory=list)    # [{code, name, action, weight, amount}]
    # ★ 调仓差异**是怎么来的**：`current_positions` = 真·当前持仓；
    #   `none` = **没传当前持仓** ⇒ keep/remove 结构上不可能出现，
    #   差异不可信（历史上就是这个洞：调用处没传，产物里全是 add，
    #   而产物与界面都看不出「没算」与「全买」的区别）。
    rebalance_source: str = "none"
    risk_notes: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "capital": self.capital,
            "positions": self.positions,
            "cash_weight": self.cash_weight,
            "rebalance": self.rebalance,
            "rebalance_source": self.rebalance_source,
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
    """排一份配置方案，**外加相对当前持仓的调仓差异**。

    ## `current_positions` 是什么、为什么必须传

    它是**当前手上持有的那一份**（代码 + 名字），**不是**今天的推荐名单。
    传了它，`rebalance` 才有 keep / add / remove 三种动作：
    交集=留（keep）、目标有而手上没有=买（add）、手上有而目标没有=卖（remove）。

    不传它 ⇒ `current_codes` 为空 ⇒ keep/remove **结构上不可能出现**，
    产物里只剩一串 add —— 而产物与界面都看不出「差异没算」和「差异就是全买」
    的区别。⇒ 这种情况下 `rebalance_source = "none"`，**差异不可信**。

    ⚠️ 传 `[]`（明确说「手上空仓」）与**不传**是两件事：
    前者 `rebalance_source = "current_positions"` 且全是 add（正确：空仓就全买），
    后者是 `none`。⇒ 这里用 `is not None` 判「有没有传」，不靠真假值。
    """
    has_current = current_positions is not None
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

    target_by_code = {p["code"]: p for p in positions}
    # 坏元素不许把整个出单流程带崩，但也不许被当成「持仓」——
    # 缺 code 的条目直接丢掉（它比不出 keep/add/remove）。
    current_by_code = {
        p["code"]: p for p in current_positions
        if isinstance(p, dict) and p.get("code")
    }
    target_codes = set(target_by_code)
    current_codes = set(current_by_code)

    # 每条差异都带齐五字段（code/name/action/weight/amount）：
    # 前端要拿它渲染「留 / 买 / 卖」三栏，缺字段就得在页面上编。
    # ⚠️ `weight` 是**比例**、`amount` 是**金额**（= 本金 × 比例），
    #    两者不能互换 —— 现有表里每只 5000 元 = 50000 × 0.10。
    rebalance = []
    for c in sorted(target_codes & current_codes):
        p = target_by_code[c]
        rebalance.append({"code": c, "name": p.get("name", ""), "action": "keep",
                          "weight": p["weight"], "amount": p["amount"]})
    for c in sorted(target_codes - current_codes):
        p = target_by_code[c]
        rebalance.append({"code": c, "name": p.get("name", ""), "action": "add",
                          "weight": p["weight"], "amount": p["amount"]})
    for c in sorted(current_codes - target_codes):
        cur = current_by_code[c]
        # 被清掉的票：**目标权重是 0**。
        # ⚠️ `amount` 写 0 不是「不用卖」—— 持仓的**市值/成本不在状态文件里**
        #    （`holdings.json` 只存代码与名字）⇒ 编一个数出来就是凭空多算一笔账。
        rebalance.append({"code": c, "name": cur.get("name", ""), "action": "remove",
                          "weight": 0.0, "amount": 0.0})

    risk_notes = [
        # 🚨 2026-10-20：这两条**曾经**在这里承诺「单票硬止损 -8%」与
        # 「组合回撤 -10% 强制权益减仓」——而**全仓 grep 只在这两行字符串命中**
        # ⇒ 平台每天把一条它根本不会执行的风控承诺交给用户。
        # ⇒ 一条假话比没有这句话危险，因为它免掉了追问。
        # ⇒ 处置不是「补实现」：按 Chan《Quantitative Trading》
        # （data/books/chan_lines.txt:4799-4813）止损要有效的前提是
        # 「我们相信处在**动量或趋势**状态」；而本平台是**短持有篮子**
        # （名单每天出、K=21 换仓）⇒ **该前提不成立** ⇒ 止损结构上不适用，
        # 实现它只会把一个不成立的对冲写进生产。
        # ⇒ 所以这里**不再承诺**，改成说清真实约束。
        "⚠️ **本方案不含止损与回撤减仓**（旧版本曾承诺「单票止损 -8%」"
        "「回撤 -10% 强制减半」，但代码里从未实现 ⇒ 那是句假话，已撤）。"
        "按 Chan（chan_lines.txt:4799-4813）止损要有效的前提是「处在动量/趋势状态」，"
        "而本平台是短持有篮子 ⇒ 该前提不成立 ⇒ 止损结构上不适用。",
        f"**整手约束**：每份 ¥{capital * per_stock_cap:,.0f} 按**整手**取整"
        "（科创板 688/689 起买 **200 股**，其余 100 股）⇒ "
        "**买不起 1 手的不下单**，该份资金留在现金里（按 0 计息）。",
        "年化目标 20% 属进取型（沪深300 长期约 5-8%）：先以纸面验证命中率与超额收益，达标前敞口保持最保守档",
    ]
    return AllocationPlan(
        capital=capital,
        positions=positions,
        cash_weight=cash_weight,
        rebalance=rebalance,
        rebalance_source="current_positions" if has_current else "none",
        risk_notes=risk_notes,
    )

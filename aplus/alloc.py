"""资产配置：规则型仓位方案 + 调仓差异 + 风控注记。

规则（PLATFORM.md §4，**满仓档**）：
- 只配「预测涨」名单（可按分数阈值过滤弱信号）；最多 max_positions 只
- 单票 per_stock_cap（默认 10%），权益总仓 = 持仓数 × 单票上限（**10×10% = 100%**）
- 现金 = 1 - 权益仓（无候选时 100% 现金——不预测不满仓）
- 调仓差异：新名单与现持仓的交集保留、新进加仓、掉出清仓

## 为什么是 100%（满仓），以及**不是**为了什么

**满仓不是成本优化。**「多投点省手续费」这个直觉恰好是反的：
¥5 最低佣金是**每笔固定额**，单笔越小它占比例越高。
实测（`aplus/costs.py` 的 `FeeSchedule`）：

    半仓（5 只 × ¥5,000）   每轮 ¥138.00   摩擦/已投权益 **6.62%**
    全仓（10 只 × ¥5,000）  每轮 ¥226.00   摩擦/已投权益 **5.42%**

绝对额确实多花了（¥88/轮），但**分母同时翻了一倍**，摩擦率反而**低** 18%。
（常驻测试：`tests/test_costs.py::test_deploying_less_capital_does_not_make_the_deployed_part_cheaper`）

⇒ 换敞口既不是省钱也不是费钱，**它改的是账户能拿到多少市场腿**。

### 满仓的**真实理由是目标相容性**（2026-10-21 改）

等权市场腿自身的几何年化 **9.9471%**
（`data/reports/market_nav_caliber.json::equal_weight.geometric_annual_pct`）。

    账户年化 ≈ 敞口 × (市场腿 + 选股腿净值)

| 敞口 | 市场腿贡献 | 剩下要由选股腿补 |
|---|---|---|
| 0.5 | 0.5 × 9.9471% = **4.97%** | 20% − 4.97% = **15.03pp** ⇒ 半仓那 2.5 万要赚 **30.1%/年** |
| 1.0 | **9.9471%** | 20% − 9.9471% = **10.05pp** |

而多头毛超额上限实测约 **11%/年**（`data/reports/width_capital_feasibility.json` 一族；
CLAUDE.md「多头 − 市场」四档 10.86 / 11.14 / 10.26 / 11.10%）。

⇒ 🚨 **在敞口 0.5 下，20% 与选股能力无关地结构上不可达**（要 30.1%/年，上限 11%）。
⇒ 任何「先验证信号再满仓」的顺序，在这个目标下**数学上封死了目标本身**。

⇒ 决策登记：`docs/DELIVERY_DECISIONS_2026-10-20.md` §1、
本轮预注册 `docs/PREREG_PRODUCTION_CONFIG_2026-10-21.md` §1。

### 🚨 风险必须写清（**这不是「免费的收益**）

- 账户 beta 由 **0.5 翻到 1.0** ⇒ **市场跌 30% 的年份，账户跌约 30%** 而不是 15%。
- 这是**用户承担的市场风险**，换来的是**目标可达性**。用户给的目标是 20%，按目标选。
- ⇒ 🚨 **不许把敞口变化说成「策略变好了」**：它是 **beta 档位**，
  选股权（超额腿）在两档里**完全相同**。

### 仓数为什么是 10

- D1 的承诺是「每天 10 涨 10 跌」（`CLAUDE.md` 交付物闸）⇒ 持仓数与名单宽度一致。
- 实测过的档宽里 **10 是唯一被测过**的（`factor_width_mde.json::widths["10"]`），
  **N=5 从未测过** ⇒ 取实测过的宽度。
- ⚠️ **10 只 × 10% = 100% 满仓**；「10 只 × 50%」是**另一个东西**（每份 ¥2,500），
  本文件**不采用**。

### 敞口的**唯一真相源**

权益 = **仓数 × 单票上限**，由 `build_allocation` 的两个参数派生。
🚨 **不许新增 `equity_weight` 参数** —— 那会让权益占比有**两个真相源**
（第 97 层：两份副本之间没有守卫）⇒ 改仓数即可。
既有消费方（`aplus/webapp.py::deployed_defaults`、
`scripts/deployed_account_nav.py::deployed_exposure`）都从
**签名默认值**读敞口 ⇒ 改默认值即自动传播。

想降成本只有一个正确方向：**降调仓频率**（月→季省 67%），
降敞口只会抬高摩擦率**且**把目标封死。
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
    # ★ 今天**是不是调仓日**（2026-10-21）。
    #   `False` ⇒ 产物描述的是「**我持有什么**」（沿用上期名单），
    #   且 `rebalance` **为空**（没有订单）。
    #   🚨 非调仓日**不许**出现 add/remove —— 那会让用户以为要交易，
    #   而 21 天里有 20 天根本不该交易。
    is_rebalance_day: bool = True
    # 距上次换仓的进度（`days_since_rebalance` / `rebalance_every`）。
    days_since_rebalance: int | None = None
    rebalance_every: int | None = None
    # ★ **建仓日**。🚨 **可能是 `None`** ⇒ 那是「建仓日未知」
    #   （**不许拿 `as_of` 顶替** —— `as_of` 是**最后出单日**，
    #   非调仓日两者必然不同）。
    held_since: str | None = None
    # 持仓沿用**哪份名单**（codes + names）。
    # ⚠️ **2026-10-21 更正**：这里原来写「取自 `state["up"]`」——**那是候选池**
    # （那份 10 只涨腿名单），**不是钱包**。非调仓日的 `positions` 取自
    # **`current_positions` 入参** ⇒ 调用方（`scripts/run_daily.py`）现在传的是
    # `state["held_codes"]`（**钱包**，缺它 ⇒ 传 `None` ⇒ 差异不可信）。
    # ⇒ 留 `state["up"]` 当候选池是**故意的**（`holdings_entry.py` 靠它匹配建仓日）。
    held: list = field(default_factory=list)
    # 可买性过滤的**报告**（计数 + 名单）。`None` ⇒ 没做过滤。
    affordability: dict | None = None
    # ⚠️🚨 **「手上那批」读不读得出来**（2026-10-21 晚）。
    # `False` = **读不出来**（状态里没有「哪几只真的拿到了钱」这一列）
    # ⇒ **不是「空仓」** ⇒ 产物**不许**把它渲染成「0 只 · 100% 现金」
    #    （那是**让人卖光**的方向 —— 第 112 层的通则：错的方向比错的值更贵）。
    # ⇒ 只在**非调仓日**可能为 `False`（调仓日那批是当天算出来的，必有）。
    holdings_known: bool = True

    def to_dict(self) -> dict:
        return {
            "capital": self.capital,
            "positions": self.positions,
            "cash_weight": self.cash_weight,
            "rebalance": self.rebalance,
            "rebalance_source": self.rebalance_source,
            "risk_notes": self.risk_notes,
            "is_rebalance_day": self.is_rebalance_day,
            "days_since_rebalance": self.days_since_rebalance,
            "rebalance_every": self.rebalance_every,
            "held_since": self.held_since,
            "held": self.held,
            "affordability": self.affordability,
            "holdings_known": self.holdings_known,
        }


def build_allocation(
    up_predictions: pd.DataFrame,
    capital: float = 50_000.0,
    max_positions: int = 10,
    per_stock_cap: float = 0.10,
    score_threshold: float | None = None,
    current_positions: list | None = None,
    *,
    is_rebalance_day: bool,
    closes: dict | None = None,
    held_since: str | None = None,
    days_since_rebalance: int | None = None,
    rebalance_every: int | None = None,
) -> AllocationPlan:
    """排一份配置方案，**外加相对当前持仓的调仓差异**。

    ## 敞口是**派生**的，不是参数

    权益 = `持仓数 × 单票上限`（默认 **10 × 10% = 100%**）。
    🚨 **没有 `equity_weight` 参数** —— 那会让权益占比有**两个真相源**，
    两份副本之间没有守卫（第 97 层）。
    改敞口 ⇒ **改 `max_positions`**。

    ⚠️ `max_positions` 的默认值**就是**全仓的仓数真相源：
    `aplus/webapp.py::deployed_defaults` 与
    `scripts/deployed_account_nav.py::deployed_exposure` 都用
    `inspect.signature(build_allocation).parameters[...].default` 读它
    ⇒ 改这里即全仓传播，**不要**在别处再写一个仓数常量。

    ## `is_rebalance_day`：**无默认值**（少传必须 `TypeError`）

    🚨 **为什么它不许有默认值**：非调仓日**不该出任何订单**，
    而「默认值」= 「调用方忘了说」⇒ 那次调用就会**静悄悄地又生成一份假订单**
    （实测：21 天里有 20 天，用户看到一张根本不会执行的卖买清单）。

    | `is_rebalance_day` | 产物描述的是 | `rebalance` |
    |---|---|---|
    | `True` | 「我要换成什么」 | keep / add / remove 齐全 |
    | `False` | 「**我持有什么**」（沿用上期名单） | **空**（没有订单） |

    ⚠️ `False` 时 `positions` 是**沿用的持仓**，**不是**今天的推荐名单
    ⇒ 不许用今天的名单冒充持仓（那正是原 bug）。

    ## `closes`：code → 不复权 close（信号日当天或之前最后一根 bar）

    🚨 **可买性过滤在**配置层**（这里），不在名单层**
    ⇒ 「推荐一只买不进的股票」是**交易**层的问题，不是**排名**层的问题
    ⇒ 发布的 10 涨 10 跌**仍然是全市场真正的前 10 / 后 10**
    ⇒ 前瞻底稿的 6 个窗口**不被作废**。

    `None`（不传）⇒ **不做可买性过滤**（保持既有行为）。

    ## `held_since` / `days_since_rebalance`：如实标注

    - `held_since`：**建仓日**。🚨 **可能是 `None`** ⇒ 那是「建仓日未知」
      （**不许拿 `as_of` 顶替** —— `as_of` 是**最后出单日**，
      非调仓日两者必然不同）。
    - `days_since_rebalance`：距上次换仓的**进度**（如 `5/21`）。

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

    # ── 可买性过滤（**配置层**，2026-10-21）────────────────────────
    # 剔掉「1 手金额 > 每份金额」的票 ⇒ **不足就少给几只**（不许拿买不起的凑数）
    # ⇒ 剩余预算留现金。
    # 🚨 **A/B 两类分列**（PREREG_AFFORDABILITY §2）：A = 1 手 > 全部本金
    # （物理不可能，与分配无关）；B = 1 手 > 每份金额（分配假设）。
    # 两者是**两句话** ⇒ 不许平均成一个「买不起率」。
    filter_report = None
    if closes is not None:
        picked, filter_report = _drop_unaffordable(
            picked, capital=capital, n_positions=max_positions,
            per_position_amount=capital * per_stock_cap, closes=closes)

    # 🚨 **非调仓日**：产物描述的是「**我持有什么**」（沿用上期名单），
    # **不是**今天的推荐名单 ⇒ 用今天的名单冒充持仓**正是**原 bug。
    # ⇒ `positions` = 沿用的持仓，`rebalance` = **空**（没有订单）。
    held = []
    if not is_rebalance_day:
        for p in current_positions:
            if isinstance(p, dict) and p.get("code"):
                held.append({"code": str(p["code"]), "name": p.get("name", "")})
        positions = [
            {
                "code": h["code"],
                "name": h["name"],
                "weight": per_stock_cap,
                "amount": round(capital * per_stock_cap, 2),
                "score": None,          # 沿用的持仓**没有**今天的分数
            }
            for h in held
        ]
        equity_weight = len(positions) * per_stock_cap
        cash_weight = round(1.0 - equity_weight, 10)
        return AllocationPlan(
            capital=capital,
            positions=positions,
            cash_weight=cash_weight,
            rebalance=[],                       # 🚨 非调仓日**没有订单**
            rebalance_source=("current_positions" if has_current else "none"),
            # ⚠️🚨 钱包读不出来时，**不许**把「空的位置列表」当成「空仓」
            #    ⇒ 显式标成第三态，让消费方（界面/日志）说「读不出来」。
            holdings_known=has_current,
            risk_notes=_risk_notes(capital, per_stock_cap, equity_weight,
                                   cash_weight, positions,
                                   holdings_known=has_current),
            is_rebalance_day=False,
            days_since_rebalance=days_since_rebalance,
            rebalance_every=rebalance_every,
            held_since=held_since,
            held=held,
            affordability=filter_report,
        )

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
        #    （`holdings.json` 存**代码与名字**；**权重与金额在这里算**，
        #    不从状态文件读 ⇒ 那是另一份真相源）⇒ 编一个数出来就是凭空多算一笔账。
        rebalance.append({"code": c, "name": cur.get("name", ""), "action": "remove",
                          "weight": 0.0, "amount": 0.0})

    risk_notes = _risk_notes(capital, per_stock_cap, equity_weight,
                             cash_weight, positions)
    return AllocationPlan(
        capital=capital,
        positions=positions,
        cash_weight=cash_weight,
        rebalance=rebalance,
        rebalance_source="current_positions" if has_current else "none",
        risk_notes=risk_notes,
        is_rebalance_day=True,
        days_since_rebalance=days_since_rebalance,
        rebalance_every=rebalance_every,
        held_since=held_since,
        held=held,
        affordability=filter_report,
    )


def _risk_notes(capital: float, per_stock_cap: float, equity_weight: float,
                cash_weight: float, positions: list,
                holdings_known: bool = True) -> list:
    """风控注记（**真话**：兑现得了的约束写出来，兑现不了的**不许承诺**）。

    ## 🚨 `holdings_known=False` 时**不许**算敞口

    手上那批读不出来 ⇒ `positions` 是**空列表**（不是「空仓」）⇒
    若照常按 `len(positions)` 算，就会写出
    「**敞口 = 0 只 × 10% = 0%（现金 100%）**」
    ⇒ **那是一句假话**（它说的是「你该 100% 现金」⇒ **让人卖光**）。
    ⇒ 这一档必须**明说读不出来**，不许给一个具体的数字。
    """
    if not holdings_known:
        return [
            "🚨🚨 **手上那批读不出来**（状态里没有「哪几只真的拿到了钱」这一列）"
            "⇒ 本方案**不描述**你的持仓。"
            "⚠️ **不许**把它读成「空仓」或「该 100% 现金」——"
            "**「读不出来」与「空仓」是两件事**（错的方向是让人卖光）。"
            "请以**券商账户**为准。",
            f"**整手约束**：每份 ¥{capital * per_stock_cap:,.0f} 按**整手**取整"
            "（科创板 688/689 起买 **200 股**，其余 100 股）⇒ "
            "**买不起 1 手的不下单**，该份资金留在现金里（按 0 计息）。",
        ]
    return [
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
        f"**敞口 = {len(positions)} 只 × {per_stock_cap:.0%} = "
        f"{equity_weight:.0%}**（现金 {cash_weight:.0%}）。"
        "🚨 **这是 beta 档位，不是「策略变好了」**："
        "市场跌 30% 的年份本账户约跌 30%（敞口 0.5 时约 15%）。"
        "满仓的理由是**目标相容性**——半仓时市场腿只贡献约 4.97%，"
        "剩下 15pp 要由半仓那 2.5 万赚出（需 30%/年），"
        "而多头毛超额上限实测约 11%/年 ⇒ 20% 在半仓下结构上不可达。"
        "选股权（超额腿）在两档里**完全相同**。",
        "年化目标 20% 属进取型（沪深300 长期约 5-8%）："
        "**该目标尚无测量支持**（当前账户口径读数 12.15pp 缺口、95% 上界 24.45% ⇒ "
        "🚨 **连符号都定不了**，不是「接近了」）；"
        "**满仓是按目标选定的 beta 档位**，不代表信号已被证明有效。",
    ]


# ── 可买性过滤（**配置层**，2026-10-21）────────────────────────────────
#
# 预注册：`docs/PREREG_PRODUCTION_CONFIG_2026-10-21.md` §3
# 门槛：`docs/PREREG_AFFORDABILITY_2026-10-16.md` §2（纯算术）
#
# 🚨 **它在配置层，不在名单层**：它决定「**买哪几只**」，
# **不决定「排第几」** ⇒ 发布的 10 涨 10 跌**仍然是全市场真正的前 10 / 后 10**
# ⇒ 前瞻底稿的 6 个窗口**不被作废**。

#: 整手股数规则的**名字**（进产物口径块 ⇒ 规则改了口径就变）。
#:
#: ⚠️ 规则本身**不在这里实现**：仓内已有两份实现
#: （`aplus/webapp.py::lot_shares_for`、`scripts/measure_deployed_d2.py::lot_shares`）
#: ⇒ **复用，不重写第三份**（第 97 层）。
LOT_SHARES_RULE_NAME = "688/689=200股,其余100股(外部交易所规则,本仓无数据源)"


def _lot_cost(close, code: str) -> float | None:
    """**1 手的金额** = 不复权 close × 该票的起买股数。

    ## 整手规则：**复用仓内已有实现**，不重写第三份

    `aplus/webapp.py::lot_shares_for`（688/689 = 200 股，其余 100 股）。
    ⚠️ 这里**延迟 import**：`aplus/webapp.py` import 了 `aplus.reports`
    ⇒ 模块层 import 会成环（`alloc → webapp → reports → …`）。

    ## 缺价 ⇒ `None`（**不是 0**）

    编 0 会被读成「免费」⇒ 可买率虚高。`None` 的含义是「判不出」。
    """
    from .webapp import lot_shares_for

    if close is None:
        return None
    try:
        px = float(close)
    except (TypeError, ValueError):
        return None
    if not (px == px) or px <= 0:      # NaN / 非正
        return None
    return px * int(lot_shares_for(code))


def _classify_affordability(lot: float | None, capital: float,
                             per_position_amount: float) -> str:
    """把一只票判成 A / B / 可买 / **判不出**（四态）。

    | 值 | 含义 |
    |---|---|
    | `"AFFORDABLE"` | `lot_cost ≤ per_position_amount`（买得起） |
    | `"B_ALLOCATION"` | 每份金额买不下、但**全部本金买得起** ⇒ **分配假设**问题 |
    | `"A_PHYSICAL"` | 连**全部本金**都买不下 ⇒ **物理不可能** |
    | `"PRICE_MISSING"` | **判不出**（缺价）⇒ **不许编 0** |

    ⚠️ **A 与 B 必须分开**：它们是**两句话**
    （「物理不可能」vs「这份配置下买不到」）⇒ 不许平均成一个「买不起率」。
    """
    if lot is None:
        return "PRICE_MISSING"
    if lot > float(capital):
        return "A_PHYSICAL"
    if lot > float(per_position_amount):
        return "B_ALLOCATION"
    return "AFFORDABLE"


def _drop_unaffordable(picked: pd.DataFrame, *, capital: float,
                       n_positions: int, per_position_amount: float,
                       closes: dict) -> tuple[pd.DataFrame, dict]:
    """剔掉买不起的票，并**报告剔了谁**（计数 + 名单）。

    ## 🚨 `n_positions` 是**显式入参、不得有默认值**

    `docs/PREREG_AFFORDABILITY_2026-10-16.md` §6.2：
    有默认值 ⇒ 「判决臂 vs 敏感性臂」的区分会被某次调用**悄悄抹掉**
    （10 仓与 20 仓是两条不同的臂）⇒ **少传必须 `TypeError``。

    ## 🚨 门槛用**真的每份金额**（`per_position_amount`），并把分歧**显式报出**

    「每份金额」有两个写法：`capital × per_stock_cap`（**方案里每只真拿到多少**）
    与 `capital / n_positions`（等权一份）。
    🚨 **B 类判据必须用前者** —— 用户按方案买，每只票的实际预算就是它
    ⇒ 用后者当门槛会按一个**方案里不存在**的钱去剔票。
    ⇒ 两者**不一致时**（`n_positions × per_stock_cap ≠ 1`）报告里
    `threshold_consistent=False` ⇒ **看得见**，不许静默。

    ## 🚨 不足就**少给几只**（不许拿买不起的凑数）

    ⇒ 剩余预算**留现金**（`cash_weight` 自动变大）。

    ## 🚨 剔除**必须可见**

    返回的报告里有**计数**（A / B / 缺价分开）与**名单**
    ⇒ 「平台每天在悄悄剔股票」这件事在产物里**看得见**。
    """
    per_position_amount = float(per_position_amount)
    capital_div_positions = float(capital) / int(n_positions)
    kept, report = [], {
        "scope": "up_only",
        "capital": float(capital),
        "n_positions": int(n_positions),
        "per_position_amount": per_position_amount,
        "capital_div_positions": capital_div_positions,
        "threshold_consistent": bool(abs(per_position_amount - capital_div_positions) < 1e-9),
        "lot_shares_rule": LOT_SHARES_RULE_NAME,
        "price_rule": ("不复权 close，取**信号日当天或之前最后一根 bar**"
                       "（出单时刻真正能看到的价）"),
        "n_candidates": int(len(picked)),
        "n_kept": 0,
        "n_B_allocation": 0,
        "n_A_physical": 0,
        "n_price_missing": 0,
        "removed_B_allocation": [],
        "removed_A_physical": [],
        "removed_price_missing": [],
    }
    for _, row in picked.iterrows():
        code = str(row["code"])
        lot = _lot_cost(closes.get(code), code)
        cls = _classify_affordability(lot, capital, per_position_amount)
        if cls == "AFFORDABLE":
            report["n_kept"] += 1
            kept.append(row)
        elif cls == "B_ALLOCATION":
            report["n_B_allocation"] += 1
            report["removed_B_allocation"].append(code)
        elif cls == "A_PHYSICAL":
            report["n_A_physical"] += 1
            report["removed_A_physical"].append(code)
        else:
            report["n_price_missing"] += 1
            report["removed_price_missing"].append(code)
    out = (pd.DataFrame(kept).reset_index(drop=True) if kept
           else picked.iloc[0:0].copy().reset_index(drop=True))
    return out, report

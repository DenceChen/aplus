"""交易成本：**按本金建模**，而不是按费率。

## 为什么不能只用一个费率

`backtest.STOCK_COST_DEFAULT = 0.003`（双边 0.3%）是按费率给的。
费率在**大额**时才接近真实成本，而在**小额**上有两条硬约束会把它顶穿：

1. **最低佣金 ¥5/笔**。费率万三听起来只有 0.03%，但单笔 ¥5,000 时
   0.03% 只有 ¥1.50——**按 ¥5 收**。于是实际费率是 0.10%，**3.3 倍**。
   跌破这个点：`notional < 5 / 0.0003 = ¥16,667`，单笔就开始吃最低佣金。
2. **印花税 0.05%，只在卖出侧**。万 15/边的费率里通常没算它
   （转债没有印花税，股票有——`bond_horizon` 的口径是照股票侧对齐的，
   不是照转债）。

所以：**同一套费率，本金 100 万和 5 万的实际成本差 2 倍以上。**
而本项目的本金就是 5 万。

## 实测差距（2026-10-14，本金 ¥50,000，5 仓位 × 10%）

    回测假设（双边/权益）   0.30%
    本模块实测（双边/权益）  0.55%      ← 高出 0.25 个百分点

    月度调仓 → 每年高估约 3 个百分点的净收益

**方向要说清楚：回测把成本算低了，所以已报告的净收益偏乐观。**
已发布的策略结论是零结果或负的，扣掉这 0.25%/轮只会更差——所以
**结论方向不变**，但绝对数字必须按本模块重算。

## 日频那条线

| 调仓频率 | 年化摩擦/权益 | 要让净值 20%，信号须给出 |
|---|---|---|
| 日频 | 138.00% | **158.00%** |
| 每周 | 28.70% | 48.70% |
| 每两周 | 14.35% | 34.35% |
| 每月 | 6.62% | 26.62% |
| 每季 | 2.21% | 22.21% |

**日频调仓的 158% 不是「难」，是「不存在」。**
而用户的目标是「每天推荐涨的跌的各 10 只」——**每天推荐 ≠ 每天交易**。
推荐可以每日，持仓周期必须拉长。这条区分是本模块存在的理由。

## 口径依据

- 佣金：万 2.5~3，单笔最低 ¥5（A股通行）
- 印花税：0.05%，仅卖出侧（2023-08-28 由 0.1% 降至 0.05%）
- 过户费：0.001%，双向（2022 起沪深统一）
- 价差：默认单边 0.15%（≈ 1.5 个最小价位变动，股价越低价差占比越大）
"""
from __future__ import annotations

from dataclasses import dataclass

# ── A 股费率（2026 现行）────────────────────────────────────────────────
COMMISSION_RATE = 0.0003      # 万三
COMMISSION_MIN = 5.0          # 单笔最低 ¥5
STAMP_DUTY_SELL = 0.0005      # 印花税，仅卖出侧
TRANSFER_FEE = 0.00001        # 过户费，双向
SPREAD_ONE_WAY = 0.0015       # 价差单边，往返 ×2

#: 低于这个单笔金额，最低佣金开始咬人（= 5 / 万三）。
COMMISSION_MIN_BINDS_BELOW = COMMISSION_MIN / COMMISSION_RATE   # ¥16,666.67

TRADING_DAYS_PER_YEAR = 250


@dataclass(frozen=True)
class FeeSchedule:
    """费率参数化是为了让测试能造极端场景，不是为了配置化。

    生产上就是上面那套常量；参数存在的意义是**让「最低佣金何时生效」
    这件事可以被验证**，而不是靠注释声称它生效了。
    """
    commission_rate: float = COMMISSION_RATE
    commission_min: float = COMMISSION_MIN
    stamp_duty_sell: float = STAMP_DUTY_SELL
    transfer_fee: float = TRANSFER_FEE
    spread_one_way: float = SPREAD_ONE_WAY

    def commission(self, notional: float) -> float:
        """单边佣金。**最低佣金在这里生效，不在别处。**"""
        if notional <= 0:
            return 0.0
        return max(notional * self.commission_rate, self.commission_min)

    def one_way(self, notional: float) -> float:
        """单边总成本（佣金 + 过户费 + 价差）。不含印花税。"""
        return (self.commission(notional)
                + notional * self.transfer_fee
                + notional * self.spread_one_way)

    def buy(self, notional: float) -> float:
        return self.one_way(notional)

    def sell(self, notional: float) -> float:
        return self.one_way(notional) + notional * self.stamp_duty_sell

    def round_trip(self, notional: float) -> float:
        """一轮「买入 + 卖出」的成本。**换手一次就算一轮。**"""
        return self.buy(notional) + self.sell(notional)

    def rebalance(self, notional_total: float, n_positions: int) -> float:
        """整轮调仓的成本：先把总敞口按仓位均分，再逐笔算，最后求和。

        **必须逐笔算而不是先求和再乘费率**——最低佣金是**非线性**的：
        先求和会把 5 笔 ¥5,000（各吃 ¥5 最低）算成 1 笔 ¥25,000（只吃 ¥7.5），
        成本凭空少掉 6 倍多。这正是原回测口径的偏差来源。
        """
        if n_positions <= 0:
            return 0.0
        per = notional_total / n_positions
        return sum(self.round_trip(per) for _ in range(n_positions))


def annual_friction(capital: float, equity_weight: float, n_positions: int,
                    rebalances_per_year: int, fee: FeeSchedule = FeeSchedule()
                    ) -> float:
    """年化摩擦成本，**占本金**的比例。"""
    notional = capital * equity_weight
    return fee.rebalance(notional, n_positions) * rebalances_per_year / capital


def gross_needed_for_net(capital: float, equity_weight: float, n_positions: int,
                         rebalances_per_year: int, net_target: float = 0.20,
                         fee: FeeSchedule = FeeSchedule()) -> float:
    """要让净值达到 `net_target`，毛收益（占**权益**）需要多少。

    摩擦按本金计，所以要先还原成权益口径——**分母搞错会让数字看着合理、
    实际错一倍**，这是这类换算最常见的翻车点。
    """
    if equity_weight <= 0:
        raise ValueError(f"equity_weight 必须为正，收到 {equity_weight}")
    drag_on_equity = annual_friction(
        capital, equity_weight, n_positions, rebalances_per_year, fee
    ) / equity_weight
    return net_target + drag_on_equity


def table(capital: float = 50_000.0, n_positions: int = 5,
          equity_weight: float = 0.5) -> list[dict]:
    """各调仓频率下的摩擦与所需毛收益。**权益 = 仓位数 × 单票上限。**"""
    return [
        {
            "label": label,
            "per_year": n,
            "annual_cost_on_capital": annual_friction(
                capital, equity_weight, n_positions, n),
            "annual_cost_on_equity": annual_friction(
                capital, equity_weight, n_positions, n) / equity_weight,
            "gross_needed": gross_needed_for_net(
                capital, equity_weight, n_positions, n),
        }
        for label, n in [("日频", TRADING_DAYS_PER_YEAR), ("每周", 52),
                         ("每两周", 26), ("每月", 12), ("每季", 4)]
    ]


if __name__ == "__main__":
    print(f"本金 ¥{50_000:,.0f} | 5 仓位 × 10% = 权益 50%\n")
    print(f"{'频率':<8}{'年化/本金':>12}{'年化/权益':>12}{'信号须给出':>12}")
    print("-" * 44)
    for r in table():
        print(f"{r['label']:<8}{r['annual_cost_on_capital'] * 100:>11.2f}%"
              f"{r['annual_cost_on_equity'] * 100:>11.2f}%"
              f"{r['gross_needed'] * 100:>11.2f}%")

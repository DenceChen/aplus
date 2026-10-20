#!/usr/bin/env python
"""按平台「真正会执行的那个账户」测 D2。

**预注册**：`docs/PREREG_DEPLOYED_D2_2026-10-20.md`（写在看任何数之前）。
**产物**：`data/reports/deployed_d2_verdict.json`（+ sidecar）。

## 它回答的唯一问题

**按平台「真正会执行的那个账户」的配置，5 万本金的年化是多少、离 20% 差多少、能不能定符号。**

屏幕上的 D2 读数（`basket_nav_01_2026-10-16.json`）测的是**满仓 10 只 + 8 因子逐年 IC 定权**、
**小数股**、**换手×费率**成本。平台实盘跑的是**半仓 5 只 + 4 因子 `DEFAULT_WEIGHTS`**、
**整手**、**逐笔成本**。⇒ 三层口径错位（敞口/持仓数/信号）＋两层执行层错位（整手/成本模型）。

## 硬约束（违反即作废）

1. **公式不许重写**：`judge()` / `account_nav()` / `excess_contribution()` /
   `required_excess()` / `tier()` 一律从 `scripts/measure_basket_nav_01.py` import。
2. **窗口钉死** `--start 2021-01-01 --end 2026-10-12`；`--end` 留空 ⇒ 拒绝（形状 23）。
3. **`--data-root` 必须是仓内 `data/`**（快照会漂）。
4. **不改 `aplus/` 生产代码**。
5. **`expect_bytes` 必须在 `main()` 的第一条可执行语句取**（第 125 层）。
6. **盖章**：`provenance.stamp(out, GENERATOR, root=ROOT, expect_bytes=..., extra={...})`。
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd


def _repo_root() -> Path:
    """向上找 `aplus/hurdle.py` 作为仓库根标记。

    ⚠️ 不写死 `parents[1]`：那在 `/tmp` 下是 `/`，`from aplus.hurdle` 会炸成一个
    与算法无关的 ImportError。
    """
    here = Path(__file__).resolve()
    for cand in (here.parents[1], *here.parents, Path.cwd(), *Path.cwd().parents):
        if (cand / "aplus" / "hurdle.py").exists():
            return cand
    raise SystemExit("找不到仓库根（缺 aplus/hurdle.py）")


ROOT = _repo_root()
sys.path.insert(0, str(ROOT))

from aplus.hurdle import DAYS_PER_YEAR as R          # noqa: E402  244，唯一真相源
from aplus.costs import FeeSchedule                  # noqa: E402  逐笔成本链
from aplus.backtest import (ENTRY_LAG, build_cross_sections,  # noqa: E402
                            evaluate_weights)
from aplus.composite import unpack, walk_forward     # noqa: E402
from aplus.snapshot import load_factors_map           # noqa: E402
from aplus.store import LocalStore                   # noqa: E402
from aplus.capweight import implied_cap              # noqa: E402
# 🚨 **调仓周期只有一份真相源**：`aplus/evolve.py::LIVE_REBALANCE_EVERY`
#   （生产 `run_daily` 用的就是它）。本脚本**不许**再写一个本地常量 ——
#   两份真相源是本仓反复吃亏的形状（改了生产那份、这份还停在旧值，
#   于是「产物说的配置」与「真跑的配置」分叉，而没有任何东西会喊）。
from aplus.evolve import LIVE_REBALANCE_EVERY          # noqa: E402
from aplus import provenance                         # noqa: E402

GENERATOR = "scripts/measure_deployed_d2.py"
DEFAULT_OUT = ROOT / "data" / "reports" / "deployed_d2_verdict.json"
PREREG = "docs/PREREG_DEPLOYED_D2_2026-10-20.md"

#: 判决窗口（钉死，与全仓 20+ 份产物同口径）。
WINDOW_START_PINNED = "2021-01-01"
WINDOW_END_PINNED = "2026-10-12"

#: 用户目标（年化 20% 净值）。
TARGET = 0.20

#: 实盘本金与单票上限 —— `aplus/alloc.py::build_allocation` 的默认值。
CAPITAL = 50_000.0
PER_STOCK_CAP = 0.10
#: ⚠️ **敞口真相源也是生产那一份**（`aplus/alloc.py::build_allocation` 的默认值）：
#:   `max_positions=10` × `per_stock_cap=0.10` ⇒ **权益 100%**（满仓）。
#:   🚨 本脚本**不** import `build_allocation`（那会把回测引擎拖进 import 期），
#:   ⇒ 这里**只登记**生产值并在产物里逐项列出出处。
LIVE_MAX_POSITIONS = 10
LIVE_EQUITY_WEIGHT = 1.0

#: 整手股数。🚨 **科创板 200 股是交易所规则，不是本仓数据里的数** ——
#: 本仓只能证明「688/689 是科创板」（`aplus/panel.py::price_limit` 认这两个前缀），
#: 「起买 200 股」这条**没有仓内数据源**，所以它写成显式常量并在产物里自报来源等级。
LOT_SHARES_DEFAULT = 100
LOT_SHARES_STAR = 200
STAR_MARKET_PREFIXES = ("688", "689")

#: 调仓周期 K 的取值（**交易日**）。`rebalance_every = K` ⇒ 每 K 个交易日换一次仓。
#: ⚠️ **K 不降低推荐频率**：非调仓日沿用上期持仓（`aplus/backtest.py::_held_predictions`）
#: ⇒ 「每天出单」与「每 K 日交易」是两件事（`aplus/live_rebalance.py` 的设计）。
K_VALUES = (5, 10, 21, 42, 63)
#: K 轴网格的**基础配置**（K=21 的那几格复用既有 config_id ⇒ 不重跑）。
#: 🚨 **仓数固定 10 只**（交付形态）；权益变 ⇒ 每份金额变（50% ⇒ ¥2,500；100% ⇒ ¥5,000）。
K_GRID_BASE = (
    ("LIVE_DEFAULT", 10, "ARM_A", None, "S1_A10"),
    ("LIVE_DEFAULT", 10, "ARM_C", "P5", "S1_C10_P5"),
    ("BACKTEST_IC",  10, "ARM_A", None, "S2_A10"),
    ("BACKTEST_IC",  10, "ARM_C", "P5", "S2_C10_P5"),
)
#: K=63 的**前瞻证据**需要 189 个交易日（batch 5 的 C2）⇒ 前瞻轨要半年。
K63_FORWARD_EVIDENCE_TRADING_DAYS = 189

#: 闲置现金的敏感性档（主口径 0）。
CASH_SENSITIVITY_ANNUAL = 0.015

#: 每份预算档（**由配置派生，不许硬编码 5000** —— 见预注册 §3.0）。
#:   P5  = ¥5,000/份（= 50000 × per_stock_cap 0.10，也是 50000/10 满仓口径）
#:   P10 = ¥10,000/份（= 50000/5 满仓口径）
#: 🚨 **每份 × 仓数 > 本金 的组合禁止**（那等于加了仓）：
#:   10 只 ⇒ 每份 ≤ ¥5,000（满仓）；5 只 ⇒ 每份 ≤ ¥10,000（满仓）。
BUDGETS = {"P2500": 2_500.0, "P5": 5_000.0, "P10": 10_000.0}

#: 成本档（回测自带的两个）与本脚本的逐笔链**不是同一个模型**（预注册 §3.1）。
COST_TIER_TAGS = ("bt003", "real")


# ── 共享脚本的加载（按路径，不靠 cwd 的偶然状态）───────────────────

def _load_script(name: str, alias: str):
    """把 `scripts/<name>` 当**模块**加载。

    ⚠️ 必须按路径加载（`cap_exposure.py` 的同款纪律）：
      `import factor_attribution` 会依赖 `sys.path` 的偶然状态，
      在别的 cwd 下会拿到**另一个**文件。
    """
    spec = importlib.util.spec_from_file_location(alias, ROOT / "scripts" / name)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


_BN = None
_MBN = None
_FA = None


def load_backtest_walk_forward():
    global _BN
    if _BN is None:
        _BN = _load_script("backtest_walk_forward_weights.py", "_deployed_bn")
    return _BN


def load_measure_basket_nav_01():
    global _MBN
    if _MBN is None:
        _MBN = _load_script("measure_basket_nav_01.py", "_deployed_mbn")
    return _MBN


def load_factor_attribution():
    global _FA
    if _FA is None:
        _FA = _load_script("factor_attribution.py", "_deployed_fa")
    return _FA


# ── 纯函数：整手与可买性 ──────────────────────────────────────────

def lot_shares(code: str) -> int:
    """该票的**起买股数**（1 手）。

    🚨 **科创板 688/689 = 200 股是外部交易所规则**，本仓无数据源；
    其余（A 股主板/创业板/北交所）= 100 股。
    证据等级在产物里自报（`caliber.lot_size`）。
    """
    head = str(code or "").split(".")[0]
    return LOT_SHARES_STAR if head[:3] in STAR_MARKET_PREFIXES else LOT_SHARES_DEFAULT


def lot_cost(px: float | None, code: str) -> float:
    """**1 手的金额** = 成交价 × 起买股数。

    🚨 **价格缺失 ⇒ 无穷大**（不是 0、不是 NaN）：「判不出」不许被读成「免费」。
    """
    if px is None:
        return float("inf")
    try:
        px = float(px)
    except (TypeError, ValueError):
        return float("inf")
    if not np.isfinite(px) or px <= 0:
        return float("inf")
    return px * lot_shares(code)


def slot_budget_from(capital: float, n_positions: int) -> float:
    """每份金额 = `CAP / n_positions`（等权假设）。

    🚨 **`n_positions` 没有默认值**（事前预注册 `docs/PREREG_AFFORDABILITY_2026-10-16.md`
    §2/§6.2）：少传参数必须 `TypeError` ——
    有默认值 ⇒「10 仓」与「20 仓」的区分会被某次调用悄悄抹掉。
    """
    if n_positions is None:
        raise TypeError("n_positions 必须显式传入（不许有默认值）")
    n = int(n_positions)
    if n <= 0:
        raise ValueError(f"仓数必须为正，收到 {n_positions!r}")
    return float(capital) / n


def is_affordable_physical(px: float | None, code: str, capital: float) -> bool:
    """**A 类（物理）**：1 手金额 ≤ **全部本金**。

    ⇒ **与仓数/资金分配完全无关** ⇒ 超过即**物理不可能**（哪怕全仓押一只）。
    🚨 **与 B 类不是同一个量**（第 7 层），**不许**把两者平均成一个「买不起率」。
    """
    return lot_cost(px, code) <= float(capital)


def fits_equal_weight_slot(px: float | None, code: str, capital: float,
                            n_positions: int) -> bool:
    """**B 类（分配）**：1 手金额 ≤ `CAP / n_positions`（等权一份）。

    ⚠️ **这是分配假设，不是物理约束**：超过 ⇒ 「按等权一份买不下，得集中」，
    **不是**「买不了」。
    """
    return lot_cost(px, code) <= slot_budget_from(capital, n_positions)


def affordable_at(px: float | None, code: str, slot_budget: float) -> bool:
    """**B 类（分配）**的判定：1 手金额 ≤ 给定的每份金额。

    🚨 **信号缺失不默认放行**（与 `aplus/panel.py::can_buy` 同纪律）：
      判不出 ⇒ 不放行（`lot_cost` 把缺失价当无穷大）。
    ⚠️ **这不是物理约束** —— 见 `is_affordable_physical`。
    """
    return lot_cost(px, code) <= float(slot_budget)


# ── 纯函数：每份预算（从配置派生）─────────────────────────────────

def slot_budget_for(capital: float, equity_weight: float, n_slots: int) -> float:
    """每份金额 = 本金 × 权益敞口 ÷ 持仓数。

    实盘：`capital=50000`、`per_stock_cap=0.10`、5 只 ⇒ **¥5,000/份**（权益 50%）。
    满仓 10 只：`50000/10` ⇒ **¥5,000/份**（权益 100%）。
    ⇒ **两档每份金额相同但总敞口差一倍**（预注册 §3.0）—— 不许混引。
    """
    if n_slots <= 0:
        raise ValueError(f"持仓数必须为正，收到 {n_slots!r}")
    return float(capital) * float(equity_weight) / int(n_slots)


# ── 纯函数：账户引擎 ──────────────────────────────────────────────

def simulate_account(steps, *, capital: float, equity_weight: float, n_slots: int,
                     slot_budget: float, lot_sizes: dict | None = None,
                     cash_annual_return: float = 0.0, fee: FeeSchedule | None = None,
                     allocation_mode: str = "fixed_slot") -> dict:
    """**实盘账户**模拟：整手取整 + 逐日盯市 + 逐笔成本。

    ## 口径（预注册 §3.3）

    * **买入**：调仓日 D 的成交日 e(D) = 该票自己时间轴上第一根 > D 的 bar；
      `shares = floor(slot_budget ÷ (px × lot)) × lot`；`实投 = shares × px`。
      **买不进的票（0 手）不投，钱留现金。**
    * **每份预算含它自己的买入费** ⇒ `实投 + 买入费 ≤ 每份预算`。
      这样「总权益 ≤ 本金 × 敞口」恒成立，现金池不会变负。
    * **持有**：逐日 `value_i ← value_i × (1 + r_i(t))`。
    * **卖出 + 换仓**：下一个调仓日全部卖出（付卖出侧成本），再按新名单买入。
      **换仓日当天的收益只归新篮子**（旧篮子在 e(D') 收盘已变现）。
    * **现金**：主口径收益 0；`cash_annual_return` 给敏感性档。
    * **成本**：`aplus/costs.py::FeeSchedule` **逐笔**算（不是 `cost_drag`）。

    ## `allocation_mode`（ARM_B / ARM_D 的唯一差别）

    * `fixed_slot`（**ARM_B**，默认）：每只一份 `slot_budget`，**买不满就留现金**。
    * `use_full_budget`（**ARM_D**）：**N = 按 `slot_budget` 买得起 1 手的只数**
      ⇒ 每只实际拿 `本金 × 敞口 ÷ N` ⇒ **预算用满**（**整手粒度余数仍留现金** ——
      **你买不了 1/3 个手**）。**N 的定义不循环**（先按固定份数判可买性，
      再定每只金额）⇒ 与「买得起 8 只 ⇒ 每只 ¥6,250」逐字一致。

    ⚠️ **ARM_D 的收益只来自 B 类闲置**（买不到 1 手的那些 slot），
    **整手粒度余数它救不回来** ⇒ `idle_cash_decomposition` 把两者**分列**。

    ⚠️ **每份预算是固定的**（`capital × 敞口 ÷ 持仓数`），不随账户增长放大 ——
    这与平台 `build_allocation(capital=50_000)` 的固定本金行为一致。
    """
    fee = fee or FeeSchedule()
    lot_sizes = lot_sizes or {}

    cash = float(capital)                      # 初始全是现金；买入后剩下的就是闲置
    positions: dict[str, list] = {}            # code -> [shares, value]
    total_fees = 0.0
    n_bought = 0
    n_unaffordable = 0
    n_rebalances = 0
    invested_last = 0.0
    idle_last = float(capital)
    if allocation_mode not in ("fixed_slot", "use_full_budget"):
        raise ValueError(f"allocation_mode 只能是 fixed_slot/use_full_budget，"
                         f"收到 {allocation_mode!r}")
    intended_equity = float(capital) * float(equity_weight)
    idle_unbuyable_sum = 0.0      # 买不到 1 手 ⇒ 那些 slot 的钱
    idle_granularity_sum = 0.0     # 买不满一手 ⇒ 整手粒度余数
    idle_unused_slots_sum = 0.0    # 名额没被占满（选出来的票少于仓数）
    actual_amount_sum = 0.0
    bought_per_rebalance: list[int] = []
    daily_cash = float(cash_annual_return) / float(R)

    dates: list[str] = []
    values: list[float] = []
    rets: list[float] = []
    invested_at_rebalances: list[float] = []
    idle_at_rebalances: list[float] = []

    prev_total = float(capital)
    for st in steps:
        d = st["date"]
        if st.get("rebalance"):
            n_rebalances += 1
            for code, (_shares, val) in list(positions.items()):
                c = fee.sell(val)
                total_fees += c
                cash += val - c
            positions = {}
            invested = 0.0
            buy_list = list(st.get("buy") or [])
            # ── ARM_D：先按固定份数数「买得起 1 手」的只数 N，再定每只金额 ──
            if allocation_mode == "use_full_budget":
                # 🚨 **不动点 + 安全钳**。第一版按「固定份数下买得起的只数 N」
                #   定每只金额 = intended/N ⇒ 但**每只变大之后又有更多票买得起**
                #   ⇒ 实际买到的只数 > N ⇒ **超投** ⇒ 现金为负 ⇒ 账户净值为负
                #   ⇒ `log1p` 越界 ⇒ MDE 变 NaN（实测 K=5/K=10 的 ARM_D 四格）。
                # ⇒ 改成：迭代到不动点（每只金额 = intended ÷ 当前买得起的只数），
                #   **最后钳一次**「买到的只数 × 每只金额 ≤ intended」。
                amount = intended_equity / max(1, len(buy_list))
                for _ in range(8):
                    n_at = sum(1 for c, p in buy_list
                               if affordable_at(p, c, amount))
                    if n_at <= 0:
                        amount = slot_budget
                        break
                    new_amount = intended_equity / n_at
                    if abs(new_amount - amount) <= 1e-6 * max(1.0, amount):
                        amount = new_amount
                        break
                    amount = new_amount
                n_at = sum(1 for c, p in buy_list
                           if affordable_at(p, c, amount))
                if n_at * amount > intended_equity + 1e-6:
                    amount = intended_equity / max(1, n_at)
            else:
                n_buy = None
                amount = slot_budget
            gran = 0.0
            n_bought_names = 0
            for code, px in buy_list:
                if px is None:
                    n_unaffordable += 1
                    continue
                px = float(px)
                if not np.isfinite(px) or px <= 0:
                    n_unaffordable += 1
                    continue
                lot = int(lot_sizes.get(code, lot_shares(code)))
                if lot <= 0:
                    n_unaffordable += 1
                    continue
                # ⚠️ 用 `amount`（ARM_D 的每只金额），**不是** `slot_budget`
                shares = int(float(amount) // (px * lot)) * lot
                if shares <= 0:
                    n_unaffordable += 1
                    continue
                notional = shares * px
                while shares > 0:
                    c = fee.buy(notional)
                    if notional + c <= float(amount):
                        break
                    shares -= lot
                    notional = shares * px
                if shares <= 0:
                    n_unaffordable += 1
                    continue
                c = fee.buy(notional)
                cash -= notional + c
                total_fees += c
                positions[code] = [shares, notional]
                invested += notional
                n_bought += 1
                n_bought_names += 1
                gran += amount - notional
                actual_amount_sum += notional
            if allocation_mode == "use_full_budget":
                # ARM_D：买不到的票那份钱**被摊到买得到的票上** ⇒ 这部分不再是闲置
                idle_unbuyable_sum += 0.0
                idle_unused_slots_sum += max(0.0, intended_equity
                                             - n_bought_names * amount)
            else:
                idle_unbuyable_sum += (len(buy_list) - n_bought_names) * amount
                # ③「名额没占满」= **根本没被选出来**的那些仓位的钱
                idle_unused_slots_sum += max(0.0, intended_equity
                                             - len(buy_list) * amount)
            idle_granularity_sum += gran
            bought_per_rebalance.append(n_bought_names)
            invested_at_rebalances.append(invested)
            idle_at_rebalances.append(cash)
            invested_last = invested
            idle_last = cash

        rr = st.get("returns") or {}
        for code, pos in positions.items():
            r = rr.get(code)
            if r is not None and np.isfinite(r):
                pos[1] = pos[1] * (1.0 + float(r))
        if daily_cash:
            cash *= (1.0 + daily_cash)

        total = cash + sum(p[1] for p in positions.values())
        dates.append(d)
        values.append(total)
        if prev_total and prev_total > 0:
            rets.append(total / prev_total - 1.0)
        prev_total = total

    intended = float(n_rebalances) * int(n_slots) * float(slot_budget)
    invested_mean = (float(np.mean(invested_at_rebalances)) if invested_at_rebalances else 0.0)
    return {
        "dates": dates,
        "total_values": values,
        "daily_returns": rets,
        "total_fees": total_fees,
        "n_rebalances": n_rebalances,
        "n_bought": n_bought,
        "n_unaffordable": n_unaffordable,
        "invested_at_last_rebalance": invested_last,
        "idle_cash_at_last_rebalance": idle_last,
        "invested_at_rebalances": invested_at_rebalances,
        "idle_at_rebalances": idle_at_rebalances,
        "intended_equity_per_rebalance": float(n_slots) * float(slot_budget),
        "lot_investable_rate": (invested_mean / (float(n_slots) * float(slot_budget))
                                if n_slots and slot_budget else None),
        "lot_investable_rate_overall": (float(np.sum(invested_at_rebalances)) / intended
                                        if intended > 0 else None),
        "idle_cash_ratio_mean": (float(np.mean(idle_at_rebalances)) / float(capital)
                                 if idle_at_rebalances else None),
        "final_value": values[-1] if values else None,
        "allocation_mode": allocation_mode,
        "idle_cash_decomposition": {
            "intended_equity_per_rebalance": intended_equity,
            "invested_mean": (float(np.mean(invested_at_rebalances))
                              if invested_at_rebalances else None),
            "idle_from_unbuyable_mean": (idle_unbuyable_sum / n_rebalances
                                         if n_rebalances else None),
            "idle_from_lot_granularity_mean": (idle_granularity_sum / n_rebalances
                                              if n_rebalances else None),
            "idle_from_unused_slots_mean": (idle_unused_slots_sum / n_rebalances
                                             if n_rebalances else None),
            "idle_total_before_fees_mean": (
                (intended_equity - float(np.mean(invested_at_rebalances)))
                if invested_at_rebalances else None),
            "identity_note": ("恒等式：intended = invested + ①买不到 + ②整手粒度余数 "
                              "+ ③名额没占满（费用从闲置里扣，见 idle_after_fees）。"),
            "note": ("🚨 **闲置现金三分解**（每调仓日均值，¥）："
                     "① **买不到 1 手**（B 类）⇒ ARM_D **能**救回来；"
                     "② **整手粒度余数**（买不满一份）⇒ **ARM_D 救不回来**"
                     "（**你买不了 1/3 个手**）；"
                     "③ **名额没占满**（选出来的票少于仓数）⇒ ARM_D 救不回来。"
                     "⚠️ 「T3 的 18.1% 闲置」里**大部分是 ②** ⇒ "
                     "**把它当成「跟着市场腿走就能多赚 1.8pp」是错的**。"),
        },
        "mean_bought_names_per_rebalance": (float(np.mean(bought_per_rebalance))
                                           if bought_per_rebalance else None),
        "mean_actual_amount_per_bought_name": (actual_amount_sum / n_bought
                                                if n_bought else None),
    }


# ── 纯函数：判决（复用共享公式，不许本地重写）─────────────────────

def tier_stats(daily_returns) -> dict:
    """**复用** `measure_basket_nav_01.py::tier()`（不许重写公式）。"""
    mbn = load_measure_basket_nav_01()
    r = np.asarray(daily_returns, dtype=float)
    out = mbn.tier("deployed", r)
    return {k: v for k, v in out.items() if k != "tier"}


def verdict_for(cagr: float | None, mde: float | None, label: str) -> dict:
    """**复用** `measure_basket_nav_01.py::judge()`（预注册 §4 的四条穷举）。"""
    mbn = load_measure_basket_nav_01()
    if cagr is None or mde is None or not np.isfinite(cagr) or not np.isfinite(mde):
        return {"label": label, "cases": ["该档没有可判决的读数（序列为空或 n≤1）"],
                "upper_one_sided_95": None, "target": TARGET,
                "gap_to_target_pp": None, "measured_sign_determinable": False,
                "cagr": cagr, "mde_annual": mde}
    cases, u = mbn.judge(float(cagr), float(mde), label)
    return {
        "label": label,
        "cases": list(cases),
        "upper_one_sided_95": float(u),
        "target": TARGET,
        "gap_to_target_pp": (float(TARGET) - float(cagr)) * 100.0,
        "measured_sign_determinable": bool(abs(cagr) >= mde),
        "cagr": float(cagr),
        "mde_annual": float(mde),
    }


# ── CLI ───────────────────────────────────────────────────────────

def parse_args(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=WINDOW_START_PINNED)
    ap.add_argument("--end", default=WINDOW_END_PINNED)
    ap.add_argument("--data-root", type=Path, default=ROOT / "data")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--cache-dir", type=Path,
                    default=ROOT / "data" / "reports" / "_deployed_d2_arms")
    ap.add_argument("--capital", type=float, default=CAPITAL)
    ap.add_argument("--rebalance-every", type=int, default=LIVE_REBALANCE_EVERY)
    ap.add_argument("--only", default="", help="只跑这些 config_id（逗号分隔）")
    ap.add_argument("--no-cache", action="store_true")
    return ap.parse_args(argv)


def validate_args(args) -> None:
    """**默认末端是一个判据，判据必须能被直接问**（形状 23 / 第 124 层）。"""
    if not str(args.end).strip():
        print("🚨 `--end` 不许留空：末端必须显式钉死（形状 23：一只票定全池窗口）。"
              f"当前默认 {WINDOW_END_PINNED}。", file=sys.stderr)
        raise SystemExit(2)
    root = Path(args.data_root).resolve()
    try:
        root.relative_to(ROOT.resolve())
    except ValueError:
        print(f"🚨 `--data-root` 必须指向仓内（当前 {root}）。"
              "快照会漂且不自证（`ic_guided_race` 的老毛病）。", file=sys.stderr)
        raise SystemExit(2)
    if not str(args.start).strip():
        print("🚨 `--start` 不许留空。", file=sys.stderr)
        raise SystemExit(2)


# ── 面板与价格表 ──────────────────────────────────────────────────

def build_price_map(daily_map: dict, rebalance_days: list[str]) -> dict:
    """两个价格表（一次流式扫描）：

    * `entry`：**成交价** = 该票自己时间轴上第一根 > D 的 bar 的不复权 close。
      ⇒ 这就是 `_next_bars` 用的 entry 下标，**可买性判据与它同一条时间轴**。
    * `signal`：**信号日价** = 该票自己时间轴上**最后一根 ≤ D** 的 bar 的不复权 close。
      ⇒ 这是「出名单那一刻看得见的价格」。

    🚨 **不复权 close**：这是真实掏出去的钱（复权价只给收益链用）。
    🚨 **不复权 close ≠ 可买性判据的价格** —— 判据用 `entry`，
      因为「买得起」必须与「买得进」（`_next_bars` 的 entry 下标）用**同一条时间轴**。
    """
    entry: dict[str, dict] = {d: {} for d in rebalance_days}
    signal: dict[str, dict] = {d: {} for d in rebalance_days}
    for code, df in daily_map.items():
        if df is None or df.empty:
            continue
        s = df.sort_values("date", kind="stable")
        dates = s["date"].to_numpy(dtype="U10")
        closes = s["close"].to_numpy(float)
        for d in rebalance_days:
            pos = int(np.searchsorted(dates, d, side="right"))
            if pos < len(dates):
                entry[d][code] = float(closes[pos])
            i = pos - 1
            if i >= 0:
                signal[d][code] = float(closes[i])
    return {"entry": entry, "signal": signal}


def _spearman(x: np.ndarray, y: np.ndarray) -> float | None:
    """Spearman 秩相关（**手工实现**：本仓没有 scipy，`method="spearman"` 会 ImportError）。

    秩相关 = 两列各自取秩之后的 Pearson。
    """
    def _rank(a):
        order = np.argsort(a, kind="stable")
        ranks = np.empty(len(a), dtype=float)
        ranks[order] = np.arange(1, len(a) + 1, dtype=float)
        # 并列取平均秩（Spearman 的标准做法）
        s = pd.Series(a)
        r = s.rank(method="average").to_numpy(float)
        return r
    rx, ry = _rank(x), _rank(y)
    if len(rx) < 3:
        return None
    sx, sy = rx.std(), ry.std()
    if not np.isfinite(sx) or not np.isfinite(sy) or sx == 0 or sy == 0:
        return None
    c = float(((rx - rx.mean()) * (ry - ry.mean())).mean() / (sx * sy))
    return c if np.isfinite(c) else None


def _ranked_with_px(universe, prices: dict, weights):
    """按合成分排名的 (code, px) 列表（分数降序；缺价的 px = None）。"""
    if universe is None or universe.empty or "code" not in universe.columns:
        return []
    from aplus.factor import composite_score
    sc = composite_score(universe, weights)
    if sc is None or not len(sc):
        return []
    df = pd.DataFrame({"code": list(universe["code"]),
                       "score": np.asarray(sc, dtype=float)})
    df = df.replace([np.inf, -np.inf], np.nan).dropna()
    df = df.sort_values("score", ascending=False)
    return [(c, prices.get(c)) for c in df["code"]]


def score_price_rank_corr(universe, prices: dict, weights) -> float | None:
    """横截面上 **`score` 与价格的秩相关**（Spearman）。

    🚨 **它量化的是「高分 = 高价」这个选择效应**：分数越高 ⇒ 动量/高波动 ⇒ 越贵
    ⇒ 越买不起。**这是池子比例答不了的那一格**（池子说 89% 买得起，
    而实盘选出来的那 10 只里可能一半买不起）。

    ⚠️ 用的价格必须与**可买性判据同源**（`entry`）⇒ 这里传进来的
    `prices` 就是 `price_map["entry"][day]`。
    """
    if universe is None or universe.empty:
        return None
    from aplus.factor import composite_score
    sc = composite_score(universe, weights)
    if sc is None or not len(sc):
        return None
    codes = list(universe["code"])
    px = [prices.get(c, float("nan")) for c in codes]
    df = pd.DataFrame({"score": np.asarray(sc, dtype=float), "px": px})
    df = df.replace([np.inf, -np.inf], np.nan).dropna()
    if len(df) < 10:
        return None
    return _spearman(df["score"].to_numpy(float), df["px"].to_numpy(float))


#: (仓数 × 每份金额) 网格的轴（事前登记，见预注册 §3.2 追加）。
GRID_N_POSITIONS = (3, 5, 10, 20, 30)
GRID_BUDGETS = (2_500.0, 5_000.0, 10_000.0)


def affordability_grid(ranked_by_day: dict, pool_med: dict, capital: float) -> dict:
    """**(仓数 × 每份金额) 网格** —— 「平台实际选出来的那几只，在这一格下能执行吗」。

    🚨 **A 类与 B 类分列，绝不平均**（`docs/PREREG_AFFORDABILITY_2026-10-16.md` §2）：
      * `n_physically_unbuyable` = **A 类**（1 手 > 全部本金）⇒ **物理不可能**，
        **与仓数/分配无关** ⇒ 每一行的这个数**应当相同**（若不同 ⇒ 口径漂了）。
      * `n_cannot_afford_1_lot_at_budget` = **B 类**（1 手 > 该格的每份金额）
        ⇒ **分配假设**，超过 =「得集中」，**不是**「买不了」。
    """
    acc: dict[int, dict] = {}
    for n in GRID_N_POSITIONS:
        acc[n] = {"aff": {b: [] for b in GRID_BUDGETS},
                  "n_cannot": {b: [] for b in GRID_BUDGETS},
                  "n_phys": [], "star": [], "price_ratio": [], "n_sel": []}
    pool_phys: list[int] = []
    for day in sorted(ranked_by_day):
        ranked = ranked_by_day[day]
        if not ranked:
            continue
        pm = (pool_med.get(day) or {}).get("price")
        pool_phys.append(sum(1 for c, p in ranked
                              if not is_affordable_physical(p, c, capital)))
        for n in GRID_N_POSITIONS:
            sel = ranked[:n]
            if not sel:
                continue
            n_sel = len(sel)
            acc[n]["n_sel"].append(n_sel)
            acc[n]["star"].append(
                sum(1 for c, _p in sel if board_of(c) == "star_688_689") / n_sel)
            acc[n]["n_phys"].append(
                sum(1 for c, p in sel if not is_affordable_physical(p, c, capital)))
            for b in GRID_BUDGETS:
                cannot = sum(1 for c, p in sel if not affordable_at(p, c, b))
                acc[n]["n_cannot"][b].append(cannot)
                acc[n]["aff"][b].append((n_sel - cannot) / n_sel)
            if pm and np.isfinite(pm) and pm > 0:
                selp = [p for _c, p in sel if p is not None and np.isfinite(p)]
                if selp:
                    acc[n]["price_ratio"].append(float(np.median(selp)) / float(pm))

    def _mean(v):
        return float(np.mean(v)) if len(v) else None

    grid: dict = {
        "note": ("**平台实际选出来的那几只**（未过滤，按合成分排名）在每一格下的可执行性。"
                 "🚨 A 类（物理）与 B 类（分配）**分列**；"
                 "B 类超过 =「等权一份买不下，得集中」，**不是**「买不了」。"),
        "axes": {"n_positions": list(GRID_N_POSITIONS),
                 "per_slot_budget": list(GRID_BUDGETS)},
        "rows": {},
    }
    for n in GRID_N_POSITIONS:
        a = acc[n]
        grid["rows"][str(n)] = {
            "n_selected_mean": _mean(a["n_sel"]),
            "A_physical_n_unbuyable_mean": _mean(a["n_phys"]),
            "A_physical_note": ("1 手金额 > 全部本金（¥50,000）⇒ **物理不可能**；"
                                "与仓数/分配无关 ⇒ 各行应相同"),
            "price_median_over_pool_median": _mean(a["price_ratio"]),
            "star_share": _mean(a["star"]),
            "by_budget": {str(int(b)): {
                "B_n_cannot_afford_1_lot_mean": _mean(a["n_cannot"][b]),
                "B_affordable_rate": _mean(a["aff"][b]),
                "B_note": "每份金额 = ¥%s 的**等权一份**；超过 =「得集中」，不是「买不了」"
                          % f"{int(b):,}",
            } for b in GRID_BUDGETS},
        }
    grid["pool_A_physical_n_unbuyable_mean"] = _mean(pool_phys)
    grid["pool_note"] = ("候选池里 A 类（物理不可买）的数量 —— "
                         "**与选股结果无关**；池子比例**答不了**"
                         "「实盘选出来的那几只里有多少买不起」")
    return grid


def board_of(code: str) -> str:
    """板块标签（按代码前缀，与 `aplus/panel.py::price_limit` 同一套前缀）。"""
    head = str(code or "").split(".")[0]
    if head[:3] in ("688", "689"):
        return "star_688_689"
    if head[:3] in ("300", "301"):
        return "gem_300_301"
    if head[:2] in ("43", "83", "87", "92"):
        return "beijing"
    return "main"


def pool_medians_at(daily_map: dict, days: list[str], pool_codes: dict) -> dict:
    """`{日: {"cap": 池子流通市值中位数, "price": 池子成交价中位数}}`。

    🚨 **流式**算，不建全池缓存（`CapTable` 的日期数组要 ~350 MB）——
      面板已经是 GB 级，这里再叠一份就是 OOM 的配方。
    """
    cap_acc: dict[str, list] = {d: [] for d in days}
    px_acc: dict[str, list] = {d: [] for d in days}
    for code, df in daily_map.items():
        if df is None or df.empty:
            continue
        s = df.sort_values("date", kind="stable")
        dates = s["date"].to_numpy(dtype="U10")
        closes = s["close"].to_numpy(float)
        cap = pd.Series(implied_cap(s)).ffill().to_numpy(float)
        for d in days:
            if code not in pool_codes.get(d, ()):
                continue
            i = int(np.searchsorted(dates, d, side="right")) - 1
            if i >= 0 and np.isfinite(cap[i]):
                cap_acc[d].append(float(cap[i]))
            j = int(np.searchsorted(dates, d, side="right"))
            if j < len(dates) and np.isfinite(closes[j]):
                px_acc[d].append(float(closes[j]))
    return {
        d: {
            "cap": (float(np.median(cap_acc[d])) if cap_acc[d] else float("nan")),
            "price": (float(np.median(px_acc[d])) if px_acc[d] else float("nan")),
        } for d in days
    }


# ── 可买性过滤（score_transform 注入）────────────────────────────

def make_affordability_transform(price_map: dict, slot_budget: float, mode: str):
    """构造 `score_transform`：**只改涨腿的候选池**。

    ## 两个模式（预注册 §3.2）

    * `drop`（ARM_B）：把「1 手金额 > 每份预算」的票的分数置 NaN
      ⇒ 它们被 `build_predictions` 的 `notna` 过滤剔出池子
      ⇒ 涨腿只剩**买得起的**（池子不足 N 只时就取剩下的那些）。
    * `reorder`（ARM_C）：**不剔人**，只把买得起的排到前面
      （买得起的一组内保持原分数序，买不起的一组内也保持原分数序）
      ⇒ `head(N)` = 买得起的里分数最高的 N 只；
      **买得起的不足 N 只时，`head(N)` 自然补上分数最高的买不起的** ⇒ 凑满 N 只。

    🚨 **跌腿不受影响**：`score_transform` 作用于**整张横截面**，
      而 `build_predictions` 之后才分涨跌两腿 ⇒ 过滤**会**改到跌腿。
      ⇒ 本脚本**另跑一遍未过滤的臂**，把**原样的跌腿**取出来并排报
      （跌腿是信息产品，「整手买不起」≠「买不进」）。
    """
    if mode not in ("drop", "reorder"):
        raise ValueError(f"mode 只能是 drop/reorder，收到 {mode!r}")

    def tf(day, df):
        out = df.copy()
        if day is None or df.empty or "code" not in out.columns:
            return out
        prices = price_map.get(day) or {}
        budget = float(slot_budget)
        flags = np.array([affordable_at(prices.get(c), c, budget)
                          for c in out["code"]], dtype=bool)
        if mode == "drop":
            out.loc[~flags, "score"] = np.nan
            return out
        s = out["score"]
        finite = s.notna().to_numpy()
        if not finite.any():
            return out
        lo = float(s[finite].min())
        hi = float(s[finite].max())
        span = (hi - lo) if hi > lo else 1.0
        bump = span + abs(lo) + 1.0          # 保证「买得起的一组」整体高于另一组
        out.loc[finite & ~flags, "score"] = s[finite & ~flags] - bump
        return out

    return tf


# ── 单个配置（信号 × 可买性臂 × 预算）的跑法 ──────────────────────

def run_config(xs: dict, dates: list[str], by_year: dict, judged_years: list[int],
               weights_for_year, top_n: int, arm: str, budget_key: str | None,
               price_map: dict, rebalance_every: int, cap_table=None,
               pool_med: dict | None = None, capital: float = CAPITAL) -> dict:
    """跑完一个配置，产出逐年统计 + 逐日序列 + 逐票数据 + 暴露变化。"""
    bn = load_backtest_walk_forward()
    entry_px = price_map["entry"]
    signal_px = price_map["signal"]
    gross_parts, market_parts, net_parts, expo_parts = [], [], {}, {}
    down_parts = []
    name_ret: dict[str, dict] = {}
    baskets: dict[str, list] = {}
    years_out = []
    lot_sizes: dict[str, int] = {}
    n_sel_total = 0
    #: **按预算分列**的 B 类计数。🚨 ARM_A **也必须量** ——
    #: 用「没有过滤 ⇒ 阈值取 1e18」的回落会让 ARM_A 的「买不起率」恒等于 0，
    #: 而那**不是测量结果**、是口径的假象（第 112 层：恒真的东西比没有更坏）。
    n_cannot_by_budget: dict[float, int] = {b: 0 for b in GRID_BUDGETS}
    # 机制测量（预注册 §3.2 追加）
    corr_entry: list[float] = []
    corr_signal: list[float] = []
    basket_px_median: list[float] = []
    basket_cap_median: list[float] = []
    board_counts: dict[str, int] = {}
    basket_size: list[int] = []
    n_phys_sel = 0
    ranked_by_day: dict[str, list] = {}

    for y in judged_years:
        ydates = by_year[y]
        w = weights_for_year(y)
        st = None
        if arm != "ARM_A":
            st = make_affordability_transform(
                entry_px, BUDGETS[budget_key], "drop" if arm == "ARM_B" else "reorder")
        res = evaluate_weights(xs, weights=w, top_n=top_n, bottom_n=top_n,
                               dates=ydates, rebalance_every=rebalance_every,
                               execution_gate=True, score_transform=st)
        if res is None or res.empty:
            years_out.append({"year": y, "n_rows": 0, "n_days": len(ydates),
                              "note": "该年无成交观测"})
            continue
        parts = bn._daily(res.assign(cost=0.0), return_parts=True)
        if parts is None:
            continue
        gross, market = parts
        gross_parts.append(gross)
        market_parts.append(market)
        for tag, ser in bn._net_by_cost(res, None, top_n, rebalance_every).items():
            net_parts.setdefault(tag, []).append(ser)
        dn = bn._daily(res, "down")
        if dn is not None and len(dn):
            down_parts.append(dn)
        expo = bn._up_exposure(res, xs, list(gross.index), bn.EXPO_COLS,
                                cap_table=cap_table)
        for f, vals in expo.items():
            expo_parts.setdefault(f, []).extend(vals)

        # 逐年统计（口径与 `backtest_walk_forward_weights.py::run_year` 一致）
        row = {"year": y, "n_days": int(len(gross)),
               "gross_annual_pct": float(gross.mean()) * R,
               "daily_sd": float(gross.std(ddof=1)) if len(gross) > 1 else None}
        t, n_obs = bn._t(bn._daily(res.assign(cost=0.0)), block=rebalance_every)
        row["t_blocked"] = t
        row["n_independent_obs"] = n_obs
        for tag in bn.COST_TIER_TAGS:
            ser = net_parts.get(tag, [None])[-1]
            if ser is None or not len(ser):
                continue
            realized, net_annual, drift = bn.realized_cost_and_net(gross, ser)
            row[f"net_{tag}_annual_pct"] = net_annual
            row[f"realized_cost_{tag}_pct"] = realized
            row[f"cost_single_deduction_drift_{tag}"] = drift
        years_out.append(row)

        # 逐票数据（账户引擎要）与篮子（调仓日）
        from aplus.rebalance import is_rebalance_day
        up = res[res["direction"] == "up"]
        for day, grp in up.groupby("pred_date"):
            rr = {}
            for _, r in grp.iterrows():
                if r["actual_pct"] is not None and np.isfinite(r["actual_pct"]):
                    rr[r["code"]] = float(r["actual_pct"])
                    lot_sizes[r["code"]] = lot_shares(r["code"])
            name_ret[day] = rr
            if is_rebalance_day(ydates, day, rebalance_every):
                px = entry_px.get(day) or {}
                buy = []
                for c in rr:
                    p = px.get(c)
                    buy.append((c, p))
                    n_sel_total += 1
                    for b in GRID_BUDGETS:
                        if not affordable_at(p, c, b):
                            n_cannot_by_budget[b] += 1
                baskets[day] = buy
                # —— 机制测量 ——
                c_e = score_price_rank_corr(xs.get(day), px, w)
                if c_e is not None:
                    corr_entry.append(c_e)
                c_s = score_price_rank_corr(xs.get(day), signal_px.get(day) or {}, w)
                if c_s is not None:
                    corr_signal.append(c_s)
                sel_px = [p for _c, p in buy if p is not None and np.isfinite(p)]
                if sel_px:
                    basket_px_median.append(float(np.median(sel_px)))
                basket_size.append(len(buy))
                for c, _p in buy:
                    b = board_of(c)
                    board_counts[b] = board_counts.get(b, 0) + 1
                # A 类（物理不可买）计数 —— **与仓数/分配无关**
                n_phys_sel += sum(1 for c, p in buy
                                  if not is_affordable_physical(p, c, capital))
                if arm == "ARM_A":
                    # **未过滤**的排名（= 平台实际出单的那份）⇒ 网格的输入
                    ranked_by_day[day] = _ranked_with_px(xs.get(day), px, w)

    def _cat(parts):
        parts = [p for p in parts if p is not None and len(p)]
        if not parts:
            return None
        return pd.concat(parts).sort_index()

    gross = _cat(gross_parts)
    market = _cat(market_parts)
    down = _cat(down_parts)
    nets = {t: _cat(v) for t, v in net_parts.items()}
    expo = {f: pd.Series(v, index=gross.index) for f, v in expo_parts.items()
            if gross is not None} if gross is not None else {}

    # 组合价格/市值分位（相对池子中位数）
    price_ratio = None
    if pool_med and basket_px_median:
        ratios = []
        for i, med in enumerate(basket_px_median):
            pass
        # 逐日配对：basket_px_median 的顺序 = baskets 的键序（已排序）
        days_sorted = sorted(baskets.keys())
        for i, day in enumerate(days_sorted):
            pm = (pool_med.get(day) or {}).get("price")
            if pm is None or not np.isfinite(pm) or pm <= 0:
                continue
            sel = [p for _c, p in baskets[day] if p is not None and np.isfinite(p)]
            if not sel:
                continue
            ratios.append(float(np.median(sel)) / float(pm))
        price_ratio = (float(np.mean(ratios)) if ratios else None)

    total_board = sum(board_counts.values()) or 1
    grid = (affordability_grid(ranked_by_day, pool_med, capital)
            if ranked_by_day and pool_med else None)
    return {
        "years": years_out,
        "gross": gross, "market": market, "down": down, "nets": nets,
        "expo": expo, "name_ret": name_ret, "baskets": baskets,
        "lot_sizes": lot_sizes,
        "n_selected_total": n_sel_total,
        "n_cannot_afford_1_lot_by_budget": {str(int(b)): v
                                            for b, v in n_cannot_by_budget.items()},
        "grid": grid,
        "mechanism": {
            "score_price_rank_corr_entry": (float(np.mean(corr_entry))
                                            if corr_entry else None),
            "score_price_rank_corr_signal_day": (float(np.mean(corr_signal))
                                                 if corr_signal else None),
            "score_price_rank_corr_note": (
                "Spearman，**逐调仓日横截面**上算再取均值；"
                "正值 = 「分数越高 ⇒ 越贵」⇒ **选择效应**（不是池子的尾部）。"
                "entry 价 = 成交价（与可买性判据同源）；signal_day 价 = 出名单那一刻的价。"),
            "basket_price_median_over_pool_median": price_ratio,
            "basket_size_mean": (float(np.mean(basket_size)) if basket_size else None),
            "board_share": {k: v / total_board for k, v in board_counts.items()},
            "board_share_note": ("按**篮子成分**计（一只票在多个调仓日被选中就多次计）；"
                                 "688/689 = 科创板，300/301 = 创业板。"),
            "A_physical_n_unbuyable": n_phys_sel,
            "A_physical_note": ("**A 类（物理）**：1 手金额 > 全部本金（¥50,000）"
                                "⇒ 物理不可能；**与仓数/分配无关**。"
                                "🚨 与 B 类（分配）**不是同一个量**，不许平均。"),
        },
    }


# ── 账户腿 ────────────────────────────────────────────────────────

def basket_turnover(baskets: dict, top_n: int) -> dict:
    """**实测**的持仓换手率（不是理论）。

    口径与 `aplus/backtest.py::cost_drag` **同一约定**：
    `turnover = (n_in + n_out) / (2 · max(top_n, |cur|, |prev|))`，
    逐调仓日算完取均值。🚨 用**该臂自己的篮子**（`baskets`），不是选股名单。
    """
    days = sorted(baskets or {})
    turnovers: list[float] = []
    for i in range(1, len(days)):
        # 🚨 **比 code，不比 (code, px) 元组** —— 原来把元组当集合元素，
        #   而 px 每天都在变 ⇒ **每只都算「新股」** ⇒ 年化换手 4268%（无意义）。
        cur = {c for c, _p in baskets[days[i]]}
        prev = {c for c, _p in baskets[days[i - 1]]}
        denom = 2.0 * max(int(top_n), len(cur), len(prev))
        if denom > 0:
            turnovers.append((len(cur - prev) + len(prev - cur)) / denom)
    return {
        "mean_turnover_per_rebalance": (float(np.mean(turnovers)) if turnovers
                                        else None),
        "n_rebalances_observed": len(days),
    }


def build_steps(bundles: list[dict], dates_all: list[str]) -> list[dict]:
    """把若干配置（同一信号、同一篮子）的逐票数据并成一个账户时间轴。

    ⚠️ **多个配置的逐票数据不可混**（篮子不同 ⇒ 持仓不同）。
    本函数只用于**同一配置**的逐年分片拼接。
    """
    steps = []
    for d in dates_all:
        rr: dict = {}
        buy: list = []
        reb = False
        for b in bundles:
            rr.update(b["name_ret"].get(d, {}))
            if d in b["baskets"]:
                # ⚠️ 调仓日由**篮子键**判定，**不是**「买的是否为空」——
                #   一个空篮子的调仓日**仍然要清仓**，否则旧持仓会一直挂着。
                reb = True
                buy.extend(b["baskets"][d])
        steps.append({"date": d, "rebalance": reb, "buy": buy, "returns": rr})
    return steps


def account_leg(bundle: dict, dates_all: list[str], *, label: str, n_slots: int,
                 equity_weight: float, slot_budget: float, capital: float,
                 cash_annual_return: float = 0.0,
                 allocation_mode: str = "fixed_slot") -> dict:
    """一个账户档：模拟 + 判决 + 口径。"""
    steps = build_steps([bundle], dates_all)
    sim = simulate_account(steps, capital=capital, equity_weight=equity_weight,
                           n_slots=n_slots, slot_budget=slot_budget,
                           lot_sizes=bundle["lot_sizes"],
                           cash_annual_return=cash_annual_return,
                           allocation_mode=allocation_mode)
    rets = sim["daily_returns"]
    out: dict = {"label": label, "n_days": len(rets),
                 "capital": capital, "n_slots": n_slots,
                 "equity_weight": equity_weight, "slot_budget": slot_budget,
                 "cash_annual_return": cash_annual_return,
                 "lot_investable_rate": sim["lot_investable_rate"],
                 "lot_investable_rate_overall": sim["lot_investable_rate_overall"],
                 "idle_cash_ratio_mean": sim["idle_cash_ratio_mean"],
                 "n_bought": sim["n_bought"], "n_unaffordable": sim["n_unaffordable"],
                 "n_rebalances": sim["n_rebalances"],
                 "total_fees": sim["total_fees"],
                 "final_value": sim["final_value"],
                 "allocation_mode": sim["allocation_mode"],
                 "idle_cash_decomposition": sim["idle_cash_decomposition"],
                 "mean_bought_names_per_rebalance":
                     sim["mean_bought_names_per_rebalance"],
                 "mean_actual_amount_per_bought_name":
                     sim["mean_actual_amount_per_bought_name"]}
    if len(rets) >= 2:
        st = tier_stats(rets)
        out["tier"] = st
        out["verdict"] = verdict_for(st["cagr"], st["mde_as_annual_pct"], label)
    else:
        out["tier"] = None
        out["verdict"] = verdict_for(None, None, label)
    return out


# ── 产物落盘 ──────────────────────────────────────────────────────

def write_artifact(out: Path, payload: dict) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


# ── 主流程 ────────────────────────────────────────────────────────

def _weights_live(_year: int) -> dict:
    """实盘回落路径：`aplus/evolve.py::load_weights` 在 `weights.json` 不存在时
    走的那一份（`caveat_kind="unraced"`）。"""
    from aplus.factor import DEFAULT_WEIGHTS
    return dict(DEFAULT_WEIGHTS)


def main(argv=None) -> int:
    # 🚨 第 125 层：盖章那一刻盘上的字节 ≠ 这次执行真正跑的那些字节。
    #   抓在**跑之前**（`main()` 第一行）；传 None 会让 stamp() 整项跳过字节检查。
    expect_bytes = provenance.code_file_sha256(ROOT, GENERATOR)

    args = parse_args(argv)
    validate_args(args)

    t0 = time.time()
    store = LocalStore(args.data_root)
    files = sorted((store.root / "master" / "daily").glob("*.parquet"))
    if not files:
        print("🚨 主库为空", file=sys.stderr)
        return 2
    print(f"[1/6] 读日线 {len(files)} 只 …", flush=True)
    daily_map = {p.stem: store.read_daily(p.stem) for p in files}
    factors_map = load_factors_map(store)
    print(f"    日线 {len(daily_map)} 只（{time.time() - t0:.0f}s）", flush=True)

    print("[2/6] 建横截面面板 …", flush=True)
    xs = build_cross_sections(store, args.start, args.end, daily_map=daily_map,
                              factors_map=factors_map, entry_lag=ENTRY_LAG)
    dates = xs["_dates"]
    if not dates:
        print("🚨 面板为空，拒绝出判决", file=sys.stderr)
        return 2
    by_year: dict[int, list] = {}
    for d in dates:
        by_year.setdefault(int(str(d)[:4]), []).append(d)
    print(f"    横截面 {len(dates)} 个交易日"
          f"（{dates[0]} → {dates[-1]}）（{time.time() - t0:.0f}s）", flush=True)

    # IC 权重（对照臂）——与判决同一份抽取表、同一台仪器
    bn = load_backtest_walk_forward()
    matrices = ROOT / "data" / "reports" / "ic_matrices.parquet"
    wmap: dict[int, dict] = {}
    if matrices.is_file():
        df = pd.read_parquet(matrices)
        from aplus.factor import FACTOR_NAMES
        unpack(df, list(FACTOR_NAMES))
        wmap = bn.weights_by_year(df, list(FACTOR_NAMES))
        print(f"    IC 逐年权重：判决年 {sorted(y for y in wmap if y >= 2021)}",
              flush=True)
    else:
        print(f"🚨 抽取表不存在：{matrices}（对照臂不可用）", file=sys.stderr)
        return 2
    judged_years = sorted(y for y in by_year if y >= 2021 and y in wmap)
    if not judged_years:
        print("🚨 没有任何判决年份", file=sys.stderr)
        return 2

    # 调仓日（**逐年**日历 —— 与 `evaluate_weights(dates=by_year[y])` 内部同口径）
    from aplus.rebalance import is_rebalance_day
    # 🚨 **必须覆盖「所有 K 的调仓日」**：原来只覆盖 K=21 的逐年日历
    #   ⇒ K=5 / K=10 的调仓日不在价格表里 ⇒ 那些臂的绝大多数选择
    #   `px = None` ⇒ 被判成「买不起」⇒ **那两组臂的读数是坏的**
    #   （实测 K=5 臂：2,608/2,777 次选择 px=None）。
    #   K=42 / K=63 的日历是 K=21 的**子集**（42=2×21、63=3×21）⇒ 恰好没露馅。
    _ks = sorted(set(int(k) for k in K_VALUES) | {int(args.rebalance_every)})
    rebalance_days = sorted({d for y in judged_years
                             for d in by_year[y]
                             for kk in _ks
                             if is_rebalance_day(by_year[y], d, kk)})
    print(f"    调仓日历覆盖 K = {_ks} ⇒ 共 {len(rebalance_days)} 个调仓日", flush=True)
    print(f"[3/6] 调仓日 {len(rebalance_days)} 个（逐年日历）"
          f"（{time.time() - t0:.0f}s）", flush=True)

    print("[4/6] 建价格表与市值表 …", flush=True)
    price_map = build_price_map(daily_map, rebalance_days)
    pool_codes = {d: set(xs[d]["code"]) for d in rebalance_days if d in xs}
    pool_med = pool_medians_at(daily_map, rebalance_days, pool_codes)
    print(f"    价格表 {len(price_map['entry'])} 日（{time.time() - t0:.0f}s）", flush=True)

    # 配置表（**按优先级排序**：核心档先跑，跑不完也不丢核心）
    spec = [
        ("S1_A5",    "LIVE_DEFAULT", 5,  "ARM_A", None, 21),
        ("S1_A10",   "LIVE_DEFAULT", 10, "ARM_A", None, 21),
        ("S1_C5_P5", "LIVE_DEFAULT", 5,  "ARM_C", "P5", 21),
        ("S1_C10_P5", "LIVE_DEFAULT", 10, "ARM_C", "P5", 21),
        ("S2_A10",   "BACKTEST_IC",  10, "ARM_A", None, 21),
        ("S2_C10_P5", "BACKTEST_IC", 10, "ARM_C", "P5", 21),
        ("S1_B10_P5", "LIVE_DEFAULT", 10, "ARM_B", "P5", 21),
        ("S1_C10_P10", "LIVE_DEFAULT", 10, "ARM_C", "P10", 21),
        ("S1_B10_P10", "LIVE_DEFAULT", 10, "ARM_B", "P10", 21),
        ("S2_C10_P10", "BACKTEST_IC", 10, "ARM_C", "P10", 21),
        # 账户层网格需要「过滤器预算 = 该格账户的每份金额」的两格：
        #   10 只 × 50% 敞口 ⇒ 每份 ¥2,500；5 只 × 100% 敞口 ⇒ 每份 ¥10,000。
        ("S1_C10_P2500", "LIVE_DEFAULT", 10, "ARM_C", "P2500", 21),
        ("S1_C5_P10", "LIVE_DEFAULT", 5, "ARM_C", "P10", 21),
    ]
    # ── K（调仓周期）轴：账户层网格 = K × 权益 × 过滤 × 信号 ──────
    #  ⚠️ **K 的语义**：`rebalance_every = K` 交易日换一次仓；**非调仓日沿用上期持仓**
    #   （`aplus/backtest.py::_held_predictions`）⇒ **「每天出单」与「每 K 日交易」是两件事**
    #   （`aplus/live_rebalance.py` 的设计）⇒ **K 不降低推荐频率**。
    #  ⇒ 网格的**仓数固定 10 只**（交付形态），权益变 ⇒ 每份金额变：
    #     50% ⇒ ¥2,500/份；100% ⇒ ¥5,000/份。
    #  ⇒ **过滤阈值固定 ¥5,000**（任务书指定）⇒ 权益 50% 档「过滤比账户松」
    #     ⇒ 该档仍会有买不起的票（投入率会显示它）。
    for _sig, _tn, _arm, _bk, _base in K_GRID_BASE:
        for _k in K_VALUES:
            _cid = _base if _k == 21 else f"{_base}_K{_k}"
            spec.append((_cid, _sig, _tn, _arm, _bk, _k))
    if args.only.strip():
        want = {s.strip() for s in args.only.split(",") if s.strip()}
        spec = [s for s in spec if s[0] in want]

    cache_dir = Path(args.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    bundles: dict[str, dict] = {}
    for cid, signal, top_n, arm, budget_key, k_rebal in spec:
        cache = cache_dir / f"{cid}.json"
        if cache.is_file() and not args.no_cache:
            try:
                bundles[cid] = _load_cache(cache, daily_map)
                print(f"    [{cid}] 命中缓存", flush=True)
                continue
            except Exception as exc:                      # noqa: BLE001
                print(f"    [{cid}] 缓存不可用（{exc}），重跑", flush=True)
        wf = (_weights_live if signal == "LIVE_DEFAULT"
              else (lambda y: wmap[y]["weights"]))
        print(f"    [{cid}] signal={signal} top_n={top_n} arm={arm} "
              f"budget={budget_key} K={k_rebal} …", flush=True)
        b = run_config(xs, dates, by_year, judged_years, wf, top_n, arm,
                       budget_key, price_map, k_rebal,
                       pool_med=pool_med, capital=args.capital)
        bundles[cid] = b
        _save_cache(cache, b, daily_map)
        print(f"    [{cid}] 完成（{time.time() - t0:.0f}s）", flush=True)

    print(f"[5/6] 汇总（{time.time() - t0:.0f}s）", flush=True)
    payload = assemble(bundles, args, dates, judged_years, by_year,
                       price_map, pool_med, wmap, rebalance_days)
    write_artifact(args.out, payload)
    provenance.stamp(
        args.out, GENERATOR, root=ROOT, expect_bytes=expect_bytes,
        extra={
            "source_parquet_sha256": {
                "ic_matrices": hashlib.sha256(matrices.read_bytes()).hexdigest(),
                "wf_weights_backtest_k21_daily":
                    hashlib.sha256((ROOT / "data/reports/wf_weights_backtest_k21.daily.parquet")
                                   .read_bytes()).hexdigest(),
            },
            "data_root": str(Path(args.data_root).resolve()),
            "prereg": PREREG,
        })
    print(f"[6/6] → {args.out}（{time.time() - t0:.0f}s）", flush=True)
    _print_headline(args.out)
    return 0


# ── 缓存（每配置一个文件；崩了不丢已算完的臂）────────────────────

def _save_cache(path: Path, b: dict, daily_map: dict) -> None:
    """落盘一个配置的**标量结果**（逐日序列以紧凑格式存，便于复算）。"""
    out = {"years": b["years"],
           "gross": None if b["gross"] is None else b["gross"].to_dict(),
           "market": None if b["market"] is None else b["market"].to_dict(),
           "down": None if b["down"] is None else b["down"].to_dict(),
           "nets": {t: (None if s is None else s.to_dict())
                    for t, s in b["nets"].items()},
           "baskets": {d: [(c, p) for c, p in v] for d, v in b["baskets"].items()},
           "name_ret": {d: v for d, v in b["name_ret"].items()},
           "expo": {f: s.to_dict() for f, s in b["expo"].items()},
           "lot_sizes": b["lot_sizes"],
           "mechanism": b.get("mechanism"),
           "grid": b.get("grid"),
           "n_selected_total": b["n_selected_total"],
           "n_cannot_afford_1_lot_by_budget": b["n_cannot_afford_1_lot_by_budget"]}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")


def _load_cache(path: Path, daily_map: dict) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8"))
    def _ser(d):
        if d is None:
            return None
        s = pd.Series(d)
        s.index = pd.Index(list(s.index), name="date")
        return s.astype(float)
    return {
        "years": raw["years"],
        "gross": _ser(raw["gross"]), "market": _ser(raw["market"]),
        "down": _ser(raw["down"]),
        "nets": {t: _ser(v) for t, v in raw["nets"].items()},
        "baskets": {d: [tuple(x) for x in v] for d, v in raw["baskets"].items()},
        "name_ret": raw["name_ret"],
        "expo": {f: _ser(v) for f, v in raw["expo"].items()},
        "lot_sizes": raw["lot_sizes"],
        "mechanism": raw.get("mechanism"),
        "grid": raw.get("grid"),
        "n_selected_total": raw["n_selected_total"],
        "n_cannot_afford_1_lot_by_budget": raw["n_cannot_afford_1_lot_by_budget"],
    }


# ── 汇总 ──────────────────────────────────────────────────────────

def assemble(bundles: dict, args, dates, judged_years, by_year, price_map,
             pool_med, wmap, rebalance_days) -> dict:
    """把各配置的读数组装成产物。"""
    bn = load_backtest_walk_forward()
    fa = load_factor_attribution()
    mbn = load_measure_basket_nav_01()

    def arm_stats(b: dict, signal: str, arm: str, budget_key: str | None,
                  top_n: int) -> dict:
        out: dict = {"signal": signal, "arm": arm, "top_n": top_n,
                     "budget_key": budget_key,
                     "slot_budget": BUDGETS.get(budget_key) if budget_key else None,
                     "years": b["years"]}
        g = b["gross"]
        if g is None or not len(g):
            out["note"] = "该臂没有可判定的逐日序列"
            return out
        st = tier_stats(g.to_numpy(float))
        out["gross_excess"] = st
        out["gross_excess_annual_pct"] = st["arith_ann"] * 100.0
        out["sd_annual_pct"] = st["ann_vol"] * 100.0
        out["mde_annual_pct"] = st["mde_as_annual_pct"] * 100.0
        t, n_obs = bn._t(g, block=args.rebalance_every)
        out["t_blocked"] = t
        out["n_independent_obs"] = n_obs
        out["detectable"] = bool(abs(st["cagr"]) >= st["mde_as_annual_pct"])
        out["net_by_cost_model"] = {}
        for tag, ser in b["nets"].items():
            if ser is None or not len(ser):
                continue
            s2 = tier_stats(ser.to_numpy(float))
            out["net_by_cost_model"][tag] = {
                "model": ("turnover_x_rate（aplus/backtest.py::cost_drag，**无 ¥5 固定额**）"
                          if tag == "bt003" else
                          "turnover_x_rate（real_cost_for(n, K)，**无 ¥5 固定额**）"),
                "annual_pct": s2["arith_ann"] * 100.0,
                "cagr_pct": s2["cagr"] * 100.0,
                "mde_annual_pct": s2["mde_as_annual_pct"] * 100.0,
                "detectable": bool(abs(s2["cagr"]) >= s2["mde_as_annual_pct"]),
            }
        # 命中率（逐笔）：涨腿成交笔数里 actual_pct > 0 的占比
        n_tr = sum(len(v) for v in b["name_ret"].values())
        n_up = sum(sum(1 for r in v.values() if r > 0) for v in b["name_ret"].values())
        out["hit_rate_per_trade"] = (n_up / n_tr) if n_tr else None
        out["n_trades_observed"] = n_tr
        # —— 整手可买性：**A 类（物理）与 B 类（分配）分列** ——
        # 事前预注册 docs/PREREG_AFFORDABILITY_2026-10-16.md §2/§6.1
        # 🚨 **两者不是同一个量**（第 7 层），**不许**平均成一个「买不起率」。
        mech0 = b.get("mechanism") or {}
        out["lot"] = {
            "A_physical": {
                "n_unbuyable": mech0.get("A_physical_n_unbuyable"),
                "threshold": ("1 手金额 > **全部本金 ¥50,000** ⇒ "
                              "close > ¥500（主板/创业板，1 手 100 股）/ "
                              "close > ¥250（**科创板 688/689，1 手 200 股**）"),
                "meaning": ("**物理不可能**：哪怕全仓押一只也买不到 1 手。"
                            "**与仓数、资金分配完全无关**。"),
                "caveat": ("⚠️ 事前预注册 §4：**A 类在前瞻判决样本里零出现** ⇒ "
                           "**前瞻数据无法为 A 类修法背书**（**不是**「A 类无问题」）。"),
            },
            "B_allocation": {
                "by_budget": {
                    str(int(bb)): {
                        "n_cannot_afford_1_lot": (b.get(
                            "n_cannot_afford_1_lot_by_budget") or {}).get(str(int(bb))),
                        "affordable_rate": (
                            1.0 - ((b.get("n_cannot_afford_1_lot_by_budget") or {})
                                   .get(str(int(bb)), 0) / b["n_selected_total"])
                            if b.get("n_selected_total") else None),
                        # 🚨 比较的是**金额**不是键名（原来 `bb == budget_key`
                        #   拿 float 比 str ⇒ 恒为 False ⇒ 这条标记一直是装饰）
                        "is_this_arm_filter_budget": bool(
                            budget_key is not None
                            and abs(float(bb) - float(BUDGETS[budget_key])) < 1e-9),
                    } for bb in GRID_BUDGETS
                },
                "n_selected_total": b.get("n_selected_total"),
                "threshold": ("1 手金额 > **每份金额**（等权一份）"
                              + (f"；本臂的过滤预算 = ¥{int(BUDGETS[budget_key]):,}"
                                 if budget_key else "；**本臂无过滤** ⇒ 计数按各预算**分别**给出")),
                "note": ("🚨 **每一臂都按各预算分别计数** —— "
                         "「没有过滤」不等于「不用量」："
                         "用「阈值取无穷大」的回落会让 ARM_A 的「买不起率」恒为 0，"
                         "而那**不是测量结果**。"),
                "meaning": ("⚠️ **这是分配假设，不是物理约束**：超过 = "
                            "「按等权一份买不下，**得集中**」，**不是**「买不了」。"),
                "caveat": ("⚠️ 事前预注册 §4：B 类分组上「买不起」的那几条命中率**反而更高**"
                           "（4/5 = 80% vs 24/44 = 49%），|差|/SE = **1.78 < 1.96** ⇒ "
                           "**测不出，不许给方向**；点估计方向**对剔池不利**。"),
            },
            "note": ("计数单位 = **逐调仓日的篮子成分**（一只票在多个调仓日被选中就多次计）。"
                     "🚨 **A 类与 B 类不许合并成一个「买不起率」。**"),
        }
        # —— 暴露变化（与超额变化**并排**；不许只报「过滤后买得起率更高」）——
        mech = b.get("mechanism") or {}
        out["exposure_shift"] = {
            "score_price_rank_corr_entry": mech.get("score_price_rank_corr_entry"),
            "score_price_rank_corr_signal_day": mech.get("score_price_rank_corr_signal_day"),
            "basket_price_median_over_pool_median":
                mech.get("basket_price_median_over_pool_median"),
            "board_share": mech.get("board_share"),
            "basket_size_mean": mech.get("basket_size_mean"),
            "note": ("🚨 **暴露变化与超额变化必须并排读**："
                     "可买性过滤把高价票剔掉 ⇒ 组合的**价格/市值/板块暴露**必然改变；"
                     "只看「买得起率上升」就写「过滤后更好」是**不成立的结论**。"
                     "`score_price_rank_corr` 量化的是**选择效应**"
                     "（分数越高 ⇒ 越贵 ⇒ 越买不起），"
                     "池子层面的「多少比例买得起」**答不了**这一格。"),
        }
        return out

    # ── 臂组 1 + 2 ──────────────────────────────────────────────
    arms: dict = {}
    for cid, signal, top_n, arm, budget_key in [
        ("S1_A10", "LIVE_DEFAULT", 10, "ARM_A", None),
        ("S1_B10_P5", "LIVE_DEFAULT", 10, "ARM_B", "P5"),
        ("S1_C10_P5", "LIVE_DEFAULT", 10, "ARM_C", "P5"),
        ("S1_B10_P10", "LIVE_DEFAULT", 10, "ARM_B", "P10"),
        ("S1_C10_P10", "LIVE_DEFAULT", 10, "ARM_C", "P10"),
        ("S2_A10", "BACKTEST_IC", 10, "ARM_A", None),
        ("S2_C10_P5", "BACKTEST_IC", 10, "ARM_C", "P5"),
        ("S2_C10_P10", "BACKTEST_IC", 10, "ARM_C", "P10"),
        ("S1_C10_P2500", "LIVE_DEFAULT", 10, "ARM_C", "P2500"),
        ("S1_C5_P10", "LIVE_DEFAULT", 5, "ARM_C", "P10"),
    ]:
        b = bundles.get(cid)
        if b is None:
            continue
        key = f"{signal}::{arm}" + (f"@{budget_key}" if budget_key else "")
        st = arm_stats(b, signal, arm, budget_key, top_n)
        # ★ 修法归属（事前预注册 §3）：ARM_B/ARM_C = 修法 ①「剔池」——
        #   改的是 **D1 的信号定义** ⇒ **作为带 role 的新臂，不许静默替换生产路径**。
        st["role"] = {
            "ARM_A": ("现状臂（**不改任何东西**）：按合成分取 top-N。"
                      "生产路径当前跑的就是这一支。"),
            "ARM_B": ("修法 ①（剔池）**新臂**：从候选池剔掉「1 手金额 > 每份金额」的票，"
                      "再取 top-N ⇒ 改的是 **D1 的信号定义**（池子变浅）⇒ "
                      "**可能降低预测力**；🚨 **不许静默替换生产路径**。"),
            "ARM_C": ("修法 ①（剔池）**新臂** + 降深度补位 ⇒ 同 ARM_B 的归属与代价。"
                      "**不改变 D1 的承诺数量**（仍是 10 只）。"),
        }[arm]
        st["fix_id"] = {"ARM_A": None, "ARM_B": "①剔池", "ARM_C": "①剔池"}[arm]
        st["changes_layer"] = {"ARM_A": "（不改）", "ARM_B": "D1 的信号定义",
                                "ARM_C": "D1 的信号定义"}[arm]
        st["d2_impact"] = ("**臂组 2 不改善 D2 的任何一点**（事前预注册 §8）："
                           "① 改的是信号定义、③ 只改交付形态；"
                           "本组是「**D1 可执行性**」的实验，**不是 D2 的实验**。")
        arms.setdefault(signal, {}).setdefault(arm, {})[budget_key or "none"] = st
        if arm == "ARM_A" and b.get("grid"):
            arms.setdefault(signal, {}).setdefault("GRID", {})["(n_positions x per_slot_budget)"] = \
                b["grid"]

    # 跌腿（原样，**可买性过滤不改跌腿**）
    down_legs = {}
    for cid, signal in (("S1_A10", "LIVE_DEFAULT"), ("S2_A10", "BACKTEST_IC")):
        b = bundles.get(cid)
        if b is None or b["down"] is None or not len(b["down"]):
            continue
        st = tier_stats(b["down"].to_numpy(float))
        down_legs[signal] = {
            "source_config": cid, "note": "**未过滤**的跌腿逐日超额（可买性过滤不改跌腿）",
            "gross_excess_annual_pct": st["arith_ann"] * 100.0,
            "mde_annual_pct": st["mde_as_annual_pct"] * 100.0,
            "t_blocked": bn._t(b["down"], block=args.rebalance_every)[0],
        }

    # ── 账户腿 ──────────────────────────────────────────────────
    dates_all = sorted({d for b in bundles.values() for d in b["name_ret"]})
    account_legs: dict = {}

    def _leg(cid, label, n_slots, eq, slot, cash=0.0):
        b = bundles.get(cid)
        if b is None:
            return
        account_legs[label] = account_leg(
            b, dates_all, label=label, n_slots=n_slots, equity_weight=eq,
            slot_budget=slot, capital=args.capital, cash_annual_return=cash)

    # T1：现有判决口径（日再平衡、无整手）—— 从 S2_A10 的逐日序列重算
    b2 = bundles.get("S2_A10")
    if b2 is not None and b2["gross"] is not None and len(b2["gross"]):
        dfe = pd.DataFrame({
            "market_return": b2["market"],
            "net_excess_bt003": b2["nets"].get("bt003"),
        })
        if dfe["net_excess_bt003"] is not None and dfe["net_excess_bt003"].notna().all():
            nav = mbn.account_nav(dfe, "net_excess_bt003")
            st = tier_stats(nav)
            v = verdict_for(st["cagr"], st["mde_as_annual_pct"], "T1")
            account_legs["T1_control_daily_rebalanced"] = {
                "label": "T1_control_daily_rebalanced",
                "n_days": int(st["n_days"]), "capital": args.capital,
                "n_slots": 10, "equity_weight": 1.0, "slot_budget": 5_000.0,
                "cash_annual_return": 0.0,
                "model": "日再平衡 100% 权益（= 现有判决口径，无整手、无逐笔成本）",
                "tier": st, "verdict": v,
            }
    # T1h：同篮子，持有到期 + 整手 + 逐笔成本
    _leg("S2_A10", "T1h_control_block_hold", 10, 1.0, 5_000.0)
    # T2：实盘真相（5 只 × ¥5,000，50% 现金）
    _leg("S1_A5", "T2_live_truth", 5, 0.5, 5_000.0)
    _leg("S1_A5", "T2_live_truth_cash1p5", 5, 0.5, 5_000.0, cash=0.015)
    # T2b：5 万 / 5 只 满仓（¥10,000/份）
    _leg("S1_A5", "T2b_full_5x10000", 5, 1.0, 10_000.0)
    # T3：满仓 10 只 + 实盘信号 + 可买性
    _leg("S1_C10_P5", "T3_achievable", 10, 1.0, 5_000.0)

    # ── 敞口阶梯（独立一维）────────────────────────────────────
    ladder: dict = {}
    for arm, cid in (("ARM_A", "S1_A5"), ("ARM_C", "S1_C5_P5")):
        if cid not in bundles:
            continue
        lad = {}
        for rung, eq in (("E50", 0.50), ("E75", 0.75), ("E100", 1.00)):
            slot = slot_budget_for(args.capital, eq, 5)
            lad[rung] = account_leg(
                bundles[cid], dates_all, label=f"{arm}::{rung}", n_slots=5,
                equity_weight=eq, slot_budget=slot, capital=args.capital)
        ladder[arm] = lad

    # ── 账户层网格（仓数 × 敞口 × 每份金额 × 过滤）────────────
    #  🚨 **每格都写清四件事**：仓数 / 权益敞口 / 账户每份金额 / 过滤臂与其阈值。
    #  🚨 **账户每份金额 = 本金 × 敞口 ÷ 仓数**（唯一一致的公式）；
    #     **每份 × 仓数 > 本金的组合禁止**（那等于加了仓）。
    #     ⇒ 「10 只 × 100% ⇒ 每份 ¥5,000」「5 只 × 100% ⇒ 每份 ¥10,000」
    #     「5 只 × 50% ⇒ 每份 ¥5,000」「10 只 × 50% ⇒ 每份 ¥2,500」。
    #  ⚠️ **过滤阈值 = 该格的账户每份金额**（否则过滤比账户松/紧，口径不闭合）
    #     ⇒ 需要两个额外的臂：S1_C10_P2500（10 只 × 50%）与 S1_C5_P10（5 只 × 100%）。
    ACCOUNT_GRID = [
        # (格名, config, 仓数, 权益, 过滤臂, 过滤阈值预算档)
        ("N5_E50_AF",    "S1_A5",      5,  0.50, "ARM_A（无过滤）", None),
        ("N5_E50_C",     "S1_C5_P5",   5,  0.50, "ARM_C", "P5"),
        ("N10_E50_AF",   "S1_A10",    10,  0.50, "ARM_A（无过滤）", None),
        ("N10_E50_C",    "S1_C10_P2500", 10, 0.50, "ARM_C", "P2500"),
        ("N10_E100_AF",  "S1_A10",    10,  1.00, "ARM_A（无过滤）", None),
        ("N10_E100_C",   "S1_C10_P5", 10,  1.00, "ARM_C", "P5"),
        ("N5_E100_AF",   "S1_A5",      5,  1.00, "ARM_A（无过滤）", None),
        ("N5_E100_C",    "S1_C5_P10",  5,  1.00, "ARM_C", "P10"),
    ]
    account_grid: dict = {}
    for name, cid, n_slots, eq, arm_label, budget_key in ACCOUNT_GRID:
        b = bundles.get(cid)
        if b is None:
            continue
        slot = slot_budget_for(args.capital, eq, n_slots)
        leg = account_leg(b, dates_all, label=name, n_slots=n_slots,
                          equity_weight=eq, slot_budget=slot,
                          capital=args.capital)
        thr = BUDGETS[budget_key] if budget_key else None
        leg["caliber"] = {
            "n_positions": n_slots,
            "equity_weight": eq,
            "account_slot_budget": slot,
            "account_slot_formula": "本金 × 权益敞口 ÷ 仓数（**从配置派生，不手打**）",
            "filter_arm": arm_label,
            "filter_threshold_budget": thr,
            "filter_threshold_matches_account_slot": (
                thr is not None and abs(thr - slot) < 1e-9),
            "note": ("🚨 **每份 × 仓数 > 本金 的组合禁止**（那等于加了仓）："
                     "10 只 × 100% ⇒ ¥5,000/份；5 只 × 100% ⇒ ¥10,000/份；"
                     "5 只 × 50% ⇒ ¥5,000/份；10 只 × 50% ⇒ ¥2,500/份。"
                     "⚠️ 任务书里「10 只 × 50% ⇒ 每份 ¥5,000」是**不闭合**的"
                     "（10×5,000 = ¥50,000 = 100% 敞口），本产物按公式取 ¥2,500。"),
        }
        d_leg = account_leg(
            b, dates_all, label=f"{name}|ARM_D", n_slots=n_slots,
            equity_weight=eq, slot_budget=slot, capital=args.capital,
            allocation_mode="use_full_budget")
        d_leg["arm_d_note"] = (
            "**ARM_D**：同一篮子，**每只实际拿「本金 × 敞口 ÷ 买得起 1 手的只数」**。"
            "⚠️ 对 **ARM_C（已过滤）** 的篮子，N = 仓数 ⇒ **ARM_D 与 ARM_B 逐位相同**"
            "（因为所有票都买得起 1 手，闲置全部是整手粒度余数 ⇒ ARM_D 救不回来）。"
            "⇒ **ARM_D 只在 ARM_A（未过滤）那一侧有差别**。")
        leg["arm_d_sibling"] = d_leg
        account_grid[name] = leg
    # T3_no_filter = N10_E100_AF（与 T3 的**唯一**差别就是可买性过滤）
    if "N10_E100_AF" in account_grid:
        nf = dict(account_grid["N10_E100_AF"])
        nf["label"] = "T3_no_filter"
        nf["tier"] = account_grid["N10_E100_AF"]["tier"]
        nf["verdict"] = verdict_for(
            (nf["tier"] or {}).get("cagr"),
            (nf["tier"] or {}).get("mde_as_annual_pct"), "T3_no_filter")
        account_legs["T3_no_filter"] = nf

    # ── 「信号 × 可买性」2×2 在**账户层**的净效应（满仓 10 只）────
    #  ⚠️ 两侧的**唯一**差别是过滤 ⇒ 差 = 过滤的净效应。
    signal_x_affordability: dict = {}
    for sig, cid_af, cid_c in (("LIVE_DEFAULT", "S1_A10", "S1_C10_P5"),
                               ("BACKTEST_IC", "S2_A10", "S2_C10_P5")):
        pair: dict = {}
        for tag, cid in (("ARM_A_no_filter", cid_af), ("ARM_C_filtered_P5", cid_c)):
            b = bundles.get(cid)
            if b is None:
                continue
            pair[tag] = account_leg(
                b, dates_all, label=f"{sig}::{tag}", n_slots=10,
                equity_weight=1.0, slot_budget=5_000.0, capital=args.capital)
        if len(pair) == 2:
            c_af = (pair["ARM_A_no_filter"].get("tier") or {}).get("cagr")
            c_c = (pair["ARM_C_filtered_P5"].get("tier") or {}).get("cagr")
            pair["filter_net_effect_pp_per_year"] = (
                (c_c - c_af) * 100.0 if (c_af is not None and c_c is not None)
                else None)
            pair["note"] = ("两侧**唯一**差别是可买性过滤（信号 / 仓数 / 敞口 / 每份金额 / "
                            "成本模型全部相同）⇒ 差 = **过滤的账户层净效应**。"
                            "⚠️ 两档的 `measured_sign_determinable` 都为 False ⇒ "
                            "这个差**不是**判决，只作形状陈述。")
            signal_x_affordability[sig] = pair

    # ── A 类（物理不可买）在**账户层**的存在性 ───────────────────
    a_class_presence: dict = {}
    for cid, b in bundles.items():
        n_a = 0
        n_sel = 0
        lot_sum = 0.0
        for _day, buy in (b.get("baskets") or {}).items():
            for code, px in buy:
                n_sel += 1
                if not is_affordable_physical(px, code, args.capital):
                    n_a += 1
                    lot_sum += lot_cost(px, code)
        if n_sel:
            a_class_presence[cid] = {
                "n_selected": n_sel,
                "n_A_physical_selected": n_a,
                "A_share_of_selections": n_a / n_sel,
                "sum_1lot_cost_of_A_names": lot_sum,
                "mean_1lot_cost_of_A_names": (lot_sum / n_a if n_a else None),
                "note": ("计数单位 = **逐调仓日的篮子成分**（一只票在多个调仓日被选中就多次计）。"
                         "🚨 **A 类 = 物理不可能**（1 手 > 全部本金），与仓数/分配无关。"
                         "⚠️ 「剔 A 类」值不值得单独做 = 看这个计数占篮子的比例。"),
            }

    # ── K 轴的账户层网格（K × 权益 × 过滤 × 信号）────────────────
    #  ⚠️ **K 的语义**：`rebalance_every = K` 交易日换一次仓；**非调仓日沿用上期持仓**
    #   ⇒「每天出单」与「每 K 日交易」是两件事（`aplus/live_rebalance.py` 的设计）
    #   ⇒ **K 不降低推荐频率**。
    #  🚨 **三项必须一起报**：毛超额的变化、摩擦的变化、持仓换手率（实测）。
    k_grid_cells: dict = {}
    k_grid_arms: dict = {}
    for sig, tn, arm, bk, base in K_GRID_BASE:
        for k in K_VALUES:
            cid = base if k == 21 else f"{base}_K{k}"
            b = bundles.get(cid)
            if b is None:
                continue
            st = arm_stats(b, sig, arm, bk, tn)
            turn = basket_turnover(b.get("baskets") or {}, tn)
            ann_turn = (turn["mean_turnover_per_rebalance"] * (R / float(k))
                        if turn["mean_turnover_per_rebalance"] is not None else None)
            k_grid_arms[cid] = {
                "signal": sig, "K": k, "filter_arm": arm, "budget_key": bk,
                "top_n": tn,
                "gross_excess_annual_pct": st["gross_excess_annual_pct"],
                "sd_annual_pct": st["sd_annual_pct"],
                "mde_annual_pct": st["mde_annual_pct"],
                "t_blocked": st["t_blocked"],
                "detectable": st["detectable"],
                "hit_rate_per_trade": st["hit_rate_per_trade"],
                "held_basket_turnover": turn,
                "held_basket_annualized_turnover": ann_turn,
                "turnover_note": ("**实测**（不是理论）：按 `aplus/backtest.py::cost_drag` 的同一约定 "
                                  "`(n_in + n_out) / (2·max(top_n, |cur|, |prev|))`，"
                                  "逐调仓日算完取均值；年化 = 均值 × 244/K。"),
            }
            for eq in (0.50, 1.00):
                slot = slot_budget_for(args.capital, eq, tn)
                label = f"{sig}|{arm}|K{k}|E{int(eq * 100)}"
                leg = account_leg(b, dates_all, label=label, n_slots=tn,
                                  equity_weight=eq, slot_budget=slot,
                                  capital=args.capital)
                # ARM_D（把预算用满）：同一篮子，**每只金额 = 本金 × 敞口 ÷ 买得起的只数**
                d_leg = account_leg(
                    b, dates_all, label=f"{label}|ARM_D", n_slots=tn,
                    equity_weight=eq, slot_budget=slot, capital=args.capital,
                    allocation_mode="use_full_budget")
                d_leg["arm_d_note"] = (
                    "**ARM_D**：同一篮子，**每只实际拿「本金 × 敞口 ÷ 买得起 1 手的只数」**"
                    "⇒ 预算用满、集中度上升。"
                    "⚠️ **每只金额变大 ⇒ 单票风险敞口变大** ⇒ "
                    "并排看 `ann_vol`（sd）与 `mean_actual_amount_per_bought_name`。"
                    "🚨 **ARM_D 救得回的是「买不到 1 手」的那部分现金，"
                    "救不回「买不满一手」的整手粒度余数**。")
                leg["arm_d_sibling"] = d_leg
                yrs = (leg.get("tier") or {}).get("years") or 1.0
                friction = (leg.get("total_fees") or 0.0) / args.capital / yrs
                leg["k_axis"] = {
                    "K": k, "signal": sig, "filter_arm": arm,
                    "filter_threshold_budget": BUDGETS[bk] if bk else None,
                    "equity_weight": eq, "n_positions": tn,
                    "account_slot_budget": slot,
                    "gross_excess_annual_pct": st["gross_excess_annual_pct"],
                    "friction_annual_pct": friction * 100.0,
                    "held_basket_annualized_turnover": ann_turn,
                    "hit_rate_per_trade": st["hit_rate_per_trade"],
                    "net_effect_split_note": (
                        "🚨 **K 的净效应 = 毛超额的变化 + 摩擦的变化**，"
                        "**两项方向可能不同** ⇒ 分列，不许只报净值。"),
                    "K_semantics": (
                        "K = 每 K 个交易日换一次仓；**非调仓日沿用上期持仓** ⇒ "
                        "「每天出单」与「每 K 日交易」是两件事（**K 不降低推荐频率**）。"),
                    "list_vs_account_note": (
                        "🚨 **K 越大，账户与每日榜单的脱节越大**：K=63 时你持有 3 个月，"
                        "而 D1 的榜单**每天**都换 ⇒ 榜单的边际价值随 K 递减。"),
                    "K63_forward_evidence": (
                        "⚠️ **K=63 的前瞻证据需要 189 个交易日**（batch 5 的 C2）"
                        "⇒ 前瞻轨要半年；即使回测说它好，也**不能**跳过这半年。"),
                }
                k_grid_cells[label] = leg

    # ── 判决腿：**新实盘配置** ─────────────────────────────────
    #  生产配置（2026-10-20 落盘）：`aplus/alloc.py` max_positions 5→10
    #  ⇒ **权益 100%**；`aplus/evolve.py` LIVE_REBALANCE_EVERY 21→63。
    #  🚨 **配置层过滤 ≡ ARM_A**：生产 `aplus/alloc.py::_drop_unaffordable`
    #  从发布的 10 只里剔掉「1 手 > 每份」的 ⇒ **持有的就是那些买得起的**，
    #  被剔掉那些位的预算**留现金** ⇒ **持有集合相同、现金相同**
    #  ⇒ **生产的账户读数 = ARM_A 那一格**，**不是 ARM_C**。
    #  ⇒ ARM_C 是**名单层**产品（从全池挑可买的再排前 10 ⇒ 持有集合不同）
    #  ⇒ 它作**对照臂**并排给出，**不进判决腿**。
    VERDICT_CELL = "LIVE_DEFAULT|ARM_A|K63|E100"
    # 🚨 **变量名不许撞车**：下面（更靠后）还有一句旧代码
    #   `verdict_leg = account_legs.get("T2_live_truth")`
    #   ⇒ 同名会把**这一档覆盖掉**，于是产物里 `::verdict` 一边写着
    #   `leg = LIVE_DEFAULT|ARM_A|K63|E100`、一边带着 **T2 的数**
    #   ⇒ **自相矛盾的产物**（比没有更坏）。⇒ 这里用独立名字。
    verdict_leg_prod = None
    verdict_cash_sensitivity = None
    _vcell = k_grid_cells.get(VERDICT_CELL)
    if _vcell is not None:
        _b = bundles.get("S1_A10_K63")
        verdict_leg_prod = dict(_vcell)
        verdict_leg_prod["verdict"] = verdict_for(
            (_vcell.get("tier") or {}).get("cagr"),
            (_vcell.get("tier") or {}).get("mde_as_annual_pct"),
            "new_live_config")
        verdict_leg_prod["config"] = {
            "cell": VERDICT_CELL,
            "signal": "LIVE_DEFAULT（4 因子 `DEFAULT_WEIGHTS`，实盘回落路径）",
            "filter_layer": ("**配置层**（≡ 生产 `aplus/alloc.py::_drop_unaffordable`）"
                             "⇒ 与 ARM_A 同一账户：持有集合相同、被剔掉那些位的钱留现金"),
            "n_positions": 10,
            "equity_weight": 1.0,
            "per_position_amount": 5_000.0,
            "K": 63,
            "costs": "逐笔（`aplus/costs.py::FeeSchedule`）",
            "lot_rounding": "A 股 1 手 100 股；科创板 688/689 200 股",
            "cash_return": 0.0,
            "production_sources": {
                "exposure": "aplus/alloc.py::build_allocation(max_positions=10, per_stock_cap=0.10)",
                "K": "aplus/evolve.py::LIVE_REBALANCE_EVERY = 63",
                "filter": "aplus/alloc.py::_drop_unaffordable（**配置层**）",
                "signal": "aplus/factor.py::DEFAULT_WEIGHTS（weights.json 不存在 ⇒ 回落）",
            },
        }
        # 闲置现金 1.5%/年 的**敏感性**（生产按 0 计息 ⇒ 这是敏感性，不是判决）
        if _b is not None:
            _sens = account_leg(
                _b, dates_all, label="new_live_config_cash1p5", n_slots=10,
                equity_weight=1.0, slot_budget=5_000.0, capital=args.capital,
                cash_annual_return=0.015)
            verdict_cash_sensitivity = {
                "label": "闲置现金按 1.5%/年（**敏感性，不是判决**）",
                "cagr": (_sens.get("tier") or {}).get("cagr"),
                "arith_ann": (_sens.get("tier") or {}).get("arith_ann"),
                "mde_annual": (_sens.get("tier") or {}).get("mde_as_annual_pct"),
                "upper_one_sided_95": (_sens.get("verdict") or {}).get(
                    "upper_one_sided_95"),
                "gap_to_target_pp": (_sens.get("verdict") or {}).get(
                    "gap_to_target_pp"),
                "note": ("生产对闲置现金**按 0 计息**（`cash_annual_return=0`）⇒ "
                         "**这一档是敏感性，不是判决**。"),
            }

    # ── 对照：复现现有判决 ─────────────────────────────────────
    control = None
    t1 = account_legs.get("T1_control_daily_rebalanced")
    if t1 and t1.get("tier"):
        existing = json.loads(
            (ROOT / "data/reports/basket_nav_01_2026-10-16.json")
            .read_text(encoding="utf-8"))
        exp = float(existing["tiers"][1]["cagr"])
        got = float(t1["tier"]["cagr"])
        control = {"existing_basket_nav_01_cagr": exp, "recomputed_T1_cagr": got,
                   "rel_diff": ((got - exp) / exp if exp else None),
                   "passed": bool(abs(got - exp) <= 1e-9),
                   "note": "本脚本重算的 T1 必须逐位复现 `basket_nav_01_2026-10-16.json::tiers[1].cagr`"}
        # 第二个外部锚：仓内既有的「实盘半仓」产物（10-16）。
        # ⚠️ 它的 `control_arm_half_cagr` 是**推算**（角色写明「对照臂 / 推算，不是判决」），
        #    而本脚本的 T2 是**真账户模拟**（实盘信号 + 整手 + 逐笔成本 + 现金）⇒
        #    **两个不是同一个量**，只并列、不相减。
        prior_path = ROOT / "data/reports/deployed_account_nav.json"
        if prior_path.is_file():
            try:
                prior = json.loads(prior_path.read_text(encoding="utf-8"))
                g = (prior.get("gap_to_target") or {})
                control["prior_deployed_account_nav"] = {
                    "file": "data/reports/deployed_account_nav.json",
                    "role": prior.get("role"),
                    "judgement_arm_full_cagr": g.get("judgement_arm_full_cagr"),
                    "control_arm_half_cagr": g.get("control_arm_half_cagr"),
                    "control_arm_half_is": "**推算**（角色自述「对照臂 / 推算，不是判决」）",
                    "not_comparable": ("🚨 它的半仓数是**推算**，且信号口径是 IC 定权；"
                                       "本脚本的 T2 是**真账户模拟**（实盘 4 因子信号 + "
                                       "整手 + 逐笔成本 + 50% 现金）⇒ "
                                       "**两个不是同一个量**，只并列、不相减、不取差。"),
                }
            except Exception:                          # noqa: BLE001
                pass

    # ── 达标所需超额（按基准分臂）──────────────────────────────
    calib_path = ROOT / "data/reports/market_nav_caliber.json"
    gap_by_bench: dict = {}
    if calib_path.is_file():
        cal = json.loads(calib_path.read_text(encoding="utf-8"))
        yrs = (b2["gross"].index.size / R) if (b2 is not None and b2["gross"] is not None) else None
        for arm in (mbn.VERDICT_BENCH_ARM,) + mbn.SENSITIVITY_BENCH_ARMS:
            try:
                c_mkt = mbn.caliber_benchmark_fraction(cal, arm)
            except KeyError:
                continue
            req = mbn.required_excess(TARGET, c_mkt, yrs or 1.0)
            row = {"arm": arm, "role": ("判决臂" if arm == mbn.VERDICT_BENCH_ARM
                                        else "⚠️ 敏感性臂（非判决）"),
                   "c_mkt_fraction": c_mkt,
                   "required_excess_annual": req}
            for cid, nm in (("S2_A10", "BACKTEST_IC"), ("S1_A10", "LIVE_DEFAULT")):
                b = bundles.get(cid)
                if b is None or b["gross"] is None or not len(b["gross"]):
                    continue
                legs = {}
                for tag, ser in b["nets"].items():
                    if ser is None or not len(ser):
                        continue
                    s2 = tier_stats(ser.to_numpy(float))
                    legs[tag] = {"cagr": s2["cagr"], "mde": s2["mde_as_annual_pct"]}
                row[cid] = {"net_legs": {k: {"cagr": v["cagr"], "mde_annual": v["mde"]}
                                         for k, v in legs.items()}}
            gap_by_bench[arm] = row

    # ── 判决腿 ─────────────────────────────────────────────────
    verdict_leg_prev = account_legs.get("T2_live_truth")
    payload = {
        "artifact_meta": {
            "generated_by": GENERATOR,
            "prereg": PREREG,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "window": {
                "start": args.start, "end": args.end,
                "start_actual": str(dates[0]), "end_actual": str(dates[-1]),
                "n_days": len(dates),
                "n_days_in_daily_series": (int(b2["gross"].index.size)
                                           if (b2 is not None and b2["gross"] is not None) else None),
                "years": (len(dates) / R),
            },
            "R_days_per_year": R,
            "R_source": "aplus.hurdle.DAYS_PER_YEAR",
            "capital": args.capital,
            "rebalance_every": args.rebalance_every,
            "judged_years": judged_years,
            "rebalance_calendar": "**逐年**日历（与 evaluate_weights(dates=by_year[y]) 内部同口径）",
            "n_rebalance_days": len(rebalance_days),
            "data_root": str(Path(args.data_root).resolve()),
        },
        "caliber": {
            "what_this_is": "按平台**真正会执行的那个账户**的配置测出来的 D2",
            "R": f"{R}（aplus.hurdle.DAYS_PER_YEAR）—— 不是 aplus.verify.TRADING_DAYS_PER_YEAR",
            "market_leg": "抽样面板等权日收益（150 只、种子 42）—— **不是**中证全指、**不是**市值加权",
            "gross_vs_net": ("`gross_excess_*` 是**毛**超额（未扣成本）；"
                             "`net_by_cost_model.*` 是**净**超额，且**每档的模型不同**"),
            "excess_leg_cagr_is_not_an_account": (
                "🚨 **超额腿的 `cagr` 不是账户净值**：超额是**差序列**"
                "（组合 − 市场），直接对它算 CAGR 会得到一个无关的数"
                "（实测差 ~10pp）⇒ **超额腿的年化一律读 `gross_excess_annual_pct`"
                "（算术 mean × 244）**；账户净值只从 `account_legs.*.tier.cagr` 读。"),
            "cost_models": {
                "bt003": "turnover × 0.003（aplus/backtest.py::STOCK_COST_DEFAULT，**无 ¥5 固定额**）",
                "real": "turnover × real_cost_for(n, K)（**无 ¥5 固定额**）",
                "per_trade_chain": ("**逐笔**成本（aplus/costs.py::FeeSchedule，佣金万三/最低 ¥5、"
                                    "印花税 0.05% 卖出侧、过户费、价差 0.15%/边）—— "
                                    "**与上面两个不是同一个模型**"),
            },
            "lot_size": {
                "main_and_growth": "100 股/手",
                "star_market_688_689": "200 股/手",
                "evidence_level": ("🚨 **外部交易所规则**，本仓**无数据源**；"
                                   "本仓只能证明 688/689 是科创板（aplus/panel.py::price_limit）"),
            },
            "affordability_filter": {
                "applies_to": "**只作用于涨腿**；跌腿原样并排报（整手买不起 ≠ 买不进；"
                              "跌腿是「该回避/该跑」的信息产品，不占资金 ⇒ "
                              "**跌腿不套用任何 lot_cost 判定**，事前预注册 §6.3）",
                "A_vs_B": ("🚨 **A 类（物理：1 手 > 全部本金）与 B 类（分配：1 手 > 每份金额）"
                           "是两个不同的量**（事前预注册 §2）⇒ **分列，不许平均**。"
                           "A 类与仓数/分配无关；B 类只是等权一份的分配假设。"),
                "price": "该票**自己时间轴**上第一根 > D 的 bar 的**不复权 close**",
                "arms": {
                    "ARM_A": "无约束：按 score 取 top-N",
                    "ARM_B": "硬过滤：剔掉「1 手金额 > 每份预算」的票，再取 top-N（池子不足 N ⇒ 就取剩下的）",
                    "ARM_C": "硬过滤 + 降深度：同过滤，**买得起的不足 N 时用分数最高的买不起的补位**",
                },
            },
            "account_model": {
                "holding": "持有到期（下一个调仓日卖出换仓），非调仓日不动",
                "cash": "闲置现金收益 0（主口径）/ 1.5%/年（敏感性档另报）",
                "costs": "逐笔（FeeSchedule）",
                "lot_rounding": "每份预算内取整手；买不进的票钱留现金",
                "slot_budget": "capital × 权益敞口 ÷ 持仓数（**从配置派生**）",
            },
            "hit_rate": "**逐笔**口径：涨腿成交笔数里 actual_pct > 0 的占比",
            "not_tested_here": ["因子有效性", "20% 到底能不能达到", "策略改进方案"],
        },
        "signals": {
            "LIVE_DEFAULT": {
                "weights": _weights_live(2021),
                "source": "aplus/factor.py::DEFAULT_WEIGHTS（实盘回落路径：weights.json 不存在）",
                "caveat": "**从未赛马**（evolve.load_weights 的 caveat_kind='unraced'）",
            },
            "BACKTEST_IC": {
                "weights_by_year": {str(y): wmap[y]["weights"] for y in judged_years},
                "source": "aplus/composite.py::walk_forward（judged_from=2021，2019/2020 等权）",
                "role": "对照臂（= 屏幕读数的信号口径）",
            },
        },
        "arms": arms,
        "down_legs_unchanged": down_legs,
        "account_legs": account_legs,
        "exposure_ladder": ladder,
        "account_grid": account_grid,
        "account_grid_note": (
            "**仓数 × 权益敞口 × 账户每份金额 × 过滤** 的账户层网格。"
            "每份金额 = 本金 × 敞口 ÷ 仓数（**从配置派生**）。"
            "🚨 **每份 × 仓数 > 本金 的组合禁止**（那等于加了仓）。"
            "每格的判决是 `judge()` 的**程序输出**，不许手写。"),
        "idle_cash_reading": {
            "note": ("🚨 **「账户只动用了多少本金」的唯一读数是 "
                     "`verdict.arm_d_sibling` 与 `account_legs[*].lot_investable_rate`**"
                     "（整手取整后的实投 / 名义权益）。生产的闲置现金**按 0 计息** ⇒ "
                     "**闲置部分不产生任何收益**（不是「少赚 1.8pp」那种算法）。"),
            "decomposition_key": "idle_cash_decomposition",
        },
        "arm_d_summary": {
            "rule": ("**ARM_D = 把预算用满**：买得起 N 只 ⇒ 每只 = 本金 × 敞口 ÷ N。"
                     "N = **按固定每份金额买得起 1 手的只数**（不循环定义）。"
                     "⚠️ **每份 × 仓数 > 本金 的组合禁止**（那等于加了仓）。"),
            "what_it_can_recover": ("**只能**救回「买不到 1 手」的那部分现金（B 类）；"
                                    "**救不回**「买不满一手」的整手粒度余数 —— "
                                    "**你买不了 1/3 个手**。"),
            "why_it_matters": ("T3 的整手投入率 81.9% ⇒ 18.1% 的本金躺着。"
                               "🚨 **那 18.1% 里大部分是整手粒度余数，不是「买不到」** ⇒ "
                               "**「把它跟着市场腿走每年多赚 1.8pp」是错的** —— "
                               "idle_cash_decomposition 把它分列。"),
            "cost": ("**每只金额变大 ⇒ 单票风险敞口变大 + 换手上升** ⇒ "
                     "必须 ARM_B / ARM_D **并排**看 cagr / ann_vol / 实际每只金额。"),
        },
        "signal_x_affordability": signal_x_affordability,
        "a_class_account_presence": a_class_presence,
        "k_grid": {
            "axes": {"K": list(K_VALUES), "equity": [0.50, 1.00],
                     "filter": ["ARM_A（无过滤）", "ARM_C@¥5,000"],
                     "signal": ["LIVE_DEFAULT", "BACKTEST_IC"],
                     "n_positions": 10},
            "cells": k_grid_cells,
            "per_config": k_grid_arms,
            "note": (
                "**K × 权益 × 过滤 × 信号** 的账户层网格。"
                "🚨 **K = 每 K 个交易日换一次仓**，非调仓日沿用上期持仓 ⇒ "
                "**K 不降低推荐频率**（「每天出单」与「每 K 日交易」是两件事）。"
                "🚨 **仓数固定 10 只**；权益 50% ⇒ 每份 ¥2,500，100% ⇒ 每份 ¥5,000。"
                "⚠️ **过滤阈值固定 ¥5,000**（任务书指定）⇒ 权益 50% 档「过滤比账户松」"
                "⇒ 该档仍会有买不起的票（**投入率会显示它**）。"
                "🚨 **K 的净效应 = 毛超额的变化 + 摩擦的变化**（两项方向可能不同）"
                "⇒ 每格都分列这两项，另给**实测**的持仓换手率。"
                "⚠️ **K=63 的前瞻证据需要 189 个交易日** ⇒ 前瞻轨要半年。"),
            "forward_evidence_note": (
                f"K=63 的前瞻证据需要 {K63_FORWARD_EVIDENCE_TRADING_DAYS} 个交易日"
                "（batch 5 的 C2）⇒ 前瞻轨要半年；"
                "**回测说它好也不许跳过这半年**。"),
        },
        "verdict": {
            "leg": VERDICT_CELL,
            "note": ("判决腿 = **新实盘配置**：配置层过滤（≡ ARM_A）+ 满仓 10 只 "
                     "+ K=63 + 实盘 4 因子信号 + 整手 + 逐笔成本 + 闲置现金 0 计息。"),
            "previous_leg": {
                "leg": "T2_live_truth",
                "note": ("**上一版实盘配置**（K=21、半仓 5 只）—— "
                         "生产配置已于 2026-10-20 改为满仓 10 只 + K=63 ⇒ "
                         "**这一档已被取代**，只作历史对照。"),
            },
            "config": (verdict_leg_prod.get("config") if verdict_leg_prod else None),
            "idle_cash_sensitivity_cash1p5": verdict_cash_sensitivity,
            "arm_d_sibling": ((verdict_leg_prod or {}).get("arm_d_sibling")),
            **(verdict_leg_prod.get("verdict") if verdict_leg_prod else
               {"cases": ["实盘档没跑出来"], "cagr": None, "mde_annual": None,
                "upper_one_sided_95": None, "gap_to_target_pp": None,
                "measured_sign_determinable": False, "target": TARGET}),
        },
        "gap_to_target_by_benchmark": gap_by_bench,
        "control_cross_check": control,
        "prior_prereg_background": {
            "source": "docs/PREREG_AFFORDABILITY_2026-10-16.md §4（**已被污染，只能当背景，不能当判决**）",
            "A_physical": ("**A 类（≤¥500）在判决样本里零出现** ⇒ 前瞻数据**无法**为 A 类修法背书"
                           "（**不是**「A 类无问题」）。"),
            "B_allocation": ("B 类：买得起 44 条 / 24 hit（48.98%），买不起 5 条 / 4 hit（**80.00%**）"
                             "⇒ |差|/SE = **1.78 < 1.96** ⇒ **测不出，不许给方向**；"
                             "点估计方向**对剔池不利**（买不起的那几条反而更准）。"),
            "not_yet_judged": ("`688498.SH`（10-14，1 手 = ¥111,889 = 本金 223.8%）落在 10-14，"
                               "而底稿只到 10-13 ⇒ **物理买不了的那一类恰好一个都还没判决**。"),
            "boundary": ("**本文件不改善 D2 的任何一点**（事前预注册 §8）："
                         "①/③ 都不改因子或权重，② 只会让 D1 更小 ⇒ "
                         "**本产物的臂组 2 是「D1 可执行性」的实验，不是 D2 的实验**。"),
        },
        "what_this_cannot_prove": [
            "🚨 **层归属**：`ARM_A` = **配置层**（≡ 生产 `aplus/alloc.py::_drop_unaffordable`："
            "从发布的 10 只里剔掉买不起的、其预算留现金 ⇒ 持有集合与 ARM_A 相同）"
            "⇒ **判决腿取这一档**；`ARM_C` = **名单层**（从全池挑可买的再排前 10）"
            "⇒ **是另一个产品**，作对照并排给出，**不是生产读数**。",
            "🚨 **价格口径差**：生产 `aplus/alloc.py::_drop_unaffordable` 用"
            "「**信号日当天或之前最后一根 bar 的不复权 close**（出单时刻真正能看到的价）」；"
            "本产物的 ARM_C 用「**入场价**（该票自己时间轴上第一根 > D 的 bar）」"
            "⇒ **两者不是同一个口径** ⇒ 要得到生产口径的读数，"
            "**需要用信号日 close 重跑那一臂**（本轮未做，已登记）。",
            "「测不出」**不等于**「没有超额」—— 只等于在这个样本量下测不到。",
            "**臂组 2（可买性）不改善 D2 的任何一点**：它改的是 D1 的信号定义与可执行性，"
            "对年化 20% 没有直接贡献（事前预注册 §8）。",
            "逐日账户序列只有 5.7 年；账户 MDE 远大于 20% 目标 ⇒ 达标与否**测不出**。",
            "本产物不回答「因子有没有效」。",
            "「整手买不起 ≠ 买不进」：本产物的可买性口径是**钱**的口径，不是**成交**的口径；"
            "后者由 `aplus/panel.py::can_buy/can_sell` 管（回测的成交闸门已开）。",
        ],
    }
    # ── 自相矛盾守卫（写盘前的最后一道）────────────────────────
    #  🚨 **产物里 `::verdict` 一边写着 leg=A、一边带着 B 的数** ⇒
    #  读者按 leg 查、按数引用 ⇒ 两处都对不上而**没有任何东西会喊**。
    #  ⇒ 写盘前**当场核对**：`::verdict` 的 cagr 必须等于它所指那一格的 cagr。
    _vv = payload.get("verdict") or {}
    if _vv.get("cagr") is not None and _vv.get("leg"):
        _cell = k_grid_cells.get(_vv["leg"])
        if _cell is not None:
            _tc = (_cell.get("tier") or {}).get("cagr")
            if _tc is not None and abs(float(_vv["cagr"]) - float(_tc)) > 1e-12:
                raise SystemExit(
                    f"🚨 产物自相矛盾：`::verdict` 声称 leg={_vv['leg']}，"
                    f"但 cagr={_vv['cagr']} 而那一格是 {_tc} ⇒ "
                    f"**拒绝写盘**（读者会按 leg 查、按数引用）。")
    return payload


def _print_headline(out: Path) -> None:
    d = json.loads(out.read_text(encoding="utf-8"))
    print("\n=== 判决腿（当前实盘配置）===")
    v = d.get("verdict") or {}
    print(f"  cagr = {v.get('cagr')}")
    print(f"  年化 MDE = {v.get('mde_annual')}")
    print(f"  95% 单侧上界 = {v.get('upper_one_sided_95')}")
    print(f"  目标 = {v.get('target')}   缺口 = {v.get('gap_to_target_pp')} pp")
    print(f"  符号可定 = {v.get('measured_sign_determinable')}")
    for c in (v.get("cases") or []):
        print(f"    · {c}")
    print("   配置：" + str((v.get("config") or {}).get("cell")))
    print("\n=== 账户腿并排 ===")
    for k, leg in (d.get("account_legs") or {}).items():
        t = leg.get("tier") or {}
        print(f"  {k:32s} cagr={t.get('cagr')}  MDE={t.get('mde_as_annual_pct')}  "
              f"投入率={leg.get('lot_investable_rate')}")


if __name__ == "__main__":
    raise SystemExit(main())

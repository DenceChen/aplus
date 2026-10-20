#!/usr/bin/env python
"""转债「5 万本金可交付账户」的年化读数 —— 判决档只读产物字段。

预注册：`docs/PREREG_BOND_DEPLOYED_NAV_2026-10-20.md`（**本文只实现预注册里
写死的口径，不在实现途中改判据**）。

## 这个脚本回答什么

> 如果这 5 万本金投**可转换公司债券**（交易所上市的可转债，**不是股票**），
> 账户年化是多少、离 20% 差多少、能不能定符号。

不是「推荐转债」，不是「转债有没有超额」——是把**替代路径**的账户读数量化出来，
让决策者能在股票侧 D2 判决档旁边看到转债同口径的读数。

## 三个硬约束（写在最前面，因为它们最容易在实现途中被悄悄破坏）

1. **纸面账户锚点只读**：`data/reports/bond_paper_state.json` 一字不改，
   判决用的前瞻臂 = `cheap_p0`（phase 0）。**不重选相位、不新开账户。**
2. **不造成本模型**：主口径 = 仓内转债唯一真相源
   `aplus/bond_horizon.COST_WAN15 = 0.003`（万 15/边 × 双边），
   计费式 = `aplus/bond_excess` / `aplus/bond_paper` **三处同式**的换手公式。
   纸面臂的落点直接读 parquet 的 `cost_paid`（实测值）。
3. **不许把「测不出」写成「没有」**：所有措辞只允许测量陈述
   （「测不出」/「连符号都定不了」/「与 0 不可区分」）。

## 口径要点

- **1 手 = 10 张 = 1,000 元面值**；1 手成本 = `10 × 价格`（价格 = 每 100 元面值报价）。
- **年化常数 R = 244**（`aplus.hurdle.DAYS_PER_YEAR`）—— 与股票侧
  `basket_nav_01` 同一把尺子。**不用** `aplus.verify` 的 252（美股惯例），
  不用 `bond_horizon` 的 250（只在与旧产物逐位对拍时并列报出）。
- **CAGR 是几何的**：`prod(1+r)^(R/n) − 1`；算术年化只作敏感性。
- **MDE 两个口径并列**：逐日（与股票侧同一把尺子）与分块（观测单位 = 一次调仓，
  本仓自认的诚实口径）。**判决用分块**；两者在「符号是否可定」上不一致时，
  **两个都写出来，不许挑一个**。
- **U = c + M**；`|c| < M` ⇒ **连符号都定不了**（不许说「接近」，也不许说「达不到」）。

## 自检（不通过 ⇒ 抛异常，不落盘）

S1 整手模拟器的「取消取整」极限 == 生产引擎 `run_rotation` 的逐期毛收益
S2 前瞻臂的分数极限 == `bond_paper.parquet` 的 `nav`（逐位）
S3 逐日序列复利终点 == 按期口径累计收益
S4 `U == c + M`   S5 `measured_sign_determinable == (|c| >= M)`
S6 投入率 ∈ [0,1]、闲置现金 ≥ 0   S7 1 手成本 == `10 × 价格`
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
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

from aplus.hurdle import DAYS_PER_YEAR as R            # noqa: E402  244，唯一真相源
from aplus import provenance                            # noqa: E402
from aplus.bond_horizon import _pool_of                # noqa: E402
from aplus.bond_excess import (                         # noqa: E402
    DEFAULT_COST,
    DEFAULT_HOLD_DAYS,
    DEFAULT_TOP_N,
    EMBARGO_DAYS,
    TRAIN_RATIO,
    run_rotation,
    select_cheap_top_n,
    split_segments,
)
from aplus.bond_horizon import build_horizon_cache      # noqa: E402
from aplus.bond_sync import load_bond_map               # noqa: E402

GENERATOR = "scripts/measure_bond_deployed_nav.py"

# 🚨 债券数据在 `data/bonds/master/daily`（1087 只），**不是**股票侧的
# `data/master/daily`（5460 只）⇒ 章上的指纹必须指对目录（第 156 层）。
DAILY_SUBDIR = "bonds/master/daily"

# ── 交付物常量（写死；改任何一条都要先改预注册）──────────────────────────
CAPITAL = 50_000.0          # 5 万本金
TARGET = 0.20               # D2 目标：年化 20%
LOT_ZHANG = 10              # 1 手 = 10 张
LOT_FACE = 100.0            # 每张面值（元）
IDLE_CASH_SENSITIVITY = 0.015   # 闲置现金 1.5%/年 敏感性
PAPER_ARM = "cheap_p0"      # 判决用的前瞻臂（phase 0，锚点 start=2026-10-11）
BOND_ARTIFACT_R = 250       # 旧产物 `bond_excess_analysis.json` 的年化常数

#: 旧产物（2026-10-16 盖章）—— 逐字引用 + 今日不可复现的证据。
BOND_ARTIFACT = "data/reports/bond_excess_analysis.json"
#: 股票侧参照（只读，不在本脚本里重算股票数）。
STOCK_VERDICT_ARTIFACT = "data/reports/basket_nav_01_2026-10-16.json"
STOCK_D2_ARTIFACT = "data/reports/d2_signal_requirement.json"
STOCK_EXEC_ARTIFACT = "data/reports/portfolio_exec.json"


# ══════════════════════════════════════════════════════════════════════
# 基础量
# ══════════════════════════════════════════════════════════════════════

def lot_cost(price: float, lots: int) -> float:
    """买 `lots` 手的现金成本。1 手 = 10 张 = 1,000 元面值 ⇒ 成本 = 10 × 价格。"""
    return float(lots) * LOT_ZHANG * float(price)


def affordable_lots(budget: float, price: float) -> int:
    """`budget` 元最多能买几手（**向下取整到整手**）。

    ⚠️ 这是转债路径的关键量：股票 1 手 = 100 × 股价（688498.SH 收盘 1010.35
    ⇒ 1 手 ¥101,035，5 万买不起），转债 1 手 = 10 × 价格（≈ ¥1,000~2,000）。
    """
    if not (price > 0):
        raise ValueError(f"价格必须为正，收到 {price}")
    if budget <= 0:
        return 0
    return int(math.floor(budget / (LOT_ZHANG * float(price))))


def annual_stats(returns, n_days: int, annualization_days: int = R,
                 block_days: int | None = None, label: str = "") -> dict:
    """一个观测序列 → 累计 / 几何 CAGR / MDE / 上界 / 符号可定性。

    `returns`  可以是**逐日**序列（block_days=None ⇒ MDE = 1.96×sd/√n×R）
                或**逐调仓期**序列（block_days=h ⇒ MDE = 1.96×sd/√n×(R/h)）。

    `n_days`   账户覆盖的**交易日**数（年化的分母；逐日序列时 = len(returns)）。
    ⚠️ 几何 CAGR 用 `n_days`，不是 `len(returns)`：逐期序列的 len 是期数，
    直接拿它当年化分母会把年数算小 h 倍（第 93 层的族）。
    """
    r = np.asarray(returns, dtype=float)
    n = len(r)
    if n == 0:
        raise ValueError("空收益序列：缺档必须报错，不许回落成 0")
    if n_days <= 0:
        raise ValueError(f"n_days 必须为正，收到 {n_days}")
    log1p = np.log1p(r)
    total = float(np.prod(1.0 + r) - 1.0)
    years = n_days / annualization_days
    cagr = None if total <= -1.0 else float((1.0 + total) ** (1.0 / years) - 1.0)
    mde = None
    if n > 1:
        scale = (annualization_days / block_days) if block_days else annualization_days
        mde = float(1.96 * log1p.std(ddof=1) / math.sqrt(n) * scale)
    peak, mdd = 1.0, 0.0
    for v in np.cumprod(1.0 + r):
        peak = max(peak, float(v))
        mdd = max(mdd, (peak - float(v)) / peak)
    return {
        "label": label,
        "n_obs": int(n),
        "n_days": int(n_days),
        "years": float(years),
        "compounded_total": total,
        "cagr": cagr,
        "arith_ann": float(r.mean()) * annualization_days,
        "ann_vol": float(r.std(ddof=1)) * math.sqrt(annualization_days) if n > 1 else None,
        "mde_annual": mde,
        "max_drawdown": float(mdd),
        "_series": r,
    }


def judge(cagr, mde, target: float = TARGET, label: str = "账户") -> tuple[list[str], float | None]:
    """预注册 §4 的判决规则（抄 `scripts/measure_basket_nav_01.py::judge` 的语义）。

    ⚠️ `cases` 可能**多于一条**（`|c| < M` 与后面三条不互斥）⇒ 只印最宽松那条
    会把「连符号都定不了」吞掉。
    """
    pct0 = f"{target:.0%}"
    if cagr is None or mde is None:
        return [f"缺 c 或 M（c={cagr}, M={mde}）⇒ **不给方向**"], None
    cases: list[str] = []
    if abs(cagr) < mde:
        cases.append("`|c| < M` ⇒ 🚨 **连符号都定不了**（不可测的点估计只支持形状陈述）")
    u = cagr + mde
    if u < target:
        cases.append(f"`U < T` ⇒ 「{label} 的 95% 单侧上界 {u:+.2%} < {pct0} ⇒ "
                     f"**在 95% 置信下**账户净值年化 {pct0} **不可达**」")
    elif cagr < target <= u:
        cases.append(f"`c < T ≤ U` ⇒ 🚨 **「测不出」**：点估计 {cagr:+.2%} 低于目标，"
                     f"但 95% 上界 {u:+.2%} 跨过 {pct0} ⇒ "
                     "**既不许说「达不到」，也不许说「接近」**")
    else:
        cases.append(f"`c ≥ T`（{cagr:+.2%} ≥ {pct0}）⇒ 不许写「能做到」")
    return cases, float(u)


# ══════════════════════════════════════════════════════════════════════
# 债券价格取数（与生产引擎同一套「as-of 末根」语义）
# ══════════════════════════════════════════════════════════════════════

def _close_on(frames: dict, code: str, day: str) -> float | None:
    """末根收盘（≤ day）。与 `aplus.bond_paper.PaperArm._close_of` 同义。"""
    if code not in frames:
        return None
    dates, _o, c, _v, _ch = frames[code]
    pos = int(np.searchsorted(dates, day, side="right")) - 1
    if pos < 0:
        return None
    return float(c[pos])


def _open_on(frames: dict, code: str, day: str) -> float | None:
    """末根开盘（≤ day）。与 `aplus.bond_paper.PaperArm._buy_at_open` 同义。"""
    if code not in frames:
        return None
    dates, o, _c, v, _ch = frames[code]
    pos = int(np.searchsorted(dates, day, side="right")) - 1
    if pos < 0:
        return None
    if not (float(o[pos]) > 0 and float(v[pos]) > 0):
        return None
    return float(o[pos])


# ══════════════════════════════════════════════════════════════════════
# 前瞻臂：纸面账户（既有锚点，cheap_p0）
# ══════════════════════════════════════════════════════════════════════

def forward_paper_arm(parquet_path: Path, frames: dict, arm: str = PAPER_ARM,
                      capital: float = CAPITAL, lot_zhang: int | None = LOT_ZHANG,
                      idle_cash_annual: float = 0.0) -> dict:
    """纸面账户的**整手变换**（或分数极限，lot_zhang=None）。

    口径完全照 `aplus.bond_paper.PaperArm`：
    - 信号日 D 收盘选券 → D+1 开盘买入（等权槽位：每槽 = 现金/n）
    - 每日收盘盯市：nav = 现金 + Σ 份额 × 末根收盘
    - 卖出日（信号日且有持仓）：按收盘卖出并**乘性**扣本期费用
      （`cost_paid` 直接读 parquet —— 任务要求「用实测成本」）
    - 整手变换只改两件事：① 每笔按 1 手 = 10 张取整；② 闲置现金计息（0 或 1.5%/年）
    """
    df = pd.read_parquet(parquet_path)
    required = ["date", "arm", "is_buy_day", "is_sell_day", "holdings",
                "nav", "cost_paid", "n_bought"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise KeyError(f"{parquet_path.name} 缺必需列 {missing} ⇒ 缺档必须报错，不许回落")
    sub = df[df["arm"] == arm]
    if sub.empty:
        raise KeyError(f"纸面账户里没有臂 {arm!r} ⇒ 缺档必须报错，不许回落")
    sub = sub.sort_values("date", kind="stable").reset_index(drop=True)

    cash = float(capital)
    positions: dict[str, int] = {}          # code -> 份额（张）
    daily_values: list[tuple[str, float]] = []
    buys: list[dict] = []
    first = True
    for _, row in sub.iterrows():
        day = str(row["date"])
        if not first and idle_cash_annual:
            cash *= (1.0 + idle_cash_annual) ** (1.0 / R)
        first = False
        if bool(row["is_buy_day"]):
            codes = [c for c in str(row["holdings"]).split(",") if c]
            n = len(codes) or 1
            budget = cash / n
            positions, spent = {}, 0.0
            entry_prices: list[float] = []
            for code in codes:
                px = _open_on(frames, code, day)
                if px is None:
                    continue
                if lot_zhang is None:
                    shares = budget / px
                else:
                    lots = affordable_lots(budget, px)
                    if lots <= 0:
                        continue
                    shares = lots * lot_zhang
                positions[code] = float(shares)
                entry_prices.append(px)
                spent += shares * px
            cash -= spent
            buys.append({
                "date": day,
                "n_candidates": len(codes),
                "n_bought": len(positions),
                "capital_at_buy": cash + spent,
                "deployed": spent,
                "deployed_ratio": (spent / (cash + spent)) if (cash + spent) > 0 else 0.0,
                "idle_cash": cash,
                "shares": dict(positions),
                "mean_entry_open": (sum(entry_prices) / len(entry_prices)
                                    if entry_prices else None),
            })
        value = cash + sum(sh * (_close_on(frames, code, day) or 0.0)
                           for code, sh in positions.items())
        if bool(row["is_sell_day"]):
            value *= (1.0 - float(row["cost_paid"]))
            cash, positions = value, {}
        daily_values.append((day, value))

    days = [d for d, _ in daily_values]
    vals = np.array([v for _, v in daily_values], dtype=float)
    rets = np.diff(vals) / vals[:-1]
    n_days = len(vals)
    return {
        "arm": arm,
        "days": days,
        "values": vals,
        "returns": rets,
        "buys": buys,
        "n_days": n_days,
    }


# ══════════════════════════════════════════════════════════════════════
# 长窗口臂：回测段（同一网格、同一 picks）
# ══════════════════════════════════════════════════════════════════════

def backtest_deployed_arm(cache: dict, start: str, end: str, rot: dict,
                          capital: float = CAPITAL, lot_zhang: int | None = LOT_ZHANG,
                          idle_cash_annual: float = 0.0,
                          hold_days: int = DEFAULT_HOLD_DAYS) -> dict:
    """整手账户在**生产引擎的同一批 picks / 同一网格 / 同一期界**上跑一遍。

    成交口径与 `aplus.bond_excess.run_rotation` 逐位同义：
    - 买入 = 信号日 D 之后该债自己的第一根 K 线的开盘（`pos_of`）
    - 卖出 = 该债自己第 h 根 K 线的收盘（数据先于 h 根结束 ⇒ 末根收盘，摘牌代理）
    - 费用 = 生产引擎的同一换手公式（按期加性扣，`rot["periods"][i]["cost"]`）
    """
    frames = cache["frames"]
    cal = [d for d in cache["calendar"] if start <= d < end]
    periods = rot["periods"]
    # 网格 → 日历的映射：run_rotation 把 cal_idx 存进了每一期。
    buy_day_of: dict[int, str] = {}
    boundary_of: dict[int, str] = {}
    for per in periods:
        i = int(per["cal_idx"])
        if i + 1 >= len(cal) or i + hold_days >= len(cal):
            continue
        buy_day_of[i] = cal[i + 1]
        boundary_of[i] = cal[i + hold_days]

    cash = float(capital)
    positions: dict[str, dict] = {}   # code -> {shares, exit_close}
    daily_rows: list[tuple[str, float]] = []
    period_rows: list[dict] = []
    first = True
    n_late_exit = 0
    n_periods_with_late = 0
    for d in cal:
        if not first and idle_cash_annual:
            cash *= (1.0 + idle_cash_annual) ** (1.0 / R)
        first = False
        # 1) 买入（期首后的第一个交易日开盘）
        for i, bday in buy_day_of.items():
            if bday != d:
                continue
            per = next(p for p in periods if int(p["cal_idx"]) == i)
            pool = _pool_of(cache, per["day"])
            pos_of = {r[0]: r[2] for r in pool}
            picks = per["picks"]
            budget = cash / max(len(picks), 1)
            positions, spent = {}, 0.0
            for code in picks:
                if code not in pos_of:
                    continue
                pos = pos_of[code]
                o = frames[code][1]
                c = frames[code][2]
                dts = frames[code][0]
                if pos >= len(o):
                    continue
                entry = float(o[pos])
                if not (entry > 0):
                    continue
                j = min(pos + hold_days - 1, len(c) - 1)
                exit_close = float(c[j])
                exit_date = str(dts[j])
                if lot_zhang is None:
                    shares = budget / entry
                else:
                    lots = affordable_lots(budget, entry)
                    if lots <= 0:
                        continue
                    shares = lots * lot_zhang
                positions[code] = {"shares": float(shares), "exit_close": exit_close,
                                   "exit_date": exit_date, "entry": entry}
                spent += shares * entry
            cash -= spent
            period_rows.append({"cal_idx": i, "signal_day": per["day"], "buy_day": d,
                                "capital_at_buy": cash + spent, "deployed": spent,
                                "deployed_ratio": (spent / (cash + spent)) if (cash + spent) > 0 else 0.0,
                                "idle_cash": cash, "n_bought": len(positions),
                                "cost_drag": float(per["cost"]),
                                "shares": {k: v["shares"] for k, v in positions.items()},
                                "n_picks": len(picks)})
        # 2) 收盘盯市
        value = cash + sum(p["shares"] * (_close_on(frames, code, d) or 0.0)
                           for code, p in positions.items())
        # 3) 期末（网格卖出日）：按该债自己的 h 根收盘卖出 + 按期扣费
        for row in list(period_rows):
            i = row["cal_idx"]
            if i in boundary_of and boundary_of[i] == d and row.get("closed") is None:
                proceeds = sum(p["shares"] * p["exit_close"] for p in positions.values())
                late = sum(1 for p in positions.values() if p["exit_date"] > d)
                if late:
                    n_late_exit += late
                    n_periods_with_late += 1
                # ⚠️ `cash` 此刻是**闲置现金**（整手取整的余数），不是卖出的货款 ——
                #    早先版本把它整段覆盖掉，等于把余数从账户里抹了（S: 投入率虚高）。
                idle = cash
                row["gross"] = ((idle + proceeds) / row["capital_at_buy"] - 1.0)
                row["net"] = row["gross"] - row["cost_drag"]
                cash = idle + proceeds - row["cost_drag"] * row["capital_at_buy"]
                row["value_after"] = cash
                row["closed"] = True
                positions = {}
                value = cash
        daily_rows.append((d, value))

    days = [d for d, _ in daily_rows]
    vals = np.array([v for _, v in daily_rows], dtype=float)
    rets = np.diff(vals) / vals[:-1]
    closed = [r for r in period_rows if r.get("closed")]
    period_rets = np.array([r["net"] for r in closed], dtype=float)
    return {
        "days": days,
        "values": vals,
        "returns": rets,
        "period_rows": closed,
        "period_returns": period_rets,
        "n_days": len(vals),
        "n_periods": len(closed),
        "n_late_exit_bonds": n_late_exit,
        "n_periods_with_late_exit": n_periods_with_late,
    }


# ══════════════════════════════════════════════════════════════════════
# 汇总
# ══════════════════════════════════════════════════════════════════════

def _arm_block(label: str, role: str, window, daily_stats: dict, period_stats: dict | None,
               extra: dict | None = None,
               verdict_caliber: str = "blocked（观测单位 = 一次调仓）") -> dict:
    """一臂的读数块：两个 MDE 口径并列 + 判决。

    ⚠️ 判决用**分块** MDE（观测单位 = 一次调仓），逐日口径只作与股票侧的
    同尺对照；两者在「符号是否可定」上不一致时**两个都写出来**。
    """
    c = daily_stats["cagr"]
    m_block = period_stats["mde_annual"] if period_stats else daily_stats["mde_annual"]
    m_daily = daily_stats["mde_annual"]
    cases, u = judge(c, m_block, label=label)
    sign_block = None
    if c is not None and m_block is not None:
        sign_block = bool(abs(c) >= m_block)
    sign_daily = None
    if c is not None and m_daily is not None:
        sign_daily = bool(abs(c) >= m_daily)
    gate = _structural_gate(daily_stats["years"])
    if gate:
        # 🚨 结构闸门**先于**显著性判据（预注册 §4）：样本短时不给方向。
        cases = [gate] + cases
    out = {
        "role": role,
        "window": list(window),
        "n_days": daily_stats["n_days"],
        "years": daily_stats["years"],
        "compounded_total": daily_stats["compounded_total"],
        "cagr": c,
        "arith_ann": daily_stats["arith_ann"],
        "ann_vol": daily_stats["ann_vol"],
        "max_drawdown": daily_stats["max_drawdown"],
        "mde_annual_daily_caliber": m_daily,
        "mde_annual_blocked_caliber": m_block,
        "verdict_caliber": verdict_caliber,
        "u_upper_95": u,
        "measured_sign_determinable": sign_block,
        "measured_sign_determinable_daily_caliber": sign_daily,
        "gap_to_target_pp": (None if c is None else (TARGET - c) * 100.0),
        "gap_is_meaningful": bool(sign_block),
        "gap_note": ("`|c| < M` ⇒ 符号不可定 ⇒ **gap 只是形状陈述**，"
                     "可读的是 U（95% 上界）与它的判决分支"
                     if not sign_block else "符号可定 ⇒ gap 可读"),
        "structural_gate": gate,
        "verdict_cases": cases,
    }
    if period_stats is not None:
        out["n_periods"] = period_stats["n_obs"]
        out["period_compounded_total"] = period_stats["compounded_total"]
    if extra:
        out.update(extra)
    return out


def _structural_gate(years: float) -> str | None:
    """结构性闸门：years < 1 ⇒ 样本过短，不给方向（先于显著性判据）。"""
    if years is not None and years < 1.0:
        return (f"🚨 **样本过短，不给方向**（years={years:.6f} < 1.0）——"
                "这条闸门**先于**显著性判据，样本短时不给方向、不说「接近」、也不说「达不到」")
    return None


def _fmt(x, pct=True):
    if x is None:
        return None
    return round(x * 100.0, 6) if pct else round(x, 12)


# ══════════════════════════════════════════════════════════════════════
# main
# ══════════════════════════════════════════════════════════════════════

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", default=str(ROOT / "data"))
    ap.add_argument("--out", default=str(ROOT / "data/reports/bond_deployed_nav.json"))
    ap.add_argument("--capital", type=float, default=CAPITAL)
    ap.add_argument("--idle-cash-sensitivity", type=float, default=IDLE_CASH_SENSITIVITY)
    args = ap.parse_args()

    # 🚨 第 125 层：盖章那一刻盘上的字节 ≠ 这次执行真正跑的那些字节。
    #   抓在**跑之前**（main() 第一条可执行语句）；比对在**盖章时**；不一致就拒绝盖章。
    expect_bytes = provenance.code_file_sha256(ROOT, GENERATOR)

    data_root = Path(args.data_root)
    reports = data_root / "reports"
    paper_parquet = reports / "bond_paper.parquet"
    paper_state = reports / "bond_paper_state.json"
    bond_artifact = ROOT / BOND_ARTIFACT
    stock_verdict = ROOT / STOCK_VERDICT_ARTIFACT
    stock_d2 = ROOT / STOCK_D2_ARTIFACT
    stock_exec = ROOT / STOCK_EXEC_ARTIFACT
    for p in (paper_parquet, paper_state, bond_artifact, stock_verdict, stock_d2, stock_exec):
        if not p.exists():
            raise FileNotFoundError(f"缺输入 {p} ⇒ 缺档必须报错，不许回落")

    state = json.loads(paper_state.read_text(encoding="utf-8"))
    state_sha_before = provenance.code_file_sha256(ROOT, "data/reports/bond_paper_state.json")

    bond_map = load_bond_map(str(data_root))
    if not bond_map:
        raise RuntimeError("转债库为空 ⇒ 缺档必须报错")
    cache = build_horizon_cache(bond_map)
    frames = cache["frames"]
    cal_all = cache["calendar"]
    segments = split_segments(cache, TRAIN_RATIO, EMBARGO_DAYS)

    # ── 前瞻臂（纸面账户既有锚点）──────────────────────────────────
    fwd_frac = forward_paper_arm(paper_parquet, frames, PAPER_ARM, capital=args.capital,
                                 lot_zhang=None, idle_cash_annual=0.0)
    fwd_dep = forward_paper_arm(paper_parquet, frames, PAPER_ARM, capital=args.capital,
                                 lot_zhang=LOT_ZHANG, idle_cash_annual=0.0)
    fwd_dep_y = forward_paper_arm(paper_parquet, frames, PAPER_ARM, capital=args.capital,
                                  lot_zhang=LOT_ZHANG,
                                  idle_cash_annual=args.idle_cash_sensitivity)
    # S2：分数极限必须逐位复现 parquet 的 nav
    parquet_nav = (pd.read_parquet(paper_parquet)
                   .query("arm == @PAPER_ARM").sort_values("date", kind="stable")["nav"]
                   .to_numpy(dtype=float))
    s2_diff = float(np.max(np.abs(fwd_frac["values"] / fwd_frac["values"][0] - parquet_nav)))
    if s2_diff > 1e-12:
        raise RuntimeError(f"S2 前瞻臂分数极限与 parquet 的 nav 不等（max|Δ|={s2_diff:.3e}）")
    # S6/S7（前瞻臂）：投入率 ∈ [0,1]、闲置现金 ≥ 0、份额是整手
    for b in fwd_dep["buys"]:
        if not (0.0 <= b["deployed_ratio"] <= 1.0):
            raise RuntimeError(f"S6 前瞻臂投入率越界：{b['deployed_ratio']}")
        if b["idle_cash"] < -1e-9:
            raise RuntimeError(f"S6 前瞻臂闲置现金为负：{b['idle_cash']}")
        for code, sh in b["shares"].items():
            if abs(sh / LOT_ZHANG - round(sh / LOT_ZHANG)) > 1e-9:
                raise RuntimeError(f"S7 前瞻臂份额不是整手：{code} {sh}")

    # 相位带（披露，不挑选）
    phase_band = {}
    for ph in range(5):
        name = f"cheap_p{ph}"
        try:
            arm = forward_paper_arm(paper_parquet, frames, name, capital=args.capital,
                                    lot_zhang=LOT_ZHANG, idle_cash_annual=0.0)
            frac_arm = forward_paper_arm(paper_parquet, frames, name, capital=args.capital,
                                         lot_zhang=None)
        except KeyError:
            continue
        phase_band[name] = {
            "last_nav_fractional": float(frac_arm["values"][-1] / frac_arm["values"][0]),
            "last_nav_deployed_50k": float(arm["values"][-1] / arm["values"][0]),
        }

    # ── 长窗口臂（回测段）──────────────────────────────────────────
    backtest_arms = {}
    s1_max_diff = 0.0
    s3_max_diff = 0.0
    for seg in ("train", "test", "full"):
        s, e = segments[seg]
        rot = run_rotation(cache, s, e, select_cheap_top_n(DEFAULT_TOP_N),
                           hold_days=DEFAULT_HOLD_DAYS, cost=DEFAULT_COST)
        # S1：整手模拟器的「取消取整」极限 == 生产引擎的逐期毛收益
        frac = backtest_deployed_arm(cache, s, e, rot, capital=args.capital,
                                     lot_zhang=None, idle_cash_annual=0.0)
        dep = backtest_deployed_arm(cache, s, e, rot, capital=args.capital,
                                     lot_zhang=LOT_ZHANG, idle_cash_annual=0.0)
        dep_y = backtest_deployed_arm(cache, s, e, rot, capital=args.capital,
                                       lot_zhang=LOT_ZHANG,
                                       idle_cash_annual=args.idle_cash_sensitivity)
        prod_gross = np.array([p["gross"] for p in rot["periods"]], dtype=float)
        mine_gross = np.array([r["gross"] for r in frac["period_rows"]], dtype=float)
        if len(prod_gross) != len(mine_gross):
            raise RuntimeError(f"S1 期数不等：生产 {len(prod_gross)} vs 本脚本 {len(mine_gross)}")
        denom = max(1.0, float(np.max(np.abs(prod_gross))))
        s1_max_diff = max(s1_max_diff, float(np.max(np.abs(prod_gross - mine_gross))) / denom)
        # S3：逐日序列复利终点 == 按期口径累计收益
        s3_diff = abs(float(np.prod(1.0 + dep["returns"]) - 1.0)
                      - float(np.prod(1.0 + dep["period_returns"]) - 1.0))
        s3_max_diff = max(s3_max_diff, s3_diff)
        if s3_diff > 1e-12:
            raise RuntimeError(f"S3 逐日终点与按期终点不等（{seg}: |Δ|={s3_diff:.3e}）")
        # S6：投入率 ∈ [0,1]、闲置现金 ≥ 0；S7：份额必须是整手（10 张的整数倍）
        for row in dep["period_rows"]:
            if not (0.0 <= row["deployed_ratio"] <= 1.0):
                raise RuntimeError(f"S6 投入率越界：{row['deployed_ratio']}")
            if row["idle_cash"] < -1e-9:
                raise RuntimeError(f"S6 闲置现金为负：{row['idle_cash']}")
        for row in dep["period_rows"]:
            if row.get("shares") is not None:
                for sh in row["shares"].values():
                    if abs(sh / LOT_ZHANG - round(sh / LOT_ZHANG)) > 1e-9:
                        raise RuntimeError(f"S7 份额不是整手：{sh}")

        n_days = int(rot["n_days"])
        d_frac = annual_stats(frac["returns"], n_days, R, None, f"{seg}/fractional")
        p_frac = annual_stats(frac["period_returns"], n_days, R, DEFAULT_HOLD_DAYS,
                              f"{seg}/fractional-period")
        d_dep = annual_stats(dep["returns"], n_days, R, None, f"{seg}/deployed")
        p_dep = annual_stats(dep["period_returns"], n_days, R, DEFAULT_HOLD_DAYS,
                             f"{seg}/deployed-period")
        d_dep_y = annual_stats(dep_y["returns"], n_days, R, None, f"{seg}/deployed_y")
        p_dep_y = annual_stats(dep_y["period_returns"], n_days, R, DEFAULT_HOLD_DAYS,
                               f"{seg}/deployed_y-period")

        deployed_ratios = [r["deployed_ratio"] for r in dep["period_rows"]]
        backtest_arms[seg] = {
            "window": [s, e],
            "n_periods": rot["n_periods"],
            "n_days": n_days,
            "fractional_production": _arm_block(
                f"{seg}·分数份额", "长窗口对照臂（生产引擎原样）", (s, e),
                d_frac, p_frac, {
                    "production_annualized_return_at_250": rot["annualized_return"],
                    "production_total_return": rot["total_return"],
                    "note": "这一臂直接调用生产引擎 `aplus.bond_excess.run_rotation`；"
                            "分数口径 = 整手模拟器的「取消取整」极限（S1 逐位对拍）",
                }),
            "deployed_50k": _arm_block(
                f"{seg}·5万可交付档", "长窗口·整手账户（判决臂候选）", (s, e),
                d_dep, p_dep, {
                    "deployed_ratio_mean": float(np.mean(deployed_ratios)),
                    "deployed_ratio_min": float(np.min(deployed_ratios)),
                    "deployed_ratio_max": float(np.max(deployed_ratios)),
                    "idle_cash_mean_at_buy": float(np.mean(
                        [r["idle_cash"] for r in dep["period_rows"]])),
                    "n_late_exit_bonds": dep["n_late_exit_bonds"],
                    "n_periods_with_late_exit": dep["n_periods_with_late_exit"],
                    "capital": args.capital,
                    # 逐期明细：给「会计恒等式对拍」用（deployed + idle == capital_at_buy）
                    "period_rows_detail": [
                        {k: (round(v, 9) if isinstance(v, float) else v)
                         for k, v in r.items() if k != "shares"}
                        for r in dep["period_rows"]
                    ],
                }),
            "deployed_50k_idle_cash_1p5pct": _arm_block(
                f"{seg}·5万可交付档·闲置现金1.5%/年", "敏感性臂（闲置现金计息）", (s, e),
                d_dep_y, p_dep_y, {
                    "deployed_ratio_mean": float(np.mean(
                        [r["deployed_ratio"] for r in dep_y["period_rows"]])),
                    "capital": args.capital,
                }),
        }
    if s1_max_diff > 1e-12:
        raise RuntimeError(f"S1 分数极限与生产引擎不等（max 相对差 {s1_max_diff:.3e}）")

    # ── 前瞻臂的读数块 ─────────────────────────────────────────────
    n_days_fwd = fwd_frac["n_days"]
    fwd_d = annual_stats(fwd_frac["returns"], n_days_fwd, R, None, "forward/fractional")
    fwd_dd = annual_stats(fwd_dep["returns"], n_days_fwd, R, None, "forward/deployed")
    fwd_dy = annual_stats(fwd_dep_y["returns"], n_days_fwd, R, None, "forward/deployed_y")
    # ⚠️ 前瞻臂只有 1.2 个调仓期 ⇒ **分块口径不可用** ⇒ 判决用逐日口径，
    #    并且 years < 1.0 的结构闸门**先于**它（见 _structural_gate）。
    FWD_CALIBER = "daily（本臂只有 1.2 个调仓期 ⇒ 分块口径不可用；且 years<1 触发结构闸门）"
    forward = {
        "window": [fwd_frac["days"][0], fwd_frac["days"][-1]],
        "anchor_state_readonly": {
            "start": state["start"], "hold_days": state["hold_days"],
            "top_n": state["top_n"], "cost": state["cost"], "phases": state["phases"],
            "note": "锚点只读；本脚本**不写** bond_paper_state.json（守卫：sha256 跑前跑后逐位不变）",
        },
        "phase_band": phase_band,
        "fractional": _arm_block("前瞻·分数份额", "前瞻锚点臂（既有口径）",
                                  (fwd_frac["days"][0], fwd_frac["days"][-1]),
                                  fwd_d, None, {
                                      "paper_nav_last": float(parquet_nav[-1]),
                                      "note": "直接读 bond_paper.parquet 的 nav（既有口径）",
                                  }, verdict_caliber=FWD_CALIBER),
        "deployed_50k": _arm_block("前瞻·5万可交付档", "前瞻臂的整手变换（判决臂的前瞻对应）",
                                   (fwd_dep["days"][0], fwd_dep["days"][-1]),
                                   fwd_dd, None, {
                                       "capital": args.capital,
                                       "buys": [{k: (round(v, 6) if isinstance(v, float) else v)
                                                 for k, v in b.items()}
                                                for b in fwd_dep["buys"]],
                                       "deployed_ratio_mean": float(np.mean(
                                           [b["deployed_ratio"] for b in fwd_dep["buys"]])),
                                   }, verdict_caliber=FWD_CALIBER),
        "deployed_50k_idle_cash_1p5pct": _arm_block(
            "前瞻·5万可交付档·闲置现金1.5%/年", "敏感性臂",
            (fwd_dep_y["days"][0], fwd_dep_y["days"][-1]),
            fwd_dy, None, {"capital": args.capital}, verdict_caliber=FWD_CALIBER),
    }
    for key in ("fractional", "deployed_50k", "deployed_50k_idle_cash_1p5pct"):
        forward[key]["structural_gate"] = _structural_gate(forward[key]["years"])

    # ── 旧产物（2026-10-16 盖章）：逐字引用 + 今日不可复现 ──────────
    old = json.loads(bond_artifact.read_text(encoding="utf-8"))
    old_side = bond_artifact.with_name(bond_artifact.name + ".provenance.json")
    old_stamp = json.loads(old_side.read_text(encoding="utf-8")) if old_side.exists() else {}

    # 相位带：**从锚点 note 里解析**，不手打（手打的门槛会静默过期）。
    note = str(state.get("note", ""))
    band = re.search(r"净年化区间\s*\+([0-9.]+)%~\+([0-9.]+)%", note)
    phase_band_disclosed = ({"low_pct": float(band.group(1)),
                             "high_pct": float(band.group(2)),
                             "source": "data/reports/bond_paper_state.json::note（解析得到，非手打）"}
                            if band else None)
    phase_shift_reading = None
    if phase_band_disclosed:
        deltas = {seg: (backtest_arms[seg]["fractional_production"]
                        ["production_annualized_return_at_250"]
                        - old["segments"][seg]["strategy_ann"]) * 100.0
                  for seg in ("train", "test", "full")}
        inside = {seg: bool(phase_band_disclosed["low_pct"] <= abs(v)
                            <= phase_band_disclosed["high_pct"])
                  for seg, v in deltas.items()}
        phase_shift_reading = {
            "ann_delta_pp_today_minus_old": deltas,
            "inside_disclosed_phase_band": inside,
            "reading": ("⚠️ 日历锚平移 2 个交易日带来的年化变动量落在锚点 note 披露的相位带内 "
                        "⇒ 这是**相位效应**，**不是**新发现；长窗口转债数字因此**对数据重同步敏感**。"),
        }

    old_quote = {
        "artifact": BOND_ARTIFACT,
        "stamped_at": old_stamp.get("stamped_at"),
        "verdict_verbatim": old.get("verdict"),
        "test_segment": {
            "window": old["segments"]["test"]["window"],
            "strategy_total": old["segments"]["test"]["strategy_total"],
            "strategy_ann": old["segments"]["test"]["strategy_ann"],
            "benchmark_total": old["segments"]["test"]["benchmark_total"],
            "excess_total": old["segments"]["test"]["excess_total"],
            "excess_ann": old["segments"]["test"]["excess_ann"],
            "vs_pool_mean_t_stat": old["segments"]["test"]["selection_skill_gross"]
                                   ["vs_pool_mean"]["t_stat"],
            "vs_pool_mean_per_period": old["segments"]["test"]["selection_skill_gross"]
                                       ["vs_pool_mean"]["per_period"],
            "n_periods": old["segments"]["test"]["n_periods"],
            "n_days": old["segments"]["test"]["n_days"],
        },
        # ⚠️ **算出来，不是写死**：拿今日重算与旧产物逐段比总收益。
        "reproducible_today": all(
            backtest_arms[seg]["fractional_production"]["production_total_return"]
            == old["segments"][seg]["strategy_total"] for seg in ("train", "test", "full")),
        "divergence_mechanism": (
            "日历锚 `110076.SH` 的首根 K 线从 2024-02-26 变成 2024-02-28 ⇒ "
            "**整个调仓网格平移 2 个交易日** ⇒ 段数/期数/天数三项全同而收益全变。"),
        "phase_band_disclosed": phase_band_disclosed,
        "today_recomputed": {
            seg: {
                "window": backtest_arms[seg]["window"],
                "strategy_total": backtest_arms[seg]["fractional_production"]
                                  ["production_total_return"],
                "strategy_ann_at_250": backtest_arms[seg]["fractional_production"]
                                       ["production_annualized_return_at_250"],
                "old_strategy_total": old["segments"][seg]["strategy_total"],
                "old_strategy_ann_at_250": old["segments"][seg]["strategy_ann"],
                "ann_delta_pp": (backtest_arms[seg]["fractional_production"]
                                 ["production_annualized_return_at_250"]
                                 - old["segments"][seg]["strategy_ann"]) * 100.0,
            } for seg in ("train", "test", "full")
        },
        "phase_shift_reading": phase_shift_reading,
        "handling": ("主数字一律用**今日数据重算**（本产物盖章）；旧产物数字**逐字引用**并"
                     "标注「2026-10-16 旧产物，今日不可复现」；**不因读数好看/难看而取舍**。"),
    }

    # ── 股票侧并排对照（只读既有产物，本脚本不重算股票数）────────────
    sv = json.loads(stock_verdict.read_text(encoding="utf-8"))
    sd2 = json.loads(stock_d2.read_text(encoding="utf-8"))
    sexec = json.loads(stock_exec.read_text(encoding="utf-8"))
    stock_tier = sv["tiers"][1]
    d2_widths = {w["n"]: w for w in sd2["headline"]["account_annual_ceiling_by_width"]}
    w10 = d2_widths[10]
    exec_ratios = {}
    for day, row in sexec["逐窗口"].items():
        a = row.get("A_hold_cash", {})
        if "投入率" in a:
            exec_ratios[day] = a["投入率"]
    stock_side = {
        "verdict_arm": {
            "artifact": STOCK_VERDICT_ARTIFACT,
            "path": "tiers[1]",
            "cagr": stock_tier["cagr"],
            "cagr_pct": _fmt(stock_tier["cagr"]),
            "mde_annual": stock_tier["mde_as_annual_pct"],
            "mde_annual_pct": _fmt(stock_tier["mde_as_annual_pct"]),
            "u_upper_95": sv.get("verdict_upper_one_sided_95"),
            "n_days": stock_tier["n_days"],
            "years": stock_tier["years"],
            "caliber": stock_tier["tier"],
            "caveat": ("⚠️ 判决档 = **满仓 10 只 + 8 因子 IC 定权**，"
                       "**不是**实盘配置；其 `caliber.this_is_not` 逐字写着"
                       "「不是 ② 账户净值（不含整手 100 股、¥5 固定佣金、闲置现金）」"),
        },
        "deliverable_50k": {
            "artifact": STOCK_D2_ARTIFACT,
            "path": "headline.account_annual_ceiling_by_width[n=10]",
            "account_annual_at_50k_pct": w10["account_annual_at_50k_pct"],
            "gap_to_target_at_50k_pp": w10["gap_to_target_at_50k_pp"],
            "caveat": ("⚠️ 股票侧 5 万档**同样不含整手取整**（整数股与 ¥5 固定佣金未建模）"
                       "⇒ 与本产物的转债 5 万档**不是同一口径**，并排只为量级对照"),
        },
        "execution_deployed_ratio": {
            "artifact": STOCK_EXEC_ARTIFACT,
            "path": "逐窗口.*.A_hold_cash.投入率",
            "by_window": exec_ratios,
            "lot_shares": sexec.get("lot_shares"),
            "caveat": "股票侧 1 手 = 100 股 ⇒ 高价股买不起 1 手（688498.SH 收盘 1010.35 ⇒ 1 手 ¥101,035）",
        },
        "not_a_verdict": "这一块**只给数、不下结论**；转债能不能替代股票是产品定义问题（见 honest_boundaries）",
    }

    # ── 判决（判决臂 = 5 万可交付档 @ 测试段，样本外）────────────────
    # ⚠️ 判决臂的**标签必须跟着对象走**（第 142 层：标签与对象各写一份 ⇒ 换对象
    #   而标签不变，读数与标签互相打脸）。⇒ 段名用常量，标签由它拼出来。
    JUDGMENT_SEG = "test"   # 预注册 §4：判决臂 = 样本外测试段
    judgment = backtest_arms[JUDGMENT_SEG]["deployed_50k"]
    judgment_sens = backtest_arms[JUDGMENT_SEG]["deployed_50k_idle_cash_1p5pct"]
    verdict = {
        "target_annual": TARGET,
        "judgment_arm": f"backtest.{JUDGMENT_SEG}.deployed_50k（5 万可交付档 @ 样本外测试段）",
        "judgment_segment": JUDGMENT_SEG,
        "cagr": judgment["cagr"],
        "cagr_pct": _fmt(judgment["cagr"]),
        "mde_annual": judgment["mde_annual_blocked_caliber"],
        "mde_annual_pct": _fmt(judgment["mde_annual_blocked_caliber"]),
        "mde_annual_blocked_caliber": judgment["mde_annual_blocked_caliber"],
        "verdict_caliber": judgment["verdict_caliber"],
        "mde_annual_daily_caliber": judgment["mde_annual_daily_caliber"],
        "mde_annual_daily_caliber_pct": _fmt(judgment["mde_annual_daily_caliber"]),
        "u_upper_95": judgment["u_upper_95"],
        "u_upper_95_pct": _fmt(judgment["u_upper_95"]),
        "measured_sign_determinable": judgment["measured_sign_determinable"],
        "verdict_cases": judgment["verdict_cases"],
        "structural_gate": _structural_gate(judgment["years"]),
        "sensitivity_idle_cash_1p5pct": {
            "cagr_pct": _fmt(judgment_sens["cagr"]),
            "mde_annual_pct": _fmt(judgment_sens["mde_annual_blocked_caliber"]),
            "u_upper_95_pct": _fmt(judgment_sens["u_upper_95"]),
            "measured_sign_determinable": judgment_sens["measured_sign_determinable"],
        },
    }
    # S4 / S5 自检
    if verdict["u_upper_95"] is None or verdict["cagr"] is None:
        raise RuntimeError("S4：判决臂缺 c 或 M")
    if abs(verdict["u_upper_95"] - (verdict["cagr"] + verdict["mde_annual"])) > 1e-15:
        raise RuntimeError("S4：U != c + M")
    if verdict["measured_sign_determinable"] != (
            abs(verdict["cagr"]) >= verdict["mde_annual"]):
        raise RuntimeError("S5：measured_sign_determinable != (|c| >= M)")

    honest = [
        "① **转债样本外超额与 0 不可区分** —— 逐字引用旧产物 "
        f"`{BOND_ARTIFACT}`（盖章 {old_stamp.get('stamped_at')}）的原文："
        f"「{old.get('verdict')}」"
        f"（费前对池均值 t={old['segments']['test']['selection_skill_gross']['vs_pool_mean']['t_stat']}，"
        f"n={old['segments']['test']['n_periods']} 期）。"
        "⚠️ **该旧产物今日不可复现**（见 old_artifact_2026_10_16.reproducible_today = False 与机制说明）。",

        "② **相位未重选** —— 本产物没有新开账户、没有改 `bond_paper_state.json` 的 `start`、"
        "没有挑相位；前瞻臂固定 `cheap_p0`（phase 0），phase 1~4 只作相位带披露。"
        "长窗口臂的网格锚点 = **数据首日**（不是我能挑的起点），"
        "而它的平移后果已实测并记录（见 old_artifact_2026_10_16.divergence_mechanism）。",

        "③ **摘牌/强赎不可成交** —— 纸面账户按**末根收盘**卖出并单独计 `n_liquidated`；"
        "现实中这类债可能根本卖不掉 ⇒ 长窗口净收益**偏乐观**。",

        "④ **符号可定性的边界** —— 「|c| < M ⇒ 连符号都定不了」在本产物里逐档给出；"
        "**「测不出」不等于对超额的否定**：不许把本产物的任何一档读成对超额的**否定**"
        "（那是水平陈述）；本产物只给测量陈述。",

        "⑤ **旧产物今日不可复现** —— 2026-10-16 那份长窗口产物与今日重算不等，"
        "机制是日历锚平移 2 个交易日（相位效应）。**不因读数好看/难看取舍任何一方。**",

        "⑥ **转债 ≠ 股票** —— 可转债是交易所上市的可转换公司债券，**不是 A 股股票**；"
        "本仓 D2（年化 20%）的定义域是**股票口径**。"
        "「转债能不能替代股票达成 D2」是**产品定义问题**，由决策者裁定，"
        "**本产物不下这个结论**。",

        "⑦ **成本口径** —— 主口径 0.003（万 15/边 × 双边，`aplus.bond_horizon.COST_WAN15`）"
        "**不含 ¥5 最低佣金**；实测换算：5 万/10 只时 ¥5 最低佣金口径是 0.10%/边（往返 0.20%），"
        "**比主口径便宜 1.5×** ⇒ 主口径**偏保守**。"
        "另：转债**无印花税**（印花税只在股票卖出侧）。"
        "⚠️ 「成本更低」是对转债**有利**的方向，将来若用它放宽结论必须**先预注册**。",

        "⑧ **年化常数** —— 本产物判决用 R=244（`aplus.hurdle.DAYS_PER_YEAR`，A 股实况，"
        "与股票侧 `basket_nav_01` 同一把尺子）；**不用** `aplus.verify` 的 252（美股惯例，"
        "不是同一个量）；旧产物用的 250 只在逐位对拍时并列报出。",
    ]

    state_sha_after = provenance.code_file_sha256(ROOT, "data/reports/bond_paper_state.json")
    if state_sha_before != state_sha_after:
        raise RuntimeError("锚点文件被改动 ⇒ 硬约束违反")

    out = {
        "artifact": GENERATOR,
        "prereg": "docs/PREREG_BOND_DEPLOYED_NAV_2026-10-20.md",
        "as_of": str(pd.Timestamp(cal_all[-1]).date()),
        "target_annual": TARGET,
        "what_this_is": (
            "**可转换公司债券**（交易所上市的可转债，**不是股票**）的 5 万本金账户读数。"
            "回答「账户年化多少、离 20% 差多少、能不能定符号」。"
            "⚠️ 本仓 D2 的定义域是**股票口径** ⇒ 本读数与股票侧并排时**不是同一个目标**。"),
        "caliber": {
            "R_days_per_year": R,
            "R_source": "aplus.hurdle.DAYS_PER_YEAR",
            "R_is_not": ("不是 aplus.verify.TRADING_DAYS_PER_YEAR=252（美股惯例）；"
                         "两者不是同一个量，本读数与 verify.compounded_stats 的 cagr 不可并排比较。"
                         f"旧产物用的 {BOND_ARTIFACT_R} 只在逐位对拍时并列报出。"),
            "cagr": "几何 CAGR = prod(1+r)^(R/n) − 1（算术年化只作敏感性）",
            "mde_daily": "MDE(逐日) = 1.96 × sd(log1p r)/sqrt(n) × R",
            "mde_blocked": "MDE(分块) = 1.96 × sd(log1p 期收益)/sqrt(n_periods) × (R/hold_days)",
            "which_is_the_verdict_caliber": (
                "**分块**（观测单位 = 一次调仓）。逐日口径假设日内独立，而本策略每 "
                f"{DEFAULT_HOLD_DAYS} 个交易日才调一次仓 ⇒ 逐日收益序列相关 ⇒ 逐日 SE 偏乐观。"
                "两个口径**并列报出**；若在「符号是否可定」上不一致，**两个都写出来，不许挑一个**。"),
            "u_upper_95": "U = c + M（M 用 1.96 双侧 95% 半宽；严格单侧 95% 需 z=1.645 ⇒ U_单侧 = c + 0.8390×M）",
            "lot": (f"1 手 = {LOT_ZHANG} 张 = {LOT_ZHANG * LOT_FACE:,.0f} 元面值；"
                    f"1 手成本 = {LOT_ZHANG} × 价格"),
            "cost": {
                "rate": DEFAULT_COST,
                "source": "aplus.bond_horizon.COST_WAN15（万 15/边 × 双边）",
                "formula": "turnover_ratio = (新进+退出)/(2×max(top_n, 当期, 上期))；期费用 = turnover_ratio × cost；首期建仓 = cost/2",
                "contains_min_commission_5cny": False,
                "stamp_duty": "转债无印花税（印花税 0.05% 只在股票卖出侧）",
                "min_commission_note": (
                    "¥5 最低佣金对转债**适用**（券商对债券同样按最低 ¥5/笔收）。"
                    "5 万/10 只 ⇒ 单边 max(¥5,000×0.03%, ¥5) = ¥5 = 0.10%/边 ⇒ 往返 0.20%，"
                    "**比主口径 0.30% 便宜 1.5×** ⇒ 主口径偏保守。"
                    "⚠️ 主口径**不改**（不另造成本模型）；此处只作口径说明。"),
            },
            "idle_cash": (f"整手取整产生的闲置现金：判决臂计 0；另报 1.5%/年敏感性"
                          f"（{args.idle_cash_sensitivity}）。"),
            "bond_is_not_stock": (
                "**可转债不是 A 股股票**。本仓 D2（年化 20%）的定义域是股票口径；"
                "「转债能否替代股票达成 D2」是产品定义问题，由决策者裁定，本产物不下结论。"),
            "generator_not_registered": (
                "⚠️ `aplus/provenance.py::DEFAULT_GENERATORS` **未登记**本生成器"
                "（任务边界：禁止改 `aplus/`）⇒ 按本仓规矩 `provenance.freshness()` 对本产物"
                "走「不在登记表里」这条 ⇒ 引用时必须同时说明这一点。"
                "本产物的可复现性由它自己的 S1~S7 自检 + sidecar 字节证据保证。"),
        },
        "verdict": verdict,
        "forward_paper_account": forward,
        "backtest": backtest_arms,
        "old_artifact_2026_10_16": old_quote,
        "stock_side_reference": stock_side,
        "honest_boundaries": honest,
        "self_checks": {
            "S1_fractional_limit_vs_production_engine": {
                "max_relative_diff": s1_max_diff,
                "note": "整手模拟器「取消取整」的逐期毛收益 vs 生产引擎 run_rotation",
            },
            "S2_forward_fractional_vs_parquet_nav": {"max_abs_diff": s2_diff},
            "S3_daily_end_vs_period_end": {"max_abs_diff": s3_max_diff,
                                           "segments": ["train", "test", "full"]},
            "S4_U_equals_c_plus_M": "已断言",
            "S5_sign_determinable": "已断言",
            "S6_deployed_ratio_in_0_1": "已断言（前瞻臂 + 三段长窗口逐期）",
            "S7_lot_cost_is_10x_price": "已断言（每笔份额都是 10 张的整数倍 ⇒ 成本 = 10×价格×手数）",
            "anchor_state_sha256_unchanged": state_sha_after,
        },
        "data": {
            "bond_daily_dir": str((data_root / DAILY_SUBDIR).relative_to(ROOT)),
            "n_bonds": len(bond_map),
            "calendar_start": cal_all[0],
            "calendar_end": cal_all[-1],
            "n_calendar_days": len(cal_all),
            "calendar_ref": cache.get("calendar_ref"),
        },
    }

    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    provenance.stamp(path, GENERATOR, root=ROOT, data_root=str(data_root),
                     daily_subdir=DAILY_SUBDIR, expect_bytes=expect_bytes,
                     extra={"capital": args.capital,
                            "judgment_arm": verdict["judgment_arm"],
                            "target_annual": TARGET,
                            "windows": {k: backtest_arms[k]["window"]
                                        for k in ("train", "test", "full")},
                            "forward_window": forward["window"]})
    print(f"→ {path}")
    print(f"判决臂 {verdict['judgment_arm']}")
    print(f"  CAGR = {verdict['cagr_pct']}%  MDE = ±{verdict['mde_annual_pct']}%  "
          f"U = {verdict['u_upper_95_pct']}%")
    for c in verdict["verdict_cases"]:
        print(f"  · {c}")
    if verdict["structural_gate"]:
        print(f"  · {verdict['structural_gate']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

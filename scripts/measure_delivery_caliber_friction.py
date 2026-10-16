#!/usr/bin/env python
"""**交付形态**（每天 10 涨 10 跌、满仓、5 万本金）的年摩擦与 D2 封口判决。

判据在 `docs/PREREG_DELIVERY_CALIBER_FRICTION_2026-10-17.md`。
🚨 **那份文件的 §3 是补记不是预注册**（作者看过读数），§1/§2 的口径是定义层。

为什么必须有这个产物：仓里全部 D2 读数都来自 **K=21 回测**，
而 D1 的**真实交付形态**是**次日买入、次日卖出**（实测 `exit_date` 比
`entry_date` 晚 1 天）。这两个是**不同的量**
（[[k-is-not-a-window-rate]] / [[compare-like-calibers-or-not-at-all]]）。

★ 本脚本的判决**不需要 MDE**（预注册 §3 D1）：
摩擦是**已公布费率**的确定性算术，换手是从底稿数出来的计数，
两者都不含随机成分 ⇒ 没有「样本量不足」这一格。
⚠️ **确定性 ≠ 可靠**：它的失效模式是费率表错或换手测错，
那两件事由守卫钉，不靠样本量兜底。

⚠️ 本脚本**只调用** `aplus.costs`，**禁止重写成本公式**
（照 `measure_basket_nav_01.py` 的做法）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd

from aplus import provenance
from aplus.costs import FeeSchedule, annual_friction, gross_needed_for_net
from aplus.hurdle import DAYS_PER_YEAR

# ---- D1 的交付契约（用户原话：每天推荐涨的跌的各 10 个）----
CAPITAL = 50_000.0
N_POSITIONS = 10
EQUITY_WEIGHT = 1.0
NET_TARGET = 0.20

GENERATOR = "scripts/measure_delivery_caliber_friction.py"

# ---- D2 的三档判决，**顺序即判据**（按严厉程度降序）----
VERDICT_SEALED = "SINGLE_SIDED_UNREACHABLE"
VERDICT_OVER = "OVER_TARGET_BUT_NOT_SEALED"
VERDICT_NONE = "NOT_SEALED"
VERDICT_TIERS = (VERDICT_SEALED, VERDICT_OVER, VERDICT_NONE)


def delivered_turnover(rows: pd.DataFrame) -> dict:
    """实测**涨腿**的逐次换手。观测单位是 `entry_date`，不是 `pred_date`（预注册 §2.2）。

    🚨 这一层是最容易搞错的地方：`verification.parquet` 6 个 `pred_date`
    只对应 4 个 `entry_date`（10-09/10-10/10-11 全部 entry 到 10-12，
    持有同一批 bar）⇒ **按 `pred_date` 相邻对算会把不存在的成交算进去。**

    每个 `entry_date` 取该日**最新的 `pred_date`** 那一份名单 = 真正被撮合的。
    首个 `entry_date` 不参与（无前仓可换）。
    """
    leg = rows[rows["direction"] == "up"]
    if leg.empty:
        raise KeyError("verification.parquet 里没有 direction=='up' 的行")

    latest = leg.groupby("entry_date")["pred_date"].max()
    names: dict = {}
    for entry, pred in latest.items():
        sel = leg[(leg["entry_date"] == entry) & (leg["pred_date"] == pred)]
        names[entry] = set(sel["code"])

    entries = sorted(names)
    if len(entries) < 2:
        raise KeyError(
            f"换手需要 ≥2 个 entry_date，实测只有 {len(entries)} 个 ⇒ 拒绝出换手读数"
        )

    per_rebalance, prev = [], None
    for entry in entries:
        cur = names[entry]
        if prev is None:
            prev = cur
            continue
        bought = len(cur - prev)
        per_rebalance.append(
            {
                "entry_date": str(entry),
                "held": len(cur),
                "kept": len(cur & prev),
                "bought": bought,
                "one_side_turnover": bought / len(cur),
            }
        )
        prev = cur

    taus = [r["one_side_turnover"] for r in per_rebalance]
    return {
        "observation_unit": "entry_date",
        "why_not_pred_date": (
            "6 个 pred_date 只对应 4 个 entry_date；按 pred_date 相邻对算"
            "会把没有产生成交的配对算进去"
        ),
        "n_pred_dates": int(rows["pred_date"].nunique()),
        "n_entry_dates": int(rows["entry_date"].nunique()),
        "n_rebalances": len(per_rebalance),
        "basket_size_per_rebalance": int(per_rebalance[0]["held"]),
        "per_rebalance": per_rebalance,
        "turnover_point_estimate": sum(taus) / len(taus),
        "turnover_min_measured": min(taus),
        "turnover_max_measured": max(taus),
    }


def friction_at(capital: float, equity_weight: float, n_positions: int,
                turnover: float, fee: FeeSchedule = FeeSchedule()) -> dict:
    """给定**单边**换手下的年摩擦与所需毛超额。

    🚨 合成口径（预注册 §2.3）：`round_trip` 已含买+卖双边，
    而 `turnover` 是**单边**的 ⇒ **只乘一次**，即 `rebalances_per_year = DAYS_PER_YEAR × τ`。
    乘 2 会重复计一次双边。
    """
    rpy = DAYS_PER_YEAR * turnover
    return {
        "one_side_turnover": turnover,
        "rebalances_per_year": rpy,
        "annual_friction_pct_of_capital": annual_friction(
            capital, equity_weight, n_positions, rpy, fee
        ),
        "gross_excess_needed_pct_of_equity": gross_needed_for_net(
            capital, equity_weight, n_positions, rpy, NET_TARGET, fee
        ),
    }


def judge(friction_min: float, net_target: float = NET_TARGET) -> tuple[str, str]:
    """D2 三档判决。**顺序即判据**（预注册 §3 D2）。

    🚨 入参**必须**是区间下端（实测最省的那一档），不是点估计（D3）。
    这是最弱的要求，因而最稳。
    """
    if friction_min >= net_target:
        return VERDICT_SEALED, (
            f"即便取实测最省的一档，年摩擦 {friction_min * 100:.2f}% "
            f"仍 ≥ 目标 {net_target * 100:.0f}% ⇒ 交付形态单边不可达"
        )
    if friction_min > 0:
        return VERDICT_OVER, (
            f"年摩擦 {friction_min * 100:.2f}% < 目标 ⇒ 未封口，"
            f"但摩擦仍吃掉该口径的一部分收益"
        )
    return VERDICT_NONE, "摩擦 ≤ 0，与正成本结构矛盾，需人工核对费率表"


def measure(rows: pd.DataFrame, fee: FeeSchedule = FeeSchedule()) -> dict:
    """把一次实测换手换算成交付形态的摩擦判决。"""
    turn = delivered_turnover(rows)

    full = friction_at(CAPITAL, EQUITY_WEIGHT, N_POSITIONS, 1.0, fee)
    at_min = friction_at(CAPITAL, EQUITY_WEIGHT, N_POSITIONS,
                         turn["turnover_min_measured"], fee)
    at_max = friction_at(CAPITAL, EQUITY_WEIGHT, N_POSITIONS,
                         turn["turnover_max_measured"], fee)
    at_point = friction_at(CAPITAL, EQUITY_WEIGHT, N_POSITIONS,
                           turn["turnover_point_estimate"], fee)

    # 反解：要把摩擦压到目标以下，τ 需要多低（预注册 §3 D3 要求并排给出）
    tau_needed = NET_TARGET / full["annual_friction_pct_of_capital"]

    verdict, verdict_text = judge(at_min["annual_friction_pct_of_capital"])

    return {
        "config": {
            "capital": CAPITAL,
            "n_positions": N_POSITIONS,
            "equity_weight": EQUITY_WEIGHT,
            "net_target": NET_TARGET,
            "days_per_year": DAYS_PER_YEAR,
            "days_per_year_source": "aplus.hurdle.DAYS_PER_YEAR",
        },
        "fee_schedule": {
            "commission_rate": fee.commission_rate,
            "commission_min": fee.commission_min,
            "stamp_duty_sell": fee.stamp_duty_sell,
            "transfer_fee": fee.transfer_fee,
            "spread_one_way": fee.spread_one_way,
            "commission_min_binds_below": fee.commission_min / fee.commission_rate,
        },
        "mechanism": {
            "notional_per_position": CAPITAL * EQUITY_WEIGHT / N_POSITIONS,
            "effective_commission_rate": max(
                (CAPITAL * EQUITY_WEIGHT / N_POSITIONS) * fee.commission_rate,
                fee.commission_min,
            ) / (CAPITAL * EQUITY_WEIGHT / N_POSITIONS),
            "note": (
                "单笔名义额 < 最低佣金阈值 ⇒ 每笔吃满 ¥5 固定额，"
                "实际费率是名义费率的数倍；这是「5 万 + 10 只 + 日频」的联合后果"
            ),
        },
        "turnover": turn,
        "friction_by_turnover": {
            "at_measured_min": {**at_min, "role": "判决臂（区间下端）"},
            "at_point_estimate": {**at_point, "role": "点估计·不进判决"},
            "at_measured_max": {**at_max, "role": "对照臂（区间上端）"},
            "at_full_turnover": {**full, "role": "对照臂（假设 10 只全换）"},
        },
        "required_turnover_for_target": tau_needed,
        "verdict": verdict,
        "verdict_text": verdict_text,
        "verdict_rule": (
            "预注册 §3 D2 三档，顺序即判据；入参取实测换手区间的**下端**（D3）"
        ),
        "caliber": {
            "no_mde_needed": (
                "🚨 本判决**不需要 MDE**：摩擦是已公布费率的确定性算术，"
                "换手是底稿计数，两者都不含随机成分 ⇒ 没有「样本量不足」这一格"
            ),
            "deterministic_is_not_reliable": (
                "⚠️ 确定性 ≠ 可靠：失效模式是费率表错或换手测错，"
                "那两件事由守卫钉，不靠样本量兜底"
            ),
            "recommend_daily_is_not_trade_daily": (
                "「每天推荐」≠「每天交易」：推荐可每日，持仓周期必须拉长。"
                "K=21 下同一只票的年摩擦是日频口径的 1/21"
            ),
            "turnover_is_a_point_estimate": (
                f"换手点估计基于 {turn['n_rebalances']} 次调仓，"
                "按 §3 D3 不进判决；判决只用实测区间"
            ),
            "two_denominators": (
                "annual_friction_pct_of_capital 与 gross_excess_needed_pct_of_equity "
                "🚨 **分母不同**，不得相除或并列成同一个「%」"
            ),
        },
        "what_this_cannot_prove": [
            "本产物**不回答**「策略毛超额能不能到 154%」——那是另一个量，"
            "且仓里实测多头毛超额天花板约 11%/年",
            "本产物**不回答**「K=21 能不能到 20%」——K=21 是另一个口径，"
            "其摩擦单列（见 friction_by_holding_period）",
            "换手只有 "
            f"{turn['n_rebalances']} 次调仓的样本 ⇒ 点估计不可引用，"
            "只支持「区间下端已超过目标」这一个**形状**陈述",
        ],
    }


def friction_by_holding_period(fee: FeeSchedule = FeeSchedule()) -> dict:
    """同一份费率表下，不同持仓周期的摩擦（**对照臂，非判决**）。"""
    out = {}
    for k in (1, 5, 10, 21, 42, 63):
        rpy = DAYS_PER_YEAR / k
        out[f"K={k}"] = {
            "rebalances_per_year": rpy,
            "annual_friction_pct_of_capital": annual_friction(
                CAPITAL, EQUITY_WEIGHT, N_POSITIONS, rpy, fee
            ),
            "gross_excess_needed_pct_of_equity": gross_needed_for_net(
                CAPITAL, EQUITY_WEIGHT, N_POSITIONS, rpy, NET_TARGET, fee
            ),
            "role": "对照臂·非判决",
        }
    return out


def _parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--verification",
                    default=str(ROOT / "data/reports/verification.parquet"))
    ap.add_argument("--out",
                    default=str(ROOT / "data/reports/delivery_caliber_friction.json"))
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    # 第 125 层：生成器字节在跑的过程中必须冻结，不一致就**拒绝盖章**
    expect_bytes = provenance.code_file_sha256(GENERATOR)

    rows = pd.read_parquet(args.verification,
                           columns=["pred_date", "entry_date", "code", "direction"])
    out = measure(rows)
    out["friction_by_holding_period"] = friction_by_holding_period()

    Path(args.out).write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # 🚨 第 156/166 层：`expect_bytes` 守的是**代码**字节，结构上拦不住
    # 「代码没变、输入变了」。`verification.parquet` 是**追加式底稿**
    # （6 个 pred_date / 120 行，会随每日补判增长）⇒ 必须盖它**字节**的 sha256，
    # 而不只是路径。路径回答「从哪来」，指纹才回答「是哪些字节」。
    provenance.stamp(args.out, GENERATOR, expect_bytes=expect_bytes, extra={
        "verification_parquet": args.verification,
        "source_parquet_sha256": hashlib.sha256(
            Path(args.verification).read_bytes()).hexdigest(),
    })

    print(f"verdict = {out['verdict']}")
    print(out["verdict_text"])
    t = out["turnover"]
    print(f"换手：点估计 {t['turnover_point_estimate']:.4f}（{t['n_rebalances']} 次调仓，"
          f"不进判决） 区间 [{t['turnover_min_measured']:.4f}, {t['turnover_max_measured']:.4f}]")
    for tag, row in out["friction_by_turnover"].items():
        print(f"  {tag:22s} τ={row['one_side_turnover']:.4f}  "
              f"摩擦={row['annual_friction_pct_of_capital']*100:7.2f}%  "
              f"需毛={row['gross_excess_needed_pct_of_equity']*100:7.2f}%")
    print(f"要让摩擦 ≤ {NET_TARGET*100:.0f}%：τ ≤ {out['required_turnover_for_target']:.4f}")
    print(f"写出 → {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
r"""摩擦对 D2（年化 20%）的敏感性 —— 摩擦本身会不会先吃掉全部目标。

## 为什么要单独算

`aplus/costs.py` 是**按本金建模**的成本模块（最低佣金 ¥5 + 印花税 + 价差），
生产用 `SPREAD_ONE_WAY = 0.0015`（单边 ⇒ 往返 **30bp**）。

外部调研（2026-10-16，见 `external_sources`）给出的**滑点**区间是
往返 **40 ~ 100bp**（中小盘），涨停板附近可达 **204bp**。

🚨 **「价差」和「滑点」是不是同一个量，未经验证。**
  - `SPREAD_ONE_WAY` = 买卖价差（bid-ask），是**报价本身的宽度**；
  - 「滑点」 = 冲击 + 延迟，是**你下单时多付的那部分**。
两者在真实成交里**部分重叠**（吃掉价差就吃掉一部分滑点），
但**重叠多少没有来源** ⇒ **不可相加**（会双计），也不可直接替换（会漏计）。

⇒ 本文件因此**只做敏感性扫描**，不产出「真实摩擦」这一个数。
它回答的是：**在多大的价差假设下，摩擦本身就会把 20% 目标吃光。**

## 口径纪律

1. 🚨 **不改 `aplus/costs.py` 的任何常量**。生产仍是 `SPREAD_ONE_WAY=0.0015`。
   本文件通过 `FeeSchedule(...)` 传参做敏感性（该 dataclass 本就是为此参数化的，
   见 `costs.py:69-70` 的 docstring）。
2. 🚨 **年调仓次数用精确 `R/LIVE_REBALANCE_EVERY`**，不 `round()`。
   `round(244/21)=12` 会让摩擦虚高 3.28% —— 这是本仓踩过的坑
   （记忆 `a-plus-friction-has-positive-floor` 里的「rpy 用精确 244/21」）。
3. 🚨 **对照臂必须标 role 与来源等级**，不许把外部数字当判决臂。

## 它不做什么

- 不给「真实摩擦是多少」——那需要实测滑点，本文件没有。
- 不改 D2 的判决档、不改 `TARGET`、不改任何预注册阈值。

用法
----
    .venv/bin/python scripts/friction_sensitivity.py
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data/reports/friction_sensitivity.json"

#: 自己的路径，写进溯源章 —— `code_file_sha256` 算的就是这份文件的字节。
GENERATOR = "scripts/friction_sensitivity.py"

#: 🚨 第 166 层的**极端形态**：本脚本**零数据输入**。
#: 它不读任何 parquet、不读任何已落盘产物，全部输入是**仓内代码常量**：
#: `FeeSchedule().spread_one_way`（生产价差）、`COMMISSION_MIN` / `COMMISSION_MIN_BINDS_BELOW`
#: （A 股 ¥5/笔固定佣金）、`LIVE_REBALANCE_EVERY`（调仓频率）、`R`（年交易日）。
#: ⇒ `stamp()` 默认那个 `master/daily`（5460 个文件）的 `data_fp` 对本脚本是
#:   **一枚指向它压根没读过的目录的假章**——而假章比无章更坏：
#:   它主动发出一个「数据是新鲜的」信号，而这里连数据都没有。
#: ⇒ 「零数据」不等于「零输入」：常量一改，「摩擦吃光 20% 的临界价差」立刻变，
#:   而产物里一个字都不会红 ⇒ 真实依赖登记成**常量来源文件的字节指纹**。
DATA_FP_NOT_APPLICABLE = (
    "data_fp 不适用：friction_sensitivity.py 零数据输入（不读任何 parquet、不读任何已落盘产物）。"
    "它的全部输入是仓内代码常量，见 constant_sources_sha256。"
    "换言之它没有数据可陈旧，但有字节可漂移——后者已逐个登记。"
)

#: 本脚本真正读到的常量，各自在哪个文件里定义。
#: 🚨 这些是**代码里的真名**，不是从文件名反推（第 123 层）：
#: `COMMISSION_MIN` / `COMMISSION_MIN_BINDS_BELOW` / `FeeSchedule` 都在 `aplus/costs.py`，
#: `LIVE_REBALANCE_EVERY` 在 `aplus/evolve.py`，`R` 在 `scripts/measure_basket_nav_01.py`。
CONSTANT_SOURCES = {
    "aplus/costs.py": "FeeSchedule.spread_one_way / COMMISSION_MIN / COMMISSION_MIN_BINDS_BELOW",
    "aplus/evolve.py": "LIVE_REBALANCE_EVERY",
    "scripts/measure_basket_nav_01.py": "R（年交易日）",
}

#: 扫描网格，单位 bp（往返）。生产值 30 与外部下沿 40、上沿 100 必须落在网格上，
#: 否则「生产 vs 外部」那两行是插值出来的、不是实测口径算出来的。
GRID_BP = (0.0, 15.0, 30.0, 40.0, 60.0, 100.0, 147.0, 204.0)


def friction_for(spread_round_trip_bp: float, cap: float, n_pos: int,
                 weight: float, reb: float) -> float:
    """年化摩擦/本金。价差按**往返 bp** 传入，内部折成单边。"""
    import sys
    sys.path.insert(0, str(ROOT))
    from aplus.costs import FeeSchedule, annual_friction
    fee = FeeSchedule(spread_one_way=spread_round_trip_bp / 2.0 / 10_000.0)
    return annual_friction(cap, weight, n_pos, reb, fee)


def breakeven_spread_bp(target: float, cap: float, n_pos: int,
                        weight: float, reb: float) -> float | None:
    """摩擦本身 == `target` 时的往返价差 bp。摩擦对价差**单调递增** ⇒ 可二分。

    返回 None 表示：即使价差为 0，摩擦也达不到 target（此时没有临界值）。
    """
    lo, hi = 0.0, 10_000.0
    if friction_for(hi, cap, n_pos, weight, reb) < target:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if friction_for(mid, cap, n_pos, weight, reb) < target:
            lo = mid
        else:
            hi = mid
    return round((lo + hi) / 2.0, 3)


def main() -> None:
    import sys
    sys.path.insert(0, str(ROOT))
    from aplus import provenance

    # 🚨 第 125 层：盖章那一刻盘上的字节 ≠ 这次执行真正跑的那些字节。
    #   抓在**读任何常量之前**。
    expect_bytes = provenance.code_file_sha256(ROOT, GENERATOR)

    from aplus.costs import COMMISSION_MIN, COMMISSION_MIN_BINDS_BELOW, FeeSchedule
    from aplus.evolve import LIVE_REBALANCE_EVERY
    from scripts.measure_basket_nav_01 import R

    reb = R / LIVE_REBALANCE_EVERY          # 🚨 精确，不 round
    cap, n_pos, weight = 50_000.0, 10, 1.0  # D1 实际：10 只多头全仓
    target = 0.20
    prod_bp = FeeSchedule().spread_one_way * 2 * 10_000.0   # 往返 bp，来自生产常量

    rows = []
    for bp in GRID_BP:
        f = friction_for(bp, cap, n_pos, weight, reb)
        rows.append({
            "round_trip_spread_bp": bp,
            "annual_friction_on_capital": round(f, 6),
            "annual_friction_on_equity": round(f / weight, 6),
            "gross_needed_for_net_20pct": round(target + f / weight, 6),
            "is_production": bp == prod_bp,
            "role": (
                "⚠️ 外部对照臂（2026-10-16 调研给出的滑点区间，非实测）" if bp in (40.0, 100.0, 204.0)
                else "敏感性格线点" if bp not in (0.0, prod_bp)
                else "生产口径" if bp == prod_bp
                else "无价差下界（纯费率+最低佣金）"
            ),
        })

    f_prod = friction_for(prod_bp, cap, n_pos, weight, reb)
    beq = breakeven_spread_bp(target, cap, n_pos, weight, reb)

    out = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "question": "摩擦本身会不会先吃掉 20% 净收益目标",
        "config": {
            "capital": cap,
            "n_positions": n_pos,
            "equity_weight": weight,
            "position_role": (
                "🚨 **D1 的「跌的 10 只」只能是回避清单，不是空头仓位。**"
                "两融开户门槛是 20 个交易日日均资产 50 万元，5 万账户**开不了信用账户**"
                "⇒ 从制度上就无从融券。`aplus/verify.py` 的实现与此一致"
                "（「跌腿不扣成本：它是我们没做过的仓位」）。"),
            "R_days_per_year": R,
            "LIVE_REBALANCE_EVERY": int(LIVE_REBALANCE_EVERY),
            "rebalances_per_year_exact": round(reb, 6),
            "rebalances_per_year_NOT_rounded_note": (
                f"round() 会得到 {round(reb)}，使摩擦虚高 "
                f"{(round(reb) / reb - 1) * 100:.2f}%。本文件全程用精确值。"),
            "commission_min_yuan": COMMISSION_MIN,
            "commission_min_binds_below_yuan": COMMISSION_MIN_BINDS_BELOW,
            "why_min_commission_matters": (
                f"5 万 ÷ 10 只 = 每只 5,000 元，远低于最低佣金咬住线 "
                f"¥{COMMISSION_MIN_BINDS_BELOW:,.0f} ⇒ 单腿佣金按 ¥5 收，"
                "实际费率是名义费率的数倍。这是小资金的**主**成本来源，"
                "不是价差。"),
        },
        "external_sources": {
            "survey_date": "2026-10-16",
            "numbers_bp_round_trip": {
                "spread_or_slippage_low_smallcap": 40.0,
                "spread_or_slippage_high_smallcap": 100.0,
                "near_limit_up": 204.0,
            },
            "source_grade": (
                "⚠️ 中低。滑点区间来自券商投教/自媒体口径，**没有学术文献标定**。"
                "与 `SPREAD_ONE_WAY`（买卖价差）**是否重叠未经验证** ⇒ "
                "不可相加（会双计），也不可直接替换（会漏计）。"
                "因此本文件只做敏感性，不产出「真实摩擦」。"),
            "limit_up_is_excluded_in_production": (
                "`scripts/run_daily.py:500` 实盘名单已剔除「一字板 / 涨停收盘」"
                "⇒ 204bp 那档在生产里不会发生。此处保留只为量出它的量级。"),
        },
        "sensitivity": rows,
        "breakeven": {
            "definition": "使年化摩擦恰好等于 20% 的往返价差 bp",
            "round_trip_spread_bp": beq,
            "production_bp": prod_bp,
            "headroom_multiple": (None if beq is None else round(beq / prod_bp, 2)),
            "reading": (
                f"生产口径往返 {prod_bp:.0f}bp ⇒ 摩擦 {f_prod * 100:.2f}%，"
                f"毛须给出 {(target + f_prod / weight) * 100:.2f}%。"
                + (f"摩擦吃光 20% 目标的临界价差是 {beq:.0f}bp，"
                   f"即生产假设有 **{beq / prod_bp:.1f} 倍**余量。"
                   if beq else "即使价差为 0 摩擦也达不到 20% ⇒ 临界值不存在。")),
        },
        "what_this_does_not_say": {
            "does_not_say_real_friction": (
                "本文件不给「真实摩擦是多少」。真实值需要实测滑点，"
                "本文件没有实测。敏感性表的每一行都是一个**假设**，不是一个测量。"),
            "does_not_change_costs_py": (
                "生产 `SPREAD_ONE_WAY` 保持 0.0015 不变。"
                "改它属于改判据臂，须另起预注册；本文件只登记外部区间，不动生产。"),
            "does_not_change_D2_verdict": "不改判决档、不改 TARGET、不改任何预注册阈值",
            "contradiction_policy": "与本文件结论相反时，只加对照臂，不改判据臂",
        },
    }

    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    # 🚨 第 166 层：零数据输入 ⇒ `data_fp` 不适用，逐字声明；
    #   真实依赖登记成**常量来源文件的字节指纹**（零数据 ≠ 零输入）。
    provenance.stamp(
        OUT, GENERATOR, root=ROOT, expect_bytes=expect_bytes,
        extra={
            "data_fp_applicability": DATA_FP_NOT_APPLICABLE,
            "constant_sources_sha256": {
                rel: hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()
                for rel in CONSTANT_SOURCES
            },
            "constant_sources_what": {rel: what for rel, what in CONSTANT_SOURCES.items()},
        })
    print(f"→ {OUT.relative_to(ROOT)}")
    print(f"D1 配置: 5 万 / {n_pos} 只全仓 / 年调仓 {reb:.4f} 次（精确）")
    print(f"{'往返价差':>10s} {'年化摩擦':>10s} {'毛须给出20%净':>14s}  说明")
    print("-" * 74)
    for r in rows:
        star = " ★生产" if r["is_production"] else ""
        print(f"{r['round_trip_spread_bp']:>8.0f}bp {r['annual_friction_on_capital'] * 100:>9.2f}%"
              f" {r['gross_needed_for_net_20pct'] * 100:>13.2f}%{star}")
    print()
    print(out["breakeven"]["reading"])


if __name__ == "__main__":
    main()

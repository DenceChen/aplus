#!/usr/bin/env python3
r"""实盘半仓账户的年化 —— D2（年化 20%）在**真钱敞口下**的读数。

## 它回答什么

`basket_nav_01_2026-10-16.json` 的判决档 `c=+10.5563%` 量的是
**满仓账户**（10 只等权、权益权重 1.0）。
但生产实盘**从来不是满仓**：

    aplus/alloc.py::build_allocation(max_positions=5, per_stock_cap=0.10)
    equity_weight = 5 × 0.10 = 0.5
    scripts/run_daily.py:647  build_allocation(up, capital=capital)
                             ← **只传 capital** ⇒ 走默认值 ⇒ 半仓

⇒ 判决档那个数**不是用户账户会拿到的数**。本脚本补上这一列。

## 敞口权重从生产签名读，不手打

🚨 `W = 0.5` **不是本文件的常量**。它由 `build_allocation` 的两个默认参数
相乘而来，而 `run_daily.py:647` 只传 `capital` ⇒ 哪天有人改默认值，
本文件的读数**必须跟着变**。手打 `0.5` 会让它在改动后仍然绿
（记忆 `guards-that-pin-existence-not-property`）。

## 摩擦路线：只走 A，`annual_friction` 已排除

`net_excess_bt003` 的成本是 **`cost_drag = 换手感知 × 费率`（0.003，无固定额）**
⇒ 对 notional **严格线性** ⇒ 半仓的摩擦 = **parquet 逐日成本 × W**，
**在它自己的模型内精确**。

🚨 `aplus.costs.annual_friction` 是**另一个模型**（满换手 + 逐笔最低佣金 ¥5），
用它配半仓等于**拿上限当实付**。本文件把它列为**对照臂**并显式标 role，
**不作判决**（第 183 层：加对照臂，不是换判据臂）。

## 🚨 「半仓 ≠ 满仓的一半」——这句话只有一半是真的

2026-10-16 实测推翻了它的朴素读法：

    r_half = W×毛 − W×成本_drag，而 成本_drag = gross_excess − net_excess_bt003
           = W×(gross_excess − (gross_excess − net_excess_bt003))
           = W × (market_return + net_excess_bt003)
           = W × r_full                              ← **逐日恒等**

⇒ **逐日层面半仓就是满仓乘 W**，没有第二项。
之前量到的「差 +1.5760%」是**把 `annual_friction` 配在成本腿上**造成的
—— 那是**两个模型在同一个式子里的接缝**，不是半仓的性质。

真正非平凡的只剩两条，产物分别单列：

1. **复利非线性**：`CAGR(W·r) ≠ W·CAGR(r)`（实测比值 0.5831 vs W=0.5）
2. **榜宽假设**：`W×g` 假设「top-10 里的 5 只子篮子收益 = 10 只篮子收益」。
   `daily.parquet` **没有 top-5 子篮子序列** ⇒ 这是**建模假设，不是测量**。

## 两把尺子：「唯一尺子」在年数上不唯一

`aplus.verify.compounded_stats` 的 docstring 逐字写着
「对标『年化 20%』的**唯一尺子**」，而它年化用
`TRADING_DAYS_PER_YEAR = 252`；判决档 `measure_basket_nav_01` 用
`aplus.hurdle.DAYS_PER_YEAR = 244`。
⇒ 同一条序列、同一句「年化」，**两个数，差 3.45%**（252/244 = 1.0328）。

🚨 **两个常数都不改**（改任一 = 改判据臂，第 91 层，须另起预注册）。
处置是**并排印出**，每行都带 `_R` 与来源。

⚠️ `compounded_stats` 吃的是**逐笔判定表**（`direction`/`actual_pct`/`exit_date`），
不是日收益序列 ⇒ 直接喂 `daily.parquet` 会**静默返回全 `None`**。
本脚本造一张**合成逐笔表**（每天 `TOP_N` 行同值）把它**精确还原**后交给它，
让那把尺子真的在算，而不是本地重写 CAGR（`measure_portfolio_nav.py` 的既定做法）。

## 产物 role

⚠️ **`对照臂 / 推算`，不是判决**。它依赖四条假设，全部列在 `assumptions` 里。

用法
----
    .venv/bin/python scripts/deployed_account_nav.py
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DAILY = ROOT / "data/reports/wf_weights_backtest_k21.daily.parquet"
OUT = ROOT / "data/reports/deployed_account_nav.json"

#: 判决档的腿。**不许手打**，由 `measure_basket_nav_01.EXCESS_LEG_PREFIX` 家族
#: 选出的那一档（`bt003`）。
LEG = "net_excess_bt003"

#: 合成逐笔表每天放几行。🚨 **必须 ≥ `aplus.verify.MIN_BASKET`**，
#: 否则 `daily_portfolio_returns` 会把成分不足的日子**整组丢掉**，
#: 而那是静默的（返回更短的序列，不报错）。
#: 取 10 而不是取 `MIN_BASKET`（3）：10 对应「10 只等权」这个真实交付形态，
#: 而 3 会把「10 只」这个事实藏起来。两者都过 `MIN_BASKET`，但只有一个诚实。
SYNTHETIC_LEGS_PER_DAY = 10


def deployed_exposure() -> dict:
    """实盘权益敞口 **从生产签名读**，不手打。

    🚨 `scripts/run_daily.py:647` 只传 `capital` ⇒ `max_positions` 与
    `per_stock_cap` 走**默认值**。改默认值必须改变本函数的输出，
    否则产品改了而仪表还指着旧数（守卫 `test_the_exposure_comes_from_the_signature`）。
    """
    import inspect
    import sys
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from aplus.alloc import build_allocation

    params = inspect.signature(build_allocation).parameters
    n_pos = params["max_positions"].default
    cap = params["per_stock_cap"].default
    if n_pos is None or cap is None:
        raise ValueError(
            f"build_allocation 的 max_positions/per_stock_cap 没有默认值："
            f"max_positions={n_pos!r} per_stock_cap={cap!r}。"
            f"⇒ 生产可能显式传参，本脚本的「读签名」前提失效，拒绝出数。")
    return {
        "source": "inspect.signature(aplus.alloc.build_allocation).parameters[...].default",
        "caller": "scripts/run_daily.py:647  build_allocation(up, capital=capital)",
        "caller_passes_only": "capital",
        "max_positions": int(n_pos),
        "per_stock_cap": float(cap),
        "equity_weight": float(n_pos) * float(cap),
        "why_not_hand_typed": (
            "手打 0.5 会在默认值被改后仍然绿 ⇒ 产品改了而仪表指着旧数。"
            "这是「只钉存在不钉性质」的形态。"),
    }


def daily_cost_of_the_leg(df: pd.DataFrame) -> np.ndarray:
    """parquet **自己**扣掉的逐日成本（收益口径，非权益口径）。

    `gross_excess − net_excess_*` 是生成器 `at_cost` 写下的那条差，
    不是本文件重算的 ⇒ 它是**产物自身的字段之差**，不是公式回代。
    """
    return (df["gross_excess"].to_numpy(float)
            - df[LEG].to_numpy(float))


def half_exposure_daily(df: pd.DataFrame, W: float) -> np.ndarray:
    """半仓日收益 = `W×毛篮子 − W×该腿的逐日成本`。

    🚨 刻意**不**写成 `W × account_nav(...)` —— 那是同一个式子的另一条浮点
    路径，守卫将无法区分「读了 parquet 的成本列」与「随手乘了个 W」。
    两条路径的差是 **8.67e-19**（实测），**不是 0**。
    """
    gross_basket = df["market_return"].to_numpy(float) + df["gross_excess"].to_numpy(float)
    return W * gross_basket - W * daily_cost_of_the_leg(df)


def synthetic_leg_history(r: np.ndarray, index, legs_per_day: int
                          ) -> pd.DataFrame:
    """把日收益序列还原成 `compounded_stats` 吃的**逐笔判定表**。

    每天 `legs_per_day` 行同值 ⇒ `daily_portfolio_returns` 按 `exit_date`
    取均值后**精确**还原该日的日收益（均值 of k 个相同数 = 那个数）。
    🚨 `legs_per_day` 必须 ≥ `MIN_BASKET`，否则整天被静默丢弃。

    ⚠️ 不写 `cost` 列：`r` 已是**净值腿**，成本在它里面扣过了；
    再写一列会被 `daily_portfolio_returns` **再扣一次**（第 15 种形状）。
    """
    if legs_per_day < 1:
        raise ValueError("legs_per_day 必须为正")
    return pd.DataFrame({
        "direction": "up",
        "actual_pct": np.repeat(np.asarray(r, float), legs_per_day),
        "exit_date": np.repeat([str(i) for i in index], legs_per_day),
    })


# ═══════════════════════════════════════════════════════════════
# 三个比值的定性词：**各一个唯一入口**
# ═══════════════════════════════════════════════════════════════
# 🚨🚨 这几个函数是**模块级**的而不是 `main()` 里的嵌套：
#   嵌在 `main()` 里测试 import 不到 ⇒ 判决逻辑无任何东西能挡
#   （记忆 `code-a-guard-cannot-import-is-effectively-unguarded`）。
# 🚨 而**模块级本身还不够**：守卫若只读落盘产物，源码一改它看不见
#   （第 96 层）⇒ 守卫必须**直接调**这些函数并喂**合成数**，
#   每条分支各钉一次——那才叫「守在真的在跑的那一层」。
# 🚨 三个函数**各答各的问题**，不许互相代答（第 7 种形状）。

def effect_size_label(eff_full: float, eff_half: float) -> str:
    """`|c|/M` 的定性词。🚨 问的是「**c 是否异于 0**」。

    ⚠️ **目标值不参与这条判断**。把 `|T−c|/M` 的结论说成「c 测不出符号」
    就是第 7 种形状（两个各自正确的数当成同一个量比），
    2026-10-16 在本文件真发生过一次：产物里手写「两档都 > 1」，
    而满仓的缺口比实测 `0.42 < 1`。
    """
    if max(eff_full, eff_half) > 1:
        return "测得出符号"
    return "两档都测不出符号，连正负都定不了"


def gap_size_label(gapm_full: float, gapm_half: float) -> str:
    """`|T−c|/M` 的定性词。🚨 问的是「**c 是否低于目标**」。

    🚨 **必须三条分支**（两档都 >1 / 两档都 <1 / 分居 1 的两侧）：
    实测形态**就是第三条**（满仓 0.42、半仓 1.22），
    而只有两条分支的写法会把「分居两侧」说成其中一边。
    """
    if min(gapm_full, gapm_half) > 1:
        return "两档都大于"
    if max(gapm_full, gapm_half) < 1:
        return "两档都小于"
    return "两档分居 1 的两侧"


def ci_high_label(ci_hi_full: float, target: float) -> str:
    """CI 上端 `c+M` 相对目标的定性词（**第三个问题**，与上面两个都不同）。"""
    return "高于" if ci_hi_full > target else "不高于"


def ci_high_verdict(ci_hi_full: float, target: float) -> str:
    """CI 上端越线 ⇒ **不许说达不到**；不越线 ⇒ 可以说达不到。

    🚨 `target` **没有默认值**（第 182 层）：给了默认值，「哪个是判决臂、
    哪个是敏感性臂」的区分会被某次调用悄悄抹掉。

    🚨 **产物与 CLI 共用这一句**：2026-10-16 重构前它们各写一份，
    而 CLI 那份是 f-string 内联的三元 ⇒ 变异打 CLI 时**产物层守卫全绿**。
    ⇒ 「同一个判断两处措辞」在本文件已造成过一次真事故，
    这里只允许**一个**入口。
    """
    if ci_hi_full > target:
        return f"不许说达不到 {target:.0%}（也不许说接近）"
    return f"可以说达不到 {target:.0%}"


def gap_over_mde_note(eff_full: float, eff_half: float, gapm_full: float,
                      gapm_half: float, ci_hi_full: float,
                      target: float) -> str:
    """产物里那段说明。🚨 **定性全部由上面四个函数从数算出**，此处不手打。"""
    return (
        f"🚨 **两个比值问两个不同的问题，不许合并读**：\n"
        f"  ① `|c|/M`（效应量比）= 满仓 {eff_full:.4f} / 半仓 {eff_half:.4f}"
        f" ⇒ c 本身{effect_size_label(eff_full, eff_half)}；\n"
        f"  ② `|T−c|/M`（缺口比）= 满仓 {gapm_full:.4f} / 半仓 {gapm_half:.4f}"
        f" ⇒ 缺口{gap_size_label(gapm_full, gapm_half)}自己的 MDE；\n"
        f"  ③ CI 上端 `c+M` = {ci_hi_full:.4%}"
        f"，{ci_high_label(ci_hi_full, target)}目标 {target:.0%}"
        f" ⇒ {ci_high_verdict(ci_hi_full, target)}。\n"
        f"⚠️ ① 与 ② 不是同一个量：① 说「c 是否异于 0」，"
        f"② 说「c 是否低于 {target:.0%}」。"
        f"用 ② 的结论去回答 ① 的问题是第 7 种形状（两个各自正确的数当成同一个量）。"
        f"本块**不是判决**——判决来自 §18.4 的三条规则，本块只是同一读数"
        f"按「相对目标」再表述一次；两个 cagr 都是**测不准的点估计**"
        f"（记忆 `unmeasurable-point-estimate-only-supports-shape-claims`）。")


def single_arm_effect_phrase(eff_full: float) -> str:
    """`|c|/M` **单档**的定性（满仓档那一行）。🚨 比较号也是**算出来的**。

    重构前这一块是 f-string 内联的两个三元（`{'<' if … else '≥'}` 与
    `{'**连符号都定不了**' if … else …}`）⇒ 与 `effect_size_label` 同一种病：
    方向词手打在这里，就多一份可以打偏的副本。
    """
    if eff_full < 1:
        return "< 1 ⇒ **连符号都定不了**"
    return "≥ 1 ⇒ 符号可定，但**不代表**幅度可定"


def not_a_verdict_note(eff_full: float) -> str:
    """`|c|/M` **单档**的定性（产物另一块用的是它，措辞不同、来源同一个函数）。"""
    return (
        "满仓档 `|c|/M = "
        f"{eff_full:.4f} {single_arm_effect_phrase(eff_full)}；"
        "本文件的所有 cagr 都继承这一点。"
        "🚨「距 20% 差 X pp」在此**不是判决**，只是两个都测不准的点估计之差"
        "（记忆 `unmeasurable-point-estimate-only-supports-shape-claims`）。")


def ci_high_console_line(ci_hi_full: float, target: float) -> str:
    """CLI 那一行。与产物**共用**同一批定性函数 ⇒ 打印与产物没有第二份副本。

    ⚠️ 「也不许说接近」这个限定词**只挂在越线那一支**（由 `ci_high_verdict`
    决定）：不越线时说「不许说接近」是错的（那就是「没说接近」）。
    """
    mark = ">" if ci_hi_full > target else "≤"
    return (f"CI 上端 c+M = {ci_hi_full:.2%} {mark} {target:.0%}"
            f" ⇒ **{ci_high_verdict(ci_hi_full, target)}**")


def main() -> None:
    import sys
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from aplus import provenance
    # 🚨 目标值**从判据臂 import**，不手打 `0.20`（第 91 层：判据必须预注册，
    #   且门槛不许是散落的字面量——本文件有三处曾经各写一次 `0.20`）。
    from aplus.hurdle import TARGET_NET
    from aplus.verify import (MIN_BASKET, TRADING_DAYS_PER_YEAR,
                              compounded_stats, daily_portfolio_returns)
    from scripts.measure_basket_nav_01 import R, account_nav, tier

    expect = provenance.code_file_sha256(ROOT, "scripts/deployed_account_nav.py")

    expo = deployed_exposure()
    W = float(expo["equity_weight"])

    df = pd.read_parquet(DAILY)
    r_full = account_nav(df, LEG)
    r_half = half_exposure_daily(df, W)

    # ── 还原核对：那把尺子是不是真的在算（外部锚：尺子自己的下游函数） ──
    hist = synthetic_leg_history(r_half, df.index, SYNTHETIC_LEGS_PER_DAY)
    restored = daily_portfolio_returns(hist).to_numpy(float)
    restore_max_abs = float(np.abs(restored - r_half).max()) if len(restored) else None
    restore_bitwise = bool(len(restored) == len(r_half)
                           and np.array_equal(restored, r_half))
    if len(restored) != len(r_half):
        raise SystemExit(
            f"🚨 合成逐笔表还原后行数 {len(restored)} ≠ 半仓序列 {len(r_half)}。"
            f"\n   ⇒ `MIN_BASKET={MIN_BASKET}` 把成分不足的日子整组丢掉了"
            f"（SYNTHETIC_LEGS_PER_DAY={SYNTHETIC_LEGS_PER_DAY}）。"
            f"\n   ⇒ 那把尺子读到的是**更短的样本**，仍会出数。停止，不出结论。")

    full_t = tier("判决档·满仓·244", r_full)
    half_t = tier("对照臂·实盘半仓·244", r_half)

    # 252 口径（`compounded_stats` = CLAUDE.md 逐字称的「唯一尺子」）
    stats_full_252 = compounded_stats(synthetic_leg_history(
        r_full, df.index, SYNTHETIC_LEGS_PER_DAY))
    stats_half_252 = compounded_stats(hist)

    naive = r_full * W
    naive_max_abs = float(np.abs(r_half - naive).max())

    # 距目标的缺口（**百分点**）—— 打印与产物共用同一个局部量，
    # 免得「一处算一处抄」（第 137 层：手抄件会悄悄过期）。
    gap_half = (TARGET_NET - half_t["cagr"]) * 100
    gap_full = (TARGET_NET - full_t["cagr"]) * 100

    # 🚨🚨 下面四个比值**问四个不同的问题**，混用会把「测不出 c 的符号」
    #   逐字说成「测不出离 20% 的距离」（第 142 层：定性话与打印的数互相打脸）。
    #   ⇒ 全部在这里**算出来**，下游不许手写方向判断；
    #   ⇒ 定性词调**模块级**那四个函数，产物与打印没有第二份手打副本。
    eff_full = abs(full_t["cagr"]) / full_t["mde_as_annual_pct"]   # c 异于 0？
    eff_half = abs(half_t["cagr"]) / half_t["mde_as_annual_pct"]
    gapm_full = abs(TARGET_NET - full_t["cagr"]) / full_t["mde_as_annual_pct"]
    gapm_half = abs(TARGET_NET - half_t["cagr"]) / half_t["mde_as_annual_pct"]
    ci_hi_full = full_t["cagr"] + full_t["mde_as_annual_pct"]

    cost_daily = daily_cost_of_the_leg(df)
    cost_annual = float(cost_daily.mean() * R)
    nonzero_cost_days = int((cost_daily != 0).sum())

    cagr_ratio = (None if (half_t["cagr"] is None or full_t["cagr"] in (None, 0))
                  else float(half_t["cagr"] / full_t["cagr"]))

    out = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "role": "⚠️ 对照臂 / 推算，**不是判决**。依赖下列四条假设。",
        "question": "实盘半仓敞口下，D2（年化 20%）的读数是多少",
        "source_daily": str(DAILY.relative_to(ROOT)),
        "leg": LEG,
        "exposure": expo,

        "assumptions": [
            "① 敞口 = `build_allocation` 的默认参数之积，而生产只传 `capital`。",
            "② 半仓摩擦 = parquet 逐日成本 × W。**仅在成本对 notional 严格线性时精确**"
            f"（该腿费率 {LEG} 的 cost_drag = 换手 × 费率，无固定额 ⇒ 无固定额即线性）。",
            "③ `W×g` 假设「top-10 里的 5 只子篮子收益 = 10 只篮子收益」——"
            "🚨 `daily.parquet` **没有 top-5 子篮子序列** ⇒ **这是建模假设，不是测量**。",
            "④ 闲置的另外 50% 现金收益按 0 计（未建模货币基金/余额宝利息）。"
            "若按 1.5%/年 计，会把账户年化抬高约 0.75pp —— 本文件**未**计入。",
        ],

        "friction_route": {
            "used": "A：parquet 逐日成本 × W",
            "why_exact_within_its_model": (
                "该腿的成本是 `cost_drag = 换手率 × 费率(0.003)`，**无每笔固定额** ⇒ "
                "成本对 notional 严格线性 ⇒ 缩放 W 是精确的，不是近似。"),
            "annual_cost_on_full_equity_pct": round(cost_annual * 100, 4),
            "annual_cost_on_deployed_equity_pct": round(cost_annual * W * 100, 4),
            "nonzero_cost_days": nonzero_cost_days,
            "n_days": int(len(df)),
            "excluded_route_B": {
                "what": "aplus.costs.annual_friction(50000, 0.5, 5, 244/21)",
                "role": "⚠️ **对照臂，非判决**。它与路线 A **不是同一个模型**。",
                "why_excluded_from_the_verdict": (
                    "`annual_friction` 按**满换手 + 逐笔最低佣金 ¥5** 建模，"
                    "而 parquet 的成本是**实际换手感知**且**无固定额**。"
                    "用它配半仓 = **拿上限当实付**（上一版就是这么错的）。"),
                "not_comparable_as_a_second_answer": (
                    "两个数不是「两个估计」，是两个模型 ⇒ 并列会制造"
                    "「半仓摩擦是 X 或 Y」的假分叉（第 7 层）。"),
            },
        },

        "two_rulers": {
            "🚨_why_two": (
                "CLAUDE.md 称 `compounded_stats` 为「对标年化 20% 的**唯一尺子**」，"
                "而判决档用 `measure_basket_nav_01`。二者年数常数不同 ⇒ "
                "**「唯一」在年数上不成立**。两个常数都不改（改 = 判据臂，第 91 层）。"),
            "ruler_244_judgement_arm": {
                "source": "scripts/measure_basket_nav_01.py::tier  (aplus.hurdle.DAYS_PER_YEAR)",
                "R": R,
                "full": full_t,
                "half": half_t,
            },
            "ruler_252_named_unique": {
                "source": "aplus/verify.py::compounded_stats  (TRADING_DAYS_PER_YEAR)",
                "R": TRADING_DAYS_PER_YEAR,
                "full": {k: stats_full_252.get(k)
                         for k in ("total_return", "cagr", "n_days", "years")},
                "half": {k: stats_half_252.get(k)
                         for k in ("total_return", "cagr", "n_days", "years")},
            },
            "caliber_conflict_measured": {
                "years_252": stats_full_252.get("years"),
                "years_244": full_t["years"],
                "years_ratio": round(full_t["years"] / stats_full_252["years"], 6),
                "cagr_252": stats_full_252.get("cagr"),
                "cagr_244": full_t["cagr"],
                "cagr_ratio_252_over_244": (None if stats_full_252.get("cagr") in (None, 0)
                                           else round(stats_full_252["cagr"] / full_t["cagr"], 6)),
                "reading": (
                    f"同一条满仓序列：252 口径 cagr="
                    f"{stats_full_252['cagr']:+.4%}，244 口径 cagr={full_t['cagr']:+.4%} ⇒ "
                    f"差 {abs(stats_full_252['cagr'] / full_t['cagr'] - 1) * 100:.2f}%，"
                    f"全部来自年数常数（252/244 = {TRADING_DAYS_PER_YEAR / R:.6f}）。"),
                "policy": "两把尺子都印、都带 R 与来源；**不许静默二选一**。",
            },
            "synthetic_restore": {
                "why_needed": (
                    "`compounded_stats` 吃**逐笔判定表**（direction/actual_pct/exit_date），"
                    "不是日收益序列 ⇒ 直接喂 `daily.parquet` 会**静默返回全 None**"
                    "（缺 `direction` 列 ⇒ `daily_portfolio_returns` 返空 Series）。"),
                "legs_per_day": SYNTHETIC_LEGS_PER_DAY,
                "MIN_BASKET": MIN_BASKET,
                "restored_rows": int(len(restored)),
                "source_rows": int(len(r_half)),
                "max_abs_diff_vs_series": restore_max_abs,
                "bitwise_equal": restore_bitwise,
                "why_not_bitwise": (
                    "还原走 `groupby().mean()`，与原序列的加法顺序不同 ⇒ "
                    f"差 {restore_max_abs:.3e}，**不是 0**。"
                    "守卫用 allclose 而非 `==`，且这个差是**打印出来的数**不是判据。"),
                "is_exact_not_faked": (
                    "每天 N 行同值 ⇒ 组内均值 **≡** 该值（数学上精确）"
                    "⇒ 还原不丢信息、不造收益，只是**换一种喂法**。"),
            },
        },

        "why_half_is_not_simply_half": {
            "daily_level_identity": (
                "逐日层面半仓**就是**满仓乘 W（见 docstring 推导）—— "
                "🚨 任何「半仓收益要另算一套」的写法都是接缝出错。"),
            "measured_max_abs_diff_vs_W_times_full": naive_max_abs,
            "why_not_zero": "两条浮点运算路径不同（W×(mkt+gr)−W×cost vs W×(mkt+net)）。",
            "compounding_is_the_real_nonlinearity": {
                "cagr_ratio_half_over_full": (None if cagr_ratio is None
                                              else round(cagr_ratio, 6)),
                "W": W,
                "deviation_from_W": (None if cagr_ratio is None
                                     else round(cagr_ratio - W, 6)),
                "mde_ratio_half_over_full": (None if not full_t["mde_as_annual_pct"]
                                             else round(half_t["mde_as_annual_pct"]
                                                        / full_t["mde_as_annual_pct"], 6)),
                "reading": (
                    f"CAGR 比 {cagr_ratio:.4f} ≠ W={W}（差 {cagr_ratio - W:+.4f}）"
                    f"⇒ 半仓年化**不是**满仓年化的一半。"
                    f"MDE 比 {half_t['mde_as_annual_pct'] / full_t['mde_as_annual_pct']:.4f}"
                    f"≈ W ⇒ 测量尺度基本线性。"),
            },
            "breadth_is_a_modelling_assumption_not_a_measurement": (
                "W×g 要求「5 只子篮子收益 = 10 只篮子收益」，而 parquet **只有 10 只**的序列。"
                "⇒ 这是假设③，**没有任何测量支持它**。"),
        },

        "gap_to_target": {
            "target": TARGET_NET,
            "judgement_arm_full_cagr": full_t["cagr"],
            "control_arm_half_cagr": half_t["cagr"],
            "gap_full_pp": round(gap_full, 4),
            "gap_half_pp": round(gap_half, 4),
            "gap_over_mde_half": round(gapm_half, 4),
            "gap_over_mde_full": round(gapm_full, 4),
            "effect_over_mde_half": round(eff_half, 4),
            "effect_over_mde_full": round(eff_full, 4),
            "ci_high_full": round(ci_hi_full, 6),
            # 🚨 定性词调**模块级**那四个函数，与 CLI 同一份来源。
            #   这里一旦再写回内联三元，就是第 142 层复发（第二份手打副本）。
            "🚨_gap_over_mde_note": gap_over_mde_note(
                eff_full, eff_half, gapm_full, gapm_half, ci_hi_full, TARGET_NET),
            # 🚨 同上：这两个内联三元**曾在这里活过一轮**（2026-10-16 重构漏掉本块，
            #   而 `not_a_verdict_note` 因此是**零调用点的死函数**）——
            #   死函数本身不报错、不报警，正是第 6 种形状「假装能调」的近亲。
            #   ⇒ AST 接线守卫（`not_a_verdict_note` 必须在 `main()` 里被调到）
            #     就是为这条而存在的：**它守的不是措辞，是「调用点还在」**。
            "🚨_not_a_verdict": not_a_verdict_note(eff_full),
        },

        "what_this_is_not": {
            "is_not_a_verdict": "对照臂 / 推算。四条假设全列在 assumptions。",
            "does_not_change_any_ruler": (
                "不改 `R=244`，不改 `TRADING_DAYS_PER_YEAR=252`，不改 `TARGET`。"
                "两个年数常数的冲突只登记、不裁决。"),
            "does_not_suggest_raising_exposure": (
                "`aplus/alloc.py:1-33` 的 docstring 逐字："
                "「**50% 不是成本优化。** 50% 的真实理由是纸面验证期的风险敞口封顶」"
                "（`excess_breadth −4.65` 仍为负），**与成本模型完全正交**。"
                "本文件不提议提到 100%。"),
            "route_B_is_not_a_second_answer": (
                "`annual_friction` 与 parquet 成本不是同一个模型，"
                "并列即制造假分叉（第 7 层）。"),
            "contradiction_policy": "若与本文件结论相反，只加对照臂，不改判据臂。",
        },
    }

    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    provenance.stamp(OUT, "scripts/deployed_account_nav.py", ROOT, expect_bytes=expect)
    print(f"→ {OUT.relative_to(ROOT)}")
    print(f"敞口 W = {W}  ({expo['max_positions']} × {expo['per_stock_cap']}, 读自 build_allocation 签名)")
    print(f"摩擦   路线A 年化 {cost_annual * 100:.4f}%/年（满权益）→ {cost_annual * W * 100:.4f}%/年（半仓）")
    print(f"       非零成本日 {nonzero_cost_days}/{len(df)}")
    print(f"逐年收益 满仓 {full_t['cagr']:+.6%}   半仓 {half_t['cagr']:+.6%}   W={W}")
    print(f"        MDE  满仓 {full_t['mde_as_annual_pct']:+.6%}  半仓 {half_t['mde_as_annual_pct']:+.6%}")
    print(f"CAGR 比 {cagr_ratio:.6f}（≠ W，差 {cagr_ratio - W:+.6f}）"
          f"  MDE 比 {half_t['mde_as_annual_pct'] / full_t['mde_as_annual_pct']:.6f}")
    print(f"两把尺子 252: {stats_full_252['cagr']:+.6%}（满仓）/{stats_half_252['cagr']:+.6%}（半仓）"
          f"   244: {full_t['cagr']:+.6%}/{half_t['cagr']:+.6%}")
    print(f"合成还原 {len(restored)}/{len(r_half)} 行，max|diff|={restore_max_abs:.3e}（非 0）")
    # 🚨🚨 第 97 层（字段名带 pct 而值是分数）在**这一行**复发过一次：
    #   cagr 侧乘了 100，而 MDE 侧 `.2f` 没乘 ⇒ 印出「0.11pp」而真值 11.31pp，
    #   差 **100 倍且方向严重低估**（读起来像「MDE 很小、13.84pp 判不了」
    #   而不是「MDE 比效应还大、连符号都定不了」）。
    # ⇒ 两侧一律用 `%` 格式（自带 ×100），跨量纲比较只在这一屏发生一次转换。
    print(f"🚨 role = 对照臂/推算；距 20% 差 满仓 {gap_full:+.2f}pp / 半仓 {gap_half:+.2f}pp"
          f"（MDE 半宽 满仓 {full_t['mde_as_annual_pct'] * 100:.2f}pp /"
          f" 半仓 {half_t['mde_as_annual_pct'] * 100:.2f}pp）")
    # 🚨 两个比值问**两个不同的问题**，混用会把「测不出 c 的符号」
    #   说成「测不出离 20% 的距离」（第 142 层：定性话与打印的数互相打脸）。
    # ⇒ 定性**从数里算出来**，不手写。
    print(f"   |c|/M（问「c 是否异于 0」）= 满仓 {eff_full:.2f} / 半仓 {eff_half:.2f}"
          f" ⇒ {effect_size_label(eff_full, eff_half)}")
    print(f"   |T−c|/M（问「c 是否低于 {TARGET_NET:.0%}」）= 满仓 {gapm_full:.2f}"
          f" / 半仓 {gapm_half:.2f}"
          f" ⇒ 缺口{gap_size_label(gapm_full, gapm_half)}自己的测量误差")
    print(f"   {ci_high_console_line(ci_hi_full, TARGET_NET)}"
          f"｜两个比值不是同一个量，合并读即第 7 种形状")


if __name__ == "__main__":
    main()

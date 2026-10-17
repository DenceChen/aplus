#!/usr/bin/env python
"""跌腿判决：用户每天收到的**第二半交付物**此前从未被测过（2026-10-17）。

## 为什么这条臂必须单独存在

`scripts/score_ranking_power.py` 的判决臂 `GATE_ARM = "up"`（预注册写死）
⇒ **涨腿**的排序力被测过，而**跌腿**的 10 只每天都在出、**从来没被判决过**。
`aplus/predict.py:276` 确认 `down_src = ranked.tail(bottom_n)`
⇒ 跌腿是**分数最低**的 `bottom_n` 只。

⚠️ **本脚本一个字都不改 `score_ranking_power.py`**（第 91 层：改判据臂 = 换判据）。
本脚本是**新增对照臂**，`role` 字段逐字声明它不是判决臂。

## 🚨 判据全部复用，本脚本**不新造任何门槛**

| 复用对象 | 来源 | 为什么必须复用 |
|---|---|---|
| `MIN_BLOCKS_FOR_ANY_SIGN = 6` | `forward_d2_verdict` | 独立观测数下限，预注册 R4 |
| `verdict_of(n, point, mde, lo, hi)` | 同上 | 四档判决 + **`POWERLESS` 必须最先判** |
| `stats_of(series)` | 同上 | MDE / CI / t 的**唯一实现**（形状 17） |

⚠️ **本文件里不许出现 MDE、t 分布、CAGR 的公式。**
第 175 层：「统一口径」这个好动机把一个对的公式改成了错的。

## 🚨 结构性闸门：**只用 `MIN_BLOCKS`，不用 `years ≥ 1.0`**

`measure_portfolio_nav.py` 用 `years ≥ 1.0` 当结构闸门，而那要求 **244 个交易日**。
跌腿的独立观测单位是**成交窗口**（一天一个）⇒ `years ≥ 1.0` 要求 **244 个窗口 = 一整年**。

⇒ 若两条同时生效，**244 永远压过 6** ⇒ 判决永久 `POWERLESS`
⇒ 那等于宣布「跌腿不可测」，而第 160 层明确：**「测不到」是缺样本，不是缺价值**，
「加数据」正是唯一能解锁判决的那条路。

⇒ **只用 `MIN_BLOCKS_FOR_ANY_SIGN = 6`。** `years` 作为**报告项**落盘，
**不参与判决分支** —— `years` 这个字段在 `out` 里但不在 `judge()` 的任何条件里，
由 `tests/test_downleg_ranking.py` 钉住。

## 🚨 符号约定：跌腿的排序力被编码成**正 = 有排序力**

`aplus/predict.py` 是「分越高越看涨」，跌腿取**最低**的 `bottom_n` 只
⇒ **越负的分数才越该跌** ⇒ 与涨腿方向相反。

⇒ 定义每块的 `spread = (分数最高的一半).gross − (分数最低的一半).gross`
⇒ **`spread > 0` ⟺ 越负的跌得更多 ⟺ 有排序力** ⇒ 于是 `verdict_of` **原样可用**，
不必改它一行（改它就是把第 91 层犯在新文件上）。

⚠️ 这个**取负是转换，不是换判据** ⇒ 所以它必须**落进产物**
（`sign_convention` 字段），否则键名与内容会互相打脸（第 179 层）。

## 🚨 跌腿不可执行 —— 外部锚，不是我的推断

石川《因子投资方法与实践》`data/books/cn_factor_lines.txt:768` 逐字：
「**不合理考虑做空限制，也会高估因子的收益。**」

⇒ A 股散户融不到券 ⇒ **跌腿不可直接执行** ⇒ 它的产品价值只可能来自
**「哪 10 只不该买 / 该减仓」**，而**不是**「空掉这 10 只」。

⇒ 所以本脚本**必须**把这个口径写进产物 `executable` 块。
⚠️ 本仓**零**融券代码（`grep` 命中 0）—— 但「5 万拿不到融资融券额度」是**外部常识**，
按本仓规矩**不许当已验证前提引用**，只当口径声明。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from aplus import provenance  # noqa: E402
from forward_d2_verdict import (  # noqa: E402
    MIN_BLOCKS_FOR_ANY_SIGN, REQUIRED_COLUMNS, VERDICT_POWERLESS,
    stats_of, verdict_of,
)

GENERATOR = "scripts/measure_downleg_ranking.py"

DOWN = "down"
# 🚨 块内切两半时**按行数**切，不写死「5 只」
#    —— 第一个成交窗口由三个 pred_date 映射而来（实测 29/30 行），
#    写死 5 会把后两个信号日排除在窗口外，而它们同样是那一天下的单。
MIN_HALF = 2  # 每半至少这么多行，否则这个窗口给不出排序力

TRADING_DAYS_PER_YEAR = 244  # 只作报告项；**不参与判决**（见模块 docstring）


# ── 取腿 ────────────────────────────────────────────────────────────

def down_leg_rows(frame: pd.DataFrame) -> pd.DataFrame:
    """取**跌腿**，剔掉不可判定的行。

    🚨 剔除条件是 `gross_pct` 为 NaN —— 按**量在不在**判，不按 `result` 判。
       （与 `forward_d2_verdict.leg_rows` 同一条纪律，两处各写一份是形状 17，
       但**判据不许改**，所以这里复制形状而不 import 涨腿版本。）

    ⚠️ `direction` 取 `"down"` 而不是引用 `forward_d2_verdict.UP` ——
       那个常量名叫 `UP`，拿去剔跌腿会让常量名与内容互相打脸（第 179 层）。
    """
    for col in REQUIRED_COLUMNS:
        if col not in frame.columns:
            raise KeyError(f"verification 底稿缺必需列 {col!r}")
    if "score" not in frame.columns:
        raise KeyError(
            "verification 底稿缺 'score' 列 —— 没有它就无法按生产逻辑排序。"
            "⚠️ 不许回落成「按原行序取一半」：那会把「测试真排名次」"
            "伪装成「测量排名次」。")
    down = frame[frame["direction"] == DOWN]
    return down[down["gross_pct"].notna() & down["entry_date"].notna()]


def excess_blocks(leg: pd.DataFrame) -> pd.DataFrame:
    """按 `entry_date` 切窗口，求每窗的**毛超额均值**。

    🚨 观测单位 = **成交窗口**（`entry_date`），不是 `pred_date`。
       前瞻底稿是逐日**追加**的，同一个 `entry_date` 会由多个 `pred_date` 映射而来
       （实测 2026-10-12 那窗 29/30 行来自 3 个信号日）
       ⇒ 按 `pred_date` 切会把**同一个成交窗口数成 3 次独立观测**。
    """
    if leg.empty:
        return pd.DataFrame(columns=["entry_date", "excess_mean", "gross_mean",
                                     "market_mean", "n_legs"])
    work = leg.assign(excess=leg["gross_pct"] - leg["market_pct"])
    # 🚨 `market_pct` 来自基准抽样（`market_n_used`），抽样没跑成的那天是 NaN。
    #    **不剔** ⇒ 一个窗口里只要有一行 NaN，整个窗口的均值就是 NaN
    #    ⇒ `stats_of` 收到 NaN 而 `NaN != 0` 为真 ⇒ 「测不出」与「算坏了」同形。
    #    剔除量在不在，与 `down_leg_rows` 同一条纪律。
    work = work[np.isfinite(work["excess"])]
    if work.empty:
        return pd.DataFrame(columns=["entry_date", "excess_mean", "gross_mean",
                                     "market_mean", "n_legs"])
    out = (work.groupby("entry_date", as_index=False)
                .agg(excess_mean=("excess", "mean"),
                     gross_mean=("gross_pct", "mean"),
                     market_mean=("market_pct", "mean"),
                     n_legs=("excess", "size"))
                .sort_values("entry_date")
                .reset_index(drop=True))
    return out


# ── 排序力臂 ────────────────────────────────────────────────────────

def ranking_spread_per_window(leg: pd.DataFrame) -> list[dict]:
    """每窗切成「分数最低的一半」与「分数最高的一半」，返回排序力差。

    🚨 **切法与 `aplus/predict.py` 的真实语义对齐**（第 119 层）：
       跌腿是 `ranked.tail(bottom_n)` ⇒ 生产**只发最低的那几只** ⇒
       能被用户买到的只有**最低**的一半 ⇒ 上界（最高那一半）只是**对照基准**。
    """
    rows: list[dict] = []
    for entry, g in leg.groupby("entry_date", sort=True):
        if len(g) < 2 * MIN_HALF:
            continue
        # 🚨 升序 ⇒ index 0 是分数**最负**的 ⇒ 生产会发的那几只
        ordered = g.sort_values("score", ascending=True)
        half = len(ordered) // 2
        lowest = ordered.head(half)          # 生产实际发出的那一半
        highest = ordered.tail(half)         # 对照基准：分数最高的那一半
        rows.append({
            "entry_date": entry,
            "n_legs": int(len(g)),
            "n_each_half": int(half),
            "lowest_score_mean": float(lowest["score"].mean()),
            "highest_score_mean": float(highest["score"].mean()),
            "lowest_gross_mean": float(lowest["gross_pct"].mean()),
            "highest_gross_mean": float(highest["gross_pct"].mean()),
            # 🚨 承重的符号转换：> 0 ⟺ 越负的跌得更多 ⟺ 有排序力
            "spread": float(highest["gross_pct"].mean() - lowest["gross_pct"].mean()),
        })
    return rows


def judge(stats: dict) -> str:
    """四档判决，**顺序即判据**，原样委托 `verdict_of`。

    ⚠️ 这里**一个字都不重写**：MDE 分层、CI 跨零、`POWERLESS` 最先判
       全在 `forward_d2_verdict` 里，换一份实现就是形状 17（第 112 层）。
    """
    return verdict_of(stats["n"], stats["point"], stats["mde"],
                      stats["lo"], stats["hi"])


# ── 口径声明 ────────────────────────────────────────────────────────

def caliber(n_windows: int) -> dict:
    """口径随数字一起离开脚本（第 7 层）。"""
    return {
        "observation_unit": "一个成交窗口（entry_date）",
        "why_not_pred_date": (
            "同一天下的多张名单映射到同一个 entry_date；按 pred_date 切"
            "会把同一个成交窗口数成多个独立观测。"),
        "n_windows": n_windows,
        "windows_per_year_report_only": TRADING_DAYS_PER_YEAR,
        "years_report_only": n_windows / TRADING_DAYS_PER_YEAR,
        "years_is_NOT_a_gate": (
            "⚠️ years **不参与判决**。若用 `years >= 1.0` 当结构闸门，"
            "它要求 244 个窗口 ⇒ 永久压过 MIN_BLOCKS=6 ⇒ 判决永久 POWERLESS"
            "⇒ 那等于宣布「跌腿不可测」，而「测不到」只等于缺样本。"
            "守卫 test_the_years_field_cannot_reach_the_verdict_branch 钉住这一条。"),
        "sign_convention": (
            "排序力臂的 `spread = 最高一半的毛收益 − 最低一半的毛收益`。"
            "⚠️ **spread > 0 ⟺ 有排序力** —— 因为生产发的是**分数最低**的"
            f"（`aplus/predict.py:276` 的 `ranked.tail(bottom_n)`），"
            "而打分器「分越高越看涨」⇒ 越负的才越该跌。"),
        "excess_convention": (
            "超额臂的 `excess = gross_pct − market_pct`，"
            "**> 0 ⟺ 跌腿比市场跌得多**（不是「跌腿在赚钱」）。"),
        "what_this_is_not": [
            "🚨 不是「跌腿无效」的判决 —— 只等于「在 N 个窗口上测不到」。",
            "🚨 不是「跌腿的绝对收益为负就说明选得准」——"
            "绝对收益里含整个市场的涨跌；本产物把 gross 与 market 并排印出。",
            "🚨 不是「跌腿可做空」的依据 —— 见 executable 块。",
        ],
    }


def executable_block() -> dict:
    """跌腿不可执行 —— 外部锚，不是本仓实验结论。"""
    return {
        "directly_executable": False,
        "why": (
            "石川《因子投资方法与实践》`data/books/cn_factor_lines.txt:768` 逐字："
            "「不合理考虑做空限制，也会高估因子的收益。」"
            "⇒ A 股散户融不到券 ⇒ 跌腿不能直接空掉。"),
        "honest_boundary": (
            "⚠️「5 万拿不到融资融券额度」是**外部常识，本仓未核实**"
            "（`grep` 全仓融券相关代码命中 0）⇒ 不许当已验证前提引用，"
            "只当口径声明。"),
        "so_the_only_useful_product_is": (
            "「这 10 只不该买 / 该从持仓里减掉」，而**不是**「空掉这 10 只」。"
            "⇒ 跌腿的价值必须由**信息价值**证明，而不由可执行性假设。"),
    }


# ── 编排 ────────────────────────────────────────────────────────────

def measure(leg: pd.DataFrame) -> dict:
    blk = excess_blocks(leg)
    ex_stats = stats_of(blk["excess_mean"].tolist()) if not blk.empty else None
    spread_rows = ranking_spread_per_window(leg)
    # 🚨 第 9 种形状（「新写的分支从未被执行过」）：
    #    `stats_of([])` 会除零，而**一个没跑出来的判决和一个崩了的判决长得一样**
    #    ⇒ 响亮拒绝，不落一个 `None` 判决。
    if not spread_rows:
        raise SystemExit(
            "没有任何成交窗口能给出排序力（每窗至少要 "
            f"{2 * MIN_HALF} 条可判定行）—— 不许出判决")
    rk_stats = stats_of([r["spread"] for r in spread_rows])
    return {
        "excess_arm": {
            "what_it_measures": "跌腿的毛超额（跌得是否比市场多）",
            "per_window": blk.to_dict(orient="records"),
            "stats": ex_stats,
            "verdict": judge(ex_stats) if ex_stats else VERDICT_POWERLESS,
        },
        "ranking_arm": {
            "what_it_measures": (
                "score 的排序力：**分数最低**的一半是否跌得更多"),
            "per_window": spread_rows,
            "stats": rk_stats,
            "verdict": judge(rk_stats),
        },
        "blocks_needed_to_leave_powerless": max(
            0, MIN_BLOCKS_FOR_ANY_SIGN - rk_stats["n"]),
    }


def verdict_about_downleg(out: dict) -> str:
    """给用户读的那一句。**不是判决，是一个待办的状态。**"""
    n = out["ranking_arm"]["stats"]["n"]
    need = out["blocks_needed_to_leave_powerless"]
    rk = out["ranking_arm"]["verdict"]
    if n < MIN_BLOCKS_FOR_ANY_SIGN:
        return (f"样本不足（{n} 个成交窗口 < {MIN_BLOCKS_FOR_ANY_SIGN}），"
                f"不给方向；还差 {need} 个成交窗口才开判。")
    if rk == VERDICT_POWERLESS:
        return (f"{n} 个窗口够数了，但排序力仍测不到（|spread|/MDE < 1）"
                "⇒ 连符号都定不了。")
    return f"排序力判决：{rk}（{n} 个窗口）。"


def main(argv=None) -> int:
    expect_bytes = provenance.code_file_sha256(ROOT, GENERATOR)  # 🚨 开跑前抓
    ap = argparse.ArgumentParser(description="跌腿判决（对照臂，不是判决臂）")
    ap.add_argument("--verification", type=Path,
                    default=ROOT / "data" / "reports" / "verification.parquet")
    ap.add_argument("--out", type=Path,
                    default=ROOT / "data" / "reports" / "downleg_ranking.json")
    args = ap.parse_args(argv)

    frame = pd.read_parquet(args.verification)
    leg = down_leg_rows(frame)
    if leg.empty:
        raise SystemExit("跌腿剔完不可判定行之后为空 —— 不许出判决")

    out = measure(leg)
    if out["excess_arm"]["stats"] is None:
        raise SystemExit(
            "跌腿超额臂剔完 NaN 之后没有任何成交窗口 —— 不许出判决")
    out["role"] = (
        "⚠️ **对照臂 / 诊断，不是判决臂。** "
        "判决臂是 `scripts/score_ranking_power.py`（GATE_ARM=\"up\"），本文件不改它一字。"
        "这里判的是 D1 交付物的**另一半**（每天 10 只跌股），此前从未被测过。")
    out["caliber"] = caliber(out["ranking_arm"]["stats"]["n"])
    out["executable"] = executable_block()
    out["verdict_about_downleg"] = verdict_about_downleg(out)
    out["structurally_the_gate_is"] = MIN_BLOCKS_FOR_ANY_SIGN
    out["overlap_check"] = {
        "same_pred_date_same_code_in_both_legs": int(
            frame[frame["direction"] == "up"].merge(
                frame[frame["direction"] == "down"],
                on=["pred_date", "code"]).shape[0]),
        "why_it_matters": (
            "同一只票同一天既说涨又说跌 ⇒ 那是一份自相矛盾的交付。"
            "实测 0 条 ⇒ 这条性质目前成立，但**没有守卫钉住它**（已登记欠账）。"),
    }
    args.out.write_text(json.dumps(out, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    provenance.stamp(
        args.out, GENERATOR, root=ROOT, expect_bytes=expect_bytes,
        extra={
            # 🚨 这才是本脚本真正的输入锚（第 166 层：章上的 data_fp 指的是
            #    脚本**没读过的**目录）。`stamp()` 不传 `data_root` 时退回
            #    `data/master/daily`（5460 个 parquet）的 `(文件数, max mtime)`，
            #    而本脚本读的是**一个** parquet ⇒ 那枚 data_fp 与判决无关。
            "source_parquet_sha256": hashlib.sha256(
                args.verification.read_bytes()).hexdigest(),
            "source_parquet_path": str(args.verification),
            "data_fp_is_NOT_my_input": (
                "章里的 data_fp 是 master/daily 的目录指纹，本脚本没读它。"
                "判断输入有没有变，只认 source_parquet_sha256。"),
            "role": "CONTROL_ARM_NOT_THE_VERDICT_ARM",
        })
    print(f"跌腿窗口 n={out['ranking_arm']['stats']['n']} "
          f"超额判决={out['excess_arm']['verdict']} "
          f"排序力判决={out['ranking_arm']['verdict']}")
    print(f"→ {out['verdict_about_downleg']}")
    print(f"→ {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

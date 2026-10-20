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
# 🚨 **信号作用域的唯一入口**（预注册 `docs/PREREG_SIGNAL_SCOPING_2026-10-21.md`）。
#   本脚本**不自己实现**过滤逻辑（第 97 层），也**不自己实现**指纹（第二份真相源）。
from aplus.signal_scope import (  # noqa: E402
    VERDICT_NOT_DECIDABLE,
    not_decidable_note,
    resolve_attribution_context,
    attribution_block,
    scope_block,
    scope_ledger,
)
from forward_d2_verdict import (  # noqa: E402
    MIN_BLOCKS_FOR_ANY_SIGN, REQUIRED_COLUMNS, VERDICT_POWERLESS,
    stats_of, verdict_of,
)
# 🚨 **只读复用涨腿臂（`scripts/score_ranking_power.py`）的判据实现。**
#    「同源才有可比性」：检出力下限、点二列 ρ、组内四分位必须与涨腿臂
#    **逐位同源** —— 复制一份的话两份会悄悄漂移，而漂移**没有任何东西会红**
#    （第 97 层：两份副本之间没有守卫）。
#    ⚠️ 本文件**不改** `score_ranking_power.py` 一个字（只 import，不改）。
#    ⚠️ 私有名（`_` 前缀）是**故意**的：它们不是对外 API，import 它们等于
#    「我知道自己在复用内部实现，且不打算改它」。
from score_ranking_power import (  # noqa: E402
    JUDGEABLE as GATE_JUDGEABLE,
    _groups_by, _independence_caliber, _permutation_p, _point_biserial,
    _within_group_quartile,
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


# ── 跌腿闸门（G4 镜像） ─────────────────────────────────────────────
#
# 预注册：`docs/PREREG_DOWNLEG_GATE_2026-10-20.md`（**写在跑数之前**）。
#
# 🚨 **方向镜像是本块最承重的一条**，它**只**由生产代码推出：
#   ① `aplus/predict.py:271` `ranked = df.sort_values("score", ascending=False)`
#      ⇒ **分越高越看涨**；
#   ② `aplus/predict.py:276` `up_src, down_src = ranked.head(top_n), ranked.tail(bottom_n)`
#      ⇒ **跌腿取的是分数最低的 bottom_n 只**；
#   ③ `aplus/verify.py:94-95` `return "hit" if (actual_pct > 0) == is_up_prediction else "miss"`
#      ⇒ 跌腿的 `hit` = **它真的跌了**。
#   ⇒ 分越低 ⇒ 越容易真的跌 ⇒ **命中率随 score 升高而下降**。
#   ⇒ 与命中率数字**无关**：把命中率换成随机数，方向规则不变。

GATE_ARM = "down"
GATE_ART_NAME = "downleg_gate.json"
GATE_SCORE_COL = "score"
#: 与 `score_ranking_power.N_PERM` / `SEED` **同值** ⇒ 检出力下限可比。
GATE_N_PERM = 20_000
GATE_SEED = 42
#: 🚨 方向必须**显式写进产物**（不许让读者猜）：
#: `"decreasing"` 指的是**命中率随 score 升高而下降**。
MONOTONE_DIRECTION = "decreasing"
QUARTILE_LABEL_CONVENTION = "Q1 = 组内最高分, Q4 = 组内最低分（与 score_ranking_power._within_group_quartile 同源）"
#: 判据在 **score 升序**的命中率序列上读：**非递增**（score ↑ ⇒ 命中率 ↓）。
#: ⚠️ 在 Q 标签上（Q1=最高分）它等价于**非递减** `Q1 <= Q2 <= Q3 <= Q4` ——
#:    与涨腿臂的 `all(seq[i] <= seq[i+1])` **代数形式逐字相同、含义相反**
#:    （第 143 层：口径被更正后，引用它的每一处都没有留下标记）⇒ 所以
#:    **方向必须显式落进产物**，不许让读者靠「长得像」去猜。
MONOTONE_TEST = (
    "按 score 升序的命中率序列**非递增**（decreasing：score ↑ ⇒ 命中率 ↓）；"
    "在 Q 标签上（Q1=最高分）等价于 hit_rate(Q1) <= hit_rate(Q2) "
    "<= hit_rate(Q3) <= hit_rate(Q4)"
)

VERDICT_PASS = "PASS"
VERDICT_POWERLESS = "NOT_PASSED_POWERLESS"
VERDICT_MEASURED = "NOT_PASSED_MEASURED"


def gate_measure(df: pd.DataFrame, score_col: str = GATE_SCORE_COL,
                 n_perm: int = GATE_N_PERM, seed: int = GATE_SEED) -> dict:
    """跌腿在 `score` 上的完整测量（**镜像**口径）。

    `n_perm` / `seed` 是**参数**而非常量，因为对账守卫要用同一组参数重算一遍
    （第 118 层：读落盘 json 等于没问）。写死的话守卫只能对着快照做自洽检查。
    """
    for col in ("direction", "result", "pred_date", score_col):
        if col not in df.columns:
            raise KeyError(f"verification 底稿缺必需列 {col!r}")
    leg = df[df["direction"] == GATE_ARM]
    # ⚠️ 判据里「可判定」的定义与涨腿臂**同源**（`score_ranking_power.JUDGEABLE`）：
    #    `hit`/`miss` 是判决，`no_data` 是**未知**、`flat` 是**无波动**，
    #    两者都**不是「未命中」**，混进分母会凭空造出命中率。
    usable = leg[leg["result"].isin(GATE_JUDGEABLE)].copy()
    # ⚠️ 缺分数的行**不许静默跳过**（第 112 层）：跳过后分母会悄悄变小，
    #    而报告仍说另一个数 ⇒ 剔除量显式记进产物。
    n_missing_score = int(usable[score_col].isna().sum())
    scored = usable[usable[score_col].notna()].copy()
    if scored.empty:
        raise SystemExit(
            "跌腿没有任何「可判定且有分数」的行 —— 不许出判决")
    out: dict = {
        "arm": GATE_ARM,
        "score_col": score_col,
        "n_rows_total": int(len(leg)),
        "n_rows_judgeable": int(len(usable)),
        "n_rows_dropped_undecidable": int(len(leg) - len(usable)),
        "dropped_breakdown": {k: int(v) for k, v in
                              leg.loc[~leg["result"].isin(GATE_JUDGEABLE), "result"]
                              .value_counts().items()},
        "n_rows_score_missing": n_missing_score,
        "n_rows_scored": int(len(scored)),
        "n_independent_pred_dates": int(leg["pred_date"].nunique()),
        # ⚠️ 独立单元是**成交窗口**（`entry_date`），不是条目数。
        #    `entry_date` 缺失时**显式返回 None 并说明**，绝不回落成 `pred_date`
        #    （回落 = 造一个与真相同形、看不出差别的数，第 6 种形状）。
        "independence_caliber": _independence_caliber(leg),
        "quartile_rule": "组内（pred_date × direction）按 score 名次切 4 段",
        "quartile_label_convention": QUARTILE_LABEL_CONVENTION,
        "monotone_direction": MONOTONE_DIRECTION,
        "monotone_test": MONOTONE_TEST,
    }
    if n_missing_score:
        out["score_missing_note"] = (
            f"⚠️ `{score_col}` 有 {n_missing_score}/{len(usable)} 条可判定记录**缺分数**"
            f" ⇒ 本口径只用 {len(scored)} 条。引用本口径的任何结论都只能带这个分母。")
    # 🚨 四分位**组内切**（`(pred_date, direction)`），不全局切：
    #    交付物就是当日那 10 只，分数**只在当天那 10 只里**被用来排序；
    #    全局切会把「哪一天」的效应混进「分数高」的效应里（第 22 层）。
    q = scored.groupby(["pred_date", "direction"], group_keys=False)[score_col].apply(
        _within_group_quartile)
    scored = scored.assign(q=q)
    sub: dict = {}
    for k in sorted(scored["q"].unique()):
        g = scored[scored["q"] == k]
        sub[f"Q{int(k)}"] = {
            "n": int(len(g)),
            "hit_rate": float((g["result"] == "hit").mean()),
        }
    seq = [sub[k]["hit_rate"] for k in sorted(kk for kk in sub if kk.startswith("Q"))]
    # 🚨🚨 **镜像的那一行**（方向写反 ⇒ 判据整个反过来，而它**不崩、不报错**）：
    #    跌腿的 `hit` = 真的跌 ⇒ **分越低越容易中** ⇒ 命中率随 score 升高而**下降**。
    #    判据在 **score 升序**的序列上读（最低分在前）⇒ 必须是**非递增**。
    #    ⚠️ 若直接在 Q 标签（Q1=最高分）上写成 `>=`，就变成「分越高越容易跌」
    #    ⇒ 那是把**发名单的那一端**判反了（而它不崩、不报错，只默默改判决）。
    seq_by_score_asc = list(reversed(seq))   # Q1=最高分 ⇒ 反过来就是 score 升序
    monotone = all(seq_by_score_asc[i] >= seq_by_score_asc[i + 1]
                    for i in range(len(seq_by_score_asc) - 1))
    x = scored[score_col].to_numpy(dtype=float)
    y = (scored["result"] == "hit").to_numpy(dtype=float)
    rho = _point_biserial(x, y)
    stat = lambda u, v: _point_biserial(u, v)
    # 判决臂：按 `pred_date` 分组（与涨腿臂同口径）。
    p, q95 = _permutation_p(x, y, _groups_by(scored, "pred_date"), stat,
                            n_perm, seed)
    if "entry_date" in scored.columns:
        g_ctl = _groups_by(scored, "entry_date")
        p_ctl, q95_ctl = _permutation_p(x, y, g_ctl, stat, n_perm, seed)
        ctl = {
            "role": "control_only_not_the_verdict_arm",
            "available": True,
            "group_by": "entry_date（独立**成交窗口**；判决臂按 pred_date）",
            "n_groups": len(g_ctl),
            "n_rows_unassigned": int(len(scored) - sum(len(g) for g in g_ctl)),
            "permutation_p_two_sided": p_ctl,
            "null_abs_rho_q95_power_floor": q95_ctl,
            "detectable_at_alpha05": bool(abs(rho) >= q95_ctl),
            "q95_over_verdict_arm_q95": (q95_ctl / q95) if q95 else None,
            "note": (
                "分组变粗 ⇒ 组内置换自由度**大体**更大 ⇒ `q95` 更高 ⇒ "
                "更容易判 `POWERLESS`。⚠️ **但这不是定律**：方向由组大小分布"
                "决定，不是组数的单调函数（涨腿臂实测两臂比值方向相反）"
                "⇒ 读这一个数要看同臂的 `q95_over_verdict_arm_q95`，"
                "不许当成单向保证。"),
        }
    else:
        ctl = {
            "role": "control_only_not_the_verdict_arm",
            "available": False,
            "unavailable_reason": (
                "本臂没有 `entry_date` 列 ⇒ 独立成交窗口数无法计算。"
                "**显式缺失，不是回落**：判决臂不受影响，"
                "而产物里不许凭空补一个与真相同形的数字。"),
        }
    sub["_stats"] = {
        "hit_rate_sequence_q1_to_q4": seq,
        # 🚨 判据真正读的那条序列：**score 升序**（最低分在前）⇒ 方向不用猜。
        "hit_rate_sequence_by_score_ascending": seq_by_score_asc,
        # 🚨 两个方向字段**都在**产物里，且**名字自带方向**：
        #    `monotone_decreasing_in_score`（含主语）＋ `monotone_direction`（枚举值）。
        "monotone_decreasing_in_score": bool(monotone),
        "monotone_direction": MONOTONE_DIRECTION,
        "monotone_test": MONOTONE_TEST,
        "point_biserial_rho": rho,
        # 跌腿的 ρ **期望为负**（分越高 ⇒ 越不容易跌）⇒ 与涨腿臂**符号相反**。
        "expected_rho_sign": "negative",
        "permutation_p_two_sided": p,
        "null_abs_rho_q95_power_floor": q95,
        "detectable_at_alpha05": bool(abs(rho) >= q95),
        "control_arm_entry_date": ctl,
    }
    out["quartiles"] = sub
    return out


def up_arm_cross_reference(df: pd.DataFrame, score_col: str = GATE_SCORE_COL) -> dict:
    """涨腿臂的**只读**对照：让本文件的读者不必开第二个文件就能看到两半。

    🚨 **只报分位序列与两个方向的单调标志，不报判决、ρ、置换 p、检出力下限。**
       涨腿那些读数的**权威**位置是 `data/reports/score_ranking_power.json`
       （那才是涨腿的闸门产物）⇒ 在这里重算一遍就是**制造第二份真相源**
       （第 97 层），而两份副本之间没有任何守卫。
    """
    up = df[(df["direction"] == "up") & df["result"].isin(GATE_JUDGEABLE)
            & df[score_col].notna()].copy()
    if up.empty:
        return {"role": "cross_reference_only_NOT_a_second_source_of_truth",
                "available": False,
                "unavailable_reason": "涨腿臂没有「可判定且有分数」的行。"}
    q = up.groupby(["pred_date", "direction"], group_keys=False)[score_col].apply(
        _within_group_quartile)
    up = up.assign(q=q)
    rates = []
    for k in sorted(up["q"].unique()):
        g = up[up["q"] == k]
        rates.append({"quartile": f"Q{int(k)}", "n": int(len(g)),
                      "hit_rate": float((g["result"] == "hit").mean())})
    seq = [r["hit_rate"] for r in rates]
    by_score_asc = list(reversed(seq))
    return {
        "role": "cross_reference_only_NOT_a_second_source_of_truth",
        "available": True,
        "source_of_truth": (
            "涨腿的判决、ρ、置换 p、检出力下限在 "
            "`data/reports/score_ranking_power.json`（`scripts/score_ranking_power.py`）"
            "⇒ 本块**不重复**它们，只报分位序列与方向标志。"),
        "quartile_hit_rates_q1_to_q4": rates,
        "monotone_increasing_in_score": bool(
            all(by_score_asc[i] <= by_score_asc[i + 1]
                for i in range(len(by_score_asc) - 1))),
        "monotone_decreasing_in_score": bool(
            all(by_score_asc[i] >= by_score_asc[i + 1]
                for i in range(len(by_score_asc) - 1))),
    }


def gate_verdict(stats: dict) -> tuple[str, str]:
    """三态判决，**同名同义**于涨腿臂（`PASS`/`NOT_PASSED_*`）。

    ⚠️ 判据顺序与涨腿臂**一致**：先看单调性，再看检出力。
       `NOT_PASSED_POWERLESS` 的含义是「**这份样本测不到**」，
       **不是**「跌腿没有预测力」（第 16 种形状）。
    """
    if stats["monotone_decreasing_in_score"]:
        return VERDICT_PASS, (
            "跌腿四分位命中率随 score 升高而下降（镜像判据成立）"
            "⇒ 跌腿有排序力；仍须过 S2 的逆大盘条件与「不可做空」的诚实边界")
    kind = (VERDICT_POWERLESS if not stats["detectable_at_alpha05"]
            else VERDICT_MEASURED)
    tail = ("**这份样本连这个量级的关联都分不出来** ⇒ 不许当回避清单发布，先攒前瞻期"
            if not stats["detectable_at_alpha05"]
            else "**检出力足够却仍不满足镜像判据** ⇒ 分数对跌腿没有排序力，要换结构")
    return kind, (
        f"跌腿：命中率未随 score 升高而下降；点二列 ρ={stats['point_biserial_rho']:+.4f}"
        f"（跌腿期望符号为负），置换 p={stats['permutation_p_two_sided']:.4f}，"
        f"零分布 95 分位（检出力下限）="
        f"{stats['null_abs_rho_q95_power_floor']:.4f} ⇒ {tail}")


def gate_cannot_prove(res: dict, kind: str) -> list[str]:
    """产物里那段「本产物**不能**证明什么」（**四条**，预注册 §6）。

    🚨 里面的数**必须**由本次运行算出来（第 73 层：同一事实被复制到多处 ⇒
    写死的那处必然漂移）。
    """
    cal = res["independence_caliber"]
    n_pred = res["n_independent_pred_dates"]
    n_rows = res["n_rows_total"]
    entry_txt = ("**未知**（输入没有 `entry_date` 列）"
                 if cal["n_entry_dates"] is None
                 else f"**{cal['n_entry_dates']}**")
    return [
        f"**不能**把 `{kind}` 读成「跌腿没有预测力」——"
        f"判据臂只有 {n_pred} 个**出单日**，而真实独立成交窗口 {entry_txt} 个"
        f"（出单日≠成交日，见 `independence_caliber`）；"
        "「测不到」与「被证伪」是两件事",
        f"**不能**用条目数 {n_rows} 当独立观测数 —— 它是 {n_pred} 个出单日"
        f"× 每臂 10 只，而那 {n_pred} 个出单日只对应 {entry_txt} 个成交窗口",
        "**不能**把跌腿当成可做空的收益来源 —— 石川《因子投资方法与实践》"
        "`data/books/cn_factor_lines.txt:768` 逐字「不合理考虑做空限制，"
        "也会高估因子的收益。」⇒ 跌腿的价值必须由**信息价值**证明，"
        "而不由可执行性假设（见 `executable` 块）",
        "**本产物不是涨腿的判据臂** —— 涨腿的上线闸门是 "
        "`scripts/score_ranking_power.py`（`GATE_ARM=\"up\"`），"
        "它的产物是 `data/reports/score_ranking_power.json`；"
        "本文件判的是 D1 交付物的**另一半**（每天 10 只跌股）",
    ]


def gate_payload(df: pd.DataFrame, n_perm: int = GATE_N_PERM,
                 seed: int = GATE_SEED) -> dict:
    """闸门产物的完整内容。**编排层单独抽出来** ⇒ 守卫够得着
    （嵌套在 `main()` 里 ⇒ 判决逻辑无任何东西能挡，第 112 层）。"""
    res = gate_measure(df, GATE_SCORE_COL, n_perm, seed)
    st = res["quartiles"]["_stats"]
    kind, why = gate_verdict(st)
    return {
        "prereg": "docs/PREREG_DOWNLEG_GATE_2026-10-20.md",
        "role": (
            "跌腿的**闸门产物**（G4 镜像）。⚠️ **不是涨腿的判据臂** —— "
            "涨腿的闸门是 `scripts/score_ranking_power.py`"
            "（`GATE_ARM=\"up\"`），产物是 `data/reports/score_ranking_power.json`。"
            "本文件判的是 D1 交付物的另一半（每天 10 只跌股）。"),
        "arm": GATE_ARM,
        "score_col": GATE_SCORE_COL,
        "verdict": kind,
        "verdict_reason": why,
        # 🚨 **字段名不许与涨腿臂同名的那个撞**：
        #    涨腿的 `publish_allowed` 意思是「允许作为『**该买**』清单发布」，
        #    跌腿的是「允许作为『**该回避**』清单发布」⇒ 语义不同，名字必须能区分
        #    （第 179 层：键名内建价值判断 ⇒ 键名和内容互相打脸）。
        #    ⇒ 本产物**不含** `publish_allowed` 键。
        "avoidance_list_publish_allowed": kind == VERDICT_PASS,
        "publish_allowed_field_note": (
            "⚠️ 本产物的 `avoidance_list_publish_allowed` **不是**涨腿臂的 "
            "`publish_allowed`：前者是「允许作为『**该回避/该减仓**』清单发布」，"
            "后者是「允许作为『**该买**』清单发布」⇒ 两者语义不同，"
            "**不许混用、也不许互相引用**。"),
        "monotone_direction": MONOTONE_DIRECTION,
        "monotone_direction_means": (
            "🚨 `decreasing` 指的是：**命中率随 `score` 升高而下降**"
            "（score ↑ ⇒ 「命中『它真的跌了』」的概率 ↓）。"
            "它**不是**指「Q 编号的序列递减」—— Q1 是**最高分**（见 "
            "`quartile_label_convention`），所以在 Q 标签上这条规则读作"
            "**非递减**（`Q1 <= Q2 <= Q3 <= Q4`）。"
            "⇒ 🚨 这与涨腿臂 `all(seq[i] <= seq[i+1])` 的**代数形式逐字相同、"
            "含义相反** ⇒ 两边都只读这个字段，**不许靠代码长得像去推断方向**。"),
        "quartile_label_convention": QUARTILE_LABEL_CONVENTION,
        "monotone_test": MONOTONE_TEST,
        "mirror_of_up_arm": (
            "涨腿的 `hit` = 真的**涨**（期望 ρ 为正），跌腿的 `hit` = 真的**跌**"
            "（期望 ρ 为**负**）⇒ 同一件事（排序力）在两个臂上**符号相反** ⇒ "
            "涨腿臂的守卫**不能直接套用**（第 143 层：口径被更正后，"
            "引用它的每一处都没有留下标记）。"),
        # 🚨🚨 **读涨腿臂时发现的一处方向口径不一致**（本轮**只记录、不修改** ——
        #    涨腿臂是判据臂，不许改它一个字）：
        "up_arm_orientation_note": (
            "🚨 涨腿臂 `scripts/score_ranking_power.py` 的判定是 "
            "`all(seq[i] <= seq[i+1])`，`seq` 按 Q1(=最高分)→Q4(=最低分) 排 "
            "⇒ 它判的是「**分越低命中率越高**」；"
            "而 `predict.py:271`（分越高越看涨）+ `verify.py:94-95`（up 的 `hit`=真涨）"
            "推出涨腿的期望是「**分越高命中率越高**」⇒ **两者方向相反**。"
            "⇒ 本产物**不复制**涨腿臂的判据臂，只复用它的**统计实现**（同源）。"
            "⚠️ 今天的**数字**没有因此翻转：涨腿臂当前的 Q1→Q4 序列在**两个方向下"
            "都不单调** ⇒ 判决 `NOT_PASSED_POWERLESS` 在两个口径下**相同**"
            "（实测见 `up_arm_cross_reference` 的两个单调标志）。"
            "🚨 但那是**运气**不是保险：若将来涨腿臂只在一个方向下单调，那枚 PASS "
            "会是一枚**反向的绿灯** ⇒ 已登记为欠账"
            "（`docs/PREREG_DOWNLEG_GATE_2026-10-20.md` §9 第 4 条）。"),
        "criterion": {
            "id": "G4-mirror",
            "source": "docs/PREREG_DOWNLEG_GATE_2026-10-20.md §2",
            "rule": MONOTONE_TEST + "（分段单调即可，非严格）",
            "monotone_direction": MONOTONE_DIRECTION,
            "alpha": 0.05,
            "n_perm": n_perm,
            "seed": seed,
            "p_formula": "(b+1)/(B+1)  —— 不用 b/B，否则零假设下会报 p=0",
            "power_floor": "零分布 95 分位（同一套置换，与涨腿臂同源）",
            "statistic": "点二列相关（score ↔ 命中指示变量）",
            "reused_from": (
                "scripts/score_ranking_power.py 的 `_permutation_p` / "
                "`_point_biserial` / `_within_group_quartile` / `_groups_by`"
                "（**只 import，不改它一个字**）⇒ 同源才有可比性。"),
        },
        "measurement": res,
        "up_arm_cross_reference": up_arm_cross_reference(df, GATE_SCORE_COL),
        "executable": executable_block(),
        "s2_status": {
            "role": "止血决定的现状（本闸门不解除它）",
            "decision": (
                "S2 撤掉逆大盘臂：`docs/TODO_reverse_scoring_2026-10-15.md` §2.1"
                " 记「`down` 臂在逆大盘时命中率 27.3%（p=0.0034）」⇒ 已停用。"),
            "what_was_stopped": (
                "🚨 **停用的是「拿它当发布闸门」这件事，不是「停止出单」** —— "
                "跌腿每天仍在出单（底稿里每天 10 只 `direction=\"down\"`）。"),
            "this_gate_does_not_measure_it": (
                "⚠️ 本闸门测的是**整体**四分位单调性，**不测**「逆大盘时」那个条件 "
                "⇒ **即使 PASS 也不解除 S2**。"),
        },
        "what_this_cannot_prove": gate_cannot_prove(res, kind),
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
    ap = argparse.ArgumentParser(
        description="跌腿：对照臂/诊断（downleg_ranking.json）"
                    " + 跌腿闸门（G4 镜像，downleg_gate.json）")
    ap.add_argument("--verification", type=Path,
                    default=ROOT / "data" / "reports" / "verification.parquet")
    ap.add_argument("--out", type=Path,
                    default=ROOT / "data" / "reports" / "downleg_ranking.json")
    # 🚨 闸门产物是**新文件**，**不覆盖**对照臂那份（那个是诊断，不是判决）。
    ap.add_argument("--gate-out", type=Path,
                    default=ROOT / "data" / "reports" / GATE_ART_NAME)
    ap.add_argument(
        "--reports-dir", type=Path, default=None,
        help="生产报告目录（用于从**生产的真实配置**算当前信号 id）。"
             "默认取 --verification 的父目录。")
    args = ap.parse_args(argv)

    # 🚨🚨 **信号作用域**（预注册 §2 / §3）。
    #   底稿是**追加式**的 ⇒ D1 的信号定义一变，同一本底稿就装着**两个信号**的行。
    #   ⇒ 跌腿的对照臂与**闸门**都只问**当前信号**的行。
    reports_dir = Path(args.reports_dir) if args.reports_dir else \
        Path(args.verification).parent
    ctx = resolve_attribution_context(reports_dir)
    # 🚨 **读口留在调用点**（既有守卫钉住：expect_bytes 必须抓在它之前）。
    frame = pd.read_parquet(args.verification)
    # 🚨 **过滤只走共享入口**（第 97 层）。
    scoped = scope_ledger(frame, ctx.signal_id, ctx.weights_fingerprint)

    if not scoped.has_evidence:
        # 🚨 作用域不对 ⇒ **不许**判「跌腿有没有排序力」（那判的是另一个信号）
        #   ⇒ 两份产物都写**不可判定**。
        scope = scope_block(scoped)
        out = {
            "role": (
                "⚠️ **对照臂 / 诊断，不是判决臂。** "
                "涨腿的判决臂是 `scripts/score_ranking_power.py`"
                "（`GATE_ARM=\"up\"`），本文件不改它一字。"),
            "verdict_about_downleg": (
                f"{VERDICT_NOT_DECIDABLE}：{scoped.reason}"),
            "signal_scope": scope,
            "attribution": attribution_block(scoped),
            "what_this_cannot_prove": [
                "🚨 **本产物没有对当前信号做任何测量。**",
                f"**不能**把 {VERDICT_NOT_DECIDABLE} 读成「跌腿没有排序力」——"
                "它说的是「**这份证据不属于当前信号**」。",
            ],
        }
        gate = {
            "prereg": "docs/PREREG_DOWNLEG_GATE_2026-10-20.md",
            "role": "跌腿的**闸门产物**（G4 镜像）",
            "arm": GATE_ARM,
            "score_col": GATE_SCORE_COL,
            "verdict": VERDICT_NOT_DECIDABLE,
            "verdict_reason": scoped.reason,
            # 🚨 字段名与涨腿臂**语义不同**（见下方 `publish_allowed_field_note`）。
            "avoidance_list_publish_allowed": False,
            "publish_allowed_field_note": (
                "⚠️ 本产物的 `avoidance_list_publish_allowed` **不是**涨腿臂的 "
                "`publish_allowed`：前者是「允许作为『**该回避/该减仓**』清单发布」，"
                "后者是「允许作为『**该买**』清单发布」⇒ 两者语义不同，"
                "**不许混用、也不许互相引用**。"),
            "monotone_direction": MONOTONE_DIRECTION,
            "signal_scope": scope,
            "attribution": attribution_block(scoped),
            "what_this_cannot_prove": [
                "🚨 **本产物没有对当前信号做任何测量。**",
                f"**不能**把 {VERDICT_NOT_DECIDABLE} 读成「跌腿没有排序力」。",
            ],
        }
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(out, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        gate_out = Path(args.gate_out)
        gate_out.parent.mkdir(parents=True, exist_ok=True)
        gate_out.write_text(json.dumps(gate, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        provenance.stamp(
            args.out, GENERATOR, root=ROOT, expect_bytes=expect_bytes,
            extra={"source_parquet_sha256": hashlib.sha256(
                args.verification.read_bytes()).hexdigest(),
                "source_parquet_path": str(args.verification),
                "verdict": VERDICT_NOT_DECIDABLE,
                "signal_scope_status": scoped.status,
                "signal_id": scoped.current_signal_id,
                "role": "CONTROL_ARM_NOT_THE_VERDICT_ARM"})
        provenance.stamp(
            gate_out, GENERATOR, root=ROOT, expect_bytes=expect_bytes,
            extra={"source_parquet_sha256": hashlib.sha256(
                args.verification.read_bytes()).hexdigest(),
                "source_parquet_path": str(args.verification),
                "verdict": VERDICT_NOT_DECIDABLE,
                "signal_scope_status": scoped.status,
                "signal_id": scoped.current_signal_id,
                "role": "DOWNLEG_GATE_ARM",
                "avoidance_list_publish_allowed": False,
                "monotone_direction": MONOTONE_DIRECTION,
                "prereg": gate["prereg"]})
        print(not_decidable_note(scoped))
        print(f"→ {args.out}")
        print(f"→ {gate_out}")
        return 0

    # 🚨 **下游所有读 `frame` 的地方都必须是作用域内的那一段**
    #   （`overlap_check` 与 `gate_payload` 都吃 `frame` ⇒ 不 rebind 就是漏过滤）。
    frame = scoped.frame
    leg = down_leg_rows(frame)
    if leg.empty:
        raise SystemExit("跌腿剔完不可判定行之后为空 —— 不许出判决")

    out = measure(leg)
    if out["excess_arm"]["stats"] is None:
        raise SystemExit(
            "跌腿超额臂剔完 NaN 之后没有任何成交窗口 —— 不许出判决")
    out["role"] = (
        "⚠️ **对照臂 / 诊断，不是判决臂。** "
        "涨腿的判决臂是 `scripts/score_ranking_power.py`（GATE_ARM=\"up\"），"
        "本文件不改它一字；跌腿自己的闸门是**另一个产物** "
        f"`data/reports/{GATE_ART_NAME}`（同脚本写出，G4 镜像，"
        "预注册 `docs/PREREG_DOWNLEG_GATE_2026-10-20.md`）。"
        "这里判的是 D1 交付物的**另一半**（每天 10 只跌股），此前从未被测过。")
    # 🚨 **作用域随数字一起离开产物**（预注册 §2.3）。
    out["signal_scope"] = scope_block(scoped)
    out["attribution"] = attribution_block(scoped)
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

    # ── 跌腿闸门（G4 镜像） ─────────────────────────────────────────
    # 🚨 **另一个产物、另一个文件** ⇒ 不覆盖上面的对照臂。
    gate = gate_payload(frame, GATE_N_PERM, GATE_SEED)
    # 🚨 **作用域随数字一起离开产物**（预注册 §2.3）。
    gate["signal_scope"] = scope_block(scoped)
    gate["attribution"] = attribution_block(scoped)
    gate_out = Path(args.gate_out)
    gate_out.parent.mkdir(parents=True, exist_ok=True)
    gate_out.write_text(json.dumps(gate, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    provenance.stamp(
        gate_out, GENERATOR, root=ROOT, expect_bytes=expect_bytes,
        extra={
            "source_parquet_sha256": hashlib.sha256(
                args.verification.read_bytes()).hexdigest(),
            "source_parquet_path": str(args.verification),
            "data_fp_is_NOT_my_input": (
                "章里的 data_fp 是 master/daily 的目录指纹，本脚本没读它。"
                "判断输入有没有变，只认 source_parquet_sha256。"),
            "role": "DOWNLEG_GATE_ARM",
            "verdict": gate["verdict"],
            "avoidance_list_publish_allowed":
                gate["avoidance_list_publish_allowed"],
            "monotone_direction": gate["monotone_direction"],
            "prereg": gate["prereg"],
        })
    print(f"跌腿窗口 n={out['ranking_arm']['stats']['n']} "
          f"超额判决={out['excess_arm']['verdict']} "
          f"排序力判决={out['ranking_arm']['verdict']}")
    print(f"→ {out['verdict_about_downleg']}")
    print(f"→ {args.out}")
    gq = gate["measurement"]["quartiles"]
    gseq = " | ".join(f"{k} {gq[k]['hit_rate']:.1%}(n={gq[k]['n']})"
                      for k in sorted(k for k in gq if k.startswith("Q")))
    print(f"\n跌腿闸门（G4 镜像，方向={gate['monotone_direction']}）: {gseq}")
    print(f"  判决: {gate['verdict']}")
    print(f"  {gate['verdict_reason']}")
    print(f"  允许作为『该回避』清单发布: "
          f"{gate['avoidance_list_publish_allowed']}")
    print(f"→ {gate_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

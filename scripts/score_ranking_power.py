#!/usr/bin/env python
"""S3 / G4：`score` 四分位命中率**是否单调递增**。

## 这个脚本答的是「能不能发布」，不是「赚不赚钱」

`docs/TODO_reverse_scoring_2026-10-15.md` §3 的主判据 G4 是**上线闸门**：

| 编号 | 判据 | 阈值 | 来源 |
|---|---|---|---|
| **G4** | `score` 四分位命中率 | **单调递增** | S3 |

原文：**「过不了这一关就不许发布」，与收益高低无关。**
⇒ 所以本脚本的判决是 `PASS` / `NOT_PASSED` 两态，
**不是**「有排序力」/「没有排序力」两态 —— 见下面「三态」一节。

## 为什么必须把**独立事件数**和**条目数**分开报（第 80 层）

🚨 **本节原文的两个数已作废**（写于 2026-10-16 实测之前）：
它写「`verification.parquet` 有 **80 行**」「**4 个预测日**」，
而底稿是**追加式**的，实测已走到 **120 行 / 6 个 `pred_date`**。
**原样保留在下面（不抹掉，第 25 层）** —— 但它对不上任何产物，
这正是它自己警告的形状：**散文字数与正文脱钩，且无守卫**。
⚠️ 更贵的是它的**结论方向**：它写「独立事件是 4 个**交易日**」，
而它数的其实是 `pred_date` —— **出单日 ≠ 成交日**，见下一节。

`verification.parquet` 有 80 行，但它是

    4 个预测日 × 2 个臂 × 每日 10 只

⇒ **独立事件是 4 个交易日，不是 80 条观测。**
把 80 当 n 会把检出力虚报 20 倍。

## 🚨「出单日」与「成交日」是两个量，而独立单元是后者（2026-10-16 实测）

上面那句「4 个交易日」当年数的是 `pred_date` —— **它是出单日**。
实测 `pred_date → entry_date` 映射：

    pred 2026-10-09(五) ┐
         2026-10-10(六) ├─→ entry 2026-10-12 / exit 2026-10-13
         2026-10-11(日) ┘   ← 三张名单押**同一个**成交窗口
         2026-10-12 → entry 10-13    2026-10-13 → entry 10-14    2026-10-14 → entry 10-15

（`10-10` / `10-11` 实测**不在主库交易日历内**。）
⇒ **6 个 `pred_date` 只对应 4 个独立成交窗口** ⇒
「一天一个独立事件」这句话**对周末重影不成立**。

🚨🚨 **「分组越粗 ⇒ `q95` 越高 ⇒ 更保守」这句话本身被实测打回了**（2026-10-17）。
方向由**组大小的具体分布**决定，**不是组数的单调函数** ——
两臂的 `q95` 比值**方向相反**，下表读的是产物字段（`data/reports/score_ranking_power.json`
:: `arms.<arm>._stats.{null_abs_rho_q95_power_floor, control_arm_entry_date.*}`）：

| 臂 | 判据臂（`pred_date`）q95 | 对照臂（`entry_date`）q95 | `q95_over_verdict_arm_q95` | 判决 |
|---|---|---|---|---|
| **`up`（闸门臂）** | **0.2347996969293442** | **0.25074433865530515** | **1.0679074203863177** | 两边都 `detectable=False` |
| `down`（并列报告） | 0.06898822396247543 | 0.0686509164413614 | **0.9951106507496481** | 两边都 `detectable=False` |

⇒ **`up` 臂更保守（+6.79%），`down` 臂反而略低（−0.49%）** ——
**方向相反** ⇒ 「按 `pred_date` 分组一定偏乐观」这句话**不成立**，
不许写成定律（第 16 层：两点观测被写成保证）。
⇒ **不变的那半句**：两条臂、两种分组，判决**都不翻**。
⚠️ 幅度只有几个百分点而非数量级，因为置换自由度按 `log(n!)` 走：
`log(29!)≈71.3` vs `3×log(10!)≈62.6`，只多 **14%**。

⇒ 所以本脚本**保留 `pred_date` 作判据臂**（预注册的 G4 口径一个字不改，第 91 层），
**另加一条 `entry_date` 对照臂**并显式标 `role`（第 183 层）。
代价不是高估能力，而是**「攒够还差多久」的 ETA 用了一个偏松的数**。

## 分位怎么切：组内切，不全局切

每天每臂就是 10 只，分数**只在当天那 10 只里被用来排序**（交付物就是当日 10 只），
所以四分位必须在 `(pred_date, direction)` **组内**切。
全局切会把「哪一天」的效应混进「分数高」的效应里（第 22 层）。

## 三态：为什么不能只报「单调 / 不单调」

G4 是**必要条件**。不满足它有三种完全不同的原因，而它们的行动含义相反：

| 情形 | 含义 | 该做什么 |
|---|---|---|
| `PASS` | 四分位单调递增 | 允许进入发布评估（还要过 G1/G2/G3/G5/G6） |
| `NOT_PASSED_POWERLESS` | 不单调，且**检出力不足以分辨任何效应** | **不许发布**；先把前瞻期攒够 |
| `NOT_PASSED_MEASURED` | 不单调，且检出力**足够**分辨 | 分数本身没排序力 ⇒ 要换结构（S1） |

⇒ 「不单调」在 n=4 个独立日上**几乎必然**出现，它**不是**「证伪」。
把 `NOT_PASSED_POWERLESS` 写成「打分没有排序力」就是我上一轮差点犯的错
（§0 里那句「`adjusted_score` 四分位无单调性」正是这么来的，
而且 `adjusted_score` 只有 20/80 非 NaN）。本脚本两个口径都报。

## 置换检验：零假设是「分数与命中无关，但保留日期结构」

置换在 `(pred_date, direction)` **组内**做 ⇒ 日期效应与方向效应都被保留，
被打乱的只有「分数 ↔ 命中」的配对关系。
统计量用**点二列相关**（score 与 hit 指示变量），它比四分位表更稳
（四分位在 n=35 上只有 4 个点，Spearman 的自由度低到没有意义）。

p 值用 Phipson–Smyth 的 `(b+1)/(B+1)`，不是 `b/B` ——
后者在「零假设下无一次超界」时会报 **p = 0**，而 p=0 不是概率。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aplus import provenance  # noqa: E402
# 🚨 **信号作用域的唯一入口**（预注册 `docs/PREREG_SIGNAL_SCOPING_2026-10-21.md`）。
#   本脚本**不自己实现**过滤逻辑（第 97 层：两份副本之间没有守卫），
#   也不自己实现指纹（那会变成第二份真相源）。
from aplus.signal_scope import (  # noqa: E402
    VERDICT_NOT_DECIDABLE,
    not_decidable_note,
    resolve_attribution_context,
    attribution_block,
    scope_block,
    scope_ledger,
)

ART_NAME = "score_ranking_power.json"
#: 默认的判据字段 —— G4 原文写的是 `score`，**不是** `adjusted_score`。
DEFAULT_SCORE_COL = "score"
#: G4 的阈值形状：**单调递增**（`q_{i+1} >= q_i` 全成立），**不写死效应量**。
MONOTONE_NONDECREASING = True
#: 判据里「可判定」的定义：`hit`/`miss` 是判决，`no_data` 是**未知**、
#: `flat` 是**无波动** —— 两者都不是「未命中」，混进分母会凭空造出命中率。
JUDGEABLE = ("hit", "miss")
N_PERM = 20_000
SEED = 42


def _within_group_quartile(g: pd.Series) -> pd.Series:
    """组内四分位（1 = 分数最高）。n<4 时退化成按名次均分，绝不返回 NaN。"""
    n = len(g)
    ranks = g.rank(ascending=False, method="first")
    return ((ranks - 1) * 4 // n + 1).astype("int64")


def _point_biserial(x: np.ndarray, y: np.ndarray) -> float:
    """score 与 0/1 命中的点二列相关。任一侧常量 ⇒ 0.0（无关联可测，不是崩）。"""
    if len(x) < 3 or np.all(y == y[0]) or np.all(x == x[0]):
        return 0.0
    xs = (x - x.mean()) / (x.std() or 1.0)
    ys = (y - y.mean()) / (y.std() or 1.0)
    return float(np.mean(xs * ys))


def _permutation_p(values: np.ndarray, hits: np.ndarray,
                   groups: list[np.ndarray], stat, n_perm: int,
                   seed: int) -> tuple[float, float]:
    """组内置换 p 值 + 零分布的 95 分位（= **检出力下限**）。

    返回 `(p_two_sided, rho_null_abs_q95)`。
    `rho_null_abs_q95` 的含义：真实 |相关| 小于它 ⇒ **这份样本分不出它**。
    ⚠️ 这就是「NOT_PASSED_POWERLESS」的判据来源，不是拍脑袋的「n 太小」。
    """
    rng = np.random.default_rng(seed)
    obs = abs(stat(values, hits))
    null = np.empty(n_perm)
    for i in range(n_perm):
        perm = values.copy()
        for idx in groups:
            perm[idx] = rng.permutation(values[idx])
        null[i] = abs(stat(perm, hits))
    # Phipson & Smyth：p = (b+1)/(B+1)，**不是 b/B**
    p = float((int((null >= obs).sum()) + 1) / (n_perm + 1))
    return p, float(np.quantile(null, 0.95))


def _groups_by(a: pd.DataFrame, col: str) -> list[np.ndarray]:
    """把某列的取值变成置换分组（`col` 相同的行必须在同一组内）。

    抽成函数是为了让**分组口径**成为**唯一真相源**：判据臂与对照臂调同一个它，
    否则两份副本之间没有守卫（第 97 层的加强版 —— 两处各写一遍 `groupby(...).indices`）。
    """
    return [np.asarray(idx) for idx in a.groupby(col).indices.values()]


def _independence_caliber(df: pd.DataFrame) -> dict:
    """**判据臂的独立单元（`pred_date`）与真实独立成交窗口（`entry_date`）的对照**。

    🚨 **为什么这两个不是一个量**：`pred_date` 是**出单日**，`entry_date` 是**成交日**。
    周末 / 节假日照常出单，而那些单子全部押**同一个**下一个交易日
    ⇒ 多个 `pred_date` 映射到同一 `entry_date` 时，**它们不是独立事件**。

    ⇒ 实测 6 个 `pred_date` 只对应 **4** 个独立成交窗口。
    ⇒ 🚨 **误差方向不能当定律**：分组变粗 ⇒ 组内置换自由度**大体**变大
      ⇒ `q95` 变高，**但方向由组大小的具体分布决定，不是组数的单调函数**
      （实测 `up` 臂比值 **1.0679**、`down` 臂 **0.9951**，**方向相反**）。
      ⇒ 因此只报**实测比值**（`q95_over_verdict_arm_q95`），
      **不许**写「按 `pred_date` 分组不会高估能力」这种无条件的话。

    ⚠️ `entry_date` 缺失时**显式返回 `None` 并说明**，绝不回落成 `pred_date`
    （回落 = 造一个与真相同形、看不出差别的数，第 6 种形状）。
    """
    n_pred = int(df["pred_date"].nunique())
    if "entry_date" not in df.columns:
        return {"verdict_arm_unit": "pred_date（判决臂，预注册 G4 口径）",
                "control_arm_unit": "entry_date（**对照臂，不是判决臂**）",
                "n_pred_dates": n_pred, "n_entry_dates": None,
                "pred_to_entry_dates": None,
                "n_entry_dates_claimed_by_several_pred_dates": None,
                "unavailable_reason": (
                    "输入没有 `entry_date` 列 ⇒ 独立成交窗口数无法计算。"
                    "**这是显式缺失，不是回落** —— 判决臂不受影响，"
                    "但产物里不许凭空补一个数字。"),
                "note": "缺失 `entry_date` ⇒ 对照臂不可算；判决臂照旧。"}
    pred_to_entry = {
        str(p): sorted({str(e) for e in df.loc[df["pred_date"] == p, "entry_date"]
                        .dropna().tolist()})
        for p in sorted(df["pred_date"].dropna().unique())}
    claimed: dict = {}
    for es in pred_to_entry.values():
        for e in es:
            claimed[e] = claimed.get(e, 0) + 1
    return {
        "verdict_arm_unit": "pred_date（判决臂，预注册 G4 口径，一个字未改）",
        "control_arm_unit": "entry_date（**对照臂，不是判决臂**：更接近「独立成交窗口」的真身）",
        "n_pred_dates": n_pred,
        "n_entry_dates": int(df["entry_date"].nunique()),
        "pred_to_entry_dates": pred_to_entry,
        "n_entry_dates_claimed_by_several_pred_dates":
            int(sum(1 for c in claimed.values() if c > 1)),
        "note": (
            "⚠️ 同一个 `entry_date` 出现在多个 `pred_date` 下 ⇒ 那些天的名单"
            "押的是**同一个成交窗口**，按 `pred_date` 数会**虚高独立事件数**。"
            "误差方向**不是定律**：分组变粗 ⇒ 置换自由度**大体**更大 ⇒ `q95` 更高，"
            "**但方向由组大小分布决定，不是组数的单调函数**（实测两臂比值方向相反）"
            "⇒ 逐臂实测比值见 `_stats.control_arm_entry_date.q95_over_verdict_arm_q95`。"),
    }


def measure(df: pd.DataFrame, score_col: str, n_perm: int = N_PERM,
            seed: int = SEED) -> dict:
    """一个 score 列上的完整测量。**两个口径一起报**（`score` 与 `adjusted_score`）。

    `n_perm` / `seed` 是参数而非常量，因为**对账守卫需要用同一组参数重算一遍**
    （第 118 层：读落盘 json 等于没问）。写死的话守卫只能对着快照做自洽检查。
    """
    usable = df[df["result"].isin(JUDGEABLE)].copy()
    # ⚠️ 缺分数的行**不许静默跳过**（第 112 层）：跳过后「6/8 可测」会变成
    #   「8/8 全部一致」，那是**另一个量**。这里把剔除量显式记进产物。
    n_missing_score = int(usable[score_col].isna().sum())
    scored = usable[usable[score_col].notna()].copy()
    out: dict = {
        "score_col": score_col,
        "n_rows_total": int(len(df)),
        "n_rows_judgeable": int(len(usable)),
        "n_rows_dropped_undecidable": int(len(df) - len(usable)),
        "dropped_breakdown": {k: int(v) for k, v in
                              df.loc[~df["result"].isin(JUDGEABLE), "result"]
                              .value_counts().items()},
        "n_rows_score_missing": n_missing_score,
        "n_rows_scored": int(len(scored)),
        "n_independent_pred_dates": int(df["pred_date"].nunique()),
        # 🚨 **这里原来逐字写着「pred_date（一天一个独立事件）」** ——
        # 而 10-10(六) / 10-11(日) 两张名单与 10-09(五) **押同一个成交窗口** ⇒
        # 那句话对周末重影**不成立**，而它被当外部锚用（第 22 层的散文化石）。
        # 现在它**不再断言独立性**，只指向口径块与对照臂。
        "independent_unit": (
            "⚠️ 判决臂按 **pred_date** 分组，而 `pred_date` 是**出单日**不是**成交日**："
            "多个 `pred_date` 可映射到同一 `entry_date`（周末/节假日出单），"
            "此时它们**不是独立事件**。独立成交窗口数与逐条映射见 "
            "`independence_caliber`。**不变的那半句**：条目数不是独立观测数。"),
        "independence_caliber": _independence_caliber(df),
        "quartile_rule": "组内（pred_date × direction）按 score 名次切 4 段",
        # ⚠️ 「四分位」这个词会让人默认「等分四份」——**实测不是**。
        # n=10（生产组大小）切出 **3/2/3/2**；只有 n 能被 4 整除才均衡
        # （n=4/8/16/20 才是 1/1/1/1、2/2/2/2、4/4/4/4、5/5/5/5）。
        # ⇒ 各段 `n` 差一倍，命中率是**小样本比率**；引用任何一段都必须带它的 `n`。
        # （守卫 `tests/test_score_ranking_power.py` 第一版断言「均分」，
        #   当场红——那是我**预测**的性质，不是量出来的。这是第 98 层。）
        "quartile_sizes_at_n10": {"Q1": 3, "Q2": 2, "Q3": 3, "Q4": 2},
        "quartile_sizes_note": (
            "四分位按名次取整分段，**段大小不均**（n=10 → 3/2/3/2）。"
            "这是「组内排名分段的必然」而不是缺陷：只有 n % 4 == 0 才均衡。"
            "⇒ 各段命中率的样本量不同，**跨段比较必须读 n**；"
            "G4 判的是**单调性**而非段间差值，故不受段大小影响。"),
    }
    if n_missing_score:
        out["score_missing_note"] = (
            f"⚠️ `{score_col}` 有 {n_missing_score}/{len(usable)} 条可判定记录**缺分数**"
            f" ⇒ 本口径只用 {len(scored)} 条。引用本口径的任何结论都只能带这个分母。")
    arms = {}
    for arm, a in scored.groupby("direction"):
        q = a.groupby("pred_date", group_keys=False)[score_col].apply(
            _within_group_quartile)
        a = a.assign(q=q)
        sub = {}
        for k in sorted(a["q"].unique()):
            g = a[a["q"] == k]
            sub[f"Q{int(k)}"] = {
                "n": int(len(g)),
                "hit_rate": float((g["result"] == "hit").mean()),
            }
        seq = [sub[k]["hit_rate"] for k in sorted(kk for kk in sub if kk.startswith("Q"))]
        # 🚨🚨 **2026-10-20 方向修正（P0）** —— 这行的不等号原来写反了。
        #
        # **分位约定**：`_within_group_quartile` 的 docstring 逐字「组内四分位
        # （1 = 分数最高）」⇒ **Q1 = 分数最高**，Q4 = 分数最低。
        # ⇒ `seq` 的下标是**分位标签**（Q1→Q4），不是分数的大小。
        #
        # **预注册原文**（docs/TODO_reverse_scoring_2026-10-15.md §3 第 128 行）：
        # 「| **G4** | `score` 四分位命中率 | **单调递增** | S3 |」
        # 同文件第 59 行：「`score 四分位 → 命中率单调递增` 的证据（分段单调即可）」。
        # ⇒ 「命中率随 score 递增」在 Q 标签上的代数形式是
        #    **hit_rate(Q1) >= hit_rate(Q2) >= hit_rate(Q3) >= hit_rate(Q4)**
        # ⇒ 原实现写的 `all(seq[i] <= seq[i+1])` 判的其实是
        #    「**命中率随 score 递减**」⇒ **与预注册相反**。
        #
        # **为什么是 P0（失效方向）**：
        # ① 一个**真的**有排序力的打分器 ⇒ 命中率随 score 递增 ⇒
        #    在原实现下**判为不单调** ⇒ **拦住目标信号**；
        # ② 一个**反预测**的打分器 ⇒ 在原实现下**判为单调** ⇒ **放行错误信号**。
        # ⇒ 这枚闸门判的是**打分器的反向**。
        #
        # **今天判决为什么没翻转**：两个方向下涨腿臂的序列**都不单调**
        # （Q1 0.7083 / Q2 0.3750 / Q3 0.4091 / Q4 0.6250）⇒ 判决仍是
        # `NOT_PASSED_POWERLESS`。**那是数据恰好如此，不是判据正确**
        # ⇒ 与 `tests/test_score_ranking_power.py` 里「monotone 全是夹具注入的
        # 字段、真实测量一次没被调用」是同一族（第 120 层）。
        #
        # **修法 = 让代码回到预注册的原文**（判据是文档先定的，§3 第 128 行一字未动
        # ⇒ 这不是「改判据」，是「修一个与预注册相反的实现」）。
        # **方向必须显式落进产物**（`monotone_direction`），读者不许靠
        # 「代码长得像」推断方向 —— 这次的方向错误就是靠推断出来的。
        monotone = all(seq[i] >= seq[i + 1] for i in range(len(seq) - 1))
        x = a[score_col].to_numpy(dtype=float)
        y = (a["result"] == "hit").to_numpy(dtype=float)
        rho = _point_biserial(x, y)
        stat = lambda u, v: _point_biserial(u, v)
        # 🚨 **加对照臂，不换判据臂**（第 183 层）：
        # 判决臂（按 `pred_date` 分组）的字段名与取值路径**一个字不动**
        # （`monotone_nondecreasing` / `permutation_p_two_sided` /
        #   `null_abs_rho_q95_power_floor` / `detectable_at_alpha05`）
        # —— `verdict()` 只读这四个。`entry_date` 那条是**另报**的对照臂，
        # `role` 显式写明它不参与判决。
        p, q95 = _permutation_p(x, y, _groups_by(a, "pred_date"), stat,
                                n_perm, seed)
        if "entry_date" in a.columns:
            g_ctl = _groups_by(a, "entry_date")
            p_ctl, q95_ctl = _permutation_p(x, y, g_ctl, stat, n_perm, seed)
            ctl = {
                "role": "control_only_not_the_verdict_arm",
                "available": True,
                "group_by": "entry_date（独立**成交窗口**；判决臂按 pred_date）",
                "n_groups": len(g_ctl),
                # 组外行不会被置换 ⇒ 它们的分数原样进统计量。
                # 实测生产上为 0（judgeable 行 `entry_date` 无 NaN），但**报出来**
                # 而不是假设 —— 假设就是第 98 层。
                "n_rows_unassigned": int(len(a) - sum(len(g) for g in g_ctl)),
                "permutation_p_two_sided": p_ctl,
                "null_abs_rho_q95_power_floor": q95_ctl,
                "detectable_at_alpha05": bool(abs(rho) >= q95_ctl),
                "q95_over_verdict_arm_q95": (q95_ctl / q95) if q95 else None,
                "note": (
                    "分组变粗 ⇒ 组内置换自由度**大体**更大 ⇒ `q95` 更高 ⇒ "
                    "更容易判 `POWERLESS`。⚠️ **但这不是定律**：方向由组大小分布"
                    "决定，不是组数的单调函数（实测两臂比值方向相反：`up` 1.0679 "
                    "/ `down` 0.9951）⇒ 读这一个数要看同臂的 "
                    "`q95_over_verdict_arm_q95`，不许当成单向保证。"),
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
            "monotone_nondecreasing": bool(monotone),
            # 🚨 **方向必须显式落进产物**（2026-10-20）。判据的**名字**
            # （`monotone_nondecreasing`）不带方向 ⇒ 读者只能靠 `seq` 的下标约定
            # 推断，而那个约定（Q1 = 分数最高）写在**另一个函数**的 docstring 里
            # ⇒ **推断链跨了两个文件** ⇒ 这就是本次方向错误的成因。
            # ⇒ 从今天起方向是产物的**字段**，不是读者的推断题。
            "quartile_label_convention": "Q1 = 分数最高（组内按 score 名次降序），Q4 = 分数最低",
            "monotone_direction": "increasing_with_score",
            "monotone_direction_meaning": (
                "命中率随 **score 递增**（预注册 G4 原文「单调递增」）；"
                "等价于 hit_rate 随 **Q 标签**（Q1=分数最高）**非递增**。"
                "⚠️ 这两个表述指同一条不等式链，但只有前者能说明它测的是哪个方向。"),
            "monotone_test_formula": (
                "hit_rate(Q1) >= hit_rate(Q2) >= hit_rate(Q3) >= hit_rate(Q4)"),
            "expected_rho_sign": "positive",
            "point_biserial_rho": rho,
            "permutation_p_two_sided": p,
            "null_abs_rho_q95_power_floor": q95,
            "detectable_at_alpha05": bool(abs(rho) >= q95),
            "control_arm_entry_date": ctl,
        }
        arms[str(arm)] = sub
    out["arms"] = arms
    out["criterion"] = {
        "id": "G4",
        "source": "docs/TODO_reverse_scoring_2026-10-15.md §3（S3）",
        "rule": "score 四分位命中率单调递增（分段单调即可）",
        "alpha": 0.05,
        "n_perm": n_perm,
        "seed": seed,
        "p_formula": "(b+1)/(B+1)  —— 不用 b/B，否则零假设下会报 p=0",
    }
    return out


#: S2 的止血决定（`docs/TODO_reverse_scoring_2026-10-15.md` §2.1）：
#: `down` 臂在逆大盘时命中率 27.3%（p=0.0034）⇒ **已停用**。
#: ⇒ **发布闸门只看 `up` 臂**。把这个写死，是因为「按字典序取第一个失败的臂」
#: 会让判决理由落在一个**已经停用**的臂上 —— 症状一样，含义完全不同。
GATE_ARM = "up"


#: 🚨 **D1 的验收门槛：独立成交窗口 W ≥ 10**。
#:
#: 出处（**预注册**，不是本脚本自定）：`docs/DELIVERY_DECISIONS_2026-10-18.md`
#: **§4 决策四**「D1 设验收门槛 —— 独立成交窗口 W ≥ 10」
#: ⇒ 「W < 10 时**不许报命中率**、不许给 CI」。
#:
#: ⚠️ **这个门槛此前从未被执行过**（2026-10-21 实测）：
#: `verdict()` 只判「四分位单调递增」⇒ ⇒ **一行底稿天然单调**
#: ⇒ ⇒ 判决 **PASS**、`publish_allowed = True`
#: ⇒ ⇒ **一行就能把 D1 验收判绿**。
#: ⇒ ⇒ 而 2026-10-21 晚间归属基准从 `weights_hash` 切到 `signal_id`
#: ⇒ ⇒ **底稿里就只剩今晚那一份匹配** ⇒ ⇒ ⇒ ⇒
#: **明晚 21:30 那个 PASS 会真的出现在屏上。**
#:
#: ⇒ 守卫：`tests/test_score_ranking_power.py`（数字不许手改 + 行为：1 行不许 PASS）。
MIN_INDEPENDENT_WINDOWS = 10


def verdict(res: dict) -> tuple[str, str]:
    """三态判决。**NOT_PASSED 两支都意味着不许发布**，区别只在下一步做什么。

    ⚠️ 判决按**闸门臂**（`GATE_ARM`）判，其余臂**并列报告但不当主判决**：
      早期版本按字典序取第一个失败臂就返回，于是理由文字落在已停用的
      `down` 臂上 —— 而「down 臂不单调」与「闸门没通过」的行动含义完全不同。
    """
    gate = res["arms"].get(GATE_ARM)
    if gate is None:
        return "NOT_PASSED_MEASURED", (
            f"闸门臂 {GATE_ARM!r} 在产物里不存在 ⇒ 判据无从判定，"
            "**不许**回落成「通过」")
    st = gate["_stats"]
    others = []
    for arm, sub in sorted(res["arms"].items()):
        if arm == GATE_ARM:
            continue
        s = sub["_stats"]
        others.append(
            f"{arm} 臂（**并列报告，非闸门**：S2 已因逆大盘 27.3% 停用）"
            f"单调={s['monotone_nondecreasing']} ρ={s['point_biserial_rho']:+.4f} "
            f"检出力下限={s['null_abs_rho_q95_power_floor']:.4f}")
    # 🚨 **最少独立窗口数**（2026-10-21 补执行一条**早已预注册**的判据）。
    #   预注册：`docs/DELIVERY_DECISIONS_2026-10-18.md §4 决策四`
    #   「D1 设验收门槛 —— 独立成交窗口 W ≥ 10」⇒ W<10 时不许报命中率、不许给 CI。
    #   ⚠️ **此前没执行 ⇒ 一行底稿就 PASS**（四分位在 1 行上天然单调）
    #   ⇒ 「一行判绿 D1 验收」。
    #   ⇒ 且 2026-10-21 晚间归属基准切换后底稿**只剩一行匹配** ⇒ 正好会踩上。
    w = int((res.get("independence_caliber") or {}).get("n_entry_dates") or 0)
    if w < MIN_INDEPENDENT_WINDOWS:
        return "NOT_PASSED_POWERLESS", (
            f"独立成交窗口只有 **{w}** 个，少于验收门槛 **{MIN_INDEPENDENT_WINDOWS}** 个"
            f"（预注册：docs/DELIVERY_DECISIONS_2026-10-18.md §4 决策四）"
            f" ⇒ **样本不足 ⇒ 不可判定** ⇒ 不许发布、不许报命中率、不许给 CI。"
            f"⚠️ **这不是「打分没有排序力」** —— 是「还没攒够样本能判」。"
            f"（四分位在 {w} 个窗口上「单调」是**平凡真**，不能当证据。）"
            + ("；" + "；".join(others) if others else ""))
    if st["monotone_nondecreasing"]:
        return "PASS", ("闸门臂四分位单调递增；仍须过 G1/G2/G3/G5/G6 才谈发布"
                        + ("；" + "；".join(others) if others else ""))
    kind = ("NOT_PASSED_POWERLESS" if not st["detectable_at_alpha05"]
            else "NOT_PASSED_MEASURED")
    tail = ("**这份样本连这个量级的关联都分不出来** ⇒ 不许发布，先攒前瞻期"
            if not st["detectable_at_alpha05"]
            else "**检出力足够却仍不单调** ⇒ 分数没有排序力，要换结构（S1）")
    return kind, (
        f"闸门臂 {GATE_ARM}：四分位不单调；点二列 ρ={st['point_biserial_rho']:+.4f}，"
        f"置换 p={st['permutation_p_two_sided']:.4f}，"
        f"零分布 95 分位（检出力下限）= "
        f"{st['null_abs_rho_q95_power_floor']:.4f} ⇒ {tail}"
        + ("；" + "；".join(others) if others else ""))


def _cannot_prove_lines(kind: str, n_pred_dates: int, n_entry_dates,
                        n_rows: int) -> list[str]:
    """产物里那段「本产物**不能**证明什么」。

    🚨 里面的数**必须**由本次运行算出来（第 73 层：同一事实被复制到多处）。
    写死的那一版在底稿补判之后已经与真值脱节（写着 4 日 / 80 条，
    真值已走到 5 日 / 100 行），而**产物是唯一证据** ——
    stdout 里那句算出来的数读者根本看不到。

    ⚠️ `10` 是**产品定义**（每日推荐 10 涨 / 10 跌），不是测量值，
    因此允许写死 —— 它不会随输入漂移。把「臂数 × 每臂只数」当测量量算
    是另一种错：那个分解在 `adjusted_score` 口径下根本不是 10。

    ⚠️ `kind` 一并参数化（预注册 §7.2 的延伸点 E1）：
    判决名与那三个数是同一段散文里的同一个量，写死它等于留下一个漂移源。

    🚨 `n_entry_dates` 允许是 `None`（输入缺 `entry_date` 列）：
    那时**照实说缺**，不许回落到 `n_pred_dates` 顶替 ——
    回落 = 一个与真值同形、看不出差别的数（第 6 种形状）。
    """
    entry_txt = ("**未知**（输入没有 `entry_date` 列）" if n_entry_dates is None
                 else f"**{n_entry_dates}**")
    return [
        f"**不能**把 {kind} 读成「打分没有排序力」——"
        f"判据臂只有 {n_pred_dates} 个**出单日**，而真实独立成交窗口 "
        f"{entry_txt} 个（出单日≠成交日，见 `independence_caliber`），"
        f"任何形状都极易出现，包括真的单调的那种；"
        "「没过关」与「被证伪」是两件事（§0 那句话就是这么错的）",
        f"**不能**用条目数 {n_rows} 当独立观测数 —— 它是 {n_pred_dates} 个出单日"
        f"× 每臂 10 只，而那 {n_pred_dates} 个出单日只对应 {entry_txt} 个成交窗口",
        "本脚本只测 G4 一个判据；G1/G2/G3/G5/G6 未测 ⇒ "
        "即使 G4 通过也**不许**据此发布",
    ]


def main(argv=None) -> int:
    # 生成器字节冻结（第 125 层）——🚨 取样点必须在**跑之前的第一条可执行语句**：
    # 放在盖章前一行就等于「拿开跑的字节盖开跑的章」⇒ 恒真（第 131 层）。
    expect_bytes = provenance.code_file_sha256(
        ROOT, "scripts/score_ranking_power.py")
    ap = argparse.ArgumentParser(description="S3/G4：score 四分位是否单调递增")
    ap.add_argument("--verification",
                    default="data/reports/verification.parquet")
    ap.add_argument("--out", default=f"data/reports/{ART_NAME}")
    ap.add_argument(
        "--reports-dir", default=None,
        help="生产报告目录（用于从**生产的真实配置**算当前信号 id）。"
             "默认取 --verification 的父目录。")
    args = ap.parse_args(argv)

    src = Path(args.verification)
    if not src.exists():
        print(f"🚨 {src} 不存在 —— G4 无法测量，**不许回落**", file=sys.stderr)
        return 2

    # 🚨🚨 **信号作用域**（预注册 §2 / §3）。
    #   底稿是**追加式**的，而 D1 的信号定义即将改变（可买性过滤启用）
    #   ⇒ 同一本底稿里会同时装着两个信号产生的行。
    #   ⇒ 本闸门**只问当前信号**的行；作用域不对 ⇒ **判据根本没资格跑**。
    reports_dir = Path(args.reports_dir) if args.reports_dir else src.parent
    ctx = resolve_attribution_context(reports_dir)
    # 🚨 **读口留在调用点**：既有守卫钉的是「`expect_bytes` 必须抓在读底稿之前」
    #   （第 125 层：盖章那一刻盘上的字节 ≠ 这次执行真正跑的那些字节）——
    #   那个性质**只有在读口可见于调用点时**才验得到。
    frame = pd.read_parquet(src)
    # 🚨 **过滤只走共享入口**（第 97 层：两份副本之间没有守卫）。
    scoped = scope_ledger(frame, ctx.signal_id, ctx.weights_fingerprint)

    if not scoped.has_evidence:
        # 🚨 作用域不对 ⇒ **不许**拿整本底稿判「现在这个打分器有没有排序力」
        #   （那判的是另一个信号）。⇒ 写一份**不可判定**的产物，
        #   覆盖掉那份按整本底稿判出来的旧判决（留着旧判决 = 假证据）。
        payload = {
            "prereg": "docs/TODO_reverse_scoring_2026-10-15.md",
            "gate": "G4 / S3 —— 上线闸门，与收益高低无关",
            "verification_source": str(src),
            "signal_scope": scope_block(scoped),
            "attribution": attribution_block(scoped),
            "primary_score_col": DEFAULT_SCORE_COL,
            "by_score_col": {},
            "verdict": VERDICT_NOT_DECIDABLE,
            "verdict_reason": scoped.reason,
            "publish_allowed": False,
            "what_this_cannot_prove": [
                "🚨 **本产物没有对当前信号做任何测量。** "
                "作用域不对 ⇒ 判据（以及其他六个判据）**根本没有资格跑**。",
                f"**不能**把 {VERDICT_NOT_DECIDABLE} 读成「打分没有排序力」—— "
                "它说的是「**这份证据不属于当前信号**」，"
                "**不是**「当前信号没有排序力」（第 16 种形状："
                "「测不出」不许写成「没有」）。",
                "**不能**引用本仓任何**按整本底稿**算出来的旧读数"
                "（旧读数属于旧信号）。",
            ],
        }
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        provenance.stamp(out, "scripts/score_ranking_power.py", root=ROOT,
                         expect_bytes=expect_bytes,
                         extra={"verdict": VERDICT_NOT_DECIDABLE,
                                "publish_allowed": False,
                                "signal_scope_status": scoped.status,
                                "source_parquet": str(src),
                                "source_parquet_sha256": _sha256_of(src)})
        print(not_decidable_note(scoped))
        print(f"判决（G4 / {DEFAULT_SCORE_COL} 口径）: {VERDICT_NOT_DECIDABLE}")
        print(f"  允许发布: False")
        print(f"→ {out}")
        return 0

    df = scoped.frame

    # 两个口径一起测：`score` 是 G4 原文的字段；`adjusted_score` 是 §0 引用的字段
    # （它只有 20/80 非 NaN ⇒ 引用它的那句结论样本量只有四分之一）。
    results = {c: measure(df, c) for c in ("score", "adjusted_score")
               if c in df.columns}
    primary = results[DEFAULT_SCORE_COL]
    kind, why = verdict(primary)

    payload = {
        "prereg": "docs/TODO_reverse_scoring_2026-10-15.md",
        "gate": "G4 / S3 —— 上线闸门，与收益高低无关",
        "verification_source": str(src),
        # 🚨 **作用域随数字一起离开产物**（预注册 §2.3）：
        #   读的人必须能一眼看出「这份判决是哪个信号的窗口判出来的」。
        "signal_scope": scope_block(scoped),
            "attribution": attribution_block(scoped),
        "primary_score_col": DEFAULT_SCORE_COL,
        "by_score_col": results,
        "verdict": kind,
        "verdict_reason": why,
        "publish_allowed": kind == "PASS",
        "what_this_cannot_prove": _cannot_prove_lines(
            kind, primary["n_independent_pred_dates"],
            primary["independence_caliber"]["n_entry_dates"],
            primary["n_rows_total"]),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    provenance.stamp(out, "scripts/score_ranking_power.py", root=ROOT,
                     expect_bytes=expect_bytes,
                     extra={"verdict": kind,
                            "publish_allowed": payload["publish_allowed"],
                            "signal_scope_status": scoped.status,
                            "signal_id": scoped.current_signal_id,
                            "source_parquet": str(src),
                            "source_parquet_sha256": _sha256_of(src)})

    for col, res in results.items():
        cal = res["independence_caliber"]
        print(f"\n=== 口径 {col}（{res['n_rows_judgeable']}/{res['n_rows_total']} 条可判定，"
              f"{cal['n_pred_dates']} 个出单日 / "
              f"{cal['n_entry_dates']} 个独立成交窗口）===")
        for arm, sub in res["arms"].items():
            st = sub["_stats"]
            seq = " | ".join(
                f"{k} {sub[k]['hit_rate']:.1%}(n={sub[k]['n']})"
                for k in sorted(k for k in sub if k.startswith("Q")))
            print(f"  {arm:>4} 臂: {seq}")
            print(f"        单调={st['monotone_nondecreasing']} "
                  f"ρ={st['point_biserial_rho']:+.4f} "
                  f"p={st['permutation_p_two_sided']:.4f} "
                  f"检出力下限|ρ|={st['null_abs_rho_q95_power_floor']:.4f} "
                  f"⇒ 分得出? {st['detectable_at_alpha05']}")
            ctl = st["control_arm_entry_date"]
            if ctl["available"]:
                print(f"        [对照臂·**非判决**] 按 entry_date 分组"
                      f"（{ctl['n_groups']} 组）: "
                      f"p={ctl['permutation_p_two_sided']:.4f} "
                      f"检出力下限|ρ|={ctl['null_abs_rho_q95_power_floor']:.4f} "
                      f"⇒ 分得出? {ctl['detectable_at_alpha05']}")
            else:
                print(f"        [对照臂·**非判决**] 不可算: "
                      f"{ctl['unavailable_reason']}")
    print(f"\n判决（G4 / {DEFAULT_SCORE_COL} 口径）: {kind}")
    print(f"  {why}")
    print(f"  允许发布: {payload['publish_allowed']}")
    print(f"→ {out}")
    return 0


def _sha256_of(path: Path) -> str:
    """产物字节指纹。**读文件本身**，不用 mtime —— mtime 是代理不是证据。"""
    import hashlib
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())

"""命中率诊断：把「推荐准不准」拆成**能区分病因**的几层，而不是只报一个总数。

## 为什么不能只报一个总数

2026-10-15 的实测（`data/reports/verification.parquet`，4 个交易日 / 69 条有效判定）：

    合计命中率 30/69 = 43.5%（95%CI 31.8%~55.2%，与 50% 无显著差异）

只看这个数会得到两个**都错**的结论：「样本不够，再攒」或「信号没用，放弃」。
必须再问一层：**这 43.5% 是被什么结构造成的？** 本脚本回答的正是这个问题。

## 三层分解

| 层 | 问题 | 判读 |
|---|---|---|
| ① 顺/逆大盘 | 信号是在选股，还是在押大盘方向？ | **顺大盘 58.3% / 逆大盘 27.3%** |
| ② beta 剔除后 | 抽掉大盘涨跌，还剩多少超额？ | 日均 **−0.304%**、t=−0.65 |
| ③ 打分排序力 | score 高的是否真的更准？ | up 臂四分位**无单调性** |

## 为什么这三层是这个项目的生死判据

用户的硬指标是「5 万元 / 年化 20%」。而：

    ① 顺大盘显著优于逆大盘，且逆大盘**显著低于随机**（p=0.0034）
       ⇒ 当前这两臂的「信号」主要在表达**大盘方向**，而其中反向表达的部分是
         **反向信息**——这不是噪声，是系统性的负贡献。
    ② 剔除 beta 后日均超额 −0.304%（年化 −76.6%）
       ⇒ 距离 +20% 的缺口是 **96.6 pp/年**，不是调参能填的量级。
    ③ score 无排序力
       ⇒ 「取 score 前 10 名」这个动作本身没有信息量，
         所以「把因子权重调好」不会改变命中率——**这是设计问题，不是标定问题**。

⇒ 结论方向：**先改结构（两臂的定义与排序），再谈标定。**
在结构改对之前跑更多同构的前瞻数据，只是把同一个 43.5% 攒得更精确。

## 🚨 口径：独立样本单位是**成交窗口**，不是交易行（2026-10-16）

本脚本曾按**交易行**算区间，于是 up 腿 45 行给出 ±14.6% 的半宽。
但那 45 行只落在 **3 个**独立成交窗口上（`run_daily.py:290-293` 逐字记载：
周五/六/日三次出名单落进同一个 10-12→10-13 窗口）⇒

    把一次开平仓的价格路径当成 45 次独立重抽样 ⇒ 标准误被低估 ⇒
    区间窄了 3.87 倍 ⇒ **「测不出方向」被显示成「边缘显著」**。

⇒ 现在每个块都报**两个口径**，并排落盘（预注册
`docs/PREREG_HIT_RATE_CLUSTER_2026-10-16.md` §3）：

| 字段 | 独立观测单位 | 保留它的理由 |
|---|---|---|
| `ci95` / `z` / `p_two_sided` | **成交窗口**（`n → W`） | 这是唯一能回答「窗口为单位时能不能判出方向」的那一个 |
| `ci95_row_level` / `z_row_level` / … | **交易行** | 🚨 **不许删**：读者要看见两个口径差多少；只留一个，历史文档里的 14.6% 就失去了对照 |
| `half_width` / `half_width_row_level` | 各自的 | 🚨 必须单列：区间在 [0,1] 上被截断时，半宽**反推不出来** |

⚠️ 这个区间是**保守方向**（偏宽）：它把整窗当成一次抽样，丢掉窗内全部信息。
写在这里是因为「过严的尺子会把人推向去改本来正确的代码」。

## 口径纪律

- 只统计 `result ∈ {hit, miss}`；`no_data`（无次日 bar）**剔除并计数**，
  剔除量必须一起报出来，否则样本量会在无声中被削减。
- `flat`（恰好 0.00%）单列，按 `aplus/verify.py::judge` 的契约不计命中。
- 🚨 **窗口数 < 2 时拒绝出区间**（`ci95 = null`）：一个窗口估不出窗口间方差，
  返回 `[0,1]` 或 `[p,p]` 都是「看起来有结论」的假象。
- 🚨 **MDE 必须与点估计并排印出**，且 `|rate−0.5| / MDE < 1` 时必须写明
  「连符号都定不了」——本仓铁律：任何「不显著」不许单独出现。
- **禁止把「顺大盘更好」直接写成「信号有效」**：两者之间的因果由 ② 检验，
  而 ② 的答案是否定的。分层是为了**防止**这种误读。
- 🚨 **禁交付式措辞**（「达标/未达标」类）：本产物只报点估计、区间与 MDE，
  判决不归它做。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from math import erfc, sqrt
from pathlib import Path
from statistics import NormalDist

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent

# 🚨 形状 24：`python <脚本>` 的 sys.path[0] 是 `scripts/`，**不含仓库根** ⇒
#    不插这一行，命令行直接跑必然 ModuleNotFoundError（而 tests/ 按路径导入
#    自带仓库根，会替它把缺陷补上——守卫全绿而脚本跑不起来）。
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

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

GENERATOR = "scripts/diagnose_hit_rate.py"

# ── 聚类口径的判据（预注册 docs/PREREG_HIT_RATE_CLUSTER_2026-10-16.md §3）──
# 🚨 这些数字**不许由数据反推**：窗口键、SE 的形式、MDE 的临界值、
#   「小于几个窗口算薄」全部写死在这里。数据变了只许改判据的**输入**，
#   不许改判据本身（`docs/TODO_factor_width_2026-10-15.md` 第 145 层：
#   「预注册保证判据先于数据，不保证前提先于判据」）。

#: 观测日列：**收益真正实现的那天**，与 `aplus/verify.py::daily_portfolio_returns`
#: 的 `day_col` 同口径。🚨 不是 `pred_date`（出名单那天）——周末出单会让
#: 周五/六/日三天落进同一个成交窗口，按 pred_date 分组等于把一个窗口数成三天。
CLUSTER_KEY = "exit_date"

#: 旧行（没有观测日）退回出名单日时加的前缀。
#: 🚨 不加前缀的话，旧行的 `2026-10-13` 会与新行的 `exit_date=2026-10-13`
#: **混进同一个窗口**——那是把两个不同的量合并，而两个量看起来一模一样。
LEGACY_PREFIX = "legacy:"

#: MDE = (z₀.₉₇₅ + z₀.₈₀)·se。双侧 α=0.05、检出力 80%（外部锚：正态分位数，
#: 不是本仓拍出来的数）。与点估计并排印出是铁律，不许只说「不显著」。
MDE_Z_SUM = NormalDist().inv_cdf(0.975) + NormalDist().inv_cdf(0.80)

#: 独立成交窗口少于这个数 ⇒ 措辞降级为「薄」。
THIN_WINDOWS = 10

#: 基准：命中与否的判定门是 0（`aplus/verify.py::judge`），
#: 「命中率是否异于 50%」的基准也必须是 0.5 而不是别的数。
NULL_RATE = 0.5


def _two_sided_p(hits: int, n: int) -> tuple[float, float, float, float]:
    """返回 (命中率, 标准误, z, 双侧 p)。比例检验的正态近似。

    n 很小的时候这个近似很粗，所以调用方必须**同时**把 CI 一起报出去——
    这与本仓「证据强度不同就必须在措辞上分级」是同一条纪律。
    """
    if n == 0:
        return (float("nan"),) * 4
    p = hits / n
    se = sqrt(p * (1 - p) / n) if 0 < p < 1 else 0.0
    z = (p - NULL_RATE) / se if se else float("nan")
    pv = erfc(abs(z) / sqrt(2)) if se == se else float("nan")
    return p, se, z, pv


def _cluster_of(df) -> "pd.Series[str]":
    """每行属于哪个**独立成交窗口**。

    优先观测日（收益实现那天），缺观测日的旧行退回出名单日并**单独成组**。
    与 `aplus/verify.py::daily_portfolio_returns` 的分组规则逐字对齐 ——
    两个产物各算各的窗口数会让「同一个窗口」在两个报告里对不上。
    """
    key = None
    if CLUSTER_KEY in df.columns and df[CLUSTER_KEY].notna().any():
        key = df[CLUSTER_KEY].astype(str)
        legacy = df[CLUSTER_KEY].isna()
        if legacy.any():
            if "pred_date" not in df.columns:
                raise ValueError(
                    "有行缺 exit_date 而整表没有 pred_date ⇒ "
                    "无法给这些行定观测窗口；此时算出来的窗口数是**猜的**")
            key = key.mask(legacy, LEGACY_PREFIX + df["pred_date"].astype(str))
    elif "pred_date" in df.columns:
        key = df["pred_date"].astype(str)
    if key is None:
        raise ValueError(
            "既没有 exit_date 也没有 pred_date ⇒ 本表无法定出独立成交窗口；"
            "此时报出来的区间是**按行**算的，也就是本脚本要修的那个缺陷")
    return key


def _windows(frame) -> list[dict]:
    """逐窗口的 (行数, 命中数, 命中率)，按窗口名排序。

    ⚠️ **逐窗口 n 必须一起落盘**：只报窗口率不报分母，
    读者无法知道 51.7% 是一窗 29 条还是一窗 5 条。
    """
    agg = (frame.groupby("_win", observed=True)["hit"]
           .agg(n="size", hits="sum").sort_index())
    return [{"window": str(w), "n": int(r["n"]), "hits": int(r["hits"]),
             "rate": round(float(r["hits"]) / int(r["n"]), 4)}
            for w, r in agg.iterrows()]


def _cluster_fields(hits: int, n: int, frame) -> dict:
    """成交窗口口径的推断量（预注册 C2）。

    点估计**不变**（仍是 Σhits/Σn），唯一改动是标准误里的独立观测数
    `n → W`。⇒ 两个口径的差**全部**归因于聚类这一个变量，可逐位对账
    （守卫钉的正是 `半宽比 == sqrt(N/W)` 这条代数恒等式）。

    W < 2 ⇒ 返回全 None：估不出窗口间方差，返回 `[0,1]` 或 `[p,p]`
    都是「看起来有结论」的假象。
    """
    wins = _windows(frame)
    W = len(wins)
    if n == 0 or hits != hits:
        return {"windows": wins, "n_windows": W, "ci95": None,
                "half_width": None, "ci95_clipped": None,
                "se": None, "z": None, "p_two_sided": None, "mde": None}
    p = hits / n
    if W < 2:
        return {"windows": wins, "n_windows": W, "ci95": None,
                "half_width": None, "ci95_clipped": None, "se": None,
                "z": None, "p_two_sided": None, "mde": None}

    se = sqrt(p * (1 - p) / W) if 0 < p < 1 else 0.0
    hw = 1.96 * se
    lo, hi = max(0.0, p - hw), min(1.0, p + hw)
    z = (p - NULL_RATE) / se if se else float("nan")
    pv = erfc(abs(z) / sqrt(2)) if se == se else float("nan")
    return {"windows": wins, "n_windows": W,
            "ci95": [round(lo, 4), round(hi, 4)],
            # ⚠️ 半宽落盘到 6 位小数不是随手定的：读者要在产物里**逐位对账**
            #   「两个口径只差 n→W 这一处」，而 4 位会让比值偏到 1e-4 量级。
            "half_width": round(hw, 6),
            # 🚨 截断过必须落盘：区间被夹进 [0,1] 之后半宽**反推不出来**，
            #   而「半宽比 = sqrt(N/W)」这条恒等式会被截断悄悄破坏。
            "ci95_clipped": bool(lo != p - hw or hi != p + hw),
            # 未取整的半宽，只在 `_block` 里用来算膨胀因子。
            # ⚠️ 用取整后的值算比值会让「没有叠加时膨胀因子恰为 1」变成 1.0001
            #   —— 四舍五入误差被当成信号（断言 vs 公式的又一次同族：拿显示精度当真值）。
            "_half_width_exact": hw,
            "se": round(se, 6),
            "z": None if z != z else round(z, 3),
            "p_two_sided": None if pv != pv else round(pv, 4),
            "mde": None if se == 0 else round(MDE_Z_SUM * se, 4)}


def _block(hits: int, n: int, frame) -> dict:
    """一个分层的全部推断量，**两个口径并排**。

    ⚠️ 调用方必须把带 `_win` 列的子表传进来（`diagnose_verdicts` 负责建它）——
    没有窗口信息就没法算聚类口径，而回落到行级就是本脚本要修的那个缺陷。
    """
    p, se_row, z_row, pv_row = _two_sided_p(hits, n)
    row_ci = (None if p != p else [round(max(0.0, p - 1.96 * se_row), 4),
                                    round(min(1.0, p + 1.96 * se_row), 4)])
    cl = _cluster_fields(hits, n, frame)
    W = cl["n_windows"]

    gap = None if p != p else round(abs(p - NULL_RATE), 4)
    mde = cl["mde"]
    ratio = (None if (mde in (None, 0.0) or gap is None)
             else round(gap / mde, 3))
    if cl["ci95"] is None:
        note = (f"独立成交窗口只有 {W} 个 ⇒ 窗口间方差估不出来 ⇒ "
                "不给区间、也不给 MDE（这不等于「没有差异」，只等于「测不到」）")
    elif ratio is not None and ratio < 1.0:
        note = (f"点估计离 50% 只有 {ratio} 个 MDE ⇒ 连符号都定不了，"
                "只支持『测不到』这类形状陈述，不支持关于水平的任何陈述")
    else:
        note = (f"点估计离 50% 有 {ratio} 个 MDE ⇒ 这个距离本身是测得到的；"
                "命中率是否异于 50% 由 ci95 回答（区间跨过 0.5 就仍然测不到）")

    return {
        "n": n,
        "hits": hits,
        "n_windows": W,
        "windows": cl["windows"],
        "rate": None if p != p else round(p, 4),
        # 🚨 每窗口 n 不等 ⇒ 窗口率的**简单平均**与合并率不是同一个量。
        #   它必须单列（展示离散度）并自带「不是整体命中率」的标注，
        #   也不许被任何人拿它当整体（第 79 层：重叠/不等权分组被当成条目计数）。
        "unweighted_mean_of_window_rates": (
            None if not cl["windows"] else
            round(sum(w["rate"] for w in cl["windows"]) / len(cl["windows"]), 4)),
        "unweighted_mean_note": (
            "⚠️ 窗口率的**简单平均**，**不是整体命中率**——每窗口行数不等"
            f"（实测 {[w['n'] for w in cl['windows']]}）⇒ 整体命中率只有 `rate`"
            "（Σhits/Σn）。此列仅用于展示窗口之间的离散度，不进任何判断。"),
        # ── 聚类口径（头条：唯一能回答「窗口为单位时能否判出方向」的）──
        "ci95": cl["ci95"],
        "ci95_caliber": f"独立观测单位 = 成交窗口（W={W} 个 {CLUSTER_KEY}）",
        "ci95_method": "正态近似，SE 的分母是窗口数 W 而非行数 N",
        "half_width": cl["half_width"],
        "ci95_clipped": cl["ci95_clipped"],
        "z": cl["z"],
        "p_two_sided": cl["p_two_sided"],
        # ── 🚨 行级口径：并排保留，标注它被窗口叠加虚增（预注册 C3）──
        "ci95_row_level": row_ci,
        "ci95_row_level_caliber": (
            "独立观测单位 = 交易行（N 行）——🚨 被窗口叠加虚增："
            "同一窗口内的行共享同一次开平仓的价格路径，不是独立重抽样；"
            "保留它是为了让读者看见两个口径差多少，不许当成可用区间"),
        "half_width_row_level": None if row_ci is None else round(1.96 * se_row, 6),
        "z_row_level": None if z_row != z_row else round(z_row, 3),
        "p_two_sided_row_level": None if pv_row != pv_row else round(pv_row, 4),
        "row_level_inflated_by_window_stacking": bool(W < n),
        "inflation_factor": (
            None if (W == 0 or se_row == 0 or cl["half_width"] is None)
            else round(cl["_half_width_exact"] / (1.96 * se_row), 6)),
        # 🚨 MDE 与点估计同块落盘；不许只报「不显著」（本仓铁律）
        "mde_rate_points": mde,
        "mde_caliber": "80% 检出力、双侧 α=0.05，MDE=(z₀.₉₇₅+z₀.₈₀)·SE(聚类口径)",
        "abs_gap_to_50pct": gap,
        "gap_over_mde": ratio,
        "measurement_note": note,
        # 窗口数 < 2 不可估、< 10 薄。两个量级差很多的证据不许长得一样
        "evidence": ("不可估（独立成交窗口 <2，聚类区间无定义）" if W < 2 else
                     f"薄（独立成交窗口 {W} <{THIN_WINDOWS}，聚类区间宽到不能当结论用）"
                     if W < THIN_WINDOWS else
                     "窗口级可作方向性证据（仍不等于对 50% 基准的判决）"),
    }


def diagnose_verdicts(df) -> dict:
    """从**已判定**的表算出分层诊断。

    单独抽出来是为了让守卫能用构造出来的表直接打，
    而不必去碰真产物（真产物每天才长几行，拿它当测试输入等于
    「拿现场当标准答案」——现场自己也在变）。
    """
    dropped = {"no_data": int((df["result"] == "no_data").sum()),
               "flat": int((df["result"] == "flat").sum())}
    m = df[df["result"].isin(["hit", "miss"])].copy()
    if m.empty:
        raise ValueError(
            f"没有可判定的记录（全部落在 no_data/flat：{dropped}）——"
            f"此时算命中率只会得到 0/0 或 0.0，那是**看起来有结论**的假象")
    m["hit"] = (m["result"] == "hit").astype(int)
    # 🚨 聚类键在这里建一次，后面每一层都拿它的子表去算窗口 ——
    #   「每层各建各的」会让同一个窗口在不同层数出不同的 W，而那时没有任何东西会喊。
    m["_win"] = _cluster_of(m)

    out = {
        "excluded": dropped,
        "verdicts": len(m),
        "trading_days": int(m["pred_date"].nunique()),
        "span": [str(m["pred_date"].min()), str(m["pred_date"].max())],
        # 成交窗口与出名单天数**不是同一个量**（实测 3 vs 5：周五/六/日
        # 三次出单落进同一个成交窗口）⇒ 两个都报，且不许拿其中一个当另一个。
        "n_windows": int(m["_win"].nunique()),
        "window_span": [str(m["_win"].min()), str(m["_win"].max())],
        "独立样本单位": (
            f"成交窗口（{CLUSTER_KEY}，缺则退回 pred_date 并加 "
            f"{LEGACY_PREFIX!r} 前缀单独成组）——不是交易行：同一窗口内的行"
            "共享同一次开平仓的价格路径，按行算等于把一次抽样当几十次"),
        # ── ① 总命中率 ──
        "overall": _block(int(m["hit"].sum()), len(m), m),
        "by_direction": {d: _block(int(g["hit"].sum()), len(g), g)
                         for d, g in m.groupby("direction")},
    }

    # ── ② 顺/逆大盘：信号是在选股还是在押方向 ──
    if "market_pct" in m.columns:
        # ⚠️ 用**字符串**标签而不是布尔列：numpy 的 `bool_` 真值判断不可靠，
        #   而且 `groupby` 出来的键类型会随 pandas 版本变（`numpy.bool_` vs
        #   `bool`），靠 `if k` 分支迟早在某次升级后静默错位。
        m["顺大盘"] = np.where((m["direction"] == "up") == (m["market_pct"] > 0),
                              "顺大盘", "逆大盘")
        out["with_market"] = {str(k): _block(int(g["hit"].sum()), len(g), g)
                              for k, g in m.groupby("顺大盘")}
        # ── ③ 剔除 beta 后的超额 ──
        sign = np.where(m["direction"] == "up", 1, -1)
        m["超额"] = (m["actual_pct"] - m["market_pct"]) * sign
        ex = {}
        for key, g in [("合计", m)] + sorted(m.groupby("direction")):
            mu = float(g["超额"].mean())
            sd = float(g["超额"].std(ddof=1))
            t = mu / (sd / sqrt(len(g))) if sd and len(g) > 1 else float("nan")
            ex[key] = {"n": len(g), "n_windows": int(g["_win"].nunique()),
                       "日均超额pct": round(mu * 100, 4),
                       "年化pct": round(mu * 252 * 100, 2),
                       "t": None if t != t else round(t, 3),
                       "p_two_sided": None if t != t else round(erfc(abs(t) / sqrt(2)), 4)}
        out["beta_adjusted_excess"] = ex
        # 🚨 这一节的 t 仍然**按行**算（年化按 252 次/年外推）。
        #   本轮不改口径——改它属于换判据，必须另写预注册；
        #   但**必须说清**，否则读者会以为它与上面两节同口径。
        out["beta_adjusted_excess_caliber"] = (
            "🚨 本节仍是**行级**口径（n = 行数，年化按 252 次/年线性外推），"
            "与命中率两节的聚类口径**不同口径**，不可互相印证。"
            "要改成窗口级必须另写预注册，不许顺手改。")

    # ── ④ 打分排序力：score 高的是否真的更准 ──
    up = m[m["direction"] == "up"].dropna(subset=["adjusted_score"])
    if len(up) >= 8:
        try:
            q = pd.qcut(up["adjusted_score"], 4, labels=["Q1最低", "Q2", "Q3", "Q4最高"])
            r = up.groupby(q, observed=True)["hit"].agg(["size", "mean"])
            out["score_ranking_power"] = {
                "by_quartile": {str(i): {"n": int(row["size"]),
                                         "rate": round(float(row["mean"]), 4)}
                                for i, row in r.iterrows()},
                "monotonic_increasing": bool(r["mean"].is_monotonic_increasing),
                "missing_adjusted_score": {
                    d: int(g["adjusted_score"].isna().sum())
                    for d, g in m.groupby("direction")},
            }
        except ValueError as exc:
            out["score_ranking_power"] = {"error": f"分箱失败：{exc}"}

    out["读取的判据契约"] = (
        "① 『命中率低』不等于『样本不够』：先看 with_market 与 "
        "beta_adjusted_excess。若顺大盘显著优于逆大盘，则信号主要在表达大盘方向；"
        "此时**增加同构前瞻样本只会把同一个数攒得更精确**，不会改变方向。"
        "② 🚨 **读 `ci95` 之前先读 `ci95_caliber`**：独立观测单位是成交窗口，"
        "不是交易行。行级那一个（`ci95_row_level`）被窗口叠加虚增，"
        "它只用来让人看见两个口径差多少。"
        "③ 🚨 **不许把「测不出」写成「没有」**：每个块的 "
        "`mde_rate_points` 与 `measurement_note` 必须与点估计一起读。"
        "④ 🚨 **每窗口 n 不等** ⇒ `unweighted_mean_of_window_rates` "
        "不是整体命中率，只有 `rate`（Σhits/Σn）是。")
    return out


def diagnose(scoped) -> dict:
    """对**已按信号身份过滤**的底稿做分层诊断。

    🚨 传入的是**作用域过滤后**的那一段（`ScopedLedger`）——
    本函数**不读**底稿，也**不自己实现**过滤（第 97 层）：
    过滤的唯一实现是 `aplus/signal_scope.py::scope_ledger`。
    """
    if not scoped.has_evidence:
        raise ValueError(
            f"底稿作用域不可用（{scoped.status}）：{scoped.reason}")
    return diagnose_verdicts(scoped.frame)


def _sha256_of(path: Path) -> str:
    """产物输入的字节指纹。

    ⚠️ **读文件本身，不用 mtime** —— mtime 是代理不是证据：
    本仓的变异台会「改源码 → 跑测试 → 写回」，
    内容回到逐位相同而 mtime 被顶到现在（第 112 层那条实测）。

    这里**不复用** `scripts/score_ranking_power.py` 的同名私函数：
    跨脚本 import 一个下划线私有名是把两个脚本焊在一条 import 链上，
    而 sha256 本身就是标准库原语（第 163 层禁的是**重写**量具，
    不是**调用**标准库）。
    """
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verification", type=Path,
                    default=ROOT / "data/reports/verification.parquet")
    ap.add_argument("--out", type=Path,
                    default=ROOT / "data/reports/hit_rate_diagnosis.json")
    ap.add_argument(
        "--reports-dir", type=Path, default=None,
        help="生产报告目录（用于从**生产的真实配置**算当前信号 id）。"
             "默认取 --verification 的父目录。")
    a = ap.parse_args()
    if not a.verification.is_file():
        print(f"✗ 缺档：{a.verification} —— 没有它就不能报命中率（缺档必须报错）")
        return 1

    # 🚨 第 125 层：盖章那一刻盘上的字节 ≠ 这次执行真正跑的那些字节。
    #   抓在**跑之前**，比对在**盖章时**；不一致就拒绝盖章（反向的错误证据）。
    expect_bytes = provenance.code_file_sha256(ROOT, GENERATOR)

    # 🚨🚨 **信号作用域**（预注册 §2 / §3）。
    #   底稿是**追加式**的 ⇒ 改动落地后同一本底稿会装着**两个信号**的行。
    #   ⇒ 命中率**只报当前信号**的行；作用域不对 ⇒ **不报命中率**。
    reports_dir = a.reports_dir if a.reports_dir else a.verification.parent
    ctx = resolve_attribution_context(reports_dir)
    # 🚨 **读口留在调用点**（既有守卫钉住：expect_bytes 必须抓在它之前）。
    frame = pd.read_parquet(a.verification)
    # 🚨 **过滤只走共享入口**（第 97 层）。
    scoped = scope_ledger(frame, ctx.signal_id, ctx.weights_fingerprint)

    if not scoped.has_evidence:
        # 🚨 作用域不对 ⇒ **不许**报命中率（那报的是别的信号）。
        #   写一份**不可判定**的产物，覆盖掉按整本底稿算出来的旧读数
        #   （留着旧读数 = 一份「看起来有结论」的化石）。
        res = {
            "verdict": VERDICT_NOT_DECIDABLE,
            "verdict_reason": scoped.reason,
            "signal_scope": scope_block(scoped),
            "attribution": attribution_block(scoped),
            "what_this_cannot_prove": [
                "🚨 **本产物没有报任何命中率。** 作用域不对 ⇒ 命中率"
                "**没有资格**被算出来。",
                f"**不能**把 {VERDICT_NOT_DECIDABLE} 读成「命中率低」或"
                "「命中率是 0」——它说的是「**这份证据不属于当前信号**」。",
                "**不能**引用任何**按整本底稿**算出来的旧读数（旧读数属于旧信号）。",
            ],
        }
        txt = json.dumps(res, ensure_ascii=False, indent=2)
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(txt, encoding="utf-8")
        # 🚨 第 156 层：`stamp()` 默认把 `data_fp` 指向 `master/daily`（5460 个文件），
        #   而本脚本读的是**单个文件** `verification.parquet` ⇒ 那样盖出来的章
        #   指的是一个本脚本没读过的数据根，而真正要抓的输入漂移落在视野之外。
        #   底稿是**追加式**的（实测 80 行/4 日 → 100 行/5 日），
        #   所以必须由本脚本**自己声明**它读的那份字节。
        provenance.stamp(
            a.out, GENERATOR, root=ROOT, expect_bytes=expect_bytes,
            extra={
                "source_parquet": str(a.verification),
                "source_parquet_sha256": _sha256_of(a.verification),
                "verdict": VERDICT_NOT_DECIDABLE,
                "signal_scope_status": scoped.status,
                "signal_id": scoped.current_signal_id,
            })
        print(not_decidable_note(scoped))
        print(txt)
        return 0

    res = diagnose(scoped)
    # 🚨 **作用域随数字一起离开产物**（预注册 §2.3）。
    res["signal_scope"] = scope_block(scoped)
    res["attribution"] = attribution_block(scoped)
    txt = json.dumps(res, ensure_ascii=False, indent=2)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(txt, encoding="utf-8")
    provenance.stamp(
        a.out, GENERATOR, root=ROOT, expect_bytes=expect_bytes,
        extra={
            "source_parquet": str(a.verification),
            "source_parquet_sha256": _sha256_of(a.verification),
            "verdicts_measured": res["verdicts"],
            "trading_days_measured": res["trading_days"],
            # 🚨 独立观测数也进章：它是本轮判决的**分母**，而章不记它的话，
            #   底稿从 100 行长到 130 行而没人知道长的是窗口还是行。
            "windows_measured": res["n_windows"],
            "signal_scope_status": scoped.status,
            "signal_id": scoped.current_signal_id,
        })
    print(txt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

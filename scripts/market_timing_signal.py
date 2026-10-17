#!/usr/bin/env python
"""市场择时真实信号臂（PREREG §0~§8，预注册：docs/PREREG_MARKET_TIMING_2026-10-17.md）。

上界（184.50%/年）已经算完；本文件回答的是**另一个问题**：
**3 个自研的、零前视的真实信号臂，能不能在成本后把账户从 10.86%/年推到 20%/年。**

🚨 本文件**不是**上界的续集，是**另一个对象**：上界用块内未来函数（oracle），
本文件一个未来函数都不许有。两者不许混着引用。

判据全部来自预注册，**代码里不写字面量门槛**：
  - 候选数 N_CANDIDATES=3（事前登记，Bonferroni 分母）
  - 年化常数 DAYS_PER_YEAR 从 aplus.hurdle import
  - t* 从 aplus.dist::t_crit_two_sided 二分反解
  - 分块 t 与 n_obs **委派**给 aplus.evolve_race::_tstat（不重写同一公式）
  - 成本档从 market_timing_ceiling.cost_tiers(df) **实测反推**（不打字面量）

跑法：.venv/bin/python scripts/market_timing_signal.py
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aplus import provenance  # noqa: E402
from aplus.dist import t_crit_two_sided  # noqa: E402
from aplus.evolve_race import _block_means, _tstat  # noqa: E402
from aplus.hurdle import DAYS_PER_YEAR  # noqa: E402
from scripts.market_timing_ceiling import annualize, cost_tiers  # noqa: E402

GENERATOR = "scripts/market_timing_signal.py"
UPSTREAM = ROOT / "data" / "reports" / "wf_weights_backtest_k21.daily.parquet"
UPSTREAM_META = ROOT / "data" / "reports" / "wf_weights_backtest_k21.json"
CEILING = ROOT / "data" / "reports" / "market_timing_ceiling.json"
OUT = ROOT / "data" / "reports" / "market_timing_signal.json"

# 🚨 事前登记值（PREREG §4）。**不许**从下面的臂表反推 —— 反推就是
# 「看到数据之后再定分母」（第 91 层）。run_arms() 里有断言把这个不等式变成响声。
N_CANDIDATES = 3
ALPHA = 0.05

# 预注册 §4 写死的信号窗口（不可调、不可事后扫）
TREND_WIN = 21
REVERSAL_WIN = 5
VOL_WIN = 252

# 预注册 §4 安慰剂臂
N_PLACEBO = 100
PLACEBO_SEED_BASE = 20261017

# 🚨 投毒常量。它必须大到「被看见」而不是大到「溢出成 nan」——
# 1e9 走完 exp/复利不会溢出，而 1e300 会。
POISON = 1e9

TARGET_ANNUAL = 0.20


# ────────────────────────────── 会计（一处定义，全文件共用） ──────────────

def daily_returns(exposure, market, tier_cost, prev0=1.0):
    """逐日净值收益率。

    与 market_timing_ceiling.oracle_nav 的会计**逐字同构**：
        nav_t = nav_{t−1} · (1 − cost_t) · (1 + e_t · r_t)
    ⇒ r_t = (1 − cost_t)·(1 + e_t·r_t) − 1，可加可乘皆可。

    cost_t = |e_t − e_{t−1}| · tier_cost，e_{−1} = prev0 = 1.0（账户本就满仓）。
    ⚠️ prev0=1.0 让 always_full 全程零成本，与 ceiling 的 always_full 同会计。
    """
    expo = np.asarray(exposure, dtype=float)
    mkt = np.asarray(market, dtype=float)
    prev = np.concatenate(([float(prev0)], expo[:-1]))
    cost = np.abs(expo - prev) * float(tier_cost)
    return (1.0 - cost) * (1.0 + expo * mkt) - 1.0


def arith_annual(daily):
    """算术年化 = mean · DAYS_PER_YEAR（PREREG §5 判决臂口径）。"""
    s = pd.Series(np.asarray(daily, dtype=float)).dropna()
    if len(s) == 0:
        return float("nan")
    return float(s.mean()) * DAYS_PER_YEAR


def compound_annual(daily):
    """复利年化（与 ceiling 的 annualize 同口径，用于交叉对拍）。"""
    s = pd.Series(np.asarray(daily, dtype=float)).dropna()
    if len(s) == 0:
        return float("nan")
    return annualize(float(np.prod(1.0 + s.to_numpy()) - 1.0), len(s), 1)


def nav_path(daily):
    return np.cumprod(1.0 + np.asarray(daily, dtype=float))


# ─────────────────── 读数层的量纲（第 97 层的唯一修法） ───────────────────
#
# 🚨 本文件**所有年化字段都是分数**，一个百分数都没有：
#     arith_annual  = mean × 244              → 0.1245 = 12.45%/年
#     excess        = 算术臂 − 算术对照        → −0.1093 = −10.93pp
#     mde           = t × (sd_block/√n) × 244  → 0.2422 = 24.22pp
# ⇒ 而它们的**字段名**写着 `pct` / `_pp`，那些字样**不承诺量纲**（第 182 层）。
# ⇒ 于是本文件曾有 6 处「拿分数配 % / pp 字样」，每个读数**缩小 100 倍**，
#   而**判决一个字都没变**（`excess` 与 `mde` 同量纲 ⇒ 比较仍然正确）——
#   错的是**读者**，而错的方向是把它读得更差（第 15 种形状）。
#
# ⇒ **修法不是逐处补 `* 100`**（那只会留下第七处入口），而是**让换算只有一个入口，
#   且单位字样由调用点必须自己说出**：少传 `unit` 直接 `TypeError`，
#   而这正是「调用点没说它认为这个数是什么」的守卫。
PCT_SCALE = 100.0


def printed(x, unit, *, signed=False, width=None, nd=4):
    """把**分数**年化量印成真实读数（0.1245 + '%' ⇒ '12.4529%'）。

    `unit` **必填**，且只接受 `%` 与 `pp`：
    - `%`  —— 该数是**水平**（年化收益本身）
    - `pp` —— 该数是**差额**（臂 − 对照、MDE）

    ⚠️ 这两者**量纲相同、只是读法不同**（都是「分数 × 100」），所以本函数
    **不做任何量纲判断** —— 它唯一的职责是「把分数换成人读的单位并盖上章」。

    🚨 **它只管显示层，不管算术层。** `cross_check_vs_ceiling.abs_diff_pp`
    里那个 `* 100.0` **必须留着**：那是在**造一个真 pp 值**（两个操作数
    `arith − compound` 都是分数，差也是分数 ⇒ ×100 才是它声称的 pp），
    属于算术，不属于读数层。**把它当成本函数的一个漏网之鱼删掉，会把
    0.9101 变成 0.009101。** 两处 ×100 测的是不同的东西。
    """
    if unit not in ("%", "pp"):
        raise ValueError(f"unit 只接受 '%'（水平）或 'pp'（差额），收到 {unit!r}")
    v = float(x) * PCT_SCALE
    w = f"{width}" if width else ""
    spec = f"{'+' if signed else ''}{w}.{nd}f"
    return f"{v:{spec}}{unit}"


# ────────────────────────────── MDE（PREREG §5） ──────────────────────────────

def mde_and_t(excess_daily, block):
    """从**实测**差序列算 MDE 与分块 t。三个读数一起返回，不许只印符号。

    n_obs 必须取自 _tstat 的返回值 —— _block_means 在 b>1 时会 dropna + 丢尾部零头，
    所以 len(excess)//block 是**另一个数**（第 135 层）。
    """
    s = pd.Series(np.asarray(excess_daily, dtype=float))
    t_blocked, n_obs = _tstat(s, block)
    blocks = _block_means(s, block)
    sd_block = float(np.std(blocks, ddof=1)) if len(blocks) > 1 else float("nan")
    se = sd_block / math.sqrt(n_obs) if n_obs > 0 and math.isfinite(sd_block) else float("nan")

    # Bonferroni：分母是事前登记的 N_CANDIDATES，不是 len(臂表)
    df = max(n_obs - 1, 1)
    t_crit = float(t_crit_two_sided(ALPHA / N_CANDIDATES, df))
    t_crit_uncorrected = float(t_crit_two_sided(ALPHA, df))
    mde = t_crit * se * DAYS_PER_YEAR if math.isfinite(se) else float("nan")

    # 自洽断言（不是「用公式重算验证」，是「两个独立算法给同一个 t」）：
    # 等长块 ⇒ 块均值的均值 == 对齐后逐日序列的均值 ⇒ 两条路径必须给同一个 t。
    self_check = None
    if n_obs >= 1 and math.isfinite(se) and se > 0:
        aligned = s.dropna().to_numpy()[: n_obs * block]
        t_recomputed = float(aligned.mean() / se)
        self_check = {
            "t_from_tstat": t_blocked,
            "t_from_aligned_mean_over_se": t_recomputed,
            "agree": bool(t_blocked is not None
                          and math.isfinite(t_recomputed)
                          and abs(t_blocked - t_recomputed) <= 1e-9 * max(1.0, abs(t_blocked))),
        }
    return {
        "block": int(block),
        "block_why": "块长 = 该臂信号窗口长度（事前写死，不可从数据反推）",
        "n_obs": int(n_obs),
        "n_obs_unit": "块数（一次调仓 = 一个独立观测），不是天数",
        "sd_block_annual_pct": sd_block * math.sqrt(DAYS_PER_YEAR),
        "se_of_daily_mean_annual_pct": se * DAYS_PER_YEAR,
        "t_crit_bonferroni": t_crit,
        "t_crit_uncorrected": t_crit_uncorrected,
        "alpha_used": ALPHA / N_CANDIDATES,
        "n_candidates": N_CANDIDATES,
        "mde_annual_pct": mde,
        "t_blocked": t_blocked,
        "self_check": self_check,
        "caliber_note": (
            "MDE = t_crit × sd_block/√n_obs × 244。sd_block/√n_obs **就是**分块下的"
            "逐日均值标准误（sd_daily = sd_block·√k、n = n_obs·k ⇒ 两者相消）。"
            "🚨 这**不是** factor_attribution.power_floor 的 MDE —— 后者用 t_power≈2.80"
            "（80% 检出力）且按天口径，是**另一个量**，只可作带标签的并排敏感性。"
        ),
    }


# ────────────────────────────── 臂（PREREG §4） ──────────────────────────────

def arm_trend21(market):
    """前 21 行复利累计收益 > 0 ⇒ 敞口 1。前 TREND_WIN 行暖机记 0。"""
    m = np.asarray(market, dtype=float)
    expo = np.zeros(len(m))
    for t in range(TREND_WIN, len(m)):
        expo[t] = 1.0 if np.prod(1.0 + m[t - TREND_WIN:t]) - 1.0 > 0.0 else 0.0
    return expo


def arm_reversal5(market):
    """前 5 行累计收益 **< 0** ⇒ 敞口 1（方向与 A 相反，事前不选方向）。"""
    m = np.asarray(market, dtype=float)
    expo = np.zeros(len(m))
    for t in range(REVERSAL_WIN, len(m)):
        expo[t] = 1.0 if np.prod(1.0 + m[t - REVERSAL_WIN:t]) - 1.0 < 0.0 else 0.0
    return expo


def arm_vol_target(market, sigma_target):
    """exposure_t = clip(σ_target/σ̂_t, 0, 1)，σ̂_t = 前 252 行日 sd。零自由度。"""
    m = np.asarray(market, dtype=float)
    expo = np.zeros(len(m))
    for t in range(VOL_WIN, len(m)):
        sd_hat = float(np.std(m[t - VOL_WIN:t], ddof=1))
        expo[t] = float(np.clip(sigma_target / sd_hat, 0.0, 1.0)) if sd_hat > 0 else 0.0
    return expo


def arm_always_full(market):
    return np.ones(len(np.asarray(market, dtype=float)))


# role 标签（G6）：判决臂 / 敏感性臂 / 对照臂 / 安慰剂臂 四者显式区分
MAIN_ARMS = [
    {"name": "trend21", "role": "判决臂", "build": arm_trend21, "block": TREND_WIN,
     "def": "前 21 行复利累计收益 > 0 ⇒ 敞口 1，否则 0", "warmup": TREND_WIN},
    {"name": "reversal5", "role": "判决臂", "build": arm_reversal5, "block": REVERSAL_WIN,
     "def": "前 5 行累计收益 < 0 ⇒ 敞口 1，否则 0", "warmup": REVERSAL_WIN},
    {"name": "vol_target_V1", "role": "判决臂", "build": None, "block": VOL_WIN,
     "def": "clip(σ_target/σ̂_t,0,1)，σ̂_t=前 252 行日 sd；σ_target = 开头 252 行实测 sd",
     "warmup": VOL_WIN},
]


def run_arms(market, sigma_v1, sigma_v1prime):
    """构造全部臂的敞口序列。候选数不等式在这里变成响声而不是沉默。"""
    built = {
        "trend21": arm_trend21(market),
        "reversal5": arm_reversal5(market),
        "vol_target_V1": arm_vol_target(market, sigma_v1),
        "vol_target_V1prime": arm_vol_target(market, sigma_v1prime),
        "always_full": arm_always_full(market),
    }
    main = [a["name"] for a in MAIN_ARMS if a["role"] == "判决臂"]
    assert len(main) == N_CANDIDATES, (
        f"🚨 事前登记候选数 {N_CANDIDATES}，实际判决臂 {len(main)}：{main}。"
        "改 N_CANDIDATES 就等于事后改 Bonferroni 分母（第 91 层）。")
    return built


# ────────────────────────────── 无前视探针（PREREG §3） ──────────────────────────────

def lookahead_probe(market, builds, cut_points, tier_cost):
    """可 import 的问函数（第 124 层）：判决逻辑不许藏在 main() 里。

    负控：投毒 market[cut+1:] ⇒ 三个主臂的 nav[:cut+1] 必须**逐位相同**。
    正控：投毒 market[cut:]    ⇒ nav[cut+1:] 必须**发生变化**（探测器看得见那 1e9）。
    🚨 没有正控，负控可以恒真（第 20 层 / §3 逐字）。
    """
    n = len(market)
    base_nav = {k: nav_path(daily_returns(v, market, tier_cost)) for k, v in builds.items()}
    rows = []
    for cut in cut_points:
        poison_neg = np.array(market, dtype=float); poison_neg[cut + 1:] = POISON
        poison_pos = np.array(market, dtype=float); poison_pos[cut:] = POISON
        # 🚨 正控的自检：投毒真的落到数组里了，否则「看不见 1e9」会被读成「没有前视」
        # ⚠️ 判据写的是「数组里有 n 个 POISON」，不是「没有 inf」——
        # POISON=1e9 是有限数，而 np.bool_ 与 Python 的 True/False 不是同一个单例，
        # 写成 `... is False` 会恒假（探针恒假 ⇒ 正控恒绿 ⇒ 负控可以毫无阻力地通过）。
        n_poison_pos = int(np.count_nonzero(poison_pos == POISON))
        n_poison_neg = int(np.count_nonzero(poison_neg == POISON))
        poison_landed = bool(n_poison_pos == n - cut and n_poison_neg == n - cut - 1)
        row = {"cut_index": int(cut),
               "cut_date": str(market.index[cut]) if hasattr(market, "index") else None,
               "poison_value": POISON,
               "n_poisoned_pos": n_poison_pos,
               "n_poisoned_neg": n_poison_neg,
               "poison_actually_present": poison_landed,
               "arms": {}}
        for k, v in builds.items():
            nav_neg = nav_path(daily_returns(v, poison_neg, tier_cost))
            nav_pos = nav_path(daily_returns(v, poison_pos, tier_cost))
            prefix_same = bool(np.array_equal(nav_neg[: cut + 1], base_nav[k][: cut + 1]))
            tail_changed = bool(not np.array_equal(nav_pos[cut + 1:], base_nav[k][cut + 1:]))
            row["arms"][k] = {
                "prefix_bitwise_identical": prefix_same,   # 负控：必须 True
                "positive_control_tail_changed": tail_changed,  # 正控：必须 True
                "ok": bool(prefix_same and tail_changed),
            }
        rows.append(row)
    return {
        "cuts_tested": len(rows),
        "full_series_comparison": "全序列逐位比较，不抽样（PREREG §3 / 第 20 层）",
        "all_ok": bool(all(a["ok"] for r in rows for a in r["arms"].values())),
        "positive_control_present": True,
        "rows": rows,
    }


# ────────────────────────────── 安慰剂臂（PREREG §4/§5） ──────────────────────────────

def placebo_annuals(market, tier_cost, warmup, n_rep=N_PLACEBO):
    """i.i.d. Bernoulli(0.5) 逐日敞口，暖机段与被对照的主臂等长。

    🚨 种子是 20261017 + r 的**显式整数**，不许 hash(day)（PYTHONHASHSEED）。
    每个主臂各配一组安慰剂：暖机长度不同 ⇒ 不配平的话，百分位是在比日历不是比信号。
    """
    n = len(market)
    out = []
    for r in range(n_rep):
        rng = np.random.default_rng(PLACEBO_SEED_BASE + r)
        expo = np.zeros(n)
        expo[warmup:] = rng.integers(0, 2, size=n - warmup).astype(float)
        out.append(arith_annual(daily_returns(expo, market, tier_cost)))
    return np.array(out, dtype=float)


def placebo_reading(arm_value, placebo_values, mde):
    """Phipson–Smyth 读法。两侧各带 [p5,p95]，并额外判 p95−p5 < MDE。

    🚨 不许「≤ p95 ⇒ 不可区分」的单侧读法：它把 −50% 也判成不可区分 ⇒ 假绿灯。
    """
    b = placebo_values
    p_hi = (1.0 + float((b <= arm_value).sum())) / (len(b) + 1.0)
    p_lo = (1.0 + float((b >= arm_value).sum())) / (len(b) + 1.0)
    p5, p25, p50, p75, p95 = (float(x) for x in np.percentile(b, [5, 25, 50, 75, 95]))
    spread = p95 - p5
    indist = bool(spread < mde) if math.isfinite(mde) else False
    return {
        "arm_value": arm_value,
        "p_above_placebo": p_hi,
        "p_below_placebo": p_lo,
        "p_two_sided": min(1.0, 2.0 * min(p_hi, p_lo)),
        "placebo_min": float(np.min(b)), "placebo_p5": p5, "placebo_p25": p25,
        "placebo_p50": p50, "placebo_p75": p75, "placebo_p95": p95,
        "placebo_max": float(np.max(b)),
        "placebo_band_width_p95_minus_p5": spread,
        "band_narrower_than_mde": indist,
        "reading": ("与随机不可区分（[p5,p95] 带宽 < MDE）⇒ 不许报任何方向"
                    if indist else "带宽 ≥ MDE ⇒ 分布本身能分辨这个读数"),
    }


# ────────────────────────────── 判决（PREREG §5 四态，顺序是判据） ────────────

def classify_excess(excess_pp, mde_pct):
    """一个臂的「臂 − 对照」相对它自己的 MDE 落在哪一格。

    🚨 这三格是**三个不同的事实**，D3 一个标签压不住（PREREG §5 补记 2）：
    `BEATS_...` = 测到它比对照好；`RESOLVED_NEGATIVE` = **测到它比对照差**；
    `UNMEASURABLE` = 测不到（连符号都定不了）。
    前两者是**测到了**，只有第三者配得上「测不到」这句话。
    """
    if excess_pp > mde_pct:
        return "BEATS_CONTROL_BY_MORE_THAN_MDE"
    if excess_pp < -mde_pct:
        return "RESOLVED_NEGATIVE"
    return "UNMEASURABLE"


def decide(rows, control_row, verdict_arm, sensitivity_row):
    """D4 → D1 → D2 → D3。顺序本身是判据：口径冲突优先于一切结论。"""
    # D4：判决臂与敏感性臂对同一读数给相反方向
    d4 = None
    if sensitivity_row is not None and verdict_arm is not None:
        v = verdict_arm["excess_over_control_pp"]
        s = sensitivity_row["excess_over_control_pp"]
        if math.isfinite(v) and math.isfinite(s) and (v > 0) != (s > 0):
            d4 = {"arm": verdict_arm["name"], "sensitivity_arm": sensitivity_row["name"],
                  "verdict_caliber_pp": v, "sensitivity_caliber_pp": s}

    kinds = {r["name"]: classify_excess(r["excess_over_control_pp"],
                                        r["mde"]["mde_annual_pct"]) for r in rows}
    target_hits = [r for r in rows
                   if r["arith_annual_pct"] >= TARGET_ANNUAL
                   and r["arith_annual_pct"] > control_row["arith_annual_pct"]]
    mde_hits = [r for r in rows if kinds[r["name"]] == "BEATS_CONTROL_BY_MORE_THAN_MDE"]
    resolved_neg = [r for r in rows if kinds[r["name"]] == "RESOLVED_NEGATIVE"]
    unmeasurable = [r for r in rows if kinds[r["name"]] == "UNMEASURABLE"]

    if d4 is not None:
        verdict, arm = "CALIBER_CONFLICT", d4["arm"]
        why = "两个口径对同一读数给出相反方向 ⇒ 方向本身就是被口径定义的，先封口径"
    elif target_hits:
        best = max(target_hits, key=lambda r: r["arith_annual_pct"])
        verdict, arm = "MEETS_TARGET", best["name"]
        # 🚨 `arith_annual_pct` 是**分数**（`arith_annual = mean × 244`），
        # 拿它配 `%` 字样会把读数缩小 100 倍（第 97 层：字段名带 pct 而值是分数）。
        # 换算走 `printed()` 这唯一入口，且带 `/年` —— 因为它是年化量，不是逐日量。
        why = (f"{best['name']} 年化 {printed(best['arith_annual_pct'], '%')}/年 "
               f"≥ {printed(TARGET_ANNUAL, '%', nd=0)}/年 且高于对照臂")
    elif mde_hits:
        best = max(mde_hits, key=lambda r: r["excess_over_control_pp"])
        verdict, arm = "BEATS_CONTROL_NOT_TARGET", best["name"]
        # 🚨 `excess_over_control_pp` 与 `mde_annual_pct` 都是**分数**（实测
        # `excess ≡ 算术臂 − 算术对照` 逐位相等），名字里的 `_pp` 承诺不了量纲。
        # ⇒ 两边同量纲，**比较仍然正确**；错的只是印出来的每一个数字（差 100 倍）。
        why = (f"{best['name']} 超对照 {printed(best['excess_over_control_pp'], 'pp', signed=True)} > MDE "
               f"{printed(best['mde']['mde_annual_pct'], 'pp')}，"
               f"但年化未到 {printed(TARGET_ANNUAL, '%', nd=0)}/年")
    elif resolved_neg:
        # 🚨 这里是 D3 的**两个子状态之一**：没有臂超对照 MDE，但有臂**在 −MDE 之外**
        # ⇒ 那些臂是**测到了比一直满仓更差**。「测不到 ≠ 没有」这句话**不适用于它们**
        # ⇒ 所以不许在这条 why 里说「都落在 MDE 之内」（那会是假话），
        #   也不许只说「测不到」（那会把一个 t=−5.64 的结果读成没力量测）。
        verdict, arm = "POWERLESS", None
        worst = min(resolved_neg, key=lambda r: r["excess_over_control_pp"])
        why = (
            f"没有主臂超对照 MDE ⇒ 不存在 D1/D2。"
            f"但 {'、'.join(r['name'] for r in resolved_neg)} 的超额落在 −MDE 之外"
            f"（{worst['name']} {printed(worst['excess_over_control_pp'], 'pp', signed=True)} vs "
            f"MDE {printed(worst['mde']['mde_annual_pct'], 'pp')}，"
            f"t={worst['mde']['t_blocked']}）"
            f"⇒ 这些臂**被测到**比『一直满仓』更差；"
            f"{'、'.join(r['name'] for r in unmeasurable) or '其余臂'}则是测不到。"
            f"测不到 ≠ 没有 —— 但那句话只适用于测不到的臂，不适用于上面这些。"
        )
    else:
        verdict, arm = "POWERLESS", None
        why = ("所有主臂的「臂 − 对照」都落在 MDE 之内（含负向超过）"
               "⇒ 测不到 ≠ 没有：这只等于『在这个样本量下测不到』，"
               "不等于『择时无效』")

    return {
        "verdict": verdict,
        "arm": arm,                       # 🚨 D1/D2 必须钉住是哪个臂（第 129 层）
        "why": why,
        "order": "D4 → D1 → D2 → D3（顺序本身是判据）",
        "d4_caliber_conflict": d4,
        "control_annual_pct": control_row["arith_annual_pct"],
        "control_annual_compound_pct": control_row["arith_annual_compound_pct"],
        # 🚨 D3 这一支覆盖**两个子状态**，而 `verdict=POWERLESS` 这个名字只描述其中一个
        # （PREREG §5 补记 2）。不许让一个标签盖住两个事实 ⇒ 逐臂落盘是哪一格。
        "per_arm_excess_class": kinds,
        "resolved_negative_arms": [r["name"] for r in resolved_neg],
        "unmeasurable_arms": [r["name"] for r in unmeasurable],
        "d3_substate": ("RESOLVED_NEGATIVE_PRESENT" if resolved_neg
                        else "ALL_UNMEASURABLE"),
        "d3_substate_note": (
            "D3 名叫 POWERLESS，但它覆盖两格：|Δ|>MDE 且为负 = **测到了更差**，"
            "|Δ|<MDE = 测不到。本字段说的是这次落在哪一格，verdict 字段两个子态同名。"
            "加第五个判决态会是跑过数据后换判据（第 91 层）⇒ 不加，改成必报子态。"),
    }


WHAT_THIS_CANNOT_PROVE = [
    "这不是「市场择时在 A 股行不行」的判决——它只回答：在 1395 行的 150 只抽样等权"
    "market_return 序列上，这 3 个自研臂里有没有一个能到 20%/年。",
    "它不回答别的市场、别的样本期、别的抽样种子。",
    "它不是前瞻验证：全部数据都在判决窗口内；vol_target 的 σ_target 更是用到了"
    "未来（开头 252 行做校准，属 PREREG §4 事前声明的已知缺陷）。"
    "⇒ 任何「通过了」都必须附这句话。",
    "它与 market_timing_ceiling.json 是两个不同的对象：上界用块内未来函数，本文件一个"
    "未来函数都不许有。184.50%/年是上界不是判决。",
    "测不到 ≠ 没有。若判决是 POWERLESS，那只说明在这个样本量下分辨不出来，"
    "出路是加前瞻数据，不是换臂、不是改门槛。",
]


def main():
    # 🚨 第 125 层：抓在开跑之前，比对在盖章那一刻。
    expect_bytes = provenance.code_file_sha256(ROOT, GENERATOR)
    t0 = time.time()

    df = pd.read_parquet(UPSTREAM)
    market = df["market_return"]
    n = len(market)

    # 🚨 G3：成本从产物自身反推，不写字面量。判决臂取更贵的 real 档（切换频繁）。
    tiers = cost_tiers(df)
    tier_cost = float(tiers["real"]["full_turnover_single_day_cost"])

    # PREREG §4 的 V1 / V1' 裁定（事前写下，不许事后改）
    sigma_v1 = float(np.std(market.to_numpy()[:VOL_WIN], ddof=1))
    sigma_v1prime = float(np.std(market.to_numpy(), ddof=1))

    builds = run_arms(market, sigma_v1, sigma_v1prime)
    ret = {k: daily_returns(v, market, tier_cost) for k, v in builds.items()}
    control_ret = ret["always_full"]

    def stats(name, expo, role, block, arm_def, warmup):
        d = ret[name]
        exc = d - control_ret
        prev_expo = np.concatenate(([1.0], expo[:-1]))
        m = mde_and_t(exc, block)
        return {
            "name": name, "role": role, "definition": arm_def,
            "block": block, "warmup_days": warmup,
            "n_days": n,
            "exposure_mean": float(np.mean(expo)),
            "n_switches": int((np.abs(expo - prev_expo) > 0).sum()),
            "arith_annual_pct": arith_annual(d),
            "arith_annual_compound_pct": compound_annual(d),
            # 名义累计成本 = Σ|Δe|·tier。它是**公式值**，不是从净值反推的实测值
            # （第 109 层：用公式重算去「验证」产物是错的，反过来用它当读数也是错的）。
            "nominal_cost_pct_sum": float(np.sum(np.abs(expo - prev_expo) * tier_cost)),
            "excess_over_control_pp": arith_annual(exc),
            "excess_definition": "臂逐日收益 − 对照臂逐日收益（同一个量上取差，MDE 才成立）",
            "mde": m,
        }

    rows = []
    for a in MAIN_ARMS:
        rows.append(stats(a["name"], builds[a["name"]], a["role"], a["block"], a["def"], a["warmup"]))
    # 敏感性臂（显式标 role，不许代替判决臂）
    sens = stats("vol_target_V1prime", builds["vol_target_V1prime"], "敏感性臂",
                 VOL_WIN, "σ_target = 全序列 sd（PREREG §4 V1'，已知方法论缺陷）", VOL_WIN)
    control = stats("always_full", builds["always_full"], "对照臂", 1,
                    "敞口恒 1（基线，不是候选）", 0)

    # 安慰剂臂：每个主臂一组，暖机等长
    placebo = {}
    for a in MAIN_ARMS:
        b = placebo_annuals(market, tier_cost, a["warmup"])
        row = next(r for r in rows if r["name"] == a["name"])
        placebo[a["name"]] = dict(
            role="安慰剂臂", matched_arm=a["name"], warmup_days=a["warmup"],
            n_reps=N_PLACEBO, seed_rule=f"{PLACEBO_SEED_BASE} + r, r=0..{N_PLACEBO - 1}",
            null_shape="i.i.d. Bernoulli(0.5) 逐日敞口",
            **placebo_reading(row["arith_annual_pct"], b, row["mde"]["mde_annual_pct"]))

    # 无前视探针（PREREG §3）
    probe_cuts = sorted({int(round(x)) for x in np.linspace(300, n - 300, 8)})
    probe = lookahead_probe(market, {a["name"]: builds[a["name"]] for a in MAIN_ARMS},
                            probe_cuts, tier_cost)

    # 🚨 D4 必须真的能触发：verdict_arm = 判决臂 vol_target_V1，sensitivity = V1'。
    # 传 None 会让 D4 分支永不进入，而「D4 优先于 D1/D2/D3」是 PREREG §5 的顺序判据
    # —— 一个永不触发的优先分支与一条不存在的分支是同一件事（第 23 种形状）。
    verdict = decide(rows, control,
                     next(r for r in rows if r["name"] == "vol_target_V1"), sens)

    # 交叉对拍：对照臂必须与 ceiling 的 hold 读数同口径可比
    cross = {"compared_to": str(CEILING.relative_to(ROOT)),
             "ceiling_field": "ceiling.best_hold_annual_pct"}
    if CEILING.exists():
        c = json.loads(CEILING.read_text(encoding="utf-8"))
        cross["ceiling_best_hold_annual_pct"] = c["ceiling"]["best_hold_annual_pct"]
        cross["this_run_control_compound_annual_pct"] = control["arith_annual_compound_pct"]
        cross["abs_diff_pp"] = abs(c["ceiling"]["best_hold_annual_pct"]
                                   - control["arith_annual_compound_pct"]) * 100.0
        cross["note"] = (
            "只报差值不做等值断言。🚨 原先这里写「成因是块聚合 vs 逐日」——"
            "那是一句**未经验证的成因**，已撤回。实测到的只有三件事："
            "① `abs_diff_pp` 的两个操作数**都是分数**（0.10857 vs 0.09947）⇒ "
            "那个 ×100 算术是对的，0.9101 是真 pp；"
            "② 本文件的对照臂与 `market_nav_caliber.json::equal_weight.geometric_annual_pct` "
            "**逐位同一**（0.0994710351459831 × 100 = 9.94710351459831）⇒ "
            "它们是**同一个量**，只差量纲存法，不是在拿两个东西比；"
            "③ ceiling 的 hold 档 10.8572%/年**是另一个数**，与 ② 的 9.9471% 差 0.9101pp。"
            "⚠️ ③ 的成因**本文件未验出** ⇒ 不许把 0.9101pp 读成「会计误差」，"
            "也不许沿用撤回的那句成因。")

    res = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "prereg": "docs/PREREG_MARKET_TIMING_2026-10-17.md",
        "target_annual": TARGET_ANNUAL,
        "upstream": {
            "parquet": str(UPSTREAM.relative_to(ROOT)),
            "parquet_sha256": hashlib.sha256(UPSTREAM.read_bytes()).hexdigest(),
            "meta_json": str(UPSTREAM_META.relative_to(ROOT)),
            "n_days": n,
            "date_first": str(market.index[0]), "date_last": str(market.index[-1]),
            "caliber": (
                "🚨 日期轴是「涨腿幸存日」的轴（MIN_BASKET 过滤后），不是交易日历；"
                "「持有 X 天」按这个轴的行数计。"
                "🚨 market_return 是 5460 池抽 150 只的等权日收益，不是全池，"
                "不可与 market_nav_caliber.json::equal_weight.geometric_annual_pct 并列引用。"
                f"⚠️ 上游判 fresh + byte_evidence=true 但 trustworthy=false（dirty_at_stamp）。"),
        },
        "cost": {
            "tier_used_for_verdict": "real",
            "tier_cost": tier_cost,
            "source": "scripts.market_timing_ceiling.cost_tiers(df)（实测反推，非字面量）",
            "model": "cost_t = |e_t − e_{t−1}| · tier_cost，e_{−1}=1.0",
            "note": "0/1 仓位切换不换股 ⇒ 这是上界式成本（把仓位变动当满换手），"
                    "对本文件不利 ⇒ 判决单向安全。",
            "all_tiers_measured": tiers,
        },
        "annualization_calibers": {
            "arith_mean_times_244": "PREREG §5 判决臂口径",
            "compound_cumprod_annualized": "与 ceiling 同口径，用于交叉对拍",
            "why_two": "两个各自正确的数，引用必须带 role（第 7 层）。",
        },
        "sigma_target_variants": {
            "V1_verdict": {"value": sigma_v1, "calib_days": VOL_WIN,
                           "calib_range": [str(market.index[0]), str(market.index[VOL_WIN - 1])],
                           "role": "判决臂"},
            "V1prime_sensitivity": {"value": sigma_v1prime, "role": "敏感性臂",
                                    "known_defect": "用全序列（含未来）校准 ⇒ 不能算前瞻验证通过"},
        },
        "arms": rows + [sens, control],
        "placebo": placebo,
        "lookahead_probe": probe,
        "verdict": verdict,
        "what_this_cannot_prove": WHAT_THIS_CANNOT_PROVE,
        # 🚨 把「D3 的措辞必含『测不到 ≠ 没有』」从**一句散文**变成**算出来的读数**
        # （第 110 层：守卫守的是机制，我的主张说的是数据）。第一版这里只有一个
        # 字符串常量，它**自称**「D3 分支输出必含」，而实际 D3 的 `why` 里没有那句话
        # ⇒ 产物在自己的字段上发假绿。现在四条都由 `verdict` 的实际内容算出来。
        "powerless_wording_check": {
            "rule": ("D3 落到 ALL_UNMEASURABLE 时 `why` 必含「测不到 ≠ 没有」；"
                     "D3 落到 RESOLVED_NEGATIVE_PRESENT 时 `why` 必**不许**说"
                     "「都落在 MDE 之内」，且必点名为哪几个臂测到了更差"),
            "d3_substate": verdict["d3_substate"],
            "why_text": verdict["why"],
            "contains_lock_phrase": "测不到 ≠ 没有" in verdict["why"],
            "claims_all_inside_mde": "都落在 MDE 之内" in verdict["why"],
            "actually_all_inside_mde": len(verdict["resolved_negative_arms"]) == 0,
            "names_the_resolved_negative_arms": all(
                nm in verdict["why"] for nm in verdict["resolved_negative_arms"]),
        },
        "cross_check_vs_ceiling": cross,
        "runtime_seconds": round(time.time() - t0, 2),
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    provenance.stamp(OUT, GENERATOR, root=ROOT, data_root=str(ROOT / "data"),
                     expect_bytes=expect_bytes,
                     extra={"source_parquet": str(UPSTREAM.relative_to(ROOT)),
                            "source_parquet_sha256": res["upstream"]["parquet_sha256"],
                            "verdict": verdict["verdict"]})

    print(f"判决: {verdict['verdict']}  arm={verdict['arm']}  "
          f"D3子态={verdict['d3_substate']}")
    for nm in verdict["resolved_negative_arms"]:
        print(f"  🚨 {nm}: 超额在 −MDE 之外 = **测到了比一直满仓更差**"
              f"（不是「测不到」）")
    for nm in verdict["unmeasurable_arms"]:
        print(f"  · {nm}: 超额落在 MDE 之内 = 测不到")
    print(f"对照 always_full 算术年化 {printed(control['arith_annual_pct'], '%', signed=True)}/年 "
          f"复利 {printed(control['arith_annual_compound_pct'], '%', signed=True)}/年")
    for r in rows + [sens]:
        p = placebo.get(r["name"])
        line = (f"  {r['name']:<20} {r['role']:<6} "
                f"算术 {printed(r['arith_annual_pct'], '%', signed=True, width=8)}/年 "
                f"复利 {printed(r['arith_annual_compound_pct'], '%', signed=True, width=8)}/年 "
                f"超对照 {printed(r['excess_over_control_pp'], 'pp', signed=True, width=8)} "
                f"MDE {printed(r['mde']['mde_annual_pct'], 'pp', width=8)} "
                f"(n_obs={r['mde']['n_obs']}, t={r['mde']['t_blocked']})")
        if p:
            # 🚨 安慰剂的三个数也是分数，且**原来一个单位字样都没有** ——
            # `[-0.670, -0.430]` 会被读成「−67% 还是 −67%？」。真值是 −67.0%/年。
            line += (f"\n  {'':<20} 安慰剂 ["
                     f"{printed(p['placebo_p5'], '%', signed=True, nd=1)}, "
                     f"{printed(p['placebo_p95'], '%', signed=True, nd=1)}]/年 "
                     f"带宽 {printed(p['placebo_band_width_p95_minus_p5'], 'pp', nd=1)} "
                     f"{'<' if p['band_narrower_than_mde'] else '>='} MDE "
                     f"=> {'不可分辨方向' if p['band_narrower_than_mde'] else '可分辨'}")
        print(line)
    print(f"无前视探针 all_ok={probe['all_ok']}（{probe['cuts_tested']} 个切点，"
          f"{len(MAIN_ARMS)} 个主臂，含正控）")
    print(f"→ {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()

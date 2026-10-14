"""因子归因 —— 逐日毛超额能被组合自身的 8 个因子暴露解释多少？

**判据已预注册**：`docs/TODO_factor_attribution_2026-10-15.md`
阈值、模型、判决分支全在那边写死。**本脚本只负责执行，不负责判定口径。**

## 为什么要用野自助而不是查表

2026-10-15（台账第 85 条）：我手打的临界值表 `crit[n-2]` 索引错了一格
（0.878 vs 正确的 0.8114），而且同一段文字先宣称「用闭式不查表」
后面又写了张表。⇒ **本仓没有 scipy，本脚本也不允许出现任何硬编码临界值。**

野自助（wild bootstrap，Rademacher 权重）的全部输入是**数据本身**：
没有分布假设、没有分位数表、没有插值。
本仓唯一的例外是 numpy 的线性代数，这部分是可验证的。

## 三个刻意写死的自我限制

1. **`R²` 不进判决**。选股就是按这几个因子打分，R² 高是设计的必然结果
   （预注册 §4.2）。把它写进输出是为了让后来的人**看见它高**，
   同时看见它**不算数**。
2. **`α` 显著为正不算能力**。它是样本内选股的过拟合残差（§3.4）。
3. **读不到 parquet 就拒绝出判决**，绝不回落到「没有显著因子」。
   「没跑」和「跑了没发现」必须能分开 —— 这是台账第 7 条。

## 用法

```bash
.venv/bin/python scripts/factor_attribution.py                 # 5 档全跑
.venv/bin/python scripts/factor_attribution.py --arms 21       # 只跑 k=21
```
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]

# ── 预注册常量（全部来自 docs/TODO_factor_attribution_2026-10-15.md）──
Y_COL = "gross_excess"
BOOTSTRAP_B = 2000
BOOTSTRAP_SEED = 20261015
THRESHOLD_JOINT_P = 0.05
THRESHOLD_FACTOR_P = 0.05 / 8          # Bonferroni，8 个因子
SUBPERIOD_SPLIT = "2024-01-01"         # 2021–2023 / 2024–2026
REQUIRED_ARMS = [5, 10, 21, 42, 63]
PRIMARY_ARM = 21

ARMS = ["k21", "k5", "k10", "k42", "k63"]

# ★ 把「不许当成验收标准的东西」提成模块级常量。
#   写成 `main()` 里的字面量字典时，它就只是**输出里的一个字段**，
#   测试只能读 JSON 才能检查 —— 而 JSON 是产物，不是契约。
#   提成常量后，守卫直接 `import` 它，改它就会让测试红。
RESULT_METADATA = {
    "model": "gross_excess_t ~ 1 + Σ β_i·expo_i,t",
    "NOT_a_criterion": {
        "r2": "R² 高是设计的必然结果（选股即按这几因子打分），**不进判决**",
        "alpha": "截距显著为正 = 样本内过拟合残差，**不算能力**",
    },
}
# 出现在阈值里就是违规 —— 守卫逐个查这个元组。
THRESHOLDS_FORBIDDEN = ("r2", "alpha", "ic", "hit_rate", "r_squared")

# 真正生效的阈值。守卫断言它与 THRESHOLDS_FORBIDDEN **无交集** ——
# 「R² 高不算证据」只有在这两个集合不相交时才是一件可执行的事。
THRESHOLDS = {
    "joint_p": THRESHOLD_JOINT_P,
    "factor_p_bonferroni": THRESHOLD_FACTOR_P,
    "require": "跨 5 档同号 + 跨子期同号",
}


def parquet_path(k: int) -> Path:
    return ROOT / "data" / "reports" / f"wf_weights_backtest_k{k}.daily.parquet"


# ── 回归 ──────────────────────────────────────────────────────────────
def _ols(X: np.ndarray, y: np.ndarray) -> dict:
    """普通最小二乘。返回 β、残差、协方差、σ²、R²。

    自己算是因为本仓没有 statsmodels；这也是好事 ——
    少一个「统计库默认做了什么我不知道」的黑箱。
    """
    n, p = X.shape
    XtX_inv = np.linalg.pinv(X.T @ X)
    beta = XtX_inv @ (X.T @ y)
    resid = y - X @ beta
    dof = n - p
    sigma2 = float(resid @ resid / dof) if dof > 0 else float("nan")
    s2_xtxinv = sigma2 * XtX_inv
    se = np.sqrt(np.clip(np.diag(s2_xtxinv), 0.0, None))
    t = np.divide(beta, se, out=np.full(p, np.nan), where=se > 0)
    sst = float(((y - y.mean()) ** 2).sum())
    sse = float(resid @ resid)
    r2 = 1.0 - sse / sst if sst > 0 else float("nan")
    return {
        "beta": beta, "resid": resid, "se": se, "t": t,
        "sigma2": sigma2, "r2": float(r2), "XtX_inv": XtX_inv,
        "dof": dof, "n": n,
    }


def wild_bootstrap(X: np.ndarray, y: np.ndarray, B: int, seed: int) -> dict:
    """H0「所有 β = 0」下的野自助零分布。**只需要受限模型（截距）的残差。**

    ★ 全程向量化：B 个伪样本一次性解完，不是循环 B 次 lstsq。
      B=2000 × n=1394 × p=9 在 numpy 下一秒内完成。

    推导（说明为什么不需要重新拟合任何东西）：
        y* = ŷ₀ + ê ⊙ w = X·β̂₀ + (ê ⊙ w)
    ⇒ β̂₁* = (X'X)⁻¹ X'y* = β̂₀ + (X'X)⁻¹ X'(ê ⊙ w)
    ⇒ **全模型在 y* 上的 OLS 就是这一行**。
    ⇒ 残差 σ²* 同样一次算完（用残差矩阵 R·Y* 的行平方和）。

    这里**不**重新拟合受限模型是对的：投影运算天然把它算进去了
    （预注册 §3.1）。
    """
    n, p = X.shape
    ybar = float(y.mean())
    e_hat = y - ybar                       # 受限模型（只有截距）的残差

    rng = np.random.default_rng(seed)
    W = rng.choice(np.array([-1.0, 1.0]), size=(B, n))   # (B, n)
    Y_star = ybar + e_hat[None, :] * W     # (B, n)

    XtY = X.T @ Y_star.T                   # (p, B)
    XtX_inv = np.linalg.pinv(X.T @ X)
    beta_star = XtX_inv @ XtY              # (p, B)

    # 残差：Y_star − X β*，向量化
    R = Y_star - (beta_star.T @ X.T)       # (B, n)
    sigma2_star = (R ** 2).sum(axis=1) / (n - p)          # (B,)
    se_star = np.sqrt(np.clip(np.outer(sigma2_star, np.diag(XtX_inv)), 0.0, None))
    t_star = np.divide(beta_star.T, se_star,
                       out=np.full((B, p), np.nan), where=se_star > 0)  # (B, p)

    # 联合 F = (R²_u/k) / ((1−R²_u)/(n−k−1))，k 为斜率个数
    R2_star = 1.0 - ((R ** 2).sum(axis=1) / ((y - ybar) ** 2).sum())   # (B,)
    k = p - 1
    F_star = np.divide((R2_star / k), (1.0 - R2_star) / (n - k - 1))
    F_star = np.where(np.isfinite(F_star) & (F_star >= 0), F_star, np.nan)

    return {"t_star": t_star, "F_star": F_star, "B": B}


def analyze(df: pd.DataFrame, factor_cols: list[str], label: str) -> dict:
    """在 `df`（行=交易日）上跑一次完整归因。"""
    X = np.column_stack([np.ones(len(df))] + [df[c].to_numpy(float) for c in factor_cols])
    y = df[Y_COL].to_numpy(float)
    n, p = X.shape
    if n <= p + 2:
        return {"error": f"{label}: n={n} 太小，p={p}，不构成检验", "verdict": None}

    fit = _ols(X, y)
    boot = wild_bootstrap(X, y, BOOTSTRAP_B, BOOTSTRAP_SEED)

    # ★ 联合 F 必须与 `wild_bootstrap` 里的 F_star **同一口径**：
    #     F = (R²/k) / ((1−R²)/(n−k−1))，k = 斜率个数。
    # 第一版这里写的是 `max(t²)` —— 那是「最强的单个因子」，
    # 不是「所有斜率同时为零」。两侧口径不同 ⇒ p 值是拿两把尺子量的。
    # 这条与「成本被扣两次」同源：**判断量与被判断量必须用同一个公式。**
    k = p - 1
    F_obs = (fit["r2"] / k) / ((1.0 - fit["r2"]) / (n - k - 1))
    joint_p = float(np.mean(boot["F_star"] >= F_obs))

    factors = {}
    for i, c in enumerate(factor_cols, start=1):
        p_i = float(np.mean(np.abs(boot["t_star"][:, i]) >= abs(fit["t"][i])))
        factors[c] = {
            "beta": float(fit["beta"][i]),
            "se": float(fit["se"][i]),
            "t": float(fit["t"][i]),
            "boot_p": p_i,
            "passes_bonferroni": bool(p_i < THRESHOLD_FACTOR_P),
        }

    return {
        "label": label, "n": n, "period": [str(df.index[0]), str(df.index[-1])],
        "alpha": {
            "value": float(fit["beta"][0]),
            "t": float(fit["t"][0]),
            "boot_p": float(np.mean(np.abs(boot["t_star"][:, 0]) >= abs(fit["t"][0]))),
        },
        "joint_F": float(np.nanmax(F_obs)),
        "joint_p": joint_p,
        "factors": factors,
        "_beta": {c: float(fit["beta"][i]) for i, c in enumerate(factor_cols, start=1)},
    }


def sign_consistency(vals: list[float]) -> dict:
    """一组系数值的符号是否全一致（**零算不一致** —— 零不是「支持」）。"""
    s = [int(np.sign(v)) if v != 0 else 0 for v in vals]
    nz = [x for x in s if x != 0]
    consistent = len(nz) == len(s) and len(set(s)) == 1
    return {"signs": s, "consistent": bool(consistent),
            "sign": s[0] if consistent else None}


def _norm_cdf(x: float) -> float:
    """标准正态 CDF。`math.erf` 在**标准库**里 —— 不需要 scipy，也不需要表。"""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_ppf(p: float, lo: float = -12.0, hi: float = 12.0) -> float:
    """标准正态分位数 = `_norm_cdf` 的反函数，二分求解。

    ★ 为什么连这两个数都要反解，而不是直接写 `1.96` / `2.80`：
      台账第 85 条 —— 我手打的临界值表 `crit[n-2]` 索引错了一格
      （0.878 vs 正确的 0.8114），而且同一段文字先宣称「用闭式不查表」
      后面又写了张表。
      ⇒ **手打的临界值就是本仓已被证伪过一次的做法**。
      ⇒ `math.erf` 属于标准库，反解没有任何「查表」成分。
    """
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if _norm_cdf(mid) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


# 显著性水平与检出力是**设计选择**（可以谈），临界**数值**是推导结果（不能手打）。
ALPHA_TWO_SIDED = 0.05
POWER_TARGET = 0.80
T_CRIT_5PCT = _norm_ppf(1.0 - ALPHA_TWO_SIDED / 2.0)          # ≈1.95996
# 80% 检出力 ⇒ δ = t_crit − Φ⁻¹(1−power)；两者都由 erf 反解，无字面量。
T_POWER_TARGET = T_CRIT_5PCT - _norm_ppf(1.0 - POWER_TARGET)  # ≈2.80158


def power_floor(daily: np.ndarray, days_per_year: int = 244,
                t_crit: float | None = None, t_power: float | None = None) -> dict:
    """★ 这份回测**能测出多大的效应**（Minimum Detectable Effect）。

    ## 为什么这是整个分析里最该先算的数

    「没测出显著」有两种完全不同的成因：
      (a) 效应真的不存在；
      (b) 效应存在，但样本量不够分辨。
    **只有算出 MDE 才知道自己落在哪一边。**

    而 MDE 与波动成正比 —— 所以**优化目标不是「把收益提到 20%」，
    是「在同样均值下把波动压下来」**。这两件事需要的动作完全不同。

    ## 判据来自外部锚，不查表

    `t_crit = 1.96`（5% 双尾）、`t_power = 1.96 + 0.84 = 2.80`（80% 检出力，
    0.84 是正态分布在两者之间的面积，标准做法）。**都不来自本仓实现**。

    顺带回答「要多久才够」：把当前效应做成 t>2.80 所需的交易日数。
    """
    t_crit = T_CRIT_5PCT if t_crit is None else t_crit
    t_power = T_POWER_TARGET if t_power is None else t_power
    n = len(daily)
    sd = float(np.std(daily, ddof=1))
    se = sd / np.sqrt(n)
    effect = float(np.mean(daily))
    mde = t_power * se
    t_obs = effect / se if se > 0 else float("nan")
    # ★ 年化因子**三者不同**（2026-10-15 修复，之前 sd 也用了 ×T）：
    #   均值 ×T、标准差 ×√T、SE ×T。
    #   SE 之所以是 ×T：SE_annual = sd_annual/√(n/T) = (sd·√T)·√(T/n) = sd·T/√n = SE_day·T。
    #   MDE = t_power·SE，跟着 SE 走 ×T，**也是对的**。
    #   旧代码把 sd 也写成 ×T ⇒ 报告印「sd = 205%/年」，真值 13.15%/年（差 √244）。
    #   它没污染判决（decide() 不读它；σ(N) 比值里 √T 约掉），受损的只是印出来的数。
    #   守卫：`tests/test_annualization_is_a_sqrt_of_time.py`
    return {
        "n": n, "daily_sd": sd, "daily_se": se,
        "annual_mean_pct": effect * days_per_year,
        "annual_sd_pct": sd * math.sqrt(days_per_year),
        "annual_se_pct": se * days_per_year,
        "annual_tilt_note": ("均值×T、标准差×√T、SE×T —— 三者不同，"
                             "把 sd 写成 ×T 会让 sd 与 SE 的比值失真 √T 倍"),
        "t_naive": float(t_obs),
        "MDE_annual_pct": mde * days_per_year,
        "detectable": bool(abs(effect) > mde),
        "days_needed_for_80pct_power": (
            int(np.ceil(n * (t_power / t_obs) ** 2)) if np.isfinite(t_obs) and t_obs != 0
            else None),
        "thresholds_from": (f"t_crit={T_CRIT_5PCT:.5f}（5%% 双尾）、"
                            f"t_power={T_POWER_TARGET:.5f}（80%% 检出力）"
                            f"—— 均由 math.erf 二分反解，无查表、无手打字面量"),
        "caveat": ("这是**按天**的口径。K 日调仓时真实独立观测只有 n/K，"
                   "报告里的分块 t 才是诚实的那一个。"),
    }


# ★ 判决分支的**严厉程度降序**。单独抽成常量，是为了让「哪条更响」
#   **不藏在源码书写位置里** —— 2026-10-15 实测：`NO_LEVER`（联合不显著，
#   8 个因子解释不掉逐日毛超额）被排在 `CANDIDATE`（「有因子三处同号，候选，
#   非证明」）**后面**，于是**唯一能报喜的分支遮住了最响的否定分支**。
#   两者**同时为真**的比例极高（随机采样 78.8%，审计 agent 独立测得 53.99%），
#   换顺序后 100% 的重叠案例判决改变。
#   ⇒ 与 `scripts/placebo_control.py:95` 那次**完全同构，只是方向写反了**：
#     那次是「否定遮住肯定」，这次是「肯定遮住否定」。两种都错。
#
#   为什么顺序本身就是判据：四个条件**互不排斥**，
#   而 `if/elif` 取第一条 ⇒ 书写顺序 = 优先级。
#   ⇒ 必须显式写下来，并让守卫穷举验证。
_BRANCH_ORDER = (
    "NO_LEVER",            # 联合不显著：最强的否定 —— 什么都不成立
    "NO_SINGLE_FACTOR",    # 联合显著但拆不出单因子
    "OVERFIT",             # 有显著因子但符号不一致：不得使用
    "CANDIDATE",           # 「候选，非证明」—— 唯一能报喜的，放最后
)

_BRANCH_WHY = {
    "NO_LEVER": "联合不显著：8 个因子解释不掉逐日毛超额",
    "NO_SINGLE_FACTOR": "联合显著但无因子过 Bonferroni：拆不出单因子",
    "OVERFIT": "显著的因子跨档/跨子期符号不一致：过拟合，不得使用",
}


def decide_all(primary: dict, cross: dict) -> dict:
    """**全部**条件各自求值，按 `_BRANCH_ORDER` 取第一条命中。

    ★ 为什么不写成 `if/elif`：四个条件互不排斥，而 `if/elif` 只留第一条 ——
      **被遮住的条件连痕迹都不留**，读产物的人看不出「其实还有别的分支也成立」。
      这里把每条都记进 `also_true`，让重叠**在产物里可见**。
    """
    pf = primary.get("factors", {})
    passed = [c for c, v in pf.items() if v.get("passes_bonferroni")]
    consistent_pass = [c for c in passed if cross.get(c, {}).get("consistent")]

    # ★ `joint_p` 在 `primary` 上，不在 `factors` 里。
    #   第一版写成 `pf.get("joint_p", 1.0)` ⇒ 永远拿到默认的 1.0
    #   ⇒ `>= 0.05` 恒真 ⇒ NO_LEVER 成了**无条件**分支，
    #   另外 3 个分支永远走不到。
    #   一个只有一条分支的判决函数，看起来仍然「会判决」。
    # ★ 条件**保持预注册 §3.4 的原样** —— 本次只修**顺序**，不动判据。
    #   （我一度给每条加上 `joint_sig and ...` 前缀，理由是"更自足"，
    #     但那会改变判决口径：旧语义下「联合不显著 + 有过线因子 + 不一致」
    #     报 OVERFIT，新语义下一律报 NO_LEVER。那是**改判据**，触发预注册
    #     铁律，不属于本次修顺序的范围。⇒ 撤销。）
    hit = {
        "NO_LEVER": primary.get("joint_p", 1.0) >= THRESHOLD_JOINT_P,
        "NO_SINGLE_FACTOR": not passed,
        "OVERFIT": bool(passed) and not consistent_pass,
        "CANDIDATE": bool(consistent_pass),
    }
    verdict = next((b for b in _BRANCH_ORDER if hit[b]), None)
    why = _BRANCH_WHY.get(verdict, "")
    if verdict == "CANDIDATE":
        why = f"有因子三处同号：{', '.join(consistent_pass)}（候选，非证明）"
    out = {"verdict": verdict, "why": why,
           "also_true": [b for b in _BRANCH_ORDER if b != verdict and hit[b]]}
    return out


def decide(primary: dict, cross: dict) -> tuple[str | None, str]:
    """预注册 §3.4 的判决分支。**提成独立函数是为了让守卫能直接调它** ——
    测试里把同样的筛选逻辑再写一遍，验的只是概念，变异实现它抓不到
    （与「测试重实现被测逻辑」是同一类假绿）。
    """
    r = decide_all(primary, cross)
    return r["verdict"], r["why"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms", type=str, default=None,
                    help="逗号分隔，如 21 或 5,21；默认全部 5 档")
    ap.add_argument("--out", type=str,
                    default=str(ROOT / "data/reports/factor_attribution.json"))
    args = ap.parse_args(argv)

    arms = REQUIRED_ARMS if not args.arms else [int(x) for x in args.arms.split(",")]

    loaded, missing = {}, []
    for k in arms:
        p = parquet_path(k)
        if not p.is_file():
            missing.append(f"k{k}（{p.name} 不存在）")
            continue
        df = pd.read_parquet(p)
        if Y_COL not in df.columns:
            missing.append(f"k{k}（缺 {Y_COL} 列）")
            continue
        cols = [c for c in df.columns if c.startswith("expo_")]
        if not cols:
            missing.append(f"k{k}（没有 expo_* 列）")
            continue
        df = df.sort_index()
        loaded[k] = (df, cols)

    # ★ 判决要求「跨 **5** 档同号」。所以缺的必须是 REQUIRED_ARMS 的全量，
    #   **不能**只数 `--arms` 里请求的那几档 —— 否则 `--arms 21` 会用一个档
    #   就发出判决，把 §3.3 这条主要防线绕过去。
    #
    # ⚠️ 第一版这里写成 `if k not in loaded` —— 而 `loaded` 里只有**被请求**的档，
    #    于是 `--arms 21` 会把**磁盘上明明存在**的 k5 报成「不存在」。
    #    症状与「守卫有洞」一模一样：一条红/一个报错，但方向相反。
    #    正确的判据是**产物可得性**，不是「这轮有没有被请求」。
    def _available(k: int) -> bool:
        p = parquet_path(k)
        if not p.is_file():
            return False
        try:
            cols = pd.read_parquet(p).columns
        except Exception:
            return False
        return Y_COL in cols and any(c.startswith("expo_") for c in cols)

    missing_required = [f"k{k}（{parquet_path(k).name} 不可用）"
                        for k in REQUIRED_ARMS if not _available(k)]

    print(f"因子归因 · 预注册 docs/TODO_factor_attribution_2026-10-15.md")
    print(f"野自助 B={BOOTSTRAP_B} 种子={BOOTSTRAP_SEED}｜"
          f"联合 p<{THRESHOLD_JOINT_P}｜单因子 p<{THRESHOLD_FACTOR_P:.6f}（Bonferroni）")
    print("─" * 96)

    if not loaded:
        print(f"🚨 一档都没读到（缺：{', '.join(missing)}）—— **拒绝出判决**。")
        print("   「没跑」和「跑了没发现」必须能分开，见预注册 §5。")
        # ★ 拒绝出判决 ≠ 拒绝留证据。
        #   第一版在这里直接 `return 2`，磁盘上什么都没有 ——
        #   于是「这一轮拒绝过」和「这一轮没跑」在事后无法区分。
        #   落一份 verdict=null 的产物，把拒绝的理由钉在时间戳上。
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps({
            "generated": date.today().isoformat(),
            "prereg": "docs/TODO_factor_attribution_2026-10-15.md",
            "refused": True,
            "refusal_reason": f"没有一档可读（{', '.join(missing)}）——拒绝出判决",
            "arms_run": [], "arms_missing": missing,
            "arms_missing_required": missing_required,
            "verdict": None, "verdict_branch": None,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"→ {args.out}（refused=true，verdict=null）")
        return 2

    factor_cols = sorted(loaded[PRIMARY_ARM][1]) if PRIMARY_ARM in loaded \
        else sorted(next(iter(loaded.values()))[1])

    # ── 每个 K 档：全期 + 两个子期 ──
    results = {}
    for k, (df, _) in sorted(loaded.items()):
        full = analyze(df, factor_cols, f"k{k}·全期")
        first = analyze(df[df.index < SUBPERIOD_SPLIT], factor_cols, f"k{k}·前半")
        second = analyze(df[df.index >= SUBPERIOD_SPLIT], factor_cols, f"k{k}·后半")
        results[f"k{k}"] = {"full": full, "sub_first": first, "sub_second": second}

        f = full.get("factors", {})
        top = sorted(f.items(), key=lambda kv: kv[1]["boot_p"])[:3]
        print(f"K={k:<3} n={full['n']:<5} 联合F={full['joint_F']:.2f} "
              f"p={full['joint_p']:.4f}{'  ✅显著' if full['joint_p'] < THRESHOLD_JOINT_P else '  ✗'}"
              f"   过Bonferroni的因子数={sum(1 for v in f.values() if v['passes_bonferroni'])}/8")
        for name, v in top:
            mark = "★" if v["passes_bonferroni"] else " "
            print(f"      {mark} {name:<18} β={v['beta']:+.5f} t={v['t']:+6.2f} p={v['boot_p']:.4f}")

    # ── 跨 K 档 / 跨子期的符号一致性（预注册 §3.3 的主要防线）──
    # 只用**真的跑出了系数**的档。某个档 `analyze` 返回 error 时把它排掉，
    # 而不是让 KeyError 冒上去 —— 但要在输出里记下排掉了谁。
    usable = [f"k{k}" for k in sorted(loaded) if "_beta" in results[f"k{k}"]["full"]]
    skipped = [f"k{k}" for k in sorted(loaded) if f"k{k}" not in usable]
    if skipped:
        print(f"⚠️ 跳过 {', '.join(skipped)}（样本太小，未产生系数）")

    cross = {}
    for c in factor_cols:
        arms_b = [results[a]["full"]["_beta"][c] for a in usable]
        sub_b = [results[a][p]["_beta"][c]
                 for a in usable for p in ("sub_first", "sub_second")
                 if "_beta" in results[a][p]]
        sc_arms = sign_consistency(arms_b)
        sc_sub = sign_consistency(sub_b)
        cross[c] = {"across_arms": sc_arms, "across_subperiods": sc_sub,
                    "consistent": sc_arms["consistent"] and sc_sub["consistent"],
                    "n_arms": len(arms_b), "n_subperiods": len(sub_b)}

    print("─" * 96)
    print("符号一致性（候选杠杆必须三处同号）：")
    for c, v in cross.items():
        mark = "★" if v["consistent"] else " "
        print(f"  {mark} {c:<18} 跨K档{v['across_arms']['signs']} "
              f"跨子期{v['across_subperiods']['signs']}")

    # ── 判决（分支写死在预注册 §3.4）──
    primary_key = f"k{PRIMARY_ARM}"
    verdict, branch = None, None
    also_true: list[str] = []
    if primary_key in results:
        d = decide_all(results[primary_key]["full"], cross)
        verdict, branch = d["verdict"], d["why"]
        also_true = d["also_true"]
    else:
        branch = f"主档 k{PRIMARY_ARM} 未读到 —— 判决需要主档"

    blocked = list(missing_required)
    if blocked:
        branch += (f"｜🚨 缺必需档 {', '.join(blocked)} ⇒ 跨 5 档同号这条防线没跑完，"
                   f"判决**暂不生效**")
        verdict = None

    print("─" * 96)
    print(f"判决分支：{branch}")
    if verdict:
        print(f"判决：{verdict}")

    # ── 落盘 ──
    def strip(o):
        if isinstance(o, dict):
            return {k: strip(v) for k, v in o.items() if k != "_beta"}
        if isinstance(o, list):
            return [strip(v) for v in o]
        return o

    out = {
        "generated": date.today().isoformat(),
        "prereg": "docs/TODO_factor_attribution_2026-10-15.md",
        "model": RESULT_METADATA["model"],
        "n_obs": results[f"k{sorted(loaded)[0]}"]["full"]["n"] if results else 0,
        "n_factors": len(factor_cols),
        "bootstrap": {"method": "wild (Rademacher)", "B": BOOTSTRAP_B,
                      "seed": BOOTSTRAP_SEED,
                      "why": "本仓无 scipy；不用任何硬编码临界值表"},
        "thresholds": THRESHOLDS,
        "NOT_a_criterion": RESULT_METADATA["NOT_a_criterion"],
        "power_floor": {f"k{k}": power_floor(loaded[k][0][Y_COL].to_numpy(float))
                        for k in sorted(loaded)},
        "arms_run": sorted(loaded),
        "arms_missing": missing,
        "arms_missing_required": missing_required,
        "cross_consistency": cross,
        "results": strip(results),
        "verdict": verdict,
        "verdict_branch": branch,
        # ★ 被判决顺序遮住、但**条件同样成立**的分支。
        #   四个条件互不排斥，而判决只取 `_BRANCH_ORDER` 里第一条命中的 ——
        #   不记这一条，被遮住的条件**连痕迹都不留**，读产物的人看不出
        #   「其实 CANDIDATE 也成立了，只是排在后面」。
        "verdict_also_true": also_true,
        "verdict_branch_order": list(_BRANCH_ORDER),
        "limitations": [
            "全样本内：权重用这 6 年拟合的，β 量级必然偏乐观；只有符号一致性有参考价值",
            "样本期单一市场环境，实现溢价不等于未来实现溢价",
            "本实验不构成任何前瞻证据；候选杠杆须另走注册即冻结流程",
        ],
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n→ {args.out}")
    return 0 if verdict else 3


if __name__ == "__main__":
    sys.exit(main())

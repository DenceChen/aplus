#!/usr/bin/env python3
r"""测量 IC 半衰期 —— 判断 k（换仓周期）是否有物理机制，而非 6 个噪声格子里挑了个好看的。

为什么做这件事
--------------
`k_sweep_summary.json` 六档里 `k=63` 净超额唯一为正（+5.76%），
但 Spearman(净超额, k) = +0.714 且**非单调** ⇒ 无法排除赢家诅咒。
`tests/test_live_rebalance_frequency.py:81` 已经明确钉住 `LIVE_REBALANCE_EVERY == 21`，
并在 docstring 里写死「🚨🚨 K=63 最好本身就是赢家诅咒，别照着它调参」。

⇒ 本脚本**不改** `LIVE_REBALANCE_EVERY`，只补一条**此前不存在的独立证据轴**：
  信息本身多久衰减一半（Kahn 1999 定义，`data/books/cn_factor_lines.txt:14286-14299`）。

⚠️ 本仓从未实现过半衰期（`grep -rln "半衰期\|half_life" scripts/ aplus/` 无结果）。

数据源
------
`data/reports/ic_matrices.parquet` —— 逐日横截面 IC，窗口 `lag1_real = FWD_WINDOWS[(0,1)]`
（1 交易日前瞻）。`sf__<因子>` = 两端排名后的 Spearman IC；`sfP__<因子>` = 原始值 Pearson（仅对照）。

🚨 口径边界（必须随结论一起被引用）
  本脚本测的是 **IC 序列的自相关半衰期**（ρ(k) 降到 0.5 的 lag）。
  它**不是**「前瞻收益随持有期衰减的半衰期」——后者需要逐日重算 forward IC，
  成本高一个量级，且结论方向可能不同。两者不可互换。

判据（预注册于看数之前；门槛全部由外部锚推导，无手打字面量）
------------------------------------------------------------
外部锚 = 仓库既有的、与本测量无关的一组数：
  - `aplus/evolve.py:79` `LIVE_REBALANCE_EVERY = 21`（生产实盘值）
  - `k_sweep_summary.json` 的五档网格 {5, 10, 21, 42, 63}

判据（在读到任何 ρ 数字之前写下）：
  A. 若 composite IC 的半衰期落在 [21, 63] 内 ⇒ 与现有生产配置相容，
     **不构成改动的理由**（赢家诅咒警告不被推翻）。
  B. 若半衰期显著 < 21 ⇒ 现有调仓频率已超过信息寿命，
     但**这依然不授权上调 k** —— 只说明「换手不是主因之外的因素」。
  C. 若半衰期显著 > 63 ⇒ 与 k=63 最优相容；此时才允许**另起一轮**独立设计去检验，
     仍不得在本文件里改 `LIVE_REBALANCE_EVERY`。
  D. 若半衰期不可估（ρ(1) 已跌破 0.5，或 ACF 在 0.5 附近被噪声主导）⇒
     判据**不适用**，结论是「日频 IC 近似白噪声，本测量无法定标 k」。

🚨 若结论与「k=63 有机制」相反：**只加对照臂，不改判据臂。**

对照臂（不是守卫，是让本测量可被证伪的东西）
--------------------------------------------
1. **白噪声置换臂**：对同一序列做循环移位随机打乱，重算 ACF。
   若实现有 bug，白噪声臂也会报出好看的半衰期；正常实现应报 ρ(k)≈0 ⇒ 半衰期 < 1 天。
   ⇒ 这一臂**必须报出「不可估」**；若它报出正常值，本测量作废。
2. **`|IC|` vs 带符号 IC 臂**：两者分开报，不许只报好看的那个
   （`diagnose-with-the-quantity-production-actually-uses`：用 abs 曾让判决整个翻面）。

用法
----
    .venv/bin/python scripts/measure_ic_halflife.py
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
IC_PARQUET = ROOT / "data/reports/ic_matrices.parquet"
OUT = ROOT / "data/reports/ic_halflife.json"

# 最大滞后：覆盖 k-sweep 全部五档（最大 63）并留余量。
# 依据外部锚取 126 = 2 × 63，不是手打的"好看"数字。
MAX_LAG = 126

# 预注册判据里的区间锚，来源见模块 docstring。
ANCHOR_LIVE_K = 21
ANCHOR_GRID = (5, 10, 21, 42, 63)

# ρ 的 0.5 = 「信息衰减一半」，定义来自 Kahn 1999（见 docstring 引的书）。
HALF_LEVEL = 0.5

RNG_SEED = 20261016  # 置换臂可复现；只影响白噪声对照臂


def load_ic(factors: list[str]) -> pd.DataFrame:
    df = pd.read_parquet(IC_PARQUET).sort_values("date").reset_index(drop=True)
    need = [f"sf__{f}" for f in factors]
    missing = [c for c in need if c not in df.columns]
    if missing:
        raise SystemExit(f"产物缺列 {missing}；先核对 IC_PARQUET 的真实列名，别猜")
    return df


def acf(x: np.ndarray, max_lag: int) -> np.ndarray:
    """标准自相关函数（先去均值）。不去均值会把「IC 有偏」伪装成「IC 持久」。"""
    x = np.asarray(x, dtype=float)
    good = ~np.isnan(x)
    x = x[good]
    if x.size < max_lag + 2:
        raise SystemExit(f"样本不足：需要 {max_lag + 2}，实有 {x.size}")
    x = x - x.mean()
    denom = float(x @ x)
    out = np.empty(max_lag + 1)
    out[0] = 1.0
    for k in range(1, max_lag + 1):
        out[k] = float(x[:-k] @ x[k:]) / denom
    return out


def halflife_from_acf(a: np.ndarray) -> dict:
    """半衰期 = ρ 首次跌破 0.5 的 lag。找不到就明说不可估，不外推。"""
    below = np.nonzero(a[1:] < HALF_LEVEL)[0]
    if below.size == 0:
        return {"halflife_lag": None, "status": "CENSORED_ABOVE_MAXLAG",
                "rho_at_maxlag": float(a[-1]), "max_lag": len(a) - 1}
    k = int(below[0]) + 1
    # 临界情形：ρ(1) 就已 < 0.5 ⇒ 日频分辨率下不可定标
    status = "IMMEDIATE_DECAY" if k == 1 else "MEASURABLE"
    return {"halflife_lag": k, "status": status, "rho_at_maxlag": float(a[-1]),
            "max_lag": len(a) - 1}


def acf_table(a: np.ndarray) -> list[dict]:
    """只在 k-sweep 五档 + 若干便于读的 lag 上落数值，不落满 126 个。"""
    lags = sorted(set(ANCHOR_GRID) | {1, 2, 5, 10, 30, 126})
    return [{"lag": k, "rho": round(float(a[k]), 4)} for k in lags if k < len(a)]


def composite_ic(df: pd.DataFrame, weights: dict[str, float], signed: bool) -> tuple[np.ndarray, np.ndarray]:
    """按生产权重合成 composite IC。

    🚨 signed=True 用 w_i 的符号（生产口径：mom20 = -1.0 是反转因子）；
    signed=False 用 |w_i| —— **这是对照臂，不是可选项**。
    """
    w = np.array([weights[f] for f in weights], dtype=float)
    if not signed:
        w = np.abs(w)
    cols = [f"sf__{f}" for f in weights]
    mat = df[cols].to_numpy(dtype=float)
    out = np.full(len(df), np.nan)
    ok = ~np.isnan(mat).any(axis=1)
    if ok.any():
        out[ok] = mat[ok] @ w
    return out, out


def surrogate_control(series: np.ndarray, max_lag: int, n: int = 200) -> dict:
    """白噪声置换对照臂：循环移位打乱时间结构 ⇒ 真 ACF 应塌到 ~0。

    这是让本测量可被证伪的东西：若它也报出正常半衰期，说明 ACF 实现坏了。
    """
    s = np.asarray(series, dtype=float)
    s = s[~np.isnan(s)]
    rng = np.random.default_rng(RNG_SEED)
    hl, r1 = [], []
    for _ in range(n):
        perm = rng.permutation(s.size)
        a = acf(s[perm], max_lag)
        hl.append(halflife_from_acf(a)["halflife_lag"] or 0)
        r1.append(a[1])
    hl = np.array(hl, dtype=float)
    return {
        "n_permutations": n,
        "median_rho_lag1": round(float(np.median(r1)), 4),
        "max_rho_lag1": round(float(np.max(np.abs(r1))), 4),
        "fraction_measurable": round(float((hl > 1).mean()), 4),
        "note": "全 False/0 才是实现正确的样子；非零 ⇒ ACF 实现有 bug，本测量作废",
    }


def main() -> None:
    import sys
    sys.path.insert(0, str(ROOT))
    from aplus.evolve import DEFAULT_WEIGHTS, LIVE_REBALANCE_EVERY

    live_factors = list(DEFAULT_WEIGHTS)
    df = load_ic(live_factors)
    all_factors = [c[4:] for c in df.columns if c.startswith("sf__")]

    result = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "what_this_is": "IC 序列自相关半衰期（Kahn 1999 定义）；**不是**前瞻收益衰减半衰期",
        "source": str(IC_PARQUET.relative_to(ROOT)),
        "source_window": "lag1_real = FWD_WINDOWS[(0,1)]（1 交易日前瞻）",
        "n_dates": int(len(df)),
        "date_range": [str(df['date'].iloc[0]), str(df['date'].iloc[-1])],
        "caliber_note": (
            "sf__ = 两端排名的 Spearman IC（进判决口径）；"
            "sfP__ = 原始值 Pearson，仅对照，未用于本测量"
        ),
        "preregistered_criterion": {
            "external_anchors": {
                "LIVE_REBALANCE_EVERY": int(LIVE_REBALANCE_K := LIVE_REBALANCE_EVERY),
                "k_sweep_grid": list(ANCHOR_GRID),
            },
            "A_in_[21,63]": "与现生产相容；不构成改动理由（赢家诅咒警告不被推翻）",
            "B_below_21": "现有频率已超信息寿命；仍不授权上调 k",
            "C_above_63": "与 k=63 相容；须另起独立设计再检，不得本文件改 k",
            "D_unmeasurable": "判据不适用；结论=日频 IC 近似白噪声",
            "contradiction_policy": "与「k=63 有机制」相反时只加对照臂，不改判据臂",
        },
        "does_not_change_live_rebalance": (
            "本脚本不写 weights.json、不改 LIVE_REBALANCE_EVERY。"
            "tests/test_live_rebalance_frequency.py:81 的断言仍然成立且未被本测量推翻。"
        ),
        "live_factors": live_factors,
        "live_weights": {k: float(v) for k, v in DEFAULT_WEIGHTS.items()},
    }

    # ---- composite（生产 4 因子），带符号与 |w| 两条臂 ----
    comp = {}
    for signed, label in ((True, "signed_production"), (False, "abs_weight_CONTROL")):
        series, _ = composite_ic(df, DEFAULT_WEIGHTS, signed)
        a = acf(series, MAX_LAG)
        h = halflife_from_acf(a)
        comp[label] = {
            "halflife_lag": h["halflife_lag"],
            "status": h["status"],
            "rho_at_maxlag": round(h["rho_at_maxlag"], 4),
            "acf_table": acf_table(a),
            "mean_daily_ic": round(float(np.nanmean(series)), 5),
            "n_obs": int((~np.isnan(series)).sum()),
        }
    comp["control_arm_surrogate"] = surrogate_control(
        composite_ic(df, DEFAULT_WEIGHTS, True)[0], MAX_LAG
    )
    result["composite"] = comp

    # ---- 全部 8 因子单因子半衰期 ----
    per = {}
    for f in all_factors:
        s = df[f"sf__{f}"].to_numpy(dtype=float)
        if np.isnan(s).all():
            per[f] = {"status": "ALL_NAN"}
            continue
        a = acf(s, MAX_LAG)
        h = halflife_from_acf(a)
        per[f] = {
            "halflife_lag": h["halflife_lag"],
            "status": h["status"],
            "rho_at_maxlag": round(h["rho_at_maxlag"], 4),
            "mean_daily_ic": round(float(np.nanmean(s)), 5),
            "n_obs": int((~np.isnan(s)).sum()),
            "acf_table": acf_table(a),
            "in_live_weights": f in DEFAULT_WEIGHTS,
        }
    result["per_factor"] = per

    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"→ {OUT.relative_to(ROOT)}")
    print(f"日期 {result['date_range'][0]} ~ {result['date_range'][1]}，{result['n_dates']} 个交易日")
    print("\ncomposite（生产 4 因子）")
    for label in ("signed_production", "abs_weight_CONTROL"):
        c = comp[label]
        print(f"  {label:22s} 半衰期={c['halflife_lag']}  状态={c['status']}  "
              f"ρ(1)={c['acf_table'][0]['rho'] if c['acf_table'] else '—'}")
    sg = comp["control_arm_surrogate"]
    print(f"\n对照臂 白噪声置换: ρ(1) 中位数={sg['median_rho_lag1']} "
          f"max|ρ(1)|={sg['max_rho_lag1']} 可测占比={sg['fraction_measurable']}")
    print("\n单因子")
    for f, v in per.items():
        tag = "*" if v.get("in_live_weights") else " "
        print(f" {tag}{f:11s} 半衰期={str(v.get('halflife_lag')):>5s}  "
              f"{v.get('status') or v.get('status', '')}")


if __name__ == "__main__":
    main()

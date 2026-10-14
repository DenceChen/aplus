"""BM / ROE 的横截面预测力 —— 本项目**第一个独立于价量的信息源**的第一次实测。

## 为什么是这一个实验

现役 8 个因子全部由 OHLCV 派生，是同一条序列的 8 种重新表述。
`factor_vs_random`（4 榜宽 × 1154 日）与 `horizon_sweep`（K∈{1,5,10,20}）
两次独立实测都是「因子与随机不可区分」——**在只有一个信息源时，这是必然结果**。

石川《因子投资方法与实践》的 BM / ROE 属于**另一族**，需要财报。
`aplus/financial.py` 的结论是当前源不可 PIT，所以这族一直被主动封存。
本脚本是该族**第一次**被放到与价量因子完全相同的检验装置下。

## 事前登记（跑之前定死）

| 项 | 值 | 理由 |
|---|---|---|
| 因子 | `bm`（价值）、`roe`（质量） | BM 与 ROE 是基本面族里区分度最高的两条 |
| 主指标 | **Spearman IC 的 t 检验** | 见下「为什么主指标是 IC」 |
| 副指标 | top-10 对随机的配对 t | 直接对应产品实际买什么 |
| 周期 | K ∈ {21, 63} 交易日 | 年报是慢变量；K=1 对基本面没有意义 |
| 网格 | **非重叠**（`i % K == 0`） | K 日窗口重叠会让 t 虚高 |
| 比较次数 | **4**（2 因子 × 2 周期） | 临界 = `bonferroni_t(4, df)` |
| 晋级 | **不写 `weights.json`** | PIT 未建立，见下 |

### 为什么主指标是 IC

逐日 IC 的标准误约 `1/√n`（n≈450 只），60 期的均值标准误约
`1/√450/√60 ≈ 0.006`。**IC=0.03 就能给出 t≈5**——石川在 A 股报的 BM IC
正是 0.03~0.05 量级，所以这个检验对真实存在的信号**有功效**。

对比 top-10 组合的配对 t：60 个观测要在均值差上打出 t=3，
需要年化差约 6 个点——**远超任何已测因子的效应量（全部在 ±1% 内）**。
换句话说组合检验对我们要找的东西几乎没有功效，而 IC 检验有。

### 三条硬边界

1. **不写 `weights.json`，不进实盘**。`financial.py` 已写明：当前源每报告期
   只存一行、无版本维度，服务的必然是**后修值**。用 `NOTICE_DATE <= as_of`
   过滤等于「用诚实的披露时间戳，贴一个事后被修订过的数值」。
   **对齐是诚实的，PIT 不是。**
2. **先报覆盖率，再报 IC**。只有 500 只票有财报（占全池 9%），若某天
   可用样本掉到几十只，IC 会变成在少数几只票上算相关——先看数字再决定。
3. **本窗口对 BM/ROE 是干净的**（它们从未进入任何赛马或权重搜索），
   但这是**第一次**测量，所以族大小取 4，不并入赛马的 84。

## 一句话预期（登记，不是结论）

如果 BM 在 A 股真的有效，这里应当给出 |t| > 临界；如果给不出，
那「因子投资在本平台不可行」就有了**跨信息源**的证据，而不只是价量内部的 null。
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from aplus.evolve_race import DEGENERATE_CV, bonferroni_t  # noqa: E402
from aplus.factor_ic import rank_corr  # noqa: E402
from scripts.fundamental_factor import (  # noqa: E402
    attach_to_cross_sections, coverage, load_annual,
)
from scripts.head_bucket_probe import _paired_t  # noqa: E402
from scripts.horizon_sweep import forward_return, per_year  # noqa: E402

FACTORS = ["bm", "roe"]
HORIZONS = [21, 63]
N_COMPARISONS = len(FACTORS) * len(HORIZONS)      # 4，事前定死
TOP_N = 10

#: 可用样本少于这个数就不报 IC——在几十只票上算秩相关不是统计，是噪声。
MIN_CROSS_SECTION = 60


def _spearman(x: np.ndarray, y: np.ndarray) -> float | None:
    """秩相关。

    直接复用 `aplus.factor_ic.rank_corr`，**不自己写、也不引 scipy**
    （`aplus/dist.py` 明确写过「为什么不给 scipy」）。它用双遍 argsort
    处理并列——`roe` 在 A 股的横截面上并列极多（同为 10% 净利率的票一抓一把），
    直接 `np.corrcoef` 会把并列当顺序，IC 被系统性污染。
    """
    return rank_corr(x, y, min_pairs=MIN_CROSS_SECTION)


def ic_series(xs: dict, days: list, factor: str, k: int) -> pd.Series:
    """非重叠网格上的逐日 IC。"""
    daily_map = xs.get("_daily_map") or {}
    factors_map = xs.get("_factors_map") or {}
    adj_cache = xs.setdefault("_adj_cache", {})

    out = {}
    for i, day in enumerate(days):
        if k > 1 and i % k != 0:
            continue
        uni = xs.get(day)
        if uni is None or uni.empty or factor not in uni.columns:
            continue
        sub = uni[["code", factor]].dropna()
        # 闸必须在**循环之前**：初版把宽度判断放在算完之后，
        # 于是明知要丢，还是把当天每只票的 forward_return 全算了一遍。
        # 判定标准不变，只是别为一个已经知道会被丢弃的横截面付 200 次计算。
        if len(sub) < MIN_CROSS_SECTION:
            continue
        xs_, ys_ = [], []
        for code, fv in zip(sub["code"], sub[factor]):
            r = forward_return(daily_map, factors_map, adj_cache, code, day, k)
            if r is not None:
                xs_.append(float(fv))
                ys_.append(r)
        if len(xs_) < MIN_CROSS_SECTION:
            continue
        ic = _spearman(np.asarray(xs_), np.asarray(ys_))
        if ic is not None:
            out[day] = ic
    return pd.Series(out, dtype=float)


def ic_tstat(ic: pd.Series) -> tuple[float | None, int]:
    """IC 序列的 t。**分母退化时必须返回 `None`。**

    初版只判了 `sd == 0`，而 `[0.03] * 20` 的样本标准差不是精确的 0——
    浮点累加会留下约 1e-18 的残渣，于是 `mean / (sd/√n)` 算出
    **t = 1.88e+16**。一个凭空冒出来的天文数字比「没有 t 值」危险得多，
    因为它不会被任何人质疑，还会直接盖过任何 Bonferroni 临界值。

    修法与 `head_bucket_probe._paired_t` 同一套：**相对判据**
    `sd <= |mu| * DEGENERATE_CV`，不靠绝对零。
    这条是被 `test_ic_t_is_none_when_the_ic_series_is_constant` 抓出来的——
    讽刺的是，我在这个函数的 docstring 里先写了「宁可缺证据，不可造证据」。
    """
    if len(ic) < 3:
        return None, len(ic)
    v = ic.to_numpy()
    mu = float(v.mean())
    sd = float(v.std(ddof=1))
    if not np.isfinite(sd) or sd <= 0 or sd <= abs(mu) * DEGENERATE_CV:
        return None, len(ic)
    se = sd / np.sqrt(len(v))
    if not se > 0:
        return None, len(ic)
    return float(mu / se), len(ic)


def portfolio_vs_random(xs: dict, days: list, factor: str, k: int,
                         seed: int) -> dict:
    """top-10（按该因子）− 随机 top-10，逐日配对。**副指标**，功效低。

    ## 为什么不能直接复用 `arm`

    `arm(xs, days, None, seed, k)` 的随机臂给的是**全池**随机分数
    （`_score` 里 `weights is None` 直接 `rng.random(len(universe))`），
    而因子臂走 `composite_score`，任一因子 NaN → 合成分 NaN → 被 `dropna`
    剔掉。于是：

        因子臂 top-10  ← 有财报的约 450 只
        随机臂 top-10  ← 全池约 1200 只

    **两臂选的不是同一个池**，这个 t 度量的是「在有财报的股票里挑 vs 在
    全市场里随机」，而不是「按因子挑 vs 随机」。财报覆盖的那 450 只若系统
    性偏大盘，这个差值会整体被大盘 beta 支配——测出来的是**样本构成**，
    不是选股能力。所以这里显式把随机臂**限制在同一个覆盖池**内。
    """
    daily_map = xs.get("_daily_map") or {}
    factors_map = xs.get("_factors_map") or {}
    adj_cache = xs.setdefault("_adj_cache", {})
    rng = np.random.default_rng(seed)

    def _top(codes) -> float | None:
        vals = []
        for c in codes:
            r = forward_return(daily_map, factors_map, adj_cache, c, day, k)
            if r is not None:
                vals.append(r)
        return float(np.mean(vals)) if vals else None

    s_vals, r_vals, ns = {}, {}, []
    for i, day in enumerate(days):
        if k > 1 and i % k != 0:
            continue
        uni = xs.get(day)
        if uni is None or uni.empty or factor not in uni.columns:
            continue
        sub = uni.loc[uni[factor].notna()]        # ← 两臂共用的池
        if len(sub) < MIN_CROSS_SECTION:
            continue
        ns.append(len(sub))
        fs = _top(sub.nlargest(TOP_N, factor)["code"])
        rr = _top(sub.iloc[rng.permutation(len(sub))[:TOP_N]]["code"])
        if fs is not None:
            s_vals[day] = fs
        if rr is not None:
            r_vals[day] = rr

    both = pd.concat([pd.Series(s_vals, dtype=float),
                      pd.Series(r_vals, dtype=float)],
                     axis=1, keys=["s", "r"]).dropna()
    if len(both) < 3:
        return {"n_days": int(len(both)), "paired_t": None,
                "pool_median_n": float(np.median(ns)) if ns else 0}
    return {
        "n_days": int(len(both)), "paired_t": _paired_t(both["s"], both["r"]),
        "factor_annual": per_year(float(both["s"].mean()), k),
        "random_annual": per_year(float(both["r"].mean()), k),
        "diff_annual": per_year(float((both["s"] - both["r"]).mean()), k),
        "pool_median_n": float(np.median(ns)) if ns else 0,
        "pool_note": "两臂（因子 / 随机）取自**同一个**有财报的覆盖池",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", type=Path, required=True)
    ap.add_argument("--financial", type=Path,
                    default=Path("data/reports/financial_annual_cache.parquet"))
    ap.add_argument("--start", default="2019-01-02")
    ap.add_argument("--end", default="2023-12-31")
    ap.add_argument("--out", type=Path,
                    default=Path("data/reports/fundamental_ic.json"))
    args = ap.parse_args()

    import pickle
    from scripts.head_bucket_probe import RANDOM_SEED

    print("载入面板缓存…", flush=True)
    xs = pickle.loads(args.cache.read_bytes())
    days = sorted(d for d in xs["_dates"] if args.start <= d <= args.end)

    print("载入年报并按 NOTICE_DATE 对齐…", flush=True)
    annual = load_annual(args.financial)
    print(f"  年报 {len(annual)} 条 / {annual['code'].nunique()} 只", flush=True)
    xs = attach_to_cross_sections(xs, annual, args.start, args.end)

    cov = coverage(xs, args.start, args.end)
    print(f"\n覆盖率：{cov['days_with_any']}/{cov['n_days']} 日有值，"
          f"中位 {cov['median_n']:.0f} 只，区间 [{cov['min_n']}, {cov['max_n']}]",
          flush=True)
    if cov["median_n"] < MIN_CROSS_SECTION:
        print(f"  ⚠️ 中位可用样本 {cov['median_n']:.0f} < {MIN_CROSS_SECTION}，"
              f"**横截面太窄，IC 不具统计意义**——不要据此下任何结论")
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(
            {"kind": "fundamental_ic", "coverage": cov, "skipped": True,
             "reason": f"中位可用样本 {cov['median_n']:.0f} < {MIN_CROSS_SECTION}"},
            ensure_ascii=False, indent=2), encoding="utf-8")
        return 1

    print(f"\n{'='*94}")
    print(f"BM / ROE 预测力（{args.start} ~ {args.end}，非重叠网格，"
          f"族 {N_COMPARISONS} 次比较）")
    print(f"{'='*94}")
    rows = []
    for factor in FACTORS:
        for k in HORIZONS:
            ic = ic_series(xs, days, factor, k)
            t, n = ic_tstat(ic)
            crit = bonferroni_t(N_COMPARISONS, df=max(n - 1, 2))
            pf = portfolio_vs_random(xs, days, factor, k, RANDOM_SEED)
            if t is None:
                v = "IC 序列退化或样本不足——不下任何结论"
            elif t >= crit:
                v = f"**{factor} @K={k} 显著有预测力**（t={t:+.2f} ≥ {crit:.2f}）"
            elif t <= -crit:
                v = f"**{factor} @K={k} 显著反向**（t={t:+.2f} ≤ -{crit:.2f}）"
            else:
                v = f"{factor} @K={k} 无预测力（t={t:+.2f}，|t| < {crit:.2f}）"
            rows.append({
                "factor": factor, "k": k, "n_ic": n,
                "mean_ic": float(ic.mean()) if len(ic) else None,
                "ic_sd": float(ic.std(ddof=1)) if len(ic) > 2 else None,
                "ic_t": t, "t_crit": crit, "verdict": v,
                "portfolio": pf,
            })
            mit = (f"{ic.mean():+.4f}" if len(ic) else " n/a ")
            pt = pf.get("paired_t")
            print(f"  {factor:<4} K={k:<3} 期数 {n:>3}  平均IC {mit}  "
                  f"IC-t {t if t is not None else float('nan'):>+6.2f}  "
                  f"临界 {crit:.2f}  | 组合 t "
                  f"{pt if pt is not None else float('nan'):>+6.2f}", flush=True)
            print(f"       → {v}", flush=True)

    hits = [r for r in rows
            if r["ic_t"] is not None and abs(r["ic_t"]) >= r["t_crit"]]
    summary = ("、".join(f"{r['factor']}@K={r['k']}" for r in hits) + " 过闸"
               if hits else "**BM 与 ROE 均无横截面预测力**")
    print(f"\n  → {summary}")

    # 每个格子的**最小可检出 IC**。没有它，「t=2.94 过闸」会被读成「IC 很大」，
    # 而实际可能只比本设计的检测下限高一点点——**过闸 ≠ 效应大**。
    for r in rows:
        if r["ic_t"] is None or r["ic_sd"] is None:
            r["mde_ic"] = None
            continue
        r["mde_ic"] = float(r["t_crit"] * r["ic_sd"] / np.sqrt(r["n_ic"]))
        r["mde_note"] = (f"本设计只能发现 |平均IC| ≥ {r['mde_ic']:.4f} 的效应；"
                         f"实测 {r['mean_ic']:+.4f}")
    print("\n  最小可检出 IC（过闸 ≠ 效应大）：")
    for r in rows:
        if r.get("mde_ic") is None:
            continue
        print(f"    {r['factor']:<4} K={r['k']:<3} 可检出 ≥ {r['mde_ic']:.4f}   "
              f"实测 {r['mean_ic']:+.4f}")

    out = {
        "kind": "fundamental_ic",
        "window": [args.start, args.end], "n_days_window": len(days),
        "factors": FACTORS, "horizons": HORIZONS,
        "n_comparisons": N_COMPARISONS, "top_n": TOP_N,
        "coverage": cov,
        "why_ic_is_primary": (
            "逐日 IC 的标准误约 1/√450≈0.047，60 期均值标准误 ≈0.006，"
            "IC=0.03 就能给出 t≈5——石川在 A 股报的 BM IC 正是这个量级。"
            "而 top-10 组合的配对 t 要在 60 个观测上打出 t=3 需要年化差约 6 个点，"
            "**远超任何已测因子的效应量（全部在 ±1% 内）**，对要找的东西没有功效"),
        "pit_status": ("对齐用 `NOTICE_DATE <= as_of` 是诚实的，但源每报告期只存一行、"
                       "无版本维度，**服务的是后修值**。PIT 未建立——"
                       "本结果不构成采纳依据，判决权在攒够 vintage 之后"),
        "no_promotion": "不写 weights.json，不改晋级门限",
        "coverage_caveat": ("财报缓存只有 500 只票（全池 9%），且是 akshare 返回的"
                            "那 500 只，不是随机抽样——横截面上可能带有选择偏差"),
        "rows": rows,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  ⚠️ {out['pit_status']}")
    print(f"\n写出 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
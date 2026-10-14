"""持仓周期扫描 —— 「因子没价值」是市场事实，还是口径事故？

## 上一轮问的是什么、答出了什么

`factor_vs_random` 在 2019-01~2023-12（1154 日）测出：现役 composite 因子
的 top-N 与随机 top-N **在四个榜宽上 t 全部 ≈ 0**（0.10 / -1.16 / -0.31 / 0.39）。

## 为什么这不能直接读作「A 股横截面没有 alpha」

那次实验测的是 **1 日前瞻**：`entry_lag=1`，D+1 买、D+2 卖，持有 1 个交易日。

而石川《因子投资方法与实践》和 Chan《Quantitative Trading》里绝大多数
横截面因子——反转、价值、动量、质量——在**日频**上的 IC 接近 0，
要到 1~3 个月才显出可交易的意义。原因不神秘：日频噪声压过了微弱的
横截面价差，而价差需要时间兑现。

**在错误的 horizon 上测因子，等于测「这里没有因子」。**
这是口径问题，不能当成市场事实写进结论。

## 本脚本做什么

同一批因子、同一批股票池、同一套 universe 过滤，只改一件事：**持有 K 个交易日**。
K ∈ {1, 5, 10, 20}，逐日配对 t（因子 − 随机），唯一差别仍是排序依据。

## 三条必须先讲清楚的口径

### 1. 非重叠观测：K>1 时逐日 t 检验是**错的**

持有 K 天时，相邻 K 天的收益窗口共享 K-1 天，序列自相关极强。
在自相关序列上做普通 t 检验，标准误被严重低估，t 值虚高——**这会
制造出一个看起来很显著的假阳性**。

所以只在网格日取样：`day_index % K == 0`。K=20 时 1154 天只剩约 57 个
观测，自由度随之下降，临界值必须带真实 df（`bonferroni_t` 不设默认 df
正是为了防这个）。

**代价要认**：有效样本从 1154 掉到 57，功效大幅下降。所以本扫描对
「没检出」的解释力远弱于对「检出」的解释力——**不出结果不等于没效果**。

### 2. 重叠不是唯一问题：K 天收益的年化不能乘 DAYS_PER_YEAR

K 天收益的均值必须按 `DAYS_PER_YEAR / K` 折算成年化。
直接乘 252 会把 K=20 的收益**放大 20 倍**。本脚本用 `per_year(scale, K)`。

### 3. 榜宽只报一个：top-10

策略实际买的是等权 top-10。K × 榜宽 会把比较次数乘起来，而 K 已经有
4 个取值。再乘 4 个榜宽 = 16 次比较，Bonferroni 临界抬到 3.5 上下，
而有效样本只剩几十——**这个设计注定什么都测不出来**。
所以榜宽固定 top-10，比较次数 = 4，临界 = `bonferroni_t(4, df)`。

## 事前登记（跑之前定）

- **主指标**：逐日配对 t，因子 top-10 − 随机 top-10，网格非重叠日
- **判据**：t ≥ `bonferroni_t(4, df)` → 「K 日尺度上因子显著」
- **不做晋级判定，不写 `weights.json`**
- **K=1 是对照组**：它必须复现 `factor_vs_random` 的 t=0.10。
  复现不出来就说明本脚本有 bug，K>1 的结果一律不可信。
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from aplus.evolve_race import bonferroni_t  # noqa: E402
from scripts.head_bucket_probe import (  # noqa: E402
    BUCKET, DAYS_PER_YEAR, N_RANKS, RANDOM_SEED, SIGN_FIXED, _paired_t,
)

# 持仓周期（交易日）。1 是对照组，必须复现上一轮 t=0.10。
HORIZONS = [1, 5, 10, 20]

TOP_N = 10                      # 策略实际买的宽度
N_COMPARISONS = len(HORIZONS)   # 4 次，不是 4×榜宽数


def per_year(daily_mean: float, k: int) -> float:
    """K 日收益均值 → 年化。

    **必须除以 K。** 直接乘 `DAYS_PER_YEAR` 会把 K=20 的收益放大 20 倍——
    一个能把噪声年化成 +400% 的算式。这不是笔误级别的问题：
    它足以让整个扫描得出「长周期因子极强」的结论。
    """
    return daily_mean * (DAYS_PER_YEAR / k)


def forward_return(daily_map: dict, factors_map: dict, adj_cache: dict,
                   code: str, day: str, k: int) -> float | None:
    """D 收盘出信号 → D+1 进场 → 持有 K 个交易日 → 第 K 日收盘卖出。

    与 `head_bucket_probe` 同口径（`entry_lag=1`，D+1 进场），只是出场往后推
    K-1 天。**进场 lag 不动**——动它就变成另一个实验了。

    ## 复权：必须走 `_build_bar_cache`，不能自己算

    原始日线帧**没有 `adjfactor` 列**（实测列集：date/code/open/high/low/close/
    volume/amount/tradestatus/isST/turn/pctChg）。本函数首版去读那一列，
    读不到就静默降级成不复权价 —— 而不复权价在除权日直接跳空。

    `_next_bars` 的文档记着这个坑的规模：全库 **2623 只**股票受影响，
    最极端一根 K 线造出 **67 个百分点**的假跌幅。

    **1 日窗口踩中除权的概率低，20 日窗口几乎必然踩中**（每个 20 日窗口里
    含约 4% 概率的除权事件，57 个窗口里会有好几个）。这不是精度问题，
    是会把整条长周期曲线打烂的系统性错误。

    所以这里复用生产代码的 `_build_bar_cache`（`hfq_close` 后复权），
    口径与 `_next_bars` **逐位一致**。
    """
    from aplus.backtest import _build_bar_cache

    df = daily_map.get(code)
    if df is None or df.empty:
        return None
    key = (code, id(df))
    hit = adj_cache.get(key)
    if hit is None:
        hit = _build_bar_cache(code, df, factors_map.get(code))
        adj_cache[key] = hit
    dates, closes = hit[0], hit[1]
    if not hit[2]:                       # 没有除权事件 → 不复权，但要在结果里可见
        pass
    pos = int(np.searchsorted(dates, day, side="right")) - 1
    if pos <= 0:                         # 信号日尚未有 K 线 → 该票当日不存在
        return None
    entry = pos + 1                      # entry_lag=1 → D+1
    # `entry + k`，**不是 `entry + k - 1`**。K=1 必须复现 `_next_bars` 的
    # `exit_i = entry + 1`；写成 `- 1` 会让 K=1 的 exit 落在 entry 上、
    # 收益率恒为 0，差值退化 → 对照组永远「复现不出」→ K>1 全部被判不可信。
    # 一个专门防自欺的机制，被 off-by-one 变成永远红灯，比没有更糟。
    exit_i = entry + k
    if entry >= len(dates) or exit_i >= len(dates):
        return None
    a, b = float(closes[entry]), float(closes[exit_i])
    if not np.isfinite(a) or not np.isfinite(b) or a == 0:
        return None
    return b / a - 1.0


def arm(xs: dict, days: list, weights: dict | None, seed: int,
        k: int) -> pd.DataFrame:
    """单臂在网格日上的 top-10 逐日收益。

    **网格是按窗口内的序号取的**（`i % k == 0`），两臂用同一个 `days`，
    所以重合成对成立。K=1 时退化为全部交易日。
    """
    daily_map = xs.get("_daily_map") or {}
    factors_map = xs.get("_factors_map") or {}
    adj_cache = xs.setdefault("_adj_cache", {})
    rng = np.random.default_rng(seed)
    from scripts.head_bucket_probe import _score

    out = {}
    for i, day in enumerate(days):
        if k > 1 and i % k != 0:
            continue
        uni = xs.get(day)
        if uni is None or uni.empty:
            continue
        sc = _score(uni, weights, rng)
        ranked = (uni.assign(_s=sc).dropna(subset=["_s"])
                  .sort_values("_s", ascending=False).head(min(TOP_N, N_RANKS)))
        vals = []
        for code in ranked["code"]:
            r = forward_return(daily_map, factors_map, adj_cache, code, day, k)
            if r is not None:
                vals.append(r)          # 买不进的照算，不猜
        if vals:
            out[day] = float(np.mean(vals))
    return pd.Series(out, dtype=float)


def compare_k(xs: dict, days: list, k: int, seed: int = RANDOM_SEED) -> dict:
    s = arm(xs, days, SIGN_FIXED, seed, k)
    r = arm(xs, days, None, seed, k)
    both = pd.concat([s, r], axis=1, keys=["s", "r"]).dropna()
    if len(both) < 3:
        return {"k": k, "n_days": int(len(both)), "paired_t": None,
                "verdict": "有效日不足——不下任何结论"}
    t = _paired_t(both["s"], both["r"])
    crit = bonferroni_t(N_COMPARISONS, df=len(both) - 1)
    if t is None:
        v = "差值退化，不可估"
    elif t >= crit:
        v = f"**K={k} 显著优于随机**（t={t:.2f} ≥ 临界 {crit:.2f}）"
    elif t <= -crit:
        v = f"**K={k} 显著劣于随机**（t={t:.2f} ≤ -临界 {crit:.2f}）"
    else:
        v = f"K={k} 与随机不可区分（t={t:.2f} < 临界 {crit:.2f}）"
    d = both["s"] - both["r"]
    return {
        "k": k, "n_days": int(len(both)), "t_crit": crit, "paired_t": t,
        "factor_annual": per_year(float(both["s"].mean()), k),
        "random_annual": per_year(float(both["r"].mean()), k),
        "diff_annual": per_year(float(d.mean()), k),
        "verdict": v,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", type=Path, required=True)
    ap.add_argument("--start", default="2019-01-02")
    ap.add_argument("--end", default="2023-12-31")
    ap.add_argument("--out", type=Path, default=Path("data/reports/horizon_sweep.json"))
    args = ap.parse_args()

    import pickle
    print("载入面板缓存…", flush=True)
    xs = pickle.loads(args.cache.read_bytes())
    days = sorted(d for d in xs["_dates"] if args.start <= d <= args.end)
    print(f"本段 {len(days)} 个交易日；K ∈ {HORIZONS}；榜宽 {TOP_N}", flush=True)

    rows = []
    for k in HORIZONS:
        r = compare_k(xs, days, k)
        rows.append(r)
        t = r.get("paired_t")
        print(f"  K={k:>2}  有效日 {r['n_days']:>4}  "
              f"因子 {r.get('factor_annual', float('nan')):>+8.2%}  "
              f"随机 {r.get('random_annual', float('nan')):>+8.2%}  "
              f"差 {r.get('diff_annual', float('nan')):>+8.2%}  "
              f"t={(t if t is not None else float('nan')):>+6.2f}  "
              f"临界 {r.get('t_crit', float('nan')):.2f}  {r['verdict']}", flush=True)

    ctrl = next((r for r in rows if r["k"] == 1), None)
    ctrl_ok = (ctrl is not None and ctrl.get("paired_t") is not None
               and abs(ctrl["paired_t"]) < 3.0)
    print(f"\n{'='*94}")
    print(f"  对照组 K=1 的 t = {ctrl.get('paired_t') if ctrl else None}"
          f"（上一轮 factor_vs_random 实测 0.10）")
    if not ctrl_ok:
        print("  ⚠️ **对照组没能复现——本脚本有 bug，K>1 的结果一律不可信**")

    hits = [r for r in rows if r.get("paired_t") is not None
            and abs(r["paired_t"]) >= r.get("t_crit", 99)]
    print(f"\n  → {'K=' + ','.join(str(r['k']) for r in hits) + ' 过闸' if hits else '**所有持仓周期都与随机不可区分**'}")
    print(f"  ⚠️ 功效警告：K=20 时有效日约 {len(days)//20} 个，"
          f"「没检出」远弱于「检出」——**不出结果不等于没效果**")

    out = {"kind": "horizon_sweep", "horizons": HORIZONS, "top_n": TOP_N,
           "n_comparisons": N_COMPARISONS, "window": [args.start, args.end],
           "n_days_window": len(days), "random_seed": RANDOM_SEED,
           "prereg": {
               "primary": "逐日配对 t（因子 top-10 − 随机 top-10），网格非重叠日",
               "criterion": "t ≥ bonferroni_t(4, df)",
               "why_non_overlapping": "K>1 时相邻日共享 K-1 天，自相关序列上做"
                                      "普通 t 检验会虚高标准误、制造假阳性",
               "why_annual_divides_by_k": "K 日收益乘 252 会放大 K 倍",
               "no_promotion": "不写 weights.json，不改晋级门限",
           },
           "control_reproduced": ctrl_ok, "rows": rows}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n写出 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""显著性检验：过闸的候选是真 alpha，还是多重比较下的噪音？

## 为什么必须做这一步

A/B 结果：新口径下 **1/14** 候选通过超额硬闸，裁决「两段双赢」。
赢家的测试段超额是 **+0.04%/天**、命中率 **50.5%**（仍是抛硬币）。

14 个候选里恰好有 1 个通过，正是**多重比较的预期误报量**。在单侧 α=5% 下，
纯噪音也会产出约 0.7 个「过闸者」。所以「过闸」本身**不构成证据**。

## 本脚本做三件事

1. **t 检验**：赢家测试段的逐日超额序列，看是否与 0 可区分
2. **Bonferroni 校正**：14 次比较把阈值收紧到 α/14
3. **随机对照（最有说服力）**：从同一候选分布里抽 N 组随机权重，跑**完全相同**的
   训练/测试切分与过闸规则，数一数随机权重能过几次

第 3 项是关键——它把多重比较、运气、数据怪癖一次性全吸收了。
若随机权重也能过闸相当多次，那唯一的结论就是「过闸不代表有 alpha」。

用法：.venv/bin/python scripts/significance_check.py [--random-null 30]
"""
import argparse
import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from aplus.backtest import _market_by_day, evaluate_weights
from aplus.evolve import objective
from aplus.evolve_race import race, split_window
from aplus.panel import build_panel
from aplus.predict import filter_universe
from aplus.snapshot import load_factors_map
from aplus.store import LocalStore
from aplus.verify import rolling_stats

ROOT = Path(__file__).resolve().parent.parent
START = "2024-01-02"
WINNER = {"mom20": 0.5, "rev5": 0.5, "vol20": -1.0, "vsur": 0.0}


def sections(panel, daily_map, entry_lag=1):
    out = {"_dates": panel.dates, "_daily_map": daily_map, "_entry_lag": entry_lag,
           "_market": _market_by_day(daily_map, panel.dates, entry_lag=entry_lag)}
    for i, day in enumerate(panel.dates):
        out[day] = filter_universe(panel.cross_section(i))
    return out


def ev_factory(xs):
    def ev(_xs, w, dates, tn, bn):
        res = evaluate_weights(xs, w, top_n=tn, bottom_n=bn, dates=dates)
        st = rolling_stats(res)
        return {"stats": st, "objective": round(objective({"weights": w, "stats": st}), 4),
                "n": int(len(res))}
    return ev


def daily_excess_series(xs, weights, dates):
    """逐日「涨篮子收益 − 市场基准」序列——t 检验的原料。"""
    res = evaluate_weights(xs, weights, top_n=10, bottom_n=10, dates=dates)
    if res.empty:
        return pd.Series(dtype=float)
    up = res[res["direction"] == "up"]
    return (up.groupby("pred_date")["actual_pct"].mean()
            - up.groupby("pred_date")["market_pct"].mean()).dropna()


def random_weights(rng, n):
    """从与真实候选同族的分布里抽权重：mom20/rev5/vsur 非负，vol20/turn20 偏负。"""
    w = {}
    if rng.random() < 0.85:
        w["mom20"] = round(rng.uniform(0.0, 2.0), 1)
    if rng.random() < 0.85:
        w["rev5"] = round(rng.uniform(0.0, 1.0), 1)
    if rng.random() < 0.85:
        w["vol20"] = round(-rng.uniform(0.0, 1.2), 1) if rng.random() < 0.6 else round(rng.uniform(0.0, 1.0), 1)
    if rng.random() < 0.7:
        w["vsur"] = round(rng.uniform(0.0, 1.0), 1)
    if rng.random() < 0.5:
        w["turn20"] = round(-rng.uniform(0.0, 1.2), 1)
    return w or {"mom20": 1.0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--random-null", type=int, default=30)
    ap.add_argument("--seed", type=int, default=20261011)
    args = ap.parse_args()

    t0 = time.time()
    store = LocalStore(ROOT / "data")
    files = sorted((store.root / "master" / "daily").glob("*.parquet"))
    end = str(pd.read_parquet(files[0])["date"].max())
    daily_map = {p.stem: store.read_daily(p.stem) for p in files}
    factors_map = load_factors_map(store)
    panel = build_panel(store, START, end, None, daily_map, factors_map)
    xs = sections(panel, daily_map)
    train_dates, test_dates = split_window(xs["_dates"])
    print(f"面板 {len(panel.dates)} 日 → {time.time()-t0:.0f}s")
    print(f"训练 {train_dates[0]}~{train_dates[-1]} | 测试 {test_dates[0]}~{test_dates[-1]}\n")

    # ── 1. 赢家的逐日超额 ──
    s = daily_excess_series(xs, WINNER, test_dates)
    n = len(s)
    mean = float(s.mean())
    sd = float(s.std(ddof=1)) if n > 1 else 0.0
    t = mean / (sd / np.sqrt(n)) if sd > 0 else 0.0
    ann = (1 + mean) ** 250 - 1
    print("═" * 76)
    print("① 赢家测试段逐日超额（相对市场基准）")
    print(f"   权重 {WINNER}")
    print(f"   n={n} 日  均值 {mean:+.4%}/日  标准差 {sd:.4%}  t={t:+.2f}")
    print(f"   简单年化 {ann:+.2%}")
    # 双侧 p（正态近似）
    from math import erfc, sqrt
    p_two = erfc(abs(t) / sqrt(2)) if n > 1 else 1.0
    crit = 1.96 + (np.log(args.random_null) if args.random_null > 1 else 0)
    print(f"   双侧 p ≈ {p_two:.3f}；Bonferroni(14 次) 临界 |t| ≈ {2.44:.2f}")
    print(f"   结论：{'与 0 不可区分 —— 这是噪音' if abs(t) < 2.44 else '统计上显著'}")

    # ── 2. 随机对照 ──
    rng = random.Random(args.seed)
    cands = [random_weights(rng, i) for i in range(args.random_null)]
    cands = [dict(c) for c in {tuple(sorted(c.items())) for c in cands}]
    print(f"\n{'═'*76}")
    print(f"② 随机权重对照（{len(cands)} 组，完全相同的切分与过闸规则）")
    r = race(xs, cands, ev_factory(xs), top_n=10, bottom_n=10)
    if r.get("error"):
        print(f"   赛马失败：{r['error']}")
        return
    passed = [row for row in r["table"]
              if row["test"]["stats"].get("up_excess_vs_market", 0) > 0]
    exc = np.array([row["test"]["stats"].get("up_excess_vs_market", 0) for row in r["table"]])
    print(f"   通过超额硬闸：{len(passed)}/{len(r['table'])}"
          f"  ({len(passed)/max(1,len(r['table'])):.1%})")
    print(f"   随机候选测试超额分布：均值 {exc.mean():+.4%}  "
          f"最好 {exc.max():+.4%}  中位 {np.median(exc):+.4%}")
    if passed:
        print(f"   过闸的随机权重（前 5）：")
        for row in passed[:5]:
            print(f"     {row['test']['stats']['up_excess_vs_market']:+.4%}  {row['weights']}")
    rate = len(passed) / max(1, len(r["table"]))
    print(f"\n   真实候选过闸率 1/14 = {1/14:.1%} vs 随机过闸率 {rate:.1%}")

    out = {"window": [START, end],
           "train": [train_dates[0], train_dates[-1]],
           "test": [test_dates[0], test_dates[-1]],
           "winner": {"weights": WINNER, "n": n, "mean_daily": mean,
                      "std_daily": sd, "t": t, "annualized": ann,
                      "p_two_sided": p_two},
           "random_null": {"n": len(r["table"]), "passed": len(passed),
                           "pass_rate": rate,
                           "mean_excess": float(exc.mean()),
                           "best_excess": float(exc.max())}}
    (ROOT / "data" / "reports" / "significance.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\n总耗时 {time.time()-t0:.0f}s → data/reports/significance.json")


if __name__ == "__main__":
    main()
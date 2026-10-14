"""调仓频率 head-to-head：同一批权重，只改调仓周期。

## 为什么这个实验排在因子调参之前

2 年终裁（`docs/FINDINGS_2026-10-11.md`）的结论是 **26 组候选全部无法与 0 区分**
（最佳测试超额 +0.06%，t = +0.91，Bonferroni 临界 3.10）。
在这种情况下继续调因子，是在噪音里找信号。

但**成本不是噪音**，它是确定的：

| 调仓频率 | 实测年成本（5 万本金 / 10 只 / 单只 5000 元） |
|---|---|
| 每日 | **16.91%** |
| 每周 | 8.15% |
| 每月 | **2.87%** |

日频与月频差 **14 个百分点/年**，而所有已测因子效应都在 ±1% 以内。
**即使某个因子真有 +0.5%/月的毛 alpha，日频口径下也会被成本吃光，
月频口径下却能留下 8 个百分点。** 换句话说，频率不是微调，它决定策略能不能成立。

书的 3.1.4 口径是**每月末调仓**。这个实验就是去检验：
把平台唯一的结构假设改回书的口径，结论会不会变。

## 样本量纪律

测试段 194 个交易日 → 月频只有 9 个周期、季频 3 个。
**少于 3 个周期一律不作统计判决**，只报量级。
`aplus/rebalance.py: effective_rebalance_every` 里有对应实现。

## 成本口径

用平台真实成本模型（`aplus/backtest.py: STOCK_COST_DEFAULT`，0.003 双边），
而不是我另算的 5 元最低佣金口径——后者对 10 只 5000 元的组合是 0.2520%，
模型是 0.30%，只保守 19%，不影响量级比较，且**保持全链路同口径**。
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from aplus.backtest import (ENTRY_LAG, build_cross_sections, evaluate_weights)
from aplus.evolve_race import breadth_tstat, down_excess_tstat, excess_tstat, split_window
from aplus.snapshot import load_factors_map
from aplus.store import LocalStore
from aplus.verify import rolling_stats

ROOT = Path("/tmp/aplus_snap_final")
START, END = "2024-10-01", "2026-10-09"

# 2 年终裁 v2 的前 6 名 + 现任冠军（取自 reports/race_final_2y_v2.json）
CANDIDATES = [
    {"turnratio": -1.0},
    {"mom20": -0.5, "turnratio": -0.5, "ivol": -0.3},
    {"mom20": -1.0},
    {"mom20": 0.0, "rev5": 0.0, "vol20": 0.0, "vsur": 0.0, "ivol": -1.0},
    {"mom20": 0.0, "rev5": 0.0, "vol20": 0.0, "vsur": 0.0, "turn20": -1.0},
    {"mom20": 1.0, "rev5": 0.3, "vol20": 0.5, "vsur": 0.5},     # 现任冠军
]

FREQS = [(1, None), (1, 3.0), (5, None), (5, 3.0), (21, None), (63, None)]


def stats_of(res):
    s = dict(rolling_stats(res))
    t, _ = excess_tstat(res)
    s["excess_t"] = t
    bt, _ = breadth_tstat(res)
    s["breadth_t"] = bt
    dt, _ = down_excess_tstat(res)
    s["down_t"] = dt
    return s


def main():
    store = LocalStore(ROOT)
    files = sorted((store.root / "master" / "daily").glob("*.parquet"))
    t0 = time.time()
    print(f"载入 {len(files)} 只…", flush=True)
    daily_map = {p.stem: store.read_daily(p.stem) for p in files}
    factors_map = load_factors_map(store)
    xs = build_cross_sections(store, START, END, daily_map=daily_map,
                              factors_map=factors_map, entry_lag=ENTRY_LAG)
    print(f"横截面 {len(xs['_dates'])} 日 × {len(daily_map)} 只 → {time.time()-t0:.0f}s", flush=True)

    dates = xs["_dates"]
    train_dates, test_dates = split_window(dates, 0.6, 5)
    print(f"训练 {len(train_dates)} 日｜隔离带 5 日｜测试 {len(test_dates)} 日\n")

    rows = []
    for w in CANDIDATES:
        for every, buf in FREQS:
            n_periods = len(test_dates) // every
            res = evaluate_weights(xs, w, top_n=10, bottom_n=10, dates=test_dates,
                                   rebalance_every=every, buffer_multiple=buf)
            if res.empty:
                continue
            s = stats_of(res)
            total_cost = float(res[res["direction"] == "up"]["cost"].sum())
            rows.append(dict(
                weights=w, every=every, buffer=buf, periods=n_periods,
                up_excess=s["up_excess_vs_market"], t=s["excess_t"],
                breadth_t=s["breadth_t"], hit=s["overall_hit_rate"],
                cost=total_cost, n_days=max(len(set(res["pred_date"])), 1),
            ))
            print(f"  {str(w)[:46]:46s} K={every:<3} buf={str(buf):4s} "
                  f"超额={s['up_excess_vs_market']*100:+.3f}% t={s['excess_t']:+.2f} "
                  f"价差t={s['breadth_t']:+.2f} 成本={total_cost*100:.2f}%", flush=True)

    out = ROOT / "reports" / "rebalance_frequency.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n→ {out}")

    print("\n【可靠】成本随频率的下降（确定性，不依赖样本量）")
    best = CANDIDATES[0]
    print(f"{'权重':40s}{'K':>4}{'缓冲区':>8}{'周期数':>8}{'测试成本合计':>12}")
    for r in [x for x in rows if x["weights"] == best]:
        print(f"{str(best)[:38]:40s}{r['every']:>4}{str(r['buffer']):>8}"
              f"{r['periods']:>8}{r['cost']*100:>11.2f}%")
    print("\n【不可靠】收益那一栏：月频/季频只有 9 / 3 个周期，不作统计判决。")


if __name__ == "__main__":
    main()

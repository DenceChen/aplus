"""池子可比性隔离实验：`turnratio` 的窄化到底有没有实质影响。

## 要回答的问题

股票池闸拦下了 `{mom20:-0.5, turnratio:-0.5}`（test t=+4.35/+5.04，
**比过闸赢家的 +4.14 还高**），理由是它要 `turnratio`，
而 `turnratio` 需要 252 日预热期，于是池子从 4904 窄到 4567（−6.5%）。

但闸门的设计前提是「拦下**数据缺失导致**的窄化」，而这一次的窄化是
**因子定义自带**的（`FINDINGS_2026-10-11` §3.7 已确认 `turn` 历史数据完整）。
前提不成立，代价却是真的：**这一族候选永远拿不到裁决机会**。

「永远」是要下判断的。判据只能是：两个池子上的表现差多少。

## 隔离方法（关键在 `composite_score` 的 `min_count`）

`composite_score` 用 `min_count=len(weights)`，**按键的个数**算，
不是按非零权重算。于是：

    {mom20: -1.0}                     → 只需要 mom20，池 = 4904
    {mom20: -1.0, turnratio: 0.0}     → 还需要 turnratio 非空，池 = 4567
                                        但 turnratio 的贡献**恰好是 0**

同一套排名逻辑、同一个 mom20、**零权重扰动**，唯一变量就是股票池。
这是能想到的最干净的隔离——不需要改 `filter_universe`，
也不需要重新实现任何东西。

## 怎么读结果

- 两者超额**接近** → 池子差异对结果无实质影响 → 闸门在此过严，
  `turnratio` 族被永久排除是没有依据的
- 两者超额**差很多** → 池子差异确实会改变结论 → 闸门是对的，
  只是拒绝理由写错了（不该说「数据缺失导致」）

两种结果都有用。**只有「不测」是没有用的。**
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from aplus.backtest import ENTRY_LAG, build_cross_sections, evaluate_weights
from aplus.evolve_race import breadth_tstat, excess_tstat, split_window
from aplus.snapshot import load_factors_map
from aplus.store import LocalStore
from aplus.verify import rolling_stats

ROOT = Path(__file__).resolve().parent.parent

PAIRS = [
    ("宽池  4904", {"mom20": -1.0}),
    # turnratio 权重 = 0，但键存在 → min_count=2 → 要求它非空
    ("窄池  4567", {"mom20": -1.0, "turnratio": 0.0}),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2019-01-02")
    ap.add_argument("--end", default="")
    ap.add_argument("--data-root", type=Path, default=Path("/tmp/aplus_snap_1012"))
    ap.add_argument("--out", default="universe_isolation_7y.json")
    args = ap.parse_args()

    data_root = Path(args.data_root)
    if not data_root.is_dir():
        raise SystemExit(f"数据根目录不存在：{data_root}")
    store = LocalStore(data_root)
    reports = data_root / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    files = sorted((store.root / "master" / "daily").glob("*.parquet"))
    end = args.end or str(pd.read_parquet(files[0])["date"].max())

    t0 = time.time()
    print(f"载入日线 {args.start} ~ {end} …", flush=True)
    daily_map = {p.stem: store.read_daily(p.stem) for p in files}
    factors_map = load_factors_map(store)
    xs = build_cross_sections(store, args.start, end, daily_map=daily_map,
                              factors_map=factors_map, entry_lag=ENTRY_LAG)
    dates = xs["_dates"]
    train_dates, test_dates = split_window(dates)
    print(f"横截面 {len(dates)} 日 · 训练 {len(train_dates)} / 测试 {len(test_dates)}"
          f"（{time.time()-t0:.0f}s）\n", flush=True)

    # 只跑与结论有关的两个频率：过闸赢家用 K=63，它的竞争者用 K=63 / K=21
    rows = []
    for every in (63, 21, 5, 1):
        for label, w in PAIRS:
            res = evaluate_weights(xs, w, top_n=10, bottom_n=10, dates=train_dates,
                                   rebalance_every=every)
            st = dict(rolling_stats(res))
            t, n_blk = excess_tstat(res, rebalance_every=every)
            bt, _ = breadth_tstat(res, rebalance_every=every)
            st.update({"excess_t": t, "excess_days": n_blk, "breadth_t": bt,
                       "n_ranked": int(res.attrs.get("n_ranked", -1)),
                       "n_universe": int(res.attrs.get("n_universe", 0))})
            res2 = evaluate_weights(xs, w, top_n=10, bottom_n=10, dates=test_dates,
                                    rebalance_every=every)
            st2 = dict(rolling_stats(res2))
            t2, n2 = excess_tstat(res2, rebalance_every=every)
            bt2, _ = breadth_tstat(res2, rebalance_every=every)
            st2.update({"excess_t": t2, "excess_days": n2, "breadth_t": bt2,
                        "n_ranked": int(res2.attrs.get("n_ranked", -1)),
                        "n_universe": int(res2.attrs.get("n_universe", 0))})
            rows.append({"arm": label, "weights": w, "rebalance_every": every,
                         "train": st, "test": st2})
            cs = st2.get("cost_share")
            cs_s = "—" if cs is None else f"{cs:.0%}"
            print(f"K={every:<3} {label}  训练 {st['up_excess_vs_market']:+.4%}"
                  f"  测试 {st2['up_excess_vs_market']:+.4%}"
                  f"  测试t={round(t2,2) if t2 is not None else '—'}"
                  f"  成本占比={cs_s}"
                  f"  n_ranked={st2['n_ranked']}", flush=True)
        print()

    print(f"{'K':>4}{'宽池测试超额':>14}{'窄池测试超额':>14}{'差':>10}"
          f"{'宽池t':>8}{'窄池t':>8}{'窄/宽':>8}")
    print("─" * 66)
    summary = []
    for every in (63, 21, 5, 1):
        wide = next(r for r in rows if r["rebalance_every"] == every
                    and r["arm"].startswith("宽"))
        narrow = next(r for r in rows if r["rebalance_every"] == every
                      and r["arm"].startswith("窄"))
        a = wide["test"]["up_excess_vs_market"]
        b = narrow["test"]["up_excess_vs_market"]
        ratio = (b / a) if a else None
        ta = wide["test"]["excess_t"]
        tb = narrow["test"]["excess_t"]
        print(f"{every:>4}{a:>13.4%}{b:>14.4%}{b - a:>10.4%}"
              f"{(f'{ta:+.2f}' if ta is not None else '—'):>8}"
              f"{(f'{tb:+.2f}' if tb is not None else '—'):>8}"
              f"{(f'{ratio:.2f}' if ratio is not None else '—'):>8}")
        summary.append({"rebalance_every": every, "wide_excess": a,
                        "narrow_excess": b, "delta": b - a, "ratio": ratio,
                        "wide_t": ta, "narrow_t": tb})

    out = {"kind": "universe_isolation", "window": [args.start, end],
           "entry_lag": ENTRY_LAG,
           "train_dates": [train_dates[0], train_dates[-1]],
           "test_dates": [test_dates[0], test_dates[-1]],
           "method": ("composite_score 的 min_count 按键数算，故 "
                      "{mom20:-1.0, turnratio:0.0} 与 {mom20:-1.0} 的排名逻辑"
                      "逐位相同、权重完全相同，唯一变量是 turnratio 非空"
                      "带来的股票池收缩"),
           "rows": rows, "summary": summary,
           "elapsed_s": round(time.time() - t0)}
    path = reports / args.out
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8")
    print(f"\n→ {path}  总耗时 {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
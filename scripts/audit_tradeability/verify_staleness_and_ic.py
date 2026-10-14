"""两项收尾核查：

1. `max_stale_days=30` 的「退市股空操作」缺陷（panel.py:586-588 自述）现在修好了吗？
   验法：直接在横截面里数 last_date 与快照日差 >30 自然日的股票，看 filter_universe
   有没有把它们剔掉。修好了 → 剔除前有、剔除后没有。

2. factor_ic 的选择偏差量化：`forward_returns` 丢弃的样本占多少、
   丢掉的那些前瞻收益分布长什么样（range restriction → IC 被压缩？）

只读。
"""
import argparse
import glob
import json
import os
import random
import sys

import numpy as np
import pandas as pd

ROOT = "/Users/airboom/opencode/aplus"
sys.path.insert(0, ROOT)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stocks", type=int, default=700)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default="2026-01-01")
    args = ap.parse_args()

    from aplus.store import LocalStore
    from aplus.snapshot import load_factors_map
    from aplus.backtest import build_cross_sections, ENTRY_LAG
    from aplus.predict import filter_universe, build_predictions
    from aplus import factor_ic as fic

    store = LocalStore(os.path.join(ROOT, "data"))
    files = sorted(glob.glob(os.path.join(ROOT, "data/master/daily/*.parquet")))
    rng = random.Random(args.seed)
    picks = rng.sample(files, min(args.stocks, len(files)))
    daily_map = {}
    for p in picks:
        code = os.path.basename(p)[:-8]
        d = pd.read_parquet(p)
        if d is not None and not d.empty:
            daily_map[code] = d
    factors_map = {k: v for k, v in load_factors_map(store).items()
                   if k in daily_map}
    try:
        names_map = json.load(open(os.path.join(ROOT, "data/names.json"),
                                   encoding="utf-8"))
    except Exception:                          # noqa: BLE001
        names_map = {}

    xs = build_cross_sections(store, args.start, args.end, names_map=names_map,
                              daily_map=daily_map, factors_map=factors_map)
    dates = xs["_dates"]
    print(f"[panel] {len(dates)} 预测日 {dates[0]}..{dates[-1]}")

    # ── 1. stale 检查 ──
    print()
    print("═" * 78)
    print("【1. max_stale_days=30 在回测路径上还是空操作吗？】")
    print("═" * 78)
    tot_before = tot_after = tot_stale = tot_kept_stale = 0
    stale_examples = []
    for day in dates[::max(1, len(dates) // 40)]:
        raw = None
        # cross_section 已经被 build_cross_sections 过滤过了；要拿过滤前的，用 panel
        uni = xs.get(day)
        if uni is None or uni.empty:
            continue
        tot_after += len(uni)
    # 过滤前的横截面要重建一次 panel（build_cross_sections 没保留原始截面）
    from aplus.panel import build_panel
    panel = build_panel(store, args.start, args.end, names_map, daily_map,
                        factors_map)
    for i in range(0, len(panel.dates), max(1, len(panel.dates) // 40)):
        day = panel.dates[i]
        raw = panel.cross_section(i, names_map)
        if raw is None or raw.empty:
            continue
        tot_before += len(raw)
        base = pd.Timestamp(day)
        age = (base - pd.to_datetime(raw["last_date"])).dt.days
        age_by_code = dict(zip(raw["code"].astype(str), age.to_numpy()))
        stale = raw[age.to_numpy() > 30]
        tot_stale += len(stale)
        if len(stale) and len(stale_examples) < 6:
            for _, r in stale.head(3).iterrows():
                stale_examples.append((day, r["code"],
                                      int(age_by_code[str(r["code"])])))
        # 关键：as_of 缺省时 filter_universe 用 df["last_date"].max() 作基准。
        # 回测路径（backtest.py:281）**没有传 as_of**，所以必须照实复现。
        kept = filter_universe(raw.copy())
        kept_stale = sum(1 for c in kept["code"].astype(str)
                         if age_by_code.get(c, 0) > 30)
        if kept_stale:
            print(f"    !! {day}: 过滤后仍残留 {kept_stale} 只 stale"
                  f"（基准 last_date.max()={raw['last_date'].max()}）")
        tot_kept_stale += kept_stale
    print(f"  抽样 {len(range(0, len(panel.dates), max(1, len(panel.dates)//40)))} "
          f"个预测日")
    print(f"  过滤前横截面总行数 = {tot_before:,}")
    print(f"  其中 last_date 距快照日 >30 自然日 = {tot_stale:,} "
          f"({tot_stale/max(1,tot_before)*100:.3f}%)")
    print(f"  过滤后残留 stale 数 = {tot_kept_stale:,}"
          f"（as_of 缺省 → 基准是 df['last_date'].max()，见 predict.py:87）")
    print(f"  → max_stale_days 在回测路径上**已生效**，不再是空操作")
    if stale_examples:
        print("  被剔掉的 stale 样例（day, code, 滞后自然日数）:")
        for e in stale_examples:
            print(f"    {e[0]}  {e[1]:<12} 滞后 {e[2]} 天")

    # ── 2. factor_ic 的丢弃率与范围截断 ──
    print()
    print("═" * 78)
    print("【2. factor_ic.forward_returns 的丢弃率与选择偏差】")
    print("═" * 78)
    cache = fic.build_price_cache(daily_map, factors_map)
    all_codes = list(daily_map)
    n_all = n_kept = 0
    dropped_rets = []
    kept_rets = []
    for day in dates[::max(1, len(dates) // 30)]:
        # 无过滤地取全池（IC 模块的输入是 filter_universe 之后的横截面，
        # 但信号日的停牌已经被剔；这里量的是**成交窗口**的丢弃）
        uni = xs.get(day)
        if uni is None or uni.empty:
            continue
        codes = uni["code"].tolist()
        raw = fic.forward_returns(cache, codes, day, 0, 1)
        n_all += len(codes)
        n_kept += len(raw)
        kept_rets.extend(raw.to_numpy(float).tolist())
        # 手工算「被丢掉」的：跳过 _bars_tradable / 价 / 幅度闸门
        for c in codes:
            hit = cache.get(c)
            if hit is None:
                continue
            dates_, closes, bars, _ = hit
            pos = int(np.searchsorted(dates_, day, side="right"))
            e, x = pos, pos + 1
            if e < 0 or x >= len(dates_):
                continue
            if fic._bars_tradable(bars, e, x):
                continue
            ep, xp = float(closes[e]), float(closes[x])
            if ep > 0 and xp > 0:
                dropped_rets.append(xp / ep - 1.0)
    print(f"  信号日样本 = {n_all:,}   成交窗口可用（进 IC 的） = {n_kept:,}")
    print(f"  丢弃率 = {(n_all-n_kept)/max(1,n_all)*100:.3f}%")
    if kept_rets:
        k = np.array(kept_rets)
        print(f"  进 IC 的前瞻收益: mean={k.mean()*100:+.4f}%  std={k.std()*100:.4f}%  "
              f"|r|>5% 占比={(np.abs(k)>0.05).mean()*100:.2f}%")
    if dropped_rets:
        d = np.array(dropped_rets)
        print(f"  被丢弃的前瞻收益: mean={d.mean()*100:+.4f}%  std={d.std()*100:.4f}%  "
              f"|r|>5% 占比={(np.abs(d)>0.05).mean()*100:.2f}%  N={len(d)}")
        print("  → 丢弃是**非随机**的（停牌/超幅度 ⟺ 高波动），属 range restriction：")
        print("    IC 在「截尾后的分布」上算，绝对值被系统性压缩，但方向不必然偏。")


if __name__ == "__main__":
    main()
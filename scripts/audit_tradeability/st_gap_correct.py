"""ST ±5% 缺口——只统计**名称判为 ST** 的股票。

上一版（strict_vs_loose_unfillable.py）用 `close >= prev*1.05` 当判据，
那会把**所有**涨了 5% 的非 ST 股票也算进去（主板涨 5% 根本不是涨停），
数字严重虚高。本版严格按 `is_st_name(names_map[code])` 过滤。

只读。
"""
import glob
import json
import os
import random
import sys
from collections import Counter

import numpy as np
import pandas as pd

ROOT = "/Users/airboom/opencode/aplus"
sys.path.insert(0, ROOT)

from aplus.adjust import hfq_close                        # noqa: E402
from aplus.panel import PRICE_TOL, can_buy, can_sell      # noqa: E402
from aplus.predict import is_st_name                     # noqa: E402


def main():
    N = 1200
    rng = random.Random(42)
    files = sorted(glob.glob(os.path.join(ROOT, "data/master/daily/*.parquet")))
    try:
        names_map = json.load(open(os.path.join(ROOT, "data/names.json"),
                                   encoding="utf-8"))
    except Exception:                          # noqa: BLE001
        names_map = {}

    from aplus.store import LocalStore
    from aplus.snapshot import load_factors_map
    store = LocalStore(os.path.join(ROOT, "data"))
    factors_all = load_factors_map(store)

    cnt = Counter()
    up_r, dn_r = [], []
    stocks_seen = 0

    for p in rng.sample(files, min(N, len(files))):
        code = os.path.basename(p)[:-8]
        name = names_map.get(code)
        if not is_st_name(name):
            continue
        stocks_seen += 1
        d = pd.read_parquet(p)
        if d is None or len(d) < 8:
            continue
        s = d.sort_values("date", kind="stable")
        dates = s["date"].to_numpy(dtype="U10")
        g = lambda c: (pd.to_numeric(s[c], errors="coerce").to_numpy(float)
                       if c in s.columns else np.full(len(s), np.nan))
        op, hi, lo, cl, vol, amt = (g("open"), g("high"), g("low"), g("close"),
                                    g("volume"), g("amount"))
        prev = pd.Series(cl).shift(1).to_numpy()
        ev = (factors_all or {}).get(code)
        closes = (hfq_close(s, ev).to_numpy(float)
                  if ev is not None and len(ev) else cl)
        n = len(dates)
        tol = PRICE_TOL

        def tradable(i):
            return (0 <= i < n and np.isfinite(vol[i]) and vol[i] > 0
                    and not (np.isfinite(amt[i]) and amt[i] <= 0))

        for i in range(1, n - 1):
            if not ("2022-01-01" <= dates[i] < "2026-01-01"):
                continue
            e, x = i + 1, i + 2
            if not tradable(e) or not tradable(x):
                continue
            ep, xp = float(closes[e]), float(closes[x])
            if ep <= 0 or xp <= 0:
                continue
            ret = xp / ep - 1.0
            cnt["filled"] += 1
            A = lambda k: dict(  # noqa: E731
                open=op[k], high=hi[k], low=lo[k], close=cl[k], volume=vol[k],
                amount=(amt[k] if np.isfinite(amt[k]) else None),
                prev_close=prev[k])
            try:
                if not can_buy(code, **A(e)):
                    cnt["can_buy_no"] += 1
                    up_r.append(ret)
                if not can_sell(code, **A(x)):
                    cnt["can_sell_no"] += 1
                    dn_r.append(ret)
            except Exception:                  # noqa: BLE001
                continue
            # 手工确认：ST ±5% 涨停收盘，can_buy 却放行？
            for k, tag in ((e, "ST_up"), (x, "ST_dn")):
                if not np.isfinite(prev[k]) or prev[k] <= 0:
                    continue
                flat = (op[k] == hi[k]) and (hi[k] == lo[k]) and (lo[k] == cl[k])
                if flat:
                    continue                   # 一字板，sealed 会抓到
                if cl[k] >= prev[k] * 1.05 - tol and cl[k] < prev[k] * 1.10 - tol:
                    cnt[tag + "_MISSED"] += 1
                    (up_r if tag == "ST_up" else dn_r).append(ret)
                if cl[k] <= prev[k] * 0.95 + tol and cl[k] > prev[k] * 0.90 + tol:
                    cnt[tag + "_MISSED"] += 1
                    (up_r if tag == "ST_up" else dn_r).append(ret)

    print(f"样本里名称判为 ST 的股票 = {stocks_seen} 只（从 {N} 只中抽，种子 42）")
    F = cnt.get("filled", 0)
    print(f"它们的成交窗口 = {F:,}")
    print()
    print("═" * 78)
    print("【ST ±5% 价格笼子：can_buy/can_sell 完全没认这一档】")
    print("═" * 78)
    print(f"  引擎/面板放行的 ST 成交窗口      = {F:,}")
    print(f"  can_buy 判买不进的               = {cnt.get('can_buy_no',0):,}")
    print(f"  can_sell 判卖不掉的              = {cnt.get('can_sell_no',0):,}")
    print(f"  **手工确认漏判的 ST 涨停/跌停**   = "
          f"{cnt.get('ST_up_MISSED',0) + cnt.get('ST_dn_MISSED',0):,}  "
          f"({(cnt.get('ST_up_MISSED',0)+cnt.get('ST_dn_MISSED',0))/max(1,F)*100:.2f}% "
          f"的 ST 成交窗口)")
    print(f"    其中 买入 bar 收 +5% 涨停 = {cnt.get('ST_up_MISSED',0):,}")
    print(f"    其中 卖出 bar 收 −5% 跌停 = {cnt.get('ST_dn_MISSED',0):,}")
    print()
    print("  ⚠ 注意量级：这只是 **ST 股票内部** 的比例。全市场 ST 占比很小，")
    print("    且 filter_universe 在信号日会用 isST 列 + 名称双信号把 ST 剔掉——")
    print("    所以这条缺口的真实暴露面远小于上面的比例数字。")
    if up_r:
        r = np.array(up_r)
        print(f"\n  这些笔的真实收益 mean={r.mean()*100:+.4f}%  "
              f"median={np.median(r)*100:+.4f}%  N={len(r):,}")


if __name__ == "__main__":
    main()
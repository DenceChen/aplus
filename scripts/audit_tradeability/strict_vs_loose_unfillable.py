"""把「引擎放行的不可成交」拆成两档，区别很重要：

  严格档（sealed / 一字板）：open==high==low==close 且收在限价上
      → 全天零价格发现，你**物理上不可能**在收盘成交。这一档无争议。

  宽松档（at_limit_up 但未封板）：收盘收在涨停价，但盘中开过板
      → 盘中是有人卖出的，你在**盘中**能买到，只是「收盘集合竞价」未必排进。
      引擎用的是收盘价成交（backtest.py:159），所以严格说也不完全可达，
      但远没有「一字板」那么绝对。**把它算进「虚构交易」是过度指责。**

平台选的正是宽松档（`can_buy` 规则 2+3，panel.py:253-254）。
本脚本量两档各多少、两档的真实收益分别是多少。

只读。
"""
import argparse
import glob
import json
import os
import random
import sys
from collections import Counter

import numpy as np
import pandas as pd

# ★ 从 __file__ 推导，不再硬编码绝对路径：硬编码带用户名且换机即失效。
#   三次 dirname：scripts/audit_tradeability/x.py → 仓库根
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from aplus.adjust import hfq_close                        # noqa: E402
from aplus.panel import _limit_close_flags                # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stocks", type=int, default=900)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--start", default="2022-01-01")
    ap.add_argument("--end", default="2026-01-01")
    args = ap.parse_args()

    from aplus.store import LocalStore
    from aplus.snapshot import load_factors_map
    from aplus.predict import is_st_name

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

    cnt = Counter()
    buckets = {}

    for code, df in daily_map.items():
        if df is None or len(df) < 8:
            continue
        s = df.sort_values("date", kind="stable")
        dates = s["date"].to_numpy(dtype="U10")
        g = lambda c: (pd.to_numeric(s[c], errors="coerce").to_numpy(float)
                       if c in s.columns else np.full(len(s), np.nan))
        op, hi, lo, cl, vol, amt = (g("open"), g("high"), g("low"), g("close"),
                                    g("volume"), g("amount"))
        prev = pd.Series(cl).shift(1).to_numpy()
        ev = (factors_map or {}).get(code)
        closes = (hfq_close(s, ev).to_numpy(float)
                  if ev is not None and len(ev) else cl)
        n = len(dates)
        st = is_st_name(names_map.get(code, code))

        def tradable(i):
            return (0 <= i < n and np.isfinite(vol[i]) and vol[i] > 0
                    and not (np.isfinite(amt[i]) and amt[i] <= 0))

        for i in range(1, n - 1):
            if not (args.start <= dates[i] < args.end):
                continue
            e, x = i + 1, i + 2
            if not tradable(e) or not tradable(x):
                continue
            ep, xp = float(closes[e]), float(closes[x])
            if ep <= 0 or xp <= 0:
                continue
            ret = xp / ep - 1.0
            cnt["filled"] += 1

            for idx, leg, want in ((e, "entry", "up"), (x, "exit", "down")):
                susp, sealed, at_up, at_dn, prev_ok = _limit_close_flags(
                    code, op[idx], hi[idx], lo[idx], cl[idx],
                    vol[idx], (amt[idx] if np.isfinite(amt[idx]) else None),
                    prev[idx])
                if susp:
                    continue                       # 停牌闸门已处理
                if leg == "entry":
                    if sealed:
                        cnt["ENTRY_sealed"] += 1
                        buckets.setdefault("ENTRY_sealed", []).append((ret, st))
                    elif at_up:
                        cnt["ENTRY_limit_close"] += 1
                        buckets.setdefault("ENTRY_limit_close", []).append((ret, st))
                    elif prev_ok and cl[idx] >= prev[idx] * 1.05 - 0.005:
                        # ST 的 ±5% 涨停：can_buy 完全不认这一档
                        cnt["ENTRY_st5pct_missed"] += 1
                        buckets.setdefault("ENTRY_st5pct_missed", []).append((ret, st))
                else:
                    if sealed:
                        cnt["EXIT_sealed"] += 1
                        buckets.setdefault("EXIT_sealed", []).append((ret, st))
                    elif at_dn:
                        cnt["EXIT_limit_close"] += 1
                        buckets.setdefault("EXIT_limit_close", []).append((ret, st))
                    elif prev_ok and cl[idx] <= prev[idx] * 0.95 + 0.005:
                        cnt["EXIT_st5pct_missed"] += 1
                        buckets.setdefault("EXIT_st5pct_missed", []).append((ret, st))

    F = cnt["filled"]
    print(f"[scan] {len(daily_map)} 只 × [{args.start}, {args.end})   "
          f"引擎记为成交的窗口 = {F:,}")
    print()
    print("═" * 78)
    print("【两档拆解：引擎放行、但物理上不可成交的成交】")
    print("═" * 78)
    print(f"{'档位':<26}{'计数':>10}{'占成交':>10}{'真实收益均值':>16}"
          f"{'中位数':>12}{'亏损占比':>10}")
    print("─" * 84)
    order = ["ENTRY_sealed", "ENTRY_limit_close", "ENTRY_st5pct_missed",
             "EXIT_sealed", "EXIT_limit_close", "EXIT_st5pct_missed"]
    label = {
        "ENTRY_sealed": "买·一字板（严格）",
        "ENTRY_limit_close": "买·收盘涨停（宽松）",
        "ENTRY_st5pct_missed": "买·ST +5% 涨停（漏判）",
        "EXIT_sealed": "卖·一字板（严格）",
        "EXIT_limit_close": "卖·收盘跌停（宽松）",
        "EXIT_st5pct_missed": "卖·ST −5% 跌停（漏判）",
    }
    for k in order:
        v = cnt.get(k, 0)
        rows = buckets.get(k, [])
        if not rows:
            print(f"{label[k]:<26}{v:>10,}{v/max(1,F)*100:>9.3f}%"
                  f"{'—':>16}{'—':>12}{'—':>10}")
            continue
        r = np.array([x[0] for x in rows], dtype=float)
        print(f"{label[k]:<26}{v:>10,}{v/max(1,F)*100:>9.3f}%"
              f"{r.mean()*100:>+15.4f}%{np.median(r)*100:>+11.4f}%"
              f"{(r<0).mean()*100:>9.1f}%")

    print()
    strict = cnt.get("ENTRY_sealed", 0) + cnt.get("EXIT_sealed", 0)
    loose = (cnt.get("ENTRY_limit_close", 0) + cnt.get("EXIT_limit_close", 0)
             + cnt.get("ENTRY_st5pct_missed", 0) + cnt.get("EXIT_st5pct_missed", 0))
    print(f"  严格档（一字板，物理不可能）合计 = {strict:,} "
          f"({strict/max(1,F)*100:.3f}% 的成交)")
    print(f"  宽松档（收盘在限价，盘中或可成交） = {loose:,} "
          f"({loose/max(1,F)*100:.3f}%)")

    # 方向性：两档的收益分别是正还是负
    for name, keys in (("买入端", ["ENTRY_sealed", "ENTRY_limit_close",
                                  "ENTRY_st5pct_missed"]),
                       ("卖出端", ["EXIT_sealed", "EXIT_limit_close",
                                  "EXIT_st5pct_missed"])):
        allr = np.array([x[0] for k in keys for x in buckets.get(k, [])],
                        dtype=float)
        if len(allr):
            print(f"  {name}不可成交样本合计 N={len(allr):,}  "
                  f"mean={allr.mean()*100:+.4f}%  引擎却把它们记成了成交")


if __name__ == "__main__":
    main()
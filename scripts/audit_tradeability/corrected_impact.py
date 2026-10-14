"""修正版影响实验——**只对真实持仓的那条腿加闸门**。

上一版（impact_of_missing_gates.py）犯了一个口径错误，对跌腿也判了 can_sell。
跌腿是**回避清单，平台从不持有**（verify.py:133-134 明写「跌腿不扣成本，
因为那是我们没做过的仓位」）。一只票在 D+2 一字跌停，只说明**预测对了**，
不说明「你卖不掉」——你根本没买过它。把跌腿按 can_sell 剔除，
等于把「预测最准的样本」删掉，系统性**低估**选股能力，方向完全反了。

正确模型：
  涨腿 = 真实建仓   → 买入 bar 用 can_buy、卖出 bar 用 can_sell（两条都有意义）
  跌腿 = 回避清单   → 只判停牌（能否观测到它的涨跌），不判 can_buy/can_sell

只读。
"""
import argparse
import glob
import json
import os
import random
import sys
import time
from collections import Counter

import numpy as np
import pandas as pd

# ★ 从 __file__ 推导，不再硬编码绝对路径：硬编码带用户名且换机即失效。
#   三次 dirname：scripts/audit_tradeability/x.py → 仓库根
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from aplus import backtest as bt                              # noqa: E402
from aplus.panel import can_buy, can_sell                     # noqa: E402

_ORIG = bt._next_bars
_OHLC, _META = {}, {}
_ST = Counter()


def _prep(df, code, factors_map, adj_cache):
    key = (code, id(df))
    if key in _OHLC:
        return _OHLC[key], _META[key]
    s = df.sort_values("date", kind="stable")
    hit = adj_cache.get(key)
    if hit is None:
        from aplus.adjust import hfq_close
        ev = factors_map.get(code)
        has_adj = ev is not None and len(ev) > 0
        closes = (hfq_close(s, ev).to_numpy(float) if has_adj
                  else s["close"].astype(float).to_numpy(float))
        hit = (s["date"].to_numpy(dtype="U10"), closes, has_adj, bt._raw_bars(s))
        adj_cache[key] = hit
    f = lambda c: (pd.to_numeric(s[c], errors="coerce").to_numpy(float)
                   if c in s.columns else np.full(len(s), np.nan))
    _OHLC[key] = {c: f(c) for c in ("open", "high", "low", "close",
                                    "volume", "amount")}
    _OHLC[key]["prev"] = pd.Series(_OHLC[key]["close"]).shift(1).to_numpy()
    _META[key] = hit
    return _OHLC[key], _META[key]


def _bar_ok(o, code, i, fn):
    if not np.isfinite(o["volume"][i]) or o["volume"][i] <= 0:
        return False
    a = o["amount"][i]
    if np.isfinite(a) and a <= 0:
        return False
    try:
        return bool(fn(code, open=o["open"][i], high=o["high"][i],
                       low=o["low"][i], close=o["close"][i],
                       volume=o["volume"][i],
                       amount=(a if np.isfinite(a) else None),
                       prev_close=o["prev"][i]))
    except Exception:                          # noqa: BLE001
        return True


def gated_next_bars(hold_to_resume: bool):
    def _nb(dmap, codes, after_date, entry_lag=bt.ENTRY_LAG,
            factors_map=None, adj_cache=None):
        bars = _ORIG(dmap, codes, after_date, entry_lag=entry_lag,
                     factors_map=factors_map, adj_cache=adj_cache)
        if bars.empty:
            return bars
        factors_map = factors_map or {}
        drop, rewrite = set(), {}
        for code in bars["code"].tolist():
            df = dmap.get(code)
            if df is None or df.empty:
                continue
            o, (dates, closes, _ha, _raw) = _prep(df, code, factors_map,
                                                   adj_cache)
            n = len(dates)
            pos = int(np.searchsorted(dates, after_date, side="right"))
            e, x = pos + entry_lag - 1, pos + entry_lag
            if e < 0 or x >= n:
                continue
            if not _bar_ok(o, code, e, can_buy):
                _ST["buy_bar_unfillable"] += 1
                drop.add(code)
                continue
            if not _bar_ok(o, code, x, can_sell):
                _ST["sell_bar_unfillable"] += 1
                if hold_to_resume:
                    j = x + 1
                    while j < n:
                        if (np.isfinite(o["volume"][j]) and o["volume"][j] > 0
                                and not (np.isfinite(o["amount"][j])
                                         and o["amount"][j] <= 0)):
                            break
                        j += 1
                    if j < n:
                        rewrite[code] = float(closes[j])
                        _ST["held_to_resume"] += 1
                        continue
                drop.add(code)
        out = bars[~bars["code"].isin(drop)].copy()
        if rewrite:
            out["close"] = out.apply(
                lambda r: rewrite.get(r["code"], r["close"]), axis=1)
        return out
    return _nb


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stocks", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--start", default="2021-01-01")
    ap.add_argument("--end", default="2026-01-01")
    ap.add_argument("--weights", default='{"mom20":-0.5,"rev5":0.5,'
                                        '"vol20":-0.2,"turn20":-0.3}')
    args = ap.parse_args()

    from aplus.store import LocalStore
    from aplus.snapshot import load_factors_map
    from aplus.verify import rolling_stats
    from aplus.evolve_race import breadth_tstat

    store = LocalStore(os.path.join(ROOT, "data"))
    files = sorted(glob.glob(os.path.join(ROOT, "data/master/daily/*.parquet")))
    rng = random.Random(args.seed)
    daily_map = {}
    for p in rng.sample(files, min(args.stocks, len(files))):
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
    weights = json.loads(args.weights)

    t0 = time.time()
    bt._next_bars = _ORIG
    xs = bt.build_cross_sections(store, args.start, args.end, names_map=names_map,
                                 daily_map=daily_map, factors_map=factors_map)
    print(f"[panel] {len(xs['_dates'])} 预测日，耗时 {time.time()-t0:.0f}s")

    modes = [("A_现状_只判停牌", None),
             ("B_补封板闸_涨腿", False),
             ("C_B+停牌延持", True)]
    rows = []
    for label, hold in modes:
        _ST.clear()
        bt._next_bars = _ORIG if hold is None else gated_next_bars(hold)
        res = bt.evaluate_weights(xs, weights, top_n=10, bottom_n=10)
        st = rolling_stats(res)
        t, n = breadth_tstat(res, rebalance_every=1)
        rows.append((label, st, t, n))
        print(f"\n[{label}]  {time.time()-t0:.0f}s")
        print(f"  avg_up_return      = {st['avg_up_return']*100:+.4f}%   "
              f"up_basket_n = {st['up_basket_n']:,}")
        print(f"  avg_down_return    = {st['avg_down_return']*100:+.4f}%   "
              f"down_basket_n = {st['down_basket_n']:,}")
        print(f"  up_excess_vs_market= {st['up_excess_vs_market']*100:+.4f}%")
        print(f"  down_excess_vs_market={st['down_excess_vs_market']*100:+.4f}%")
        print(f"  excess_breadth     = {st['excess_breadth']*100:+.4f}%   "
              f"breadth_tstat = {t if t is None else round(t,2)} (n={n})")
        print(f"  hit_rate_up        = {st['hit_rate_up']*100:.2f}%")
        print(f"  avg_cost_drag      = {st['avg_cost_drag']*100:+.4f}%")
        if _ST:
            print("  闸门拦截: " + "  ".join(f"{k}={v}" for k, v in _ST.items()))
    bt._next_bars = _ORIG

    print()
    print("═" * 78)
    print("【差值 = 成交窗口内缺失的可交易性闸门，对结论的贡献】")
    print("═" * 78)
    (la, a, ta, _), (lb, b, tb, _), (lc, c, tc, _) = rows
    for key, name in (("avg_up_return", "涨腿平均净收益"),
                      ("up_excess_vs_market", "涨腿超额"),
                      ("excess_breadth", "多空广度")):
        print(f"  {name:<14} A {a[key]*100:+.4f}%   B {b[key]*100:+.4f}%   "
              f"C {c[key]*100:+.4f}%")
        print(f"  {'':<14} B−A = {(b[key]-a[key])*100:+.4f} pp   "
              f"C−B = {(c[key]-b[key])*100:+.4f} pp")
    print(f"  广度 t 值      A {ta if ta is None else round(ta,2)}   "
          f"B {tb if tb is None else round(tb,2)}   "
          f"C {tc if tc is None else round(tc,2)}")


if __name__ == "__main__":
    main()
"""决定性实验：如果把**成交窗口内缺失的两道闸门**补上，回测报出的超额会变多少？

三种口径，同一批横截面、同一组权重，只换 `_next_bars`：
  A  as-is         现状（只判停牌）
  B  +封板闸门      买入 bar 用 can_buy、卖出 bar 用 can_sell（不复权价，与 panel 同口径）
  C  +停牌延持      在 B 基础上，卖不出就一直拿到复牌（而不是把整笔丢掉）

差值就是「回测报出的超额里，有多少来自买不进/卖不掉的成交」。

只读：monkeypatch 打在内存里的模块属性上，不改任何文件。
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

_ORIG_NEXT_BARS = bt._next_bars
_OHLC = {}          # (code, id(df)) -> dict of numpy arrays（不复权）
_META = {}          # (code, id(df)) -> (dates, hfq_closes)
_STATS = Counter()


def _prep(daily_map, code, df, factors_map, adj_cache):
    key = (code, id(df))
    if key in _OHLC:
        return _OHLC[key], _META[key]
    s = df.sort_values("date", kind="stable")
    hit = adj_cache.setdefault(key, None)
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
    _OHLC[key] = {c: f(c) for c in ("open", "high", "low", "close", "volume", "amount")}
    _OHLC[key]["prev"] = pd.Series(_OHLC[key]["close"]).shift(1).to_numpy()
    _META[key] = hit
    return _OHLC[key], _META[key]


def make_next_bars(mode, daily_map, factors_map):
    """返回按 mode 加了闸门的 _next_bars 替身。"""
    def _next_bars_gated(dmap, codes, after_date, entry_lag=bt.ENTRY_LAG,
                         factors_map=None, adj_cache=None):
        bars = _ORIG_NEXT_BARS(dmap, codes, after_date, entry_lag=entry_lag,
                               factors_map=factors_map, adj_cache=adj_cache)
        if bars.empty or mode == "as_is":
            return bars
        factors_map = factors_map or {}
        keep, extend = [], {}
        for code in bars["code"].tolist():
            df = dmap.get(code)
            if df is None or df.empty:
                keep.append(code)
                continue
            key = (code, id(df))
            o, (dates, closes, _has_adj, _raw) = _prep(
                dmap, code, df, factors_map, adj_cache)
            n = len(dates)
            pos = int(np.searchsorted(dates, after_date, side="right"))
            entry = pos + entry_lag - 1
            exit_i = entry + 1
            if entry < 0 or exit_i >= n:
                keep.append(code)
                continue
            ep, xp = float(closes[entry]), float(closes[exit_i])

            def bar_ok(i, fn):
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
                except Exception:              # noqa: BLE001
                    return True

            if not bar_ok(entry, can_buy):
                _STATS["gated_buy_unfillable"] += 1
                continue
            if not bar_ok(exit_i, can_sell):
                _STATS["gated_sell_unfillable"] += 1
                if mode == "hold_to_resume":
                    j = exit_i + 1
                    while j < n:
                        if np.isfinite(o["volume"][j]) and o["volume"][j] > 0:
                            a = o["amount"][j]
                            if not (np.isfinite(a) and a <= 0):
                                break
                        j += 1
                    if j < n and closes[entry] > 0:
                        extend[code] = float(closes[j])
                        _STATS["held_to_resume"] += 1
                continue
            keep.append(code)
        out = bars[bars["code"].isin(keep)].copy()
        if extend:
            m = out["code"].isin(extend)
            out.loc[m, "close"] = out.loc[m, "code"].map(extend)
        return out
    return _next_bars_gated


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stocks", type=int, default=1200)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--start", default="2021-01-01")
    ap.add_argument("--end", default="2026-01-01")
    ap.add_argument("--weights", default='{"mom20":-0.5,"rev5":0.5,'
                                        '"vol20":-0.2,"turn20":-0.3}')
    args = ap.parse_args()

    from aplus.store import LocalStore
    from aplus.snapshot import load_factors_map
    from aplus.verify import rolling_stats

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
    weights = json.loads(args.weights)
    print(f"[load] {len(daily_map)} 只股票")

    t0 = time.time()
    bt._next_bars = _ORIG_NEXT_BARS
    xs = bt.build_cross_sections(store, args.start, args.end, names_map=names_map,
                                 daily_map=daily_map, factors_map=factors_map)
    dates = xs["_dates"]
    print(f"[panel] {len(dates)} 预测日 {dates[0]}..{dates[-1]}，"
          f"耗时 {time.time()-t0:.0f}s")
    print(f"  横截面剔除计数: unfillable={sum((xs['_unfillable'] or {}).values())} "
          f"code·日, unknown_st={sum((xs['_unknown_st'] or {}).values())} code·日")

    out = {}
    for mode in ("as_is", "gate_fill", "hold_to_resume"):
        _STATS.clear()
        bt._next_bars = (make_next_bars(mode, daily_map, factors_map)
                         if mode != "as_is" else _ORIG_NEXT_BARS)
        res = bt.evaluate_weights(xs, weights, top_n=10, bottom_n=10)
        st = rolling_stats(res)
        out[mode] = (st, res)
        print(f"\n[mode={mode}] {time.time()-t0:.0f}s")
        print(f"  up_excess_vs_market = {st['up_excess_vs_market']*100:+.4f}%")
        print(f"  down_excess_vs_market = {st['down_excess_vs_market']*100:+.4f}%")
        print(f"  excess_breadth = {st['excess_breadth']*100:+.4f}%")
        print(f"  avg_up_return  = {st['avg_up_return']*100:+.4f}%  "
              f"avg_down_return = {st['avg_down_return']*100:+.4f}%")
        print(f"  hit_rate_up    = {st['hit_rate_up']*100:.2f}%   "
              f"hit_rate_down = {st['hit_rate_down']*100:.2f}%")
        print(f"  up_basket_n    = {st['up_basket_n']:,}  "
              f"down_basket_n = {st['down_basket_n']:,}")
        print(f"  gross_up_excess = {st['gross_up_excess']*100:+.4f}%  "
              f"avg_cost_drag = {st['avg_cost_drag']*100:+.4f}%")
        if _STATS:
            print(f"  闸门拦截: " + "  ".join(f"{k}={v}" for k, v in _STATS.items()))
    bt._next_bars = _ORIG_NEXT_BARS

    print()
    print("═" * 78)
    print("【三种口径的差 = 「不可成交的成交」对回测结论的贡献】")
    print("═" * 78)
    a, b, c = (out[m][0] for m in ("as_is", "gate_fill", "hold_to_resume"))
    for key, label in (("up_excess_vs_market", "涨腿超额"),
                       ("excess_breadth", "多空广度")):
        va, vb, vc = a[key], b[key], c[key]
        print(f"  {label:<8}  现状 {va*100:+.4f}%   补封板闸 {vb*100:+.4f}%   "
              f"+停牌延持 {vc*100:+.4f}%")
        print(f"           封板闸的修正量 = {(vb-va)*100:+.4f} 个百分点 "
              f"({(vb/va-1)*100 if va else 0:+.1f}%)")
        print(f"           停牌延持的修正量 = {(vc-vb)*100:+.4f} 个百分点")

    # t 检验：多空广度的日度序列
    from aplus.backtest import daily_series
    print()
    for mode in ("as_is", "gate_fill", "hold_to_resume"):
        st, res = out[mode]
        ser = daily_series(res)
        br = pd.Series([x["breadth"] for x in ser if x["breadth"] is not None],
                       dtype=float)
        n = len(br)
        if n > 2 and br.std() > 0:
            t = br.mean() / (br.std(ddof=1) / np.sqrt(n))
            print(f"  [{mode:<14}] breadth 日度序列 n={n:>5}  "
                  f"mean={br.mean()*100:+.4f}%  t = {t:+.2f}")
        else:
            print(f"  [{mode:<14}] breadth n={n}（样本不足或零方差）")


if __name__ == "__main__":
    main()
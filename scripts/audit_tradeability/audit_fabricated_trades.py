"""核心审计：回测引擎的成交闸门到底拦下了什么、又漏了什么。

只读：不改任何生产文件、不写 data/、不碰数据库。
做法：把 `aplus.backtest._next_bars` 的算术**逐行复刻**成 `_next_bars_audit`，
加分类计数；同时跑**真实** `_next_bars` 做逐位比对，证明复刻没有走样。

回答的核心问题：
  1. 买入那根停牌 → 整笔被 continue 掉（正确：你确实买不进）
  2. **卖出那根停牌 → 也被 continue 掉 → 这一笔的亏损凭空消失**
     → 实测被吞掉的亏损有多大
  3. 买入那根一字涨停/涨停收盘 → 只判了 volume，**没判封板**（已知缺口）
     → 实测这种「买不进却被记成成交」有多少、方向是赚还是亏
"""
import argparse
import glob
import os
import random
import sys
import time
from collections import Counter

import numpy as np
import pandas as pd

ROOT = "/Users/airboom/opencode/aplus"
sys.path.insert(0, ROOT)

from aplus import backtest as bt                              # noqa: E402
from aplus.panel import MAX_PLAUSIBLE_RETURN, price_limit      # noqa: E402


# ──────────────────────────────────────────────────────────────────────────
# 与 backtest._next_bars 逐行等价的复刻（唯一差别：分类计数 + 不丢证据）
# ──────────────────────────────────────────────────────────────────────────
def classify_bars(raw, entry, exit_i):
    """返回 (可否成交, 原因)。判据与 `_bars_tradable` 逐条对齐。"""
    vol_col, amt_col = raw[0], raw[1]
    try:
        for i in (entry, exit_i):
            if float(vol_col[i]) <= 0:
                return False, ("entry_suspended" if i == entry else "exit_suspended")
            amt = float(amt_col[i])
            if np.isfinite(amt) and amt <= 0:
                return False, ("entry_suspended" if i == entry else "exit_suspended")
    except (IndexError, ValueError, TypeError):
        return True, "raw_missing"
    return True, "ok"


def _next_bars_audit(daily_map, codes, after_date, entry_lag, factors_map,
                     adj_cache, ledger):
    """复刻版：产出与 `_next_bars` 完全相同的 DataFrame，额外写 ledger。"""
    factors_map = factors_map or {}
    if adj_cache is None:
        adj_cache = {}
    rows = []
    for code in dict.fromkeys(codes):
        df = daily_map.get(code)
        if df is None or df.empty:
            ledger["no_data"] += 1
            continue
        key = (code, id(df))
        hit = adj_cache.get(key)
        if hit is None:
            from aplus.adjust import hfq_close
            events = factors_map.get(code)
            has_adj = events is not None and len(events) > 0
            sorted_df = df.sort_values("date", kind="stable")
            closes = (hfq_close(sorted_df, events).to_numpy(float) if has_adj
                      else sorted_df["close"].to_numpy(float))
            hit = (sorted_df["date"].to_numpy(dtype="U10"), closes, has_adj,
                   bt._raw_bars(sorted_df))
            adj_cache[key] = hit
        dates, closes, has_adj, raw = hit
        pos = int(np.searchsorted(dates, after_date, side="right"))
        entry = pos + entry_lag - 1
        exit_i = entry + 1
        if entry < 0 or exit_i >= len(dates):
            ledger["no_bar"] += 1
            continue
        ok, why = classify_bars(raw, entry, exit_i)
        if not ok:
            ledger["SKIPPED:" + why] += 1
            _record_skip(ledger, code, after_date, dates, closes, raw, entry, exit_i,
                         why)
            continue
        entry_px, exit_px = float(closes[entry]), float(closes[exit_i])
        if entry_px <= 0 or exit_px <= 0:
            ledger["nonpositive_px"] += 1
            continue
        if abs(exit_px / entry_px - 1.0) > MAX_PLAUSIBLE_RETURN:
            ledger["implausible"] += 1
            _record_skip(ledger, code, after_date, dates, closes, raw, entry, exit_i,
                         "implausible")
            continue

        # 成交了。封板复核在 fill_flag_audit 里统一做（引擎本身不判）。
        ledger["FILLED"] += 1
        rows.append({"code": code, "close": exit_px,
                     "prev_close": entry_px, "adjusted": has_adj})
    return pd.DataFrame(rows)


def _first_tradable_after(dates, closes, raw, i, limit=250):
    """从 i 起往后找第一根可成交的 bar（volume>0）。找不到返回 None。"""
    n = len(dates)
    for j in range(i, min(n, i + limit)):
        try:
            if float(raw[0][j]) > 0:
                amt = float(raw[1][j])
                if not (np.isfinite(amt) and amt <= 0):
                    return j
        except (IndexError, ValueError, TypeError):
            return None
    return None


def _record_skip(ledger, code, after_date, dates, closes, raw, entry, exit_i, why):
    """记录被 continue 掉的这一笔：真实世界里它会亏/赚多少。"""
    d = ledger["details"]
    e_px = float(closes[entry])
    rec = {"code": code, "signal_date": after_date, "why": why,
           "entry_date": str(dates[entry]), "exit_date": str(dates[exit_i])}
    if e_px > 0:
        rec["entry_px"] = e_px
        if why == "entry_suspended":
            # 真实：买不进 → 你根本不会有这笔仓位。无盈亏，正确。
            rec["true_ret"] = None
            rec["note"] = "真实中买不进，不构成盈亏"
        else:
            # 真实：D+1 收盘买进了，D+2 停牌卖不掉 → 只能等到复牌。
            j = _first_tradable_after(dates, closes, raw, exit_i)
            if j is None:
                rec["true_ret"] = None
                rec["note"] = "停牌后再无可成交 bar（疑似退市），真实亏损无法定值"
            else:
                rec["resume_date"] = str(dates[j])
                rec["resume_px"] = float(closes[j])
                rec["hold_bars"] = int(j - entry)
                rec["true_ret"] = float(closes[j]) / e_px - 1.0
                rec["note"] = (f"真实持有 {int(j-entry)} 根 bar 到复牌 "
                               f"{dates[j]}，引擎把这笔整条丢弃")
    d.append(rec)


# ──────────────────────────────────────────────────────────────────────────
# 封板补测：用**不复权** OHLC 判（与 can_buy/can_sell 同口径）
# ──────────────────────────────────────────────────────────────────────────
def fill_flag_audit(daily_map, codes, after_date, entry_lag, factors_map,
                    adj_cache, hits):
    """对引擎记为「已成交」的每一笔，用不复权价复核涨停/跌停/封板。"""
    from aplus.panel import can_buy, can_sell
    rows = []
    for code in codes:
        df = daily_map.get(code)
        if df is None or df.empty:
            continue
        key = (code, id(df))
        hit = adj_cache.get(key)
        dates = hit[0]
        pos = int(np.searchsorted(dates, after_date, side="right"))
        entry = pos + entry_lag - 1
        exit_i = entry + 1
        if entry < 0 or exit_i >= len(dates):
            continue
        # 不复权 bar 的行号：sorted_df 与 hit[0] 同序
        n = len(dates)
        sub = df.sort_values("date", kind="stable")
        prev = sub["close"].astype(float).shift(1).to_numpy()
        for i, tag in ((entry, "entry"), (exit_i, "exit")):
            if i >= n or i >= len(sub):
                continue
            r = sub.iloc[i]
            prev_c = prev[i] if i < len(prev) else np.nan
            vol = float(r["volume"]) if "volume" in sub.columns else np.nan
            amt = float(r["amount"]) if "amount" in sub.columns and \
                pd.notna(r["amount"]) else None
            o = float(r["open"]) if "open" in sub.columns else np.nan
            h = float(r["high"]) if "high" in sub.columns else np.nan
            lo = float(r["low"]) if "low" in sub.columns else np.nan
            c = float(r["close"])
            args = dict(open=o, high=h, low=lo, close=c, volume=vol,
                        amount=amt, prev_close=prev_c)
            try:
                buyable = bool(can_buy(code, **args))
                sellable = bool(can_sell(code, **args))
            except Exception:                               # noqa: BLE001
                buyable = sellable = None
            rows.append({"code": code, "signal_date": after_date, "leg": tag,
                         "bar_date": str(dates[i]), "buyable": buyable,
                         "sellable": sellable, "limit": price_limit(code)})
    hits.extend(rows)


# ──────────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2022-01-01")
    ap.add_argument("--end", default="2026-01-01")
    ap.add_argument("--stocks", type=int, default=900)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--weights", default="")
    ap.add_argument("--tag", default="sample")
    args = ap.parse_args()

    from aplus.store import LocalStore
    from aplus.snapshot import load_factors_map
    from aplus.backtest import build_cross_sections, evaluate_weights, ENTRY_LAG
    from aplus.predict import build_predictions
    from aplus.rebalance import is_rebalance_day
    import json

    store = LocalStore(os.path.join(ROOT, "data"))
    t0 = time.time()
    files = sorted(glob.glob(os.path.join(ROOT, "data/master/daily/*.parquet")))
    rng = random.Random(args.seed)
    picks = rng.sample(files, min(args.stocks, len(files)))
    daily_map = {}
    for p in picks:
        code = os.path.basename(p)[:-8]
        d = pd.read_parquet(p)
        if d is not None and not d.empty:
            daily_map[code] = d
    factors_map = load_factors_map(store)
    try:
        names_map = json.load(open(os.path.join(ROOT, "data/names.json"),
                                   encoding="utf-8"))
    except Exception:                                       # noqa: BLE001
        names_map = {}
    print(f"[load] {len(daily_map)} 只股票，耗时 {time.time()-t0:.1f}s")

    t1 = time.time()
    xs = build_cross_sections(store, args.start, args.end, names_map=names_map,
                              daily_map=daily_map, factors_map=factors_map)
    dates = xs["_dates"]
    print(f"[panel] {len(dates)} 个预测日 "
          f"{dates[0] if dates else '-'}..{dates[-1] if dates else '-'}，"
          f"耗时 {time.time()-t1:.1f}s")

    weights = json.loads(args.weights) if args.weights else \
        {"mom20": -0.5, "rev5": 0.5, "vol20": -0.2, "turn20": -0.3}
    adj_cache = xs.setdefault("_adj_cache", {})

    # ── 主循环：与 evaluate_weights 同构，但用复刻版并计数 ──
    ledger = Counter()
    ledger["details"] = []
    ledger["rawcache"] = {}
    fill_hits = []
    prev_up = None
    n_days = 0
    n_days_skip = 0
    filled_rows = []

    for day in dates:
        uni = xs.get(day)
        if uni is None or uni.empty:
            continue
        n_days += 1
        up, down = build_predictions(uni, top_n=10, bottom_n=10, weights=weights)
        if up is None or down is None or up.empty or down.empty:
            continue
        preds = pd.concat([up, down], ignore_index=True)
        # 复刻版
        bars_a = _next_bars_audit(daily_map, preds["code"].tolist(), day,
                                  ENTRY_LAG, factors_map, adj_cache, ledger)
        # 真实版（逐位比对）
        bars_r = bt._next_bars(daily_map, preds["code"].tolist(), day,
                                entry_lag=ENTRY_LAG, factors_map=factors_map,
                                adj_cache=adj_cache)
        # 比对
        if len(bars_a) != len(bars_r):
            print(f"  !! 复刻与真实行数不一致 {day}: {len(bars_a)} vs {len(bars_r)}")
        else:
            a = bars_a.sort_values("code").reset_index(drop=True)
            r = bars_r.sort_values("code").reset_index(drop=True)
            if not np.allclose(a["close"].to_numpy(float),
                               r["close"].to_numpy(float), equal_nan=True):
                print(f"  !! 复刻与真实 close 不一致 {day}")
            if not np.allclose(a["prev_close"].to_numpy(float),
                               r["prev_close"].to_numpy(float), equal_nan=True):
                print(f"  !! 复刻与真实 prev_close 不一致 {day}")
        if bars_r.empty:
            n_days_skip += 1
            continue
        fill_flag_audit(daily_map, bars_r["code"].tolist(), day, ENTRY_LAG,
                        factors_map, adj_cache, fill_hits)
        res = bt.verify_predictions(preds, bars_r)
        res["pred_date"] = day
        filled_rows.append(res)
        prev_up = list(up["code"])

    # ──────────────────────────────────────────────────────────────────────
    print()
    print("═" * 78)
    print(f"【闸门拦截计数】  预测日 = {n_days} 个，篮子 = {n_days * 20} 只·日")
    print("═" * 78)
    tot_cand = 0
    for k in sorted(ledger):
        if k in ("details", "rawcache"):
            continue
        print(f"  {k:<28} {ledger[k]:>8,}")
        if k != "FILLED":
            tot_cand += ledger[k]
    print(f"  {'— 候选总数（去重后 code·日）':<28} "
          f"{sum(v for k, v in ledger.items() if k not in ('details', 'rawcache')):>8,}")
    filled = ledger.get("FILLED", 0)
    print()
    print(f"  成交率 = {filled}/{filled + tot_cand} = "
          f"{filled / max(1, filled + tot_cand) * 100:.2f}%")

    # ── 被吞掉的停牌卖出的真实盈亏 ──
    print()
    print("═" * 78)
    print("【卖出那根停牌 = 亏损是否凭空消失】")
    print("═" * 78)
    det = ledger["details"]
    exit_susp = [d for d in det if d["why"] == "exit_suspended"]
    entry_susp = [d for d in det if d["why"] == "entry_suspended"]
    print(f"  买入 bar 停牌 → 丢弃 : {len(entry_susp):>6}  "
          f"({len(entry_susp)/max(1,len(det))*100:.2f}% of all skipped)")
    print(f"  卖出 bar 停牌 → 丢弃 : {len(exit_susp):>6}  "
          f"({len(exit_susp)/max(1,len(det))*100:.2f}% of all skipped)")

    rets = [d["true_ret"] for d in exit_susp if d.get("true_ret") is not None]
    unres = [d for d in exit_susp if d.get("true_ret") is None]
    if rets:
        r = np.array(rets, dtype=float)
        print(f"  其中能定值的真实收益 N = {len(r):,}（后复权，复牌卖出）")
        print(f"    mean   = {r.mean()*100:+.4f}%")
        print(f"    median = {np.median(r)*100:+.4f}%")
        print(f"    最差   = {r.min()*100:+.2f}%   最好 = {r.max()*100:+.2f}%")
        neg = float((r < 0).mean())
        print(f"    亏损占比 = {neg*100:.1f}%")
        # 引擎口径：这一笔在 .mean() 里直接不存在 → 相当于贡献 0
        print()
        print(f"  ► 引擎口径把这 {len(r):,} 笔当「不存在」（对 .mean() 贡献 0）")
        print(f"  ► 真实口径它们合计贡献 {r.sum()*100:+.2f} 个百分点")
        print(f"  ► 真实均值 {r.mean()*100:+.4f}%  vs  引擎隐含 0.0000%"
              f"  → 单笔偏差 {(0 - r.mean())*100:+.4f} 个百分点")
        print(f"  ► 这 {len(r):,} 笔占「成交 {filled:,} + 被丢弃 {tot_cand:,}」"
              f"的 {len(r)/max(1,filled+tot_cand)*100:.2f}%")
    if unres:
        print(f"  ⚠ 另有 {len(unres):,} 笔停牌后**再无任何可成交 bar**"
              f"（疑似退市/永久停牌）——真实亏损无法定值，引擎直接丢弃")
        print(f"     这些是**亏损无上限**的样本：引擎连它们的负号都没记。")
    hb = [d.get("hold_bars") for d in exit_susp if d.get("hold_bars")]
    if hb:
        hb = np.array(hb)
        print(f"  实际被延长持有的 bar 数: mean={hb.mean():.1f} median={np.median(hb):.0f} "
              f"max={hb.max()}")

    # ──────────────────────────────────────────────────────────────────────
    print()
    print("═" * 78)
    print("【封板复核：引擎记了成交，但按 can_buy/can_sell 本该拒单】")
    print("═" * 78)
    fh = pd.DataFrame(fill_hits)
    if fh.empty:
        print("  无样本")
    else:
        e_b = fh[(fh.leg == "entry")]
        x_s = fh[(fh.leg == "exit")]
        n = len(fh)
        e_bad = int((~e_b["buyable"].astype(bool)).sum())
        x_bad = int((~x_s["sellable"].astype(bool)).sum())
        print(f"  复核样本 N = {n:,} 根 bar")
        print(f"  买入 bar 引擎判成交、can_buy 说买不进 : {e_bad:,} "
              f"({e_bad/max(1,len(e_b))*100:.3f}% 的买入 bar)")
        print(f"  卖出 bar 引擎判成交、can_sell 说卖不掉: {x_bad:,} "
              f"({x_bad/max(1,len(x_s))*100:.3f}% 的卖出 bar)")

        # 这些「买不进却成交」的笔，方向是赚是亏？
        bad_codes = set(e_b.loc[~e_b["buyable"].astype(bool), "code"]) | \
                    set(x_s.loc[~x_s["sellable"].astype(bool), "code"])
        if filled_rows:
            allres = pd.concat(filled_rows, ignore_index=True)
            sub = allres[allres["code"].isin(bad_codes)]
            if len(sub):
                print()
                print(f"  这些不可成交 code 在结果表里共 {len(sub):,} 条判定，"
                      f"实际收益均值 {sub['actual_pct'].mean()*100:+.4f}%")
                for direction, g in sub.groupby("direction"):
                    print(f"    {direction:<5} N={len(g):>5}  "
                          f"mean={g['actual_pct'].mean()*100:+.4f}%")

    # ──────────────────────────────────────────────────────────────────────
    if filled_rows:
        allres = pd.concat(filled_rows, ignore_index=True)
        print()
        print("═" * 78)
        print("【结果表本身】")
        print("═" * 78)
        print(f"  判定条数 {len(allres):,}   "
              f"no_data = {int((allres['result']=='no_data').sum()):,}")
        for d in ("up", "down"):
            g = allres[allres.direction == d]
            print(f"  {d:<5} N={len(g):>6}  mean_actual={g['actual_pct'].mean()*100:+.4f}%"
                  f"  hit={float((g['result']=='hit').mean())*100:.1f}%")

    print()
    print(f"[done] 总耗时 {time.time()-t0:.1f}s")
    return ledger


if __name__ == "__main__":
    main()
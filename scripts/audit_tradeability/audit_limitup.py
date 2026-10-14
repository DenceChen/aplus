"""涨停/跌停判定正确性审计 + 「引擎放行但物理上不可成交」的成交到底是赚是亏。

三个问题：

A. `price_limit` 的板块映射对不对？±5%(ST) / ±10%(主板) / ±20%(创业板科创) / ±30%(北交所)
   - 逐一核对前缀覆盖：有没有漏掉的板块前缀
   - **重点：`can_buy` 的涨停判据用的是「板块限制」，完全没有 ST 的 ±5%。**
     一个 ST 股封在 +5%，`at_limit_up` 判 False（因为 band 是 +10%）→ can_buy 放行。
     只有 `sealed`（一字板）那条额外认了 ±5%。**一字 ST 能拦住，收盘 ST 涨停拦不住。**

B. 引擎在**成交窗口内**只判 volume，不判封板。实测漏掉多少？
   口径：**引擎记了成交** vs **按 can_buy/can_sell 该拒单**，逐笔对齐。

C. 那些「本该拒单却被记成成交」的笔，实际收益是多少？
   这一条决定缺口是「偏乐观」还是「偏保守」——不测就没有结论。

只读。
"""
import argparse
import glob
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
from aplus.panel import (LIMIT_BEIJING, LIMIT_GROWTH,       # noqa: E402
                          LIMIT_MAIN, LIMIT_ST, PRICE_TOL,
                          can_buy, can_sell, price_limit)


def part_a_prefixes():
    print("═" * 78)
    print("【A. 板块涨跌幅映射】")
    print("═" * 78)
    print(f"  LIMIT_MAIN={LIMIT_MAIN} LIMIT_GROWTH={LIMIT_GROWTH} "
          f"LIMIT_BEIJING={LIMIT_BEIJING} LIMIT_ST={LIMIT_ST} PRICE_TOL={PRICE_TOL}")
    cases = [
        ("600519.SH", 0.10, "沪主板"), ("601398.SH", 0.10, "沪主板"),
        ("000001.SZ", 0.10, "深主板"), ("002415.SZ", 0.10, "深主板中小板原"),
        ("001979.SZ", 0.10, "深主板新"), ("300750.SZ", 0.20, "创业板"),
        ("301029.SZ", 0.20, "创业板新"), ("688981.SH", 0.20, "科创板"),
        ("689009.SH", 0.20, "科创板CDR"), ("830799.BJ", 0.30, "北交所"),
        ("430047.BJ", 0.30, "北交所"), ("920008.BJ", 0.30, "北交所新"),
        ("873693.BJ", 0.30, "北交所"),
        # 有争议的：注册制全面推行后 主板 001/002 也是 10%，但：
        ("900901.SH", None, "沪B股 → 未知前缀，落到主板"),
        ("200011.SZ", None, "深B股 → 未知前缀"),
        ("000001.SH", None, "指数/未知"),
        ("688xxx.SH", 0.20, "科创"),
    ]
    for code, want, desc in cases:
        got = price_limit(code)
        flag = "" if want is None else ("  ✓" if abs(got - want) < 1e-9 else "  ✗ MISMATCH")
        print(f"  {code:<12} → {got:.0%}  ({desc}){flag}")

    print()
    print("  ── ST 的 ±5% 在 can_buy 上完全没被用到 ──")
    # 构造：主板 ST 股，prev=10，次日封在 +5%（ST 的真实涨停价）
    st_args = dict(open=10.50, high=10.50, low=10.50, close=10.50,
                   volume=1e5, amount=1e6, prev_close=10.0)
    print(f"  ST 股一字封在 +5.00%（{st_args['close']}）: can_buy={can_buy('600001.SH', **st_args)}")
    st_flat = dict(open=10.00, high=10.50, low=10.50, close=10.50,
                   volume=2e5, amount=3e6, prev_close=10.0)
    print(f"  ST 股开盘 10.00 盘中封 +5.00% 收盘 10.50 : "
          f"can_buy={can_buy('600001.SH', **st_flat)}   "
          f"← 收盘封在 ST 涨停价，物理上排不进队，但 can_buy 说买得进")
    st_flat_dn = dict(open=10.00, high=9.50, low=9.50, close=9.50,
                      volume=2e5, amount=3e6, prev_close=10.0)
    print(f"  ST 股收盘封在 −5.00%（跌停）: can_sell={can_sell('600001.SH', **st_flat_dn)}"
          f"   ← 卖不掉，但 can_sell 说卖得掉")

    # 真实数据里 ST 有多少、名字能不能识别
    print()
    print("  ── 该缺口实际暴露面：主库里 isST=1 的股票有多少 ──")


def part_bc_scan(daily_map, factors_map, names_map, start, end, is_st_fn):
    print()
    print("═" * 78)
    print("【B/C. 成交窗口内的封板：引擎放行了多少本该拒单的成交】")
    print("═" * 78)
    cnt = Counter()
    up_bad = []
    dn_bad = []
    st_hits = 0

    for code, df in daily_map.items():
        if df is None or len(df) < 8:
            continue
        s = df.sort_values("date", kind="stable")
        dates = s["date"].to_numpy(dtype="U10")
        keep = (dates >= start) & (dates < end)
        if not keep.any():
            continue
        vol = pd.to_numeric(s["volume"], errors="coerce").to_numpy(float)
        amt = (pd.to_numeric(s["amount"], errors="coerce").to_numpy(float)
               if "amount" in s.columns else np.full(len(s), np.nan))
        cl = pd.to_numeric(s["close"], errors="coerce").to_numpy(float)
        op = (pd.to_numeric(s["open"], errors="coerce").to_numpy(float)
              if "open" in s.columns else np.full(len(s), np.nan))
        hi = (pd.to_numeric(s["high"], errors="coerce").to_numpy(float)
              if "high" in s.columns else np.full(len(s), np.nan))
        lo = (pd.to_numeric(s["low"], errors="coerce").to_numpy(float)
              if "low" in s.columns else np.full(len(s), np.nan))
        prev = pd.Series(cl).shift(1).to_numpy()
        events = (factors_map or {}).get(code)
        has_adj = events is not None and len(events) > 0
        closes = (hfq_close(s, events).to_numpy(float) if has_adj else cl)
        n = len(dates)
        name = names_map.get(code, code)
        st_flag = bool(is_st_fn(name))
        if st_flag:
            st_hits += 1

        def tradable(i):
            if i < 0 or i >= n:
                return False
            if not np.isfinite(vol[i]) or vol[i] <= 0:
                return False
            a = amt[i]
            if np.isfinite(a) and a <= 0:
                return False
            return True

        for i in range(1, n - 1):
            if dates[i] < start or dates[i] >= end:
                continue
            entry, exit_i = i + 1, i + 2
            if not tradable(entry) or not tradable(exit_i):
                continue                       # 停牌闸门已处理，不重复计
            ep, xp = float(closes[entry]), float(closes[exit_i])
            if ep <= 0 or xp <= 0:
                continue
            cnt["filled"] += 1
            ret = xp / ep - 1.0

            ea = dict(open=op[entry], high=hi[entry], low=lo[entry], close=cl[entry],
                      volume=vol[entry], amount=amt[entry], prev_close=prev[entry])
            xa = dict(open=op[exit_i], high=hi[exit_i], low=lo[exit_i],
                      close=cl[exit_i], volume=vol[exit_i], amount=amt[exit_i],
                      prev_close=prev[exit_i])
            try:
                eb = bool(can_buy(code, **ea))
                xs_ = bool(can_sell(code, **xa))
            except Exception:                  # noqa: BLE001
                continue
            if not eb:
                cnt["entry_unfillable"] += 1
                up_bad.append({"code": code, "D": dates[i], "ret": ret,
                               "st": st_flag})
            if not xs_:
                cnt["exit_unfillable"] += 1
                dn_bad.append({"code": code, "D": dates[i], "ret": ret,
                               "st": st_flag})
    return cnt, up_bad, dn_bad, st_hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stocks", type=int, default=1200)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--start", default="2021-01-01")
    ap.add_argument("--end", default="2026-01-01")
    args = ap.parse_args()

    import json
    from aplus.store import LocalStore
    from aplus.snapshot import load_factors_map
    from aplus.predict import is_st_name

    part_a_prefixes()

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
    print(f"\n[scan] {len(daily_map)} 只股票 × 信号日 [{args.start}, {args.end})")
    print(f"  其中名称判为 ST 的 : "
          f"{sum(1 for c in daily_map if is_st_name(names_map.get(c, c)))}")

    cnt, up_bad, dn_bad, st_hits = part_bc_scan(
        daily_map, factors_map, names_map, args.start, args.end, is_st_name)

    F = cnt.get("filled", 0)
    print()
    print(f"  引擎记为成交的窗口           = {F:,}")
    print(f"  其中买入 bar 本该买不进      = {cnt['entry_unfillable']:,} "
          f"({cnt['entry_unfillable']/max(1,F)*100:.3f}%)")
    print(f"  其中卖出 bar 本该卖不掉      = {cnt['exit_unfillable']:,} "
          f"({cnt['exit_unfillable']/max(1,F)*100:.3f}%)")

    def summarize(name, rows):
        if not rows:
            print(f"\n  ── {name}：无样本")
            return
        r = np.array([x["ret"] for x in rows], dtype=float)
        print(f"\n  ── {name}（这些笔引擎当成了真实成交）──")
        print(f"      N     = {len(r):,}")
        print(f"      mean  = {r.mean()*100:+.4f}%")
        print(f"      median= {np.median(r)*100:+.4f}%")
        print(f"      min/max = {r.min()*100:+.2f}% / {r.max()*100:+.2f}%")
        print(f"      上涨占比 = {(r>0).mean()*100:.1f}%")
        print(f"      其中 ST 股 = {sum(1 for x in rows if x['st'])}")
        top = pd.Series([x["code"] for x in rows]).value_counts().head(5)
        print(f"      最多的 code: {dict(top)}")

    summarize("买入 bar 封板却成交（涨停/一字板买不进）", up_bad)
    summarize("卖出 bar 封板却成交（跌停卖不掉）", dn_bad)

    # 若把这些样本剔除，池子平均收益变多少
    allr = []
    print()
    print("═" * 78)
    print("【D. 影响量级】")
    print("═" * 78)
    print("  （以全窗口等权口径估计：剔除这些样本后池子均值的移动）")
    print("  注意这是**上限估计**——真实篮子只取 top10/bottom10，不是全池。")


if __name__ == "__main__":
    main()
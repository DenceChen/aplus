"""定向普查：**卖出那根停牌**这个失效路径在全库到底覆盖多大面积、吞掉多少亏损。

为什么必须定向普查而不是只靠自然抽样：
自然抽样里 `filter_universe` 的 `volume > 0`（predict.py:106，已经把「信号日 D 当天
停牌」的票剔掉了）会把绝大多数停牌样本在**进篮子之前**就滤掉，于是篮子层面
观测率≈0，看起来像「没问题」。但那是筛选器的功劳，不是引擎闸门的功劳。
本脚本绕开选股，直接对**全库所有「D → D+1 买 → D+2 卖」窗口**做穷举，
量的是这条失效路径的**暴露面**（population exposure）。

只读：pd.read_parquet only。
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
from aplus.panel import MAX_PLAUSIBLE_RETURN             # noqa: E402


def scan(daily_map, factors_map, start, end):
    """穷举所有信号日 D 的成交窗口。返回 Counter + 明细列表。"""
    cnt = Counter()
    detail = []
    for code, df in daily_map.items():
        if df is None or len(df) < 5:
            continue
        s = df.sort_values("date", kind="stable")
        dates = s["date"].to_numpy(dtype="U10")
        keep = (dates >= start) & (dates < end)
        if not keep.any():
            continue
        vol = pd.to_numeric(s["volume"], errors="coerce").to_numpy(float)
        amt = pd.to_numeric(s["amount"], errors="coerce").to_numpy(float) \
            if "amount" in s.columns else np.full(len(s), np.nan)
        events = (factors_map or {}).get(code)
        has_adj = events is not None and len(events) > 0
        closes = (hfq_close(s, events).to_numpy(float) if has_adj
                  else s["close"].astype(float).to_numpy(float))
        n = len(dates)

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
            D = dates[i]
            entry, exit_i = i + 1, i + 2          # ENTRY_LAG=1: D+1 买, D+2 卖
            cnt["windows"] += 1
            if not tradable(entry):
                cnt["engine_skip_entry_susp"] += 1
                continue
            if not tradable(exit_i):
                cnt["engine_skip_exit_susp"] += 1
                # 真实路径：D+1 收盘买进，D+2 停牌卖不掉 → 拿到复牌才能出
                j = i + 3
                while j < n and not tradable(j):
                    j += 1
                rec = {"code": code, "D": D, "entry_date": dates[entry]}
                if j >= n:
                    rec["true_ret"] = None       # 再无 bar：退市/永久停牌
                    rec["resume_date"] = None
                    rec["hold_bars"] = None
                else:
                    ep, xp = float(closes[entry]), float(closes[j])
                    rec["resume_date"] = dates[j]
                    rec["hold_bars"] = int(j - entry)
                    rec["true_ret"] = (xp / ep - 1.0) if ep > 0 and xp > 0 else None
                detail.append(rec)
                continue
            ep, xp = float(closes[entry]), float(closes[exit_i])
            if ep <= 0 or xp <= 0:
                cnt["engine_skip_nonpositive"] += 1
                continue
            if abs(xp / ep - 1.0) > MAX_PLAUSIBLE_RETURN:
                cnt["engine_skip_implausible"] += 1
                continue
            cnt["engine_FILLED"] += 1
    return cnt, detail


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stocks", type=int, default=1500)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--start", default="2019-01-01")
    ap.add_argument("--end", default="2026-01-01")
    args = ap.parse_args()

    from aplus.store import LocalStore
    from aplus.snapshot import load_factors_map

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
    factors_map = load_factors_map(store)
    codes_wanted = set(daily_map)
    factors_map = {k: v for k, v in factors_map.items() if k in codes_wanted}
    print(f"[scan] {len(daily_map)} 只股票 × 信号日 [{args.start}, {args.end})")

    cnt, detail = scan(daily_map, factors_map, args.start, args.end)

    W = cnt["windows"]
    F = cnt["engine_FILLED"]
    print()
    print("═" * 78)
    print("【全库穷举：引擎对每一个成交窗口做了什么】")
    print("═" * 78)
    for k in ("windows", "engine_FILLED", "engine_skip_entry_susp",
              "engine_skip_exit_susp", "engine_skip_nonpositive",
              "engine_skip_implausible"):
        v = cnt.get(k, 0)
        print(f"  {k:<26} {v:>12,}  ({v/max(1,W)*100:7.4f}%)")
    print()
    print(f"  卖出 bar 停牌被丢弃 = {cnt['engine_skip_exit_susp']:,} / {W:,} "
          f"= {cnt['engine_skip_exit_susp']/max(1,W)*100:.4f}% 的成交窗口")
    print(f"  买入 bar 停牌被丢弃 = {cnt['engine_skip_entry_susp']:,} / {W:,} "
          f"= {cnt['engine_skip_entry_susp']/max(1,W)*100:.4f}%")

    print()
    print("═" * 78)
    print("【被丢弃的那些笔，真实世界里值多少】")
    print("═" * 78)
    rets = np.array([d["true_ret"] for d in detail
                     if d["true_ret"] is not None], dtype=float)
    unres = [d for d in detail if d["true_ret"] is None]
    print(f"  停牌卖出总样本 = {len(detail):,}")
    print(f"  其中能定值（复牌后有 bar）= {len(rets):,}")
    print(f"  其中**永远无法卖出**（退市/永久停牌）= {len(unres):,} "
          f"({len(unres)/max(1,len(detail))*100:.1f}%)  ← 亏损无上限，引擎整条丢弃")
    if len(rets):
        print()
        print(f"  真实收益（后复权，买入价=D+1 收盘，卖价=复牌首根可成交 bar 收盘）")
        print(f"    N     = {len(rets):,}")
        print(f"    mean  = {rets.mean()*100:+.4f}%")
        print(f"    median= {np.median(rets)*100:+.4f}%")
        print(f"    min   = {rets.min()*100:+.2f}%    max = {rets.max()*100:+.2f}%")
        print(f"    亏损占比 = {(rets<0).mean()*100:.1f}%")
        print(f"    |ret|>10% 占比 = {(np.abs(rets)>0.10).mean()*100:.1f}%")
        print()
        print("  ► 引擎口径：这些笔在 verify.merge(how='left') 后 actual_pct=NaN，")
        print("    rolling_stats 的 .mean() 静默跳过 → 相当于贡献 0.0000%")
        print(f"  ► 真实均值 {rets.mean()*100:+.4f}%   vs   引擎隐含 0.0000%")
        print(f"  ► 单笔方向性偏差 = {(0 - rets.mean())*100:+.4f} 个百分点")
        # 对整个成交窗口池子的影响
        share = len(rets) / max(1, W)
        print()
        print(f"  ► 这些笔占全部成交窗口 {share*100:.4f}%")
        print(f"  ► 若按等权篮子口径，把它们从池子里整体剔掉，"
              f"对池子平均收益的直接影响 ≈ {share * (0 - rets.mean()) * 100:+.5f} 个百分点/笔")
        print(f"    （= share × (0 − mean) ；符号为正 = 引擎口径偏乐观）")

    hb = np.array([d["hold_bars"] for d in detail if d["hold_bars"]], dtype=float)
    if len(hb):
        print()
        print(f"  被迫延长的持有 bar 数: mean={hb.mean():.1f} median={np.median(hb):.0f} "
              f"p90={np.percentile(hb,90):.0f} max={hb.max():.0f}")
        print("  （引擎口径固定 1 根 bar；真实最长被拖 "
              f"{int(hb.max())} 根 ≈ {int(hb.max()/21)} 个月）")

    # 按 code 聚类，看是不是集中在少数问题股上
    d = pd.DataFrame(detail)
    if len(d):
        print()
        top = d.groupby("code").size().sort_values(ascending=False).head(8)
        print("  停牌卖出事件最多的 code（是否存在「问题股反复进篮子」）：")
        for c, k in top.items():
            rr = [x["true_ret"] for x in detail
                  if x["code"] == c and x["true_ret"] is not None]
            mm = f"{np.mean(rr)*100:+.2f}%" if rr else "n/a"
            print(f"    {c:<14} {k:>4} 次   其中可定值笔的真实均值 {mm}")


if __name__ == "__main__":
    main()
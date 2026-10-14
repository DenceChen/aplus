"""停牌的两种形态，闸门只看得见其中一种。

形态 1（有行，volume==0）：备源（腾讯）写出了这一天的行情行，只是零成交。
        → `_bars_tradable` 的 volume 闸门**能**拦住。

形态 2（无行，日期直接跳过去）：主源（baostock）停牌日根本不输出行情行
        （README.md:109 自己写了「baostock 停牌日不输出行情行」）。
        → parquet 里根本没有这一行，`_bars_tradable` 的 index 落在**下一根**
           真实 bar 上，闸门完全看不见，收益窗口被静默拉长。

对形态 2，`_next_bars` 的 `entry = pos+entry_lag-1` 落在「该股的第一根真实 bar」，
它未必是市场日历上的 D+1。若停牌持续 N 天，D+1 和 D+2 都落在停牌期内，
引擎会把 D+1 记成复牌日、D+2 记成复牌次日 —— **收益窗口长度错位**，
且这笔成交被当成「正常成交」记进篮子。

本脚本量两种形态各占多少，以及形态 2 对收益窗口的扭曲程度。

只读。
"""
import glob
import os
import random
import sys

import numpy as np
import pandas as pd

# ★ 从 __file__ 推导，不再硬编码绝对路径：硬编码带用户名且换机即失效。
#   三次 dirname：scripts/audit_tradeability/x.py → 仓库根
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

N = 800
rng = random.Random(11)
files = sorted(glob.glob(os.path.join(ROOT, "data/master/daily/*.parquet")))
picks = rng.sample(files, min(N, len(files)))

# 市场日历 = 取行数最多的那只股票
ref = pd.read_parquet(os.path.join(ROOT, "data/master/daily/600519.SH.parquet"),
                      columns=["date"])
calendar = np.sort(ref["date"].astype(str).unique())
pos_of = {d: i for i, d in enumerate(calendar)}

rows = []
for p in picks:
    code = os.path.basename(p)[:-8]
    df = pd.read_parquet(p, columns=["date", "volume", "close"])
    if df is None or df.empty:
        continue
    d = np.sort(df["date"].astype(str).unique())
    # 只在「该股已上市区间」内比，避免把上市前的空档算成停牌
    idx = np.array([pos_of.get(x, -1) for x in d])
    idx = idx[idx >= 0]
    if len(idx) < 30:
        continue
    lo, hi = idx.min(), idx.max()
    span = hi - lo + 1
    present = np.zeros(span, dtype=bool)
    present[idx - lo] = True
    n_missing = int((~present).sum())
    vol = pd.to_numeric(df["volume"], errors="coerce").to_numpy(float)
    # volume==0 的行数（形态 1）
    n_zero = int((vol <= 0).sum())
    rows.append(dict(code=code, span=int(span), present=int(len(idx)),
                     missing=n_missing, zero_vol=n_zero,
                     miss_frac=n_missing / span))
    # 形态 2 的 run 结构
    m = ~present
    if m.any():
        grp = (m != np.r_[False, m[:-1]]).cumsum()
        for g in np.unique(grp[m]):
            rows[-1].setdefault("gap_runs", []).append(int((grp == g).sum()))

print(f"样本 = {len(rows)} 只（种子 11，从 {len(files)} 只抽）")
print(f"市场日历长度 = {len(calendar)} 个交易日 "
      f"{calendar[0]}..{calendar[-1]}")

tm = sum(r["span"] for r in rows)
tmi = sum(r["missing"] for r in rows)
tzv = sum(r["zero_vol"] for r in rows)
print()
print("═" * 78)
print("【停牌的两种形态】")
print("═" * 78)
print(f"  上市区间内的交易日·股票数 = {tm:,}")
print(f"  形态1  有行但 volume==0    = {tzv:,}  ({tzv/max(1,tm)*100:.4f}%)   "
      f"← 闸门看得见")
print(f"  形态2  日期缺失（整行没有）  = {tmi:,}  ({tmi/max(1,tm)*100:.4f}%)   "
      f"← 闸门看不见")

gr = [x for r in rows for x in r.get("gap_runs", [])]
if gr:
    gr = np.array(gr)
    print()
    print(f"  形态 2 的连续缺行 run：n={len(gr)}  mean={gr.mean():.1f}  "
          f"median={np.median(gr):.0f}  max={gr.max()}  sum={gr.sum():,}")
    for k in (1, 2, 3, 5, 10):
        print(f"    run >= {k:>2} 天 : {(gr >= k).sum():>6} "
              f"({(gr >= k).mean()*100:.1f}%)")
    # 关键：形态2 里 run>=2 的，意味着 D+1 和 D+2 **同时**落在停牌期内
    # → 引擎的「1 根 bar 持有窗口」被静默拉长
    long_runs = gr[gr >= 2]
    print()
    print(f"  ► run >= 2 天的缺行 = {len(long_runs):,} 个 run，"
          f"合计 {int(long_runs.sum()):,} 天")
    print("    这些区间里，「D+1 买 / D+2 卖」的两天**都落在停牌期内**：")
    print("    引擎记下的其实是「复牌日买 / 复牌次日卖」，持有窗口长度错位，")
    print("    而闸门一条都没拦住（volume>0，因为那两根是真 bar）。")
else:
    print("  未观测到形态 2")

st = sum(1 for r in rows if r["missing"] > 0)
print()
print(f"  含形态 2 缺行的股票 = {st}/{len(rows)}")
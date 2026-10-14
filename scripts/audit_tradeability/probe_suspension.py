"""只读探针：停牌日在 parquet 里到底有没有行？

这决定 backtest._bars_tradable 的 volume==0 闸门**是否真的会触发**。
- 若停牌日有行且 volume==0 → `_bars_tradable` 的停牌分支是活的
- 若停牌日整行不存在 → pos/exit_i 直接跨过停牌期，闸门形同虚设，
  收益窗口被静默拉长到复牌日（`close` 比的是 D+1 与复牌日收盘）

只读：只 pd.read_parquet，不写任何东西。
"""
import glob
import os
import random
import sys

import numpy as np
import pandas as pd

ROOT = "/Users/airboom/opencode/aplus"
sys.path.insert(0, ROOT)

SAMPLE_N = 800
SEED = 42

files = sorted(glob.glob(os.path.join(ROOT, "data/master/daily/*.parquet")))
rng = random.Random(SEED)
sample = rng.sample(files, min(SAMPLE_N, len(files)))

rows = []
for p in sample:
    code = os.path.basename(p)[:-8]
    try:
        df = pd.read_parquet(p, columns=["date", "close", "volume", "amount",
                                         "open", "high", "low", "pctChg"])
    except Exception as e:                                   # noqa: BLE001
        rows.append(dict(code=code, err=str(e)[:60]))
        continue
    if df is None or df.empty:
        continue
    vol = pd.to_numeric(df["volume"], errors="coerce")
    amt = pd.to_numeric(df["amount"], errors="coerce")
    amt_known = amt.notna()
    zero_vol = (vol <= 0)
    zero_amt_known = amt_known & (amt <= 0)
    susp = zero_vol | zero_amt_known
    # 停牌日：源站通常把 close 填成上一日收盘（面板注释 panel.py:376 说的就是它）
    close = pd.to_numeric(df["close"], errors="coerce")
    same_as_prev = close.shift(1)
    frozen = (close == same_as_prev) & susp
    pct = pd.to_numeric(df["pctChg"], errors="coerce")
    rows.append(dict(
        code=code, n=len(df),
        first=str(df["date"].iloc[0]), last=str(df["date"].iloc[-1]),
        vol_zero=int(zero_vol.sum()),
        amt_zero_known=int(zero_amt_known.sum()),
        susp_bars=int(susp.sum()),
        susp_and_close_eq_prev=int(frozen.sum()),
        # 停牌之后那根复牌 bar 的真实涨跌幅——「跳过停牌」到底吞掉了多少
        resume_pctchg=[float(x) for x in pct[susp.shift(1, fill_value=False)].dropna()],
        vol_na=int(vol.isna().sum()),
        amt_col_missing="amount" not in df.columns,
    ))

ok = [r for r in rows if "err" not in r]
errs = [r for r in rows if "err" in r]
print(f"样本 = {len(sample)} 只（随机种子 {SEED}，从 {len(files)} 只中抽）")
print(f"读取成功 {len(ok)}，失败 {len(errs)}")
if errs:
    print("  失败样例:", errs[:3])

tot_susp = sum(r["susp_bars"] for r in ok)
stocks_with_susp = sum(1 for r in ok if r["susp_bars"] > 0)
tot_bars = sum(r["n"] for r in ok)
print()
print("── 停牌 bar 密度 ──")
print(f"全样本总 K 线数        : {tot_bars:,}")
print(f"停牌 bar (vol<=0 或 已知的 amt<=0): {tot_susp:,}  ({tot_susp/tot_bars*100:.4f}%)")
print(f"有停牌 bar 的股票      : {stocks_with_susp}/{len(ok)}")
print(f"vol 为 NaN 的 bar 数   : {sum(r['vol_na'] for r in ok):,}")

# 复牌跳空：若闸门触发，被 continue 掉的就是这一笔
res = [x for r in ok for x in r["resume_pctchg"]]
res = np.array(res, dtype=float)
res = res[np.isfinite(res)]
print()
print("── 复牌首日涨跌幅（source pctChg，不复权）──")
print(f"N = {len(res):,}")
if len(res):
    print(f"mean   = {res.mean()*100:+.4f}%")
    print(f"median = {np.median(res)*100:+.4f}%")
    print(f"|mean| = {np.abs(res).mean()*100:.4f}%")
    for thr in (0.05, 0.10, 0.20, 0.40):
        print(f"  |x| > {thr*100:>5.1f}% : {(np.abs(res) > thr).sum():>6} "
              f"({(np.abs(res) > thr).mean()*100:.2f}%)")

# 停牌持续多长的分布（连续停牌 run-length）
runs = []
for p in sample[:200]:
    try:
        df = pd.read_parquet(p, columns=["volume"])
    except Exception:                                        # noqa: BLE001
        continue
    if df is None or df.empty:
        continue
    susp = (pd.to_numeric(df["volume"], errors="coerce") <= 0)
    if not susp.any():
        continue
    grp = (susp != susp.shift()).cumsum()
    for _, g in susp[susp].groupby(grp[susp]):
        runs.append(len(g))
runs = np.array(runs)
print()
print("── 连续停牌 run 长度（200 只子样本）──")
if len(runs):
    print(f"run 数 = {len(runs)}, mean={runs.mean():.2f}, median={np.median(runs):.0f}, max={runs.max()}")
    for k in (1, 2, 3, 5, 10, 20):
        print(f"  run >= {k:>2} 根 : {(runs >= k).sum():>5} ({(runs >= k).mean()*100:.1f}%)")
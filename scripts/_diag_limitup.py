"""量化涨停不可买入对「涨篮子」的污染（一次性诊断，非平台代码）。

## 第一版错在哪（留档，避免重犯）

1. 请求了 `tradestatus` 列 → 648 个降级 7 列文件全部读失败并被 `continue` 丢弃，
   静默丢掉 11.9% 的股票。这是**我自己引入的抽样偏差**，而脚本却照常打印结论。
2. 结尾写死了「一字板入场收益显著更高」——实测是**更低**（−0.31%）。
   写死的结论文案比没有结论更危险：它会让读者跳过数字。
3. 末两行 `(a * len(a)).sum()/n` 是个无意义的公式，结果 −294.78 纯属荒谬，
   同样因为「有输出」而没被当场质疑。

**教训：诊断脚本的结论必须由数字生成，不能手写。**

## 这一版只测一件事

按真实涨篮子（mom20 排序 top-10）统计：入场日 D+1 是一字涨停的比例，
以及「剔除不可买入的入场」后涨篮子的次日收益变化多少。
"""
import glob
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, "/Users/airboom/opencode/aplus")

ROOT = "/tmp/aplus_snap_1610/master/daily"
START, END = "2026-04-09", "2026-10-09"
TOPN = 10

frames, bad = [], 0
for f in sorted(glob.glob(f"{ROOT}/*.parquet")):
    try:
        # 只取 OHLC —— 不依赖降级文件缺失的列，任何文件都能读进来
        d = pd.read_parquet(f, columns=["date", "code", "open", "high", "low", "close"])
    except Exception:
        bad += 1
        continue
    d["date"] = d["date"].astype(str)
    frames.append(d)

df = pd.concat(frames, ignore_index=True)
df = df[(df["date"] >= START) & (df["date"] <= "2026-10-12")].sort_values(["code", "date"])
print(f"读失败 {bad}；纳入 (股,日) = {len(df)}；股票 = {df['code'].nunique()}")
assert df["code"].nunique() > 4000, "分组键异常——先修数据读取再谈结论"
print()

# 逐股算 mom20 与次日收益
g = df.groupby("code", sort=False)
df["mom20"] = g["close"].transform(lambda s: s / s.shift(20) - 1.0)
df["ret_next"] = g["close"].transform(lambda s: s.shift(-1) / s - 1.0)
df["one_word"] = (df["open"] == df["high"]) & (df["high"] == df["low"]) & (df["low"] == df["close"])

w = df[(df["date"] >= START) & (df["date"] <= END)].dropna(subset=["mom20", "ret_next", "one_word"])
print(f"可排名 (股,日) = {len(w)}，交易日 = {w['date'].nunique()}")
print()

# 每日按 mom20 取 top-10 作为涨篮子，映射到它的入场日 (=该日+1 个交易日的行)
picks = w[w.groupby("date")["mom20"].rank(ascending=False, method="first") <= TOPN]
print(f"涨篮子样本 = {len(picks)} 个（{picks['date'].nunique()} 天 × {TOPN} 只）")
print()

n_lu = int(picks["one_word"].sum())
r_all = picks["ret_next"]
r_ok = picks.loc[~picks["one_word"], "ret_next"]

print("── 信号日 D 是一字板（即 D 收盘已涨停）──")
print(f"  比例 {n_lu/len(picks):.3%}  ({n_lu}/{len(picks)})")
print(f"  其次日收益均值 = {picks.loc[picks['one_word'],'ret_next'].mean():+.4%}"
      if n_lu else "  无样本")
print(f"  其余次日收益均值 = {picks.loc[~picks['one_word'],'ret_next'].mean():+.4%}")
print()

print("── 结论所需的对照：全市场同窗口 ──")
print(f"  全市场一字板比例 = {w['one_word'].mean():.3%}")
print(f"  全市场次日收益   = {w['ret_next'].mean():+.4%}")
print()

print("── 涨篮子次日收益 ──")
print(f"  全部        n={len(r_all):5d}  均值={r_all.mean():+.4%}  中位={r_all.median():+.4%}")
print(f"  剔除一字板  n={len(r_ok):5d}  均值={r_ok.mean():+.4%}  中位={r_ok.median():+.4%}")
delta = r_all.mean() - r_ok.mean()
print(f"  剔除造成的收益差 = {delta:+.4%}  （每笔 {TOPN} 只组合的日均影响）")
print()
print(f"  涨篮子 vs 全市场 超额（全部）    = {r_all.mean()-w['ret_next'].mean():+.4%}")
print(f"  涨篮子 vs 全市场 超额（剔一字板）= {r_ok.mean()-w.loc[~w['one_word'],'ret_next'].mean():+.4%}")
print()
print("结论由上面的数字直接读出，不预设方向。")

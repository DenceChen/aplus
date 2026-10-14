"""书 3.5 节的真正动量因子 vs 平台的 mom20 —— 上线前的先验测量。

## 为什么要单独测

`docs/factor_spec_cn.md:389` 记着一条**符号相反**的差异：

| | 书 3.5 节 | 平台 `mom20` |
|---|---|---|
| 排序窗口 | **11 个月**（t−12 → t−1 的总收益） | 20 个交易日（≈1 个月） |
| 是否剔除 t 月 | **明确剔除**，排除短期反转干扰 | 未剔除（`shift(20)` 含最近 20 日） |

书里 11 个月口径在 A 股**测不出动量**（t=−0.14），
而 20 日口径落在短期反转区间——**两者符号相反**。

2026-10-11 的探针赛马里，`{mom20:-1.0}` 测试超额 +0.039%（t=−0.01）、
`{mom20:+2.0}` +0.048%（t=0.18），两者都约等于零。
**那不是「动量无效」的结论，而是「我们从来没测过书里的动量」的结论。**

## 口径

`mom_12_1 = adj_close.shift(21) / adj_close.shift(252) - 1`
即 t−12 月末 → t−1 月末的收益，**不含最近一个月**。
用后复权价（`aplus/adjust.py`），与平台全链路口径一致。

## 只测量，不改平台代码

本脚本是决策依据，不是实现。若它证明 12-1 明显优于 mom20，
再正式落进 `aplus/panel.py`。**先看结果再决定值不值得实现，
本身就是一种「看结果再定参数」的风险**——所以这里只比较**书里已经写死的口径**，
不含任何待调的参数（没有窗口长度、没有缓冲区倍数）。
"""
import glob
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, "/Users/airboom/opencode/aplus")

ROOT = "/tmp/aplus_snap_1610/master/daily"
START, END = "2026-04-09", "2026-10-09"
TOPN = 10
MIN_HISTORY = 252   # 至少要有 252 根 K 线才算得出 12-1


def load():
    frames = []
    for f in sorted(glob.glob(f"{ROOT}/*.parquet")):
        try:
            d = pd.read_parquet(f, columns=["date", "code", "open", "high", "low", "close"])
        except Exception:
            continue
        d["date"] = d["date"].astype(str)
        frames.append(d)
    return pd.concat(frames, ignore_index=True).sort_values(["code", "date"])


def one_word(d):
    return (d["open"] == d["high"]) & (d["high"] == d["low"]) & (d["low"] == d["close"])


def evaluate(df, factor, label):
    """与探针赛马同口径：按因子排序取 top-10，统计次日收益与市场超额。"""
    w = df.dropna(subset=[factor, "ret_next"]).copy()
    w = w[~w["one_word"]]                      # 剔除买不进的一字板（实测污染 52% 超额）
    # 市场收益先算成列，再做 groupby——不要在 apply 里回头引用已被排除的 date 列
    mkt = w.groupby("date")["ret_next"].transform("mean")
    w = w.assign(_mkt=mkt)
    picks = w[w.groupby("date")[factor].rank(ascending=False, method="first") <= TOPN]
    if not len(picks):
        print(f"{label}: 无样本")
        return None
    by_day = picks.assign(_exc=picks["ret_next"] - picks["_mkt"]) \
                    .groupby("date")["_exc"].mean()
    sd = by_day.std()
    t = float(by_day.mean() / (sd / np.sqrt(len(by_day)))) if len(by_day) > 1 and sd and sd > 0 else float("nan")
    scored = w[w[factor].notna()]
    return {
        "factor": label, "n": len(picks), "days": len(by_day),
        "pool": int(scored["code"].nunique()),
        "up": float(picks["ret_next"].mean()),
        "excess": float(by_day.mean()),
        "t": t,
    }


def main():
    df = load()
    g = df.groupby("code", sort=False)
    df["ret_next"] = g["close"].transform(lambda s: s.shift(-1) / s - 1.0)
    df["mom20"] = g["close"].transform(lambda s: s / s.shift(20) - 1.0)
    # 书 3.5 节：t−12 月末 → t−1 月末，不含最近一个月
    df["mom_12_1"] = g["close"].transform(lambda s: s.shift(21) / s.shift(252) - 1.0)
    df["one_word"] = one_word(df)
    df = df[(df["date"] >= START) & (df["date"] <= END)]
    df = df[g["close"].count().reindex(df["code"]).values >= MIN_HISTORY]
    print(f"窗口 {START} ~ {END}；有 ≥{MIN_HISTORY} 根 K 线的股票 {df['code'].nunique()} 只")
    print()

    rows = []
    for f, lab in [("mom20", "mom20（平台现状 20 日）"),
                   ("mom_12_1", "mom_12_1（书 3.5 节 11-1 月）"),
                   ("mom_12_1", "mom_12_1 取反（= 11-1 反转）")]:
        d = df.copy()
        if "取反" in lab:
            d[f] = -d[f]
            lab = "mom_12_1 取反（= 11-1 反转）"
        res = evaluate(d, f, lab)
        if res:
            rows.append(res)

    print(f"{'因子':<28}{'可排名股数':>10}{'日数':>6}{'涨腿日均':>10}{'日均超额':>10}{'t':>8}")
    print("─" * 72)
    for r in rows:
        print(f"{r['factor']:<28}{r['pool']:>10}{r['days']:>6}"
              f"{r['up']*100:>9.3f}%{r['excess']*100:>9.3f}%{r['t']:>8.2f}")
    print()
    print("已剔除一字板信号日。t 检验在 ~100 个交易日上功效很低，")
    print("这里只用来排除「明显无效」，不足以据此晋级任何权重。")


if __name__ == "__main__":
    main()

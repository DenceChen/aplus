"""日频 vs 周频 vs 月频 —— 同一套因子，只改调仓频率。

## 为什么这个实验优先级最高

已知（`docs/FINDINGS_2026-10-11.md` 第 3 节）：5 万本金、10 只持仓、
单只 5000 元时，**5 元最低佣金**是成本主导项，一次往返 12.60 元 = 0.2520%。
实测日均换手 24.83%（持仓日日重算的换手），于是

| 调仓频率 | 年成本 |
|---|---|
| 每日 | 15.3% ~ 18.2% |
| 每月 | 0.30% ~ 0.35% |

而平台至今测到的**每一个**因子效应都在 ±1% 以内。
换句话说：只要坚持日频，一个方向完全正确、效应量翻倍的因子也填不平成本缺口。

书 3.1.4 的口径是**每月末调仓 + 10 档分组**，从来不是日频 top-10。
本脚本检验的是：把平台唯一的「结构假设」改回书的口径，结论会不会变。

## 口径（全部无未来信息）

- 信号日 t0：用 **截至 t0 收盘**的 mom20 排序取 top-10
- 持有 K 个交易日：t0+1 ~ t0+K 的收益，只有 t0 那天发生换手
- 成本：调仓日按换手率全额计，非调仓日 0
- 年化：`(1 + 期净收益) ** (244 / K) − 1`

## 为什么不直接改平台代码

`aplus/panel.py` / `backtest.py` 正被另一个 agent 修改中。
本脚本只读冻结快照、不碰任何平台文件，结论出来再决定值不值得接线。
"""
import glob
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, "/Users/airboom/opencode/aplus")

ROOT = "/tmp/aplus_snap_1610/master/daily"
START, END = "2026-04-09", "2026-10-09"
TOPN = 10

CAPITAL = 50_000.0
MIN_COMMISSION = 5.0
RATE = 0.00015
STAMP = 0.0005
TRANSFER = 0.00001
TRADING_DAYS = 244


def roundtrip_cost(pos: float) -> float:
    """单只持仓一次买卖往返的总成本（元）。"""
    buy = max(MIN_COMMISSION, pos * RATE)
    sell = max(MIN_COMMISSION, pos * RATE) + pos * STAMP + pos * TRANSFER * 2
    return buy + sell


def basket_cost(n_names: int) -> float:
    pos = CAPITAL / max(n_names, 1)
    return n_names * roundtrip_cost(pos) / CAPITAL   # 占本金比例


def load():
    frames = []
    for f in sorted(glob.glob(f"{ROOT}/*.parquet")):
        try:
            d = pd.read_parquet(f, columns=["date", "code", "open", "high", "low", "close"])
        except Exception:
            continue
        d["date"] = d["date"].astype(str)
        frames.append(d)
    df = pd.concat(frames, ignore_index=True).sort_values(["code", "date"])
    g = df.groupby("code", sort=False)
    df["fwd"] = g["close"].transform(lambda s: s.shift(-1) / s - 1.0)
    df["mom20"] = g["close"].transform(lambda s: s / s.shift(20) - 1.0)
    df["one_word"] = (df["open"] == df["high"]) & (df["high"] == df["low"]) & (df["low"] == df["close"])
    return df[(df["date"] >= START) & (df["date"] <= END)]


def run(df, K, label, use_buffer):
    dates = sorted(df["date"].unique())
    rebal_dates = dates[::K]
    # 透视成 date × code 的收益矩阵：持仓票在某些日子没有行（停牌/缺数据）
    # 会让 `.loc` 抛 KeyError，而矩阵里那些格子天然是 NaN，可被正确剔除。
    fwd = df.pivot_table(index="date", columns="code", values="fwd", aggfunc="first")
    held = None
    period_gross, period_net, n_names_used, turnovers = [], [], [], []
    cost = basket_cost(TOPN)

    for t0 in rebal_dates:
        idx = dates.index(t0)
        if idx + K >= len(dates):
            break
        window_dates = dates[idx + 1: idx + 1 + K]
        if len(window_dates) < K:
            break

        if held is None or not use_buffer:
            sub = df[df["date"] == t0].dropna(subset=["mom20"])
            sub = sub[~sub["one_word"]]                  # 买不进的票不进篮子
            if len(sub) < TOPN:
                continue
            new_held = list(sub.nlargest(TOPN, "mom20")["code"])
        else:
            sub = df[df["date"] == t0].dropna(subset=["mom20"])
            ranked = list(sub["code"])                 # 近似：按 code 排序，缓冲区语义另测
            band = set(ranked[: TOPN * 3])
            new_held = [c for c in (held or []) if c in band]
            have = set(new_held)
            for c in ranked:
                if len(new_held) >= TOPN:
                    break
                if c not in have:
                    new_held.append(c)
        # 换手率必须**实测**，不能假设 1.0：
        # 平台日频相邻 top-10 实测重合 75.17%，换手只有 24.83%。
        # 本脚本第一版在每个调仓点写死 turnover_ratio=1.0，
        # 把日频年成本虚报成 24.51%（真实约 15–18%）——高估成本会让
        # 频率对比得出「日频更差」的错误结论。
        if held is None:
            turnover_ratio = 1.0                        # 首期建仓确实全买
        else:
            turnover_ratio = (len(set(new_held) - set(held)) + len(set(held) - set(new_held))) / (2.0 * TOPN)
        held = new_held

        # 持有期内每天的等权收益，逐日复利
        block = fwd.reindex(index=window_dates, columns=held)
        daily = block.mean(axis=1, skipna=True)
        # 成分不足的日子直接丢弃：宁可少几天样本，也不用 1 只股票代表一个篮子
        counts = block.notna().sum(axis=1)
        daily = daily[counts >= max(3, len(held) // 2)]
        if len(daily) < K:                # 持有期内有任何一天成分不足 → 整期作废
            continue
        gross = float(np.prod(1.0 + daily.values) - 1.0)
        net = gross - turnover_ratio * cost
        period_gross.append(gross)
        period_net.append(net)
        n_names_used.append(len(held))
        turnovers.append(turnover_ratio)

    if not period_net:
        return None
    pg, pn = np.array(period_gross), np.array(period_net)
    n = len(pn)
    ann_g = float(np.prod(1 + pg) ** (TRADING_DAYS / (K * n)) - 1)
    ann_n = float(np.prod(1 + pn) ** (TRADING_DAYS / (K * n)) - 1)
    t = float(pn.mean() / (pn.std() / np.sqrt(n))) if n > 1 and pn.std() > 0 else float("nan")
    rebal_per_year = TRADING_DAYS / K
    ann_cost = float(np.mean(turnovers)) * cost * rebal_per_year
    return dict(label=label, K=K, periods=n, ann_gross=ann_g, ann_net=ann_n, t=t,
                mean_names=float(np.mean(n_names_used)), cost=cost,
                mean_turn=float(np.mean(turnovers)), ann_cost=ann_cost)


def main():
    df = load()
    print(f"窗口 {START} ~ {END}；股票 {df['code'].nunique()} 只；交易日 {df['date'].nunique()}")
    print(f"满仓一次调仓成本 = 本金的 {basket_cost(TOPN):.4%}（{TOPN} 只 × 12.60 元 / 50000）\n")
    print("【可靠部分】成本是确定的，不依赖样本量")
    print(f"{'方案':<22}{'K':>4}{'实测换手':>10}{'年成本':>10}")
    print("─" * 48)
    for K, lab in [(1, "日频（平台现状）"), (5, "周频"), (10, "双周频"), (21, "月频（书 3.1.4）"), (63, "季频")]:
        r = run(df, K, lab, use_buffer=False)
        if not r:
            print(f"{lab:<22}{K:>4}  样本不足"); continue
        print(f"{lab:<22}{r['K']:>4}{r['mean_turn']:>9.2%}{r['ann_cost']:>10.2%}")
    print()
    print("【不可靠部分】收益在这个窗口是噪音，只能看量级不能做判决")
    print(f"{'方案':<22}{'期数':>6}{'年化毛':>11}{'年化净':>11}{'t':>8}")
    print("─" * 60)
    for K, lab in [(1, "日频"), (5, "周频"), (10, "双周频"), (21, "月频"), (63, "季频")]:
        r = run(df, K, lab, use_buffer=False)
        if not r or r['periods'] < 3:
            print(f"{lab:<22}{'—':>6}{'样本不足':>11}"); continue
        print(f"{lab:<22}{r['periods']:>6}{r['ann_gross']:>10.2%}{r['ann_net']:>11.2%}{r['t']:>8.2f}")
    print()
    print("⚠ 期数 <20 的方案一律不做统计判决：月频在这个窗口只有 6 期，")
    print("  把 6 个点年化出来的 ±30% 摆动毫无意义。")
    print("  成本那一栏才是本脚本能给出的可靠结论。")


if __name__ == "__main__":
    main()

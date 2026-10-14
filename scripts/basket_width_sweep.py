"""榜宽 × 频率 —— 唯一还可能同时留住信号与压住成本的方向。

## 起因：成本是唯一的敌人，而它按 1/N 缩放

修复市场基准（2026-10-14，`backtest.py` 的 `pos == 0` 守卫）之后，实测数据
把困局说清楚了。`signcorrect_freq_sweep` + `regime_control_sweep` 的**成本列与
Δ列都与基准无关**（市场项在差里对消），所以这两组数字是真的：

    K    Δ净       配对t    命中率        年成本(榜宽 10)
     1   +302.3%    7.78     53.2%         370.4%
     5   +179.3%    5.34     51.4%         122.3%
    21   + 75.0%    2.36     50.3%          35.1%
    63   + 24.6%    0.82     49.5%          12.2%

**信号强度随持有期单调衰减，成本单调下降。信号最强的 K=1 恰恰是成本致命的
K=1；成本可接受的 K=63 恰恰没有信号。** 这不是调参能解的，是结构性困局。

缓冲只能压一半成本（K=1：370% → 176%，5 倍带）——因为榜宽 10 只占 3362 只
可排名股票的 **0.3%**，每天都换手。缓冲救不了「篮子相对池子太窄」。

## 为什么榜宽是剩下唯一没试过的杠杆

1. **成本近似按 1/N 缩放。** top-10 的日换手 ~2.4 倍，top-50 大约 0.8 倍。
   这不是估计——`cost_drag` 按实际换手算，本脚本直接读它的输出。
2. **因子是横截面的。** IC 量度的是全截面单调性，榜从 10 扩到 50 砍掉的是
   最极端的一小撮，不是砍掉效应本身。均值超额应大体留存。
3. **统计噪声按 1/√N 缩放**，而我们要的是净值不是 t 值。

这三条合起来意味着：**存在一个 N，使信号仍够强而成本降到信号之下。**
本脚本就是去找那个 N——或者证明它不存在。

## 主指标：复利终值，不是年化

修复基准那天的教训必须写进代码结构：本次**所有判定都基于复利终值**，
年化只作参考并列。理由是 `lowfreq_plausibility` 已经证明六个配置的复利终值
**全部 < 1**（0.00~0.60 倍），而同期报出的「年超额」高达 +69%~+106%。
两个数同时为真且互相矛盾，因为超额的分母是假的。

复利终值**不需要相信任何年化口径**，它由逐日净收益直接连乘得到。

## 事前登记（写在跑之前，不许事后改）

- **主指标**：`net_final` —— 2019-01~2023-12 的复利净值终值（1.0 = 刚好不亏）
- **达标线**：`net_final ≥ 1.0`（不亏）**且** `net_final / mkt_final ≥ 1.2`
  （跑赢大盘两成）**且** 相对错号对照的配对 t ≥ 3.0
- **容量闸**：`n_ranked ≥ 1000`（太少则结论不可推广）
- **覆盖**：扫描 ~30 个 (N × K × 缓冲)。**在这 30 个里挑最好的，就是又一次
  多重比较**——与那 97 组权重搜索同类。因此任何达标的配置必须再到
  **2024-01 ~ 2026-10** 上复测，两段都达标才算结果。
- 本脚本**不写 `weights.json`**，**不做晋级判定**。

## 已知硬边界

IC 是 2019-2026 全段测的，符号决策对两段都不算样本外。本脚本能拿到的
样本外只有**榜宽与频率的选择**。
"""
import argparse
import json
import math
import pickle
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from aplus.backtest import evaluate_weights, scoreable_count  # noqa: E402

DAYS_PER_YEAR = 244

SIGN_FIXED = {"mom20": -1.0, "rev5": 0.3, "vol20": -0.5, "vsur": -0.5}
WRONG_SIGN = {"mom20": 1.0, "rev5": 0.3, "vol20": 0.5, "vsur": 0.5}
PAIRS = [("符号修正", SIGN_FIXED), ("错号对照", WRONG_SIGN)]

SIZES = [10, 20, 30, 50, 100]
FREQS = [1, 3, 5]
BUFFERS = [None, 3.0]

PREREG = {
    "primary_metric": "net_final",
    "target_net_final": 1.0,
    "target_vs_market": 1.2,
    "min_paired_t": 3.0,
    "min_n_ranked": 1000,
    "confirm_required": "选段达标的必须到 2024-01~2026-10 复测，两段都达标才算结果",
    "note": "全部判定基于复利终值；年化仅作参考。降低 N 是降成本，不是降门槛",
}


def _daily_equity(pred: pd.DataFrame) -> pd.Series:
    """涨腿逐日净收益复利净值。

    `cost` 由 `backtest.cost_drag` 按**实际换手**算出，不是估计值——
    榜宽扫描的全部意义就在成本，绝不能让成本变成一个拍脑袋的常数。
    """
    up = pred[pred["direction"] == "up"].copy()
    if up.empty:
        return pd.Series(dtype=float)
    up["_net"] = up["actual_pct"] - up["cost"]
    return (1.0 + up.groupby("pred_date")["_net"].mean().dropna()).cumprod()


def _market_equity(pred: pd.DataFrame) -> pd.Series:
    """大盘同期复利净值 —— 分母，必须用**修复后**的 `_market`。

    修复前它复利到 0.0069 倍（偏差 −114%/年），任何「相对大盘」的比值都会
    变成除以一个假的极小数。`net_final / mkt_final` 在那种分母下毫无意义。
    """
    m = pred.groupby("pred_date")["market_pct"].mean().dropna()
    return (1.0 + m).cumprod()


def _paired_t(pred: pd.DataFrame) -> float | None:
    """相对**错号对照**的配对 t（逐日净值之差，非两个独立 t 值）。"""
    up = pred[pred["direction"] == "up"].copy()
    if up.empty:
        return None
    up["_net"] = up["actual_pct"] - up["cost"]
    return up.groupby("pred_date")["_net"].mean().dropna()


def _run(xs, dates, w, n, k, buf):
    return evaluate_weights(xs, w, top_n=n, bottom_n=n, dates=dates,
                            rebalance_every=k, buffer_multiple=buf)


def sweep(xs, start, end, label) -> list[dict]:
    dates = sorted(d for d in xs["_dates"] if d >= start and d <= end)
    probe = next((d for d in dates if not xs[d].empty), None)
    # 容量闸：榜宽 N 必须远小于可排名池，否则「榜宽 N」其实是「榜宽 = 全池」，
    # 结论无法推广。取**最早**那个非空横截面的池子（最保守：停牌最多）。
    n_ranked_fn = (lambda x, _d, _n: scoreable_count(x[probe], SIGN_FIXED)) \
        if probe else None
    rows = []
    print(f"\n{'='*104}\n{label}  {start} ~ {end}   {len(dates)} 个交易日\n{'='*104}")
    print(f"{'N':>4}{'K':>4}{'缓冲':>6}{'配置':<10}{'年成本':>9}"
          f"{'净值终值':>11}{'大盘终值':>11}{'相对大盘':>10}{'年复利':>9}{'配对t':>8}")
    for n in SIZES:
        for k in FREQS:
            for buf in BUFFERS:
                series, finals, cost = {}, {}, 0.0
                mkt_final = None
                for name, w in PAIRS:
                    t0 = time.time()
                    pred = _run(xs, dates, w, n, k, buf)
                    eq, mk = _daily_equity(pred), _market_equity(pred)
                    finals[name] = float(eq.iloc[-1]) if len(eq) else float("nan")
                    series[name] = _paired_t(pred)
                    up = pred[pred["direction"] == "up"]
                    if len(up):
                        nd = max(len(set(up["pred_date"])), 1)
                        # 按日**取均值**再求和：`evaluate_weights` 把同一个
                        # 组合级 drag 写给涨腿每一行，`sum()` 会把每期成本
                        # 记成 `top_n` 份、整列虚高 N 倍（实测榜宽 10 时
                        # 报 370.4%，实为 37.04%）。
                        cost = float(up.groupby("pred_date")["cost"].mean().sum()) \
                            / (nd / DAYS_PER_YEAR)
                    if mkt_final is None and len(mk):
                        mkt_final = float(mk.iloc[-1])
                    del pred, up, eq, mk

                diff = series["符号修正"] - series["错号对照"]
                diff = diff.dropna()
                pt = float(diff.mean() / (diff.std(ddof=1) / math.sqrt(len(diff)))) \
                    if len(diff) > 2 and diff.std(ddof=1) > 0 else None

                fin = finals["符号修正"]
                rel = fin / mkt_final if mkt_final else float("inf")
                comp = float(fin ** (DAYS_PER_YEAR / max(len(dates), 1)) - 1) \
                    if fin and fin > 0 else float("nan")
                # 容量闸：可排名股票太少时结论不可推广。低于 1000 就明确判不达标，
                # 而不是照报一个漂亮数字。
                n_ranked = int(n_ranked_fn(xs, dates, n)) if n_ranked_fn else 0
                ok = (fin >= 1.0 and rel >= 1.2 and (pt or 0) >= 3.0
                      and n_ranked >= PREREG["min_n_ranked"])
                mark = "  <<< 达标" if ok else ""
                rows.append({"n": n, "rebalance_every": k, "buffer_multiple": buf,
                             "final_multiple": fin, "market_final": mkt_final,
                             "net_vs_market": rel, "comp_annual": comp,
                             "annual_cost": cost, "paired_t": pt,
                             "n_ranked": n_ranked, "hits_prereg": ok,
                             "wrong_sign_final": finals["错号对照"]})
                print(f"{n:>4}{k:>4}{str(buf):>6}{'符号修正':<10}{cost:>8.1%}"
                      f"{fin:>11,.2f}{mkt_final:>11,.2f}{rel:>10.2f}"
                      f"{comp:>8.1%}{(pt or 0):>8.2f}{mark}", flush=True)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", type=Path, default=Path("/tmp/reversal_panel_cache.pkl"))
    ap.add_argument("--start", default="2019-01-02")
    ap.add_argument("--end", default="2023-12-31")
    ap.add_argument("--confirm-start", default=None)
    ap.add_argument("--confirm-end", default=None)
    ap.add_argument("--out", type=Path, default=Path("data/reports/basket_width_sweep.json"))
    args = ap.parse_args()

    xs = pickle.loads(args.cache.read_bytes())
    out = {"kind": "basket_width_sweep", "prereg": PREREG,
           "window": [args.start, args.end], "rows": []}
    out["rows"] = sweep(xs, args.start, args.end, "选段")
    if args.confirm_start:
        out["confirm_window"] = [args.confirm_start, args.confirm_end]
        out["confirm_rows"] = sweep(xs, args.confirm_start, args.confirm_end, "复测段（未参与选择）")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str),
                        encoding="utf-8")
    print(f"\n写出 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

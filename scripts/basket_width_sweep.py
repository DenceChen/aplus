"""榜宽 × 频率 —— 唯一还可能同时留住信号与压住成本的方向。

## 起因：成本是唯一的敌人，而它按 1/N 缩放

以下是 2026-10-14 **修复两个 P0 之前**跑出来的数。留在这里是因为本脚本的
设计动机就是从它们来的，但**它们现在全部作废，不得引用**：

    K    Δ净       配对t    命中率        年成本(榜宽 10)
     1   +302.3%    7.78     53.2%         370.4%   ← 成本虚高 10×，实为 37.04%
     5   +179.3%    5.34     51.4%         122.3%   ← 实为 12.23%
    21   + 75.0%    2.36     50.3%          35.1%   ← 实为 3.51%
    63   + 24.6%    0.82     49.5%          12.2%   ← 实为 1.22%

作废原因两条，各占一半：

1. **成本列**：`sum()` 聚合虚高恰好 `top_n` 倍（见 `tests/test_cost_aggregation.py`）。
2. **Δ净列**：`Δ净 = Δ广度 − (年成本_修 − 年成本_错)`，两个成本都虚高，
   所以那个**差**也虚高，Δ净跟着作废。

`命中率` 与 `配对 t` 不受这两个 bug 影响——它们既不含市场项，也不含成本。

### 作废之后，这张表还支持什么结论？

**不再支持**「信号随持有期单调衰减、成本单调下降，两头不兼容」这个叙述。
修正成本后，K=1 的成本/信号比是**四档里最低的**（37.04% 对信号约 92%），
而不是最高的。原叙述整段需要重写。

### 一个不作废的独立事实

`data/reports/lowfreq_plausibility.json` 的 `final_multiple`（复利净值终值）
**既不含市场项，成本聚合也是对的**，因此下面这个结论**独立于 P0 修复成立**：

    K=1  符号修正 0.114 倍   错号对照 0.0003 倍
    K=21 符号修正 0.602 倍   错号对照 0.168  倍
    K=63 符号修正 0.407 倍   错号对照 0.33   倍

**六个配置在 2019–2023 全部亏损，榜宽 10。** 这是目前唯一一个经得起
P0 审计的否决结论，也是本脚本要推翻的对象。

### 为什么榜宽仍然是剩下唯一没试过的杠杆

1. **成本近似按 1/N 缩放。** top-10 的日换手 ~2.4 倍，top-50 大约 0.8 倍。
   这不是估计——`cost_drag` 按实际换手算，本脚本直接读它的输出。
2. **因子是横截面的。** IC 量度的是全截面单调性，榜从 10 扩到 50 砍掉的是
   最极端的一小撮，不是砍掉效应本身。均值超额应大体留存。
3. **统计噪声按 1/√N 缩放**，而我们要的是净值不是 t 值。

**但这三条都只是理由，不是预测。** 榜宽只压成本，不改毛收益：
如果符号修正篮子的**毛**收益本身就是负的（本脚本会一并输出毛收益），
那把成本从 0.152%/日 压到 0.05%/日 也救不回来——净值从 0.114 变 0.5 而已，
仍是亏。本脚本必须能给出「榜宽也救不了」的结论，那同样是有效结果。

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
    "diagnostic_gross_final": (
        "并列报出 gross_final（不扣成本的毛净值终值）。它不参与达标判定，"
        "但 gross_final < 1 意味着选股本身毁灭价值——此时榜宽无论多宽都无解，"
        "必须直说而不是让『成本太高』盖住『选股没用』"),
}


def _daily_equity(pred: pd.DataFrame, gross: bool = False) -> pd.Series:
    """涨腿逐日净收益复利净值。

    `cost` 由 `backtest.cost_drag` 按**实际换手**算出，不是估计值——
    榜宽扫描的全部意义就在成本，绝不能让成本变成一个拍脑袋的常数。

    `gross=True` 时**不扣成本**，得到毛净值。这不是附赠，是本脚本最重要
    的输出之一：毛净值 < 1 意味着**选股本身在毁灭价值**，此时把榜宽从
    10 扩到 100 只是把亏损从 0.11 倍改成 0.6 倍——**榜宽救不了它**。
    毛利与成本必须分开报，否则一个「成本太高」的结论会把「选股没用」
    这件更要命的事藏起来。
    """
    up = pred[pred["direction"] == "up"].copy()
    if up.empty:
        return pd.Series(dtype=float)
    col = "actual_pct" if gross else "_net"
    if not gross:
        up["_net"] = up["actual_pct"] - up["cost"]
    return (1.0 + up.groupby("pred_date")[col].mean().dropna()).cumprod()


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
    print(f"\n{'='*116}\n{label}  {start} ~ {end}   {len(dates)} 个交易日\n{'='*116}")
    print(f"{'N':>4}{'K':>4}{'缓冲':>6}{'年成本':>8}{'毛终值':>9}{'净终值':>10}"
          f"{'大盘终值':>10}{'相对大盘':>9}{'年复利':>8}{'配对t':>7}  判定")
    for n in SIZES:
        for k in FREQS:
            for buf in BUFFERS:
                series, finals, gross_finals, cost = {}, {}, {}, 0.0
                mkt_final = None
                for name, w in PAIRS:
                    t0 = time.time()
                    pred = _run(xs, dates, w, n, k, buf)
                    eq = _daily_equity(pred)
                    ge = _daily_equity(pred, gross=True)
                    mk = _market_equity(pred)
                    finals[name] = float(eq.iloc[-1]) if len(eq) else float("nan")
                    gross_finals[name] = float(ge.iloc[-1]) if len(ge) else float("nan")
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
                    del pred, up, eq, ge, mk

                diff = series["符号修正"] - series["错号对照"]
                diff = diff.dropna()
                pt = float(diff.mean() / (diff.std(ddof=1) / math.sqrt(len(diff)))) \
                    if len(diff) > 2 and diff.std(ddof=1) > 0 else None

                fin = finals["符号修正"]
                gross = gross_finals["符号修正"]
                rel = fin / mkt_final if mkt_final else float("inf")
                comp = float(fin ** (DAYS_PER_YEAR / max(len(dates), 1)) - 1) \
                    if fin and fin > 0 else float("nan")
                # 容量闸：可排名股票太少时结论不可推广。低于 1000 就明确判不达标，
                # 而不是照报一个漂亮数字。
                n_ranked = int(n_ranked_fn(xs, dates, n)) if n_ranked_fn else 0
                ok = (fin >= 1.0 and rel >= 1.2 and (pt or 0) >= 3.0
                      and n_ranked >= PREREG["min_n_ranked"])
                # 毛终值 < 1 = 选股本身毁灭价值。此时**任何**榜宽都救不回来，
                # 必须在判决里直说，而不是让「成本太高」这个说法盖住它。
                if gross < 1.0:
                    verdict = "毛<1：选股本身亏，榜宽无解"
                elif not ok:
                    verdict = ""
                else:
                    verdict = "<<< 达标"
                rows.append({"n": n, "rebalance_every": k, "buffer_multiple": buf,
                             "final_multiple": fin, "gross_final": gross,
                             "market_final": mkt_final,
                             "net_vs_market": rel, "comp_annual": comp,
                             "annual_cost": cost, "paired_t": pt,
                             "n_ranked": n_ranked, "hits_prereg": ok,
                             "wrong_sign_final": finals["错号对照"],
                             "wrong_sign_gross_final": gross_finals["错号对照"]})
                print(f"{n:>4}{k:>4}{str(buf):>6}{cost:>8.1%}{gross:>9.3f}{fin:>10,.3f}"
                      f"{mkt_final:>10,.2f}{rel:>9.1f}"
                      f"{comp:>8.1%}{(pt or 0):>7.2f}  {verdict}", flush=True)
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

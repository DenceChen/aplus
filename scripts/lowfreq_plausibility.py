"""低频净值的**可信度上限**检查——年化数字必须能被净值曲线兑现。

## 起因：一个不像真的数字

`signcorrect_freq_sweep` 报出：旧默认（错号配置）在 K=21 上年超额
**+88.4%**、K=63 上 **+95.0%**、t=6.67。若这是可兑现的超额，一个 10 只
A 股多头组合在 2019–2023 跑出约 **97%/年**、4.7 年约 **60 倍**。

60 倍对 A 股小盘多头不是「很强」，是**不可能**。所以年化口径本身要么错了，
要么那不是超额。两个可能性必须分开验：

1. **口径错**：`年化 = 逐日均值 × 244` 与净值曲线不等价（算术 vs 复利、
   逐行 vs 逐日、非调仓日的处理）。
2. **指标错**：那不是超额，是 beta / 选股池本身的市场暴露。

## 本脚本只做一件事：把净值曲线**摆出来**

对每组 (配置, K) 直接复利出日度净值，并报：

- 期末净值（复利，**不可辩驳**的一个数）
- 同期把同一区间的大盘等权持有做出来，算出真的超额复利
- 同期买入并持有沪深全指

**复利终值是唯一不需要相信任何年化口径的数字。** 年化与之对不上，
就是年化错了；两者一致，那 60 倍就真的是策略赚到的——那就得怀疑数据。

## 判据（事前登记）

若符号修正配置在任一 K 上的**复利超额终值**超过大盘同期复利的 **5 倍**，
则该 K 的年化数字不得引用，回到口径层排查。这条线是拍脑袋定的，
但它只用来**否定**，不用来**肯定**——它拦不住所有假信号，只拦明显离谱的。
"""
import argparse
import json
import pickle
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from aplus.backtest import evaluate_weights  # noqa: E402

SIGN_FIXED = {"mom20": -1.0, "rev5": 0.3, "vol20": -0.5, "vsur": -0.5}
WRONG_SIGN = {"mom20": 1.0, "rev5": 0.3, "vol20": 0.5, "vsur": 0.5}
PAIRS = [("符号修正", SIGN_FIXED), ("错号对照", WRONG_SIGN)]
FREQS = [1, 21, 63]
PLAUSIBILITY_MULTIPLE = 5.0


def _daily_equity(pred: pd.DataFrame) -> pd.Series:
    """涨腿逐日净值的**复利**曲线（日频净收益序列）。

    每行是一只成分股一天的前瞻收益，先按天取均值得到该日组合收益，
    再逐日复利。这与 `年化 = 逐日均值 × 244` 是两回事：
    算术年化把单日 1% 和 −1% 当成可以线性相加，而复利下它们是 0。
    2019–2023 若日超额均值 0.39%（=95%/244），算术年化给 +95%，
    复利给 (1.0039)^1154 ≈ **+87 倍**——差距不是细节，是量级。
    """
    up = pred[pred["direction"] == "up"].copy()
    up["_net"] = up["actual_pct"] - up["cost"]
    daily = up.groupby("pred_date")["_net"].mean().dropna()
    return (1.0 + daily).cumprod()


def _market_equity(pred: pd.DataFrame) -> pd.Series:
    m = pred.groupby("pred_date")["market_pct"].mean().dropna()
    return (1.0 + m).cumprod()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", type=Path,
                    default=Path("/tmp/reversal_panel_cache.pkl"))
    ap.add_argument("--start", default="2019-01-02")
    ap.add_argument("--end", default="2023-12-31")
    ap.add_argument("--out", type=Path,
                    default=Path("data/reports/lowfreq_plausibility.json"))
    ap.add_argument("--reuse", action="store_true",
                    help="复用已盖章且窗口匹配的缓存。**默认重建**")
    args = ap.parse_args()

    # **2026-10-14：这行原来是无条件 `pickle.loads`——不查存在性、
    # 不查窗口、不查来源。** 缓存是三天前的污染面板时，它照跑不误，
    # 还照常产出一份带结论的报告。现走 `panel_cache`（见该模块 docstring）。
    xs = PC.load_cache(args.cache, root=ROOT, start=args.start, end=args.end,
                       reuse=args.reuse)
    dates = sorted(d for d in xs["_dates"] if d >= args.start and d <= args.end)
    print(f"面板 {len(xs['_dates'])} 日；本段 {len(dates)} 个交易日\n", flush=True)

    rows = []
    for k in FREQS:
        curves = {}
        for name, w in PAIRS:
            t = time.time()
            pred = evaluate_weights(xs, w, top_n=10, bottom_n=10,
                                    dates=dates, rebalance_every=k)
            curves[name] = (_daily_equity(pred), _market_equity(pred))
            print(f"  K={k:>3} {name} 跑完 {time.time()-t:.0f}s", flush=True)

        mk = curves["符号修正"][1]
        mkt_final = float(mk.iloc[-1])
        print(f"\n{'='*78}\nK={k}   大盘同期复利终值 = {mkt_final:,.1f} 倍"
              f"（{len(mk)} 个交易日）\n{'='*78}")
        print(f"{'配置':<12}{'终值(倍)':>12}{'相对大盘(倍)':>14}"
              f"{'倍数>5x?':>10}{'年均算术':>11}{'年均复利':>11}")
        for name, _ in PAIRS:
            eq = curves[name][0]
            fin = float(eq.iloc[-1])
            n = len(eq)
            # 算术年化（= 扫描脚本的口径）与复利年化并列，让差异自己说话
            daily = (eq.pct_change().dropna())
            arith = float(daily.mean()) * 244
            comp = float(fin ** (244 / n) - 1) if fin > 0 else float("nan")
            ratio = fin / mkt_final if mkt_final else float("inf")
            flag = "  可疑!!" if ratio > PLAUSIBILITY_MULTIPLE else ""
            rows.append({"k": k, "name": name, "final_multiple": fin,
                         "market_final_multiple": mkt_final,
                         "excess_vs_market": ratio,
                         "arith_annual": arith, "comp_annual": comp,
                         "implausible": ratio > PLAUSIBILITY_MULTIPLE})
            print(f"{name:<12}{fin:>12,.1f}{ratio:>14,.1f}{flag:>10}"
                  f"{arith:>11.1%}{comp:>11.1%}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(
        {"kind": "lowfreq_plausibility",
         "prereg": {"market_multiple_ceiling": PLAUSIBILITY_MULTIPLE,
                    "use": "只用于否定，不用于肯定"},
         "window": [args.start, args.end], "rows": rows},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n写出 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
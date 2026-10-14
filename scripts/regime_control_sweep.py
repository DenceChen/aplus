"""剥掉 regime：符号修正到底带来了多少**信号**，而不是多少 beta。

## 起因：第一版扫描的主指标被它自己的负对照证伪了

`signcorrect_freq_sweep.py` 事前登记的主指标是

    net = 年化(up_excess_vs_market) − 年成本

跑到一半就出现了一个**不可能**的结果：已知逐符号相反的旧默认配置
（`{mom20:+1.0, rev5:+0.3, vol20:+0.5, vsur:+0.5}`）在 K=63 上拿到
**+95.0% 年超额、+83.0% 净、t=6.67**。

一个为「挑明天会跌的票」而生的配置不可能有这个本事。唯一解释是：

    该指标测的是**这一段的市场状态**，不是选股的本事。

`aplus/verify.py` 的文档早就写了这件事，只是我当时没把它当回事：

> **广度对大盘方向免疫。** 涨腿超额 `E[r_up] − E[r_mkt]` 在大盘上涨时会
> 自动变大——选股毫无本事、只是踩在 beta 上，也能拿到一个漂亮的正数。

所以**第一版的主指标自此作废**。这不是「结果不好看所以换个指标」——
是**指标本身没有效度**（validity），而且失效是被内置对照当场证实的。
两种情况必须分开：前者是 p-hacking，后者是发现测量错了。

作废的是「用它当晋级判据」，**不是**已经算出来的那些行——那些行
在下面的对照表里还要用。

## 三个指标，三个不同的毛病

| 指标 | 公式 | 毛病 |
|---|---|---|
| `up_excess` | `E[r_up − cost] − E[r_mkt]` | **含 beta**：踩在牛市上就有正数 |
| `excess_breadth` | `E[r_up − cost] − E[r_down]` | 市场项消掉了，但**不可兑现**（空不掉跌篮子） |
| `excess_tilt` | `E[r_up − cost] − 2E[r_mkt] + E[r_down]` | 两端合起来偏离市场 = 名单含系统性暴露 |

**没有任何一个是干净的**。所以本脚本不挑一个当主指标，而是加上第四个：

## 负对照差值 —— 本脚本的主指标

    signal = 指标(符号修正) − 指标(旧默认错号)

两个配置跑在**同一批日期、同一份面板**上，只差权重符号。它们共同的部分
（市场状态、板块轮动、2019-2023 的动量牛市）**逐项抵消**；剩下的才是
选股符号本身的贡献。

这比「哪个指标更干净」更可靠，因为它**不依赖对指标性质的假设**，只依赖
一个事实：两个配置除符号外完全同源。

差值的 t 检验用**配对**口径：逐日算两配置的超额之差，再对日序列做 t 检验。
配对会消掉共同的时间序列波动，因此这个 t 是本脚本里**唯一无偏**的显著性
证据——未配对的两组 t 各算各的，regime 波动仍留在分母里。

## 事前登记（写在跑之前）

**达标线**：`signal_net ≥ 0.20` **且** `paired_t ≥ 3.0` **且**
`n_ranked ≥ 4000`。`signal_net` 用**广度口径**（免疫 regime）——既然目标是
年化 20%，而广度是不含 beta 的那个数，宁可保守。

**仍然必须复测**：本脚本仍只跑 2019–2023 选段。任何达标者必须到
2024-01 ~ 2026-10 复测，两段都达标才算结果。本脚本**不写 weights.json**。

**已知硬边界**：IC 是 2019-2026 全段测的，符号决策对两段都不算样本外。
这里能拿到样本外的只有频率与缓冲的选择。
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from aplus.backtest import (  # noqa: E402
    build_cross_sections, evaluate_weights, scoreable_count,
)
from aplus import panel_cache as PC
from aplus.store import LocalStore

ROOT = Path(__file__).resolve().parents[1]  # noqa: E402
from aplus.verify import rolling_stats  # noqa: E402

DAYS_PER_YEAR = 244

# ── 对照组 ──
#
# `SIGN_FIXED` 是现役默认权重（符号按实测 IC 校正）。
# `WRONG_SIGN` 是**真实的上一版生产权重**——2026-10-14 的 730 天赛马里
# 排 81/87、测试段 t=−3.03。它在本脚本里不是候选，是**尺子**。
#
# ## 翻转的是 3 个因子，不是 4 个
#
# `rev5` 两边都是 `+0.3`，因为它的实测 IC 为**正**（+0.0262），本来就不该
# 翻；需要翻的只有 IC 为负的 mom20 / vol20 / vsur。真实的生产变更本身
# 就是这 3 个因子的翻转，所以对照组照搬真实旧配置，而不是造一个合成的
# 全符号翻转——**对照组该反映被评估的那个东西**。
#
# 幅度逐因子相同，因此差值里既没有幅度效应，也没有 rev5 的贡献，
# 剩下的**只**是那 3 个因子的符号效应。`tests/test_regime_control_sweep.py`
# 把这个构造前提钉死。
SIGN_FIXED = {"mom20": -1.0, "rev5": 0.3, "vol20": -0.5, "vsur": -0.5}
WRONG_SIGN = {"mom20": 1.0, "rev5": 0.3, "vol20": 0.5, "vsur": 0.5}

PAIRS = [("符号修正", SIGN_FIXED), ("错号对照", WRONG_SIGN)]
FREQS = [1, 5, 21, 63]
BUFFERS = [None, 3.0]

PREREG = {
    "target_signal_net": 0.20,
    "min_paired_t": 3.0,
    "min_n_ranked": 4000,
    "primary_metric": "signal_breadth_excess",
    "note": (
        "达标组合必须到 2024-01~2026-10 未参与选择的窗口复测，两段都达标"
        "才算结果。本脚本不写 weights.json。"
    ),
}


def _panel(data_root: Path, cache: Path, reuse: bool,
           start=None, end=None):
    # 2026-10-14：原为 `if cache.exists() and not rebuild`——那次假重跑
    # 就是从这里流出去的。理由与 _verify_reversal_freq.py 完全相同。
    if reuse:
        xs = PC.load_cache(cache, root=ROOT, start=start, end=end, reuse=True)
        print(f"复用面板缓存 {len(xs['_dates'])} 个交易日", flush=True)
        return xs
    store = LocalStore(data_root)
    daily_map = {p.stem: store.read_daily(p.stem)
                 for p in sorted((data_root / "master" / "daily").glob("*.parquet"))}
    t = time.time()
    print(f"日线 {len(daily_map)} 只载入 {time.time()-t:.0f}s", flush=True)
    from aplus.snapshot import load_factors_map
    xs = build_cross_sections(store, "2019-01-01", "2026-12-31",
                              daily_map=daily_map,
                              factors_map=load_factors_map(store))
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(__import__("pickle").dumps(xs))
    print(f"横截面 {len(xs['_dates'])} 个交易日 {time.time()-t:.0f}s", flush=True)
    return xs


def _metrics(pred: pd.DataFrame) -> dict:
    """一次性把三个指标都算出来——事后补算会再跑一遍面板。

    年化口径：三个量都是**逐日**收益的均值，乘 DAYS_PER_YEAR 得到
    算术年超额。`actual_pct` 由 `_next_bars` 按 ENTRY_LAG 生成，逐行都是
    单日口径，所以 ×244 成立（不是持有期收益，持有期收益会虚报 K 倍）。

    `cost` 必须**按日取均值再求和**：`evaluate_weights` 把同一个组合级 drag
    写给涨腿每一行，`sum()` 会把每期成本记成 `top_n` 份、整列虚高 10 倍
    （实测 K=1 报 370.4%，实为 37.04%）。
    """
    st = rolling_stats(pred)
    up, dn = pred[pred["direction"] == "up"], pred[pred["direction"] == "down"]
    n_days = len(set(up["pred_date"])) if len(up) else 0
    cost = float(up.groupby("pred_date")["cost"].mean().sum()) / max(n_days / DAYS_PER_YEAR, 1e-9)
    return {
        "n_days": n_days,
        "annual_cost": cost,
        "annual_up_excess": float(st["up_excess_vs_market"]) * DAYS_PER_YEAR,
        "annual_breadth": float(st["excess_breadth"]) * DAYS_PER_YEAR,
        "annual_tilt": float(st["excess_tilt"]) * DAYS_PER_YEAR,
        "hit_rate": float(st["overall_hit_rate"]),
        "_pred": pred,
    }


def _daily_series(pred: pd.DataFrame, col: str) -> pd.Series:
    """按 pred_date 聚合的逐日序列——配对 t 检验的分母来源。"""
    up = pred[pred["direction"] == "up"]
    dn = pred[pred["direction"] == "down"]
    s_up = ((up.assign(_x=up["actual_pct"] - up["cost"])
             .groupby("pred_date")["_x"].mean()))
    s_dn = dn.groupby("pred_date")["actual_pct"].mean()
    if col == "breadth":
        s = s_up.sub(s_dn, fill_value=float("nan"))
    else:
        s = s_up.sub(pred.groupby("pred_date")["market_pct"].mean(),
                     fill_value=float("nan"))
    return s.dropna()


def sweep(xs, start: str, end: str, label: str) -> list[dict]:
    dates = sorted(d for d in xs["_dates"] if d >= start and d <= end)
    probe = next((d for d in dates if not xs[d].empty), None)
    rows: list[dict] = []

    hdr = (f"\n{'K':>4}{'缓冲':>6}  {'年成本':>8}"
           f"{'↑超额(旧)':>10}{'↑超额(新)':>10}{'↑差':>9}"
           f"{'广度(旧)':>10}{'广度(新)':>10}{'广度差':>9}"
           f"{'Δ净':>9}{'配对t':>8}{'命中率':>8}")
    print(f"\n{'='*100}\n{label}  {start} ~ {end}\n{'='*100}{hdr}")

    for k in FREQS:
        for buf in BUFFERS:
            got = {}
            for name, w in PAIRS:
                pred = evaluate_weights(xs, w, top_n=10, bottom_n=10,
                                        dates=dates, rebalance_every=k,
                                        buffer_multiple=buf)
                got[name] = _metrics(pred) if len(pred) else None

            nrank = scoreable_count(xs[probe], SIGN_FIXED) if probe else 0
            fix, wrong = got["符号修正"], got["错号对照"]
            if not fix or not wrong:
                print(f"{k:>4}{str(buf):>6}  （样本为空，跳过）", flush=True)
                continue

            d_up = fix["annual_up_excess"] - wrong["annual_up_excess"]
            d_bd = fix["annual_breadth"] - wrong["annual_breadth"]
            # 净差：两条腿都只按涨腿计成本，所以成本差直接相减。
            d_net = d_bd - (fix["annual_cost"] - wrong["annual_cost"])

            # 配对 t：逐日广度之差。regime 的逐日波动在差里被消掉。
            s_fix = _daily_series(fix["_pred"], "breadth")
            s_wrong = _daily_series(wrong["_pred"], "breadth")
            common = s_fix.index.intersection(s_wrong.index)
            diff = (s_fix.loc[common] - s_wrong.loc[common])
            n = len(diff)
            sd = float(diff.std(ddof=1)) if n > 1 else 0.0
            pt = float(diff.mean() / (sd / (n ** 0.5))) if n > 1 and sd > 0 else 0.0

            hit = "  <<<" if (d_net >= PREREG["target_signal_net"]
                              and abs(pt) >= PREREG["min_paired_t"]
                              and nrank >= PREREG["min_n_ranked"]) else ""
            rows.append({
                "rebalance_every": k, "buffer_multiple": buf,
                "n_ranked": nrank,
                "wrong_sign": {kk: vv for kk, vv in wrong.items() if kk != "_pred"},
                "sign_fixed": {kk: vv for kk, vv in fix.items() if kk != "_pred"},
                "delta_up_excess": d_up, "delta_breadth": d_bd,
                "signal_net": d_net, "paired_t": pt, "paired_n": n,
                "prereg_hit": bool(hit),
            })
            print(f"{k:>4}{str(buf):>6}"
                  f"{fix['annual_cost']:>8.1%}"
                  f"{wrong['annual_up_excess']:>10.1%}"
                  f"{fix['annual_up_excess']:>10.1%}"
                  f"{d_up:>9.1%}"
                  f"{wrong['annual_breadth']:>10.1%}"
                  f"{fix['annual_breadth']:>10.1%}"
                  f"{d_bd:>9.1%}"
                  f"{d_net:>9.1%}{pt:>8.2f}{fix['hit_rate']:>8.1%}{hit}",
                  flush=True)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", type=Path, default=Path("/tmp/aplus_snap_final"))
    ap.add_argument("--start", default="2019-01-02")
    ap.add_argument("--end", default="2023-12-31")
    ap.add_argument("--confirm-start", default=None)
    ap.add_argument("--confirm-end", default=None)
    ap.add_argument("--cache", type=Path,
                    default=Path("/tmp/reversal_panel_cache.pkl"))
    ap.add_argument("--out", type=Path,
                    default=Path("data/reports/regime_control_sweep.json"))
    ap.add_argument("--reuse", action="store_true",
                    help="复用已盖章且窗口匹配的缓存。**默认重建**")
    args = ap.parse_args()

    xs = _panel(args.data_root, args.cache, args.reuse,
               start=args.start, end=args.end)
    out = {"kind": "regime_control_sweep", "prereg": PREREG,
           "design": (
               "signal = 指标(符号修正) − 指标(错号对照)。两组同源同日，"
               "共同部分即 regime，逐项抵消。paired_t 为逐日配对 t。"),
           "window": [args.start, args.end], "rows": []}
    out["rows"] = sweep(xs, args.start, args.end, "选段（2019-2023）")
    if args.confirm_start:
        out["confirm_window"] = [args.confirm_start, args.confirm_end]
        out["confirm_rows"] = sweep(xs, args.confirm_start, args.confirm_end,
                                    "复测段（未参与选择）")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print(f"\n写出 {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
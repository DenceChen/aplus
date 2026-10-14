"""独立验证 #2：`反转 × 调仓频率` 在 2019-2023 干净数据上。

## 为什么频率比权重更关键

频率赛马（36 组，2024-10~2026-10）给出的画面是刺眼的：

| 权重 | 频率 | 年成本 | 年超额 | 净 |
|---|---|---|---|---|
| 现任 | 日频 K=1 | **272.1%** | −145.9% | **−418.0%** |
| 现任 | 周频 K=5 | 82.5% | −29.0% | −111.5% |
| 现任 | 月频 K=21 | 27.9% | +6.8% | −21.1% |
| `{mom20:-1.0}` | 季频 K=63 | 10.5% | +39.7% | **+29.2%**（t=1.23） |

**现任在任何频率下都是负的**，而反转族一旦降频就转正。
这说明两件事同时成立：权重方向错了，且**执行频率错了**——
5 万本金 / 10 只 / 单只 5000 元时，日频调仓的 5 元最低佣金
让任何 alpha 都会被交易成本吃掉。

## 为什么必须重跑

上面那张表来自 2024-10~2026-10，而我**看过**那段数据的结果。
它不能作为「反转 + 低频」可行的证据——那正是方向污染闸要拦的情形。

所以在 **2019-01 ~ 2023-12**（从未看过任何赛马结果的干净集）上重跑。
这里回答的是一个更窄也更硬的问题：
**在干净数据上，「反转 + 低频」的净值扣除成本后是否仍为正。**

注意本脚本**不**做晋级判定，也不写 weights.json。
它只产出一张表；结论仍要过 `oos_decision` 的六道闸。
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aplus import panel_cache as PC  # noqa: E402
from aplus.backtest import build_cross_sections, evaluate_weights  # noqa: E402
from aplus.store import LocalStore  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

CHAMP = {"mom20": 1.0, "rev5": 0.3, "vol20": 0.5, "vsur": 0.5}
CANDIDATES = [
    ("现任", CHAMP),
    ("纯 20 日反转", {"mom20": -1.0}),
    ("反转+低异常换手", {"mom20": -0.5, "turnratio": -0.5}),
]
FREQS = [1, 5, 21, 63]
BUFFERS = [None, 3.0]
DAYS_PER_YEAR = 244


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", type=Path, default=Path("/tmp/aplus_snap_final"))
    ap.add_argument("--start", default="2019-01-02")
    ap.add_argument("--end", default="2023-12-31")
    ap.add_argument("--out", type=Path,
                    default=Path("data/reports/reversal_freq_2019_2023.json"))
    ap.add_argument("--cache", type=Path,
                    default=Path("/tmp/reversal_panel_cache.pkl"))
    ap.add_argument("--reuse", action="store_true",
                    help="复用已盖章且窗口匹配的缓存。**默认重建**")
    args = ap.parse_args()

    t0 = time.time()
    # ── 2026-10-14：这里原来写着 `if cache.exists() and not args.rebuild` ──
    # 那是**一次假重跑**：缓存里 `_market` 的均值是 −0.00402，与修复前的
    # 基准污染值逐位相同，五年复合 −99%（真实基准同段 +8.96%/年），
    # 而它算出了「净收益 +109.1%、t=+11.62」——全项目最漂亮的一个数。
    #
    # **「重跑」这个词本身不能作为证据**：命令行是今天的，不代表输入是今天的。
    # 现在走 `panel_cache`：默认重建；显式 `--reuse` 也只放行「没盖章」，
    # 窗口不覆盖、基准指纹对不上这两条**一条都不放**。
    cache = args.cache
    rep = PC.inspect(cache, root=ROOT, start=args.start, end=args.end)
    if args.reuse and rep["ok"]:
        xs = PC.load_cache(cache, root=ROOT, start=args.start, end=args.end,
                           reuse=True)
        print(f"复用 {cache.name} {len(xs['_dates'])} 日 "
              f"基准 {(rep['stamp'] or {}).get('market_mean'):+.6f}/日", flush=True)
    else:
        if cache.is_file():
            print(f"不复用 {cache.name}：", flush=True)
            for p in rep["problems"]:
                print(f"  · {p}", flush=True)
        store = LocalStore(args.data_root)
        daily_map = {p.stem: store.read_daily(p.stem)
                     for p in sorted((args.data_root / "master" / "daily").glob("*.parquet"))}
        from aplus.snapshot import load_factors_map
        print(f"日线 {len(daily_map)} 只载入 {time.time()-t0:.0f}s", flush=True)
        xs = build_cross_sections(store, args.start, args.end, daily_map=daily_map,
                                  factors_map=load_factors_map(store))
        PC.build_and_stamp(cache, xs, start=args.start, end=args.end, root=ROOT)
        print(f"横截面 {len(xs['_dates'])} 个交易日 {time.time()-t0:.0f}s（已盖章）",
              flush=True)

    dates = sorted(xs["_dates"])
    rows = []
    print(f"\n{'候选':<16} {'K':>3} {'缓冲':>5} {'日数':>7} {'年成本':>7} {'年超额':>8} "
          f"{'净':>8} {'t':>6} {'广度t':>6} {'n_ranked':>9}")
    for name, w in CANDIDATES:
        for k in FREQS:
            for buf in BUFFERS:
                pred = evaluate_weights(xs, w, top_n=10, bottom_n=10,
                                        rebalance_every=k, buffer_multiple=buf)
                from aplus.evolve_race import breadth_tstat, excess_tstat
                t, _ = excess_tstat(pred)
                bt, _ = breadth_tstat(pred)
                # 成本**已经在结果表里**（pred["cost"] 列，由 backtest 的
                # cost_drag 按实际换手算出），不需要也不应该自己重估一个模型——
                # 第一版凭印象写了 `_annual_cost()`，那是编的：
                # `summarize_turnover(baskets, top_n)` 签名完全不同，
                # 而我按自己的理解去用。**猜出来的成本口径照常打印出一张漂亮的表。**
                up = pred[pred["direction"] == "up"]
                n_days = max(len(set(pred["pred_date"])), 1)
                n_years = max(n_days / DAYS_PER_YEAR, 1e-9)
                cost = float(up["cost"].sum()) / n_years      # 年化成本
                st = None
                from aplus.verify import rolling_stats
                if len(up):
                    st = rolling_stats(up)
                # rolling_stats 的 up_excess_vs_market 是**毛**超额（未扣成本）；
                # excess_tstat 内部已经 `actual_pct - cost`，是**净值**口径的 t。
                # 两者混用会重复扣一次成本。
                ann_x = float(st.get("up_excess_vs_market", 0.0)) * DAYS_PER_YEAR
                from aplus.backtest import scoreable_count
                probe = next((d for d in dates if not xs[d].empty), None)
                nr = scoreable_count(xs[probe], w) if probe else 0
                net = ann_x - cost
                rows.append({"name": name, "weights": w, "rebalance_every": k,
                             "buffer_multiple": buf, "annual_cost": cost,
                             "annual_excess": ann_x, "net": net,
                             "t": float(t) if t is not None else 0.0,
                             "breadth_t": float(bt) if bt is not None else 0.0,
                             "n_ranked": nr})
                print(f"{name:<16} {k:>3} {str(buf):>5} {n_days:>7} {cost:>7.1%} "
                      f"{ann_x:>+8.1%} {net:>+8.1%} {t or 0:>6.2f} {bt or 0:>6.2f} "
                      f"{nr:>9}", flush=True)

    best = max(rows, key=lambda r: r["net"])
    print(f"\n净收益最高：{best['name']} K={best['rebalance_every']} "
          f"缓冲={best['buffer_multiple']} → {best['net']:+.1%}（t={best['t']:+.2f}）")

    out = {"kind": "independent_validation", "variant": "reversal_x_frequency",
           "window": [args.start, args.end],
           "note": ("独立验证集，从未看过这段数据的任何赛马结果。"
                    "回答的问题：在干净数据上，「反转 + 低频」扣成本后是否仍为正。"
                    "本表**不**做晋级判定。"),
           "champion": CHAMP, "rows": rows, "best": best,
           "elapsed_s": round(time.time() - t0)}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"→ {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

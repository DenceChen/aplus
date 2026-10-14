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
from aplus.provenance import stamp  # noqa: E402
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
          f"{'净':>8} {'t':>6} {'广度t':>6} {'中位可排名/覆盖率':>15}")
    for name, w in CANDIDATES:
        for k in FREQS:
            for buf in BUFFERS:
                pred = evaluate_weights(xs, w, top_n=10, bottom_n=10,
                                        rebalance_every=k, buffer_multiple=buf)
                from aplus.evolve_race import breadth_tstat, excess_tstat
                # **必须传 k**：K 日调仓时选股在 K 天内不变，
                # 逐日序列只有约 n_days/K 次**独立决策**。
                # 不传则自由度虚增 K 倍，t 被系统性压小
                # （实测 K=63 的超额 t 从 −1.37 变成 −2.85，2026-10-14）。
                # 闸门链一直传的是分块口径，只有这个独立验证脚本漏了——
                # 于是它的 t 与赛马的 t **不可比**，却并排印在同一份报告里。
                t, _ = excess_tstat(pred, k)
                bt, _ = breadth_tstat(pred, k)
                # 成本**已经在结果表里**（pred["cost"] 列，由 backtest 的
                # cost_drag 按实际换手算出），不需要也不应该自己重估一个模型——
                # 第一版凭印象写了 `_annual_cost()`，那是编的：
                # `summarize_turnover(baskets, top_n)` 签名完全不同，
                # 而我按自己的理解去用。**猜出来的成本口径照常打印出一张漂亮的表。**
                up = pred[pred["direction"] == "up"]
                n_days = max(len(set(pred["pred_date"])), 1)
                # `cost` 是**篮子级逐日**值，一天一个数、被复制到当天约 10 行
                # up 记录上（backtest.py: `res.loc[...== "up", "cost"] = drag`）。
                # 所以年成本 = **日均** drag × 244；
                # 写成 `.sum() / n_years` 会把同一个 drag 数约 10 遍，
                # 实测六行的放大比全部落在 9.97–9.99（2026-10-14 事故）。
                cost = (float(up["cost"].mean()) * DAYS_PER_YEAR
                        if len(up) else 0.0)                # 年化成本
                st = None
                from aplus.verify import rolling_stats
                if len(up):
                    st = rolling_stats(up)
                # ⚠️ `up_excess_vs_market` **已经是净值口径**——`verify.py` 里
                # `up_net = actual_pct - cost`、`up_excess = avg_up - market`，
                # 成本在算超额**之前**就扣掉了。
                #
                # 这里曾有一行注释断言它是「**毛**超额（未扣成本）」，
                # 并据此写了 `net = ann_x - cost`——**重复扣了一次成本**，
                # 报告的 `净` 多扣 11–35 个百分点。
                # 那行注释不是描述代码，是**替代码辩护**：我以为核过
                # `rolling_stats`，实际没读到 `avg_up` 是怎么构造的。
                # `cost_share` 同源：它同样是「已扣成本」口径下的占比。
                ann_x = float(st.get("up_excess_vs_market", 0.0)) * DAYS_PER_YEAR
                # `n_ranked` 只反映**探针那一天**的可排名只数，不能代表全程。
                # 第一版拿区间内第一个非空日当探针，于是含 `turnratio` 的候选
                # 打印出 `n_ranked=0`——看着像策略失败，其实是 `turnratio`
                # 需要累积成交量历史、缺 2019（aplus/factor.py 已记录）。
                # **更糟的是表格因此藏了一件事：该候选只交易了 988/1154 天。**
                # 覆盖率必须和收益一起印出来，否则「净收益最高」是在比不同长度的赛程。
                from aplus.backtest import scoreable_count
                probe = dates[len(dates) // 2]
                nr = scoreable_count(xs[probe], w) if not xs[probe].empty else 0
                cover = len(up) / max(len(pred), 1)
                # 净 == 年超额。**不再减成本**——它已经扣过了。
                net = ann_x
                rows.append({"name": name, "weights": w, "rebalance_every": k,
                             "buffer_multiple": buf, "annual_cost": cost,
                             "annual_excess": ann_x, "net": net,
                             "t": float(t) if t is not None else 0.0,
                             "breadth_t": float(bt) if bt is not None else 0.0,
                             "n_ranked_mid": nr, "coverage": cover,
                             "days_traded": n_days})
                print(f"{name:<16} {k:>3} {str(buf):>5} {n_days:>7} {cost:>7.1%} "
                      f"{ann_x:>+8.1%} {net:>+8.1%} {t or 0:>6.2f} {bt or 0:>6.2f} "
                      f"{nr:>6}/{cover:>5.0%}", flush=True)

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

    # ── 溯源章：必须带上**所用面板的市场指纹** ──────────────────────
    # 只盖时间戳不够。本项目 2026-10-14 中过一次：报告是当天写的、
    # mtime 比基准修复还新，但它读的是修复前的面板缓存——
    # **按 mtime 分类的审计会把它判成干净。**
    # 指纹让作废与时间无关；章盖在写盘之后，因为章要描述最终状态。
    _sc = PC.sidecar_path(cache)
    _cs = json.loads(_sc.read_text(encoding="utf-8")) if _sc.is_file() else {}
    stamp(args.out, "aplus/backtest.py", root=ROOT,
          extra={"window": [args.start, args.end],
                 "panel_market_mean": _cs.get("market_mean"),
                 "panel_window": _cs.get("window")})
    print(f"→ {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

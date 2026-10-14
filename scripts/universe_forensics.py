"""股票池闸的真实理由：`turnratio` 那 317 只被剔掉的股票，到底是什么。

## 为什么要查这个

7.8 年赛马 97 组里，**涨腿 t 最高的不是过闸冠军**：

    {mom20:-0.5, turnratio:-0.5} K=63   t=+5.04  广度 +2.745  成本 2.3%
    {mom20:-1.0}             K=63   t=+4.14  广度 +1.086  成本 4.3%

`turnratio` 需要 252 日预热（`panel.py` 的 `TURN_RATIO_LONG`），
于是可打分股票从 4904 降到 4567，被**股票池闸**拦下（门槛 2%）。

## 但闸门的理由需要被检验

闸门的原话是「被剔掉的标的集中在缺该因子列的那批股票……**不是随机缺失，
是系统性缺失**」，而 `aplus/coverage.py` 记着「末根 turn 缺 62.0%」。

可是 `turn` 的历史覆盖**实测是完整的**（2019-2026 每年 242~244 根，
`FINDINGS` §3.8），收窄完全来自 252 日预热期。那么被剔掉的 317 只
**到底是什么股票**？如果它们全都是**上市不足 252 个交易日的次新股**，
那么：

  · 这不是「数据缺失」，是**因子的定义属性**——上市不到一年的票
    本来就没有 252 日换手率历史，任何正确实现都会剔掉它们
  · 书里本身就要求剔除次新（3.1.4 次新阈值 **1 年**，我们是 60 日）
  · 闸门的理由（「非随机缺失导致不可比」）**在这个因子上不成立**

## 本脚本查什么

1. 被 `turnratio` 剔掉的股票，按**上市交易日数**分布——是不是清一色次新
2. 剔除比例随时间怎么变（252 日滚动窗口，次新股池会滚动补充）
3. 若只保留上市满 252 日的股票，**其余因子**的可打分数量掉多少
   ——这才是关键：如果别的因子也掉同样多，那「窄」是全市场的事实，
   不是 `turnratio` 的缺陷

用法：
    .venv/bin/python scripts/universe_forensics.py --data-root /tmp/aplus_snap_1012
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from aplus.backtest import ENTRY_LAG, build_cross_sections, evaluate_weights
from aplus.evolve_race import split_window
from aplus.snapshot import load_factors_map
from aplus.store import LocalStore

ROOT = Path(__file__).resolve().parent.parent

WIDE = {"mom20": -1.0}
NARROW = {"mom20": -0.5, "turnratio": -0.5}
FREQS = (63, 21)

# Bonferroni 的校正次数 = 与 ic_guided_race_7y 同口径的候选总数。
N_COMPARISONS = 97

# 252 个交易日 ≈ 1 年。书的次新阈值也是 1 年。
MIN_LISTED_DAYS = 252


def load_universe_panel(daily_map, codes, dates):
    """每只股票在每个日期的「已上市交易日数」。

    用**当日之前**的 bar 数，不用日历天数——252 是交易日口径，
    拿自然日去比会得出「上市 350 天就够」的错觉。
    """
    rows = []
    for code in codes:
        s = daily_map.get(code)
        if s is None or s.empty:
            continue
        s = s.sort_values("date")
        d = pd.to_datetime(s["date"])
        age = pd.Series(np.arange(len(s), dtype=np.int64), index=d.index)
        rows.append(pd.DataFrame({"code": code, "date": d.values, "age": age.values}))
    if not rows:
        return pd.DataFrame(columns=["date", "code", "age"])
    return pd.concat(rows, ignore_index=True)


def listing_age_by_date(daily_map, dates):
    """{date_str: {code: 上市交易日数}}——只用**当日及之前**的 bar 数。"""
    out = {}
    want = {str(d) for d in dates}
    for code, s in daily_map.items():
        if s is None or s.empty:
            continue
        s = s.sort_values("date")
        d = s["date"].astype(str)
        hit = d[d.isin(want)]
        if hit.empty:
            continue
        # 每只股票一次扫描：date → 该日之前的 bar 数（含当日）
        arr_dates = d.to_numpy()
        ages = np.arange(len(arr_dates), dtype=np.int64)
        idx = {v: i for i, v in enumerate(arr_dates)}
        for day in hit.to_numpy():
            out.setdefault(str(day), {})[code] = int(ages[idx[str(day)]] + 1)
    return out



def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2019-01-02")
    ap.add_argument("--end", default="")
    ap.add_argument("--data-root", type=Path, default=Path("/tmp/aplus_snap_1012"))
    ap.add_argument("--out", default="universe_forensics.json")
    args = ap.parse_args()

    data_root = Path(args.data_root)
    if not data_root.is_dir():
        raise SystemExit(f"数据根目录不存在：{data_root}")
    store = LocalStore(data_root)
    reports = data_root / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    files = sorted((store.root / "master" / "daily").glob("*.parquet"))
    end = args.end or str(pd.read_parquet(files[0])["date"].max())

    t0 = time.time()
    print(f"载入日线 {args.start} ~ {end} …", flush=True)
    daily_map = {p.stem: store.read_daily(p.stem) for p in files}
    factors_map = load_factors_map(store)
    xs = build_cross_sections(store, args.start, end, daily_map=daily_map,
                              factors_map=factors_map, entry_lag=ENTRY_LAG)
    _train, test_dates = split_window(xs["_dates"])
    print(f"横截面 {len(xs['_dates'])} 日 · 测试段 {test_dates[0]} ~ {test_dates[-1]}"
          f"（{len(test_dates)} 日，{time.time()-t0:.0f}s）\n", flush=True)

    # ── 1. 被 turnratio 剔掉的是不是次新股 ────────────────────────────
    # **必须读 `xs[day]`（真实横截面）**，不能自己从 `factors_map` 拼一个快照：
    # 那会把 `filter_universe` 的过滤（次新、ST、可交易性）全部绕过去，
    # 量到的「被剔掉」就不是赛马真正面对的那个集合。
    print("=== 被 turnratio 剔掉的股票：上市交易日数分布 ===", flush=True)
    probe_date = test_dates[len(test_dates) // 2]
    sec = xs.get(probe_date)
    dist = {"probe_date": probe_date}
    if sec is None or getattr(sec, "empty", True):
        print(f"  {probe_date} 无横截面，跳过逐日分布", flush=True)
        dist["skipped"] = "横截面为空"
    else:
        ages = listing_age_by_date(daily_map, [probe_date]).get(probe_date, {})
        s = sec.copy()
        s["age"] = s["code"].map(ages)
        has_mom = s["mom20"].notna()
        has_tr = s["turnratio"].notna()
        dropped = s[has_mom & ~has_tr]           # mom20 能打分、turnratio 不能
        kept = s[has_mom & has_tr]
        print(f"  探针日 {probe_date}（真实横截面 {len(s)} 只）")
        print(f"    mom20 可打分        {int(has_mom.sum())} 只")
        print(f"    turnratio 可打分    {int(has_tr.sum())} 只")
        print(f"    **被 turnratio 剔掉** {len(dropped)} 只", flush=True)
        under = 0
        if len(dropped):
            a = dropped["age"].dropna()
            under = int((a < MIN_LISTED_DAYS).sum())
            print(f"    上市交易日数：min={a.min():.0f} p50={a.median():.0f} "
                  f"max={a.max():.0f}")
            print(f"    其中上市不足 {MIN_LISTED_DAYS} 个交易日的："
                  f"**{under}/{len(dropped)} = {under/len(dropped):.1%}**")
            hist = np.histogram(a, bins=[0, 60, 120, 180, 251, 252, 500, 10**6])
            print("    年龄分布：" + "  ".join(
                f"{lab}:{c}" for lab, c in
                zip(["<60", "60-119", "120-179", "180-251", "=252", "253-500", ">500"],
                    hist[0])))
        if len(kept):
            ak = kept["age"].dropna()
            print(f"    对照：留存 {len(kept)} 只，年龄 p50={ak.median():.0f}")
        dist.update({"universe": int(len(s)), "mom_ok": int(has_mom.sum()),
                     "turnratio_ok": int(has_tr.sum()), "dropped": int(len(dropped)),
                     "dropped_under_252": under,
                     "share_under_252": (under / len(dropped)) if len(dropped) else None})
        print()

    # ── 2. 收窄随时间怎么变 ──────────────────────────────────────────
    print("=== 收窄随时间（每年平均可打分比例）===", flush=True)
    yearly = []
    for y in sorted({d[:4] for d in test_dates}):
        ys = [d for d in test_dates if d.startswith(y)]
        ms, ts_ = [], []
        for day in ys:
            sec2 = xs.get(day)
            if sec2 is None or getattr(sec2, "empty", True):
                continue
            n = len(sec2)
            if not n:
                continue
            ms.append(float(sec2["mom20"].notna().mean()))
            ts_.append(float(sec2["turnratio"].notna().mean()))
        if not ms:
            continue
        yearly.append({"year": y, "mom_ok": round(float(np.mean(ms)), 4),
                       "turnratio_ok": round(float(np.mean(ts_)), 4),
                       "gap": round(float(np.mean(ms) - np.mean(ts_)), 4)})
        r = yearly[-1]
        print(f"    {r['year']}  mom20 {r['mom_ok']:.1%}  turnratio "
              f"{r['turnratio_ok']:.1%}  差 {r['gap']:.1%}")


    # ── 3. 两个候选的真实判决（当前代码，非旧报告）────────────────────
    print("\n=== 当前代码下两个候选的判决要素 ===", flush=True)
    print("（t 与广度用 `excess_tstat` / `breadth_tstat` 现算——"
          "`rolling_stats` **不产出**这两个字段，"
          "报告里的值是赛马阶段另算的，直接读会全是 None）\n", flush=True)
    from aplus.evolve_race import (_block_means, _daily_series, bonferroni_t,
                                   breadth_tstat, down_excess_tstat, excess_tstat)
    from aplus.verify import rolling_stats

    def per_block(res, direction, every):
        """逐块涨腿超额——复用 `candidate_block_audit` 的同一口径。

        自己重推一遍就会漏掉 `_daily_series` 的 `MIN_BASKET` 护栏，
        算出来的 t 与赛马报的 t 不是同一个数。
        """
        d = _daily_series(res, direction)
        if d is None:
            return np.empty(0)
        return np.asarray(_block_means(d, every), dtype=float)

    rows = []
    for label, w in (("mom20_only", WIDE), ("with_turnratio", NARROW)):
        for every in FREQS:
            res = evaluate_weights(xs, w, top_n=10, bottom_n=10,
                                  dates=test_dates, rebalance_every=every)
            st = dict(rolling_stats(res))
            t_up, n_obs = excess_tstat(res, every)
            t_dn, _ = down_excess_tstat(res, every)
            t_br, _ = breadth_tstat(res, every)
            blocks = per_block(res, "up", every)
            row = {"label": label, "weights": w, "K": every,
                   "excess_t": t_up, "n_obs": n_obs, "breadth_t": t_br,
                   "down_t": t_dn, "n_blocks": len(blocks),
                   "n_positive_blocks": int((blocks > 0).sum()) if len(blocks) else None,
                   **{k: st.get(k) for k in
                      ("up_excess_vs_market", "avg_down_return", "hit_rate_up",
                       "hit_rate_down", "cost_share")}}
            rows.append(row)
            r = row
            def num(v, spec="{:.3f}", suffix=""):
                return "—" if v is None else format(v, spec) + suffix
            print(f"  {label:14s} K={every:<3d} "
                  f"涨腿超额={num(None if r['up_excess_vs_market'] is None else r['up_excess_vs_market']*100, '+.4f', '%')}/日 "
                  f"t={num(r['excess_t'], '+.2f')}({r['n_obs']}块) "
                  f"正块={r['n_positive_blocks']}/{r['n_blocks']} "
                  f"广度t={num(r['breadth_t'], '+.2f')} 跌腿t={num(r['down_t'], '+.2f')} "
                  f"跌腿={num(None if r['avg_down_return'] is None else r['avg_down_return']*100, '+.4f', '%')}/日 "
                  f"成本={num(None if r['cost_share'] is None else r['cost_share']*100, '.1f', '%')}")
    # 门槛必须按**各行自己的块数**给（`bonferroni_t` 的 df 无默认值，
    # 就是为了不让调用方假装知道样本量）。
    #
    # 原来这里印的是 `bonferroni_t(97)`，即 df=∞ 的 3.47——2026-10-14 那次
    # 假阳性晋级用的就是它：`ic_guided_race_7y.json` 放行了 t=4.14 @ 11 块
    # 的 K=63 候选，而 11 块的真门槛是 5.03。把渐近值单独立一行、紧挨着
    # 一列 n_obs 从 11 到 35 的 t 值，读者只会拿它当统一门槛用。
    for n_obs in sorted({r["n_obs"] for r in rows if r.get("n_obs")}):
        crit = bonferroni_t(N_COMPARISONS, df=n_obs - 1)
        print(f"  Bonferroni 临界（97 次比较，{n_obs} 个不重叠块）= {crit:.2f}")
    print(f"  参考：df=∞ 的渐近极限是 {bonferroni_t(N_COMPARISONS, df=10 ** 9):.2f}——"
          f"只在上表样本量足够大时才贴近真门槛；6 组前瞻门槛另有口径")


    out = {"kind": "universe_forensics",
           "window": [args.start, end],
           "test_dates": [test_dates[0], test_dates[-1]],
           "dropped_profile": dist,
           "yearly_coverage": yearly,
           "candidates": rows,
           "elapsed_s": round(time.time() - t0)}
    path = reports / args.out
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8")
    print(f"\n→ {path}  总耗时 {time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

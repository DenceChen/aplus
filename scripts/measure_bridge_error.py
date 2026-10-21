"""**实测桥的误差** —— `IC → 收益` 那座桥，到底准不准。

## 这份脚本要回答的唯一问题

判决侧已经过了：`scripts/verdict_composite.py` 报 0/6 不及格。
判决打印的「年毛超额」却不是回测出来的，是**桥算出来的**：

    年毛超额 = implied_spread(组合IC, σ_r) × 244

桥里有**四个假设**，每个都比现实乐观：

| # | 假设 | 若错，方向 |
|---|---|---|
| 1 | 联合高斯：分数→收益是**线性**的 | 高斯**低估重尾** → 真实价差**更小** |
| 2 | 尾部可成交：top/bottom 真能按均价成交 | 涨跌停/流动性 → **更小** |
| 3 | 日频口径：日价差 × 244 就是年化 | 月频调仓吃到的是**陈旧排序** → **更小** |
| 4 | **价差按分位宽度线性缩放** | IC 住在中段而尾巴为零 → **更小** |

> 四个假设**方向全部一致**，所以它们不会互相抵消，只会**叠加**。
> 桥给出的年化是**上界**，不是估计值。

## 为什么要扫多个分位宽度（这是本脚本最要紧的设计）

判决的 IC 是**全截面平均相关**。若分数→收益是线性的，
那么 20% 分位价差就代表了一切。但**线性正是假设 1 本身**。

而策略**实际买 top-10 / ~4600 ≈ 0.2% 分位**。
IC 完全可能**住在中段**——那样 20% 那一档是好消息，
0.2% 那一档就是坏消息，而两者都是真的。

> **只测一个宽度然后拿它当答案 = 用池子深浅掩盖信号深浅。**
> 所以默认扫 `0.2 / 0.05 / 0.01 / 0.002` 四档，
> 并直接打印「最深一档 / 最宽一档」的倍数。

## 本脚本怎么测

**不另起炉灶**：权重直接取判决脚本用的那一份
（`ic_matrices.parquet` + `walk_forward` 逐年滚动），
横截面口径、走 `extract_ic_matrices.py` 同一个 `build_cross_sections`。
差一点点就不可比，而「差一点点」正是本项目今天栽过四次的地方。

两个窗口各测一遍：

- `lag1_real`（D+1 买 → D+2 卖）—— 与判决的 IC **同一口径**，
  直接回答「桥这个函数准不准」
- `hold21`（D+1 买 → D+22 卖）—— **这才是月频策略真正吃到的东西**，
  乘 244/21 才是判决该用的年化

## 为什么必须报非重叠样本的 t

`hold21` 相邻两天重叠 20/21 天。若对**每天**一个样本求 t，
有效样本量被高估约 **21 倍**，t 会虚高 4.6 倍——
足以把一个纯噪声的策略报成「t=12 的强信号」。

所以 t **只在每 21 个交易日取一个的重叠无关子样本上**算。
均值不受影响（无偏），**只有 t 受影响**——而 t 正是判断「这是不是噪声」的唯一依据。

用法：
    .venv/bin/python scripts/measure_bridge_error.py \
        --matrices data/reports/ic_matrices.parquet \
        --out data/reports/bridge_error.json
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from aplus.backtest import ENTRY_LAG, build_cross_sections
from aplus.composite import rank_zscore, unpack, walk_forward
from aplus.factor import FACTOR_NAMES
from aplus.factor_ic import (FWD_WINDOWS, TAIL_Q, build_price_cache,
                             forward_returns)
from aplus.ic_to_return import annualise, implied_spread
from aplus.report_ready import require_keys
from aplus.snapshot import load_factors_map
from aplus.store import LocalStore

#: 与 `extract_ic_matrices.py` 同一个：8 个因子要估 64 个相关，
#: 样本太小时 R 几乎全是噪声，分位桶也就没有代表性。
MIN_N = 200

#: 月频持有交易日数，与判决的 K=21 同一口径。
K = 21

_VERDICT = None


def _verdict():
    """延迟载入 —— 模块级常量必须在函数定义**之前**算出来，反之不行。"""
    global _VERDICT
    if _VERDICT is None:
        path = Path(__file__).resolve().parent / "verdict_composite.py"
        spec = importlib.util.spec_from_file_location("_verdict_composite", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _VERDICT = mod
    return _VERDICT


def bucket_means(score: np.ndarray, ret: np.ndarray, tail_q: float,
                 ) -> tuple[float | None, float | None, float | None, int]:
    """切出最高/最低 `tail_q` 两桶，返回 `(top 均值, bottom 均值, 价差, n)`。

    **分桶只有这一个实现。** 并列用 `kind="stable"` 稳定排序切开
    ——**并列不给权重差别**，否则同分的票会被算进一半桶里，
    价差凭空多出一截。

    为什么多返回 `top` 与 `bottom` 而不只是它们的差：差值是
    **多空**口径，账户能做的是**多头**口径，两个不是同一个量。
    只留差值就等于把多空结果直接交给读者当收益用。
    """
    n = len(score)
    k = int(n * tail_q)
    if k < 1 or n < 3:
        return None, None, None, n
    order = np.argsort(score, kind="stable")
    lo, hi = order[:k], order[-k:]
    top, bot = float(ret[hi].mean()), float(ret[lo].mean())
    return top, bot, top - bot, n


def quintile_spread(score: np.ndarray, ret: np.ndarray,
                    tail_q: float = TAIL_Q) -> tuple[float | None, int]:
    """最高 `tail_q` 与最低 `tail_q` 的均值收益之差。"""
    _, _, spread, n = bucket_means(score, ret, tail_q)
    return spread, n


def measured_kurtosis(ret: np.ndarray) -> float:
    """横截面收益的**超额峰度**（正态 = 0）。

    桥里 `spread = ρ_p·σ_r·K(q)` 假设联合高斯；
    这个数就是那个假设的直接读数。**> 0 即重尾。**
    """
    if len(ret) < 4:
        return float("nan")
    x = ret - ret.mean()
    s = float(np.sqrt((x * x).mean()))
    if s <= 0:
        return float("nan")
    return float((x ** 4).mean() / s ** 4 - 3.0)


def weights_by_year(df: pd.DataFrame, factors: list[str]):
    """**判决用的那一份逐年权重** —— 单一真相源就在这里。

    刻意调用 `walk_forward` 而不是自己重算：自己重算等于
    「在测量台旁边另造一台测量仪」，两台之间的差永远查不出来。
    """
    rows = walk_forward(df, factors,
                        need_ic=_verdict().monthly_ic_threshold()[1],
                        judged_from=2021, equal_weight_years=(2019, 2020))
    return {r["year"]: r for r in rows if r.get("weights") is not None}


def measure(dates, xs, cache, factors, wmap, windows, tail_qs,
            min_n: int = MIN_N, verbose_every: int = 200):
    """逐日：定权重 → 打分 → 分桶 → 测价差。返回长表。

    ## 每个窗口都要扫全部分位宽度

    20% 分位的价差**不能**代表策略真正交易的地方：策略买
    top-10 / ~4600 ≈ **0.2% 分位**。只测一个宽度然后拿它当答案，
    是**用池子深浅掩盖信号深浅**。

    ## 🚨 而且必须**同时**记「多空价差」与「多头减市场」

    这两个是**不同的量**，我今天把它们当成同一个量比过一次：

    | | 定义 | 市场暴露 | A 股能不能做 |
    |---|---|---|---|
    | `spread` | top − bottom | ≈ 0（β≈0） | **不能**（散户基本融不到券） |
    | `long_vs_mkt` | top − 市场 | β≈1 | **能**（这就是账户真实收益） |

    实测 top-9：多空价差 108.34%/年，而**多头减市场只有 6.78%/年**——
    **差 16 倍**。若只报前者，读者会以为「策略能赚 100%」。

    ⚠️ **6.78% 带口径**：那是 **K=21 · 等权判决档**的历史读数
    （不是现役交付口径的数；现役实盘档见 `deployed_d2_verdict.json::verdict`
    ⇒ **+10.4245%/年**，缺口 9.5755pp）。
    ⇒ ⇒ 引用 6.78% 时**必须带这句口径**（第 143 层：口径被更正后，
    引用它的每一处都要留下标记 ⇒ 否则读者会拿旧口径的数当现役结论）。

    > 所以两个口径**必须成对出现**。只报一个的脚本必然被误读，
    > 而误读方向**永远是朝乐观**。
    """
    market = xs.get("_market") or {}
    records = []
    skipped = []
    t0 = time.time()

    for i, day in enumerate(dates):
        year = int(str(day)[:4])
        rec_w = wmap.get(year)
        if rec_w is None:
            skipped.append({"date": day, "reason": "该年无权重"})
            continue
        w = np.asarray([rec_w["weights"][f] for f in factors], float)

        universe = xs.get(day)
        if universe is None or universe.empty:
            continue
        u_idx = universe.set_index("code")
        F = u_idx[factors].to_numpy(float)
        ok = np.isfinite(F).all(axis=1)
        if int(ok.sum()) < min_n:
            skipped.append({"date": day, "reason": "因子缺失", "n": int(ok.sum())})
            continue
        # 分数：**秩标准化因子**的加权和 —— 与判决的组合 IC 同一个表达式，
        # 所以这里的排序就是判决认定的那个排序。
        score = rank_zscore(F[ok]) @ w

        row = {"date": day, "year": year, "n": int(ok.sum())}
        for label, (enter, exit_off) in windows.items():
            fwd = forward_returns(cache, list(u_idx.index), day, enter, exit_off)
            r = fwd.reindex(u_idx.index).to_numpy(float)[ok]
            m = np.isfinite(r)
            if int(m.sum()) < min_n:
                for q in tail_qs:
                    row[f"spread_{label}_q{q:g}"] = None
                    row[f"top_{label}_q{q:g}"] = None
                    row[f"bot_{label}_q{q:g}"] = None
                    row[f"long_vs_mkt_{label}_q{q:g}"] = None
                row[f"kurt_{label}"] = None
                row[f"sd_{label}"] = None
                continue
            s, r2 = score[m], r[m]
            mkt = float(market.get(day, float("nan")))
            row[f"mkt_{label}"] = mkt
            for q in tail_qs:
                # 刻意**调用** `bucket_means` 而不是把分桶逻辑再抄一遍：
                # 抄一遍就等于造了第二台测量仪，两台之间的差永远查不出来。
                top, bot, sp, _ = bucket_means(s, r2, tail_q=q)
                row[f"top_{label}_q{q:g}"] = top
                row[f"bot_{label}_q{q:g}"] = bot
                row[f"spread_{label}_q{q:g}"] = sp
                row[f"long_vs_mkt_{label}_q{q:g}"] = (
                    None if top is None or not np.isfinite(mkt) else top - mkt)
            row[f"kurt_{label}"] = measured_kurtosis(r2)
            row[f"sd_{label}"] = float(r2.std(ddof=1))
        records.append(row)

        if verbose_every and (i + 1) % verbose_every == 0:
            print(f"  {i + 1}/{len(dates)} 日  记 {len(records)}  "
                  f"跳 {len(skipped)}  {time.time() - t0:.0f}s", flush=True)

    return pd.DataFrame(records), pd.DataFrame(skipped)


def _t_stat(x: np.ndarray) -> float | None:
    """重叠**无关**样本上的 t。少于 3 个独立观测不给 t。"""
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    if len(x) < 3 or x.std(ddof=1) <= 0:
        return None
    return float(x.mean() / x.std(ddof=1) * math.sqrt(len(x)))


def non_overlapping(df: pd.DataFrame, col: str, k: int = K):
    """每 `k` 个交易日取一个 —— `hold21` 相邻窗口重叠 21−1 天。"""
    s = df[col].to_numpy(float)
    return s[::k]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--matrices", type=Path,
                    default=Path("data/reports/ic_matrices.parquet"))
    ap.add_argument("--factors", default="")
    ap.add_argument("--start", default="2021-01-01")
    ap.add_argument("--end", default="")
    ap.add_argument("--data-root", type=Path, default=Path("data"))
    ap.add_argument("--out", type=Path, default=Path("data/reports/bridge_error.json"))
    ap.add_argument("--sigma-r", type=float, default=0.02656)
    ap.add_argument("--tail-qs", default="0.2,0.05,0.01,0.002",
                    help="要扫的分位宽度，逗号分隔。"
                         "0.002 ≈ top-9/~4600 —— 策略真正交易的那一档")
    args = ap.parse_args()
    tail_qs = [float(x) for x in args.tail_qs.split(",") if x.strip()]
    for q in tail_qs:
        if not 0 < q < 0.5:
            raise SystemExit(f"分位宽度 {q} 不在 (0, 0.5) 内")

    if not args.matrices.is_file():
        raise SystemExit(f"抽取表不存在：{args.matrices}")
    factors = [f.strip() for f in args.factors.split(",") if f.strip()] \
        or list(FACTOR_NAMES)
    windows = {"lag1": FWD_WINDOWS["lag1_real"], "hold21": FWD_WINDOWS["hold21"]}

    df = pd.read_parquet(args.matrices)
    _, _, sf = unpack(df, factors)
    wmap = weights_by_year(df, factors)
    years = sorted(wmap)
    print(f"权重来自判决同一份抽取表：{years[0]} ~ {years[-1]}，"
          f"{len(factors)} 个因子")
    for y in years:
        wr = wmap[y]
        top = sorted(wr["weights"].items(), key=lambda kv: -abs(kv[1]))[:3]
        print(f"  {y}  {'{'}src={wr.get('weights_source')}{'}'}"
              f"  权重最大的三个：" + "、".join(f"{k}{v:+.3f}" for k, v in top))

    store = LocalStore(args.data_root)
    files = sorted((store.root / "master" / "daily").glob("*.parquet"))
    if not files:
        print("主库为空")
        return 1
    end = args.end or str(pd.read_parquet(files[0])["date"].max())

    t0 = time.time()
    print(f"\n载入日线 {args.start} ~ {end} …", flush=True)
    daily_map = {p.stem: store.read_daily(p.stem) for p in files}
    factors_map = load_factors_map(store)
    cache = build_price_cache(daily_map, factors_map)
    print(f"日线 {len(daily_map)} 只 / 价格缓存 {len(cache)} 只 "
          f"（{time.time() - t0:.0f}s）", flush=True)

    xs = build_cross_sections(store, args.start, end, daily_map=daily_map,
                              factors_map=factors_map, entry_lag=ENTRY_LAG)
    dates = xs["_dates"]
    print(f"横截面 {len(dates)} 个交易日（{time.time() - t0:.0f}s）\n", flush=True)

    long_df, skipped = measure(dates, xs, cache, factors, wmap, windows, tail_qs)

    # ── 判决那一侧的数：同一个组合 IC，走桥 ────────────────────────
    need, ic_need = _verdict().monthly_ic_threshold()

    # ★ 主表改口径：**hold21 × 每个分位宽度**。
    # 只有 20% 那一列的话，池子最深的那一档会替尾巴说话。
    print(f"\n{'年':<6}{'n':>6}{'组合IC':>10}{'桥:年毛超额':>13}"
          + "".join(f"{'多头/年 q=' + format(q, 'g'):>15}"
                     f"{'多头-市场/年':>14}" for q in tail_qs))
    print("─" * (37 + 29 * len(tail_qs)))
    table = []
    for y in sorted(set(long_df["year"])):
        yr = long_df[long_df["year"] == y]
        ic_y = wmap[y].get("ic_mean")
        bridge_day = (implied_spread(ic_y, sigma_r=args.sigma_r, kind="spearman")
                      if ic_y is not None else float("nan"))

        cells = ""
        per_q, per_lv, per_top, per_bot = {}, {}, {}, {}
        for q in tail_qs:
            key = format(q, "g")
            per_q[q] = float(np.nanmean(yr[f"spread_hold21_q{key}"])) if len(yr) else float("nan")
            per_top[q] = float(np.nanmean(yr[f"top_hold21_q{key}"])) if len(yr) else float("nan")
            per_bot[q] = float(np.nanmean(yr[f"bot_hold21_q{key}"])) if len(yr) else float("nan")
            per_lv[q] = (float(np.nanmean(yr[f"long_vs_mkt_hold21_q{key}"]))
                        if len(yr) else float("nan"))
            cells += (f"{per_top[q] * (244.0 / K):>13.2%}"
                      f"{per_lv[q] * (244.0 / K):>14.2%}")
        print(f"{y:<6}{len(yr):>6}{ic_y:>10.5f}{annualise(bridge_day):>13.2%}" + cells)

        table.append({
            "year": y, "n_days": int(len(yr)),
            "composite_ic": ic_y,
            "bridge_daily_spread": bridge_day,
            "bridge_annual_gross": annualise(bridge_day),
            # 「多空」口径：top − bottom，β≈0，**A 股散户基本做不了**
            "measured_hold21_daily_by_q": {format(q, "g"): per_q[q] for q in tail_qs},
            "measured_hold21_annual_by_q": {
                format(q, "g"): per_q[q] * (244.0 / K) for q in tail_qs},
            # 「多头减市场」口径：账户真正拿得到的那个量
            "measured_long_vs_mkt_daily_by_q": {format(q, "g"): per_lv[q] for q in tail_qs},
            "measured_long_vs_mkt_annual_by_q": {
                format(q, "g"): per_lv[q] * (244.0 / K) for q in tail_qs},
            "measured_long_vs_mkt_t_no_overlap": {
                format(q, "g"): _t_stat(non_overlapping(yr, f"long_vs_mkt_hold21_q{format(q, 'g')}",
                                                         k=K))
                for q in tail_qs},
            "measured_top_bucket_annual_by_q": {
                format(q, "g"): per_top[q] * (244.0 / K) for q in tail_qs},
            "measured_bottom_bucket_annual_by_q": {
                format(q, "g"): per_bot[q] * (244.0 / K) for q in tail_qs},
            "measured_hold21_t_no_overlap": {
                format(q, "g"): _t_stat(non_overlapping(yr, f"spread_hold21_q{format(q, 'g')}",
                                                         k=K))
                for q in tail_qs},
            "n_universe": int(np.nanmedian(yr["n"])),
            "n_per_bucket": {format(q, "g"): max(1, int(np.nanmedian(yr["n"]) * q))
                             for q in tail_qs},
            "kurtosis_hold21": float(np.nanmean(yr["kurt_hold21"])),
            "sigma_r_lag1_measured": float(np.nanmean(yr["sd_lag1"])),
            "weights_source": wmap[y].get("weights_source"),
        })

    res = {"kind": "bridge_error", "windows": {k: list(v) for k, v in windows.items()},
           "k": K, "tail_qs": tail_qs, "factors": factors,
           "sigma_r_used_by_bridge": args.sigma_r,
           "hurdle_gross": need, "ic_need": ic_need,
           "note": ("t 一律在每 21 个交易日取一个的重叠无关子样本上计算；"
                    "对全部交易日求 t 会把有效样本高估约 21 倍。"
                    "分位宽度多扫几档是因为策略只买 top-10，"
                    "20% 那一档不代表策略真正吃到的价差。"
                    "⚠️ 同一行里有**两个口径**：`measured_hold21_*` 是"
                    "**多空价差（顶-底）**，β≈0 且 A 股散户做不了；"
                    "`measured_long_vs_mkt_*` 是**多头减市场**，"
                    "那才是账户能拿到的量。**引用时必须带口径标签**——"
                    "只报前者会被读成「能赚 100%/年」。"),
           "years": table}

    if table:
        print(f"\n{'口径':<26}{'年均':>10}{'最低':>10}{'最高':>10}{'门槛倍数':>11}")
        print("─" * 67)
        bl = np.mean([r["bridge_annual_gross"] for r in table])
        print(f"{'桥·年毛超额（修正后常数）':<24}{bl:>10.2%}"
              f"{np.min([r['bridge_annual_gross'] for r in table]):>10.2%}"
              f"{np.max([r['bridge_annual_gross'] for r in table]):>10.2%}"
              f"{bl / need:>10.2f}×")
        for q in tail_qs:
            key = format(q, "g")
            sp = [r["measured_hold21_annual_by_q"][key] for r in table]
            lv = [r["measured_long_vs_mkt_annual_by_q"][key] for r in table]
            ts_ls = [t for t in (r["measured_hold21_t_no_overlap"][key]
                                 for r in table) if t is not None]
            ts_lv = [t for t in (r["measured_long_vs_mkt_t_no_overlap"][key]
                                 for r in table) if t is not None]
            tag = f"q={key}"
            if len(tail_qs) > 1:
                tag += f" top{int(4600 * q)}"
            print(f"  {'多空 顶-底':<22}{np.mean(sp):>10.2%}{np.min(sp):>10.2%}"
                  f"{np.max(sp):>10.2%}{np.mean(sp) / need:>10.2f}×"
                  + (f"  t {np.mean(ts_ls):>5.2f}" if ts_ls else ""))
            print(f"  {'多头 - 市场':<24}{np.mean(lv):>10.2%}{np.min(lv):>10.2%}"
                  f"{np.max(lv):>10.2%}{np.mean(lv) / need:>10.2f}×"
                  + (f"  t {np.mean(ts_lv):>5.2f}" if ts_lv else ""))
            if abs(np.mean(sp)) > 1e-12:
                print(f"  {'└ 两口径之比':<24}{np.mean(sp) / np.mean(lv):>9.1f}×"
                      f" ← 多空比多头大这么多，**差的那部分是空头腿**")

        print(f"\n门槛（年毛超额）{need:.2%}；年调仓 {244 / K:.1f} 次；"
              f"超额峰度 年均 {np.mean([r['kurtosis_hold21'] for r in table]):.2f}")
        if len(tail_qs) > 1:
            deep = format(tail_qs[-1], "g")
            dm = np.mean([r["measured_hold21_annual_by_q"][deep] for r in table])
            sm = np.mean([r["measured_hold21_annual_by_q"][format(tail_qs[0], "g")]
                          for r in table])
            print(f"★【多空口径】最深一档 / 最宽一档 = {dm / sm:.2f}×"
                  f"（< 1 说明信号住在中段，池子越深钱越少）")
            dlv = np.mean([r["measured_long_vs_mkt_annual_by_q"][deep] for r in table])
            slv = np.mean([r["measured_long_vs_mkt_annual_by_q"][format(tail_qs[0], "g")]
                           for r in table])
            print(f"★【多头口径】最深一档 / 最宽一档 = "
                  f"{dlv / slv:.2f}×" if abs(slv) > 1e-12 else "")
            print("  → 两个口径**可以给出相反的深度结论**，"
                  "所以引用时必须带口径标签。")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(res, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    long_df.to_parquet(args.out.with_suffix(".long.parquet"), index=False)

    # ── 自查：写完之后，用**产物指纹**确认本轮该有的列真的在 ──────
    #
    # 不靠退出码、不靠「进程还活着」—— 2026-10-14 踩过：
    # `nohup … &` 配 `run_in_background` 会**立刻**返回 exit 0，
    # 而 python 还在跑。于是我差点把**上一轮**的报告读成本轮结论
    # （旧文件里连 `measured_long_vs_mkt_*` 都没有）。
    ready = require_keys(args.out, ["years.0.measured_long_vs_mkt_annual_by_q"])
    print(f"\n产物自检：{ready.reason}")
    if not ready.ok:
        raise SystemExit(f"产物不完整，后续任何引用都不可信：{ready.reason}")
    print(f"\n→ {args.out}")
    print(f"→ {args.out.with_suffix('.long.parquet')}")
    print(f"跳过：{skipped['reason'].value_counts().to_dict() if len(skipped) else '（无）'}")
    print(f"总耗时 {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
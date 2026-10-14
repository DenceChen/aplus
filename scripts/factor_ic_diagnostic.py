"""逐因子 IC 诊断：这些因子在 A 股到底有没有预测力。

## 为什么在赛马之外还要做这件事

赛马回答的是「哪组权重最好」，IC 回答的是「**因子本身有没有用**」。
两者会给出互相矛盾的结论，而矛盾本身是最有价值的信息：

- 若 8 个因子的 IC 全部不显著 → 权重怎么搭都是噪声，赛马只是
  在 84 组噪声里排序。**此时继续调权重是纯粹的浪费**，
  该做的是加因子 / 换数据源 / 承认目标不可达。
- 若某因子 `all` 显著但 `tail_high`/`tail_low` 为零 → 它解释了收益，
  但不解释极端收益。策略只买 top-10，**这样的因子对它没用**。
- 若 `lag0_leaky` 显著而 `lag1_real` 消失 → 过去的正收益是
  一根 K 线的前视，不是 alpha。这条同时是 `ENTRY_LAG=1` 的事后验证。

## 口径（与回测逐位一致，有测试钉死）

- 因子取 `build_cross_sections` 的输出 —— 策略看到什么就量什么
- 真实窗口 = D+1 收盘买 → D+2 收盘卖（`ENTRY_LAG=1`）
- 只在可成交样本上算（停牌 / 非正价格 / 超 ±40% 剔除）
- t 值**按持有期分块**：hold21 的相邻 IC 共享 20 天数据，
  逐日 t 会把一次决策数 21 遍

用法：
    .venv/bin/python scripts/factor_ic_diagnostic.py \
        --start 2019-01-02 --data-root /tmp/aplus_snap_1012 \
        --out factor_ic_7y.json
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from aplus.backtest import ENTRY_LAG, build_cross_sections
from aplus.evolve_race import _block_means, _tstat
from aplus.factor import FACTOR_NAMES
from aplus.factor_ic import (FWD_WINDOWS, MIN_OBS, build_price_cache,
                             daily_ic_ranked, forward_returns,
                             make_ranked_forward, window_block)
from aplus.snapshot import load_factors_map
from aplus.store import LocalStore


def scan(dates, xs, cache, factors, windows, verbose_every=200):
    """逐日逐窗口逐因子算 IC，返回扁平记录列表。"""
    rows = []
    t0 = time.time()
    for i, day in enumerate(dates):
        universe = xs.get(day)
        if universe is None or universe.empty:
            continue
        u_idx = universe.set_index("code")
        codes = list(u_idx.index)
        for window in windows:
            enter, exit_off = FWD_WINDOWS[window]
            fwd = forward_returns(cache, codes, day, enter, exit_off)
            rf = make_ranked_forward(u_idx, fwd)
            if rf is None:
                continue
            for factor in factors:
                ic = daily_ic_ranked(u_idx, rf, factor)
                if ic["all"] is None:
                    continue
                rows.append({"date": day, "factor": factor, "window": window,
                             "all": ic["all"], "tail_high": ic["tail_high"],
                             "tail_low": ic["tail_low"], "n": ic["n"]})
        if verbose_every and (i + 1) % verbose_every == 0:
            print(f"  {i + 1}/{len(dates)} 日  {time.time() - t0:.0f}s",
                  flush=True)
    return rows


def summarize(rows, factors, windows) -> list[dict]:
    """逐 (因子 × 窗口) 汇总：均值、分块 t、正 IC 占比。"""
    out = []
    for window in windows:
        block = window_block(window)
        for factor in factors:
            sel = [r for r in rows if r["factor"] == factor
                   and r["window"] == window and r.get("all") is not None]
            if len(sel) < MIN_OBS:
                out.append({"factor": factor, "window": window, "block": block,
                            "n_days": len(sel),
                            "note": "样本不足，不给结论" if sel else "无可用观测"})
                continue
            row = {"factor": factor, "window": window, "block": block,
                   "n_days": len(sel)}
            for field in ("all", "tail_high", "tail_low"):
                s = pd.Series([r[field] for r in sel
                               if r.get(field) is not None], dtype=float)
                if len(s) < MIN_OBS:
                    row[field] = None
                    row[f"{field}_t"] = None
                    row[f"{field}_hit"] = None
                    continue
                row[field] = round(float(s.mean()), 5)
                t, n_blk = _tstat(s, block=block)
                row[f"{field}_t"] = None if t is None else round(float(t), 3)
                row[f"{field}_n_blocks"] = n_blk
                row[f"{field}_hit"] = round(float((s > 0).mean()), 4)
            row["avg_n"] = round(float(pd.Series([r["n"] for r in sel]).mean()), 1)
            out.append(row)
    return out


def yearly(rows, factors, window) -> list[dict]:
    """逐年 IC —— 均值显著的因子，必须再看它是不是只靠某一年撑着。

    **三档必须齐全**（`all` / `tail_low` / `tail_high`），与全段表 `table` 同口径。

    > 第一版只报 `ic_mean`（即 `all`），而 `table` 出三档。
    > 于是报告里出现**两套口径**：全段表有低尾，逐年表没有。
    > 而整条月频路线恰恰压在低尾那一个数上——
    > 「26.7% 刚过 26.4% 门槛」背后是一个**看不到分布的单点估计**。
    > 全截面的逐年 IC 八年里 0 个年份够到月频门槛，
    > 所以「够得着」这件事**只有低尾能回答**，而它偏偏没被逐年统计。

    样本不足的档**如实缺失（None）**，绝不回落到 `all`：
    回落会让读者以为看到的是低尾，实际看到的是全截面——
    那正是本 bug 的形态，只是把「缺」换成了「假装有」。
    """
    out = []
    sel_all = [r for r in rows if r["window"] == window]
    if not sel_all:
        return out
    years = sorted({r["date"][:4] for r in sel_all})
    buckets = ("all", "tail_low", "tail_high")
    for factor in factors:
        for y in years:
            # 三档取自**同一批逐日记录**，保证互相可比
            yr = [r for r in sel_all
                  if r["factor"] == factor and r["date"][:4] == y]
            vals = [r["all"] for r in yr if r.get("all") is not None]
            if len(vals) < MIN_OBS:
                continue
            s = pd.Series(vals, dtype=float)
            row = {"factor": factor, "year": y, "n_days": len(vals),
                   "ic_mean": round(float(s.mean()), 5),
                   "hit": round(float((s > 0).mean()), 4)}
            for b in buckets:
                bv = [r[b] for r in yr if r.get(b) is not None]
                if len(bv) < MIN_OBS:
                    row[b] = None          # 如实缺失，不回落
                    continue
                sb = pd.Series(bv, dtype=float)
                row[b] = round(float(sb.mean()), 5)
                row[f"{b}_hit"] = round(float((sb > 0).mean()), 4)
            out.append(row)
    return out


def verdict(table, factors, window="lag1_real"):
    """给一句能直接写进结论的话 —— 不是「有信号」，而是「哪个因子、在哪、多强」。

    阈值按 8 个因子 × 2 个尾部 = 16 次比较做 Bonferroni：
    逐日 t 的临界 2.16（z≈0.975），校正后 2.16 + ln(16)/2 ≈ 3.40。
    """
    crit = 3.40
    hit = []
    for row in table:
        if row.get("window") != window:
            continue
        if row.get("all_t") is None:
            continue
        if row["all_t"] > crit:
            hit.append((row["factor"], "全截面", row["all"], row["all_t"]))
        if (row.get("tail_high_t") or 0) > crit:
            hit.append((row["factor"], "高尾", row["tail_high"], row["tail_high_t"]))
        if (row.get("tail_low_t") or 0) > crit:
            hit.append((row["factor"], "低尾", row["tail_low"], row["tail_low_t"]))
    return hit, crit


def _f(v, spec: str = "+.4f", width: int = 9) -> str:
    """`None` → 占位破折号。其余按格式输出。

    IC 表里 `None` 有两种完全不同的含义 —— 「样本不足」与「全并列」，
    两者都必须显示成破折号而不是 0.0000：0 会被读成「测了，没信号」。
    """
    if v is None:
        return "—".rjust(width)
    return format(v, spec).rjust(width)


def print_table(table, windows) -> None:
    for window in windows:
        block = window_block(window)
        print(f"\n=== 窗口 {window}（独立观测单位 {block} 日） ===")
        print(f"{'因子':<12}{'全截面IC':>10}{'t':>8}{'块数':>7}{'正IC占比':>9}"
              f"{'高尾IC':>9}{'高尾t':>8}{'低尾IC':>9}{'低尾t':>8}")
        print("─" * 90)
        for row in table:
            if row.get("window") != window:
                continue
            if "note" in row:
                print(f"{row['factor']:<12}{row['note']:>43}")
                continue
            hit = row.get("all_hit")
            print(f"{row['factor']:<12}"
                  f"{_f(row.get('all'), '+.4f', 10)}"
                  f"{_f(row.get('all_t'), '+.2f', 8)}"
                  f"{row.get('all_n_blocks', 0):>7}"
                  f"{_f(None if hit is None else hit * 100, '.1f', 9) + ('' if hit is None else '%')}"
                  f"{_f(row.get('tail_high'), '+.4f', 9)}"
                  f"{_f(row.get('tail_high_t'), '+.2f', 8)}"
                  f"{_f(row.get('tail_low'), '+.4f', 9)}"
                  f"{_f(row.get('tail_low_t'), '+.2f', 8)}")


def print_yearly(rows, factors, window, start, end) -> None:
    ytab = yearly(rows, factors, window)
    yrs = sorted({r["year"] for r in ytab})
    print(f"\n=== 逐年全截面 IC（{start} ~ {end}，窗口 {window}） ===")
    if not yrs:
        print("（逐年样本不足，不给表）")
        return
    print(f"{'因子':<12}" + "".join(f"{y:>10}" for y in yrs))
    print("─" * (12 + 10 * len(yrs)))
    for factor in factors:
        cells = []
        for y in yrs:
            hit = next((r for r in ytab
                        if r["factor"] == factor and r["year"] == y), None)
            cells.append(f"{hit['ic_mean']:>10.4f}" if hit else f"{'—':>10}")
        print(f"{factor:<12}" + "".join(cells))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2019-01-02")
    ap.add_argument("--end", default="")
    ap.add_argument("--data-root", type=Path, default=Path("/tmp/aplus_snap_1012"))
    ap.add_argument("--out", default="factor_ic.json")
    ap.add_argument("--factors", default="", help="逗号分隔，默认全部 8 个")
    ap.add_argument("--windows", default="lag1_real,lag0_leaky,hold4,hold21")
    args = ap.parse_args()

    factors = [f.strip() for f in args.factors.split(",") if f.strip()] \
        or list(FACTOR_NAMES)
    windows = [w.strip() for w in args.windows.split(",") if w.strip()]
    bad = [w for w in windows if w not in FWD_WINDOWS]
    if bad:
        raise SystemExit(f"未知窗口 {bad}，可选：{list(FWD_WINDOWS)}")

    data_root = Path(args.data_root)
    if not data_root.is_dir():
        raise SystemExit(f"数据根目录不存在：{data_root}")
    store = LocalStore(data_root)
    reports = data_root / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    files = sorted((store.root / "master" / "daily").glob("*.parquet"))
    if not files:
        print("主库为空")
        return 1
    end = args.end or str(pd.read_parquet(files[0])["date"].max())

    t0 = time.time()
    print(f"载入日线 {args.start} ~ {end} …", flush=True)
    daily_map = {p.stem: store.read_daily(p.stem) for p in files}
    factors_map = load_factors_map(store)
    cache = build_price_cache(daily_map, factors_map)
    print(f"日线 {len(daily_map)} 只 / 价格缓存 {len(cache)} 只 "
          f"（{time.time() - t0:.0f}s）", flush=True)

    xs = build_cross_sections(store, args.start, end, daily_map=daily_map,
                              factors_map=factors_map, entry_lag=ENTRY_LAG)
    dates = xs["_dates"]
    print(f"横截面 {len(dates)} 个交易日（{time.time() - t0:.0f}s）\n", flush=True)

    rows = scan(dates, xs, cache, factors, windows)
    print(f"IC 观测 {len(rows)} 条（{time.time() - t0:.0f}s）\n", flush=True)

    table = summarize(rows, factors, windows)
    crit_hit, crit = verdict(table, factors)

    print_table(table, windows)
    ytab = yearly(rows, factors, "lag1_real")
    print_yearly(rows, factors, "lag1_real", args.start, end)

    print(f"\n=== 结论（窗口 lag1_real，Bonferroni 校正 {len(factors) * 2} 次比较，临界 {crit}） ===")
    if not crit_hit:
        print("没有任何因子在全截面或尾部通过显著性闸。")
        print("含义：权重怎么搭都只是在噪声里排序，赛马的胜负不具备可下注的含义。")
    else:
        for factor, seg, ic, t in crit_hit:
            print(f"  {factor:<12} {seg:<8} IC={ic:+.4f}  t={t:+.2f}")

    out = {"kind": "factor_ic", "window": [args.start, end],
           "entry_lag": ENTRY_LAG, "factors": factors, "windows": windows,
           "fwd_windows": {k: list(v) for k, v in FWD_WINDOWS.items()
                           if k in windows},
           "tail_q": 0.20, "bonferroni_crit": crit,
           "n_rows": len(rows), "n_dates": len(dates),
           "table": table, "yearly": ytab,
           "passed": [{"factor": f, "segment": s, "ic": i, "t": t}
                      for f, s, i, t in crit_hit],
           "elapsed_s": round(time.time() - t0)}
    path = reports / args.out
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8")
    print(f"\n→ {path}  总耗时 {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
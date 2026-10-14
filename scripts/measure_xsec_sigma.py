"""实测 σ_r（横截面收益标准差）——`aplus/ic_to_return.py` 里唯一未实测的输入。

## 为什么非做不可

    毛超额 = ρ_p · σ_r · K(q) · 244

`ρ_p` 已实测、`K(q)` 是解析常数、244 是常数，**只有 σ_r 是估的**。
而它单独决定月频结论的真伪：

    σ_r = 0.022 → 25.5%/年  →  够不到月频门槛 26.4%
    σ_r = 0.023 → 26.7%/年  →  刚好越过

余量 1.2% 小于输入的估计误差——「刚好越过」是噪声，不是结论。
（详见 `docs/TODO_measure_sigma_r.md`，第 63 条失误记录。）

## 为什么口径写在脚本里，不写进库

`aplus/xsec_sigma.py` 只提供「拿到一组当日收益就算 σ」的纯函数，
**不读面板、不出默认值**。因为**口径会随研究变化，而公式不该跟着变**：
今天用「可成交池 ∩ 上市≥250日 ∩ 非ST」，明天可能要看全市场；
口径一变就该只改这个脚本，库函数一个字节都不动。

## 池子：必须与 IC 报告同总体

`ρ_p` 是用 `avg_n ≈ 4368.5` 只的横截面算出来的。
σ_r 若换成另一个池子，两者**就不是同一个总体**——
而 IC→收益换算要求它们配对。

## 极值：涨跌停会在尾部堆质量

原始与 1%/99% 缩尾**两版都要报**。
注意缩尾**可能静默失效**（尾部堆超过缩尾线时 clip 退化成恒等变换），
所以额外报出 `n_days_winsor_noop`——
**两版相等的天数**，这个数非零就说明那几天缩尾没起作用。

用法：
    .venv/bin/python scripts/measure_xsec_sigma.py \
        --data-root /tmp/aplus_snap_1012 --out xsec_sigma_r.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from aplus.xsec_sigma import MIN_OBS, cross_sectional_sigma, summarise

#: 次新股过滤：上市不足这个天数的历史不足以形成有效的 20 日动量。
MIN_LISTED_DAYS = 250

#: 缩尾档。与 `aplus/factor.py:WINSOR_Q` 同源，但**独立声明**——
#: 那个用于因子值，这个用于收益率，口径不同则各自演进。
WINSOR = (0.01, 0.99)

#: 构池必需的列。**缺任何一列的股票整个剔除并报出**——
#: 静默让它掉出池子，会让 `avg_n` 与 IC 报告对不上而没人察觉。
#: `code` 也在内：上市天数要按股票分组，没有它构不了池。
REQUIRED_COLS = ("date", "code", "tradestatus", "isST", "pctChg")


def load_panel(root: Path, verbose_every: int = 1000) -> tuple[pd.DataFrame, dict]:
    """把全市场日线拼成**一张长表**，顺带记录 schema 分歧。

    为什么用长表而不是逐股 DataFrame：池子是**按日期切片**的，
    长表一次 `groupby(date)` 就能拿到所有横截面；
    逐股 DataFrame 则要 1882 天 × 5460 只 = 一千万次扫描。

    schema 分歧必须**报出而不是吸收**——
    实测（2026-10-14）三个快照里都有 2 只股票（`000043.SZ`、`300114.SZ`）
    的 parquet 截断在 `volume` 列，缺 `tradestatus` / `isST` / `pctChg`。
    若不报出，它们会被池子悄悄吃掉，而结果看起来完全正常。
    """
    t0 = time.time()
    files = sorted((root / "master" / "daily").glob("*.parquet"))
    if not files:
        raise SystemExit(f"主库为空：{root / 'master' / 'daily'}")

    frames: list[pd.DataFrame] = []
    dropped: dict[str, list[str]] = {}
    for i, p in enumerate(files):
        cols = set(pq.read_schema(p).names)
        missing = [c for c in REQUIRED_COLS if c not in cols]
        if missing:
            dropped[p.stem] = missing
            continue
        frames.append(pd.read_parquet(
            p, columns=["date", "code", "tradestatus", "isST", "pctChg"]))
        if verbose_every and (i + 1) % verbose_every == 0:
            print(f"  读入 {i + 1}/{len(files)}（{time.time() - t0:.0f}s）",
                  flush=True)

    if not frames:
        raise SystemExit("没有任何一只股票带齐必需列——快照本身有问题")

    panel = pd.concat(frames, ignore_index=True)
    meta = {
        "n_files": len(files),
        "n_files_used": len(frames),
        "dropped_missing_cols": dropped,
        "n_rows": int(len(panel)),
    }
    if dropped:
        print(f"  ⚠ {len(dropped)} 只股票缺列，整只剔除（下面会列名）：",
              flush=True)
        for code, miss in sorted(dropped.items())[:10]:
            print(f"      {code}  缺 {miss}", flush=True)
    return panel, meta


def build_pool(panel: pd.DataFrame, start: str, end: str,
               min_listed_days: int = MIN_LISTED_DAYS,
               verbose_every: int = 200) -> tuple[dict, dict]:
    """逐日构造可成交横截面，返回 `(逐日收益, 池子元信息)`。

    池子 = **可成交 ∩ 上市≥N日 ∩ 非ST**。三条各有理由：

    - **可成交**（`tradestatus == 1`）：停牌的当日收益缺失，
      混进去会把 σ 压低——停牌股**不产生收益分布**。
    - **上市≥250日**：次新股没有 20 日动量历史，
      因子本身就缺失（而不是为 0），留着会系统性稀释相关性。
    - **非ST**：ST 涨跌幅限制是 ±5%，与主板 ±10% 不同池，
      混算等于把两种不同的截断机制当成一种。

    > ⚠️ `tradestatus == 1` 的语义**由实测定夺，不由本脚本假定**。
    > `tradestatus_value_counts` 会把分布报出来：
    > 若 1 不是主流取值，这个过滤器就是**反向**的，
    > 而结果看起来完全正常。
    """
    t0 = time.time()
    panel = panel.sort_values(["date", "code"])
    panel = panel[(panel["date"] >= start)
                  & (panel["date"] <= (end or panel["date"].max()))]

    # 上市天数：按 (code, date) 去重后累计，shift(1) 给出「当日之前已上市天数」
    panel = panel.drop_duplicates(subset=["code", "date"])
    panel["prior_days"] = panel.groupby("code").cumcount()

    keep = ((panel["prior_days"] >= min_listed_days)
            & (panel["tradestatus"] == 1)
            & (panel["isST"] == 0))
    pool = panel[keep]
    # **百分比 → 比例**，差 100 倍，这是最容易错的一处
    pool = pool.assign(ret=pool["pctChg"] / 100.0)

    ts_counts = (panel["tradestatus"].value_counts()
                 .sort_values(ascending=False).to_dict())

    daily: dict[str, np.ndarray] = {}
    n_skipped_too_small = 0
    for i, (_date, grp) in enumerate(pool.groupby("date", sort=True)):
        arr = grp["ret"].to_numpy(dtype=float)
        arr = arr[np.isfinite(arr)]
        if arr.size < MIN_OBS:
            n_skipped_too_small += 1
        else:
            daily[_date] = arr
        if verbose_every and (i + 1) % verbose_every == 0:
            print(f"  构池 {i + 1} 日（{time.time() - t0:.0f}s）", flush=True)

    meta = {
        "min_listed_days": min_listed_days,
        "n_calendar_days": int(panel["date"].nunique()),
        "n_days_kept": len(daily),
        "n_days_too_small": n_skipped_too_small,
        "n_stocks_in_pool": int(pool["code"].nunique()),
        # 让人眼能核对 tradestatus 的语义，而不是相信它
        "tradestatus_value_counts": {str(k): int(v)
                                     for k, v in ts_counts.items()},
        "tradestatus_note": (
            "1 必须是**主流取值**，否则可成交过滤器是反向的。"
            "非 1 的占比应当很小（真实停牌率）。"
        ),
    }
    return daily, meta


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", type=Path, default=Path("data"))
    ap.add_argument("--start", default="2019-01-02")
    ap.add_argument("--end", default="")
    ap.add_argument("--out", default="xsec_sigma_r.json")
    ap.add_argument("--min-listed-days", type=int, default=MIN_LISTED_DAYS)
    args = ap.parse_args()

    root = Path(args.data_root)
    if not root.is_dir():
        raise SystemExit(f"数据根目录不存在：{root}")

    t0 = time.time()
    print("读入全市场日线 …", flush=True)
    panel, load_meta = load_panel(root)
    print(f"  {load_meta['n_rows']:,} 行 / {load_meta['n_files_used']} 只"
          f"（{time.time() - t0:.0f}s）", flush=True)

    print("构造可成交横截面 …", flush=True)
    daily, pool_meta = build_pool(panel, args.start, args.end,
                                  min_listed_days=args.min_listed_days)
    print(f"  保留 {pool_meta['n_days_kept']} 个交易日"
          f"（{time.time() - t0:.0f}s）", flush=True)

    if not daily:
        raise SystemExit("没有任何一天凑够横截面——先查 tradestatus 语义")

    print("  tradestatus 取值分布（用来核对语义，不是 1 为主就是剔反了）：")
    for k, v in sorted(pool_meta["tradestatus_value_counts"].items(),
                       key=lambda kv: -kv[1]):
        print(f"    {k:<8} {v:>12,}  ({v / sum(pool_meta['tradestatus_value_counts'].values()):.2%})")

    out = summarise(daily, winsorize=WINSOR)

    # 逐日重算一遍，只为报出「缩尾完全没动过」的天数。
    # summarise 把两版之差平均掉了，差为 0 的那些天会被平均**掩盖**。
    n_noop = sum(
        1 for arr in daily.values()
        if cross_sectional_sigma(arr, winsorize=WINSOR)
        >= cross_sectional_sigma(arr))

    # 逐年：σ_r 本身也可能有年度漂移，单点值撑不起「刚好越过」
    yearly = {}
    for y in sorted({d[:4] for d in daily}):
        sub = {d: daily[d] for d in daily if d.startswith(y)}
        yearly[y] = summarise(sub, winsorize=WINSOR)

    payload = {
        "kind": "xsec_sigma_r",
        "window": [args.start, args.end or max(daily)],
        "winsorize_q": list(WINSOR),
        "load": load_meta,
        "pool": pool_meta,
        **out,
        "n_days_winsor_noop": n_noop,
        "winsor_note": (
            "n_days_winsor_noop > 0 表示那几天缩尾退化成恒等变换"
            "（尾部堆质量超过缩尾比例）。这些天的 σ 应按**原始**读。"
        ),
        "unit_warning": "σ_r 是**收益率比例**；0.023 = 2.3%/日，不是 2.3。",
        "yearly": yearly,
        "elapsed_s": round(time.time() - t0),
    }

    reports = root / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    path = reports / args.out
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2,
                               default=str), encoding="utf-8")

    print(f"\nσ_r 原始      {out['sigma_r_raw']:.5f}")
    print(f"σ_r 缩尾      {out['sigma_r_winsorized']:.5f}")
    print(f"σ_r 中位      {out['sigma_r_median']:.5f}")
    print(f"σ_r 5%~95%    [{out['sigma_r_p05']:.5f}, {out['sigma_r_p95']:.5f}]")
    print(f"天数          {out['n_days']}（横截面不足跳过 "
          f"{pool_meta['n_days_too_small']}，平均 {out['avg_n']:.0f} 只）")
    print(f"缩尾没动过    {n_noop} 天")
    print("\n逐年：")
    for y, s in yearly.items():
        print(f"  {y}  原始 {s['sigma_r_raw']:.5f}  "
              f"缩尾 {s['sigma_r_winsorized']:.5f}  {s['n_days']} 天")
    print(f"\n→ {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

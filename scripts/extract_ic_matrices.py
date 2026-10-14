"""抽取每日因子相关矩阵 —— 组合 IC 的**充分统计量**。

## 为什么要有这个脚本

每次 IC 诊断都要重新载入面板（GB 级、约 10 分钟）。而因子合成
要反复问同一批问题：**换一组权重，逐年 IC 是多少？**
若每个权重方案都重跑一遍诊断，那是十次十分钟。

组合 IC 只依赖每天的两个小矩阵（`aplus/composite.py` 的恒等式）：

    R = corr(F_z)      k×k = 64 个数
    Σ_f = corr(F_z, r) k   =  8 个数

存下来就是每天 72 个浮点、1882 天 ≈ 13 万个数——
**一个几 MB 的 parquet**，之后所有权重方案**秒级**迭代。

## 口径（与 `factor_ic_diagnostic.py` 逐位一致）

- 因子取 `build_cross_sections` 的输出 —— 策略看到什么就量什么
- 真实窗口 = D+1 收盘买 → D+2 收盘卖（`ENTRY_LAG=1`，`lag1_real`）
- 秩相关口径与 `daily_ic_ranked` 的 `all` 一致（秩上的 Pearson）

## ⚠️ 与单因子 IC 的口径差异（**必须知道，否则会误比**）

本脚本取**共同完整样本**：某天某个因子缺失就整行剔除。
而 `daily_ic_ranked` 是**逐因子用各自的有效子集**。

    所以本文件里的 Σ_f ≠ 报告里单因子的 IC（差在缺失的那部分股票上）。

**后果**：单因子与合成的对照**必须用本文件里的同一份 Σ_f**，
拿 `factor_ic_7y_v2.json` 的数来比是**口径不一致**——
本脚本因此同时报出 `n_common` 与 `n_any`，
让这个差异**可量化**而不是靠信任。

用法：
    .venv/bin/python scripts/extract_ic_matrices.py \
        --start 2019-01-02 --data-root /tmp/aplus_snap_1012 \
        --out data/reports/ic_matrices.parquet
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from aplus.backtest import ENTRY_LAG, build_cross_sections
from aplus.composite import DegenerateFactor, daily_stats, zscore
from aplus.factor import FACTOR_NAMES
from aplus.factor_ic import FWD_WINDOWS, build_price_cache, forward_returns, \
    make_ranked_forward
from aplus.snapshot import load_factors_map
from aplus.store import LocalStore

#: 少于此样本量当天不记——秩相关在极小样本上极不稳
#: （8 个因子要估 64 个相关，小样本的 R 几乎是噪声）。
MIN_N = 200


def extract(dates, xs, cache, factors, window: str,
            verbose_every: int = 200):
    """逐日算 (R, Σ_f, n)，返回记录列表与跳过的日子清单。"""
    enter, exit_off = FWD_WINDOWS[window]
    k = len(factors)
    rows: list[dict] = []
    skipped: list[dict] = []
    t0 = time.time()

    for i, day in enumerate(dates):
        universe = xs.get(day)
        if universe is None or universe.empty:
            continue
        u_idx = universe.set_index("code")
        fwd = forward_returns(cache, list(u_idx.index), day, enter, exit_off)
        rf = make_ranked_forward(u_idx, fwd)
        if rf is None:
            continue

        F = u_idx.reindex(rf.codes)[factors]
        r = rf.r
        ok = np.isfinite(F.to_numpy(float)).all(axis=1)
        n_any = int(ok.sum())
        ok &= np.isfinite(r)
        n_common = int(ok.sum())
        if n_common < MIN_N:
            skipped.append({"date": day, "reason": "n_common<MIN_N",
                            "n_common": n_common, "n_any": n_any})
            continue

        Fv = F.to_numpy(float)[ok]
        rv = r[ok]
        try:
            R, sf = daily_stats(zscore(Fv), rv, names=tuple(factors))
        except DegenerateFactor as e:
            skipped.append({"date": day, "reason": str(e),
                            "n_common": n_common, "n_any": n_any})
            continue

        rec = {"date": day, "n_common": n_common, "n_any": n_any}
        for j, nm in enumerate(factors):
            rec[f"sf__{nm}"] = float(sf[j])
        for a in range(k):
            for b in range(k):
                rec[f"R__{a}{b}"] = float(R[a, b])
        rows.append(rec)

        if verbose_every and (i + 1) % verbose_every == 0:
            print(f"  {i + 1}/{len(dates)} 日  记 {len(rows)}  "
                  f"跳 {len(skipped)}  {time.time() - t0:.0f}s", flush=True)

    return rows, skipped


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2019-01-02")
    ap.add_argument("--end", default="")
    ap.add_argument("--data-root", type=Path, default=Path("/tmp/aplus_snap_1012"))
    ap.add_argument("--out", default="data/reports/ic_matrices.parquet")
    ap.add_argument("--factors", default="", help="逗号分隔，默认全部 8 个")
    ap.add_argument("--window", default="lag1_real")
    args = ap.parse_args()

    factors = [f.strip() for f in args.factors.split(",") if f.strip()] \
        or list(FACTOR_NAMES)
    if args.window not in FWD_WINDOWS:
        raise SystemExit(f"未知窗口 {args.window}，可选：{list(FWD_WINDOWS)}")

    data_root = Path(args.data_root)
    if not data_root.is_dir():
        raise SystemExit(f"数据根目录不存在：{data_root}")
    store = LocalStore(data_root)
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

    rows, skipped = extract(dates, xs, cache, factors, args.window)
    if not rows:
        print("一天都没记下来——先查 MIN_N 与因子列是否真的存在")
        return 1
    df = pd.DataFrame(rows)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)

    # 口径差异必须**报出来**而不是留在文件里等人踩
    loss = (df["n_any"] - df["n_common"]) / df["n_any"]
    print(f"\n记下 {len(df)} 天，跳过 {len(skipped)} 天")
    print(f"共同完整样本损失：均值 {loss.mean():.4%}  最大 {loss.max():.4%}")
    print(f"  （即：本文件的 Σ_f 与报告里的单因子 IC 差在这 "
          f"{loss.mean():.3%} 的样本上）")
    print(f"跳过的日子：{pd.DataFrame(skipped)['reason'].value_counts().to_dict()}"
          if skipped else "  （无）")
    print(f"\n→ {out}  {len(df)} 行 × {len(df.columns)} 列  "
          f"总耗时 {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
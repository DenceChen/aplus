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

### 列名**必须带因子名**（旧格式踩过一次坑）

`R__<因子a>__<因子b>` 与 `sf__<因子名>` —— **不是** `R__ab`。

初版按 `FACTOR_NAMES` 的**位置**存 `R__ab`，读的时候按**调用方顺序**取。
只要传子集（`--factors mom20,rev5,vsur`），整块 R 就**平移错标**，
而且**一个错都不报**：`R__00`…`R__22` 一列都不缺。

> R 无论怎么错标都仍然**对称**、对角线仍然**全 1**，
> 所以这类结构校验在这里**恒真**——它是安慰剂，不是守卫。
> 能防住错标的**只有标签本身**。

## 口径（与 `factor_ic_diagnostic.py` 逐位一致）

- 因子取 `build_cross_sections` 的输出 —— 策略看到什么就量什么
- 真实窗口 = D+1 收盘买 → D+2 收盘卖（`ENTRY_LAG=1`，`lag1_real`）
- **两端都排名**：前瞻收益由 `make_ranked_forward` 排（`r` 是秩），
  因子端由 `rank_zscore` 排。所以 `Σ_f` 是**秩相关（Spearman）**，
  与 `daily_ic_ranked` 的 `all` 同口径。

### ⚠️ 这里曾经出过一次**口径事故**，它值得被记住

初版写的是 `daily_stats(zscore(Fv), rv)` —— 因子端用**原始值**，
收益端却是**秩**。算出来的是 `corr(z(F原始), rank(r))`：

> **一半秩、一半原始值，既不是 Spearman 也不是 Pearson。**

它不崩、不报警、报告照样出数。病根是那句 docstring **写着「秩相关口径」而代码没实现**——
注释与代码不一致，且注释是**对的那一边**。

后果可量化：弱信号区 Pearson 比 Spearman 高约 **1.047 倍**，
而它经 `σ_r · K` **直接乘在年化收益上**。
更糟的是它让人得出「口径无差异」的错觉——实测的
「共同完整样本损失 0.0000%」只证明了**样本**相同，
**没证明统计量相同**。

因此本脚本**两套都存**：`sf__` 是秩口径（进判决），
`sfP__` 是原始值口径（只作对照），让这个比值可查而不是靠记。

## ⚠️ 与单因子 IC 的**样本**差异（**必须知道，否则会误比**）

本脚本取**共同完整样本**：某天某个因子缺失就整行剔除。
而 `daily_ic_ranked` 是**逐因子用各自的有效子集**。

    统计量现在一致了；剩下的差异**只在样本上**。
    本脚本因此同时报出 `n_common` 与 `n_any`，
    让这个差异**可量化**而不是靠信任。

**后果**：单因子与合成的对照**必须用本文件里的同一份 Σ_f**，
拿 `factor_ic_7y_v2.json` 的数来比是**样本不一致**。

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
from aplus.composite import (DegenerateFactor, daily_stats, rank_zscore,
                             r_column)
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
            R, sf = daily_stats(rank_zscore(Fv), rv, names=tuple(factors))
        except DegenerateFactor as e:
            skipped.append({"date": day, "reason": str(e),
                            "n_common": n_common, "n_any": n_any})
            continue

        rec = {"date": day, "n_common": n_common, "n_any": n_any}
        for j, nm in enumerate(factors):
            rec[f"sf__{nm}"] = float(sf[j])
            # **同时存 Pearson 口径**（因子不排名的原始值）。
            # 它不进判决，只为把「秩口径 vs 原始值口径」差多少**量化**——
            # 弱信号区这个比值约 1.047，而它直接乘在年化收益上。
            # 只存一套的话，这个差只能靠人记得，而记得是最靠不住的一环。
            rec[f"sfP__{nm}"] = float(np.corrcoef(Fv[:, j], rv)[0, 1])
        for a in range(k):
            for b in range(k):
                # **按名字**存，不按位置。见 `aplus.composite.r_column`：
                # 位置索引过一次「平移错标」的静默事故，
                # 而对称性/单位对角这类结构校验**恒真**，抓不到它。
                rec[r_column(factors[a], factors[b])] = float(R[a, b])
        rows.append(rec)

        if verbose_every and (i + 1) % verbose_every == 0:
            print(f"  {i + 1}/{len(dates)} 日  记 {len(rows)}  "
                  f"跳 {len(skipped)}  {time.time() - t0:.0f}s", flush=True)

    return rows, skipped


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2019-01-02")
    ap.add_argument("--end", default="")
    ap.add_argument("--data-root", type=Path, default=Path("data"))
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
    print(f"  ⚠️ 这只说明**样本**一致，**没说明统计量一致**——")
    print(f"     样本一致与统计量一致是两件事。把前者当后者，"
          f"是我 2026-10-14 犯过的错。")

    # 两套口径的比值直接**算出来**。它是逐因子而变的：
    # 实测 0.996（ivol/vol20/turn20，波动率类）到 1.499（vsur，收益类）。
    # 所以「秩 vs 原始值差多少」**没有全局常数**，必须逐因子看。
    print("\n秩口径 / 原始值口径 的比值（判决年才看，判决不受早期影响）：")
    y = df["date"].str[:4].astype(int)
    m = y >= 2021
    for f in factors:
        a = df.loc[m, f"sf__{f}"].mean()
        b = df.loc[m, f"sfP__{f}"].mean()
        if a:
            print(f"  {f:>10}  秩 {a:>+9.5f}  原始值 {b:>+9.5f}  "
                  f"比值 {abs(b / a):.3f}")
    print("  → 波动率类 ≈1.00，收益类 1.1~1.5。**没有全局常数。**")

    print(f"跳过的日子：{pd.DataFrame(skipped)['reason'].value_counts().to_dict()}"
          if skipped else "  （无）")
    print(f"\n→ {out}  {len(df)} 行 × {len(df.columns)} 列  "
          f"总耗时 {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
#!/usr/bin/env python
"""`turn` 输入完整性诊断 —— 执行 `docs/TODO_turn_integrity_2026-10-15.md`。

判据在预注册里写死，此处**只实现**，不改任何阈值。

## 口径已用地面真值校准（2026-10-15）

预注册的 §0 触发链里，基准被判定为「被输入数据挡住」，**该判定已被推翻**：
反推 `volume*100/turn` 得到的不是 `float_shares`，而是 **`a_shares`（流通 A 股）**。
对 `data/master/shares/` 全部 21 个文件做**逐日同口径**比对：

```
float_shares   10/21 落在 [0.95,1.05]   中位比值 0.9112
total_shares    8/21 落在 [0.95,1.05]   中位比值 0.8834
a_shares       21/21 落在 [0.95,1.05]   中位比值 1.0000   ← 命中
```

⇒ `turn` 未被污染，用的是正确的口径（流通 A 股才是 A 股真实可交易基数）。
⚠️ 本脚本的 `iqr_ratio` 度量的是**时间稳定性**，与除以哪一列无关
（除以一个每票常数不改变 p75/p25 之比）⇒ **预注册的判据与阈值一字未改**。

## 判决

    R = median(200 只票的 iqr_ratio)

| 分支 | 条件 |
|---|---|
| `TURN_CLEAN` | `R < 1.3` |
| `TURN_CORRUPT` | `R >= 2.0` |
| `TURN_GRAY` | `1.3 <= R < 2.0` |

**先判覆盖再抽样**：只在窗口内有 ≥ 100 个有效日的票里抽，避免「抽到数据不全的票」
把 IO 噪声误判成数据缺陷。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

# ── 预注册常量（改动即作弊，见预注册 §1「三条不得逾越的规则」）────────────────
TURN_AUDIT_SEED = 20261015
N_SAMPLE = 200
WINDOW_START = "2021-01-01"
WINDOW_END = "2026-10-14"
MIN_VALID_DAYS = 100          # 覆盖门槛：先判覆盖再抽样
R_CLEAN = 1.3                 # TURN_CLEAN  上界
R_CORRUPT = 2.0               # TURN_CORRUPT 下界

DAILY_DIR = ROOT / "data" / "master" / "daily"
SHARES_DIR = ROOT / "data" / "master" / "shares"
OUT = ROOT / "data" / "reports" / "turn_integrity.json"


def implied_a_shares(daily: pd.DataFrame) -> pd.Series:
    """`turn` 反推出的流通 A 股。只在 turn>0 且 volume>0 的日子有意义。

    `turn` 是**百分数**，故 ×100。
    """
    ok = daily["turn"].notna() & (daily["turn"] > 1e-9) & \
         daily["volume"].notna() & (daily["volume"] > 0)
    return daily.loc[ok, "volume"] * 100.0 / daily.loc[ok, "turn"]


def iqr_ratio(s: pd.Series) -> float:
    p25, p75 = s.quantile(0.25), s.quantile(0.75)
    return float(p75 / p25) if p25 and p25 > 0 else float("nan")


def truth_asof(sh: pd.DataFrame, dates: np.ndarray, col: str = "a_shares"):
    """逐日取股本真值（分段常数右连）。

    🚨 **早于首条记录的日期必须剔除，不能 clip 到 0。**
    clip 会把它们向前填充成第一条的值，而 `value > 0` 抓不到 ——
    不报错，只是悄悄比错一个量。返回 `(values, valid_mask)` 让调用方
    显式处理无效日。
    """
    raw = np.searchsorted(sh["date"].to_numpy(), dates, side="right") - 1
    valid = raw >= 0
    idx = np.clip(raw, 0, len(sh) - 1)
    values = sh[col].to_numpy(float)[idx]
    valid &= values > 0
    return values, valid


def ground_truth_check() -> dict:
    """用 `data/master/shares/` 的**逐日同口径**真值校准反推值。"""
    rows = []
    for sf in sorted(SHARES_DIR.glob("*.parquet")):
        code = sf.stem
        dp = DAILY_DIR / f"{code}.parquet"
        if not dp.exists():
            continue
        sh = pq.read_table(sf).to_pandas().sort_values("date")
        d = pq.read_table(dp).to_pandas()
        if "turn" not in d.columns or "a_shares" not in sh.columns:
            continue
        w = d[(d["date"] >= WINDOW_START) & (d["date"] <= WINDOW_END)]
        w = w[w["turn"].notna() & (w["turn"] > 1e-9) & w["volume"].notna() & (w["volume"] > 0)]
        if len(w) < MIN_VALID_DAYS:
            continue
        # ★ 逐日取真值：比「窗口末值」严格 —— 若窗口内股本变过，
        #   窗口末值比较本身就是错的量（记忆：compare-like-calibers-or-not-at-all）。
        truth, ok = truth_asof(sh, w["date"].to_numpy())
        if ok.sum() < MIN_VALID_DAYS:
            continue
        ratio = implied_a_shares(w).to_numpy() / truth[ok]
        rows.append({"code": code, "n_days": int(ok.sum()),
                     "ratio_p05": float(np.percentile(ratio, 5)),
                     "ratio_med": float(np.median(ratio)),
                     "ratio_p95": float(np.percentile(ratio, 95))})
    if not rows:
        return {"n_stocks": 0, "note": "shares 目录为空或列名不符"}
    df = pd.DataFrame(rows)
    in_band = df["ratio_med"].between(0.95, 1.05)
    return {
        "truth_column": "a_shares",
        "n_stocks": len(df),
        "n_in_band_095_105": int(in_band.sum()),
        "median_ratio": float(df["ratio_med"].median()),
        "per_stock": df.to_dict("records"),
        "verdict": ("turn 用 a_shares(流通A股) 口径，未被污染"
                    if in_band.all() else
                    f"⚠️ 仅 {int(in_band.sum())}/{len(df)} 命中 [0.95,1.05]"),
    }


def main() -> int:
    files = sorted(DAILY_DIR.glob("*.parquet"))
    # ── 第一步：覆盖判定（先判覆盖再抽样）────────────────────────────────
    covered = []
    for f in files:
        try:
            md = pq.ParquetFile(f).metadata.num_rows
        except Exception:
            continue
        if md >= MIN_VALID_DAYS:
            covered.append(f)
    rng = np.random.default_rng(TURN_AUDIT_SEED)
    if len(covered) > N_SAMPLE:
        pick = rng.choice(len(covered), size=N_SAMPLE, replace=False)
        sample = [covered[i] for i in sorted(pick)]
    else:
        sample = covered

    rows = []
    for f in sample:
        d = pq.read_table(f).to_pandas()
        if "turn" not in d.columns:
            continue
        w = d[(d["date"] >= WINDOW_START) & (d["date"] <= WINDOW_END)]
        s = implied_a_shares(w)
        if len(s) < MIN_VALID_DAYS:
            continue
        r = iqr_ratio(s)
        by_year = s.groupby(w.loc[s.index, "date"].str[:4]).median()
        rows.append({"code": f.stem, "n_days": len(s), "iqr_ratio": r,
                     "jump_max_over_min": float(by_year.max() / by_year.min())
                     if len(by_year) > 1 else 1.0})

    df = pd.DataFrame(rows).sort_values("iqr_ratio", ascending=False)
    R = float(df["iqr_ratio"].median())
    verdict = ("TURN_CLEAN" if R < R_CLEAN
               else "TURN_CORRUPT" if R >= R_CORRUPT
               else "TURN_GRAY")
    out = {
        "prereg": "docs/TODO_turn_integrity_2026-10-15.md",
        "seed": TURN_AUDIT_SEED, "n_sample_requested": N_SAMPLE,
        "n_sample_used": len(df), "n_covered": len(covered),
        "window": [WINDOW_START, WINDOW_END],
        "metric": "iqr_ratio = p75(implied)/p25(implied)，implied = volume*100/turn",
        "R": R, "R_clean_threshold": R_CLEAN, "R_corrupt_threshold": R_CORRUPT,
        "verdict": verdict,
        "distribution": {
            "q25": float(df["iqr_ratio"].quantile(.25)),
            "median": R, "q75": float(df["iqr_ratio"].quantile(.75)),
            "max": float(df["iqr_ratio"].max()),
            "hist": {f"{lo:.2f}-{lo+0.5:.2f}": int(((df["iqr_ratio"] >= lo) &
                                                    (df["iqr_ratio"] < lo+0.5)).sum())
                     for lo in np.arange(1.0, 5.01, 0.5)},
        },
        "frac_gt_2": float((df["iqr_ratio"] > 2.0).mean()),
        "frac_gt_1_3": float((df["iqr_ratio"] > R_CLEAN).mean()),
        "jump_distribution": {
            "median": float(df["jump_max_over_min"].median()),
            "q75": float(df["jump_max_over_min"].quantile(.75)),
            "max": float(df["jump_max_over_min"].max()),
        },
        "ground_truth": ground_truth_check(),
        "worst20": df.head(20).to_dict("records"),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"verdict           = {verdict}")
    print(f"R (median iqr)    = {R:.4f}   [CLEAN<{R_CLEAN} | CORRUPT>={R_CORRUPT}]")
    print(f"n_covered/sample  = {len(covered)}/{len(df)}")
    print(f"iqr p25/med/q75   = {out['distribution']['q25']:.3f} / "
          f"{R:.3f} / {out['distribution']['q75']:.3f}  max={out['distribution']['max']:.3f}")
    print(f"frac(iqr>1.3)     = {out['frac_gt_1_3']*100:.1f}%")
    print(f"frac(iqr>2.0)     = {out['frac_gt_2']*100:.1f}%")
    print(f"跨年跳变 median   = {out['jump_distribution']['median']:.2f}x")
    gt = out["ground_truth"]
    print(f"地面真值 a_shares = {gt.get('n_in_band_095_105')}/{gt.get('n_stocks')} 命中"
          f"  中位比值 {gt.get('median_ratio', float('nan')):.4f}")
    print(f"\n⚠️ 报告义务：iqr>2 的票逐只列出（是个例还是普遍）；")
    print(f"   20 日 / 21 日 / 252 日窗口各跨越多少跳变点 —— 缺这两项判决不可用。")
    print(f"\n→ {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

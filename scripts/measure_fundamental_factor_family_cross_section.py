"""A3 —— **因子族就绪报告**：五个冻结因子的全池横截面。

预注册：`docs/PREREG_FUNDAMENTAL_FAMILY_2026-10-20.md` §2.1 / §8.1 A3。

## 这个问题要回答什么

族 A 的判决门槛是 `bonferroni_t(n_A, df)`，而 **`n_A` 必须由「因子数 × K 档数」算出来**
（预注册 §5.2：门槛不许手打）。

🚨 **A3 是「描述性」的产物，不是判决。** 预注册 §8.1 明写 A3 的判据是
「**不含 t、不含判决**」——它的用途是**冻结族 A 的因子数**（从而让门槛可推导），
**不是**判因子好不好。任何 t 值或判决出现在这里都是越界。

⇒ 所以本产物只给：覆盖率、分布、**因子间相关矩阵**、市值分层，
外加**由相关结构算出的有效因子数**（阈值无关的谱量，不是判决）。

## 五个冻结因子（预注册 §2.1，**一字不改**）

| ID | 因子 | 定义 | 方向 |
|---|---|---|---|
| F1 | `bm` | `log(BPS / close)` | 越大越便宜 |
| F2 | `ep` | `log(EPSJB / close)`（**年报 EPS，不是 TTM**） | 越大越便宜 |
| F3 | `roe` | `ROEJQ`（加权 ROE，季报口径） | 越大越好 |
| F4 | `size` | `-log(close × a_shares)`，**对照因子**（不进族 A） | 越小越好 |
| F5 | `q` | `MGJYXJJE / BPS`（每股经营现金流 ÷ 每股净资产） | 越大越好 |

🚨 **F2 的口径偏离是登记在案的**（预注册 §2.3）：本仓没有 ROE(TTM)，
石川的 EP 基准**不可直接套用**。F3 同理。

## 为什么 A3 的相关矩阵是**决定 n_A 的那一个**

预注册 §8.1 A3 逐字：「它产出**因子间相关矩阵** ⇒ 若 F1/F2/F3/F5 高度相关
（石川说股息率本质是价值指标、`EP = BM × ROE`），则**族 A 里那些因子其实是同一个轴**
⇒ ⇒ **族 A 的有效因子数要按相关结构重算**，否则 Bonferroni 分母是虚高的
（门槛会过严，**过严的门槛会把人推向「改本来正确的代码」**）」

⇒ 本产物用**阈值无关**的谱量（参与比 participation ratio）给有效维数：
不设「多高算高相关」的门槛（那会是一次**看过数据之后**的选择），
而是把特征值谱原样报出来，让下一份预注册自己定冻结规则。

## 数据协议（预注册 §3.1，不许自创）

- 财务侧**只**从 `aplus.vintage.pit_frame(root, as_of)` 取，
  年报口径 + `NOTICE_DATE` 对齐 ⇒ 委托 `aplus/factor_fundamental.py::annual_from_frame`。
- 价格 = **不复权 close**，逐票单行。
- `a_shares = volume × 100 / turn`（预注册 §3.3 指定的 `aplus/capweight.py` 口径）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from aplus import provenance  # noqa: E402
from aplus.factor_fundamental import (  # noqa: E402
    annual_from_frame, bare_code, pit_frame,
)
from scripts._fundamental_family_stats import read_bars, stale_price_count  # noqa: E402

GENERATOR = "scripts/measure_fundamental_factor_family_cross_section.py"
DEFAULT_OUT = (ROOT / "data/reports"
               / "fundamental_factor_family_cross_section_2026-10-20.json")
PREREG = "docs/PREREG_FUNDAMENTAL_FAMILY_2026-10-20.md"

AS_OF = "2026-10-20"

#: 预注册 §2.1 的五个冻结因子。**顺序即 §2.1 的顺序**，一个不许加一个不许减。
FACTORS = ("bm", "ep", "roe", "size", "q")

#: 预注册 §2.1 表里 F1/F2/F3/F5 进族 A，F4（size）是**对照因子**
#: （预注册 §2.5：「F4（size）是对照因子，不是基本面因子……它**不进入**基本面族的「晋级」，
#: 它进入**族 B**（诊断族），因为它的作用是解释，不是晋级」）。
FAMILY_A = ("bm", "ep", "roe", "q")

#: 族 A 的 K 档数（预注册 §5.2：臂 R 的 K ∈ {21, 63} ⇒ 2 档）。
N_K_ARMS = 2

#: 分布分位数（描述性，不是判决）。
QUANTS = (0.01, 0.05, 0.25, 0.50, 0.75, 0.95, 0.99)


def build_frame(root: Path, as_of: str) -> tuple[pd.DataFrame, dict]:
    """PIT 帧 + 价格 ⇒ 五个因子的全池横截面。"""
    vintage = root / "reports" / "financial_vintage"
    frame = pit_frame(vintage, as_of)
    ann = annual_from_frame(frame, as_of)
    # `annual_from_frame` 只留 bps ⇒ 其余三个字段从**同一帧**按同一键取回，
    # 不另立对齐协议（对齐仍由 annual_from_frame 决定）。
    extra = frame[["SECURITY_CODE", "REPORT_DATE", "EPSJB", "ROEJQ",
                   "MGJYXJJE"]].copy()
    extra["code"] = extra["SECURITY_CODE"].astype(str).map(bare_code)
    extra["report_date"] = pd.to_datetime(extra["REPORT_DATE"], errors="coerce")
    ann = ann.merge(extra, on=["code", "report_date"], how="left")
    ann["eps"] = pd.to_numeric(ann["EPSJB"], errors="coerce")
    ann["roe"] = pd.to_numeric(ann["ROEJQ"], errors="coerce")
    ann["cfps"] = pd.to_numeric(ann["MGJYXJJE"], errors="coerce")

    bars = read_bars(root, as_of)
    m = ann.merge(bars, on="code", how="inner").copy()

    m["bm"] = np.log(m["bps"].where(m["bps"] > 0) / m["close"])
    m["ep"] = np.log(m["eps"].where(m["eps"] > 0) / m["close"])
    # F5 q = 每股经营现金流 ÷ 每股净资产（预注册 §2.1 F5）。
    m["q"] = m["cfps"] / m["bps"].where(m["bps"] > 0)
    # F4 size = -log(close × a_shares)，a_shares = volume×100/turn（预注册 §3.3）。
    m["a_shares"] = m["volume"] * 100.0 / m["turn"].where(m["turn"] > 1e-9)
    m["size"] = -np.log((m["close"] * m["a_shares"]).where(m["a_shares"] > 0))
    m["cap"] = m["close"] * m["a_shares"]
    diag = {
        "n_codes_with_bar": int(len(bars)),
        "n_stale_price": stale_price_count(bars, as_of),
        "n_codes_annual": int(len(ann)),
        "n_codes_merged": int(len(m)),
    }
    return m, diag


def spectrum(corr: np.ndarray) -> dict:
    """相关矩阵的**阈值无关**有效维数。

    - 特征值谱（描述性）
    - 参与比 `n_eff = (Σλ)² / Σλ²`：**不受「多高算高相关」的影响**，
      因此不会是一次「看过数据之后」的选择。
    - `n_eff_rank` = 参与比的秩版本（对单调变换不变）。
    """
    ev = np.linalg.eigvalsh(corr)
    ev = np.clip(ev, 0.0, None)
    s = ev.sum()
    n_eff = float((s ** 2) / max((ev ** 2).sum(), 1e-12))
    return {
        "eigenvalues": [float(x) for x in ev[::-1]],
        "participation_ratio": n_eff,
        "participation_ratio_rank": n_eff / len(ev),
        "note": ("参与比 = (Σλ)²/Σλ²，**阈值无关**。"
                 "它不回答「哪些因子算同一个轴」——那个判断留给下一份预注册；"
                 "本产物只保证**不用事后挑阈值**就把结构摆出来"),
    }


def main(argv=None) -> int:
    # 第 125 层：盖章那一刻盘上的字节 ≠ 这次执行真正跑的那些字节。
    expect_bytes = provenance.code_file_sha256(ROOT, GENERATOR)

    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", type=Path, default=ROOT / "data")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args(argv)

    print("[1/4] 构造五因子全池横截面（PIT 帧 + 逐票单行价格）…", flush=True)
    m, diag = build_frame(args.data_root, AS_OF)
    print(f"    {diag['n_codes_merged']} 只（年报口径 ∩ 有价格）", flush=True)

    print("[2/4] 覆盖率与分布 …", flush=True)
    n_total = int(len(m))
    coverage = {}
    dists = {}
    for f in FACTORS:
        v = m[f].to_numpy(dtype=float)
        ok = np.isfinite(v)
        vv = v[ok]
        coverage[f] = {
            "n_valid": int(ok.sum()),
            "n_total": n_total,
            "coverage": round(float(ok.mean()), 6),
            "n_missing": int((~ok).sum()),
        }
        if vv.size:
            dists[f] = {
                "mean": float(vv.mean()),
                "std": float(vv.std(ddof=1)),
                "min": float(vv.min()),
                "quantiles": {str(q): float(np.quantile(vv, q)) for q in QUANTS},
                "max": float(vv.max()),
            }
        else:
            dists[f] = {"n_valid": 0}

    print("[3/4] 因子间相关矩阵 …", flush=True)
    # **共同样本**：五个因子都有值的那些行。
    # 🚨 为什么要共同样本：两两相关若各用各的有效样本，
    #    同一个矩阵里每一格的分母**不同** ⇒ 矩阵不再是同一个量。
    common = m[list(FACTORS)].dropna()
    n_common = int(len(common))
    spearman = common.corr(method="spearman").to_numpy()
    pearson = common.corr(method="pearson").to_numpy()
    # 族 A 子集的相关矩阵（**决定 n_A 的那一个**，预注册 §8.1 A3）。
    idx_a = [FACTORS.index(f) for f in FAMILY_A]
    corr_a = spearman[np.ix_(idx_a, idx_a)]

    print("[4/4] 市值分层（5 档）…", flush=True)
    # 市值 = close × a_shares（预注册 §3.3 口径）。
    cap = m["cap"].to_numpy(dtype=float)
    ok_cap = np.isfinite(cap) & (cap > 0)
    cap_edges = np.unique(np.quantile(cap[ok_cap], np.linspace(0, 1, 6)))
    strat = []
    for i in range(len(cap_edges) - 1):
        lo, hi = cap_edges[i], cap_edges[i + 1]
        sel = ok_cap & (cap >= lo) & (cap < hi if i < len(cap_edges) - 2 else cap <= hi)
        row = {
            "bucket": i + 1,
            "cap_lo": float(lo),
            "cap_hi": float(hi),
            "n": int(sel.sum()),
            "median_cap": float(np.median(cap[sel])) if sel.any() else None,
            "factor_medians": {},
        }
        for f in FACTORS:
            v = m.loc[sel, f].to_numpy(dtype=float)
            v = v[np.isfinite(v)]
            row["factor_medians"][f] = float(np.median(v)) if v.size else None
        strat.append(row)

    # ── 族 A 的有效因子数（**只给结构，不给判决**）────────────────────
    spec_a = spectrum(corr_a)
    n_eff = spec_a["participation_ratio"]
    # 门槛分母的候选：n_A = 可晋级因子数 × K 档数（预注册 §5.2）。
    n_A_naive = len(FAMILY_A) * N_K_ARMS
    n_A_eff = n_eff * N_K_ARMS

    payload = {
        "kind": "fundamental_factor_family_cross_section",
        "prereg": PREREG,
        "prereg_sections": ["§2.1 因子清单", "§2.5 冻结规则",
                             "§5.2 族 A 的假设数 n_A", "§8.1 A3 轮次 A 的第三件"],
        "as_of": AS_OF,
        "question": "五个冻结因子的全池横截面长什么样（族 A 的 n_A 的输入）",
        "product_role": {
            "descriptive_only": True,
            "contains_no_t_statistic": True,
            "contains_no_verdict": True,
            "why": ("预注册 §8.1 A3 的判据逐字：「**不含 t、不含判决** —— "
                    "它的用途是**冻结族 A 的因子数**（从而让门槛可推导），"
                    "不是判因子好不好」"),
        },
        "factors": {
            "frozen_list": list(FACTORS),
            "definitions": {
                "bm": "log(BPS / close)，越大越便宜（§2.1 F1）",
                "ep": "log(EPSJB / close)，越大越便宜；**年报 EPS，不是 TTM**（§2.1 F2 + §2.3 口径偏离登记）",
                "roe": "ROEJQ 加权 ROE，越大越好；**季报口径，不是 ROE(TTM)**（§2.1 F3 + §2.3）",
                "size": "-log(close × a_shares)，越小越好；**对照因子，不进族 A**（§2.1 F4 + §2.5）",
                "q": "MGJYXJJE / BPS（每股经营现金流 ÷ 每股净资产），越大越好；**代理变量**（§2.1 F5）",
            },
            "a_shares_caliber": "volume × 100 / turn（预注册 §3.3 指定的 aplus/capweight.py 口径；不用 float_shares）",
            "price_caliber": "不复权 close（预注册 §3.3）",
            "financial_caliber": "年报口径 + NOTICE_DATE 对齐，只从 aplus/vintage.py::pit_frame 取（§3.1）",
        },
        "universe": diag,
        "coverage": coverage,
        "distributions": dists,
        "correlations": {
            "sample": "**五个因子都有值**的共同样本（否则同一矩阵里每格分母不同）",
            "n_common": n_common,
            "labels": list(FACTORS),
            "spearman": {
                "matrix": [[float(x) for x in row] for row in spearman],
                "why_primary": ("**秩相关是主口径**：F3（ROEJQ，跨度 -2.5e4…2.7e3）"
                                "与 F2（EP）都有极端值，Pearson 会被极值主导；"
                                "而本仓的 IC 检验本身就是**秩相关**"
                                "（aplus/factor_ic.py::rank_corr）"),
            },
            "pearson": {
                "matrix": [[float(x) for x in row] for row in pearson],
                "note": "副口径。极值敏感，仅作对照",
            },
            "family_a_subset": {
                "labels": list(FAMILY_A),
                "spearman_matrix": [[float(x) for x in row] for row in corr_a],
                "spectrum": spec_a,
            },
        },
        "market_cap_strata": {
            "n_buckets": len(strat),
            "caliber": "close × a_shares，5 档等分位切分",
            "buckets": strat,
        },
        "family_a_n_A_inputs": {
            "note": ("**这是「冻结族 A 的因子数」的输入，不是判决。**"
                     "预注册 §8.1 A3 逐字：有效因子数要按相关结构重算，"
                     "否则 Bonferroni 分母虚高（门槛过严 ⇒ 过严的门槛会把人推向"
                     "「改本来正确的代码」）。**冻结规则本身要写进下一份预注册的 §5.2** ——"
                     "本产物**不替下一份预注册做这个选择**"),
            "n_factors_family_a": len(FAMILY_A),
            "n_K_arms": N_K_ARMS,
            "n_A_if_factors_uncorrelated": n_A_naive,
            "n_A_if_effective_dimension_used": float(n_A_eff),
            "effective_dimension": n_eff,
            "effective_dimension_note": ("参与比（阈值无关）。"
                                         "若下一份预注册采用「有效维数即因子数」的规则，"
                                         "则 n_A = 有效维数 × K 档数；"
                                         "若采用「四因子各算一个假设」的规则，"
                                         "则 n_A = 4 × 2。**两个数都在这里，规则留给下一份预注册**"),
        },
        "scope": {
            "reads": "只读 vintage parquet + daily parquet 的单票单行；未建面板",
            "no_promotion": "本产物不产生任何采纳结论，不判因子好坏",
            "not_measured": [
                "因子的预测力（族 A 的事，需 forward return）",
                "F6 股息率（预注册 §2.4：数据源无分红字段，**算不出来**，不是「不显著」）",
            ],
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    provenance.stamp(
        args.out, GENERATOR, root=ROOT, expect_bytes=expect_bytes,
        extra={
            "prereg": PREREG,
            "as_of": AS_OF,
            "frozen_factors": list(FACTORS),
            "family_a_factors": list(FAMILY_A),
            "data_root": str(Path(args.data_root).resolve()),
        })
    print(f"\n  → {args.out}")
    for f in FACTORS:
        c = coverage[f]
        print(f"  {f:5s} 覆盖 {c['n_valid']}/{c['n_total']} = {c['coverage']:.1%}")
    print(f"  共同样本 {n_common} 只；族 A 参与比有效维数 {n_eff:.3f}")
    print(f"  n_A 候选：不去相关 {n_A_naive} / 用有效维数 {n_A_eff:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

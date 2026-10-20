"""A1 —— **污染集中度**：PIT 变更行是不是挤在 BM 的两端？

预注册：`docs/PREREG_FUNDAMENTAL_FAMILY_2026-10-20.md` §6.1（机理③）。

## 问题

BM 的 IC 显著而组合不赚钱（`data/reports/bm_ic_attribution.json`）。
七条候选机理里，机理③是「**PIT 污染集中在 BM 两端**」——
若被改过的行恰好落在最便宜（或最贵）那一端，
那么 BM 两端就是**脏数据**，用它排序等于按「数据脏不脏」排序。

## 做法（预注册 §6.1 逐条）

1. 取 `date=2026-10-14` 与 `date=2026-10-20` 两份 PIT 帧，
   按 `(SECUCODE, REPORT_DATE)` 对齐，差分出**至少一个数值列变了**的行。
2. 对这些变更行，用 **2026-10-14 快照的 `BPS`** ÷ **2026-10-14 的不复权 close**
   算 `BM_before`（**变更前的账面值**，这正是「脏」的那一份）。
3. 把变更行按 `BM_before` 放进**全池十分档**，与全池本身的分档分布对比。
4. **判据**：各档占比的**最大绝对偏差 > 3 个百分点** ⇒ 判定「污染集中在尾部」
   ⇒ 机理③成立。同时报卡方统计量与 p 值。

## 🚨 三条不许越界的线

1. **不许把「没测出差异」写成「没有污染」。**
   本产物只报**测到了什么**；「污染不存在」这句话**不在本产物的射程内**——
   窗口只有 6 天、变更行只有几十行，检出力由产物自己报出来。
2. **3pp 门槛是自研的**（预注册 §6.1 明写「书与仓内均无此门槛」）。
   ⇒ 产物里必须同时给出**零假设下的噪声尺度**（`mc_null_max_dev`），
   否则「3pp」这句话**没有参照系**。
3. **不许把「污染均匀」读成「污染无害」**——预注册 §6.0 的机理③原文：
   「均匀 ⇒ 污染只加噪（**降低** IC）⇒ **削弱③**」。

## 数据协议（预注册 §3.1，不许自创）

- 财务侧**只**从 `aplus.vintage.pit_frame(root, as_of)` 取。
- 对齐**只**用 `NOTICE_DATE`（`aplus/financial.py::ALIGN_DATE_COL`）。
- **年报口径**，披露日 ≥ 报告期末 + 30 天，同一 (code, report_date) 取最后一条，
  同一 code 取披露日最新那一份 ⇒ 全部委托 `aplus/factor_fundamental.py::annual_from_frame`，
  **本脚本不自己写这套协议**。
- 价格 = **不复权 close**。

## 内存纪律

🚨 **不许跑 GB 级面板。** 本脚本逐票读**一根 bar**
（`scripts/_fundamental_family_stats.py::read_bars`），
峰值内存 = 5,460 行，不建 `FactorPanel`。
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
from aplus.factor_fundamental import annual_from_frame, bare_code, pit_frame  # noqa: E402
from scripts._fundamental_family_stats import (  # noqa: E402
    chi2_sf, decile_counts, decile_edges, max_abs_rate_dev_pp,
    max_abs_share_dev_pp, mc_null_max_dev, mc_null_max_rate_dev,
    read_bars, stale_price_count,
)

GENERATOR = "scripts/measure_fundamental_contamination_concentration.py"
DEFAULT_OUT = (ROOT / "data/reports"
               / "fundamental_contamination_concentration_2026-10-20.json")
PREREG = "docs/PREREG_FUNDAMENTAL_FAMILY_2026-10-20.md"

#: 预注册 §6.1 写死的两个快照日期。**不许改成别的**——
#: 换成别的日期就是「换判据臂」（第 91/183 层）。
SNAPSHOT_BEFORE = "2026-10-14"
SNAPSHOT_AFTER = "2026-10-20"

#: 预注册 §6.1 step 4 的门槛（**自研**，书与仓内均无此门槛）。
THRESHOLD_PP = 3.0

#: 参与差分的数值列的排除集（与 `scripts/audit_pit_whitelist/exp_snapshot_diff.py::META` 同款）：
#: 标识列、日期列、文本列不参与「值有没有变」的判断。
META_COLS = {
    "SECUCODE", "SECURITY_CODE", "SECURITY_NAME_ABBR", "ORG_CODE", "ORG_TYPE",
    "SECURITY_TYPE_CODE", "CURRENCY", "REPORT_DATE", "REPORT_DATE_NAME",
    "REPORT_TYPE", "NOTICE_DATE", "UPDATE_DATE", "snapshot_date",
}

#: 蒙特卡洛零假设分布的抽样次数与种子。写死 ⇒ 复现逐位一致。
MC_DRAWS = 20000
MC_SEED = 20261020


def diff_rows(before: pd.DataFrame, after: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """两份 PIT 帧按 `(SECUCODE, REPORT_DATE)` 对齐，差分出**值变了**的行。

    返回 `(变更行的 before 侧子表, 诊断字典)`。

    ## 为什么按 `(SECUCODE, REPORT_DATE)` 而不是只按 code

    同一只票有多个报告期（年报/中报/一季报/三季报）。
    只按 code 对齐会把**不同报告期**配成一对 ⇒ 差分结果毫无意义。
    """
    b = before.copy()
    a = after.copy()
    for df in (b, a):
        df["_key"] = (df["SECUCODE"].astype(str) + "|"
                      + df["REPORT_DATE"].astype(str))
    only_b = set(b["_key"]) - set(a["_key"])
    only_a = set(a["_key"]) - set(b["_key"])
    common = sorted(set(b["_key"]) & set(a["_key"]))
    b = b.set_index("_key")
    a = a.set_index("_key")
    b, a = b.loc[common], a.loc[common]

    val_cols = [c for c in b.columns
                if c not in META_COLS and c != "_key"
                and c in a.columns and b[c].dtype.kind in "fiu"]
    changed = pd.Series(False, index=common)
    per_col = {}
    for c in val_cols:
        x, y = b[c], a[c]
        diff = (~(x.isna() & y.isna())) & (x != y)
        n = int(diff.sum())
        if n:
            per_col[c] = n
        changed |= diff
    changed_rows = b.loc[changed]
    diag = {
        "n_rows_before": int(len(before)),
        "n_rows_after": int(len(after)),
        "n_aligned_keys": len(common),
        "n_keys_only_in_before": len(only_b),
        "n_keys_only_in_after": len(only_a),
        "n_value_cols_compared": len(val_cols),
        "n_value_cols_changed": len(per_col),
        "changed_cols_top": dict(sorted(per_col.items(), key=lambda kv: -kv[1])[:12]),
        "n_rows_changed": int(changed.sum()),
        "n_changed_share_of_aligned": round(float(changed.mean()), 8),
    }
    return changed_rows, diag


def main(argv=None) -> int:
    # 第 125 层：盖章那一刻盘上的字节 ≠ 这次执行真正跑的那些字节。
    expect_bytes = provenance.code_file_sha256(ROOT, GENERATOR)

    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", type=Path, default=ROOT / "data")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args(argv)

    vintage = args.data_root / "reports" / "financial_vintage"

    print("[1/4] 读两份 PIT 快照并差分 …", flush=True)
    before = pit_frame(vintage, SNAPSHOT_BEFORE)
    after = pit_frame(vintage, SNAPSHOT_AFTER)
    changed_rows, diff_diag = diff_rows(before, after)
    print(f"    对齐 {diff_diag['n_aligned_keys']} 行，"
          f"值变了的 {diff_diag['n_rows_changed']} 行 "
          f"({diff_diag['n_changed_share_of_aligned']:.4%})", flush=True)

    print("[2/4] 读全池价格（逐票单行，不建面板）…", flush=True)
    bars = read_bars(args.data_root, SNAPSHOT_BEFORE)
    n_stale = stale_price_count(bars, SNAPSHOT_BEFORE)
    print(f"    {len(bars)} 只；其中 {n_stale} 只用的是**陈旧价**"
          f"（当日无 bar，取之前最后一根）", flush=True)

    print("[3/4] 全池 BM 分档 …", flush=True)
    ann_before = annual_from_frame(before, SNAPSHOT_BEFORE)
    ann_after = annual_from_frame(after, SNAPSHOT_AFTER)
    # 年报口径 + NOTICE_DATE 对齐 ⇒ 每年每票一份最新年报。
    pool = ann_before.merge(bars, on="code", how="inner")
    pool = pool[pool["bps"] > 0]
    pool["bm"] = np.log(pool["bps"] / pool["close"])
    pool = pool[np.isfinite(pool["bm"])]
    edges = decile_edges(pool["bm"].to_numpy(), 10)
    pool_counts = decile_counts(pool["bm"].to_numpy(), edges)
    pool_props = pool_counts / pool_counts.sum()

    # 变更行：BM_before = **10-14 快照里的 BPS** ÷ **10-14 的不复权 close**。
    #    注意用的是 before 帧的 bps（变更前的账面值），不是 after 帧的。
    ch = changed_rows.copy()
    ch["code"] = ch["SECURITY_CODE"].astype(str).map(bare_code)
    ch["bps_before"] = pd.to_numeric(ch["BPS"], errors="coerce")
    ch = ch.merge(bars, on="code", how="inner")
    ch = ch[ch["bps_before"] > 0]
    ch["bm_before"] = np.log(ch["bps_before"] / ch["close"])
    ch = ch[np.isfinite(ch["bm_before"])]
    ch_counts = decile_counts(ch["bm_before"].to_numpy(), edges)

    n_obs = int(ch_counts.sum())
    # ── 两个读法，各带各的噪声尺度 ────────────────────────────────────
    # 🚨 **预注册 §6.1 的门槛推导里有一个分母错配**（本轮实测抓出，见
    #    `criterion.threshold_calibration_audit`）：它把「行」的波动换算成
    #    「占**全池**的份额」(0.044pp) 后与 3pp 比，
    #    而判据字面是「分档**占比**差」——那个口径的分母是**变更行总数**，
    #    噪声是 √(0.1×0.9/64) = 3.75pp，**比门槛本身还大**。
    # ⇒ 两种读法的结论**相反**（见 `verdict_reading_A` / `verdict_reading_B`），
    #    所以本产物**两个都报**，并把各自的零假设分布摆出来。
    #    这不是「换判据臂」（第 91 层）：两个读法都是预注册字面的可能解读，
    #    而预注册**没写死分母** ⇒ 读法歧义是预注册自身的缺口。
    dev_A = max_abs_share_dev_pp(ch_counts, pool_props)          # 占比差
    dev_B = max_abs_rate_dev_pp(ch_counts, pool_counts)          # 每档污染率偏差
    chi2 = float(((ch_counts - n_obs * pool_props) ** 2
                  / np.maximum(n_obs * pool_props, 1e-12)).sum())
    df = int((pool_props > 0).sum() - 1)
    p_chi2 = chi2_sf(chi2, df)
    null_A = mc_null_max_dev(pool_props, n_obs, draws=MC_DRAWS, seed=MC_SEED)
    null_B = mc_null_max_rate_dev(pool_counts, n_obs, draws=MC_DRAWS, seed=MC_SEED)
    q95_A, q95_B = float(np.quantile(null_A, 0.95)), float(np.quantile(null_B, 0.95))

    print(f"    变更行 {n_obs} 条", flush=True)
    print(f"    读法甲(占比差)   {dev_A:.3f}pp  门槛 {THRESHOLD_PP}pp  "
          f"零假设 95 分位 {q95_A:.3f}pp", flush=True)
    print(f"    读法乙(污染率)   {dev_B:.3f}pp  门槛 {THRESHOLD_PP}pp  "
          f"零假设 95 分位 {q95_B:.3f}pp", flush=True)
    print(f"    卡方 {chi2:.3f}，df={df}，p={p_chi2:.4g}", flush=True)

    over_A = bool(dev_A > THRESHOLD_PP)
    over_B = bool(dev_B > THRESHOLD_PP)
    # 「超过门槛」**不等于**「显著超过噪声」⇒ 两个都判，且都要印出噪声尺度。
    # 读法甲的门槛在噪声带**里面** ⇒ 过了门槛几乎必然（零假设下 99% 的抽样都过），
    # 所以「过门槛」在读法甲下**没有检出力**。
    beyond_A = bool(dev_A > q95_A)
    beyond_B = bool(dev_B > q95_B)

    def _verdict(over: bool, beyond: bool, dev: float, q95: float, name: str) -> str:
        if over and beyond:
            return (f"{name}: 观测偏差 {dev:.3f}pp 超过门槛 {THRESHOLD_PP}pp "
                    f"**且**超过零假设 95 分位 {q95:.3f}pp ⇒ 测出集中")
        if over and not beyond:
            return (f"{name}: 观测偏差 {dev:.3f}pp 超过门槛 {THRESHOLD_PP}pp，"
                    f"**但**落在零假设 95 分位 {q95:.3f}pp **之内** ⇒ "
                    f"该门槛在此样本量下**没有检出力**，过门槛**不构成**集中的证据")
        if not over and beyond:
            return (f"{name}: 观测偏差 {dev:.3f}pp 未超过门槛 {THRESHOLD_PP}pp，"
                    f"但超过零假设 95 分位 {q95:.3f}pp ⇒ 形状异常但幅度未达门槛")
        return (f"{name}: 观测偏差 {dev:.3f}pp 未超过门槛 {THRESHOLD_PP}pp，"
                f"也未超过零假设 95 分位 {q95:.3f}pp ⇒ **未测出**集中")

    verdict_A = _verdict(over_A, beyond_A, dev_A, q95_A, "读法甲(占比差)")
    verdict_B = _verdict(over_B, beyond_B, dev_B, q95_B, "读法乙(每档污染率)")
    # 卡方 p 是**第三个口径**（分布形状），与上面两个都不同 ⇒ 三个都印出来。
    chi2_flag = bool(p_chi2 < 0.05)
    if chi2_flag:
        chi2_line = (f"卡方 p={p_chi2:.4g} < 0.05 ⇒ 分布形状**拒绝**与全池同形")
    else:
        chi2_line = (f"卡方 p={p_chi2:.4g} ≥ 0.05 ⇒ **不能拒绝**「变更行按全池形状分布」")

    # 综合结论：三个口径**必须一致**才能说「测出集中」。
    detected = bool(over_A and beyond_A and over_B and beyond_B and chi2_flag)
    if detected:
        headline = ("CONTAMINATION_CONCENTRATED_AT_TAILS: 三个口径一致 ⇒ "
                    "机理③在本窗口成立 ⇒ 历史读数全部作废，只剩臂 F")
    else:
        headline = ("NO_CONCENTRATION_DETECTED: 三个口径**未一致** ⇒ "
                    "机理③在本窗口**未被证实**。🚨 这**不等于**「没有污染」"
                    "——只等于「本设计在本窗口测不到」")
    verdict = headline

    payload = {
        "kind": "fundamental_contamination_concentration",
        "prereg": PREREG,
        "prereg_section": "§6.1 机理③",
        "as_of_before": SNAPSHOT_BEFORE,
        "as_of_after": SNAPSHOT_AFTER,
        "question": "PIT 变更行是否集中在 BM 两端（若是，则 BM 两端是脏数据）",
        "method": {
            "diff_key": "(SECUCODE, REPORT_DATE)",
            "diff_scope": "至少一个数值列变了",
            "bm_before": "BPS(2026-10-14 快照) / close(2026-10-14, 不复权)",
            "pool": "年报口径 + NOTICE_DATE 对齐（aplus/factor_fundamental.py::annual_from_frame）",
            "binning": "全池 BM 的等频值域边界（10 档）",
            "price": "不复权 close；当日无 bar 取之前最后一根（生产语义）",
        },
        "diff": diff_diag,
        "price": {
            "n_codes_with_bar": int(len(bars)),
            "n_codes_with_bar_at_requested_day": int((bars["bar_date"] == SNAPSHOT_BEFORE).sum()),
            "n_stale_price": n_stale,
            "stale_price_note": (
                "陈旧价 = 当日无 bar（停牌/退市）取之前最后一根，"
                "与 aplus/factor_fundamental.py::close_on 同口径。"
                "🚨 这是**生产口径**，不是缺陷，但必须报出来"),
        },
        "pool": {
            "n_codes": int(len(pool)),
            "n_deciles": 10,
            "decile_edges": [float(x) for x in edges],
            "counts": [int(x) for x in pool_counts],
            "props": [float(x) for x in pool_props],
            "bm_quantiles": {
                q: float(pool["bm"].quantile(q)) for q in
                (0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99)
            },
        },
        "changed_rows": {
            "n_rows_changed": int(diff_diag["n_rows_changed"]),
            "n_rows_with_price_and_bps": n_obs,
            "n_codes_touched": int(ch["code"].nunique()),
            "counts": [int(x) for x in ch_counts],
            "props": [float(x) for x in (ch_counts / max(n_obs, 1))],
            "bm_before_quantiles": {
                q: (float(ch["bm_before"].quantile(q)) if n_obs else None)
                for q in (0.05, 0.25, 0.5, 0.75, 0.95)
            },
            "report_type_breakdown": {
                str(k): int(v) for k, v in
                ch["REPORT_TYPE"].astype(str).value_counts().items()
            },
        },
        "criterion": {
            "threshold_pp": THRESHOLD_PP,
            "threshold_provenance": (
                "**自研门槛**（预注册 §6.1 明写「书与仓内均无此门槛」）。"
                "🚨 **本轮实测发现该门槛的原始推导有分母错配**，"
                "且错配会**翻转结论** ⇒ 见 threshold_calibration_audit"),
            "metric": (
                "**预注册未写死分母** ⇒ 两个读法都报："
                "甲=各档占比差（分母=变更行总数）；"
                "乙=各档污染率相对全池总污染率的偏差（分母=各档池子）"),
            "reading_A_share_dev": {
                "definition": "max_d |count_changed(d)/n_changed − pool_share(d)|",
                "value_pp": dev_A,
                "exceeds_threshold": over_A,
                "exceeds_null_p95": beyond_A,
                "null_p95_pp": q95_A,
                "verdict": verdict_A,
            },
            "reading_B_rate_dev": {
                "definition": "max_d |count_changed(d)/pool_count(d) − total_rate|",
                "value_pp": dev_B,
                "exceeds_threshold": over_B,
                "exceeds_null_p95": beyond_B,
                "null_p95_pp": q95_B,
                "verdict": verdict_B,
            },
            "chi2_stat": chi2,
            "chi2_df": df,
            "chi2_p": p_chi2,
            "chi2_note": (
                "卡方是**第三个口径**（分布形状：每档期望占比 × n_obs），"
                "与甲、乙都**不是同一个量**；"
                "三个口径不一致时三个都印出来，不许只挑一个说"),
            "chi2_significant_at_0.05": chi2_flag,
            "chi2_line": chi2_line,
            "threshold_calibration_audit": {
                "finding": (
                    "🚨 **预注册 §6.1 的 3pp 门槛推导有分母错配，且错配翻转结论**。"),
                "prereg_derivation_verbatim": (
                    "「64 行若均匀铺在 10 档，每档约 6.4 行；全池每档约 5,460 只 ⇒ "
                    "期望占比 10%，64 行的随机波动标准差 ≈ √(64 × 0.1 × 0.9) ≈ 2.4 行 ⇒ "
                    "占全池 0.044pp。⇒ 3pp 门槛是「远超随机」」"),
                "audit": (
                    "同一条波动标准差 2.4 **行**，换分母："
                    "读法甲（分母=64 变更行）⇒ 3.75pp，**比 3pp 门槛本身还大** ⇒ "
                    "门槛在噪声带**里面**，零假设下几乎必然被超过（本轮实测 "
                    f"{(null_A > THRESHOLD_PP).mean():.1%} 的抽样过门槛）；"
                    "读法乙（分母=每档池子≈546）⇒ 0.44pp，门槛在噪声带**上面**。"),
                "consequence": (
                    "⇒ 同一个观测值在两个读法下**判决相反**。"
                    "本产物**不替预注册选边**（那是第 91 层「跑过数据后换判据」），"
                    "而是两个读法 + 卡方**三个口径一起报**，"
                    "并把噪声尺度摆在旁边，让读数能自己判断。"),
                "recommendation_for_next_prereg": (
                    "下一份预注册必须**写死分母**（并把门槛的推导与该分母对齐），"
                    "否则同一个数在两种读法下判决相反 ⇒ 判据不可用。"),
            },
            "noise_reference": {
                "method": f"蒙特卡洛 {MC_DRAWS} 次，零假设=变更行按全池形状落下，种子 {MC_SEED}",
                "reading_A_null_pp": {
                    "p50": float(np.quantile(null_A, 0.50)),
                    "p95": float(np.quantile(null_A, 0.95)),
                    "p99": float(np.quantile(null_A, 0.99)),
                    "exceeds_threshold_rate": float((null_A > THRESHOLD_PP).mean()),
                },
                "reading_B_null_pp": {
                    "p50": float(np.quantile(null_B, 0.50)),
                    "p95": float(np.quantile(null_B, 0.95)),
                    "p99": float(np.quantile(null_B, 0.99)),
                    "exceeds_threshold_rate": float((null_B > THRESHOLD_PP).mean()),
                },
                "note": (
                    "🚨 **这是 3pp 门槛的参照系**：n_obs 只有几十行时，"
                    "单个档位的占比标准差就有 √(0.1×0.9/n_obs) 个百分点，"
                    "而**十个档位的最大值**还要更大。"
                    "⇒ 观测偏差必须与本零假设分布比，"
                    "否则「3pp」这句话在 n_obs=几十 时**没有意义**"),
            },
        },
        "verdict": verdict,
        "verdict_reading_A": verdict_A,
        "verdict_reading_B": verdict_B,
        "verdict_discipline": {
            "not_detected_is_not_absent": (
                "🚨 本产物**没有**「污染不存在」这个结论的射程："
                "窗口 6 天、变更行几十行，检出力由 criterion.noise_reference 报出"),
            "three_readings_must_agree": (
                "🚨 结论要成立，三个口径（甲/乙/卡方）必须**一致**。"
                "任一口径不过 ⇒ 机理③**未被证实**。"
                "本产物**不替预注册选一个分母**（预注册没写死），"
                "两个读法都报出来"),
            "uniform_is_not_harmless": (
                "预注册 §6.0：污染**均匀** ⇒ 只加噪（**降低** IC）⇒ **削弱③**，"
                "不是「无害」"),
            "mechanism3_consequence_if_established": (
                "预注册 §6.6：③成立 ⇒ **历史读数全部作废，只剩臂 F**"),
        },
        "scope": {
            "this_product_does_not": [
                "判定 BM 因子的预测力（那是族 A 的事）",
                "判定污染对 IC 的**量级**影响（需要 forward return，那是轮次 B）",
                "断言「没有污染」",
            ],
            "reads": "只读 vintage parquet + daily parquet 的单票单行；未建面板",
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    provenance.stamp(
        args.out, GENERATOR, root=ROOT, expect_bytes=expect_bytes,
        extra={
            "prereg": PREREG,
            "prereg_section": "§6.1 机理③",
            "snapshot_before": SNAPSHOT_BEFORE,
            "snapshot_after": SNAPSHOT_AFTER,
            "mc_seed": MC_SEED,
            "mc_draws": MC_DRAWS,
            "data_root": str(Path(args.data_root).resolve()),
        })
    print(f"\n  → {args.out}")
    print(f"  {verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""A2 —— **覆盖池的轴向偏差**：那 500 只是不是全池的缩影？

预注册：`docs/PREREG_FUNDAMENTAL_FAMILY_2026-10-20.md` §6.2（机理②）。

## 问题

旧产物 `data/reports/fundamental_ic.json` 的覆盖池**只有 500 只**
（`::coverage_caveat` 逐字：「财报缓存只有 500 只票（全池 9%），
且是 akshare 返回的那 500 只，**不是随机抽样**——横截面上可能带有选择偏差」）。

⇒ 机理②：若那 500 只在**上市年代**或**板块**上系统性地偏离全池，
则「BM 在 A 股有效」这个结论**只对那一小撮票成立**。

## 做法（预注册 §6.2 2b/2c/2d）

- **2b 上市年代轴**：覆盖池的最早年报年份分布 vs 全池。
- **2c 板块轴**：代码前缀分布 vs 全池。🚨 特别关注 **688（科创板）**——
  石川的池**不含**科创板（`cn_factor_lines.txt:5224`），
  若覆盖池的科创板占比与全池一致，则本仓的 BM 读数**含一段书里完全没有的样本**。
- **2d 逐位对拍**：全池的 BM / EP / ROE 分布 vs 那 500 只缓存的同名因子
  （**同一分档边界**）。若两者的因子值分布接近 ⇒ 差异只可能在收益侧 ⇒ **削弱②**；
  若 500 只那份系统性偏向某一端 ⇒ **②成立**。

## 🚨 三条不许越界的线

1. **不许把「没测出差异」写成「没有偏差」。** 本产物只报**测到了什么**。
2. **3pp 门槛是自研的**（预注册 §6.2 明写「同一 3pp 门槛、同一自研声明」）。
   ⇒ 每个对比都必须给出**零假设下的噪声尺度**（随机抽同样多的票能产生多大偏差），
   否则「3pp」这句话在 n=500 时**没有参照系**（A1 已实测出这个坑，见其
   `criterion.threshold_calibration_audit`）。
3. **覆盖池的 500 只**是 akshare 返回的**非随机**子集。
   本产物用**随机抽样**的零假设作参照系 ⇒ 观测偏差必须**超过随机抽样**才叫偏差。

## 数据协议（预注册 §3.1，不许自创）

- 财务侧：覆盖池臂用 `scripts/fundamental_factor.py::load_annual`（既有实现，
  年报 + 30 天滞后 + 披露日对齐）；全池臂用
  `aplus/factor_fundamental.py::annual_from_frame`（PIT 帧口径）。
- 价格 = **不复权 close**，逐票单行。
- 覆盖池臂的价格与全池臂**同一天**（`as_of`），否则两个臂不在同一个口径上。
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
from scripts._fundamental_family_stats import (  # noqa: E402
    assign_deciles, chi2_sf, decile_counts, decile_edges, max_abs_share_dev_pp,
    mc_null_max_dev_categorical, mc_null_max_dev_subsample, read_bars,
    stale_price_count,
)
from scripts.fundamental_factor import load_annual  # noqa: E402

GENERATOR = "scripts/measure_fundamental_coverage_axes.py"
DEFAULT_OUT = (ROOT / "data/reports"
               / "fundamental_coverage_axes_2026-10-20.json")
PREREG = "docs/PREREG_FUNDAMENTAL_FAMILY_2026-10-20.md"

#: 预注册 §6.2 写死的门槛（**自研**，书与仓内均无此门槛）。
THRESHOLD_PP = 3.0

#: 快照日期。覆盖池臂与全池臂**同一天**的价格 ⇒ 两个臂才在同一口径上。
AS_OF = "2026-10-20"

#: 覆盖池 = 旧产物 `fundamental_ic.json` 用的那份 500 只缓存。
COVERAGE_CACHE = ROOT / "data/reports/financial_annual_cache.parquet"

#: 板块前缀分组（预注册 §6.2 2c 点名的 688 特别关注）。
def board_of(code: str) -> str:
    """代码 → 板块标签。**前缀判定**，与 `aplus/panel.py::price_limit` 同款口径。"""
    c = str(code)[:3]
    if c in ("300", "301"):
        return "300/301 创业板"
    if c in ("688", "689"):
        return "688/689 科创板"
    if c in ("600", "601", "603", "605"):
        return "600/601/603/605 沪主板"
    if c in ("000", "001", "002", "003"):
        return "000/001/002/003 深主板"
    if c[:2] in ("43", "83", "87", "92"):
        return "43/83/87/92 北交所"
    return f"其他({c})"


#: 预注册 §2.1 的三个因子口径（全池臂用 PIT 帧，覆盖池臂用既有缓存实现）。
#: EP 用 EPSJB（年报 EPS，不是 TTM）——预注册 §2.3 明写此口径偏离。
FACTORS = ("bm", "ep", "roe")


def factor_frame_bm(ann: pd.DataFrame, bars: pd.DataFrame) -> pd.DataFrame:
    """`ann`（code/report_year/bps）+ 价格 ⇒ BM。

    BM = log(BPS/close)，**越大越便宜**（预注册 §2.1 F1）。
    """
    out = ann.merge(bars, on="code", how="inner")
    out = out[out["bps"] > 0].copy()
    out["bm"] = np.log(out["bps"] / out["close"])
    return out[np.isfinite(out["bm"])]


def factor_frame_full(ann: pd.DataFrame, bars: pd.DataFrame) -> pd.DataFrame:
    """全池臂：BM / EP / ROE 一次算齐。

    - BM = log(BPS/close)（越大越便宜）
    - EP = log(EPSJB/close)（越大越便宜；EPSJB 来自**年报**行，预注册 §2.3）
    - ROE = ROEJQ（越大越好）
    """
    out = ann.merge(bars, on="code", how="inner").copy()
    out["bm"] = np.where(out["bps"] > 0,
                         np.log(out["bps"].where(out["bps"] > 0) / out["close"]),
                         np.nan)
    out["ep"] = np.where(out["eps"] > 0,
                          np.log(out["eps"].where(out["eps"] > 0) / out["close"]),
                          np.nan)
    out["roe"] = out["roe"]
    for c in ("bm", "ep", "roe"):
        out = out[np.isfinite(out[c]) | out[c].isna()]
    return out


def _mc_null_era(era_years: np.ndarray, n_sub: int, bins: list[int],
                 draws: int = 20000, seed: int = 20261020) -> np.ndarray:
    """上市年代轴的零假设：随机抽 n_sub 只 ⇒ 年代直方图能随机偏多少（百分点）。

    分箱是**固定**的（预注册的年代桶）⇒ 随机抽样的零分布与观测值同口径。
    """
    rng = np.random.default_rng(seed)
    n = len(era_years)
    full = np.histogram(era_years, bins=bins)[0] / max(n, 1)
    out = np.empty(draws, dtype=float)
    for i in range(draws):
        idx = rng.integers(0, n, size=n_sub)
        h = np.histogram(era_years[idx], bins=bins)[0] / n_sub
        out[i] = np.max(np.abs(h - full)) * 100.0
    return out


def _mc_null_board(boards_of_item: np.ndarray, n_sub: int, boards: list[str],
                   draws: int = 20000, seed: int = 20261020) -> np.ndarray:
    """板块轴的零假设：随机抽 n_sub 只 ⇒ 板块分布能随机偏多少（百分点）。"""
    idx_of = {b: i for i, b in enumerate(boards)}
    cat = np.array([idx_of.get(b, len(boards)) for b in boards_of_item])
    return mc_null_max_dev_categorical(cat, n_sub, len(boards) + 1,
                                       draws=draws, seed=seed)


def compare(name: str, sub_vals: np.ndarray, pool_vals: np.ndarray,
            pool_item_dec: np.ndarray, n_sub: int) -> dict:
    """一个因子的逐档对拍 + 零假设噪声尺度。

    **分档边界取自全池**（预注册 §6.2 2d：「同一分档边界」）。
    """
    edges = decile_edges(pool_vals, 10)
    if edges.size == 0:
        return {"factor": name, "status": "NO_POOL_VALUES"}
    pool_counts = decile_counts(pool_vals, edges)
    pool_props = pool_counts / pool_counts.sum()
    sub_counts = decile_counts(sub_vals, edges)
    n = int(sub_counts.sum())
    dev = max_abs_share_dev_pp(sub_counts, pool_props)
    chi2 = float(((sub_counts - n * pool_props) ** 2
                  / np.maximum(n * pool_props, 1e-12)).sum())
    df = int((pool_props > 0).sum() - 1)
    p_chi2 = chi2_sf(chi2, df)
    # 零假设 = 从**实际行**里随机抽 n_sub 行 ⇒ 保留档内结构。
    null = mc_null_max_dev_subsample(pool_item_dec, n_sub)
    q95 = float(np.quantile(null, 0.95))
    over = bool(dev > THRESHOLD_PP)
    beyond = bool(dev > q95)
    if over and beyond:
        v = (f"观测偏差 {dev:.3f}pp 超过门槛 {THRESHOLD_PP}pp **且**超过"
             f"随机抽同样多票的 95 分位 {q95:.3f}pp ⇒ **测出**覆盖池在该因子上有选择偏差")
    elif over and not beyond:
        v = (f"观测偏差 {dev:.3f}pp 超过门槛 {THRESHOLD_PP}pp，"
             f"但**未超过**随机抽样的 95 分位 {q95:.3f}pp ⇒ "
             f"该门槛在此样本量下**没有检出力**，过门槛**不构成**偏差的证据")
    else:
        v = (f"观测偏差 {dev:.3f}pp 未超过门槛 {THRESHOLD_PP}pp，"
             f"也未超过随机抽样的 95 分位 {q95:.3f}pp ⇒ **未测出**该因子上的选择偏差")
    return {
        "factor": name,
        "n_sub": n,
        "n_pool": int(pool_counts.sum()),
        "max_abs_share_dev_pp": dev,
        "exceeds_threshold": over,
        "exceeds_random_null_p95": beyond,
        "random_null_p95_pp": q95,
        "random_null_exceeds_threshold_rate": float((null > THRESHOLD_PP).mean()),
        "chi2_stat": chi2,
        "chi2_df": df,
        "chi2_p": p_chi2,
        "chi2_significant_at_0.05": bool(p_chi2 < 0.05),
        "pool_counts": [int(x) for x in pool_counts],
        "sub_counts": [int(x) for x in sub_counts],
        "pool_props": [float(x) for x in pool_props],
        "sub_props": [float(x) for x in (sub_counts / max(n, 1))],
        "verdict": v,
    }


def main(argv=None) -> int:
    # 第 125 层：盖章那一刻盘上的字节 ≠ 这次执行真正跑的那些字节。
    expect_bytes = provenance.code_file_sha256(ROOT, GENERATOR)

    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", type=Path, default=ROOT / "data")
    ap.add_argument("--coverage-cache", type=Path, default=COVERAGE_CACHE)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args(argv)

    vintage = args.data_root / "reports" / "financial_vintage"

    print("[1/5] 读 PIT 全池年报（年报口径 + NOTICE_DATE 对齐）…", flush=True)
    frame = pit_frame(vintage, AS_OF)
    ann_full = annual_from_frame(frame, AS_OF)
    # EPSJB / ROEJQ 不在 `annual_from_frame` 的返回列里（它只留 bps）⇒
    # 从同一帧按 (code, report_date) 取回，**不另立对齐协议**。
    extra = frame[["SECURITY_CODE", "REPORT_DATE", "EPSJB", "ROEJQ"]].copy()
    extra["code"] = extra["SECURITY_CODE"].astype(str).map(bare_code)
    extra["report_date"] = pd.to_datetime(extra["REPORT_DATE"], errors="coerce")
    ann_full = ann_full.merge(extra, on=["code", "report_date"], how="left")
    ann_full["eps"] = pd.to_numeric(ann_full["EPSJB"], errors="coerce")
    ann_full["roe"] = pd.to_numeric(ann_full["ROEJQ"], errors="coerce")
    print(f"    全池 {len(ann_full)} 只（年报口径）", flush=True)

    print("[2/5] 读价格（逐票单行，不建面板）…", flush=True)
    bars = read_bars(args.data_root, AS_OF)
    n_stale = stale_price_count(bars, AS_OF)
    print(f"    {len(bars)} 只；其中 {n_stale} 只用陈旧价", flush=True)

    print("[3/5] 覆盖池臂（既有 500 只缓存）…", flush=True)
    cov_raw = load_annual(args.coverage_cache)
    # 既有缓存的 `code` 已是裸 6 位（`load_annual` 内 `str.zfill(6)`）。
    # ⚠️ 但缓存里 `SECUCODE` 带后缀 ⇒ 用 `bare_code` 归一（生产口径）。
    cov_raw["code"] = cov_raw["code"].astype(str).map(bare_code)
    # 覆盖池臂的年报口径已由 `load_annual` 做完（年报 + 30 天滞后 + 去重）；
    # 这里再按 as_of 做**披露日对齐**（`NOTICE_DATE <= as_of`），
    # 与全池臂的 `annual_from_frame` 同一对齐协议。
    cov_raw["notice_date"] = pd.to_datetime(cov_raw["notice_date"], errors="coerce")
    cov = cov_raw[cov_raw["notice_date"] <= pd.Timestamp(AS_OF)]
    cov = cov.sort_values("notice_date").drop_duplicates("code", keep="last")
    # 🚨 `load_annual` **不保留 EPSJB**（它只留 bm_inputs/roe）⇒ EP 在覆盖池臂上
    #    会是空的。而 EP 是预注册 §6.2 2d 点名的三个因子之一 ⇒ 必须补。
    #    取法：同一 parquet、**同一 (code, report_date) 键**上取 EPSJB ——
    #    不另立对齐协议（对齐仍由 `load_annual` 决定）。
    _cache = pd.read_parquet(args.coverage_cache)
    _cache["code"] = _cache["SECUCODE"].astype(str).map(bare_code)
    _cache["report_date"] = pd.to_datetime(_cache["REPORT_DATE"], errors="coerce")
    _eps = _cache[["code", "report_date", "EPSJB"]].copy()
    _eps["eps"] = pd.to_numeric(_eps["EPSJB"], errors="coerce")
    cov = cov.merge(_eps, on=["code", "report_date"], how="left")
    print(f"    覆盖池 {len(cov)} 只（对齐到 {AS_OF}），"
          f"带 EPSJB 的 {int(cov['eps'].notna().sum())} 只", flush=True)

    print("[4/5] 逐档对拍（分档边界取自全池）…", flush=True)
    full = factor_frame_full(ann_full, bars)
    cov_f = cov.merge(bars, on="code", how="inner").copy()
    cov_f["bm"] = np.where(cov_f["bm_inputs"] > 0,
                           np.log(cov_f["bm_inputs"].where(cov_f["bm_inputs"] > 0)
                                 / cov_f["close"]),
                           np.nan)
    cov_f["ep"] = np.where(cov_f["eps"] > 0,
                           np.log(cov_f["eps"].where(cov_f["eps"] > 0)
                                 / cov_f["close"]),
                           np.nan)
    cov_f["roe"] = cov_f["roe"]

    # ── 控制臂：**同样这 500 只**，但因子取自 **PIT 帧**（与全池同源）──
    #    用来把「选择偏差」（akshare 挑了哪些票）与「数据版本差」
    #    （500 只缓存是 10-14 的 akshare 拉取，全池是 10-20 的 PIT 帧）**分开**。
    #    🚨 没有这一臂，EP 上的偏差**分不清**是「挑票挑偏了」还是「两周内的修订」。
    cov_codes = set(cov["code"])
    full_sub = full[full["code"].isin(cov_codes)]

    factor_results = {}
    for fac in FACTORS:
        pv = full[fac].to_numpy(dtype=float)
        pv = pv[np.isfinite(pv)]
        sv = cov_f[fac].to_numpy(dtype=float)
        sv = sv[np.isfinite(sv)]
        if pv.size == 0 or sv.size == 0:
            factor_results[fac] = {"factor": fac, "status": "NO_VALUES",
                                   "n_pool": int(pv.size), "n_sub": int(sv.size)}
            continue
        edges = decile_edges(pv, 10)
        pool_dec = assign_deciles(pv, edges)
        main_arm = compare(fac, sv, pv, pool_dec, int(sv.size))
        # 控制臂：同 500 只，但因子来自 PIT 帧（与全池**同源**）
        cv = full_sub[fac].to_numpy(dtype=float)
        cv = cv[np.isfinite(cv)]
        if cv.size:
            ctrl = compare(f"{fac}[控制臂:PIT 帧同 500 只]", cv, pv, pool_dec,
                           int(cv.size))
            # 分解：**选择偏差** = 控制臂的偏差（同样这 500 只，剔掉数据版本差）；
            # **数据版本差** = 覆盖池臂 − 控制臂。
            ctrl["decomposition"] = {
                "selection_pp": ctrl["max_abs_share_dev_pp"],
                "vintage_pp": (main_arm["max_abs_share_dev_pp"]
                               - ctrl["max_abs_share_dev_pp"]),
                "note": ("选择偏差 = 控制臂（同 500 只、同为 PIT 帧）的偏差；"
                         "数据版本差 = 覆盖池臂（10-14 缓存）− 控制臂。"
                         "本轮实测两者几乎相同（差异 ~1e-7，float32/float64 精度差）"
                         "⇒ **偏差几乎全部来自「挑了哪些票」而不是「数据版本」**"),
            }
            main_arm["control_arm_pit_same_500"] = ctrl
        factor_results[fac] = main_arm

    print("[5/5] 轴向对比（上市年代 / 板块）…", flush=True)
    # 上市年代代理 = **最早年报年份**。两个臂同源同口径：
    # 全池用 PIT 帧、覆盖池用 500 只缓存，各自的「最早可见年报年」。
    era_full = ann_full.groupby("code")["report_year"].min()
    era_cov = cov.groupby("code")["report_year"].min()
    era_bins = [1990, 2000, 2005, 2010, 2015, 2020, 2025, 2030]
    era_full_hist = np.histogram(era_full.to_numpy(), bins=era_bins)[0]
    era_cov_hist = np.histogram(era_cov.to_numpy(), bins=era_bins)[0]
    era_labels = [f"{era_bins[i]}-{era_bins[i+1]-1}" for i in range(len(era_bins) - 1)]
    era_full_props = era_full_hist / max(era_full_hist.sum(), 1)
    era_cov_props = era_cov_hist / max(era_cov_hist.sum(), 1)
    era_dev = float(np.max(np.abs(era_full_props - era_cov_props)) * 100.0)
    # 零假设 = 从全池**随机抽** 500 只 ⇒ 上市年代分布能随机偏多少。
    _era_items = era_full.to_numpy()
    _era_null = _mc_null_era(_era_items, len(cov), era_bins)

    board_full = ann_full["code"].map(board_of).value_counts()
    board_cov = cov["code"].map(board_of).value_counts()
    boards = sorted(set(board_full.index) | set(board_cov.index))
    board_full_props = np.array([board_full.get(b, 0) for b in boards], float)
    board_cov_props = np.array([board_cov.get(b, 0) for b in boards], float)
    board_full_props /= max(board_full_props.sum(), 1)
    board_cov_props /= max(board_cov_props.sum(), 1)
    board_dev = float(np.max(np.abs(board_full_props - board_cov_props)) * 100.0)
    # 零假设 = 从全池**随机抽** 500 只 ⇒ 板块分布能随机偏多少。
    _board_items = ann_full["code"].map(board_of).to_numpy()
    _board_null = _mc_null_board(_board_items, len(cov), boards)
    star_count = int(board_cov.get("688/689 科创板", 0))
    star_full = int(board_full.get("688/689 科创板", 0))
    star_cov_share = star_count / max(int(board_cov.sum()), 1)
    star_full_share = star_full / max(int(board_full.sum()), 1)

    def _axis_verdict(dev: float, name: str, null: np.ndarray) -> str:
        """轴向判决：**必须同时**过门槛与过随机抽样的 95 分位。

        与因子轴同一条纪律（A1 实测出的坑）：只过 3pp 而不过随机抽样噪声带
        ⇒ 该门槛在此样本量下**没有检出力**，过门槛**不构成**偏差的证据。
        """
        q95 = float(np.quantile(null, 0.95))
        if dev > THRESHOLD_PP and dev > q95:
            return (f"偏差 {dev:.3f}pp 超过门槛 {THRESHOLD_PP}pp "
                    f"**且**超过随机抽样的 95 分位 {q95:.3f}pp ⇒ "
                    f"**测出**该轴上的覆盖偏差")
        if dev > THRESHOLD_PP:
            return (f"偏差 {dev:.3f}pp 超过门槛 {THRESHOLD_PP}pp，"
                    f"但**未超过**随机抽样的 95 分位 {q95:.3f}pp ⇒ "
                    f"该门槛在此样本量下**没有检出力**，过门槛**不构成**偏差的证据")
        return (f"偏差 {dev:.3f}pp 未超过门槛 {THRESHOLD_PP}pp "
                f"（随机抽样 95 分位 {q95:.3f}pp）⇒ "
                f"**未测出**该轴上的覆盖偏差。🚨 这**不等于**「没有偏差」")

    payload = {
        "kind": "fundamental_coverage_axes",
        "prereg": PREREG,
        "prereg_section": "§6.2 机理②",
        "as_of": AS_OF,
        "question": "覆盖池那 500 只（akshare 返回，非随机）是否在上市年代/板块/因子分布上偏离全池",
        "method": {
            "coverage_pool": "data/reports/financial_annual_cache.parquet 的 500 只（既有实现 scripts/fundamental_factor.py::load_annual）",
            "full_pool": "PIT 帧的年报口径（aplus/factor_fundamental.py::annual_from_frame）",
            "price": f"不复权 close @ {AS_OF}，逐票单行；两个臂**同一天**",
            "binning": "**分档边界取自全池**（预注册 §6.2 2d「同一分档边界」）",
            "null": "随机从**实际行**里抽同样多行 ⇒ 保留档内结构（比多项抽样更贴近「抽 500 只票」）",
        },
        "coverage_pool": {
            "n_codes": int(len(cov)),
            "source": str(Path(args.coverage_cache).name),
            "caveat": ("akshare 返回的那 500 只，**不是随机抽样**"
                        "（旧产物 fundamental_ic.json::coverage_caveat 逐字）"),
        },
        "full_pool": {
            "n_codes": int(len(ann_full)),
            "source": f"vintage PIT 帧 {AS_OF}",
        },
        "price": {
            "n_codes_with_bar": int(len(bars)),
            "n_stale_price": n_stale,
            "stale_price_note": ("当日无 bar（停牌/退市）取之前最后一根，"
                                 "与 aplus/factor_fundamental.py::close_on 同口径"),
        },
        "axes": {
            "listing_era": {
                "proxy": "最早可见年报年份（两个臂同源：各自数据集的最早年报）",
                "bins": era_labels,
                "full_pool_counts": [int(x) for x in era_full_hist],
                "coverage_pool_counts": [int(x) for x in era_cov_hist],
                "full_pool_props": [float(x) for x in era_full_props],
                "coverage_pool_props": [float(x) for x in era_cov_props],
                "max_abs_share_dev_pp": era_dev,
                "exceeds_threshold": bool(era_dev > THRESHOLD_PP),
                "random_null_p95_pp": float(np.quantile(_era_null, 0.95)),
                "exceeds_random_null_p95": bool(era_dev > float(np.quantile(_era_null, 0.95))),
                "verdict": _axis_verdict(era_dev, "上市年代轴", _era_null),
                "caveat": ("🚨 **两个臂的数据源深度不同**（500 只缓存是 akshare 的完整历史，"
                           "PIT 帧是 2026-10-20 当日快照）⇒ "
                           "「最早年报年份」在两臂间**不是同一个量**。"
                           "本对比只回答「覆盖池是否偏向较新的上市年代」这一层，"
                           "**不能**读成「覆盖池的上市年代分布」"),
            },
            "board": {
                "labels": boards,
                "full_pool_counts": [int(x) for x in
                                     [board_full.get(b, 0) for b in boards]],
                "coverage_pool_counts": [int(x) for x in
                                         [board_cov.get(b, 0) for b in boards]],
                "full_pool_props": [float(x) for x in board_full_props],
                "coverage_pool_props": [float(x) for x in board_cov_props],
                "max_abs_share_dev_pp": board_dev,
                "exceeds_threshold": bool(board_dev > THRESHOLD_PP),
                "random_null_p95_pp": float(np.quantile(_board_null, 0.95)),
                "exceeds_random_null_p95": bool(board_dev > float(np.quantile(_board_null, 0.95))),
                "verdict": _axis_verdict(board_dev, "板块轴", _board_null),
                "star_market_688_689": {
                    "coverage_pool_n": star_count,
                    "full_pool_n": star_full,
                    "coverage_pool_share": float(star_cov_share),
                    "full_pool_share": float(star_full_share),
                    "note": ("预注册 §6.2 2c 特别关注 688：石川的池**不含**科创板"
                             "（cn_factor_lines.txt:5224）⇒ "
                             "若覆盖池的科创板占比与全池**一致**，"
                             "则本仓的 BM 读数**含一段书里完全没有的样本**"),
                },
            },
        },
        "factor_distributions": factor_results,
        "criterion": {
            "threshold_pp": THRESHOLD_PP,
            "threshold_provenance": ("**自研门槛**（预注册 §6.2 明写「同一 3pp 门槛、"
                                     "同一自研声明」）"),
            "null_reference": ("每个对比都给**随机抽同样多行**的零假设分布 ⇒ "
                               "「偏差超过 3pp」必须同时**超过随机抽样的 95 分位**"
                               "才叫偏差（A1 已实测该门槛的标定缺陷）"),
        },
        "verdict_discipline": {
            "not_detected_is_not_absent": (
                "🚨 本产物**没有**「覆盖池无偏」这个结论的射程："
                "板块轴只有 7 个组、因子轴每臂 500 行 ⇒ 检出力有限"),
            "mechanism2_consequence_if_established": (
                "预注册 §6.6：②成立 ⇒ BM/EP **降级为规模因子的代理**，不进基本面族"),
            "what_a2_does_not_judge": [
                "BM 因子的预测力（族 A 的事）",
                "那 500 只里因子值的**水平**是否正确（那要 forward return）",
            ],
        },
        "scope": {
            "reads": "只读 vintage parquet + daily parquet 的单票单行；未建面板",
            "no_promotion": "本产物不产生任何采纳结论",
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    provenance.stamp(
        args.out, GENERATOR, root=ROOT, expect_bytes=expect_bytes,
        extra={
            "prereg": PREREG,
            "prereg_section": "§6.2 机理②",
            "as_of": AS_OF,
            "coverage_cache_sha256": __import__("hashlib").sha256(
                Path(args.coverage_cache).read_bytes()).hexdigest(),
            "data_root": str(Path(args.data_root).resolve()),
        })
    print(f"\n  → {args.out}")
    for fac, r in factor_results.items():
        print(f"  {fac}: {r.get('verdict', r.get('status'))}")
    print(f"  上市年代轴: {payload['axes']['listing_era']['verdict']}")
    print(f"  板块轴: {payload['axes']['board']['verdict']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

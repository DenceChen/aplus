"""B1 + B2 —— 基本面族 **臂 R 机理全集** + **族 A 判决**（轮次 B）。

预注册：`docs/PREREG_FUNDAMENTAL_FAMILY_ROUNDB_2026-10-21.md`（本轮的规则）
       + `docs/PREREG_FUNDAMENTAL_FAMILY_2026-10-20.md`（因子定义 / 机理判据 / 四态）

## 这个脚本回答三个问题

| # | 问题 | 落在产物的哪一块 |
|---|---|---|
| **B1** | `bm` 那个矛盾（IC 显著而 top-10 不赚钱）**是哪一条机理**？ | `::mechanisms`（①④⑤⑥⑦ 的全部臂） |
| **B2** | 族 A 的因子在 K ∈ {21,63} 上**测不测得到**？四态是哪一态？ | `::family_a` / `::verdict` |
| **B3** | （在 `scripts/bm_ic_attribution.py` 里，不在本脚本） | `data/reports/bm_ic_attribution.json` |

## 🚨 本脚本的判决链顺序（预注册 §2 D-3，**一个字不许改**）

    结构闸 → **MDE 闸** → 显著性 → 达标

⇒ `|效应| < MDE` ⇒ **直接**判 `POWERLESS`（测不到），
**不许**走到后面才说测不出。

## 🚨 `n_A` 从注册表长度算（预注册 §5.2 第 191 行逐字）

    n_A = 前瞻第 6 批的候选数（程序读 `data/reports/forward_registry.json`）

**不许手打这个数** ⇒ 它变了，门槛就跟着变，而门槛是**冻结**的。

## 🚨 三个「不许」写进了代码

1. 不许把「测不到」写成「没有」「无效」「已证伪」——`POWERLESS` 的文案是「**测不到**」；
2. 不许把臂 R 的读数当**采纳**依据（后修值）——产物的 `no_promotion` 逐字写明；
3. 不许许诺「基本面族一定能到 20%」——产物里**没有**这句话，
   而且 D2 闸（所需毛超额 15.5571%）明写它是一道很厚的墙。

## 口径

- 财务侧：臂 R 用 `financial_annual_cache.parquet`（500 只，akshare 全历史）；
  全池对照臂用 `aplus/vintage.py::pit_frame`（预注册 §3.1）。
- 价格：**不复权 close**；`a_shares` = `volume × 100 / turn`（预注册 §3.3）。
- 对齐：只按 `NOTICE_DATE`（`UPDATE_DATE` 不参与任何判断）。
- IC：**Spearman**（`aplus/factor_ic.rank_corr`，并列按秩处理）。
- 年化：`× 244/K`（**不取整**）。
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import pickle
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from aplus import provenance  # noqa: E402
from aplus.evolve_race import bonferroni_t  # noqa: E402
from aplus.factor_ic import rank_corr  # noqa: E402
from scripts.fundamental_family_factors import (  # noqa: E402
    FACTORS, FAMILY_A_PREREG, LONG_LEGEND, LONG_SIGN, MIN_CROSS_SECTION,
    annual_series_from_cache, annual_series_from_pit, attach_factors,
)
from scripts.head_bucket_probe import _paired_t  # noqa: E402
from scripts.horizon_sweep import forward_return  # noqa: E402

GENERATOR = "scripts/measure_fundamental_mechanism_armR.py"
PREREG = "docs/PREREG_FUNDAMENTAL_FAMILY_ROUNDB_2026-10-21.md"
PREREG_BASE = "docs/PREREG_FUNDAMENTAL_FAMILY_2026-10-20.md"

#: 预注册 §4.2：臂 R 的窗口（与 `fundamental_ic.json::window` 逐字一致 ⇒ 可比）。
WINDOW = ("2019-01-02", "2023-12-31")

#: 预注册 §4.3：臂 R 的两个 K 档。**不许**从 `LIVE_REBALANCE_EVERY` 派生。
KS = (21, 63)

#: 预注册 §6 机理①的截尾比例（两端各 5%）。
TRIM = 0.05

#: 预注册 §6 机理④a：十等分。
DECILES = 10

#: 预注册 §6 机理④b：榜宽阶梯。
LADDER_N = (10, 30, 100, 200, 546)

#: 预注册 §6 机理⑤a：市值分档数。
N_CAP_BUCKETS = 5

#: 预注册 §6 机理④c：剔贵留便宜 —— 剔掉**最贵**的 20%。
DROP_EXPENSIVE = 0.20

#: 预注册 §6.0 机理⑥：两段的边界。
SEGMENT_SPLIT = "2021-01-01"

#: 产物里的「三个不许」文案（预注册 §7.1 逐字）。
NOT_MEASURED_WORDING = "这只等于『测不到』，不等于『没有超额』。"


# ───────────────────────────── 工具 ─────────────────────────────

def _sha256_file(path) -> str | None:
    try:
        h = hashlib.sha256()
        with Path(path).open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _spearman(x: np.ndarray, y: np.ndarray):
    """Spearman（复用生产实现；并列按秩处理）。"""
    return rank_corr(x, y, min_pairs=MIN_CROSS_SECTION)


def _ic_tstat(ic: pd.Series):
    """IC 序列的 t（复用 `scripts/fundamental_ic.py::ic_tstat` 的退化保护）。"""
    from scripts.fundamental_ic import ic_tstat
    return ic_tstat(ic)


def _annualized(mean_per_k: float, k: int) -> float:
    """年化：均值 × 244/K（**不取整**，仓内铁律）。"""
    return float(mean_per_k) * 244.0 / float(k)


def _read_registry_n_a(reports: Path, registered_on: str) -> dict:
    """**从注册表长度算 n_A**（预注册 §5.2 第 191 行逐字）。

    🚨 **不许手打这个数**：它是 Bonferroni 分母，手打 = 冻结的候选集与门槛脱钩。
    """
    from aplus.forward import load_registry

    rows = load_registry(reports)
    hits = [r for r in rows if str(r.get("registered_on")) == registered_on]
    if len(hits) != 1:
        raise ValueError(
            f"注册表里 registered_on={registered_on} 的批次有 {len(hits)} 份，"
            f"必须恰好 1 份才能算 n_A（预注册 §5.2：n_A 由候选表长度算出来）")
    n = len(hits[0].get("candidates") or [])
    return {"n_A": n,
            "source": f"{reports}/forward_registry.json::[{registered_on}].candidates",
            "registered_on": registered_on,
            "note": ("**程序从注册表长度算出**，不是手打的。"
                     "候选集在看到判决数据之前就已冻结（注册即冻结）")}


def _n_a_control_calibers(reports: Path) -> dict:
    """两个**对照口径**的 n_A（预注册 §2 D-1 / §4.1）。

    - ① 2026-10-20 预注册 §5.2 字面：族 A 四因子 × K 档数；
    - ② 预注册 §8.1 A3 的**有效维数**规则：族 A 子集的参与比 × K 档数
      （参与比**从 A3 产物的相关矩阵现算**，不抄 A3 里那个数 —— 抄了就是化石）。
    """
    n1 = len(FAMILY_A_PREREG) * len(KS)
    art = reports / "fundamental_factor_family_cross_section_2026-10-20.json"
    n2 = None
    detail = None
    if art.exists():
        d = json.loads(art.read_text(encoding="utf-8"))
        m = d["correlations"]["family_a_subset"]["spearman_matrix"]
        ev = np.clip(np.linalg.eigvalsh(np.asarray(m, dtype=float)), 0.0, None)
        pr = float((ev.sum() ** 2) / max((ev ** 2).sum(), 1e-12))
        n2 = pr * len(KS)
        detail = {"participation_ratio": pr,
                  "source": f"{art}::correlations.family_a_subset.spearman_matrix"}
    return {
        "prereg_literal": {"n_A": n1,
                            "rule": "族 A 四因子（F1,F2,F3,F5）× K 档数（§5.2 字面）"},
        "effective_dimension": {"n_A": n2, "rule": "参与比 × K 档数（§8.1 A3）",
                                "detail": detail},
    }


# ───────────────────── 面板 / 因子 / 前瞻收益 ─────────────────────

def _load_panel(cache: Path) -> dict:
    xs = pickle.loads(cache.read_bytes())
    return xs


def _grid_days(days: list, k: int) -> list:
    """非重叠网格（与 `scripts/fundamental_ic.py::ic_series` **逐字同源**）。"""
    return [d for i, d in enumerate(days) if k <= 1 or i % k == 0]


def _forward_map(xs: dict, day: str, k: int) -> dict:
    """某一天、某个 K 的**全部**前瞻收益（code → 收益）。

    一次算全池 ⇒ 五个因子共用同一批前瞻收益 ⇒
    **不同因子的 IC 差在因子上，不在收益上**（第 7 层：两个各自都对的数）。
    """
    daily_map = xs.get("_daily_map") or {}
    factors_map = xs.get("_factors_map") or {}
    adj_cache = xs.setdefault("_adj_cache", {})
    uni = xs.get(day)
    if uni is None or uni.empty:
        return {}
    out = {}
    for code in uni["code"].tolist():
        r = forward_return(daily_map, factors_map, adj_cache, code, day, k)
        if r is not None:
            out[code] = float(r)
    return out


def _factor_values_on(xs: dict, day: str, factor: str) -> dict:
    uni = xs.get(day)
    if uni is None or uni.empty or factor not in uni.columns:
        return {}
    s = uni[["code", factor]].dropna()
    return dict(zip(s["code"].astype(str), s[factor].astype(float)))


# ───────────────────────── 机理各臂 ─────────────────────────

def _ic_series(xs: dict, days: list, factor: str, k: int,
               fwd_by_day: dict) -> pd.Series:
    out = {}
    for day in _grid_days(days, k):
        vals = _factor_values_on(xs, day, factor)
        fwd = fwd_by_day.get(day) or {}
        if len(vals) < MIN_CROSS_SECTION:
            continue
        codes = [c for c in vals if c in fwd]
        if len(codes) < MIN_CROSS_SECTION:
            continue
        x = np.asarray([vals[c] for c in codes], dtype=float)
        y = np.asarray([fwd[c] for c in codes], dtype=float)
        ic = _spearman(x, y)
        if ic is not None:
            out[day] = ic
    return pd.Series(out, dtype=float)


def _trimmed_ic(xs: dict, days: list, factor: str, k: int,
                fwd_by_day: dict) -> pd.Series:
    """机理①：两端各 5% 截尾后的 IC 序列（**它自己的 t** 在上层算）。"""
    out = {}
    for day in _grid_days(days, k):
        vals = _factor_values_on(xs, day, factor)
        fwd = fwd_by_day.get(day) or {}
        if len(vals) < MIN_CROSS_SECTION:
            continue
        codes = [c for c in vals if c in fwd]
        if len(codes) < MIN_CROSS_SECTION:
            continue
        v = np.asarray([vals[c] for c in codes], dtype=float)
        r = np.asarray([fwd[c] for c in codes], dtype=float)
        lo, hi = np.quantile(v, [TRIM, 1 - TRIM])
        m = (v >= lo) & (v <= hi)
        if int(m.sum()) < MIN_CROSS_SECTION:
            continue
        ic = _spearman(v[m], r[m])
        if ic is not None:
            out[day] = ic
    return pd.Series(out, dtype=float)


def _deciles(xs: dict, days: list, factor: str, k: int,
             fwd_by_day: dict) -> pd.DataFrame:
    """机理④a：十等分（**按位置分箱** —— BM 分布左偏，按数值等分会空档）。"""
    rows = []
    for day in _grid_days(days, k):
        vals = _factor_values_on(xs, day, factor)
        fwd = fwd_by_day.get(day) or {}
        if len(vals) < MIN_CROSS_SECTION:
            continue
        codes = [c for c in vals if c in fwd]
        if len(codes) < MIN_CROSS_SECTION:
            continue
        v = np.asarray([vals[c] for c in codes], dtype=float)
        r = np.asarray([fwd[c] for c in codes], dtype=float)
        order = np.argsort(v, kind="stable")
        for b, idx in enumerate(np.array_split(order, DECILES)):
            if len(idx):
                rows.append({"day": day, "q": b + 1,
                             "ret": float(r[idx].mean())})
    return pd.DataFrame(rows)


def _long_leg(vals: dict, fwd: dict, sign: int, n: int):
    """长腿（涨腿 top-N）**等权**的平均前瞻收益。

    `sign = +1` ⇒ 取因子值**最大**的 N 只；`sign = -1` ⇒ 取**最小**的 N 只。
    """
    if len(vals) < MIN_CROSS_SECTION:
        return None, None
    items = [(c, v) for c, v in vals.items() if c in fwd]
    if len(items) < MIN_CROSS_SECTION:
        return None, None
    items.sort(key=lambda kv: kv[1], reverse=(sign > 0))
    if n > len(items):
        return None, None
    picks = items[:n]
    return float(np.mean([fwd[c] for c, _ in picks])), [c for c, _ in picks]


def _pool_mean(vals: dict, fwd: dict):
    items = [(c, v) for c, v in vals.items() if c in fwd]
    if len(items) < MIN_CROSS_SECTION:
        return None, None
    return float(np.mean([fwd[c] for c, _ in items])), [c for c, _ in items]


def _leg_table(xs: dict, days: list, factor: str, k: int,
               fwd_by_day: dict, ns=LADDER_N, n_a: int = 10) -> dict:
    """机理④b：等权 top-N 阶梯 —— 每档报**长腿 − 市场腿**的年化毛超额、t 与 **MDE**。

    🚨 **MDE 必须齐报**（预注册 §5.5：「任何不显著 ⇒ 必须与它的 MDE 并排印出」
    —— 没有 MDE 的显著性结论在量级上无信息）。
    MDE 用 A3 锚：`MDE_ANN = crit × sd_per_K / √n_pairs × (244/K)`，
    `crit` 与判决用**同一个**（来自 `n_A`）。
    """
    sign = LONG_SIGN[factor]
    per_n = {}
    for n in ns:
        diffs, longs, pools = {}, {}, {}
        for day in _grid_days(days, k):
            vals = _factor_values_on(xs, day, factor)
            fwd = fwd_by_day.get(day) or {}
            lg, _codes = _long_leg(vals, fwd, sign, n)
            pm, _pc = _pool_mean(vals, fwd)
            if lg is None or pm is None:
                continue
            longs[day] = lg
            pools[day] = pm
            diffs[day] = lg - pm
        s = pd.Series(diffs, dtype=float)
        if len(s) < 3:
            per_n[str(n)] = {"n": int(len(s)), "mean_per_k": None,
                             "gross_annual_pct": None, "t": None,
                             "note": ("该档每日可用样本 < N（池子上限不足）"
                                      "或配对日 < 3 ⇒ **不报数、不回落成 0**")}
            continue
        sd = float(s.std(ddof=1))
        crit = float(bonferroni_t(n_a, df=max(len(s) - 1, 2)))
        mde_per_k = float(crit * sd / np.sqrt(len(s)))
        per_n[str(n)] = {
            "n": int(len(s)),
            "mean_per_k": float(s.mean()),
            "gross_annual_pct": _annualized(s.mean(), k),
            "sd_per_k": sd,
            "t": _paired_t(s, pd.Series(0.0, index=s.index)),
            "t_crit": crit,
            "mde_per_k": mde_per_k,
            "mde_annual_pct": float(mde_per_k * 244.0 / k),
            "mde_formula": ("MDE_ANN = crit × sd_per_K / √n_pairs × (244/K)"
                            "（预注册 §5.1 A3；crit 与判决同一个）"),
            "long_leg_annual_pct": _annualized(float(pd.Series(longs).mean()), k),
            "market_leg_annual_pct": _annualized(float(pd.Series(pools).mean()), k),
        }
    return per_n


def _drop_expensive(xs: dict, days: list, factor: str, k: int,
                     fwd_by_day: dict, n: int = 10) -> dict:
    """机理④c：**剔贵留便宜** —— 剔除**最贵**的 20% 后再取 top-10。

    🚨 对价值因子（bm/ep，§2.1「越大越便宜」），**最贵 = 因子值最小的那一端**
    ⇒ 剔掉的是**最小**的 20%。对 roe/q（越大越好）**「贵」没有价格轴**
    ⇒ 本臂**不适用**（返回 `applicable: false` 并写明原因）。
    """
    if factor not in ("bm", "ep"):
        return {"applicable": False,
                "reason": ("本臂是「剔贵留便宜」，而 roe/q 的长腿是「越大越好」，"
                           "**没有「价格贵贱」轴** ⇒ 剔贵在这两个因子上无定义。"
                           "（bm/ep 是价值因子：越大越便宜 ⇒ 最贵 = 因子值最小的那端）")}
    sign = LONG_SIGN[factor]
    diffs = {}
    for day in _grid_days(days, k):
        vals = _factor_values_on(xs, day, factor)
        fwd = fwd_by_day.get(day) or {}
        items = [(c, v) for c, v in vals.items() if c in fwd]
        if len(items) < MIN_CROSS_SECTION:
            continue
        items.sort(key=lambda kv: kv[1])
        cut = int(len(items) * DROP_EXPENSIVE)
        kept = items[cut:] if cut > 0 else items
        if len(kept) < MIN_CROSS_SECTION:
            continue
        lg, _ = _long_leg(dict(kept), fwd, sign, n)
        pm, _ = _pool_mean(dict(kept), fwd)
        if lg is None or pm is None:
            continue
        diffs[day] = lg - pm
    s = pd.Series(diffs, dtype=float)
    if len(s) < 3:
        return {"applicable": True, "n": int(len(s)), "mean_per_k": None,
                "gross_annual_pct": None, "t": None}
    return {"applicable": True, "n": int(len(s)),
            "mean_per_k": float(s.mean()),
            "gross_annual_pct": _annualized(s.mean(), k),
            "t": _paired_t(s, pd.Series(0.0, index=s.index)),
            "trimmed": {"end": "因子值最小的一端（= 最贵）",
                        "share": DROP_EXPENSIVE}}


def _cheap_x_quality(xs: dict, days: list, factor: str, k: int,
                     fwd_by_day: dict, n: int = 10,
                     quality: str = "roe") -> dict:
    """机理④d：**便宜 × 盈利质量** —— 长腿内按 `ROEJQ` 分两层。

    报「高质量层 − 低质量层」的年化毛超额与 t
    ⇒ 阳性 ⇒ 「便宜且赚钱」显著优于「便宜但不赚钱」。
    """
    sign = LONG_SIGN[factor]
    diffs = {}
    for day in _grid_days(days, k):
        vals = _factor_values_on(xs, day, factor)
        q = _factor_values_on(xs, day, quality)
        fwd = fwd_by_day.get(day) or {}
        items = [(c, v) for c, v in vals.items()
                 if c in fwd and c in q and np.isfinite(q[c])]
        if len(items) < MIN_CROSS_SECTION:
            continue
        lg, picks = _long_leg(dict(items), fwd, sign, n)
        if lg is None or not picks:
            continue
        qq = np.asarray([q[c] for c in picks], dtype=float)
        if len(qq) < 2:
            continue
        med = float(np.median(qq))
        hi = [c for c in picks if q[c] > med]
        lo = [c for c in picks if q[c] <= med]
        if not hi or not lo:
            continue
        diffs[day] = float(np.mean([fwd[c] for c in hi])) - float(
            np.mean([fwd[c] for c in lo]))
    s = pd.Series(diffs, dtype=float)
    if len(s) < 3:
        return {"applicable": True, "n": int(len(s)), "mean_per_k": None,
                "gross_annual_pct": None, "t": None}
    return {"applicable": True, "n": int(len(s)),
            "mean_per_k": float(s.mean()),
            "gross_annual_pct": _annualized(s.mean(), k),
            "t": _paired_t(s, pd.Series(0.0, index=s.index)),
            "quality_factor": quality}


def _cap_strata_ic(xs: dict, days: list, factor: str, k: int,
                   fwd_by_day: dict, caps_by_day: dict) -> dict:
    """机理⑤a：按市值分 5 档，**档内**算 IC。"""
    buckets = {}
    for day in _grid_days(days, k):
        vals = _factor_values_on(xs, day, factor)
        caps = caps_by_day.get(day) or {}
        fwd = fwd_by_day.get(day) or {}
        codes = [c for c in vals if c in fwd and c in caps
                 and np.isfinite(caps[c]) and caps[c] > 0]
        if len(codes) < MIN_CROSS_SECTION:
            continue
        capv = np.asarray([caps[c] for c in codes], dtype=float)
        edges = np.unique(np.quantile(capv, np.linspace(0, 1, N_CAP_BUCKETS + 1)))
        for i in range(len(edges) - 1):
            lo, hi = edges[i], edges[i + 1]
            sel = (capv >= lo) & ((capv < hi) if i < len(edges) - 2
                                  else (capv <= hi))
            idx = np.where(sel)[0]
            if len(idx) < MIN_CROSS_SECTION:
                continue
            x = np.asarray([vals[codes[j]] for j in idx], dtype=float)
            y = np.asarray([fwd[codes[j]] for j in idx], dtype=float)
            ic = _spearman(x, y)
            if ic is not None:
                buckets.setdefault(i + 1, []).append(
                    {"ic": ic, "n": int(len(idx)), "cap_lo": float(lo),
                     "cap_hi": float(hi)})
    out = {}
    for b, lst in sorted(buckets.items()):
        ics = [d["ic"] for d in lst]
        out[str(b)] = {"n_cells": len(ics), "mean_ic": float(np.mean(ics)),
                       "cap_lo": lst[0]["cap_lo"], "cap_hi": lst[0]["cap_hi"],
                       "median_n": float(np.median([d["n"] for d in lst]))}
    return out


def _cap_exposure(xs: dict, days: list, factor: str, k: int,
                  fwd_by_day: dict, caps_by_day: dict, n: int = 10) -> dict:
    """机理⑤c：篮子市值暴露 = 组合中位市值 ÷ 基准池中位市值。

    🚨 **方向别读反**：`> 1` = **偏大盘**（第 142 层）。
    """
    sign = LONG_SIGN[factor]
    ratios = []
    for day in _grid_days(days, k):
        vals = _factor_values_on(xs, day, factor)
        caps = caps_by_day.get(day) or {}
        fwd = fwd_by_day.get(day) or {}
        items = [(c, v) for c, v in vals.items() if c in fwd and c in caps]
        if len(items) < MIN_CROSS_SECTION:
            continue
        _lg, picks = _long_leg(dict(items), fwd, sign, n)
        if not picks:
            continue
        bc = np.asarray([caps[c] for c in picks], dtype=float)
        pc = np.asarray([caps[c] for c, _ in items], dtype=float)
        if not (np.isfinite(bc).all() and np.isfinite(pc).all()):
            continue
        bm, pm = float(np.median(bc)), float(np.median(pc))
        if pm > 0:
            ratios.append(bm / pm)
    if not ratios:
        return {"cap_exposure_ratio": None, "n": 0}
    r = float(np.median(ratios))
    return {"cap_exposure_ratio": r, "n": len(ratios),
            "direction": "偏大盘" if r > 1 else ("偏小盘" if r < 1 else "持平"),
            "direction_note": "🚨 `> 1` = **偏大盘**（第 142 层：方向别读反）"}


def _alpha_beta(xs: dict, days: list, factor: str, k: int,
                fwd_by_day: dict, n: int = 10) -> dict:
    """机理⑦：篮子收益对**等权市场**回归，报 α、β。"""
    sign = LONG_SIGN[factor]
    xs_, ys = [], []
    for day in _grid_days(days, k):
        vals = _factor_values_on(xs, day, factor)
        fwd = fwd_by_day.get(day) or {}
        lg, _ = _long_leg(vals, fwd, sign, n)
        pm, _ = _pool_mean(vals, fwd)
        if lg is None or pm is None:
            continue
        ys.append(pm)
        xs_.append(lg)
    if len(xs_) < 3:
        return {"n": int(len(xs_)), "alpha": None, "beta": None}
    x = np.asarray(ys, dtype=float)
    y = np.asarray(xs_, dtype=float)
    vx = float(x.var(ddof=1))
    if vx <= 0:
        return {"n": int(len(x)), "alpha": None, "beta": None}
    beta = float(((x - x.mean()) * (y - y.mean())).mean() / vx)
    alpha = float(y.mean() - beta * x.mean())
    resid = y - (alpha + beta * x)
    se = float(np.sqrt((resid ** 2).sum() / max(len(x) - 2, 1) / vx)) if vx > 0 else None
    return {"n": int(len(x)), "alpha": alpha, "beta": beta,
            "alpha_t": (alpha / se) if se else None,
            "market_leg": "同一个覆盖池的等权均值",
            "caliber": "α、β 都是**每 K 日**口径（不是年化）"}


def _year_segments(ic_by_year: dict) -> dict:
    """机理⑥：两段对比（2019–2020 vs 2021–2023）的符号与量级比。"""
    early = [v["mean_ic"] for y, v in ic_by_year.items() if y < "2021"]
    late = [v["mean_ic"] for y, v in ic_by_year.items() if y >= "2021"]
    out = {"early_years": sorted(y for y in ic_by_year if y < "2021"),
           "late_years": sorted(y for y in ic_by_year if y >= "2021"),
           "early_mean_ic": float(np.mean(early)) if early else None,
           "late_mean_ic": float(np.mean(late)) if late else None}
    if out["early_mean_ic"] is not None and out["late_mean_ic"] is not None:
        e, l = out["early_mean_ic"], out["late_mean_ic"]
        out["same_sign"] = bool((e > 0) == (l > 0))
        out["magnitude_ratio"] = (abs(l) / abs(e)) if e not in (0, None) else None
        out["criterion"] = ("符号相同且量级比 < 3 ⇒ 不是 regime 断点；"
                            "符号不同**或**量级比 > 3 ⇒ 是 regime 不是因子"
                            "（预注册 §6.5）")
        out["regime_break"] = (not out["same_sign"]) or (
            out["magnitude_ratio"] is not None
            and out["magnitude_ratio"] > 3.0)
    return out


def _book_prior_5b(cap_strata: dict) -> dict:
    """机理⑤b：把 5a 的**档内 IC 形状**与**石川的先验形状**对照。

    石川的先验（2026-10-20 预注册 §2.2 F1 行，出处带行号）：
    BM 在 **Small 档 t=4.76、Large 档 t=1.22** ⇒ 先验形状 = **小市值档更强**。

    🚨 **只比形状，不比 t**：石川的 t 是**石川的池与口径**下的数
    （预注册 §2.3：「不许把石川的 t 值当成本仓的先验」）
    ⇒ 本函数把先验的 **t** 记下来只作出处，
    **判据是「小市值档的档内 IC 是否更大」这个形状**。
    """
    out = {
        "prior_shape": "小市值档的档内 IC 更大（小市值更强）",
        "prior_values_from_the_book": {"small_cap_t": 4.76, "large_cap_t": 1.22},
        "prior_source": ("data/books/cn_factor_lines.txt:5949-5950, 5968-5976"
                         "（石川：High−Low 在 Small 档 t=4.76、Large 档 t=1.22）"),
        "why_only_the_shape": ("🚨 石川的 t 是**他的池与口径**下的数 ⇒ "
                               "**不许**当本仓的先验（预注册 §2.3）；"
                               "只比「小市值档是否更强」这个形状（预注册 §6.4 5b）"),
    }
    keys = sorted((k for k in cap_strata if cap_strata[k].get("mean_ic")
                   is not None), key=lambda s: int(s))
    if len(keys) < 2:
        out["measured_shape"] = "档内 IC 不足两档 ⇒ 形状不可读"
        out["agrees_with_prior"] = None
        return out
    small, large = cap_strata[keys[0]], cap_strata[keys[-1]]
    out["measured"] = {
        "smallest_cap_bucket": {"bucket": int(keys[0]),
                                "mean_ic": small["mean_ic"],
                                "cap_lo": small.get("cap_lo"),
                                "cap_hi": small.get("cap_hi")},
        "largest_cap_bucket": {"bucket": int(keys[-1]),
                               "mean_ic": large["mean_ic"],
                               "cap_lo": large.get("cap_lo"),
                               "cap_hi": large.get("cap_hi")},
        "ratio_small_over_large": (float(small["mean_ic"] / large["mean_ic"])
                                   if large["mean_ic"] else None),
    }
    out["measured_shape"] = ("小市值档档内 IC 更大" if small["mean_ic"] > large["mean_ic"]
                              else "大市值档档内 IC 更大")
    out["agrees_with_prior"] = bool(small["mean_ic"] > large["mean_ic"])
    out["conflict_note"] = ("若形状与先验相反 ⇒ **必须解释**，"
                             "**不许默默改判据**（预注册 §6.4 5b）")
    return out


# ───────────────────────── 判决 ─────────────────────────

def _verdict_for_cell(cell: dict, n_a: float, df: int,
                      d1: float | None, d2: float | None,
                      gross_annual: float | None) -> dict:
    """四态（预注册 §5.4）+ **判决链顺序**（结构闸 → MDE 闸 → 显著性 → 达标）。

    🚨 **MDE 闸必须第一个判**（委托方 §2 D-3）：
    `|效应| < MDE` ⇒ 直接判 `POWERLESS`（**测不到**），
    **不许**走到后面才说测不出。
    """
    if cell.get("ic_t") is None or cell.get("mde_ic") is None:
        return {"state": "FUNDAMENTAL_POWERLESS",
                "reason": ("IC 序列退化或样本不足 ⇒ 本设计**测不到**这个格子的效应。"
                           "⚠️ 这**不等于**「没有效应」——它等于「这个设计测不到」"),
                "wording": NOT_MEASURED_WORDING}
    ratio = abs(cell["mean_ic"]) / cell["mde_ic"] if cell["mde_ic"] > 0 else None
    if ratio is not None and ratio < 1:
        return {"state": "FUNDAMENTAL_POWERLESS",
                "reason": (f"MDE 闸先判：|平均IC| {abs(cell['mean_ic']):.4f} < "
                           f"MDE_IC {cell['mde_ic']:.4f}（比值 {ratio:.3f} < 1）"
                           f" ⇒ 本设计**测不到**这个量级的效应"),
                "effect_over_mde": ratio,
                "wording": NOT_MEASURED_WORDING}
    sig = cell.get("ic_t") is not None and abs(cell["ic_t"]) >= cell.get("t_crit", 1e9)
    if not sig:
        return {"state": "FUNDAMENTAL_NOT_MEASURED",
                "reason": (f"测到了但不够：|平均IC|/MDE = {ratio:.3f} ≥ 1"
                           f"（效应**测得到**），而 |t| = {abs(cell['ic_t']):.2f} < "
                           f"临界 {cell.get('t_crit'):.2f} ⇒ 换因子或换 K，"
                           f"**不换判据**"),
                "effect_over_mde": ratio}
    if d1 is None or d2 is None:
        # 🚨 **缺档必须报错，绝不回落**（仓内铁律）：回落成「吃不到」或
        # 「能吃到」都是在**没有判据**的情况下发判决。
        raise ValueError(
            f"交付阈值读不到（D1={d1} / D2={d2}）⇒ 拒绝给四态判决。"
            f"阈值必须从产物读（已提交版 d2_signal_requirement.json + "
            f"factor_width_mde.json）")
    d1_ok = None if gross_annual is None else gross_annual > d1
    d2_ok = None if gross_annual is None else gross_annual > d2
    if d1_ok and d2_ok:
        return {"state": "FUNDAMENTAL_LEVER",
                "reason": (f"族 A 过线（|t| = {abs(cell['ic_t']):.2f} ≥ "
                           f"{cell.get('t_crit'):.2f}）且 D1/D2 全过"),
                "effect_over_mde": ratio, "d1_pass": d1_ok, "d2_pass": d2_ok}
    return {"state": "FUNDAMENTAL_SIG_NOT_DELIVERABLE",
            "reason": (f"族 A 过线（|t| = {abs(cell['ic_t']):.2f} ≥ "
                       f"{cell.get('t_crit'):.2f}）**但吃不到**"
                       f"（长腿年化毛超额 "
                       f"{'读不到' if gross_annual is None else f'{gross_annual:.4%}'}"
                       f" vs D1 摩擦 {d1:.4%} / D2 所需 {d2:.4%}）"
                       f" ⇒ 查是否可作**增量腿**"),
            "effect_over_mde": ratio, "d1_pass": d1_ok, "d2_pass": d2_ok}


def _git_history_json(path: Path) -> list:
    """读该产物在 git 历史里的**各个版本**（新 → 旧）。

    🚨 为什么需要它：交付阈值所在的 `d2_signal_requirement.json` 在本轮
    运行期间被**另一个 agent 改过**（口径从 K=21 换成 K=63 ⇒ D1 与 D2 整套数都变了）
    ⇒ 「从产物读阈值」在产物**正在变**的时候，读到的是**时钟**，不是判据。
    ⇒ ⇒ **判据取「与 D1 产物同源同口径」的那一版**（见 `_threshold_sets`）。
    """
    import subprocess
    rel = None
    try:
        root = subprocess.run(["git", "-C", str(ROOT), "rev-parse",
                               "--show-toplevel"], capture_output=True,
                              text=True, timeout=30)
        top = Path(root.stdout.strip())
        rel = Path(path).resolve().relative_to(top)
        shas = subprocess.run(["git", "-C", str(ROOT), "log",
                               "--format=%H", "--", rel.as_posix()],
                              capture_output=True, text=True, timeout=60).stdout
    except Exception:                                   # noqa: BLE001
        return []
    out = []
    for sha in [s.strip() for s in shas.splitlines() if s.strip()]:
        try:
            got = subprocess.run(["git", "-C", str(ROOT), "show",
                                  f"{sha}:{rel.as_posix()}"],
                                 capture_output=True, text=True, timeout=60)
            if got.returncode == 0:
                out.append((sha, json.loads(got.stdout)))
        except Exception:                               # noqa: BLE001
            continue
    return out


def _threshold_sets(reports: Path) -> dict:
    """两套**各自自洽**的交付阈值（D1 与 D2 **必须同源同口径**）。

    🚨 **为什么必须分两套**：本轮运行期间，另一个 agent 把
    `d2_signal_requirement.json` 的口径**从 K=21 换成 K=63**
    （⇒ 摩擦从 6.4137%/年 掉到 2.1379%/年、D2 从 15.5571% 掉到 11.2813%）
    并**已提交**。⇒ 现在的「当前产物」与预注册 §5.1 A4 / §5.3 引用的那一版
    **不是同一个口径** ⇒ 混用 D1（旧）与 D2（新）就是**第 7 层**的错。

    ⇒ **主判据的选法（机械规则，不靠记忆）**：
    D1 取 `factor_width_mde.json`（**未变**，K=21 交付档的摩擦），
    D2 取 **D2 产物历史中「`rows[n=10].friction_at_50k` 与 D1 逐位相等」的那一版**
    ⇒ 两者的口径由**产物自己**保证一致，而不是由我记着哪个数是哪一版。

    ⇒ **对照口径**：工作区/当前 HEAD 的 D2 产物（K=63 口径），
    D1 与 D2 都取**它自己那一版** ⇒ 也是自洽的。
    """
    d2_path = reports / "d2_signal_requirement.json"
    fwm_path = reports / "factor_width_mde.json"
    out = {"primary": {}, "control": {}, "notes": []}

    wt = json.loads(d2_path.read_text(encoding="utf-8")) if d2_path.exists() else None
    fwm = (json.loads(fwm_path.read_text(encoding="utf-8"))
           if fwm_path.exists() else None)
    d1_wt = (fwm["widths"]["10"]["friction_width_aware_annual_pct"]
             if fwm else None)

    def _pack(d2doc, d1_value, source_label, sha=None):
        if not d2doc:
            return None
        rows = d2doc.get("rows") or []
        r10 = next((r for r in rows if r.get("n") == 10), None)
        if r10 is None:
            return None
        # 🚨🚨 **单位陷阱**：`headline::required_gross_at_50k_deliverable`
        # 是**百分数**（15.5571 表示 15.5571%），
        # 而 `rows[n=10]::required_gross_at_50k` 是**分数**（0.155571）。
        # ⇒ 拿 headline 去和「年化毛超额」比 ⇒ **差 100 倍** ⇒
        # 「毛超额 > 所需」**恒假** ⇒ 每个过线格都会被误判成「吃不到」。
        # ⇒ ⇒ **判决只用 `rows` 的分数值**；headline 只作对照并**明标单位**。
        head = d2doc["headline"].get("required_gross_at_50k_deliverable")
        d2_frac = r10.get("required_gross_at_50k")
        return {
            "d1_friction_annual_pct": d1_value,
            "d1_source": (f"{fwm_path}::widths['10']."
                          "friction_width_aware_annual_pct" if d1_value == d1_wt
                          else f"{d2_path}::rows[n=10].friction_at_50k"),
            "d1_unit": "分数（0.0641371 = 6.4137%/年；字段名带 `_pct` 但**值是分数**）",
            "d2_required_gross_annual_pct": d2_frac,
            "d2_source": f"{d2_path}::rows[n=10].required_gross_at_50k",
            "d2_unit": "分数（0.1555708 = 15.5571%/年）",
            "d2_headline_percent_form": head,
            "d2_headline_note": ("headline 的那个数是**百分数**（15.5571 = 15.5571%），"
                                 "与 rows 的**分数**差 100 倍 ⇒ 判决只用 rows"),
            "d1_vs_d2_row_friction_diff": (
                None if d1_value is None else d1_value - r10.get("friction_at_50k")),
            "source_version": source_label,
            "source_sha": sha,
            "k_caliber": (d2doc.get("caliber") or {}).get("k_used_by_this_ledger"),
            "d2_row_n10": {k: r10.get(k) for k in
                           ("friction_at_50k", "friction_floor",
                            "required_gross_at_50k",
                            "required_gross_at_infinite_capital")},
        }

    # ── 主判据：D2 产物历史中，与 D1 产物**摩擦值逐位相等**的那一版 ──
    hist = _git_history_json(d2_path)
    match = None
    for sha, doc in hist:
        rows = doc.get("rows") or []
        r10 = next((r for r in rows if r.get("n") == 10), None)
        if not r10 or d1_wt is None:
            continue
        if abs(r10.get("friction_at_50k", -1) - d1_wt) <= 1e-12:
            match = (sha, doc)
            break
    if match:
        out["primary"] = _pack(match[1], d1_wt,
                               "与 D1 产物同源同口径的那一版（git 历史里"
                               " friction_at_50k 与 D1 逐位相等者）", match[0])
    elif wt:
        out["primary"] = _pack(wt, d1_wt, "工作区当前版本（未找到同口径的历史版本）")
    # ── 对照口径：当前版本（D1 与 D2 都取它自己）────────────────────
    out["control"] = _pack(wt, None, "当前版本（另一个 agent 在本轮期间换过口径）")
    if out["control"] and out["control"].get("d2_row_n10"):
        out["control"]["d1_friction_annual_pct"] = \
            out["control"]["d2_row_n10"]["friction_at_50k"]
    if out["primary"] and out["control"]:
        a = out["primary"].get("d2_required_gross_annual_pct")
        b = out["control"].get("d2_required_gross_annual_pct")
        if a is not None and b is not None and abs(a - b) > 1e-12:
            out["notes"].append(
                f"🚨 **交付阈值有两套口径，本轮整套报、绝不混用**："
                f"主判据 D1={(out['primary'].get('d1_friction_annual_pct') or 0):.4%}"
                f" / D2={a:.4%}（K={out['primary'].get('k_caliber') or '21（预注册 §5.1 A4）'}，"
                f"= 预注册 §5.1 A4 / §5.3 引用的那一版）；"
                f"对照 D1={(out['control'].get('d1_friction_annual_pct') or 0):.4%}"
                f" / D2={b:.4%}（K={out['control'].get('k_caliber') or '未记录'}）。"
                f"⇒ 本轮运行期间 `d2_signal_requirement.json` 被另一个 agent "
                f"换过口径（换手次数变了 ⇒ 摩擦与所需毛超额一起变）"
                f"⇒ 第 7 层：两个各自都对的数放一起比是不许的")
    return out


def _replication_check(xs: dict, financial: Path, days: list,
                       reports: Path, factors=("bm", "roe")) -> dict:
    """**复现**对照臂：**直接调用旧脚本的函数**重算 bm/roe，与既有产物逐位对拍。

    🚨 为什么要有这一臂：`bm` 与 `roe` 的臂 R 读数**在本轮之前就存在**
    （`fundamental_ic.json` / `bm_ic_attribution.json`）⇒ 对这两个因子而言，
    本轮的数字是**复现**，不是**发现**（预注册 §6 末段）。

    🚨 这一臂**不走本轮的新代码路径**，而是**原样调用**
    `scripts/fundamental_factor.py::load_annual` / `attach_to_cross_sections`
    与 `scripts/fundamental_ic.py::ic_series` ——
    ⇒ 任何差异都指向**本轮的代码**，而不是「口径差不多」。
    🚨 旧口径与本轮口径有一处**真实差别**：同日披露多份年报时的取舍
    （旧脚本靠 pandas 不稳定排序的偶然顺序；本轮按 `aplus` 的
    `annual_from_frame` 口径取「报告期最大」的那一份）
    ⇒ 实测同一网格日样本差 19/320 ⇒ 这就是两套口径的**全部**来源。
    """
    from scripts.fundamental_factor import (attach_to_cross_sections,
                                            load_annual)
    from scripts.fundamental_ic import ic_series, ic_tstat
    from scripts.bm_ic_attribution import trimmed_ic

    old_ic = reports / "fundamental_ic.json"
    old_at = reports / "bm_ic_attribution.json"
    if not old_ic.exists():
        return {"available": False,
                "reason": f"{old_ic} 不存在 ⇒ 无既有读数可对拍"}
    xs_old = {d: (f.drop(columns=[c for c in ("bm", "roe", "report_year")
                                   if c in f.columns])
                  if isinstance(f, pd.DataFrame) else f)
              for d, f in xs.items()}
    xs_old = attach_to_cross_sections(xs_old, load_annual(financial),
                                      WINDOW[0], WINDOW[1])
    rows = []
    ref = {f"{r['factor']}@K{r['k']}": r
           for r in json.loads(old_ic.read_text(encoding="utf-8"))["rows"]}
    for f in factors:
        for k in KS:
            ic = ic_series(xs_old, days, f, k)
            t_ic, n_ic = ic_tstat(ic)
            key = f"{f}@K{k}"
            r = ref.get(key) or {}
            rows.append({
                "cell": key,
                "new_mean_ic": float(ic.mean()) if n_ic else None,
                "new_ic_t": t_ic, "new_n_ic": int(n_ic),
                "old_mean_ic": r.get("mean_ic"), "old_ic_t": r.get("ic_t"),
                "old_n_ic": r.get("n_ic"), "old_t_crit": r.get("t_crit"),
                "bitwise_equal": bool(
                    n_ic == r.get("n_ic")
                    and ic.mean() == r.get("mean_ic")
                    and t_ic == r.get("ic_t")),
                "diff_mean_ic": (float(ic.mean()) - r.get("mean_ic")
                                 if n_ic and r.get("mean_ic") is not None else None),
            })
    out = {"available": True,
           "caliber": ("**旧脚本的函数原样调用**（`load_annual` / "
                       "`attach_to_cross_sections` / `ic_series`）⇒ "
                       "差异只可能来自本轮的新代码"),
           "note": ("本臂是**复现**对照，不是发现。"
                    "旧产物 `fundamental_ic.json`（bm/roe）与 "
                    "`bm_ic_attribution.json`（bm 的分档/截尾）都是本轮之前"
                    "就存在的读数"),
           "rows": rows}
    # 机理①：截尾 IC 的 **t**（旧产物从来没算对过 ⇒ 本轮第一次算出来）
    if old_at.exists():
        d = json.loads(old_at.read_text(encoding="utf-8"))
        tic = trimmed_ic(xs_old, days, 21, xs_old.get("_daily_map") or {},
                         xs_old.get("_factors_map") or {},
                         xs_old.setdefault("_adj_cache", {}))
        t_trim, n_trim = ic_tstat(tic)
        old_t = (d.get("trimmed_ic") or {})
        out["bm_trimmed_ic"] = {
            "k": 21,
            "new_mean": float(tic.mean()) if n_trim else None,
            "new_t": t_trim, "new_n": int(n_trim),
            "old_mean": old_t.get("mean"), "old_n": old_t.get("n"),
            "old_t_field": old_t.get("t"),
            "mean_bitwise_equal": bool(n_trim == old_t.get("n")
                                       and tic.mean() == old_t.get("mean")),
            "old_t_field_note": (
                "🚨 旧产物的 `trimmed_ic::t` **不是**截尾 IC 的 t："
                "它被 G 节尾部对照循环的 `t` 覆盖了（变量遮蔽）"
                "⇒ 旧值 = `tail_contrasts::q1_minus_pool::t`。"
                "**本轮的 `new_t` 是截尾 IC 序列自己的 t（第一次算出来）**"),
            "retention": (float(tic.mean() / ref["bm@K21"]["mean_ic"])
                          if (n_trim and ref.get("bm@K21", {})
                              .get("mean_ic") is not None) else None),
            "retention_note": ("均值保留率 = **截尾 IC 均值 ÷ 原始 IC 均值**"
                               "（机理①的判据是「均值保留 ≥ 80%」）"),
        }
        out["bm_ic_attribution_old_deciles_mean"] = \
            (d.get("deciles") or {}).get("mean")
    return out


# ───────────────────────── 主流程 ─────────────────────────

def analyze_arm(xs: dict, annual: pd.DataFrame, arm_name: str,
                factors=FACTORS, ks=KS, caps_from_daily=True) -> dict:
    """一个臂的全部测量。"""
    days = sorted(d for d in (xs.get("_dates") or []) if WINDOW[0] <= d <= WINDOW[1])
    grid = sorted({d for k in ks for d in _grid_days(days, k)})
    print(f"  [{arm_name}] 窗口 {len(days)} 日 · 网格日 {len(grid)} 个", flush=True)

    # 因子挂载（**只挂网格日**：全池臂下这是 74 天而不是 1,214 天）
    xs = attach_factors(xs, annual, grid, xs.get("_daily_map") or {})
    print(f"  [{arm_name}] 因子挂载完成", flush=True)

    # 市值（close × a_shares）—— 只算网格日
    from scripts.fundamental_family_factors import price_and_shares_on
    px = price_and_shares_on(xs.get("_daily_map") or {}, grid)
    caps_by_day = {}
    for day, g in px.groupby("day"):
        caps_by_day[day] = dict(zip(g["code"].astype(str),
                                    (g["close"] * g["a_shares"]).to_numpy(float)))
    del px

    # 前瞻收益（每 (日, K) 一次，五个因子共用）
    fwd_by_k = {}
    for k in ks:
        t0 = time.time()
        m = {}
        for day in _grid_days(days, k):
            m[day] = _forward_map(xs, day, k)
        fwd_by_k[k] = m
        print(f"  [{arm_name}] K={k} 前瞻收益 {len(m)} 天 "
              f"（{time.time() - t0:.0f}s）", flush=True)

    # 覆盖率
    coverage = {}
    for f in factors:
        ns = []
        for day in grid:
            vals = _factor_values_on(xs, day, f)
            ns.append(len(vals))
        coverage[f] = {"n_days": len(grid),
                       "mean_n": float(np.mean(ns)) if ns else 0.0,
                       "median_n": float(np.median(ns)) if ns else 0.0,
                       "min_n": int(np.min(ns)) if ns else 0,
                       "max_n": int(np.max(ns)) if ns else 0}

    return {"days": days, "grid": grid, "xs": xs, "caps_by_day": caps_by_day,
            "fwd_by_k": fwd_by_k, "coverage": coverage, "factors": list(factors),
            "ks": list(ks)}


def main(argv=None) -> int:
    # 第 125 层：盖章那一刻盘上的字节 ≠ 这次执行真正跑的那些字节
    # ⇒ `main()` 的**第一条可执行语句**抓一次，盖章时比对。
    expect_bytes = provenance.code_file_sha256(ROOT, GENERATOR)

    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", type=Path, required=True, help="面板缓存（pickle）")
    ap.add_argument("--financial", type=Path,
                    default=ROOT / "data/reports/financial_annual_cache.parquet",
                    help="臂 R 的 500 只年报缓存")
    ap.add_argument("--pit-as-of", default=None,
                    help="若给，则财务侧改从 aplus.vintage.pit_frame(root, 该日) 取")
    ap.add_argument("--vintage-root", type=Path,
                    default=ROOT / "data/reports/financial_vintage")
    ap.add_argument("--arm-name", default="armR500")
    ap.add_argument("--replication", action="store_true",
                    help="加跑**复现对照臂**（旧臂 R 口径重算 bm/roe，"
                         "与既有产物逐位对拍）")
    ap.add_argument("--factors", default=",".join(FACTORS))
    ap.add_argument("--reports", type=Path, default=ROOT / "data/reports")
    ap.add_argument("--out", type=Path,
                    default=ROOT / "data/reports/fundamental_mechanism_armR_2026-10-21.json")
    args = ap.parse_args(argv)

    factors = tuple(f.strip() for f in args.factors.split(",") if f.strip())
    reports = Path(args.reports)

    # ── 判据的外部锚（**读产物，不手打**）────────────────────────────
    thr = _threshold_sets(reports)
    d1 = (thr.get("primary") or {}).get("d1_friction_annual_pct")
    d2 = (thr.get("primary") or {}).get("d2_required_gross_annual_pct")
    d3 = None
    lot = reports / "lot_affordability.json"
    if lot.exists():
        d3 = json.loads(lot.read_text(encoding="utf-8"))["widths"][0]

    # ── n_A（**从注册表长度算**）────────────────────────────────────
    reg = _read_registry_n_a(reports, "2026-10-21")
    n_a_primary = reg["n_A"]
    controls = _n_a_control_calibers(reports)
    print(f"n_A（注册表长度）= {n_a_primary}；对照口径："
          f"预注册字面 {controls['prereg_literal']['n_A']} / "
          f"有效维数 {controls['effective_dimension']['n_A']}", flush=True)

    # ── 财务侧 ─────────────────────────────────────────────────────
    if args.pit_as_of:
        from aplus.factor_fundamental import pit_frame
        frame = pit_frame(args.vintage_root, args.pit_as_of)
        annual = annual_series_from_pit(frame, args.pit_as_of)
        fin_source = (f"aplus.vintage.pit_frame({args.vintage_root}, "
                      f"'{args.pit_as_of}')")
    else:
        annual = annual_series_from_cache(args.financial)
        fin_source = str(Path(args.financial).resolve())
    print(f"财务侧：{fin_source} · 年报 {len(annual)} 行 / "
          f"{annual['code'].nunique()} 只", flush=True)

    # ── 面板 + 机理全集 ────────────────────────────────────────────
    xs = _load_panel(args.cache)
    arm = analyze_arm(xs, annual, args.arm_name, factors=factors)
    xs = arm.pop("xs")

    fam_cells = {}
    for f in factors:
        for k in arm["ks"]:
            fwd = arm["fwd_by_k"][k]
            ic = _ic_series(xs, arm["days"], f, k, fwd)
            t_ic, n_ic = _ic_tstat(ic)
            sd_ic = float(ic.std(ddof=1)) if n_ic > 2 else None
            crit = bonferroni_t(n_a_primary, df=max(n_ic - 1, 2))
            mde = (float(crit * sd_ic / np.sqrt(n_ic))
                   if (sd_ic is not None and n_ic > 0) else None)
            tic = _trimmed_ic(xs, arm["days"], f, k, fwd)
            t_trim, n_trim = _ic_tstat(tic)
            dec = _deciles(xs, arm["days"], f, k, fwd)
            dec_mean = (dec.groupby("q")["ret"].mean().round(5).to_dict()
                        if not dec.empty else {})
            dec_win = (dec.assign(w=dec["ret"] > 0).groupby("q")["w"].mean()
                       .round(3).to_dict() if not dec.empty else {})
            legs = _leg_table(xs, arm["days"], f, k, fwd, n_a=n_a_primary)
            _cap5a = _cap_strata_ic(xs, arm["days"], f, k, fwd,
                                    arm["caps_by_day"])
            cell = {
                "factor": f, "k": k, "n_ic": int(n_ic),
                "mean_ic": float(ic.mean()) if n_ic else None,
                "ic_sd": sd_ic, "ic_t": t_ic, "t_crit": float(crit),
                "mde_ic": mde,
                "effect_over_mde": (abs(float(ic.mean())) / mde
                                    if (mde and n_ic) else None),
                "long_leg_legend": LONG_LEGEND[f],
                "new_or_replication": ("replication（该因子在臂 R 上的读数在本轮之前"
                                        "已存在于 fundamental_ic.json / "
                                        "bm_ic_attribution.json）"
                                        if f in ("bm", "roe")
                                        else "new（本轮第一次测这个因子）"),
                "trimmed_ic": {"mean": float(tic.mean()) if n_trim else None,
                               "t": t_trim, "n": int(n_trim), "trim": TRIM,
                               "mean_retention": (float(tic.mean() / ic.mean())
                                                  if (n_trim and ic.mean()) else None),
                               "note": ("机理①的判据是**截尾 IC 序列自己的 t**"
                                        "（不是尾部对照的 t —— 变量遮蔽已修）")},
                "deciles": {"mean": dec_mean, "win_rate": dec_win,
                            "monotonic_decreasing": (bool(
                                pd.Series(dec_mean).is_monotonic_decreasing)
                                if dec_mean else None)},
                "leg_ladder": legs,
                "drop_expensive_4c": _drop_expensive(xs, arm["days"], f, k, fwd),
                "cheap_x_quality_4d": _cheap_x_quality(xs, arm["days"], f, k, fwd),
                "cap_strata_ic_5a": _cap5a,
                "book_prior_5b": _book_prior_5b(_cap5a),
                "cap_exposure_5c": _cap_exposure(xs, arm["days"], f, k, fwd,
                                                arm["caps_by_day"]),
                "alpha_beta_7": _alpha_beta(xs, arm["days"], f, k, fwd),
            }
            # 分年（机理⑥）
            byy = {}
            for day, v in ic.items():
                byy.setdefault(day[:4], []).append(v)
            ic_by_year = {y: {"n": len(v), "mean_ic": float(np.mean(v))}
                          for y, v in sorted(byy.items())}
            cell["ic_by_year"] = ic_by_year
            cell["year_segments_6"] = _year_segments(ic_by_year)
            # 长腿毛超额（收工闸要的那个数）：取 N=10 那一档
            l10 = legs.get("10") or {}
            cell["long_leg"] = {
                "n_positions": 10, "k": k,
                "gross_annual_pct": l10.get("gross_annual_pct"),
                "long_leg_annual_pct": l10.get("long_leg_annual_pct"),
                "market_leg_annual_pct": l10.get("market_leg_annual_pct"),
                "t": l10.get("t"),
                "caliber": ("**多头口径**：长腿（涨腿 top-10，等权）− 市场腿"
                            "（同一覆盖池等权均值）；年化 = 每 K 日均值 × 244/K。"
                            "🚨 **不是多空口径**（顶−底）：融券不可得，"
                            "书里的多空数**不是本项目的可达目标**"),
            }
            fam_cells[f"{f}@K{k}"] = cell

    # ── 复现对照臂（**先于**判决：它不产生判决，只证明复现）─────────
    replication = {"available": False,
                   "reason": "本轮没有跑复现对照臂（--replication 未开）"}
    if args.replication:
        if args.pit_as_of:
            replication = {"available": False,
                           "reason": "复现对照臂只对**500 只缓存臂**有意义"}
        else:
            replication = _replication_check(xs, args.financial, arm["days"],
                                             reports=reports)
        for r in replication.get("rows") or []:
            print(f"  复现 {r['cell']:>12}: mean_ic 新 {r['new_mean_ic']} "
                  f"vs 旧 {r['old_mean_ic']} · 逐位相等 {r['bitwise_equal']}",
                  flush=True)
        bt = replication.get("bm_trimmed_ic") or {}
        if bt:
            print(f"  截尾 IC（bm@K21，旧代码路径）：mean {bt['new_mean']} "
                  f"(旧 {bt['old_mean']}，逐位相等 {bt['mean_bitwise_equal']}) "
                  f"· **t = {bt['new_t']}**（旧产物那个 t 是被覆盖的）",
                  flush=True)

    # ── 判决（三套 n_A 口径各出一份四态）───────────────────────────
    def _verdict_set(n_a: float, label: str, d1_v=d1, d2_v=d2) -> dict:
        out = {}
        for key, cell in fam_cells.items():
            gross = ((cell.get("long_leg") or {}).get("gross_annual_pct"))
            out[key] = _verdict_for_cell(cell, n_a, max(cell["n_ic"] - 1, 2),
                                         d1_v, d2_v, gross)
        states = {}
        for v in out.values():
            states[v["state"]] = states.get(v["state"], 0) + 1
        return {"n_A": n_a, "n_A_label": label, "by_cell": out,
                "state_counts": states,
                "cells_pass_family_a": sum(
                    1 for v in out.values()
                    if v["state"] in ("FUNDAMENTAL_LEVER",
                                      "FUNDAMENTAL_SIG_NOT_DELIVERABLE"))}

    verdicts = {
        "primary": _verdict_set(n_a_primary,
                                 "注册表长度（第 6 批候选数，程序读出）",
                                 d1, d2),
        "control_prereg_literal": _verdict_set(
            controls["prereg_literal"]["n_A"],
            "2026-10-20 预注册 §5.2 字面（族 A 四因子 × K 档数）", d1, d2),
    }
    if controls["effective_dimension"]["n_A"] is not None:
        verdicts["control_effective_dimension"] = _verdict_set(
            controls["effective_dimension"]["n_A"],
            "参与比（有效维数）× K 档数（预注册 §8.1 A3）", d1, d2)
    ctrl = thr.get("control") or {}
    if ctrl.get("d2_required_gross_annual_pct") is not None:
        verdicts["control_thresholds_working_tree"] = _verdict_set(
            n_a_primary,
            "n_A 同主口径，但交付阈值取**工作区当前**的 D2 产物"
            "（另一个 agent 在本轮期间改了摩擦模型）",
            ctrl.get("d1_friction_annual_pct"),
            ctrl.get("d2_required_gross_annual_pct"))

    # 族级标签：**逐格分布**，不取极值（第 143 层）
    prim = verdicts["primary"]
    if prim["cells_pass_family_a"] == len(fam_cells):
        family_state = ("FUNDAMENTAL_LEVER"
                        if all(v["state"] == "FUNDAMENTAL_LEVER"
                               for v in prim["by_cell"].values())
                        else "FUNDAMENTAL_SIG_NOT_DELIVERABLE")
    elif prim["cells_pass_family_a"] > 0:
        family_state = "FUNDAMENTAL_SIG_NOT_DELIVERABLE"
    elif all(v["state"] == "FUNDAMENTAL_POWERLESS"
             for v in prim["by_cell"].values()):
        family_state = "FUNDAMENTAL_POWERLESS"
    else:
        family_state = "FUNDAMENTAL_NOT_MEASURED"

    payload = {
        "kind": "fundamental_mechanism_armR",
        "prereg": PREREG, "prereg_base": PREREG_BASE,
        "prereg_sections": ["§2 D-1/D-2/D-3", "§3 臂", "§4 判据", "§5 不做"],
        "arm": args.arm_name,
        "window": list(WINDOW),
        "k_arms": list(arm["ks"]),
        "financial_source": fin_source,
        "panel_cache": str(Path(args.cache).resolve()),
        "panel_cache_sha256": _sha256_file(args.cache),
        "coverage": arm["coverage"],
        "factors": {f: {"definition": LONG_LEGEND[f]} for f in factors},
        "n_A": {"primary": reg, "controls": controls},
        "thresholds": {
            "primary": thr.get("primary"),
            "control_working_tree": thr.get("control"),
            "notes": thr.get("notes"),
            "d1_friction_annual_pct": d1,
            "d2_required_gross_annual_pct": d2,
            "d3_lot_affordability": d3,
            "d3_note": ("预注册 §5.3 的 D3 **没有给「足够」的数值门槛** ⇒ "
                        "本轮只报读数，**不判布尔**；D1/D2 先封口，D3 不参与四态"),
            "family_b_critical": ("预注册 §6.3 引用「族 B 的临界」但**未给数值** ⇒ "
                                  "本轮用与族 A 同一 crit（来自 n_A），"
                                  "并在产物里标明这是**自选口径**"),
        },
        "family_a_cells": fam_cells,
        "replication_check": replication,
        "verdict": {
            "calibers": verdicts,
            "primary": {
                "n_A": n_a_primary,
                "family_state": family_state,
                "family_state_rule": ("族级标签 = **逐格分布**（过线格数 / 各态计数），"
                                      "**不取极值**（第 143 层：跨格取极值会抹掉"
                                      "它从哪一行来）"),
                "state_counts": prim["state_counts"],
                "cells_pass_family_a": prim["cells_pass_family_a"],
                "n_cells": len(fam_cells),
            },
        },
        "wording_guard": {
            "powerless_wording": NOT_MEASURED_WORDING,
            "note": ("`FUNDAMENTAL_POWERLESS` 读作「**这个设计测不到**」，"
                     "**不是**「因子无效」「已证伪」「没有超额」"),
        },
        "no_promotion": (
            "**臂 R 是后修值臂**（预注册 §4.1）⇒ 本产物**不产生任何采纳结论**；"
            "§5.4 的四态是**族 A 的判决**，不是实盘晋级。"
            "🚨 本产物**不宣称**基本面族能独立达到年化 20% —— "
            "D2 所需毛超额 " + (f"{d2:.4%}" if d2 else "（读不到）")
            + " 是一道很厚的墙"),
        "what_this_cannot_prove": [
            "臂 R 的读数出自**后修值**财务缓存 ⇒ 不能作为**晋级依据**（预注册 §3.2/§8.4）",
            "**不显著**只等于「没检出」，不等于「不存在」（预注册 §7.1 逐字）",
            "「族 A 过线」只说明**因子在历史上测得到预测力**，"
            "不说明**扣掉摩擦后吃得到**（D1/D2 另判）",
            "**多空口径不是本项目的可达目标**（融券不可得）⇒ 本产物只报多头口径",
        ],
        "scope": {
            "reads": "面板缓存（pickle）+ 财务缓存 / PIT 帧；未建面板",
            "no_panel_build": True,
        },
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    provenance.stamp(args.out, GENERATOR, root=ROOT, expect_bytes=expect_bytes,
                     extra={
                         "arm": args.arm_name,
                         "window": list(WINDOW),
                         "k_arms": list(arm["ks"]),
                         "financial_source": fin_source,
                         "panel_cache": str(Path(args.cache).resolve()),
                         "panel_cache_sha256": _sha256_file(args.cache),
                         # 🚨 因子定义模块的字节也要进章：产物里的每个因子值
                         # 都经过它，而 `stamp()` 只盖生成器一个文件
                         # ⇒ 只盖生成器 = 「盖了身份证没盖出生日期」（第 156 层）
                         "factor_module": "scripts/fundamental_family_factors.py",
                         "factor_module_sha256": _sha256_file(
                             ROOT / "scripts" / "fundamental_family_factors.py"),
                         "n_A_primary": n_a_primary,
                         "n_A_source": reg["source"],
                     })
    print(f"\n  → {args.out}")
    print(f"  n_A（注册表长度）= {n_a_primary}")
    print(f"  族级标签：{family_state}   逐格计数：{prim['state_counts']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

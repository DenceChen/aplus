#!/usr/bin/env python3
"""持仓级降级预警的**测量台**（不是接线层）。

## 它回答什么

D1 每天给两个榜：会涨 10 只、会跌 10 只。**两个榜都是从全池两端挑的极端**，
所以用户手上的持仓天然落在两榜之外（跌榜 = 全市场最差的 10 只，
而持仓来自涨榜 = 全池最好的 10 只）。
⇒ **同一天**两腿在构造上不相交；但产品口径「**实际持仓** × **各日跌榜**」
**并非恒不相交**（实测有持仓被跌榜点过名 ⇒ 本脚本逐日量，见 `defect_measurement`）
⇒ **跌榜这一半对 long-only 账户是边际的**。

本脚本量的是用户真正问的那句：
**「我手上这几只里，哪几只已经掉到最差那一档？」**

## 三条口径纪律

1. **全池重算不许用 GB 级面板**（`build_panel` 在本机是 GB 级 / 10 分钟量级，
   且与赛马同跑会 OOM）。这里**流式**逐只算：读一只、算一只、扔一只。
   因子/过滤/打分**全部复用生产函数**（**面板那一个**因子实现
   `aplus/panel.py::_factor_series` / `filter_universe` /
   `composite_score` / `buyable_series`）⇒ 不重写一遍数学。
   ⚠️ 慢路径 `aplus/factor.py::compute_factors` **不是**生产实现（见 §9.1）。
2. **重算结果必须与生产产物对拍**（外部锚）：生产落盘的
   `pred_<date>.parquet` 里两腿共 20 只的合成分是**生产自己算的数**。
   本脚本重算后必须复现那 20 个名次与分数（容差 1e-9）⇒ 否则**拒绝出产物**
   （rc=2）。**自己重算自己不是证据，生产产物才是。**
3. **合成分跨日比较的是「相对位次」不是收益率**。合成分是横截面相对量，
   两天的合成分相减不等于价格涨跌 ⇒ 产物里写死这条边界。

## 产物

`data/reports/holdings_alert_<as_of>.json` + 同名 `.provenance.json` sidecar。

⚠️ 本脚本**只测量、不接线**：`aplus/alloc.py` / `scripts/run_daily.py`
由协调方接，本脚本不碰它们。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aplus import provenance                                          # noqa: E402
from aplus.evolve import LIVE_REBALANCE_EVERY  # noqa: E402
from aplus.adjust import attach_factor                                # noqa: E402
from aplus.evolve import load_weights                                 # noqa: E402
from aplus.factor import composite_score                              # noqa: E402
from aplus.holdings_alert import (                                    # noqa: E402
    ANCHOR_CITATION,
    HONEST_BOUNDARIES,
    bottom_cuts,
    build_holdings_alert,
)
from aplus.live_rebalance import load_state                           # noqa: E402

# ⚠️ `_factor_series` 是**私有名**，但它**就是生产用的那个因子实现**
#   （`aplus/panel.py:573`，`build_panel` 逐股调它；`build_cross_section`
#   的 docstring 明确写着「实盘**直接复用面板**：单一实现，口径不可能再分叉」，
#   `cross_rows_from_map` 只作**参照实现**）。
#   ⇒ 这里逐股调它 = 生产的因子口径，**不重写一遍数学**。
#   🚨 教训（实测）：改用慢路径 `aplus.factor.compute_factors` ⇒
#   **vol20 差 0.0468**（20 只里有 15 只不同）⇒ 合成分差 0.57 ⇒
#   连名次都变（涨腿 10 只里 3 只不同）⇒ 与生产产物**对不上**。
#   根因：慢路径 `volatility()` 是「dropna 后取最后 20 个**有效**收益」，
#   面板是「固定 20 个**日历**行、剔停牌、钳复牌极值、≥2/3 样本才算」。
from aplus.panel import _factor_series as _panel_factor_series        # noqa: E402
from aplus.panel import buyable_series                               # noqa: E402
from aplus.predict import filter_universe                            # noqa: E402
from aplus.reports import weights_fingerprint                        # noqa: E402
from aplus.snapshot import COLUMNS, FACTOR_WINDOW_MIN                 # noqa: E402
from aplus.store import LocalStore                                   # noqa: E402

GENERATOR = "scripts/measure_holdings_alert.py"

#: 这两个因子**不是窗口局部**（RSI 递推、IVOL 需要全市场收益序列）。
#: 权重里出现它们 ⇒ 必须走全历史口径（下面据此决定要不要两趟）。
FULL_HISTORY_FACTORS = ("rsi", "ivol")

#: 与生产产物对拍的容差（合成分是 z 量纲，1e-9 已远严于浮点噪声）
PARITY_TOL = 1e-9


# --------------------------------------------------------------------------
# 参数
# --------------------------------------------------------------------------

def _parse_args(argv=None):
    p = argparse.ArgumentParser(description="持仓级降级预警测量台")
    p.add_argument("--as-of", default="", help="信号日 YYYY-MM-DD（默认 = 全池最新交易日）")
    p.add_argument("--data-root", default=str(ROOT / "data"), help="数据根（默认仓内 data/）")
    p.add_argument("--reports-dir", default="", help="报告目录（默认 <data-root>/reports）")
    p.add_argument("--out", default="", help="产物路径（默认 <reports>/holdings_alert_<as_of>.json）")
    return p.parse_args(argv)


# --------------------------------------------------------------------------
# 全池重算（流式，不建 GB 级面板）
# --------------------------------------------------------------------------

def _latest_trading_day(daily_dir: Path) -> str:
    """全池最新交易日（只读 date 列，~几秒）。"""
    best = ""
    for path in sorted(daily_dir.glob("*.parquet")):
        try:
            df = pd.read_parquet(path, columns=["date"])
        except Exception:      # noqa: BLE001 - 单只读不出来不该杀掉全池
            continue
        if df.empty:
            continue
        d = str(pd.to_datetime(df["date"]).max().date())
        if d > best:
            best = d
    return best


def _load_daily(path: Path) -> pd.DataFrame:
    df = pd.read_parquet(path)
    if "date" not in df.columns:
        raise ValueError(f"{path} 缺 date 列")
    df["date"] = df["date"].astype(str)
    return df


def _load_factor(factor_dir: Path, code: str):
    path = factor_dir / f"{code}.parquet"
    if not path.exists():
        return None
    ev = pd.read_parquet(path)
    if ev is None or ev.empty:
        return None
    ev = ev.copy()
    ev["date"] = ev["date"].astype(str)
    return ev


def _market_return_series(files, factor_dir: Path, as_of: str) -> pd.Series:
    """等权市场日收益（与 `aplus.panel.market_return_series` 同一口径）。

    生产那条路是「把所有股票塞进一个 DataFrame 再 `mean(axis=1, skipna=True)`」；
    这里逐只累加 sum/count ⇒ **逐位等价**，但不必把全池放进内存。
    """
    sums: dict[str, float] = {}
    counts: dict[str, int] = {}
    for path in files:
        daily = _load_daily(path)
        sliced = daily[daily["date"] <= as_of]
        if sliced.empty:
            continue
        ev = _load_factor(factor_dir, path.stem)
        s = attach_factor(sliced, ev) if ev is not None else sliced
        adj = s["close"] * s.get("adjust_factor", 1.0)
        r = adj.pct_change()
        if "volume" in s.columns:
            r = r.where(s["volume"].astype(float) > 0)
        r = r.dropna()
        for d, v in r.items():
            dv = str(d)
            sums[dv] = sums.get(dv, 0.0) + float(v)
            counts[dv] = counts.get(dv, 0) + 1
    if not sums:
        return pd.Series(dtype=float)
    idx = sorted(sums)
    mkt = pd.Series([sums[d] / counts[d] for d in idx], index=pd.Index(idx))
    return mkt.dropna()


def _cross_section_rows(files, factor_dir: Path, as_of: str, names_map: dict,
                        mkt) -> list[dict]:
    """逐只算横截面（**一次只留一只** ⇒ 内存与全池大小无关）。

    口径 = 生产面板（`aplus/panel.py::_factor_series` + `FactorPanel.cross_section`）：
    同样的 attach_factor、同样的因子实现、同样的 buyable 判定、
    同样的 NaN 不冒充 0 的 isST、同样的 listed_days / last_date。

    ⚠️ 与面板的一致性靠三件事：
    ① 因子用**面板那一个实现**（不是慢路径的 `compute_factors`——实测会漂）
    ② `listed_days` = 该股截至信号日的 K 线根数（= 面板的 `idx+1`）
    ③ 末根取「≤ as_of 的最后一行」（面板用 `searchsorted(side="right")-1`）
    """
    rows: list[dict] = []
    for path in files:
        code = path.stem
        daily = _load_daily(path)
        if daily.empty:
            continue
        ev = _load_factor(factor_dir, code)
        merged = attach_factor(daily, ev) if ev is not None else daily
        merged = merged.sort_values("date", kind="mergesort")
        # 面板取行号的方式：`searchsorted(side="right") - 1` = 「≤ as_of 的最后一行」
        dates = merged["date"].to_numpy(dtype="U10")
        idx = int(np.searchsorted(dates, as_of, side="right") - 1)
        if idx < 0:
            continue
        if (idx + 1) < FACTOR_WINDOW_MIN:      # 面板：listed >= min_history
            continue
        factors = _panel_factor_series(merged, mkt=mkt).iloc[idx]
        last = merged.iloc[idx]
        isst = last.get("isST")
        isst = float(isst) if pd.notna(isst) else float("nan")
        buyable = bool(buyable_series(merged, code).iloc[idx])
        rows.append({
            "code": code,
            "name": names_map.get(code, code),
            **factors.to_dict(),
            "isST": isst,
            "volume": float(last.get("volume", 0) or 0),
            "listed_days": idx + 1,
            "last_date": str(last["date"]),
            "buyable": buyable,
        })
    return rows


def _scored_pool(data_root: Path, as_of: str, weights: dict, names_map: dict) -> dict:
    """重算当日全池合成分（流式）。

    返回 {"ranked": DataFrame（score 降序，全池）, "pool_size": int,
          "filtered_out": {...}}
    """
    store = LocalStore(data_root)
    daily_dir = store.root / "master" / "daily"
    factor_dir = store.root / "master" / "factor"
    files = sorted(daily_dir.glob("*.parquet"))

    full_history = bool(set(weights) & set(FULL_HISTORY_FACTORS))
    mkt = None
    if "ivol" in weights:
        # IVOL 需要等权市场收益序列 ⇒ 必须先扫一遍全池（生产面板也是两趟）。
        mkt = _market_return_series(files, factor_dir, as_of)

    rows = _cross_section_rows(files, factor_dir, as_of, names_map, mkt)
    cross = pd.DataFrame(rows, columns=COLUMNS)

    unknown_st: dict = {}
    stale: dict = {}
    unfillable: dict = {}
    universe = filter_universe(cross, unknown_st=unknown_st,
                               stale=stale, unfillable=unfillable)
    universe = universe.copy()
    universe["score"] = composite_score(universe, weights)
    ranked = (universe[universe["score"].notna()]
              .sort_values("score", ascending=False)
              .reset_index(drop=True))
    return {
        "ranked": ranked,
        "pool_size": int(len(ranked)),
        "cross_size": int(len(cross)),
        "filtered": {
            "stale": len(stale),
            "unfillable": len(unfillable),
            "unknown_st": len(unknown_st),
        },
        "full_history_factors_used": full_history,
        "ivol_reproduced": ("ivol" in weights),
    }


# --------------------------------------------------------------------------
# 与生产产物对拍（外部锚）
# --------------------------------------------------------------------------

def _rel(path: Path) -> str:
    """仓相对路径；**不在仓内**（如 tmp_path）时原样返回，不炸。"""
    try:
        return str(Path(path).relative_to(ROOT))
    except ValueError:
        return str(path)


def _production_parity(reports_dir: Path, as_of: str, ranked: pd.DataFrame,
                       top_n: int = 10, bottom_n: int = 10) -> dict:
    """重算结果 vs 生产落盘的两腿。

    生产产物 `pred_<as_of>.parquet` 里的 `score` 是**生产自己算的合成分**
    ⇒ 它是本脚本的外部锚。对不上 ⇒ 本脚本算错了 ⇒ **拒绝出产物**。
    """
    path = reports_dir / "predictions" / f"pred_{as_of}.parquet"
    out: dict = {
        "checked_against": _rel(path) if path.exists() else None,
        "available": path.exists(),
    }
    if not path.exists():
        out["verdict"] = "NO_PRODUCTION_ARTIFACT_TO_COMPARE_AGAINST"
        return out
    prod = pd.read_parquet(path)
    up = prod[prod["direction"] == "up"]
    down = prod[prod["direction"] == "down"]
    my_up = ranked.head(top_n)
    my_down = ranked.tail(bottom_n)

    def _cmp(mine: pd.DataFrame, theirs: pd.DataFrame) -> dict:
        t = {str(r["code"]): float(r["score"]) for _, r in theirs.iterrows()}
        m = {str(r["code"]): float(r["score"]) for _, r in mine.iterrows()}
        same_codes = set(t) == set(m)
        common = set(t) & set(m)
        max_diff = max((abs(t[c] - m[c]) for c in common), default=float("inf"))
        return {
            "codes_match": same_codes,
            "prod_codes": sorted(t),
            "recomputed_codes": sorted(m),
            "max_abs_score_diff": (None if common and max_diff == float("inf") else max_diff),
            "n_common": len(common),
        }

    cu = _cmp(my_up, up)
    cd = _cmp(my_down, down)
    out["up_leg"] = cu
    out["down_leg"] = cd
    diffs = [d for d in (cu["max_abs_score_diff"], cd["max_abs_score_diff"])
             if d is not None]
    out["max_abs_score_diff"] = max(diffs) if diffs else None
    ok = (cu["codes_match"] and cd["codes_match"]
          and out["max_abs_score_diff"] is not None
          and out["max_abs_score_diff"] <= PARITY_TOL)
    out["tolerance"] = PARITY_TOL
    out["verdict"] = "MATCHES_PRODUCTION" if ok else "MISMATCH_DO_NOT_TRUST_THIS_ARTIFACT"
    return out


# --------------------------------------------------------------------------
# 持仓与建仓日的解析
# --------------------------------------------------------------------------

def _pred_files(reports_dir: Path) -> list[Path]:
    d = reports_dir / "predictions"
    return sorted(d.glob("pred_*.parquet")) if d.exists() else []


def _resolve_entry_day(reports_dir: Path, state: dict | None) -> dict:
    """「持仓是哪天定的」——**两个来源，先信 state，再靠集合唯一匹配**。

    ① `holdings.json::held_since`（真值，但旧文件没有 ⇒ 缺就是缺）
    ② 集合唯一匹配：state 的 `up`（建仓日那份涨腿）逐位对应某一天的涨腿
       ⇒ 那天就是建仓日。**必须唯一**，多天匹配 ⇒ 判不出来（不许随便挑一天）。
    """
    out: dict = {"state_held_since": None, "sources_tried": []}
    if not state:
        out["verdict"] = "NO_STATE_FILE"
        return out
    held_since = state.get("held_since")
    out["state_held_since"] = held_since
    basket = [str(c) for c in (state.get("up") or [])]

    if isinstance(held_since, str) and held_since.strip():
        d = held_since.strip()
        if (reports_dir / "predictions" / f"pred_{d}.parquet").exists():
            out["entry_day"] = d
            out["entry_day_source"] = "holdings.json::held_since"
            out["verdict"] = "RESOLVED_FROM_STATE"
            return out
        out["sources_tried"].append(f"held_since={d}（无对应 pred 文件）")

    if not basket:
        out["verdict"] = "NO_BASKET_IN_STATE"
        return out

    matches = []
    for path in _pred_files(reports_dir):
        d = path.stem.replace("pred_", "")
        try:
            df = pd.read_parquet(path, columns=["code", "direction"])
        except Exception:      # noqa: BLE001
            continue
        up_codes = {str(c) for c in df[df["direction"] == "up"]["code"]}
        if up_codes == set(basket):
            matches.append(d)
    out["candidate_days_with_same_up_leg"] = matches
    if len(matches) == 1:
        out["entry_day"] = matches[0]
        out["entry_day_source"] = ("唯一集合匹配：state.up == pred_%s 的涨腿"
                                    % matches[0])
        out["verdict"] = "RESOLVED_BY_UNIQUE_SET_MATCH"
    elif not matches:
        out["verdict"] = "NO_MATCHING_DAY"
    else:
        out["verdict"] = "AMBIGUOUS_MATCH"
    return out


def _resolve_holdings(reports_dir: Path, entry_day: str | None,
                      basket: list[str]) -> dict:
    """实际持仓 = **建仓日那份配置里真正拿到钱的那些**。

    ⚠️ `alloc_<date>.json::positions` 是「那天的**计划**持仓」（top-5 of 涨腿），
    只有**建仓日**那份才是「用户手上真有的」——非调仓日那份是当天的建议，
    没被执行。⇒ 必须锚在**建仓日**，不能拿最新那份当持仓。
    """
    out: dict = {"basis": None, "codes": [], "entry_scores": {}, "names": {}}
    if not entry_day:
        out["note"] = "建仓日未解析 ⇒ 持仓名单与建仓分都取不到（测不出）"
        return out
    alloc_path = reports_dir / "allocation" / f"alloc_{entry_day}.json"
    if not alloc_path.exists():
        out["note"] = f"缺 {alloc_path.name} ⇒ 无法取持仓"
        return out
    alloc = json.loads(alloc_path.read_text(encoding="utf-8"))
    positions = alloc.get("positions") or []
    out["basis"] = f"{alloc_path.relative_to(ROOT)}::positions"
    out["codes"] = [str(p.get("code")) for p in positions]
    for p in positions:
        code = str(p.get("code"))
        out["names"][code] = p.get("name")
        sc = p.get("score")
        if sc is not None:
            out["entry_scores"][code] = float(sc)
    out["n_positions"] = len(positions)
    rb = alloc.get("rebalance")
    if isinstance(rb, list):
        # `rebalance` 是**列表**（[{code, action, …}]），不是 dict
        counts: dict = {}
        for row in rb:
            act = str((row or {}).get("action"))
            counts[act] = counts.get(act, 0) + 1
        out["allocation_rebalance_counts"] = counts
    elif isinstance(rb, dict):
        out["allocation_rebalance_counts"] = {
            k: len(v) for k, v in rb.items() if isinstance(v, list)
        }
    else:
        out["allocation_rebalance_counts"] = None
    return out


def _entry_score_crosscheck(reports_dir: Path, entry_day: str | None,
                            entry_scores: dict) -> dict:
    """建仓分与当日涨腿落盘分逐位对拍（同一口径的第二份来源）。"""
    out: dict = {"available": False}
    if not entry_day:
        return out
    path = reports_dir / "predictions" / f"pred_{entry_day}.parquet"
    if not path.exists():
        out["note"] = f"缺 {path.name}"
        return out
    df = pd.read_parquet(path)
    up = df[df["direction"] == "up"]
    leg = {str(r["code"]): float(r["score"]) for _, r in up.iterrows()}
    out["available"] = True
    out["source"] = _rel(path) + "::score"
    diffs = [abs(leg[c] - s) for c, s in entry_scores.items() if c in leg]
    out["n_compared"] = len(diffs)
    out["max_abs_diff"] = max(diffs) if diffs else None
    out["codes_in_leg"] = sorted(leg)
    if "weights_hash" in df.columns:
        vals = {str(v) for v in df["weights_hash"].dropna().unique()}
        out["weights_hash_at_entry_day"] = sorted(vals)
    return out


# --------------------------------------------------------------------------
# 缺陷测量：跌榜 ∩ 持仓
# --------------------------------------------------------------------------

def _defect_measurement(reports_dir: Path, held: list[str]) -> dict:
    """跌榜（会跌 10 只）∩ 持仓，逐日。

    这是**本块存在的理由**：跌榜来自全池最差的一端，持仓来自涨腿
    ⇒ 同一天两腿在构造上就不相交（**构造事实**）。
    ⇒ 但产品口径（**实际持仓** × **各日跌榜**）**并非恒不相交**。
    """
    per_day = []
    dn_total = up_total = 0
    for path in _pred_files(reports_dir):
        d = path.stem.replace("pred_", "")
        try:
            df = pd.read_parquet(path, columns=["code", "direction"])
        except Exception:      # noqa: BLE001
            continue
        up_codes = {str(c) for c in df[df["direction"] == "up"]["code"]}
        dn_codes = {str(c) for c in df[df["direction"] == "down"]["code"]}
        inter_dn = sorted(dn_codes & set(held))
        inter_up = sorted(up_codes & set(held))
        dn_total += len(inter_dn)
        up_total += len(inter_up)
        per_day.append({
            "pred_date": d,
            "down_leg_x_holdings": len(inter_dn),
            "up_leg_x_holdings": len(inter_up),
            "down_leg_x_holdings_codes": inter_dn,
            "up_leg_x_holdings_codes": inter_up,
        })
    n_days = len(per_day)
    return {
        "question": "跌榜（会跌 10 只）与持仓的交集，逐日",
        "per_day": per_day,
        "n_days": n_days,
        "n_heldings": len(held),
        "down_leg_x_holdings_total": dn_total,
        "up_leg_x_holdings_total": up_total,
        "denominator": n_days * len(held),
        "note": ("跌榜 = 全池最差的 10 只，持仓来自涨腿（全池最好的 10 只）"
                 "⚠️ **口径**：同一天两腿在构造上不相交（构造事实）；"
                 "本块量的是**产品口径**「实际持仓 × 各日跌榜」——"
                 "**并非恒不相交**，有持仓被跌榜点过名。"
                 "⇒ 跌榜这一半对 long-only 账户是**边际**的：偶尔点到你手上的票，"
                 "却不回答「我该卖哪只」。"),
    }


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------

def main(argv=None) -> int:
    # ★ 第一条可执行语句：抓生成器字节（第 125 层：跑的和盖章的必须是同一份字节）
    expect_bytes = provenance.code_file_sha256(ROOT, GENERATOR)
    args = _parse_args(argv)

    data_root = Path(args.data_root).resolve()
    reports_dir = Path(args.reports_dir).resolve() if args.reports_dir \
        else data_root / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    weights_info = load_weights(reports_dir)
    weights = dict(weights_info.get("weights") or {})
    w_hash = weights_fingerprint(weights)

    as_of = args.as_of.strip()
    if not as_of:
        as_of = _latest_trading_day(data_root / "master" / "daily")
    if not as_of:
        print("[holdings-alert] 找不到任何日线 ⇒ 没有信号日", file=sys.stderr)
        return 2

    names_map = {}
    names_path = data_root / "names.json"
    if names_path.exists():
        try:
            names_map = json.loads(names_path.read_text(encoding="utf-8"))
        except Exception:      # noqa: BLE001
            names_map = {}

    print(f"[holdings-alert] 信号日 {as_of}｜权重指纹 {w_hash}｜"
          f"权重 {weights}", flush=True)

    # ---- 全池重算（流式）------------------------------------------
    pool = _scored_pool(data_root, as_of, weights, names_map)
    ranked = pool["ranked"]
    print("[holdings-alert] 全池重算完成：横截面 " + str(pool["cross_size"])
          + " 只 → 过滤后 " + str(pool["pool_size"]) + " 只有合成分", flush=True)

    # ---- 与生产产物对拍（外部锚）----------------------------------
    parity = _production_parity(reports_dir, as_of, ranked)
    print("[holdings-alert] 生产对拍：" + str(parity.get("verdict"))
          + "｜最大分数差 " + str(parity.get("max_abs_score_diff")), flush=True)
    if parity.get("verdict") != "MATCHES_PRODUCTION":
        print("[holdings-alert] 🚨 重算与生产产物对不上 ⇒ 拒绝出产物"
              "（自己重算自己不是证据，生产产物才是）", file=sys.stderr)
        print(json.dumps(parity, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2

    # ---- 持仓与建仓日 ----------------------------------------------
    state = load_state(reports_dir)
    entry_info = _resolve_entry_day(reports_dir, state)
    basket = [str(c) for c in ((state or {}).get("up") or [])]
    holdings = _resolve_holdings(reports_dir, entry_info.get("entry_day"), basket)
    entry_scores = holdings["entry_scores"]

    crosscheck = _entry_score_crosscheck(reports_dir, entry_info.get("entry_day"),
                                         entry_scores)
    # 口径可比性：建仓日的合成分必须是**同一组权重**算出来的，否则不是同一个量。
    comparable = True
    comparability_note = ""
    hashes = crosscheck.get("weights_hash_at_entry_day") or []
    if hashes and w_hash not in hashes:
        comparable = False
        comparability_note = (f"建仓日权重指纹 {hashes} ≠ 当前 {w_hash} ⇒ "
                              "两个合成分不是同一个量 ⇒ 相对建仓的变化不可比")
    if not entry_scores:
        comparable = False
        comparability_note = comparability_note or "取不到建仓日合成分 ⇒ 相对建仓的变化测不出"

    if comparable and crosscheck.get("max_abs_diff") is not None:
        pass   # 对拍结果写进产物，不在这里判（判据在别处）

    held = [{"code": c, "name": holdings["names"].get(c)} for c in holdings["codes"]]
    alert = build_holdings_alert(held, ranked[["code", "score", "name"]],
                                 entry_scores if comparable else None)

    defect = _defect_measurement(reports_dir, holdings["codes"])

    cuts = bottom_cuts()
    out = {
        "as_of": as_of,
        "generated_by": GENERATOR,
        "question": "我手上这几只里，哪几只已经掉到最差那一档？",
        "population": {
            "basis": holdings.get("basis"),
            "codes": holdings["codes"],
            "n_heldings": len(holdings["codes"]),
            "why": ("持仓 = **建仓日那份配置里真正拿到钱的那些**（alloc_<建仓日>::positions）"
                    "；非调仓日那份 alloc 是当天建议、没被执行 ⇒ 不能当持仓。"
                    "state.up（10 只）是**候选池**，不是持仓。"),
            "basket_from_state": basket,
            "basket_is_candidates_not_holdings": True,
        },
        "entry": {
            "entry_day": entry_info.get("entry_day"),
            "entry_day_source": entry_info.get("entry_day_source"),
            "entry_day_verdict": entry_info.get("verdict"),
            "state_held_since": entry_info.get("state_held_since"),
            "candidate_days_with_same_up_leg": entry_info.get("candidate_days_with_same_up_leg"),
            "entry_scores": entry_scores,
            "entry_scores_source": crosscheck.get("source"),
            "entry_scores_crosscheck": crosscheck,
            "weights_hash_current": w_hash,
            "comparable_with_current_weights": comparable,
            "comparability_note": comparability_note,
        },
        "pool": {
            "as_of": as_of,
            "size": pool["pool_size"],
            "cross_section_size": pool["cross_size"],
            "filtered": pool["filtered"],
            "method": ("流式逐只重算（复用**生产面板那一个**因子实现 "
                       "aplus/panel.py::_factor_series，外加 filter_universe / "
                       "composite_score / buyable_series）；**不建 GB 级面板**"),
            "method_note": ("⚠️ 慢路径 aplus/factor.py::compute_factors **不是**生产实现"
                            "（build_cross_section 的 docstring：实盘直接复用面板）"
                            "⇒ 实测它与面板的 vol20 差 0.0468、合成分差 0.57 ⇒ 名次都变"
                            "⇒ 这里逐股调用面板那个函数"),
            "full_history_factors_used": pool["full_history_factors_used"],
            "ivol_reproduced": pool["ivol_reproduced"],
            "ivol_note": ("IVOL 需要全市场等权收益序列（面板是两趟扫描）。"
                           "当前权重不含 ivol ⇒ 合成分不受影响 ⇒ 本测量台不付这一趟。"
                           "权重一旦含 ivol ⇒ 脚本自动改走两趟（market series）。"),
            "production_parity": parity,
            "weights": weights,
            "weights_source": weights_info.get("source"),
            "weights_caveat": weights_info.get("caveat"),
            "weights_caveat_kind": weights_info.get("caveat_kind"),
        },
        "criteria": {
            **cuts,
            "anchors": {"citation": ANCHOR_CITATION},
            "rule_order": [
                "1. 不在当日全池里 ⇒ UNDECIDED（测不出位次）",
                "2. 缺建仓日合成分 ⇒ UNDECIDED（相对建仓的升降测不出，≠ 没变差）",
                "3. 相对建仓变差（合成分 < 建仓日合成分）且当前分位 ≤ 最差十分位 ⇒ REDUCE（减持/卖出）",
                "4. 在最差五分位 或 相对建仓变差 ⇒ WATCH（观察）",
                "5. 其余 ⇒ HOLD（维持持有）",
            ],
            "primary_basis": "relative_to_entry_score（相对建仓日的变化）",
            "auxiliary_basis": "percentile / is_in_bottom_decile / is_in_bottom_quintile（当日横截面位次）",
        },
        "items": alert["items"],
        "summary": alert["summary"],
        "defect_measurement": defect,
        "boundaries": list(HONEST_BOUNDARIES),
        "what_this_cannot_prove": [
            "本块只回答**横截面位次**（「今天它在全池里排第几」），"
            "不预测下一个交易日涨跌；「全池最差十分位」≠「会跌」",
            "合成分是**横截面相对量**：跨日比较的是相对位次，不是价格涨跌；"
            "两天的合成分相减**不是**收益率",
            "阈值来自书里的**切组方式**（十分位 L=10 / 五分位 L1=L2=5），"
            "「切到最差十分位 ⇒ 减仓」这一步是**本平台的策略选择**，不是书里的结论",
            "本块**不改出单**：不改配置权重、不改换仓频率"
            f"（K={LIVE_REBALANCE_EVERY}）、不生成买卖单",
            "「5 万拿不到融资融券额度」是**外部常识·本仓未核实** ⇒ 只当口径声明",
            "建仓日若解析不出（或建仓日与当前不是同一组权重）⇒ 相对建仓的变化**测不出**，"
            "该条判 UNDECIDED，**不拿别的顶替**",
        ],
    }

    out_path = Path(args.out) if args.out else \
        reports_dir / f"holdings_alert_{as_of}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    provenance.stamp(out_path, GENERATOR, root=ROOT, expect_bytes=expect_bytes,
                     data_root=data_root,
                     extra={
                         "as_of": as_of,
                         "weights_hash": w_hash,
                         "pool_size": pool["pool_size"],
                         "entry_day": entry_info.get("entry_day"),
                         "held_codes": holdings["codes"],
                         "production_parity_verdict": parity.get("verdict"),
                         "production_parity_max_abs_score_diff":
                             parity.get("max_abs_score_diff"),
                         "summary": alert["summary"],
                     })
    # ⚠️ 这里**不许用 f-string**：带嵌套引号的 f-string（`{x['k']}`）在
    # CPython 3.13 会被编译成**独立作用域**，而那个作用域里的名字**按全局解析**
    # ⇒ 局部变量 `holdings` / `item` 变成 NameError。
    # 🚨 实测它还是**非确定性**的：同一份源码（sha256 不变）在不同进程里
    # 编出**不同的字节码**，`__pycache__` 里落下的那一份就是坏的那份
    # ⇒ 「重跑一次就好了」与「重跑一次还是坏」都出现过。
    # ⇒ 改用**普通拼接**（无 f-string ⇒ 无独立作用域 ⇒ 只有一种编法）。
    held_codes = holdings["codes"]
    print("[holdings-alert] 产物 " + str(out_path), flush=True)
    print("[holdings-alert] 持仓 " + str(len(held_codes)) + " 只："
          + " ".join(held_codes), flush=True)
    for item in alert["items"]:
        print("  " + str(item["code"]) + " " + str(item["name"])
              + "｜" + str(item["action"]) + "｜" + str(item["action_criterion"]),
              flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

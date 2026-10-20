"""持仓级降级预警的**接线层**：建仓日 / 持仓 / 建仓分的解析 + 产物组装。

## 为什么从 `scripts/` 搬进 `aplus/`

`scripts/measure_holdings_alert.py`（测量台）里长着这三段解析逻辑，
但**库不许依赖 scripts** ⇒ 日频接线（`scripts/run_daily.py`）用不了它们。
⇒ 单一实现放这里，两条路径共用同一套口径。

## 三条口径纪律（与预注册 `docs/PREREG_HOLDINGS_ALERT_2026-10-21.md` 一字不差）

1. **建仓分从建仓日那份预测文件读**（`pred_<建仓日>.parquet::score`），
   **不重算建仓日的全池**。
   依据：测量台已验过「建仓分与当日涨腿落盘分**逐位一致**」
   （对拍 `max_abs_score_diff = 0.0`）。
   ⇒ 持仓股票若**不在**建仓日那份名单里 ⇒ **没有建仓分**
   ⇒ 走既有第 4 态 `UNDECIDED`（「测不出 ≠ 没变差」），
   **不许**用 0 或当前分顶替。
2. **持仓 = 建仓日那份配置里真正拿到钱的那些**
   （`alloc_<建仓日>.json::positions`）——
   不是 `holdings.json::up`（那是候选池），
   也不是最新那份 `alloc_<今天>`（那是今天的建议，非调仓日没被执行）。
3. **缺建仓分 / 不在当日全池 ⇒ `UNDECIDED`**。

## 本模块**不**做什么

- **不预测涨跌、不算盈亏、不改权重、不改 K、不生成买卖单。**
  它只回答**横截面位次**：「今天它在全池里排第几、比建仓那天是好了还是差了」。
- **不建 GB 级面板。** 日频路径的全池是 `run_daily` 的 `predict` 步骤
  **已经在内存里**的那一份（同一个 `build_cross_section` ⇒ 同一套因子实现），
  本模块只给它排序、分位、定级。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from .evolve import LIVE_REBALANCE_EVERY
from .factor import composite_score
from .holdings_alert import (
    ANCHOR_CITATION,
    HONEST_BOUNDARIES,
    bottom_cuts,
    build_holdings_alert,
)
from .live_rebalance import load_state
from .reports import weights_fingerprint

#: 仓根（本模块不读盘，只用它把仓内路径写成**仓相对**形式）。
REPO_ROOT = Path(__file__).resolve().parents[1]

#: 与生产产物对拍的容差（合成分是 z 量纲，1e-9 已远严于浮点噪声）。
#: ⚠️ 收紧它会误报，放宽它会让真错误被放过 —— 判据在
#: `tests/test_holdings_alert.py`（测量台那份）与
#: `tests/test_holdings_alert_wiring.py`（本接线层那份）。
PARITY_TOL = 1e-9

#: 建仓分与配置产物里那份分数的对拍容差（同一口径的第二份来源）。
ENTRY_SCORE_PARITY_TOL = 1e-9

#: 判据的**有序阶梯**（预注册 §2 逐条）。
#: ⚠️ **一个字不许改**（第 91 层：判据是事前写死的）。
#:   测量台（`scripts/measure_holdings_alert.py`）里有一份同样的字面量，
#:   两份之间由 `tests/test_holdings_alert_wiring.py` 的对拍守卫钉住。
RULE_ORDER = (
    "1. 不在当日全池里 ⇒ UNDECIDED（测不出位次）",
    "2. 缺建仓日合成分 ⇒ UNDECIDED（相对建仓的升降测不出，≠ 没变差）",
    "3. 相对建仓变差（合成分 < 建仓日合成分）且当前分位 ≤ 最差十分位 ⇒ REDUCE（减持/卖出）",
    "4. 在最差五分位 或 相对建仓变差 ⇒ WATCH（观察）",
    "5. 其余 ⇒ HOLD（维持持有）",
)

#: 「持仓是哪些」的依据说明（产物 `population.why`）。
POPULATION_WHY = (
    "持仓 = **建仓日那份配置里真正拿到钱的那些**（alloc_<建仓日>::positions）；"
    "非调仓日那份 alloc 是当天建议、没被执行 ⇒ 不能当持仓。"
    "state.up（10 只）是**候选池**，不是持仓。"
)


# ── 小工具 ──────────────────────────────────────────────────────────

def repo_relative(path: Path | str) -> str:
    """仓内路径写成**仓相对**形式；仓外原样返回（不炸、不藏）。"""
    p = Path(path)
    try:
        return str(p.resolve().relative_to(REPO_ROOT.resolve()))
    except ValueError:
        return str(p)


def predictions_dir(reports_dir) -> Path:
    return Path(reports_dir) / "predictions"


def pred_files(reports_dir) -> list[Path]:
    """按日期排序的预测文件（`pred_<YYYY-MM-DD>.parquet`）。"""
    d = predictions_dir(reports_dir)
    if not d.exists():
        return []
    return sorted(d.glob("pred_*.parquet"))


def _read_pred(path: Path, columns: list[str] | None = None):
    """读一份预测文件。**读不出来就返回 None**（缺就是缺，不编）。"""
    try:
        return pd.read_parquet(path, columns=columns)
    except Exception:      # noqa: BLE001 - 单份读不出来不该杀掉整条链路
        return None


# ── ① 建仓日 ────────────────────────────────────────────────────────

def resolve_entry_day(reports_dir, state: dict | None) -> dict:
    """「持仓是哪天定的」——**两个来源，先信 state，再靠集合唯一匹配**。

    ① `holdings.json::held_since`（真值；旧 state 文件没有它 ⇒ 缺就是缺）
    ② **集合唯一匹配**：`state.up`（建仓日那份涨腿）逐位对应某一天的涨腿
       ⇒ 那天就是建仓日。**必须唯一**：0 个匹配 ⇒ 判不出；
       >1 个匹配 ⇒ 判不出（不许随便挑一天）。

    ⚠️ 判不出 ⇒ **不许猜** ⇒ 相对建仓的变化**测不出** ⇒ 每条 `UNDECIDED`。
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
        if (predictions_dir(reports_dir) / ("pred_%s.parquet" % d)).exists():
            out["entry_day"] = d
            out["entry_day_source"] = "holdings.json::held_since"
            out["verdict"] = "RESOLVED_FROM_STATE"
            return out
        out["sources_tried"].append("held_since=%s（无对应 pred 文件）" % d)

    if not basket:
        out["verdict"] = "NO_BASKET_IN_STATE"
        return out

    matches = []
    for path in pred_files(reports_dir):
        d = path.stem.replace("pred_", "")
        df = _read_pred(path, columns=["code", "direction"])
        if df is None or df.empty:
            continue
        up_codes = {str(c) for c in df[df["direction"] == "up"]["code"]}
        if up_codes == set(basket):
            matches.append(d)
    out["candidate_days_with_same_up_leg"] = matches
    if len(matches) == 1:
        out["entry_day"] = matches[0]
        out["entry_day_source"] = ("唯一集合匹配：state.up == pred_%s 的涨腿" % matches[0])
        out["verdict"] = "RESOLVED_BY_UNIQUE_SET_MATCH"
    elif not matches:
        out["verdict"] = "NO_MATCHING_DAY"
    else:
        out["verdict"] = "AMBIGUOUS_MATCH"
    return out


# ── ② 持仓 ──────────────────────────────────────────────────────────

def resolve_holdings(reports_dir, entry_day: str | None) -> dict:
    """实际持仓 = **建仓日那份配置里真正拿到钱的那些**。

    ⚠️ `alloc_<date>.json::positions` 是「那天的**计划**持仓」，
    只有**建仓日**那份才是「用户手上真有的」——
    非调仓日那份是当天的建议，没被执行。
    ⇒ 必须锚在**建仓日**，不能拿最新那份当持仓。
    """
    out: dict = {"basis": None, "codes": [], "entry_scores": {},
                 "names": {}, "n_positions": 0}
    if not entry_day:
        out["note"] = "建仓日未解析 ⇒ 持仓名单与建仓分都取不到（测不出）"
        return out
    alloc_path = Path(reports_dir) / "allocation" / ("alloc_%s.json" % entry_day)
    if not alloc_path.exists():
        out["note"] = "缺 %s ⇒ 无法取持仓" % alloc_path.name
        return out
    try:
        alloc = json.loads(alloc_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        out["note"] = "%s 读不出来（%r）⇒ 无法取持仓" % (alloc_path.name, exc)
        return out
    positions = alloc.get("positions") or []
    out["basis"] = repo_relative(alloc_path) + "::positions"
    out["codes"] = [str(p.get("code")) for p in positions if isinstance(p, dict)]
    for p in positions:
        if not isinstance(p, dict):
            continue
        code = str(p.get("code"))
        out["names"][code] = p.get("name")
        sc = p.get("score")
        if sc is not None:
            try:
                out["entry_scores"][code] = float(sc)
            except (TypeError, ValueError):
                pass
    out["n_positions"] = len(out["codes"])
    rb = alloc.get("rebalance")
    if isinstance(rb, list):
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


# ── ③ 建仓分（从建仓日那份预测文件读，**不重算全池**）─────────────

def entry_scores_from_predictions(reports_dir, entry_day: str | None,
                                  alloc_scores: dict | None = None) -> dict:
    """建仓日合成分 = `pred_<建仓日>.parquet` **涨腿**落盘的 `score`。

    ⚠️ 为什么读文件而不是重算：测量台已验「建仓分与当日涨腿落盘分**逐位一致**」
    （对拍 `max_abs_score_diff = 0.0`）⇒ 两者是**同一个量**，
    而重算建仓日的全池要再付一次 GB 级代价。
    ⇒ 读文件是**同一个量的更便宜的一条路**，不是换了一个量。

    同时与配置产物里那份分数**对拍**（同一口径的第二份来源）：
    对不上 ⇒ 两份来源互相矛盾 ⇒ 建仓分**不可信** ⇒ 相对变化不可比。
    """
    out: dict = {"scores": {}, "crosscheck": {"available": False}}
    if not entry_day:
        return out
    path = predictions_dir(reports_dir) / ("pred_%s.parquet" % entry_day)
    if not path.exists():
        out["crosscheck"] = {"available": False,
                             "note": "缺 %s" % path.name}
        return out
    df = _read_pred(path)
    if df is None or df.empty or "score" not in df.columns:
        out["crosscheck"] = {"available": False,
                             "note": "%s 读不出来或没有 score 列" % path.name}
        return out
    up = df[df["direction"] == "up"]
    leg: dict = {}
    for _, r in up.iterrows():
        try:
            leg[str(r["code"])] = float(r["score"])
        except (TypeError, ValueError):
            continue
    out["scores"] = leg

    cross: dict = {"available": True,
                   "source": repo_relative(path) + "::score",
                   "codes_in_leg": sorted(leg)}
    if "weights_hash" in df.columns:
        cross["weights_hash_at_entry_day"] = sorted(
            {str(v) for v in df["weights_hash"].dropna().unique()})
    ref = dict(alloc_scores or {})
    diffs = [abs(leg[c] - s) for c, s in ref.items() if c in leg]
    cross["n_compared"] = len(diffs)
    cross["max_abs_diff"] = max(diffs) if diffs else None
    cross["tolerance"] = ENTRY_SCORE_PARITY_TOL
    if diffs:
        cross["verdict"] = ("MATCHES_ALLOCATION_ARTIFACT"
                            if max(diffs) <= ENTRY_SCORE_PARITY_TOL
                            else "MISMATCH_DO_NOT_TRUST_THIS_ARTIFACT")
    else:
        cross["verdict"] = "NO_COMPARABLE_PAIR"
    out["crosscheck"] = cross
    return out


def comparability(entry_scores: dict, crosscheck: dict, weights_hash: str | None) -> tuple:
    """建仓分与**当前**合成分是不是同一个量。

    口径可比性：建仓日的合成分必须是**同一组权重**算出来的，否则不是同一个量
    ⇒ 相对变化**不可比** ⇒ 视同建仓分缺失 ⇒ `UNDECIDED`。
    """
    note = ""
    hashes = crosscheck.get("weights_hash_at_entry_day") or []
    if hashes and weights_hash is not None and weights_hash not in hashes:
        return False, ("建仓日权重指纹 %s ≠ 当前 %s ⇒ 两个合成分不是同一个量 "
                       "⇒ 相对建仓的变化不可比" % (hashes, weights_hash))
    if crosscheck.get("verdict") == "MISMATCH_DO_NOT_TRUST_THIS_ARTIFACT":
        return False, ("建仓分与配置产物里那份分数对不上（最大差 %s > 容差 %s）"
                       "⇒ 两个来源互相矛盾 ⇒ 相对建仓的变化不可比"
                       % (crosscheck.get("max_abs_diff"), ENTRY_SCORE_PARITY_TOL))
    if not entry_scores:
        return False, "取不到建仓日合成分 ⇒ 相对建仓的变化测不出"
    return True, note


# ── ④ 全池（当日横截面）──────────────────────────────────────────────

def scored_universe(universe: pd.DataFrame, weights: dict | None) -> pd.DataFrame:
    """给全池算合成分并按分数降序排。

    ⚠️ 这一段是 `aplus/predict.py::build_predictions` 内部的**同一条语句路径**
    （同一个 `composite_score`、同一个 `notna` 过滤、同一个排序），
    抄在这里是因为 `build_predictions` 只返回两腿（各 10 只），
    而本块要的是**全池**（分位只在全池上才有意义）。
    ⇒ 两份之间**必须**由守卫对拍
    （`tests/test_holdings_alert_wiring.py`：涨腿 10 只的代码集合与分数
    必须与 `build_predictions` 逐位相同）——
    抄一份而没有对拍 = 两份副本之间没有守卫（第 137 层）。
    """
    df = universe.copy()
    df["score"] = composite_score(df, weights)
    df = df[df["score"].notna()].sort_values("score", ascending=False)
    return df.reset_index(drop=True)


def production_parity(reports_dir, as_of: str, up_leg, down_leg) -> dict:
    """**本块要用的两腿** vs 生产落盘的那份（外部锚）。

    生产产物 `pred_<as_of>.parquet` 里的 `score` 是**生产自己算的合成分**
    ⇒ 它是外部锚。对不上 ⇒ 落盘那份不是当前代码产出的 ⇒ 判决不可信。

    ⚠️ **入参是「两腿」而不是「全池」**：两腿的取法由
    `aplus/predict.py::build_predictions` 决定（三段形态：池满 / 池小 / 池中），
    **本模块不许自己重写那套取法**（抄一份就会在池子变小时分叉）。
    """
    path = predictions_dir(reports_dir) / ("pred_%s.parquet" % as_of)
    out: dict = {"checked_against": repo_relative(path) if path.exists() else None,
                 "available": path.exists()}
    if not path.exists():
        out["verdict"] = "NO_PRODUCTION_ARTIFACT_TO_COMPARE_AGAINST"
        return out
    prod = _read_pred(path)
    if prod is None or prod.empty:
        out["verdict"] = "PRODUCTION_ARTIFACT_UNREADABLE"
        return out

    def _cmp(mine, theirs: pd.DataFrame) -> dict:
        t = {str(r["code"]): float(r["score"]) for _, r in theirs.iterrows()}
        m = {str(r["code"]): float(r["score"]) for _, r in mine.iterrows()}
        common = set(t) & set(m)
        max_diff = max((abs(t[c] - m[c]) for c in common), default=float("inf"))
        return {
            "codes_match": set(t) == set(m),
            "prod_codes": sorted(t),
            "recomputed_codes": sorted(m),
            "max_abs_score_diff": (None if common and max_diff == float("inf")
                                   else max_diff),
            "n_common": len(common),
        }

    cu = _cmp(up_leg, prod[prod["direction"] == "up"])
    cd = _cmp(down_leg, prod[prod["direction"] == "down"])
    out["up_leg"] = cu
    out["down_leg"] = cd
    diffs = [d for d in (cu["max_abs_score_diff"], cd["max_abs_score_diff"])
             if d is not None]
    out["max_abs_score_diff"] = max(diffs) if diffs else None
    ok = (cu["codes_match"] and cd["codes_match"]
          and out["max_abs_score_diff"] is not None
          and out["max_abs_score_diff"] <= PARITY_TOL)
    out["tolerance"] = PARITY_TOL
    out["verdict"] = ("MATCHES_PRODUCTION" if ok
                      else "MISMATCH_DO_NOT_TRUST_THIS_ARTIFACT")
    return out


def defect_measurement(reports_dir, held: list[str]) -> dict:
    """跌榜（会跌 10 只）∩ 持仓，逐日。

    这是**本块存在的理由**：跌榜来自全池最差的一端，持仓来自涨腿
    ⇒ 同一天两腿在构造上就不相交（**构造事实**）。
    ⇒ 但产品口径（**实际持仓** × **各日跌榜**）**并非恒不相交**。
    """
    per_day = []
    dn_total = up_total = 0
    for path in pred_files(reports_dir):
        d = path.stem.replace("pred_", "")
        df = _read_pred(path, columns=["code", "direction"])
        if df is None or df.empty:
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


# ── ⑤ 产物组装 ──────────────────────────────────────────────────────

def what_this_cannot_prove() -> list[str]:
    """产物里**必须逐条**出现的诚实边界（预注册 §5）。"""
    return [
        "本块只回答**横截面位次**（「今天它在全池里排第几」），"
        "不预测下一个交易日涨跌；「全池最差十分位」≠「会跌」",
        "合成分是**横截面相对量**：跨日比较的是相对位次，不是价格涨跌；"
        "两天的合成分相减**不是**收益率",
        "阈值来自书里的**切组方式**（十分位 L=10 / 五分位 L1=L2=5），"
        "「切到最差十分位 ⇒ 减仓」这一步是**本平台的策略选择**，不是书里的结论",
        "本块**不改出单**：不改配置权重、不改换仓频率"
        "（K=%s）、不生成买卖单" % LIVE_REBALANCE_EVERY,
        "「5 万拿不到融资融券额度」是**外部常识·本仓未核实** ⇒ 只当口径声明",
        "建仓日若解析不出（或建仓日与当前不是同一组权重）⇒ "
        "相对建仓的变化**测不出**，该条判 UNDECIDED，**不拿别的顶替**",
        "本块**不产生收益**：它不预测涨跌、不算盈亏、不改权重、不改 K、"
        "不生成买卖单——它只把持仓的**相对位置**说清楚",
    ]


def build_holdings_alert_artifact(
    *,
    reports_dir,
    as_of: str,
    ranked: pd.DataFrame,
    weights: dict | None,
    weights_hash: str | None,
    generated_by: str,
    pool_method: str,
    pool_method_note: str,
    legs=None,
    pool_extra: dict | None = None,
    state: dict | None = None,
    extra: dict | None = None,
) -> dict:
    """把「全池 + 持仓 + 建仓分」组装成**产物字典**（不写盘）。

    ⚠️ 本函数**不写盘**：写盘与盖章由调用方（`scripts/run_daily.py`）做，
    因为「第 125 层」要求盖章那一刻的字节与开跑时一致 ⇒
    抓字节的时机必须在调用方手里。
    """
    if state is None:
        state = load_state(reports_dir)
    entry_info = resolve_entry_day(reports_dir, state)
    entry_day = entry_info.get("entry_day")
    holdings = resolve_holdings(reports_dir, entry_day)
    basket = [str(c) for c in ((state or {}).get("up") or [])]

    entry = entry_scores_from_predictions(reports_dir, entry_day,
                                         holdings.get("entry_scores"))
    entry_scores = entry["scores"]
    crosscheck = entry["crosscheck"]
    comparable, comparability_note = comparability(entry_scores, crosscheck,
                                                   weights_hash)

    held = [{"code": c, "name": holdings["names"].get(c)}
            for c in holdings["codes"]]
    alert = build_holdings_alert(
        held,
        ranked[[c for c in ("code", "score", "name") if c in ranked.columns]],
        entry_scores if comparable else None,
    )

    cuts = bottom_cuts()
    if legs is not None:
        up_leg, down_leg = legs
        parity = production_parity(reports_dir, as_of, up_leg, down_leg)
    else:
        parity = {"verdict": "NOT_CHECKED",
                  "note": "调用方没给两腿 ⇒ 本块没有做生产对拍"}
    pool: dict = {
        "as_of": as_of,
        "size": int(len(ranked)),
        "method": pool_method,
        "method_note": pool_method_note,
        "weights": dict(weights or {}),
        "weights_hash": weights_hash,
        "production_parity": parity,
    }
    if pool_extra:
        pool.update(pool_extra)

    out: dict[str, Any] = {
        "as_of": as_of,
        "generated_by": generated_by,
        "question": "我手上这几只里，哪几只已经掉到最差那一档？",
        "population": {
            "basis": holdings.get("basis"),
            "codes": holdings["codes"],
            # ⚠️ 键名**必须与测量台产物逐字相同**（`n_heldings`）——
            #   两个产出者写**同一个文件名** ⇒ 键名不一样 ⇒ 页面读谁取决于
            #   「最后是谁写的」⇒ **同一屏上的数会随写入顺序变**。
            #   （实测踩到：日频写 `n_holdings`、测量台写 `n_heldings`
            #     ⇒ 页面显示「全池 0 只持仓」而实际有 5 只。）
            #   ⇒ 守卫：`tests/test_holdings_alert_wiring.py`
            #   的「population 键集必须与测量台一致」。
            "n_heldings": len(holdings["codes"]),
            "why": POPULATION_WHY,
            "basket_from_state": basket,
            "basket_is_candidates_not_holdings": True,
            # ⚠️ 建仓日判不出 ⇒ 持仓名单**取不到**（不是「空仓」）。
            #   这一句必须**明说**，否则界面会把它渲染成「你没有持仓」。
            "note": holdings.get("note"),
        },
        "entry": {
            "entry_day": entry_day,
            "entry_day_source": entry_info.get("entry_day_source"),
            "entry_day_verdict": entry_info.get("verdict"),
            "state_held_since": entry_info.get("state_held_since"),
            "candidate_days_with_same_up_leg":
                entry_info.get("candidate_days_with_same_up_leg"),
            "entry_scores": entry_scores if comparable else {},
            "entry_scores_source": crosscheck.get("source"),
            "entry_scores_crosscheck": crosscheck,
            "weights_hash_current": weights_hash,
            "comparable_with_current_weights": comparable,
            "comparability_note": comparability_note,
        },
        "pool": pool,
        "criteria": {
            **cuts,
            "anchors": {"citation": ANCHOR_CITATION},
            "rule_order": list(RULE_ORDER),
            "primary_basis": "relative_to_entry_score（相对建仓日的变化）",
            "auxiliary_basis": ("percentile / is_in_bottom_decile / "
                                "is_in_bottom_quintile（当日横截面位次）"),
        },
        "items": alert["items"],
        "summary": alert["summary"],
        "defect_measurement": defect_measurement(reports_dir, holdings["codes"]),
        "boundaries": list(HONEST_BOUNDARIES),
        "what_this_cannot_prove": what_this_cannot_prove(),
    }
    if extra:
        out.update(extra)
    return out

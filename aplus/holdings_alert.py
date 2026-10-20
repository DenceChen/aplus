"""持仓级降级预警：把「我手上这几只，掉到最差那一档了吗」说清楚。

## 它补的是 D1 的哪一个洞

D1 每天给**全市场**两个榜：会涨的 10 只、会跌的 10 只。
两个榜都是从**全池**里挑出来的极端 ⇒ **用户手上的持仓天然落在两榜之外**：
跌榜是全市场最差的 10 只，而持仓来自涨榜（全池最好的 10 只）。
⇒ **同一天**的两腿在构造上就不相交（涨腿 ∩ 跌腿 = 0）——那是**构造事实**。
⇒ 但换成产品口径「**实际持仓** × **各日跌榜**」，**并非恒不相交**：
   实测有持仓被跌榜点过名（读数见产物的 `defect_measurement`），
   而跌榜**依然不回答**用户真正的问题（「我该卖哪只」）。
⇒ 结论：**跌榜这一半对 long-only 账户是边际的**，本模块补的就是它缺的那一问。

本模块回答的是那个真正的问题：
**「我手上这几只里，哪几只已经掉到最差那一档？」**
（不是「哪只会跌」——那是另一个产品，见下方边界。）

## 三条诚实边界（写进产物，不许只在文档里说）

1. **这不是做空信号。** A 股散户融不到券 ⇒ 唯一可执行形态是**减持/卖出**。
   跌榜自己的闸门 `data/reports/downleg_gate.json::executable` 也写着
   「不考虑做空限制 ⇒ 会高估因子的收益」（石川《因子投资方法与实践》L768）。
2. **这不是涨跌预测。** 本模块只回答**横截面位次**（今天它在全池里排第几），
   不预测下一交易日涨跌。「全池最差十分位」≠「会跌」。
3. **它不改变出单。** 不改配置权重、不改换仓频率（K = `aplus.evolve.LIVE_REBALANCE_EVERY`）、不生成买卖单。
   它只把持仓的**相对位置**说清楚。

## 判据：相对建仓日是主判据，绝对分位是辅助

- **主判据** `relative_to_entry_score` = 当前合成分 − 建仓日合成分。
  「该不该减」问的是「比买的时候差了吗」，不是「它在全池里第几」。
- **辅助** `percentile` / `is_in_bottom_decile` / `is_in_bottom_quintile`：
  当前合成分在**全池**（当日横截面）里的位次。
- ⇒ **`entry_scores` 缺失时，`relative_to_entry_score` 显式为 `None`**，
  该条 `action` = `UNDECIDED`（测不出 ≠ 没变差）。**不许用别的顶替。**

## 阈值从哪来（不许手打）

两个阈值都是**从外部锚推导**的，改锚阈值跟着变：

| 阈值 | 值 | 外部锚 |
|---|---|---|
| `bottom_decile_percentile` | 1/10 = 0.10 | 石川 §排序分组：单变量排序「按十分位数把全部股票分成 10 组（L=10）」⇒ 最差那一组 = 最差 10% |
| `bottom_quintile_percentile` | 1/5 = 0.20 | 石川 §排序分组：独立双重排序「取这两个变量各自的五分位数（L1=L2=5）」⇒ 最差那一组 = 最差 20% |

锚的原文位置：`data/books/cn_factor_lines.txt`（L1325-1341 / L1493 / L1656 / L5194）。

⚠️ **锚管的是「横截面怎么切组」，不是「散户该什么时候卖」**。
「切到最差十分位 ⇒ 减仓」这一步是**本平台的策略选择**，
在 `docs/PREREG_HOLDINGS_ALERT_2026-10-21.md` 里预注册，不是书里的结论。

## 纯函数层

`build_holdings_alert()` 不读盘、不写盘、不碰 `data/`。
它只吃三样东西：**持仓名单**、**全池合成分**、**建仓日合成分**。
"""
from __future__ import annotations

from .evolve import LIVE_REBALANCE_EVERY

from typing import Any, Iterable

import pandas as pd

# --------------------------------------------------------------------------
# 外部锚（改这两个常量 ⇒ 阈值跟着变；测试钉住这一点）
# --------------------------------------------------------------------------

#: 石川《因子投资方法与实践》§排序分组：单变量排序「按十分位数把全部股票分成 10 组」。
#: 锚原文：`data/books/cn_factor_lines.txt` L1325-1341、L5194。
SORT_GROUPS_SINGLE = 10

#: 石川同节：独立双重排序「取这两个变量各自的五分位数」（L1=L2=5）。
#: 锚原文：`data/books/cn_factor_lines.txt` L1493、L1656。
SORT_GROUPS_DOUBLE = 5

#: 锚出处（写进产物，用户能自己去查）。
ANCHOR_CITATION = (
    "石川《因子投资方法与实践》§排序分组"
    "（data/books/cn_factor_lines.txt L1325-1341 十分位 L=10；L1493/L1656 五分位 L1=L2=5）"
)

ACTION_REDUCE = "REDUCE"
ACTION_WATCH = "WATCH"
ACTION_HOLD = "HOLD"
ACTION_UNDECIDED = "UNDECIDED"

ACTION_LABELS_CN = {
    ACTION_REDUCE: "减仓（减持/卖出）",
    ACTION_WATCH: "观察",
    ACTION_HOLD: "持有",
    ACTION_UNDECIDED: "未定（测不出）",
}

#: 写进产物的诚实边界（每条都指向产物路径::JSON 路径或仓内可查位置）。
HONEST_BOUNDARIES = (
    "这不是做空信号：A 股散户融不到券 ⇒ 唯一可执行形态是减持/卖出"
    "（跌榜自己的闸门 data/reports/downleg_gate.json::executable 同样写着"
    "「不考虑做空限制 ⇒ 会高估因子的收益」）",
    "这不是涨跌预测：本块只回答横截面位次（今天在全池里排第几），"
    "「全池最差十分位」≠「会跌」",
    "它不改变出单：不改配置权重、不改换仓频率"
    f"（K={LIVE_REBALANCE_EVERY}）、不生成买卖单",
    "跌榜（会跌 10 只）是全市场榜单，对 long-only 账户是**边际**的："
    "持仓来自涨榜 ⇒ 同一天两腿在构造上就不相交；"
    "但「实际持仓 × 各日跌榜」**并非恒不相交**（有持仓被跌榜点过名）"
    "⇒ 读数看产物的 defect_measurement，**别把「不相交」当事实念**",
    "合成分是**横截面相对量**：跨日比较的是「相对位次」而不是价格涨跌，"
    "两天的合成分不可当成收益率加减",
    "「5 万拿不到融资融券额度」是外部常识·本仓未核实 ⇒ 只当口径声明，不当判据",
)


def bottom_cuts() -> dict[str, float]:
    """从外部锚推导两个分位阈值。**不在 import 时定死** ⇒ 改锚即改阈值。"""
    if SORT_GROUPS_SINGLE < 1 or SORT_GROUPS_DOUBLE < 1:
        raise ValueError(
            f"排序分组数必须 ≥1，收到 single={SORT_GROUPS_SINGLE} double={SORT_GROUPS_DOUBLE}"
        )
    return {
        "bottom_decile_percentile": 1.0 / float(SORT_GROUPS_SINGLE),
        "bottom_quintile_percentile": 1.0 / float(SORT_GROUPS_DOUBLE),
    }


def _normalise_codes(series: Iterable) -> list[str]:
    out = []
    for c in series:
        s = str(c).strip()
        if s:
            out.append(s)
    return out


def _pool_frame(scored_universe: pd.DataFrame) -> pd.DataFrame:
    """把全池摆正：`code` / `score` 两列必备，score 非空，code 去空白。"""
    if not isinstance(scored_universe, pd.DataFrame):
        raise TypeError(
            f"scored_universe 必须是 DataFrame（要有 code / score 两列），"
            f"收到 {type(scored_universe).__name__}"
        )
    missing = [c for c in ("code", "score") if c not in scored_universe.columns]
    if missing:
        raise ValueError(f"scored_universe 缺列：{missing}（实际列：{list(scored_universe.columns)}）")
    df = scored_universe.loc[:, [c for c in ("code", "score", "name") if c in scored_universe.columns]].copy()
    df["code"] = _normalise_codes(df["code"])
    df = df[df["code"] != ""]
    df["score"] = pd.to_numeric(df["score"], errors="coerce")
    df = df[df["score"].notna()]
    return df.reset_index(drop=True)


def _rank_and_percentile(pool: pd.DataFrame) -> pd.DataFrame:
    """rank 1 = 分最高；percentile 越高越好（最差那只是 0）。"""
    df = pool.sort_values("score", ascending=False, kind="mergesort").reset_index(drop=True)
    n = len(df)
    df["rank_in_universe"] = df["score"].rank(ascending=False, method="min").astype("int64")
    if n > 1:
        df["percentile"] = (n - df["rank_in_universe"]) / float(n - 1)
    else:
        df["percentile"] = 1.0
    df["percentile"] = df["percentile"].clip(0.0, 1.0)
    return df


def _fmt(x: float | None, nd: int = 4) -> str:
    return "缺失" if x is None else f"{x:.{nd}f}"


def _criterion(
    action: str,
    code: str,
    cur: float | None,
    entry: float | None,
    rel: float | None,
    pct: float | None,
    cuts: dict[str, float],
    in_pool: bool,
) -> str:
    """把「这条建议凭什么这么判」写成一整句。**产物里必须带它。**"""
    dec = cuts["bottom_decile_percentile"]
    qui = cuts["bottom_quintile_percentile"]
    if action == ACTION_UNDECIDED:
        if not in_pool:
            return (
                f"{code}：不在今日全池里（可能被过滤/停牌/缺数据）⇒ 测不出位次，"
                f"不判减仓也不判持有（测不出 ≠ 没有）"
            )
        return (
            f"{code}：缺建仓日合成分 ⇒ 相对建仓的升降**测不出**（≠ 没变差）"
            f"⇒ 判「未定」；当前全池分位 {_fmt(pct)} 仅作辅助，不单独构成减仓依据"
        )
    if action == ACTION_REDUCE:
        return (
            f"{code}：合成分 {_fmt(cur)} < 建仓日合成分 {_fmt(entry)}"
            f"（相对建仓 {rel:+.4f}）**且**当前全池分位 {_fmt(pct)} ≤ 最差十分位阈值"
            f" {_fmt(dec)}（锚：{ANCHOR_CITATION}）⇒ 建议减持/卖出"
            f"（A 股散户融不到券 ⇒ 不是做空）"
        )
    if action == ACTION_WATCH:
        why = []
        if pct is not None and pct <= qui:
            why.append(f"全池分位 {_fmt(pct)} ≤ 最差五分位阈值 {_fmt(qui)}（锚：{ANCHOR_CITATION}）")
        if rel is not None and rel < 0:
            why.append(f"合成分 {_fmt(cur)} < 建仓日合成分 {_fmt(entry)}（相对建仓 {rel:+.4f}）")
        # ⚠️ 不用 f-string：`{'；'.join(why)}` 这种**嵌套引号**的 f-string 在
        # CPython 3.13 会被编成独立作用域，局部变量按**全局**解析 ⇒ 间歇性 NameError。
        return code + "：" + "；".join(why) + " ⇒ 建议观察（尚未同时满足「相对建仓变差 + 最差十分位」）"
    return (
        f"{code}：全池分位 {_fmt(pct)} > 最差五分位阈值 {_fmt(qui)}"
        f"（锚：{ANCHOR_CITATION}）**且**合成分 {_fmt(cur)} ≥ 建仓日合成分 {_fmt(entry)}"
        f"（相对建仓 {rel:+.4f}）⇒ 维持持有"
    )


def build_holdings_alert(
    held: list,
    scored_universe: pd.DataFrame,
    entry_scores: dict | None = None,
) -> dict:
    """持仓级降级预警。

    Parameters
    ----------
    held:
        当前持仓，形如 ``[{"code": ..., "name": ...}, ...]``。
    scored_universe:
        **全池**横截面，必须有 ``code`` / ``score`` 两列（``score`` = 合成分，
        越高越好）。**不是 top-10**——分位/排名只有在全池上才有意义。
    entry_scores:
        ``{code: 建仓那天的合成分}``。可为 ``None`` ⇒ 相对建仓的变化**显式缺失**
        （不许用别的顶替），该条动作判 ``UNDECIDED``。

    Returns
    -------
    dict
        ``items`` 每只持仓一条（code / name / score / rank_in_universe /
        percentile / is_in_bottom_decile / is_in_bottom_quintile /
        relative_to_entry_score / action / action_criterion），
        外加 ``summary`` / ``cutoffs`` / ``pool_size`` / ``boundaries``。
    """
    if held is None:
        raise TypeError("held 不能为 None（缺持仓名单 ⇒ 不是空名单）")
    if not isinstance(held, (list, tuple)):
        raise TypeError(f"held 必须是 list，收到 {type(held).__name__}")

    cuts = bottom_cuts()
    pool = _rank_and_percentile(_pool_frame(scored_universe))
    pool_size = len(pool)
    by_code: dict[str, dict] = {}
    for row in pool.itertuples(index=False):
        by_code[str(row.code)] = {
            "score": float(row.score),
            "rank_in_universe": int(row.rank_in_universe),
            "percentile": float(row.percentile),
            "name": getattr(row, "name", None),
        }

    entry_map: dict[str, float] = {}
    if entry_scores:
        for k, v in entry_scores.items():
            if v is None:
                continue
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            if fv == fv:  # NaN 检查
                entry_map[str(k).strip()] = fv

    items: list[dict[str, Any]] = []
    for h in held:
        if not isinstance(h, dict):
            raise TypeError(f"held 的每一项必须是 dict（code/name），收到 {type(h).__name__}")
        code = str(h.get("code", "")).strip()
        name = h.get("name")
        prow = by_code.get(code)
        in_pool = prow is not None
        score = prow["score"] if in_pool else None
        rank = prow["rank_in_universe"] if in_pool else None
        pct = prow["percentile"] if in_pool else None
        if not name and in_pool:
            name = prow.get("name")
        entry = entry_map.get(code)
        rel = None if (score is None or entry is None) else score - entry
        rel_pct = None if (rel is None or entry in (None, 0.0)) else rel / abs(entry)

        is_dec = None if pct is None else bool(pct <= cuts["bottom_decile_percentile"])
        is_qui = None if pct is None else bool(pct <= cuts["bottom_quintile_percentile"])

        if not in_pool:
            action = ACTION_UNDECIDED
        elif entry is None:
            action = ACTION_UNDECIDED
        elif rel < 0 and is_dec:
            action = ACTION_REDUCE
        elif is_qui or rel < 0:
            action = ACTION_WATCH
        else:
            action = ACTION_HOLD

        items.append({
            "code": code,
            "name": name,
            "score": score,
            "rank_in_universe": rank,
            "percentile": pct,
            "is_in_bottom_decile": is_dec,
            "is_in_bottom_quintile": is_qui,
            "entry_score": entry,
            "relative_to_entry_score": rel,
            "relative_to_entry_pct": rel_pct,
            "action": action,
            "action_criterion": _criterion(action, code, score, entry, rel, pct, cuts, in_pool),
        })

    summary = {a: 0 for a in (ACTION_REDUCE, ACTION_WATCH, ACTION_HOLD, ACTION_UNDECIDED)}
    for it in items:
        summary[it["action"]] += 1

    return {
        "pool_size": pool_size,
        "cutoffs": {
            **cuts,
            "anchors": {
                "sort_groups_single": SORT_GROUPS_SINGLE,
                "sort_groups_double": SORT_GROUPS_DOUBLE,
                "citation": ANCHOR_CITATION,
            },
        },
        "items": items,
        "summary": summary,
        "boundaries": list(HONEST_BOUNDARIES),
    }

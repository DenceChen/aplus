"""持仓级降级预警的**接线层**守卫。

## 这个文件守的是什么

**接线**，不是判据本身（判据由 `tests/test_holdings_alert.py` 守）。

| 轴 | 钉住什么 |
|---|---|
| 口径纪律 | 建仓分从**建仓日那份预测文件**读；缺建仓分 ⇒ `UNDECIDED`；建仓日与当前不是同一组权重 ⇒ `UNDECIDED`；持仓 = **建仓日**那份 alloc 的 positions |
| 成本 | 日频路径**不许**再建一次面板、**不许**用慢路径因子实现 |
| 失败 | 对拍失败 ⇒ **不落盘**；步骤失败 ⇒ 记成 `failed` 并**继续** |
| 前端 | 页面必须能看见每一条的**判据**；显示串不许与它自己的数矛盾 |

## 硬约束

- 🚨 **不许写生产路径**（`data/reports/…`）：夹具全在 `tmp_path`。
- 🚨 **不许读生产产物**当夹具：判据的可测性必须与当天数据无关。
- 断言不许靠**子串命中注释** ⇒ 接线层用 **AST**，产物层用**行为**。
"""
from __future__ import annotations

import ast
import importlib.util
import json
import re
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import aplus.holdings_alert as HA          # noqa: E402
import aplus.holdings_entry as HE           # noqa: E402
from aplus.predict import build_predictions  # noqa: E402
from scripts import run_daily as rd          # noqa: E402

MEASURE_SCRIPT = ROOT / "scripts" / "measure_holdings_alert.py"

#: 预注册 `docs/PREREG_HOLDINGS_ALERT_2026-10-21.md` §2 的四态阶梯（逐字）。
#: ⚠️ 这份**判据**是事前写死的 ⇒ **一个字不许改**（第 91 层）。
PREREG_RULE_ORDER = (
    "1. 不在当日全池里 ⇒ UNDECIDED（测不出位次）",
    "2. 缺建仓日合成分 ⇒ UNDECIDED（相对建仓的升降测不出，≠ 没变差）",
    "3. 相对建仓变差（合成分 < 建仓日合成分）且当前分位 ≤ 最差十分位 ⇒ REDUCE（减持/卖出）",
    "4. 在最差五分位 或 相对建仓变差 ⇒ WATCH（观察）",
    "5. 其余 ⇒ HOLD（维持持有）",
)


# --------------------------------------------------------------------------
# 夹具：现造一份「建仓日 + 持仓 + 建仓分」
# --------------------------------------------------------------------------

def _make_reports(tmp_path, *, entry_day="2026-01-02",
                  up_scores=None, extra_up=None,
                  positions=None, weights_hash="WH1",
                  state_up=None, held_since=None,
                  alloc_score_delta=0.0):
    """造一份 reports 目录（**不碰生产路径**）。

    `up_scores`  = 建仓日涨腿的 {code: score}
    `extra_up`   = 涨腿里有、但**没拿到钱**的持仓候选（不在 positions 里）
    `positions`  = 建仓日那份 alloc 里真正拿到钱的那些
    `alloc_score_delta` = alloc positions 里的 score 与涨腿落盘分的偏差
    """
    reports = tmp_path / "reports"
    (reports / "predictions").mkdir(parents=True)
    (reports / "allocation").mkdir(parents=True)
    up_scores = dict(up_scores or {"600001.SH": 2.0, "600002.SH": 1.5,
                                   "600003.SH": 1.0})
    codes = sorted(up_scores)
    rows = []
    for c in codes:
        rows.append({"code": c, "name": c, "direction": "up",
                     "score": float(up_scores[c])})
    for c in (extra_up or []):
        rows.append({"code": c, "name": c, "direction": "up", "score": 0.5})
    for c in ("900001.SH", "900002.SH"):
        rows.append({"code": c, "name": c, "direction": "down", "score": -1.0})
    df = pd.DataFrame(rows)
    df["weights_hash"] = weights_hash
    df.to_parquet(reports / "predictions" / ("pred_%s.parquet" % entry_day),
                  index=False)

    if positions is None:
        positions = [{"code": c, "name": c, "weight": 0.1, "amount": 5000.0,
                      "score": float(up_scores.get(c, 0.0)) + alloc_score_delta}
                     for c in codes[:2]]
    alloc = {"capital": 50000, "cash_weight": 0.5, "date": entry_day,
             "positions": positions, "rebalance": []}
    (reports / "allocation" / ("alloc_%s.json" % entry_day)).write_text(
        json.dumps(alloc, ensure_ascii=False), encoding="utf-8")

    if state_up is None:
        state_up = codes + list(extra_up or [])
    state = {"as_of": entry_day, "days_since_rebalance": 0, "every": 21,
             "buffer_multiple": None, "up": list(state_up),
             "down": ["900001.SH"], "up_names": [], "down_names": [],
             "held_since": held_since}
    (reports / "holdings.json").write_text(
        json.dumps(state, ensure_ascii=False), encoding="utf-8")
    return reports


def _pool(scores: dict) -> pd.DataFrame:
    return pd.DataFrame({
        "code": list(scores.keys()),
        "score": list(scores.values()),
        "name": [c for c in scores.keys()],
    })


def _build(reports, *, ranked=None, weights=None, weights_hash="WH1"):
    weights = weights or {"mom20": 1.0}
    ranked = ranked if ranked is not None else _pool(
        {"600001.SH": 2.0, "600002.SH": 1.5, "600003.SH": 1.0,
         "900001.SH": -1.0, "900002.SH": -1.5})
    return HE.build_holdings_alert_artifact(
        reports_dir=reports, as_of="2026-01-02", ranked=ranked,
        weights=weights, weights_hash=weights_hash,
        generated_by="scripts/run_daily.py",
        pool_method="测试夹具", pool_method_note="测试夹具")


# --------------------------------------------------------------------------
# 1. 产物契约
# --------------------------------------------------------------------------

def test_the_artifact_carries_every_top_level_key_a_reader_needs(tmp_path):
    """产物顶层键必须齐全 —— 少一个键，前端就只能显示一半的事实。"""
    reports = _make_reports(tmp_path)
    art = _build(reports)
    for key in ("as_of", "generated_by", "question", "population", "entry",
                "pool", "criteria", "items", "summary", "boundaries",
                "what_this_cannot_prove", "defect_measurement"):
        assert key in art, f"产物缺顶层键 {key}：{sorted(art)}"


def test_every_item_carries_its_own_criterion(tmp_path):
    """🚨 每条建议必须自带判据（预注册 §6：`items[i].action_criterion`）。

    ⚠️ 这不是「有个字段」就够：**判据里必须出现这只票的代码**
    ⇒ 否则用户看到的是一句通用话，看不出凭什么这么判。
    """
    reports = _make_reports(tmp_path)
    art = _build(reports)
    assert art["items"], "产物里一条建议都没有"
    for it in art["items"]:
        assert it.get("action") in ("REDUCE", "WATCH", "HOLD", "UNDECIDED")
        crit = it.get("action_criterion")
        assert isinstance(crit, str) and crit.strip(), f"{it.get('code')} 没有判据"
        assert str(it["code"]) in crit, (
            f"{it['code']} 的判据里没有它自己的代码：{crit!r}")


# --------------------------------------------------------------------------
# 2. 口径纪律（接线不许走样）
# --------------------------------------------------------------------------

def test_a_holding_missing_from_the_entry_day_list_is_undecided_not_zero_filled(tmp_path):
    """🚨 持仓股票若**不在建仓日那份名单里** ⇒ **没有建仓分**
    ⇒ 走第 4 态 `UNDECIDED`（「测不出 ≠ 没变差」）。

    ⚠️ **不许用 0 或当前分顶替** —— 那会把「测不出」说成「没有」。
    """
    # 600888.SH 在**建仓日那份名单里根本没有**（既不在涨腿也不在跌腿）
    # ⇒ 它**没有建仓分**。
    reports = _make_reports(tmp_path, positions=[
        {"code": "600001.SH", "name": "A", "score": 2.0},
        {"code": "600888.SH", "name": "B", "score": 0.5},
    ])
    art = _build(reports)
    assert "600888.SH" not in art["entry"]["entry_scores"], (
        "建仓分只许来自建仓日那份预测文件的涨腿")
    item = [i for i in art["items"] if i["code"] == "600888.SH"][0]
    assert item["action"] == "UNDECIDED", item
    assert item["relative_to_entry_score"] is None, item
    assert item["entry_score"] is None, item


def test_an_unresolvable_entry_day_is_reported_as_unknown_not_as_empty(tmp_path):
    """建仓日判不出 ⇒ **说清「取不到」**，不许渲染成「你没有持仓」。

    ⚠️ 构造：`held_since` 指向一个**没有**预测文件的日子，
    且 `state.up` 与任何一天的涨腿都**不相同** ⇒ 集合匹配也判不出。
    """
    reports = _make_reports(tmp_path, held_since="2025-12-31",
                            state_up=["111111.SH"])
    art = _build(reports)
    assert art["entry"]["entry_day"] is None, art["entry"]
    assert art["entry"]["entry_day_verdict"] in (
        "NO_MATCHING_DAY", "AMBIGUOUS_MATCH", "NO_BASKET_IN_STATE"), art["entry"]
    assert art["population"]["note"], (
        "建仓日判不出 ⇒ 必须**明说**取不到持仓名单（不是「空仓」）")
    assert art["population"]["n_heldings"] == 0


def test_a_different_weights_hash_at_the_entry_day_makes_it_undecided(tmp_path):
    """建仓日与当前**不是同一组权重** ⇒ 两个合成分不是同一个量
    ⇒ 相对建仓的变化**不可比** ⇒ `UNDECIDED`（预注册 §4.4）。"""
    reports = _make_reports(tmp_path, weights_hash="WH_OLD")
    art = _build(reports, weights_hash="WH_NEW")
    assert art["entry"]["comparable_with_current_weights"] is False, art["entry"]
    assert art["entry"]["comparability_note"], "必须说清为什么不可比"
    for it in art["items"]:
        assert it["action"] == "UNDECIDED", it


def test_entry_scores_that_disagree_with_the_allocation_artifact_are_undecided(tmp_path):
    """建仓分与配置产物里那份分数**对不上** ⇒ 两个来源互相矛盾
    ⇒ 相对建仓的变化不可比 ⇒ `UNDECIDED`。

    ⚠️ 偏差 1e-6 **小于**被测的容差 1e-9？—— 不，1e-6 **大于** 1e-9 ⇒ 必须判红。
    """
    reports = _make_reports(tmp_path, alloc_score_delta=1e-6)
    art = _build(reports)
    cc = art["entry"]["entry_scores_crosscheck"]
    assert cc["verdict"] == "MISMATCH_DO_NOT_TRUST_THIS_ARTIFACT", cc
    assert art["entry"]["comparable_with_current_weights"] is False, art["entry"]
    for it in art["items"]:
        assert it["action"] == "UNDECIDED", it


def test_the_rule_order_is_verbatim_the_preregistered_one(tmp_path):
    """🚨 判据阶梯**一个字不许改**（第 91 层：预注册是事前写死的）。"""
    assert HE.RULE_ORDER == PREREG_RULE_ORDER, (
        "判据阶梯与预注册不一致：\n  预注册：%s\n  现在：%s"
        % (PREREG_RULE_ORDER, HE.RULE_ORDER))
    reports = _make_reports(tmp_path)
    art = _build(reports)
    assert tuple(art["criteria"]["rule_order"]) == PREREG_RULE_ORDER, art["criteria"]


def test_the_thresholds_are_derived_from_the_anchor_not_typed(tmp_path):
    """阈值必须**从外部锚推导**（`bottom_cuts()`），不许手打。"""
    reports = _make_reports(tmp_path)
    art = _build(reports)
    cuts = HE.bottom_cuts()
    assert art["criteria"]["bottom_decile_percentile"] == cuts[
        "bottom_decile_percentile"]
    assert art["criteria"]["bottom_quintile_percentile"] == cuts[
        "bottom_quintile_percentile"]
    # 锚在书上（石川 §排序分组）：十分位 L=10 / 五分位 L1=L2=5。
    assert cuts["bottom_decile_percentile"] == 1.0 / HA.SORT_GROUPS_SINGLE
    assert cuts["bottom_quintile_percentile"] == 1.0 / HA.SORT_GROUPS_DOUBLE


def test_the_holdings_come_from_the_entry_day_allocation_not_the_latest_one(tmp_path):
    """持仓 = **建仓日**那份 alloc 的 positions，
    **不是**最新那份（非调仓日那份是当天的建议、没被执行）。"""
    reports = _make_reports(tmp_path, entry_day="2026-01-02")
    # 造一份**别的日子**的 alloc：里面有一只票不在建仓日那份里。
    other = {"capital": 50000, "cash_weight": 0.5, "date": "2026-01-05",
             "positions": [{"code": "777777.SH", "name": "Z", "weight": 0.1,
                            "amount": 5000.0, "score": 3.0}],
             "rebalance": []}
    (reports / "allocation" / "alloc_2026-01-05.json").write_text(
        json.dumps(other, ensure_ascii=False), encoding="utf-8")
    art = _build(reports)
    assert "777777.SH" not in art["population"]["codes"], art["population"]
    assert art["population"]["basis"].endswith(
        "alloc_2026-01-02.json::positions"), art["population"]


# --------------------------------------------------------------------------
# 3. 全池口径：接线层那份排序必须与生产逐位相同
# --------------------------------------------------------------------------

def _synthetic_universe(n=40):
    """造一份带因子列的横截面（`composite_score` 要这些列）。

    ⚠️ **行序故意与合成分序相反**（`mom20` 递增 ⇒ 权重取负 ⇒ 合成分递减，
    而行序是**递增**的）⇒ 「排序那一步」在夹具上**不是空操作**。
    ⇒ 否则「去掉排序」这个变异体会**存活**（夹具本来就已排好序 ⇒
    变异是空操作 ⇒ 绿灯是假的，第 122 层那条）。
    """
    rng = pd.Series(range(n), dtype=float)
    return pd.DataFrame({
        "code": ["S%03d" % i for i in range(n)],
        "name": ["S%03d" % i for i in range(n)],
        "mom20": rng / 100.0,
        "rev5": -rng / 200.0,
        "vol20": rng / 500.0,
        "vsur": rng / 300.0,
    }).iloc[::-1].reset_index(drop=True)


def test_the_wiring_pool_matches_the_production_legs_bit_for_bit():
    """🚨 `scored_universe` 是 `build_predictions` 内部的**同一条语句路径**的副本。

    ⇒ 涨腿 10 只的**代码集合**与**分数**必须与 `build_predictions` 逐位相同。
    ⚠️ 抄一份而没有对拍 = 两份副本之间没有守卫（第 137 层）。
    """
    uni = _synthetic_universe()
    weights = {"mom20": -1.0, "rev5": 0.3}
    ranked = HE.scored_universe(uni, weights)
    up, down = build_predictions(uni, top_n=10, bottom_n=10, weights=weights)
    # ⚠️ 比**集合**（生产把跌腿**倒序**排：最看空在前 ⇒ 顺序不是同一个量，
    #   但**是哪几只**必须逐只相同）。
    assert list(ranked["code"].head(10)) == list(up["code"]), (
        "涨腿代码集合与生产不一致")
    assert sorted(ranked["code"].tail(10)) == sorted(down["code"]), (
        "跌腿代码集合与生产不一致")
    prod_scores = {str(r["code"]): float(r["score"]) for _, r in up.iterrows()}
    mine = {str(r["code"]): float(r["score"]) for _, r in ranked.head(10).iterrows()}
    assert prod_scores == mine, "涨腿分数与生产不一致"


def test_the_wiring_pool_is_sorted_by_score_descending():
    """全池必须**按合成分降序**（位次/分位全靠这个顺序）。"""
    uni = _synthetic_universe()
    ranked = HE.scored_universe(uni, {"mom20": -1.0})
    scores = list(ranked["score"])
    assert scores == sorted(scores, reverse=True), "全池没有按合成分降序"


# --------------------------------------------------------------------------
# 4. 接线（AST）：成本与顺序
# --------------------------------------------------------------------------

def _run_daily_ast():
    return ast.parse((ROOT / "scripts" / "run_daily.py").read_text(encoding="utf-8"))


def _function_node(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError("run_daily.py 里没有函数 " + name)


def test_the_daily_path_never_builds_a_second_panel():
    """🚨 日频路径**不许**再建一次面板（GB 级 / 10 分钟量级）。

    全池必须**复用** `predict` 步骤已经在内存里的那一份。
    ⚠️ 判据是**这个函数体里有没有那个调用**，不是全文有没有那个词
    （全文会被别的函数的调用满足）。
    """
    tree = _run_daily_ast()
    fn = _function_node(tree, "step_holdings_alert")
    called = {n.func.id for n in ast.walk(fn)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    called |= {n.func.attr for n in ast.walk(fn)
               if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert "build_cross_section" not in called, (
        "step_holdings_alert 里不许再调 build_cross_section（GB 级面板）")
    assert "build_panel" not in called, (
        "step_holdings_alert 里不许再调 build_panel（GB 级面板）")


def test_the_daily_path_never_uses_the_slow_factor_implementation():
    """🚨 慢路径 `aplus/factor.py::compute_factors` **不是**生产实现。

    测量台实测：vol20 差 0.0468 ⇒ 合成分差 0.57 ⇒ 涨腿 10 只里 3 只名次不同。
    ⇒ 接线层不许碰它（判据：**这个函数体里**有没有那个调用）。
    """
    tree = _run_daily_ast()
    fn = _function_node(tree, "step_holdings_alert")
    called = {n.func.id for n in ast.walk(fn)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    called |= {n.func.attr for n in ast.walk(fn)
               if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert "compute_factors" not in called, (
        "step_holdings_alert 里不许用慢路径 compute_factors")


def test_a_missing_universe_must_not_be_recorded_as_ok():
    """🚨 `predict` 没产出全池时，`holdings_alert` 这一步必须记成**非 ok**。

    ⇒ 事故形状（CLAUDE.md 第 13 层那条）：裸 `return` 被记成 `"ok"`
    ⇒ `reduce_run_status` 判整轮 `"ok"` ⇒ 日更守卫以为今天交够了，
    而**产物一个字节都没落盘**。
    """
    tree = _run_daily_ast()
    fn = _function_node(tree, "_pipeline")
    found = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Assign):
            continue
        if not (isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            continue
        for tgt in node.targets:
            if isinstance(tgt, ast.Subscript) and re.match(
                    r"^steps\[['\"](holdings_alert)['\"]\]$", ast.unparse(tgt)):
                found.append(node.value.value)
    assert found, "找不到 holdings_alert 这一步的赋值"
    assert all(v != "ok" for v in found), (
        "holdings_alert 缺输入时必须记成「非 ok」，实际：%s" % found)


def test_the_entry_scores_are_the_entry_day_file_scores_bit_for_bit(tmp_path):
    """🚨 建仓分必须**逐字**来自建仓日那份预测文件的涨腿落盘分。

    ⚠️ **不许用 0 或当前分顶替** —— 那会把「测不出」说成「没有」
    （预注册 §2 的第 2 条：`UNDECIDED` 那一格存在的全部理由）。
    """
    reports = _make_reports(tmp_path)
    art = _build(reports)
    pred = pd.read_parquet(reports / "predictions" / "pred_2026-01-02.parquet")
    leg = {str(r["code"]): float(r["score"]) for _, r in pred.iterrows()
           if r["direction"] == "up"}
    assert leg, "夹具的建仓日涨腿是空的"
    assert art["entry"]["entry_scores"] == leg, (
        "建仓分与建仓日那份预测文件的涨腿落盘分不一致：%s vs %s"
        % (art["entry"]["entry_scores"], leg))


def test_the_holdings_alert_step_runs_after_the_predict_step():
    """🚨 排序：必须在 **predict/allocate 之后**（它要用当天的全池与配置产物）。

    ⚠️ 判据落在 `_pipeline` 的**语句顺序**上，不是「文件里出现过」——
    后者会被注释与别的函数满足（第 13 层）。
    """
    tree = _run_daily_ast()
    fn = _function_node(tree, "_pipeline")
    order = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "run_step":
            if node.args and isinstance(node.args[0], ast.Constant):
                order.append(node.args[0].value)
    assert "predict" in order and "holdings_alert" in order, order
    assert order.index("holdings_alert") > order.index("predict"), (
        "holdings_alert 必须排在 predict 之后：%s" % order)


def test_the_production_parity_mismatch_refuses_to_write_anything(tmp_path):
    """🚨 对拍失败 ⇒ **抛异常**且**不落盘**（半截 JSON 会被当成有效读数）。

    构造：把落盘那份预测文件的涨腿**换掉一只代码** ⇒ 对不上。
    """
    reports = _make_reports(tmp_path)
    path = reports / "predictions" / "pred_2026-01-02.parquet"
    prod = pd.read_parquet(path)
    idx = prod[prod["direction"] == "up"].index[0]
    prod.loc[idx, "code"] = "ZZZ999.SH"
    prod.to_parquet(path, index=False)

    uni = _synthetic_universe()
    with pytest.raises(Exception):
        rd.step_holdings_alert(str(reports), "2026-01-02", {
            "universe": uni,
            "cross_section_size": len(uni),
            "weights": {"mom20": -1.0, "rev5": 0.3, "vol20": 0.5, "vsur": 0.5},
            "filtered": {},
            "weights_meta": {},
        })
    assert not (reports / "holdings_alert_2026-01-02.json").exists(), (
        "对拍失败却落了产物 ⇒ 半截 JSON 会被当成有效读数")


# --------------------------------------------------------------------------
# 5. 与测量台的**跨实现一致**（两份副本之间要有锁）
# --------------------------------------------------------------------------

def _load_measure_script():
    spec = importlib.util.spec_from_file_location("measure_holdings_alert",
                                                  MEASURE_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _dict_keys_for(tree, parent_key: str) -> set:
    """在 AST 里找「键为 `parent_key` 的那个字典」的字面量键集。"""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for k, v in zip(node.keys, node.values):
            if isinstance(k, ast.Constant) and k.value == parent_key \
                    and isinstance(v, ast.Dict):
                out = set()
                for kk in v.keys:
                    if isinstance(kk, ast.Constant):
                        out.add(kk.value)
                return out
    return set()


def test_the_population_block_must_have_the_same_keys_as_the_measurement_script():
    """🚨 **两个产出者写同一个文件名 ⇒ 键集必须逐字相同**。

    ## 实测踩到（2026-10-21，浏览器里抓到的）

    日频接线写 `population.n_holdings`、测量台写 `population.n_heldings`
    ⇒ 页面读哪个**取决于最后是谁写的那个文件**
    ⇒ 同一屏上的读数**随写入顺序变**（本次表现：页面显示「全池 0 只持仓」
    而实际有 5 只）。
    ⇒ 症状与「持仓清空了」**逐位同形**（第 89 层的形状）。

    ⚠️ 判据落在 **AST 的字面量键集**上（不是子串 —— 子串会被注释满足）。
    """
    mine = _dict_keys_for(
        ast.parse((ROOT / "aplus" / "holdings_entry.py").read_text(encoding="utf-8")),
        "population")
    theirs = _dict_keys_for(
        ast.parse(MEASURE_SCRIPT.read_text(encoding="utf-8")), "population")
    assert mine, "接线层找不到 population 字面量"
    assert theirs, "测量台里找不到 population 字面量"
    # ⚠️ 方向是「**测量台的键集 ⊆ 接线层的键集**」：
    #   **多**一个键是**兼容**的（页面不认识就忽略），
    #   **少**一个键或**改名**才是**破坏性**的（页面读到 undefined）。
    missing = theirs - mine
    assert not missing, (
        "接线层的 population **缺**测量台提供的键 ⇒ 页面读那个键会拿到 undefined"
        "（两个产出者写同一个文件名 ⇒ 症状随写入顺序变）：缺 %s\n"
        "  接线层：%s\n  测量台：%s"
        % (sorted(missing), sorted(mine), sorted(theirs)))


def test_the_entry_day_resolution_agrees_with_the_measurement_script(tmp_path):
    """🚨 两份实现（接线层 / 测量台）必须给出**同一个**建仓日。

    ⚠️ 这是「两份副本」的对拍锁（第 137 层）：不许靠「人记得同步」。
    """
    mod = _load_measure_script()
    reports = _make_reports(tmp_path, held_since="2026-01-02")
    state = json.loads((reports / "holdings.json").read_text(encoding="utf-8"))
    mine = HE.resolve_entry_day(reports, state)
    theirs = mod._resolve_entry_day(reports, state)
    assert mine.get("entry_day") == theirs.get("entry_day"), (mine, theirs)
    assert mine.get("verdict") == theirs.get("verdict"), (mine, theirs)


def test_the_holdings_resolution_agrees_with_the_measurement_script(tmp_path):
    """两份实现解析出来的**持仓名单**必须逐只相同。"""
    mod = _load_measure_script()
    reports = _make_reports(tmp_path, held_since="2026-01-02")
    # ⚠️ 测量台把 `alloc` 路径写成**仓相对**（`Path.relative_to(ROOT)`）
    #   ⇒ 夹具在仓外时它会抛 ⇒ 这里把它的 ROOT 指到夹具目录
    #   （只改路径基准，不改它的逻辑）。
    mod.ROOT = tmp_path
    mine = HE.resolve_holdings(reports, "2026-01-02")
    theirs = mod._resolve_holdings(reports, "2026-01-02", [])
    assert mine["codes"] == theirs["codes"], (mine, theirs)
    assert mine["names"] == theirs["names"], (mine, theirs)


def test_the_measurement_script_declares_the_same_rule_order():
    """测量台产物里那份 `rule_order` 字面量必须与接线层**逐字相同**。

    ⚠️ 判据是 **AST 里的字面量**，不是子串（子串会被注释满足）。
    """
    tree = ast.parse(MEASURE_SCRIPT.read_text(encoding="utf-8"))
    found = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if isinstance(k, ast.Constant) and k.value == "rule_order":
                    found = v
    assert found is not None, "测量台里找不到 rule_order"
    theirs = tuple(ast.literal_eval(found))
    assert theirs == HE.RULE_ORDER, (
        "测量台与接线层的判据阶梯不一致：\n  测量台：%s\n  接线层：%s"
        % (theirs, HE.RULE_ORDER))


# --------------------------------------------------------------------------
# 6. 前端：用户看得见 + 显示串不许与数矛盾
# --------------------------------------------------------------------------

INDEX = ROOT / "aplus" / "web" / "index.html"


def test_the_position_label_is_never_contradicted_by_its_own_number():
    """🚨 显示串不许与它自己的数矛盾（协调方 2026-10-21 追加）。

    实盘敞口已是 **1.0（满仓）** ⇒ 屏上不许再输出「半仓 100%」。

    ⚠️ 判据是**行为**：拿 `exposure = 1.0` 造夹具，
    标签函数必须给出「满仓」，且**不许**出现「半仓」。
    """
    from aplus.webapp import position_label
    lab = position_label(1.0)
    assert "半仓" not in lab, lab
    assert "满仓" in lab, lab
    assert position_label(0.5) != lab, "0.5 与 1.0 不许给同一个标签"


def test_the_position_label_comes_from_a_table_not_a_literal():
    """⚠️ 显示串不许是**手打**的：形容词必须从常量表查出来。

    ⚠️ 判据在 **AST** 上：`position_label` 里不许出现「半仓」/「满仓」
    这两个字面量（那说明它是手打的，改数时不会跟着变）。
    """
    src = (ROOT / "aplus" / "webapp.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = _function_node(tree, "position_label")
    literals = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            literals.append(node.value)
    for lit in literals:
        assert "半仓" not in lit and "满仓" not in lit, (
            "position_label 里不许出现手打的仓位形容词：%r" % lit)


def test_the_exposure_display_never_contradicts_its_own_number():
    """🚨 屏上那一串（标签 + 百分比）不许与它自己的数矛盾。

    ⚠️ **判别力**：夹具是 `exposure = 1.0`（真值就是满仓）⇒
    输出里**不许**出现「半仓」；而 `0.5` 那档**必须**给出不同的串
    （否则这条守卫可能退化成「永远返回同一个常量」）。
    """
    from aplus.webapp import exposure_display
    full = exposure_display(1.0)
    assert "半仓" not in full, full
    assert "满仓" in full and "100%" in full, full
    assert exposure_display(0.5) != full, "0.5 与 1.0 不许给同一串"
    assert exposure_display(None) == "—", "读不出就写「—」，不许编"


# --------------------------------------------------------------------------
# 7. 「W < 10 不许报命中率」：图形层守住、**无障碍通道**漏过（2026-10-21）
# --------------------------------------------------------------------------

def _js_function_body(src: str, name: str) -> str:
    """从 `function <name>(` 起手逐字符配对花括号，跳过字符串与注释。

    ⚠️ **不复用别处的实现**：那会把「抽错了一段」变成一条**恒真的守卫**
    （抽错 ⇒ node 跑挂 ⇒ 断言红 ⇒ 但红的原因不是判据坏了）。
    ⇒ 下面那条守卫带**正控**（抽出来的片段必须含已知标记）。
    """
    start = src.find("function %s(" % name)
    assert start != -1, "index.html 里找不到 function %s(" % name
    i = src.index("{", start)
    depth, n = 0, len(src)
    while i < n:
        ch = src[i]
        if ch in "\"'`":
            q = ch
            i += 1
            while i < n and src[i] != q:
                i += 2 if src[i] == "\\" else 1
        elif ch == "/" and i + 1 < n and src[i + 1] == "/":
            while i < n and src[i] != "\n":
                i += 1
        elif ch == "/" and i + 1 < n and src[i + 1] == "*":
            i = src.index("*/", i) + 2
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
        i += 1
    raise AssertionError("function %s 的花括号没配平" % name)


def _js_const_line(src: str, name: str) -> str:
    """取 `const <name> = ...;` 那一行（**逐字**，不许自己重写一份）。"""
    for line in src.splitlines():
        if line.strip().startswith("const %s =" % name):
            return line.strip()
    raise AssertionError("index.html 里找不到 const %s = ..." % name)


def _run_node(js: str) -> str:
    import shutil
    import subprocess
    node = shutil.which("node")
    assert node, "本机没有 node ⇒ 前端守卫**不可验证**（不许静默跳过）"
    proc = subprocess.run([node, "-e", js], capture_output=True, text=True)
    assert proc.returncode == 0, "node 跑挂了：%s" % proc.stderr[:800]
    return proc.stdout


#: 一份**区分性**的 stats：命中率 0.6207 ⇒ 渲染成 "62.1%"（页面上别处不会有）。
_TIP_STATS = {
    "n_independent_windows": 3,
    "min_windows": 10,
    "hit_rate_up": 0.6207,
    "hit_rate_down": 0.6333,
    "overall_hit_rate": 0.6271,
    "gross_up_excess": 0.00138,
    "cost_share": 2.1734,
    "avg_cost_drag": 0.003,
}


def _figure_tips(stats: dict) -> dict:
    """在 node 里跑**页面上的那段真代码**（不是重写一份）。"""
    src = INDEX.read_text(encoding="utf-8")
    body = _js_function_body(src, "figureTips")
    assert "function figureTips(" in body, "抽取失败（正控）"
    assert "const tipHit" in body, "抽取到了错的片段（正控）"
    js = "\n".join([
        _js_const_line(src, "fmtPct"),
        _js_const_line(src, "fmtNum"),
        _js_const_line(src, "MIN_WINDOWS"),
        body,
        "console.log(JSON.stringify(figureTips(%s)));" % json.dumps(stats),
    ])
    return json.loads(_run_node(js))


def test_the_tooltip_must_not_carry_the_hit_rate_when_windows_are_too_few():
    """🚨 `W < 10` 时，**渲染出来的文本里不许出现命中率**。

    起因（2026-10-21，协调方取证）：`voided()` 把 tip 原样放进 `title`
    **和** `aria-describedby` 指向的 sr-only 文本 ⇒
    **读屏软件会念出「涨腿 0.62 · 跌腿 0.63」**
    ⇒ 「不许报命中率」被自己的 tooltip 破了。

    ⚠️ 判据是**行为**：在 node 里跑页面上的那段真代码，
    喂 `n_independent_windows = 3` ⇒ 输出里不许有那个命中率字符串。
    """
    tips = _figure_tips(dict(_TIP_STATS))
    assert tips["has"] is False, tips
    blob = json.dumps(tips, ensure_ascii=False)
    assert "62.1%" not in blob, "W 不足时 tooltip 里出现了命中率：%s" % blob
    assert "63.3%" not in blob, blob
    assert "0.30%" not in blob, "W 不足时 tooltip 里出现了成本比率：%s" % blob


def test_the_tooltip_must_carry_the_hit_rate_when_windows_are_enough():
    """🚨 **反向正控**：够窗口时那些数**必须给**。

    ⚠️ 没有这条，上一条可能退化成「永远不含任何数」——
    那是**过度收紧**（够窗口时数是该给的，而且带口径正是这个仓的规矩）。
    """
    stats = dict(_TIP_STATS)
    stats["n_independent_windows"] = 12
    tips = _figure_tips(stats)
    assert tips["has"] is True, tips
    blob = json.dumps(tips, ensure_ascii=False)
    assert "62.1%" in blob, "够窗口时 tooltip 必须带命中率：%s" % blob
    assert "0.30%" in blob, "够窗口时 tooltip 必须带成本口径：%s" % blob


def _synthetic_history(n_windows: int) -> pd.DataFrame:
    """造一份**有 n 个独立成交窗口**的验证底稿（现造，不读生产产物）。"""
    rows = []
    for w in range(n_windows):
        for k in range(4):
            up = (k % 2 == 0)
            rows.append({
                "pred_date": "2026-01-%02d" % (w + 1),
                "code": "%d%d.SZ" % (w, k),
                "direction": "up" if up else "down",
                "result": "hit" if up else "miss",
                "actual_pct": 0.01 if up else -0.01,
                "cost": 0.001,
                "market_pct": 0.0,
                "entry_date": "2026-01-%02d" % (w + 1),
                "exit_date": "2026-01-%02d" % (w + 2),
            })
    return pd.DataFrame(rows)


def test_the_api_must_not_hand_out_hit_rates_when_windows_are_too_few():
    """🚨 `W < 10` 时 `/api/status` 的 `stats` **不许**带命中率族。

    依据：预注册 `docs/DELIVERY_DECISIONS_2026-10-18.md` §4 决策四
    （`docs/DELIVERY_GATE.md` §D1 同款：「W < 10 时不许报命中率、不许给 CI」）。

    ⚠️ **缺就是缺，不许回落成 0**（回落成 0 会被读成「查过了，命中率 0%」）。
    """
    from aplus.webapp import UNCITABLE_RATIO_FIELDS, _stats_with_compounded
    st = _stats_with_compounded(_synthetic_history(3))
    assert st["n_independent_windows"] == 3, st
    assert st["citable"] is False, st
    assert st["citable_reason"], "必须说清为什么不许报"
    for key in UNCITABLE_RATIO_FIELDS:
        assert key not in st, f"W 不足时 `{key}` 不许在载荷里：{st}"
    # 计数类**照给**（读者判断样本量的依据）
    assert st["judged"] == 12, st
    assert st["min_windows"] == 10, st


def _annualised_fixture(n_days: int):
    """造一份底稿，使它的 `cagr` **恰好**是 `1.005799`（年化 +100.58%）。

    ⚠️ **判别力**：这个数在生产实测里出现过（3 天样本外推出的 +100.58%）
    ⇒ 一条「载荷里不该出现它」的断言**不会**被别的数字满足。

    ⇒ 日收益由 `cagr` 反解出来：`cagr = (1+total)^(1/years) − 1`
    ⇒ `years = n_days/252`、`total = 1.005799^years − 1`
    ⇒ `r = (1+total)^(1/n_days) − 1`（每个交易日的涨腿净收益）。

    ⇒ 独立成交窗口数 = **天数**（每个观测日一个窗口）⇒
    `n_days = 12` ⇒ W = 12（**过** W ≥ 10 那道闸）但 years = 12/252 < 1
    ⇒ **只剩 years 那道闸拦住它** ⇒ 这条守卫专治「只实现 W 闸」的半截实现。
    """
    target_cagr = 1.005799
    years = n_days / 252.0
    total = (1.0 + target_cagr) ** years - 1.0
    r = (1.0 + total) ** (1.0 / n_days) - 1.0
    rows = []
    for day in pd.bdate_range("2024-01-01", periods=n_days):
        for k in range(5):
            up = (k % 2 == 0)
            rows.append({
                "pred_date": day.strftime("%Y-%m-%d"),
                "exit_date": day.strftime("%Y-%m-%d"),
                "entry_date": day.strftime("%Y-%m-%d"),
                "code": "%03d.SZ" % k,
                "direction": "up" if up else "down",
                "result": "hit" if up else "miss",
                "actual_pct": (r + 0.001) if up else -0.01,
                "cost": 0.001,
                "market_pct": 0.0,
            })
    return pd.DataFrame(rows)


def test_the_api_must_hand_out_the_hit_rates_when_it_is_citable():
    """🚨 **反向正控**：可引用时命中率**必须给**（不许过度收紧）。

    ⚠️ 夹具必须**两道闸都过**（W ≥ 10 **且** years ≥ 1）⇒
    252 个交易日 = 252 个独立成交窗口。
    """
    from aplus.webapp import _stats_with_compounded
    st = _stats_with_compounded(_annualised_fixture(n_days=252))
    assert st["n_independent_windows"] == 252, st
    assert st["citable"] is True, st.get("citable_reason")
    assert "hit_rate_up" in st, st
    assert "overall_hit_rate" in st, st


def test_the_api_must_not_hand_out_the_annualised_family_when_the_span_is_too_short():
    """🚨 跨度不足时 `/api/status` **不许**吐出年化族（cagr / arith_ann …）。

    依据：决策四那句「**不许给 CI**」里的 CI 就是**外推的不确定度**；
    而 `measure_portfolio_nav.py` 的结构闸门早写着「样本过短，不给方向」。

    ⚠️ **这道守卫专治「只实现 W 闸」的半截实现**：夹具的 W = 12（**过** W ≥ 10）
    ⇒ 只有 **years < 1** 那道闸拦住它 ⇒ 只砍比率族、不砍年化族的实现**会红**。

    ⚠️ **判别力**：夹具的 `cagr` **恰好**是 `1.005799`（年化 +100.58%）
    ⇒ 断言它**不出现**，而**反向正控**证明它**该出现时确实出现**。
    """
    from aplus.webapp import (UNCITABLE_ANNUALIZED_FIELDS,
                               _stats_with_compounded)
    st = _stats_with_compounded(_annualised_fixture(n_days=12))
    assert st["n_independent_windows"] == 12, st
    assert st["years"] < 1.0, st
    assert st["citable"] is False, st
    for key in UNCITABLE_ANNUALIZED_FIELDS:
        assert key not in st, f"跨度不足时 `{key}` 不许在载荷里：{st}"
    blob = json.dumps(st, ensure_ascii=False, default=str)
    assert "1.005799" not in blob, f"年化数漏出去了：{blob}"
    # 计数类**照给**（读者判断样本量的依据）
    assert st["n_days"] == 12, st
    assert st["years"] is not None, st


def test_the_api_must_hand_out_the_annualised_family_once_it_is_citable():
    """🚨 **反向正控**：够长时年化族**必须**给（不许过度收紧）。"""
    from aplus.webapp import _stats_with_compounded
    st = _stats_with_compounded(_annualised_fixture(n_days=252))
    assert st["n_independent_windows"] == 252, st
    assert st["citable"] is True, st.get("citable_reason")
    for key in ("total_return", "cagr", "arith_ann", "ann_vol", "vol_drag"):
        assert key in st, f"可引用时 `{key}` 必须在载荷里：{st}"
    assert st["cagr"] == pytest.approx(1.005799, rel=1e-6), st["cagr"]


def test_the_frontend_threshold_is_the_same_number_as_the_api_threshold():
    """⚠️ 两份副本（W 的门槛）中间要有锁：页面兜底常量必须等于 API 的判据值。

    ⇒ 改判据时只改一处 ⇒ 另一处由这条守卫喊。
    """
    from aplus.webapp import MIN_INDEPENDENT_WINDOWS
    src = INDEX.read_text(encoding="utf-8")
    m = re.search(r"const MIN_WINDOWS = (\d+);", src)
    assert m, "index.html 里找不到 MIN_WINDOWS 常量"
    assert int(m.group(1)) == MIN_INDEPENDENT_WINDOWS, (
        "页面的兜底门槛 %s ≠ API 的判据值 %s ⇒ 两份副本分叉了"
        % (m.group(1), MIN_INDEPENDENT_WINDOWS))


def test_the_endpoint_must_not_read_the_sidecar_as_the_artifact(tmp_path):
    """🚨 端点必须读**产物**，不是它的 sidecar。

    ⚠️ **实测踩到**：sidecar 的文件名是 `holdings_alert_<日期>.json.provenance.json`
    ⇒ 它**也**匹配 `holdings_alert_*.json`（glob 只看结尾）⇒
    不排掉它，端点就把**章**当产物读 ⇒ 页面显示「0 条持仓」而实际有 5 条。
    ⇒ 症状与「持仓清空了」**逐位同形**（第 89 层的形状）。
    """
    from fastapi.testclient import TestClient

    from aplus.webapp import create_app

    reports = tmp_path / "data" / "reports"
    reports.mkdir(parents=True)
    artifact = {
        "as_of": "2026-01-02",
        "question": "我手上这几只里，哪几只已经掉到最差那一档？",
        "items": [{"code": "600001.SH", "name": "甲", "action": "WATCH",
                   "action_criterion": "600001.SH：合成分 1.0 < 建仓日合成分 2.0 ⇒ 建议观察"}],
        "summary": {"REDUCE": 0, "WATCH": 1, "HOLD": 0, "UNDECIDED": 0},
        "population": {"n_holdings": 1, "codes": ["600001.SH"]},
        "entry": {"entry_day": "2026-01-01"},
        "criteria": {"rule_order": ["1. …"]},
        "boundaries": ["不预测涨跌"],
        "what_this_cannot_prove": ["不产生收益"],
    }
    (reports / "holdings_alert_2026-01-02.json").write_text(
        json.dumps(artifact, ensure_ascii=False), encoding="utf-8")
    (reports / "holdings_alert_2026-01-02.json.provenance.json").write_text(
        json.dumps({"generator": "scripts/run_daily.py"}), encoding="utf-8")

    body = TestClient(create_app(tmp_path / "data", llm=None)).get(
        "/api/holdings-alert").json()
    assert body["available"] is True, body
    assert body["as_of"] == "2026-01-02", body
    assert len(body["items"]) == 1, body
    assert body["items"][0]["code"] == "600001.SH", body
    assert body["items"][0]["criterion"], "每条必须带判据"
    assert body["report"] == "holdings_alert_2026-01-02.json", body


def test_the_page_fetches_the_holdings_alert_endpoint():
    """⚠️ 判据锚在**语法位置**上（`fetch`/`jget` 的实参），
    不是「文件里出现过这个字符串」（那会被注释与文案满足，第 13 层）。"""
    src = INDEX.read_text(encoding="utf-8")
    found = re.findall(r"jget\(\s*['\"](/api/[^'\"]+)['\"]", src)
    assert "/api/holdings-alert" in found, (
        "页面没有取 /api/holdings-alert（取到的端点：%s）" % found)


def test_the_page_renders_each_holding_action_and_its_criterion():
    """页面必须渲染**每一条**的动作与**判据**（预注册 §6）。"""
    src = INDEX.read_text(encoding="utf-8")
    assert "action_criterion" in src, "页面没有渲染 action_criterion"
    assert "UNDECIDED" in src, "页面没有渲染 UNDECIDED 这一态"

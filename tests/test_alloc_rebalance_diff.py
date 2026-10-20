"""「资产配置方案」必须有**调仓差异**（2026-10-20 的 P0）。

## 缺陷（实测，不是推演）

概览页「资产配置方案」表只列**要买的 5 只**，看不出「哪些该留、哪些该卖」。

根因：`scripts/run_daily.py` 调 `build_allocation(up, capital=capital)`
**没传 `current_positions`** ⇒ `aplus/alloc.py` 里 `current_codes` 为空 ⇒
keep/remove **结构上不可能出现**。实测 `alloc_2026-10-19.json` 的
`rebalance` 只有 5 条 `"action": "add"`，`grep -c '"action": "keep"'` = **0**、
`"action": "remove"` = **0**。

⇒ 相对 `holdings.json` 的真差本应是：keep **2**（001309/688110）、
add **3**（688610/688300/001285）、remove **8**。

## 这不是「少传一个参数」，是**产物与界面都看不出差别**

`rebalance` 全是 add 时，**两种完全不同的真相**长得一模一样：
①「差异算过了，就是全要买」；②「差异**根本没算**」（没传当前持仓）。
⇒ 必须有一个字段显式说「这个差异是怎么来的」：
`rebalance_source` = `'current_positions'` | `'none'`。

## 四层守卫（不可互换）

| 层 | 问什么 | 变异能杀 |
|---|---|---|
| 行为（`build_allocation`） | keep/add/remove 三种动作**真的**会出现，且集合正确 | 删掉 remove 分支 |
| 行为（口径） | `weight` 是比例、`amount` 是金额，二者**不可互换** | 把两者写反 |
| 接线（AST） | `run_daily` 真的把**当前持仓**传进去了 | 删掉 `current_positions=` 实参 |
| 行为（API） | `/api/allocation/latest` 把差异与来源一起递出来 | 少递一个字段 |

## 为什么「remove 的金额是 0」不是 bug

持仓的**市值/成本不在状态文件里**（`holdings.json` 只存代码与名字）。
被清掉的票**目标权重**是 0 ⇒ `weight=0`、`amount=0`。
编一个数出来（哪怕按 10% 猜）就是凭空多算一笔账。
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

_ROOT = Path(__file__).resolve().parents[1]
# 让本文件**从任何目录**都能跑（不靠「cwd 恰好是仓根」）。
# 仓里 `test_allocation_carries_its_own_date.py` 等已这么做。
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from aplus.alloc import build_allocation  # noqa: E402
from aplus.reports import save_allocation, save_predictions  # noqa: E402
from aplus.webapp import create_app  # noqa: E402

_RD = _ROOT / "scripts" / "run_daily.py"


def _ups(codes_names_scores):
    return pd.DataFrame({
        "code": [c for c, _, _ in codes_names_scores],
        "name": [n for _, n, _ in codes_names_scores],
        "direction": ["up"] * len(codes_names_scores),
        "score": [s for _, _, s in codes_names_scores],
    })


#: 生产实测的那一组（2026-10-19 榜的 top5 vs holdings.json 的 10 只）
TARGET = [("688610.SH", "埃科光电", 1.95), ("688300.SH", "联瑞新材", 1.89),
          ("688110.SH", "东芯股份", 1.77), ("001285.SZ", "瑞立科密", 1.75),
          ("001309.SZ", "德明利", 1.64)]
HELD = [("001309.SZ", "德明利"), ("688110.SH", "东芯股份"), ("301232.SZ", "飞沃科技"),
        ("688403.SH", "汇成股份"), ("688559.SH", "海目星"), ("688668.SH", "鼎通科技"),
        ("688629.SH", "华丰科技"), ("600499.SH", "科达制造"),
        ("688433.SH", "华曙高科"), ("300227.SZ", "光韵达")]


def _current():
    return [{"code": c, "name": n} for c, n in HELD]


# ── ① 行为：三种动作真的会出现，且集合逐只对得上 ────────────────────

def test_the_three_actions_appear_and_match_the_holdings():
    """★ keep 2 / add 3 / remove 8，且每一只都与 holdings.json 对得上。

    remove 的定义是「**上一期有、这一期没有**」⇒
    `set(remove) == set(当前持仓) − set(目标)`。
    """
    plan = build_allocation(_ups(TARGET), capital=50_000,
                            current_positions=_current())
    by_action = {a: {r["code"] for r in plan.rebalance if r["action"] == a}
                 for a in ("keep", "add", "remove")}
    assert by_action["keep"] == {"001309.SZ", "688110.SH"}
    assert by_action["add"] == {"688610.SH", "688300.SH", "001285.SZ"}
    assert by_action["remove"] == {c for c, _ in HELD} - {c for c, _, _ in TARGET}
    assert len(by_action["remove"]) == 8
    # 三种动作合起来 = 当前持仓 ∪ 目标（不多不少）
    assert (by_action["keep"] | by_action["add"] | by_action["remove"]) \
        == {c for c, _ in HELD} | {c for c, _, _ in TARGET}


def test_the_source_tells_whether_the_diff_was_computed():
    """★ `rebalance_source` 必须区分「算了」与「没算」。

    没传当前持仓 ⇒ keep/remove 结构上不可能出现 ⇒ 差异**不可信**。
    产物里没有这个标记时，前端无法区分「全要买」与「没算过」。
    """
    without = build_allocation(_ups(TARGET), capital=50_000)
    assert without.rebalance_source == "none"
    assert {r["action"] for r in without.rebalance} == {"add"}

    with_current = build_allocation(_ups(TARGET), capital=50_000,
                                    current_positions=_current())
    assert with_current.rebalance_source == "current_positions"


def test_an_explicitly_empty_book_is_not_the_same_as_not_passing_one():
    """`current_positions=[]`（明确空仓）与**不传**是两件事。

    空仓 ⇒ 全 add 是**真的**答案 ⇒ source='current_positions'。
    不传 ⇒ 差异没算 ⇒ source='none'。
    ⇒ 这里用 `is not None` 判「有没有传」，不靠真假值。
    """
    plan = build_allocation(_ups(TARGET), capital=50_000, current_positions=[])
    assert plan.rebalance_source == "current_positions"
    assert {r["action"] for r in plan.rebalance} == {"add"}


# ── ② 口径：weight 是比例、amount 是金额 ───────────────────────────

def test_weight_is_a_ratio_and_amount_is_the_money():
    """★ 两者**不可互换**：现有表里每只 5000 元 = 50000 × 0.10。

    写反了不会崩、不会少字段，只会让用户按 0.1 元去买 5000 元的票。
    """
    plan = build_allocation(_ups(TARGET), capital=50_000,
                            current_positions=_current())
    for r in plan.rebalance:
        if r["action"] == "remove":
            # 被清掉的票：目标权重 0；金额**不在状态文件里** ⇒ 不编
            assert r["weight"] == 0.0
            assert r["amount"] == 0.0
            continue
        assert r["weight"] == pytest.approx(0.10)
        assert r["amount"] == pytest.approx(50_000 * r["weight"])
        assert r["amount"] == pytest.approx(5_000)


def test_every_rebalance_row_carries_the_full_five_fields():
    """前端要照 `code/name/action/weight/amount` 五个名字写 ⇒ 一个都不能缺。"""
    plan = build_allocation(_ups(TARGET), capital=50_000,
                            current_positions=_current())
    for r in plan.rebalance:
        assert set(r) == {"code", "name", "action", "weight", "amount"}, r
        assert r["action"] in {"keep", "add", "remove"}
    names = {r["code"]: r["name"] for r in plan.rebalance}
    assert names["001309.SZ"] == "德明利"
    assert names["301232.SZ"] == "飞沃科技"      # remove 的名字来自当前持仓


# ── ③ 接线：run_daily 真的把**当前持仓**传进去了（AST）──────────────

def _step_fn() -> str:
    src = _RD.read_text(encoding="utf-8")
    m = re.search(r"^def step_predict_and_allocate\(.*?(?=^def |\Z)", src, re.S | re.M)
    assert m, "step_predict_and_allocate 不见了 —— 守卫比产品活得久"
    return m.group(0)


def test_run_daily_passes_the_current_positions_in():
    """★ 调用点必须带 `current_positions` 实参。

    变异：把 `build_allocation(up, capital=capital, current_positions=…)`
    改回 `build_allocation(up, capital=capital)` ⇒ 红。
    """
    tree = ast.parse(_step_fn())
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "build_allocation"]
    assert calls, "找不到 build_allocation(...) 调用 —— 守卫需要重新评估"
    kwargs = {kw.arg: ast.unparse(kw.value) for call in calls for kw in call.keywords
              if kw.arg}
    assert "current_positions" in kwargs, (
        f"build_allocation 的实参是 {sorted(kwargs)} —— "
        "没传 current_positions ⇒ keep/remove 结构上不可能出现（旧缺陷）"
    )


def test_the_current_positions_come_from_the_previous_state():
    """★ 传进去的必须是**进函数时**读到的 `prev`，不是今天刚写的那份。

    变异：把 `current_positions_from_state(prev)` 改成
    `current_positions_from_state(state)`（调仓日已被写成今天的名单）
    ⇒ keep 恒等于全部、remove 恒为空 ⇒ 红。
    """
    tree = ast.parse(_step_fn())
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "build_allocation"]
    kwargs = {kw.arg: ast.unparse(kw.value) for call in calls for kw in call.keywords
              if kw.arg}
    src = kwargs["current_positions"]
    assert "prev" in src, (
        f"current_positions 的实参是 {src!r} —— "
        "必须是**今天之前**手上那份（prev），不是今天刚写的 state['up']"
    )
    assert "trade_up" not in src and "trade_down" not in src, (
        f"current_positions 的实参是 {src!r} —— "
        "拿今天的名单当「当前持仓」⇒ remove 永远为空"
    )


def test_the_helper_refuses_to_invent_names_when_the_two_lists_disagree():
    """`up` 与 `up_names` 长度不符时**不配对**。

    错配的名字比没有名字更糟：它会让用户以为自己在看另一只票。
    """
    from scripts.run_daily import current_positions_from_state

    assert current_positions_from_state(None) is None, "状态读不出来 ⇒ 必须返回 None"
    good = current_positions_from_state({"up": ["001309.SZ", "688110.SH"],
                                          "up_names": ["德明利", "东芯股份"]})
    assert good == [{"code": "001309.SZ", "name": "德明利"},
                     {"code": "688110.SH", "name": "东芯股份"}]
    bad = current_positions_from_state({"up": ["001309.SZ", "688110.SH"],
                                         "up_names": ["德明利"]})
    assert [p["code"] for p in bad] == ["001309.SZ", "688110.SH"]
    assert all(p["name"] == "" for p in bad), "长度不符还配了名字 ⇒ 名字可能是错的"


# ── ④ API 层：`/api/allocation/latest` ──────────────────────────────

def _client(tmp_path, rebalance, with_source=True):
    reports = tmp_path / "reports"
    date = "2026-10-19"
    plan = {
        "capital": 50_000,
        "positions": [{"code": c, "name": n, "weight": 0.1, "amount": 5000.0,
                       "score": s} for c, n, s in TARGET],
        "cash_weight": 0.5,
        "rebalance": rebalance,
        "risk_notes": [],
    }
    if with_source:
        plan["rebalance_source"] = "current_positions"
    save_allocation(reports, date, plan)
    preds = reports / "predictions"
    preds.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"date": [date]}).to_parquet(preds / f"pred_{date}.parquet")
    return TestClient(create_app(tmp_path, llm=object()))


def test_the_api_carries_the_diff_and_its_source(tmp_path):
    rows = [
        {"code": "001309.SZ", "name": "德明利", "action": "keep",
         "weight": 0.1, "amount": 5000.0},
        {"code": "688610.SH", "name": "埃科光电", "action": "add",
         "weight": 0.1, "amount": 5000.0},
        {"code": "301232.SZ", "name": "飞沃科技", "action": "remove",
         "weight": 0.0, "amount": 0.0},
    ]
    body = _client(tmp_path, rows).get("/api/allocation/latest").json()
    assert body["rebalance_source"] == "current_positions"
    assert [r["action"] for r in body["rebalance"]] == ["keep", "add", "remove"]
    for r in body["rebalance"]:
        assert set(r) == {"code", "name", "action", "weight", "amount"}, r


def test_a_legacy_allocation_reports_the_source_as_none(tmp_path):
    """旧产物（2026-10-20 之前）没有 `rebalance_source`，元素只有 {code, action}。

    ⇒ 一律按 `'none'` 递（差异**没算过**，不可信），
    缺的字段填 `null`（**不编**：不从 `positions` 里猜）。
    """
    body = _client(tmp_path, [{"code": "001309.SZ", "action": "add"}],
                   with_source=False).get("/api/allocation/latest").json()
    assert body["rebalance_source"] == "none"
    row = body["rebalance"][0]
    assert row["code"] == "001309.SZ" and row["action"] == "add"
    assert row["name"] is None and row["weight"] is None and row["amount"] is None

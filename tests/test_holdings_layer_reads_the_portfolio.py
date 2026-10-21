"""持仓层要读**钱包**，不是候选池。

## 缺陷（2026-10-21 抓到）

`scripts/run_daily.py` 的持仓层有三处把 `holdings.json::up`
（**候选池** = 那份 10 只涨腿名单）当成了**钱包**：

| 面 | 旧行为 | 实测后果 |
|---|---|---|
| 调仓日指令 | 「买入这 **10** 只」 | **6 只**配置层判「1 手 > ¥5,000」买不进 |
| 非调仓日指令 | 「继续持有：**10** 只」 | 其中 6 只**从未买过** |
| 调仓差异 | 「**卖 6 只**」 | 卖的是**从未持有过**的票 |

三处都长一个样：读数正常、报告照出、**没有任何东西会红**。

## 为什么不能改 `state["up"]` 的含义

`aplus/holdings_entry.py` 的模块 docstring 逐字写着
「**持仓 = 建仓日那份配置里真正拿到钱的那些**
（`alloc_<建仓日>.json::positions`）—— **不是 `holdings.json::up`（那是候选池）**」，
而且它**靠 `up` 做「集合唯一匹配」来定位建仓日**
⇒ 改 `up` 的语义会打断建仓日匹配。
⇒ 正解是**加一列** `held_codes`（钱包），两列并存、各司其职（第 143 层：先建行，不改旧列）。

## 这条文件守什么

1. `portfolio_codes`：**有** ⇒ 给钱包；**没有** ⇒ `None`（**不回落到候选池**）。
2. `current_positions_from_state`：没有钱包 ⇒ `None` ⇒ 调仓差异**不可信**。
3. `holdings_layer_view`：调仓日的 `buy` = **配置方案真正拿到钱的那些**。
4. **端到端对拍**：造一份「1 只贵到买不进」的候选 ⇒ 指令文本里
   **不许出现**那只票，**必须出现**留下的那只。
5. 接线层（AST）：`instruction(...)` 的买入清单实参**不许**是 `trade_up`。
"""
import ast
import json
import re
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aplus.alloc import build_allocation  # noqa: E402
from aplus.live_rebalance import instruction, portfolio_codes  # noqa: E402
from scripts.run_daily import (  # noqa: E402
    current_positions_from_state,
    holdings_layer_view,
)

_RUN_DAILY = ROOT / "scripts" / "run_daily.py"

#: 「1 手 > 每份金额 ¥5,000」的票（科创板 200 股起 + 高价 ⇒ 16080 元）
TOO_DEAR = "688610.SH"
#: 买得起的（100 股 × ¥29.37 = ¥2,937 ≤ ¥5,000）
AFFORDABLE = "001285.SZ"
#: 同样买不起（200 股 × ¥59.02 = ¥11,804）
TOO_DEAR_2 = "688110.SH"


def _candidates() -> pd.DataFrame:
    return pd.DataFrame({
        "code": [TOO_DEAR, AFFORDABLE, TOO_DEAR_2],
        "name": ["埃科光电", "瑞立科密", "东芯股份"],
        "score": [2.13, 1.69, 1.50],
    })


def _closes() -> dict:
    return {TOO_DEAR: 80.40, AFFORDABLE: 29.37, TOO_DEAR_2: 59.02}


def _plan():
    return build_allocation(
        _candidates(), capital=50_000.0, max_positions=10, per_stock_cap=0.10,
        current_positions=None, is_rebalance_day=True, closes=_closes(),
    )


# ── ① 钱包这一列的语义 ────────────────────────────────────────────

def test_the_wallet_column_is_the_portfolio_not_the_candidate_pool():
    """有 `held_codes` ⇒ 给**钱包**，绝不返回候选池。"""
    state = {"up": ["A", "B", "C"], "up_names": ["a", "b", "c"],
             "held_codes": ["A", "C"]}
    assert portfolio_codes(state) == ["A", "C"], (
        "钱包这一列没有被读出来")


def test_a_state_without_the_wallet_column_reads_as_unknown():
    """🚨 缺 `held_codes` ⇒ **读不出来**（`None`），**不许**回落到候选池。

    回落实测代价：10 只候选里 6 只从未拿到钱 ⇒ 回落后会告诉用户
    「你持有这 10 只」⇒ 其中 6 只他没买过。
    """
    state = {"up": ["A", "B", "C"], "up_names": ["a", "b", "c"]}
    assert portfolio_codes(state) is None, (
        "缺钱包这一列却给出了持仓 ⇒ 报告会叫用户卖掉从未持有过的票")
    assert portfolio_codes(None) is None
    assert portfolio_codes({}) is None


def test_an_empty_wallet_is_different_from_an_unreadable_one():
    """「空仓」与「读不出来」是两件事。

    空仓 ⇒ 全 add（真的答案）；读不出来 ⇒ 差异不可信。
    """
    assert portfolio_codes({"held_codes": []}) == []
    assert portfolio_codes({"up": ["A"]}) is None


# ── ② 调仓差异的「当前持仓」 ──────────────────────────────────────

def test_the_rebalance_diff_refuses_to_guess_the_current_positions():
    """没有钱包 ⇒ 差异**不可信**（`None`），不许拿候选池顶上。"""
    pool_only = {"up": ["A", "B", "C"], "up_names": ["a", "b", "c"]}
    assert current_positions_from_state(pool_only) is None, (
        "只有候选池却算出了「当前持仓」⇒ 会算出「卖 3 只」这种从未持有过的票")


def test_the_rebalance_diff_uses_the_wallet_when_it_is_readable():
    """有钱包 ⇒ 差异对着**钱包**算，且名字从候选池借（只借名字）。"""
    state = {"up": ["A", "B", "C"], "up_names": ["甲", "乙", "丙"],
             "held_codes": ["A", "C"]}
    assert current_positions_from_state(state) == [
        {"code": "A", "name": "甲"}, {"code": "C", "name": "丙"}]


# ── ③ 持仓视图：调仓日的买入清单 = 真正拿到钱的那些 ────────────────

def test_the_buy_list_is_the_funded_positions_not_the_candidates():
    """🚨 **核心**：调仓日的「买入这 N 只」= 配置方案真正拿到钱的那些。"""
    plan = _plan()
    funded = [p["code"] for p in plan.positions]
    assert TOO_DEAR not in funded, "夹具没起作用（贵的票被配进去了）"
    assert funded == [AFFORDABLE], f"夹具的剔除结果不对：{funded}"

    view = holdings_layer_view(True, plan.positions, None)
    assert view["buy"] == funded, (
        f"买入清单是 {view['buy']}，而配置方案真正配的是 {funded} ⇒ "
        "指令会让用户买平台自己判定买不起的票")


def test_a_non_rebalance_day_buys_nothing_and_keeps_the_wallet():
    """非调仓日：不买，且钱包原样带出。"""
    plan = _plan()
    view = holdings_layer_view(False, plan.positions, ["A", "C"])
    assert view["buy"] == []
    assert view["held_codes"] == ["A", "C"]
    assert view["reliable"] is True


def test_a_non_rebalance_day_with_an_unreadable_wallet_says_so():
    """非调仓日 + 钱包读不出来 ⇒ `reliable=False`，**不许**编一批持仓。"""
    view = holdings_layer_view(False, _plan().positions, None)
    assert view["reliable"] is False
    assert view["held_codes"] is None


# ── ④ 端到端：指令文本本身 ────────────────────────────────────────

def test_the_instruction_never_names_a_stock_the_plan_could_not_fund():
    """★ 端到端对拍：**指令不许点名配置方案没配的票**。

    这是这轮修复要守的那条性质本身 —— 不是「某个函数返回了什么」，
    而是**用户读到的那段话**。
    """
    plan = _plan()
    view = holdings_layer_view(True, plan.positions, None)
    text = instruction(True, view["buy"], ["600241.SH"], 63, None,
                       held_up=None, held_down=None)

    assert TOO_DEAR not in text, (
        f"指令点名了平台判定买不起的票 {TOO_DEAR}：\n{text}")
    assert TOO_DEAR_2 not in text, (
        f"指令点名了平台判定买不起的票 {TOO_DEAR_2}：\n{text}")
    assert AFFORDABLE in text, f"指令漏掉了真正配得起的票：\n{text}"
    assert "买入这 1 只" in text, f"买入只数与配置方案不符：\n{text}"


def test_a_non_rebalance_day_with_an_unknown_wallet_does_not_say_empty():
    """🚨 「读不出来」≠「空仓」。

    报成「（空）」会让用户以为该清仓 ⇒ **错的方向是让人卖光**。
    """
    text = instruction(False, [], ["600241.SH"], 63, None,
                       held_up=None, held_down=["600241.SH"])
    assert "读不出来" in text, f"没说实话：\n{text}"
    assert "（空）" not in text, (
        f"把「读不出来」写成了「空仓」⇒ 用户会以为该清仓：\n{text}")


def test_a_genuinely_empty_wallet_says_empty_warehouse():
    """反向正控：真空仓**就该**说空仓（否则上面那条恒真）。"""
    text = instruction(False, [], ["600241.SH"], 63, None,
                       held_up=[], held_down=["600241.SH"])
    assert "（空仓）" in text, f"真空仓被说成「读不出来」：\n{text}"
    assert "读不出来" not in text


# ── ⑤ 落盘：白名单里的钱包这一列 ──────────────────────────────────

def _state_for_disk(**extra) -> dict:
    base = {"as_of": "2026-10-21", "days_since_rebalance": 0, "every": 63,
            "buffer_multiple": None, "up": ["A", "B"], "down": ["C"],
            "up_names": ["甲", "乙"], "down_names": ["丙"]}
    base.update(extra)
    return base


def test_the_wallet_column_actually_lands_on_disk(tmp_path):
    """🚨 `save_state` 的落盘 `out` 是**白名单** ⇒ 不显式写进去，
    它就**永远落不了盘** —— 而**所有测试照样全绿**（第 96 层：守卫对产物零覆盖）。

    ⇒ 这条守卫做的是**往返**（写进去 → 读回来），不是「函数返回了什么」。
    """
    from aplus.live_rebalance import load_state, save_state

    reports = tmp_path / "reports"
    reports.mkdir()
    save_state(reports, _state_for_disk(held_codes=["A"]))

    raw = json.loads((reports / "holdings.json").read_text(encoding="utf-8"))
    assert raw.get("held_codes") == ["A"], (
        f"钱包这一列没落盘（落盘内容：{sorted(raw)}）⇒ "
        "整个修复是空操作，而上面的测试全绿")
    assert load_state(reports).get("held_codes") == ["A"], "读回来时丢了"


def test_an_unknown_wallet_stays_unknown_on_disk(tmp_path):
    """⚠️ 缺 ⇒ 落盘后**仍缺**。

    写成 `[]` 会被读成「**空仓**」⇒ 持仓指令会说「（空仓）」⇒
    **叫用户清仓** —— 错的方向是让人卖光。
    """
    from aplus.live_rebalance import load_state, save_state

    reports = tmp_path / "reports"
    reports.mkdir()
    save_state(reports, _state_for_disk())

    raw = json.loads((reports / "holdings.json").read_text(encoding="utf-8"))
    assert "held_codes" not in raw, (
        "缺钱包这一列却落了盘 ⇒ 读回来是「空仓」而不是「读不出来」")
    assert portfolio_codes(load_state(reports)) is None, (
        "读出来是「空仓」而不是「读不出来」")


def test_a_state_without_the_wallet_column_is_still_loadable(tmp_path):
    """旧状态文件（没有这一列）必须**照样读得出来**。

    把它加进 `REQUIRED_KEYS` ⇒ 旧文件被判成**坏状态** ⇒ 整份持仓被当成
    「读不出来」⇒ 明天按**首次建仓**重新买一遍（多付一次成本 + 拿着错仓位）。
    """
    from aplus.live_rebalance import load_state, save_state

    reports = tmp_path / "reports"
    reports.mkdir()
    save_state(reports, _state_for_disk())
    (reports / "holdings.json").write_text(json.dumps(
        {"as_of": "2026-10-20", "days_since_rebalance": 5, "every": 21,
         "buffer_multiple": None, "up": ["A"], "down": [],
         "up_names": ["甲"], "down_names": []}, ensure_ascii=False),
        encoding="utf-8")

    state = load_state(reports)
    assert state is not None, "旧状态被判成坏状态 ⇒ 明天会按首次建仓重买一遍"
    assert portfolio_codes(state) is None, (
        "旧状态没有钱包这一列 ⇒ 必须是「读不出来」")


# ── ⑥ 接线层：AST（不许靠子串）──────────────────────────────────

def _step_body() -> str:
    src = _RUN_DAILY.read_text(encoding="utf-8")
    m = re.search(r"^def step_predict_and_allocate\(.*?(?=^def |\Z)", src, re.S | re.M)
    assert m, "step_predict_and_allocate 不见了 —— 守卫比产品活得久"
    return m.group(0)


def test_the_instruction_buy_list_is_not_the_candidate_list():
    """🚨 接线层：`instruction(...)` 的买入清单**不许**是 `trade_up`。

    `trade_up` = 今天那 10 只候选（未经可买性过滤）⇒ 旧接线就是这个 ⇒
    指令与配置方案各说各话。
    """
    tree = ast.parse(_step_body())
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "instruction"]
    assert calls, "找不到 instruction(...) 调用 —— 守卫需要重新评估"

    for call in calls:
        args = [ast.unparse(a) for a in call.args]
        assert "trade_up" not in args, (
            f"instruction() 的买入清单实参是 {args} —— "
            "那是**候选池**，不是配置方案真正配到的那些")
        assert any("buy" in a for a in args), (
            f"instruction() 的买入清单实参是 {args} —— "
            "必须取自 holdings_layer_view 的 buy（= 配置方案配到的那些）")


def test_the_holdings_view_is_built_from_the_allocation_positions():
    """`holdings_layer_view` 的实参必须是 `plan.positions`（配置方案）。"""
    tree = ast.parse(_step_body())
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "holdings_layer_view"]
    assert calls, "找不到 holdings_layer_view(...) 调用 —— 守卫需要重新评估"
    for call in calls:
        args = [ast.unparse(a) for a in call.args]
        assert any("positions" in a for a in args), (
            f"holdings_layer_view 的实参是 {args} —— "
            "必须是 plan.positions（配置方案真正拿到钱的那些）")


def test_the_wallet_column_is_written_into_the_state():
    """调仓日必须把**钱包**落进 `state`（否则明天又是一份读不出来的状态）。

    ⚠️ **不用子串**（第 13 层）：`"HELD_CODES_KEY" in body` 会被**我自己写的注释**满足
    ⇒ 改成 AST：必须存在一条 `state[HELD_CODES_KEY] = …` 的**赋值语句**。
    """
    body = _step_body()
    tree = ast.parse(body)
    writes = [ast.unparse(n) for n in ast.walk(tree)
              if isinstance(n, ast.Assign)
              and any(isinstance(t, ast.Subscript)
                      and ast.unparse(t.slice) == "HELD_CODES_KEY"
                      for t in n.targets)]
    assert writes, (
        "钱包那一列没有落盘（找不到 `state[HELD_CODES_KEY] = …`）⇒ "
        "明天的持仓层还是瞎的")


# ── ⑦ API 层：`/api/status` 的 live 载荷 ──────────────────────────

def _api_live(tmp_path, state):
    from fastapi.testclient import TestClient

    from aplus.webapp import create_app
    reports = tmp_path / "reports"
    reports.mkdir(exist_ok=True)
    if state is not None:
        (reports / "holdings.json").write_text(
            json.dumps(state, ensure_ascii=False), encoding="utf-8")
    return TestClient(create_app(tmp_path)).get("/api/status").json()["live"]


def test_the_status_endpoint_carries_the_wallet(tmp_path):
    """前端要拿到**钱包**，而不只是候选池。"""
    live = _api_live(tmp_path, _state_for_disk(held_codes=["A"]))
    assert "held_codes" in live, "live 载荷没有钱包这一列"
    assert live["held_codes"] == ["A"], f"钱包递错了：{live.get('held_codes')}"


def test_the_status_endpoint_flags_an_unknown_wallet(tmp_path):
    """旧状态文件 ⇒ 钱包**读不出来** ⇒ 必须能看出来。

    ⚠️ 「读不出来」与「空仓」在界面上**必须不同形**：
    报成空仓 ⇒ 用户以为该清仓 ⇒ **错的方向是让人卖光**。
    """
    live = _api_live(tmp_path, _state_for_disk())
    assert live["held_codes"] == []
    assert live["held_codes_known"] is False, (
        "钱包读不出来却没标出来 ⇒ 前端会把它渲染成「空仓」")


def test_the_status_endpoint_marks_a_known_empty_wallet_as_known(tmp_path):
    """反向正控：真空仓**是**「已知的空仓」，不许与「读不出来」同形。"""
    live = _api_live(tmp_path, _state_for_disk(held_codes=[]))
    assert live["held_codes"] == []
    assert live["held_codes_known"] is True, (
        "真空仓被标成「读不出来」⇒ 上面那条守卫会退化成恒真")

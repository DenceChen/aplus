"""「持仓基准日」必须是**建仓日**，不是**最后出单日**（2026-10-20 的 P0）。

## 缺陷（实测，不是推演）

概览页显示「持仓基准日 2026-10-19」，让人以为持有的是 10-19 那份名单。
真相：`holdings.json` 的 `as_of` 由 `scripts/run_daily.py` **每天无条件**写
`state["as_of"] = today`（不论当天是不是调仓日）⇒ 它的语义是
「**最后一次出单日**」，不是「持仓是哪天定的」。

实锤：holdings.json 的 up 名单与 `pred_2026-10-15.parquet` 的 up
**10/10 逐只相同**（001309/300227/301232/600499/688110/688403/688433/688559/
688629/688668），与 10-19 榜只交集 **4/10** ⇒ 手上是 10-15 那份，
其中 6 只已经不在最新名单里。照字面理解 ⇒ **买错票**。

## 根因（两段，各自都能单独成立）

① `scripts/run_daily.py` 写了 `state["held_since"] = today`（调仓日），
   但 `aplus/live_rebalance.py::save_state` 的 `out` 字典只列 8 个键，
   **不含 `held_since`** ⇒ 那个值只活在内存里，落盘文件里
   `grep -c held_since` = **0**。
② 于是「持仓基准日」只能拿 `as_of` 顶 —— 而 `as_of` 是最后出单日。

⇒ 这是一条**断链**：算出来的东西没有落盘 ⇒ 落盘的东西没有身份。

## 三层守卫，各钉各的（不可互换）

| 层 | 问什么 | 变异能杀 |
|---|---|---|
| 写（AST） | `save_state` 的 `out` 里**有没有** `held_since` 这个键 | 删掉 `out` 里那一行 |
| 行为（真落盘真读回） | 值真的活过了「非调仓日」那一轮 | `advance` 里丢掉它 |
| 行为（API） | `/api/status` 的 `live` 把它连同**可信度**一起递出来 | 少递一个字段 |

## 为什么「回落」必须显式标出来

旧 state 文件没有这个键 ⇒ 回落成 `as_of`。但 `as_of` 是**出单日**，
把它说成持仓日就是第 6 种形状（看起来像开关，实际焊死）的近亲：
界面照常渲染、HTTP 200、零告警，而它说的是一件假事。

⇒ `live.held_since_is_fallback` / `live.held_since_reliable` 两个字段
必须**互补**且**都递出来** ⇒ 前端才能在回落时说「实际持仓日未知」。
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient

_ROOT = Path(__file__).resolve().parents[1]
# 让本文件**从任何目录**都能跑（不靠「cwd 恰好是仓根」）。
# 仓里 `test_allocation_carries_its_own_date.py` 等已这么做。
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from aplus.live_rebalance import (  # noqa: E402
    HELD_SINCE_KEY,
    advance,
    held_since_info,
    load_state,
    save_state,
)
from aplus.webapp import _live_holdings_payload, create_app  # noqa: E402

_LR = _ROOT / "aplus" / "live_rebalance.py"
_RD = _ROOT / "scripts" / "run_daily.py"


# ── 夹具 ────────────────────────────────────────────────────────────

def _state(**kw):
    """一份 `load_state` 会认的最小状态（`REQUIRED_KEYS` 全在）。"""
    base = {
        "as_of": "2026-10-19",
        "days_since_rebalance": 2,
        "every": 21,
        "buffer_multiple": None,
        "up": ["001309.SZ", "688110.SH", "301232.SZ"],
        "down": ["301190.SZ"],
        "up_names": ["德明利", "东芯股份", "飞沃科技"],
        "down_names": ["善水科技"],
    }
    base.update(kw)
    return base


# ── ① 写层：`save_state` 的 out 字典里**必须**有 held_since ─────────

def test_save_state_writes_the_held_since_key():
    """AST 层：**键必须真的在 `out` 里**。

    为什么不锚在「文件里出现过这个词」：删掉 `out` 里那一行之后，
    `held_since` 仍然出现在本函数的其它地方（参数校验、`state.get(...)`），
    子串断言照样全绿 —— 第 13 种形状（注释满足了一个子串断言）。
    ⇒ 只认**写进 `out` 的那个字典的键**。
    """
    tree = ast.parse(_LR.read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "save_state")
    keys: list[str] = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "out" for t in node.targets):
            continue
        if isinstance(node.value, ast.Dict):
            keys = [k.value for k in node.value.keys if isinstance(k, ast.Constant)]
    assert HELD_SINCE_KEY in keys, (
        f"save_state 落盘的键里没有 {HELD_SINCE_KEY!r}（实得 {sorted(keys)}）—— "
        "run_daily 算出来的建仓日又只活在内存里了（旧缺陷："
        "holdings.json 里 grep -c held_since = 0）"
    )


# ── ② 行为层：值真的活过了「非调仓日」那一轮 ────────────────────────

def test_held_since_survives_a_non_rebalance_day(tmp_path):
    """★ 建仓日不随「出单日」往前走。

    2026-10-15 建仓 → 10-16 / 10-19 是**非调仓日** ⇒
    `as_of` 每天往前走，`held_since` **必须原地不动**。
    这正是「持仓基准日 ≠ 最后出单日」在状态机里的样子。
    """
    reports = tmp_path / "reports"
    reports.mkdir()
    save_state(reports, _state(as_of="2026-10-15", days_since_rebalance=0,
                               held_since="2026-10-15"))

    for day, n in (("2026-10-16", 1), ("2026-10-19", 2)):
        cur = load_state(reports)
        nxt = advance(cur, day)
        nxt["as_of"] = day
        save_state(reports, nxt)
        got = load_state(reports)
        assert got["held_since"] == "2026-10-15", (
            f"{day} 之后建仓日变成了 {got['held_since']!r} —— "
            "持仓是哪天定的**不随出单日移动**；它移动就等于又回到了「出单日」"
        )
        assert got["as_of"] == day, f"出单日没往前走（{got['as_of']!r}）"
        assert got["days_since_rebalance"] == n


def test_a_state_without_held_since_still_loads(tmp_path):
    """🚨 **旧 state 文件必须照样读得出来**。

    `held_since` 是后加的键。若把它放进 `REQUIRED_KEYS`，
    旧文件会被判成**坏状态** ⇒ `load_state` 返回 None ⇒
    整份持仓被当成「读不出来」⇒ 明天按**首次建仓**重新买一遍。
    ⇒ 这条钉的是「缺它 ≠ 坏状态」。
    """
    reports = tmp_path / "reports"
    reports.mkdir()
    raw = _state()
    raw.pop("held_since", None)
    (reports / "holdings.json").write_text(json.dumps(raw, ensure_ascii=False),
                                           encoding="utf-8")
    got = load_state(reports)
    assert got is not None, "旧格式的 state 被判成坏状态了 ⇒ 持仓会被当成读不出来"
    assert got["as_of"] == "2026-10-19"


# ── ③ 三态：真值 / 回落 / 读不出来 ──────────────────────────────────

def test_a_real_held_since_is_reliable():
    info = held_since_info(_state(held_since="2026-10-15"))
    assert info == {"held_since": "2026-10-15", "is_fallback": False, "reliable": True}


def test_a_legacy_state_falls_back_to_as_of_and_says_so():
    """★ 回落必须是**显式**的：`is_fallback=True` 且 `reliable=False`。

    回落值 = `as_of`（最后出单日）。它是**唯一**能给的答案，
    但它**不是**建仓日 ⇒ 前端必须能据此说「实际持仓日未知」。
    """
    info = held_since_info(_state())          # 没有 held_since 键 = 旧文件
    assert info["held_since"] == "2026-10-19", "回落值应当是 as_of"
    assert info["is_fallback"] is True, "回落了却没标出来 ⇒ 用户会把出单日当成持仓日"
    assert info["reliable"] is False, "回落的值不许说可靠"


def test_an_unreadable_state_has_no_held_since_at_all():
    """状态读不出来 ⇒ **没有**任何日期可给。

    既不是「回落」也不是「可靠」⇒ 两个旗子都是 false，值是 None。
    （这与「空仓」严格不同：`state is None` ⇒ 前端渲染「持仓状态读不出来」。）
    """
    info = held_since_info(None)
    assert info == {"held_since": None, "is_fallback": False, "reliable": False}


# ── ④ API 层：`/api/status` 的 live 载荷 ────────────────────────────

def test_the_status_endpoint_carries_the_three_held_since_fields(tmp_path):
    """三个字段必须**都在**，且语义一致（真值 ⇒ 可靠；回落 ⇒ 不可信）。"""
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "holdings.json").write_text(
        json.dumps(_state(held_since="2026-10-15"), ensure_ascii=False),
        encoding="utf-8")
    live = TestClient(create_app(tmp_path)).get("/api/status").json()["live"]
    for k in ("held_since", "held_since_is_fallback", "held_since_reliable"):
        assert k in live, f"live 载荷缺 {k}"
    assert live["held_since"] == "2026-10-15"
    assert live["held_since_is_fallback"] is False
    assert live["held_since_reliable"] is True
    # 它**不是** as_of：这两个字段指向不同的日子（这正是缺陷本身）
    assert live["as_of"] == "2026-10-19"
    assert live["held_since"] != live["as_of"]


def test_the_status_endpoint_flags_a_fallback_instead_of_hiding_it(tmp_path):
    """旧 state 文件 ⇒ 回落成 as_of，**且**两个旗子逐字递出来。"""
    reports = tmp_path / "reports"
    reports.mkdir()
    raw = _state()
    raw.pop("held_since", None)
    (reports / "holdings.json").write_text(json.dumps(raw, ensure_ascii=False),
                                           encoding="utf-8")
    live = _live_holdings_payload(reports)
    assert live["held_since"] == "2026-10-19"
    assert live["held_since_is_fallback"] is True
    assert live["held_since_reliable"] is False


def test_the_two_flags_are_never_both_true(tmp_path):
    """`is_fallback` 与 `reliable` 必须**互补**（第三个情形是「都没有值」）。

    两个都 true = 「它是回落的」与「它可靠」同时成立 = 自相矛盾；
    两个都 false 且有值 = 有值却不知道它可不可信 = 前端无从显示。
    """
    reports = tmp_path / "reports"
    reports.mkdir()
    for st in (_state(held_since="2026-10-15"), _state(), None):
        if st is None:
            (reports / "holdings.json").unlink(missing_ok=True)
        else:
            (reports / "holdings.json").write_text(
                json.dumps(st, ensure_ascii=False), encoding="utf-8")
        info = held_since_info(load_state(reports))
        assert not (info["is_fallback"] and info["reliable"]), info
        if info["held_since"] is not None:
            assert info["is_fallback"] != info["reliable"], info

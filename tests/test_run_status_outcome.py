"""「今天那次尝试的结局」必须**说得出来**（2026-10-20）。

## 事故（实测，不是推演）

2026-10-20 18:30 的 launchd 日更：采集失败（baostock 超时）⇒ fail-closed
**一份名单都没出**，落盘的是：

```json
{"date": "2026-10-20", "status": "skipped",
 "steps": {"run_day_gate": "WORKDAY_NOT_IN_STORE"},
 "updated_at": "2026-10-20 18:31:36"}
```

界面上只有「**部分完成 · 2026-10-20 · run_day_gate**」——一个**门名**：
- 它没说「**今天没出单**」；
- 更没说「**不是 cron 没跑，是采集没拿到今天的数据**」；
- 而用户真正要修的是**采集**。

⇒ `status:"skipped"` 本身也是**歧义**的：同一个值同时表示
「今天不是工作日」（本来就不出单）与「今天工作日但采集失败」（本该出单却没出）。
⇒ 这两件事在同一个值里 ⇒ 界面无从说真话。

## 两个量，**不许合并**（本仓反复吃过的亏）

| 字段 | 它说的是 |
|---|---|
| `date` / `status` / `steps` | **那一次尝试**跑在哪天、跑成没跑成、哪一步没过 |
| `attempted_date` / `outcome` / `outcome_reason` | **那一次尝试的结局**：出了单没出单、为什么没出 |

`date` / `status` / `steps` **保持原义**（改了会连带改坏 `guard_daily.py` 的补跑判据
——它的唯一判据是 `status == "ok"`——与前端的既有渲染）。

## 为什么「工作日但仓里没数据」必须**单独**一个值

周末/节假日 ⇒ 本来就不出单（不是故障，用户不该被吓到）；
工作日但仓里没这一天 ⇒ **本该出单却没出**（采集挂了，用户该去查采集）。

## 诚实边界（必须写在脸上）

仓里**没有交易所日历** ⇒「法定节假日」与「采集失败」在「今天不在库里」这一件事上
**同形**（`aplus/trading_calendar.py` 的 `RUN_DAY_LABELS` 早就逐字写明）。
⇒ 本模块**不假装分得开**：它把「今天是工作日 + 仓里缺这一天」这个**能判定的事实**
说出来，并在理由里写明残余歧义与**要查的是采集**。
真要分开必须引入**外部交易日历**（baostock `query_trade_dates`）——
那是给网络依赖加一条新的失败面，**不在本轮授权范围内**，已登记为待办。
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

from fastapi.testclient import TestClient

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from aplus.reports import (
    OUTCOME_UPSTREAM_UNAVAILABLE,  # noqa: E402
    OUTCOME_DATA_MISSING,
    OUTCOME_FAILED,
    OUTCOME_NOT_A_WORKDAY,
    OUTCOME_OK,
    OUTCOME_PARTIAL_COVERAGE,
    RUN_STATUS_OUTCOMES,
    build_run_status,
    outcome_for_run_day,
    read_run_status,
)
from aplus.trading_calendar import (  # noqa: E402
    RUN_DAY_TRADING,
    RUN_DAY_WEEKEND,
    RUN_DAY_WORKDAY_NOT_IN_STORE,
)
from aplus.webapp import create_app  # noqa: E402

_RD = _ROOT / "scripts" / "run_daily.py"


# ── ① 判决 → 结局：三个值必须**互不相同** ────────────────────────────

def test_the_three_run_day_verdicts_map_to_three_distinct_outcomes():
    """★ 「不是工作日」与「工作日但没数据」必须**分得开**。

    合成一个它们相同的实现（都返回 `skipped`）⇒ 这条立刻红。
    """
    outcomes = {v: outcome_for_run_day(v)[0] for v in
                (RUN_DAY_TRADING, RUN_DAY_WEEKEND, RUN_DAY_WORKDAY_NOT_IN_STORE)}
    assert outcomes[RUN_DAY_TRADING] == OUTCOME_OK
    assert outcomes[RUN_DAY_WEEKEND] == OUTCOME_NOT_A_WORKDAY
    assert outcomes[RUN_DAY_WORKDAY_NOT_IN_STORE] == OUTCOME_DATA_MISSING
    assert len(set(outcomes.values())) == 3, f"三个判决落到了同一个结局：{outcomes}"


def test_the_reason_says_what_actually_happened():
    """★ 理由必须**说人话**，且两句各说各的事。

    周末那句必须说「本来就不出单」（否则用户以为平台坏了）；
    工作日缺数据那句必须说「今天没出单」+「要查的是采集」
    （否则用户去查 cron，而真正坏的是采集）。
    """
    _, weekend = outcome_for_run_day(RUN_DAY_WEEKEND)
    assert "不出单" in weekend and "不是故障" in weekend, weekend
    assert "采集" not in weekend, "周末不该指向采集（它不是故障）"

    _, missing = outcome_for_run_day(RUN_DAY_WORKDAY_NOT_IN_STORE)
    assert "没出单" in missing, missing
    assert "采集" in missing, "必须说清要查的是采集"
    assert "法定节假日" in missing, (
        "必须把残余歧义写在脸上 —— 仓里没有交易所日历，"
        "法定节假日与采集失败在「今天不在库里」这一件事上同形")


# ── ② 产物形状：两个量并排，不合并 ──────────────────────────────────

def test_build_run_status_keeps_both_quantities():
    """★ 新字段是**加**，不是**改**：`date`/`status`/`steps` 原义不动。"""
    st = build_run_status(attempted_date="2026-10-20", outcome=OUTCOME_OK,
                          outcome_reason="全部步骤跑完，名单已发布。", status="ok",
                          steps={"predict": "ok"},
                          updated_at="2026-10-20 20:00:00")
    for k in ("date", "status", "steps", "updated_at",
              "attempted_date", "outcome", "outcome_reason"):
        assert k in st, f"缺 {k}"
    assert st["date"] == "2026-10-20" and st["status"] == "ok"
    assert st["steps"] == {"predict": "ok"}
    assert st["attempted_date"] == "2026-10-20"
    assert st["outcome"] == OUTCOME_OK
    assert st["outcome_reason"]


def test_an_unknown_outcome_is_rejected_loudly():
    """写错一个值**必须当场报错** —— 拼错的 outcome 落到产物里，
    前端会拿它去走一条**不存在的分支**，而界面照常渲染（形状 23 的方向）。"""
    try:
        build_run_status(attempted_date="2026-10-20", outcome="success",
                         outcome_reason="x", status="ok", steps={}, updated_at="x")
    except ValueError:
        return
    raise AssertionError("拼错的 outcome 被静默写进了产物")


def test_the_outcome_vocabulary_is_closed():
    """结局的取值是**闭集** —— 前端按它分支，多一个值就多一条没被审过的分支。

    ⚠️ 第五态 `partial_coverage`（2026-10-20 加）也在闭集里：它是
    「工作日、仓里**部分**股票有今天的数据」—— 与 `data_missing`
    （**完全**没数据 ⇒ 采集全挂）是**两件事**，排查方向不同
    （前者查「采了一半」，后者查「全挂」）。合成一个值 ⇒
    界面会说「仓里没有这一天的数据」⇒ **事实错误**。

    ⚠️ 第六态 `upstream_unavailable`（2026-10-21 加）：「**数据源不可用**」
    （网络/服务器挂了 ⇒ 该重试）—— 与 `data_missing`（**结果**：仓里没这一天的数据，
    可能是法定节假日）的**应对相反** ⇒ 合成一个值 ⇒ 界面会说「仓里没数据」
    ⇒ 而真因是「数据源挂了，晚点重试」⇒ **排查方向相反**。
    ⇒ 它是**闭集** ⇒ 加一态就要改这一行（不许悄悄多出第四个第五个值）。
    """
    assert set(RUN_STATUS_OUTCOMES) == {
        OUTCOME_OK, OUTCOME_NOT_A_WORKDAY, OUTCOME_DATA_MISSING, OUTCOME_FAILED,
        OUTCOME_PARTIAL_COVERAGE, OUTCOME_UPSTREAM_UNAVAILABLE}


# ── ③ 接线：fail-closed 那条路必须真的写出新字段 ─────────────────────

class _FakeRun:
    def log(self, *a, **kw):      # noqa: D102 - 只为让 run_day_gate 能跑
        pass


def test_the_fail_closed_path_writes_the_outcome(tmp_path):
    """★ 闸门拦下发布时，状态文件里必须**说得清没出单的原因**。

    走**真的** `run_day_gate`（不是重写一遍它的逻辑）——
    重写一遍就是第 137 层那种「测试里重写生产逻辑」。
    """
    import scripts.run_daily as rd  # noqa: PLC0415

    # 2026-10-24 是周六 ⇒ 判决必然是 WEEKEND（与仓里日历内容无关）
    rc, verdict = rd.run_day_gate("2026-10-24", str(tmp_path), _FakeRun())
    assert verdict == RUN_DAY_WEEKEND
    assert rc == 0, "周末是正常的一天（不是失败）"

    st = read_run_status(tmp_path / "reports")
    assert st is not None, "闸门拦下发布时必须留下状态文件"
    assert st["outcome"] == OUTCOME_NOT_A_WORKDAY
    assert st["attempted_date"] == "2026-10-24"
    assert "不出单" in st["outcome_reason"]
    # 旧字段不许被改义
    assert st["status"] == "skipped"
    assert st["steps"] == {"run_day_gate": RUN_DAY_WEEKEND}


def test_the_fail_closed_path_on_a_workday_says_data_missing(tmp_path):
    """★★ 「今天是工作日但仓里没数据」= **采集失败** ⇒ 必须说清「今天没出单」。

    这是 2026-10-20 18:30 那次**真实发生**的形态：采集失败 ⇒ fail-closed
    **一份名单都没出**，而落盘的状态只说「skipped + run_day_gate」。
    ⇒ 用户看到的必须能回答两个问题：**今天有没有出单？该查什么？**
    —— 答案：**没出单；查采集**（不是 cron 没跑）。

    2026-10-21 是**周三**且不在仓里（仓内只到 10-20）⇒ 判决必然是
    `WORKDAY_NOT_IN_STORE` ⇒ 与日历内容无关。
    """
    import scripts.run_daily as rd  # noqa: PLC0415

    rc, verdict = rd.run_day_gate("2026-10-21", str(tmp_path), _FakeRun())
    assert verdict == RUN_DAY_WORKDAY_NOT_IN_STORE
    assert rc == 2, "「工作日不在仓里」必须有人来看（rc=2）"

    st = read_run_status(tmp_path / "reports")
    assert st is not None
    assert st["outcome"] == OUTCOME_DATA_MISSING, (
        f"outcome={st['outcome']!r} —— 工作日但没数据必须落成 data_missing，"
        "不能与「不是工作日」混成同一个值")
    assert st["outcome"] != OUTCOME_NOT_A_WORKDAY, "两件事必须能分开"
    assert "没出单" in st["outcome_reason"], st["outcome_reason"]
    assert "采集" in st["outcome_reason"], (
        "必须说清**要查的是采集**（不是 cron 没跑）—— "
        f"实际理由：{st['outcome_reason']}")


def test_the_pipeline_writes_the_outcome_through_the_same_builder():
    """★ 接线（AST）：每一处 `write_run_status(...)` 的载荷都必须**由
    `build_run_status(...)` 组装**，且带上那三个新字段。

    ⚠️ 只钉「产物里有这个键」**不够** —— 产物层守卫读的是上一次落盘的文件，
    对当前代码零覆盖（第 96 层）⇒ 必须另有一条**接线**守卫。

    ⚠️ 第一版写成了「`write_run_status` 必须有 `outcome=` 实参」⇒ **当场红**，
    而红的原因是**守卫自己量错了东西**：`outcome` 是**载荷字典里**的键，
    不是 `write_run_status` 的实参。载荷是位置实参（`write_run_status(dir, {...})`），
    键在 `build_run_status(...)` 里面。
    ⇒ 真正的不变式是「载荷来自那一个组装器」⇒ 改成钉**载荷表达式本身**。
    """
    src = _RD.read_text(encoding="utf-8")
    tree = ast.parse(src)
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "write_run_status"]
    assert calls, "找不到 write_run_status(...) 调用 —— 守卫需要重新评估"
    for call in calls:
        # `write_run_status(reports_dir, status)` ⇒ 载荷是**第二个**位置实参
        assert len(call.args) >= 2, "write_run_status 必须有载荷"
        payload = call.args[1]
        assert isinstance(payload, ast.Call) and getattr(payload.func, "id", "") == "build_run_status", (
            "write_run_status 的载荷不是 build_run_status(...) ⇒ "
            f"键集有了第二处定义（{ast.unparse(payload)[:90]}）—— "
            "两份必须同步的东西中间没有锁")
        kwargs = {kw.arg for kw in payload.keywords}
        missing = {"attempted_date", "outcome", "outcome_reason"} - kwargs
        assert not missing, (
            f"build_run_status 少了实参 {sorted(missing)} ⇒ "
            "这次尝试的结局没落盘（界面只能显示「部分完成 · run_day_gate」）")


# ── ④ API：`/api/run-status` 必须把新字段递出去 ─────────────────────

def test_the_api_carries_the_new_fields(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "run_status.json").write_text(json.dumps({
        "date": "2026-10-20", "status": "skipped",
        "steps": {"run_day_gate": RUN_DAY_WORKDAY_NOT_IN_STORE},
        "updated_at": "2026-10-20 18:31:36",
        "attempted_date": "2026-10-20",
        "outcome": OUTCOME_DATA_MISSING,
        "outcome_reason": "今天是工作日，但仓里没有这一天的数据 ⇒ 今天没出单。",
    }, ensure_ascii=False), encoding="utf-8")
    body = TestClient(create_app(tmp_path, llm=object())).get("/api/run-status").json()
    for k in ("attempted_date", "outcome", "outcome_reason"):
        assert k in body, f"/api/run-status 没递 {k}"
    assert body["outcome"] == OUTCOME_DATA_MISSING
    assert body["attempted_date"] == "2026-10-20"

"""因子表完整性闸门：**坏因子不得进入推荐名单**。

## 事故经过（2026-10-11 生产事故）

`data/run_daily.log` 里留着这一行：

    601607.SH 上海医药 score=22.53 mom20=903.72%
    002261.SZ 拓维信息 score=21.45 mom20=839.55%
    000626.SZ 远大控股 score=18.60 mom20=745.63%

20 个交易日涨 **903%** 在真实市场不存在。逐只复算证实：这三只股票的
因子表当时是坏的（坏因子 → 后复权价跳变 → 动量因子爆炸），它们因此
霸占「预测涨 Top10」并进了用户的当日建仓清单。

同一批坏数据在 09:07 的体检里被抓到，09:08–09:41 全量修复。修复之后
逐只复算，mom20 最大值回落到 152.8%，>200% 的股票 0 只——**数据本身没问题，
是输入有问题**。

## 原设计错在哪

当时的 `step_factor_audit` 写在流水线的**最后**，而且注释里明确写着
「体检**不阻断**主流程（因子坏了也要出预测）」。

这是拿数据完整性换可用性：在数据坏掉时**照样出**一份基于坏数据的推荐，
用户按它建仓 5 万块。可用性的正确含义是「服务能响应」，不是「服务能
在输入损坏时编一个答案出来」。

## 现在的规则

因子表损坏率超过阈值 → **不发布推荐**，整轮标记失败，并给出可执行的下一步
（跑 `scripts/repair_factors.py --all`）。

阈值取 1%，与原审计一致：低于它就是正常的数据噪声（实测长期值在 0.06%
附近，且全部来自借壳上市首行判据的已知误报），超过它说明数据层整体出了问题。
"""
from __future__ import annotations

import pytest

from scripts.run_daily import (CORRUPT_GATE_MAX_BAD, CORRUPT_GATE_RATIO,
                               factor_gate_blocks)

# ── 闸门判定 ────────────────────────────────────────────────────────────────

def test_systemic_corruption_blocks_recommendation():
    """损坏达到**系统性**量级 → 不得发布推荐。

    取数链路整体故障时（批量污染、源站改接口），没有任何单点数据闸兜得住。
    """
    assert factor_gate_blocks(CORRUPT_GATE_MAX_BAD + 1, 5447) is True
    assert factor_gate_blocks(500, 5447) is True


def test_handful_of_corruption_is_neutralized_downstream():
    """少数几只损坏**不**拦——因为它们已在两层数据闸上被中和。

    这不是放水，是前提条件的成立：`attach_factor` 作废基准未知的时期，
    `MAX_PLAUSIBLE_RETURN` 作废 ±40% 之外的收益 → mom20 变 NaN →
    该股退出横截面。实测 `302132.SZ`（+462.51% 虚高收益）就是被这样中和的。

    对照 2026-10-11 的教训：闸门「1 只即停」上线当天就被两只**误报**顶住，
    天天出不了名单。一个天天误报的闸门等于没有闸门。
    """
    assert factor_gate_blocks(1, 5447) is False
    assert factor_gate_blocks(CORRUPT_GATE_MAX_BAD, 5447) is False


def test_neutralization_layers_actually_exist():
    """本测试的前提：中和层必须真的在代码里，不能只是文档里的说法。"""
    from aplus.adjust import UNKNOWN_SCALE_MIN_FACTOR, is_scale_neutralized
    from aplus.backtest import MAX_PLAUSIBLE_RETURN as TRADE_GUARD
    from aplus.panel import MAX_PLAUSIBLE_RETURN as FACTOR_GUARD

    assert TRADE_GUARD == FACTOR_GUARD == 0.40
    assert UNKNOWN_SCALE_MIN_FACTOR > 1.0
    assert callable(is_scale_neutralized)


def test_no_corruption_does_not_block():
    assert factor_gate_blocks(0, 5447) is False


def test_empty_universe_does_not_silently_allow():
    """因子表一张都没有时必须挡住，而不是当成「0 损坏」放行。

    分母为 0 时 `0/0` 若按 0 处理，等于把「数据层整个空了」读成「一切正常」。
    """
    assert factor_gate_blocks(0, 0) is True
    assert factor_gate_blocks(5, 0) is True


def test_ratio_and_count_are_two_separate_concepts():
    """比例阈值只用于**日志分级**（是否需要全量修复），不参与放行判断。

    两个概念混成一个是这类闸门最常见的退化：调阈值调到「刚好不拦」为止，
    然后既失去了停机能力，也失去了告警能力。
    """
    assert CORRUPT_GATE_RATIO == 0.01
    assert 0 < CORRUPT_GATE_RATIO < 0.1
    assert 0 < CORRUPT_GATE_MAX_BAD <= 20


# ── 变异验证：闸门必须真的挡得住 ────────────────────────────────────────────

def test_mutation_would_be_caught(monkeypatch):
    """把闸门改坏必须让原判定变红——否则这些断言是摆设。

    注意必须走 `rd.factor_gate_blocks` 动态取：模块顶部 `from ... import`
    拿到的是**当时**的函数对象，之后 monkeypatch 模块属性不会再影响它，
    那样测出来的「变异体放行了」是假象。
    """
    import scripts.run_daily as rd

    monkeypatch.setattr(rd, "factor_gate_blocks", lambda bad, total: False)
    assert rd.factor_gate_blocks(500, 5447) is False, (
        "变异体确实把闸门放行了——证明原判定有鉴别力"
    )
    # 原判定（monkeypatch 之前的真身）对同一输入必须挡住
    assert factor_gate_blocks(500, 5447) is True


# ── 流程顺序 ────────────────────────────────────────────────────────────────

def test_factor_audit_runs_before_predict():
    """审计必须排在预测**之前**。

    「先发名单、后体检」意味着体检发现问题时，那份名单已经发出去了。
    2026-10-11 事故正是这个顺序：08:53 用坏因子发出名单，09:07 才体检出来。
    """
    import inspect

    import scripts.run_daily as rd

    src = inspect.getsource(rd._pipeline)
    i_audit = src.find("_run_audit(")
    i_predict = src.find('run_step("predict"')
    assert i_audit != -1, "_pipeline 必须先调 _run_audit"
    assert i_predict != -1, "_pipeline 必须有 predict 步骤"
    assert i_audit < i_predict, (
        "factor_audit 排在 predict 之后 —— 这正是 2026-10-11 事故的结构"
    )


def test_audit_failure_also_blocks(monkeypatch):
    """体检**自身抛异常**时必须照样挡住预测。

    「拿不到体检结论」不等于「数据没问题」。若此时放行，
    就等于把「不知道」当成「好」——这正是这类系统最常见的失效方式。
    """
    import scripts.run_daily as rd

    monkeypatch.setattr(rd, "step_factor_audit",
                        lambda root: (_ for _ in ()).throw(RuntimeError("体检炸了")))

    steps: dict = {}

    def fake_run_step(name, fn):
        try:
            fn()
            steps[name] = "ok"
        except Exception as exc:  # noqa: BLE001
            steps[name] = f"failed: {exc!r}"

    box = rd._run_audit("data", fake_run_step, steps)
    assert box["error"] is not None
    assert rd.factor_gate_blocks(box["bad"], box["usable"]) is True


def test_predict_is_wired_to_the_gate():
    """预测步骤必须真的读闸门结果，而不是只在文档里说说。"""
    import inspect

    import scripts.run_daily as rd

    src = inspect.getsource(rd._pipeline)
    assert "factor_gate_blocks(audit" in src, "predict 的执行条件必须挂在闸门上"
    assert "blocked: 因子表损坏" in src, "挡住时必须留下可读的状态，而不是静默跳过"


# ── 回填并发：数据正在改写时不得出名单 ──────────────────────────────────────

# `build_predictions` 会按 `DEFAULT_WEIGHTS` 取列，桩横截面必须带全，
# 否则测试会在 KeyError 上失败、而真正要测的「是否执行」根本走不到。
_CS_COLS = ["code", "name", "volume", "isST", "listed_days", "last_date",
            "mom20", "rev5", "vol20", "vsur", "turn20", "ivol", "turnratio"]




def test_predict_skips_while_backfill_holds_the_lock(tmp_path):
    """回填持锁时不出预测——否则会读到写了一半的 parquet。

    `LocalStore._upsert` 用 `to_parquet` 直写（**非原子替换**），并发读
    可能命中半截文件。崩在一个坏文件上不如明确不发布，但必须留下日志。
    """
    import scripts.run_daily as rd
    from aplus.store import LocalStore

    data_root = tmp_path / "data"
    (data_root / "reports").mkdir(parents=True)
    (data_root / "bootstrap.lock").write_text("12345")
    store = LocalStore(data_root)
    reports = data_root / "reports"

    # 注意 patch 的是 rd 自己的命名空间：run_daily 顶部是
    # `from aplus.snapshot import build_cross_section`，引用已绑在模块里，
    # 改 aplus.snapshot 不起作用（这本身也是一处值得记住的绑定时机）。
    import pandas as pd

    called = []
    empty = pd.DataFrame(columns=_CS_COLS)
    orig = rd.build_cross_section

    def _stub(*a, **k):
        called.append(1)
        return empty
    rd.build_cross_section = _stub
    try:
        rd.step_predict_and_allocate(store, reports, "2026-10-11", 50000, {})
    finally:
        rd.build_cross_section = orig

    assert not called, "回填进行中却仍然构建了横截面"
    assert not list(reports.glob("*.parquet")), "回填进行中却写了预测文件"


def test_predict_runs_when_no_lock(tmp_path, monkeypatch):
    """无锁时正常执行——这道防护不能把正常流程也挡住。"""
    import scripts.run_daily as rd
    from aplus.store import LocalStore

    data_root = tmp_path / "data"
    (data_root / "reports").mkdir(parents=True)
    store = LocalStore(data_root)

    import pandas as pd

    called = []
    empty = pd.DataFrame(columns=_CS_COLS)

    def _stub(*a, **k):
        called.append(1)
        return empty

    monkeypatch.setattr(rd, "build_cross_section", _stub)
    rd.step_predict_and_allocate(store, data_root / "reports",
                                 "2026-10-11", 50000, {})
    assert called, "没有锁却没执行预测——防护过头了"

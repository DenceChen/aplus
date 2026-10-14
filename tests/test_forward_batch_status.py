"""前瞻验证批次的可用性——**批次有效 ≠ 证据可引用**。

## 这个模块最该被钉住的三条

1. **证据作废不撤销批次。** 前瞻有效性看的是冻结之后的行情，
   不是它当初凭什么被选中。撤销会丢掉可能真的有东西的候选。
2. **「审计没看见」= 判不了，不等于没事。** 与污染审计同一原则：
   `unknown` 伪装成 `clean`，是最贵的误判。
3. **不破 `sealed_sha256`。** 哪怕本意是加注记，改封的动作本身就
   让「冻结」失去意义——那看起来就是作弊。

## embargo 不许被放松

最早可验证日 = 注册日 + embargo + 1。embargo 存在是为了避开
注册当日/次日的结算与复权时点噪声。
**放松它 = 把窗口推后 = 降低偷看难度**，与本项目的前瞻铁律冲突。
"""
import json
from pathlib import Path

import pytest

from scripts import forward_batch_status as F


# ── embargo：注册日之后，不是当天 ──────────────────────────────────

def test_the_window_starts_strictly_after_the_registration_day():
    """窗口必须**晚于**注册日。注册当日就可见 = 那天收盘已知，
    却声称「在看到之前冻结」，那是自欺。"""
    assert F.verdict_start("2026-10-14") > "2026-10-14"


def test_the_embargo_pushes_the_first_verifiable_day_out():
    d0 = F.verdict_start("2026-10-14", embargo=0)
    d2 = F.verdict_start("2026-10-14", embargo=2)
    assert d2 > d0
    assert d2 == "2026-10-17"


def test_a_tighter_embargo_must_not_make_verification_earlier():
    """**放松 embargo 会让判定更早到来**，也就是更容易。
    这是与前瞻铁律相反的方向，所以反向断言：embargo 越大越晚。"""
    days = [F.verdict_start("2026-10-14", embargo=e) for e in (0, 1, 2, 5)]
    assert days == sorted(days), f"embargo 与判定日必须同向，实际 {days}"


# ── 证据三档 ────────────────────────────────────────────────────────

def _rep(cutoff=1000):
    """一份 audit() 形状的替身。**字段必须与真输出一致**——
    少了 `fix_commit` 这类键，测试会报 KeyError 而不是断言失败，
    于是「测试红」和「实现坏」分不清。"""
    return {"cutoff_ts": cutoff, "fix_commit": "3c846ff",
            "contaminated": [{"report": "old.json", "mtime": 500,
                              "panel_market_mean": None}],
            "clean": [{"report": "new.json", "mtime": 2000,
                       "panel_market_mean": 0.00066}],
            "unverified": [{"report": "unknown.json", "mtime": 2000,
                            "panel_market_mean": None}]}


def test_a_report_the_audit_never_saw_is_unverified_not_clean():
    """审计清单里没有它 → **我不知道它干净还是脏**，这是第三档。
    若归入 clean，读者会以为「检查过了」。"""
    got = F.evidence_state(["never_seen.json"], _rep())
    assert got == {"never_seen.json": "unverified"}


def test_a_contaminated_report_and_a_clean_one_get_different_verdicts():
    got = F.evidence_state(["old.json", "new.json"], _rep())
    assert got == {"old.json": "contaminated", "new.json": "clean"}


def test_a_batch_is_valid_even_when_its_evidence_is_contaminated():
    """**这条是全模块的立场。** 前瞻有效性不依赖历史 t；
    撤销批次会丢掉可能真的有东西的候选，且破坏「增量注册」的路子。"""
    reg = [{"registered_on": "2026-10-12", "evidence": "old.json",
            "candidates": [{"weights": {}}], "sealed_sha256": "ab" * 32}]
    rep = F.status(registry=reg, rep=_rep())
    b = rep["batches"][0]
    assert b["evidence_verdict"] == "contaminated"
    assert b["batch_valid"] is True


# ── seal 必须在状态里显式可见 ──────────────────────────────────────

def test_a_missing_seal_is_reported_as_absent_not_as_an_empty_hash():
    """批次1/2 本来就没有 `sealed_sha256`，只有批次3 有。
    打印成空串会让人分不清「无 seal」与「seal 是空的」——
    **两种情况的处置完全不同**（前者更弱）。"""
    reg = [{"registered_on": "2026-10-12", "evidence": "old.json",
            "candidates": [{}]}]
    b = F.status(registry=reg, rep=_rep())["batches"][0]
    assert b["sealed_sha256"] is None, "无 seal 必须报 None，不是空串"


def test_an_existing_seal_is_truncated_but_still_identifiable():
    reg = [{"registered_on": "2026-10-12", "evidence": "old.json",
            "candidates": [{}], "sealed_sha256": "b061871918" + "0" * 54}]
    b = F.status(registry=reg, rep=_rep())["batches"][0]
    assert b["sealed_sha256"].startswith("b061871918")
    assert len(b["sealed_sha256"]) == 12


# ── 观测数：0 是个有意义的数 ───────────────────────────────────────

def test_zero_forward_observations_is_reported_as_zero(tmp_path, monkeypatch):
    """0 观测不是「没数据」，是「还没到期，任何判定都太早」。
    把它报成 None 或报错都会让人误以为可以开始下结论了。"""
    res = tmp_path / "forward_results.json"
    res.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(F, "RESULTS", res)
    rep = F.status(registry=[], rep=_rep())
    assert rep["n_forward_observations"] == 0

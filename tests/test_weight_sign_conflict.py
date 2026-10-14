"""权重符号 vs 实测 IC：这条判定必须在**证据缺失时沉默**。

## 为什么需要它

2026-10-12 的 IC 实测发现，现任 `{mom20:1.0, rev5:0.3, vol20:0.5, vsur:0.5}`
给三个**负 IC 因子挂了正权重**。判决板原来只能说「这组权重已被证伪」，
那是**结果**；现在能说「三个因子的方向和实测预测力相反」，那是**原因**。

## 三条防线，全部关于「沉默」

1. **没有 IC 报告 → `unknown`，不是「0 处冲突」**。
   「0 处冲突」会被读成「查过了，都对」——这正是
   「安静的错误输出 > 崩溃」最典型的形态：报告看起来完整。
2. **因子没有 IC 数据 → 列入 `unmeasurable`，不计入冲突数**。
   把「没测过」算成「测过且正确」，会把 3/4 的合格率说成 4/4。
3. **权重为 0 的因子不算冲突**。`composite_score` 里它不参与合成，
   谈不上方向对错。

## 「冲突」不等于「翻过来就能赚」

负 IC 的因子挂负权重**可能**改善，也**可能**只是把另一个偏差换上来。
本函数只回答「方向与实测是否相反」，不回答「反过来是否可行」。
后者是 `scripts/ic_guided_race.py` 的事，且必须过七道闸。
"""
import json

import pytest

from aplus.factor_ic import MIN_OBS, weight_sign_conflicts


def _ic_report(tmp_path, rows, **extra):
    """写一份最小可用的 factor_ic 报告。"""
    payload = {"kind": "factor_ic", "table": rows}
    payload.update(extra)
    path = tmp_path / "factor_ic_7y.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _row(factor, ic, n_days=MIN_OBS + 10):
    return {"factor": factor, "window": "lag1_real", "all": ic,
            "all_t": ic / 0.01 if ic else 0.0, "n_days": n_days,
            "block": 1}


# ── 基本判定 ─────────────────────────────────────────────────────

def test_negative_ic_with_positive_weight_is_a_conflict(tmp_path):
    _ic_report(tmp_path, [_row("mom20", -0.036)])
    got = weight_sign_conflicts(tmp_path, {"mom20": 1.0})
    assert got["conflicts"] == ["mom20"]


def test_negative_ic_with_negative_weight_agrees(tmp_path):
    _ic_report(tmp_path, [_row("mom20", -0.036)])
    got = weight_sign_conflicts(tmp_path, {"mom20": -1.0})
    assert got["conflicts"] == []
    assert got["agrees"] == ["mom20"]


def test_zero_ic_factor_is_unmeasurable_not_agreement(tmp_path):
    """IC 恰为 0 → 没有方向可言。列入未核对，**不得**算成「已确认正确」。

    配一个正常的因子一起，才能看出它是「没测」而不是「测了说对」。
    """
    _ic_report(tmp_path, [_row("mom20", 0.0), _row("rev5", 0.026)])
    got = weight_sign_conflicts(tmp_path, {"mom20": 1.0, "rev5": 1.0})
    assert got["unknown"] is False
    assert got["conflicts"] == []
    assert got["agrees"] == ["rev5"]
    assert got["unmeasurable"] == ["mom20"]


def test_zero_weight_factor_is_excluded_entirely(tmp_path):
    """权重 0 的因子不参与合成，谈不上方向对错，不该进任何一栏。

    而「全部权重都是 0」时结论仍然是 `unknown` 而不是「0 处冲突」——
    没东西可查 ≠ 查过了都对。
    """
    _ic_report(tmp_path, [_row("mom20", -0.036)])
    got = weight_sign_conflicts(tmp_path, {"mom20": 0.0})
    assert got["unknown"] is True
    assert got["conflicts"] is None and got["agrees"] is None
    assert got["checked"] == []


# ── 证据缺失必须沉默 ─────────────────────────────────────────────

def test_missing_report_is_unknown_not_zero_conflicts(tmp_path):
    """没有 IC 报告 → `unknown=True`，且 conflicts 必须是 None。

    返回 `[]` 会被读成「查过了，一处冲突都没有」——
    这是本模块最容易犯、也最难发现的错误。
    """
    got = weight_sign_conflicts(tmp_path, {"mom20": 1.0})
    assert got["unknown"] is True
    assert got["conflicts"] is None
    assert got["agrees"] is None
    assert got["unmeasurable"] is None
    assert "没有" in got["note"]


def test_corrupt_report_is_unknown_not_a_crash(tmp_path):
    """坏报告 → unknown，且不抛异常。名单照发，横幅照打，只是没有 IC 结论。"""
    (tmp_path / "factor_ic_7y.json").write_text("{ not json", encoding="utf-8")
    got = weight_sign_conflicts(tmp_path, {"mom20": 1.0})
    assert got["unknown"] is True


def test_wrong_window_rows_are_not_used(tmp_path):
    """只认 `lag1_real` 窗口。泄露窗口的符号是另一回事，混进来就污染判定。"""
    _ic_report(tmp_path, [{"factor": "mom20", "window": "lag0_leaky",
                           "all": 0.5, "n_days": 900, "block": 1}])
    got = weight_sign_conflicts(tmp_path, {"mom20": 1.0})
    assert got["unmeasurable"] == ["mom20"]      # 没找到真实窗口的行


def test_insufficient_days_makes_the_factor_unmeasurable(tmp_path):
    """样本不足的 IC 不参与判定——但要**列出来**，不能悄悄消失。

    配一个够样本的因子，验证混合情形：有结论，同时说明谁没参与。
    """
    _ic_report(tmp_path, [_row("mom20", -0.036, n_days=5), _row("rev5", 0.026)])
    got = weight_sign_conflicts(tmp_path, {"mom20": 1.0, "rev5": 1.0})
    assert got["unmeasurable"] == ["mom20"]
    assert got["conflicts"] == []
    assert got["agrees"] == ["rev5"]


def test_every_factor_unmeasurable_is_unknown_not_zero_conflicts(tmp_path):
    """全都测不出来 → 结论是「未知」，不是「0 处冲突」。

    这是本模块最关键的一条：返回空列表会让判决板显示
    「符号全部一致」——一个从未做过的结论。
    """
    _ic_report(tmp_path, [_row("mom20", -0.036, n_days=5)])
    got = weight_sign_conflicts(tmp_path, {"mom20": 1.0})
    assert got["unknown"] is True
    assert got["conflicts"] is None
    assert got["unmeasurable"] == ["mom20"]


def test_empty_weights_gives_unknown_with_empty_scope(tmp_path):
    _ic_report(tmp_path, [_row("mom20", -0.036)])
    got = weight_sign_conflicts(tmp_path, {})
    assert got["unknown"] is True
    assert got["checked"] == []


# ── 结论的措辞必须能直接给人看 ───────────────────────────────────

def test_note_states_direction_not_verdict(tmp_path):
    """note 只描述「方向相反」，不得暗示「反过来就能赚」。"""
    _ic_report(tmp_path, [_row("mom20", -0.036), _row("rev5", 0.026)])
    got = weight_sign_conflicts(tmp_path, {"mom20": 1.0, "rev5": 0.3})
    assert "mom20" in got["note"] and "rev5" in got["note"]
    assert "方向" in got["note"]


def test_report_window_is_carried_through(tmp_path):
    """把 IC 的测量窗口报出来——7.8 年和 3 个月不是同一个证据。"""
    _ic_report(tmp_path, [_row("mom20", -0.036)], window=["2019-01-02", "2026-10-12"])
    got = weight_sign_conflicts(tmp_path, {"mom20": 1.0})
    assert got["ic_window"] == ["2019-01-02", "2026-10-12"]


def test_ic_values_are_reported_for_audit(tmp_path):
    """判定要能被复核：每个因子的 IC 值必须一并带出。"""
    _ic_report(tmp_path, [_row("mom20", -0.036), _row("rev5", 0.026)])
    got = weight_sign_conflicts(tmp_path, {"mom20": 1.0, "rev5": 0.3})
    assert got["ic_values"]["mom20"] == pytest.approx(-0.036)
    assert got["ic_values"]["rev5"] == pytest.approx(0.026)
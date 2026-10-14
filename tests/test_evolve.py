"""自进化模块测试：TDD RED 先行。

设计（防过拟合护栏是硬约束）：
- objective：超额收益为主、命中率为辅的可解释复合目标
- maybe_promote 三道闸：最小样本量 / 最小改进幅度 / 晋级冷却期
- weights.json 持久化 + 进化日志留痕（每次评估结果都存，含被否决者）
"""
import pytest

from aplus.evolve import load_weights, maybe_promote, objective, save_weights


def _eval(weights, excess, hit, judged=1000):
    return {"weights": weights, "stats": {"up_excess_vs_market": excess, "overall_hit_rate": hit, "judged": judged}}


# ---------- 目标函数 ----------

def test_objective_orders_by_excess_then_hit():
    a = _eval({"mom20": 1.0}, excess=0.003, hit=0.50)
    b = _eval({"mom20": 0.5}, excess=0.001, hit=0.55)
    c = _eval({"mom20": 0.2}, excess=-0.002, hit=0.60)
    assert objective(a) > objective(b) > objective(c)


# ---------- 晋级护栏 ----------

def test_promote_when_challenger_beats_threshold():
    current = {"mom20": 1.0, "rev5": 0.3, "vol20": 0.5, "vsur": 0.5}
    champion = _eval(current, excess=0.000, hit=0.45, judged=1000)
    challenger = _eval({"mom20": 1.2, "rev5": 0.1, "vol20": 0.3, "vsur": 0.3}, excess=0.004, hit=0.52, judged=1000)
    result = maybe_promote(champion, [challenger, champion], last_promotion_date="2026-09-01", today="2026-10-10")
    assert result.promoted is True
    assert result.new_weights == challenger["weights"]
    assert "超额" in result.reason or "excess" in result.reason.lower()


def test_refuse_small_improvement():
    current = {"mom20": 1.0}
    champion = _eval(current, excess=0.002, hit=0.50, judged=1000)
    marginal = _eval({"mom20": 1.1}, excess=0.0021, hit=0.501, judged=1000)
    result = maybe_promote(champion, [marginal], last_promotion_date="2026-09-01", today="2026-10-10")
    assert result.promoted is False


def test_refuse_insufficient_sample():
    current = {"mom20": 1.0}
    champion = _eval(current, excess=0.000, hit=0.45, judged=1000)
    small = _eval({"mom20": 1.3}, excess=0.01, hit=0.60, judged=100)  # 样本太小
    result = maybe_promote(champion, [small], last_promotion_date="2026-09-01", today="2026-10-10")
    assert result.promoted is False
    assert "样本" in result.reason


def test_cooldown_blocks_promotion():
    current = {"mom20": 1.0}
    champion = _eval(current, excess=0.000, hit=0.45, judged=1000)
    better = _eval({"mom20": 1.3}, excess=0.005, hit=0.55, judged=1000)
    result = maybe_promote(champion, [better], last_promotion_date="2026-10-05", today="2026-10-10")
    assert result.promoted is False
    assert "冷却" in result.reason


# ---------- 持久化 ----------

def test_weights_roundtrip_with_evidence(tmp_path):
    weights = {"mom20": 1.2, "rev5": 0.1, "vol20": 0.3, "vsur": 0.3}
    save_weights(tmp_path, weights, evidence={"objective": 0.9, "promoted_on": "2026-10-10"})
    loaded = load_weights(tmp_path)
    assert loaded["weights"] == weights
    assert loaded["evidence"]["objective"] == 0.9


def test_load_weights_missing_returns_default(tmp_path):
    from aplus.factor import DEFAULT_WEIGHTS

    loaded = load_weights(tmp_path)
    assert loaded["weights"] == DEFAULT_WEIGHTS
    assert loaded["evidence"] is None

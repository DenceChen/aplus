"""资产配置模块测试：TDD RED 先行。

设计（PLATFORM.md §4，保守规则型）：
- 本金 5 万；单票 ≤ 10%；最多 5 只；权益仓位 ≤ 50%；现金 ≥ 50% - 缓冲
- 只配「预测涨」名单；「预测跌」名单为回避清单
- 有现有持仓时输出调仓差异（保留/新增/清仓）
- 风控线：单票止损 -8%、组合回撤 -10% 强制减半——作为注记随报告输出
"""
import pandas as pd
import pytest

from aplus.alloc import AllocationPlan, build_allocation


def _ups(n=10, base_score=1.0):
    return pd.DataFrame({
        "code": [f"60000{i}.SH" for i in range(n)],
        "name": [f"股票{i}" for i in range(n)],
        "direction": ["up"] * n,
        "score": [base_score + i * 0.1 for i in range(n)],
    })


def test_full_capital_allocation_rules():
    plan = build_allocation(_ups(10), capital=50_000)
    assert isinstance(plan, AllocationPlan)
    assert len(plan.positions) == 5                    # 最多 5 只
    assert all(p["weight"] == pytest.approx(0.10) for p in plan.positions)  # 单票 10%
    assert sum(p["weight"] for p in plan.positions) == pytest.approx(0.50)  # 权益 ≤ 50%
    assert plan.cash_weight == pytest.approx(0.50)
    assert plan.positions[0]["amount"] == pytest.approx(5_000)  # 5 万 × 10%


def test_fewer_candidates_allocate_less():
    plan = build_allocation(_ups(3), capital=50_000)
    assert len(plan.positions) == 3
    assert plan.cash_weight == pytest.approx(0.70)     # 现金 = 1 - 3×10%


def test_score_threshold_filters_weak_candidates():
    ups = _ups(10)
    ups.loc[0, "score"] = -2.0  # 最高名次里塞一个低分（模拟分数混布）
    plan = build_allocation(ups, capital=50_000, score_threshold=0.0)
    # 持仓里不含 score < 0 的票
    assert all(p["score"] >= 0.0 for p in plan.positions)


def test_current_positions_produce_rebalance_diff():
    ups = _ups(5)
    current = [{"code": "600000.SH", "weight": 0.10}, {"code": "688888.SH", "weight": 0.10}]
    plan = build_allocation(ups, capital=50_000, current_positions=current)
    kept = {p["action"] for p in plan.rebalance if p["action"] == "keep"}
    added = {p["code"] for p in plan.rebalance if p["action"] == "add"}
    removed = {p["code"] for p in plan.rebalance if p["action"] == "remove"}
    assert "688888.SH" in removed       # 不在新名单 → 清仓
    assert "600001.SH" in added         # 新名单补足
    assert kept                          # 有保留动作
    assert len(plan.positions) == 5


def test_risk_notes_attached():
    plan = build_allocation(_ups(10), capital=50_000)
    notes = " ".join(plan.risk_notes)
    assert "-8%" in notes      # 单票止损线
    assert "-10%" in notes     # 组合回撤线
    assert "20%" in notes      # 年化目标与风险提示


def test_zero_candidates_all_cash():
    plan = build_allocation(_ups(0), capital=50_000)
    assert plan.positions == []
    assert plan.cash_weight == pytest.approx(1.0)

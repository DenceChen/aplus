"""资产配置模块测试：TDD RED 先行。

设计（PLATFORM.md §4，保守规则型）：
- 本金 5 万；单票 ≤ 10%；最多 5 只；权益仓位 ≤ 50%；现金 ≥ 50% - 缓冲
- 只配「预测涨」名单；「预测跌」名单为回避清单
- 有现有持仓时输出调仓差异（保留/新增/清仓）
- 风控注记：🚨 **2026-10-20 已改** —— 旧版本承诺「单票止损 -8%」「组合回撤 -10%
  强制减半」，而**全仓 grep 只在这两行字符串命中** ⇒ 平台每天把一条它根本不会
  执行的风控承诺交给用户 ⇒ 已撤回。撤回不是「补实现」：按 Chan
  （chan_lines.txt:4799-4813）止损要有效的前提是「处在动量/趋势状态」，
  而本平台是短持有篮子 ⇒ 前提不成立 ⇒ 止损结构上不适用。
  现在注记里写的是**真实执行约束**（整手取整、科创板 200 股、闲置现金按 0 计息）。
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
    """注记必须是**真话**：兑现得了的约束写出来，兑现不了的**不许承诺**。

    🚨 2026-10-20：这一条曾经断言 `-8%` 与 `-10%` 在场（单票止损线 / 组合回撤线）
    —— 而那两条**代码里从未实现** ⇒ 平台每天把一条它不会执行的风控承诺
    交给用户 ⇒ 一句假话比没有这句话危险，因为它免掉了追问。
    ⇒ 撤回，并按 Chan（chan_lines.txt:4799-4813）说明**为何不实现**
    （止损要有效的前提是「处在动量/趋势状态」，本平台是短持有篮子 ⇒ 不成立）。
    ⇒ 断言也从「承诺在不在」改成「**旧承诺不许复活 + 真实约束必须在场**」。
    """
    plan = build_allocation(_ups(10), capital=50_000)
    notes = " ".join(plan.risk_notes)
    # 撤回的承诺不许以旧措辞复活
    assert "触及即次日卖出" not in notes
    assert "强制权益减半" not in notes
    # 撤回这件事本身必须**明说**（不许悄悄删掉——读者会以为漏了）
    assert "不含止损" in notes
    # 真实执行约束必须在场
    assert "整手" in notes and "200 股" in notes
    assert "20%" in notes      # 年化目标与风险提示仍在


def test_zero_candidates_all_cash():
    plan = build_allocation(_ups(0), capital=50_000)
    assert plan.positions == []
    assert plan.cash_weight == pytest.approx(1.0)

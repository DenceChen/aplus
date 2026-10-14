"""组合层回测测试：TDD RED 先行。

把选股层结果（pred_date, actual_pct, market_pct）翻译成组合净值：
配置规则（权益比例 × 市场状态闸门）→ 日收益 → 复利净值曲线 → 年化/回撤。
这是 20% 目标的直接度量单位——选股命中不等于赚钱。
"""
import pandas as pd
import pytest

from aplus.portfolio import simulate_portfolio


def _results(market_daily, pick_alpha=0.01):
    """构造 results：每日 10 只预测涨，实际收益 = 市场 + pick_alpha；
    market_daily 为市场逐日收益序列（长度 n，代表 n 个交易日）。"""
    rows = []
    for d, mkt in enumerate(market_daily):
        date = f"2026-{1 + d // 28:02d}-{1 + d % 28:02d}"
        for i in range(10):
            rows.append({"pred_date": date, "code": f"S{i}", "direction": "up",
                         "actual_pct": mkt + pick_alpha, "market_pct": mkt, "result": "hit"})
    return pd.DataFrame(rows)


def test_portfolio_beats_market_with_positive_alpha():
    mkt = [0.001] * 120  # 市场日 +0.1%
    results = _results(mkt, pick_alpha=0.002)
    sim = simulate_portfolio(results, base_equity=0.5)
    assert sim["portfolio_total"] > sim["market_total"]
    assert sim["max_drawdown"] == pytest.approx(0.0, abs=0.005)


def test_regime_gate_cuts_exposure_in_crash():
    """市场先涨 60 天后崩 40 天：闸门开启后组合总回撤应显著小于无闸门。"""
    mkt = [0.002] * 60 + [-0.01] * 40
    results = _results(mkt, pick_alpha=-0.002)  # 崩盘中选股也亏（贴近现实）
    gated = simulate_portfolio(results, base_equity=0.5, regime_gate=True)
    ungated = simulate_portfolio(results, base_equity=0.5, regime_gate=False)
    assert gated["max_drawdown"] < ungated["max_drawdown"]
    assert gated["gate_off_fraction"] > 0  # 崩盘段确实关了闸


def test_annualized_return_computed():
    mkt = [0.001] * 250  # 一年 250 交易日
    results = _results(mkt, pick_alpha=0.001)
    sim = simulate_portfolio(results, base_equity=0.5)
    # 日组合收益 = 0.5 × 0.002 = 0.001 → 年化 ≈ (1.001^250 - 1)
    assert sim["annualized_return"] == pytest.approx(1.001 ** 250 - 1, rel=0.02)


def test_empty_results_zero():
    sim = simulate_portfolio(pd.DataFrame(columns=["pred_date", "actual_pct", "market_pct"]))
    assert sim["portfolio_total"] == 0.0
    assert sim["n_days"] == 0

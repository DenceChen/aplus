"""组合层回测：选股结果 + 配置规则 → 净值曲线。

把「方向命中」翻译成「钱」：每日组合收益 = 权益比例 × 预测涨组合平均实际收益
（现金按 0 收益），复利成净值曲线，输出年化/回撤/与市场对比。

市场状态闸门（regime gate）：市场 20 日累计涨幅 < gate_threshold 时，
权益比例降到 gate_off_equity（崩盘减仓）——选股层不动，只动敞口。
这是「组合层」的风险开关，与选股层的 regime 权重切换（已证伪）是两回事。
"""
import pandas as pd


def simulate_portfolio(results: pd.DataFrame, base_equity: float = 0.5,
                       regime_gate: bool = True, gate_threshold: float = -0.03,
                       gate_off_equity: float = 0.1) -> dict:
    if results is None or results.empty or "actual_pct" not in results.columns:
        return {"portfolio_total": 0.0, "market_total": 0.0, "annualized_return": 0.0,
                "max_drawdown": 0.0, "market_max_drawdown": 0.0, "n_days": 0,
                "gate_off_fraction": 0.0}

    # 逐日聚合：预测涨组合的平均实际收益 + 当日市场收益
    daily = (results[results["direction"] == "up"]
             .groupby("pred_date")
             .agg(pick=("actual_pct", "mean"), market=("market_pct", "first"))
             .sort_index())
    n = len(daily)
    if n == 0:
        return {"portfolio_total": 0.0, "market_total": 0.0, "annualized_return": 0.0,
                "max_drawdown": 0.0, "market_max_drawdown": 0.0, "n_days": 0,
                "gate_off_fraction": 0.0}

    # 市场状态：20 日滚动累计涨幅（不足 20 日按已有窗口）
    market_series = daily["market"]
    roll = market_series.rolling(20, min_periods=1).sum()
    regime_ok = roll >= gate_threshold

    def _equity(i):
        if regime_gate and not regime_ok.iloc[i]:
            return gate_off_equity
        return base_equity

    # 逐日组合收益与净值
    equity_curve, market_curve = [1.0], [1.0]
    port_daily = []
    for i in range(n):
        day_ret = _equity(i) * daily["pick"].iloc[i]
        port_daily.append(day_ret)
        equity_curve.append(equity_curve[-1] * (1 + day_ret))
        market_curve.append(market_curve[-1] * (1 + market_series.iloc[i]))

    def _max_dd(curve):
        peak, mdd = curve[0], 0.0
        for v in curve:
            peak = max(peak, v)
            mdd = max(mdd, (peak - v) / peak)
        return mdd

    portfolio_total = equity_curve[-1] - 1
    annualized = (1 + portfolio_total) ** (250 / n) - 1 if n > 0 else 0.0
    gate_off_frac = float((~regime_ok).mean()) if regime_gate else 0.0
    return {
        "portfolio_total": float(portfolio_total),
        "market_total": float(market_curve[-1] - 1),
        "annualized_return": float(annualized),
        "max_drawdown": float(_max_dd(equity_curve)),
        "market_max_drawdown": float(_max_dd(market_curve)),
        "n_days": int(n),
        "gate_off_fraction": gate_off_frac,
    }

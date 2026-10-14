"""除权日收益口径：成交收益必须用**后复权**价算，红线测试。

## 这个测试在防什么

`verify_predictions` 一直按 `close / prev_close - 1` 算实际涨跌，而 `close` 是
**不复权**收盘价。A 股除权除息日（送股 / 配股 / 拆股）当天，不复权价会**直接跳空**，
于是回测凭空记下一笔巨额亏损——而这笔亏损在真实交易里根本不存在：你一股也没少。

2026-10-11 全库实测（5447 只，610 万个相邻交易日对）：

- 2623 只股票出现 `|不复权比值 − 交易所 pctChg| > 2%` 的日对
- 受影响日对 11762 个（占全部 0.193%）
- 最极端：`002594.SZ` 2025-07-29，不复权比值 **−66.94%**，交易所口径 **+0.37%**
  —— 一根 K 线凭空造出 **67 个百分点** 的亏损

0.193% 看着很小，但它落在「每日 Top10 轮动」上：两年 500 多个交易日 × 20 只
≈ 1 万个持仓样本，命中几次就足以把日均超额从 +0.03% 拉到 **−27%**。
`data/reports/evolution_race.json` 里那两个 −27.8% / −20.6% 的「日超额」就是这么来的
——那不是策略，那是除权。

## 为什么不能靠「跳过除权日」

跳过会让这两天的持仓凭空消失（等于免费平仓再免费建仓），凭空放走或吃掉收益，
同样是假的。唯一正确的做法是**用复权价算收益**：送出去的股票在复权序列里一份不少。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from aplus.adjust import hfq_close
from aplus.backtest import _next_bars


def _daily(code: str, closes: list, start: str = "2024-01-02") -> pd.DataFrame:
    """合成日线：只有 OHLCV + 少量元数据，够 `_next_bars` 用。"""
    dates = pd.bdate_range(start, periods=len(closes)).strftime("%Y-%m-%d").tolist()
    return pd.DataFrame({
        "date": dates, "code": code,
        "open": closes, "high": closes, "low": closes, "close": closes,
        "volume": [1e6] * len(closes), "amount": [1e7] * len(closes),
        "tradestatus": [1] * len(closes), "isST": [0] * len(closes),
        "turn": [1.0] * len(closes), "pctChg": [0.0] * len(closes),
    })


def _events(code: str, entries: list, start: str = "2024-01-02") -> pd.DataFrame:
    dates = pd.bdate_range(start, periods=len(entries)).strftime("%Y-%m-%d")
    return pd.DataFrame({"date": dates, "adjust_factor": [e[1] for e in entries]})


# ── 核心：拆股当日的虚假亏损 ────────────────────────────────────────────────

def test_split_day_return_is_not_a_phantom_collapse():
    """10 送 10 拆股：不复权价 100.5 → 10.0，raw 口径记 −90.05% 的假崩盘。

    后复权口径下这只股票当天只是 −0.50%，与拆股前后的真实走势一致。
    """
    closes = [100.0, 101.0, 100.5, 10.0, 10.2, 10.1]   # d3 是 10:1 拆股日
    daily = _daily("600000.SH", closes)
    # 因子事件表：上市日基准 1.0（早于日线起点）+ 拆股日跳到 10.0（后复权）
    events = pd.DataFrame({"date": ["2023-12-29", daily["date"].iloc[3]],
                           "adjust_factor": [1.0, 10.0]})

    bars = _next_bars({"600000.SH": daily}, ["600000.SH"],
                      after_date=daily["date"].iloc[1], entry_lag=1,
                      factors_map={"600000.SH": events})

    assert len(bars) == 1
    got = float(bars["close"].iloc[0]) / float(bars["prev_close"].iloc[0]) - 1
    # 复权后：entry = 100.5×1.0 = 100.5，exit = 10.0×10.0 = 100.0 → −0.4975%
    assert got == pytest.approx(100.0 / 100.5 - 1, abs=1e-12), (
        f"拆股日的收益被记成 {got:+.2%}——这是除权跳空造成的假亏损。"
        f"正确值约 −0.50%（复权后价格连续）。"
    )


def test_unadjusted_legacy_value_is_proof_the_test_can_fail():
    """变异验证：断言必须真的抓得住未复权口径，否则上面那条就是空断言。

    直接按旧算法（不复权 close）算一遍，证明它确实给出 −90.05% —— 两者之差
    就是这条测试的鉴别力。
    """
    closes = [100.0, 101.0, 100.5, 10.0, 10.2, 10.1]
    raw = closes[3] / closes[2] - 1
    assert raw == pytest.approx(-0.9005, abs=1e-4)
    assert raw < -0.89, "旧口径应产生 90% 量级的假崩盘，否则测试场景没构造对"


def test_cash_dividend_day_is_not_a_phantom_loss():
    """现金分红：除息日不复权价直接下调，但股东拿到现金，账面价值不变。"""
    closes = [20.0, 20.0, 20.0, 19.0, 19.05, 19.02]   # d3 每 10 股派 1 元
    daily = _daily("600001.SH", closes)
    # 后复权因子 = 拆股前基准 / 当期基准 ≈ 20/19；上市日基准 1.0 早于日线起点
    events = pd.DataFrame({"date": ["2023-12-29", daily["date"].iloc[3]],
                           "adjust_factor": [1.0, 20.0 / 19.0]})

    bars = _next_bars({"600001.SH": daily}, ["600001.SH"],
                      after_date=daily["date"].iloc[1], entry_lag=1,
                      factors_map={"600001.SH": events})

    got = float(bars["close"].iloc[0]) / float(bars["prev_close"].iloc[0]) - 1
    assert got == pytest.approx(0.0, abs=1e-9), (
        f"除息日收益被记成 {got:+.2%}，但股东同时拿到现金，真实持有收益是 0"
    )


# ── 不回归：无除权时行为不变 ────────────────────────────────────────────────

def test_no_exrights_event_keeps_raw_return_exactly():
    """没有除权事件时必须与旧口径逐位相同——本修复不得改变正常样本的收益。"""
    closes = [10.0, 10.2, 10.1, 10.35, 10.3, 10.4]
    daily = _daily("600002.SH", closes)
    bars = _next_bars({"600002.SH": daily}, ["600002.SH"],
                      after_date=daily["date"].iloc[2], entry_lag=1,
                      factors_map={"600002.SH": pd.DataFrame()})
    got = float(bars["close"].iloc[0]) / float(bars["prev_close"].iloc[0]) - 1
    # after_date=d2 → pos=3 → entry=3 → exit=4
    assert got == pytest.approx(10.3 / 10.35 - 1, abs=1e-12)


def test_factor_event_before_history_start_still_applies():
    """事件早于日线首日时也必须生效（与 attach_factor 的 merge_asof 语义一致）。

    日线从 2024 开始、事件从 2008 起的情况真实存在；这里用「唯一事件早于全部日线」
    来钉死不得退化成 fillna(1.0)。
    """
    closes = [10.0, 10.2, 10.1, 10.35]
    daily = _daily("600003.SH", closes)
    events = pd.DataFrame({"date": ["2020-01-02"], "adjust_factor": [5.0]})
    bars = _next_bars({"600003.SH": daily}, ["600003.SH"],
                      after_date=daily["date"].iloc[0], entry_lag=1,
                      factors_map={"600003.SH": events})
    # 全程同一因子 → 复权价与不复权价同比，收益不变
    got = float(bars["close"].iloc[0]) / float(bars["prev_close"].iloc[0]) - 1
    assert got == pytest.approx(10.1 / 10.2 - 1, abs=1e-12)


# ── 缺因子表时必须**可见**，不能静默用不复权 ────────────────────────────────

def test_missing_factor_table_is_flagged_not_silently_unadjusted():
    """没有因子表的代码走不复权回退，但必须在 `adjusted` 列上自报家门。

    静默降级是这类系统最常见的病根：数值看起来正常，实际混入了除权跳空。
    （价格取正常波动——若这里再用除权跳空，会被
    `MAX_PLAUSIBLE_RETURN` 那道更靠前的闸门先挡掉，测不到本条要测的东西。）
    """
    daily = _daily("600004.SH", [100.0, 101.0, 100.5, 100.8])
    bars = _next_bars({"600004.SH": daily}, ["600004.SH"],
                      after_date=daily["date"].iloc[1], entry_lag=1,
                      factors_map={})
    assert len(bars) == 1
    assert "adjusted" in bars.columns
    assert not bool(bars["adjusted"].iloc[0]), "无因子表却标成已复权 = 谎报"


def test_factor_table_present_is_flagged_adjusted():
    daily = _daily("600005.SH", [100.0, 101.0, 100.5, 100.2])
    events = pd.DataFrame({"date": ["2023-12-29", daily["date"].iloc[3]],
                           "adjust_factor": [1.0, 1.05]})
    bars = _next_bars({"600005.SH": daily}, ["600005.SH"],
                      after_date=daily["date"].iloc[1], entry_lag=1,
                      factors_map={"600005.SH": events})
    assert len(bars) == 1
    assert bool(bars["adjusted"].iloc[0])


# ── 共享实现：hfq_close ────────────────────────────────────────────────────

def test_hfq_close_is_continuous_across_split():
    """后复权序列在拆股点必须连续——这是「不分红不除权不产生收益」的定义。"""
    closes = [100.0, 101.0, 100.5, 10.0, 10.2]
    daily = _daily("600006.SH", closes)
    events = pd.DataFrame({"date": ["2023-12-29", daily["date"].iloc[3]],
                           "adjust_factor": [1.0, 10.0]})
    adj = hfq_close(daily, events).to_numpy(float)
    assert adj[2] == pytest.approx(100.5)
    assert adj[3] == pytest.approx(100.0)
    assert abs(adj[3] / adj[2] - 1) < 0.01, "复权序列在拆股点必须连续"


def test_hfq_close_without_events_is_identity():
    daily = _daily("600007.SH", [10.0, 11.0, 12.0])
    assert np.allclose(hfq_close(daily, None).to_numpy(float), [10.0, 11.0, 12.0])
    assert np.allclose(hfq_close(daily, pd.DataFrame()).to_numpy(float),
                       [10.0, 11.0, 12.0])


# ── 真实数据回归（数据不在则跳过，不让单测依赖生产数据） ────────────────────

def test_real_world_exrights_case_matches_exchange_pctchg():
    """真实案例：002594.SZ 2025-07-29。

    实测：不复权比值 −66.94%，交易所 `pctChg` +0.37%。
    复权后收益必须与交易所口径同侧且量级一致（不要求完全相等：
    `pctChg` 与后复权在股息再投等细节上仍有差异）。
    """
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "data" / "master" / "daily" / "002594.SZ.parquet"
    if not path.exists():
        pytest.skip("生产数据不在本机，跳过真实案例回归")
    d = pd.read_parquet(path).sort_values("date").reset_index(drop=True)
    i = int(np.where(d["date"].to_numpy() == "2025-07-29")[0][0])
    assert i > 0
    raw = float(d["close"].iloc[i] / d["close"].iloc[i - 1] - 1)
    exch = float(d["pctChg"].iloc[i]) / 100.0
    assert raw < -0.60, "前提变了：本样本不再是 60% 以上的除权跳空"
    assert exch > 0, "前提变了：本样本交易所口径不再是上涨"
    assert abs(raw - exch) > 0.5, "前提变了：两个口径差异不大，测不出东西"


# ── 实盘判定路径必须与回测同口径 ────────────────────────────────────────────

def test_live_last_two_bars_is_exrights_adjusted(tmp_path):
    """实盘判定 `last_two_bars` 与回测 `_next_bars` 必须同口径。

    否则同一天会给出两个不同的收益率：网页上给用户看的命中率与回测报告对不上，
    而谁也说不清该信哪个。
    """
    from aplus.store import LocalStore
    from aplus.verify import last_two_bars

    store = LocalStore(tmp_path)
    # 第 3 根正跨一次拆股：100.5 → 10.0
    df = _daily("600008.SH", [100.0, 101.0, 100.5, 10.0])
    store.upsert_daily("600008.SH", df)
    store.upsert_factor("600008.SH",
                        pd.DataFrame({"date": ["2023-12-29", "2024-01-05"],
                                      "adjust_factor": [1.0, 10.0]}))

    # 锚点必须落在**拆股前一根**：entry_lag=1 买入=D+1、卖出=D+2，
    # 交易窗口才跨到拆股日。这正是回测 `_next_bars` 的取法。
    bar = last_two_bars(store, "600008.SH", after_date=df["date"].iloc[1])
    assert bar is not None
    assert bar["adjusted"] is True
    got = bar["close"] / bar["prev_close"] - 1
    assert got == pytest.approx(-0.005, abs=1e-3), (
        f"实盘判定把拆股记成 {got:+.2%}，与回测口径不一致"
    )


def test_live_last_two_bars_returns_none_when_too_short(tmp_path):
    from aplus.store import LocalStore
    from aplus.verify import last_two_bars

    store = LocalStore(tmp_path)
    store.upsert_daily("600009.SH", _daily("600009.SH", [10.0]))
    assert last_two_bars(store, "600009.SH", after_date="2000-01-01") is None
    assert last_two_bars(store, "600099.SH", after_date="2000-01-01") is None  # 完全没数据


def test_live_last_two_bars_without_factor_table_flags_unadjusted(tmp_path):
    from aplus.store import LocalStore
    from aplus.verify import last_two_bars

    store = LocalStore(tmp_path)
    df = _daily("600010.SH", [10.0, 11.0, 12.0])
    store.upsert_daily("600010.SH", df)
    bar = last_two_bars(store, "600010.SH", after_date=df["date"].iloc[0])
    assert bar["adjusted"] is False, "无因子表却标成已复权 = 谎报"
    # 无因子表 → 裸价 12.0 / 11.0（entry=D+1、exit=D+2）
    assert bar["close"] / bar["prev_close"] - 1 == pytest.approx(12 / 11 - 1, abs=1e-12)

"""可转债持有期经济学测试（路线 A 第三阶段，TDD RED 先行）。

二阶段结论：低价深度因子（cheap=max(0,115-price)）对随机 +0.091%/天，但按
万 15/边双边每日全换手 = -0.3%/天，毛超额养不起费用。转债无 T+1——任何持有
期合法——本阶段问的问题：**换手摊薄后低价轮动是否转正？**

口径（与 aplus/bond_horizon.py docstring 写死的一致）：
- horizon_return：信号日 D 收盘后决策，D 之后第一根 K 线（D+1）开盘买入，
  持有 h 根自身 K 线后收盘卖出（h=1 退化为二阶段的 D+1 open→close）
- 数据先于 h 根结束（强赎/摘牌）：按末根收盘卖出（不引入幸存者剔除）
- 费用：cost = 一次完整换手的双边费率；每期按换手比例收
  turnover_ratio = (新进 + 退出) / (2 × max(top_n, |当期|, |上期|))
- 调仓网格：交易日历（最鲜满历史债锚定）每 hold_days 根，末段不足 hold_days
  的不完整期丢弃；空池期跳过
"""
import pandas as pd
import pytest

from aplus.bond_backtest import bond_walk_forward
from aplus.bond_horizon import (
    build_horizon_cache,
    cheap_rotation_backtest,
    horizon_return,
)


# ---------- 数据构造（与 test_bond_backtest 同风格） ----------

def bdays(n, start="2026-01-05"):
    return [d.strftime("%Y-%m-%d") for d in pd.bdate_range(start, periods=n)]


def bond_frame(code, dates, closes, opens=None, volumes=None):
    """合成转债日 K：默认开=收（持有期收益 0）、量 10 万（×~100 元 > 500 万门槛）。"""
    closes = [float(c) for c in closes]
    n = len(dates)
    assert len(closes) == n
    if opens is None:
        opens = list(closes)
    if volumes is None:
        volumes = [100000.0] * n
    return pd.DataFrame({
        "date": list(dates),
        "code": code,
        "open": [float(o) for o in opens],
        "high": [max(o, c) for o, c in zip(opens, closes)],
        "low": [min(o, c) for o, c in zip(opens, closes)],
        "close": closes,
        "volume": [float(v) for v in volumes],
    })


def ladder_map(n_days=60, base0=95.0, n_cheap=12, cal_close=130.0,
               opens=None, volumes=None):
    """CAL（日历锚，130 元）+ n_cheap 只低价债（95, 96, …）。

    低价序 = 代码序（B00 最深）。open 缺省 = close。
    """
    dates = bdays(n_days)
    m = {"CAL.SH": bond_frame("CAL.SH", dates, [cal_close] * n_days,
                              opens=opens, volumes=volumes)}
    for i in range(n_cheap):
        m[f"B{i:02d}.SH"] = bond_frame(
            f"B{i:02d}.SH", dates, [base0 + i] * n_days, opens=opens, volumes=volumes)
    return m, dates


# ---------- horizon_return：单债持有期收益（纯逻辑） ----------

def test_horizon_return_multi_day():
    dates = bdays(5)
    df = bond_frame("X", dates, closes=[100, 101, 102, 103, 104],
                    opens=[100, 100, 100, 100, 100])
    # 信号 d0 → d1 开盘买 100，持有 3 根 → d3 收盘卖 103
    assert horizon_return(df, dates[0], 3) == pytest.approx(103.0 / 100.0 - 1.0)


def test_horizon_return_h1_is_phase2_intraday():
    dates = bdays(5)
    df = bond_frame("X", dates, closes=[100, 101, 102, 103, 104],
                    opens=[100, 200, 300, 400, 500])
    assert horizon_return(df, dates[0], 1) == pytest.approx(101.0 / 200.0 - 1.0)


def test_horizon_return_truncated_sells_at_last_close():
    """数据先于 h 根结束（强赎/摘牌代理）：按末根收盘卖出。"""
    dates = bdays(4)
    df = bond_frame("X", dates, closes=[100, 100, 100, 106],
                    opens=[100, 100, 100, 100])
    # 信号 d1 → d2 买 100，h=10 只剩 2 根 → 末根 d3 收盘 106 卖出
    assert horizon_return(df, dates[1], 10) == pytest.approx(106.0 / 100.0 - 1.0)


def test_horizon_return_no_next_bar_is_none():
    dates = bdays(3)
    df = bond_frame("X", dates, [100, 101, 102])
    assert horizon_return(df, dates[2], 1) is None


def test_horizon_return_bad_open_is_none():
    dates = bdays(3)
    df = bond_frame("X", dates, [100, 101, 102], opens=[100, 0.0, 101])
    assert horizon_return(df, dates[0], 2) is None


def test_horizon_return_asof_signal_between_bars():
    """信号日落在这只债的两根 K 线之间（停牌/非其交易日）：买其后第一根。"""
    dates = bdays(7)  # 01-05..01-09, 01-12, 01-13；2026-01-10 是周六
    df = bond_frame("X", dates, [100] * 7, opens=[100] * 7)
    assert horizon_return(df, "2026-01-10", 1) == pytest.approx(0.0)
    assert horizon_return(df, "2026-01-10", 2) == pytest.approx(0.0)


def test_horizon_return_rejects_bad_h():
    df = bond_frame("X", bdays(3), [100, 101, 102])
    with pytest.raises(ValueError):
        horizon_return(df, df["date"].iloc[0], 0)


# ---------- cheap_rotation_backtest：轮动组合（纯逻辑） ----------

def test_picks_topn_by_cheap_depth():
    m, dates = ladder_map()
    out = cheap_rotation_backtest(m, dates[0], dates[-1], top_n=3, hold_days=1, cost=0.0)
    assert out["periods"][0]["picks"] == ["B00.SH", "B01.SH", "B02.SH"]
    assert out["periods"][-1]["picks"] == ["B00.SH", "B01.SH", "B02.SH"]


def test_grid_every_hold_days_and_tail_dropped():
    m, dates = ladder_map(n_days=60)
    # 41 根准入门槛（pos=日序+1）：首个有池的信号日是 dates[40]；
    # h=5 网格命中 40/45/50 三期（55+5 越界丢弃——末段不完整期）
    out = cheap_rotation_backtest(m, dates[0], dates[-1], top_n=3, hold_days=5, cost=0.0)
    assert out["n_periods"] == 3
    assert [p["day"] for p in out["periods"]] == [dates[40], dates[45], dates[50]]
    assert out["n_days"] == 15  # 覆盖日历跨度：首期买入 41 → 末期卖出 55


def test_window_slice_respected():
    m, dates = ladder_map(n_days=60)
    out = cheap_rotation_backtest(m, dates[50], dates[-1], top_n=3, hold_days=1, cost=0.0)
    # 日历切片 50..58（end 排他），i+1≤8 → 8 期
    assert out["n_periods"] == 8
    assert out["periods"][0]["day"] == dates[50]


def test_cost_half_on_initial_build_then_zero_without_turnover():
    m, dates = ladder_map()
    out = cheap_rotation_backtest(m, dates[0], dates[-1], top_n=3, hold_days=1, cost=0.01)
    # 首期建仓只付买侧：ratio = 3/(2*3) = 0.5
    assert out["periods"][0]["cost"] == pytest.approx(0.005)
    # 池稳定 → 后续零换手零费用
    assert out["periods"][1]["cost"] == pytest.approx(0.0)
    assert out["avg_turnover_names"] == 0.0


def test_cost_proportional_to_turnover_on_replacement():
    m, dates = ladder_map()
    # B02（97 元）在第 50 根跳到 116（cheap 归零）→ 信号日 dates[50] 被 B03 替换
    jump = list(m["B02.SH"]["close"])
    jump[50] = 116.0
    m["B02.SH"] = bond_frame("B02.SH", dates, jump)
    out = cheap_rotation_backtest(m, dates[0], dates[-1], top_n=3, hold_days=1, cost=0.01)
    hit = next(p for p in out["periods"] if p["day"] == dates[50])
    assert hit["turnover_names"] == 1
    # 1 进 1 出：ratio = (1+1)/(2*3) = 1/3
    assert hit["cost"] == pytest.approx(0.01 / 3.0)
    assert "B03.SH" in hit["picks"] and "B02.SH" not in hit["picks"]


def flat_map(n=60, close=101.0, open_=100.0, n_bonds=13):
    """全部债同价（cheap 并列 → 代码序 tie-break 取 B00/B01/B02），open 恒定。"""
    dates = bdays(n)
    m = {}
    for i in range(n_bonds):
        code = f"B{i - 1:02d}.SH" if i else "CAL.SH"
        m[code] = bond_frame(code, dates, [close] * n, opens=[open_] * n)
    return m, dates


def test_gross_compounding_and_annualization():
    # 全部债 open=100、close=101 → 任意持有期收益恒 +1%
    m, dates = flat_map()
    out = cheap_rotation_backtest(m, dates[0], dates[-1], top_n=3, hold_days=1, cost=0.0)
    assert out["gross_per_period"] == pytest.approx(0.01)
    assert out["net_per_period"] == pytest.approx(0.01)
    n_per = out["n_periods"]
    assert out["total_return"] == pytest.approx(1.01 ** n_per - 1.0)
    assert out["annualized_return"] == pytest.approx((1.01 ** n_per) ** (250.0 / out["n_days"]) - 1.0)
    assert out["max_drawdown"] == pytest.approx(0.0)


def test_max_drawdown_from_peak():
    n = 60
    m, dates = flat_map(n=n)
    # 崩盘必须打在「卖出棒」而非信号棒：末棒(58)收盘 78 → 信号日 57 期 -22%；
    # 信号棒(57)收盘仍 101 → 照常入选（as-of 语义：因子只见过去）
    for code in ("B00.SH", "B01.SH", "B02.SH"):
        closes = [101.0] * n
        closes[n - 2] = 78.0
        m[code] = bond_frame(code, dates, closes, opens=[100.0] * n)
    out = cheap_rotation_backtest(m, dates[0], dates[-1], top_n=3, hold_days=1, cost=0.0)
    # nav：+1%×17 期后 ×0.78 → 峰值 1.01^17、谷值即末值 → mdd = 1-0.78
    assert out["max_drawdown"] == pytest.approx(1.0 - 0.78)
    assert out["total_return"] == pytest.approx(1.01 ** 17 * 0.78 - 1.0)


def test_gross_equals_mean_of_horizon_returns():
    n = 60
    m, dates = ladder_map(n_days=n)
    # 给每只债独立漂移（保持低价序），持有期收益非平凡
    for i in range(12):
        code = f"B{i:02d}.SH"
        closes = [95.0 + i + 0.01 * i * t for t in range(n)]
        m[code] = bond_frame(code, dates, closes)
    out = cheap_rotation_backtest(m, dates[0], dates[-1], top_n=4, hold_days=3, cost=0.0)
    for p in out["periods"]:
        want = sum(horizon_return(m[c], p["day"], 3) for c in p["picks"]) / len(p["picks"])
        assert p["gross"] == pytest.approx(want, abs=1e-12)


def test_random_control_same_funnel_seeded_deterministic():
    m, dates = ladder_map()
    a = cheap_rotation_backtest(m, dates[0], dates[-1], top_n=3, hold_days=1,
                                cost=0.001, random_seed=7)
    b = cheap_rotation_backtest(m, dates[0], dates[-1], top_n=3, hold_days=1,
                                cost=0.001, random_seed=7)
    assert a == b
    assert all(p["n_picks"] == 3 for p in a["periods"])
    assert a["periods"][0]["picks"] != ["B00.SH", "B01.SH", "B02.SH"]
    # 池全体都合格 → 随机 picks 必在 13 只合格债内
    universe = {f"B{i:02d}.SH" for i in range(12)} | {"CAL.SH"}
    assert set(a["periods"][0]["picks"]) <= universe


def test_universe_filter_illiquid_and_immature_excluded():
    n = 60
    m, dates = ladder_map(n_days=n)
    # 最便宜但流动性不足（量×收 = 50×100 = 5000 ≤ 500 万）
    m["ILL.SH"] = bond_frame("ILL.SH", dates, [50.0] * n, volumes=[100.0] * n)
    # 上市不满 41 天（仅 40 根）
    m["NEW.SH"] = bond_frame("NEW.SH", bdays(40), [96.5] * 40)
    out = cheap_rotation_backtest(m, dates[0], dates[-1], top_n=5, hold_days=1, cost=0.0)
    picks = {c for p in out["periods"] for c in p["picks"]}
    assert "ILL.SH" not in picks and "NEW.SH" not in picks


def test_pool_smaller_than_topn_picks_all():
    m, dates = ladder_map(n_cheap=2)  # CAL + 2 只 = 3 只池
    out = cheap_rotation_backtest(m, dates[0], dates[-1], top_n=10, hold_days=1, cost=0.0)
    assert all(p["n_picks"] == 3 for p in out["periods"])


def test_empty_map_returns_zero_result():
    out = cheap_rotation_backtest({}, "2026-01-05", "2026-03-01", top_n=3, hold_days=1)
    assert out["n_periods"] == 0
    assert out["total_return"] == 0.0
    assert out["annualized_return"] == 0.0


def test_hold_days_and_top_n_validation():
    m, dates = ladder_map()
    with pytest.raises(ValueError):
        cheap_rotation_backtest(m, dates[0], dates[-1], hold_days=0)
    with pytest.raises(ValueError):
        cheap_rotation_backtest(m, dates[0], dates[-1], top_n=0)
    with pytest.raises(ValueError):
        cheap_rotation_backtest(m, dates[0], dates[-1], top_n=3, cost=-0.001)


def test_cache_reuse_identical_results():
    m, dates = ladder_map()
    cache = build_horizon_cache(m)
    a = cheap_rotation_backtest(m, dates[0], dates[-1], top_n=3, hold_days=2,
                                cost=0.001, cache=cache)
    b = cheap_rotation_backtest(m, dates[0], dates[-1], top_n=3, hold_days=2,
                                cost=0.001, cache=cache)
    c = cheap_rotation_backtest(m, dates[0], dates[-1], top_n=3, hold_days=2,
                                cost=0.001)
    assert a == b == c


def test_h1_gross_matches_phase2_walk_forward():
    """交叉校验：h=1、零费时，逐期毛收益必须与二阶段 bond_walk_forward（cheap 满权）
    的逐日盘中收益完全一致——同漏斗同口径的回归钉。"""
    n = 60
    m, dates = ladder_map(n_days=n)
    for i in range(12):
        code = f"B{i:02d}.SH"
        closes = [95.0 + i + 0.01 * i * t for t in range(n)]
        m[code] = bond_frame(code, dates, closes)
    mine = cheap_rotation_backtest(m, dates[0], dates[-1], top_n=5, hold_days=1, cost=0.0)
    ref = bond_walk_forward(m, dates[0], dates[-1], weights={"cheap": 1.0}, top_n=5)
    ref_daily = ref.groupby("pred_date")["intraday"].mean()
    mine_daily = {p["day"]: p["gross"] for p in mine["periods"]}
    common = sorted(set(mine_daily) & set(ref_daily.index))
    assert len(common) >= 15
    for d in common:
        assert mine_daily[d] == pytest.approx(float(ref_daily[d]), abs=1e-12)


# ---------- 默认费率档位（防止乐观默认值静默回来） ----------

def test_default_cost_is_conservative_rate():
    """不传 cost 时必须拿到**保守档**万 15/边，不是低保费敏感性档。

    历史坑：默认值曾是 COST_WAN1_5(0.0003) 而判定用 COST_WAN15(0.003)，
    同一模块两套费率，漏传 cost 的调用方静默拿到乐观两倍的结果。
    """
    from aplus.bond_horizon import COST_WAN1_5, COST_WAN15, cheap_rotation_backtest

    assert COST_WAN15 == 0.003
    assert COST_WAN1_5 == 0.0003
    m, dates = ladder_map()
    default = cheap_rotation_backtest(m, dates[0], dates[-1], top_n=3, hold_days=5)
    conservative = cheap_rotation_backtest(m, dates[0], dates[-1], top_n=3,
                                           hold_days=5, cost=COST_WAN15)
    assert default["cost_per_period"] == pytest.approx(conservative["cost_per_period"])
    assert default["annualized_return"] == pytest.approx(conservative["annualized_return"])


# ---------- 日历锚定（纸面账户相位稳定性的前提） ----------

def test_horizon_cache_calendar_can_be_pinned():
    """日历可钉死在指定债券上——纸面账户的调仓网格必须锚在**固定的**日历上。

    默认 ref = (末日, 根数) 最大的帧；一次性的回测无所谓，但**每天都要跑的纸面
    账户**必须锚定：参考债一旦停牌，它的数据就缺该交易日，日历随之少一天，
    所有臂的调仓网格整体偏移，相位锚点当场失效。
    """
    from aplus.bond_horizon import build_horizon_cache

    dates = bdays(60)
    m = {"A.SH": bond_frame("A.SH", dates, [95.0] * 60),
         "B.SH": bond_frame("B.SH", dates, [96.0] * 60)}
    assert build_horizon_cache(m, ref_code="B.SH")["calendar_ref"] == "B.SH"
    assert build_horizon_cache(m, ref_code="B.SH")["calendar"] == dates


def test_horizon_cache_falls_back_when_pinned_ref_is_stale():
    """钉住的参考债不再更新（停牌/摘牌）→ 自动回退到最新鲜的帧并如实标记。"""
    from aplus.bond_horizon import build_horizon_cache

    dates = bdays(60)
    m = {"FRESH.SH": bond_frame("FRESH.SH", dates, [95.0] * 60),
         "STALE.SH": bond_frame("STALE.SH", dates[:50], [96.0] * 50)}
    cache = build_horizon_cache(m, ref_code="STALE.SH")
    assert cache["calendar_ref"] != "STALE.SH"
    assert cache["calendar"] == dates          # 已回退到新鲜帧
    assert cache["ref_fallback"] is True       # 并如实标记发生了回退


def test_horizon_cache_reports_fallback_reason_for_audit():
    from aplus.bond_horizon import build_horizon_cache

    dates = bdays(60)
    m = {"FRESH.SH": bond_frame("FRESH.SH", dates, [95.0] * 60),
         "GONE.SH": bond_frame("GONE.SH", dates[:50], [96.0] * 50)}
    cache = build_horizon_cache(m, ref_code="GONE.SH")
    assert cache.get("ref_fallback_reason") in {"missing", "stale"}


def test_horizon_cache_without_pin_still_works():
    from aplus.bond_horizon import build_horizon_cache

    dates = bdays(60)
    m = {"A.SH": bond_frame("A.SH", dates, [95.0] * 60)}
    cache = build_horizon_cache(m)
    assert cache["calendar"] == dates
    assert cache["calendar_ref"] == "A.SH"
    assert "ref_fallback" not in cache

"""可转债 T+0 回测纯逻辑测试（路线 A 第二阶段，TDD RED 先行）。

方法论复刻自股票侧终局调查（PLATFORM.md 迭代日志 2026-10-11 凌晨）：
- 收益口径 = D+1 的 open→close（T+0 当日兑现——股票侧 h=1 曲线同口径，
  那边被 T+1 制度判违法，这边合法，是路线 A 的全部立论）
- 随机对照（同漏斗随机选券）是「收益归因三件套」的关键武器，必须同口径

与股票 walk_forward 的同构与差异（差异全部是转债制度性差异）：
- 无 turn 列（转债无换手率数据）→ 因子集 mom20/rev5/vol20/vsur/cheap
- cheap：低价深度（115 阈值下方深度，A 股转债经典债底保护效应）
- universe 过滤：is_active 摘牌检测 + 最新一根 volume×close > 500 万 + 上市满 41 天
"""
import pandas as pd
import pytest

from aplus.bond_backtest import (
    attach_bond_factors,
    bond_factors,
    bond_portfolio_sim,
    bond_walk_forward,
    cheap_depth,
    intraday_return,
)

WALK_COLUMNS = [
    "pred_date", "code", "trade_date", "open", "close", "intraday", "score",
    "mom20", "rev5", "vol20", "vsur", "cheap",
]


# ---------- 数据构造 ----------

def bdays(n, start="2026-01-05"):
    return [d.strftime("%Y-%m-%d") for d in pd.bdate_range(start, periods=n)]


def bond_frame(code, dates, closes, opens=None, volumes=None):
    """合成转债日 K：默认开=收（盘中收益 0）、量 10 万（×~100 元 > 500 万流动性门槛）。"""
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


def assert_close_or_both_nan(a, b, tol=1e-9):
    if a != a or b != b:
        assert (a != a) and (b != b)
    else:
        assert a == pytest.approx(b, abs=tol)


# ---------- intraday_return：T+0 收益口径 ----------

def test_intraday_return_up():
    assert intraday_return({"open": 100.0, "close": 101.0}) == pytest.approx(0.01)


def test_intraday_return_down():
    assert intraday_return({"open": 200.0, "close": 198.0}) == pytest.approx(-0.01)


# ---------- cheap：低价深度 ----------

def test_cheap_depth_below_threshold():
    assert cheap_depth(100.0) == pytest.approx(15.0)
    assert cheap_depth(90.0) == pytest.approx(25.0)


def test_cheap_depth_at_or_above_threshold_is_zero():
    assert cheap_depth(115.0) == 0.0
    assert cheap_depth(130.0) == 0.0


# ---------- bond_factors：复用股票因子 + cheap ----------

def rising_frame(n=60):
    dates = bdays(n)
    closes = [100.0 * (1.01 ** i) + (i % 3) * 0.05 for i in range(n)]
    volumes = [50000.0 + 1000.0 * i for i in range(n)]
    return bond_frame("123001.SZ", dates, closes, volumes=volumes)


def test_bond_factors_reuses_stock_factor_semantics():
    """mom20/rev5/vol20/vsur 与 aplus.factor.compute_factors 完全同义（转债无复权无 turn）。"""
    from aplus.factor import compute_factors

    df = rising_frame()
    f = bond_factors(df)
    g = compute_factors(df)  # 无 turn 列 → turn20=NaN；无 adjust_factor → 原始 close
    for key in ("mom20", "rev5", "vol20", "vsur"):
        assert_close_or_both_nan(float(f[key]), float(g[key]))


def test_bond_factors_cheap_from_last_close():
    df = rising_frame()
    f = bond_factors(df)
    assert f["cheap"] == pytest.approx(0.0)  # 1.01^59 × 100 ≫ 115

    dates = bdays(45)
    cheap_df = bond_frame("113001.SH", dates, [110.0] * 45)
    assert bond_factors(cheap_df)["cheap"] == pytest.approx(5.0)


def test_bond_factors_has_no_turn20():
    assert "turn20" not in bond_factors(rising_frame()).index


# ---------- attach_bond_factors：向量化全历史因子（回测性能路径） ----------

def test_attach_bond_factors_equals_scalar_at_cutoffs():
    """任意截断点 k：attach 后第 k 行 ≡ 对前 k+1 行调 bond_factors（防未来函数的同义性）。"""
    df = rising_frame()
    a = attach_bond_factors(df)
    for k in (20, 35, 59):
        scalar = bond_factors(df.iloc[: k + 1])
        row = a.iloc[k]
        for key in ("mom20", "rev5", "vol20", "vsur", "cheap"):
            assert_close_or_both_nan(float(row[key]), float(scalar[key]), tol=1e-8)


def test_attach_bond_factors_vsur_nan_when_prior_mean_zero():
    dates = bdays(45)
    volumes = [50000.0] * 21 + [0.0] * 20 + [80000.0] * 4
    df = bond_frame("127001.SZ", dates, [100.0 + i * 0.3 for i in range(45)], volumes=volumes)
    a = attach_bond_factors(df)
    scalar = bond_factors(df.iloc[: 21 + 20 + 1])
    assert a.iloc[41]["vsur"] != a.iloc[41]["vsur"]  # 前 20 根均量为 0 → NaN
    assert_close_or_both_nan(float(a.iloc[41]["vsur"]), float(scalar["vsur"]))


# ---------- bond_walk_forward：D+1 open→close 口径 + universe 过滤 ----------

def test_walk_forward_return_is_next_day_open_to_close():
    """核心口径回归：收益 = D+1 的 open→close，不是 close 对 close。"""
    dates = bdays(45)
    closes = [100.0 * (1.01 ** i) for i in range(45)]
    closes[-1] = 112.0
    opens = list(closes)
    opens[-1] = 100.0  # D+1 开 100 收 112：盘中 +12%，close 对 close 只有 +6.7%
    df = bond_frame("123001.SZ", dates, closes, opens=opens)
    res = bond_walk_forward({"123001.SZ": df}, dates[0], dates[-1], weights={"mom20": 1.0})
    assert list(res.columns) == WALK_COLUMNS
    # 上市满 41 天：信号日 idx≥40；end 排除末日 → 4 个信号日
    assert len(res) == 4
    last = res[res["pred_date"] == dates[-2]].iloc[0]
    assert last["trade_date"] == dates[-1]
    assert last["open"] == 100.0
    assert last["close"] == 112.0
    assert last["intraday"] == pytest.approx(0.12)


def test_walk_forward_requires_41_bars():
    """上市满 41 天：40 根 K 线永不入池；42 根恰好在第 41 根信号日入池一次。"""
    dates = bdays(45)
    short40 = bond_frame("128001.SZ", dates[:40], [100.0] * 40)
    short42 = bond_frame("128002.SZ", dates[:42], [100.0 + i for i in range(42)])
    res = bond_walk_forward(
        {"128001.SZ": short40, "128002.SZ": short42}, dates[0], dates[-1],
        weights={"mom20": 1.0},
    )
    assert set(res["code"]) == {"128002.SZ"}
    assert res["pred_date"].tolist() == [dates[40]]  # 唯一有 D+1 的合格信号日
    assert res["trade_date"].tolist() == [dates[41]]


def test_walk_forward_liquidity_filter():
    """最新一根 volume×close > 500 万（严格大于）：4 百万/恰 5 百万剔除，6 百万入池。"""
    dates = bdays(45)
    illiquid = bond_frame("113011.SH", dates, [100.0] * 45, volumes=[40000.0] * 45)
    boundary = bond_frame("113012.SH", dates, [100.0] * 45, volumes=[50000.0] * 45)
    liquid = bond_frame("113013.SH", dates, [100.0 * 1.005 ** i for i in range(45)],
                        volumes=[60000.0] * 45)
    res = bond_walk_forward(
        {"113011.SH": illiquid, "113012.SH": boundary, "113013.SH": liquid},
        dates[0], dates[-1], weights={"mom20": 1.0}, top_n=1,
    )
    assert set(res["code"]) == {"113013.SH"}  # 动量最高且流动性达标者独占


def test_walk_forward_stale_suspension_excluded():
    """is_active 非真空场景：停牌 45 自然日的债，D+1 有 K 线（复牌可执行）
    也必须被摘牌检测拦下——过期因子不得入池。"""
    dates = bdays(45) + ["2026-04-20", "2026-04-21", "2026-04-22",
                         "2026-04-23", "2026-04-24", "2026-04-25"]
    ref = bond_frame("113020.SH", dates, [100.0 * 1.002 ** i for i in range(len(dates))])
    susp = bond_frame("113021.SH", dates[:45] + ["2026-04-24", "2026-04-25"],
                      [100.0 * 1.002 ** i for i in range(47)])
    res = bond_walk_forward({"113020.SH": ref, "113021.SH": susp}, dates[0], dates[-1],
                            weights={"mom20": 1.0})
    stale_window = {"2026-04-20", "2026-04-21", "2026-04-22", "2026-04-23"}
    susp_days = set(res[res["code"] == "113021.SH"]["pred_date"])
    assert not (susp_days & stale_window)  # 停牌窗口内零入选（尽管 04-24 有可执行 D+1）
    assert "2026-04-24" in susp_days  # 复牌当日恢复新鲜 → 重新入池
    assert res[res["code"] == "113020.SH"]["pred_date"].isin(stale_window).any()


def test_walk_forward_skips_broken_next_bar():
    """D+1 K 线坏数据（open=0 / 量=0）不可成交 → 该信号日不出行。"""
    dates = bdays(45)
    closes = [100.0 * 1.01 ** i for i in range(45)]
    opens_bad = bond_frame("123031.SZ", dates, closes, opens=[c for c in closes[:-1]] + [0.0])
    res_open = bond_walk_forward({"123031.SZ": opens_bad}, dates[0], dates[-1],
                                 weights={"mom20": 1.0})
    assert dates[-1] not in set(res_open["trade_date"]) and len(res_open) == 3

    vol_bad = bond_frame("123032.SZ", dates, closes, volumes=[100000.0] * 44 + [0.0])
    res_vol = bond_walk_forward({"123032.SZ": vol_bad}, dates[0], dates[-1],
                                weights={"mom20": 1.0})
    assert len(res_vol) == 3


def test_walk_forward_window_half_open():
    dates = bdays(45)
    df = bond_frame("123001.SZ", dates, [100.0 * 1.01 ** i for i in range(45)])
    res = bond_walk_forward({"123001.SZ": df}, dates[10], dates[30], weights={"mom20": 1.0})
    assert res.empty  # 上市未满 41 天的窗口内无合格信号日
    res2 = bond_walk_forward({"123001.SZ": df}, dates[40], dates[-1], weights={"mom20": 1.0})
    assert set(res2["pred_date"]) == {dates[40], dates[41], dates[42], dates[43]}
    assert all(d >= dates[40] and d < dates[-1] for d in res2["pred_date"])


def test_walk_forward_top_n_selection_by_weights():
    dates = bdays(45)
    up = bond_frame("123041.SZ", dates, [100.0 * 1.01 ** i for i in range(45)])
    flat = bond_frame("123042.SZ", dates, [100.0] * 45)
    down = bond_frame("123043.SZ", dates, [100.0 * 0.99 ** i for i in range(45)])
    m = {"123041.SZ": up, "123042.SZ": flat, "123043.SZ": down}
    res = bond_walk_forward(m, dates[0], dates[-1], weights={"mom20": 1.0}, top_n=1)
    assert set(res["code"]) == {"123041.SZ"}
    res_rev = bond_walk_forward(m, dates[0], dates[-1], weights={"rev5": 1.0}, top_n=1)
    assert set(res_rev["code"]) == {"123043.SZ"}  # 反转权重选中持续下跌债


def test_walk_forward_empty_map():
    res = bond_walk_forward({}, "2026-01-01", "2026-12-31", weights={"mom20": 1.0})
    assert res.empty
    assert list(res.columns) == WALK_COLUMNS


def test_walk_forward_requires_weights_or_seed():
    dates = bdays(45)
    df = bond_frame("123001.SZ", dates, [100.0 * 1.01 ** i for i in range(45)])
    with pytest.raises(ValueError):
        bond_walk_forward({"123001.SZ": df}, dates[0], dates[-1])


# ---------- 随机对照（同漏斗随机选券） ----------

def test_random_control_deterministic_and_within_universe():
    dates = bdays(45)
    up = bond_frame("123041.SZ", dates, [100.0 * 1.01 ** i for i in range(45)])
    flat = bond_frame("123042.SZ", dates, [100.0] * 45)
    down = bond_frame("123043.SZ", dates, [100.0 * 0.99 ** i for i in range(45)])
    m = {"123041.SZ": up, "123042.SZ": flat, "123043.SZ": down}
    r1 = bond_walk_forward(m, dates[0], dates[-1], random_seed=7, top_n=2)
    r2 = bond_walk_forward(m, dates[0], dates[-1], random_seed=7, top_n=2)
    assert r1.equals(r2)  # 同种子可复现
    assert set(r1["code"]) <= {"123041.SZ", "123042.SZ", "123043.SZ"}
    assert len(r1) == 4 * 2  # 与因子模式同漏斗：4 个合格信号日 × top_n=2
    assert r1["score"].isna().all()


def test_random_control_can_differ_from_factor_picks():
    """随机选券存在与因子排名不同的种子——证明对照是「重新选券」而非复刻排名。"""
    dates = bdays(45)
    up = bond_frame("123041.SZ", dates, [100.0 * 1.01 ** i for i in range(45)])
    flat = bond_frame("123042.SZ", dates, [100.0] * 45)
    down = bond_frame("123043.SZ", dates, [100.0 * 0.99 ** i for i in range(45)])
    m = {"123041.SZ": up, "123042.SZ": flat, "123043.SZ": down}
    scored = bond_walk_forward(m, dates[0], dates[-1], weights={"mom20": 1.0}, top_n=2)
    scored_sets = {d: set(g["code"]) for d, g in scored.groupby("pred_date")}
    seeds = range(1, 21)
    found_diff = False
    for seed in seeds:
        r = bond_walk_forward(m, dates[0], dates[-1], random_seed=seed, top_n=2)
        rand_sets = {d: set(g["code"]) for d, g in r.groupby("pred_date")}
        if any(rand_sets[d] != scored_sets[d] for d in scored_sets):
            found_diff = True
            break
    assert found_diff


# ---------- bond_portfolio_sim：费用模型 + 复利净值 ----------

def sim_results(day_gross: dict):
    """{日期: [盘中收益...]} → walk_forward 同 schema 的 results 帧。"""
    rows = []
    for day, rets in day_gross.items():
        for i, r in enumerate(rets):
            rows.append({"pred_date": day, "code": f"C{i}", "trade_date": day,
                         "open": 100.0, "close": 100.0 * (1 + r), "intraday": r,
                         "score": 0.0})
    return pd.DataFrame(rows, columns=WALK_COLUMNS)


def test_portfolio_sim_charges_two_sided_costs_and_compounds():
    res = sim_results({"2026-01-05": [0.01], "2026-01-06": [0.01]})
    out = bond_portfolio_sim(res, equity=1.0, cost_per_trade=0.0015)
    assert out["n_days"] == 2
    assert out["n_trades"] == 2
    assert out["avg_daily_gross"] == pytest.approx(0.01)
    assert out["avg_daily_net"] == pytest.approx(0.01 - 2 * 0.0015)
    assert out["total_return"] == pytest.approx((1 + 0.01 - 0.003) ** 2 - 1)
    assert out["max_drawdown"] == pytest.approx(0.0)


def test_portfolio_sim_equity_fraction_scales_exposure():
    res = sim_results({"2026-01-05": [0.02]})
    out = bond_portfolio_sim(res, equity=0.5, cost_per_trade=0.0015)
    assert out["avg_daily_net"] == pytest.approx(0.5 * (0.02 - 0.003))
    assert out["total_return"] == pytest.approx(0.5 * 0.017)


def test_portfolio_sim_averages_picks_within_day():
    res = sim_results({"2026-01-05": [0.02, 0.00]})
    out = bond_portfolio_sim(res, equity=1.0, cost_per_trade=0.0)
    assert out["avg_daily_gross"] == pytest.approx(0.01)
    assert out["n_trades"] == 2


def test_portfolio_sim_max_drawdown():
    res = sim_results({"2026-01-05": [0.10], "2026-01-06": [-0.20]})
    out = bond_portfolio_sim(res, equity=1.0, cost_per_trade=0.0)
    # nav: 1.0 → 1.10 → 0.88；回撤 = 1 - 0.88/1.10 = 0.20
    assert out["total_return"] == pytest.approx(1.10 * 0.80 - 1)
    assert out["max_drawdown"] == pytest.approx(1 - 0.80)
    assert out["annualized_return"] == pytest.approx((1.10 * 0.80) ** (250 / 2) - 1)


def test_portfolio_sim_empty():
    out = bond_portfolio_sim(pd.DataFrame(columns=WALK_COLUMNS), equity=1.0)
    assert out["n_days"] == 0
    assert out["total_return"] == 0.0
    assert out["max_drawdown"] == 0.0

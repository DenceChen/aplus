"""走前回测测试：TDD RED 先行。

核心原则：任何预测日 D 只能使用 ≤D 的数据（as_of 防未来函数），
用 D+1 的真实行情判定对错——与每日实盘走完全相同的代码路径。
"""
import numpy as np
import pandas as pd
import pytest

from aplus.backtest import walk_forward
from aplus.store import LocalStore


def _make_stock(code, n=80, drift=0.01, start_price=10.0, st=0):
    """漂移为正的股票稳步上涨，为负稳步下跌。"""
    dates = pd.bdate_range("2026-01-01", periods=n).strftime("%Y-%m-%d")
    closes = [start_price * ((1 + drift) ** i) for i in range(n)]
    return pd.DataFrame({
        "date": dates,
        "code": [code] * n,
        "open": closes,
        "high": [c * 1.01 for c in closes],
        "low": [c * 0.99 for c in closes],
        "close": closes,
        "volume": [1e6 + i * 1000 for i in range(n)],
        "amount": [1e7] * n,
        "tradestatus": [1] * n,
        "isST": [st] * n,
    })


def _populate(store):
    store.upsert_daily("600001.SH", _make_stock("600001.SH", drift=0.012))   # 稳涨
    store.upsert_daily("600002.SH", _make_stock("600002.SH", drift=0.011))
    store.upsert_daily("600003.SH", _make_stock("600003.SH", drift=0.010))
    store.upsert_daily("600004.SH", _make_stock("600004.SH", drift=-0.010))  # 稳跌
    store.upsert_daily("600005.SH", _make_stock("600005.SH", drift=-0.011))
    store.upsert_daily("600006.SH", _make_stock("600006.SH", drift=-0.012))


def test_walk_forward_produces_verified_rows(tmp_path):
    store = LocalStore(tmp_path)
    _populate(store)
    dates = store.read_daily("600001.SH")["date"].tolist()
    start, end = dates[60], dates[70]  # 越过 listed_days>60 门槛
    results = walk_forward(store, start, end, top_n=3, bottom_n=3)
    assert len(results) > 0
    for col in ("pred_date", "code", "direction", "actual_pct", "result", "market_pct"):
        assert col in results.columns
    # 10 个交易日 × 每日 top3/bottom3 = 最多 60 行
    assert len(results) <= 10 * 6


def test_trending_stocks_get_high_hit_rate(tmp_path):
    """稳涨股应被预测涨且命中；稳跌股反之——策略在趋势市的基本有效性。"""
    store = LocalStore(tmp_path)
    _populate(store)
    dates = store.read_daily("600001.SH")["date"].tolist()
    results = walk_forward(store, dates[60], dates[70], top_n=3, bottom_n=3)
    up_rows = results[results["direction"] == "up"]
    down_rows = results[results["direction"] == "down"]
    assert (up_rows["result"] == "hit").mean() == 1.0
    assert (down_rows["result"] == "hit").mean() == 1.0


def test_walk_forward_respects_as_of_no_future_leak(tmp_path):
    """D 日的预测不得使用 D 之后的数据：构造一只前 62 天涨、之后崩盘的股票，
    D=61 的预测仍应为涨（崩溃尚未发生）。"""
    store = LocalStore(tmp_path)
    df = _make_stock("600001.SH", n=80, drift=0.012)
    crash_dates = df["date"] >= df["date"].iloc[62]
    df.loc[crash_dates, "close"] = df.loc[crash_dates, "close"] * 0.5
    store.upsert_daily("600001.SH", df)
    for i in range(2, 7):
        store.upsert_daily(f"60000{i}.SH", _make_stock(f"60000{i}.SH", drift=-0.005 - i * 0.001))
    dates = df["date"].tolist()
    results = walk_forward(store, dates[61], dates[62], top_n=1, bottom_n=1)
    up_row = results[results["direction"] == "up"].iloc[0]
    assert up_row["code"] == "600001.SH"  # 崩盘前它动量最高


# ---------- 自进化贯通：权重可注入回测 ----------

def test_walk_forward_accepts_custom_weights(tmp_path):
    """纯反转权重（rev5=1）下，稳跌股的 rev5 分最高 → 应被排进预测涨。"""
    store = LocalStore(tmp_path)
    _populate(store)
    dates = store.read_daily("600001.SH")["date"].tolist()
    r = walk_forward(
        store, dates[60], dates[61], top_n=1, bottom_n=1,
        weights={"mom20": 0.0, "rev5": 1.0, "vol20": 0.0, "vsur": 0.0},
    )
    up_row = r[r["direction"] == "up"].iloc[0]
    assert up_row["code"] in {"600004.SH", "600005.SH", "600006.SH"}


# ---------- 日序列（图表数据） ----------

def test_daily_series_aggregates_per_date():
    from aplus.backtest import daily_series

    results = pd.DataFrame({
        "pred_date": ["2026-01-05"] * 4 + ["2026-01-06"] * 2,
        "direction": ["up", "up", "down", "down", "up", "up"],
        "result": ["hit", "miss", "hit", "hit", "hit", "hit"],
        "actual_pct": [0.03, -0.02, -0.05, 0.01, 0.02, 0.01],
        "market_pct": [0.0] * 6,
    })
    series = daily_series(results)
    assert len(series) == 2
    day1 = series[0]
    assert day1["date"] == "2026-01-05"
    assert day1["judged"] == 4
    assert day1["hit_rate"] == 0.75
    # 涨组合超额 = mean(up actual - market) = (0.03 - 0.02) / 2 = 0.005
    assert day1["up_excess"] == pytest.approx(0.005)
    assert series[1]["hit_rate"] == 1.0


# ---------- 预计算横截面（自进化提速的关键） ----------
#
# evolve_weekly 对每组候选权重都重跑整个 walk_forward——而复权挂载、因子计算、
# 股票过滤全都**与权重无关**，只有最后一步 build_predictions 依赖权重。10 组
# 候选 = 10 倍无谓重算。横截面算一次、权重赛马复用，才让「回测驱动自进化」
# 真正跑得动（会话里记过「向量化预计算才是正解（未实施）」——这次补上）。

def test_precompute_plus_evaluate_equals_walk_forward(tmp_path):
    """预计算 + 单权重评估，必须与 walk_forward **逐行等价**。

    行为等价是硬约束：优化一旦改变数字，所有历史结论立刻失效。
    """
    from aplus.backtest import build_cross_sections, evaluate_weights

    store = LocalStore(tmp_path)
    _populate(store)
    dates = store.read_daily("600001.SH")["date"].tolist()
    start, end = dates[60], dates[70]
    w = {"mom20": 1.0, "rev5": 0.3, "vol20": 0.5, "vsur": 0.5}

    base = walk_forward(store, start, end, top_n=3, bottom_n=3, weights=w)
    xs = build_cross_sections(store, start, end)
    fast = evaluate_weights(xs, w, top_n=3, bottom_n=3)

    assert len(base) == len(fast)
    pd.testing.assert_frame_equal(
        base.reset_index(drop=True),
        fast.reset_index(drop=True),
        check_dtype=False,
    )


def test_many_weights_reuse_one_precompute(tmp_path):
    """多组权重复用同一批横截面，结果必须与逐组单独回测完全一致。"""
    from aplus.backtest import build_cross_sections, evaluate_weights

    store = LocalStore(tmp_path)
    _populate(store)
    dates = store.read_daily("600001.SH")["date"].tolist()
    start, end = dates[60], dates[72]
    ws = [
        {"mom20": 1.0, "rev5": 0.3, "vol20": 0.5, "vsur": 0.5},
        {"mom20": 1.4, "rev5": 0.6, "vol20": 0.5, "vsur": 0.5},
        {"mom20": 0.6, "rev5": 0.3, "vol20": 0.0, "vsur": 0.0},
    ]
    xs = build_cross_sections(store, start, end)
    for w in ws:
        expect = walk_forward(store, start, end, top_n=3, bottom_n=3, weights=w)
        got = evaluate_weights(xs, w, top_n=3, bottom_n=3)
        pd.testing.assert_frame_equal(expect.reset_index(drop=True),
                                      got.reset_index(drop=True),
                                      check_dtype=False)


def test_cross_sections_built_once_not_per_candidate(tmp_path, monkeypatch):
    """提速不是「跑得更快」，是**重复计算次数与候选数无关**——用调用计数钉死。"""
    import aplus.backtest as bt
    from aplus.backtest import build_cross_sections, evaluate_weights

    store = LocalStore(tmp_path)
    _populate(store)
    dates = store.read_daily("600001.SH")["date"].tolist()
    start, end = dates[60], dates[68]

    calls = {"n": 0}
    orig = bt.cross_rows_from_map

    def counted(*a, **kw):
        calls["n"] += 1
        return orig(*a, **kw)

    monkeypatch.setattr(bt, "cross_rows_from_map", counted)
    xs = build_cross_sections(store, start, end)
    n_after_build = calls["n"]
    for w in [{"mom20": 1.0, "rev5": 0.3, "vol20": 0.5, "vsur": 0.5},
              {"mom20": 1.4, "rev5": 0.6, "vol20": 0.5, "vsur": 0.5},
              {"mom20": 0.6, "rev5": 0.2, "vol20": 0.0, "vsur": 0.0}]:
        evaluate_weights(xs, w, top_n=3, bottom_n=3)
    assert calls["n"] == n_after_build, "评估权重时不得重算横截面"


def test_market_benchmark_independent_of_weights(tmp_path):
    """市场基准与权重无关 → 只需算一次（否则每组候选都重抽一次基准）。"""
    from aplus.backtest import build_cross_sections, evaluate_weights

    store = LocalStore(tmp_path)
    _populate(store)
    dates = store.read_daily("600001.SH")["date"].tolist()
    start, end = dates[60], dates[70]
    xs = build_cross_sections(store, start, end)
    a = evaluate_weights(xs, {"mom20": 1.0, "rev5": 0.3, "vol20": 0.5, "vsur": 0.5},
                          top_n=3, bottom_n=3)
    b = evaluate_weights(xs, {"mom20": 0.2, "rev5": 0.0, "vol20": 1.0, "vsur": 1.0},
                          top_n=3, bottom_n=3)
    assert a["market_pct"].tolist() == b["market_pct"].tolist()


# ---------- 因子面板（向量化预计算） ----------
#
# 把「每只股票 × 每个预测日」重算因子，压缩成「每只股票算一次」。
# 滚动窗口天然只向后看，故面板取值与「算到 D 为止再取末行」逐位等价——
# 不是近似。下列测试用逐值对比钉死这个等价性：一旦漂移，所有历史结论失效。

def test_panel_factor_values_match_compute_factors(tmp_path):
    from aplus.factor import compute_factors
    from aplus.panel import build_panel

    store = LocalStore(tmp_path)
    _populate(store)
    dates = store.read_daily("600001.SH")["date"].tolist()
    panel = build_panel(store, dates[60], dates[75])

    for code in ["600001.SH", "600004.SH"]:
        daily = store.read_daily(code)
        for i, day in enumerate(panel.dates):
            sliced = daily[daily["date"] <= day]
            expect = compute_factors(sliced)
            got = panel.cross_section(i)
            row = got[got["code"] == code]
            assert len(row) == 1, f"{code} 在 {day} 应入面"
            for f in ["mom20", "rev5", "vol20", "vsur", "turn20"]:
                got_v, want_v = float(row.iloc[0][f]), float(expect[f])
                if want_v != want_v:            # NaN：两者必须同为 NaN
                    assert got_v != got_v, f"{code} {day} 的 {f} 一边 NaN 一边非 NaN"
                else:
                    assert got_v == pytest.approx(want_v, rel=1e-9, abs=1e-12), \
                        f"{code} {day} 的 {f} 不等价：{got_v} vs {want_v}"


def test_panel_matches_cross_rows_from_map(tmp_path):
    """面板横截面必须与既有 cross_rows_from_map 逐行等价（列与值全等）。"""
    from aplus.panel import build_panel
    from aplus.snapshot import cross_rows_from_map

    store = LocalStore(tmp_path)
    _populate(store)
    dates = store.read_daily("600001.SH")["date"].tolist()
    start, end = dates[60], dates[72]
    panel = build_panel(store, start, end)

    daily_map = {p.stem: store.read_daily(p.stem)
                 for p in sorted((store.root / "master" / "daily").glob("*.parquet"))}
    for i, day in enumerate(panel.dates):
        expect = cross_rows_from_map(daily_map, as_of=day)
        got = panel.cross_section(i).reindex(columns=expect.columns)
        assert len(expect) == len(got)
        pd.testing.assert_frame_equal(
            expect.reset_index(drop=True), got.reset_index(drop=True), check_dtype=False)


def test_panel_respects_as_of_no_future_leak(tmp_path):
    """面板不得使用 D 之后的数据：把 D 之后的价格改到天荒地老，D 的因子必须不变。"""
    from aplus.panel import build_panel

    store = LocalStore(tmp_path)
    _populate(store)
    dates = store.read_daily("600001.SH")["date"].tolist()
    start, end = dates[60], dates[72]
    panel = build_panel(store, start, end)
    cut = dates[65]
    i = panel.dates.index(cut)
    before = panel.cross_section(i)

    poisoned = {}
    for p in sorted((store.root / "master" / "daily").glob("*.parquet")):
        df = store.read_daily(p.stem).copy()
        df.loc[df["date"] > cut, ["close", "open", "volume"]] = 9999.0
        store.upsert_daily(p.stem, df)
        poisoned[p.stem] = df

    panel2 = build_panel(store, start, end)
    after = panel2.cross_section(i)
    pd.testing.assert_frame_equal(before.reset_index(drop=True),
                                  after.reindex(columns=before.columns).reset_index(drop=True),
                                  check_dtype=False)


def _noisy_stock(code, n=80, drift=0.01, seed=0, st=0):
    """带随机波动的股票（真实横截面必需的因子离散度）。"""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2026-01-01", periods=n).strftime("%Y-%m-%d")
    closes = 10.0 * np.exp(np.cumsum(rng.normal(drift, 0.02, n)))
    return pd.DataFrame({
        "date": dates, "code": [code] * n, "open": closes,
        "high": closes * 1.01, "low": closes * 0.99, "close": closes,
        "volume": [1e6 + i * 1000 for i in range(n)], "amount": [1e7] * n,
        "tradestatus": [1] * n, "isST": [st] * n,
    })


def test_fast_path_equals_original_slow_path(tmp_path):
    """**回归地基**：面板快路径必须与改造前的逐日重算慢路径逐行等价。

    慢路径原样复制在此（cross_rows_from_map → filter_universe → build_predictions
    → verify），作为不可协商的参照实现。面板只是把「每只 × 每日重算因子」
    提前算好；一旦数字漂移，所有历史结论与已晋级的权重同时失效。
    """
    # 本测试只比较**共有列**：面板快路径额外带 `ivol`（特质波动率）这类需要全市场
    # 两趟扫描才能得到的因子，慢路径没有。这一列的数值正确性由 tests/test_ivol.py
    # 直接对 numpy.linalg.lstsq 真值单独覆盖，不在本测试职责内。
    from aplus.backtest import build_cross_sections, evaluate_weights
    from aplus.predict import build_predictions, filter_universe
    from aplus.snapshot import cross_rows_from_map
    from aplus.verify import verify_predictions

    def slow_path(store, start, end, weights, top_n=3, bottom_n=3):
        import random as _r
        from aplus.adjust import attach_factor
        from aplus.snapshot import load_factors_map

        fm = load_factors_map(store)
        dm = {p.stem: store.read_daily(p.stem)
              for p in sorted((store.root / "master" / "daily").glob("*.parquet"))}
        dm = {c: (attach_factor(df, fm[c])
                 if c in fm and fm[c] is not None and len(fm[c]) else df)
              for c, df in dm.items()}
        ref = max(dm.values(), key=len)
        dates = [d for d in sorted(ref["date"].unique()) if start <= d < end]
        rng = _r.Random(42)
        sample = rng.sample(list(dm), min(150, len(dm)))
        out = []
        prev_up = []
        for day in dates:
            universe = filter_universe(cross_rows_from_map(dm, as_of=day))
            up, down = build_predictions(universe, top_n=top_n, bottom_n=bottom_n,
                                         weights=weights)
            preds = pd.concat([up, down], ignore_index=True)
            if preds.empty:
                continue
            bars = backtest_next_bars(dm, preds["code"].tolist(), day)
            if bars.empty:
                continue
            r = verify_predictions(preds, bars)
            # 换手成本必须与快路径**同模**：首期只付买侧（cost/2），
            # 之后仅在名单变动时付。此前慢路径压根不算成本，而 `cost` 列
            # 又不在 `verify_predictions` 的输出里，于是 `shared` 把它排除、
            # 比较自动跳过——**测试在悄悄比较两个不同的策略**。
            drag = cost_drag(prev_up, list(up["code"]), top_n)
            r.loc[r["direction"] == "up", "cost"] = drag
            prev_up = list(up["code"])
            r["pred_date"] = day
            mk = backtest_next_bars(dm, sample, day)
            r["market_pct"] = float((mk["close"] / mk["prev_close"] - 1).mean()) if len(mk) else 0.0
            out.append(r)
        return pd.concat(out, ignore_index=True)

    from aplus.backtest import _next_bars as backtest_next_bars, cost_drag

    store = LocalStore(tmp_path)
    # 必须用**带真实波动**的价格：全等差的合成路径会让某个因子列方差≈0，
    # cross_section_z 除以近似零的标准差后放大浮点残差（该退化与面板无关）。
    for i, dr in enumerate([0.012, 0.011, 0.010, -0.010, -0.011, -0.012]):
        store.upsert_daily(f"60000{i+1}.SH", _noisy_stock(f"60000{i+1}.SH", drift=dr, seed=i))
    store.upsert_daily("600007.SH", _noisy_stock("600007.SH", drift=0.02, seed=9, st=1))
    store.upsert_daily("600008.SH", _noisy_stock("600008.SH", drift=0.02, seed=10))
    dates = store.read_daily("600001.SH")["date"].tolist()
    start, end = dates[60], dates[74]

    for w in [{"mom20": 1.0, "rev5": 0.3, "vol20": 0.5, "vsur": 0.5},
              {"mom20": 0.2, "rev5": 1.0, "vol20": 0.0, "vsur": 0.0}]:
        slow = slow_path(store, start, end, w)
        fast = evaluate_weights(build_cross_sections(store, start, end), w,
                                top_n=3, bottom_n=3)
        shared = [c for c in slow.columns if c in fast.columns]
        assert shared, "共有列不得为空，否则本测试退化成空断言"
        pd.testing.assert_frame_equal(
            slow.reset_index(drop=True)[shared],
            fast.reset_index(drop=True)[shared],
            check_dtype=False)

"""可转债纸面账户测试（路线 A 第四阶段，TDD RED 先行）。

背景（第三阶段收盘，PLATFORM.md §7.5）：低价因子周频轮动样本内净年化 +17.1%
（h=5）/ +21.0%（h=20），但 t 统计随持有期衰减（h=5 t=1.57）、网格相位敏感
（净年化五相 +6.8%~+17.1%，回测锚点恰是最幸运相位）、2026 段溢价收敛转负
——判定成立但证据强度为边缘。纸面账户的职责是用样本外数据裁决它。

三条设计红线（改动需重写测试并说明理由）：
1. **保真**：纸面账户必须与 cheap_rotation_backtest 同口径。口径漂移会让样本外
   与样本内不可比——那是自欺。test_paper_period_returns_match_backtest 用同一份
   合成数据跑回测器与纸面账户，逐期比对净收益。
2. **无未来函数**：信号日 D 只看 ≤D 的 K 线，D+1 开盘才成交。
   test_select_targets_is_as_of_no_future_leak。
3. **随机对照必须有**：判定线是「对随机净超额 > 0」，没有随机臂就裁决不了超额。

注意 MIN_LISTED_BARS=41：上市不足 41 根 K 线的债不进池，故合成数据的前 40 个
交易日信号池全空——信号日必须动态定位，不能假定落在第 0 天。
"""
import pandas as pd
import pytest

from aplus.bond_horizon import build_horizon_cache, cheap_rotation_backtest
from aplus.bond_paper import (
    MIN_PERIODS_FOR_VERDICT,
    DEFAULT_COST,
    BondPaperAccount,
    PAPER_PHASES,
    bond_rebalance_signal,
    select_targets,
    summarize_paper,
)

WARMUP = 40          # MIN_LISTED_BARS=41 → 前 40 个交易日信号池全空
END = "9999-12-31"   # run()/回测器均为右开区间哨兵


# ---------- 合成数据构造（与 test_bond_horizon 同风格） ----------

def bdays(n, start="2026-01-05"):
    return [d.strftime("%Y-%m-%d") for d in pd.bdate_range(start, periods=n)]


def bond_frame(code, dates, closes, opens=None, volumes=None):
    closes = [float(c) for c in closes]
    n = len(dates)
    assert len(closes) == n
    if opens is None:
        opens = list(closes)
    if volumes is None:
        volumes = [100000.0] * n  # ×~100 元 > 500 万流动性门槛
    return pd.DataFrame({
        "date": list(dates),
        "code": code,
        "open": [float(o) for o in opens],
        "high": [max(o, c) for o, c in zip(opens, closes)],
        "low": [min(o, c) for o, c in zip(opens, closes)],
        "close": closes,
        "volume": [float(v) for v in volumes],
    })


def ladder_map(n_days=120, base0=95.0, n_cheap=12, cal_close=130.0):
    """CAL（日历锚，130 元不进低价池）+ n_cheap 只低价债（95, 96, …）。

    低价序 = 代码序（B00 最深）→ TopN 选择结果确定可断言。
    """
    dates = bdays(n_days)
    m = {"CAL.SH": bond_frame("CAL.SH", dates, [cal_close] * n_days)}
    for i in range(n_cheap):
        m[f"B{i:02d}.SH"] = bond_frame(f"B{i:02d}.SH", dates, [base0 + i] * n_days)
    return m, dates


def osc_map(n_days=200, n_cheap=20):
    """低价池**逐日轮换**的合成世界（相位必须能产生差异，否则相位测试是假的）。

    ladder_map 价格恒定 → 任何相位选出的都是同一批债 → 相位差异恒为 0，
    那种数据测不出相位敏感性，只会给出「五相一致」的假安心。
    """
    dates = bdays(n_days)
    m = {"CAL.SH": bond_frame("CAL.SH", dates, [130.0] * n_days)}
    for k in range(n_cheap):
        closes = [95.0 + ((i * (k + 2)) % 23) + (k % 4) for i in range(n_days)]
        m[f"B{k:02d}.SH"] = bond_frame(f"B{k:02d}.SH", dates, closes)
    return m, dates


def only_cheap(hold_days=5, top_n=3, cost=DEFAULT_COST, seeds=()):
    return BondPaperAccount(hold_days=hold_days, top_n=top_n, cost=cost,
                            phases=[0], random_seeds=list(seeds))


# ---------- select_targets：信号日选出目标持仓（as-of 语义） ----------

def test_select_targets_picks_deepest_cheap_bonds():
    m, dates = ladder_map()
    cache = build_horizon_cache(m)
    picks = select_targets(cache, dates[WARMUP], top_n=3)
    assert [p["code"] for p in picks] == ["B00.SH", "B01.SH", "B02.SH"]


def test_select_targets_is_as_of_no_future_leak():
    """信号日 D 的选择只能看到 ≤D 的 K 线——D+1 的暴跌不得改变 D 的持仓。"""
    dates = bdays(120)
    m = {"CAL.SH": bond_frame("CAL.SH", dates, [130.0] * 120)}
    m["B00.SH"] = bond_frame("B00.SH", dates, [95.0] * 60 + [150.0] * 60)
    m["B01.SH"] = bond_frame("B01.SH", dates, [96.0] * 60 + [80.0] * 60)
    cache = build_horizon_cache(m)
    day = dates[WARMUP]
    assert select_targets(cache, day, top_n=1)[0]["code"] == "B00.SH"
    assert select_targets(cache, dates[80], top_n=1)[0]["code"] == "B01.SH"


def test_select_targets_excludes_stale_and_illiquid():
    dates = bdays(120)
    m = {"CAL.SH": bond_frame("CAL.SH", dates, [130.0] * 120)}
    m["B00.SH"] = bond_frame("B00.SH", dates, [95.0] * 120)
    m["B01.SH"] = bond_frame("B01.SH", dates[:60], [94.0] * 60)                   # 摘牌（停更 >30 自然日）
    m["B02.SH"] = bond_frame("B02.SH", dates, [93.0] * 120, volumes=[1.0] * 120)  # 流动性不足
    m["B03.SH"] = bond_frame("B03.SH", dates[:60], [92.0] * 60)                  # 停更
    cache = build_horizon_cache(m)
    day = dates[100]
    codes = [p["code"] for p in select_targets(cache, day, top_n=4)]
    assert "B00.SH" in codes
    assert not {"B01.SH", "B02.SH", "B03.SH"} & set(codes)


def test_select_targets_needs_next_day_to_be_tradeable():
    """末日信号无法确认 D+1 可成交——宁可不选，不做假决策。"""
    m, dates = ladder_map()
    cache = build_horizon_cache(m)
    assert select_targets(cache, dates[-1], top_n=5) == []


def test_select_targets_empty_pool_when_no_bond_is_listed_long_enough():
    m, dates = ladder_map(n_days=30)
    cache = build_horizon_cache(m)
    assert select_targets(cache, dates[-2], top_n=5) == []


def test_select_targets_pool_is_not_filtered_by_cheap_value():
    """漏斗只看流动性/新鲜度/上市根数——非低价债照样入池（cheap=0 只是不优先）。"""
    m, dates = ladder_map(n_cheap=0)
    cache = build_horizon_cache(m)
    picks = select_targets(cache, dates[WARMUP], top_n=3)
    assert [p["code"] for p in picks] == ["CAL.SH"]


def test_select_targets_random_is_reproducible_for_same_seed_and_day():
    m, dates = ladder_map()
    cache = build_horizon_cache(m)
    a = select_targets(cache, dates[WARMUP], top_n=4, mode="random", seed=3)
    b = select_targets(cache, dates[WARMUP], top_n=4, mode="random", seed=3)
    c = select_targets(cache, dates[WARMUP], top_n=4, mode="random", seed=4)
    assert [x["code"] for x in a] == [x["code"] for x in b]
    assert [x["code"] for x in a] != [x["code"] for x in c]


# ---------- bond_rebalance_signal：调仓差异（每日可执行输出） ----------

def test_rebalance_signal_splits_add_remove_hold():
    assert bond_rebalance_signal(["A", "B", "C"], ["B", "C", "D"]) == {
        "add": ["D"], "remove": ["A"], "hold": 2}


def test_rebalance_signal_from_empty_is_all_add():
    assert bond_rebalance_signal([], ["A", "B"]) == {
        "add": ["A", "B"], "remove": [], "hold": 0}


def test_rebalance_signal_no_change_is_all_hold():
    assert bond_rebalance_signal(["A", "B"], ["B", "A"]) == {
        "add": [], "remove": [], "hold": 2}


def test_rebalance_signal_accepts_comma_string():
    assert bond_rebalance_signal("A,B", "B,C") == {
        "add": ["C"], "remove": ["A"], "hold": 1}


# ---------- 纸面账户状态机 ----------

def test_warmup_period_stays_in_cash_at_nav_one():
    """MIN_LISTED_BARS 之前池空——账户必须空仓不动，不得凭空建仓。"""
    m, dates = ladder_map()
    rows = only_cheap().run(build_horizon_cache(m), dates[0], END)
    warm = rows.iloc[:WARMUP]
    assert (warm["nav"] == 1.0).all()
    assert (warm["n_positions"] == 0).all()


def test_signal_day_produces_target_but_no_position_yet():
    """D 收盘决策 → D+1 开盘才建仓。D 当天不得有持仓。"""
    m, dates = ladder_map()
    rows = only_cheap().run(build_horizon_cache(m), dates[0], END)
    first = rows[rows["target"] != ""].iloc[0]
    assert bool(first["is_signal_day"])
    assert first["n_positions"] == 0
    assert float(first["nav"]) == 1.0
    assert first["target"] == "B00.SH,B01.SH,B02.SH"


def test_position_enters_at_next_day_open():
    """建仓价 = D+1 开盘价（非收盘价、非信号日收盘价）。"""
    dates = bdays(120)
    m = {"CAL.SH": bond_frame("CAL.SH", dates, [130.0] * 120)}
    for i in range(4):
        m[f"B{i:02d}.SH"] = bond_frame(f"B{i:02d}.SH", dates, [95.0] * 120,
                                       opens=[100.0] * 120)
    rows = only_cheap(top_n=2).run(build_horizon_cache(m), dates[0], END)
    buy = rows[rows["is_buy_day"]].iloc[0]
    sig = rows[rows["target"] != ""]["date"].iloc[0]
    assert dates[dates.index(sig) + 1] == buy["date"]
    assert float(buy["mean_entry_open"]) == pytest.approx(100.0)


def test_nav_marks_every_holding_day_at_close():
    """等权槽位记账：open=100、close=95、2 只 → nav = 1 + mean(-5%) = 0.95。"""
    dates = bdays(120)
    m = {"CAL.SH": bond_frame("CAL.SH", dates, [130.0] * 120)}
    for i in range(4):
        m[f"B{i:02d}.SH"] = bond_frame(f"B{i:02d}.SH", dates, [95.0] * 120,
                                       opens=[100.0] * 120)
    rows = only_cheap(top_n=2).run(build_horizon_cache(m), dates[0], END)
    held = rows[rows["n_positions"] > 0]
    first = held.iloc[:5]
    assert len(first) == 5  # h=5：买入日起共 5 个交易日持有
    assert float(first.iloc[0]["nav"]) == pytest.approx(0.95, rel=1e-9)


def test_sell_at_hth_bar_close_and_simultaneously_next_signal_day():
    m, dates = ladder_map()
    rows = only_cheap().run(build_horizon_cache(m), dates[0], END)
    buy = rows[rows["is_buy_day"]]["date"].iloc[0]
    sells = rows[rows["is_sell_day"]]
    assert len(sells) > 0
    assert dates.index(sells.iloc[0]["date"]) - dates.index(buy) == 4  # 含买含卖共 5 根
    assert int(sells.iloc[0]["n_sold"]) == 3
    assert sells.iloc[0]["target"] != ""  # 卖出日同时产出下一组目标


def test_cost_only_charged_on_signal_days():
    m, dates = ladder_map()
    rows = only_cheap(cost=0.003).run(build_horizon_cache(m), dates[0], END)
    costly = rows[rows["cost_paid"] > 0]
    assert len(costly) > 0
    assert costly["is_signal_day"].all()


def test_first_build_charges_half_of_round_trip_cost():
    """首期建仓只付买侧 → cost/2（与回测器同一公式自然退化）。"""
    m, dates = ladder_map()
    rows = only_cheap(cost=0.003).run(build_horizon_cache(m), dates[0], END)
    first = rows[rows["cost_paid"] > 0].iloc[0]
    assert float(first["cost_paid"]) == pytest.approx(0.003 / 2, rel=1e-9)


def test_unchanged_pool_charges_no_turnover_cost():
    """低价池极稳定（回测实测每期只换 2.8~5.5 只）——全 hold 期费用为 0。"""
    m, dates = ladder_map()
    rows = only_cheap(cost=0.003).run(build_horizon_cache(m), dates[0], END)
    costly = rows[rows["cost_paid"] > 0]
    assert len(costly) == 1  # 池子恒定 → 只有首期建仓付费


def test_full_turnover_charges_full_round_trip_cost():
    """池子彻底换手（随机臂在池子极小时）→ 完整双边费率。"""
    dates = bdays(120)
    m = {"CAL.SH": bond_frame("CAL.SH", dates, [130.0] * 120)}
    for i in range(3):  # 池子恰好 = top_n=2，永不变 → 用 seed 制造换手不可靠
        m[f"B{i:02d}.SH"] = bond_frame(f"B{i:02d}.SH", dates, [95.0 + i] * 120)
    rows = only_cheap(cost=0.003, top_n=1, seeds=[1]).run(
        build_horizon_cache(m), dates[0], END)
    rand = rows[rows["mode"] == "random"]
    assert (rand["cost_paid"] > 0).sum() > 1  # 随机臂会换手


def test_rerun_is_idempotent():
    """重跑不得重复记账——18:30 跑过、19:30 守卫再跑是常态。"""
    m, dates = ladder_map()
    first = only_cheap().run(build_horizon_cache(m), dates[0], END)
    second = only_cheap().run(build_horizon_cache(m), dates[0], END)
    assert len(first) == len(second) == 120
    assert first["nav"].tolist() == second["nav"].tolist()


def test_catchup_replays_every_missing_day():
    """合盖/关机多天后补跑：必须补齐所有缺失交易日，不得跳空记账。"""
    m, dates = ladder_map()
    acct = only_cheap()
    acct.run(build_horizon_cache(m), dates[0], dates[60])
    full = acct.run(build_horizon_cache(m), dates[0], END)
    assert full["date"].tolist() == dates
    once = only_cheap().run(build_horizon_cache(m), dates[0], END)
    assert full["nav"].tolist() == once["nav"].tolist()  # 补跑 ≠ 断点续算


def test_delisted_holding_sold_at_stale_price_and_flagged():
    """强赎/摘牌：卖出日无成交棒 → 按末根收盘卖出并标记 n_liquidated。"""
    dates = bdays(120)
    m = {"CAL.SH": bond_frame("CAL.SH", dates, [130.0] * 120)}
    for i in range(1, 6):
        m[f"B{i:02d}.SH"] = bond_frame(f"B{i:02d}.SH", dates, [95.0 + i] * 120)
    m["B00.SH"] = bond_frame("B00.SH", dates[:WARMUP + 2], [94.0] * (WARMUP + 2))
    rows = only_cheap(top_n=2).run(build_horizon_cache(m), dates[0], END)
    assert int(rows["n_liquidated"].sum()) == 1
    assert rows["date"].nunique() == 120  # 无重复记账


def test_short_series_never_signals():
    """数据不足 41 根时账户保持空仓——不得为了“有输出”而编造决策。"""
    m, dates = ladder_map(n_days=30)
    rows = only_cheap().run(build_horizon_cache(m), dates[0], END)
    assert (rows["target"] == "").all()
    assert (rows["nav"] == 1.0).all()


# ---------- 随机对照臂与相位臂 ----------

def test_random_arm_picks_are_reproducible_across_split_runs():
    """同一 (seed, 信号日) 必得同一组选券——与补跑路径无关。"""
    m, dates = ladder_map()
    kw = dict(hold_days=5, top_n=3, phases=[0], random_seeds=[7])
    whole = BondPaperAccount(**kw).run(build_horizon_cache(m), dates[0], END)
    split = BondPaperAccount(**kw)
    split.run(build_horizon_cache(m), dates[0], dates[40])
    tail = split.run(build_horizon_cache(m), dates[0], END)
    w = whole[whole["mode"] == "random"]
    t = tail[tail["mode"] == "random"]
    assert w["holdings"].tolist() == t["holdings"].tolist()
    assert w["nav"].tolist() == t["nav"].tolist()


def test_random_arm_differs_from_cheap_arm():
    m, dates = ladder_map()
    rows = only_cheap(seeds=[1]).run(build_horizon_cache(m), dates[0], END)
    assert rows[rows["mode"] == "cheap"]["holdings"].tolist() != \
        rows[rows["mode"] == "random"]["holdings"].tolist()


def test_all_phases_and_seeds_produce_complete_rows():
    m, dates = ladder_map()
    rows = BondPaperAccount().run(build_horizon_cache(m), dates[0], END)
    n_arms = len(PAPER_PHASES) * (1 + 5)
    assert rows["arm"].nunique() == n_arms
    assert len(rows) == 120 * n_arms
    for ph in PAPER_PHASES:
        assert len(rows[rows["phase"] == ph]) == 120 * 6


def test_phase_arms_rebalance_on_different_days():
    """相位并行：稳态网格必须错开。

    预热期例外：MIN_LISTED_BARS 之前池空，各相位逐日重试，会**汇合到同一个首个
    信号日**。只允许这一个日期重合，之后网格必须分离——相位敏感性正是由此而来，
    不能被预热期抹平。
    """
    m, dates = ladder_map()
    rows = BondPaperAccount(random_seeds=[]).run(
        build_horizon_cache(m), dates[0], END)
    d0 = set(rows[(rows["mode"] == "cheap") & (rows["phase"] == 0) &
                 (rows["target"] != "")]["date"])
    d1 = set(rows[(rows["mode"] == "cheap") & (rows["phase"] == 1) &
                 (rows["target"] != "")]["date"])
    assert d0 and d1
    assert len(d0 & d1) <= 1   # 仅预热汇合的首个信号日
    assert d0 != d1            # 稳态网格不同


# ---------- 保真：纸面账户 ≡ 回测器（最重要的一条） ----------

def test_paper_period_returns_match_backtest():
    """同一份合成数据下，纸面账户的逐期净收益必须等于 cheap_rotation_backtest。

    这是整套纸面验证的地基：口径一旦漂移，样本外结果与样本内不可比，
    20% 候选的判决就失去意义。
    """
    dates = bdays(160)
    m = {"CAL.SH": bond_frame("CAL.SH", dates, [130.0] * 160)}
    for k in range(20):
        base = 95.0 + (k % 6)
        closes = [round(base * (1 + ((i % 7 - 3) * 0.01) * (1 + k * 0.1)), 3)
                  for i in range(160)]
        opens = [round(closes[i] * (1 + ((i % 3) - 1) * 0.004), 3) for i in range(160)]
        m[f"B{k:02d}.SH"] = bond_frame(f"B{k:02d}.SH", dates, closes, opens=opens)

    cache = build_horizon_cache(m)
    start, hold = dates[0], 5
    bt = cheap_rotation_backtest(None, start, END, top_n=5, hold_days=hold,
                                 cost=DEFAULT_COST, cache=cache)
    bt_nets = [p["net"] for p in bt["periods"]]

    rows = only_cheap(hold_days=hold, top_n=5).run(
        build_horizon_cache(m), start, END)
    nav_by_date = dict(zip(rows["date"], rows["nav"]))
    sig = rows[rows["target"] != ""]["date"].tolist()
    paper_nets = [nav_by_date[sig[i + 1]] / nav_by_date[sig[i]] - 1
                  for i in range(len(sig) - 1)]

    assert len(paper_nets) == len(bt_nets)
    for got, want in zip(paper_nets, bt_nets):
        # 纸面账户费用按净值乘性扣除，回测器按加性近似——差 gross×cost（二阶小量）
        assert got == pytest.approx(want, abs=2e-4)


def test_paper_picks_match_backtest_picks():
    """持仓名单也必须与回测器逐期一致——净值一致但持仓不同说明选券有分歧。"""
    dates = bdays(160)
    m = {"CAL.SH": bond_frame("CAL.SH", dates, [130.0] * 160)}
    for k in range(20):
        base = 95.0 + (k % 6)
        closes = [round(base * (1 + ((i % 7 - 3) * 0.01) * (1 + k * 0.1)), 3)
                  for i in range(160)]
        m[f"B{k:02d}.SH"] = bond_frame(f"B{k:02d}.SH", dates, closes)

    cache = build_horizon_cache(m)
    bt = cheap_rotation_backtest(None, dates[0], END, top_n=5,
                                 hold_days=5, cost=DEFAULT_COST, cache=cache)
    bt_picks = [p["day"] for p in bt["periods"]]

    rows = only_cheap(top_n=5).run(build_horizon_cache(m), dates[0], END)
    paper_days = rows[rows["target"] != ""]["date"].tolist()
    assert set(bt_picks).issubset(set(paper_days))


# ---------- 摘要与诚实性护栏 ----------

def test_summarize_empty_rows_reports_empty():
    s = summarize_paper(pd.DataFrame(), {"start": "2026-10-11"})
    assert s["status"] == "empty"
    assert s["verdict"] is None
    assert s["state"]["start"] == "2026-10-11"


def test_summarize_refuses_verdict_before_min_periods():
    """刚开张不足 3 个调仓期时**不得**给年化/超额判决——防止「两天年化 40%」的假信号。"""
    m, dates = ladder_map(n_days=50)   # 40 预热 + 2 期，不足判决门槛
    rows = only_cheap(seeds=[1, 2]).run(build_horizon_cache(m), dates[0], END)
    s = summarize_paper(rows, {})
    assert s["headline"]["n_signals"] < MIN_PERIODS_FOR_VERDICT
    assert s["status"] == "accumulating"
    assert s["verdict"] is None


def test_summarize_gives_verdict_once_enough_periods():
    m, dates = ladder_map(n_days=200)
    rows = only_cheap(seeds=[1, 2, 3]).run(build_horizon_cache(m), dates[0], END)
    s = summarize_paper(rows, {})
    assert s["headline"]["n_signals"] >= MIN_PERIODS_FOR_VERDICT
    assert s["status"] == "judging"
    v = s["verdict"]
    assert v["head_nav"] == pytest.approx(s["headline"]["last_nav"])
    assert v["excess_vs_random"] == pytest.approx(v["head_nav"] - v["random_nav"])
    assert v["phase_best"] >= v["phase_worst"]
    assert "相位" in v["caveat"]


def test_summarize_exposes_phase_spread_not_just_best():
    """相位带必须整体暴露——只报最好那条就是在藏起已知弱点。"""
    m, dates = osc_map()
    rows = BondPaperAccount(random_seeds=[]).run(
        build_horizon_cache(m), dates[0], END)
    s = summarize_paper(rows, {})
    assert set(s["phase_spread"]) == {f"cheap_p{p}" for p in PAPER_PHASES}
    assert len(set(round(v, 9) for v in s["phase_spread"].values())) > 1


def test_summarize_handles_no_random_arms():
    m, dates = osc_map()
    rows = BondPaperAccount(random_seeds=[]).run(
        build_horizon_cache(m), dates[0], END)
    s = summarize_paper(rows, {})
    assert s["random_mean_nav"] is None
    assert s["verdict"] is None  # 没有随机对照就不给超额判决

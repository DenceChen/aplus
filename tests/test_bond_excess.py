"""样本外超额口径的测试（scripts/bond_excess_analysis.py 的护栏）。

要钉死的东西：
1. **同口径**：`run_rotation` 的 cheap 臂必须与 `cheap_rotation_backtest`
   逐期逐值同义——否则本模块报出的「策略净收益」就不是平台自己的口径，
   超额结论全部失去意义。
2. **防未来数据（本文件重点）**：信号只用信号日 D 收盘可得，成交一律 D+1。
   用三条互相独立的办法钉：
   - 截断数据重跑，历史前缀结果必须逐值不变（证明没有全样本统计量
     污染历史某一天）
   - 追加一根「未来暴跌 K 线」，历史信号日的选券必须不变
   - 构造一个**全窗口标准化会翻转结论**的数据集，断言流水线仍然只按
     点时刻 cheap 选券，不接受泄露口径
3. **超额符号/几何口径**：负超额必须报负，train×test 连乘要能还原全窗口。
"""
import numpy as np
import pandas as pd
import pytest

from aplus.bond_horizon import (
    build_horizon_cache,
    cheap_rotation_backtest,
)
from aplus.bond_excess import (
    excess_of,
    run_rotation,
    select_cheap_top_n,
    select_full_pool,
)

VOL = 100000.0  # 量 10 万 × ~100 元 > 500 万流动性门槛


def bdays(n, start="2026-01-05"):
    return [d.strftime("%Y-%m-%d") for d in pd.bdate_range(start, periods=n)]


def bond_frame(code, dates, closes, opens=None, volumes=None):
    closes = [float(c) for c in closes]
    opens = list(closes) if opens is None else [float(o) for o in opens]
    volumes = [VOL] * len(dates) if volumes is None else [float(v) for v in volumes]
    return pd.DataFrame({
        "date": list(dates), "code": code,
        "open": opens,
        "high": [max(o, c) for o, c in zip(opens, closes)],
        "low": [min(o, c) for o, c in zip(opens, closes)],
        "close": closes, "volume": volumes,
    })


def ladder_map(n_days=60, base0=95.0, n_cheap=12, cal_close=130.0,
               opens=None, tail=None):
    """CAL（130 元日历锚）+ n_cheap 只低价债（95, 96, …），低价序 = 代码序。

    `tail` 可给 {code: closes_tail} 追加一段更晚的价格（用于制造未来暴跌）。
    """
    dates = bdays(n_days)
    m = {"CAL.SH": bond_frame("CAL.SH", dates, [cal_close] * n_days, opens=opens)}
    for i in range(n_cheap):
        m[f"B{i:02d}.SH"] = bond_frame(f"B{i:02d}.SH", dates,
                                       [base0 + i] * n_days, opens=opens)
    for code, extra in (tail or {}).items():
        m[code] = pd.concat([m[code], extra], ignore_index=True)
    return m, dates


def _digest(r):
    """把一次回测压成可比对的摘要（净值 + 逐期选券）。"""
    return (r["total_return"], r["annualized_return"], r["n_days"],
            r["n_periods"],
            [(p["day"], round(p["net"], 12), tuple(p["picks"])) for p in r["periods"]])


# ---------- 1. 同口径：与平台自己的回测器逐值同义 ----------

def test_run_rotation_matches_bond_horizon_engine():
    m, dates = ladder_map(n_days=60, n_cheap=12)
    cache = build_horizon_cache(m)
    start, end = dates[0], dates[-1]
    for top_n, hold in ((10, 5), (3, 1), (5, 3)):
        ref = cheap_rotation_backtest(None, start, end, top_n=top_n, hold_days=hold,
                                      cost=0.003, cache=cache)
        mine = run_rotation(cache, start, end, select_cheap_top_n(top_n),
                            hold_days=hold, cost=0.003)
        assert _digest(mine) == _digest(ref), f"top{top_n}/h{hold} 与平台引擎不同义"


def test_benchmark_arm_holds_whole_pool():
    m, dates = ladder_map(n_days=60, n_cheap=12)
    cache = build_horizon_cache(m)
    r = run_rotation(cache, dates[0], dates[-1], select_full_pool,
                     hold_days=5, cost=0.003)
    assert r["n_periods"] > 0
    # 基准臂每期持有的是**全池**（12 只低价 + CAL = 13）
    assert all(p["n_picks"] == 13 for p in r["periods"])
    assert r["mean_n_picks"] == 13


# ---------- 2a. 防泄露：截断数据重跑，历史前缀逐值不变 ----------

def test_truncated_data_gives_identical_prefix():
    """把数据截到 CUT 之后重跑，[start, END) 的结果必须与全量数据逐值相同。

    这是「不得用全样本统计量标准化历史某一天」的可执行反证：若引擎偷偷用了
    全窗口均值/标准差，截断会改变全窗口统计量 → 历史某一天的信号/收益随之改变
    → 本断言失败。
    """
    m, dates = ladder_map(n_days=120, n_cheap=12)
    full = build_horizon_cache(m)

    start, end = dates[45], dates[80]   # 历史前缀区间（信号日 pos ≥ 41，池非空）
    cut = dates[95]                     # 截断点（> end，留足持有期）

    truncated_map = {c: df[df["date"] <= cut].reset_index(drop=True)
                     for c, df in m.items()}
    trunc = build_horizon_cache(truncated_map)

    for arm in (select_cheap_top_n(10), select_full_pool):
        a = run_rotation(full, start, end, arm, hold_days=5, cost=0.003)
        b = run_rotation(trunc, start, end, arm, hold_days=5, cost=0.003)
        assert _digest(a) == _digest(b), "截断数据改变了历史区间结果 → 存在未来数据泄露"


def test_truncated_excess_is_identical_to_full():
    """超额也一样：全量与截断在同区间上必须给出同一个超额。"""
    m, dates = ladder_map(n_days=120, n_cheap=12)
    full = build_horizon_cache(m)
    start, end = dates[45], dates[80]
    cut = dates[95]
    trunc = build_horizon_cache({c: df[df["date"] <= cut].reset_index(drop=True)
                                 for c, df in m.items()})

    def _exc(cache):
        s = run_rotation(cache, start, end, select_cheap_top_n(10), 5, 0.003)
        b = run_rotation(cache, start, end, select_full_pool, 5, 0.003)
        return excess_of(s, b)["excess_total"]

    a, b_ = _exc(full), _exc(trunc)
    assert a is not None and b_ is not None, "夹具失效：区间内没有任何非空池期"
    assert a == pytest.approx(b_, abs=0.0)


# ---------- 2b. 防泄露：追加未来暴跌 K 线，历史信号不变 ----------

def test_future_crash_bar_cannot_change_earlier_picks():
    """给一只债在信号日之后追加暴跌 K 线，信号日的选券必须纹丝不动。

    选券只看信号日 D 收盘的 cheap 点值；未来暴跌只会抬高未来的 cheap，
    若实现里存在任何「全窗口最便宜」式的排序，这只债会被错误地选进来。

    注意信号日必须落在 index ≥ MIN_LISTED_BARS(41) 之后，否则池为空、
    本测试会静默变成空断言。
    """
    n = 90
    dates = bdays(n)
    # CRASH 信号日 80 元（cheap=35，全池最便宜→必被选中），之后崩到 15 元
    m = {"CAL.SH": bond_frame("CAL.SH", dates, [130.0] * n)}
    m["CRASH.SH"] = bond_frame("CRASH.SH", dates,
                               [80.0] * 50 + [15.0] * (n - 50))
    for i in range(12):
        m[f"B{i:02d}.SH"] = bond_frame(f"B{i:02d}.SH", dates, [95.0 + i] * n)

    start, end = dates[45], dates[80]  # 段内所有信号日 pos ≥ 41
    full = build_horizon_cache(m)
    picks = run_rotation(full, start, end, select_cheap_top_n(10),
                         hold_days=5, cost=0.003)["periods"][0]["picks"]
    assert picks, "夹具失效：段内没有任何非空池期"
    assert "CRASH.SH" in picks, \
        "本夹具应让 CRASH.SH 在信号日就是最低价（cheap 最大）"

    # 截掉暴跌段重建缓存：未来数据不存在时，同一信号日的选券必须完全一致
    clean_map = {c: df[df["date"] <= dates[60]].reset_index(drop=True)
                 for c, df in m.items()}
    clean = build_horizon_cache(clean_map)
    picks_clean = run_rotation(clean, start, end, select_cheap_top_n(10),
                               hold_days=5, cost=0.003)["periods"][0]["picks"]
    assert picks_clean == picks, "未来暴跌 K 线改变了历史信号日的选券 → 未来数据泄露"


# ---------- 2c. 防泄露：全窗口标准化会翻转结论，流水线不接受它 ----------

def test_global_standardization_would_flip_picks_but_pipeline_does_not():
    """构造「用全窗口统计量标准化」与「点时刻 cheap」结论相反的数据。

    设计：信号日 D 那天，C 券 120 元（最贵、cheap=0，本不该选）；D 之后 C 崩到
    10 元并保持 → 全窗口口径下 C 的均值/标准差把 C 排到最便宜 → 泄露实现会选
    C。点时刻口径下 C 的 cheap=0，必须**不选** C。
    断言：(a) 两种口径确实给出不同的选券（否则本夹具无鉴别力）；
          (b) 平台流水线给的是点时刻结果。
    """
    n = 90
    dates = bdays(n)
    # CAL 112 元（cheap=3，与 C 的 0 不撞值），同时充当日历锚
    m = {"CAL.SH": bond_frame("CAL.SH", dates, [112.0] * n)}
    m["A.SH"] = bond_frame("A.SH", dates, [90.0] * n)   # 信号日 cheap=25
    m["B.SH"] = bond_frame("B.SH", dates, [88.0] * n)   # 信号日 cheap=27（最便宜）
    # C 信号日最贵（120→cheap=0），index 50 之后崩到 10
    m["C.SH"] = bond_frame("C.SH", dates,
                           [120.0] * 50 + [10.0] * (n - 50))
    cache = build_horizon_cache(m)

    signal_day = dates[45]  # pos=46 ≥ 41，池非空
    from aplus.bond_horizon import _pool_of
    pool = _pool_of(cache, signal_day)
    assert len(pool) == 4, f"夹具失效：信号日池应含 CAL/A/B/C，实际 {len(pool)}"

    point_in_time = select_cheap_top_n(2)(pool)             # 平台口径（top2 才有鉴别力）
    assert point_in_time == ["B.SH", "A.SH"], \
        f"点时刻口径下应是 B(27)→A(25)，实际 {point_in_time}"

    # 泄露口径：用**全窗口**（整段，含未来）的均值/标准差把 cheap 标准化，
    # z = (cheap - 全窗口均值)/全窗口标准差，z 越小越便宜。
    # C 的未来暴跌把全窗口均值抬到 24.7、C 的标准差撑到 52，于是 C 当下的
    # cheap=0 被当成全池「最便宜」——而它随后正好崩掉。
    all_cheap = np.concatenate([cache["frames"][cd][4] for cd, _c, _p in pool])
    gm, gs = float(all_cheap.mean()), float(all_cheap.std())
    pos_of = {cd: pos for cd, _c, pos in pool}

    def _z(cd):
        return (float(cache["frames"][cd][4][pos_of[cd] - 1]) - gm) / (gs or 1.0)

    global_picks = sorted(pos_of, key=lambda cd: (_z(cd), cd))[:2]  # z 升序 = 最便宜

    # (a) 夹具有鉴别力：全窗口标准化确实会翻转选券（把要崩的 C 选进来）
    assert "C.SH" in global_picks, \
        f"夹具失效：全窗口标准化没能翻转选券（实际 {global_picks}），本测试将无法证明防泄露"
    assert set(global_picks) != set(point_in_time)

    # (b) 平台流水线给的是点时刻结果，且不受未来段影响
    clean = build_horizon_cache({c: df[df["date"] <= dates[60]].reset_index(drop=True)
                                 for c, df in m.items()})
    assert select_cheap_top_n(2)(_pool_of(clean, signal_day)) == point_in_time


# ---------- 3. 超额口径：符号、几何还原 ----------

def test_excess_sign_and_geometric_linking():
    m, dates = ladder_map(n_days=120, n_cheap=12)
    cache = build_horizon_cache(m)

    def _pair(start, end):
        s = run_rotation(cache, start, end, select_cheap_top_n(10), 5, 0.003)
        b = run_rotation(cache, start, end, select_full_pool, 5, 0.003)
        return s, b, excess_of(s, b)

    s, b, x = _pair(dates[0], dates[-1])
    # 符号：超额就是策略净值 / 基准净值 − 1
    assert x["excess_total"] == pytest.approx(
        (1 + s["total_return"]) / (1 + b["total_return"]) - 1.0)
    if x["excess_total"] > 0:
        assert x["verdict"].startswith("超额为正")
    else:
        assert x["verdict"].startswith("超额为负")

    # 几何还原：train × test 两段净值连乘 ≈ 全窗口净值（网格边界会差几根）
    tr = _pair(dates[0], dates[60])
    te = _pair(dates[65], dates[-1])
    nav = ((1 + tr[0]["total_return"]) * (1 + te[0]["total_return"])
           * (1 + tr[1]["total_return"]) ** -1 * (1 + te[1]["total_return"]) ** -1)
    assert nav == pytest.approx(1 + x["excess_total"], abs=0.25)


def test_excess_sign_when_benchmark_collapses():
    """基准腰斩、策略腰斩得少时：超额必须为**正**（几何口径的正确符号）。

    s=-50% / b=-90% → (0.5/0.1)-1 = +4.0。这是「亏得比别人少」，
    符号不能因为两者都为负就报成负超额。
    """
    s = {"total_return": -0.5, "annualized_return": -0.4, "net_per_period": -0.01,
         "n_days": 250, "n_periods": 10}
    b = {"total_return": -0.9, "annualized_return": -0.9, "net_per_period": -0.02,
         "n_days": 250, "n_periods": 10}
    x = excess_of(s, b)
    assert x["excess_total"] == pytest.approx(4.0)
    assert x["verdict"].startswith("超额为正")


def test_excess_total_minus_one_is_not_defined():
    """基准亏到 -100%（1+total=0）时超额无定义 → 报 None 而不是抛异常。"""
    s = {"total_return": -0.5, "annualized_return": -0.4, "net_per_period": -0.01,
         "n_days": 250, "n_periods": 10}
    b = {"total_return": -1.0, "annualized_return": -1.0, "net_per_period": -0.5,
         "n_days": 250, "n_periods": 10}
    x = excess_of(s, b)
    assert x["excess_total"] is None
    assert x["excess_ann"] is None
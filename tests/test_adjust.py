"""复权计算测试：TDD RED 先行。

设计依据（调研报告 4.1 节）：
- 落库只存不复权 OHLCV + 复权因子，复权在查询时现算
- 后复权 hfq = 不复权价 × 当日累计复权因子
- 前复权 qfq = hfq / 窗口内最后一个交易日的因子（动态基准，随窗口右端变化）
"""
import pandas as pd
import pytest

from aplus.adjust import attach_factor, to_hfq, to_qfq


@pytest.fixture
def two_day_frame():
    """合成数据：day1 收盘 10 元，之后发生分红除权，day2 因子升到 1.25，不复权收盘 8 元。

    真实含义：10 元 → 分红除权后价格跳到 8 元，复权因子补偿这个跳变，
    使得 qfq 序列连续（day1 也显示 8）、hfq 序列单调可比（day2 = 8×1.25 = 10）。
    """
    return pd.DataFrame({
        "date": ["2026-01-05", "2026-01-06"],
        "close": [10.0, 8.0],
        "open": [10.5, 8.2],
        "adjust_factor": [1.0, 1.25],
    })


def test_hfq_multiplies_by_row_factor(two_day_frame):
    hfq = to_hfq(two_day_frame)
    assert hfq["close"].tolist() == [10.0 * 1.0, 8.0 * 1.25]
    assert hfq["open"].tolist() == [10.5 * 1.0, 8.2 * 1.25]


def test_qfq_uses_last_factor_as_base(two_day_frame):
    qfq = to_qfq(two_day_frame)
    # day2 因子 1.25 为基准：day1 qfq = 10×1.0/1.25 = 8.0，day2 = 8×1.25/1.25 = 8.0
    assert qfq["close"].tolist() == [8.0, 8.0]
    assert qfq["open"].tolist() == [10.5 / 1.25, 8.2]


def test_qfq_base_moves_with_window_end(two_day_frame):
    """qfq 基准随窗口右端漂移——这正是调研发现的跨源 0.58% 差异根源，
    此测试固定该语义：只取前 1 天时基准变为 day1 的因子。"""
    head = two_day_frame.head(1)
    qfq = to_qfq(head)
    assert qfq["close"].tolist() == [10.0]  # 10×1.0/1.0


def test_unsorted_input_raises(two_day_frame):
    shuffled = two_day_frame.iloc[::-1].reset_index(drop=True)
    with pytest.raises(ValueError, match="date"):
        to_qfq(shuffled)


def test_attach_factor_ffills_events_onto_daily():
    """因子只在除权日变化，日线上需要前向填充；首个事件前的日期按 1.0 处理。"""
    daily = pd.DataFrame({
        "date": ["2026-01-05", "2026-01-06", "2026-01-07"],
        "close": [10.0, 8.0, 8.5],
    })
    events = pd.DataFrame({"date": ["2026-01-06"], "adjust_factor": [1.25]})
    merged = attach_factor(daily, events)
    assert merged["adjust_factor"].tolist() == [1.0, 1.25, 1.25]


# ---------- 因子表健康检查（2026-10-11 生产事故补丁） ----------

def test_is_factor_corrupt_detects_monotonicity_break():
    from aplus.adjust import is_factor_corrupt

    # 真实形态：baostock 必在上市日返回 back=1.0 的基准事件
    ok = pd.DataFrame({"date": ["1994-03-24", "2026-01-05", "2026-07-17"],
                       "adjust_factor": [1.0, 9.83, 10.04]})
    bad = pd.DataFrame({"date": ["1994-03-24", "2026-01-05", "2026-07-17", "2026-09-18"],
                        "adjust_factor": [1.0, 9.83, 1.02, 10.10]})
    assert not is_factor_corrupt(ok)
    assert is_factor_corrupt(bad)      # ← 生产事故的原始形状


def test_is_factor_corrupt_does_not_flag_legitimate_large_split():
    """大比例送股会产生百倍级合法跳变——量级检测会误伤，故刻意不做。

    实测 1200 只健康因子表：相邻跳变 P99=2.03 / P99.9=3.51 / 最大 242.77。
    任何量级阈值都会把这类正常数据判成损坏。
    """
    from aplus.adjust import is_factor_corrupt

    big_split = pd.DataFrame({"date": ["2026-01-05", "2026-07-17"],
                              "adjust_factor": [1.0, 242.77]})
    assert not is_factor_corrupt(big_split)


def test_is_factor_corrupt_detects_nonpositive_and_nan():
    from aplus.adjust import is_factor_corrupt

    assert is_factor_corrupt(pd.DataFrame(
        {"date": ["2026-01-05"], "adjust_factor": [0.0]}))
    assert is_factor_corrupt(pd.DataFrame(
        {"date": ["2026-01-05", "2026-07-17"], "adjust_factor": [1.0, float("nan")]}))
    assert is_factor_corrupt(pd.DataFrame(
        {"date": ["2026-01-05", "2026-07-17"], "adjust_factor": [1.0, -2.0]}))


def test_is_factor_corrupt_accepts_flat_and_small_series():
    from aplus.adjust import is_factor_corrupt

    flat = pd.DataFrame({"date": ["2026-01-05", "2026-07-17"],
                         "adjust_factor": [1.0, 1.0]})
    assert not is_factor_corrupt(flat)     # 无除权事件的股票因子恒为 1.0，不是损坏


def test_is_factor_corrupt_handles_missing_and_empty():
    """文件缺失=状态未知（判损坏）；空表=无除权事件（健康，attach_factor 填 1.0）。"""
    from aplus.adjust import is_factor_corrupt

    assert is_factor_corrupt(None)
    assert not is_factor_corrupt(pd.DataFrame(columns=["date", "adjust_factor"]))
    assert is_factor_corrupt(pd.DataFrame({"date": ["2026-01-05"]}))


def test_is_factor_corrupt_detects_incomplete_history():
    """第二类损坏（残缺）：baostock 必在上市日返回 back=1.0 的基准事件，
    故完整表首值恒为 1.0；首值 > 1 即证明历史事件丢失。"""
    from aplus.adjust import is_factor_corrupt

    complete = pd.DataFrame({"date": ["1994-03-24", "2026-09-18"],
                             "adjust_factor": [1.0, 10.10]})
    truncated = pd.DataFrame({"date": ["2026-09-10"], "adjust_factor": [10.146129]})
    assert not is_factor_corrupt(complete)
    assert is_factor_corrupt(truncated)      # ← 002261.SZ 的真实形状


# ---------- attach_factor 前向填充语义（2026-10-11 二次事故） ----------

def test_attach_factor_carries_event_that_predates_daily_range():
    """因子事件落在日线区间**之前**时，区间内每一根都应带上该值——不是 1.0。

    生产事故：日线自 2019-01-02 起，因子事件自 2008 年起。merge+fillna(1.0)
    只匹配上区间内那一事件，于是 2019~事件日 整整七年多的因子被填成 1.0，
    事件当天跳到 10.146 → 20 日动量 +839% 进入用户当日配置。
    """
    from aplus.adjust import attach_factor

    daily = pd.DataFrame({"date": ["2019-01-02", "2019-06-03", "2019-06-04"],
                          "close": [25.0, 26.0, 27.0]})
    events = pd.DataFrame({"date": ["2008-07-23", "2018-06-21", "2019-06-04"],
                           "adjust_factor": [1.0, 10.138245, 10.146129]})
    out = attach_factor(daily, events)
    got = out["adjust_factor"].tolist()
    assert got[0] == pytest.approx(10.138245)   # ← 事故点：原为 1.0
    assert got[1] == pytest.approx(10.138245)
    assert got[2] == pytest.approx(10.146129)


def test_attach_factor_before_first_event_still_defaults_to_one():
    """上市日之前无事件 → 1.0（无除权即无修正，语义正确）。

    夹具必须包含**上市日那条基准事件**（baostock 必返回 factor=1.0），
    否则就变成另一种情况：基准未知，见下面的测试。
    """
    from aplus.adjust import attach_factor

    daily = pd.DataFrame({"date": ["2019-01-02", "2019-06-03"],
                          "close": [25.0, 26.0]})
    events = pd.DataFrame({"date": ["2018-12-26", "2019-06-03"],
                           "adjust_factor": [1.0, 1.2]})
    out = attach_factor(daily, events)
    assert out["adjust_factor"].tolist() == [1.0, pytest.approx(1.2)]


def test_attach_factor_invalidates_unknown_scale_period():
    """**旧行为在此被推翻**，且必须被推翻。

    旧测试断言「日线 2019-01-02 起、唯一事件 2019-06-03 因子 10.146 → 前段填 1.0」
    是正确的。那正是 `test_is_factor_corrupt_detects_incomplete_history` 认定的
    **002261.SZ 残缺形状**——两条测试当时互相矛盾，而当时被当作规范的是错的那条。

    填 1.0 不是「无除权故无修正」，而是**编造了一个未知的尺度**：
    10.146 倍的跳变会变成 +914% 的假收益。本测试钉死新行为——那段标 NaN。
    """
    from aplus.adjust import attach_factor

    daily = pd.DataFrame({"date": ["2019-01-02", "2019-06-03"],
                          "close": [25.0, 2.5]})
    events = pd.DataFrame({"date": ["2019-06-03"], "adjust_factor": [10.146129]})
    out = attach_factor(daily, events)
    assert pd.isna(out["adjust_factor"].iloc[0]), "基准未知期不得填 1.0"
    assert out["adjust_factor"].iloc[1] == pytest.approx(10.146129)


def test_attach_factor_preserves_daily_row_order():
    """日线乱序输入也不该改变逐行的因子取值。"""
    from aplus.adjust import attach_factor

    daily = pd.DataFrame({"date": ["2019-06-04", "2019-01-02", "2019-06-03"],
                          "close": [27.0, 25.0, 26.0]})
    events = pd.DataFrame({"date": ["2018-06-21", "2019-06-04"],
                           "adjust_factor": [10.138245, 10.146129]})
    out = attach_factor(daily, events)
    pairs = dict(zip(out["date"], out["adjust_factor"]))
    assert pairs["2019-01-02"] == pytest.approx(10.138245)
    assert pairs["2019-06-03"] == pytest.approx(10.138245)
    assert pairs["2019-06-04"] == pytest.approx(10.146129)
    assert out["date"].tolist() == daily["date"].tolist()   # 顺序不变


def test_attach_factor_does_not_introduce_factor_event_rows():
    """外连接会把 12 条因子事件塞进 1882 行日线——只保留日线行。"""
    from aplus.adjust import attach_factor

    daily = pd.DataFrame({"date": ["2019-01-02", "2019-06-03"], "close": [25.0, 26.0]})
    events = pd.DataFrame({"date": ["2008-07-23", "2018-06-21"],
                           "adjust_factor": [1.0, 10.0]})
    assert len(attach_factor(daily, events)) == 2


# ---------- 孤儿因子表：未使用 ≠ 损坏（TDD，2026-10-11） ----------
#
# 现象：data/master/factor 有 5556 只，data/master/daily 只有 5447 只。
# 多出来的 109 只（1.96%）是**孤儿因子表**——因子文件在，但该股没有日线。
#
# 为什么必须分开算：
# - 它们**从不参与横截面**（没日线就 join 不进来），坏不坏都不影响任何预测
# - 但旧审计把它们算进损坏率，且 `repair_factors.py` 只遍历日线代码，
#   于是它们**永远修不了**，却一直把审计比例顶高、反复打 error
# - 把「没在用」和「数据坏了」混为一谈，会让人为一个不影响结果的问题反复重跑修复

def test_classify_separates_orphans_from_usable(tmp_path):
    from aplus.adjust import classify_factor_files
    daily = tmp_path / "master" / "daily"; daily.mkdir(parents=True)
    factor = tmp_path / "master" / "factor"; factor.mkdir(parents=True)
    for c in ("A", "B"):
        (daily / f"{c}.parquet").touch()
    for c in ("A", "B", "GHOST"):
        (factor / f"{c}.parquet").touch()
    usable, orphans = classify_factor_files(tmp_path)
    assert sorted(usable) == ["A", "B"]
    assert orphans == ["GHOST"], "没有日线的因子表应归为孤儿，不是损坏"


def test_classify_handles_missing_dirs(tmp_path):
    from aplus.adjust import classify_factor_files
    usable, orphans = classify_factor_files(tmp_path / "nonexistent")
    assert usable == [] and orphans == []

"""尾部「有成交却缺值」的体检盲区。

## 为什么要加这条判据

体检原本只看「列在不在」（`missing_columns`），从不看「**在的列尾部有没有值**」。
于是 3342 只股票（61.2%）的 `turn` 列在最后一根 K 线上是空的，体检一路判健康，
缺口静默存在了三周。列存在 ≠ 列有数据——这两件事必须分开验。

## 关键约束：不能把停牌误报成缺口

停牌日 `volume=0`，`turn`/`amount`/`pctChg` 为空是**正常的**（当天根本没成交，
没有换手率可言）。若不排除停牌，这��判据会对长期停牌股全线误报，比原来的
盲区更吵。所以「有成交」是前置条件，不是加分项。

复刻的真实样本：`601607.SH` 末根 `turn=NaN` 而前一根有值 0.6231，
`volume` 正常——这是真缺口；停牌股则是 `volume=0` 且整段为空。
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aplus.coverage import (  # noqa: E402
    REQUIRED_EXTRA_COLUMNS,
    Thresholds,
    classify_frame,
    coverage_gaps,
    tail_nan_columns,
)

TARGET_START = "2019-01-02"
TARGET_END = "2026-10-09"

# 造帧起点在 2026-08-20。走 classify_* 时必须给出上市登记，否则首行距
# TARGET_START 七年有余，会被判成「历史被砍头」——那是另一个缺口，
# 会把 tail_nan 的断言淹没掉（第一版测试就栽在这，判成不健康的是 head
# 而不是 tail）。上市登记把首行解释成「本来就这么晚才有」，头尾两项归零，
# 此时不健康的唯一理由只可能是尾部空值。
LISTING_DATE = "2026-08-19"


def make_frame(n=30, *, turn_tail=0.0, amount_tail=0.0, volume=None, tradestatus=None):
    """造一只股票：`turn_tail`/`amount_tail` = 末尾多少根置空（0 表示不置空）。"""
    dates = pd.bdate_range("2026-08-20", periods=n)
    close = pd.Series([10.0 + (i % 7) * 0.37 for i in range(n)])
    vol = pd.Series([1_000_000.0 + i * 10 for i in range(n)]) if volume is None \
        else pd.Series(volume, dtype="float64")
    df = pd.DataFrame({
        "date": dates.strftime("%Y-%m-%d"),
        "code": "601607.SH",
        "open": close.round(2),
        "high": (close * 1.02).round(2),
        "low": (close * 0.98).round(2),
        "close": close.round(2),
        "volume": vol,
    })
    df["amount"] = (close * vol).round(2)
    df["tradestatus"] = 1 if tradestatus is None else pd.Series(tradestatus).to_numpy()
    df["isST"] = 0
    df["turn"] = 0.5
    df["pctChg"] = 0.0
    for col, k in (("turn", turn_tail), ("amount", amount_tail)):
        if k:
            df.loc[df.index[-k:], col] = float("nan")
    return df


# --- 核心：末根缺值要被看见 -------------------------------------------------


def test_tail_nan_detected_on_last_root():
    """复刻 601607.SH：末根 turn 为空、前一根有值，且当天正常成交。"""
    df = make_frame(turn_tail=1)
    assert df["turn"].iloc[-1] != df["turn"].iloc[-1]      # NaN
    assert df["turn"].iloc[-2] == df["turn"].iloc[-2]      # 上一根有值
    got = tail_nan_columns(df)
    assert "turn" in got, "末根有成交却缺 turn，必须报出来"
    assert got["turn"] == 1


def test_healthy_frame_reports_nothing():
    df = make_frame()
    assert tail_nan_columns(df) == {}


def test_turn_and_amount_both_flagged():
    df = make_frame(turn_tail=2, amount_tail=2)
    got = tail_nan_columns(df)
    assert got.get("turn") == 2 and got.get("amount") == 2


# --- 不能误报：停牌 ---------------------------------------------------------


def test_suspended_tail_not_flagged():
    """停牌日 volume=0，turn 为空是正常的，不能报成缺口。"""
    n = 30
    vol = [1_000_000.0 + i * 10 for i in range(n)]
    vol[-5:] = [0.0] * 5                      # 尾部连续停牌 5 根
    df = make_frame(turn_tail=5, volume=vol, tradestatus=[1] * (n - 5) + [0] * 5)
    assert tail_nan_columns(df) == {}, "停牌导致的空值是正常状态，不算缺口"


def test_suspended_and_traded_mixed_only_counts_traded_rows():
    """尾部混合：停牌 3 根 + 正常成交 2 根，只数后者的空值。"""
    n = 30
    vol = [1_000_000.0 + i * 10 for i in range(n)]
    vol[-3:] = [0.0] * 3
    df = make_frame(turn_tail=5, volume=vol, tradestatus=[1] * (n - 3) + [0] * 3)
    got = tail_nan_columns(df)
    assert got.get("turn") == 2, "只应计入真正成交的 2 根"


def test_tradestatus_zero_with_positive_volume_still_counted():
    """tradestatus=0 但 volume>0 属数据自相矛盾，按成交处理（宁可吵）。"""
    n = 30
    vol = [1_000_000.0 + i * 10 for i in range(n)]
    df = make_frame(turn_tail=1, volume=vol, tradestatus=[0] * n)
    assert tail_nan_columns(df).get("turn") == 1


# --- 窗口边界 ---------------------------------------------------------------


def test_gap_deeper_than_window_is_caught():
    """缺 5 根整段、窗口正好 5 根：边界要能抓到。"""
    df = make_frame(turn_tail=5)
    assert tail_nan_columns(df, Thresholds(halt_run=5)).get("turn") == 5


def test_gap_older_than_window_is_ignored():
    """缺口整段落在窗口**之外**（历史深处）不属于尾部体检职责，交给别的判据。

    位置刻意放在 rows 8:13：`tail(5)`（rows 25:29）够不到，
    `tail(20)`（rows 10:29）够得到。

    期望值**从帧本身算**，不硬编码：第二版我把重叠行数写成了 5
    （实际只有 rows 10,11,12 共 3 行），于是这条断言在未变异时是红的，
    却因为变异体恰好把窗口放大到覆盖全部缺口而「看起来被杀掉」。
    **一条靠巧合通过的断言比没有断言更糟**——它制造它正在验证的那份信心。
    """
    df = make_frame(n=30)
    df.loc[df.index[8:13], "turn"] = float("nan")
    assert df["turn"].iloc[-1] == 0.5

    assert tail_nan_columns(df, Thresholds(halt_run=5)) == {}

    # 反证：窗口一放大，同一只就该被看见——否则上面的断言是空转
    expected = int(df.tail(20)["turn"].isna().sum())
    assert expected > 0, "反证本身空转：扩大窗口也没看见任何缺口"
    assert tail_nan_columns(df, Thresholds(halt_run=20)).get("turn") == expected


# --- 接进判定链路 -----------------------------------------------------------


def test_coverage_gaps_exposes_tail_nan_cols():
    info = coverage_gaps(make_frame(turn_tail=1), TARGET_START, TARGET_END,
                         listing_date=LISTING_DATE)
    assert "turn" in info["tail_nan_cols"]


def test_classify_frame_flags_unhealthy_with_reason():
    res = classify_frame("601607.SH", make_frame(turn_tail=1), TARGET_START, TARGET_END,
                         listing_date=LISTING_DATE)
    assert "tail_nan_cols" in res["reasons"]
    assert res["is_healthy"] is False


def test_classify_frame_healthy_when_filled():
    res = classify_frame("601607.SH", make_frame(), TARGET_START, TARGET_END,
                         listing_date=LISTING_DATE)
    assert res["is_healthy"] is True, f"不该有原因，实际：{res['reasons']}"
    assert res["tail_nan_cols"] == {}


def test_tail_nan_only_checks_extra_columns():
    """基线列（OHLCV）不在此判据范围——它们缺列由 missing_columns 管。"""
    df = make_frame(turn_tail=1)
    got = tail_nan_columns(df)
    assert all(c in REQUIRED_EXTRA_COLUMNS for c in got)


def test_absent_column_is_not_double_reported():
    """列根本不存在时归 missing_columns，不该又被算成尾部空值。"""
    df = make_frame(turn_tail=1).drop(columns=["turn"])
    assert "turn" not in tail_nan_columns(df)

"""判定窗口的**身份**：`pred_date` 不是观测日，`exit_date` 才是（2026-10-14）。

## 实测事故

`verification.parquet` 只有 1 个 `pred_date`（2026-10-12）。补判机制
（`pending_prediction_dates`）跑出来 3 天：2026-10-09 / 10-10 / 10-11，
概览页 `n_days` 从 1 跳到 3。

但那 3 天**量的是同一个窗口**：

    2026-10-09 周五、2026-10-10 周六、2026-10-11 周日 —— 周末主库没有 bar。
    `last_two_bars(after_date=...)` 取「信号日之后第一根」，于是在这三个日期上
    **分别**落到同一根 10-12 买入、10-13 卖出。

`daily_portfolio_returns` 按 `pred_date` 分组，复利把「同一天的三个篮子」
当成三天连乘：

    total = (1+r_10-09)(1+r_10-10)(1+r_10-11) - 1
    years = 3 / 252

那不是三天，是**一天里换了三次名单**。`n_days=3` 让
`MIN_DAYS_FOR_ANNUALISATION` 放行，一个只含**单日**市场波动的序列被开
84 次方，实测报出 `-78.06%` 的「年化」——三个篮子的真实加权日收益是
`+0.67%`，符号都不一样。

## 修法

观测日的身份是**收益实现的那一天**（`exit_date`），不是发名单的那一天。
同一个 `exit_date` 的多个篮子合并成一天的等权收益。

命中率的分子分母是**逐笔**的（`rolling_stats`），不受本文件影响——
所以这个修正只动复利曲线，一个判定数字都不改。
"""
import pandas as pd
import pytest

from aplus.reports import pending_prediction_dates
from aplus.verify import (
    MIN_DAYS_FOR_ANNUALISATION,
    compounded_stats,
    daily_portfolio_returns,
    last_two_bars,
)


def _store_with_gap(tmp_path, code="600001.SH", start="2026-10-09", n=4):
    """构造一只股票。`pd.bdate_range` 自动跳过周末 —— 正是实盘主库的形状。

    start=2026-10-09（周五）起 4 个交易日 → 10-09 / 10-12 / 10-13 / 10-14。
    中间没有 10-10、10-11 两根 bar，与实盘一致。
    """
    from aplus.store import LocalStore

    closes = [10.0, 11.0, 12.0, 13.0, 14.0][:n]
    dates = pd.bdate_range(start, periods=len(closes))
    df = pd.DataFrame({"date": dates.strftime("%Y-%m-%d"),
                       "open": closes, "high": closes,
                       "low": closes, "close": closes, "volume": [1e6] * len(closes)})
    store = LocalStore(tmp_path)
    path = tmp_path / "master" / "daily" / f"{code}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    return store, df


# ---------- 窗口的身份必须被记录 ----------

def test_last_two_bars_reports_which_dates_it_actually_measured(tmp_path):
    """返回 `entry_date` / `exit_date` —— 收益是在哪两根 K 线上实现的。

    没有这两个字段，下游无法知道「三个预测日」是不是同一天，
    复利就会把一周同一个窗口连乘三次。
    """
    store, df = _store_with_gap(tmp_path)
    bar = last_two_bars(store, "600001.SH", after_date="2026-10-09")
    assert bar["entry_date"] == "2026-10-12"
    assert bar["exit_date"] == "2026-10-13"
    assert (bar["close"], bar["prev_close"]) == (12.0, 11.0)


def test_predictions_spanning_a_weekend_measure_one_and_the_same_window(tmp_path):
    """周五 / 周六 / 周日三次出名单 → 成交窗口**完全相同**。

    这是整件事的根：周末主库没有 bar，`searchsorted(side="right")`
    在这三个信号日上落到的「之后第一根」都是 10-12。

    之前没有任何地方记录这件事，于是三次判定被当成三天。
    """
    store, _ = _store_with_gap(tmp_path)
    windows = set()
    for signal in ("2026-10-09", "2026-10-10", "2026-10-11"):
        bar = last_two_bars(store, "600001.SH", after_date=signal)
        windows.add((bar["entry_date"], bar["exit_date"]))
    assert windows == {("2026-10-12", "2026-10-13")}, (
        f"三个信号日应落在同一个成交窗口，实际 {windows}")


# ---------- 复利曲线必须按 exit_date 分组 ----------

def _hist(rows):
    """逐笔判定记录。带 `exit_date` = 新口径（锚定判定）。"""
    return pd.DataFrame(rows)


def _basket(pred_date, exit_date, ret, n=3):
    return [{"pred_date": pred_date, "exit_date": exit_date,
             "code": f"{pred_date[-2:]}{k}.SZ", "direction": "up",
             "actual_pct": ret, "cost": 0.0, "result": "hit"}
            for k in range(n)]


def test_daily_returns_collapse_baskets_sharing_one_exit_date():
    """同一 `exit_date` 的三个篮子 = **一天**，不是三天。"""
    rows = (_basket("2026-10-09", "2026-10-13", 0.10)
            + _basket("2026-10-10", "2026-10-13", 0.30)
            + _basket("2026-10-11", "2026-10-13", -0.20))
    daily = daily_portfolio_returns(_hist(rows))
    assert len(daily) == 1, (
        f"三个篮子同一个 exit_date 应塌缩成 1 天，实际 {len(daily)} 天："
        f"复利会把同一天的收益连乘 {len(daily)} 次")
    assert daily.iloc[0] == pytest.approx((0.10 + 0.30 - 0.20) / 3)


def test_compounded_refuses_to_annualise_a_single_realised_window():
    """`n_days=1` 时**不得**给年化 —— 那是被开 84 次方的单日噪音。

    实测未修正时这里返回 `cagr=-0.7806`：三个篮子的真实日收益 +0.67%
    被当成三天连乘（累计 −1.79%）再年化，符号整个翻过来。
    """
    rows = (_basket("2026-10-09", "2026-10-13", 0.10)
            + _basket("2026-10-10", "2026-10-13", 0.30)
            + _basket("2026-10-11", "2026-10-13", -0.20))
    st = compounded_stats(_hist(rows))
    assert st["n_days"] == 1
    assert st["cagr"] is None
    assert st["arith_ann"] is None
    assert st["total_return"] == pytest.approx((0.10 + 0.30 - 0.20) / 3)


def test_distinct_exit_dates_still_produce_one_day_each():
    """回归防护：真正**连续**的交易日（每天一个不同窗口）必须各算一天。

    这条钉住修正没有反向过头 —— 合并只针对「同窗口多篮子」，
    正常的逐日序列不能被压扁。
    """
    rows = (_basket("2026-10-09", "2026-10-13", 0.10)
            + _basket("2026-10-12", "2026-10-14", 0.20))
    daily = daily_portfolio_returns(_hist(rows))
    assert len(daily) == 2
    assert compounded_stats(_hist(rows))["n_days"] == 2


def test_legacy_rows_without_exit_date_still_group_by_pred_date():
    """没有 `exit_date` 的历史行按 `pred_date` 分组，不得整个丢掉。

    存量 `verification.parquet` 是旧判定路径产的，没有这两个字段。
    直接 `groupby("exit_date")` 会 KeyError 或静默清空 —— 那等于把
    上线以来攒下的全部证据抹掉。
    """
    rows = [{"pred_date": "2026-10-09", "code": f"{k}.SZ", "direction": "up",
             "actual_pct": 0.10, "cost": 0.0} for k in range(3)]
    rows += [{"pred_date": "2026-10-12", "code": f"9{k}.SZ", "direction": "up",
              "actual_pct": 0.20, "cost": 0.0} for k in range(3)]
    daily = daily_portfolio_returns(_hist(rows))
    assert len(daily) == 2
    assert compounded_stats(_hist(rows))["n_days"] == 2


def test_min_days_gate_still_applies_after_collapsing():
    """塌缩后不足 `MIN_DAYS_FOR_ANNUALISATION` 的，一律不给年化。"""
    assert MIN_DAYS_FOR_ANNUALISATION == 2
    rows = _basket("2026-10-09", "2026-10-13", 0.10) + \
        _basket("2026-10-10", "2026-10-13", 0.30)
    assert compounded_stats(_hist(rows))["cagr"] is None


# ---------- 旧口径的判定必须能被重新判定 ----------

def test_pending_reopens_days_judged_on_the_unanchored_basis(tmp_path):
    """没有 `exit_date` 的历史判定 = **旧 bug 口径**，必须重新判定。

    实测 `verification.parquet` 里 2026-10-12 那 20 行是
    `closes[-1]/closes[-2]` 产的（`entry_lag=0`），
    而锚定口径对 10-12 要 10-13 买、10-14 卖 —— 差一根 K 线。
    实测同一批推荐，换一天判定命中率就从 20.0% 变成 70.6%。

    `pending_prediction_dates` 只看 `pred_date` 在不在判定档里，
    于是这一天**永远不会被重判**，两种口径的行混在同一张表里。
    """
    reports = tmp_path / "reports"
    preds = reports / "predictions"
    preds.mkdir(parents=True)
    for d in ("2026-10-09", "2026-10-12"):
        (preds / f"pred_{d}.parquet").write_text(d, encoding="utf-8")
    # 旧路径产的行：有 pred_date，没有 exit_date
    (reports / "verification.parquet").write_bytes(pd.DataFrame(
        {"code": ["600001.SH"], "pred_date": ["2026-10-12"],
         "direction": ["up"], "actual_pct": [0.01], "result": ["hit"]}
    ).to_parquet(index=False))

    pending = pending_prediction_dates(reports)
    assert pending == ["2026-10-09", "2026-10-12"], (
        "旧口径的判定必须重开，否则两种口径永久混在一张表里")


def test_pending_excludes_days_already_judged_on_the_current_basis(tmp_path):
    """锚定口径判过的日子不得重复判定。"""
    reports = tmp_path / "reports"
    preds = reports / "predictions"
    preds.mkdir(parents=True)
    for d in ("2026-10-09", "2026-10-12"):
        (preds / f"pred_{d}.parquet").write_text(d, encoding="utf-8")
    (reports / "verification.parquet").write_bytes(pd.DataFrame(
        {"code": ["600001.SH"], "pred_date": ["2026-10-12"],
         "exit_date": ["2026-10-14"], "direction": ["up"],
         "actual_pct": [0.01], "result": ["hit"]}
    ).to_parquet(index=False))

    assert pending_prediction_dates(reports) == ["2026-10-09"]
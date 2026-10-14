"""实盘判定口径必须与回测逐字对齐——三条独立缺陷的回归防护。

## 背景：为什么这三条比「命中率 20%」重要得多

网页上那个「累计 20 条判定、总命中 20.0%」的数字被反复引用，
但它量化的东西和回测量化的**不是同一个量**。诊断（2026-10-14）确认
三条**代码事实**，各自独立成立：

### P0-1　判定窗口少一根 K 线

`aplus/verify.py::last_two_bars` 取 `closes[-1] / closes[-2]`，
**没有日期锚定**——它量的是「库里最后两根 K 线」，
与「预测那天之后的第几根」无关。

回测侧 `backtest._next_bars` 是对的：
`searchsorted(dates, D, side="right")` 锚到信号日之后第一根，
`entry_lag=1` → D+1 收盘买、D+2 收盘卖。

实测（301190.SZ，`hfq_close`）：`actual_pct=0.01548` = 48.45→49.20，
即 10-12 收盘→10-13 收盘，而信号正是用 10-12 收盘算的。
**窗口起点落在信息集之内**——`backtest.py:78-91` 的 docstring 早就把
这个口径命名为 bug，并注明「`entry_lag=0` 任何判定都不得使用」。
但 `verify.py` 正是那个活着的 `entry_lag=0`。

后果：换一天、换一个窗口，命中率从 20% 变成 70.6%（12/17）。
一个能被一天之差翻转的指标，不能用来决定加不加仓。

### P0-2　80% 的判定被静默丢弃

`reports.latest_prediction_date` 返回**目录里最新**的预测文件名，
不是「最早的未判定日」。`run_daily.step_verify_yesterday` 用它选判定对象，
于是每次只判一天，前一天**永久跳过**，没有任何补判循环。

实测：5 个预测日共 100 条推荐，只入库 20 条。

### P0-3　实盘链路零成本，但报告显示「成本 0」

`pred_*.parquet` 没有 `cost` 列 → `verify.py` 的 `if "cost" in up.columns`
落空 → 静默按**毛**收益算。`stats.json` 写 `avg_cost_drag: 0.0`，
读作「成本不花钱」，实际是「成本没测」。

回测侧明确建模 0.003（`backtest.STOCK_COST_DEFAULT`）。
"""
import pandas as pd
import pytest

from aplus.reports import pending_prediction_dates
from aplus.verify import last_two_bars


def _store_with_closes(tmp_path, code="600001.SH", closes=None, start="2026-01-05"):
    """构造一只股票，返回 LocalStore。closes 从 start 起逐日排列（跳过周末）。"""
    from aplus.store import LocalStore

    closes = closes or [10.0, 11.0, 12.0, 13.0, 14.0, 15.0]
    dates = pd.bdate_range(start, periods=len(closes))
    df = pd.DataFrame({"date": dates.strftime("%Y-%m-%d"),
                       "open": closes, "high": closes,
                       "low": closes, "close": closes, "volume": [1e6] * len(closes)})
    store = LocalStore(tmp_path)
    path = tmp_path / "master" / "daily" / f"{code}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    return store, df


# ---------- P0-1 判定窗口必须锚定到信号日 ----------

def test_last_two_bars_is_anchored_to_signal_date(tmp_path):
    """给定信号日 D，必须返回 D 之后第 2 根（卖）与第 1 根（买）的收盘价。

    回测口径：`searchsorted(D, 'right')` → entry=D+1、exit=D+2，
    `actual_pct = exit / entry - 1`。
    """
    store, df = _store_with_closes(tmp_path)
    dates = list(df["date"])

    bar = last_two_bars(store, "600001.SH", after_date=dates[0])
    # D+1 = 11.0（买），D+2 = 12.0（卖）
    assert bar["prev_close"] == pytest.approx(11.0), "买价应为信号日之后第一根"
    assert bar["close"] == pytest.approx(12.0), "卖价应为信号日之后第二根"


def test_last_two_bars_rejects_predictions_without_a_future_bar(tmp_path):
    """信号日就是最后一天 → 没有卖价 → 不成交，返回 None。

    绝不能退化成「拿最后一根凑」——那样就是在量一笔无法执行的交易。"""
    store, df = _store_with_closes(tmp_path)
    assert last_two_bars(store, "600001.SH", after_date=df["date"].iloc[-1]) is None


def test_last_two_bars_differs_from_backtest_free_version(tmp_path):
    """回归防护：不传信号日时，**不得**再默默返回库里最后两根。

    这正是原 bug 的形态——API 允许省略锚点，于是调用方省略了它。
    """
    store, df = _store_with_closes(tmp_path)
    with pytest.raises(TypeError):
        last_two_bars(store, "600001.SH")


def test_live_window_is_one_day_later_than_the_buggy_one(tmp_path):
    """把 bug 的口径显式算出来，证明两者**确实差一根**。

    原 bug：库里最后两根 = 15.0 / 14.0。
    锚定口径（信号日取第 0 天）：卖 12.0 / 买 11.0。
    """
    store, df = _store_with_closes(tmp_path)
    buggy = last_two_bars(store, "600001.SH",
                          after_date=None, _allow_unanchored_for_test=True)
    fixed = last_two_bars(store, "600001.SH", after_date=df["date"].iloc[0])
    assert buggy["close"] / buggy["prev_close"] != pytest.approx(
        fixed["close"] / fixed["prev_close"])


# ---------- P0-2 必须补判所有未判定日 ----------

def test_pending_dates_lists_every_unjudged_day_not_just_the_newest(tmp_path):
    """5 天预测只判 1 天 = 80% 证据被丢弃。

    `latest_prediction_date` 返回最新文件名，于是前四天永远轮不到。
    正确语义是「**还没判过的**预测日，按时间排」。

    fixture 带 `exit_date`：判定档里的行默认按**锚定口径**判过
    （2026-10-14 起，见 `test_pending_reopens_days_judged_on_the_unanchored_basis`
    ——缺 `exit_date` 的旧行会被重开，不是「已判定」）。
    """
    reports = tmp_path / "reports" / "predictions"
    reports.mkdir(parents=True)
    for d in ("2026-10-08", "2026-10-09", "2026-10-12"):
        (reports / f"pred_{d}.parquet").write_text(d, encoding="utf-8")
    (reports.parent / "verification.parquet").write_bytes(pd.DataFrame(
        {"code": ["A"], "pred_date": ["2026-10-08"],
         "exit_date": ["2026-10-09"]}).to_parquet(index=False))

    pending = pending_prediction_dates(reports.parent)
    assert pending == ["2026-10-09", "2026-10-12"], (
        f"未判定日应全部补判，实际只给出 {pending}")


def test_pending_dates_excludes_already_judged(tmp_path):
    reports = tmp_path / "reports" / "predictions"
    reports.mkdir(parents=True)
    for d in ("2026-10-08", "2026-10-09"):
        (reports / f"pred_{d}.parquet").write_text(d, encoding="utf-8")
    vpath = reports.parent / "verification.parquet"
    vpath.write_bytes(pd.DataFrame(
        {"code": ["A"], "pred_date": ["2026-10-08"],
         "exit_date": ["2026-10-09"]}).to_parquet(index=False))

    assert pending_prediction_dates(reports.parent) == ["2026-10-09"]


def test_pending_dates_returns_all_when_nothing_judged(tmp_path):
    reports = tmp_path / "reports" / "predictions"
    reports.mkdir(parents=True)
    for d in ("2026-10-08", "2026-10-09", "2026-10-12"):
        (reports / f"pred_{d}.parquet").write_text(d, encoding="utf-8")
    assert pending_prediction_dates(reports.parent) == ["2026-10-08", "2026-10-09",
                                                "2026-10-12"]


# ---------- P0-3 成本缺失必须显式，不可静默当 0 ----------

def test_missing_cost_column_raises_instead_of_silently_using_gross(tmp_path):
    """`pred_*.parquet` 没有 cost 列时，**必须报错**。

    原来 `if "cost" in up.columns` 落空 → 静默按毛收益算 →
    `stats.json` 写 `avg_cost_drag: 0.0`，读起来像「成本不花钱」。
    """
    from aplus.verify import verify_predictions

    preds = pd.DataFrame([{"code": "600001.SH", "direction": "up"}])
    bars = pd.DataFrame([{"code": "600001.SH", "close": 11.0, "prev_close": 10.0}])

    with pytest.raises(KeyError, match="cost"):
        verify_predictions(preds, bars, require_cost=True)


def test_cost_column_lands_in_the_frame_and_netting_happens_once():
    """实测成本必须**落到 `verification.parquet` 里那一列**，而不是乘进 `actual_pct`。

    `actual_pct` 是毛值（回测侧 `evaluate_weights` → `rolling_stats` 一直这么消费），
    成本由 `rolling_stats` 扣一次。若这里就把成本乘进 `actual_pct`，
    下游会再扣一遍——净收益被扣两遍，实盘报告系统性低报一个往返成本。
    """
    from aplus.verify import rolling_stats, verify_predictions

    preds = pd.DataFrame([{"code": "600001.SH", "direction": "up"}])
    bars = pd.DataFrame([{"code": "600001.SH", "close": 11.0,
                          "prev_close": 10.0, "cost": 0.003}])
    out = verify_predictions(preds, bars, require_cost=True)

    assert out["actual_pct"].iloc[0] == pytest.approx(0.10), "actual_pct 必须是毛值"
    assert out["cost"].iloc[0] == pytest.approx(0.003)
    st = rolling_stats(out.assign(pred_date="2026-01-05", market_pct=0.0,
                                  result=out["result"]))
    assert st["avg_cost_drag"] == pytest.approx(0.003, abs=1e-9), "成本没被测到"
    assert st["up_excess_vs_market"] == pytest.approx(0.10 - 0.003, abs=1e-9)


def test_declared_default_cost_is_used_when_no_measured_column(tmp_path):
    """无 cost 列但申报了 `default_cost` → 口径写进数据，不靠猜。"""
    from aplus.verify import verify_predictions

    preds = pd.DataFrame([{"code": "600001.SH", "direction": "up"}])
    bars = pd.DataFrame([{"code": "600001.SH", "close": 11.0, "prev_close": 10.0}])
    out = verify_predictions(preds, bars, require_cost=True, default_cost=0.003)
    assert out["cost"].iloc[0] == pytest.approx(0.003)


def test_down_leg_is_not_charged_cost():
    """跌腿是回避清单，不是做过的仓位——扣它等于惩罚一笔不存在的交易。

    扣了会让 `down_excess = market - avg_down` 系统性高估。
    """
    from aplus.verify import verify_predictions

    preds = pd.DataFrame([{"code": "600001.SH", "direction": "down"}])
    bars = pd.DataFrame([{"code": "600001.SH", "close": 9.0, "prev_close": 10.0}])
    out = verify_predictions(preds, bars, require_cost=True, default_cost=0.003)
    assert out["cost"].iloc[0] == 0.0


def test_flat_move_stays_flat_after_cost_accounting():
    """恰好平盘必须仍是 `flat`，不能因为 0.3% 摩擦被判成 `miss`。

    成本是摩擦不是方向信息；用它判方向等于用手续费凭空造判断。
    """
    from aplus.verify import verify_predictions

    preds = pd.DataFrame([{"code": "600001.SH", "direction": "up"}])
    bars = pd.DataFrame([{"code": "600001.SH", "close": 10.0, "prev_close": 10.0}])
    out = verify_predictions(preds, bars, require_cost=True, default_cost=0.003)
    assert out["result"].iloc[0] == "flat"
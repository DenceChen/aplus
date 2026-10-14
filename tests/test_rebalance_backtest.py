"""调仓频率轴：日频 vs 低频，篮子与成本都必须随频率变。

## 为什么把频率做成参数，而不是另写一套回测

成本是这件事的根（`docs/FINDINGS_2026-10-11.md` 第 3 节）：
5 万本金 / 10 只持仓 / 单只 5000 元时，**5 元最低佣金**是主导项，
一次往返 12.60 元 = 0.2520%；实测日频换手 27.50% → **年成本 16.91%**，
而平台至今测到的每个因子效应都在 ±1% 以内。

书的 3.1.4 口径是**每月末调仓**。如果平台只能跑日频，就永远只能验证
一个书从没声称过的东西。

## 三条不可让渡的语义

1. **非调仓日不重新选股**，原样持有上期篮子。
2. **非调仓日不产生换手**，成本必须为 0——否则低频会被重复收费，
   频率对比直接失去意义。
3. **`rebalance_every=1` 必须与修复前逐位相同**，否则历史结论被静默改写。
"""
import pandas as pd
import pytest

from aplus.backtest import build_cross_sections, evaluate_weights
from aplus.store import LocalStore

W = {"mom20": 1.0, "rev5": 0.3, "vol20": 0.5, "vsur": 0.5}


def _make_stock(code, n=90, drift=0.01, start_price=10.0):
    dates = pd.bdate_range("2026-01-01", periods=n).strftime("%Y-%m-%d")
    closes = [start_price * ((1 + drift) ** i) for i in range(n)]
    return pd.DataFrame({
        "date": dates, "code": [code] * n, "open": closes,
        "high": [c * 1.01 for c in closes], "low": [c * 0.99 for c in closes],
        "close": closes, "volume": [1e6 + i * 1000 for i in range(n)],
        "amount": [1e7] * n, "tradestatus": [1] * n, "isST": [0] * n,
    })


def _xs(tmp_path, n_stocks=12):
    store = LocalStore(tmp_path)
    for i in range(n_stocks):
        d = 0.004 * (i % 4) + 0.001 * i
        store.upsert_daily(f"60{i:04d}.SH",
                           _make_stock(f"60{i:04d}.SH", drift=d if i % 2 == 0 else -d))
    dates = store.read_daily("600000.SH")["date"].tolist()
    return build_cross_sections(store, dates[30], dates[-2]), dates


def _baskets(res):
    return (res[res["direction"] == "up"].groupby("pred_date")["code"]
            .apply(lambda s: tuple(sorted(s))).to_dict())


def test_rebalance_every_one_is_identical_to_default(tmp_path):
    """默认路径必须与不传参数时逐位相同——历史结论不能被静默改写。"""
    xs, _ = _xs(tmp_path)
    a = evaluate_weights(xs, W, top_n=4, bottom_n=4)
    b = evaluate_weights(xs, W, top_n=4, bottom_n=4, rebalance_every=1)
    pd.testing.assert_frame_equal(
        a.reset_index(drop=True), b.reset_index(drop=True))


def test_basket_is_unchanged_between_rebalance_days(tmp_path):
    xs, _ = _xs(tmp_path)
    res = evaluate_weights(xs, W, top_n=4, bottom_n=4, rebalance_every=5)
    b = _baskets(res)
    days = sorted(b)
    for d in days:
        idx = days.index(d)
        if idx % 5 == 0:
            continue
        assert b[d] == b[days[idx - (idx % 5)]], (
            f"{d} 不是调仓日，篮子却变了")


def test_cost_is_charged_only_on_rebalance_days(tmp_path):
    """非调仓日不换手，成本必须是 0——否则低频被重复收费。"""
    xs, _ = _xs(tmp_path)
    res = evaluate_weights(xs, W, top_n=4, bottom_n=4, rebalance_every=5)
    up = res[res["direction"] == "up"].copy()
    up["d"] = pd.to_datetime(up["pred_date"])
    up["block"] = (up["d"] - up["d"].min()).dt.days  # 只用于分组
    days = sorted(_baskets(res))
    first_of_block = {days[i] for i in range(0, len(days), 5)}
    non_reb = up[~up["pred_date"].isin(first_of_block)]
    reb = up[up["pred_date"].isin(first_of_block)]
    assert len(non_reb) > 0, "样本里没有非调仓日，测不出东西"
    assert float(non_reb["cost"].abs().max()) == 0.0, "非调仓日收了成本"
    assert float(reb["cost"].max()) > 0.0, "调仓日没收成本"


def test_lower_frequency_has_lower_total_cost(tmp_path):
    xs, _ = _xs(tmp_path)
    def total(every):
        r = evaluate_weights(xs, W, top_n=4, bottom_n=4, rebalance_every=every)
        return float(r[r["direction"] == "up"]["cost"].sum())
    assert total(5) < total(1), "降低调仓频率没有降低成本"


def test_daily_basket_actually_changes_day_to_day(tmp_path):
    """防呆：日频下篮子若天天不变，这套频率轴就是恒等参数，测不出任何东西。"""
    xs, _ = _xs(tmp_path)
    res = evaluate_weights(xs, W, top_n=4, bottom_n=4, rebalance_every=1)
    b = _baskets(res)
    days = sorted(b)
    changes = sum(1 for i in range(1, len(days)) if b[days[i]] != b[days[i - 1]])
    assert changes > 0, "日频下篮子从不变化 —— 夹具造不出可区分的样本"


def test_buffer_reduces_turnover_at_same_frequency(tmp_path):
    """缓冲区必须在**同样调仓频率**下降低换手，否则它是个装饰参数。"""
    xs, _ = _xs(tmp_path)
    plain = evaluate_weights(xs, W, top_n=4, bottom_n=4, rebalance_every=1)
    buf = evaluate_weights(xs, W, top_n=4, bottom_n=4, rebalance_every=1,
                           buffer_multiple=3.0)
    c1 = float(plain[plain["direction"] == "up"]["cost"].sum())
    c2 = float(buf[buf["direction"] == "up"]["cost"].sum())
    assert c2 <= c1, f"缓冲区反而增加了成本：{c2} > {c1}"


def test_unfillable_exposed_on_result(tmp_path):
    """可交易性过滤的剔除数要外露，否则「不可买」在报告里是看不见的。"""
    xs, _ = _xs(tmp_path)
    res = evaluate_weights(xs, W, top_n=4, bottom_n=4)
    assert "unfillable" in res.attrs, "剔除数没有外露"
    assert int(res.attrs["unfillable"]) >= 0

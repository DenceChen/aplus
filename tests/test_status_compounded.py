"""概览页必须报**复利口径**——用户的目标是「年化 20%」（2026-10-14）。

## 为什么要单独钉这一条

用户的年化目标只有一种算法能回答：把日收益真正连乘再开根号（CAGR）。
而 `/api/status` 的 `stats` 一直是 `rolling_stats`，它**只出均值、从不连乘**。

实测现任权重跑在 `data/reports/backtest.parquet` 上：

    日均净收益（算术）  -0.68224%
    算术 ×252         -171.92%   ← 报告口径，年化跌幅 >100%，物理上不可能
    复利累计           -96.96%
    真实 CAGR          -84.70%   ← 复利口径

两者差 **14.91%/年**，而且方向上足以把「差一点达标」说成「远超目标」。
所以复利字段不是锦上添花，它是那把尺子。

## 为什么用**真实回测文件**当断言对象

这里不用构造数据，直接钉生产口径：`data/reports/backtest.parquet` 是
`run_daily` 每天落盘的真实验证历史。如果哪天有人把 `stats` 换成别的
统计量、或把 `actual_pct` 口径改了，这条测试立刻红。

但真实数据依赖 `data/` 存在，所以用 `skipif` 而不是直接失败——
**CI 上没有数据仓库时，测试该跳过，不该编一个假数来通过。**
"""
from pathlib import Path

import pandas as pd
import pytest

BACKTEST = Path("data/reports/backtest.parquet")


def _real_history() -> pd.DataFrame:
    return pd.read_parquet(BACKTEST)


@pytest.mark.skipif(not BACKTEST.exists(), reason="生产验证历史不在本地")
def test_real_history_cagr_is_far_above_the_arithmetic_annualisation():
    """真实数据上，两个口径必须差出一截——否则其中一个算错了。

    这条不假设策略好坏，只钉**两个口径的差**：`vol_drag ≈ σ²/2 × 252`。
    差得极小意味着「复利」被实现成了算术的换标签。
    """
    from aplus.verify import compounded_stats

    st = compounded_stats(_real_history())
    assert st["cagr"] is not None, "真实回测历史必须算出 CAGR"
    assert st["n_days"] > 100, f"样本天数异常少：{st['n_days']}"

    gap = abs(st["arith_ann"] - st["cagr"])
    assert gap > 0.01, (
        f"算术 {st['arith_ann']:.2%} 与复利 {st['cagr']:.2%} 几乎相同"
        f"（差 {gap:.4%}），说明复利口径没真正生效")


@pytest.mark.skipif(not BACKTEST.exists(), reason="生产验证历史不在本地")
def test_arithmetic_annualisation_is_no_longer_physically_impossible():
    """**这条测试在 2026-10-14 之前断言的是一个 bug。**

    当时现任权重的算术年化是 **−171.92%**——年化跌幅超过 100% 不可能，
    所以断言写的是 `arith_ann < -1.0`，把「口径是错的」钉成可复现的检查。

    基准污染（`3c846ff`：市场基准把上市前股票也采样进去）
    修掉、重跑之后，实测变成 **−6.14%**（CAGR −11.69%，区间 −21.20%）。

    **「物理上不可能」的那个数，本身就是污染造出来的。**
    这也是为什么修复它不只需要改代码，还需要这条测试自己变红。

    断言方向随之翻转：现在钉的是「**它必须是可能的**」。
    这个方向比原来那个更耐用——原断言一旦被修好就只会报红，
    而新断言在口径再次坏掉时会立刻报红（跌幅又会超过 100%）。
    """
    from aplus.verify import compounded_stats

    st = compounded_stats(_real_history())
    assert st["arith_ann"] > -1.0, (
        f"算术年化 {st['arith_ann']:.2%} 又是物理上不可能的——"
        "基准污染或 actual_pct 口径退化了。查 _market_by_day 是否又采到未上市股票。")
    assert st["cagr"] > -1.0, f"CAGR {st['cagr']:.2%} 不可能"


@pytest.mark.skipif(not BACKTEST.exists(), reason="生产验证历史不在本地")
def test_status_endpoint_carries_the_compounded_ruler():
    """`/api/status` 必须把复利口径递到前端，否则尺子还是躺在库里。"""
    from fastapi.testclient import TestClient

    from aplus.webapp import create_app

    root = BACKTEST.parents[1]          # data/
    app = create_app(root, llm=None)
    body = TestClient(app).get("/api/status").json()
    stats = body["stats"]

    for key in ("total_return", "cagr", "arith_ann", "ann_vol", "vol_drag"):
        assert key in stats, f"/api/status 缺复利字段 {key}"


@pytest.mark.skipif(not BACKTEST.exists(), reason="生产验证历史不在本地")
def test_status_cagr_matches_what_verify_computes():
    """接线不许篡改数值：API 报出来的 CAGR 必须等于直接算的。

    ## 两个数据源不能混为一谈——这条测试第一版就踩了

    第一版拿 `backtest.parquet`（9660 条 / 483 天）当期望值，却让
    `/api/status` 去读 `verification.parquet`。实测后者只有 **20 条 / 1 天**
    （CAGR −99.9%），于是测试红了，红的原因是**我把两个源当成了一个**。

    这两个源的分工是：
      - `verification.parquet` = `run_daily` 的**逐日归档**，概览页/前端读它
      - `backtest.parquet`      = `run_backtest` 的**回测导出**，483 天

    所以期望值必须来自 `verification.parquet` 自己。这也顺带钉住一件事：
    **前端报的天数必须等于归档里的天数**，不许前端偷偷换个源。
    """
    from pathlib import Path

    from fastapi.testclient import TestClient

    from aplus.reports import load_verification_history
    from aplus.verify import compounded_stats
    from aplus.webapp import create_app

    root = BACKTEST.parents[1]
    # 🚨 2026-10-21：期望值必须用**同一个作用域**算 ——
    #   `/api/status` 的命中率已改成**按当前信号分桶**（`aplus/signal_scope.py`），
    #   而 D1 闸门也是分桶的 ⇒ 若这里仍用**整本底稿**算期望值 ⇒
    #   两边样本不同 ⇒ 测试红在一个「口径不同」上（正是本文件 docstring
    #   记的那个第一版错误：把两个源当成了一个）。
    # ⇒ 期望值用**同一个分桶入口**算 ⇒ 这条测试守的仍然是
    #   「**API 不许篡改数值**」（接线层），而**作用域本身**由
    #   `aplus/signal_scope.py` 的守卫与 `test_signal_scoping.py` 守。
    from aplus.signal_scope import scoped_history

    history, _scope = scoped_history(Path(root) / "reports")
    expect = compounded_stats(history)

    app = create_app(root, llm=None)
    body = TestClient(app).get("/api/status").json()
    got = body["stats"]
    # 作用域也钉住：API 报的分桶行数必须等于**分桶后**的行数
    assert body["signal_scope"]["rows_in_scope"] == len(history), (
        f"API 报作用域 {body['signal_scope']['rows_in_scope']} 行，"
        f"而分桶后是 {len(history)} 行 ⇒ 两者不同源")

    assert got["n_days"] == expect["n_days"], (
        f"前端天数 {got['n_days']} 与归档天数 {expect['n_days']} 不符——"
        "说明前端读了另一个数据源")
    if expect["cagr"] is None:
        assert got["cagr"] is None, "归档为空时前端不得编出 CAGR"
    else:
        assert got["cagr"] == pytest.approx(expect["cagr"], rel=1e-9)
        assert got["total_return"] == pytest.approx(expect["total_return"], rel=1e-9)


def test_archive_with_a_single_day_must_not_look_like_a_track_record():
    """归档只有 1 天时，前端拿到的必须是「不可解读」而不是一个假的年化。

    ## 这条测试改过一次，方向是「从报出到不报」

    第一版断言单日样本下 `cagr` **应该有值**，理由是「警示条需要它来
    触发『样本不足』」。接上 `/api/status` 后实测发现那时报的是
    `cagr = -99.90%`、`arith_ann = -681.93%` —— 正是警示条自己警告的那种
    「看起来像业绩、实则是噪音」的数字。

    于是改成：**后端不给**。前端警示由 `n_days` 触发
    （`docketWarning` 的样本不足分支），不需要一个假的 CAGR 去凑。

    天数必须如实传出去：它是唯一能让读者判断样本量的字段。
    """
    from aplus.verify import compounded_stats

    one_day = pd.DataFrame([{
        "pred_date": pd.Timestamp("2026-10-12"), "code": f"{i}.SZ",
        "direction": "up", "actual_pct": -0.5, "cost": 0.0,
        "result": "miss", "market_pct": 0.0,
    } for i in range(10)])
    st = compounded_stats(one_day)
    assert st["n_days"] == 1, "天数是唯一能让读者判断样本量的字段"
    assert st["cagr"] is None, "1 天的 CAGR 不可解读，必须是 None"
    assert st["arith_ann"] is None
    assert st["total_return"] == pytest.approx(-0.5, rel=1e-9), \
        "当日的实际涨跌是事实，必须给"

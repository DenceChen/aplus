"""regime_control_sweep 的口径测试 —— 负对照差值能不能信，全看这三条。

## 为什么要有这个文件

第一版扫描（`signcorrect_freq_sweep.py`）事前登记的主指标是
`年化(up_excess_vs_market) − 年成本`，跑到一半就被它自己的**内建负对照**
证伪了：已知逐符号相反的旧默认配置在 K=63 上拿到 +95.0% 年超额、
+83.0% 净、t=6.67。一个为「挑明天会跌的票」而生的配置不可能有这个本事。

`aplus/verify.py` 的文档早就写了这件事：

> 涨腿超额 `E[r_up] − E[r_mkt]` 在大盘上涨时会自动变大——选股毫无本事、
> 只是踩在 beta 上，也能拿到一个漂亮的正数。

所以改用**负对照差值**：

    signal = 指标(符号修正) − 指标(错号对照)

两组跑在同一批日期、同一份面板上，只差权重符号。共同的部分（市场状态、
板块轮动、2019-2023 的动量牛市）逐项抵消，剩下的是符号本身的贡献。

**这比「挑一个更干净的指标」更可靠**，因为它不依赖对指标性质的假设，
只依赖一个事实：两组除符号外完全同源。

## 这三条测试守的是什么

1. **广度口径必须消掉市场项** —— 不消掉，差值就不是 regime-immune。
2. **配对 t 必须在逐日差上算** —— 各算各的 t 仍把 regime 的逐日波动
   留在分母里，那 t 测的是「两组都怕同一种跌」，不是「符号有用」。
3. **差值符号必须随符号翻转** —— 恒等式级别的自检，防止指标读反了方向
   还报出一个漂亮的正数。
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.regime_control_sweep import (  # noqa: E402
    SIGN_FIXED, WRONG_SIGN, _daily_series, _metrics,
)


def _pred(rows):
    """rows: (pred_date, code, direction, actual_pct, market_pct, cost)

    ## 单位是**小数**，不是百分数

    `actual_pct` / `market_pct` / `cost` 三列在真实产物里都是收益率小数
    （实测 `data/reports/backtest.parquet`：`actual_pct` ∈ [−0.20, +0.20]
    即涨跌停，`market_pct` ∈ [−0.12, +0.04]，`cost` ≤ 0.0027）。

    这一点很容易搞错，代价是**差 100 倍**：本测试文件第一版把 +1% 写成
    `1.0`，`_metrics` 于是给出 −976.0（= −4.0 × 244），一度看着像代码有
    bug——其实数据单位错了。下面的 `test_units_are_fractions_not_percents`
    把这件事钉死。
    """
    df = pd.DataFrame(rows, columns=["pred_date", "code", "direction",
                                     "actual_pct", "market_pct", "cost"])
    df["result"] = "hit"
    return df


def _two_day(up_ret, down_ret, market, up_cost=(0.0, 0.0)):
    return _pred([
        ("2023-01-03", "600000.SH", "up", up_ret[0], market[0], up_cost[0]),
        ("2023-01-03", "600001.SH", "down", down_ret[0], market[0], 0.0),
        ("2023-01-04", "600000.SH", "up", up_ret[1], market[1], up_cost[1]),
        ("2023-01-04", "600001.SH", "down", down_ret[1], market[1], 0.0),
    ])


def test_units_are_fractions_not_percents():
    """钉住单位：**小数**。年化 = 逐日均值 × DAYS_PER_YEAR，结果仍是小数。

    以 A 股涨跌停为标尺：单日 ±10% 在小数口径下是 ±0.10。真实产物里
    `actual_pct` 的极值是 ±0.2009（含 20% 的创业板/科创板）。
    """
    p = _two_day([0.10, 0.10], [0.0, 0.0], [0.0, 0.0])
    m = _metrics(p)
    assert m["annual_up_excess"] == pytest.approx(0.10 * 244, rel=1e-9), (
        f"+10%/日 × 244 必须是 +24.4（= +2440%），实得 {m['annual_up_excess']}；"
        "若得 2440 说明单位被当成了百分数")


# ---------- 1. 广度口径必须消掉市场项 ----------

def test_breadth_series_must_cancel_the_market_term():
    """广度 = E[r_up − cost] − E[r_down]，**市场项必须完全消掉**。

    这是整个负对照差值的地基：若市场项没消掉，两组共同的市场状态就会
    留在差里，差值就不再 regime-immune。

    构造：涨腿 +1%、跌腿 −1%、大盘 **+5%**（大涨日）。
    涨腿超额 `E[r_up] − E[r_mkt]` = 1% − 5% = **−4%**，是负的；
    广度 1% − (−1%) = **+2%**，与大盘涨 5% 无关。
    """
    p = _two_day([0.01, 0.01], [-0.01, -0.01], [0.05, 0.05])
    breadth = _daily_series(p, "breadth")
    assert breadth.tolist() == pytest.approx([0.02, 0.02]), (
        f"广度必须不受大盘涨跌影响，实得 {breadth.tolist()}")

    up_ex = _daily_series(p, "up_excess")
    assert up_ex.tolist() == pytest.approx([-0.04, -0.04]), (
        "对照组：涨腿超额应被大涨的市场压成负数——这正是它含 beta 的证据")


def test_breadth_series_must_be_identical_under_opposite_markets():
    """同一条篮子、同样的收益，大盘 +5% 与 −5% 必须给出**同一个**广度。"""
    boom = _daily_series(_two_day([0.01, 0.01], [-0.01, -0.01], [0.05, 0.05]), "breadth")
    bust = _daily_series(_two_day([0.01, 0.01], [-0.01, -0.01], [-0.05, -0.05]), "breadth")
    assert boom.tolist() == bust.tolist(), (
        f"牛市 {boom.tolist()} 与熊市 {bust.tolist()} 的广度不该不同")


def test_breadth_must_charge_cost_on_the_long_leg_only():
    """成本只挂在涨腿（我们真建仓的一侧）；跌腿是回避清单，不扣。

    跌腿也扣成本 = 凭空惩罚一个不存在的交易，指标会系统性偏保守，
    而且**两组被同等惩罚**，差值的含义就变了（差的是交易频率，不是信号）。
    """
    free = _daily_series(_two_day([0.01, 0.01], [-0.01, -0.01], [0.0, 0.0]), "breadth")
    paid = _daily_series(
        _two_day([0.01, 0.01], [-0.01, -0.01], [0.0, 0.0], up_cost=[0.001, 0.001]),
        "breadth")
    assert free.tolist() == pytest.approx([0.02, 0.02])
    assert paid.tolist() == pytest.approx([0.019, 0.019]), (
        f"成本必须从涨腿扣，实得 {paid.tolist()}")


# ---------- 2. 逐日聚合，不能逐行 ----------

def test_daily_series_aggregates_by_day_not_by_row():
    """同一天多只成分股必须先聚成**日均值**再进序列。

    否则一天的 10 只股票会变成 10 个观测，t 检验的分母被放大 10 倍，
    显著性凭空翻根号 10 倍。这是逐行 vs 逐日的经典错。
    """
    rows = [("2023-01-03", f"60000{i}.SH", "up", r, 0.0, 0.0)
            for i, r in enumerate([0.01, 0.03])]
    rows.append(("2023-01-03", "600099.SH", "down", 0.0, 0.0, 0.0))
    rows.append(("2023-01-04", "600000.SH", "up", 0.02, 0.0, 0.0))
    rows.append(("2023-01-04", "600099.SH", "down", 0.0, 0.0, 0.0))

    s = _daily_series(_pred(rows), "breadth")
    assert list(s.index) == ["2023-01-03", "2023-01-04"], "必须按天聚合"
    assert s.loc["2023-01-03"] == pytest.approx(0.02), (
        "01-03 的涨腿日均应是 (1%+3%)/2=2%；"
        f"若得 3% 就是把同一天的两只当两个观测了")


def test_daily_series_aligns_on_dates_present_in_both_series():
    """两组天数不同时（停牌/闸门拦截），差值只能在**共同日期**上算。"""
    a = _pred([("2023-01-03", "A", "up", 0.01, 0.0, 0.0),
               ("2023-01-03", "B", "down", 0.0, 0.0, 0.0),
               ("2023-01-04", "A", "up", 0.01, 0.0, 0.0),
               ("2023-01-04", "B", "down", 0.0, 0.0, 0.0)])
    b = _pred([("2023-01-03", "A", "up", 0.01, 0.0, 0.0),
               ("2023-01-03", "B", "down", 0.0, 0.0, 0.0)])
    common = _daily_series(a, "breadth").index.intersection(
        _daily_series(b, "breadth").index)
    assert common.tolist() == ["2023-01-03"], "未配对的日期不得进入差值"


# ---------- 3. 指标方向自检 ----------

def test_metrics_report_all_three_indicators_not_just_the_contaminated_one():
    """`_metrics` 必须把三个指标都算出来。

    只报 `up_excess` 就是把已知含 beta 的那个当唯一数字——第一版扫描
    栽在这里。三个一起报，读者才有得选；脚本再自己用负对照做裁决。
    """
    p = _two_day([0.01, 0.01], [-0.01, -0.01], [0.05, 0.05])
    m = _metrics(p)
    assert m["annual_up_excess"] == pytest.approx(-0.04 * 244, rel=1e-6), (
        f"涨腿超额年化算错：{m['annual_up_excess']}")
    assert m["annual_breadth"] == pytest.approx(0.02 * 244, rel=1e-6)
    assert "annual_tilt" in m, "偏度（两端合起来偏离市场）必须报出——它是 beta 暴露的度量"


def test_sign_fixed_and_wrong_sign_differ_only_in_sign():
    """对照组的构造前提：两组**幅度逐一相同**，且翻转集合 = 实测 IC 为负的因子。

    这条看起来像废话，但它是整个差值归因的**唯一依据**。

    ## 注意：翻转的是 3 个因子，不是 4 个（本测试第一版就栽在这）

    最初这里断言「每个因子都逐符号相反」，实跑立刻失败：
    `rev5` 在两组里都是 `+0.3`。查因是 **rev5 的实测 IC 为正**
    （+0.0262，2019–2026），所以它**本来就不该翻**——旧默认的 `+0.3`
    是对的，需要翻的只有 IC 为负的 `mom20 / vol20 / vsur`。

    修法**不是**把 `rev5` 强行翻成 −0.3 造一个全符号翻转的合成对照，
    而是承认现实：`WRONG_SIGN` 就是**真实的上一版生产权重**，
    真实的生产变更本身就是 3 因子翻转。对照组该反映被评估的那个东西，
    不该为了「更干净」而偏离它。

    翻转集合与「IC 为负」严格一致，正是本脚本可以声称「测的是符号效应」
    的依据；`rev5` 两边相同，它在差值里**精确抵消**，不污染归因。
    """
    assert set(SIGN_FIXED) == set(WRONG_SIGN), "两组因子集合必须相同"

    # IC 实测符号（`data/reports/factor_ic_7y.json` 的 overall，2026-10-14）。
    # 硬编码在此处是**刻意的**：对照组构造的依据必须钉死，不能被上游改动
    # 悄悄改掉。若将来重算 IC 导致翻转集合变化，这条会失败并要求复核。
    ic_sign = {"mom20": -1, "rev5": +1, "vol20": -1, "vsur": -1}
    assert set(ic_sign) == set(SIGN_FIXED), "IC 证据表与权重集合不一致"

    for f in SIGN_FIXED:
        assert abs(SIGN_FIXED[f]) == abs(WRONG_SIGN[f]), (
            f"{f} 幅度不同：{SIGN_FIXED[f]} vs {WRONG_SIGN[f]}——"
            "幅度差会把幅度效应混进符号效应")
        if ic_sign[f] < 0:
            assert SIGN_FIXED[f] * WRONG_SIGN[f] < 0, (
                f"{f} 的 IC 为负，权重必须翻号")
        else:
            assert SIGN_FIXED[f] == WRONG_SIGN[f], (
                f"{f} 的 IC 为正，权重必须保持不变——翻它就是引入一个"
                "未测量的符号决策")
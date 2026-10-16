"""成本必须被报出来，而不只是被扣掉（TDD）。

## 这组测试在钉什么

`rolling_stats` 早在 2026-10-11 之前就把成本从涨腿收益里扣掉了
（`actual_pct - cost`），所以**赛马没有在给净值为负的候选排名**。
这一点是对的，但它只做了一半：

    毛超额 +0.19%，成本 0.08% → 净 +0.11%
    毛超额 +0.111%，成本 0.001% → 净 +0.11%

这两行在报告里**完全一样**。而它们根本不是同一个策略——
第一组一年要把 20% 的毛收益交给手续费（5 万本金 / 10 只持仓时，
日频换手 27.5% 的年成本量级就在 20% 上下，等于把用户的目标整个吃掉）。

## 为什么这条对 5 万本金格外要紧

小资金的成本不是线性的：**5 元最低佣金是主导项**。
单只 5000 元时，一次往返的佣金 10 元 = 0.20%，与成交额几乎无关。
按比例建模（大资金口径）会系统性低估小资金的成本，
而用户拿的是 5 万。

所以报告必须给出：
- `avg_cost_drag`  日均成本拖累（收益单位）
- `gross_up_excess` 扣成本前的毛超额
- `cost_share`     **成本吃掉毛超额的几成**（>1 = 毛的超额全被手续费吃光）

`cost_share` 是这三个里唯一能直接回答「这个策略能不能活」的。
"""
import json

import pandas as pd
import pytest

from aplus.verify import rolling_stats


def _frame(up_ret=0.0, down_ret=0.0, market=0.0, cost=0.0, days=30):
    rows = []
    for i in range(days):
        for code in range(10):
            rows.append({"pred_date": f"2026-01-{i + 1:02d}", "code": f"{i}{code}",
                         "direction": "up", "actual_pct": up_ret, "cost": cost,
                         "market_pct": market,
                         "result": "hit" if up_ret > 0 else "miss"})
            rows.append({"pred_date": f"2026-01-{i + 1:02d}", "code": f"x{i}{code}",
                         "direction": "down", "actual_pct": down_ret, "cost": 0.0,
                         "market_pct": market,
                         "result": "hit" if down_ret < 0 else "miss"})
    return pd.DataFrame(rows)


# ── 三个新字段必须存在 ────────────────────────────────────────────

def test_cost_fields_are_present():
    st = rolling_stats(_frame(up_ret=0.002, cost=0.0005))
    for k in ("avg_cost_drag", "gross_up_excess", "cost_share"):
        assert k in st, k


def test_avg_cost_drag_reports_the_daily_drag():
    st = rolling_stats(_frame(up_ret=0.002, cost=0.0005))
    assert st["avg_cost_drag"] == pytest.approx(0.0005, abs=1e-9)


def test_gross_excess_is_net_plus_cost():
    """毛超额 = 净超额 + 成本。扣成本的钱必须看得见，不能凭空消失。"""
    st = rolling_stats(_frame(up_ret=0.002, market=0.001, cost=0.0005))
    net = st["up_excess_vs_market"]
    assert st["gross_up_excess"] == pytest.approx(net + 0.0005, abs=1e-9)


def test_cost_share_is_the_decision_number():
    """毛超额 0.20%、成本 0.08% → 成本吃掉 40%。"""
    st = rolling_stats(_frame(up_ret=0.002, market=0.0, cost=0.0008))
    assert st["cost_share"] == pytest.approx(0.4, abs=1e-6)


def test_cost_share_above_one_means_the_edge_is_gone():
    """成本 > 毛超额 → 策略净值为负，`cost_share` 必须 > 1 而不是被截断。"""
    st = rolling_stats(_frame(up_ret=0.001, market=0.0, cost=0.002))
    assert st["cost_share"] > 1.0


# ── 退化情形必须诚实 ─────────────────────────────────────────────

def test_cost_share_is_none_when_gross_excess_is_zero():
    """毛超额恰为 0 → 比值无定义。返回 None，不返回 0（0 会被读成「成本为零」）。"""
    st = rolling_stats(_frame(up_ret=0.001, market=0.001, cost=0.0005))
    assert st["gross_up_excess"] == pytest.approx(0.0, abs=1e-9)
    assert st["cost_share"] is None


def test_cost_share_is_none_when_cost_is_zero():
    """零成本（冒烟测 / 纯排序研究）不是「成本占比未知」，是「成本为零」。"""
    st = rolling_stats(_frame(up_ret=0.002, market=0.0, cost=0.0))
    assert st["avg_cost_drag"] == pytest.approx(0.0)
    assert st["cost_share"] == 0.0


def test_cost_share_none_when_gross_excess_is_negative():
    """毛超额本身为负 → 比值没有意义，不给数。"""
    st = rolling_stats(_frame(up_ret=-0.002, market=0.0, cost=0.0005))
    assert st["gross_up_excess"] < 0
    assert st["cost_share"] is None


def test_missing_cost_column_treated_as_zero():
    """旧数据没有 cost 列 → 成本 0，**不得报错**（缺列不等于成本未知）。"""
    df = _frame(up_ret=0.002, cost=0.0).drop(columns=["cost"])
    st = rolling_stats(df)
    assert st["avg_cost_drag"] == pytest.approx(0.0)
    assert st["gross_up_excess"] == pytest.approx(st["up_excess_vs_market"])


def test_empty_history_zero_block_still_has_cost_fields():
    """`zero` 兜底字典必须带上新字段，否则空数据时 KeyError。"""
    st = rolling_stats(None)
    assert st["avg_cost_drag"] == 0.0
    assert st["cost_share"] == 0.0
    assert st["gross_up_excess"] == 0.0


def test_empty_dataframe_also_has_cost_fields():
    st = rolling_stats(pd.DataFrame())
    assert "cost_share" in st and "gross_up_excess" in st


# ── 不得改变既有数字 ─────────────────────────────────────────────

def test_net_excess_is_unchanged_by_this_feature():
    """加字段不是为了改口径。净超额的算法必须逐位不变。"""
    df = _frame(up_ret=0.002, market=0.001, cost=0.0005)
    st = rolling_stats(df)
    # 涨腿净 = 0.002 − 0.0005 = 0.0015；减市场 0.001 → 净超额 0.0005
    assert st["up_excess_vs_market"] == pytest.approx(0.0005, abs=1e-9)
    assert st["excess_breadth"] == pytest.approx(
        (0.002 - 0.0005) - 0.001 + 0.001 + 0.0, abs=1e-9)


def test_down_leg_is_still_not_charged():
    """跌腿是回避清单，不产生交易——新字段不得把它算进去。"""
    st = rolling_stats(_frame(up_ret=0.002, down_ret=-0.01, cost=0.0005))
    assert st["avg_cost_drag"] == pytest.approx(0.0005, abs=1e-9)
    assert st["avg_down_return"] == pytest.approx(-0.01, abs=1e-9)

# ── 实盘频率的成本占比：证据缺失时沉默 ───────────────────────────

def _race_report(tmp_path, rows):
    payload = {"kind": "composite_race", "table": rows}
    (tmp_path / "race_composite_v1.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def test_live_cost_share_reports_the_live_frequency(tmp_path):
    """问的是「线上这组 + 这个频率」的成本占比，不是全场最好的一组。"""
    from aplus.champion_health import live_cost_share
    d = _race_report(tmp_path, [
        {"rebalance_every": 1, "weights": {"mom20": 1.0},
         "test_stats": {"cost_share": 0.91}},
        {"rebalance_every": 21, "weights": {"mom20": 1.0},
         "test_stats": {"cost_share": 0.16}},
    ])
    got = live_cost_share(d, 1)
    assert got["known"] is True
    assert got["cost_share"] == pytest.approx(0.91)


def test_live_cost_share_unknown_when_no_report(tmp_path):
    """没有报告 → 未知。不是 0。"""
    from aplus.champion_health import live_cost_share
    got = live_cost_share(tmp_path, 1)
    assert got["known"] is False
    assert got["cost_share"] is None


def test_live_cost_share_unknown_when_frequency_not_present(tmp_path):
    """该频率没有候选 → 未知，不是拿别的频率的数字顶上。"""
    from aplus.champion_health import live_cost_share
    d = _race_report(tmp_path, [
        {"rebalance_every": 21, "weights": {"mom20": 1.0},
         "test_stats": {"cost_share": 0.16}}])
    assert live_cost_share(d, 1)["known"] is False


def test_live_cost_share_takes_the_worst_case_in_that_frequency(tmp_path):
    """同一频率下多个候选，取**最差**的一组。

    取最好的一组是粉饰：问的是「我线上这组有多危险」，
    而报告里未必有线上那组的行。
    """
    from aplus.champion_health import live_cost_share
    d = _race_report(tmp_path, [
        {"rebalance_every": 1, "weights": {"mom20": 1.0},
         "test_stats": {"cost_share": 0.16}},
        {"rebalance_every": 1, "weights": {"mom20": -1.0},
         "test_stats": {"cost_share": 2.92}},
    ])
    assert live_cost_share(d, 1)["cost_share"] == pytest.approx(2.92)


def test_live_cost_share_ignores_rows_without_the_metric(tmp_path):
    """旧报告没有 cost_share → 未知，不当成 0。"""
    from aplus.champion_health import live_cost_share
    d = _race_report(tmp_path, [
        {"rebalance_every": 1, "weights": {"mom20": 1.0}, "test_stats": {}}])
    got = live_cost_share(d, 1)
    assert got["known"] is False


def test_live_cost_share_picks_the_report_by_mtime_not_filename(tmp_path):
    """按文件名排序取「最新」会拿到旧报告 → 永远显示「未测过」。

    `race_composite_*` 在字典序上排在 `ic_guided_race_*` **后面**，
    所以「取最后一个」挑中的恰恰是那份没有 cost_share 的老文件。
    """
    from aplus.champion_health import live_cost_share
    import os, time
    old = tmp_path / "race_composite_zzz.json"
    new = tmp_path / "ic_guided_race_aaa.json"
    old.write_text(json.dumps({"table": [
        {"rebalance_every": 1, "weights": {}, "test_stats": {}}]}), encoding="utf-8")
    new.write_text(json.dumps({"table": [
        {"rebalance_every": 1, "weights": {},
         "test_stats": {"cost_share": 0.91}}]}), encoding="utf-8")
    now = time.time()
    os.utime(old, (now - 3600, now - 3600))     # 旧的，名字却排在后面
    got = live_cost_share(tmp_path, 1)
    assert got["known"] is True
    assert got["cost_share"] == pytest.approx(0.91)


def test_live_cost_share_falls_back_to_older_report_with_the_metric(tmp_path):
    """最新的报告没有该指标时，仍应回退到**有该指标的最近一份**。

    否则新增一个不含 cost_share 的报告就会把已知结论变成未知。
    """
    from aplus.champion_health import live_cost_share
    import os, time
    withm = tmp_path / "race_composite_old.json"
    without = tmp_path / "race_composite_new.json"
    withm.write_text(json.dumps({"table": [
        {"rebalance_every": 1, "weights": {},
         "test_stats": {"cost_share": 0.91}}]}), encoding="utf-8")
    without.write_text(json.dumps({"table": [
        {"rebalance_every": 1, "weights": {}, "test_stats": {}}]}), encoding="utf-8")
    now = time.time()
    os.utime(withm, (now - 3600, now - 3600))
    got = live_cost_share(tmp_path, 1)
    assert got["known"] is True and got["cost_share"] == pytest.approx(0.91)


# ---------- 跌腿健康度（2026-10-14 增补） ----------
#
# 产品承诺是「10 涨 10 跌」。涨腿那一半由 `oos_decision` 的超额闸看着，
# 跌腿那一半此前既没有闸、也没有任何展示——而实测现任权重
# `avg_down_return = +0.126%/日`：它推的「10 只会跌」平均每天**在涨**。
# 半年下来用户每天收到 10 只「会跌」的股票，判决板一个字都没说。

def _down_report(tmp_path, rows):
    payload = {"kind": "composite_race", "table": rows}
    (tmp_path / "race_composite_v1.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def test_live_down_leg_reads_the_incumbent_row_not_the_best_one(tmp_path):
    """**现任那一行**，不是候选池里的极值。

    ⚠️ 本条于 2026-10-16 **换臂**，预注册
    `docs/PREREG_LIVE_DOWN_LEG_INCUMBENT_2026-10-16.md` §2②。
    旧版钉的是「取最差」，理由抄自 `live_cost_share`
    （「报告里未必有线上那组权重的行」）。
    🚨 **那条理由的前提今天已不成立**：`run_daily.py:417` 的 `load_weights`
    回落路径给出确定的 incumbent，`weights.json` 存不存在都不影响
    用户今天拿到哪一组名单 ⇒ 「兑现了吗」问的就是那一行。

    ⇒ 换的是**主体**（这个量是关于谁的），不是**判据臂**
    （`avg_down_return < 0` 一个字未动）。
    ⚠️ `live_cost_share` **不动**：成本问「最坏能有多糟」，
    跌腿问「用户名单跌没跌」——🚨 同一个 `max` 手法套在两个问题上，一个对一个错。
    """
    from aplus.champion_health import live_down_leg
    champ = {"mom20": -1.0}
    d = _down_report(tmp_path, [
        {"rebalance_every": 1, "weights": {"mom20": -1.0},
         "test_stats": {"avg_down_return": -0.040, "hit_rate_down": 0.528}},
        {"rebalance_every": 1, "weights": {"mom20": 1.0},
         "test_stats": {"avg_down_return": 0.126, "hit_rate_down": 0.503}},
    ])
    got = live_down_leg(d, 1, champ, None)
    assert got["known"] is True
    # 现任是 mom20=-1.0 那一行（-0.040），不是涨得最狠的 +0.126
    assert got["avg_down_return"] == pytest.approx(-0.040)
    assert "未兑现" not in got["note"]          # 真跌 ⇒ 不喊


def test_the_two_down_leg_fields_come_from_the_same_row(tmp_path):
    """`avg_down_return` 与 `hit_rate_down` 必须来自**同一行**。

    🚨 旧实现里一个取 `max`、另一个对**另一个字段**也取 `max`
    ⇒ 返回的 dict 里两个数讲的是**两个不同候选**的故事
    （2026-10-16 我把这两条并列读成「矛盾」，读完源码才发现它们不构成矛盾、
    也不构成一致，当场撤回 —— 第 7 层）。

    ★ 这条**独立于**上一条：无论主体是现任还是极值，「同源」都是对的
    ⇒ 它不是同一判据的第二个影子，变异打在其中一条上另一条不会跟着红。
    """
    from aplus.champion_health import live_down_leg
    d = _down_report(tmp_path, [
        # 现任：均值最高（0.126）但命中率最低（0.503）
        {"rebalance_every": 1, "weights": {"mom20": 1.0},
         "test_stats": {"avg_down_return": 0.126, "hit_rate_down": 0.503}},
        # 别人：均值不是最高（0.050）但命中率最高（0.560）
        {"rebalance_every": 1, "weights": {"mom20": -1.0},
         "test_stats": {"avg_down_return": 0.050, "hit_rate_down": 0.560}},
    ])
    got = live_down_leg(d, 1, {"mom20": 1.0}, None)
    assert got["avg_down_return"] == pytest.approx(0.126)
    assert got["hit_rate_down"] == pytest.approx(0.503)   # 0.560 属于另一行


def test_live_down_leg_says_unknown_instead_of_falling_back_to_the_extreme(tmp_path):
    """找不到现任 ⇒ `known=False`，**不许**回落到 max。

    回落会把「没找到现任」悄悄变成「最坏候选」，
    与该函数 docstring 自己的「没有数据就说没有」直接矛盾。
    ⚠️ 失效方向是**真警报变沉默**：现任不在报告里时会丢掉一个真的警报，
    而丢失与「今天没有警报」在判决板上长得完全一样。
    """
    from aplus.champion_health import live_down_leg
    d = _down_report(tmp_path, [
        {"rebalance_every": 1, "weights": {"mom20": -1.0},
         "test_stats": {"avg_down_return": -0.040, "hit_rate_down": 0.528}},
        {"rebalance_every": 1, "weights": {"mom20": 1.0},
         "test_stats": {"avg_down_return": 0.126, "hit_rate_down": 0.503}},
    ])
    got = live_down_leg(d, 1, {"ivol": 0.9, "rsi": 0.7}, None)   # 报告里没有这一行
    assert got["known"] is False
    assert got["avg_down_return"] is None
    assert got["hit_rate_down"] is None


def test_live_down_leg_separates_two_rows_that_differ_only_in_the_buffer(tmp_path):
    """🚨 现任是**三元组**（权重, K, buffer），不是二元组。

    这条判据是**量出来的、不是设计出来的**：先量后判的探针发现，
    incumbent 的权重在 `ic_guided_race_7y_corrected.json` 的 K=5 与 K=1 上
    **各命中 2 行且数值不同** ⇒ 差异字段是 `buffer_multiple`（`3.0` vs `None`），
    而 `run_daily.py:421` 恰恰在读它。

    ⚠️ **若只按 (权重, K) 匹配**：K=1 会命中 2 行 ⇒ 只能返回 `known=False`
    ⇒ **任何跑日频的人会静默失去这条检查**，而「静默」与「今天没警报」
    在判决板上逐位同形（第 119 层）。
    """
    from aplus.champion_health import live_down_leg
    d = _down_report(tmp_path, [
        {"rebalance_every": 1, "buffer_multiple": 3.0,
         "weights": {"mom20": -1.0},
         "test_stats": {"avg_down_return": -0.040, "hit_rate_down": 0.528}},
        {"rebalance_every": 1, "buffer_multiple": None,
         "weights": {"mom20": -1.0},
         "test_stats": {"avg_down_return": 0.050, "hit_rate_down": 0.560}},
    ])
    got = live_down_leg(d, 1, {"mom20": -1.0}, 3.0)      # 现任带 3× 缓冲区
    assert got["avg_down_return"] == pytest.approx(-0.040)
    assert got["hit_rate_down"] == pytest.approx(0.528)  # 0.560 属于 buffer=None 那行


def test_live_down_leg_refuses_to_default_the_champion_weights():
    """🚨 `champion_weights` **不许有默认值**（第 119 层）。

    有默认值时「现任是谁」这个区分会被某次调用悄悄抹掉，
    而那种抹掉的症状与「一切正常」逐位同形 —— 报告照常出数。
    """
    from aplus.champion_health import live_down_leg
    with pytest.raises(TypeError):
        live_down_leg("/nonexistent-reports", 21)
    with pytest.raises(TypeError):
        # 🚨 `buffer_multiple` 同为**必填**：现任是三元体，
        # 给它默认值等于让「现任用的是哪条执行配置」被某次调用悄悄抹掉。
        live_down_leg("/nonexistent-reports", 21, {"mom20": -1.0})


def test_live_down_leg_flags_a_falling_down_leg(tmp_path):
    """跌腿确实在跌时，note 里不该出现「未兑现」——警告必须能区分真假。"""
    from aplus.champion_health import live_down_leg
    d = _down_report(tmp_path, [
        {"rebalance_every": 1, "weights": {"mom20": -1.0},
         "test_stats": {"avg_down_return": -0.040, "hit_rate_down": 0.528}}])
    got = live_down_leg(d, 1, {"mom20": -1.0}, None)
    assert got["known"] is True and "未兑现" not in got["note"]


def test_live_down_leg_unknown_is_none_not_zero(tmp_path):
    """未测过 ≠ 跌腿不跌不涨。返回 0 会被读成「测过了，没跌」。"""
    from aplus.champion_health import live_down_leg
    got = live_down_leg(tmp_path, 1, {"mom20": -1.0}, None)
    assert got["known"] is False
    assert got["avg_down_return"] is None


def test_live_down_leg_unknown_when_frequency_absent(tmp_path):
    """该频率没有候选 → 未知，不拿别的频率的数字顶上。"""
    from aplus.champion_health import live_down_leg
    d = _down_report(tmp_path, [
        {"rebalance_every": 21, "weights": {"mom20": -1.0},
         "test_stats": {"avg_down_return": -0.040}}])
    assert live_down_leg(d, 1, {"mom20": -1.0}, None)["known"] is False


def test_live_down_leg_uses_absolute_return_not_market_relative(tmp_path):
    """判据必须是绝对收益。

    5 万本金做不了空，用户要的是「这 10 只别买」。
    `down_excess_vs_market` 实测只有 45/97 ≈ 随机，拿它当合格线
    等于拿一个约等于随机的量当承诺标准。
    """
    from aplus.champion_health import live_down_leg
    d = _down_report(tmp_path, [
        {"rebalance_every": 1, "weights": {"mom20": -1.0},
         "test_stats": {"avg_down_return": -0.040, "down_excess_vs_market": -0.024}}])
    got = live_down_leg(d, 1, {"mom20": -1.0}, None)
    assert got["avg_down_return"] == pytest.approx(-0.040)
    assert "down_excess" not in got            # 没拿相对量当判据

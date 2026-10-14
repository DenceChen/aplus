"""赛马必须能把「频率 + 缓冲」当成候选的一部分，否则七道闸裁决的是错的东西。

## 事故（2026-10-11 20:42）

独立验证给出的实测差距（同一组权重 `{mom20:-1.0}`，2019-2023）：

    日频净 −168.9%   周频净 −5.3%   月频净 +77.6%   季频净 +105.7%

**276 个百分点**的差距，全部来自两个「执行参数」。
而 `candidate_weights` 只产出权重、`oos_decision` 只看得懂权重——
频率这个维度根本没进过赛马，于是每一轮都在**已知不可行的执行方式**下评判信号。

## 三条防线

1. `race` 把 `rebalance_every` / `buffer_multiple` 透传给 evaluate
2. evaluate 不接受这两个参数时，**带频率的候选必须报错**——
   静默忽略等于把月频候选当天频跑，测出来的数字与实盘完全不符却看不出来
3. `oos_decision` 认现任必须认**三元组**（权重 + 频率 + 缓冲），
   否则同权重的日频行会被月频行冒名顶替
"""
import pytest

from aplus.evolve_race import oos_decision, race


def _xs(n=40):
    """n 个交易日的横截面桩。日期必须真的是日期——
    `2024-01-400` 这种假日期能跑，但读代码的人会以为索引没对齐。"""
    import pandas as pd
    return {"_dates": [d.strftime("%Y-%m-%d")
                       for d in pd.bdate_range("2024-01-01", periods=n)]}


def _eval_fixed(obj_train=1.0, obj_test=1.0, t=5.0, excess=0.01, n_ranked=100):
    """7 参 evaluate：接受频率与缓冲。"""
    seen = []

    def ev(_xs, w, dates, tn, bn, every, buf):
        seen.append((dict(w), every, buf, len(dates)))
        return {"stats": {"up_excess_vs_market": excess,
                          "avg_down_return": -0.001,
                          "excess_t": t,
                          "excess_days": len(dates), "n_ranked": n_ranked},
                "objective": obj_train if len(dates) > 20 else obj_test}
    ev.seen = seen
    return ev


def test_race_passes_execution_params_to_evaluate():
    xs = _xs(400)          # 测试段 ~160 日 → K=21 有 7 个周期，不被排除
    ev = _eval_fixed()
    race(xs, [{"weights": {"mom20": -1.0}, "rebalance_every": 21,
               "buffer_multiple": 3.0}], ev, top_n=10, bottom_n=10)
    assert ev.seen[0][1] == 21 and ev.seen[0][2] == 3.0


def test_race_row_records_execution_params():
    xs = _xs(400)
    r = race(xs, [{"weights": {"mom20": -1.0}, "rebalance_every": 21,
                   "buffer_multiple": None}], _eval_fixed())
    row = r["table"][0]
    assert row["weights"] == {"mom20": -1.0}
    assert row["rebalance_every"] == 21
    assert row["buffer_multiple"] is None


def test_plain_dict_candidate_defaults_to_daily_no_buffer():
    xs = _xs()
    r = race(xs, [{"mom20": 1.0}], _eval_fixed())
    row = r["table"][0]
    assert row["rebalance_every"] == 1
    assert row["buffer_multiple"] is None


def test_legacy_five_arg_evaluate_still_works_for_plain_weights():
    """历史测试与旧脚本都是 5 参 evaluate，纯权重候选必须照常跑。"""
    xs = _xs()

    def ev(_xs, w, dates, tn, bn):
        return {"stats": {"up_excess_vs_market": 0.01,
                          "avg_down_return": -0.001, "excess_t": 5.0,
                          "excess_days": len(dates), "n_ranked": 100},
                "objective": 1.0}

    r = race(xs, [{"mom20": 1.0}], ev)
    assert r["table"][0]["rebalance_every"] == 1


def test_legacy_evaluate_rejects_frequency_candidate_loudly():
    """静默忽略 = 把月频候选当天频跑，数字与实盘不符却看不出来。"""
    xs = _xs()

    def ev(_xs, w, dates, tn, bn):
        return {"stats": {}, "objective": 1.0}

    with pytest.raises(ValueError, match="rebalance_every"):
        race(xs, [{"weights": {"mom20": -1.0}, "rebalance_every": 21}], ev)


def test_legacy_evaluate_rejects_buffer_candidate_loudly():
    xs = _xs()

    def ev(_xs, w, dates, tn, bn):
        return {"stats": {}, "objective": 1.0}

    with pytest.raises(ValueError, match="buffer_multiple"):
        race(xs, [{"weights": {"mom20": -1.0}, "buffer_multiple": 3.0}], ev)


# ── 测试段装不下调仓周期的候选，必须被排除并留痕 ──────────────────
# 2 年窗口切 60/40 → 测试段约 194 个交易日。K=63 只剩 **3 个**调仓周期，
# t 检验在 3 个观测上永远给不出可信结论。给它排个名次，
# 前端就会显示「第 3 名 t=4.2」——那是拿 3 个数当证据。

def test_too_few_test_periods_candidate_is_excluded_and_recorded():
    xs = _xs(400)          # 测试段 ~160 日 → K=63 只剩 2 个周期
    ev = _eval_fixed()
    r = race(xs, [{"weights": {"mom20": -1.0}, "rebalance_every": 63}], ev)
    assert r["table"] == [], "K=63 在 160 日测试段上只有 2 个调仓周期，不该进排名"
    assert r["rejected"], "被排除的候选必须留痕，不能悄悄消失"


def test_exactly_three_test_periods_is_accepted():
    """3 个周期是下限：至少还能区分「一直正」与「一直负」。"""
    xs = _xs(520)          # 测试段 ~208 日 → K=63 剩 3 个周期
    r = race(xs, [{"weights": {"mom20": -1.0}, "rebalance_every": 63}], _eval_fixed())
    assert len(r["table"]) == 1


def test_daily_candidate_is_never_excluded():
    xs = _xs(40)
    r = race(xs, [{"mom20": 1.0}], _eval_fixed())
    assert len(r["table"]) == 1
    assert not r.get("rejected")


def _dec_table(include_champ_daily=True, include_champ_monthly=False):
    """`oos_decision` 读 `table[0]` 当最优，前提是 `race()` 已按训练段降序排好。

    fixture 必须自己排——不排就是拿一个不可能由 `race()` 产出的形状去测裁决，
    那测的是我的假设，不是代码。
    """
    rows = [{
        "weights": {"mom20": 1.0}, "rebalance_every": 1, "buffer_multiple": None,
        "train": {"objective": 0.50, "stats": {"n_ranked": 100}},
        "test": {"objective": 0.50, "stats": {"up_excess_vs_market": 0.001,
                                              "excess_t": 1.0, "excess_days": 250,
                                              "n_ranked": 100}},
    }]
    if include_champ_monthly:
        rows.append({
            "weights": {"mom20": 1.0}, "rebalance_every": 21, "buffer_multiple": None,
            "train": {"objective": 0.90, "stats": {"n_ranked": 100}},
            "test": {"objective": 0.90, "stats": {"up_excess_vs_market": 0.01,
                                                  "avg_down_return": -0.001,
                                                  "excess_t": 9.0,
                                                  "excess_days": 30,
                                                  "n_ranked": 100}},
        })
    return sorted(rows, key=lambda r: r["train"]["objective"], reverse=True)


def test_champion_is_matched_by_full_triple_not_weights_alone():
    """同权重、不同频率是**两个不同的策略**，不能互相冒名。

    变异测试指出：只写「理由里不含某句话」是**测不出**认错现任的。
    认错时 champ 变成月频行（0.90），训练增益算成 0.90−0.90 = 0；
    认对时 champ 是日频行（0.50），增益 = 0.90−0.50 = **0.40**。
    断言 `train_gain` 才真正区分得了。
    """
    d = oos_decision({"table": _dec_table(include_champ_monthly=True)},
                     {"mom20": 1.0})
    assert d.get("train_gain") == pytest.approx(0.40), (
        f"现任必须认日频那一行（训练目标 0.50）；"
        f"得到 {d.get('train_gain')} 说明认成了月频行")
    assert d.get("promote") is True, d.get("reason")


def test_same_weights_different_frequency_is_not_treated_as_champion():
    """`{mom20:1.0} 月频` 绝非现任 `{mom20:1.0} 日频`，
    它必须以自己的执行方式通过裁决，而不是被判成「现任仍是最优」。"""
    rows = _dec_table(include_champ_monthly=True)
    d = oos_decision({"table": rows}, {"mom20": 1.0})
    best = d.get("best")
    assert best is not None
    assert best.get("rebalance_every", 1) == 21, "最优行必须带着自己的频率参与裁决"
    assert "训练段最优仍是现任" not in d["reason"], d["reason"]


def test_weights_only_legacy_tables_still_work():
    """旧形状（没有 rebalance_every 字段）不能因为新增三元组而失效。"""
    rows = [{"weights": {"mom20": 1.0},
             "train": {"objective": 1.0, "stats": {"n_ranked": 100}},
             "test": {"objective": 1.0, "stats": {"up_excess_vs_market": 0.01,
                                                   "avg_down_return": -0.001,
                                                   "excess_t": 5.0,
                                                   "excess_days": 250,
                                                   "n_ranked": 100}}}]
    d = oos_decision({"table": rows}, {"mom20": 1.0})
    assert d["promote"] is False
    assert "现任" in d["reason"]
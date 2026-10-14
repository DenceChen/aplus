"""方向污染闸：因子方向**看过测试数据**才选的候选，不得凭同一段数据晋级。

## 为什么必须加这道闸

2026-10-11 的过程：

1. `evolve_race_fast` 在 2024-10 ~ 2026-10 的 2 年窗口上跑了 31 组，
   我看到 `{mom20:-1.0}`（20 日反转）测试超额 +0.03%，而现任 −0.60%。
2. 诊断又定位到机制：涨榜 Top10 占了全市场 `mom20>100%` 那 **3 只里的 2 只**，
   集中度 370 倍——现任是「追逐极端」。
3. 于是我准备把**反转族**加进候选池。

第 3 步是问题所在。训练/测试切分能挡住「在测试段上偷看某组权重」，
但**挡不住「根据测试段的结论决定要试哪个方向」**。
训练段和测试段来自同一段行情；我看完测试段后选的因子方向，
再用同一段行情的测试段去验收它——**这道闸是漏的**。

同样地，`t = 3.15` 的 Bonferroni 校正只数「跑了几组」，
数不了「我事先知道该往哪个方向猜」。方向已知时，有效比较次数不止候选数。

## 修法

新增第五道闸 `unverified_directions`：标记「方向来自事后观察」的候选。
即便四条腿全过，也只判为**待前瞻验证**，不晋级、不写 `weights.json`。
它必须等**没被看过的**新数据（下一段交易日）才能真正裁决。

## 这条闸不是万能的

它只挡「方向事后选」这一种泄漏。挡不住「因子本身是我挖出来的」
（因子构造过程看了全样本）。后者要靠前瞻数据，只能等。
"""
import pytest

from aplus.evolve_race import oos_decision


def _race(best_w, best_te_excess=0.01, best_t=5.0, champ_te_excess=-0.006,
          champ_t=-2.8, train_gain=0.2):
    # n_ranked 必须给：2026-10-11 新增的「股票池可比闸」会核对候选与现任的
    # 可打分股票数，缺失即视为无法核对并否决。这里给相同值表示两者可比，
    # 股票池闸本身的判别力由 tests/test_universe_comparability.py 负责。
    champ = {"weights": {"mom20": 1.0},
             "train": {"objective": 0.10},
             "test": {"objective": 0.0,
                      "stats": {"up_excess_vs_market": champ_te_excess,
                                "excess_t": champ_t, "excess_days": 194,
                                "n_ranked": 4185}}}
    best = {"weights": best_w,
            "train": {"objective": 0.10 + train_gain},
            "test": {"objective": 0.20,
                     "stats": {"up_excess_vs_market": best_te_excess,
                               "avg_down_return": -0.001,
                               "excess_t": best_t, "excess_days": 194,
                               "n_ranked": 4185}}}
    return {"table": [best, champ]}


CHAMP = {"mom20": 1.0}


def test_promotes_clean_direction_when_all_gates_pass():
    """对照组：方向**不是**事后选的，四条腿全过就该放行——闸不能变成永动机。"""
    d = oos_decision(_race({"mom20": -1.0}), CHAMP)
    assert d["promote"] is True


def test_blocks_direction_chosen_after_seeing_test_data():
    """核心用例：方向看过测试数据后才加进候选池的，不能凭同一段数据晋级。

    2026-10-11 19:42 更新：原先用 `negative_momentum` 做这个用例，
    但它已在**从未看过的** 2019-2023 独立数据上被证实（t=+5.52），
    闸门按设计解除（`DIRECTION_VERDICTS`）。闸门本身仍需覆盖，
    改用**尚未解除**的 `low_ivol`——它的历史成绩来自前视泄露期。
    """
    d = oos_decision(_race({"ivol": -1.0}), CHAMP,
                     unverified_directions={"low_ivol"})
    assert d["promote"] is False
    assert "前瞻" in d["reason"] or "方向" in d["reason"]


def test_blocked_reason_names_the_waiting_condition():
    """否决理由必须说清楚「要什么才能过」，否则只是又一道黑箱闸。"""
    d = oos_decision(_race({"ivol": -1.0}), CHAMP,
                     unverified_directions={"low_ivol"})
    assert "新" in d["reason"] or "未" in d["reason"]
    assert d.get("needs_forward") is True


def test_unverified_gate_does_not_mask_stronger_reasons():
    """显著性闸先否决时，理由必须说显著性，不能被污染闸抢走。"""
    d = oos_decision(_race({"ivol": -1.0}, best_te_excess=0.001, best_t=0.5),
                     CHAMP, unverified_directions={"low_ivol"})
    assert d["promote"] is False
    assert "显著性" in d["reason"]


def test_polluted_gate_runs_after_significance_but_before_promotion():
    """污染闸必须在显著性闸之后：两条都不过时，先报更硬的那条。"""
    d = oos_decision(_race({"ivol": -1.0}, best_te_excess=0.02, best_t=4.0),
                     CHAMP, unverified_directions={"low_ivol"})
    assert d["promote"] is False
    assert d.get("needs_forward") is True
    assert d.get("test_t") == pytest.approx(4.0)


def test_empty_direction_set_is_noop():
    d = oos_decision(_race({"mom20": -1.0}), CHAMP, unverified_directions=set())
    assert d["promote"] is True
    d2 = oos_decision(_race({"mom20": -1.0}), CHAMP, unverified_directions=None)
    assert d2["promote"] is True


def test_promotion_reason_counts_all_six_gates():
    """晋级理由必须说清过了**六**道闸。

    这条是给日志/判决词诚实性加的钉子：理由写「五条腿全过」而实际过了六条
    （2026-10-14 增补跌腿闸），读判决的人会以为跌腿闸不存在——
    那正是本条闸想防的自欺被悄悄抹掉。
    """
    d = oos_decision(_race({"mom20": -1.0}), CHAMP, unverified_directions={"low_ivol"})
    assert d["promote"] is True
    assert "六" in d["reason"] and "方向" in d["reason"]
    assert "跌腿" in d["reason"], "理由里必须出现跌腿闸，否则读判决的人以为没这道闸"

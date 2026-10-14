"""候选之间的**标的池必须可比**，否则赛马的排名没有意义。

## 事故（2026-10-11 19:00，20:00 赛马前发现）

2 年终裁报告的末尾自己写了一行警告：

> ⚠ 各候选的可打分股票数不一致（[4021, 4177, 4183, 4185]）
> ——**这些候选不在同一批标的上比较**，排名只能作参考。
> 根因是部分日线缺列（turn/ivol 等因子恒为 NaN，含该权重的组合会永久剔除这些股票）。

实测当日末根 `turn` 缺失 **62.0%**（抽样 400 推出约 3385 只），
`turnratio` 因此只覆盖约 38% 的横截面。

## 为什么「自己警告了」还不够

那句话在报告正文里，而**裁决函数 `oos_decision` 完全没看它**。
一份会自我警告但照样参与排名的报告，等于没有警告——
排名照常产生、冠军照常产生、晋级照常走完整个流程。

后果不是「排名有点偏」：`{turn20: -1.0}` 是在 **4021 只**里选出的，
`{vol20: -1.0}` 是在 **4185 只**里选出的。前者的股票池少了 4%，
而那 4% 恰恰是**换手率数据缺失的股票**——它们不是随机缺失的，
是被数据完整性筛掉的特定群体。两者排名天然不可比。

## 修法

晋级前比较候选与现任的可打分股票数，差异超阈值即否决。
理由里必须写出两个数，让「谁跟谁不可比」一目了然。
"""
import pytest

from aplus.evolve_race import oos_decision

CHAMP = {"mom20": 1.0}


def _race(best_w, best_n, champ_n, best_excess=0.01, best_t=5.0,
          train_gain=0.2, best_down=-0.001):
    return {"table": [
        {"weights": best_w,
         "train": {"objective": 0.10 + train_gain},
         "test": {"objective": 0.20,
                  "stats": {"up_excess_vs_market": best_excess,
                            "avg_down_return": best_down,
                            "excess_t": best_t,
                            "excess_days": 194, "n_ranked": best_n}}},
        {"weights": CHAMP,
         "train": {"objective": 0.10},
         "test": {"objective": 0.0,
                  "stats": {"up_excess_vs_market": -0.006, "excess_t": -2.8,
                            "excess_days": 194, "n_ranked": champ_n}}},
    ]}


def test_blocks_when_universe_differs_beyond_tolerance():
    d = oos_decision(_race({"turn20": -1.0}, best_n=4021, champ_n=4185),
                     CHAMP, max_universe_gap=0.02)
    assert d["promote"] is False
    assert "股票池" in d["reason"] or "标的" in d["reason"]


def test_allows_small_universe_difference():
    d = oos_decision(_race({"turn20": -1.0}, best_n=4170, champ_n=4185),
                     CHAMP, max_universe_gap=0.02)
    assert d["promote"] is True


def test_identical_universe_passes():
    d = oos_decision(_race({"mom20": -1.0}, best_n=4185, champ_n=4185), CHAMP)
    assert d["promote"] is True


def test_missing_n_ranked_is_treated_as_unverifiable_not_as_fine():
    """缺 `n_ranked` 不得当成「一样多」放行——那正是本页事故的形状。"""
    race = _race({"turn20": -1.0}, best_n=4021, champ_n=4185)
    for row in race["table"]:
        row["test"]["stats"].pop("n_ranked")
    d = oos_decision(race, CHAMP)
    assert d["promote"] is False
    assert "n_ranked" in d["reason"] or "无法核" in d["reason"]


def test_reason_shows_both_numbers():
    """理由里必须写出两个股票池数字，否则读的人无法自行判断差多少。"""
    d = oos_decision(_race({"turn20": -1.0}, best_n=4021, champ_n=4185),
                     CHAMP, max_universe_gap=0.02)
    assert "4021" in d["reason"] and "4185" in d["reason"]


def test_zero_universe_blocks():
    """候选一只都打不了分（n_ranked=0）时必须拦下，不能当成「差得少」。"""
    d = oos_decision(_race({"turnratio": -1.0}, best_n=0, champ_n=4185), CHAMP)
    assert d["promote"] is False


def test_zero_universe_has_its_own_reason_not_the_gap_reason():
    """n_ranked=0 必须由「股票池为空」这道闸拦下，而不是碰巧被 gap 闸兜住。

    变异体 U3 把 `cand_n <= 0` 那道闸删掉、改用 `max(cand_n, 1)` 算 gap，
    结果 gap 仍然巨大照样否决——**结论对，理由错**。
    「因子列全 NaN」和「股票池比现任小 4%」是完全不同的两件事：
    前者是数据缺失要补数，后者是不可比要重跑。混掉会让人去补错的东西。
    """
    d = oos_decision(_race({"turnratio": -1.0}, best_n=0, champ_n=4185), CHAMP)
    assert "为空" in d["reason"], f"理由应指向因子列全 NaN，实际：{d['reason']}"


def test_default_threshold_blocks_real_observed_gap():
    """默认阈值必须能拦住**实测到的**那组差距（4021 vs 4185 = 4.1%）。

    变异体 U4 把默认 `max_universe_gap` 改成 10.0 而测试照过——
    因为每条测试都显式传了自己的阈值，默认值根本没被测到。
    默认值是 `evolve_weekly` 实际走的那条路径，必须单独钉住。
    """
    d = oos_decision(_race({"turn20": -1.0}, best_n=4021, champ_n=4185), CHAMP)
    assert d["promote"] is False
    assert "不可比" in d["reason"]

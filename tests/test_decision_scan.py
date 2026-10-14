"""裁决不能只看训练段第 1 名——Bonferroni 校正了全部比较，就该检查全部。

## 事故（2026-10-11 21:15，复合赛马 84 组之后发现）

`oos_decision` 的流程是 `best = table[0]`，然后对**它**跑七道闸。

而显著性门槛按 `bonferroni_t(len(table))` 计算——**校正的是全部 84 组**。
也就是说：按 84 次比较的严格标准设门槛，却只检查第 1 名。
这不是「保守」，是**不一致**：门槛是 84 组的门槛，被检验的却只有 1 组。

复合赛马的数字把这一点暴露得很清楚：

| K | 训练毛超额 | 训练命中率 | 训练目标分 | 有效观测 |
|---|---|---|---|---|
| 63 | 0.19% | 50.4% | **0.691** ← 第 1 名 | 3 |
| 1 | 0.15% | 52.5% | 0.672 | 194 |
| 5 | 0.08% | 52.1% | 0.606 | 38 |

**毛超额几乎一样**（0.19% / 0.15% / 0.08%），排名差距几乎全部来自命中率。
而命中率在 K=1 与 K=63 之间**根本不是同一个量**：
K=1 问「明天涨没涨」，K=63 问「这 63 天涨没涨」。

**当前第一名是被一个跨频率不可比的量选出来的。**

## 正确做法

按训练目标分顺序**逐个**过闸，命中第一个全过的就晋级；
全都没过则不晋级，并报告**检查了几个**以及最接近的那个卡在哪一道。

这不是「放宽挑选」——Bonferroni 的作用本来就是让「84 组里扫一遍挑最好的」
这件事有可控的假阳性率。门槛不变，变的只是**被检查的对象与被校正的对象一致**。

**不扫描的那些情况**必须保持原样：现任本人永远不会被「晋级成自己」。
"""
import pytest

from aplus.evolve_race import oos_decision

CHAMP = {"mom20": 1.0, "rev5": 0.3, "vol20": 0.5, "vsur": 0.5}


def _row(w, train_obj, test_obj, *, excess=0.01, down_ret=-0.001, t=9.0,
         n_ranked=100, every=1, buf=None, breadth_t=3.0):
    return {"weights": w, "rebalance_every": every, "buffer_multiple": buf,
            "train": {"objective": train_obj, "stats": {"n_ranked": n_ranked}},
            "test": {"objective": test_obj,
                     "stats": {"up_excess_vs_market": excess,
                               "avg_down_return": down_ret,
                               "excess_t": t,
                               "excess_days": 100, "n_ranked": n_ranked,
                               "breadth_t": breadth_t}}}


def _champ_row(train_obj=-1.0, test_obj=-1.0):
    return _row(CHAMP, train_obj, test_obj, excess=-0.006, t=-2.8, breadth_t=-3.4)


def test_second_place_is_promoted_when_first_fails_significance():
    """第 1 名显著性不过、第 2 名全过 → 晋级第 2 名。"""
    table = [
        _row({"mom20": -0.5, "rev5": 0.5}, 0.691, 0.60, every=63, t=0.2),
        _row({"mom20": -1.0}, 0.650, 0.55, every=21, t=9.0),
        _champ_row(),
    ]
    d = oos_decision({"table": table}, CHAMP)
    assert d["promote"] is True, d.get("reason")
    assert d["best"]["weights"] == {"mom20": -1.0}
    assert d["examined"] == 2, "第 1 名不过就检查第 2 名，examined 必须如实计数"


def test_scan_stops_at_the_first_candidate_that_passes():
    table = [
        _row({"mom20": -1.0}, 0.650, 0.55, every=21, t=9.0),
        _row({"mom20": -0.5, "rev5": 0.5}, 0.640, 0.60, t=9.0),
        _champ_row(),
    ]
    d = oos_decision({"table": table}, CHAMP)
    assert d["best"]["weights"] == {"mom20": -1.0}
    assert d["examined"] == 1, "过了就该停，不要继续扫"


def test_champion_is_never_promoted_to_itself():
    """现任排在最前时不得「晋级成自己」，且**不能被算作已检查**。"""
    table = [_champ_row(train_obj=5.0, test_obj=5.0)]
    d = oos_decision({"table": table}, CHAMP)
    assert d["promote"] is False
    assert "现任" in d["reason"]
    assert d["examined"] == 0, "现任不算候选——数进 examined 会虚增「检查了几组」"


def test_bonferroni_threshold_is_still_over_the_whole_table():
    """扫描放宽了挑选面，**门槛不许跟着放宽**——这是 Bonferroni 的全部意义。

    ## 这里必须**确定性构造**，不能用随机种子（事故：2026-10-11 21:25）

    第一版造了 7 个 `t ~ N(0, 1.5)` 的随机候选，指望其中至少一个
    落在 (1.96, bonferroni_t(8)=2.75) 这个窗口里。

    算了一下：单个候选落进窗口的概率约 9.5%，7 个都没有的概率约 50%。
    **变异体只有一半的机会被杀掉**——而且我第一次跑它恰好就绿了。

    一条靠运气才通过的测试等于没有。改成全部 `t = 2.2`：
    门槛 2.75 时全灭，门槛 1.96 时第一个就过——**必然**区分得开。
    """
    table = [_champ_row()]
    for i in range(7):
        table.append(_row({"f%d" % i: 1.0}, 0.9 - 0.01 * i, 0.5,
                          excess=0.002, t=2.2))          # 全部落在 (1.96, 2.75)
    table.sort(key=lambda r: r["train"]["objective"], reverse=True)
    d = oos_decision({"table": table}, CHAMP)
    assert d["promote"] is False, d.get("reason")
    assert d["examined"] == 7, "7 个候选都该被逐一检查过"


def test_nothing_passing_reports_the_training_leader():
    """全都没过时，`best` 应是训练段第一名（最接近的那个），不是最后扫到的。"""
    table = [
        _row({"mom20": -0.5, "rev5": 0.5}, 0.691, 0.60, t=0.2),
        _row({"mom20": -1.0}, 0.650, 0.55, t=1.0),
        _champ_row(),
    ]
    d = oos_decision({"table": table}, CHAMP)
    assert d["promote"] is False
    assert d["best"]["weights"] == {"mom20": -0.5, "rev5": 0.5}
    assert d["examined"] >= 1


def test_examined_count_never_exceeds_table_size():
    table = [_champ_row()] + [_row({"mom20": -1.0}, 0.1 * i, 0.1 * i, t=0.1)
                              for i in range(1, 8)]
    d = oos_decision({"table": table}, CHAMP)
    assert d["examined"] <= len(table)


def test_single_candidate_table_behaves_exactly_as_before():
    """只有一组候选时，扫描不能改变任何东西。"""
    table = [_row({"mom20": -1.0}, 0.5, 0.5, t=9.0), _champ_row()]
    d = oos_decision({"table": table}, CHAMP)
    assert d["examined"] == 1
    assert d["promote"] is True
"""第八道闸：**过闸不等于够得着目标**。

## 为什么需要它

`oos_decision` 的七道闸全部通过，意味着「这个候选比现任强」。
但用户的目标是**年化 20% 净值**，而 K=1 日频的成本上限是 134.2%/年。

于是完全可能出现这样的判决：某候选毛超额 +30%、t=+5.4、六道闸全过、
`promote=True`——**然后它在数学上离 20% 还差 96 个百分点。**

晋级它会怎样？权重被改写，日报照它出单，而实测净值会远低于目标。
**判决语言（"比现任好"）和目标语言（"到 20%"）不是一件事**，
七道闸全都只在说前者。

## 这道闸的位置

放在**最后**：先确认「它确实比现任强」，再问「够不够到 20%」。
反过来会误伤那些方向正确、幅度不够的候选——
它们仍可能是**下一步的研究对象**，只是不该现在就晋级到实盘。

## 为什么它不该变成「不许研究」

`hurdle_blocked=True` 时 `promote` 仍可为 True，只是附上诊断信息；
是否忽略这个诊断去晋级是人的决定，不是代码的。
**代码负责把话说清楚，不负责替人做选择。**
"""
import pytest

from aplus.evolve_race import hurdle_note, oos_decision

CHAMP = {"mom20": 1.0}


def _race(best_w, *, every=1, daily_excess=0.0004, champ_every=1):
    """一份**七闸全过**的赛马表。daily_excess 是**日均**超额——
    与真报告同口径，门槛比较前必须年化（`_judge_candidate` 里乘 244）。"""
    def _row(w, every_, obj, exc, t):
        return {"weights": w, "rebalance_every": every_,
                "train": {"objective": obj},
                "test": {"objective": obj,
                         "stats": {"up_excess_vs_market": exc, "excess_t": t,
                                   "avg_down_return": -0.002,
                                   "excess_days": 194, "n_ranked": 4185}}}
    return {"table": [
        _row(best_w, every, 0.30, daily_excess, 5.0),
        _row(CHAMP, champ_every, 0.10, -0.006, -2.8),
    ]}


# ── 频率决定门槛 ───────────────────────────────────────────────────

def test_daily_rebalancing_is_blocked_from_the_20pct_target():
    """日频：毛超额要 >154%/年。**任何候选都不可能满足**——
    但代码不能假设这一点，要如实算出来并说出来。"""
    n = hurdle_note(gross_excess=0.30, rebalance_every=1)
    assert n["hurdle"] > 1.5
    assert n["reachable"] is False


def test_monthly_is_the_frequency_where_the_target_becomes_reachable():
    n = hurdle_note(gross_excess=0.30, rebalance_every=21)
    assert 0.20 < n["hurdle"] < 0.30
    assert n["reachable"] is True


def test_the_note_states_the_gap_not_just_the_verdict():
    """只说「达不到」不够——**要说出差多少**，那才是可行动的信息。"""
    n = hurdle_note(gross_excess=0.10, rebalance_every=21)
    assert n["reachable"] is False
    assert n["gap"] == pytest.approx(n["hurdle"] - 0.10, abs=1e-9)
    assert f"{n['gap']:+.1%}" in n["note"]


def test_a_negative_gross_excess_is_reported_as_a_gap_not_an_error():
    """负毛超额是常态（反转族实测 −5.6%）。报错会让它「看起来像故障」，
    而它其实是**结论**：这个信号离目标差 32 个百分点。"""
    n = hurdle_note(gross_excess=-0.056, rebalance_every=21)
    assert n["reachable"] is False
    assert n["gap"] > 0


# ── 不能因为门槛高就改变别的东西 ───────────────────────────────────

def test_the_note_does_not_change_when_the_target_is_unreachable_for_everyone():
    """门槛与 `hurdle.py` 同源。**若两处算术漂移，这条测试会红**——
    它就是防漂移的锚。"""
    from aplus.hurdle import COST_REAL_50K, hurdle_rate, rounds_per_year
    n = hurdle_note(gross_excess=0.30, rebalance_every=21)
    assert n["hurdle"] == pytest.approx(
        hurdle_rate(0.20, COST_REAL_50K, rounds_per_year(21)))


def test_target_is_a_parameter_not_a_hardcoded_twenty_percent():
    """目标写死在代码里，就等于用户改了目标而代码不知道。"""
    n = hurdle_note(gross_excess=0.30, rebalance_every=21, target=0.50)
    assert n["hurdle"] > hurdle_note(gross_excess=0.30, rebalance_every=21,
                                     target=0.20)["hurdle"]
    assert n["reachable"] is False


def test_a_missing_rebalance_period_falls_back_to_the_champion_default():
    """旧记录可能没有 `rebalance_every`。**不能崩，也不能当 1**——
    当 1 会让门槛变成 154% 而把一个够得着的候选误判为不可达。"""
    n = hurdle_note(gross_excess=0.30, rebalance_every=None,
                    default_rebalance_every=21)
    assert n["rebalance_every"] == 21
    assert n["reachable"] is True


# ── 接进 oos_decision：诊断必须在真实判决里出现 ──────────────────────
#
# 只测 hurdle_note 等于测了一个没人调用的函数。
# 下面这几条走的是**完整闸链**——它们测的是「诊断会不会真的到达判决」。

def test_a_promoting_verdict_carries_the_hurdle_diagnostic():
    """七闸全过的判决必须**带上门槛诊断**。没有它，这个函数就只是死代码。"""
    d = oos_decision(_race({"turn20": -1.0}), CHAMP)
    assert d["promote"] is True
    assert d["hurdle"]["rebalance_every"] == 1


def test_an_unreachable_candidate_says_so_in_the_reason_people_actually_read():
    """只躺在字段里不够——**理由是唯一被复制进报告的东西**。
    字段可以没人翻，理由一定会进日报。"""
    # 日均 0.0004 → 年化 +9.8%，而 K=1 门槛 154.2%：差 144 个百分点。
    d = oos_decision(_race({"turn20": -1.0}, daily_excess=0.0004), CHAMP)
    assert d["promote"] is True
    assert d["hurdle"]["reachable"] is False
    assert "够不着" in d["reason"]


def test_a_reachable_candidate_does_not_get_a_scary_warning():
    """**反向也必须测**：够得着的候选不该被无谓地标红。
    天天误报的告警，几周内就没人看了——那是给守卫自己挖坑。"""
    # K=21 月频：门槛 26.4%。日均 0.0002 → 年化 +4.9%…不够；
    # 给 0.0015/日 → 年化 +36.6% ≥ 26.4%，够得着。
    d = oos_decision(_race({"turn20": -1.0}, every=21, daily_excess=0.0015),
                     CHAMP)
    assert d["promote"] is True
    assert d["hurdle"]["reachable"] is True
    assert "够不着" not in d["reason"]


def test_the_diagnostic_does_not_flip_the_verdict():
    """**这条是本模块的立场**：够不着目标 ≠ 不许研究。
    代码把话说清楚，是否无视诊断去晋级是人的决定。
    若将来有人把它改成硬闸，这条会红——那时需要的是一次显眼的推翻，
    而不是一次悄悄的收紧。"""
    d = oos_decision(_race({"turn20": -1.0}, daily_excess=0.0004), CHAMP)
    assert d["hurdle"]["reachable"] is False and d["promote"] is True


def test_the_daily_excess_is_annualized_before_comparison():
    """`up_excess_vs_market` 是**日均**，门槛是**年化**。
    拿日均直接比年化门槛，会把候选错判成「差 154 个百分点」——
    而它其实只差 144 个百分点。口径错了，数字照样看着体面。"""
    d = oos_decision(_race({"turn20": -1.0}, every=21, daily_excess=0.0015),
                     CHAMP)
    assert d["hurdle"]["gap"] == pytest.approx(
        d["hurdle"]["hurdle"] - 0.0015 * 244, abs=1e-9)


# ── 闸门名单不许漂 ──────────────────────────────────────────────────
#
# 事故（2026-10-14）：同一个模块里 docstring 说「七道闸」、
# 行内注释说「第五道闸：方向污染」、判决理由说「六条腿全过」，
# 三个数字互相矛盾，且都跟代码里的实际闸数（9）不符。
# 三处都是手写常量，每加一道闸都要人记得回头改三处——从没有人记得。

def _judge_source() -> str:
    import inspect

    from aplus import evolve_race
    return inspect.getsource(evolve_race._judge_candidate)


def _judge_lines() -> list[str]:
    import inspect

    from aplus import evolve_race
    return inspect.getsource(evolve_race._judge_candidate).splitlines()


def test_every_listed_gate_actually_blocks_promotion():
    """**名单里每一项都必须在源码里指出它的 `return`。**

    第一版我只数「`promote: False` 的返回点数」== 名单长度，于是绿灯——
    但我把 `breadth_t 显著` 写进了名单，而那一行**只拼警告、没有 return**。
    它是披露，不是闸。判决对外宣称「12 道闸全过」，其中一道不否决。

    第二版仍然会数出同样的 12 个 return——因为 `breadth_t` 分支本来就没有
    return，而我的名单写成了 11 项 + 1 项误登记。**长度对了，语义错了。**
    所以这条测试不数长度，改成**逐项验证语义**。
    """
    from aplus.evolve_race import GATE_NAMES, GATE_WARNINGS

    src = "\n".join(_judge_lines())
    # 判优闸与可测性守卫都必须真的 return
    assert src.count('"promote": False') == len(GATE_NAMES), (
        f"否决返回点 {src.count(chr(34) + 'promote' + chr(34) + ': False')} "
        f"个 vs 名单 {len(GATE_NAMES)} 项——加闸时必须同步补名单"
    )
    # 警告项**不得**出现在否决名单里（这是本条测试真正要防的那件事）
    overlap = set(GATE_NAMES) & set(GATE_WARNINGS)
    assert not overlap, (
        f"{overlap} 同时出现在否决名单和警告名单里——"
        f"**只披露不否决的检查混进否决名单，会让判决对外虚报闸数**"
    )
    for name in GATE_WARNINGS:
        assert name not in GATE_NAMES, f"{name} 是警告项，不得计入 N_GATES"


def test_a_warning_only_check_cannot_be_reported_as_a_passed_gate():
    """**端到端钉住这条语义**：`breadth_t` 不显著时判决仍然是 promote=True，
    所以它绝不能出现在「N 道闸全过」之外还占一个名额。"""
    from aplus.evolve_race import GATE_WARNINGS, N_GATES, oos_decision

    # breadth_t = -9.0，远低于临界 → 必然触发警告
    race = _race({"turn20": -1.0}, every=21, daily_excess=0.0015)
    race["table"][0]["test"]["stats"]["breadth_t"] = -9.0
    d = oos_decision(race, CHAMP)
    assert d["promote"] is True, "breadth_t 不显著但**不否决**——这是既定政策"
    assert "⚠️" in d["reason"] or "广度" in d["reason"], "警告必须出现在理由里"
    assert d["reason"].startswith(f"{N_GATES} 道闸全过")
    # 关键：声称的闸数**不含**这个警告项
    assert N_GATES == len(__import__("aplus.evolve_race",
                                     fromlist=["x"]).GATE_NAMES)
    assert "breadth_t 显著" in GATE_WARNINGS


def test_the_verdict_reason_says_how_many_gates_actually_passed():
    """判决理由里的数字必须**由名单算出**，不能手写。
    理由是唯一会被复制进报告的东西，它说错就是对外说错。"""
    d = oos_decision(_race({"turn20": -1.0}), CHAMP)
    from aplus.evolve_race import N_GATES

    assert d["reason"].startswith(f"{N_GATES} 道闸全过")

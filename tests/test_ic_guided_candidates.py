"""IC 引导候选：候选池必须能表达「符号与现任相反」这个假设。

## 这组测试在钉什么

2026-10-12 的 IC 诊断发现：8 个因子里 7 个 IC 为负，
而现任 `{mom20:1.0, rev5:0.3, vol20:0.5, vsur:0.5}` 给其中三个挂了正权重。
再看候选池——14 组里 9 组是 `mom20` 正权重，没有任何一组同时用负 `vol20`。

**如果数据说的是「该反着来」，赛马在候选生成阶段就看不见那个组合。**
这不是调参问题，是搜索空间缺了一块。

于是有了 `ic_guided_race.ic_candidates`。它的正确性必须钉住三件事：

1. **符号真的来自 IC**，且能产出与现任相反的组合——否则这脚本白写。
2. **测不出来的因子不得默认成正号**。`{f: +1.0 if ic > 0 else -1.0}`
   在 ic 缺失时会走 `None > 0` 的分支或直接崩，而「崩溃」在这里
   反而是安全的一种失败——真正危险的是静默塞进一个 +1。
3. **候选必须带 `source` 标记**。报告里混着两组来源却分不清，
   就无法回答「是 IC 引导赢了，还是它只是碰巧」。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from ic_guided_race import ic_candidates, ic_champion_sign  # noqa: E402
from aplus.factor_ic import window_block  # noqa: E402


def _ic(**kwargs):
    """构造 train_ic 的返回形状：值 -> {mean, t, n}。"""
    return {k: (v if isinstance(v, dict) or v is None
                else {"mean": v, "t": 3.0, "n": 900})
            for k, v in kwargs.items()}


# ── 符号真的来自 IC ──────────────────────────────────────────────

def test_candidates_flip_sign_to_match_ic():
    """IC 为负 → 权重为负。符号必须由数据决定，不是手调。"""
    cands = ic_candidates(_ic(mom20=-0.05, rev5=0.03))
    signs = next(c["weights"] for c in cands
                 if c["source"] == "ic_sign_all")
    assert signs == {"mom20": -1.0, "rev5": 1.0}


def test_candidates_include_the_combo_the_standard_pool_lacks():
    """标准池里没有「负 vol20 + 正 mom20」，引导候选必须能产出它。

    这条是本脚本存在的理由：产不出这个组合，诊断就只是又一张好看的表。
    """
    ic = _ic(mom20=0.04, vol20=-0.05)
    produced = {c["weights"]["mom20"] for c in ic_candidates(ic)
                if "mom20" in c["weights"]}
    assert any(w > 0 for w in produced)
    # 反向那组也必须在
    ic2 = _ic(mom20=-0.04, vol20=-0.05)
    got = next(c["weights"] for c in ic_candidates(ic2)
               if c["source"] == "ic_sign_all")
    assert got == {"mom20": -1.0, "vol20": -1.0}


def test_ic_mag_candidates_weight_by_magnitude():
    """`ic_mag_all` 用 IC 幅度当权重：更强的因子权重更大。"""
    cands = ic_candidates(_ic(mom20=-0.05, vol20=-0.02))
    got = next(c["weights"] for c in cands if c["source"] == "ic_mag_all")
    assert abs(got["mom20"]) > abs(got["vol20"])


# ── 测不出来的因子不得被塞进候选 ─────────────────────────────────

def test_unmeasurable_factor_is_excluded_not_defaulted_positive():
    """IC 为 None（样本不足）→ 该因子**整个不进候选**。

    塞一个默认 +1 意味着「我们测不出它，于是赌它正向」——
    这正是把噪声当信号的那一步。
    """
    cands = ic_candidates(_ic(mom20=-0.05, rsi=None))
    for c in cands:
        assert "rsi" not in c["weights"], c["source"]


def test_factor_with_too_few_days_is_not_used():
    ic = {"mom20": {"mean": -0.05, "t": -3.0, "n": 3},
          "ivol": {"mean": None, "t": None, "n": 3}}
    for c in ic_candidates(ic):
        assert "ivol" not in c["weights"]


def test_no_usable_ic_yields_no_candidates():
    """全测不出来时返回空列表，而不是造一组默认权重。"""
    assert ic_candidates({"mom20": None, "rsi": {"mean": None, "t": None, "n": 1}}) == []
    assert ic_candidates({}) == []


def test_ic_entry_with_none_mean_but_populated_dict_is_skipped():
    """形状合法但 mean 为 None → 同样排除。`if v` 对非空 dict 恒真。"""
    cands = ic_candidates({"mom20": {"mean": None, "t": None, "n": 900}})
    assert cands == []


# ── 来源标记 ─────────────────────────────────────────────────────

def test_every_candidate_carries_a_source_tag():
    """报告里两组来源混在一起却分不清，就答不出「谁赢的」。"""
    for c in ic_candidates(_ic(mom20=-0.05, rev5=0.03)):
        assert c["source"].startswith("ic_")


def test_candidates_span_multiple_rebalance_frequencies():
    """IC 只定符号，定不了多久调一次一次。频率维度不能丢。"""
    freqs = {c["rebalance_every"] for c in ic_candidates(_ic(mom20=-0.05))}
    assert len(freqs) >= 3


# ── 现任重定符号 ─────────────────────────────────────────────────

def test_champion_sign_uses_ic_and_drops_unmeasurable():
    """现任的因子按 IC 重定符号；测不出来的置 0 而不是留原值。

    留原值 = 「测不出来就沿用现任的符号」，那等于假装测过。
    """
    got = ic_champion_sign(_ic(mom20=-0.05, rev5=0.03, vsur=None),
                           {"mom20": 1.0, "rev5": 0.3, "vsur": 0.5})
    assert got["mom20"] < 0            # 现任 +1.0 → 变负
    assert got["rev5"] > 0             # 现任 +0.3 → 保持正
    assert "vsur" not in got           # 测不出来 → 不出现


def test_champion_sign_returns_none_when_nothing_measurable():
    """全测不出来时返回 None（= 不产出候选），不是空 dict 混进候选池。"""
    assert ic_champion_sign({"mom20": None, "vol20": None},
                            {"mom20": 1.0, "vol20": 0.5}) is None


def test_champion_sign_of_zero_ic_is_dropped():
    """IC 恰为 0 → 该因子不贡献方向，留着会把合成分拉向噪声。"""
    got = ic_champion_sign(_ic(mom20=0.0, rev5=0.03), {"mom20": 1.0, "rev5": 0.3})
    assert "mom20" not in got and "rev5" in got


# ── t 检验的分块单位必须等于持有期 ────────────────────────────────

def test_window_block_equals_holding_period():
    """相邻 hold21 的 IC 共享 20 天数据，观测单位必须是 21 而不是 1。

    块长用错不会报错，只会让 t 虚高几倍——正好撞上显著性闸。
    """
    assert window_block("lag1_real") == 1
    assert window_block("hold4") == 4
    assert window_block("hold21") == 21
    assert window_block("lag0_leaky") == 1


@pytest.mark.parametrize("window,expect", [("lag1_real", 1), ("hold21", 21)])
def test_window_block_never_below_one(window, expect):
    assert window_block(window) >= 1
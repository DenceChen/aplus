"""显著性否决理由里那句「实测随机权重也能过这道闸（30 组里 1 组）」必须消失。

## 这句话是怎么来的

`significance_check.py` 在 **2026-10-11** 跑过一次随机对照，把结果**手抄**进了
`oos_decision` 的否决理由模板里，作为一句固定文案：

    「实测随机权重也能过这道闸（30 组里 1 组），故判为噪音」

抄的时候三件事和当前这次裁决**全不相干**（`data/reports/significance.json` 可查）：

| | 抄写时的实测 | 引用它的裁决 |
|---|---|---|
| 窗口 | 2024-01-02 ~ 2026-10-09 | 任意（本次 2025-12-18 ~ 2026-10-12） |
| 权重 | mom20:+0.5 / rev5:+0.5 / vol20:-1.0 | 任意（本次是 neg-mom20 族） |
| 判据 | `up_excess_vs_market > 0` | **t 闸门** `t > t_crit` |

第三行是致命的。`> 0` 是一个**没有统计含义**的弱闸：零假设下对称分布的正值
概率约 0.5，30 组里过闸的**期望数是 15 组**，不是 1 组。实测只有 1 组，
是因为那族随机权重本身极度左偏（均值 −0.456%/日，最好的也才 +0.080%/日）
——**它量的是权重族的偏斜，不是闸门的漏报率**。

所以这句话在裁决里是**反向误导**：它暗示「这道闸挡不住随机」，而真实情况是
86 次比较下期望假阳性只有 **0.05 组**（`0.05/86 × 86`）。闸门严得不能再严，
`t=1.12` 被挡是**正确**的。

## 为什么这属于代码问题而不是文案问题

它不是「措辞不准」，是**一条不成立的统计断言被无条件播报**。它会在每一次
显著性否决时复读，包括那些**样本量充足、t 值只是差一点**的场合——在那里
读者会被引导去怀疑闸门，而真相是闸门没问题、样本量不够。两者的下一步动作
完全相反：前者该放松 alpha，后者该攒窗口。

## 正确的信息是什么

期望假阳性数是可以**当场算出来**的，不需要引用任何历史实验：

    E[假阳性] = n_cmp × P(|t| > t_crit | H0) = n_cmp × (alpha / n_cmp) = alpha = 0.05

即：**无论候选池多大，这道闸的设计期望假阳性恒为 α = 0.05 组。**
这才是应该写进理由里的数字——它自洽、可复算、不依赖任何一次性的随机实验。
"""
import pytest

from aplus.evolve_race import oos_decision, split_window
from aplus.race_reason import expected_false_positives

CHAMP = {"mom20": 1.0}
CAND = {"mom20": -1.0}


def _mk(n):
    return [f"d{i:03d}" for i in range(n)]


def _k(w):
    return tuple(sorted(w.items()))


def _wire(spec, holder):
    def ev(sections, w, dates, top_n, bottom_n):
        tr_obj, te_obj, te_exc = spec[_k(w)]
        is_train = bool(dates) and dates[0] in holder["train"]
        return {"objective": float(tr_obj if is_train else te_obj),
                "judged": 1000,
                "stats": {"up_excess_vs_market": float(0.01 if is_train else te_exc),
                          "avg_down_return": -0.001, "overall_hit_rate": 0.51,
                          "excess_t": 0.2, "excess_days": 1,
                          "n_ranked": 4897}}
    return ev


def _rejected_by_significance(n_cmp, t_test, excess_days):
    """构造一次「只被显著性闸拦下」的裁决，返回其 reason 全文。"""
    from aplus.evolve_race import race

    spec = {_k(CHAMP): (0.10, 0.10, 0.001), _k(CAND): (0.50, 0.30, 0.0012)}
    holder = {}
    ev = _wire(spec, holder)
    xs = {"_dates": _mk(100)}
    tr, _ = split_window(xs["_dates"])
    holder["train"] = set(tr)
    r = race(xs, [CHAMP, CAND], ev)
    for row in r["table"]:
        row["train"]["stats"]["excess_t"] = 0.2
        row["test"]["stats"]["excess_t"] = t_test
        row["test"]["stats"]["excess_days"] = excess_days
        row["test"]["stats"]["n_ranked"] = 4897
    d = oos_decision(r, CHAMP, significance_lookup=n_cmp)
    assert d["promote"] is False, f"本用例只测文案，先确保它确实被显著性拦下：{d}"
    return d["reason"]


# ---------- 硬编码的旧随机对照数字必须消失 ----------

def test_reason_does_not_quote_a_stale_random_null_experiment():
    """那句话里的「30 组里 1 组」来自另一窗口、另一权重、另一判据，必须移除。

    这是本次事故的直接载体：它让 `t=1.12` 被拒看起来像「闸门挡不住随机」，
    而真相是 86 次比较的期望假阳性只有 0.05 组。
    """
    reason = _rejected_by_significance(86, 1.12, 193)
    assert "30 组里 1 组" not in reason, (
        f"否决理由仍在引用一次性的随机对照实验（不同窗口/权重/判据）：{reason}")
    assert "随机权重也能过这道闸" not in reason, (
        f"否决理由仍在断言「随机权重能过 t 闸门」，该断言无实测支撑：{reason}")


def test_reason_states_the_real_expected_false_positive_count():
    """理由里应当给出**当场可复算**的期望假阳性数，而不是引用历史实验。"""
    reason = _rejected_by_significance(86, 1.12, 193)
    assert "期望假阳性" in reason, (
        f"否决理由应当报出本轮的真实期望假阳性数：{reason}")
    assert f"{expected_false_positives(86):.2f}" in reason, (
        f"期望假阳性数应为 {expected_false_positives(86):.2f} 组"
        f"（= alpha，与候选池大小无关），实得理由：{reason}")


def test_expected_false_positives_is_alpha_regardless_of_pool_size():
    """Bonferroni 的定义性质：校正后整池的期望假阳性恒为 α。

    这条是上一个测试的数学依据，也是「门槛随候选数收紧」没有放松全局
    错误率的证明——收紧的是**单点**门槛，放松的是**比较次数*，两者相消。
    """
    for n_cmp in (2, 14, 86, 97, 500):
        assert expected_false_positives(n_cmp) == pytest.approx(0.05), (
            f"{n_cmp} 次比较下期望假阳性应为 0.05 组，实得 "
            f"{expected_false_positives(n_cmp)}")


def test_wide_sample_weak_t_is_rejected_without_pretending_the_gate_failed():
    """752 个观测、t 不足的场合：闸门是对的，理由不得暗示闸门失效。"""
    reason = _rejected_by_significance(97, 2.0, 752)
    assert "期望假阳性" in reason
    assert "随机权重也能过" not in reason


def test_reason_still_reports_the_measured_quantities():
    """移除旧文案不等于削弱理由：t、观测数、df、临界值、真实 p 都必须还在。"""
    reason = _rejected_by_significance(86, 1.12, 193)
    for token in ("1.12", "193", "df=192", "3.50"):
        assert token in reason, f"理由必须保留实测口径 {token}，实得：{reason}"

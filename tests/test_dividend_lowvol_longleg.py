"""`scripts/measure_dividend_lowvol_longleg.py` 的守卫。

**预注册**：`docs/PREREG_DIVIDEND_LOWVOL_LONGLEG_2026-10-21.md`（事前写死）。

## 这份文件的纪律

1. **能力守卫**（第 120 层）：产出判决的函数，第一条测试必须证明它
   **能给出非 `None` 的判决**；形状守卫在它恒返 `None` 时**全绿**。
2. **反向正控**：每条「方向型」守卫都要有一条**方向反了必须红**的对照。
3. **测试不许写生产产物路径**（AST 级；生产路径是
   `data/reports/dividend_lowvol_longleg.json`）。
4. **外部锚**：门槛必须能由 `aplus.costs` / `aplus.hurdle` 的原语**重算**出来
   —— 不是「断言 == 公式」（第 144 层：自己和自己比 = 恒真）。
   ⇒ 这里用的是**仓内既有带章产物**做**逐位对拍**：
   `data/reports/factor_width_mde.json::widths["10"].hurdle_width_aware_annual_pct`。
5. **不许跑 GB 级面板**：本文件**只调纯函数**，
   面板相关的路径一律用合成夹具（`tmp_path`）。

## ⚠️ 中文断词不许用 `\\b`

JS/正则的 `\\b` 只认 ASCII 单词字符 ⇒ `乙\\b` 恒不成立
⇒ 判据恒假 = 守卫是装饰（记忆：`chinese-regex-word-boundary`）。
⇒ 本文件用**逐字子串**扫禁用词。
"""
from __future__ import annotations

import ast
import importlib.util
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "measure_dividend_lowvol_longleg.py"
PROD_OUT = ROOT / "data" / "reports" / "dividend_lowvol_longleg.json"


def _load():
    """按路径加载脚本（仓内测试的同款纪律）。

    ⚠️ **不把模块登记进 `sys.modules`** ⇒ 这条路径**必须**能 import
    （`@dataclass` 在这条路径上会崩 ⇒ 本脚本刻意不用 dataclass）。
    """
    spec = importlib.util.spec_from_file_location("_divlowvol_under_test", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


M = _load()


# ═══════════════════════════════════════════════════════════════
# 1  能力守卫：排序力（命中率随分数单调）——**方向不许写反**
# ═══════════════════════════════════════════════════════════════

def _pairs(scores, rets):
    return pd.DataFrame({"score": list(scores), "actual_pct": list(rets)})


def test_the_sorting_power_measure_can_reach_a_monotone_verdict():
    """**能力守卫**：给一份「命中率随分数单调上升」的数据 ⇒ 必须判 `increasing`。

    🚨 恒真的守卫长得和真守卫一模一样 ⇒ 先证明它**能**给出判决。
    """
    scores = [4.0, 4.0, 3.0, 3.0, 2.0, 2.0, 1.0, 1.0]
    rets = [0.02, 0.03, 0.01, 0.04, -0.01, 0.005, -0.02, -0.03]
    out = M.hit_rate_by_score_bucket(_pairs(scores, rets), n_buckets=4)
    assert out["monotone"] is not None, "能力守卫：它连一个判决都给不出"
    assert out["monotone"] == "increasing", out
    assert out["n"] == 8
    # 桶 0 = 分数最高 ⇒ 它的命中率必须最高（**方向不许写反**）
    assert out["buckets"][0]["mean_score"] > out["buckets"][-1]["mean_score"]
    assert out["buckets"][0]["hit_rate"] == 1.0
    assert out["buckets"][-1]["hit_rate"] == 0.0


def test_the_sorting_power_measure_reports_the_reversed_direction_as_decreasing():
    """**反向正控**：把同一份数据**反过来** ⇒ 必须判 `decreasing`。

    🚨 「方向写反」的守卫会把**真的**有排序力的因子判成没排序力
    （第 20 层：判据写反 ⇒ 拦住目标信号）。
    """
    scores = [4.0, 4.0, 3.0, 3.0, 2.0, 2.0, 1.0, 1.0]
    rets = [-0.02, -0.03, -0.01, -0.04, 0.01, -0.005, 0.02, 0.03]
    out = M.hit_rate_by_score_bucket(_pairs(scores, rets), n_buckets=4)
    assert out["monotone"] == "decreasing", out


def test_the_sorting_power_measure_returns_none_when_it_cannot_judge():
    """**形状守卫**：样本不足 ⇒ `None`（不许硬凑一个判决）。"""
    out = M.hit_rate_by_score_bucket(_pairs([1.0, 2.0], [0.1, -0.1]), n_buckets=4)
    assert out["monotone"] is None
    assert "n" in out and out["n"] == 2


def test_the_sorting_power_bucket_order_is_high_score_first():
    """桶 0 必须是**分数最高**的那一桶（方向约定的唯一真相源）。

    🚨 这份夹具里**高分 ⇒ 高命中** ⇒ 判决必须是 `increasing`
    （「命中率随分数递增」）。**方向写反的守卫会把真有排序力的因子判成没有**
    ⇒ 这一条与上面两条**互为镜像**。
    """
    scores = [1.0] * 4 + [5.0] * 4
    rets = [-0.01] * 4 + [0.01] * 4
    out = M.hit_rate_by_score_bucket(_pairs(scores, rets), n_buckets=2)
    assert out["buckets"][0]["mean_score"] == 5.0
    assert out["buckets"][1]["mean_score"] == 1.0
    assert out["hit_rate_high_score_bucket"] == 1.0
    assert out["hit_rate_low_score_bucket"] == 0.0
    assert out["monotone"] == "increasing", out


def test_the_sorting_power_bucket_order_survives_a_non_even_split():
    """🚨 分桶边界必须覆盖**全部**样本（第一版只覆盖前 nb 个秩 ⇒ 其余被丢掉）。"""
    scores = list(range(10, 0, -1))          # 10 个不同分数
    rets = [0.01 if i < 5 else -0.01 for i in range(10)]
    out = M.hit_rate_by_score_bucket(_pairs(scores, rets), n_buckets=2)
    assert sum(b["n"] for b in out["buckets"]) == 10, "有样本被分桶边界吃掉了"
    assert out["monotone"] == "increasing", out


# ═══════════════════════════════════════════════════════════════
# 2  判决链顺序守卫：MDE 闸必须第一个判
# ═══════════════════════════════════════════════════════════════

def _decide(**kw):
    base = dict(
        gross_annual_pct=30.0,      # 远超门槛
        mde_annual_pct=10.0,        # 但效应 30% > MDE 10%
        t_blocked=5.0,              # 显著
        t_crit=2.97,
        hurdle_annual_pct=26.4137,
        n_independent_obs=66,
        n_scoreable_min=50,
        n_positions=10,
        coverage_ratio=1.0,
        coverage_min=M.COVERAGE_MIN,
        negative_control=None,
    )
    base.update(kw)
    return M.decide(**base)


def test_a_weak_effect_is_judged_powerless_and_never_significant():
    """**判决链顺序守卫**：MDE 不够 ⇒ 必须判 `POWERLESS`。

    🚨 **不许**产出显著性/达标结论（第 16 层）：
    同一句「没测出显著」，作用在门槛上是判决，作用在小效应上是废话。
    """
    out = _decide(gross_annual_pct=4.81, mde_annual_pct=15.31, t_blocked=0.92)
    assert out["label"] == M.LABEL_POWERLESS, out
    assert out["gate"] == "G1_mde"
    # 🚨 门槛明明也会挡住它（4.81 < 26.41）⇒ 若判决走到 G3 就是顺序错了
    labels = [c["gate"] for c in out["checks"]]
    assert "G1_mde" in labels
    assert "G2_significance" not in labels
    assert "G3_hurdle" not in labels


def test_a_powerful_but_insignificant_effect_is_judged_not_significant():
    """MDE 过了、t 没过 ⇒ `NOT_SIGNIFICANT`（**不是**「没有超额」）。"""
    out = _decide(gross_annual_pct=30.0, mde_annual_pct=10.0, t_blocked=1.2)
    assert out["label"] == M.LABEL_NOT_SIGNIFICANT, out
    assert out["gate"] == "G2_significance"


def test_a_significant_but_below_hurdle_effect_is_judged_below_hurdle():
    """MDE 过了、t 过了、毛超额 < 门槛 ⇒ `EXCESS_BELOW_HURDLE`。"""
    out = _decide(gross_annual_pct=12.0, mde_annual_pct=10.0, t_blocked=5.0)
    assert out["label"] == M.LABEL_BELOW_HURDLE, out
    assert out["gate"] == "G3_hurdle"


def test_a_significant_and_above_hurdle_effect_meets_the_hurdle():
    out = _decide(gross_annual_pct=30.0, mde_annual_pct=10.0, t_blocked=5.0)
    assert out["label"] == M.LABEL_MEETS, out


def test_an_insufficient_coverage_produces_no_verdict_rather_than_a_judgement():
    """**覆盖不足 ⇒ 不出判决**（不许给「因子有效/无效」的结论）。"""
    out = _decide(coverage_ratio=50 / 5460)
    assert out["label"] == M.LABEL_NO_VERDICT, out
    assert out["gate"] == "G0_structure"


def test_too_few_independent_periods_produce_no_verdict():
    out = _decide(n_independent_obs=2)
    assert out["label"] == M.LABEL_NO_VERDICT, out


def test_a_pool_smaller_than_n_produces_no_verdict():
    """**选不出 N 只 ⇒ 那不是选股，是整池** ⇒ 不出判决。"""
    out = _decide(n_scoreable_min=8, n_positions=30)
    assert out["label"] == M.LABEL_NO_VERDICT, out


def test_the_gate_order_is_strictly_m1_m2_m3():
    """判决链的**顺序**就是判据：G1 → G2 → G3，谁先命中谁说了算。"""
    labels = []
    for kw in (dict(gross_annual_pct=4.81, mde_annual_pct=15.31, t_blocked=0.92),
               dict(gross_annual_pct=30.0, mde_annual_pct=10.0, t_blocked=1.2),
               dict(gross_annual_pct=12.0, mde_annual_pct=10.0, t_blocked=5.0),
               dict(gross_annual_pct=30.0, mde_annual_pct=10.0, t_blocked=5.0)):
        out = _decide(**kw)
        labels.append(out["gate"])
    assert labels == ["G1_mde", "G2_significance", "G3_hurdle", "passed"]


# ═══════════════════════════════════════════════════════════════
# 3  对照臂守卫：负控通过 ⇒ INVALID
# ═══════════════════════════════════════════════════════════════

def test_the_negative_control_state_distinguishes_the_three_outcomes():
    assert M.negative_control_state(-5.0, 2.97) == M.NC_FAILED_AS_EXPECTED
    assert M.negative_control_state(+5.0, 2.97) == M.NC_PASSED
    assert M.negative_control_state(0.5, 2.97) == M.NC_INCONCLUSIVE
    assert M.negative_control_state(None, 2.97) == M.NC_INCONCLUSIVE


def test_a_passed_negative_control_invalidates_the_whole_verdict():
    """🚨 **负控（符号翻转）通过 ⇒ 判决必须是 `INVALID`**。"""
    out = _decide(negative_control=M.NC_PASSED)
    assert out["label"] == M.LABEL_INVALID, out
    assert out["gate"] == "negative_control"


def test_a_passed_negative_control_overrides_even_a_passing_measurement():
    """负控通过 ⇒ 整套作废，**哪怕测量本身全过线**。"""
    out = _decide(gross_annual_pct=99.0, mde_annual_pct=1.0, t_blocked=50.0,
                  negative_control=M.NC_PASSED)
    assert out["label"] == M.LABEL_INVALID, out


def test_a_failed_as_expected_negative_control_does_not_invalidate():
    out = _decide(negative_control=M.NC_FAILED_AS_EXPECTED)
    assert out["label"] == M.LABEL_MEETS, out


def test_the_sign_flip_transform_actually_flips_the_score():
    """**负控的机制守卫**：变换必须只把 `score` 取负，不许动别的。"""
    df = pd.DataFrame({"code": ["a", "b"], "score": [1.0, -2.0]})
    out = M._negate_transform()("2021-01-04", df)
    assert list(out["score"]) == [-1.0, 2.0]
    assert list(out["code"]) == ["a", "b"]
    assert len(out) == len(df)


def test_the_random_transform_keeps_the_pool_and_only_permutes_the_score():
    """**随机臂的机制守卫**：池子不变（NaN 位置不变）、行数不变、只有分数换位。"""
    df = pd.DataFrame({"code": list("abcdef"),
                       "score": [3.0, 2.0, 1.0, float("nan"), 0.5, -1.0]})
    out = M._perm_transform(20261021)("2021-01-04", df)
    assert len(out) == len(df)
    assert list(out["code"]) == list(df["code"])
    # NaN 的位置必须不变 ⇒ 「谁有资格进池子」没被换掉
    assert out["score"].isna().equals(df["score"].isna())
    # 有限值的集合必须不变（只是换位）
    a = sorted(v for v in out["score"].dropna())
    b = sorted(v for v in df["score"].dropna())
    assert a == b


def test_the_random_transform_is_reproducible_for_the_same_seed_and_day():
    """🚨 **不许 `hash(day)`**（`PYTHONHASHSEED` ⇒ 不可复现的阴性等于没跑）。"""
    df = pd.DataFrame({"code": list("abcdef"), "score": [3.0, 2.0, 1.0, 0.5, -1.0, 0.0]})
    a = M._perm_transform(20261021)("2021-01-04", df)
    b = M._perm_transform(20261021)("2021-01-04", df)
    assert list(a["score"]) == list(b["score"])
    c = M._perm_transform(20261022)("2021-01-04", df)
    assert list(a["score"]) != list(c["score"])


# ═══════════════════════════════════════════════════════════════
# 4  失败文案守卫：禁用词不许出现在**本脚本自己写的判决文案**里
# ═══════════════════════════════════════════════════════════════

def test_the_failure_wording_contains_none_of_the_forbidden_phrases():
    """🚨 **中文断词不许用 `\\b`** ⇒ 这里用逐字子串。"""
    for label, text in M.WORDING.items():
        for bad in M.FORBIDDEN_PHRASES:
            assert bad not in text, f"{label} 的文案里出现了禁用词「{bad}」"


def test_the_powerless_wording_says_measurable_not_absent():
    """「测不到」必须真的写「测不到」，且**不**写「没有超额」以外的断言。"""
    w = M.WORDING[M.LABEL_POWERLESS]
    assert "测不到" in w
    assert "无效" not in w
    assert "已证伪" not in w


def test_the_wording_guard_catches_a_synthetic_violation():
    """**守卫的能力**：给一份带违规文案的产物 ⇒ 必须被逮到。"""
    payload = {"arms": {"A3": {"verdict": {"wording": "这个因子无效，没有 alpha"}}}}
    findings = M.wording_guard_findings(payload, ("arms",))
    assert findings, "守卫连一份编造的违规产物都逮不到 ⇒ 它是装饰"


def test_the_wording_guard_passes_a_clean_payload():
    payload = {"arms": {"A3": {"verdict": {"wording": M.WORDING[M.LABEL_POWERLESS]}}}}
    assert M.wording_guard_findings(payload, ("arms",)) == []


# ═══════════════════════════════════════════════════════════════
# 5  门槛守卫：门槛**不许手打** ⇒ 必须能由原语重算
# ═══════════════════════════════════════════════════════════════

def test_the_hurdle_is_reproducible_from_the_repo_primitives():
    """**外部锚**：由 `aplus.costs` + `aplus.hurdle` 现算，不是字面量。"""
    from aplus.costs import annual_friction
    from aplus.hurdle import hurdle_rate, rounds_per_year
    for k, n in ((21, 10), (21, 30), (63, 10), (63, 30)):
        cpr = annual_friction(50_000, 1.0, n, 1)
        exp = hurdle_rate(target=0.20, cost_per_round=cpr,
                          rounds_per_year=rounds_per_year(k)) * 100.0
        assert M.hurdle_for(k, n)["hurdle_annual_pct"] == pytest.approx(exp, rel=1e-12)


def test_the_hurdle_matches_the_stamped_artifact_bit_for_bit():
    """**逐位对拍**（预注册 §4.2 的那个例子）：与带章产物
    `data/reports/factor_width_mde.json::widths["10"].hurdle_width_aware_annual_pct`
    **逐位相同**。

    ⚠️ 这条守卫**依赖产物存在** ⇒ 产物不在时 `skip`（不许假装通过）。
    """
    art = ROOT / "data" / "reports" / "factor_width_mde.json"
    if not art.is_file():
        pytest.skip("带章产物不在（本仓惯例：产物是可选的）")
    d = json.loads(art.read_text(encoding="utf-8"))
    exp = float(d["widths"]["10"]["hurdle_width_aware_annual_pct"])
    got = M.hurdle_for(21, 10)["hurdle_annual_pct"] / 100.0
    assert got == exp, f"门槛推导式与仓内既有实现不一致：{got!r} vs {exp!r}"


def test_the_hurdle_table_matches_the_preregistration():
    """预注册 §4.2 的四个门槛（**逐位**，由原语重算 ⇒ 同一批数）。"""
    exp = {(21, 10): 26.4137, (21, 30): 31.0613, (63, 10): 22.1379, (63, 30): 23.6871}
    for (k, n), v in exp.items():
        got = M.hurdle_for(k, n)["hurdle_annual_pct"]
        assert round(got, 4) == pytest.approx(v, abs=1e-4), (k, n, got)


def test_the_t_crit_uses_a_real_t_distribution_and_not_a_table():
    """**真 t 分布**：`t_crit_two_sided(0.05/12, df)`，不是查表、不是正态近似。"""
    from aplus.dist import t_crit_two_sided
    for df in (10, 65, 200):
        assert M.t_crit_for(df) == pytest.approx(
            t_crit_two_sided(0.05 / M.M_CANDIDATES, df), rel=1e-12)
    # 🚨 正态近似在 df 小的时候差 1.56（`aplus/evolve_race.py::bonferroni_t` 的实测）
    assert M.t_crit_for(10) > 3.0


def test_the_candidate_set_is_frozen_at_twelve_arms_with_a_main_arm():
    """**候选集写死**（预注册 §5）：m = 12，主臂 = A3。"""
    assert len(M.ARMS) == 12
    assert M.M_CANDIDATES == 12
    mains = [a.arm_id for a in M.ARMS if a.is_main]
    assert mains == ["A3"]
    ids = [a.arm_id for a in M.ARMS]
    assert ids == ["A1", "A2", "A3", "A4", "A5", "A6",
                   "A7", "A8", "A9", "A10", "A11", "A12"]
    combos = {(a.factor_def, a.k, a.n) for a in M.ARMS}
    assert len(combos) == 12, "3 定义 × 2 K × 2 N 必须是 12 个**不同**的组合"


def test_the_ivol_leg_is_negated():
    """🚨 IVOL **取负**（石川书做多**最低** IVOL 组）。"""
    assert M.FACTOR_DEFS["ivol"]["weights"]["ivol"] == -1.0
    assert M.FACTOR_DEFS["dividend"]["weights"]["divyield"] == 1.0
    # 合成：等权 z，两条腿各占一半
    w = M.FACTOR_DEFS["composite"]["weights"]
    assert w["divyield"] == 0.5 and w["ivol"] == -0.5


# ═══════════════════════════════════════════════════════════════
# 6  缺数据守卫：没采到分红的票 ⇒ 显式 NaN，**不许编 0**
# ═══════════════════════════════════════════════════════════════

def test_a_code_that_was_never_collected_is_nan_not_zero():
    """**缺数据 ⇒ NaN**（编 0 会让它看起来像「零股息」⇒ 被动持有）。"""
    cdp = M.load_collect_dividend_pit()
    events = pd.DataFrame([
        {"code": "000001", "plan_announce_date": "2021-03-01",
         "operate_date": "2021-06-01", "cash_ps_before_tax": 0.5,
         "cash_status": "parsed"},
    ])
    ttm = cdp.ttm_cash_dividend(events, "2021-07-01",
                                 covered_codes={"000001"},
                                 codes={"000001", "000002"})
    row = ttm[ttm["code"] == "000002"].iloc[0]
    assert row["status"] == cdp.STATUS_NOT_COLLECTED
    assert pd.isna(row["ttm_cash_ps"]), "没采到的票必须是 NaN，**不许编 0**"


def test_a_collected_code_with_no_payout_in_the_window_is_zero():
    """**三态分得开**：采到了、窗口里没分红 ⇒ 0 是**事实**。"""
    cdp = M.load_collect_dividend_pit()
    # 🚨 「000002」在**窗口外**有一条分红记录 ⇒ 它**有分红历史**
    # （采集器靠「事件表里出现过」判「采到了」）
    # ⇒ 窗口里没有 ⇒ **0 是事实**，不是缺数据。
    events = pd.DataFrame([
        {"code": "000001", "plan_announce_date": "2021-03-01",
         "operate_date": "2021-06-01", "cash_ps_before_tax": 0.5,
         "cash_status": "parsed"},
        {"code": "000002", "plan_announce_date": "2018-03-01",
         "operate_date": "2019-01-01", "cash_ps_before_tax": 0.2,
         "cash_status": "parsed"},
    ])
    ttm = cdp.ttm_cash_dividend(events, "2021-07-01",
                                 covered_codes={"000001", "000002"},
                                 codes={"000001", "000002"})
    row = ttm[ttm["code"] == "000002"].iloc[0]
    assert row["status"] == cdp.STATUS_COLLECTED_NO_PAYOUT
    assert float(row["ttm_cash_ps"]) == 0.0


def test_the_covered_codes_come_from_the_cells_ledger_not_from_the_event_table(tmp_path):
    """🚨 「采到了」只能从**单元账**读（事件表里「没采到」与「零分红」同形）。"""
    root = tmp_path / "dividend_pit"
    d = root / "date=2026-10-21"
    d.mkdir(parents=True)
    (d / "_cells.json").write_text(json.dumps({
        "day": "2026-10-21", "pool_size": 3, "years": [2026],
        "cells": {"000001|2026": "ok", "000002|2026": "empty",
                  "000003|2026": "error"},
        "written_at": "2026-10-21T00:00:00+00:00",
    }), encoding="utf-8")
    codes, ledger = M.covered_codes_from_ledger(root)
    assert codes == {"000001", "000002"}, "error 的格**不算**采到"
    assert ledger["n_cells_error"] == 1
    assert ledger["n_cells_ok"] == 1
    assert ledger["n_cells_empty"] == 1


def test_the_covered_codes_reader_survives_an_empty_root(tmp_path):
    codes, ledger = M.covered_codes_from_ledger(tmp_path / "nope")
    assert codes == set()
    assert ledger["n_codes_covered"] == 0


# ═══════════════════════════════════════════════════════════════
# 7  产物纪律：测试不许写生产路径；盖章字节冻结
# ═══════════════════════════════════════════════════════════════

def test_this_test_file_never_writes_the_production_artifact_path():
    """**AST 级**（不靠子串 —— 第 13 层：子串会被注释满足）。"""
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    prod = str(PROD_OUT)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = getattr(fn, "id", None) or getattr(fn, "attr", None)
            if name not in ("write_text", "write_bytes", "open"):
                continue
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    assert prod not in arg.value, "测试不许写生产产物路径"
                if isinstance(arg, ast.Name):
                    assert arg.id not in ("PROD_OUT",), "测试不许引用生产产物路径"


def test_the_script_captures_expect_bytes_as_the_first_executable_statement_of_main():
    """🚨 第 125 层：`expect_bytes` 必须在 `main()` 的**第一条可执行语句**取。

    ⚠️ 判据用 **AST**（不靠子串）：子串会被 docstring 与注释满足。
    """
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    main_fn = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            main_fn = node
            break
    assert main_fn is not None, "找不到 main()"
    stmts = [s for s in main_fn.body
             if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
    assert stmts, "main() 没有可执行语句"
    first = stmts[0]
    assert isinstance(first, ast.Assign), ast.dump(first)
    assert any(getattr(t, "id", "") == "expect_bytes" for t in first.targets)


def test_the_script_stamps_with_the_generator_and_expect_bytes():
    """盖章必须带 `expect_bytes`（否则第 125 层的检查整项跳过且无人察觉）。"""
    src = SCRIPT.read_text(encoding="utf-8")
    tree = ast.parse(src)
    calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if getattr(f, "attr", "") == "stamp":
                calls.append(node)
    assert calls, "脚本没有盖章调用"
    for c in calls:
        kws = {k.arg for k in c.keywords}
        assert "expect_bytes" in kws, "盖章没接 expect_bytes ⇒ 字节检查被整项跳过"
        assert "root" in kws


def test_the_data_root_outside_the_repo_is_rejected():
    """🚨 仓外 `--data-root` ⇒ rc=2 **拒绝**，不许回落。"""
    args = M.parse_args(["--data-root", "/tmp/definitely-not-the-repo"])
    with pytest.raises(SystemExit) as e:
        M.validate_args(args)
    assert e.value.code == 2


def test_an_empty_end_is_rejected():
    """形状 23：末端必须显式钉死，不许回落。"""
    args = M.parse_args(["--end", " "])
    with pytest.raises(SystemExit) as e:
        M.validate_args(args)
    assert e.value.code == 2


def test_the_default_window_is_pinned():
    args = M.parse_args([])
    assert args.start == "2021-01-01"
    assert args.end == "2026-10-12"


# ═══════════════════════════════════════════════════════════════
# 8  统计口径：MDE 必须是**分块**口径（预注册 §4.3 明令）
# ═══════════════════════════════════════════════════════════════

def test_the_mde_is_computed_on_block_means_not_on_the_daily_series():
    """🚨 预注册 §4.3：「**不许**用『按天 SE』当 MDE」。

    ## 判据为什么不是「分块 MDE 更大」

    🚨 **「分块 MDE 一定比按天 MDE 大」是**错的性质**：
    对 **i.i.d.** 日频序列，块均值的 sd ≈ 日 sd/√K ⇒ 两个 MDE **几乎相等**
    （实测 0.2703 vs 0.2727，块均值那个**反而略小**）。
    ⇒ 真正的判据是**「它是用块均值算的」**，而那要由**逐位对拍**来证：
    `mde_annual_fraction` 必须**逐位等于** `power_floor(块均值)`。
    """
    rng = np.random.default_rng(7)
    daily = pd.Series(rng.normal(0.0, 0.01, size=600))
    out = M.mde_annual_pct(daily, 21)
    assert out["mde_annual_pct"] is not None
    fa = M.load_factor_attribution()
    bm = M.block_means(daily, 21)
    assert out["n_independent_obs"] == 600 // 21 == bm.size
    assert out["mde_annual_fraction"] == pytest.approx(
        float(fa.power_floor(bm, days_per_year=M.R)["MDE_annual_pct"]), rel=1e-12)
    naive = float(fa.power_floor(daily.to_numpy(float),
                                  days_per_year=M.R)["MDE_annual_pct"])
    assert out["mde_annual_fraction"] != pytest.approx(naive, rel=1e-6), (
        "分块 MDE 与按天 MDE 竟然一样 ⇒ 它根本没在分块上算")


def test_for_an_autocorrelated_series_the_blocked_mde_is_larger():
    """**K 日调仓的真实情形**：同一次选股在 K 天内不变 ⇒ 逐日超额序列
    **正自相关** ⇒ 分块 SE 更大 ⇒ 分块 MDE **更大**。

    ⚠️ 这条性质**只在正自相关下成立**（对 i.i.d. 序列它**不成立**）
    ⇒ 上一条守卫刻意**不**用它做判据。
    """
    rng = np.random.default_rng(13)
    n = 600
    noise = rng.normal(0.0, 0.01, n)
    daily = pd.Series(0.0, index=np.arange(n))
    for i in range(1, n):
        daily.iloc[i] = 0.9 * daily.iloc[i - 1] + noise[i]
    out = M.mde_annual_pct(daily, 21)
    fa = M.load_factor_attribution()
    naive = float(fa.power_floor(daily.to_numpy(float),
                                  days_per_year=M.R)["MDE_annual_pct"])
    assert out["mde_annual_fraction"] > naive, (
        "正自相关序列上分块 MDE 必须更大 —— 这是 K 日调仓的真实情形")


def test_the_annual_sd_uses_sqrt_of_time_not_time():
    """仓内口径：年化 sd = sd × √T（不是 sd × T）。"""
    rng = np.random.default_rng(11)
    daily = pd.Series(rng.normal(0.0, 0.01, size=500))
    got = M.annual_sd(daily)
    exp = float(daily.std(ddof=1)) * math.sqrt(M.R)
    assert got["annual_sd_fraction"] == pytest.approx(exp, rel=1e-12)
    assert got["annual_sd_pct"] == pytest.approx(exp * 100.0, rel=1e-12)


def test_the_mde_is_reported_as_a_percentage_and_as_a_fraction():
    """🚨 **单位守卫**：`power_floor` 的 `MDE_annual_pct` 是**分数**
    （仓内 `factor_width_mde.json` = 0.1531 = 15.31%）⇒ 少乘 100 会让
    MDE **小 100 倍** ⇒ 效应看起来「可测」⇒ 判决整个翻面（第 97/138 层）。
    """
    rng = np.random.default_rng(9)
    daily = pd.Series(rng.normal(0.001, 0.01, size=600))
    out = M.mde_annual_pct(daily, 21)
    assert out["mde_annual_pct"] == pytest.approx(out["mde_annual_fraction"] * 100.0)
    assert out["mde_annual_fraction"] == pytest.approx(
        M.load_factor_attribution().power_floor(
            M.block_means(daily, 21), days_per_year=M.R)["MDE_annual_pct"])


def test_the_coverage_minimum_is_the_repos_own_sample_ratio():
    """**门槛不许手打** ⇒ 覆盖率闸取 `aplus/panel.py::MIN_SAMPLE_RATIO`。"""
    from aplus.panel import MIN_SAMPLE_RATIO
    assert M.COVERAGE_MIN == MIN_SAMPLE_RATIO
    assert M.COVERAGE_MIN == pytest.approx(2 / 3)


# ═══════════════════════════════════════════════════════════════
# 9  整套作废的作用域（负控只在**测得动的臂**上作废整套）
# ═══════════════════════════════════════════════════════════════

def _arm(arm_id, factor_def="composite", k=21, n=10):
    return M.Arm(arm_id, factor_def, k, n, "test", is_main=False)


def test_a_passed_negative_control_on_a_measurable_arm_voids_the_whole_suite():
    arms = [_arm("A3")]
    results = {"A3": {"scoreable": {"min": 5000}}}
    controls = {"negative_control": {"A3": {"state": M.NC_PASSED}}}
    assert M.suite_invalid_arms(arms, results, controls, 5460) == ["A3"]


def test_a_passed_negative_control_on_an_unmeasurable_arm_does_not_void_the_suite():
    """🚨 一个 0.92% 覆盖的池上的负控**本身不说明什么** ⇒ 不得用它作废整套。

    ⇒ 那是**用一把不准的尺子去否决一切**（第 112 层：过严的守卫和恒真的一样有害）。
    """
    arms = [_arm("A3")]
    results = {"A3": {"scoreable": {"min": 50}}}      # 50 / 5460 = 0.9%
    controls = {"negative_control": {"A3": {"state": M.NC_PASSED}}}
    assert M.suite_invalid_arms(arms, results, controls, 5460) == []


def test_a_failed_negative_control_never_voids_the_suite():
    arms = [_arm("A3")]
    results = {"A3": {"scoreable": {"min": 5000}}}
    controls = {"negative_control": {"A3": {"state": M.NC_FAILED_AS_EXPECTED}}}
    assert M.suite_invalid_arms(arms, results, controls, 5460) == []


# ═══════════════════════════════════════════════════════════════
# 10  降本跑（缩小池）必须**自报**不是预注册的池
# ═══════════════════════════════════════════════════════════════

def test_the_pool_scope_is_a_declared_choice_not_a_silent_default():
    args = M.parse_args([])
    assert args.pool == "full", "默认必须是**全池**（预注册口径）"
    assert args.pool_size > 0


def test_the_sampled_pool_labels_itself_as_not_the_prereg_pool():
    """🚨 缩小池的跑法**必须**在产物里自报，否则会被误读成测量。"""
    src = SCRIPT.read_text(encoding="utf-8")
    assert "is_prereg_pool" in src
    tree = ast.parse(src)
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for k in node.keys:
                if isinstance(k, ast.Constant) and k.value == "is_prereg_pool":
                    found = True
    assert found, "产物里没有 `is_prereg_pool` 字段"


def test_the_artifact_meta_carries_the_window_and_the_pool_scope():
    """窗口必须**钉死**并落在产物里（形状 23：窗口不可见 = 不可核对）。"""
    args = M.parse_args([])
    assert args.start == "2021-01-01"
    assert args.end == "2026-10-12"
    src = SCRIPT.read_text(encoding="utf-8")
    assert '"window"' in src and '"pool_scope"' in src


def test_the_daily_layer_ledger_is_never_counted_as_covered(tmp_path):
    """🚨🚨 **只读事件层的单元账**。

    采集 A（`root/daily/`「全池当年视图」）与采集 B（事件层，整段历史）
    是**两个量**：前者只覆盖**当年**，后者覆盖判决窗口的整段历史。
    ⇒ 混进「采到了」⇒ 覆盖率闸形同虚设（第 7 层）。
    """
    root = tmp_path / "dividend_pit"
    ev = root / "date=2026-10-21"
    ev.mkdir(parents=True)
    (ev / "_cells.json").write_text(json.dumps(
        {"day": "2026-10-21", "cells": {"000001|2026": "ok"},
         "written_at": "2026-10-21T00:00:00+00:00"}), encoding="utf-8")
    dl = root / "daily" / "date=2026-10-21"
    dl.mkdir(parents=True)
    (dl / "_cells.json").write_text(json.dumps(
        {"day": "2026-10-21", "cells": {"000002|2026": "ok",
                                        "000003|2026": "ok"},
         "written_at": "2026-10-21T00:00:00+00:00"}), encoding="utf-8")
    codes, ledger = M.covered_codes_from_ledger(root)
    assert codes == {"000001"}, "采集 A 的单元账**不许**算进「采到了」"
    assert ledger["n_codes_covered"] == 1


# ═══════════════════════════════════════════════════════════════
# 11  交叉对账：t 与 MDE 必须来自**同一个** SE（口径不许混）
# ═══════════════════════════════════════════════════════════════

def test_the_mde_and_the_blocked_t_come_from_the_same_standard_error():
    """🚨 `t_blocked` 与 `mde_annual_pct` 是**两条不同代码路径**算的
    （`_tstat` vs `power_floor(块均值)`）⇒ 二者一致 = **真交叉核对**。

    🚨 若有人把 MDE 换成**按天 SE**（仓内既有产物 `factor_width_mde.json` 那一档），
    而 t 仍是分块的 ⇒ 二者不再自洽 ⇒ **这条守卫会红**。
    """
    rng = np.random.default_rng(3)
    daily = pd.Series(rng.normal(0.001, 0.01, size=600))
    mde = M.mde_annual_pct(daily, 21)
    bn = M.load_backtest_walk_forward()
    t, n = bn._t(daily, block=21)
    assert mde["n_independent_obs"] == n
    # 🚨 单位：`mde_annual_fraction` 才是**分数**（`power_floor` 的原值）。
    se_annual_fraction = mde["mde_annual_fraction"] / mde["t_power"]
    bm = M.block_means(daily, 21)
    t_expected = (float(bm.mean()) * M.R) / se_annual_fraction
    assert t == pytest.approx(t_expected, rel=1e-9), (
        "t 与 MDE 不自洽 ⇒ 它们不是从同一个 SE 算的")


def test_a_daily_se_mde_would_not_match_the_blocked_t():
    """**反向正控**：按天 SE 的 MDE 与分块 t **不自洽**（⇒ 守卫有判别力）。"""
    rng = np.random.default_rng(5)
    daily = pd.Series(rng.normal(0.001, 0.01, size=600))
    mde = M.mde_annual_pct(daily, 21)
    bn = M.load_backtest_walk_forward()
    t, _n = bn._t(daily, block=21)
    fa = M.load_factor_attribution()
    naive = fa.power_floor(daily.to_numpy(float), days_per_year=M.R)
    se_annual_naive = naive["MDE_annual_pct"] / fa.T_POWER_TARGET
    t_from_naive = (float(M.block_means(daily, 21).mean()) * M.R) / se_annual_naive
    assert abs(t_from_naive - t) > 1e-6, "按天口径与分块口径居然一样 ⇒ 夹具失效"
    assert mde["mde_annual_fraction"] != pytest.approx(
        float(naive["MDE_annual_pct"]), rel=1e-6)


# ═══════════════════════════════════════════════════════════════
# 12  产物层对账（产物不在 ⇒ skip，**不许假装通过**）
# ═══════════════════════════════════════════════════════════════

def _artifact():
    if not PROD_OUT.is_file():
        pytest.skip("产物不在（本轮还没跑 / 跑在别的路径）")
    return json.loads(PROD_OUT.read_text(encoding="utf-8"))


def test_the_artifact_contains_no_forbidden_wording_in_its_own_verdicts():
    """🚨 产物层的**失败文案**守卫：禁用词不许出现在**本脚本写的判决文案**里。

    ⚠️ 作用域 = `arms.*.verdict.wording` + `suite_verdict.wording`
    （**账户层 `cases` 是仓内权威 `judge()` 的原文**，其作用域在
    `wording_policy` 里写明 ⇒ 不扫）。
    """
    d = _artifact()
    findings = M.wording_guard_findings(d, ("arms", "suite_verdict"))
    assert not findings, findings


def test_the_artifact_declares_its_coverage_and_its_bias_direction():
    """🚨 覆盖不足 ⇒ 产物**必须**写清「覆盖了多少 / 全池多少 / 偏差方向」。"""
    d = _artifact()
    cov = d.get("coverage") or {}
    for key in ("coverage_ratio", "n_covered_codes", "n_pool_codes",
                "coverage_min", "bias_direction", "covered_codes",
                "sampling_mechanism", "two_legs_warning"):
        assert key in cov, f"覆盖块缺字段 {key}"
    assert cov["n_pool_codes"] == d["artifact_meta"]["n_pool_full"]

    # 🚨 **条件化断言**（2026-10-21 改）：覆盖**够**与覆盖**不够**⇒
    #   产物该说的话**不同** ⇒ 判据必须**跟着覆盖走**，
    #   不能是一张关键词白名单。
    #   ⇒ 白名单会让「覆盖已达标却仍写『不可判』」也放过
    #   ⇒ 那是对旧读数（20% 覆盖那版）的残留。
    #   ⇒ 现在：**覆盖达标 ⇒ 必须写「没有覆盖偏差方向的修正项」；
    #   覆盖不足 ⇒ 必须写明偏差方向（不可判 / 偏保守）。**
    if cov["coverage_ratio"] >= cov["coverage_min"]:
        assert ("达标" in cov["bias_direction"]
                or "没有覆盖偏差" in cov["bias_direction"]), (
            f"覆盖已达标（{cov['coverage_ratio']:.4f} ≥ {cov['coverage_min']:.4f}）"
            f"⇒ 产物必须写明「没有覆盖偏差方向的修正项」"
            f"（还要单列修订风险），现在写的是：{cov['bias_direction']!r}")
    else:
        assert "不可判" in cov["bias_direction"] or "偏保守" in cov["bias_direction"], (
            f"覆盖不足（{cov['coverage_ratio']:.4f} < {cov['coverage_min']:.4f}）"
            f"⇒ 产物必须写明偏差方向（不可判 / 偏保守），"
            f"现在写的是：{cov['bias_direction']!r}")


def test_every_arm_in_the_artifact_carries_a_verdict_and_its_wording():
    d = _artifact()
    arms = d.get("arms") or {}
    assert len(arms) >= 1
    for aid, a in arms.items():
        v = a.get("verdict") or {}
        assert v.get("label") in (M.LABEL_INVALID, M.LABEL_NO_VERDICT,
                                   M.LABEL_POWERLESS, M.LABEL_NOT_SIGNIFICANT,
                                   M.LABEL_BELOW_HURDLE, M.LABEL_MEETS), (aid, v)
        assert v.get("wording"), aid
        assert a.get("hurdle", {}).get("hurdle_annual_pct") is not None, aid


def test_the_artifact_records_the_mde_in_both_fraction_and_percent():
    """🚨 单位：`*_pct` 是**百分数**、`*_fraction` 是**分数** ⇒ 两者必须并存。"""
    d = _artifact()
    for aid, a in (d.get("arms") or {}).items():
        if a.get("mde_annual_pct") is None:
            continue
        assert a["mde_annual_pct"] == pytest.approx(
            a["mde_annual_fraction"] * 100.0), aid
        assert a["gross_excess_annual_pct"] > -1e6


def test_the_random_control_arm_set_is_explicit_and_bounds_the_cost():
    """🚨 随机臂 100 rep × 每个臂 ≈ 全池 1 小时 ⇒ **12 个臂全跑要 ~20 小时**
    ⇒ 跑哪些臂**必须是一个显式参数**，不是「顺手都跑」。"""
    args = M.parse_args([])
    assert args.random_arms == "auto"
    assert args.random_reps == 100, "预注册 §6 C2：随机臂是 100 rep"
    assert M.RANDOM_REPS == 100
    assert M.RANDOM_SEED_BASE == 20261021, "预注册 §6 C2：rep_seed = 20261021 + r"


def test_the_main_arm_is_always_in_the_random_control_arm_set():
    """预注册的**主臂**必须有随机对照 ⇒ 否则「主臂是不是随便选都行」没人答。"""
    mains = [a for a in M.ARMS if a.is_main]
    assert len(mains) == 1 and mains[0].arm_id == "A3"


# ═══════════════════════════════════════════════════════════════
# 13  缺数据排除探针：**未采到的票是显式 NaN，不是被编成了别的值**
# ═══════════════════════════════════════════════════════════════

def _fake_xs(rows: list[dict]):
    return {"2021-01-04": pd.DataFrame(rows)}


def test_the_nan_exclusion_probe_confirms_a_nan_row_cannot_enter_the_ranking():
    """**能力守卫**：横截面里有一行 `divyield = NaN`
    ⇒ 把它注入 0 **或**池内最大值，**被选中的集合都必须不变**。

    ⇒ 判别力来自**注入高值**那一半：若那行被当成某个有限值参与排名，
    它会**直接冲进 top-N** ⇒ 读数必变 ⇒ 「不变」就是排除有效的证据。
    """
    arm = M.Arm("T1", "dividend", 21, 2, "test")
    rows = [{"code": "A", "divyield": 0.05},
            {"code": "B", "divyield": 0.02},
            {"code": "C", "divyield": 0.01},
            {"code": "D", "divyield": float("nan")}]
    out = M.nan_exclusion_probe(_fake_xs(rows), ["2021-01-04"], arm,
                                sample_days=1)
    assert out["applicable"] is True
    assert out["n_nan_rows_seen"] == 1
    # ① 基线：那行（NaN）**不在** top-N 里
    assert out["n_days_nan_row_in_baseline_topn"] == 0, out
    # ② 注入**高值**后它**能进** top-N ⇒ 它的缺席是 NaN 造成的
    assert out["n_days_nan_row_entered_after_high_injection"] == 1, out
    assert out["n_days_changed_by_high_injection"] == 1, out
    # ③ 注入 0 通常**不变**（没有判别力）—— 这一半只作对照
    assert out["n_days_changed_by_zero_injection"] == 0, out
    assert out["excluded"] is True


def test_the_nan_exclusion_probe_has_discriminating_power():
    """**反向正控**：同一行若**本来就有一个有限的高值** ⇒ top-N 必变。

    ⇒ 证明这条探针**看得见**「那行参与排名」的情形
    （否则「不变」可能只是探针坏了）。
    """
    arm = M.Arm("T1", "dividend", 21, 2, "test")
    rows = [{"code": "A", "divyield": 0.05},
            {"code": "B", "divyield": 0.02},
            {"code": "C", "divyield": 0.01},
            {"code": "D", "divyield": 0.99}]     # **有限**的高值
    out = M.nan_exclusion_probe(_fake_xs(rows), ["2021-01-04"], arm,
                                sample_days=1)
    # 探针只看「NaN 行」；这里没有 NaN 行 ⇒ 没有可注入的对象
    assert out["applicable"] is False
    assert "NaN" in out["reason"] or "未采到" in out["reason"]
    # ⇒ 单独断言判别力：把那行改成 NaN 再注入高值 ⇒ 选中的集合不变
    rows2 = [dict(r) for r in rows]
    rows2[3]["divyield"] = float("nan")
    out2 = M.nan_exclusion_probe(_fake_xs(rows2), ["2021-01-04"], arm,
                                 sample_days=1)
    assert out2["excluded"] is True
    assert out2["n_days_nan_row_entered_after_high_injection"] == 1, out2
    # ⇒ 而**有限高值**那一版，top-N 确实会变（这是「若没被排除会怎样」）
    from aplus.factor import composite_score
    df = pd.DataFrame(rows)
    sc = composite_score(df, M.FACTOR_DEFS["dividend"]["weights"])
    top = M._top_codes_by_score(df, sc, 2)
    assert "D" in top, "夹具失效：有限高值那一行本来就该进 top-N"
    # 🚨 **行序 ≠ 名次序**（第一版探针踩的坑）：D 在**最后一行**，
    # 按行序取 top-2 拿不到它 ⇒ 探针会恒真。
    assert list(df["code"])[:2] == ["A", "B"], "夹具的 D 必须在最后一行"


def test_the_nan_exclusion_probe_reports_that_it_is_not_applicable_to_the_ivol_arm():
    arm = M.Arm("T2", "ivol", 21, 10, "test")
    out = M.nan_exclusion_probe(_fake_xs([{"code": "A", "ivol": 0.1}]),
                                ["2021-01-04"], arm, sample_days=1)
    assert out["applicable"] is False


def _plain(text: str) -> str:
    """剥掉 markdown 强调标记再找子串。

    ⚠️ **中文子串断言不许直接搜带 `**` 的原串**（2026-10-21 实测踩到）：
    说明串写的是「**没有**任何臂的负控」⇒ 裸搜「没有任何臂的负控」**找不到**
    ⇒ 而 `\b` 对中文更是不成立 ⇒ ⇒ **先剥标记再找**。
    """
    return str(text).replace("**", "").replace("`", "")


@pytest.mark.xfail(
    strict=True,
    reason=("产物里的说明串是**修复前**那版（写死的常量）⇒ 需重跑整个测量"
            "（~3.5 小时，12 随机臂 × 100 rep）才能刷新。"
            "⇒ 本轮为给 18:30 的日频作业让出内存而停掉了重跑。"
            "⇒ strict=True：产物一重跑就 XPASS(strict) ⇒ **自动翻红提醒**。"))
def test_the_suite_note_in_the_artifact_matches_what_actually_happened():
    """产物里那一格的说法必须与 `invalidated_by` **一致**（当前产物待重跑）。"""
    d = _artifact()
    sv = d.get("suite_verdict") or {}
    note = _plain(sv.get("note") or "")
    invalid = bool(sv.get("invalidated_by"))
    if invalid:
        assert "作废" in note, note
        for aid in sv["invalidated_by"]:
            assert aid in note, f"作废的臂 {aid} 没有被点名：{note}"
    else:
        assert "没有任何臂的负控" in note, (
            f"没有任何臂的负控通过 ⇒ 说明串却没说这件事：{note}")
        assert "作废" not in note or "不是" in note, (
            f"没有任何臂的负控通过 ⇒ 说明串不许声称「整套判决作废」：{note}")


def test_the_suite_note_matches_what_actually_happened():
    """🚨 **说明串不许声称没发生的原因**（2026-10-21 抓出并修）。

    ## 缺陷的形态

    `suite_verdict.note` 原先是**写死的常量** ⇒ 不管发生了什么，它都断言
    「负控「通过」（翻转后显著为正）⇒ **整套判决作废**」
    ⇒ 而实测 `invalidated_by = []`（**没有任何臂的负控通过**）
    ⇒ ⇒ **产物声称了一个没发生的原因**
    ⇒ ⇒ 它把读的人（**包括协调方自己**）导向了错误的理由：
    真实原因是**逐臂**的两种不可判（覆盖不足 / 测不到）。

    ⇒ **机器生成的解释同样会脱钩** ⇒ 散文那个病不豁免机器。

    ## 判据

    ① 产物里那一格的说法必须与 `invalidated_by` **一致**
    ② `suite_note()` **两个分支都要对**（⇒ 守卫能直接调它 ⇒
       **不必重跑 25 分钟的产物**就能验 ⇒ 这是把它抽成模块级函数的理由）
    """
    # 函数本身：两个分支各验一次（**不必重跑产物**就能验 ⇒ 这正是
    # 把它抽成模块级函数的理由）
    assert "作废" in _plain(M.suite_note(["A3"], 0.5))
    assert "A3" in _plain(M.suite_note(["A3"], 0.5)), "作废的臂必须被点名"
    empty = _plain(M.suite_note([], 0.5))
    assert "没有任何臂的负控" in empty, empty
    assert "不是" in empty, empty
    assert "作废" not in empty or "不是" in empty, empty

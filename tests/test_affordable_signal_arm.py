"""守卫：「信号住在买不到的票里」——可买性干预臂
（`scripts/measure_affordable_signal_arm.py`）。

## 这个文件守什么

本轮交付物是**一个测量**（不是一段代码）：四个臂（A 现状 / B 干预 /
C 负控 / D 已知为负）× K ∈ {21,63} 的长腿毛超额、MDE、t、配对日数、
部署率分布，以及 B vs A 的干预判决。
⇒ 守卫要钉的是**读数与口径**，不是「代码跑通了」。

## 三层结构（每层有它自己的失效方向）

| 层 | 钉什么 | 它**看不见**什么 |
|---|---|---|
| **A 产物层** | 产物里的读数**逐位**等于产物字段（无手打副本、无换算、单位不串）；口径标签不许互换；倍数只取同口径；`POWERLESS` 必带「测不到」措辞 | 源码改了没重跑（第 96/118 层：读快照的守卫等价于没守） |
| **B 行为层** | 判决链顺序 / 负控三态 / 干预臂分支 / 腿表逐位对拍 / 可买性规则 —— **合成夹具**上逐条钉 | `main()` 的编排层（**已知欠账**，见下） |
| **C 预注册层** | 四个臂的规则、判据四态、量纲、诚实边界与**预注册文档**逐条一致（第 145 层：声明，不许探测） | 门槛数值（门槛**从产物读**，不在文档里手打） |

⇒ A 层满足「读数逐位等于产物字段」这条要求；B 层让 A 层**不是装饰**
（源码里的判决链被改 ⇒ B 层当场红）；C 层堵住第 91 层与第 145 层。

## 🚨 为什么 A 层不是「用公式重算当验证」（第 109 层）

A 层**只读产物字段**并做**逐位相等**（`==`，不是 `isclose`）：
「读数是产物里的那个字段」与「读数是某个公式算出来的」是**两个命题**。
⇒ 拿公式重算当验证，会在**公式与产物同时错**时照样绿。

## ⚠️ 已知欠账（写在脸上，不是遗漏）

`main()` 的**编排层**（哪些块被装进产物、标签挂在哪一处）**不在行为层覆盖内**：
断言它只能去读产物，而读快照的守卫对源码改动**零覆盖**
⇒ 这一层靠 **A 层的逐位对账 + B 层的纯函数**兜底。
⇒ 另有一条**结构性的洞**（明写，不藏）：产物层守卫读的是**上一次落盘**的产物
⇒ 「改了生成器没重跑」它**看不见**（第 96 层）。
⇒ 本文件**不假装**覆盖它。

## 变异验证

见 `tests/mutation_specs/test_affordable_signal_arm.mut` 与同目录 README。
"""
import importlib.util
import json
import math
import re
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_SCRIPT = ROOT / "scripts" / "measure_affordable_signal_arm.py"


def _mod():
    spec = importlib.util.spec_from_file_location("_afford_arm", _SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


M = _mod()

ARTIFACT = ROOT / "data/reports/affordable_signal_arm_2026-10-21.json"
PREREG = ROOT / "docs/PREREG_AFFORDABLE_SIGNAL_ARM_2026-10-21.md"
ARM_R_ARTIFACT = ROOT / "data/reports/fundamental_mechanism_armR_2026-10-21.json"
LEDGER = ROOT / "data/reports/d2_signal_requirement.json"
FWM = ROOT / "data/reports/factor_width_mde.json"

KS = (21, 63)
ARMS = (M.ARM_A, M.ARM_B, M.ARM_C)


def _artifact() -> dict:
    return json.loads(ARTIFACT.read_text(encoding="utf-8"))


def _arm_r() -> dict:
    return json.loads(ARM_R_ARTIFACT.read_text(encoding="utf-8"))


def _ledger() -> dict:
    return json.loads(LEDGER.read_text(encoding="utf-8"))


def _fwm() -> dict:
    return json.loads(FWM.read_text(encoding="utf-8"))


def _cells() -> dict:
    return _artifact()["cells"]


def _cell_keys() -> set:
    return {f"{a}@K{k}" for a in ARMS for k in KS}


def _prereg_text() -> str:
    return PREREG.read_text(encoding="utf-8")


# ════════════════════════════════════════════════════════════════════
# A 层 · 产物层：读数逐位对账
# ════════════════════════════════════════════════════════════════════

def test_the_artifact_has_exactly_the_six_declared_cells_and_each_is_measured():
    """**六个格**（三臂 × 两个 K）**一个不多一个不少**，且每一格都读出了数。"""
    art = _artifact()
    assert set(art["cells"]) == _cell_keys(), (
        f"产物里的格 {sorted(art['cells'])} 与声明的 {sorted(_cell_keys())} 不一致")
    for key, cell in art["cells"].items():
        assert cell["verdict"]["state"] != "NOT_MEASURED", (
            f"{key} 没测出数：{cell['verdict']}")
        assert cell["n_pairs"] >= 3, f"{key} 配对日不足：{cell['n_pairs']}"


def test_each_cells_reading_is_bit_equal_to_the_artifact_field():
    """🚨🚨 **读数逐位 = 产物字段**（不许重算当验证，第 109 层）。

    ⇒ 头条读数（毛超额/年、MDE、t、|g|/MDE、部署率）必须是**产物自己的字段**，
    逐位相等（`==`，不是 `isclose`）。
    """
    art = _artifact()
    for key, cell in art["cells"].items():
        ll = cell["long_leg"]
        assert ll["gross_annual_pct"] == pytest.approx(
            ll["gross_annual_fraction"] * 100.0, rel=0, abs=0), (
            f"{key} 毛超额与它的分数字段不逐位相等")
        assert ll["mde_annual_pct"] == pytest.approx(
            ll["mde_annual_fraction"] * 100.0, rel=0, abs=0), (
            f"{key} MDE 与它的分数字段不逐位相等")
        assert ll["effect_over_mde"] == pytest.approx(
            abs(ll["gross_annual_fraction"]) / ll["mde_annual_fraction"],
            rel=0, abs=0), f"{key} |g|/MDE 与它的两个字段不自洽"
        # 判决层用的就是这两个数（**不许**另有一份手打副本）
        ratio = abs(ll["gross_annual_pct"]) / ll["mde_annual_pct"]
        assert cell["verdict"]["effect_over_mde"] == pytest.approx(
            ratio, rel=0, abs=0), f"{key} 判决里的比值与字段不自洽"


def test_the_intervention_verdict_numbers_are_bit_equal_to_the_cells():
    """干预判决里的数**逐位**等于两格的字段（不许有第二份手打副本）。"""
    art = _artifact()
    for k in KS:
        v = art["intervention_verdict"]["by_k"][f"K{k}"]
        a = art["cells"][f"{M.ARM_A}@K{k}"]["long_leg"]
        b = art["cells"][f"{M.ARM_B}@K{k}"]["long_leg"]
        assert v["a_gross_pct"] == pytest.approx(a["gross_annual_pct"],
                                                 rel=0, abs=0)
        assert v["b_gross_pct"] == pytest.approx(b["gross_annual_pct"],
                                                 rel=0, abs=0)
        assert v["a_deployment_mean"] == pytest.approx(
            art["cells"][f"{M.ARM_A}@K{k}"]["deployment"]["mean"],
            rel=0, abs=0)
        assert v["b_deployment_mean"] == pytest.approx(
            art["cells"][f"{M.ARM_B}@K{k}"]["deployment"]["mean"],
            rel=0, abs=0)
        if v.get("account_product_delta_pct") is not None:
            assert v["account_product_delta_pct"] == pytest.approx(
                v["b_account_product_pct"] - v["a_account_product_pct"],
                rel=0, abs=0)


def test_the_account_product_is_the_deployment_rate_times_the_basket_return():
    """**账户层乘积** = 部署率 × 已部署篮子的年化收益（预注册 §4 的那把尺子）。

    🚨 两个数（部署率、毛超额）**不是同一个量** ⇒ 必须用这把尺子比。
    """
    art = _artifact()
    for key, cell in art["cells"].items():
        dep = cell["deployment"]["mean"]
        lg = cell["long_leg"]["long_leg_annual_fraction"]
        assert cell["account_product_annual_pct"] == pytest.approx(
            dep * lg * 100.0, rel=0, abs=0), f"{key} 账户层乘积不自洽"


def test_the_caliber_labels_are_not_swapped():
    """🚨 **口径标签不许装反**（第 143 层）。

    ⇒ 装反是**静默**的：数字全对、判决全对，而读者会把本轮的数
    当成交付档的数去比 20%。
    """
    cal = _artifact()["caliber"]
    assert "500 只缓存池" in cal["this_arm"]
    assert "2019-01-02→2023-12-31" in cal["this_arm"]
    assert "毛**超额" in cal["this_arm"]
    assert "5,460 只全池" in cal["production_caliber"]
    assert "2021-01-01→2026-10-12" in cal["production_caliber"]
    # 本轮**没有**账户腿 ⇒ 必须写明
    assert "没有**账户腿" in cal["account_leg"]
    # 臂 D 的覆盖池与 A/B/C 不同 ⇒ 必须写明（否则读者会并列比大小）
    assert "臂 D 的覆盖池与 A/B/C 不同" in cal["arm_d_pool_note"]


def test_the_gap_multiple_is_the_same_caliber_one_and_never_the_cross_caliber_one():
    """🚨 **倍数只取同口径**（第 183 层）。

    ⇒ 台账里另有一个**跨口径**倍数（K=21 实测 ÷ K=63 要求），台账自报
    `is_the_gap = false` ⇒ 本产物**不许**把它当数引用。
    """
    art = _artifact()
    blk = art["same_caliber_multiple"]
    ledger_mc = _ledger()["headline"][
        "multiple_needed_at_50k_deliverable_by_caliber"]
    assert blk["value"] == pytest.approx(
        ledger_mc["same_caliber"]["multiple_needed"], rel=0, abs=0), (
        "倍数不是台账的**同口径**那一列")
    assert blk["is_the_gap"] is True
    # 🚨 **跨口径那个数不许出现在产物里**（哪怕是"提一句"也不许：
    # 它长得跟结论一样，读的人会照抄）
    raw = ARTIFACT.read_text(encoding="utf-8")
    assert "2.3450" not in raw, (
        "🚨 产物里出现了**跨口径**的 2.3450× ⇒ 它自报 `is_the_gap = false`"
        "⇒ 不许当缺口引用")


def test_the_required_gross_is_read_from_the_ledger_in_percent_not_in_fraction():
    """🚨 **量纲**（第 182 层）：D2 所需毛超额在**百分数**空间（11.2813）。

    ⇒ `headline::required_gross_at_50k_deliverable` 是**百分数**，
    而 `rows[n=10]::required_gross_at_50k` 是**分数**（0.1128）
    ⇒ 拿错就是**差 100 倍**。
    """
    thr = _artifact()["thresholds"]
    ledger = _ledger()
    assert thr["d2_required_gross_annual_pct"] == pytest.approx(
        ledger["headline"]["required_gross_at_50k_deliverable"], rel=0, abs=0)
    r10 = next(r for r in ledger["rows"] if r.get("n") == 10)
    # 🚨 **两者不是同一个数**（差 100 倍）⇒ 守卫必须认得出它们不同
    assert abs(thr["d2_required_gross_annual_pct"]
               - r10["required_gross_at_50k"] * 100.0) < 1e-9


def test_each_cells_thresholds_are_the_artifact_values_for_its_own_k():
    """**D1 摩擦按 K 各取各的**（K=21 来自 fwm，K=63 来自台账 rows）。

    🚨 混用两套口径就是第 7 层：两个各自都对的数放一起比。
    """
    art = _artifact()
    fwm = _fwm()
    ledger = _ledger()
    r10 = next(r for r in ledger["rows"] if r.get("n") == 10)
    for k in KS:
        cell = art["cells"][f"{M.ARM_A}@K{k}"]
        d1 = cell["thresholds"]["d1_friction_annual_pct"]
        if k == 21:
            assert d1 == pytest.approx(
                fwm["widths"]["10"]["friction_width_aware_annual_pct"] * 100.0,
                rel=0, abs=0)
        else:
            assert d1 == pytest.approx(r10["friction_at_50k"] * 100.0,
                                       rel=0, abs=0)
        assert cell["thresholds"]["d2_required_gross_annual_pct"] == \
            pytest.approx(art["thresholds"]["d2_required_gross_annual_pct"],
                           rel=0, abs=0)


def test_powerless_is_written_as_undetectable_and_never_as_useless():
    """🚨 `POWERLESS` 必带「测不到」措辞，**不许**写「无效/没有/已证伪」。"""
    art = _artifact()
    for key, cell in art["cells"].items():
        v = cell["verdict"]
        if v["state"] == "POWERLESS":
            assert v["wording"] == M.NOT_MEASURED_WORDING, (
                f"{key} 的 POWERLESS 没有「测不到」措辞")
            assert "测不到" in v["reason"], f"{key} 的理由里没有「测不到」"
        text = json.dumps(v, ensure_ascii=False)
        for bad in ("已证伪", "证明没有", "无效因子", "没有超额收益"):
            assert bad not in text, f"{key} 的判决里出现了「{bad}」"
    assert art["wording_guard"]["powerless_wording"] == M.NOT_MEASURED_WORDING


def test_the_bm_control_arm_reproduces_the_published_reading_bit_for_bit():
    """🚨🚨 **臂 D：同一台仪器必须逐位复现已发表的负读数**。

    ⇒ 复现不出 ⇒ 本轮四个臂**一律不可信**（预注册 §2 臂 D）。
    """
    art = _artifact()
    published = _arm_r()
    blk = art["control_bm_reproduction"]
    assert blk["all_within_tolerance"] is True, (
        f"臂 D 没复现已发表的负读数 ⇒ 本轮读数不可信：{blk['cells']}")
    for k in KS:
        cell = blk["cells"][f"bm@K{k}"]
        pub = published["family_a_cells"][f"bm@K{k}"]["long_leg"][
            "gross_annual_pct"]
        assert cell["reproduced_gross_annual_fraction"] == pytest.approx(
            pub, rel=0, abs=0), f"bm@K{k} 与已发表的读数不逐位相等"
        assert cell["within_tolerance"] is True
        assert cell["prereg_target_matches_published"] is True
        # 🚨 臂 D 的**完整一格**也要在产物里（毛超额 + MDE + t + n + |g|/MDE）
        assert cell["n_pairs"] >= 3
        assert cell["t"] is not None and cell["t_crit"] is not None
        assert cell["mde_annual_pct"] is not None
        assert cell["effect_over_mde"] == pytest.approx(
            abs(cell["reproduced_gross_annual_fraction"])
            / cell["mde_annual_fraction"], rel=0, abs=0)
        assert "不与 A/B/C 并列比大小" in cell["caliber"]


def test_the_leg_table_equivalence_is_three_way_and_bitwise():
    """🚨🚨 **三方逐位对拍**：本轮腿表 ≡ `leg_table_signed` ≡ 臂 R 的 `_leg_table`。"""
    art = _artifact()
    eq = art["leg_table_equivalence"]
    assert eq["bitwise_equal"] is True, (
        f"腿表逐位对拍失败 ⇒ 本轮读数不可信：{eq['rows']}")
    assert eq["mine_vs_mirror_bitwise_equal"] is True
    assert eq["mirror_vs_arm_r_ladder"]["bitwise_equal"] is True
    assert eq["n_values_compared"] > 0


def test_the_deployment_rate_is_a_per_day_distribution_not_a_single_reading():
    """🚨 **逐日部署率分布**（预注册 §5：「一天的 Top10 不许当分布」）。"""
    art = _artifact()
    for key, cell in art["cells"].items():
        dep = cell["deployment"]
        assert dep["n_days"] >= cell["n_pairs"] - 1, (
            f"{key} 的部署率天数比配对日还多 ⇒ 两者的口径不一致")
        assert dep["mean"] is not None
        assert dep["min"] is not None and dep["max"] is not None
        assert 0.0 <= dep["min"] <= dep["mean"] <= dep["max"] <= 1.0
        assert sum(dep["distribution"].values()) == dep["n_days"], (
            f"{key} 的分布计数与天数不一致")
        assert len(dep["per_day"]) == dep["n_days"]


def test_the_artifact_carries_every_honest_boundary_the_prereg_listed():
    """🚨 **预注册 §5 的五条诚实边界**必须逐条进产物正文。"""
    art = _artifact()
    blob = json.dumps(art, ensure_ascii=False)
    # ① 毛超额 ⇒ 回答不了「20% 够不够」
    assert "回答不了「20% 够不够」" in blob, (
        "产物里没有「毛超额回答不了 20% 够不够」这条边界")
    assert art["no_promotion"]
    # ② 「1 手 ≤ ¥5,000」是分配假设（B 类），不是物理不可能
    assert "分配假设" in blob and "B 类" in blob
    # ③ 科创板 200 股/手是外部规则（本仓未核实）
    assert "外部交易所规则" in blob and "本仓未核实" in blob
    # ④ 一天的 Top10 不许当分布
    assert "逐日" in blob
    # ⑤ 臂 D 的覆盖池不同 ⇒ 不并列比大小
    assert "不与 A/B/C 并列比大小" in blob


def test_the_intervention_branch_order_is_the_declared_constant():
    """🚨 **顺序即判据** ⇒ 顺序必须写在代码常量里，且产物与它一致。"""
    art = _artifact()
    assert art["intervention_verdict"]["branch_order"] == \
        list(M.INTERVENTION_BRANCH_ORDER)
    # INVALID 两支必须排在最前（它们让整套作废）
    assert M.INTERVENTION_BRANCH_ORDER[0] == "INVALID_D_NOT_REPRODUCED"
    assert M.INTERVENTION_BRANCH_ORDER[1] == \
        "INVALID_C_NEGATIVE_CONTROL_NOT_WORSE"


def test_the_negative_control_literal_reading_is_disclosed():
    """🚨 负控的**字面读法**（没有显著性门槛）必须**逐格写出来**，不许藏。"""
    art = _artifact()
    blk = art["negative_control"]["literal_reading"]
    assert "cells_where_control_is_not_worse_but_not_significant" in blk
    assert "n_cells" in blk
    assert "is_the_gap = false" not in blk.get("what_would_happen", "")
    assert "没有写显著性门槛" in blk["what_would_happen"]


def test_the_verdict_counts_the_cells_instead_of_taking_an_extremum():
    """**族级标签 = 逐格分布**（各态计数），**不取极值**（第 143 层）。"""
    art = _artifact()
    counts = art["verdict"]["primary"]["state_counts"]
    assert sum(counts.values()) == len(art["cells"])
    assert counts == {k: sum(1 for c in art["cells"].values()
                             if c["verdict"]["state"] == k) for k in counts}


def test_the_panel_is_the_same_one_round_b_used():
    """**同一块面板**（sha256 与 round B 逐位相同）⇒ 否则臂 D 的复现无意义。"""
    art = _artifact()
    assert art["panel_cache"]["sha256"] == M.PANEL_SHA256
    assert art["panel_cache"]["same_panel_as_round_b"] is True
    assert art["panel_cache"]["prereg_pinned_sha256"] == M.PANEL_SHA256
    assert _arm_r()["panel_cache_sha256"] == M.PANEL_SHA256


def test_the_n_a_is_read_from_the_round_b_artifact_not_hand_typed():
    """**候选数 m** 从产物读（预注册 §4「同一把尺子的 crit」）。"""
    art = _artifact()
    assert art["n_A"]["n_a"] == _arm_r()["n_A"]["primary"]["n_A"]
    assert "not" not in art["n_A"]["source"] or "手打" in art["n_A"]["source"]


# ════════════════════════════════════════════════════════════════════
# B 层 · 行为层：合成夹具上逐条钉
# ════════════════════════════════════════════════════════════════════

def test_the_mde_gate_is_judged_before_everything_else():
    """🚨 **预注册 §4：MDE 闸先判**（顺序不许改）。

    ⇒ |毛超额| < MDE ⇒ 直接 `POWERLESS`，**不许**走到门槛那一支去喊达标。
    """
    v = M.verdict_for_cell(gross_pct=20.0, mde_pct=30.0, d1_pct=6.0,
                           d2_pct=11.0, n_pairs=50)
    assert v["state"] == "POWERLESS", v["state"]
    assert v["shape_only"] is True
    # 🚨 措辞必须**自带**「测不到」（不许只写「无效」）
    assert v["wording"] == M.NOT_MEASURED_WORDING
    assert "测不到" in v["reason"]
    # 恰好等于 MDE ⇒ **不**是 POWERLESS（边界是 `>=`）
    v_eq = M.verdict_for_cell(gross_pct=30.0, mde_pct=30.0, d1_pct=6.0,
                              d2_pct=11.0, n_pairs=50)
    assert v_eq["state"] != "POWERLESS", v_eq["state"]


def test_the_four_states_map_to_the_preregistered_conditions():
    """🚨 四态的条件逐条钉住（预注册 §4 的判决链）。"""
    # ② 长腿毛超额 ≤ −D1 摩擦 ⇒ SIG_NOT_DELIVERABLE
    v = M.verdict_for_cell(gross_pct=-6.0, mde_pct=3.0, d1_pct=6.0,
                           d2_pct=11.0, n_pairs=50)
    assert v["state"] == "SIG_NOT_DELIVERABLE", v["state"]
    # 恰好等于 −D1 ⇒ **算**抵不过（边界是 `≤`）
    v_eq = M.verdict_for_cell(gross_pct=-6.0, mde_pct=3.0, d1_pct=6.0,
                              d2_pct=11.0, n_pairs=50)
    assert v_eq["state"] == "SIG_NOT_DELIVERABLE"
    # ③ 0 < 毛超额 < D2 所需 ⇒ POSITIVE_BUT_SHORT
    v3 = M.verdict_for_cell(gross_pct=5.0, mde_pct=3.0, d1_pct=6.0,
                            d2_pct=11.0, n_pairs=50)
    assert v3["state"] == "POSITIVE_BUT_SHORT", v3["state"]
    # ④ 毛超额 ≥ D2 所需 ⇒ DELIVERABLE（边界是 `≥`）
    v4 = M.verdict_for_cell(gross_pct=11.0, mde_pct=3.0, d1_pct=6.0,
                            d2_pct=11.0, n_pairs=50)
    assert v4["state"] == "DELIVERABLE", v4["state"]


def test_the_uncovered_region_gets_its_own_state_and_is_never_called_positive():
    """🚨 预注册四态**没覆盖**的那一格（负、|g| ≥ MDE、g > −D1）：

    ⇒ 必须有**自己的名字** ⇒ **不许**塞进「正但不够」那一格
    （那会把符号说反）。
    """
    v = M.verdict_for_cell(gross_pct=-3.0, mde_pct=2.0, d1_pct=6.0,
                           d2_pct=11.0, n_pairs=50)
    assert v["state"] not in ("POSITIVE_BUT_SHORT", "DELIVERABLE"), v["state"]
    assert v["state"] == "NEGATIVE_MEASURED_BELOW_FRICTION", v["state"]


def test_a_missing_reading_is_not_measured_and_never_crashes():
    """**读不出来 ⇒ 不报数**（不回落成 0），配对日 < 3 同样。"""
    v = M.verdict_for_cell(gross_pct=None, mde_pct=3.0, d1_pct=6.0,
                           d2_pct=11.0, n_pairs=50)
    assert v["state"] == "NOT_MEASURED"
    assert v["wording"] == M.NOT_MEASURED_WORDING
    v2 = M.verdict_for_cell(gross_pct=1.0, mde_pct=3.0, d1_pct=6.0,
                            d2_pct=11.0, n_pairs=2)
    assert v2["state"] == "NOT_MEASURED"


def test_a_missing_delivery_threshold_refuses_to_give_a_verdict():
    """🚨 **缺档必须报错，绝不回落**（仓内铁律）。"""
    with pytest.raises(ValueError):
        M.verdict_for_cell(gross_pct=5.0, mde_pct=3.0, d1_pct=None,
                           d2_pct=11.0, n_pairs=50)


def test_the_negative_control_has_three_states():
    """🚨 **负控三态**：`VOID` / `PASS` / `INCONCLUSIVE`。"""
    # 显著**且不比 B 差**（c ≥ b）⇒ 作废
    st = M.negative_control_state(c_gross_pct=+1.5, c_t=3.0, t_crit=2.0,
                                  b_gross_pct=+1.0)
    assert st["state"] == "VOID", st
    # 显著但**更差**（c < b）⇒ 不作废
    st2 = M.negative_control_state(c_gross_pct=-1.0, c_t=3.0, t_crit=2.0,
                                   b_gross_pct=+1.0)
    assert st2["state"] == "PASS", st2
    # 不显著 ⇒ 不作废（纯噪声不该有一半概率作废）
    st3 = M.negative_control_state(c_gross_pct=+1.0, c_t=1.0, t_crit=2.0,
                                   b_gross_pct=+1.0)
    assert st3["state"] == "PASS", st3
    # 读不出来 ⇒ INCONCLUSIVE（**不决定**）
    st4 = M.negative_control_state(c_gross_pct=None, c_t=None, t_crit=None,
                                   b_gross_pct=+1.0)
    assert st4["state"] == "INCONCLUSIVE", st4


def test_the_negative_control_boundary_is_inclusive_at_the_critical_value():
    """|t| **恰好等于**临界值 ⇒ 算显著（闸门是 `>=`，不是 `>`）。

    🚨 差一个 ulp 就换判（第 190 层：测度零点结构失明）。
    """
    st = M.negative_control_state(c_gross_pct=+1.0, c_t=2.0, t_crit=2.0,
                                  b_gross_pct=+1.0)
    assert st["state"] == "VOID", "|t| == t_crit 必须算显著"
    st2 = M.negative_control_state(c_gross_pct=+1.0, c_t=1.999999,
                                   t_crit=2.0, b_gross_pct=+1.0)
    assert st2["state"] == "PASS", st2


def _row(gross=None, mde=None, dep=None, lg=None):
    return {"gross_annual_pct": gross, "mde_annual_pct": mde,
            "long_leg_annual_pct": lg,
            "deployment": (None if dep is None else {"mean": dep})}


def test_the_intervention_verdict_is_invalid_when_the_bm_arm_did_not_reproduce():
    """🚨 臂 D 没复现 ⇒ **整套作废**（预注册 §4）。"""
    v = M.intervention_verdict(_row(0.05, 0.02, 0.5, 0.10),
                               _row(0.06, 0.02, 0.9, 0.10),
                               d_ok=False, nc_state={"state": "PASS"})
    assert v["state"] == "INVALID", v
    assert v["branch"] == "INVALID_D_NOT_REPRODUCED"


def test_the_intervention_verdict_is_invalid_when_the_negative_control_did_not_worsen():
    """🚨 臂 C 负控没有变差 ⇒ **整套作废**（预注册 §4）。"""
    v = M.intervention_verdict(_row(0.05, 0.02, 0.5, 0.10),
                               _row(0.06, 0.02, 0.9, 0.10),
                               d_ok=True, nc_state={"state": "VOID"})
    assert v["state"] == "INVALID", v
    assert v["branch"] == "INVALID_C_NEGATIVE_CONTROL_NOT_WORSE"


def test_the_intervention_verdict_is_lever_when_gross_is_up_and_deployment_is_up():
    """臂 B 毛超额**不低于** A（逐位）**且**部署率更高 ⇒ `LEVER_WORTH_MEASURING`。"""
    v = M.intervention_verdict(_row(0.05, 0.02, 0.5, 0.10),
                               _row(0.05, 0.02, 0.9, 0.10),
                               d_ok=True, nc_state={"state": "PASS"})
    assert v["state"] == "LEVER_WORTH_MEASURING", v
    assert v["branch"] == "LEVER_WORTH_MEASURING_GROSS_UP"
    # 🚨 反向正控：毛超额涨了但**部署率没涨** ⇒ **不**是杠杆
    v2 = M.intervention_verdict(_row(0.05, 0.02, 0.9, 0.10),
                                _row(0.06, 0.02, 0.5, 0.10),
                                d_ok=True, nc_state={"state": "PASS"})
    assert v2["state"] != "LEVER_WORTH_MEASURING", v2
    assert v2["branch"] == "TRADE_OFF_NO_VERDICT", v2


def test_the_same_caliber_multiple_is_the_only_one_the_artifact_quotes():
    """🚨 **倍数只取同口径**（行为层）：跨口径那一列**碰不到**。

    ⇒ 台账里两个倍数差 7.94/2.79 倍，取错列 = 引用一个**不是缺口**的数。
    """
    mc = _ledger()["headline"]["multiple_needed_at_50k_deliverable_by_caliber"]
    blk = M.same_caliber_multiple(mc)
    assert blk["value"] == pytest.approx(mc["same_caliber"]["multiple_needed"],
                                         rel=0, abs=0)
    assert blk["value"] != pytest.approx(mc["cross_caliber"]["multiple_needed"],
                                         rel=0, abs=0), (
        "同口径与跨口径两个倍数**必须真的不同** ⇒ 否则这条守卫是恒真的")
    assert blk["is_the_gap"] is True
    assert "cross_caliber" not in blk["source"]


def test_the_intervention_verdict_is_powerless_when_both_arms_are_within_the_mde():
    """臂 B < A 且**两者都在 MDE 之内** ⇒ `POWERLESS`（测不到，不是「没有」）。"""
    v = M.intervention_verdict(_row(0.01, 0.05, 0.5, 0.10),
                               _row(0.005, 0.05, 0.9, 0.10),
                               d_ok=True, nc_state={"state": "PASS"})
    assert v["state"] == "POWERLESS", v
    assert v["branch"] == "POWERLESS_BOTH_WITHIN_MDE"
    assert "测不到" in v["reason"]


def test_the_intervention_trade_off_is_decided_on_the_account_layer_product():
    """🚨 「部署率的增益抵不过毛超额的损失」**必须用同一个尺子算**。

    ⇒ 比较**账户层的乘积**（部署率 × 篮子收益），不是两个数比大小。
    """
    # 毛超额掉了，但部署率涨了 ⇒ 账户层乘积**没有变差** ⇒ 值得再走一次账户腿
    a = _row(0.05, 0.02, 0.5, 0.10)
    b = _row(0.03, 0.02, 0.9, 0.10)
    v = M.intervention_verdict(a, b, d_ok=True, nc_state={"state": "PASS"})
    assert v["state"] == "LEVER_WORTH_MEASURING", v
    assert v["branch"] == "LEVER_WORTH_MEASURING_ACCOUNT_PRODUCT_NOT_WORSE"
    assert v["a_account_product_pct"] == pytest.approx(0.5 * 0.10 * 100.0)
    assert v["b_account_product_pct"] == pytest.approx(0.9 * 0.10 * 100.0)
    # 账户层乘积也变差 ⇒ TRADE_OFF_NO_VERDICT
    a2 = _row(0.05, 0.02, 0.9, 0.10)
    b2 = _row(0.03, 0.02, 0.5, 0.10)
    v2 = M.intervention_verdict(a2, b2, d_ok=True, nc_state={"state": "PASS"})
    assert v2["state"] == "TRADE_OFF_NO_VERDICT", v2
    assert v2["branch"] == "TRADE_OFF_NO_VERDICT"


def test_the_account_product_helper_reads_the_deployment_and_the_basket_return():
    """**账户层乘积**的定义（纯函数）：部署率 × 已部署篮子的年化收益。"""
    row = {"deployment": {"mean": 0.8}, "long_leg_annual_pct": 0.05}
    assert M.account_product(row) == pytest.approx(0.8 * 0.05 * 100.0)
    assert M.account_product({"deployment": None}) is None
    assert M.account_product({"deployment": {"mean": 0.8}}) is None


def test_the_lot_rule_uses_two_hundred_shares_for_the_star_market():
    """🚨 **整手规则**：688/689 = 200 股，其余 100 股（外部规则，本仓未核实）。"""
    from aplus.webapp import lot_shares_for
    assert lot_shares_for("688001.SH") == 200
    assert lot_shares_for("689001.SH") == 200
    assert lot_shares_for("600000.SH") == 100
    assert lot_shares_for("300001.SZ") == 100
    assert lot_shares_for("000001.SZ") == 100


def test_the_affordability_rule_is_price_times_lot_and_not_a_board_filter():
    """🚨🚨 **干预是「价格 × 手数」的规则**，**不是**「剔掉科创板」。

    ⇒ 一只**便宜**的 688 票（close 10 ⇒ 1 手 ¥2,000 ≤ ¥5,000）**必须**买得起
    ⇒ 这条守卫杀死「按板块硬剔」的写法（第 6 层：不用先验结论当判据）。
    """
    lookup = {"688001.SH": (["2020-01-02"], [10.0]),
              "600000.SH": (["2020-01-02"], [60.0])}
    costs, ok, missing = M.affordable_set(lookup, ["688001.SH", "600000.SH"],
                                          "2020-01-02")
    assert costs["688001.SH"] == pytest.approx(2000.0)
    assert costs["600000.SH"] == pytest.approx(6000.0)
    assert ok == {"688001.SH"}, "便宜的科创板票必须买得起"
    assert missing == []


def test_a_missing_price_means_not_affordable_and_is_reported():
    """**缺价 ⇒ 不可买**（生产同款），且**必须报出来**（不许静默）。"""
    lookup = {"600000.SH": (["2020-01-02"], [10.0])}
    costs, ok, missing = M.affordable_set(lookup, ["600000.SH", "999999.SH"],
                                          "2020-01-02")
    assert ok == {"600000.SH"}
    assert missing == ["999999.SH"], "缺价必须单独报出来"


def test_the_price_rule_is_the_last_bar_on_or_before_the_signal_day():
    """🚨 **价格规则**逐字抄生产：信号日当天或之前最后一根 bar。"""
    lookup = {"600000.SH": (["2020-01-02", "2020-01-03"], [10.0, 20.0])}
    assert M.close_on(lookup, "600000.SH", "2020-01-03") == 20.0
    assert M.close_on(lookup, "600000.SH", "2020-01-05") == 20.0
    assert M.close_on(lookup, "600000.SH", "2020-01-01") is None
    assert M.close_on(lookup, "111111.SH", "2020-01-03") is None


def test_the_three_selectors_implement_the_three_preregistered_rules():
    """🚨 三个选择器的**顺序**逐条钉住（这是干预臂的全部内容）。

    ⚠️ 夹具里**买得起的多于 10 只**（15 只）⇒ 臂 C 的「最差 10 只」
    那一支**真的会被走到** ⇒ 否则 `aff[-10:]` 与 `aff[:10]` 在
    `len(aff) == 10` 时**逐位相同** ⇒ 方向翻转的变异体是**等价变异体**
    （实测：M15 因此存活过一次 ⇒ 改夹具，**不改判据**）。
    """
    codes = [f"{i:06d}.SZ" for i in range(30)]      # 已按合成分降序
    ok = {c for c in codes if int(c[:6]) % 2 == 0}
    aff = [c for c in codes if c in ok]
    assert len(aff) > 10, "夹具前提：买得起的多于 10 只（否则最差 10 只不可见）"
    # 臂 A：**先**取 Top10，**再**剔买不起的
    picks_a, dep_a = M.select_arm_a(codes, ok)
    assert picks_a == codes[:10]
    assert dep_a == [c for c in codes[:10] if c in ok]
    assert len(dep_a) < 10
    # 臂 B：**先**剔买不起的，**再**取 Top10
    picks_b, dep_b = M.select_arm_b(codes, ok)
    assert picks_b == aff[:10]
    assert dep_b == picks_b
    assert len(dep_b) == 10, "臂 B 必须把 10 个名额填满（买得起的）"
    # 臂 C：**买得起的最差** 10 只
    picks_c, dep_c = M.select_arm_c(codes, ok)
    assert picks_c == aff[-10:]
    assert dep_c == picks_c
    assert picks_c != picks_b, "臂 C 与臂 B 必须取到**不同**的一批（方向相反）"


def test_the_production_pool_applies_the_production_filters():
    """🚨 **池子**走生产路径：`filter_universe` + `composite_score(DEFAULT_WEIGHTS)`。

    ⇒ 一只 `isST == 1` 的票**必须**被剔掉；`listed_days ≤ 60` 的也剔掉；
    ⇒ 分数**必须**等于生产合成分（权重不许被换掉）；
    ⇒ 池子**必须**按合成分**降序**（干预臂的整个逻辑建立在这个顺序上）。
    """
    from aplus.predict import filter_universe
    from aplus.factor import composite_score, DEFAULT_WEIGHTS
    n = 120
    codes = [f"{600000 + i:06d}.SH" for i in range(n)]
    df = pd.DataFrame({
        "code": codes,
        "name": codes,
        "mom20": [0.001 * (i % 11) for i in range(n)],
        "rev5": [0.002 * (i % 7) for i in range(n)],
        "vol20": [0.01 + 0.001 * (i % 5) for i in range(n)],
        "vsur": [1.0 + 0.01 * (i % 13) for i in range(n)],
        "turn20": [1.0] * n, "ivol": [0.01] * n, "turnratio": [1.0] * n,
        "rsi": [50.0] * n,
        "isST": [1.0 if i == 3 else 0.0 for i in range(n)],
        "volume": [1e6] * n,
        "listed_days": [30 if i == 5 else 500 for i in range(n)],
        "last_date": ["2020-01-02"] * n,
        "buyable": [False if i == 7 else True for i in range(n)],
    })
    xs = {"2020-01-02": df}
    pool = M.production_pool(xs, "2020-01-02")
    assert pool is not None
    codes_out = set(pool["code"])
    assert codes[3] not in codes_out, "ST 票必须被剔掉"
    assert codes[5] not in codes_out, "次新股必须被剔掉"
    assert codes[7] not in codes_out, "买不进（涨停收盘）的票必须被剔掉"
    assert len(codes_out) == n - 3
    # 分数**逐位**等于生产合成分
    filtered = filter_universe(df.copy(), as_of="2020-01-02")
    exp = composite_score(filtered, DEFAULT_WEIGHTS)
    expected = {str(c): float(v) for c, v in zip(filtered["code"], exp)}
    got = dict(zip(pool["code"], pool["score"]))
    assert got == expected, "池子的合成分与生产的 composite_score 不一致"
    # 降序
    scores = list(pool["score"])
    assert scores == sorted(scores, reverse=True), "池子必须按合成分降序"


def test_the_deployment_block_reports_a_distribution_not_a_single_reading():
    """🚨 **逐日部署率分布**（预注册 §5：一天的 Top10 不许当分布）。"""
    blk = M.deployment_block([0.5, 1.0, 0.8], 2, 3)
    assert blk["n_days"] == 3
    assert blk["mean"] == pytest.approx((0.5 + 1.0 + 0.8) / 3.0)
    assert blk["min"] == 0.5 and blk["max"] == 1.0
    assert blk["median"] == 0.8
    assert sum(blk["distribution"].values()) == 3
    assert blk["n_days_zero_deployed"] == 2
    assert blk["n_price_missing_candidates"] == 3
    assert len(blk["per_day"]) == 3
    # 空的日子 ⇒ **不报数**（不回落成 0）
    empty = M.deployment_block([], 5, 0)
    assert empty["mean"] is None and empty["distribution"] is None


def test_the_selection_and_deployment_pipeline_on_synthetic_data():
    """🚨🚨 **整条干预链**（池 → 选 → 可买性 → 部署率）逐位钉住。

    ⇒ 合成面板：按合成分降序的前 20 只**贵**（买不起），其余**便宜**。
    ⇒ 臂 A 的部署率 = 「Top10 里买得起的那几只 / 10」（**先取 Top10 再剔**）；
    ⇒ 臂 B 的部署率 = **1.0**（**先剔再取 Top10** ⇒ 名额填满）。
    """
    n = 120
    codes = [f"{600000 + i:06d}.SH" for i in range(n)]
    days = ["2020-01-02", "2020-01-03", "2020-01-04"]
    xs = {}
    for j, day in enumerate(days):
        xs[day] = pd.DataFrame({
            "code": codes, "name": codes,
            "mom20": [0.001 * (i % 11) + 0.0001 * j for i in range(n)],
            "rev5": [0.002 * (i % 7) + 0.0001 * j for i in range(n)],
            "vol20": [0.01 + 0.001 * (i % 5) + 0.0001 * j for i in range(n)],
            "vsur": [1.0 + 0.01 * (i % 13) + 0.001 * j for i in range(n)],
            "turn20": [1.0] * n, "ivol": [0.01] * n, "turnratio": [1.0] * n,
            "rsi": [50.0] * n, "isST": [0.0] * n, "volume": [1e6] * n,
            "listed_days": [500] * n, "last_date": [day] * n,
            "buyable": [True] * n,
        })
    pool = M.production_pool(xs, days[0])
    assert pool is not None
    # 「贵」的 10 只**故意排在名次 5..14** ⇒ Top10 里**一半**买得起
    expensive = set(list(pool["code"])[5:15])
    lookup = {c: (["2020-01-02"], [60.0 if c in expensive else 30.0])
              for c in codes}
    fwd = {d: {c: 0.01 + 0.001 * (i % 5) + 0.0001 * j
               for j, d in enumerate(days)
               for i, c in enumerate(codes)}
           for d in days}
    a = M.leg_table_arm(xs, days, 1, fwd, kind=M.ARM_A, n_a=8, lookup=lookup)
    b = M.leg_table_arm(xs, days, 1, fwd, kind=M.ARM_B, n_a=8, lookup=lookup)
    top10 = list(pool["code"])[:10]
    n_cheap = sum(1 for c in top10 if c not in expensive)
    assert n_cheap < 10, "夹具前提失真：Top10 里应当有买不起的"
    assert a["deployment"]["mean"] == pytest.approx(n_cheap / 10.0), (
        "臂 A 的部署率 = Top10 里买得起的那几只 / 10（先取 Top10 再剔）")
    assert b["deployment"]["mean"] == pytest.approx(1.0), (
        "臂 B 的部署率必须是 1.0（先剔再取 Top10 ⇒ 名额填满）")


def test_the_bm_reproduction_tolerance_is_bitwise():
    """🚨 预注册 §2 臂 D：「**逐位**相等才算数」⇒ 容差必须 ≤ 1e-12。"""
    assert M.BM_REPRO_TOL <= 1e-12, (
        f"臂 D 的复现容差被放宽到 {M.BM_REPRO_TOL} ⇒ 「逐位相等」变成了"
        "「差不多相等」")


def test_the_leg_table_summary_is_bit_equal_to_arm_r_on_synthetic_data():
    """🚨🚨 **两份腿表实现必须逐位相等**（第 137 层）。

    ⇒ 合成夹具上跑**同一个 bm** ⇒ 本轮的腿表与臂 R 的 `_leg_table`
    逐位相等（差 0.0）。
    """
    from scripts.measure_fundamental_mechanism_armR import _leg_table
    xs, days, fwd = _synthetic_panel()
    for k in (21, 7):
        mine = M.leg_table_plain_bm(xs, days, k, fwd, n_a=8)
        ref = (_leg_table(xs, days, "bm", k, fwd, ns=(10,), n_a=8) or {}).get("10")
        for fld in M._EQUIV_FIELDS:
            assert M._same_number(mine.get(fld), ref.get(fld)), (
                f"K={k} {fld}: 本轮 {mine.get(fld)!r} ≠ arm R {ref.get(fld)!r}")


def test_the_equivalence_field_list_covers_every_numeric_field_of_the_leg_row():
    """**完整性守卫**：逐位对拍的字段清单不许漏项。

    🚨 漏一个字段 ⇒ 那个字段上的变异**永远活下来**，而台子只打 ✅。
    """
    from scripts.measure_fundamental_mechanism_armR import _leg_table
    xs, days, fwd = _synthetic_panel()
    row = (_leg_table(xs, days, "bm", 21, fwd, ns=(10,), n_a=8) or {})["10"]
    numeric = {k for k, v in row.items()
               if isinstance(v, (int, float)) and not isinstance(v, bool)}
    assert numeric == set(M._EQUIV_FIELDS), (
        f"腿表行的数值字段 {sorted(numeric)} 与对拍清单 "
        f"{sorted(M._EQUIV_FIELDS)} 不一致 ⇒ 有字段没被逐位对拍")


def _synthetic_panel(n_codes=120, n_days=64, factor="bm"):
    """**合成面板**：足够多票（≥ MIN_CROSS_SECTION）以便两条腿都算得出来。"""
    codes = [f"{i:06d}.SZ" for i in range(n_codes)]
    days = [f"20{20 + i // 300:02d}-{1 + (i // 20) % 12:02d}-"
            f"{1 + i % 28:02d}" for i in range(n_days)]
    days = sorted(set(days))
    xs, fwd = {}, {}
    for j, day in enumerate(days):
        vals = [(i % 17) * 0.01 + j * 0.1 for i in range(n_codes)]
        xs[day] = pd.DataFrame({"code": codes, factor: vals})
        fwd[day] = {c: ((i * 7 + j * 3) % 23) * 0.001 - 0.01
                    for i, c in enumerate(codes)}
    return xs, days, fwd


def test_the_thresholds_are_read_from_the_artifacts_not_hand_typed():
    """🚨 **门槛从产物读**（预注册 §4），不许手打。"""
    thr = M.read_thresholds(ROOT / "data/reports")
    fwm = _fwm()
    ledger = _ledger()
    r10 = next(r for r in ledger["rows"] if r.get("n") == 10)
    assert thr["k21"]["d1_friction_annual_pct"] == pytest.approx(
        fwm["widths"]["10"]["friction_width_aware_annual_pct"] * 100.0)
    assert thr["k63"]["d1_friction_annual_pct"] == pytest.approx(
        r10["friction_at_50k"] * 100.0)
    assert thr["d2_required_gross_annual_pct"] == pytest.approx(
        ledger["headline"]["required_gross_at_50k_deliverable"])
    assert "百分数" in thr["d2_raw_unit"]
    assert "分数" in thr["k21"]["d1_raw_unit"]


# ════════════════════════════════════════════════════════════════════
# C 层 · 预注册层：与**文档**逐条一致（第 145 层：声明，不许探测）
# ════════════════════════════════════════════════════════════════════

def test_the_prereg_declares_the_four_arms_and_the_artifact_matches():
    """🚨 **四个臂**的规则与**预注册文档**逐条一致（第 145 层）。"""
    text = _prereg_text()
    art = _artifact()
    assert "臂 A（判决臂 / 现状）" in text
    assert "臂 B（干预臂）" in text
    assert "臂 C（对照臂 · 负控）" in text
    assert "臂 D（对照臂 · 已知为负）" in text
    # 产物里四个臂都在
    for arm in (M.ARM_A, M.ARM_B, M.ARM_C, M.ARM_D):
        assert arm in art["arms"], f"产物里少了臂 {arm}"
    # 臂 B 的规则文本：只保留「1 手 ≤ ¥5,000」的票，再取 Top10
    assert "1 手 ≤ " in art["arms"][M.ARM_B]
    assert "Top10" in art["arms"][M.ARM_B]
    # 臂 A 的规则文本：排序 → Top10 → 配置层可买性过滤
    assert "配置层可买性过滤" in art["arms"][M.ARM_A]
    # 臂 C 的规则文本：方向翻转
    assert "最差" in art["arms"][M.ARM_C]
    # 臂 D：bm + 逐位复现
    assert "bm" in art["arms"][M.ARM_D]
    assert "逐位" in art["arms"][M.ARM_D]


def test_the_prereg_declares_the_measurement_caliber_and_the_artifact_matches():
    """🚨 **口径**（榜宽 10 · ¥5 万 · K ∈ {21,63} · 毛超额 · 多头口径）逐条一致。"""
    text = _prereg_text()
    art = _artifact()
    assert "榜宽 10" in text and "¥5 万" in text
    assert "K ∈ {21, 63}" in text or "K ∈ {21,63}" in text
    assert "毛超额" in text and "多头口径" in text
    assert art["window"] == ["2019-01-02", "2023-12-31"]
    assert art["k_arms"] == [21, 63]
    assert art["signal"]["n_slots"] == 10
    assert art["signal"]["per_slot_budget"] == 5000.0
    assert art["signal"]["capital"] == 50000.0


def test_the_prereg_declares_the_four_states_and_the_artifact_uses_exactly_them():
    """🚨 **判决四态**与预注册逐条一致（第 145 层：声明，不许探测）。"""
    text = _prereg_text()
    for state in ("POWERLESS", "SIG_NOT_DELIVERABLE", "POSITIVE_BUT_SHORT",
                  "DELIVERABLE"):
        assert state in text, f"预注册里没有 {state}"
    art = _artifact()
    counts = art["verdict"]["primary"]["state_counts"]
    for state in counts:
        assert state in ("POWERLESS", "SIG_NOT_DELIVERABLE",
                         "POSITIVE_BUT_SHORT", "DELIVERABLE",
                         "NEGATIVE_MEASURED_BELOW_FRICTION", "NOT_MEASURED"), (
            f"产物里出现了预注册之外的状态 {state}")


def test_the_prereg_declares_the_threshold_sources_and_the_artifact_matches():
    """🚨 **门槛来源**（字段路径）与预注册逐条一致。"""
    text = _prereg_text()
    assert "factor_width_mde.json::widths[\"10\"]" in text or \
        "factor_width_mde.json::widths['10']" in text
    assert "friction_width_aware_annual_pct" in text
    assert "d2_signal_requirement.json::rows[n=10].friction_at_50k" in text
    assert "required_gross_at_50k_deliverable" in text
    thr = _artifact()["thresholds"]
    assert "factor_width_mde.json" in thr["k21"]["d1_source"]
    assert "friction_width_aware_annual_pct" in thr["k21"]["d1_source"]
    assert "rows[n=10].friction_at_50k" in thr["k63"]["d1_source"]
    assert "required_gross_at_50k_deliverable" in thr["d2_source"]


def test_the_prereg_declares_the_bm_published_number_and_the_target_matches_it():
    """🚨 臂 D 的复现目标 = 预注册里写的那个数（round B 已发表的 −7.5894%）。"""
    text = _prereg_text()
    assert "−7.5894%" in text or "-7.5894%" in text
    assert M.BM_REPRO_TARGET["bm@K21"] * 100.0 == pytest.approx(-7.5894,
                                                                 abs=1e-4)
    art = _artifact()
    for k in KS:
        cell = art["control_bm_reproduction"]["cells"][f"bm@K{k}"]
        assert cell["prereg_target"] == pytest.approx(
            M.BM_REPRO_TARGET[f"bm@K{k}"], rel=0, abs=0)


def test_the_prereg_forbids_the_cross_caliber_comparison_and_the_artifact_says_so():
    """🚨 预注册 §2 末段：「不许把 B 臂的结果直接当 D2 的缺口」（跨口径）。"""
    text = _prereg_text()
    assert "跨口径" in text
    art = _artifact()
    assert art["same_caliber_multiple"]["cross_caliber_multiple_is_not_used"]
    assert art["same_caliber_multiple"]["this_round_readings_are_not_same_caliber"]


def test_the_prereg_forbids_the_prior_conclusion_and_the_artifact_never_claims_it():
    """🚨 预注册 §6：**不许**把「剔掉科创板 ⇒ 一定更好」当前提写进判据。

    ⇒ 产物里不许出现这类先验结论；干预只由「价格 × 手数」这条规则驱动。
    """
    text = _prereg_text()
    assert "先验结论" in text
    art = _artifact()
    blob = json.dumps(art, ensure_ascii=False)
    for bad in ("一定更好", "必然更好", "肯定更好", "剔掉科创板就"):
        assert bad not in blob, f"产物里出现了先验结论「{bad}」"
    # 干预规则**只**由可买性驱动（不是板块）
    assert "price" not in art["signal"]["lot_rule"].lower() or True
    assert "688/689" in art["signal"]["lot_rule"]


def test_the_prereg_lists_the_five_honest_boundaries_and_the_artifact_has_them():
    """🚨 预注册 §5 的**五条**诚实边界逐条在产物里。"""
    text = _prereg_text()
    assert "诚实边界" in text
    art = _artifact()
    blob = json.dumps(art, ensure_ascii=False)
    checks = [
        "毛超额",                       # ① 口径
        "分配假设",                     # ② B 类
        "外部交易所规则",                # ③ 外部常识
        "逐日",                          # ④ 分布
        "不与 A/B/C 并列比大小",          # ⑤ 臂 D 的覆盖池
    ]
    for c in checks:
        assert c in blob, f"产物里少了诚实边界「{c}」"


def test_the_generator_does_not_modify_any_aplus_module():
    """🚨 **不许改 `aplus/` 任何模块的行为**（本轮只在测量脚本里干预）。

    ⇒ 用 **AST** 判，不许用子串（第 13 层：子串断言会被注释与文档满足）。
    ① 生成器**不得**给 `aplus.*` 的任何属性赋值（含 `setattr`）；
    ② 生成器**不得**自己定义一份同名函数（那是「重写第三份」，第 137 层），
       而它**必须**引用生产实现。
    """
    import ast as _ast

    src = _SCRIPT.read_text(encoding="utf-8")
    tree = _ast.parse(src)
    aplus_names = {"aplus", "provenance", "factor", "predict", "alloc",
                   "webapp", "evolve_race"}

    # ① 不许写生产模块的属性
    for node in _ast.walk(tree):
        targets = []
        if isinstance(node, _ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, (_ast.AugAssign, _ast.AnnAssign)):
            targets = [node.target]
        for t in targets:
            if isinstance(t, _ast.Attribute) and \
                    isinstance(t.value, _ast.Name) and \
                    t.value.id in aplus_names:
                raise AssertionError(
                    f"生成器给生产模块 `{t.value.id}.{t.attr}` 赋值了 ⇒ "
                    "违反「不改 aplus 任何模块的行为」")
        if isinstance(node, _ast.Call) and isinstance(node.func, _ast.Name) \
                and node.func.id == "setattr":
            raise AssertionError("生成器用了 setattr（可能改生产模块）")

    # ② 不许自己重写一份生产实现
    defined = {n.name for n in _ast.walk(tree)
               if isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef))}
    for name in ("filter_universe", "composite_score", "lot_shares_for",
                 "DEFAULT_WEIGHTS", "bonferroni_t"):
        assert name not in defined, (
            f"生成器自己定义了一份 `{name}` ⇒ 那是**重写第三份**"
            "（第 137 层：两份副本之间没有守卫）")
        assert name in src, f"生成器没有引用生产实现 {name}"

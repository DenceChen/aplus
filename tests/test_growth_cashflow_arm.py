"""守卫：成长族 + 现金流族的长腿臂（`scripts/measure_growth_cashflow_arm.py`）。

## 这个文件守什么

本轮交付物是**一个测量**（不是一段代码）：四个因子（`g_profit` / `g_rev` /
`cf_accrual` / `cf_growth`）在**臂 R 同款仪器**上的长腿毛超额、MDE、t、
独立观测数。
⇒ 守卫要钉的是**读数与口径**，不是「代码跑通了」。

## 三层结构（每层有它自己的失效方向）

| 层 | 钉什么 | 它**看不见**什么 |
|---|---|---|
| **A 产物层** | 产物里的读数**逐位**等于产物字段（无手打副本、无换算、单位不串）；口径标签不许互换；倍数只取同口径 | 源码改了没重跑（第 96/118 层：读快照的守卫等价于没守） |
| **B 行为层** | 判决链 / 负控 / 因子定义 / 腿表 / PIT 断言 —— **合成夹具**上逐条钉 | `main()` 的编排层（**已知欠账**，见下） |
| **C 预注册层** | 因子集、方向、源列与**预注册文档**逐条一致（第 145 层：声明，不许探测） | 门槛数值（门槛**从产物读**，不在文档里手打） |

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

见 `tests/mutation_specs/test_growth_cashflow_arm.mut` 与同目录 README。
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

_SCRIPT = ROOT / "scripts" / "measure_growth_cashflow_arm.py"


def _mod():
    spec = importlib.util.spec_from_file_location("_gc_arm", _SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


M = _mod()

ARTIFACT = ROOT / "data/reports/growth_cashflow_arm_2026-10-21.json"
PREREG = ROOT / "docs/PREREG_GROWTH_CASHFLOW_ARM_2026-10-21.md"
ARM_R_ARTIFACT = ROOT / "data/reports/fundamental_mechanism_armR_2026-10-21.json"
DEPLOYED_ARTIFACT = ROOT / "data/reports/deployed_d2_verdict.json"
LEDGER = ROOT / "data/reports/d2_signal_requirement.json"

FACTORS = ("g_profit", "g_rev", "cf_accrual", "cf_growth")
KS = (21, 63)
FAMILIES = ("GROWTH", "CASHFLOW")


def _artifact() -> dict:
    return json.loads(ARTIFACT.read_text(encoding="utf-8"))


def _arm_r() -> dict:
    return json.loads(ARM_R_ARTIFACT.read_text(encoding="utf-8"))


def _deployed() -> dict:
    return json.loads(DEPLOYED_ARTIFACT.read_text(encoding="utf-8"))


def _ledger() -> dict:
    return json.loads(LEDGER.read_text(encoding="utf-8"))


def _cells() -> dict:
    return _artifact()["family_cells"]


def _cell_keys() -> set:
    return {f"{f}@K{k}" for f in FACTORS for k in KS}


# ════════════════════════════════════════════════════════════════════
# A 层 · 产物层：读数逐位对账
# ════════════════════════════════════════════════════════════════════

def test_the_artifact_has_exactly_the_eight_declared_cells_and_each_is_measured():
    """**非空性守卫**：先把「产物里到底有什么」钉死。

    🚨 没有这一条，下面所有逐位对账在**空产物**上都会绿
    （`None == None` 为真 ⇒ 第 178 层：没定阈值的「一致」等于没有判据）。
    """
    cells = _cells()
    assert set(cells) == _cell_keys(), (
        f"产物里的格子集合不对：{sorted(cells)}")
    for key, cell in cells.items():
        ll = cell["long_leg"]
        assert ll["gross_annual_fraction"] is not None, f"{key} 没有读数"
        assert ll["mde_annual_fraction"] is not None, f"{key} 没有 MDE"
        assert cell["n_pairs"] >= 3, f"{key} 配对日 {cell['n_pairs']} < 3"
        assert ll["t"] is not None, f"{key} 没有 t"


def test_each_cells_reading_is_bit_equal_to_the_artifact_field():
    """**读数逐位等于产物字段**（第 109 层：不用公式重算当验证）。

    ⇒ 钉的是「产物里报出来的数」与「产物里存着的数」**逐位相同**：
    * `*_pct` 必须**恰好**是 `*_fraction × 100`（不是四舍五入、不是另一个换算）；
    * 阶梯表里的 `n=10` 那一行必须与头条读数**同一个数**（不是第二次计算）。
    """
    for key, cell in _cells().items():
        ll = cell["long_leg"]
        assert ll["gross_annual_pct"] == ll["gross_annual_fraction"] * 100.0, (
            f"{key}：百分数不是分数 ×100 逐位相等")
        assert ll["mde_annual_pct"] == ll["mde_annual_fraction"] * 100.0, (
            f"{key}：MDE 的百分数不是分数 ×100 逐位相等")
        assert cell["n_pairs"] == cell["leg_ladder"]["10"]["n"], (
            f"{key}：配对日数与阶梯表 n=10 那一行不一致")
        assert cell["leg_ladder"]["10"]["gross_annual_pct"] == \
            ll["gross_annual_fraction"], (
            f"{key}：阶梯表里的读数与头条读数**不是同一个数** ⇒ 有第二份副本")


def test_the_lineup_table_is_the_same_numbers_and_not_a_second_hand_typed_copy():
    """**并列表 = 同一批数**（第 143 层：手打副本会静默漂移）。"""
    cells = _cells()
    lineup = _artifact()["same_ruler_lineup"]
    seen = 0
    for fam in FAMILIES:
        for key, row in lineup[fam]["rows"].items():
            assert key in cells, f"并列表里的 {key} 在格子表里不存在"
            assert row["gross_annual_fraction"] == \
                cells[key]["long_leg"]["gross_annual_fraction"], (
                f"{key}：并列表与格子表**不是同一个数** ⇒ 有一份手打副本")
            assert row["gross_annual_pct"] == row["gross_annual_fraction"] * 100.0
            seen += 1
    assert seen == 8, f"并列表只覆盖了 {seen} 格（应为 8）"


def test_the_bm_control_arm_reproduces_the_published_reading_bit_for_bit():
    """**预注册 §5 C2**：同一台仪器必须复现已发表的 bm 负读数。

    ⇒ 钉的是**逐位相等**（`abs_diff == 0.0`），不是「在容差内」
    （容差判据对「值没变」的变异零信息量）。
    """
    bm = _artifact()["control_bm_reproduction"]
    assert bm["all_within_tolerance"] is True
    published = _arm_r()["family_a_cells"]
    for key, cell in bm["cells"].items():
        assert cell["abs_diff"] == 0.0, (
            f"{key}：bm 复现差 {cell['abs_diff']!r} ⇒ 仪器不是同一台")
        assert cell["reproduced_gross_annual_fraction"] == \
            cell["published_gross_annual_fraction"]
        assert cell["reproduced_gross_annual_fraction"] == \
            published[key]["long_leg"]["gross_annual_pct"], (
            f"{key}：与 round B 已发表的产物**不逐位相等**")
        assert cell["prereg_target_matches_published"] is True
        assert cell["n_pairs"] >= 3


def test_the_instrument_equivalence_check_is_not_vacuous():
    """**两份腿表实现必须逐位相等**（第 137 层：两份副本之间没有守卫）。

    ⇒ 非空性：比过的数必须够多（本轮 100 个），且每一档 `n_diffs == 0`。
    """
    eq = _artifact()["instrument_equivalence"]
    assert eq["bitwise_equal"] is True, "本轮腿表与 arm R 的不逐位相等"
    assert eq["factor"] == "bm"
    assert eq["n_values_compared"] >= 100, (
        f"只比了 {eq['n_values_compared']} 个数 ⇒ 判据可能退化成装饰")
    assert len(eq["rows"]) == 2
    for row in eq["rows"]:
        assert row["n_diffs"] == 0, f"K={row['k']} 有 {row['n_diffs']} 处不同"
        assert row["bitwise_equal"] is True
        assert row["n_ladder_levels"] == 5


# ════════════════════════════════════════════════════════════════════
# A 层 · PIT / 口径 / 倍数 / 措辞
# ════════════════════════════════════════════════════════════════════

def test_the_pit_assertion_has_zero_violations_over_a_real_sample():
    """**预注册 §3.2.4**：每个 (日,票) 用的报告必须 `NOTICE_DATE ≤ 当日`。

    ⇒ 非空性：`n_rows` 必须够大（否则「0 违规」是**空集上的真**）。
    """
    a = _artifact()["pit_check"]["assertion"]
    assert a["violations"] == 0, f"PIT 违规 {a['violations']} 条"
    assert a["n_rows"] >= 1000, f"PIT 只查了 {a['n_rows']} 行 ⇒ 判据是装饰"
    assert a["lag_days"]["min"] >= 0, "间隔分布里出现了负数"


def test_the_aplus_cross_check_reports_its_differences_instead_of_hiding_them():
    """**预注册 §3.2.5**：与 aplus 独立实现的对拍，差异**必须报出来**。

    ⇒ 守卫不是「差异必须为 0」（那是要求两套实现**逐位相同**，
    而它们在「同一披露日多份报告期」的 tie-break 上**本来**就可能不同），
    而是「**差异必须被报出来**」——不许藏（第 143 层）。
    """
    cross = _artifact()["pit_check"]["aplus_cross_check"]
    rows = cross["probe_dates"]
    assert len(rows) >= 5, f"只对拍了 {len(rows)} 个探针日"
    for r in rows:
        assert r["n_mine"] > 0 and r["n_aplus"] > 0
        # 差异条数与差在哪，必须两路都对得上
        assert r["n_report_date_diffs"] == len(r["report_date_diffs"]) or \
            r["n_report_date_diffs"] > 0
        # 选中的**股票集合**必须一致（可见性一致）；只有报告期可能不同
        assert not r["only_in_mine"], (
            f"{r['as_of']}：本轮多选了 {r['only_in_mine']} ⇒ 可见性判据不同")
        assert not r["only_in_aplus"], (
            f"{r['as_of']}：aplus 多选了 {r['only_in_aplus']} ⇒ 可见性判据不同")


def test_the_caliber_labels_are_not_swapped():
    """**实盘档 vs 判决臂**不许互换（第 7 层 / 第 143 层）。"""
    art = _artifact()
    cal = art["caliber"]
    assert "判决臂" in cal["this_arm"], "本轮这一臂没标成判决臂"
    assert "交付档" not in cal["this_arm"], "本轮这一臂被标成了交付档"
    assert "交付档" in cal["baseline_arm"], "基线那一档没标成交付档"
    assert "判决臂" not in cal["baseline_arm"], "基线那一档被标成了判决臂"

    cmp_ = art["baseline_comparison"]
    assert cmp_["delivered_price_arm"]["is_the_baseline_for_gap"] is True
    assert cmp_["arm_r_readings_are_not_same_caliber"]["caliber_match"] is False
    assert cmp_["arm_r_readings_are_not_same_caliber"]["differences"], \
        "跨口径的差异清单是空的 ⇒ 读者无从判断能不能比"


def test_the_delivered_baseline_is_bit_equal_to_the_live_deployed_artifact():
    """**基线必须逐位等于现役产物字段**（第 109 层：不许重算）。"""
    cell = (_deployed()["k_grid"]["cells"]
            ["LIVE_DEFAULT|ARM_A|K63|E100"])
    live = cell["k_axis"]["gross_excess_annual_pct"]
    got = _artifact()["baseline_comparison"]["delivered_price_arm"][
        "gross_annual_pct"]
    assert got == live, f"基线 {got!r} ≠ 现役产物 {live!r}"
    # 预注册 §7 引的是**另一条路径**（`per_config["S1_A10_K63"]`）⇒
    # 两条路径必须**逐位相等**（否则「引用了同一个数」这句话是假的）
    per_cfg = _deployed()["k_grid"]["per_config"]["S1_A10_K63"]
    assert per_cfg["gross_excess_annual_pct"] == live, (
        "交付档的两条引用路径给出不同的数 ⇒ 有一处引用是错的")


def test_the_gap_multiple_is_the_same_caliber_one_and_never_the_cross_caliber_one():
    """🚨🚨 **跨口径的 2.3450× 不是缺口**（台账自报 `is_the_gap = false`）。

    ⇒ 三条同时钉：
    ① 报出来的倍数**逐位**等于台账的 `same_caliber.multiple_needed`；
    ② `is_the_gap` 为真；
    ③ **跨口径那个数不许出现在产物的任何地方**
    （否则读者会顺手引用它 —— 第 143 层：表格是唯一会被查的地方）。
    """
    art = _artifact()
    mc = _ledger()["headline"]["multiple_needed_at_50k_deliverable_by_caliber"]
    same = mc["same_caliber"]["multiple_needed"]
    cross = mc["cross_caliber"]["multiple_needed"]

    block = art["baseline_comparison"]["same_caliber_gap_multiple"]
    assert block["value"] == same, f"倍数 {block['value']!r} ≠ 同口径 {same!r}"
    assert block["is_the_gap"] is True

    hits = _numeric_values_equal_to(art, cross)
    assert hits == [], (
        f"跨口径倍数 {cross!r} 出现在产物的 {hits} ⇒ 会被当成缺口引用")


def test_the_required_gross_is_read_from_the_ledger_in_both_units():
    """**单位陷阱**（第 182 层）：headline 是百分数、rows 是分数。"""
    led = _ledger()
    pct = led["headline"]["required_gross_at_50k_deliverable"]
    row = next(r for r in led["rows"] if r.get("n") == 10)
    frac = row["required_gross_at_50k"]
    block = _artifact()["baseline_comparison"]["required_gross_at_50k_deliverable"]
    assert block["percent"] == pct
    assert block["fraction"] == frac
    assert block["fraction"] * 100.0 == pct


def test_each_cells_thresholds_are_the_ledger_values_for_its_own_k():
    """**K=21 与 K=63 的门槛不许互换**（第 7 层：K 不同 ⇒ 门槛不同）。

    ⇒ 门槛**从产物读**（`scripts/measure_fundamental_mechanism_armR.py::
    _threshold_sets`），守卫也用同一个来源重读一次做**逐位**对账。
    """
    from scripts.measure_fundamental_mechanism_armR import _threshold_sets
    thr = _threshold_sets(ROOT / "data/reports")
    expect = {21: thr["primary"], 63: thr["control"]}
    for key, cell in _cells().items():
        k = cell["k"]
        assert k in expect, f"{key} 的 K={k} 不在判决的 K 档里"
        t = cell["thresholds"]
        assert t["d1_friction_annual_pct"] == \
            expect[k]["d1_friction_annual_pct"], f"{key} 的 D1 不是 K={k} 的那个"
        assert t["d2_required_gross_annual_pct"] == \
            expect[k]["d2_required_gross_annual_pct"], f"{key} 的 D2 不是 K={k} 的那个"


def test_powerless_is_written_as_undetectable_and_never_as_useless():
    """**「测不到」≠「无效」**（第 16 层 / 第 160 层）。

    🚨 失效方向是**乐观**的：把「仪器看不清」写成「东西不行」
    ⇒ 会误导人放弃「加数据」这条**唯一能解锁判决**的路。
    """
    forbidden = ("无效", "已证伪", "证明没有", "没有超额收益")
    for key, cell in _cells().items():
        v = cell["verdict"]
        if v["state"].endswith("_POWERLESS"):
            assert v["wording"] == M.NOT_MEASURED_WORDING, (
                f"{key} 的 POWERLESS 没带「测不到」的措辞")
            assert v["shape_only"] is True, f"{key} 的 POWERLESS 不是形状陈述"
            assert "effect_over_mde" in v
        for field in ("reason", "wording"):
            text = v.get(field) or ""
            for w in forbidden:
                assert w not in text, f"{key} 的 {field} 里出现了「{w}」"


def test_the_negative_control_clause_b_literal_reading_is_disclosed():
    """🚨 **两个读法的差必须写出来**（不藏）。

    ⇒ 预注册 §5 C1 第二句「负控毛超额不得高于原臂」**没有**显著性门槛
    ⇒ 按**字面**执行 ⇒ 违反它的格子会让**整套判决作废**。
    ⇒ 本产物只在负控**显著**时作废 ⇒ 两个读法结论可能不同
    ⇒ 这个差必须**逐格列出来**（否则读者只能看到「没作废」，
    而不知道「按字面读法就该作废」）。
    """
    blk = _artifact()["verdict"]["primary"]["clause_b_literal_reading"]
    computed = sorted(k for k, v in _cells().items()
                      if v["negative_control"]["clause_b_satisfied"] is False)
    assert blk["cells_violating_clause_b"] == computed, (
        f"字面读法的违规清单与逐格标志对不上：{blk['cells_violating_clause_b']}"
        f" vs {computed}")
    assert blk["n_cells_violating"] == len(computed)
    assert "显著性门槛" in blk["what_would_happen"]
    assert "作废" in blk["what_would_happen"]


def test_the_full_pool_arm_is_still_not_constructible_in_the_delivery_window():
    """**本轮为什么没跑全池臂** —— 这条理由必须**还能被复核**（不是一句散文）。

    ⇒ 预注册 §3.3：全池 PIT 财务覆盖**在 2024-07 才有**
    ⇒ 交付窗口 2021-01-01→2026-10-12 的**全池财务臂不可构造**
    ⇒ 现场**重算**（读原始 vintage parquet，不是读产物）⇒ 窗口起点的
    可见年报只有**几十只**，离 5,460 差两个数量级
    ⇒ 若哪天这个前提不成立了，**守卫会喊**（那时全池臂才可构造）
    """
    vroot = ROOT / "data/reports/financial_vintage"
    snaps = sorted(p for p in vroot.glob("date=*") if (p / "financial.parquet").exists())
    if not snaps:
        pytest.skip("financial_vintage 快照不在本地（不入库）⇒ 前提没法复核")
    raw = pd.read_parquet(snaps[-1] / "financial.parquet")
    ann = raw[raw["REPORT_TYPE"].astype(str).str.strip() == "年报"].copy()
    ann["nd"] = pd.to_datetime(ann["NOTICE_DATE"], errors="coerce")
    ann["code"] = ann["SECURITY_CODE"].astype(str).str.zfill(6)
    n_start = int(ann.loc[ann["nd"] <= pd.Timestamp("2021-01-04"), "code"].nunique())
    n_end = int(ann.loc[ann["nd"] <= pd.Timestamp("2026-10-12"), "code"].nunique())
    assert n_start < 100, (
        f"交付窗口起点的可见年报已有 {n_start} 只 ⇒ 全池臂**可能**可构造 ⇒ "
        f"预注册 §3.3 的前提需要重评（不是守卫坏了）")
    assert n_end >= 5000, f"快照末期只 {n_end} 只 ⇒ 快照本身有问题"

    scope = _artifact()["scope"]
    assert "全池" in scope["arm_not_run"], "产物没写清「全池臂没跑」"
    assert "2024-07" in scope["arm_not_run"], "产物没写清「为什么没跑」"
    assert "全池" in scope["arm_run"] or "500" in scope["arm_run"]


def test_the_artifact_carries_every_honest_boundary_the_prereg_listed():
    """**预注册 §9 的六条诚实边界**必须**逐条**落进产物（不许只留一半）。

    ⇒ 读的是产物的**正文块**（`what_this_cannot_prove` / `no_promotion` /
    `caliber` / `baseline_comparison`）⇒ 读者不必翻脚本就能看到边界。
    """
    art = _artifact()
    assert art["what_this_cannot_prove"], "产物没有诚实边界清单"
    assert art["no_promotion"], "产物没有「不晋级」的声明"
    txt = "\n".join(art["what_this_cannot_prove"]) + "\n" + art["no_promotion"]
    for kw, why in (
        ("后修值", "① 财务源是后修值"),
        ("跨口径", "④ 与基线的比较跨口径"),
        ("账户腿", "本轮没有账户腿"),
        ("全池", "⑤ 没有全池臂"),
        ("20%", "⑥ 回答不了「20% 够不够」"),
    ):
        assert kw in txt, f"诚实边界缺了：{why}（关键词 {kw!r}）"
    assert art["caliber"]["account_leg"], "口径块没写「没有账户腿」"
    diffs = art["baseline_comparison"]["arm_r_readings_are_not_same_caliber"][
        "differences"]
    assert len(diffs) >= 5, f"跨口径差异清单只有 {len(diffs)} 条 ⇒ 不够具体"


def test_the_vendor_growth_columns_are_verified_as_percent_by_recomputation():
    """🚨 **列名不承诺语义**（第 159 层）：厂商那一列是**百分数**还是分数，
    光看名字定不了 ⇒ 必须**逐行反算**证明。

    ⇒ 外部锚：拿**基期金额**与**本期金额**重算厂商那一列 ⇒ 匹配率必须
    接近 1（且样本量够大）⇒ 才敢说「它是百分数」。
    ⇒ 而这正是「**量纲必须由产物自己声明**」（第 182 层）。
    """
    ver = _artifact()["factor_definition_verification"]
    defs = _artifact()["factor_definitions"]
    for f in ("g_profit", "g_rev", "cf_growth"):
        assert f in ver, f"{f} 没有量纲核验"
        row = ver[f]
        assert row["n_rows"] >= 1000, (
            f"{f} 只核了 {row['n_rows']} 行 ⇒ 判据是装饰")
        assert row["match_rate_with_rel_tol_1e-9"] >= 0.999, (
            f"{f} 的厂商公式匹配率 {row['match_rate_with_rel_tol_1e-9']} "
            f"⇒ 列的含义与声明不符")
        assert "百分数" in defs[f]["unit"], (
            f"{f} 的产物没声明它是百分数 ⇒ 量纲只存在于散文里")
        assert defs[f]["vendor_formula"], f"{f} 没写厂商公式"
        assert "|基期|" in row["formula_tested"], (
            "核验用的公式里没有绝对值分母 ⇒ 上年亏损时的符号约定没被验")
        assert M.GC_FACTOR_SPECS[f]["unit"] == defs[f]["unit"], (
            f"{f}：代码里的量纲声明与产物里的不一致")


def test_the_code_recomputes_the_vendor_growth_formula():
    """**行为层**：直接问**代码**（产物层读的是上一次落盘 ⇒ 看不见源码改动）。

    ⚠️ 需要 `financial_annual_cache.parquet`，而它**不入库**
    （`.gitignore` 的「只把 JSON 结论入库」）⇒ 换台机器上**没有**这个文件
    ⇒ 按仓内惯例 `skipif`（**不是**假装通过）。
    """
    cache = ROOT / "data/reports/financial_annual_cache.parquet"
    if not cache.is_file():
        pytest.skip(f"财务缓存不在本地（不入库）：{cache}")
    live = M.verify_vendor_growth_columns(cache)
    ver = _artifact()["factor_definition_verification"]
    for f in ("g_profit", "g_rev", "cf_growth"):
        assert live[f]["match_rate_with_rel_tol_1e-9"] >= 0.999, (
            f"{f} 的厂商公式在**当前代码**下匹配率 "
            f"{live[f]['match_rate_with_rel_tol_1e-9']} ⇒ 公式被改过")
        assert live[f] == ver[f], (
            f"{f}：代码算出来的量纲核验与产物里的**不是同一个东西** ⇒ "
            f"产物不是这份代码造的（或代码改过没重跑）")


def test_the_artifact_points_at_a_prereg_that_lists_the_four_factors():
    """**产物必须指向一份真存在的预注册**（第 22 层：注释里的引用也可能悬空）。"""
    art = _artifact()
    assert art["prereg"] == "docs/PREREG_GROWTH_CASHFLOW_ARM_2026-10-21.md"
    assert PREREG.is_file(), "预注册文档不存在"
    text = PREREG.read_text(encoding="utf-8")
    for f in FACTORS:
        assert f"`{f}`" in text, f"预注册里没有因子 {f}"


# ════════════════════════════════════════════════════════════════════
# B 层 · 行为层：合成夹具上钉判决链（源码敏感）
# ════════════════════════════════════════════════════════════════════

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


def test_the_leg_table_mirror_is_bit_equal_to_arm_r_on_synthetic_data():
    """🚨🚨 **两份腿表实现必须逐位相等**（第 137 层）。

    ⇒ 合成夹具上跑**同一个 bm** ⇒ 本轮的 `leg_table_signed` 与 arm R 的
    `_leg_table` 输出必须**逐位相等**（差 0.0）。
    ⇒ 这条守卫对「方向取反 / ddof 改 0 / MDE 乘 K / 年化用 244·K」都敏感。
    """
    from scripts.measure_fundamental_mechanism_armR import _leg_table
    xs, days, fwd = _synthetic_panel()
    for k in (21, 7):
        mine = M.leg_table_signed(xs, days, "bm", k, fwd, +1,
                                  ns=(10, 30, 500), n_a=8)
        ref = _leg_table(xs, days, "bm", k, fwd, ns=(10, 30, 500), n_a=8)
        assert set(mine) == set(ref)
        for n in mine:
            for fld in M._EQUIV_FIELDS:
                assert M._same_number(mine[n].get(fld), ref[n].get(fld)), (
                    f"K={k} n={n} {fld}: 本轮 {mine[n].get(fld)!r} ≠ "
                    f"arm R {ref[n].get(fld)!r}")


def test_the_equivalence_field_list_covers_every_numeric_field_of_the_leg_row():
    """**完整性守卫**：逐位对拍的字段清单不许漏项。

    🚨 漏一个字段 ⇒ 那个字段上的变异**永远活下来**，而台子只打 ✅
    （第 148 层：变异台的「存活者」是指向「哪一块没人测」的路标）。
    """
    xs, days, fwd = _synthetic_panel()
    row = M.leg_table_signed(xs, days, "bm", 21, fwd, +1, ns=(10,), n_a=8)["10"]
    numeric = {k for k, v in row.items()
               if isinstance(v, (int, float)) and not isinstance(v, bool)}
    assert numeric == set(M._EQUIV_FIELDS), (
        f"腿表行的数值字段 {sorted(numeric)} 与对拍清单 "
        f"{sorted(M._EQUIV_FIELDS)} 不一致 ⇒ 有字段没被逐位对拍")


def test_the_negative_control_voids_the_verdict_when_the_flipped_arm_is_better():
    """**预注册 §5 C1 第二句**：负控显著更好 ⇒ 整套判决作废。

    🚨 守卫钉的是**行为**（给定输入必须判 VOID），
    不是「产物里那个 state 字段写着什么」（读快照的守卫对源码零覆盖）。
    """
    st = M.nc_state(gross_nc=+0.08, t_nc=3.0, t_crit=2.0, gross_orig=-0.05)
    assert st["state"] == "VOID", f"负控显著更好却没作废：{st}"
    assert st["better_than_original"] is True
    # 负控显著**为正**（即便它比原臂差）⇒ 同样作废（预注册 §5 C1 显式箭头）
    st_pos = M.nc_state(gross_nc=+0.08, t_nc=3.0, t_crit=2.0,
                         gross_orig=+0.20)
    assert st_pos["state"] == "VOID", (
        f"负控显著为正却没作废：{st_pos}")
    assert st_pos["significantly_positive"] is True
    # 🚨🚨 **这一格是「只有第二支会触发」的那一格**：
    #   负控显著（|t| ≥ t_crit）**但为负**（⇒ 第一支「显著为正」不触发）
    #   **同时**比原臂好（−0.02 > −0.05 ⇒ nc_better）
    #   ⇒ 只有「显著更好 ⇒ 作废」那一支能判它作废
    # ⇒ 少了这一格，摘掉第二支（`if False and nc_better`）**全绿**
    #   ⇒ 变异台实测：这一格是 M2 唯一的杀点（第 148 层：存活者是指向
    #   「哪一块没人测」的路标 —— 这里**确实**没人测过这一格）。
    st_neg_better = M.nc_state(gross_nc=-0.02, t_nc=3.0, t_crit=2.0,
                               gross_orig=-0.05)
    assert st_neg_better["state"] == "VOID", (
        f"负控显著**且比原臂好**（尽管为负）却没作废：{st_neg_better}")
    assert st_neg_better["significantly_positive"] is False
    assert st_neg_better["better_than_original"] is True
    # 对照：负控不显著 ⇒ 不作废（否则纯噪声会有一半概率作废）
    st2 = M.nc_state(gross_nc=+0.08, t_nc=1.0, t_crit=2.0, gross_orig=-0.05)
    assert st2["state"] == "PASS"
    # 对照：负控显著但**更差** ⇒ 不作废
    st3 = M.nc_state(gross_nc=-0.08, t_nc=3.0, t_crit=2.0, gross_orig=-0.05)
    assert st3["state"] == "PASS"


def test_the_negative_control_boundary_is_inclusive_at_the_critical_value():
    """|t| **恰好等于**临界值 ⇒ 算显著（闸门是 `>=`，不是 `>`）。

    🚨 差一个 ulp 就换判 ⇒ 边界必须被钉住（第 190 层：
    「测度零点」结构失明 ⇒ 连续采样永远采不到它）。
    """
    st = M.nc_state(gross_nc=+0.01, t_nc=2.0, t_crit=2.0, gross_orig=-0.05)
    assert st["state"] == "VOID", "|t| == t_crit 必须算显著"
    st2 = M.nc_state(gross_nc=+0.01, t_nc=1.999999, t_crit=2.0,
                     gross_orig=-0.05)
    assert st2["state"] == "PASS"


def test_the_mde_gate_is_judged_before_the_significance_gate():
    """**预注册 §6.1**：MDE 闸**先判**。

    🚨 顺序本身是判据：|t| 很大但 |毛超额| < MDE ⇒ 判「测不到」，
    **不许**跳到显著性那一支去喊「显著」。
    """
    v = M.verdict_for_leg(gross=0.05, mde=0.10, t=5.0, t_crit=2.0,
                          n_pairs=50, d1=0.01, d2=0.02, prefix="GROWTH")
    assert v["state"] == "GROWTH_POWERLESS", (
        f"MDE 闸没先判：{v['state']}")
    assert v["shape_only"] is True
    # 比值 0.9（离 1 还有余量）⇒ 同样是「测不到」
    v09 = M.verdict_for_leg(gross=0.09, mde=0.10, t=5.0, t_crit=2.0,
                            n_pairs=50, d1=0.01, d2=0.02, prefix="GROWTH")
    assert v09["state"] == "GROWTH_POWERLESS", v09["state"]
    # 对照：|毛超额| ≥ MDE ⇒ 走显著性那一支
    v2 = M.verdict_for_leg(gross=0.05, mde=0.01, t=5.0, t_crit=2.0,
                           n_pairs=50, d1=0.01, d2=0.02, prefix="GROWTH")
    assert v2["state"] == "GROWTH_LEVER"
    # 对照：刚过显著性线（|t| 略高于临界）⇒ 过线
    v3 = M.verdict_for_leg(gross=0.05, mde=0.01, t=2.5, t_crit=2.0,
                           n_pairs=50, d1=0.01, d2=0.02, prefix="GROWTH")
    assert v3["state"] == "GROWTH_LEVER", v3["state"]


def test_the_delivery_thresholds_are_compared_strictly():
    """`gross > d1` 是**严格大于** ⇒ 恰好等于 D1 时**不算过**。"""
    v = M.verdict_for_leg(gross=0.05, mde=0.01, t=5.0, t_crit=2.0,
                          n_pairs=50, d1=0.05, d2=0.01, prefix="GROWTH")
    assert v["state"] == "GROWTH_SIG_NOT_DELIVERABLE", (
        f"恰好等于 D1 却被判过线：{v['state']}")
    assert v["d1_pass"] is False and v["d2_pass"] is True
    # D2 那一侧同样是**严格大于**
    v2 = M.verdict_for_leg(gross=0.05, mde=0.01, t=5.0, t_crit=2.0,
                           n_pairs=50, d1=0.01, d2=0.05, prefix="GROWTH")
    assert v2["state"] == "GROWTH_SIG_NOT_DELIVERABLE", (
        f"恰好等于 D2 却被判过线：{v2['state']}")
    assert v2["d1_pass"] is True and v2["d2_pass"] is False


def test_a_missing_mde_is_not_measured_and_never_crashes():
    """**读不出来 ⇒ 不报数**（不许回落成 0，也不许崩）。"""
    v = M.verdict_for_leg(gross=0.05, mde=None, t=5.0, t_crit=2.0,
                          n_pairs=50, d1=0.01, d2=0.02, prefix="GROWTH")
    assert v["state"] == "GROWTH_NOT_MEASURED", v
    v2 = M.verdict_for_leg(gross=None, mde=0.01, t=5.0, t_crit=2.0,
                           n_pairs=50, d1=0.01, d2=0.02, prefix="GROWTH")
    assert v2["state"] == "GROWTH_NOT_MEASURED", v2
    # 配对日不足 ⇒ 结构闸，不报数
    v3 = M.verdict_for_leg(gross=0.05, mde=0.01, t=5.0, t_crit=2.0,
                           n_pairs=2, d1=0.01, d2=0.02, prefix="GROWTH")
    assert v3["state"] == "GROWTH_NOT_MEASURED", v3


def test_the_number_comparison_is_bitwise_and_not_a_tolerance():
    """**逐位相等**（不是「在容差内」）⇒ 容差判据对「值没变」的变异零信息量。"""
    assert M._same_number(1.0, 1.0) is True
    assert M._same_number(None, None) is True
    assert M._same_number(1.0, None) is False
    assert M._same_number(1.0, math.nextafter(1.0, 2.0)) is False, \
        "差一个 ulp 竟被判相等 ⇒ 判据被放松成容差"
    assert M._same_number(float("nan"), float("nan")) is True


def test_a_missing_delivery_threshold_refuses_to_give_a_verdict():
    """**缺档必须报错，绝不回落**（仓内铁律）。"""
    with pytest.raises(ValueError):
        M.verdict_for_leg(gross=0.05, mde=0.01, t=5.0, t_crit=2.0,
                          n_pairs=50, d1=None, d2=0.02, prefix="GROWTH")
    with pytest.raises(ValueError):
        M.verdict_for_leg(gross=0.05, mde=0.01, t=5.0, t_crit=2.0,
                          n_pairs=50, d1=0.02, d2=None, prefix="GROWTH")


def test_the_family_state_counts_the_cells_instead_of_taking_an_extremum():
    """**族级标签 = 逐格分布**（第 143 层：跨格取极值会抹掉它从哪一行来）。

    🚨 一格 LEVER + 一格 POWERLESS ⇒ 分布标签是「部分过线」，
    **不是** LEVER（取极值会把「只有一格过」说成「整族过」）。
    """
    cells = {
        "a@K21": {"verdict": {"state": "GROWTH_POWERLESS"}},
        "b@K21": {"verdict": {"state": "GROWTH_LEVER"}},
    }
    s = M.family_state(cells, "GROWTH")
    assert s["state"] == "GROWTH_SIG_NOT_DELIVERABLE", s
    assert s["state_counts"] == {"GROWTH_POWERLESS": 1, "GROWTH_LEVER": 1}
    assert s["cells_pass_family_a"] == 1
    assert s["n_cells"] == 2
    # 全 POWERLESS ⇒ 才是 POWERLESS
    allp = {k: {"verdict": {"state": "CASHFLOW_POWERLESS"}} for k in ("a", "b")}
    assert M.family_state(allp, "CASHFLOW")["state"] == "CASHFLOW_POWERLESS"
    # 🚨🚨 **这一格是「全 POWERLESS 那一支」唯一的杀点**：
    #   一格 POWERLESS + 一格 NOT_MEASURED ⇒ 分布**不是**全 POWERLESS
    #   ⇒ 族级标签必须是 NOT_MEASURED
    # ⇒ 少了这一格，把 `all` 改成 `any`（取极值）**全绿**
    #   ⇒ 变异台实测：这一格是 M20 唯一的杀点。
    mixed = {"a": {"verdict": {"state": "CASHFLOW_POWERLESS"}},
             "b": {"verdict": {"state": "CASHFLOW_NOT_MEASURED"}}}
    assert M.family_state(mixed, "CASHFLOW")["state"] == "CASHFLOW_NOT_MEASURED"


def test_the_pit_assertion_catches_a_future_dated_report():
    """**能力守卫**：造一条 `NOTICE_DATE > 当日` 的行 ⇒ 必须报出违规。

    🚨 没有这一条，「违规数 = 0」可能只是**断言恒真**（第 178 层）。
    """
    frame = pd.DataFrame([
        {"code": "000001.SZ", "day": pd.Timestamp("2020-06-30"),
         "notice_date": pd.Timestamp("2020-07-15"),
         "report_date": pd.Timestamp("2019-12-31")},
    ])
    r = M.pit_violations(frame)
    assert r["violations"] == 1, f"未来的报告没被抓住：{r}"
    assert r["n_rows"] == 1
    assert r["violation_examples"], "违规样本没被列出来"


def test_the_pit_assertion_does_not_treat_a_same_day_disclosure_as_a_violation():
    """**当日披露合法** ⇒ `lag == 0` **不是**违规（`<` 不是 `<=`）。"""
    frame = pd.DataFrame([
        {"code": "000001.SZ", "day": pd.Timestamp("2020-06-30"),
         "notice_date": pd.Timestamp("2020-06-30"),
         "report_date": pd.Timestamp("2019-12-31")},
    ])
    r = M.pit_violations(frame)
    assert r["violations"] == 0, f"当日披露被判成违规：{r}"
    assert r["lag_days"]["min"] == 0


def test_the_cash_flow_accrual_factor_masks_a_non_positive_denominator():
    """**预注册 §2**：分母 ≤ 0 ⇒ 该 (日,票) 缺失（不裁剪、不加 epsilon）。"""
    m = pd.DataFrame({
        "PARENTNETPROFIT": [100.0, 100.0, 100.0],
        "KCFJCXSYJLR": [40.0, 40.0, 40.0],
        "TOTALOPERATEREVE": [1000.0, 0.0, -500.0],
        "PARENTNETPROFITTZ": [0.1, 0.1, 0.1],
        "TOTALOPERATEREVETZ": [0.2, 0.2, 0.2],
        "KCFJCXSYJLRTZ": [0.3, 0.3, 0.3],
    })
    out = M.factor_values_gc(m)
    assert out["cf_accrual"][0] == 60.0 / 1000.0
    assert pd.isna(out["cf_accrual"][1]), "分母 = 0 竟有值"
    assert pd.isna(out["cf_accrual"][2]), "分母 < 0 竟有值"


def test_the_growth_factors_are_the_vendor_growth_rates_verbatim():
    """**三个成长率因子就是厂商那一列**，不许自己重算一遍。"""
    m = pd.DataFrame({
        "PARENTNETPROFIT": [100.0], "KCFJCXSYJLR": [40.0],
        "TOTALOPERATEREVE": [1000.0],
        "PARENTNETPROFITTZ": [0.123], "TOTALOPERATEREVETZ": [-0.456],
        "KCFJCXSYJLRTZ": [0.789],
    })
    out = M.factor_values_gc(m)
    assert out["g_profit"][0] == 0.123
    assert out["g_rev"][0] == -0.456
    assert out["cf_growth"][0] == 0.789


def test_adding_the_extra_columns_never_changes_the_row_set(tmp_path):
    """**「只加列不改行」**（预注册 §3.2.2）：加列后行数必须一模一样。

    🚨 这条同时是「join 键写错」的探测器：键一改，行数就变 ⇒ 当场红。
    """
    cache = _synthetic_financial_cache(tmp_path)
    spine = M.annual_series_from_cache(cache)
    ext = M.annual_series_extended(cache)
    assert len(ext) == len(spine), "加列后行数变了 ⇒ 骨架被破坏"
    assert ext["code"].nunique() == spine["code"].nunique()
    for col in ("PARENTNETPROFITTZ", "TOTALOPERATEREVE"):
        assert col in ext.columns
    # 值必须接对了行（不是接错行）：按 code 逐只核对
    by_code = {c: sorted(g["PARENTNETPROFITTZ"].tolist())
               for c, g in ext.groupby("code")}
    for idx, code in enumerate(("001234", "000005", "000006")):
        want = 0.1 + idx
        assert by_code[code] == [want, want], (
            f"{code} 的额外列接到了错误的行：{by_code[code]}")


def test_the_extra_columns_frame_normalizes_a_short_security_code(tmp_path):
    """**裸 6 位码**：短码必须补零到 6 位（`str.zfill(6)`）。"""
    cache = _synthetic_financial_cache(tmp_path)
    extra = M.extra_columns_frame(cache)
    assert set(extra["code"]) == {"001234", "000005", "000006"}, \
        sorted(set(extra["code"]))


def _synthetic_financial_cache(tmp_path) -> Path:
    """造一份**最小**的财务缓存（3 只 × 2 个报告期 + 1 只被闸剔掉的）。"""
    rows = []
    for i, code in enumerate(("1234", "5", "000006")):
        for k, (rd, nd) in enumerate((("2018-12-31", "2019-04-20"),
                                       ("2017-12-31", "2018-04-20"))):
            rows.append({
                "SECURITY_CODE": code, "REPORT_DATE": rd, "NOTICE_DATE": nd,
                "REPORT_TYPE": "年报", "BPS": 1.0 + i, "EPSJB": 0.5,
                "ROEJQ": 0.1, "MGJYXJJE": 0.2,
                "PARENTNETPROFITTZ": 0.1 + (i % 3),
                "TOTALOPERATEREVETZ": 0.2,
                "PARENTNETPROFIT": 100.0 + i, "KCFJCXSYJLR": 40.0,
                "TOTALOPERATEREVE": 1000.0, "KCFJCXSYJLRTZ": 0.3,
            })
    df = pd.DataFrame(rows)
    p = tmp_path / "fin.parquet"
    df.to_parquet(p, index=False)
    return p


# ════════════════════════════════════════════════════════════════════
# B 层 · 行为层：产物装配（口径标签 / 倍数 / 读数来源）
# ════════════════════════════════════════════════════════════════════

def _synthetic_ladder(gross=0.05, mde=0.01, t=5.0, t_crit=2.0, n=50):
    row = {"n": n, "mean_per_k": gross * 21.0 / 244.0,
           "gross_annual_pct": gross, "sd_per_k": 0.02, "t": t,
           "t_crit": t_crit, "mde_per_k": mde * 21.0 / 244.0,
           "mde_annual_pct": mde, "long_leg_annual_pct": 0.08,
           "market_leg_annual_pct": 0.03, "note": "x"}
    return {"10": row, "30": dict(row, n=30)}


def test_the_cell_headline_reading_comes_from_the_original_leg_not_the_flipped_one():
    """🚨🚨 **头条读数必须来自原臂**，负控读数必须来自**翻号臂**。

    ⇒ 装反了的话，产物里「这个因子赚多少」会变成「翻过来赚多少」
    ⇒ 而**数字全对、判决全对**，只是方向反了（第 143 层）。
    """
    spec = {"family": "GROWTH"}
    ladder = _synthetic_ladder(gross=0.05)
    fladder = _synthetic_ladder(gross=-0.20, t=-1.0)
    tset = {"d1_friction_annual_pct": 0.02, "d2_required_gross_annual_pct": 0.02}
    cell = M.cell_payload("g_profit", spec, 21, ladder, fladder, tset)
    assert cell["long_leg"]["gross_annual_fraction"] == 0.05, \
        "头条读数取自了**翻号臂**"
    assert cell["long_leg"]["gross_annual_pct"] == 5.0
    assert cell["negative_control_leg"]["gross_annual_fraction"] == -0.20, \
        "负控读数取自了**原臂**"
    assert cell["negative_control_leg"]["sign_flipped"] is True
    # 负控的三个原始数与「不藏」的条款必须**写出来**（读者可按自己的口径重判）
    assert cell["negative_control"]["clause_b_note"], "负控条款被抹掉了"
    assert "不藏" in cell["negative_control"]["clause_b_note"]
    assert "clause_b_satisfied" in cell["negative_control"]
    assert cell["negative_control"]["gross_nc"] == -0.20
    # 阶梯表的 n=10 那一行与头条**必须是同一个数**
    assert cell["leg_ladder"]["10"]["gross_annual_pct"] == \
        cell["long_leg"]["gross_annual_fraction"]
    assert cell["n_pairs"] == cell["leg_ladder"]["10"]["n"]
    # 判决必须挂在**原臂**上（|t| = 5 过线 ⇒ LEVER）
    assert cell["verdict"]["state"] == "GROWTH_LEVER", cell["verdict"]


def test_the_cell_payload_carries_the_thresholds_it_was_given():
    """**K=21 与 K=63 的门槛不许串** ⇒ 装配层必须原样带着。"""
    spec = {"family": "CASHFLOW"}
    tset = {"d1_friction_annual_pct": 0.021, "d2_required_gross_annual_pct": 0.112}
    cell = M.cell_payload("cf_growth", spec, 63, _synthetic_ladder(),
                          _synthetic_ladder(gross=-0.01), tset)
    assert cell["thresholds"] == tset
    assert cell["k"] == 63
    assert cell["sign"] == M.LONG_SIGN["cf_growth"]


def test_the_caliber_labels_are_not_swapped_inside_the_block():
    """**实盘档 vs 判决臂**不许互换（行为层，不只是读产物）。"""
    cal = M.caliber_block()
    assert "判决臂" in cal["this_arm"]
    assert "交付档" not in cal["this_arm"]
    assert "交付档" in cal["baseline_arm"]
    assert "判决臂" not in cal["baseline_arm"]
    assert "**不可**与交付档的净值/CAGR 直接比大小" in cal["account_leg"]


def test_the_baseline_block_picks_the_same_caliber_multiple_and_never_the_cross_one():
    """🚨🚨 **跨口径的 2.3450× 不是缺口**（行为层：函数自己挑哪一列）。"""
    mc = {
        "same_caliber": {"multiple_needed": 1.7897265643606735,
                         "is_the_gap": True},
        "cross_caliber": {"multiple_needed": 2.3450157528864404,
                          "is_the_gap": False},
    }
    req = {"percent": 11.281298415805717, "fraction": 0.11281298415805717}
    dep = {"gross_annual_pct": 6.303364234768245}
    blk = M.baseline_block(dep, req, mc)
    assert blk["same_caliber_gap_multiple"]["value"] == \
        mc["same_caliber"]["multiple_needed"]
    assert blk["same_caliber_gap_multiple"]["value"] != \
        mc["cross_caliber"]["multiple_needed"], "取成了**跨口径**那一列"
    assert blk["same_caliber_gap_multiple"]["is_the_gap"] is True
    assert blk["arm_r_readings_are_not_same_caliber"]["caliber_match"] is False
    assert blk["delivered_price_arm"]["gross_annual_pct"] == \
        dep["gross_annual_pct"]
    assert blk["required_gross_at_50k_deliverable"]["percent"] == req["percent"]


def test_the_instrument_equivalence_check_really_calls_both_leg_tables():
    """🚨 **AST 接线守卫**：逐位对拍必须**同时**调本轮的腿表与 arm R 的腿表。

    ⇒ 否则「把 `mine` 换成 `ref`」⇒ 对拍恒真 ⇒ 而**没有任何东西会红**
    （第 137 层：两份副本之间没有守卫）。
    """
    import ast
    src = _SCRIPT.read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and \
                node.name == "instrument_equivalence_check":
            fn = node
            break
    assert fn is not None, "找不到 instrument_equivalence_check"
    called = {n.func.id for n in ast.walk(fn)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "leg_table_signed" in called, \
        f"逐位对拍没有调本轮的腿表（调了 {sorted(called)}）"
    assert "_leg_table" in called, \
        f"逐位对拍没有调 arm R 的腿表（调了 {sorted(called)}）"


# ════════════════════════════════════════════════════════════════════
# C 层 · 预注册层：因子集与方向必须与文档一致（第 145 层）
# ════════════════════════════════════════════════════════════════════

def _prereg_rows() -> dict:
    """解析预注册 §2 的因子表 ⇒ {名字: (方向文字, 整行原文)}。"""
    out = {}
    for line in PREREG.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| **F"):
            continue
        cells = [c.strip() for c in line.split("|")]
        if len(cells) < 6:
            continue
        m = re.search(r"`([a-z_]+)`", cells[2])
        if not m:
            continue
        out[m.group(1)] = (cells[4], line)
    return out


def test_the_factor_set_and_directions_agree_with_the_prereg_document():
    """🚨🚨 **因子集必须是「声明」出来的，不是「探测」出来的**（第 145 层）。

    ⇒ 预注册钉死四个因子与方向；代码里的 `LONG_SIGN` 与 `GC_FACTOR_SPECS`
    必须与它**逐条**一致 ⇒ 改一个符号 / 加一个因子 ⇒ 当场红。
    """
    rows = _prereg_rows()
    assert set(rows) == set(FACTORS), (
        f"预注册里的因子集合是 {sorted(rows)}，与代码的 {sorted(FACTORS)} 不一致")
    for name, (direction_text, line) in rows.items():
        assert "越大越好" in direction_text or "越小越好" in direction_text, \
            f"{name} 的方向读不出来：{direction_text!r}"
        want = +1 if "越大越好" in direction_text else -1
        assert M.LONG_SIGN[name] == want, (
            f"{name} 的方向符号 {M.LONG_SIGN[name]} ≠ 预注册的 {want}")
        spec = M.GC_FACTOR_SPECS[name]
        assert spec["direction"] in ("越大越好", "越小越好"), spec["direction"]
        assert (1 if spec["direction"] == "越大越好" else -1) == want
        for col in spec["source_columns"]:
            assert col in line, (
                f"{name} 的源列 {col} 不在预注册那一行里 ⇒ "
                f"代码与预注册脱钩（第 91 层）")


def test_the_artifact_factor_definitions_agree_with_the_prereg_document():
    """**产物里报的因子定义**必须与预注册一致（第 145 层）。"""
    rows = _prereg_rows()
    defs = _artifact()["factor_definitions"]
    assert set(defs) == set(FACTORS)
    for name, (direction_text, line) in rows.items():
        d = defs[name]
        want = +1 if "越大越好" in direction_text else -1
        assert d["sign"] == want, f"{name} 的产物符号与预注册不一致"
        assert d["direction"] == ("越大越好" if want > 0 else "越小越好")
        assert d["long_leg_end"] == ("大的一端" if want > 0 else "小的一端")
        for col in d["source_columns"]:
            assert col in line, f"{name} 的源列 {col} 不在预注册那一行里"


def test_the_prereg_declares_the_measurement_caliber_and_the_artifact_matches():
    """**预注册里写死的口径**（窗口 / K 档 / 榜宽）必须与产物一致。

    🚨 判据变更（第 91 层）的最省事检测器：**文档与产物对不上**。
    """
    art = _artifact()
    text = PREREG.read_text(encoding="utf-8")
    assert art["window"] == ["2019-01-02", "2023-12-31"]
    assert "2019-01-02" in text and "2023-12-31" in text
    assert art["k_arms"] == [21, 63]
    assert "K ∈ {21, 63}" in text
    for cell in art["family_cells"].values():
        assert cell["long_leg"]["n_positions"] == 10
    assert "榜宽 10" in text or "取前 10" in text


# ════════════════════════════════════════════════════════════════════
# 工具
# ════════════════════════════════════════════════════════════════════

def _numeric_values_equal_to(obj, target, path="") -> list:
    """递归找「数值**逐位**等于 target」的所有位置（供「跨口径倍数不许出现」用）。"""
    hits = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            hits += _numeric_values_equal_to(v, target, f"{path}::{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            hits += _numeric_values_equal_to(v, target, f"{path}[{i}]")
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
        if math.isclose(float(obj), float(target), rel_tol=0.0, abs_tol=0.0):
            hits.append(path)
    return hits

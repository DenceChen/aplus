"""`scripts/turn_integrity_audit.py` 的守卫。

三条纪律（与本仓既有守卫同族）：
- **判据阈值不得被实现改掉**（预注册 `docs/TODO_turn_integrity_2026-10-15.md` §1）
- **抽样种子不得被实现改掉**
- **口径不得用 clip 把无效日向前填充**（那是最隐蔽的一类静默错误）
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import turn_integrity_audit as tia  # noqa: E402


def test_preregistered_thresholds_are_not_retyped_anywhere():
    """🚨 阈值只准出现在预注册里，实现里改了就是事后调判据。"""
    doc = (ROOT / "docs" / "TODO_turn_integrity_2026-10-15.md").read_text(encoding="utf-8")
    assert tia.R_CLEAN == 1.3 and "1.3" in doc
    assert tia.R_CORRUPT == 2.0 and "2.0" in doc
    assert tia.N_SAMPLE == 200 and "**200**" in doc.replace("**200 ", "**200**")
    assert tia.TURN_AUDIT_SEED == 20261015 and "20261015" in doc


def test_sample_seed_is_reproducible():
    """同一种子两次抽样必须逐位相同 —— 不可复现的判决等于没跑。"""
    covered = sorted(tia.DAILY_DIR.glob("*.parquet"))
    assert len(covered) > 100, (
        f"日线目录只有 {len(covered)} 个文件 ⇒ 本条会退化成跳过，等于没在守")
    rng = np.random.default_rng(tia.TURN_AUDIT_SEED)
    a = sorted(np.array(covered)[rng.choice(len(covered), 5, replace=False)])
    rng = np.random.default_rng(tia.TURN_AUDIT_SEED)
    b = sorted(np.array(covered)[rng.choice(len(covered), 5, replace=False)])
    assert list(a) == list(b)
    rng = np.random.default_rng(tia.TURN_AUDIT_SEED + 1)
    c = sorted(np.array(covered)[rng.choice(len(covered), 5, replace=False)])
    assert list(a) != list(c), "换个种子结果相同 ⇒ 种子其实没起作用"


def test_implied_turn_matches_a_shares_not_float_shares():
    """★ 本次审计里最关键的一条口径事实：turn 的分母是 **a_shares**。

    若哪天数据源把分母改成 float_shares，这条守卫必须先红 ——
    因为流通市值的定义会随之改变，而市值加权的基准依赖它。
    """
    gt = tia.ground_truth_check()
    assert gt.get("n_stocks", 0) > 0, "没有地面真值可比 ⇒ 这条守卫是恒真的"
    assert gt["truth_column"] == "a_shares"
    assert gt["n_in_band_095_105"] == gt["n_stocks"], (
        f"只有 {gt['n_in_band_095_105']}/{gt['n_stocks']} 只票的反推值命中 a_shares 真值")


def test_truth_asof_marks_out_of_range_dates_invalid_instead_of_clipping():
    """⚠️ 早于首条股本记录的日期必须被判为**无效**，不能 clip 到第一条的值。

    这条必须**测 `truth_asof` 本身**（本仓第 5 层纪律：守卫要测对层）。
    只断言 `np.searchsorted` 返回 -1 是**恒真的** —— 那测的是 numpy，不是本仓代码。
    """
    sh = pd.DataFrame({"date": ["2023-01-01", "2024-01-01"],
                       "a_shares": [1e10, 2e10]}).sort_values("date")
    vals, ok = tia.truth_asof(sh, np.array(["2020-06-01", "2023-06-01"]))
    assert ok.tolist() == [False, True], "早于首条记录必须判为无效"
    assert vals[1] == 1e10, "2023-06-01 应取 2023-01-01 那条"
    # 🚨 变异检验：clip 的实现会让 ok[0] 变 True —— 这条必须能拦住它
    assert ok[0] is np.False_ or not ok[0]


def test_truth_asof_picks_the_latest_record_at_or_before_each_date():
    """分段常数右连：同一天多次变动要取**当日**那条，不是上一条。"""
    sh = pd.DataFrame({"date": ["2023-01-01", "2023-06-01", "2024-01-01"],
                       "a_shares": [1.0, 2.0, 3.0]}).sort_values("date")
    vals, ok = tia.truth_asof(sh, np.array(["2023-01-01", "2023-05-31",
                                            "2023-06-01", "2023-12-31"]))
    assert ok.all()
    assert vals.tolist() == [1.0, 1.0, 2.0, 2.0]


def test_iqr_ratio_is_scale_invariant():
    """★ 判据为什么不受「除以哪一列」影响 —— 这是预注册口径不用改的根据。

    `a_shares / float_shares` 对每只票是常数，除以它不改变 p75/p25 之比。
    """
    s = pd.Series([1.0, 2.0, 4.0, 8.0])
    assert tia.iqr_ratio(s) == pytest.approx(s.quantile(.75) / s.quantile(.25))
    assert tia.iqr_ratio(s) == pytest.approx(tia.iqr_ratio(s * 0.6398))


def test_verdict_thresholds_map_to_the_preregistered_branches():
    """三个分支的边界必须与预注册表格逐字对应，不多不少。"""
    def v(R):
        return ("TURN_CLEAN" if R < tia.R_CLEAN
                else "TURN_CORRUPT" if R >= tia.R_CORRUPT
                else "TURN_GRAY")
    assert v(1.29) == "TURN_CLEAN"
    assert v(1.30) == "TURN_GRAY"          # 边界归 GRAY（预注册 [1.3, 2.0)）
    assert v(1.99) == "TURN_GRAY"
    assert v(2.00) == "TURN_CORRUPT"       # 边界归 CORRUPT（预注册 R ≥ 2.0）

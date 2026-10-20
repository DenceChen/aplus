"""守卫：`scripts/measure_bond_deployed_nav.py` 的口径与判决。

预注册：`docs/PREREG_BOND_DEPLOYED_NAV_2026-10-20.md`。

## 这份守卫守什么

1. **整手口径**：1 手 = 10 张 = 1,000 元面值；买得起几手必须**向下取整**。
2. **口径分叉防线（S1）**：整手模拟器的「取消取整」极限必须与**生产引擎**
   `aplus.bond_excess.run_rotation` 的逐期毛收益一致 —— 否则整手模拟器会把
   「口径不同」伪装成「整手影响」。
3. **账本对账（S2）**：前瞻臂的分数极限必须与 `bond_paper.parquet` 的 `nav` 一致。
4. **年化常数**：R = 244，**不许**混 `aplus.verify` 的 252。
5. **缺档报错不回落**：缺列 / 缺臂 ⇒ 抛异常。
6. **锚点只读**：`bond_paper_state.json` 跑前跑后 sha256 逐位不变。
7. **产物内容**：诚实边界（含「转债 ≠ 股票」）、旧产物逐字引用、股票侧只读不重算。

⚠️ **测试不许写生产产物路径** —— 集成测试一律 `--out tmp_path/...`，
并断言 `data/reports/` 下的既有产物字节未变。
⚠️ **不许对着产物快照做自洽检查** —— 每个数都从**真实输入重算**。
"""
import hashlib
import importlib.util
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = ROOT / "scripts" / "measure_bond_deployed_nav.py"


def _mod():
    spec = importlib.util.spec_from_file_location("_bond_deployed_nav", _SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


M = _mod()
PAPER_PARQUET = ROOT / "data/reports/bond_paper.parquet"
PAPER_STATE = ROOT / "data/reports/bond_paper_state.json"
OLD_BOND_ARTIFACT = ROOT / "data/reports/bond_excess_analysis.json"
STOCK_VERDICT = ROOT / "data/reports/basket_nav_01_2026-10-16.json"
STOCK_D2 = ROOT / "data/reports/d2_signal_requirement.json"


@pytest.fixture(scope="module")
def cache():
    from aplus.bond_horizon import build_horizon_cache
    from aplus.bond_sync import load_bond_map
    bond_map = load_bond_map(str(ROOT / "data"))
    if not bond_map:
        pytest.skip("转债库为空")
    return build_horizon_cache(bond_map)


@pytest.fixture(scope="module")
def produced(tmp_path_factory):
    """跑一次脚本（写到 tmp_path），返回解析后的产物 dict。"""
    out = tmp_path_factory.mktemp("bond_deployed_nav") / "bond_deployed_nav.json"
    argv = sys.argv
    sys.argv = ["measure_bond_deployed_nav.py", "--out", str(out)]
    try:
        rc = M.main()
    finally:
        sys.argv = argv
    assert rc == 0
    return json.loads(out.read_text(encoding="utf-8"))


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# ══════════════════════════════════════════════════════════════════
# ① 整手口径
# ══════════════════════════════════════════════════════════════════

def test_one_lot_is_ten_zhang_of_one_hundred_face():
    """1 手 = 10 张 = 1,000 元面值 ⇒ 1 手成本 = 10 × 价格。"""
    assert M.LOT_ZHANG == 10
    assert M.LOT_FACE == 100.0
    assert M.lot_cost(106.1257, 1) == pytest.approx(10 * 106.1257, rel=0, abs=0)
    assert M.lot_cost(106.1257, 4) == pytest.approx(4245.028, rel=0, abs=1e-9)


def test_a_bond_lot_is_affordable_where_a_stock_lot_is_not():
    """本轮立项的前提：5 万本金下股票整手买不起、转债整手买得起。

    688498.SH 收盘 1010.35 ⇒ 股票 1 手 = 100 × 1010.35 = ¥101,035（全部本金的 2 倍）；
    转债 1 手 = 10 × 106.1257 = ¥1,061.26（2.1% 本金）。
    """
    stock_lot = 100 * 1010.35
    bond_lot = 10 * 106.1257
    assert stock_lot > 50_000.0          # 股票：买不起
    assert bond_lot < 5_000.0            # 转债：每只分到 5,000 时买得起
    assert M.affordable_lots(5_000.0, 106.1257) == 4
    # 股票侧同样的算法：5 万 / 10 只 = 每只 5,000 ⇒ 一手都买不起
    assert M.affordable_lots(5_000.0, 1010.35) == 0


def test_affordable_lots_rounds_down_to_whole_lots():
    """取整必须是**向下**到整手（不能四舍五入、不能取上限）。"""
    assert M.affordable_lots(1061.257, 106.1257) == 1
    assert M.affordable_lots(1061.2569, 106.1257) == 0
    assert M.affordable_lots(50_000.0, 100.0) == 50
    with pytest.raises(ValueError):
        M.affordable_lots(1000.0, 0.0)


# ══════════════════════════════════════════════════════════════════
# ② 口径分叉防线
# ══════════════════════════════════════════════════════════════════

def test_the_forward_fractional_limit_reproduces_the_parquet_nav(cache):
    """S2：前瞻臂的分数极限必须与 parquet 的 nav 一致（差 ≤ 1e-12）。"""
    arm = M.forward_paper_arm(PAPER_PARQUET, cache["frames"], M.PAPER_ARM,
                               capital=50_000.0, lot_zhang=None)
    nav = (pd.read_parquet(PAPER_PARQUET)
           .query("arm == @M.PAPER_ARM").sort_values("date", kind="stable")["nav"]
           .to_numpy(dtype=float))
    got = arm["values"] / arm["values"][0]
    assert len(got) == len(nav)
    assert float(np.max(np.abs(got - nav))) <= 1e-12


def test_the_fractional_limit_reproduces_the_production_engine(cache):
    """S1：整手模拟器的「取消取整」极限 == 生产引擎的逐期毛收益。

    ⚠️ 这是**防口径分叉**的那一条：分叉的模拟器会把「口径不同」伪装成
    「整手影响」（整手影响是本轮唯一想量的东西）。
    """
    from aplus.bond_excess import (DEFAULT_COST, DEFAULT_HOLD_DAYS, DEFAULT_TOP_N,
                                   run_rotation, select_cheap_top_n, split_segments)
    segs = split_segments(cache, 0.6, 5)
    s, e = segs["full"]
    rot = run_rotation(cache, s, e, select_cheap_top_n(DEFAULT_TOP_N),
                       hold_days=DEFAULT_HOLD_DAYS, cost=DEFAULT_COST)
    frac = M.backtest_deployed_arm(cache, s, e, rot, capital=50_000.0, lot_zhang=None)
    prod = np.array([p["gross"] for p in rot["periods"]], dtype=float)
    mine = np.array([r["gross"] for r in frac["period_rows"]], dtype=float)
    assert len(prod) == len(mine)
    assert float(np.max(np.abs(prod - mine))) <= 1e-12 * max(1.0, float(np.max(np.abs(prod))))


def test_the_deployed_arm_holds_whole_lots_only(cache):
    """整手档的每一笔必须是 10 张的整数倍，且投入率 ∈ [0,1]。"""
    from aplus.bond_excess import (DEFAULT_COST, DEFAULT_HOLD_DAYS, DEFAULT_TOP_N,
                                   run_rotation, select_cheap_top_n, split_segments)
    segs = split_segments(cache, 0.6, 5)
    s, e = segs["test"]
    rot = run_rotation(cache, s, e, select_cheap_top_n(DEFAULT_TOP_N),
                       hold_days=DEFAULT_HOLD_DAYS, cost=DEFAULT_COST)
    dep = M.backtest_deployed_arm(cache, s, e, rot, capital=50_000.0, lot_zhang=M.LOT_ZHANG)
    assert dep["period_rows"], "整手档没有产生任何调仓期 ⇒ 空跑"
    for row in dep["period_rows"]:
        assert 0.0 <= row["deployed_ratio"] <= 1.0
        assert row["idle_cash"] >= -1e-9
        assert row["shares"], "这一期一笔都没买"
        for code, sh in row["shares"].items():
            assert sh % M.LOT_ZHANG == pytest.approx(0.0, abs=1e-9), code
            assert sh > 0


def test_the_idle_cash_is_accounted_for_in_every_period(produced):
    """会计恒等式对拍：`deployed + idle_cash == capital_at_buy`（逐期）。

    ⚠️ 早先版本在期末把**闲置现金整段覆盖掉**（货款 + 成本覆盖 cash）⇒
    余数从账户里消失 ⇒ 投入率虚高、账户凭空缩水。
    这条恒等式是那道缺陷的**外部锚**（不靠重算自己的字段）。
    """
    rows = produced["backtest"]["test"]["deployed_50k"]["period_rows_detail"]
    assert rows
    for r in rows:
        assert r["deployed"] + r["idle_cash"] == pytest.approx(
            r["capital_at_buy"], rel=1e-12, abs=1e-6), r
        assert r["deployed_ratio"] == pytest.approx(
            r["deployed"] / r["capital_at_buy"], rel=1e-9, abs=1e-12)
        assert 0.0 < r["deployed_ratio"] < 1.0     # 整手取整**必然**留余数
        assert r["idle_cash"] > 0.0


def test_the_forward_buys_also_reconcile_deployed_plus_idle(produced):
    """同一恒等式在前瞻臂上也成立。"""
    for b in produced["forward_paper_account"]["deployed_50k"]["buys"]:
        assert b["deployed"] + b["idle_cash"] == pytest.approx(
            b["capital_at_buy"], rel=1e-12, abs=1e-6)
        assert 0.0 < b["deployed_ratio"] < 1.0


def test_the_deployed_ratio_is_much_higher_than_the_stock_side(produced):
    """转债的整手几乎不吃掉本金；股票侧投入率只有 5.6%~44%。"""
    dep = produced["backtest"]["test"]["deployed_50k"]
    assert dep["deployed_ratio_mean"] > 0.75
    stock_ratios = list(produced["stock_side_reference"]["execution_deployed_ratio"]
                        ["by_window"].values())
    assert stock_ratios and max(stock_ratios) < 0.5


# ══════════════════════════════════════════════════════════════════
# ③ 统计口径
# ══════════════════════════════════════════════════════════════════

def test_the_account_cagr_is_geometric_not_arithmetic():
    """算术年化会把符号/量级说歪 ⇒ CAGR 必须是几何的。"""
    r = np.array([0.10, -0.10, 0.10, -0.10, 0.10, -0.10, 0.10, -0.10,
                  0.10, -0.10, 0.10, -0.10, 0.10, -0.10, 0.10, -0.10], dtype=float)
    st = M.annual_stats(r, n_days=len(r), annualization_days=244)
    total = float(np.prod(1.0 + r) - 1.0)
    assert st["compounded_total"] == pytest.approx(total, rel=0, abs=0)
    assert st["cagr"] == pytest.approx((1.0 + total) ** (244.0 / len(r)) - 1.0, rel=0, abs=1e-15)
    # 这个夹具上几何 ≠ 算术 ⇒ 断言「不是算术」才有信息量
    assert st["arith_ann"] != pytest.approx(st["cagr"], rel=1e-9)


def test_the_mde_uses_244_trading_days_and_is_not_252():
    """R = 244（aplus.hurdle），**不许**混 verify.py 的 252。"""
    from aplus.hurdle import DAYS_PER_YEAR as R
    from aplus.verify import TRADING_DAYS_PER_YEAR as R_VERIFY
    assert M.R == 244 == R
    assert R_VERIFY == 252 and R_VERIFY != R
    r = np.array([0.01, -0.02, 0.015, -0.005, 0.002, -0.011, 0.004], dtype=float)
    st = M.annual_stats(r, n_days=len(r), annualization_days=M.R)
    expected = 1.96 * float(np.log1p(r).std(ddof=1)) / math.sqrt(len(r)) * 244.0
    assert st["mde_annual"] == pytest.approx(expected, rel=0, abs=1e-15)
    wrong = 1.96 * float(np.log1p(r).std(ddof=1)) / math.sqrt(len(r)) * 252.0
    assert st["mde_annual"] != pytest.approx(wrong, rel=1e-6)


def test_the_upper_bound_is_c_plus_m():
    """U = c + M，逐位相等。"""
    cases, u = M.judge(0.10, 0.05, label="账户")
    assert u == pytest.approx(0.15, rel=0, abs=1e-15)
    assert any("U < T" in c for c in cases)
    # c < T ≤ U 且 |c| < M ⇒ 两条**并列**命中（不互斥）
    cases, u = M.judge(0.10, 0.15, label="账户")
    assert u == pytest.approx(0.25, rel=0, abs=1e-15)
    assert any("测不出" in c for c in cases)
    assert any("连符号都定不了" in c for c in cases)


def test_the_sign_is_not_determinable_when_abs_c_is_below_m():
    """|c| < M ⇒ 连符号都定不了 ⇒ **不许**说「接近」，也不许说「达不到」。"""
    cases, u = M.judge(-0.05, 0.30, label="账户")
    assert any("连符号都定不了" in c for c in cases)
    text = " ".join(cases)
    assert u == pytest.approx(0.25, rel=0, abs=1e-15)
    # 上界跨过目标 ⇒ 判「测不出」
    assert "测不出" in text
    # ⚠️ **不许**用裸子串判「接近 / 达不到」：判决文案里本来就写着
    #   「既不许说「达不到」，也不许说「接近」」（第 13 层的同族：子串断言会被
    #   **禁止句**满足）。真正要判的是**分支没走错**：
    assert "不可达" not in text          # U ≥ T ⇒ 「U < T ⇒ 不可达」这一支不许命中
    assert "不可达" in " ".join(M.judge(-0.05, 0.10, label="账户")[0])   # U < T ⇒ 命中


def test_the_structural_gate_fires_before_the_significance_rules():
    """years < 1 ⇒ 样本过短，不给方向；这条闸门**先于**显著性判据。"""
    st = M.annual_stats(np.array([0.001, -0.002, 0.003]), n_days=3, annualization_days=244)
    gate = M._structural_gate(st["years"])
    assert gate is not None and "样本过短" in gate


# ══════════════════════════════════════════════════════════════════
# ④ 缺档报错不回落
# ══════════════════════════════════════════════════════════════════

def test_a_missing_required_column_raises_instead_of_falling_back(tmp_path, cache):
    """缺 `nav` 等必需列 ⇒ 抛异常，**不许**回落成 0 或跳过该臂。"""
    df = pd.read_parquet(PAPER_PARQUET)
    broken = tmp_path / "broken.parquet"
    df.drop(columns=["nav"]).to_parquet(broken, index=False)
    with pytest.raises(KeyError):
        M.forward_paper_arm(broken, cache["frames"], M.PAPER_ARM)


def test_a_missing_arm_raises_instead_of_falling_back(tmp_path, cache):
    """纸面账户里没有那条臂 ⇒ 抛异常。"""
    df = pd.read_parquet(PAPER_PARQUET)
    broken = tmp_path / "no_arm.parquet"
    df[df["arm"] != M.PAPER_ARM].to_parquet(broken, index=False)
    with pytest.raises(KeyError):
        M.forward_paper_arm(broken, cache["frames"], M.PAPER_ARM)


def test_an_empty_return_series_raises_instead_of_returning_zero():
    with pytest.raises(ValueError):
        M.annual_stats(np.array([]), n_days=0)


# ══════════════════════════════════════════════════════════════════
# ⑤ 锚点只读
# ══════════════════════════════════════════════════════════════════

def test_the_anchor_state_file_is_never_written(cache):
    """`bond_paper_state.json` 跑前跑后 sha256 逐位不变（硬约束）。"""
    before = _sha(PAPER_STATE)
    M.forward_paper_arm(PAPER_PARQUET, cache["frames"], M.PAPER_ARM,
                        capital=50_000.0, lot_zhang=M.LOT_ZHANG)
    assert _sha(PAPER_STATE) == before


def test_the_judgment_arm_is_not_a_phase_reselection(produced):
    """判决臂是 phase 0（锚点臂），相位带只作披露。"""
    band = produced["forward_paper_account"]["phase_band"]
    assert set(band) == {"cheap_p0", "cheap_p1", "cheap_p2", "cheap_p3", "cheap_p4"}
    assert produced["forward_paper_account"]["anchor_state_readonly"]["start"] == "2026-10-11"
    # 判决臂用的是 phase 0
    assert produced["verdict"]["judgment_arm"].startswith("backtest.test.deployed_50k")


def test_the_verdict_numbers_follow_the_selected_arm_not_just_its_label(produced):
    """🚨 标签必须跟着对象走（第 142 层：标签与对象各写一份 ⇒ 换对象而标签不变）。

    ⚠️ 只断言标签**不够**：把判决臂从 test 换成 train 而标签照旧，标签断言照样绿。
    ⇒ 这里断言**数字**等于所选臂的数字，且与另一臂的数字**不同**（保证有判别力）。
    """
    v = produced["verdict"]
    assert v["judgment_segment"] == "test"
    test_arm = produced["backtest"]["test"]["deployed_50k"]
    train_arm = produced["backtest"]["train"]["deployed_50k"]
    assert v["cagr"] == test_arm["cagr"]
    assert v["mde_annual"] == test_arm["mde_annual_blocked_caliber"]
    assert v["u_upper_95"] == test_arm["u_upper_95"]
    # 有判别力：两个段的读数确实不同（否则上三条恒真）
    assert train_arm["cagr"] != test_arm["cagr"]


# ══════════════════════════════════════════════════════════════════
# ⑥ 产物内容（集成）
# ══════════════════════════════════════════════════════════════════

def test_the_script_writes_only_to_the_given_output_path(produced, tmp_path):
    """集成测试不许写生产产物路径。

    🚨 **有判别力的测法**：**跑一次脚本，前后比对生产路径的字节与 mtime**。
    原来那版写的是 `assert not (...).exists() or True` ⇒ **恒真** ⇒ 是装饰
    ⇒ 被仓库自带的 `test_no_guard_in_tests_asserts_a_constant_truth` 当场抓到。
    ⇒ 「恒真的守卫」比「没有守卫」更坏：它让人以为这一层有人看着。
    """
    prod = ROOT / "data/reports/bond_deployed_nav.json"
    before = (_sha(prod), prod.stat().st_mtime_ns) if prod.exists() else None

    out = tmp_path / "again_bond_deployed_nav.json"
    argv = sys.argv
    sys.argv = ["measure_bond_deployed_nav.py", "--out", str(out)]
    try:
        rc = M.main()
    finally:
        sys.argv = argv
    assert rc == 0
    assert out.exists(), "脚本必须写到 --out 给的路径"
    assert produced["artifact"] == "scripts/measure_bond_deployed_nav.py"

    after = (_sha(prod), prod.stat().st_mtime_ns) if prod.exists() else None
    assert after == before, "集成测试**改动了生产产物路径**（字节或 mtime 变了）"


def test_the_output_carries_the_honest_boundaries_including_bond_is_not_stock(produced):
    """四条诚实边界 + 「转债 ≠ 股票」必须逐字在产物里。"""
    hb = " ".join(produced["honest_boundaries"])
    assert "与 0 不可区分" in hb
    assert "相位未重选" in hb
    assert "摘牌" in hb
    assert "连符号都定不了" in hb
    assert "不是 A 股股票" in hb
    assert produced["caliber"]["bond_is_not_stock"].startswith("**可转债不是 A 股股票**")
    # 「测不出」不许被写成「没有」
    for forbidden in ("没有超额", "证明没有 alpha", "超额不存在", "不存在超额"):
        assert forbidden not in hb
        assert forbidden not in produced["verdict"]["verdict_cases"][0] + " ".join(
            produced["verdict"]["verdict_cases"])


def test_the_output_quotes_the_old_bond_artifact_verbatim(produced):
    """旧产物的 verdict 逐字引用（不许转述成自己的话）。"""
    old = json.loads(OLD_BOND_ARTIFACT.read_text(encoding="utf-8"))
    verbatim = old["verdict"]
    assert verbatim in " ".join(produced["honest_boundaries"])
    assert produced["old_artifact_2026_10_16"]["verdict_verbatim"] == verbatim
    assert produced["old_artifact_2026_10_16"]["reproducible_today"] is False


def test_the_stock_side_numbers_are_read_from_the_artifacts_not_recomputed(produced):
    """股票侧只读不重算：产物里的数必须与既有产物逐位相等。"""
    sv = json.loads(STOCK_VERDICT.read_text(encoding="utf-8"))
    sd2 = json.loads(STOCK_D2.read_text(encoding="utf-8"))
    ref = produced["stock_side_reference"]
    assert ref["verdict_arm"]["cagr"] == sv["tiers"][1]["cagr"]
    assert ref["verdict_arm"]["mde_annual"] == sv["tiers"][1]["mde_as_annual_pct"]
    w10 = {w["n"]: w for w in sd2["headline"]["account_annual_ceiling_by_width"]}[10]
    assert ref["deliverable_50k"]["account_annual_at_50k_pct"] == \
        w10["account_annual_at_50k_pct"]


def test_both_mde_calibers_are_reported_and_the_verdict_uses_the_blocked_one(produced):
    """两个 MDE 口径必须并列报出；判决口径是分块（观测单位 = 一次调仓）。"""
    v = produced["verdict"]
    assert v["mde_annual_daily_caliber"] is not None
    assert v["mde_annual_blocked_caliber"] is not None
    assert v["mde_annual"] == v["mde_annual_blocked_caliber"]
    for seg in ("train", "test", "full"):
        arm = produced["backtest"][seg]["deployed_50k"]
        assert arm["mde_annual_daily_caliber"] is not None
        assert arm["mde_annual_blocked_caliber"] is not None
        assert arm["measured_sign_determinable"] is not None
        assert arm["measured_sign_determinable_daily_caliber"] is not None


def test_the_idle_cash_sensitivity_never_hurts_the_account(produced):
    """闲置现金计 1.5%/年 ⇒ 账户终值不可能低于计 0 的那一档。"""
    for seg in ("train", "test", "full"):
        b = produced["backtest"][seg]
        assert b["deployed_50k_idle_cash_1p5pct"]["compounded_total"] >= \
            b["deployed_50k"]["compounded_total"] - 1e-12


def test_the_forward_arm_is_gated_by_the_structural_short_sample_rule(produced):
    """前瞻臂只有 7 个交易日 ⇒ 必须触发「样本过短，不给方向」。"""
    for k in ("fractional", "deployed_50k", "deployed_50k_idle_cash_1p5pct"):
        arm = produced["forward_paper_account"][k]
        assert arm["years"] < 1.0
        assert arm["structural_gate"] is not None
        assert "样本过短" in arm["verdict_cases"][0]


def test_the_gap_to_target_is_flagged_as_meaningless_when_the_sign_is_undecidable(produced):
    """`|c| < M` ⇒ gap 只是形状陈述，可读的是 U。"""
    v = produced["verdict"]
    if v["measured_sign_determinable"] is False:
        assert v["cagr"] is not None
        assert produced["backtest"]["test"]["deployed_50k"]["gap_is_meaningful"] is False
        assert v["u_upper_95"] is not None

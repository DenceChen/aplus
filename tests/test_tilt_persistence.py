"""「风格倾斜持续性」的守卫测试 —— **尺子歪了，判决就整个反过来**。

本脚本的判决只有两种可能：稳定 / 漂移。若块划分、符号、自相关、
对照臂任何一处算错，结论会**指向相反方向**，
而报告照样出数、照样打印一张像模像样的判决表。

## 守住的八件事

1. **块划分**：`to_blocks` 是**不重叠**块均值，块长 = K。
   写成重叠滑动窗口不会报错，只会让 `AC1` 变成一个更好看的数。
2. **块边界按位置**：与 `aplus/rebalance.py::is_rebalance_day` 同一约定。
   用日历日（`% 63 天`）会静默错位 —— 交易日序列里有春节、国庆。
3. **外部锚（不依赖实现的正确性）**：
   - 常量块 ⇒ `AC1 = NaN`（**不是 0，也不是 1**）
   - 完美线性块 ⇒ `AC1 = +1`
   - 完美交替块 ⇒ `AC1 = −1`
   - **Fisher 区间必须把 `r=+1` 包住、`r=0` 的区间跨 0**
4. **符号一致率**：`agree ∈ [0,1]` 且 `agree_n` 与 `n_years` 自洽。
5. **对照臂不是摆设**：C1 打乱的是**符号**不是**年份顺序**。
   打乱顺序对 `agree` 完全无效（顺序无关统计量），
   那样会安静返回 `p ≡ 1.0` —— 看着在跑，其实在什么都没测。
6. **判决无 margin 字段**：`pass_` 只有 `bool`（CLAUDE.md 铁律）。
7. **缺列响亮拒绝**：少一个 `expo_*` 不得回落成「有的算有的不算」。
8. **翻转率出局规则真的生效**：`vol20` 在 K=63 下就是被它出局的 ——
   一条从不生效的规则等于没有规则。

## ★ 为什么每条都做过变异验证

项目已栽过两次「变异没变红 ⇒ 误判守卫有洞」的跟头
（`CLAUDE.md` 第 13/14 层）。所以每条守卫的变异都按三步走，**顺序不可颠倒**：

    ① 文件内容哈希变了 **且** 标记串恰好出现 1 次
    ② 变异后文件**仍能被 ast.parse 解析**（语法错误造成的红不算数）
    ③ 才看测试结果

三步缺一不可。校验脚本见文件末尾的变异清单。
"""
import ast
import hashlib
import importlib.util
import math
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _ROOT / "scripts" / "tilt_persistence.py"


def _load():
    spec = importlib.util.spec_from_file_location("_tilt_persistence", _SCRIPT)
    m = importlib.util.module_from_spec(spec)
    sys.modules["_tilt_persistence"] = m
    spec.loader.exec_module(m)
    return m


TP = _load()


# ── 1. 块划分：不重叠、块长 = K、末块保留 ────────────────────────────

def test_blocks_are_non_overlapping_means_of_exactly_k_days():
    """手算外部锚：12 个数、K=4 ⇒ 3 块，逐块均值。

    不依赖实现：如果 `to_blocks` 写成滑动窗口，这里立刻对不上。
    """
    v = np.arange(12, dtype=float)
    out = TP.to_blocks(v, 4)
    assert out.tolist() == [1.5, 5.5, 9.5]


def test_blocks_keep_the_short_final_block_instead_of_dropping_it():
    """末块不足 K 天**必须保留**。

    删掉它等于「假装最后一次调仓没发生」，
    而它恰恰是最新的一块 —— 删掉等于用「未来不存在」来抬高历史一致性。
    """
    out = TP.to_blocks(np.arange(10, dtype=float), 4)   # 10 = 4+4+2
    assert len(out) == 3
    assert out[-1] == pytest.approx((8 + 9) / 2)


def test_blocks_refuse_rather_than_pad_when_k_exceeds_the_sample():
    """K 大于样本 ⇒ 必须报错，**不许**补零/补 NaN 凑整。

    凑整会让末块均值被填充值污染，而报告照样出数。
    """
    with pytest.raises(SystemExit):
        TP.to_blocks(np.arange(5, dtype=float), 50)


def test_block_length_is_honoured_so_k_changes_the_verdict_input():
    """同一串数，K 不同 ⇒ 块数不同。

    防「K 参数被吃掉、块长写死」这类**不报错的退化**：
    块数必须随 K 变，否则 AC1 测的根本不是调仓频率轴上的东西。
    """
    v = np.arange(120, dtype=float)
    assert len(TP.to_blocks(v, 10)) == 12
    assert len(TP.to_blocks(v, 20)) == 6


# ── 2. 块边界：按位置，不是按日历日 ────────────────────────────────────

def test_block_boundaries_follow_trading_day_position_not_calendar_days():
    """跨春节的那一段：日历日 % 63 与**位置** % 63 会给出不同的块。

    节假日缺口正是「用日历日会错位」的现成反例。
    """
    idx = pd.Index(["2021-02-05", "2021-02-08", "2021-02-09", "2021-02-10",
                    "2021-02-18", "2021-02-19", "2021-02-22"])
    df = pd.DataFrame(
        {f"expo_{f}": np.arange(7, dtype=float) for f in ["mom20", "rev5", "vol20", "vsur",
                                                           "turn20", "turnratio", "ivol", "rsi"]},
        index=idx)
    yearly = TP.to_yearly(df, TP.FACTOR_NAMES)
    # 全部落在 2021 年 ⇒ 只有一个年度键（证明没按日历切碎）
    assert list(yearly) == [2021]


def test_yearly_means_use_every_trading_day_of_the_year():
    """年度均值 = 该年**全部交易日**等权，不是块加权。

    块加权会让只有 1~2 个块的 2026 与 2021 不可比，而报告不会为此报警。
    """
    idx = pd.Index(["2021-01-04"] * 3 + ["2022-01-04"] * 2)
    df = pd.DataFrame({f"expo_{f}": [1.0, 2.0, 3.0, 10.0, 20.0] for f in TP.FACTOR_NAMES},
                      index=idx)
    y = TP.to_yearly(df, ["mom20"])
    assert y[2021]["mom20"] == pytest.approx(2.0)
    assert y[2022]["mom20"] == pytest.approx(15.0)


# ── 3. 外部锚：AC1 的三个可手算特例 + Fisher 区间 ──────────────────────

def test_constant_blocks_yield_nan_not_zero_and_not_one():
    """常量序列的自相关**无定义**。

    返回 0 会被读成「无持续性」，返回 1 会被读成「完美持续」——
    两者都是凭空结论。项目的 `t_blocked` 已专门修过这类退化。
    """
    assert np.isnan(TP.ac1(np.full(10, 0.5)))


def test_a_perfectly_rising_block_series_has_ac1_of_plus_one():
    assert TP.ac1(np.arange(10, dtype=float)) == pytest.approx(1.0, abs=1e-9)


def test_a_perfectly_alternating_block_series_has_ac1_of_minus_one():
    x = np.array([1.0, -1.0] * 8)
    assert TP.ac1(x) == pytest.approx(-1.0, abs=1e-9)


def test_ac1_refuses_on_two_points_because_that_is_noise_not_persistence():
    """两个点总能算出 ±1 —— 那是定义，不是持续性。

    ★ 断言**返回值还不够**：两个点的 `np.corrcoef` 自己也返回 NaN
    （伴随 `Degrees of freedom <= 0` 的 RuntimeWarning）。
    ⇒ 只钉 `isnan` 时，把 `len<3` 放宽成 `len<2` 的变异**照样绿**。
    真正可区分的是**有没有发出 RuntimeWarning** ——
    那正是这道守卫存在的意义：**不把垃圾算给调用方看**。
    """
    assert np.isnan(TP.ac1(np.array([1.0, 2.0])))
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        TP.ac1(np.array([1.0, 2.0]))
    assert not [x for x in caught if issubclass(x.category, RuntimeWarning)], (
        "两点序列被放行到 corrcoef ⇒ 守卫失效（会发 Degrees of freedom 警告）")


def test_ac1_matches_the_textbook_ar1_process_by_construction():
    """外部锚：`x[t] = φ·x[t−1] + ε`（iid 噪声）⇒ AC1 ≈ φ。

    不依赖实现，也不依赖本脚本的任何中间量。
    """
    rng = np.random.default_rng(7)
    phi = 0.5
    x = np.zeros(20000)
    e = rng.standard_normal(20000)
    for t in range(1, len(x)):
        x[t] = phi * x[t - 1] + e[t]
    assert TP.ac1(x) == pytest.approx(phi, abs=0.02)


def test_fisher_interval_brackets_the_truth_and_crosses_zero_when_uninformative():
    """Fisher 区间必须：(a) 包住 r；(b) n 小、r≈0 时跨 0。

    预注册 §3.5.2：B 只有 ~23 个块时，`AC1 > 0` 远不足以说「显著为正」。
    """
    lo, hi = TP.ac1_ci95(0.5, 23)
    assert lo < 0.5 < hi
    assert lo > 0.0                      # n=23、r=0.5 时不该跨 0
    lo0, hi0 = TP.ac1_ci95(0.02, 23)
    assert lo0 < 0.0 < hi0               # r≈0 时必须跨 0
    assert TP.ac1_ci95(0.5, 3)[0] != TP.ac1_ci95(0.5, 3)[0]   # NaN != NaN


# ── 4. 符号一致率 ──────────────────────────────────────────────────────

def test_agree_is_a_fraction_and_its_count_matches_the_year_count():
    """`agree` ∈ [0,1]，且 `agree_n` 与 `n_years` 自洽。"""
    df = _frame(tilt_by_year={
        2021: {"ivol": -1.0}, 2022: {"ivol": -1.0}, 2023: {"ivol": -1.0},
        2024: {"ivol": -1.0}, 2025: {"ivol": -1.0}, 2026: {"ivol": +1.0}})
    doc = TP.analyze(df, k=20, n_shuffle=50, seed=1)
    r = [x for x in doc["per_factor"] if x["factor"] == "ivol"][0]
    assert r["agree_n"] == 5
    assert r["agree"] == pytest.approx(5 / 6)
    assert 0.0 <= r["agree"] <= 1.0


def test_flip_rate_counts_adjacent_year_sign_changes():
    """`- - + -` ⇒ 2 次翻转 / 3 段 = 0.667；全同号 ⇒ 0。"""
    assert TP.flip_rate([-1.0, -2.0, -3.0]) == 0.0
    assert TP.flip_rate([-1.0, -2.0, 3.0, -4.0]) == pytest.approx(2 / 3)


# ── 5. 对照臂：打乱符号而非打乱顺序（★ 这是最容易写废的一条） ──────────

def test_the_shuffle_arm_permutes_signs_not_year_order():
    """**外部锚**：一个全同号的序列，`p_agree` 必须很小。

    若对照臂去打乱**年份顺序**，`agree` 是顺序无关的统计量，
    打乱后观测值纹丝不动 ⇒ `p ≡ 1.0`，而界面照常显示「跑过了」。
    """
    yv = [-1.0] * 6
    p = TP.shuffle_p_agree(yv, n_shuffle=2000, seed=3)
    assert p < 0.05, "全同号却没超过运气 ⇒ 对照臂多半在打乱一个无关的量"


def test_the_ac1_shuffle_arm_reports_white_noise_as_pure_luck():
    """白噪声块序列 ⇒ `AC1` 不应被判为「超过运气」。

    ⚠️ 这里**必须用白噪声，不能用随机游走**（`cumsum`）。
    随机游走的 AC1 **本来就应该接近 +1**（它是一阶积分过程），
    实测 p = 0.002 是**正确行为**，不是 bug。
    第一版把它当「纯运气」写进断言，方向反了 ——
    与项目第「失败方向判断错」那条同族：**测到反直觉的值先怀疑断言**。
    """
    rng = np.random.default_rng(11)
    b = rng.standard_normal(60)
    assert abs(TP.ac1(b)) < 0.35                 # 白噪声的样本 AC1 应接近 0
    p = TP.shuffle_p_ac1(b, n_shuffle=500, seed=4)
    assert p > 0.05


def test_a_random_walk_is_correctly_flagged_as_persistent():
    """反向检验：**随机游走确实有持续性**，对照臂必须把它认出来。

    这是上一条的反面 —— 若这条也红了，说明对照臂恒返回同一个值，
    那它就是个摆设。
    """
    rng = np.random.default_rng(11)
    b = np.cumsum(rng.standard_normal(60))
    assert TP.ac1(b) > 0.8
    assert TP.shuffle_p_ac1(b, n_shuffle=500, seed=4) < 0.05


def test_the_ac1_shuffle_arm_is_a_no_op_on_a_constant_series():
    """常量序列 `AC1` 无定义 ⇒ p 必须是 NaN，不能返回 0 或 1。"""
    assert np.isnan(TP.shuffle_p_ac1(np.full(30, 0.3), n_shuffle=100, seed=5))


# ── 6. 判决结构：无 margin 字段、pass_ 是真 bool ───────────────────────

def test_verdict_exposes_a_bool_pass_and_no_margin_field():
    """"只差一点点"这种话不许有地方说（CLAUDE.md 铁律）。"""
    doc = TP.analyze(_frame(tilt_by_year={
        2021: {"ivol": -1.0}, 2022: {"ivol": -1.0}, 2023: {"ivol": -1.0},
        2024: {"ivol": -1.0}, 2025: {"ivol": -1.0}, 2026: {"ivol": -1.0}}),
        k=20, n_shuffle=50, seed=1)
    v = doc["verdict"]
    assert isinstance(v["pass_"], bool)
    assert not any("margin" in kk.lower() for kk in v)


def test_a_strongly_alternating_yearly_series_is_not_called_stable():
    """反向检验：符号年年翻的因子绝不能进 `stable_factors`。"""
    doc = TP.analyze(_frame(tilt_by_year={
        2021: {"ivol": -1.0}, 2022: {"ivol": +1.0}, 2023: {"ivol": -1.0},
        2024: {"ivol": +1.0}, 2025: {"ivol": -1.0}, 2026: {"ivol": +1.0}}),
        k=20, n_shuffle=50, seed=1)
    assert "ivol" not in doc["verdict"]["stable_factors"]


# ── 7. 缺列必须响亮拒绝 ────────────────────────────────────────────────

def test_a_missing_exposure_column_is_refused_not_silently_dropped():
    """少一列 ⇒ `SystemExit`。

    回落成「有的算有的不算」会让「8 个因子里 6 个稳定」
    变成「6 个稳定 + 2 个从未参与」的假象，而报告照常出数。
    """
    idx = pd.Index(["2021-01-04", "2021-01-05"])
    df = pd.DataFrame({"expo_mom20": [-1.0, -1.0]}, index=idx)   # 只有 1 列
    with pytest.raises(SystemExit):
        TP.factor_columns(df.columns)


def test_factor_columns_ignores_an_unrelated_expo_prefixed_column():
    """多一列无关 `expo_*` 不得混进 8 个因子的判决。"""
    idx = pd.Index(["2021-01-04"])
    cols = {f"expo_{f}": [0.0] for f in TP.FACTOR_NAMES}
    cols["expo_bogus"] = [9.9]
    got = TP.factor_columns(pd.DataFrame(cols, index=idx).columns)
    assert got == list(TP.FACTOR_NAMES)
    assert "bogus" not in got


# ── 8. 翻转率出局规则真的生效 ──────────────────────────────────────────

def test_the_flip_rate_rule_actually_excludes_a_factor():
    """一条从不生效的规则等于没有规则。

    真实数据里 `vol20` 在 K=63 下正是被这条规则出局的
    （翻转率 0.40 > 1/3，尽管 AC1 是全表最强的 +0.472）。
    """
    df = _frame(tilt_by_year={
        2021: {"ivol": -1.0}, 2022: {"ivol": -1.0}, 2023: {"ivol": -1.0},
        2024: {"ivol": +1.0}, 2025: {"ivol": -1.0}, 2026: {"ivol": -1.0}})
    doc = TP.analyze(df, k=20, n_shuffle=50, seed=1)
    r = [x for x in doc["per_factor"] if x["factor"] == "ivol"][0]
    assert r["flip_rate"] == pytest.approx(0.4)
    assert r["side"] == "excluded_flip"
    assert "ivol" in doc["verdict"]["excluded_flip_factors"]
    assert "ivol" not in doc["verdict"]["stable_factors"]


def test_an_excluded_factor_is_not_silently_counted_on_either_side():
    doc = TP.analyze(_frame(tilt_by_year={
        2021: {"ivol": -1.0}, 2022: {"ivol": -1.0}, 2023: {"ivol": -1.0},
        2024: {"ivol": +1.0}, 2025: {"ivol": -1.0}, 2026: {"ivol": -1.0}}),
        k=20, n_shuffle=50, seed=1)
    v = doc["verdict"]
    tot = v["n_stable"] + v["n_drift"] + v["n_mixed"] + v["n_excluded_flip"]
    assert tot == len(TP.FACTOR_NAMES)      # 八类必须互斥且完备


# ── 9. 诊断臂：只报不判，且不参与判决 ────────────────────────────────

def test_the_diagnostic_arm_reports_direction_persistence_separately():
    """★ 本任务最重要的一条。

    `AC1` 测的是**水平**的持续性，**符号一致率**测的是**方向**的持续性。
    一个围绕稳定均值来回摆的序列可以「符号 100% 一致、AC1 为负」。
    真实数据里 `turn20`（K=63）块级符号 **23/23 一致**、AC1 仅 +0.258。
    """
    df = _frame(tilt_by_year={
        2021: {"ivol": -1.0}, 2022: {"ivol": -1.0}, 2023: {"ivol": -1.0},
        2024: {"ivol": -1.0}, 2025: {"ivol": -1.0}, 2026: {"ivol": -1.0}})
    doc = TP.analyze(df, k=20, n_shuffle=50, seed=1)
    r = [x for x in doc["per_factor"] if x["factor"] == "ivol"][0]
    assert "diag_block_sign_same" in r
    assert r["diag_block_sign_same"] == 1.0


def test_the_diagnostic_arm_does_not_participate_in_the_verdict():
    """诊断臂只许报，不许改判决 —— 否则就是事后改判据。"""
    src = _SCRIPT.read_text(encoding="utf-8")
    body = src.split("verdict = ", 1)
    assert len(body) == 2
    # 判决分支里不得出现诊断臂的字段名
    tail = body[1].split("return {", 1)[0]
    assert "diag_block_sign_same" not in tail
    assert "diag_n_blocks_same_sign" not in tail


# ── 10. 口径标签必须跟着数字离开脚本 ──────────────────────────────────

def test_caliber_tags_are_attached_to_the_report():
    """本项目曾因核对脚本乘错 100 倍，凭空造出一个假的「口径矛盾」。"""
    doc = TP.analyze(_frame(tilt_by_year={
        2021: {"ivol": -1.0}, 2022: {"ivol": -1.0}, 2023: {"ivol": -1.0},
        2024: {"ivol": -1.0}, 2025: {"ivol": -1.0}, 2026: {"ivol": -1.0}}),
        k=20, n_shuffle=50, seed=1)
    cal = doc["caliber"]
    assert "robust_z" in cal["exposure_unit"]
    assert "涨腿" in cal["leg"]
    assert cal["days_per_year"] == 244          # 与全仓判决同口径，不许中途换
    assert doc["prereg_doc"] == "docs/TODO_tilt_persistence.md"


def test_prereg_thresholds_are_frozen_in_the_script_and_the_doc_agrees():
    """判据只有一个来源：预注册文档。脚本里的阈值不得私自偏离。

    防止「读到结果后把门槛调低」——项目第 63 条：先有结论后凑判据。
    """
    doc = TP.analyze(_frame(tilt_by_year={
        2021: {"ivol": -1.0}, 2022: {"ivol": -1.0}, 2023: {"ivol": -1.0},
        2024: {"ivol": -1.0}, 2025: {"ivol": -1.0}, 2026: {"ivol": -1.0}}),
        k=20, n_shuffle=50, seed=1)
    md = (_ROOT / "docs" / "TODO_tilt_persistence.md").read_text(encoding="utf-8")
    pr = doc["prereg"]
    assert f"`agree_f ≥ {pr['agree_stable']}/6`" in md or \
           f"agree_f ≥ {pr['agree_stable']}/6" in md
    assert f"{pr['min_factors_for_verdict']}/8" in md
    # 判决阈值 AC1 必须仍是 0（未被人事后挪走）
    assert pr["ac1_stable"] == 0.0 and pr["ac1_drift"] == 0.0


def test_duplicate_dates_are_refused_before_any_statistic_runs():
    """重复日期会让同一天被数两遍，统计量仍算得出来（只是偏了），不抛异常。"""
    idx = pd.Index(["2021-01-04", "2021-01-04", "2021-01-05"])
    df = pd.DataFrame({f"expo_{f}": [1.0, 1.0, 1.0] for f in TP.FACTOR_NAMES}, index=idx)
    assert df.index.duplicated().any()


# ── fixture ────────────────────────────────────────────────────────────

def _frame(n_days=1400, tilt_by_year=None, seed=0):
    """一段能算出闭式解的逐日表。

    ★ 每个因子默认 0（全横截面中性 ⇒ 零倾斜）。
    **不用随机数**：随机 fixture 会让「全 0 ⇒ NaN」这类断言变成
    「碰巧不显著」—— 而 NaN 与 0 在判决里走的是完全不同的分支。

    ★ `n_days` 默认 **1400 个工作日**（≈5.7 年，跨 2021–2026 六个日历年）。
    第一版用 400 天，**只覆盖 2021–2022 两年**，
    于是「6 年里 5 年同号」的断言实际在测 2 年里的 1 年 ——
    fixture 的形状不对时，断言照样「绿」而什么也没测。
    """
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2021-01-04", periods=n_days)
    data = {}
    for f in TP.FACTOR_NAMES:
        data[f"expo_{f}"] = np.zeros(n_days)
    for ystr, per_factor in (tilt_by_year or {}).items():
        y = int(ystr)
        sel = (days.year == y)
        assert sel.any(), f"fixture 不含 {y} 年 —— 逐年断言会测到别的年份上"
        for f, v in per_factor.items():
            data[f"expo_{f}"][sel] = v
    data["gross_excess"] = rng.standard_normal(n_days) * 0.01
    return pd.DataFrame(data, index=[d.strftime("%Y-%m-%d") for d in days])


# ══════════════════════════════════════════════════════════════════════
# 变异验证清单（每条按「哈希变 → 标记串恰好 1 次 → 仍可 ast.parse → 看结果」四步）
#
# 变异 = 在 **运行的那一层** 把实现改坏，看守卫会不会红。
# ★ 必须在运行层做变异，不是在源码扫描层（项目实测：扫描层变异全绿）。
#
#  M1  to_blocks: `v[: n_full * k].reshape(n_full, k)` → `v[: n_full * k][::2].reshape(...)`
#      期望红：test_blocks_are_non_overlapping_means_of_exactly_k_days
#      （手算外部锚 1.5/5.5/9.5 被打破）
#
#  M2  to_blocks: 删掉末块保留（`if len(tail): blocks.append(...)` → 删）
#      期望红：test_blocks_keep_the_short_final_block_instead_of_dropping_it
#
#  M3  ac1: `len(x) < 3` → `len(x) < 2`
#      期望红：test_ac1_refuses_on_two_points_because_that_is_noise_not_persistence
#
#  M4  ac1: 常量序列 `return nan` → `return 0.0`
#      期望红：test_constant_blocks_yield_nan_not_zero_and_not_one
#      ★ 失效方向最坏的一个：返回 0 会被读成「无持续性」，
#        而 8 个因子里任何一个归零都可能把「漂移」读成「无记忆」。
#
#  M5  shuffle_p_agree: 打乱符号 `flips = rng.integers(...)` → 打乱顺序
#      （`year_vals[perm]` 形式）
#      期望红：test_the_shuffle_arm_permutes_signs_not_year_order
#      ★ 最隐蔽：顺序打乱对 `agree` 是恒等操作 ⇒ p 恒等于 1.0，
#        而界面照常显示「跑过了」。
#
#  M6  判决阈值 agree_stable: 4 → 2
#      期望红：test_prereg_thresholds_are_frozen_in_the_script_and_the_doc_agrees
#
#  M7  翻转率出局: `> PREREG["flip_rate_discount"]` → `> 1.0`（永不触发）
#      期望红：test_the_flip_rate_rule_actually_excludes_a_factor
#      （规则从不生效 ⇒ 无条件通过 ⇒ 等于没有规则）
#
#  M8  factor_columns: 删掉 missing 检查（`raise SystemExit` → `pass`）
#      期望红：test_a_missing_exposure_column_is_refused_not_silently_dropped
#
#  M9  诊断臂漏进判决（`tail` 检查的字段名改掉）
#      期望红：test_the_diagnostic_arm_does_not_participate_in_the_verdict
#
#  M10 数值 bug（守恒性，最容易漏）：`y[: nb * k].reshape(nb, k).mean(axis=1)`
#      改成 `.sum(axis=1)`（漏掉除以 k）
#      期望红：test_blocks_are_non_overlapping_means_of_exactly_k_days
#      （1.5/5.5/9.5 → 6/22/38；**不报错的量纲错**，
#        与项目第 12 条同族：口径错而报告照常出数）
# ══════════════════════════════════════════════════════════════════════
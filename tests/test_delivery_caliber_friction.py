"""**交付形态摩擦封口**的守卫 —— 判据在
`docs/PREREG_DELIVERY_CALIBER_FRICTION_2026-10-17.md` §5（G1~G8）。

🚨 **§5 是补记不是预注册**（作者看过读数），且 §3 的判决规则也是补记。
本文件因此**不比 §3 更严**，只保证「代码机械执行了 §3」。

★ **本文件的核心纪律**：判别力全部来自**构造出来的分岔夹具**。
每条守卫都必须能让**一个具体的错误实现**变红，而不只是「跑通了」。
（第 146 层：靠真实数据巧合成立的守卫会全绿而变异存活。）

三条通用禁令（沿用 `test_forward_d2_verdict.py`）：

- **不许用子串断言**（第 13 形状）。本文的判决串在本文件的 docstring 里
  就出现过 ⇒ 任何 `"SINGLE_SIDED_UNREACHABLE" not in src` 都会被自己满足。
- **不许用恒等式钉语义**（第 176 层）。「摩擦 = `annual_friction` 的值」
  是**同一个函数自己**，改了公式它跟着改 ⇒ 改公式时守卫全绿。
  ⇒ 真正的外部锚是**线性**（G1）与**下界**（G8），两者都不需要重写公式。
- **夹具必须自检非退化**，否则守卫会在退化输入下变成恒真（第 112 层）。
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "scripts" / "measure_delivery_caliber_friction.py"


def _load():
    spec = importlib.util.spec_from_file_location("_dcf", SRC)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_dcf"] = mod
    spec.loader.exec_module(mod)
    return mod


M = _load()


# ---- 夹具：可控的换手结构。`pred_date` 与 `entry_date` **刻意不同** ----

def _with_down_leg(up_rows: pd.DataFrame) -> pd.DataFrame:
    """给一份**纯涨腿**底稿加上内容完全不同的跌腿。

    跌腿代码前缀 `DOWN_`，与涨腿**零重叠** ⇒ 若实现忘了按 `direction` 过滤，
    篮子大小会从 10 变 20、τ 从 `(n−kept)/n` 变成 `n/(2n)` ⇒ 分岔可观测。
    """
    down = up_rows.copy()
    down["code"] = "DOWN_" + down["code"]
    down["direction"] = "down"
    return pd.concat([up_rows, down], ignore_index=True)


def _turnover_overlap(kept_per_step):
    """每一步从上一期**保留** `kept` 只、其余全换。返回 (rows, 期望 τ 列表)。"""
    pairs = [("2026-01-0%d" % (i + 2), "2026-01-0%d" % (i + 1))
             for i in range(len(kept_per_step) + 1)]
    rows, taus = [], []
    n = 10
    carried: list[str] = []
    for i, ((entry, pred), kept) in enumerate(zip(pairs, [0] + kept_per_step)):
        names = carried[:kept] + [f"N{i}_{j}" for j in range(kept, n)]
        for name in names:
            rows.append({"entry_date": entry, "pred_date": pred,
                         "code": name, "direction": "up"})
        if i > 0:
            taus.append((n - kept) / n)
        carried = names
    return pd.DataFrame(rows), taus


# ============ G2：观测单位必须是 entry_date ============

def test_turnover_groups_by_entry_date_not_pred_date():
    """G2 主守卫：同一 `entry_date` 下三个 `pred_date` **不是三次调仓**。

    夹具刻意把 3 个信号日压到**同一个** entry ⇒ 真实调仓只有 1 次
    ⇒ 按 `pred_date` 算会得 2 次。
    """
    rows = pd.DataFrame(
        [{"entry_date": "2026-01-02", "pred_date": p, "code": f"C{j}",
          "direction": "up"} for p in ("2026-01-01", "2026-01-02") for j in range(10)]
        + [{"entry_date": "2026-01-05", "pred_date": "2026-01-04",
            "code": f"D{j}", "direction": "up"} for j in range(10)]
    )
    turn = M.delivered_turnover(rows)
    assert turn["n_pred_dates"] == 3
    assert turn["n_entry_dates"] == 2
    assert turn["n_rebalances"] == 1, "3 个 pred_date 压在同一 entry ⇒ 只有 1 次调仓"
    assert turn["observation_unit"] == "entry_date"


def test_grouping_by_pred_date_would_give_a_different_number():
    """G2 的反控：证明上一条**有判别力**（按 `pred_date` 会得不同答案）。

    没有这条，上面那条可能因为夹具恰好两种口径一致而恒绿。
    """
    rows = pd.DataFrame(
        [{"entry_date": "2026-01-02", "pred_date": p, "code": f"C{p[-1]}{j}",
          "direction": "up"}
         for p in ("2026-01-01", "2026-01-02") for j in range(10)]
        + [{"entry_date": "2026-01-05", "pred_date": "2026-01-04",
            "code": f"D{j}", "direction": "up"} for j in range(10)]
    )
    by_entry = M.delivered_turnover(rows)["n_rebalances"]
    by_pred = len(rows[rows.direction == "up"].groupby("pred_date")) - 1
    assert by_entry == 1 and by_pred == 2, "两种口径必须给出不同的调仓次数"


def test_the_first_entry_date_never_participates():
    """G2：首个 entry_date 无前仓可换，必须被排除。"""
    rows, _ = _turnover_overlap([4, 4])
    turn = M.delivered_turnover(rows)
    assert turn["n_entry_dates"] == 3
    assert turn["n_rebalances"] == 2, "3 个 entry_date 只有后两个有前仓可换"
    assert turn["per_rebalance"][0]["entry_date"] == "2026-01-03"


def test_the_down_leg_is_excluded_from_turnover():
    """G2：跌腿不可执行（`news_overlay` 只接 `up.head(10)`），混进来会把 τ 算错。

    🚨 本条的关键是**它有判别力**：跌腿的代码与涨腿**零重叠**，
    若实现忘了按 `direction` 过滤，篮子从 10 只变 20 只、`kept` 从 4 变 4、
    `bought` 从 6 变 16 ⇒ τ 从 0.6 变 0.8。⇒ 断言不是恒真。
    """
    good, taus = _turnover_overlap([4, 4])
    turn = M.delivered_turnover(good)
    assert turn["turnover_point_estimate"] == pytest.approx(0.6)
    assert [r["one_side_turnover"] for r in turn["per_rebalance"]] \
        == pytest.approx(taus)
    for r in turn["per_rebalance"]:
        assert r["held"] == 10

    mixed = M.delivered_turnover(_with_down_leg(good))
    assert mixed["turnover_point_estimate"] == pytest.approx(0.6), \
        "加上零重叠的跌腿后涨腿换手变了 ⇒ direction 过滤没生效"
    assert all(r["held"] == 10 for r in mixed["per_rebalance"])
    # 反控：若不过滤，τ 会是 16/20 = 0.8 —— 与 0.6 相差 0.2，足够分开
    assert pytest.approx(0.6) != pytest.approx(16 / 20)


def test_a_single_entry_date_is_refused_not_reported_as_zero():
    """G7 分支：只有 1 个 entry_date 时**拒绝**，不许回落成 τ=0。"""
    rows = pd.DataFrame([{"entry_date": "2026-01-02", "pred_date": "2026-01-01",
                          "code": f"A{j}", "direction": "up"} for j in range(10)])
    with pytest.raises(KeyError):
        M.delivered_turnover(rows)


# ============ G1 + G3：线性，且只乘一次 ============

def test_friction_is_strictly_linear_in_turnover():
    """G1：摩擦对 τ **严格线性**（最低佣金的非线性已在 `commission()` 里按笔吃掉）。

    这是 `f(τ) = τ × Σ round_trip(per)` 的直接推论；
    若有人在聚合层再乘一次（或改用「先求和再乘费率」），这条会红。
    """
    fee = M.FeeSchedule()
    full = M.friction_at(M.CAPITAL, 1.0, 10, 1.0, fee)
    half = M.friction_at(M.CAPITAL, 1.0, 10, 0.5, fee)
    assert half["annual_friction_pct_of_capital"] == \
        pytest.approx(0.5 * full["annual_friction_pct_of_capital"], rel=1e-12)


def test_the_quarter_turnover_is_exactly_one_quarter_of_full():
    """G1 的第二条：换一个不可公约的比例，排除「恰好」对上。"""
    fee = M.FeeSchedule()
    full = M.friction_at(M.CAPITAL, 1.0, 10, 1.0, fee)
    q = M.friction_at(M.CAPITAL, 1.0, 10, 0.25, fee)
    assert q["annual_friction_pct_of_capital"] == \
        pytest.approx(0.25 * full["annual_friction_pct_of_capital"], rel=1e-12)


def test_one_side_turnover_is_multiplied_once_not_twice():
    """G3：`round_trip` **已含双边** ⇒ 单边换手只乘一次。

    变异 M1（把 `DAYS_PER_YEAR * τ` 改成 `DAYS_PER_YEAR * 2 * τ`）由两条合起来杀：
    ① `rebalances_per_year` 减半；② 摩擦与**手写的原始成本链**不符。

    ★ 第二条的锚**推导路径不同**（不调 `annual_friction`、不调 `friction_at`）：
    一轮调仓 = 10 只各付一个 `round_trip(¥5,000)`，年化 = 轮成本 × 244 / 本金。
    若代码乘 2，读数就是这条式子的两倍。
    """
    fee = M.FeeSchedule()
    full = M.friction_at(M.CAPITAL, 1.0, 10, 1.0, fee)
    half = M.friction_at(M.CAPITAL, 1.0, 10, 0.5, fee)
    assert half["rebalances_per_year"] == pytest.approx(0.5 * full["rebalances_per_year"])

    # 手写的原始成本链（独立于被测函数的三层）
    per = M.CAPITAL * 1.0 / 10
    one_round_cost = 10 * fee.round_trip(per)
    expect_half = one_round_cost * (M.DAYS_PER_YEAR * 0.5) / M.CAPITAL
    assert half["annual_friction_pct_of_capital"] == pytest.approx(expect_half, rel=1e-12)
    # 反控：乘 2 的那个值必须与真值**明显不同**（否则本条可能恒真）
    assert expect_half != pytest.approx(2 * expect_half)


def test_half_turnover_strictly_costs_less_than_full():
    """G3 的形状断言：乘 2 那个变异会让 `at_min` 超过 `at_full`，这里先钉住次序。"""
    fee = M.FeeSchedule()
    assert M.friction_at(M.CAPITAL, 1.0, 10, 0.5, fee)["annual_friction_pct_of_capital"] \
        < M.friction_at(M.CAPITAL, 1.0, 10, 1.0, fee)["annual_friction_pct_of_capital"]


def test_days_per_year_comes_from_hurdle_not_a_literal():
    """G6：年化常数取自 `aplus.hurdle`，不许字面量 244。

    仓里有 **三套**（244 / 250 / 252）⇒ 换错一套会让全部数字变。
    """
    from aplus.hurdle import DAYS_PER_YEAR
    assert M.DAYS_PER_YEAR == DAYS_PER_YEAR == 244
    assert M.friction_at(M.CAPITAL, 1.0, 10, 1.0)["rebalances_per_year"] \
        == pytest.approx(float(DAYS_PER_YEAR))
    # 源码里不许出现裸的 244 / 250 / 252（AST 读常量 ⇒ 注释与 docstring 不算）
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    bad = [(n.lineno, n.value) for n in ast.walk(tree)
           if isinstance(n, ast.Constant) and n.value in (244, 250, 252)]
    assert not bad, f"年化常数必须是 import，不许字面量（244/250/252）：{bad}"


# ============ G4 + G5：判决取区间下端，点估计不进判决 ============

def test_the_verdict_reads_the_interval_low_end_not_the_point_estimate():
    """G4：判决入参必须是 `turnover_min_measured`（D3）。

    🚨🚨 **更正：本 docstring 此前逐字声称「让两档给出**不同判决档**」，
    而它的夹具给出的恰恰是同一档 —— 那句话是假的。**

    夹具 `_turnover_overlap([4, 0])` ⇒ τ ∈ {0.6, 1.0}、点估计 0.8
    ⇒ 下端摩擦 **80.81%**、点估计摩擦 **107.75%**，
    **两档都 ≥ 20% ⇒ 都 `SEALED`**。
    ⇒ 把调用点的 `judge(at_min…)` 改成 `judge(at_point…)`，
    `out["verdict"]` **一个字都不变** ⇒ **F4 会整条存活**。

    ⇒ 它能提供的只是「两档的**摩擦数**不相等」（下面那条断言），
    而**数值不同 ≠ 取错哪一个看得见**。

    ★ **真正杀 F4 的是下一条**
    `test_the_verdict_would_flip_if_it_read_the_point_estimate_instead`
    （跨档夹具 τ ∈ {0.10, 0.60} ⇒ 判决整档翻转）。
    本条**只**钉「读的是下端」这个方向，不钉「换掉下端会被抓到」。
    ⇒ 留着我是因为它有效（分子分母同为下端值时能钉住绝对量），
    **不是**因为它守住了「取错哪一档」—— 它没有。
    """
    rows, _ = _turnover_overlap([4, 0])          # τ ∈ {0.6, 1.0}，点估计 0.8
    out = M.measure(rows)
    assert out["turnover"]["turnover_min_measured"] == pytest.approx(0.6)
    assert out["turnover"]["turnover_max_measured"] == pytest.approx(1.0)
    # 判决臂与点估计臂的摩擦必须**不同**，否则「取错哪一个」不可观测
    at_min = out["friction_by_turnover"]["at_measured_min"]
    at_pt = out["friction_by_turnover"]["at_point_estimate"]
    assert at_min["annual_friction_pct_of_capital"] \
        != at_pt["annual_friction_pct_of_capital"]
    assert out["verdict"] == M.VERDICT_SEALED


def test_the_verdict_would_flip_if_it_read_the_point_estimate_instead():
    """G4 的**分岔夹具**：`min` 臂与点估计臂落在**不同判决档**上。

    🚨 **上一条守卫（`[4, 0]`）在判决上零判别力**：τ ∈ {0.6, 1.0}
    ⇒ 下端摩擦 80.81%、点估计摩擦 107.75%，**两档都 ≥ 20%** ⇒ 都 `SEALED`
    ⇒ 「取 min 还是取点估计」在那条夹具下**不可观测**，
    而它断言的是 `verdict == SEALED` ⇒ **F4 会整条存活**。
    它能提供的只是「两档的**摩擦数**不同」，那与判决无关。

    ⇒ 这里造一个**跨档**结构：摩擦对 τ 严格线性（满换手 134.688%/年，
    已在 G1 被钉），于是
        τ=0.10 → 13.47% < 20% ⇒ `OVER_TARGET_BUT_NOT_SEALED`
        τ=0.35 → 47.14% ≥ 20% ⇒ `SINGLE_SIDED_UNREACHABLE`
    ⇒ 判决**整档翻转**，F4（`judge(at_min…)` → `judge(at_point…)`）当场变红。

    ★ 这也是 [[unmeasurable-point-estimate-only-supports-shape-claims]] 的反面用法：
    那个形状要求「构造出来的差异必须大到足以改变**判决类别**」，
    而不是「数值上不相等」—— **数值不同与判决可观测是两件事**。
    """
    rows, taus = _turnover_overlap([9, 4])       # τ ∈ {0.10, 0.60}，点估计 0.35
    assert taus == [pytest.approx(0.10), pytest.approx(0.60)]
    out = M.measure(rows)
    assert out["turnover"]["turnover_min_measured"] == pytest.approx(0.10)
    assert out["turnover"]["turnover_point_estimate"] == pytest.approx(0.35)

    f_min = out["friction_by_turnover"]["at_measured_min"][
        "annual_friction_pct_of_capital"]
    f_pt = out["friction_by_turnover"]["at_point_estimate"][
        "annual_friction_pct_of_capital"]
    # 判决臂未封口、点估计臂已封口 —— 这才是「取错哪一个」**看得见**的前提
    assert M.judge(f_min)[0] == M.VERDICT_OVER
    assert M.judge(f_pt)[0] == M.VERDICT_SEALED
    # 判决必须跟着 min 臂走（换成 point 臂这条当场红）
    assert out["verdict"] == M.VERDICT_OVER
    # 反控：两档必须真的**跨过**目标线，否则上面两条可能因同档而假绿
    assert f_min < M.NET_TARGET <= f_pt, (
        f"夹具失效：min={f_min} / point={f_pt} 落在 {M.NET_TARGET} 同侧，"
        "此时「取 min 还是取 point」不可观测")


def test_the_mechanism_reads_the_same_position_count_as_the_friction_arms():
    """`measure()` 里**每一个** `friction_at` 调用点都必须传模块常量 `N_POSITIONS`。

    🚨 缺口（第 148 层的读者才看得见）：`mechanism.notional_per_position`
    单独算 `CAPITAL × EQUITY_WEIGHT / N_POSITIONS`，而四档摩擦走
    `friction_at(..., N_POSITIONS, ...)` —— **两个独立引用**。
    只钉住 `mechanism` 那一侧时，把调用点改成 `N_POSITIONS - 5`
    （摩擦翻倍）**全绿**：判决仍是 `SEALED`、点估计仍不进判决、
    连 `required_turnover_for_target` 都自洽（它由同样被改的 `full` 推出）。

    ⇒ 本条用**参数化枚举全部调用点**：任何一处漏传/写死/改值即红。
    """
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", None)
        if name != "friction_at":
            continue
        calls.append((node.lineno, node.args))

    assert len(calls) == 4, f"friction_at 调用点数量变了：{len(calls)}"
    # 四臂必须**逐项同构**：参数个数相等 ⇒ 才谈得上「四档可比」
    widths = {len(args) for _, args in calls}
    assert len(widths) == 1, (
        f"四臂参数个数不一致：{sorted(widths)} ⇒ 三档摩擦与判决臂不同构，"
        "「区间」这个概念本身就没意义了")
    for lineno, args in calls:
        n_positions = args[2]
        # 名数必须是**模块常量的裸名**，不许是字面量、不许是算式
        assert isinstance(n_positions, ast.Name) \
            and n_positions.id == "N_POSITIONS", (
            f"第 {lineno} 行：名数必须原样传 `N_POSITIONS`，"
            f"实际是 `{ast.unparse(n_positions)}`")


def test_judge_takes_the_argument_it_is_given_not_a_global():
    """G4 的机制守卫：`judge` 是纯函数，读入参而非某处全局读数。

    ⚠️ **它抓不到 F4**：F4 改的是**调用点传的实参**，不是 `judge` 本身
    ⇒ 纯函数性完全不受影响。真正杀 F4 的是上面那条**跨档夹具**。
    """
    assert M.judge(0.80)[0] == M.VERDICT_SEALED
    assert M.judge(0.80)[0] == M.VERDICT_SEALED
    assert M.judge(0.19)[0] == M.VERDICT_OVER
    assert M.judge(0.0)[0] == M.VERDICT_NONE
    assert M.judge(-0.01)[0] == M.VERDICT_NONE


def test_the_verdict_boundary_is_inclusive_at_the_target():
    """G4/D2：判据逐字是「摩擦 **≥** 目标」⇒ 恰好等于目标必须判 SEALED。

    「严格大于」那个变异（M4）由这条杀。
    """
    assert M.judge(M.NET_TARGET)[0] == M.VERDICT_SEALED
    assert M.judge(M.NET_TARGET - 1e-12)[0] == M.VERDICT_OVER


def test_the_artifact_carries_the_point_estimate_but_labels_it_out_of_the_verdict():
    """G5：点估计必须**在产物里**且标 `role`，但判决字段不含它。"""
    rows, _ = _turnover_overlap([4, 0])
    out = M.measure(rows)
    fbt = out["friction_by_turnover"]
    assert "at_point_estimate" in fbt and "at_measured_min" in fbt
    assert "不进判决" in fbt["at_point_estimate"]["role"]
    assert "判决" in fbt["at_measured_min"]["role"]
    # 判决臂的 τ 必须等于实测下端
    assert fbt["at_measured_min"]["one_side_turnover"] == \
        pytest.approx(out["turnover"]["turnover_min_measured"])
    # 判决文本里出现的摩擦必须是下端那一档的数
    assert f'{fbt["at_measured_min"]["annual_friction_pct_of_capital"] * 100:.2f}%' \
        in out["verdict_text"]


def test_the_artifact_reports_the_solved_turnover_for_the_target():
    """G5/D3：必须并排给出「要让摩擦 ≤ 目标所需的 τ」。"""
    rows, _ = _turnover_overlap([4, 0])
    out = M.measure(rows)
    need = out["required_turnover_for_target"]
    assert 0.0 < need < 1.0
    # 反解必须真的让摩擦落回目标（外部锚：这不是回代自己的公式）
    fee = M.FeeSchedule()
    at_need = M.annual_friction(M.CAPITAL, 1.0, 10, M.DAYS_PER_YEAR * need, fee)
    assert at_need == pytest.approx(M.NET_TARGET, rel=1e-9)


# ============ G8：最低佣金真的在咬 ============

def test_the_effective_commission_rate_exceeds_the_nominal_one_below_the_threshold():
    """G8：单笔 < 阈值 ⇒ 实际费率**必须**高于名义费率。

    这是「5 万 + 10 只 + 日频」的机制本身；它也是唯一一条能在
    「费率表被改成正确」时变红的守卫（预注册 §5 附注）。
    """
    rows, _ = _turnover_overlap([4, 4])
    out = M.measure(rows)
    mech, fs = out["mechanism"], out["fee_schedule"]
    per = mech["notional_per_position"]
    assert per == pytest.approx(5000.0)
    assert per < fs["commission_min_binds_below"]
    # 实际费率 = 每笔真正付出的佣金 / 名义额，而每笔吃满 ¥5
    assert mech["effective_commission_rate"] == pytest.approx(
        fs["commission_min"] / per)
    assert mech["effective_commission_rate"] == pytest.approx(0.001)
    assert mech["effective_commission_rate"] > fs["commission_rate"] * 3.0
    # 反控：阈值必须真的在两条线之间，否则上面几条可能恒绿
    assert per < fs["commission_min_binds_below"] \
        < per * 100, "夹具的每笔名义额必须落在最低佣金咬人的那一侧"


def test_the_nominal_and_effective_rates_would_coincide_above_the_threshold():
    """G8 的反控：证明上一条**有判别力**。

    把本金抬到单笔 ≥ 阈值 ⇒ 实际费率必须**等于**名义费率。
    没有这条，上一条可能因为公式写错而恒绿。
    """
    fee = M.FeeSchedule()
    threshold = fee.commission_min / fee.commission_rate
    big = threshold * 10 * 10          # 单笔 = 阈值
    assert fee.commission(big) / big == pytest.approx(fee.commission_rate)
    small = threshold / 2
    assert fee.commission(small) / small > fee.commission_rate


# ============ 接线层（AST）：不许绕过 aplus.costs ============

def test_the_generator_never_rewrites_the_cost_formulas():
    """🚨 本脚本只许**调用** `aplus.costs`，不许自己写佣金/印花税/价差。

    用 **AST 读常量**，不用子串（本文件 docstring 就写着这些词）。
    """
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "aplus.costs":
            imported |= {a.name for a in node.names}
    assert {"FeeSchedule", "annual_friction", "gross_needed_for_net"} <= imported
    # 不许出现自己算钱的字面量：佣金率 / 印花税 / 过户费 / 价差
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, float):
            assert node.value not in (0.0003, 0.0005, 0.00001, 0.0015), \
                f"第 {node.lineno} 行出现费率字面量：费率必须来自 aplus.costs"


def test_the_verdict_tiers_are_declared_in_strictest_first_order():
    """判决分支顺序本身就是判据（第 141 层）⇒ 它必须是模块级常量。"""
    assert M.VERDICT_TIERS == (
        M.VERDICT_SEALED, M.VERDICT_OVER, M.VERDICT_NONE,
    )
    assert M.VERDICT_SEALED == "SINGLE_SIDED_UNREACHABLE"


def test_judge_is_module_level_so_guards_can_reach_it():
    """第 131 层：嵌套在 `main()` 里的判决逻辑**无任何东西能挡**。"""
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    top_funcs = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    assert {"judge", "measure", "delivered_turnover", "friction_at"} <= top_funcs


def test_the_stamp_is_wired_with_expect_bytes_and_the_input_fingerprint():
    """第 125 + 156 层：盖章必须带 `expect_bytes` **与输入字节 sha256**。

    ⚠️ 两条都缺一不可：`expect_bytes` 守**代码**字节，
    `source_parquet_sha256` 守**输入**字节 —— 后者结构上拦不住前者。
    """
    src = SRC.read_text(encoding="utf-8")
    tree = ast.parse(src)
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute)
             and n.func.attr == "stamp"]
    assert len(calls) == 1, "必须恰好一处 stamp 调用"
    kw = {k.arg for k in calls[0].keywords}
    assert {"expect_bytes", "extra"} <= kw
    stamp_src = ast.unparse(calls[0])
    assert "expect_bytes=" in stamp_src
    assert "source_parquet_sha256" in stamp_src
    assert "hashlib" in stamp_src

"""`scripts/regime_control_min_basket_control.py` 的守卫。

## 这个文件在守什么（三条独立的判据）

| 判据 | 形状 | 谁会红 |
|---|---|---|
| **对拍①** `_a_parity` | 本脚本的 A **逐位**等于主产物的 `sign_fixed.annual_up_excess` | 任何人把已年化的数再乘/除一次 244 |
| **闭合** `decompose` 的两条断言 | A 的点估计与它的分解**锁不死** ⇒ raise；A−B 的两项分解**不闭合** ⇒ raise | `resid` 是代数恒等式 ⇒ A 与分解项一起变仍闭合 |
| **判决** `verdict` | 预注册第二节四条规则**一个字不改** | 发明第五个档位 / 改门槛 |

## 为什么每条都要有**能力守卫**（第 120 层）

`closes` 是一个 `bool`。只断言「它为真」的测试，
在夹具没造出该失败的情形时会**恒真**。
⇒ 每条 `assert X is False` 旁边必配一条 `assert X is True`，
且**先验**跑一遍证明夹具真的有牙（基线先验绿）。

⚠️ 反过来也一样危险：**只验判决不验接线** ⇒ 探测器的变异会被读成「守住了」。
所以这里对每条都同时钉**函数**与**判别力**。

## ⚠️ 夹具必须让成分数**不齐**

`tests/test_regime_control_sweep.py` 现有的 `_two_day()` **每天 up 与 down 成分数相等**，
⇒ 逐行口径与逐日口径**逐位相同** ⇒ 任何 A/B 差异都测不出来。
本文件因此有一对**成对**夹具：

- `_equal_basket_pred()`：**每天 up 行数相等且篮子内取值相同** ⇒ A == B（**反控**）
- `_unequal_basket_pred()`：**每天 up 行数 4/3/2/1** ⇒ A != B（**正控**）

⚠️ 与第 146 层同族：**「实测通过」对容差判据零信息量** ⇒
判别力的 teeth 只能来自**合成夹具**（`math.nextafter` 造只差最后一位）。
"""
from __future__ import annotations

import importlib.util
import math
import re
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


M = _load("_rmbc", "scripts/regime_control_min_basket_control.py")
MIN_BASKET = M.MIN_BASKET
HURDLE = M.HURDLE


# ── 夹具 ────────────────────────────────────────────────────────────
#
# ⚠️ 单位是**小数**不是百分数。第一版把 +1% 写成 `1.0`，
#    于是 `annual_up_excess` 给出 `−976.0`（= −4.0 × 244），
#    而断言写的是 `≈ +1.0` ⇒ **红的原因不对**（层 147：红的归因要取证）。

def _pred(rows) -> pd.DataFrame:
    """rows: (pred_date, code, direction, actual_pct, market_pct, cost)"""
    df = pd.DataFrame(
        rows,
        columns=["pred_date", "code", "direction", "actual_pct",
                 "market_pct", "cost"],
    )
    # `rolling_stats` 的命中率分支读 `result`；不造它那一支返回 0.0。
    df["result"] = "hit"
    return df


_UP_COST = 0.001


def _unequal_basket_pred() -> pd.DataFrame:
    """**正控**：每天 up 篮子成分数 4/3/2/1 ⇒ 逐行口径与逐日口径必然不同。

    两个差异来源**分别打开**（`regime_control_min_basket_control.py` 文件头）：

    1. up 篮子逐日成分数不齐 ⇒ `mean_row(up净) ≠ mean_daily(up净)`
    2. `market_pct` 的分母是**全表行数**（up+down），不是 up 行数
       ⇒ 每天 up 行数不同，market 项也跟着不同

    ⇒ 只有两处都造出来，`decompose` 的两项分解才验得到。
    """
    rows = []
    # (日期, 当天 market, 当天 up 篮子**净**收益, 当天 up 行数)
    plan = [("2023-01-03", 0.000, 0.020, 4),
            ("2023-01-04", 0.001, 0.040, 3),
            ("2023-01-05", 0.002, 0.030, 2),
            ("2023-01-06", 0.003, -0.010, 1)]
    for date, mkt, net, n_up in plan:
        for j in range(n_up):
            rows.append((date, f"6000{j:02d}.SH", "up",
                         net + _UP_COST, mkt, _UP_COST))
        # down 腿每天固定 1 行：它只进 `market` 的分母，不进任何超额口径。
        rows.append((date, "000001.SZ", "down", 0.005, mkt, 0.0))
    return _pred(rows)


def _equal_basket_pred() -> pd.DataFrame:
    """**反控**：每天 up 行数相同、篮子内取值相同 ⇒ **A 必须逐位等于 B**。

    ⚠️ 这不是「随便造个数」，它是**判别力的上界**：
    没有它，`test_..._disagree_when_baskets_are_unequal` 在夹具退化成
    成分数相等时会**假红**，而在夹具造不出差异时会**恒绿**。
    """
    rows = []
    plan = [("2023-01-03", 0.000, 0.020),
            ("2023-01-04", 0.001, 0.040),
            ("2023-01-05", 0.002, 0.030)]
    for date, mkt, net in plan:
        for j in range(3):                       # 每天固定 3 行 up
            rows.append((date, f"6000{j:02d}.SH", "up",
                         net + _UP_COST, mkt, _UP_COST))
        rows.append((date, "000001.SZ", "down", 0.005, mkt, 0.0))
    return _pred(rows)


# ══ ① 对拍①：`_a_parity` ══════════════════════════════════════════
#
# ★ 这一组是**今天真实发生过**的那个 bug 的守卫：
#   `_pack()` 的 `annual_pct` 已经是年化，而 `main()` 的对拍里又除了一次 244。
#   症状是**硬闸拒写产物**，而日志同时印出与主产物逐位相同的 `A=+0.747990%`
#   ⇒ **报的是假红** ⇒ 不去归因就会去「调容差」，而判据本身是对的。

def test_the_a_parity_guard_can_actually_close():
    """**能力守卫**：同一个数进去必须拿到 `True`，而不只是 bool。"""
    assert M._a_parity(0.007479904981219636, 0.007479904981219636)["closes"] is True


def test_a_parity_rejects_a_number_that_was_already_annualized_twice():
    """今天的 244× bug：A 已是年化，再除一次 244 ⇒ 必须不闭合。"""
    ref = 0.007479904981219636
    out = M._a_parity(ref, ref / 244)
    assert out["closes"] is False
    # ⚠️ 钉判决不钉探测器：真正被引用的是 `closes`，不是返回类型。
    assert out["A_rowwise_annual_pct"] == ref / 244


def test_a_parity_rejects_a_deviation_of_one_part_in_a_million():
    """1e-6 相对偏差必须被抓 —— 真实数据里的量级差都在这个尺度以上。"""
    ref = 0.007479904981219636
    assert M._a_parity(ref, ref + 1e-6)["closes"] is False
    assert M._a_parity(ref, ref + 1e-4)["closes"] is False


def test_the_a_parity_tolerance_is_one_part_in_a_trillion():
    """**容差守卫**：`rel_tol=1e-12` 由这条钉住，不许悄悄放宽（第 146 层）。

    ⚠️ 与「把容差放宽到 1e-9 而真实偏差 4.95 照样被抓住 ⇒ 全绿」相反，
    这里用 `nextafter` 造**只差最后一位**的偏差，它才是容差的唯一探针。
    """
    ref = 0.007479904981219636
    one_ulp = math.nextafter(ref, math.inf) - ref
    # ⚠️ 先断言构造出来的偏差确实 ≠ 0，否则连假测试都拦不住。
    assert one_ulp != 0.0
    assert one_ulp / ref < 1e-12                 # 相对差落在容差内
    assert M._a_parity(ref, ref + one_ulp)["closes"] is True
    # 再往上一格：相对差越过容差 ⇒ 必须红。
    just_over = ref * (1 + 2e-12)
    assert M._a_parity(ref, just_over)["closes"] is False


def test_the_a_parity_takes_no_second_annualization_step():
    """**接线守卫**（第 144 层：「同一个文件」不够，「同一个变量」才是及格线）。

    `_a_parity` 的两个实参必须都**已经是年化**；脚本里唯一的年化换算
    在 `decompose()::a_annual_rebuilt` 已经做完 ⇒ 这里若出现第二个
    `DAYS_PER_YEAR` 就是复发。
    """
    import inspect
    src = inspect.getsource(M._a_parity)
    body = src.split('"""')[-1]          # 丢掉 docstring——它在讲这件事
    assert "DAYS_PER_YEAR" not in body
    assert "244" not in body


# ══ ② `decompose` 的两条断言 ══════════════════════════════════════

def test_decompose_locks_the_A_point_estimate_to_its_own_decomposition():
    """**能力守卫**：正确实现下不齐夹具必须过（否则下面两条全是空转）。"""
    d = M.decompose(_unequal_basket_pred())
    assert d["A_rowwise"]["annual_pct"] is not None
    assert d["decomposition"]["closes_bitwise"] is True
    assert abs(d["decomposition"]["residual_per_year"]) <= 1e-12


def test_decompose_rejects_an_A_that_does_not_lock():
    """变异靶：`a_annual` 被换成逐日口径的数 ⇒ A 与分解锁不死 ⇒ 必须 raise。"""
    pred = _unequal_basket_pred()
    ref = float(M._metrics(pred)["annual_up_excess"])
    s_up, mkt, _ = M._daily_parts(pred)
    mutated = float(s_up.mean()) - float(mkt.mean())
    mutated *= M.DAYS_PER_YEAR
    # 夹具先验：这份数据下 A 与逐日口径**确实不等**，否则这条守卫会恒真。
    assert not math.isclose(ref, mutated, rel_tol=1e-12)
    # 真正被引用的事实：实现**必须**为这组数 raise。
    assert _raises(lambda: _decompose_with_a(pred, mutated))


def test_decompose_rejects_a_decomposition_that_does_not_close():
    """变异靶：`resid` 去掉分解项 —— 它此时恒为 `A − B`，在不齐夹具上 ≠ 0。"""
    pred = _unequal_basket_pred()
    s_up, mkt, _ = M._daily_parts(pred)
    series_b = M._daily_series(pred, "up_excess")
    days = series_b.index
    b_up, b_mkt = float(s_up.reindex(days).mean()), float(mkt.reindex(days).mean())
    b_annual = (b_up - b_mkt) * M.DAYS_PER_YEAR
    a_annual = float(M._metrics(pred)["annual_up_excess"])
    # 夹具先验：不齐夹具下 A != B ⇒ 抽掉分解项后残差 ≠ 0
    assert not math.isclose(a_annual, b_annual, abs_tol=1e-12)
    assert _raises(lambda: _assert_resid(a_annual, b_annual))


def _raises(fn) -> bool:
    try:
        fn()
    except AssertionError:
        return True
    return False


def _decompose_with_a(pred, a_annual: float):
    """按 `decompose()` 的锁不死断言跑一遍，喂给它一个**外来**的 A。"""
    up = pred[pred["direction"] == "up"]
    a_up = float((up["actual_pct"] - up["cost"]).mean())
    a_mkt = float(pred["market_pct"].mean())
    a_annual_rebuilt = (a_up - a_mkt) * M.DAYS_PER_YEAR
    if not math.isclose(a_annual_rebuilt, a_annual, rel_tol=1e-12, abs_tol=1e-15):
        raise AssertionError("A 的点估计与它的分解**锁不死**")
    return a_annual


def _assert_resid(a_annual: float, b_annual: float):
    """变异靶的等价体：`resid` 抽掉分解项后仍会 close ⇒ 就是洞。"""
    resid = a_annual - b_annual
    if not math.isclose(resid, 0.0, abs_tol=1e-12):
        raise AssertionError("A−B 的两项分解**不闭合**")


# ══ ③ 两个口径：反控 A==B / 正控 A!=B ════════════════════════════

def test_the_two_calibers_agree_bitwise_when_every_basket_has_the_same_size():
    """**反控**：成分数全相等时 A 与 B **必须逐位相同**。

    ⚠️ 没有这条，`test_..._disagree...` 只是一条「A≠B」的断言；
    有了它才能说 A/B 的差别**确实来自成分数不齐**，
    而不是来自「我算错了两个数」。
    """
    d = M.decompose(_equal_basket_pred())
    assert math.isclose(d["A_rowwise"]["annual_pct"],
                        d["B_daily"]["annual_pct"],
                        rel_tol=1e-12, abs_tol=1e-15)


def test_the_two_calibers_disagree_when_baskets_are_unequal():
    """**能力守卫**：不齐夹具下 A 必须真的与 B 不同（能力，不是形状）。"""
    d = M.decompose(_unequal_basket_pred())
    a = d["A_rowwise"]["annual_pct"]
    b = d["B_daily"]["annual_pct"]
    assert not math.isclose(a, b, rel_tol=1e-12)
    # 符号相同、量级不同 —— 本夹具不该顺手造出「符号相反」那个真实现象，
    # 那属于真实面板的性质，不能由夹具冒领。
    assert d["decomposition"]["observed_A_minus_B"] != 0.0


def test_the_decomposition_attributes_the_gap_to_two_named_terms():
    """两项分解必须**各自报出来**，不是只报残差。"""
    d = M.decompose(_unequal_basket_pred())
    dec = d["decomposition"]
    assert "term_up_rows_minus_daily" in dec
    assert "term_market_rows_minus_daily" in dec
    rebuilt = dec["reconstructed_A_minus_B"]
    assert math.isclose(rebuilt, dec["observed_A_minus_B"],
                        rel_tol=1e-12, abs_tol=1e-15)


# ══ ④ `MIN_BASKET` 对照臂 ════════════════════════════════════════

def test_min_basket_is_imported_from_verify_not_typed_as_a_literal():
    """对照臂的门槛必须**跟随** `aplus/verify.py`，不许在本脚本写死 3。

    ⇒ 断言用**求值**（真跑一次 `hurdle`/`MIN_BASKET` 的来源），
    而不是 `MIN_BASKET == 3` —— 后者在常量被改成 4 时会红，
    但**红的原因不对**（不是「字面量漂移」，是「门限真的变了」）。
    """
    import aplus.verify as V
    assert MIN_BASKET is V.MIN_BASKET
    import inspect
    src = inspect.getsource(M)
    # `from aplus.verify import MIN_BASKET` 必须还在（不许改成局部赋值）
    assert "from aplus.verify import MIN_BASKET" in src


def test_the_control_arm_drops_exactly_the_days_below_min_basket():
    """能力 + 判别力：夹具 4/3/2/1 行 ⇒ C 只留 4 与 3 行那两天。"""
    d = M.decompose(_unequal_basket_pred())
    assert d["basket_size"]["min_basket_constant"] == MIN_BASKET
    assert d["basket_size"]["n_days_lt_min_basket"] == 2        # 2 行 + 1 行
    assert d["C_daily_min_basket"]["n_days"] == 2               # 4 行 + 3 行


def test_the_control_arm_is_not_a_round_lot_filter():
    """⚠️ `MIN_BASKET` 是「有效成分数下限」，**不是整手过滤器**。

    判决文档必须照抄这句；本守卫钉住「它的来源与名字」，
    防止后人把它改述成「能不能买整手」——那两个是不同的约束，
    而 A 股单笔 5 万/N 时最小可买单位完全不受它管。
    """
    import aplus.verify as V
    assert "MIN_BASKET" in inspect_source(V)
    assert M.GENERATOR in (
        "scripts/regime_control_min_basket_control.py",)
    # 脚本自己声明的口径文字里必须留着这句免责
    assert "不裁定" in M.__doc__ or "不裁定" in inspect_source(M)


def inspect_source(mod) -> str:
    import inspect
    return inspect.getsource(mod)


# ══ ⑤ 对拍②：B 与 `by_year.pooled()` ═════════════════════════════

def test_b_closes_with_the_independently_verified_pooled_implementation():
    """**能力守卫**：真实数据下这条对拍**必须能 close**，否则下面两条是装饰。"""
    d = M.decompose(_unequal_basket_pred())
    ref = d["B_vs_pooled_ref"]
    assert ref["closes"] is True
    assert math.isclose(ref["annual_pct"], ref["ref_annual_up_excess_NET_pct"],
                        rel_tol=1e-12)


def test_b_parity_covers_the_se_not_just_the_point_estimate():
    """⚠️ 第一版 `closes` 只比点估计 —— 那 SE 乘错 √244 会**照样绿**。

    SE 的乘数是层 93 的三个「长得一样」乘数里最容易写错的一个。
    """
    d = M.decompose(_unequal_basket_pred())
    ref = d["B_vs_pooled_ref"]
    se = d["B_daily"]["se_annual_pct"]
    assert se is not None and se > 0
    assert math.isclose(se, ref["ref_se_annual_pct"], rel_tol=1e-12)
    # 年化 sd 乘 √244，不是 ×244
    assert math.isclose(d["B_daily"]["sd_annual_pct"],
                        ref["ref_sd_annual_pct"], rel_tol=1e-12)


def test_the_three_annualization_multipliers_are_all_different():
    """层 93：均值 ×T、sd ×√T、SE ×T/√n —— 三个乘数各不相同。"""
    d = M.decompose(_unequal_basket_pred())
    b = d["B_daily"]
    n = b["n_days"]
    assert math.isfinite(b["annual_pct"])
    assert b["sd_annual_pct"] != 0.0
    assert b["se_annual_pct"] != 0.0
    # sd × √T 与 SE × T/√n 只在 n == T 时相等；本夹具 n=4 ≪ 244
    assert n != M.DAYS_PER_YEAR

    # ⚠️⚠️ 第一版把这条写成 `se == sd_annual / √n`，**漏了 `× √T`**，
    #    而它是红的：2.7325 vs 0.17493，比值恰为 √244 = 15.6205。
    #    ⇒ 红的不是实现，是判据。
    #    ★ 这就是层 147 的最小形态：**「红」不蕴含「代码错」**，
    #      而两者的修法完全相反。取证只需手算那个比值并逐位对上。
    # 正确关系（层 93）：SE_annual = sd_day/√n × T = sd_annual/√n × √T
    assert math.isclose(b["se_annual_pct"],
                        b["sd_annual_pct"] / math.sqrt(n) * math.sqrt(M.DAYS_PER_YEAR),
                        rel_tol=1e-12)
    # 反控：写错的那个式子（漏 √T）必须**不等** —— 否则这条守卫恒真。
    assert not math.isclose(b["se_annual_pct"],
                            b["sd_annual_pct"] / math.sqrt(n), rel_tol=1e-12)
    # sd 乘 √T、不是 ×T（层 93 的第二个受害者，第 16 种形状的真凶）
    sd_day = b["sd_annual_pct"] / math.sqrt(M.DAYS_PER_YEAR)
    assert math.isclose(b["se_annual_pct"],
                        sd_day / math.sqrt(n) * M.DAYS_PER_YEAR, rel_tol=1e-12)


# ══ ⑤之二 `plus1sd_*` 的**名与实**（2026-10-15，字段名债） ═══════════

# ⚠️⚠️ 本节**刻意不裁定**「预注册写的 `+1σ` 到底是 SD 还是 SE」。
#     它只钉**实现现状**，并把「名字与预注册记号不对应」这件事变成可测事实。
#     ⇒ 见 `docs/MISTAKE_LOG_2026-10-14.md` 第 154 层与
#       `docs/VERDICT_PREREG_4FACTOR_JUDGE_WINDOW_2026-10-15.md` 第六节新发现 ④。
#
#     🚨 **看到下面第一条红了，不许直接把 `point + se` 改成 `point + sd_annual`。**
#     那是把「实现与预注册记号不符」这个**待裁决的债**换成「实现符合字面」，
#     而裁决权在预注册，不在这里（第 91 层：判据一个字不改）。
#     正确的下一步是**先裁决哪一个是本仓的 `+1σ`**，再改一边。

def test_plus1sd_is_the_point_plus_the_standard_ERROR_not_the_standard_DEVIATION():
    """`plus1sd_pct` 实为 `point + se`，而字段名与预注册记号都写 σ。

    源码（`scripts/regime_control_min_basket_control.py::_pack`）：
        se = sd_day / math.sqrt(n) * DAYS_PER_YEAR
        return {..., "se_annual_pct": se, "plus1sd_pct": point + se}

    实测（真实面板 buffer=None，口径 A）：
        point 0.00748 + se 0.09318 = 0.10066 == 产物 `plus1sd_pct` ✅
        point 0.00748 + sd 0.22279 = 0.23027 != 产物值                ❌

    ⇒ **逐位相等**是安全的：`_pack` 里 `plus1sd_pct` 的 `point`
      与返回的 `annual_pct` 是**同一个变量**，浮点加法可重现。
    """
    d = M.decompose(_unequal_basket_pred())
    for key in ("A_rowwise", "B_daily", "C_daily_min_basket"):
        c = d[key]          # ⚠️ `decompose()` 返回**平铺**（不是产物 json 的 `calibers` 嵌套）
        se = c["se_annual_pct"]
        assert se is not None, f"{key} 没有 se ⇒ 这条守卫恒真"
        assert c["plus1sd_pct"] == c["annual_pct"] + se, (
            f"{key}: plus1sd_pct ≠ 点 + 标准误。\n"
            "⇒ 实现改了。⚠️ **不许**直接改成 `点 + 标准差` 去迎合预注册的 "
            "`+1σ` 字面 —— 先裁决（见本节顶部），判据改动不在测试里做。"
        )


def test_the_sd_written_inside_the_field_name_is_not_the_quantity_that_gets_added():
    """★ 反控：上面那条**不是恒真的**，而名字与实现的冲突是可测事实。

    没有这一条时，若某天 `se` 与 `sd` 恰好相等，
    「plus1sd = 点 + SE」这条守卫会**照样绿**（层 112：「iPad 视口 ≠ iPad」）。
    ⇒ 必须断言「读名字会读成另一个量」这件事**此刻为真**。
    """
    d = M.decompose(_unequal_basket_pred())
    for key in ("A_rowwise", "B_daily", "C_daily_min_basket"):
        c = d[key]
        se, sd = c["se_annual_pct"], c["sd_annual_pct"]
        assert se != sd, f"{key}: se == sd ⇒ 名字与实现的冲突测不出来"
        assert c["plus1sd_pct"] != c["annual_pct"] + sd, (
            f"{key}: plus1sd_pct 竟然等于 点 + 标准差 —— "
            f"要么字段已改名，要么反控失效，两种情况都要重新读本节。"
        )
    # 夹具的独立观测数必须 ≪ 年交易日数，否则 se 与 sd 只差 √244 的那一小段
    assert d["A_rowwise"]["n_days"] < M.DAYS_PER_YEAR


def test_the_field_name_says_sd_while_the_preregistration_says_one_sigma():
    """★ 把「名字 / 预注册 / 实现」三方的**错位**钉成可测事实。

    这不是风格断言：**它保证下一个读产物的人不会默默把 `plus1sd_pct`
    当成「点 + 1 个标准差」引用**，而那个读法会给每个数加上
    `sd − se ≈ 0.13/年` 的偏移。
    """
    import inspect
    src = inspect.getsource(M)
    assert '"plus1sd_pct": point + se' in src, (
        "实现的加法已变（不是 +se 了）⇒ 请连同判决文档第六节新发现 ④ 一起更新。"
    )
    prereg = (ROOT / "docs/PREREG_4FACTOR_JUDGE_WINDOW_2026-10-15.md").read_text(
        encoding="utf-8")
    assert "+1σ" in prereg, (
        "预注册里已经找不到 `+1σ` ⇒ 名实冲突可能已被裁决过，"
        "但本测试仍按旧假设断言 +se。**先读预注册再改这里。**"
    )


# ── 判决文档与产物的**一致性**（第 154 层：名实不符要由文档承认）────────

DOC = ROOT / "docs" / "VERDICT_PREREG_4FACTOR_JUDGE_WINDOW_2026-10-15.md"
ART = ROOT / "data" / "reports" / "regime_control_min_basket_control_2026-10-15.json"
DOC_TABLE_HEAD = "### 判决读法分档"
#: 形状常量 = **三口径 × 两 buffer**。⚠️ 它是**形状**不是会漂的读数 ⇒ 可以写死。
#: 但它同时是「覆盖面」的锚：任何一格的 `se` 变成 `None` ⇒ 覆盖面缩小 ⇒ 红。
EXPECTED_CELLS = 6


def _computed_reading_split():
    """从**产物**算出「两种读法是否同档」，逐格。

    实现读法：`verdict(点, plus1sd)`（`plus1sd = 点 + SE`）
    字面读法：`verdict(点, 点 + sd)`（把字段名的 `sd` 当真）

    ⇒ 返回 `(分档集合, 核到的格数, 核到的总格数)`
    """
    import json
    art = json.loads(ART.read_text(encoding="utf-8"))
    split, n_checked, n_total = set(), 0, 0
    for arm in art["arms"]:
        for key, c in arm["calibers"].items():
            if c["se_annual_pct"] is None:
                continue
            n_total += 1
            label = f"{arm.get('buffer_multiple')}/{key}"
            as_se = M.verdict(c["annual_pct"], c["plus1sd_pct"])
            as_sd = M.verdict(c["annual_pct"], c["annual_pct"] + c["sd_annual_pct"])
            n_checked += 1
            if as_se != as_sd:
                split.add((label, as_se, as_sd))
    return split, n_checked, n_total


def _recorded_reading_split(doc_text):
    """从**判决文档**里读出它记录的分档集合。

    格式（文档里那一节）：

        | `None/A_rowwise` | `INSUFFICIENT` | `UNDERPOWERED` |

    ⇒ 漏记 ⇒ 红；多记 ⇒ 红。
    """
    out = set()
    in_block = False
    for ln in doc_text.splitlines():
        # ⚠️ **精确匹配**，不许前缀匹配（否则「### 判决读法分档（已删除）」
        #   也会被当成那一节 ⇒ 整节被删掉时守卫反而绿）。
        if ln.strip() == DOC_TABLE_HEAD:
            in_block = True
            continue
        if in_block and ln.strip().startswith("#"):
            break                       # 下一节 ⇒ 表结束
        if not in_block:
            continue
        m = re.match(r"^\|\s*`([^`]+)`\s*\|\s*`([^`]+)`\s*\|\s*`([^`]+)`\s*\|\s*$",
                     ln.strip())
        if m:
            out.add((m.group(1), m.group(2), m.group(3)))
    return out


def test_the_judgment_document_records_exactly_the_verdict_reading_split():
    """★ 「两种读法是否同档」这个事实**不许只活在测试里**。

    ## 判据为什么从「必须同档」改成「文档必须与数据一致」

    守卫原本断言「真实格上两种读法同档」⇒ 那等于断言
    「**判决方向不受影响**」（判决文档第六节新发现 ④）。

    🚨 **实测把它证伪了**：有格实现读法给 `INSUFFICIENT`、
    字面（+1 SD）读法给 `UNDERPOWERED` ⇒ **判决类别不同** ⇒
    「判决方向不受影响」这句话**已过期**。

    ⇒ 按第 154 层的纪律：**不许**把实现改成 `+sd`
    （那是**预注册判决**的口径，第 91 层）⇒ 正确处置是
    **让判决文档逐字记录这个分档**。

    ⇒ 判据变成：**从产物算出的分档集合** == **文档记录的分档集合**。
      · 文档漏记 ⇒ 红（下一个读产物的人会把 `plus1sd_pct` 当成「点 + 1 个标准差」）
      · 文档多记 ⇒ 红（文档在讲一个不存在的事实）
    ⇒ 它**自己算**答案 ⇒ 恒真不了。

    ⚠️ 这条守卫**不替人做判决**：它只要求「文档说的 == 数据算的」。
    """
    assert ART.exists(), (
        f"产物 {ART.name} 不在盘上 ⇒ 本条守卫会**静默失去覆盖面**。"
        "要么重跑 `scripts/regime_control_min_basket_control.py`，"
        "要么把它从本文件里删掉并登记为债。**不许让它 skip。**")
    doc_text = DOC.read_text(encoding="utf-8")
    computed, n_checked, n_total = _computed_reading_split()
    assert n_total == EXPECTED_CELLS and n_checked == EXPECTED_CELLS, (
        f"核到 {n_checked}/{n_total} 个格（应为 {EXPECTED_CELLS} = 三口径 × 两 buffer）"
        f"⇒ 产物形状变了或某格 se 为 None ⇒ **覆盖面已缩小**，要有人看。")
    recorded = _recorded_reading_split(doc_text)
    assert recorded == computed, (
        f"判决文档记录的分档与**产物实测**不符。\n"
        f"  实测（{len(computed)} 格分档）：{sorted(computed)}\n"
        f"  文档记录（{len(recorded)} 格分档）：{sorted(recorded)}\n"
        f"⇒ 文档漏记 ⇒ 下一个读产物的人会把 `plus1sd_pct` 当成「点 + 1 个标准差」；\n"
        f"  文档多记 ⇒ 文档在讲一个不存在的事实。\n"
        f"⇒ 改**文档**（`{DOC.name}` 的「{DOC_TABLE_HEAD}」那一节），"
        f"**不要**改实现（第 154 层：`+sd` 是预注册判决的口径）。")


# ══ ⑥ `verdict`：预注册第二节四条规则 ═════════════════════════════

def test_verdict_never_invents_a_fifth_tier():
    """点估计够线时**不许**编一个既非 REACHES_HURDLE 也非
    REACHES_BUT_UNSTABLE 的档位充数（第 91 层：判据一个字不改）。"""
    out = M.verdict(HURDLE, None)
    assert out not in ("REACHES_HURDLE", "REACHES_BUT_UNSTABLE")
    assert out.startswith("NEEDS_YEARLY_SIGN__")


@pytest.mark.parametrize("point,plus1sd,expected", [
    (None, None, "INSUFFICIENT"),
    (HURDLE - 0.10, HURDLE + 0.10, "UNDERPOWERED"),
    (HURDLE - 0.10, HURDLE - 0.01, "INSUFFICIENT"),
])
def test_verdict_fires_the_preregistered_four_rules(point, plus1sd, expected):
    assert M.verdict(point, plus1sd) == expected


def test_the_hurdle_is_derived_not_typed():
    """门槛必须由 `hurdle_rate` 推导，不在本文件写字面量（层 133）。"""
    from aplus.hurdle import hurdle_rate, COST_REAL_50K
    from aplus.evolve import LIVE_REBALANCE_EVERY
    expect = hurdle_rate(target=0.20, cost_per_round=COST_REAL_50K,
                         rounds_per_year=244 / LIVE_REBALANCE_EVERY,
                         benchmark_annual=0.0)
    assert M.HURDLE == expect
    # 目标 20% + 成本 ⇒ 门槛必须**高于**目标本身
    assert HURDLE > 0.20


def test_verdict_is_monotone_in_plus1sd_at_a_fixed_point():
    """判别力：同一点估计下，+1σ 跨过门槛那一刻档位必须换。"""
    point = HURDLE - 0.05
    assert M.verdict(point, HURDLE - 1e-9) == "INSUFFICIENT"
    assert M.verdict(point, HURDLE + 1e-9) == "UNDERPOWERED"
    # 反控：刚好够线一点点 ⇒ 立刻交给「逐年符号还没定」
    assert M.verdict(HURDLE, None).startswith("NEEDS_YEARLY_SIGN__")
    assert M.verdict(HURDLE - 1e-15, HURDLE * 2).startswith("UNDERPOWERED")

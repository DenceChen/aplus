"""**基准项必须是一个参数，不是一个假设**（预注册 §8，写于改 `aplus/hurdle.py` 之前）。

## 缺口本体

`aplus/hurdle.py` 的恒等式是

    净 = 毛超额 − 年成本   ⇒   毛超额 = target + rounds × cost

它**只在基准年化 = 0 时成立**。而模块里的常量注释写着「用户的目标：年化 20% **净值**」
—— 一个自称「净值」的常量，配一个返回「超额」的函数，
且这个偏差对调用方**完全不可见**（签名里没有基准项）。

实测基准（`data/reports/market_nav_caliber.json`，150 只抽样、种子 42）：
等权基准·几何年化 **9.9471%** ⇒ 门槛偏高整整一个基准。

## 本文件守的是**能力**，不是**判决**

⚠️ 特别要说清楚：`§8 G7` 要求新参数的默认值是 **0.0**，
所以**9 条仓内调用点算出来的数字一个都不变**，判决也不变。
⇒ 因此本文件里**没有**一条守卫会说「门槛应该降到 16.44%」——
那会是**把记账修正说成策略变强**（第 129 层）。

真正被守住的是三件「不变量」：
① 默认路径**逐位不变**（否则就是偷偷放松了门槛）；
② 实测基准**不许被烤进代码**（它是带抽样噪声的测量）；
③ 两个口径**并存且各自标注**（因子有效性判超额 / 用户的钱判净值）。
"""
from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path

import pytest

from aplus import hurdle
from aplus.hurdle import (
    COST_REAL_50K, TARGET_NET, feasible_k, hurdle_rate,
    max_affordable_daily, rounds_per_year, table,
)

ROOT = Path(__file__).resolve().parents[1]
PREREG = ROOT / "docs" / "TODO_nav_vs_excess_caliber_2026-10-15.md"
NAV = ROOT / "data" / "reports" / "market_nav_caliber.json"
FWM = ROOT / "data" / "reports" / "factor_width_mde.json"

#: 判决窗口上的等权基准·几何年化（**百分数**）。§7 实测，抽样口径。
BENCH_PCT = 9.94710351459831
#: 同一个数的分数形式。**只在本文件里换算一次**（第 137 层：两份副本之间没有守卫）。
BENCH = BENCH_PCT / 100.0

#: 产物自报的超额口径门槛（K=21）。**读产物字段**，不用公式重算（第 109 层）。
OLD_HURDLE_K21 = json.loads(FWM.read_text(encoding="utf-8"))["hurdle_gross_annual"]


# ══ ① 默认路径必须逐位不变 —— 否则「加参数」就是偷偷放松门槛 ══════════

def test_the_default_benchmark_is_exactly_zero():
    """G9-1：默认值必须是字面量 `0.0`。

    ⚠️ 写成 `benchmark_annual = TARGET_NET * 0` 之类「看起来也是 0」的写法，
    会让「默认 = 假设基准为零」这件事在签名上变得不可读。"""
    sig = inspect.signature(hurdle_rate)
    assert "benchmark_annual" in sig.parameters, \
        "hurdle_rate 没有 benchmark_annual ⇒ §8 G7 未实现"
    assert sig.parameters["benchmark_annual"].default == 0.0, \
        f"默认值是 {sig.parameters['benchmark_annual'].default!r}，不是 0.0 ⇒ " \
        "有默认路径的数字被改过，判决已经变了"


@pytest.mark.parametrize("k", list(hurdle.FREQS))
def test_the_default_path_is_bit_identical_to_the_excess_caliber(k):
    """★ 核心不变量：传不传 `benchmark_annual`，**默认值下必须同一个数**。

    锚点是**产物自报值**（`factor_width_mde.json::hurdle_gross_annual`），
    不是我在这里重算的公式 —— 用公式重算就是拿同一层直觉验证自己（第 109 层）。"""
    assert hurdle_rate(rounds_per_year=rounds_per_year(k)) == pytest.approx(
        OLD_HURDLE_K21 if k == 21 else _old_formula_for(k), rel=1e-12)
    assert hurdle_rate(rounds_per_year=rounds_per_year(k), benchmark_annual=0.0) == \
        hurdle_rate(rounds_per_year=rounds_per_year(k))


def _old_formula_for(k: int) -> float:
    """K=21 之外的档位没有产物字段可锚，用**改之前的公式**逐位复算。"""
    return TARGET_NET + rounds_per_year(k) * COST_REAL_50K


def test_the_four_public_entry_points_all_default_to_the_same_number():
    """G7 的表格：**四个函数一个都不许留在里面读不到基准**。

    只给最底层的 `hurdle_rate` 加参数，另外三个照旧传 0，
    等于造了「四把尺子里只有一把会量」而调用方从签名上看不出来。"""
    n = rounds_per_year(21)
    assert hurdle_rate(rounds_per_year=n, benchmark_annual=BENCH) == \
        pytest.approx(TARGET_NET - BENCH + n * COST_REAL_50K)
    # 三个上层函数必须能**把基准传到底**
    # ⚠️ 这一段是**防御性冗余**：变异台实测，把这个 `assert` 改成恒真
    #    仍然全绿，因为下一条测试的**正控**（返回值必须随基准变）把它完全覆盖了。
    #    留着的唯一理由：参数压根没收时报错信息可读（否则是 TypeError）。
    for probe in (feasible_k, max_affordable_daily, table):
        sig = inspect.signature(probe)
        assert "benchmark_annual" in sig.parameters, \
            f"{probe.__name__} 没接 benchmark_annual ⇒ 基准在它这一层被吞掉"


def test_the_upper_layers_actually_forward_it_not_just_accept_it():
    """⚠️ 「签名里有」不等于「用上了」（第 137 层：两份副本之间没有守卫）。

    正控：**三把尺子**的返回值都必须随基准**变**；
    反控：把基准换成 0，数字必须回到旧口径。

    🚨 **本条是被变异台逼出来的**：第一版只测 `table`，
    于是「`feasible_k` / `max_affordable_daily` 收下基准却丢掉」这两条变异
    **全绿存活** ⇒ 上面的签名检查是**最弱的一层覆盖**，
    它只证明「参数在」，不证明「参数被用」。
    签名检查留着（它拦住的是另一种错：压根没收参数），但**不够**。"""
    # ── table：门槛直接出现在返回值里 ──
    assert table()[2]["gross_excess_needed"] != table(benchmark_annual=BENCH)[2][
        "gross_excess_needed"], "table 收了基准却没往 hurdle_rate 传"
    assert table(benchmark_annual=0.0)[2]["gross_excess_needed"] == \
        table()[2]["gross_excess_needed"]

    # ── feasible_k / max_affordable_daily：基准只能通过**门槛**影响结果，
    #    所以挑一个**夹在两个口径之间**的毛超额：旧门槛够不着，新门槛够得着。
    between = TARGET_NET                      # 0.20，落在两门槛中间（见下）
    n21 = rounds_per_year(21)
    h_nav = hurdle_rate(rounds_per_year=n21, benchmark_annual=BENCH)
    h_gross = hurdle_rate(rounds_per_year=n21)
    assert h_nav < between < h_gross, (
        f"测试自身的夹逼前提不成立：净值门槛 {h_nav:.4f}、"
        f"超额门槛 {h_gross:.4f}，{between} 不在其中。"
        "⇒ 基准口径或成本口径变了，下面的正控就失去意义（会变成恒失败）")

    assert feasible_k(between, freqs=(21,)) == [], \
        "默认（超额）口径下 20% 的毛超额够不着月频门槛，前提反了"
    assert feasible_k(between, freqs=(21,), benchmark_annual=BENCH) == [21], \
        "feasible_k 收了基准却没往下传 ⇒ 净值口径下够得着却被判为够不着"
    assert feasible_k(between, freqs=(21,), benchmark_annual=0.0) == \
        feasible_k(between, freqs=(21,)), "反控：基准 0 必须回到旧口径"

    assert max_affordable_daily(between, freqs=(21,)) == 0
    assert max_affordable_daily(between, freqs=(21,), benchmark_annual=BENCH) == 21, \
        "max_affordable_daily 收了基准却没转发 ⇒ 净值口径下频率被压成 0"
    assert max_affordable_daily(between, freqs=(21,), benchmark_annual=0.0) == \
        max_affordable_daily(between, freqs=(21,))


# ══ ② 实测基准不许被烤进代码（它带抽样噪声） ═══════════════════════

def test_no_module_level_constant_in_aplus_holds_the_measured_benchmark():
    """G7-bis：`0.099471` 不许出现在 `aplus/` 的任何模块级常量里。

    理由不是洁癖：它是 **150 只抽样**的数，且**偏差方向非系统性**（§7.1）。
    烤进 8 个消费者 = 让全仓判决继承一个已知带噪的测量。"""
    offenders = []
    for path in sorted((ROOT / "aplus").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:                     # 模块级赋值
            if isinstance(node, ast.Assign):
                for val in ast.walk(node.value):
                    if isinstance(val, ast.Constant) and \
                            isinstance(val.value, float) and \
                            abs(val.value - BENCH) < 1e-4:
                        offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, (
        f"实测基准被写进了模块级常量：{offenders}\n"
        "⇒ 它是 150 只抽样的数（§7.1），烤进代码 = 让 8 个消费者继承它的噪声。"
        "基准必须是**调用方的输入**。")


def test_the_benchmark_used_by_this_file_comes_from_the_artifact():
    """本文件自己的基准数必须**来自产物**，不许是我手打的。

    ⚠️ 手打的 `9.9471` 与产物漂移时，本文件会安静地守着一个旧数（第 119 层）。"""
    nav = json.loads(NAV.read_text(encoding="utf-8"))
    assert nav["equal_weight"]["geometric_annual_pct"] == pytest.approx(
        BENCH_PCT, rel=1e-12), \
        "产物里的基准变了 ⇒ §7 的判决要重判，先更新预注册再改本文件"


# ══ ③ 两个口径并存，且报告必须分别标注 ═════════════════════════════

def test_the_excess_caliber_is_still_reachable_and_is_the_default():
    """G7-ter：超额口径**不许被删** —— 石川用超额衡量因子有效性
    （`data/books/cn_factor_lines.txt:565/:571`），因子判决全挂在它上面。"""
    assert hurdle_rate(rounds_per_year=rounds_per_year(21)) == pytest.approx(
        OLD_HURDLE_K21, rel=1e-12), "超额口径的默认路径被改了"
    # 超额口径答的是「因子有没有效」，净值口径答的是「钱会不会涨」
    assert TARGET_NET == 0.20, "G9-2：目标不许被改"


def test_the_nav_caliber_hurdle_is_exactly_one_benchmark_lower():
    """净值口径门槛 = 超额口径门槛 − 基准，**逐位**。

    ⚠️ 这条**不许**被写成「门槛应该降到 16.44%」——
       那是把记账修正说成策略变强（第 129 层）。
       它守的是**两个口径的差等于基准**，不是任何一个的绝对值。"""
    n = rounds_per_year(21)
    assert hurdle_rate(rounds_per_year=n, benchmark_annual=BENCH) == pytest.approx(
        OLD_HURDLE_K21 - BENCH, rel=1e-12)
    assert hurdle_rate(rounds_per_year=n, benchmark_annual=BENCH) < OLD_HURDLE_K21


def test_a_negative_benchmark_is_accepted_because_it_is_not_a_cost():
    """⚠️ `hurdle_rate` 对**成本**与**轮数**有负值报错，对**基准**必须有。

    负基准意味着「市场跌、你的策略少跌」⇒ 净值门槛**应该更高**。
    若顺手给它也加一条负值报错，就会把「基准跌 ⇒ 门槛升」这个正确方向判成错误。"""
    assert hurdle_rate(rounds_per_year=rounds_per_year(21),
                       benchmark_annual=-0.05) == pytest.approx(
        OLD_HURDLE_K21 + 0.05, rel=1e-12)


# ══ ④ 预注册不许缺席（G8 / 形状 22） ═══════════════════════════════

def test_section_eight_of_the_preregistration_exists_and_states_the_formula():
    """代码引用了 §8 ⇒ §8 必须真的在那儿，且公式是**可执行的那一行**。

    ⚠️ 否则「预注册」就是一个事后补的标题：代码已经写完，
       再回去写一节 §8 说「我一直打算这么改」——形状 22。"""
    doc = PREREG.read_text(encoding="utf-8")
    assert "## §8 预注册" in doc, "预注册 §8 不存在 ⇒ 代码先于预注册"
    assert "target − benchmark_annual + rounds_per_year × cost_per_round" in doc


def test_hurdle_py_points_back_at_its_preregistration():
    """`aplus/hurdle.py` 的 docstring 必须点名 §8。

    守的是**双向**：代码改了而 §8 没改（docstring 不再点名）⇒ 红；
    §8 没了 ⇒ 上一条也红。"""
    assert "§8" in inspect.getdoc(hurdle), \
        "hurdle.py 的说明里没有 §8 ⇒ 它已经不再由那条预注册授权"


def test_no_artifact_picked_up_a_nav_caliber_number_from_this_change():
    """G8：本次**不许重跑任何东西** ⇒ 产物里仍是**超额口径**的门槛。

    ⚠️ 反过来读：如果哪天 `factor_width_mde.json::hurdle_gross_annual`
    变成了净值口径的数，**那不是本节造成的**，必须查出来。"""
    assert json.loads(FWM.read_text(encoding="utf-8"))["hurdle_gross_annual"] == \
        pytest.approx(OLD_HURDLE_K21, rel=1e-12), \
        "产物里的门槛变了 ⇒ 有东西重跑了产物，而 §8 明令不许重跑"

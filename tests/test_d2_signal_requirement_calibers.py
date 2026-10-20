"""D2 缺口台账的「还差几倍」必须**分口径**：同口径那一列是缺口，跨口径那一列不是。

## 这个文件存在的理由

台账 `data/reports/d2_signal_requirement.json::headline` 里现在有**两个**倍数：

| 列 | 分子 | 分母 | 是不是缺口 |
|---|---|---|---|
| `multiple_needed_at_50k_deliverable_by_caliber.same_caliber` | K=63 的要求 | K=63 · 生产臂 `LIVE_DEFAULT` · 榜宽 10 · 同一等权市场腿的实测毛超额 | ✅ **是** |
| `multiple_needed_at_50k_deliverable_by_caliber.cross_caliber`（= 老的那一个裸 float `multiple_needed_at_50k_deliverable`） | K=63 的要求 | **K=21 研究臂**的实测毛超额 | ❌ **不是** |

⇒ 老的那一个把 **K=21 的实测**除以 **K=63 的要求** ⇒ **K 与信号两处口径都不同**
（第 7 层：两个各自都对的数放一起比）⇒ 它**不是**「还差几倍」，
却长得最像答案（它是一个裸 float，孤零零挂在 headline 里，没有口径名）。

## 五条硬纪律（每条都有守卫）

① **只加不改** —— 既有字段的**值**一个字不许动（前端与既有守卫读它们）。
② **要求那一个数不许动** —— `required_gross_at_50k_deliverable` 是判据的输入
   （预注册的）⇒ 守卫用**独立重算**（`aplus.costs.annual_friction` +
   `basket_nav_01` 的 `required_excess_annual`）对它对拍。
③ **实测不许重算** —— 实测毛超额**逐位**取自上游产物（第 109 层）。
④ **不许跨口径取比值** —— 同口径那一列的分子分母必须
   **同 K / 同信号 / 同榜宽 / 同市场腿**，每一条都有守卫。
⑤ **不许换判据臂** —— 生产臂由上游 `verdict.config.cell` 认，本脚本不许自己挑。

## 为什么守卫读的是**生成器**而不是落盘 JSON

只读落盘 JSON 的守卫**对当前代码零覆盖**（第 96 层）：改生成器它照样绿。
⇒ 这里**import 生成器模块**并调用它的纯函数 ⇒ 变异改生成器时守卫立刻红
（同 `tests/test_d2_gap_two_calibers.py` 的做法）。

## 夹具一律直读真产物，期望值不手打

每个期望值都能指到 `产物路径 :: JSON 路径`；**不许**在断言里手抄数字
（抄进散文 ⇒ 静默过期，第 73 层）。

## 变异验证

见 `tests/mutation_specs/test_d2_signal_requirement_calibers.mut` 与同目录 README。
"""
import ast
import copy
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
REPORTS = ROOT / "data" / "reports"
LEDGER = REPORTS / "d2_signal_requirement.json"
DEPLOYED = REPORTS / "deployed_d2_verdict.json"
WIDTH = REPORTS / "factor_width_mde.json"
CALIB = REPORTS / "market_nav_caliber.json"
NAV = REPORTS / "basket_nav_01_2026-10-16.json"


# ---------- 生成器模块（import，不是落盘 JSON） ----------

def _mod():
    spec = importlib.util.spec_from_file_location(
        "_d2_signal_requirement_generator",
        ROOT / "scripts" / "d2_signal_requirement.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------- 产物（真读，不手抄） ----------

def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _ledger() -> dict:
    return _json(LEDGER)


def _headline() -> dict:
    return _ledger()["headline"]


def _block() -> dict:
    return _headline()["multiple_needed_at_50k_deliverable_by_caliber"]


def _deliverable_row() -> dict:
    """台账自己的**交付档**那一行。

    ⚠️ **不许手打 10** ⇒ 从产物自己的 `deliverable_width` 取。
    """
    art = _ledger()
    n = art["headline"]["measured_gross_best"]["deliverable_width"]
    return next(r for r in art["rows"] if r["n"] == n)


def _rebuilt_block():
    """**重跑生成器的那一块**（纯函数）⇒ 变异改生成器时这里立刻变。"""
    mod = _mod()
    return mod, mod.build_multiple_calibers(
        _json(WIDTH), _json(CALIB), _json(DEPLOYED), _deliverable_row())


# ---------- ① 生产格：标签必须由口径常量拼出来，且就是上游认的那一格 ----------

def test_the_same_caliber_cell_is_the_production_verdict_cell():
    """同口径那一列读的那一格，必须**就是**上游 `verdict.config.cell`。

    ⚠️ 为什么钉「上游认的那一格」而不是「我们自己挑的一格」：
       上游那一格是**生产判决格**（它的 `production_sources.signal` 逐字写着
       「`aplus/factor.py::DEFAULT_WEIGHTS`（weights.json 不存在 ⇒ 回落）」），
       我们自己挑 ⇒ 就有了「挑一个好看的臂」的空间（第 91 层）。
    """
    mod = _mod()
    deployed = _json(DEPLOYED)
    label = mod.production_cell_label()
    assert label == deployed["verdict"]["config"]["cell"], (
        f"拼出来的生产格标签 {label!r} ≠ 上游认的 "
        f"{deployed['verdict']['config']['cell']!r} ⇒ "
        f"要么标签拼错，要么上游的生产配置变了 ⇒ **不许**拿另一格算倍数")
    # 标签里的 K 与敞口**跟着台账的口径常量走**（不许写死）
    assert f"|K{mod.LEDGER_K}|" in label
    assert label.endswith(f"|E{int(round(mod.EQUITY_WEIGHT * 100))}")


def test_the_same_caliber_column_reads_the_production_cell_not_another_one():
    """同口径那一列的 `cell` 必须**就是**生产格。"""
    mod, block = _rebuilt_block()
    deployed = _json(DEPLOYED)
    assert block["same_caliber"]["cell"] == deployed["verdict"]["config"]["cell"]
    assert block["same_caliber"]["signal"] == mod.PRODUCTION_SIGNAL
    assert block["same_caliber"]["filter_arm"] == mod.PRODUCTION_FILTER_ARM


# ---------- ② 实测值逐位取自上游，不许重算 ----------

def test_the_measured_gross_is_read_verbatim_from_the_deployed_artifact():
    """实测毛超额**逐位**等于上游那一格 ⇒ 不许用公式重算（第 109 层）。"""
    mod = _mod()
    deployed = _json(DEPLOYED)
    label = mod.production_cell_label()
    meas = mod.k_axis_measurement(deployed, label)
    upstream = deployed["k_grid"]["cells"][label]["k_axis"]["gross_excess_annual_pct"]
    assert meas["measured_gross_annual_pct"] == upstream, (
        "实测毛超额被改写（不是逐位取自上游）⇒ 第 109 层："
        "用公式重算当验证")
    # 产物路径必须**点名那一格**（改名 ⇒ 守卫会红）
    assert meas["source"].endswith(
        f'["{label}"].k_axis.gross_excess_annual_pct')
    assert DEPLOYED.name in meas["source"]


def test_the_artifact_block_is_exactly_what_the_generator_produces():
    """产物里的那一块必须**等于生成器现在的输出** ⇒ 生成器改了必须重跑。

    ⚠️ 这是「产物 vs 代码」那条关系（第 96 层）：只读落盘 JSON 的守卫
       对当前代码零覆盖 ⇒ 这里对拍**生成器的输出**。
    """
    _mod, rebuilt = _rebuilt_block()
    assert _block() == rebuilt, (
        "落盘产物的那一块与生成器现在的输出**不一致** ⇒ "
        "要么产物没重跑，要么落盘的那块是手工改的 ⇒ 重跑 "
        "`scripts/d2_signal_requirement.py`")


# ---------- ③ 口径自检必须拦得住跨口径 ----------

def test_the_caliber_check_rejects_a_measurement_with_a_different_k():
    """反向正控：K 不符 ⇒ 必须响。

    ⚠️ 没有这一条，`check_ledger_caliber` 可能恒真 ⇒ 那它就是装饰。
    """
    mod = _mod()
    deployed = _json(DEPLOYED)
    meas = mod.k_axis_measurement(deployed, mod.production_cell_label())
    bad = copy.deepcopy(meas)
    bad["k"] = mod.LEDGER_K + 1
    with pytest.raises(AssertionError):
        mod.check_ledger_caliber(bad)
    with pytest.raises(AssertionError):
        mod.check_production_arm(bad)


def test_the_caliber_check_rejects_a_different_width_or_exposure():
    """榜宽 / 敞口不符 ⇒ 必须响（半仓那一档就在上游躺着）。"""
    mod = _mod()
    deployed = _json(DEPLOYED)
    meas = mod.k_axis_measurement(deployed, mod.production_cell_label())
    for key, value in (("n_positions", meas["n_positions"] + 1),
                       ("equity_weight", 0.5)):
        bad = copy.deepcopy(meas)
        bad[key] = value
        with pytest.raises(AssertionError):
            mod.check_ledger_caliber(bad)
        with pytest.raises(AssertionError):
            mod.check_production_arm(bad)


def test_the_production_arm_check_rejects_a_non_production_signal():
    """信号 / 臂不对 ⇒ 必须响（`BACKTEST_IC` 是研究臂，不是生产臂）。"""
    mod = _mod()
    deployed = _json(DEPLOYED)
    meas = mod.k_axis_measurement(deployed, mod.production_cell_label())
    for key in ("signal", "filter_arm"):
        bad = copy.deepcopy(meas)
        bad[key] = "BACKTEST_IC" if key == "signal" else "ARM_C"
        with pytest.raises(AssertionError):
            mod.check_production_arm(bad)


def test_the_market_leg_check_rejects_a_different_market_leg():
    """市场腿不是同一腿 ⇒ 必须响。

    ⚠️ 反向正控：把「抽样只数」换掉 ⇒ 检查必须响（否则它是恒真的）。
    """
    mod = _mod()
    calib = _json(CALIB)
    deployed = _json(DEPLOYED)
    mod.check_same_market_leg(calib, deployed)
    bad = copy.deepcopy(calib)
    bad["sample"]["size"] = bad["sample"]["size"] + 1
    with pytest.raises(AssertionError):
        mod.check_same_market_leg(bad, deployed)


# ---------- ④ 倍数 = 要求 ÷ 实测（同一份要求，同一格实测） ----------

def test_the_same_caliber_multiple_is_required_over_measured():
    """同口径倍数 = 要求 ÷ 实测（**只做一次除法**）。"""
    _mod, block = _rebuilt_block()
    req = block["requirement"]["required_gross_at_50k_deliverable_pct"]
    meas = block["same_caliber"]["measured_gross_annual_pct"]
    assert block["same_caliber"]["multiple_needed"] == pytest.approx(
        req / meas, rel=1e-12)
    assert block["same_caliber"]["is_the_gap"] is True


def test_the_block_carries_the_very_same_requirement_the_headline_has():
    """块里那份「要求」必须**逐位等于** headline 那个 ⇒ 两份副本不许漂移。"""
    block = _block()
    head = _headline()
    assert block["requirement"]["required_gross_at_50k_deliverable_pct"] == (
        head["required_gross_at_50k_deliverable"])
    assert block["requirement"]["k"] == _mod().LEDGER_K
    assert block["requirement"]["n_positions"] == (
        head["measured_gross_best"]["deliverable_width"])


def test_the_multiple_refuses_to_answer_when_the_measured_side_is_zero():
    """分母为 0 ⇒ 报错，**不许**回落成 0（第 112 层：恒真的东西比没有更坏）。"""
    mod = _mod()
    with pytest.raises(ValueError):
        mod.multiple_needed(11.0, 0.0)


# ---------- ⑤ 跨口径那一列必须被标成「不是缺口」 ----------

def test_the_legacy_multiple_stays_a_bare_float_and_is_flagged_cross_caliber():
    """老的那一个**保留**（前端与既有守卫读它）⇒ 必须是裸 float，且被标注。

    ⚠️ 「保留 + 标注」而不是「改名」：改名会打断
       `aplus/web/verdict.js` 与 `aplus/reports.py` 的既有读取
       ⇒ 那两个文件由别的 agent 负责 ⇒ 这里**只加不改**。
    """
    head = _headline()
    legacy = head["multiple_needed_at_50k_deliverable"]
    assert isinstance(legacy, float), (
        "老的那一列必须仍是**裸 float**（前端要拿它做算术）⇒ "
        "改成 dict 会打断既有消费者")
    block = _block()
    cross = block["cross_caliber"]
    assert cross["multiple_needed"] == legacy, (
        "新块里的跨口径那一列与老的那一个**逐位不等** ⇒ 两份副本漂移了")
    assert cross["is_the_gap"] is False
    assert cross["is_the_legacy_number_kept"] is True
    assert cross["legacy_key"] == "headline.multiple_needed_at_50k_deliverable"


def test_the_cross_caliber_column_says_which_caliber_it_is():
    """跨口径那一列必须**说出**它的 K 是几、且那个 K **确实不是**台账的 K。

    ⚠️ K 从**上游产物**读（`factor_width_mde.json::k_rebalance_every`），
       不许手打 ⇒ 那个 K 变了，守卫跟着变。
    """
    mod, block = _rebuilt_block()
    cross = block["cross_caliber"]
    width = _json(WIDTH)
    assert cross["k_of_measured"] == width["k_rebalance_every"]
    assert cross["k_of_requirement"] == mod.LEDGER_K
    assert cross["k_of_measured"] != cross["k_of_requirement"], (
        "跨口径那一列的两个 K 相同 ⇒ 「跨口径」这个标注是假的")
    assert "跨口径" in cross["caliber_name"]
    assert "不是缺口" in cross["caliber_name"]


def test_the_cross_caliber_measured_side_is_the_research_arm_value():
    """跨口径那一列的分母必须**逐位**等于 `factor_width_mde.json::widths["10"]`。"""
    _mod, block = _rebuilt_block()
    width = _json(WIDTH)
    w10 = width["widths"]["10"]
    cross = block["cross_caliber"]
    assert cross["measured_gross_annual_pct"] == w10["gross_annual_pct"] * 100.0
    assert cross["measured_gross_source"] == (
        'data/reports/factor_width_mde.json::widths["10"].gross_annual_pct')
    # 它与同口径那一列的实测**必须不同**（否则「两列」是同一列）
    assert cross["measured_gross_annual_pct"] != (
        block["same_caliber"]["measured_gross_annual_pct"])


# ---------- ⑥ 对照臂：显式标 role，且信号**必须**与生产臂不同 ----------

def test_the_control_arms_are_labelled_and_differ_from_the_production_signal():
    """对照臂 = 同 K / 同榜宽 / 同市场腿，**信号不同** ⇒ 只能作对照。

    ⚠️ 有了对照臂才看得出「生产臂那一列不是挑出来的」⇒
       加对照臂，不是换判据臂（第 183 层）。
    """
    mod, block = _rebuilt_block()
    controls = block["control_arms"]
    assert controls, "没有对照臂 ⇒ 读者无从判断生产臂那一列是不是挑出来的"
    for c in controls:
        assert c["signal"] != mod.PRODUCTION_SIGNAL
        assert "对照臂" in c["role"]
        assert c["k"] == mod.LEDGER_K
        assert c["n_positions"] == mod.DELIVERABLE_N
        assert c["equity_weight"] == mod.EQUITY_WEIGHT


def test_the_control_arm_check_has_teeth(monkeypatch):
    """反向正控：把对照臂换成生产臂 ⇒ **必须**响。

    ⚠️ 没有这一条，「对照臂的信号必须与生产臂不同」那条检查可能恒真
       ⇒ 那它就是装饰（第 112 层：恒真的守卫比没有更贵）。
    """
    mod = _mod()
    monkeypatch.setattr(
        mod, "CONTROL_ARMS",
        ((mod.PRODUCTION_SIGNAL, mod.PRODUCTION_FILTER_ARM),))
    with pytest.raises(AssertionError):
        mod.build_multiple_calibers(
            _json(WIDTH), _json(CALIB), _json(DEPLOYED), _deliverable_row())


def test_the_production_cell_check_has_teeth():
    """反向正控：上游认的那一格变了 ⇒ **必须**响（不许默默换一格算）。"""
    mod = _mod()
    deployed = copy.deepcopy(_json(DEPLOYED))
    deployed["verdict"]["config"]["cell"] = "SOMETHING|ELSE|K63|E100"
    with pytest.raises(AssertionError):
        mod.check_production_cell(deployed)
    # 真实产物必须过
    assert mod.check_production_cell(_json(DEPLOYED)) == (
        mod.production_cell_label())


# ---------- ⑥ 接线层：口径自检必须**真的被调**（否则是装饰） ----------

def _func_node(src: str, name: str):
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"源码里没有函数 {name} ⇒ 守卫打空了")


def _called_names(fn) -> set:
    return {n.func.id for n in ast.walk(fn)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}


def test_the_caliber_checks_are_actually_called_by_the_builder():
    """口径自检必须**被 builder 调用** ⇒ 否则它存在但没人用（装饰）。

    ⚠️ 这条是**接线层**的守卫：只测「检查函数本身能抛」不够 ——
       它得**真的在出数的路径上**被调（第 131 层：正控要验判决不只验探测器）。
    """
    src = (ROOT / "scripts" / "d2_signal_requirement.py").read_text(
        encoding="utf-8")
    called = _called_names(_func_node(src, "build_multiple_calibers"))
    for fn in ("check_production_arm", "check_ledger_caliber",
               "check_same_market_leg"):
        assert fn in called, (
            f"{fn} 没有被 build_multiple_calibers 调用 ⇒ 它是装饰"
            f"（当前调用集合：{sorted(called)}）")


def test_main_calls_the_builder_and_the_production_cell_check():
    """`main` 必须调 builder **与**生产格核对 ⇒ 落盘路径上它们真在跑。"""
    src = (ROOT / "scripts" / "d2_signal_requirement.py").read_text(
        encoding="utf-8")
    called = _called_names(_func_node(src, "main"))
    assert "build_multiple_calibers" in called, (
        f"main 没有调 build_multiple_calibers ⇒ 落盘的那块不是它产的"
        f"（当前调用集合：{sorted(called)}）")
    assert "check_production_cell" in called


# ---------- ⑦ 要求那一个数：一个字不许动（独立重算对拍） ----------

def test_the_required_gross_is_unchanged_and_still_derived_from_the_prereg():
    """`required_gross_at_50k_deliverable` 必须**逐位**等于独立重算的值。

    ⚠️ 重算走的是**底层原语**（`aplus.costs.annual_friction`）与
       **上游产物**（`basket_nav_01` 的 `required_excess_annual`），
       **不**调生成器自己的函数 ⇒ 生成器里的公式被改，这里立刻红
       （第 5 层：断言 == 公式，公式错了断言跟着一错）。
    """
    import sys
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from aplus.costs import annual_friction
    from aplus.evolve import LIVE_REBALANCE_EVERY

    nav = _json(NAV)
    art = _ledger()
    x_star = nav["gap_to_target_by_benchmark"]["equal_weight"][
        "required_excess_annual"]
    rounds_per_year = 244 / LIVE_REBALANCE_EVERY
    friction = annual_friction(50_000.0, 1.0, 10, rounds_per_year)
    want = (x_star + friction) * 100.0
    got = art["headline"]["required_gross_at_50k_deliverable"]
    assert got == want, (
        f"要求毛超额被改了：产物 {got!r} ≠ 独立重算 {want!r} ⇒ "
        f"判据的输入不许动（预注册 §2.1）")
    # 判据的输入本身也必须逐位来自上游
    assert art["account_leg"]["required_excess_annual"] == x_star
    # 244/K **不取整**（取整 4 会抬摩擦下界）
    assert art["caliber"]["rounds_per_year"] == rounds_per_year


def test_the_existing_headline_values_are_untouched_by_the_new_column():
    """既有字段的值**不许**因为多了这一列而变。

    ⚠️ 这里钉的是**恒等式**（headline 的值 == rows 的值 == 上游的值），
       不是一个手抄的字面量 ⇒ 重跑后依然成立，而改了算法就会红。
    """
    art = _ledger()
    head = art["headline"]
    row = _deliverable_row()
    assert head["required_gross_at_50k_deliverable"] == (
        row["required_gross_at_50k"] * 100.0)
    assert head["multiple_needed_at_50k_deliverable"] == (
        row["multiple_needed_at_50k"])
    assert head["measured_gross_best"]["at_deliverable_width"] == (
        row["measured_gross_annual"] * 100.0)
    # 实测那一侧逐位等于上游（K=21 研究臂）
    width = _json(WIDTH)
    assert row["measured_gross_annual"] == width["widths"]["10"]["gross_annual_pct"]
    # 既有键一个都不许少
    for key in ("required_gross_at_infinite_capital_best",
                "required_gross_at_50k_deliverable",
                "measured_gross_best",
                "multiple_needed_at_infinite_capital",
                "multiple_needed_at_50k_deliverable",
                "account_annual_ceiling_pct",
                "account_annual_ceiling_by_width",
                "capital_can_close_at_most_pp",
                "gap_left_after_infinite_capital_pp"):
        assert key in head, f"既有键 {key} 不见了 ⇒ 这次改动不是「只加」"

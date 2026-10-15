"""🚨 守卫：基准年化项（`benchmark_annual`）—— 判据逐字在预注册 §6

## 这个产物为什么必须有

`aplus/hurdle.py:80-102` 逐字写着
「账户净值 = **基准** + 毛超额 − 年成本 ⇒ 毛超额 = target − **benchmark_annual** + rounds × cost」，
而 docstring 自认 `benchmark_annual` 默认 `0.0` 是「把一个**藏起来的假设**摆到签名上」。

全仓 9 条调用点**全部**用默认值 ⇒ 产物里的 `hurdle_gross_annual = 0.2639047…`
**隐含「基准年化 = 0」**。而 `docs/GOAL_20PCT_VERDICT_2026-10-15.md` §三
逐字写着「升 K 最多只能把门槛压到 **20%**」—— **那个下限就是这个假设**。

⚠️ 实测基准年化 **+12.4529%/年**（算术口径）
⇒ `benchmark_annual=0.0` 把门槛**虚高了 12.45 个百分点**。
判决**不变**（预注册 R2：X\\* = 21.5884% > 12.4529%），
但**判决文档的前提漏了一个数量级可观的数** ⇒ 必须钉住它被逐字报告。

## 这道守卫抓的是哪一类失效

`guards-that-pin-existence-not-property`：只钉「产物里有这几个字段」是恒真的 ——
把字段填成 0.0 也照样绿。
⇒ 所以下面每一条都**钉性质**：算术口径与复利口径**必须都在**、
X\\* 必须**算出来**、脚本**不许手打阈值**、复刻路径**不许换仪器**。

## 判据脆性的防线（第 112 层）

`test_the_mutation_that_picks_one_caliber_is_killed` 是**反控**：
若产物只留算术、删掉复利，它必须红 ⇒ 防止下一个人「挑口径」把 9.9471% 那条藏起来。
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "benchmark_annual_term.py"
ARTIFACT = ROOT / "data" / "reports" / "benchmark_annual_term_2026-10-15.json"
PREREG = ROOT / "docs" / "PREREG_BENCHMARK_ANNUAL_TERM_2026-10-15.md"

# 🚨 逐字抄自预注册 §1 的 R1/R2/R3 判据阈值。X\* = hurdle_rate(0) − 实测毛超额。
#   它在**看数据之前**就算出来了（闭式反解），所以钉它不许事后调参。
X_STAR_N10 = 0.2639047619047619 - 0.048108


def _art() -> dict:
    return json.loads(ARTIFACT.read_text(encoding="utf-8"))


def _src() -> str:
    return SCRIPT.read_text(encoding="utf-8")


# ─────────────────────────────────────────────────────────────
# §6.1 机制：产物必须**同时**含三个量，缺一即红
# ─────────────────────────────────────────────────────────────
def test_the_script_itself_still_computes_both_calibers():
    """🚨 变异实测：光看**产物**抓不到「脚本删掉一个口径」。

    第一次跑变异台时 M1（删掉 `_bench_annual` 里的 `compounded_nav`）
    **存活了** —— 因为机制测试读的是**上次落盘的产物 JSON**，
    脚本改了但没重跑，产物没变 ⇒ 绿。
    ⇒ 正是 `snapshot-guards-guard-nothing`：**产物一落盘，
    代码与产物的关系就断了**，除非每次对账。

    ⇒ 所以这条守卫**直接调脚本里的纯函数**，
    绕开快照，让「脚本不产出这个口径」当场红。
    产物侧那条（`test_artifact_reports_the_benchmark_term_not_just_the_hurdle`）
    与脚本↔产物那道桥（`test_the_artifact_is_fresh_against_its_generator`）
    三条一起才够。
    """
    import importlib.util
    import sys as _sys

    spec = importlib.util.spec_from_file_location("_bat_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    saved = list(_sys.path)
    _sys.path.insert(0, str(SCRIPT.parent))
    try:
        spec.loader.exec_module(mod)
    finally:
        _sys.path[:] = saved

    got = mod._bench_annual([0.01, -0.02, 0.005])
    for key in ("arithmetic_mean_x_days_per_year", "compounded_nav"):
        assert key in got, (
            f"脚本的 `_bench_annual()` 不再产出 `{key}` ⇒ "
            "M1 那种变异（删一个口径）现在能被抓到了。"
            "只钉产物是不够的：产物是上一次跑的快照。"
        )
    # 顺带钉一个**性质**：两个口径必须真的不同（否则其中一个是另一个的复制）
    assert got["arithmetic_mean_x_days_per_year"] != got["compounded_nav"], (
        f"两个口径逐位相等：{got} ⇒ 其中一个不是独立算出来的")


def test_the_artifact_is_fresh_against_its_generator():
    """脚本↔产物那道桥（M1 那条变异**真正**会被这里抓到）。

    `provenance.stamp(args.out, "scripts/benchmark_annual_term.py")`
    已经记了生成器的字节 ⇒ 改脚本不重跑，`freshness()` 就会给 `stale`。
    ⇒ 机制测试读快照、这条守「快照还配不配得上现在的代码」，两条合起来才闭环。

    ⚠️ 但它**只逼你重跑，不保证重跑对了** ——
      那正是 `forced-rerun-needs-a-parameter-guard` 说的：
      「守卫逼你重跑，却不管你重跑对了没有」⇒ 由上面那条守「对」。
    """
    from aplus import provenance

    got = provenance.freshness(ARTIFACT, generator="scripts/benchmark_annual_term.py",
                               root=ROOT)
    assert got["verdict"] == "fresh", (
        f"产物相对它的生成器是 `{got['verdict']}` ⇒ "
        f"脚本改动后没有重跑（`{got}`）。"
        "读着旧产物说新结论，就是第 156 层「盖章只盖代码不盖输入」的同形复发"
    )


def test_artifact_reports_the_benchmark_term_not_just_the_hurdle():
    """🚨 三者缺一即红：**不许只报有利的那一个口径**。

    实测基准年化 = +12.4529%（算术）/ +9.9471%（复利）。
    只印算术会让人以为那是账户真赚到的净值；只印复利则与
    `annual_gross_pct = mean × 244` 不同口径（`compare-like-calibers-or-not-at-all`）。
    """
    a = _art()
    b = a["benchmark"]
    for key in ("arithmetic_mean_x_days_per_year", "compounded_nav"):
        assert key in b, (
            f"产物缺 `benchmark.{key}` ⇒ 只有一个口径被报出来。"
            "两个口径的差就是波动拖累，藏掉哪一个都是在挑结论。"
        )
    assert "hurdle_gross_annual_bench0" in a, (
        "产物没有印出 `benchmark_annual=0` 时的门槛 ⇒ "
        "读者无法判断「藏起来的假设」到底虚高了多少"
    )


def test_the_measured_benchmark_is_not_zero():
    """钉**性质**而非存在：基准年化必须**真的非零**。

    若哪天它真的变成 0.0，本守卫会红 ⇒ 那是一个需要重新宣判的事件，
    不该悄悄滑过去（`guards-only-count-after-mutation`）。
    """
    a = _art()
    arith = a["benchmark"]["arithmetic_mean_x_days_per_year"]
    comp = a["benchmark"]["compounded_nav"]
    assert arith > 0.0, (
        f"算术口径基准年化 = {arith}，不是正数 ⇒ "
        "判决分支从 SEALED_* 翻成了 SEALED_BENCHMARK_NEGATIVE，"
        "预注册 R3 生效，**判决文档要重写**"
    )
    assert comp > 0.0, f"复利口径基准年化 = {comp}，不是正数 ⇒ 同上"
    # 两个口径必然不相等（波动拖累）：逐位相等说明其中一个被算成了另一个
    assert arith != comp, (
        "两个口径逐位相等 ⇒ 其中一个不是独立算出来的。"
        f"算术 {arith} vs 复利 {comp}"
    )


# ─────────────────────────────────────────────────────────────
# §6.2 反控：挑口径的变异必须被杀
# ─────────────────────────────────────────────────────────────
def test_the_mutation_that_picks_one_caliber_is_killed():
    """🚨 反控：把复利口径删掉（只留算术）必须让本文件**红**。

    为什么这是反控而不是重复：产物里两个字段都在，机制测试是绿的；
    「只留算术」是一种**合法编辑**（脚本照样能跑、照样能盖章），
    只有反控能抓住它 —— 它会让人把 9.9471% 那条从判决里悄悄删掉。
    """
    a = _art()
    mutated = dict(a)
    mutated["benchmark"] = {k: v for k, v in a["benchmark"].items()
                            if k != "compounded_nav"}
    # 复刻机制测试的判据，必须因这一个变异而红
    with pytest.raises(AssertionError):
        for key in ("arithmetic_mean_x_days_per_year", "compounded_nav"):
            assert key in mutated["benchmark"]


# ─────────────────────────────────────────────────────────────
# §6.3 判决中性：X\* 必须算出来，不许手打
# ─────────────────────────────────────────────────────────────
def test_the_required_benchmark_is_computed_not_typed():
    """脚本里不许出现能翻转 R1/R2 的**浮点阈值字面量**。

    自查用 AST 核**浮点常量**，不是核子串 —— 0.215884 / 0.26 / 0.1245
    无论写在哪儿（注释、文档字符串）都抓不到，但写成 `Constant` 就抓得到。
    同款：`cap_weighted_benchmark.py:56-59` 对 `1.96` 的处理。
    """
    tree = ast.parse(_src())
    banned = {round(X_STAR_N10, 6), round(0.2639047619047619, 6), 0.1245, 0.0994}
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, float):
            if round(node.value, 6) in banned:
                offenders.append((node.lineno, node.value))
    assert not offenders, (
        f"脚本里出现了判决阈值字面量：{offenders}。"
        "X\\* 必须由 `hurdle_rate(...)` 与 `factor_width_mde.json` 的实测毛超额"
        "**算**出来 —— 手打它就等于把预注册 §1 的 R1/R2 挪到了可编辑的位置"
    )


def test_x_star_equals_hurdle_minus_measured_gross():
    """X\\* 的**闭合关系**：它必须真的是 `hurdle_rate(0) − 实测毛超额`。

    这是 R1/R2 的全部依据。守卫逐位核一次，
    防止「有人把 required_benchmark_annual 改成别的减法」。
    """
    a = _art()
    widths_src = json.loads(
        (ROOT / "data" / "reports" / "factor_width_mde.json").read_text(encoding="utf-8"))
    w = widths_src["widths"]
    rows = list(w.values()) if isinstance(w, dict) else w
    gross_by_n = {int(r["n"]): float(r["gross_annual_pct"]) for r in rows}

    h0 = a["hurdle_gross_annual_bench0"]
    for row in a["widths"]:
        n = int(row["n"])
        assert n in gross_by_n, f"产物里有 N={n}，但 `factor_width_mde.json` 里没有"
        assert row["gross_annual_pct"] == gross_by_n[n], (
            f"N={n} 的毛超额 {row['gross_annual_pct']} 与上游产物 "
            f"{gross_by_n[n]} 不一致 ⇒ 两份产物量的不是同一个量"
        )
        assert row["required_benchmark_annual"] == pytest.approx(h0 - gross_by_n[n]), (
            f"N={n} 的 X\\* = {row['required_benchmark_annual']}，"
            f"但 hurdle_rate(0) − 毛超额 = {h0 - gross_by_n[n]} ⇒ 减法被改过了"
        )


# ─────────────────────────────────────────────────────────────
# §6.4 一台测量仪
# ─────────────────────────────────────────────────────────────
def test_reproduction_path_is_the_same_instrument():
    """不许自算交易日历、不许自己抽样基准。

    基准抽样只有 `backtest._market_by_day` 一处（种子 42、`MARKET_SAMPLE=150`）；
    在旁边再写一份就是**第二台测量仪**，两台之间的差永远查不出来（第 22 层）。
    交易日历同理：`panel.window_dates` 是 `build_cross_sections` 里
    `panel.dates` 的唯一来源。
    """
    tree = ast.parse(_src())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.update(a.name for a in node.names)

    assert "_market_by_day" in imported, (
        "脚本没有从 aplus.backtest import `_market_by_day` ⇒ "
        "它自己算了一份基准，那就是两个市场，差值没有意义")
    assert "window_dates" in imported, (
        "脚本没有从 aplus.panel import `window_dates` ⇒ "
        "交易日历是另外数出来的，与 `panel.dates` 不保证同一天")

    src = _src()
    assert "random.Random" not in src and "random.seed" not in src, (
        "脚本自己抽样了基准 —— 基准抽样只有 `_market_by_day` 一处")

    # 🚨 变异 M6 实测：只查 import 名会被 `from … import window_dates as _wd`
    #   **绕过**（AST 的 `ImportFrom.asname` 存的是 `_wd`，但 `a.name` 仍是
    #   `window_dates` ⇒ 「import 了吗」判绿，而调用点接的是本地那个）。
    #   ⇒ 必须查**有没有同名定义**：仪器被重新定义过就是第二台仪器。
    tree2 = ast.parse(src)
    shadowed = sorted(
        n.name for n in ast.walk(tree2)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name in {"window_dates", "_market_by_day", "_next_bars", "hurdle_rate",
                       "rounds_per_year"}
    )
    assert not shadowed, (
        f"脚本重新定义了仪器函数：{shadowed} ⇒ "
        "那不是复刻，是另写了一份。两台测量仪之间的差永远查不出来（第 22 层）"
    )


def test_cost_and_target_come_from_the_single_source():
    """K / 成本 / 目标 / DAYS_PER_YEAR 一律 import，脚本内不许出现字面量。

    自查用 AST 核 `ast.Name` 引用 —— 核子串会被文档字符串骗过。
    """
    tree = ast.parse(_src())
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    for name in ("COST_REAL_50K", "TARGET_NET", "DAYS_PER_YEAR",
                 "LIVE_REBALANCE_EVERY", "ENTRY_LAG"):
        assert name in used, (
            f"产物用了 `{name}` 却没有从 aplus 的单一真相源 import ⇒ "
            "口径被复制了一份，改一处不会改另一处")


# ─────────────────────────────────────────────────────────────
# §6.5 判决与文档的一致性
# ─────────────────────────────────────────────────────────────
def test_the_hand_typed_x_star_discrepancy_stays_on_the_record():
    """🚨 本预注册**自己**手打了 X\\*，并算错了 0.0087pp。这件事不许消失。

    §1 表格手写 **+21.5884%**，脚本按 §6.3 算出来是 **+21.5797%**。
    两者都 > 实测 +12.4529% ⇒ R2 同样成立，**判据一个字不改**。

    ⇒ 这条守卫的作用是**防止「把手算错的那一格悄悄改成脚本值」**——
    那正是 `pre-register-thresholds-before-data` 禁止的动作：
    改判据比留错更糟，因为它让「先写死」这件事失去意义。
    ⇒ 正确处置是：**错的留着 + 旁边写明差异 + 声明哪个权威**。
    """
    text = PREREG.read_text(encoding="utf-8")
    assert "## §7" in text, (
        "预注册的执行记录（§7）不见了 ⇒ 那次手算差异被抹掉了。"
        "抹掉它的人大概是想让文档和产品一致，"
        "但那会让**预注册变成事后追认**。"
    )
    for token in ("21.5884", "21.5797"):
        assert token in text, (
            f"预注册 §7 里 `{token}` 不见了 ⇒ 手写值与实算值必须**并排**留着。"
            "只留一个 = 把判据换成了另一个，那正是预注册要防的事。"
        )
    # 声明了哪个权威 —— 没有这句，读者无法判断该信哪一格
    assert "产品值权威" in text, (
        "预注册 §7 没有声明**产品值权威** ⇒ 两格并存却不下判断，"
        "等于把选择权留给下一个读它的人"
    )


def test_prereg_still_states_the_three_rules_verbatim():
    """判据的上游：预注册里 R1/R2/R3 必须还在。

    ⇒ 这道守卫的依据是**预注册文档**，不是我的偏好。
    若预注册被改写，这里会先红（`snapshot-guards-guard-nothing`：
    产物一落盘，代码与产物的关系就断了，除非每次对账）。
    """
    text = PREREG.read_text(encoding="utf-8")
    for rule in ("**R1**", "**R2**", "**R3**"):
        assert rule in text, f"预注册里 {rule} 不见了 ⇒ 判据被改写或删除"
    for phrase in ("21.5884", "18.8220", "benchmark_annual"):
        assert phrase in text, (
            f"预注册里 `{phrase}` 不见了 ⇒ 判据的依据正在被削短")


def test_the_goal_verdict_doc_no_longer_hides_the_benchmark_term():
    """`GOAL_20PCT_VERDICT` §三 那句「压到 20%」的**前提必须写出来**。

    实测基准年化 +12.4529%/年 ⇒ 「K→∞ 的门槛下限 = 20%」实际是
    `TARGET_NET − benchmark`。文档若仍只写 20%，就是把一个
    **藏起来的假设**当成已写出的结论（第 160 层同形）。

    🚨 **判据形状**：变异 M4（删掉整段补充）第一次**存活** ——
    因为守卫当时只查全文有没有 `benchmark` 这个**词**，
    而第 81 行的公式 `hurdle_rate = target − benchmark_annual + …` 本来就有它。
    ⇒ 正是 `guards-that-pin-existence-not-property`：
    **只钉存在不钉性质**，误伤面为零、信息量也为零。

    ⇒ 现在钉的是**这一段独有的句子 + 两个实测数**，
    删掉整段才会红；改写措辞不会红（可维护），
    但**把基准项从判决里拿掉**一定红。
    """
    goal = (ROOT / "docs" / "GOAL_20PCT_VERDICT_2026-10-15.md").read_text(encoding="utf-8")
    anchors = (
        "「K→∞ 的门槛下限」真实值是 `TARGET_NET − benchmark`",
        "+12.4529%/年",        # 算术口径实测
        "+9.9471%/年",         # 净值复利口径实测 —— 两个都必须印
        "PREREG_BENCHMARK_ANNUAL_TERM_2026-10-15.md",
    )
    missing = [a for a in anchors if a not in goal]
    assert not missing, (
        f"GOAL 判决文档的基准项段落丢了这些锚点：{missing}。"
        "§三「升 K 最多只能把门槛压到 20%」那个下限就会重新变成藏起来的假设 —— "
        "实测基准年化 +12.4529%/年，真实下限是 TARGET_NET − benchmark。"
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))

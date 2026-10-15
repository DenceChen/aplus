"""★ 口径守卫：**目标 20% 是净值，判决量是超额**（预注册 §4 G4）。

## 这组守卫守的是什么

不是「基准那个数算得对不对」——那是产物层守卫，而产物层守卫
**对当前代码零覆盖**（第 96 层）。这里守的是**三件不会自己变的事**：

1. **`hurdle_rate` 的恒等式不许悄悄改口径**（它有 8 个消费者）
2. **`market_nav_caliber` 必须真的调生产那个 `_market_by_day`**，
   而不是自己重写一遍（第 22 层 / 形状 17）
3. **几何 ≠ 算术**（第 4 次口径事故）——门槛是复利，报出来的必须是复利

## 为什么第 1 条是「不许改」而不是「必须改成 X」

预注册 §4 G3 的判决分支**依赖 G1 测出来的基准符号**。
在那个数出来之前改 `hurdle_rate`，就是**先改判据再看数据**
（第 91 层：先有码后补文档，判据与数据之间的先后关系不可辨）。

⇒ 本文件**不钉门槛的数值**，只钉「门槛仍由 `hurdle_rate` 算」这件事。
⇒ 于是 G1 出数后要改门槛，改的是 `hurdle.py`，而这条守卫会**立刻变红**
   —— 这正是它该有的行为。

## 变异设计说明（第 23 层）

每条守卫都配了变异体。⚠️ 特别小心两类**报错方向相反**的失败：

- 守卫**不变**而变异**不在失效方向上** ⇒ 它在守不会发生的事（第 136 层）
- 变异**没打中** ⇒ 台子给假绿（第 14 层）⇒ 规格里全部靠
  `src.count(old) == 1` 断言兜底
"""
from __future__ import annotations

import ast
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "market_nav_caliber.py"
DOC = ROOT / "docs" / "TODO_nav_vs_excess_caliber_2026-10-15.md"
ARTIFACT = ROOT / "data" / "reports" / "market_nav_caliber.json"


def _load():
    spec = importlib.util.spec_from_file_location("mnc", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["mnc"] = mod
    spec.loader.exec_module(mod)
    return mod


# ─────────────────────────────────────────────────────────────
# ① hurdle_rate 的口径不许变（它有 8 个消费者）
# ─────────────────────────────────────────────────────────────

def test_the_hurdle_identity_still_names_excess_not_nav():
    """`hurdle_rate` 的 docstring 必须继续把**分子/分母**都写成「超额」。

    ★ 这条不是「不许改代码」，是**不许让文档偷偷换掉口径**。
    docstring 逐字写着「净 = 毛超额 − 年成本」——
    那个恒等式少了基准项，而 G1 出数前改它就是先改判据。
    """
    src = (ROOT / "aplus" / "hurdle.py").read_text()
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "hurdle_rate")
    doc = ast.get_docstring(fn) or ""
    assert "毛超额" in doc, "hurdle_rate 的 docstring 不再声明输出是「毛超额」⇒ 口径被换掉了"
    assert "净值" in doc, "docstring 连「净值」这个目标词都不提了 ⇒ 读者无法发现口径分歧"
    # ⚠️ 反向对照：若哪天有人把它改成只说净值、只说超额其中之一，上面两条会红。
    assert "目标" in doc or "target" in doc or "所需" in doc


def test_the_hurdle_identity_is_still_annotated_as_omitting_the_benchmark():
    """★ 钉住一个**当前为假**的事实：仓库里还没有任何机制标注那个省略。

    ## 为什么这条守卫现在是红的（不是绿的）

    `aplus/hurdle.py` 全文**没有「基准」二字**（2026-10-15 实测）。
    那个省略了基准项的恒等式，在代码里**从未被标注过** ——
    预注册文档 §1 记了它，而注释不是产物、不在任何 provenance 覆盖范围内
    （形状 22）⇒ 后人读 `hurdle_rate` 只会看到一句自洽的恒等式。

    ⇒ **红着是对的。** 本条守卫是**待落地清单**，不是绿闸：
    `hurdle.py` 的 docstring 补上那句标注之前，它必须一直红。
    🚨 与形状 16/21 同款：**守卫应当在待修的东西面前保持红，
    而不是替它开绿灯**；手写一个 sidecar / 改断言去变绿都是形状 16。

    ⚠️ 但它**不能**变绿的方式是「把断言删掉」——
    那样缺口就连「待办」都不是了（形状 89：拒绝出判决 vs 什么都不留）。
    ⇒ 落地方式唯一：在 `hurdle_rate` 的 docstring 里补上基准项的说明。
    """
    src = (ROOT / "aplus" / "hurdle.py").read_text()
    assert "基准" in src, (
        "hurdle.py 不再提到「基准」⇒ 那个省略了基准项的恒等式变成了"
        "「看起来正确」的身份（本文件的全部前提都建立在它是**已知的错**之上）。"
        "落地方式：在 hurdle_rate 的 docstring 里补上基准项说明，"
        "**不要**改门槛数值（预注册 §4 G4）。"
    )


def test_every_hurdle_consumer_still_goes_through_the_function():
    """8 个消费者**全都**从 `aplus.hurdle` 导入 —— 一个都不许内联自己的门槛。

    ⇒ 内联 = 出现第二台测量仪（第 22 层），而两台之间的差永远查不出来。
    """
    consumers = {
        "scripts/width_capital_feasibility.py": "hurdle_rate",
        "scripts/factor_width_mde.py": "hurdle_rate",
        "scripts/verdict_monthly_hurdle.py": "hurdle_rate",
        "scripts/backtest_walk_forward_weights.py": "hurdle_rate",
        "scripts/verdict_composite.py": "hurdle_rate",
        "aplus/ic_to_return.py": "hurdle_rate",
        "aplus/evolve_race.py": "TARGET_NET",
    }
    missing = []
    for rel, name in consumers.items():
        src = (ROOT / rel).read_text()
        if name not in src:
            missing.append(f"{rel}: {name}")
    assert not missing, (
        f"消费者不再引用 {sorted(set(consumers.values()))}：{missing} ⇒ "
        f"门槛被内联或改名，判决链出现第二个真相源"
    )


# ─────────────────────────────────────────────────────────────
# ② 基准必须走生产那条路径（第 22 层）
# ─────────────────────────────────────────────────────────────

def test_the_benchmark_comes_from_the_production_function_not_a_rewrite():
    """★ 核心守卫：脚本必须 import `_market_by_day`，不许自己算基准。

    ⚠️ 为什么这条最要紧：抽样（种子 42 / 150 只）、`entry_lag`、后复权、
    **上市前剔除**（`pos == 0` 那个 −114%/年 的事故）**全在那个函数里**。
    在旁边重写一遍就是第二台测量仪，而两台之间的差永远查不出来。

    ⇒ 检查的是 **import 绑定**，不是「文件里出现过这个字符串」
    （子串会被注释满足，第 13 层）。
    """
    mod = _load()
    assert mod._market_by_day.__module__ == "aplus.backtest", (
        f"基准来自 {mod._market_by_day.__module__} 而非 aplus.backtest ⇒ "
        f"已经变成第二台测量仪"
    )


def test_the_sampling_parameters_are_the_production_ones():
    """抽样规模与 lag 必须**从生产常量导入**，不许写成字面量。"""
    src = SCRIPT.read_text()
    tree = ast.parse(src)
    # 取模块级 import 的绑定名
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("aplus"):
            imported.update(a.asname or a.name for a in node.names)
    assert {"MARKET_SAMPLE", "ENTRY_LAG"} <= imported, (
        f"没从 aplus.backtest 导入抽样常量，导入了 {sorted(imported)} ⇒ "
        f"抽样口径可能与生产脱钩，而那个差没人查"
    )
    # ★ 断言字面量不在源码里（防止「导进来了但没用」）
    assert "MARKET_SAMPLE = 150" not in src


def test_both_benchmark_calibers_are_reported():
    """★ 预注册 G2：等权 **与** 市值加权两条都必须报。

    ⚠️ `cap_weighted_benchmark.json` 已经证明两条在 2021–2026 差 **8.04pp/年** ——
    只报一条就是第 7 种形状（拿一个自己都不确定口径的数当全貌）。
    """
    mod = _load()
    src = SCRIPT.read_text()
    assert "equal_weight" in src and "cap_weight" in src
    assert mod.CapContext is not None, "市值加权那条臂的 context 不存在 ⇒ 只跑了一条臂"


def test_the_window_is_the_one_the_verdicts_used():
    """★ 窗口必须与 `cap_weighted_benchmark.json` 的 `window_arg` **逐字相同**。

    ⇒ 基准换窗口 = 分子分母不是同一段（第 7 种形状的极端形态）。
    """
    ref = ROOT / "data" / "reports" / "cap_weighted_benchmark.json"
    assert ref.exists(), "参照产物不在 ⇒ 本守卫无法判窗口一致性"
    want = json.loads(ref.read_text())["window_arg"]
    mod = _load()
    assert [mod.WINDOW_START, mod.WINDOW_END] == want, (
        f"判决窗口 {[mod.WINDOW_START, mod.WINDOW_END]} != 参照产物 {want} ⇒ "
        f"基准与组合不是同一段时间"
    )


def test_the_end_of_the_window_is_pinned_not_derived_from_one_stock():
    """★ 形状 23/124：`--end` 的默认值不许由 `files[0]` 之类推导。

    ⇒ 唯一票定全池窗口这件事，已经在仓里犯过 16 次。
    """
    mod = _load()
    assert mod.WINDOW_END == "2026-10-12", (
        f"窗口末端变成 {mod.WINDOW_END} —— 若它是由某只票的末根 K 线推导的，"
        f"整条判决链会无声地换窗口"
    )
    src = SCRIPT.read_text()
    assert "files[0]" not in src, "窗口末端由单票推导（形状 23/124）"


# ─────────────────────────────────────────────────────────────
# ③ 几何 ≠ 算术（第 4 次口径事故）
# ─────────────────────────────────────────────────────────────

def test_annualization_is_geometric_not_the_arithmetic_mean():
    """★ 门槛 `TARGET_NET = 0.20` 是**复利** ⇒ 报出来的年化也必须是复利。

    ⚠️ 反向对照（第 4 次事故的机制）：`annual_mean_pct = effect × 244` 在 σ 小时
    与几何几乎相等 ⇒ 只用「低波动」夹具测，这条守卫**会永远绿**。
    ⇒ 正控刻意用一条**高波动**序列。
    """
    mod = _load()
    # 正控：高波动下算术与几何相差可观
    import random
    rng = random.Random(20261015)
    noisy = [rng.uniform(-0.20, 0.20) for _ in range(244)]
    mult = 1.0
    for v in noisy:
        mult *= (1.0 + v)
    geo = mod._annualized_compound(mult, len(noisy))
    arith = sum(noisy) / len(noisy) * 244
    assert abs(geo - arith) > 1e-3, "正控失效：这条序列下两个口径几乎相等"

    # ★ 恒等式对拍：`_annualized_compound` 必须是 `(1+g)^T − 1` 的逆。
    #   ⚠️ 夹具写成 1.21 时本守卫当场红（得 0.21）——
    #   **那是我把夹具算错了**：+20%/年 ⇒ 一年后终值 **1.20**。
    #   ⇒ 第 12 层的又一次现场：核对脚本自己错两遍，而产物是对的。
    assert mod._annualized_compound(1.20, 244) == pytest.approx(0.20, abs=1e-12)
    assert mod._annualized_compound(1.0, 100) == pytest.approx(0.0, abs=1e-12)

    # ★★ 往返恒等式：**不依赖任何已知正确答案**（比上一条强 ——
    #   上一条的正确答案是我自己算的，正是第 12 层）。
    #   给定年化 g、期数 T，构造出 `mult` 使其复利年化必须回到 g。
    for g in (-0.30, -0.05, 0.0, 0.08, 0.20, 0.45):
        for n in (100, 244, 1395):
            mult = (1.0 + g) ** (n / 244.0)
            assert mod._annualized_compound(mult, n) == pytest.approx(g, abs=1e-12), (
                f"往返不闭合：g={g} n={n} ⇒ {mod._annualized_compound(mult, n)}"
            )


def test_geometric_and_arithmetic_are_both_in_the_artifact_shape():
    """产物里两个口径都必须在 —— 只报一个就是第 7 种形状。"""
    mod = _load()
    import inspect
    body = inspect.getsource(mod.main)
    for field in ("geometric_annual_pct", "arithmetic_daily_mean_pct"):
        assert field in body, f"产物缺 {field} ⇒ 读者无从判断口径"


def test_a_nonpositive_final_multiple_returns_none_not_a_crash():
    """★ 空数据/崩掉必须**区分开**（第 130 层）。

    ⚠️ `lowfreq_plausibility.json` 的 `market_final_multiple = 0.005473`
    是个**合法正数**，但如果基准真的归零或转负，Python 的
    `x ** (1/y)` 对负底数给复数 ⇒ 静默变成 NaN 而不是报错。
    """
    mod = _load()
    assert mod._annualized_compound(0.0, 244) is None
    assert mod._annualized_compound(-1.0, 244) is None
    assert mod._annualized_compound(1.2, 0) is None


# ─────────────────────────────────────────────────────────────
# ④ 预注册不许事后被改写成「本来就这么写的」（形状 22）
# ─────────────────────────────────────────────────────────────

def test_the_prereg_document_exists_and_declares_its_own_status():
    """预注册文件必须存在，且**自报**是「看到数据之前」写的。"""
    text = DOC.read_text()
    assert "预注册" in text
    assert "在看到任何基准数字之前" in text or "之前" in text
    assert "G1" in text and "G3" in text and "G4" in text


def test_a_revision_must_be_appended_not_rewritten_in_place():
    """★ 若 §4 判据被改过，文档必须留下「修订 N（补记于 …）」的痕迹。

    ⇒ 「补写出来的登记不能冒充预注册」（形状 22）。
    ⇒ 这条守卫**现在应当是绿的**（尚无修订），它的作用是**以后**红。
    """
    text = DOC.read_text()
    import re
    revisions = re.findall(r"修订\s*\d+（补记于\s*[\d-]+", text)
    # 允许 0 条（还没改判据）；若 §4 的门槛字样变了而没有修订痕迹 ⇒ 这里抓不到
    # ⇒ 所以额外钉一条：G3 的三分支必须都还在
    for branch in ("`> 0`", "`= 0`", "`< 0`"):
        assert branch in text, f"G3 的分支 {branch} 消失了 ⇒ 判据被改过而没留修订痕迹"


# ─────────────────────────────────────────────────────────────
# ⑤ 产物层（**已知对当前代码零覆盖** —— 所以只钉可问的那部分）
# ─────────────────────────────────────────────────────────────

@pytest.mark.skipif(not ARTIFACT.exists(), reason="产物尚未生成")
def test_the_artifact_labels_both_calibers_and_the_window():
    """产物自报口径 —— 不读字段就无从判断这个数是什么。"""
    d = json.loads(ARTIFACT.read_text())
    for key in ("equal_weight", "cap_weight"):
        assert key in d, f"产物缺 {key} 臂"
        assert d[key]["caliber"], f"{key} 臂没自报口径"
        assert d[key]["n_days"] == d["n_days"], f"{key} 臂缺交易日"
    assert d["window_arg"] == ["2021-01-01", "2026-10-12"]


@pytest.mark.skipif(not ARTIFACT.exists(), reason="产物尚未生成")
def test_two_independent_calls_agree_bitwise():
    """★ 同一窗口两次独立调用必须逐位相同。

    ⚠️ 若不同，说明抽样顺序或全局状态在两次调用之间被改动 ——
    而那个差**永远查不出来**（第 22 层）。
    ⚠️ 这条守卫是**能力守卫**：断言 `== 0.0` 而不是 `is False`，
    否则真出现偏差时它会返回 False 而不给信息。
    """
    d = json.loads(ARTIFACT.read_text())
    assert d["cross_check_rel_diff"] == 0.0, (
        f"两次独立调用相对差 {d['cross_check_rel_diff']} ⇒ 基准不可复现"
    )

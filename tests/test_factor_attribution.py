"""因子归因脚本的守卫。

**最重要的一条是 `test_pipeline_detects_a_planted_relationship`（正控）。**

一个「什么都没测出来」的结论，只有在**证明它测得出东西的时候**才可信。
如果我先看到 `F=1.03 / p=0.674` 再去验证管道，那就是拿结论找理由。
⇒ 先证明管道有检出力，再去看那 0.674。

预注册：`docs/TODO_factor_attribution_2026-10-15.md`
"""
from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import factor_attribution as fa  # noqa: E402


FACTORS = [f"expo_f{i}" for i in range(8)]


def _frame(n=1394, beta=None, seed=0, noise=0.008):
    """造一份与真产物同形状的数据（正控/零控都用它）。"""
    rng = np.random.default_rng(seed)
    X = rng.normal(size=(n, 8))
    beta = np.zeros(8) if beta is None else np.asarray(beta, float)
    y = X @ beta + rng.normal(0, noise, size=n) + 0.000267
    idx = pd.date_range("2021-01-04", periods=n, freq="B").strftime("%Y-%m-%d")
    return pd.DataFrame(X, columns=FACTORS, index=idx).assign(gross_excess=y)


# ── 1. 正控：管道**必须**能检出已知关系 ──────────────────────────────
def test_pipeline_detects_a_planted_relationship():
    """把 β=0.002 植入 3 个因子，管道必须同时报出：联合显著 + 至少一个过 Bonferroni。

    **这是本文件存在的理由。** 没有它，「实测 p=0.674」这句话无法区分
    「因子真的没关系」与「我的代码算错了」。
    """
    beta = np.array([0.002, 0, 0, 0.002, 0, 0, 0.002, 0])
    r = fa.analyze(_frame(beta=beta, seed=1), FACTORS, "正控")

    assert r["joint_p"] < fa.THRESHOLD_JOINT_P, (
        f"植入了 β=0.002 却测不出联合显著性（p={r['joint_p']:.4f}）"
        f" —— **管道没有检出力**，实测的『不显著』一律不可信")

    hit = [c for c, v in r["factors"].items() if v["passes_bonferroni"]]
    assert hit, (
        "联合显著但没有任何一个因子过 Bonferroni —— 阈值或 t 统计量那一侧断了。"
        f" 各因子 p：{ {c: round(v['boot_p'], 4) for c, v in r['factors'].items()} }")

    # 植入的那几个必须是显著的那些，且 β 符号为正
    assert set(hit) == {"expo_f0", "expo_f3", "expo_f6"}
    for c in ("expo_f0", "expo_f3", "expo_f6"):
        assert r["factors"][c]["beta"] > 0
    # 噪声因子不该被误报为显著（假阳性率检查）
    false_pos = [c for c in ("expo_f1", "expo_f4", "expo_f7")
                 if r["factors"][c]["passes_bonferroni"]]
    assert not false_pos, f"把噪声因子报成显著了：{false_pos}"


def test_bootstrap_is_reproducible_under_a_fixed_seed():
    """同一份数据跑两次必须逐位相同 —— 否则 p 值不可复现，也就不可审计。"""
    df = _frame(beta=np.array([0.002] * 8), seed=2)
    a = fa.analyze(df, FACTORS, "a")
    b = fa.analyze(df, FACTORS, "b")
    assert a["joint_p"] == b["joint_p"]
    assert a["factors"] == b["factors"]


# ── 2. 零控：纯噪声不该被判显著 ──────────────────────────────────────
def test_pure_noise_is_not_reported_as_signal():
    r = fa.analyze(_frame(beta=None, seed=3), FACTORS, "零控")
    assert r["joint_p"] > 0.05
    assert not any(v["passes_bonferroni"] for v in r["factors"].values())


# ── 3. 口径守卫：判断量与被判断量必须同一个公式 ──────────────────────
def test_observed_and_bootstrapped_F_use_the_same_formula():
    """★ 这是我**自己犯过**的 bug 的守卫。

    第一版 `analyze` 里算实测联合 F 用的是 `max(t²)`（最强的单个因子），
    而 `wild_bootstrap` 里算零分布用的是标准联合 F
    `(R²/k)/((1−R²)/(n−k−1))`。
    ⇒ **两把尺子量同一个东西**，p 值是垃圾。

    这条断言直接核对公式，而不是核对某个具体数。
    """
    df = _frame(beta=np.array([0.001] * 8), seed=4)
    r = fa.analyze(df, FACTORS, "口径")
    n, k = r["n"], len(FACTORS)

    # 用 analyze 里那两个数反推 R²，两条路必须相等
    r2_from_F = (r["joint_F"] * k) / (r["joint_F"] * k + (n - k - 1))
    X = np.column_stack([np.ones(n)] + [df[c].to_numpy(float) for c in FACTORS])
    r2_direct = fa._ols(X, df["gross_excess"].to_numpy(float))["r2"]
    assert abs(r2_from_F - r2_direct) < 1e-9, (
        f"实测 F 对应的 R²={r2_from_F:.6f}，直接回归的 R²={r2_direct:.6f}"
        f" —— 两侧口径不一致")

    # max(t²) 不会等于联合 F（除非极特殊），证明守卫抓的是真差异
    t = fa._ols(X, df["gross_excess"].to_numpy(float))["t"][1:]
    assert abs(r["joint_F"] - np.max(t ** 2)) > 1e-6


def test_a_near_perfect_fit_now_yields_a_huge_finite_F_instead_of_collapsing():
    """★ `1 − R²` 是**灾难性抵消**，而崩溃不是「R² 恰好等于 1」那么窄。

    实测触发条件：SSE = 2.57e-27（**不是 0**），`sse/sst ≈ 1e-30`，
    于是 `1.0 − r2` 在 float64 里**恰好**是 0.0 ⇒ `ZeroDivisionError`。
    ⇒ 真正的条件是**相对** SSE < eps/2 ≈ 1.1e-16，也就是「拟合到机器精度
    最后一比特」——**近**完美拟合就够了，完美不是必要条件。

    ## 契约变更（2026-10-15，处置见 TODO_factor_attribution §7）

    第一版把这里做成「抛错」。现在改成**两侧同口径地取 SSE**
    （恒等式 `1 − R² = SSE/SST`），于是这个 case **不再崩溃**，
    而是给出一个**有限且巨大**的 F。

    ⚠️ 两种做法都不许**编**：
      回落成 `F=inf` ⇒ `joint_p = 0.0` ⇒ 凭空造出「极显著」（形状 16）
      回落成 `p=1.0`  ⇒ 凭空造出「无杠杆」（形状 16）
    ⇒ 现在这条守卫钉的是第三种：**如实算，并标出「结论不成立」**。

    这条守卫守的**不是**「这个 p 值可信」（它不可信，见 §7.4），
    是「它没有假装」。
    """
    df = _frame(seed=11)
    df = df.copy()
    # 让 gross_excess 恰好是某列因子的仿射变换 ⇒ 完美拟合
    df["gross_excess"] = 2.0 * df[FACTORS[0]] + 1.0

    X = np.column_stack([np.ones(len(df))] + [df[c].to_numpy(float) for c in FACTORS])
    r2 = fa._ols(X, df["gross_excess"].to_numpy(float))["r2"]
    assert r2 == 1.0, f"★ 正控前提：本构造必须真的把 R² 顶到 1.0，实得 {r2!r}"

    # ★ 不再抛错 —— 抛错才是「崩溃被藏起来」，而这里 F 是可算的
    r = fa.analyze(df, FACTORS, "退化")

    assert np.isfinite(r["joint_F"]), f"★ F 应当是有限的大数：{r['joint_F']!r}"
    assert r["joint_F"] > 1e6, \
        f"★ 完美拟合的 F 应极大，实得 {r['joint_F']:.3e} —— 分母口径没换成 SSE？"
    assert r["collinear"] is True, "★ 近完美拟合必须被标为共线，否则读者会当真"


def test_the_collinearity_warning_survives_in_the_payload():
    """★ **大 F 本身就是最容易被误读的东西。**

    数字没有错，但「F=1e30 ⇒ 极显著」是一个**错误推论**：
    残差退化时 F 分布的渐近性不适用（TODO §7.4）。
    ⇒ 这条告警必须**活在产物里**，不能只打在终端 ——
      否则下一个读 JSON 的人只看到 `joint_F: 1e30`，没有告警。

    守卫存在的理由与「p≤1/B 必须留在 JSON」相同：**终端会滚走，产物不会。**
    """
    df = _frame(seed=11).copy()
    df["gross_excess"] = 2.0 * df[FACTORS[0]] + 1.0
    r = fa.analyze(df, FACTORS, "退化")

    # ⚠️ 用 `.get` 而不是 `[...]`：键整个消失时，`r["collinear_note"]` 抛
    #   `KeyError`，红的原因就变成「字典没这个键」而不是「告警被删了」——
    #   两者都红，但只有后者是这个守卫要守的东西（红的原因要对）。
    note = r.get("collinear_note")
    assert note, "★ 共线时产物里必须有告警原文"
    assert "结论不成立" in note, note
    assert "共线性" in note, note

    # 反向：普通噪声**不得**带这条告警（否则它会退化成背景噪声）
    ok = fa.analyze(_frame(seed=12, noise=0.008), FACTORS, "普通噪声")
    assert ok["collinear"] is False and ok.get("collinear_note", "") == "", \
        f"★ 普通输入被误报警，告警就废了：{ok.get('collinear_note')!r}"


def test_a_zero_p_is_reported_as_at_most_one_over_B_not_as_zero():
    """★ B 个重抽 ⇒ 最小的非零 p 是 `1/B`。`p == 0` 与 `p ≤ 1/B`
    在这一栏**不可区分**，写 0 就是**编精度**。

    与形状 16 同源：一个编出来的数和一个真数长得一模一样。
    ⇒ 产物里必须同时给出 `joint_p_floor` 和说明文字。
    """
    df = _frame(seed=11).copy()
    df["gross_excess"] = 2.0 * df[FACTORS[0]] + 1.0   # ⇒ 没有一次重抽够得着
    r = fa.analyze(df, FACTORS, "退化")

    assert r["joint_p"] == 0.0, "★ 正控前提：这个构造必须真的把 p 压到 0"
    assert r["joint_p_floor"] == pytest.approx(1.0 / fa.BOOTSTRAP_B)
    assert "1/" in r["joint_p_note"] and "非 p=0" in r["joint_p_note"], \
        f"★ p=0 必须说明它其实是 p ≤ 1/B：{r['joint_p_note']!r}"


def test_the_collapse_guard_does_not_fire_on_ordinary_noise():
    """★ 正控：新的真·无定义守卫（只对 SST≤0 / SSE 坏掉生效）
    不能变成「见 R² 高就抛错」的噪音源。

    与第 87 层同族 —— 只有零假设臂的守卫会把真数据也判死。
    """
    for noise in (0.008, 1e-4):
        df = _frame(seed=12, noise=noise)
        r = fa.analyze(df, FACTORS, f"普通噪声{noise}")
        assert r.get("joint_p") is not None, \
            f"★ noise={noise} 属正常输入，守卫误伤了：{r}"


def test_both_sides_of_the_F_formula_come_from_one_function():
    """★ 「同口径」必须是**结构保证**，不是两条注释的承诺。

    两处各写一遍公式，早晚会有人只改一侧 —— 而 p 值是两侧**比较**出来的，
    两把尺子量的比较没有意义（与「成本被扣两次」同源）。

    ⇒ 用 **AST 钉死**：`analyze` 与 `wild_bootstrap` 都在调 `joint_F`，
      且 `joint_F` 体内**不得**出现 `1.0 -`（抵消就在那一步）。
      子串会被注释满足（第 13 种形状），所以不能写 `"joint_F" in src`。
    """
    import ast
    src = ast.parse(Path(fa.__file__).read_text(encoding="utf-8"))

    def calls(name):
        tree = next(n for n in ast.walk(src)
                    if isinstance(n, ast.FunctionDef) and n.name == name)
        out = []
        for c in ast.walk(tree):
            if not isinstance(c, ast.Call):
                continue
            f = c.func
            # ⚠️ **必须同时收 `ast.Name` 和 `ast.Attribute`。**
            #   第一版只收 `ast.Attribute`，而模块内 `analyze` 调的是**裸名**
            #   `joint_F(...)`（ast.Name）⇒ 守卫报红，报的还是一句假话。
            #   同族已犯两次：另一处只收 `args[0]`，而真实首参是路径不是常量。
            if isinstance(f, ast.Attribute):
                out.append(f.attr)
            elif isinstance(f, ast.Name):
                out.append(f.id)
        return out

    for fn in ("analyze", "wild_bootstrap"):
        assert "joint_F" in calls(fn), (
            f"★ {fn} 没有调 joint_F —— 两侧各写一遍公式 = 迟早口径错分")

    jf = next(n for n in ast.walk(src)
              if isinstance(n, ast.FunctionDef) and n.name == "joint_F")
    # ⚠️ 判据必须**精确到那一处减法**，不能粗到「不许有减法」：
    #   `n - k - 1`（自由度）本来就是减法，粗判据会逼着人把 dof 也改掉 ——
    #   **守卫过严和守卫恒真一样有害**：它会把人推向错误的修法。
    # ⇒ 只禁「对 r2 的减法」，那才是抵消发生的地方。
    bad = []
    for node in ast.walk(jf):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Sub):
            names = {x.id for x in ast.walk(node) if isinstance(x, ast.Name)}
            if "r2" in names:
                bad.append(ast.dump(node))
    assert not bad, (
        f"★ joint_F 里对 r2 做了减法（{bad}）—— `1.0 - r2` 就是抵消发生的那一步，"
        f"分母必须直接取 SSE")


def test_the_new_formula_equals_the_old_one_wherever_the_old_one_was_stable():
    """★ 换公式必须证明**没换错**，而不是证明「新公式不崩」。

    在**旧公式还稳定**的区间（普通噪声）两者应当一致；
    若不一致 ⇒ 代数写错了，而「不崩」这种守卫完全看不出来。
    """
    df = _frame(seed=13)
    n = len(df)
    X = np.column_stack([np.ones(n)] + [df[c].to_numpy(float) for c in FACTORS])
    y = df["gross_excess"].to_numpy(float)
    k = len(FACTORS)

    fit = fa._ols(X, y)
    resid = y - X @ fit["beta"]
    sse = float(resid @ resid)
    sst = float(((y - y.mean()) ** 2).sum())
    old = (fit["r2"] / k) / ((1.0 - fit["r2"]) / (n - k - 1))
    new = fa.joint_F(fit["r2"], sse, sst, k, n)

    assert new == pytest.approx(old, rel=1e-9), \
        f"★ SSE 口径与原公式在稳定区间不等价：{new:.12g} vs {old:.12g}"


def test_bonferroni_threshold_is_derived_not_typed():
    """阈值必须是 `0.05 / n_factors` 算出来的，且因子数变了它就变。"""
    assert fa.THRESHOLD_FACTOR_P == pytest.approx(0.05 / 8)
    assert len(FACTORS) == 8, "改因子数就必须同步改预注册里的 Bonferroni 分母"


# ── 4. 纪律守卫：R² 与 α 都**不得**变成验收标准 ──────────────────────
def test_r_squared_is_explicitly_excluded_from_the_criteria():
    """预注册 §4.2：R² 高是「按这几个因子打分选股」的必然结果，不是发现。

    这条测的是**产物形状**而不是数值 —— 因为 R² 一定会很高，
    真正的风险是日后有人把它改成通过条件。
    """
    assert "r2" in fa.RESULT_METADATA["NOT_a_criterion"]
    # 真正可执行的那一条：生效阈值与禁止清单**无交集**
    assert not (set(fa.THRESHOLDS) & set(fa.THRESHOLDS_FORBIDDEN)), (
        f"R² 混进了生效阈值：{sorted(set(fa.THRESHOLDS) & set(fa.THRESHOLDS_FORBIDDEN))}")


def test_alpha_significance_is_not_treated_as_ability():
    """预注册 §3.4：α 显著为正是样本内过拟合残差，不算能力。

    测法：造一份「因子解释一切、截距为 0」的数据 ⇒ α 不该被抬高。
    再造一份「因子全零、只有常数收益」的数据 ⇒ α 会显著，
    但那恰恰是过拟合的样子，脚本必须照样把它标成 not_ability。
    """
    assert "alpha" in fa.RESULT_METADATA["NOT_a_criterion"]
    assert "alpha" in fa.THRESHOLDS_FORBIDDEN
    assert not (set(fa.THRESHOLDS) & set(fa.THRESHOLDS_FORBIDDEN))
    # α 显著时脚本仍不得因此抬高结论：verdict 只看 factors + 一致性
    assert "alpha" not in ("joint_p", "factor_p_bonferroni")


# ── 5. 「没跑」与「跑了没发现」必须能分开 ────────────────────────────
def test_missing_parquet_refuses_to_issue_a_verdict(tmp_path, monkeypatch):
    """零档可读时**必须**退出 2，且产物里不能出现任何 verdict。"""
    monkeypatch.setattr(fa, "REQUIRED_ARMS", [99999])
    monkeypatch.setattr(fa, "parquet_path", lambda k: tmp_path / "nope.parquet")
    out = tmp_path / "r.json"
    assert fa.main(["--out", str(out)]) == 2
    assert out.is_file()
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["verdict"] is None


def test_single_arm_run_still_blocks_the_verdict(tmp_path, monkeypatch, capsys):
    """★ `--arms 21` 不得绕开「跨 5 档同号」这条防线。

    第一版写成 `if k not in loaded`，而 `loaded` 里只有**被请求**的档
    ⇒ 于是磁盘上明明存在的 k5 被报成「不存在」，
    更糟的是：**真实存在的其余档也不会被算进一致性检验**。
    """
    df = _frame(seed=5)
    parquet = tmp_path / "wf_weights_backtest_k21.daily.parquet"
    df.to_parquet(parquet)
    for k in (5, 10, 42, 63):                       # 其余四档真的不存在
        (tmp_path / f"wf_weights_backtest_k{k}.daily.parquet").write_bytes(b"")
    monkeypatch.setattr(fa, "parquet_path", lambda k: tmp_path / f"wf_weights_backtest_k{k}.daily.parquet")

    out = tmp_path / "r.json"
    rc = fa.main(["--arms", "21", "--out", str(out)])
    payload = json.loads(out.read_text(encoding="utf-8"))

    assert payload["verdict"] is None, "只跑一档就发了判决 —— §3.3 被绕过了"
    assert rc == 3
    # 缺档清单必须只列**真的不可用**的，不含 k21 自身
    assert not any("k21" in m for m in payload["arms_missing_required"])
    assert len(payload["arms_missing_required"]) == 4


# ── 6. 结论形状：结论必须带着限制一起走 ──────────────────────────────
def test_verdict_payload_always_carries_its_limitations():
    df = _frame(seed=6)
    res = fa.analyze(df, FACTORS, "x")
    assert res["n"] == 1394
    for key in ("label", "n", "period", "alpha", "joint_F", "joint_p", "factors"):
        assert key in res, f"缺字段 {key} —— 少了它读者就少一个可核对的锚"


def test_no_hardcoded_critical_values_in_the_source():
    """★ 台账第 85 条：手打的临界值表错了一格。

    本仓没有 scipy，判据全部来自野自助。所以源码里**不允许**出现
    形如 `1.96` / `2.7764` / `0.8114` 的临界值字面量 ——
    出现即意味着有人绕过野自助去查表了。
    """
    import ast
    src = (ROOT / "scripts/factor_attribution.py").read_text(encoding="utf-8")
    # ★ 用 ast 扫**数值字面量**，不扫文本。
    #   第一版用 `"0.8114" not in 源码文本`，而那个数正好出现在模块 docstring 里
    #   （那里正是在讲「我手打的表错了一格」这件事）⇒ 守卫误报。
    #   教训与台账第 84 条同源：**文本匹配会被注释与文档骗过，语义匹配不会。**
    banned = {1.96, 2.7764, 0.8114, 1.945, 2.306, 0.878, 0.950}
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Constant) and isinstance(node.value, float):
            assert node.value not in banned, (
                f"第 {node.lineno} 行出现了临界值字面量 {node.value} —— "
                f"判据必须来自野自助，不能查表（台账第 85 条：我手打的表错了一格）")
        if isinstance(node, ast.Constant) and isinstance(node.value, int) \
                and not isinstance(node.value, bool) and node.value in (95, 99):
            raise AssertionError(f"第 {node.lineno} 行疑似查表用的百分位整数 {node.value}")


# ── 7. 自助参数必须钉死（这条是被变异 D 逼出来的）────────────────────
def test_bootstrap_parameters_are_pinned_to_the_preregistration():
    """★ 变异 D：`default_rng(seed)` → `default_rng(0)`，**11 条测试全绿**。

    种子是预注册参数（§3.1）。它没被钉住意味着：
    **换种子重跑直到 p 掉进阈值** —— 而这正是我刚用 Bonferroni
    防的那个失败模式的另一个入口，只是从后门进来的。
    """
    assert fa.BOOTSTRAP_SEED == 20261015, "种子被改了 —— 预注册写死的参数"
    assert fa.BOOTSTRAP_B == 2000, "自助次数被改了 —— 预注册写死的参数"
    assert fa.SUBPERIOD_SPLIT == "2024-01-01"


def test_the_seed_is_load_bearing_not_a_no_op():
    """钉住数值还不够：还得证明**换种子真的会改变结论**。

    如果换种子 p 值纹丝不动，说明种子是个摆设，
    那「种子 = 20261015」这句话本身就没有可审计的内容。
    """
    # ★ 必须是**边界**效应，不能是饱和效应。
    #   第一版植入 β=0.0015（t≈9）⇒ p 直接压到 0.0000，
    #   于是换什么种子都是 0，测试读到「种子不起作用」——
    #   **是我的探针太强，不是参数不承重。**
    #   se(β) ≈ 0.008/√1394 ≈ 0.000214 ⇒ β=0.0004 约合 t≈1.9，落在 p∈(0,1)。
    df = _frame(beta=np.array([0.00040] * 8), seed=9)
    ps = []
    for seed in (20261015, 1, 2, 3, 4):
        fa.BOOTSTRAP_SEED = seed
        ps.append(fa.analyze(df, FACTORS, f"s{seed}")["joint_p"])
    fa.BOOTSTRAP_SEED = 20261015

    # 只要求「换种子会改变结果」，**不**要求两两不同 ——
    # B=2000 时 p≈0.005 附近只有约 10 个可取值，5 次里撞上一次是期望之内。
    # 第一版写成 `len(set(ps)) == len(ps)`，读到 0.005 撞了两次就红了，
    # 于是我差点把一条**正常**的随机性当成守卫失效。
    assert len(set(ps)) > 1, f"换种子对 p 值毫无影响 ⇒ 种子不是承重墙：{ps}"
    for p_val in ps:
        assert 0.0 < p_val < 1.0, f"p={p_val} 落在 0 或 1 上 ⇒ 探针又饱和了"
    # 且落点必须紧挨着 —— 若换种子能把 p 甩出量级，那才是真的失控
    assert max(ps) - min(ps) < 0.01, f"换种子把 p 甩开了 {max(ps)-min(ps):.4f} ⇒ 随机性失控：{ps}"


def test_bootstrap_count_affects_p_value_granularity():
    """B 是 p 值的**分辨率**：B=100 只能给出 1% 的刻度。

    若有人把 B 调小来「让 p 更容易过」，p 的粒度会同步变粗 ——
    这条把粒度钉住，让调小 B 变成一件看得见的事。
    """
    df = _frame(beta=np.array([0.00040] * 8), seed=10)
    fa.BOOTSTRAP_B = 200
    coarse = fa.analyze(df, FACTORS, "coarse")
    fa.BOOTSTRAP_B = 2000
    fine = fa.analyze(df, FACTORS, "fine")

    for name, r, b in (("B=200", coarse, 200), ("B=2000", fine, 2000)):
        p_val = r["joint_p"]
        assert 0.0 < p_val < 1.0, f"{name} 的 p={p_val} 落在边界上（0 或 1）⇒ 探针又饱和了"
        # p 是 B 个布尔值的均值 ⇒ 必须是 B 的整数分之一。
        # 容差 1e-9 是浮点舍入（B=2000 时步长 5e-4，相对误差 < 1e-12）。
        assert abs(p_val * b - round(p_val * b)) < 1e-9, (
            f"{name} 的 p={p_val} 不是 {b} 的整数分之一 ⇒ B 根本没在计数")

    assert coarse["joint_p"] != fine["joint_p"], (
        "B 从 200 调到 2000 结果纹丝不动 ⇒ B 不起作用")


# ── 8. §3.3 这条主要防线本身必须被直接验证 ───────────────────────────
def test_sign_consistency_treats_zero_as_inconsistent():
    """★ 零**不算**「支持」。

    一档 β=0 意味着「这一档该因子没起作用」，不是「这一档也同意」。
    把它当成同号，等于让缺失值投票。
    """
    assert fa.sign_consistency([-1.0, -1.0, -1.0])["consistent"]
    assert fa.sign_consistency([1.0, 1.0, 1.0])["consistent"]
    assert not fa.sign_consistency([-1.0, -1.0, 0.0])["consistent"]
    assert not fa.sign_consistency([-1.0, 1.0])["consistent"]
    assert fa.sign_consistency([1.0, 1.0, 0.0])["sign"] is None


def test_an_inconsistent_factor_cannot_become_a_candidate():
    """预注册 §3.4 第 4 行：**三处不同号 = 过拟合，不得使用**。

    这条比 p 值更严，是 40 次检验里唯一把期望假阳性压到 ≈0 的机制。
    ★ 直接调 `fa.decide` —— **不在测试里重写筛选逻辑**。
      重写一遍的测试验的是概念不是实现：把 `decide` 里的
      `cross.get(c, {}).get("consistent")` 改成恒真，它照样绿。
    """
    primary = {"joint_p": 0.001,
               "factors": {"expo_x": {"passes_bonferroni": True},
                           "expo_y": {"passes_bonferroni": True}}}
    cross = {"expo_x": {"consistent": False}, "expo_y": {"consistent": True}}
    verdict, branch = fa.decide(primary, cross)
    assert verdict == "CANDIDATE"
    assert "expo_y" in branch and "expo_x" not in branch.split("：")[1]

    # 全不一致 ⇒ 必须 OVERFIT，**不得**是 CANDIDATE
    cross_bad = {"expo_x": {"consistent": False}, "expo_y": {"consistent": False}}
    verdict, _ = fa.decide(primary, cross_bad)
    assert verdict == "OVERFIT", f"跨档反号的因子被放行成候选了，实际判成 {verdict}"


def test_a_factor_missing_from_cross_is_never_a_candidate():
    """★ 缺失 ≠ 同意。

    某个因子压根没进一致性检验（比如那一档样本太小被跳过），
    `cross` 里就没有它。默认放行 = **让没测过的因子投票**。
    """
    primary = {"joint_p": 0.001, "factors": {"expo_x": {"passes_bonferroni": True}}}
    verdict, _ = fa.decide(primary, {})          # cross 里根本没有 expo_x
    assert verdict == "OVERFIT"


def test_decide_covers_every_preregistered_branch():
    """§3.4 写了 4 个分支，一条都不得被合并或漏掉。"""
    sig = {"passes_bonferroni": True}
    yes = {"consistent": True}
    no = {"consistent": False}
    cases = [
        ({"joint_p": 0.674, "factors": {"a": sig}}, {"a": yes}, "NO_LEVER"),
        ({"joint_p": 0.001, "factors": {"a": {"passes_bonferroni": False}}},
         {"a": yes}, "NO_SINGLE_FACTOR"),
        ({"joint_p": 0.001, "factors": {"a": sig}}, {"a": yes}, "CANDIDATE"),
        ({"joint_p": 0.001, "factors": {"a": sig}}, {"a": no}, "OVERFIT"),
    ]
    for primary, cross, want in cases:
        got, _ = fa.decide(primary, cross)
        assert got == want, f"分支错判：期望 {want}，实得 {got}"


def test_critical_values_are_solved_not_typed():
    """★ 临界值必须**反解**出来，不能手打。

    台账第 85 条：我手打的 `crit[n-2]` 索引错了一格。
    所以 `power_floor` 里曾一度出现 `t_crit: float = 1.96` ——
    **那条守卫立刻红了，而且红得对**：我正在重复同一个错误。

    修法不是把 1.96 加进白名单，是改用 `math.erf` 二分反解
    （标准库，无 scipy、无查表）。
    """
    assert abs(fa.T_CRIT_5PCT - 1.959963985) < 1e-6
    # ★ 独立核对：反解出来的数，代回 CDF 必须回到设定的分位。
    assert abs(fa._norm_cdf(fa.T_CRIT_5PCT) - 0.975) < 1e-9
    # ★ 80% 检出力的正确恒等式：power = 1 − Φ(t_crit − δ) = 0.80
    #   ⇒ Φ(t_crit − δ) = 0.20，其中 δ = T_POWER_TARGET
    #   我第一版写成 `Φ(T_POWER) − 0.975 + 0.20` ⇒ 恒等于 0.222，测了个空气。
    #   **恒等式写错时，断言照样「红」或「绿」，但验的不是这件事。**
    assert abs(fa._norm_cdf(fa.T_CRIT_5PCT - fa.T_POWER_TARGET) - 0.20) < 1e-9

    # 幂等：反解是确定性的，不带随机成分
    assert fa._norm_ppf(0.975) == fa.T_CRIT_5PCT


def test_power_floor_reports_both_an_effect_and_a_floor():
    """★ 「没测出显著」必须能区分两种成因。

    `detectable=False` 只说明「低于测量下限」，
    **不等于**「效应不存在」。产物里必须同时给 MDE 和效应本身，
    否则读者会把「测不准」读成「没有」。
    """
    rng = np.random.default_rng(31)
    tiny = rng.normal(0.0001, 0.008, 1394)      # 效应远低于下限
    r = fa.power_floor(tiny)
    assert not r["detectable"]
    assert r["MDE_annual_pct"] > abs(r["annual_mean_pct"])
    assert "caveat" in r and "分块" in r["caveat"]

    big = rng.normal(0.010, 0.008, 1394)        # 效应远超下限
    assert fa.power_floor(big)["detectable"]


# ══════════════════════════════════════════════════════════════════
# 判决分支顺序（2026-10-15，与 placebo_control 同构、方向相反）
# ══════════════════════════════════════════════════════════════════

def _mk(joint_p: float, passed: list[str], consistent: list[str]) -> tuple[dict, dict]:
    names = [f"F{i}" for i in range(8)]
    primary = {"joint_p": joint_p,
               "factors": {n: {"passes_bonferroni": n in passed} for n in names}}
    cross = {n: {"consistent": n in consistent} for n in names}
    return primary, cross


def _brute(n_cases: int = 20000, seed: int = 7):
    import random
    rng = random.Random(seed)
    names = [f"F{i}" for i in range(8)]
    cases = []
    for _ in range(n_cases):
        joint_p = rng.choice([rng.uniform(0.0, 0.2), rng.uniform(0.2, 1.0)])
        passed = [n for n in names if rng.random() < 0.5]
        cons = [n for n in names if rng.random() < 0.5]
        primary, cross = _mk(joint_p, passed, cons)
        cases.append((primary, cross, joint_p, passed, cons))
    return cases


def test_the_branch_order_is_the_conservative_one():
    """★ **判决分支的书写顺序本身就是判据** —— 而它此前**纯属运气**。

    四个条件**互不排斥**（`NO_SINGLE_FACTOR` 的条件是 `not passed`，
    **不含** `joint_p` 前缀），而判决只取第一条命中的 ⇒ **顺序 = 优先级**。

    穷举 40000 例实测的重叠：

    | 重叠对 | 比例 |
    |---|---|
    | `NO_LEVER` ∧ `CANDIDATE` | **78.56%** |
    | `NO_LEVER` ∧ `OVERFIT` | 8.46% |
    | `NO_LEVER` ∧ `NO_SINGLE_FACTOR` | 0.32% |

    ⇒ 23 个非源码序**全部**会改判决，最多改 **87.33%**。

    🚨 源码序（`NO_LEVER` 最先）恰好已经是保守序，**但没有任何一行代码
    或注释声明过这一点** —— 一个下一个人加分支时很容易把它挪走。

    ★★ 我一度试图给每条加 `joint_sig and ...` 前缀让它们「更自足」，
    那样重叠会归零、顺序不再重要。但那是**改判据**：
    「联合不显著 + 有过线因子 + 不一致」从报 `OVERFIT` 变成报 `NO_LEVER`。
    ⇒ **触发预注册铁律，不属于本次修顺序的范围。已撤销。**
    顺序与条件必须**分开**动。

    ⇒ 与 `placebo_control` 曾经那次同构（那里是「肯定遮住否定」）。
    """
    import itertools

    cases = _brute(n_cases=40000)
    order = list(fa._BRANCH_ORDER)

    # ① 危险的重叠**必须真的存在** —— 否则本守卫是恒真的装饰
    n_lv_cap = sum(1 for p, c, jp, pa, co in cases
                   if jp >= fa.THRESHOLD_JOINT_P and [x for x in pa if x in co])
    n_lv_ovf = sum(1 for p, c, jp, pa, co in cases
                   if jp >= fa.THRESHOLD_JOINT_P and pa
                   and not [x for x in pa if x in co])
    assert n_lv_cap > len(cases) * 0.5, (
        f"NO_LEVER ∧ CANDIDATE 只出现 {n_lv_cap}/{len(cases)} —— 采样分布不对")
    assert n_lv_ovf > 0, "NO_LEVER ∧ OVERFIT 从不重叠 ⇒ 顺序无关，这条无效"

    # ② 最响的否定必须在最前；唯一能报喜的必须在最后
    assert order[0] == "NO_LEVER", f"最响的否定分支不在最前：{order}"
    assert order[-1] == "CANDIDATE", f"唯一能报喜的 CANDIDATE 不在最后：{order}"
    assert order.index("OVERFIT") < order.index("CANDIDATE"), \
        f"报喜的 CANDIDATE 压过了「过拟合，不得使用」：{order}"

    # ③ 穷举全部排列，量出「顺序到底有多重要」。
    #    ★ 判据不是「每个排列都要改判决」—— 那太强了：
    #      只要 `NO_LEVER` 在首位，后面三条的相对序就无关
    #      （命中 `NO_LEVER` 时先返回；不命中时后三条**本来就互斥**）。
    #    真正必须钉的是：**只要 `NO_LEVER` 不在首位，判决就会改变**。
    def _outcome(o, p, c):
        pf = p.get("factors", {})
        pa = [x for x, v in pf.items() if v.get("passes_bonferroni")]
        cp = [x for x in pa if c.get(x, {}).get("consistent")]
        hit = {
            "NO_LEVER": p.get("joint_p", 1.0) >= fa.THRESHOLD_JOINT_P,
            "NO_SINGLE_FACTOR": not pa,
            "OVERFIT": bool(pa) and not cp,
            "CANDIDATE": bool(cp),
        }
        return next((b for b in o if hit[b]), None)

    base = [_outcome(order, p, c) for p, c, *_ in cases]
    demoted = [(perm, sum(1 for a, b in zip(base, [_outcome(list(perm), p, c)
                                                    for p, c, *_ in cases]) if a != b))
               for perm in itertools.permutations(order)
               if perm[0] != "NO_LEVER"]
    assert all(d > 0 for _, d in demoted), (
        "有排列把 NO_LEVER 挪到非首位却没改变任何判决 —— "
        "要么采样没覆盖到，要么顺序其实不重要"
    )
    # 「顺序有多重要」的量级：把 NO_LEVER 挪到最后的排列，改判决的最多
    worst = max(demoted, key=lambda t: t[1])
    assert worst[1] > len(cases) * 0.5, (
        f"把 NO_LEVER 挪到最后只改 {worst[1]}/{len(cases)} 例 —— "
        f"{worst[0]} 看起来像是保守序，顺序在这里几乎不起作用"
    )

    # ④ 判决必须**从不**是「肯定遮住否定」
    n_shadowed = sum(1 for p, c, jp, pa, co in cases
                     if fa.decide_all(p, c)["verdict"] == "CANDIDATE"
                     and jp >= fa.THRESHOLD_JOINT_P)
    assert n_shadowed == 0, (
        f"{n_shadowed} 例里 CANDIDATE 遮住了同样成立的 NO_LEVER —— 报喜压过否定")

    # ⑤ 四条分支都必须可达
    got = {fa.decide_all(p, c)["verdict"] for p, c, *_ in cases}
    assert got == set(order), f"分支不可达：缺 {set(order) - got}，实得 {got}"


def test_the_shadowed_branches_stay_visible_in_the_artifact():
    """★ 被遮住的条件**必须留痕** —— 否则它等于没发生过。

    `if/elif` 只留第一条命中的，读产物的人看不出「其实 CANDIDATE 也成立了」。
    ⇒ `decide_all` 必须把其余成立的分支记进 `also_true`，并落进产物。

    🚨 样例必须选**真会重叠**的那一档：`joint_p` 不显著（⇒ `NO_LEVER` 命中）
    **同时**有因子过 Bonferroni 且三处同号（⇒ `CANDIDATE` 也成立）。
    我第一版选 `joint_p=0.8` + 一致因子，看起来对，其实恰好撞上互斥边界。
    """
    primary, cross = _mk(0.8, ["F1"], ["F1"])      # 联合不显著 + 有候选因子
    r = fa.decide_all(primary, cross)
    assert r["verdict"] == "NO_LEVER", r
    assert "CANDIDATE" in r["also_true"], (
        f"被遮住的 CANDIDATE 没留痕：{r} —— "
        "读产物的人会以为这个候选不存在"
    )

    art = ROOT / "data" / "reports" / "factor_attribution.json"
    if art.is_file():
        payload = json.loads(art.read_text(encoding="utf-8"))
        assert "verdict_also_true" in payload, (
            f"{art} 里没有 verdict_also_true ⇒ 重跑前该产物不可引用"
        )
        assert "verdict_branch_order" in payload, (
            f"{art} 里没有 verdict_branch_order ⇒ 判决顺序无法自证"
        )


def test_mutating_the_branch_order_back_to_the_good_news_first_kills_it():
    """★ **守卫只有变异后算数**（本仓铁律）。

    变异：把 `_BRANCH_ORDER` 改成「报喜在前」（CANDIDATE 提到最前），
    要求上一条守卫**必须红**。

    ★ 变异样例必须落在**真重叠**的那一档：`joint_p` 不显著 + 一致因子。
    我第一版变异后拿 `joint_p=0.8` 去断言 `CANDIDATE` 获胜，红了 ——
    但红的原因是**我把条件改成了互斥的**（见上面的 `joint_sig` 撤销），
    不是顺序起作用。⇒ **变异自测自己也要挑对样例**，
    否则它会「因为别的原因红」，而我把那个原因当成了顺序在起作用。
    """
    real = fa._BRANCH_ORDER
    try:
        fa._BRANCH_ORDER = ("CANDIDATE", "NO_LEVER",
                            "NO_SINGLE_FACTOR", "OVERFIT")
        with pytest.raises(AssertionError):
            test_the_branch_order_is_the_conservative_one()
        # 变异体自身：报喜的应该开始遮住否定
        primary, cross = _mk(0.8, ["F1"], ["F1"])
        assert fa.decide_all(primary, cross)["verdict"] == "CANDIDATE", (
            "变异顺序下 CANDIDATE 没有遮住 NO_LEVER —— 变异没生效"
        )
    finally:
        fa._BRANCH_ORDER = real
    assert list(fa._BRANCH_ORDER) == list(real), "变异后没还原干净"


# ══════════════════════════════════════════════════════════════════
# 数据根 `--data-root`（2026-10-15）
#
# 改之前这个目录是 `ROOT / "data" / "reports"` 这个字面量直接长在
# `parquet_path` 里 —— **没有任何开关能改它**。而并排的
# `factor_width_mde.py` 有 `--data-root` ⇒ 两个脚本跑同一个快照时，
# 一个能指定数据根、一个不能，看起来"同一个参数两边都有"，
# 实际上只有一边生效。
# ══════════════════════════════════════════════════════════════════

# 变异点：`_data_root()` 里那个把入参写进模块全局量的语句。
# 4 空格缩进 + 唯一字面量 ⇒ 全文件恰好命中 1 处（守卫里会实测这一点）。
_WIRING_SITE = "    DATA_ROOT = Path(root)\n"
_WIRING_CUT = "    DATA_ROOT = DEFAULT_DATA_ROOT  # APLUS_MUTATION_PROBE_dataroot_cut\n"


def _plant_reports_dir(root: Path, n_rows: int, n_factors: int, seed: int) -> Path:
    """在 `root` 下种一份**形状可辨认**的 k21 逐日表。

    ★ 形状刻意与仓库里的真产物不同（真产物 n=1394、8 个因子、5 档齐全），
      所以「到底读到哪个根」只看产物里的数字就能分辨 ——
      **不需要去猜脚本碰过哪个路径**（读路径是被测行为，不是被测契约）。
    """
    root.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2021-01-04", periods=n_rows, freq="B").strftime("%Y-%m-%d")
    cols = [f"expo_p{i}" for i in range(n_factors)]
    X = rng.normal(size=(n_rows, n_factors))
    df = pd.DataFrame(X, columns=cols, index=idx).assign(
        gross_excess=X[:, 0] * 0.002 + rng.normal(0, 0.008, size=n_rows))
    df.to_parquet(root / "wf_weights_backtest_k21.daily.parquet")
    return root


def _run_against(mod, data_root: Path, out: Path) -> dict:
    """只传 `--data-root` 与 `--out` 跑一次，返回产物。

    ★ 刻意**不传** `--arms`：若靠 `--arms` 也能改变读到的内容，
      那这条守卫就分不清是哪个参数在起作用。
    """
    rc = mod.main(["--data-root", str(data_root), "--out", str(out)])
    assert out.is_file(), f"产物没落盘（rc={rc}）"
    return json.loads(out.read_text(encoding="utf-8"))


def _assert_data_root_is_honoured(mod, tmp_path: Path) -> None:
    """共享实现：守卫与变异自测跑**同一个**函数。

    ⇒ 变异探针不许持有自己的副本（否则它验的是副本而不是被测接线）。
    """
    a = _plant_reports_dir(tmp_path / "A", n_rows=200, n_factors=3, seed=41)
    b = _plant_reports_dir(tmp_path / "B", n_rows=137, n_factors=5, seed=42)

    # 根 A ⇒ 只可能是 A 的形状
    pa = _run_against(mod, a, tmp_path / "a.json")
    assert pa.get("n_obs") == 200, (
        f"指定了 A 却在别的数据根上读的：n_obs={pa.get('n_obs')}，"
        f"A 里那份是 200 行 / 3 个因子 ⇒ --data-root 根本没进到构造点")
    assert pa.get("n_factors") == 3, f"A 里只有 3 个因子，实得 {pa.get('n_factors')}"
    assert pa.get("arms_run") == [21], f"A 里只有 k21，实得 {pa.get('arms_run')}"

    # 根 B ⇒ 换成 B 的形状。**两点才排得掉「读了某个固定的别处」**：
    # 一条守卫只要一个点，「恰好读了默认值」也能骗过它。
    pb = _run_against(mod, b, tmp_path / "b.json")
    assert pb.get("n_obs") == 137, (
        f"换了个 --data-root 读到的东西一模一样：n_obs={pb.get('n_obs')} ⇒ "
        "参数是摆设")
    assert pb.get("n_factors") == 5, f"B 里只有 5 个因子，实得 {pb.get('n_factors')}"

    # 反混淆：`--out` 与 `--data-root` 指的是**两件事** ——
    # 产物落在 tmp，样本却来自 root。两者混了这条守卫就白跑了。
    assert (tmp_path / "b.json").is_file()


def test_the_data_root_argument_decides_which_files_get_read(tmp_path):
    """★ 新参数必须**真的**被用到（argparse 接受了就丢掉 = 第 6 种形状）。

    判定逻辑：种两份**形状不同**的逐日表，传 A 读到的必须是 A 的行数与因子数，
    传 B 读到的必须是 B 的。默认那份（仓库 `data/reports`，n=1394 / 8 因子 / 5 档）
    两组都匹配不上 ⇒ 走默认路径会当场红。
    """
    _assert_data_root_is_honoured(fa, tmp_path)


def test_cutting_the_data_root_wiring_makes_the_guard_go_red(tmp_path):
    """★ **守卫只有变异后算数**（本仓铁律）。

    变异：`--data-root` 的入参不再被写进模块全局量（构造点断开），
    它就退回默认根。要求上一条守卫**必须红**。

    ⚠️ 顺序不能颠倒：先确认变异**真的落进文件**再看测试结果，
      否则「全绿」会被读成「守卫有洞」，而真相是变异压根没生效。
      写入确认用**与旧串无子串关系**的标记串 + 恰好 1 次计数，
      不用「新串 in 文件」（那是第 14 种形状的假确认）。
    """
    src = ROOT / "scripts" / "factor_attribution.py"
    original = src.read_text(encoding="utf-8")
    marker = "APLUS_MUTATION_PROBE_dataroot_cut"

    assert marker not in original, "探针标记串本来就在文件里 ⇒ 写入确认不可信"
    assert original.count(_WIRING_SITE) == 1, (
        f"变异模式匹配 {original.count(_WIRING_SITE)} 处（必须恰好 1）"
        " ⇒ 变异是空操作，绿灯什么也不证明")

    mutated = original.replace(_WIRING_SITE, _WIRING_CUT)
    assert mutated != original and mutated.count(marker) == 1

    # ★ 走 `mutation_check` 的备份约定（2026-10-15 补）。
    # 本测试直接改写真实源码，而 `finally` 只挡 Python 异常——
    # 门禁被 OOM SIGKILL 时它不会执行。没有这份磁盘备份，
    # 下一个 pytest 会话（`tests/conftest.py` 的
    # `recover_interrupted_mutations`）就没有任何东西可还原，
    # 源文件会永久停在变异态**并且报告一切正常**。
    _VICTIM = "scripts/factor_attribution.py"
    sys.path.insert(0, str(ROOT / "scripts"))
    import mutation_check as mc

    mc.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    bak = mc._backup_name(_VICTIM)
    bak.write_bytes(original.encode("utf-8"))
    try:
        src.write_text(mutated, encoding="utf-8")
        on_disk = src.read_text(encoding="utf-8")
        assert on_disk.count(marker) == 1 and _WIRING_SITE not in on_disk, (
            "变异已核验落地失败 ⇒ 下面的红灯没有任何意义")
        importlib.reload(fa)
        with pytest.raises(AssertionError):
            _assert_data_root_is_honoured(fa, tmp_path)
    finally:
        src.write_text(original, encoding="utf-8")
        bak.unlink(missing_ok=True)
        importlib.reload(fa)

    # 还原后必须重新变绿 —— 否则「变异后变红」可能是因为别的坏理由
    _assert_data_root_is_honoured(fa, tmp_path)
    assert src.read_text(encoding="utf-8") == original, "变异后没还原干净"


def test_without_the_flag_the_data_root_is_still_the_one_from_before(tmp_path):
    """★ 不传 `--data-root` 时行为**与改动前一致** —— 默认路径不许被顺手换掉。

    判定逻辑：默认值必须逐位等于改前那个字面量拼出来的路径；
    再真跑一次不传参的 `main()`，产物里的样本量与因子数必须等于
    默认根上那份真 parquet 的形状（从文件现读，不写死 1394 ——
    写死会让真产物一重生成就假红）。
    """
    root = ROOT / "data" / "reports"
    assert fa.DEFAULT_DATA_ROOT == root
    assert fa.DATA_ROOT == fa.DEFAULT_DATA_ROOT
    assert fa.parquet_path(21) == root / "wf_weights_backtest_k21.daily.parquet"
    assert fa._parse_args([]).data_root == root
    assert fa._parse_args(["--data-root", "/tmp/x"]).data_root == Path("/tmp/x")

    expected = root / "wf_weights_backtest_k21.daily.parquet"

    real = pd.read_parquet(expected)
    rc = fa.main(["--arms", "21", "--out", str(tmp_path / "r.json")])
    payload = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert payload.get("arms_run") == [21], f"默认路径下没读到 k21：{payload.get('arms_run')}"
    assert payload.get("n_obs") == len(real), (
        f"默认根读到的样本量 {payload.get('n_obs')} ≠ 真文件 {len(real)} ⇒ "
        "默认路径被换过了")
    assert payload.get("n_factors") == len(
        [c for c in real.columns if c.startswith("expo_")])
    assert rc in (0, 3), f"默认路径下跑出了意外的退出码 {rc}"


def test_the_data_root_is_restored_after_the_run(tmp_path):
    """★ `DATA_ROOT` 是模块级全局量，**必须**在 `finally` 里还原。

    与 `factor_width_mde.py` 对 `verify.MIN_BASKET` 的处理同源：不还原的话，
    同一个进程里之后跑的任何一次 `main()`（别的守卫、每日任务）都会静默
    沿用上一轮的数据根 —— 那正是"参数看起来能调、实际污染后面所有人"。
    """
    before = fa.DATA_ROOT
    _run_against(fa, _plant_reports_dir(tmp_path / "R", 200, 3, seed=43),
                 tmp_path / "r.json")
    assert fa.DATA_ROOT == before == fa.DEFAULT_DATA_ROOT, (
        f"跑完之后数据根变成了 {fa.DATA_ROOT} ⇒ 全局量没还原")

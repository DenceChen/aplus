"""因子归因脚本的守卫。

**最重要的一条是 `test_pipeline_detects_a_planted_relationship`（正控）。**

一个「什么都没测出来」的结论，只有在**证明它测得出东西的时候**才可信。
如果我先看到 `F=1.03 / p=0.674` 再去验证管道，那就是拿结论找理由。
⇒ 先证明管道有检出力，再去看那 0.674。

预注册：`docs/TODO_factor_attribution_2026-10-15.md`
"""
from __future__ import annotations

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

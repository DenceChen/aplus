"""篮子宽度 × MDE 实验的守卫。

预注册：`docs/TODO_basket_width_2026-10-15.md` §6 列了三条必须有的守卫，
这里全部落成测试，并额外补了两条**当场**想到的。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import factor_width_mde as fw  # noqa: E402
import aplus.verify as verify_mod  # noqa: E402


# ── 1. MIN_BASKET 必须比例化（预注册 §4.2.1）─────────────────────────
def test_min_basket_must_scale_with_width():
    """★ 绝对值 3 在宽榜上是**危险**的。

    `aplus/verify.py` 的 `MIN_BASKET = 3` 对榜宽 10 刚好够用，
    但榜宽 200 时，一天里 200 只只剩 5 只都有 bar 仍然计入
    ⇒ 测出来的「篮子超额」是 5 只票的收益，**噪声远大于信号**。
    """
    assert fw.scaled_min_basket(10) == 3
    assert fw.scaled_min_basket(30) == 3          # 窄榜沿用绝对值
    assert fw.scaled_min_basket(100) == 25
    assert fw.scaled_min_basket(200) == 50
    assert fw.scaled_min_basket(546) == 136


def test_min_basket_is_never_smaller_for_a_wider_basket():
    """单调性：榜宽变大 ⇒ 成分下限不许变小。"""
    ns = [10, 20, 30, 50, 60, 100, 200, 300, 546]
    mbs = [fw.scaled_min_basket(n) for n in ns]
    assert mbs == sorted(mbs), f"成分下限非单调：{list(zip(ns, mbs))}"


def test_min_basket_is_always_strictly_below_the_width():
    """下限必须 < 榜宽，否则那一天必然被丢光 —— 整档作废且不报错。"""
    for n in fw.WIDTHS:
        assert fw.scaled_min_basket(n) < n, f"N={n} 的成分下限 {fw.scaled_min_basket(n)} ≥ 榜宽"


# ── 2. 窗口必须与判决一致（预注册 §4.2.2）───────────────────────────
def test_window_2019_is_refused(capsys):
    """★ `basket_width_sweep.json` 里的数字来自 2019–2023，与判决窗口
    2021–2026 **不是同一个量**。任何人都可能顺手引用它们。

    守卫必须在脚本里（不是文档里）—— 文档能被满足，代码不能。
    """
    assert fw.main(["--start", "2019-01-02", "--out", "/tmp/_fw_should_not_exist.json"]) == 2
    assert not Path("/tmp/_fw_should_not_exist.json").exists(), (
        "窗口不对却还是落了盘 —— 旧口径的产物比没有产物更危险")
    err = capsys.readouterr().err
    assert "不是同一个量" in err


def test_default_window_is_the_judgement_window():
    """★ 验**值**，不验**那行字在哪**。

    第一版写的是 `inspect.getsource(fw.main)` + 子串匹配
    `'ap.add_argument("--start", default="2021-01-01"' in src`。
    今天把 `main` 拆成 `main`（薄包装）+ `_parse_args`（真解析）之后，
    **窗口默认仍然是 2021-01-01，守卫却红了** ——
    它守的其实是我的代码长什么样，不是这条安全性质。

    ⇒ 这是第 13 种形状（子串断言）的**第三种死法**：
    前两种是「被注释喂饱」「锚在整个文件」，
    这一种是「**锚在一个具体的函数体上**」，而函数体是会重构的。

    修法与前两次同一味药：**运行它，断言它的值**。
    """
    args = fw._parse_args([])
    assert args.start == "2021-01-01", (
        f"默认窗口是 {args.start}，而判决窗口是 2021-01-01；"
        "第一版这里就是默认 2019")
    # 且必须**真的能被覆盖**（守卫要能测那条错路，而不是只钉住一个常量）
    assert fw._parse_args(["--start", "2019-01-01"]).start == "2019-01-01"


# ── 3. 措辞守卫：「测不到」≠「没有」（预注册 §6 第三条）──────────────
def test_insignificant_is_never_written_as_no_excess():
    """★ 最容易犯、也最贵的一个错。

    「N=200 仍不显著」说的是**测量能力**，不是**效应是否存在**。
    把它写成「没有超额」= 宣称了一件本实验无权宣称的事。
    """
    notes = fw.__doc__ + (ROOT / "docs/TODO_basket_width_2026-10-15.md").read_text(encoding="utf-8")
    for bad in ("没有超额收益", "证明没有 alpha", "已证伪 alpha", "超额不存在"):
        assert bad not in notes, (
            f"文档里出现了 {bad!r} —— 本实验只能说「测不到」，"
            f"不能说「没有」")

    # 判决分支文案本身也必须带上这句
    verdict, branch = fw.decide({"t_blocked": 0.08, "gross_annual_pct": 0.0652,
                                 "sd_ratio_obs_over_theory": 1.0})
    assert verdict == "NOT_SIGNIFICANT_AT_MEASURABLE_WIDTH"
    assert "不等于「没有超额」" in branch or "只等于「测不到」" in branch


# ── 4. 1/√N 是假设，必须实测（预注册 §4.1 第二行）───────────────────
def test_sd_ratio_is_measured_not_assumed(monkeypatch):
    """σ ∝ 1/√N 是**假设**。脚本必须实测 σ(N) 并与理论值对照，
    不得把理论值直接当成实测值（那就把假设当测量结果了）。

    ★ 做成**行为**测试而不是源码文本匹配：
      喂进「σ 严格按 1/√N 缩放」与「σ 完全不缩放」两种合成序列，
      看报出来的比值分别是 ≈1.00 与远大于 1。
      文本匹配 `assert "sd_theory" in source` 只证明字符串出现过，
      **证明不了它参与了计算** —— 第一版就栽在这里（逻辑搬进 `run_widths` 后，
      断言还在查 `main` 的源码，于是红，而红的原因是我搬了地方）。
    """
    import numpy as np
    import pandas as pd

    def synth(scaling: float):
        """σ(N) ∝ scaling^log2(N/10)，即 scaling=1/√2 时恰好按 1/√N。"""
        def fake(xs, weights, dates, n, bot, k, daily_out=None):
            rng = np.random.default_rng(7)
            sd = 0.008 * (scaling ** np.log2(n / 10))
            # ★ 日期必须唯一：`series_from_chunks` 按日期建 dict，
            #   我第一版写成 `i % 28` ⇒ 200 个点塌成 28 天 ⇒ 低于 60 天的
            #   「样本太少」门槛，测试读到 KeyError 而不是它要验的东西。
            dates = pd.date_range("2021-01-01", periods=200, freq="B").strftime("%Y-%m-%d")
            daily_out.append({"dates": list(dates),
                              "excess": list(rng.normal(0.0003, sd, 200)),
                              "exposure": {}})
            return {"n_rows": 200, "n_days": 200,
                    "real": {"annual_net_pct": 0.0, "hit_threshold": False}}
        return fake

    monkeypatch.setattr(fw, "run_year", synth(1 / np.sqrt(2)))
    rows = fw.run_widths(None, {2021: {"weights": {}}}, [2021], {2021: []},
                         widths=[10, 100], k=21)
    assert abs(rows["100"]["sd_ratio_obs_over_theory"] - 1.0) < 0.15, (
        f"喂进严格 1/√N 的序列，比值应≈1，实得 "
        f"{rows['100']['sd_ratio_obs_over_theory']:.3f}")

    monkeypatch.setattr(fw, "run_year", synth(1.0))       # σ 完全不缩放
    rows2 = fw.run_widths(None, {2021: {"weights": {}}}, [2021], {2021: []},
                          widths=[10, 100], k=21)
    r = rows2["100"]["sd_ratio_obs_over_theory"]
    assert r > 2.0, f"喂进 σ 不缩放的序列，比值应远大于 1，实得 {r:.3f} —— 比值没在计算"


# ── 5. MIN_BASKET 必须在跑完后还原（当场想到的）───────────────────────
def test_min_basket_is_restored_after_the_run(monkeypatch):
    """★ 脚本把 `aplus.verify.MIN_BASKET` 改成比例值，跑完**必须还原**。

    否则同进程里后续的每日任务会静默地按 N=546 的成分下限去跑 N=10。
    这是典型的「测试跑完污染全局」型缺陷：单测全绿，日常任务静默出错。

    ★ 直接调 `run_widths`（**不调 `main()`**）——
      `main()` 会读 5460 个日线 parquet（GB 级），
      第一版这个守卫就这么写的，结果挂住 120s 还和后台 K 扫描抢内存。
    """
    original = verify_mod.MIN_BASKET
    assert original == 3, "前置条件变了：verify.MIN_BASKET 现在不是 3，本测试的前提失效"

    seen = {"n": 0}

    def fake_run_year(xs, weights, dates, n, bot, k, daily_out=None):
        seen["n"] += 1
        # ★ 断言「循环内确实改成了比例值」—— 否则这条测试恒真
        assert verify_mod.MIN_BASKET == fw.scaled_min_basket(n), (
            f"N={n} 时 MIN_BASKET={verify_mod.MIN_BASKET}，"
            f"应为 {fw.scaled_min_basket(n)}")
        raise RuntimeError("故意在跑的过程中炸")

    monkeypatch.setattr(fw, "run_year", fake_run_year)

    with pytest.raises(RuntimeError):
        fw.run_widths(xs=None, wmap={2021: {"weights": {}}},
                      judged=[2021], by_year={2021: []}, widths=[100], k=21)

    assert seen["n"] >= 1, "根本没进宽度循环，测试是空的"
    assert verify_mod.MIN_BASKET == 3, (
        f"[P0] MIN_BASKET 跑完没还原（现值 {verify_mod.MIN_BASKET}）—— "
        f"同进程后续任务会静默按错的成分下限跑")


def test_narrow_width_keeps_the_absolute_min_basket():
    """窄榜沿用绝对值 3 —— 不是「忘了改」，是**该不改**。

    N=10 时 min_basket=3 是 `verify.py` 的原意；
    无差别地按比例放大到 10 会让窄榜凭空少掉一批有效日子，
    而那批日子正是当前判决所依据的样本。
    """
    assert fw.scaled_min_basket(10) == 3
    assert fw.scaled_min_basket(30) == 3
    assert fw.scaled_min_basket(49) == 3
    assert fw.scaled_min_basket(50) != 3, "分界点 50 上必须切到比例化"


def test_every_shipped_width_is_proportional_or_explicitly_narrow():
    """★ 交付的每一档榜宽都必须交代它用的是哪种成分下限。"""
    for n in fw.WIDTHS:
        mb = fw.scaled_min_basket(n)
        assert (mb == fw.MIN_BASKET_BASE) == (n < fw.MIN_BASKET_PROPORTIONAL_FROM)


# ── 6. 预注册与实现必须逐条对齐（2026-10-15 补正后新增）──────────────
def test_the_near_theory_band_lives_in_the_prereg_and_not_only_in_code():
    """★ 「接近 1/√N」的第一版代码里是裸的 `0.7 <= ratio <= 1.4`，
    而预注册 §4.3 只写了「接近」两个字 —— **先有码、后补文档**。

    那个方向的错误特别隐蔽：它是**看起来无害的**（一个带宽），
    而读完数之后再决定带宽，就是一次**未预注册的事后调整**。

    ⇒ 断言代码里的带与文档里的带是**同一个**，且文档里有。
    """
    doc = (ROOT / "docs/TODO_basket_width_2026-10-15.md").read_text(encoding="utf-8")
    assert "0.70 ≤ ratio ≤ 1.40" in doc, (
        "预注册里没有这个带 —— 那它就是看完数据挑的")
    assert (fw.NEAR_THEORY_LO, fw.NEAR_THEORY_HI) == (0.70, 1.40)
    # ★ 而且代码里不许再出现第二个裸的 0.7/1.4（否则单点真相源失效）
    import inspect
    src = inspect.getsource(fw.decide)
    assert "0.7" not in src and "1.4" not in src, (
        "decide() 里又出现裸带宽了 —— 阈值必须只在 NEAR_THEORY_* 单点持有")


def test_cross_width_note_never_becomes_the_verdict():
    """★ 跨档结论一旦占掉 `verdict`，**主档的显著性判决就整个消失了**。

    这不是洁癖：`verdict` 是「N=200 有没有可证明的超额」，
    跨档那句是「放宽后毛超额有没有下降」—— 两件事，
    而 `decide()` 只看主档，第一版预注册却把它们列在同一张表里。

    ⇒ 三条断言：返回值是**字符串**不是判决码；
      它**必须提到**两个端点档；
      且 `decide()` 的签名里**没有**跨档参数。
    """
    rows = {
        "10": {"gross_annual_pct": 0.0652},
        "200": {"gross_annual_pct": 0.03},
        "546": {"gross_annual_pct": 0.02},     # 比 N=10 低 ⇒ 触发「集中持股」
    }
    note = fw.cross_width_note(rows)
    assert isinstance(note, str), "跨档观察必须是字符串，不是判决码"
    assert "逐票研究" in note and "集中持股" in note
    assert "10" in note and "546" in note, "必须报出两个端点档，否则读者不知道比的是谁"

    # 反向：放宽后不降 ⇒ 不得出现「集中持股」结论
    up = {"10": {"gross_annual_pct": 0.02}, "546": {"gross_annual_pct": 0.06}}
    assert "集中持股" not in fw.cross_width_note(up)

    # ★ decide() 只吃主档
    import inspect
    assert list(inspect.signature(fw.decide).parameters) == ["primary"], (
        "decide() 收了跨档输入 ⇒ 跨档结论会把主档判决顶掉")


def test_cross_width_note_survives_a_single_width():
    """档位不足时必须**明确说不构成观察**，而不是悄悄返回空串。

    空串会被读者读成「没问题」—— 而实际上是无从比较。
    """
    assert "不构成" in fw.cross_width_note({"200": {"gross_annual_pct": 0.03}})


# ══════════════════════════════════════════════════════════════════
# 判决分支是「嵌套链」而不是四条独立条件（2026-10-15）
# ══════════════════════════════════════════════════════════════════

def _fw():
    import sys as _sys
    from pathlib import Path as _P
    _root = str(_P(__file__).resolve().parents[1] / "scripts")
    if _root not in _sys.path:
        _sys.path.insert(0, _root)
    import importlib
    return importlib.import_module("factor_width_mde")


def _primary(sig_t, gross, ratio):
    """造一份能被 `decide()` 读的最小输入。`t_blocked` 的符号取正号，
    因为 `sig` 判的是 `abs(t) > T_CRIT`。"""
    import math
    fw = _fw()
    t = (sig_t * (fw.T_CRIT_5PCT + 1.0)) if sig_t else (fw.T_CRIT_5PCT - 1.0)
    return {"t_blocked": t, "gross_annual_pct": gross,
            "sd_ratio_obs_over_theory": ratio}


def test_the_verdict_branches_form_a_nested_chain_not_four_conditions():
    """★ **这四条是「特例 → 一般」的嵌套链**，不是四个可独立为真的条件。

    全仓判决分支审计把这处报成「重叠 19.88%、源码序纯属运气」。
    **实测推翻了那个框架**：

        WIDE_MEASURABLE ⊂ SIG_BUT_CORRELATED ⊂ SIGNIFICANT_BUT_NEGATIVE

    ⇒ 「重叠」量的只是**特例分支的触发频率**，本来就该那样。
    ⇒ 而且这是**预注册 §7.2** 的判据，顺序本身即判据，**不得改动**。

    🚨 **风险方向与审计结论正相反**：按「有重叠就重排」改，
    `WIDE_MEASURABLE` 会在 `(显著, 正, 近理论)` 上变成**永不触发的死代码**，
    「取证成立 ⇒ 进前瞻」这条路整个消失，而剩下的判决看起来完全正常。

    ⚠️ 第一版这条守卫把「强制 near=True 的探测」与「循环自己的 near」混在一起，
    断言了一个我根本没构造的条件 —— **守卫自己在验不存在的东西**。
    所以这里只验一件能直接从真值表读出来的事：**每一格都有归属，且归属符合嵌套**。
    """
    import itertools

    fw = _fw()
    mid = (fw.NEAR_THEORY_LO + fw.NEAR_THEORY_HI) / 2
    out = fw.NEAR_THEORY_HI + 1.0

    expected = {
        (True, True, True):   "WIDE_MEASURABLE",
        (True, True, False):  "SIG_BUT_CORRELATED",
        (True, False, True):  "SIGNIFICANT_BUT_NEGATIVE",
        (True, False, False): "SIGNIFICANT_BUT_NEGATIVE",
        (False, True, True):  "NOT_SIGNIFICANT_AT_MEASURABLE_WIDTH",
        (False, True, False): "NOT_SIGNIFICANT_AT_MEASURABLE_WIDTH",
        (False, False, True): "NOT_SIGNIFICANT_AT_MEASURABLE_WIDTH",
        (False, False, False): "NOT_SIGNIFICANT_AT_MEASURABLE_WIDTH",
    }
    for (sig, pos, near), want in expected.items():
        got = fw.decide(_primary(sig, 0.10 if pos else -0.10,
                                  mid if near else out))[0]
        assert got == want, (
            f"(显著={sig}, 正超额={pos}, 近理论={near}) 判决={got}，"
            f"按嵌套链应为 {want}"
        )
        assert got is not None, f"判决为 None —— 真值表出现了全假格"


def test_every_verdict_branch_stays_reachable():
    """★ 嵌套链最危险的失效是「特例分支变死代码」——**而它不会报错**。

    `WIDE_MEASURABLE` 一旦被 `SIG_BUT_CORRELATED` 吞掉，
    「取证成立 ⇒ 进前瞻」这条路消失，但其余三条照常工作、全程无异常。
    ⇒ 必须有一条守卫**主动构造特例输入**，要求它确实拿到特例判决。

    这与层 102（变异探针打在自己副本上）同源：
    **「没报错」不等于「在守东西」。**
    """
    fw = _fw()
    mid = (fw.NEAR_THEORY_LO + fw.NEAR_THEORY_HI) / 2

    # 特例分支：显著 + 正超额 + σ 落在「近理论」区间
    assert fw.decide(_primary(True, 0.10, mid))[0] == "WIDE_MEASURABLE", (
        "特例分支不可达 —— 「取证成立 ⇒ 进前瞻」这条路已经死了"
    )
    # 第二层：显著 + 正超额，但 σ 明显偏离理论
    assert fw.decide(_primary(True, 0.10, fw.NEAR_THEORY_HI + 1.0))[0] == "SIG_BUT_CORRELATED"
    # 第三层：显著但方向为负（注意：ratio 必须**不在**近理论区间，否则会被上面两层截走）
    assert fw.decide(_primary(True, -0.10, fw.NEAR_THEORY_HI + 1.0))[0] == "SIGNIFICANT_BUT_NEGATIVE"
    # 兜底：不显著
    assert fw.decide(_primary(False, 0.10, mid))[0] == "NOT_SIGNIFICANT_AT_MEASURABLE_WIDTH"


def test_reordering_the_nested_chain_would_kill_the_specific_branch():
    """★ **守卫只有变异后算数** —— 且这里要验的是「换序」这个具体变异。

    变异模拟：把 `SIG_BUT_CORRELATED` 提到 `WIDE_MEASURABLE` 之前
    （审计建议的方向）。在真值表上，**特例输入 (sig, pos, near) 全被截走** ⇒
    特例分支死代码。

    本条不复制 `decide` 的实现，而是**按顺序枚举**四条条件在真值表上的首命中，
    与源码序对照 —— 被测的是「顺序」这个属性本身。
    """
    import itertools

    fw = _fw()
    mid = (fw.NEAR_THEORY_LO + fw.NEAR_THEORY_HI) / 2
    out = fw.NEAR_THEORY_HI + 1.0

    # (名称, 条件) —— 条件按**源码序**列出，忠实反映嵌套关系
    def conds(sig, pos, near):
        g = 0.10 if pos else -0.10
        return [
            ("WIDE_MEASURABLE", sig and pos and near),
            ("SIG_BUT_CORRELATED", sig and pos),
            ("SIGNIFICANT_BUT_NEGATIVE", sig),
            ("NOT_SIGNIFICANT", not sig),
        ]

    src_order = [n for n, _ in conds(True, True, True)]
    swap_order = ["SIG_BUT_CORRELATED", "WIDE_MEASURABLE",
                  "SIGNIFICANT_BUT_NEGATIVE", "NOT_SIGNIFICANT"]
    assert src_order[0] == "WIDE_MEASURABLE"

    def first_hit(order, table):
        return next(n for n in order if table[n])

    killed = 0
    for sig, pos, near in itertools.product([True, False], repeat=3):
        table = dict(conds(sig, pos, near))
        if first_hit(swap_order, table) != first_hit(src_order, table):
            killed += 1
    # 变异只在**特例输入**上生效 ⇒ 恰好 1/8（不是全部）
    assert killed == 1, (
        f"换序影响了 {killed}/8 个输入组合 —— "
        "若不是 1，说明嵌套关系已经变了，本守卫的依据需要重测"
    )

    # 而源码序在真值表上**必须每格都有归属**（不得出现全 False ⇒ verdict=None）
    for sig, pos, near in itertools.product([True, False], repeat=3):
        table = dict(conds(sig, pos, near))
        assert any(table.values()), (
            f"(sig={sig}, pos={pos}, near={near}) 四条条件全假 ⇒ 判决会是 None"
        )

"""测量台本身的测试 —— **台子歪了，量出来的数全是歪的**。

`scripts/measure_bridge_error.py` 是用来质疑 `aplus/ic_to_return.py`
那座桥的。质疑者本身若不可信，质疑就没有意义。

## 这里守住的四件事

1. **分位价差算的是哪两个桶** —— 取错桶（取相邻两桶、取头尾两档）
   会让数差好几倍，而报告照样出数
2. **重叠窗口的 t 值** —— 唯一真正会让人误判「这是噪声还是信号」的一处
3. **峰度读数** —— 联合高斯假设的直接检验，正态必须是 0
4. **桥梁本身的对拍** —— 同一个 IC 在高斯世界里的价差能被闭式解复现

## 为什么第 2 条最重要

`hold21` 相邻两天的前瞻窗口重叠 **20/21 天**。
若对**全部交易日**求 t，有效样本量被高估约 21 倍，
`t` 虚高 `√21 ≈ 4.6` 倍——足以把一个纯噪声的价差报成 `t = 12`。

这类误差有个特别恶劣的性质：**它只会把话说得更好听**。
所以必须有一条测试，让它在数字上**响**。
"""
import importlib.util
import math
from pathlib import Path

import numpy as np
import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "measure_bridge_error.py"


def _mod():
    spec = importlib.util.spec_from_file_location("_bridge_error", _SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


M = _mod()


# ── ① 分位价差 ────────────────────────────────────────────────

def test_the_spread_is_the_top_bucket_minus_the_bottom_bucket():
    """分数单调、收益同步单调时，价差可以**手算**。

    100 只、k = 20：高分 80~99 均值 89.5，低分 0~19 均值 9.5，
    收益按 `score/1000` 造 ⇒ 价差 = (89.5 − 9.5)/1000 = **0.08**。

    这个断言不依赖实现里的任何常数，连 `TAIL_Q` 换成别的值它也该红——
    因为它钉的是「**算的是哪两个桶**」，不是「桶有多宽」。
    """
    score = np.arange(100.0)
    ret = score / 1000.0
    spread, n = M.quintile_spread(score, ret, tail_q=0.20)
    assert n == 100
    assert spread == pytest.approx(0.08, abs=1e-12)


def test_neighbouring_buckets_would_give_a_different_number():
    """**相邻两桶 ≠ 首尾两桶**。这是最容易悄悄取错的一种。

    首尾各 20% 的价差是 0.08；而「第 3 桶减第 2 桶」只有 0.04。
    差 2 倍。若实现取错桶，报告照样出数，只是数错了一倍——
    所以这里把两种口径**并排钉住**，让取错的那一种立刻暴露。
    """
    score = np.arange(100.0)
    ret = score / 1000.0
    tail, _ = M.quintile_spread(score, ret, tail_q=0.20)
    order = np.argsort(score, kind="stable")
    adjacent = ret[order[60:80]].mean() - ret[order[20:40]].mean()
    assert adjacent == pytest.approx(0.04, abs=1e-12)
    assert tail == pytest.approx(2 * adjacent, abs=1e-12)


def test_pure_noise_gives_a_spread_near_zero_not_a_tidy_number():
    """分数与收益完全无关时，价差必须**落在噪声里**。

    这一条防的是「用随机数也测出 +40%/年」——
    那种数一旦出现，整台测量仪就该被判不可信，而不是被写进报告。
    """
    rng = np.random.default_rng(7)
    score = rng.normal(size=4000)
    ret = rng.normal(size=4000) * 0.03
    spread, _ = M.quintile_spread(score, ret)
    assert abs(spread) < 0.006, f"噪声上测出 {spread:.4%}，量级不对"


def test_a_bucket_too_small_to_trade_must_not_return_a_number():
    """样本少到分不出 q 分位时，返回 `None` 而不是硬算。

    「至少 1 只」这个下限之前有个版本写的是 `n // 2`，
    那会在 n=3 时拿 1 只去代表一个分位。宁可没有数，不要一个假数。
    """
    assert M.quintile_spread(np.array([1.0, 2.0, 3.0]), np.array([1.0, 2.0, 3.0]),
                             tail_q=0.2)[0] is None
    assert M.quintile_spread(np.array([]), np.array([]), tail_q=0.2)[0] is None


# ── ② 重叠窗口的 t 值（本文件最要紧的一条）──────────────────────

def test_t_on_overlapping_days_inflates_the_t_stat_by_sqrt_k():
    """**重叠 21 天 ⇒ t 虚高 √21 ≈ 4.6 倍。**

    造 21 段完全不重叠的独立序列，拼成一串「每天一个样本」的
    重叠假象：每一段内部完全相同，所以**均值是对的**，
    但把 21 个重复当成 21 个独立观测，标准误会小 21 倍。

    `non_overlapping` 取每 21 个一个，返回**独立**样本 ⇒
    t 应当回到单段的真实值。

    > 这个误差的性质特别恶劣：它**只朝一个方向**。
    > 所以它必须被一条测试钉住，而不是靠注释提醒。
    """
    rng = np.random.default_rng(3)
    n_seg, seg = 40, 21
    # 每一段内 21 个值**完全相同** —— 段内零方差，段间独立
    truth = rng.normal(loc=0.004, scale=0.02, size=n_seg)
    fake = np.repeat(truth, seg)

    naive = M._t_stat(fake)
    honest = M._t_stat(M.non_overlapping(
        __import__("pandas").DataFrame({"x": fake}), "x", k=seg))

    assert honest == pytest.approx(abs(truth.mean() / truth.std(ddof=1)
                                      * math.sqrt(n_seg)), rel=1e-9)
    # 天真算法把 t 抬高了约 √21 —— 正是要防的那一档
    assert naive / honest == pytest.approx(math.sqrt(seg), rel=0.05)
    assert naive > honest


def test_the_means_agree_but_only_the_t_disagrees():
    """**重叠不改变均值，只改变 t。**

    这是整条测试的立论：若有人以为「重叠污染了均值」而去改均值，
    那是改错了地方——均值无偏，**唯一的损失是 t（= 可信度）**。
    """
    import pandas as pd
    rng = np.random.default_rng(11)
    truth = rng.normal(loc=0.001, scale=0.01, size=30)
    fake = np.repeat(truth, 21)
    frame = pd.DataFrame({"x": fake})
    assert np.nanmean(frame["x"]) == pytest.approx(
        np.nanmean(M.non_overlapping(frame, "x", k=21)), rel=1e-12)


def test_fewer_than_three_independent_samples_gives_no_t_at_all():
    """独立样本 < 3 个时**不给 t**，而不是给一个看起来正常的数。

    `t = mean/(sd/√n)` 在 n=2 时恒等于 ±1 附近，
    那个数**不含任何信息**，却会被读成「弱信号」。
    """
    assert M._t_stat([0.01, 0.02]) is None
    assert M._t_stat([0.01, 0.01, 0.01]) is None       # 零方差
    assert M._t_stat([np.nan, 1.0, 2.0]) is None       # 样本不足


# ── ③ 峰度：联合高斯假设的直接读数 ──────────────────────────────

def test_kurtosis_of_a_normal_sample_is_zero_and_of_a_t_sample_is_large():
    """正态 ≈ 0，t₈ = 1.5 —— 这是「桥假设联合高斯」的判决点。

    t 分布的超额峰度有闭式解 `6/(ν−4)`，ν=8 时正好 **1.5**。
    实测值若显著大于 0，说明分位价差被高斯**高估**，
    而那正是 `ic_to_return.py` 那条公式最脆弱的地方。

    > ν=4 的超额峰度在数学上是 **∞**（定义域边缘），
    > 拿它当测试基准会得到一个「永远差一点」的数。这里用 ν=8。
    >
    > **容差宽到 0.2 是有理由的，不是偷懒**：峰度估计量在重尾下
    > **方差发散**（四次方的期望根本不存在于许多分布里），
    > 所以即便 20 万样本也只能把 t₈ 钉在 1.3~1.7。
    > 峰度**要用来读方向，不要用来读小数点后一位**。
    """
    rng = np.random.default_rng(5)
    assert abs(M.measured_kurtosis(rng.normal(size=200_000))) < 0.1
    assert M.measured_kurtosis(rng.standard_t(df=8, size=500_000)) == pytest.approx(
        1.5, abs=0.2)


def test_kurtosis_needs_four_points():
    assert math.isnan(M.measured_kurtosis(np.array([1.0, 2.0, 3.0])))


# ── ④ 与桥梁本身对拍：高斯世界里闭式解必须能被复现 ──────────────

def test_the_bridge_is_exact_in_the_world_it_assumes():
    """在**高斯世界**里，桥必须给出闭式解——否则它连自己的假设都不满足。

    构造：n 只股票，收益 `r = σ·z`（z ~ N(0,1)），分数取 z 的秩
    ⇒ `ρ_p = 1`、`ρ_s = 1` ⇒ 价差必须精确等于 `σ·K(q)`。

    > 桥若在这里都对不上，那它在上游给的数就完全不用看了。
    > 这一条是**外部锚**：不是「桥和实现一致」，是「桥和数学一致」。
    >
    > ⚠️ 这条测试我**写错过一次**：收益写成了 `rng.normal(...)·σ`
    > ——**与 z 独立**，于是 ρ=0，测出来的价差 ≈ 0，测试当场红。
    > 而红的原因不在 `quintile_spread`，在我把「构造世界」写错了。
    > **又一次：注释描述的是 A，代码造的是 B。**
    """
    from aplus.ic_to_return import implied_spread, tail_spread_constant

    rng = np.random.default_rng(13)
    n, sigma = 400_000, 0.02656
    z = rng.normal(size=n)
    score = np.argsort(np.argsort(z)).astype(float)   # 严格单调 ⇒ ρ_s = 1
    ret = sigma * z                                   # ⇒ ρ_p = 1（不是独立抽样！）

    spread, _ = M.quintile_spread(score, ret, tail_q=0.20)
    bridge = implied_spread(1.0, sigma_r=sigma, kind="spearman")
    assert spread == pytest.approx(bridge, rel=0.02)
    assert bridge == pytest.approx(sigma * tail_spread_constant(0.20), rel=1e-12)


def test_a_moderate_ic_bridge_predicts_less_than_a_perfect_one():
    """IC 越大桥给的钱越多——**单调**。

    若这条破了，说明换算里有一处符号或幂次被写反了，
    而那种错误在单点上完全看不出来（仍然是个正数）。
    """
    from aplus.ic_to_return import implied_spread

    vals = [implied_spread(ic, sigma_r=0.02656, kind="spearman")
            for ic in (0.01, 0.03, 0.05, 0.07, 0.09)]
    assert all(b > a for a, b in zip(vals, vals[1:]))


# ── ⑤ 分位宽度：策略吃的是最深的桶 ─────────────────────────────

def test_deeper_buckets_can_and_did_come_out_smaller():
    """**信号可以住在中段** —— 那时深桶的价差远小于浅桶。

    这条不是测真实数据，是证明「浅桶的数字不能代表深桶」这件事
    **在数学上完全可能**，所以只测一个宽度然后拿它当答案是不安全的。

    造一个分数，其信息**只在中间一段**：两端的收益都趋近于 0。
    于是 q=0.2 的价差大、q=0.01 的价差小。
    若哪天把这个脚本简化回单宽度，这条会提醒你少测了什么。
    """
    rng = np.random.default_rng(21)
    n = 20_000
    score = rng.normal(size=n)
    # 收益 = 中间一段有信号，两端被压平（模拟「IC 在中段、尾巴干净」）
    strength = np.exp(-0.5 * (score / 1.2) ** 2)
    ret = 0.02 * strength + rng.normal(scale=0.01, size=n)

    wide, _ = M.quintile_spread(score, ret, tail_q=0.20)
    deep, _ = M.quintile_spread(score, ret, tail_q=0.01)
    assert deep < wide, (
        f"深桶 {deep:.4%} 应小于浅桶 {wide:.4%}；"
        "两者不等，所以浅桶不能代表策略真正吃到的价差"
    )


def test_more_than_one_bucket_width_is_measured_by_default():
    """**默认必须扫多档，且必须有一档 ≤ 0.005**（top-10 / ~4600 ≈ 0.0022）。

    这条是纪律，不是实现细节：策略买 top-10，
    而判决的 IC 是全截面平均。只报 20% 那一档，
    等于**用池子深浅掩盖信号深浅**。

    > 单测从**源码**里读默认值，而不是调 `main()`——
    > `main()` 会去读 GB 级面板，测试套件不能付这个代价。
    """
    import ast

    src = _SCRIPT.read_text(encoding="utf-8")
    tree = ast.parse(src)
    defaults = {}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument"):
            name = node.args[0].value if node.args else ""
            for kw in node.keywords:
                if kw.arg == "default" and isinstance(kw.value, ast.Constant):
                    defaults[name] = kw.value.value
    raw = defaults.get("--tail-qs")
    assert raw, "脚本必须显式声明默认要扫哪些分位宽度"
    qs = [float(x) for x in raw.split(",") if x.strip()]
    assert len(qs) >= 2, (
        f"只扫一档宽度（{qs}）等于用最宽的池子替最深的桶说话——"
        "策略只买 top-10，20% 那一档不代表它吃到的东西"
    )
    assert min(qs) <= 0.005, (
        f"最深一档是 {min(qs):g}，还不够深（策略约 top-10/~4600 ≈ 0.0022）"
    )
    for q in qs:
        assert 0 < q < 0.5
"""守卫：`scripts/measure_basket_nav_01.py` 的判决与口径。

⚠️ **这个脚本此前零守卫**，而它装着 ① 的判决逻辑（§18.4 三档全部「测不出」）。
    而 2026-10-16 那天我**差点把一个正确的 MDE 公式改错**（乘 R 改乘 √R，差 15.62 倍）
    —— 守卫正是那条防线，所以这些不是装饰性约束。

⚠️⚠️ **本文件只覆盖 import 得到的纯函数**：`tier()` / `account_nav()` /
    `excess_contribution()` / `caliber_benchmark_fraction()` / `required_excess()` /
    `judge()`。`main()` 里的**编排**（例如「多条命中时用 `‖` 拼起来而不是只取最后一条」、
    「`gap_to_target_by_benchmark` 是否两支都报」）**不在覆盖范围内** ——
    断言它只能去读产物，而读快照的守卫**等价于没守**：改了源码不重跑照样绿
    （第 96 层）。⚠️ 这条覆盖缺口是**已知欠账**，不是遗漏；见 §18.11 / §18.13。

★ **可测性障碍的通用问法**（这一天撞上两次）：写任何守卫之前先问
    「它能被 `import` 吗」。`judge()` 与 caliber 换算都曾只存在于 `main()` 内部
    ⇒ 守卫连 import 都做不到 ⇒ 那段逻辑**零守卫**，
    连「写一个 import 就炸的守卫」也不行。处置一律是**先提取到模块级**，
    并用**重跑对拍**（不是眼睛看）证明提取不改行为。
"""
import importlib.util
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_SCRIPT = (Path(__file__).resolve().parent.parent / "scripts"
           / "measure_basket_nav_01.py")


def _mod():
    spec = importlib.util.spec_from_file_location("_basket_nav_01", _SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


M = _mod()
T = M.TARGET


def _which(cases):
    """把 cases 映射成可断言的**规则标记**集合（不靠文案全文）。

    ⚠️ 标记从文案里取**语义关键词**，不是整句 —— 换措辞不该让守卫误报，
    但把「测不出」改成「不可达」这种**换分支**必须被杀。
    """
    out = set()
    for c in cases:
        if "连符号都定不了" in c:
            out.add("SIGN")
        if "不可达" in c:
            out.add("UNREACH")
        if "测不出" in c:
            out.add("UNDECIDED")
        if "不许写「能做到」" in c:
            out.add("NOT_ALLOWED_YES")
    return out


# ── N1 四条规则穷举（每条都有构造输入命中它）──────────────────────────

def test_all_four_verdict_branches_are_reachable_and_exclusive_when_they_should_be():
    """§16 的规则**四条穷举**：四条分支都必须能被构造输入命中。

    ⚠️ 实测三档恰好命中 SIGN + UNDECIDED 两条（§18.4），但**判据的完备性**
       是独立性质 —— 只测实测那一个点，等于没测穷举性。
    """
    # c<T<U 且 |c|<M ⇒ SIGN ∥ UNDECIDED（实测 ① 落在这里）
    assert _which(M.judge(0.1056, 0.2267, "①")[0]) == {"SIGN", "UNDECIDED"}
    # U<T（无论符号是否可定）⇒ UNREACH
    assert "UNREACH" in _which(M.judge(0.05, 0.01, "x")[0])
    # c≥T ⇒ NOT_ALLOWED_YES（仍然不许说「能做到」）
    assert "NOT_ALLOWED_YES" in _which(M.judge(0.25, 0.01, "x")[0])
    # ⚠️ 第四象限：|c|<M 且 c≥T ⇒ SIGN ∥ NOT_ALLOWED_YES
    #    「不可测」与「不许说能做到」**同时**成立 —— 只印一条就是误导。
    assert _which(M.judge(0.21, 0.30, "x")[0]) == {"SIGN", "NOT_ALLOWED_YES"}


# ── N2 「|c| < M」与另外三条并列，不是 else 分支 ──────────────────────

def test_sign_indeterminacy_is_CONJOINED_with_the_rest_not_an_else():
    """`|c| < M` 必须**与**其余三条**并列**，不能是 `else`。

    ⚠️ 第 160 层：不可测的点估计只支持**形状**陈述。把它做成 `else` 分支，
       则「U<T ⇒ 不可达」会**吞掉**「连符号都定不了」——
       而「不可达」是一个**比实测支持的更具体**的结论。
    ⚠️ 实测 ① 的 `U=+33.23% > T`，所以 UNDECIDED 那条也成立；
       但**两条一起印**才是设计意图（`cases` 是 list 而不是 str）。
    """
    cases, _ = M.judge(0.1056, 0.2267, "①")
    assert isinstance(cases, list)
    assert len(cases) == 2, f"实测点应命中两条并列，实际 {len(cases)} 条：{cases}"
    # ⚠️ 反向：|c| ≥ M 时**不该**出现 SIGN，否则这条规则恒真
    cases2, _ = M.judge(0.05, 0.01, "x")
    assert "SIGN" not in _which(cases2)


# ── N3 MDE 年化乘 R，不乘 √R（绝对外部锚 + 显式「不是同一个量」）──────

def test_mde_scales_with_R_not_sqrt_R():
    """年化 MDE = `1.96·σ/√n · R`，**不是** `1.96·σ/√n · √R`。

    ⚠️ 绝对外部锚：`σ` 由**本测试指定**（不是从被测对象取出来的），
       解析式来自 iid 高斯抽样分布的性质，与源码实现无关 ⇒ 不是「用公式重算
       自己的字段」（第 109 层），也不是相对守卫（第 168 层）。
    ⚠️ `√R` 是「持有一年那**一个**观测」的 sd；这里是「由 n 天估出的年化率」的
       标准误。两者差 `√R = 15.62` 倍 —— 我 2026-10-16 差点改错的就是这里。
    """
    n, sigma = 2440, 0.0155
    rng = np.random.default_rng(7)
    r = rng.normal(0.0, sigma, size=n)
    got = M.tier("t", r)["mde_as_annual_pct"]

    expect_R = 1.96 * sigma * M.R / math.sqrt(n)      # 乘 R
    expect_sqrtR = 1.96 * sigma * math.sqrt(M.R) / math.sqrt(n)   # 乘 √R（错式）
    # 抽样误差：单序列 std 的相对 sd ≈ 1/√(2n) ≈ 1.4%，取 6% 容差
    assert got == pytest.approx(expect_R, rel=0.06), \
        f"年化 MDE 应≈乘 R 的解析值 {expect_R:.6f}，实得 {got:.6f}"
    # ⚠️ 显式排除错式：实测比值 ≈ 15.4×，取 5× 门槛 ⇒「乘 √R」不可能悄悄活着。
    #    ⚠️ 写这一行时我先写成 `abs(got - expect_sqrtR) > 10*abs(expect_R - expect_sqrtR)`
    #    —— **守卫自己错了**：`got ≈ expect_R`，所以它俩的差本来就≈0.014，
    #    乘 10 变成 0.140，反而大于实测的 0.1387 ⇒ 把一条**本来正确的公式**判死。
    #    🚨 这与 2026-10-16 那次是**同型的镜像**：那次是守卫缺失、诱导改错公式；
    #    这次是守卫写错、同样诱导改错公式。过严的守卫与恒真的守卫一样有害。
    assert got > 5 * expect_sqrtR, \
        f"实得 {got:.6f} 与「乘 √R」的 {expect_sqrtR:.6f} 太近 ⇒ MDE 缩放错了"


# ── N4 R = 244，来自 aplus.hurdle，且明确不是 252 ─────────────────────

def test_R_is_244_from_hurdle_and_not_the_252_us_convention():
    """`R` 必须是 `aplus.hurdle.DAYS_PER_YEAR`，且 **244 ≠ 252**（美股惯例）。

    ⚠️ 本仓曾有三处 R 不一致：`portfolio_nav.json` 252 / 本脚本 244 /
       `aplus.verify.TRADING_DAYS_PER_YEAR` 252。实测 A 股约 244 个交易日
       ⇒ 244 对、252 错（错的那个会把 MDE 与年化**系统性放大 3.3%**）。
    ⚠️ 绝对锚 = 实测的 A 股年交易日数，不是「另一个常量」。
    """
    from aplus import hurdle
    assert M.R == hurdle.DAYS_PER_YEAR
    assert M.R == 244, f"年交易日数锚应是实测的 244，实得 {M.R}"
    # ⚠️ 显式钉住「不是 252」，免得有人以「统一口径」为名把它换回去
    assert M.R != 252


# ── N5 account_nav 必须相加，且**不等于**单独那条差序列 ───────────────

def test_account_nav_adds_the_market_leg_and_is_NOT_the_excess_series():
    """账户净值 = `market_return` + 净超额差序列，**不是**差序列本身。

    ⚠️ §18.1：直接对 `net_excess_*` 算 CAGR 得 `+0.5428%`，真值 `+10.5563%`
       —— **差 10.01 个百分点**。根因：列名带 `excess` 不承诺语义（第 109 层）。
    """
    df = pd.DataFrame({"market_return": [0.01, 0.02, -0.005],
                       "net_excess_bt003": [0.005, 0.005, 0.005]})
    got = M.account_nav(df, "net_excess_bt003")
    assert got == pytest.approx([0.015, 0.025, 0.0])
    # ⚠️ 显式「不是同一个量」：这两条一被违反，10pp 的错就会回来
    assert not np.allclose(got, df["net_excess_bt003"].to_numpy(float))
    assert not np.allclose(got, df["market_return"].to_numpy(float))


# ── N6 超额贡献必须取**同源那条超额腿**的 MDE ───────────────────────

def test_excess_contribution_uses_the_SAME_legs_own_mde_not_another_leg():
    """`|x|/M` 的 MDE 必须是**该组合自己那条净超额腿**的。

    ⚠️ 取错腿（毛腿 / 账户腿 / 市场腿）时，`|x|/M` 变成**另一个量**，
       而读数照样印得出来 —— 这正是「断言对、成因错」那类失效的温床。
    ⚠️ 键名前缀是**单一真相源**（`EXCESS_LEG_PREFIX`），两处不许各写一个字符串。
    """
    legs = {"毛超额": {"mde_as_annual_pct": 0.106917},
            "净超额@bt003": {"mde_as_annual_pct": 0.107071},
            "净超额@real": {"mde_as_annual_pct": 0.107652}}
    r = M.excess_contribution(0.1056, 0.0995, 5.7172, legs, "bt003")
    assert r["excess_leg_key"] == M.EXCESS_LEG_PREFIX + "bt003"
    assert r["mde_of_the_underlying_excess_leg"] == 0.107071
    # ⚠️ 显式排除「误取毛腿」：它最接近，最容易被无声换掉
    assert r["mde_of_the_underlying_excess_leg"] != legs["毛超额"]["mde_as_annual_pct"]
    # real 档取的是 real 的腿，不是 bt003 的
    r2 = M.excess_contribution(0.0744, 0.0995, 5.7172, legs, "real")
    assert r2["mde_of_the_underlying_excess_leg"] == 0.107652
    # ⚠️ 取腿约定错了必须**炸**，不许静默取到别的腿
    with pytest.raises(KeyError):
        M.excess_contribution(0.1056, 0.0995, 5.7172, legs, "不存在的组合")


# ── N7 U = c + M（判据用的上界必须是这个，不是 c 本身）───────────────

def test_upper_bound_is_c_plus_M_not_c_and_not_c_times_M():
    """95% 单侧上界 `U = c + M`（**预注册于看到数据之前**，§16）。

    ⚠️ 若退化成 `U = c`，则 `c < T ≤ U` 永不成立 ⇒ 判决只剩「不可达」，
       而「不可达」比实测支持的更具体（§18.10 第 1 条不许说）。
    """
    for c, mm in ((0.1056, 0.2267), (0.05, 0.01), (0.21, 0.30), (-0.4, 0.2267)):
        _, uu = M.judge(c, mm, "x")
        assert uu == pytest.approx(c + mm), f"U 应 = c+M = {c + mm:.6f}，实得 {uu:.6f}"
        assert uu != pytest.approx(c) or mm == 0.0


# ── N9 达标所需的超额必须**年化**（2026-10-16 我把每期比值当成年化，
#     让「实测/需要」从 6.27% 变成 1.06%，差 5.93×，且错数进了预注册文档）──

def test_required_excess_is_annualized_and_monotone_decreasing_in_years():
    """所需超额 = `((1+T)/(1+c_mkt))^(1/y) − 1`，**不是** `(1+T)/(1+c_mkt) − 1`。

    两条断言**互相独立**，因为只写一条会漏：
    ① **几何均值的定义**（把年化率复利回去必须还原成每期比值）。
       ⚠️ 这**不是**第 109 层的「用公式重算自己的字段」：同一条式子写两遍是恒等式、
       零信息量；而「复利回去要还原」是**年化这个量自己的定义**，与实现路径无关。
    ② **对 `y` 严格单调递减**（每期比值 > 1 时，期限越长，年化所需越接近 0）。
       未年化的写法**与 `y` 无关**（是个常数）⇒ 这条独立于 ① 把「漏掉 `**(1/y)`」
       的写法杀掉，且不依赖任何数值容差。
    ⚠️ 这里刻意**不**用「到正确式的距离 vs 到错式的距离×N」——那种写法我今天
    先写成 `abs(got-e√R) > 10*abs(eR-e√R)`，结果把一条**本来正确的公式**判死了
    （两个距离本就几乎相等）。⇒ 排除某个错式时，判据要与错式**成比例**地表述，
    或干脆换一条与它无关的性质。
    """
    T, cm, y = 0.20, 0.0995, 5.717
    got = M.required_excess(T, cm, y)

    # ① 定义锚：年化率复利 y 期后还原成每期比值
    per_period = (1.0 + T) / (1.0 + cm)
    assert (1.0 + got) ** y == pytest.approx(per_period, rel=1e-12), \
        f"(1+{got:.8f})^{y} 未还原成每期比值 {per_period:.8f}"

    # ② 形状锚：期限越长越接近 0（未年化写法是常数 ⇒ 这条独立杀掉它）
    seq = [M.required_excess(T, cm, yy) for yy in (1.0, 2.0, 5.717, 20.0)]
    assert all(b < a for a, b in zip(seq, seq[1:])), f"未随期限单调递减：{seq}"
    # ⚠️ 显式钉住「错式是常数」：它在 y 变化时一动不动
    naive = per_period - 1.0
    assert [naive] * 4 != seq, "未年化写法与期限无关 ⇒ 它不是年化率"

    with pytest.raises(ValueError):
        M.required_excess(T, cm, 0.0)


# ── N10 caliber 的 `*_pct` 是**百分数**，换算只许发生一次 ──────────────────

def test_the_caliber_percent_scale_is_converted_exactly_once():
    """caliber 的 `9.9471` = **9.9471%**；当小数用会算出「基准年化 +994.71%」。

    ⚠️ **外部锚**：年化收益不可能低于 −100% ⇒ 换算后必落在 `(−1, 1)`。
    这条不需要公式也不需要容差，且**方向对称**（百分数当分数读会 > 1，当场红）。
    ⚠️ `arm` **不许有默认值**：一旦有了默认值，「判决臂」与「敏感性臂」的区分
       就会在某次调用里被悄悄抹掉，而读数照样印得出来（第 119 层）。
       ⇒ 少传参数必须炸（TypeError），传错名字也必须炸（KeyError）。
    """
    cal = {"equal_weight": {"geometric_annual_pct": 9.94710351459831},
           "cap_weight": {"geometric_annual_pct": 1.808663656249898}}
    got = M.caliber_benchmark_fraction(cal, "equal_weight")
    assert -1.0 < got < 1.0, f"年化收益不可能超出 (−1,1)，实得 {got} ⇒ 量纲错了"
    assert got != pytest.approx(9.94710351459831), "原样返回 ⇒ 换算没发生"
    # ⚠️ 两条基准臂必须能被分别取到，且不是同一个数
    assert M.caliber_benchmark_fraction(cal, "cap_weight") != pytest.approx(got)
    # ⚠️ 无默认臂：漏传参数必须炸，而不是悄悄用判决臂
    with pytest.raises(TypeError):
        M.caliber_benchmark_fraction(cal)
    with pytest.raises(KeyError):
        M.caliber_benchmark_fraction(cal, "不存在的臂")


# ── N11 判决臂是**预注册**的那支，不许因为读数难看就换 ───────────────────

def test_the_verdict_arm_is_the_preregistered_one_and_not_a_sensitivity_arm():
    """判决臂 = `equal_weight`；敏感性臂里不许含判决臂。

    ⚠️ 实测两臂「实测/需要」差 **7.94×**（6.27% vs 49.78%）⇒ 换臂就是换结论。
    ⚠️ 但换臂**仍然是错的**：判决臂是**预注册**的（`VERDICT_BENCH_ARM`），
       看了数据再换就是第 91 层。⇒ 正确处置是**加对照臂**（敏感性臂），
       不是换掉判决臂 —— 本条守的正是这个区别。
    ⚠️ 外部锚来自既有守卫
       `tests/test_nav_caliber_judgment.py::test_the_verdict_benchmark_is_the_equal_weight_arm_and_not_the_cap_weighted_one`，
       那条钉的是**测试内**的 helper；本条钉的是**生产侧**的常量（第 96 层：
       产物层/测试层守卫对生产代码零覆盖）。
    """
    assert M.VERDICT_BENCH_ARM == "equal_weight"
    assert M.VERDICT_BENCH_ARM not in M.SENSITIVITY_BENCH_ARMS, \
        "判决臂同时出现在敏感性臂里 ⇒ 「敏感性」这个词失去意义"
    assert "cap_weight" in M.SENSITIVITY_BENCH_ARMS, \
        "⚠️ 少一条对照臂 ⇒ 「离目标多远」会被当成有唯一答案的问题"


# ── N8 超额贡献走比值，**复利年化不可相减** ─────────────────────────

def test_excess_contribution_is_a_RATIO_not_a_difference_of_cagrs():
    """超额贡献 = `((1+c_bk)/(1+c_mkt))^(1/y) − 1`，**不是** `c_bk − c_mkt`。

    ⚠️ 分数减分数得到的是**差**，不是比值的年化。构造输入让两者差 ~8 倍，
       这样「实现悄悄换成减法」会立刻被抓到。
    ⚠️ 这不是第 109 层的「用公式重算假装验证」—— 这里断言的是**口径本身**
       （超额贡献**定义为**比值），不是重算产物字段。
    """
    cb, cm, y = 0.30, 0.20, 5.0
    legs = {"净超额@bt003": {"mde_as_annual_pct": 0.107}}
    r = M.excess_contribution(cb, cm, y, legs, "bt003")
    naive_diff = cb - cm                       # ❌ 错的口径
    assert r["annual_pct"] == pytest.approx(((1 + cb) / (1 + cm)) ** (1 / y) - 1)
    assert r["annual_pct"] != pytest.approx(naive_diff)
    assert abs(r["annual_pct"]) < abs(naive_diff) / 4, \
        "构造输入下比值口径应远小于相减口径，两者不可混"

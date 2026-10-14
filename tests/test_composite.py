"""组合 IC 恒等式必须**与暴力解对拍**——它是抽取方案的地基。

## 为什么这条最重要

`aplus/composite.py` 的全部依据是一句话：

    ρ(w) = (wᵀΣ_f) / sqrt(wᵀRw)

若它错了，后面**每一层**都是错的，而症状会伪装成
「合成因子没效果」——你会去调因子、调权重、调窗口，
而真正错的是最底下那个恒等式。

> 恒等式错误有个特别恶劣的性质：**它不会崩**。
> 它只是安静地给出一个小一点的数，看起来像「结果不理想」。
> 所以必须用**独立的暴力解**去对拍，而不是靠公式本身自证。

## 抽取方案的地基

每天只存 `R`（8×8）与 `Σ_f`（8），省掉 10 分钟的面板重载。
这条路成立**完全依赖**这个恒等式——
即「`R` 与 `Σ_f` 是组合 IC 的充分统计量」。
`test_the_stored_matrices_are_sufficient` 钉的就是这件事：
从矩阵算出来的 IC，必须与**重新持有原始数据**算出来的一致。
"""
import numpy as np
import pytest

import pandas as pd

from aplus.composite import (DegenerateFactor, composite_ic,
                             daily_ic_series, unpack, walk_forward,
                             daily_stats, optimal_weights, zscore)


def _correlated(n=2000, k=4, rho=0.35, seed=7):
    """造 k 个互相相关的因子 + 一个能被它们线性预测的收益。"""
    rng = np.random.default_rng(seed)
    A = rng.normal(size=(n, k))
    # 把 A 混出相关性：A = Z @ chol(R)
    C = np.full((k, k), rho)
    np.fill_diagonal(C, 1.0)
    L = np.linalg.cholesky(C)
    F = A @ L.T
    # 交替正负的固定 beta：k 任意时都能用，且不会偶然全同号
    beta = np.array([(-1.0) ** i * (0.3 + 0.1 * i) for i in range(k)])
    r = F @ beta + rng.normal(scale=0.8, size=n)
    return F, r, beta


# ── 一、恒等式 vs 暴力解 ─────────────────────────────────────────

def test_the_identity_matches_brute_force_on_random_weights():
    """**本文件的核心断言**：任取 20 组随机权重，
    恒等式给出的组合 IC，必须与「真的把 `wᵀz(F)` 算出来再求相关」一致。

    暴力解 = `np.corrcoef(z(F) @ w, r)`——它不知道 R 和 Σ_f 的存在，
    所以两条路**在实现上独立**。

    > `z(F)` 不是随手加的：`R` 与 `Σ_f` 是相关系数、**无量纲**，
    > 它们隐含因子已 z 标准化。见下一条测试。
    """
    F, r, _ = _correlated()
    Z = zscore(F)
    R, sf = daily_stats(F, r)
    rng = np.random.default_rng(3)
    for _ in range(20):
        w = rng.normal(size=F.shape[1])
        if w @ R @ w <= 1e-12:
            continue
        brute = float(np.corrcoef(Z @ w, r)[0, 1])
        assert composite_ic(w, R, sf) == pytest.approx(brute, abs=1e-10), (
            "恒等式与暴力解不一致——抽取方案（只存 R 与 Σ_f）就建立在它上面"
        )


def test_skipping_the_zscore_gives_a_close_but_wrong_answer():
    """**钉住那个静默错误**：忘记 z 标准化时，结果与正确答案**很接近
    但不等于**。

    这条测试存在的理由：它不会崩、不会告警、看起来完全合理。

    > **偏差大小本身依赖数据**：它约等于各因子当日标准差的离散程度
    > （合成数据各列 sd 接近 → 偏差 ~1e-3；真实因子的 sd 差得更开，
    > 偏差也会更大）。所以本测试**只钉「不等于」**，
    > 不钉具体量级——钉量级等于把夹具的偶然性质当成规律。
    """
    F, r, _ = _correlated()
    R, sf = daily_stats(F, r)
    w = np.array([0.6, -0.4, 0.3, -0.2])
    right = composite_ic(w, R, sf)
    naive = float(np.corrcoef(F @ w, r)[0, 1])
    assert naive != pytest.approx(right, abs=1e-10), (
        "未标准化的结果竟然等于标准化的——恒等式的契约变了，"
        "请确认 daily_stats / composite_ic 的归一化没有被改动"
    )


def test_single_factor_weights_reproduce_the_plain_correlation():
    """`w = e_i` 时组合 IC 必须**恰好等于**该因子的单独 IC。

    这条同时钉住两件事：恒等式自洽，以及**抽取文件里的 Σ_f
    可以与单因子 IC 直接对照**——对照的前提就是这个等式。
    """
    F, r, _ = _correlated()
    R, sf = daily_stats(F, r)
    for i in range(F.shape[1]):
        e = np.zeros(F.shape[1])
        e[i] = 1.0
        assert composite_ic(e, R, sf) == pytest.approx(
            float(np.corrcoef(F[:, i], r)[0, 1]), abs=1e-10)


def test_the_stored_matrices_are_sufficient():
    """**抽取方案本身**：把原始 `F`/`r` 全部丢掉，只留 `R` 与 `Σ_f`，
    之后仍然能算出组合 IC——与丢弃之前完全一致。

    这就是 `scripts/extract_ic_matrices.py` 每天只存 72 个数的依据。
    若哪天恒等式需要更多统计量，这条测试会失败，
    届时抽取文件的字段数必须同步增加。
    """
    F, r, _ = _correlated(n=800)
    R, sf = daily_stats(F, r)
    w = optimal_weights(R, sf)
    before = composite_ic(w, R, sf)

    del F                      # 原始数据已不可得
    after = composite_ic(w, R, sf)
    assert after == pytest.approx(before, abs=1e-12)


# ── 二、最优权重确实是最优 ───────────────────────────────────────

def test_optimal_weights_beat_random_ones():
    """最优权重的 IC 必须**高于**随机权重的 IC——否则权重白算了。

    用中位数而不是均值：一次抽样里均值会被某个极端权重拉偏，
    导致测试在**正确的实现**上偶发红。
    """
    F, r, _ = _correlated(n=3000)
    R, sf = daily_stats(F, r)
    best = composite_ic(optimal_weights(R, sf), R, sf)
    rng = np.random.default_rng(11)
    rand = [composite_ic(rng.normal(size=F.shape[1]), R, sf)
            for _ in range(500)]
    assert best > float(np.median(rand)), (
        f"最优 {best:.4f} 未高于随机中位数 {np.median(rand):.4f}——"
        "权重求解反了，或者 R/Σ_f 装反了"
    )


def test_optimal_weights_are_normalised():
    """`wᵀRw = 1`。`ρ(w)` 对正倍数不变，但权重的**绝对大小毫无意义**——
    留着不归一，下游「权重和应为 1」之类的检查会误报。
    """
    F, r, _ = _correlated()
    R, sf = daily_stats(F, r)
    w = optimal_weights(R, sf)
    assert float(w @ R @ w) == pytest.approx(1.0, abs=1e-10)


def test_optimal_weights_survive_collinear_factors():
    """因子**完全共线**时必须给出解，而不是抛 `LinAlgError`。

    因子共线在真实数据里是常态（mom20 vs mom60、turn20 vs turnratio），
    而 `inv` 在这里会直接崩——崩了就得有人现场加 ridge，
    加了就等于偷偷改了口径。
    """
    F, r, _ = _correlated(k=3)
    F = np.column_stack([F, F[:, 0]])        # 第 4 列与第 1 列完全相同
    R, sf = daily_stats(F, r)
    w = optimal_weights(R, sf)
    assert np.all(np.isfinite(w)), "共线下权重必须是有限值"
    assert np.isfinite(composite_ic(w, R, sf))


# ── 三、退化输入必须**报错**，不许静默填数 ───────────────────────

def test_a_flat_factor_raises_instead_of_contributing_zero():
    """某因子当天全并列 → 秩相关对它**无定义**。

    静默填 0 的后果：那个因子凭空「没有观点」，权重被稀释，
    而组合 IC 看起来还是一个正常的数——**又一次静默失效**。
    """
    F, r, _ = _correlated(k=3)
    F[:, 1] = 7.0                              # 全并列
    with pytest.raises(DegenerateFactor) as e:
        daily_stats(F, r, names=("a", "b", "c"))
    assert "b" in str(e.value), "报错要点名是哪几个因子，否则无从修"


def test_flat_forward_return_raises():
    F, r, _ = _correlated()
    with pytest.raises(DegenerateFactor):
        daily_stats(F, np.zeros_like(r))


def test_a_zero_variance_combination_raises_rather_than_returning_zero():
    """`wᵀRw ≤ 0` 时组合 IC **无定义**——不许返回 0。

    返回 0 会被下游读成「组合没有预测力」，
    而真实含义是「这个权重组合不出信号」。两件事不能混。

    > 注意：**满秩相关矩阵没有零空间**——`wᵀRw > 0` 对任何 `w≠0` 成立，
    > 所以这条不是从 `svd` 随便取一条最小奇异向量（那是近似零，
    > 不是零）。真实能触发的只有两种：**全零权重**、
    > 以及**共线因子造成的奇异 R**——后者才是活着的那个场景。
    """
    F, r, _ = _correlated(k=3)
    R, sf = daily_stats(F, r)
    ok_w = np.array([1.0, -1.0, 0.0])        # 正常权重：组合分仍有方差
    assert float(ok_w @ R @ ok_w) > 0
    assert np.isfinite(composite_ic(ok_w, R, sf))

    with pytest.raises(DegenerateFactor):
        composite_ic(np.zeros(3), R, sf)

    # 共线：第 4 列 = 第 1 列 ⇒ R 精确奇异，存在真零方向
    F2 = np.column_stack([F, F[:, 0]])
    R2, sf2 = daily_stats(F2, r)
    _, sv, Vt = np.linalg.svd(R2)
    assert float(sv[-1]) < 1e-12, "共线应当给出精确的零奇异值"
    null_w = Vt[-1]
    assert float(null_w @ R2 @ null_w) == pytest.approx(0.0, abs=1e-9)
    with pytest.raises(DegenerateFactor):
        composite_ic(null_w, R2, sf2)

# ── 四、滚动扩展窗：权重**不许**看见当年之后的数据 ───────────────

def _long_table(years=(2019, 2020, 2021, 2022), k=3, seed=5, noise=0.7):
    """造一份与抽取脚本同列名的长表。"""
    rng = np.random.default_rng(seed)
    recs = []
    for y in years:
        for _ in range(120):
            A = rng.normal(size=(400, k))
            R = np.full((k, k), 0.3)
            np.fill_diagonal(R, 1.0)
            F = A @ np.linalg.cholesky(R).T
            beta = np.array([(-1.0) ** i for i in range(k)])
            ret = F @ beta + rng.normal(scale=noise, size=400)
            _, sf = daily_stats(F, ret)
            rec = {"date": f"{y}-06-{rng.integers(1, 28):02d}"}
            for j in range(k):
                rec[f"sf__{j}"] = float(sf[j])
            for a in range(k):
                for b in range(k):
                    rec[f"R__{a}{b}"] = float(R[a, b]) if a != b else 1.0
            recs.append(rec)
    return pd.DataFrame(recs)


def test_walk_forward_never_lets_a_year_see_its_own_future():
    """**本节核心断言**：把 2024 年的数据整个换掉，
    **2023 年的权重必须一位都不变**，而 2025 年的权重必须变。

    两半都断不得：
    - 只断前半 → 权重压根没读数据，滚动扩展窗是装饰
    - 只断后半 → **有前视**，判决就是作弊
    """
    factors = ["0", "1", "2"]
    base = _long_table(years=tuple(range(2019, 2026)))
    tampered = base.copy()
    later = tampered["date"].str.startswith("2024")
    tampered.loc[later, "sf__0"] = 0.9        # 2024 彻底翻转
    tampered.loc[later, "R__01"] = -0.85

    w_before = walk_forward(base, factors, need_ic=0.04)
    w_after = walk_forward(tampered, factors, need_ic=0.04)
    b = {r["year"]: r for r in w_before}
    a = {r["year"]: r for r in w_after}

    assert np.allclose(list(b[2023]["weights"].values()),
                       list(a[2023]["weights"].values())), (
        "2023 的权重被 2024 的数据改变了——**有前视**。"
        "判决用的是未来数据，等于作弊。"
    )
    assert not np.allclose(list(b[2025]["weights"].values()),
                           list(a[2025]["weights"].values())), (
        "2025 的权重对 2024 的数据毫无反应——权重根本没接上数据"
    )


def test_equal_weight_years_are_reported_but_never_judged():
    """真实起点没有历史 → 前两年等权，且**只报不判**。

    用它们判决等于承认「你事先就知道它们的 IC」。
    """
    factors = ["0", "1", "2"]
    out = {r["year"]: r for r in
           walk_forward(_long_table(), factors, need_ic=0.04)}
    assert out[2019]["weights_source"] == "equal"
    assert out[2020]["weights_source"] == "equal"
    assert out[2019]["judged"] is False
    assert out[2020]["judged"] is False
    assert out[2021]["weights_source"] == "walk_forward"
    assert out[2021]["judged"] is True


def test_a_year_below_the_bar_is_marked_failed_not_close():
    """不过线就是不过线——不许有「差一点就过」的中间态。

    判决只有 `pass_: bool`，没有 `margin` 字段参与判断：
    有 margin 字段就一定会有人拿它讲「其实只差一点点」。
    """
    factors = ["0", "1", "2"]
    df = _long_table()
    # 门槛抬到秩相关**不可能**达到的位置：判据本身要能一票否决。
    # （不靠「造噪声把 IC 压低」——那同时压低了分母，会把机制一起改掉。）
    out = {r["year"]: r for r in walk_forward(df, factors, need_ic=0.99)}
    judged = [r for r in out.values() if r["judged"]]
    assert judged, "应有判决年份"
    assert any(r["pass_"] is False for r in judged), (
        "噪声这么大还全过，说明门槛根本没生效"
    )


def test_missing_matrix_columns_raise_instead_of_reindexing():
    """抽取表少列必须报错。`df[c]` 缺列会 KeyError，
    但若有人写成 `df.get(c, 0)` 就会**静默填 0**——
    那等于把某个因子当成恒等于零的相关矩阵，还照样出结果。
    """
    df = _long_table().drop(columns=["R__21"])
    with pytest.raises(KeyError):
        unpack(df, ["0", "1", "2"])


def test_degenerate_days_are_skipped_not_zero_filled():
    """退化的一天（零方差）在序列里必须是 NaN 而**不是 0**。

    填 0 会被读成「那天组合 IC 恰好为零」，
    而真实含义是「那天没测出来」——两者对均值的贡献完全不同。
    """
    Rs = np.array([[[1.0, 0.3], [0.3, 1.0]]] * 3)
    sf = np.array([[0.05, 0.02]] * 3)
    Rs[1] = [[1.0, 1.0], [1.0, 1.0]]     # 第 2 天两因子完全共线
    sf[1] = [0.0, 0.0]
    # w 必须落在 R 的**零空间**：w=[1,-1] ⇒ wᵀRw = 0（组合分方差为零）。
    # 注意 w=[1,0] **不是**退化——那等于「只要因子 0」，方差仍是 1。
    ic = daily_ic_series(Rs, sf, np.array([1.0, -1.0]))
    assert np.isnan(ic[1]), "退化那天必须是 NaN"
    assert np.isfinite(ic[0]) and np.isfinite(ic[2])


def test_the_equal_weight_arm_exists_as_a_separate_control():
    """**石川 7.4 的警告必须能被告数据回答**。

    原文（行 14500）：「理论上，变量的权重应和它们预测未来收益率的能力
    成正比。**然而在实践中，等权重配置所有变量往往就是很好的选择。**」

    而 `R⁻¹Σ_f` **正是偏离等权**。所以必须有一个**全程等权**的对照臂，
    与主判决跑**同一门槛**。

    > 本测试只钉「对照臂存在且逐年都用等权」。
    > 它**不是**主判决——读到书上那句就改判据，是第 63 条。
    """
    factors = ["0", "1", "2"]
    df = _long_table(years=tuple(range(2019, 2024)))
    out = walk_forward(df, factors, need_ic=0.99, all_years_equal=True)
    assert {r["weights_source"] for r in out} == {"equal"}, (
        "对照臂必须**每年**都用等权，否则它不是对照臂"
    )
    for r in out:
        w = list(r["weights"].values())
        assert all(abs(x - 1 / np.sqrt(3)) < 1e-12 for x in w)


def test_the_control_arm_never_sees_even_one_past_day():
    """对照臂若也读了历史，它就成了第二个主判决——
    「等权」和「滚动」的分界就没了。

    > **只断「定权」不看过去**：篡改 2022 的数据**当然**会改变 2022
    > 自己的 IC——那是它自己的样本，不是历史。
    > 断言若把这件事也算进泄漏，会逼实现去冻结当年的数据，那是错的。
    """
    factors = ["0", "1", "2"]
    df = _long_table(years=tuple(range(2019, 2024)))
    tampered = df.copy()
    tampered.loc[tampered["date"].str.startswith("2022"), "sf__0"] = 0.9
    a = {r["year"]: r for r in
         walk_forward(df, factors, need_ic=0.04, all_years_equal=True)}
    b = {r["year"]: r for r in
         walk_forward(tampered, factors, need_ic=0.04, all_years_equal=True)}

    for y in a:
        # 任何一年，权重都必须与「过去发生了什么」无关
        assert a[y]["weights"] == b[y]["weights"]
        # 当年自己的 IC 只在该年数据被改动时才变
        if y != 2022:
            assert a[y]["ic_mean"] == pytest.approx(b[y]["ic_mean"]), (
                f"{y} 年既不是被篡改的年份，IC 就不该变"
            )


# ── 五、秩口径：策略靠排名选股，所以统计量必须是秩相关 ────────────
#
# **下面第一条测试描述的是一个已经上线的 bug**，不是假想：
# `extract_ic_matrices.py` 一度对**原始值**做 z 标准化，
# 而前瞻收益那一端已经被 `make_ranked_forward` 排了名，
# 于是算出来的是 corr(z(F原始), rank(r)) —— **一半秩一半原始**。
# 它不崩、不报警，报告照样出数，只是那个数**既不是 Spearman 也不是 Pearson**。
#
# 后果是可量化的：同样的数据，Pearson 口径比 Spearman 高约 1.047 倍（弱信号区），
# 而年化毛超额直接乘 σ_r 与 K —— **1.047 倍直接乘在收益上**。

def _skewed(n=3000, k=3, seed=11):
    """带重尾的因子——**这正是真实因子的形状**（vol/ivol/turnratio）。

    正态数据上原始值与秩几乎无关，用它当夹具这条测试就是假绿。
    """
    rng = np.random.default_rng(seed)
    base = rng.standard_t(df=3, size=(n, k)) * 4.0   # t(3) 重尾
    beta = np.array([0.5, -0.4, 0.3])
    r = base @ beta + rng.standard_t(df=3, size=n) * 0.5
    return base, r


def test_ranking_the_factors_changes_the_answer_on_realistic_factor_shapes():
    """原始值口径与秩口径在重尾数据上**差得出来**，不是同一个数的两种写法。

    > 用正态夹具的话这条测试会假绿：正态下分位数变换近似线性，
    > 两种口径只差千分之几，看不出来。
    """
    from aplus.factor_ic import _rankdata
    F, r = _skewed()
    ic_raw = float(np.corrcoef(zscore(F)[:, 0], r)[0, 1])
    ic_rank = float(np.corrcoef(_rankdata(F[:, 0]), _rankdata(r))[0, 1])
    assert abs(ic_raw - ic_rank) > 0.01, (
        f"重尾因子上两种口径只差 {abs(ic_raw - ic_rank):.4f}——"
        "夹具不够重尾，这条测试就白写了"
    )


def test_rank_zscore_reuses_the_ic_reports_own_tie_handling():
    """必须调用 `aplus.factor_ic._rankdata` 本身，不许自己再写一个排名。

    并列平均怎么算，两处只要有一处不同，两侧的数就**再也比不了**——
    而差异小到没有任何人会发现：单因子与合成之间的对照会整体偏一点，
    看起来仍然「差不多」。
    """
    from aplus.composite import rank_zscore
    from aplus.factor_ic import _rankdata
    F, _ = _skewed()
    got = rank_zscore(F)
    # 构造大量并列（量化过的因子天天如此）
    Fq = np.round(F)
    got_q = rank_zscore(Fq)
    assert np.allclose(got_q, zscore(np.column_stack(
        [_rankdata(Fq[:, j]) for j in range(Fq.shape[1])])), atol=1e-12), (
        "并列处理的差异不会被恒等式吸收——它直接改变 R 与 Σ_f"
    )


def test_the_identity_is_still_exact_on_ranks():
    """**秩化不会破坏恒等式**——因为 z 标准化对秩是仿射变换。

    这是能放心把口径换成秩的全部理由：不是「秩更对」这种偏好，
    而是恒等式**一字不改**照样精确成立。
    """
    from aplus.composite import rank_zscore
    F, r, _ = _correlated()
    Z = rank_zscore(F)
    R, sf = daily_stats(Z, r)
    rng = np.random.default_rng(5)
    for _ in range(15):
        w = rng.normal(size=F.shape[1])
        if w @ R @ w <= 1e-12:
            continue
        brute = float(np.corrcoef(Z @ w, r)[0, 1])
        assert composite_ic(w, R, sf) == pytest.approx(brute, abs=1e-10)


def test_mixing_raw_factors_with_ranked_returns_is_not_either_correlation():
    """钉住那个真实 bug：**混合口径两种口径都不等于**。

    > 这条比「秩口径更准」更有用：它证明当初那个数不是「粗略」，
    > 而是**归类错误**——所以必须作废重算，不能拿它做微调。
    """
    from aplus.composite import rank_zscore
    from aplus.factor_ic import _rankdata
    F, r, _ = _correlated(n=4000, k=3, seed=13)
    mixed = float(np.corrcoef(zscore(F)[:, 0], _rankdata(r))[0, 1])
    spear = float(np.corrcoef(_rankdata(F[:, 0]), _rankdata(r))[0, 1])
    pears = float(np.corrcoef(zscore(F)[:, 0], r)[0, 1])
    assert mixed != pytest.approx(spear, abs=2e-3)
    assert mixed != pytest.approx(pears, abs=2e-3)


def test_rank_zscore_rejects_a_fully_tied_column():
    """全并列 → 秩方差为零 → `DegenerateFactor`。

    秩化**制造**了新的零方差（原始值各不相同但秩全同），
    所以这道守卫在秩口径下**更容易**触发，必须仍然抛出并点名。
    """
    from aplus.composite import rank_zscore
    F = np.array([[1.0, 5.0], [2.0, 5.0], [3.0, 5.0], [4.0, 5.0]])
    with pytest.raises(DegenerateFactor):
        rank_zscore(F)


# ── 六、朴素翻号：石川 7.4 那句话的最强版本 ──────────────────────
#
# 「等权重配置所有变量往往就是很好的选择」——主判决用的 R⁻¹Σ_f
# 正是**偏离等权**。第一版对照臂是「原样等权」，但它有个致命的毛病：
# 本项目 8 个因子里 **7 个 IC 为负**，原样等权的 IC 必然是负的，
# 于是对照臂输得**毫不费力**，读起来像「等权不行」——
# 而真正的问题是「等权**不学任何系数**够不够」。
#
# 朴素翻号只回答后者：按**历史**平均 IC 的符号定向，
# **一个系数都不学**。它比 R⁻¹Σ_f 弱得多，是这条路线真正的及格线。

def test_sign_weights_learn_no_magnitude_only_the_direction():
    """符号臂必须**只用符号**——出现任何 |w| ≠ 1 就是它在偷偷学系数。"""
    from aplus.composite import sign_weights
    sf = np.array([[0.05, -0.03, 0.0], [0.09, -0.01, 0.0],
                   [0.01, -0.08, 0.0]])          # 第三列全 0 → 不定方向
    w = sign_weights(sf)
    # 归一到单位长度，所以 |w| 是 1/√k 而非 1。真正要钉的是
    # **所有 |w| 一模一样**——只要有一个更大，就是它在偷偷学幅度。
    assert np.allclose(np.abs(w), np.abs(w)[0]), f"幅度不等：{w}"
    assert w[0] > 0 and w[1] < 0                    # 符号跟着历史 IC 走
    assert np.isfinite(w).all()


def test_sign_weights_never_see_the_evaluation_year():
    """符号臂的权重只能来自**当年之前**——篡改当年数据不得改变当年权重。

    > 注意：`sign_weights` 自己**没有**「不看未来」的本事，
    > 它拿到什么历史就算什么。挡住前视的是 `walk_forward` 的切片，
    > 所以这条必须从 `walk_forward` 那一层测——
    > 测 `sign_weights` 本身会测出一个**它压根没承诺**的性质。
    >
    > 而且符号臂**看起来**不像会前视（「我就取个符号」），
    > 恰恰因此更容易写错成「用全样本的符号」。
    """
    base = _long_table(years=tuple(range(2019, 2026)), noise=12.0)
    tampered = base.copy()
    # 两个细节缺一不可，少一个这条测试就**假绿**而不是失败：
    #
    # 1. 夹具的 `noise` 必须调到**真实量级**。默认 noise=0.7 时
    #    单因子 IC 高达 0.589 —— 一年 120 天的权重根本压不过它，
    #    篡改永远翻不了符号，测试会「通过」而其实什么也没测到。
    #    noise=12 给出的 IC ≈ 0.08，正落在实测合成 IC 0.062~0.083 里。
    # 2. 必须**翻符号**。合成夹具 beta=[1,-1,1]，sf__0 本就为正，
    #    写成同号只是「加强」，历史均值符号不变。
    tampered.loc[tampered["date"].str.startswith("2024"), "sf__0"] = -0.999
    tampered.loc[tampered["date"].str.startswith("2024"), "sf__1"] = 0.999

    a = {r["year"]: r for r in walk_forward(
        base, ["0", "1", "2"], need_ic=0.04, weight_rule="sign")}
    b = {r["year"]: r for r in walk_forward(
        tampered, ["0", "1", "2"], need_ic=0.04, weight_rule="sign")}

    # 2023 年的符号只能来自 2019-2022 → 篡改 2024 不得改变它
    assert np.allclose(list(a[2023]["weights"].values()),
                       list(b[2023]["weights"].values())), (
        "2023 年的符号臂读了 2024 年——**符号同样是可以前视的**")
    # 反过来：2025 年的符号含 2024，篡改后**必须**变。
    # 两半都断不得——只断前半说明符号臂压根没读历史。
    assert not np.allclose(list(a[2025]["weights"].values()),
                           list(b[2025]["weights"].values()))


def test_the_three_arms_are_all_available_and_distinct():
    """三条臂必须**真的不同**——全等就说明其中一条退化成了另一条。"""
    base = _long_table(years=tuple(range(2019, 2026)))
    factors = ["0", "1", "2"]
    arms = {}
    for rule in ("optimal", "equal", "sign"):
        got = walk_forward(base, factors,
                           need_ic=0.0, judged_from=2021,
                           equal_weight_years=(2019, 2020),
                           weight_rule=rule)
        arms[rule] = {r["year"]: r.get("ic_mean") for r in got if r.get("judged")}
    assert arms["optimal"] != arms["equal"]
    assert arms["optimal"] != arms["sign"]
    assert arms["equal"] != arms["sign"]


def test_the_sign_arm_is_never_stronger_than_the_optimal_arm_by_construction():
    """**在训练期上**符号臂不可能赢——它只学方向，最优臂还学了幅度。

    这不是缺陷而是定义。把它钉住，是为了防止有人把
    「符号臂样本外更差」讲成「符号臂哪里不对」——
    它差是因为它**就该**差，差的幅度才是信息。
    """
    from aplus.composite import sign_weights, train_weights
    base = _long_table(years=tuple(range(2019, 2026)))
    factors = ["0", "1", "2"]
    dates, Rs, sf = unpack(base, factors)
    w_opt = train_weights({"date": dates, "R": Rs, "sf": sf}, factors)
    w_sgn = sign_weights(sf)
    assert composite_ic(w_opt, Rs.mean(0), sf.mean(0)) >= \
        composite_ic(w_sgn, Rs.mean(0), sf.mean(0)) - 1e-12

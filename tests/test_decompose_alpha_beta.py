"""α/β 分解与暴露计算本身的测试 —— **尺子歪了，读数就全是歪的**。

这个分解的用途是判断「11.10%/年的超额里有多少是持续因子暴露」。
如果尺子本身有偏，结论会反过来指向错误的方向，
而报告照样出数、照样像模像样。

## 守住的五件事

1. **恒等式**：`α + β部分 ≡ 合计`。分解漏掉一块，这个等式立刻破。
2. **外部锚**：把 y 造���「恰好由暴露线性生成 + 常数」，
   读出的 α **必须**等于那个常数 —— 不依赖任何实现细节的正确性。
3. **暴露是相对全市场的倾斜**（★ 最容易错、失效方向最坏的一处）：
   在全横截面上排名、再对持仓取均值。
   `robust_z` 是仿射变换，**永远以自身均值为中心** ——
   在持仓内部排名，那几只票的 z 均值**按构造就是 0**，
   于是 8 个 β 全塌成 0，**全部超额被归给 α**，结论完全反过来。
4. **只算涨腿** —— 空头腿在 A 股不可执行，解释变量必须与因变量同一组持仓。
5. **口径不许漂**：分块 t 与 `evolve_race` 同一算法；NaN 全列必须拒绝。

## 为什么第 3 条要做反向检验

这个 bug **不会让任何断言失败** —— 它给出的是一个看起来完全正常的数（0）。
所以必须有一条测试**主动构造那个 bug**、断言它与正确实现**分得开**，
否则守卫只是写着好看。实测两者相距两个数量级：
正确 **1.83~2.28**，错误 **−0.00~0.02**。
"""
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT_BT = _ROOT / "scripts" / "backtest_walk_forward_weights.py"
_SCRIPT_AB = _ROOT / "scripts" / "decompose_alpha_beta.py"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


BT = _load(_SCRIPT_BT, "_bt_ab")
AB = _load(_SCRIPT_AB, "_decomp_ab")


# ── fixture：一段能算出闭式解的横截面 ────────────────────────────

def _dates(n, year=2023):
    """**唯一**的日期标签。

    之前用 `i%12` / `i%28` 拼日期，250 行里撞掉 196 行 ——
    于是「β 部分应为 0」之类的断言其实是在为去重后的随机子集赌运气。
    """
    return pd.Index([f"{year}-{i // 28 + 1:02d}-{(i % 28) + 1:02d}"
                     for i in range(n)])


def _universe(n=200, seed=0):
    """形状与真实横截面一致：index 是 code，列是因子 + 收益。"""
    rng = np.random.default_rng(seed)
    codes = [f"{i:06d}.SZ" for i in range(n)]
    F = pd.DataFrame(rng.normal(size=(n, 2)), index=codes,
                     columns=["mom20", "rev5"])
    F["code"] = codes
    F["actual_pct"] = rng.normal(scale=0.01, size=n)
    return F


def _res(universe, held, direction="up"):
    """构造 `evaluate_weights` 那样的结果表：长表，带 pred_date/direction。

    `codes` 里可以出现横截面没有的 code（退市/新上市），
    真实链路上 `_held_predictions` 就是先 `if c in by_code.index` 过滤过的，
    这里**不过滤**是为了让守卫证明自己扛得住未过滤的输入。
    """
    rows = [{"pred_date": day, "code": c, "direction": direction}
            for day, codes in held.items() for c in codes]
    return pd.DataFrame(rows)


def _top_n(universe, n=10, by="mom20"):
    """按因子取真正的 top-n —— 真实策略选的就是这个，不是 index 前 n 个。

    用 index 前 n 个的话，拿到的是随机票，暴露可能为负，
    于是「涨腿暴露必须为正」这条断言就变成**赌种子**。
    """
    return universe[by].sort_values(ascending=False).index[:n].tolist()


def _bottom_n(universe, n=10, by="mom20"):
    return universe[by].sort_values().index[:n].tolist()


# ── ① 恒等式 ────────────────────────────────────────────────────

def test_alpha_plus_beta_equals_total():
    """`α + β部分 ≡ 合计`。这是恒等式，所以它能抓住「分解漏了一块」。"""
    rng = np.random.default_rng(7)
    n = 400
    X = rng.normal(size=(n, 3))
    y = 0.0004 + X @ np.array([0.002, -0.001, 0.0005]) + rng.normal(scale=0.01, size=n)
    f = [f"f{i}" for i in range(3)]
    frame = pd.DataFrame({"gross_excess": y}, index=_dates(n, 2023))
    for i, c in enumerate(f):
        frame[AB.EXPO_PREFIX + c] = X[:, i]

    r = AB.decompose_one(2023, frame, f, min_obs=10)
    assert r is not None
    lhs = r["alpha_annual_pct"] + r["beta_part_annual_pct"]
    assert lhs == pytest.approx(r["total_annual_pct"], rel=1e-9), \
        "α + β 必须是合计本身，否则分解吞掉了一块收益"


# ── ② 外部锚：y 恰好由暴露生成 ──────────────────────────────────

def test_alpha_is_the_constant_when_y_is_exactly_exposure_driven():
    """★ 外部锚：**不依赖任何实现细节**。

    造 y = 常数 + 暴露的线性组合 + **无噪声**。真实最小二乘必然
    恢复出那个常数 —— 于是 α 读数有了闭式解可对拍。
    """
    rng = np.random.default_rng(11)
    n = 300
    f = ["a", "b"]
    idx = _dates(n, 2024)
    keep = np.asarray(~idx.duplicated())

    # 顺序要紧：**先去重、再中心化、最后才生成 y**。
    # 反过来（先生成 y 再中心化 X）等于把 y 的常数项也挪了位置，
    # 截距会吸收掉那个偏移 —— 断言照样「失败」，但失败原因与它要测的东西无关，
    # 是最难查的一类假红。
    Xc = rng.normal(size=(n, 2))[keep]
    Xc = Xc - Xc.mean(axis=0)
    CONST = 0.0005                       # ≈ 12.2%/年
    y = CONST + Xc @ np.array([0.003, -0.002])

    frame = pd.DataFrame({"gross_excess": y, "expo_a": Xc[:, 0], "expo_b": Xc[:, 1]},
                         index=idx[keep])

    r = AB.decompose_one(2024, frame, f, min_obs=10)
    assert r["alpha_annual_pct"] == pytest.approx(CONST * 244, rel=1e-6)
    assert r["beta_part_annual_pct"] == pytest.approx(0.0, abs=1e-9), \
        "零均值暴露 + 无噪声 ⇒ β 部分恒为 0"
    assert r["resid_daily_sd"] == pytest.approx(0.0, abs=1e-12), \
        "y 完全由暴露生成时不该有残差 —— 有残差说明回归没吃进 X"


def test_a_pure_constant_y_leaves_beta_at_zero_and_alpha_at_the_constant():
    """反向对照：y 若是常数，β 必须塌到 0，α 就是那个常数本身。

    没有这一条，「β 恒等于 0」和「实现漏了 X」看起来一模一样。
    """
    n = 200
    CONST = 0.0003
    idx = _dates(n, 2025)
    rng = np.random.default_rng(3)
    frame = pd.DataFrame({"gross_excess": np.full(n, CONST),
                          "expo_a": rng.normal(size=n),
                          "expo_b": rng.normal(size=n)}, index=idx)[~idx.duplicated()]
    r = AB.decompose_one(2025, frame, ["a", "b"], min_obs=10)
    assert r["alpha_annual_pct"] == pytest.approx(CONST * 244, rel=1e-6)
    assert abs(r["beta_part_annual_pct"]) < 1e-6


def test_the_muscle_would_notice_if_the_exposure_column_were_shifted_one_day():
    """**变异检验**：暴露错位一天，读数就会变。

    测的是「这个测试对错位敏感」，而不是重算一遍分解 ——
    后者只是自证。
    """
    rng = np.random.default_rng(5)
    n = 300
    X = rng.normal(size=(n, 2))
    y = 0.0002 + X @ np.array([0.003, -0.002]) + rng.normal(scale=0.005, size=n)
    idx = _dates(n, 2026)
    base = pd.DataFrame({"gross_excess": y, "expo_a": X[:, 0], "expo_b": X[:, 1]},
                        index=idx)[~idx.duplicated()]
    shifted = base.copy()
    shifted["expo_a"] = np.roll(base["expo_a"].to_numpy(), 1)

    a = AB.decompose_one(2026, base, ["a", "b"], min_obs=10)
    b = AB.decompose_one(2026, shifted, ["a", "b"], min_obs=10)
    assert a["alpha_annual_pct"] != pytest.approx(b["alpha_annual_pct"], rel=1e-3), \
        "暴露错位一天而 α 读数不变 ⇒ 这条测量对最常见的接线错误是瞎的"


# ── ③ 暴露是相对全市场的倾斜（★ 最容易错的一处） ────────────────

def test_exposure_is_the_tilt_versus_the_whole_cross_section():
    """持仓 10 只，但 z 是在 **200 只**上排的 —— 所以均值可以接近 0。

    如果 z 只在这 10 只里排，均值必然是这 10 只里最大的那个，
    恒为正 ⇒ 每个因子的 β 都是正的，纯属算法制造的结论。
    """
    uni = _universe(n=200, seed=1)
    held = {"2023-01-03": _top_n(uni)}
    res = _res(uni, held)

    expo = BT._up_exposure(res, {"2023-01-03": uni}, ["2023-01-03"],
                           ["mom20", "rev5"])
    # 选股只用了 mom20，所以 **rev5 的倾斜可以为负** ——
    # 那不是缺陷，那是「组合其实只在赌一个因子」的直接读数。
    assert expo["mom20"][0] > 1.5, (
        f"mom20 暴露只有 {expo['mom20'][0]:.2f} —— "
        f"在持仓内部排名会让它恒为 0（实测正确值 1.83~2.28）")
    for f in ("mom20", "rev5"):
        v = expo[f][0]
        assert np.isfinite(v), f"{f} 暴露必须是有限值"
        # rev5 没参与选股，倾斜可以为负；但幅度仍受全截面尺度约束。
        assert abs(v) < 3.0, f"{f} 暴露 {v:.2f} 越界 —— z 不该由 10 只票自己定"


def test_ranking_only_the_held_names_collapses_every_exposure_to_zero():
    """★ 反向检验：**主动实现那个 bug**，断言本守卫分得开。

    `robust_z` 是仿射变换，永远以自身均值为中心 ⇒ 在持仓内部排名，
    那几只票的 z 均值**按构造恒为 0**。失效方向因此是
    「β 全塌成 0、超额全归 α」，与直觉相反，
    而且是个**看起来完全正常的数**（0），不会有任何异常抛出。

    实测 200 只正态 / mom20 最强 10 只 / 30 个种子：
    全横截面排名 **1.83~2.28**，持仓内排名 **−0.00~0.02** —— 差两个数量级。
    """
    uni = _universe(n=200, seed=2)
    codes = _top_n(uni)
    day = "2023-01-03"
    res = _res(uni, {day: codes})

    truth = BT._up_exposure(res, {day: uni}, [day], ["mom20"])["mom20"][0]

    from aplus.factor import robust_z
    sub = uni[uni["code"].isin(codes)]
    bug = float(sub.apply(robust_z, axis=0)["mom20"].mean())

    assert abs(bug) < 0.2, "先取持仓再排名 ⇒ z 均值按构造为 0"
    assert truth > 1.5, "全横截面上排名 ⇒ 顶部 5% 的 z 均值应在 1.8 以上"
    assert truth - abs(bug) > 1.5, \
        "两种算法竟给出同一个值 ⇒ 守卫分不出对错，这条测试是摆设"


def test_scattered_holdings_still_produce_a_nonzero_global_exposure():
    """对照组：持仓与顶部分数无关时，暴露应当接近 0 但**不恰好是 0**。

    「恰好为 0」是那个 bug 的签名；「接近 0」是真实组合的中性状态。
    两者必须分得开 —— 否则守卫会把真实的零暴露误报成 bug。
    """
    uni = _universe(n=200, seed=12)
    codes = uni.index[::20].tolist()
    day = "2023-01-04"
    res = _res(uni, {day: codes})
    v = BT._up_exposure(res, {day: uni}, [day], ["mom20"])["mom20"][0]
    assert abs(v) < 1.5, "分散持仓本就不该有强倾斜"
    assert v != pytest.approx(0.0, abs=1e-9), \
        "恰好 0.0 正是「在持仓内部排名」的签名 —— 实现可能退化了"


def test_exposure_ignores_names_missing_from_the_universe():
    """持仓里出现横截面没有的 code（退市/新上市）时不得整体变 NaN。"""
    uni = _universe(n=60, seed=3)
    codes = _top_n(uni, 5) + ["999999.SH"]
    res = _res(uni, {"2023-01-03": codes})
    expo = BT._up_exposure(res, {"2023-01-03": uni}, ["2023-01-03"], ["mom20"])
    assert np.isfinite(expo["mom20"][0]), "5 只真实持仓足够算出暴露"


def test_a_factor_absent_from_the_cross_section_yields_nan_not_zero():
    """★ 变异检验的落点：横截面在、持仓在，唯独**该因子列没有**。

    之前那条（整张横截面缺失）走的是早退分支，压根到不了
    「因子列缺失」这一支 —— 所以把 NaN 换成 0 时测试照样全绿。
    守卫必须盯的是**真正被执行到的那一支**。
    """
    uni = _universe(n=60, seed=8)
    day = "2023-01-05"
    res = _res(uni, {day: _top_n(uni, 5)})
    expo = BT._up_exposure(res, {day: uni.drop(columns=["rev5"])}, [day],
                           ["mom20", "rev5"])
    assert np.isfinite(expo["mom20"][0]), "mom20 存在时必须照常算出暴露"
    assert np.isnan(expo["rev5"][0]), \
        "因子列不存在 ⇒ NaN。填 0 会让「没这个因子」读成「完全不暴露」"


def test_a_factor_that_is_all_nan_on_the_held_names_yields_nan():
    """持仓那天的该因子全是 NaN（数据缺口）⇒ NaN，不是 0。"""
    uni = _universe(n=60, seed=15)
    day = "2023-01-06"
    held = _top_n(uni, 5)
    uni.loc[held, "rev5"] = np.nan
    res = _res(uni, {day: held})
    expo = BT._up_exposure(res, {day: uni}, [day], ["mom20", "rev5"])
    assert np.isfinite(expo["mom20"][0])
    assert np.isnan(expo["rev5"][0])


def test_a_missing_day_yields_nan_rather_than_a_silent_zero():
    """缺横截面的那一天必须是 NaN。填 0 会让「这天没数据」变成
    「这天组合完全不暴露于该因子」—— 两者在回归里天差地别。"""
    uni = _universe(n=60, seed=4)
    res = _res(uni, {"2023-01-03": _top_n(uni, 5)})
    expo = BT._up_exposure(res, {}, ["2023-01-03", "2023-01-04"], ["mom20"])
    assert np.isnan(expo["mom20"][0])
    assert np.isnan(expo["mom20"][1])


# ── ④ 只算涨腿 ──────────────────────────────────────────────────

def test_only_the_up_leg_counts_toward_exposure():
    """空头腿在 A 股不可执行；把它算进暴露，β 描述的就是一个不存在的组合。"""
    uni = _universe(n=100, seed=6)
    up_codes = _top_n(uni)
    dn_codes = _bottom_n(uni)
    res = pd.concat([_res(uni, {"2023-01-03": up_codes}, "up"),
                     _res(uni, {"2023-01-03": dn_codes}, "down")],
                    ignore_index=True)
    expo = BT._up_exposure(res, {"2023-01-03": uni}, ["2023-01-03"], ["mom20"])
    only_up = BT._up_exposure(_res(uni, {"2023-01-03": up_codes}, "up"),
                              {"2023-01-03": uni}, ["2023-01-03"], ["mom20"])
    assert expo["mom20"] == only_up["mom20"], "空头腿竟然进了暴露"


def test_a_down_leg_only_frame_yields_nan_not_zero():
    """★ 空头腿在 A 股**不可执行**，所以它根本不参与暴露。

    守卫点：只喂空头腿时必须给 NaN。给 0 的话，
    「组合完全不暴露于该因子」和「这天没数据」在回归里长得一模一样。
    """
    uni = _universe(n=100, seed=7)
    dn_only = BT._up_exposure(_res(uni, {"d": _bottom_n(uni)}, "down"),
                              {"d": uni}, ["d"], ["mom20"])["mom20"][0]
    assert np.isnan(dn_only), "只有空头腿 ⇒ 没有可算的涨腿暴露，必须是 NaN"


def test_the_up_leg_tilt_is_positive_when_the_picks_were_made_on_that_factor():
    """反向对照：涨腿按 mom20 选 ⇒ 它的 mom20 倾斜必须为正。"""
    uni = _universe(n=100, seed=7)
    up_only = BT._up_exposure(_res(uni, {"d": _top_n(uni)}, "up"),
                              {"d": uni}, ["d"], ["mom20"])["mom20"][0]
    assert up_only > 0, "选的就是 mom20 最强的一批，倾斜不可能为负"


# ── ⑤ 口径不许漂 ────────────────────────────────────────────────

def test_blocked_t_matches_the_projects_shared_implementation():
    """分块 t 必须是 `evolve_race` 那一把尺子，不是本脚本另写的。"""
    from aplus.evolve_race import _tstat
    rng = np.random.default_rng(9)
    x = pd.Series(rng.normal(scale=0.01, size=244))
    assert AB.t_blocked(x, block=21) == pytest.approx(_tstat(x, block=21)[0], rel=1e-12)


@pytest.mark.parametrize("x", [
    np.arange(10.0),                          # 不足 3 个块
    np.zeros(244),                            # 常数序列（退化）
    np.full(244, 0.01) * (1 + 1e-18),         # 变异系数可忽略
])
def test_blocked_t_refuses_to_answer_when_it_cannot(x):
    """算不出就必须给 nan，而不是 0。

    0 会被读成「测了，没信号」；nan 的意思是「没测出来」。
    这两句话在判决里是相反的方向。
    """
    assert np.isnan(AB.t_blocked(x, block=21))


def test_the_blocked_t_keeps_the_projects_degeneracy_guard():
    """★ 变异系数护栏必须还在。

    第一版重写的 `t_blocked` 把它换成了 `sd == 0`。而
    `pd.Series.std` 对常数序列返回 3.5e-18 而不是 0，
    于是那份版本会吐出 3.1e16 的凭空巨值 —— 正是项目修过的那个坑。
    这条测试钉的是「不许再退化回去」。
    """
    from aplus.evolve_race import DEGENERATE_CV
    tiny = np.full(244, 0.02) * (1 + 1e-12)
    assert np.isnan(AB.t_blocked(tiny, block=21)), "变异系数护栏失效"
    assert DEGENERATE_CV > 0


def test_rows_with_a_nan_exposure_are_dropped_and_counted():
    """★ 暴露里有 NaN 的行必须被剔除，且 `n` 要反映**剔除后**的样本量。

    不剔除的话 `lstsq` 会把 NaN 传播进系数，读数全变 NaN；
    更坏的情形是某些实现会静默跳过，于是「n」与实际参与拟合的行数对不上，
    而报告照样出数。剔除后的 n 必须写出来，让人能核对。
    """
    rng = np.random.default_rng(21)
    n = 200
    idx = _dates(n, 2022)
    X = rng.normal(size=n)
    y = 0.0004 + X * 0.01
    expo = X.copy()
    expo[:30] = np.nan                        # 前 30 天该因子没数据
    frame = pd.DataFrame({"gross_excess": y, "expo_a": expo}, index=idx)

    r = AB.decompose_one(2022, frame, ["a"], min_obs=10)
    assert r is not None
    assert r["n"] == int(np.isfinite(expo).sum()), "n 必须是**剔除后**的行数"
    assert np.isfinite(r["alpha_annual_pct"]), "NaN 不该传播进截距"
    assert r["alpha_annual_pct"] == pytest.approx(0.0004 * 244, rel=1e-6)


def test_beta_share_is_the_ratio_of_hand_computed_parts():
    """★ 外部锚：占比的两个分子分母**都在测试里手算**，不用返回值。

    y = 常数 + X·b 且无噪声 ⇒ α = 常数、β 部分 = b·mean(X)、
    合计 = 常数 + b·mean(X)。三者都是闭式的。
    占比 = 1 只在截距恰为 0 时成立 —— 而这里的截距是 0.0002，不是 0。
    """
    rng = np.random.default_rng(23)
    n = 250
    idx = _dates(n, 2023)
    # ★ **不**中心化。真实组合的倾斜本来就是非零均值的（它一直在赌某个方向），
    # 中心化会人为把「β 贡献的水平」压成 0 —— 那是把策略的性质
    # 误当成分解的性质。
    X = rng.normal(size=n) + 1.5
    B, CONST = 0.01, 0.0002
    y = CONST + X * B
    frame = pd.DataFrame({"gross_excess": y, "expo_a": X}, index=idx)

    r = AB.decompose_one(2023, frame, ["a"], min_obs=10)
    mx = float(X.mean())
    assert r["alpha_annual_pct"] == pytest.approx(CONST * 244, rel=1e-8)
    assert r["beta_part_annual_pct"] == pytest.approx(B * mx * 244, rel=1e-8)
    assert r["total_annual_pct"] == pytest.approx((CONST + B * mx) * 244, rel=1e-8)
    # ★ 这里原来断言 `beta_share = β部分/合计`。那个字段已被删：
    #   实测每年 `β/|α| ≈ 1`，而 α + β ≡ 合计 ⇒ 「β 占多少」
    #   在这份数据上不是一个小于 1 的比例，**报出来会被读成「β 占了 3000%」**。
    #   闭式的部分（α、β部分、合计）三条断言一个字没动，它们才是外部锚。
    assert r["alpha_beta_cancellation"] == pytest.approx(abs(B * mx / CONST), rel=1e-8), \
        f"抵消比 {r['alpha_beta_cancellation']:.4f} —— 分子分母必须都是手算值，不能被取反"
    assert r["r2"] == pytest.approx(1.0, abs=1e-9), \
        f"R² {r['r2']:.3f} —— 暴露完全生成 y 时必须解释掉全部变异"


def test_exposure_driving_returns_down_shows_up_as_a_negative_tilt_contribution():
    """方向对照：暴露越高、超额越低 ⇒ **β 部分本身**为负。

    注意不能断「占比为负」：分子分母都是负数，比值必然为正。
    那个断言看着合理、实则在测一个恒真的性质 —— 真正要钉的是
    **β 部分的符号**，因为报告里读的就是那一列。
    """
    rng = np.random.default_rng(24)
    n = 250
    idx = _dates(n, 2024)
    X = rng.normal(size=n) + 1.5
    B, CONST = -0.01, 0.0002
    y = CONST + X * B
    frame = pd.DataFrame({"gross_excess": y, "expo_a": X}, index=idx)

    r = AB.decompose_one(2024, frame, ["a"], min_obs=10)
    mx = float(X.mean())
    assert r["beta_part_annual_pct"] == pytest.approx(B * mx * 244, rel=1e-8)
    assert r["beta_part_annual_pct"] < 0, "暴露在拖累收益 ⇒ β 部分必须为负"
    assert r["total_annual_pct"] < 0 < r["alpha_annual_pct"], \
        "合计为负而 α（未被暴露解释的那部分）仍为正"


def test_decompose_refuses_when_the_sample_is_too_small():
    """样本不足必须返回 None（跳过），而不是硬拟合出一个漂亮的 α。"""
    idx = pd.Index(["2023-01-03", "2023-01-04"])
    frame = pd.DataFrame({"gross_excess": [0.001, 0.002],
                          "expo_a": [0.1, 0.2],
                          "expo_b": [0.3, 0.4]}, index=idx)
    assert AB.decompose_one(2023, frame, ["a", "b"], min_obs=10) is None


def test_ols_recovers_known_coefficients():
    rng = np.random.default_rng(13)
    n = 200
    X = rng.normal(size=(n, 2))
    y = 0.001 + X @ np.array([0.004, 0.001])
    coef = AB.ols(y, X)
    assert coef[0] == pytest.approx(0.001, abs=1e-9)
    assert coef[1] == pytest.approx(0.004, abs=1e-6)
    assert coef[2] == pytest.approx(0.001, abs=1e-6)


# ── ⑥ 落盘形状（第 66 层失效的直接修法） ─────────────────────────

def _chunk(dates, excess, expo=None):
    return {"dates": list(dates), "excess": list(excess),
            "exposure": expo or {}}


def test_the_daily_frame_is_a_dataframe_with_one_row_per_date():
    """★ 这条测试的存在理由：`pd.concat([Series, ...])` 返回 **Series**。

    第一次写落盘时就是这么拼的，K=5/10/42 全绿（它们跑的是更早的版本），
    直到 K=63 第一次真正走到那个分支才当场崩。
    抽成独立函数之后，这条断言只要几毫秒就能守住它。
    """
    c = [_chunk(["2023-01-03", "2023-01-04"], [0.01, -0.02],
                {"mom20": [2.1, 1.9], "rev5": [-0.4, 0.3]})]
    df = BT._daily_frame(c, ["mom20", "rev5"])
    assert isinstance(df, pd.DataFrame), f"拿到的是 {type(df).__name__}，不是 DataFrame"
    # 列序固定：毛超额第一，然后按 `factors` 顺序。
    # 下游按**列名**取列，名字对了但顺序/重名变了照样出事，所以断言全名列表。
    assert list(df.columns) == ["gross_excess", "expo_mom20", "expo_rev5"]
    assert len(df) == 2
    assert df["expo_mom20"].to_numpy().tolist() == [2.1, 1.9]


def test_the_daily_frame_gives_one_column_per_name_across_disjoint_chunks():
    """★ `pd.concat(axis=1)` 是**按索引对齐的 join，不是堆叠**。

    两段**日期完全不重叠**时，按 chunk 拼 Series 会得到两根同名
    `expo_mom20`：第一段那根在第二段的日期上全是 NaN，反之亦然。
    而 `df["expo_mom20"]` 返回的是 DataFrame —— 下游
    `frame[["expo_mom20"]]` 照样不报错，只是喂给 OLS 的是二维数组。

    这个 bug 在**单段 chunk 的测试里永远看不见**，所以必须有双段用例。
    """
    c = [_chunk(["2023-01-03", "2023-01-04"], [0.01, -0.02],
                {"mom20": [2.1, 1.9]}),
         _chunk(["2024-01-03", "2024-01-04"], [0.03, -0.04],
                {"mom20": [3.1, 3.9]})]
    df = BT._daily_frame(c, ["mom20"])
    assert list(df.columns).count("expo_mom20") == 1, (
        f"同名列出现了 {list(df.columns).count('expo_mom20')} 次：{list(df.columns)}")
    assert isinstance(df["expo_mom20"], pd.Series), (
        f"取一列拿到的是 {type(df['expo_mom20']).__name__}，说明列被复制了")
    # 两段的值都必须在位 —— join 而非堆叠的话，后面那段会是 NaN。
    assert df["expo_mom20"].notna().all(), df["expo_mom20"].to_dict()
    assert df.loc["2023-01-03", "expo_mom20"] == pytest.approx(2.1)
    assert df.loc["2024-01-04", "expo_mom20"] == pytest.approx(3.9)
    assert df.loc["2024-01-03", "gross_excess"] == pytest.approx(0.03)


def test_the_daily_frame_keeps_excess_and_exposures_on_one_row():
    """日期是 join 键 —— 超额与暴露错开一行，回归照样出数，只是全错。"""
    c = [_chunk(["2023-01-03", "2023-01-04"], [0.01, -0.02],
                {"mom20": [2.1, 1.9]})]
    df = BT._daily_frame(c, ["mom20"])
    assert df.loc["2023-01-03", "expo_mom20"] == pytest.approx(2.1)
    assert df.loc["2023-01-04", "expo_mom20"] == pytest.approx(1.9)


def test_the_daily_frame_fills_a_missing_factor_with_nan_not_zero():
    """某年缺一个因子 ⇒ 全 NaN 列。填 0 会让「没这个因子」读成「完全不暴露」。"""
    c = [_chunk(["2023-01-03"], [0.01], {"mom20": [2.0]})]
    df = BT._daily_frame(c, ["mom20", "rev5"])
    assert np.isnan(df["expo_rev5"].iloc[0])
    assert not df["expo_rev5"].eq(0).all()


def test_the_daily_frame_tolerates_timestamp_style_dates():
    """日期先转字符串：晚一步就是拿 Timestamp 索引去 reindex 字符串索引，
    全变 NaN 而不报错 —— 回归会安静地丢掉这些行。"""
    c = [_chunk([pd.Timestamp("2023-01-03"), pd.Timestamp("2023-01-04")],
                [0.01, -0.02], {"mom20": [2.1, 1.9]})]
    df = BT._daily_frame(c, ["mom20"])
    assert list(df.index) == ["2023-01-03", "2023-01-04"]
    assert df["expo_mom20"].notna().all()


def test_the_daily_frame_dedups_keeping_the_last_occurrence():
    """跨年边界的重叠段：同一天两次，留**后**一次并让 n 对得上。"""
    c = [_chunk(["2023-01-03", "2023-01-04"], [0.01, -0.02], {"mom20": [2.1, 1.9]}),
         _chunk(["2023-01-04", "2023-01-05"], [0.50, 0.60], {"mom20": [9.9, 8.8]})]
    df = BT._daily_frame(c, ["mom20"])
    assert len(df) == 3, "重复日期不得让同一天被数两遍"
    assert df.loc["2023-01-04", "expo_mom20"] == pytest.approx(9.9)


# ── ⑧ 产物自检必须能区分「这轮写的」和「上一轮的旧文件」 ──────────

def _daily_parquet(path, n_days=80, year=2023, seed=0):
    """造一份最小可用的逐日产物：日期无重复、暴露非全 NaN。"""
    rng = np.random.default_rng(seed)
    dates = pd.date_range(f"{year}-01-01", periods=n_days, freq="D").strftime("%Y-%m-%d")
    data = {"gross_excess": rng.normal(0.0, 0.01, n_days)}
    for f in BT.FACTOR_NAMES:
        data[f"expo_{f}"] = rng.normal(0.0, 1.0, n_days)
    pd.DataFrame(data, index=pd.Index(dates, name="date")).to_parquet(path)
    return path


def test_the_report_selfcheck_is_anchored_to_this_run(tmp_path, monkeypatch):
    """★ `require_keys` 的 `min_mtime` 就是为这件事存在的。

    不传它 ⇒ 自检在「读到上一轮的旧文件」这个**唯一需要它的场景**里失效，
    而那恰恰是本项目读错报告的方式。引用守卫却不传让它生效的参数，
    等于没引用。
    """
    from aplus import report_ready
    seen = {}

    def _spy(path, key_paths, min_mtime=None):
        seen["min_mtime"] = min_mtime
        seen["path"] = path
        return report_ready.Ready(ok=True, reason="spy")

    monkeypatch.setattr(report_ready, "require_keys", _spy)
    monkeypatch.setattr(report_ready, "is_nan_column",
                        lambda df, col: bool(df[col].isna().all()))

    pq = _daily_parquet(tmp_path / "d.parquet")
    out = tmp_path / "decomp.json"
    assert AB.main(["--daily", str(pq), "--out", str(out)]) == 0

    assert "min_mtime" in seen, "调用里没有 min_mtime ⇒ 旧文件也能通过自检"
    assert out.stat().st_mtime >= seen["min_mtime"], (
        "min_mtime 晚于本轮产物 ⇒ 自检会拒掉自己刚写的文件")


def test_a_stale_report_from_a_previous_run_is_rejected(tmp_path, monkeypatch):
    """把产物时间戳改到上一轮 —— 自检必须**报出来**，而不是悄悄放过。"""
    from aplus import report_ready
    monkeypatch.setattr(report_ready, "is_nan_column",
                        lambda df, col: bool(df[col].isna().all()))
    pq = _daily_parquet(tmp_path / "d.parquet", seed=1)
    out = tmp_path / "decomp.json"
    assert AB.main(["--daily", str(pq), "--out", str(out)]) == 0

    old = out.stat().st_mtime - 86_400          # 昨天写的
    import os
    os.utime(out, (old, old))

    ok = report_ready.require_keys(out, ["years.0.alpha_annual_pct"],
                                  min_mtime=out.stat().st_mtime + 1)
    assert not ok.ok, "陈旧文件被当成本轮产物放行了"


# ── ⑨ 生产侧与消费侧的**契约**：两边各自的测试都绿，改了一边另一边不会响 ──

def test_the_daily_frame_produces_exactly_the_columns_the_reader_asks_for():
    """★ `_daily_frame` 写 `f"expo_{f}"`，`decompose_one` 读 `f"{EXPO_PREFIX}{f}"`。

    两个**字面量**分居两个文件，谁也没引用谁。各自单测全绿，
    而把任意一边的 `expo_` 改名，另一边要等到**真跑**才会发现 ——
    发现的方式是 `SystemExit: 缺列`，或者更糟：一整列 NaN 之后
    回归安静地丢掉那些行。

    ⇒ 用「真的生产者 → 真的消费者」端到端钉一次。
    """
    factors = list(BT.FACTOR_NAMES)
    chunks = [_chunk([f"2023-01-{d:02d}" for d in range(1, 13)],
                     [0.001 * i for i in range(12)],
                     {f: [float(i) for i in range(12)] for f in factors})]
    produced = BT._daily_frame(chunks, factors)

    wanted = ["gross_excess"] + [f"{AB.EXPO_PREFIX}{f}" for f in factors]
    assert list(produced.columns) == wanted, (
        "生产者列名与消费者要的不一致："
        f"多 {set(produced.columns) - set(wanted)} / 少 {set(wanted) - set(produced.columns)}")


def test_the_reader_actually_consumes_what_the_producer_wrote():
    """端到端跑一遍：生产者出的表，喂给 `decompose_one` 必须**出得来数**。

    只比列名不够 —— 列名对了但值全是 NaN 时，读者会返回 `None`（样本不足），
    读起来像「这一年没数据」，实际是管道断了。
    """
    factors = list(BT.FACTOR_NAMES)
    n = 60
    rng = np.random.default_rng(7)
    dates = pd.date_range("2023-01-01", periods=n, freq="D").strftime("%Y-%m-%d")
    chunks = [_chunk(dates, list(rng.normal(0, 0.01, n)),
                     {f: list(rng.normal(0, 1, n)) for f in factors})]
    produced = BT._daily_frame(chunks, factors)

    got = AB.decompose_one(2023, produced, factors, min_obs=len(factors) + 12)
    assert got is not None, "生产→消费管道断了（读者认为样本不足）"
    assert np.isfinite(got["alpha_annual_pct"])
    assert np.isfinite(got["r2"])
    # α + β 恒等于合计 —— 跨模块的恒等式，必须端到端成立。
    assert (got["alpha_annual_pct"] + got["beta_part_annual_pct"]
            == pytest.approx(got["total_annual_pct"], abs=1e-12))

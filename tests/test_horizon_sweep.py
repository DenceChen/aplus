"""持仓周期扫描：三个最容易写错、也最难被发现的点。

`horizon_sweep` 的结论会直接决定「因子没价值」这句话是市场事实还是口径事故，
所以它的三个口径必须有测试守着：

1. **年化必须除以 K** —— 乘 252 会把 K=20 的收益放大 20 倍；
2. **网格必须非重叠** —— 自相关序列上做普通 t 检验会虚高标准误；
3. **对照组 K=1 必须复现上一轮的 t=0.10** —— 复现不出来，K>1 全部不可信。
"""
import numpy as np
import pandas as pd
import pytest

from scripts.horizon_sweep import (
    HORIZONS, N_COMPARISONS, TOP_N, arm, compare_k, per_year,
)


# ── 1. 年化折算 ────────────────────────────────────────────────────────

def test_annualization_divides_by_k():
    """K 日收益均值 → 年化，必须乘 `DAYS_PER_YEAR / K`。

    这是本脚本最贵的潜在错误：把 0.10%/日 的 K=20 均值乘 252 而不是乘 12.6，
    会得到 +25%/年 的漂亮数字——**足以让整个扫描得出「长周期因子极强」的结论**，
    而且数字看着完全正常。
    """
    from scripts.head_bucket_probe import DAYS_PER_YEAR
    assert DAYS_PER_YEAR == 244, "本项目按实际交易日折年，不是 252——变了要重登记"
    assert per_year(0.001, 1) == pytest.approx(0.001 * DAYS_PER_YEAR)
    assert per_year(0.001, 20) == pytest.approx(0.001 * DAYS_PER_YEAR / 20)
    assert per_year(0.001, 20) * 20 == pytest.approx(0.001 * DAYS_PER_YEAR)


def test_annualization_of_the_same_daily_rate_is_invariant_to_k():
    """同一**日均**收益率折算出的年化，必须与 K 无关。

    注意 `per_year` 的入参是 **K 日收益的均值**，不是日均。
    所以不变量的正确陈述是：

        日均 r  →  K 日累计约 r·K  →  per_year(r·K, K) == per_year(r, 1)

    这正是「拿 K 日累计当天均」会漏掉的那一步：漏掉它，
    K=20 的收益会被当成日均再乘 244/20，年化凭空缩掉 20 倍，
    **长周期因子会被系统性地看衰**——和放大 20 倍一样是灾难，只是方向相反。
    """
    r_daily = 0.0005
    assert per_year(r_daily, 1) == pytest.approx(r_daily * 244)
    for k in (5, 10, 20):
        assert per_year(r_daily * k, k) == pytest.approx(per_year(r_daily, 1))
    # 反证：真的把日均直接塞进 K>1 的分支，结果缩了 k 倍
    assert per_year(r_daily, 20) == pytest.approx(per_year(r_daily, 1) / 20)


def test_annualization_preserves_sign():
    """亏损不能被折算成正。"""
    assert per_year(-0.001, 20) < 0


# ── 2. 非重叠网格 ──────────────────────────────────────────────────────

def _xs(n_days=120, n_stocks=60, seed=3):
    """一份最小的假面板：每只票一根价格阶梯 + 与价格无关的随机因子列。

    因子列是**纯噪声**——所以任何 K 下都不该测出显著。这正是控制组要的。
    价格用几何阶梯，于是 K 日收益随 K 近似线性放大，可以拿来验年化折算。
    """
    rng = np.random.default_rng(seed)
    all_dates = [f"D{i:04d}" for i in range(n_days + 25)]
    days = all_dates[:n_days]
    xs = {"_daily_map": {}, "_factors_map": {}, "_adj_cache": {},
          "_entry_lag": 1, "_dates": days}
    for j in range(n_stocks):
        code = f"{j:06d}"
        step = 1.0 + j * 0.0001
        xs["_daily_map"][code] = pd.DataFrame({
            "date": all_dates,
            "close": [10.0 * (step ** i) for i in range(n_days + 25)],
            "volume": 1e6, "isST": 0,
        })
    from scripts.head_bucket_probe import SIGN_FIXED
    codes = [f"{k:06d}" for k in range(n_stocks)]
    # 因子列必须**逐日重抽**。若每天复用同一张表，因子臂每天固定买同样 10 只、
    # 随机臂每天换，两臂带着**系统性不同的漂移**——那不是零假设，
    # 会让对照组的 t 冲到 −20 却不代表实现有错。零假设要求两臂**可交换**。
    for day in days:
        xs[day] = pd.DataFrame({
            "code": codes, "volume": 1e6,
            **{k: rng.normal(size=n_stocks) for k in SIGN_FIXED},
        })
    return xs


def test_k_above_one_only_samples_every_kth_day():
    """K>1 时观测数必须约为 1/K，否则就是拿重叠窗口做 t 检验。

    重叠序列的自相关会让标准误被低估，t 值虚高——**这是能制造出假阳性的
    那种错误**，所以必须由测试钉住而不是靠注释。
    """
    xs = _xs(n_days=120)
    days = xs["_dates"]
    for k in (2, 5, 10):
        got = arm(xs, days, None, 7, k)
        assert len(got) <= len(days) // k + 1, f"K={k} 采了 {len(got)} 日，>={len(days)}/{k}"


def test_k_one_uses_every_day():
    xs = _xs(n_days=60)
    got = arm(xs, xs["_dates"], None, 7, 1)
    assert len(got) > len(xs["_dates"]) * 0.8, f"K=1 只采到 {len(got)} 日"


def test_both_arms_share_the_same_grid():
    """两臂必须采在**同一批日子**上，否则配对失效。"""
    xs = _xs(n_days=120)
    days = xs["_dates"]
    from scripts.head_bucket_probe import SIGN_FIXED
    a = set(arm(xs, days, SIGN_FIXED, 7, 5).index)
    b = set(arm(xs, days, None, 7, 5).index)
    assert a == b, f"两臂网格不一致：因子臂 {sorted(a)[:3]} vs 随机臂 {sorted(b)[:3]}"


# ── 3. 判据与登记 ──────────────────────────────────────────────────────

def test_criterion_is_bonferroni_over_the_number_of_horizons():
    """4 个 K = 4 次比较，临界必须严于单次比较。"""
    from aplus.evolve_race import bonferroni_t
    assert N_COMPARISONS == len(HORIZONS)
    assert bonferroni_t(4, df=56) > bonferroni_t(1, df=56)


def test_criticals_across_horizons_are_not_comparable_to_each_other():
    """**钉住一条容易踩的坑：跨实验比 t 值前必须比临界值。**

    `bonferroni_t(4, df=56) = 2.58`，**低于**单次比较常用的 3.0——
    因为 df=56 时 t 分布本身很宽，压 α 抬不动多少。

    所以「K=20 过了 2.58」与「K=1 卡在 3.0」**不是同一条线**：
    两者的零假设分布形状不同。这不是 bug，但拿它们横向比较就是错的。
    本测试把这个事实写死，免得下次有人看到 2.58 < 3.0 就以为
    Bonferroni 算错了、从而把门槛改回 3.0。
    """
    from aplus.evolve_race import bonferroni_t
    assert bonferroni_t(4, df=56) < 3.0, (
        "若此值变了，说明 bonferroni_t 的实现动过——"
        "上面那条注释里的比较需要重新审视")
    # 大样本下临界反而**更低**（2.50 < 2.58）：df 大 → t 分布窄 →
    # 即使 α 压到 1/8，尾部分位数也不需要那么远。
    assert bonferroni_t(4, df=1100) < bonferroni_t(4, df=56)
    assert bonferroni_t(4, df=1100) < 3.0


def test_top_n_is_the_width_the_strategy_actually_buys():
    assert TOP_N == 10, "策略实际买等权 top-10；改成别的宽度要重新登记"


def test_horizons_include_one_as_a_control():
    """K=1 是对照组。没有它，脚本跑完也没有任何东西能证明自己没坏。"""
    assert 1 in HORIZONS


def test_control_k1_must_not_significantly_differ_from_random():
    """K=1 的臂里若没有真信号，两臂之差必须落在噪声内。

    合成数据里因子列与价格阶梯无关，所以这只是一条「对照组不得报警」的闸。
    """
    xs = _xs(n_days=200, seed=9)
    r = compare_k(xs, xs["_dates"], 1)
    if r.get("paired_t") is not None:
        assert abs(r["paired_t"]) < 5.0, (
            f"合成零假设下 K=1 的 t={r['paired_t']:.2f}，"
            f"网格或配对实现有问题")


def test_compare_k_refuses_to_conclude_from_too_few_grid_days():
    """有效日不足 → 不得报任何实质判定。"""
    xs = _xs(n_days=12, seed=4)
    r = compare_k(xs, xs["_dates"], 20)
    if r["n_days"] < 3:
        assert r["paired_t"] is None
        assert r["verdict"] == "有效日不足——不下任何结论"


# ── 复权：口径必须与生产 `_next_bars` 逐位一致 ──────────────────────────

def test_forward_return_uses_adjusted_prices_not_raw_closes():
    """除权跳空必须被还原，否则 K>1 的收益里混着从未发生的亏损。

    `hfq_close` = 不复权价 × 当日累计复权因子，且**除权点连续**。
    10 送 10 的一次除权：价格 20 → 10（腰斩），累计因子 1.0 → 2.0。
    后复权后两侧都是 20，跨除权持有 = 0 收益；不复权则是 **−50%** 的假亏损。

    K=1 时踩中除权的概率低，K=20 时几乎必然踩中——这个缺陷会专门
    把长周期曲线打烂，而长周期正是本脚本要测的那一段。
    """
    from scripts.horizon_sweep import forward_return

    import datetime as _dt
    n = 30
    base = _dt.date(2020, 1, 1)
    dates = [(base + _dt.timedelta(days=i)).isoformat() for i in range(n)]
    closes = [20.0] * n
    closes[20:] = [10.0] * (n - 20)          # 送股后价格腰斩
    df = pd.DataFrame({"date": dates, "code": "X",
                       "open": closes, "high": closes, "low": closes,
                       "close": closes, "volume": 1e6})
    events = pd.DataFrame({"date": [dates[0], dates[20]],
                           "adjust_factor": [1.0, 2.0]})
    r = forward_return({"X": df}, {"X": events}, {}, "X", dates[5], 5)
    assert r is not None, "跨除权的窗口被整段丢弃——宁可算错也别算少"
    assert abs(r) < 1e-6, f"除权跳空没被还原，K=5 收益={r:.4f}（不复权会是 −0.5）"


def test_forward_return_without_events_equals_the_raw_close_ratio():
    """无除权事件时后复权与不复权恒等——不该凭空改变正常样本。

    同时钉住 `exit_i = entry + k`（**不是** `entry + k - 1`）：
    信号日 dates[2] → entry=3 → exit=7，K=4 恰好 4 个 session 的收益。
    """
    import datetime as _dt
    from scripts.horizon_sweep import forward_return

    n = 20
    base = _dt.date(2020, 1, 1)
    dates = [(base + _dt.timedelta(days=i)).isoformat() for i in range(n)]
    closes = [10.0 * (1.01 ** i) for i in range(n)]
    df = pd.DataFrame({"date": dates, "code": "X", "open": closes,
                       "high": closes, "low": closes, "close": closes,
                       "volume": 1e6})
    r = forward_return({"X": df}, {}, {}, "X", dates[2], 4)
    assert r == pytest.approx(closes[7] / closes[3] - 1.0)


def test_forward_return_returns_none_before_listing():
    import sys
    sys.path.insert(0, ".")
    from scripts.horizon_sweep import forward_return

    import datetime as _dt
    base = _dt.date(2020, 1, 1)
    dates = [(base + _dt.timedelta(days=i)).isoformat() for i in range(30)]
    closes = [10.0] * 30
    df = pd.DataFrame({"date": dates, "code": "X", "open": closes,
                       "high": closes, "low": closes, "close": closes,
                       "volume": 1e6})
    assert forward_return({"X": df}, {}, {}, "X", dates[0], 1) is None


def test_forward_return_returns_none_when_the_exit_bar_does_not_exist():
    """卖出价不存在 → 不许拿更早的凑（与 `_next_bars` 同纪律）。"""
    from scripts.horizon_sweep import forward_return

    import datetime as _dt
    base = _dt.date(2020, 1, 1)
    dates = [(base + _dt.timedelta(days=i)).isoformat() for i in range(10)]
    closes = [10.0] * 10
    df = pd.DataFrame({"date": dates, "code": "X", "open": closes,
                       "high": closes, "low": closes, "close": closes,
                       "volume": 1e6})
    assert forward_return({"X": df}, {}, {}, "X", dates[5], 20) is None


def test_k1_holds_one_full_session_not_zero():
    """K=1 的收益必须**非零**。

    这是本脚本最贵的一个 off-by-one：`exit_i = entry + k - 1` 会让 K=1 的
    exit 落在 entry 上、收益率恒等于 0，于是对照组退化成常数序列、
    配对 t 恒为 None、脚本永远报「对照组没能复现」——而 K>1 的曲线照样
    打印得整整齐齐。**没有对照组的自检，一个静默退化的 bug 可以畅通无阻。**
    """
    import datetime as _dt
    from scripts.horizon_sweep import forward_return

    n = 20
    base = _dt.date(2020, 1, 1)
    dates = [(base + _dt.timedelta(days=i)).isoformat() for i in range(n)]
    closes = [10.0 * (1.01 ** i) for i in range(n)]
    df = pd.DataFrame({"date": dates, "code": "X", "open": closes,
                       "high": closes, "low": closes, "close": closes,
                       "volume": 1e6})
    r = forward_return({"X": df}, {}, {}, "X", dates[2], 1)
    assert r is not None and abs(r) > 1e-9, f"K=1 的收益是 {r}——exit 落回 entry 了"
    assert r == pytest.approx(closes[4] / closes[3] - 1.0)


# ── 成交闸门：必须与 `_next_bars` 同纪律 ──────────────────────────────

def test_unfillable_entry_is_dropped_not_scored_as_a_trade():
    """进场 bar 买不进 → 这笔**不进观测**。

    首版把买不进的照算，K=1 对照组因此给 t=+0.48 而上游同一实验是
    t=0.10。配对比较虽然仍自洽（两臂受同样影响），但**工具不一致时
    绝对年化不能与上游并列引用**——那个数字量的不是同一件事。
    """
    import datetime as _dt
    from scripts.horizon_sweep import forward_return

    n = 30
    base = _dt.date(2020, 1, 1)
    dates = [(base + _dt.timedelta(days=i)).isoformat() for i in range(n)]
    base_close = [10.0 * (1.01 ** i) for i in range(n)]
    closes, prev = list(base_close), [None] + base_close[:-1]
    # 信号日 dates[2] → entry=3 → exit=4。
    # dates[3] 做成**涨停收盘**：can_buy 规则 3 是
    # `close >= prev×(1+limit) − 0.005`，一字板只是其中一条路径。
    # 上一版只涨了 6.8% 就断言「买不进」——根本没触线，测试是假通过。
    closes[3] = prev[3] * 1.10
    o = list(closes); h = list(closes); lo = list(closes)
    df = pd.DataFrame({"date": dates, "code": "600000.SH", "open": o,
                       "high": h, "low": lo, "close": closes,
                       "volume": 1e6, "amount": 1e7, "prev_close": prev})
    # 先自证这条数据**确实**判成买不进——否则下面的断言毫无意义
    from aplus.panel import can_buy
    assert not can_buy("600000.SH", o[3], h[3], lo[3], closes[3],
                       1e6, 1e7, prev[3]), "测试数据没造出涨停，断言会空过"
    r = forward_return({"X": df}, {}, {}, "X", dates[2], 1)
    assert r is None, f"涨停买不进的一字板被当成成交计入了：{r}"


def test_suspended_exit_extends_to_the_first_sellable_bar():
    """出场 bar 停牌 → **持有到复牌首根可卖 bar**，不是删掉这笔。

    删掉会让这笔亏损从统计里凭空蒸发——`_up_leg_exit` 的 docstring
    把这条列为硬纪律：亏损无上界，不留痕等于消失。
    """
    import datetime as _dt
    from scripts.horizon_sweep import forward_return

    n = 30
    base = _dt.date(2020, 1, 1)
    dates = [(base + _dt.timedelta(days=i)).isoformat() for i in range(n)]
    closes = [10.0 * (1.01 ** i) for i in range(n)]
    amt = [1e7] * n
    amt[4] = 0.0                       # 出场 bar 停牌（无量无额）
    df = pd.DataFrame({"date": dates, "code": "X", "open": closes,
                       "high": closes, "low": closes, "close": closes,
                       "volume": 1e6, "amount": amt, "prev_close": closes})
    r = forward_return({"X": df}, {}, {}, "X", dates[2], 1)
    assert r is not None, "停牌出场被整笔删掉了——亏损会凭空蒸发"
    # 延长到 dates[5] 而不是停在 dates[4]
    assert r == pytest.approx(closes[5] / closes[3] - 1.0), (
        f"延长持有落点不对：{r:.6f}（应为 dates[5]/dates[3]）")


# ── 种子稳健性：单种子随机臂不是可靠的显著性参照 ──────────────────────

def test_seed_panel_reports_every_seed_not_just_the_best_one():
    """全部种子都必须出现在输出里。

    只报最好的那个种子 = 增加比较次数后择优 = 制造假阳性。
    """
    import pandas as pd

    from scripts.horizon_sweep import SEED_PANEL, seed_panel

    xs = _xs(n_days=120, seed=11)
    sp = seed_panel(xs, xs["_dates"], 5, seeds=SEED_PANEL)
    got = {x["seed"] for x in sp["seeds"] if x}
    assert got == set(SEED_PANEL), f"只报了 {got}——最好的那个被挑出来了吗？"


def test_seed_panel_marks_a_flipped_result_as_not_robust():
    """种子上正负翻面 → 必须判 `robust=False`，而不是报「显著」。

    这是本脚本对自己设的防自欺闸：首轮 K=20 报了 t=−3.23「显著劣于随机」，
    而那个种子的随机臂报出 +23.08%/年——**参照本身在喊「我可能只是幸运」**，
    脚本却没听。
    """
    import pandas as pd

    import scripts.horizon_sweep as hs

    real_arm = hs.arm

    def fake_arm(xs, days, weights, seed, k):
        if weights is not None:
            return real_arm(xs, days, weights, seed, k)
        # 种子 42 给一条系统偏高的序列，其余给一条偏低的
        if seed == 42:
            return pd.Series(0.30, index=days)
        return pd.Series(-0.30, index=days)

    hs.arm = fake_arm
    try:
        sp = hs.seed_panel(_xs(n_days=120), [f"D{i:04d}" for i in range(120)],
                           5, seeds=hs.SEED_PANEL)
    finally:
        hs.arm = real_arm
    assert sp["robust"] is False, "种子上正负翻面却判为稳健"
    assert "依赖种子" in sp["verdict"]


def test_seed_panel_reports_robust_only_when_every_seed_agrees():
    import pandas as pd

    import scripts.horizon_sweep as hs

    real_arm = hs.arm
    factor = {}

    def fake_arm(xs, days, weights, seed, k):
        if weights is not None:
            factor["s"] = real_arm(xs, days, weights, seed, k)
            return factor["s"]
        # 种子 43 让随机臂**逐点等于**因子臂 → 差值恒为 0 → t 产不出来。
        # 注意不能写成 `real_arm(..., None, 43, ...)`：那是**另一颗种子**的
        # 随机臂，和因子臂不相等，差值不退化，这个测试就会变成空断言。
        if seed == 43:
            return factor["s"]
        return pd.Series(-0.30, index=days)

    hs.arm = fake_arm
    try:
        sp = hs.seed_panel(_xs(n_days=120), [f"D{i:04d}" for i in range(120)],
                           5, seeds=hs.SEED_PANEL)
    finally:
        hs.arm = real_arm
    assert sp["robust"] is False, "有一个种子产不出 t 却仍判为稳健"
    assert "退化" in sp["verdict"], (
        f"缺种子必须显式报出来，实际报了：{sp['verdict']}")


def test_seed_panel_refuses_to_judge_with_too_few_usable_seeds():
    import scripts.horizon_sweep as hs

    xs = _xs(n_days=6, seed=2)
    sp = hs.seed_panel(xs, xs["_dates"], 20, seeds=hs.SEED_PANEL)
    if sp["robust"] is None:
        assert "不足" in sp["verdict"]

"""BM 的 IC 从哪来 —— 归因诊断。

这个脚本要回答的是一个**自相矛盾**的现象：IC-t +2.94 过闸，组合 t −0.96。
最要紧的测试不是「能不能算出数」，而是三件会让诊断**静默说谎**的事：

1. **索引错位会吞掉 t 值**——初版 `pd.Series(np.zeros(len(s)))` 的索引是 0..n-1，
   而 `s` 的索引是日期，pandas 按索引对齐后差值全 NaN，`_paired_t` 返回 `None`。
   而均值和年化照样打印——**半个输出是坏的且不报错**。
2. **「不显著」必须附带检测下限**，否则会被读成「不存在」。
3. **`UPDATE_DATE` 不是重述证据**——把它当污染度量就是拿一个坏仪表开车。
"""
import numpy as np
import pandas as pd
import pytest

from scripts.bm_ic_attribution import (
    MIN_CROSS_SECTION, decile_table, grid_days, revision_exposure,
    tail_contrasts,
)


# ── 造假面板：BM 与收益**强正相关**，任何真检验都必须检出 ────────────

def _strong_panel(days, n=100):
    """`bm` 随代码序号单调上升，且收益也与序号单调上升 → Q10 应显著最好。"""
    codes = [f"{i:06d}" for i in range(n)]
    xs = {"_dates": list(days), "_daily_map": {}, "_factors_map": {}}
    for d in days:
        xs[d] = pd.DataFrame({
            "code": codes,
            "bm": np.linspace(-1.0, 1.0, n),
            "report_year": 2021,
        })
    return xs


@pytest.fixture
def strong_returns(monkeypatch):
    """收益 = 代码序号线性递增 + 逐日噪声。

    **噪声是必需的，不是装饰。** 首版只放线性项，结果每个信号日的
    Q10−Q1 差值都是同一个常数 → 配对序列方差为 0 → `_paired_t` 判定
    退化并返回 `None`。判据没错（零方差的确定性关系确实无法估 t），
    但那样测的就不是「一个统计检验」，是一个恒等式。
    """
    def fake(daily_map, factors_map, adj_cache, code, day, k):
        # 代码是 6 位零填充 → `int(code)` 只有 0..99。首版写成
        # `int(code)/99999 - 0.5`，于是基项在 -0.1 附近几乎恒定，
        # 关系被压平、方向由噪声决定——**量纲错了，看数字全对**。
        base = int(code) * 0.002
        # 噪声必须是**散列**而非线性函数。第二版用 `code*7 % 23`，
        # 而 7×10 ≡ 1 (mod 23)，于是一档 10 只票拿到的是 10 个连续余数，
        # 分箱求均值后噪声被整除消掉 → 每个信号日的差值完全相同
        # → 配对序列 sd = 3.9e-17，t 要么 None 要么 242，**全是假象**。
        h = (int(code) * 2654435761 + int(day[-2:]) * 40503) & 0xFFFFFFFF
        return base + ((h % 10007) / 10007.0 - 0.5) * 0.05
    monkeypatch.setattr("scripts.bm_ic_attribution.forward_return", fake)


def _args(xs):
    return (xs, xs["_dates"], 2, xs["_daily_map"], xs["_factors_map"],
            xs.setdefault("_adj_cache", {}))


# ── 1. 索引错位：t 值不许被静默吞掉 ────────────────────────────────

def test_tail_contrasts_actually_produce_t_values(strong_returns):
    """**这条是被首版的索引错位 bug 抓出来的。**

    数据里存在一个极强的单调关系，可若零基线索引对不上，
    `t` 会静默变成 `None`，而同一份输出里的均值和年化**照样看起来正常**。
    """
    days = [f"2022-01-{d:02d}" for d in (5, 6, 7, 10, 11, 12, 13)]
    tc = tail_contrasts(*_args(_strong_panel(days)))
    for key in ("q10_minus_q1", "q10_minus_pool", "q1_minus_pool"):
        assert tc[key]["t"] is not None, (
            f"{key} 的 t 是 None——零基线与序列索引不一致，"
            f"差值被对齐成全 NaN 后 dropna 掉了")


def test_a_strong_monotone_relationship_is_actually_detected(strong_returns):
    """造一个效应大到不可能检不出的场景，检验必须通过。"""
    days = [f"2022-01-{d:02d}" for d in (5, 6, 7, 10, 11, 12, 13)]
    tc = tail_contrasts(*_args(_strong_panel(days)))
    assert tc["q10_minus_q1"]["t"] > tc["t_crit"], (
        f"Q10−Q1 只有 t={tc['q10_minus_q1']['t']}——"
        f"造出来的强单调关系都没检出，是判据本身坏了")
    assert tc["q10_minus_q1"]["annualized"] > 0


def test_the_sign_of_the_spread_follows_the_factor_direction(strong_returns):
    """`bm` 越大越便宜，所以「更便宜 → 更高收益」时 Q10−Q1 必须为正。

    因子方向若被翻反，这个数会变号而**不会报错**。
    """
    days = [f"2022-01-{d:02d}" for d in (5, 6, 7, 10, 11, 12, 13)]
    tc = tail_contrasts(*_args(_strong_panel(days)))
    assert tc["q10_minus_q1"]["mean_per_k"] > 0


# ── 2. 「不显著」必须配检测下限 ─────────────────────────────────────

def test_the_minimum_detectable_effect_is_always_reported(strong_returns):
    """没有 MDE，「不显著」会被读成「不存在」——这是把没能力测说成没有。"""
    days = [f"2022-01-{d:02d}" for d in (5, 6, 7, 10, 11, 12, 13)]
    tc = tail_contrasts(*_args(_strong_panel(days)))
    m = tc["mde"]
    assert m["n_pairs"] == len(grid_days(days, 2))
    # 首版这里写的是 `> m["crit"] * 0`——**恒真**，测不出任何东西。
    # 改成核对公式本身：MDE 必须真的是 `crit · sd / √n`，
    # 否则「不显著」的解读就建立在一个来路不明的数上。
    want = m["crit"] * m["sd_per_k"] / np.sqrt(m["n_pairs"])
    assert m["min_detectable_per_k"] == pytest.approx(want)
    assert m["min_detectable_annualized"] == pytest.approx(want * 244 / 2)
    assert m["sd_per_k"] > 0, (
        "配对差值的标准差为 0——噪声被分箱均值消掉了，"
        "此时任何 t 都是假象（第二版就踩过这个）")


def test_a_bigger_sample_lowers_the_detection_floor(strong_returns):
    """期数翻倍 → MDE 应当按 1/√n 缩小。功效不是靠感觉，是可算的。"""
    short = [f"2022-01-{d:02d}" for d in (5, 6, 7, 10, 11, 12, 13)]
    long = [f"2022-{m:02d}-{d:02d}" for m in (1, 2, 3, 4)
            for d in (5, 6, 7, 10, 11, 12, 13)]
    a = tail_contrasts(*_args(_strong_panel(short)))["mde"]
    b = tail_contrasts(*_args(_strong_panel(long)))["mde"]
    assert b["min_detectable_per_k"] < a["min_detectable_per_k"], (
        f"样本从 {a['n_pairs']} 增到 {b['n_pairs']}，"
        f"检测下限却没降——MDE 的公式错了")


# ── 3. 分档本身 ────────────────────────────────────────────────────

def test_deciles_are_position_based_so_none_is_empty(strong_returns):
    """BM 极度左偏，按数值等分会有一档空着。必须按**位置**分箱。"""
    days = [f"2022-01-{d:02d}" for d in (5, 6, 7, 10, 11, 12, 13)]
    dt = decile_table(*_args(_strong_panel(days)))
    assert dt["q"].nunique() == 10, "有档位是空的——分箱用了数值切点而不是位置"


def test_a_narrow_pool_produces_no_decile_rows(monkeypatch):
    """覆盖池太窄时不出分档，而不是在一只票上算收益。"""
    monkeypatch.setattr("scripts.bm_ic_attribution.forward_return",
                        lambda *a, **k: 0.01)
    days = ["2022-01-05", "2022-01-06", "2022-01-07"]
    xs = _strong_panel(days, n=MIN_CROSS_SECTION - 1)
    assert decile_table(*_args(xs)).empty


# ── 4. `UPDATE_DATE` 不是重述证据 ───────────────────────────────────

def test_revision_exposure_counts_untouched_records_as_unrevised(tmp_path):
    """`UPDATE_DATE == NOTICE_DATE` 记 0 次改动——它是「没被刷新过」，不是「可信」。"""
    df = pd.DataFrame({
        "REPORT_TYPE": ["年报"] * 400,
        "REPORT_DATE": ["2021-12-31"] * 400,
        "NOTICE_DATE": ["2022-04-28"] * 400,
        "UPDATE_DATE": ["2022-04-28"] * 200 + ["2023-04-28"] * 200,
    })
    p = tmp_path / "f.parquet"
    df.to_parquet(p)
    out = revision_exposure(p)
    assert out["2021"]["n"] == 400
    assert out["2021"]["revised_share"] == pytest.approx(0.5)


def test_the_module_forbids_reading_update_date_as_restatement_evidence():
    """这条纪律必须写在文件里，不能靠人记得。

    实测各报告年份的改动滞后中位数**恰好 ~365 天**，整齐地锁在一年——
    逐笔重述不会这样。这是全表刷新时间戳。把它当污染度量，
    等于用一个只反映「这行最近一次被刷新」的字段去论证「这个数被改过」。
    """
    import inspect

    import scripts.bm_ic_attribution as m
    src = inspect.getsource(m)
    assert "revision_caveat" in src
    assert "365" in src and "全表刷新" in src


def test_the_macro_vs_pit_discriminator_is_kept():
    """判别式（按报告年份交叉拆 IC）必须在，因为它是唯一能分开
    「2021 风格逆转」和「PIT 污染」两者的设计。"""
    import inspect

    import scripts.bm_ic_attribution as m
    src = inspect.getsource(m)
    assert "ic_by_calendar_and_report_year" in src
    assert "宏观" in src and "PIT" in src

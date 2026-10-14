"""显著性门槛必须用 t 分布，不能拿正态近似充数（2026-10-14，事故复盘）。

## 事故

`ic_guided_race_7y.json` 的裁决写着：

    「五条腿全过：训练 +1.086、样本外 +0.992、超额 +0.12%、t=4.14 > 临界 3.47」

`3.47` 来自 `bonferroni_t(97)`，而它是 **`NormalDist().inv_cdf()`**——正态近似。
获批的冠军是 `{mom20:-1.0}` @ K=63，`excess_days = 11`。

**11 个观测的 t 统计量，配正态近似的临界值。** 真值：

    n=11  (df=10)  t 临界 = 5.03     差 +1.56
    n=21  (df=20)  t 临界 = 4.13     差 +0.66
    n=752 (df=751) t 临界 = 3.49     差 +0.02

`excess_days` 是 `excess_tstat(..., block=K)` 做**不重叠分块**之后的块数：
K=63 在 750 个交易日里只剩 11 块。**代码把 n=11 的统计量按 n=∞ 判。**

真实双侧 p 值：t=4.14 @ n=11 → p = 2.0e-3，而 Bonferroni 阈值是 0.05/97 = 5.2e-4。
**超了 3.9 倍。** 这次晋级是假阳性。

## 为什么这件事必须先修

用户的指令是「反转+**低频**策略尽快推进到实盘」。低频恰恰是 n 小的那一档——
`tests/test_forward_registry_lowfreq.py` 整份文件都在处理 K=63。
**错的正好是想推的那一档。**

## 为什么这不是「随 K 放松门槛」

原有两条测试断言门槛与 K 无关，理由是：

    「低频候选就能靠『多注册几组低频』把门槛压低」

方向反了。df 变小使 t 分布尾巴变厚，**真临界值单调变大**（5.03 > 3.49）。
K 变小只会让门槛更**严**。要放松证据标准，得放松的是 `alpha`，不是让 n 偷偷变小。
"""
import pytest

from aplus.evolve_race import bonferroni_t, oos_decision, race, split_window

CHAMP = {"mom20": 1.0}


def _mk(n):
    return [f"d{i:03d}" for i in range(n)]


def _k(w):
    return tuple(sorted(w.items()))


def _wire(spec):
    """绑定训练日期集合的 evaluate（与 tests/test_evolve_race.py::_mk_ev 同义）。"""
    holder = {}

    def ev(sections, w, dates, top_n, bottom_n):
        tr_obj, te_obj, te_exc = spec[_k(w)]
        is_train = bool(dates) and dates[0] in holder["train"]
        return {"objective": float(tr_obj if is_train else te_obj), "judged": 1000,
                "stats": {"up_excess_vs_market": float(0.01 if is_train else te_exc),
                          "avg_down_return": -0.001, "overall_hit_rate": 0.51,
                          "n_ranked": 4897}}

    ev.holder = holder
    return ev


def _race_with(cand_exc, t_test, excess_days):
    spec = {_k(CHAMP): (0.10, 0.10, 0.001), _k({"mom20": -1.0}): (0.50, 0.30, cand_exc)}
    ev = _wire(spec)
    xs = {"_dates": _mk(100)}
    tr, _ = split_window(xs["_dates"])
    ev.holder["train"] = set(tr)
    r = race(xs, [CHAMP, {"mom20": -1.0}], ev)
    for row in r["table"]:
        row["train"]["stats"]["excess_t"] = 0.2
        row["test"]["stats"]["excess_t"] = t_test
        row["test"]["stats"]["excess_days"] = excess_days
        row["test"]["stats"]["n_ranked"] = 4897
    return r


# ---------- 门槛本身 ----------

def test_critical_value_at_n11_is_five_not_three_and_a_half():
    """11 个观测的 Bonferroni 临界是 5.03，不是 3.47。"""
    assert bonferroni_t(97, df=10) == pytest.approx(5.03, abs=0.05)


def test_critical_value_converges_to_normal_as_df_grows():
    """df 大时正态近似才成立——这解释了为什么 1123 条测试里没有一条抓到它。"""
    assert bonferroni_t(97, df=751) == pytest.approx(3.49, abs=0.02)


def test_smaller_sample_strictly_raises_the_bar():
    """n 小 → 门槛更严。原有测试担心的「低频把门槛压低」方向是反的。"""
    lo, mid, hi = (bonferroni_t(97, df=n - 1) for n in (11, 150, 752))
    assert lo > mid > hi


# ---------- 裁决不得放行低样本量 ----------

def test_the_actually_promoted_champion_must_be_rejected():
    """事故原样复现：t=4.14 @ 11 块、97 次比较，曾被放行。"""
    d = oos_decision(_race_with(0.0012, 4.14, 11), CHAMP, significance_lookup=97)
    assert d["promote"] is False, (
        f"11 个观测的 t=4.14（p=2.0e-3）不该过 5.2e-4 的门槛，"
        f"却实得 {d}")


def test_same_t_on_a_large_sample_still_passes():
    """反方向：752 个观测上的 t=4.14 确实显著，门槛收紧不许变成「一律否决」。"""
    d = oos_decision(_race_with(0.0012, 4.14, 752), CHAMP, significance_lookup=97)
    assert d["promote"] is True, f"大样本下 t=4.14 应放行，实得 {d}"


def test_t_far_above_the_bar_still_passes_on_a_small_sample():
    """n=11 但 t=6.5（p=3.5e-4）真显著 —— 证明不是「低频一律否」。"""
    d = oos_decision(_race_with(0.0012, 6.5, 11), CHAMP, significance_lookup=97)
    assert d["promote"] is True, f"n=11 t=6.5 应放行，实得 {d}"


# ---------- 前瞻注册：门槛随攒到的周期数变化 ----------

def test_forward_threshold_rises_as_the_window_stays_short():
    """前瞻注册里，窗口攒的周期越少，门槛越高——不是恒为 3.47。

    `forward_status` 按每个候选自己的 `test_periods` 给门槛，
    并对还没攒够的标 `t_crit_provisional`。少了 `provisional` 标记，
    一个暂定门槛会被当成最终口径引用。
    """
    import datetime as _dt
    from aplus.forward import forward_status, register

    def _cal(days):
        d = _dt.date(2026, 10, 14)
        out, seen = [], 0
        while seen < days:
            if d.weekday() < 5:
                out.append(d.isoformat())
                seen += 1
            d += _dt.timedelta(days=1)
        return out

    tmp = "/tmp/_bfwd_test"
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
    tmp_path = __import__("pathlib").Path(tmp)
    tmp_path.mkdir(parents=True)
    register(tmp_path, "2026-10-14", [{"weights": {"mom20": -0.5}, "rebalance_every": 1}],
             note="频率轴")
    short = forward_status(tmp_path, as_of="2026-11-06", dates=_cal(5))["candidates"][0]
    long = forward_status(tmp_path, as_of="2026-12-31", dates=_cal(40))["candidates"][0]

    assert short["test_periods"] < long["test_periods"]
    assert short["t_crit"] > long["t_crit"], (
        f"周期少({short['test_periods']})时门槛必须更严："
        f"{short['t_crit']:.2f} vs {long['t_crit']:.2f}")
    assert short["measurable"] is False and short["t_crit_provisional"] is True, \
        "窗口没攒够时必须标出这个门槛是暂定值"
    assert long["measurable"] is True and long["t_crit_provisional"] is False
    shutil.rmtree(tmp, ignore_errors=True)
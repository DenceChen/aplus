"""`scripts/basket_weighting.py` 的守卫。

## 三条纪律在这份文件里的落点

1. **守卫不得触发面板加载** —— 每个测试自带**小合成面板**（几十行），
   不读 `data/master`。跑守卫时 OOM 过一次的门禁不能再挂一次。
2. **断言必须锚在行为上**，不锚在源码子串上 —— 否则守卫会被它自己的注释满足
   （第 13 种形状：作者与断言者是同一个人、在同一次编辑里）。
3. **判决函数的四个分支必须各自可达** —— 「不可达的冻结分支」是第 88 层，
   靠推理发现不了，只能构造已知输入打进去。
"""
from __future__ import annotations

import ast
import importlib.util
import inspect
import json
import math
import sys
import textwrap
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "basket_weighting.py"

_spec = importlib.util.spec_from_file_location("basket_weighting_under_test", SCRIPT)
bw = importlib.util.module_from_spec(_spec)
sys.path.insert(0, str(ROOT / "scripts"))
_spec.loader.exec_module(bw)


# ───────────────────────── 合成面板 ─────────────────────────
def _panel(n_days=90, seed=0, scale=None):
    """每只票一条日线。`scale` 给每只票一个波动水平（用来造可区分的 σ）。"""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2021-01-04", periods=n_days)
    dm = {}
    for i in range(6):
        s = scale[i] if scale else 0.01 * (i + 1)
        dm[f"T{i}"] = pd.DataFrame({
            "date": dates,
            "close": 10.0 * np.exp(np.cumsum(rng.normal(0.0, s, n_days))),
        })
    return dm, dates


def _daily(code, n_days=120, seed=1, scale=0.01):
    """一只票的日线。`seed` 由调用方显式给 —— 不用 `hash(str)`，
    因为字符串哈希每进程加盐，同一个守卫两次跑会拿到不同的 σ。"""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2021-01-04", periods=n_days)
    return pd.DataFrame({"date": dates,
                         "close": 10.0 * np.exp(np.cumsum(rng.normal(0, scale, n_days)))})


def _leg(dates, codes, ret, mkt=0.0, cost=0.0):
    """造一张涨腿表，结构与 `evaluate_weights` 的输出对齐。"""
    rows = []
    for d in dates:
        for c in codes:
            rows.append({"pred_date": d, "code": c, "direction": "up",
                         "date": d, "actual_pct": ret,
                         "market_pct": mkt, "cost": cost})
    return pd.DataFrame(rows)


# ─────────────────── ① 防泄露：本实验唯一的泄露点 ───────────────────
def test_polluting_the_future_cannot_change_the_trailing_sigma():
    """★ 防泄露本身：把 D 之后的数据改到天上，σ 必须逐位不变。

    ⚠️ **这条不测顺序，而且我第一版的 docstring 说它测顺序 —— 那是错的。**
      顺序颠倒**不会**泄露：`tmp[tmp.date <= upto]` 这一行在任何顺序下
      都把 D 之后的行滤掉了。所以「投毒」这个探针对顺序**天生瞎** ——
      被污染的行照样被滤掉，两边窗口一致 ⇒ σ 相同 ⇒ 这条照样绿。
      ⇒ 顺序由下面那条**截断**探针负责。

    这条本身仍然有价值且必须留着：它是**防泄露的正向锚**
    （`entry_lag=1`，D+1 买 / D+2 卖，泄露的量级小、方向随机，报告上看不出来）。
    """
    dm, dates = _panel()
    D = dates[60]
    before = bw.trailing_sigma(dm, "T0", D)

    poisoned = {k: v.copy() for k, v in dm.items()}
    future = poisoned["T0"]["date"] > D
    poisoned["T0"].loc[future, "close"] = 1e9

    assert bw.trailing_sigma(poisoned, "T0", D) == before


def test_truncating_the_future_changes_nothing_which_is_what_a_wrong_window_order_would_break():
    """★ 顺序的真正守卫，**补上去的**（变异验证时发现上面那条打不中）。

    性质：**有未来的表算出的 σ，必须等于把未来删掉之后算出的 σ。**
    σ 只看 `<= upto`，所以未来**存在**与未来**不存在**是同一件事。

    为什么「投毒」测不出而「截断」测得出：

    | 顺序 | 投毒探针 | 截断探针 |
    |---|---|---|
    | 正确（先滤后 tail） | 窗口 = 最后 61 根 ≤ D | 窗口 = 最后 61 根 ≤ D ⇒ **相等** |
    | 颠倒（先 tail 后滤） | tail 落在末尾 → 滤完更短；投毒行也被滤掉 ⇒ **两边同长 ⇒ 假相等** | tail 落在末尾 → 滤完**落在不同窗口** ⇒ **不等** |

    ★ 也就是说：投毒让「错误的窗口」在两边**同步地错**，于是差值为 0；
      截断让错误的窗口**无处躲**。这是第 14 种形状的一个新变体 ——
      **探针本身带着一个把缺陷掩盖掉的对称性**，输出与「守卫失效」逐字相同。

    正控：本构造下两条路径**本来就不相等**，所以这条不是恒真。
    """
    dm, dates = _panel()
    D = dates[60]
    full = bw.trailing_sigma(dm, "T0", D)

    truncated = {k: v.copy() for k, v in dm.items()}
    keep = truncated["T0"]["date"] <= D
    truncated["T0"] = truncated["T0"].loc[keep].reset_index(drop=True)
    got = bw.trailing_sigma(truncated, "T0", D)

    assert got == full, f"★ σ 不该关心未来存不存在：{full} vs {got}"

    # ★ 正控：打乱顺序后，两条路径**确实**不等 —— 否则这条守卫是恒真的
    tail_first = dm["T0"].tail(bw.WINDOW + 1)
    tail_first = tail_first[tail_first["date"] <= pd.Timestamp(D)]
    assert len(tail_first) < bw.WINDOW + 1, \
        f"★ 正控前提：颠倒顺序后窗口必须变短，实得 {len(tail_first)} 行"
    assert len(tail_first) != len(dm["T0"].loc[keep]), \
        "★ 正控前提：颠倒顺序后的窗口长度必须与正确顺序不同，否则守卫测不出东西"


def test_a_shortened_history_changes_the_sigma_proving_the_read_is_real():
    """★ 正控臂：不加这一条，上面那条可能因为「压根没读数据」而恒绿
    （CLAUDE.md 第 87 层：只有零假设臂的野自助守卫）。"""
    dm, dates = _panel()
    D = dates[60]
    long_full = bw.trailing_sigma(dm, "T0", D)
    short = bw.trailing_sigma(dm, "T0", dates[20])
    assert np.isfinite(long_full) and np.isfinite(short)
    assert long_full != short, "σ 与窗口无关 ⇒ 函数没在读窗口"


def test_the_two_arms_measure_different_quantities():
    """A1 用**原始** sd、A2 用**对市场超额**的 sd —— 若两者相同，
    A2 就是 A1 的同义词，实验少了一条臂。

    ⚠️ 第一版这里写了一个**不存在的性质**：市场序列取常数 `0.004`，
      然后断言「减掉常数漂移 ⇒ σ 变小」。**减常数不改变 sd** ——
      那个断言是错的，而它当时没红只是因为另一条更早的 KeyError 先炸了。
      ⇒ 恒等式的正确外部锚是 `std(x − c) ≡ std(x)`（用 `np.allclose` 钉），
        而 A1/A2 不同**必须**来自市场序列本身在动。
    """
    dm, dates = _panel()
    D = dates[60]
    sub = dm["T0"].sort_values("date")
    win = sub[sub["date"] <= D].tail(61)
    raw_ret = win["close"].pct_change().to_numpy()[1:]

    flat = bw.trailing_sigma(dm, "T0", D, market={str(d.date()): 0.004 for d in dates})
    assert np.allclose(flat, float(np.std(raw_ret, ddof=1))), \
        "★ 外部锚：减去常数市场后 sd 必须与原始 sd 相同"

    rng = np.random.default_rng(7)
    mkt = {str(d.date()): v for d, v in zip(dates, rng.normal(0, 0.02, len(dates)))}
    raw = bw.trailing_sigma(dm, "T0", D)
    exc = bw.trailing_sigma(dm, "T0", D, market=mkt)
    assert raw != exc, "★ 市场序列在动时，A1 与 A2 必须给出不同的量"

    # 再钉一次：两个数分别等于各自定义的 sd（不是「不相等」这么弱）
    mk = np.array([mkt[str(d.date())] for d in win["date"]])[1:]
    assert np.allclose(exc, float(np.std(raw_ret - mk, ddof=1)))


def test_a_stock_with_too_short_a_history_is_unusable_not_zero():
    """σ 不可用必须返回 NaN（被剔除），**不能返回 0** ——
    返回 0 会让 1/σ 变成 ∞、权重爆掉，而报告上只看得见「sd 变大了」。"""
    dm, _ = _panel()
    assert np.isnan(bw.trailing_sigma(dm, "T0", "2021-01-05"))
    assert np.isnan(bw.trailing_sigma(dm, "不存在", "2021-06-01"))


# ─────────────────── ② 加权聚合与成本口径 ───────────────────
def test_weighted_aggregation_with_equal_weights_reproduces_the_plain_mean():
    """★ A0 臂必须与 `evolve_race._daily_series` 的等权口径相同到 ULP 级。
    不同 ⇒ 臂间的差里混进了「聚合方式」这个额外变量，
    而那正是本实验不许变的东西。

    ⚠️ 不能写 `atol=0`。实测差异**恰好是 1 个 ULP**（1.7347e-18）：
    本实现用 Python `sum()` **顺序**累加 `Σw·net`，而 pandas `.mean()`
    走的是成对求和 —— 两条路径的加法次序不同，最后一位就会差。
    打印出来看着一样，逐位比不一样。
    ⇒ 容差按 **ULP** 表达（`np.spacing`），不拍字面量；
      容差本身由下一条单独钉住。
    """
    dates = pd.bdate_range("2021-03-01", periods=8).strftime("%Y-%m-%d")
    leg = _leg(dates, ["A", "B", "C"], ret=0.01, mkt=0.002, cost=0.0005)
    leg.loc[leg["code"] == "B", "actual_pct"] = 0.02

    got, _ = bw._weighted_daily(leg, "equal", {}, {}, 60, 1, 1)
    expect = (leg["actual_pct"] - leg["cost"]).groupby(leg["pred_date"]).mean() \
        - leg["market_pct"].groupby(leg["pred_date"]).mean()
    assert list(got.index) == list(expect.index)
    tol = 8 * np.spacing(np.abs(expect.to_numpy()).max())
    assert np.allclose(got.to_numpy(), expect.to_numpy(), rtol=0, atol=tol), \
        f"max|Δ|={np.abs(got.to_numpy() - expect.to_numpy()).max()} > 容差 {tol}"


def test_the_ulp_tolerance_is_itself_pinned():
    """★ **容差必须被单独测一条**（CLAUDE.md 纪律 ④）。

    否则把容差从 8 ULP 悄悄放宽到 1e-12 时，**没有一条守卫会变红**，
    而「偏差 1e-13」这种量级的缺陷照样能过。

    这里构造一个**恰好比容差大一倍**的缺陷，断言它必须被抓住，
    并断言该缺陷**确实 ≠ 0**（否则连假测试都拦不住）。
    ★ 同时明写容差的**下边界**：半个 ULP 以内的缺陷是抓不到的，
      不假装这条守卫比它实际能做的更强。
    """
    base = 0.008
    tol = 8 * np.spacing(base)
    defect = base + 2 * tol
    assert defect != base, "★ 构造出来的缺陷必须真的 ≠ 0"
    assert not np.allclose([base], [defect], rtol=0, atol=tol), \
        "★ 容差若宽到 2 倍，真实缺陷就能混过去"
    assert np.allclose([base], [base + tol / 2], rtol=0, atol=tol)


def test_the_baseline_arm_is_never_empty():
    """★ 这条是被一次真 bug 逼出来的：`if mode != "equal" and (...)` 让
    **等权基线臂永远不初始化 `held`**，后面 `if not held: continue`
    把每一天都丢掉 ⇒ A0 产出**空序列** ⇒ `decide` 拿到 NaN 的 `sd_A0`
    ⇒ **拒绝出判决**（而空判决与「跑挂了」在报告上长得一模一样）。

    ⇒ 基线臂非空，是本实验**能出判决**的前提，不是卫生问题。
    """
    dates = pd.bdate_range("2021-03-01", periods=9).strftime("%Y-%m-%d")
    leg = _leg(dates, ["A", "B", "C"], ret=0.01, mkt=0.002, cost=0.0005)
    dm = {c: _daily(c, seed=10 + i) for i, c in enumerate(("A", "B", "C"))}
    # ★ `inv_excess` 必须有真市场序列：给它 `{}` 时超额收益全是 NaN ⇒ σ 不可用
    #   ⇒ 3/3 剔除 ⇒ 超 20% 上限 ⇒ 该臂作废。**这个行为是对的** ——
    #   没有市场序列时「超额 σ」无定义，退回总 σ 就是偷换口径。
    mkt = {str(d.date()): 0.001 for d in pd.bdate_range("2021-01-04", periods=120)}
    for mode in ("equal", "inv_total", "inv_excess"):
        s, meta = bw._weighted_daily(leg, mode, dm, mkt, 60, 1, 1)
        assert len(s) == len(dates), f"★ {mode} 臂丢了日子：{len(s)}/{len(dates)}"
        assert np.isfinite(s.to_numpy()).all(), f"★ {mode} 臂含非有限值"


def test_weighting_does_not_discount_the_cost_a_second_time():
    """★ `cost` 是**组合级**常数（`cost_drag` 算一次、写给涨腿每一行）。

    加权后 `Σw·(r−c) = Σw·r − c·Σw = Σw·r − c`（因 `Σw=1`）
    ⇒ 成本仍恰好扣一次。若这条破了，成本会随权重方案一起变，
    而 sd 的差异里就混进了成本差异 —— 那正是第 15 种形状（P0 成本被扣两次）。
    """
    dates = pd.bdate_range("2021-03-01", periods=8).strftime("%Y-%m-%d")
    leg = _leg(dates, ["A", "B", "C"], ret=0.01, mkt=0.002, cost=0.001)
    # ⚠️ 面板的代码名必须与涨腿**对齐**：第一版用 `_panel()`（T0..T5）
    #   配 A/B/C，于是 3/3 全被剔除、序列为空，守卫测的是一个空对象。
    dm = {c: _daily(c) for c in ("A", "B", "C")}

    s_w, _ = bw._weighted_daily(leg, "inv_total", dm, {}, 60, 1, 1)
    s_e, _ = bw._weighted_daily(leg, "equal", dm, {}, 60, 1, 1)
    assert len(s_w) == len(dates) and len(s_e) == len(dates), "★ 两臂都不得为空"
    # 所有持仓收益相同 ⇒ 加权与否都一样。
    # 序列本身已经是 `净超额 = (actual − cost) − market`，
    # ⇒ 期望值 0.01 − 0.001 − 0.002 = 0.007，**不能再减一次市场**。
    # （第一版写成 `(s − 0.002) == 0.009`，把市场减了两次 —— 守卫自己犯了
    #   它要防的那个错：口径错配的守卫若与实现同错，就永远绿。）
    want = 0.01 - 0.001 - 0.002
    assert np.allclose(s_w.to_numpy(), want, rtol=0, atol=1e-12), s_w.to_numpy()
    assert np.allclose(s_e.to_numpy(), want, rtol=0, atol=1e-12), s_e.to_numpy()


def test_inverse_vol_actually_tilts_toward_the_low_volatility_name():
    """★ 权重方案本身必须真的改变权重，否则「sd 没降下来」这个零结果
    会被读成「方案没生效」。这是一个**正控臂**。"""
    dm, dates = _panel(scale=[0.005, 0.05, 0.002, 0.05, 0.05, 0.05])
    D = str(dates[60].date())
    rows = pd.DataFrame({"code": ["T0", "T1", "T2", "T3", "T4", "T5"],
                         "date": D})
    w, dropped = bw.arm_weights(rows, "inv_total", dm, {}, D)
    assert dropped == 0
    assert set(w) == set(rows["code"])
    assert abs(sum(w.values()) - 1.0) < 1e-12, "权重和必须为 1"
    # 最稳的两只（T2=0.002, T0=0.005）拿到的权重必须高于最不稳的（T1/T3/T4/T5=0.05）
    assert w["T2"] > w["T1"] and w["T0"] > w["T1"]


def test_non_rebalance_days_reuse_the_previous_weights():
    """★ 这是最贵的一条：**非调仓日重新加权 = 无成本换仓**，
    那是一种不存在的策略，会凭空造出收益 —— 且报告上完全看不出来。

    构造：调仓日权重明显偏向 A，若非调仓日重算权重，
    而 A 在调仓日之后波动变高，`inv_total` 会在**没有换手**的情况下
    把仓位挪走 —— 于是净值曲线的差异不是「交易」造成的，是算出来的。
    ⇒ 守卫直接查：同一段数据里，非调仓日的那几行必须用**同一套**权重。
    """
    n = 12
    dates = pd.bdate_range("2021-03-01", periods=n).strftime("%Y-%m-%d")
    base = pd.bdate_range("2021-01-04", periods=120)
    dm = {}
    # A 先极稳后极不稳；B 先极不稳后极稳
    for i, name in enumerate(["A", "B", "C"]):
        rng = np.random.default_rng(10 + i)
        dm[name] = pd.DataFrame({"date": base, "close": 10 + i,
                                 "_x": rng.normal(0, 0.01, len(base))})
        dm[name] = pd.DataFrame({"date": base, "close": 10.0 * np.exp(np.cumsum(dm[name]["_x"]))})
    rows = []
    for j, d in enumerate(dates):
        # 让 A 的 σ 在中途跳变：B 相反
        for c in ["A", "B", "C"]:
            rows.append({"pred_date": d, "code": c, "direction": "up", "date": d,
                         "actual_pct": 0.01 if c == "A" else 0.0,
                         "market_pct": 0.0, "cost": 0.0})
    leg = pd.DataFrame(rows)

    k = 4
    s_inv, _ = bw._weighted_daily(leg, "inv_total", dm, {}, 60, k, 1)

    # k=4、12 天 ⇒ 三个调仓块（0-3 / 4-7 / 8-11）。
    # ⚠️ 第一版断言 `nunique() == 1`，那是**错的规格**：块与块之间本就该不同
    #   （每次调仓换权重），所以正确的判据是「**块内恒定**」，
    #   而实测 nunique()==3 —— 每块一个值，正是期望的形状。
    #   若权重在非调仓日被重算，块内就会漂，nunique 远大于 3。
    assert len(s_inv) == 12
    vals = s_inv.to_numpy()
    blocks = [vals[i:i + k] for i in range(0, 12, k)]
    assert len(blocks) == 3
    for j, b in enumerate(blocks):
        assert np.allclose(b, b[0], rtol=0, atol=0), \
            f"★ 第 {j} 块内部漂了：{b} —— 非调仓日重算了权重"
    assert len({round(float(b[0]), 18) for b in blocks}) == 3, \
        "★ 正控：三块的取值必须各不相同，否则这个构造区分不出「重算」与「沿用」"


def test_dropping_more_than_the_preregistered_share_voids_the_day():
    """★ 预注册 §4：σ 不可用的票超过 20% ⇒ 该臂作废。

    理由写在预注册里：剔除一半说明**名单本身**有问题，
    不是权重方案的问题 —— 而「用剩下的算」会把这个信号吃掉。
    """
    dates = pd.bdate_range("2021-03-01", periods=6).strftime("%Y-%m-%d")
    dm, _ = _panel(n_days=90)
    codes = [f"T{i}" for i in range(6)]
    rows = []
    for d in dates:
        for c in codes:
            rows.append({"pred_date": d, "code": c, "direction": "up", "date": d,
                         "actual_pct": 0.01, "market_pct": 0.0, "cost": 0.0})
    leg = pd.DataFrame(rows)
    # 只让 2/6 只有效 ⇒ 剔除 4/6 = 67% > 20%
    thin = {c: dm[c] for c in ["T0", "T1"]}
    s, meta = bw._weighted_daily(leg, "inv_total", thin, {}, 60, 1, 1)
    assert len(s) == 0, "剔除超限时该臂必须不产出，而不是用剩下的算"
    assert meta["dropped"] > 0


# ─────────────────── ③ 判决函数：四个分支各自可达 ───────────────────
#
# ★ 夹具承载**真实测量值**（`factor_width_mde.json::widths["200"]`，
#   2026-10-15），不是玩具数 —— 预注册 §6-修订1.4 把 `SD_WORKS` 的门槛
#   从字面量 `0.20` 改成**由外部锚推导**，而推导的输入就是这三个数。
#   ⇒ 用玩具夹具（`sd=10, mean=6`，推出 ~48.8% 的门槛）测出来的
#   「门过 / 门不过」**不约束真实验收的那条线**。
#
#   三个量缺一不可：`sd`/`mean` 决定降幅与代价，`n_days` 决定**门槛本身**
#   （门槛 ∝ 1/√年数）。少了 `n_days` ⇒ 门槛算不出来 ⇒ 拒绝出判决。
#   守卫 `..._refuses_to_verdict_without_the_sample_length` 钉的就是这一条。
_BASE = {"sd": 0.08854148167643767,      # annual_sd_pct @ N=200
         "mean": 0.0694654436628111,      # gross_annual_pct @ N=200
         "n_days": 1395}                  # 判决窗口 2021-01-01 → 2026-10-12

# 达标所需的 sd（`sd_needed`）。门槛的另一种说法是「降幅 = 1 − sd_needed/sd」，
# 所以这个数既是推导的中间量，也是 `decide` 判据的物理含义。
_SD_NEEDED = _BASE["mean"] * math.sqrt(_BASE["n_days"] / bw.DAYS_PER_YEAR) / bw.T_POWER_TARGET


def _arms(**kw):
    a = {k: dict(_BASE) for k in bw.ARMS}
    for k, v in kw.items():
        arm, field = k.split("__")
        a[arm][field] = v
    return a


def _flat(drop):
    """逐年降幅恒为 `drop`，六年全正 —— 用来看「逐年一致性之外的分支」。"""
    return {"A1_inv_total": {y: drop for y in range(2021, 2027)}}


def test_the_baseline_constants_still_match_the_artifact():
    """★ 夹具承载真实测量值 ⇒ 它**必须**跟着产物走。

    否则产物重跑后基线变了，这条文件里的「推导门槛 = 33.0%」会静默过期，
    而守卫照样全绿 —— 这正是第 18 种形状（机制绿、数据侧已腐）。
    """
    art = json.loads((ROOT / "data/reports/factor_width_mde.json").read_text())
    w = art["widths"][str(bw.MAIN_N)]
    assert w["annual_sd_pct"] == _BASE["sd"], (
        "榜宽实验重跑过，annual_sd_pct 变了 ⇒ 更新 _BASE 并重算 _SD_NEEDED，"
        "不要只改测试的断言让它绿")
    assert w["gross_annual_pct"] == _BASE["mean"], "同上：毛超额变了"
    assert w["n_days"] == _BASE["n_days"], "同上：样本长度变了（门槛 ∝ 1/√年数）"


def test_sd_neutral_when_the_best_arm_barely_moves_the_sd():
    """★ 零结果是本实验**最有决策价值**的输出（预注册 §1.4），所以
    它必须可达、且被明确命名 —— 而不是被当成「没跑出东西」。"""
    v, why = bw.decide(_arms(A1_inv_total__sd=_BASE["sd"] * 0.96), _flat(0.04))
    assert v == "SD_NEUTRAL"
    # 🚨 文案里**不许**再把 ρ≈1 当解释 —— 榜宽实验已用两套独立论证排除它。
    #   这条断言按「形状」写（出现「不等于」/「排除」），不按字面量写，
    #   免得下一次改文案时要么红要么被注释满足（第 13 种形状）。
    assert "ρ≈1" in why and ("不等于" in why or "排除" in why), (
        "ρ≈1 已被实测排除，不许再作为 NEUTRAL 的理由")


def test_sd_works_requires_both_the_drop_and_the_yearly_consistency():
    """降幅**过推导门槛**且逐年一致，才给 SD_WORKS。

    ⚠️ 这里的 `sd` 是从真实基线推出来的，不是随手写的：
    要过门槛就得 `sd ≤ _SD_NEEDED`（5.93%），否则判决是 TRADEOFF。
    """
    v, why = bw.decide(_arms(A1_inv_total__sd=0.0500), _flat(0.44))
    assert v == "SD_WORKS", why
    assert "仪器" in why, "★ sd 降只说明仪器变好，不说明 alpha 变大 —— 措辞必须带出去"
    # 门槛必须**印出来**，且印的是推导出来的那个值（不是旧字面量 0.20）
    thr = bw.required_sd_drop(_BASE["mean"], _BASE["sd"], _BASE["n_days"])
    assert f"{thr:.1%}" in why, f"理由里必须带门槛值 {thr:.1%}"


def test_a_big_average_drop_with_unstable_years_is_mixed_not_works():
    """★ 逐年一致性是**硬要求**。66 个独立观测上的均值差不可信，
    逐年一致性是唯一不额外要数据就能拿到的稳健性证据。

    这是「先判逐年、再判降幅」的守卫：降幅 43.5% 已**过门槛**，
    若逐年分支被写成后判，这个案例会被报成 SD_WORKS ——
    而那正是本实验最该防的错误结论。"""
    v, why = bw.decide(
        _arms(A1_inv_total__sd=0.0500),
        {"A1_inv_total": {2021: 0.5, 2022: 0.5, 2023: -0.4,
                          2024: 0.5, 2025: -0.3, 2026: -0.45}},
    )
    assert v == "SD_MIXED", why


def test_a_drop_that_costs_more_than_half_of_itself_is_a_tradeoff():
    """降幅 15.3%（>10% 所以不是 NEUTRAL），但均值代价 0.672 sd 单位
    ≫ 降幅的一半 0.076 ⇒ 因代价被否。"""
    v, why = bw.decide(
        _arms(A1_inv_total__sd=0.0750, A1_inv_total__mean=0.0100), _flat(0.15))
    assert v == "SD_TRADEOFF"
    assert "不采用" in why


def test_a_drop_above_the_floor_but_below_the_derived_threshold_is_a_tradeoff():
    """★ **这是本轮新写的守卫**，它守的是「推导门槛真的在判决里」。

    降幅 29.98% > 0.10（所以不是 NEUTRAL）、均值代价为 0（所以不是被代价否），
    唯一能判它的是 `bd < threshold` 那条分支。
    ⇒ 若有人把门槛换回字面量 `0.20`，本例会翻成 `SD_WORKS` ⇒ 变红。

    ⚠️ 旧守卫抓不到这个：它们全都只测「过 / 不过」，
    而在 `0.20` 下这三个用例**结论一模一样**（见本节的 0.20→0.33 说明）。
    """
    v, why = bw.decide(_arms(A1_inv_total__sd=0.0620), _flat(0.30))
    assert v == "SD_TRADEOFF", why
    assert "未达" in why and "推导" in why, (
        "未过门槛的理由必须写明门槛是推导的，并印出门槛值")
    # 降幅确实过了旧字面量 0.20 —— 所以这条用例对 0.20 与 0.33 是可区分的
    assert 0.20 < (0.08854148167643767 - 0.0620) / 0.08854148167643767


def test_the_mean_cost_is_measured_in_sd_units_not_mean_units():
    """★ 预注册 §5 的核心口径：均值代价除以 **`sd_A0`** 而不是 `mean_A0`。

    两个均值都是 6%~9%，除以均值会得到一个很大的相对数、看起来像
    「牺牲了很多」；除以 sd 才是「以风险为单位衡量收益损失」——
    这正是风险平价的原始命题。

    ⇒ 守卫用一个**判决真的会翻转**的构造，而不是只核一次算术：
      降幅 43.5%、均值从 6.9465% 掉到 5.20%，
        除以 `sd_A0`（8.854%）得 **0.197 sd 单位** ≤ 降幅一半（0.218）⇒ 过；
        除以 `mean_A0`（6.9465%）得 **0.251** > 0.218 ⇒ 不过。
      ⇒ 口径写错 ⇒ 判决从 `SD_WORKS` 翻成 `SD_TRADEOFF`。

    🚨 旧版这条守卫**测不出这个**：它只断言 `cost_sd_units ≈ 0.20`，
    而 verdict 在两种口径下**都是** TRADEOFF ——
    断言的是算术，判决没被区分开（第 5 种形状：断言 == 公式，公式错了跟着一错）。
    """
    arms = _arms(A1_inv_total__sd=0.0500, A1_inv_total__mean=0.0520)
    drop = (_BASE["sd"] - 0.0500) / _BASE["sd"]
    cost_sd = (_BASE["mean"] - 0.0520) / _BASE["sd"]
    cost_mean = (_BASE["mean"] - 0.0520) / _BASE["mean"]
    # 正控：构造出来的两个代价确实**分居降幅一半的两侧**，否则本守卫恒真
    assert cost_sd <= drop / 2 < cost_mean, (
        "判别构造失效：两个口径必须分居两侧，否则这条守卫测不到口径")
    v, why = bw.decide(arms, _flat(0.44))
    assert v == "SD_WORKS", why
    assert "sd 单位" in why


def test_no_verdict_is_issued_when_the_baseline_is_unusable():
    """★ 「拒绝出判决」必须真的什么都不给。
    空判决与「跑挂了」在报告上长得一模一样 —— 第 89 层。"""
    v, why = bw.decide({"A0_equal": {"sd": float("nan"), "mean": 0.0}}, {})
    assert v is None
    assert "拒绝出判决" in why


def test_it_refuses_to_verdict_without_the_sample_length():
    """★ 门槛 ∝ 1/√年数 ⇒ **没有 `n_days` 就算不出门槛**。

    ⇒ 拒绝出判决，而不是回落到某个默认长度。
    「缺档必须报错，绝不回落」—— 回落到默认值会让判决看起来正常，
    而它用的样本长度是假的。
    """
    v, why = bw.decide({"A0_equal": {"sd": _BASE["sd"], "mean": _BASE["mean"]}}, {})
    assert v is None
    assert "n_days" in why and "拒绝出判决" in why


def test_the_threshold_is_inf_when_the_gross_excess_is_not_positive():
    """★ 毛超额 ≤ 0 ⇒ 门槛**不可达**，必须返回 `inf` 而不是负门槛。

    负门槛会让「任何正降幅」都判 `SD_WORKS` ——
    而那是本实验最该防的错误结论（t 的分子是 0 或负，把 sd 降到 0 也测不出）。
    """
    assert bw.required_sd_drop(0.0, _BASE["sd"], _BASE["n_days"]) == math.inf
    assert bw.required_sd_drop(-0.05, _BASE["sd"], _BASE["n_days"]) == math.inf
    assert bw.required_sd_drop(_BASE["mean"], 0.0, _BASE["n_days"]) == math.inf
    # 正控：真实基线必须给出**有限**门槛，否则上面三条是恒真的
    assert 0.0 < bw.required_sd_drop(_BASE["mean"], _BASE["sd"], _BASE["n_days"]) < 1.0


def test_the_threshold_is_derived_from_the_external_anchor():
    """★ `SD_WORKS` 的门槛**由外部锚推导**，不是字面量。

    推导链（每一环都不在本模块内定义）：
        MDE = t_power × SE_年     ← `factor_attribution.power_floor`
        可测 ⟺ 毛超额 > MDE ⟺ 夏普 > t_power/√年数

    ⇒ 等价说法：**降幅达门槛 ⟺ sd 降到 `_SD_NEEDED`**。
      这里从定义那一侧独立复算一次，与 `required_sd_drop` 对拍 ——
      推导路径不同（第 5 种形状的正解：给断言换一条真值源）。
    """
    t_power = bw.T_POWER_TARGET
    n_years = _BASE["n_days"] / bw.DAYS_PER_YEAR
    sd_needed = _BASE["mean"] * math.sqrt(n_years) / t_power
    got = bw.required_sd_drop(_BASE["mean"], _BASE["sd"], _BASE["n_days"])
    assert abs(got - (1.0 - sd_needed / _BASE["sd"])) < 1e-15
    # 关键换算：`夏普 = 毛超额/sd` ⟺ 可测 ⟺ `sd ≤ sd_needed`
    # ⇒ 毛超额/sd_needed 必须恰好等于「可测所需的夏普门槛」t_power/√年数
    assert abs(_BASE["mean"] / sd_needed - t_power / math.sqrt(n_years)) < 1e-12
    # 预注册文档里手算的 33.0% —— 钉到 0.1pp，防止推导悄悄改口径
    assert abs(got - 0.330) < 1e-3, f"推导门槛 {got:.4%} 与预注册的 33.0% 不符"
    # 🚨 旧字面量 0.20 在真实基线**买不到可测性**：它只把夏普抬到 0.981，
    #   而可测门槛是 t_power/√年数。正控：两条数都要在守卫里被算出来，
    #   否则这条断言会变成「我又写了一句正确的话」而没人能验它对不对。
    sharpe_at_020 = (_BASE["mean"] / _BASE["sd"]) / (1 - 0.20)
    assert sharpe_at_020 < t_power / math.sqrt(n_years)


def test_the_threshold_moves_with_the_sample_length_and_was_not_typed():
    """★ 两件事合成一条守卫：门槛**依赖样本长度**，且**不含字面量**。

    ① 行为层：样本翻倍 ⇒ 门槛下降（√年数）。
       ⇒ 若有人把门槛写死，这一条立刻红。
    ② 结构层：`decide` 的函数体里**不许出现**任何裸浮点比较常量。
       用 AST 取 `Compare` 的比较子（第 13 种形状的解法：锚在语法位置上，
       不锚在「这个串出现过」上 —— 否则能被注释满足）。
    """
    thr_now = bw.required_sd_drop(_BASE["mean"], _BASE["sd"], _BASE["n_days"])
    thr_2x = bw.required_sd_drop(_BASE["mean"], _BASE["sd"], 2 * _BASE["n_days"])
    assert thr_2x < thr_now, "样本翻倍门槛必须降（∝ 1/√年数）"
    # ⚠️ 随样本长度按 √ 缩放的是**达标所需的 sd**，
    #   而阈值形如 `1 − sd_needed/sd` ⇒ 按同一比例缩的是 `1 − 阈值`。
    #   （第一版把关系写成 `阈值 ∝ 1/√n` —— 守卫当场红 0.255 ≠ 0。
    #   这是「先写下我以为的性质再让代码确认它」的又一次，
    #   好在它红的是**我写错的期望**，而不是守卫本身。）
    sd_1x = _SD_NEEDED
    sd_2x = (_BASE["mean"] * math.sqrt(2 * _BASE["n_days"] / bw.DAYS_PER_YEAR)
             / bw.T_POWER_TARGET)
    assert abs(sd_2x / sd_1x - math.sqrt(2)) < 1e-12, "所需 sd 必须 ∝ √天数"
    assert abs((1 - thr_2x) / (1 - thr_now) - math.sqrt(2)) < 1e-12

    fn = next(n for n in ast.walk(ast.parse(SCRIPT.read_text(encoding="utf-8")))
              if isinstance(n, ast.FunctionDef) and n.name == "decide")
    # ⚠️ 第一版写成「`decide` 里不许有任何浮点比较常量」⇒ 抓到 `bd < 0.10` 就红。
    #   **那是过严**：0.10 是「降幅太小就当没动」的 NEUTRAL 下限，
    #   是预注册里的**判断**，不是推导门槛。把它也逼成推导 ⇒ 人会去改一个本来
    #   正确的判断。过严的守卫与恒真的一样有害。
    # ⇒ 精确形式：**只有 0.10 以内的语义下限允许写死**；
    #   任何 >0.10 的字面量都是**与推导门槛竞争的第二个门槛**，必须红。
    #   （旧字面量 0.20 与新推导值 0.330 都落在这一侧。）
    typed = [c.value for c in ast.walk(fn)
             if isinstance(c, ast.Compare) for c in c.comparators
             if isinstance(c, ast.Constant) and isinstance(c.value, float)]
    assert all(v <= 0.10 for v in typed), (
        f"decide 里出现了 >10%% 的字面量门槛 {typed} —— "
        f"那是与推导门槛竞争的第二个判据")
    # 正控：这条守卫本身没瞎（typed 非空 ⇒ 它真的扫到了那个下限）
    assert typed, "AST 扫描没抓到任何字面量门槛 ⇒ 正控失效，别再相信上面的断言"

    # 真正的「推导而非手打」在**语法位置**上：`threshold` 必须来自函数调用
    assert any(isinstance(n, ast.Call)
               and getattr(n.func, "id", None) == "required_sd_drop"
               for n in ast.walk(fn)), (
        "decide 里的 threshold 必须由 required_sd_drop(...) 算出")
    assert any(isinstance(n, ast.Assign)
               and any(getattr(t, "id", None) == "threshold" for t in n.targets)
               and isinstance(n.value, ast.Call)
               for n in ast.walk(fn)), "threshold 必须被赋成一次调用，而不是一个字面量"


def test_the_threshold_boundary_is_exact_with_no_epsilon_slack():
    """★ 容差必须被单独测一条（第 4 条纪律）。

    `sd = sd_needed` ⇒ 降幅恰好等于推导门槛 ⇒ 应判 `SD_WORKS`；
    `sd = sd_needed × (1+1e-12)` ⇒ 降幅短 **6.7e-13** ⇒ 应判 `SD_TRADEOFF`。
    ⇒ 若有人把比较放宽成 `bd < threshold − 1e-9`，第二个用例会翻案。

    🚨 **`bd < threshold` 与 `bd <= threshold` 在这里测不出来，这是已知且已证的。**
    变异台 M5（`<` 改成 `<=`）打绿了，原因不是守卫漏了断言，而是：
    `threshold = 0.33040780634529643` **不在 `drop = (S−sd)/S` 的浮点网格上**
    —— 它恰好落在相邻网格点 `…964` 与 `…965` 之间。
    ⇒ `bd == threshold` **在可达输入上不存在** ⇒ 两个写法行为完全相同
    ⇒ M5 是**等价变异**，不是守卫有洞。（实测：sd 空间 ±2e6 个 ulp、
      400 万个网格点、命中 0。）
    ⇒ 这条守卫守的是**epsilon 松懈**（真能杀），不是**等号方向**（不可观察）。
    下一个人不必再搜一遍这个边界。
    """
    at = _arms(A1_inv_total__sd=_SD_NEEDED)
    just_over = _arms(A1_inv_total__sd=_SD_NEEDED * (1 + 1e-12))
    assert bw.decide(at, _flat(0.34))[0] == "SD_WORKS", "恰好达标必须过"
    assert bw.decide(just_over, _flat(0.34))[0] == "SD_TRADEOFF", (
        "只差 6.7e-13 的降幅就翻案 ⇒ 判据被某个 epsilon 放宽过")


def test_the_threshold_is_off_the_reachable_float_grid():
    """★ 把上面那条「无法测」的结论**做成守卫**，免得它退化成一句抱怨。

    断言的不是判决（判决在两种写法下相同），而是一个**关于浮点表示的事实**：
    `threshold` 不在 `drop` 的网格上 ⇒ `bd == threshold` 不可达。

    ⚠️ 它同时是一条**防空真**守卫：若哪天有人把门槛改写成某个
    恰好落在网格上的数（例如取整到 0.33），本例会红 ——
    那时 `<` 与 `<=` 就**变得可观察**了，必须补一条等号方向的断言。
    """
    thr = bw.required_sd_drop(_BASE["mean"], _BASE["sd"], _BASE["n_days"])
    grid = set()
    sd = _SD_NEEDED
    for _ in range(20000):                     # ±2e4 个 ulp ≈ ±2.8e-13
        sd = math.nextafter(sd, 0.0)
        grid.add((_BASE["sd"] - sd) / _BASE["sd"])
    sd = _SD_NEEDED
    for _ in range(20000):
        sd = math.nextafter(sd, 1.0)
        grid.add((_BASE["sd"] - sd) / _BASE["sd"])
    assert len(grid) > 10000, "扫描没扫到东西 ⇒ 本守卫恒真，别信它"
    assert thr not in grid, (
        "门槛现在**落在** drop 的浮点网格上 ⇒ `<` 与 `<=` 已变得可观察，"
        "必须补一条等号方向的断言（本守卫的 docstring 前提已失效）")


# ─────────────────── ④ 守卫自身的健康 ───────────────────
def test_the_guard_never_loads_the_panel():
    """★ 预注册 §8 的硬要求：守卫**不得**触发面板加载。

    断言方式是**结构**上的：脚本里不许出现对真实数据根的硬编码默认路径被
    单测跑到 —— 这里直接断言单测跑完之后没有产生大文件访问，
    且脚本的数据根是参数而不是常量。"""
    src = SCRIPT.read_text(encoding="utf-8")
    assert "build_cross_sections(" in src, "主流程必须走共享的横截面构建器"
    # 数据根必须是 argparse 参数（可被测试替换），不能是模块级常量
    tree = ast.parse(src)
    consts = [n.targets[0].id for n in tree.body
              if isinstance(n, ast.Assign) and len(n.targets) == 1
              and isinstance(n.targets[0], ast.Name)]
    assert not any("DATA_ROOT" in c or "SNAPSHOT" in c for c in consts), \
        "数据根不许是模块级常量"


def test_the_min_basket_global_is_restored_even_when_a_year_raises():
    """★ `aplus.verify.MIN_BASKET` 是全局量，脚本改了必须在 `finally` 还原 ——
    否则同进程后续的每日任务会**静默**按错误下限跑，而且不会有任何报错。"""
    from aplus import verify as v
    before = v.MIN_BASKET
    try:
        try:
            bw.main(["--data-root", "/nonexistent-路径", "--start", "2019-01-01"])
        except SystemExit:
            pass
    finally:
        assert v.MIN_BASKET == before


def test_a_window_before_2021_is_refused_rather_than_silently_measured():
    """★ 判决窗口必须与 `factor_width_mde` 一致（2021 起）。
    窗口不同 ⇒ 臂间不可比，而表格看上去完全正常。"""
    rc = bw.main(["--data-root", "/nonexistent-路径", "--start", "2019-01-01"])
    assert rc == 2


# ═══════════════════════════════════════════════════════════════
#  ④ 输入契约（2026-10-15，真实崩溃逼出来的）
# ═══════════════════════════════════════════════════════════════
#  起因：`scripts/basket_weighting.py::_weighted_daily` 读了
#  `leg["date"]`，而 `evaluate_weights` 的返回里**根本没有这一列**
#  ⇒ 面板载入 320s + 六个年份全跑完，才在第一行加权处抛 `KeyError`。
#  而那一列 `d` **零消费者** —— 既崩了，又没人用。
#
#  ★ 所以这一节测的不是「崩溃没了」（那是 5 分钟真跑的事），
#  而是**契约**：消费方能读哪些列、从哪读、缺列时必须响。
#  全部用**合成帧**，几十行，不碰面板。


def _leg_frame(**over) -> pd.DataFrame:
    """一份**完全符合权威清单**的合成逐行结果。

    ⚠️ 列顺序与 `aplus.backtest.RESULT_COLUMNS` 一致，但这里**刻意重打一遍**：
    合成帧必须是**从消费方的眼睛看过去的形状**，而不是同一份字面量 ——
    否则「消费方和生产方同时改错」会一起通过（一致性测试的经典失效）。
    真正把两边钉在一起的是下面那条「`_NEEDED_COLUMNS` 是派生的」。

    ★🚨 **第一版这个夹具是错的，而且错得很有教育意义**：
    我给 6 行各配了**不同的日期和不同的代码** ⇒ 每个「篮子」只有 1 只票。
    于是 `_weighted_daily` 的 `held` 只含第一天那只，
    后五天的 `idx` 恒为 0 ⇒ 产出 1 天序列，而**测试报的是
    「days=1 ≠ 6」** —— 一个看起来像代码 bug 的失败。

    真实面板是**每个日期几十上百只**。
    ⇒ **不像真实数据的夹具，会让守卫去测一个不存在的场景**，
    然后把「夹具错了」误报成「实现错了」。本仓已有同族事故：
    `test_the_two_arms_measure_different_quantities` 栽在 `std(x−c) ≡ std(x)` 上。
    ⇒ 判据：**造完夹具先问「它像不像面板里的一天」**，而不是「列齐不齐」。
    """
    dates = [f"2021-01-{d:02d}" for d in range(1, 5)]      # 4 个调仓/持有日
    codes = ["000001.SZ", "000002.SZ", "600000.SH"]        # 每日 3 只
    rows = [(d, c) for d in dates for c in codes]          # 12 行
    base = {
        "pred_date": [d for d, _ in rows],
        "code": [c for _, c in rows],
        "direction": ["up"] * len(rows),
        "actual_pct": [0.01, -0.02, 0.03] * len(dates),
        "result": [1, -1, 1] * len(dates),
        "market_pct": [0.001] * len(rows),
        "cost": [0.0002] * len(rows),
    }
    for k in list(over):                       # 覆盖成 None ⇒ 该列直接删掉
        if over[k] is None:
            base.pop(k, None)
        else:
            base[k] = over[k]
    return pd.DataFrame(base)


def test_the_needed_columns_are_derived_from_the_authoritative_list_not_retyped():
    """★ 契约不许有第二个真相源。

    两份字面量必然漂移 —— 那是第 17 种形状（同一个语义处理 N 次，
    第 N+1 处几乎必然漏）。今天的 `KeyError: 'date'` 就是这么来的：
    生产方那份清单只存在于**空路径那一行**里，从没被当成契约公布过。
    """
    from aplus.backtest import RESULT_COLUMNS
    assert bw._NEEDED_COLUMNS == tuple(
        c for c in RESULT_COLUMNS
        if c in {"pred_date", "code", "actual_pct", "market_pct", "cost"}
    ), "_NEEDED_COLUMNS 不再是从权威清单派生的 ⇒ 有人把它重打成了字面量"
    assert set(bw._NEEDED_COLUMNS) <= set(RESULT_COLUMNS)


def test_the_authoritative_list_has_no_date_column_and_that_is_deliberate():
    """🚨 `date` **不在**权威清单里，而这是**刻意的**，不是遗漏。

    `pred_date` 是信号日，成交日由 `entry_lag` 隔着 1 天推出；
    谁拿 `pred_date` 顶替成交日，就是把两个不同的量当成同一个量
    （第 7 种形状）。⇒ 必须有一条守卫把「不许加 date 列」钉住，
    否则下一个人的「顺手补上」会重新制造今天的崩溃。
    """
    from aplus.backtest import RESULT_COLUMNS
    assert "date" not in RESULT_COLUMNS, (
        "权威清单里出现了 `date`。\n"
        "⇒ 如果它是**成交日**，那它是第 7 种形状（与 pred_date 不可混比）；\n"
        "  如果它是 pred_date 的别名，那它就是今天那个 KeyError 的同一个坑。"
        "两种都不许靠加一列解决 —— 该做的是让调用方**显式声明**它要哪一个。"
    )


def test_a_frame_missing_the_cost_column_is_refused_rather_than_silently_uncosted():
    """★ 这条直接钉**形状 6（假装能调）**的修法。

    旧写法是 `if "cost" in leg.columns: net = net - leg["cost"]`。
    `cost` 是权威清单里**恒在**的一列 ⇒ 那个 `if` 永远为真，
    于是读代码的人以为「成本可以不带」，而：
      带 ⇒ 恒扣一次；不带 ⇒ 恒不扣
    **两条分支的净超额差一整个成本量级，而表格看上去完全正常。**

    ⚠️ 这条守卫第一版写反了方向：我原本要断言「缺列会抛」，
    而真正要防的是「不抛」。⇒ 判据是**它必须响**。
    """
    with pytest.raises(KeyError, match="缺列"):
        bw._weighted_daily(_leg_frame(cost=None), "equal", {}, {}, 60, 21, 1)


def test_a_missing_prediction_date_is_also_refused():
    """不是只有 `cost` 值得守 —— `pred_date` 缺了会让 `dates` 变空，
    而空 `dates` 走到最后是「样本不足，不构成测量」，
    **那是一条看起来很正常的消息**，比抛异常更难认。"""
    with pytest.raises(KeyError, match="pred_date"):
        bw._weighted_daily(_leg_frame(pred_date=None), "equal", {}, {}, 60, 21, 1)


def test_a_frame_carrying_an_extra_date_column_still_runs():
    """★ 这条是**删死代码**的判据，不是**换列名**的判据。

    `_weighted_daily` 第一版建了一个 `"d"` 列（读 `leg["date"]`），
    而它**零消费者**。修法必须是**删掉它**：
    如果改成 `leg["pred_date"]`，这帧照样能跑，测试照样绿 ——
    **而那一列就成了一句「`d` 就是信号日」的假话**，
    等哪天有人真的去读它，σ 窗口就会落在信号日而不是成交日上。
    ⇒ 这条断言的是「额外多一列不影响」，因此**换列名的修法也通过**；
    真正把删死代码钉住的是下面那条 AST 守卫。
    """
    s, meta = bw._weighted_daily(_leg_frame(date=["2021-01-05"] * 12),
                                 "equal", {}, {}, 60, 21, 3)
    assert meta["days"] == 4, f"应为 4 个持有日，实得 {meta['days']}"


def test_the_function_never_reads_a_column_outside_the_declared_contract():
    """★ AST 守卫：`_weighted_daily` 里每一个 `leg["…"]` 都在契约内。

    这条把「重新引入 `leg["date"]`」变成**当场红**，
    而不用等 5 分钟的真跑再崩一次。
    ⚠️ 锚在**语法位置**上而不是「这个串在文件里出现过」——
       否则它会被 docstring 里那段事故记录喂饱（第 13 种形状：
       作者与断言者是同一个人，且在同一次编辑里）。
    """
    src = inspect.getsource(bw._weighted_daily)
    tree = ast.parse(textwrap.dedent(src))
    read = set()
    for n in ast.walk(tree):
        if (isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name)
                and n.value.id == "leg" and isinstance(n.slice, ast.Constant)
                and isinstance(n.slice.value, str)):
            read.add(n.slice.value)
    assert read, ("AST 一个 `leg[...]` 都没抓到 ⇒ 正控失效，"
                  "别再相信上面那些断言")
    assert read <= set(bw._NEEDED_COLUMNS), (
        f"`_weighted_daily` 读了契约外的列 {sorted(read - set(bw._NEEDED_COLUMNS))}。"
        f"\n⇒ 要么把它加进契约（并说清它是什么量），要么删掉。"
        f"**不许**靠加一个 `if 列存在` 让它变成可选 —— 那是形状 6。"
    )

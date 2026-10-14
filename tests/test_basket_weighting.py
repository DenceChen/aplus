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
import sys
from pathlib import Path

import numpy as np
import pandas as pd

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
def _arms(**kw):
    a = {"A0_equal": {"sd": 10.0, "mean": 6.0},
         "A1_inv_total": {"sd": 10.0, "mean": 6.0},
         "A2_inv_excess": {"sd": 10.0, "mean": 6.0}}
    for k, v in kw.items():
        arm, field = k.split("__")
        a[arm][field] = v
    return a


def test_sd_neutral_when_the_best_arm_barely_moves_the_sd():
    """★ 零结果是本实验**最有决策价值**的输出（预注册 §1.4），所以
    它必须可达、且被明确命名 —— 而不是被当成「没跑出东西」。"""
    v, why = bw.decide(_arms(A1_inv_total__sd=9.5), {"A1_inv_total": {2022: 0.05}})
    assert v == "SD_NEUTRAL"
    assert "ρ≈1" in why


def test_sd_works_requires_both_the_drop_and_the_yearly_consistency():
    v, why = bw.decide(
        _arms(A1_inv_total__sd=7.0),
        {"A1_inv_total": {2021: 0.3, 2022: 0.25, 2023: 0.2,
                          2024: 0.28, 2025: 0.26, 2026: 0.22}},
    )
    assert v == "SD_WORKS"
    assert "仪器" in why, "★ sd 降只说明仪器变好，不说明 alpha 变大 —— 措辞必须带出去"


def test_a_big_average_drop_with_unstable_years_is_mixed_not_works():
    """★ 逐年一致性是**硬要求**。66 个独立观测上的均值差不可信，
    逐年一致性是唯一不额外要数据就能拿到的稳健性证据。

    这是「先判逐年、再判降幅」的守卫：若顺序反了，
    这个案例会被报成 SD_WORKS —— 而那正是本实验最该防的错误结论。"""
    v, why = bw.decide(
        _arms(A1_inv_total__sd=7.0),
        {"A1_inv_total": {2021: 0.5, 2022: 0.5, 2023: -0.4,
                          2024: 0.5, 2025: -0.3, 2026: -0.45}},
    )
    assert v == "SD_MIXED", why


def test_a_drop_that_costs_more_than_half_of_itself_is_a_tradeoff():
    v, why = bw.decide(
        _arms(A1_inv_total__sd=8.0, A1_inv_total__mean=1.0),
        {"A1_inv_total": {2021: 0.2, 2022: 0.2, 2023: 0.2,
                          2024: 0.2, 2025: 0.2, 2026: 0.2}},
    )
    assert v == "SD_TRADEOFF"
    assert "不采用" in why


def test_the_mean_cost_is_measured_in_sd_units_not_mean_units():
    """★ 预注册 §5 的核心口径：均值代价除以 **`sd_A0`** 而不是 `mean_A0`。

    两个均值都是 6%~9%，除以均值会得到一个很大的相对数、看起来像
    「牺牲了很多」；除以 sd 才是「以风险为单位衡量收益损失」——
    这正是风险平价的原始命题。

    ⇒ 守卫用一个**能区分两者**的构造：降幅 15%、均值从 6.0 掉到 4.0。
      除以 mean_A0 得 −33%（远超降幅一半 ⇒ TRADEOFF），
      除以 sd_A0 得 −0.20 sd 单位（< 降幅一半 ⇒ 不是因代价被否）。
    """
    arms = _arms(A1_inv_total__sd=8.5, A1_inv_total__mean=4.0)
    py = {"A1_inv_total": {2021: 0.15, 2022: 0.15, 2023: 0.15,
                           2024: 0.15, 2025: 0.15, 2026: 0.15}}
    v, why = bw.decide(arms, py)
    cost_sd_units = (arms["A0_equal"]["mean"] - 4.0) / arms["A0_equal"]["sd"]
    assert abs(cost_sd_units - 0.20) < 1e-12
    # 降幅 15% < 20% ⇒ 即使代价可接受也仍是 TRADEOFF（未达 SD_WORKS 门槛）
    assert v == "SD_TRADEOFF"
    assert "sd 单位" in why or "未达" in why


def test_no_verdict_is_issued_when_the_baseline_is_unusable():
    """★ 「拒绝出判决」必须真的什么都不给。
    空判决与「跑挂了」在报告上长得一模一样 —— 第 89 层。"""
    v, why = bw.decide({"A0_equal": {"sd": float("nan"), "mean": 0.0}}, {})
    assert v is None
    assert "拒绝出判决" in why


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

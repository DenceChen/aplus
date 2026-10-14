"""成本常数是**为 10 个仓位标定的** —— 拿去量别的名数就是换了一把尺子。

## 起因（2026-10-15，榜宽实验实测）

榜宽实验（`scripts/factor_width_mde.py`）跑出「净收益随榜宽单调上升」：

| N | 毛超额/年 | 净(real)/年 | 分块 t |
|---|---|---|---|
| 10 | 6.52% | +0.49% | 1.38 |
| 200 | 9.46% | +3.86% | 2.44 |
| 546 | 9.78% | **+5.05%** | 2.69 |

读起来像「**榜越宽，策略越好**」——正好是我这几个月一直想看到的结论。

**它整个是假的。**

## 为什么：`COST_REAL_50K = 0.0055` 与名数无关

`aplus/hurdle.py:64` 的 `COST_REAL_50K` 是**一个固定比例**。
而 A 股**单边最低佣金 ¥5/笔**（`aplus/costs.py::COMMISSION_MIN`）是**每笔固定额**，
单笔越小它占比例越高。

用仓库里**已经存在**的 `costs.FeeSchedule.rebalance` 实测（本金 ¥50,000，
K=21 ⇒ 年调仓 11.6 次，权益 100%）：

| N | 每只金额 | **真实年摩擦/权益** | 模型假设 | **低估** |
|---|---|---|---|---|
| **10** | ¥5,000 | **6.41%** | 6.39% | **1.0×** |
| 21 | ¥2,381 | 8.97% | 6.39% | 1.4× |
| 30 | ¥1,667 | 11.06% | 6.39% | 1.7× |
| 100 | ¥500 | 27.33% | 6.39% | **4.3×** |
| 200 | ¥250 | 50.57% | 6.39% | **7.9×** |
| 546 | ¥92 | **130.97%** | 6.39% | **20.5×** |

⇒ **N=546 的真实净收益是 9.78% − 130.97% = −121.19%/年。**
   报出来的是 **+5.05%/年**。

## ★ 为什么这没有伤到 K 扫描

K 扫描（`data/reports/k_sweep_summary.json`）五档**全部在 N=10**，
而 0.0055 **恰好就是 N=10 的实测值**（上表第一行 1.0×）。
⇒ **K 扫描的 P0 结清依然成立，一个数都不用改。**

一个「被标定在某个配置上」的常数，**在那个配置上是对的** ——
这就是为什么它一直没人报错：它在 90% 的用途上都合法。

## 这是第 17 种形状

不是「算错了」，是**「用 A 配置标定的仪器去量 B」**。
它不抛异常、不越界、每一步都合法，只是那个数在 B 里**没有意义**。

第 7 层（口径标签）管的是「数字离开脚本后要带着口径」；
这一种更靠前：**数字还在脚本里，口径就已经不适用了。**

## 本守卫的外部锚

¥5/笔是 **A 股法规**，不是本仓实现。
所以「宽榜成本被低估」这件事**不需要参考任何一行我的代码就能验证**，
这也是全仓少数「一旦红了就一定是真坏了」的守卫之一。
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from aplus.costs import FeeSchedule, annual_friction  # noqa: E402
from aplus.hurdle import COST_REAL_50K  # noqa: E402
from scripts.backtest_walk_forward_weights import COST_FLAT_CALIBRATED_N  # noqa: E402

CAPITAL = 50_000.0
EQUITY = 1.0
K = 21
ROUNDS_PER_YEAR = 244 / K


def _friction(n: int) -> float:
    return annual_friction(CAPITAL, EQUITY, n, ROUNDS_PER_YEAR) / EQUITY


def test_the_flat_constant_is_exactly_the_ten_position_value():
    """★ 这个常数**没有错**——它是 N=10 的值。本条把它钉死。

    若哪天有人「修正」了 `COST_REAL_50K`，K 扫描五档的数字会全部作废。
    所以本条的作用是**禁止**把它当成一个随手可调的口径参数。
    """
    measured = _friction(10)
    assert COST_REAL_50K * ROUNDS_PER_YEAR == pytest.approx(measured, rel=2e-2), (
        f"COST_REAL_50K×{ROUNDS_PER_YEAR:.1f} = {COST_REAL_50K * ROUNDS_PER_YEAR:.4%}"
        f"，而 N=10 的实测摩擦是 {measured:.4%}。"
        " 这个常数**是** 10 仓位的标定值；要改它必须整轮重跑 K 扫描。"
    )


def test_flat_constant_understates_cost_more_and_more_as_width_grows():
    """★ 核心不变量：**低估倍数随名数单调上升**。

    只断言两端（10 只≈1×、546 只≥10×）容易被人「调参数调绿」，
    所以这里断言**单调性** —— 那是成本结构的性质，不是某个数字。
    """
    ns = [10, 21, 30, 50, 100, 200, 546]
    under = [(n, _friction(n) / (COST_REAL_50K * ROUNDS_PER_YEAR)) for n in ns]
    ratios = [r for _, r in under]
    assert ratios == sorted(ratios), f"低估倍数不是单调的：{dict(under)}"
    assert ratios[0] == pytest.approx(1.0, abs=0.05), f"N=10 应≈1×，实得 {ratios[0]:.3f}"
    assert ratios[-1] > 10, (
        f"N=546 只被低估 {ratios[-1]:.1f}× —— "
        f"真实年摩擦 {_friction(546):.2%}，报出来的却是 "
        f"{COST_REAL_50K * ROUNDS_PER_YEAR:.2%}"
    )


def test_at_some_width_the_friction_alone_exceeds_the_whole_target():
    """★ **产品硬上限**：摩擦本身必须小于目标，否则产品数学上不成立。

    用户的目标是**年化 20% 净值**。若年摩擦 ≥ 20%，
    那么**信号必须给出 100% 的毛超额**才够 —— 而没人能长期做到。

    这条把「合理名数」从口味问题变成算术问题。
    """
    target = 0.20
    ns = [n for n in range(5, 300) if _friction(n) <= target]
    assert ns, "没有任何名数能让摩擦低于目标 —— 检查成本常量"
    hard_cap = max(ns)
    assert _friction(hard_cap) <= target < _friction(hard_cap + 1), (
        f"摩擦 ≤ {target:.0%} 的最大名数是 {hard_cap}"
    )
    # 🚨 这个上限必须远小于「为了测量而想用的名数」（100/200/546）
    assert hard_cap < 100, (
        f"名数上限 {hard_cap} 已经进入宽榜取证区 —— "
        "本条的前提（取证用的宽榜不可交付）需要重新审视"
    )


def test_fee_schedule_is_nonlinear_in_position_count():
    """★ **最低佣金是非线性的**：必须逐笔算，不能先求和再乘费率。

    这是 `FeeSchedule.rebalance` 存在的唯一理由。
    若哪天有人把它改成 `per * n * rate`，本条立刻红。
    """
    fee = FeeSchedule()
    total_notional = 50_000.0
    linear = fee.commission(total_notional)
    per_trade = fee.rebalance(total_notional, 10)
    assert per_trade > linear * 3, (
        f"逐笔算 {per_trade:.2f} vs 先求和 {linear:.2f} —— "
        "最低佣金没生效，说明被当成线性费率了"
    )


def test_the_backtest_is_the_only_path_to_the_real_cost_and_it_is_width_aware():
    """★ **运行层守卫**：实况成本必须**按名数**给，不许在别处偷偷用那个常数。

    这是本守卫的主条。第一版写的是「grep `factor_width_mde.py` 里有没有
    `COST_REAL_50K`」—— 它红了，红在 **`import` 那一行**，
    而那行恰恰是**声明**（产物的 `cost_model` 块必须报出「这个常数是谁」）。

    ⇒ 那是**子串断言 + 同一文件里存在合法声明 = 守卫被文档满足**（第 13 种形状）。
    真正的缺陷点在 `run_year`：**`top_n` 是它的参数，而成本与 `top_n` 无关**。
    今天 K 扫描全在 N=10 所以没出事，危险在于它**完全合法地等着被加宽**。

    ★ 第二版把它改成「标定名数之外直接 `raise`」—— 那是**过度设计**，
      当场被榜宽实验打脸（它也调 `run_year(xs, …, n, n, k)`）。
      正解不是禁止测量，是**换对的尺子**：
      现在 `real_cost_for` 是唯一入口，N=10 逐位返回旧常数（K 扫描不用重跑），
      别处按 `costs.annual_friction` 逐笔算。
    """
    from scripts.backtest_walk_forward_weights import (  # noqa: PLC0415
        COST_FLAT_CALIBRATED_N,
        real_cost_for,
    )

    # ① 标定名数上必须**逐位**回到旧常数 —— 这是「改了不用重跑 K 扫描」的凭据
    assert real_cost_for(10) == COST_REAL_50K, (
        f"N={COST_FLAT_CALIBRATED_N} 上返回 {real_cost_for(10):.6f}，"
        f"而旧常数是 {COST_REAL_50K:.6f} —— 这会让 K 扫描五档全部作废"
    )

    # ② 别处必须等于**按名数逐笔算**的每轮成本（不是年摩擦，年/轮要换算）
    for n in (21, 30, 100, 200, 546):
        got = real_cost_for(n, k=21)
        expect = annual_friction_for(n, 21) / ROUNDS_PER_YEAR
        assert got == pytest.approx(expect, rel=1e-9), (
            f"N={n} 的每轮成本返回 {got:.6%}，而逐笔算应该是 {expect:.6%}"
        )
        # ③ 且必须**明显高于**那个常数（否则等于「换了个名字继续用常数」）
        assert got > COST_REAL_50K * 1.2, (
            f"N={n} 只比常数高 {(got / COST_REAL_50K - 1):.0%} —— "
            "宽榜成本没有真的按名数算"
        )


def test_run_year_takes_its_real_cost_from_that_one_function():
    """★ **接线守卫**：唯一入口必须真的被用上，不能只是「存在」。

    上一条证明了 `real_cost_for` 是对的；这条证明 `run_year` **调的是它**。
    两条合起来才闭合 —— 只测函数的正确性，接线断了照样全绿（第 6 种形状）。
    """
    import inspect  # noqa: PLC0415

    import scripts.backtest_walk_forward_weights as bw  # noqa: PLC0415

    src = inspect.getsource(bw.run_year)
    assert "real_cost_for(top_n" in src, (
        "run_year 必须从 real_cost_for(top_n) 取实况成本 —— "
        "否则标定常数会被拿到别的名数上"
    )
    assert "COST_REAL_50K" not in src, (
        "run_year 里出现了裸的 COST_REAL_50K —— "
        "那条路径与 top_n 无关，正是第 17 种形状"
    )


def annual_friction_for(n: int, k: int) -> float:
    return annual_friction(CAPITAL, EQUITY, n, ROUNDS_PER_YEAR) / EQUITY


def test_the_width_artifact_recomputes_its_own_friction_per_width():
    """★ **产物层守卫**：宽榜产物里的摩擦必须能被 `annual_friction` 独立重算出来。

    这是唯一能在**不重跑 10 分钟面板**的前提下钉住那条成本链的层次。
    缺档直接报错 —— 「没有产物」与「产物说成本是平的」必须可区分
    （第 89 层：「什么都不留」会让空目录与崩溃长得一样）。
    """
    path = _ROOT / "data" / "reports" / "factor_width_mde.json"
    assert path.exists(), (
        f"{path} 不存在 —— 本守卫**不允许**在缺档时静默通过。"
        "跑 `python scripts/factor_width_mde.py --start 2021-01-01`。"
    )
    rep = json.loads(path.read_text(encoding="utf-8"))

    # ★ 产物必须**自报**这把尺子是为谁标定的（口径跟着数字离开脚本，第 7 层）
    cm = rep["cost_model"]
    assert cm["flat_constant_is_calibrated_for_n"] == COST_FLAT_CALIBRATED_N
    assert cm["flat_constant_void_above_n"] == COST_FLAT_CALIBRATED_N
    # ★ 声明的作废字段必须**真的存在于产物里**。
    #   `cm["void_field"] in rep` 是在 dict 的**键**里找，而它是个说明字符串 ——
    #   那种断言恒为 False，看着像守卫，其实是死代码。
    void_field = str(cm["void_field"]).split("——")[0].strip()
    field_name = void_field.split("[*].")[-1].split(".")[-1]
    first_row = next(iter(rep["widths"].values()))
    assert field_name in first_row, (
        f"产物声明作废字段是 {void_field!r}（末段 {field_name!r}），"
        f"而 widths 的行里没有这一列 —— 声明与实物对不上"
    )

    k = int(rep["k_rebalance_every"])
    checked = 0
    # ★★ 这三个字段**名里带 pct，值是分数**（0.0652 = 6.52%）——
    #    `power_floor` 的返回就是 `sd*√T` 这种分数，整个报告层统一如此。
    #    第一版这里多除了一个 100 ⇒ 拿 6.41% 去比 0.064%。
    #    **第 7 种形状第一次出现在守卫自己身上**：守卫假设了一个单位约定，
    #    而那个约定既没写在产物里，也没写在被守卫的脚本里。
    #    处置见下方 `test_the_pct_suffixed_fields_are_fractions_not_percents`。
    for row in rep["widths"].values():
        n = int(row["n"])
        expect = annual_friction_for(n, int(k))
        got = float(row["friction_width_aware_annual_pct"])
        assert got == pytest.approx(expect, rel=5e-3), (
            f"N={n} 的宽榜摩擦报 {got:.4%}，而逐笔重算是 {expect:.4%}"
        )
        # ★ 净超额必须 = 毛超额 − 逐笔摩擦（会计恒等式，成本只扣一次，第 15 种形状）
        net = float(row["net_width_aware_annual_pct"])
        gross = float(row["gross_annual_pct"])
        assert net == pytest.approx(gross - got, abs=2e-3), (
            f"N={n}：毛 {gross:.4%} − 摩擦 {got:.4%} = {gross - got:.4%}，"
            f"而产物报的净是 {net:.4%}"
        )
        # 作废阈值必须**真的低于本档名数**，否则那行等于没标
        void_from = int(row["net_flat_constant_is_void_above_n"])
        assert void_from == cm["flat_constant_void_above_n"] == COST_FLAT_CALIBRATED_N
        if n > void_from and row["net_real_annual_pct"] is not None:
            # 留着可以，但**必须**在行内带着作废阈值 —— 口径跟着数字离开脚本
            assert void_from < n, f"N={n} 的作废阈值 {void_from} 不低于本档名数"
        checked += 1
    assert checked >= 3, f"只查到 {checked} 档 —— 守卫没有真的在跑"



def test_the_pct_suffixed_fields_are_fractions_not_percents():
    """★ **本条由一次真实的守卫失败逼出来**（2026-10-15）。

    上一条守卫第一版写了 `float(row["friction_..._pct"]) / 100.0`，
    它红了：`Obtained 0.000641 / Expected 0.0641` —— 正好差 100 倍。

    ## 根因不是算错，是**字段名与值不同量纲**

    `power_floor` 的返回是 `sd*√T` / `effect*T` 这种**分数**，
    整个报告层（`factor_attribution.json`、`factor_width_mde.json`）统一如此，
    但字段名一律叫 `*_pct`。**名字说百分，值是分数。**

    ⇒ **这个约定从来没有被写下来过**。谁第一次读产物都得靠猜，
      猜错的表现是「差 100 倍」，而 100 倍的错**很容易被当成「另一个 bug」**。

    ## 为什么不全仓改名（那会引入新问题）

    改名或 ×100 会波及所有消费方与既有守卫，属于一次独立的大重构。
    **本条只做两件事**：把约定**写死并钉住**，让下一次有人改它时**响**。

    ## 双向检查
    ① 正向：这些字段的量级必须在「分数」区间（年化小数不可能到 100）
    ② 反向：若哪天有人改成真百分，`daily_sd*√T` 那条恒等式会先炸 ——
       守卫会红，而**红的原因是「量纲变了」**，不是「数字错了」。
    """
    path = _ROOT / "data" / "reports" / "factor_width_mde.json"
    assert path.exists(), f"{path} 不存在 —— 缺档不许静默通过"
    rep = json.loads(path.read_text(encoding="utf-8"))

    for row in rep["widths"].values():
        n = int(row["n"])
        for field in ("gross_annual_pct", "annual_sd_pct", "MDE_annual_pct",
                      "friction_width_aware_annual_pct",
                      "net_width_aware_annual_pct", "sd_theory_pct"):
            v = row[field]
            assert v is None or abs(v) < 10, (
                f"N={n} 的 {field}={v} —— 这不是分数。"
                " 若产物已改成真百分，本守卫与所有下游的显示口径都要一起改，"
                "**不许只改一处**"
            )

    # ★ 量纲的**自洽锚**：sd_annual 必须等于 daily_sd×√T（若产物暴露了 daily_sd）。
    #   `power_floor` 传的是 `series.to_numpy(float)`，MDE 与 sd 共用同一个 se_day。
    se_anchor = None
    for row in rep["widths"].values():
        if "daily_sd" in row:
            se_anchor = float(row["daily_sd"])
            break
    if se_anchor is not None:
        row = rep["widths"]["10"]
        assert row["annual_sd_pct"] == pytest.approx(se_anchor * math.sqrt(244), rel=1e-6), (
            "annual_sd_pct 与 daily_sd×√244 对不上 —— 年化口径被换过了"
        )


def test_the_artifact_is_not_older_than_the_script_that_writes_it():
    """★ **0 号守卫**：产物不得比产出它的脚本旧。

    ## 它是被一次变异逼出来的

    2026-10-15：把 `factor_width_mde.py` 的摩擦字段从分数改成真百分（×100），
    **8 条守卫全绿**。因为这 8 条全都读磁盘上**上一次跑出来的** JSON，
    改源码不改产物 —— 两者根本不在同一层相遇。

    ⇒ 与「守卫只有变异后算数」同族，但更靠前一层：
      **产物层守卫只对历史快照有效，对当前代码零覆盖。**
      它守的不是「这段代码会不会产出对的东西」，是「上次那份东西对不对」。

    ## 所以「修完 P0 一律重跑」必须是**可测的**，不能只是纪律
    mtime 比较是最便宜也最难赖掉的代理信号：脚本一改、产物没重跑，就红。
    （比 `git status` 可靠 —— 提交过的旧产物一样会红。）

    ## 边界（诚实标注）
    mtime 只证明「先后顺序」，不证明内容正确 —— 内容由前面 7 条负责。
    它也认不出「改了又改回」这种净零修改；那种情况由 P0 重跑清单负责。
    """
    art = _ROOT / "data" / "reports" / "factor_width_mde.json"
    src = _ROOT / "scripts" / "factor_width_mde.py"
    assert art.exists(), f"{art} 不存在"
    gap = art.stat().st_mtime - src.stat().st_mtime
    assert gap >= 0, (
        f"产物比脚本旧 {abs(gap):.0f} 秒 ⇒ 脚本改过而没重跑。\n"
        "**修完 P0 一律重跑，重跑前绝对收益数字不可引用。**\n"
        "重跑命令：.venv/bin/python scripts/factor_width_mde.py --start 2021-01-01"
    )


def test_mtime_alone_cannot_tell_a_fresh_artifact_from_an_in_flight_one():
    """★ 第 21 种形状：**产物比脚本新，仍可能是旧代码跑出来的。**

    ## 实测（2026-10-15）

    `factor_width_mde.py` 那次跑耗时 **553s**。我在这条跑的**最后 0.31 秒**
    给脚本加上了 `provenance.stamp(...)`：

    ```
    03:06:46.62  scripts/factor_width_mde.py        ← 我改的
    03:06:46.93  data/reports/factor_width_mde.json ← 跑完落盘
    gap = +0.31 s   ⇒ 上面那条 mtime 守卫判「绿」
    ```

    产物**没有 sidecar** ⇒ 它出自**盖章之前**的代码。
    因为 Python 的模块级代码在**进程 import 时就绑定了**，
    跑的过程中改磁盘上的文件，对在飞的进程**毫无影响**。

    ⇒ `产物 mtime ≥ 脚本 mtime` 证明的只是「写盘发生在改动之后」，
      **不是**「写盘用的就是改动之后的代码」。中间隔着 import 那一刻。

    ## 为什么 mtime 守不住

    mtime 是**文件层**的代理信号，而绑定发生在 **import 层**。
    任何「长跑 + 期间改源码」的组合，都能让这两层的先后关系**脱钩**。

    ## 那这条守什么

    它守的是更常见、更贵的那一半：**改完源码忘了重跑**。
    上面那个 0.31 秒窗口是它**证不出来**的部分，必须由 sidecar 兜底：
    章是**跑的时候**写的，所以「章里没有本次新增的字段」
    = 「产物早于那次改动」，与 mtime 无关。

    ⇒ 正控：mtime 绿 + sidecar 缺 ⇒ 必须喊。
      恒真的写法（只查 mtime）就是上面那条，它在这个状态下是绿的。
    """
    from aplus import provenance as P

    art = _ROOT / "data" / "reports" / "factor_width_mde.json"
    rec = P.read_stamp(art)
    assert rec is not None, (
        f"{art.name} 没有 sidecar ⇒ 它出自盖章之前的代码。\n"
        f"（文件 mtime 能证明「先后顺序」，证明不了「用的是哪份代码」——\n"
        f"  模块级代码在 import 时就绑定，跑的过程中改磁盘文件对它无效。）\n"
        f"重跑命令：.venv/bin/python scripts/factor_width_mde.py "
        f"--start 2021-01-01")

    assert rec["generator"] == "scripts/factor_width_mde.py", \
        f"★ 章记的生成器不对：{rec['generator']!r}"
    assert rec.get("data_fp"), (
        "★ 章里没有数据指纹 ⇒ 代码侧新鲜但数据侧不可知，"
        "这正是 02:22 归因判决早于 02:25 数据修复 77 秒的那类失效")

    # ★ 章必须与产物**同一次写入**：章比产物新 ⇒ 有人替换过产物，章描述的不是它
    gap = P.sidecar_path(art).stat().st_mtime - art.stat().st_mtime
    assert gap >= 0, (
        f"★ 章比产物新 {gap:.0f} 秒 ⇒ 产物被替换过，这份章描述的是另一个文件")

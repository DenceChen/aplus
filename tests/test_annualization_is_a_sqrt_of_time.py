"""年化口径守卫：**均值 ×T、标准差 ×√T、SE ×T —— 三者不一样。**

## 起因（2026-10-15，产物层交叉验算发现）

`data/reports/factor_attribution.json` 里有四个数，`annual_sd_pct` 与其余三个不自洽：

| 字段 | 产物值 | 反推的年化因子 |
|---|---|---|
| `annual_mean_pct` | 6.5223 | 6.5223/0.000258822 = **244** ✓ |
| `annual_sd_pct`   | 2.0542 | 2.0542/0.00841906 = **244** ✗ 应为 **√244 = 15.62** |
| `annual_se_pct`   | 0.05502 | 0.05502/0.00022549 = **244** ✓ |
| `MDE_annual_pct`  | 0.15414 | 0.15414/0.00063168 = **244** ✓ |

⇒ **只有 sd 用了 ×T。** 于是报告里印着「逐日超额 sd = 205%/年」，
而真实的年化 sd 是 **13.15%/年**——**差 15.6 倍**。

## 为什么「其余三个都用 ×T」是对的，不是三个错

`SE = sd/√n`。年化到**每年**：

```
SE_annual = sd_annual / √(n/T)
          = (sd_day·√T) · √(T/n)
          = sd_day · T / √n
          = SE_day · T
```

⇒ **SE 的年化因子确实是 T**，不是 √T。
（第一版排查时我按「SE 该 ×√T」推到底，得出「MDE 也错了」的结论，
 靠 `t = mean/SE` 与 `n = (t_power·sd/mean)²` 两处独立恒等式才发现推错了方向。）

同理 `MDE = t_power · SE`，跟着 SE 走 ×T，**也是对的**。

## 为什么这个字段**没有**污染判决（重要）

`decide()` 不读 `annual_sd_pct`。而 `factor_width_mde.py` 把它当
σ(N) 理论曲线的**参考基准**，算的是比值：

```
ratio = sd_n / (ref / √(n/base_n))     ← √244 在分子分母里约掉
```

⇒ **比值与年份化因子无关。** 所以 `WIDTHS` 那张表的 σ 结论仍成立。
受损的只有**写着这个数的那几句人话**（脚本 docstring、`CLAUDE.md` 第 16 种形状）。

## 本守卫的形态：不是断言，是**恒等式**

不对着产物硬编码数字（数字会随窗口变），
而是断言**四个字段之间的代数关系**——它对任何窗口都成立。
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
ART = _ROOT / "data" / "reports" / "factor_attribution.json"


def _arms() -> dict:
    assert ART.is_file(), f"产物不见了：{ART}"
    return json.loads(ART.read_text(encoding="utf-8"))["power_floor"]


def _assert_sd_annualizes_by_sqrt_time(target: dict) -> None:
    """**唯一实现**。主守卫与变异自测都必须调它。

    🚨 变异探针**不许持有自己的副本** ——
    今天第 14 次同族错误：探针打在我复制的那份代码上，
    而被测对象走的是另一条路，于是变异 100% 存活而我还以为它死了。
    """
    for arm, pf in target.items():
        d, t = pf["daily_sd"], 244.0
        assert pf["annual_sd_pct"] == pytest.approx(d * math.sqrt(t), rel=2e-3), (
            f"{arm}: annual_sd_pct={pf['annual_sd_pct']:.6f}，"
            f"应为 daily_sd×√244={d * math.sqrt(t):.6f}"
            f"（sd 的年化是 ×√T，不是 ×T）"
        )


def test_sd_annualizes_by_sqrt_time_not_by_time():
    """★ sd 的年化因子必须是 √T。**T 是根因**。

    红的原因必须是我要验的那件事：
    第一版守卫里我顺手也断言了 SE/MDE，SE 用 ×T 是对的，
    于是它在验一件不存在的事 ⇒ 红得莫名其妙。
    所以这里**只**断言 sd 这一项。
    """
    _assert_sd_annualizes_by_sqrt_time(_arms())


def test_the_other_three_annualize_by_time_and_are_not_the_bug():
    """★ SE / MDE / 均值按 ×T 是**对的**，这条把它钉住防止「好心办坏事」。

    有人看到「sd 该 ×√T」很可能顺手把 SE 也改成 ×√T。
    那会把 `t = mean/SE` 从 1.185 改成 18.3 —— 一个看起来
    「显著得多了」的数，实际上是**同一个数乘错了年化因子**。
    """
    for arm, pf in _arms().items():
        t = 244.0
        se_day = pf["daily_sd"] / math.sqrt(pf["n"])
        assert pf["daily_se"] == pytest.approx(se_day, rel=1e-9), f"{arm}: daily_se 不自洽"
        assert pf["annual_se_pct"] == pytest.approx(pf["daily_se"] * t, rel=2e-3), (
            f"{arm}: annual_se_pct 不该是 ×√T（SE = sd/√n，年化因子是 T）")
        assert pf["MDE_annual_pct"] == pytest.approx(
            pf["annual_se_pct"] * 2.80159, rel=5e-3), (
            f"{arm}: MDE_annual_pct 不等于 t_power×SE（MDE 必须跟着 SE 一起 ×T）")


def test_t_statistic_is_invariant_to_the_annualization_convention():
    """★ **t = 夏普 × √年数** —— 把 `t_naive` 与「正确年化的 sd」绑在一起。

    ```
    sd_annual = sd_day·√T            ← 正确年化（本函数自算，不读 annual_sd_pct）
    Sharpe    = mean_annual / sd_annual
    t         = Sharpe · √(n/T)       ← n/T 是样本年数
    ```

    物化验算（k21）：`0.065223 / (0.008419×√244) = 0.4961`，
    `× √(1394/244) = 1.1854` = 产物的 `t_naive` ✓

    ## 这条防的是哪一种「好心办坏事」

    把 `annual_sd_pct` 改成 `×√T` 之后，**很容易顺手把 `t_naive`
    也改成 `mean_annual / annual_sd_pct`** —— 那个数是 **0.496**（那是夏普），
    比 1.185 小，而且**看起来仍然像个 t 检验值**。
    数值上「更合理」，统计上**换了量**。

    本条要求 `t_naive` 等于「夏普×√年数」，
    于是「夏普冒充 t」当场露馅。
    """
    for arm, pf in _arms().items():
        T, n = 244.0, pf["n"]
        sd_annual = pf["daily_sd"] * math.sqrt(T)      # 自算，不读 annual_sd_pct
        sharpe = pf["annual_mean_pct"] / sd_annual
        t_expected = sharpe * math.sqrt(n / T)
        assert pf["t_naive"] == pytest.approx(t_expected, rel=5e-3), (
            f"{arm}: t_naive={pf['t_naive']:.4f}，夏普×√年数={t_expected:.4f}"
            f"（若它等于 {sharpe:.4f}，说明有人把夏普当成了 t）"
        )


def test_the_sqrt_vs_time_guard_would_catch_the_x244_bug():
    """★ **守卫只有变异后算数**（本仓铁律）。

    变异：把 `annual_sd_pct` 的年化因子从 `√T` 改成 `T`（也就是缺陷本身），
    要求**第一条守卫必须红**，而其余三条保持绿 —— 否则它们是恒真的装饰。

    🚨🚨 **第一版这条守卫是无效的，而且无效得很隐蔽。**
    它 `monkeypatch` 了 `scripts.factor_attribution.power_floor`，
    可被测的 `_arms()` **读的是产物 JSON**（`factor_attribution.json`），
    代码与产物之间**没有任何通路** ——
    ⇒ 变异打在了空气上，而 `pytest.raises` 本该捕获的「第一条守卫不红」
    **被掩盖成**了另一个错误信息。

    ★ **教训（第 14 次同族）：变异探针必须打在数据通路上，
    否则「我替换了」只是「我替换了我自己的副本」。**
    今天前端 agent 也栽在同一处：它复制了一份 `const boardDay = …`
    而没有执行文件里那行，变异 31/31 存活。

    ⇒ 所以这里变异的是**产物本身**（`_arms` 唯一的数据源）。
    并且**先自证变异真的生效**（断言变异体的值确实等于 ×T），
    再要求守卫红 —— 顺序反了就又变成「我不知道它为什么红」。
    """
    import copy  # noqa: PLC0415

    arms = _arms()
    # ★ 先确认产物里这个字段**确实存在且非平凡**，
    #   否则下面把一个不存在的键改掉，守卫红的原因会跟变异无关。
    arm_name, base = next(iter(arms.items()))
    assert base.get("annual_sd_pct") is not None, (
        f"产物 {ART} 的 arm={arm_name} 没有 annual_sd_pct —— "
        "变异无的放矢，请先重跑 factor_attribution.py"
    )

    mutated_arms = copy.deepcopy(arms)
    d = mutated_arms[arm_name]["daily_sd"]
    mutated_arms[arm_name]["annual_sd_pct"] = d * 244.0
    # ★ 自证：变异体的值必须**确实是 ×244**，不是 ×√244
    assert mutated_arms[arm_name]["annual_sd_pct"] != pytest.approx(
        d * math.sqrt(244.0), rel=1e-9
    ), "变异没生效（×244 与 ×√244 相同）—— 这条变异本身是无效的"

    _assert_sd_annualizes_by_sqrt_time(arms)              # 未变异：必须绿
    with pytest.raises(AssertionError):                   # 变异后：必须红
        _assert_sd_annualizes_by_sqrt_time(mutated_arms)

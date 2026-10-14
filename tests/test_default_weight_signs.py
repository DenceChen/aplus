"""默认权重的**符号**必须有实测 IC 背书——不是「调参调出来的偏好」。

## 为什么符号单独要钉，而不只是钉权重数值

钉数值（`== {"mom20": -1.0, ...}`）只能防「有人乱改」，防不住「依据消失了还
不改」。符号的依据是一份会重算的 IC 报告；重算后如果符号变了，默认权重
必须跟着变，否则线上会继续跑一个已被数据否掉的约定。

所以这里的钉法是**反向引用数据**：测试去读 `data/reports/factor_ic_7y.json`，
拿实测 IC 的符号去要求 `DEFAULT_WEIGHTS` 的符号。数据变 → 测试红。

## 证据本身有多硬（2026-10-12 那份 `factor_ic_7y.json`，窗口 2019-01-02 起）

`lag1_real`（信号日 D 排序、D+1 建仓，**非泄漏**）全截面：

    mom20 −0.0357(t=−9.40)  rev5 +0.0262(t=+7.22)  vol20 −0.0514(t=−11.28)
    vsur  −0.0290(t=−12.57) turn20 −0.0428        turnratio −0.0378
    ivol  −0.0590(t=−16.73) rsi −0.0366

**63/63**：8 个因子里 7 个满 8 个年份（2019–2026），逐年 IC 符号无一翻转；
`turnratio` 只有 7 年（缺 2019——需要累积成交量历史，年初无前值），
合计 63 个（因子×年）观测全部与该因子的全段 IC 同号。

这个数**由 `factor_ic.yearly_sign_consistency()` 从报告算出**，不手写。
本文件原先写的是「8 因子 × 8 年逐年符号 32/32 一致」，三处错：32/32
算术上不自洽（8×8 应为 64）、turnratio 实际只有 7 年、真实是 63/63。
信号本身没错（63/63 比 32/32 更强），但报出来的数是编的——而它是**直接
显示给用户**的证据句。反向引用见 `tests/test_yearly_sign_claim.py`。

`hit`（日频块同号占比）只有 0.35–0.45，**不能**据此说效应不稳定——那是
日与日之间的噪声，日频接近抛硬币是正常的。判效应真假看**逐年均值**，
63/63 一致恰恰排除了「一两个极端块造出 t=16」这个伪象。

## 为什么这不是又一次「97 组权重搜索」

那次搜的是权重**向量**并取最优，必然被多重比较污染（实测随机权重 30 组
里 1 组能过闸）。这里做的是每个因子**一次**「IC 符号是否为负」的符号检验：
8 次检验，Bonferroni 临界值 3.4，全部远超。1 bit 的决策，无择优，
不产生选择偏差。

而且方向有**独立佐证**：赛马里 `{mom20:−0.5, turnratio:−0.5}` 的 avg_up
+0.128%，是冠军 +0.031% 的 4.1 倍，t=5.04 为 97 组最高。那次赛马有自己的
train/test 切分，不依赖本文件。

## 仍然成立的局限

IC 是**样本内**（2019-01 ~ 2026-10）测的。翻转符号因此必须走注册-前瞻验证
才能晋级 `weights.json`——本测试只保证**方向**对，不保证**幅度**对。
"""
import math
from pathlib import Path

import pandas as pd
import pytest

from aplus.factor import DEFAULT_WEIGHTS, FACTOR_NAMES

IC_REPORT = Path(__file__).resolve().parents[1] / "data" / "reports" / "factor_ic_7y.json"

# 与 factor_ic_7y.json 的 `windows` 同名：`lag0_leaky` 是故意保留的泄漏
# 诊断窗（信号日 D 收盘即建仓），**不能**用作符号依据——用它定符号就是作弊。
REAL_WINDOW = "lag1_real"


@pytest.fixture(scope="module")
def ic_report() -> dict:
    if not IC_REPORT.exists():
        pytest.skip(f"缺少 {IC_REPORT.name}：符号证据不在，测试不能退化成断言新值")
    import json
    return json.loads(IC_REPORT.read_text(encoding="utf-8"))


def _overall_ic(report: dict) -> dict:
    """factor → lag1_real 全截面 IC。只取 `all`，不取 tail_*——尾段口径不同。"""
    return {r["factor"]: r["all"] for r in report["table"]
            if r["window"] == REAL_WINDOW and r.get("all") is not None}


# ---------- 证据一：默认权重的符号必须与实测 IC 同号 ----------

def test_default_weight_signs_match_measured_ic(ic_report):
    """RED 依据：翻转前 `mom20:+1.0` / `vol20:+0.5` / `vsur:+0.5` 三个符号全反。"""
    overall = _overall_ic(ic_report)
    wrong = []
    for factor, weight in DEFAULT_WEIGHTS.items():
        assert factor in overall, f"{factor} 不在 IC 报告里，无法为它的符号背书"
        ic = overall[factor]
        if math.copysign(1, float(weight)) != math.copysign(1, ic):
            wrong.append(f"{factor}: 权重 {weight:+} vs IC {ic:+.4f}")
    assert not wrong, "默认权重符号与实测 IC 相反 → 涨榜在挑明天该跌的票:\n  " \
                      + "\n  ".join(wrong)


# ---------- 证据二：IC 符号逐年稳定（排除「少数极端块造出大 t」） ----------

def test_measured_ic_sign_is_stable_in_every_single_year(ic_report):
    """63/63：任一因子出现一年反号，这条证据就作废，必须重跑而不是照用。

    本条只查「有记录的年份里符号是否一致」；**记录总数是多少**由
    `tests/test_yearly_sign_claim.py` 反向钉到报告上（63/63，`turnratio`
    缺 2019）。两件事分开，是为了不把「一致性」和「覆盖率」混成一个断言——
    混了之后，哪天数据扩到 64 格，本条会以「数字变了」失败并诱导人去改期望值。
    """
    import collections
    by_factor = collections.defaultdict(list)
    for row in ic_report["yearly"]:
        by_factor[row["factor"]].append((row["year"], row["ic_mean"]))

    for factor, rows in sorted(by_factor.items()):
        signs = {math.copysign(1, ic) for _, ic in rows if ic is not None}
        assert len(signs) == 1, (
            f"{factor} 的逐年 IC 出现符号翻转，效应不是稳定的 —— "
            f"该因子不该被赋予任何方向的权重。实测各年："
            + ", ".join(f"{y}={ic:+.4f}" for y, ic in sorted(rows))
        )


def test_every_factor_in_the_report_is_covered_by_the_stability_evidence(ic_report):
    """防止以后加因子时忘了这条证据——没证据的因子不许进默认权重。"""
    covered = {r["factor"] for r in ic_report["yearly"]}
    assert set(FACTOR_NAMES) <= covered, (
        f"这些因子没有逐年 IC 证据：{sorted(set(FACTOR_NAMES) - covered)}"
    )


# ---------- 行为钉：符号对不对，看榜单行为而不是看常量 ----------

def _vol_only_frame() -> pd.DataFrame:
    """4 只票，**只有 vol20 不同**，其余因子完全相同。

    IC 实测 vol20 = −0.0514：高波动 → 次日收益更低。所以
    低波动的应进涨榜、高波动的应进跌榜。符号若反了，这两只会互换。
    """
    return pd.DataFrame({
        "code": ["000001.SZ", "000002.SZ", "000003.SZ", "000004.SZ"],
        "name": ["低波甲", "次低乙", "次高丙", "高波丁"],
        "mom20": [0.10, 0.10, 0.10, 0.10],
        "rev5": [0.02, 0.02, 0.02, 0.02],
        "vol20": [0.10, 0.20, 0.40, 0.80],
        "vsur": [1.0, 1.0, 1.0, 1.0],
    })


def test_high_volatility_stock_must_not_land_in_the_up_basket():
    """翻转前的行为：vol20:+0.5 → 高波动拿高分 → 进涨榜 = 挑明天该跌的票。"""
    from aplus.predict import build_predictions

    up, down = build_predictions(_vol_only_frame(), top_n=1, bottom_n=1)
    assert up.iloc[0]["name"] == "低波甲", (
        f"涨榜首位是 {up.iloc[0]['name']}；实测 vol20 的 IC 为负，高波动不该排第一"
    )
    assert down.iloc[0]["name"] == "高波丁", (
        f"跌榜首位是 {down.iloc[0]['name']}；实测 vol20 的 IC 为负，高波动该排跌榜第一"
    )


def test_reversal_factor_still_points_the_way_it_did():
    """rev5 是唯一正 IC——它在因子层已取负号（`reversal()` 返回 −动量）。

    这条钉住**别顺手把它也翻成负的**：那会把全表唯一为正的信号翻过去。
    """
    from aplus.factor import reversal

    # 6 根，不是 5 根：`momentum(window=5)` 会取 `iloc[-6]` 做窗口前价。
    # 给 5 根直接 IndexError —— 那是测试序列长度不足，与符号约定无关。
    rising = pd.Series([1.0, 1.06, 1.12, 1.18, 1.24, 1.30])   # 近 5 日在涨
    falling = pd.Series([1.30, 1.24, 1.18, 1.12, 1.06, 1.00])  # 近 5 日在跌
    assert reversal(rising) < 0 < reversal(falling)
    assert DEFAULT_WEIGHTS["rev5"] > 0
"""「维持现任」必须比「晋级新候选」举证更难。

## 事故（2026-10-11 20:09，14 组赛马之后）

赛马结果：现任排第 10（测试超额 −0.57%），最好的候选 +0.04% 但 t=+0.37
不过 Bonferroni。裁决：`维持现任`，理由「显著性否决」。

**六道闸全部正确工作**，零晋级，零噪音混入。看起来很干净。

但把时间轴拉长，现任在**三个独立窗口**上全是负的：

| 窗口 | 天数 | 测试段超额 |
|---|---|---|
| 2021-12-30 ~ 2023-12-29 | 486 | −0.49%（t=−3.55） |
| 2024-10 ~ 2026-10（2 年终裁 v3） | 194 | −0.60%（t=−2.83） |
| 2025-12-16 ~ 2026-10-08（本轮） | 194 | −0.57% |

合计约 5 年、跨两轮牛熊，**没有一段是正的**。而反转族 `{mom20:-1.0}`
在同样两段上是 +0.41%（t=+5.52）与 +0.02%——**都不显著，但都不是负的**。

## 闸门体系的缺口

现有闸门只管「什么能进来」，不管「什么该被换掉」。
于是出现一个荒谬局面：**一个已被反复证伪的现任，因为「没有替代品」而继续服役**。
「没有更好的候选」被当成了「现在这个可以」。

这两句话不等价。正确的不对称是：
- **晋级新候选**：需要证明它显著为正（门槛高）
- **维持现任**：需要现任本身没有被证伪（门槛也不低）

现任 t=−2.83 / −3.55，两段都**显著为负**——它早就该被换掉，
而不是因为「没找到更好的」就一直留着。

## 修法

第七道闸 `champion_falsified`：现任自身在**独立窗口**上被证明为显著负时，
「维持现任」不再是默认结局，裁决必须显式标 `champion_falsified=True`，
并要求理由里写明「继续使用一个已证伪策略」这一事实。
"""
import pytest

from aplus.evolve_race import evaluate_champion_health


def _champion_windows():
    """现任的真实三段证据（2026-10-11 实测）。"""
    return [
        {"window": ["2021-12-30", "2023-12-29"], "n_days": 486,
         "excess": -0.0049, "t": -3.55},
        {"window": ["2024-10-01", "2026-10-09"], "n_days": 194,
         "excess": -0.0060, "t": -2.83},
        {"window": ["2025-12-16", "2026-10-08"], "n_days": 194,
         "excess": -0.0057, "t": -0.37},
    ]


def test_all_negative_with_significant_t_is_falsified():
    h = evaluate_champion_health(_champion_windows())
    assert h["falsified"] is True
    assert h["verdict"] == "significantly_negative"


def test_inconclusive_t_is_not_falsified():
    """t 不显著的负值不算证伪——只是没测出东西。"""
    h = evaluate_champion_health([{"window": ["2024-01-01", "2024-12-31"],
                                   "n_days": 200, "excess": -0.001, "t": -0.4}])
    assert h["falsified"] is False
    assert h["verdict"] == "inconclusive"


def test_mixed_sign_is_not_falsified():
    """只要有一段为正且显著，就不能叫「已被证伪」。"""
    h = evaluate_champion_health([
        {"window": ["2023-01-01", "2023-12-31"], "n_days": 200,
         "excess": -0.006, "t": -3.0},
        {"window": ["2024-01-01", "2024-12-31"], "n_days": 200,
         "excess": 0.004, "t": 3.0},
    ])
    assert h["falsified"] is False


def test_two_negatives_plus_one_positive_is_not_falsified():
    """**两段负 + 一段正**也不能判证伪。

    变异体 F4 删掉 `not positive` 条件后这个用例仍通过，因为
    `test_mixed_sign_is_not_falsified` 只有一段负、够不到 `len(negative) >= 2`。
    于是「三段里有两段显著为负」这种局面从未被真正测过。
    """
    h = evaluate_champion_health([
        {"window": ["2021-01-01", "2021-12-31"], "n_days": 240,
         "excess": -0.006, "t": -3.0},
        {"window": ["2022-01-01", "2022-12-31"], "n_days": 240,
         "excess": -0.005, "t": -2.8},
        {"window": ["2023-01-01", "2023-12-31"], "n_days": 240,
         "excess": 0.004, "t": 3.1},
    ])
    assert h["negative_windows"] == 2
    assert h["falsified"] is False, "有一段显著为正就不能叫「已被证伪」"


def test_two_significant_negative_independent_windows_is_enough():
    """两段独立窗口都显著为负即可判定证伪，不必等到第三段。"""
    h = evaluate_champion_health([
        {"window": ["2021-12-30", "2023-12-29"], "n_days": 486,
         "excess": -0.0049, "t": -3.55},
        {"window": ["2024-10-01", "2026-10-09"], "n_days": 194,
         "excess": -0.0060, "t": -2.83},
    ])
    assert h["falsified"] is True
    assert h["negative_windows"] == 2


def test_overlapping_windows_count_once():
    """重叠的窗口不算独立证据——同一段行情数两次会虚增信心。"""
    h = evaluate_champion_health([
        {"window": ["2024-01-01", "2024-12-31"], "n_days": 200,
         "excess": -0.006, "t": -3.5},
        {"window": ["2024-06-01", "2025-06-01"], "n_days": 200,
         "excess": -0.006, "t": -3.2},
    ])
    assert h["negative_windows"] == 1, "重叠窗口被当成两段独立证据了"
    assert h["falsified"] is False, "单段显著为负不足以判定证伪"


def test_empty_evidence_is_unknown_not_falsified():
    h = evaluate_champion_health([])
    assert h["verdict"] == "unknown"
    assert h["falsified"] is False


def test_falsified_reason_names_the_asymmetry():
    """证伪结论必须把举证责任的不对称说清楚，不能只是一句「维持现任」。"""
    h = evaluate_champion_health(_champion_windows())
    assert "证伪" in h["note"]
    # note 必须**点名否定**那句话，而不是回避它——
    # 只说「已证伪」而不说「所以不能用『没更好的候选』搪塞」，读者仍会自己找补
    assert "没有更好的候选" in h["note"]
    assert "不能拿" in h["note"]

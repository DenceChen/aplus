"""股票池闸必须**单向**：只拦「候选池比现任更小」，不拦「更大」。

## 矛盾（2026-10-11 19:50，evolve 触发前 10 分钟发现）

闸门本来是为拦 `{turnratio:-1.0}` 加的：它只能在 4541 只上打分，
而现任能打 4897 只，**小 7.3%**。少掉的那批正是换手率数据缺失的股票——
系统性缺失，排名天然不可比。方向是「候选池**更小**」。

而反转族 `{mom20:-1.0}` 只用**一个**因子。`composite_score` 的
`min_count=len(weights)` 意味着因子越少越容易打分，于是它的池子会
从 4897 涨到约 5441，**大 11%**——照样超过 2% 阈值。

**一刀切的后果**：闸门会拦下「把 4 个因子换成 1 个」这件完全正常的事，
理由却是「股票池不可比」。那一届的裁决会变成一句自相矛盾的判决。

## 两个方向，性质完全不同

| 方向 | 含义 | 可比性 |
|---|---|---|
| 候选池**更小** | 候选依赖的因子有 NaN → **数据缺失**造成系统性窄化 | 不可比，须拦 |
| 候选池**更大** | 候选因子更少、要求更松 → **设计使然**，可解释 | 可比，放行 |

所以闸门改成单向：`gap = (champ_n − cand_n) / champ_n`，
**只有 cand_n < champ_n 时才可能否决**。变大只记录、不否决。
"""
import pytest

from aplus.evolve_race import oos_decision

CHAMP = {"mom20": 1.0, "rev5": 0.3, "vol20": 0.5, "vsur": 0.5}


def _race(best_w, best_n, champ_n, best_excess=0.01, best_t=5.0, train_gain=0.2,
          best_down=-0.001):
    return {"table": [
        {"weights": best_w,
         "train": {"objective": 0.10 + train_gain},
         "test": {"objective": 0.20,
                  "stats": {"up_excess_vs_market": best_excess,
                            "avg_down_return": best_down,
                            "excess_t": best_t,
                            "excess_days": 194, "n_ranked": best_n}}},
        {"weights": CHAMP,
         "train": {"objective": 0.10},
         "test": {"objective": 0.0,
                  "stats": {"up_excess_vs_market": -0.006, "excess_t": -2.8,
                            "excess_days": 194, "n_ranked": champ_n}}},
    ]}


def test_blocks_narrower_universe():
    """候选池更小 = 数据缺失造成系统性窄化 → 必须拦。"""
    d = oos_decision(_race({"turnratio": -1.0}, best_n=4541, champ_n=4897), CHAMP)
    assert d["promote"] is False
    assert "不可比" in d["reason"]


def test_allows_wider_universe():
    """候选池更大 = 候选因子更少、要求更松，是设计使然 → 不得拦。

    `{mom20:-1.0}` 单因子 → 池子 5441 vs 现任 4897（+11%）。
    一刀切会把这个正常候选拦掉，判决理由还会写成「股票池不可比」——
    那等于用一条为别的缺陷造的闸，去否决一件完全正常的事。
    """
    d = oos_decision(_race({"mom20": -1.0}, best_n=5441, champ_n=4897), CHAMP)
    assert d["promote"] is True, d["reason"]
    assert abs(d.get("universe_wider") - 0.1111) < 1e-4, "变宽应当被记录，供交叉核对"


def test_equal_universe_passes():
    d = oos_decision(_race({"mom20": -1.0}, best_n=4897, champ_n=4897), CHAMP)
    assert d["promote"] is True


def test_missing_n_ranked_still_blocks():
    race = _race({"mom20": -1.0}, best_n=5441, champ_n=4897)
    for row in race["table"]:
        row["test"]["stats"].pop("n_ranked")
    d = oos_decision(race, CHAMP)
    assert d["promote"] is False


def test_zero_universe_still_blocks():
    d = oos_decision(_race({"turnratio": -1.0}, best_n=0, champ_n=4897), CHAMP)
    assert d["promote"] is False
    assert "为空" in d["reason"]


def test_observed_gap_of_73_percent_blocks():
    """真实观测到的窄化（4541 vs 4897 = 7.3%）必须被拦。"""
    d = oos_decision(_race({"turnratio": -1.0}, best_n=4541, champ_n=4897), CHAMP)
    assert d["promote"] is False
    assert "4541" in d["reason"] and "4897" in d["reason"]

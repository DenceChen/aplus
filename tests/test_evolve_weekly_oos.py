"""evolve_weekly 的样本外裁决（TDD）。

## 为什么单独测这个脚本

`scripts/evolve_weekly.py` 每周日 20:00 由 launchd 自动跑，是**唯一**会改写
`weights.json` 的地方。上一版它用单一 130 天窗口既选冠军又验收
（`maybe_promote` 的三道闸全作用在同一窗口上）——用历史挑权重本身就是数据泄露，
挑出来的权重记住了历史噪声。

2 年赛马已经给出判决：14 组候选里 **0 组**测试段对市场超额为正。也就是说，旧版
evolve_weekly 每跑一次，就在自动生产一个「样本内很漂亮、样本外是负的」权重并写进
线上配置。这不是概率问题，是机制问题，所以要对**裁决函数本身**钉死。

这些用例不碰真实数据、不写 weights.json，只验证裁决逻辑。
"""
import importlib.util
from pathlib import Path

import pytest

from aplus.evolve_race import oos_decision, split_window

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "evolve_weekly.py"


@pytest.fixture(scope="module")
def weekly():
    """加载 evolve_weekly.py 本身（不执行 main）。"""
    spec = importlib.util.spec_from_file_location("evolve_weekly_mod", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _mk(n):
    return [f"d{i:03d}" for i in range(n)]


def _k(w):
    return tuple(sorted(w.items()))


def _race_with(spec, weights, n=100, t_test=0.6):
    """构造 race() 的返回值（不真的跑回测——这里只验裁决，不验行情）。"""
    xs = {"_dates": _mk(n)}
    train_dates, _ = split_window(xs["_dates"])
    tset = set(train_dates)
    table = []
    for w in weights:
        tr_obj, te_obj, te_exc = spec[_k(w)]
        is_tr = lambda d: bool(d) and d[0] in tset  # noqa: E731
        table.append({
            "weights": w,
            "train": {"objective": float(tr_obj if is_tr(train_dates) else 0),
                      "stats": {"up_excess_vs_market": 0.01, "overall_hit_rate": 0.53}},
            "test": {"objective": float(te_obj if is_tr(train_dates) else te_obj),
                     "stats": {"up_excess_vs_market": float(te_exc),
                               "avg_down_return": -0.001,
                               "overall_hit_rate": 0.49,
                               "excess_t": t_test, "excess_days": 266,
                               # 股票池可比闸需要它（见 tests/test_universe_comparability.py）
                               "n_ranked": 4897}},
        })
    table.sort(key=lambda r: r["train"]["objective"], reverse=True)
    return {"train_dates": train_dates, "test_dates": _mk(n)[60:], "table": table}


CHAMP = {"mom20": 1.0}
CAND = {"mom20": 2.0}


def test_weekly_module_exposes_oos_decision(weekly):
    """脚本必须走样本外裁决，而不是旧的可能同窗口 maybe_promote。"""
    assert hasattr(weekly, "decide_promotion"), "缺少样本外裁决入口"
    assert weekly.oos_decision is oos_decision


def test_rejects_train_win_test_loss(weekly):
    """训练段大幅领先、测试段超额为负 → 必须否决。"""
    spec = {_k(CHAMP): (0.10, 0.10, -0.001), _k(CAND): (0.50, 0.02, -0.004)}
    r = _race_with(spec, [CHAMP, CAND])
    dec = weekly.decide_promotion(r, CHAMP)
    assert dec["promote"] is False
    assert "样本外" in dec["reason"] or "超额" in dec["reason"]


def test_rejects_when_train_gain_below_threshold(weekly):
    """训练段领先幅度不够（0.01 < 门槛 0.05）→ 否决，不许微调就换。"""
    spec = {_k(CHAMP): (0.100, 0.100, 0.001), _k(CAND): (0.110, 0.105, 0.002)}
    r = _race_with(spec, [CHAMP, CAND])
    assert weekly.decide_promotion(r, CHAMP)["promote"] is False


def test_promotes_only_when_both_segments_pass(weekly):
    """两条腿都过 → 允许晋级（证明不是无脑全否）。"""
    spec = {_k(CHAMP): (0.10, 0.10, 0.001), _k(CAND): (0.50, 0.30, 0.008)}
    r = _race_with(spec, [CHAMP, CAND], t_test=6.0)   # 显著，才可能过第四道闸
    dec = weekly.decide_promotion(r, CHAMP)
    assert dec["promote"] is True
    assert dec["best"]["weights"] == CAND


def test_empty_candidate_set_never_promotes(weekly):
    """候选为空或切不出两段 → 拒绝盲跳。"""
    assert weekly.decide_promotion({"table": []}, CHAMP)["promote"] is False
    empty = {"train_dates": [], "test_dates": [], "table": [], "error": "窗口太短"}
    assert weekly.decide_promotion(empty, CHAMP)["promote"] is False


def test_log_record_keeps_web_contract(weekly):
    """进化日志要保住前端 /api/evolution 依赖的字段形状。"""
    spec = {_k(CHAMP): (0.10, 0.10, 0.001), _k(CAND): (0.50, 0.30, 0.008)}
    r = _race_with(spec, [CHAMP, CAND])
    rec = weekly.build_log_record(_race_with(spec, [CHAMP, CAND], t_test=6.0),
                                  "2026-10-11", ["2024-01-02", "2026-10-09"], CHAMP,
                                  proposed=2, raced=1)
    assert rec["date"] == "2026-10-11"
    # 注意键名是 promoted（不是 promote）——前端 loadEvolution 读 e.decision.promoted
    assert rec["decision"]["promoted"] is True
    assert rec["decision"]["reason"]
    assert rec["researcher"] == {"proposed": 2, "raced": 1}
    # 前端 loadEvolution 读 evaluations[].{weights,objective,source,stats}；
    # 新增 train_stats / test_objective / oos_edge 供前端展示两段对比。
    for ev in rec["evaluations"]:
        assert "weights" in ev and "objective" in ev and "source" in ev
        assert "up_excess_vs_market" in ev["stats"]          # = 测试段（样本外）
        assert "overall_hit_rate" in ev["stats"]
        assert "overall_hit_rate" in ev["train_stats"]
        assert "train_objective" in ev and "test_objective" in ev
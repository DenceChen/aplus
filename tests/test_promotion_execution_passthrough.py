"""晋级落盘必须带上执行参数——这条测试是为了钉住一个**已经造成实盘灾难**的缺陷。

## 缺陷现场（2026-10-14 接手时发现）

七道闸是在赛马窗口上评的，K=21 的候选净年化 +77.6% 过闸。
但 `evolve_weekly.py` 落盘时这样调：

    save_weights(reports_dir, promoted_w, {...})     # ← 没传 execution

`save_weights` 的签名是 `(reports_dir, weights, evidence, execution=None)`，
不传就落成 `{"rebalance_every": 1, "buffer_multiple": None}`。

而实盘 `scripts/run_daily.py` 是这么读的：

    execution = cfg.get("execution") or {}
    every = int(execution.get("rebalance_every", 1) or 1)

于是**闸门放行的是月频策略，上线执行的是日频版本**。
实测同一组权重：月频净 +77.6%/年，日频净 **−168.9%/年**。

同一段代码里的回测基线 `evaluate_weights(xs, promoted_w, ...)` 也没传频率，
所以前端 `/api/backtest` 展示给用户看的是日频数字——
**上线的和展示的，都是那个过闸时没被检验过的版本。**

## 为什么难发现

`save_weights` 的 docstring 早就把这个失败模式写出来了：

    「实盘只读 weights，于是『每 21 个交易日调一次仓』被静默丢掉，
      按日频全换执行，而回测净值是按月频算的」

**文档写了，调用方没照做。** 所以这条测试存在的意义是让调用方无法再漂移。
"""
import json

from aplus.evolve import candidate_key, load_weights, save_weights


def _race_row(rebalance_every=21, buffer_multiple=None):
    """一条过闸的赛马行，形状对齐 `evolve_race.race()` 的输出。"""
    return {
        "weights": {"mom20": -0.5, "turnratio": -0.5},
        "rebalance_every": rebalance_every,
        "buffer_multiple": buffer_multiple,
        "train": {"objective": 1.23, "stats": {}},
        "test": {"objective": 1.19, "stats": {"up_excess_vs_market": 0.0013}},
    }


def test_execution_of_extracts_frequency_from_race_row():
    from scripts.evolve_weekly import execution_of

    assert execution_of(_race_row(21)) == {"rebalance_every": 21,
                                           "buffer_multiple": None}
    assert execution_of(_race_row(63, 3.0)) == {"rebalance_every": 63,
                                                "buffer_multiple": 3.0}


def test_execution_of_defaults_to_daily_when_row_has_no_frequency():
    """老赛马行没有频率字段 → 日频。**默认值本身就是危险的那个**，
    所以这里显式钉住，让它成为一个需要被有意识改动的值。"""
    from scripts.evolve_weekly import execution_of

    assert execution_of({"weights": {"mom20": -1.0}}) == {"rebalance_every": 1,
                                                          "buffer_multiple": None}


def test_promotion_writes_execution_params_to_weights_json(tmp_path):
    """端到端：晋级 → 落盘 → 读回，调仓频率必须原样存在。"""
    from scripts.evolve_weekly import execution_of

    row = _race_row(21)
    save_weights(tmp_path, row["weights"], {"promoted_on": "2026-10-14"},
                 execution=execution_of(row))

    raw = json.loads((tmp_path / "weights.json").read_text(encoding="utf-8"))
    assert raw["execution"]["rebalance_every"] == 21, (
        "晋级落盘丢了调仓频率 → 实盘会按日频执行 K=21 才验证过的策略")

    # 走生产同一个读法
    cfg = load_weights(tmp_path)
    every = int((cfg.get("execution") or {}).get("rebalance_every", 1) or 1)
    assert every == 21


def test_buffer_multiple_survives_the_round_trip(tmp_path):
    from scripts.evolve_weekly import execution_of

    row = _race_row(5, 3.0)
    save_weights(tmp_path, row["weights"], {}, execution=execution_of(row))
    assert load_weights(tmp_path)["execution"]["buffer_multiple"] == 3.0


def test_champion_key_and_saved_weights_still_identify_same_strategy(tmp_path):
    """落盘后 `candidate_key` 必须还能认出这就是刚才过闸的那组——
    否则「谁才是现任」会在下一次运行里对不上。"""
    from scripts.evolve_weekly import execution_of

    row = _race_row(21)
    save_weights(tmp_path, row["weights"], {}, execution=execution_of(row))
    cfg = load_weights(tmp_path)
    assert candidate_key({"weights": cfg["weights"],
                          "rebalance_every": cfg["execution"]["rebalance_every"],
                          "buffer_multiple": cfg["execution"]["buffer_multiple"]}) \
        == candidate_key(row)
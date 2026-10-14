"""实盘**不能**假装自己在执行冠军的配置。

## 事故（2026-10-11 21:00）

复合候选池上线后，冠军可能带上 `rebalance_every` / `buffer_multiple`——
`save_weights` 的证据里本来就有这两个字段了。而实盘路径：

```python
weights = load_weights(reports_dir)["weights"]        # 只取 weights
up, down = build_predictions(universe, top_n=10, bottom_n=10, weights=weights)
```

**每天从零选一遍 Top10，没有频率、没有缓冲区、没有上期持仓。**

于是如果赛马晋级出「`{mom20:-1.0}` + K=21」，实盘会：

- 照样每天出 10 涨 10 跌（看起来一切正常）
- 按**日频全换**执行，而那个配置的回测净值是按月频算的
- 每天把 3.5% 的年成本加上，而回测里这一项是 0.35%

**测的是月频，做的是日频，报告和实盘对不上，且没有任何地方会报错。**

独立验证里这两者的差距有多大（2019-2023，纯反转）：

    日频净 −168.9%   月频净 +77.6%

**方向都反了。**

## 这道闸现在只做一件事

**说清楚实盘到底在执行什么**。真正的持仓状态机还没接，
但静默地按日频跑一个为月频设计的配置，是这个平台今天最危险的状态。

`live_execution_status` 必须能回答：
「线上这份名单，是在执行 weights.json 里声明的那个配置吗？」

真正的持仓状态机还没接。默认保持日频（`rebalance_every=1`）不变，
**不改变任何已验证行为**；只有当 weights.json 声明了实盘无法执行的东西时才报警。
"""
import json

import pytest


def _write(tmp_path, payload):
    d = tmp_path / "reports"
    d.mkdir(parents=True, exist_ok=True)
    (d / "weights.json").write_text(json.dumps(payload, ensure_ascii=False),
                                    encoding="utf-8")
    return d


def test_legacy_weights_file_is_honored(tmp_path):
    """旧文件没有 execution 字段 = 当日频跑 = 实盘确实这么做。"""
    from aplus.evolve import live_execution_status
    d = _write(tmp_path, {"weights": {"mom20": 1.0}, "evidence": None})
    st = live_execution_status(d)
    assert st["honored"] is True
    assert st["rebalance_every"] == 1
    assert st["buffer_multiple"] is None


def test_missing_weights_file_is_NOT_reported_as_honored(tmp_path):
    """**没有记录 ≠ 记录正确**。这一条曾被写成 `honored is True`。

    2026-10-14 实测：仓库里根本没有 `weights.json`，
    `load_weights` 静默回落 `DEFAULT_WEIGHTS`（`mom20:+1.0`，最近一次赛马
    测试段超额 **−0.74%、t=−3.03**）@K=1；`live_execution_status` 随后返回
    `honored=True`，理由「日频全换，无缓冲区——与 weights.json 声明一致」。

    也就是说：**系统在自己不知道自己跑什么的时候，报告自己一切正常。**
    守护闸的语义是「实盘在执行声明的配置吗」，而没有声明时这个问题
    无解——无解不能记成「是」。
    """
    from aplus.evolve import live_execution_status
    st = live_execution_status(tmp_path / "reports")
    assert st["honored"] is False
    assert "weights.json" in st["reason"]


def test_load_weights_reports_whether_it_actually_read_a_file(tmp_path):
    """调用方必须能区分「读到文件」与「回落到默认值」。

    回落本身是合理的（首次运行还没有冠军），
    但**静默**回落不是——回落的默认值是 `mom20:+1.0`，实测为负。
    """
    from aplus.evolve import load_weights, save_weights

    reports = tmp_path / "reports"
    assert load_weights(reports)["source"] == "fallback"

    save_weights(reports, {"mom20": -1.0}, {"promoted_on": "2026-10-14"})
    assert load_weights(reports)["source"] == "file"


def test_fallback_default_is_labelled_as_untested_not_blessed(tmp_path):
    """回落到默认权重时必须带上「未经测量」的标记。

    默认权重在最近一次 730 天赛马里排第 81 名（共 87 组），
    测试段超额 −0.74%、t=−3.03——它是**已知的坏配置**，
    不能以「默认值」的中性面目出现在实盘。
    """
    from aplus.evolve import load_weights

    cfg = load_weights(tmp_path / "reports")
    assert cfg["source"] == "fallback"
    assert cfg["evidence"] is None
    assert "未经" in (cfg.get("caveat") or "")


def test_monthly_champion_is_flagged_as_unexecutable(tmp_path):
    from aplus.evolve import live_execution_status
    d = _write(tmp_path, {"weights": {"mom20": -1.0},
                          "execution": {"rebalance_every": 21, "buffer_multiple": None},
                          "evidence": {"promoted_on": "2026-10-11"}})
    st = live_execution_status(d)
    assert st["honored"] is False
    assert st["rebalance_every"] == 21
    assert "21" in st["reason"]


def test_buffer_only_champion_is_also_flagged(tmp_path):
    """缓冲区与频率正交：只声明缓冲区同样执行不了。"""
    from aplus.evolve import live_execution_status
    d = _write(tmp_path, {"weights": {"mom20": 1.0},
                          "execution": {"rebalance_every": 1, "buffer_multiple": 3.0}})
    st = live_execution_status(d)
    assert st["honored"] is False
    assert "缓冲" in st["reason"]


def test_reason_mentions_the_net_gap_not_just_the_field_name(tmp_path):
    """理由必须说清**后果**：日频全换会让年成本从 0.35% 变成 3.5%。"""
    from aplus.evolve import live_execution_status
    d = _write(tmp_path, {"weights": {"mom20": -1.0},
                          "execution": {"rebalance_every": 21, "buffer_multiple": None}})
    assert "日频" in live_execution_status(d)["reason"]


def test_save_weights_round_trips_execution(tmp_path):
    from aplus.evolve import load_weights, save_weights
    reports = tmp_path / "reports"
    reports.mkdir()
    save_weights(reports, {"mom20": -1.0}, {"promoted_on": "2026-10-11"},
                 execution={"rebalance_every": 21, "buffer_multiple": None})
    cfg = load_weights(reports)
    assert cfg["execution"]["rebalance_every"] == 21
    assert cfg["weights"] == {"mom20": -1.0}


def test_execution_defaults_to_daily_on_save(tmp_path):
    from aplus.evolve import load_weights, save_weights
    reports = tmp_path / "reports"
    reports.mkdir()
    save_weights(reports, {"mom20": 1.0}, {})
    assert load_weights(reports)["execution"] == {"rebalance_every": 1,
                                                   "buffer_multiple": None}


def test_load_weights_without_weights_file_has_execution(tmp_path):
    from aplus.evolve import load_weights
    cfg = load_weights(tmp_path / "reports")
    assert cfg["execution"] == {"rebalance_every": 1, "buffer_multiple": None}


def test_corrupt_execution_field_falls_back_to_daily_loudly(tmp_path):
    """坏字段不能悄悄变成日频后说「一切正常」——
    独立验证里日频净 −168.9%、月频 +77.6%，方向都反了。"""
    from aplus.evolve import live_execution_status
    d = _write(tmp_path, {"weights": {"mom20": -1.0}, "execution": "每周一次"})
    st = live_execution_status(d)
    assert st["honored"] is False
    assert st["reason"]
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

**说清楚实盘到底在执行什么**。

`live_execution_status` 必须能回答：
「线上这份名单，是在执行 weights.json 里声明的那个配置吗？」

## ★ 2026-10-15：事故已修，守卫随之反转

上面那次事故（**测的是月频、实盘做的是日频**）的根因是
**实盘没有持仓状态机**。今天补上了：

- `run_daily.py` 的持仓写入收进 `if due` 分支（非换仓日沿用旧持仓）
- `every` 从 `LIVE_REBALANCE_EVERY = 21` 下发，不显式传时 `save_weights`
  也不再退回 K=1

⇒ 「实盘执行不了月频」这个前提**不再成立**，
于是 `test_monthly_champion_is_flagged_as_unexecutable` 的方向翻转成
`..._is_now_honored_because_live_has_a_state_machine`。

⚠️ **翻转不是因为事故消失了，而是因为事故被修了。**
若哪天状态机被摘掉，`test_honored_depends_on_a_real_state_machine`
会先红 —— 它钉的正是 `honored=True` 的依据。
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
    """旧文件没有 execution 字段 ⇒ 走 `load_weights` 的回落 ⇒ K=LIVE_REBALANCE_EVERY。

    ⚠️ 2026-10-15 起这个值是 **21** 而不是 1。
    旧文件「没写 K」从来就没有一个独立于回落策略的确定含义 ——
    它取的是回落值，而回落值现在是月频。
    实盘读的是**同一个** `load_weights`，所以两者天然一致，`honored=True`。
    """
    from aplus.evolve import LIVE_REBALANCE_EVERY, live_execution_status
    d = _write(tmp_path, {"weights": {"mom20": 1.0}, "evidence": None})
    st = live_execution_status(d)
    assert st["honored"] is True
    assert st["rebalance_every"] == LIVE_REBALANCE_EVERY == 21
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


def test_monthly_champion_is_now_honored_because_live_has_a_state_machine(tmp_path):
    """★ 这条守卫在 2026-10-15 **反转了方向**，理由记在这里。

    原断言是 `honored is False`，理由写着「实盘没有持仓状态机」。
    那个前提**已经不成立**：`scripts/run_daily.py` 的持仓写入已收进
    `if due` 分支，非换仓日沿用旧持仓 —— 事故本身被修掉了。

    事故被修掉之后，正确的行为就变成 `honored is True`。
    **但这不是「把测试改成能过」**：
    `honored=True` 这个说法**只有在状态机真的存在时才是真的**，
    所以下面必须同时钉住那个前提（`test_honored_depends_on_a_real_state_machine`），
    否则这条测试就是一句没有依据的话。
    """
    from aplus.evolve import live_execution_status
    d = _write(tmp_path, {"weights": {"mom20": -1.0},
                          "execution": {"rebalance_every": 21, "buffer_multiple": None},
                          "evidence": {"promoted_on": "2026-10-11"}})
    st = live_execution_status(d)
    assert st["honored"] is True
    assert st["rebalance_every"] == 21
    assert "21" in st["reason"]


def test_buffer_only_champion_is_also_honored(tmp_path):
    """缓冲区与频率正交；缓冲区也已由 `if due and buf and held_up` 实现。"""
    from aplus.evolve import live_execution_status
    d = _write(tmp_path, {"weights": {"mom20": 1.0},
                          "execution": {"rebalance_every": 1, "buffer_multiple": 3.0}})
    st = live_execution_status(d)
    assert st["honored"] is True
    assert "缓冲" in st["reason"]


def test_honored_depends_on_a_real_state_machine():
    """★ `honored=True` 这个主张的**唯一依据**是状态机真的存在。

    `live_execution_status` 自己看不到 `run_daily.py`，它只是**声称**实盘
    在按 `due` 换仓。要让这句话不是空话，必须在这里钉住它的依据。

    ⇒ 这是一条**跨文件**守卫：产品里那三行一旦回退，这两条测试同时失去意义，
    而这一条会先红。
    """
    import ast
    import re
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "scripts" / "run_daily.py").read_text(
        encoding="utf-8")
    body = re.search(r"^def step_predict_and_allocate\(.*?(?=^def |\Z)", src, re.S | re.M)
    assert body, "step_predict_and_allocate 不见了"

    # ① 确实按 every 判到期
    assert re.search(r"is_due\(\s*prev\s*,\s*every\s*,\s*buf\s*\)", body.group(0)), (
        "实盘不再用 `is_due(prev, every, buf)` 判换仓日 —— "
        "`live_execution_status` 声称的「按声明执行」就没有依据了"
    )
    # ② 持仓写入确实只在 due 时发生
    tree = ast.parse(body.group(0))
    unguarded = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        cond = ast.unparse(node.test)
        for sub in ast.walk(node):
            tgt = (ast.unparse(sub.targets[0])
                   if isinstance(sub, ast.Assign) and sub.targets else None)
            if tgt and re.match(r"^state\[?['\"](up|down)['\"]", tgt) and "due" not in cond:
                unguarded.append((cond, tgt))
    assert not unguarded, (
        f"持仓写入又跑出 `if due` 了：{unguarded} ⇒ "
        "`honored=True` 变成一句假话，K 与缓冲区都只是名义上的"
    )


def test_reason_states_what_is_executed_not_the_old_daily_claim(tmp_path):
    """理由必须说**实盘在执行什么**，不能再喊「正在按日频全换」。

    事故修好之后继续输出旧警告，比不输出更坏 ——
    **它训练人忽略这道闸**（第 6 条：过期的前提变成对正确行为的报警）。
    """
    from aplus.evolve import live_execution_status
    d = _write(tmp_path, {"weights": {"mom20": -1.0},
                          "execution": {"rebalance_every": 21, "buffer_multiple": None}})
    reason = live_execution_status(d)["reason"]
    assert "日频" not in reason, (
        f"理由仍在喊日频：{reason!r} —— 实盘已经按 K=21 执行了"
    )
    assert "21" in reason


def test_save_weights_round_trips_execution(tmp_path):
    from aplus.evolve import load_weights, save_weights
    reports = tmp_path / "reports"
    reports.mkdir()
    save_weights(reports, {"mom20": -1.0}, {"promoted_on": "2026-10-11"},
                 execution={"rebalance_every": 21, "buffer_multiple": None})
    cfg = load_weights(reports)
    assert cfg["execution"]["rebalance_every"] == 21
    assert cfg["weights"] == {"mom20": -1.0}


def test_execution_defaults_to_monthly_on_save(tmp_path):
    """★ 2026-10-15 改：不显式传 execution 时，落盘的 K 必须是 21 而不是 1。

    留一个通往日频的默认后门，等于让「月频换仓」这个决策**只接了一半**：
    `load_weights` 回落给 21、`save_weights` 不传时写 1，
    实盘拿到哪个全看走哪条路。**半接的开关比不接更坏。**
    """
    from aplus.evolve import load_weights, save_weights
    reports = tmp_path / "reports"
    reports.mkdir()
    save_weights(reports, {"mom20": 1.0}, {})
    assert load_weights(reports)["execution"] == {"rebalance_every": 21,
                                                   "buffer_multiple": None}


def test_load_weights_without_weights_file_has_execution(tmp_path):
    from aplus.evolve import load_weights
    cfg = load_weights(tmp_path / "reports")
    assert cfg["execution"] == {"rebalance_every": 21, "buffer_multiple": None}


def test_corrupt_execution_field_falls_back_to_daily_loudly(tmp_path):
    """坏字段不能悄悄变成日频后说「一切正常」——
    独立验证里日频净 −168.9%、月频 +77.6%，方向都反了。"""
    from aplus.evolve import live_execution_status
    d = _write(tmp_path, {"weights": {"mom20": -1.0}, "execution": "每周一次"})
    st = live_execution_status(d)
    assert st["honored"] is False
    assert st["reason"]
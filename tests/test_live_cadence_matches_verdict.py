"""**实盘跑的调仓周期必须 == D2 判决档假设的那个**。

## 这条守卫在守什么

仓里有**两个**地方决定「多久换一次仓」：

| 路径 | 取值来源 |
|---|---|
| **实盘** | `run_daily.py` ⇒ `load_weights()["execution"]["rebalance_every"]`<br>· **回落**（没有 `weights.json`）⇒ `aplus/evolve.py::LIVE_REBALANCE_EVERY`<br>· **有 `weights.json`** ⇒ **它自己的 `execution`** |
| **判决** | `deployed_d2_verdict.json::verdict::config` ⇒ 假设 K = `LIVE_REBALANCE_EVERY` |

⇒ **今天两者一致**（仓里没有 `weights.json` ⇒ 实盘读常量 ⇒ 都是 63）。

🚨 **但一次晋级就能让它们分叉**：`aplus/evolve.py::save_weights` 写的是
`exec_cfg.get("rebalance_every", LIVE_REBALANCE_EVERY)` ⇒
**候选里若自带 `execution.rebalance_every = 21`，晋级就会把它原样带进生产**
⇒ 实盘悄悄回到 K=21 ⇒ **而 D2 判决档还在按 K=63 报数** ⇒

> **用户跑的账户 ≠ 被测量的账户，而没有任何东西会喊。**

⇒ 2026-10-20 的决策（`docs/DELIVERY_DECISIONS_2026-10-20.md` §12）把生产
从 K=21 改到 K=63，账户读数 +1.11% → +10.16%（**+9.05pp**）。
**一个未来的晋级就能把这 +9.05pp 静默还回去。**

⇒ 所以这条守卫把两处**钉在一起**：实盘读到什么，就必须等于判决假设的那个。

⚠️ 这条守卫**不判断哪个 K 更好**（那是预注册的事）⇒ 它只保证
「**说一套、跑一套**」。
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aplus.evolve import LIVE_REBALANCE_EVERY, load_weights  # noqa: E402

VERDICT_ART = ROOT / "data" / "reports" / "deployed_d2_verdict.json"


def _live_cadence(reports_dir) -> int:
    """实盘路径**真的会读到**的调仓周期（不是常量，是实读）。

    ⇒ 与 `run_daily.py` 的取法同源：`load_weights()["execution"]["rebalance_every"]`。
    """
    cfg = load_weights(reports_dir)
    execution = cfg.get("execution") or {}
    return int(execution.get("rebalance_every", 1) or 1)


def _conflicts(reports_dir) -> bool:
    """实盘跑的 K 与判决档假设的那个**不一致**吗？"""
    return _live_cadence(reports_dir) != int(LIVE_REBALANCE_EVERY)


def test_the_live_cadence_equals_the_one_the_d2_verdict_assumes():
    """🚨 说一套、跑一套 ⇒ 不许分叉。"""
    reports_dir = ROOT / "data" / "reports"
    live = _live_cadence(reports_dir)
    assert live == int(LIVE_REBALANCE_EVERY), (
        f"实盘跑的调仓周期是 **{live}** 个交易日，"
        f"而 D2 判决档假设的是 `LIVE_REBALANCE_EVERY` = **{int(LIVE_REBALANCE_EVERY)}**。\n"
        f"⇒ **用户跑的账户 ≠ 被测量的账户**：\n"
        f"   · 实盘：`run_daily.py` ⇒ `load_weights()['execution']['rebalance_every']`"
        f" = {live}\n"
        f"   · 判决：`deployed_d2_verdict.json::verdict::config` 假设 K = "
        f"{int(LIVE_REBALANCE_EVERY)}\n"
        f"⇒ 差 {abs(live - int(LIVE_REBALANCE_EVERY))} 个交易日 ⇒ 换手与摩擦全不同 ⇒ "
        f"判决读数**不描述这个账户**。\n"
        f"⇒ 修法：要么把 `weights.json` 的 `execution.rebalance_every` 改成 "
        f"{int(LIVE_REBALANCE_EVERY)}，要么**重新做**一份按那个 K 测的判决档 —— "
        f"**不许**只改其中一边。")

    # 判决档自己也得说清它假设的是哪个 K（否则上面那条无从对照）
    assert VERDICT_ART.exists(), f"判决档不在盘上：{VERDICT_ART.name}"
    art = json.loads(VERDICT_ART.read_text(encoding="utf-8"))
    cfg = art["verdict"]["config"]
    assert cfg["K"] == int(LIVE_REBALANCE_EVERY), (
        f"判决档自己假设的 K 是 {cfg['K']}，而生产常量是 "
        f"{int(LIVE_REBALANCE_EVERY)} ⇒ 判决档与生产已经分叉")


def test_the_conflict_detector_has_teeth(tmp_path, monkeypatch):
    """⚠️ 正控：造一个「实盘 K ≠ 判决 K」的世界 ⇒ 检测器必须喊。

    ⇒ 没有这一条，上面那条可能因为「读法错了」而**恒绿**
    （第 112 层：过严的筛选会把 `all()` 退化成装饰）。

    ⇒ 造法与生产同形：`weights.json` 里写 `execution.rebalance_every`。
    """
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "weights.json").write_text(json.dumps({
        "weights": {"mom20": -1.0},
        "execution": {"rebalance_every": 21, "buffer_multiple": None},
        "evidence": None,
    }), encoding="utf-8")

    assert _live_cadence(reports) == 21, "夹具没起作用（实盘没读到 21）"
    assert _conflicts(reports) is True, (
        "实盘 K=21 与判决 K 不一致时检测器没喊 ⇒ 它是恒真的装饰")

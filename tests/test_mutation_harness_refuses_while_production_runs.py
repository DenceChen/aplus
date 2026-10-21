"""变异台**不许**在生产跑在飞时改写生产脚本。

## 这道闸守的是什么（2026-10-21 晚，差点出事）

变异台会**真的改写**目标脚本 ⇒ 而 `scripts/run_daily.py` 是**生产入口**
（`launchd` 的 `com.aplus.daily` 每晚 18:30 拉它）。

⇒ **若生产跑在飞的同时有人在跑变异台**：
- ① 正在跑的那一跑**已经 import 完了** ⇒ 它不受影响（安全）；
- ② 但**任何一次重启**（守卫补跑、launchd 重试）会**重新 import**
  ⇒ ⇒ **新进程会读到被改坏的脚本** ⇒ 它会**对着生产路径跑**
  ⇒ ⇒ 那是「**变异体被执行**」，不是「变异体被测试」。

实测 2026-10-20 有过**两次 run_start 相隔 3 分钟**（19:30:02 → 19:33:45）
⇒ ⇒ **重启是常态，不是假设**。

⇒ ⇒ 变异台**必须**在生产跑在飞时**拒绝运行**。

## 为什么必须用 `ps -ax`

`ps` 不带 `-a`/`-x` **只列当前终端的进程**
⇒ 而 launchd 拉起的生产跑**没有终端** ⇒ 那样查**永远查不到**
⇒ ⇒ **闸门会恒绿** —— 正是第 112 层那种「看起来正常的装饰」。
⇒ 这条守卫把 `-ax` 钉死。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load() -> object:
    spec = importlib.util.spec_from_file_location(
        "mh_gate", ROOT / "scripts" / "mutation_harness.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


MH = _load()

SAMPLE = """
  69072 /Users/airboom/opencode/aplus/.venv/bin/python /Users/airboom/opencode/aplus/scripts/run_daily.py
  90796 /bin/zsh -c tail -F data/run_daily.log
  97975 /Users/airboom/opencode/aplus/.venv/bin/python -m pytest -q
  53496 /Users/airboom/opencode/aplus/.venv/bin/python /Users/airboom/opencode/aplus/scripts/serve.py
"""


def test_the_ps_call_lists_all_processes_not_just_the_terminals():
    """🚨 **`-ax` 不可省** ⇒ 否则闸门**恒绿**（装饰）。

    `ps` 不带 `-a`/`-x` 只列**当前终端**的进程，
    而 launchd 拉起的生产跑**没有终端** ⇒ 那样查**永远查不到**。
    """
    assert "-ax" in MH.PS_ARGV, (
        f"ps 的参数是 {MH.PS_ARGV} ⇒ 缺 -ax ⇒ "
        "launchd 的生产跑根本查不到 ⇒ 闸门恒绿")


def test_the_parser_finds_the_production_run():
    """生产跑**必须**被认出来（否则闸门形同虚设）。"""
    hits = MH.parse_production_runs(SAMPLE)
    assert [pid for pid, _ in hits] == [69072], f"没认出生产跑：{hits}"


def test_the_parser_ignores_the_log_watchers_and_pytest():
    """⚠️ **不许误伤**：看日志的外壳、变异台自己的 pytest 子进程都不是生产跑。"""
    hits = MH.parse_production_runs(SAMPLE)
    cmds = [cmd for _, cmd in hits]
    assert not any("run_daily.log" in c for c in cmds), f"把看日志的外壳当成了生产跑：{cmds}"
    assert not any("pytest" in c for c in cmds), f"把 pytest 子进程当成了生产跑：{cmds}"
    assert not any(c.endswith("serve.py") for c in cmds), f"把 web 服务当成了生产跑：{cmds}"


def test_the_harness_refuses_before_doing_anything(tmp_path, monkeypatch):
    """🚨 生产跑在飞 ⇒ **拒绝**，且**在任何改写之前**就拒绝。"""
    spec_file = tmp_path / "spec.mut"
    spec_file.write_text("M1 x\taplus/live_rebalance.py\nold\n=== 变异体 ===\nnew\n",
                          encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [
        "mutation_harness.py", str(spec_file), "--node", "tests/test_nothing.py"])
    monkeypatch.setattr(MH, "production_runs_in_flight",
                        lambda: [(69072, ".../scripts/run_daily.py")])

    def boom(*a, **k):  # pragma: no cover — 不许被调到
        raise AssertionError("闸门没拦住 ⇒ 变异台开始改写生产脚本了")

    monkeypatch.setattr(MH, "run", boom)
    assert MH.main() == 2, "生产跑在飞时变异台没有拒绝"


def test_the_harness_proceeds_when_nothing_is_in_flight(tmp_path, monkeypatch):
    """反向正控：没有生产跑 ⇒ **照常跑**（否则闸门会把变异台永久锁死）。"""
    spec_file = tmp_path / "spec.mut"
    spec_file.write_text("M1 x\taplus/live_rebalance.py\nold\n=== 变异体 ===\nnew\n",
                          encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [
        "mutation_harness.py", str(spec_file), "--node", "tests/test_nothing.py"])
    monkeypatch.setattr(MH, "production_runs_in_flight", lambda: [])

    calls = []

    def fake_run(specs, node):
        calls.append((specs, node))
        return (0, 0)

    monkeypatch.setattr(MH, "run", fake_run)
    assert MH.main() == 0, "没有生产跑时变异台却拒绝了"
    assert calls, "闸门放行了，但变异台没跑"

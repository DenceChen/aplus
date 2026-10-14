"""变异测试工具本身的还原能力（TDD，2026-10-14 增补）。

## 这组测试为什么必须存在

2026-10-14 出了一次真事故：我在变异测试跑到一半时用 `task_stop` 掐掉了进程。
`finally` 挡得住 Python 异常，**挡不住 SIGTERM**——于是
`scripts/ic_guided_race.py` 被留在变异态：

    emit("ic_sign_all", {f: 1.0 for f in usable})     # 变异体
    emit("ic_sign_all", {f: 1.0 if v["mean"] > 0 else -1.0
                         for f, v in usable.items()}) # 原本的

那个文件在变异态下**报告一切正常**：不报错、测试不红，
只是「让符号来自数据」这个脚本存在的唯一理由被摘掉了。
���是再跑一次变异测试才暴露的（报「源串未命中」）。

教训不是「别掐进程」——人一定会掐。是**工具被中断后必须能自己回到干净状态**，
而且这件事要有测试钉住，否则下一次改动又会把它弄丢。
"""
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MUT = ROOT / "scripts" / "mutation_check.py"


def _run(*args, **kw):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run([sys.executable, str(MUT), *args],
                          cwd=ROOT, env=env, capture_output=True, text=True, **kw)


def test_recovers_source_left_mutated_by_a_previous_killed_run(tmp_path):
    """上次被中断遗留的备份 → 本次启动必须自动还原，并喊出来。

    直接构造「备份还在、源文件已是变异态」的现场，验证自愈逻辑。
    """
    sys.path.insert(0, str(ROOT / "scripts"))
    import mutation_check as mc

    victim = "scripts/ic_guided_race.py"
    src = ROOT / victim
    original = src.read_text(encoding="utf-8")
    mutated = original.replace(
        'emit("ic_sign_all", {f: 1.0 if v["mean"] > 0 else -1.0\n'
        '                         for f, v in usable.items()})\n',
        'emit("ic_sign_all", {f: 1.0 for f in usable})\n', 1)
    assert mutated != original, "变异串没命中，测试自己就失效了"

    mc.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    bak = mc._backup_name(victim)
    bak.write_bytes(original.encode("utf-8"))
    try:
        src.write_text(mutated, encoding="utf-8")      # 模拟「停在变异态」
        n = mc._recover_pending()
        assert n == 1, "应当恢复且只恢复 1 个文件"
        assert src.read_text(encoding="utf-8") == original, "源文件没有被还原"
        assert not bak.exists(), "恢复后备份文件应当清掉"
    finally:
        src.write_text(original, encoding="utf-8")
        bak.unlink(missing_ok=True)


def test_recovery_runs_before_any_mutation_is_applied(tmp_path):
    """顺序钉子：自愈必须在安放变异体之前跑完。

    顺序反了会把「已被变异过的文件」当原件备份下来，
    等于把一次意外固化成永久改动，而且此后每次跑都还原不回去。
    """
    body = MUT.read_text(encoding="utf-8")
    i_recover = body.index("_recover_pending()")
    i_group = body.index("for g in groups:")
    i_run = body.index("run_group(g)")
    assert i_recover < i_run, "自愈必须发生在 run_group 之前"
    assert i_recover < i_group, "自愈必须发生在遍历分组之前"


def test_signal_guard_restores_in_flight_source_files():
    """SIGTERM 必须先还原在途源码再退出，而不是让 `finally` 落空。

    这里不真发信号（会杀掉测试进程自己），而是直接验证处理器存在，
    且它遍历的 `_PENDING` 字典确实被 run_group 维护。
    """
    sys.path.insert(0, str(ROOT / "scripts"))
    import mutation_check as mc

    assert hasattr(mc, "_PENDING"), "缺少在途源码登记表"
    assert hasattr(mc, "_install_signal_guards")
    assert hasattr(mc, "_recover_pending")

    src = MUT.read_text(encoding="utf-8")
    assert "signal.SIGTERM" in src, "SIGTERM 必须被处理"
    assert "bak.write_bytes(backup)" in src, "安放前必须落盘备份，否则 SIGKILL 无解"
    assert "bak.unlink(missing_ok=True)" in src, "还原后必须清掉备份"


def test_every_mutation_group_source_files_are_present_and_unmutated():
    """全量清单的源串必须都能命中。

    「无法安放」不是小毛病：它意味着某个变异体**根本没跑过**，
    而报告里的分母照样把它算进去了——58 个里跑过 57 个，
    读起来却像「58 个都验过了」。
    """
    sys.path.insert(0, str(ROOT / "scripts"))
    import mutation_check as mc

    missing = []
    for group, rel, old, _new, desc, _tests in mc.MUTATIONS:
        p = ROOT / rel
        if not p.exists():
            missing.append(f"{group}: 文件不存在 {rel}")
        elif old not in p.read_text(encoding="utf-8"):
            missing.append(f"{group}: 源串未命中 —— {desc[:40]}")

    assert not missing, "以下变异体无法安放（等于没验过）：\n" + "\n".join(missing)


def test_mutation_run_reports_no_unplaceable_mutants():
    """端到端跑一遍，任何「无法安放」都要让这次运行失败。

    这条比较慢，但它守的是唯一能发现「源码被变异体固化」的那道信号——
    上一次事故正是靠它报出来的。
    """
    p = _run("--only", "ic")
    assert "无法安放" not in p.stdout, p.stdout

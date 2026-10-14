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
这是再跑一次变异测试才暴露的（报「源串未命中」）。

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
    # ⚠️ 这条断言以前钉的是 `bak.write_bytes(backup)`，而 P1-4 把备份改成
    #   `_write_backup_atomic(bak, data)`（原子落盘）之后，那个串**只剩
    #   docstring 里解释「为什么改」的那句话**。断言因此一直绿着，
    #   守的却是一句注释而不是行为——删掉注释它就红、写坏备份它照样绿。
    #   ⇒ 钉真实调用，并额外要求「原子」这件事本身在场。
    assert "_write_backup_atomic(bak, backup)" in src, \
        "安放前必须经原子写落盘备份，否则 SIGKILL 会留下被截断的备份"
    assert "os.replace(tmp, bak)" in src, \
        "备份必须经 os.replace 原子落盘（直接 write 会留下被截断的备份）"
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


def test_every_mutants_named_tests_actually_exist():
    """每条变异指向的测试名必须真实存在。

    🚨 为什么这条必须与上一条**分开**：上一条只量「源串」，本条量「测试名」，
    两者是不同的失效面。而漏掉本条的后果比漏掉上一条更隐蔽：

        pytest 拿到一个不存在的 node id ⇒ rc=4「一个测试都没跑」
        旧代码 `if rc == 0: 存活 else: 被杀` ⇒ **rc=4 被记成「被杀」**

    也就是说，一条**没有任何守卫在场**的变异体，会被永久地显示成
    「这条守卫守住了」。本仓实测 86 行里有 4 行正是如此：
    测试被重命名后没人回来改 MUTATIONS，表就一直绿得很好看。
    这是**把证据缺失当成证据充分**——方向恰好与「测了没测出来」相反，
    后者至少会让人去查，前者只会让人更相信自己的测试。
    """
    sys.path.insert(0, str(ROOT / "scripts"))
    import mutation_check as mc

    missing = []
    for group, _rel, _old, _new, desc, tests in mc.MUTATIONS:
        for t in tests:
            if "::" not in t:                      # 整文件，无需点名
                if not (ROOT / t).is_file():
                    missing.append(f"{group}: 测试文件不存在 {t}")
                continue
            f, name = t.split("::", 1)
            fp = ROOT / f
            if not fp.is_file():
                missing.append(f"{group}: 测试文件不存在 {f}（{name}）")
            elif f"def {name}(" not in fp.read_text(encoding="utf-8"):
                missing.append(f"{group}: 测试名不存在 {name}（{f}）—— "
                               f"多半是重命名后没回来改 MUTATIONS")

    assert not missing, ("以下变异指向的测试不存在，"
                         "它们会被记成「被杀」而实际没跑：\n" + "\n".join(missing))


def test_run_group_does_not_count_never_ran_as_killed():
    """尺子必须区分「跑了并且红了」与「一个测试都没跑」。

    pytest 的退出码是分层的：

        rc=0  跑了，全绿      ⇒ 存活
        rc=1  跑了，红了      ⇒ **被杀**（这才是变异体被抓住的唯一证据）
        rc=4  用法错误        ⇒ 一个测试都没跑
        rc=5  收集到 0 个测试 ⇒ 一个测试都没跑

    旧写法只看「非零」，于是 rc=4/5 被算成「被杀」。
    这条用一个**故意指向虚空**的探针变异来钉：它必须报「不可验证」，
    且 `killed` 不得增加。
    """
    sys.path.insert(0, str(ROOT / "scripts"))
    import mutation_check as mc

    probe = ("__probe__", "scripts/mutation_check.py",
             'ROOT = Path(__file__).resolve().parent.parent\n',
             'ROOT = Path("/tmp")  # 变异：探针\n',
             "探针：故意指向一个不存在的测试名",
             ["tests/test_mutation_restore.py::test_zzz_this_does_not_exist"])
    body = MUT.read_text(encoding="utf-8")
    assert body.count(probe[2]) == 1, "探针锚点必须唯一命中（否则本守卫无效）"

    mc.MUTATIONS.append(probe)
    try:
        killed, total, notes = mc.run_group("__probe__")
    finally:
        mc.MUTATIONS.remove(probe)
        assert MUT.read_text(encoding="utf-8") == body, \
            "探针跑完必须把 mutation_check.py 逐位还原"

    assert killed == 0, \
        f"一个测试都没跑的变异被记成了「被杀」({killed})——这是把证据缺失当证据充分"
    assert any("不可验证" in n for n in notes), \
        "必须明确报「不可验证」，而不是混进存活或被杀：" + "\n".join(notes)
    # 🚨 理由也必须对（变异台实测 M10：把「一个测试都没跑」改成「跑了」，
    #   8 条测试**全绿**——标签有人守，它在解释什么却无人过问。
    #   守卫说错话比守卫不说话更坏：读的人会以为那是个正常的「跑了」结论。）
    assert any("一个测试都没跑" in n for n in notes), \
        ("「不可验证」的理由必须说清是「一个测试都没跑」，"
         "不能改成任何听起来像正常结论的说法：" + "\n".join(notes))


def test_the_guards_in_this_file_still_have_teeth_when_read_as_ast():
    """从**语法树**读回本文件的三条新守卫，把它们从自我指涉里拉出来。

    ## 为什么需要这一层（变异台实测：M3/M4/M5 三条全部存活）

    变异 `tests/test_mutation_restore.py` **自己的断言体**时，
    「本该抓住这个变异的那条测试」正好被变异本身废掉 ⇒ 没有测试能在运行中
    抓住自己。这是自我指涉守卫的固有上界，不是本守卫写得不好。

    出路是本仓既有的手法：让**另一条**测试去读守卫的源码。
    ⚠️ 但必须用 `ast` 而不是字符串匹配——否则就重蹈 M5 的覆辙：
    那条 `assert "bak.write_bytes(backup)" in src` 之所以一直绿着，
    唯一原因是它命中了 `_write_backup_atomic` 的 **docstring 里解释
    「为什么改」的那句话**。字符串匹配分不清「守行为」和「守一句注释」。
    """
    import ast

    src = (ROOT / "tests" / "test_mutation_restore.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    # 🚨🚨 第一件事：**把本函数自己的源码剔除**。
    # 检测器不得为自己作证——`required` 列表本身是一条 `Assign` 语句，
    # 它也在 blob 里，于是把目标处改掉、列表里的副本还在，判据照样满足
    # （实测 M4/M5/M6 三条全部这样存活）。这是守卫的不动点缺陷，
    # 不是判据写得不好，只能靠剔除自身来打破。
    me = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef)
              and n.name == "test_the_guards_in_this_file_still_have_teeth_when_read_as_ast")
    mine = ast.get_source_segment(src, me)

    # 取样层：收**全部语句节点**（`ast.If` 装着 `elif` 的判据，它不是
    # `Assert`），但**排除 `Expr`** —— docstring 就是 Expr，收进来就等于
    # 把「守一句注释」和「守行为」重新混在一起（第一版只收 `ast.Assert`，
    # 结果本守卫在**未变异时就已红**，而变异台不验基线 ⇒ 全是假 credit）。
    stmts = [ast.get_source_segment(src, n) or ""
             for n in ast.walk(tree)
             if isinstance(n, ast.stmt) and not isinstance(n, ast.Expr)]
    blob = "\n".join(s for s in stmts if s not in mine)

    required = [
        # 测试名存在性那一维必须在场
        ('f"def {name}(" not in fp.read_text',
         "「测试名存在」这一维被删掉了，尺子退回半盲"),
        # 🚨 源串存在性那一维也必须在场（变异台实测 M8：换成 `elif False:`
        #   之后 8 条测试**全绿**——因为本文件那条守卫是重新遍历 MUTATIONS
        #   自己算的，根本不经过 `test_every_mutation_group_source_files_are_present_and_unmutated`
        #   里那个 `elif`，于是那一维无人看守，摘掉它没有任何东西会喊）
        ('old not in p.read_text',
         "「源串存在」这一维被摘掉了，没有任何东西会喊"),
        # 探针守卫必须严到 killed == 0
        ("killed == 0",
         "探针守卫不再要求 killed==0，「没跑」可以冒充「被杀」"),
        # 原子落盘必须真的被断言，且不能是弱断言
        ('"os.replace(tmp, bak)" in src',
         "原子落盘断言被摘掉或被换成恒真式弱断言"),
    ]
    missing = [why for frag, why in required if frag not in blob]
    assert not missing, ("本文件的守卫被削弱了（语法树层面确认）：\n"
                         + "\n".join(missing))


def test_mutation_run_reports_no_unplaceable_mutants():
    """端到端跑一遍，任何「无法安放」都要让这次运行失败。

    这条比较慢，但它守的是唯一能发现「源码被变异体固化」的那道信号——
    上一次事故正是靠它报出来的。
    """
    p = _run("--only", "ic")
    assert "无法安放" not in p.stdout, p.stdout

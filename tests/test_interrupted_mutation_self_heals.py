"""被中断的变异测试必须能自己回到干净状态（2026-10-15 增补）。

## 这组测试为什么必须存在

2026-10-14 出了一次真事故（记在 `tests/test_mutation_restore.py` 的文档串里）：
变异测试跑到一半被 `task_stop` 掐掉，`finally` 挡不住信号，
`scripts/ic_guided_race.py` 被留在变异态 —— 而它在变异态下
**报告一切正常**：不报错、测试不红，只是存在的唯一理由被摘掉了。

据此仓里有了 `scripts/mutation_check.py` 的三层还原网
（`finally` → 信号守卫 → 磁盘备份 + 启动自愈）。

★ **2026-10-15 在同一个形状上又踩了一次**：这次不是 SIGTERM，是**网本身有洞**。
`tests/test_factor_attribution.py` 的变异测试**手写 `write_text` + `finally`**，
既没登记磁盘备份也没装信号守卫 —— 三层全绕过。
⚠️ 而第三层的自愈只挂在 `mutation_check` 启动上，
**普通全仓门禁被 OOM SIGKILL 时没有任何东西会来读那份备份**。

⇒ 洞的准确形状不是「忘了备份」，是「备份了但没人负责读」。
⇒ 修法也不是再加一层，而是把自愈的触发点从「谁改的」挪到**每次会话开工**。

## 判据纪律

下面每条守卫都按 [[guards-only-count-after-mutation]] 的要求做过变异验证：
变异后**先断言「文件/行为真的变了」**，再看测试红不红。
「变异没落地导致的绿灯」被当成过一次绿灯的原因（见 MISTAKE_LOG 第 14 种形状）。
"""
from __future__ import annotations

import ast
import os
import re
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _mc(tmp_root: Path):
    """把 `mutation_check` 的两个根指到临时目录，返回该模块。

    ⚠️ 必须在临时根上做：**被测函数会写真实文件**，
    而本组守卫绝不能重蹈「测试动真源码」那个洞的覆辙。
    """
    scripts_dir = str(ROOT / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import mutation_check as mc

    mc.ROOT = tmp_root
    mc.BACKUP_DIR = tmp_root / ".mutation_backup"
    return mc


@pytest.fixture()
def mc_on_tmp(tmp_path):
    _mc(tmp_path)
    import mutation_check as mc

    yield mc
    _mc(ROOT)          # 还原模块全局，别把临时根漏给后面的测试


# ── 行为层：真调 _recover_pending，断言它做了什么 ──────────────────────

def test_with_no_backup_left_behind_the_recovery_touches_nothing(mc_on_tmp, tmp_path):
    """常态必须是**彻底空操作** —— 没出事的时候不许写任何字节。

    这是「过严的守卫和恒真的一样有害」的反面：自愈如果每次会话
    都去碰 700 个源文件算 sha，那它本身就成了污染源。
    """
    assert mc_on_tmp.BACKUP_DIR.exists() is False
    assert mc_on_tmp._recover_pending() == 0
    assert not mc_on_tmp.BACKUP_DIR.exists(), "空转也建了备份目录 ⇒ 不是空操作"


def test_a_backup_left_by_a_killed_run_is_restored_byte_for_byte(mc_on_tmp, tmp_path):
    """核心行为：备份在 ⇒ 源文件被还原成**备份里的那串字节**。

    判据用 `read_bytes()` 而不是 `read_text()`：
    还原必须回到字节原样，中间隔一层文本编解码就等于允许漂移。
    """
    original = b'emit("ic_sign_all", {f: 1.0 for f in usable})\n'   # ← 变异态
    victim = tmp_path / "scripts" / "demo.py"
    victim.parent.mkdir(parents=True)
    victim.write_bytes(original)

    mc_on_tmp.BACKUP_DIR.mkdir(parents=True)
    bak = mc_on_tmp._backup_name("scripts/demo.py")
    clean = b'emit("ic_sign_all", {f: 1.0 if v["mean"] > 0 else -1.0\n' \
           b'                         for f, v in usable.items()})\n'
    bak.write_bytes(clean)

    # 写入确认：确认现场确实构造对了，否则下面的绿什么也不证明
    assert victim.read_bytes() == original != clean

    n = mc_on_tmp._recover_pending()

    assert n == 1, f"应恢复恰好 1 个文件，实际 {n}"
    assert victim.read_bytes() == clean, "源文件没有被还原回备份字节"
    assert not bak.exists(), "还原后备份应当清掉，否则下次会话会重复还原"


def test_a_backup_that_cannot_be_restored_is_kept_not_raised(mc_on_tmp, tmp_path):
    """还原不了 ⇒ **喊，但不许抛，也不许把备份删掉**。

    ⚠️ 现状（2026-10-15 增补前）：`_recover_pending` 直接
    `src.write_bytes(...)`，源文件被删/改名时抛 `FileNotFoundError`。
    自愈挂上「每个会话开工」之后，这意味着**一次残留能阻断此后每一场门禁**。

    ⇒ 判据是三条同时成立：不抛 / 返回值不计入 / **备份字节仍在盘上**
    （那串字节是原件唯一的拷贝，删掉就是静默丢证据）。
    """
    mc_on_tmp.BACKUP_DIR.mkdir(parents=True)
    bak = mc_on_tmp._backup_name("scripts/vanished.py")
    original_bytes = b"x = 1\n"
    bak.write_bytes(original_bytes)

    assert mc_on_tmp._recover_pending() == 0, "没还原成却计入 n ⇒ 报告会谎报自愈成功"
    assert bak.exists(), "备份被删了 ⇒ 原件字节静默丢失"
    assert bak.read_bytes() == original_bytes, "备份内容被改动"
    assert not (tmp_path / "scripts" / "vanished.py").exists(), (
        "还原失败时**凭空造出了源文件** —— 备份不该复活已删的脚本")


# ── 接线层：自愈必须真的挂在每个会话上 ────────────────────────────────

def _conftest_fixture_decorator(fname: str):
    """从 `tests/conftest.py` 的 AST 里取出该 fixture 的装饰器实参。

    ⚠️ **刻意不去读 pytest 的私有属性**（`_pytestfixturefunction` /
    `_fixture_function_marker`）：本仓 pytest 9.1.1 用的是后者，
    7/8 用前者——我第一版守卫按旧形状断言，拿到 None 直接红了。
    ★ 教训：拿「我以为的对象形状」当判据，等于把判据建在会变的地上。
    装饰器语法本身是稳定的，所以接线判据走 AST。
    """
    src = (ROOT / "tests" / "conftest.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == fname:
            for dec in node.decorator_list:
                if not isinstance(dec, ast.Call):
                    continue
                fn = dec.func
                dotted = (fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", ""))
                if dotted != "fixture":
                    continue
                return {kw.arg: ast.literal_eval(kw.value) for kw in dec.keywords}
    return None


def test_every_pytest_session_runs_the_recovery_automatically():
    """★ 钉接线，不钉机制 —— 上面的行为守卫**证明不了**这个函数会被调用。

    ⚠️ 这正是本组 bug 的形状：`finally` 与磁盘备份的机制都好好的，
    坏的是「没人调用」。只测函数 = 第 18 种形状（机制绿 ≠ 数据绿）。
    """
    kw = _conftest_fixture_decorator("recover_interrupted_mutations")
    assert kw is not None, "tests/conftest.py 里没有 recover_interrupted_mutations fixture"
    assert kw.get("scope") == "session", (
        f"scope={kw.get('scope')!r}：非 session 级则每个测试都跑一遍，"
        "且盖不住「会话中途」才安放的变异体")
    assert kw.get("autouse") is True, (
        "不是 autouse ⇒ 只有显式请求它的测试才会触发，"
        "而本组守卫要覆盖的是**别人**的测试留下的现场")


def test_the_recovery_runs_and_reports_zero_when_the_tree_is_clean(
        recover_interrupted_mutations):
    """★ 行为侧：它真的是 pytest 能解析的 fixture，且跑得起来。

    ⚠️ 显式请求它还顺带证明了「session 级」这个 scope 是真被 pytest 认的，
    不是只写在源码里的字面量。
    """
    assert recover_interrupted_mutations == 0, (
        "仓里没有遗留备份时应当返回 0；若非 0，说明此刻仓里躺着"
        "「上次被中断的变异体」—— 先查 .mutation_backup/")


# ── 反向闸：自愈**不许**吃掉正在飞的变异体（2026-10-15 当场实测的真事故）──

def test_a_backup_owned_by_a_live_mutation_check_is_not_swallowed(
        mc_on_tmp, tmp_path):
    """★ 本组最重要的一条守卫，而且它守的是一个**自愈自己造成的事故**。

    `tests/conftest.py` 的自愈挂在每个 pytest 会话开工前，
    而 `run_group` **自己就是跑 pytest 子进程的那个人** ——
    于是它每安放一个变异体，紧接着启动的 pytest 就把
    **它自己刚写的**那份磁盘备份当成「上次被中断的残留」还原掉了。

    ## 后果不崩，是**静默的假绿**

    在**收集期就 import 了**被测模块的守卫仍会红（模块已在内存里）；
    而**在测试体内才 import / 才 `read_text()` / 才 `inspect.getsource()`**
    的守卫读到的是已被还原的原件 ⇒ 全绿 ⇒ 被记成
    「存活：测试全绿，说明它没测到这个形状」。

    实测（`cap_weighted` 组 24 个变异体）：21/24 → 12/24，
    12 条守卫被误报成安慰剂，而**手动重跑每一个都真的变红**。

    ⇒ 判据：备份归一个**活着且身份是 mutation_check** 的进程所有时，
      自愈必须**跳过**，一个字节都不许写。
    """
    import subprocess
    import sys as _sys

    # ⚠️ 字节字面量只能放 ASCII（`b"...中文..."` 是 SyntaxError，不是失败）。
    # 判据要的是「逐位还原」，与内容是不是中文无关。
    original = b"x = 1  # MUTATED\n"
    victim = tmp_path / "scripts" / "inflight.py"
    victim.parent.mkdir(parents=True)
    victim.write_bytes(original)

    mc_on_tmp.BACKUP_DIR.mkdir(parents=True)
    bak = mc_on_tmp._backup_name("scripts/inflight.py")
    owner = mc_on_tmp._owner_name("scripts/inflight.py")
    clean = b"x = 1  # PRISTINE\n"
    bak.write_bytes(clean)

    # 起一个**真的**活着、且 argv 里带 mutation_check 的进程当主人。
    # 不用打桩：打桩测的是桩。
    proc = subprocess.Popen(
        [_sys.executable, "-c", "import time; time.sleep(30)",
         "mutation_check.py"],            # ← argv 里有它，`ps -o command=` 看得见
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        owner.write_text(str(proc.pid), encoding="utf-8")
        assert mc_on_tmp._owner_is_live(owner), (
            "主人进程是活的，但探针说它死了 —— 判据本身坏了")

        n = mc_on_tmp._recover_pending()
        assert n == 0, "在飞的变异体被当成残留还原了（守卫正在被自己的网吃掉）"
        assert victim.read_bytes() == original, "变异体被还原 ⇒ 本轮变异证据无效"
        assert bak.exists(), "备份被删了 ⇒ 主人进程被 SIGKILL 时再也还原不回来"
    finally:
        proc.kill()
        proc.wait(timeout=10)


def test_a_backup_owned_by_a_dead_run_is_still_recovered(mc_on_tmp, tmp_path):
    """★ 反向的正控：自愈**必须**仍然有效 —— 否则上一条就成了「永远跳过」。

    这是「过严的守卫和恒真的一样有害」的正面对照：
    跳过的条件必须**只**覆盖活着的主人，一旦放宽成「有 owner 就不还原」，
    SIGKILL 事故（2026-10-14 那次）就会**静默复发**。

    主人 pid 取一个确定已死的：起一个进程、等它退出、拿它的 pid。
    """
    import subprocess
    import sys as _sys

    original = b"x = 1  # MUTATED\n"
    victim = tmp_path / "scripts" / "killed.py"
    victim.parent.mkdir(parents=True)
    victim.write_bytes(original)

    mc_on_tmp.BACKUP_DIR.mkdir(parents=True)
    bak = mc_on_tmp._backup_name("scripts/killed.py")
    owner = mc_on_tmp._owner_name("scripts/killed.py")
    clean = b"x = 1  # PRISTINE\n"
    bak.write_bytes(clean)

    proc = subprocess.Popen([_sys.executable, "-c", "pass"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    proc.wait(timeout=30)
    owner.write_text(str(proc.pid), encoding="utf-8")

    assert not mc_on_tmp._owner_is_live(owner), (
        "主人已经退出，探针却说他活着 ⇒ 还原网被永久短路了")

    assert mc_on_tmp._recover_pending() == 1
    assert victim.read_bytes() == clean
    assert not bak.exists() and not owner.exists()


def test_a_backup_owned_by_a_live_bystander_is_still_recovered(
        mc_on_tmp, tmp_path):
    """★ pid 复用的守卫：主人**活着但不是 mutation_check** ⇒ 照样还原。

    为什么这条必须存在：`_owner_is_live` 只认 pid 的话，pid 会被复用 ——
    上一次跑留下的 `.owner` 写着 pid 4242，几周后 4242 归了某个无关进程，
    于是那份**真残留**备份被当成「在飞的变异体」**永远跳过**。
    这就是 docstring 里说的「备份了但没人负责读」的镜像：
    这次不是没人读，是**读了却认错了主人**。

    ⇒ 判据是**双证**：pid 活着 **且** cmdline 认得出是 mutation_check。
    ⚠️ 主人在自己那轮里就是当前 pytest 进程（`pid == os.getpid()`）也认，
      但那是巧合不是判据 —— 这里的 Bystander 是**别的**活进程。
    """
    import subprocess
    import sys as _sys

    original = b"x = 1  # MUTATED\n"
    victim = tmp_path / "scripts" / "reused.py"
    victim.parent.mkdir(parents=True)
    victim.write_bytes(original)

    mc_on_tmp.BACKUP_DIR.mkdir(parents=True)
    bak = mc_on_tmp._backup_name("scripts/reused.py")
    owner = mc_on_tmp._owner_name("scripts/reused.py")
    clean = b"x = 1  # PRISTINE\n"
    bak.write_bytes(clean)

    # 活着的**旁观者**：argv 里刻意不含 mutation_check。
    proc = subprocess.Popen(
        [_sys.executable, "-c", "import time; time.sleep(30)", "unrelated_job.py"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        owner.write_text(str(proc.pid), encoding="utf-8")
        assert not mc_on_tmp._owner_is_live(owner), (
            "活着的旁观进程被认成 mutation_check 了 ⇒ pid 复用会让真残留永远不被还原")

        assert mc_on_tmp._recover_pending() == 1
        assert victim.read_bytes() == clean
    finally:
        proc.kill()
        proc.wait(timeout=10)


def test_a_backup_whose_owner_is_unreadable_is_still_recovered(
        mc_on_tmp, tmp_path):
    """★ 主人文件**坏掉**（半写、被截断、手工编辑）⇒ 当残留处理。

    ⚠️ 这一支**只有 owner 文件存在时**才可能跑到 ——
    `if owner.is_file() and _owner_is_live(owner)` 短路，
    所以「没有 owner」的常规残留根本进不来。
    ⇒ 没有这条守卫，`_owner_is_live` 的 `except` 分支就是**零覆盖**，
    而变异验证会把它静默报成「存活」（实测：第一次跑正是如此 ——
    变异体确实落地了，**只是打在了不被执行的那一行**）。

    方向很重要：**读不出主人 ⇒ 还原**，不是跳过。
    反过来（跳过）会让一份真残留永久滞留。
    """
    original = b"x = 1  # MUTATED\n"
    victim = tmp_path / "scripts" / "torn.py"
    victim.parent.mkdir(parents=True)
    victim.write_bytes(original)

    mc_on_tmp.BACKUP_DIR.mkdir(parents=True)
    bak = mc_on_tmp._backup_name("scripts/torn.py")
    owner = mc_on_tmp._owner_name("scripts/torn.py")
    clean = b"x = 1  # PRISTINE\n"
    bak.write_bytes(clean)

    owner.write_text("not-a-pid", encoding="utf-8")
    assert not mc_on_tmp._owner_is_live(owner), (
        "主人文件不是 pid，探针却说他活着")
    assert mc_on_tmp._recover_pending() == 1
    assert victim.read_bytes() == clean, (
        "主人文件损坏 ⇒ 备份永远不被读 ⇒ 源码永久停在变异态")


def test_the_run_group_wiring_keeps_the_owner_then_backup_then_mutant_order():
    """🚨 **顺序钉子，且方向在 2026-10-15 被改正过**（审查 agent P1-2）。

    旧守卫叫 `..._keeps_the_backup_then_owner_then_mutant_order`，
    钉的是 **`备份 → 主人 → 变异`** —— 也就是**危险的那个顺序**。
    它写下的理由是「`.bak` 一落盘就有证据，主人晚一步会被当残留」，
    而它自己正是那个窗口的制造者。

    ## 旧顺序的真实后果（不是「可能」，是必然）

    `_recover_pending` 遍历的是 **`*.bak`** ⇒ **只有 `.bak` 才构成证据**。
    在 `bak.write_bytes` 与 `owner.write_text` 之间起一个 pytest：
    它看到 `.bak`、看不到 `.owner` ⇒ 认成残留 ⇒ 还原并**删掉 `.bak`**；
    随后本进程照样写下 `.owner` 与变异体 ⇒
    **`.owner` 在、主人活着 ⇒ `_owner_is_live` 为真 ⇒ 自愈永远跳过**
    ⇒ **源码永久冻结在变异态，且没有任何东西会喊。**

    ## 新顺序为什么无窗口

    `主人 → 备份 → 变异`。`.owner` 早于 `.bak` 存在是**无害的**：
    没有 `.bak` 就没有证据，`_recover_pending` 压根不会遍历到它。
    ⇒ 任何时刻的磁盘状态要么「无证据」，要么「证据 + 主人都在」，
    **不存在「有证据而主人还没写」的中间态。**
    """
    import inspect as _inspect
    import mutation_check as mc

    src = _inspect.getsource(mc.run_group)
    i_owner = src.index("owner.write_text(")
    i_bak = src.index("_write_backup_atomic(bak, backup)")
    i_mutate = src.index("src.write_text(original.replace(old, new, 1)")
    assert i_owner < i_bak < i_mutate, (
        f"顺序是 主人@{i_owner} / 备份@{i_bak} / 变异@{i_mutate}，"
        "必须严格递增（备份先于主人会打开一个永久冻结的窗口）")


def test_the_owner_file_is_dropped_when_the_backup_is_released():
    """还原之后主人文件必须一起删 —— 否则下一轮自愈会拿一个过期的 pid 说话。"""
    import inspect as _inspect
    import mutation_check as mc

    src = _inspect.getsource(mc.run_group)
    i_restore = src.index("src.write_bytes(backup)")
    i_drop_bak = src.index("bak.unlink(missing_ok=True)")
    i_drop_owner = src.index("owner.unlink(missing_ok=True)")
    assert i_restore < i_drop_bak < i_drop_owner


# ── 接线层：每个会动真源码的测试都必须登记备份 ──────────────────────────

# ★ 判据只认「独立的 ROOT 标识符」：`_ROOT` 里没有词边界，不会被误收，
#   而 `tmp_path` 是实打实的合成树信号。
_ROOT_TOKEN = re.compile(r"(?<![A-Za-z0-9_])ROOT(?![A-Za-z0-9_])")
_WRITE_METHODS = ("write_text", "write_bytes", "unlink", "rename", "open", "mkdir")


def _real_script_writers():
    """找出所有**改写仓根下真实文件**的测试函数。

    ★ 第一版判据按「函数体里有没有 tmp_path」来排除合成树 ——
      结果把两处真阳性全滤掉了（它们都把 tmp_path 当 fixture 参数），
      名单恒为空 ⇒ **恒真守卫**。M5 变异（摘掉备份登记）没有把它变红，
      正是暴露这一点的那一发。〔见 MISTAKE_LOG 第 14 种形状〕
    ⇒ 正确判据在**绑定表达式**上，不在整个函数上：
      写出方法的接收者，其绑定表达式引用了独立的 `ROOT` 且不引用 tmp_path。
    """
    found = []
    for f in sorted((ROOT / "tests").rglob("*.py")):
        src = f.read_text(encoding="utf-8")
        try:
            tree = ast.parse(src)
        except SyntaxError:                                       # pragma: no cover
            continue
        lines = src.splitlines()
        for fn in [n for n in ast.walk(tree)
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            binds = {}
            for n in ast.walk(fn):
                if isinstance(n, ast.Assign):
                    seg = ast.get_source_segment(src, n.value) or ""
                    for t in n.targets:
                        if isinstance(t, ast.Name):
                            binds[t.id] = seg
            fn_seg = "\n".join(lines[fn.lineno - 1:fn.end_lineno or fn.lineno])
            for n in ast.walk(fn):
                if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                        and n.func.attr in _WRITE_METHODS):
                    continue
                recv = n.func.value
                if not isinstance(recv, ast.Name):
                    continue
                bound = binds.get(recv.id, "")
                if _ROOT_TOKEN.search(bound) and "tmp_path" not in bound:
                    found.append((f, fn.name, fn_seg, recv.id, bound))
    return found


def test_every_test_that_mutates_a_real_script_registers_a_disk_backup():
    """★ 这条守卫存在的理由：把「绕过自愈网」这个动作**本身**变成会红的。

    现状（2026-10-15 全仓 AST 审计）：只有 2 处会改写仓根下的真实文件 ——
    `test_factor_attribution.py`（`scripts/factor_attribution.py`）与
    `test_mutation_restore.py`（`scripts/ic_guided_race.py`），两处都已登记 `.bak`。
    ⇒ 判据不是「某个文件里有 `_backup_name`」，
    而是**凡动真源码者必登记** —— 新增一处漏网的当场红。
    """
    offenders = []
    for f, name, fn_seg, recv, bound in _real_script_writers():
        if "_backup_name" not in fn_seg:
            offenders.append(f"{f.relative_to(ROOT)}::{name}（写 {recv} = {bound}）")

    assert not offenders, (
        "这些测试改写真实仓里的文件却没登记磁盘备份 ⇒ "
        f"被 SIGKILL 时没有任何东西能还原：{offenders}")


def test_the_detector_itself_is_not_vacuous():
    """★ 防空真守卫：探测器必须**真的能检出东西**。

    ⚠️ 第一版探测器恒返回空名单（判据滤掉了全部真阳性），
    而守卫照样全绿 —— 与第 14 种形状同形。
    ⇒ 判据：**名单里必须有那两处已知真阳性**。
    名单变空不是「变干净了」，是探测器坏了。
    """
    names = {f"{f.relative_to(ROOT)}::{name}" for f, name, *_ in _real_script_writers()}
    assert names == {
        "tests/test_factor_attribution.py::"
        "test_cutting_the_data_root_wiring_makes_the_guard_go_red",
        "tests/test_mutation_restore.py::"
        "test_recovers_source_left_mutated_by_a_previous_killed_run",
    }, (
        f"真阳性名单变了：{sorted(names)}。"
        "新增 = 有人开始动真源码（须同时登记备份）；"
        "清空 = 探测器本身坏了（比前者更危险）"
    )


def test_the_backup_is_registered_before_the_mutant_is_written():
    """顺序钉子：备份必须**先于**变异写入。

    反了就等于把变异态当「原件」备份下来，一次意外被固化成永久改动。
    """
    src = (ROOT / "tests" / "test_factor_attribution.py").read_text(encoding="utf-8")
    i_backup = src.index("bak.write_bytes(original.encode")
    i_mutate = src.index("src.write_text(mutated")
    assert i_backup < i_mutate, (
        f"备份在第 {i_backup} 串、变异写入在第 {i_mutate} 串 ⇒ 顺序反了")


def test_the_mutation_test_removes_its_backup_only_after_restoring():
    """还原必须早于删备份 —— 反了等于删掉了唯一的还原凭据。"""
    src = (ROOT / "tests" / "test_factor_attribution.py").read_text(encoding="utf-8")
    i_restore = src.index("src.write_text(original, encoding=\"utf-8\")")
    i_drop = src.index("bak.unlink(missing_ok=True)")
    assert i_restore < i_drop, "备份先被删、源文件后还原 ⇒ SIGKILL 落在中间就再也还原不回来"


# ═════════════════════ 整轮互斥 + 原子备份（2026-10-15，审查 agent P0-1 / P1-4）═════════════════════
def test_two_concurrent_runs_cannot_both_enter_the_mutation_zone():
    """🚨 **P0-1**：两个并发的 `mutation_check` 会把仓永久留在变异态。

    病根不是「少了锁」这么简单，是**备份把当前磁盘内容当成了原件**：

        A 备份**原件** → A 写变异体 A
        B 启动，读到的 `src` **已经是 A 的变异体** → B 把它当「原件」备份
        A 先收工：还原成**原件**
        B 后收工：还原成 **A 的变异体**
        ⇒ 仓里留下一个**永久变异体**，所有守卫全绿，
          之后每个测量都跑在改坏的代码上，而**没有任何东西会喊**。

    ⇒ 修法是 `O_CREAT|O_EXCL` 的整轮互斥锁，
      **不许**用 `if lock.exists()`（那是 check-then-act，两边可同时通过）。
    ⇒ 本守卫用**真行为**而不是子串：真取一次锁，真撞一次。
    """
    import mutation_check as mc
    with tempfile.TemporaryDirectory() as td:
        lock = Path(td) / ".run.lock"
        mc.acquire_lock(lock)
        try:
            with pytest.raises(mc.MutationLockBusy):
                mc.acquire_lock(lock)
            # 正控：锁里确实写着占用者的 pid
            assert lock.read_text(encoding="utf-8").strip() == str(os.getpid())
        finally:
            mc.release_lock(lock)
        # 释放之后可以再取
        mc.acquire_lock(lock)
        assert lock.exists()
        mc.release_lock(lock)
        assert not lock.exists(), "释放后锁文件必须消失"


def test_releasing_the_lock_never_steals_someone_elses_lock():
    """删掉别人的锁 = 让第三个人也能进来 ⇒ 并发窗口重新打开。

    这与 `aplus/write_lock.py::release` 是同一条纪律，且
    **`_recover_pending` 是每个 pytest 会话都会走的路** ——
    一个偷锁的自愈会让真正的持有者以为自己还 exclusive。
    """
    import mutation_check as mc
    with tempfile.TemporaryDirectory() as td:
        lock = Path(td) / ".run.lock"
        lock.write_text("999999", encoding="utf-8")      # 别人的 pid
        mc.release_lock(lock)
        assert lock.exists(), "释放了一个不属于本进程的锁 ⇒ 互斥形同虚设"


def test_the_lock_is_taken_before_self_heal_and_released_after():
    """顺序钉子：`main()` 必须**先取锁再自愈**，且用 `finally` 释放。

    锁晚于自愈取 ⇒ 自愈会和另一个进程正在安放的变异体抢同一批 `.bak`。
    不用 `finally` ⇒ 任何异常/SIGTERM 都会把锁留在原地，
    而下一次任何变异检查都会被挡住，报错还会让人误以为「有人正在跑」。
    """
    import inspect as _inspect
    import mutation_check as mc
    src = _inspect.getsource(mc.main)
    i_lock = src.index("acquire_lock()")
    i_recover = src.index("_recover_pending()")
    i_finally = src.index("finally:")
    i_release = src.index("release_lock()", i_finally)
    assert i_lock < i_recover, "自愈必须在锁之后"
    assert i_finally < i_release, "锁必须在 finally 里释放"


def test_the_backup_is_written_atomically_so_sigkill_cannot_truncate_it():
    """🚨 **P1-4**：`bak.write_bytes(...)` 落盘中途被 SIGKILL 时，
    会留下一个**被截断的备份**，而自愈会**照样拿它还原**
    ⇒ 源码被写成半个文件，且没有 traceback。

    ⇒ 解法是 `临时文件 + os.replace`（同文件系统内原子），
      磁盘上只可能出现「完整的旧备份」或「完整的新备份」。
    ⇒ 选它而不是「sha256 校验」：原子写**不改 `.owner` 的格式**，
      不引入新的失败分支 —— 后者会把一个缺陷换成两个。
    """
    import inspect as _inspect
    import mutation_check as mc
    src = _inspect.getsource(mc._write_backup_atomic)
    assert "os.replace(" in src, "备份必须经 os.replace 原子落盘"
    assert ".tmp" in src, "必须先写临时文件"
    # 真行为：写进去的内容必须逐位等于传入的字节，且不留 .tmp
    with tempfile.TemporaryDirectory() as td:
        bak = Path(td) / "x__y.bak"
        data = b"\xe4\xb8\xad\xe6\x96\x87\n" * 1000
        mc._write_backup_atomic(bak, data)
        assert bak.read_bytes() == data, "逐位不一致"
        assert not bak.with_name(bak.name + ".tmp").exists(), "临时文件没清掉"


def test_an_owner_without_a_backup_is_inert_so_the_new_order_has_no_window():
    """新顺序（主人先写）的安全性**建立在这个事实上**：
    只有 `.bak` 才让 `_recover_pending` 动手 ⇒ 孤立的 `.owner` 无害。

    ⚠️ 这条同时是**旧顺序危险性的证明**：旧顺序下若主人晚写，
    那个窗口里的状态恰恰是「有 `.bak`、无 `.owner`」——
    也就是**有证据、无主人**，自愈必然把它当残留。
    """
    import mutation_check as mc
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "src"
        root.mkdir()
        (root / "a.py").write_text("CLEAN = 1\n", encoding="utf-8")
        orig_backup_dir = mc.BACKUP_DIR
        orig_root = mc.ROOT
        try:
            mc.BACKUP_DIR = root / ".mutation_backup"
            mc.ROOT = root
            mc.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            mc._owner_name("a.py").write_text(str(os.getpid()), encoding="utf-8")
            assert mc._recover_pending() == 0, "孤立的 .owner 触发了还原 ⇒ 顺序反了会开窗口"
            assert (root / "a.py").read_text(encoding="utf-8") == "CLEAN = 1\n"
            # 对照面：**有 `.bak` 且主人已死 ⇒ 必须还原**
            # （主人不能留成本进程 pid，否则 `_owner_is_live` 会判「在飞」而跳过，
            #   那这条对照就变成恒真 —— 正控必须自己先排除掉那条旁路。）
            mc._owner_name("a.py").write_text("999999", encoding="utf-8")
            mc._backup_name("a.py").write_bytes(b"CLEAN = 1\n")
            assert mc._recover_pending() == 1, "有备份却没还原 ⇒ 自愈坏了"
            assert not mc._backup_name("a.py").exists(), "还原后必须删备份"
        finally:
            mc.BACKUP_DIR = orig_backup_dir
            mc.ROOT = orig_root


def test_the_signal_handler_also_releases_the_lock():
    """🚨 只在 `main()` 的 `finally` 里释放锁**不够**：
    `finally` 挡不住 SIGTERM/SIGINT（那正是 `_install_signal_guards` 存在的理由）。

    ⇒ 锁被留在原地 ⇒ **此后每一次**变异检查都被挡住，
    而那条报错（"锁已被占用"）会让人以为「有人在跑」，
    不会有人想到「上次那个被 kill 了」——
    ⇒ 一个 P0 修成了**永久性拒绝服务**。
    """
    import inspect as _inspect
    import mutation_check as mc
    src = _inspect.getsource(mc._install_signal_guards)
    i_restore = src.index("_clear_pycache(rel)")
    i_release = src.index("release_lock()")
    i_exit = src.index("raise SystemExit(128 + signum)")
    assert i_restore < i_release < i_exit, (
        "信号处理器必须先还原在途源码、再释放锁、最后才退出")


# ══════════════ 身份三证：pid 会被复用，双证封不住（2026-10-15 P1-3 / P1-5）══════════════


def _spawn_fake_owner(tag: str):
    """起一个**真的**活着、argv 里带 `mutation_check` 的进程。"""
    import subprocess as _sp
    return _sp.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)", f"mutation_check.py {tag}"],
        stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)


def test_the_start_token_is_read_under_a_fixed_locale_and_is_byte_stable(mc_on_tmp):
    """★ 正控：第三证本身必须**可用且稳定**，否则主守卫会永远红或永远绿。

    ⚠️ 为什么 locale 必须钉死：实测本机默认 locale 下 `ps -o lstart=`
      印 `四 10/15 07:23:09 2026`，`C` locale 下印 `Thu Oct 15 07:23:09 2026`。
      **存用一套、读用另一套 ⇒ 同一个进程两次读不相等 ⇒ 好主人被判成复用的。**
    ⚠️ 为什么 `etimes=` 不用：实测本机 ps 直接 `rc=1` 且把帮助打到 stdout。
    ⚠️ 为什么 `start=` 不用：实测它只有**分钟**分辨率，同分钟内复用判不出来。
    """
    mc = mc_on_tmp
    tok = mc._proc_start(os.getpid())
    assert tok, "取不到启动时刻 ⇒ 第三证整条塌了，下面主守卫此刻在拿残废的探针判活"
    again = mc._proc_start(os.getpid())
    assert again == tok, (
        f"同一 pid 两次读出不同启动时刻：{tok!r} vs {again!r}\n"
        f"⇒ locale 没钉死，或 ps 输出不稳定 —— 这会让**好进程**被判成 pid 复用")
    # ⚠️ 期望必须按 **C locale** 的形状写（`Thu Oct 15 07:24:48 2026`）。
    #   我第一版写了 `"/" in tok` —— 那是**默认 locale**（中文）的形状，
    #   而中文形状恰恰是这条守卫要防的那个 ⇒ 守卫把自己钉的东西当成了缺陷。
    assert re.fullmatch(
        r"[A-Z][a-z]{2} [A-Z][a-z]{2} +\d+ \d{2}:\d{2}:\d{2} \d{4}", tok), (
        f"lstart 格式异常：{tok!r}。期望 C locale 的 "
        f"'Thu Oct 15 07:24:48 2026'；若拿到的是 '四 10/15 …' "
        f"说明 LC_ALL=C 没生效 ⇒ 存用一套读用另一套会把好主人判成复用的")
    # 分辨率必须到**秒**：`start=` 只有分钟（`7:23AM`），同分钟内复用判不出来。
    assert re.search(r"\d{2}:\d{2}:\d{2}", tok), (
        f"只有分钟分辨率：{tok!r} ⇒ 同一分钟内的 pid 复用判不出来")


def test_a_live_owner_whose_start_token_matches_is_still_skipped(mc_on_tmp, tmp_path):
    """★ 正控：三证**全中** ⇒ 在飞，必须一个字节都不写。

    这条是「守卫没把所有人一律当残留」的证明 ——
    没有它，「全部还原」也能让下面那条 pid 复用守卫变绿。
    """
    mc = mc_on_tmp
    victim = tmp_path / "scripts" / "inflight3.py"
    victim.parent.mkdir(parents=True)
    victim.write_bytes(b"MUTATED\n")
    mc.BACKUP_DIR.mkdir(parents=True)
    mc._backup_name("scripts/inflight3.py").write_bytes(b"PRISTINE\n")
    owner = mc._owner_name("scripts/inflight3.py")

    proc = _spawn_fake_owner("inflight3")
    try:
        owner.write_text(f"{proc.pid}\n{mc._proc_start(proc.pid) or ''}\n",
                         encoding="utf-8")
        assert mc._owner_is_live(owner), (
            "三证全中的活主人被判成死的 ⇒ 在飞的变异体会被还原，"
            "守卫证据作废（这正是 2026-10-14 那场假绿的机制）")
        assert mc._recover_pending() == 0
        assert victim.read_bytes() == b"MUTATED\n"
    finally:
        proc.kill()
        proc.wait(timeout=10)


def test_a_reused_pid_is_not_mistaken_for_its_predecessor(mc_on_tmp, tmp_path):
    """★ 主守卫：**pid 被复用** ⇒ 启动时刻不等 ⇒ 判为残留，必须还原。

    ## 这个洞为什么双证封不住

    pid 是**可复用资源**。上一次 mutation_check 被 SIGKILL 后，
    它的 pid 几周后归了**另一个** mutation_check —— 此时
    「pid 活着」✓、「cmdline 含 mutation_check」✓ ⇒ **双证全过** ⇒
    那份真残留被当成「在飞」**永远跳过**，而仓里没有任何东西会喊。

    🚨 删掉 `pid == os.getpid()` 旁路**也不够**：本进程自己跑
    `_recover_pending()`（`main()` 里自愈先于 `run_group`）时，
    复用到自己头上的 pid 查出来的 cmdline **就是 mutation_check**
    ⇒ 旁路删了照样冻结。⇒ 只有**启动时刻**能封住。

    ⇒ 判据方向是**不等**：pid 复用是真实存在的形态，
      而「启动时刻不相等 ⇒ 一定是别的进程」不需要知道正确答案。
    """
    mc = mc_on_tmp
    victim = tmp_path / "scripts" / "reused3.py"
    victim.parent.mkdir(parents=True)
    victim.write_bytes(b"MUTATED\n")
    mc.BACKUP_DIR.mkdir(parents=True)
    mc._backup_name("scripts/reused3.py").write_bytes(b"PRISTINE\n")
    owner = mc._owner_name("scripts/reused3.py")

    proc = _spawn_fake_owner("reused3")
    try:
        live_start = mc._proc_start(proc.pid)
        assert live_start, "正控失败：拿不到活主人的启动时刻"
        # 伪造：pid 是活的、cmdline 也是对的，**只有启动时刻是别人的**。
        owner.write_text(f"{proc.pid}\nThu Jan  1 00:00:00 1970\n", encoding="utf-8")

        assert not mc._owner_is_live(owner), (
            "pid 被复用却仍被判成原主人 ⇒ 那份真残留永远不会被还原，"
            "且没有任何东西会喊（与「备份了但没人负责读」同族）")
        assert mc._recover_pending() == 1, "真残留没有被还原"
        assert victim.read_bytes() == b"PRISTINE\n"
    finally:
        proc.kill()
        proc.wait(timeout=10)


def test_the_owner_file_actually_carries_the_start_token(mc_on_tmp, tmp_path):
    """接线层：`run_group` 写 `.owner` 时**必须**写进启动时刻。

    ⚠️ 主守卫只验「读的时候用不用第三证」；若写的时候不写，
      磁盘上永远只有旧格式 ⇒ 第三证**对全部现存备份零覆盖**，
      而两条守卫照样全绿 —— 这就是第 18 种形状（守卫守机制、
      失效发生在数据侧）。⇒ 这一条跨过代码与产物之间的那道缝。
    """
    import inspect as _inspect
    import mutation_check as mc
    src = _inspect.getsource(mc.run_group)
    m = re.search(r'owner\.write_text\(\s*f"\{os\.getpid\(\)\}\\n'
                  r'\{_proc_start\(os\.getpid\(\)\)', src)
    assert m, (
        "run_group 写 .owner 时没有带上启动时刻 ⇒ "
        "第三证对全部现存备份零覆盖，而读侧守卫仍会全绿")


def test_the_self_pid_shortcut_is_gone(mc_on_tmp, tmp_path):
    """🚨 P1-5：`.owner` 写着**本进程自己的 pid** 时不得无条件当「在飞」。

    原实现有一句 `if pid == os.getpid(): return True`。它有一个
    **真实**的假阴性方向：上一次 mutation_check 被杀、pid 随后被
    **本进程**复用 ⇒ `_recover_pending()` 判「在飞」⇒ 真残留永不还原。

    ⚠️ 注意本条**只钉住「不许无条件认」**，不要求「一律判死」——
      若 pid 真的是本进程且启动时刻也相等，那**确实**是在飞（自愈前不该有）。
    """
    mc = mc_on_tmp
    victim = tmp_path / "scripts" / "selfpid.py"
    victim.parent.mkdir(parents=True)
    victim.write_bytes(b"MUTATED\n")
    mc.BACKUP_DIR.mkdir(parents=True)
    mc._backup_name("scripts/selfpid.py").write_bytes(b"PRISTINE\n")
    owner = mc._owner_name("scripts/selfpid.py")
    # 伪造：本进程 pid + **别人的**启动时刻 ⇒ 必须是残留
    owner.write_text(f"{os.getpid()}\nThu Jan  1 00:00:00 1970\n", encoding="utf-8")
    assert not mc._owner_is_live(owner), (
        "pid == os.getpid() 的无条件旁路还在 ⇒ pid 复用回本进程时，"
        "真残留会被永久跳过")
    assert mc._recover_pending() == 1
    assert victim.read_bytes() == b"PRISTINE\n"


def test_a_legacy_owner_without_a_start_token_is_not_recovered_as_residue(
        mc_on_tmp, tmp_path):
    """旧格式（单行纯 pid）**不得**因为「拿不到第三证」而被当残留。

    理由写在 `_owner_is_live` 的 docstring 里：那份 `.owner` 可能正是
    **这一轮正在飞的**变异体，还原它就是 2026-10-14 那场假绿。

    ⚠️ 夹具形状必须是**真实链路里会出现的那种**：主人是一个
      **argv 含 `mutation_check` 的活进程**。
      🚨 我第一版写的是 `owner.write_text(str(os.getpid()))` ——
      那是 pytest 自己的 pid，而 **pytest 永远不是写 `.owner` 的那个进程**
      （`run_group` 在 mutation_check 里写，pytest 是它的子进程 ⇒ pid 必不相同；
      `main()` 里的 `_recover_pending` 早于任何备份落盘）。
      ⇒ 那个形状只可能来自 **pid 复用**，而 pid 复用**判死才是对的**。
      我拿一个不存在于生产的形状去断言，得到的红是**夹具的错**，
      而报告长得与「守卫有洞」一模一样（第 12 种形状）。

    ⚠️ 代价要说清：旧格式走的是**双证**，第三证对它零覆盖 ——
      这是「守卫是时间局部的」又一个实例，不是缺陷。
    """
    mc = mc_on_tmp
    victim = tmp_path / "scripts" / "legacy.py"
    victim.parent.mkdir(parents=True)
    victim.write_bytes(b"MUTATED\n")
    mc.BACKUP_DIR.mkdir(parents=True)
    mc._backup_name("scripts/legacy.py").write_bytes(b"PRISTINE\n")
    owner = mc._owner_name("scripts/legacy.py")

    proc = _spawn_fake_owner("legacy")
    try:
        owner.write_text(str(proc.pid), encoding="utf-8")   # 旧格式：单行纯 pid
        assert mc._owner_is_live(owner), (
            "旧格式 .owner 被当成残留 ⇒ 在飞的变异体会被还原"
            "（本组守卫存在的理由：2026-10-14 那场假绿）")
        assert mc._recover_pending() == 0
        assert victim.read_bytes() == b"MUTATED\n"
    finally:
        proc.kill()
        proc.wait(timeout=10)


def test_the_ps_degradation_is_announced_rather_than_silent(mc_on_tmp, tmp_path, capsys):
    """🚨 P1-3：`ps` 不可用时**必须喊**，不许静默退回单证。

    与 `provenance` 的「依据：字节证据 / ⚠️ 仅 commit 走查」同一条纪律：
    **两种强度差一个量级的证据，不许长得一样。**
    静默降级的后果不是崩溃，是那条守卫在某台机器上悄悄只剩一个证据，
    而它的报告与正常时一模一样。
    """
    import subprocess as _sp

    mc = mc_on_tmp
    victim = tmp_path / "scripts" / "nops.py"
    victim.parent.mkdir(parents=True)
    victim.write_bytes(b"MUTATED\n")
    mc.BACKUP_DIR.mkdir(parents=True)
    mc._backup_name("scripts/nops.py").write_bytes(b"PRISTINE\n")
    owner = mc._owner_name("scripts/nops.py")

    proc = _spawn_fake_owner("nops")
    try:
        # ★ 旧格式（无启动时刻）⇒ 跳过第三证，才会走到 cmdline 那一处。
        #   🚨 我第一版给的是**新格式**，并把 `subprocess.run` 整个替换掉 ——
        #   于是 `_proc_start` 里的 `ps` 也被打掉、第三证返回 `None`
        #   ≠ 记录值 ⇒ **提前 return False**，降级分支根本没被执行到。
        #   ⇒ 探针打在了错误的层，报出来的红是**探针的错**（第 14 种形状）。
        owner.write_text(str(proc.pid), encoding="utf-8")

        real_run = mc.subprocess.run

        def only_cmdline_fails(*a, **k):
            if a and isinstance(a[0], (list, tuple)) and "command=" in a[0]:
                raise FileNotFoundError("ps")
            return real_run(*a, **k)

        try:
            mc.subprocess.run = only_cmdline_fails
            alive = mc._owner_is_live(owner)
        finally:
            mc.subprocess.run = real_run
        err = capsys.readouterr().err
        assert alive, "ps 不可用时方向应保守（宁可跳过，不可误删备份）"
        assert "降级" in err and owner.name in err, (
            f"ps 不可用时**一声不吭**（或没点名是哪份备份）⇒ 这条守卫在某台机器上"
            f"只剩 pid 单证，而报告与正常时一模一样。stderr 实际内容：{err!r}")
    finally:
        proc.kill()
        proc.wait(timeout=10)

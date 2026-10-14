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
import re
import sys
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

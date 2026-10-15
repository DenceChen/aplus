"""变异台**自己**的守卫：只有 `rc == 1` 才算「守住了」。

## 为什么这条文件存在（第 121 层）

仓里每一个 `✅ S<n> 被杀` 都建立在**变异台把退出码分层**这一行上，
而这一行此前零覆盖。2026-10-15 实测它怎么坏的：

    caught = rc != 0        # ← S1 的变异体是顶格 `if False:` ⇒ IndentationError

    rc = 2（收集错误，被测文件压根解析不了）⇒ 被记成 ✅

于是「`train_spread_and_ic` 有守卫」这句话是**假**的 ——
守卫一条都没跑过。把它改成合法语义变异后它**存活**（24 条全绿），
才暴露出那个函数**零覆盖**。

## 这里的证据全部是**真子进程**

不许用 stub 假装 pytest 返回某个 rc：那样测的是「我 mock 的那个数」，
而要钉的恰恰是**pytest 真的会吐出哪个码**。所以下面三条都真的起
`.venv/bin/python -m pytest` 跑一个临时 node 文件 —— 单条约 1 秒。

⇒ 与第 118 层同源：**读落盘 json 的守卫与代码毫无关系**；
在这里，「stub 一个 rc 的守卫」与「变异探针打在自己副本上」同族。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_harness():
    p = ROOT / "scripts/mutation_harness.py"
    spec = importlib.util.spec_from_file_location("mutation_harness", p)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["mutation_harness"] = mod          # 让 dataclass/NamedTuple 正常
    spec.loader.exec_module(mod)
    return mod


mh = _load_harness()


def _scratch(tmp_path: Path) -> tuple[Path, Path]:
    """一份被变异的源 + 一个真的 pytest node（都在 tmp，**绝不碰仓库源码**）。"""
    src = tmp_path / "src.py"
    src.write_text("def value():\n    return 1\n", encoding="utf-8")
    node = tmp_path / "test_scratch_node.py"
    node.write_text(
        "import importlib.util\n"
        f"_P = {str(src)!r}\n"
        "def _load():\n"
        "    spec = importlib.util.spec_from_file_location('scratch_src', _P)\n"
        "    mod = importlib.util.module_from_spec(spec)\n"
        "    spec.loader.exec_module(mod)\n"
        "    return mod\n"
        "def test_the_value_is_one():\n"
        "    assert _load().value() == 1\n",
        encoding="utf-8",
    )
    return src, node


def _spec(label: str, rel: Path, old: str, new: str) -> mh.Spec:
    """拼一条 spec，**显式给全缩进**。

    ⚠️ `load_specs` 会按**最小**缩进 dedent；这里绕过文件解析直接构造，
      是为了让「变异体长什么样」在测试里一目了然 —— 语法炸弹那个
      正是必须看得出「第二行顶格」的那种。
    """
    return mh.Spec(label, str(rel), old, new, "killed")


# ── 三条真实退出码，构成完整的判据分层 ─────────────────────────────

def test_a_same_length_mutation_is_still_observed_when_a_stale_bytecode_cache_exists(tmp_path, capsys):
    """★ 主守卫（第 121 层第四入口）：**字节变了 ≠ 被测对象变了**。

    变异体 `return 1` → `return 2`，**长度不变**；同秒写入。
    Python 的 `.pyc` 校验键是 **(源文件 mtime, 大小)**，两者都没变
    ⇒ 缓存命中 ⇒ 按路径 `importlib` 加载执行的是**旧代码**
    ⇒ **整条变异静默变成空操作**（实测 `1 passed`）。

    ⚠️⚠️ **本条刻意先手工造出一份陈旧 `.pyc`**，而不是靠变异跑自己写出来。
      原因是一次**「红的原因不对」**：先给 `_pytest` 加了
      `PYTHONDONTWRITEBYTECODE=1` 之后，变异跑**根本不再写缓存**，
      于是 `drop_bytecode_cache()` 在变异跑里**用不上了** ——
      当时 H2 变异（摘掉清缓存）**存活**，而 K1 仍是绿的：
      **它已经抓不到自己声称要抓的那个危险了。**

      而真实危险在**变异跑之外**：仓里任何一次普通
      `.venv/bin/python -m pytest tests/` 都会写 `.pyc`
      （那一跑**没有**这个环境变量）⇒ 被测脚本**本来就有**陈旧缓存。
      ⇒ 守卫必须自己把那个前提造出来，否则它验的是「变异跑内部自洽」。
    """
    import py_compile

    src, node = _scratch(tmp_path)
    py_compile.compile(str(src), doraise=True)      # ← 野生前提：陈旧缓存已存在
    assert list((src.parent / "__pycache__").glob(f"{src.stem}.cpython-*.pyc")), \
        "正控失败：没造出陈旧缓存，本条就只是在测变异跑内部的自洽"

    spec = _spec("K1 同长度 + 陈旧缓存", src, "    return 1\n", "    return 2\n")

    ok, total = mh.run([spec], str(node))

    assert (ok, total) == (1, 1), (
        "rc=1 的变异没被计成 killed ⇒ 陈旧 `.pyc` 把它吃了。"
        "『字节变了』不等于『被测对象变了』"
    )
    assert "✅" in capsys.readouterr().out


def test_the_bytecode_cache_of_the_mutated_file_is_actually_dropped(tmp_path):
    """钉住那条清理动作本身：写入变异体后，它的 `.pyc` 必须**不在**。

    ⚠️ 不许改成「清掉整个 `__pycache__` 目录」——那是**递归删除**，
      而 `__pycache__` 里可能躺着本仓其它模块的缓存；
      本函数只按 `<stem>.cpython-*.pyc` 精确匹配被测文件。
    """
    src, node = _scratch(tmp_path)
    # 先造出一条真实的 .pyc。⚠️ **不能靠 `mh._pytest`**：
    # 它带 `PYTHONDONTWRITEBYTECODE=1`（第 121 层修法之一，就是不让变异跑留缓存），
    # 所以那条路**故意**不写 pyc ⇒ 用它当正控会拿到「没有缓存」这个假阴性。
    import py_compile
    py_compile.compile(str(src), doraise=True)
    cache = src.parent / "__pycache__"
    assert list(cache.glob(f"{src.stem}.cpython-*.pyc")), \
        "正控失败：被测文件压根没产出字节码缓存，后面的断言无意义"

    mh.drop_bytecode_cache(src)

    assert not list(cache.glob(f"{src.stem}.cpython-*.pyc")), \
        "被测文件的 .pyc 没被清掉 ⇒ 同长度变异会被缓存吃掉"
    # 反控：同目录里**别的**文件的缓存不许被动
    other = src.parent / "other.py"
    other.write_text("x = 1\n", encoding="utf-8")
    py_compile.compile(str(other), doraise=True)

    mh.drop_bytecode_cache(src)

    assert (other.parent / "__pycache__" / f"{other.stem}.cpython-313.pyc").exists(), \
        "清理波及了**别的**模块的缓存 ⇒ 那是一条会递归删目录的守卫"


def test_a_mutant_that_cannot_even_be_parsed_is_not_credited_as_killed(tmp_path, capsys):
    """★ 主守卫（第 121 层）：**语法炸弹不得获得信用**。

    变异体第二行顶格、且没有函数体 ⇒ `IndentationError`。

    ⚠️ 这里量到过一个**对称的坑**，所以守卫必须钉两层而不是一层：
      同一个语法炸弹，pytest 在**收集期** import 被测模块 ⇒ `rc=2 / 1 error`；
      而 node 在**测试函数体内** import 它 ⇒ **`rc=1 / 1 failed`** ——
      **与「守卫真的抓到了缺陷」逐位相同**。
      ⇒ 只靠 rc 分层抓不住第二种；正解是**跑 pytest 之前**就
        `compile()` 掉它（本文件的 node 刻意用函数体内 import，
        正是为了钉住「rc 分层不够」这一点）。
    """
    src, node = _scratch(tmp_path)
    spec = _spec("K2 语法炸弹", src, "    return 1\n", "    return 1\nif False:\n")

    ok, total = mh.run([spec], str(node))

    out = capsys.readouterr().out
    assert "解析不了" in out, \
        f"变异体压根不是合法 Python，台子却去跑测试并可能记成 killed：{out[-500:]}"
    assert "不可验证" in out, \
        "必须**明说**是「不可验证」并印出原因，不许读者自己脑补"
    assert "rc=" not in out, \
        "语法非法的变异体**根本不该跑 pytest** —— 跑出来的红与守卫无关"
    assert (ok, total) == (0, 1), \
        "语法炸弹被记成了「守住了」⇒ 这正是第 121 层的失效本身"


def test_a_valid_mutant_that_breaks_collection_is_still_not_credited(tmp_path, capsys):
    """第二层：变异体**语法合法**，但被测模块在**收集期**炸 ⇒ `rc=2`。

    这条是 compile 检查**抓不到**的形态 —— 它必须由 rc 分层兜住。
    两条守卫缺一不可：一个抓「不是 Python」，一个抓「是 Python 但跑不起来」。
    """
    src, node = _scratch(tmp_path)
    # node 改成**模块级** import ⇒ 收集期就会执行
    node.write_text(
        "import importlib.util as _u\n"
        f"_S = _u.spec_from_file_location('scratch_src', {str(src)!r})\n"
        "_M = _u.module_from_spec(_S)\n"
        "_S.loader.exec_module(_M)\n"
        "def test_v():\n"
        "    assert _M.value() == 1\n",
        encoding="utf-8",
    )
    # 语法**完全合法**（所以 compile 检查放行），但模块级就抛 ⇒ 收集期 rc=2。
    spec = _spec("K5 合法语法但收集期失败", src,
                 "def value():\n    return 1\n",
                 "raise RuntimeError('boom')\n")

    ok, total = mh.run([spec], str(node))

    out = capsys.readouterr().out
    assert "rc=2" in out, f"本该是收集期错误（rc=2），实际输出：{out[-500:]}"
    assert (ok, total) == (0, 1), \
        "收集错误被记成了「守住了」"


def test_a_semantic_mutant_nobody_guards_is_reported_as_surviving(tmp_path, capsys):
    """反控：变异**生效了**（字节变了、行为变了）而没有守卫 ⇒ 必须报存活。

    ⇒ 这一条同时钉住「变异确实落进了文件」：
      如果 `old` 匹配 0 处、哈希没变，台子会打 `❌ 旧串匹配 0 处`
      而**不会**走到 rc 判定 —— 那两条失败消息是分开的。
    """
    src, node = _scratch(tmp_path)
    # 加一个没人看的局部变量：行为真的变了（新增一条语句），
    # 而 `test_the_value_is_one` 照样通过 ⇒ rc=0 ⇒ 必须记成 survived。
    spec = _spec("K3 无人守的语义变异", src, "    return 1\n",
                 "    _unused = 99\n    return 1\n")

    ok, total = mh.run([spec], str(node))

    out = capsys.readouterr().out
    assert "旧串匹配 1" not in out and "哈希没变" not in out, \
        f"变异压根没落进文件，这条测试就只是在测 stub：{out[-400:]}"
    assert "rc=0" in out, f"本该 rc=0（无人守），实际：{out[-400:]}"
    assert (ok, total) == (0, 1)


def test_a_red_baseline_voids_every_verdict_including_a_real_kill(tmp_path, capsys):
    """基线红 ⇒ **一条都不许计分**，哪怕它确实是 rc=1。

    这与上面三条是同一个道理的两端：变异台只问「变异后红了吗」，
    从不问「变异前绿吗」。恒红的守卫会给每条真变异发免费信用。
    """
    src, node = _scratch(tmp_path)
    # 把 node 改成**未变异时就红**（断言 2，而源码返回 1）
    node.write_text(
        "import importlib.util\n"
        f"_P = {str(src)!r}\n"
        "def _load():\n"
        "    spec = importlib.util.spec_from_file_location('scratch_src', _P)\n"
        "    mod = importlib.util.module_from_spec(spec)\n"
        "    spec.loader.exec_module(mod)\n"
        "    return mod\n"
        "def test_baseline_is_red_on_purpose():\n"
        "    assert _load().value() == 2\n",
        encoding="utf-8",
    )
    spec = _spec("K4 真失败但基线已红", src, "    return 1\n", "    return 2\n")

    ok, total = mh.run([spec], str(node))

    assert (ok, total) == (0, 1), "基线红时仍给了分"
    assert "基线就红" in capsys.readouterr().out


def test_the_source_file_is_restored_byte_for_byte_after_every_spec(tmp_path):
    """变异台必须把被测文件**逐位还原** —— 否则它自己就是形状 113。

    三条 spec 跑在同一份 `src.py` 上，末态必须与初态逐位相同。
    """
    src, node = _scratch(tmp_path)
    before = src.read_bytes()
    specs = [
        _spec("R1", src, "    return 1\n", "    return 2\n"),
        _spec("R2", src, "    return 1\n", "    return 1\nif False:\n"),
        _spec("R3", src, "    return 1\n", "    _unused = 99\n    return 1\n"),
    ]

    mh.run(specs, str(node))

    assert src.read_bytes() == before, "被测文件没被逐位还原"

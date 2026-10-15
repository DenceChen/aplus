"""变异台：一次性变异验证 + **基线正控**。

## 为什么必须先验基线（2026-10-15，本段亲历）

一次变异台跑出 `✅ M3 被杀` 看着是好事。实际那次是**假 credit**：
被挂上去的那条守卫在**未变异时就已红**，所以每条变异都必然让它红，
于是每条都记成「被杀」。我据此写下「M3 被抓到了」——**完全错误**。

    变异台只问「变异后红了吗」，从不问「变异前绿吗」。
    ⇒ 一条恒红的守卫会被无限计成「守住了」，
      而且比「恒绿」更危险：恒绿至少在某个别的机制里会露头，
      恒红则让每一条**真**变异都获得免费信用，检验能力被彻底稀释。

**降低 K 是提速，砍掉正控臂是作弊。** 野自助守卫只有零假设臂没有正控臂
这条病，在这里长成了测试侧的样子。

纪律（每条变异必须四项全中，缺一则该条的绿/红都不作数）：

    ① 基线绿     变异前这一组必须 rc=0
    ② 旧串命中 1 处（多处 ⇒ 锚点不唯一，改了什么不确定）
    ③ 哈希真的变了（写入后源码逐位不同）
    ④ 还原后逐位一致

⚠️ **上面四项全在验「变异正确地落到了文件里」，
一条都没验「落进去的那段代码本身是不是合法」** —— 第 121 层。

另有一条**独立于跑测试**的前置检查：**变异体必须能被解析**
（`.py` 走 `compile()`；`.html`/`.js` 走**逐块 `node --check`**；
`.json` 走 `json.loads`；无解析器的后缀会**印出来**并放行）。
理由是实测出来的对称性：同一条语法非法的变异，
pytest 在收集期 import 它给 `rc=2 / 1 error`，
在函数体内 import 它给 **`rc=1 / 1 failed`** ——
**与「守卫真的抓到了缺陷」逐位相同。**
⇒ 光靠 rc 分层抓不住后者；只能在跑之前挡住。

🚨🚨 **这条闸门曾经写死 `compile()`，于是对 JavaScript 目标恒假**（2026-10-15 实测）：
第一次变异 `aplus/web/index.html` 时四条**全部**报「解析不了 ⇒ 不可验证」，
而那不是变异体坏了，是**闸门用错了语言**。
⇒ 而它报的那句「不可验证」与「证据作废」**逐位同形**
⇒ **恒为假的闸门比没有闸门更贵**：它把「没检查」说成「检查失败」，
人于是会去改规格（冤枉规格）或去改本来正确的代码（第 112 层通则）。
⇒ 修法不是「Python 之外一律放行」（那是恒真的闸门，一样有害），
是**逐个后缀问「它有没有解析器」，有就用真的那个**。

还有一条判据分层：**只有 `rc == 1` 才算被杀**
（rc∈{2,3,4,5} = 收集/用法/内部错误/中断 ⇒ 不可验证，按存活计入上界）。

用法：

    .venv/bin/python scripts/mutation_harness.py --node tests/xxx.py 变异规格.py

## 🚨 运行期间不许并发跑别的 pytest（2026-10-15 实测踩到）

本台**不是只读实验**：每一轮它都在**真实改写被测文件**，然后才跑 pytest、再还原。
⇒ **任何与它并发的、会 import 那个模块的进程，读到的是变异体，不是源码。**

当天实录：本台在跑 `factor_attribution.py` 的 15 条变异时，
另一个 agent 正在审计 `tests/test_cap_exposure.py`（它 import 那个模块），
**我直到台子已经开跑才想起这件事**，只能事后去打招呼「你看到的红可能是我的实验」。

⇒ 这个失效方向的**危险在于它伪装成缺陷**：并发跑出来的那条红**是真的红**
（代码当时确实是错的），只是「错」是台子制造的 ⇒
人会把它归因成「仓里有个 bug」，然后去**修一段本来正确的代码**（第 112 层通则）。

⇒ 与第 125 层（长跑期间改生成器 ⇒ 章盖上没跑过的字节）**同族、方向相反**：
125 是「跑长任务时别改它的生成器」，本条是「跑变异台时别跑别的测试」。
**同一个错误的两个时标。**

⇒ 处置是**排程**，不是加检查（此刻没有便宜可靠的锁能测「有没有人在 import 它」）：
**变异台运行期间不并发跑门禁、不并发起审计 agent**；
已经并发跑出来的结果**一律重跑**，并在报告里写明第一次可能受污染。
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parent.parent
PY = str(ROOT / ".venv/bin/python")


class Spec(NamedTuple):
    label: str
    rel: str          # 被变异的文件（相对 ROOT）
    old: str          # 锚点，必须在 rel 里恰好命中 1 次
    new: str          # 变异体
    expect: str       # killed = 期望变红；survived = 期望仍绿


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def drop_bytecode_cache(p: Path) -> None:
    """清掉被测文件的 `__pycache__` 条目 —— 🚨 第 121 层的第二个入口。

    实测（2026-10-15）：把 `return 1` 改成 `return 2`（**长度不变**、
    同一秒内写入），文件哈希确实变了，而 `importlib` 按路径加载时
    **仍然执行旧的 `.pyc`** ⇒ 变异对被测模块**等于没发生**。

    ⇒ 「字节变了」**不等于**「被测对象变了」。
      Python 的 pyc 校验键是 (源文件 mtime, 大小)，两者都没变 ⇒ 缓存命中。

    本仓的测试**大量**用 `spec_from_file_location` 按路径加载脚本，
    所以这不是假想：一条同长度的变异会静默变成空操作。

    ⚠️ 失效方向说明：这种情况台子报的是**存活**（保守方向，不会给假信用），
      但它会让人去「修一条本来正确的守卫」—— 那是最贵的错误方向。
    """
    cache = p.parent / "__pycache__"
    if not cache.is_dir():
        return
    for c in cache.glob(f"{p.stem}.cpython-*.pyc"):
        try:
            c.unlink()
        except OSError:
            pass


def _pytest(node: str) -> tuple[int, str, str]:
    r = subprocess.run(
        [PY, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider", node],
        cwd=ROOT, capture_output=True, text=True, timeout=3600,
        # 🚨 绝不让这一跑在仓里留下 .pyc（第 121 层）：
        #   字节码缓存的校验键是 (源文件 mtime, 大小)，一次变异跑会在
        #   同一秒内反复写同一个文件 ⇒ 缓存命中 ⇒ 后续任何按路径
        #   import 该脚本的测试都会执行**旧代码**。
        #   （`drop_bytecode_cache` 清的是**已存在**的缓存；这里断掉**新写**的。）
        #   与老台子 `mutation_check.py::_run` 的做法保持一致。
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    tail = [x for x in r.stdout.splitlines() if x.strip()]
    return r.returncode, (tail[-1] if tail else "(无输出)"), r.stdout


def _failed_names(out: str) -> list[str]:
    return sorted({l.split("::")[1].split()[0]
                   for l in out.splitlines() if l.startswith("FAILED ")})


def load_specs(path: Path) -> list[Spec]:
    """变异规格是一段**文字**：

        标签<TAB>相对路径
        <缩进的锚点原文>
        === 变异体 ===
        <缩进的变异体原文>

    刻意用纯文本而不是 Python 模块：规格里的变异体常常就是**语法非法的**
    源码片段，import 进来会让整个台子在启动时就崩。

    🚨🚨 **两条只能靠「规格写对」才能过的硬限制**（2026-10-15 实测，
    两条都把台子输出变成**误导性**的，所以写在这里 ——spec 作者唯一会读的地方）：

    ① **锚点只认得动「第 0 列」的写法。**
      上面 `dedent` 的 pad 取的是**块内所有非空行的最小缩进**，
      所以一个整体缩进 4 格（函数体）或 8 格的锚点会被拽成 0 格 / 4 格，
      而 `src.count(old)` 是**子串**匹配 ⇒ 恰好还能命中源码里那行。
      ⚠️ 但这意味着**锚点必须比源码缩进少（或相等）**：
      源码 8 格、锚点写 12 格 ⇒ pad 取到 8 ⇒ 拽成 4 格 ⇒ 仍命中（少）；
      源码 4 格、锚点写 0 格 ⇒ 拽成 0 ⇒ 也命中（等）；
      **真正匹配不上的只有「锚点首行比源码缩进多」**（第 122 层那两次）。
      ⚠️ 想改成「首行相对缩进」**不可行**：
      `tests/mutation_specs/test_spread_tilted_arm.mut:S4` 实测依赖 min 语义，
      改了会当场打破一条既有规格 ⇒ 保持 min，别动它。

    ② **模块级 `assert` 会把整条变异变成 rc=2（不可验证），而不是 rc=1。**
      例：`tests/test_factor_attribution.py` 顶层 `assert len(FACTOR_COLS) == 8`
      在**收集期**抛 ⇒ pytest 给 `no tests collected` / rc=2
      ⇒ 按本台纪律判「不可验证」⇒ **该文件其余所有变异一并失去判别力**
      （台子对 rc=2 只印一行警告，读者很容易看成「不关我事」）。
      ⇒ **写变异时必须让变异保持那些模块级断言为真**：
      把 `"expo_rsi"` 改成 `"expo_rsi_typo"`（长度不变、仍 8 列）
      而不是删掉一项 —— 后者把实验从「改坏」变成「换实验」。

    ③ 块与块之间用 `"\n\n"` 切分 ⇒ 一个以换行结尾的块会让**下一块以
      `"\n"` 开头** ⇒ `block.split("\n", 1)[1]` 把**首行标签**当成 `old` 的
      第一行 ⇒ `src.count(old) == 0`。追加规格后若报「旧串匹配 0 处」
      而你确信锚点是对的，**先查这一项**（改法：`b.strip("\\n")` 再 join）。

    ④ 🚨 **`dedent` 对「变异体」也生效，且两边的 pad 可以不同。**
      2026-10-15 实测（`test_bond_sync_probe.mut:BS4` 第一次写）：
        锚点写 `failed.append(c)` @0 格 + `continue` @12 格
          ⇒ pad=0，**逐字命中**源码；
        而变异体我写成了 `failed.append(c)` @12 格 + `raise` @12 格
          ⇒ pad=**12**，两条都被拽成 0 格
          ⇒ 替进源码后 `except` 块里多出一行 0 格缩进
          ⇒ **IndentationError** ⇒ rc=2「不可验证」。

      ⇒ **规则不是「锚点首行缩进 ≤ 块内最小值」，而是**
        **「锚点与变异体的首行缩进必须相同」**（两者的 pad 才一致）。
        多行块的稳妥写法：首行写 0 格（`pad=0` ⇒ 逐字命中），
        其余行**照抄源码的缩进**，变异体同样首行 0 格。

      ⚠️ 这与 ①② 是不同的失效方向：①② 报「旧串匹配 0 处」（一眼看得见），
        ④ 报的是 **IndentationError**，
        而台子对 `compile()` 失败只说「不可验证，跳过 pytest」——
        **很容易读成「这条变异不重要」**，其实它可能正是你唯一想验的那条。
        见到 SyntaxError 请先查本项，别直接改变异体。
    """
    specs: list[Spec] = []
    for block in path.read_text(encoding="utf-8").split("\n\n"):
        lines = [l for l in block.splitlines() if l.strip()]
        if not lines:
            continue
        head = lines[0]
        if "\t" not in head:
            sys.exit(f"规格首行必须是「标签<TAB>相对路径」：{head!r}")
        if "=== 变异体 ===" not in block:
            sys.exit(f"规格缺「=== 变异体 ===」分隔：{head!r}")
        label, rel = head.split("\t", 1)
        body = block.split("\n", 1)[1]
        old, new = body.split("=== 变异体 ===", 1)

        def dedent(t: str) -> str:
            ls = [l for l in t.splitlines()]
            pad = min((len(l) - len(l.lstrip()) for l in ls if l.strip()), default=0)
            return "\n".join(l[pad:] if l.strip() else "" for l in ls).strip("\n") + "\n"

        specs.append(Spec(label.strip(), rel.strip(), dedent(old), dedent(new), "killed"))
    return specs


def _syntax_gate(p: Path, mutant: str) -> tuple[bool, str | None]:
    """第 121 层闸门：**按目标文件的真实语言**解析变异体。

    返回 `(通过?, 说明)`。⚠️ `说明` 一定会被打印出去 ——
    **静默的闸门与没有闸门同形**（形状 6 的读侧版本）：
    一个恒为假的开关看起来比没有开关更可信，因为它让人以为做过检查。

    🚨🚨 **这条闸门曾经写死 `compile(...)`（2026-10-15 实测踩到）**：
    本机第一次拿它变异 `aplus/web/index.html`（JavaScript），四条**全部**
    报「变异体解析不了 ⇒ 不可验证」—— 而那不是变异体坏了，
    是**闸门用错了语言**。⚠️ 而它报的那句「不可验证」与「证据作废」逐位同形
    ⇒ 人会以为「这批变异写错了」而回头改规格，或更糟：
    以为「守卫有问题」而回头改本来正确的代码（第 112 层通则）。
    ⇒ **恒为假的闸门比没有闸门更贵**，因为它把「没检查」说成「检查失败」。

    ⇒ 处置不是「Python 之外一律放行」（那换成恒真的闸门，同样有害）：
      **有解析器的后缀用真解析器**，逐个后缀问「这东西有没有解析器」。

    | 后缀 | 闸门 | 理由 |
    |---|---|---|
    | `.py` | `compile()` | 原形 |
    | `.html`/`.htm` | 抽内联 `<script>` → **逐块 `node --check`** | 仓里已有这条闸门（`tests/test_chat_wiring.py::test_every_inline_script_block_actually_parses`）⇒ **复用它抽块的量具，不重写正则**（第 163 层） |
    | `.js`/`.mjs` | `node --check` 整个文件 | |
    | `.json` | `json.loads` | |
    | 其余 | **打印「无解析器」并放行** | 本仓的文本型目标（`.md`/`.txt`）没有任何东西会解析它 ⇒ 第 121 层的危害**结构上不存在**；但**必须印出来**，不许静默 |

    ⚠️ 「抽不到内联脚本块」判**不可验证**而不是判通过：
    空输出 ≠ 判决（第 130 层）—— 探测器坏了与「变异体合法」逐位同形。
    """
    suffix = p.suffix.lower()

    if suffix == ".py":
        try:
            compile(mutant, str(p), "exec")
        except SyntaxError as e:
            return False, (f"变异体**解析不了**（{type(e).__name__}: {e.msg}"
                           f"，行 {e.lineno}）⇒ **不可验证**，跳过 pytest。")
        return True, None

    if suffix in (".html", ".htm", ".js", ".mjs"):
        node = shutil.which("node")
        if node is None:
            return False, ("本机没有 `node` ⇒ 前端语法闸门跑不了 ⇒ **不可验证**"
                           "（不许「反正没人解析它」就放行：浏览器会）。")
        with tempfile.TemporaryDirectory() as td:
            if suffix in (".js", ".mjs"):
                blocks = [mutant]
            else:
                blocks = _inline_scripts(mutant)
                if not blocks:
                    return False, ("变异体里**一个内联 `<script>` 都抽不到** ⇒ "
                                   "不是变异体合法，是探测器坏了 ⇒ **不可验证**。")
            for i, code in enumerate(blocks):
                f = Path(td) / f"blk{i}.mjs"
                f.write_text(code, encoding="utf-8")
                r = subprocess.run([node, "--check", str(f)],
                                   capture_output=True, text=True)
                if r.returncode != 0:
                    return False, (f"第 {i} 个内联脚本块**解析不了** ⇒ "
                                   f"**不可验证**，跳过 pytest：\n"
                                   f"{r.stderr.strip()[:400]}")
        return True, None

    if suffix == ".json":
        try:
            json.loads(mutant)
        except json.JSONDecodeError as e:
            return False, f"变异体**不是合法 JSON**（{e}）⇒ **不可验证**，跳过 pytest。"
        return True, None

    return True, (f"⚠️ 后缀 `{suffix}` **没有解析器** ⇒ 第 121 层闸门**未执行**。"
                  f"本仓的文本型目标不会被任何东西解析，危害结构上不存在；"
                  f"但这一条**不许当成「查过了」**。")


def _inline_scripts(src: str) -> list[str]:
    """抽内联 `<script>`（跳过 `src=` 外部脚本）。

    🚨 **从 `tests/test_chat_wiring.py` 按路径 import，不照抄它的正则**（第 163 层）：
    手写一份会在它改掉抽法时静默分叉 —— 那时台子会拿一个**没人验过**的抽法
    给出「解析通过」，而真实浏览器里那块脚本早就没了。
    """
    global _INLINE_SCRIPTS
    if _INLINE_SCRIPTS is None:
        spec = importlib.util.spec_from_file_location(
            "_chat_wiring_for_harness", ROOT / "tests" / "test_chat_wiring.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _INLINE_SCRIPTS = mod._inline_scripts
    return _INLINE_SCRIPTS(src)


_INLINE_SCRIPTS = None


def run(specs: list[Spec], node: str) -> tuple[int, int]:
    print(f"== 正控：基线必须绿（node={node}）==", flush=True)
    base_rc, base_tail, _ = _pytest(node)
    if base_rc != 0:
        print(f"⛔ 基线就红（rc={base_rc}）：{base_tail}")
        print("   ⇒ 每条变异都会「变红」，本次全部记分无效。"
              "先修好基线再谈变异——基线红时的『被杀』是免费信用。")
        return 0, len(specs)
    print(f"✔ 基线绿：{base_tail}\n", flush=True)

    ok = 0
    for spec in specs:
        p = ROOT / spec.rel
        base = sha(p)
        src = p.read_text(encoding="utf-8")

        n = src.count(spec.old)
        if n != 1:
            print(f"❌ {spec.label}\n     旧串匹配 {n} 处（必须恰好 1）⇒ 空操作，绿灯是假的",
                  flush=True)
            continue
        mutant = src.replace(spec.old, spec.new, 1)

        # 🚨🚨 第 121 层：**变异体自己解析不了时，它产生的任何「红」都不是信用。**
        #   实测（2026-10-15）：同一条变异，pytest 在**收集期** import 它 ⇒
        #     rc=2 / "1 error"；
        #   而测试在**函数体内** import 它 ⇒ **rc=1 / "1 failed"** ——
        #     与「守卫真的抓到了缺陷」逐位相同。
        #   ⇒ 靠 rc 分层**抓不住**后者：两边都是 1。
        #   ⇒ 正解是**在跑 pytest 之前**就把「它压根解析不了」挡掉：
        #     一个语法非法的变异体不是实验，它只制造信用。
        #   ⚠️ 闸门按**目标文件的真实语言**走，见 `_syntax_gate`。
        # 🚨 累加器叫 `ok`；🚨 **闸门的返回值不许也叫 `ok`** ——
        #   第一版就踩了：`ok, note = _syntax_gate(...)` 把累加器覆盖成 `True`，
        #   随后 `ok += good` 变成 `True + True == 2`，
        #   于是「四条全杀」被计成 **2/4**，而台子逐条打的是 ✅ ✅ ✅ ✅。
        #   ★ **症状与「有两条变异被放过」逐位同形**（第 148 层同族），
        #   而正确动作恰好相反：不是去改那两条变异体，是去改这个命名。
        gate_ok, note = _syntax_gate(p, mutant)
        if not gate_ok:
            print(f"⚠️  {spec.label}\n     {note}"
                  f"\n     规格必须是能跑起来的源码片段：一条语法非法的变异体"
                  f"无论 pytest 吐哪个码，都拿不到关于守卫的任何信息。",
                  flush=True)
            continue
        if note:
            print(f"ℹ️  {spec.label}\n     {note}", flush=True)

        with tempfile.TemporaryDirectory() as td:
            shutil.copy2(p, Path(td) / "orig")
            p.write_text(mutant, encoding="utf-8")
            drop_bytecode_cache(p)          # 🚨 否则同长度的变异会被 pyc 缓存吃掉
            if sha(p) == base:
                print(f"❌ {spec.label}\n     哈希没变 ⇒ 没写进去", flush=True)
                shutil.copy2(Path(td) / "orig", p)
                continue
            rc, tail, out = _pytest(node)
            shutil.copy2(Path(td) / "orig", p)
            if sha(p) != base:
                print(f"⛔ {spec.label}\n     还原后哈希未回到基线", flush=True)
                continue

        # 🚨🚨 第 116 层在**变异台自己身上**复发（2026-10-15 实测）：
        #   `caught = rc != 0` 会把 pytest 的**用法/收集错误**（rc=2，
        #   例如变异体把 `if` 挪到第 0 列造出 IndentationError，文件压根解析不了）
        #   记成「守住了」。那个 kill 是**假的** ——
        #   守卫并没有拦住任何东西，是被测文件先死了。
        #
        #   ⇒ 只有 **rc == 1**（真失败）才算 killed。
        #      rc ∈ {2,3,4,5} = 「不可验证」：按**未捕获**计入（保守方向），
        #      并把理由原文印出来，不许读者自己脑补。
        if rc == 0:
            caught = False
        elif rc == 1:
            caught = True
        else:
            print(f"⚠️  {spec.label}\n     rc={rc} 属 pytest 的非失败退出码"
                  f"（用法错误 / 收集错误 / 中断）⇒ **不可验证**，"
                  f"不计 killed。原样输出：{tail}", flush=True)
            caught = False
        good = caught if spec.expect == "killed" else not caught
        ok += good
        print(f"{'✅' if good else '❌'} {spec.label}"
              f"（期望 {spec.expect}）\n     rc={rc} / {tail}", flush=True)
        names = _failed_names(out)
        if names:
            print(f"     变红：{', '.join(names)}", flush=True)
    return ok, len(specs)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("specs", type=Path, help="变异规格文本文件")
    ap.add_argument("--node", required=True, help="被测的 pytest node id")
    a = ap.parse_args()
    ok, total = run(load_specs(a.specs), a.node)
    print("\n" + "=" * 74)
    print(f"变异台：{ok}/{total} 条符合预期（基线已验绿）")
    return 0 if ok == total else 1


if __name__ == "__main__":
    raise SystemExit(main())

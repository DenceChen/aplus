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

另有一条**独立于跑测试**的前置检查：**变异体必须能 `compile()`**。
理由是实测出来的对称性：同一条语法非法的变异，
pytest 在收集期 import 它给 `rc=2 / 1 error`，
在函数体内 import 它给 **`rc=1 / 1 failed`** ——
**与「守卫真的抓到了缺陷」逐位相同。**
⇒ 光靠 rc 分层抓不住后者；只能在跑之前挡住。

还有一条判据分层：**只有 `rc == 1` 才算被杀**
（rc∈{2,3,4,5} = 收集/用法/内部错误/中断 ⇒ 不可验证，按存活计入上界）。

用法：

    .venv/bin/python scripts/mutation_harness.py --node tests/xxx.py 变异规格.py
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
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
        #   ⇒ 正解是**在跑 pytest 之前**就把「它压根不是合法 Python」挡掉：
        #     一个语法非法的变异体不是实验，它只制造信用。
        try:
            compile(mutant, str(p), "exec")
        except SyntaxError as e:
            print(f"⚠️  {spec.label}\n     变异体**解析不了**（{type(e).__name__}: {e.msg}"
                  f"，行 {e.lineno}）⇒ **不可验证**，跳过 pytest。"
                  f"\n     规格必须是能跑起来的源码片段：一条语法非法的变异体"
                  f"无论 pytest 吐哪个码，都拿不到关于守卫的任何信息。",
                  flush=True)
            continue

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

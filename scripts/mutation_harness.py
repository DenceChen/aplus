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

用法：

    .venv/bin/python scripts/mutation_harness.py --node tests/xxx.py 变异规格.py
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
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


def _pytest(node: str) -> tuple[int, str, str]:
    r = subprocess.run(
        [PY, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider", node],
        cwd=ROOT, capture_output=True, text=True, timeout=3600,
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
        with tempfile.TemporaryDirectory() as td:
            shutil.copy2(p, Path(td) / "orig")
            p.write_text(src.replace(spec.old, spec.new, 1), encoding="utf-8")
            if sha(p) == base:
                print(f"❌ {spec.label}\n     哈希没变 ⇒ 没写进去", flush=True)
                shutil.copy2(Path(td) / "orig", p)
                continue
            rc, tail, out = _pytest(node)
            shutil.copy2(Path(td) / "orig", p)
            if sha(p) != base:
                print(f"⛔ {spec.label}\n     还原后哈希未回到基线", flush=True)
                continue

        caught = rc != 0
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

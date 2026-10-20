"""变异验证：**「用更新的快照代替」这条退化路径**必须被守卫抓住。

## 变异的是什么

`aplus/vintage.py::latest_on_or_before` 的**唯一**职责是
「取 `<= as_of` 的最后一份快照」。把它改成「取最新的一份」，
就是 `aplus/vintage.py` 的 docstring 逐字禁止的那件事：

> 拿最新的一份凑数，产出的 PIT 帧与直接读当前缓存**在行为上完全一致**
> ——都是后修值——却多了一个「已做 PIT」的标签。
> 那比不做 PIT 更糟，因为它让前视偏差**免于被审查**。

## 为什么要有这个脚本（而不是只用 `scripts/mutation_harness.py`）

台子给的是「变异 → pytest 红不红」。本脚本额外给三样台子没有的：

1. **标记计数**：变异体里放一个唯一标记串，断言它在落盘后的文件里
   **恰好出现 1 次**。
   ⇒ 「哈希变了」只证明**文件**变了，不证明**哪一处**变了
   （仓里第 14 种形状：写入确认用子串 ⇒ 恒真）。
2. **逐位还原**（`sha256` 相等，不是「替换执行了」）。
3. **失败原因的合法性**：pytest 的 `rc` 必须落在 **1**（真失败），
   `rc ∈ {2,3,4,5}` 是收集/用法/内部错误 ⇒ **不可验证**，
   那种红与「守卫抓到了缺陷」在终端上长得一模一样。

## 🚨 运行期间不许并发跑别的 pytest

本脚本会**真的改写** `aplus/vintage.py`。任何并发的、会 import 它的
pytest 会读到变异体 ⇒ 那些红是本实验制造的，不是仓里的缺陷
（仓里第 173 层）。

用法：
    .venv/bin/python scripts/mutate_pit_snapshot_substitution.py
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = str(ROOT / ".venv/bin/python")
TARGET = ROOT / "aplus" / "vintage.py"
TEST_FILE = "tests/test_factor_fundamental.py"

#: 变异锚点。**必须恰好命中 1 处**（多处 ⇒ 改了什么不确定）。
OLD = "    cands = [d for d in available_days(root) if d <= as_of]"

#: 变异体：把「只取 ≤ as_of」改成「取最新的一份」——
#: 这正是 `vintage.py` docstring 逐字禁止的那件事。
NEW = ("    cands = available_days(root)  "
       "# MUTANT newest-snapshot-substitution")

#: 落盘后必须**恰好出现 1 次**的唯一标记。
MARKER = "MUTANT newest-snapshot-substitution"


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def drop_bytecode_cache(p: Path) -> None:
    """清掉被测文件的 `.pyc`。

    pyc 的校验键是 (源文件 mtime, 大小)。同长度、同秒写入的变异
    会命中缓存 ⇒ 按路径 import 的测试执行**旧代码** ⇒ 整条变异静默变成空操作
    （仓里第 121 层的第二个入口）。
    """
    cache = p.parent / "__pycache__"
    if not cache.is_dir():
        return
    for c in cache.glob(f"{p.stem}.cpython-*.pyc"):
        try:
            c.unlink()
        except OSError:
            pass


def run_pytest(node: str) -> tuple[int, str, str]:
    """跑 pytest，**输出落文件**再读。

    🚨 绝不让管道站在被观测的命令上（仓里第 8/107 层）：
    `pytest … | tail` 的退出码是 `tail` 的，而「2 failed」与全绿
    在终端上长得几乎一样。
    """
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "pytest.out"
        with out.open("w", encoding="utf-8") as f:
            rc = subprocess.run(
                [PY, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider",
                 node],
                cwd=ROOT, stdout=f, stderr=subprocess.STDOUT,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            ).returncode
        text = out.read_text(encoding="utf-8", errors="replace")
    tail = [l for l in text.splitlines() if l.strip()]
    return rc, (tail[-1] if tail else "(无输出)"), text


def failed_names(text: str) -> list[str]:
    return sorted({l.split("::")[1].split()[0]
                   for l in text.splitlines() if l.startswith("FAILED ")})


def main() -> int:
    original = TARGET.read_bytes()
    orig_sha = sha256_bytes(original)
    src = original.decode("utf-8")

    n = src.count(OLD)
    print("=" * 78)
    print("变异验证：「用更新的快照代替」")
    print("=" * 78)
    print(f"目标文件      : {TARGET.relative_to(ROOT)}")
    print(f"变异前 sha256 : {orig_sha}")
    print(f"锚点命中      : {n} 处（必须恰好 1）")
    if n != 1:
        print("⛔ 锚点不唯一 ⇒ 改了什么不确定 ⇒ 本次实验作废。")
        return 2

    mutant = src.replace(OLD, NEW, 1)
    with tempfile.TemporaryDirectory() as td:
        backup = Path(td) / "orig.py"
        backup.write_bytes(original)          # **字节**备份，不是 git checkout
        try:
            TARGET.write_text(mutant, encoding="utf-8")
            drop_bytecode_cache(TARGET)
            mutated = TARGET.read_bytes()
            mut_sha = sha256_bytes(mutated)
            marker_count = mutated.decode("utf-8").count(MARKER)
            print(f"变异后 sha256 : {mut_sha}")
            print(f"标记计数      : {marker_count}（必须恰好 1）")
            if mut_sha == orig_sha:
                print("⛔ 哈希没变 ⇒ 没写进去 ⇒ 绿灯是假的。")
                return 2
            if marker_count != 1:
                print("⛔ 标记计数不对 ⇒ 变异没落在预期的地方。")
                return 2

            rc, tail, text = run_pytest(TEST_FILE)
            print(f"\n变异后 pytest : rc={rc}")
            print(f"摘要行        : {tail}")
            names = failed_names(text)
            print(f"具名失败用例  : {len(names)} 条")
            for nm in names:
                print(f"    - {nm}")
            if rc != 1:
                print(f"⛔ rc={rc} 不是 1 ⇒ 不是「真失败」"
                      f"（2/3/4/5 = 收集或用法错误 ⇒ 不可验证）")
                return 2
            if not names:
                print("⛔ 没有具名失败用例 ⇒ 这条红不是来自断言失败。")
                return 2
        finally:
            shutil.copy2(backup, TARGET)       # 逐位还原
            drop_bytecode_cache(TARGET)

    back = TARGET.read_bytes()
    back_sha = sha256_bytes(back)
    print(f"\n还原后 sha256 : {back_sha}")
    print(f"与变异前逐位相同: {back_sha == orig_sha}")
    if back_sha != orig_sha:
        print("⛔ 还原失败 ⇒ 文件还躺在变异态。")
        return 2

    rc2, tail2, _ = run_pytest(TEST_FILE)
    print(f"\n还原后 pytest : rc={rc2}")
    print(f"摘要行        : {tail2}")
    if rc2 != 0:
        print("⛔ 还原后仍不绿 ⇒ 变异之前仓里就有问题，别把它算到这次头上。")
        return 2

    print("\n" + "=" * 78)
    print("结论：变异真的落地（sha256 变 + 标记恰好 1 次），"
          "且被 **真实断言失败**抓住（rc=1 + 具名用例）。")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

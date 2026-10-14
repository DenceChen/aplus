"""全仓门禁的唯一入口 —— **取代手敲的 `pytest … | tail`**。

## 为什么需要它

2026-10-15 两次把管道放在被观测的命令上：

```bash
.venv/bin/python -m pytest tests/ -q 2>&1 | tail -12   # → $? 是 tail 的
.venv/bin/python -m pytest tests/ -q 2>&1 | tail -25   # → 2 failed 却报 exit 0
```

第二次的输出长这样：

```
2 failed, 2477 passed, 1 skipped, 1 warning in 284.85s
[exited with code 0]        ← 就在失败摘要下面三行
```

而那 2 条红守卫**正是为了拦这种提交而存在的** ——
它们拦住了我，而管道本来要把信号吞掉。

⇒ 与其靠纪律（已经在同一天失效两次），不如**让失败形态表达不出来**。

## 三条结构保证

1. **不经 shell**：用 `subprocess.Popen([...])` 传列表，
   `shell=False`（默认）⇒ **没有任何管道可言**，也就没有「$? 属于谁」的问题。
2. **退出码原样透传**：本脚本的返回值 = pytest 的返回值，`raise SystemExit(rc)`。
   `SystemExit(0)` 只在 pytest 真的全绿时才发生。
3. **摘要行单独打一遍**：从 pytest 的输出里把末行摘要抠出来，
   以 `GATE:` 前缀重打，**即使调用方自己接了管道也看得到**。
   🚨 这一条是给「调用方仍然手滑加了管道」兜底的——
   本脚本管不了自己的 stdout 被谁消费，所以结论必须**打两次**。

## 用法

```bash
.venv/bin/python scripts/run_gate.py                  # 全量
.venv/bin/python scripts/run_gate.py -k provenance    # 传任何 pytest 参数
.venv/bin/python scripts/run_gate.py --log /tmp/g.log # 额外留一份完整输出
```

## 边界（诚实标注）

- **它管不住「忘记看输出」**。退出码对了、人不看，绿灯仍会自己发出去。
  本脚本能保证的只有「**信号没被中间那截吞掉**」。
- **它不替代产物层守卫**。mtime / provenance 那批守卫照旧跑在 pytest 里面。
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: pytest 的**判决行**。`-q` 下形如
#: `2 failed, 2477 passed, 1 skipped, 1 warning in 284.85s (0:04:44)`
#:
#: ⚠️ **只认判决形态，不认 `FAILED …` / `ERROR …` 这类逐条行**：
#: 收集期崩溃时正文里有 `ERRORS !`、`E   SyntaxError: …` 等十几行，
#: 取「最后一个匹配」会把某条**报错细节**当成摘要打出去，
#: 而那行对「跑没跑到判决阶段」零信息。
#: ⇒ 「摘要缺失」必须真的对应「pytest 没给出判决」，才能触发那句播报。
SUMMARY = re.compile(
    r"^(?:"
    r"\d+ (?:failed|error|errors|passed|passed)\b[^\n]*"   # `2 failed, 2477 passed …`
    r"|no tests ran\b[^\n]*"                               # `-k` 空选集
    r"|=+ .*?(?:passed|failed|error|no tests ran).*?=+\s*$"  # `-v` 下的横幅
    r")$"
)


def summary_line(text: str) -> str | None:
    """从 pytest 输出里取**最后一行判决行**。

    取末行而不是 grep 全文：测试名里可能含 "passed"，
    正则全文搜索会命中一串噪声，而**噪声会把真判决淹掉**。
    """
    hits = [ln.strip() for ln in text.splitlines() if SUMMARY.match(ln.strip())]
    return hits[-1] if hits else None


def run(argv: list[str], log: Path | None = None) -> int:  # noqa: C901
    """跑 pytest，**原样返回它的退出码**。不解析、不改写、不美化。"""
    cmd = [sys.executable, "-m", "pytest", *argv]
    print(f"门禁命令：{' '.join(cmd)}", flush=True)

    started = time.time()
    # ★ `shell=False`（默认）是本脚本的全部意义所在。
    #   一旦 `shell=True`，管道就会回来，`$?` 也就重新有了歧义。
    proc = subprocess.Popen(
        cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    assert proc.stdout is not None
    collected: list[str] = []
    for line in proc.stdout:
        collected.append(line)
        sys.stdout.write(line)          # 实时透传，人能看着它跑
        sys.stdout.flush()
    rc = proc.wait()

    text = "".join(collected)
    if log:
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(text, encoding="utf-8")

    elapsed = time.time() - started
    print(f"\n{'=' * 68}")
    print(f"GATE: rc={rc}  用时 {elapsed:.1f}s")
    summary = summary_line(text)
    if summary:
        print(f"GATE: 摘要 → {summary}")
    else:
        # 摘要缺失本身就是异常：可能是收集期就崩了（UsageError / ImportError），
        # 那时 rc 非 0 而正文里一行 "passed" 都没有。**必须说出来**，
        # 否则「没打摘要」会被读成「在跑」。
        print("GATE: ⚠️ 未找到摘要行 —— pytest 可能根本没跑到判决阶段")
    verdict = "✅ 绿" if rc == 0 else f"❌ 红（rc={rc}）"
    print(f"GATE: 判决 → {verdict}")
    print("=" * 68, flush=True)
    return rc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="全仓门禁唯一入口（不经 shell，退出码原样透传）",
        add_help=False,
    )
    ap.add_argument("--log", type=Path, default=None, help="把完整输出另存一份")
    ap.add_argument("-h", "--help", action="store_true", help="显示本脚本用法")
    args, passthrough = ap.parse_known_args(argv)

    if args.help:
        ap.print_help()
        print("\n其余参数原样转交 pytest，例如：\n"
              "    .venv/bin/python scripts/run_gate.py -k provenance\n"
              "    .venv/bin/python scripts/run_gate.py tests/test_sources.py")
        return 0

    # ⚠️ 用 `parse_known_args` 而不是 `nargs=REMAINDER`：
    #   REMAINDER 遇到**以 `-` 开头**的第一个 token 会让 argparse 直接报
    #   「unrecognized arguments」—— 而 `-k` / `-x` / `-m` 恰恰都是以 `-` 开头，
    #   也就是说 REMAINDER 恰好在最常用的传参方式上失效。
    passthrough = [a for a in passthrough if a != "--"]

    # 默认 `-q`：与仓库其它门禁一致，输出量可控。
    # ⚠️ **只判断"有没有给过"**，不要重复加 —— 两次 `-q` 等于 `-qq`，
    #   摘要虽然还在，但中间的逐用例行会整段消失，排障时少一半信息。
    if not any(a in {"-q", "-v", "-vv", "--verbose"} for a in passthrough):
        passthrough = ["-q", *passthrough]
    return run(passthrough, args.log)


if __name__ == "__main__":
    raise SystemExit(main())

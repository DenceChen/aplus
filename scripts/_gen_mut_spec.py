#!/usr/bin/env python
"""变异规格生成器：从源码**按行**切出 `.mut` 锚点。

为什么不用手写：第 122 层已在「手数缩进」这个坑上栽过 7 次 ——
`load_specs` 的 `dedent` 取**块内最小缩进**且**只减不增**，
多行锚点若首行不写到列 0，续行会被一起剥掉，而 `src.count(old)` 是**子串匹配**
⇒ 报「旧串匹配 0 处」，而台子的输出与「守卫有洞」**逐位同形**。

本脚本**不复用**台子的 `dedent`（那是待检对象，不是生成器），
但**自检必须复用**：`load_specs` + `src.count(old)==1`，
即复用 harness 自己的那份解析（第 171 层）。

⚠️ **行号是维护点**：`MUTS` 里那些 `1-based 闭区间` 在被测文件改动后会腐烂。
好消息是腐烂**会被响亮报出**——`carve()` 的 `assert old.count(old_sub) == 1`
当场抛异常，而症状（`old` 命中 0 处）与「台子有问题」**不同**：
本脚本抛的是 `AssertionError` 且带标签名，台子报的是「拒收」。
⇒ **该改的是 `MUTS` 的行号，不是去手改 `.mut`。**
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REL = "scripts/measure_delivery_caliber_friction.py"
SRC = ROOT / REL

# (label, 起始行, 结束行, old 子串, new 子串)  —— 行号 1-based、闭区间
MUTS = [
    ("F1 单边换手被乘 2", 125, 125,
     "rpy = DAYS_PER_YEAR * turnover",
     "rpy = DAYS_PER_YEAR * 2 * turnover"),
    ("F2 判决臂写死满换手", 163, 163,
     'turn["turnover_min_measured"], fee)', "1.0, fee)"),
    ("F3 年化常数取错另一套", 38, 38,
     "from aplus.hurdle import DAYS_PER_YEAR",
     "from aplus.costs import TRADING_DAYS_PER_YEAR as DAYS_PER_YEAR"),
    ("F4 判决取点估计而非区间下端", 172, 172,
     'judge(at_min["annual_friction_pct_of_capital"])',
     'judge(at_point["annual_friction_pct_of_capital"])'),
    ("F5 名数写成 5", 42, 42, "N_POSITIONS = 10", "N_POSITIONS = 5"),
    ("F6 判决边界 >= 写成 >", 144, 144,
     "if friction_min >= net_target:", "if friction_min > net_target:"),
    ("F7 跌腿不过滤", 65, 65,
     'leg = rows[rows["direction"] == "up"]', "leg = rows"),
    ("F8 首个 entry_date 参与换手", 84, 86, "            continue\n", ""),
    ("F9 measure 调用点名数少 5", 162, 162,
     "N_POSITIONS,", "N_POSITIONS - 5,"),
]


def carve(src_lines: list[str], a: int, b: int) -> str:
    """切出 [a, b] 行，**首行写到列 0**、续行保持源码真实缩进。

    ⇒ pad = 0 ⇒ `dedent` 不左移 ⇒ 逐字是源码子串。
    """
    chunk = src_lines[a - 1:b]
    out = [chunk[0].lstrip()] + chunk[1:]
    return "\n".join(out) + "\n"


def main() -> int:
    src_text = SRC.read_text(encoding="utf-8")
    src_lines = src_text.splitlines()

    blocks = []
    for label, a, b, old_sub, new_sub in MUTS:
        old = carve(src_lines, a, b)
        new = carve(src_lines, a, b)
        assert old.count(old_sub) == 1, (
            f"{label}: 第 {a}~{b} 行里 `{old_sub!r}` 出现 "
            f"{old.count(old_sub)} 次（必须恰好 1 次）")
        new = new.replace(old_sub, new_sub, 1)
        assert old != new, f"{label}: 替换后没有变化"
        # 🚨 load_specs 限制③：块与块用 "\n\n" 切分，而 `block.split("\n",1)[1]`
        # 会把**下一块开头的那个 "\n"** 当成「标签行已结束」⇒ **标签被算进 old**
        # ⇒ `src.count(old)==0`。故每块**不许以换行结尾**。
        blocks.append(
            f"{label}\t{REL}\n{old}=== 变异体 ===\n{new}".rstrip("\n"))

    out = "\n\n".join(blocks) + "\n"
    out_path = ROOT / "tests/mutation_specs/test_delivery_caliber_friction.mut"
    out_path.write_text(out, encoding="utf-8")

    # ---- 自检：**复用 harness 自己的解析器**（第 171 层）----
    spec = importlib.util.spec_from_file_location(
        "_mh", ROOT / "scripts/mutation_harness.py")
    mh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mh)
    specs = mh.load_specs(out_path)
    bad = 0
    for sp in specs:
        n = src_text.count(sp.old)
        flag = "ok " if n == 1 else "BAD"
        if n != 1:
            bad += 1
        print(f"{flag} {sp.label}  (rel={sp.rel!r}  old 命中 {n} 处)")
    print(f"--- 共 {len(specs)} 条，不合规 {bad} 条 -> {out_path}")
    return 1 if (bad or len(specs) != len(MUTS)) else 0


if __name__ == "__main__":
    raise SystemExit(main())

"""守卫：产物有没有**落后于**生成它的代码（重跑纪律）。

## 为什么单独一个文件

产物层的守卫有个特殊失效方向：**它读的是上次跑落盘的产物**，
所以「源码改了、没重跑」这件事它**看得见**，
而「源码形状」它**看不见**。两者必须分开：

| 变异类别 | 谁接住 | 为什么 |
|---|---|---|
| 代码形状（公式、判据、盖章） | `test_portfolio_nav_measures_the_target.py` | AST/行为 |
| **产物新鲜度**（改了码没重跑） | **本文件** | 比「章 vs 盘上字节」 |

⇒ 若把 G12 混进代码形状那个文件，它会在**每一条**变异下变红
（变异必然改源码字节）⇒ **给所有变异发免费信用**，把台子的判别力稀释掉。
这正是 `mutation_harness` 顶部记的那个「假 credit」事故。
⇒ 本文件**只**挂「改了码」类变异。

## G12 抓的是什么

章上的 `code_file_sha256` 是**盖章那一刻**脚本自身的字节指纹。
它与盘上脚本当前字节不等 ⇒ 有人改了脚本却没重跑 ⇒ 产物在说一件
它生成时还不成立的事（第 125 层：盖章那一刻盘上的字节 ≠ 这次跑的字节）。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

#: 章的 `code_file_sha256` vs 盘上脚本字节 —— **同一个量**，所以由同一处守卫。
#: ⚠️ 2026-10-16 加第二个条目的原因：第一版只有 portfolio_nav 一条，于是
#:   `portfolio_exec.json` **没有任何守卫**覆盖这件事
#:   （它自己的 G9 只查 `source_parquet_sha256` = **底稿**指纹，对源码变更失明）。
#:   实测症状：改了 `measure_portfolio_exec.py` 之后 G9 照样绿。
#:   ⇒ 这正是「强制重跑要配参数守卫」那条：守卫逼你重跑，
#:     却不管你重跑对了没有；派生产物也不提示自己过期。
FINGERPRINTED = (
    ("portfolio_nav", ROOT / "scripts" / "measure_portfolio_nav.py",
     ROOT / "data/reports/portfolio_nav.json",
     ROOT / "data/reports/portfolio_nav.json.provenance.json"),
    ("portfolio_exec", ROOT / "scripts" / "measure_portfolio_exec.py",
     ROOT / "data/reports/portfolio_exec.json",
     ROOT / "data/reports/portfolio_exec.json.provenance.json"),
    # 🚨 2026-10-21 补第三个条目：`d2_gap_calibers` 此前**零覆盖** ⇒
    #   改了 `scripts/d2_gap_calibers.py` 不重跑，**没有任何东西会红**
    #   （症状与「改了 `measure_portfolio_exec.py`」那次一模一样：
    #    守卫只钉了别的产物的那一件 ⇒ 这一件在视野之外）。
    ("d2_gap_calibers", ROOT / "scripts" / "d2_gap_calibers.py",
     ROOT / "data/reports/d2_gap_calibers_2026-10-17.json",
     ROOT / "data/reports/d2_gap_calibers_2026-10-17.json.provenance.json"),
)


def _same(a: str | None, b: str) -> bool:
    """hex 比较**由程序做**（第 155 层）。

    绝不把两个 hex 手抄到断言里比大小写或前 12 位——
    那是「打印出来的东西看起来对」而不是「比较发生过」。
    """
    return a == b


@pytest.mark.parametrize("name,script,report,side", FINGERPRINTED,
                         ids=[f[0] for f in FINGERPRINTED])
def test_g12_stamp_records_the_scripts_bytes_at_generation_time(
        name, script, report, side):
    """G12：章里的 `code_file_sha256` == 脚本**当前字节**。"""
    if not (report.is_file() and side.is_file()):
        pytest.skip(f"{name} 产物未生成")
    sc = json.loads(side.read_text(encoding="utf-8"))
    got = sc.get("code_file_sha256")
    assert got is not None, f"{name} 的章里没有 code_file_sha256：{sc}"
    want = hashlib.sha256(script.read_bytes()).hexdigest()
    if not _same(got, want):
        assert False, (
            f"{name}：章记录的脚本字节与盘上脚本当前字节不相等 ⇒ "
            f"源码改过但产物没重跑（章={got[:12]}… 盘上={want[:12]}…）。"
            f"修法：.venv/bin/python {script.relative_to(ROOT)}")


def test_g13_positive_control_this_guard_can_go_red():
    """正控：`_same` 确实会红。

    全绿但失不掉的守卫等于没有守卫（第 122 层）。
    本条不碰真文件（🚨 门禁运行期间不得改文件），只打比对函数。
    """
    assert _same("abc", "abc") is True
    assert _same("abc", "abd") is False
    assert _same(None, "abc") is False

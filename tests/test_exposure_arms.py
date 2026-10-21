"""「半仓档」的权益权重：**一个真相源**，且**不是**生产敞口。

## 为什么要单独立这个模块（2026-10-21）

`scripts/deployed_account_nav.py` 要报两个臂：「满仓」与「**半仓**」。
⇒ 「半仓档」的 `W` 是**一个固定的示意配置**，**不是**生产敞口
⇒ 它该有自己的名字、自己的真相源。

🚨 **它曾经接的是生产敞口**（`W = expo["equity_weight"]`）——
那是 **2026-10-16 的巧合**（生产恰好是「5 只 × 10% = 0.5」）。
⇒ 生产在 **2026-10-20** 改成「满仓 10 只 × 10% = 1.0」之后，
「半仓档」变成了**满仓档自己** ⇒ 两臂逐位相同
⇒ **那个对比等于拿一个东西跟它自己比**。

⚠️ **而没有任何东西会红**：守卫 `test_A1b_…`（AST）只禁**字面量 0.5**，
而半仓臂当时读的是 `expo["equity_weight"]` —— 源码里**压根没有 0.5 这个字面量**。
⇒ **守卫守的是「不许手打生产敞口」，它拦不住「把两个量接在一起」。**
⇒ 这条文件补的就是那个缺口。

## 本文件守什么

1. `HALF_EXPOSURE_W` **只在一个地方**被定义（`aplus/exposure_arms.py`）
2. 生产脚本**导入**它，**不重新定义**它
3. 它的值是 0.5，且**明写**它不是生产敞口
4. **生产敞口仍然从生产签名读**（不许被这个模块接管）
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aplus.exposure_arms import HALF_EXPOSURE_W  # noqa: E402

ARMS = ROOT / "aplus" / "exposure_arms.py"
SCRIPT = ROOT / "scripts" / "deployed_account_nav.py"


def test_the_half_arm_weight_is_half():
    """「半仓档」就是半仓。"""
    assert HALF_EXPOSURE_W == pytest.approx(0.5)


def test_the_half_arm_weight_has_exactly_one_definition():
    """🚨 **一个真相源**。

    两份副本之间没有守卫 ⇒ 它们会漂（第 97 层）。
    ⇒ 全仓只许 `aplus/exposure_arms.py` 定义它。
    """
    offenders = []
    for path in sorted(ROOT.glob("aplus/**/*.py")) + sorted(ROOT.glob("scripts/**/*.py")):
        if path == ARMS:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name) and tgt.id == "HALF_EXPOSURE_W":
                        offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert not offenders, (
        f"HALF_EXPOSURE_W 在这些地方又被定义了一遍：{offenders} ⇒ 两份副本会漂")


def test_the_production_script_imports_it_instead_of_redefining_it():
    """生产脚本**导入**，不重新定义（重新定义 = 第二份副本）。"""
    src = SCRIPT.read_text(encoding="utf-8")
    tree = ast.parse(src)
    redefinitions = [n.lineno for n in ast.walk(tree)
                     if isinstance(n, ast.Assign)
                     for t in n.targets
                     if isinstance(t, ast.Name) and t.id == "HALF_EXPOSURE_W"]
    assert not redefinitions, (
        f"生产脚本第 {redefinitions} 行又定义了一次 HALF_EXPOSURE_W")
    assert "from aplus.exposure_arms import HALF_EXPOSURE_W" in src, (
        "生产脚本没有从 aplus.exposure_arms 导入它 ⇒ 它在用别的来源的半仓 W")


def test_it_says_plainly_that_it_is_not_the_production_exposure():
    """⚠️ 「不是生产敞口」这句话**必须写在常量旁边**。

    理由：下一个读代码的人会问「那生产用哪个」⇒ 答案必须在**一眼之内**。
    """
    lines = ARMS.read_text(encoding="utf-8").splitlines()
    idx = next(i for i, ln in enumerate(lines)
               if ln.startswith("HALF_EXPOSURE_W = "))
    # 「常量旁边」= 它**上面**那几行连续的注释（不是整个文件，也不是它下面）
    above = []
    j = idx - 1
    while j >= 0 and lines[j].lstrip().startswith("#"):
        above.append(lines[j])
        j -= 1
    neighborhood = "\n".join(reversed(above))
    assert "生产敞口" in neighborhood, (
        "常量**上面**的注释里没有说它与生产敞口的关系 ⇒ "
        "读者会拿它当生产敞口用（它在文件末尾、注释在末尾 ⇒ 刚好是那几行）")


def test_the_production_exposure_is_still_read_from_the_signature():
    """🚨 **生产敞口不许被这个模块接管**。

    生产敞口 = `build_allocation` 的两个默认参数之积，**从签名读**（不手打）。
    ⇒ 这个模块只放示意档 ⇒ 守卫钉住「生产脚本的敞口仍然走 `deployed_exposure()`」。
    """
    src = SCRIPT.read_text(encoding="utf-8")
    assert "expo = deployed_exposure()" in src, (
        "生产脚本不再从签名读敞口了 ⇒ 生产敞口要么被手打了、要么被这个模块接管了")
    assert "HALF_EXPOSURE_W" not in src.split("expo = deployed_exposure()")[0], (
        "半仓档的常量出现在生产敞口读取**之前** ⇒ 容易被读成它在定义生产敞口")

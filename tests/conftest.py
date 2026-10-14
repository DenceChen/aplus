"""pytest 会话级自愈：开工前把上次被中断的变异测试遗留的源码还原回去。

★ 为什么必须有它（2026-10-15 实测，不是我拍的）

`scripts/mutation_check.py` 造变异体时有三层还原网：

| 层 | 机制 | 挡得住 |
|---|---|---|
| 1 | `finally` | Python 异常 |
| 2 | `_install_signal_guards()` | SIGTERM / SIGINT |
| 3 | `.mutation_backup/*.bak` + `_recover_pending()` | **SIGKILL**（靠下次启动自愈） |

但第 3 层只在 `mutation_check` 自己启动时才会跑。
⇒ **普通全仓门禁被 SIGKILL 杀掉（本机 16 GB 发生过 OOM）时，
那条备份谁都不会去读**，源文件就永久停在变异态。

⚠️ 变异态的可怕之处在于它**报告一切正常**：不报错、测试不红，
只是被摘掉的那条性质悄悄没了。2026-10-14 的 `scripts/ic_guided_race.py`
就是这样活了一整轮，是再跑一次变异测试才暴露的。

⇒ 所以自愈的触发点不能挂在「谁改的」上，必须挂在**每次会话开工**上。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session", autouse=True)
def recover_interrupted_mutations():
    """**每个** pytest 会话开工前先跑一遍磁盘遗留备份自愈。

    没有遗留备份时它是彻底的空操作（`BACKUP_DIR` 不存在 → 直接返回 0），
    所以常态下不会碰任何文件。

    还原失败就让它抛：**带着「源码可能正停在变异态」这件事继续跑门禁，
    比当场红掉危险得多** —— 后者只浪费五分钟，前者会产出假判决。
    """
    scripts_dir = str(ROOT / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import mutation_check as mc

    return mc._recover_pending()

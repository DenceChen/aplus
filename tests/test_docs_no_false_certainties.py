"""🚨 文档层守卫：**CLAUDE.md 正文不许把「测不到」写成「没有」**（第 160 层）

## 为什么这道守卫存在（它不是「顺手加的」）

`factor_width_mde.json` 的产物**自己逐字写了两次**：

```
verdict_branch
  「N=200 仍不显著 ⇒ 在能测的量级上没有可证明的超额。
    ⚠️ 这不等于「没有超额」—— 只等于『测不到』。」
what_this_cannot_prove[3]
  「『N=200 不显著』只等于『测不到』，不等于『没有超额』」
```

实测五档 `detectable` **全为 `False`**，`|毛超额| / MDE` = 0.31 / 0.45 / 0.57 / 0.67 / 0.81
⇒ **连符号都定不了**。

⚠️ 而 CLAUDE.md **第 830 行**当时写的是：

> ⇒ 瓶颈**不在成本、不在测量、不在样本长度，在信号本身**。

那是**产物自己禁止的结论**，而它出现在**每次会话都自动进上下文的文件里**。
错误的**方向是乐观的**（把「仪器看不清」写成「东西本身不行」）
⇒ 会误导人放弃**唯一能解锁判决**的那一项（加样本量）。

## 为什么代码层已有的守卫抓不到它

`tests/test_bm_ic_attribution.py:101` 有守卫
「『不显著』必须配检测下限」，但它守的是 `tail_contrasts()` **产物字段**
⇒ **守住了代码层，漏了文档层**。
而文档层的消费者是**人**，不是 pytest。

⇒ 同族：`aplus-guard-must-test-the-right-layer`
（「iPad 视口 ≠ iPad，判据要钉分支与设备能力」）。

## 为什么只用**单个字符串**，不做禁语黑名单

`an-over-strict-guard-is-as-harmful-as-a-tautological-one` 的头号成因是判据脆。
若在这里维护一份长禁语表，CLAUDE.md 里每加一句「**禁止说** X」都会把自己判红
（503 行就写着「已证伪 alpha」作为**禁语示例**）。

⇒ 所以只钉**一条**逐字已知的越界句 + 一条反控，判据来源是**产物**而非我的发明。

## 变更纪律

⚠️ 若产品层将来真的**证伪**了超额（而不是「测不到」），
这道守卫会挡下合法的改写。⇒ 那时正确的做法是：
**先在产物里写下 `what_this_cannot_prove` 的对应条款**
（让它从「测不到」变成「已证伪」，且给出证据），
再同步改文档 —— 而**不是**把这里的断言删掉。
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CLAUDE_MD = REPO_ROOT / "CLAUDE.md"
MISTAKE_LOG = REPO_ROOT / "docs" / "MISTAKE_LOG_2026-10-14.md"
ARTIFACT = REPO_ROOT / "data" / "reports" / "factor_width_mde.json"

# 🚨 逐字抄自 CLAUDE.md 第 830 行（第 160 层被推翻的那一句）。
#   保留完整措辞而不是关键词，是为了让误伤面等于零：
#   只有「重新写出这句原话」才会触发。
BANNED = "瓶颈**不在成本、不在测量、不在样本长度，在信号本身**"


def test_claude_md_never_asserts_the_bottleneck_is_not_measurement():
    """CLAUDE.md 正文不许断言「瓶颈不在测量」。

    五档 `detectable` 全为 `False`（|gross|/MDE = 0.31~0.81）⇒ 测量**就是**
    当前的瓶颈；产物逐字写「只等于『测不到』，不等于『没有超额』」。
    """
    text = CLAUDE_MD.read_text(encoding="utf-8")
    offenders = [
        (i, line.strip())
        for i, line in enumerate(text.splitlines(), 1)
        if BANNED in line
    ]
    assert not offenders, (
        "CLAUDE.md 重新出现了「瓶颈不在测量」：\n"
        + "\n".join(f"  L{i}: {ln}" for i, ln in offenders)
        + "\n产物 `factor_width_mde.json` 的 `what_this_cannot_prove[3]` 逐字写："
        "「『N=200 不显著』只等于『测不到』，不等于『没有超额』」；"
        "五档 |gross|/MDE = 0.31~0.81 ⇒ **连符号都定不了**。"
        "『加数据』是**解锁**判决的手段，不是与『加本金/加名数』同类的封口项。"
    )


def test_the_banned_phrase_is_actually_checked_not_vacuous():
    """🚨 反控：这道守卫不许退化成永真（第 112 层）。

    若把 `BANNED` 改成一段文档里根本不存在的散文，断言会永远绿。
    ⇒ 它的前提是「这句话曾经真的出现过，且已被登记为推翻」。
    """
    log = MISTAKE_LOG.read_text(encoding="utf-8")
    assert "第 160 层" in log or "## 160." in log, (
        "第 160 层从 MISTAKE_LOG 里消失了 —— "
        "守卫的依据（那段越界句曾真实存在）需要重新核实，"
        "否则这道守卫会变成一条没人查过的断言"
    )
    assert "测不到" in log and "MDE" in log, (
        "第 160 层还在，但它的两个关键词（测不到 / MDE）不见了 ⇒ "
        "登记被改写成别的东西了；守卫的依据已经不成立"
    )
    # ⚠️ 守卫自身必须仍钉着那条**完整**措辞（防止有人把 BANNED 改成片段而放宽）
    #   `**` 只出现首尾两次——写六个会在第一次跑时就红（本层作者实测过）。
    assert BANNED.count("**") == 2 and BANNED.startswith("瓶颈**") \
        and BANNED.endswith("**"), (
        f"BANNED 的结构变了（`**`×{BANNED.count('**')}）⇒ "
        "判据被放宽成子串匹配，误伤面会扩大"
    )
    for part in ("不在成本", "不在测量", "不在样本长度", "在信号本身"):
        assert part in BANNED, (
            f"BANNED 不再含分句「{part}」⇒ 有人把判据削短了；"
            "削短会让它只匹配一句话的重排版本，而第 160 层的失效形状"
            "恰恰是「换一个说法说同一件事」"
        )


def test_the_artifact_still_says_measurement_is_the_limit():
    """判据的上游：产物自己必须仍写着「测不到」。

    ⇒ 这条守卫的依据是**产物**，不是我发明的偏好。
    若产物将来真的给出证伪，这条会先红，
    提醒人「文档与产物的关系断了」（`snapshot-guards-guard-nothing`）。
    """
    import json

    art = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    cannot = art["what_this_cannot_prove"]
    if isinstance(cannot, list):
        joined = "\n".join(str(x) for x in cannot)
    else:
        joined = str(cannot)
    assert "测不到" in joined and "没有超额" in joined, (
        "产物的 what_this_cannot_prove 不再写着「测不到 ≠ 没有超额」⇒ "
        "**判据的上游变了**。此时 CLAUDE.md 那条守卫的依据已经过时，"
        "必须先弄清产物的结论是否真的变了（可能是产物被重跑覆盖），"
        "再决定文档怎么写 —— 不许直接删守卫（`guards-that-pin-existence-not-property`）。"
    )
    # 逐位钉住「五档都不可测」这个事实来源，而不是钉住某一档的数
    widths = art["widths"]
    rows = list(widths.values()) if isinstance(widths, dict) else widths
    undetectable = [r["n"] for r in rows if not r["detectable"]]
    assert undetectable, (
        "现在每一档都 `detectable=True` ⇒ 「瓶颈在测量」这句话本身过期了。"
        "此时应重新读产物并改写文档，**而不是**继续引用旧结论。"
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))

"""CLAUDE.md 声称的「N 层守卫分类」不得与 `MISTAKE_LOG` 正文脱钩。

CLAUDE.md 自己写过这句话：

> ⚠️ **这个数字自己也会腐**：它一度写着「十六种」而正文已到 21。
> 形状 23 之前先记一笔：**散落在正文里的计数会静默过期**，
> 所以「标题数字」本身就是形状 18 的一个实例。

它承认了「无守卫」，本文件补上那条守卫。

★ 为什么这条值得写：这是今天**第二次**被同一形态咬到。
第一次是 2026-10-15 写新层时，我按 CLAUDE.md 里的「91 层」把新层编成 108，
而正文已经到 112 ⇒ **撞号**。两次的成因完全一样：
**外部记忆里的计数 vs 仓内真相源，而真相源在仓里。**
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "docs/MISTAKE_LOG_2026-10-14.md"
CLAUDE = ROOT / "CLAUDE.md"

# 三种标题形态都在正文里真实出现过（先量后写，见 test_doc_citations_resolve 的教训）：
#   ① `## 96. ★ …`            —— 顶层 `## N.` 形式
#   ② `## 第 74 层：…` / `### 77 层附四：…` —— 带「第 N 层」的变体与「层附 N」
#   ③ `# 续：78–81 层（…）`    —— 跨零点那两批用**区间**声明，取区间上界
_HEAD_PATTERNS = (
    re.compile(r"^##\s+(?:第\s*)?(\d+)\s*层", re.M),
    re.compile(r"^###\s*(?:第\s*)?(\d+)\s*层", re.M),
    re.compile(r"^##\s+(\d+)\.\s", re.M),
    re.compile(r"^#\s*续[：:]\s*(\d+)\s*[–\-~]\s*(\d+)\s*层", re.M),
)


def _declared_layers(log_text: str) -> set:
    """正文里**自称**的层号集合。"""
    found = set()
    for pat in _HEAD_PATTERNS[:-1]:
        found |= {int(m) for m in pat.findall(log_text)}
    for lo, hi in _HEAD_PATTERNS[-1].findall(log_text):
        found |= {int(lo), int(hi)}
    return found


def _claimed_layer(claude_text: str) -> int:
    m = re.search(r"\*\*(\d+)\s*层\*\*\s*守卫分类", claude_text)
    assert m, ("CLAUDE.md 里找不到「**N 层**守卫分类」这句话 —— "
               "要么措辞被改写、要么这条断言该跟着改，"
               "**不许**因此判成「没有过期」")
    return int(m.group(1))


def test_the_parser_really_finds_the_layers_rather_than_returning_an_empty_set():
    """★ 正控：解析器一旦改坏就返回空集，主守卫会**永远绿** —— 那比红更危险。

    这不是假想：`test_doc_citations_resolve.py` 的探针第一版把
    `## 3.1 显著性怎么算` 整行当节号，把 3 条好引用全报成坏的。
    ⇒ **探针先被验证，再拿去判别人。**
    """
    log_text = LOG.read_text(encoding="utf-8")
    layers = _declared_layers(log_text)
    # ⚠️ 门槛是**实测值下留余量**，不是「看起来该有多少」。
    # 第一版我写 `>= 100`，当场红 —— 而层号本来就**不连续**：
    # 68~77 层是由 `# 续：78–81 层` 这种**区间**标题声明的，
    # 它只贡献端点 78/81，不贡献区间内的每一个数。
    # 实测 72 个 ⇒ `>= 100` 是**永远红**的守卫，
    # 而永远红的守卫会把人推向「去给日志补 28 个假层号」这个错误动作。
    assert len(layers) >= 60, (
        f"只解析出 {len(layers)} 个层号（实测基线 72）—— "
        f"正则已经和正文脱节。**主守卫此刻正在拿一个残废的探针判过期**，"
        f"它的绿灯没有任何信息。"
    )
    # 三种形态各至少命中一处：这三条是解析器的覆盖面自证
    assert _HEAD_PATTERNS[0].search(log_text), "`## 第 N 层` 形态没抓到"
    assert _HEAD_PATTERNS[1].search(log_text), "`### N 层附N` 形态没抓到"
    assert _HEAD_PATTERNS[3].search(log_text), "`# 续：N–N 层` 区间形态没抓到"


def test_the_mistake_log_actually_declares_the_top_layer_as_a_heading():
    """最高层号必须**真的以标题形式存在**，不能只出现在正文叙述里。

    否则「最大层号」会被某句散文里的「做到第 120 层」顶上去，
    而那条散文随时会被改写。
    """
    log_text = LOG.read_text(encoding="utf-8")
    top = max(_declared_layers(log_text))
    heading = re.search(rf"^##\s+{top}\.\s", log_text, re.M)
    assert heading, (
        f"最大层号 {top} 在正文里被提到，但**没有对应的 `## {top}. ` 标题**。"
        f"⇒ 「最大层号」这个真相源被散文污染了。"
    )


def test_claude_mds_claimed_layer_count_is_not_older_than_the_mistake_log():
    """★ 主守卫（上半）：文档不得**少于**正文 —— 那就是静默过期。

    ⚠️ **为什么拆成两条不等号，而不是一条 `==`**：
    因为这两种失效**方向相反、修法也不同**，值得各自给一条消息：
      「文档落后于正文」 ⇒ 补文档
      「文档超前于正文」 ⇒ 删掉那句悬空的声明

    🚨🚨 **但拆开并不防「有人把断言改松」—— 这一点实测过，别再声称它防。**
    变异 M3 把这条改成 `assert claimed >= 0` ⇒ **全绿**；
    M4 把另一条改成 `claimed <= 9999` ⇒ **也全绿**。

    原因一眼可见：**正常态下 `claimed == max`，两个不等号同时为真**，
    拿掉任何一个，剩下的那个照样为真。
    ⇒ 要让剩下那条接手，必须在**漂移态**下求值；
      而「削弱」这件事本身是在**正常态**下发生的。
      **这是结构性的，不是实现没写好。**

    ⇒ 归类：那属于本仓纪律里「**变异打在守卫的断言对象上**」，
      不算真实失效形态 —— **没有任何测试能抓住自己被削弱**。
      这里留两条的真实收益是**诊断精度**（两种方向给两种消息），
      **不是**防御强度。写清楚这一点，免得下一个人把它当防线。
    """
    layers = _declared_layers(LOG.read_text(encoding="utf-8"))
    claimed = _claimed_layer(CLAUDE.read_text(encoding="utf-8"))
    assert claimed >= max(layers), (
        f"CLAUDE.md 写「**{claimed} 层**守卫分类」，而 "
        f"MISTAKE_LOG 正文已经到 **第 {max(layers)} 层**"
        f"（共 {len(layers)} 个层号）。\n"
        f"⇒ 这就是 CLAUDE.md 自己承认的形状 18 实例：散落计数静默过期。\n"
        f"修法：把 CLAUDE.md 的数字改成 {max(layers)}，"
        f"并把新增的层号补进那句枚举里。"
    )


def test_claude_mds_claimed_layer_count_is_not_ahead_of_the_mistake_log():
    """★ 主守卫（下半）：文档不得**多于**正文 —— 那是一个悬空的声明。

    「文档声称有 120 层、正文只有 113」同样是错的，而且**更坏**：
    读者会去找第 114~120 层，找不到时会怀疑是自己读漏了，
    而不是怀疑那句话本身。

    ⚠️ 上一条的 docstring 已实测说明：拆开**不防**断言被改松。
    这条独立成立，靠的是变异 A2（CLAUDE.md 声称 120 / 正文 113）当场变红。
    """
    layers = _declared_layers(LOG.read_text(encoding="utf-8"))
    claimed = _claimed_layer(CLAUDE.read_text(encoding="utf-8"))
    assert claimed <= max(layers), (
        f"CLAUDE.md 声称 **{claimed} 层**，而 MISTAKE_LOG 正文只到 **第 {max(layers)} 层**。\n"
        f"⇒ 那是个**悬空的声明**：读者去找第 {max(layers)+1}~{claimed} 层会找不到，"
        f"而找不到时他怀疑的是自己。"
    )

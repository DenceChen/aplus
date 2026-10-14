"""第七轮（2026-10-12 22:35）灌一条 **flat**，并**撤回一个被证伪的假设**。

幂等——标题已存在就跳过。

## 这条为什么要写成 flat，而且要专门写一条「假设被证伪」

我当时的推理链是：

    mom20 的 IC 是 −0.0357（t=−9.40，显著）
      + 书 3.1.2 要求构造因子所用收益做 ±10% 复牌压缩
      + 代码里 mom20/rev5 直接用原始价比值，绕过了这道压缩
      + 而 mom20 越高越差 → 复牌暴涨的股票被排到最前，
        正好是策略最确定会亏的那一端
    ⇒ 「IC 为负是复牌极值造成的，压缩之后会好转」

**最后一箭头是错的。** 实测 400 只股票、720,587 行：

    复牌日且 |收益| > 10% 的行：**90 行（0.0125%）**，涉及 81 只
    mom20 修复前/后 绝对值之比：中位数 **1.000**、95 分位 **1.000**
    最大 |mom20|：488% → 380%（只有极端尾部动了）

修复**确实生效**（90 行被压），但它对因子值的实际影响可以忽略。
所以 `mom20` 的负 IC 不是数据缺陷造成的，**是真实的短期反转效应**。

这一条必须留在台账里，因为它同时否定了两件事：
1. 「IC 为负 = 因子坏了」的默认读法；
2. 我自己那套听起来很顺的因果链。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aplus.ledger import ledger_path, load_ledger, record_improvement  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
D = "2026-10-12"

ENTRIES = [
    dict(
        title="复牌首日压缩补进 mom20/rev5：符合书 3.1.2，但实测影响可忽略（flat）",
        category="correctness", severity="medium",
        metric="mom20 修复前后绝对值之比（中位数）", higher_is_better=False,
        before=1.000, after=1.000,
        note=("`mom20` / `rev5` 此前直接用 `_safe_ratio(adj_close, adj_close.shift(k))`，"
              "**绕过了 `RESUME_CLAMP`**——而那道压缩本来就存在，只是只用在了 "
              "`vol20` / `ivol` 上。\n\n"
              "改成「复牌日压缩后的日收益连乘」，并保留三层口径互不替代：\n"
              "    >±40%      数据不可信 → **作废**\n"
              "    复牌首日    合法但任意 → **压到 ±10%**\n"
              "    普通交易日  真实涨跌停 → **不动**\n\n"
              "第三层是**既有测试拦下来的**：第一版实现全局 `clip(±10%)`，"
              "被 `test_factor_layer_still_keeps_legitimate_limit_up_moves` 打红——"
              "创业板 +20% 涨停是真实、可交易、有信息的事件，全局压缩会"
              "**系统性削平所有信号**。压得太宽比不压更糟。\n\n"
              "**但实测影响可忽略**：400 只 / 720,587 行里，"
              "「复牌日且 |收益|>10%」只有 **90 行（0.0125%）**、涉及 81 只；"
              "mom20 新旧绝对值之比中位数 **1.000**、95 分位 **1.000**；"
              "只有极端尾部从 488% 降到 380%。\n\n"
              "结论：**保留这个修复**（书的要求、对正常样本零影响、"
              "去掉一类尾部风险），但**不把它当成任何问题的答案**。"),
        invalidates=["「mom20 的负 IC 源于复牌极值数据缺陷」——2026-10-12 被实测否定，"
                     "负 IC 是真实的短期反转效应，不是数据问题"],
        evidence="aplus/panel.py（resume_day / ret_resume_clamped / _chain_momentum）；"
                 "tests/test_momentum_clamp.py（9 项）；"
                 "400 只实测：复牌极值行 90/720587，|新/旧| 中位数 1.000",
    ),
    dict(
        title="撤回假设：mom20 的负 IC 不是复牌极值造成的（我自己推错了）",
        category="methodology", severity="medium",
        metric="被实测推翻的未验证假设数", higher_is_better=False,
        before=0, after=1,
        note=("当时的推理链听起来很顺：IC 显著为负 → 书要求压缩而代码没压缩 → "
              "压缩后应该好转。\n\n"
              "**前两箭头都对，第三箭头错了。** 修复确实生效（90 行被压），"
              "但对因子值的影响中位数是 1.000。\n\n"
              "教训：**「找到了一个看起来能解释现象的缺陷」不等于「它就是这个现象的原因」**。"
              "两者之间必须有一步实测，否则链条会在听起来最顺的那一环断掉——"
              "而那一环恰恰是最少被人验证的。\n\n"
              "这份平台已经吃过一次同形状的亏：2026-10-11 把 "
              "`n_ranked` 单点采样改成 5 点中位数之后，"
              "顺手推断「池子窄化会消失」——它确实消失了，"
              "但原因是数据缺口当天被补上了，不是采样改对了。"),
        invalidates=["「修好一个符合书的缺陷就能改善 IC/超额」——"
                     "本轮实测：符合书 ≠ 有效果，两者必须分开验"],
        evidence="tests/test_momentum_clamp.py；400 只 × 720,587 行实测对比",
    ),
]


def main() -> int:
    existing = {r.get("title") for r in load_ledger(ROOT / "data")}
    added = 0
    for e in ENTRIES:
        if e["title"] in existing:
            print(f"[跳过 ] {e['title']}（台账里已有）")
            continue
        row = record_improvement(ROOT / "data", dict(e, date=D))
        added += 1
        print(f"[{row['direction']:>6}] {e['title']}\n"
              f"         {e['metric']}: {e['before']} → {e['after']}")
    print(f"\n新增 {added} 条 · 台账现有 {len(load_ledger(ROOT / 'data'))} 条 → "
          f"{ledger_path(ROOT / 'data')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
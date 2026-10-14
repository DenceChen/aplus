"""第六轮（2026-10-12 22:15）灌一条：**日频调仓在 5 万本金上结构性不可行**。

幂等——标题已存在就跳过。

## 这条的形状与前几轮不同：指标没变坏，是**尺度变了**

`up_excess_vs_market` 一直**已经扣掉**成本，所以赛马没有在给净值为负的
候选排名。这一点是对的。但报告里**看不出扣了多少**，于是：

    毛 +0.190% − 成本 0.080% = 净 +0.110%
    毛 +0.111% − 成本 0.001% = 净 +0.110%

这两行在报告里**完全一样**，而它们根本不是同一个策略。

## 实测（97 组候选，2 年窗口）

    K       组数    成本占比中位数      最差      >50% 的组数
    1       14          91%          8305%        12/14
    5       13          30%           194%         3/13
    21      15          16%            51%         1/15
    63       8           9%           105%         1/8

**日频调仓下，手续费吃掉毛超额的中位数是 91%，最差一组 8305%。**

这不是策略选错了，是算术：5 万本金 / 10 只持仓，单只 5000 元，
一次往返约 0.3%；日频换手 27.5% → 每天 0.08% 的拖累，
对着的是 0.05%~0.1%/日 量级的毛超额。**本金越小，成本越致命。**

## 为什么这条必须留在台账里

它作废的是一个**默认设定**：整个平台（含线上默认权重、
前瞻台账第一批候选里的两组 K=1）都默认「每天出单、每天换手」。
而每日推荐和每日换手是**两件事**——推荐是排序产品，
换手是交易决策。日频出单没问题，**日频换手在这笔本金上是送钱**。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aplus.ledger import ledger_path, load_ledger, record_improvement  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
D = "2026-10-12"

ENTRY = dict(
    title="日频调仓在 5 万本金上结构性不可行：成本吃掉毛超额的中位数是 91%",
    category="correctness", severity="critical",
    metric="日频（K=1）候选的成本占毛超额比例中位数", higher_is_better=False,
    before=0.91, after=0.91,
    note=("`rolling_stats` 早就在算净超额时扣掉成本，所以赛马**没有**在给净值为负的"
          "候选排名——但报告里看不出扣了多少，于是两种完全不同的策略"
          "在表里长得一模一样。\n\n"
          "新增 `avg_cost_drag` / `gross_up_excess` / `cost_share` 三个字段后，"
          "97 组候选的实测分布：\n\n"
          "    K=1   14 组  中位数 **91%**  最差 8305%\n"
          "    K=5   13 组  中位数  30%  最差  194%\n"
          "    K=21  15 组  中位数  16%  最差   51%\n"
          "    K=63   8 组  中位数   9%  最差  105%\n\n"
          "**日频调仓下 14 组里 12 组的成本占比超过 50%**——"
          "毛超额的一半以上还没到手就已经付给券商了。\n\n"
          "这是算术不是策略问题：5 万本金 / 10 只持仓 = 单只 5000 元，"
          "一次往返约 0.3%（含印花税），日频换手 27.5% → 每天 0.08% 的拖累，"
          "对着 0.05%~0.1%/日 量级的毛超额。**本金越小，成本越致命。**\n\n"
          "注意 `before=after=0.91`：这条**不是**修好了某个指标，"
          "是让一个一直存在但看不见的事实变得可见。"),
    invalidates=["「日频调仓是可以考虑的默认执行方式」——在 5 万本金上，"
                 "成本占比中位数 91%，该选项不成立",
                 "线上默认权重 K=1 与前瞻台账中两组 K=1 候选的执行假设"],
    evidence="aplus/verify.py（avg_cost_drag / gross_up_excess / cost_share）；"
             "tests/test_cost_reporting.py（13 项）；"
             "scripts/mutation_check.py 组 cost（4/4 全杀）；"
             "/tmp/aplus_snap_1012/reports/ic_guided_race_v1.json",
)


def main() -> int:
    existing = {r.get("title") for r in load_ledger(ROOT / "data")}
    if ENTRY["title"] in existing:
        print(f"[跳过 ] {ENTRY['title']}（台账里已有）")
        return 0
    row = record_improvement(ROOT / "data", dict(ENTRY, date=D))
    print(f"[{row['direction']:>6}] {ENTRY['title']}\n"
          f"         {ENTRY['metric']}: {ENTRY['before']} → {ENTRY['after']}")
    print(f"\n台账现有 {len(load_ledger(ROOT / 'data'))} 条 → {ledger_path(ROOT / 'data')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
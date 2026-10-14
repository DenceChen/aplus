"""第五轮（2026-10-12 晚）灌**一条 better + 一条 worse**进台账。

幂等——标题已存在就跳过，可以反复执行。

## 这一轮的两条不是一回事

1. **代价：现任权重的符号是反的（worse）**。
   平台连续三周每天从一组「三个因子方向反了」的权重出名单。
   这条必须留在台账里，而且必须标 worse——
   台账如果只记「我做了什么改进」，下一个看台账的人会以为
   这三周的每日推荐是有依据的。它们不是。

2. **收获：加了因子 IC 这一维（better）**。
   新增 `aplus/factor_ic.py`。指标是「测量过的因子数」0 → 8，
   这个「指标」本身不产生收益，它的价值是**让上面那条被看见**。

## 为什么值得单独记一条 worse

`INVALIDATES` 字段在这里比任何新发现都重要：
它作废的是**一整段历史里「现任排名靠后就说明它差」这个推理**。
不是现任变差了，是**量它的尺子从来没量过它真正的问题**。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aplus.ledger import ledger_path, load_ledger, record_improvement  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
D = "2026-10-12"

ENTRIES = [
    dict(
        title="现任权重三个因子的符号反了：每天买在预测收益最差的一端",
        category="correctness", severity="critical",
        metric="现任权重中「符号与 IC 相反」的因子数", higher_is_better=False,
        before=0, after=3,
        note=("1821 个交易日逐因子 IC 实测（`aplus/factor_ic.py`，窗口 D+1 买 D+2 卖）："
              "**8 个因子里 7 个显著为负**，只有 rev5（5 日反转）为正。\n\n"
              "    mom20 −0.0357 (t=−9.40)   rev5 **+0.0262 (t=+7.22)**\n"
              "    vol20 −0.0514 (t=−11.28)  vsur −0.0290 (t=−12.57)\n"
              "    turn20 −0.0428 (t=−9.29)  turnratio −0.0378 (t=−12.67)\n"
              "    ivol −0.0590 (t=−16.73)   rsi −0.0366 (t=−9.87)\n\n"
              "逐年看：8 个因子 × 8 个年份，**同号率 100%**——不是行情噪声。\n\n"
              "现任 `{mom20:1.0, rev5:0.3, vol20:0.5, vsur:0.5}` 给 mom20 / vol20 / vsur "
              "三个**负 IC 因子挂了正权重**。它的 −0.54%~−0.85%/日不是运气不好，"
              "是结构性地买在预测收益最差的一端。\n\n"
              "**为什么三周里七场赛马都没发现**：84 组候选里 9 组是 mom20 正权重、"
              "只有 3 组测负动量、没有任何一组同时用「负 vol20 + 正 mom20」。"
              "候选池在**生成阶段**就漏掉了数据指认的那块搜索空间——"
              "这不是调参没调好，是搜索空间缺了一块。"),
        invalidates=["「现任排名 #79/84 → 它只是差一点」——2026-10-12 起不成立，"
                     "它的三个权重符号与预测方向相反，与排名无关",
                     "所有「现任 vs 候选」比较里隐含的「现任方向大致正确」这一前提",
                     "2026-10-12 之前基于现任权重做出的任何「再调调权重也许能好」的打算"],
        evidence="data/reports/factor_ic_7y.json（1821 日 / 8 因子 / 4 窗口，816s）；"
                 "scripts/factor_ic_diagnostic.py；"
                 "aplus/factor_ic.py；tests/test_factor_ic.py（32 项）",
    ),
    dict(
        title="加了因子 IC 这一维：赛马答不了「因子有没有预测力」",
        category="methodology", severity="high",
        metric="被独立测量过预测力的因子数", higher_is_better=True,
        before=0, after=8,
        note=("此前所有实验都问「哪组权重最好」，一次都没问过"
              "「这个因子本身有没有预测力」。赛马**结构上答不了**："
              "它只能在候选池里排序，而候选池里没有符号正确的那个组合。\n\n"
              "新增 `aplus/factor_ic.py`：逐日逐因子 Spearman IC，三条口径钉死——\n"
              "- 因子取 `build_cross_sections` 的输出，策略看到什么就量什么\n"
              "- 真实窗口 D+1 收盘买 → D+2 收盘卖，与 `_next_bars` **逐位相等**（有测试）\n"
              "- 只在可成交样本上算（停牌 / 非正价 / 超 ±40% 剔除）\n\n"
              "外加两件赛马没有的：\n"
              "1. **尾部分段 IC**——策略只买 top-10（0.2%），整体 IC 高不代表用得上。\n"
              "   rev5 的低尾 IC = +0.0486（t=+24.03），信号高度集中在「5 日跌最多」那一端；\n"
              "   而 vsur 高尾 IC = −0.0499（t=−30.31），意思是买它的极值就是接盘。\n"
              "2. **泄露探针**——顺带量出 `entry_lag=0` 虚高 **1.13~1.68 倍**。\n"
              "   2026-10-11 把它改成 1 时只知道「0 是错的」，不知道错了多少。\n\n"
              "t 值按**持有期**分块：hold21 的相邻 IC 共享 20 天数据，"
              "逐日 t 会虚高一个量级且不报任何错。"),
        invalidates=["「赛马排第一 = 这组权重有效」——2026-10-12 起降级为"
                     "「在候选池里排第一」，候选池的符号结构必须单独审查"],
        evidence="aplus/factor_ic.py；tests/test_factor_ic.py（32 项）；"
                 "scripts/mutation_check.py 组 ic（7/7 全杀）",
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
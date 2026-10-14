"""第八轮（2026-10-12 23:35）灌两条：一条 better（首个过闸候选），一条 worse（晋级的代价）。

幂等——标题已存在就跳过。

## 这一轮的核心张力

7.8 年 IC 引导赛马（97 组，训练 2019-2023 / 测试 2023-2026）**首次有候选过闸**：

    {mom20: -1.0}  K=63（纯 20 日反转 + 季频）
    测试超额 +0.1196%/日   t=+4.14 > 3.47（97 次 Bonferroni）
    成本占比 4.3%（对比 K=1 的 95%）
    n_ranked 4904，与现任池子可比

按平台自己的协议，它现在「够格晋级」。**但我没有晋级**，理由如下，
而且这个理由比结论本身更重要：

    训练/测试切分挡住了「权重拟合」的泄露，
    但挡不住「候选族的存在是被全窗口信息决定的」这一层。

我先跑了 `factor_ic_diagnostic`（**2019-01 ~ 2026-10 全窗口**），
从它的结果推断「符号该反过来」，**于是才去构造 ic_guided 这一族候选**。
权重确实只在训练段上拟合，但候选族的**存在**是被全窗口决定的。
所以这一场的「样本外」没有它自称的那么干净。

外加两条实测的弱点：有效观测只有 **11 个**、广度 t=**+1.09 不显著**
（超额几乎全部来自涨腿，排序的两端分离能力未被证明）。

处置：开**第二批**前瞻注册（4 组），与第一批一起等 2026-10-13 之后的窗口。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aplus.ledger import ledger_path, load_ledger, record_improvement  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
D = "2026-10-12"

ENTRIES = [
    dict(
        title="首个过闸候选：纯20日反转 + 季频，t=+4.14，成本占比 4.3%",
        category="methodology", severity="high",
        metric="7.8 年赛马中过闸的候选数", higher_is_better=True,
        before=0, after=1,
        note=("97 组候选（84 标准 + 13 IC 引导），训练 2019-01~2023-08、"
              "测试 2023-08~2026-10，裁决按训练分**全表扫描**，逐一检查 6 组后命中第 6 名：\n\n"
              "    {mom20: -1.0}  K=63\n"
              "    测试超额 +0.1196%/日   t=+4.14 > 3.47（97 次 Bonferroni）\n"
              "    成本占比 4.3%          n_ranked 4904（现任池子可比）\n\n"
              "**为什么它不是第一个被想到的**：它需要 K=63，而第一批前瞻候选里\n"
              "有 `{mom20:-1.0}` K=1 和 K=5 缓冲3.0，**唯独没有 K=63**。\n"
              "同一组权重在 K=1 下成本占比 95%、在 K=63 下 4.3%——差 20 倍。\n"
              "在 5 万本金上，**调仓频率不是执行细节，它就是策略本身**。\n\n"
              "第 4、5 名 t 更高（+4.35 / +5.04）却没过：它们需要 `turnratio`，\n"
              "该列在部分股票上仍缺，池子窄到 4567 vs 现任 4904，被股票池闸拦下。"
              "数据修复把窄化从 9.2% 降到 6.9%，**但没归零**。"),
        invalidates=["「平台从未有过任何过闸候选」——2026-10-12 起不成立，"
                     "已有一个（尽管尚未晋级实盘）"],
        evidence="data/reports/ic_guided_race_7y.json（97 组 · 五条腿全过 · 4195s）；"
                 "scripts/ic_guided_race.py",
    ),
    dict(
        title="首个过闸候选也**没有**晋级：候选族的存在是被全窗口信息决定的",
        category="correctness", severity="critical",
        metric="已晋级实盘但候选族曾被全窗口信息影响的候选数",
        higher_is_better=False,
        before=0, after=0,
        note=("这一条记的是**没做**的事：赛马说晋级，我没有晋级。\n\n"
              "泄露链条：\n"
              "    ① factor_ic_diagnostic 跑的是 **2019-01 ~ 2026-10 全窗口**\n"
              "    ② 我从它的结果推断「符号该反过来」\n"
              "    ③ **于是才去构造 ic_guided 这一族候选**\n"
              "    ④ 再用 2019-01~2023-08 训练 / 2023-08~2026-10 测试去赛马\n\n"
              "权重确实只在训练段上拟合——但**候选族的存在是被全窗口决定的**。\n"
              "训练/测试切分挡住的是「用测试段挑权重」，挡不住\n"
              "「我已经知道整个样本期的规律，于是只造了符合这个规律的候选」。\n\n"
              "另两条弱点也要一起记：\n"
              "· **有效观测只有 11 个**（测试段 3.9 年 ÷ 63），t=+4.14 建在 11 个块上\n"
              "· **广度 t=+1.09 不显著** —— 超额几乎全部来自涨腿，\n"
              "  排序本身「把两端分开」的能力没有被证明\n\n"
              "处置：开**第二批**前瞻注册（4 组，含该候选），"
              "与第一批共 10 组一起等 2026-10-13 之后的窗口。\n"
              "**weights.json 仍然不存在。**\n\n"
              "教训：样本外切分保护的是「拟合」，不是「提问」。\n"
              "如果你已经用全窗口数据决定要问什么问题，那么切分得再干净也没用——\n"
              "唯一能救的是**预注册**：在看到答案之前把问题冻住。"),
        invalidates=["「测试段通过 = 该候选干净」——对 ic_guided 这一族不成立，"
                     "它们的候选族来自全窗口 IC"],
        evidence="scripts/register_forward_candidates_r2.py（第二批 4 组）；"
                 "data/reports/forward_registry.json（2 份注册 / 10 组候选）；"
                 "data/reports/factor_ic_7y.json",
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
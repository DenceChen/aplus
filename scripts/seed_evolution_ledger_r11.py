"""第十一轮（2026-10-14 10:10）灌三条：缺陷 26 修复、PIT 阻塞判决、Q2 定论。

幂等——标题已存在就跳过。

## 这一轮的三条性质各不相同，必须分开看

1. **缺陷 26** 是 `better`：修了一个真实的、可测量的数据缺陷。
2. **PIT 判决** 是 `flat`：**堵死**一条路。把一条看起来最有希望的路
   （FAR，书中 t=7.60）在动手之前证伪，比做完再发现便宜得多。
3. **Q2 定论** 也是 `flat`：一个悬了三天的口径问题有了答案，
   而且答案对现有因子**没有影响**——这本身值得记，否则以后还会有人重新怀疑一遍。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aplus.ledger import ledger_path, load_ledger, record_improvement  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
D = "2026-10-14"

E1 = dict(
    title="缺陷 26：turnratio 因 rolling(252) 要求零缺失而丢掉 482 只股票（覆盖中位 96%）",
    category="correctness", severity="high",
    metric="turnratio 可打分股票数（2025-03-20 真实横截面）", higher_is_better=True,
    before=4662, after=4840,
    note=("`t.rolling(252).mean()` 的 `min_periods` 默认等于窗口长度，"
          "即 252 根里一根都不能缺。而源站 `turn` 列有零散缺失，"
          "一只上市三年、覆盖率 96% 的股票，中间只缺 1 天，整个因子就是 NaN。\n\n"
          "取证过程与两次假设修正：\n\n"
          "  ① 第一次假设「剔掉的是次新股，符合书的次新 1 年过滤」——**实测否掉**：\n"
          "     242 只里只有 23.1% 上市不足 252 日，**180 只（74.4%）上市超 500 日**\n"
          "  ② 继续量 `turn` 的非空根数，定位真因：\n"
          "     已上市 ≥252 日的 5235 只 → 算不出 482 只，**覆盖率中位 96.0%**\n"
          "     只缺 1 根的就有 **122 只**\n\n"
          "**修复**：`MIN_TURN_COVERAGE = 0.9`。门槛是先验的完整性判据，"
          "不是为让某组候选过闸调出来的——无缺失样本结果与原实现**逐位相同**"
          "（`tests/test_turnratio_coverage.py` 钉死）。\n\n"
          "修复后：被剔 242→**64**，其中次新占比 23.1%→**71.9%**，"
          "上市>500 日却剔的 180→**17 只**，年度收窄 5.4~8.3%→**1.7~4.1%**。\n"
          "残留的收窄重新变成以次新为主——正是书里明确的 1 年过滤该有的样子。\n\n"
          "**为什么重要**：全池 t 最高的 `{mom20:-0.5,turnratio:-0.5}` K=63"
          "（t=+5.04）此前被「数据缺失导致不可比」拦下，而那个理由是可修的瑕疵。\n\n"
          "**纪律**：前瞻窗口当时仍 0 天，改定义不损失已累积证据；"
          "该候选 2026-10-12 已冻结注册；候选数 97 与 Bonferroni 门槛不变。"),
    invalidates=["「turnratio 的股票池收窄来自 252 日预热期的定义属性，修数据归不了零」"
                 "——2026-10-13 的结论，2026-10-14 被实测推翻："
                 "74.4% 的收窄来自零散缺失日，是可修的数据瑕疵"],
    evidence="data/reports/universe_forensics_fixed.json；"
             "tests/test_turnratio_coverage.py；scripts/universe_forensics.py",
)

E2 = dict(
    title="PIT 判决：FAR/BM/ROE 因后修财务**做不到真 PIT**，可安全进回测的财务因子归零",
    category="data-integrity", severity="critical",
    metric="可安全进入样本外回测的财务因子候选数", higher_is_better=True,
    before=3, after=0,
    note=("FAR（基本面锚定反转）在书中 A 股实证 t=7.60，是全章最高单因子，"
          "本打算作为第二信号族的主力。动手前的 PIT 可行性调研给出否定判决。\n\n"
          "    报告期与披露日是两个独立字段          ✅ 可行\n"
          "    按 NOTICE_DATE ≤ as_of 切片           ✅ 机械可行\n"
          "    **切出来的值 = 当天真实可得的值**      ⛔ 不成立\n\n"
          "数据源每个报告期只存一行（`REPORT_DATE` 重复数 = 0），"
          "而 `UPDATE_DATE > NOTICE_DATE` 成立 ⇒ 被服务的值**必然是后修值**，"
          "原始披露值在源里不存在。东财 vs 同花顺 28 个年度对比最大差 0.18%。\n\n"
          "    修订量级 20.2% 的报告期被改过，滞后中位 364 天\n"
          "    逐 as_of 活污染均值 1.48%\n\n"
          "**与第 14 号缺陷（ivol 的 `.iloc[-1]`）是同一个形状**：不崩、不报 NaN、"
          "横截面完全正常、`no_future_leak` 测试会通过。\n"
          "`PLATFORM.md §0` 已写死「后修财务 → 禁止入回测」。\n\n"
          "**1.48% 不能当噪声读**：做追溯调整的公司恰恰是有会计问题/业绩变脸的，"
          "而那正是 F-Score 要识别的子集（此为推断，未实测相关性）。\n\n"
          "**方向是 worse，这是有意的**：台账必须能看见倒退，"
          "否则「堵死一条错路」会��记成「没进展」。\n\n"
          "**结论：在白名单过滤（`UPDATE_DATE == NOTICE_DATE`）或攒 vintage "
          "两条路做出决议之前，不写 FAR/BM/ROE 的任何代码。**\n"
          "先把 `factor_spec_cn.md` §1.5 从「未接入」改成实测结论。"),
    invalidates=["「FAR 是下一条最有希望的信号来源，等财务数据到位就实现」"
                 "——2026-10-14 之前成立；实测证明当前数据源下它不可实现，"
                 "缺的不是工程量而是数据源的历史版本"],
    evidence="docs/factor_spec_cn.md §1.5.1（实测记录 + 9 字段映射表 + 两条出路）",
)

E3 = dict(
    title="Q2 定论：turn 的分母是流通 A 股，不是总股本（口径判定从 0% 到 100% 确定）",
    category="correctness", severity="medium",
    metric="turn 分母口径被唯一确定的样本比例", higher_is_better=True,
    before=0.0, after=1.0,
    note=("悬了三天的口径问题今天定死。此前**没有任何一个口径被证实**，"
          "分母是总股本还是自由流通股本一直悬着（0% 确定）。\n\n"
          "反推 `隐含股本 = volume × 100 / turn` 再与巨潮股本按时点 join：\n\n"
          "    601988.SH 中国银行  隐含 210,762,220,941  /总股本=0.6543  /A股=1.0000\n"
          "    601939.SH 建设银行  隐含   9,593,683,108  /总股本=0.0367  /A股=1.0000\n\n"
          "**凡有数据的 76 只，`/A股` 全部为 1.0000**（1.0 = 全部命中唯一口径）；"
          "`/总股本` 在 0.0367–1.0000 间大幅漂移，总股本口径可确定地排除。\n\n"
          "带 H 股的公司分母**既不是总股本也不是「已流通股份」**（后者含 H 股），"
          "而是**人民币普通股**——只留前两条腿会把判定做错。\n\n"
          "抽样汇总 `{ambiguous: 53, unknown: 4, a_shares: 3}`。`ambiguous` 不是没结论——"
          "那 53 只的「已流通股份」与「人民币普通股」数值完全相同（无 H 股），两条腿分不开；"
          "4 只 `unknown` 全是**已退市**票。\n\n"
          "**结论对现有因子没有影响，但必须记**：`turnratio` 是比值，"
          "对分母的乘法缩放不敏感；`turn20`（水平值）会受影响，而它是现有因子之一。"
          "记下来是为了以后不必重新怀疑一遍。"),
    invalidates=["「turn 的分母口径未知，可能是总股本」——2026-10-14 定论为流通 A 股"],
    evidence="aplus/sources/shares.py（`classify_denominator`）；"
             "tests/test_shares.py（42 项）；data/master/shares/*.parquet",
)


def main() -> int:
    for entry in (E1, E2, E3):
        ledger = load_ledger(ROOT / "data")
        if entry["title"] in {r.get("title") for r in ledger}:
            print(f"[跳过 ] {entry['title']}")
            continue
        row = record_improvement(ROOT / "data", dict(entry, date=D))
        print(f"[{row['direction']:>6}] {entry['title']}")
    total = load_ledger(ROOT / "data")
    print(f"\n台账现有 {len(total)} 条 → {ledger_path(ROOT / 'data')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

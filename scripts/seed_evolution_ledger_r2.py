"""把第二轮（20:40 ~ 21:20）**实测**的改进灌进台账。

与 `seed_evolution_ledger.py` 一样：每条 before/after 都是当天真跑出来的数字。
幂等——标题已存在就跳过，可以反复执行。

**这里有一条 `worse` 必须留着**：低频候选的 t 值从 13.12 掉到 6.46。
数字变差，但那是因为原来的数字是**按假样本量算出来的**。
把虚高修成真实，正是这个台账存在的意义。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aplus.ledger import ledger_path, load_ledger, record_improvement  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
D = "2026-10-11"

ENTRIES = [
    dict(
        title="赛马候选池加入调仓频率与缓冲区（复合候选）",
        category="methodology", severity="critical",
        metric="候选身份字段数（权重 / 频率 / 缓冲）", higher_is_better=True,
        before=1, after=3,
        note=("独立验证（2019-2023，962 日）显示同一组 `{mom20:-1.0}`："
              "日频净 −168.9%、周频 −5.3%、月频 +77.6%、季频 +105.7%——"
              "**276 个百分点全部来自 rebalance_every 这一个参数**。"
              "而候选池里只有权重，于是赛马永远在「已知不可行的执行方式」下"
              "比较信号强弱，挑出来的权重再送进那个不可行的执行方式。"
              "指标含义：候选的身份由 1 个字段决定，变成 3 个字段决定。"),
        invalidates=["「先修好权重、再调频率」——这个顺序本身是错的",
                     "候选池只含权重时的一切排名（排名只在日频口径下成立）"],
        evidence="aplus/evolve.py: split_weights / candidate_key / dedupe_candidates；"
                 "tests/test_composite_candidates.py + test_race_composite.py",
    ),
    dict(
        title="低频候选的显著性按调仓周期分块（t 值下降）",
        category="correctness", severity="critical",
        metric="反转+风控 K=63 的 t 值", higher_is_better=False,
        before=13.12, after=6.46,
        note=("**这是一条 worse，必须留着。** K 日调仓时一天里「选了谁」不变，"
              "把 K×n 个交易日当成 K×n 个独立观测，等于把同一个决策数了 K 遍。"
              "逐日序列报 t=13.12，按 63 日块均值聚合后是 6.46。"
              "理论虚高上限是 √63≈7.9 倍，实测只有 2.03 倍——"
              "因为逐日序列里的个股噪声在块内被平均掉了一部分。"
              "**我事先担心得比实际严重，照实记。**"
              "对照：K=63 只有 18 个独立观测，t=6.46 仍然显著。"),
        invalidates=["「反转+低异常换手 K=63 t=12.49」等逐日口径的 t 值",
                     "任何以「交易日数」为样本量的低频显著性陈述"],
        evidence="scripts/_verify_freq_block_t.py → data/reports/frequency_block_t_2019_2023.json；"
                 "tests/test_block_tstat.py（8 测试，4/4 变异体被杀）",
    ),
    dict(
        title="退化序列报出 t=3.1e16，显著性硬闸从未生效",
        category="correctness", severity="critical",
        metric="常数序列上报的 t 值（None 记作 0，越小越诚实）", higher_is_better=False,
        before=3.10e16, after=0,
        note=("`if sd <= 0` 写了，`breadth_tstat` 的注释还专门解释了为什么必须这么写，"
              "但 **pandas 的 `std(ddof=1)` 对常数序列返回 3.5e-18 而非 0**，"
              "这一支从未成立过。实测 t = 0.02/(3.5e-18/√30) = 3.10e16，"
              "一个完全测不出差别的序列凭空报出能闯过 Bonferroni 硬闸的 t。"
              "760 个测试全绿，没有一条测到这个形状。"
              "判据改成变异系数：`sd <= |mean| * 1e-12` 即判无法测量。"),
        invalidates=["「显著性硬闸拦住了一切退化序列」"],
        evidence="aplus/evolve_race.py: DEGENERATE_CV / _tstat；"
                 "tests/test_block_tstat.py::test_constant_series_returns_none_not_a_huge_t",
    ),
    dict(
        title="独立验证表里 8 行建立在空股票池上",
        category="correctness", severity="high",
        metric="验证表中 n_ranked=0 的候选行数", higher_is_better=False,
        before=8, after=0,
        note=("`反转+低异常换手`（`turnratio`）在 2019-2023 的 `n_ranked = 0`——"
              "该列在这 5 年根本不存在。其中包括当时被当成「净收益最高 +114.5%」"
              "的那一行。之所以能被当成结论，是因为 `n_ranked` 排在表的最后一列。"
              "**一张表里最该先看的列，往往是排在最后的那列。**"),
        invalidates=["「反转+低异常换手 K=63 净 +114.5%」",
                     "「反转 + 低异常换手在干净数据上被验证过」"],
        evidence="data/reports/reversal_freq_2019_2023.json（n_ranked 列）→ "
                 "frequency_block_t_2019_2023.json（候选已剔除）",
    ),
    dict(
        title="现任被只按权重认，会被同权重的月频行冒名顶替",
        category="correctness", severity="high",
        metric="裁决层认现任时使用的身份字段数", higher_is_better=True,
        before=1, after=3,
        note=("候选池加入频率之后，`r[\"weights\"] == champion_weights` 会让"
              "同权重的月频行在裁决里**坐现任的席位**——"
              "两者净值差着一整个成本量级。去重也只看权重，"
              "于是日频与月频被当成同一组，抹掉 276 个百分点的差距。"
              "附带一条：5 参 evaluate 遇到带频率的候选**当场报错**而不是静默忽略，"
              "静默忽略等于把月频候选当天频跑且看不出来。"),
        invalidates=["候选池含频率时的任何按权重匹配的裁决与去重"],
        evidence="aplus/evolve_race.py: _row_key / _eval_accepts_execution_params；"
                 "tests/test_race_composite.py（13 测试，5/5 变异体被杀）",
    ),
    dict(
        title="建了变异测试工具（本轮抓出 3 个存活变异体）",
        category="process", severity="medium",
        metric="存活变异体数（9 个变异体）", higher_is_better=False,
        before=3, after=0,
        note=("`scripts/mutation_check.py`：9 个变异体全部被杀。"
              "其中 3 个第一轮存活——两个是变异体打到了测试覆盖不到的分支，"
              "**一个是测试真的写得太弱**（只断言「理由里不含某句话」，"
              "认对现任与认错现任都满足）。"
              "还有一个是变异体「文本变了但效果是 no-op」："
              "`get(key, 3.0)` 在键存在且值为 None 时照样返回 None。"),
        invalidates=["「本轮的防护测试确实在防护」——在变异测试之前这是一句没被检验的话"],
        evidence="scripts/mutation_check.py（block_tstat 4/4，composite 5/5）",
    ),
    dict(
        title="判决板补「调仓」列（此前藏着一个决定性变量）",
        category="observability", severity="medium",
        metric="判决板显示的候选身份字段数", higher_is_better=True,
        before=2, after=3,
        note=("候选池升到 84 组之后，同一组权重会以 K=1/5/21/63 出现 6 次，"
              "而表格里看不出区别——**276 个百分点的差距全在被隐藏的那一列里**。"
              "补列时才意识到：这不是「加个显示」，是**藏了一个决定性变量**。"),
        invalidates=["2026-10-11 20:50 之前判决板表格显示的候选是可区分的"],
        evidence="aplus/web/index.html: cadence()；真实浏览器验证控制台 0 error",
    ),
    dict(
        title="裁决只检查第 1 名，而门槛按 84 组校正",
        category="correctness", severity="critical",
        metric="被七道闸实际检查的候选数（门槛按 84 组 Bonferroni 校正）",
        higher_is_better=True,
        before=1, after=84,
        note=("`oos_decision` 过去是 `best = table[0]`，而显著性门槛按 "
              "`bonferroni_t(len(table))` 算——**校正 84 次比较，只检验 1 组**，"
              "被校正的比较有 83 次根本没发生。\n"
              "复合赛马暴露了它：第一名 `{mom20:-0.5, rev5:0.5}` K=63 的训练毛超额 "
              "**0.19%**，与 K=1 候选的 0.15%、K=5 的 0.08% 基本持平；"
              "排名差距几乎全来自命中率——而命中率在 K=1（明天涨没涨）"
              "与 K=63（这 63 天涨没涨）之间根本不是同一个量。\n"
              "改成按训练分顺序逐个过闸、命中第一个全过的即晋级，"
              "门槛一个字没改。结果新增 `examined` 字段如实报告检查了几组。\n"
              "**2 年窗口上扫描前后都是 0 晋级**——可见这一修正在该窗口不改变结论，"
              "只在其他窗口里不让好候选被漏掉。"),
        invalidates=["「训练段第一名的目标分就是应该被检验的那个」",
                     "2026-10-11 21:15 之前所有只看 table[0] 的裁决"],
        evidence="aplus/evolve_race.py: oos_decision / _judge_candidate；"
                 "tests/test_decision_scan.py（7 测试，4/4 变异体被杀）",
    ),
    dict(
        title="一条靠运气才杀得掉变异的测试（功效只有 50%）",
        category="process", severity="medium",
        metric="变异体被杀的概率", higher_is_better=True,
        before=0.50, after=1.00,
        note=("「Bonferroni 只按 1 组算」这个变异体第一次跑就**存活**。"
              "原因是我那条测试用 `t ~ N(0, 1.5)` 造 7 个随机候选，"
              "指望至少一个落进 (1.96, 2.75) 窗口：单个概率 9.5%，"
              "7 个全落空 **50%**。\n"
              "改成全部 `t = 2.2` 的确定性构造后必然区分得开。\n"
              "**一条一半概率才杀得掉变异的测试，等于没有。**"),
        invalidates=["「变异体全杀」这个结论在小样本量下未经检验"],
        evidence="tests/test_decision_scan.py::"
                 "test_bonferroni_threshold_is_still_over_the_whole_table",
    ),
    dict(
        title="实盘持仓状态机：每天照常出名单，另附该不该动手",
        category="methodology", severity="critical",
        metric="实盘可执行配置的能力（支持的执行参数种数）", higher_is_better=True,
        before=0, after=2,
        note=("此前实盘只有权重、没有频率与缓冲区：若赛马晋级出「反转 + 月频」，"
              "实盘会照样每天出 10 涨 10 跌并**按日频全换执行**，"
              "而那个配置的净值是按月频算的——独立验证里同一组权重"
              "日频净 **−168.9%**、月频 **+77.6%**，**方向都反了**。\n"
              "这里刻意**不**把推荐也降频：用户要的是每天的涨跌名单，那是**排序**产品；"
              "调仓频率管的是**交易**。所以每天照常出单，另外附一段持仓指令。\n"
              "两层护栏：`live_execution_status` 回答「线上在执行声明的配置吗」；"
              "`live_rebalance` 回答「今天该不该动手、该动哪几只」。\n"
              "`every=1` 时指令返回空字符串、日频行为与今天之前逐字相同（有测试钉死）。"),
        invalidates=["「实盘可以执行任何回测出来的配置」——在这之前它做不到"],
        evidence="aplus/live_rebalance.py；tests/test_live_rebalance_state.py"
                 "（19 测试，5/5 变异体被杀）；scripts/run_daily.py 端到端跑通",
    ),
    dict(
        title="bool 穿透了 isinstance(x, int) 的校验",
        category="correctness", severity="medium",
        metric="被 Python 静默接受的非法状态种数", higher_is_better=False,
        before=1, after=0,
        note=("`load_state` 写的是 `isinstance(data[\"days_since_rebalance\"], int)`，"
              "而 **Python 里 `bool` 是 `int` 的子类**——`True` 通过校验，"
              "被当成「距上次调仓 1 天」。\n"
              "同一个项目在 `save_state` 与 `aplus/ledger.py::_validate` 里"
              "都写了 `isinstance(x, bool)` 排除，**唯独 `load_state` 漏了**。"
              "三处写法不一致，第三处是最后写的。"),
        invalidates=["「状态校验拦得住非法输入」"],
        evidence="aplus/live_rebalance.py: load_state；"
                 "tests/test_live_rebalance_state.py::test_state_with_invalid_counter_is_rejected",
    ),
    dict(
        title="股票池闸的输入只看窗口第一天（数据起点报 0）",
        category="correctness", severity="high",
        metric="n_ranked 取样点数量", higher_is_better=True,
        before=1, after=5,
        note=("`n_ranked = scoreable_count(跨段[第一天])`——**单点**，"
              "而它是第六道闸（股票池可比）的输入。\n"
              "跑 7.8 年全历史赛马时窗口从 2019-01-02 开始，那是**数据集的第一天**："
              "那天没有任何股票有可计算的 mom20（20 日窗口需要 20 个交易日历史），"
              "于是现任训练段报 `n_ranked = 0`。\n"
              "这次**没造成误判**（闸门读的是测试段的 4805），"
              "但它说明**闸门可以拿到退化样本而不会知道**——"
              "它只会安静地拿 0 去比，然后报「股票池空了」。\n"
              "改为跨 5 个等距日期取**中位数**：中位数对「少数日子不可算」稳健，"
              "平均不是（实测：前 10 天空后 10 天各 200 只，中位数 200、平均 100）。"),
        invalidates=["「股票池闸的输入一定是有代表性的样本」"],
        evidence="aplus/backtest.py: sampled_ranked_count；tests/test_n_ranked_sampling.py"
                 "（5 测试，3/3 变异体被杀）",
    ),
    dict(
        title="全历史赛马找到一个过闸候选，但**主动不晋级**",
        category="methodology", severity="critical",
        metric="7.8 年窗口 84 组候选中过闸的组数（污染原因下不采纳）",
        higher_is_better=False,
        before=0, after=1,
        note=("`{mom20:1.0, rev5:0.3, turn20:-1.0}` K=63：测试超额 +0.13%、"
              "**t=+4.00 > 临界 3.43**（84 次 Bonferroni）、**广度 t=+2.90 为正**、"
              "n_ranked 4798 vs 现任 4805 可比。五条腿全过，扫描在第 13 个候选上找到它。\n"
              "**仍然不晋级**，三条各自独立成立的理由：\n"
              "① 测试段 2023-08~2026-10 与我今天看过两遍的 2 年赛马测试段重叠；\n"
              "② 方向不是预注册的——赢家是正动量+低换手+季频，"
              "不是我从独立验证提出的反转方向，方向污染闸按规则不拦它，"
              "但**规则没覆盖不等于没有污染**；\n"
              "③ K=63 只有 11 个独立观测，t=4.00 意味着 mean/sd 仅 1.21。\n"
              "同一轮里两条 `turnratio` 候选的 t 也过门槛，"
              "但股票池窄 9.2% 被第六道闸拦下——闸门按设计工作。\n"
              "**这条记为 better 是错的**：候选数从 0 变成 1 本身不是改善，"
              "而是「系统找到了它但拒绝采纳」的证据。"),
        invalidates=["「七道闸全过就可以写 weights.json」——还差一条：污染检查"],
        evidence="data/reports/race_composite_full_7y.json（84 组，examined=13，"
                 "总计 3773s）；docs/FINDINGS_2026-10-11.md 第 3.5 节",
    ),
    dict(
        title="判决板拿季频的成绩描述日频策略（同一错误第三副本）",
        category="correctness", severity="critical",
        metric="「现任只按权重认」的错误副本数", higher_is_better=False,
        before=3, after=0,
        note=("同一个错误一天里犯了三次，每次都在我没看的地方：\n"
              "① `oos_decision` 认现任 → 月频行冒名顶替日频现任（21:15 修）\n"
              "② `dedupe_candidates` 去重 → 日频与月频当成同一组（21:40 修）\n"
              "③ **`webapp._live_oos`** → 判决板拿 K=63 的成绩描述日频策略（22:30 修）\n"
              "第三个是**真浏览器截图**抓到的：把 84 组复合报告拷进 reports 后，"
              "绿色框从「样本外显著为负 −0.598% t=−2.83」变成「无法区分于 0 t=—」。\n"
              "根因：现任权重在 84 组候选里出现 **6 次**，只按权重匹配会返回"
              "表里第一个匹配行。\n"
              "**教训：改了一个地方的匹配规则，要去 grep 所有做同类匹配的地方。**"),
        invalidates=["「三处任选一处修好就够了」"],
        evidence="aplus/webapp.py: _live_oos；tests/test_live_health.py（15 测试）",
    ),
    dict(
        title="赛马报告有两种行布局，读方只认一种",
        category="correctness", severity="critical",
        metric="读报告的代码支持几种行布局", higher_is_better=True,
        before=1, after=2,
        note=("`evolve_race_fast.py` 写 `row[\"test\"][\"stats\"]`，"
              "`evolve_race_composite.py` 写顶层 `row[\"test_stats\"]`。\n"
              "`_live_oos` 只读前者，于是对 84 组复合报告**一直返回 t=None**，"
              "而那批报告里现任日频行 t=−7.56。\n"
              "本项目读赛马报告的地方已有三个"
              "（`champion_health.collect_windows` / `_live_oos` / "
              "`evolve_weekly.build_log_record`），我加了新写入方后只检查了其中一个。\n"
              "**加新的报告写入方之后，第一件事是把所有读方跑一遍。**"),
        invalidates=["「新增报告写入方不会影响既有读方」"],
        evidence="aplus/webapp.py: _live_oos；"
                 "tests/test_live_health.py::test_live_oos_reads_the_flat_test_stats_shape",
    ),
    dict(
        title="末根元数据缺口闭合（turn 62%→95.6%，turnratio 38%→98.8%）",
        category="data-quality", severity="critical",
        metric="末根 turn 覆盖率（1200 只随机样本）", higher_is_better=True,
        before=0.620, after=0.956,
        note=("今天上午的 `repair_tail_cols` 把这条补上了。\n"
              "**它为什么重要**：7.8 年复合赛马里 t=+4.27（K=21）与 t=+4.37（K=63）"
              "两个 `turnratio` 反转候选，本来是那轮最强的两个，"
              "却因为 `n_ranked` 4362 vs 现任 4805（**窄 9.2%**）被第六道闸拦下——"
              "而那个窄化是**纯数据缺失**造成的系统性窄化，不是设计使然。\n"
              "数据补上之后，股票池闸不再有理由拦它们。**闸门是对的，"
              "当年拦下的是数据问题而不是策略问题**——这正是第六道闸的设计意图。\n"
              "剩余 4.7% 缺失的股票，末根日期大多停在 2021~2024（退市/长期停牌），"
              "只有 1 只在当日缺 turn；对可交易股票而言缺口已闭合。"),
        invalidates=["「turnratio 族候选只覆盖 38% 的股票」",
                     "「反转 + 低异常换手在 2019-2023 无该列、n_ranked=0」——"
                     "那是数据缺口，不是因子无效"],
        evidence="1200 只随机样本实测；data/logs/2026-10-12.jsonl 的 repair_tail_cols "
                 "（fixed=2140, unresolved=961）",
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
        print(f"[{row['direction']:>6}] {row['title']}  "
              f"{row['metric']}: {row['before']} → {row['after']}")
    print(f"\n新增 {added} 条 · 台账现有 {len(load_ledger(ROOT / 'data'))} 条 → "
          f"{ledger_path(ROOT / 'data')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
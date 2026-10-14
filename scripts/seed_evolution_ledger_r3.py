"""把第三轮（2026-10-12 上午）**实测**的改进灌进台账。

幂等——标题已存在就跳过，可以反复执行。

## 这一轮的性质与前两轮不同

前两轮灌的是「让策略更好的改进」。这一轮灌的两条**都不会让任何超额数字变好**：

1. 体检补上「列有值吗」——让 3342 只缺数据的市场从「健康」翻成「不健康」。
   指标变差，但坏消息比好消息值钱。
2. 写库互斥——让「能不能安全地修数据」变成有保证的事。

还有一条是 **worse / flat**：把「90 只文件被截断」的结论撤掉。
台账里必须留着「我曾经相信过一个错误结论」这件事，
否则下一个看台账的人会以为数据一直是对的。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aplus.ledger import ledger_path, load_ledger, record_improvement  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
D = "2026-10-12"

ENTRIES = [
    dict(
        title="体检补上「列有值吗」：3342 只缺数据的股票从健康翻成不健康",
        category="data_quality", severity="critical",
        metric="末根缺 turn 的股票数（体检判定为健康的）", higher_is_better=False,
        before=0, after=3342,
        note=("**指标大幅变差，这是本条的全部意义。** 原来体检只看列名在不在，"
              "于是 2026-10-09 那批被腾讯备源写入的 bar（仅 OHLCV）"
              "末根五列全空，体检一律判健康，缺口静默存在三周。\n\n"
              "实测：5458 只有 turn 列，其中 **3342 只（61.2%）末根缺失**，"
              "而 3182 只**恰好只缺最后 1 根**——这是写入端削薄了某一根的形状，"
              "不是「这因子历史上就没数据」。\n\n"
              "新增 `coverage.tail_nan_columns()`：扫尾部 5 根，"
              "统计「**当天有成交**、但某列却是空」的根数。停牌（volume=0）"
              "不计——否则长期停牌股会被整片误报，比原来的盲区更吵。"),
        invalidates=["「体检通过 = 数据可用」——2026-10-12 之前成立，当天起不成立",
                     "所有在 2026-10-12 之前跑出的健康度数字（它们漏掉了 61.2% 的尾部空值）"],
        evidence="aplus/coverage.py: tail_nan_columns / 体检原因 tail_nan_cols；"
                 "tests/test_coverage_tail_nan.py（13 项）；"
                 "scripts/mutation_check.py 组 tail_nan 4/4 全杀",
    ),
    dict(
        title="isST 缺失被当成「不是 ST」：推荐名单 30% 处于 ST 筛查盲区",
        category="correctness", severity="critical",
        metric="末根 isST 为空、因而被放行的推荐股票占比", higher_is_better=False,
        before=0.0, after=0.30,
        note=("`panel.py` 里 `np.where(np.isnan(isST), 0, isST)` —— "
              "NaN 被折成 0，而 0 的语义是「**不是** ST 股」。"
              "缺数据 = 声称自己正常，过滤器在最需要它的时候最不设防。\n\n"
              "实测 2026-10-11 的 20 只推荐里有 **6 只（30%）** 末根 isST 为空，"
              "其中 5 只在跌篮子。\n\n"
              "**但实测没有造成实际损失**：这 6 只最近一次有效 isST 全是 0，"
              "名单里没有真的 ST 股。这个区别必须说清楚——"
              "没出 ST 股是**运气**，不是防线起了作用。"
              "一只在当天被实施风险警示的股票会毫无阻碍地出现在推荐里。\n\n"
              "根因（`panel.py` 把 NaN 折成 0）**本轮未改**：改了会让今天之前的"
              "回测不可比，需要连同全部重跑一起做。已在 INCIDENTS 记为未解决。"),
        invalidates=["「推荐名单里的 ST 股已经被过滤掉了」——在数据有洞时不成立"],
        evidence="aplus/panel.py:536；data/reports/holdings.json（as_of=2026-10-11）；"
                 "docs/INCIDENTS_2026-10-12.md 缺陷 24",
    ),
    dict(
        title="四个写主库的脚本统一互斥：daily_update 原先一点锁都没有",
        category="correctness", severity="critical",
        metric="有写库互斥保护的脚本数 / 写主库的脚本总数", higher_is_better=True,
        before=2, after=4,
        note=("`daily_update.py` 由 launchd 每天 18:30 无人值守地触发，"
              "却**一点锁都没有**；而它一次要写 5460 个 parquet。"
              "只要和任意一次回填撞上，两个进程同时对同一文件做 "
              "concat + to_parquet，后写覆盖先写，丢的列和行没有任何人会知道。\n\n"
              "第二层：原有的 `if lock.exists()` 本身就是 check-then-act 竞态——"
              "两个进程可以同时看到「不存在」然后都以为自己拿到了锁。"
              "改用 `O_CREAT|O_EXCL` 让内核保证原子。\n\n"
              "锁里写 pid 但**不自动判活抢占**：把一个「其实还活着、只是慢」的"
              "进程误判成死的，比等它跑完危险得多。真要抢显式传 `force=True`。\n\n"
              "顺带修了 `run_daily` 的预检查——它检查锁却不握锁，"
              "检查与写入之间隔着整个网络往返。"),
        invalidates=["「回填进行中时 daily_update 会自己让路」——2026-10-12 之前它不查也不让"],
        evidence="aplus/write_lock.py（新建）；tests/test_write_lock.py（12 项）+ "
                 "tests/test_run_daily_write_lock.py（4 项）；"
                 "scripts/mutation_check.py 组 writelock 4/4 全杀",
    ),
    dict(
        title="撤掉「90 只文件被截断」的错误结论（flat：数据本来就是对的）",
        category="data_quality", severity="major",
        metric="短于中位行数且无法用上市/退市登记解释的股票数", higher_is_better=False,
        before=90, after=0,
        note=("2026-10-11 记下「90 只股票的文件被截断，且根因未定位」，"
              "并推断「必有代码绕过 store 直接 to_parquet 覆盖」。\n\n"
              "**这是误判。** 用 baostock 上市/退市登记簿全量复核：\n\n"
              "    行数 < 1882 的 2122 只\n"
              "      已退市（末行 <= 退市日）        236\n"
              "      次新股（首行 >= 上市日且未退市） 1886\n"
              "      无法解释                          0\n\n"
              "反向也查了：末行 == 全库最新日的 5224 只里，登记为已退市的有 0 只。\n"
              "逐只对上号：000418.SZ 退市日 2019-06-21，文件末行正好 2019-06-21。\n\n"
              "**现成的反证当时就在手边却被我略过**：这些文件的 mtime 散布在"
              "10-09 23 / 10-10 08 / 10-10 21 / 10-11 15 四个时段，"
              "与正常股无异——mtime 分散就说明不是某一次坏写入。\n\n"
              "差点用远端数据去覆盖 236 只**本来正确**的退市股历史。"),
        invalidates=["INCIDENTS_2026-10-11.md「有一处能截断文件的代码路径尚未定位」——"
                     "没有这样的代码路径，数据一直是正确的"],
        evidence="docs/INCIDENTS_2026-10-12.md 第 0 节；"
                 "aplus/coverage.py 的 docstring 早就写明「单看 parquet 无法区分，"
                 "要用上市/退市登记」，而我当时用行数阈值重造了更差的启发式",
    ),
    dict(
        title="「ST 状态未知」从静默折成 0 改为按日计数上报",
        category="correctness", severity="critical",
        metric="ST 状态未知的股票被上报的比例", higher_is_better=True,
        before=0.0, after=1.0,
        note=("`panel.py`（快路径）与 `snapshot.py`（慢路径）各有一处把缺失的 "
              "`isST` 折成 `0`——**缺数据 = 声称自己正常**。同一件事两份实现，"
              "这次一起改，否则两条路径对同一个未知会给出两种说法。\n\n"
              "改法是**如实传递 NaN** + `filter_universe` 新增 `unknown_st` "
              "按日累加 + `run_daily` 出单时打印：\n\n"
              "    [predict] ⚠️ ST 状态未知 6 只（末根 isST 缺失，已按「不是 ST」放行）\n\n"
              "**刻意没有改成 fail-closed**：该函数 docstring 记着一条实测——"
              "要求 `isST == 0` 才保留会砍掉 88% 的正常股票。"
              "在数据还有洞时 fail-closed 等于让平台不可用。"
              "正确顺序是「先让洞看得见 → 补洞 → 数据干净后再谈 fail-closed」。\n\n"
              "同时把 `backtest` 结果的 `attrs` 补上 `unknown_st` / "
              "`unknown_st_codes`，让回测路径也能读到这个规模。"),
        invalidates=["「`isST != 1` 就够了」——它在数据有洞时等于「缺数据按正常处理」",
                     "「两处折成 0 是刻意的回归处理」——2026-10-11 的那条注释是错的，"
                     "它只防住了崩溃，没防住语义"],
        evidence="aplus/predict.py: filter_universe(unknown_st=…)/"
                 "aplus/panel.py:542 / aplus/snapshot.py:96 / aplus/backtest.py attrs；"
                 "tests/test_unknown_st_visibility.py（9 项）；"
                 "scripts/mutation_check.py 组 unknown_st 4/4 全杀",
    ),
    dict(
        title="变异测试报告补上存活率的置信上界（flat：结论没变，边界变清楚了）",
        category="methodology", severity="minor",
        metric="变异结果里报告的置信信息量", higher_is_better=True,
        before=0, after=2,
        note=("**「33/33 全杀」不等于「测试好使」，它只等于「这 33 个变异体被杀掉了」。** "
              "如果某个等价形态的变异体存在而没被造出来，它照样能活。\n\n"
              "0 存活 / n 次试验时存活率的 95% 单侧上界（Clopper–Pearson）："
              "`1 - 0.05**(1/n)`。n=33 → **8.7%**。\n\n"
              "同时加了薄弱组告警：某组变异体少于 3 个就打出来。"
              "单跑 `unknown_st` 组（n=4）时上界是 **52.7%**，"
              "脚本自己打印「全杀但样本量小——这份结论的运气成分不小」。\n\n"
              "功效是设计出来的，不是靠运气。这一条不改变任何结论，"
              "它只让「全杀」这句话不再被过度解读。"),
        invalidates=["「全杀 = 防护测试完备」"],
        evidence="scripts/mutation_check.py: _survival_upper_bound / 薄弱组告警",
    ),
    dict(
        title="主源会话中途死亡时熔断：省掉 961 次注定失败的网络往返",
        category="operations", severity="major",
        metric="主源死亡后的无谓网络往返次数", higher_is_better=False,
        before=961, after=15,
        note=("首轮全量修复 3101 只：**填上 2140｜未填 961｜异常 0**。"
              "961 只全部 `degraded`，起点精确在第 2141 只——baostock 会话在那里断开。\n\n"
              "**「异常 0」差点让这次失败隐形**：降级不是异常，"
              "`sync_daily` 安静返回「成功但没用」。只看异常数，这次失败完全看不到。\n\n"
              "手工复现证明源站有数据（`603605.SH` 隔几分钟重跑即成功，"
              "`turn=1.3295`），所以缺的不是数据，是会话。\n\n"
              "熔断判据是**连续**降级而非累计——累计会被中间的成功稀释，"
              "中间成功一次就清零。并且区分 `degraded`（会话死了）"
              "与 `quarantined`（源还活着、数据不合格）："
              "一次批量数据质量事件不该伪装成源站挂掉而误熔断。\n\n"
              "熔断只省时间，不影响正确性——即使没有它，"
              "「写完重读复验」也保证了一条错数据都不会留下。"),
        invalidates=["「异常计数为 0 = 这一批跑成功了」——降级与隔离都不计入异常"],
        evidence="scripts/repair_tail_cols.py: DEGRADE_STREAK_LIMIT / --degrade-limit；"
                 "tests/test_repair_tail_cols_breaker.py（7 项）；"
                 "scripts/mutation_check.py 组 breaker 3/3 全杀",
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

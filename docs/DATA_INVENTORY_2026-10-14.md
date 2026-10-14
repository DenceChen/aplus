# 数据盘点 · DATA_INVENTORY_2026-10-14

> 盘点范围：`/Users/airboom/opencode/aplus` 的 `data/`（19791 → **19767** 文件，见 §0.1）、
> `aplus/`（49 模块 + `sources/` 11 + `web/`）、`scripts/`（88 个 .py + 5 目录）。
>
> **本次盘点为只读**：未删除、未移动、未改名任何文件，未执行任何写 git 操作。
> 唯一写入物是本文件。
>
> **安全约束遵守情况**：未读取 `~/.aplus/llm.json`；未打印环境变量全集；
> 未加载任何 panel 数据（parquet 一律只做 `ls`/`du`/`stat`/`find`/`head`/`grep`）；
> 未跑全量 pytest（只跑了 2 个文件共 10 条测试）。

---

## 0. 两个必须先说的发现

### 0.1 盘点期间 `data/` 被另一个进程删掉了 23 个文件

这不是我做的。我的第一次扫描（本次会话开始）与最后一次扫描之间：

| 指标 | 首次扫描 | 末次扫描 | 差 |
|---|---|---|---|
| `data/` 文件总数 | 19791 | **19767** | **−24** |
| `data/` 总字节 | 1122672 KB | 1121664 KB | −1008 KB |
| `data/reports/` 文件数 | 89 | **90** | +1 |
| `data/` 散落 `*.log` | **32** | **9** | **−23** |

被删的 23 个全部是散落日志（`backfill_full.log`、`bootstrap_full.log`、
`backfill_meta.log`、`backtest_2y.log`、`backtest_fixed.log`、`bond_bootstrap.log`、
`bond_param_race.log`、`bond_race.log`、`evolve.err.log`、`evolve.log`、
`evolve_race.log`、`evolve_weekly_smoke.log`、`execution_lag_ab.log`、
`factor_backfill.log`、`factor_repair_all.log`、`guard.err.log`、`quality_tilt.log`、
`regime_backtest.log`、`remeasure.log`、`reversal_2y.log`、`run_daily.err.log`、
`significance.log`、`web.log`、`web_agent.err.log`、`web_agent.log`）。

同时**新增**了 `data/reports/restatement_20261012_manifest.json`（0 字节，mtime 22:59）。

### 0.2 有一个并发进程正在准备删掉 352 MB 的 tailcols 备份

`ps` 里有两个非本会话的进程（PID 13735、92565），命令行显示它们正在：

1. 遍历 `data/_backup_tailcols_*`，逐个 parquet 与 `data/master/daily/` 同名文件
   做逐格比对，输出 `data/reports/restatement_20261012_manifest.json`；
2. 该脚本自己的 `why` 字段写得很直白：
   > `"删掉 352MB tailcols 备份前，把「哪些股票的哪些列被上游追溯修订过」落成文件"`

即：**这个并发 agent 的下一步就是删除 `_backup_tailcols_20261012-082806`(281576 KB)
+ `-090838`(79316 KB) = 361 MB**。

**这直接触发了任务书里点名的事故形态。** 见 §B3.4 的判定：这两份备份的股票代码
100% 存在于 `data/master`，但备份内容是**上游追溯修订之前**的主库副本，
而修订已经覆盖掉了 master —— 也就是说，**「哪些格子被追溯改过」这个事实，
删除备份后就只剩那个并发进程正在写的、目前 0 字节的清单**。

盘点结论：本报告不参与删除决策，但**必须把这条记录在案**。

---

## A. 代码逻辑图谱

### A.1 `aplus/` 模块职责表（49 个顶层模块）

`data/` 路径列的取证方式：AST 解析 `aplus/*.py` 的 import 图（精确，
含缩进/延迟 import）；data 路径列来自 `Path(...)` 字面量 + `grep -nE "data/|DATA_ROOT|data_root"`。

| 模块 | 一句话职责 | aplus 内被谁 import | 读 data/ 路径 | 写 data/ 路径 |
|---|---|---|---|---|
| `__init__` | 包声明：A股免费多源数据获取与交叉校验框架 | — | — | — |
| `adjust` | 复权计算：不复权 + 因子 → 前复权/后复权 | — | `master/`(root) `master/factor` `master/daily` | — |
| `alloc` | 资产配置：规则型仓位 + 调仓差异 + 风控注记 | — | — | — |
| `backtest` | 走前回测：历史区间重放「预测→次日验证」闭环 | `factor_ic` | `master/daily` | `reports/backtest.parquet`, `reports/backtest_summary.json` |
| `bond_backtest` | 可转债 T+0 日内策略回测（路线A二阶段） | — | — | — |
| `bond_excess` | 转债低价轮动样本外超额口径（策略失效 vs 市场 beta） | — | `master/daily/{code}` | `reports/bond_excess_analysis.json` |
| `bond_horizon` | 可转债低价因子的持有期经济学（三阶段） | — | — | — |
| `bond_paper` | 可转债纸面账户（路线A四阶段） | — | — | `reports/bond_paper.json/.parquet`, `bond_paper_state.json` |
| `bond_sync` | 可转债日线增量同步 | — | `bonds/master/daily` | `bonds/master/daily` |
| `champion_health` | 现任权重的多窗口健康度 | — | `reports/race_*.json`, `reports/evolution_race*.json`, `reports/independent_validation_*.json` | — |
| `composite` | 因子合成的纯函数层：组合IC恒等式与权重求解 | — | `reports/factor_ic_*.json` | — |
| `costs` | 交易成本：**按本金建模**而非按费率 | — | — | — |
| `coverage` | 覆盖度体检：区分新股天然短 vs 老股被截断 | — | `master/daily` | — |
| `crosscheck` | 交叉校验：双源比对 + 单源合理性规则 | — | — | — |
| `dist` | t 分布分位数（显著性判定基础设施） | — | — | — |
| `evolve` | 自进化：回测驱动权重晋级（带防过拟合护栏） | `evolve_researcher` | `reports/weights.json` | **`reports/weights.json`** |
| `evolve_race` | 自进化赛马：训练段选权重，测试段验收 | — | — | — |
| `evolve_researcher` | LLM 进化研究员：读证据 → 提候选权重假设 | — | `reports/evolution_log.json` | 台账追加 |
| `factor` | 因子引擎：日线 → 横截面因子（纯函数） | — | `master/daily`, `master/factor` | — |
| `factor_ic` | 逐因子信息系数(IC)诊断 | — | `reports/factor_ic*.json` | `reports/factor_ic*.json` |
| `financial` | 财务数据层：**披露日对齐**协议（前视偏差第一道闸） | — | — | — |
| `forward` | 前瞻验证台账：唯一干净的晋级路径 | — | `reports/forward_registry.json` | `forward_registry.json` |
| `history` | 历史预测与验证的装配层 | — | `reports/predictions/pred_*.parquet` | — |
| `hurdle` | 频率门槛：把「要不要降频」变成一道算术题 | — | — | — |
| `ic_to_return` | IC → 收益：把「排得准」翻译成「赚多少」 | — | `reports/xsec_sigma_r.json` | — |
| `ledger` | 改进台账：追加式、结构化、方向由数字推导 | — | `reports/evolution_ledger.json` | **`reports/evolution_ledger.json`** |
| `live_rebalance` | 实盘持仓状态机 | — | `reports/holdings.json` | `reports/holdings.json` |
| `llm` | LLM 客户端：经本机 glm-failover 代理 | — | `~/.aplus/llm.json`（**本报告未读取**） | — |
| `log` | 结构化日志系统（JSONL 一行一事件） | — | `logs/*.jsonl` | `logs/{date}.jsonl` |
| `news` | 新闻情绪覆盖层 | — | — | — |
| `panel` | 因子面板：横截面重算（每只股票算一次） | — | `master/daily` | — |
| `panel_cache` | 横截面面板缓存的**溯源守卫** | — | 缓存 pickle | 缓存 pickle |
| `pipeline` | 同步流水线：多源容灾 + 交叉校验 + 隔离 | — | — | `master/*`, `quarantine/daily` |
| `portfolio` | 组合层回测：选股 + 配置规则 → 净值曲线 | — | — | — |
| `predict` | 预测引擎：横截面因子 → 涨跌 TopN | — | `data/names.json` | — |
| `provenance` | 报告溯源与新鲜度 | — | `reports/**.json` | **`*.provenance.json` 边车** |
| `race_reason` | 显著性否决理由里可当场复算的数字 | — | — | — |
| `rebalance` | 调仓频率与缓冲区 | — | — | — |
| `report_ready` | 「跑完了吗」的判据——不信退出码只信产物指纹 | — | `reports/bridge_error.json` | — |
| `reports` | 报告持久化：预测/验证/配置落盘与历史读取 | — | `reports/predictions/pred_*.parquet` | `predictions/pred_*.parquet` |
| `snapshot` | 横截面快照：主库 → 全市场因子横截面 | — | `master/daily`, `master/factor` | — |
| `store` | 本地 parquet 存储：主库 + 隔离区 | — | `master/daily`, `master/factor`, `quarantine/daily` | **全部三者的写入口** |
| `verify` | 验证闭环：T+1 实际涨跌 vs 预测判定 | — | — | `reports/verification.parquet` |
| `vintage` | Vintage 累积（出路B）：把「PIT尚未建立」变成「正在建立」 | — | `reports/financial_vintage` | `reports/financial_vintage/date=*` |
| `webapp` | FastAPI Web 应用：仪表盘 API + 带记忆对话 | — | `reports/*`, `master/daily`, `chat.db`, `logs/` | `chat.db` |
| `write_lock` | 主库写锁：同一时刻只许一个进程改 master/daily | — | — | `bootstrap.lock` |
| `xsec_sigma` | σ_r 横截面收益标准差（IC→收益唯一未实测输入） | — | `master/daily` | `reports/xsec_sigma_r.json` |

**关键结构性事实：`aplus/` 内部只有 4 条依赖边**（AST 精确解析，非 grep 近似）：

```
factor_ic      -> adjust, backtest
composite      -> factor_ic
evolve_researcher -> evolve
```

其余 44 个模块**互不 import**。这说明架构是刻意解耦的，但也意味着
「一个模块读错文件」不会被同包内的其他模块发现——只有测试会发现。

### A.2 `aplus/sources/`（11 个数据源适配器）

| 模块 | 职责 |
|---|---|
| `base` | 数据源抽象基类 |
| `baostock_source` | **主源**：日线 1990 起 + 复权因子 + 股票列表 |
| `akshare_source` | 备源：东财，礼貌限速，禁高并发（封 IP/滑块） |
| `tencent_source` | 备源：python 直连稳定（本机实测），无 Referer 限制 |
| `tushare_source` | 可选第三源（120 积分免费档） |
| `convert` | 源数据 → 统一 schema 的纯转换（单位归一） |
| `shares` | 股本数据源：总股本/流通/A股（时间序列）→ `master/shares` |
| `bond_convert` | 可转债源数据 → 统一 schema（路线A一阶段） |
| `bond_list` | 可转债动态名单源（SSE allBonds.js） |
| `tencent_bond_source` | 腾讯可转债行情适配器 |

### A.3 `scripts/` 按目录分组（88 个 .py）

| 类别 | 数量 | 代表文件（→ 产出/消费什么） |
|---|---|---|
| **回测类** | 17 | `backtest_walk_forward_weights.py`(23KB, `R⁻¹Σ_f` 逐年权重真回测)、`ic_guided_race.py`、`evolve_race_composite.py`、`evolve_race_fast.py`、`basket_width_sweep.py`、`horizon_sweep.py`、`regime_control_sweep.py`、`execution_lag_ab.py`(entry_lag 0 vs T+1)、`signcorrect_freq_sweep.py`、`lowfreq_plausibility.py`、`k_sweep_extract.py`、`rerun_backtest_fixed_factors.py`、`bond_race.py`、`bond_param_race.py`、`bond_horizon_race.py`、`bond_excess_analysis.py` |
| **因子类** | 13 | `factor_ic_diagnostic.py`、`fundamental_ic.py`、`fundamental_factor.py`、`bm_ic_attribution.py`、`extract_ic_matrices.py`(→`ic_matrices.parquet`)、`measure_bridge_error.py`(→`bridge_error.json`)、`measure_xsec_sigma.py`(→`xsec_sigma_r.json`)、`factor_vs_random.py`、`significance_check.py`、`tilt_persistence.py`、`turnratio_vs_random.py`、`decompose_alpha_beta.py`、`build_panel_cache.py` |
| **进化类** | 22 | `evolve_weekly.py`（**唯一写 `weights.json` 的脚本**，`:421`）、`forward_validate.py`、`forward_batch_status.py`、`register_forward_candidates{,_r2,_r3}.py`、`verdict_composite.py`、`verdict_monthly_hurdle.py`、`seed_evolution_ledger.py`…`_r12.py`（**12 个**，逐轮台账播种） |
| **运维类** | 14 | `run_daily.py`（每日总编排）、`daily_update.py`、`bootstrap_history.py`、`backfill_factors.py`、`backfill_gaps.py`、`repair_factors.py`、`repair_tail_cols.py`、`recover_quarantine.py`、`collect_financial_vintage.py`、`fetch_shares.py`、`guard_daily.py`、`guard_vintage.py`、`healthcheck.py`、`bond_bootstrap.py` |
| **工具类** | 23 | `_bootstrap.py`（所有长跑脚本共用前奏）、`_diag_*`(4)、`_verify_*`(3)、`audit_report_contamination.py`、`candidate_block_audit.py`、`market_benchmark_audit.py`、`universe_forensics.py`、`universe_isolation.py`、`probe_turn_*`(3)、`measure_amount_availability.py`、`mutation_check.py`、`live_smoke.py`、`bond_smoke.py`、`smoke_researcher.py`、`head_bucket_probe.py`、`diag_financial_lookahead.py` |
| **前端/web** | 1 | `serve.py`（→ `http://127.0.0.1:8788`，`create_app(<repo>/data)`） |

另含 4 个归档审计目录（`audit_calendar/` 12 文件、`audit_survivorship/` 6、
`audit_tradability/` 1、`audit_tradeability/` 13）——是**审计产物集**，不是活代码。

---

## B. `data/` 产物盘点

### B.1 顶层目录统计

| 目录 | 文件数 | 字节 | 占比 | 最新 mtime |
|---|---|---|---|---|
| `data/master/` | 11037 | 498,642,393 (475.5 MB) | 44.4% | 2026-10-14 19:53 |
| `data/bonds/` | 1087 | 24,282,570 (23.2 MB) | 2.2% | 2026-10-11 03:29 |
| `data/books/` | 4 | 2,905,656 (2.8 MB) | 0.3% | 2026-10-11 11:49 |
| `data/reports/` | 90 | 53,007,814 (50.6 MB) | 4.7% | 2026-10-14 22:59 |
| `data/logs/` | 4 | 135,399 | 0.01% | 2026-10-14 20:00 |
| `data/quarantine/` | 10 | 91,531 | 0.01% | 2026-10-14 19:16 |
| `data/_backup_*`（10 个） | 7520 | ~421,815,000 (402 MB) | 37.6% | 2026-10-12 09:05 |
| `data/` 散落文件 | 9（盘点时） | ~1,000,000 | 0.1% | 2026-10-14 22:00 |
| **合计** | **19767** | **~1,121,664 KB** | | |

`master/` 细分：`daily/` 606368 KB(592 MB, 5462 个股票目录)、
`factor/` 22224 KB、`shares/` 164 KB。
`reports/` 细分：68 个 `.json`（2.4 MB，其中 **5 个是 provenance 边车**）、
20 个 `.parquet`（46.6 MB，含 `financial_vintage/date=2026-10-14/financial.parquet`
单文件 36.7 MB）、7 个子目录。

### B.2 活数据（必留）

| 资产 | 规模 | 引用证据（行数） | 判定 |
|---|---|---|---|
| `data/master/` | 11037 文件 / 475.5 MB | `grep -rn 'master' aplus/` = **21** 行；`scripts/` = **94** 行；`tests/` = **36**；`docs/` = **3** | **必留**。全平台唯一真相源 |
| `data/reports/*.json` | 68 个 / 2.4 MB | `grep -rn 'reports' aplus/` = 164 行；`scripts/` = 199；`tests/` = 296；`docs/` = 51 | **必留**（研究结论本体） |
| `data/bonds/` | 1087 文件 / 23.2 MB | `bonds`：`aplus/`=4；`scripts/`=16；`tests/`=9；`docs/`=1 | **必留** |
| `data/books/` | 4 文件 / 2.8 MB | `books`：`aplus/`=**0**；`scripts/`=**0**；`tests/`=1；`docs/`=**6** | **必留**（见下） |
| `data/quarantine/` | 10 文件 / 91 KB | `quarantine`：`aplus/`=19；`scripts/`=17；`tests/`=15 | **必留**（`store.py` 的隔离区语义 + `store.py:105-106` 有 `unlink` 清理逻辑，说明是活状态） |
| `data/logs/` | 4 jsonl | `aplus/log.py:122,134,144,186,206` | **必留**（`rotate_logs` 在用，只保留近 7 天） |

**`data/books/` 的特殊性**：它 **0 行代码引用**，只有 docs 引用。但它不是垃圾——
`data/books/{chan,cn_factor}.txt` 是仓库根目录两本 PDF 的抽取文本
（`Quantitative Trading How to Build Your Own Algorithmic Trading Business`
9.5 MB、`因子投资方法与实践` 38.6 MB），`docs/methodology_chan.md:3`、
`docs/factor_spec_cn.md:4`、`docs/chan_recipe_mining_2026-10-14.md:11` 都把它当
「动手前先翻书」的原书语料。**PDF 本身进了 git（38 MB 进 `.git`，`.git` 总 46 MB），
所以 books 目录技术上可再生**——但可再生 ≠ 该删，抽取步骤无自动化。

### B.3 可再生中间物（候选删除）

#### B.3.1 `data/_backup_*` 十个目录 —— **「有人在用的机制」，不是垃圾**

关键发现：`_backup_` 字面量在代码里有 **3 个明确的构造点**，每个都是
**写前必备份**纪律的实现：

| 备份目录 | 文件 | 大小 | 由谁产生（代码位置） |
|---|---|---|---|
| `_backup_gapfill_20261011-145503` | 3386 | 48,460 KB | `scripts/backfill_gaps.py:176` `backup_dir = Path(args.data_root)/f"_backup_gapfill_{stamp}"`；拷贝 `_backup()` 定义在 `:99`，调用 `:252`(daily) `:253`(factor)；纪律写在 docstring `:18` |
| `_backup_gapfill_20261011-162751` | 46 | 652 KB | 同上 |
| `_backup_gapfill_20261011-145423` | 4 | 32 KB | 同上 |
| `_backup_gapfill_20261011-201818` | 4 | 48 KB | 同上 |
| `_backup_tailcols_20261012-082806` | 3101 | 281,576 KB | `scripts/repair_tail_cols.py:118` `backup_dir = root/f"_backup_tailcols_{stamp}"`；`mkdir :157`；`shutil.copy2 :164`（**明确在写之前**）；纪律写在 docstring `:29`「写前必备份」 |
| `_backup_tailcols_20261012-090838` | 960 | 79,316 KB | 同上 |
| `_backup_tailcols_20261012-082736` | 7 | 620 KB | 同上（定点 7 只的试跑） |
| `_backup_quarantine_recovery_20261011` | 13 | 884 KB | `scripts/recover_quarantine.py:40` 硬编码 `BACKUP = DATA/"_backup_quarantine_recovery_20261011"`；写入 `:58-60` |
| `_backup_pre_weekly` | 2 | 184 KB | **来源不明**：`grep -rn 'pre_weekly' aplus/ scripts/ tests/ docs/` = **0 行**。内容是 `backtest.parquet` + `backtest_summary.json`（window 2026-04-01→2026-10-01，overall_hit_rate 0.4707），是 evolve_weekly 前的旧基线 |
| `_backup_predictions_121936` | 3 | 28 KB | **来源不明**：`grep -rn 'predictions_121936\|121936'` = **0 行**。pred_2026-10-09/10/11 的旧版；09/10 与现文件字节数相同，**10-11 不同（备份 10750 vs 现 12687）** |

**这是任务书预言的情形：「可能有脚本专门写备份目录，那意味着它是有人在用的机制」——
确实如此。** 这三个脚本（`backfill_gaps.py` / `repair_tail_cols.py` /
`recover_quarantine.py`）都遵守「覆盖前必备份」，删掉备份**不会**破坏机制
（下次运行会重新造），但会**摧毁这三次具体修复的回滚能力**。

**覆盖度核验**（`find data/master -name '*.parquet'` 提取 5559 个代码后逐一比对）：

```
_backup_gapfill_20261011-145423        codes=2     in_master=2     NOT_in_master=0
_backup_gapfill_20261011-145503        codes=1693  in_master=1693  NOT_in_master=0
_backup_gapfill_20261011-162751        codes=23    in_master=23    NOT_in_master=0
_backup_gapfill_20261011-201818        codes=2     in_master=2     NOT_in_master=0
_backup_tailcols_20261012-082736       codes=7     in_master=7     NOT_in_master=0
_backup_tailcols_20261012-082806       codes=3101  in_master=3101  NOT_in_master=0
_backup_tailcols_20261012-090838       codes=960   in_master=960   NOT_in_master=0
_backup_quarantine_recovery_20261011   codes=13    in_master=13    NOT_in_master=0
_backup_pre_weekly                     codes=1     NOT_in_master=1   ← 是 backtest.parquet，非股票
_backup_predictions_121936             codes=3     NOT_in_master=3   ← 是 pred_*.parquet，非股票
```

**结论：所有备份里的股票代码 100% 已回到 `data/master`。** 但
「代码还在」≠「内容还在」——见 B.3.4。

#### B.3.2 `data/*.log` —— **有一半是 launchd 活日志，不能删**

7 个 launchd agent **全部处于 LOADED 状态**，其 stdout/stderr 直接重定向到
`data/*.log`：

| plist | 重定向目标 | 状态（盘点时） |
|---|---|---|
| `com.aplus.daily` | `data/run_daily.log` | 21766 B, 10-14 20:00 |
| `com.aplus.evolve` | `data/evolve.log` | **已被并发进程删除** |
| `com.aplus.forward` | `data/forward_validate.log` | 5417 B, 10-14 21:05 |
| `com.aplus.guard` | `data/guard.log` | 4317 B, 10-14 19:31 |
| `com.aplus.guardvintage` | `data/guard_vintage.log` | **MISSING**（plist 期望但不存在） |
| `com.aplus.vintage` | `data/vintage.log` | 48 B, 10-14 22:30 |
| `com.aplus.web` | `data/web_agent.log` | **已被并发进程删除** |

取证命令：
```bash
for p in ~/Library/LaunchAgents/com.aplus.*.plist; do
  grep -oE 'data/[a-z_]*\.log' "$p" | sort -u; done
launchctl list | grep com.aplus     # → 7 个全部 LOADED
```

**这 7 个不是「可再生中间物」，是运行中作业的活输出**。删掉它们不会省多少
（合计 <40 KB），但会**丢掉下一次 launchd 写入前的历史**，且会让排障失去现场。
`.gitignore:14` 已经用 `*.log` 把它们排除出版本库了——**这已经够了，不需要再删**。

已被并发进程删掉的 23 个才是真正的一次性运行日志（`bootstrap_full.log` 595 KB、
`backfill_full.log` 150 KB、`evolve_race.log` 51 KB、`evolve_weekly_smoke.log` 29 KB、
`execution_lag_ab.log` 4 KB 等），**删掉它们是可接受的**。

#### B.3.3 `*.tmp` / `.DS_Store` / `__pycache__`（data/ 内）

```
find data -name '*.tmp'         | wc -l   → 0
find data -name '.DS_Store'     | wc -l   → 0
find data -name '__pycache__' -type d | wc -l → 0
```

**data/ 内三者均为零**，无需处理。（`aplus/__pycache__` 存在但不在 data/ 内，
且 `.gitignore:8` 已覆盖。）

#### B.3.4 ⚠️ 并发进程正在删的 352 MB —— 本报告标注为**高风险**

`_backup_tailcols_20261012-082806`(281576 KB) + `-090838`(79316 KB)。
并发进程 PID 13735 正在生成的清单里，每条记录的 `note` 字段是：

> 「上游追溯修订后 sync_daily 覆盖；**备份为主库覆盖前唯一副本，删后仅存本清单**」

即：这些备份是 **2026-10-12 追溯修订覆盖 master 之前**的唯一副本。
删除后，「哪些股票的哪些列被上游追溯改过」这一事实将**只**存在于
`data/reports/restatement_20261012_manifest.json` —— 而该文件盘点时为 **0 字节**
（写入进程仍在跑，尚未 flush）。

**这正是任务书描述的失效模式的镜像**：不是 provenance 边车顶掉主报告，
而是**用一个尚未写完的清单替代它声称要保护的唯一副本**。
等这个清单落盘且经过独立核验之前，不建议删。

### B.4 危险区（不许建议删除）

| 文件 | 存在 | 引用证据 |
|---|---|---|
| `data/reports/weights.json` | **不存在** | `weights.json`：`aplus/`=**14** 行；`scripts/`=**35**；`tests/`=**42**；`docs/`=**10** |
| `data/reports/evolution_log.json` | 存在 378037 B | `evolution_log.json`：`aplus/`=**3**（`webapp.py:493,530`）、`scripts/`=**5**、`tests/`=**5**、`docs/`=**2** |
| `data/reports/backtest_summary.json` | 存在 661 B | `backtest_summary.json`：`aplus/`=**4**（含 `provenance.py:61` 登记为 `aplus/backtest.py` 的产物）、`scripts/`=**4**、`tests/`=**10**、docs=0 |
| `data/reports/evolution_ledger.json` | 存在 171241 B | `ledger`：`aplus/`=**3**（`ledger.py:19` `LEDGER_NAME`、`provenance.py:151`）、`scripts/`=**2**、`tests/`=**2**、`docs/`=**2** |

`weights.json` 缺失本身值得注意：`aplus/evolve.py:295-302` 明确处理了这个状态
（「没有 weights.json —— 实盘正在执行**回落默认权重**」），
`tests/test_fallback_weights_visibility.py` 有守卫。
**这个文件从没被生成过**，不是被删的。

#### B.4.1 `data/reports/*.provenance.json` 边车 —— 5 个

| 边车 | 大小 | 写出方（`generator` 字段） | 结构 |
|---|---|---|---|
| `factor_ic_7y_v2.json.provenance.json` | 178 B | `scripts/factor_ic_diagnostic.py` | `code_sha`/`code_dirty`/`generator`/`stamped_at` |
| `backtest_summary.json.provenance.json` | 180 B | `aplus/backtest.py` | 同上 + `test:true` |
| `VOID_reversal_freq_...json.provenance.json` | 323 B | `aplus/backtest.py` | 同上 + `window`/`panel_window`/`panel_market_mean` |
| `xsec_sigma_r.json.provenance.json` | 1037 B | `scripts/measure_xsec_sigma.py` | 同上 + `pool`/`avg_n`/`n_days`/`sigma_r_raw`/`note`/`known_limit`/`data_issue` |
| `ic_guided_race_7y_corrected.json.provenance.json` | 1067 B | `scripts/ic_guided_race.py` | 同上 + `note`/`double_confound`/`promote`/`best_test_excess`/`n_candidates`/`rerun_pending` |

**写出模块**：`aplus/provenance.py:47` `SIDECAR_SUFFIX = ".provenance.json"`，
`:197` `return p.with_name(p.name + SIDECAR_SUFFIX)`。
`grep -rn 'provenance' aplus/ scripts/ tests/` 命中文件数：`tests/test_audit_core_arch.py`(11)、
`aplus/provenance.py`(3)、`scripts/audit_report_contamination.py`(2)、
`tests/test_stale_weights_warning.py`(2)、`tests/test_audit_report_contamination.py`(2)、
`aplus/panel_cache.py`(2)、`tests/test_provenance.py`(1)、`tests/test_panel_cache.py`(1)、
`scripts/ic_guided_race.py`(1)、`scripts/evolve_weekly.py`(1)、`scripts/_verify_reversal_freq.py`(1)。

**注意：边车里含大量人类判断，不是纯机器元数据。**
`ic_guided_race_7y_corrected` 的 `double_confound` 写着「本轮含**两处不可分离**的修正…
本报告无法归因到其中任一个——引用时必须整体引用」；`xsec_sigma_r` 的 `known_limit` 写着
「rev5 低尾 IC 仍是**单点估计**…σ_r 这条线已查清，IC 那条线未查清」。
**删边车 = 删掉「这份数字能/不能支撑什么结论」的约束。**

#### B.4.2 事故链复核：**已修复，但存在同类残余风险**

复现命令：
```bash
.venv/bin/python -c "
from aplus.factor_ic import _find_ic_report; print(_find_ic_report('data/reports'))"
# → data/reports/factor_ic_7y_v2.json   ✅ 真报告，不是边车
.venv/bin/python -m pytest tests/test_yearly_sign_claim.py \
    tests/test_report_sidecar_selection.py -q -p no:cacheprovider
# → 10 passed in 0.99s   ✅ 无 skip
```

修复点在 `aplus/factor_ic.py:308`：`sorted(p for p in Path(reports_dir).glob("factor_ic*.json") if not p.name.endswith(SIDECAR_SUFFIX))`。
文档串 `:289-306` 把整个事故写成了 docstring。守卫是 `tests/test_report_sidecar_selection.py`
（测「选择规则本身」而非「函数返回什么」）。

**残余风险：三个 glob 没有同样的防护。**

| 位置 | 模式 | 当前是否命中边车 |
|---|---|---|
| `aplus/webapp.py:611` | `reports_dir.glob("race_*.json")` | 否（6 命中 0 边车） |
| `aplus/champion_health.py:45` | `reports_dir.glob("race_*.json")` | 否 |
| `aplus/champion_health.py:46` | `reports_dir.glob("evolution_race*.json")` | 否（4 命中 0 边车） |
| `aplus/champion_health.py:47` | `reports_dir.glob("independent_validation_*.json")` | 否（1 命中 0 边车） |

核验命令：
```bash
python -c "
import glob
from pathlib import Path
for pat in ['data/reports/race_*.json','data/reports/evolution_race*.json',
            'data/reports/independent_validation_*.json','data/reports/*.json']:
    m=sorted(glob.glob(pat))
    print(pat, len(m), [p for p in m if Path(p).name.endswith('.provenance.json')])"
```

**这四行今天是安全的，但只要有人给 `race_*` 或 `evolution_race_*` 报告打上
provenance 边车，事故就会以完全相同的方式复发**——`webapp.py:611` 更是
`sorted(...)` + `reversed()` 的组合。`SIDECAR_SUFFIX` 过滤应当下沉成
`reports_dir.glob()` 的统一封装，而不是每个调用点各自记得。

#### B.4.3 孤儿报告（0 引用，但仍不建议删）

`grep -rl <文件名> aplus/ scripts/ tests/ docs/ | wc -l` = 0 的报告：

| 文件 | 大小 | mtime |
|---|---|---|
| `FOSSAL_reversal_freq_2019_2023.json` | 10268 | 10-14 17:34 |
| `evolution_race_v2.json` / `_v3` / `_v4` | 34041 / 34035 / 36570 | 10-11 |
| `race_composite_v2_signfix.json` | 172318 | 10-14 15:10 |
| `race_probe_6m.json` / `race_screen_6m.json` | 51038 / 36404 | 10-11 |
| 4 个 provenance 边车 | — | — |

**「0 引用」是弱证据**：`evolution_race_v2/v3/v4` 会被
`champion_health.py:46` 的 `evolution_race*.json` glob 匹配到，
`race_*` 会被 `webapp.py:611` 匹配到——**它们是活代码的输入，只是没被字面量引用**。
这正是任务书警告的「你以为的垃圾文件，可能正在支撑一个结论」的另一种形态。

---

## C. 版本控制现状

### C.1 三条命令的原始输出

```bash
$ cat .gitignore
# 数据层（465MB+ 行情/因子/回测产物，永远不进版本库）
data/
*.parquet
...
*.log
...
```

```bash
$ git check-ignore -v data/reports/weights.json
.gitignore:2:data/	data/reports/weights.json

$ git ls-files data/ | wc -l
0
```

`data/` 被 `.gitignore:2` 的 `data/` 一行整体排除，**整个 1.1 GB 零跟踪**。

### C.2 跟踪文件构成（`git ls-files | wc -l` = **377**）

| 目录 | 跟踪数 |
|---|---|
| `tests/` | 157 |
| `scripts/` | 118 |
| `aplus/` | 62 |
| `output/` | 20 |
| `docs/` | 14 |
| 根文件（含 2 本 PDF） | 6 |

`.git` 体积 **46 MB**（其中两本 PDF 贡献约 38 MB）。

### C.3 ⚠️ 仓库**没有任何 remote**

```bash
$ git remote -v
（空输出，0 行）
```

**这是本次盘点最重要的单条结论。** `git` 在这里只是一份**本地快照**，
没有任何异地副本。377 个跟踪文件、46 MB `.git`、1.1 GB `data/`——
**全部只存在于这一块磁盘上**。

### C.4 「如果现在换机，有多少研究结论会永久丢失？」

**答：`data/reports/` 下 68 个 JSON 研究结论 + 20 个 parquet 派生物，
共 90 个文件 / 50.6 MB，100% 永久丢失。** 加上 `data/master/`（475.5 MB
主库）、`data/bonds/`（23.2 MB）、`data/books/`（2.8 MB）——
**整个 1.1 GB / 19767 个文件无一进入版本控制**。

按「结论」口径细分：

| 层级 | 数量 | 换机后 |
|---|---|---|
| `data/reports/*.json` 研究结论 | 68 | **永久丢失** |
| 其中 5 个 provenance 边车（含 `double_confound`/`known_limit` 等人工判断约束） | 5 | **永久丢失** |
| `data/reports/*.parquet` 派生物（backtest / ic_matrices / vintage） | 20 | **永久丢失** |
| `data/reports/predictions/` 6 天预测 | 6 | **永久丢失** |
| `data/master/` 主库 | 11037 文件 | **永久丢失**（可从 baostock/东财重新拉，但需数天且历史会变） |
| `data/bonds/` | 1087 文件 | **永久丢失**（可重拉） |
| `docs/*.md` 中**引用**这些报告的结论 | 17 个 md | ✅ **保留**（在 git 里） |

**缓解因素（唯一的好消息）**：`docs/` 有 17 个 md 在版本控制里（`PLATFORM.md` 55 KB、
`MISTAKE_LOG_2026-10-14.md` 82 KB、`AUDIT_honesty_2026-10-14.md` 31 KB 等），
它们**转述**了大部分研究结论。所以「换机后完全失忆」不成立——
**结论的叙述性记录会活，结论的原始数字不会**。引用校验会全部失效
（`tests/test_critical_value_provenance.py` 这类依赖 `data/` 存在的测试会 skip 或失败）。

**风险排序**：无 remote（异地零副本）> `data/` 整体 gitignore > 1.1 GB 单点。

---

## 结论清单与建议

### 「候选可删」清单

| # | 路径 | 大小 | mtime | 判定依据 | 风险 |
|---|---|---|---|---|---|
| 1 | `data/_backup_gapfill_20261011-145503` | 48,460 KB | 10-11 09:28 | 1693 个代码 100% 已回 `master`；由 `backfill_gaps.py:176` 造 | **中**——可重拉覆盖，回滚能力丢失 |
| 2 | `data/_backup_gapfill_20261011-162751` | 652 KB | 10-11 16:23 | 23 代码全回 master；同上 | 低 |
| 3 | `data/_backup_gapfill_20261011-145423` | 32 KB | 10-11 09:31 | 2 代码全回 master | 低 |
| 4 | `data/_backup_gapfill_20261011-201818` | 48 KB | 10-11 15:16 | 2 代码全回 master | 低 |
| 5 | `data/_backup_tailcols_20261012-082736` | 620 KB | 10-10 21:23 | 7 代码全回 master；`repair_tail_cols.py:118` | 低 |
| 6 | `data/_backup_quarantine_recovery_20261011` | 884 KB | 10-11 16:27 | 13 代码全回 master；`recover_quarantine.py:40` | 低 |
| 7 | `data/_backup_pre_weekly` | 184 KB | 10-11 10:41 | 来源不明（0 引用）；内容是 evolve_weekly 前的旧 backtest_summary（hit_rate 0.4707） | **中**——可能是不小心删掉而没写进文档的基线 |
| 8 | `data/_backup_predictions_121936` | 28 KB | 10-11 12:19 | 来源不明（0 引用）；pred_10-11 与现文件不同（10750 vs 12687 B） | **中**——旧版预测，可能是某次改动的唯一副本 |
| 9 | **`data/_backup_tailcols_20261012-082806`** | **281,576 KB** | 10-10 21:24 | 并发进程正准备删；3101 代码全在 master **但备份是追溯修订前的唯一副本** | **高——本报告不建议删**，见 B.3.4 |
| 10 | **`data/_backup_tailcols_20261012-090838`** | **79,316 KB** | 10-12 09:05 | 同上 | **高——本报告不建议删** |
| 11 | `data/*.log` 一次性运行日志（23 个，已被并发进程删除） | ~965 KB | — | 纯 stdout 重定向，无下游消费者 | 低 |
| 12 | `data/*.log` 中 7 个 launchd 活日志 | <40 KB | — | 7 个 plist 全部 LOADED，重定向目标 | **不可删**（收益≈0，风险=丢现场） |

### 三条最高优先级建议

1. **先建 remote**（`git remote add` + push）。当前 0 异地副本，
   这是所有其他风险的前提。
2. **给 `data/reports/` 单独建一份版本控制**（如 `reports_tracked/`，只放 68 个 JSON，
   2.4 MB）。它在 `.gitignore:2` 的 `data/` 覆盖范围内，必须显式 `!` 例外。
3. **把 `SIDECAR_SUFFIX` 过滤下沉到 `reports_dir.glob()` 的统一封装**，
   否则 `webapp.py:611` / `champion_health.py:45-47` 会在下一次给 `race_*`
   打边车时以完全相同的方式复发（见 B.4.2）。

---

*本报告由只读盘点生成：未删除、未移动、未改名任何文件；未执行写 git 操作。
唯一写入：`docs/DATA_INVENTORY_2026-10-14.md`。
盘点期间另一个进程删除了 `data/` 下 23 个 `.log` 并正在生成 `restatement_20261012_manifest.json`
（见 §0.1 / §0.2），该变化非本报告所为。*

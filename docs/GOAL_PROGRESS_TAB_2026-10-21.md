# 「目标进度」页：每日快照 + 常驻 tab（2026-10-21 登记）

> **用户原话**（需求的唯一来源）：
>
> 「web 页面加一个 tab 页面 记录目标达成页面。记录目标达成进度，
> **不用每次我都问你**。」
>
> ⇒ 交付物是**一个常驻的 tab**，内容是**目标达成进度**，而且**自己会更新**。

## 0. 一句话

`scripts/measure_goal_progress.py` 每晚写一份 `data/reports/goal_progress_<日期>.json`
⇒ `/api/goal-progress` 递「最新 + 历日历史」
⇒ 页面「目标进度」tab 渲染它 ⇒ **页面每天自己长一格，用户不用问**。

| 环节 | 文件 |
|---|---|
| 生成器 | `scripts/measure_goal_progress.py` |
| 端点 | `aplus/webapp.py::/api/goal-progress`（+ `_goal_progress_payload`） |
| 页面 | `aplus/web/index.html`（tab `目标进度` / panel `panel-goal` / `loadGoalProgress`） |
| 登记 | `aplus/provenance.py::PATTERN_GENERATORS`（`^goal_progress_\d{4}-\d{2}-\d{2}\.json$`） |
| 守卫 | `tests/test_goal_progress.py`（**38 条**） |
| 变异台 | `tests/mutation_specs/test_goal_progress.mut`（**17/17 全杀**） |
| cron | `5 22 * * *`（排在 `daily_review` 的 21:40 之后） |

## 1. 8 条目标逐条：状态 · 出处 · 判据

归档表 = **`docs/DELIVERY_DECISIONS_2026-10-18.md §1`**（8 条目标的唯一出处）。
**本页不给「8 条目标的达成百分比」** —— 归档表把分母定死 = D1 + D2 两项，
手段类**不产生分数**。

| # | 目标 | 归档 | 状态（2026-10-21 实测） | 关键读数与出处 |
|---|---|---|---|---|
| 1 | 5 万本金年化 20% | **D2** | **不可判定（测不出）** | `deployed_d2_verdict.json::verdict`：`cagr` / `gap_to_target_pp` / `mde_annual` / `measured_sign_determinable` / `upper_one_sided_95` / `target` |
| 2 | 每天 10 涨 10 跌 | **D1** | **进行中**（形态在出，验收未过） | `predictions/pred_<最新>.parquet` 的 up/down 只数 ＋ `score_ranking_power.json::verdict` / `::publish_allowed` / `::n_independent_pred_dates` / `::independence_caliber.n_entry_dates`（W） |
| 3 | 回测 + deep-research + 金融数据自进化 | 手段 | **进行中** | `forward_registry.json`（批次/候选/登记日）、`evolution_log.json`、`dividend_pit_coverage_shard*of*.json`、`fundamental_mechanism_armR_fullpool_*.json`、`dividend_lowvol_longleg.json` |
| 4 | 技能 / TDD / 日志 | 手段 | **进行中** | 可自动化：提交归类（`daily_review.json::commits.counts.*`）；不可自动化：**由人判断** |
| 5 | 不迷失目标 + 复盘 | 手段 | **进行中** | 同上 ＋ `daily_review.json::review_date` / `::vs_previous.has_previous` |
| 6 | 代码质量 / 架构 | 手段 | **进行中** | 同 4 |
| 7 | 唯一代码位置 | 铁律 | **达成** | `git rev-parse --show-toplevel` == 声明路径 |
| 8 | 常设决策（不再向用户确认） | 授权 | **达成** | 授权条目在 `docs/DELIVERY_DECISIONS_2026-10-18.md §1`（第 8 行） |

### 目标 1 为什么是「不可判定」而不是「未达」

产物自己写着 `measured_sign_determinable = false` ⇒ `|c| < M` ⇒
**连符号都定不了** ⇒ 产物逐字：「既不许说『达不到』，也不许说『接近』」。

⚠️ **本仓的两条硬纪律同时生效**：
① 第 16 层：「测不出」≠「没有」；
② 守卫 `test_goal_1_is_undecidable_when_the_sign_is_not_determinable`：
**不可判定不许被写成达成/未达**。

⇒ 屏上写「**不可判定（测不出）**」，**不写**「已达成 20%」，**也不写**「未达」。

### 目标 1 的两个 D2 口径（并排，各带标签）

| 口径 | 是什么 | 出处 |
|---|---|---|
| **实盘档** `deployed_live_k63` | 生产配置 · 满仓 10 只 · K=63 · 整手 · 逐笔成本 · 闲置现金 0 计息 | `deployed_d2_verdict.json::verdict` |
| **理想档** `ideal_ceiling_k63` | 满仓 100% 部署假设下的**公式上界** | `d2_signal_requirement.json::headline` |

🚨 K 相同但**不是同一个量**（理想档不含整手与闲置现金的代价）
⇒ 并排、各带标签，**不许合并、不许取极值**（第 143 层）。

### 「还差几倍」：屏上是 **1.79×**（同口径），不是 2.35×（跨口径）

| 列 | 值 | 是不是缺口 |
|---|---|---|
| `same_caliber`（K=63 生产臂） | **1.7897×** | ✅ **是** |
| `cross_caliber`（K=21 研究臂） | 2.3450× | ❌ **不是**（台账自报 `is_the_gap = false`） |

⇒ 屏上两列都在，**各带标签**；「是缺口」的标记**由后端那一列的 `is_the_gap` 决定**，
前端不许自己挑（第 143 层）。

## 2. 诚实边界（页面上那一整块逐条显示）

1. **不给「8 条目标的达成百分比」** —— 归档表把分母定死 = D1 + D2。
2. **每个数都直读产物并带 `产物路径 :: JSON 路径`** —— 屏上没有一个数是手抄的，
   也没有一个是前端算的。
3. **缺档 = 明说缺**，绝不回落成 0。
4. `|c| < M` ⇒ 连符号都定不了 ⇒ 目标 1 是「不可判定」。
5. 两个 D2 口径不是同一个量 ⇒ 并排、各带标签。
6. 「还差几倍」只取同口径那一列。
7. **D1 的 W < 10 时不许报命中率、不许给 CI**（决策四）；
   **也不许**把门槛换算成日历 ETA。
8. **屏上不出现「投入率」「闲置比例」** —— 那两个字段名字与内容相反
   （`docs/TODO_deployed_idle_cash_caliber_2026-10-21.md`）。
9. **目标 4/5/6 的效果没有自动度量** ⇒ 明写「由人判断」，不许塌成 0。

## 3. 端点：`/api/goal-progress`

| 键 | 是什么 |
|---|---|
| `latest` | 最新一份快照全文（8 条目标逐条：状态 / 数 / 出处 / 判据） |
| `history` | 历日快照列表（日期 + 生成时间 + 摘要 + 变化行 + 各目标状态） |
| `gaps` | **历日之间缺的日历日**（cron 漏跑的那几天，明写出来） |
| `n_snapshots` | 一共几份 |

**缺档的三种形态分开说**：

* 一份都没有 ⇒ **404 + 一句原因**（空数组与「读不出来」长得一样 ⇒ 形状 23）。
* 某一份读不出来 ⇒ **它仍然出现在 `history` 里**，带 `readable: false` 与错误原文
  ⇒ 不许静默跳过（跳过 = 把「没跑到」读成「那天没变化」）。
* 中间漏了几天 ⇒ 那些天进 `gaps` ⇒ 不许让时间轴看起来连续。

`latest` 是**最新可读**的那一份；若最新那一份读不出来 ⇒ `latest_note` **明说**
退到了哪一天、为什么。

## 4. cron

```
5 22 * * * cd /Users/airboom/opencode/aplus && .venv/bin/python scripts/measure_goal_progress.py >> data/goal_progress.log 2>&1
```

🚨 **排在 `daily_review`（21:40）之后** ⇒ 当天的提交归类统计已经落盘，
目标 4/5/6 那一块读得到数。
🚨 **没有动** `daily_review` 那条 cron（`crontab -l` 里原样保留）。

## 5. 守卫与变异台

**守卫 38 条**（`tests/test_goal_progress.py`），每条都对应一种失效：

| # | 守卫 | 堵住的失效 |
|---|---|---|
| 1 | 每个产物读数**逐位等于**产物字段（`struct.pack` 比字节） | 手抄 / 用公式重算（第 109 层） |
| 2 | 每个推导数**能被独立重算**（带 `verify` 配方） | 「我算了一遍」= 传闻（第 134 层） |
| 3 | `measured_sign_determinable = false` ⇒ 目标 1 必须是「不可判定」**且分支名是 `SIGN_NOT_DETERMINABLE`** | 把「测不出」写成「未达」/「接近」/「已达成」 |
| 4 | 两个 D2 口径都在 + 各带标签 + **不是同一个量** | 只给一个 / 两个口径混比（第 7、143 层） |
| 5 | 只有同口径那一列是缺口 | 2.35× 被显示成缺口 |
| 6 | 缺档 ⇒ `value: null` + 原因，**绝不回落成 0** | 「没有台账」被读成「缺口是 0」（第 89 层） |
| 7 | 缺档不许**静默翻掉**一个判决 | 「读不出来」被渲染成方向性结论 |
| 8 | 8 条目标一条不少 + 逐条对应归档表 | 悄悄少一条 / 另抄一份清单（形状 17） |
| 9 | 摘要与变化行**不含手打的数** | 散文里的数静默过期（第 73 层） |
| 10 | 摘要**必须**是渲染器算出来的那一句 | 手改摘要 |
| 11 | 屏上**不出现**「投入率」「闲置比例」 | 字段名与内容相反 |
| 12 | 目标 4/5/6 的「由人判断」恒为 `null` + **带判据** | 「不知道」塌成 0（第 7 层） |
| 13 | **不许算「8 条目标的达成率」** | 手段类被算成分数 |
| 14 | 端点：缺档 404 / 读不出的仍在历史里 / 漏日明写 | 空数组当正常（第 89 层） |
| 15 | 前端 JS **一个测量值都不许手打** | 数字离开脚本没有通道把它拉回来 |
| 16 | 前端**不许**写「已达成 20%」 | 把测不出说成达成 |
| 17 | 前端**行为**守卫：只有同口径那列被标成缺口 | 静态断言会被注释满足（第 13 层） |
| 18 | 前端**行为**守卫：文本型读数另起一行 | 长文本被压成逐字竖排（浏览器实测过的症状） |
| 19 | 深链白名单 `TABS` 与实际 tab 按钮**逐项一致** | 深链静默回落概览 |
| 20 | 生产目录那份快照**没漂** | 存档与产物脱钩 |
| 21 | 快照能**解析出生成器**（走模式表） | 盖了章却不可引用 |
| 22 | 生成器**开跑前**抓自己的字节 | 拿开跑的字节盖开跑的章（第 125/131 层） |
| 23 | 内联脚本**每一段**都过 `node --check` | 页面永远停在「加载…」 |

**变异台 17/17 全杀**（基线先验绿）：见
`tests/mutation_specs/README_test_goal_progress.md`。

## 6. 本轮**拒绝**做的事

* ❌ **没算「8 条目标的达成百分比」** —— 归档表把分母定死 = D1 + D2，
  手段类不产生分数。
* ❌ **没把目标 1 写成「未达」** —— 产物说「测不出」，
  且本仓的规矩是「不可判定不许被写成达成/未达」。
* ❌ **没把 2.35×（跨口径）显示成缺口** —— 屏上是 **1.79×**（同口径）。
* ❌ **没端出「投入率」「闲置比例」** —— 那两个字段名字与内容相反。
* ❌ **没给目标 4/5/6 造效果分数** —— 没有自动度量就明写「由人判断」。
* ❌ **没改 `daily_review` 那条 cron** —— 只**加**了一条新的。
* ❌ **没把 W 门槛换算成日历 ETA** —— 决策四明令禁止。
* ❌ **没动** `scripts/daily_review.py` / `scripts/d2_signal_requirement.py` /
  `scripts/d2_gap_calibers.py` / `scripts/measure_dividend_lowvol_longleg.py` /
  `aplus/reports.py` / `aplus/web/verdict.js` / `aplus/alloc.py` /
  `aplus/holdings_entry.py` / `aplus/holdings_alert.py` / `CLAUDE.md` /
  `docs/DELIVERY_DECISIONS_2026-10-20.md`。
* ❌ **没跑全量 pytest**（8~10 分钟 + 抢内存）⇒ 只跑新增与受影响的测试。
* ❌ **没建 GB 级面板** ⇒ 本任务只读产物。
* ❌ **没 git commit / push** ⇒ 交给用户。

## 7. 已知欠账

* ⬜ **`tests/e2e/test_layout.py` 的 `_TABS` 名单没加 `#tab-goal`**
  ⇒ 新 tab **没有** iPad 视口的 e2e 覆盖（该文件不在本轮的文件所有权内）。
  修法：往 `_TABS` 加一行 `("#tab-goal", "#panel-goal")`。
* ⬜ **端点把历日历史整份返回** ⇒ 快照攒到几百天时响应体会变大
  （现在每份 ~50 KB）。修法：历史只返回摘要（已经是这样），
  或加个分页参数 —— 现在的 `history` 已经是「摘要级」（不含 `latest` 那样的全文），
  ⇒ 实际增长有限，**不急**。
* ⬜ **`daily_review.json` 缺档时，目标 4/5/6 的提交归类会整块变「缺失」**
  （不回落成 0）⇒ 这是**正确**行为，但屏上会有一整块空着。
  修法（可选）：在 `daily_review` 跑完之后再跑本脚本（现在就是 22:05 > 21:40）。
* ⬜ **没有一条守卫覆盖「cron 真的每天跑了」**
  ⇒ `gaps` 会**披露**漏跑，但不会**阻止**它。
  修法（可选）：一条守卫断言「最近 N 天的快照连续」——
  ⚠️ 但那会是一条**会红**的守卫（漏跑那天它就红）⇒ 按仓内规矩，
  宁可让它红也不要制造恒真。

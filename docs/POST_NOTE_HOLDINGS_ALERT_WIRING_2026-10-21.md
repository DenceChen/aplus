# 补记：持仓级降级预警接进日频流水线（2026-10-21）

> ⚠️ **本文件是补记，不是预注册。**
> 判据全部在 `docs/PREREG_HOLDINGS_ALERT_2026-10-21.md`（**事前**写死），
> 本文件只记**接线怎么做的**、**读数从哪来**、**哪些是测不出的**。
> **一个数都不抄** ⇒ 读数一律走 `产物路径 :: JSON 路径`。

## §1 这一轮用户能打开什么

| 谁 | 打开什么 |
|---|---|
| 用户 | 概览页新的一屏「**持仓降级预警**」（`aplus/web/index.html` 的 `#holdings-alert-body`）——每只持仓一行：代码 / 名字 / 动作（减仓·观察·持有·未定）/**逐字判据** |
| 用户 | `data/reports/holdings_alert_<信号日>.json` + 同名 `.provenance.json` sidecar（**每天**自动出） |
| 代码 | `aplus/holdings_entry.py`（**新建**：建仓日 / 持仓 / 建仓分的解析 + 产物组装）、`scripts/run_daily.py::step_holdings_alert`（**新建**：接线步骤） |

## §2 接线怎么做的（三条纪律）

### 2.1 成本：**零额外面板**

日频路径的全池 = `predict` 步骤**已经在内存里**的那一份
（同一个 `build_cross_section` ⇒ 同一个 `aplus/panel.py::_factor_series`
⇒ 与生产**逐位一致**）。
⇒ 本步骤**只排序、定级、写一份 json**，**不建 GB 级面板**。

> ⚠️ 慢路径 `aplus/factor.py::compute_factors` **不是**生产实现
> （测量台实测：vol20 差 0.0468 ⇒ 合成分差 0.57 ⇒ 涨腿 10 只里 3 只名次不同）
> ⇒ 接线层**不许**碰它（守卫：`tests/test_holdings_alert_wiring.py` 的 AST 守卫）。

### 2.2 建仓分：**从建仓日那份预测文件读**，不重算建仓日的全池

`pred_<建仓日>.parquet::score`（涨腿落盘分）＝ 建仓分。
依据：测量台已验「建仓分与当日涨腿落盘分**逐位一致**」（对拍 `max_abs_score_diff = 0.0`）
⇒ 读文件是**同一个量的更便宜的一条路**，不是换了一个量。

⇒ 持仓股票若**不在**建仓日那份名单里 ⇒ **没有建仓分**
⇒ 第 4 态 `UNDECIDED`（「测不出 ≠ 没变差」），**不许**用 0 或当前分顶替。

### 2.3 失败：**不许**拖垮流水线，也**不许**静默成功

- 步骤抛异常 ⇒ `run_step` 记成 `failed: …` 并**继续**跑后面的步骤。
- 生产对拍（MISMATCH）⇒ **拒绝出产物**（半截 JSON 会被当成有效读数）。
- `predict` 没产出全池 ⇒ 这一步记成 `skipped: …`（**不是** `"ok"` —— 那是
  第 13 层那个事故形状：缺交付物却报成功）。

## §3 判据：一个字没动

四态阶梯与两个阈值**逐字**来自预注册：
`aplus/holdings_entry.py::RULE_ORDER` 与 `aplus/holdings_alert.py::bottom_cuts()`
（十分位 L=10 / 五分位 L1=L2=5，锚在石川 `data/books/cn_factor_lines.txt`）。

守卫 `tests/test_holdings_alert_wiring.py` 把 `RULE_ORDER` **逐字**钉在预注册的五条上
⇒ 改判据 ⇒ 立刻红。

## §4 诚实边界（**必须出现在产物里**，不只写在文档里）

产物带 `boundaries` 与 `what_this_cannot_prove` 两块，页面也印着：

- **不产生收益**：不改权重、不改 K、不生成买卖单。
- **不预测涨跌**：只回答**横截面位次**（今天在全池里排第几）。
- **不算盈亏**：合成分跨日比较的是**相对位次**，不是收益率。
- **不是做空信号**：A 股散户融不到券 ⇒ 唯一可执行形态是减持/卖出。

## §5 跨实现的同一性（两份副本之间要有锁）

日频路径（`aplus/holdings_entry.py`）与测量台
（`scripts/measure_holdings_alert.py`）**各自**解析建仓日 / 持仓 / 建仓分
⇒ 两份副本之间必须有锁，否则会分叉：

| 守卫 | 钉住什么 |
|---|---|
| `test_the_entry_day_resolution_agrees_with_the_measurement_script` | 建仓日解析**逐字相同** |
| `test_the_holdings_resolution_agrees_with_the_measurement_script` | 持仓名单**逐只相同** |
| `test_the_measurement_script_declares_the_same_rule_order` | 测量台产物里那份 `rule_order` 字面量 == 接线层的 |

> 🚨 **本轮没动测量台**（`scripts/measure_holdings_alert.py` 不在本轮独占范围）
> ⇒ 它仍是**独立的深验工具**：它**流式重算**全池（~4~9 分钟、不建面板）
> 并与生产产物对拍；日频路径**不重算**（复用内存里的生产全池）。
> ⇒ 两者对**同一天**必须给出**同一个判决** —— 守卫钉的是**解析层**的一致，
> **池的等价性**由测量台自己的生产对拍守（`pool.production_parity`）。

## §6 「不许报」的两道闸（2026-10-21 协调方追加）

`/api/status` 的 `stats` 在样本不足时**只给计数**，派生量**缺**（不是 0）：

| 族 | 什么时候缺 | 判据出处 |
|---|---|---|
| 比率族（命中率 / 超额 / 成本占比） | W < 10 | 决策四（`docs/DELIVERY_DECISIONS_2026-10-18.md` §4） |
| 年化族（`cagr` / `arith_ann` / `total_return` / `ann_vol` / `vol_drag`） | years < 1 | `scripts/measure_portfolio_nav.py` 的结构闸门「样本过短，不给方向」 |

⇒ **两道闸取严**（W ≥ 10 **且** years ≥ 1）⇒ 载荷带 `citable` / `citable_reason`
说明为什么缺 ⇒ 消费者分得清「**不可引用**」与「**零**」。

> ⚠️ 实测：W = 3、跨度 0.0119 年时，屏上曾出现「年化 **+100.58%**」
> ⇒ 它是把 3 天**外推**到一年 ⇒ 比命中率 62% 危险得多。
> ⇒ **tooltip 那一层也漏过**：`title` 与 `aria-describedby` 的 sr-only 文本
> 都会把数念出来 ⇒ 读屏软件读到「不许报」的命中率
> ⇒ 现在 tooltip 的**数值部分**只在 `has` 为真时才进字符串
> （口径永远留着 —— 数走、口径也走是这个仓的规矩）。

## §7 守卫与变异台

- 守卫文件：`tests/test_holdings_alert_wiring.py`（**34 条**）。
- 变异台：`tests/mutation_specs/test_holdings_alert_wiring.mut`
  ⇒ **17/17 逐条 `rc=1`**（基线先验绿；变异体先过 `compile()`；
  只有 `rc == 1` 才算被杀）。
- 前端改完**必须** `node --check`（同名 `const` 是**解析期**错误，
  pytest 全绿而页面永远停在「加载…」）⇒ 本轮实测过一次：
  `const tips` 重复声明 ⇒ `node --check` 当场报 `SyntaxError`。

### §7.1 🚨 两个**浏览器里**才暴露的缺陷（pytest 全绿时它们都在）

| # | 症状 | 病因 | 守卫 |
|---|---|---|---|
| 1 | 页面「**全池 0 只持仓**」而实际有 5 只 | 日频写 `population.n_holdings`、测量台写 `population.n_heldings` ⇒ **两个产出者写同一个文件名** ⇒ 页面读哪个**取决于最后是谁写的** | `test_the_population_block_must_have_the_same_keys_as_the_measurement_script`（AST 比**键集**：测量台 ⊆ 接线层 —— **多**一个键兼容，**改名**破坏） |
| 2 | 端点返回 `items: []`、`population: null`、`freshness: unknown` | `holdings_alert_*.json` 这个 glob **也匹配 sidecar**（`….json.provenance.json`）⇒ 端点把**章**当产物读 | `test_the_endpoint_must_not_read_the_sidecar_as_the_artifact` |

⇒ 两条的**症状都与「持仓清空了」逐位同形**（第 89 层的形状）
⇒ **端到端看一眼浏览器**抓到的东西，pytest 一条都没报。
⇒ 守卫 1 与 2 各配一条变异（M16 / M17）。

## §8 本轮**发现但没修**的别处问题（只报告）

1. 🚨 **测量台自己的生产对拍在今天会过**（实测 `MATCHES_PRODUCTION`，`max_abs_score_diff = 0.0`）
   —— 但**日频路径**用**流式重算的池**去对拍时会差 **0.002** ⇒
   **两条路径喂的池不是同一份**：流式重算的**横截面成员**与生产面板差了几只
   ⇒ z 分（robust_z 在横截面上估尺度）差一点点 ⇒ 合成分差 0.002 ⇒
   名次不变（20 只逐只相同）但分数不同。
   ⇒ **本轮的对拍门只在「池就是生产那份」时成立**（日频路径正是如此）。
   ⇒ **待办**：测量台与日频的池差异该定位到**哪几只**（成员差异还是因子差异）。
2. 🚨 **实盘配置已从「半仓 5 只」改成「满仓 10 只」**，而屏上/接口里手打的
   「半仓」标签还有别处 ⇒ **本轮已收口**（`position_label` / `exposure_display`
   从数里推 + 行为守卫），但 `scripts/deployed_account_nav.py` 那一族
   「半仓」是**对照臂**（历史口径）⇒ **不许一起改**。
3. ⚠️ `aplus/provenance.py` 的 `PATTERN_GENERATORS` 把
   `holdings_alert_<日期>.json` 登记给**测量台** ⇒ 而**日频**的产出者是
   `scripts/run_daily.py` ⇒ 同一文件名**两个产出者** ⇒
   `freshness()` 已改成「**比对对象取章自报的生成器**」
   （登记表仍然管「有没有人管这份产物」）。
4. ⚠️ `tests/test_status_compounded.py` 的两条端点守卫已按新判据改写成
   「**可引用时必须给、不可引用时必须缺**」，并补了一条**合成夹具的正向正控**
   （造 260 个交易日的底稿 ⇒ 两道闸都过 ⇒ 复利口径**必须**递出去）
   ⇒ 免得那条守卫退化成「永远不给」的装饰。

# 交付产物的身份证：登记与盖章（2026-10-20）

> **一句话**：D1 每天真正交出去的那几个产物，此前**按本仓自己的规矩是不可引用的** ——
> 不是「少一道守卫」，是**交付物缺身份证**。本轮把**登记**补上、把**章的路径**修成可搬迁的；
> **盖章接线在另一批改动里**。

## 0. 起点（实测，不是推演）

`data/reports/` 扫全量（`python -m aplus.provenance`）：

| 时点 | 总数 | fresh | stale | unknown |
|---|---|---|---|---|
| 本轮开始前 | 107 | 44 | 12 | **51** |
| 本轮登记后 | 108 | 45 | 12 | **51** |

（总数 +1 是并发落地的 `downleg_gate.json`；fresh +1 是它已带章。
`unknown` 数不变 —— **这正是本轮的真实效果**：登记让**判据能跑**，证据还没到。）

51 份 unknown 里有**四份就在交付面上**：

| 产物 | 它回答什么问题 | 写盘模块（**不是调用它的脚本**） | 本轮之前为什么 unknown |
|---|---|---|---|
| `run_status.json` | 今天出了没出单、哪一步没过 | `aplus/reports.py::write_run_status` | **未登记** + 无 sidecar |
| `holdings.json` | 实盘跨日持仓状态 | `aplus/live_rebalance.py::save_state` | **未登记** + 无 sidecar |
| `forward_results.json` | 前瞻评估结果 | `aplus/forward.py::merge_results` | **未登记** + 无 sidecar |
| `stats.json` | 已交付产品的前瞻账 | `scripts/run_daily.py::step_stats` | 已登记但**无章** |

⇒ 按 `aplus/provenance.py` 自己的规矩：**`unknown` = 不许引用**。
⇒ 「今天出了没出单」「名单状态」「前瞻结果」这三件用户当天真正拿到的东西，
   当时**没有出处可查**。

> ⚠️ **`unknown` 的含义是「判据没跑成 / 没有证据」，不是「事实为否」。**
> 本轮登记之后这四份**仍然是 `unknown`** —— 那是**对的**，不是没做完。
> 把「登记了」读成「可引用了」，就是本仓第 143 层那个「口径被更正后引用处没留标记」的近亲。

## 1. 本轮改了什么

### ① 登记（`aplus/provenance.py::DEFAULT_GENERATORS`）

新增三行：

```python
"run_status.json":      "aplus/reports.py",
"holdings.json":        "aplus/live_rebalance.py",
"forward_results.json": "aplus/forward.py",
```

外加一条并发落地时顺手收口的：

```python
"downleg_gate.json": "scripts/measure_downleg_ranking.py",
```

**为什么登记的是「写盘的那个模块」而不是调用它的脚本**：
`scripts/run_daily.py` 调 `aplus.reports.write_run_status()`，但落盘的是 `aplus/reports.py`
⇒ 登记成 `scripts/run_daily.py` 会让 `commits_touching()` 去追**调用方**的提交历史，
于是「谁动过这个产物」答的是另一个文件的提交（**第 123 层**）。
⇒ 生成器名**只从写盘点读**，绝不许从输出文件名反推。

`stats.json` 核实过：写盘点就在 `scripts/run_daily.py::step_stats` 里，
原登记值 `scripts/run_daily.py` **正确**，不动。

`downleg_gate.json` = **跌腿闸门**（D1 每天 10 只跌腿那半边的发布闸，
产物自报 `role = "DOWNLEG_GATE_ARM"` / `avoidance_list_publish_allowed: false`
⇒ **它决定跌腿名单许不许发**），与上面四份同属交付面。
它是 `scripts/measure_downleg_ranking.py` 的**第二份产物**
（`--out` → 对照臂 `downleg_ranking.json`，`--gate-out` → 闸门 `downleg_gate.json`）
⇒ **同一个生成器**。
⚠️ 登记前它带章却查不到生成器 ⇒ `tests/test_provenance.py` 那条
「盖了章却解析不出生成器」的红是**它**造成的（不是本轮的改动），
登记后那条恢复绿。

### ② 章的路径：绝对路径 → 仓相对路径

**实测**：全仓 55 份 sidecar 里有 **1 份**把 `generator` 写成**绝对路径**
（`/Users/airboom/opencode/aplus/scripts/market_nav_caliber.py`），其余 54 份都是仓相对路径
⇒ **口径孤例**。

**成因不在 `aplus/provenance.py`，而在一个调用点**：
`scripts/market_nav_caliber.py` 传的是 `__file__` ——
而 **Python 3.9 起 `__file__` 恒为绝对路径**（3.8 以前是相对路径，
所以这是版本升级带进来的缺陷）。本模块只是**照抄**了调用者给的字符串。

**后果**：那枚章**不可搬迁** —— 仓一换位置（换机器、换目录、换挂载点），
章里的路径就指向一个不存在的文件，而**章本身看上去完全正常**。

**修法**（在 `aplus/provenance.py` 里，因为只有它能改）：

- 新增 `_repo_relative_generator(generator, root)`：
  - 绝对路径且**落在仓内** ⇒ 转成仓相对路径；
  - 仓外的绝对路径 ⇒ **原样保留**（硬转 `../../..` 既难读，也会把「它在仓外」这个诚实信息藏起来）；
  - 已经是相对路径 ⇒ **原样不动**。
- `stamp()` 在**算字节哈希之前**调用它。
  ⚠️ 顺序重要：相对路径与绝对路径指向同一个文件（pathlib 里绝对右操作数覆盖左操作数）
  ⇒ `code_file_sha256` **逐位不变** ⇒ 不影响第 125 层的字节冻结与 `expect_bytes` 拒绝逻辑。

**现存那枚绝对路径的章本轮没有手改** ——
手改 sidecar 是**形状 16**（从不可测的输入捏造一个与真章无法区分的文件），
比红着更坏。它会在 `market_nav_caliber.py` 下次重跑时自动变成相对路径。
⇒ 本轮只保证「**以后**的章都可搬迁」。

## 2. 盖章接线在另一批改动里（**本轮没有做**）

`aplus/reports.py` / `aplus/live_rebalance.py` / `aplus/forward.py`
正在被**另一批改动**接手（它们在 2026-10-20 都有在飞的未提交改动），
本轮**没有动这三个模块里的任何一行**。

⇒ **登记 ≠ 盖章。** 那三份在盖章接线落地之前**仍然是 `unknown` = 不可引用。

| 产物 | 登记 | 盖章 | 当前 `freshness()` |
|---|---|---|---|
| `run_status.json` | ✅ 本轮 | ⛔ 另一批改动 | `unknown`（缺章） |
| `holdings.json` | ✅ 本轮 | ⛔ 另一批改动 | `unknown`（缺章） |
| `forward_results.json` | ✅ 本轮 | ⛔ 另一批改动 | `unknown`（缺章） |
| `stats.json` | ✅ 早就有 | ⛔ 未接 | `unknown`（缺章） |
| `downleg_gate.json` | ✅ 本轮 | ✅ 已有真章 | `fresh`（字节证据） |

**接盖章时必须照本仓的两条现成规矩**（不是新规矩）：

1. `stamp(..., expect_bytes=...)` —— 第 125 层：章上写的必须是**这次执行真正跑的字节**。
   `expect` 要在 `main()` **第一行**抓（不是盖章前抓 —— 那样检查恒真）。
2. `stamp()` 传的生成器必须是**仓相对路径**（如 `"aplus/reports.py"`）。
   传 `__file__` 现在会被规范化，但**传相对路径更直白**。

## 3. 仍未收口的清单

### 3.1 已登记但**无章**（15 份 —— 全部仍是 `unknown` = 不可引用）

```
bridge_error.json          evolution_ledger.json     factor_ic_7y.json
forward_registry.json      forward_results.json      fundamental_ic.json
holdings.json              ic_guided_race_7y.json     ic_guided_race_v1.json
lowfreq_plausibility.json  market_benchmark_audit.json
regime_control_sweep.json  run_status.json           stats.json
wf_weights_backtest.json
```

⇒ 其中 **4 份在交付面上**（见 §2 表格），**11 份是历史判决产物**。

### 3.2 **未登记**的历史产物（36 份 —— 本轮明确不收口）

```
FOSSAL_reversal_freq_2019_2023.json      alpha_beta_decomp.json
bm_ic_attribution.json                    bond_paper.json
bond_paper_state.json                     bond_param_race.json
candidate_block_audit.json                evolution_log.json
evolution_race.json  evolution_race_v2.json  evolution_race_v3.json
evolution_race_v4.json                    execution_lag_ab.json
factor_vs_random.json                     financial_lookahead_diag.json
frequency_block_t_2019_2023.json          head_bucket_probe.json
horizon_sweep.json                        independent_validation_2019_2023.json
race_composite_full_7y.json  race_composite_v1.json  race_composite_v2_signfix.json
race_final_2y_v3.json                     race_probe_6m.json  race_screen_6m.json
restatement_20261012_manifest.json        significance.json
tilt_persistence.json  tilt_persistence_k21.json
turn_integrity.json                       turnratio_vs_random.json
universe_isolation_7y.json
wf_bt_k5.json  wf_bt_k10.json  wf_bt_k42.json  wf_bt_k63.json
```

**本轮不收口的理由（三条，都不是「懒得做」）**：

1. **它们不是今天的交付面。** 交付面是「用户当天打开能用的东西」
   —— 今天出了没出单、名单、前瞻结果、前瞻账。
   上面 36 份是**历史实验产物**（因子归因、赛马、对照臂、占位审计），
   它们进 `data/reports/` 是因为「结论入库」这条仓惯例，不是因为它们在交付面上。
2. **逐个登记要逐个读源码确认写盘点**（第 123 层：生成器名只从写盘点读，
   绝不许从输出文件名反推）⇒ 那是 36 次独立取证，
   而本轮的任务面是 D1 交付面 ⇒ **摊到那一批里做，不要混进这一轮**。
3. ⚠️ **但它们**不是**永久豁免**：`unknown` = 不许引用，
   ⇒ **引用其中任何一份之前，先给它登记 + 盖章**（或重跑）。
   ⇒ 若某一份被写进 `docs/` 或 `CLAUDE.md` 当结论引用，那它就**必须**收口
   —— 「在仓里」不等于「在用」。

### 3.3 `stale`（12 份）—— 本轮不碰

```
VOID_reversal_freq_2019_2023__cost_10x_and_double_deduct.json  factor_ic_7y_v2.json
k_sweep_summary.json   market_timing_ceiling.json   random_arm_nav.json
random_arm_nav_smoke.json   weight_vs_spread.json
wf_weights_backtest_k5/k10/k42/k63.json   xsec_sigma_r.json
```

⇒ 过期是**已知的坏**，处置方式是**重跑**（GB 级），不是登记。
⇒ 登记一张表的条目不会让它变新，只会让人以为它有人管。

## 4. 守卫

`tests/test_delivery_artifacts_are_citable.py`（**12 条**，先红后绿）：

| # | 守卫 | 钉住的是哪一层 |
|---|---|---|
| 1 | 五份交付面产物**都有登记** | 登记表 |
| 2 | 登记的生成器**在盘上存在** | 第 123 层（查不到 ≠ 没有） |
| 3 | 登记键**与产物文件名逐字相等** | 全路径 / 漏扩展名都判红 |
| 4 | 登记的生成器**就是写盘的那个模块** | AST 扫写盘点（不是子串 —— 第 13 层） |
| 5 | `stamp()` 写出的 `generator` 是**仓相对路径** | 章可搬迁 |
| 6 | 已经是相对路径的**不许被改写** | 反向对照 |
| 7 | 仓外的生成器**保留绝对路径** | 「它在仓外」这个信息不许被藏起来 |
| 8 | **已登记但无章 ⇒ 仍然是 `unknown`** | ★ 登记 ≠ 盖章；判据不许被放松 |
| 9 | 盖章之后**才**变 `fresh` | 正控：证明第 8 条不是恒真 |
| 10 | 生成器不在盘上 ⇒ `unknown`，且理由**不许**出现「没有提交动过它」 | 第 123 层 |
| 11 | 判据取值**恰好三态** | 不许加「assumed_fresh」第四态 |
| 12 | 本文件**不写生产产物路径** | audit hook（只看本进程开过的写句柄） |

### 本轮被守卫自己咬到的一次（**已修**，记在这里）

第一版第 12 条里还写了「生产目录里必须**逐个**有那几份产物」。
⇒ **当场红**（2026-10-20 23:41）：另一批改动正在重写 `data/reports/`，
本条读到的是**别的进程正在替换文件的那一瞬间**。
⇒ 症状读起来像「交付面清单与现实脱节」，真因是**并发写同一个目录**
⇒ 那是一条**会随机误报**的守卫（第 112 层：过严的守卫和恒真的一样有害）。

**处置**：逐个存在性断言**删掉**，只保留「目录存在 + 目录里有 json」
（目录不会被并发写删掉）。「产物在不在」由产物的守护者与用户负责，
不该由本文件在别人写盘的窗口里去查。

**同类修法（源码读取）**：第 ④ 条要读 `aplus/reports.py` 等源文件，
而那三个文件**正在被编辑** ⇒ `write_text` 是「截断 + 写」⇒
一次并发 `read_text` 可能读到**半截文件** ⇒ `ast.parse` 抛 `SyntaxError`
⇒ 守卫随机误报。⇒ 加 `_read_source_stably()`：**只在语法不完整时重读**
（最多 3 次 / 50 ms）；「解析成功但内容不对」**不许**被重试吃掉
（那是真实的判红）。

**变异验证（7 条，rc==1 才算被杀）**：

| 变异 | 结果 |
|---|---|
| M1 删掉 `run_status.json` 的登记 | ✅ **KILLED**（7 failed） |
| M2 把 `holdings.json` 登记成不存在的生成器 | ✅ **KILLED** |
| M3 把 `forward_results.json` 登记成调用方脚本 | ✅ **KILLED** |
| M4 去掉 `stamp()` 里的路径规范化 | ✅ **KILLED** |
| M5 放松判据：不再报「没有溯源 sidecar」 | ✅ **KILLED** |
| M6 去掉 `freshness()` 的「不变量兜底」 | ⚠️ **SURVIVED** ⇒ 见下 |
| M7 删掉 `downleg_gate.json` 的登记 | ✅ **KILLED**（3 failed） |

### M6 存活 = **等价变异体**，不是守卫有洞（已用探针自证）

M6 删掉的是 `freshness()` 里那条「不变量兜底」分支
（`if gen is None or st is None: return unknown`）。

**它为什么存活**：该分支在当前代码里**不可达**。
逐条推：`problems` 为空 ⇒ ① `bare` 为假（否则裸名那条已经 append）；
② `st` 非 None（`st is None` 且非裸名时，C2 一定 append）；
③ `gen` 非 None（`gen` 为 None 且非裸名时，「不在 DEFAULT_GENERATORS 里」那条一定 append）。
⇒ `problems` 为空 ⇒ `gen` 与 `st` **必非空** ⇒ 兜底永不触发。

**探针自证**（先证明探针看得见它，再拿去判别人）：

- 自证用例：把「不在 DEFAULT_GENERATORS 里」那条 append 短路 ⇒ 兜底**确实可达**
  （`FALLBACK_REACHED`）⇒ **raise 探针有效**。
- 真实数据：**108 份生产产物 + 108 个裸名 + 62 个已登记键** ⇒ 兜底**一次都没触发**。

⇒ M6 删掉的是**不可达的防御代码**，行为逐位不变 ⇒ **不追**。
（记忆 `assertion-right-explanation-wrong`：**变异存活先问「它落在哪」**，
不许先归因「守卫没用」。）

⚠️ 这条**保留**兜底分支是有理由的：它是**未来**改 `problems` 构造逻辑时的安全网。
删掉它不会让今天变好，只会让明天崩在 `None.get(...)` 上（离病因很远）。

## 5. 判据纪律（本轮守住的、且不许被放松的）

1. **不许把 `unknown` 改写成 `fresh`。** 登记 + 盖章是**两件事**；
   缺章的产物**仍然是 `unknown`**。守卫 ⑧ 与 ⑨ 专门区分这两种情况。
2. **`unknown` 的含义是「判据没跑成 / 没有证据」**，不是「事实为否」。
   守卫 ⑧ 断言理由句里必须明写「没有 sidecar」⇒ 不许用一句含糊的
   「大概没事」把它盖过去。
3. **不许为了让 `freshness()` 变绿而放宽任何判据。** 守卫 ⑩⑪ 钉住判据本身。
4. **生成器路径不存在 ⇒ 判据本身没跑**，不是「没有提交动过它」。

## 6. 并发冲突说明（**给下一个读这份文档的人**）

⚠️ **2026-10-20 本轮期间，`aplus/provenance.py` 被两个 agent 同时写过**：

- 本轮（交付面收口）：加 4 条登记 + `_repo_relative_generator`。
- 另一批（`docs/PREREG_DEPLOYED_D2_2026-10-20.md` 那条线）：
  加了 `"deployed_d2_verdict.json": "scripts/measure_deployed_d2.py"`。

⇒ **两者都在文件里，当前 53 条 `tests/test_provenance.py` 全绿**
（含 AST 数键的「重复登记」守卫）。
⚠️ **若两边下一次又同时写这一个文件，仍要用字节备份还原 + sha256 逐位对拍**，
不要 `git checkout`（git 是历史不是备份）。

⚠️ **另一批改动在 2026-10-20 23:41 前后提交了
`60401c9 交付: 收口五处「界面/产物在骗用户」的生产缺陷 …`**
⇒ 本文件 §0 的实测数字取自 **23:35 前后**的扫描，
而 `data/reports/` **仍在被并发改写** ⇒ **数字会漂**，
引用时以当场重扫为准（`python -m aplus.provenance`）。

## 7. 下一步（**只列，不承诺**）

| # | 事项 | 谁做 | 代价 |
|---|---|---|---|
| 1 | 给 `run_status.json` / `holdings.json` / `forward_results.json` 接 `stamp(..., expect_bytes=)` | **另一批改动**（正在改那三个模块） | 小 |
| 2 | 给 `stats.json` 接 `stamp(...)`（`scripts/run_daily.py`） | 谁动 `run_daily.py` 谁接 | 小 |
| 3 | 36 份历史产物逐个取证 + 登记 | 单独一批 | 中（36 次独立取证） |
| 4 | 15 份「已登记无章」里凡是被 `docs/` 引用的，补章或重跑 | 按引用面筛 | 不定 |
| 5 | 12 份 `stale` 重跑 | 按需 | GB 级，**不与别的事同跑** |
| 6 | `market_nav_caliber.json` 那枚绝对路径的章 | 下次重跑 `market_nav_caliber.py` 时自动变相对 | 中（GB 级） |

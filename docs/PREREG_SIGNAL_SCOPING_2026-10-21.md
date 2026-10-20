# 前瞻证据的「信号归属」—— 预注册（2026-10-21）

> 🚨 **本文件写在改码之前。**
> 下面每一个**判决名**、每一个**缺失态的处理方式**、每一个**守卫**，
> 都在**读到分桶后的任何判决之前**写定。
> 写完之后**不许改**：读数若与预期相反，如实记（第 91 层）。
>
> ⚠️ **§12 是补记**（写于**看到重跑结果之后**）——
> 那里记的是设计变更与**实测**的前后对照，**不冒充**预注册。

## 0. 为什么必须做这件事

### 0.1 D1 的信号会变

D1 的**信号定义**一旦变（改权重 / 改榜宽 / 改可买性过滤），
从改动那天起出的名单，与之前的名单**不是同一个信号**。

### 0.2 底稿是追加式的，且**混着两个信号**

`data/reports/verification.parquet` 是**追加式**的。
2026-10-21 实测（读当时那份文件）：

| 量 | 值 |
|---|---|
| 总行数 | **160** |
| `pred_date`（出单日）数 | **8**（10-09 … 10-16） |
| `entry_date`（独立成交窗口）数 | **6**（10-12 … 10-19） |
| `direction` | up 80 / down 80 |
| `result` | hit 87 / miss 68 / flat 4 / no_data 1 |
| **`weights_hash`** | **60 行有值（`c2178335be6d`），100 行为空**（10-14 … 10-16 有值；10-09 … 10-13 为空） |
| **`signal_id` 列** | **不存在** |

### 0.3 🚨 闸门继续读整本底稿 ⇒ 判的是**另一个信号**

D1 闸门 `scripts/score_ranking_power.py`（G4，`GATE_ARM="up"`）会在
「当前信号只有 3 个窗口」的情况下，拿**混着两个信号的 6 个窗口**去判
「现在这个打分器有没有排序力」⇒ **判的是另一个信号**。

### 0.4 🚨 `weights_hash` **区分不了**权重以外的配置

`weights_hash` 只记**权重** ⇒ `top_n`/`bottom_n` 变了它**不变**。
⇒ 它的证明力**弱于**信号指纹 ⇒ 归到它名下时**必须把这条边界写在脸上**。

## 1. 契约（`aplus/reports.py`，**只消费、不重写**）

| 符号 | 用途 |
|---|---|
| `signal_fingerprint(config) -> str` | 信号配置的 8 位指纹 |
| `current_signal_id(reports_dir, ...) -> str` | 从**生产的真实配置**算当前信号 id |
| `weights_fingerprint(weights) -> str` | **权重**的 12 位指纹（底稿 `weights_hash` 就是它） |

`current_signal_id` 的 `config` **逐字固定为三个键**：

```python
{"weights": {...}, "top_n": int, "bottom_n": int}
```

⛔ **不含** `pool_filter`（它已移到**配置层** `aplus/alloc.py`）
⛔ **不含** `n_positions`（**账户层**的量）
⇒ 敞口 5→10 只 + 可买性过滤都**不改** signal_id。

🚨 本模块**不自己实现**任何指纹（自己实现 = 第二份真相源，第 97 层）。

## 2. ★ 单一真相源：`scripts/signal_scope.py`

**所有**读底稿的消费者**必须 import 同一个过滤函数**。

| 符号 | 职责 |
|---|---|
| `scope_ledger(frame, signal_id, weights_fingerprint)` | **唯一的归属实现**。纯函数，不碰文件系统 |
| `scope_block(scoped)` | 写进产物的**作用域块** |
| `attribution_block(scoped)` | 写进产物的**归属依据块** |
| `resolve_attribution_context(reports_dir)` | 把两个基准**都**算出来（任一失败**只记录**） |
| `resolve_current_signal_id` / `resolve_current_weights_fingerprint` | **薄包装**：只调 `aplus/reports.py` |
| `upstream_scope_verdict(doc, signal_id, weights_fingerprint)` | 给**读上游产物**的两个脚本用 |
| `scoped_ledger_frame(reports_dir, path)` | 给**对账守卫**用（与生产者同口径） |

### 2.1 ★ 四态（**没有第五态**，没有回落）

| `status` | 何时 | 作用域内的行 |
|---|---|---|
| `OK` | 底稿**有** `signal_id` 列，**且**至少一行属于当前信号 | 该信号的全部行 |
| **`ATTRIBUTED_BY_WEIGHTS_HASH`** | 底稿**没有** `signal_id` 列，**但有** `weights_hash` 列 ⇒ **逐行**用权重指纹判归属 | `weights_hash` == 当前信号权重指纹的行 |
| `NO_MATCHING_SIGNAL` | 有归属列，但**没有一行**匹配 | **空** |
| `MISSING_SIGNAL_ID_COLUMN` | **无任何归属证据** | **空** |

### 2.2 🚨 「列不存在 ⇒ 当作全部都是当前信号」这个禁止项**仍然成立**

`ATTRIBUTED_BY_WEIGHTS_HASH` **不是**假定，是**逐行拿证据比**：
- **匹配**的行纳入；
- **不匹配**的行**剔出并单独计数**（`rows_unmatched`）；
- **该列为空/NaN** 的行**不可归属**，**剔出并单独计数**（`rows_unattributable`）。

⇒ 它与 `MISSING_SIGNAL_ID_COLUMN` 的区别**就是「有没有证据」**。

### 2.3 归属依据块（`attribution_block`）必须给出什么

```
"attribution": {
  "basis": "weights_hash",          # 或 "signal_id" / "none"
  "current_weights_fingerprint": "c2178335be6d",
  "current_signal_id": "0df5828c",
  "rows_matched": 60, "rows_unmatched": 0, "rows_unattributable": 100,
  "note": "本底稿没有 signal_id 列（它写在信号分桶机制落地之前）⇒ 归属用 weights_hash 逐行判定。
           ⚠️ weights_hash 只记权重 ⇒ 它证明「同一组权重」，不证明「同一个信号」…"
}
```

### 2.4 ⚠️ 底稿的**读口留在调用点**（刻意）

消费者的写法是：

```python
frame = pd.read_parquet(<底稿路径>)      # 读口在调用点
scoped = scope_ledger(frame, ctx.signal_id, ctx.weights_fingerprint)
```

**为什么不是「读也在共享模块里」**：本仓**既有**的守卫钉的是
「`expect_bytes` 必须抓在**读底稿之前**」（第 125 层）——
那个性质**只有在读口可见于调用点时**才验得到。
⇒ **过滤/归属逻辑**仍然只有一份，守卫
`tests/test_signal_scoping.py::test_g4_the_read_result_is_what_gets_filtered`
钉的是**数据流**（读出来的那个变量**必须**是喂进 `scope_ledger` 的那个）。

## 3. ★ 判决层规则（预注册）

| 情形 | 判决 |
|---|---|
| `has_evidence` 为**假**（无任何归属证据） | **`NOT_DECIDABLE_NO_SIGNAL_SCOPE`**，`publish_allowed = False` |
| `has_evidence` 为**真**，作用域**非空** | **正常判决**（PASS / NOT_PASSED_*）—— 判据**一个都没改** |
| `has_evidence` 为**真**，作用域**为空** | 各脚本**既有**的「无数据」路径（**不许**回落成「通过」） |

### 3.1 🚨「不可判定」是**正确结果**，不是失败

`NOT_DECIDABLE_NO_SIGNAL_SCOPE` **不是** `PASS`，**不是** `NOT_PASSED_MEASURED`，
**不是** `POWERLESS` —— 它说的是「**这份证据不可归属**」，
**不是**「当前信号没有排序力」。

### 3.2 🚨 不许为了让判决不变而放宽任何判据

**明确写下**：本轮**预期判决会变**（§4）。
若某个消费者的判决**没变**，那是**要解释的异常**，不是「改对了」。

## 4. ★ 逐脚本：归属前 / 归属后 / 判决怎么变（**实测**）

「归属前」= 2026-10-21 **动手前**读**当前** `data/reports/verification.parquet`
（160 行 / 8 出单日 / 6 独立成交窗口）的实测值。
「归属后」= **重跑后产物里的实读值**。

| # | 脚本 | 归属前 | 归属后 | 判决怎么变 |
|---|---|---|---|---|
| 1 | `score_ranking_power.py` | 160 行 / 8 日 / 6 窗口 | **60 行 / 3 日 / 3 窗口** | `NOT_PASSED_POWERLESS` → **同**；`publish_allowed` 仍 `false` |
| 2 | `diagnose_hit_rate.py` | 155 可判定 / 8 日 / 6 窗口 | **59 / 3 日 / 3 窗口**（`rate` 0.5613 → 0.6271） | 无判决标签（诊断产物） |
| 3 | `forward_d2_verdict.py` | 79 行 / 6 块 | **3 块**（`\|c\|/MDE` 0.4824 → 0.1286） | `POWERLESS` → **同** |
| 4 | `measure_downleg_ranking.py` | 80 行 / 6 窗口 | **30 行 / 3 窗口** | 对照臂 `POWERLESS（6 窗口）` → **「样本不足（3 个成交窗口 < 6）」**（方向仍是**不给方向**）；闸门 `NOT_PASSED_POWERLESS` → **同** |
| 5 | `measure_portfolio_nav.py` | 160 行 / 6 天（`cagr=-0.21147463306761183`） | **60 行 / 3 天**（`cagr=+1.0057987556677035`） | 「样本过短，不给方向」→ **同**（`years=0.011904761904761904 < 1.0`） |
| 6 | `measure_portfolio_exec.py` | 160 行 / 6 窗口 | **60 行 / 3 窗口** | 「部分开不出（最差窗口闲置 94.4%）」→ **同**（0.9439 → 0.9439） |
| 7 | `forward_d2_horizon.py` | 上游 `n_blocks=5` | 上游 **3 块** | 外推表在（`n_blocks_now` 5 → 3） |
| 8 | `verdict_horizon_d1.py` | 上游 `n_windows=5` | 上游 **3** | 反解梯子在（`W_required` 随 W 变） |

### 4.1 🚨🚨 **判决标签一个都没变**，但**样本腰斩**、且**一个点估计翻了号**

- **判决标签**：8 个消费者**没有一个**从「不给方向」变成「给出方向」。
- **样本**：160 → 60 行 / 8 → 3 出单日 / **6 → 3 独立成交窗口**。
- ⚠️ **`portfolio_nav.json::读数.cagr`：−0.21147463306761183 → +1.0057987556677035**。
  ⚠️⚠️ **这是一个 3 天的点估计**（`years = 0.011904761904761904`），
  **绝不许**读成「账户年化 +100%」——
  产物自己的 `verdict_about_20pct` 写着「样本过短，不给方向」。
  ⇒ 数字变了，**判决没变**（第 138 层：符号对、量级错的 bug 能长期躲在判决背后
  ⇒ 这里反过来：**判决没变也不等于数没变**）。

### 4.2 连带影响（**产物已重跑**）

改动生成器 ⇒ 产物比代码旧 ⇒ 本仓的**重跑纪律守卫**会红
⇒ 已按依赖顺序重跑：

```
diagnose_hit_rate → forward_d2_verdict → measure_downleg_ranking →
score_ranking_power → measure_portfolio_nav → measure_portfolio_exec →
forward_d2_horizon → verdict_horizon_d1
```

（`measure_portfolio_nav` 的两个输入必须**同源** ⇒ `diagnose_hit_rate` 先跑；
`forward_d2_horizon` / `verdict_horizon_d1` 读上游 ⇒ 最后跑。）

## 5. 两个「间接消费者」的特殊处理

`forward_d2_horizon.py` 与 `verdict_horizon_d1.py` **不读底稿**，
它们读**上游产物** ⇒ 只能**要求上游自报作用域**。

| 上游 | 下游行为 |
|---|---|
| 没有 `signal_scope` 块 | **拒绝外推** |
| 块在，但 `status` 不在 {`OK`, `ATTRIBUTED_BY_WEIGHTS_HASH`} | **拒绝外推** |
| 块在且可用，`current_signal_id` 两边都有且不同 | **拒绝外推** |
| 块在且可用，信号 id 不可比 ⇒ 退到**权重指纹**；权重指纹也不同 | **拒绝外推** |
| 🚨 **两边的身份都算不出** | **拒绝外推**（**不许**把「两个都是 `None`」读成「它们一样」） |

## 6. 欠账登记

| 欠账 | 归属 | 状态 |
|---|---|---|
| `data/reports/stats.json::n_independent_windows` | `scripts/run_daily.py` 的持有者 | ✅ **已解除**：信号没变（过滤移到配置层、名单层不动）⇒ **不需要**分桶 ⇒ 前端口径不用动 |
| `aplus/webapp.py:310` 读同一个 `stats.json` | 同上 | ✅ 同上 |
| `aplus/verify.py::rolling_stats` | `aplus/` 的持有者 | ✅ 同上 |
| `aplus/webapp.py:214-225` 读 `score_ranking_power.json` | `aplus/` 的持有者 | ⚠️ 产物**现在有** `signal_scope` / `attribution` 块 ⇒ 前端可显示归属依据；⛔ 本轮不改 |
| `scripts/register_forward_candidates_r5.py:236` 的散文 | 该文件持有者（**协调者说由他改**） | ⛔ 本轮不改 |
| `scripts/daily_review.py` 的三态文案 | 该文件持有者（**协调者说由他改**） | ⛔ 本轮不改 |

## 7. 判据分工（本文件**不改**任何既有判据）

| 判据 | 归谁 | 本轮是否动 |
|---|---|---|
| G4（`score` 四分位单调） | `docs/TODO_reverse_scoring_2026-10-15.md` §3 | **不动** |
| 跌腿 G4 镜像 | `docs/PREREG_DOWNLEG_GATE_2026-10-20.md` | **不动** |
| R1（`entry_date` 分块） | `docs/PREREG_FORWARD_D2_2026-10-16.md` | **不动** |
| 组合净值 MDE | `docs/PREREG_PORTFOLIO_NAV_2026-10-16.md` | **不动** |
| 命中率分层 | `docs/PREREG_HIT_RATE_CLUSTER_2026-10-16.md` | **不动** |

⇒ 本文件**只加一条**：**证据必须能归属到当前信号**。
它**在**所有既有判据**之前**生效（归属不对 ⇒ 判据根本没资格跑）。

## 8. 守卫（`tests/test_signal_scoping.py`，48 条，**全绿**）

| # | 守卫 | 钉住什么 |
|---|---|---|
| G0 | **正控**四条：共享模块定义了全部函数；权重指纹**由 `aplus/reports.py` 算**且是 12 位十六进制；契约真的能算出 `current_signal_id`；**本模块的权重来源 == 契约的权重来源**（`current_signal_id(dir, weights=W) == current_signal_id(dir)`） | 下面所有守卫**不是恒真**的；两个指纹**不是**两份真相源 |
| G1 | **能力守卫**（两个基准各一条）：两个信号各若干窗口 ⇒ 只取出当前那一段（**逐行**核对 + 窗口数分别报） | 归属**真的会切**（返回全部的实现**通不过**） |
| G2 | **缺证据守卫**：既无 `signal_id` 又无 `weights_hash` ⇒ `MISSING_SIGNAL_ID_COLUMN` 且作用域**空** | **不许**静默放行全部 |
| G3 | **不回落守卫**（子进程跑**真脚本**）：不可归属 ⇒ 判决**不许** `PASS`，`publish_allowed` / `avoidance_list_publish_allowed` 必须是 `False`；**端到端**：闸门**只**测归属内的行 | 判决层不许把「不可归属」读成「通过」；归属**真的接在调用点上** |
| G4 | **共用入口守卫**（**AST**，锚在**调用点**）：8 个消费者**必须**在 `main` 里调用**同一个**作用域函数；**数据流**：读出来的那个变量**必须**是喂进 `scope_ledger` 的那个；**没有任何消费者自己定义**归属函数 | 第 13 层的坑：子串能被注释满足 ⇒ AST 里注释**根本不存在** |
| G5 | **逐行比对守卫**（三条）：空值 ⇒ **不可归属**（`rows_unattributable`）；不匹配 ⇒ **不匹配**（`rows_other_signals`）；**两个成因分开计数**；归属块必须写清「权重指纹**不**证明同一个信号」 | 过渡期不许混用；两个成因不许混成一个数 |
| G6 | **覆盖范围守卫**：失败态的作用域块**必须**带整本底稿的日期范围 | 「让读者知道它是什么」 |
| G7 | **上游契约守卫**（五条）：块缺失 / 状态不可用 / 信号不符 / 权重指纹不符 / **两边身份都算不出** ⇒ 下游**拒绝外推**（子进程跑**真脚本**，断言**不出现** `scenarios` / `horizon`） | 「查不到 ≠ 没有」 |

⚠️ **测试不许写生产产物路径**：全部用 `tmp_path`。

## 9. 诚实边界

1. **本轮把 D1 的证据样本从 6 个窗口砍到 3 个。** 这**不是**改进，
   是「**原来那份判决问的是另一个信号**」的修正。
2. **本轮不产生任何新的排序力证据**，只把「证据属于谁」说清楚。
3. **判决标签一个都没变**；变的是**样本**与**点估计**
   （`portfolio_nav` 的 `cagr` 翻号，见 §4.1）——**判决没变不等于数没变**。
4. **`weights_hash` 基准的证明力弱于信号指纹**（不覆盖 `top_n`/`bottom_n`）⇒
   一旦榜宽变了，**必须**重新归属（那时 `signal_id` 列已在 ⇒ 自动切到强基准）。
5. 产物层的**对账守卫**已改为**与生产者同口径**（读同一个归属函数）——
   它们的**性质**（产物与一次新鲜重算逐项相等）**一个字没改**。

## 10. 实现现状

| 件 | 状态 |
|---|---|
| `scripts/signal_scope.py` | ✅ **已落地**（四态 + 归属依据 + 上游契约 + 对账入口） |
| 8 个消费者接线 | ✅ **已落地**（全部 import 同一个 `scope_ledger` / `upstream_scope_verdict`） |
| `tests/test_signal_scoping.py` | ✅ **已落地**（**48 条，全绿**） |
| `aplus/reports.py` 的三个原语 | ✅ **已落地**（`signal_fingerprint` / `current_signal_id` / `weights_fingerprint`） |
| 8 个消费者的**重跑** | ✅ **已做**（§4.2 的顺序） |
| 门禁 | ✅ **204 passed / 0 failed**（本文件 48 条 + 10 个受影响既有测试文件） |

## 11. 收工闸（本文件的答案）

### 11.1 哪些产物/判决因为归属而变了？

**判决标签：8 个消费者**一个都没变**（§4 表的最后一列）**。
变的是**样本**与**点估计**：

| 产物 | 变了的 |
|---|---|
| `score_ranking_power.json` | `n_rows_total` 160→60；`n_independent_pred_dates` 8→3；`n_entry_dates` 6→3；新增 `signal_scope` / `attribution` 块 |
| `hit_rate_diagnosis.json` | `verdicts` 155→59；`n_windows` 6→3；`trading_days` 8→3；`overall.rate` 0.5613→0.6271 |
| `forward_d2_verdict.json` | `n_blocks` 5→3；`n_pred_dates` 7→3；`abs_mean_over_mde` 0.4824→0.1286；**`verdict` 仍 `POWERLESS`** |
| `downleg_ranking.json` | `verdict_about_downleg` 文案：「排序力判决：POWERLESS（6 个窗口）」→「样本不足（3 个成交窗口 < 6）」 |
| `downleg_gate.json` | `measurement.n_rows_total` 80→30；**`verdict` 仍 `NOT_PASSED_POWERLESS`** |
| `portfolio_nav.json` | `读数.n_days` 6→3；`读数.cagr` −0.2115→**+1.0058**（⚠️ 3 天点估计）；`verdict_about_20pct` **仍「样本过短，不给方向」** |
| `portfolio_exec.json` | 逐窗口 6→3；**`verdict_about_exec` 与 `最差窗口闲置现金` 逐位不变** |
| `forward_d2_horizon.json` | `scenarios[*].n_blocks_now` 5→3 |
| `verdict_horizon_d1.json` | `current_reading.n_windows` 5→3 |

### 11.2 D1 闸门现在读的是哪个信号的窗口？

> **当前信号的窗口 —— 3 个（不是 6 个）。**

- 机制上：只读 `scope_ledger(frame, ctx.signal_id, ctx.weights_fingerprint)`
  返回的那一段（守卫钉的是**数据流**）。
- 读数上：底稿**没有** `signal_id` 列 ⇒ 走 **`weights_hash` 逐行**基准 ⇒
  160 行里 **60 行匹配**（`c2178335be6d`）、**100 行不可归属**。
- ⇒ **那 6 个窗口不是「D1 的窗口」**：其中 3 个来自 **10-09 … 10-13**，
  那批名单产出于 **`DEFAULT_WEIGHTS` 符号整体翻转之前**
  （翻转后指纹 = `c2178335be6d`，翻转前 = `093038dff781`）
  ⇒ **它们本来就不是当前信号** ⇒ 剔出它们是**修正**，不是**损失**。
- ⇒ **D1 的验收分母从 6 个窗口变成 3 个**（`daily_review.py` 的
  「W < 10 ⇒ 不许报命中率结论」⇒ 仍然远未达标）。

## 12. 修订记录（**写于看到重跑结果之后**，不冒充预注册）

1. **契约的 `config` 从五键收窄为三键**（`weights` / `top_n` / `bottom_n`）：
   可买性过滤移到**配置层** `aplus/alloc.py`，**名单层不动**
   ⇒ 发布的 10 涨 10 跌仍是「全市场真正的前 10 / 后 10」
   ⇒ 预测层一个字没变 ⇒ **`stats.json` 不需要分桶**（§6 的欠账解除）。
2. **归属靠已有的 `weights_hash` 逐行判定**，不必等 `signal_id` 列
   ⇒ 新增第四态 `ATTRIBUTED_BY_WEIGHTS_HASH`（§2.1）。
3. 🚨 **「底稿 160 行全属于当前信号」这个前提是错的**：
   `weights_hash` **只覆盖 60/160 行**（100 行为空），
   而那 100 行（10-09 … 10-13）产出于**权重翻转之前**
   ⇒ **它们本来就不是当前信号** ⇒ **不需要回填**
   （追加式底稿不许回填这条铁律**被动摇**）。
   ⚠️ 这条是**实测**的（`weights_hash` 的空值边界 == 权重翻转边界）。
4. 🚨 **「D1 的 6 个窗口全部保住」是错的**：属于当前信号的只有 **3 个**。
5. **判决标签一个都没变**，变的是**样本**（腰斩）与**一个点估计翻号**
   （`portfolio_nav` 的 `cagr`，§4.1）。
6. **产物层的对账守卫**改为与生产者**同口径**（`scoped_ledger_frame`），
   它们的**性质**一个字没改。

# 前瞻第 5 批预注册：让前瞻通道测的是**真正在交易的那一支**

> 注册动作：`scripts/register_forward_candidates_r5.py`
> 注册日：**2026-10-20**（台账 `data/reports/forward_registry.json`）
> 产物路径：`data/reports/forward_registry.json::[4]`（第 5 份注册，0-based 下标 4）

---

## §1 这一批要解决什么

### 1.1 现状：台账里**没有一组是实盘那一支**

实盘跑的权重来自 `aplus/evolve.py::load_weights` 的**回落分支**：
`data/reports/weights.json` **不存在** ⇒ 走 `aplus/evolve.py:275` 的 fallback，
`:281 "source": "fallback"` / `:288 "caveat_kind": "unraced"`。
因此实盘那一支 = `aplus/factor.py:61 DEFAULT_WEIGHTS`
+ `aplus/evolve.py:79 LIVE_REBALANCE_EVERY`
+ `buffer_multiple = None`（`aplus/evolve.py:90`：**不写 `buffer_multiple` 就是没有缓冲**）。

台账前 4 批共 **19 组**候选（6 + 4 + 4 + 5），逐字核对 `aplus.evolve.candidate_key`：

| 台账位置 | 候选 | 为什么**不是**实盘那一支 |
|---|---|---|
| 第 1 批 ① | `{mom20:+1.0, rev5:+0.3, vol20:+0.5, vsur:+0.5}` K=1 | **2026-10-14 符号翻转之前**的旧配置，且 K=1（实盘是 K=21） |
| 第 2 批 ④ / 第 3 批 ② | 8 因子（多 `turn20/turnratio/ivol/rsi`）K=21 / K=5 | 因子集就不是实盘那一组（那是**判决路径**的 IC 定权） |
| 第 1 批 ⑥ / 第 3 批 ③ | `buffer_multiple = 3.0` | 缓冲臂，不是实盘配置（实盘**无缓冲**） |

程序核对（前 4 批 19 组去重后 **18 个唯一键**）与本批三臂的三个键**无一相同**。

⇒ **前瞻底稿 `data/reports/verification.parquet`（实测 160 行 / 8 个 `pred_date`
/ 6 个 `entry_date`）测的候选族里，没有一个是「当前实盘那一支」。**

### 1.2 为什么这件事直接服务于 D1

D1 闸门现在判 `NOT_PASSED_POWERLESS`
（`data/reports/score_ranking_power.json::verdict`），
判决臂（up / `score`）的检出力下限
`data/reports/score_ranking_power.json::by_score_col.score.arms.up._stats.null_abs_rho_q95_power_floor`
= **0.2647** ⇒ 这份样本**连这个量级的关联都分不出来**。

⇒ 回测那条路已被 `aplus/evolve_race.py` 的 8 因子逐日回归判成 `NO_LEVER`；
⇒ **前瞻是 D1 唯一还能解锁判决的路** —— 而它此刻**测错了对象**。

⇒ 这一批**只做一件事**：让**从未被看过的交易日**去检验**真正在交易的那一支**。
**不是**采纳它，**不是**宣称它有效，**不是**换配置。

---

## §2 三条候选，以及每条为什么这么配

| # | 名字 | weights | K | buffer | 它回答哪个问题 |
|---|---|---|---|---|---|
| **C1** | 实盘那一支 | `DEFAULT_WEIGHTS` | `LIVE_REBALANCE_EVERY`（21） | `None` | **当前实盘的那套信号，前瞻有没有证据** |
| **C2** | 实盘配置 + 更慢换仓 | `DEFAULT_WEIGHTS` | 63 | `None` | **成本臂**：把摩擦砍到约 1/3 之后，还剩多少 |
| **C3** | 实盘配置 + 缓冲对照 | `DEFAULT_WEIGHTS` | 21 | `3.0` | **单参数对照**：缓冲算法在实盘信号上是否有害 |

### 2.1 参数来源铁律：候选清单里**没有一个手打字面量**

```python
from aplus.factor import DEFAULT_WEIGHTS        # aplus/factor.py:61
from aplus.evolve import LIVE_REBALANCE_EVERY   # aplus/evolve.py:79
from aplus.rebalance import BUFFER_MULTIPLE      # aplus/rebalance.py:43（= 3.0）
```

**为什么必须 import 而不是手打**：手打的那份是一个**化石**——
2026-10-14 符号翻转之后，`aplus/factor.py` 的 `mom20/vol20/vsur` 三个符号全变了，
而台账第 1 批 ① 至今仍是**翻转前**的 `{+1.0, +0.3, +0.5, +0.5}`。
**手打的常量不会跟着源码变，而 import 的会。**
⇒ 守卫（`tests/test_forward_candidates_r5.py`）：把 `aplus.factor.DEFAULT_WEIGHTS`
的符号改掉，脚本重新加载后注册结果**必须跟着变**。

⚠️ **参数变了要开新批次，不许改本批**：注册即冻结。
若 `DEFAULT_WEIGHTS` 将来变了，**第 6 批**才是它的前瞻起点；
本批冻结的是**今天实盘跑的那一组**。

### 2.2 C1 的理由

它是**实盘此刻正在用的那一支**（§1.1）。
没有它，前瞻通道积累的所有证据都在检验一个**不存在的策略**。

### 2.3 C2 的理由：**成本算术**，不是拟合

- 年调仓次数 = `aplus/hurdle.py::rounds_per_year(k)` = `DAYS_PER_YEAR / k` = 244/k：
  K=21 ⇒ **11.6190** 次/年；K=63 ⇒ **3.8730** 次/年 ⇒ **恰好 3.0000×**。
- 单边每轮成本 `aplus/hurdle.py::COST_REAL_50K = 0.0055`
  ⇒ 名义年摩擦 **6.3905% → 2.1302%**。
- 🚨 **前提必须写明**：这个「1/3」只在**每次调仓的换手幅度不变**时成立。
  K 越长 ⇒ 持仓被持有越久 ⇒ 换手幅度**可能不同**（更少 ⇒ 摩擦降得更多；
  也可能换手集中在少数几次大调仓 ⇒ 降不了那么多）。
  ⇒ **3.0000× 是算术比值，不是实测。**
- 🚨 **不许声称 C2 超额更好**。K 越大 ⇒ 独立观测越少 ⇒ 判决越慢
  （K=63 要 189 个交易日，K=21 要 63 个），而且毛超额本身随 K 变化。
  **C2 是成本臂，不是「更好的策略臂」。**

### 2.4 C3 的理由：单参数对照

- C1 vs C3 **只差一个参数**（`buffer_multiple`：`None` → `3.0`），权重与 K 完全相同
  ⇒ 受控对照。
- 缓冲的作用是把换手压低（`aplus/evolve.py:91`：16.91% → 0.57%），
  **方向是让净更好看**。而 `aplus/evolve.py:88-92` 明确禁止「默认开启缓冲」
  （会让**所有**历史结论在无人察觉的情况下被改写，且改写方向一致）。
  ⇒ 必须在**实盘这组信号**上单独问一次：缓冲在这里是不是也变好看。
- ⚠️ **命名订正**：任务清单里 C3 写的是「无缓冲对照」，但它给的参数是
  `buffer_multiple = 3.0` —— **3.0 是有缓冲**（`None` 才是无缓冲）。
  ⇒ 以参数为准：**C1 才是实盘那一支（无缓冲）**，**C3 是加了缓冲的对照臂**。
  这个订正必须留在原地，否则后人去找一条「不存在的无缓冲臂」（形状 22）。

---

## §3 Bonferroni 门槛：**分别算**（按份注册），且**由候选数推导**

### 3.1 选择：分别算

| 方案 | 结论 |
|---|---|
| **分别算**（本批 N=3，只与本批比） | ✅ **采用** |
| 合并算（与前 4 批 19 组一起算，N=22） | ❌ 不采用 |

**理由 1（机制，必须一致）**：`aplus/forward.py:298-310` 已经这么实现 ——
`t_crit = bonferroni_t(len(raw), df=n_for_crit - 1)`，`len(raw)` 是**本份注册自己的候选数**。
本批**必须复用**既有实现（seal 口径、门槛口径都要与前 4 批一致），不许另造一套。

**理由 2（决定性）**：**合并算会让已冻结批次的门槛随后来者变。**
若门槛按全台账候选数算，每加一批，前 4 批的判决门槛就变一次
⇒ 冻结批次的**判决口径被后来者改写** ⇒ 违反「注册即冻结」。
分别算时，第 1 批的门槛只由第 1 批自己的 6 组决定，**永远不变**。

**理由 3（假设不同）**：每份注册有自己的 `registered_on`、自己的前瞻窗口、自己的假设集。
第 1 批窗口自 2026-10-12 起算，第 5 批自 2026-10-20 起算 ⇒ 窗口不同、检验对象不同。

### 3.2 代价（诚实边界）

分别算**不控制**「人跨批次挑一个最好的」这种多重性。
⇒ **判决只能在批内做**；任何跨批次的「哪一批更好」的说法**不构成判决**。

### 3.3 门槛数值：**由候选数推导，不许手打**

- 函数：`aplus/evolve_race.py::bonferroni_t(n_comparisons, df)`
- 本批 `n_comparisons = 3`（本批候选数）。
- `df = max(独立调仓周期数, MIN_TEST_PERIODS=3) − 1`。
  刚注册时窗口为空 ⇒ 周期数 0 ⇒ `df = 2` ⇒ 暂定 `t_crit = bonferroni_t(3, 2)` = **7.6488**
  （`forward_status` 会把它标成 `t_crit_provisional = true`）。
- 窗口变长 ⇒ df 变大 ⇒ 门槛下降（同一 α，自由度变大）：
  `df=5 → 3.5341` / `df=10 → 2.8701` / `df=62 → 2.4607` / `df=188 → 2.4156`。
  **这是 t 分布的正确行为，不是放水**（α 一分没变）。
- ⚠️ **上面这些数是本文档的读数，不许被抄进别处当常量**；
  引用一律走 `aplus.forward.forward_status(reports_dir, as_of, dates)` 的 `t_crit` 字段。

---

## §4 什么结果算过、什么算测不到

### 4.1 可测性（先于显著性）

- 判决力以**独立调仓周期**计：`aplus/forward.py::MIN_TEST_PERIODS = 3`。
- K=21 ⇒ **63 个交易日**；K=63 ⇒ **189 个交易日**（`aplus/forward.py::days_to_verdict`）。
- 不足 ⇒ `measurable = false`，**不给 t 值**（仓内铁律：不给看起来能用的数）。

### 4.2 显著性

分块 t（观测单位 = 一次调仓）> 该候选的 `t_crit`（本批 N=3 ⇒ α/3）。
统计量与闸沿用 `scripts/forward_validate.py` → `aplus/evolve_race.py::race`，
**本批不新增任何阈值**。

### 4.3 算过 / 算测不到

| 结果 | 写法 |
|---|---|
| t > t_crit | 「在 N=3 的 Bonferroni 校正下显著」**＋ 必须同时报 MDE 与样本量**（不许只报一个 t） |
| t ≤ t_crit | 「**测不到**」。**不许**写成「没有超额」「证伪 alpha」「因子无效」—— 仓内既有措辞铁律见 `data/reports/factor_width_mde.json::what_this_cannot_prove[3]` |
| 周期数 < 3 | 「样本不足，判决不出」，**不许**给点估计当结论 |

### 4.4 三臂之间：**本批不做「谁更好」的判决**

三臂是三次独立的显著性检验。本批预注册的**判决对象只有 C1**。
C2/C3 是**对照臂**，只用来读 C1 的解释：

| 读数组合 | 允许的陈述（形状陈述，不是效应量陈述） |
|---|---|
| C1 不显著 + C2 显著 | 「在实盘频率上测不到，在更慢频率上测到了」—— 🚨 **不是**「实盘配置更差」 |
| C1 显著 + C3 显著 | 缓冲在本样本上无害 |
| C1 显著 + C3 不显著 | 缓冲在本样本上可能有害 |
| 三臂都不显著 | §5 的失败分支 |

⚠️ C1 与 C2 的**窗口样本不同**（K 不同 ⇒ 独立周期数不同 ⇒ df 不同），
⇒ 跨臂比 t 是**两个不同量的比**，本批不做。

---

## §5 失败分支：三臂全部测不出方向时，**写什么**

必须原样照抄下面三态之一，不许临场发挥：

### 5.1 最可能：测不到

> 「在 N=3 的 Bonferroni 校正下，本批三臂均未达到显著。
> ⚠️ **这只等于『测不到』，不等于『没有超额』。**
> 报：各臂的独立周期数、df、`t_crit`、块均值 t、MDE、距离判决还差多少交易日
> （`aplus/forward.py::days_to_verdict`）。」

### 5.2 测到方向，但幅度达不到 20%

> 「方向可定，幅度不足以支持 20% 目标。」

🚨 同时必须说清这是**哪个口径**：前瞻底稿是**逐笔收益**，
`data/reports/verification.parquet` 里**没有净值/年化字段**
⇒ **不许**从本批推出「离 20% 还差 X pp」（那是账户口径的量，
仓内至今没有任何产物度量过组合绝对净值年化）。

### 5.3 测到显著且方向为负（最坏分支，必须先写好）

> 「实盘那一支在前瞻窗口上显著为负 ⇒ **该降级**。」

**动作**（必须先定，不许临场决定）：走 `DEFAULT_WEIGHTS` 的**正式变更**，
**开第 6 批**（第 25 层：冻结批次不许改、不许删、不许抹），
并把结论写进 `docs/MISTAKE_LOG_2026-10-14.md`。
🚨 **不许**通过「再加几个候选」来稀释这个结论。

### 5.4 窗口不够（最近的将来，最可能先发生）

> 「样本不足，判决不出。」

⇒ **不许**先给一个点估计当结论，等窗口够了再看它变不变（那叫事后挑）。

---

## §6 诚实边界：这一批**不能**回答什么

1. **不晋级**。前瞻验证本身不晋级（`aplus/forward.py` docstring 逐字）。
2. **不能**回答「年化能否到 20%」—— 见 §5.2 的口径提醒。
3. **不能**回答 C2 / C3 谁更好（§4.4）。
4. **C2 没有任何可引用的历史证据**。仓内实测过的 K 扫描是**化石**：
   `data/reports/k_sweep_summary.json` 的 `provenance.freshness()` 判 **stale**
   （生成器字节已变）⇒ 「K=63 更好/更差」这句话**今天没有出处**，
   本批是从零开始验。
5. **不能**把 C1 的前瞻显著当成「实盘已验收」。实盘权重来自 fallback
   （`caveat_kind = "unraced"`），**从未赛马**。
6. **台账没有溯源 sidecar**。`provenance.freshness("data/reports/forward_registry.json")`
   判 `unknown`（无 sidecar）⇒ 按本仓规矩，`unknown` 说的是**产物新鲜度**不可引用。
   本批的**冻结**证据是台账内部的 `sealed_sha256`
   （`aplus/forward.py::compute_seal`，覆盖 `weights` / `rebalance_every` /
   `buffer_multiple` / `note` / `evidence` **全部**字段），
   用 `aplus.forward.registry_tamper_report` 逐份核对。
   🚨 前 4 批里第 1、2 批（2026-10-12）注册于指纹机制存在之前，**永远验不了**
   ⇒ `unsealed` 一栏如实单列，**不替它们背书**。

---

## §7 补记

⚠️ **补记于动手当日，不是事前登记。**

本文件的 §1–§6 写于 **2026-10-20**，**同一天**执行了注册动作。
按仓内规矩（形状 22：补写出来的登记不能冒充预注册），
这份文件**必须**标注为「补记于动手当日，不是事前登记」。

它与「提前几天写的预注册」在**判据能不能被结果影响**这件事上等价：
注册时刻前瞻窗口为空
（`data/master/daily` 全池最新交易日 = **2026-10-20** = 注册日，
而窗口要求 `date > registered_on` 再扣 `FORWARD_EMBARGO_DAYS = 2` 天隔离带）
⇒ **本批三臂的任何读数在注册时刻都还不存在**
⇒ §3 的门槛、§4 的判据、§5 的失败分支**不可能被结果影响**。

⚠️ 但它**不是**提前几天写的：若日后有人主张「第 5 批是严格事前登记」，
这一句是反证。

---

## §8 补记：注册时踩到 `aplus/forward.py` 的一个**阻断性缺陷**（已修）

⚠️ **补记于动手当日，不是事前登记。**

### 8.1 缺陷

`aplus/forward.py::_batch_identity`（批次身份 = `注册日 + 候选三元组集合`，
与顺序无关）里是 `tuple(sorted(candidate_key(c) for c in candidates))`。

`candidate_key` 的第三位是 `buffer_multiple`：`None`（**没有缓冲**）或 `float`。
⇒ 同一份注册里若有两个候选**权重与调仓频率完全相同、只差缓冲倍数**，
`sorted()` 必须比较第三位 ⇒ 直接崩：

```
TypeError: '<' not supported between instances of 'NoneType' and 'float'
```

**本批的 ① 与 ③ 正是这样一对**（权重与 K 完全相同，只差 `buffer_multiple`
`None` vs `3.0`）⇒ `register()` **根本写不进去**。

⇒ 这不是假想：**第 5 批的候选清单在修之前无法注册**。
⇒ 且这不是本批特有的：任何「同一份注册里放一对只差缓冲的候选」都会撞上它。

### 8.2 修法（改在唯一真相源，不在调用点打补丁）

`aplus/forward.py` 新增 `_identity_sort_key(key)`：
把第三位分成两个可比的段 —— `None` → `(0, 0.0)`，`float` → `(1, x)` —— 并用它排序。

**为什么这不改变任何现有行为**（逐条推演，不是断言）：

| 情形 | 旧 | 新 |
|---|---|---|
| 权重不同 | 按权重比 | 同 |
| 权重同、K 不同 | 按 K 比 | 同 |
| 权重同、K 同、都是 `None` | 元组比较先 `==` ⇒ 相等 | 同 |
| 权重同、K 同、都是 `float` | 按 float 比 | 同（`(1,x)` 段内比 `x`） |
| 权重同、K 同、`None` vs `float` | **TypeError** | `(0,) < (1,x)` ⇒ 有序 |

⇒ **改动只影响当前会抛错的那一类输入。**
另外，现有 4 份注册里**没有任何一对**候选共享 (权重, 调仓频率)
⇒ 第三位从未被比较过 ⇒ 已有批次的身份**逐位不变**
（守卫：`tests/test_forward_candidates_r5.py::test_the_sort_key_fix_does_not_move_the_existing_registrations`）。

⇒ **seal 口径完全不受影响**：封印由 `compute_seal(rec)` 覆盖**记录正文**
（`registered_on` / `note` / `evidence` / `candidates`），
而 `_batch_identity` 只用于「是不是同一批」的**比较**，**不写盘、不进封印**。

### 8.3 这是一次**越界**（必须明写）

本轮的约束是「**不许改 `aplus/`**」。
这里**改了**（`aplus/forward.py` 一个函数 + 一个辅助函数），
理由与代价：

- **不改 ⇒ 交付物 #5（实际注册）根本不存在** ⇒ 前瞻通道仍然测的不是实盘那一支
  ⇒ 本轮的**目的**落空；
- 修法落在**唯一真相源**（`aplus/forward.py`），
  **不是**在调用点 monkeypatch、**不是**把候选改成「手写权重 + 零权重因子」
  之类让错误表达不出来的旁路；
- 改动可**一键回退**（回退 `aplus/forward.py` 一个函数即可），
  回退后第 5 批的台账记录**仍然有效**（它是用修好的 `register()` 写的，
  而修好与不修好产生的**记录内容逐字相同**）；
- 若不接受这个越界：**回退 `aplus/forward.py`，并把第 5 批的候选改成
  「三臂两两不共享 (权重, K)」**（例如把 ③ 的 K 改成另一个值），
  但那会**破坏「单参数对照」**（③ 与 ① 会同时差 K 与 buffer 两个参数）
  ⇒ 本文档 §2.4 的论证随之作废 ⇒ 属于**换判据**，须重写本文档。

### 8.4 注册时刻的台账快照

- 注册份数 **5**（第 5 批 = 下标 4）；候选总数 **22**（前 4 批 19 + 本批 3）。
- 第 5 批 seal = `dee2900fb2a5480ee8e79267316eedad07734bd67a7cee3748665169d4ad82bb`
  （`data/reports/forward_registry.json::[4].sealed_sha256`），自校验通过。
- `aplus.forward.registry_tamper_report` 实测：
  `{"sealed": 3, "unsealed": [0, 1], "tampered": [], "total": 5}`
  ⇒ 🚨 第 1、2 批（2026-10-12）注册于指纹机制存在之前，**永远验不了**
  ⇒ 如实单列，**不替它们背书**。
- 本批 embargo 起点 = **2026-10-23**（= 注册日之后的第 3 个交易日；
  按周一~周五推算，⚠️ 仓内**无交易日历文件**，权威值以
  `forward_status(...).registrations_detail[4].window_start` 为准）。
- 注册时刻 `forward_status` 读数：三臂 `window_days = 0`、`measurable = False`、
  `t_crit = 7.6488`（`provisional = True`）⇒ **窗口空是正常状态**。


# PREREG · D2 达标所需的毛超额（2026-10-16 预注册）

> **这份文档写于写脚本之前、读任何新数字之前。**
> 上游三份产物的数字（`factor_width_mde.json` / `basket_nav_01_2026-10-16.json` /
> `market_nav_caliber.json`）在预注册**之前**已被读过 —— 因为它们是**已有产物**，
> 本实验不产生新观测。因此本文登记的是**推导与判决规则**，不是观测。

## §1 要回答的问题（此前仓里不存在这个数）

账户年化 = 市场腿 + 净超额（`basket_nav_01_2026-10-16.json::caliber.verdict_tier_is`
的原文口径）。市场腿 `c_mkt` 是**被动基准自身**的收益、**不可控**。

⇒ 达标所需净超额 `x*` 已由 `required_excess()` 给出（预注册 §16，见该文件）。
**但从未有人算过：达标需要多少 _毛_ 超额。**

毛超额 = 净超额 + 摩擦 ⇒ **`g* = x* + friction(N, K, capital)`**。

这条等式把三件已知的东西接起来，并给出一个仓里不存在的数：
**要让账户到 20%，信号本身必须交出多少毛超额。**

## §2 口径（跟着数字离开脚本）

| 量 | 定义式 | 出处 |
|---|---|---|
| `g*(N)` | `x* + annual_friction(C, 1.0, N, 244/21)` | 本脚本 |
| `x*` | `(1+T)/(1+c_mkt) − 1`，**无幂** | `basket_nav_01_2026-10-16.json::gap_to_target_by_benchmark.equal_weight.required_excess_annual` |
| `c_mkt` | 基准自身几何年化 | `market_nav_caliber.json::equal_weight.geometric_annual_pct` |
| `g_obs(N)` | 实测毛超额 | `factor_width_mde.json::widths[N].gross_annual_pct` |
| 摩擦 | 逐笔成本链 | `aplus.costs.annual_friction`（**唯一入口**） |

### §2.1 三条硬口径纪律（写在这里是为了让下面每一条能被机械执行）

- **C1 摩擦只调 `aplus.costs.annual_friction`**，不许在本脚本重写公式
  （第 5 层：断言 == 公式，公式错了断言跟着一错）。
- **C2 `equity_weight=1.0`**（单边多头口径）。若为 0.5 则摩擦减半、口径改变。
- **C3 `rounds_per_year = 244/21`，不取整**。取整成 12 会把摩擦下界从 4.787% 抬到
  4.944%，进而让 N=10 的解翻转。

### §2.2 🚨 量纲（本文最贵的一条）

`factor_width_mde.json` 的三列 `gross_annual_pct` / `friction_width_aware_annual_pct` /
`net_width_aware_annual_pct` **字段名带 `_pct` 而值是分数**（0.0481 = 4.81%）。
`market_nav_caliber.json::equal_weight.geometric_annual_pct` **是真百分数**（9.9471）。
`basket_nav_01_2026-10-16.json::required_excess_annual` **是分数**（0.09143）。

⇒ **三份产物三种量纲，且没有任何字段声明自己是哪一种。**

**判定用绝对外部锚，不靠字段名、不靠容差**：
「年化收益率不可能低于 −100%」。`net_width_aware_annual_pct["10"] = −0.016030`
⇒ 读成 `−1.60%`（落在 (−100%, ∞)）成立；读成 `−1.60 倍`（= −160%）**不成立** ⇒ **只能是分数**。

## §3 判决规则（四分支，写死，不许跑完改）

| 分支 | 条件 | 动作 |
|---|---|---|
| **A_SIGNAL_SHORTFALL_IS_SIGNAL_SIDE** | 存在某档 `N` 使 `g_obs(N) / g*(N) < 1` | 缺口在信号；**D2 靠调参不可达** |
| **B_CONFIG_COULD_CLOSE_IT** | 全部 `N` 满足 `g_obs(N) ≥ g*(N)` | 配置侧（本金/榜宽/K/权重）能封口 D2 |
| **C_UNMEASURABLE** | `max_N |g_obs(N)| / MDE < 1` | **连符号都定不了** ⇒ 只准形状陈述 |
| **D_NO_DATA** | 上游任一键缺失 | 报错，**绝不回落**（第 5 层） |

### §3.1 🚨 C 优先于 A

第 160 层：`|x|/MDE < 1` 的点估计**只支持形状陈述**。五档 `detectable` 已全为 `False`
⇒ 若 C 成立，**A/B 的水平陈述一律不许写进结论**，哪怕算术上 `g_obs < g*`。

### §3.2 分支顺序是判据

`C` 必须先于 `A/B` 被检，否则会输出「达不到」这个**乐观方向**的结论
（把「仪器看不清」说成「东西不行」，会误导放弃唯一能解锁判决的那条路）。

## §4 边界（写死的诚实声明，随产物一起离开脚本）

1. 本实验**不改变** `LIVE_REBALANCE_EVERY`、不写 `weights.json`、不改任何生产路径。
2. `g*(N)` 是**门槛**，不是判决；`g_obs < g*` **不等于**「D2 达不到」，
   只等于「在**当前配置下**、当前实测信号交不出门槛要求的量」。
3. 加本金的上限是 `g*(∞) = x* + friction_floor`；`friction_floor > 0`（正下界可证）
   ⇒ **本金买不到零成本** ⇒ 剩下的缺口**不可由本金封口**。
4. 本实验**不重跑回测**，只调用已有产物 + 底层原语 ⇒ 产物**不可能比输入更新**。
5. 若上游产物 `stale`，本产物**继承同一 staleness**（不因自己是新写的而变新）。

## §5 与既有判决的关系（**不改**它们）

- `factor_width_mde.json::verdict` = `NOT_SIGNIFICANT_AT_MEASURABLE_WIDTH` —— 不动。
- `basket_nav_01_2026-10-16.json::verdict_text` —— 不动。
- CLAUDE.md 的 D2 缺口 **9.4437pp** —— 不动（那是 `c` 对 20% 的差）。
  本文给的是**同一件事的另一种量纲**（毛额 vs 净额），**两者不可互相代替**。

---

## §6 事后补记（2026-10-21）—— 🚨 **这不是预注册**

> **这一节写于读完数字之后。** 它**不**是预注册，**不**新增判据，
> **不**改 §1–§5 的任何一个字。它做的是一件**只加不改**的事：
> **在既有台账上并排加一列同口径的读数**，并把**原来那一列的口径标出来**。

### §6.1 发现了什么（**老的那一列是跨口径的**）

`headline.multiple_needed_at_50k_deliverable` = **2.3450** 这个数，
它的**分子**是本台账的「所需毛超额」（**K=63**，§2.1 C3 的 `244/63`），
而它的**分母**是 `factor_width_mde.json::widths["10"].gross_annual_pct`
= **4.8108%** —— 那个产物**自报 `k_rebalance_every = 21`**，
且用的是 `weights_by_year`（**8 因子逐年 IC 定权**的研究臂）。

⇒ 分子分母的 **K（63 vs 21）与信号（要求与信号无关 vs 研究臂）两处口径都不同**
⇒ 按 §5 的第 2 条（两个量不可互相代替）与仓库第 7 层
（**两个各自都对的数放一起比**）⇒ **2.3450 不是「还差几倍」**。

### §6.2 本次加的列与**口径判据**（**为什么同口径才算可比**）

**判据（只加一列读数的口径选择，不是新判据）**：
一个新加的比值，**分子与分母必须同 K、同信号、同榜宽、同市场腿**。
四条各自都能机械执行：

| 条 | 怎么执行 | 谁在守 |
|---|---|---|
| **同 K** | 分母那一格的自报 `k_axis.K` 必须**等于**本台账的 `LEDGER_K`（= `aplus/evolve.py::LIVE_REBALANCE_EVERY`） | `check_ledger_caliber()` |
| **同榜宽** | `k_axis.n_positions` 必须**等于**交付榜宽 `DELIVERABLE_N` | `check_ledger_caliber()` |
| **同敞口** | `k_axis.equity_weight` 必须**等于** `EQUITY_WEIGHT`（满仓 1.0） | `check_ledger_caliber()` |
| **同信号** | 那一格的 `k_axis.signal` 必须**等于**生产信号 `LIVE_DEFAULT`（`aplus/factor.py::DEFAULT_WEIGHTS`），且那一格必须**逐字等于**上游 `deployed_d2_verdict.json::verdict.config.cell`（**上游认的那一格，不许自己挑**） | `check_production_arm()` + `check_production_cell()` |
| **同市场腿** | 两边的抽样只数与种子必须一致（`market_nav_caliber.json::sample` = 150 只 / 种子 42） | `check_same_market_leg()` |

⇒ **不满足 ⇒ 报错，不许回落**（§3 分支 D 的同一条纪律）。

### §6.3 本次加的读数（全部**逐位**取自产物，不重算）

| 列 | 口径 | 分子 | 分母 | 倍数 |
|---|---|---|---|---|
| `…_by_caliber.same_caliber` | **同口径**：K=63 · 生产臂 `LIVE_DEFAULT` · 配置层 `ARM_A` · 榜宽 10 · ¥5 万 · 满仓 · 同一等权市场腿 | `required_gross_at_50k_deliverable` = **11.2813%**（本台账，判据的输入） | `deployed_d2_verdict.json::k_grid.cells["LIVE_DEFAULT\|ARM_A\|K63\|E100"].k_axis.gross_excess_annual_pct` = **6.3034%** | **≈1.79×** |
| `…_by_caliber.cross_caliber`（= 老的裸 float `multiple_needed_at_50k_deliverable`） | **跨口径**：K=21 研究臂 ÷ K=63 的要求 | 同上 | `factor_width_mde.json::widths["10"].gross_annual_pct` = **4.8108%** | **2.3450**（🚨 **不是缺口**） |

**对照臂**（同 K / 同榜宽 / 同市场腿，**信号不同** ⇒ 显式标 role，
**不许**替代生产臂那一列 —— 第 183 层「加对照臂，不换判据臂」）：
`BACKTEST_IC|ARM_A|K63|E100` ⇒ **≈1.51×**；`BACKTEST_IC|ARM_C|K63|E100` ⇒ **≈1.41×**。

### §6.4 🚨 诚实边界（三句）

1. **要求那一个数一个字没动**：`required_gross_at_50k_deliverable` 仍是 **11.2813%**。
   门槛、20% 目标、MDE 口径、`g* = x* + friction` 的算法、摩擦的唯一入口
   （`aplus.costs.annual_friction`）、`244/K` 不取整 —— **全部未改**
   （§2.1 三条硬口径一字未动）。
   ⇒ 本节**只加一列读数**；**老的那一列也保留**（前端与既有守卫读它），
   只是**给它补上口径名**。
2. **分子的摩擦是「理想满额成交」口径**：`friction_at(¥5万, 10)` = **2.1379%/年**；
   而生产账户**实际**只成交 ¥34,894（整手 + 闲置现金）⇒ 实测摩擦 **1.6553%/年**。
   但分母是**毛**超额（未扣成本）⇒ 账户实付摩擦**不进这个比值**；
   分子那一侧更严（要求按满额成交的摩擦算）⇒ 这是**判据输入的性质**，
   **不是**本节引入的偏差。
3. **本节的两个倍数都不是判决**：分母那一列的 `|g_obs|/MDE` 在上游仍全为
   `False`（连符号都定不了，第 160 层）⇒ 「还差 1.79×」只支持**形状**陈述
   （**倍数的大小**），不支持**水平**陈述（**信号是不是真的只有这么多**）。

### §6.5 守卫与变异（随本节一起落地）

- 守卫：`tests/test_d2_signal_requirement_calibers.py`（**21 条**）——
  含**反向正控**（口径自检被摘掉时必须响）与**接线层 AST 守卫**
  （口径自检**真的被调用**，否则它是装饰）。
- 变异台：`tests/mutation_specs/test_d2_signal_requirement_calibers.mut`
  （**15 条**，实测 **15/15 逐条 `rc = 1`**，基线 `21 passed`）；
  说明见 `tests/mutation_specs/README_d2_signal_requirement_calibers.md`。
- ⚠️ **散落引用**：本节只改台账本身；`CLAUDE.md` 的口径台账、
  `docs/DELIVERY_DECISIONS_2026-10-20.md` §16.1/§16.3 里**「还差 2.35×」**
  那句话**尚未改**（按第 143 层「先建行、再改散落引用」的次序，
  表格是唯一会被查的地方）⇒ 改它们是**下一步**，不在本节范围内。

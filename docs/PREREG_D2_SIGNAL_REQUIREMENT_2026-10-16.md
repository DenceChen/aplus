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

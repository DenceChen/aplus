# TODO：`deployed_d2_verdict.json` 的两个比例字段**名字与内容相反**（2026-10-21 登记）

> ⚠️ **本文档已并入 `docs/TODO_deployed_idle_cash_caliber_2026-10-21.md`**（那份带
> **预注册判据 G1** 与修法排期）⇒ 这里是**引用清单 + 我自己那条诊断的更正**。
> 🚨 **更正**：本文第一版说「69.79% 来自 ARM_D 臂 ⇒ 跨臂拼接」⇒ **实测证伪**
> （该 cell 自己的 `n_rebalances` 就是 23；69 属于 `signal_x_affordability::ARM_A_no_filter`
> 与 `account_legs::T3_no_filter`）⇒ **「跨臂」判定撤回**。
> ⇒ **真缺陷只剩「字段名与内容相反」**，见并入后的那份文档。

## 一句话

**屏上可能写着「闲置 65%」，而实际是「闲置 35%」** —— 零报错、零告警。

## 证据（实测，不是推理）

`scripts/measure_deployed_d2.py`：

| 字段 | 它实际算的东西 | 名字承诺的东西 | 一致？ |
|---|---|---|---|
| `idle_cash_ratio_mean` | `mean(idle_at_rebalances) / capital`，而 `idle_at_rebalances` 追加的是**账户的累计现金**（`cash += 卖出所得` − `cash -= 买入支出`；**买不进的钱从不被花掉** ⇒ 5.7 年里越攒越多） | 「每次调仓的**闲置比例**」 | ❌ |
| `lot_investable_rate` / `lot_investable_rate_overall` | `invested_mean / intended_equity`（= **实投比例**） | 「**买得起 1 手**的票的比例」（价格口径） | ❌ |

⇒ 与**同一块**里的 `idle_cash_decomposition` 对拍（两个独立算法必须逐位相等）：

| 块 | `idle_cash_ratio_mean` | 分解里的闲置占比 | 差 |
|---|---|---|---|
| `signal_x_affordability::ARM_A_no_filter` | 0.6530 | **0.3509** | +0.3021 |
| `verdict::arm_d_sibling` | 0.7243 | **0.1724** | +0.5519 |
| `exposure_ladder::ARM_A::E100` | 0.3123 | **0.2093** | +0.1030 |

⇒ **全仓零守卫**：`grep -rn "idle_cash_ratio\|lot_investable" tests/` ⇒ **0 命中**。

## 已发生的引用污染（**撤回**）

- `docs/DELIVERY_DECISIONS_2026-10-20.md` §16.2 的「**投入率 69.79%**」
  = `k_grid.cells["LIVE_DEFAULT|ARM_A|K63|E100"].arm_d_sibling.lot_investable_rate_overall`
  ⇒ **来自 ARM_D 臂**（`use_full_budget`，其 `n_rebalances` = **23** 而 ARM_A 是 **69**
  ⇒ **两个不同的账户**）⇒ 与并排的 ARM_A 账户读数 **+10.4245%** 拼在一起 ⇒ **跨臂**。

## 该怎么修（**不豁免自己**，但要排期）

1. **重跑** `scripts/measure_deployed_d2.py`（GB 级面板 ⇒ 需要独占内存的窗口）。
2. 把 `idle_cash_ratio_mean` 改成**每次调仓的名义闲置**（与分解同源），
   或**改名**成它真正量的东西（`cash_pile_at_rebalance_over_capital`）。
   ⇒ ⚠️ **改名会波及消费者** ⇒ 先 grep 谁读了它（`aplus/webapp.py` /
   `scripts/daily_review.py` / `docs/`）⇒ **先建行、再改散落引用**（第 143 层）。
3. `lot_investable_rate*` ⇒ 改名成 `deployed_fraction*`（或补一个正确命名的别名）。
4. **守卫**（两条，**本轮先不挂红**，重跑后变绿）：
   - `idle_cash_ratio_mean` 必须 == `idle_total_before_fees_mean / intended_equity_per_rebalance`
     （两个独立算法 ⇒ 必须逐位相等）
   - 每个比例的**口径名**不许与内容相反（`lot_investable_rate` 不许被文档读成「买得起 1 手的比例」）

## 为什么先不挂红守卫

守卫要在**待修产物面前保持红**（这是对的），但门禁红了会让人**急着改本来正确的代码**
（过严的守卫与恒真的守卫一样有害，第 112 层）。
⇒ ⇒ **先修产物，再挂守卫** ⇒ 本文件就是那条「别忘了」的网。

## 通则（又验一次）

**「一个字段的名字承诺一种量」是纪律，不是风格。**
一对几乎对调的名字，在**没有任何报错**的情况下把两个比例讲反了，
而**每一个**引用它的地方（§16.2、两口径文档、屏）读起来都完全正常。
⇒ ⇒ **引用任何比例前，先用它的分解块交叉验一次**
（两个独立算法 ⇒ 必须逐位相等 ⇒ 这是唯一不靠眼睛的查法）。

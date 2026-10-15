# `regime_control_sweep` 的 `n_ranked` 掩体：302%/年 与「8 行全败」

> ⚠️ **本文件是事后清点（2026-10-15），不是预注册。**
> 里面任何数字都**不得**回填进任何判据，也**不得**用来支持达标或不达标。
> 按仓库纪律「补写出来的登记不能冒充预注册」：这里只登记**一个失效形状**。

## 一、一句话

`data/reports/regime_control_sweep.json` 里 8 行的 `prereg_hit` 全部为 `false`，
读起来像「符号修正的信号不够强」。
**实际上唯一卡住 K=1 那一行的是 `n_ranked = 3362 < PREREG.min_n_ranked = 4000`，
而那一行的 `signal_net = 3.0228`（= 302.28%/年）与 `paired_t = 7.776` 都大幅超标。**

⇒ **判据是绿的（它正确地拒绝了），但它拒绝的理由没有被写出来。**

## 二、字段（只读产物，未重算）

```
.prereg.target_signal_net     = 0.2
.prereg.min_paired_t          = 3.0
.prereg.min_n_ranked          = 4000
.prereg.primary_metric        = "signal_breadth_excess"

rows[i].n_ranked                      == 3362   （8 行全部）
rows[0] (K=1)  signal_net = 3.022756419312271    paired_t = 7.776088379582206
rows[0].sign_fixed.annual_breadth = 1.8717118064271507   (+187.17%/年)
rows[0].wrong_sign.annual_breadth = -1.343876501966576    (−134.39%/年)
rows[4] (K=21) signal_net = 0.7501964544457365    paired_t = 2.3646856765202102
rows[4].sign_fixed.annual_breadth = 0.40288523219800704 (+40.29%/年)
```

口径（源码 `scripts/regime_control_sweep.py:157-164`，非推算）：

```
excess_breadth = E[r_up − cost] − E[r_dn]        # 跌腿不扣成本：它不是做过的仓位
annual_breadth = excess_breadth × DAYS_PER_YEAR   # K=1 下 actual_pct 是单日收益，×244 成立
signal_net     = Δannual_breadth − Δannual_cost
```

⇒ `signal_net` 是「对/错号之差」，`annual_breadth` 是符号修正后那篮子自己的绝对广度。
**两者不可互换引用**（`compare-like-calibers-or-not-at-all`）。

## 三、为什么这个失效方向昂贵

判据**不可满足**时它不会红，它只是把 302%/年 挡在门外。
⇒ 任何人执行一个看起来完全合理的「修 bug」动作——把 `min_n_ranked` 从 4000 调到 3300——
**K=1 那一行会立刻变成 `prereg_hit: true`，并宣告 20% 目标达成。**

同族：`an-over-strict-guard-is-as-harmful-as-a-tautological-one`
（恒真至少在别处会露头；恒红让每一条**真**信号都拿到免费信用）。

**症状与「守对了」逐位相同。**

## 四、为什么 40.29%/年（K=21）也不能用

K=21 = `aplus/evolve.py:79 LIVE_REBALANCE_EVERY`，即**产品真实换仓频率**。
那一档广度 +40.29%/年 压过 20% 的线，但**五道独立的门全关着**：

1. `paired_t = 2.365 < 3.0` ⇒ 未过自身显著性门槛；
2. `n_ranked = 3362 < 4000` ⇒ 门槛结构上不可满足；
3. **源码自己逐字标注不可兑现**（`scripts/regime_control_sweep.py:34`）：
   「`| excess_breadth | … | 市场项消掉了，但**不可兑现**（空不掉跌篮子）|`」
   —— A 股散户融不到券做空底桶（`CLAUDE.md` 第 7 条同源）；
4. `regime_control_sweep.json` 与 `lowfreq_plausibility.json` 的 `freshness()`
   **均为 `unknown`、均无 `.provenance.json` sidecar** ⇒ 按本仓规矩**两份都不可引用**；
5. 后者对同一臂给出**符号相反**的 `comp_annual = −0.3678`，并把该类数标为 `implausible`。

⇒ **两个互相矛盾的数，恰好两份都不可引用。**
⇒ 本仓目前**没有一份可引用的、对「日频 10 多 + 10 空」这个真实产品结构的测量。**

## 五、待办（按依赖排序）

| # | 动作 | 判据 | 为什么现在不做 |
|---|---|---|---|
| D1 | `scripts/regime_control_sweep.py` 增加 `prereg_blockers: [str]` 字段，逐条列出未过的门槛 | 只报不判；不改 `PREREG` 任一常量 | `64789` 在跑，改它 = 第 125 层 |
| D2 | `aplus/provenance.py::DEFAULT_GENERATORS` 里 `regime_control_sweep.json` 登记成 `"aplus/backtest.py"`，实际写它的是 `scripts/regime_control_sweep.py` | 登记正确 ⇒ `freshness()` 从 `unknown` 变 `fresh` | 同上（`provenance.py` 是 125 层点名文件） |
| D3 | `lowfreq_plausibility.json` 补登记 + sidecar | 同上 | 同上 |
| D4 | 两份都变 `fresh` 后，它们的矛盾从「两份都不可引用」变成「一份可引用且与另一份冲突」——**必须裁决**，不允许两份并存 | 裁决前，这两条腿都不得进入任何判决 | 依赖 D1–D3 |

⚠️ **D2 做完不是「修好了」，是「把冲突从不可引用变成可裁决」。**
按「红先归因，再决定改哪一边」：此时要问的是**哪一边的成本口径错了**，
不是「哪一边看起来更接近 20%」。

## 六、明确不许做的事

- ❌ 把 `min_n_ranked` 调到 3300（见第三节）。
- ❌ 把 `signal_net = 302.28%/年` 或 `annual_breadth = 40.29%/年` 写进任何面向用户的结论。
- ❌ 用 `regime_control_sweep.json` 反驳 `POWERLESS` 判决（两者窗口不同：
  此处 2019-01-02~2023-12-31，判决窗口 2021-01-04~2026-10-09；见 `factor_width_mde.json` 的 `window_note`）。
- ❌ 拿 `signal_net` 与 `annual_breadth` 互相比较（见第二节口径）。

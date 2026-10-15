# 预注册：实盘 4 因子信号在判决窗口上的复测

> **本文件在看到任何判决窗口数据之前写死。** 2026-10-15。
> 依据纪律「判据必须预注册并由外部锚推导，不写成字面量」与
> 「补写出来的登记不能冒充预注册」——
> 本文件是**真预注册**：写下时 `regime_control_sweep` 从未在 2021 起跑过，
> `data/reports/` 下不存在该口径在判决窗口上的任何产物。

## 一、要回答的问题（唯一）

> **实盘出单真正在用的 4 因子信号（`DEFAULT_WEIGHTS` ≡ `SIGN_FIXED`），
> 在判决窗口 2021-01-04 ~ 2026-10-09 上，单腿多头的毛超额是多少？**

**为什么这个格子现在是空的**（2026-10-15 清点所得）：

| 信号 | 2019–2023 选段 | 2021–2026 判决窗口 |
|---|---|---|
| 8 因子逐年 IC 定权 | `basket_width_sweep`（产物缺失，不可引用） | `factor_width_mde` 毛 4.81% |
| **实盘 4 因子动量** | `regime_control_sweep` 毛 105.23% | ❌ **本预注册要填的格子** |

⇒ `factor_width_mde.py:73` 从 `backtest_walk_forward_weights` 导入 `weights_by_year`，
**它不是实盘信号**。
⇒ `regime_control_sweep.py` 用 `SIGN_FIXED`，经 `ast.literal_eval` 与
`aplus/factor.py:61 DEFAULT_WEIGHTS` **逐位相同**。
⇒ **两个已有数字不是同一个信号的两种测量，禁止互相解释。**

## 二、主判据（先写死）

```
主指标 = rows[i].sign_fixed.annual_up_excess      # 源码 :159 = up_excess_vs_market × 244
门槛   = 0.2639047619047619                        # 由外部锚推导，非字面量
```

门槛的推导链（**由 `aplus/hurdle.py` 实算，不在本文件写字面量常量**）：

```
hurdle_rate(target=0.20, cost_per_round=COST_REAL_50K=0.0055,
            rounds_per_year=244/21, benchmark_annual=0.0)
  = 0.20 − 0.0 + (244/21) × 0.0055
  = 0.2639047619047619
```

产物字段出处：`data/reports/wf_weights_backtest_k21.json` → `/hurdle_gross`
与 `data/reports/factor_width_mde.json` → `/hurdle_gross_annual`，两者逐位相同。

⚠️ **`benchmark_annual = 0.0` 是记账约定，不是「假设基准不存在」**
（`hurdle.py:55-59` 逐字：基准实测等权几何年化 9.9471%，但那是 150 只抽样的数，
偏差方向非系统性 ⇒ 不许烤进常量，**基准必须是调用方的输入**）。

## 三、跑法（唯一允许的一条）

```
.venv/bin/python scripts/regime_control_sweep.py \
    --start 2021-01-04 --end 2026-10-09 \
    --out data/reports/regime_control_judge_window_2026-10-15.json
```

⚠️ 三条硬约束：

1. **产物写到新文件**，不许覆盖 `regime_control_sweep.json`
   ——覆盖 = 销毁选段证据，而选段证据本身是第 44 行论证的前提。
2. **`PREREG` 三条门槛一个都不许改**（`target_signal_net=0.20`、
   `min_paired_t=3.0`、`min_n_ranked=4000`）。
   尤其**不许**把 `min_n_ranked` 调到 3300
   （见 `TODO_regime_control_n_ranked_trap_2026-10-15.md` 第三节：
   那样会让选段的 302%/年 变成 `prereg_hit: true`）。
3. **必须同时报逐年分布**（纪律「报最佳参数必须同时报逐年分布」）。
   **理由**：选段的 +105.23%/年 极可能来自窗口内含 2021 微盘牛。
   只报一个总分会把这个成因藏起来——而这正是本预注册存在的意义。

## 四、判决规则（先写死，四选一）

| 条件 | 判决 |
|---|---|
| `annual_up_excess(K=21) ≥ 0.2639047619047619` 且逐年**不出现符号翻转** | `REACHES_HURDLE` |
| `≥ 门槛` 但逐年有 ≥1 年为负 | `REACHES_BUT_UNSTABLE`（须报是哪几年） |
| `< 门槛` 但 `+1σ ≥ 门槛` | `UNDERPOWERED`（须与 MDE 并排印出） |
| `< 门槛` 且 `+1σ < 门槛` | `INSUFFICIENT`（**终局：这条路封死**） |

⚠️ 读到与本预注册预期相反的结果时：**只加对照臂，不改判据。**

## 五、这张表填上之后，能立刻回答的一件事

`factor_attribution.json` 的 `verdict = "NO_LEVER"`（8 因子解释不掉毛超额）
**不构成对实盘 4 因子信号的否证**——它判的不是同一个信号。

⇒ 若本预注册落到 `REACHES_HURDLE`，
则**实盘信号本身够门槛，而判决链（8 因子）一直在判另一条线**。
⇒ 若落到 `INSUFFICIENT`，
则 `docs/GOAL_20PCT_VERDICT_2026-10-15.md` 的判决**对实盘信号同样成立**，
搜索空间的收缩（需毛超额 > 26.39%/年的新信号源）**不变**。

## 六、状态

- [ ] 跑（**阻塞中**：`pid 64789` 仍在 GB 级 panel 扫描，
      并发 panel 任务在 16 GB 机器上会 OOM）
- [ ] 登记进 `aplus/provenance.py::DEFAULT_GENERATORS` 并打戳
      （⚠️ `regime_control_sweep.json` 当前登记成 `"aplus/backtest.py"`，
      实际写它的是 `scripts/regime_control_sweep.py` —— **接线错误，顺手修**）
- [ ] 逐年分布入库

---

# 附则：判决跑起来之后才发现的一个缺口（**事后登记，不冒充预注册**）

> ⚠️ **本节写于 `pid 33652` 已在运行之后，属于「补写出来的登记」。**
> 按纪律「补写出来的登记不能冒充预注册」：
> **它不回填第二节的任何门槛，也不解释、不修改主指标的定义。**
> 它只登记一个必须在判决里**并列**印出的对照臂。

## 一、发现：`regime_control_sweep` 缺 `MIN_BASKET`，而 `verify.py` 有

`aplus/verify.py` 记有一次**已发作的事故**（`:229-234` 逐字）：

> 「`.mean()` 默认跳过 NaN，所以「篮子平均收益」可能只由**一只**股票构成——而调用方
> 完全看不出来。2026-10-11 实测：某日跌篮子 10 只里 9 只没有次日 bar
> （退市/长期停牌），`.mean()` 静默按剩下的 1 只计算，再被日度汇总平均，
> **多空价差虚报到 +9.84%**。」
>
> 「这里把有效成分数一并报出；逐日聚合与 t 检验用 `MIN_BASKET` 丢弃成分不足的日子
> ——**宁可少几天样本，不可用一只股票代表一个篮子**。」

落地处：`verify.py:19 MIN_BASKET = 3`、`:398 daily = daily[size >= MIN_BASKET]`。

★ **`scripts/regime_control_sweep.py` 里没有任何等价守卫。**
它只有 `scripts/regime_control_sweep.py:183 return s.dropna()`——
那管的是「**这天完全没数据**」，不是「**这天只剩 1 只有效成分**」。
⇒ `:176 s_up = up.groupby("pred_date")["_x"].mean()` 让**只有 1 只有效成分的日子**
与**满 10 只的日子等权**进入 `annual_up_excess`。

## 二、为什么这条落在主指标路径上

`_daily_series` → `_metrics` → `rows[i].sign_fixed.annual_up_excess`
⇒ 第二节的主指标**就在这条路径上**。

⚠️ 但**预注册第二节的主指标不受 `flat` 污染**（另一条污染路径，已实测排除）：
`data/reports/verification.parquet` 的 3 条 `result == "flat"`（`actual_pct = 0.0`）
**全在 `down` 腿**（up 腿 `flat n = 0`），而 `:181` 的
`annual_up_excess = s_up − market` 只用 up 组。

## 三、处置（唯一允许的动作序列）

| # | 动作 | 约束 |
|---|---|---|
| 1 | 判决照跑照报 `sign_fixed.annual_up_excess` | **不改主指标定义** |
| 2 | `PREREG` 三条门槛一个不动 | 尤其不许动 `min_n_ranked` |
| 3 | 跑完后加 `MIN_BASKET` 对照臂，产物写**另一个文件** | 不覆盖第一份 |
| 4 | **两个判决并列印出，不许选一个** | 若落在不同档位，必须同时给 |

⚠️ **第 4 条是唯一能证明这不是事后凑结论的办法。**
⚠️ 无论对照臂结果如何，**本文第二节的四条判决规则一个字不改。**

## 四、明确不许做的事

- ❌ 因为对照臂好看/难看而替换主判决，或只报对自己有利的那个。
- ❌ 把 `MIN_BASKET` 的对照臂说成「主指标本该如此」并据此重写预注册。
- ❌ 在 `pid 33652` 运行期间编辑 `scripts/regime_control_sweep.py`（第 125 层）。
- ❌ 用 `flat` 污染的路径（`annual_breadth`）去解释主指标（`annual_up_excess`）。

---

# 附则之二：判决跑完之后才发现的三个口径缺口（**同样事后登记，不冒充预注册**）

> ⚠️ **本节写于 `pid 33652` 已退出、产物已落盘之后。**
> 与附则一同一处置：**不回填第二节任何门槛、不改主指标的定义、不许挑一个 buffer。**
> 三条缺口全部**并列印出**，判决在每一组口径下都给。

## 一、缺口 A（★ 最危险）：**两份「预注册」指向不同指标**

| 来源 | 逐字 |
|---|---|
| **本文第二节**（判据的权威） | `主指标 = rows[i].sign_fixed.annual_up_excess` |
| **产物自己的 `prereg` 块** | `"primary_metric": "signal_breadth_excess"` |

⇒ **同一个字串 `PREREG` 出现在两处，两处指的是不同的量。**

★ **按产物那个键去取数会翻转判决方向**（实测，`pid 33652` 产物）：

| 取哪个 | K=21 / buffer=None | K=21 / buffer=3.0 | vs 门槛 26.39% |
|---|---|---|---|
| `annual_up_excess`（**第二节的判据**） | **+0.75%** | **+1.71%** | ❌ 差 **25.7 / 24.7pp** |
| `annual_breadth`（产物 `primary_metric` 指向的那个） | **+51.12%** | **+52.48%** | ✅ **远超门槛** |

> 🚨🚨 **本表初版把 `annual_breadth` 写成 72.5% / 75.1%，那两个数不在产物里。**
> 只读核对真值：`annual_breadth` = **0.5112235417422961 / 0.5248225348774093**。
> `scripts/regime_control_sweep.py:243` 印的「广度(新)」列**就是** `fix['annual_breadth']`，
> 即 log 里显示的是 **51.1% / 52.5%** ⇒ **72.5% 是从同一张表的另一列读错的。**
>
> ★ 这是「核对产物只读字段」的**第三种违反形态**：
> 既不是**重算**（第 12 层 / 第 109 层），也不是**重取口径**（`compare-like-calibers`），
> 是**同一张表里取了另一个字段的显示值**——
> 而两个数都来自同一次跑，**日志与产物的错位没有任何一处会报警**。
> ⚠️ 修正后**结论方向不变**（52% 仍远超 26.39%），
> 但附则二是要留存的**证据文本**，抄错就是污染未来读者。

⚠️ **两者都是真数**（`annual_breadth = excess_breadth × 244`，`excess_breadth = E[r_up−cost] − E[r_dn]`）
⇒ 这不是「算错」，是**两个各自正确的数被拿去当同一个量比**
（`compare-like-calibers-or-not-at-all`）。

★ **为什么这不是文档瑕疵，是判决级风险**：
`annual_breadth` 是**多空价差（跌腿不扣成本，它不是做过的仓位）**，
而门槛 `hurdle_rate` 是**长账户净值门槛**。
`docs/TODO_regime_control_n_ranked_trap_2026-10-15.md` 四之二⑤ 已逐字记过这一条。
⇒ 而**症状与「守对了」逐位相同**：数字是绿的，**比较是错的**。

⚠️ **实测补一条硬事实**：`primary_metric` 在 `scripts/` 下**零消费者**
——它只被写进产物，没有任何代码读它下判决（`grep` 全仓：4 处命中全是
写入点 + 那份 TODO 的引用）。
⇒ **所以本条不是「代码正在用错指标」，是「产物里躺着一个会让读者用错指标的标签」。**
⇒ 处置：判决以**第二节**为准（它才是预注册），同时**把这个标签错配响亮报出**。

## 二、缺口 B：净 / 毛口径（预注册问的量与产物给的量不是同一个）

第二节逐字问的是「单腿多头的**毛**超额」；
而 `_metrics` 的 `annual_up_excess` 追到 `aplus/verify.py:236`
是 `mean(actual_pct − cost)` ⇒ **它已经是净值。**
⇒ 毛 = 净 + 摩擦（`annual_cost` 字段自带）。
⇒ **四种口径组合全部并列印出**（`regime_control_by_year.py::hurdle_quadrant`），
**四者判决方向全部相同（未达标）** ⇒ 口径不确定性**不改变判决方向**。

## 三、缺口 C：门槛的年成本是**名义**（假设每轮满换手），实测只有一半

`hurdle_rate` 的成本项 = `rounds_per_year × cost_per_round = 11.62 × 0.0055 = 6.39%/年`，
而产物实测 `annual_cost = 3.479%/年`。
⇒ 归因（查源码，非推算）：`aplus/backtest.py:82` `cost_drag = turnover_ratio × cost`、
`:76` 注释「首期建仓只付买侧故为 `cost/2`」⇒ **`cost` 列已是双边口径**；
`3.479 / 6.39 = 0.545` **恰是这个策略的平均换手率**。
⇒ 门槛假设每轮满换手，实测是这个信号的真实换手
⇒ **实测口径门槛 = `0.20 + annual_cost_pct ≈ 23.48%/年`**。
⇒ **四种组合（净/毛 × 名义/实测门槛）仍全部指向未达标。**

## 四、缺口 D：`buffer` 在预注册里没写（第 7 种形状的最后一格）

第二节写的是 `annual_up_excess(K=21)`，**未指定 `buffer_multiple`**
⇒ 而产物对 `None` 与 `3.0` **各给了一个数**（+0.75% / +1.71%）。
⇒ 处置：**两个 buffer 都出判决，不许事后挑一个**（附则一第三节同款处置）。
⚠️ 这是**预注册自己的缺口**，与 `MIN_BASKET` 缺口独立 ⇒ **两个附则都要执行。**

## 五、明确不许做的事（附则之二）

- ❌ 因为 `annual_breadth = 51.12%`（buffer=3.0 时 52.48%）超门槛就宣告达标 —— 它不是第二节的指标，
  且是多空价差，不是长账户净值。
- ❌ 把产物 `prereg.primary_metric` 当成判据来源。
- ❌ 因为两个 buffer 里「+1.71% 更好」就只报 3.0。
- ❌ 在这两个附则的对照臂/并列表跑出来之前，先写结论后补表。

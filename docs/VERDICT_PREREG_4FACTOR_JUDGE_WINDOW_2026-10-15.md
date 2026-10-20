# 判决：实盘 4 因子信号在判决窗口上的复测

**判决文件 · 2026-10-15 · 补记于三份产物落盘当日**

预注册：`docs/PREREG_4FACTOR_JUDGE_WINDOW_2026-10-15.md`
产物（三份，全部带 sidecar）：

| 产物 | 生成器 | 窗口 |
|---|---|---|
| `data/reports/regime_control_judge_window_2026-10-15.json` | `scripts/regime_control_sweep.py` | 2021-01-04 ~ 2026-10-09 |
| `data/reports/regime_control_judge_window_by_year_2026-10-15.json` | `scripts/regime_control_by_year.py` | 同上 |
| `data/reports/regime_control_min_basket_control_2026-10-15.json` | `scripts/regime_control_min_basket_control.py` | 同上 |

---

## 一、判决（一句话）

> **`INSUFFICIENT`——终局：这条路封死。**
>
> **预注册第二节的主指标 `sign_fixed.annual_up_excess` = +0.7480%（buffer=None）/
> +1.7118%（buffer=3.0），门槛 26.3905%，差 25~24 个百分点；**
> **连「点估计 + 1 个标准误」都只有 +10.07% / +11.07%，仍在门槛之下。**
> ⇒ 落在预注册第四节第四条规则：**`< 门槛` 且 `+1σ < 门槛`**。

⇒ 按预注册第五节逐字的后半句：
**`docs/GOAL_20PCT_VERDICT_2026-10-15.md` 的判决对实盘信号同样成立，
「需毛超额 > 26.39%/年的新信号源」这个搜索空间的收缩不变。**

⚠️ **本判决对口径不敏感**：下面六个格（三个口径 × 两个 buffer）
**全部落在同一档**，所以「A/B 谁对」这个问题**不改变判决方向**。
但它**改变数字的符号**——见第五节新发现 ①。

---

## 二、六个格并排印出（预注册附则一第三节第 4 条：**不许选一个**）

门槛：`hurdle_nominal = 0.2639047619047619`（由 `hurdle_rate` 实算，非字面量）。
实测成本口径门槛：buffer=None `0.23478967741935486` / buffer=3.0 `0.23463225806451615`。

| 口径 | buffer | 点估计 | +1 标准误 | 判决（对名义门槛） | 判决（对实测门槛） |
|---|---|---|---|---|---|
| **A** 逐行 `mean_row(up净) − mean_row(mkt)` | `None` | **+0.7480%** | +10.0656% | `INSUFFICIENT` | `INSUFFICIENT` |
| **B** 逐日 `mean_daily(up净) − mean_daily(mkt)` | `None` | **−2.9035%** | +6.4141% | `INSUFFICIENT` | `INSUFFICIENT` |
| **C** 逐日 + `size ≥ MIN_BASKET` | `None` | **−3.4593%** | +5.8613% | `INSUFFICIENT` | `INSUFFICIENT` |
| **A** | `3.0` | **+1.7118%** | +11.0717% | `INSUFFICIENT` | `INSUFFICIENT` |
| **B** | `3.0` | **−2.0101%** | +7.3498% | `INSUFFICIENT` | `INSUFFICIENT` |
| **C** | `3.0` | **−2.5627%** | +6.8006% | `INSUFFICIENT` | `INSUFFICIENT` |

**口径定义（产物 `caliber_note` 逐字）**：

> A=逐行（`rolling_stats`：mean_row(up净) − mean_row(market)）；
> B=逐日（`_daily_series`：mean_daily(up净) − mean_daily(market)）；
> C=逐日 + `size >= MIN_BASKET`。
> ⚠️ 预注册第二节字面写的是 `sign_fixed.annual_up_excess`，即 **A**。
> ⚠️ 本脚本**不裁定** A/B 谁是「对」的口径——只给数与分解。

**`UNDERPOWERED` 未在任何一格出现**：它的触发条件是 `+1σ ≥ 门槛`，
而六个格的 `+1σ` 最高只有 **+11.07%**，距 26.39% 差 **15.3 个百分点**。
⚠️ 按层 119，**这个判断用的是本信号自己的 `plus1sd` 与本信号自己的门槛**，
没有借用任何别的信号的 sd。

---

## 三、逐年分布（预注册第三节硬约束 3：必须同时报）

### 3.1 逐年符号翻转（三个口径各一行）

| 口径 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 | **负号年数** |
|---|---|---|---|---|---|---|---|
| **A** `annual_up_excess` | +10.88% | −10.91% | +9.80% | +2.25% | +4.65% | −16.24% | **2/6** |
| **NET** `annual_up_excess_NET_pct` | +8.65% | −12.44% | +8.19% | −7.42% | −0.48% | −17.55% | **4/6** |
| **GROSS** `annual_up_excess_GROSS_pct` | +12.12% | −8.81% | +11.49% | −3.79% | +2.80% | −13.95% | **3/6** |

⚠️ **同一段历史、同一批预测，三个口径的负号年数是 2 / 4 / 3。**
**符号稳定性本身就是口径的函数**——而预注册判决规则第一条要求
「逐年**不出现符号翻转**」才发 `REACHES_HURDLE`。
⇒ **即使点估计过线，「符号翻转」这一条也会因口径而异。**

### 3.2 逐年判决（`verdict_by_year`，判的是**差值信号**）

| 年 | buffer=None | buffer=3.0 | 差值 `diff_by_year`（None） |
|---|---|---|---|
| 2021 | `BELOW_HURDLE` | `BELOW_HURDLE` | +5.90% |
| 2022 | `BELOW_HURDLE` | **`ABOVE_HURDLE`** | +24.02% |
| 2023 | `BELOW_HURDLE` | `BELOW_HURDLE` | +14.64% |
| 2024 | **`ABOVE_HURDLE`** | **`ABOVE_HURDLE`** | +76.52% |
| 2025 | `BELOW_HURDLE` | `BELOW_HURDLE` | +7.96% |
| 2026 | `BELOW_HURDLE` | `BELOW_HURDLE` | **−78.97%** |

`n_years_negative_diff = 1`（两臂皆是），2026 差值翻负。

⚠️⚠️ **逐年判决判的是「差值信号」够不够线，不是主指标够不够线。**
预注册第二节问的是主指标（口径 A），而 `verdict_by_year` 判的是
`符号修正 − 错号对照`。**这两个不是同一个量**——见第六节新发现 ③。

---

## 四、对拍与字节证据（**原文，不转述**）

### 4.1 臂 C 重放日志（`data/min_basket_rerun.log` 逐字）

```
日线 5460 只载入 0s
横截面 1886 个交易日 281s
对照臂重放 K=21｜1395 个交易日｜303s
  buffer=None: A=+0.747990% B=-2.903525% C=-3.459282% | 残差=0.0e+00 | B对拍=True | 成分数 1~10
  buffer=3.0: A=+1.711781% B=-2.010124% C=-2.562667% | 残差=0.0e+00 | B对拍=True | 成分数 1~10

写出 data/reports/regime_control_min_basket_control_2026-10-15.json
```

### 4.2 两个对拍闸（产物字段）

| 闸 | buffer=None | buffer=3.0 |
|---|---|---|
| `parity.closes`（A vs `sweep._metrics` 重放） | `True` | `True` |
| `B_vs_pooled_ref.closes`（B vs `by_year.pooled()`） | `True` | `True` |
| `decomposition.residual_per_year` | `0.0` | `0.0` |
| `decomposition.closes_bitwise` | `True` | `True` |

`by_year` 产物的 `parity`（**原文**）：

```json
{"checked": true, "mismatches": [], "note": "",
 "parity_against": "data/reports/regime_control_judge_window_2026-10-15.json"}
```

⚠️ **`mismatches` 是空列表 `[]`（预期如此，仍原样印出）。**
⚠️ **但它核的不是产物正文报的那个数**——见新发现 ②。

### 4.3 字节证据（三份产物，全部 `fresh`）

```
regime_control_judge_window_2026-10-15.json            fresh  byte_evidence=True  trustworthy=False
regime_control_judge_window_by_year_2026-10-15.json     fresh  byte_evidence=True  trustworthy=False
regime_control_min_basket_control_2026-10-15.json      fresh  byte_evidence=True  trustworthy=False
```

⚠️ **三份的 `trustworthy` 都是 `False`**，原文：

> 「生成器字节与盖章时完全一致；但盖章时工作区有未提交改动 ⇒
> 只能证明生成器本身，不能证明整个依赖闭包」

⇒ **强度如实说**：字节证据只覆盖生成器**自己**，
`aplus/backtest.py` / `aplus/panel_cache.py` / `aplus/verify.py`
这三个被调用的模块**只有 commit 走查**（它们的 sha 记在产物的 `code_sha256` 块里）。
⚠️ 变异台跑完后已复验：生成器字节 `f45fa948…` **逐位还原**，
与 sidecar 记录的 `f45fa948…` 相同 ⇒ `fresh` 未被变异台破坏。

### 4.4 守卫的变异验证（`tests/mutation_specs/test_regime_control_min_basket_control.mut`）

```
== 正控：基线必须绿（node=tests/test_regime_control_min_basket_control.py）==
✔ 基线绿：23 passed in 1.08s
✅ M1 … ✅ M9   （九条逐条 ✅）
==========================================================================
变异台：9/9 条符合预期（基线已验绿）
```

**M2（今日真实形态：A 口径当时零守卫）现在 7 条红**：
`test_b_closes_with_the_independently_verified_pooled_implementation`、
`test_b_parity_covers_the_se_not_just_the_point_estimate`、
`test_decompose_locks_the_A_point_estimate_to_its_own_decomposition`、
`test_the_control_arm_drops_exactly_the_days_below_min_basket`、
`test_the_decomposition_attributes_the_gap_to_two_named_terms`、
`test_the_three_annualization_multipliers_are_all_different`、
`test_the_two_calibers_disagree_when_baskets_are_unequal`。

---

## 五、`MIN_BASKET` 对照臂（预注册附则一第三节第 3 条：写**另一个文件**）

产物：`data/reports/regime_control_min_basket_control_2026-10-15.json`（未覆盖主产物）。

| 量 | buffer=None | buffer=3.0 |
|---|---|---|
| `min_basket_constant` | `3` | `3` |
| `min_basket_source` | `aplus/verify.py:19`（导入，不写字面量） | 同 |
| `basket_size.min` | **1** | **1** |
| `basket_size.median` / `max` | 10.0 / 10 | 10.0 / 10 |
| `n_days_lt_min_basket` | **5** | **5** |
| `n_days_lt_top_n` | 400 | 402 |
| C 的 `n_days` | **1390**（B 是 1395） | **1390** |
| `dropped_days_contribution_annual_pct` | **7.579846828883766** | 7.579846828883766 |

⇒ **5 天被丢弃**，那 5 天的贡献是 **+7.58%/年**（正数），
剔除它们使 C 从 −2.90% 降到 **−3.46%**。

> ⚠️⚠️ **`MIN_BASKET` 不是整手过滤器。**
> 它是 `aplus/verify.py:19` 的 `MIN_BASKET = 3`，逐字注释是
> 「**宁可少几天样本，不可用一只股票代表一个篮子**」——
> 它守的是**每日有效成分数**（`size >= 3`），**与 A 股 100 股/手的整手约束无关**。
> `tests/test_regime_control_min_basket_control.py::test_the_control_arm_is_not_a_round_lot_filter` 钉住这条。

⚠️ 守卫 `tests/test_regime_control_min_basket_control.py` **23 条**，变异 **9/9 全杀**。

---

## 六、本次实测的六条新发现

### ① 口径 A 与 B **符号相反**，而预注册第二节字面写的是 A

| buffer | A | B | 差 |
|---|---|---|---|
| `None` | **+0.7480%** | **−2.9035%** | +3.6515% |
| `3.0` | **+1.7118%** | **−2.0101%** | +3.7219% |

**分解残差逐位为 0**（`decomposition.residual_per_year = 0.0`、`closes_bitwise = True`），
两项贡献（buffer=None）：

```
term_up_rows_minus_daily    = +0.00012634851095550114
term_market_rows_minus_daily = −0.000023303749888395862
⇒ 244 × (两者之差)          = +3.651515164591087%
observed_A_minus_B           = +3.651515164591087%   ← 逐位相同
```

⚠️ **第一层来源是分母不同**（`rolling_stats` 里）：
`avg_up` 的分母是 **up 腿行数**，而 `market` 的分母是**全表（up+down）行数**
⇒ A 口径的 market 项**天生带 down 腿的权重**。第二层来源是 up 篮子逐日成分数不齐。
**两个来源独立，夹具里分别打开。**

★ **判决方向不受影响**（两者都 `INSUFFICIENT`），但**符号受影响**：
若有人按 A 读，这条信号是「微正 +0.75%」；按 B 读，它是「**负的 −2.90%**」。

### ② `parity` 核的量与产物正文报的量**不是同一个**

`by_year` 产物的 `parity.parity_against` 指向主产物，`mismatches = []`。
读源码（`_check_parity`）后确认：它核的是 **`annual_up_excess` / `annual_cost`**，
而 by_year **自己也用 `sweep._metrics()` 重放** ⇒ **核的是 A vs A**。
但 by_year 的**主判决量**是 `pooled()` 的口径 **B**（−2.90%）。

⇒ **`mismatches: []` 是对的，但它证明的不是「产物正文那个数被核对过」。**
⚠️ 字段名与 `parity_against` 都不带被核对的量名 ⇒ 读者会误以为报出来的数被对拍过。
**处置：登记，不改字段**（改字段要动已盖章的产物生成器）。
⇒ 三个口径各自的守卫在 `min_basket` 产物里分开承担：
`parity.closes` 核 A、`B_vs_pooled_ref.closes` 核 B（变异 M3/M4 各自能杀掉一个）。

### ③ 🚨 `n_no_data` 两臂差 **2.38× ~ 7.56×** ⇒ 差值信号混着样本差异

| 年 | `sign_fixed` | `wrong_sign` | 比值 |
|---|---|---|---|
| 2021 | 57 | 178 | 3.12× |
| 2022 | 47 | 186 | 3.96× |
| **2023** | **18** | **136** | **7.56×** |
| 2024 | 63 | 150 | 2.38× |
| 2025 | 51 | 127 | 2.49× |
| **2026** | **26** | **161** | **6.19×** |

（buffer=3.0 逐字同量级：3.14 / 3.91 / 7.56 / 2.37 / 2.42 / **6.31**）

⚠️⚠️ **`scripts/regime_control_sweep.py` 的主指标定义是**
`signal = 指标(符号修正) − 指标(错号对照)`，
而它的 docstring 逐字承诺：

> 「两个配置跑在**同一批日期、同一份面板**上，只差权重符号。
> 它们共同的部分（市场状态、板块轮动、2019-2023 的动量牛市）**逐项抵消**」

⇒ **在样本层面这个前提就不成立**：两条腿每天进入计算的标的数差 2.4~7.6 倍
（`n_no_data` 是「这天有多少条预测没有次日 bar」）。
⇒ **差值里混着样本差异，而样本差异本身不是信号。**
⚠️ 这一条**不改变本判决**（主判决读的是口径 A 的单臂，不读差值），
但它**直接威胁 sweep 的 8 行 `signal_net` 与 `paired_t` 的可解释性**——
包括 K=1 那个 `signal_net = +3.08`、`paired_t = +8.26`。
⇒ **登记为 `signal_net` 的前置债**：在把差值当「选股符号的贡献」引用之前，
必须先解释为什么两臂的有效样本差这么多。

### ④ 🚨 字段名 `plus1sd_*` 实际加的是**标准误**，而预注册写的是 `+1σ`

源码（`scripts/regime_control_min_basket_control.py`）：

```python
215:                "plus1sd_pct": point + se}
```

⇒ `plus1sd = point + se_annual`，**不是** `point + sd_annual`。
实测验证（buffer=None）：`0.00748 + 0.09318 = 0.10066` = 产物 `A_rowwise.plus1sd_pct` ✅；
而 `0.00748 + 0.22279 = 0.23027` ≠ 产物值。

`by_year::pooled()` 的 `plus1sd_NET_pct` / `plus1sd_GROSS_pct` **同样是 +SE**
⇒ **两个独立实现一致**，所以这不是某一处的笔误，是**一处一致的口径**。

⚠️ **预注册第四节第三条逐字写的是**「`< 门槛` 但 `+1σ ≥ 门槛` → `UNDERPOWERED`」。
⚠️ **偏离方向是保守的**（SE < SD ⇒ 更易判 `INSUFFICIENT` 而非 `UNDERPOWERED`）。

★ **当前判决不受影响**：按 `+1σ`（真标准差）重算，六个格最高是
buffer=3.0 的 A：`0.01712 + 0.22380 = 0.24092 < 0.2639` ⇒ **仍是 `INSUFFICIENT`**。
⇒ 但这**不是「守住了」，是两条路径恰好落在同一档**。
⚠️ **只要点估计再高 2.3 个百分点，两种读法就会分档。**
⇒ 登记：字段名与判据字面不对应，且**当前数据上不改变判决**。

### 判决读法分档

🚨 **本节是 2026-10-21 的事后补记**（生产配置 K=21 → **K=63** 后的重跑实测）。
**它不是预注册**（第 91 层：跑过数据后不可辨 ⇒ 补写出来的登记不能冒充预注册）。

⚠️ **上面那句「当前判决不受影响」在 K=63 上已被证伪。**
K=21 时代六格恰好落在同一档；**K=63 重跑后六格全部分档**：

| 格 | 实现读法（+SE） | 字面读法（+1 SD） |
|---|---|---|
| `None/A_rowwise` | `INSUFFICIENT` | `UNDERPOWERED` |
| `None/B_daily` | `INSUFFICIENT` | `UNDERPOWERED` |
| `None/C_daily_min_basket` | `INSUFFICIENT` | `UNDERPOWERED` |
| `3.0/A_rowwise` | `INSUFFICIENT` | `UNDERPOWERED` |
| `3.0/B_daily` | `INSUFFICIENT` | `UNDERPOWERED` |
| `3.0/C_daily_min_basket` | `INSUFFICIENT` | `UNDERPOWERED` |

⇒ 「点估计再高 2.3 个百分点就会分档」那句预警**已经兑现**（K=63 把点估计抬上去了）。
⇒ **判决方向受影响** ⇒ 读 `plus1sd_*` 的人**必须知道它加的是 SE**。
⇒ **处置**（第 154 层）：**不许**把实现改成 `+sd`（那是**预注册判决**的口径）
⇒ 保留字段名与实现的错位，**由本节逐字记录分档**。
⇒ 守卫 `tests/test_regime_control_min_basket_control.py::
test_the_judgment_document_records_exactly_the_verdict_reading_split`
**从产物算出分档集合**并与**本节记录的集合**逐项相等 ⇒
**文档漏记 ⇒ 红**（下一个读产物的人会被骗）；**文档多记 ⇒ 红**（文档在讲不存在的事实）。

### ⑤ 篮子成分数最小值是 **1**，5 天的有效成分 < 3

产物 `basket_size.min = 1`（两臂皆是）⇒ **确实存在「篮子只剩 1 只有效成分」的日子**，
正是 `verify.py:229-234` 记的那次事故的形态。
`regime_control_sweep.py` 里没有任何 `MIN_BASKET` 等价守卫（预注册附则一第一节逐字）。
⇒ 对照臂把它们剔掉了，代价是 −0.556 pp/年（B −2.904% → C −3.459%）。
⇒ **主产物 `annual_up_excess`（口径 A）仍然含这 5 天**，而 A 是预注册主指标。

### ⑥ 预注册里有**两处行号锚点已腐烂**

| 预注册写的 | 盘上实际 | 差 |
|---|---|---|
| 第二节「源码 **:159** = up_excess_vs_market × 244」 | `sweep.py:159` = `n_days = len(set(up["pred_date"])) if len(up) else 0`；真那行是 **`:164`** | **差 5** |
| 附则一「`regime_control_sweep.py:183` `return s.dropna()`」 | 真身在 **`:184`** | **差 1** |

⚠️ **`verify.py` 的三个锚点全部正确**（`:19` `MIN_BASKET = 3`、
`:229-234` 事故记录原文、`:398 daily = daily[size >= MIN_BASKET]`）。
⚠️ **腐烂的两处都指向「同一函数里的另一行」** ⇒ 读者核不下去，因为它读起来完全合理。
⇒ **本文引用行号时一律同时给出行内容**（例如上表），因为**行号本身会腐烂而内容不会**。
⇒ 处置：**登记，不改预注册**（预注册是事后登记的文档，改它的行号会抹掉「当时写的」这个事实）。

---

## 七、本次执行里我自己犯的一个错（登记）

⚠️⚠️ **我第一次查新鲜度时把裸名传给了 `provenance.freshness()`：**

```python
provenance.freshness("regime_control_sweep.json", root=Path("."))
```

⇒ 它逐字回报「**产物路径 `regime_control_sweep.json` 不存在** ⇒ 这是**产物缺失**」，
**四份产物全部这么报**，而**同一秒前我刚从这四份里读出了 `n_ranked`**。

**成因**：`freshness()` 用 `Path` 拼 sidecar，裸名拼出的是
仓库根下的 `regime_control_sweep.json.provenance.json`；
真名在 `data/reports/` 下。⇒ **「产物不存在」这句话是关于**我传的路径**的断言，
不是关于产物的断言。**

⚠️ **我差一点就据此改文档**——若照写，会把三份已盖章产物登记成
「产物缺失、不可引用」，**方向与事实完全相反**。
⇒ 这是 `found-is-not-found-right` 的第四个入口：
**前三个是子串锚点抓错表、探针抓不到、抓错了不崩；这一个是我自己调用错参数，
而函数用一句自信的散文回答，且不抛异常。**

⇒ 记下来的做法：**引用任何「新鲜度」结论前，先确认那次调用的路径解析约定**
（`grep` 一处真实调用点即可），**不许把 `unknown` 读成「产物有问题」**——
`unknown` 的字面意思是「**判据没跑成**」，不是「事实为否」。

---

## 八、仍然未偿的债

| # | 债 | 状态 |
|---|---|---|
| 1 | `data/reports/regime_control_sweep.json`（**选段** 2019-01-02~2023-12-31） | `freshness = unknown`，原文「**没有溯源 sidecar——生成它的 commit 无从得知**」⇒ 按本仓规矩**不可引用** |
| 2 | `data/reports/lowfreq_plausibility.json` | 同上，`unknown`，无 sidecar |
| 3 | 新发现 ③（`n_no_data` 两臂差 2.38~7.56×） | **未偿**，直接威胁 `signal_net` 的可解释性 |
| 4 | 新发现 ④（`plus1sd` 名实不符） | **未偿**，**K=63 重跑后六格全部分档 ⇒ 判决受影响**（见 ④ 的「判决读法分档」补记） |
| 5 | 新发现 ②（`parity` 核的量 ≠ 产物主量） | **未偿**（登记） |
| 6 | 新发现 ⑥（预注册两处行号锚点腐烂） | **未偿**（登记，不改预注册） |
| 7 | `scripts/regime_control_sweep.py:141` 面板缓存裸 `write_bytes`（**无盖章**） | ⚠️ **刻意未修**：改它会动字节并把已盖章的第一份产物翻成 `stale` |
| 8 | `n_ranked = 3701 < 4000`（PREREG 第三条不满足） | **观察项**，见下 |

⚠️ **`n_ranked = 3701`**（判决窗口）**与选段的 `3362` 是两个不同窗口的两个数，
不许混引**（第 7 种形状）。选段那条已在
`docs/TODO_regime_control_n_ranked_trap_2026-10-15.md` 单独登记。
⇒ 判决窗口这条**只作为观察印出**：
**它不构成「差值信号够线」**，因为差值信号那两条门槛
（`signal_net ≥ 0.20`、`paired_t ≥ 3.0`）在 8 行里
K=21 两档分别是 `+0.7251 / +2.540` 与 `+0.7501 / +2.603` ⇒ **两条都不够**。

---

## 九、判据一个字没改的证据

| 项 | 产物逐字 |
|---|---|
| 预注册主指标 | `sign_fixed.annual_up_excess`（A），**未改** |
| `target_signal_net` | `0.2` |
| `min_paired_t` | `3.0` |
| `min_n_ranked` | `4000`（**未调到 3701**） |
| 门槛 | `0.2639047619047619`，由 `hurdle_rate(target=0.20, cost_per_round=COST_REAL_50K, rounds_per_year=244/LIVE_REBALANCE_EVERY, benchmark_annual=0.0)` 实算 |
| `MIN_BASKET` | `3`，从 `aplus/verify.py:19` **导入**（变异 M7 把门槛退化成字面量 `1` 时 1 条红） |
| 四条判决规则 | `scripts/regime_control_min_basket_control.py::verdict` 逐条对应，**不发第五档** |

⚠️ **8 行 `prereg_hit` 全为 `false`**（逐字）：

```
K=  1 buf=None n_ranked=3701 signal_net=+3.0764 paired_t=+8.259 prereg_hit=False
K=  1 buf= 3.0 n_ranked=3701 signal_net=+2.4253 paired_t=+6.674 prereg_hit=False
K=  5 buf=None n_ranked=3701 signal_net=+1.5939 paired_t=+4.705 prereg_hit=False
K=  5 buf= 3.0 n_ranked=3701 signal_net=+1.3841 paired_t=+4.192 prereg_hit=False
K= 21 buf=None n_ranked=3701 signal_net=+0.7251 paired_t=+2.540 prereg_hit=False
K= 21 buf= 3.0 n_ranked=3701 signal_net=+0.7501 paired_t=+2.603 prereg_hit=False
K= 63 buf=None n_ranked=3701 signal_net=+0.6378 paired_t=+2.240 prereg_hit=False
K= 63 buf= 3.0 n_ranked=3701 signal_net=+0.6388 paired_t=+2.233 prereg_hit=False
```

⚠️ **K=1 两行的 `signal_net` 与 `paired_t` 大幅超标**，
而 `prereg_hit` 仍为 `false`——**唯一卡住它的是 `n_ranked = 3701 < 4000`**。
⚠️ **这不构成理由去调门槛**（预注册第三节硬约束 2 逐字禁止）。
⚠️ 而且按新发现 ③，`signal_net` 本身混着两臂的样本差异 ⇒
**它越超标，越不能当作「符号修正带来了这么多信号」的证据。**

---

## 十、明确不许做的事（预注册附则之二逐字 + 本次追加）

- ❌ 因为 `annual_breadth` 超门槛就宣告达标 —— 它不是第二节的指标，且是多空价差。
- ❌ 把产物 `prereg.primary_metric`（`signal_breadth_excess`）当成判据来源。
- ❌ 因为两个 buffer 里「+1.71% 更好」就只报 `3.0`。
- ❌ 用 `flat` 污染的路径（`annual_breadth`）去解释主指标。
- ❌ 把 `MIN_BASKET` 的对照臂说成「主指标本该如此」并据此重写预注册。
- ❌ **把 `n_ranked` 调到 3701 或 3300**。
- ❌ **把「六个格都 `INSUFFICIENT`」读成「A/B 口径之争不影响任何事」** ——
  它不影响**判决方向**，但**符号**受影响（A 正 B 负）。
- ❌ **把 `plus1sd_*` 当成「点 + 1 个标准差」引用**（它加的是标准误）。
- ❌ **把 by_year 的 `mismatches: []` 读成「产物正文那个数被核对过」**（它核的是 A）。

# `notna()` ≠ `is_valid()`：`flat` 对 `stats.json` 三字段的污染，以及两个字段名与值不同量

> ⚠️ **本文件是事后清点（2026-10-15），不是预注册。**
> 按「补写出来的登记不能冒充预注册」：**它不回填任何判据，只登记一个口径缺陷。**
> 与判决窗口复测（`docs/PREREG_4FACTOR_JUDGE_WINDOW_2026-10-15.md`）**完全无关**，
> 下面不出现该判决的任何数字。

## 一、一句话

`aplus/verify.py::judge()` 把 `actual_pct == 0` 单独归为 `result = "flat"`，
`JUDGEABLE = ("hit", "miss")` 又把 `flat` 排除 ⇒ **判定层已裁定它不是有效观测**；
但 `avg_up` / `avg_down` 用 `.mean()`，`skipna=True` 只跳过 `NaN`，
**`0.0` 不是 `NaN`，于是被当成真实的零收益参与平均。**

⇒ **同一个字段，两条路径，两个相反的裁定，且无任何注释说明。**

## 二、源码（逐字，非推算）

```python
# aplus/verify.py:26-27    判定路径 —— 认出 0.0 是特殊值
if actual_pct == 0:
    return "flat"

# aplus/verify.py:236,240  均值路径 —— 不认
up_net = ((up["actual_pct"] - up["cost"]) if "cost" in up.columns
          else up["actual_pct"])
avg_up = float(up_net.mean()) if n_up else 0.0

# aplus/verify.py:243      同理
avg_down = float(down["actual_pct"].mean()) if n_down else 0.0
```

⚠️ `n_up = up_net.notna().sum()`（`:238`）、`n_down = down["actual_pct"].notna().sum()`（`:239`）
——**`notna()` 对 `0.0` 返回 `True`。**
⇒ 守卫（有效成分数）**看不见** `flat`。

## 三、这不是假设：仓库自己记着同类事故

`aplus/verify.py:229-234` 逐字：

> 「`.mean()` 默认跳过 NaN，所以「篮子平均收益」可能只由**一只**股票构成——而调用方
> 完全看不出来。2026-10-11 实测：某日跌篮子 10 只里 9 只没有次日 bar
> （退市/长期停牌），`.mean()` 静默按剩下的 1 只计算，再被日度汇总平均，
> **多空价差虚报到 +9.84%**。」

落地：`verify.py:19 MIN_BASKET = 3`、`:398 daily = daily[size >= MIN_BASKET]`。

★★★ **⇒ 那次事故是 `NaN` 那一半，`MIN_BASKET` 也只管 `NaN`。
`flat`（`0.0`）这一半从未被覆盖——它长得像一个有效的数，所以躲过了以 `notna()` 为锚的守卫。**

## 四、本次实测（区分「产物字段」与「新增测量」）

`data/reports/verification.parquet` 全 100 行，`result` 四态：

```
hit 45 / miss 44 / no_data 8 / flat 3      合计 100 ✓
up 50 / down 50                              合计 100 ✓
```

⚠️ 闭合关系（实测）：

| 腿 | 判定 hit+miss | flat | no_data | 合计 |
|---|---|---|---|---|
| up | 45 | **0** | 5 | 50 |
| down | 44 | **3** | 3 | 50 |

`judged = 89 = 45 + 44` ✓ ⇒ **三条 `flat` 全在 down 腿，up 腿零污染。**

`flat` 三条的形态（实测）：

```
pred_date 全为 2026-10-09   actual_pct 精确 0.0
entry_date 2026-10-12       exit_date 2026-10-13
```

⇒ 对照 `no_data` 的 `actual_pct = NaN`（2026-10-12 七条）：
**同一个缺口（`2026-10-12` 取数失败），两种编码，一种污染统计。**
⚠️ 三只 A 股同日精确 `0.00%` 不是市场现象——是取数失败被填成了 `0`。

### 污染量级（★ **以下三项是本次新增测量，不是 `stats.json` 的产物字段**）

⚠️ `market_pct` 均值 `−0.00688834` **也是新增测量**——`stats.json` 没有这个字段。

| 字段 | 含 flat（产物报的值） | 排除 flat（新增对照臂） | 差 |
|---|---|---|---|
| `avg_up_return` | −0.00474596 | −0.00474596 | **+0.00000000** |
| `avg_down_return` | −0.00491908 | −0.00525447 | **+0.00033539** |
| `down_excess_vs_market` | +0.00196926 | +0.00163387 | **−0.00033539** |
| `excess_tilt` | +0.00411165 | +0.00377626 | −0.00033539 |
| `excess_breadth` | +0.00017312 | +0.00050851 | **+0.00033539** |

### 方向与后果

⚠️ `down_excess = market − avg_down`（`verify.py:246`）
⇒ **跌篮子跌得越多，`down_excess` 越大。**
⇒ 真实情况跌篮子跌 **−0.5254%**，含 `flat` 的账把它算成 **−0.4919%**
⇒ **「回避清单有效」这个卖点被虚报 0.034 个百分点/笔。**

⚠️ 而 `down_excess` 的注释逐字是
**「正 = 回避有效（挑出的确实跑输市场）」**（`verify.py:246`）。

⚠️ 两个字段朝**相反**方向偏：`excess_breadth` 偏低（相对误差 >2 倍）、
`excess_tilt` 偏高（相对误差 ~8%）。而它们是两个不同的卖点。

### 量级定性

★★ **这不是判决翻转级别的问题**：绝对偏移 3.4e-4/笔，
按 `LIVE_REBALANCE_EVERY = 21`（年 11.6 笔）折算约 **0.39%/年**，
相对 20% 目标是噪声。
★ **但它是必须修的缺陷**，理由是**相对误差**（`excess_breadth` 差 2 倍），
以及它证明 `notna() ⇒ is_valid()` 这个替换在本仓库**已发作过两次**
（`NaN` 那次 +9.84%，`0.0` 这次）。

## 五、附带的第二个缺陷：字段名与值不同量

`verify.py:296-297` 报 `"up_basket_n": n_up, "down_basket_n": n_down`，
而 `:238-239` 里 `n_up = up_net.notna().sum()`、`n_down = down["actual_pct"].notna().sum()`
——**都是「已判定」条数，不是「篮子条目数」。**

实测：up 篮子实际 **50** 条，报 `up_basket_n = 45`；down 实际 **50** 条，报 `47`。
⇒ **字段名说「条目数」，值是「已判定条目数」；
而同一张表里 `n_predictions = 100` 是「全部条目数」。两者分母不同却并列。**

⚠️ 命中率用已判定做分母**是对的**；问题在**字段名没说是哪一个**。
⇒ 调用方拿 `up_basket_n` 当「up 篮子有 45 只」就错了。

★ **同族**：`docs/TODO_regime_control_n_ranked_trap_2026-10-15.md` 的 **D0**
（`annual_breadth` 从「超额」改名为「价差组合（多空）宽度」）——
**D0 不只该改 `regime_control_sweep`，也该改 `stats.json` 这几个字段的标签。**

## 六、附：一次自我更正的记录

⚠️ 我先观察到 `up_basket_n + down_basket_n = 92 ≠ n_predictions = 100`，
**假设**有 8 条 `direction` 非 `up/down`。
★ **实测证伪**：direction 恰为 up 50 / down 50，无缺失。
真相是 `result` 有 `no_data` 8 条被 `JUDGEABLE` 排除。
⇒ 「找到了 ≠ 找对了」的又一次：**先数出一个对不上的数，就以为找到了原因。**

## 七、处置（不在本文件执行；不改任何判据）

| # | 动作 | 约束 | 状态 |
|---|---|---|---|
| F1 | 用显式有效性判据替代 `notna()`：`result.isin(JUDGEABLE)` 或 `actual_pct != 0` | 只改均值口径，**不改 `judge()`**（它是对的） | 待做 |
| F2 | `up_basket_n`/`down_basket_n` → `n_up_judged`/`n_down_judged`，另报 `up_basket_total` | 只改名，不改值 | 待做 |
| F3 | `flat` 与 `no_data` 归一为同一种缺失编码 | 取数层（`run_daily.py`），不改判定 | 待做 |
| F4 | 加守卫：`flat`/`no_data` 条数必须随产物报出 | `guards-only-count-after-mutation`：变异后才算 | 待做 |

## 八、明确不许做的事

- ❌ 因为 `up_excess_vs_market = +0.002142384` 是正的，就宣称产品已达标。
  （它挂在 `n_independent_windows = 3` 个独立成交窗口上。）
- ❌ 拿 `excess_tilt` 当「多头超额」对外说——它是 `up_excess − down_excess`，
  是**多空价差**（`compare-like-calibers-or-not-at-all`）。
- ❌ 用本文件的修正值去替换任何已提交的判决数字；本文件只登记缺陷。

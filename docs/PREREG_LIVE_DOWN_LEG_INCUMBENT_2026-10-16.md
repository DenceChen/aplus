# 预注册：`live_down_leg` 报告**现任**那一行，不是 18 组里的 max

> 🚨 **本文件的写作时间是「代码未改、守卫未写」**。
> 据此它满足 [[pre-register-thresholds-before-data]]。
> ⚠️ 但**有一条必须诚实写明**，见 §2 第 3 条。

## 1. 要修的是什么（一句话）

`live_down_leg()` 同时返回两个字段，而它们**来自不同的候选行**：

```python
worst = max(float(r["avg_down_return"]) for r in rows)   # ← 最坏那一行
rates = [r["hit_rate_down"] for r in rows if ...]
best_rate = max(rates) if rates else None                 # ← 最好那一行
return {"known": True, "avg_down_return": worst,
        "hit_rate_down": best_rate, ...}
```

⇒ **一个 `dict` 里的两个字段讲的是两个不同候选的故事。**

## 2. 判据（改代码之前写死）

**① 判据臂不改。** 「跌腿必须真跌」这条判据（`avg_down_return < 0`，理由见该函数 docstring
「判据用绝对收益，不用跑输大盘」）**一个字都不动**。本轮动的是**「这个量是关于谁的」**。

**② 主体是现任。** 外部锚（与任何读数无关）：

- 产品承诺文本 = 「每天推荐 10 涨 **10 跌**」⇒ 问的是**用户今天拿到的那 10 只跌没跌**。
- 用户名单由 `scripts/run_daily.py:417` 的 `load_weights(reports_dir)["weights"]` 出单，
  按 `execution["rebalance_every"]` 调仓。
- ⇒ **「兑现了吗」的量必须取现任那一行**，不是候选池的极值。

**③ 🚨 诚实边界：这条判断是在看到 `incumbent` 与 `max` 相差很远之后才形成的。**
BUT 论证**不依赖那组读数** —— 论证只依赖「承诺的主体是用户名单」，而名单的定义在
`run_daily.py:417` 里写死。读数只是**让我发现实现没兑现这个承诺**，
不是**让我选择兑现哪个承诺**。⇒ **这不是第 91 层。**
⚠️ 但读者有权知道这个时间顺序，所以写在这里。

**④ 两条同源断言（今天的真缺陷，与判据臂无关）：**
`avg_down_return` 与 `hit_rate_down` **必须来自同一行**。
现状两者不同源 ⇒ 任何把这两个数并列解读的动作（**我今天做过一次，已当场撤回**）
都在读一个仓里不存在的组合。

**⑤ incumbent 找不到 ⇒ `known=False`。** 不许回落到 `max` —— 那会悄悄把
「没找到现任」变成「最坏候选」⇒ 与函数 docstring 自己的
「没有数据就说没有（`known=False`）」直接矛盾。

**⑥ `live_cost_share` 不在本轮范围内。** 它取最坏是对的：成本问的是
「这套执行方式**最坏**能有多糟」，与跌腿问的不是同一个问题。
⇒ 🚨 **同一个 `max` 手法套在两个问题上，一个对一个错。**

## 3. 已实测的读数（**机制证据，不是判决**）

来源 `data/reports/ic_guided_race_7y_corrected.json`（⚠️ 10-14 旧产物，
输入是 `/tmp` 快照 ⇒ **不可引用为 D1 判决**；此处只用于说明「取 max 会掩盖什么」）：

| K=21 候选 | `avg_down_return` | `hit_rate_down` |
|---|---|---|
| **现任行**（`weights == DEFAULT_WEIGHTS`） | **−0.144052%** | 0.5303 |
| 18 组里 `max` 那行 | **+0.0957%** | — |
| 18 组里 `min(hit_rate_down)` 那行 | — | 0.5061 |

⚠️ `DEFAULT_WEIGHTS` 逐键相等命中 `table` 第 14 行（`rebalance_every=21`, `buffer=None`）
⇒ 不是模糊匹配。

## 4. 本文件**不**主张的东西

- ❌ **不**主张跌腿失效。第 3 节恰好相反：**现任那一行跌腿是真跌的**。
- ❌ **不**改 `GATE_NAMES` 的任何一道闸（`aplus/evolve_race.py`）。
- ❌ **不**改 `avg_down_return < 0` 这条判据。
- ❌ **不**改 `live_cost_share`。
- ❌ **不**因本修复而宣称 `[down]` 行会变绿 —— **修完可能是真警报**，
  而真警报才是有价值的（[[an-over-strict-guard-is-as-harmful-as-a-tautological-one]]：
  过严的守卫是**绿的**，会把人推向「改本来正确的代码」；假警报是**红的**，
  会把人推向「修本来正确的代码」）。
- ❌ **不**用刚跑完的赛马当验证 ⇒ 赛马是 10-16 新产物，本预注册写在它落盘**之前**。

## 5. 处置清单

1. 本文件
2. 守卫：三条互相独立 —— ①**同源**（两字段同行）②**主体是现任**（`max` 会红）
   ③**找不到 ⇒ `known=False`**（不许回落 max）
3. 改 `live_down_leg`：加 `champion_weights` 参数（🚨 **不许有默认值** ——
   有默认值则「现任是谁」这个区分会被某次调用悄悄抹掉 = 第 119 层），
   改为在**同一行**上取两个字段
4. 改 `run_daily.py` 的调用点，把 `load_weights()["weights"]` 传进去
5. 变异：三条各配一个变异体，**逐条 `rc=1`** 才算数
6. 跑门禁，引用 `GATE: 摘要 → …` / `GATE: 判决 → …` 原文

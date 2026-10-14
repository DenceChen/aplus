# 重跑待办：独立验证报告的口径修复

> 发现于 2026-10-14，由「查反转族毛超额为负的根因」子代理查出，
> 我逐条复核代码后确认。**不是假重跑，输入面板干净**——
> 错的是报告脚本自己的三个口径。
>
> 铁律：**修完 P0 一律重跑，重跑前绝对收益数字不可引用。**

## 状态

| 项 | 值 |
|---|---|
| 报告 | `data/reports/VOID_reversal_freq_2019_2023__cost_10x_and_double_deduct.json` |
| 代码修复 | 已完成（`scripts/_verify_reversal_freq.py`） |
| 回归测试 | `tests/test_reversal_report_cost.py`（7 条，全绿） |
| **重跑** | **未做 —— 在此之前报告里的量级一律不可引用** |

## 三个 bug

### 1. 年成本放大 ~10 倍

```python
cost = float(up["cost"].sum()) / n_years      # ❌
cost = float(up["cost"].mean()) * DAYS_PER_YEAR   # ✅
```

`cost` 是**篮子级逐日**值，一天一个数、被复制到当天约 10 行 up 记录上
（`aplus/backtest.py`: `res.loc[res["direction"] == "up", "cost"] = drag`）。
`.sum()` 把同一个 drag 数了约 10 遍。实测六行的放大比全部落在 **9.97–9.99**。

### 2. 净收益重复扣了一次成本

`aplus/verify.py:238`：
```python
up_net = up["actual_pct"] - up["cost"]
up_excess = avg_up - market
```

所以 `up_excess_vs_market` **已经是净值口径**。而原脚本有一行注释
断言它是「毛超额」，并据此写了 `net = ann_x - cost`。

> 那行注释不是描述代码，是**替代码辩护**。我当时以为核过 `rolling_stats`，
> 实际没读到 `avg_up` 是怎么构造的。

影响：报告的 `净` 多扣 11–35 个百分点。

### 3. t 检验没按调仓周期分块

```python
t, _ = excess_tstat(pred)          # ❌ 自由度虚增 K 倍
t, _ = excess_tstat(pred, k)       # ✅
```

K=63 的 1151 个日观测只有约 **18 次独立决策**。
闸门链一直传的是分块口径，**只有这个独立验证脚本漏了**——
于是它的 t 与赛马的 t 不可比，却并排印在同一份报告里。

实测：K=63 的超额 t 从 **−1.37 变成 −2.85**。

## 为什么这个 bug 特别难发现

三个 bug 的**符号方向一致**：都让成本显得更大、净收益显得更差。
于是整张表看起来都在说「反转策略完蛋了」——

> **结论方向与 bug 方向同向，人不会去质疑一个支持自己既有判断的数。**

## 结论有没有变

**没有变。** 24 行的 `annual_excess` 全为负——那一列不受三个 bug 影响
（它们只污染 `annual_cost` 和 `net`，而 `ann_x` 是直接从 `rolling_stats` 取的）。

变的是两件事：

1. **量级**：最乐观行从报告的 `net −39.70%` 修正到约 `−5.06%/年`。
2. **「最优行」的 identity**：报告的 `best` 是 `K=63 buf=3.0`，
   因为它按含污染成本的 `net` 选；按 `annual_excess` 排最乐观的是
   `纯20日反转 K=21 buf=3.0`。

## 重跑命令

面板缓存仍在（`/tmp/reversal_panel_cache.pkl`，带基准指纹），
重跑约需数分钟：

```bash
python scripts/_verify_reversal_freq.py \
  --data-root /Users/airboom/opencode/aplus \
  --start 2019-01-02 --end 2023-12-31 \
  --out data/reports/reversal_freq_2019_2023.json
```

跑完必须：
- [ ] 手工盖溯源章（脚本自己会盖，但要核对 `panel_market_mean`）
- [ ] 把 `aplus/provenance.py` 里的 `VOID_…` 条目删掉
- [ ] 删掉 `data/reports/VOID_…json` 与它的 sidecar
- [ ] 更新 `tests/test_reversal_report_cost.py` 里那条
      `test_every_row_still_has_negative_annual_excess_after_the_fix`
      （它是**底线断言**：若重跑后出现正超额，不是「修对了」而是引入了新错误）

## 未解决的子问题（子代理提出，需另行验证）

- **`breadth_t` 门禁构造物——已验证（2026-10-14）**。
  **机制为真，但方向与子代理推测相反，真实读数低于零信号基线。**
  详见 `docs/MISTAKE_LOG_2026-10-14.md` 第 65 条与
  `tests/test_breadth_gate_asymmetry.py`。摘要：
  - 零信号基线 `breadth_t ≈ +4.88`（高于 Bonferroni 临界 3.55）
  - 真实数据厚尾在 **up 侧**（11.39% vs down 1.17%），因为
    `_up_leg_exit` 对封跌停是 `sell_extended` 而非删样本
  - 真实 `breadth_t = −1.17`（不显著、为负）
  - **结论：构造物把价差压低，没有替任何正结论背书。**
    `breadth_t` 在 `GATE_WARNINGS` 不在 `GATE_NAMES`，不参与晋级判决。
  - ⚠️ **引用 `breadth_t` 必须带 +4.88 这个基线。**
- **`MIN_BASKET` 丢日偏差**：2019 只保留 184/244 天，被丢的 60 天市场年化 +153.7%，
  保留的天 −3.6%。→ 报告里「2019 是熊市」是丢日造出来的。
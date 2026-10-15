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

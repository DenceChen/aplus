# TODO：13 处「一只票定全池」的单点依赖（2026-10-16 登记）

## 一句话

`end = pd.read_parquet(files[0])["date"].max()` 这个形态在 `scripts/` 下有 **13 处**，
每一处都拿**字母序第一只票**的末日期代表**全池**最新交易日。
今天不出事只因为 `000001.SZ` 恰好是全池最新的 —— **那是运气，不是性质**。

## 为什么现在登记而不是现在修

2026-10-16 修掉了其中一处（`scripts/forward_validate.py`，前瞻验证那条**唯一出路**），
修的过程顺手量出了这个债的规模。剩下 13 处的修法已经清楚（抽一个共用函数收敛），
但**现在不动**，理由有两条，都不是「懒得做」：

1. 🚨 **它不改变今天的任何一个判决**（`000001.SZ` 今天是全池最新 ⇒ 13 处取值全对）。
   按第 112 层通则，为一个「今天不改变结论」的改动去动 13 个文件，
   是**过度改动**——而过度改动与「过严的守卫」同源：它让人去改本来正确的代码。
2. 🚨 **改动面 13 个文件 vs 收益 0 个数字**。真要它产生价值的那天，
   是 `000001.SZ` 停牌的那天，而那天之前它一直是对的。
   ⇒ 正确的做法是**让守卫继续盯着它**（仓里已经盯着了，见下），
   而不是提前把 13 个文件都改成「未来才正确」的样子。

## 实测证据（不是推断）

```
$ .venv/bin/python -c "…300 只随机样本…"
全池最大日期 = 2026-10-15
样本中比 files[0] 更旧的票数 = 12
[('002231.SZ','2026-03-26'), ('600747.SH','2019-12-12'),
 ('600723.SH','2021-10-22'), ('600193.SH','2026-07-03'),
 ('605081.SH','2026-07-02'), …]
```

⇒ 「一只票代表全池」在真实主库里**确实会分道扬镳**，只是还没轮到 `000001.SZ`。

## 13 处清单（形态完全相同）

| 脚本 | 形态 | 产物/引用 |
|---|---|---|
| `candidate_block_audit.py` | `args.end or files[0]` | `candidate_block_audit.json`、`evolution_ledger.json` |
| `evolve_race_composite.py` | `args.end or files[0]` | `evolution_ledger.json`、`AUDIT_honesty` |
| `evolve_race_fast.py` | `args.end or files[0]` | `evolution_ledger.json`、`DATA_INVENTORY` |
| `evolve_weekly.py` | `files[0]`（**无 `args.end` 兜底**） | `evolution_ledger.json`、`forward_registry.json` |
| `execution_lag_ab.py` | `files[0]` | `DATA_INVENTORY`、`PLATFORM.md` |
| `extract_ic_matrices.py` | `args.end or files[0]` | `DATA_INVENTORY`、`TODO_measure_sigma_r` |
| `factor_ic_diagnostic.py` | `args.end or files[0]` | `evolution_ledger.json`、`factor_ic_7y_v2` |
| `ic_guided_race.py` | `args.end or files[0]` | `evolution_ledger.json`、`forward_registry.json` |
| `measure_bridge_error.py` | `args.end or files[0]` | `AUDIT_honesty`、`DATA_INVENTORY` |
| `placebo_control.py` | `args.end or files[0]` | `placebo_control.json`（**安慰剂对照臂**） |
| `significance_check.py` | `files[0]` | `DATA_INVENTORY`、`PLATFORM.md` |
| `universe_forensics.py` | `args.end or files[0]` | `evolution_ledger.json`、`MISTAKE_LOG_2026-10-11` |
| `universe_isolation.py` | `args.end or files[0]` | `evolution_ledger.json`、`DATA_INVENTORY` |

**8 处带 `args.end or` 兜底**（手工传 `--end` 时绕过该取法），
**5 处没有**（`evolve_weekly` / `execution_lag_ab` / `significance_check` 等）
⇒ 没有兜底的那几处**连人工都无法绕过**，是风险最高的一档。

## 修法（已定，但不在本轮动手）

抽**一个**共用函数，13 处收敛成一处调用。**不要逐个手工改**——
那是 13 个可以各自漂移的副本（第 163 层：用手写重复代替单一量具的镜像）。

```python
# aplus/store.py 或新建 aplus/timeline.py
def latest_trading_day(files) -> str:
    """全池最新交易日。逐只读 date 列取最大值；全池读不出日期时抛错，
    不许返回 'None' 字符串——那会让下游 needs_forward_panel 走成
    「有窗口」而静默跳过整轮，症状与「样本还没攒够」逐位同形（第 148 层）。"""
```

⚠️ **`evolve_weekly.py` 那处额外注意**：它没有 `args.end` 兜底，
且 `start` 是由 `end` 反推的（`end - window_days`）⇒ `end` 退回一天，
`start` 一起退，窗口整体平移 ⇒ **样本区间与登记的窗口错位**，
而不只是「少一天」。这一处的后果比其余 12 处都重。

## 仓里已有的守卫（别再写一条新的）

`tests/test_panel_end_date_single_point.py::test_the_frozen_list_matches_what_is_actually_in_the_tree`
**已经**在盯这件事：它扫 `files[0]` 的所有站点，与 `_KNOWN_END_FROM_FILES0` 冻结名单比对，
任何增删都会让它红，并要求「当场判断」。

2026-10-16 那次门禁红**正是它**要求把 `forward_validate.py` 移出名单——
不是它坏了，是我修了实现而没更新登记。⇒ **修完之后必须回来更新名单**，
否则会读成「守卫误报」而把实现改回去（第 112 层通则）。

⚠️ 该守卫自己的注释记录过一次事故（第 117 行）：
它的正则**匹配的是参数**（`files[0]`），不是整条调用；
第一版因此扫出 **0 处**，差点把 16 处真实站点**从记录里删掉**，再冻一份空名单当真相。
⇒ **「扫出 0 处」时先怀疑探针，别急着相信它**（第 163 层）。

## 与常设目标的关系

- 前瞻验证是当前**唯一**没被「数据不足 2.6 倍」堵死的出路
  （见 `CLAUDE.md` 现状：毛超额上限远低于 20% 门槛）。
- 那条出路上的 `files[0]` 2026-10-16 已修（本文件的起点）。
- 剩下 13 处喂的是 `evolution_ledger.json` / `placebo_control.json` 等**进化账本**，
  它们坏掉不会让年化数字变好或变坏，但会让**账本记录的时间区间**错位
  ⇒ 进化循环可能在「用错窗口的账本」继续往前跑。

## 触发条件（什么时候该动手）

不是「有空的时候」，而是**下面任一条成立**：

1. `000001.SZ` 停牌/退市/数据更新中断 ⇒ 该债立刻从「不改变结论」变成「静默停摆」。
2. 要拿这 13 个脚本的产物下**判决**（而不只是记账）。
3. `evolution_ledger.json` 的时间区间开始与主库 `latest` 对不上。

## 教训归属

MISTAKE_LOG 第 165 层记的是**审计动作空转**那一族；
本条属于它的**上游**：守卫早就在盯（`test_panel_end_date_single_point.py`），
债务早就在册（`_KNOWN_END_FROM_FILES0`），**只是没人做那次「当场判断」**。
⇒ 通则：**登记了的债不叫已知风险，叫待办事项**；
守卫为你守着它，不等于它在被修。

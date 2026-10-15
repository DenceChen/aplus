# TODO：vintage 采集被两个 launchd 调度器重复触发（2026-10-15 实测登记）

**状态**：⬜ 未修。`scripts/guard_vintage.py` 的**后果**已在本轮修掉（超时不再让守卫变空操作），
**病因**（重复采集）留到单独一轮，因为它要改 launchd 调度。

---

## 1. 实测证据（全部来自产物与系统日志，不是推理）

| 量 | 值 | 出处 |
|---|---|---|
| 采集单跑实测耗时 | **68.3 分钟** | `data/vintage.log` 收尾行「2026-10-15: 65391 行 / 5460 只 / 141 列 / 耗时 68.3 分钟，失败 0 只」 |
| 守卫旧超时 | **90 分钟**（`timeout=5400`） | `scripts/guard_vintage.py` 旧字面量 |
| ⇒ 余量 | **21.7 分钟** | 实测减法 |
| 守卫超时时刻 | 11:00（09:30 起跑，恰好 +90 分钟） | `data/guard_vintage.err.log` 的 `TimeoutExpired ... after 5400 seconds` |
| 同日另一次采集 | 11:08 正常收尾（68.3 分钟，起于 ≈09:59） | `data/vintage.log` |

**两个调度器**（`~/Library/LaunchAgents/`）：

| launchd 项 | 时刻 | ProgramArguments |
|---|---|---|
| `com.aplus.guardvintage` | **9:30** | `scripts/guard_vintage.py` → **内部自己再起一次** `collect_financial_vintage.py --ensure` |
| `com.aplus.vintage` | **10:00** | `scripts/collect_financial_vintage.py --ensure` |

## 2. 机制：`--ensure` 是「查了再动」，而窗口长达 68 分钟

`collect_financial_vintage.py:341-344`：

```python
if args.ensure:
    ...
    print(f"{day} 已登记（{e['n_rows']} 行），跳过。")
```

**没有锁、没有占位、没有原子 claim。**
⇒ 09:30 的进程查完说「未登记」→ 开始干；
   10:00 的进程也查完说「未登记」→ 也开始干。
⇒ **两次都在采同一份数据，写同一个目录。**

## 3. 病根是一句**文档里的话**（本轮已改）

`guard_vintage.py` 旧 docstring 原话：

> 采集器自带 `--ensure`（重复触发是空操作），
> 所以守卫「今天已登记就跳过」的判据让一天被触发两次也只付一次网络往返。

★ **这句话里文档与代码完全一致**（代码确实只在「已登记」时跳过）——
**两者一起错在世界上**：`--ensure` 只能挡住「上一次已经跑完」的重复触发，
挡不住 68 分钟内的重复。

⇒ 形状与**第 128 层同族**：那里是「help 说的是快照，实现取的是一只票」；
这里是「文档说的是『已登记』，现实里差着 68 分钟」。

⚠️ **两处照抄了这句假话的东西**（本轮已一并更正数字，但断言形态未动）：

| 位置 | 抄了什么 |
|---|---|
| `tests/test_guard_vintage.py::test_guard_main_does_not_rerun_when_today_is_registered` 的 docstring | 「一天补跑两次……把机器占满 **45 分钟**」—— 数字已过期（实测 68.3），且**方向被当成了缓解手段** |
| `tests/test_guard_vintage.py::test_guard_main_reruns_when_today_is_missing` | `assert "--ensure" in calls[0][0]`，失败消息「否则重复触发会白跑 45 分钟」 |

⇒ **它们钉住的是「实参里传了 `--ensure`」这个形状，
而 `--ensure` 挡不挡得住并发是另一个量**（第 119 层同族：
断言的对象不是被诊断的量）。⇒ 本轮补的真守卫见
`tests/test_guard_vintage.py` 的「超时三条」，它们量的是**产物**，不是实参。

## 4. 候选修法（**都还没做**，先量后决）

| # | 修法 | 代价 / 风险 |
|---|---|---|
| A | 在 `collect_financial_vintage.py --ensure` 顶部加 **原子 claim**（`O_CREAT\|O_EXCL` 的锁文件），已有实例在跑就退出 | 两个调度器都被覆盖（锁在采集器里）。⚠️ **主风险**：进程崩了留下死锁 ⇒ 那天永远采不到，而源站过了当天就取不回 ⇒ **永久损失**。必须配「锁主人的 pid + 起始时间」并允许回收已死主人的锁（第 115 层：pid 是可复用资源，单一证据不够） |
| B | **把守卫改到采集之后**（如 12:00），让它只在今天仍未登记时兜底 | 最省事，且让守卫的判据**第一次真的有信息量**（9:30 跑时它永远只能看到「未登记」）。⚠️ 要改 launchd plist 并重载；且机器睡过 10:00 时守卫才真正补位——这正是它存在的理由，**没有削弱** |
| C | 删掉守卫的自启动，只留 `com.aplus.vintage` | ⚠️ **不可取**：守卫存在的唯一理由就是「launchd 日历触发不补跑，机器睡着就永久漏采」（它自己 docstring 第 13–17 行）。删掉就把**不可逆损失**的兜底删了 |

⇒ **倾向 B**，A 作为后续加固。**理由**：B 消掉竞态本身（两个启动点变成「先采集、后检查」），
A 只是让第二次变便宜——**第二次仍然会浪费一次网络往返和一个 slot**。
⚠️ **两者不互斥**：B 做完之后 A 仍值得做，因为 `com.aplus.vintage` 自己被重复触发
（launchd 撞钟、运维手动重跑）时仍然会撞上。

## 5. 为什么不能靠「把超时调大」了事

本轮已经把超时从字面量 `5400` 改成 `3 × 实测`（见 `guard_vintage.py::COLLECT_TIMEOUT_S`），
但那是**兜底不是修法**：
它只保证「双实例时守卫不超时」，**不减少任何一次多余的网络往返**。
⇒ 超时调大是「让症状不发作」，病因照旧。
`COLLECT_TIMEOUT_S` 的注释里已经写明这一点。

## 6. 本轮顺带修掉的（与本 TODO 是两件事）

`scripts/guard_vintage.py::main()` 里 `subprocess.TimeoutExpired` **未被捕获**：

```
subprocess.TimeoutExpired: Command '[... collect_financial_vintage.py --ensure]'
timed out after 5400 seconds
```

⇒ 抛出 ⇒ 后面的 `span(root)` / `days_missed(root)` / 缺口播报 **一行都没跑**
⇒ **守卫在超时那天整个变成空操作**，唯一证据是一段没人读的 traceback。
（launchd 因此记退出 1 ⇒ `com.aplus.guardvintage` 在 `launchctl list` 里显示 `1`。）

★ 「**守卫存在**」与「**守卫今天跑了**」不是同一件事——
与 `data/run_daily.log` 停更 15 小时而 launchd 退出码为 0 是同一个形状。

⇒ 现改为：捕获超时 → **按产物重判**（`registered_days` 复查）→ **无论如何都继续跑覆盖报告**。
守卫的职责是**报覆盖**，不是**报采集成功**；后者只是它顺带做的事。

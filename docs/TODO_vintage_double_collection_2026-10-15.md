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

---

## 7. `n_source_dup_*` 字段：三层的复核（**2026-10-15 补记**）

> ⚠️ **本节不是纯预注册**：写下它之前，我已经读过盘上 `_manifest.jsonl` 的三条条目与
> `aplus/vintage.py` / `collect_financial_vintage.py` 的源码。
> ⇒ 按第 91 层，这里**只能算「补记于看过产物之后」**，
> **不许**冒充事前登记。仍然有效的部分只有 §7.4 的判据 —— 那是在动手写守卫之前定的。

### 7.1 上一轮的记账被推翻：这两个字段**不是**语义欠账

本 TODO 起草时记的是「字段名承诺『重复了几只/几行』，而实际只能承载
『登记成功即证明源站干净』⇒ 名字的承诺大于测量范围」。**复核后判定：不成立。**

| 层 | 事实（逐条实测） | 判定 |
|---|---|---|
| 代码 | `collect_financial_vintage.py:422-430` 的 `return 2` 在 `write_snapshot`（463 行）**上游** ⇒ 非零结构上不可达 | ✅ **刻意**，且 **455-462 行已把理由写明**（登记它是为了让「当天源站干净」成为**账本里的陈述**，而不是 stdout 里的一句承诺） |
| 产物 | 盘上 **3 条** manifest，`n_dup_dropped` 与两个 `n_source_dup_*` **全是键缺席**（不是 `0`） | ✅ 正是 `write_snapshot` docstring 103-114 行要求的区分：「那天还没量」≠「量了、确实为 0」 |
| 语义 | `write_snapshot` 的 docstring **已经**讨论 `None` ⇒ 缺席，并写着「**一个恒为 0 的字段比没有字段更坏**」 | ✅ 名字与语义都诚实 |

⇒ **字段不是恒真装饰**：它在盘上的**缺席**恰好携带信息（「这道检查那天还不存在」），
而它一旦**在**，那个 `0` 就是真的「量过、是 0」。
`write_snapshot` docstring 自己守的正是这两端。

⇒ **上一轮的记账属于「断言对、成因错」**：被「名字承诺 > 测量范围」这个读法
推着去改本来正确的东西。
⇒ 顺带一条本仓通则的又一次印证：**要改一个看着可疑的地方之前，
先把注释和 docstring 读完** —— 那两段**恰好就是**对它的完整论证。

### 7.2 真缺口：那条让字段恒为 0 的**结构不变量，零守卫**

`tests/test_collect_financial_vintage.py::test_main_refuses_to_register_a_manifest_when_the_source_duplicates`
（222-227 行）：

```python
src = inspect.getsource(m.main)
assert 'res["source_dup"]' in src
assert "return 2" in src
```

**它的名字承诺「refuses to register a manifest」，而身体从不检查 manifest
有没有被写。** ⇒ 按第 13 种形状，一句把这两个串抄进去的**注释**就能让它绿
⇒ 「**登记成功 ⇒ `source_dup` 为空**」这条不变量——**整个字段恒为 0 的唯一理由**——
是**散文**（455-462 行）而不是守卫（形状 22：代码里那句话说了，代码本身不会验证）。

⇒ ⚠️ **失效方向是「删字段」**：有人看到「这个数永远是 0」而把它清掉，
理由听着完全合理，而后果是**盘上「那天还没量」与「量了是 0」这两端重新合并** ——
正是 docstring 109-114 行花了一整段去防的那件事，**且没有任何东西会红**。

#### ✅ 已修（2026-10-15）：子串守卫被**替换**成 4 条行为层守卫

`tests/test_collect_financial_vintage.py` 里那条 `inspect.getsource(m.main)`
子串断言**已整条删除**，换成 §7.4 预注册的 G1–G3
（`_dup_source` / `_run_main` / `_source_dup_violations` 三个夹具函数 +
`test_main_registers_nothing_when_the_source_duplicates` /
`test_a_clean_source_does_register_exactly_one_entry` /
`test_the_registered_source_dup_counts_are_never_nonzero` /
`test_the_source_dup_probe_can_recognize_a_nonzero_value`），
G4 扩到三个键落在 `tests/test_vintage_dup_accounting.py` ⑤。

**变异台实测 3/3 全杀（基线 25 passed 已验绿，每条 `rc=1`）**，
规格 `tests/mutation_specs/test_source_dup_gate.mut`。
⚠️ 归因更正见 §7.4 末尾 —— **M3 不是 G3 杀的**，G3 对当前代码零覆盖。

⚠️ **跑 G1/G2 需要真的把 `main()` 跑完**，前置条件四条全部实测过：
① 不带 `--sample`/`--max_codes`（否则 `partial=True` ⇒ 提前 `return 0`）；
② 补丁后的 `_fetch` 返回非空帧；③ 覆盖 `universe() − skip` **全池**
（否则 `return 3`）；④ `MIN_FREE_GB` patch 成 0（它在 `argparse` **之前**，
是 `return 4` 那条闸）。
⚠️ `rc == 2` 是**合法**的行为层判据而不是「另一个量」：
`grep -c "return 2$"` 实测**全文件唯一**（430 行，在 source_dup 闸内），
其余闸门是 `1`/`3`/`4` ⇒ 「rc 是 2」与「源站重复」构成**双射**。

### 7.3 外部锚的口径更正（石川 §6，**不能**当本表的锚）

`data/books/cn_factor_lines.txt:5018-5030` 讲的是：

> 「无论是 **Wind 还是 Tushare**，**三大表**在处理调整和更正时都采用相同的步骤。
> 以**大智慧（601519.SH）2014 年年报合并资产负债表**为例……这个例子表明
> **底层表中同一个报告期可能会有多条记录**，分属于以下四类……」

⇒ ⚠️ **口径错配**：那说的是 Wind/Tushare 三大表的**存储层四类记录**
（首次披露/调整/更正），**不是本采集器读的东财财务指标表**。
⇒ 它**只能**支撑「**同一报告期多行不是天然异常**」这一条弱结论；
**不能**支撑「本表会给出版本对（修订行）」—— 本模块实测结论**相反**
（`aplus/vintage.py` 模块 docstring 逐字写着「每个报告期只存一行」「**没有版本维度**」，
全文 847 字符里「重复」出现 **0 次**）。
⇒ 同段里「1998 年之前每个报告期只有一条记录」**同样不适用于本表**，不得引用。

### 7.4 预注册的判据（本节唯一事前定死的部分）

**要加的守卫**：源站重复时，`main()` **一条 manifest 都不许追加**，且 `rc == 2`。
**必须由行为证明，不许用源码子串。**

| # | 判据（事前定死） | 理由 |
|---|---|---|
| G1 | **行为层**：源站重复 ⇒ `main()` 返回 2 **且** `_manifest.jsonl` 行数**逐条不变** | 子串断言能被注释满足（第 13 种形状）；只有真跑一次才能证明「没登记」 |
| G2 | **反控（防恒真）**：源站干净 ⇒ **确实**追加了一条，且两字段**逐位等于 0** | 没有反控的话，「行数没变」在**采集器压根没跑**时同样成立 —— 那是空输出，不是判决（第 130 层） |
| G3 | **不变量的正面表述**：盘上**任何**一条含这两个键的条目，其值必须为 0 | 把「登记 ⇒ 为 0」从散文变成**逐条检查**，且它对未来任何新增条目都成立 |
| G4 | **不许回落**：读侧禁止 `.get("n_source_dup_codes", 0)` 一类缺档回落 | 回落会把 7.1 表里的「键缺席」重新变成「量了是 0」（`test_vintage_dup_accounting.py:271` 已覆盖 `n_dup_dropped`，本条补 source_dup 两键） |

**预期杀手**（写变异时按此核对，防「变异体写错」被读成「守卫有洞」）：

| 变异 | 预期被谁杀 |
|---|---|
| M1 把 `return 2` 整块摘掉（源站重复也继续登记） | G1 |
| M2 `return 2` 改成 `return 0` | G1 |
| M3 `write_snapshot(...)` 的 `source_dup=` 实参摘掉 | ~~G3（键变缺席 ⇒ 不再是 0）~~ **⇒ 实测是 G2，归因错了，见下方更正** |

⚠️ **只有 `rc == 1` 才算被杀**；`rc ∈ {2,3,4,5}` = 收集/用法/内部错误 ⇒ **不可验证**，
按存活计入上界。
⚠️ **跑变异后先 `grep` 确认改动落进文件**再看 pytest 结果（第 14/148 层）。

#### 🚨 归因更正（**2026-10-15 实测，判据本身一个字未改**）

上表 M3 那行的归因**是错的**：实测杀掉 M3 的是 **G2**（当场跑 `main()`、
检查**新写入**那条条目的两个字段），**不是 G3**。

| | 归因 | 实测 |
|---|---|---|
| M1 | G1 | ✅ G1（`…source_duplicates`） |
| M2 | G1 | ✅ G1（同上） |
| M3 | ~~G3~~ | ❌ **G3 结构上抓不到**；真凶是 **G2**，外加**既有**守卫 `test_the_collector_hands_the_counts_it_measured_to_the_writer`（在 `tests/test_vintage_dup_accounting.py`） |

**实测台子输出**（`tests/mutation_specs/test_source_dup_gate.mut`，基线 25 passed 已验绿）：

```
✅ M1 …（期望 killed）
     rc=1 / 1 failed, 24 passed
     变红：test_main_registers_nothing_when_the_source_duplicates
✅ M2 …（期望 killed）
     rc=1 / 1 failed, 24 passed
     变红：test_main_registers_nothing_when_the_source_duplicates
✅ M3 …（期望 killed）
     rc=1 / 1 failed, 24 passed
     变红：test_a_clean_source_does_register_exactly_one_entry
变异台：3/3 条符合预期（基线已验绿）
```

**G3 为什么结构上抓不到 M3（这是它值钱的地方，别读成「G3 没用」）**：
G3 读的是 `m.VINTAGE_ROOT` 下**已经落盘**的 `_manifest.jsonl` ——
那是**上一次跑**写下的字节。改源码**不会重写它**。
⇒ 这是 [[snapshot-guards-guard-nothing]]（第 118 层）的教科书实例：
**读落盘快照的守卫对当前代码零覆盖**。
⇒ G3 的职责本来就**不是**「抓这一行源码被改了」，而是
「盘上任何一条**已登记**的条目，它的计数必须为 0」——
那是**未来任何一天新增条目**的守卫，对**当天新跑的代码**零覆盖。
它没做错任何事，只是**被错记成了 M3 的杀手**。

**为什么这条更正必须写下来而不能只默默修表**（第 91 层）：
判据（G1–G4 四条）**一个字都没改**，改的只是「谁杀谁」。
但**错误归因比没有归因更贵**——没有归因是空白，
错误归因会让人**以为 G3 有牙**：下次谁摘掉 `source_dup=` 实参时，
他会以为「G3 会拦」，而它不会 ⇒ 只有 G2 与那条既有守卫在拦，
而**没人知道该指望它们**。

⚠️ 附带一条本轮实测的台子口径坑（**与判据无关，是规格写法**）：
本 spec 第一次跑时 M1/M2 报 `旧串匹配 0 处（必须恰好 1）⇒ 空操作，绿灯是假的`——
**台子没骗人，错的是规格**：`.mut` 多行锚点首行缩进了 4 格，
而 `load_specs` 的 `textwrap.dedent` 取**块内最小缩进** ⇒ 续行被一起减掉 4 格。
⇒ 修法（第 122 层）：**首行写到列 0**（子串匹配仍命中），续行保持源码真实缩进。
⇒ ★ **「变异体写错」与「守卫有洞」在终端上输出逐位相同**，
所以判据永远是同一套三步：`shasum -c` 复验还原 → 看 `rc` 是否 `== 1`
→ 再问「我改的是哪一行」。

### 7.5 明确**不做**

- ❌ **不改**字段名、不挪闸门位置、不改 `write_snapshot` 的签名或 docstring
  —— 7.1 已判定它们是对的。
- ❌ **不删**那两个字段（理由见 7.2 的失效方向：删掉就把「没量」与「量了是 0」合并回去）。
- ❌ **不把石川 §6 当成本表的外部锚**（7.3）。

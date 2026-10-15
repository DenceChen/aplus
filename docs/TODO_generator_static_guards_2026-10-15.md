# TODO：生成器静态守卫与三处仍开着的缺口（2026-10-15）

**状态**：守卫已落地并变异验过（`tests/test_generator_names_resolve.py`，8 条，
变异 7/7 全杀，规格 `tests/mutation_specs/test_generator_names_resolve.mut`）。
**本文档登记的是守卫没能覆盖、或修守卫时**刻意没有**当场拍板的三处。**

---

## 1. `freshness()` 传裸名时给出**两条都是假的**理由（本轮新发现，未修）

### 实测

```
  verdict_composite                    → unknown  problems=['没有溯源 sidecar——生成它的 commit 无从得知',
                                                     '不在 DEFAULT_GENERATORS 里，不登记来源就无法判定…']
  data/reports/verdict_composite.json  → fresh    problems=None
```

**同一份报告，两种拼法，判决相反。** 而那个 `unknown` 附带的两条理由**都是假的**：
sidecar 确实存在（`data/reports/verdict_composite.json.provenance.json`，
338 字节），也**确实**在 `DEFAULT_GENERATORS` 里。

### 机制

`aplus/provenance.py`：

```python
SIDECAR_SUFFIX = ".provenance.json"
def sidecar_path(report_path):
    p = Path(report_path)
    return p.with_name(p.name + SIDECAR_SUFFIX)      # ← 拼在**完整文件名**后面
```

⇒ 传 `"verdict_composite"` 去找的是 `verdict_composite.provenance.json`，
而真实文件名是 `verdict_composite**.json**.provenance.json`（后缀拼在 `.json` 之后）。

### 为什么它贵

**方向是保守的**（`unknown` = 不可引用，不像第 123 层那次会让人丢弃新鲜产物），
但它附带的建议是**可执行的错误建议**：「去 `DEFAULT_GENERATORS` 补一行」——
照做就会在那个 dict 字面量里**加一个重复键**，重复键会被静默覆盖，
而仓里恰好有一条守卫 `test_default_generators_has_no_duplicate_keys` 在防它。

⇒ **照着工具的假建议修一次，就制造一个新缺陷。**

### 修法（两案，**都还没做**）

| # | 修法 | 风险 |
|---|---|---|
| A | `freshness()` / `read_stamp()` 先规范化：路径无后缀则补 `.json`、无目录则落到 `data/reports/` | ⚠️ 扩大了接受面 ⇒ 必须先量清仓里有没有**故意**传裸名的调用点（当前 `tests/` 里全部传真路径，已核） |
| B | 传裸名时**明确拒绝**（抛异常或给出「你是不是想写 `data/reports/x.json`」的提示），绝不静默猜 | 「报错胜过静默错答」，与 `read_stamp` 现有规矩一致（取不到就返回 `None`，**不做降级猜测**） |

⇒ **倾向 B**：它不扩大接受面，与本模块既有哲学一致，而且这条函数的职责是
**判定产物能不能引用**——在这种职责上「猜」比「报错」危险得多。
⚠️ 但 B 会让**任何**传裸名的调用从「静默降级」变成「响亮报错」，
先跑一遍全仓调用点清单再动手。

### 与本轮新守卫的关系

`test_generator_names_resolve.py` **抓不到这一类**——它管的是**未定义名**，
而这里是「名字定义得好好的，只是被当成了另一种形状」。
形态不同（不是 `NameError`，是「查错了路径然后把查不到说成没有」），
但**同族**：查不到 ≠ 没有。

### §1.1 ★ 第三种情形：**产物根本不存在**，也报成同一个 `unknown`（2026-10-15 12:2x 实测）

§1 讲的是「路径传错」。今天顺着它多量了一步，发现还有第三种：

```
真实产物 + 完整路径          → fresh    '生成器字节与盖章时完全一致；…'
真实产物 + 裸文件名          → unknown  '没有溯源 sidecar——生成它的 commit 无从得知'
**根本不存在**的产物        → unknown  '没有溯源 sidecar——生成它的 commit 无从得知；不在 DEFAULT_GENERATORS 里…'
路径拼错（reports→report）  → unknown  '没有溯源 sidecar——生成它的 commit 无从得知'
```

⇒ **四种输入、两个判决词，而后三个的理由句是逐字相同的。**

⚠️ 危害方向和 §1 **不同**，因此不能靠 §1 的「方向保守」一笔带过：

| 情形 | 真相 | `unknown` 的含义被读成 | 后果 |
|---|---|---|---|
| 路径拼错 | 产物其实 **fresh** | 「产物坏了」 | 去重跑一个 45 分钟的活，**白跑** |
| 产物根本不存在 | **产物缺失** | 「产物在，但不许引用」 | 去找一个不存在的文件，或以为重跑没生效 |
| 真的没盖章 | 不许引用 | 不许引用 | ✔ 唯一说对的 |

★ 「产物缺失」被报成「产物在、但不可引用」，是**方向相反**的静默失效：
不是把好产物丢掉，而是**让人去找一个从来不存在的东西**。

⇒ 它与第 123 层是同一句话的另一个方向：**「查不到」不等于「没有」**，
而 `unknown` 这个词**没有位置**分别表达「产物不在」与「产物在但没章」。
⇒ 修法仍倾向 **B（响亮拒绝）**，但要多一条：**产物路径不存在时**
  必须报「产物缺失」而不是「没有 sidecar」——现在这句话对着一份
  从没生成过的产物说「生成它的 commit 无从得知」，**是在陈述一件不成立的事**。

⚠️ 本条**未修**：`aplus/provenance.py` 在链的盖章路径上（第 125 层冻结），
等 `placebo_control` / `spread_tilted_arm` 落盘后再动。

### §1.2 🚨 最糟的一种：**在仓外调用，21/21 份产物一律塌成 `unknown`**（2026-10-15 12:4x 实测）

§1/§1.1 都是「**传错参数**」。今天量到的是第三类：**参数完全正确，判决却随 cwd 变**。

```python
freshness(Path("<abs>/data/reports/placebo_control.json"))
# cwd = 仓库根 → fresh   「生成器字节与盖章时完全一致；…」
# cwd = /tmp     → unknown 「生成器路径 `scripts/placebo_control.py` 在仓里**不存在**
#                          ⇒ 判据本身没跑」
```

**实测波及面：21 份 sidecar，从 `/tmp` 调用时 21/21 判 `unknown`。**

| | 真相 | `unknown` 的含义被读成 | 后果 |
|---|---|---|---|
| 仓外调用 `freshness()` | 产物**fresh**，生成器字节**逐位一致** | 「判据本身没跑」 | 去重跑一个 45 分钟的活，**白跑**；或弃用一份完好产物 |

★ 这比 §1.1 更糟，因为**误报指向「我根本没验」**——
安全判据谎报「没跑」，比谎报「跑过」更危险。

**机制**：sidecar 里存的是**仓库相对**的 `generator`（`scripts/placebo_control.py`），
而 `freshness()` 用它拼路径时按 **cwd** 解析 ⇒ 换目录就等于换了一条判据。

⚠️ 与**形状 24** 同源但更隐蔽：形状 24 是「按路径导入比按脚本运行多给了它一样东西」，
这里是「同一个函数、同一组参数，**只有 cwd 不同**」。
⇒ 我写 `audit_placebo_rerun.py` 时明确要求它**换个 cwd 也能跑**，
  而这条设计恰好撞出了这个缺陷——**这正是那条设计该起的作用**。

⇒ 修法：`freshness()` 内部一律以**仓库根**为基准解析 `generator`
  （`DEFAULT_ROOT` 或从报告绝对路径回推），**永远不看 cwd**。

**✅ 已修（2026-10-15，第 125 层冻结解除后）**

不是只修 `freshness()`——本模块里**七个**签名都有同一个默认值 `root="."`：

| 函数 | 它拿 `root` 干什么 | 仓外调用的后果 |
|---|---|---|
| `freshness()` | `root/gen` 判存在、`code_file_sha256`、`commits_touching` | 生成器「不存在」⇒ 21/21 `unknown` |
| `scan()` / `summary()` | `reports_dir="data/reports"` | 扫到**空目录** ⇒ `[]` ⇒ 读成「零问题」（第 89 层同族） |
| `stamp()` | `code_sha(root)`、`is_dirty(root)`、`code_file_sha256` | `code_sha` 返回 `None` ⇒ **章里写 `code_sha: null`** |
| `data_freshness()` / `data_fingerprint()` | `root/data_dir` | 同样塌成 `unknown` |
| `resolve_data_dir()` / `commits_touching()` / `code_sha()` / `is_dirty()` / `code_file_sha256()` | `git -C root` / `root/gen` | 同样错位 |

⇒ 统一加 `REPO_ROOT = Path(__file__).resolve().parents[1]` 与 `_root(root)`，
  **默认值一律是 `None`**，只有**显式传入**才用别的目录。

**验收（修复前 → 修复后，两个方向都量了）**

```
修复前  cwd=仓内 → {fresh: 14, stale: 7}
修复前  cwd=/tmp  → {unknown: 21}          ← §1.2 实测的那 21/21
修复后  cwd=仓内 → {fresh: 14, stale: 7}
修复后  cwd=/tmp  → {fresh: 14, stale: 7}  ← 与仓内**逐份相同**
```

⇒ 三个新守卫（`tests/test_provenance.py`）全部变异验过：
把 `_root(None)` 退回 `Path(".")`（= 旧行为）⇒ **3 failed**；
`aplus/provenance.py` 按 sha256 逐字节还原。

⚠️ **本条不涉及 §1.3 的三条修法**：`is_dirty()` 的**作用域**一个字没动
（仍然是整个仓库）。这里改的只是「在哪个目录跑 `git status`」，
而那一个方向是**保守的**——cwd 不在仓库时它以前返回 `False`（=「干净」，
正是 `test_audit_core_arch` D3 那个假警报），现在返回真值。
⇒ 只可能把「假的干净」变成「真的脏」，不可能反过来。

### §1.4 ★ 修的过程中，第一版守卫**比较的是两个同样错误的答案**（2026-10-15）

第一条守卫写完是绿的，变异下**没红**。查下去：

```python
names = [p.name[:-len(".provenance.json")] ...]      # ← 裸文件名
inside  = {n: P.freshness(n)          for n in names}   # cwd = 仓根
outside = {n: P.freshness(n)          for n in names}   # cwd = /tmp
assert inside == outside
```

`freshness("placebo_control.json")` 在**两个** cwd 下都**找不到那个文件**
⇒ 两边都是 `unknown` ⇒ **相等恒成立**。
⇒ 它守的不是「判决与 cwd 无关」，是「两个空集合相等」。

修法两条，缺一不可：

1. **传绝对路径**（`p.with_name(...)`），让两个 cwd 差**唯一**那个变量；
2. **加正控前置断言**：仓内判决里至少要有一个不是 `unknown`，
   否则测试自己先失败，而不是「绿着通过」。

⇒ 这是第 87 层「零假设臂 + 正控臂」在**比较型守卫**上的形态：
  断言 `A == B` 时必须同时钉住 **A 不是空**，
  否则 `A == B` 会在 `A = B = 什么都没有` 下成立。
  与第 136 层同源（守卫不变 ≠ 守卫没洞），但触发机制不同：
  136 是**对称变异**免疫，本条是**比较基退化**。
  ⇒ 所以**新开一层不划算**：两条通则已经覆盖，登记在此处即可。

### §1.3 🚨 盖章顺序让 `code_dirty` **必然**为 True ⇒「完整字节证据」结构上不可满足（2026-10-15 12:4x 实测）

`placebo_control` 跑完那刻量到的：sidecar `code_dirty = True`，而依据句是

```
「生成器字节与盖章时完全一致；但盖章时工作区有未提交改动
  ⇒ 只能证明生成器本身，不能证明整个依赖闭包」
```

**这不是偶发**——是流程锁死的。三行代码决定了它：

| 位置 | 做什么 |
|---|---|
| `scripts/placebo_control.py:679` | `args.out.write_text(...)` **先落盘**产物 |
| `scripts/placebo_control.py:684` | 之后才 `provenance.stamp(...)` |
| `aplus/provenance.py:292` | `is_dirty` = `git status --porcelain`（**整个仓库**，含 `data/reports/`） |

⇒ 产物自己那次未提交的写入，**必然**出现在 `git status` 里
⇒ `code_dirty` **必然**为 True ⇒ `freshness()` 的依据**必然**带上那句限定。

⚠️ **唯一例外**：重跑产物与 HEAD **逐字节相同**（`git status` 才干净）——
  而那正好是「这次重跑毫无意义」的情形。

⇒ 所以 `TODO_placebo_control` §0.1 那条「将是第一份带字节证据的 placebo 产物」
  在当前盖章顺序下**结构上无法兑现**，不是「这次没跑好」。

**三条可能的修法，各有代价，都不许顺手做**：

| 修法 | 代价 / 陷阱 |
|---|---|
| 改成**先盖章后落盘** | 盖章时产物还不存在 ⇒ 章记的哈希与字节无关，`code_file_sha256` 失去意义 |
| 把 `is_dirty` **收窄到代码目录** | ⚠️ **看起来像修语义 bug，效果却是让一条红变绿** —— 这正是第 129 层的定义域 |
| 落盘后**先提交再补盖章** | 需要在流程里插一次提交，且章的 `code_sha` 会指向提交之后的那棵树 |

⇒ 🚨 **本条不修，只登记**：`aplus/provenance.py` 在链的盖章路径上（第 125 层冻结）。
⇒ ⚠️ 特别提醒第二条路：它最像「显然的正确修复」，
  **正因为如此**它最危险——**改判据让它变绿，是本仓最常见的自欺形态**。

**★ 可证伪的预测（写下时链④还没落盘）**——
链④ `spread_tilted_arm` 与 ③ 用的是**同一个** `stamp()`、**同一个** `is_dirty()`，
而且我已在 ③ 落盘后把工作区**提交干净**（`a561fe8`）⇒ 落盘时全仓除它自己那次写入之外是干净的。

⇒ **预测：`spread_tilted_arm.json.provenance.json` 的 `code_dirty` 也是 `True`。**
⇒ 若它是 `False`，那说明「产物自己的写入」并不是唯一触发条件（或 `is_dirty` 另有语义），
  ⇒ **本节的结构性判断就是错的**，先查机制，**不许**直接改判据（第 129 层）。

**✅ 预测兑现（2026-10-15 12:48:41 链④落盘后核对）**

```
④ spread_tilted_arm rc=0 12:48:41  code_sha=1f70c660
   code_dirty = True   stamped_at = 2026-10-15T04:48:40+00:00
   generator  = scripts/spread_tilted_arm.py
③ placebo_control       code_dirty = True   （对照）
```

★ 它不是「同一个函数跑两次得到同一个答案」那么弱：

| | ③ | ④ |
|---|---|---|
| 生成器 | `scripts/placebo_control.py` | `scripts/spread_tilted_arm.py`（**另一个文件**） |
| 落盘前工作区 | 有未提交改动 | **已提交干净**（`a561fe8` / `1f70c66`） |
| `code_dirty` | True | **True** |

⇒ 换生成器、换干净度，两次都红 ⇒ **触发条件不是「恰好有别的脏文件」，
而是盖章动作自己**。§1.3 的结构性判断成立，
「第一份带完整字节证据的 placebo 产物」在现行盖章顺序下**确实兑现不了**。

⚠️ 反过来说：如果哪天它变成 `False`，那**不是**「缺陷修好了」的证据，
先查是不是 `is_dirty` 的作用域被收窄过——那正是本节列的三条修法里最危险的那条
（**改判据让它变绿** = 第 129 层）。


## 2. `lowfreq_plausibility.json` 没有 sidecar ⇒ 按本仓规矩**不可引用**

实测：文件在（10-14 13:50，2089 字节），`data/reports/lowfreq_plausibility.json.provenance.json`
**不存在**。

⇒ 按 `freshness()` 的规矩，**没有 sidecar = `unknown` = 不许引用**。
这不是 bug，是规矩在正常履行职责；但要清楚：**这份产物现在引用不了**。

它的生成器本轮还中了第四只雷（`ROOT` / `PC` 从未绑定 ⇒ `main()` 跑不起来），
已修（`scripts/lowfreq_plausibility.py`），但**产物本身仍是旧字节跑出来的**。

⇒ 要让这份产物可引用，必须**用修好的字节重跑一次**。未做。

## 3. `tests/test_live_backtest_parity.py` 删掉了一条恒真断言，**没补新的**

原句：

```python
assert "ivol" in legacy.columns or legacy["ivol"].isna().all() or True
```

末项 `or True` ⇒ 它**从来不判任何东西**，而在文件里长得和旁边两条真断言一模一样。
被 `test_no_guard_in_tests_asserts_a_constant_truth` 逮到。

本轮**只删不补**。理由：补一条新判据需要一个**从实测量推导的门槛并预注册**，
而「参照实现允不允许缺 `ivol`」是个**尚未测过**的问题 ——
当场拍一个门槛就是「看到数据前写字面量」，正是本仓最贵的错。

⇒ **缺口**：实盘 `build_cross_section` 与参照实现 `cross_rows_from_map`
在共有列上是否逐位一致（实测 ivol 差 4%~21%，所以这件事本身有判据价值），
需要先测量再预注册门槛。

---

## 4. 本仓 shell 不入 git，所以 lint 扫的是「盘上实际存在的」

`tests/test_generator_names_resolve.py` 的两条 shell 判据扫 `ROOT.rglob("*.sh")`
（排除 `.git` / `.venv` / `node_modules`），而本仓的 shell 全在 `.testtmp/`——
**重跑链、变异脚本**，它们钉的是当天的 PID 与窗口，不适合进版本库。

⇒ 干净克隆上这个集合是空的。**因此那两条不设「扫到几个文件」的下限**，
改用**正控**（`test_the_shell_scanner_can_actually_see_a_swallowed_variable`）
证明扫描器有眼睛——这样即使集合为空，守卫也不是装饰。

### §1.5 ★ 预注册：`freshness()` 必须区分**三种输入**，而不是两种判决词（2026-10-15）

**写下时 §1.2 已修、§1/§1.1 未修。门槛与理由在下，代码在下。**

#### 现状：四种输入、两个判决词

实测（`tests/test_provenance.py:115/127` 的两个路径）：

| 输入 | 真相 | 报出来的 |
|---|---|---|
| `data/reports/x.json`（真产物、真章） | fresh | `fresh` ✔ |
| `"verdict_composite"`（**裸名**） | 产物是 **fresh** | `unknown`：「没有溯源 sidecar」+「不在 DEFAULT_GENERATORS 里」——**两条都假** |
| `/tmp/x/unmapped_report.json`（**不存在**） | **产物缺失** | `unknown`：**逐字同上两条** |
| `data/report/x.json`（目录名打错） | 产物其实 fresh | `unknown`：「没有溯源 sidecar」 |

⇒ 后三条的理由句**逐字相同**，而真相**两两不同**。

根因在两处：

1. `sidecar_path()` 把 `SIDECAR_SUFFIX` 拼在**完整文件名**后面
   ⇒ 裸名 `verdict_composite` 去找 `verdict_composite.provenance.json`，
   而真名是 `verdict_composite**.json**.provenance.json`；
2. `DEFAULT_GENERATORS` 的 31 个键里 **29 个带 `.json`**
   ⇒ `resolve_generator("verdict_composite")` 返回 `None`，
   而它**其实登记着**。

#### 三条判据（预注册，先于实现）

| # | 输入条件 | 必须出现的问题 | 必须**不**出现 |
|---|---|---|---|
| **C1** | 传进来的 `report_path` **既无目录部分、又无 `.json` 后缀** | 一条说清「裸名 ⇒ `sidecar_path` 会去找错的名字」，并给出补 `.json` 后能解析到的生成器 | 「不在 DEFAULT_GENERATORS 里」——**前提是**补上 `.json` 后确实能解析到；若补了仍解析不到，那条**要留** |
| **C2** | `report_path` 指向的文件**不存在** | 一条说「**产物不存在**」，与「产物在但没盖章」分开 | 「没有溯源 sidecar——生成它的 commit 无从得知」——对着一份**从没生成过**的产物说「生成它的 commit 无从得知」，是**陈述一件不成立的事** |
| **C3** | 两者都不成立 | 维持现状：`没有溯源 sidecar` + 可选的 `没有 code_sha` | —— |

⚠️ **C2 为什么不抑制「不在 DEFAULT_GENERATORS 里」**：路径拼错与真没登记，
`freshness()` **分不出来**——要分出来就得在 `data/reports/` 里猜相似名，
而本模块的规矩是**不回落猜测**（猜错生成器会让走查去查一个无关文件）。
⇒ 分不出来就**照实说不分得出来**，不许假装能分。

#### 连带会改的断言（先登记）

`test_every_reason_is_reported_not_just_the_first_one`：
`len(problems) == 2` **不变**（产物不存在那条顶掉了「没有 sidecar」那条，条数还是 2）；
但 `"sidecar" in r` → 改为断言**产物不存在**那一条。

⚠️ **这一处改动是改判据，不是为了让门禁变绿**：
原断言把「产物缺失」钉成「产物在、但没盖章」，
而 §1.1 已经指出那是**方向相反的静默失效**——
不是把好产物丢掉，而是**让人去找一个从不存在的东西**。
⇒ 旧断言**编码的是一句假陈述**，必须换。
⇒ 门槛（两条 ⇒ 两条）**一个字没放宽**，只换了其中一条的**内容**。

#### 不做

- **不抛异常**。§1 倾向的「响亮拒绝」会波及 `scan()` / CLI / 全部测试；
  而 C1/C2 已经把「静默猜错」变成了「说清为什么猜错」，方向风险已被堵住。
  ⇒ **先改报告内容，不改接口。** 真要拒绝，另开一次并先量全仓调用点。
- **不做相似名搜索**（见上）。

#### ✅ C1 / C2 已实现（2026-10-15），三条判据全部按预注册落地

三种输入现在**互相可辨**（实测输出）：

```
裸名 verdict_composite（真产物其实是 fresh）
  unknown
  1. 传进来的是**裸名** `verdict_composite`（既无目录也无 `.json` 后缀）⇒
     `sidecar_path` 去找的是 `verdict_composite.provenance.json`，而真名是
     `verdict_composite.json.provenance.json`；补上 `.json` 后能解析到生成器
     `scripts/verdict_composite.py`，请传 `data/reports/verdict_composite.json`

产物缺失 /tmp/x/unmapped_report.json
  unknown
  1. 产物路径 `…` **不存在** ⇒ 这是**产物缺失**，与「产物在、但没盖章」是两件事
  2. 不在 DEFAULT_GENERATORS 里…

产物在但没章 /tmp/x2/no_stamp.json
  unknown
  1. 没有溯源 sidecar——生成它的 commit 无从得知
  2. 不在 DEFAULT_GENERATORS 里…
```

⇒ 修复前这三条的**理由句逐字相同**，而真相两两不同。

**变异验过**（各自独立，且 sha256 逐字节还原）：

| 变异 | 结果 |
|---|---|
| 抽掉 C1 的裸名分支 | `1 failed` |
| 抽掉 C2 的产物缺失分支（退回统一说「没有 sidecar」） | `2 failed`（含对照组那条，见下） |

★ C2 的守卫里有一条**对照组**：文件**真的存在**但没盖章时，
必须**仍然**说「没有 sidecar」。
没有它的话，C2 可以靠「一律改口」通过——**那与旧缺陷同构**，
只是把一句假话换成另一句假话。

#### 🚨 连带发现：C1 破掉了旧代码的一条**不变量**，崩点离病因很远

旧代码有一条隐含保证：**`problems` 为空 ⇒ `gen` 必有值**，
因为 `gen` 为空时那句「不在 DEFAULT_GENERATORS 里」一定会被追加。

C1 里 `and not (bare and bare_gen)` **抑制了那一条** ⇒ 保证破了
⇒ 变异抽掉 C1 后，落进 `dirty = bool(st.get("code_dirty"))`
⇒ **`AttributeError: 'NoneType' object has no attribute 'get'`**。

★ 报错说的是「`NoneType` 没有 `get`」，病因却在**上面二十行**那个 `and not (...)`
⇒ 这正是第 131 层的形状：**跑到底才炸，且炸点不可读**。

⇒ 已加显式兜底（**方向保守**：只把「崩在半路」变成「报 unknown」）：

```python
if gen is None or st is None:
    missing = "生成器" if gen is None else "溯源章"
    return {..., "verdict": "unknown", "reason": f"{missing}解析不到 ⇒ 无从判定（兜底，不是正常路径）"}
```

⚠️ 这个兜底**在正确代码下不可达**（C1/C2 任一生效时 `problems` 非空）。
它存在的理由不是「防住今天这个 bug」，而是**把那条隐含保证重新写成显式的**——
隐含保证会被人无意中破掉，显式判断不会（第 131 层同族）。

#### 连带改动的两条断言（**门槛一个字没放宽**）

| 断言 | 怎么改的 | 为什么 |
|---|---|---|
| `test_a_report_with_no_sidecar_is_unknown_not_fresh` | **不改断言，改被测物**：把 `/nonexistent/x.json` 换成 `tmp_path` 下**真的建出来**的文件 | 它声称在测「产物在、但没章」，实际测的是「文件找不到」——**因为错误的原因通过**（第 134/135 层同一个病）。改完之后 `"sidecar" in reason` 才第一次为真地成立 |
| `test_every_reason_is_reported_not_just_the_first_one` | `len(problems) == 2` **不变**；`"sidecar" in r` → `"不存在" in r` | 旧断言把「产物缺失」钉成「产物在但没盖章」，而那正是 §1.1 指出的**方向相反的静默失效**。⇒ 旧断言**编码的是一句假陈述**，必须换 |

⇒ 两条都**不是**为了让门禁变绿；第一条是让测试名副其实，第二条是换掉一句假话。

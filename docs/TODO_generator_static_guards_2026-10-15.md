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
  ⇒ ⚠️ 但本条**未修**：`aplus/provenance.py` 在链的盖章路径上（第 125 层冻结）。
  ⇒ 已做的规避：`scripts/audit_placebo_rerun.py` 在问字节证据前**自己 chdir 回仓库根**，
    并把 cwd 打印出来——⚠️ 这是**规避不是修复**，真修要动 `provenance.py`。

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

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

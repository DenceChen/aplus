# `test_bench_seed_sweep.mut` —— 变异台规格的散文

`.mut` 块内**不许有散文**（块内一个空行就会吃掉 `=== 变异体 ===` 分隔符，
而 `textwrap.dedent` 的首行必须写到列 0）⇒ 意图、预期、边界全在这个兄弟文件里。

被测对象：`scripts/bench_seed_sweep.py`
被守卫对象：`tests/test_bench_seed_sweep.py`

---

## 为什么是这 14 条

判据的难点不是写变异体，是**挑「真实失效形态」**：
一条变异若落在判据量不到的层，它会「存活」，而**存活会被读成「守卫有洞」**
⇒ 最贵的错误方向是「去改本来正确的守卫」（第 112 / 148 层）。

所以每条变异都必须能回答两个问题：
1. **我改的是哪一行**（台子输出只有 ✅/❌，没有任何一栏能告诉你这个）；
2. **改完之后，被它杀的那条守卫在守什么**。

选变异时的三条过滤（按顺序，任一不通过就不写）：

| 过滤 | 被刷掉的候选 |
|---|---|
| **打「用点」，不打「定义点」**（第 137 层） | `market_nav_caliber.py::_annualized_compound` 的年化定义**不可动**：那个文件的字节正在被在飞的重跑使用（第 125 层）⇒ M6 改的是 `bench_seed_sweep.py` 里的**调用点** |
| **等价变异体不算**（第 149 层） | `deltas = {k: c - base …}` → `{k: c …}`：减去常数**保持 `repr` 的互异性** ⇒ `all_identical_across_k` 与 `reading` 两个判决字段结构上不变 ⇒ 存活也说明不了任何东西 ⇒ **换掉那条变异，守卫一个字不动**（补断言会给生产加一条恒真约束） |
| **无处可打的登记为债，不凭印象编** | ~~`required_excess` 零调用点 ⇒ 「变异它的接线」无处可打~~ ⚠️ 这条**真的命中过**，且命中得**错**了 —— 见下 |

🚨 **第 3 条过滤曾被用错一次（2026-10-16 补记，这是本节最贵的一条）**：
它当初把 `required_excess` 在 `bench_seed_sweep.py` 里**零调用点**（只有 import 与
散文）写成「无处可打 ⇒ 登记为债」。🚨 而它真正的含义不是「这个变异打不了」，
是 **预注册 §7 约束 1 只兑现了一半** —— 字段 `required_excess_all_positive`
承诺「`required_excess()` 全部为正」，实现写的是 `bool(holds)`，
**从未调用过它**（第 177 层：字段名不承诺语义）。
⇒ 那笔「债」把一个**该做的缺口**降级成了一句不起眼的技术备注，
**而没有任何机制会喊**。⇒ 已接线（见 M14），现在这条变异打得动了。

🚨🚨 **通则（2026-10-16）：「登记为债」要分两种，混用会把缺口洗成边界**

| | 是什么 | 登记为债是否诚实 |
|---|---|---|
| **(a) 本轮就不该动** | 如 `market_nav_caliber.py::_annualized_compound` 的**定义点** —— 那个文件的字节正被在飞重跑使用（第 125 层） | ✅ 是**诚实的边界声明** |
| **(b) 本该接线却没接线** | 如上面的 `required_excess` | 🚨 是**用一句散文把预注册的约束变成已兑现的假象** |

⇒ **判据一句话**：这个缺口是「本轮不做」还是「本轮该做而没做」？
⇒ 而「登记为债」这个动作本身**不携带**这个区分 ⇒ 必须显式标注 (a)/(b)。

---

## 预期：**14 条全被杀**（M10 / M12 曾两次存活，已处置）

🚨 **`Spec.expect` 在 `mutation_harness.py::load_specs` 里硬编码 `"killed"`
⇒ 规格无法表达「预期存活」** ⇒ 下面几节记的是**当时的存活与它的处置**，
**不是**现在的预期。

✅ **实测终态（2026-10-16）：14/14 逐条 `rc=1`，基线先验绿 `27 passed`。**
🚨 若你读到某一节写着「预期存活」而台子输出是 ✅，那是**散文过期**
（第 142 层：散文描述了一条代码不具备的性质）⇒ 以台子输出为准，并顺手改这里。

### M10 —— D2 的比值去掉 `abs()`：**曾存活（真洞）→ 补守卫后被杀**

```
ratios = {k: abs(c) / m_mkt …}   →   ratios = {k: c / m_mkt …}
```

`MDE` 的定义是 `|c|`（`judge()` 第一条：`|c| < M` ⇒ 连符号都定不了），
所以分母上一步要的是**绝对值**。摘掉它 ⇒ 比值变成**单边读数**：
一个 `c = −0.2` 的市场腿会算出负比值，而 `ge1 = sorted(k for k, r in ratios if r >= 1.0)`
对它仍然判「测不出」。

**为什么存活**：`tests/test_bench_seed_sweep.py` 里喂给 `d2_…` 的夹具
**`c_mkt` 全为正** ⇒ `abs(c) == c` ⇒ 变异体在**任何现有夹具上与原实现逐位同形**。
⇒ 这是**真洞**（守卫对负 `c_mkt` 零覆盖），不是等价变异体
（判别依据：负 `c_mkt` 真的会发生吗？会 —— `market_nav_caliber.json` 的
`equal_weight.geometric_annual_pct` 是符号量，市场跌一年它就是负的）。

⇒ **处置（不要改生产去迎合变异）**：补一条守卫，喂**负 `c_mkt`** 并断言比值为正。
⚠️ 判据**不许用公式重算「验证」**（第 109 层）⇒ 外部锚用
「`|mean|` 落在 `min` 与 `max` 之间」这类**不需要公式也不需要容差**的性质，
或者直接断言「负 `c` 与等值的正 `c` 必须给出同一个比值」（对称性判据）。

✅ **已落地（2026-10-16）**：守卫
`test_d2_measures_a_sign_blind_ratio_not_a_single_sided_one`，按上面**第二种**
写法落（`+c` 与 `−c` 必须给出同一个比值，且该比值为正）。
★ **权威取自产物自己的 `rule` 字段**：`d2_…()["rule"]` 逐字写着 `|c_mkt(k)| / M_mkt >= 1`
⇒ 本条钉的是「实现符合它**自报**的规则」，不是「我想要 `abs()`」——
而锚在自己偏好上就是「守卫与作者同一个人」（第 13 层的自噬形态）。
**实测**：变异被这条杀，`rc=1 / 1 failed, 24 passed`。

### M12 —— 判决臂换成敏感性臂：**曾存活（第 183 层在新文件里的复发）→ 补守卫后被杀**

```
verdict_arm = VERDICT_BENCH_ARM   →   verdict_arm = "cap_weight"
```

**被诊断的对象整个换掉，而 D1–D4 的判决可能一字不变。**
理由是 D1 判的是 `max_k c_mkt(k) < target`，而当年实测两臂
`equal_weight` 与 `cap_weight` 的「实测/需要」差 **7.94×**
⇒ 换臂足以改结论，**但也可能一个判决都不改**（两者都落在同一侧）。

**为什么存活**：`test_d4_is_fed_the_production_conversion_not_a_local_one`
只查 `c_mkt_verdict` 的**来源**（必须是 `caliber_benchmark_fraction(...)`），
**不查 `verdict_arm` 的取值** ⇒ 对换臂完全隐形。
这正是第 183 层记录的形态：当时换臂时**18 条守卫全绿**，因为判决没变，
而「换了被诊断的对象」被完全藏住。

⇒ **处置**：补一条 **AST 接线守卫** —— `_run` 里 `verdict_arm` 的赋值右端
**必须是 `Name(id="VERDICT_BENCH_ARM")`**，不许是字面量。
⚠️ 判据要钉**赋值右端**，不是「源码里出现过那个名字」（第 175 附二：
`ast.walk` 对 `Module` 与 `FunctionDef` 都会递归进函数体，
所以必须收在**具体那条 `Assign`** 上，而不是「全文出现过」）。

🚨 **绝不允许的处置**：把 `verdict_arm` 改回、或者把它降级成「随扫描参数」。
判决臂是**预注册**的（第 183 层 + 预注册 §8.1），换臂要走
**加对照臂并显式标 `role`** 那条路。

✅ **已落地（2026-10-16）**：守卫
`test_the_verdict_arm_is_the_preregistered_constant_not_a_literal`
＋ 两个**共用** helper（`_verdict_arm_rhs` / `_is_preregistered_verdict_arm`）。
⚠️ **helper 抽出来是为了被两处共用**（第 131 层）：主守卫与它的**正控**
必须走**同一个判决函数**。上一版正控断言的是 AST 自己 ⇒ **恒真**；
而「探测器认得出错误」与「判决拒得了错误」在代码上根本不相干。
⚠️ 收在**具体那条 `Assign`** 上，而不是「源码里出现过那个名字」（第 175 附二：
`ast.walk` 对 `Module` 与 `FunctionDef` 都会递归进函数体 ⇒ 那条赋值被整个删掉、
而别处还留着那个名字时，「全文出现过」照样成立 ⇒ 对本次要抓的形态零信息量）。
**实测**：变异被这条杀，`rc=1 / 1 failed, 24 passed`（⚠️ 那是**当时**的 25 条守卫；
现为 27 条 ⇒ 同一条变异的输出行会是 `26 passed`，**别拿它当现状**）。

### M14 —— 接线的年化年数传错：**新加**，且带出第 184 层在**新守卫自己身上**的复发

```
req = {k: required_excess(target, c_mkts[k], yrs) for k in sorted(c_mkts)}
→   req = {k: required_excess(target, c_mkts[k], yrs + 1) for k in sorted(c_mkts)}
```

**它为什么存在**：M14 打得动，是因为上一节那条「`required_excess` 零调用点」
的债**已经还掉**（接线）⇒ 变异台第一次能打「实参传错」这一类形态。

⚠️ **判判决分辨不了它**：按代数必然，`all(required_excess(...) > 0)` 与 `holds`
**恒同值**（第 176 层）⇒ 任何断言那个布尔的守卫在「接线传对」与「接线传错」上
**同样为真** ⇒ 守卫只能锚在**逐格数值**上。

🚨🚨 **两轮实测，两轮红法不同 —— 这一节本身就是第 184 层的现场**：

| 轮次 | 输出 | 主守卫 | 正控 | 说明 |
|---|---|---|---|---|
| 第一轮 | `rc=1 / 2 failed` | 红 | **红** | 🚨 正控红在**「正控前提失效」**那句上，**不是**它 docstring 声称的「认得出实参被换」 |
| 正交化后 | `rc=1 / 1 failed` | 红 | 绿 | ✅ 各自只在**自己声称的那一支**上生效 |

**第一轮那两条红是假的覆盖**。机制：正控当时长成
「主守卫 ∧ 判定函数非恒真」的**合取**（它先 `assert` 了 `d1()` 的输出通过）
⇒ 它红的**永远只能是合取里先失败的那一支**
⇒ 🚨 **它声称的那一支结构上从未生效过**。精确推理（不需重跑即可判定）：
M14 让 `by_k` 用 `yrs=6.7` 算，而判定函数比的是 `(1+v)**5.7` ⇒ **两端 yrs 不同
必然不等** ⇒ 删掉前置 assert 后它在 M14 上**必绿** ⇒ 对 M14 零判别力
（第 20 层的镜像：**探针自带一个把目标断言遮住的前置断言**）。

⇒ **处置不是加断言**（那会把一条有效的锚绑到它看不见的一半上，第 112 层），
而是**把正控与被测实现解耦**：`by_k` 改由 `M.required_excess`（生产函数，
**不重写公式**）直接造，**不经过 `d1()`** ⇒ 正控只问「判定函数本身」，
**接线由主守卫问** ⇒ M14 只被主守卫杀。

⚠️ **诚实边界（别当成已覆盖）**：那个判定函数住在**测试文件**里，
而变异台的 `Spec.rel` 只打**生产文件** ⇒ 🚨 **台子结构上打不到它**
⇒ 它是「人工可问」层，不是「变异可证」层（第 124 层）。
⚠️ 并且 `-k bench_seed_sweep` 会多选中 **1 条**
`test_every_bonferroni_call_site_passes_df[scripts/bench_seed_sweep.py]` ——
那个文件**零** Bonferroni 调用 ⇒ 该 parametrize 用例**平凡通过**
（docstring 逐字写「每个**真的会执行**的调用」⇒ 空集平凡通过是它的**正确语义**）
⇒ 🚨 但它的绿灯**不携带任何关于本次改动的信息** ⇒ 汇报口径是
「**27 条真覆盖 + 1 条平凡通过**」，不是「28 条都在守它」。

---

## 逐条：改的是哪一行 / 被谁杀

| # | 改的是哪一行 | 预期 | 被谁杀 |
|---|---|---|---|
| M1 | D4 取基线格 `c_mkt_by_k.get(0)` → `.get(1)` | 杀 | `test_d4_can_actually_match_the_artifact_bitwise`（`k=0` 那格逐位复现产物 caliber，换成 `k=1` 后 `k0_matches…` 变 `False`） |
| M2 | 逐位比较右端 `base == artifact_as_fraction` → `base == base` | 杀 | `test_d4_can_actually_report_a_mismatch`（恒真后「报得出不一致」那一支永远走不到） |
| M3 | `"all_identical_across_k": len(distinct) == 1` → `True` | 杀 | `test_d4_flags_the_seed_as_ignored_when_every_delta_is_identical`（`varied` 分支断言 `is False`） |
| M4 | `missing = [d for d in dates if d not in series]` → `d in series` | 杀 | `test_geometric_annual_refuses_to_compromise_on_a_missing_day` 的**反向正控**：完整档也抛 `KeyError` |
| M5 | `n = len(dates)` → `n = len(dates) - 1` | 杀 | `test_geometric_annual_compounds_back_to_the_measured_multiple`（`assert n == len(dates)`） |
| M6 | 调用点 `_annualized_compound(mult, n)` → `(mult, n * 244)` | 杀 | **只有** `test_geometric_annual_compounds_back_to_the_measured_multiple`（它把指数 `n/244` 逐字钉进断言 ⇒ 变异后 `(1+g)^(n/244) = mult**(1/244) ≠ mult`）。🚨 **原写「＋单调性那条」是错的**：实测 `rc=1 / 1 failed, 24 passed`，单调性那条**没有红** —— 终值固定时 `244/n` 与 `1/n` **都**随 n 严格递减 ⇒ 缺陷在**那条守卫所测的方向上是对称的**（第 20 层）⇒ 它对 M6 结构上零信息量 |
| M7 | `r >= 1.0` → `r > 1.0` | 杀 | `test_d2_boundary_is_inclusive_at_exactly_one_mde`（`|c| == M` 必须进越线集；`nextafter(M, 0)` 必须不进） |
| M8 | `holds = c_mkts[max_k] < target` → `holds = True` | 杀 | `test_d1_calls_the_production_required_excess_on_this_files_own_c_mkt`（`equals_holds` 变 `False`）＋ `test_d1_can_reach_both_branches` ＋ `test_d1_reads_the_target_from_its_argument` |
| M9 | 拒绝条件 `1.0 + c <= 0.0` → `c < 0.0` | 杀 | `test_d1_refuses_a_c_mkt_for_which_the_annualization_is_undefined` 的**反向正控**：`-0.999` 是合法的年化输入，被误拒 |
| M10 | `abs(c) / m_mkt` → `c / m_mkt` | 杀（补守卫后） | `test_d2_measures_a_sign_blind_ratio_not_a_single_sided_one` —— **曾存活**，见上 |
| M11 | `for k in daily_map:` → `for k in sorted(daily_map):`（**行尾注释原样保留**） | 杀 | `test_order_fingerprint_is_over_insertion_order_not_sorted_order` |
| M12 | `verdict_arm = VERDICT_BENCH_ARM` → `"cap_weight"`（**行尾注释原样保留**） | 杀（补守卫后） | `test_the_verdict_arm_is_the_preregistered_constant_not_a_literal` —— **曾存活**，见上 |
| M13 | `c_mkt_verdict = caliber_benchmark_fraction(…)` → `… / CALIBER_PCT_SCALE` | 杀 | `test_the_pct_conversion_occurs_exactly_once_in_this_file`（量纲换算只许发生一次）＋ `test_d4_is_fed_the_production_conversion_not_a_local_one`（`rhs` 不再是 `Call`） |
| M14 | `required_excess(target, c_mkts[k], yrs)` → `(…, yrs + 1)` | 杀 | `test_d1_calls_the_production_required_excess_on_this_files_own_c_mkt`。⚠️ 第一轮它还多红一条正控，而**那条红是假的覆盖**（正控红在前置 assert 上，不在它声称的那一支）⇒ 正交化后正控转绿，见上节 |

★ **M11/M12 的注释是故意留下的**：变异后源码里那句
「按**插入序**，不是排序序」「# `equal_weight`」当场变成谎话，
而 `ast` 守卫看不见注释（第 13 层）⇒ 留着它零成本，还能让脱钩在 diff 里肉眼可见。

---

## 读台子输出时的三条

1. 🚨 **只有 `rc == 1` 才算被杀**。`rc ∈ {2,3,4,5}`（收集 / 用法 / 内部错误 / 中断）
   是**不可验证**，按存活计入上界 —— 台子会印出原文。
2. ⚠️ **「拒收（旧串匹配 0 处）」≠「存活」**：
   前者的下一步动作是**改规格**，后者是**查守卫**，两者方向相反（第 141 层）。
3. 🚨 **变异台运行期间不得并发跑别的 pytest**（第 173 层）：
   并发的 pytest 会读到**正在被改写的源码**，而 `tests/conftest.py` 的
   session 级 autouse fixture 会跑 `_recover_pending()` 可能中途还原
   ⇒ 结果是**全部报存活**。这条至今**只有约定、没有守卫**（登记为债）。

★ 「存活」在本仓是最贵的错误方向：它会被读成「守卫有洞」，
而正确动作有时是「**这条变异压根没打在判据上**」（第 172 层）。
⇒ 每条判读前先回看它改的是哪一行。

# 变异台说明：`scripts/measure_delivery_caliber_friction.py`

**散文只允许待在这里。** `.mut` 里既不许写说明也不许写 `#` 注释 ——
`load_specs` 按 `"\n\n"` 切块，一个以 `#` 开头的注释块会被当成一条**没有
`=== 变异体 ===` 的规格**，台子 `sys.exit(...)` **杀掉整个进程**
（不是跳过那一条）。

规格文件：`tests/mutation_specs/test_delivery_caliber_friction.mut`（9 条，F1–F9）
目标 node：`tests/test_delivery_caliber_friction.py`（23 条守卫）

```bash
.venv/bin/python scripts/mutation_harness.py \
  tests/mutation_specs/test_delivery_caliber_friction.mut \
  --node tests/test_delivery_caliber_friction.py
```

⚠️ **`specs` 是位置参数**（不是 `--spec`，第 174 层栽过一次）
⚠️ **不要把管道放在这条命令上**（第 8/107 层：退出码会变成 `tail` 的）
⚠️ **只有 `rc == 1` 才算被杀**；`rc ∈ {2,3,4,5}` = 不可验证，按存活计入上界
⚠️ **运行期间不得并发跑别的 pytest** —— 台子每一轮都在**真实改写被测文件**，
并发进程 import 到的是变异体（第 173 层）

---

## 为什么需要这一台：一条守卫的 docstring 声称能抓的形态，它实测抓不到

`test_the_verdict_reads_the_interval_low_end_not_the_point_estimate` 的 docstring
**逐字写着**「且让两档给出**不同判决档**」。而它的夹具 `_turnover_overlap([4, 0])`
给出的是 τ ∈ {0.6, 1.0}，点估计 0.8：

| 臂 | τ | 摩擦 | 判决 |
|---|---|---|---|
| min（下端） | 0.60 | 80.81% | `SINGLE_SIDED_UNREACHABLE` |
| point（点估计） | 0.80 | 107.75% | `SINGLE_SIDED_UNREACHABLE` |

⇒ **同档**。把调用点的 `judge(at_min…)` 改成 `judge(at_point…)`，
`out["verdict"]` **一个字都不变** ⇒ **F4 会整条存活**，
而那条守卫断言的正是 `verdict == SEALED`。

🚨 **这不是夹具的人造性质，是真实产物的形态。** 实读
`data/reports/delivery_caliber_friction.json`：`at_min` 摩擦 **80.81%**、
`at_point` **98.77%**，**两档都 ≥ 20%** ⇒ 都 `SINGLE_SIDED_UNREACHABLE`
⇒ **在真实数据上把判决臂换掉同样不可观测。**
⇒ 所以跨档夹具不是「为了让测试变红而造的玩具」，
它是**真实形态的缩影**：真实数据下 F4 天然隐形，
只有**造一个跨档结构**才让它显形。

★ **「两档的摩擦数不相等」与「取错哪一个看得见」是两件事。**
那条守卫只能杀「两臂摩擦**完全相同**」那一类变异。
（第 184 层：守卫自己的 docstring 声称能抓的形态，实测它抓不到，
而变异台**永远不会喊这件事**——它只对被送进去的变异负责。）

修法不是改那条守卫（第 112 层：补断言会给生产加恒真约束），
而是**加一条跨档夹具** `_turnover_overlap([9, 4])`（第 183 层：加臂而非换臂）：

| 臂 | τ | 摩擦 | 判决 |
|---|---|---|---|
| min（下端） | 0.10 | **13.47%** | `OVER_TARGET_BUT_NOT_SEALED` |
| point（点估计） | 0.35 | **47.14%** | `SINGLE_SIDED_UNREACHABLE` |

⇒ 判决**整档翻转** ⇒ F4 当场变红。
（摩擦对 τ 严格线性 `1.34688 = 276 × 244 / 50000`，该线性由 G1 独立钉住。）

⚠️ **副产品当场被证伪**：我在写这份说明时预测「同一条新守卫顺带杀掉 F9」，
台子实测**跨档夹具没有红**，F9 唯一变红的是下面那条 **AST 接线守卫**。
预测与实测方向相反，按实测改（详见「F9 的归因更正」一节）。

---

## 变异清单

| # | 标签 | 源码行 | 改什么 | 靠哪条守卫杀掉 |
|---|---|---|---|---|
| F1 | 单边换手被乘 2 | 125 | `rpy = D * τ` → `D * 2 * τ` | `test_one_side_turnover_is_multiplied_once_not_twice` |
| F2 | 判决臂写死满换手 | 163 | `turnover_min_measured` → `1.0` | G4/G5 组 + 跨档夹具 |
| F3 | 年化常数取错另一套 | 38 | `hurdle.DAYS_PER_YEAR`(244) → `costs.TRADING_DAYS_PER_YEAR`(250) | `test_days_per_year_comes_from_hurdle_not_a_literal` |
| F4 | 判决取点估计而非区间下端 | 172 | `judge(at_min…)` → `judge(at_point…)` | ★ **跨档夹具**（旧夹具对 F4 零判别力） |
| F5 | 名数写成 5 | 42 | `N_POSITIONS = 10` → `5` | `test_the_effective_commission_rate_…`（`per == 5000.0`） |
| F6 | 判决边界 `>=` 写成 `>` | 144 | | `test_the_verdict_boundary_is_inclusive_at_the_target` |
| F7 | 跌腿不过滤 | 65 | `rows[rows.direction=="up"]` → `rows` | `test_the_down_leg_is_excluded_from_turnover` |
| F8 | 首个 `entry_date` 参与换手 | 84–86 | 删 `continue` | `test_the_first_entry_date_never_participates` |
| F9 | `measure()` 调用点名数少 5 | 162 | `N_POSITIONS` → `N_POSITIONS - 5` | ★ **AST 接线守卫**（跨档夹具**没有**红，见下） |

### F9 的归因更正（写预测的那个错，比算错更贵）

🚨 **我原先写「跨档夹具（摩擦翻倍 ⇒ min 臂 26.94% ⇒ SEALED ≠ OVER）」，台子实测跨档夹具全绿。**
台子只报 `rc`，不报「谁杀的」和「为什么」，所以这段散文可以**安静地腐烂**
——两个独立来源（台子输出 + 下面的算术）才把它钉出来。

**算术（实测，不是推理）**：

| 名数 | 每腿名义额 | 单腿佣金 | τ=0.10 摩擦 | 判决臂 | τ=0.35 摩擦 | 判决臂 |
|---|---|---|---|---|---|---|
| 10（真值） | ¥5,000 | `max(5000×0.0003, ¥5)` = **¥5** | **13.469%** | 否 | **47.141%** | 是 |
| 5（变异） | ¥10,000 | `max(10000×0.0003, ¥5)` = **¥5** | **11.029%** | 否 | **38.601%** | 是 |

⇒ 名数减半**让摩擦下降 2.44pp**，而我预测的是「翻倍到 26.94%」。**方向反了。**

**成因**（这是本条唯一有价值的部分）：

- **最低佣金 ¥5/笔是每笔固定额**（`fee.commission_min`），而
  `max(名义额 × 0.0003, ¥5)` 在 ¥5,000 与 ¥10,000 两档**都已触底**
  （5000×0.0003 = ¥1.50 < ¥5；10000×0.0003 = ¥3.00 < ¥5）
  ⇒ 单腿佣金**纹丝不动**（都是 ¥5），而**腿数从 10 减到 5** ⇒ 佣金总额减半。
- 印花税与过户费按 `名义额` 比例 ⇒ 单腿翻倍 × 腿数减半 ⇒ **恰好抵消**。
- 合起来 ⇒ 摩擦**降**。

⇒ **两档都留在同一判决档（min 臂未封口、point 臂已封口）**
⇒ 判决 `OVER` 一个字不变 ⇒ 跨档夹具**对 F9 零判别力**。

★ **通用教训**：`max(固定额, 比例额)` 这类**带地板的成本函数不是单调的**。
我脑子里的模型是「仓位越小 ⇒ 额越大 ⇒ 成本越高」，
而地板让那个直觉在**地板生效区完全失效**。
⇒ 遇到带下限/地板的成本，**不许用心智模型推方向**，必须调函数算。

### F9 真正靠谁杀掉

`mechanism.notional_per_position` 单独算 `CAPITAL × EQUITY_WEIGHT / N_POSITIONS`，
而四档摩擦走 `friction_at(…, N_POSITIONS, …)` —— **两个独立引用**。
只钉住 `mechanism` 那一侧时，把调用点改成 `N_POSITIONS - 5` **全绿**：
判决不变、点估计不进判决、甚至 `required_turnover_for_target` 都自洽
（它由同样被改的 `full` 推出）。

⇒ 缺口 ⑥ 由 **`test_the_mechanism_reads_the_same_position_count_as_the_friction_arms`**
（**AST 枚举全部 4 个 `friction_at` 调用点**，逐个断言第三实参是裸名 `N_POSITIONS`）
关闭，实测 `1 failed` / `rc=1`。

⇒ ★ **这条正好是「形态守卫 vs 行为守卫」不可互换的又一个例子**：
行为守卫（跨档夹具）问的是「判决变没变」，而 F9 的真实失效是
「**两个本该相等的引用分家了**」—— 判决没变正**因为**它没变。
只有接线层能把「分家」这件事本身变成红。

---

## 规格是怎么生成的（别手写）

`scripts/_gen_mut_spec.py`（**入库**，因为变异规格本身是产物）从源码**按行**切锚点，
然后用 **`MH.load_specs` + `src.count(old)==1`** 自检 ——
自检必须复用**被检对象的那份代码**（第 171 层：
另写一份「等价」解析器做自检，量的是我的脚本，不是台子的）。

本轮它当场抓到 **8/9 不合规**，成因是 `load_specs` 限制 ③：
块以换行结尾 + `"\n\n"` 分隔 ⇒ 下一块的 `"\n"` 让 `block.split("\n",1)[1]`
把**标签行**算进 `old`。首块侥幸对、其余全中 ——
**症状（8 条「旧串匹配 0 处」）与「守卫有洞」逐位同形**。

另两条已踩过的：① `dedent` 的 pad 取**块内最小缩进**且**只减不增**
⇒ 多行锚点**首行必须写到列 0**、续行保持源码真实缩进；
② `Spec` 的字段名是 `rel` 不是 `path`，`expect` 在 `load_specs` 里
**硬编码 `"killed"`** ⇒ 无法表达「预期存活」。

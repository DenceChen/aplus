# 变异台台账：跌腿闸门（G4 镜像，2026-10-20）

配套 spec：`tests/mutation_specs/test_downleg_gate.mut`（**7 条**）
被测 node：`tests/test_downleg_gate.py`
被测实现：`scripts/measure_downleg_ranking.py` 的闸门块
预注册：`docs/PREREG_DOWNLEG_GATE_2026-10-20.md`

复跑（⚠️ `specs` 是**位置参数**，没有 `--spec`；第 174 层）：

```bash
.venv/bin/python scripts/mutation_harness.py \
    tests/mutation_specs/test_downleg_gate.mut --node tests/test_downleg_gate.py
```

⚠️ `rc=1` 才算被杀；`rc ∈ {2,3,4,5}` = 不可验证，按存活计入上界。
⚠️ **变异台运行期间不许并发跑别的 pytest**（会读到变异体；第 173 层）。

---

## 七条变异各自在钉什么

| 标签 | 变异 | 被谁杀（**具体的那条**，不含通用盖章守卫） | 杀的方向 |
|---|---|---|---|
| **D1** | 方向写反（`>=` → `<=`） | `test_the_down_gate_judges_hit_rate_falling_as_score_rises` | 跌腿的镜像方向 |
| **D2** | 三态塌成两态（`else VERDICT_MEASURED` → `VERDICT_POWERLESS`） | `test_all_three_verdicts_are_reachable_from_forked_fixtures` | 「下一步该做什么」 |
| **D3** | 检出力下限改成手打常数 `0.2348` | `test_the_gate_artifact_reconciles_with_a_fresh_measurement`（**对账守卫**） | 检出力下限的来源 |
| **D4** | `avoidance_list_publish_allowed` 写死 `True` | `test_the_avoidance_publish_flag_is_wired_to_the_verdict`（**AST 接线层**） | 闸门形同虚设 |
| **D5** | 闸门臂 `down` → `up` | `test_the_gate_arm_is_the_down_leg` | 把跌腿的判决拿到涨腿上 |
| **D6** | 四分位从组内切成全局 | `test_the_quartiles_are_cut_within_each_group_not_globally` | 第 22 层（哪天 vs 分数高） |
| **D7** | 缺 `entry_date` 时回落成 `pred_date` | `test_a_missing_entry_date_column_is_explicit_and_never_falls_back` | 第 6 种形状（显式缺失） |

⚠️ **每条都另有一条「通用」红**：`test_the_artifact_is_stamped_with_the_current_bytes`
在**任何**源码改动下都红（章上的 `code_file_sha256` 对不上当前字节）⇒
**那一条不算每条变异的独立证据**（第 137 层的同族：「同一个红算几笔」）。
⇒ 本表只列**具体**的那一条。

⚠️ **D3 的读法要特别小心**：`test_the_power_floor_is_the_permutation_null_quantile_not_a_literal`
**没有**变红 —— 因为它读的是**盘上那份产物**（产物没重跑 ⇒ 产物里的数当然还是对的），
而产物层守卫对**源码变异零覆盖**（第 96 层）。
⇒ 真正抓住 D3 的是**对账守卫**（在测试里**现算一遍**再逐位对账，第 118 层）。
⇒ 这正是「读快照的守卫等于没守」的又一处实证。

---

## ① D1 是本轮最要紧的一条：方向写反**不崩、不报错**

跌腿的判据与涨腿臂**代数形式逐字相同、含义相反**（Q1=最高分 ⇒
`<=` 在跌腿读作「分越高命中率越低」✓，在涨腿读作「分越低命中率越高」✗）。

⇒ 方向写反的后果是：**判据整个反过来，判决默默改，产物照常出数、章照常盖**。
⇒ 所以守卫必须**两个方向各造一个夹具**：
「低分 ⇒ 更可能真的跌」判单调；「高分 ⇒ 更可能真的跌」判**不**单调。
⇒ 且夹具**不许**用真实产物（真实产物落在哪一态是**运气**）。

## ② D6 的夹具是**刻意**让两种切法给出相反答案的

两组各 8 行、分数区间**不重叠**（100..93 与 8..1）⇒
组内切 ⇒ 两组都单调 ⇒ 判据成立；
全局切 ⇒ 两组被混进同一条排序 ⇒ 判据不成立。
⚠️ 若两组用**同一段分数**（最初的夹具就是），
全局切与组内切会给出**同一个答案** ⇒ 那条守卫就成恒真的装饰。

## ③ 「变异存活」有四种解释，而台子只打 ✅/❌

| 解释 | 怎么排除 |
|---|---|
| 变异是**空操作**（锚点没生效） | 台子会**响亮拒收**（「旧串匹配 0 处」） |
| 变异体**语法非法** | 台子 v2 有 `compile()` 闸门（`⚠️ 不可验证`） |
| 变异是**等价变异体** | 程序取证：两个实现逐位对照 |
| **守卫真的没覆盖到** | 取证后确认行为真的变了而没人红 |

⇒ 读到 ❌（存活）时，**下一个问题永远是「我改的是哪一行」**，不是「守卫没洞」。

## ④ 锚点写法（本 spec 用的全是**单行**，多行锚点见台子 docstring ①④⑤）

- 单行锚点按**源码真实缩进**写 ⇒ 台子 `dedent` 去掉公共缩进后
  仍是源码的子串 ⇒ `src.count(old) == 1` 成立。
- 多行锚点必须**首行顶格**、其余行**照抄源码缩进**（否则 `dedent` 会把续行一起左移）。
- D7 是本 spec 里唯一的**多行**锚点：首行顶格、后两行 12 格（源码真实缩进）。

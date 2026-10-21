# `test_goal_progress` 的变异规格（2026-10-21 新增）

规格本体在 `test_goal_progress.mut`；这里只放说明。

## 跑法

```bash
.venv/bin/python scripts/mutation_harness.py \
    tests/mutation_specs/test_goal_progress.mut \
    --node tests/test_goal_progress.py
```

基线必须先绿（`37 passed`），台子才会计分。
**规格里没有注释**（第 121 层纪律 ⑥：`.mut` 的块首行必须是「标签`<TAB>`相对路径」，
一个以 `#` 开头的块会让台子**整个进程退出**）。

## 本批 17 条：每一条在守什么

| # | 变异 | 它模拟的真实缺陷 | 杀掉它的守卫 |
|---|---|---|---|
| M1 | `if sign_ok is False:` → `is not False` | 「符号不可定」那一支的判据被翻面 | `test_the_goal_1_status_rule_reaches_every_branch`（分岔夹具） |
| M2 | 缺档时 `value: None` → `0` | 「没有台账」被读成「缺口是 0」 | `test_a_missing_artifact_yields_null_and_a_reason_never_zero` |
| M3 | 同口径倍数 → 跨口径倍数 | 屏上的「还差几倍」取错列（台账标它 `is_the_gap = false`） | `test_only_the_same_caliber_multiple_is_the_gap` |
| M4 | `ideal_ceiling_k63` → 改名 | 屏上只剩一个口径 | `test_both_d2_calibers_are_present_with_labels` |
| M5 | 达成判据 `>= target` → `>= target * 0.5` | 20% 目标被偷换成 10% | `test_the_goal_1_status_rule_reaches_every_branch` |
| M6 | `kind: manual` → `kind: artifact` | 「不知道」塌成一条普通读数 | `test_goals_4_5_6_mark_the_non_automatable_part_as_manual` |
| M7 | 两个 W 撞同一个 key | 屏上第二行盖掉第一行 | `test_no_goal_carries_two_readings_with_the_same_key` |
| M8 | 摘要换成手打的一句 | 散文里的数静默过期（第 73 层） | `test_the_summary_line_is_the_rendered_one` |
| M9 | MDE 的出处换成 `cagr` | 数看起来还是数，但**不是那个量** | `test_every_artifact_number_points_to_an_artifact_and_matches_it_bitwise` |
| M10 | 名单行数 `+1` | 推导数与真产物对不上 | `test_every_derived_number_is_independently_recomputable` |
| M11 | 会涨只数 `+1` | 形态达标被写成 11 只 | 同上 |
| M12 | 台账出处换成另一个产物 | 那一族读数整体读空 | `test_only_the_same_caliber_multiple_is_the_gap` |
| M13 | `readable: False` → `True` | 读不出来的那份被当成正常 | `test_the_endpoint_keeps_an_unreadable_snapshot_in_the_history` |
| M14 | `gaps` 被清空 | 时间轴看起来连续 | `test_the_endpoint_discloses_the_calendar_days_it_missed` |
| M15 | `is_the_gap === true` → `!== true` | 跨口径那一列被标成缺口 | `test_the_page_marks_only_the_same_caliber_multiple_as_the_gap`（**行为**守卫） |
| M16 | 倍数被手打进前端 | 数字离开脚本没有通道把它拉回来 | `test_the_frontend_does_not_hand_type_any_measured_number` |
| M17 | 深链白名单 `TABS` 漏了新 tab | 页面正常、console 无报错，**只有深链坏了** | `test_the_deep_link_allowlist_matches_the_tab_buttons` |

## ★ 两条**存活过**的变异，以及它们为什么不是「守卫没牙」

第一次跑：**14/16**。存活的是 **M1** 与 **M5**。

### M5 为什么存活（真实数据上那一支不可达）

M5 把「达成」判据放松一半。而**真实产物**落在「符号不可定」那一支 ⇒
`if sign_ok is False` **先返回** ⇒ 后面那两行（达成 / 未达）在真实数据上
**根本走不到** ⇒ 判据改坏了也**没有任何东西会红**。

⇒ 这不是「守卫是安慰剂」，是**判据不可问**（第 124 层）：
「达成那一支的判据对不对」这个问题在真实数据上**无法被问到**。

⇒ **修法不是放松守卫，是造分岔夹具**：
`test_the_goal_1_status_rule_reaches_every_branch` 用**合成输入**把五条分支
各走一遍 ⇒ M5 在夹具上落到 `ACHIEVED`（期望 `NOT_ACHIEVED` / `UNDECIDABLE`）
⇒ 被杀。

⚠️ 合成输入里的数是**判据的输入**，不是测量值 ⇒ 不违反
「不许手抄产物的数」。

### M1 为什么存活（子串断言被引文满足）

第一版的守卫钉的是「目标 1 的理由里含 `连符号都定不了`」。
而**产物自己的 `cases` 引文里也有这句话** ⇒ 两条分支的理由**都**含它
⇒ 这个子串**恒成立** ⇒ 守卫是装饰（第 13 层）。

⇒ 修法：把「走了哪一支」变成**显式标签**（`verdict_branch`），
守卫钉**标签**而不是**散文里的某个词**。
⇒ 副作用（好的一侧）：屏上现在能显示「判定分支」，
人一眼看出这条读数是「符号不可定」而不是「c < T ≤ U」。

## ⚠️ 这批变异**会真实改写** `aplus/web/index.html`

台子会改文件再还原（还原后按 **sha256 逐位**验，不是「替换执行了」）。
⇒ 跑之前确认**没有别的任务在改同一批文件**（本仓有并发会话）。
⇒ 本批 17 条**全部落在** `scripts/measure_goal_progress.py` / `aplus/webapp.py` /
`aplus/web/index.html` 三个文件上。

## 诚实边界

* 这 16 条只覆盖**本轮新增的三处代码**（生成器 / 端点 / 前端渲染）。
* 产物本身**不覆盖**：`data/reports/*.json` 的**内容**由上游生成器负责，
  本文件只钉「快照里的数**逐位等于**产物字段」。
* 前端那条 M15 靠**行为**守卫（把纯渲染函数交给 node 真跑一遍），
  不用「源码里有某个串」当判据 —— 后者会被注释满足（第 13 层）。

## ★ M17 的由来：**实测踩到的真缺陷**

加了「目标进度」这个 tab 之后，页面**一切正常**（tab 能点、能渲染、console 无报错），
但深链 `#/goal` **静默回落到概览** —— 根因是 `const TABS = [...]` 那张
**手维护的白名单**忘了加 `'goal'`。

🚨 这个缺陷**没有任何东西会喊**：
页面不报错、pytest 全绿、变异台全杀 —— 只有**深链这一条路**坏了。
⇒ 修法不是「记得改名单」（= 把判据挂在人的记忆上，第 96 层），
是**把两张名单钉在一起**：守卫扫出 HTML 里所有 `data-panel`，
与 `TABS` 比对 ⇒ **下一个 tab 忘了改就当场红**。

⚠️ 这条守卫的第一版**抓到了我自己刚写的注释**（文档里写了一句
`<button class="tab" data-panel="…">` ⇒ 子串正则把它当成了一个真按钮）
⇒ 第 13 层（子串断言 + 注释 = 守卫可被文档满足）。
⇒ 修法：守卫**先把 HTML 注释剥掉**再扫。

# 变异台说明：`tests/test_progress_page_export.py` ＋ `tests/e2e/test_progress_page_browser.py`

**散文只允许待在这里。** `.mut` 里既不许写说明也不许写 `#` 注释 ——
`load_specs` 按 `"\n\n"` 切块，一个以 `#` 开头的注释块会被当成一条**没有
`=== 变异体 ===` 的规格**，台子 `sys.exit(...)` **杀掉整个进程**
（不是跳过那一条）。

## 两份规格、两个 node

| 规格 | 变异条数 | 目标 node | 杀的是哪一层 |
|---|---|---|---|
| `tests/mutation_specs/test_progress_page_export.mut` | **21**（M1–M19 ＋ D1/U1） | `tests/test_progress_page_export.py`（**53** 条守卫） | 静态 / node 级：结构、内容、CSS 禁令 |
| `tests/mutation_specs/test_progress_page_browser.mut` | **4**（B1–B4） | `tests/e2e/test_progress_page_browser.py`（**14** 条守卫） | 浏览器级：像素、视口矩阵、展开后可见 |

```bash
.venv/bin/python scripts/mutation_harness.py \
  tests/mutation_specs/test_progress_page_export.mut \
  --node tests/test_progress_page_export.py

.venv/bin/python scripts/mutation_harness.py \
  tests/mutation_specs/test_progress_page_browser.mut \
  --node tests/e2e/test_progress_page_browser.py
```

⚠️ **`specs` 是位置参数**（不是 `--spec`，第 174 层栽过一次）
⚠️ **不要把管道放在这条命令上**（第 8/107 层：退出码会变成 `tail` 的）
⚠️ **只有 `rc == 1` 才算被杀**；`rc ∈ {2,3,4,5}` = 不可验证，按存活计入上界
⚠️ **运行期间不得并发跑别的 pytest** —— 台子每一轮都在**真实改写被测文件**
（`scripts/export_progress_page.py`），并发进程 import 到的是变异体（第 173 层）
🚨 **本台会改写 `scripts/export_progress_page.py`** —— 跑完确认文件已还原
（判还原成功**只认 sha256 逐位相等**，不认「替换执行了」；本轮实测
`ea7fbd11…ab79` 跑前跑后一致）

---

## 实测结果（2026-10-21）：**21/21 ＋ 4/4 逐条 `rc = 1`**（基线均已验绿）

### 静态 / node 级（21 条）

| # | 变异 | 被谁杀 |
|---|---|---|
| M1 | 倍数那一族改成取**跨口径**那一列 | 导出器**自己拒收**（`_self_check`）⇒ 27 errors |
| M2 | 封禁字面串的机械替换被摘掉 | 导出器**自己拒收** ⇒ 27 errors |
| M3 | 没有出处的读数不再拒收 | `test_the_exporter_refuses_to_export_a_metric_without_a_source` |
| M4 | 不再借 live 页的配色 | `test_the_static_page_borrows_the_colors_from_the_live_page` |
| M5 | 页面不写快照时间 | 2 条（含 `…_says_the_data_is_frozen_at_the_snapshot_time`） |
| M6 | 页面自称「已达成 20%」 | 导出器**自己拒收** ⇒ 27 errors |
| M7 | 出处行不渲染 | 3 条 |
| M8 | 页面里多一次 `fetch` | `test_the_exported_page_is_self_contained` |
| M9 | 缺口倍数那一列的唯一性检查被摘掉 | `test_the_exporter_refuses_when_the_gap_multiple_column_is_ambiguous` |
| M10 | 少导一条目标（8 → 7） | 2 条 |
| D1 | 推送脚本的目标目录改成**另一个服务**那个目录 | `test_the_deploy_script_only_touches_its_own_directory` |
| U1 | systemd 单元去占 8000 | `test_the_systemd_unit_only_listens_on_8080` |
| **M11** | **两个口径被折进 `<details>`** | `test_the_two_d2_calibers_are_not_inside_any_collapsed_region` |
| **M12** | **紧凑读数行不渲染状态标签** | `test_every_goal_has_its_own_status_label_in_the_compact_row` |
| **M13** | **紧凑读数行不渲染读数（退回散文）** | 2 条 |
| **M14** | **首屏的读数被换成手打的数** | `test_the_d2_headline_numbers_are_snapshot_values_verbatim` |
| **M15** | **一句话结论被写成「未达」** | `test_the_conclusion_and_the_status_labels_never_say_not_reached_or_close` |
| **M16** | **9 条诚实边界只渲染前 2 条** | `test_the_collapsed_sections_keep_every_word_of_the_snapshot` |
| **M17** | **目标行里的读数一块被摘掉** | 4 条 |
| **M18** | **CSS 里加一条 `text-overflow: ellipsis`** | `test_the_page_never_truncates_content_with_ellipsis` |
| **M19** | **文本型读数的值不再渲染 Markdown**（屏上出现裸记号） | `test_the_visible_page_never_shows_raw_markdown_markers` |

### 浏览器级（4 条）

| # | 变异 | 被谁杀 |
|---|---|---|
| **B1** | **8 条目标的折叠体默认展开**（页面从「一屏」变回「一堵墙」） | `test_the_page_height_stays_within_the_declared_budget[×3 视口]` |
| **B2** | **CSS 里加一条固定宽度**（整页被撑出横向滚动条） | `test_the_page_never_scrolls_sideways[×3 视口]` |
| **B3** | **折叠体被加上 `hidden`**（展开后什么都看不见） | `test_opening_every_fold_keeps_every_word` |
| **B4** | **两个口径被折进 `<details>`** | `test_the_first_screen_holds_…[×3 视口]` |

---

## ⚠️ 三条写规格时踩到的坑（写下来给下一个写规格的人）

1. **`rel` 那一列是「被改写的文件」**，不是「跑测试的文件」。
   本轮第一版把 B1–B4 的 `rel` 写成了 `tests/e2e/test_progress_page_browser.py`
   ⇒ 台子去改**测试文件** ⇒ 四条全报「旧串匹配 0 处」。
   ⇒ 台子的输出长这样时，**先查 `rel` 列**。
2. **锚点唯一性要在**改过的源码**上重新数**。
   M14 第一版锚 `esc("缺失" if m.get("display") is None else m.get("display")))`
   在本轮改造后**出现了 2 处**（`goal_line_html` 与 `d2_gap_row_html`）
   ⇒ 台子报「旧串匹配 2 处」⇒ **空操作，绿灯是假的**（第 14 层）。
   ⇒ 修法：锚到**带变量名**的那一份（`met.get(...)` vs `m.get(...)`）。
3. **多行锚点的 pad 会吃掉首行缩进**：
   `% (esc("缺失" if met.get(...` 与 `% esc("缺失" if met.get(...`
   差一个 `(` ⇒ 台子报「旧串匹配 0 处」。
   ⇒ 锚点必须**逐字照抄源码那一行**（含括号、含逗号）。

## ⚠️ 为什么 B3（`hidden`）这条值得单记

「折叠区展开后内容一个字不少」这条守卫，若只判「折叠体的文字还在 DOM 里」，
它是**恒真的**（内容当然还在 DOM 里 —— 被 `display:none` 藏起来也一样在）。
⇒ 浏览器级那条判的是**展开后的 `innerText`**（≈ 可见文本）
⇒ 加一个 `hidden` 就被抓到（实测 rc=1）。
⇒ **静态级那条**（`test_the_collapsed_sections_keep_every_word_of_the_snapshot`）
管的是**另一边**：内容**被删掉**（而不是被藏起来）时它会红。
⇒ **两层合起来才盖住「藏」与「删」两种失效方向。**

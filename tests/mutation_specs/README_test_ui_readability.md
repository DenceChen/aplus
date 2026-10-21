# `test_ui_readability` 的变异规格（2026-10-21 新增）

规格本体在 `test_ui_readability.mut`；这里只放说明。

## 跑法

```bash
.venv/bin/python scripts/mutation_harness.py \
    tests/mutation_specs/test_ui_readability.mut \
    --node tests/test_ui_readability.py
```

基线必须先绿（`21 passed`），台子才会计分。
**规格里没有注释**（第 121 层纪律 ⑥：`.mut` 的块首行必须是「标签`<TAB>`相对路径」，
一个以 `#` 开头的块会让台子**整个进程退出**）。

⚠️ **多行锚点的首行必须写在列 0**：`textwrap.dedent` 取的是**最小**缩进，
首行带缩进 ⇒ 整块被一起剥掉 ⇒ 旧串匹配 0 处 ⇒ **空操作，绿灯是假的**
（本批 M7 / M11 第一次就是这么写的，台子**响亮拒收**了它 ——
这正是「拒收」与「静默跳过」必须分开读的地方）。

## 实测结果：11/11 全杀（基线 21 passed）

## 本批 11 条：每一条在守什么

| # | 变异 | 它模拟的真实缺陷 | 杀掉它的守卫 |
|---|---|---|---|
| M1 | `s-REDUCE` 颜色 `--up` → `--down` | 「**该减仓**」被涂成绿色 | `test_the_four_holding_states_use_the_danger_colours_and_not_the_swapped_ones` |
| M2 | `s-HOLD` 颜色 `--down` → `--up` | 「**拿着没事**」被涂成红色 | 同上 |
| M3 | `s-UNDECIDED` 颜色 `--void` → `--down` | 「测不出」被读成「查过了，没问题」 | 同上 + 浏览器级四态检查 |
| M4 | 宽表 `overflow-x: auto` → `hidden` | 「1 手要 ¥202,070」整列**永久不可达** | 浏览器级「被裁掉且滚不到」×2 + 静态级宽表检查 |
| M5 | `.lot-table th/td` 去掉 `nowrap` | 中文被压成**逐字竖排**（瑞/立/科/密） | 静态级宽表检查 |
| M6 | ≤600px 的 `.th-note` 不再折行 | 名单表放不下 ⇒ 综合分与消息面被裁 | 浏览器级「被裁掉且滚不到」×1 + 静态级窄屏规则检查 |
| M7 | 「持仓降级预警」被排回依据区之后 | 首屏答不完「看什么 / 该动吗」 | `test_the_action_block_sits_right_after_the_daily_list` |
| M8 | 行内 Markdown 的加粗被短接 | 屏上重新出现 **143 处裸星号** | 静态级（node 真跑渲染器）+ 浏览器级（真页面文本） |
| M9 | `is-zero` 的颜色 `--faint` → `--up` | 「减仓 0」红着喊人 | 静态级零计数检查 + 浏览器级四态检查 |
| M10 | `chip()` 的 `k !== 'UNDECIDED'` 被摘掉 | 「测不出」被当成「零」调暗 ⇒ 把未知说成没有 | 浏览器级四态检查 + 静态级「四态要有文字」 |
| M11 | 首屏那两张对照卡又抄一遍第 01 行 | 首屏被重复内容吃掉 ~180px ⇒ 名单一行都看不到 | 浏览器级「对照卡不许有第二行」 |

## ★ 三条**设计上刻意只有一处**的地方

- **M9 / M10 是一对**：颜色规则（CSS）与「哪个状态参与变暗」（渲染层）
  各管一半 ⇒ **两处各写一份「未定不参与变暗」就是两份副本**（第 97 层）。
  ⇒ 守卫 `test_a_zero_count_is_quiet_but_still_visible` 刻意断言
  **CSS 里没有** `.ha-chip.s-UNDECIDED.is-zero` 规则 ⇒ 防止有人把它加回来。
- **M7 的判据钉在文档顺序**（不是「有没有这个块」—— 那是恒真的）。
- **M4 / M5 / M6 三条都只动呈现层**：变异体改的全是 CSS 与 DOM 顺序，
  **一个数字都没动** ⇒ 与「UI 只负责呈现」这条分工一致。

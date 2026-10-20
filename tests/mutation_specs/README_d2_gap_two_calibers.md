# 变异台说明：`tests/test_d2_gap_two_calibers.py`

**散文只允许待在这里。** `.mut` 里既不许写说明也不许写 `#` 注释 ——
`load_specs` 按 `"\n\n"` 切块，一个以 `#` 开头的注释块会被当成一条**没有
`=== 变异体 ===` 的规格**，台子 `sys.exit(...)` **杀掉整个进程**
（不是跳过那一条）。

规格文件：`tests/mutation_specs/test_d2_gap_two_calibers.mut`（16 条，M1–M16）
目标 node：`tests/test_d2_gap_two_calibers.py`（24 条守卫）

```bash
.venv/bin/python scripts/mutation_harness.py \
  tests/mutation_specs/test_d2_gap_two_calibers.mut \
  --node tests/test_d2_gap_two_calibers.py
```

⚠️ **`specs` 是位置参数**（不是 `--spec`，第 174 层栽过一次）
⚠️ **不要把管道放在这条命令上**（第 8/107 层：退出码会变成 `tail` 的）
⚠️ **只有 `rc == 1` 才算被杀**；`rc ∈ {2,3,4,5}` = 不可验证，按存活计入上界
⚠️ **运行期间不得并发跑别的 pytest** —— 台子每一轮都在**真实改写被测文件**，
并发进程 import 到的是变异体（第 173 层）
🚨 **本台会改写 `aplus/web/index.html`** —— 而**另一个 agent 同时在改同一个文件**
⇒ 台子还原时会把**对方的改动一起还原掉** ⇒ 跑完必须确认对方的块还在
（实测 2026-10-21 跑完：`#holdings-alert-body` 仍在，`node --check` 两块内联脚本全绿）。

---

## 实测结果（2026-10-21）：**16/16 逐条 `rc = 1`**

| # | 变异 | 被谁杀 |
|---|---|---|
| M1 | 实盘档缺口被乘 1.01 | `test_the_deployed_gap_is_bit_identical_to_the_artifact_field` 等 4 条 |
| M2 | 实盘档年化漏掉 ×100 | `test_the_deployed_annual_is_the_artifact_cagr_with_only_a_unit_scale` 等 5 条 |
| M3 | 实盘档锚错块（`verdict` → `caliber`） | 9 条（含「两个口径都在」） |
| M4 | 判决里实盘档那一段整段删掉 | `test_the_verdict_carries_both_calibers_each_with_its_own_label` |
| M5 | 实盘档的标签被改成理想档的（撞名） | `test_the_verdict_labels_each_number_with_its_own_caliber` |
| M6 | 实盘档年化的显示精度 4 位 → 2 位 | 4 条 |
| M7 | 首屏只渲染理想档一格 | `test_the_first_screen_renders_both_calibers_side_by_side` 等 2 条 |
| M8 | 首屏那块退回单列 | `test_the_first_screen_block_is_two_columns` |
| M9 | 首屏那块的**接线**被摘掉（函数还在、没人调） | `test_the_first_screen_block_is_actually_wired_into_render_docket` |
| M10 | 「唯一可引用」的顶层键又回来了 | `test_the_only_citable_claim_stays_retired` |
| M11 | 屏上给哪个口径被改成理想档 | `test_the_screen_facing_caliber_is_the_deployed_one` |
| M12 | 判决把「同口径」那一列换成了跨口径那一列 | `test_the_same_caliber_multiple_is_quoted_bit_for_bit` 等 2 条 |
| M13 | 跨口径那个倍数的**口径标签**被摘掉 | `test_the_cross_caliber_multiple_never_appears_unlabeled` 等 2 条 |
| M14 | 投影里不再带 by_caliber 块（前端读不到同口径那一列） | 3 条 |
| M15 | 首屏那块读错了载荷（倍数那一行整行消失） | `test_the_first_screen_block_shows_the_same_caliber_multiple_only` |
| M16 | 「倍数」那一族被塞进缺口口径表（变成第四档） | `test_the_multiple_family_is_not_a_fourth_gap_caliber` |

⚠️ **M4/M5/M9/M10/M11/M15 各只杀 1 条** ⇒ 这几条的判别力是**单点**的
（它们各自只破坏一件事）。这**不是**「守卫太弱」的证据，
而是「那件事只有一条守卫在说」的记录 ⇒ 以后有人删掉那一条，
这几条变异就会**整条存活** ⇒ 变异台会立刻报出来。

🚨 **M10 / M11 / M16 改的是 `scripts/d2_gap_calibers.py`（生成器），不是产物。**
它们之所以杀得掉，是因为那几条守卫量的是**生成器的输出**（`build()`），
**不是**落盘 JSON ⇒ 避免了「读快照的守卫等于没守」（第 96 层）。
⚠️ 代价：`build()` 要跑一次全仓 AST 普查 ⇒ 那几条守卫**慢约 3 秒**
（实测整文件 24 条 ≈ 12 秒）。这是**故意**的：慢的守卫会被跳过，
被跳过的守卫等于没有（第 140 层）。

🚨🚨 **本台会真实改写 `scripts/d2_gap_calibers.py`** ⇒ **M11 改的正是
`which_one_goes_on_the_screen.answer` 里那一句「实盘档」**
⇒ **台子跑的那几秒里，任何并发跑 `test_the_screen_facing_caliber_is_the_deployed_one`
的 pytest 都会读到变异体** ⇒ **看到那条红时先问「是不是与变异台并发」**
（第 173 层：并发跑出来的结果**一律重跑**，不能读成「产物旧了」）。
⇒ 判据：台子跑完后 `scripts/d2_gap_calibers.py` 的 mtime 与 sha256 **回到原值**
⇒ 产物是否陈旧看 **sidecar 的 `code_file_sha256` vs 盘上脚本字节**
（`tests/test_artifact_freshness_discipline.py::G12`，2026-10-21 补了本产物这一条）。

---

## 三条**故意没写**的变异（以及为什么）

### ① 「把字段读改成公式重算」——**等价变异体，永久无判别力**

`deployed_d2_verdict.json::verdict.gap_to_target_pp` = `9.575538229643438`，
而**产物自己**就是按 `(target − cagr) × 100` 算的 ⇒ 实测

```
(target − cagr) × 100  ==  9.575538229643438   ← 与产物逐位相同
20 − cagr × 100         ==  9.575538229643437   ← 差 1 ulp
```

⇒ 把 `out["gap_to_target_pp"] = gap` 改成
`out["gap_to_target_pp"] = (block["target"] - cagr) * 100.0`
**值逐位不变** ⇒ **这条变异无论守卫多严都杀不掉**（第 137 层的「等价变异体」）。

⇒ **处置：换掉那条变异、守卫一个字不动**（第 112 层：给生产补恒真约束
   是最贵的错误修法）。本台用 **M1（乘 1.01）** 代替它 ——
   它改的是**同一个赋值语句**，杀它的守卫与杀 M1 的是**同一条**
   ⇒ M1 与「重算」在守卫层面**不是两条独立证据**（只算一条）。

🚨 **诚实边界**：`test_the_deployed_gap_is_bit_identical_to_the_artifact_field`
证明的是「**载荷里的那个数 == 产物那个数**」，
它**不能**证明「生产代码是**读**出来的而不是**重算**出来的」——
在真产物上这两件事**数值上不可区分**。
⇒ 这一点写进了守卫的 docstring，不是靠猜。

### ② 「把 `× 100` 换成 `÷ 100`」——已被 M2 覆盖

M2（漏掉 ×100）已经杀掉「量纲换算被改掉」这一类。
再写一条 `÷ 100` 的变异**不增加独立证据**（同一行、同一守卫）⇒ 不写。

### ③ 「把理想档的数换成实盘档的」——由**反向正控**覆盖

`test_swapping_the_two_calibers_is_detected` 与
`test_the_first_screen_swapped_calibers_are_detected` 是**夹具层**的对调：
它们把**载荷**对调后真跑一遍生产代码，断言「标签后面是对应那一档的数」
这条**不再成立**。
⇒ 与「改源码的对调」相比，夹具层的对调**不碰生产文件**
⇒ 台子运行时**不会**与另一个 agent 抢同一个文件。

### ④ 「改 `retired_claims` 的记录文本」——**故意没写**

M10 杀的是「键回来」（结构）。而 `retired_claims` 里那段**散文**
（为什么退休、被谁替代）**没有守卫** ⇒ 有人把它改写坏了，
**没有任何东西会红**。
⇒ 这是**已登记的欠账**，不是遗漏：给散文写守卫就是给生产加恒真约束
（第 112 层）。真要守，只能守**结构**（键在不在、指向哪个键），
而那已经由 `test_the_retirement_is_recorded_and_each_caliber_carries_its_k` 做了。

### ⑤ 「把跨口径那一整段从判决里删掉」——**故意没写（它合规）**

协调方的要求是「**若**你要同时显示跨口径那个，**必须**带它的 `is_the_gap = False` 标签」
⇒ **显示它是可选的** ⇒ 删掉整段**不违反**要求 ⇒ 那条变异**应当存活**
⇒ 本台**不写**它（写了却存活，会被读成「守卫有洞」，而它不是）。
⚠️ 但屏上**现在**是显示的（第 25 层：标注作废，不抹掉 —— 它此前就在屏上，
删掉会让看过旧屏的人不知道它去哪了）。
⇒ 这条取舍写在 `docs/D2_GAP_TWO_CALIBERS_2026-10-21.md` §6。

---

## 为什么「反向正控」要**两条**（一层在载荷，一层在首屏）

`test_the_verdict_labels_each_number_with_its_own_caliber` 断言
「**理想档的标签后面是理想档的数，实盘档的标签后面是实盘档的数**」。

⚠️ 只断言「两个数都在」是**不够**的：把两档**对调**同样能通过那一条
⇒ 「不许合成一个数」与「不许跨口径取极值」必须由**配对**判据来守。

而**只守载荷层不够**：首屏那一格是**另一条渲染路径**
（`index.html::renderD2GapCalibers`）⇒ 它自己有独立的对调可能。
⇒ 两层各钉一次。

---

## 这台的**诚实边界**

1. **产物层守卫只对「当前落盘的那份产物」有效**（第 96 层）：
   产物重跑后数值会变，而守卫**从产物读期望值** ⇒ 守卫自动跟着走
   ⇒ 但「源码改了、产物没重跑」这件事本台**看不见**。
2. **首屏那块是「判决板里的一个常驻块」**，不是「判决卡的标题」——
   判决卡的正文在 `<details>` 里（默认折叠）⇒ 缺口数在那里看不见。
   本台守的是**两个地方都有**（判决正文 + 常驻块）。
3. `read_d2_gap` 的实盘档只有**一处**量纲换算（×100）。
   本台用两条断言钉它（`pct == cagr × 100` 与 `pct / 100 == cagr` 往返逐位相等）。
   ⚠️ **往返相等不是恒等式**：它只在**这个**值上成立（实测成立），
   且换个值可能不成立 ⇒ 它的作用是**当场抓出「多了一次变换」**，
   不是**保证**任何值都往返相等。

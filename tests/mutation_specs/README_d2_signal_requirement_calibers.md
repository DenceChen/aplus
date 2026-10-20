# 变异台说明：`tests/test_d2_signal_requirement_calibers.py`

**散文只允许待在这里。** `.mut` 里既不许写说明也不许写 `#` 注释 ——
`load_specs` 按 `"\n\n"` 切块，一个以 `#` 开头的注释块会被当成一条**没有
`=== 变异体 ===` 的规格**，台子 `sys.exit(...)` **杀掉整个进程**
（不是跳过那一条）。

规格文件：`tests/mutation_specs/test_d2_signal_requirement_calibers.mut`（15 条，M1–M15）
目标 node：`tests/test_d2_signal_requirement_calibers.py`（21 条守卫）
被变异的文件：`scripts/d2_signal_requirement.py`（**生成器**，不是产物）

```bash
.venv/bin/python scripts/mutation_harness.py \
  tests/mutation_specs/test_d2_signal_requirement_calibers.mut \
  --node tests/test_d2_signal_requirement_calibers.py
```

⚠️ **`specs` 是位置参数**（不是 `--spec`，第 174 层栽过一次）
⚠️ **不要把管道放在这条命令上**（第 8/107 层：退出码会变成 `tail` 的）
⚠️ **只有 `rc == 1` 才算被杀**；`rc ∈ {2,3,4,5}` = 不可验证，按存活计入上界
⚠️ **运行期间不得并发跑别的 pytest** —— 台子每一轮都在**真实改写被测文件**，
并发进程 import 到的是变异体（第 173 层）

---

## 为什么变异打的是**生成器**而不是产物

这一族守卫是**两层**：

| 层 | 守什么 | 为什么需要它 |
|---|---|---|
| **接线/行为层**（`_rebuilt_block()`） | **import 生成器**并调它的纯函数 | 只读落盘 JSON 的守卫**对当前代码零覆盖**（第 96 层）：改生成器它照样绿 |
| **产物层**（`_block()`） | 落盘那一块的内容与形状 | 守「产物里那个数**逐位**等于上游」与「老的那一列仍是裸 float」 |

⇒ 变异改的是 `scripts/d2_signal_requirement.py` ⇒ **接线层立刻红**；
产物层同时提供第二份独立证据。

---

## 实测结果（2026-10-21）：**15/15 逐条 `rc = 1`**（基线 `21 passed`）

| # | 变异 | 被谁杀 |
|---|---|---|
| M1 | 实测毛超额被乘 1.01 | `test_the_measured_gross_is_read_verbatim_from_the_deployed_artifact` + 产物层对拍 |
| M2 | 生产臂的臂改成 ARM_C | 5 条（含 `test_the_same_caliber_cell_is_the_production_verdict_cell`） |
| M3 | 台账 K 写死成 21（不再跟 `LIVE_REBALANCE_EVERY`） | 6 条 |
| M4 | 倍数被乘 1.01 | `test_the_same_caliber_multiple_is_required_over_measured` + 产物层对拍 |
| M5 | 跨口径那一列被标成「是缺口」 | 产物层对拍 |
| M6 | 跨口径的 K 被抹成台账的 K | `test_the_cross_caliber_column_says_which_caliber_it_is` + 产物层对拍 |
| M7 | 跨口径的分母换成同口径的（两列变同一列） | `test_the_cross_caliber_measured_side_is_the_research_arm_value` + 产物层对拍 |
| M8 | 同口径那一列读的不是生产格（自己挑了一格） | 6 条 |
| M9 | 生产臂的口径自检被摘掉（函数还在、没人调） | `test_the_caliber_checks_are_actually_called_by_the_builder`（**接线层**） |
| M10 | 市场腿自检内部被摘掉（永远不响） | `test_the_market_leg_check_rejects_a_different_market_leg`（反向正控） |
| M11 | 对照臂的信号检查被摘掉 | `test_the_control_arm_check_has_teeth`（反向正控） |
| M12 | 对照臂被塞进生产臂（挑臂的空间） | 6 条 |
| M13 | 同口径倍数用错分母（拿跨口径的实测） | `test_the_same_caliber_multiple_is_required_over_measured` + 产物层对拍 |
| M14 | 块里那份「要求」被乘 1.01 | 产物层对拍 |
| M15 | 生产格的核对被摘掉（落盘路径上不再核对） | `test_main_calls_the_builder_and_the_production_cell_check`（**接线层**） |

### ⚠️ 三条变异被**同一条守卫**杀掉（只算一条证据）

- **M9 / M11 / M15** 都被**接线层**那条 AST 守卫杀掉
  （`test_the_caliber_checks_are_actually_called_by_the_builder` 与
  `test_main_calls_the_builder_and_the_production_cell_check`）：
  它们改的是**不同的行**（builder 里的一处调用 / builder 里的另一处调用 /
  `main` 里的一处调用），但**证据是同一条** ⇒ 记成「接线层这一条守住了」，
  **不拆成三笔**。

### ⚠️ 哪些变异**只**被产物层对拍杀掉

M5 / M6 / M7 / M13 / M14 里有一部分只红在
`test_the_artifact_block_is_exactly_what_the_generator_produces`
（它对拍**生成器的输出**与**落盘的那块**）⇒ 这是**有意**的：
那几条改的是「值」，值一变产物与生成器就对不上 ⇒ 守卫立刻响（第 96 层）。

---

## 三条诚实边界

1. **产物层守卫天然只对历史快照有效** —— 上面「产物层对拍」这一条之所以有牙，
   是因为它**对拍的是生成器现在的输出**（不是「读一遍落盘 JSON」）。
2. **本台只验这一族**（`d2_signal_requirement.py`）；台账的**其他**既有守卫
   （`tests/test_d2_gap_surface.py` 等）**不在本 node 里** ⇒
   本台的「基线绿」**不代表**全仓门禁绿 ⇒ 跑完整门禁要另外跑。
3. **M3（K 写死）会让 `build_multiple_calibers` 直接抛** ⇒ 那几���红的是
   **异常**而不是断言 ⇒ 症状与「守卫抓到了缺陷」同形，但 `rc = 1` 已验明
   它是**真失败**（不是收集错误）。

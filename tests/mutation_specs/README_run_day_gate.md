# 变异台：交易日闸（`tests/test_run_day_gate.py`）

```
.venv/bin/python scripts/mutation_harness.py \
    tests/mutation_specs/test_run_day_gate.mut --node tests/test_run_day_gate.py
```

`specs` 是**位置参数**（没有 `--spec`）；`--node` 是**单文件** ⇒
「目标 node 绿 + 台子全杀」**不构成放行理由**，必须另跑全仓门禁
（`scripts/guard_daily.py` 还被 `tests/test_audit_logging.py:707` 检查）。

**实测：基线 19 passed，7/7 逐条 `rc=1`，无 `rc∈{2,3,4,5}`。**

---

## 🚨 为什么散文在这个文件里而不在 `.mut` 里

`load_specs` 用切块的 `lines[0]` 当 `标签<TAB>相对路径`，
`if "\t" not in head: sys.exit(...)` ⇒
一个以 `#` 开头的注释块会让台子**整个进程退出**，不是跳过那一条。
块内也不许有空行（按 `"\n\n"` 切分）。

---

## 🚨 锚点是怎么切的（写新变异前必读）

三条硬限制，全部实测踩过：

| 限制 | 症状 | 成因 |
|---|---|---|
| **首行必须写到列 0** | 多行锚点 `命中=0`（M1/M2/M6 三条同时中招） | `dedent` 取块内**最小**缩进；照抄源码缩进 ⇒ `pad=4` ⇒ 续行被一起剥 |
| **变异体里嵌的源码必须用 `raw()` 而不是 `cut()`** | `compile()` 假、`IndentationError` ⇒ 判「不可验证」 | `cut()` 已剥过首行 ⇒ 嵌进去顶掉源码自己的 4 格 |
| **块内不许有注释行** | 台子 `sys.exit` | 同上第一条 |

⇒ 生成脚本里两个函数分工：`cut()` 供锚点（首行顶格），`raw()` 供变异体（原缩进）。

---

## 逐条：改的是哪一行 / 为什么该被谁杀

台子只印 `✅/❌`，**不会告诉你它抓的是不是它该抓的那个东西**
（第 172 层）。下表是人工核对，须与实测输出逐条对照。

| 标签 | 改动落在哪一行 | 预期杀手 | 实测 |
|---|---|---|---|
| **M1** `gate-moved-before-update` | `run_daily.py:1108-1109`（闸整块插到 update **之前**） | `…gate_runs_after_the_update_and_before_the_verify` | ✅ 1 failed |
| **M2** `gate-removed-entirely` | `run_daily.py:1111-1114`（闸整块删除，只剩注释） | 探针正控 / 位置 / none_check | ✅ 3 failed |
| **M3** `gate-connected-but-not-blocking` | `run_daily.py:1113`（`if _gated is not None:` → `if False and …`） | **只有** `…raised_under_exactly_the_none_check` | ✅ 1 failed |
| **M4** `rc-always-zero` | `run_daily.py:1079`（`rc = 0 if verdict == … else 2` → `rc = 0`） | `test_b_a_workday_absent_from_the_store_exits_two` | ✅ 1 failed |
| **M5** `three-states-collapse-to-two` | `aplus/trading_calendar.py:129`（`weekday() >= 5` → `if True:`） | A 组 2 条 + B 组 2 条 | ✅ 4 failed |
| **M6** `1930-guard-gate-removed` | `guard_daily.py:32-35`（闸整块删除） | 19:30 三条 | ✅ 3 failed |
| **M7** `1930-skips-every-non-trading-day` | `guard_daily.py:33`（只豁免 `WEEKEND` → 非交易日都跳过） | **只有** `…only_exempts_the_weekend` | ✅ 1 failed |

### 为什么 M3 与 M7 要写成语义短接而不是语法炸弹

`if _gated is not None:` → 顶格 `if False:` 会让变异体 `IndentationError`
⇒ `compile()` 判「不可验证」⇒ **这条变异永久失去判别力**，而台子只印一行
`⚠️ 不可验证`，读起来像「这条变异不重要」（第 122 层：不可验证 = **证据作废**，不许绕过）。

### 🚨 M7 必须写字面量 `"TRADING_DAY"` 而不是符号名 `RUN_DAY_TRADING`

`guard_daily.py` 只 `from aplus.trading_calendar import RUN_DAY_WEEKEND, classify_run_day, trading_days`
⇒ 没 import `RUN_DAY_TRADING` ⇒ 写符号名会在**运行期** `NameError`，
而那个 traceback 与「守卫真抓到」**逐位同形**（第 121 层）。
`RUN_DAY_TRADING == "TRADING_DAY"`（`trading_calendar.py:93`）⇒ 语义逐位相同。

⇒ ⚠️ **不要为了「统一风格」把它改回符号名。**

### M1 与 M3 是两条独立证据

改的是**不同的行**（位置 vs 条件）、不共用锚点 ⇒ 各算一条。
若两条落到同一行，只算一条证据，不拆成两笔。

---

## 这批变异覆盖不到的（诚实边界）

- **`_pipeline` 的编排层**：判据是 AST 取行号，钉的是「三个调用点的相对位置」，
  **不是**「`main()` 里多条命中怎么拼」—— 那部分零覆盖。
- **产物层**：`run_status.json` 的内容由 B 组在 `tmp_path` 上验，
  而生产产物 `data/reports/run_status.json` 的新鲜度**没有任何东西守**（第 96 层）。
- **`guard_daily.py` 的运行期**：`main()` 一天真跑一次且会 `subprocess.run` 真的补跑
  ⇒ 绝不跑它来验证。

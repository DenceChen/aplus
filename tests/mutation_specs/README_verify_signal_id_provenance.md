# 变异台：`signal_id` 的来源不许被伪造（2026-10-21）

被测节点：`tests/test_verify_signal_id_provenance.py`
（7 条守卫：G-A 接线 ×2 · G-B 行为 · G-C 行为 · G-D 对账 ×2 · G-E 口径）

```bash
.venv/bin/python scripts/mutation_harness.py \
    tests/mutation_specs/test_verify_signal_id_provenance.mut \
    --node tests/test_verify_signal_id_provenance.py
```

## 为什么有这四条变异

预注册 `docs/PREREG_FIX_RESIGNAL_ID_2026-10-21.md` §4 给的是**修复前**的代码形态
（`verify.py` 里还有 `signal_id` 入参、还有「整批没有该列 ⇒ 用入参补上」那一支）。
本轮按预注册 R2 **删掉了那个入参** ⇒ 预注册的 M2/M3 原文在修好后的代码里
**没有对应物**（旧串匹配 0 处 ⇒ 空操作 ⇒ 绿灯是假的，仓规第 122 层）。
⇒ **按「变异要打的仍是它声称的那个东西」重写**（不是放宽锚点，也不是删掉这条蒙过去）：

| 变异 | 预注册原文 | 本轮写法 | 为什么等价 |
|---|---|---|---|
| **M1** | `run_daily.py:481` 还原成 `signal_id=current_signal_id(reports_dir)` | **逐字照旧** | 无需改写 |
| **M2** | `verify.py:275` 改成**无条件覆盖**（去掉 `if SIGNAL_ID_COLUMN not in out.columns` 那一层） | 注入 `out[SIGNAL_ID_COLUMN] = "MUTLABEL"` | 修好后函数**没有入参** ⇒「无条件覆盖」的**语义**只剩一个：**给每一行盖一个不是它自己的标签**。M2 就是这个语义 |
| **M3** | 把「已有该列就保留」改成「总是用入参覆盖」 | 调用点**事后**落一行 `results["signal_id"] = current_signal_id(reports_dir)` | 「调用方的标签总是赢」——预注册盯的是**这个效果**，不是**哪一行**实现的。修好后入参已删 ⇒ 缺陷只可能从**另一扇门**回来（调用点自己盖）。M1 与 M3 **不同行** ⇒ 两条独立证据 |
| **M4** | （预注册未给） | `scope_ledger` 的 `NO_MATCHING_SIGNAL` 塌成 `MISSING_SIGNAL_ID_COLUMN` | 补的：**G-E 在预注册的三条变异里没有判别力** ⇒ 「守卫只有变异后算数」⇒ 补一条，证明 G-E 的四态不是装饰 |

## 变异纪律（本台已强制）

- 基线必须先绿（`run()` 第一步）⇒ 基线红 ⇒ 本次全部记分无效；
- 旧串必须**恰好命中 1 处**；
- 写盘后 sha256 必须变；还原后必须逐位回到基线；
- 变异体必须过 `compile()`（语法非法的变异体不是实验，它只制造信用）；
- **只有 `rc == 1` 才算被杀**（rc ∈ {2,3,4,5} = 不可验证 ⇒ 按存活计入上界）。

## ⚠️ 运行期间不许并发跑别的 pytest

本台会**真的改写** `scripts/run_daily.py` / `aplus/verify.py` / `aplus/signal_scope.py`
⇒ 并发的 pytest 会读到**变异体** ⇒ 它报出来的红**是真的红**（代码当时确实是错的），
但**错**是台子制造的 ⇒ 人会把它归因成「仓里有个 bug」⇒ 去修一段本来正确的代码。
⇒ 跑之前先确认没有别的 pytest 在飞。

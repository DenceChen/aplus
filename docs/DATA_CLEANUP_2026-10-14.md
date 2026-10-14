# 数据清理记录 · 2026-10-14

任务：**梳理代码文件逻辑，删除无用数据文件**。

本文件的用途是**可审计的删除凭证**：每删一份东西，这里写着它的路径、体积、
mtime、判定依据，以及**用什么命令复现这个依据**。
删掉的东西不能再删第二次，所以凭证必须先于删除存在。

---

## 一、结论摘要

| 动作 | 对象 | 体积 | 依据强度 |
|---|---|---|---|
| **删** | `_backup_tailcols_*` ×3 | **352 MB** | 4068/4068 文件被主库支配 + 值级差异集中在单日 |
| **删** | `_backup_gapfill_*` ×4 | **48 MB** | 备份 schema 更旧（因子表已独立成表） |
| **删** | 一次性 `*.log` ×23 | 890 KB | 10-10/10-11 的手工运行 stdout，无下游消费者 |
| **删** | `__pycache__` ×5 / `.DS_Store` ×4 | ~28 MB | 纯再生 |
| **留** | `_backup_quarantine_recovery_20261011` | 884 KB | 存 `quarantine_reason`——13 只票被隔离的**唯一原因记录** |
| **留** | `_backup_pre_weekly` / `_backup_predictions_121936` | 212 KB | 来源不明（0 代码引用），体积极小，保留待查 |
| **留** | `data/reports/*.json` ×62 | 2 MB | **研究结论唯一真相源**，50 份被文档/代码引用 |

`data/`：**1.1 GB → 696 MB**。

---

## 二、`_backup_tailcols_*`：352 MB 为什么可以删

### 2.1 结构支配（4068 个文件逐个比对）

```bash
.venv/bin/python -c "
import pyarrow.parquet as pq; from pathlib import Path
for b in sorted(Path('data/_backup_tailcols_20261012-082806/daily').glob('*.parquet')):
    m = Path('data/master/daily')/b.name
    fb, fm = pq.ParquetFile(b), pq.ParquetFile(m)
    assert not (set(fb.schema_arrow.names)-set(fm.schema_arrow.names))
    assert fm.metadata.num_rows >= fb.metadata.num_rows"
```

结果：3101 + 960 + 7 = **4068 个文件全部满足**「主库不缺股、不缺列、不少行」。

### 2.2 值级差异只集中在一天

结构支配**不等于**数值支配 —— 修复脚本也可能**改写已有行**。
于是做了值级比对（重叠日期 × 重叠单元格）：

```
总单元格 224829，一致 224761，不一致 68
```

68 处不一致是什么？进一步定位：

```
=== 被改写的日期分布（抽样 120 只）===
  2026-10-09  120 只          ← 120/120 全部只在这一天
```

**全宇宙 2026-10-09 一天的陈旧数据。** 成因清楚：10-09 首次抓取后上游做了追溯修订，
10-12 跑 `repair_tail_cols.py` 时经 `sync_daily` 重取该日并覆盖。
改写列分布 `amount/tradestatus/isST/turn/pctChg` 恰好就是「一整行」。

### 2.3 删除前先落清单

★ **352 MB 换的是一天的数据，所以先把「哪一天、哪些股票、哪些列被改过」落成文件**：

`data/reports/restatement_20261012_manifest.json`（1.6 MB，4068 条记录）

```
restated_dates: ["2026-10-09"]
root_cause:     上游对 2026-10-09 全市场做了追溯修订 …
sample_evidence: 抽样 120/120 只，差异日期集合均为 {2026-10-09}
```

**信息保住了，体积放掉了。**

### 2.4 无代码读取这些路径

```bash
grep -rn --include="*.py" '_backup_tailcols\|_backup_gapfill' aplus/ scripts/
```
只有写入点（`repair_tail_cols.py:118`、`backfill_gaps.py:176`），没有读取点。

---

## 三、`_backup_gapfill_*`：48 MB

备份里带 `adjust_factor` 列（1720 个文件），主库**没有** —— 方向与 tailcols 相反：

```
备份独有列:  adjust_factor  ×1720
主库新增列:  isST/amount/tradestatus/turn/pctChg/open/high/code/close/volume/low
```

这是 **schema 演进**：因子后来独立成 `data/master/factor/`（5556 张表，
列为 `date, adjust_factor`），日线不再重复携带。

⇒ 备份是**更旧 schema 的快照**，不是更新的数据。同样无读取点，同样可删。

### 附：一个查证中被推翻的假设

盘点过程中发现 `daily` 有而 `factor` 缺的股票 **3 只**
（`002499.SZ` / `600265.SH` / `600385.SH`），且都在 13 只隔离股名单里。
当时推断「缺因子 ⇒ 后复权退化成不复权 ⇒ 除权日造出假跳空」。
拿供应商自己的 `pctChg` 对账后**推翻**：

```
002499.SZ  2023-03-27  收盘价算出 -52.38%   pctChg -52.38%   差额 +0.00pp
600385.SH  2022-06-10  收盘价算出 -71.77%   pctChg -71.77%   差额 +0.00pp
```

`pctChg` 与收盘价序列**完全一致** ⇒ 主库存的就是已复权价，`factor=1.0` 恰好正确。
⇒ 这不是缺陷，是结构不对称（`recover_quarantine.py:48-75` 只恢复日线、不碰因子表）。

★ 记在这里是因为「推翻自己」和「证实自己」同样需要留下凭证。

---

## 四、日志：只删陈旧的，保留 launchd 活日志

`data/*.log` 共 32 个。删 23 个（**10-10 / 10-11** 的一次性手工运行捕获：
`bootstrap_full.log` 595 KB、`web_agent.err.log` 74 KB、`evolve_race.log` 51 KB …），
**保留 7 个 10-12 及以后**的，因为它们是 launchd 常驻进程的现役日志，
而本项目的目标之一就是「完善日志系统好排查问题」。

> 已知问题（**本次刻意不修**）：`aplus/log.py:203-213 rotate_logs()` 只轮转
> `data/logs/*.jsonl`，launchd 写到 `data/*.log` 的这些**从不轮转**，会无限增长。
>
> 为什么不在本次顺手修掉：Unix 上 `unlink()` 一个**正被进程持有的 fd 打开的文件**，
> 磁盘空间不会释放（inode 活到最后一个 fd 关闭），等于「删了但没省」，
> 还会让正在写日志的进程静默丢失后续输出。
> 正确做法是 `truncate` 到 0 字节而不是 `unlink`。
> 而 `rotate_logs` 现在对 `logs/*.jsonl` 做的确实是 `unlink`（那里安全，
> 因为每轮都是新开文件），把它泛化到 `data/*.log` **必须换实现方式**，
> 这是一个独立的行为变更，不该混进一次清理任务里。
>
> ⇒ 清理只把存量降下来；机制问题单列。

---

## 五、★ 最重要的一个改动：把真相源纳入版本控制

删数据之前先量了「删了能不能恢复」：

```bash
$ git ls-files data/ | wc -l
0
$ git log --oneline -- data/ | head -3
（空）
$ git check-ignore -v data/reports/significance.json
.gitignore:2:data/    data/reports/significance.json
```

**62 份研究结论全部只存在于这块盘上，换机即全部丢失。**
其中 50 份被 `docs/` `aplus/` `scripts/` `tests/` `CLAUDE.md` 引用 ——
文档里写着「证据是 `significance.json`」，而那个文件**没有任何版本历史**。

⇒ 在「删除无用数据文件」这个任务里，**这才是先决条件**：
没有版本控制，任何删除都是永久删除。

### 改动

`.gitignore` 新增一组破例规则，把 `data/reports/*.json`（62 份 / 2 MB）纳入追踪。

⚠️ **第一版写错了，`git check-ignore` 立刻抓出来**：

```gitignore
data/          # ← 排除的是「目录本身」
!data/reports/ # ← 够不着
```

gitignore **不会进被排除的目录**，所以破例规则静默失效，
62 份全部仍显示「被忽略」。必须写成 `data/*`（排除条目而非目录）才逐层放开：

```gitignore
data/*
!data/reports/
data/reports/*
!data/reports/*.json
```

**验证方式不是读规则文本，是问 git：**

```bash
git check-ignore -q data/reports/significance.json   # 期望：非零（不忽略）
git check-ignore -q data/reports/backtest.parquet    # 期望：零（仍忽略）
git add -An data/ | wc -l                            # 期望：62
```

---

## 六、没有删的东西，以及为什么

| 对象 | 不删的理由 |
|---|---|
| `data/reports/*.json`（62 份） | 研究结论的唯一真相源。12 份「无人引用」里 4 份是边车（**必须留**，见 MISTAKE_LOG 第 74 层），`evolution_race_v2/v3/v4` 是否被 v4 取代属于晋级历史，**没查证就不许猜着删** |
| `_backup_quarantine_recovery_20261011` | 存 `quarantine_reason` 列 —— 13 只票当初为什么被踢出主库的**唯一记录**。行情数据主库都有，但判断依据只此一份 |
| `weights.json` / `evolution_log.json` / `backtest_summary.json` | 铁律：任何 agent 不得改动 |
| `data/master/factor/` 里 99 张孤儿表 | factor 有、daily 没有。删了省不了多少字节，且未确认 `load_factors_map` 的行为 |
| 7 个 launchd 活日志 | 现役排障依赖 |

---

## 七、这次顺手修掉的三个产品缺陷

盘点时的 grep 顺带暴露了同一形状的三个 bug（详见 MISTAKE_LOG 第 74/75 层）：

1. **`aplus/factor_ic.py:_find_ic_report`** — `sorted(glob("factor_ic*.json"))[-1]`
   选中 provenance 边车（边车名是主文件名的超长前缀，字典序必然更大），
   导致概览页「符号已按实测 IC 校正」**没有数据支撑**，5 条守它的测试集体 skip。
   修复后那 5 条测试**跑起来并通过了**。
2. **`aplus/champion_health.py` / `aplus/webapp.py`** — 同形状的另外两处未防护 glob。
3. **`tests/test_docket_warning.py:198`** — `assert "verdict.js" in html` 被 5 处**注释**喂饱，
   把 `<script src>` 改成不存在的文件后 82 个测试依然全绿。

修复方式不是补第 3、第 4 个 `if`，而是把「排除边车」收进
**`aplus/provenance.report_jsons()`** 单一实现，四个调用点统一走它，
并由 `tests/test_report_sidecar_selection.py`（7 条，全部变异验证过）钉住。

---

## 八、本次清理自己犯的三个测量错误

删数据这件事，最危险的错误是**测量本身错了**。本次全部当场抓出：

1. **`grep -rql` 让计数恒为 0** — `-q` 只给退出码不给文件名，
   `grep -rql … | wc -l` 于是永远是 0，61 份报告被全列为「无人引用」。
   去掉 `-q` 后真实值是 12。
2. **`.gitignore` 写 `data/` 而不是 `data/*`** — 破例规则静默失效，
   靠 `git check-ignore` 抓出，而不是靠读规则文本。
3. **盘点 agent 提出「用一个 0 字节的清单替代它声称要保护的副本」** — 它看到的是
   中间态；我核对时序后确认清单已完整落盘（1.6 MB / 4068 条）才执行删除。
   **但这条质疑指出的风险窗口是真实的**：我的流程确实存在
   「清单尚未写完、备份已被删」的缝隙。下次应当**先等清单非空并校验 JSON 可解析**，
   再动删除，而不是靠外部 Monitor 的通知。

★ 通则：**先量，后删；量错了当场推翻自己，并把推翻也记下来。**

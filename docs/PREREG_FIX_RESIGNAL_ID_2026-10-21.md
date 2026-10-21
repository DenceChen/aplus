# 预注册：修 `verify.py` 重判路径给旧名单盖当前 `signal_id`（2026-10-21）

> **状态**：🟡 **预注册已写，代码未动**（本轮任务边界 = **只读调查 + 写文档**）
> **缺陷登记**：`docs/TODO_VERIFY_RESIGNAL_ID_2026-10-21.md` §1–§4（原文）
> **根因报告**：同文件 **§5**（2026-10-21 只读调查补记）
> **本文件是「怎么修」的预注册，不是「修好了」的报告。**
>
> ⚠️ **本轮不许动的东西一律列在 §5**（W 的门槛 10 / `scope_ledger` 四态 / 今晚那批产物）。

---

## §1 修法（两条，缺一不可）

### R1（主修）重判路径**不许**再喂「当前信号 id」

**要改的地方**：`scripts/run_daily.py:479-481`

```python
results = verify_predictions(preds, bars, require_cost=True,
                             default_cost=STOCK_COST_DEFAULT,
                             signal_id=current_signal_id(reports_dir))   # ← 删掉这个实参
```

**为什么删掉就对了**（不用新机制，用已有机制）：

- 每一行「自己是用哪套信号排出来的」这个真相，**唯一的载体是那份预测文件自带的
  `signal_id` 列**（`aplus/reports.py::save_predictions` 在**出单当天**写进去）。
- `save_predictions` 写 `signal_id` 是从 **2026-10-21**（commit `c4beabe`）才有的
  ⇒ **在此之前落盘的预测文件里没有这一列**（实测：11 份预测文件里只有
  `pred_2026-10-21.parquet` 有）。
- ⇒ 对**没有**该列的旧文件，重判出来的行**必须没有** `signal_id`
  ⇒ `aplus/signal_scope.py` 判它们「不可归属」
  ⇒ **它们被排除在当前信号的作用域之外** ⇒ **这才是真相**：
  那些名单是旧信号排的，**不属于当前信号**。

⇒ **正常路径不受影响**：今天出单、明天判定时，预测文件**自带** `signal_id`
⇒ `verify_predictions` 保留它（`aplus/verify.py:275-277` 的「已有该列就保留」）
⇒ 归属照旧成立。
⇒ **反向正控**：R1 落地后，**新出单的行仍然带正确标签**（否则会把整条前瞻链打死）。

### R2（收口）`verify_predictions` 的 `signal_id` 入参**不许**用来「补列」

**要改的地方**：`aplus/verify.py:275-277`

```python
if SIGNAL_ID_COLUMN not in out.columns:
    if signal_id is not None and str(signal_id).strip():
        out[SIGNAL_ID_COLUMN] = str(signal_id).strip()
```

**要改成**：**只保留「已有该列就保留」这一支**；
「整批没有 ⇒ 用入参补上」这一支**删掉**。
⇒ 该函数**从「可以造标签」变成「只能透传标签」**。

**为什么 R2 不是多余的**：R1 只是**调用点**不再传参；
只要这个入参还在，**下一个调用点**（或将来的人）还会传 ⇒
缺陷会**原样复发**（第 97 层：两份副本之间没有守卫 ⇒ 改一处就静默分叉）。
⇒ **R1 堵住今天这一条路，R2 让那条路本身不存在。**

⚠️ **R2 的取舍要说清**：删掉入参 ⇒ `verify_predictions` 的 `signal_id` 参数
**要么删掉、要么改成「只校验不补」**。本预注册**不裁定**这一点
（属于实现细节，由动手的人按「最少惊喜 + 不留旁路」的原则选），
但**两条都受同一条守卫覆盖**（§4 的 G-A/G-C）。

### R3（可选，本预注册**不要求**）把「这份名单属于哪个信号」**显式落进底稿**

如果将来想给**旧**名单也标上它自己的信号 id，**唯一正确的来源**是
**它自己那份预测文件**（`data/reports/predictions/pred_<date>.parquet` 的
`signal_id` 列）⇒ 而 10-10 那份文件**没有这一列** ⇒
**没有任何正确来源** ⇒ **10-10 那 20 行在可预见的将来都应当「不可归属」。**

⇒ 🚨 **明确禁止**：把 10-10 那 20 行的 `signal_id` **手工改成 `da18a166`**
（= 翻转前权重的指纹）—— 那是**事后编造标签**（第 25 层）。
`da18a166` 是**从 git 反推出来的**，不是**从落盘文件读出来的** ⇒
它没有证据支撑。**正确的处置是让它们不可归属。**

---

## §2 判据与口径：**从哪读**（🚨 不许手打）

| 量 | 唯一真相源 | 消费侧读法 |
|---|---|---|
| 验收门槛 **W ≥ 10** | `scripts/score_ranking_power.py::MIN_INDEPENDENT_WINDOWS`（`:437`） | 读**这个常量**，不许在测试/文档里写死 `10` |
| W 的**读数** | `data/reports/score_ranking_power.json::by_score_col.score.independence_caliber.n_entry_dates` | 读**产物字段**，不许用公式重算 |
| 归属**四态**与判定顺序 | `aplus/signal_scope.py::scope_ledger` + `STATUS_*` / `BASIS_*` | **只调用**，不许在测试里重写一份四态 |
| **当前**信号 id | `aplus/reports.py::current_signal_id`（唯一真相源） | **只调用**（`aplus/signal_scope.py` 已经这么做了） |
| **某一行**的信号 id（真相） | **该 `pred_date` 的预测文件自带的 `signal_id` 列** | 逐行对拍，见 §4 的 **G-D** |
| 预测文件里 `signal_id` 列的**列名** | `aplus/reports.py::SIGNAL_ID_COLUMN` ≡ `aplus/verify.py::SIGNAL_ID_COLUMN` | 已有守卫钉（`tests/test_production_config_2026_10_21.py::test_the_signal_id_column_name_is_identical_in_both_modules`） |

⚠️ **`da18a166` 这个数**（翻转前权重的指纹）**只允许出现在「诊断/取证」语境**，
**不许**出现在任何守卫的期望值里（守卫的期望值必须来自**落盘文件**，不是来自 git 反推）。

---

## §3 判别实验的复现步骤（下一个 agent 照着能重跑）

**前置**：`.venv/bin/python`；**只读**；不需要 GB 级面板（**不建面板**）。

### 步骤 1 —— 判别实验 A/B：文件里有没有 `signal_id`

```bash
cd /Users/airboom/opencode/aplus
.venv/bin/python - <<'PY'
import pandas as pd, glob, os, datetime
for p in sorted(glob.glob('data/reports/predictions/pred_*.parquet')):
    df = pd.read_parquet(p)
    mt = datetime.datetime.fromtimestamp(os.path.getmtime(p)).strftime('%Y-%m-%d %H:%M:%S')
    print(f"{os.path.basename(p):26s} mtime={mt} rows={len(df):3d} "
          f"signal_id={'YES ' + str(df['signal_id'].unique()) if 'signal_id' in df.columns else 'NO'}")
PY
```

**期望（2026-10-21 实测）**：**只有 `pred_2026-10-21.parquet` 有 `signal_id`**
（值 `['0df5828c']`），其余 10 份**都没有**。
⇒ `pred_2026-10-10.parquet` **没有** ⇒ 底稿里那个标签**不可能**是从文件读出来的。

### 步骤 2 —— 判别实验 C：重判路径盖的是哪个 id

```bash
.venv/bin/python - <<'PY'
import sys; sys.path.insert(0, "/Users/airboom/opencode/aplus")
import pandas as pd
from aplus.reports import current_signal_id, load_predictions
from aplus.verify import verify_predictions
RD = "data/reports"
led = pd.read_parquet(f"{RD}/verification.parquet")
l10 = led[led["pred_date"] == "2026-10-10"]
up, down = load_predictions(RD, "2026-10-10")
preds = pd.concat([up, down], ignore_index=True)
bars = pd.DataFrame({"code": l10["code"].to_numpy(), "prev_close": 1.0,
                      "close": 1.0 + l10["gross_pct"].to_numpy()})
sid = current_signal_id(RD)
print("current_signal_id =", sid)
out = verify_predictions(preds, bars, require_cost=True, default_cost=0.003, signal_id=sid)
print("盖上去的 signal_id =", out["signal_id"].unique())
print("与底稿逐位相等 ?", list(out["signal_id"]) == list(l10["signal_id"]))
PY
```

**期望**：`0df5828c`；与底稿**逐位相等** ⇒ **重判路径就是产出它的那条**。

### 步骤 3 —— 判别实验 D：它**不是**当时那套权重的 id

```bash
.venv/bin/python - <<'PY'
import sys; sys.path.insert(0, "/Users/airboom/opencode/aplus")
from aplus.reports import signal_fingerprint
OLD = {"mom20": 1.0, "rev5": 0.3, "vol20": 0.5, "vsur": 0.5}   # git show d9b0dc9^:aplus/factor.py:14
NEW = {"mom20": -1.0, "rev5": 0.3, "vol20": -0.5, "vsur": -0.5} # git show d9b0dc9:aplus/factor.py:54
print("旧权重 id =", signal_fingerprint({"weights": OLD, "top_n": 10, "bottom_n": 10}))
print("当前权重 id =", signal_fingerprint({"weights": NEW, "top_n": 10, "bottom_n": 10}))
PY
```

**期望**：旧 = `da18a166`、当前 = `0df5828c`；底稿里 10-10 那批 = `0df5828c`
⇒ **等于当前、不等于当时**。

### 步骤 4 —— 承重证据：`mom20` 极性（**不依赖任何近似**）

```bash
.venv/bin/python - <<'PY'
import pandas as pd
for d in ("2026-10-10", "2026-10-19", "2026-10-21"):
    df = pd.read_parquet(f"data/reports/predictions/pred_{d}.parquet")
    up = df[df["direction"] == "up"]
    print(d, "涨腿 mom20:", [round(float(x), 4) for x in up["mom20"]])
PY
```

**期望**：`2026-10-10` **全正**（+0.0848 ~ +9.0372 ⇒ 旧权重 `mom20=+1.0`）；
`2026-10-19` / `2026-10-21` **全负**（−0.29 ~ −0.48 ⇒ 当前权重 `mom20=−1.0`）。

### 步骤 5 —— 修复后的期望读数（**改完必须复跑这一条**）

```bash
.venv/bin/python - <<'PY'
import sys; sys.path.insert(0, "/Users/airboom/opencode/aplus")
import pandas as pd
from aplus.signal_scope import scope_ledger
from aplus.reports import current_signal_id
RD = "data/reports"
led = pd.read_parquet(f"{RD}/verification.parquet")
sc = scope_ledger(led, current_signal_id(RD), None)
print("status =", sc.status, "| rows_in_scope =", sc.coverage["rows_in_scope"],
      "| W =", sc.coverage["entry_dates_in_scope"],
      "| rows_unattributed =", sc.coverage["rows_unattributed"])
PY
```

**期望（修好 + 底稿被下一轮重判覆盖之后）**：
`status=NO_MATCHING_SIGNAL`（若当前信号的行也还没被重判过）或 `status=OK` 且
`rows_in_scope=20` / **`W=1`** / `rows_unattributed=160`。
🚨 **判据是 `W=1`（10-10 那批**不在**作用域里）**，不是 `W=2`。

---

## §4 守卫形态（**守卫只有变异后算数**）

### G-A（接线层，AST）重判路径**不许**出现 `signal_id=` 实参

- 锚点：`scripts/run_daily.py` 里**那个** `verify_predictions` 调用点
  （**不是**全文子串 —— 第 13 条：子串会被注释满足）。
- 判据：解析 AST，找到 `verify_predictions(...)` 的调用点 ⇒
  **它的 `keywords` 里不许有 `signal_id`**。
- ⚠️ **锚定方式**：用**语法位置**（`ast.Call` 的 `func.id == "verify_predictions"`）
  + **实参表**，不用 `"signal_id=current_signal_id"` 这种字面串
  （改了变量名就骗过了 ⇒ 那是「只钉存在不钉性质」）。

### G-B（行为层）**造一行旧 `signal_id` 的底稿 ⇒ 重判后必须仍是旧的**

- 造一份**带** `signal_id='OLD12345'` 的预测文件
  （`save_predictions(..., signal_id='OLD12345')`），
  再走**重判路径**（`verify_predictions` + `append_verification`）。
- 判据：底稿里那些行的 `signal_id` **必须仍是 `OLD12345`**。
- ⚠️ **这条守卫今天就是绿的**（`verify.py:275-277` 的「已有该列就保留」已经满足）
  ⇒ 它**不是**抓今天这个缺陷的那条 ⇒ 它防的是**别的实现**把这条路径改成「无条件覆盖」。
  **必须配一条变异（M2，见下）才有牙**。

### G-C（行为层）**没有 `signal_id` 列的预测文件 ⇒ 重判后那一批不许有这一列**

- 造一份**不带** `signal_id` 列的预测文件（= 10-10 的真实情形），走重判路径。
- 判据：底稿里那些行**没有** `signal_id` 列，或该列的值为空 ⇒ **不可归属**。
- 🚨 **这条才是抓今天这个缺陷的那条**（现状：会盖上 `0df5828c` ⇒ 红）。

### G-D（对账层，**只读，钉真实底稿**）**底稿每一行的 `signal_id` 必须逐行等于该 `pred_date` 的预测文件自带的 `signal_id`**

- 判据（伪码，判据里的数字**不许手打**）：

  ```
  for 每个 (pred_date, code) 行 r in verification.parquet:
      f = predictions/pred_{r.pred_date}.parquet
      if f 有 signal_id 列:
          assert r.signal_id == f[r.code 的那一行].signal_id
      else:
          assert r.signal_id 缺失或为空
  ```

- ⇒ 这条**不依赖任何 git 反推、不依赖任何手打常数**
  ⇒ 它对**任何一次重判**都成立 ⇒ **今晚这一批就会红**（现状：10-10 那 20 行红）。
- ⚠️ **这条守卫天生是红的**（对当前底稿）⇒ 按仓规，
  **守卫应当在待修产物面前保持红，而不是替它开绿灯**。
  ⇒ 底稿被下一轮重判覆盖之后它**才会转绿** ⇒ **这就是「修好了」的判据**。

### G-E（口径层）`scope_ledger` 的四态**不许**被这次修改动过

- 判据：`aplus/signal_scope.py` 的 `STATUS_*` / `BASIS_*` 常量与
  `scope_ledger` 的判定顺序**逐字未变**（diff 断言 / 行为断言）。
- ⇒ 防止「为了让 W 变好看而改四态」。

### 变异台（**每条守卫都要有独立判别力**）

| 变异 | 改哪一行 | 必须杀掉 |
|---|---|---|
| **M1** | `scripts/run_daily.py:481` 还原成 `signal_id=current_signal_id(reports_dir)` | G-A（红）、G-C（红）、G-D（红） |
| **M2** | `aplus/verify.py:275` 改成**无条件覆盖**（去掉 `if SIGNAL_ID_COLUMN not in out.columns` 那一层） | G-B（红） |
| **M3** | 把 `verify.py` 里「已有该列就保留」改成「总是用入参覆盖」 | G-B（红）、G-D（红） |

⚠️ **变异纪律**（第 121/122/164 层）：
① 变异体**先过 `compile()`**（语法非法的变异体不是实验，它只制造信用）；
② **只有 `rc == 1` 才算被杀**（`rc ∈ {2,3,4,5}` = 不可验证 ⇒ 按存活计入上界）；
③ 变异**必须确认落盘**（sha256 真变了 + 标记串恰好 1 次，**不用子串**）；
④ 变异**不许与别的变异共用同一行**（那只能算一条证据）。

---

## §5 🚨 不许做什么（硬约束）

1. 🚨 **不许改 W 的门槛 10**（`scripts/score_ranking_power.py::MIN_INDEPENDENT_WINDOWS`）。
   ⇒ **缺陷的方向是「W 被算大了」**（2 而真值是 1）⇒ 改门槛只会让缺陷更贵。
2. 🚨 **不许改 `aplus/signal_scope.py::scope_ledger` 的四态**
   （`STATUS_OK` / `STATUS_ATTRIBUTED_BY_WEIGHTS_HASH` / `STATUS_NO_MATCHING_SIGNAL` /
   `STATUS_MISSING_SIGNAL_ID_COLUMN` 及其判定顺序）。
   ⇒ 四态判的是「证据在不在」，**不是**「证据真不真」⇒ **它没坏**。
3. 🚨 **不许动今晚那批产物**：
   `data/reports/verification.parquet` · `score_ranking_power.json` ·
   `downleg_gate.json` · `hit_rate_diagnosis.json` · `portfolio_nav.json` ·
   `stats.json` · `goal_progress_2026-10-21.json`
   ⇒ **不许手工编辑**（连把 10-10 那 20 行的 `signal_id` 改成 `da18a166` 都不许，见 R3）。
   ⇒ 产物的刷新**只能**由生成器**重跑**产生。
4. 🚨 **不许先改判据**（那是第 91 层）。**先查清「标签到底是什么」** —— 本预注册
   是在**查清之后**写的。
5. 🚨 **不许用「判决标签没翻」当「没关系」**：
   W=2 与 W=1 **都远小于门槛 10** ⇒ 判决方向不变；
   **但门槛的达成进度是 `2/10` 还是 `1/10` 是两件事** ⇒ 进度读数是错的。
6. 🚨 **不许用读数解释读数**（「W 从 3 变 2 说明归属基准更严了」不是原因）⇒
   原因只能由 §3 的判别实验与 `mom20` 极性给出。
7. 🚨 **不许改 `aplus/reports.py::signal_fingerprint` 的三个键**
   （`weights` / `top_n` / `bottom_n`）⇒ 改它等于**换判据**（第 91 层）。
8. 🚨 **不许建 GB 级面板**来复现本缺陷（§3 的五步**全部**是秒级只读脚本）。
9. 🚨 **不许跑全量 pytest**（本轮只跑你需要的**只读**脚本）。

---

## §6 「修好了」的判据（**全部**要满足，缺一条不算修好）

| # | 判据 | 怎么验（只读） |
|---|---|---|
| F1 | `verify_predictions` **不再**用入参补 `signal_id` 列 | G-C 绿 + M2 被杀 |
| F2 | 重判路径**不再**传 `current_signal_id` | G-A 绿 + M1 被杀 |
| F3 | 底稿里 10-10 那 20 行**没有** `0df5828c`（要么无标签、要么是别的值） | 实读 `verification.parquet` |
| F4 | `scope_ledger` 的 W **= 1**（不是 2） | §3 步骤 5 |
| F5 | 判决仍是 `NOT_PASSED_POWERLESS` / `publish_allowed=false` | 读 `score_ranking_power.json` |
| F6 | 新出单的行**仍带正确的** `signal_id`（R1 没有把前瞻链打死） | 实读 `pred_2026-10-2X.parquet` 的 `signal_id` 列 |
| F7 | `scope_ledger` 的四态**逐字未变** | G-E 绿 |

⚠️ **F5 的方向性说明**：修好之后 W 从 2 变 1 ⇒ 判决**不会**翻转
（1 < 10）⇒ **不许**把「判决没翻」写成「修法没效果」；
**F4（W=1）才是修好的那一格**。
⇒ 🚨 **也不许**把 W=1 读成「比 W=2 更接近达标」——
**W 是样本量，不是效果量**（第 129 层）。

---

## §7 修完之后**必须**发生的两件事（不许跳过）

1. **下一轮日跑会重判 2026-10-10**（实测 `pending_prediction_dates` 当前返回
   `['2026-10-10', '2026-10-20', '2026-10-21']` ⇒ 10-10 **仍待补判**）
   ⇒ 修好之后那一跑会把 10-10 那 20 行**重写成没有 `signal_id` 的行**
   ⇒ **旧标签被自动清掉**（`append_verification` 的 `drop_duplicates(keep="last")`）
   ⇒ **不需要手工编辑底稿** ⇒ 这正是 §5-3「不许手工改」的落地方式。
   ⚠️ **但这也意味着**：在下一轮日跑之前，底稿里**仍带着错标签** ⇒
   **F3/F4 在那之前是红的** ⇒ **不许因此说「修法没生效」**。
2. **重跑并重新发布**：闸门 / 跌腿 / 命中率诊断 / 组合净值 / `stats.json` /
   `goal_progress` / 阿里云静态页 —— 全部读到 W ⇒ 全部要重跑。
   ⇒ 屏上「W = 2 / 10」在重跑之前是**一个知道自己错了却没改**的数。
   ⚠️ **不许只重跑其中一部分**（口径会打架：§5 根因报告里
   `webapp.py` 与闸门打架过一次，守卫 `test_run_daily_scopes_the_stats_windows_by_signal`
   就是为那次打架写的）。

---

## §8 本预注册的诚实边界

- ⚠️ **R1/R2 的实现细节未裁定**（是删 `signal_id` 参数还是改成「只校验不补」）
  ⇒ §2 钉的是**性质**（不许造标签），**不是**某一种写法。
- ⚠️ **「底稿每一行都该有自己的 `signal_id`」这个理想状态**，
  在**旧文件没有该列**时**不可达** ⇒ R1/R2 的落地形态是
  **「让它们不可归属」**，**不是**「给它们补上正确的标签」。
  ⇒ ⇒ **W 短期内会从 2 掉到 1**（甚至掉到 0）⇒
  🚨 **这不是回退，是真相**（第 16 层：宁可「测不出」，也不许拿别的信号的行充数）。
- ⚠️ **未查**：`forward_d2_verdict.py` / `measure_pool_board_exposure.py` /
  `register_forward_candidates_r5.py` / `measure_delivery_caliber_friction.py` /
  `measure_portfolio_exec.py` 这几个读底稿的脚本**有没有**受同一口径影响
  （见根因报告 §5.7）⇒ **动手的人应当先扫一遍**，再决定要不要一并处理。
- ⚠️ **G-D 对当前底稿天生是红的** ⇒ 按仓规**不许**为了让守卫变绿而去改底稿
  （那正是本缺陷的形态：为了让读数好看而去动证据）。

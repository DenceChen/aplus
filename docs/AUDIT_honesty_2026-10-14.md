# 特性交付完整性与文档诚实性审计报告

**审计日期**：2026-10-14
**审计范围**：`/Users/airboom/opencode/aplus` 的 `docs/` vs `aplus/` + `scripts/` 实际实现
**审计人**：审计 agent（只读；未新建测试文件，未 git add/commit，未修改任何现有文件）
**核对条数**：**108**（见 §7 统计）

---

## 0. 阅读须知：本报告如何对待「疑似缺陷」

本项目今日已多次「把证据读成坏了、其实没坏」。因此每条结论都先过一道闸：

> **「如果它没坏呢？」—— 先去 `docs/` 找有没有预注册条款为它辩护。**

§5 列出**我查过但判定为「没坏」的项**，其中一条正是审计任务书点名的
`tilt 判据没用 p_value_cap` —— **我确认它已被 §3.4 明确辩护，不作为缺陷上报**。

| 标签 | 含义 |
|---|---|
| **确认问题** | 亲自核对，且 `docs/` 中**无**预注册条款辩护 |
| **已自纠** | 有残留不一致，但文档自身已明确更正并留痕 ⇒ 记为诚实披露，不算缺陷 |
| **没坏** | 找到预注册辩护条款 ⇒ **不报** |

---

## 1. 两条指定线索的核实结果

### 1.1 线索一：「α/β 分解那节从未标明 K」⇒ ❌ **不成立，撤回**

**文档实际怎么说**：`docs/TODO_measure_sigma_r.md:1293`

```
## 结果（K=21，6 年，2021-01-04 ~ 2026-10-08）
```

**核对命令**：
```bash
awk 'NR>=1214 && NR<=1400' docs/TODO_measure_sigma_r.md | grep -nE "K=21|K=63|K=|调仓"
```

**实际输出**：
```
1293: ## 结果（K=21，6 年，2021-01-04 ~ 2026-10-08）
1309: 本表「合计/年」与 K 扫描表 K=21 那一行**逐年逐位相同，差 0.00e+00**：
```

**代码/产物实际**：
```bash
.venv/bin/python -c "import json;print(json.load(open('data/reports/alpha_beta_decomp.json'))['k'])"
# → 21
grep -n "^K = " scripts/decompose_alpha_beta.py
# → 58:K = 21
```

⇒ 该节**在标题里就写明了 K=21**，正文 1309 行再次锚定，产物 `k=21`，脚本 `K = 21`。
**四处一致，不存在「读反」的空间。**

> 该线索可能源于「1214 行起的那一节里，**预注册判据表**（1264–1292 行）写的是
> `block = K` 而不是 `block = 21`」。这是**表述简写**，K 的取值在 16 行后的
> 结果节标题里给出——**不构成缺陷**。

---

### 1.2 线索二：「K=21 arm 只在 /tmp ⇒ 头条结论不可复现」⇒ ✅ **成立，但措辞需修正**

**文档实际怎么说**：`docs/TODO_tilt_persistence.md:206-207`

```
单一真相源：`data/reports/tilt_persistence.json`（K=63，23 块）与
`/tmp/tilt_k21.json`（K=21，67 块），均由 `scripts/tilt_persistence.py` 产出。
```

**核对命令与实际输出**：
```bash
ls -la /tmp/tilt_k21.json
# -rw-r--r--  23651  10 14 22:01 /tmp/tilt_k21.json     ← 当前存在

grep -rn "/tmp" docs/TODO_tilt_persistence.md docs/PLATFORM.md CLAUDE.md \
  | grep -i "清\|丢\|风险\|reboot\|重启\|易失"
# （无输出 —— 全文没有任何一处披露 /tmp 易失性）
```

**产物内容核对**（证明它确实是头条结论的来源）：
```bash
.venv/bin/python -c "
import json; d=json.load(open('/tmp/tilt_k21.json'))
print(d['k'], d['verdict']['verdict'], d['verdict']['n_stable'])"
# → 21 stable 6
```
该 `verdict` 与文档 §5「判决 = STABLE（pass_=True）：稳定 6 / 漂移 0 / 混合 1 / 出局 1」
**逐字吻合**。文档第 5 节的**头条结论**（"这张倾斜可以据此显式构建/约束"）
确实只由这个 `/tmp` 文件支撑。

**⚠️ 必须修正线索措辞的一点**：该文件**可以重跑复现**，不是「不可复现」：

```bash
.venv/bin/python scripts/tilt_persistence.py --help
# --daily DAILY   --out OUT   --k K  调仓块长，须与输入文件的 rebalance_every 一致
```
且输入 `data/reports/wf_weights_backtest.daily.parquet` **在仓库内存在**
（132171 字节，含全部 8 个 `expo_*` 列，1394 行）。

**⇒ 真正的问题不是「不可复现」，而是「不在版本控制内，且无任何降级预案」**：

| 维度 | 事实 | 证据 |
|---|---|---|
| 文件当前存在 | ✅ 是 | `ls -la /tmp/tilt_k21.json` |
| 被 git 跟踪 | ❌ 否 | `git ls-files data/ \| wc -l` → `0` |
| 换机后是否还在 | ❌ 否 | `/tmp` 随系统清理 |
| 文档是否披露此风险 | ❌ **否** | 上述 grep 无输出 |
| 文档是否给出重跑命令 | ❌ **否** | grep `--k 21` 无命中 |

**⇒ 确认为问题，见 §2 P2。**

---

## 2. 确认的问题清单

> 每条都经「如果它没坏呢？」检查过，且 `docs/` 无辩护条款。

### P1 — 同一文档开篇首句把 K=21 的判决归给 K=63

**位置**：`docs/TODO_tilt_persistence.md:9`

**文档怎么说**（第 9 行，开篇即定调）：
```
`docs/TODO_measure_sigma_r.md` 的 α/β 分解已定案：K=63 下预注册的
「α > 0」判据**不通过**（6 年里只有 2 年为正）。
```

**代码/产物实际**：
```bash
.venv/bin/python -c "import json;print(json.load(open('data/reports/alpha_beta_decomp.json'))['k'])"
# → 21        ← 产物是 K=21，不是 K=63
```

**证据链**：
- `alpha_beta_decomp.json` 的 `k = 21`，`source_daily = wf_weights_backtest.daily.parquet`
- `wf_weights_backtest.json` 的 `rebalance_every = 21`
- 「2/6 年 α>0」这个判决**只存在于 K=21 的产物里**，K=63 下从未做过 α/β 分解
  （`data/reports/` 下无任何 K=63 的 `alpha_beta_decomp*` 文件）

**该文档第 223 行已自行更正**：
```
⇒ **「2/6 年 α>0」与那张倾斜表同属 K=21**（`alpha_beta_decomp.json: k=21,
source_daily=wf_weights_backtest.daily.parquet`）。
把 K=63 的数字当成同一件事引用，就是第 7 条「两份各自正确的数放一起比」。
```

**为什么仍算问题**：第 223 行的更正**只覆盖"倾斜表"**，而第 9 行的错误陈述是
**「K=63 下 α 判据不通过」**——这句本身仍是错的，且它位于文档**第一个自然段**，
是读者最先读到的定调句。文档花了 214 行才纠正它，且纠正语没回头点名第 9 行。

> 这正是本项目自己第 7 条「两份各自正确的数放一起比」的**实例**：
> K=63 的「6/6 不够线」是真的，K=21 的「α 2/6 年为正」也是真的，
> 第 9 行把它们并成了一句。

---

### P2 — 头条判决的「单一真相源」放在 `/tmp`，且零风险披露

**位置**：`docs/TODO_tilt_persistence.md:206-207`（详见 §1.2 完整证据）

**为什么算问题**：本项目核心方法论是「产物即真相源」（CLAUDE.md 第 8 条
「完成与否由产物内容决定」）。把头条判决的真相源放进 `/tmp`，
与该原则**方向相反**：

| | K=63 臂 | K=21 臂（头条结论所属） |
|---|---|---|
| 路径 | `data/reports/tilt_persistence.json` | `/tmp/tilt_k21.json` |
| 在仓库内 | 是 | **否** |
| 换机存活 | 否（`data/` 被 gitignore） | **否**（`/tmp` 被系统清理） |
| 风险已披露 | 不适用 | **否** |

**加重情节**：K=21 臂是**判决为 STABLE 的那一臂**，K=63 臂是 MIXED。
即「能据此构建/约束」这个**唯一正面结论**挂在最脆弱的文件上；
反证结论（K=63 不成立）反而存在较稳的文件里。**风险与结论方向正相关**。

**建议**：`/tmp/tilt_k21.json` 迁到 `data/reports/tilt_persistence_k21.json`，
并在文档里补一行重跑命令。

---

### P3 — `data/` 被 gitignore ⇒ 「单一真相源」**只在这台机器上成立**

**位置**：`.gitignore:2` = `data/`

**核对命令与实际输出**：
```bash
git check-ignore -v data/reports/alpha_beta_decomp.json
# .gitignore:2:data/   data/reports/alpha_beta_decomp.json

git ls-files data/ | wc -l
# → 0

find data/reports -mindepth 1 -maxdepth 1 | wc -l
# → 76        （72 文件 + 4 目录）
find data/reports -maxdepth 1 -name '*.json' ! -name '*.provenance.json' | wc -l
# → 55
```

**结论**：**76 个条目、55 份 JSON 报告、0 份进入版本控制。**

| 能力 | 成立吗 | 依据 |
|---|---|---|
| 数字能被现存产物复算 | ✅ **本机成立** | 我复算了 40+ 个数字，全部命中（§6） |
| 数字能被**换台机器**复算 | ❌ **不成立** | `git clone` 后 `data/` 是空的 |
| 结论能被**回溯到某个 commit** | ❌ **不成立** | 产物无 VCS 版本号 |
| 崩溃后能恢复 | ❌ **不成立** | 无备份即丢失 |

**诚实性判定**：CLAUDE.md 明确写了「`data/` 被 gitignore，报告不入库是**本仓惯例**」
⇒ 这**是已披露的既定决策，不是隐瞒**。但其**未被评估的代价**是：
文档中所有「单一真相源」的措辞，**实际含义是「这台机器这个时刻的真相源」**。

---

### P4 — 文档 `:813`「门槛本身没有放松」与同文件 `:801`「门槛朝松」**直接矛盾**

**核对命令**：
```bash
sed -n '801p;813p' docs/TODO_measure_sigma_r.md
```
**实际输出**：
```
两个外部锚**原封不动**，变的只有「IC 怎么翻译成分位价差」这一步，
### ⑥ 门槛本身没有放松（反查钉死）
```
（`:799-801` 完整句为「**门槛方向朝松**（0.04167 → 0.01389）」）

⇒ **同一文件相隔 12 行，一处说「门槛朝松」，一处说「没有放松」。**
`CLAUDE.md:493` 与 `tests/test_verdict_composite.py:63` 的注释也都写「方向朝松」。

**重要限定**：**门槛改变本身是合规的**（bug 迫使的重新推导 + 三处主动披露方向，
详见 §5 N2）。**问题只在这句标题否认了一个同一文件已经承认的事实。**

---

### P5 — `:813` 援引的「反查」是**恒等式**，证明不了它宣称的事

**文档第 815-817 行怎么说**：
> `tests/test_verdict_composite.py` 里加了一条**反查**：
> 门槛 IC 代回 `implied_spread` 必须**恰好**还原出年毛超额门槛 26.39%
> （`rel=1e-9`）。所以「偷偷调松门槛」不可能悄悄发生

**我实测的反证**（把目标换成别的值，反查照样通过）：
```bash
.venv/bin/python -c "
from aplus.hurdle import *
from aplus.ic_to_return import required_ic, annualise, implied_spread
for tgt in (0.20, 0.10, 0.05):
    need = tgt + COST_REAL_50K*rounds_per_year(21)
    got = required_ic(need/DAYS_PER_YEAR, sigma_r=0.02656)
    back = annualise(implied_spread(got, sigma_r=0.02656, kind='spearman'))
    print(tgt, need, got, 'reverse==need?', abs(back-need)<1e-12)"
```
**实际输出**：
```
0.20 0.26390 0.013890  reverse==need? True
0.10 0.16390 0.008627  reverse==need? True
0.05 0.11390 0.005995  reverse==need? True
```

⇒ `annualise ∘ implied_spread ∘ required_ic = identity`，
**对任意门槛都成立**，因此它**无法区分"门槛被调松"和"门槛没动"**。

真正能抓住改动的是同文件另一条**字面量**断言
`assert got == pytest.approx(0.013890, abs=5e-5)`（`:101`）——
而那条字面量**与门槛改动在同一次提交里被一起改了**。

---

### P6 — 「不许写死门槛」的 AST 守卫，其检查区间**只覆盖旧门槛，已失效**

**位置**：`tests/test_verdict_composite.py:51`
```python
hard = [v for v in literals if 0.04 < v < 0.05]
assert not hard, f"判决线被写死成字面量 {hard}"
```

**我实测的区间判定**：
```bash
.venv/bin/python -c "
import ast
t=ast.parse(open('scripts/verdict_composite.py',encoding='utf-8').read())
lits=[n.value for n in ast.walk(t) if isinstance(n,ast.Constant) and isinstance(n.value,float)]
print('literals:',lits); print('in band:',[v for v in lits if 0.04<v<0.05])
print('new 0.01389 in band?', 0.04<0.01389<0.05)
print('old 0.04167 in band?', 0.04<0.04167<0.05)"
```
**实际输出**：
```
literals: [0.02656, 1.0, 0.25]
in band: []
new 0.01389 in band? False
old 0.04167 in band? True
```

⇒ 守卫区间 `[0.04, 0.05)` **正是旧门槛 0.04167 的位置**。
**把新门槛 0.01389 原样写死进脚本，这条守卫依然全绿。**

**这正是 CLAUDE.md 自己写下的「守卫只有变异后算数」**——
变异做在运行层，这次是**区间没跟着门槛走**，守卫从"守东西"退化成"报个数"。

---

### P7 — 两个判决脚本的 docstring 仍写着**改动前**的门槛

**位置**：`scripts/verdict_composite.py:7,12`；`scripts/verdict_monthly_hurdle.py:7`
```bash
sed -n '7p;12p' scripts/verdict_composite.py
sed -n '7p'  scripts/verdict_monthly_hurdle.py
```
**实际输出**：
```
- **门槛 0.04167** —— 由 σ_r 实测值与月频成本假设推导，**不是字面量**。
- **6 个判决年全部 ≥ 0.04167 才算过**；任何一年不过就是不过。
    月频过关所需的 rev5 低尾 IC（σ_r = 0.02656 实测）= 0.04167
```

**代码实际算出**：`required_ic(...) = 0.013890`（脚本自己 `print` 出来的也是这个）。

⇒ **注释说 0.04167，代码跑 0.01389。**

**为什么没有守卫兜住**：`tests/test_verdict_composite.py:47` 明确写着
「只查**可执行代码**，不查 docstring / 注释」⇒ **这一类漂移无任何测试覆盖**。

> ⚠️ 讽刺点：`MISTAKE_LOG_2026-10-14.md:654` 把
> 「docstring 的数字与代码不一致」列为**第 4 层静默失效**，
> 而这条守卫的设计**恰好把这一层排除在检查范围外**。

---

### P8 — 同一处翻转的规模被写错方向（8 年里 1 年 ⇒ 实为 3 年）

**位置**：`docs/TODO_measure_sigma_r.md:804` vs `:319`
```bash
sed -n '804p' docs/TODO_measure_sigma_r.md   # → 单因子 rev5 从「8 年里 1 年不及格」…
sed -n '319p' docs/TODO_measure_sigma_r.md   # → 不及格年份：3 / 8
```
⇒ **同一文件相隔 485 行，改动前的状态一处写 3/8、一处写 1/8。**

**方向性说明**：此处**低报**了翻转幅度（真实是 3/8 → 0/8），
**对结论不利方向的数字被写小了**，因此**不构成"调松门槛以让结论通过"**。
**严重性低**，但仍是文档内数字不自洽。

---

### P9 — 线上权重的注释里，**引用了一个已被自己仓库撤销的赛马 t 值**

**位置**：`aplus/factor.py:41-43`（`DEFAULT_WEIGHTS` 的理由之一，会随代码分发）
```python
# 3. **独立佐证**：赛马里 `{mom20:−0.5, turnratio:−0.5}` 的 avg_up +0.128%
#    是冠军 +0.031% 的 4.1 倍、t=5.04（97 组最高）。那次赛马有自己的
#    train/test 切分，不依赖本注释。
```

**该赛马已被本仓库的 ledger 撤销**：`evolution_ledger.json` 第 91 条
（「两条独立证据同时否掉唯一候选：显著性闸门用错分布 + 基准污染」）记录
显著性闸门在 df=10 下真临界是 **5.03**、基准污染未扣除。
`aplus/evolve_race.py:425` 的注释亦复述了这次事故。

⇒ **一个已撤销的 t=5.04 仍以"独立佐证"的身份留在生产权重的理由注释里，无作废标记。**

**为何算问题**：项目在别处（`TODO_measure_sigma_r.md:474` 的「~~2 / 6 过~~ **作废**」）
确立了"被推翻的数字要显式作废"的惯例，**而这里没有**。
且 `ic_guided_race_7y.json` **无 `.provenance.json` sidecar**，
按 `aplus/provenance.py:58-59` 属「不知道来源的报告会被报成 unknown（不许引用）」。

**限定（防止夸大）**：
- 被引用的**不是因子**，而是 t 值；**没有任何被拒因子进入 `composite_score`**
  （`DEFAULT_WEIGHTS` 只含 mom20/rev5/vol20/vsur，`min_count` 把它锁死）
- 主证据是 IC 符号检验（63/63 逐年同号），独立成立且有 `tests/test_default_weight_signs.py` 守护
- ⇒ **符号结论不依赖这条注释**，缺陷是**认知层面**而非计算层面

---

### P10 — 同一个赛马结果，**同一次运行里打印出两个不同的闸门数**

**位置**：`scripts/evolve_race_composite.py:219` vs `aplus/evolve_race.py:798`

**`GATE_NAMES` 实为 12 道**（`aplus/evolve_race.py:72-85`）：
```bash
sed -n '72,85p' aplus/evolve_race.py   # 12 个闸门名
sed -n '96p'   aplus/evolve_race.py   # N_GATES = len(GATE_NAMES) → 12
sed -n '798p'  aplus/evolve_race.py
#   "reason": (f"{N_GATES} 道闸全过：..."                      ⇒ 「12 道闸全过」
sed -n '219p'  scripts/evolve_race_composite.py
#   print("  （七道闸全过，但未加 --promote，不写 weights.json）")  ⇒ 手写「七道闸」
```

⇒ **同一次赛马运行，理由字段说 12 道，调用方那行 print 说七道。**

**为什么是缺陷而非旧文档残迹**：项目**自己已把它记成过缺陷**。
`docs/MISTAKE_LOG_2026-10-14.md:177`：
> 关于闸门数量的说法互相矛盾：docstring 写「七道闸」…三处都是手写常量…
> 建 `GATE_NAMES` 名单，让数字由名单推出来

修复只落在 `aplus/evolve_race.py`（守卫 `tests/test_hurdle_aware_gate.py:199`），
**兄弟脚本的手写 print 未同步**。`docs/PLATFORM.md:50,60` 也仍写「七道闸」，
`grep -rn "七道闸" docs/` **无任何作废标注**。

---

### P11 — `bucket_means()` 第 4 个返回值文档写成 `long_vs_mkt`，实际是 `n`

**位置**：`CLAUDE.md:172`、`docs/MISTAKE_LOG_2026-10-14.md:848`、
`docs/TODO_measure_sigma_r.md:964`（三处同款）vs `scripts/measure_bridge_error.py:110`

**文档怎么说**（`CLAUDE.md:172`）：
> `bucket_means()` 同时返回 `top`/`bottom`/`spread`/`long_vs_mkt`

**代码实际**：
```bash
sed -n '110,112p' scripts/measure_bridge_error.py
# def bucket_means(score, ret, tail_q) -> tuple[float|None, float|None, float|None, int]
#     """切出最高/最低 tail_q 两桶，返回 `(top 均值, bottom 均值, 价差, n)`。"""
```

**全部调用点的实际解包**：
```bash
grep -rn "bucket_means(" scripts/ tests/
# measure_bridge_error.py:135:  _, _, spread, n = bucket_means(...)      ← 第4位是 n
# measure_bridge_error.py:237:  top, bot, sp, _ = bucket_means(...)
# tests/...:305:               top, bot, spread, n = M.bucket_means(...)
```
⇒ **第 4 位是样本数 `n`。** `long_vs_mkt` 由调用方在 `:241` 另行计算。

**为什么重要**：按 `CLAUDE.md:172` 写调用代码的人会把一个 **int 当成收益率口径**用
—— 正是 MISTAKE_LOG 第 22 层花 200 行修的「口径混比」。
**双口径成对输出本身已实现且有守卫**（`pytest tests/test_measure_bridge_error.py -q`
→ 17 passed），**这是文档归因错误，不是功能缺失**。

---

### P12 — 文档把**未实现的止损/回撤规则**写成已实施的约束

**位置**：`docs/PLATFORM.md:104`
> - 每票硬止损 -8%；组合回撤 -10% **强制降半仓**

**代码实际**：
```bash
grep -rn "止损\|stop_loss" aplus/*.py
# 仅 aplus/alloc.py:91 —— 一条**字符串文案**，在返回的 notes 列表里
sed -n '91p' aplus/alloc.py
#   "单票硬止损 -8%：收盘价触及即次日卖出，不犹豫、不摊平",
```

**结构性证明**：`build_allocation(...)` 的签名里**没有任何价格输入**，
因此它在架构上**不可能**判断是否触及止损。

**⇒ 同一节里混着两类规则而不加区分**：`:100` 的「≤10%、最多 5 只」是**真实施的**
（`aplus/alloc.py:58-59` 的 `max_positions=5, per_stock_cap=0.10`），
而 `:104` 的止损/回撤**只是提示文案**。

**⚠️ 减轻情节**：仓库自己的文档已承认退出层缺失
（`docs/chan_recipe_mining_2026-10-14.md:274`「❌ 需实现退出规则层；
`grep` 确认仓库零命中」；`CLAUDE.md:507`「当前是等权满仓持有 21 天」）。
**问题在于 `PLATFORM.md` 这一节没有标注它尚未实施。**

---

### P13 — 变异体数量在两处都写错了，且**方向相反**

**位置**：`docs/PLATFORM.md:68`、`scripts/mutation_check.py:610`
```bash
sed -n '68p' docs/PLATFORM.md
# - mutation_check.py：变异测试工具（18 个变异体，全杀）
sed -n '610p' scripts/mutation_check.py
# **「29/29 全杀」不等于「测试好使」…它只等于「这 29 个变异体被杀掉了」**
```

**代码实际**：
```bash
.venv/bin/python -c "
import ast; t=ast.parse(open('scripts/mutation_check.py',encoding='utf-8').read())
for n in t.body:
    if isinstance(n,ast.AnnAssign) and getattr(n.target,'id','')=='MUTATIONS':
        print('MUTATIONS =',len(n.value.elts))"
# → MUTATIONS = 58
```

⇒ **文档写 18（少报 40 个），自己的 docstring 写 29（少报 29 个）。**

**方向说明**：第 610 行那段话的**用意**恰恰是提醒「全杀不等于充分」，
而它用错了样本量 n ⇒ 按 n=29 算的置信上界（原文「≈9.8%」）
在 n=58 时应约为 **5.1%**。**低报变异体数量 = 低报上界 = 高估守卫强度**，
方向对己不利，**不构成作弊**，属维护债。

---

### P14 — `PLATFORM.md` §2 列了一个**代码里根本不存在**的因子

**位置**：`docs/PLATFORM.md:88`
```
  - `turn_z`：换手率 z-score（异常放量警惕，负向）
```
**代码实际**：
```bash
grep -rn "turn_z" --include="*.py" aplus scripts tests | wc -l
# → 0
```
现存换手类因子是 **`turn20`**（`aplus/panel.py:50`）与 **`turnratio`**。
`docs/PLATFORM.md` 自己在 `:203` 也写「2026-10-10 加入的换手因子是 `turn20`」
—— **同一文档内 §2 与 §8 互斥**。

**附带**：§2 的因子清单还**漏了 `vsur`**，而 `vsur` 就在默认权重里
（`aplus/factor.py:61` `DEFAULT_WEIGHTS` 含 `vsur: -0.5`）。

---

### P15 — `node --check` 守卫的引用数字（48 条）与实际不符

**位置**：`CLAUDE.md:401`、`docs/MISTAKE_LOG_2026-10-14.md:1318`

**文档怎么说**：
> `node --check`：同名 `const` 是**解析期**错误，48 条 pytest 全绿而页面永远停在「加载…」

**代码实际**（守卫**确实存在**，只是条数对不上）：
```bash
grep -rn "node.*--check" tests/*.py
# tests/test_chat_wiring.py:253:  r = subprocess.run([node, "--check", path], ...)
grep -c "^def test_" tests/test_chat_wiring.py   # → 12
grep -c "^def test_" tests/test_webapp.py       # → 18
```
（合计 30；含另外两个前端测试文件则 83 —— **没有任何自然分组等于 48**）

**判定**：守卫功能**完好**，`MISTAKE_LOG:1321` 也准确记录了守卫名
`test_every_inline_script_block_actually_parses`。**只有"48"这个数字对不上**。
**严重性低**，但仍是手打数字——正是 §6 说的「汇总行手打」那类错误。

---

## 3. 问题严重性排序（供处置参考）

| 优先级 | 条目 | 一句话 |
|---|---|---|
| **高** | P1 | 开篇首句把 K=21 判决归给 K=63（214 行后才纠正，且没回头点名） |
| **中高** | P5、P6 | 两个"防偷改门槛"的守卫**一个恒真、一个区间过期** |
| **中** | P4、P8 | 文档内部数字自相矛盾（12 行内、485 行内各一处） |
| **中** | P11 | `bucket_means()` 第 4 返回值文档写错，调用方会把 int 当收益率 |
| **中** | P10 | 同一次运行打印 12 道闸 vs 七道闸 |
| **中** | P2、P3 | 真相源在 `/tmp`；`data/` 全忽略 ⇒ 换机不可复现 |
| **中低** | P7、P9、P12、P13、P14、P15 | docstring 陈旧、被撤销 t 值仍在注释、未实施规则未标注、计数陈旧 |

---

## 4. 未完成/待确认的方向（诚实标注，不猜测）

以下方向**未在本次审计窗口内取得完整结论**，**不作为发现上报**：

- `docs/PLATFORM.md` §1.1 模块清单内部计数一致性（"六个模块" vs 10 行表格）
- Web/API 路由文档与 `aplus/webapp.py` 的**完整**对照（已核 2 条文档明列的路由均存在）
- `aplus/provenance.py` 的 `DEFAULT_GENERATORS` 与实际写盘脚本的**完整**差集

**这些都需要更多核对轮次；本次不产出未经核实的结论。**

> 诚实声明：本报告 §2 的 15 条问题**全部来自我亲自执行命令核对**的项。
> 上列未核完方向**不得**被读成"已确认无问题"。

---

## 5. 「没坏」——查过但判定**不报**为缺陷的项

> 这一节是本报告**更重要**的一半：记录我如何避免重复今日已发生的 5 次误判。

### N1 — `tilt` 判决没用 `p_value_cap` ⇒ **已辩护，不报**

**曾被断言为**："判决分支不读 `PREREG["p_value_cap"]`，是实锤缺陷。"

**为什么不是**：`docs/TODO_tilt_persistence.md:179` 节标题即为
```
### 3.4 对照臂（**不参与判决**，防止把「恒为负」当成「稳定」）
```
产生 p 值的两条臂（C1 符号打乱 / C2 块内打乱）被**显式预注册为对照臂**，
判决用 agree + AC1 两轴。且 `tests/test_tilt_persistence.py:362`
`test_the_diagnostic_arm_does_not_participate_in_the_verdict` **把它钉死了**。

⇒ **预注册条款存在，不报。**

### N2 — 判决门槛 0.04167 → 0.01389 ⇒ **方向朝松但已诚实披露，不报为"改门槛"**

- `:789` 标题即写「**判决门槛从 0.04167 降到 0.01389**」，主动声明
- 表格逐项列出：σ_r「没动」、成本「没动」、K「没动」、目标「没动」，
  **只有 K(q) 常数「改了 —— 这里是 bug」**
- 明写「**门槛方向朝松**…所以本文件里「3/8 年不够」「0/6 不及格」等结论**全部要重读**」
- 判决随之翻转**也被主动写出**

⇒ **这是「先有结论后凑判据」的反面：结论被推翻后如实撤回并重算，
且把放松的方向主动摊开写。合规，不报**（其表述矛盾另见 P4、其守卫失效另见 P5/P6）。

### N3 — `STOCK_COST_DEFAULT = 0.003` 与文档口径 0.0055 不一致 ⇒ **已披露为待办，不报**

`docs/TODO_cost_constant_alignment.md:3` 写「**状态**：未执行。**原因**：改这一个常量
会作废全部已发布回测的绝对数字。」`aplus/backtest.py:48` 仍是 `0.003`
⇒ 文档与代码**一致地处于"待办"状态**，产物里 `cost_real=0.0055 /
cost_bt_default=0.003` 两个口径**并列存储**。诚实，不报。

### N4 — `VOID_…` 报告存在 ⇒ **是主动隔离标记，不报**

`docs/TODO_rerun_reversal_report.md:13` 标注"在此之前报告里的量级一律不可引用"。
我核对三个 bug 的修复**确已落在代码**：
- `scripts/_verify_reversal_freq.py:132` 已改 `mean() * DAYS_PER_YEAR`
- `aplus/verify.py:238` 已按净值口径处理

⇒ **改名隔离 + 待办标注 = 正确做法**，不报。

### N5 — 被拒因子是否被当作有效输入 ⇒ **结构上未发生，不报**

```bash
aplus/factor.py:61  DEFAULT_WEIGHTS = {"mom20":-1.0, "rev5":0.3, "vol20":-0.5, "vsur":-0.5}
```
`composite_score` 只索引权重键（`factor.py:444-449`，`min_count=len(weights)`），
而 `ivol` / `rsi` / `turn20` / `turnratio` **均不在其中** ——
`factor.py:55` 写明「turn20 / turnratio / ivol / rsi 不进默认权重：
新因子只经赛马晋级，不走行政指定」。
`evolution_ledger.json` 记录的 `ivol` 前视泄露、`rsi` 无预测力**都在**该排除集合内。

---

## 6. 文档数字 vs 产物：**逐位复算通过**（本报告最正面的部分）

| 文档位置 | 文档写的 | 产物实际 | 结论 |
|---|---|---|---|
| `TODO_measure_sigma_r.md:3,12` | σ_r = 0.02656 | `0.026564318601178846` | ✅ |
| 同上 / `MISTAKE_LOG:542` | 缩尾后 0.02467（−7.1%） | `0.024670326793575687` | ✅ |
| `:1105` K 扫描表 | 毛超额 9.78/9.90/6.78/1.13/6.04% | 全部吻合 | ✅ |
| 同上 | 门槛 46.84/33.42/26.39/23.20/22.13% | 逐个吻合 | ✅ |
| 同上 | K=42 t 均 = −0.245 | `-0.2453625755500053` | ✅ |
| 同上逐年表 | 6 年 × 5 个 K = **30 个数** | **30/30 全中** | ✅ |
| `:1296` α/β 表 | 6 年合计/α/β/R²/t | 与产物全同 | ✅ |
| `:1309` 交叉验证 | 「与 K 扫描 K=21 逐年差 0.00e+00」 | 实测 **6/6 年 diff = 0.0** | ✅ |
| `:1293` | 2/6 年 α>0 | `alpha_positive_years=2, n_years=6` | ✅ |
| `TODO_tilt:230` | K=63 逐年 差 0.000e+00 | 一致 | ✅ |
| `TODO_tilt:236` | K=21 表 STABLE 6/0/1/1 | `/tmp/tilt_k21.json` 全同 | ✅ |
| `TODO_tilt:249` | K=63 表 MIXED 3/0/2/3 | `tilt_persistence.json` 全同 | ✅ |
| `TODO_tilt:249` | mom20 AC1 +0.214 CI[−0.22,+0.58] | `0.214 / [-0.217, 0.576]` | ✅ |
| `TODO_tilt:249` | vol20 +0.472 CI[+0.07,+0.74] | `0.472 / [0.074, 0.740]` | ✅ |
| `CLAUDE.md` §16倍 | 多空 33.51/55.03/82.16/**108.34**% | `33.5124/55.0342/82.1619/108.3376` | ✅ |
| `CLAUDE.md` §16倍 | 多头 10.86/11.14/10.26/**11.10**% | `10.8639/11.1377/10.2649/11.1008` | ✅ |
| `CLAUDE.md` | 残差 11.10/6.78 = 1.64× | `1.6372` | ✅ |
| `CLAUDE.md:437` | MISTAKE_LOG 有 25 层 | 第 25 层存在 | ✅ |

**⇒ 结论**：本仓文档的**数字诚实度极高**。真正的问题集中在**元信息层**
（哪个 K、哪个文件、是否进版本库、接口返回什么）与**守卫有效性层**，不在数字层。

---

## 7. 统计

| 项 | 数 |
|---|---|
| 亲自核对的断言/数字 | **108** |
| 确认问题 | **15**（P1–P15） |
| 判定「没坏」并记录理由 | **5**（N1–N5） |
| 撤回的预设结论 | **1**（§1.1 线索一不成立） |
| 复算命中 | **40+ 数字，0 处不符** |
| 变异/反例验证（证明守卫失效） | **2**（P5 恒等式、P6 区间失效） |
| 新建测试文件 | **0** |
| 修改的现有文件 | **0**（仅新增本报告） |
| git commit / add | **0** |

---

## 8. 一句话结论

> **数字层是干净的，元信息层与"守卫有效性"层有裂缝。**
>
> 40 余个文档数字全部能从 `data/reports/*.json` 逐位复算，跨路径交叉验证
> （K 扫描 vs α/β 分解）实测差 `0.0`——这是本仓少有的强证据。
> 问题集中在三处：
>
> 1. **元信息错配**：开篇首句把 K=21 的判决归给 K=63；`bucket_means()`
>    第 4 返回值文档写成 `long_vs_mkt` 实为 `n`；`PLATFORM.md` 列了不存在的
>    `turn_z`、漏了 `vsur`；同一次运行打印 12 道闸 vs 七道闸。
> 2. **可追溯性**：头条正面结论的真相源躺在 `/tmp`；`data/` 全量 gitignore
>    ⇒「单一真相源」实为「这台机器的真相源」（76 条目 / 55 份 JSON / 0 入库）。
> 3. **守卫失效（最值得警惕）**：`:815` 援引的"反查"经实测是**恒等式**
>    （换任意目标都通过）；`:51` 的"不许写死门槛"守卫区间仍停在**旧门槛**
>    `0.04~0.05`，把新门槛 0.01389 写死进去照样全绿。
>    ⇒ 这两条正是 CLAUDE.md 自己定义的「恒真守卫」与「变异后算数」的实例。
>
> **须并列记录的另一面**：门槛 0.04167→0.01389 **本身合规**——公式 bug 迫使、
> 三外部锚可证未动、方向三处主动披露；被拒因子**无一进入** `composite_score`；
> 被判据推翻的两处（`tilt_persistence`）均如实记录且未改门槛；
> 污染报告主动改名 `VOID_`/`FOSSAL_` 隔离。**本仓红线纪律整体成立**，
> 上述问题多为元信息与守卫覆盖的疏漏，而非结论造假。
>
> 另需记一笔：任务书给的两条线索里，**「α/β 节没标 K」经核对不成立**
>（`TODO_measure_sigma_r.md:1293` 标题即写 `K=21`）——
> 这是今日第 6 次「疑似缺陷经查不成立」，**已按铁律撤回，不写入问题清单**。
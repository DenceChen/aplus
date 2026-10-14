# 测试套件质量审计 —— 2026-10-14

**范围**：`/Users/airboom/opencode/aplus/tests/`（测试套件质量）
**方法**：AST 静态扫描全量 157 个文件 + 对最可疑的候选做**实证变异验证**
**铁律遵守**：未加载任何 panel 数据；未读任何 key/token；未 git add/commit；未修改任何现有测试文件或产品代码

> **本次审计产生 0 个仓库改动。** 所有变异都发生在 `/tmp/mut` 沙箱副本里，
> 每次变异都记录了 **改前 sha → 改后 sha** 与「改后文件里锚串是否还在」，
> 再看 pytest 结果 —— 顺序不能颠倒（CLAUDE.md 第 14 条）。

---

## 0. 一句话结论

这批测试的**纪律水平明显高于平均**：无 `except: pass`、无 `xfail`、无未打桩的真实网络/LLM 调用、
变异验证过的守卫（`test_report_ready.py`、`test_cross_section_metadata_contract.py`、
`test_verdict_composite.py`、`test_sweep_factor_count.py`）**全部真的能红**。

但有 **2 处确认的假绿**，其中 1 处是**真实产品缺陷**（不是测试的问题），
它已经**静默地**让两个用户可见的证据句失效了。

---

## 1. 规模统计

| 项 | 数 |
|---|---|
| 测试文件（审计基线） | 157（`tests/*.py` 减 `icmat_synthetic.py` 辅助模块） |
| 测试函数 | 1994（AST 计数） |
| 收集到的用例 | 2138（parametrize 展开后） |
| 总行数 | 37053 |
| 全量运行 | 144.98s，**1 failed / 2221 passed / 6 skipped** |

> ⚠️ **口径说明**：审计期间**另一个 agent 正在并发往 `tests/` 加文件**
> （`tests/e2e/`、`test_audit_core_arch.py`、`test_audit_prompts.py`、`test_audit_logging.py`
> 在 22:14 左右陆续出现）。因此**文件数在 157~161 之间浮动，收集数从 2138 涨到 2255**。
> 本报告的静态扫描结论基于 **157 文件基线**；全量运行的数字请以上面那行基线运行为准。

### 静态扫描发现的「可疑形状」分布

| 形状 | 命中数 | 处置 |
|---|---|---|
| 无任何 assert / `pytest.raises` | 13 | 逐个读源码，**全部排除**（见 §4） |
| 只有弱断言（`assert True` / `is not None`） | 2 | 均被同文件的强断言兜住，排除 |
| `pytest.skip` | 19 处调用 / 12 个函数 | 见 §3 |
| `xfail` | **0** | ✅ |
| `except: pass` | **0** | ✅ |
| 未打桩的真实网络 / LLM 调用 | **0** | ✅ 见 §5 |
| 整文件子串断言（shape 2/3 风险面） | **218 个测试函数、296 条断言** | 见 §2.3 |
| `parametrize` 落空集 | **0** | 20 处 parametrize 的入参集合全部非空 |

---

## 2. 假绿嫌疑榜

### 2.1 ✅ 确认是假绿（2 条）

---

#### 【假绿 A】`_find_ic_report` 的 glob 命中 provenance sidecar —— **真实产品缺陷**

**位置**：`aplus/factor_ic.py:289-295`（`_find_ic_report`）
**受害的测试**：`tests/test_yearly_sign_claim.py`（5 个用例全部 skip）

```python
files = sorted(Path(reports_dir).glob("factor_ic*.json"))
return files[-1] if files else None      # ← 恒取排序最后一个
```

**复现命令**：
```bash
cd /Users/airboom/opencode/aplus && .venv/bin/python -c "
from pathlib import Path
from aplus.factor_ic import _find_ic_report
print(_find_ic_report(Path('data/reports')))"
```

**实际输出**：
```
data/reports/factor_ic_7y_v2.json.provenance.json
```

而目录里真实存在三份：
```
factor_ic_7y.json                    ← 真报告（10-12）
factor_ic_7y_v2.json                 ← 真报告（10-14，最新）
factor_ic_7y_v2.json.provenance.json ← sidecar，只有 4 个 key
```
`sorted()` 把 `.provenance.json` 排在**最后**，`files[-1]` 因此**永远**选中 sidecar。

sidecar 的内容是：
```json
{"code_dirty":..., "code_sha":..., "generator":..., "stamped_at":...}
```
没有 `yearly` 键 ⇒ `payload["yearly"]` 抛 `KeyError` ⇒ 被
`aplus/factor_ic.py:424` 的 `except (OSError, ValueError, KeyError, TypeError)`
吞掉 ⇒ 返回 `unknown=True`。

**「如果它没坏呢？」—— 我认真查过这一条，它确实坏了**，三条独立证据：

1. 报告**真的存在且结构完好**（`yearly` 63 行、`factors` 8 个、键齐全），
   单独 `json.loads` 完全正常。**不是数据坏了，是选文件选错了。**
2. `_find_ic_report` 的 docstring 白纸黑字写着「**不回落**到别的文件」——
   它确实没回落，但它**爬到了自己写的 sidecar 上**。
3. 真报告 `factor_ic_7y_v2.json` 里 8 个因子的全段 IC **全部有值**
   （mom20 -0.03587 / rev5 +0.02622 / …），完全够算逐年一致性，
   而守卫报告「结构异常，未做逐年符号核对」。

**两个下游守卫同时静默失效**：

```bash
.venv/bin/python -c "
from pathlib import Path
from aplus.factor_ic import weight_sign_conflicts, yearly_sign_consistency
r=Path('data/reports')
print(weight_sign_conflicts(r, {'mom20':-1.0,'rev5':0.3,'vol20':-0.5,'vsur':-0.5}))
print(yearly_sign_consistency(r)['note'])"
```
```
{'unknown': True, 'conflicts': None, 'report': 'factor_ic_7y_v2.json.provenance.json',
 'note': '没有可用的因子 IC 报告，本次未做符号核对（不等于「无冲突」）'}
因子 IC 报告结构异常，未做逐年符号核对（不等于「逐年一致」）
```

**用户可见后果**（这才是它要紧的地方）：概览页那段给用户看的
「逐年符号 N/N 一致」证据句**消失了**。

```bash
.venv/bin/python -c "
from pathlib import Path
from aplus.evolve import load_weights
print(load_weights(Path('data/reports')).get('caveat'))"
```
现在印出来的是：
> 回落默认权重，**未经赛马验证**。符号已按实测 IC 校正，但**幅度**沿用旧值未调…

**「符号已按实测 IC 校正」这句话的依据，此刻是取不到的。**
报告在磁盘上、格式正确、8 个因子全有 IC —— 只是没人去读它。

**为什么测试没抓住**：`tests/` 全库 **0 处** 引用 `_find_ic_report`
（`grep -rn "_find_ic_report\|factor_ic\*" tests/*.py` → 无输出）。
这条路径**完全没有测试覆盖**，而它的失败模式是 `unknown=True` 而非抛错，
于是被 fixture 的 `pytest.skip` 顺势接住 —— **shape 5 + shape 9 叠加**：
守卫守的东西已经取不到，却因为「宽松地退化成跳过」而仍然是绿的。

**这 5 个 skip 的理由不成立**。它们的理由写的是
「因子 IC 报告结构异常，未做逐年符号核对」——
但这句话**把「我找错文件了」误报成了「数据坏了」**。
skip 的字面理由（数据坏）与真实原因（选择器 bug）不符。

> **最小修法**（不属于本审计范围，仅记录）：glob 排除 sidecar，
> 例如 `glob("factor_ic*.json")` 后过滤 `not p.name.endswith(".provenance.json")`，
> 或改用 `glob("factor_ic*.json")` 且要求 `"yearly" in payload` 后再接受。
> 另外这 5 个 skip 的消息应当能区分「没找到」与「找到了但坏了」。

---

#### 【假绿 B】`verdict.js` 的加载守卫被**注释**喂饱（shape 3 的第二实例）

**位置**：`tests/test_docket_warning.py:195-198`

```python
def test_verdict_js_is_shipped_or_loaded():
    """index.html 必须能加载这个模块——否则运行时是 ReferenceError。"""
    html = Path("aplus/web/index.html").read_text(encoding="utf-8")
    assert "verdict.js" in html, "index.html 没有引用 aplus/web/verdict.js"
```

**变异**（在 `/tmp/mut` 沙箱，改的是**产品代码**不是测试）：
把 `aplus/web/index.html:1798` 的真实加载标签

```html
<script src="/verdict.js"></script>
```

改成

```html
<script src="/verdict-disabled.js"></script>
```

**变异落地核验**（CLAUDE.md 第 14 条要求）：
```
occurrences of anchor: 1 (replaced 1)
sha 66f4d2a40ac4 -> 2a241428aa5a   landed_on_disk=True  anchor_still_present=False
L1798: <script src="/verdict-disabled.js"></script>
```

**复现命令与实际输出**：
```bash
cd /tmp/mut && /Users/airboom/opencode/aplus/.venv/bin/python -m pytest \
  tests/test_docket_warning.py tests/test_stale_weights_warning.py \
  tests/test_chat_wiring.py tests/test_webapp.py -q
```
```
........................................................................ [ 87%]
..........                                                               [100%]
82 passed, 1 warning in 26.23s
```

**82 条测试全绿**，而页面上 `docketWarning` 现在是**彻底的 ReferenceError** ——
判决板警示、证伪提示、回落权重警告全部不会显示。

**为什么**：`"verdict.js"` 在 `index.html` 里出现 **6 次**，其中 **5 次是注释**：

```
 843:     判定逻辑在 `aplus/web/verdict.js`（纯函数，node 可直接跑），
 851:    /* `detailHtml` 只在 caveat 分支存在（由 verdict.js 的 renderCaveat 生成：
 853:       两条路径都在 verdict.js 里由数据本身决定，不接受调用方传入 HTML。 */
1789:   判定逻辑与测试分离的收益见 aplus/web/verdict.js 顶部注释。 */
1798: <script src="/verdict.js"></script>          ← 唯一真正加载的那一处
1799:   这里在 verdict.js 就绪后重跑一次判决板渲染——幂等，且避免
```

子串断言只回答「这个串出现过」，不回答「**在哪用**」。
注释喂饱了它。**这就是 CLAUDE.md 第 13 条记载的形状，
但那一处（`/api/chat/stream`）已经修了，这一处没有。**

**「如果它没坏呢？」—— 我专门排除了这个可能**：
本文件里另 9 条同类的接线守卫**是真能红的**，我逐条变异验证过：

| 变异 | 结果 |
|---|---|
| 删掉 `.docket-warn[hidden]` CSS 补丁 | 🔴 红（`test_hidden_warning_can_actually_be_hidden`） |
| 把 `loadChrome` 改名成 `loadChrome_DISABLED` | 🔴 红（2 条） |
| `loadOverview` 的 `renderDocket(...)` 少传 `evo` | 🔴 红（2 条） |
| 删掉 `<script src="/verdict.js">` | 🟢 **绿（假绿）** |

所以**不是整份文件松，是这一条松**。区别就在于：
前三条断言的是「**某个具体语法位置上**有没有它」，
`test_verdict_js_is_shipped_or_loaded` 断言的是「**整个文件里**有没有出现过」。

**修法与本文件已有的正确范式一致**（`test_both_docket_call_sites_must_pass_evo` 就是对的）：
断言锚在语法位置上，例如
`re.findall(r'<script[^>]*src="([^"]+)"', html)` 里必须包含 `/verdict.js`，
而不是 `"verdict.js" in html`。

---

### 2.2 🟡 高度可疑但未完成验证（1 条）

#### 【可疑 C】`assert_close_or_both_nan` 在**双方皆 NaN** 时真空通过

**位置**：`tests/test_bond_backtest.py:57-61`（27 个测试函数用它）

```python
def assert_close_or_both_nan(a, b, tol=1e-9):
    if a != a or b != b:
        assert (a != a) and (b != b)      # ← 两边都 NaN 时这一句成立
    else:
        assert a == pytest.approx(b, abs=tol)
```

这正是项目记忆里记过的「全 NaN 会让断言恒真」的形状：
若被测函数**两条路径都**退化成 NaN，每一处调用仍然通过。

**我做了实证，结果是「它没坏」—— 但理由值得记下来**：

```bash
cd /tmp/mut && python - <<'EOF'   # 把 bond_factors 改成全返回 NaN
# 结果：
#   assert (nan != nan and 0.2208... != 0.2208...)     ← 被抓
#   assert np.float64(nan) == 0.0 ± 1.0e-12             ← 被抓
# FAILED test_bond_factors_reuses_stock_factor_semantics
# FAILED test_bond_factors_cheap_from_last_close
# FAILED test_attach_bond_factors_equals_scalar_at_cutoffs
# 3 failed, 22 passed
```

它之所以还红，**不是因为这个 helper 本身够强**，
而是因为同文件里另有 3 条**直接比较具体数值**的断言兜住了底
（`cheap == pytest.approx(0.0)`、`bond_factors(...)["cheap"] == approx(5.0)`）。

⇒ 记为**「依赖兄弟断言兜底」**，不是「helper 本身假绿」。
风险在于**别的文件若单独复用这个 helper 而没有数值兜底，就会恒真**。
当前 27 个使用点都在同一文件内且有兜底，所以**暂不构成实际缺陷**。

---

### 2.3 ⚪ 形状上像，但已排除（8 类）

**「如果它没坏呢？」—— 下面每一条我都实际查过，结论是「没坏」。列出来是为了避免下一个人重新怀疑它们。**

| # | 疑似形状 | 查法 | 结论 |
|---|---|---|---|
| 1 | 13 个「无断言」测试 | 逐个读源码 | **全部合法**。10 个用 `pd.testing.assert_frame_equal` / `json.dumps(allow_nan=False)` / `pytest.approx` 这类**表达式语句式**断言（我的 AST 分类器只认 `assert` 关键字，漏判了）；3 个是「不抛异常即通过」的契约测试（`test_write_lock.py:67` 的锁重入、`test_winsorize.py:83` 的短序列不崩） |
| 2 | `test_race_data_root.py:61` 只有 `assert args is not None` | 读源码 | **没坏**。`parse_args` 遇到未注册参数会 `SystemExit(2)`，参数被挤掉时测试真的会红。`is not None` 只是收尾标记 |
| 3 | `check_monotone` 可能恒返回 success（shape 2） | **变异验证** | 🟢 真守卫。改成 `return [True for _ in chain]` 后 **4 条变红** |
| 4 | 判决门槛被硬编码（CLAUDE.md 第 5 条） | **变异验证** | 🟢 真守卫。插入 `HARDCODE_TRAP = 0.04167` 后 `test_the_threshold_is_derived_not_hardcoded` 变红 |
| 5 | `require_keys` 恒返回 ready（shape 2） | **变异验证** | 🟢 真守卫。改成无条件 `Ready(True)` 后 **6 条变红** |
| 6 | `is_nan_column` 恒返回 False（shape 2） | **变异验证** | 🟢 真守卫。改成 `return False` 后 **2 条变红** |
| 7 | 元数据键「假装能调」（CLAUDE.md 第 6 条） | **变异验证** | 🟢 真守卫。重新塞进一个无主的 `_hurdle` 读，**当场变红**并指名道姓：`这些元数据键只被读、从没被写过：{'_hurdle': 'backtest.py'}` |
| 8 | 文档里的 `108.34%` 裸引（shape 3 的另一种） | **变异验证** | 🟢 真守卫。剥掉 `CLAUDE.md` 全部 6 个口径标记后，`test_the_108_number_is_never_cited_without_its_caliber` 变红并指出行号 |

> **关于第 8 条的插曲，值得单独记一笔**（CLAUDE.md 第 14 条的又一次复现）：
> 我第一次只替换了 6 个「多空」中的 **1 个**，测试**全绿**。
> 若就此收工，会得出「文档守卫是假绿」的错误结论 —— 而实际上
> 守卫是好的，是**我的变异太弱**（锚点匹配 1 处、文件里还有 5 处标记兜着）。
> **「变异没变红」与「守卫有洞」的证据完全相同**，唯一区别是
> 变异是否真的改到了被守的那一处。剥掉全部 6 处后，守卫如期变红。
> 这已经是本项目记忆里记的第 5 次同型误判。

### 2.4 关于「218 个测试函数、296 条整文件子串断言」的风险面

这是**最大的一块风险敞口**，但**不能一笔判死**。分两种：

- **锚在语法位置上的**（`re.findall(r"fetch\(\s*['\"]...")`、收在函数体内的断言）—— 可靠。
  本文件 `test_both_docket_call_sites_must_pass_evo` / `..._must_pass_all_three_arguments`
  就是这一类，变异验证全部变红。
- **锚在整个文件上的**（`"X" in html`）—— 受注释喂饱。**假绿 B 就是其中一条**。

`index.html` 有近 80 行中文注释，`aplus/web/verdict.js` 的引用在注释里出现 5 次，
所以这个文件上的**整文件子串断言天然不可信**。
`tests/test_docket_warning.py`（20 个）与 `tests/test_stale_weights_warning.py`（9 个）是重灾区。

---

## 3. skip / xfail 清单

**`xfail`：全库 0 处** —— ✅ 这是好事，意味着没人用 xfail 藏失败。

**skip 调用共 19 处，落在 12 个测试函数上**。逐条判：

| 文件:行 | skip 理由 | 成立？ |
|---|---|---|
| `test_verdict_monthly_hurdle.py:74` | 报告不在（data/ 被 gitignore） | ✅ 成立（本机有报告，未触发） |
| `test_verdict_monthly_hurdle.py:78` | 报告没有 yearly 段 | ✅ 成立 |
| `test_verdict_monthly_hurdle.py:80` | 报告已重跑，三档齐全——**前提已消失** | ✅ **这条是本仓 skip 的范本**：前提没了就明说前提没了，而不是让测试空转 |
| `test_yearly_tail_decomposition.py:176,180` | 报告不在 / 没有 yearly 段 | ✅ 成立 |
| `test_yearly_sign_claim.py:79,103,110,124,147` | 「因子 IC 报告结构异常，未做逐年符号核对」 | ❌ **不成立 —— 见【假绿 A】。真实原因是选择器 bug，不是数据坏。5 条全跳 = 5 条证据句失效无人在意** |
| `test_ic_to_return.py:567` | `{doc} 不存在` | ✅ 成立（doc 在，跳过 2 个） |
| `test_yearly_sign_claim.py:65` | 没有 IC 报告 | ✅ 成立（未触发） |
| `test_default_weight_signs.py:68` | 「缺少符号证据，测试**不能退化成断言新值**」 | ✅ **理由写得极好**：明确拒绝在缺证据时降级断言 |
| `test_breadth_gate_asymmetry.py:223` | parquet 不在 | ✅ 成立 |
| `test_chat_wiring.py:243` | 没有 node，跳过 JS 解析检查 | ✅ **成立**（node 在，未触发）。注释写明「这是环境能力，不是代码正确性」 |
| `test_critical_value_provenance.py:141` | 无 evolution_log.json | ✅ 成立 |
| `test_daily_series_json.py:106` | 无 backtest.parquet 产物 | ✅ 成立 |
| `test_exrights_return.py:203` | 生产数据不在本机 | ✅ 成立 |
| `test_measure_amount_availability.py:194` | 主库还没有日线数据 | ✅ 成立 |
| `test_prediction_weights_provenance.py:435,446` | 生产榜单不在（别的机器上跳过） | ✅ 成立 |
| `test_reversal_report_cost.py:197` | 报告尚未生成 | ✅ 成立 |
| `test_webapp.py:269` | 转债库为空（CI 环境） | ✅ 成立 |

**实测基线**：全量跑只有 **6 个 skip**，其中 **5 个是假绿 A 造成的**。
⇒ **本机实际未经验证的只有 1 条**（`test_reversal_report_cost.py`）。

**唯一一条失败**：`test_measure_amount_availability.py:216 test_the_real_store_amount_has_no_null_values`
```
AssertionError: 53 只股票的 amount 有空值：['688737.SH', '688739.SH', '688750.SH', ...]
```
这条**是真红**（守的是主库 `amount` 空值率必须为 0，实测 53 只股票有空值），
**不是测试的问题，是数据现状**。它正是「守卫能响」的证据。

---

## 4. 真实网络 / LLM 调用未打桩清单

**✅ 全库 0 处。**

AST 扫描 `requests.* / httpx.* / urllib.request.* / aiohttp.* / socket.*` 与
`baostock.* / akshare.* / tushare.*` 的全部调用点：**零命中**。

数据源相关测试（`test_sources.py` 等）全部使用**假 baostock 模块**打桩，
例如 `test_sources.py:216` 的 `_fake_bs(monkeypatch, rows)` 类。

**唯一的外部进程调用是 `node`**（跑前端 JS 判定函数 + `node --check` 解析内联脚本），
共 8 处，全部有 `shutil.which("node")` 环境保护，node 缺失时 skip 并**明说**
「这是环境能力，不是代码正确性」。本机 node v24.15.0 可用，**全部实跑，无 skip**。

⇒ **shape 11（网络/LLM 实调用跑不通就 skip）在本仓不存在。**

---

## 5. 结论：这批测试的可信度分层

### 第 1 层 —— 可信（已变异验证，守卫能响）

| 文件 | 依据 |
|---|---|
| `test_report_ready.py` | 2 个变异全红；且 docstring 明写「全 NaN 列会让 `np.allclose` 恒真」 |
| `test_cross_section_metadata_contract.py` | 变异红；**且自带反向测试**（造一个无主键当场抓） |
| `test_verdict_composite.py` | 2 个变异红；AST 只查可执行代码、排除 docstring |
| `test_sweep_factor_count.py` | `check_monotone` 变异红；**有反例链**（`[(7,.130),(8,.0709)]` 必须判 `[False]`） |
| `test_ic_to_return.py` | 文档口径变异红；**且有反向测试** `test_the_guard_would_catch_a_bare_citation` |
| `test_docket_warning.py` | **9/10 条**变异红（1 条假绿，见下） |

这批文件的共同特征：**每个守卫都配一条「守卫自己必须能红」的反向测试**，
并且文件头把「在守什么 / 坏了什么症状」写在 docstring 里。这是本仓最强的一档。

### 第 2 层 —— 大体可信，零散风险

`test_bond_backtest.py`（依赖兄弟断言兜底）、
`test_evolve_race.py` / `test_chat_wiring.py` / `test_webapp.py` 等
**其余 130+ 个文件**：AST 层面无 `except: pass`、无 `xfail`、无空断言、
无未打桩网络调用。这些文件**未做逐个变异验证**，
但静态扫描未发现任何一种已知的假绿形状。

### 第 3 层 —— 不可信（1 处）

**`aplus/web/index.html` 上的整文件子串断言**（假绿 B）。
该文件近 80 行中文注释，**任何 `assert "X" in html` 形式的断言都应视为未验证**。
`test_docket_warning.py` 有 20 个测试函数、`test_stale_weights_warning.py` 有 9 个落在这个面上。

### 第 4 层 —— 静默失效中（1 处，**且是产品缺陷不是测试缺陷**）

**`aplus/factor_ic.py:289` 的 `_find_ic_report`**（假绿 A）。
测试**没有义务**发现它（0 处引用），但它让 5 条本该验证
「用户可见证据句」的测试**跳过了**。**这条的优先级高于任何测试侧的修复。**

---

## 6. 建议的修复顺序

1. **先修 `_find_ic_report`**（`aplus/factor_ic.py:292`）——
   glob 排除 `.provenance.json`。这是唯一有**用户可见后果**的一条：
   「符号已按实测 IC 校正」此刻是没有数据支撑的。修完 5 个 skip 会自动转成真跑。
2. **修 `test_verdict_js_is_shipped_or_loaded`**（`tests/test_docket_warning.py:198`）——
   改成 `re.findall(r'<script[^>]*src="([^"]+)"', html)` 里必须含 `/verdict.js`。
   同一个文件里 `test_both_docket_call_sites_must_pass_evo` 就是现成的正确范式。
3. **给 `_find_ic_report` 补一条测试** —— 目录里同时存在报告与 sidecar 时，
   必须选中报告。这是 shape 5（守卫守着一个已经取不到的东西）的标准防线。
4. **`test_measure_amount_availability.py:216` 的红灯需要处置** ——
   它是真信号（53 只股票 `amount` 有空值），要么修数据要么改判据并写进预注册。

---

## 附录：本次审计的变异清单（全部在 `/tmp/mut`，全部已还原）

| # | 目标 | 变异 | 结果 |
|---|---|---|---|
| M3 | `index.html` | 删 `.docket-warn[hidden]` CSS | 🔴 红 |
| M4 | `index.html` | `loadChrome` 改名 | 🔴 红 |
| M1 | `index.html` | `loadOverview` 少传 `evo` | 🔴 红 |
| **M2** | `index.html` | **删 `<script src="/verdict.js">`** | 🟢 **假绿** |
| M7 | `sweep_factor_count.py` | `check_monotone` 恒 True | 🔴 红 |
| M8 | `verdict_composite.py` | 硬编码 0.04167 | 🔴 红 |
| M10 | `CLAUDE.md` | 剥掉**全部** 6 个口径标记 | 🔴 红 |
| M11 | `aplus/factor.py` | 删 `DEFAULT_WEIGHTS` | 🔴 红 |
| M12 | `bond_backtest.py` | `bond_factors` 全 NaN | 🔴 红（兄弟断言兜住） |
| M13 | `report_ready.py` | `require_keys` 恒 ready | 🔴 红 |
| M14 | `report_ready.py` | `is_nan_column` 恒 False | 🔴 红 |
| M16 | `backtest.py` | 塞回无主的 `_hurdle` 读 | 🔴 红 |
| ~~M5~~ | `index.html` | — | 锚点未命中，**未产生结论** |
| ~~M9~~ | `verdict_composite.py` | — | **等价变异**（语法错误），**未产生结论** |
| ~~M9b~~ | `verdict_composite.py` | 加 `pass` | **等价变异**（空操作），**未产生结论** |
| ~~M15~~ | `backtest.py` | 改注释 | **等价变异**，**未产生结论** |

> 最后 4 行是**「变异没变红」的四种情形**：锚点没匹配上、语义空操作。
> 它们和「守卫有洞」在终端上输出**完全一样**。
> 本审计把它们全部剔除，**没有据此修改任何守卫或产品代码**。

# 预注册：判决卡上屏（2026-10-18）

> **本文件写在看到任何新数据之前。** 下面 §1 的全部读数都是**动手前**从只读字段与
> 浏览器资源计时里量出来的，量完即冻结。§3 的判决判据在**改任何分支门槛之前**钉死。
> 依据：本仓「判据必须预注册」+ 第 91 层（**预注册保证「判据先于数据」，
> 不保证「前提先于判据」**——所以 §2 单列前提并逐条写了它是怎么验的）。

---

## §1 冻结读数（2026-10-18 动手前，来源逐条可查）

### §1.1 生产判决面（`GET /api/status`，**只读字段，未用公式重算** — 第 109 层）

| 字段 | 值 |
|---|---|
| `stats.cagr` | **`-0.7364854369679485`** |
| `stats.n_days` | **`4`** |
| `stats.total_return` | `-0.02094650393344788` |
| `stats.years` | `0.015873015873015872` |

⚠️ `cagr = -73.6%` 本身是**把 −2.09% 的 4 日总收益按 0.0159 年外推**的产物。
**它不是一个可解读的年化数字**，而 `verdict.js` 第 4 支的存在正是为了拦它。

⚠️ 这 27 个 `stats` 键里**没有** `mde` 与 `verdict_about_20pct`
⇒ 它们属于 `scripts/measure_portfolio_nav.py` 的产物面，**不是 `/api/status` 的面**。

### §1.2 D2 台账（`GET /api/d2-gap`，本轮新建，端点已实测 200）

| 字段 | 值 |
|---|---|
| `...account_annual_ceiling_pct.gap_to_target_at_deliverable_width_and_50k_pp` | **`11.655854851067353`** |
| `...account_annual_ceiling_pct.at_deliverable_width_and_50k` | 8.3441（%） |
| `...account_annual_ceiling_pct.at_infinite_capital` | 12.73（%） |
| `headline.required_gross_at_50k_deliverable` | 15.5571（%） |
| `headline.multiple_needed_at_50k_deliverable` | 3.2338（×） ← ⚠️ **2026-10-21 补记**：该值是 **K=21 时代**的数；台账换到 **K=63** 后它是 **2.3450**（**跨口径**：K=63 的要求 ÷ K=21 研究臂的实测 ⇒ 台账已单列 `cross_caliber` 并标 `is_the_gap = false` ⇒ **不是缺口**）；**同口径**倍数在 `headline.multiple_needed_at_50k_deliverable_by_caliber::same_caliber::multiple_needed` = **1.7897** |

**这三行是本轮唯一要上屏的新增内容。** 引用时必须连口径标签一起引
（`at_deliverable_width_and_50k` ≠ `at_infinite_capital`，
`gap_to_target_at_deliverable_width_and_50k_pp` ≠ 本金→∞ 的缺口）。

### §1.3 浏览器实际执行的是哪一份字节（**唯一可靠量具 = `performance` 资源计时**）

| 量 | 值 | 结论 |
|---|---|---|
| `/verdict.js`（`<script src>` 路径） | `transferSize 0` / `encodedBodySize 14881` / `deliveryType "cache"` | **走缓存** |
| `/verdict.js`（`fetch(no-store)` 路径） | `transferSize 22285` / `encodedBodySize 21985` / `deliveryType ""` | 走网络 |
| `document` | `transferSize 136606` / `deliveryType ""` | **文档走网络** |

- `git show 3fdd92a:aplus/web/verdict.js` 字节数 = **14881** ≡ `encodedBodySize`
- 该 commit 的顶层函数 = **4 个**（行 117/124/146/169），
  **`grep -c d2LedgerVerdict` = 0**
- 盘上顶层函数 = **5 个**（117/124/146/**195**/272），含 `d2LedgerVerdict`
- 浏览器页面上 `typeof` 的 5 个名字：`d2LedgerVerdict` = **`"undefined"`**，其余 4 个 = `"function"`

⇒ **四对四逐个函数对上**：浏览器执行的是 `3fdd92a` 的化石副本。
🚨 与「部署层零守卫」（`serve.py` 无 `--reload`）**方向相反**：
那条是「改了不生效」，这条是「**改了生效了但跑的是旧的**」⇒ 后者更隐蔽，因为**没有任何错误**。

⚠️ **响应头实测两处都没有 `cache-control` / `expires`，但都有 `etag` + `last-modified`**
⇒ 机制是 RFC 9111 §4.2.2 **启发式新鲜度**：缺 `Cache-Control` 时浏览器**必须**用
`10% × (Date − Last-Modified)` 当 freshness。**没写策略 ≠ 不缓存，而是授权浏览器猜。**
`grep "add_middleware\|Middleware" aplus/webapp.py` ⇒ **零命中**。

### §1.4 缓存不是唯一的原因 —— `d2LedgerVerdict` **双重不可达**

| 支 | 条件 | 生产取值 | 命中？ | 调 `d2LedgerVerdict`？ |
|---|---|---|---|---|
| 1 | `weights_check.status !== 'match'` | `match` | ❌ | ❌ |
| **2** | `weights?.caveat` | **有** | ✅ **唯一命中** | ❌ |
| 3 | `evo.champion_falsified === true` | `false` | ❌ | ❌ |
| 4 | `nDays > 0 && nDays < 30` | `4 < 30` | （**被支 2 遮住**） | ❌ |
| 5 | `cagr < 0` | `-0.7365 < 0` | （被支 2 遮住） | ❌ |
| 6 | `cagr < 0.20` | 是 | （被支 2 遮住） | ❌ |
| **7** | `cagr >= 0.20` | **否** | ❌ **结构性不可达** | ✅ **唯一调用点** |

⇒ 即便缓存修好，卡片**仍不上屏**：支 2 先返回（A），
且支 7 要求 `cagr ≥ 0.20` 而实测 `cagr = -0.7365`（B）。

### §1.5 分支顺序：**cagr 家族内部已经正确，不许动**

逐字读 `verdict.js:330-389` 实测：支 4 → 支 5 → 支 6 → 支 7 已在源码里按
「样本不足 → 为负 → 未达标 → 达标」排列。

⇒ **本轮不动支 4↔支 5↔支 6↔支 7 的相对顺序。**
上一段登记的「修支 2↔支 5」隐含支 4↔支 5 也有问题，**已实测排除**。

---

## §2 前提（每条写了「怎么验的」—— 第 91 层：前提必须先于判据）

| # | 前提 | 怎么验的 | 若不成立会怎样 |
|---|---|---|---|
| P1 | `/api/d2-gap` 在生产确实返回 200 且 `verdict_input` 非 null | 浏览器内 `fetch` 实测（上一段） | 台账臂拿不到数据，恒返 `null` |
| P2 | 浏览器执行的字节 = 盘上字节 | `performance` 资源计时 + 顶层函数逐个比对 + FNV-1a | 测的是旧副本的判决 |
| P3 | `static` mount 之后没有别的中间件覆盖响应头 | `grep add_middleware` 零命中 | 加了头也会被覆盖 |
| P4 | `docketWarning` 在**生产载荷**上非 null | 上一段实测支 2 命中 | 全部改动无处可验 |

---

## §3 判决判据（**动手前钉死**）

### §3.1 修法甲（部署层，**不碰任何判据**）

给静态资源加 `Cache-Control: no-cache`。

- **接受条件**：对 `/verdict.js` 发一次请求，响应头里**有** `cache-control`；
  且 `Cache-Control` 判据挂在**模块级函数**上（第 96 层：守卫 import 不到的代码等于零守卫）。
- ⚠️ **必须同时写进文档**：`no-cache` **不是** `no-store`——
  它仍然会存副本，但**每次必须回源验证**。
- 🚨 **单修甲，卡片仍不上屏**（§1.4 的 A、B 未变）。
  **不许事后把甲当成「修好了」** —— 这是本条最容易被自欺的地方。

**备选（明确不选）**：`<script src="/verdict.js?v=<常量>">` 查询串击穿缓存。
实测 `?cachebust=x1` 拿到 21985 ⇒ 有效。**不选的理由**：
它把判据挂在「记得改版本号」上，而**本仓已因「记忆负担」留下三处化石注释**
（第 13 层同族：注释能满足一个子串断言）⇒ 一次修全部、永久生效 > 每次记得改。

### §3.2 修法乙（**加臂，不是换臂** —— 第 183 层 sanctioned move）

把 D2 台账**并进支 2 已经返回的那一支**的 `detail`。

- **不动**：`TARGET_CAGR`、`MIN_DAYS_FOR_CAGR`、任何 `cagr` 判据、
  任何分支的**条件**、任何分支的 **`level`**。
- **只加**：支 2 的 `detail` 末尾追加台账句。
- **接受条件**：在**真实载荷**（`read_d2_gap` 的实际输出 + 实际 `weights.caveat`）上跑
  `docketWarning`，返回非 null **且** `detail` 含台账的三个数。
- 🚨 **不接受的替代**：把支 4/5/6/7 提到支 2 之前（严重度倒置修复）。
  **那是换判据臂**，撞第 91 层。**本轮明确拒绝**，单独立项。

### §3.3 守卫（绿灯不计数，写清它守不住什么）

| 守卫 | 守什么 | 🚨 **守不住什么** |
|---|---|---|
| `Cache-Control` 头真出现在响应里 | 甲生效了 | 浏览器**实际**有没有用它重取（只有浏览器里能验） |
| 台账臂在真实载荷上返回非 null | 乙生效了 | 用户**实际**看到的屏是不是这个判决（要浏览器实测） |
| `docketWarning` 仍可 import | 第 96 层 | —— |

⚠️ **两条守卫都产生绿灯，且都不计数**（开工闸 Q4 已登记）：
它们钉的是「代码与响应头」，而真实失效形态是「浏览器里跑旧字节」
与「支 2 先行返回」—— 前者只能由 `performance.getEntriesByType` 验，
后者只能由**拿真实产物跑判决**验。**两者都不是源码形状能覆盖的。**

---

## §4 严重度倒置（本轮**只登记、不修**）

支 2（「权重未经赛马验证」）**遮住了**支 4（「样本不足，年化不可解读」），
而支 4 正是为拦 `cagr = -73.6%` 这个外推产物而存在的。

这违反本仓 `CLAUDE.md` 自订的规则：「**顺序按结论的严厉程度降序排，
且这条规则必须写进代码**」。

⇒ **单独立项。** 修它 = 换判据臂 ⇒ 必须先预注册（本文件 §3.2 已明写不选）。
⇒ **本轮拒绝**：拒绝把它顺手做掉；也拒绝把它写成一个「已修」的待办。

---

## §5 本轮明确拒绝做的事

1. 改 `TARGET_CAGR`（0.20）或 `MIN_DAYS_FOR_CAGR`（30）
2. 重排支 2↔支 4/5/6 的顺序
3. 把 `cagr = -73.6%` 当成一个可解读的年化数字展示
4. 改 `index.html` 的 `renderDocket` 实参个数（第 6 槽）
5. 用查询串版本号代替响应头（§3.1 备选，已写明不选的理由）
6. 把 `-11.66pp` 说成「再攒样本就能补上」—— 台账逐字说**没有任何（榜宽×本金）组合能到 20%**
7. 重跑 `d2_signal_requirement.py`
8. push / merge 到 `main`

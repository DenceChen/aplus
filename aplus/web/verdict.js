/**
 * 判决板警示判定 —— 纯逻辑，node 可直接跑。
 *
 * 跑法：node tests/verdict.js
 *
 * ## 为什么必须抽出来而不是留在 renderDocket 里
 *
 * `renderDocket(stats)` 直接写 DOM。没有测试基建时，判定逻辑只能靠
 * 「打开浏览器看」来验证——而那验证不了**分支覆盖**，只验证得了
 * 「这一屏看着对」。抽出纯函数后，每一条警示分支都能被钉住。
 *
 * 这个文件只做一件事：把「stats + 进化健康度」翻译成
 * {level, word, why}。不碰 DOM、不发请求、不读全局。
 */

// ── 判据常量（与 tests/test_status_compounded.py 保持一致）──

/** 样本天数低于此值时，任何年化数字都不可解读。 */
const MIN_DAYS_FOR_CAGR = 30;
/** 用户的年化目标。CAGR 低于它就该直说，而不是只报命中率。 */
const TARGET_CAGR = 0.20;

/**
 * `caveat_kind` → 判决板标题。
 *
 * ## 为什么标题必须由 kind 决定，不能硬编码
 *
 * 标题与正文出自同一份记录，两者对证据强度的断言必须一致。曾经出过
 * 一次真实的矛盾：默认权重符号按实测 IC 翻转后，正文如实改成了
 * 「方向有证据、效果未验证」，而标题仍写着「当前跑的是已知的坏配置」——
 * 同一个框里，正文说**没验过**，标题说**验过且是坏的**。
 *
 * 两句话都在骗用户，而且方向相反：
 * · 说「已知为坏」→ 用户以为验过了，下一步该是「换掉它」。
 * · 说「未验证」  → 用户知道还没验，下一步该是「去验它」。
 *
 * 所以：证据状态由**后端判定**并以机器可读的 `caveat_kind` 递下来，
 * 前端只做查表。不在前端解析中文措辞——改一次文案就会静默出错。
 *
 * `measured_bad` —— 已实测且结论为负。
 * `unraced`     —— 从未赛马，符号方向有证据但整组未验收。
 */
const CAVEAT_HEADLINES = {
  measured_bad: '当前跑的是已知的坏配置',
  unraced: '当前权重未经赛马验证',
};

/** kind 缺失或未知时降级到**较弱**的说法。 */
const DEFAULT_CAVEAT_KIND = 'unraced';

/**
 * `weights_check.status` → 判决板标题。
 *
 * ## 这个分支与 `CAVEAT_HEADLINES` 是**两回事**，不要合并
 *
 * `caveat`（来自 `/api/weights`）说的是「**现在跑的**权重未经赛马验收 /
 * 是已知坏配置」——一个关于**配置本身**的属性。
 *
 * 本表说的是另一件事：「**你屏幕上这张榜单**是用另一组权重排出来的」，
 * 或者「**根本不知道**它是用哪组权重排的」。判据来自
 * `/api/predictions/latest` 的 `weights_check`。
 *
 * 两者的失效方向相反：
 * · `caveat` 触发时，榜单**确实**是用当前这组权重排的，它只是这组权重有问题。
 * · `weights_check` 触发时，榜单**根本不代表**当前权重的判断——
 *   它是上一把尺子留下的，而上一把尺子已经被换掉了。
 *
 * 只报前者不报后者，用户会看着一把**已经换掉**的尺子排出的名次建仓，
 * 而页面只告诉他「现在这把尺子没验过」。两句话合起来仍会让人下错注。
 *
 * ## 为什么没有 `match` 这一行
 *
 * `match` = 一致 = 没有警示。没有警示的东西不需要标题。
 * 需要标题的是**需要说话**的两种情况。
 *
 * `mismatch` —— 两边的指纹都读得出，且不相等。
 * `unknown`  —— 榜单没记出处（2026-10-14 之前生成的文件全部如此）。
 *
 * `unknown` 同样必须报警：**无法证明一致 ≠ 一致。**
 * 把它静默掉，等于让所有权重变更继续无声通过——
 * 那这个字段就退化成了摆设。
 */
const STALE_WEIGHTS_HEADLINES = {
  mismatch: '当前榜单不是现在这组权重排出来的',
  unknown: '当前榜单的权重出处不可知',
};

/**
 * status 缺失或不认识时降级到 `unknown`。
 *
 * ## 为什么这里降级方向与 `DEFAULT_CAVEAT_KIND` 相反
 *
 * `caveat` 那边降级到**较弱**的说法，是因为多说一句没根据的话比少说一句危险。
 *
 * 本表这边每个分支**都**报警，所以「不认识」的最安全落点也在报警那一侧。
 * 若默认成静默（当作 match），后端将来加一个新 status 的那一刻，
 * 警示就会**静默地全部失效**——而那正是这个字段被加进来要防的那类事故。
 */
const DEFAULT_STALE_WEIGHTS_STATUS = 'unknown';

/**
 * caveat 里的 Markdown 记号 → HTML。
 *
 * ## 为什么只处理 `**`，且必须先转义
 *
 * caveat 是 `load_weights` 里写死的**内部常量**，可信——但它经 JSON
 * 传到前端，仍不能直接当 HTML 插入。所以这里的顺序是
 * 「先转义 `<>&"'`，再把 `**x**` 换成 `<b>x</b>`」，而不是反过来。
 * 顺序反了就是 XSS。
 *
 * ## 为什么不给全站引入 Markdown 渲染
 *
 * 因为这里只需要加粗。不引入渲染器意味着多一个依赖、少一处
 * 「用户输入被当 HTML 解析」的入口。加粗是唯一的诉求，
 * 值得的实现就是这一行。
 */
function renderCaveat(text) {
  const escaped = String(text ?? '')
    .replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  return escaped.replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>');
}

/** 把一组权重渲染成 `mom20 -1、rev5 +0.3` 这样人能读的短串。 */
function formatWeights(w) {
  if (!w || typeof w !== 'object') return '';
  return Object.keys(w).sort()
    .map(k => `${k} ${Number(w[k]) > 0 ? '+' : ''}${w[k]}`).join('、');
}

/**
 * 榜单出处分支的正文。`mismatch` 与 `unknown` 讲的话**不一样**，不能共用一句。
 *
 * mismatch —— 已知是两组不同的权重，可以指名道姓说「A vs B」。
 * unknown  —— 只能说「没有任何记录」。**不能**说成「不一样」：
 *   那是把「不知道」说成了「知道」，而且方向不同：
 *   说「不同」会让用户以为只是换了配置、榜单照样有参考价值；
 *   只有说「不可知」才告诉他这份榜单**没有任何证据**支持。
 *
 * ## 这里**不写** Markdown 记号
 *
 * 与 caveat 分支不同，本分支不带 `detailHtml`——它的内容来自接口数据，
 * 不是写死的可信常量（理由见 `renderCaveat` 与 index.html 的渲染处）。
 * 所以正文里的 `**` 会被原样打印到页面上。所以要用的话直接不用，
 * 用中文语气词（「不是同一把尺子」）承担强调。
 */
function staleWeightsDetail(status, check, date) {
  const cur = check.current_weights;
  const shown = formatWeights(cur);
  const curPart = shown ? `当前跑的是 ${shown}（指纹 ${check.current_hash}）。`
                       : `当前权重的指纹是 ${check.current_hash}。`;
  if (status === 'mismatch') {
    return `这张 ${date} 的榜单是用指纹 ${check.board_hash} 那组权重排的，`
      + `与当前这组不是同一把尺子——它早于当前的权重配置。`
      + `${curPart}榜单上的名次不代表现在的判断，别照着它建仓。`;
  }
  return `这张 ${date} 的榜单没有记录自己是哪组权重排的（生成于出处记录上线之前），`
    + `所以无法证明它用的是当前这组。${curPart}`
    + `无法证明一致不等于一致：先重跑一次出单，或等下一份带出处的榜单。`;
}

// ── D2 缺口的**两个口径**（2026-10-21）──────────────────────────────────
//
// 🚨🚨 **两个口径是两个不同的量，不许合并、不许取极值**（第 143 层）：
//
//   | 口径 | 它是什么 | 载体 |
//   |---|---|---|
//   | **理想档** | 满仓（100% 部署）假设下的**公式上界** | `d2_signal_requirement.json` |
//   | **实盘档** | 平台**真会执行**的那个账户的**模拟净值**（整手 ⇒ 投入率下降、闲置现金 0 计息） | `deployed_d2_verdict.json` |
//
//   差额 = **整手 + 闲置现金**的代价。
//   ⇒ 屏上**两个都要给，各自带标签**；只给一个 ⇒ 要么少报、要么虚报。
//
// 🚨 **标签 / 来源各只有一份**（下面这四个常量）—— 文案不许在判决与首屏各抄一份
//   （第 137 层：两份副本之间没有守卫，而重跑后它们会静默分叉）。
const D2_CALIBER_IDEAL_LABEL = '理想档（满仓假设）';
const D2_CALIBER_DEPLOYED_LABEL = '实盘档（整手+闲置现金后）';
const D2_CALIBER_IDEAL_SOURCE =
  'd2_signal_requirement.json::headline.account_annual_ceiling_pct';
const D2_CALIBER_DEPLOYED_SOURCE = 'deployed_d2_verdict.json::verdict';

/**
 * D2 缺口的两个口径 → **首屏并排显示**用的格子。
 *
 * ## 为什么是纯函数
 *
 * 首屏要并排摆两格，而 HTML 渲染在 `index.html` 里 ⇒ 判据（哪两格、各自多少）
 * 必须能在 **node 里真跑**，否则「并排」这件事只有肉眼能验（第 120 层）。
 * ⇒ 本函数只**读载荷、格式化**，不碰 DOM。
 *
 * ## 🚨 格式化只此一处
 *
 * `annual` / `gap` 的字符串在这里生成，`d2LedgerVerdict` 的正文也**用同一份**
 * ⇒ 判决里印的数与首屏格子里印的数**逐位相同**，结构上不可能分叉。
 *
 * ## 🚨 缺一档 ⇒ 只给另一档，**不许拿它顶替**
 *
 * 实盘档产物缺失 ⇒ 那格写「读不出来」，理想档照常给。
 * ⇒ 两档**都是独立的量**，谁也替不了谁（第 7 层）。
 *
 * @param {object|null} ledger 台账投影（`read_d2_gap().verdict_input`）
 * @returns {Array<{key,label,what,annual,gap,source,width}>}
 */
function d2GapCalibers(ledger) {
  const cells = [];

  const head = ledger?.headline;
  const ceil = head?.account_annual_ceiling_pct;
  if (ceil && typeof ceil === 'object') {
    const c50k = ceil.at_deliverable_width_and_50k;
    const gap50k = ceil.gap_to_target_at_deliverable_width_and_50k_pp;
    if (typeof c50k === 'number' && typeof gap50k === 'number') {
      cells.push({
        key: 'ideal',
        label: D2_CALIBER_IDEAL_LABEL,
        what: '满仓（100% 部署）假设下的**公式上界** —— 是「能做到的上限」，'
          + '不是真跑出来的数。',
        annual: `${c50k.toFixed(2)}%`,
        gap: `${gap50k.toFixed(2)}pp`,
        width: ceil.deliverable_width,
        source: D2_CALIBER_IDEAL_SOURCE,
      });
    }
  }

  const dep = ledger?.deployed;
  if (dep && dep.available === true
      && typeof dep.account_annual_pct === 'number'
      && typeof dep.gap_to_target_pp === 'number') {
    const a = dep.account_annual_pct;
    cells.push({
      key: 'deployed',
      label: D2_CALIBER_DEPLOYED_LABEL,
      what: '平台**真正会执行**的那个账户的**模拟净值**：整手取整 ⇒ '
        + '有一部分钱买不进、只能趴着当闲置现金（**按 0 计息**）。'
        + '⚠️ 闲置比例只认 `idle_total_before_fees_mean ÷ intended_equity_per_rebalance`；'
        + '`idle_cash_ratio_mean` 量的是**另一件事**，**不许**当闲置比例用。',
      // 正号写出来：这是一个**正**的年化读数（`docs/DELIVERY_DECISIONS_2026-10-20.md §16.2`）
      annual: `${a >= 0 ? '+' : ''}${a.toFixed(4)}%`,
      gap: `${dep.gap_to_target_pp.toFixed(4)}pp`,
      width: null,
      source: D2_CALIBER_DEPLOYED_SOURCE,
    });
  }

  return cells;
}

/**
 * 「还差几倍」那一族 —— **三个口径**（2026-10-21 台账新增 `..._by_caliber`）。
 *
 * ## 为什么这一族必须拆开
 *
 * 台账里现在有**三个**倍数，而**只有同口径那一列是「还差几倍」**：
 *
 * | 列 | 分子 | 分母 | 是不是缺口 |
 * |---|---|---|---|
 * | `same_caliber` | K=63 的要求 | K=63 · 生产臂 `LIVE_DEFAULT` 的实测毛超额 | ✅ **是** |
 * | `control_arms[*]` | K=63 的要求 | **对照臂**（非生产、非判决）的实测 | ⚠️ 对照臂 |
 * | `cross_caliber` | K=63 的要求 | **K=21 研究臂**的实测 | ❌ **不是**（台账显式标 `is_the_gap = false`）|
 *
 * 🚨 旧的裸键 `headline.multiple_needed_at_50k_deliverable` **就是 `cross_caliber` 那个**
 * ⇒ 它长得最像答案（一个孤零零的 float，没有口径名）而它**不是**缺口。
 * ⇒ **屏上那个「还差几倍」必须取 `same_caliber` 那一列**。
 *
 * ## 🚨 跨口径那个**可以**出现，但**必须带标签**
 *
 * 它此前就在屏上（不带标签）⇒ 静默删掉会让看过旧屏的人不知道它去哪了
 * ⇒ 按第 25 层「标注作废，不抹掉」：**保留 + 说清它不是缺口**。
 * ⇒ 它的标签**用产物自己的 `caliber_name`**（那句里逐字写着「不是缺口」）
 * ⇒ 屏上的措辞**不自己另写一句免责声明** ⇒ 产物改了标签，守卫就会红。
 *
 * @param {object|null} ledger 台账投影（或产物原形）
 * @returns {{same: object|null, cross: object|null}|null}
 */
function d2Multiples(ledger) {
  const head = ledger?.headline;
  const blk = head?.multiple_needed_at_50k_deliverable_by_caliber;
  if (!blk || typeof blk !== 'object') return null;
  const same = blk.same_caliber;
  const cross = blk.cross_caliber;
  const req = head.required_gross_at_50k_deliverable;
  const requiredText = typeof req === 'number' ? `${req.toFixed(2)}%` : null;
  return {
    same: (same && typeof same.multiple_needed === 'number') ? {
      value: same.multiple_needed,
      text: `${same.multiple_needed.toFixed(2)}×`,
      caliberName: typeof same.caliber_name === 'string' ? same.caliber_name : '',
      measuredText: typeof same.measured_gross_annual_pct === 'number'
        ? `${same.measured_gross_annual_pct.toFixed(2)}%` : null,
      requiredText,
      isTheGap: same.is_the_gap === true,
      source: 'd2_signal_requirement.json::headline.'
        + 'multiple_needed_at_50k_deliverable_by_caliber.same_caliber',
    } : null,
    cross: (cross && typeof cross.multiple_needed === 'number') ? {
      value: cross.multiple_needed,
      text: `${cross.multiple_needed.toFixed(2)}×`,
      caliberName: typeof cross.caliber_name === 'string' ? cross.caliber_name : '',
      isTheGap: cross.is_the_gap === true,
      kOfMeasured: cross.k_of_measured,
      kOfRequirement: cross.k_of_requirement,
      source: 'd2_signal_requirement.json::headline.'
        + 'multiple_needed_at_50k_deliverable_by_caliber.cross_caliber',
    } : null,
  };
}

/**
 * D2 缺口台账的判决 —— 读 `data/reports/d2_signal_requirement.json`
 * （**理想档**）与 `data/reports/deployed_d2_verdict.json`（**实盘档**），
 * **只读不算**。
 *
 * ## 这个函数存在的理由：封一条**活的**假绿
 *
 * `docketWarning` 第 7 支在 `cagr >= TARGET_CAGR` 时喊「达到 20% 目标」。
 * 那个 `cagr` 来自 `nDays >= 30` 的前瞻单笔底稿，而 30 天里 10 只股票涨 20%
 * **完全正常** ⇒ 这一支现实可期。而同一屏此刻必须能说的真话在台账里：
 * **理想档**（榜宽 10 / ¥5 万 / 满仓假设）的账户年化天花板与它的缺口，
 * 以及**实盘档**（平台真正会执行的那个账户：整手 ⇒ 投入率下降、
 * 闲置现金 0 计息）的年化与缺口；
 * 即使本金 → ∞ 且榜宽取该族最宽档，天花板仍低于目标
 * ⇒ **没有任何 (榜宽 × 本金) 组合能到 20%**。
 *
 * ⇒ 这些数字都在同一屏上时，第 7 支的「达标」是**假绿**，不是过时文案。
 *
 * ⚠️ **本 JSDoc 里刻意不写死那几个数**：它们**随 K 变**（K 21 → 63 时
 * 缺口与天花板都变过一轮）⇒ 手抄进注释就是**必烂的化石**（第 73 层）。
 * ⇒ 数值一律从产物读（`d2GapCalibers` / 本函数），**不许**在这里复述。
 *
 * ## 🚨 两个口径**必须并排**，各自带标签与来源（2026-10-21）
 *
 * 只给理想档 ⇒ 用户看到的「离 20% 还有多远」**少了整手 + 闲置现金那一截**；
 * 只给实盘档 ⇒ 用户看不到「满仓假设下最多能到哪」。
 * ⇒ 两个都给，且**标明是哪个口径**（`d2GapCalibers` 的标签常量只有一份）。
 *
 * ## 形状守卫为什么写在这里、且只写这一处
 *
 * 第 4 参是「袋」（JSDoc 里解释了为什么不能另开第 5 参），
 * 于是本函数**自己**认领形状：四个数全是 `number` 才继续，否则 `null`。
 * ⇒ `d2_signal_requirement.json` 没有 `weights_check`（第 1 支认不出它），
 *   `/api/predictions/latest` 的载荷没有 `headline`（本函数认不出它），
 *   两者**互不冒充**，所以这不是启发式。
 *
 * ## 三态，不是两态
 *
 * `null`（不是台账 / 读数不全 / **缺口非正**）⇒ 这条卡不说话。
 * ⚠️ 缺口非正时也闭嘴：那属于「台账说有缺口」之外的情形，
 * 让 `docketWarning` 的对照臂去处理，本函数不许抢话。
 *
 * ## 禁词：`|毛超额|/MDE < 1` 时不许说「达不到」「接近目标」「只差」
 *
 * 五档 `detectable` 全为 `false`、`|g|/MDE` 最大 0.81 ⇒ **连符号都定不了**。
 * 这种情况下「达不到」与「接近目标」同样是无信息的断言
 * —— 一个说没到，一个说快到了，而**两者都不可测**。
 *
 * ## 🚨 交付口径的两个数必须在 `detail` 里，**只放 `headline` 等于没上屏**
 *
 * 生产唯一命中的那一支（支 2，`weights.caveat` 有值）**保留自己的 headline**，
 * 只把本函数的 `detail` 拼到自己正文末尾 ⇒ **`headline` 在生产里没有通路**。
 * 实测（真实载荷 + 真实 `weights.caveat`）：改动前屏上（`detailHtml`）有
 * 12.73 / 15.56 / 3.23 / 0.81，而**台账那两个数一个都不在屏上**
 * （⚠️ 不在这里点名它们：台账是 K=63 口径，写死旧值就是留一颗化石），
 * 而预注册 §1.2 点名要上屏的正是这两行。
 * ⇒ 交付口径（`c50k` / `gap50k`）**必须**进 `detail`（`detail` 的第 0 段）。
 * ⚠️ 屏上还有一层：渲染层读的是 `detailHtml ?? esc(detail)`
 * （`index.html:1131`），而 `detailHtml` 由 `renderCaveat(detail)` 生成 ——
 * ⇒ 验「上了屏」必须验 `detailHtml`，验 `detail` 不算数（第 13 层）。
 */
function d2LedgerVerdict(ledger) {
  const head = ledger?.headline;
  const ceil = head?.account_annual_ceiling_pct;
  if (!ceil || typeof ceil !== 'object') return null;

  // ── 形状守卫：四个数缺一不可，缺一个就闭嘴 ──
  const c50k = ceil.at_deliverable_width_and_50k;                 // 真百分数
  const gap50k = ceil.gap_to_target_at_deliverable_width_and_50k_pp;
  const cInf = ceil.at_infinite_capital;                         // 上确界
  const gapInf = ceil.gap_to_target_at_infinite_capital_pp;
  if (![c50k, gap50k, cInf, gapInf].every(v => typeof v === 'number')) return null;
  // 缺口非正 ⇒ 台账没说「还差多少」⇒ 本条卡无话可说（不许抢对照臂的话）
  if (!(gap50k > 0)) return null;

  const w = ceil.deliverable_width;
  const widest = ceil.width;

  // ── 第 1 段：上确界。**必须带「哪个宽度/哪个本金」** ──
  // 只说「天花板 X%」而不说它是哪个宽度 + 哪个本金，
  // 用户会拿它与某个最宽档的数比 —— 而那一档在 ¥5 万 下摩擦极高、
  // 净为负，是最不可交付的一档（第 182 层同族：口径不跟着数字走）。
  // ⚠️ 上面那几个数**不写死**在这里：它们随 K 变 ⇒ 写死即化石（第 73 层）。
  const parts = [];
  // 🚨🚨 **两个口径并排**（2026-10-21）：理想档（满仓假设的公式上界）与
  // 实盘档（整手 + 闲置现金后的模拟账户）**不是同一个量**
  // ⇒ 差额就是「整手 + 闲置现金」的代价（`docs/DELIVERY_DECISIONS_2026-10-20.md §16.2`）
  // ⇒ **两个都要给，各自带标签与来源**；只给一个 ⇒ 少报或虚报。
  //
  // ⚠️ 数字的**格式化只有一处**（`d2GapCalibers`）⇒ 判决里印的与首屏格子里印的
  //    **逐位相同**，结构上不可能分叉（第 137 层）。
  const cells = d2GapCalibers(ledger);
  const ideal = cells.find(c => c.key === 'ideal');
  const dep = cells.find(c => c.key === 'deployed');
  if (!ideal) return null;          // 理论上到不了（上面已守住形状），但不许 `undefined`

  // ── 第 0 段（**交付口径，排在最前**）：用户真正关心的那一行 ──
  //
  // 🚨 它原来**只在自己的 `headline` 里**，而生产唯一命中的支 2
  // （`weights.caveat` 有值）保留自己的 headline、只搬 `ledger.detail`
  // ⇒ 实测屏上（`detailHtml`）有 12.73 / 15.56 / 3.23 / 0.81，而
  // **台账那两个数一个都不在屏上**——而预注册 §1.2 点名要上屏的就是这两行。
  // ⚠️ 这里不写死数值：口径随 K 变（当前 K=63），写死即化石。
  //
  // 排在第 1 段之前的另一个理由：第 1 段说的是**榜宽 546 + 本金 → ∞**，
  // 而那一档在 ¥5 万 下摩擦 130.97%、净 −123.40%，是最不可交付的一档。
  // 让它当第一句，读者第一眼读到的是「天花板 12.73%」（第 182 层：
  // 数字不跟着口径走）。
  parts.push(`交付口径 · ${ideal.label}：榜宽 ${w} · 本金 ¥5 万 时，`
    + `账户年化上限 ${ideal.annual}，距 20% 还差 ${ideal.gap}。`
    + `来源 ${ideal.source}。`);

  // ── 第 0b 段：**实盘档**（平台真正会执行的那个账户）──────────────
  //
  // 🚨 **不许用理想档的数顶替**：产物缺失时写「读不出来」，并说清缺的是哪一档。
  if (dep) {
    parts.push(`${dep.label}：账户年化 ${dep.annual}，距 20% 差 ${dep.gap}。`
      + `来源 ${dep.source}。`);
    // 🚨 形状纪律：产物自报连符号都定不了 ⇒ 既不许说「接近」，也不许说「达不到」。
    if (ledger?.deployed?.measured_sign_determinable === false) {
      parts.push('（实盘档：产物自报 `measured_sign_determinable = false` ⇒ '
        + '连符号都定不了 ⇒ 既不许说「接近」，也不许说「达不到」。）');
    }
  } else {
    parts.push(`（${D2_CALIBER_DEPLOYED_LABEL} 读不出来：`
      + `${ledger?.deployed?.note || '端点没给这一档'} —— 不拿理想档顶替。）`);
  }

  parts.push(`上限：榜宽 ${widest} 且本金 → ∞ 时账户年化最多 ${cInf.toFixed(2)}%，`
    + `距 20% 仍差 ${gapInf.toFixed(2)}pp —— 没有任何（榜宽 × 本金）组合能到 20%，`
    + '所以「再多攒些样本」不会抬高这个上限。');

  // ── 第 2 段（可选）：所需毛超额 ↔ 实测毛超额。**倍数只取同口径那一列** ──
  //
  // 🚨 2026-10-21：这一段原来打的是 `head.multiple_needed_at_50k_deliverable`
  //    = **2.35×**，而那个数**是跨口径的**（K=63 的要求 ÷ K=21 研究臂的实测）
  //    ⇒ 台账已显式判它 `is_the_gap = false` ⇒ **它不是缺口**。
  // ⇒ 现在打的是 `same_caliber` 那一列（同 K、同信号、同榜宽、同市场腿）。
  //
  // ⚠️ 少一个数就整段跳过，绝不格式化 undefined（第 168 层：`.get()` 猜键名 +
  //    格式化已经在本仓造成过一次 `TypeError: unsupported format string ... NoneType`）。
  // 🚨 **整块缺失 ⇒ 整段不出现**（而不是回落到那个裸 float）⇒ 失败方向是「少说一句」，
  //    不是「说一句错的」。
  const mult = d2Multiples(ledger);
  const same = mult?.same;
  if (same && same.measuredText && same.requiredText) {
    parts.push(`要达标，信号须交出毛超额 ${same.requiredText}`
      + `（${same.caliberName}：实测 ${same.measuredText}，还差 ${same.text}）。`);
    // 跨口径那个：**保留 + 说清它不是缺口**（不许静默删，也不许当缺口显示）。
    // ⚠️ **「不是缺口」这句话只由产物自己的 `caliber_name` 说**（那里面逐字写着）
    //    ⇒ 本段**不另写一遍免责声明** ⇒ 两份副本漂移时守卫会红（第 137 层）。
    const cross = mult?.cross;
    if (cross) {
      parts.push(`⚠️ 旧账上那个「还差 ${cross.text}」是${cross.caliberName}`
        + ` —— 台账已显式判它 **is_the_gap = false**`
        + `（分子是 K=${cross.kOfRequirement} 的要求、分母是 K=${cross.kOfMeasured} 的实测，`
        + `K 与信号两处口径都不同）⇒ 别拿它替代上面那一列。`);
    }
  }

  // ── 第 3 段：为什么「还差多少」这件事本身**测不出来** ──
  // 无量纲比值，来自 `rows[*].measured_gross_over_mde_abs`。
  // ⚠️ **不是** `measured_sign_determinable`（那个键名在产物里不存在，是散文里的词）。
  const ratios = (Array.isArray(ledger.rows) ? ledger.rows : [])
    .map(r => r?.measured_gross_over_mde_abs)
    .filter(v => typeof v === 'number');
  if (ratios.length) {
    parts.push(`这批读数连符号都定不了：五档 |毛超额|/MDE 最大 `
      + `${Math.max(...ratios).toFixed(2)} < 1。`);
  }

  return {
    // ⚠️ 缺口为正时**绝不为 'ok'** —— 这是本函数唯一不容协商的一条。
    level: 'warn',
    // 🚨 **两个口径都在标题里**（2026-10-21）：标题是屏上**永远可见**的那一行
    // （正文在 `<details>` 里，默认折叠）⇒ 少写一档 = 少报 2.2pp。
    headline: dep
      ? `${D2_CALIBER_IDEAL_LABEL} ${ideal.annual}（榜宽 ${w} · 本金 5 万）`
        + `差 ${ideal.gap} · ${D2_CALIBER_DEPLOYED_LABEL} ${dep.annual} 差 ${dep.gap}`
      : `${D2_CALIBER_IDEAL_LABEL} ${ideal.annual}（榜宽 ${w} · 本金 5 万），`
        + `距 20% 差 ${ideal.gap}`,
    detail: parts.join(' '),
  };
}

/**
 * 支 2 的正文 = `weights.caveat` **加上** D2 台账那一段。
 *
 * 🚨 **「只取它的 `detail`」这一句是承重的，不是省事**：交付口径的两个档
 * （理想档 / 实盘档）的年化与缺口**只在 `detail` 里**（`d2LedgerVerdict` 的
 * JSDoc 记了为什么）。哪天谁觉得「headline 更重要、应该把台账 headline 也搬上来」
 * 而去改这一支，屏上那两行会**静默消失**，而所有守卫照样全绿
 * —— 因为字段还在、函数还返回非 null。
 *
 * 台账不在袋子里时（`null`）**原样返回** —— 本函数不许把支 2 的正文改短。
 *
 * @param {string|null} caveat 生产实际的 `weights.caveat`（逐字透传）
 * @param {object|null} screen 第 4 袋（`d2LedgerVerdict` 按形状认领它）
 * @returns {string} 屏上正文
 */
function caveatDetail(caveat, screen) {
  const base = String(caveat ?? '');
  const ledger = d2LedgerVerdict(screen);
  if (!ledger || !ledger.detail) return base;
  return `${base}\n\n${ledger.detail}`;
}

/**
 * @param {object|null} stats       `/api/status` 的 stats（含 cagr/arith_ann/n_days）
 * @param {object|null} evo   `/api/evolution/summary`（含 champion_falsified/health）
 * @param {object|null} weights `/api/weights`（含 source/caveat/execution）
 * @param {object|null} screen 第 4 参是「屏上已知的其余读数」这个**袋子**，各项
 *   按**自己的形状**认领，不按位置认领（理由见下面「为什么第 4 参不许开新位置」）。
 * @returns {{level: string, headline: string, detail: string, detailHtml: string}|null}
 *          null = 无警示，判决板维持原样。
 *
 * ── 为什么第 4 参不许开新位置 ──
 * 这一槽历史上装的是 `predictions`，而**生产与既有守卫都按这个接线**：
 * `web/index.html` 传 `predictions`、`tests/test_stale_weights_warning.py`
 * 传 `p.predictions`。所以 D2 台账**不能另开第 5 个参数** ——
 * 那样生产那一处永远传不到台账闸，闸就在位而闸后面那条支是死的。
 * ⇒ 两条读法都按**形状**认领，谁也不冒充谁：
 *   · predictions：`weights_check` 在（出处过期那一支读它）
 *   · D2 台账：`headline.account_annual_ceiling_pct` 在（`d2LedgerVerdict` 读它）
 * 真产物里两者互不冒充：`d2_signal_requirement.json` 没有 `weights_check`，
 * `/api/predictions/latest` 的载荷没有 `headline` ⇒ 形状判据不是启发式。
 */
function docketWarning(stats, evo, weights, screen) {
  const falsified = evo?.champion_falsified === true;
  const cagr = stats?.cagr ?? null;
  const nDays = stats?.n_days ?? 0;

  // ── 最高优先级：屏幕上这张榜单的出处（2026-10-14）──
  //
  // 2026-10-14 实测：概览页显示的榜单来自 pred_2026-10-13.parquet，
  // 那是默认权重符号**整体翻转之前**生成的。那份榜单的看涨前 6 名
  // 20 日动量全是正的，而实测 IC 说 mom20 为负——符号修正后，
  // 看涨榜应该全是低动量股。用户要的是「明天会涨的 10 只」，
  // 屏幕上这张榜却是用一把实测会挑反的尺子排出来的，且页面一处说明都没有。
  //
  // 排在回落配置 caveat **之前**：caveat 讲的是「配置本身有多不可靠」，
  // 这条讲的是「**你看到的这份东西**到底代表什么」。榜单已经过期时，
  // 再解释当前配置有多没验过，是在回答次要问题——用户先需要知道的是
  // 「这张榜不能照着用」。
  //
  // `screen` 这袋里没有 `weights_check` 时不报警：那不是「查出不可知」，
  // 而是「压根没查」。渲染成「出处不可知」等于指控一份可能完全正常的榜单。
  //
  // ⚠️ 形状判据只写在下游函数里（`d2LedgerVerdict` 看 `headline`，
  // 这里看 `weights_check`），**本支不判**「这袋到底是啥」——
  // 一袋里两样都有时各支各读各的，缺了哪样哪支就闭嘴。
  const check = screen?.weights_check;
  if (check) {
    const status = check.status || DEFAULT_STALE_WEIGHTS_STATUS;
    if (status !== 'match') {
      const kind = STALE_WEIGHTS_HEADLINES[status]
        ? status : DEFAULT_STALE_WEIGHTS_STATUS;
      return {
        level: 'danger',
        headline: STALE_WEIGHTS_HEADLINES[kind],
        detail: staleWeightsDetail(kind, check, screen.date ?? '（日期未知）'),
      };
    }
  }

  // ── 次高：回落权重，且证据状态必须如实转述 ──
  //
  // `load_weights` 在回落时返回 `caveat`（正文）与 `caveat_kind`（证据
  // 状态）。正文是一次实测的完整记录，逐字透传；**标题由 kind 查表**，
  // 不硬编码——理由见 CAVEAT_HEADLINES 的注释。
  //
  // 这条排在「已被证伪」之前：它比证伪更根本——证伪说的是「这组权重
  // 在独立窗口里被判负」，而这里说的是「线上根本没在跑任何经过赛马的
  // 权重」。用户据此建仓亏钱，责任在配置本身。
  if (weights?.caveat) {
    const kind = weights.caveat_kind || DEFAULT_CAVEAT_KIND;
    // 🚨 支 2 是**生产唯一命中的那一支**（`weights.caveat` 有值），
    // 而支 7（唯一调 `d2LedgerVerdict` 的那一支）要求 `cagr >= 0.20`
    // 而实测 `cagr = -0.7365` ⇒ 台账**只在支 2 上有通路**。
    // ⇒ 正文必须并进去，否则屏上一个交付口径的字都没有（预注册 §3.2「只加」）。
    const text = caveatDetail(weights.caveat, screen);
    return {
      level: 'danger',
      headline: CAVEAT_HEADLINES[kind] || CAVEAT_HEADLINES[DEFAULT_CAVEAT_KIND],
      detail: text,
      detailHtml: renderCaveat(text),
    };
  }

  // ── 次高：现任权重已被证伪 ──
  // 进化页早就红字写着这件事，概览页却完全看不到。用户第一眼看到的是
  // 「命中率 49%，与抛硬币无异」——那句话没错，但它掩盖了更严重的事实：
  // 这组权重已经被独立窗口一致判负，是**待替换**的，不是「待观察」的。
  if (falsified) {
    const w = Number(evo?.champion_windows) || 0;
    return {
      level: 'danger',
      headline: '现任权重已被证伪',
      detail: `${w} 段互不重叠的样本外窗口一致显著为负。`
        + '这组权重是「待替换」，不是「待观察」——继续按它选股等于用一个已知无效的排序。',
    };
  }

  // ── 样本不足：年化数字不可解读 ──
  // 只测了 1 天时，CAGR 会被算成 (1+r)^252−1 这样的荒谬值（实测 −99.9%）。
  // 那不是「年化亏 99.9%」，而是「没测」。两者对用户是天壤之别。
  if (nDays > 0 && nDays < MIN_DAYS_FOR_CAGR) {
    return {
      level: 'warn',
      headline: '样本不足，年化不可解读',
      detail: `只测了 ${nDays} 个交易日，少于 ${MIN_DAYS_FOR_CAGR} 天。`
        + '此时的「年化」是把单日收益开 252 次方，没有意义——先看命中率和已判定笔数。',
    };
  }

  // ── 复利口径显著为负：命中率可能还行，但钱在亏 ──
  // 这是本次改动最关键的一条：命中率与赚钱是两件事。一个 51% 命中率的
  // 策略完全可能稳定亏钱——只要赢的时候赚得少、输的时候亏得多。
  // 实测现任权重就是这个形态：算术年化 −171.92%（物理上不可能），
  // 真实 CAGR −84.70%。
  if (cagr != null && cagr < 0) {
    const gap = (stats?.ann_vol ?? 0) ** 2 / 2;
    const detail = `按复利口径年化 ${(cagr * 100).toFixed(1)}%，`
      + `低于目标 ${(TARGET_CAGR * 100).toFixed(0)}%。`;
    return {
      level: 'danger',
      headline: '复利口径为负：命中率不等于赚钱',
      detail: detail + (
        Math.abs(gap) > 0.005
          ? `波动拖累约 ${(gap * 100).toFixed(1)}%/年——`
            + `算术年化 ${(stats.arith_ann * 100).toFixed(0)}% 高估了它，别看那个数。`
          : ''
      ),
    };
  }

  // ── 未达标但为正：距目标有多远 ──
  if (cagr != null && cagr < TARGET_CAGR) {
    return {
      level: 'warn',
      headline: `年化 ${(cagr * 100).toFixed(1)}%，距目标 ${(TARGET_CAGR * 100).toFixed(0)}% 还有距离`,
      detail: `已测 ${nDays} 个交易日。达标与否看复利口径，不看算术年化。`,
    };
  }

  // ── 达标 ──
  // ⚠️ 这一支在**台账有正缺口时不许裸奔**。`cagr` 来自 nDays ≥ 30 的前瞻单笔底稿，
  // 而 30 天里 10 只股票涨 20% 完全正常 ⇒ 这一支现实可期；
  // 而同一屏的台账逐字说账户年化天花板与缺口（理想档 + 实盘档两档并排）⇒ 两个数字同时在场时
  // 「达标」是**假绿**。闸在**这一支内部**而不是它前面：第 4 支（样本不足）
  // 必须先说「不可解读」（那是关于**这个点估计能不能读**的判决，不是关于 D2 的），
  // 第 6 支也不许被碰（`cagr < 0.20` 与台账一致，闸它=换判据臂，第 183 层）。
  if (cagr != null && cagr >= TARGET_CAGR) {
    const gate = d2LedgerVerdict(screen);
    if (gate) return gate;          // 台账说有缺口 → 这一支闭嘴
    return {
      level: 'ok',
      headline: `年化 ${(cagr * 100).toFixed(1)}%，达到 ${(TARGET_CAGR * 100).toFixed(0)}% 目标`,
      detail: `已测 ${nDays} 个交易日，按复利口径计算。`,
    };
  }

  return null;
}

// node 直接跑时：自检 + 打印
if (typeof require !== 'undefined' && require.main === module) {
  const cases = [
    ['现任被证伪（概览页此前毫无提示）',
      { cagr: 0.05, n_days: 483 }, { champion_falsified: true, champion_windows: 4 }],
    ['只测了 1 天 —— CAGR 荒谬',
      { cagr: -0.999, n_days: 1 }, null],
    ['复利为负（命中率可能还行）',
      { cagr: -0.847, arith_ann: -1.719, ann_vol: 0.546, n_days: 483 }, null],
    ['为正但未达标',
      { cagr: 0.11, arith_ann: 0.25, n_days: 483 }, null],
    ['达标',
      { cagr: 0.24, arith_ann: 0.35, n_days: 483 }, null],
    ['无复利数据（旧接口）',
      { overall_hit_rate: 0.5, judged: 20 }, null],
  ];
  for (const [label, stats, evo] of cases) {
    const w = docketWarning(stats, evo);
    console.log(`\n【${label}】`);
    console.log(w ? `  ${w.level.toUpperCase()}  ${w.headline}\n  ${w.detail}`
                  : '  （无警示）');
  }
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { docketWarning, d2LedgerVerdict, d2GapCalibers, d2Multiples,
                      caveatDetail,
                      renderCaveat, CAVEAT_HEADLINES,
                      STALE_WEIGHTS_HEADLINES, DEFAULT_STALE_WEIGHTS_STATUS,
                      D2_CALIBER_IDEAL_LABEL, D2_CALIBER_DEPLOYED_LABEL,
                      D2_CALIBER_IDEAL_SOURCE, D2_CALIBER_DEPLOYED_SOURCE,
                      MIN_DAYS_FOR_CAGR, TARGET_CAGR };
}
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

/**
 * D2 缺口台账的判决 —— 读 `data/reports/d2_signal_requirement.json`，**只读不算**。
 *
 * ## 这个函数存在的理由：封一条**活的**假绿
 *
 * `docketWarning` 第 7 支在 `cagr >= TARGET_CAGR` 时喊「达到 20% 目标」。
 * 那个 `cagr` 来自 `nDays >= 30` 的前瞻单笔底稿，而 30 天里 10 只股票涨 20%
 * **完全正常** ⇒ 这一支现实可期。而同一屏此刻必须能说的真话在台账里：
 * 宽度 10 / ¥5 万 / 等权下账户年化天花板 **8.34%**，离 20% 差 **11.66pp**；
 * 即使本金 → ∞ 且榜宽 → 546（该族最宽档）天花板也只有 **12.73%**，仍差 **7.27pp**
 * ⇒ **没有任何 (榜宽 × 本金) 组合能到 20%**。
 *
 * ⇒ 两个数字都在同一屏上时，第 7 支的「达标」是**假绿**，不是过时文案。
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
 * 12.73 / 15.56 / 3.23 / 0.81，而 **8.34% 与 11.66pp 一个字都没有**，
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
  // 只说「天花板 8.34%」而不说它是宽度 10 + ¥5 万，
  // 用户会拿它与某个 546 只的数比 —— 而那一档在 ¥5 万 下摩擦 130.97%、
  // 净 −123.40%，是最不可交付的一档（第 182 层同族：口径不跟着数字走）。
  const parts = [];
  // ── 第 0 段（**交付口径，排在最前**）：用户真正关心的那一行 ──
  //
  // 🚨 它原来**只在自己的 `headline` 里**，而生产唯一命中的支 2
  // （`weights.caveat` 有值）保留自己的 headline、只搬 `ledger.detail`
  // ⇒ 实测屏上（`detailHtml`）有 12.73 / 15.56 / 3.23 / 0.81，而
  // **8.34% 与 11.66pp 一个字都没有**——而预注册 §1.2 点名要上屏的就是这两行。
  //
  // 排在第 1 段之前的另一个理由：第 1 段说的是**榜宽 546 + 本金 → ∞**，
  // 而那一档在 ¥5 万 下摩擦 130.97%、净 −123.40%，是最不可交付的一档。
  // 让它当第一句，读者第一眼读到的是「天花板 12.73%」（第 182 层：
  // 数字不跟着口径走）。
  parts.push(`交付口径：榜宽 ${w} · 本金 ¥5 万 时，账户年化上限 ${c50k.toFixed(2)}%，`
    + `距 20% 还差 ${gap50k.toFixed(2)}pp。`);

  parts.push(`上限：榜宽 ${widest} 且本金 → ∞ 时账户年化最多 ${cInf.toFixed(2)}%，`
    + `距 20% 仍差 ${gapInf.toFixed(2)}pp —— 没有任何（榜宽 × 本金）组合能到 20%，`
    + '所以「再多攒些样本」不会抬高这个上限。');

  // ── 第 2 段（可选）：所需毛超额 ↔ 实测毛超额。**三个数必须同时是数** ──
  // 少一个就整段跳过，绝不格式化 undefined（第 168 层：`.get()` 猜键名 + 格式化
  // 已经在本仓造成过一次 `TypeError: unsupported format string ... NoneType`）。
  const required = head.required_gross_at_50k_deliverable;
  const measured = head.measured_gross_best?.at_deliverable_width;
  const multiple = head.multiple_needed_at_50k_deliverable;
  if ([required, measured, multiple].every(v => typeof v === 'number')) {
    parts.push(`要达标，信号在榜宽 ${w} · ¥5 万下须交出毛超额 ${required.toFixed(2)}%`
      + `（实测 ${measured.toFixed(2)}%，还差 ${multiple.toFixed(2)}×）。`);
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
    headline: `账户年化天花板 ${c50k.toFixed(2)}%（榜宽 ${w} · 本金 5 万），`
      + `距 20% 差 ${gap50k.toFixed(2)}pp`,
    detail: parts.join(' '),
  };
}

/**
 * 支 2 的正文 = `weights.caveat` **加上** D2 台账那一段。
 *
 * 🚨 **「只取它的 `detail`」这一句是承重的，不是省事**：交付口径的
 * `8.34%` / `11.66pp` **只在 `detail` 里**（`d2LedgerVerdict` 的 JSDoc 记了
 * 为什么）。哪天谁觉得「headline 更重要、应该把台账 headline 也搬上来」
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
  // 而同一屏的台账逐字说账户年化天花板 8.34%、缺口 11.66pp ⇒ 两个数字同时在场时
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
  module.exports = { docketWarning, d2LedgerVerdict, caveatDetail, renderCaveat, CAVEAT_HEADLINES,
                      STALE_WEIGHTS_HEADLINES, DEFAULT_STALE_WEIGHTS_STATUS,
                      MIN_DAYS_FOR_CAGR, TARGET_CAGR };
}
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
 * @param {object|null} stats       `/api/status` 的 stats（含 cagr/arith_ann/n_days）
 * @param {object|null} evo         `/api/evolution/summary`（含 champion_falsified/health）
 * @param {object|null} weights     `/api/weights`（含 source/caveat/execution）
 * @param {object|null} predictions `/api/predictions/latest`（含 weights_check）
 * @returns {{level: string, headline: string, detail: string, detailHtml: string}|null}
 *          null = 无警示，判决板维持原样。
 */
function docketWarning(stats, evo, weights, predictions) {
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
  // `predictions` 整个取不到时不报警：那不是「查出不可知」，
  // 而是「压根没查」。渲染成「出处不可知」等于指控一份可能完全正常的榜单。
  const check = predictions?.weights_check;
  if (check) {
    const status = check.status || DEFAULT_STALE_WEIGHTS_STATUS;
    if (status !== 'match') {
      const kind = STALE_WEIGHTS_HEADLINES[status]
        ? status : DEFAULT_STALE_WEIGHTS_STATUS;
      return {
        level: 'danger',
        headline: STALE_WEIGHTS_HEADLINES[kind],
        detail: staleWeightsDetail(kind, check, predictions.date ?? '（日期未知）'),
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
    return {
      level: 'danger',
      headline: CAVEAT_HEADLINES[kind] || CAVEAT_HEADLINES[DEFAULT_CAVEAT_KIND],
      detail: weights.caveat,
      detailHtml: renderCaveat(weights.caveat),
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
  if (cagr != null && cagr >= TARGET_CAGR) {
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
  module.exports = { docketWarning, renderCaveat, CAVEAT_HEADLINES,
                      STALE_WEIGHTS_HEADLINES, DEFAULT_STALE_WEIGHTS_STATUS,
                      MIN_DAYS_FOR_CAGR, TARGET_CAGR };
}
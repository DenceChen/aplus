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

/**
 * @param {object|null} stats  `/api/status` 的 stats（含 cagr/arith_ann/n_days）
 * @param {object|null} evo    `/api/evolution/summary`（含 champion_falsified/health）
 * @param {object|null} weights `/api/weights`（含 source/caveat/execution）
 * @returns {{level: string, headline: string, detail: string, detailHtml: string}|null}
 *          null = 无警示，判决板维持原样。
 */
function docketWarning(stats, evo, weights) {
  const falsified = evo?.champion_falsified === true;
  const cagr = stats?.cagr ?? null;
  const nDays = stats?.n_days ?? 0;

  // ── 最高优先级：跑的是一个已测量的坏配置 ──
  //
  // `load_weights` 在回落时返回的 caveat 写得很准：
  //   「回落默认权重：……在 730 天赛马里排第 81/87 名，测试段超额 −0.74%、
  //     t=−3.03 —— 这是**已知的坏配置**，不是「中性起点」。日频执行它的
  //     年成本量级 351%。」
  //
  // 这条排在「已被证伪」之前：它比证伪更根本——证伪说的是「这组权重
  // 在独立窗口里被判负」，而这里说的是「线上根本没在跑任何经过赛马的
  // 权重，跑的是一个已知排倒数第 6 的默认值」。用户据此建仓亏钱，
  // 责任在配置本身。
  if (weights?.caveat) {
    return {
      level: 'danger',
      headline: '当前跑的是已知的坏配置',
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
  module.exports = { docketWarning, renderCaveat, MIN_DAYS_FOR_CAGR, TARGET_CAGR };
}
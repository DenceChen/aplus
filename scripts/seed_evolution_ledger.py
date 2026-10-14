"""把 2026-10-11 这一轮**实测**的改进灌进台账。

每条 before/after 都是当天真跑出来的数字，不是估计值。
数字变差的条目（`worse`）必须与变好的并列——见 `aplus/ledger.py` 顶部说明。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aplus.ledger import record_improvement  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
D = "2026-10-11"

# (title, category, severity, metric, before, after, note, invalidates, evidence)
ENTRIES = [
    dict(
        title="涨停/停牌可交易性过滤",
        category="correctness", severity="critical",
        metric="涨篮子日均超额（回测）",
        before=0.001965, after=-0.000067,
        # 超额本身「越大越好」，所以这里下降 = 真的变差。
        # 但它仍然是一条必要的改进——见 note：虚高来自买不进的交易。
        higher_is_better=True,
        note=("52% 的超额来自**根本买不进的**交易（一字涨停/停牌）。"
              "实测涨篮子里 15.58% 的样本买不进，全市场仅 2.60%，集中度 6 倍。"
              "修复后超额变成负数——这是把虚高修成真实，不是策略变差。"),
        invalidates=["「涨篮子超额 +0.1965%」", "所有未做可交易性过滤的历史结论"],
        evidence="aplus/panel.py: can_buy / buyable_series；tests/test_tradability.py（41 测试，9/9 变异体被杀）",
    ),
    dict(
        title="IVOL 全时间轴前视泄露",
        category="data-leak", severity="fatal",
        metric="面板中 ivol 的时间轴取值条数（应为逐日不同）", higher_is_better=True,
        before=1, after=12,
        note=("_factor_series 末尾写成 `ivol_series(r, m).iloc[-1]`——取**数据末端**的 ivol "
              "广播给窗口内每一天。等于回测里任意历史日都用 2026-10-09 的数据算 ivol。"
              "不崩、不报 nan、每列都有区分度，只靠「污染未来看 D 变不变」才抓得到。"
              "指标含义：面板 ivol 从 1 个常数变成随日期变化的序列。"),
        invalidates=["2 年终裁 v2 冠军 {ivol:-1.0}（t=+0.91）",
                     "频率赛马 t=2.32 的 {mom20:-0.5, turnratio:-0.5, ivol:-0.3}",
                     "此前一切含 ivol 的回测数字"],
        evidence="aplus/panel.py: _factor_series；tests/test_ivol_no_leak.py（3 测试，4/4 变异体被杀）",
    ),
    dict(
        title="实盘名单出现一字涨停买不进的股票",
        category="correctness", severity="critical",
        metric="榜首股票 20 日动量（越小=越正常）", higher_is_better=False,
        before=1.419, after=0.589,
        note=("实盘走 snapshot.py，其 COLUMNS 缺 buyable 列，filter_universe 看不到。"
              "2026-10-11 榜首是 600825.SH 新华传媒——连续 9 个一字涨停、"
              "收盘价就是涨停价，排单进不去。修复后它从榜首消失。"),
        invalidates=["2026-10-11 修复前的实盘名单"],
        evidence="aplus/snapshot.py: COLUMNS；tests/test_snapshot_buyable.py（7 测试）",
    ),
    dict(
        title="缩尾硬 clip 造成排名并列",
        category="correctness", severity="major",
        metric="Top10 内部并列个数", higher_is_better=False,
        before=9, after=0,
        note=("winsorize() 用硬 clip 把超 99% 分位的值全压成同一数，"
              "真实横截面（2026-10-09，5215 只）实测最大并列组 53 只，"
              "Top10 里 9 个并列 → Top10 实际退化成按股票代码字母序取前十。"
              "改用 robust_z：缩尾只估均值/标准差，排名一律用原值（仿射变换保序）。"),
        invalidates=["修复前的任何 Top-N 名单（并列时排序由代码序决定）"],
        evidence="aplus/factor.py: robust_z；tests/test_rank_fidelity.py（9 测试，7/7 变异体被杀）",
    ),
    dict(
        title="交叉校验容差误伤，13 只股票从池子消失",
        category="data-integrity", severity="major",
        metric="被隔离且 master 文件不存在的股票数", higher_is_better=False,
        before=13, after=0,
        note=("tol_rel=1e-4 判不出腾讯按 100 股取整的成交量，513 个失配行 100% 可被 ±100 股吸收"
              "（最大相对误差 0.0864%），却导致 36 只被隔离，其中 13 只 master 根本不存在——"
              "从股票池静默消失。修法：只给 volume 字段加绝对容差 100 股，"
              "价格字段绝不放宽相对容差（那会让真正的价格错配蒙混过关）。"),
        invalidates=["隔离区 36 只股票的所有历史统计"],
        evidence="aplus/crosscheck.py: ABS_TOL_BY_FIELD；tests/test_crosscheck_lot.py（8 测试，4/4 变异体被杀）",
    ),
    dict(
        title="/api/backtest 返回 500",
        category="availability", severity="major",
        metric="HTTP 状态码", higher_is_better=False,
        before=500, after=200,
        note=("daily_series 485 行里 20 行吐 NaN，Starlette JSONResponse 用 allow_nan=False 直接抛。"
              "修法：算完后把 NaN/Inf 统一转 None。0.0 是「恰好持平」，None 是「算不出来」，"
              "两者不能混。"),
        invalidates=[],
        evidence="aplus/backtest.py: _finite_or_none；tests/test_daily_series_json.py（8 测试）",
    ),
    dict(
        title="实盘单日窗口返回空横截面",
        category="correctness", severity="critical",
        metric="实盘横截面返回行数", higher_is_better=True,
        before=0, after=5460,
        note=("build_cross_section 写的是 build_panel(store, as_of, as_of)，"
              "而 build_panel 的 end 是**开区间**，start==end 直接得到空 dates，"
              "cross_section(0) 抛 IndexError。当天出不了名单。"
              "修法：显式算窗口（panel_window_for）+ build_panel 对 start>=end 当场抛错。"),
        invalidates=[],
        evidence="aplus/panel.py: build_panel 入参防线；tests/test_panel_window.py（7 测试，5/5 变异体被杀）",
    ),
    dict(
        title="调仓缓冲区（3×）压低换手成本",
        category="cost", severity="major",
        metric="日频年化交易成本", higher_is_better=False,
        before=0.1691, after=0.0057,
        note=("缓冲区：持仓跌出目标名次 3 倍才卖。36 组频率赛马里 10/10 降成本、"
              "9/10 升或平超额，且该档超额同时从 +0.121% 升到 +0.155%。"
              "本档权重 {ivol:-1.0}；buffer_multiple 在看结果前固定为 3.0，"
              "并有测试断言它非恒等（真的改变了篮子）。"),
        invalidates=["任何未计缓冲区的换手率与年成本估计"],
        evidence="aplus/rebalance.py；tests/test_rebalance.py（20 测试，5/5 变异体被杀）、tests/test_rebalance_backtest.py（7 测试）",
    ),
    dict(
        title="降级写入破坏列完整性",
        category="data-integrity", severity="major",
        metric="被降级写入覆盖的已有列数", higher_is_better=False,
        before=12, after=0,
        note=("baostock 被限流时腾讯只返回 7 列，直接 upsert 会把主库的其余列写成 NaN。"
              "修法 _merge_preserving_fidelity：择多+少者填空——列取并集、"
              "信息量=非空格数、并列时新行胜出、落选行只补空格。"
              "实测 12 列保住、重叠日 close=2.0 未被窄行 99.0 覆盖、turn=2.5 未变 NaN。"),
        invalidates=["降级运行时段写入的所有日线"],
        evidence="aplus/store.py: _merge_preserving_fidelity；aplus/pipeline.py: _emit",
    ),
    dict(
        title="进化 tab 缺少判决板（把全否决读成有进展）",
        category="honesty", severity="major",
        metric="界面上可见的赛马轮次/晋级次数", higher_is_better=True,
        before=0, after=8,
        note=("tab 只画候选超额表，一屏绿色（A股绿=跌），却没有一行字说明"
              "「8 轮赛马、0 次晋级、线上跑的是未经验收的默认权重」。"
              "用户据此问「是不是记录了每次自进化改进带来的提升」。"
              "补 /api/evolution/summary 判决板 + 本改进台账。"
              "指标含义：界面上可见的轮次从 0 变 8（真实值此前一直存在，只是没显示）。"),
        invalidates=[],
        evidence="aplus/webapp.py: /api/evolution/summary；tests/test_evolution_summary.py（8 测试）",
    ),
    dict(
        title="11-1 月动量（书 3.5 节）实测最弱，决定不实现",
        category="negative-result", severity="info",
        metric="2 年终裁测试段日均超额", higher_is_better=True,
        before=0.00221, after=0.00067,
        note=("对照 mom20=+0.221%、11-1 反转=+0.180%。书里 3.5 节主推的 11-1 月动量"
              "在本数据上最弱。不实现是一次有价值的**不作为**，但必须留档，"
              "否则下个轮次会重新发明它。指标方向 worse 表示实测更差。"),
        invalidates=[],
        evidence="/tmp/aplus_snap_final/reports/ + scripts/_diag_mom121.py",
    ),
    dict(
        title="IVOL 泄露修复后，它从第一名掉到近乎为零",
        category="negative-result", severity="fatal",
        metric="{ivol:-1.0} 的 2 年终裁测试段超额",
        before=0.0006, after=0.0002,
        higher_is_better=True,
        note=("泄露版下 `{ivol:-1.0}` 是终裁**第一名**（t=+0.91）；"
              "修好前视后同一组只剩 +0.02%，从第 1 名掉到第 6 名，"
              "`{mom20:1,rev5:0.3,ivol:-0.8}` 从 +0.06% 变成 **−0.12%**。"
              "那个「第一名」不是因子有效，是数据末端的信息漏到了过去——"
              "本轮泄露缺陷最直接的证据。"),
        invalidates=["「ivol 低波动有 alpha」这一结论的全部历史证据"],
        evidence="/tmp/aplus_snap_final/reports/race_final_2y_v3.json（31 组，1815s）",
    ),
    dict(
        title="线上现任权重样本外显著为负（t=−2.83）",
        category="negative-result", severity="fatal",
        metric="现任 {mom20:1.0, rev5:0.3, vol20:0.5, vsur:0.5} 的测试段涨腿超额",
        before=0.0006, after=-0.0060,
        higher_is_better=True,
        note=("ivol 修复后重跑 31 组：现任在**样本外**涨腿超额 **−0.5978%/日**，"
              "`excess_t=−2.83`、`breadth_t=−3.39`，命中率 **47.46%**（低于抛硬币），"
              "涨篮子平均收益 −0.66%。这比「没有 alpha」更糟：是**显著为负**。"
              "叠加日频 34.22% 的年化换手成本，实际是负负。"
              "**没有擅自替换它**——31 组里没有一组过得了 Bonferroni 门槛，"
              "换成任何一组都只是换一组噪音，那正是本平台反复出现过的错误动作。"),
        invalidates=["「默认权重至少持平」的假设"],
        evidence="/tmp/aplus_snap_final/reports/race_final_2y_v3.json",
    ),
    dict(
        title="RSI 横截面排序无独立预测力（第二次裁决）",
        category="negative-result", severity="major",
        metric="{rsi:-1.0} 的 2 年终裁测试段超额",
        before=0.0022, after=-0.0004,
        higher_is_better=True,
        note=("第一次裁决是**共线性**：与 mom20 的 Spearman +0.8648、Top10 重叠 3/10。"
              "第二次是**样本外**：5 组 RSI 候选全部 ≤0，最好 `{rsi:-1.0}` 仅 −0.04%，"
              "反号 `{rsi:+1.0}` −0.46%，现任加 RSI −0.52%。"
              "结论：RSI 作为横截面因子在 2 年窗口上**没有可用的独立信息**。"
              "保留实现（零成本、随时可复核），但不参与权重。"),
        invalidates=["「RSI 可替代或补充 mom20」这一设想"],
        evidence="/tmp/aplus_snap_final/reports/race_final_2y_v3.json；tests/test_rsi.py（16 测试）",
    ),
    dict(
        title="定位现任失败的具体机制：极端动量集中度 370 倍",
        category="diagnosis", severity="critical",
        metric="涨榜 Top10 占全市场 mom20>100% 池子的比例（期望 0.18%）",
        before=0.667, after=0.667,
        higher_is_better=False,
        note=("2026-10-11 实测横截面（5441 只）：`mom20>100%` 全市场只有 **3 只**，"
              "而涨榜 Top10 占了 **2 只 = 66.7%**；`mom20>50%` 全市场 27 只，Top10 占 6 只。"
              "涨榜 mom20 中位数 **56.1%**。"
              "现任权重 `mom20:1.0`，于是 10 个名额里有 2 个花在全市场仅有的 3 只极端动量股上。"
              "这就是「样本外 t=−2.83」的**机制**：不是动量因子没用，"
              "是 10 只的样本量让它退化成「追逐极端」。"
              "两个已验证的对照：`{mom20:-1.0}`（纯 20 日反转）测试超额 **+0.03%**，"
              "而现任是 **−0.60%**——方向反过来就翻正。"
              "指标为诊断量（before=after），不构成改善，只是把黑箱打开。"),
        invalidates=["「现任失效原因不明，只能换权重」的含糊认知"],
        evidence="scripts/ 诊断（横截面 5441 只，2026-10-09 截面）；race_final_2y_v3.json",
    ),
    dict(
        title="加第五道闸：方向污染（看过测试数据才选的方向不得晋级）",
        category="methodology", severity="fatal",
        metric="晋级裁决的闸门数",
        before=4, after=5,
        higher_is_better=True,
        note=("原候选池 10 组**全是动量向前**，反转方向根本没被试过——"
              "这是历次赛马找不到反转的系统性原因。"
              "加上反转族之前必须先回答一个问题：**我已经看过这 2 年测试段的结果了**，"
              "「反转更好」这个认识本身就来自测试段。训练/测试切分挡不住这一种，"
              "Bonferroni 也数不了（它数「跑了几组」，数不了「事先知道往哪猜」）。"
              "因此新增第五道闸：标记为方向污染的候选即便四条腿全过，"
              "也只判**待前瞻验证**，不写 weights.json，等没被看过的交易日裁决。"
              "这条闸只挡「方向事后选」，挡不住「因子本身是我挖出来的」——"
              "后者只能靠前瞻数据。"),
        invalidates=["「通过训练/测试切分就等于没有被污染」的假设"],
        evidence="aplus/evolve_race.py: oos_decision 第五闸 + DIRECTION_TESTS；tests/test_direction_pollution.py（7 测试，6/6 变异体被杀）",
    ),
    dict(
        title="报告里的自我警告没进裁决：差点让 turnratio 候选带着 7.3% 的股票池缺口晋级",
        category="correctness", severity="critical",
        metric="晋级裁决的闸门数",
        before=5, after=6,
        higher_is_better=True,
        note=("2 年终裁报告末尾白纸黑字写着「各候选可打分股票数不一致，**不在同一批标的上比较**，"
              "排名只能作参考」——而 `oos_decision` **完全没看它**。"
              "会自我警告但照样参与排名的报告等于没有警告：冠军照常产生、晋级照常走完。"
              "实测（v3 测试段，全宇宙 4899 只）：无换手率因子者 n_ranked=4897；"
              "含 `turn20` 者 4876；**含 `turnratio` 者只有 4541（92.7%）**。"
              "缺的那 7.3% 恰恰是**换手率数据缺失的那批**（当日末根 turn 缺 62.0%、约 3385 只）——"
              "不是随机缺失而是系统性缺失，Top-N 天然不可比。"
              "**而 `{turnratio:-1.0}` 正是那一届的第 2 名**（测试超额 +0.06%）："
              "这道闸很可能真的挡住了一次错误晋级。现已加为第六道闸，差异超 2% 即否决，"
              "理由写出双方具体只数；闸序在显著性之后，两条都不过时先报更硬的那条。"),
        invalidates=["「报告里已经警告过了」的自我宽容"],
        evidence="aplus/evolve_race.py: oos_decision 股票池闸；tests/test_universe_comparability.py（8 测试，5/5 变异体被杀）",
    ),
    dict(
        title="独立验证 2019-2023：现任 t=−3.55 强显著为负，反转 t=+5.52 强显著为正",
        category="validation", severity="fatal",
        metric="{mom20:-1.0} 相对现任的测试段超额（2019-2023 独立验证集）",
        before=-0.0049, after=0.0041,
        higher_is_better=True,
        note=("反转方向来自 2024-10~2026-10 的观察（属方向污染），"
              "因此必须用**我从未看过任何一次赛马结果的** 2019-01~2023-12 来验证。"
              "结果（训练 723 日 / 测试 486 日，股票池 3362 vs 3374 可比）："
              "现任 −0.55%(t=−4.76) / −0.49%(t=−3.55，广度t=−6.73)；"
              "纯 20 日反转 +0.23%(t=+3.52) / **+0.41%(t=+5.52，广度t=+3.77)**。"
              "反转的**测试段 t 大于训练段 t**（+5.52 > +3.52）——不是记住训练段，"
              "是在更晚的数据上更有效。"
              "四重独立：时间 / 段 / 超额与广度双指标 / 股票池可比。"),
        invalidates=["「现任只是没有 alpha，只是运气不好」——它是**显著为负**",
                     "「反转方向只是 2024-2026 的噪音」"],
        evidence="scripts/_verify_reversal.py → data/reports/independent_validation_2019_2023.json（1214 个交易日，221s）",
    ),
    dict(
        title="20:00 赛马：14 组零晋级，但暴露闸门体系的缺口——没有「现任否决」这一道",
        category="methodology", severity="fatal",
        metric="现任在独立窗口上的累计健康度",
        before=-0.0057, after=-0.0057,
        higher_is_better=False,
        note=("14 组赛马（2024-10~2026-10）：现任排第 10（测试超额 −0.57%），"
              "最好的一组 +0.04% 但 t=+0.37 < Bonferroni 临界 2.91 → 零晋级。"
              "**六道闸全部正确工作**，第 1 名含 turnratio、股票池窄 7.3% 被第六道闸拦下，"
              "正是它当初要拦的东西。"
              "但把时间轴拉长：现任在 3 段独立窗口（合计约 5 年）全部显著为负，"
              "而 `{mom20:-1.0}` 在同样两段上是 +0.41%(t=5.52) 与 +0.02%——"
              "**都不显著，但都不是负的**。"
              "缺口在于：闸门只管「什么能进来」，不管「什么该被换掉」，"
              "于是「没有更好的候选」被当成了「现在这个可以」。"
              "已加第七道闸 `evaluate_champion_health`：两段以上**互不重叠**窗口"
              "都显著为负即判证伪，重叠窗口只算一段（同一段行情数两次会虚增信心）。"
              "指示灯为 flat：这是新增机制，不是策略数字变化。"),
        invalidates=["「裁决写着维持现任，就说明现任没问题」"],
        evidence="data/evolve.log；aplus/evolve_race.py: evaluate_champion_health；tests/test_champion_falsified.py（8 测试，5/5 变异体被杀）",
    ),
    dict(
        title="独立验证：调仓频率比权重更决定成败（反转月频净 +77.6%，日频净 −168.9%）",
        category="validation", severity="fatal",
        metric="纯反转 {mom20:-1.0} 的年化净值（2019-2023 干净数据，扣成本后）",
        before=-1.689, after=1.057,
        higher_is_better=True,
        note=("同一组权重、同一段干净数据，只改调仓频率："
              "日频 净 −168.9%(t=6.32，成本 246.3%)｜周频 −5.3%(t=8.01)｜"
              "**月频 +77.6%(t=9.19，成本 35.8%)**｜**季频 +105.7%(t=11.17，成本 12.2%)**。"
              "t 值本身随降频单调上升（6.32→8.01→9.19→11.17），说明低频不只是省成本，"
              "还让信号本身更干净。"
              "现任同样降频后净也转正（季频 +64.3%，t=5.44），但**广度 t 全为负**"
              "（−3.49/−2.22）——涨腿好、跌腿差，排序两端没分开，是整体偏移而非选股能力；"
              "反转的广度 t 全为正（+3.89→+2.10），两端都分得开。"
              "含 turnratio 的第三组 t 更高（12.49）但股票池窄 7.3%，正是第六道闸拦的对象。"),
        invalidates=["「先修好权重再说，频率以后再调」——在这个资金规模下顺序反了"],
        evidence="scripts/_verify_reversal_freq.py → data/reports/reversal_freq_2019_2023.json（24 组，1214 个交易日）",
    ),
]


def main() -> int:
    for e in ENTRIES:
        e = dict(e, date=D)
        row = record_improvement(ROOT / "data", e)
        print(f"[{row['direction']:>6}] {row['title']}  "
              f"{row['metric']}: {row['before']} → {row['after']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

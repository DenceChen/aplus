# 从 Chan《Quantitative Trading》(2nd ed.) 挖策略配方 —— 2026-10-14

> **页码口径**：本文所有页码 = **PDF 页码**（用 Read 工具的 `pages` 参数核对）。
> 书内印刷页码 = PDF 页码 **− 24**（已核对：印刷 p.101「Minimizing Transaction Costs」= PDF p.125）。
> 例：PDF p102 = 印刷 p78。
> 本轮为**只读**挖掘：未改 `aplus/` 任何代码、未改 `data/reports/*.json`、未动数据库。
> 本文件与既有 `docs/methodology_chan.md` 有重叠，已在条目上标注 **[已在旧文档]** / **[新增]**。

## 零、先说三个「负结果」（比正面结论更重要）

1. **全书 0 次出现 "turnover" 作为因子**。`grep -inE "\bturnover\b" data/books/chan.txt` → **零命中**。
   书里谈成交量的地方**全部**是「成交量用来估算自己的订单冲击」（执行问题，PDF p125/p126/p132），
   不是「成交量异常作为选股 alpha」。**C 类（异常换手/流动性因子）在本书里没有配方。**
   → 平台当前的冠军因子 `turnratio` **不能**从这本书找到任何支持或反驳依据。
2. **全书 0 次出现 "Amihud"**。"illiquid/illiquidity" 仅 3 次（PDF p36、p132、p208），
   且全部是** adjectival 的执行层描述**（"illiquid stocks that caused market impact"），非因子定义。
   流动性只作为**约束**出现（"1% of ADV"、"$5 价格下限"）。
3. **书里没有任何 hysteresis / 阈值带 / 信号平滑的讨论**。A 类里"第三种办法"确实存在，
   但**不是**平滑信号，而是**四条执行/组合层的办法**（见 A1–A4）。

---

## A. 降低换手 / 降低成本（书里的原话与原公式）

### A1. [新增] 避开低价股：机构惯例是**股价低于 $5 一律不碰** —— PDF p125（印刷 p101）

> **原话**："To cut down on commissions, you can refrain from trading low-priced stocks.
> Typically, institutional traders do not trade any stocks with prices lower than $5.
> Not only do low-price stocks increase your total commissions costs (since you need to
> buy or sell more shares for a fixed amount of capital), percentage-wise they also have
> a wider bid–ask spread and therefore increase your total liquidity costs."

**为什么这条对 aplus 特别值钱（推断）**：平台当前成本是 **5 元最低佣金**，单只 2500 元 → 单边 0.2%。
5 元最低佣金与**股数**无关，只与**笔数**有关；而**一手 = 100 股**（A 股最小交易单位，书中美股语境无此约束）。
→ 推断：低价股在 A 股这里有**书里没有的额外惩罚**——2500 元买一股 3 元 = 833 股 = 9 手（取整到 900 股 = 2700 元，
超买 8%）；买一股 30 元 = 83 股 = 1 手（取整 100 股 = 3000 元，超买 20%）。
**"整手取整误差"是 A 股特有的换手放大器**，与佣金费率无关。建议直接查 `panel.can_buy` / 下单取整逻辑确认。

### A2. [新增] 单笔订单 ≤ **日均成交量的 1%** —— PDF p125（印刷 p101）

> **原话**："One common measure of liquidity is the average daily volume (it is your choice what
> lookback period you want to average over). As a rule of thumb, each order should not exceed
> 1 percent of the average daily volume."

**书里紧接着的告诫（PDF p125，原话）**：独立交易者"可能觉得达不到这个 1% 门槛"，但——
"other people are already trading very similar strategies and impacting your returns. Adding an
extra trader or two, unless the trader works for an institutional money manager, is not likely to
cause much more impact."

→ **推断**：5 万本金在 1% ADV 约束下几乎必然满足（单笔 2500 元 ≈ A 股日均成交额的极小比例）。
**这条约束对平台是"已满足"，不能当降成本手段用**——写在这里是为了**排除**它，避免误以为还有空间。

### A3. [新增] 权重 ∝ 市值的**四次方根**，且最大/最小权重比**不超过 10** —— PDF p126（印刷 p102）

> **原话**："most practitioners would not recommend a linear scale because the market capitalization
> of companies varies over several orders of magnitude... A linear scale would result in practically
> zero weights for most small- and micro-cap stocks... **we should not allow that ratio to be more
> than 10 or so**... **If the capital weight of a stock is proportional to the fourth root of its
> market cap, it would do the trick.**"

→ **推断**：这条**不能外推**到平台当前形态。平台是 **Top10 等权**打分选股，`buffer_multiple` 作用于排名带宽，
不是权重上限。若要引入，需要新增"权重上限/市值缩放"这一执行维度——**仓库当前无此参数**（标为需先实现）。

### A4. [新增] **参数平均化**：把资金分给多组参数，而不是押注单点最优 —— PDF p96（印刷 p72）+ PDF p86（印刷 p62）

这是任务里要找的「`rebalance_every` 和 `buffer_multiple` 之外的**第三种办法**」，而且书里讲得最重的一条。

> **PDF p96 原话**："When one has reduced the set of parameters and conditions that trigger a trade
> to the minimum, and after one has ascertained that small variations in these parameters and
> conditions do not drastically alter the out-of-sample performance, one should consider **dividing
> the trading capital across the different parameter values and sets of conditions. This averaging
> over parameters will further help ensure that the actual trading performance of the model will not
> deviate too much from the backtest result.**"

> **PDF p86 侧栏原话**："Often, it is **better to make a trading decision based on some kind of
> averages over different sets of parameters**."

**为什么它是"降成本"的第三种办法（推断）**：单点最优 `K=21` 在 `K=20/21/22` 上各自都可能给出
略有不同的持仓名单；平均化 = **并集持仓**，换手会**上升**而非下降。所以：
- 它的直接收益是**降低参数风险 / 降低回测-实盘偏离**（书里明说的）；
- 它**不是**降换手手段。
→ **诚实标注**：任务假设"第三种办法能降换手"，书里这一条**不能**这么用。它降的是 variance，不是 turnover。
真正的第三条降换手路径在 B4（利润上限 / profit cap），见下。

### A5. [已在旧文档] 成本**只在权重变化时**收取，按 `|w_t − w_{t−1}|` 累加 —— PDF p99（印刷 p75）

代码原样：
```matlab
dailypnlminustcost = dailypnl - smartsum(abs(weights-lag1(weights)), 2).*onewaytcost;
```
`aplus/backtest.py:46` 的 `turnover_ratio = (新进只数+退出只数)/(2×max(top_n, 本期只数, 上期只数))`
已经是同一口径。**已对齐，无需改动。**

### A6. [新增] 拆单会换来滑点，**散户不适用** —— PDF p126（印刷 p102）

> **原话**："Many institutional traders who desire to execute a large order will break it down into
> many smaller orders and execute them over time. This method will certainly reduce market impact;
> **however, it engenders another kind of transaction cost, namely, slippage.** Since reducing market
> impact in this way may increase slippage, **it is not really suitable for retail traders** whose
> order size is usually not big enough to require this remedy."

→ **推断**：书里明确排除了一条常被滥用的降成本路径。平台 2500 元/笔更远在门槛之下，
**拆单（TWAP/VWAP）在本平台无价值，应从候选里删掉而不是加进去**。

### A7. [已在旧文档] 成本敏感性 + 极端案例 —— PDF p96–p100（印刷 p72–76）
最戏剧性的一条（PDF p97）：Khandani-Lo 均值回归原论文 2006 年夏普 **4.47**，
换成 S&P500 池后 **0.25/0.96**，加 5bp 后 **−3.19/−2.16**。
→ **对平台的直接含义（推断）**：反转信号在**小市值池**上的表现会远好于大盘池。
平台的 `predict.py` 已经在剔 ST/次新/停牌，池子天然偏中小盘，这个优势**已经在收益里**。

---

## B. 反转 / 均值回归类信号

### B1. [新增] 横截面均值回归远比时间序列均值回归常见 —— PDF p158–159（印刷 p134–135）

> **原话**："Reversion of the price of a single stock from a temporary deviation from its mean price
> level back to its mean is called **time-series mean reversion, which doesn't happen often.**
> ... Mean reversion of the spread of a pair of stocks, or a portfolio of stocks, back to its mean
> level is called **cross-sectional mean reversion, and it happens much more often.**"

→ 平台的 `mom20:-0.5` 是**横截面**排序反转，方向上与书里"更常见"的那一类一致。**支持现有选择。**

### B2. [新增·关键警告] 反转策略回测有**两个**特有数据陷阱 —— PDF p159（印刷 p135）

> **原话①（脏报价）**："Many historical financial databases contain errors in price quotes. Any such
> error tends to **artificially inflate the performance of mean-reverting strategies**. It is easy
> to see why: a mean-reverting strategy will buy on a fictitious quote that is much lower than some
> moving average and sell on the next correct quote."

> **原话②（幸存者偏差，对反转是致命的）**："**Survivorship bias also affects the backtesting of
> mean-reverting strategies disproportionately**... Stocks that went through extreme price actions
> are likely to be either acquired (the prices went very high) or went bankrupt (the prices went to
> zeros). **A mean-reverting strategy will short the former and buy the latter, losing money in both
> cases.** However, these stocks may not appear at all in your historical database if it has
> survivorship bias."

→ **对平台的直接含义（推断）**：`aplus/predict.py` 明确剔 ST / 次新 / **退市冻结**，
`coverage.py` 有 `delist_date` 登记。也就是说 **ST 股被主动剔除了**。
但书里说的正是：那些最终崩掉/退市的股票，在历史上曾经**看起来像极好的反转标的**。
**剔除 ST 是为了实盘可执行性（正确），但同时也可能顺手抹掉了历史上真实的反转收益来源。**
这是一个**用实盘约束污染回测信号池**的风险，值得作为一条待验证项（不改代码，只记录）。

### B3. [已在旧文档] OU 半衰期定持有期，**半衰期 = ln(2)/θ** —— PDF p194–196（印刷 p170–172）

书里对**两种策略的对比极重要**（PDF p194 原话）：
- 动量策略：持有期"可以用回测发现"，但**充满数据窥探**，因为"the number of historical trades may be limited"；
- 均值回归策略：OU 半衰期"**does not depend on the limited number of actual trades**"——
  因为可以用**整条时间序列**估 θ，不只是触发交易的日子。

GLD/GDX 算例（PDF p195–196）：hedgeRatio=1.6395，θ=−0.0884，**half-life = 7.839 天**。
→ **对平台的直接含义（推断）**：冠军是 `mom20:-0.5` **反转**策略，按书里的逻辑，
它的 `K=21` **应该**能用 OU 半衰期交叉验证。如果 `K=21` 与反转价差的 OU 半衰期接近，
则 K 的可信度大幅上升；若偏离甚远，说明 K=21 更像是网格搜出来的噪声。**这是零成本的验证。**

### B4. [新增·A/B 交叉] **反转策略绝不该用止损**，该用**利润上限** —— PDF p198（印刷 p174）

这条是任务里"降低换手/成本的第三种办法"最贴近的答案——它是**退出规则的改写**，不需要新因子。

> **原话**："Consider a parallel situation when we are running a **reversal model**. If an existing
> position has incurred a loss, running the reversal model again will simply generate a new signal
> **with the same sign. Thus, a reversal model for entry signals will never recommend a stop loss.**
> (On the contrary, it can recommend a **target price or profit cap** when the reversal has gone so far
> as to hit the opposite entry threshold.) And, indeed, **it is much more reasonable to exit a position
> recommended by a mean-reversal model based on holding period or profit cap than stop loss, as a stop
> loss in this case often means you are exiting at the worst possible time.**"

书里给的四种退出（PDF p193 原话）：固定持有期 / 目标价或利润上限 / **跟随最新入场信号** / 止损。
并明确（PDF p197 原话）："**you can use the target price together with the half-life as exit signals
(exit when either criterion is met).**"

→ **为什么这能省成本（推断）**：反转策略的止损会在**最差时点**卖出，且制造额外换手。
换成"持有到期 或 触及对侧入场阈值（利润上限）才走"，**换手会系统性下降**，且信号含义更干净。
→ **仓库现状**：`grep -rn "halflife|profit_cap|stop_loss|target_price" aplus/` → **零命中**。
**退出规则层完全不存在**，这是需要先实现的功能，不是可跑候选。

### B5. [新增] 竞争对两类策略的侵蚀方式**不同** —— PDF p161（印刷 p137）

> **原话**：均值回归 → "the gradual elimination of any arbitrage opportunity, and thus gradually
> diminishing returns down to zero"；一旦机会被压到近乎为零，"an increasing percentage of trading
> signals are actually due to **fundamental changes in stocks' valuation and thus is not going to
> mean revert**"。
> 动量 → "the diminishing of the time horizon over which the trend will continue"。

→ **对平台的直接含义（推断）**：冠军是反转策略。按书里的说法，反转的失效模式是
**"性质变了"而非"收益小了"**——即信号开始被基本面变化淹没。
`aplus/forward.py` 的前向注册机制正好能观测这个：若反转在最近样本外失效，
更可能是信号性质改变，而不是"套利被磨平"。这为当前 +92.4% 的高净年化**预设了失效判据**。

### B6. [新增] Khandani-Lo 反转模型的**原公式** —— PDF p97–p98（印刷 p73–74）

```matlab
% 权重 ∝ 到市场等权指数的负距离（横截面反转）
dailyret   = (cl - lag1(cl))./lag1(cl);
marketDailyret = smartmean(dailyret, 2);
weights = -(dailyret - repmat(marketDailyret,[1 size(dailyret,2)])) ./ ...
          repmat(smartsum(isfinite(cl),2),[1 size(dailyret,2)]);
dailypnl = smartsum(lag1(weights).*dailyret, 2);
```
→ 推断：这是**等权池内对市场收益的偏离取负**，`mom20` 是它的多日推广版本。**平台无需改**，
但值得作为"`mom20` 该不该减去市场收益"的参照——现有 `composite_score` 是否已做市场中性化，值得对照。

---

## C. 异常换手 / 流动性 / 微观结构因子

### **本节为空，且这是结论本身。**

如"零结果"所述：全书 **0 次** "turnover" 作因子、**0 次** "Amihud"。
（"illiquid" 仅 3 次，均为执行层描述，非因子定义。）
书里成交量/流动性的全部内容是**执行约束**，不是 alpha 因子：

| 位置 | 内容 | 性质 |
|---|---|---|
| PDF p125 | 不买 <$5 低价股 | 佣金 + 价差约束 |
| PDF p125 | 单笔 ≤ 1% 日均成交量 | 冲击成本约束 |
| PDF p126 | 权重 ∝ 市值四次方根，最大/最小比 ≤10 | 冲击成本约束 |
| PDF p132 | "Are you trading **illiquid** stocks that caused a lot of market impact?" | 自查问题 |

→ **诚实结论（推断）**：`turnratio` 之所以有效，**这本书既没提供支持也没提供反驳**。
若要为异常换手找依据，需要换一本（石川《因子投资方法与实践》，同目录下已存在）。
把"书里没有"当成"书里否定"是错的——这里只能得出**证据缺失**。

---

## D. 书里明确指出的常见错误 / 陷阱 —— 附 aplus 现状判定

| # | 陷阱 | 页码（PDF） | 书里原话要点 | aplus 现状（只读判定） |
|---|---|---|---|---|
| D1 | **定性决策也是参数** | **p84** | "not all data-snooping bias is due to the optimization of parameters. Numerous choices one makes... can be affected by repeated backtesting on the same data set—decisions such as **whether to enter at the open or close**, whether to hold the positions overnight, whether to trade large-cap or mid-cap stocks." | **正在踩**：`evolve_race_fast.py` 候选池 32 行 × 因子权重连续值（如 `mom20: 1.4 / 0.6 / 2.0`），每次赛马都是一次对同数据的重测。书里的 5 参数上限见 D2。 |
| D2 | **参数数目上限：5 个** | **p84** | "As a rule of thumb, I would not employ more than **five parameters**, including quantities such as entry and exit thresholds, holding period, or the lookback period in computing moving averages." | **⚠ 正在踩**：`{mom20:-0.5, turnratio:-0.5}` = 2 个权重 + `K=21` + `B=3.0` = **4 个自由参数**，再加 `mom20` 的窗口 20、`turnratio` 的 num/den 窗口 → **已超 5**。且这 6 个值**每一个都可能被赛马调过**。 |
| D3 | **前视偏差自检：截断重跑** | **p83** | 全量数据生成持仓文件 A → 截掉最近 N 天（10~100）重跑得 B → A、B 的持仓必须**逐位相同**。 | **✅ 未踩**：`tests/test_daily_update_isolation.py`、`test_live_rebalance.py` 等做隔离测试；`factor.py:80` 对 ±10% 收益截断。 |
| D4 | **样本外 + Embargo 隔离带** | **p85–86** | 训练段调参、测试段验收；"The minimum size of the out-of-sample test set is determined by the same mathematical result"（681 数据点/2.71 年等）。 | **✅ 做对了，且比书更严**：`evolve_race.py:12` 前 60% 训练 / 后 40% 测试 + **隔离带(embargo)**，`forward.py` 有前向注册。书里没要求 embargo，平台主动加了。 |
| D5 | **Deflated Sharpe**：调得越多真实夏普折损越狠 | **p84** | "Bailey et al. developed a metric called 'Deflated Sharpe Ratio' to take into account **how many times you have tweaked a backtest**... The more tweaks you have done, the more deflated your true Sharpe ratio will be." | **⚠ 部分踩**：平台有 t 统计门禁（t=12.59），但**没看到记录"这个因子被试过几次"的计数器**。t=12.59 极高，但 DSR 会因尝试次数把它折损——**折损多少取决于跑了多少组候选，平台未记录**。 |
| D6 | **成本只在权重变化时收** | **p99** | 见 A5 | **✅ 已对齐**：`backtest.py:46` 同口径。 |
| D7 | **幸存者偏差对"买便宜的"策略致命** | **p50, p159** | 见 B2 | **✅ 大体已处理**：`coverage.py` 有 `delist_date`、`predict.py` 剔退市冻结/ST。但**按 B2 的推理，ST 剔除可能反向抹掉反转信号的历史来源**——需验证，非明确违规。 |
| D8 | **策略变体要有经济学依据，不能试错** | **p101–102** | "it is preferable that the refinement has some basis in **fundamental economics or a well-studied market phenomenon**, rather than some arbitrary rule based on trial and error. Otherwise, **data-snooping bias looms.**" | **✅ 设计正确**：`evolve_race_fast.py:33-50` 明确"形态多样"而非在最优点附近微调。 |
| D9 | **早年数据不可信：只信最近几年** | **p51** | "**bid–ask spreads were much wider then**: So if you assumed the transaction cost today was applicable throughout the backtest, the earlier period would have unrealistically high returns." + 幸存者偏差"mainly inflates the performance of an **earlier period**" + regime shift 使老数据失效。 | **⚠ 需查**：平台成本用**单一常数** `STOCK_COST_DEFAULT = 0.003`（`backtest.py:36`）覆盖全历史。书里明确说早年价差更宽 → **回测早年段的净收益被系统性高估**。建议按年份分档报成本（只记录，不改）。 |
| D10 | **反转策略的脏报价陷阱** | **p159** | 见 B2 | **✅ 部分处理**：`backtest.py:161` `MAX_PLAUSIBLE_RETURN`（±40%）作废样本——正是"假极低价"的防御。 |
| D11 | **因子模型必须假设因子收益有动量才可交易** | **p186–187** | "factor models are contemporaneous—that is, given historical returns and factor exposures, we can compute the factor returns of those same historical periods—what good are they for trading? It turns out that often **factor returns are more stable than individual stock returns—they exhibit stronger serial autocorrelations**." | 参照系：平台的 `composite_score` 是横截面打分而非时序因子回归，**不适用本条**。列此仅为对照。 |
| D12 | **因子暴露与因子收益都是同期回归，不能预测下一期** | **p185–186** | "Note that this regression is **contemporaneous, not predictive**—you can't use these factor returns to predict a stock's next day's return, unless you can predict the next day's SMB and HML." | **✅ 未踩**：平台用的是横截面动量/反转本身，不是因子回归外推。 |

---

## E. 可直接进赛马的候选清单

### E1. **零新增代码**（现有 5 因子 + 现有执行参数，`rebalance_every`/`buffer_multiple`）

冠军基线（题目给定）：
```json
{"weights": {"mom20": -0.5, "turnratio": -0.5}, "rebalance_every": 21, "buffer_multiple": 3.0}
```

来自书的候选：
```json
{"weights": {"mom20": -0.5, "turnratio": -0.5}, "rebalance_every": 21, "buffer_multiple": 1.5}
{"weights": {"mom20": -0.5, "turnratio": -0.5}, "rebalance_every": 21, "buffer_multiple": 5.0}
{"weights": {"mom20": -0.5, "turnratio": -0.5}, "rebalance_every": 15, "buffer_multiple": 3.0}
{"weights": {"mom20": -0.5, "turnratio": -0.5}, "rebalance_every": 42, "buffer_multiple": 3.0}
{"weights": {"mom20": -0.5, "turnratio": -0.5}, "rebalance_every": 63, "buffer_multiple": 3.0}
```
> **诚实标注**：这 5 条**不是书里的配方**，是 K/B 网格。书里（PDF p84）恰恰警告网格搜索 =
> 数据窥探。列在这里是因为**赛马本来就在跑这个网格**，此处只是把它显式化。
> **更符合书意的做法（不新增代码）**：跑 K∈{15,21,42} × B∈{1.5,3.0,5.0} 后，
> **按 PDF p96 的"参数平均化"选组而非选单点**——即"哪一组在整个邻域都不垮"。

### E2. **需要先实现因子**（仓库只认 `mom20/rev5/vol20/vsur/turn20`）

| 候选形状 | 书的依据 | 状态 |
|---|---|---|
| 任何**排除低价股**的池子（`close > 5`） | **PDF p125** | ⚠ `predict.py` 已有 `close` 但无价格下限过滤；A 股 2500 元/笔下整手取整误差才是真问题 |
| 权重 ∝ 市值四次方根、最大/最小权重比 ≤10 | **PDF p126** | ❌ 需新增"权重缩放"执行维度 |
| OU 半衰期定 `rebalance_every`（不是网格搜 K） | **PDF p194–196** | ❌ 需实现 `aplus/mean_revert.py` 式 OU 拟合 |
| 反转策略的利润上限退出（无止损） | **PDF p197–198** | ❌ 需实现退出规则层；`grep` 确认仓库零命中 |
| PCA / 因子收益动量 | PDF p187–191 | ❌ 书里自己结果只有 2–4% 年化，**不值得做** |

### E3. **明确不要做的**（书里点名排除）
- **拆单 / TWAP / VWAP** —— PDF p126 明确对散户不适用（换来滑page）。
- **单笔 ≤1% ADV 约束** —— PDF p125，5 万本金早已满足，加了是零收益的复杂度。

---

## F. 哪些结论**不能**外推到 A 股（逐条）

1. **"$5 价格下限"不能照搬**（PDF p125）—— 美股口径基于**佣金按股数**+机构惯例。
   A 股佣金按**金额**收最低 5 元，与股价无关。但 **A 股有 100 股整手约束**，
   书里完全没有这个约束 → **低价股在 A 股有书里未提及的取整误差惩罚**（推断，见 A1）。
2. **"权重 ∝ 市值四次方根"不宜照搬**（PDF p126）—— 基于美股 large/small-cap 的流动性梯度。
   A 股壳价值、小盘流动性断层更极端，且平台 Top10 等权形态与之冲突。
3. **季节性/日历效应结论不可外推**（PDF p199）—— 书里自己说**股票**季节性近年已失效
   （"much of the seasonality in equity markets has weakened"），商品期货仍有效。A 股有独立的春节/两会效应，
   不能用美股结论覆盖。
4. **PEAD / 财报漂移**（PDF p160）—— 美股财报制度。A 股财报披露节奏、预告制度不同，需独立验证。
5. **"$5 / 5bp 成本量级"不可照搬**（PDF p49）—— 书里 S&P500 单边约 5bp。
   A 股 5 元最低佣金在 2500 元/笔上是**单边 20bp**，是美股的 4 倍。
   → **这是平台最大的成本劣势，也是最大的优化空间**：同样 0.2% 的成本，
   平台必须比美股策略**少换 4 倍的手**才能有相同的净收益。
6. **不能外推的方向**：书里**没有任何** A 股流动性/换手因子证据（C 节空），
   不能用"Chan 说反转有效"来为 `turnratio` 背书。

---

## G. 本轮最有价值的三条（按"可立即行动"排序）

1. **PDF p198 / p197：反转策略不该有止损，该用利润上限 + OU 半衰期双条件退出。**
   这是唯一一条**在不加新因子的情况下就能降低换手**的书里建议，且逻辑与
   "反转"这一策略性质严格匹配。仓库当前**完全没有退出规则层**。
2. **PDF p125：低价股成本惩罚。** A 股语境下要重新表述为**整手取整误差**，
   这才是 5 元最低佣金下真正的换手放大器。
3. **PDF p84 + p96：参数数目上限 5 个 + 参数平均化。** 平台当前冠军已用掉 6 个自由参数，
   且每个都可能被赛马调过。**下一步的正确动作可能不是加因子，而是先做 K/B 邻域平均化**，
   用"邻域整体表现"而不是"单点最优"来选参数。

---

*页码全部为 PDF 页码。印刷页码 = PDF 页码 − 24。核对基准：PDF p125 页眉 "Execution Systems 101"。*
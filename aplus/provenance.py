"""报告溯源与新鲜度——**「这份数字是哪个 commit 算出来的」必须可回答**。

## 这模块是被一次真实的错误逼出来的

2026-10-14 我读了 `data/reports/backtest_summary.json`（11:45 生成），
据此告诉用户「信号符号反了」。

而 14:02 的 commit `3c846ff` 修掉了市场基准污染（把基准从 +0.066%/日
拖到 −0.402%/日，虚增约 114 个百分点/年）。**那份报告作废了。**

数字本身看着完全正常——`−0.40%/日` 是个体面的数，
`excess_tilt = −0.005` 是个体面的数。**没有任何东西看起来是坏的。**

我之所以没发现，纯粹是因为我**没去看生成时间 vs 最后一次相关改动**。
那一步没有任何工具提示我必须做，于是我没做。

> **没有溯源的报告，等于没有来源的断言。**
> 而看起来正常的数字，恰恰是最难被质疑的那一种。

## 提供的三件事

1. `stamp(...)` —— 生成报告时把 `{code_sha, generator, at}` 写进 sidecar。
2. `freshness(...)` —— 判定一份报告相对**指定生成模块**是否过期。
3. `scan(...)` —— 扫整个 reports 目录，列出过期与**无溯源**的。

## 为什么判据是「生成模块」而不是「整个 commit 范围」

HEAD 每动一次就全盘报红，红色很快会变成背景噪声——和「永远红的测试」
是同一种失败：它教会所有人忽略告警。

只盯**真正产出这个数字的模块**，红才有信息量。
`backtest_summary.json` 该对 `backtest.py` 敏感，不该对 `vintage.py` 敏感。

## 无溯源 ≠ 过期，但也不等于可信

现存的老报告都没有 sidecar。它们被报成 `unknown` 而不是 `stale`：
**「不知道」和「已知坏了」是两回事**，混为一谈会让人连真的坏报告也忽略。
`unknown` 的处理方式是**不许引用**，而不是「大概没事」。
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

SIDECAR_SUFFIX = ".provenance.json"


#: 仓库根 —— 从**本文件位置**推导，**永远不看 cwd**。
#:
#: ★ 这些函数的 `root` 默认值曾经是 `"."`，也就是 **cwd**。
#:   而 sidecar 里的 `generator` 记的是**仓库相对**路径
#:   （如 `scripts/placebo_control.py`）⇒ 在仓外调用 `freshness()` 时，
#:   `Path(root) / generator` 指向仓外那个**不存在**的路径
#:   ⇒ **21/21 份产物**塌成 `unknown`，而理由句说的是
#:   「生成器路径在仓里不存在 ⇒ **判据本身没跑**」。
#:
#: ★ 那比「产物坏了」更糟：它**谎报我没验**，而我明明验了、而且逐位一致。
#:   同样的默认值在 `stamp()` 上更险——`code_sha(root)` 会返回 `None`
#:   ⇒ 章里写下 `code_sha: null`。**尚未实际发生**（现有 21 份 sidecar 全部显式
#:   传了 `root=ROOT`），但那是接线运气，不是设计。
#:
#: ⚠️ 显式传 `root=` 的调用点**一律不受影响** ⇒ 这条改动只会把
#:   「误报的红」变成「正确的绿」，不可能让一条真红变绿（第 129 层）。
REPO_ROOT = Path(__file__).resolve().parents[1]


def _root(root: Path | str | None) -> Path:
    """`root=None` ⇒ 仓库根。**只有显式传入才用别的目录。**"""
    return REPO_ROOT if root is None else Path(root)


def report_jsons(reports_dir, *patterns: str) -> list[Path]:
    """列出报告 JSON，**排除 provenance 边车**。不给 patterns 时用 `*.json`。

    ## 为什么需要这个函数（而不是每个调用点各写一个 if）

    边车的文件名是 `主报告名 + ".provenance.json"` —— 它**也是 `.json`**，
    而且是主文件名的延长，字典序必然更大。于是：

    - `sorted(dir.glob("factor_ic*.json"))[-1]` 会选中边车而不是真报告
      （`aplus/factor_ic.py:_find_ic_report` 真实发生过，概览页
      「符号已按实测 IC 校正」因此失去数据支撑）
    - `sorted(dir.glob("race_*.json"), key=mtime)` 同理
      （`aplus/webapp.py`、`aplus/champion_health.py`）
    - `dir.glob("*.json")` 会把边车当成「一份没有边车的报告」
      —— **告警自己制造告警**（`aplus/provenance.py` 的 freshness 扫描）

    ⚠️ 修一处、漏三处，正是 `docs/MISTAKE_LOG_2026-10-14.md` 第 73 层
    记的那个形状（主脚本修好了，兄弟脚本没跟着修）。
    ⇒ 所以**约定与实现放在同一个模块**：想列报告就调这里，
    想新写一个 glob 就会在 import 时看到它。
    """
    pats = patterns or ("*.json",)
    seen: set[Path] = set()
    for pat in pats:
        for p in Path(reports_dir).glob(pat):
            if not p.name.endswith(SIDECAR_SUFFIX):
                seen.add(p)
    return sorted(seen)

#: 指路时用的模块全名。**不能写 `__name__`**——`python -m aplus.provenance`
#: 时它是 `"__main__"`，于是提示变成「在 __main__ 的 DEFAULT_GENERATORS 补一行」，
#: 指了个根本不存在的地方。**指错路比不指路更糟。**
SELF_PATH = "aplus/provenance.py"

#: 报告 → 产出它的模块。**这张表是本模块的全部知识**，
#: 不知道来源的报告会被报成 unknown（不许引用），而不是默默放行。
#:
#: 键是 reports 目录下的文件名，值是 git 路径。
#: 表外的报告一律 `unknown` —— **漏登记比登记错更安全**，
#: 因为登记错只是漏报，漏登记会让人以为没人管。
DEFAULT_GENERATORS: dict[str, str] = {
    # 2026-10-15 登记：**PIT 链的唯一真相源**（27 GB/年）。
    # 键是**分区文件名**而不是日期 —— 每一天的快照都叫 `financial.parquet`，
    # 靠 `PATTERN_GENERATORS` 那样的模式表登记反而更啰嗦，而基名足够唯一
    # （与既有的 `ic_matrices.parquet` / `financial_annual_cache.parquet` 同款）。
    # 🚨 不登记的后果：`resolve_generator()` 返 `None` ⇒ `freshness()` 判
    #   `unknown`（= 不许引用），而 `unknown` 与「数据坏了」长得一模一样。
    "financial.parquet": "scripts/collect_financial_vintage.py",
    # 2026-10-15 登记：基准年化项（`benchmark_annual`）对 20% 判决的影响。
    # 判据逐字在 docs/PREREG_BENCHMARK_ANNUAL_TERM_2026-10-15.md（看数据前写死）。
    # 🚨 这条登记是**被守卫逼出来的**，不是想起来的：
    #   `test_every_stamped_report_resolves_back_to_a_registered_generator`
    #   在全量门禁里点名 `benchmark_annual_term_2026-10-15.json.provenance.json` ——
    #   我盖了章却没登记，而「盖了假章比不盖更坏」（形状 16）。
    "benchmark_annual_term_2026-10-15.json":
        "scripts/benchmark_annual_term.py",
    # 2026-10-16 登记：组合绝对净值年化（**年化 20% 目标第一次被对着交付物测量**）。
    # 判据逐字在 docs/PREREG_PORTFOLIO_NAV_2026-10-16.md（看任何组合数据前写死），
    # 其中 §7/§8 是补记（前者修掉一条把两个不同量绑死的错判据，
    # 后者补上 MDE 里漏掉的 252 年化放大因子）。
    # 🚨 同样是被守卫逼出来的：`test_every_stamped_report_resolves_back_to_a_registered_generator`
    #   在全量门禁里点名了本文件 —— 我盖了章却没登记，
    #   而「盖一个**解析不出生成器**的章比不盖更坏」（形状 16）：
    #   读者无从判断它出自哪份代码。
    "portfolio_nav.json": "scripts/measure_portfolio_nav.py",
    # 2026-10-16 登记：组合**资金可执行性**（¥50,000 开不写得出来）。
    # 🚨 同样是**被这条守卫逼出来的**（第 3 次）：全量门禁点名
    #   `portfolio_exec.json.provenance.json` 解析不出生成器。
    #   修法照旧 —— 在 `provenance.py` 里补一行，而不是删掉那份 sidecar
    #   或给守卫加白名单。**盖一个解析不出生成器的章比不盖更坏**（形状 16）。
    "portfolio_exec.json": "scripts/measure_portfolio_exec.py",
    # 2026-10-16 登记：Y5 净值口径随机臂（预注册 §10 + §19.5）。
    # 🚨 **两个键都必须登记，不能只登记正式那份** —— 冒烟产物 `verdict`/`branch`
    #   都是 `null`，它**照样会被引用**（引用它的人看到的是「冒烟没出判决」，
    #   而这恰恰是最该被看见的一条）；只登记正式那份 ⇒ 冒烟 sidecar 判
    #   `unknown` = 不可引用 ⇒ **「不可引用」与「没出判决」被合并成同一个
    #   症状**（第 25 层的反面：把不可引用的东西登记成永久债）。
    "random_arm_nav.json": "scripts/measure_random_arm_nav.py",
    "random_arm_nav_smoke.json": "scripts/measure_random_arm_nav.py",
    # 2026-10-17 登记：转债低价轮动的**样本外超额**判决（train/test/full 三段）。
    # 🚨 这是 `bond` 系列在 `DEFAULT_GENERATORS` 里的**第一条**登记 —— 此前
    #   `grep -c bond aplus/provenance.py` 实测 **0**，而 `data/reports/` 下
    #   44 份 sidecar 里 `bond*.provenance.json` 零份
    #   ⇒ 按本模块自己的规矩 `freshness()` 判 `unknown` = **不许引用**，
    #   而「可转债线已样本外失败」这条**已写进 `docs/PLATFORM.md` §7.5 的判决表**
    #   —— 即一份不可引用的判决被引用着。
    # ⚠️ 与股票线**不是同一个量**（第 7 层）：`bond_paper.json` 是纸面账户
    #   （5 天、`verdict=null`、`cost_paid=0.0`、`calendar_ref=110076.SH`），
    #   本文件是历史三段（`calendar_ref=110075.SH`）⇒ 两个数不可混引。
    "bond_excess_analysis.json": "scripts/bond_excess_analysis.py",
    # 2026-10-16 登记：§18 ① **组合账户净值**判决档（c=+10.5563%、M=±22.6738%、
    # 判「测不出」）。🚨 这是**对着 D2 本身**的一次测量，产物却**既没章也没登记**
    # ⇒ 交付物最该被引用的那份产物，恰好是引用链上唯一断掉的一环。
    # ⚠️ 文件名带 `2026-10-16` 且**写死在脚本里**（`measure_basket_nav_01.py`
    #   无 `--out`）⇒ 换名要两处一起改；这里登记的是**精确名**。
    "basket_nav_01_2026-10-16.json": "scripts/measure_basket_nav_01.py",
    # 2026-10-20 登记：**按平台真正会执行的账户**测 D2
    # （半仓 5 只 + 4 因子 DEFAULT_WEIGHTS + 整手 + 逐笔成本）。
    # 预注册 `docs/PREREG_DEPLOYED_D2_2026-10-20.md`（看任何数之前写死）。
    # ⚠️ 它**不改** `basket_nav_01_*.json` 的判决档（那是满仓 10 只 + IC 定权口径），
    #   而是把**另一档账户**并排摆出来 —— 两个口径不是同一个量（不许混引）。
    "deployed_d2_verdict.json": "scripts/measure_deployed_d2.py",
    # 2026-10-15 登记（第 127 层）：三臂判决（最优定权 / 朴素翻号 / 原样等权）。
    # 🚨 登记**卡在 K 链跑完之后才做** —— `aplus/provenance.py` 正在被链的
    #   `backtest_walk_forward_weights.py` 调用（`provenance.stamp()`），
    #   在它运行时改它就是第 125 层本人。
    # 为什么非登记不可：这份产物落盘前，三臂数字**只活在终端里** ⇒
    #   「原样等权 6/6 全败」无人可查 ⇒ 「等权够不够」被当成未回答的问题
    #   **带着一个错的基线重新预注册了一遍**。
    # 2026-10-15 登记：市值暴露判决（第 147 层批次）。
    # 预注册 docs/TODO_cap_exposure_2026-10-15.md —— 与 factor_attribution **不同**：
    #   那份判的是「因子能不能解释超额」，这份判的是「组合有没有市值倾斜」。
    # ⚠️ 它的判决**不能**替换头条毛超额，也不许进 FACTOR_COLS。
    "cap_exposure.json": "scripts/cap_exposure.py",
    # 2026-10-16 登记：**实盘半仓敞口下的账户年化**。
    # 🚨 它补的是判决档**从来没覆盖的一列**：`basket_nav_01_*.json` 的判决档
    #   `c=+10.5563%` 量的是**满仓**账户（10 只等权、权益权重 1.0），
    #   而 `aplus/alloc.py::build_allocation(max_positions=5, per_stock_cap=0.10)`
    #   的 `equity_weight = 0.5`，且 `run_daily.py:647` **只传 `capital`** ⇒ 走默认值。
    #   ⇒ 判决档那个数**不是用户账户会拿到的数**。
    # ⚠️ 登记它不是给它判决权：产物自带 `role = 对照臂/推算`，
    #   依赖「5 只子篮子收益 = 10 只篮子收益」这个 daily.parquet **测不出来**的假设。
    # 🚨 本条**不是**被守卫逼出来的（`basket_nav_01` 至今仍未登记），
    #   是本轮自查发现的：盖了章却查不到生成器 = `unknown` = 不可引用（形状 16）。
    "deployed_account_nav.json": "scripts/deployed_account_nav.py",
    # 2026-10-16 登记：**D2 达标所需的毛超额** `g*(N) = x* + friction(N)`。
    # 预注册 docs/PREREG_D2_SIGNAL_REQUIREMENT_2026-10-16.md §3（四分支，
    # 且 **C_UNMEASURABLE 必须先于 A/B** —— 顺序本身就是判据）。
    # 🚨 它**只读三份已有产物**、不重跑回测 ⇒ 产物不比输入更新，
    #   上游 stale 则本产物继承同一 staleness（预注册 §4.5）。
    "d2_signal_requirement.json": "scripts/d2_signal_requirement.py",
    # 2026-10-17 登记：D2 缺口陈述的**口径对照表**（`the_only_citable_d2_gap`）。
    # 🚨 它存在的唯一理由是**防止第 7 层**：同一个「还差多少」在
    #   `equal_weight` 与 `cap_weight` 两个基准下读数差约 7.94 倍，
    #   而**这两个都是真数、都不是同一个量** ⇒ 谁把它们相除谁就是在
    #   报告一个量纲错误。
    # ⚠️ **它不是 D2 判决**：判决在 `basket_nav_01_*.json::verdict_rule`
    #   且早已出了；本产物只把「各口径各自的数」并排放好，**不出判决**。
    # 🚨 同样只读已有产物、不重跑回测 ⇒ 产物不比输入更新，
    #   上游 stale 则本产物继承同一 staleness（与 `d2_signal_requirement` 同条纪律）。
    "d2_gap_calibers_2026-10-17.json": "scripts/d2_gap_calibers.py",
    "verdict_composite.json": "scripts/verdict_composite.py",
    "backtest_summary.json": "aplus/backtest.py",
    "lowfreq_plausibility.json": "scripts/lowfreq_plausibility.py",
    "market_benchmark_audit.json": "aplus/backtest.py",
    # 2026-10-15 登记：我曾断言「这份报告全仓没有生成器」，那是**错的** ——
    # `scripts/k_sweep_extract.py` 一直在写它（提交 aef0a8a）。
    # 错因：输出文件名**不绑在脚本上**（来自调用者的 `--json-out`），
    # 所以 `grep k_sweep` 匹配不到源文件，grep 的空被读成了「不存在」。
    # ⇒ 「用字面量 grep 找生成器」是探针陷阱，不是捷径。
    # ⚠️⚠️ 2026-10-15 实测：这个键**曾被登记两次**（这里 + 更靠后的 ⑤ 号那段）。
    #   `DEFAULT_GENERATORS` 是 dict 字面量 ⇒ 后者**静默覆盖**前者，
    #   不报错、不告警、登记表读起来还完全正常。重复的那一条已删除。
    #   ⇒ 守卫 `tests/test_provenance.py::test_default_generators_has_no_duplicate_keys`
    #   用 AST 数键，出现两次就红。**重复登记 = 有一次登记从来没生效过。**
    "k_sweep_summary.json": "scripts/k_sweep_extract.py",
    # 赛马报告的判决逻辑在 evolve_race.oos_decision，不在驱动脚本
    "ic_guided_race_7y.json": "aplus/evolve_race.py",
    "ic_guided_race_v1.json": "aplus/evolve_race.py",
    # 2026-10-14 修正基准后的重跑。与首跑**同名不同源**：它换了数据根
    # （aplus_snap_1012 → aplus_snap_final）、换了基准、且今晨翻过
    # DEFAULT_WEIGHTS 的符号——**代码与基准双变**，所以它是新报告，
    # 不能与 `ic_guided_race_7y.json` 混为一谈，也不能覆盖它。
    #
    # ⚠️ 这份报告与首跑**不构成对照实验**，重跑同时改了两个东西：
    #   ① 基准污染修复（首跑的 up_excess_vs_market 虚增约 115pp/年）
    #   ② 冠军基准的符号翻转（首跑 champion 是 {mom20:1.0, vol20:0.5, vsur:0.5}，
    #      今晨改为 {mom20:-1.0, vol20:-0.5, vsur:-0.5}）
    # 窗口（2019-01-02~2026-10-12）与切分（train→2023-08-17 /
    # test 2023-08-25~2026-10-09）刻意保持一致，所以**只有这两项在变**——
    # 但正因如此，两者的效应在报告里**分不开**。引用它做「基准修复带来多少
    # 改善」的结论是不成立的。
    "ic_guided_race_7y_corrected.json": "aplus/evolve_race.py",
    "reversal_freq_2019_2023.json": "aplus/backtest.py",
    # 2026-10-14 作废：年成本放大 ~10 倍（篮子级逐日值用 .sum()）
    # + 净收益重复扣一次成本 + t 未按调仓周期分块。
    # **结论方向未变**（24 行年超额全负，那一列不受影响），
    # 变的是量级与「最优行」的identity——`best` 按含污染的 net 选出。
    # 重跑见 docs/TODO_rerun_reversal_report.md。
    "VOID_reversal_freq_2019_2023__cost_10x_and_double_deduct.json":
        "aplus/backtest.py",
    # 🚨 2026-10-15 接线修正（D2）：这里原来登记成 `aplus/backtest.py`，
    # 而唯一写它的是 `scripts/regime_control_sweep.py`
    # （`:261` 默认 `--out`、`:278-281` 唯一的 `write_text` 写盘点）。
    # ⚠️ `aplus/backtest.py` 只是它 import 的库，不是生成器 ——
    # 登记错生成器会让 `commits_touching()` 追错文件，
    # 于是「谁动过这个产物」答的是另一个脚本的提交历史。
    # ⚠️ 第 123 层：**生成器名只从写盘点与 `DEFAULT_GENERATORS` 读，
    # 绝不许从输出文件名反推**（那份教训就是反推出来的名字不存在）。
    "regime_control_sweep.json": "scripts/regime_control_sweep.py",
    # 判决窗口两份：同一个脚本、同一段代码，产物写新文件名（预注册第三节约束 1）。
    # 🚨 2026-10-15 补记：登记在位，但**两个脚本都不调 `provenance.stamp()`**
    # ⇒ `freshness()` 判 `unknown`（逐字：「没有溯源 sidecar——生成它的 commit
    # 无从得知」）⇒ **判决链上的两份产物都不可引用**。
    # ⇒ 已给两个脚本补上 `stamp(..., expect_bytes=)`（第 125 层模式）并重跑。
    "regime_control_judge_window_2026-10-15.json":
        "scripts/regime_control_sweep.py",
    "regime_control_judge_window_by_year_2026-10-15.json":
        "scripts/regime_control_by_year.py",
    # PREREG 附则一第三节第 3 条要求的 `MIN_BASKET` 对照臂（写**另一个文件**，
    # 不覆盖第一份）。⚠️ 它**不是预注册产物**，是事后补跑的对照臂。
    "regime_control_min_basket_control_2026-10-15.json":
        "scripts/regime_control_min_basket_control.py",
    # 🚨 2026-10-15：`stats.json` 此前**未登记** ⇒ `freshness()` 判 `unknown`
    # ⇒ 按本模块自己的规矩「不可引用」，而它恰是**已交付产品的前瞻账**
    # （`docs/GOAL_20PCT_VERDICT_2026-10-15.md` 第五节整节引它的字段）。
    # ⚠️ 生成器名**从写盘点读**：`scripts/run_daily.py:800` 的 `path.write_text`
    # （第 123 层：绝不许从输出文件名反推）。
    # ⚠️⚠️ 同样**不调 stamp()**（该文件 `grep stamp` 零命中）
    # ⇒ 与上面两条同理，判 fresh 只有 **commit 走查**强度，没有字节证据。
    # ★ 2026-10-20 补：本条是 D1 交付面四份产物之一，
    #   与 `run_status.json` / `holdings.json` / `forward_results.json`
    #   一并在**本表末尾**登记（那里有一整块注释说明「登记的是写盘的那个模块」）。
    #   ⚠️ 本条的生成器是 `scripts/run_daily.py`（**写盘点就在这个脚本里**，
    #   `step_stats()` 的 `path.write_text`）—— 与上面三份**不同**：
    #   那三份由 `aplus/` 下的模块写盘、脚本只是调用方。
    "stats.json": "scripts/run_daily.py",
    "factor_ic_7y.json": "aplus/factor_ic.py",
    # 2026-10-14 重跑：`yearly` 补齐三档（原实现只聚合 `all`，
    # 是**口径不一致**——全段表出三档、逐年表只出一档）。
    # 判决见 docs/TODO_measure_sigma_r.md：**3/8 年不过月频门槛**。
    # 旧报告仍在册：它的**全截面逐因子符号**仍有效（v2 已复核同号），
    # 但**它的 yearly 缺低尾档，永远不许用来做月频判决**——
    # scripts/verdict_monthly_hurdle.py 对它会拒绝退出，测试钉着这件事。
    "factor_ic_7y_v2.json": "scripts/factor_ic_diagnostic.py",
    # σ_r 的**读面板口径**在脚本，纯函数在 aplus/xsec_sigma.py——
    # 公式不该跟着口径变（换池子/缩尾比例时只改脚本）。
    "xsec_sigma_r.json": "scripts/measure_xsec_sigma.py",
    # 2026-10-15 登记：两个诊断脚本的产物此前**从未登记**，
    # 新鲜度只能靠 commit 走查判断。
    # ① 命中率分层诊断（顺/逆大盘、扣 beta 后超额、打分排序力）
    "hit_rate_diagnosis.json": "scripts/diagnose_hit_rate.py",
    # ② 权重目标函数错配诊断：判「权重跟的是 IC，策略吃的是尾部价差」
    #    三态判决；守卫在 tests/test_weight_vs_spread.py + tests/mutation_specs/
    "weight_vs_spread.json": "scripts/weight_vs_spread.py",
    # 尾部价差配权三臂对照（预注册 A1/A2/A3）。
    # ⚠️ 它的**存在理由**曾是一个被证伪的前提：`weight_vs_spread.json`
    #   判决「IC 与尾部价差错配」，而那个判决量曾是 |IC| ——
    #   生产权重用的是带符号 IC，ρ 的符号整个反过来（形状 119）。
    #   登记它是为了让「前提被证伪」这件事本身**可追溯**，不是为了让它可引用。
    "spread_tilted_arm.json": "scripts/spread_tilted_arm.py",
    # ③ 安慰剂对照：判「t=2.44 是不是因子，还是一个差基准」。
    #    判决 `PLACEBO_NULL` 是全项目的**承重判决**——
    #    在它之前「因子有效」一个字都不能写。
    # 🚨 2026-10-15 补登记：它此前**既没登记也没盖章**，
    #    而按本模块自己的规矩 `unknown` = **不许引用**
    #    ⇒ 一份决定「因子有没有效」的产物一直在**不可引用**状态。
    #    这不是形状 6 的变体（那是「假装能调」），是它的镜像：
    #    **假装不可引用**——产物照常出数、判决照常被写进文档，
    #    而它自己从不发出「我不可引用」的信号。
    "placebo_control.json": "scripts/placebo_control.py",
    # ④ 榜宽结果的**金额翻译**：把毛超额换算成「要多少本金才能回本 / 到 20% 净」。
    #    CLAUDE.md「回本所需本金 16.1/23.9/55.8/81.3/182.4 万」那张表就是照它写的。
    # 🚨 2026-10-15 第 137 层补记：**同一组抄错的数在本文件里还有第二份**
    #    （原文 55.9/81.2/182.3，五格错三格）。而那个对账守卫
    #    `tests/test_claude_md_capital_table_matches_artifact.py`
    #    **只扫 CLAUDE.md 一个文件** ⇒ 这份副本在守卫全绿时照样是错的
    #    ⇒ **「已经加了对账守卫」不等于「对账做全了」，守卫自己也有覆盖面**
    #    （第 131 层：守卫要验守卫）。
    #    不重跑回测、只读 `factor_width_mde.json` ⇒ 秒级。
    # 🚨 2026-10-15 补登记 + 补盖章：它此前**两者皆无**，而这张表是
    #    「本金只能吃掉损耗、不能创造收益」那条**结构性结论**的全部依据。
    "width_capital_feasibility.json": "scripts/width_capital_feasibility.py",
    # 篮子权重（等权 / sd / 波动率目标）：判决 `SD_NEUTRAL`，依据是 sd 只降 1.3%
    # 而均值代价吃掉 0.7 个 sd。⚠️ 2026-10-15 补登记 + 补盖章：它此前两者皆无，
    # 而「按波动率缩权」这条路被否掉、实盘改用等权，这个判决是**唯一的依据**。
    "basket_weighting.json": "scripts/basket_weighting.py",
    # 组合 IC 的**充分统计量**（每日 R 8×8 与 Σ_f 8），1882 天 340s。
    # 存下来之后所有权重方案秒级迭代，不再读面板。
    #
    # ⚠️ **2026-10-14 口径修正后重跑**：初版因子端用**原始值** z 标准化，
    # 而收益端已被 `make_ranked_forward` 排了名 —— 算出来的是
    # corr(z(F原始), rank(r))，**一半秩一半原始值**。
    # 它不是 Spearman 也不是 Pearson，**归类错误**，
    # 且弱信号区比真 Spearman 高约 1.047 倍，直接乘在年化收益上。
    # 现在 `sf__*` 是**秩口径**（进判决），`sfP__*` 是原始值口径（只作对照），
    # 让那个比值可查而不是靠记。回归由
    # `tests/test_composite.py::test_the_identity_is_still_exact_on_ranks` 钉着。
    #
    # ⚠️ **2026-10-14 标签修正后再次重跑**：R 的列名曾按
    # `FACTOR_NAMES` 里的**位置**存（`R__ab`），而 `unpack` 按
    # **调用方顺序**读——传子集时整块 R 被**平移错标**，且一列都不缺，
    # 所以**不报错**。R 无论怎么错标都仍然对称、对角线仍全 1，
    # 结构不变量**恒真**，抓不到它。现在是 `R__<因子a>__<因子b>`，
    # 位置这个歧义源头从数据里消失；旧格式由 `unpack` **响亮拒绝**。
    # 往返由 `tests/test_extract_ic_matrices.py` 钉着（已做变异验证：
    # 写入端退回位置索引，三条测试全部变红）。
    "ic_matrices.parquet": "scripts/extract_ic_matrices.py",
    "fundamental_ic.json": "aplus/factor_ic.py",
    "forward_registry.json": "aplus/forward.py",
    # 2026-10-14 新增。**实测** IC→收益那座桥的误差：
    # 权重直接取 `verdict_composite` 用的那一份逐年 `walk_forward`，
    # 横截面走同一个 `build_cross_sections`——差一点点就不可比，
    # 而「差一点点」正是本项目当天栽过四次的地方。
    #
    # ⚠️ **这份报告作废了它自己要质疑的那个公式的用法**：
    # `ic_to_return.py` 的尾部常数分母曾写成 `1−2q`（中间段宽度）
    # 而非 `q`（尾部宽度），差整 `(1−2q)/q` 倍（q=0.2 时 3.0 倍）。
    # 修正常数后桥给出 135%/年，**荒谬**；而直接实测只有 33.51%/年。
    # 也就是说**常数错了不是全部问题，「IC 可线性外推成分位价差」这个
    # 形状本身在 IC≈0.07 这个水平上就不成立**。
    # 所以引用 `verdict_*.py` 打印的「年毛超额」列是不成立的，
    # 只能用本报告里的 `measured_*` 字段。
    "bridge_error.json": "scripts/measure_bridge_error.py",
    # `R⁻¹Σ_f` 逐年权重**第一次**进真实回测引擎。
    # 在此之前它只在 `verdict_composite.py` 里被打印，从未落盘、从未进过
    # `evaluate_weights` ——所以「组合 IC 够不够线」与「能不能赚钱」
    # 这两个问题之间**从来没有一座桥**，只有一次口头类比。
    #
    # 保留四件真回测要素：可成交性闸门（开）、按换手计的成本、
    # Top-N 选股、逐日分块 t。**两个成本口径都报**——
    # 回测默认 0.003 只是价差那项，真实现况 0.0055（低估 1.84 倍），
    # 而门槛用的是 0.0055：只报前者 = 分子乐观分母悲观。
    "wf_weights_backtest.json": "scripts/backtest_walk_forward_weights.py",
    # 因子个数扫描（石川：因子不是越多越好）。2026-10-14 补登记。
    "sweep_factor_count.json": "scripts/sweep_factor_count.py",
    "financial_annual_cache.parquet": "aplus/financial.py",
    "evolution_ledger.json": "aplus/ledger.py",
    # 2026-10-15 补登记。此前这两份**未登记也不盖章** ⇒ `freshness` 报
    # `unknown`，而按本模块自己的规矩 `unknown` = **不许引用** ——
    # 也就是说，全仓最要害的两份判决（榜宽、归因）当时是「不可引用」的。
    # 登记只是第一步：**还得由写它的脚本真的调 `stamp()`**，
    # 否则登记表有名字、sidecar 仍然是空的（见 `test_registered_is_not_enough`）。
    "factor_width_mde.json": "scripts/factor_width_mde.py",
    "factor_attribution.json": "scripts/factor_attribution.py",
    "cap_weighted_benchmark.json": "scripts/cap_weighted_benchmark.py",
    # 2026-10-18 登记：`cap_exposure_ratio` 的**分母侧符号**可判性探针。
    # 🚨 它存在的理由是 `_size_tilt_label` 只剩四支、`1.0173` **必然**落进
    #   「组合偏大盘」，而那条标签是「C 臂不是伪装成 alpha 的市值押注」
    #   的唯一依据 ⇒ 用「测不出」去支撑「没有押注」会让**唯一可能推翻
    #   C 臂的检查**被跳过（失效方向是乐观的）。
    # ⚠️ 本产物**只量分母**：分子（组合持仓逐日中位市值）在 `CapContext.diag`
    #   里算完即弃、未落盘 ⇒ 它的判决**不能**推出组合自身暴露的方向。
    #   判据零阈值（只问符号，符号零点 1.0 本身就是零点）⇒ 预注册
    #   `docs/PREREG_CAP_EXPOSURE_SIGMA_2026-10-18.md` 写在任何实测之前。
    "cap_exposure_sigma.json": "scripts/cap_exposure_sigma_probe.py",
    # 2026-10-15 登记：S3/G4 —— **`score` 四分位有没有排序力**。
    # 这不是一份研究报告，是**发布闸门**：`docs/TODO_reverse_scoring_2026-10-15.md`
    # §3 原文「过不了这一关就不许发布」。
    # 🚨 登记它的理由和上面几份不同：**它的判决是「不许发布」**，
    #   而「不许发布」这种结论一旦没有产物，就只活在终端里 ⇒
    #   下一个人会以为闸门还没测，于是照发。
    # ⇒ 它的 `verdict` 是**三态**而不是布尔：
    #   `NOT_PASSED_POWERLESS`（不单调且检出力不足）与
    #   `NOT_PASSED_MEASURED`（不单调且检出力足够）**都意味着不许发布**，
    #   但下一步动作相反（攒前瞻期 vs 换结构）。
    #   把两者合并成「不通过」会丢掉唯一有用的那部分信息。
    "score_ranking_power.json": "scripts/score_ranking_power.py",
    # 2026-10-15 登记：整手（A 股 100 股）可行性 ——
    # **¥50,000 到底能买进几个名字**。它不重跑回测，只报一个事实：
    # 在 `EQUITY_WEIGHT = 1.0` 的满仓假设下，单笔 = 50000/N，
    # 而 1 手要买得起，股价须 ≤ `50000/(100·N)`。
    # 抽样 499 只的实测：**89.2% / 54.1% / 14.4% / 4.2% / 3.2%**
    # （N = 10/30/100/200/546）。
    # ⇒ 回测与 `aplus/costs.py` **都在小数股上跑**
    #   （实测 `grep -rn '整手' aplus/ scripts/` 零命中），
    #   而**榜宽越宽、单笔越小、越买不起** ⇒ 现有 `widths[*]` 里
    #   N ≥ 30 的那些档是在**开不出来的账户**上测出来的。
    # 登记它是为了让「宽榜更稳」这句话**带上它的前提**，不是为了让它可引用。
    "lot_affordability.json": "scripts/lot_affordability.py",

    # 🚨 判决链上「缺的那个数」：用户的目标字面上是**账户净值年化 20%**，
    #   而 `hurdle.py` 给的是**要拿到多少超额** —— 恒等式
    #   `账户净值 = 基准 + 超额 − 摩擦` 里**少了基准项**，
    #   而那个式子**只在基准年化 = 0 时成立**。
    #   实测等权基准几何年化 **9.9471%**（2021-01-04→2026-10-09，n=1395，零缺日）
    #   ⇒ 毛超额门槛 **26.41% → 16.47%**（偏高量 9.95pp），
    #   而五档毛超额 4.81%~7.57% **全部仍然不够线** ⇒ 判决方向一字未改（R 档）。
    #   ⚠️ **本条只改门槛数字，不改结论**：门槛降低 = 目标降低（第 129 层）。
    #   预注册 `docs/TODO_nav_vs_excess_caliber_2026-10-15.md` §4 G3/G3-bis/G3-quad。
    "market_nav_caliber.json": "scripts/market_nav_caliber.py",

    # 2026-10-17 补登记：择时腿的两份判决地平线产物。它们此前**盖了章但文件名不在册**
    #   ⇒ `resolve_generator()` 只查这两张表、**从不读 sidecar** ⇒ 返 `None` ⇒ `unknown`
    #   ⇒ 按本模块自己的规矩「不可引用」。
    #   ⚠️ **这与「没盖章」是两种状态，修法相反**：没盖章须重跑；盖了章而没登记须补注册表。
    #     误判成前者会白白重跑两份已经验真的产物。
    #   🚨 断言给的另一半修法是「删掉那份 sidecar」——**本轮明确拒绝**：
    #     删章会销毁**已被四个独立布尔验真**的证据（生成器存在 ∧ sha256==章 ∧
    #     源 parquet 存在 ∧ sha256==章，程序比对、八个布尔全 True），
    #     而本模块的 `stamp()` 本来就是**自动盖章、不会与真写入者漂移**的那一套。
    #   ⇒ 四个独立布尔 = 「盖章的可验证性」；`unknown` = 「读者查不到生成器」。二者不同层。
    "market_timing_ceiling.json": "scripts/market_timing_ceiling.py",
    "market_timing_signal.json": "scripts/market_timing_signal.py",

    # 🚨 2026-10-16 补登记：下面四份此前**既未登记也未盖章** ⇒ `freshness` 报
    #   `unknown`，而按本模块自己的规矩 `unknown` = **不许引用**。
    #   之所以拖到现在才收口，是因为它们是 2026-10-16 当天才写出来的四个
    #   **判决地平线**脚本，而**判决恰恰是最不许没有产物出处的东西**
    #   （第 6 种形状的镜像「假装不可引用」：产物照常出数、判决照常被抄进文档，
    #   而它自己从不发出「我不可引用」的信号）。
    #
    # ① D1（每天 10 涨 10 跌）的判决需要多少个**独立成交窗口**。
    #   它与 D2 卡的**不是同一个量**：观测单位是成交窗口（`exit_date` 去重，
    #   现读数 **W=5**）而不是交易日（1395 天）⇒ 136 行判定只落在 5 个窗口上，
    #   行级区间虚增 `√(136/5) = 5.215` 倍。
    "verdict_horizon_d1.json": "scripts/verdict_horizon_d1.py",
    # ② D2 的判决地平线：判符号需 **26.4 年**（`1.96·sd·√R/√n` 反解）。
    #   ⚠️ ①与②的先后不是随意的：②先算、①后算，而**两者差一个数量级以上**
    #   ⇒ 「两个交付物都样本不够」是一个**把两个量合并**的说法（第 7 种形状）。
    "verdict_horizon.json": "scripts/verdict_horizon.py",
    # ③ 摩擦敏感性扫描：在多大的价差假设下，摩擦会先于信号把 20% 目标吃光。
    #   🚨 **它零数据输入**（纯推导，读的全是仓内常量与已落盘产物）
    #   ⇒ `stamp()` 默认那个 `master/daily` 的 `data_fp` 对它是**一枚假章**
    #   （第 166 层：章指向它压根没读过的目录）⇒ 脚本用 `extra` 逐字声明
    #   「`data_fp` 不适用」，并在产物正文里带这个声明。
    "friction_sensitivity.json": "scripts/friction_sensitivity.py",
    # ④ IC 序列**自相关**的半衰期（Kahn 1999 定义），用来回答「k=21 有没有物理机制」。
    #   ⚠️ 🚨 **它不是「前瞻收益随持有期衰减的半衰期」**，两者不可互换：
    #   前者是 IC 序列的 ρ(k) 降到 0.5 的 lag，后者要逐日重算 forward IC，
    #   成本高一个量级且**结论方向可能不同**。
    #   同理，`fundamental_ic` 用的 `[21,63]` 窗口**不能**与
    #   `ic_matrices.parquet` 的 1 天前瞻窗口并列比较 ——
    #   那不是同一个量（记忆 `ic-autocorrelation-is-not-halflife`）。
    "ic_halflife.json": "scripts/measure_ic_halflife.py",
    # ⑤ 前瞻 D2 的第一个实测判决（判「超额腿能不能定出符号」，
    #   **不判**「年化够不够 20%」—— 那需要 n ≥ 174 个独立观测，
    #   而底稿的独立观测上限实测是 **4** 个 `entry_date`）。
    #   判据逐字在 docs/PREREG_FORWARD_D2_2026-10-16.md §3 R1~R5（写死在跑产物之前）。
    #   🚨 它的独立观测单位是 **`entry_date` 而不是 `pred_date`**：
    #   实测 6 个 `pred_date` 只对应 4 个 `entry_date`，10-09/10-10/10-11
    #   三天的单**全部 entry 到 2026-10-12** ⇒ 持有同一批 bar ⇒ 不是 3 个观测。
    "forward_d2_verdict.json": "scripts/forward_d2_verdict.py",
    # ⑥ 前瞻 D2 的**时间账**（2026-10-17 登记）：回答「还要攒多少 `entry_date` 块、
    #   还要等多少个日历日」，而 `forward_d2_verdict.json` 只答「现在能不能定出符号」。
    #   🚨 **它的观测单位与 Bailey 的外部锚不可比**：书里那个「数据点」是**日频观测**
    #   （681 点 = 2.71 年日频），而本产物的观测单位是 **`entry_date` 块**
    #   ⇒ 两个 n 不是同一个量，只各自成表（第 7 层）。
    "forward_d2_horizon.json": "scripts/forward_d2_horizon.py",
    #   D1 的**真实交付形态**（次日买、次日卖、5 万、10 只）下的年摩擦判决。
    #   🚨 它与仓里其余全部 D2 读数**不是同一个量**：那些出自 K=21 回测，
    #   而本项量的是**日频**形态的**成本算术**（不需要 MDE，见预注册 §3 D1）。
    "delivery_caliber_friction.json": "scripts/measure_delivery_caliber_friction.py",
    #   D1 的**另一半**（每天 10 只跌股）的判决臂，2026-10-17 登记。
    #   🚨 判别臂是 `score_ranking_power.py`（`GATE_ARM="up"`）；**本条是对照臂**
    #      （产物 `role` 字段自报 `CONTROL_ARM_NOT_THE_VERDICT_ARM`），
    #      它判的是 D1 交付物的另一半，此前从未被判决过。
    #   ⚠️ 登记 ≠ 可引用：还要有 sidecar。本脚本自己 `stamp(..., expect_bytes=)`
    #      且 `extra` 自报 `source_parquet_sha256`（第 156 层：**章里的 `data_fp`
    #      指的是 `master/daily` 目录，而本脚本只读一个 parquet**，
    #      判断输入有没有变只认 `source_parquet_sha256`）。
    #   ⚠️ `code_dirty=True` 时 `trustworthy` 恒 `False`，引用时两句都要带。
    "downleg_ranking.json": "scripts/measure_downleg_ranking.py",

    # ── 2026-10-20：D1 每天真正交付的那几个产物 ──────────────────────
    #
    # 🚨 **本块登记的是「写文件的那个模块」，不是调用它的脚本。**
    #   `scripts/run_daily.py` 调 `aplus.reports.write_run_status()`，
    #   但落盘的是 `aplus/reports.py` ⇒ 登记成 `scripts/run_daily.py`
    #   会让 `commits_touching()` 去追**调用方**的提交历史，
    #   于是「谁动过这个产物」答的是另一个文件的提交（第 123 层）。
    #   同理 `holdings.json` 由 `aplus/live_rebalance.py::save_state` 写
    #   （`STATE_NAME` 常量），`forward_results.json` 由
    #   `aplus/forward.py::merge_results` 写 —— 三者都**不是** `run_daily.py`。
    #
    # ⚠️ **登记 ≠ 盖章。** 本块只让 `freshness()` **能判**；
    #   真拿到 sidecar 还要写它的那三个模块自己调 `stamp()` ——
    #   **盖章接线在另一批改动里**（`aplus/reports.py` /
    #   `aplus/live_rebalance.py` / `aplus/forward.py` 由另一批改动接手）。
    #   在那之前这三份**仍然是 `unknown` = 不可引用**，
    #   而这正是登记的真实效果：**判据能跑了，证据还没到。**
    #   🚨 不许为了让 `freshness()` 变绿而放宽任何判据 ——
    #     `unknown` 的含义是「判据没跑成 / 没有证据」，**不是**「事实为否」。
    "run_status.json": "aplus/reports.py",
    "holdings.json": "aplus/live_rebalance.py",
    "forward_results.json": "aplus/forward.py",
    # 2026-10-20 补登记：**跌腿闸门**（D1 每天 10 只跌腿那半边的发布闸），
    # 产物 `role = "DOWNLEG_GATE_ARM"`、判 `NOT_PASSED_POWERLESS`、
    # 自报 `avoidance_list_publish_allowed: false` ⇒ **它决定跌腿名单许不许发**，
    # 与上面三份同属 D1 交付面。
    # 🚨 它是 `scripts/measure_downleg_ranking.py` 的**第二份产物**
    #   （`--out` → `downleg_ranking.json` 对照臂，
    #     `--gate-out` → `downleg_gate.json` 闸门）⇒ 与它兄弟那条**同一生成器**。
    # ⚠️ 登记只是让 `freshness()` **能判**；它**仍无章** ⇒ 仍判 `unknown` = 不可引用。
    "downleg_gate.json": "scripts/measure_downleg_ranking.py",
    # ── 2026-10-21 登记：本轮新产出的判决/测量产物 ──────────────────
    # 每一个都**盖了真章**（sidecar 的 code_file_sha256 与当前生成器字节逐位相同）
    # ⇒ 不登记 ⇒ `freshness()` 判 unknown ⇒ 按仓规**不可引用**
    # ⇒ 而「盖了章却解析不出生成器」比「没盖章」更坏：读者无从判断它出自哪份代码。
    "bond_deployed_nav.json": "scripts/measure_bond_deployed_nav.py",
    "pool_board_exposure_2026-10-21.json": "scripts/measure_pool_board_exposure.py",
    "dividend_pit_coverage_shard1of5.json": "scripts/collect_dividend_pit.py",
    # 50 只小样本那一份（第一批采集的产物）—— 它是**真实历史**，
    # 按第 25 层「不删不改不抹历史」⇒ 登记而不是删 sidecar。
    "dividend_pit_coverage_2026-10-21.json": "scripts/collect_dividend_pit.py",
    "fundamental_contamination_concentration_2026-10-20.json":
        "scripts/measure_fundamental_contamination_concentration.py",
    "fundamental_coverage_axes_2026-10-20.json":
        "scripts/measure_fundamental_coverage_axes.py",
    "fundamental_factor_family_cross_section_2026-10-20.json":
        "scripts/measure_fundamental_factor_family_cross_section.py",
    "bm_ic_attribution_reduced_2026-10-21.json": "scripts/bm_ic_attribution.py",
    # 2026-10-21 登记：BM 的 canonical 归因（轮次 B 的 B3 刷新件，**基名固定**）。
    # 🚨 同样是被 `test_every_stamped_report_resolves_back_to_a_registered_generator`
    #   点名的：盖了章却解析不出生成器 ⇒ 按仓内规矩**不可引用**。
    "bm_ic_attribution.json": "scripts/bm_ic_attribution.py",
    # 每晚复盘产物（最新 + 每日归档）
    "daily_review.json": "scripts/daily_review.py",
    "dividend_lowvol_longleg.json": "scripts/measure_dividend_lowvol_longleg.py",
}

#: 文件名**模式** → 生成模块。逐档产物是同一模块的第 6~10 份输出；
#: 写成 5 条具体名字的话，**新增一档就又漏登记**（CLAUDE.md 形状 17：
#: 同一语义被处理 N−1 次）。基名走 `DEFAULT_GENERATORS`，逐档走这里。
PATTERN_GENERATORS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^wf_weights_backtest_k\d+\.json$"),
     "scripts/backtest_walk_forward_weights.py"),
    #   判决臂 C 的六格（N∈{10,30,50} × K∈{21,63}），预注册
    #   `docs/PREREG_CAP_WIDTH_GRID_2026-10-18.md`（2026-10-19 跑完）。
    #   🚨 走**模式表**而不是往 `DEFAULT_GENERATORS` 里写六个名字：
    #   六格是同一生成器的第 2~7 份输出，枚举写法下**新增一格就又漏登记**
    #   （CLAUDE.md 形状 17：同一语义被处理 N−1 次）。
    #   基名 `cap_weighted_benchmark.json`（N=200/K=21 的既有产物）在
    #   `DEFAULT_GENERATORS` 里，且**先查精确表**，所以它归精确表管。
    #   ⚠️ 刻意**不写**「两条互不遮蔽」这类话：两者指向**同一个脚本** ⇒
    #      返回值逐位相同 ⇒「谁命中的」在行为上不可观测（形状 23 之后那条
    #      **散文里的公式没有守卫**的变体：留着一句没人能验的宣称）。
    (re.compile(r"^cap_weighted_benchmark_n\d+_k\d+\.json$"),
     "scripts/cap_weighted_benchmark.py"),
    # 2026-10-21 登记：持仓级降级预警（`holdings_alert_<信号日>.json`）。
    # 走**模式表**而不是精确表：文件名带日期（每天一份）⇒ 枚举写法下
    # **新增一天就又漏登记**（CLAUDE.md 形状 17）。
    # 🚨 不登记的后果同上一条：`resolve_generator()` 返 `None` ⇒
    #   `freshness()` 判 `unknown`（= 不许引用），而「不可引用」与
    #   「数据坏了」长得一模一样（形状 16：盖一个解析不出生成器的章比不盖更坏）。
    (re.compile(r"^holdings_alert_\d{4}-\d{2}-\d{2}\.json$"),
     "scripts/measure_holdings_alert.py"),
    # 2026-10-21 登记：基本面族**轮次 B**的机理全集（臂 R）。
    # 文件名带**批次标签**（`armR` / `armR_fullpool` + 日期）⇒ 走**模式表**：
    # 枚举写法下「再加一臂就又漏登记」（形状 17）。
    # ⚠️ `bm_ic_attribution.json` 走**精确表**（基名固定，不带标签）。
    (re.compile(r"^fundamental_mechanism_armR[\w-]*\.json$"),
     "scripts/measure_fundamental_mechanism_armR.py"),
]


def resolve_generator(name: str) -> str | None:
    """报告文件名 → 生成模块。先查精确表，再查模式表。

    ⚠️ 表外的返回 `None`（⇒ `unknown` ⇒ 不许引用）。
      **不回落猜测**：猜错生成器会让 `commits_touching` 去查一个无关文件，
      得出一个「看起来判定过、其实判定的是别的东西」的结论。
    """
    if name in DEFAULT_GENERATORS:
        return DEFAULT_GENERATORS[name]
    for pat, gen in PATTERN_GENERATORS:
        if pat.match(name):
            return gen
    return None


# ── git ────────────────────────────────────────────────────────────

def _git(root: Path, *args: str) -> str | None:
    try:
        r = subprocess.run(["git", "-C", str(root), *args],
                           capture_output=True, text=True, timeout=30)
        return r.stdout.strip() if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def code_sha(root: Path | str | None = None) -> str | None:
    """当前代码的 sha。取不到就返回 None——**不做降级猜测**。"""
    return _git(_root(root), "rev-parse", "HEAD")


def is_dirty(root: Path | str | None = None) -> bool:
    """工作区有未提交改动。

    有未提交改动时 sha 不足以标识代码状态——**所以这个标记要跟着
    sidecar 一起存**，否则「干净时是 A、脏时其实是 B」这种混淆无法事后分辨。
    """
    out = _git(_root(root), "status", "--porcelain")
    return bool(out)


def code_file_sha256(root: Path | str | None = None, generator: str | None = None) -> str | None:
    """生成器**文件字节**的 sha256 —— dirty 时唯一还能标识「实际跑的是什么」的东西。

    ★ 为什么这个字段必须存在：`code_sha` 是 `git rev-parse HEAD`，标识的是
      **提交**；而实际执行的是**工作区字节**。工作区脏时两者是不同的东西。

      2026-10-15 实测（wf k=5）：章里写 `code_sha=27bcdfd`，
      而实际跑的是**后来成为 `a09fbdb` 的那些字节** ——
      `27bcdfd` 那棵树里的脚本**根本没有盖章调用**，
      即**侧卡本身就是它所声称那个 sha 的反证**。

      更糟的是 commit 走查：它只看 commit，于是把「脏改动后来被提交」
      判成「生成之后动过」⇒ 对一个**当前代码就能复现**的产物**误报 stale**。
      这是过严守卫：它红的时候把人推向「去改本来正确的产物」。

    取不到就返回 `None`，**不做降级猜测**（与 `code_sha` 同规矩）。
    """
    if not generator:
        return None
    f = _root(root) / generator
    if not f.is_file():
        return None
    return hashlib.sha256(f.read_bytes()).hexdigest()


#: 数据根。默认取主库日线——**全部回测/实验读的都是它**。
DATA_DIR = "data/master/daily"


def data_fingerprint(root: Path | str | None = None, data_dir: str = DATA_DIR) -> str | None:
    """数据指纹 = `(文件数, 最大 mtime)`。目录不存在返回 `None`。

    ★ 为什么**只**盖章而不折进 `freshness()`：`freshness` 的语义是
      「相对生成模块还新不新」，它的守卫有 15 条、结论已经写进文档；
      把数据条件折进去会让**全部 20 个已登记报告**一次性翻红，
      而它们大多只是没带这个新字段 —— 那是**引入新问题**，不是修问题。
      ⇒ 拆成并列的一条 `data_freshness()`，各自独立判。

    ⚠️ **已知边界（不是猜测，`require_run_completed` 已经撞过一次）**：
      mtime 认不出「改了又改回」——`cp` 还原也会刷新 mtime，
      于是指纹**保持不变**，而数据其实变过。
      ⇒ 这个指纹证明「**数据没在报告之后被动过**」，
      **不**证明「报告用的就是现在这份数据」。
      后者只有内容哈希能给，而 GB 级每次盖章跑不动。
      ⇒ 所以它是**必要条件**，不是充分条件；别把它的 `fresh` 读成「数据对」。

    取不到就返回 `None`（同 `code_sha`）——**不做降级猜测**。
    """
    d = _root(root) / data_dir
    if not d.is_dir():
        return None
    n = 0
    newest = 0.0
    for p in d.glob("*.parquet"):
        try:
            m = p.stat().st_mtime
        except OSError:
            continue
        n += 1
        newest = max(newest, m)
    if n == 0:
        return None
    return f"{n}:{newest:.6f}"


def data_freshness(report_path: Path | str, root: Path | str | None = None,
                   data_dir: str | None = None) -> dict:
    """报告的数据新鲜度，与 `freshness()`（代码）**并列**。

    返回 `verdict` ∈ {`fresh`, `stale`, `unknown`}，语义与 `freshness` 一致：
    `unknown` = **不许引用**，不是「大概没事」。

    ★ `data_dir` 缺省时**用章里记的那个**（`data_dir` 字段）——
      拿仓库那份去比一份用 `/tmp` 快照跑出来的报告，比出来的 `fresh`
      是**假的**（2026-10-15 实测：`snap_1015` 与仓库差 5217/5460 只）。
      章里没有该字段（旧章）才回落到调用方给的路径。
    """
    name = Path(report_path).name
    st = read_stamp(report_path)
    if data_dir is None:
        data_dir = (st or {}).get("data_dir") or DATA_DIR
    now = data_fingerprint(root, data_dir)
    if st is None:
        return {"report": name, "verdict": "unknown",
                "problems": ["没有溯源 sidecar——数据指纹无从得知"]}
    got = st.get("data_fp")
    if got is None:
        return {"report": name, "verdict": "unknown",
                "problems": ["溯源章里没有 data_fp（盖章时还没有这个字段）"]}
    if now is None:
        return {"report": name, "verdict": "unknown",
                "problems": [f"数据根 {data_dir} 不存在或为空，指纹算不出"]}
    if got != now:
        return {"report": name, "verdict": "stale",
                "problems": ["数据在报告之后被动过"],
                "stamped_data_fp": got, "current_data_fp": now}
    return {"report": name, "verdict": "fresh",
            "stamped_data_fp": got, "current_data_fp": now}


def commits_touching(root: Path | str, path: str, since: str | None) -> list[str]:
    """`since` 之后动过 `path` 的 commit（短 sha + 摘要）。

    `since` 为 None 时返回空列表——**没有基线就无从判断「之后」**，
    硬猜一个会让「无法判断」看起来像「没变化」，而那正是最危险的误判。
    """
    if not since:
        return []
    out = _git(_root(root), "log", f"{since}..HEAD", "--oneline", "--", path)
    return [ln for ln in (out or "").splitlines() if ln.strip()]


# ── sidecar ────────────────────────────────────────────────────────

def sidecar_path(report_path: Path | str) -> Path:
    p = Path(report_path)
    return p.with_name(p.name + SIDECAR_SUFFIX)


def resolve_data_dir(root: Path | str | None = None, data_root: Path | str | None = None,
                     daily_subdir: str = "master/daily") -> str:
    """算出**这次跑真正读的那份日线**的路径。

    ★ 为什么必须显式：脚本的 `--data-root` 默认是
      `/tmp/aplus_snap_1012`（一个**几天前的快照**），而 `data_fp`
      一律按仓库里的 `data/master/daily` 算 ——
      于是章里记的指纹**描述的不是这份产物用的数据**。

      实测代价（2026-10-15）：`snap_1015` 与仓库差 **5217/5460 只**
      （`000001.SZ` 仓库 1885 行 vs 快照 1883 行），而当时**所有**产物
      都是 `--data-root /tmp/aplus_snap_1015` 跑出来的
      ⇒ 指纹会读成 `fresh`，而它量的压根是另一份数据。

    ⇒ 两种情形都记**真实路径**，不静默回落到默认值：
      `data_root` 给了就记它；没给就记仓库那份。
      ⚠️「静默回落到默认值」正是这个 bug 的原型。

    ## ⚠️ 同名参数，两种布局 —— 今天第四个「口径」级别的坑

    `--data-root` 在**三个**脚本里指的不是同一个东西：

    | 脚本 | 指的是 | 数据在哪 |
    |---|---|---|
    | `factor_width_mde.py` / `backtest_walk_forward_weights.py` | **主库根** | `<root>/master/daily` |
    | `factor_attribution.py` | **上游 parquet 所在目录** | 就是它本身 |

    ⇒ 调用方必须说清自己的布局（`daily_subdir=""` = 目录本身就是数据）。
    **同名不等于同义** —— 照 `master/daily` 硬套会算出**不存在的目录**，
    指纹变成 `None`，而 `None` 在这个模块里的语义是 `unknown`（不许引用）。
    那个方向是安全的，但它是**蒙对的**，不是对的。

    落在仓库之外（`/tmp/aplus_snap_*`）时记**绝对路径** ——
    否则 `data_freshness` 会拿仓库那份来比，比出来的 `fresh` 是假的。
    """
    if data_root is None:
        return DATA_DIR
    dr = Path(data_root)
    target = dr if not daily_subdir else dr / daily_subdir
    try:
        return str(target.resolve().relative_to(_root(root).resolve()))
    except ValueError:
        return str(target)


def _repo_relative_generator(generator: str, root: Path | str | None = None) -> str:
    """生成器路径 → **仓相对路径**（可搬迁的章）。

    ## 为什么要做这一层规范化

    🚨 **2026-10-16 实测**：全仓 55 份 sidecar 里有 **1 份**把 `generator`
      写成**绝对路径**（`/Users/airboom/opencode/aplus/scripts/market_nav_caliber.py`），
      而其余 54 份全是仓相对路径 ⇒ **口径孤例**。

      成因不在本模块，而在一个**调用点**：`scripts/market_nav_caliber.py:239`
      传的是 `__file__` —— 而 **Python 3.9 起 `__file__` 恒为绝对路径**
      （3.8 以前是相对路径，所以这条缺陷是版本升级带进来的）。
      本模块只是**照抄**了调用者给它的字符串。

      后果：那枚章**不可搬迁** —— 仓一换位置（换机器、换目录、换挂载点），
      章里的路径就指向一个不存在的文件，而**章本身看上去完全正常**。

    ⇒ 修法放在**盖章那一刻**（本模块），而不是「读的时候再纠正」：
      `freshness()` 解析生成器走的是 `DEFAULT_GENERATORS` 的**登记值**
      （本来就是相对路径），sidecar 里的 `generator` 是**给人读的**
      —— 它必须自解释、且跟着仓一起搬。

    ⚠️ **只在确实落在仓内时转相对**；仓外的绝对路径**原样保留** ——
      把它硬转成 `../../..` 既更难读，也会丢掉「它在仓外」这个诚实信息
      （仓外数据根 `data_dir` 就是这么处理的，见 `resolve_data_dir`）。

    ⚠️ **不改变字节哈希**：`_root(root) / "scripts/x.py"` 与
      `_root(root) / "/abs/root/scripts/x.py"` 指向同一个文件
      （pathlib 里绝对右操作数覆盖左操作数）⇒ `code_file_sha256` 逐位不变。
    """
    p = Path(generator)
    if not p.is_absolute():
        return generator
    try:
        return str(p.resolve().relative_to(_root(root).resolve()))
    except ValueError:
        # 在仓外（如 `/tmp/...`）：保持原样 —— 硬转相对会把「它在仓外」藏起来。
        return generator


def stamp(report_path: Path | str, generator: str, root: Path | str | None = None,
          extra: dict | None = None,
          data_root: Path | str | None = None,
          daily_subdir: str = "master/daily",
          expect_bytes: str | None = None) -> Path:
    """给一份刚生成的报告盖上溯源章。

    写在**报告旁边**而不是报告内部：报告的 schema 是下游在读的，
    往里塞字段会污染它，也会在 `sha256` 比对时炸掉。

    ★ `generator` 会被规范成**仓相对路径**（见 `_repo_relative_generator`）——
      绝对路径（如 `__file__`）也会被转掉 ⇒ **章可搬迁**。
      仓外的绝对路径原样保留。

    ★ `data_root` 必须是**这次跑真正读的**那个根（通常直接传 `--data-root`），
      `daily_subdir` 必须是这个脚本的布局（见 `resolve_data_dir`）。
      不传就退回仓库那份 —— 那正是 2026-10-15
      「指纹读成 fresh 而数据是 `/tmp` 快照」的成因。

    🚨🚨 `expect_bytes`（第 125 层）：**盖章那一刻盘上的字节**，
      不等于**这次执行真正跑的那些字节** ——
      K 链串行跑 ~10 分钟一档，我在跑的过程中改了生成器，
      `stamp()` 就在每档写盘那一刻算哈希 ⇒ 章上写的是「写盘时盘上恰好是这些字节」，
      而不是「这份产物是这些字节造出来的」。
      ⇒ 两侧不一致时**不是弱证据，是反向的错误证据** ⇒ **拒绝盖章**。

      用法（由 `main()` 开头抓一次）：

          expect = provenance.code_file_sha256(ROOT, GENERATOR)
          ...跑…
          stamp(out, GENERATOR, root=ROOT, expect_bytes=expect)

      不传 `expect_bytes` ⇒ **不做这项检查**（保持旧行为）。
      ⚠️ 这是**刻意**不做成必填：几十个调用点，一次性改完会把
      「大家都在传」变成「大家都在抄一个变量」——
      那样检查照样在，而没人再读它（第 102 层：探针打在自己的副本上）。
      ⇒ 正确性靠**新写的脚本按这个模式写** + 下面那条守卫钉住「用了就必须传」。
    """
    # ★ 生成器路径规范化成**仓相对路径**（章要可搬迁 —— 见
    #   `_repo_relative_generator` 的注释与那枚绝对路径孤例的实测）。
    #   必须在算字节哈希**之前**做：相对路径与绝对路径指向同一个文件，
    #   所以哈希逐位不变（见该函数的边界说明）。
    generator = _repo_relative_generator(generator, root)
    data_dir = resolve_data_dir(root, data_root, daily_subdir)
    now_bytes = code_file_sha256(root, generator)
    if expect_bytes is not None and expect_bytes != now_bytes:
        raise RuntimeError(
            f"拒绝盖章：生成器在**跑的过程中**被改过。\n"
            f"  {generator}\n"
            f"  开跑时的字节 {expect_bytes}\n"
            f"  盖章时的字节 {now_bytes}\n"
            f"⇒ 这枚章说的不是「这份产物是这些字节造的」，"
            f"而是「写盘那一刻盘上恰好是这些字节」（第 125 层）。\n"
            f"⇒ 铁律：**产物正在生成时，它的生成器字节必须冻结**。"
            f"重跑这一档，别把这次的数字当数用。")
    rec = {
        "code_sha": code_sha(root),
        "code_dirty": is_dirty(root),
        # ★ 字节哈希：dirty 时 `code_sha` **标识不了实际跑的代码**。
        #   没有它，`freshness()` 只能靠 commit 走查，而走查看不见未提交的那部分。
        "code_file_sha256": now_bytes,
        # ★ 数据指纹**默认写入**，不做成可选参数：可选 = 下一个报告会忘，
        #   而「忘了」这件事没有任何守卫会报（形状 17：同一语义被处理 N−1 次）。
        #   2026-10-15 的实测代价：`factor_attribution.json` 比数据修复早 77 秒，
        #   而 `code_sha` 一直是「新鲜」的 —— **代码没变，数据变了**，
        #   而章里没有字段能表达这件事。
        "data_fp": data_fingerprint(root, data_dir),
        "data_dir": data_dir,
        "generator": generator,
        "stamped_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if extra:
        rec.update(extra)
    out = sidecar_path(report_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def read_stamp(report_path: Path | str) -> dict | None:
    p = sidecar_path(report_path)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


# ── 判定 ───────────────────────────────────────────────────────────

def freshness(report_path: Path | str, generator: str | None = None,
              root: Path | str | None = None) -> dict:
    """一份报告相对它的生成模块，是否还新鲜。

    返回 `verdict` ∈ {`fresh`, `stale`, `unknown`}：

    - `stale` —— 有溯源章，且**生成之后**动过生成模块。
    - `unknown` —— 没有溯源章，或章里的 sha 不是本仓库的 commit。
      **不是「大概没事」，是「不许引用」。**
    - `fresh` —— 有章，且生成模块此后未动。

    ## 脏工作区单独报出来

    脏的时候 sha 描述不了真实代码状态，**任何 `fresh` 结论都不可靠**——
    所以 `dirty=True` 时即使 verdict 是 fresh，也附 `trustworthy=False`。
    宁可多一次人工确认，也不要一个看起来干净实际不干净的结论。

    ## 🚨🚨 字节证据**只覆盖生成器自己**，不覆盖它的 import 闭包（2026-10-21 实测）

    `code_file_sha256` 盖的是**生成器那个文件**的字节。
    ⇒ **生成器 import 的模块变了 ⇒ 它的行为变了 ⇒ 而它的字节没变 ⇒ 仍判 `fresh`。**

    **实测的一例**：`scripts/regime_control_min_basket_control.py` 的判决阈值来自
    `aplus/evolve.py::LIVE_REBALANCE_EVERY` ⇒ **K 从 21 改成 63** 之后：
    - 生成器文件**一个字没动** ⇒ `code_file_sha256` **逐位相同** ⇒ 判 `fresh`
    - 而它的**判决行为变了**（门槛随 K 变）⇒ 产物里的数是**旧 K 下算的**

    ⇒ ⇒ **`fresh` 的含义是「生成器自身没动」，不是「内容仍然正确」。**
    ⇒ ⇒ 这是 `trustworthy=False`（只证明生成器、不证明依赖闭包）那条边界的**具体实例化**。
    ⇒ ⇒ **唯一可靠的处置是重跑**（⇒ 产物与当前行为同源），
    **不是**相信 `fresh`。
    """
    root = _root(root)
    rp = Path(report_path)
    name = rp.name
    gen = generator or resolve_generator(name)
    st = read_stamp(rp)

    # ── C1：裸名（`TODO_generator_static_guards` §1.5，判据先于实现写下）──
    #   `sidecar_path` 把后缀拼在**完整文件名**后面，所以裸名 `verdict_composite`
    #   去找的是 `verdict_composite.provenance.json`，真名是
    #   `verdict_composite**.json**.provenance.json` ⇒ 静默返回「没有 sidecar」。
    #   而 `DEFAULT_GENERATORS` 31 个键里 29 个带 `.json`
    #   ⇒ `resolve_generator("verdict_composite")` 也是 `None`
    #   ⇒ 两条理由**都是假的**。
    bare = bool(name) and not rp.parent.name and not name.endswith(".json")
    bare_gen = resolve_generator(name + ".json") if bare else None

    # **所有原因一次报全，不在第一个就 return。**
    #
    # 初版按「sidecar → generator → sha」逐个 return，于是又老又没登记的
    # 报告只报「没有 sidecar」——而**可操作**的那条（去 DEFAULT_GENERATORS
    # 补一行）被吞掉了。只报第一个原因，等于把能修的那条藏起来：
    # 人去补了 sidecar，下次还是 unknown，而且这次连线索都没了。
    problems: list[str] = []
    if bare:
        problems.append(
            f"传进来的是**裸名** `{name}`（既无目录也无 `.json` 后缀）⇒ "
            f"`sidecar_path` 去找的是 `{name}.provenance.json`，"
            f"而真名是 `{name}.json.provenance.json`"
            + (f"；补上 `.json` 后能解析到生成器 `{bare_gen}`，"
               f"请传 `data/reports/{name}.json`"
               if bare_gen else "；且补上 `.json` 后仍不在登记表里"))
    if st is None:
        # ── C2：产物缺失 与 产物在但没章，是**两件事**，不许用同一句话 ──
        #   旧代码对着一份**从没生成过**的产物说「生成它的 commit 无从得知」，
        #   那是在陈述一件不成立的事，而且是**方向相反**的静默失效：
        #   不是把好产物丢掉，而是让人去找一个从不存在的东西（第 123 层的另一侧）。
        if not bare and not rp.exists():
            problems.append(f"产物路径 `{rp}` **不存在** ⇒ 这是**产物缺失**，"
                            f"与「产物在、但没盖章」是两件事（分不清是哪一件就照实说）")
        elif not bare:
            problems.append("没有溯源 sidecar——生成它的 commit 无从得知")
    elif not st.get("code_sha"):
        problems.append("溯源章里没有 code_sha")
    if not gen and not (bare and bare_gen):
        problems.append(f"不在 DEFAULT_GENERATORS 里，不登记来源就无法判定"
                        f"（在 {SELF_PATH} 的 DEFAULT_GENERATORS 补一行）")
    # 🚨🚨 第 123 层：**生成器路径不存在时，旧代码判 `fresh`。**
    #   实测（2026-10-15）：`freshness(f, "scripts/wf_weights_backtest.py")` ——
    #   那个文件**根本不存在**（真名是 `backtest_walk_forward_weights.py`），
    #   而 `commits_touching` 对不存在的路径返回 `[]`，
    #   `code_file_sha256` 返回 `None` ⇒ 走查说「没有提交动过它」
    #   ⇒ **判 fresh**。脏工作区时 `trustworthy=False` 兜住了它，
    #   **但工作区一干净就是一次彻底的假 fresh**，也就是最贵的那个答案。
    #
    #   ⇒ 「查不到动过」与「查不到那个文件」必须分开。
    #     前者才是证据；后者是**什么都没查**。
    if gen and not (root / gen).is_file():
        problems.append(
            f"生成器路径 `{gen}` 在仓里**不存在** ⇒ 判据本身没跑"
            f"（多半是写错了名字，或它被改名/删除；"
            f"用 `resolve_generator({name})` 拿登记值重查）")
    if problems:
        return {"report": name, "verdict": "unknown", "generator": gen,
                "reason": "；".join(problems),
                "problems": problems, "trustworthy": False, "commits": []}

    # ★ 不变量兜底：C1 抑制了「不在 DEFAULT_GENERATORS 里」那条之后，
    #   「`problems` 为空 ⇒ `gen` 与 `st` 必然都有值」这条**旧有保证**就不再成立。
    #   旧代码靠它保证下面不会拿 `gen=None` 去走查、也不会拿 `st=None` 去 `.get()`；
    #   一旦破了，崩点落在 `None.get(...)` 上——**离病因很远**，
    #   报错说的是「NoneType 没有 get」，而病因是上面那行 `and not (...)`（第 131 层）。
    #   ⇒ 显式判一次。方向**保守**：只把「崩在半路」变成「报 unknown」。
    if gen is None or st is None:
        missing = "生成器" if gen is None else "溯源章"
        return {"report": name, "verdict": "unknown", "generator": gen,
                "reason": f"{missing}解析不到 ⇒ 无从判定（兜底，不是正常路径）",
                "problems": [f"{missing}解析不到"], "trustworthy": False,
                "commits": []}

    dirty = bool(st.get("code_dirty"))
    touched = commits_touching(root, gen, st["code_sha"])

    # ★ 字节判据**优先于** commit 走查，因为走查有两个**方向**的结构性盲区：
    #
    #   ① 假阳性：它只看 commit，看不见「生成时跑的是 A，事后才把 A 提交掉」。
    #      于是一个当前代码就能复现的产物被判 stale（2026-10-15 实测 k=5）。
    #   ② 假阴性：它也看不见「生成之后有人改了生成器但**没提交**」。
    #      那时 `touched` 为空 ⇒ 判 fresh ⇒ **一个假的全清信号**。这个更危险。
    #
    #   盖章时记下的内容哈希是**当场**的证据，不依赖之后的 git 历史，
    #   两个方向都堵得上。
    #
    # ⚠️ 边界要说清：字节一致**只证明生成器自己没变**，不证明它 import 的
    #   `aplus/factor.py` / `hurdle.py` / `store.py` 没变 ⇒ dirty 时仍然
    #   `trustworthy=False`。这里修的是判决，**不放松可信度**。
    stamped_bytes = st.get("code_file_sha256")
    now_bytes = code_file_sha256(root, gen) if gen else None
    if stamped_bytes and now_bytes:
        if stamped_bytes == now_bytes:
            return {"report": name, "verdict": "fresh", "generator": gen,
                    "reason": "生成器字节与盖章时完全一致" + (
                        "；但盖章时工作区有未提交改动 ⇒ 只能证明生成器本身，"
                        "不能证明整个依赖闭包" if dirty else ""),
                    "commits": [], "trustworthy": not dirty, "byte_evidence": True,
                    "stamped_sha": st["code_sha"], "stamped_at": st.get("stamped_at"),
                    "dirty_at_stamp": dirty, "code_file_sha256": stamped_bytes}
        return {"report": name, "verdict": "stale", "generator": gen,
                "reason": "生成器字节已变（内容哈希不同）" + (
                    f"；另有 {len(touched)} 次提交动过它" if touched else
                    "；★ 没有任何提交动过它 ⇒ 这是**未提交改动**，commit 走查看不见"),
                "commits": touched, "trustworthy": False, "byte_evidence": True,
                "stamped_sha": st["code_sha"], "stamped_at": st.get("stamped_at"),
                "dirty_at_stamp": dirty, "code_file_sha256": stamped_bytes,
                "bytes_match": False}

    # 无字节证据（旧 sidecar）⇒ 退回 commit 走查，行为与从前完全一致。
    if touched:
        return {"report": name, "verdict": "stale", "generator": gen,
                "reason": f"生成后动过 {gen} {len(touched)} 次",
                "commits": touched, "trustworthy": False,
                "stamped_sha": st["code_sha"], "stamped_at": st.get("stamped_at")}
    return {"report": name, "verdict": "fresh", "generator": gen,
            "reason": "生成后生成模块未改动（★ 无字节证据：这份 sidecar 是"
                      "`code_file_sha256` 落地之前盖的，只能靠 commit 走查）",
            "commits": [], "trustworthy": not dirty,
            "byte_evidence": False,
            "stamped_sha": st["code_sha"], "stamped_at": st.get("stamped_at"),
            "dirty_at_stamp": dirty}


def scan(reports_dir: Path | str | None = None,
         root: Path | str | None = None) -> list[dict]:
    """扫全目录，按严重程度排序：**能判坏的排前面，不能判的排后面**。

    排序不是装饰——`stale` 是**已知的坏**，`unknown` 是**不知道**，
    前者必须先看。只按文件名排序会把这两类混在一起，
    然后人只看前几行，最要命的那条就永远没被看到。
    """
    root = _root(root)
    # ⚠️ `reports_dir` 曾经默认 `"data/reports"` —— 同样吃 cwd：
    #   在仓外跑 `scan()` 会扫到**空目录**，返回 `[]`，
    #   而 `[]` 在 `summary()` 里读成「零 stale、零 unknown、全 fresh」。
    #   ⇒ 空与「全好」不可区分，和第 89 层同一族。
    reports_dir = _root(root) / "data/reports" if reports_dir is None else reports_dir
    out = []
    # 边车排除逻辑已下沉到 report_jsons()（本模块），避免出现第二个实现
    for p in report_jsons(reports_dir):
        out.append(freshness(p, root=root))
    order = {"stale": 0, "unknown": 1, "fresh": 2}
    out.sort(key=lambda r: (order.get(r["verdict"], 9), r["report"]))
    return out


def summary(reports_dir: Path | str | None = None,
            root: Path | str | None = None) -> dict:
    rows = scan(reports_dir, root)
    return {"n": len(rows),
            "stale": [r["report"] for r in rows if r["verdict"] == "stale"],
            "unknown": [r["report"] for r in rows if r["verdict"] == "unknown"],
            "fresh": [r["report"] for r in rows if r["verdict"] == "fresh"],
            "rows": rows}

# ── CLI ────────────────────────────────────────────────────────────

def _main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="报告新鲜度体检")
    ap.add_argument("--reports-dir", default="data/reports")
    ap.add_argument("--root", default=".")
    ap.add_argument("--json", action="store_true", help="输出机器可读")
    a = ap.parse_args(argv)
    s = summary(a.reports_dir, a.root)

    if a.json:
        print(json.dumps(s, ensure_ascii=False, indent=2))
    else:
        print(f"HEAD {code_sha(a.root)}"
              f"{'  **工作区有未提交改动**' if is_dirty(a.root) else ''}")
        print(f"共 {s['n']} 份报告："
              f"过期 {len(s['stale'])} · 无溯源 {len(s['unknown'])} · "
              f"新鲜 {len(s['fresh'])}\n")
        for r in s["rows"]:
            mark = {"stale": "🔴 过期", "unknown": "⚠️ 无溯源",
                    "fresh": "🟢 新鲜"}[r["verdict"]]
            print(f"{mark}  {r['report']}")
            if r["verdict"] == "stale":
                print(f"          {r['reason']}")
                for c in r["commits"][:5]:
                    print(f"            · {c}")
            elif r["verdict"] == "unknown":
                print(f"          {r['reason']}")
            elif r["verdict"] == "fresh":
                # ★ 新鲜也必须说清**依据**：字节证据 vs 只靠 commit 走查。
                # 2026-10-15 实测：12 份 sidecar 全都没有 `code_file_sha256`
                # （字段在它们盖章之后才落地），而 CLI 当时只印「🟢 新鲜」——
                # **两种强度差一个量级的绿，看起来一模一样。**
                print(f"          {r['reason']}  · 依据："
                      f"{'字节证据' if r.get('byte_evidence') else '⚠️ 仅 commit 走查'}")
    # **stale 非零，unknown 也非零。**
    # unknown 让 CI 变红看起来很烦，但「不知道」被当成「没事」才是真事故——
    # 2026-10-14 我就是这么引用了一份已经作废的报告。
    return 2 if s["stale"] else (3 if s["unknown"] else 0)


if __name__ == "__main__":
    raise SystemExit(_main())

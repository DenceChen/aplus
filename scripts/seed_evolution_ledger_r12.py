"""第十二轮（2026-10-14 11:10）灌四条：LLM 链换模型、强制思考的预算坑、研究员补执行轴、真链路冒烟。

幂等——标题已存在就跳过。重复运行第二次必 0 新增。

## 这一轮的性质：不是「找到了更强的信号」，而是「修好了发现信号的那根管子」

前十一轮的产出都落在**回测这一侧**：因子、闸门、样本外判决。今天这四条全部落在
**研究员这一侧**——即「谁在提出假设」的那一层。理由很直接：研究员管线一旦静默失效，
前面十一轮的改进全部失去增量，因为再也没有新假设进赛马，而台账会安静地停在
「better / better / better」上，看着像在进步。

顺序是因果的，不是并列的：

    第 4 条（冒烟）先暴露「没人用真链路验过」
        → 第 1 条（换 m3.1）第一次真打，发现 0 候选
            → 第 2 条（token 预算）根因
                → 第 3 条（执行轴）真链路通了之后才看得见的问题

所以第 4 条虽然排在最后入账，却是这一轮的**起点**：如果它早两天存在，
第 1、2 两条的 0 候选当天就会被抓出来，而不是等换模型时才撞上。

## 方向上有一条要单独说

四条全是 `better`，但**没有一条增加了 alpha**。它们增加的是「提出假设的能力」
与「发现故障的时机」。把这四条和「找到更强的因子」写进同一个台账是刻意的——
它们量纲不同，但同样可证伪：0 候选是可数的，轴数是可数的，真链路验过的环节数是可数的。

台账不允许出现「只有定性描述」的条目，所以下面每一条的 before/after
都选了**可数、可复现、且不靠作者判断**的量：装配数、预算 token、候选轴数、验证环节数。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aplus.ledger import ledger_path, load_ledger, record_improvement  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
D = "2026-10-14"

# ── 1. 模型路线 ────────────────────────────────────────────────────────────
# 为什么先记这条：它把「默认链里到底跑的是谁」从代码常量变成了 ~/.aplus/llm.json 的
# 一个字段。此前主备顺序写死在 `default_llm_chain()` 里，换模型要改源码——而 aplus
# 不在 git 版本控制下，改错了没有 diff 可查。
E1 = dict(
    title="LLM 链全量切到 MiniMax-M3.1-Flash-Preview：默认链装配 m31 主 / glm 备，M3 彻底移除",
    category="operations", severity="high",
    metric="默认 LLM 主链中启用强制思考 + prompt caching 降本的链路占比", higher_is_better=True,
    before=0.0, after=1.0,
    note=("新增 `aplus/llm.py::MiniMaxM31FlashClient`。它与既有 `MiniMaxClient`（M3）\n"
          "**不是同一个东西**，四个维度全不同：\n\n"
          "    项          M3                              M3.1-Flash-Preview\n"
          "    域名        api.minimaxi.com              api.minimax.cn\n"
          "    协议        OpenAI /text/chatcompletion_v2 Anthropic /v1/messages\n"
          "    思考        可控                            **强制、不可关闭**\n"
          "    缓存        无                              prompt caching\n\n"
          "选它的理由是成本结构而不是聪明程度：进化 prompt 几乎不变（事实是同一个\n"
          "evidence 模板），二次调用应命中 `cache_read_input_tokens`。M3 走 OpenAI\n"
          "兼容协议，拿不到这个降本通道。\n\n"
          "**M3（`MiniMaxClient`）从默认链完全移除**：用户硬指示「不要使用 m3，\n"
          "全部用 m3.1」。类保留代码供手动构造，`default_llm_chain()` 不再装配它——\n"
          "`tests/test_llm_chain.py::test_default_chain_m31_only_no_m3_in_chain`\n"
          "**故意在配置里同时放 M3 的 key**，断言链里不出现它。\n\n"
          "主备顺序改由 `~/.aplus/llm.json` 的 `primary` 字段决定（`m31-flash` / `glm`），\n"
          "**改配置不改代码**——这是这一条附带的真正改进。glm-failover（5 账号轮询，\n"
          "本机代理）始终兜底在最后一位。\n\n"
          "思考**从未关闭**：`thinking` 块只是不在应用层暴露（防 prompt 注入进日志），\n"
          "模型始终在思考，`usage.thinking_tokens` 可验证。把它当「关掉了思考」是误读。"),
    invalidates=["「默认链里跑的是 MiniMax-M3」——2026-10-14 起默认链只装配 M3.1-Flash-Preview"],
    evidence="aplus/llm.py::MiniMaxM31FlashClient / default_llm_chain；"
             "tests/test_llm_chain.py（5 条，含「M3 不得出现」的反向断言）",
)

# ── 2. token 预算坑 ────────────────────────────────────────────────────────
# 为什么单独成条：0 候选这个现象在旧模型上从不出现，M3 的思考是可关的。
# 这条记的不是「调了个参数」，是「换模型会静默改变输出契约的形状」这个坑本身。
E2 = dict(
    title="M3.1 强制思考吃光 max_tokens：首次接入返回 0 候选，2048 改 8192 + 529 指数退避",
    category="correctness", severity="high",
    metric="M3.1 强制思考模式下不被思考截断的输出预算上限（token）", higher_is_better=True,
    before=2048, after=8192,
    note=("换到 m3.1 后第一次打研究员：**返回 0 组候选**。\n\n"
          "根因不是 prompt 不好、不是协议解析错、不是 key 没配。Anthropic Messages 协议\n"
          "下 content 的**首项是 `thinking` 块**，而 m3.1 的思考不可关闭。\n"
          "`max_tokens=2048` 是**思考与正文共享**的预算——思考先花，\n"
          "轮到要吐 JSON 数组正文时已无余额，于是 text 块为空，`parse_candidates` 拿到\n"
          "空串，安静返回 `[]`。\n\n"
          "**这个坑的恶劣之处在于它不报错**：HTTP 200，链路 `last_served_by` 正常，\n"
          "日志无异常，只有「研究员这轮没提候选」这一句话——看起来像模型没灵感。\n\n"
          "修复两处：\n"
          "  ① `max_tokens` 默认 **8192**。依据是实测单组候选正文 200~400 token，\n"
          "     思考再贪也得给它留够。\n"
          "  ② minimaxi 端点偶发 **529 `overloaded_error`**（临时拥挤）：3 次指数退避\n"
          "     重试（1s/2s/4s）。**只对 529 重试**——其他 5xx/4xx 是真故障\n"
          "     （参数错、权限、配额），重试只会掩盖它。\n\n"
          "**教训要记住的不是 8192 这个数，是这条**：换协议/换模型后，输出预算的\n"
          "分配方式可能整个变掉，而失败形态是静默的 0 结果。这类假设必须用真实响应验，\n"
          "fake 客户端永远测不出来（见第 4 条）。"),
    invalidates=["「研究员返回 0 候选 = 模型没想出好假设」——2026-10-14 实测为 "
                 "max_tokens 被强制思考吃光，是预算问题不是能力问题"],
    evidence="aplus/llm.py::_extract_text / _is_overloaded（529 判定）；"
             "scripts/smoke_researcher.py（真链路复现）；data/reports/candidate_block_audit.json",
)

# ── 3. 执行参数轴 ──────────────────────────────────────────────────────────
# 为什么必须记：台账第 19/20 条已经记过「频率比权重更决定成败」（276 个百分点）。
# 但那两条说的是**内置候选池**已经支持频率。研究员这一侧当时仍是纯权重，
# 于是同一个已证事实没有传导到研究员——这是典型的「结论对了但没落到执行」。
E3 = dict(
    title="研究员候选缺执行参数轴：曾把「纯权重一律日频入场」写死，等于主动把候选送进成本地狱",
    category="methodology", severity="critical",
    metric="研究员候选形状中可表达的轴数（权重 / 调仓频率 / 缓冲）", higher_is_better=True,
    before=1, after=3,
    note=("`scripts/evolve_weekly.py` 里写死过一句注释：\n\n"
          "    # 研究员提的是**纯权重**，一律按日频 / 无缓冲入场。\n\n"
          "这句话在**主池**里是对的（内置候选本来就带频率），用在**研究员池**上是错的。\n\n"
          "台账第 19 条早已实测：2019-2023 独立验证（962 交易日，纯反转）\n\n"
          "    | K | 年成本 | 净       |\n"
          "    |---|--------|----------|\n"
          "    | 1 | 246.3%  | −168.9%  |\n"
          "    | 5 |  98.2%  |   −5.3%  |\n"
          "    |21 |  35.8%  |  +77.6%  |\n"
          "    |63 |  12.2%  | +105.7%  |\n\n"
          "**276 个百分点的差距全部来自这一个参数。** `ic_guided_race_v1.json` 里同权重\n"
          "`ic_sign_all` 的三档是同一件事的另一次印证：\n\n"
          "    K=1   超额 +0.01%   t=0.21   ← 按日频入场就是这个数\n"
          "    K=5   超额 +0.13%   t=3.34   ← 过闸\n\n"
          "所以「研究员提的是纯权重，一律按日频」= **亲手把可能过闸的候选按日频送进\n"
          "成本地狱**，测出来必然是超额 ≤ 0。研究员看到的反馈是「我的假设不行」，\n"
          "而真正的原因是入场方式。**这是一个会系统性筛掉好假设的 bug，\n"
          "而它不产生任何报错。**\n\n"
          "修复（`aplus/evolve_researcher.py`）：接受复合形状\n"
          "`{weights, rebalance_every, buffer_multiple}`，纯权重形状仍合法（= 日频）。\n\n"
          "两处刻意的取舍：\n"
          "  ① 形状判别复用 `evolve.split_weights`，**不重写第二份**——两处判别逻辑\n"
          "     迟早漂移，漂移后去重口径与主池就对不上，同一个候选会被跑两遍，\n"
          "     两次都进 Bonferroni 的比较次数，显著性门槛被自己抬高。\n"
          "  ② 去重键用 `evolve.candidate_key`（权重, 频率, 缓冲）三元组。只按权重去重\n"
          "     会把「日频 vs 季频」抹成同一个候选——而这正是那 276 个百分点所在。\n\n"
          "值域**锁死在实测网格** `EXECUTION_GRID=(1,5,21,63)` /\n"
          "`BUFFER_GRID={1:(None,3.0), 5:(None,3.0), 21:(None,), 63:(None,)}`。\n"
          "跑没测过的格子不是探索，是把一次赛马名额花在没人知道答案的格子上，\n"
          "还平白抬高比较次数。缓冲的实测依据也一并记下：K=1 缓冲把成本从\n"
          "246.3% 砍到 122.5%（**最有效**），K=63 从 12.2% 到 12.1%（完全无效）——\n"
          "日频恰恰最需要缓冲，它是成本占比最高的一格，不是最不需要的那格。\n\n"
          "prompt 里也显式要求「对每组候选显式给出 rebalance_every」，并说明\n"
          "只给权重就等于声明按日频入场。"),
    invalidates=["「研究员的候选形状是纯权重，日频入场是研究员链路的固有属性」"
                 "——2026-10-14 之前成立；频率是候选形状的一等公民，"
                 "已证事实（276 个百分点）此前未传导到研究员侧"],
    evidence="aplus/evolve_researcher.py（EXECUTION_GRID / BUFFER_GRID / validate_candidates）；"
             "tests/test_researcher_execution_axis.py（复合形状 / 越格拒绝 / 缓冲合法性）；"
             "data/reports/ic_guided_race_v1.json；data/reports/independent_validation_2019_2023.json",
)

# ── 4. 冒烟脚本 ────────────────────────────────────────────────────────────
# 为什么是最后一条却是起点：前三条的 0 候选正是靠它抓出来的。
# 这条的价值在于「以后不需要再靠运气发现」。
E4 = dict(
    title="新增研究员真实链路冒烟 scripts/smoke_researcher.py：此前全链路只有 fake 客户端验过",
    category="validation", severity="high",
    metric="研究员端到端链路中被真实模型验证过的环节数", higher_is_better=True,
    before=0, after=4,
    note=("接手研究员这一侧时的现状：`tests/` 下 `FakeClient` 双链路全绿，\n"
          "parse / validate 单元测试全绿——**但没有任何一个测试用真模型跑过一次**。\n\n"
          "fake 客户端的结构性盲区：它只验「我给它的东西能被解析」，不验\n"
          "「模型真的会照契约输出」。第 2 条的 0 候选正是这个盲区的直接产物——\n"
          "fake 客户端**永远不会**返回 thinking 块，也就不会发现 max_tokens 被吃光。\n\n"
          "`scripts/smoke_researcher.py` 打真 prompt、走真 `default_llm_chain()`，\n"
          "依次验四个环节：\n\n"
          "    ① 默认直连链能起来（主备装配正确、last_served_by 可观测）\n"
          "    ② 真实模型响应能进入 parse_candidates 的解析路径\n"
          "    ③ validate_candidates 能从**真响应**里挑出合法候选\n"
          "    ④ 整个链路对 prompt 的 JSON 数组契约守得住\n\n"
          "外加可选的 `claude -p` 子进程路径（子进程链路常挂，挂了不算 bug，\n"
          "但要打印出来——**静默降级比失败更可怕**）。\n\n"
          "**0 候选即非零退出**（`return 0 if raw else 1`）。这是唯一一条真正重要的\n"
          "设计：0 候选必须让 CI / 人看见。默认返回 0 的「跑完就算过」会把静默失效\n"
          "变成常态——而失效的管线不会自己报警，只会安静地停止产生增量。\n\n"
          "脚本**不写任何文件**：它是体检，不是数据生产。"),
    invalidates=["「研究员链路已被测试覆盖」——2026-10-14 之前成立的只是 fake 链路，"
                 "真模型响应的形状从未被任何测试观察过"],
    evidence="scripts/smoke_researcher.py；"
             "tests/test_llm_chain.py（链装配）；tests/test_evolve_researcher.py（解析单元）",
)


def main() -> int:
    # 每轮都重读一次而不是缓存：record_improvement 会重写整个文件，
    # 缓存的 len() 会在第二条之后开始说谎。标题判重是唯一的幂等依据——
    # 不按内容判重，因为 note 会随口径修正而改写，改内容就会重复入账。
    for entry in (E1, E2, E3, E4):
        ledger = load_ledger(ROOT / "data")
        if entry["title"] in {r.get("title") for r in ledger}:
            print(f"[跳过 ] {entry['title']}")
            continue
        before_n = len(ledger)
        row = record_improvement(ROOT / "data", dict(entry, date=D))
        after_n = len(load_ledger(ROOT / "data"))
        print(f"[{row['direction']:>6}] {entry['title']}\n"
              f"         {entry['metric']}: {entry['before']} → {entry['after']}"
              f"   （台账 {before_n} → {after_n}）")
    total = load_ledger(ROOT / "data")
    print(f"\n台账现有 {len(total)} 条 → {ledger_path(ROOT / 'data')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

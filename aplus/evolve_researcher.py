"""LLM 进化研究员：读证据 → 提候选权重假设。

定位：研究员不直接改系统——它产出的候选与内置候选一起进赛马，受同样的
护栏裁决（样本量/改进幅度/冷却期）。LLM 不可用时自动降级为只用内置候选。

## 候选形状：纯权重 **或** 权重 + 执行参数

两种形状都合法：

    {"mom20": 1.0, "rev5": 0.3}                                   ← 纯权重（日频）
    {"weights": {"mom20": -1.0}, "rebalance_every": 63}            ← 复合

## 为什么必须让研究员能提执行参数（2026-10-14 接手时发现）

执行参数不是细节，是**决定性的**。2019-2023 独立验证（962 交易日，纯反转）：

    | K | 年成本 | 净     |
    |---|--------|--------|
    | 1 | 246.3% | −168.9%|
    | 5 |  98.2% |   −5.3%|
    |21 |  35.8% |  +77.6%|
    |63 |  12.2% | +105.7%|

**276 个百分点的差距全部来自这一个参数。** 同一组权重按日频测出 −168.9%，
按季频测出 +105.7%——差 276 个点。

`ic_guided_race_7y.json` 里同权重的三档实测是同一件事的另一次印证：

    ic_sign_all  K=1  超额 +0.01%  t=0.21   ← 按日频入场就是这个数
    ic_sign_all  K=5  超额 +0.13%  t=3.34
    ic_sign_all  K=21 超额 +0.10%  t=3.23

而 `evolve_weekly.py` 曾经写死「研究员提的是纯权重，一律按日频入场」——
**那等于亲手把可能过闸的候选按日频送进成本地狱**，
测出来必然是「超额 ≤ 0」，然后研究者看到的是「我的假设不行」，
而真正的原因是入场方式。

**执行参数只允许取实测网格内的值**（`EXECUTION_GRID` / `BUFFER_GRID`，
与 `evolve_weekly.py` 的内置候选同一套）。跑没测过的格子不是探索，是浪费。
"""
import json
import re

from aplus.evolve import DEFAULT_REBALANCE_EVERY, candidate_key, split_weights

LEGAL_FACTORS = ("mom20", "rev5", "vol20", "vsur", "turn20")
WEIGHT_BOUND = 3.0

# 调仓频率网格——与 `evolve_weekly.REBALANCE_GRID` 同源（K=1/5/21/63）。
# 不允许研究员提议 7、11 这类没测过的值。
EXECUTION_GRID = (1, 5, 21, 63)

# 缓冲区实测（同一批回测，纯反转，2019-2023）：
#
#   K=1  成本 246.3% → 122.5%（砍掉一半，**最有效**）
#   K=5  成本  98.2% →  78.1%
#   K=21 成本  35.8% →  35.4%（可忽略）
#   K=63 成本  12.2% →  12.1%（完全无效）
#
# 与 `evolve_weekly.BUFFER_GRID` 逐格同源：K=1/5 跑两个格子，K≥21 只跑无缓冲。
# 日频恰恰最需要缓冲——它是成本占比最高的一格，不是最不需要的那格。
BUFFER_GRID = {1: (None, 3.0), 5: (None, 3.0), 21: (None,), 63: (None,)}

_EXAMPLE = ('[{"weights": {"mom20": -1.0, "rev5": 1.0}, "rebalance_every": 21},'
            ' {"mom20": 1.0, "rev5": 0.3, "vol20": 0.5, "vsur": 0.5}]')


def build_research_prompt(evidence: dict) -> str:
    w = evidence["current_weights"]
    s = evidence["baseline_stats"]
    grid_desc = "、".join(
        f"K={k}" + ("（可加 buffer_multiple=3.0）" if 3.0 in BUFFER_GRID.get(k, ())
                    else "（不可加缓冲）")
        for k in EXECUTION_GRID)
    lines = [
        "你是 aplus 量化平台的策略进化研究员。以下是平台真实回测证据，请基于证据（不是直觉）",
        "提出 1-3 组新的因子权重候选，目标是提高涨组合超额收益（首要）与方向命中率（次要）。",
        f"只允许这些因子：{', '.join(LEGAL_FACTORS)}；每个权重范围 [-3, 3]；",
        "",
        "## 执行参数（**这一轴与权重一样决定成败**）",
        "同一组权重在 2019-2023 独立验证（962 交易日）里，净值随频率天翻地覆：",
        "日频 −168.9%／周频 −5.3%／月频 +77.6%／季频 +105.7%——"
        "**276 个百分点的差距全部来自这一个参数，权重一个都没变**。",
        f"可选调仓频率（K 个交易日调一次仓）：{grid_desc}",
        "两组候选形状都合法：",
        '  纯权重（默认日频）：{"mom20": 1.0, "rev5": 0.3}',
        '  带执行参数：{"weights": {"mom20": -1.0}, "rebalance_every": 21}',
        "**请对每组候选显式给出 rebalance_every**——只给权重就是声明按日频入场。",
        "日频的代价是可量化的：7 年 97 组赛马测试段里，日频候选的成本占毛收益 "
        "**94.9%**、周频 28.2%、月频 12.1%；同一组权重日频测出超额 +0.008%（t=0.21，"
        "不过闸），周频 +0.134%（t=3.34，过闸）。",
        "",
        "简述每组候选的依据（哪条证据支持它），最后单独输出一行 JSON 数组（可用代码块包裹）：",
        f"格式如 {_EXAMPLE}",
        "",
        "## 现任权重（baseline）",
        json.dumps(w, ensure_ascii=False),
        "## 现任回测表现",
        f"总命中 {s['overall_hit_rate']:.1%}，涨命中 {s['hit_rate_up']:.1%}，"
        f"跌命中 {s['hit_rate_down']:.1%}，涨组合超额 {s['up_excess_vs_market']:+.2%}"
        f"（{s['judged']} 条判定）",
    ]
    challenger = evidence.get("best_challenger")
    if challenger:
        lines += [
            "## 上期最强挑战者（未晋级或已晋级均注明）",
            json.dumps(challenger["weights"], ensure_ascii=False),
            f"其表现：总命中 {challenger['stats']['overall_hit_rate']:.1%}，"
            f"涨超额 {challenger['stats']['up_excess_vs_market']:+.2%}",
        ]
    if evidence.get("market_summary"):
        lines += ["## 市场环境", evidence["market_summary"]]
    return "\n".join(lines)


def parse_candidates(llm_reply: str) -> list:
    """从回复中提取 JSON 数组候选（容忍代码块/前后噪声），失败返回空。"""
    if not llm_reply:
        return []
    text = None
    fenced = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", llm_reply, re.S)
    if fenced:
        text = fenced.group(1)
    else:
        bare = re.search(r"\[.*\]", llm_reply, re.S)
        if bare:
            text = bare.group(0)
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [c for c in data if isinstance(c, dict)]


def validate_candidates(raw: list) -> list:
    """按平台协议校验候选：剔未知因子、剔越界权重、剔非法执行参数。

    ## 两种形状

    | 形状 | 输入 | 输出 |
    |---|---|---|
    | 纯权重 | `{"mom20": 1.0}` | 原样（默认日频、无缓冲） |
    | 复合 | `{"weights": {...}, "rebalance_every": 21}` | 补全 `buffer_multiple: None` |

    形状判别交给 `evolve.split_weights`（判据是「有没有装着 dict 的 `weights`
    键」，因子名永远不可能叫 `weights`）——**不重写第二份判别逻辑**，
    否则两处迟早漂移，去重口径与主池就对不上了。

    去重键用 `evolve.candidate_key`：`(权重, 频率, 缓冲)` 三元组。
    只按权重去重会把「日频 vs 月频」抹成同一个候选，
    而这正是 276 个百分点的差异所在。

    ## 执行参数必须落在实测网格里

    `rebalance_every ∉ EXECUTION_GRID` 或 `buffer_multiple ∉ BUFFER_GRID[K]`
    的候选整体剔除：跑没测过的格子不是探索，是把一次赛马名额花在
    一个没人知道答案的格子上，还平白抬高 Bonferroni 的比较次数。
    """
    seen = set()
    out = []
    for cand in raw:
        if not isinstance(cand, dict):
            continue
        try:
            w, every, buf = split_weights(cand)
        except Exception:  # noqa: BLE001 — 形状非法/缓冲越界的候选直接丢
            continue

        clean = {k: float(v) for k, v in w.items()
                 if k in LEGAL_FACTORS
                 and isinstance(v, (int, float)) and not isinstance(v, bool)}
        if not clean:
            continue
        if any(abs(v) > WEIGHT_BOUND for v in clean.values()):
            continue

        if isinstance(cand.get("weights"), dict):
            k = int(every)
            if k not in EXECUTION_GRID or buf not in BUFFER_GRID.get(k, (None,)):
                continue
        else:
            k, buf = DEFAULT_REBALANCE_EVERY, None

        key = candidate_key({"weights": clean, "rebalance_every": k,
                             "buffer_multiple": buf})
        if key in seen:
            continue
        seen.add(key)

        if isinstance(cand.get("weights"), dict):
            out.append({"weights": clean, "rebalance_every": k,
                        "buffer_multiple": buf})
        else:
            out.append(clean)
    return out


def research_candidates(evidence: dict, claude_runner=None, direct=None) -> list:
    """进化研究员编排：claude -p（能读文件、带技能栈）优先，直连链兜底。

    两级输出都经 parse + validate——LLM 是假设来源，不是决策者；
    一切候选仍需赛马与护栏裁决。
    """
    prompt = build_research_prompt(evidence)
    if evidence.get("log_path"):
        prompt += f"\n\n完整进化历史可自行读取：{evidence['log_path']}"

    if claude_runner is not None:
        try:
            cands = validate_candidates(parse_candidates(claude_runner(prompt)))
            if cands:
                return cands
        except Exception:  # noqa: BLE001 — claude -p 任何故障都降级直连
            pass

    if direct is not None:
        try:
            reply = direct.chat([{"role": "user", "content": "基于以上证据提出候选权重。"}], system=prompt)
            return validate_candidates(parse_candidates(reply))
        except Exception:  # noqa: BLE001
            return []
    return []

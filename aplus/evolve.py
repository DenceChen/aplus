"""自进化：回测驱动的权重晋级（带防过拟合护栏）。

目标函数（可解释）：objective = 100 × 涨组合超额 + 总命中率
  —— 超额是 alpha 的直接度量（1% 日超额记 1 分），命中率是方向能力的底线度量。

晋级三道闸（全部通过才换权重）：
  1. 冷却期：距上次晋级 ≥ cooldown_days，防止高频漂移追噪声
  2. 样本量：挑战者判定数 ≥ min_judged，小样本优势一律不采信
  3. 改进幅度：目标分提升 ≥ min_improvement，边际改进不换（换权有成本）
"""
import json
from dataclasses import dataclass
from datetime import date as _date
from pathlib import Path

from .factor import DEFAULT_WEIGHTS


@dataclass
class PromotionResult:
    promoted: bool
    new_weights: dict | None = None
    reason: str = ""


# ── 候选的形状（2026-10-11 20:40）────────────────────────────────
# 候选必须同时带**权重**与**执行参数**：
#
#     {"mom20": -1.0}                                        ← 历史形状：日频、无缓冲
#     {"weights": {"mom20": -1.0},
#      "rebalance_every": 21, "buffer_multiple": 3.0}        ← 复合形状
#
# 为什么非拆不可：独立验证（2019-2023，962 日）给出同一组权重 `{mom20:-1.0}`
#
#     日频净 −168.9%   周频 −5.3%   月频 +77.6%   季频 +105.7%
#
# **276 个百分点的差距全部来自两个执行参数。** 候选池里只有权重时，
# 赛马永远在「已知不可行的执行方式」下比较信号强弱，
# 挑出来的权重再送进那个不可行的执行方式——测得再准也落不了地。
#
# 纯权重 dict 必须继续能用：历史脚本、回测、台账全是这个形状。

DEFAULT_REBALANCE_EVERY = 1


def split_weights(candidate) -> tuple[dict, int, float | None]:
    """把候选拆成 `(权重, 调仓频率, 缓冲区倍数)`。

    两种形状都接受，判据是**是否存在一个装着 dict 的 `weights` 键**——
    因子名永远不可能叫 `weights`，所以这个判据不会与纯权重 dict 混淆。

    ## 缓冲默认不得静默开启

    不写 `buffer_multiple` 就是**没有缓冲**，返回 `None` 而非 `BUFFER_MULTIPLE`。
    缓冲把换手从 16.91% 压到 0.57%：默认开启会让**所有**历史结论在无人察觉的
    情况下被改写，且改写方向一致（全部变好看）。

    `buffer_multiple < 1` 直接报错：宽带比 `top_n` 还窄意味着「宁可留着
    排名更差的」，这几乎必然是把 3.0 写成了 0.3。`buffer_hold` 会把它
    算成 `max(round(top_n * 0.3), 1) = 3`，安静地变成另一个策略。
    """
    if not isinstance(candidate, dict):
        raise TypeError(f"候选必须是 dict，收到 {type(candidate).__name__}")
    if isinstance(candidate.get("weights"), dict):
        w = dict(candidate["weights"])
        every = candidate.get("rebalance_every", DEFAULT_REBALANCE_EVERY)
        buf = candidate.get("buffer_multiple")
    else:
        w = dict(candidate)
        every, buf = DEFAULT_REBALANCE_EVERY, None

    if not w:
        raise ValueError(f"候选权重为空：{candidate!r}")

    try:
        every = int(every if every is not None else DEFAULT_REBALANCE_EVERY)
    except (TypeError, ValueError):
        raise ValueError(f"rebalance_every 必须是正整数，收到 {every!r}") from None
    if every < 1:
        raise ValueError(f"rebalance_every 至少为 1，收到 {every}")

    if buf is not None:
        buf = float(buf)
        if not buf >= 1.0:
            raise ValueError(
                f"buffer_multiple={buf} < 1：宽带比 top_n 还窄，"
                f"这不是缓冲区策略，是把 3.0 写成了 0.3")
    return w, every, buf


def candidate_key(candidate) -> tuple:
    """候选身份三元组：`(权重项, 调仓频率, 缓冲倍数)`。

    ## 去重与「谁才是现任」都必须用它，不能只比权重

    `{mom20:-1.0} 日频` 与 `{mom20:-1.0} 月频` 净值差 276 个百分点。
    只按权重去重，等于把**最关键的差异**抹掉；
    只按权重认现任，则月频的「现任权重副本」会冒充日频现任，
    在裁决里赢下它从未被授权的席位。

    权重值统一转 `float`：`{mom20: -1}` 与 `{mom20: -1.0}` 是同一个策略，
    缓冲统一 `round(..., 9)` 消掉浮点尾差。
    """
    w, every, buf = split_weights(candidate)
    return (tuple(sorted((k, float(v)) for k, v in w.items())), int(every),
            None if buf is None else round(float(buf), 9))


def dedupe_candidates(candidates) -> list:
    """按三元组去重，**保留首次出现的顺序**。"""
    seen, uniq = set(), []
    for c in candidates or []:
        k = candidate_key(c)
        if k in seen:
            continue
        seen.add(k)
        uniq.append(c)
    return uniq


def objective(evaluation) -> float:
    stats = evaluation["stats"]
    return 100.0 * stats.get("up_excess_vs_market", 0.0) + stats.get("overall_hit_rate", 0.0)


def maybe_promote(
    champion_eval,
    challenger_evals,
    last_promotion_date,
    today,
    min_judged: int = 400,
    min_improvement: float = 0.05,
    cooldown_days: int = 7,
) -> PromotionResult:
    best = max(challenger_evals, key=objective)
    if best["weights"] == champion_eval["weights"]:
        return PromotionResult(False, None, "现任仍是最优，不换")

    last = _date.fromisoformat(last_promotion_date)
    now = _date.fromisoformat(today)
    if (now - last).days < cooldown_days:
        return PromotionResult(
            False, None,
            f"冷却期内（上次晋级 {last_promotion_date}，需间隔 {cooldown_days} 天）；"
            f"最优挑战者目标分 {objective(best):.3f} 已记录进进化日志",
        )

    if best["stats"].get("judged", 0) < min_judged:
        return PromotionResult(
            False, None,
            f"样本量不足（{best['stats'].get('judged', 0)} < {min_judged}）——小样本优势不采信，防过拟合",
        )

    gain = objective(best) - objective(champion_eval)
    if gain < min_improvement:
        return PromotionResult(False, None, f"改进幅度 {gain:.3f} 未达门槛 {min_improvement}，不换")

    reason = (
        f"挑战者胜出：目标分 {objective(best):.3f} vs 现任 {objective(champion_eval):.3f}"
        f"（涨超额 {best['stats']['up_excess_vs_market']:+.2%} vs "
        f"{champion_eval['stats']['up_excess_vs_market']:+.2%}，"
        f"总命中 {best['stats']['overall_hit_rate']:.1%} vs "
        f"{champion_eval['stats']['overall_hit_rate']:.1%}，判定数 {best['stats']['judged']}）"
    )
    return PromotionResult(True, best["weights"], reason)


def _weights_path(reports_dir) -> Path:
    return Path(reports_dir) / "weights.json"


def save_weights(reports_dir, weights: dict, evidence: dict,
                 execution: dict | None = None) -> None:
    """落盘冠军权重。

    `execution` 是**执行参数**（调仓频率 / 缓冲区倍数），与 `weights` 分开存。
    分开是因为它们是两回事：权重是「选谁」，执行参数是「多久换一次」。
    合成一个 dict 存着的后果已经出现过——实盘只读 `weights`，
    于是「每 21 个交易日调一次仓」被静默丢弃，
    按日频全换执行，而回测净值是按月频算的。
    """
    path = _weights_path(reports_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    exec_cfg = dict(execution or {})
    path.write_text(
        json.dumps({"weights": weights,
                    "execution": {"rebalance_every": int(exec_cfg.get("rebalance_every", 1)),
                                  "buffer_multiple": exec_cfg.get("buffer_multiple")},
                    "evidence": evidence}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _yearly_evidence_sentence(reports_dir) -> str:
    """「逐年符号 N/N 一致」这句证据——**从 IC 报告算**，不许手写。

    ## 为什么它必须是算出来的

    这句话是 `DEFAULT_WEIGHTS` 符号翻转的全部说服力，而且**直接显示在
    概览页给用户看**。它此前是手写的常量「8 因子 × 8 年逐年符号 32/32 一致」，
    三处错：32/32 算术上不自洽（8×8 应为 64）；`turnratio` 实际只有 7 年
    （缺 2019，需要累积成交量历史）；真实数字是 **63/63**。信号本身没错，
    但报出来的是编的——而这正是整条证据链唯一给用户看的部分。

    手写的证据数字必然漂移。所以散文反查数据。

    ## 查不到就不说那句

    与 `weight_sign_conflicts` 同一纪律：`unknown=True` 时返回**空串**，
    让这句话整句缺席。写「逐年一致性未知」比编一个数字诚实，但比
    「符号已按实测 IC 校正」弱——后者仍然成立（`weight_sign_conflicts`
    单独核实），只是这条**更强的**证据此刻不可用。
    """
    try:
        from .factor_ic import yearly_sign_consistency
        got = yearly_sign_consistency(reports_dir)
    except Exception:                                   # noqa: BLE001
        return ""
    if got.get("unknown") or not got.get("total"):
        return ""
    gaps = {f: d["missing"] for f, d in (got.get("per_factor") or {}).items()
            if d.get("missing")}
    # 必须说清「哪个因子缺哪一年」。第一版这里只列了年份，
    # 生成出「（2019 因子缺记录）」——读起来像「2019 这个因子」，
    # 而实际是「turnratio 缺 2019 年」。缺口本身是事实，说错归属就变成了
    # 一个关于因子的假陈述。
    note = ("（" + "、".join(f"{f} 缺 {','.join(ys)} 年"
                             for f, ys in gaps.items())
            + "，已计入分母）") if gaps else ""
    return (f"（{got['agree']}/{got['total']} 个（因子×年）观测与该因子的"
            f"全段 IC 同号{note}；见 aplus/factor.py 的 DEFAULT_WEIGHTS 注释）")


def load_weights(reports_dir):
    path = _weights_path(reports_dir)
    if not path.exists():
        return {"weights": dict(DEFAULT_WEIGHTS), "evidence": None,
                "execution": {"rebalance_every": 1, "buffer_multiple": None},
                "source": "fallback",
                # `caveat_kind` 是给前端的**证据状态**，与 caveat 正文配套。
                # 前端据此查表定标题，不再硬编码——正文改写时标题不会说反话。
                #   "measured_bad" —— 已实测且结论为负。
                #   "unraced"     —— 从未赛马。
                # 2026-10-14 起回落路径是 "unraced"：默认权重符号刚按实测
                # IC 翻转，方向有证据但整组从未被赛马测过。
                "caveat_kind": "unraced",
                "caveat": (
                    "回落默认权重，**未经赛马验证**。符号已按实测 IC 校正"
                    + _yearly_evidence_sentence(reports_dir) +
                    "，但**幅度**沿用旧值未调，更没有任何样本外验收。"
                    "上一版默认权重 "
                    "{mom20:+1.0,rev5:+0.3,vol20:+0.5,vsur:+0.5} 在 2026-10-14 的 "
                    "730 天赛马里排第 81/87 名、测试段超额 −0.74%、t=−3.03 —— "
                    "那个数字属于**旧配置**，不能算到这一版头上。"
                    "当前状态是「方向有证据、效果未验证」，"
                    "不是「已验收的冠军」。日频执行它的年成本量级仍在数百个百分点。")}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {"weights": data.get("weights"), "evidence": data.get("evidence"),
            "execution": data.get("execution") or {"rebalance_every": 1,
                                                    "buffer_multiple": None},
            "source": "file", "caveat": None, "caveat_kind": None}


def live_execution_status(reports_dir) -> dict:
    """实盘**到底在执行什么**——以及它是不是 weights.json 声明的那个配置。

    ## 为什么必须有它

    实盘路径目前没有持仓状态机：`run_daily.py` 每天从零选一遍 Top10。
    这对 `rebalance_every=1` 的日频冠军是**如实执行**，没有任何问题。

    但一旦复合赛马晋级出带频率或缓冲区的冠军，实盘就会：

    - 照样每天出 10 涨 10 跌（看起来一切正常）
    - 按**日频全换**执行，而那个配置的净值是按月频算的
    - 独立验证（2019-2023，纯反转）：日频净 **−168.9%**、月频净 **+77.6%**

    **测的是月频，做的是日频，方向都反了，而没有任何地方会报错。**

    这道闸现在只回答「在不在执行声明的配置」，不做持仓状态机。
    持仓状态机接上之前，返回 `honored=False` 会让实盘打出醒目横幅，
    而不是默默按日频跑。

    ## 坏字段按「未知」处理，不按「日频」处理

    `execution` 字段损坏时回落到日频会让一切看起来正常。
    那恰恰是最坏的一种：**配置读不出来 = 按最贵的口径跑 = 静默亏钱。**
    """
    cfg = load_weights(reports_dir)
    raw = cfg.get("execution")
    every, buf, broken = 1, None, False

    # **没有记录**不等于「记录正确」（2026-10-14 修复）。
    #
    # `weights.json` 不存在时 `load_weights` 回落 `DEFAULT_WEIGHTS` @K=1，
    # 于是下面那段「K=1 且无缓冲 → 一切正常」的判定会成立，
    # `honored` 返回 True，理由写着「与 weights.json 声明一致」——
    # 可那个文件根本不存在。**系统在自己不知道自己跑什么的时候，
    # 报告自己一切正常。** 实测那天仓库里确实没有这个文件。
    if cfg.get("source") != "file":
        return {"honored": False, "rebalance_every": every, "buffer_multiple": buf,
                "reason": ("没有 weights.json —— 实盘正在执行**回落默认权重**"
                           f"（{cfg.get('caveat') or '未经测量的默认配置'}）。"
                           "这不是「已验证的冠军」，请先确认它是你要的配置")}

    if raw is None:
        every, buf = 1, None
    elif not isinstance(raw, dict):
        broken = True
    else:
        try:
            every = int(raw.get("rebalance_every", 1) or 1)
            buf = raw.get("buffer_multiple")
            if every < 1:
                broken = True
            if buf is not None:
                buf = float(buf)
                if buf < 1.0:
                    broken = True
        except (TypeError, ValueError):
            broken = True

    if broken:
        return {"honored": False, "rebalance_every": every, "buffer_multiple": buf,
                "reason": (f"weights.json 的 execution 字段无法解析（{raw!r}），"
                           f"已回落到日频。**声明的执行方式未知 ≠ 日频就是对的**"
                           f"——独立验证里日频净 −168.9%、月频 +77.6%")}

    missing = []
    if every > 1:
        missing.append(f"每 {every} 个交易日调一次仓（K={every}）")
    if buf is not None:
        missing.append(f"{buf} 倍缓冲区")
    if not missing:
        return {"honored": True, "rebalance_every": every, "buffer_multiple": buf,
                "reason": f"日频全换，无缓冲区（K=1）——与 weights.json 声明一致"}

    return {"honored": False, "rebalance_every": every, "buffer_multiple": buf,
            "reason": (f"⚠️ 权重文件声明的执行方式是「{'、'.join(missing)}」，"
                       f"但实盘没有持仓状态机，**正在按日频全换出单**。"
                       f"两者的年成本差约 10 倍（实测 3.5% vs 0.35%），"
                       f"独立验证里同一组权重日频净 −168.9%、月频 +77.6%。"
                       f"这份名单不能当作该配置的实绩")}

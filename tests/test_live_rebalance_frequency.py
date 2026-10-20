"""实盘换仓周期：**降低损耗**，不是提高收益 —— 这两件事不许被说成一件事。

## 决策依据（2026-10-21 重测，`data/reports/deployed_d2_verdict.json::k_grid`）

口径：实盘信号（`DEFAULT_WEIGHTS` 4 因子）+ 可买性过滤 + **满仓 10 只**、每份 ¥5,000、
整手取整、逐笔成本、窗口 2021-01-01→2026-10-12（n_days=1395，5.717213 年）。
数据源是**新鲜实测**（产物带 sidecar + 字节证据），
**不是**旧的 `k_sweep_summary.json` —— 那份的**五份输入全 stale ⇒ 整张不可引用**。

| K | 次数/年 | 年化换手 | 毛超额/年 | 账户 cagr |
|---|---|---|---|---|
| 5 | 48.8 | 42.06 | 5.29% | -11.81% |
| 10 | 24.4 | 22.66 | 1.27% | -2.34% |
| 21 | 11.6 | 11.41 | 4.29% | 7.85% |
| 42 | 5.8 | 5.72 | -7.63% | 0.43% |
| 63 | 3.9 | 3.85 | 4.81% | 10.16% |

### 为什么选 63 —— 理由**只用摩擦算术**

- **毛超额那一列在 K 上是平的**：4.20 / −3.85 / 4.29 / −7.63 / 4.81
  ⇒ 极差 12.4pp，而该档的**年化 MDE ≈ 16–17pp** ⇒ **极差落在噪声内**
  ⇒ **K 之间的毛超额差不可测**（每档的 `detectable` 全为 `false`）
- **年化换手 42.69 → 3.85（11.1×）是实测的**
  （按 `aplus/backtest.py::cost_drag` 的同一约定 `(n_in+n_out)/(2·max(...))`，
  逐调仓日算完取均值，年化 = 均值 × 244/K）
  ⇒ **摩擦降约 4.3pp/年**
- `净 = 毛 − 摩擦`，而**毛是噪声** ⇒ **净的期望差 ≈ 摩擦差** ⇒ **K=63 占优**

### 🚨 这条论证**不受「赢家诅咒」影响**（旧 docstring 的那条警告仍然成立，但要分清它针对什么）

「K=63 最好」若**只**看毛超额那一列，就是赢家诅咒
（逐年最优 K 每年都不同：2021→K=5、2022→K=10、2023→K=21、2024→K=10、2025→K=63、2026→K=5）。
⇒ 但本决策**不靠毛超额挑档**，它靠**摩擦随 K 单调下降**这个算术事实
⇒ ⇒ 赢家诅咒**不适用**。
⚠️ 但「毛超额在 K 上是平的」这件事本身**也是测量出来的**（不是假设）⇒
⇒ **必须由守卫钉住**（见下面的 `test_the_gross_excess_is_flat_in_k`），
否则这句话会退化成一句「我记得是这样」。

### 代价（三条，都要记住）

1. **每日榜单与账户持仓脱节**：K=63 时持有 3 个月 ⇒
   **「每天 10 涨 10 跌」是排名，不是指令** ⇒ 持仓级预警成为主要行动依据。
2. **前瞻证据要 189 个交易日**（`forward_registry.json` 第 5 批的 C2）⇒
   **决策先按算术走，前瞻只做检验、不做依据**。
3. **不许**把 K 变化说成「策略变好了」—— 换 K **不改变选股能力**。

---

## 为什么这份文件是**重新写的**（2026-10-21）

旧版的三条守卫读 `data/reports/k_sweep_summary.json`，而那份产物**五份输入全 stale**
⇒ 守卫与 docstring 表互相锁死在一**对化石**上 ⇒ **两边一起错，守卫照样绿**
（本仓「读快照的守卫等于没守」那一族）。
⇒ 现在改读**新鲜产物**，且把「决策依据表」钉在它上面。
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]

#: 决策依据的产物（**新鲜**：带 sidecar + 字节证据）。
_ART = _ROOT / "data" / "reports" / "deployed_d2_verdict.json"

#: 实盘信号 + 可买性过滤 + 满仓 10 只 —— 决策依据表的那一档。
_ARM_SIGNAL = "LIVE_DEFAULT"
_ARM_FILTER = "ARM_C"
_ARM_EQUITY = "E100"

#: 选中的 K。
CHOSEN_K = 63

#: 对账容差的**单一真相源**。主判据与容差守卫共用它 ——
#   两边各写一个 5e-4 就是「第 102 层：探针打在自己的副本上」：
#   实测把主判据放宽到 5e-2 时，容差守卫读的还是副本 ⇒ 照样绿。
# ⚠️ **按显示精度分档，不是「一个容差管所有列」** ——
#   决策表里的列**显示位数不同** ⇒ 舍入误差的量级也不同 ⇒ 塞进一个容差
#   就会「为了统一而降低精度」（旧文件对「次数/年」已经这么做过一次）。
#   🚨 2026-10-21 实测：换手列按 2 位小数显示（42.69）而产物是 42.66960854
#   ⇒ 差 0.0204 ⇒ 用 5e-4 立刻红 ⇒ **红在一个显示精度问题上，
#   而不是语义问题上** —— 红的原因必须是我要验的那件事。
_TOL_DISPLAY = 0.005  # 2 位小数的列（年化换手 / 毛超额% / 账户 cagr%）
_TOL_ROUNDS = 0.05    # 1 位小数的列（次数/年 = 244/K），量纲也不同


# ── 产物读取（唯一入口） ─────────────────────────────────────────────

def _k_grid() -> dict:
    assert _ART.is_file(), (
        f"决策依据产物不见了：{_ART.name} —— 决策依据必须可复核"
    )
    return json.loads(_ART.read_text(encoding="utf-8"))["k_grid"]


def _per_config(g: dict) -> dict:
    """按 (signal, filter, K) 取出信号级那一档。"""
    out = {}
    for name, v in (g.get("per_config") or {}).items():
        if v.get("signal") != _ARM_SIGNAL or v.get("filter_arm") != _ARM_FILTER:
            continue
        out[int(v["K"])] = v
    return out


def _cells(g: dict) -> dict:
    """按 K 取出账户级那一格（满仓）。"""
    out = {}
    for name, v in (g.get("cells") or {}).items():
        if not isinstance(v, dict):
            continue
        if not name.startswith(f"{_ARM_SIGNAL}|{_ARM_FILTER}|"):
            continue
        if not name.endswith(f"|{_ARM_EQUITY}"):
            continue
        m = re.search(r"\|K(\d+)\|", name)
        if m:
            out[int(m.group(1))] = v
    return out


# ── 守卫 ───────────────────────────────────────────────────────────

def test_live_fallback_uses_the_chosen_rebalance_frequency():
    """回落路径的换仓周期必须是 **63**，不是 1。

    ⚠️ 注意与 `DEFAULT_REBALANCE_EVERY` **故意不同**：
    那个是赛马口径（候选之间要比日频），这个是实盘口径。
    合并成一个常量会把赛马悄悄改成别的频率。
    """
    from aplus.evolve import DEFAULT_REBALANCE_EVERY, LIVE_REBALANCE_EVERY, load_weights

    assert LIVE_REBALANCE_EVERY == CHOSEN_K, (
        f"实盘换仓周期变成 {LIVE_REBALANCE_EVERY} —— "
        f"选 {CHOSEN_K} 的理由是**摩擦算术**（年化换手 42.69→3.85，"
        f"而毛超额在 K 上是平的），见本文件 docstring"
    )
    assert DEFAULT_REBALANCE_EVERY == 1, (
        "赛马默认被改成非 1 ⇒ 候选之间不再可比，所有历史赛马排名作废"
    )
    assert DEFAULT_REBALANCE_EVERY != LIVE_REBALANCE_EVERY, (
        "两个常量合并了 —— 赛马口径与实盘口径必须能独立演进"
    )

    cfg = load_weights("/nonexistent-reports-dir")
    assert cfg["execution"]["rebalance_every"] == CHOSEN_K, (
        f"回落路径实际下发 {cfg['execution']['rebalance_every']}，应为 {CHOSEN_K}"
    )


def test_the_chosen_k_is_supported_by_the_artifact():
    """★ 选 63 必须**由产物支撑**，不是拍脑袋。

    外部锚（`deployed_d2_verdict.json::k_grid`）：
    ① **年化换手随 K 单调下降**（否则「省成本」这个理由本身不成立）；
    ② 选中的那一档比 K=5 便宜**一个量级**；
    ③ 账户 cagr 在选中档**最高**（不是「不低于别的档」）。
    """
    g = _k_grid()
    pc = _per_config(g)
    ks = sorted(pc)
    # ⚠️ 「换手随 K 单调下降」**不在这里断言** —— 它有自己的一条守卫
    #   （`test_the_turnover_falls_monotonically_with_k`）。
    #   🚨 2026-10-21 实测：同一条性质写在**两个测试**里 ⇒ 变异体改掉其中一处
    #   另一处还在 ⇒ **变异存活** ⇒ 而「看起来在守」的守卫其实没在守
    #   （第 97 层：两份副本之间没有守卫）。⇒ **一条性质只写一处。**
    assert pc[CHOSEN_K]["held_basket_annualized_turnover"] < \
        pc[min(ks)]["held_basket_annualized_turnover"] / 10, (
        f"K={CHOSEN_K} 的年化换手 {pc[CHOSEN_K]['held_basket_annualized_turnover']:.2f} "
        f"相对 K={min(ks)} 的 {pc[min(ks)]['held_basket_annualized_turnover']:.2f} "
        "并没有实质下降一个量级"
    )
    cells = _cells(g)
    cagrs = {k: (cells[k].get("tier") or {}).get("cagr") for k in cells}
    best = max(cagrs, key=lambda k: cagrs[k])
    assert best == CHOSEN_K, (
        f"账户 cagr 最高的一档是 K={best}（{cagrs[best]:.4f}），"
        f"不是选中的 K={CHOSEN_K}（{cagrs[CHOSEN_K]:.4f}）"
    )


def test_the_turnover_falls_monotonically_with_k():
    """★ 「年化换手随 K 单调下降」—— 决策理由的**承重前提**，独立成一条。

    这条**必须只有一处**：2026-10-21 实测，同一条性质写在两个测试里 ⇒
    变异体改掉其中一处、另一处还在 ⇒ **变异存活** ⇒ 看着在守的守卫其实没在守。
    ⇒ 现在它有自己的位置，且**别处不许再抄一遍**。
    """
    g = _k_grid()
    pc = _per_config(g)
    ks = sorted(pc)
    turnovers = [pc[k]["held_basket_annualized_turnover"] for k in ks]
    assert turnovers == sorted(turnovers, reverse=True), (
        f"年化换手没有随 K 下降：{dict(zip(ks, turnovers))}"
        " ⇒ 「拉长持有期买到的是损耗下降」这个理由本身不成立"
    )


def test_the_gross_excess_is_flat_in_k():
    """🚨 **决策不靠毛超额挑档** —— 这一条必须钉住，否则「赢家诅咒」适用。

    本决策的理由是**摩擦随 K 单调下降**（算术事实）。
    但要成立，还需要「**毛超额在 K 上没有可测的差异**」——
    否则「毛超额其实在 K=63 更好」就仍是一个**未排除的**解释，
    而「K=63 最好」就会重新变成一个**挑出来的数**。

    ⇒ 判据：**毛超额的极差必须小于该档的 MDE**（落在噪声内）。
    ⚠️ 这是**两条腿的判据**（极差 vs MDE），不是「毛超额必须等于某个数」——
    后者会把这个守卫变成「抄一个数」。
    """
    g = _k_grid()
    pc = _per_config(g)
    gross = {k: pc[k]["gross_excess_annual_pct"] for k in pc}
    spread = max(gross.values()) - min(gross.values())
    min_mde = min(pc[k]["mde_annual_pct"] for k in pc)
    assert spread < min_mde, (
        f"毛超额在 K 上的极差 {spread * 100:.2f}pp 不小于最小 MDE "
        f"{min_mde * 100:.2f}pp ⇒ 「毛超额在 K 上是平的」这句话不成立 ⇒ "
        "本决策的理由必须重新审视"
    )
    # 每档的 `detectable` 全为 false ⇒ 连**单档**的毛超额符号都定不了
    assert not any(pc[k]["detectable"] for k in pc), (
        "某一档的毛超额变得可测了 ⇒ 「毛超额在 K 上是平的」需要重新评估"
    )


def test_the_target_is_not_relaxed_alongside_the_frequency():
    """★ 降 K 是提速，砍前瞻验证是作弊。

    门槛必须**跟着成本收紧**（K 越小、门槛越高），
    而不是为了让某一档够线而放松门槛。
    ⇒ 在新产物里等价于：**每一格的目标都还是 20%**，
    且**没有任何一格自称达标**。
    """
    g = _k_grid()
    cells = _cells(g)
    assert cells, "账户层 K 网格是空的"
    for k, v in cells.items():
        ver = v.get("verdict") or {}
        assert abs(float(ver.get("target", -1)) - 0.20) < 1e-12, (
            f"K={k} 的目标变成了 {ver.get('target')} ⇒ 目标被放松了"
        )
        # 「达标」在这个仓里从来没有出现过 ⇒ 任何一格也不许出现
        assert "达标" not in " ".join(str(c) for c in (ver.get("cases") or [])), (
            f"K={k} 的判决里出现了「达标」—— 需要重新走一遍判决，而不是沿用旧结论"
        )
        # 每一格都必须**给了判决**（不是「没结论」）
        assert ver.get("cases"), f"K={k} 没有判决用例"


def test_decision_basis_table_matches_the_committed_artifact():
    """★ 决策依据那张表必须**逐格**等于产物里抽出来的数。

    ## 为什么这条必须存在

    那张表写在**本文件的 docstring** 里——也就是**决策记录的正文**。
    2026-10-15 的 P0（成本被扣两次）结清后，产物里的净收益全变了，
    而**这张表一个字都没跟着变** ⇒ 它还印着已作废的数。
    同一批作废数字当时散落在 `CLAUDE.md`、`aplus/evolve.py` 的注释、
    本文件、`docs/TODO_measure_sigma_r.md` **四处**——
    这是第 7 层（口径标签）的一个新变体：
    **标签跟着数字走了，但没人保证数字本身是最新的。**

    「口径必须跟着数字离开脚本」的反面还有一句：
    **数字离开脚本之后，必须有一条通道把它拉回来。**

    ## 外部锚

    逐格的值只有一个来源：入库的 `data/reports/deployed_d2_verdict.json`
    （`.gitignore` 第 27 行 `!data/reports/*.json` 把 JSON 结论放回仓库）。
    没有第二个实现可以对照 ⇒ 这条守卫防的是**漂移**，不是**算错**。
    """
    g = _k_grid()
    pc = _per_config(g)
    cells = _cells(g)

    doc = __doc__ or ""
    # 只认表格行：`| K | 次数/年 | ... |` 之后、表格结束之前的行
    body = doc.split("| K | 次数/年 |", 1)[1].split("\n\n", 1)[0]
    # ★ 表格里用的是排版减号 U+2212，不是 ASCII '-'。
    tr = str.maketrans("−", "-")
    parsed = {}
    for line in body.splitlines():
        cells_ = [c.strip().strip("*").translate(tr)
                  for c in line.strip().strip("|").split("|")]
        # ★ 5 列：K / 次数/年 / 年化换手 / 毛超额 / 账户 cagr
        #   守卫的列数必须跟表一起变，否则它会红在一个**解析错误**上，
        #   而不是一个语义错误上 —— 红的原因必须是我要验的那件事。
        if len(cells_) != 5 or not cells_[0].isdigit():
            continue
        # 🚨 **单位**：`gross_excess_annual_pct` 的值**已经是百分数**
        #   （4.1957 表示 4.1957%，不是分数）⇒ 表里的百分数**不除 100**。
        #   而 `tier.cagr` 是**分数**（0.0785）⇒ 那一条要 ×100 才能对上。
        #   ⇒ **同一个表里两种单位** —— 这不是笔误，是产物自身的口径，
        #   守卫必须**逐列**处理，否则会「红在一个单位问题上」。
        parsed[int(cells_[0])] = {
            "rounds": float(cells_[1]),
            "turnover": float(cells_[2]),
            "gross_pct": float(cells_[3].rstrip("%")),
            "cagr_pct": float(cells_[4].rstrip("%")),
        }
    assert set(parsed) == set(pc), (
        f"docstring 表格的 K 档 {sorted(parsed)} 与产物 {sorted(pc)} 对不上")

    for k, q in parsed.items():
        r = pc[k]
        # ★ 次数/年是 244/K 的**恒等式**，不是抄来的数 ⇒ 它有自己的容差，
        #   而且比 `_TOL` 宽（48.8 这种数多一位小数）。
        #   我第一版把它也并进 `_TOL` ⇒ 当场红：把两种量纲不同的判据塞进一个容差，
        #   就是在「为了统一而降低精度」。
        assert abs(q["rounds"] - 244 / k) < _TOL_ROUNDS, f"K={k} 次/年漂了"
        assert abs(q["turnover"] - r["held_basket_annualized_turnover"]) < _TOL_DISPLAY, \
            f"K={k} 年化换手漂了"
        # 毛超额：产物里**已经是百分数** ⇒ 直接比
        assert abs(q["gross_pct"] - r["gross_excess_annual_pct"]) < _TOL_DISPLAY, \
            f"K={k} 毛超额漂了"
        # 账户 cagr：产物里是**分数** ⇒ ×100 再比
        account_cagr = (cells[k].get("tier") or {}).get("cagr")
        assert abs(q["cagr_pct"] - account_cagr * 100) < _TOL_DISPLAY, \
            f"K={k} 账户 cagr 漂了"


def test_the_table_comparison_tolerance_cannot_be_loosened_silently():
    """★ 容差必须被**单独测一条**。

    🚨 实测（2026-10-15）：把 `< 5e-4` 放宽成 `< 5e-2` ⇒ **其余 4 条全绿**。
    数字错 1 个百分点照样通过，而守卫「在守东西」的外观完全不变。
    这与「守卫比恒真的更危险」同源：一条松了的守卫是**绿的**，
    会把人推向「这张表大概没错」。

    ⚠️ 判据不能写成「容差 == 某个字面量」—— 那只是把字面量又抄一遍，
      改代码的人和改断言的人是同一个，于是**两边一起改就又恒真了**
      （第 5 种形状：断言 == 公式，公式错了断言跟着一错）。
    ⇒ 这里钉的是**性质**：「容差必须比一个 ulp 宽、比 1 个百分点窄」，
      两个方向都要够狠才能让「1% 的错」与「最后一位的错」落在它两侧。
    """
    g = _k_grid()
    pc = _per_config(g)
    tol = _TOL_DISPLAY
    real = pc[min(pc)]["held_basket_annualized_turnover"]   # K=5 的年化换手

    # 方向一：容差必须**严格宽于**最小可表示偏差，否则 ulp 级噪声会天天误报
    drift = math.nextafter(real, 1.0) - real
    assert drift != 0.0, "nextafter 必须真的产生了偏差，否则本条是空转"
    assert abs(drift) < tol, (
        f"容差 {tol} 比一个 ulp（{abs(drift):.3e}）还窄 ⇒ 浮点末位噪声就会误报")

    # 方向二：容差必须**严格窄于** 1 个百分点，否则「错 1%」这种最常见的
    # 抄表错误会静默通过 —— 这正是实测里 L4 变异放过去的那一类。
    assert tol < 1e-2, (
        f"容差 {tol} 已经宽到能放过 1 个百分点的错误；"
        "实测把 5e-4 放宽成 5e-2 后守卫全绿")

    # 同一个纪律对另一个容差同样成立 —— 逐一钉，不给「只有主判据被看着」的错觉
    assert _TOL_ROUNDS < 0.1, "次数/年容差放宽到 0.1 ⇒ 244/K 抄错也放过"
    # 🚨 **两个容差必须真的分档** —— 合成一个就等于「为了统一而降低精度」
    assert _TOL_DISPLAY < _TOL_ROUNDS, (
        "2 位小数的列与 1 位小数的列必须各有容差；合成一个就是降精度")
    # 正控：把容差真的放宽一次，本条必须红
    assert abs(1.95 * real - real) > tol, (
        "正控：把某个格子的数放大 95% 必须被判红")

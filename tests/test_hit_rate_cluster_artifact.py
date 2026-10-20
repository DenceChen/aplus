"""守卫（**产物层**）：落盘的 `hit_rate_diagnosis.json` 必须真的是聚类口径。

**预注册**：`docs/PREREG_HIT_RATE_CLUSTER_2026-10-16.md` §5 G6

## 为什么必须与源码层守卫**分文件**

产物层守卫读的是**上一次落盘的 JSON**，源码怎么改它都看不见；
而产物新鲜度会因为**任何**源码变异而红 ⇒ 两者混进同一个文件，
等于给**每一条**变异发免费信用（`CLAUDE.md` 第 157 层的纪律）。
源码层那半在 `test_hit_rate_cluster_statistic.py`。

## 这一层能抓而源码层抓不到什么

源码层守卫验的是「函数在合成夹具上算出什么」；这一层验的是
**读者真正会读到的那份文件**。两者之间隔着落盘、序列化与一次真实运行 ——
产物一旦不同步，源码层**全绿**。
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

# 🚨 对账守卫必须与**生产者**读**同一个口径**的样本。
from aplus.signal_scope import scoped_ledger_frame  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ART = ROOT / "data/reports/hit_rate_diagnosis.json"
NULL_RATE = 0.5

# 与落盘精度一致（半宽落 6 位）。⚠️ 写成推导式而不是经验值：
#   精度变了守卫要自己变松；写死的 rel_tol 会让它悄悄开始接受一条偏 1e-3 的产物。
QUANTUM = 1e-6


def _ratio_tol(*values: float) -> float:
    return sum(QUANTUM / v for v in values)


def _artifact() -> dict:
    assert ART.is_file(), f"产物不存在：{ART}（先真跑 scripts/diagnose_hit_rate.py）"
    return json.loads(ART.read_text(encoding="utf-8"))


def _blocks(a: dict) -> dict:
    """产物里所有带推断量的块。"""
    out = {"overall": a["overall"]}
    out.update({f"by_direction.{k}": v for k, v in a["by_direction"].items()})
    out.update({f"with_market.{k}": v for k, v in a.get("with_market", {}).items()})
    return out


#: `scripts/measure_portfolio_nav.py:180-181` 逐字写死的两个剔除原因。
#: 抄的是**键集**（契约面），不是**数值**（随底稿漂移的快照）。
_CONSUMER_EXCLUSION_KEYS = frozenset({"no_data", "flat"})


def _consumer_contract_violation(a: dict) -> str | None:
    """验消费者（`measure_portfolio_nav.py`）依赖的**契约面**；完好返回 `None`。

    抽成函数是为了让**对照臂能真的调用它** —— 若把断言留在测试体里，
    对照臂就只能再抄一份（第 73 层的副本搬进了测试）。
    """
    td = a.get("trading_days")
    if type(td) is not int or td <= 0:      # 🚨 `type is` 而非 isinstance：bool 是 int 的子类
        return f"trading_days 必须是正 int，实测 {td!r}（type={type(td).__name__}）"

    ex = a.get("excluded")
    if not isinstance(ex, dict):
        return f"excluded 必须是 dict，实测 {type(ex).__name__}"
    extra, miss = set(ex) - _CONSUMER_EXCLUSION_KEYS, _CONSUMER_EXCLUSION_KEYS - set(ex)
    if extra or miss:
        return (f"excluded 的键集与消费者不符：多出 {sorted(extra)}、缺 {sorted(miss)}"
                " ⇒ measure_portfolio_nav.py:180-181 只算这两个，多出来的会静默漏掉")
    for k, v in ex.items():
        if type(v) is not int or v < 0:
            return f"excluded[{k!r}] 必须是非负 int，实测 {v!r}"
    return None


# ══════════════ 两个口径必须在**落盘的那份**里并存 ══════════════

def test_every_block_in_the_landed_artifact_carries_both_calibers():
    a = _artifact()
    blocks = _blocks(a)
    assert len(blocks) >= 5, f"块数 {len(blocks)} 异常 ⇒ 产物结构变了，先查清再改守卫"
    for name, b in blocks.items():
        for k in ("ci95", "ci95_row_level", "ci95_caliber", "ci95_row_level_caliber",
                  "half_width", "half_width_row_level"):
            assert k in b, f"{name} 缺 {k} ⇒ 产物是**旧格式**，没重跑"
        assert b["ci95_row_level_caliber"] != b["ci95_caliber"], f"{name} 两个标注相同"
        assert "虚增" in b["ci95_row_level_caliber"], \
            f"{name} 行级口径没标「被窗口叠加虚增」"
        assert b["half_width"] > b["half_width_row_level"], (
            f"{name} 聚类半宽 {b['half_width']} 没有大于行级半宽 "
            f"{b['half_width_row_level']} ⇒ 这份产物不是按成交窗口算的")


# ══════════════ 端点判据：合法集合由**真值源 + 落盘精度**推导 ══════════════
#
# 🚨 手调的容差在没有推导时会同时犯两个错（第 112 / 136 层）：
#   太窄 ⇒ 守现实中不会发生的方向，把人推向「改本来正确的产物」；
#   太宽 ⇒ 变成 2 倍误差的假绿，而**它看起来一直绿**。
# ⇒ 下面三个常数全部由 `scripts/diagnose_hit_rate.py` 的 `round(·, N)` 逐字推导。
_Q_RATE = 1e-4   # `rate`（`:254`）与两个 `ci95`（`:206`/`:230`）都落 4 位
_Q_HW = 1e-6     # `half_width`（`:209`）与 `half_width_row_level`（`:279`）都落 6 位

#: 端点的合法偏差 = **半宽的显示误差**，即 `round(hw_exact, 6)` 相对 `hw_exact`
#: 的最大偏移（半个末位）。
#:
#: ★ **两个口径同值，而这不是偷懒** —— 它们的误差结构逐项相同：
#: 点估计 `p = hits/n`（`_two_sided_p` `:133` 与 `_cluster_fields` `:194` 逐字同源）
#: ⇒ **无显示误差**；半宽都只落 **6 位** ⇒ 误差 ≤ `_Q_HW/2`。
#:
#: 🚨 **我第一版断言「簇口径残差恒为 0、容差 5e-7，行级需要 1.005e-4」，
#: 而那个断言的证据是「实测五个块的残差都是 0」—— 它被本块自己证伪了**：
#: `with_market.逆大盘` 的 `ci95` 上界真值 `0.8411500526315789` 距 4 位网格中点
#: `0.84115` 只有 **5.26e-8** < 5e-7 ⇒ 落盘的 `0.8411` 与回代的 `0.8412` **都对**
#: （同一块的 `ci95_row_level` 上界真值 `0.4936500526315789` 同因）。
#: ⇒ 「实测若干点成立」不构成「恒成立」（第 137 层），而**据此写下的守卫会过严**。
#: 两处同因的原因是 `p = 21/57 = 7/19`，小数部分恒为 `…0526315789…`。
_TOL_ENDPOINT = _Q_HW / 2


def _grid_index(v: float) -> int:
    """4 位小数落盘值 → 整数格号。

    ⚠️ **为什么绕这一圈**：两个相邻的 4 位网格点，其**差**在浮点上不一定恰为 `_Q_RATE`。
    实测 `0.0558 − 0.0557 == 0.00010000000000000286 > 1e-4` ⇒
    `math.isclose(0.0557, 0.0558, abs_tol=1e-4)` 为**假**
    ⇒ 「差整整一格」在差值比较里表现为「**不等**」，于是**越界半格**会假红，
    而真正的容差语义（一个带 ±5e-7 不确定度的真值）无处安放。
    （加法方向反而是精确的：`0.0557 + 1e-4 == 0.0558` 逐位为真 ——
    所以这个坑只在**减法**上出现，写注释时若照抄成加法就是一句假话。）

    换算到整数格号把比较变成**整数**比较，两个轴（差值方向、累加方向）都不再参与。
    """
    return round(v / _Q_RATE)


def _legal_grid_span(x: float, tol: float = _TOL_ENDPOINT) -> range:
    """端点真值 `x` 经 `tol` 扰动后，落盘值**允许**落在哪几格。

    产物内部算的是 `round(p ± hw_exact, 4)`，而守卫手里只有
    `round(hw_exact, 6)` ⇒ 端点真值带 ±`_TOL_ENDPOINT` 的不确定度
    ⇒ 合法落盘值是 `round(x ± tol, 4)`，**至多两格**（因为 `tol < _Q_RATE`）。

    ★ **这不是「差 ≤ 容差」**，而是「必须是这几个格之一」——
    `abs_tol=1e-4` 会把**整整一格**的任何错误一起放过去（第 137 层：
    容差类判据的判别力只能来自能力守卫，而这个写法把 20 个端点里的 20 个
    都变成了「差一格照样绿」）。
    """
    return range(_grid_index(x - tol), _grid_index(x + tol) + 1)


def _p_exact(b: dict) -> float:
    """产物内部那个**未舍入**的点估计 —— `ci95` 的真身。

    🚨 落盘的 `rate` 是 `round(p, 4)`（`scripts/diagnose_hit_rate.py:254`），
    而 `ci95` 出自**全精度 `p`**（`:194` `p = hits / n` → `:202` `lo = max(0, p − hw)`
    → `:206` `round(lo, 4)`）⇒ **拿 `rate` 回代等于用显示精度冒充内部精度**，
    差 ≤ 5e-5；而 `lo` 距离 4 位舍入边界不足 4e-5 时就整格翻掉。

    ★ **2026-10-16 实测**（这条红是守卫的错，不是产物的错）：
    `by_direction.down` 的 `p = 31/57 = 0.5438596491…`、`hw = 0.4881111640…`
    ⇒ `lo = 0.05574…` ⇒ `round(…,4) = 0.0557` = **产物真值**；
    而用落盘 `rate = 0.5439` 回代得 `0.0558` ⇒ **差整一个末位单位**。

    🚨 **旧判据 `abs_tol = 1e-4` 在这个差上**两侧都错**，方向相反：
    ① **过严**：实测 `0.0558 − 0.0557 == 0.00010000000000000286 > 1e-4`
       ⇒ `math.isclose(..., abs_tol=1e-4)` 为**假** ⇒ 一份**完全正确**的产物被判红；
    ② **过松**：这同一行**也**说明容差就卡在「差一格」的边上，
       换成 `0.0559`（两格）它照样绿 ⇒ 它守着的是「误差 < 2 格」，
       而**它想守的是「误差 ≤ 0 格」**（第 137 层：容差判据的判别力
       只能来自能力守卫，「实测通过」对它零信息量）。
    ⇒ 一个字面量同时犯两个错，是因为**两个错各自需要一个不同的抽象**：
    ① 要求的是「逐位相等」，② 要求的是「只能落在这几格里」。

    ⇒ 处置是**换锚 + 换抽象**：锚从 `rate` 换到 `hits/n`（病根），
    判据从 `abs_tol` 换到「合法落盘值集合的成员资格」（上半条过严）；
    两者都做才闭合，**只改一个会把过严变成恒真**
    （第 112 层：过严与恒真同害，两者都会把人推向改本来正确的东西）。
    """
    # 承重墙（第 143 层）：缺项必须 KeyError 崩掉，不许回落成「用 rate 顶替」。
    # 悄悄回落 = 把上面那条错口径又请回来，而报错文案会指向别处。
    return b["hits"] / b["n"]


def test_the_landed_point_estimate_is_the_one_the_intervals_were_built_from():
    """🚨 `rate` 必须是**同一个** `p` 的显示精度，且**当前至少有一处不是恒等**。

    第一句钉住同源（`rate == round(p, 4)`），第二句钉住**这条守卫锚的不是同一个数**。

    🚨 第二句是承重的，不是装饰：若哪天 `p` 恰好能被 `round(p,4)` 表示，
    `_p_exact(b)` 与 `b["rate"]` 就**逐位相等** ⇒ 主守卫的合法集合退化成
    「用 `rate` 自己算的那一格」⇒ 「用显示精度冒充内部精度」这个缺陷
    被整个吃掉，而守卫**恒绿且看不出来**（第 146 层：「口径一致」≠「恰好落进缝里」）。

    ⇒ 判据问的是「我锚的两个量真的不同吗」，而不是「它们这次算出来一样吗」。

    ★ 2026-10-16 实测：当前产物上**不需要**这条兜底 —— 把主守卫的锚换回
    `b["rate"]`，新判据在 20 个端点里抓出 **4 处**
    （`by_direction.down.ci95[0]` / `by_direction.up.ci95[1]` /
    `with_market.顺大盘.ci95[0]` / `with_market.顺大盘.ci95_row_level[1]`），
    而旧的 `abs_tol=1e-4` 抓出 **0 处**。⇒ 这句话现在守的是**未来那种形态**
    （`p` 恰好可被 4 位精确表示），不是当前这道红。
    """
    saw_differs = False
    for name, b in _blocks(_artifact()).items():
        p = _p_exact(b)
        assert b["rate"] == round(p, 4), (
            f"{name}: 落盘 rate {b['rate']} ≠ round(hits/n, 4) {round(p, 4)} "
            "⇒ rate 与 ci95 不是同一个点估计的两种显示")
        if b["rate"] != p:
            saw_differs = True
    assert saw_differs, (
        "全部块的 `hits/n` 都能被 round(·,4) 精确表示 ⇒ 全精度 p 与落盘 rate "
        "逐位相同 ⇒ 主守卫的容差会吃掉「显示精度冒充内部精度」这个缺陷，"
        "它现在恒绿。**这是夹具/产物形态变了，不是守卫变严了。**")


def _endpoint_matches(x: float, landed: float, tol: float = _TOL_ENDPOINT) -> bool:
    """落盘端点 `landed` 是否是端点真值 `x` 的**合法显示**。

    ★ **唯一实现** —— 主守卫与下面三条能力守卫全部调它。
      两处各写一遍的话，能力守卫验的就不是主守卫在用的那个判据
      （本仓第 137 层：能力守卫量的是「判据有牙」，判据换实现它就变成空气）。
    """
    return _grid_index(landed) in _legal_grid_span(x, tol)


def _legal_str(x: float, tol: float = _TOL_ENDPOINT) -> str:
    span = _legal_grid_span(x, tol)
    return "{" + ", ".join(f"{i * _Q_RATE:.4f}" for i in span) + "}"


def _endpoints_of(name: str, b: dict) -> list[tuple[str, float, float]]:
    """把一个块摊成 `(标签, 端点真值, 落盘值)` 列表（截断与守卫同款）。"""
    p = _p_exact(b)
    return [
        ("ci95[0]", max(0.0, p - b["half_width"]), b["ci95"][0]),
        ("ci95[1]", min(1.0, p + b["half_width"]), b["ci95"][1]),
        ("ci95_row_level[0]", max(0.0, p - b["half_width_row_level"]), b["ci95_row_level"][0]),
        ("ci95_row_level[1]", min(1.0, p + b["half_width_row_level"]), b["ci95_row_level"][1]),
    ]


def test_every_landed_interval_is_exactly_its_own_point_estimate_plus_its_half_width():
    """🚨 `ci95` 与 `half_width` 必须在产物里**互为对账**。

    这条是被一次变异逼出来的：把 `ci95` 换成行级那一个、`half_width` 留在聚类值上，
    守卫**全绿** —— 因为上面两条读的是 `half_width`，而读者读的是 `ci95`。
    ⇒ 「两个字段都在」不等于「它们说的是同一件事」。

    截断规则写在这里：区间必须落在 [0,1]，且 `ci95_clipped` 必须如实说它截没截。

    ★ 点估计锚在 `_p_exact(b)`（`hits/n`）而**不是** `b["rate"]` —— 理由与实测
    见 `_p_exact` 的 docstring。

    ★ 判据形式是**合法集合的成员资格**（`_endpoint_matches`），不是 `abs_tol`：
      端点真值只带 ±`_TOL_ENDPOINT` 的不确定度，而「合法落盘值」是一张**至多两格**的清单。
      `abs_tol = 1e-4` 等于把**整整一格**的任何错误一起放过去 ——
      2026-10-16 实测：拿落盘 `rate` 回代（显示精度冒充内部精度）时
      新判据抓出 **4 处**，而 `abs_tol=1e-4` 抓出 **0 处**。
    """
    for name, b in _blocks(_artifact()).items():
        for ci, hw, clipped_key in (
                ("ci95", "half_width", "ci95_clipped"),
                ("ci95_row_level", "half_width_row_level", None)):
            if b[ci] is None:
                assert b[hw] is None, f"{name}: {ci} 是 None 而 {hw} 不是 ⇒ 半个口径"
                continue
            assert hw is not None, name
            p = _p_exact(b)
            lo = max(0.0, p - b[hw])
            hi = min(1.0, p + b[hw])
            for side, x, landed in (("下界", lo, b[ci][0]), ("上界", hi, b[ci][1])):
                assert _endpoint_matches(x, landed), (
                    f"{name}.{ci} {side} {landed:.4f} 不是端点真值 {x!r} 的合法显示"
                    f"（合法 {_legal_str(x)}；真值距 4 位网格中点 "
                    f"{abs(x * 2 - round(x * 2)) / 2:.3e}）")
            if clipped_key is not None:
                assert b[clipped_key] is bool(lo != p - b[hw]
                                              or hi != p + b[hw]), \
                    f"{name}.{clipped_key} 与实际截断情况不符"
                assert b[ci][1] <= 1.0 and b[ci][0] >= 0.0, f"{name}.{ci} 越出 [0,1]"


def test_the_endpoint_tolerance_cannot_span_more_than_two_grid_cells():
    """🚨 守的是**容差本身** —— 合法集合必须窄于「一个显示量子」。

    `_TOL_ENDPOINT < _Q_RATE` 是**结构性**的（由真值源推出，不看数据）：
    端点真值的全部不确定度就是半宽那 6 位的半个末位，而 4 位小数的相邻格相距 `_Q_RATE`
    ⇒ 合法集合至多含 2 格 ⇒ 判据不是「差多少」，是「是不是那一格」。

    🚨 **旧版本守的是「簇口径容差 < 行级容差」，而那个前提已被证伪**：
    实测 `with_market.逆大盘` 的**簇**上界真值 `0.8411500526315789` 距网格中点
    `0.84115` 只有 5.26e-8 < 5e-7 ⇒ 落盘的 `0.8411` 与回代的 `0.8412` **都是合法的**
    ⇒ 两个口径的残差由**同一个机制**支配 ⇒ 同容差。
    而按旧守卫去「修」它，唯一的路径是**把推导改成手调的宽值** ——
    那正是第 112 层（过严守卫把人推向改本来正确的东西）的教科书形态。
    """
    assert _TOL_ENDPOINT < _Q_RATE, (
        f"端点容差 {_TOL_ENDPOINT} 已经 ≥ 一个显示量子 {_Q_RATE} "
        "⇒ 合法集合不再窄于「两格」⇒ 对账退化成 `abs_tol ≈ 2×显示误差` 的假绿")


def test_most_landed_endpoints_still_demand_exactly_one_grid_cell():
    """🚨 反退化：`tol < _Q_RATE` 只是**上界**，真判别力是**实际集合有多窄**。

    只钉 `_TOL_ENDPOINT < _Q_RATE` 挡不住「有人把它调到 `0.9 × _Q_RATE`」——
    那仍然过上一条，而**每个端点的合法集合都变成两格** ⇒ 判据悄悄退化成
    `abs_tol ≈ 1.8e-4`，比它声称守着的显示误差宽 180 倍。

    ⇒ 这条钉的是**实测形状**：绝大多数端点只允许一格（要求逐位相等）。
    2026-10-16 实测 20 个端点里 **18 个** `n=1`、**2 个** `n=2`，
    那 2 个同在 `with_market.逆大盘` 且同因（`p = 21/57 = 7/19` ⇒ 小数部分恒为 `…0526…`）。
    门槛取 80%：**它是一条退化探测器，不是判决** —— 真掉到 50% 时它喊，
    掉到 70% 时它不喊但报告仍可被读，**不许把它当「唯一防线」**。
    """
    spans = []
    for name, b in _blocks(_artifact()).items():
        if b["ci95"] is None:
            continue
        for label, x, _landed in _endpoints_of(name, b):
            spans.append(len(_legal_grid_span(x)))
    assert spans, "🚨 一个端点都没量到 ⇒ 产物形态变了，本守卫已失效（不是守卫变严了）"
    one_cell = sum(1 for n in spans if n == 1)
    assert one_cell / len(spans) >= 0.80, (
        f"只有 {one_cell}/{len(spans)} 个端点只允许一格 ⇒ 判据已退化成容差比较；"
        f"集合大小分布 {sorted(set(spans))} ⇒ 检查 _TOL_ENDPOINT 与 _Q_HW 是否被改过")


def test_one_grid_cell_of_error_is_rejected_far_from_a_boundary():
    """🚨 能力守卫（正向）：**距网格中点远**的端点，注入一格误差必须被拒。

    第 137 层：**容差类判据的判别力只能来自合成夹具**，「实测通过」对它零信息量 ——
    真实偏差 0.95pp vs 判决间隙 12.51pp 时，`bias/slack` 改成 `bias*slack` 照样全绿。
    ⇒ 只有构造出「离容差边界恰一个量子」的缺陷，才知道判据还咬得动。

    真值取 `0.5`（**恰在格点上**，距中点 `0.5e-4`，是容差的 200 倍）
    ⇒ 合法集合必须**只有一格**；`0.5001` 是整整一格 ⇒ 必须被拒。
    """
    x, ok, bad = 0.5, 0.5, 0.5001
    assert len(_legal_grid_span(x)) == 1, (
        f"真值 {x} 距最近的网格中点 5e-5 ≫ 容差 {_TOL_ENDPOINT}，"
        f"合法集合应当只有一格，实测 {len(_legal_grid_span(x))} 格")
    assert _endpoint_matches(x, ok), "正控失败：合法的落盘值被拒 ⇒ 判据过严"
    assert not _endpoint_matches(x, bad), (
        "🚨 一格误差被放过了 ⇒ 判据退化成 `abs_tol ≥ 1e-4`（本守卫存在之前就是这样）")


def test_both_neighbours_are_legal_exactly_within_half_a_quantum_of_a_boundary():
    """🚨 能力守卫（反向正控）：真值**恰好落在网格中点**上时，两侧都必须合法。

    为什么必须有这条：上一条钉「远端点拒一格」，若有人把 `_TOL_ENDPOINT` 直接
    归零（`round` 的边界情形本来就该同分），上一条照样绿而**本条会红** ——
    于是两个方向都被钉住，判据不能被单边削。

    机理见 `_TOL_ENDPOINT` 的推导：`with_market.逆大盘` 的上界真值
    `0.8411500526315789` 距中点 5.26e-8，`round` 的结果**取决于 6 位半宽的显示误差**
    ⇒ 两侧都是产物可能落下的值 ⇒ **判据必须承认**（这正是旧守卫红的原因）。
    """
    x = 0.50005  # 4 位网格的中点
    assert _endpoint_matches(x, 0.5000), (
        "中点真值的两侧都必须合法 ⇒ 若这条红，说明容差被削到 0，"
        "而真实产物 `with_market.逆大盘` 会当场判红（那是产物对、守卫错）")
    assert _endpoint_matches(x, 0.5001), "同上，另一侧"
    assert not _endpoint_matches(x, 0.5002), "两格之外必须仍然被拒（否则容差失控）"


def test_the_landed_difference_between_calibers_is_purely_the_window_stacking():
    """产物里两个口径的差必须**只**来自 `n → W`。

    ⇒ 代数恒等式。它不需要知道任何正确答案，只需要「除了聚类什么都没变」。
    """
    for name, b in _blocks(_artifact()).items():
        if b["half_width"] is None:
            continue
        ratio = b["half_width"] / b["half_width_row_level"]
        assert math.isclose(ratio, math.sqrt(b["n"] / b["n_windows"]),
                            rel_tol=_ratio_tol(b["half_width"], b["half_width_row_level"])), (
            f"{name} 半宽比 {ratio:.6f} ≠ sqrt(N/W)={math.sqrt(b['n'] / b['n_windows']):.6f}")


def test_the_window_count_in_the_landed_artifact_is_a_measurement_not_a_typed_number():
    """🚨 对账守卫：从**真实底稿**重数一遍成交窗口，看它等不等于产物里的 `n_windows`。

    上面三条都验「产物内部自洽」，而一份自洽的产物完全可以把
    `n_windows` 写成行数、把 `windows` 编成每行一个 ——
    那样三个字段**互相**都对得上，而独立观测数是错的（整条修复的核心）。

    ⚠️ 底稿 parquet **不入库**（`.gitignore` 只放 JSON 回去）⇒ 缺它时 skip。
      这是本仓既有做法（`test_diagnose_hit_rate.py` 同款），
      但 skip 与「通过」在门禁输出里长得一样 ⇒ 这里把 skip 的理由写进说明。
    """
    import pandas as pd

    ver = ROOT / "data/reports/verification.parquet"
    if not ver.is_file():
        pytest.skip("verification.parquet 尚未生成（它不入库）⇒ 无法对账成交窗口数")
    a = _artifact()
    df = scoped_ledger_frame(ROOT / "data" / "reports", ver)
    m = df[df["result"].isin(["hit", "miss"])]
    # ⚠️ 只在底稿带 `exit_date` 时对账：旧行退回 `pred_date` 并加 `legacy:`
    #   前缀，分组规则见 `diagnose_hit_rate.py::_cluster_of`。
    key = m["exit_date"] if m["exit_date"].notna().any() else m["pred_date"]
    assert a["n_windows"] == int(key.nunique()), (
        f"产物说 {a['n_windows']} 个成交窗口，底稿数出来是 {int(key.nunique())} 个 "
        f"⇒ 窗口数不是量出来的，是写上去的")
    assert a["verdicts"] == len(m), "判定数与底稿不一致 ⇒ 产物比底稿旧"


def test_the_window_denominators_add_up_to_the_block_denominator():
    """逐窗口 `n` 之和必须等于块的 `n`。

    🚨 不等就说明窗口表与区间**不是同一批行算的** ——
      而两个数都在产物里，读者没有任何办法发现。
    """
    for name, b in _blocks(_artifact()).items():
        assert sum(w["n"] for w in b["windows"]) == b["n"], name
        assert sum(w["hits"] for w in b["windows"]) == b["hits"], name
        assert b["n_windows"] == len(b["windows"]), name


# ══════════════ 🚨 本缺陷本身：把「测不出」显示成「边缘显著」 ══════════════

def test_no_block_may_be_directional_while_its_point_sits_inside_one_mde():
    """「测不出方向」不许被任何一层区间显示成「有方向」。

    ⚠️ **如实标注：这条不是旧产物的原样形状。**
    旧 `up.ci95 = [0.3428, 0.6349]` **也跨过了 0.5** ——
    旧产物的错不是「判成了显著」，是**半宽窄了 3.87 倍**，
    读起来像「离 50% 只差 15 个点」而实际是「差 ±57 个点都定不了」。
    那条由上面两条（半宽比恒等式 + ci95/half_width 对账）守着。

    这里守的是**同族的下一格**：若某天底稿变多、点估计真的远离 50%，
    但 `gap_over_mde < 1`（80% 检出力下测不到），区间仍然**不许**排除 0.5。
    它今天近乎恒真 ⇒ 记在这里是为了不让它变成一个「看起来在守」的装饰。
    """
    for name, b in _blocks(_artifact()).items():
        if b["gap_over_mde"] is None or b["ci95"] is None:
            continue
        if b["gap_over_mde"] < 1.0:
            assert b["ci95"][0] < NULL_RATE < b["ci95"][1], (
                f"{name} 离 50% 只有 {b['gap_over_mde']} 个 MDE，"
                f"区间却是 {b['ci95']}（排除 0.5）⇒ 把「测不出」显示成了「有方向」")


def test_every_point_estimate_is_printed_beside_its_mde():
    """MDE 必须与点估计同块落盘（第 16/160 层：不许只说「不显著」）。"""
    for name, b in _blocks(_artifact()).items():
        assert "mde_rate_points" in b and "abs_gap_to_50pct" in b, name
        assert b["measurement_note"], f"{name} 没有 measurement_note"
        if b["gap_over_mde"] is not None and b["gap_over_mde"] < 1.0:
            assert "连符号都定不了" in b["measurement_note"], (
                f"{name} 测不到却没写「连符号都定不了」⇒ 把『测不出』写成了『没有』")


def test_the_row_level_label_is_false_exactly_when_no_window_stacking_happened():
    """反控的一半：恒 `True` 的标注是装饰。

    ⇒ 真产物里若有一块 `n_windows == n`，那个块的标注必须是 `False`。
      （今天的真产物每一块都叠加了，所以这条**今天近乎恒真** ——
      它守的是「实现不许把这个布尔写死」，不是今天的数。）
    """
    for name, b in _blocks(_artifact()).items():
        assert b["row_level_inflated_by_window_stacking"] == (b["n_windows"] < b["n"]), name
        if b["n_windows"] == b["n"]:
            assert math.isclose(b["inflation_factor"], 1.0, rel_tol=1e-9), name


# ══════════════ 措辞与兼容面 ══════════════

def test_no_value_in_the_landed_artifact_carries_pass_fail_wording():
    blob = json.dumps(_artifact(), ensure_ascii=False)
    for word in ("达标", "未达标", "过关", "及格"):
        assert word not in blob, f"产物正文出现交付式措辞「{word}」"


def test_the_consumer_contract_of_the_older_report_is_untouched():
    """`measure_portfolio_nav.py` 读 `trading_days` 与 `excluded`：
    前者参与不变式 `0 < n_days <= trading_days`，后者在 `:188` **逐字对账**
    （消费者的 `raise` 守着那一半）。

    这里守的是**键集与类型**这个契约面，**不**守数值 ——
    数值随底稿漂移（实测 `no_data` 已从 8 走到 1），钉它就是第 73 层的副本，
    而副本一红就只会被人原地更新，永远不消灭（预注册 §6.1/§6.2）。"""
    a = _artifact()
    why = _consumer_contract_violation(a)
    assert why is None, why
    assert a["verdicts"] > 0


# ══════════════ 契约面的探测器不许是装饰（第 112/117 层） ══════════════

def test_the_contract_checker_rejects_a_key_the_consumer_does_not_know():
    """正控 A：消费者 `:180-181` 只算 `no_data`/`flat` ⇒ 多出来的键会**静默漏掉**。"""
    a = dict(_artifact())
    a["excluded"] = {**a["excluded"], "halt": 2}
    assert _consumer_contract_violation(a) is not None


def test_the_contract_checker_rejects_a_stringified_count():
    """正控 B：`"1"` 与 `1` 混过去不会被任何人看见 ⇒ 类型必须被钉。"""
    a = dict(_artifact())
    a["excluded"] = {**a["excluded"], "no_data": str(a["excluded"]["no_data"])}
    assert _consumer_contract_violation(a) is not None


def test_the_contract_checker_accepts_a_different_but_legal_count():
    """🚨 反控 P6′：数值**必须**能变。
    若把 `no_data` 加 7 反而会红，说明这条守卫已经退回成快照守卫 ——
    那正是本次要消灭的缺陷（第 73 层），**绿才是守好**。"""
    a = dict(_artifact())
    a["excluded"] = {**a["excluded"], "no_data": a["excluded"]["no_data"] + 7}
    assert _consumer_contract_violation(a) is None


def test_the_contract_checker_rejects_a_boolean_trading_days():
    """正控 C：`isinstance(True, int)` 在 Python 里为真 ⇒ 旧断言对 `True` 放行。"""
    a = dict(_artifact())
    a["trading_days"] = True
    assert _consumer_contract_violation(a) is not None

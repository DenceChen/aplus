"""基准抽样种子扫描的**判据守卫**（2026-10-16）。

预注册 `docs/PREREG_BENCH_SEED_SWEEP_2026-10-16.md` §9 原本逐字写着
「🚨 **本预注册目前没有守卫**……本文件补的就是这一条欠账」——
🚨 **那句话已于 2026-10-16 从 §9 删掉**（因为欠账真的还了：判据已是模块级
函数，本文件 27 条守卫直接调用它们）⇒ **本段 docstring 曾经逐字引用一句
文件里已不存在的话，而没有任何机制会喊**（守卫不读那个 `.md`）。
⇒ 所以现在这里**只记事实、不逐字引用**：判据在**代码**里，不在散文里。

⚠️⚠️ **本文件守的是「代码」，不是「预注册文档」**（2026-10-16 实测）：
全仓对那个 `.md` 的引用共 3 处，**没有一处读它的内容** ——
`bench_seed_sweep.py:1` 是 docstring、`:335` 是产物里的路径字符串、
而这里曾经是逐字引用 ⇒ 🚨 **预注册的散文层至今零守卫**：
§3 两张清单若再次自相矛盾、§5 的门槛被悄悄改掉，**没有任何东西会喊**
（第 142 层：逐字引用是最强的锚，也是最容易过期的锚——它把两处散文绑死，
而只有一处在改）。⚠️ 欲守那层，要么加读它的守卫，要么把门槛搬进代码。

## 为什么每一条都在**直接调用判据**，而不是读落盘产物

`judge()` / `required_excess()` / `caliber_benchmark_fraction()`
此前正是埋在 `main()` 里（第 96 层）—— 嵌套函数守卫够不着，
**判决逻辑此前无任何东西能挡**。
⇒ 这里断言的对象是**模块级判据函数的行为**，不是 JSON 快照：
**读快照的守卫等于没守**（第 96 层）—— 产物一落盘，代码与产物的关系
就断了，除非每次对账；改代码而产物没重跑时，那类守卫**照样全绿**。

## 这份文件的形状是三件事逼出来的，不是设计出来的

### ① 能力守卫必须排在判决守卫之前

产出判决的函数，第一条测试必须证明**它能给出判决**，且能给出**非平凡**的那一支。
第 120 层：`_score_quartile_quartile` 首次实跑返回 `None`，而它的失败形态
与「真的测不出」**同形**，16 条形状守卫全绿。
⇒ 这里对 D1 / D2 / D4 各配「两支都必须可达」的合成夹具。

### ② 任何「恒等于真」的断言都必须配一条反向正控

「D4 说匹配」若只在匹配时测过，那它在**永远不匹配**的实现上也绿。
⇒ 每条恒真风险都配一条「不成立时必须给假」，且**不成立的那一侧要真的不成立**
（不是「换一个随机值碰碰运气」）。

### ③ 量纲换算不许有第二份副本（第 138 / 182 层）

`market_nav_caliber.json::equal_weight.geometric_annual_pct = 9.9471`
是**百分数**，而 `basket_nav_01…::tiers[0].mde_as_annual_pct = 0.20014`
是**小数** —— 字段名同为 `*_pct` 而**量纲相反**。
⚠️ 量纲守卫钉住了**生产者**，对**消费者零覆盖**：守卫全绿的那一天，
本扫描照样把 `9.9471` 当小数用，算出「基准年化 +994.71%」。
⇒ 换算**只许**在生产的 `caliber_benchmark_fraction()` 里发生一次；
  第五节那两条 AST 守卫守的就是「本文件里没有第二份」。
"""
from __future__ import annotations

import ast
import inspect
import math

import pytest

from scripts import bench_seed_sweep as M


# ── 合成夹具：不碰面板，判据函数全部是纯函数 ────────────────────────────
#
# ⚠️ 合成夹具必须**诚实**：若夹具里 `c_mkts` 全同，D4 的「跨 k 逐位非零」
#   分支就永远不可达，那条守卫会在**永远绿**的状态下以为自己有牙。
#   ⇒ 每个夹具都显式给出**分叉**的种子值（第 141 层）。

# 真实读数（只从产物**读字段**，不许用公式重算 —— 第 109 层）：
#   caliber equal_weight.geometric_annual_pct = 9.94710351459831（百分数）
#   经生产的 caliber_benchmark_fraction 换算 ⇒ 0.0994710351459831
_C_MKT_VERDICT_ARM = 0.0994710351459831
_T_TARGET = 0.2
_M_MKT_SEED42 = 0.20014094352558393


def _cmkts() -> dict:
    """`{k: c_mkt}`，**键必须是整数 k**（不是 c_mkt 本身）。

    🚨 第一版把键写成 `-0.031 / -0.017 / 0.003 / 0.019`（c_mkt 的值）⇒
      `d4_bitwise_reconciliation` 的 `c_mkt_by_k.get(0)` 取到 `None` ⇒
      `KeyError: 扫描里没有 k=0`，三条 D4 守卫一起红。
    ⇒ 而**红因不在守卫**：生产侧是 `c_by_k = {r["k"]: … for r in rows}`
      且 `for k in range(N_SEEDS)`（`scripts/bench_seed_sweep.py`）⇒
      键的**类型与取值域由生产定义**，夹具必须照抄。
    ⇒ 第 141 层：**红值落在被测量量的合法取值集合里时，先怀疑构造**。

    ★ **k=0 必须逐位等于 `_C_MKT_VERDICT_ARM`**：预注册 §4 说 `k=0` 是
      「基线复核」格，`d4_bitwise_reconciliation` 要拿它和产品逐位比 ⇒
      夹具不给对值，那条复核在夹具上结构上不可能通过。
    ⚠️ 曾写过两个互斥版本（一份说 `0.094711`、一份说 `0.099471`）——
      那是**两个手打常量副本之间没有锁**（第 137 层）。
      现在只有这一个真相源，任何测试要另一个差值都必须**从它推导**。
    """
    return {0: _C_MKT_VERDICT_ARM,   # 基线复核格：逐位等于产品读数
            1: 0.101002,
            2: 0.085530,
            3: 0.094711}
    # ⚠️ 上面四格**刻意互不相同** ⇒ 跨 k 的逐位差非零（预注册 §5 D4 第②行）；
    #   若某天把它们改成一组相同的数，`test_d4_flags_the_seed_as_ignored…`
    #   会绿成「种子被忽略」那条分支，而 D4 的「非零」分支永远不可达（第 141 层）。


# ══ 一、能力守卫：判决的两支都必须可达（第 120 层）══════════════════════

def test_d1_can_reach_both_branches():
    """D1 必须**真的**能给出 `False`，而不只是在成立时绿。

    🚨 只喂「全种都低于 target」的话，这条可以在「`holds` 写死
    `True`」的实现上永远绿 —— 而它守的是判决，不是实现。
    """
    below = M.d1_required_excess_is_positive_for_every_seed(_cmkts(), _T_TARGET, 5.7)
    assert below["holds"] is True, (
        f"c_mkt 全部 < target 时 D1 必须成立，实际 {below['holds']}")

    # 🚨 判别力前提：必须有**一个种子越过 target**。
    #   若这个夹具某天不再分叉，下面那条就会在退化数据上绿 ⇒
    #   把「夹具真的能触发另一支」本身写成断言（第 146 层：
    #   「判别力前提」必须被断言，否则夹具失效时会静默退化成装饰）。
    over = M.d1_required_excess_is_positive_for_every_seed(
        {0: 0.09, 1: 0.34, 2: 0.11}, _T_TARGET, 5.7)
    assert over["c_mkt_at_argmax"] == 0.34, "夹具已失效：argmax 不再是越过门槛那格"
    assert over["holds"] is False, (
        f"有种子 c_mkt=0.34 ≥ target=0.2 时 D1 必须不成立，"
        f"实际 {over['holds']} ⇒ 它守的是「读数好看」而不是「代数」")


def test_d4_can_actually_match_the_artifact_bitwise():
    """D4 的基线复核格必须**真的**能给出 `True`（能力守卫，不是形状守卫）。

    第 120 层：形状守卫在恒返 `None` / 恒返 `False` 的实现上全绿。
    ⇒ 这里断言的是**具体那一支为真**，不是「返回了 dict」。
    """
    got = M.d4_bitwise_reconciliation(_cmkts(), _C_MKT_VERDICT_ARM)
    assert got["k0_matches_artifact_bitwise"] is True, (
        f"c_mkt(0) 与产物读数逐位相同时 D4 必须判匹配，实际 "
        f"{got['k0_matches_artifact_bitwise']}（扫描 {got['k0_scan']!r} vs "
        f"产物 {got['k0_artifact_as_fraction']!r}）")


def test_d4_can_actually_report_a_mismatch():
    """反向正控：不匹配时**必须**给假。

    没有这条，上面那条在「`== True` 写死」的实现上永远绿。
    """
    got = M.d4_bitwise_reconciliation(_cmkts(), 0.5)
    assert got["k0_matches_artifact_bitwise"] is False, (
        f"c_mkt(0)={got['k0_scan']!r} 与产物读数 {got['k0_artifact_as_fraction']!r} "
        f"不等，D4 仍判匹配 ⇒ 它守的不是「基线复核」而是「恒真」")
    # 🚨 **不许手打这个差值**（第 137 层）：第一版写死 −0.4052889648540169，
    #   而那是 `_C_MKT_VERDICT_ARM = 0.0994710…` 的**另一个副本**
    #   ⇒ 它隐含 `c_mkt(0)=0.0947110351459831`，与上面那条测试要求的
    #     `_C_MKT_VERDICT_ARM` **互斥** ⇒ 两个测试对同一夹具提出矛盾要求。
    # ⇒ 正解：从**两个被测字段**相减 —— 这既不含字面量，
    #   又恰恰就是本条要证的命题（「差值是两者之差，不是重算出来的」）。
    assert got["k0_bitwise_diff"] == got["k0_scan"] - got["k0_artifact_as_fraction"], (
        f"差值必须逐位等于两个入参之差，实际 {got['k0_bitwise_diff']!r} ≠ "
        f"{got['k0_scan']!r} − {got['k0_artifact_as_fraction']!r} ⇒ "
        "差值是走了另一条换算路径算出来的")


def test_d2_can_actually_cross_the_mde_boundary():
    """D2 的 `seeds_with_ratio_ge_1` 必须**真的**能非空。

    否则「一个都没越过」这句话在分母被换成 1e-9 的实现上也照样成立。
    """
    got = M.d2_sign_determinable_against_the_seed42_mde(
        {0: 0.09, 1: 0.5, 2: 0.11}, _M_MKT_SEED42, "夹具")
    assert got["seeds_with_ratio_ge_1"] == [1], (
        f"c_mkt=0.5 远大于 MDE=0.2001，k=1 必须进越线集，实际 "
        f"{got['seeds_with_ratio_ge_1']}")
    assert got["verdict_direction"].startswith("🚨"), (
        "有种子越过 1 时判决方向必须改成「该格必须撤回」那一支")


# ══ 二、判据形状：门槛必须来自实参，不许手打字面量 ═════════════════════

def test_d1_reads_the_target_from_its_argument():
    """把 target 换成 0.99，D1 必须翻面 ⇒ 它读的是实参。

    ⚠️ 若 `target` 在实现里被写死成 0.2，下面这个夹具（c_mkt 全部 < 0.2）
    仍然会给 `True` ⇒ **判别力在「哪一格越过门槛」上**：
    这里故意用 `c_mkt` 全部落在 0.2~0.99 之间，
    于是一旦 target 被手死成 0.2，判决就一定与实参脱钩。
    """
    mid = {0: 0.30, 1: 0.45, 2: 0.60}
    assert M.d1_required_excess_is_positive_for_every_seed(
        mid, _T_TARGET, 5.7)["holds"] is False, "夹具前提：c_mkt 全部 ≥ 0.2"
    got = M.d1_required_excess_is_positive_for_every_seed(mid, 0.99, 5.7)
    assert got["holds"] is True, (
        f"target=0.99 时全部种子都低于它，D1 必须成立；实际 {got['holds']} "
        f"⇒ target 被写死成 0.2 了")
    assert got["target"] == 0.99, "产物必须回报**实参**那个 target，不是字面量"


def test_d1_refuses_a_c_mkt_for_which_the_annualization_is_undefined():
    """`c_mkt ≤ −1` ⇒ `(1+c)^(1/yrs)` 无定义 ⇒ 必须**拒绝**。

    预注册 §7 约束 3：缺档必须报错，绝不静默给复数或异常外结果。
    ⚠️ 反向正控：`-0.999` 必须**不**触发（它在 (−1, 1) 内，有定义）。
    """
    with pytest.raises(ValueError):
        M.d1_required_excess_is_positive_for_every_seed(
            {0: 0.09, 1: -1.4}, _T_TARGET, 5.7)
    assert M.d1_required_excess_is_positive_for_every_seed(
        {0: 0.09, 1: -0.999}, _T_TARGET, 5.7)["holds"] is True, (
        "c_mkt=-0.999 在 (−1,1) 内且低于 target，不该被拒 ⇒ "
        "拒绝条件写成了 `< 0` 或 `abs(c) > 1`")


# ── D1 的接线（预注册 §7 约束 1 的另一半） ──────────────────────────────

def _matches_its_own_reported_identity(out: dict, target: float,
                                       c_mkts: dict, yrs: float) -> bool:
    """主守卫与其**反向正控共用**的判定函数（第 131 层）。

    锚是 `required_excess` **docstring 逐字自报**的恒等式：
      「年化后必然满足 `(1 + 本函数) ** yrs == (1+target)/(1+c_mkt)`，
        这是几何均值的**定义**（与本函数实现路径无关 ⇒ 不是第 109 层的用公式重算）」

    ⇒ 权威取自**被测对象自己的自报**（第 159 层：产物/docstring → 函数 → 书），
      不是「我想要某个值」。
    ⚠️ 它验的是**接线**（实参有没有真传进生产函数），**不是**重写 `required_excess`
      的实现 —— 那正是 §7 约束 1「只换输入、不重写公式」要的东西。
    """
    by_k = out.get("required_excess_by_k")
    if not isinstance(by_k, dict) or set(by_k) != set(c_mkts):
        return False
    return all(math.isclose((1.0 + v) ** yrs,
                            (1.0 + target) / (1.0 + c_mkts[k]),
                            rel_tol=1e-12)
               for k, v in by_k.items())


def test_d1_calls_the_production_required_excess_on_this_files_own_c_mkt():
    """D1 必须**真调** `required_excess()`，而不是把 `holds` 复述一遍。

    🚨 旧实现写的是 `"required_excess_all_positive": bool(holds)`：字段名承诺
    「`required_excess()` 全部为正」，而**全文件从未调用过它**
    （AST 实测 `Call` 节点 = 0，只有 import 与散文）
    ⇒ 预注册 §7 约束 1 当时只兑现了 `caliber_benchmark_fraction` 那一半。

    ⚠️🚨 **判判决分辨不了**：按代数必然，`all(required_excess(...) > 0)` 与 `holds`
    **恒同值**（(1+c)<(1+T) 且指数 1/yrs>0 ⇒ 比值>1）⇒ 第 176 层：恒真的恒等式
    定不了语义 ⇒ 任何断言那个布尔的守卫在「真调」与「复述」上**同样为真**。
    ⇒ 本条只能锚在**逐格数值**上：夹具三格分叉，实现若把某格实参换掉
    （或把 `yrs` 挪一位），只有**那一格**的恒等式会红。

    ⚠️ 两个夹具**分处判决两侧**（第 120 层：两支都必须可达）。
    """
    below = {0: 0.09, 1: 0.11, 2: 0.13}          # 全低于 target ⇒ holds True
    over = {0: 0.09, 1: 0.11, 2: 0.34}           # 末格越过 target ⇒ holds False

    for name, c, want_holds in (("全低于 target", below, True),
                                ("末格越过 target", over, False)):
        out = M.d1_required_excess_is_positive_for_every_seed(c, _T_TARGET, 5.7)
        assert out["holds"] is want_holds, f"夹具前提失效：{name} 那一支没走到"
        assert _matches_its_own_reported_identity(out, _T_TARGET, c, 5.7), (
            f"{name}：required_excess_by_k 必须逐格等于生产函数在本文件 c_mkt 上的"
            f"返回值；实得 {out.get('required_excess_by_k')}")
        # 这条代数恒等在落盘成**可失败的检查**，不是散文里的一句断言：
        assert out["required_excess_all_positive_equals_holds"] is True, (
            f"{name}：真调用算出的布尔与 holds 不等 ⇒ 要么生产函数被改坏，"
            "要么某格绕过了 `c_mkt ≤ −1` 的拒绝")


def test_the_required_excess_identity_check_is_not_tautological():
    """反向正控：判定函数必须**认得出实参被换掉**。

    🚨 上一轮 M12 的正控验的是「AST 探测器找不找得到那条赋值」⇒ 它**恒真**，
      而「探测器认不出错误」与「判决拒得了错误」在代码上根本不相干。
    ⇒ 本条正控喂的是**真的错夹具**，走的是**主守卫同一个判定函数**。

    ⚠️⚠️ **本条与主守卫正交：不经过 `d1()`**（2026-10-16 改）。
      第一版把 `d1()` 的输出拿来做「正控前提」assert，于是本条变成
      「主守卫 ∧ 判定函数非恒真」的**合取** ⇒ 它红的**永远只能是合取里先失败
      的那一支** ⇒ 🚨 它声称的那一支（认得出实参被换）**结构上从未生效过**：
      实测 M14 变异（`yrs` → `yrs + 1`）时它红在「前提失效」那句上，
      而按两端 `yrs` 不同必然不等，它声称的那一支**必绿** ⇒ 对 M14 零判别力
      （第 184 层：能力声明与实测逐位不符；第 20 层的镜像：探针自带一个
      把目标断言遮住的前置断言）。
      ⇒ 现在 `by_k` 由 **生产函数 `M.required_excess` 直接造**，不经过接线
      ⇒ 本条只问「判定函数本身」，**接线由主守卫问** ⇒ M14 只被主守卫杀。

    ⚠️ 正控不许写成「恒假的断言」：若把 `yrs` 挪一位再拿去核对同一份 `by_k`，
      失败是被**恒等式本身**保证的 ⇒ 那验的是代数，不是判定函数。
      ⇒ 这里改的是**夹具**。
    """
    c = {0: 0.09, 1: 0.11, 2: 0.34}
    fake = {"required_excess_by_k": {k: M.required_excess(_T_TARGET, c[k], 5.7)
                                     for k in sorted(c)}}
    assert _matches_its_own_reported_identity(fake, _T_TARGET, c, 5.7), (
        "正控前提失效：判定函数在正确夹具上就不通过")

    tampered = dict(c)
    tampered[1] = c[1] + 0.05          # 只动一格
    assert not _matches_its_own_reported_identity(fake, _T_TARGET, tampered, 5.7), (
        "把夹具一格改掉后判定函数仍通过 ⇒ 它比的是恒等式本身而非**实参绑定** "
        "⇒ 对「实参被换掉」零信息量（第 176 层）")


def test_d2_boundary_is_inclusive_at_exactly_one_mde():
    """阈值 1 必须**闭区间**（`|c| == M` 算越线）。

    「MDE 的定义」是 `|c| < M ⇒ 连符号都定不了`，因此 `|c| == M` 那一格
    **不在**「定不了」的集合里 ⇒ 必须进越线集。
    ⚠️ 写成 `>` 会把恰好等于 MDE 那一格静默判成「测不出来」。
    """
    at = M.d2_sign_determinable_against_the_seed42_mde(
        {0: _M_MKT_SEED42, 1: 0.5}, _M_MKT_SEED42, "夹具")
    assert at["seeds_with_ratio_ge_1"][0] == 0, (
        "|c| 恰好等于 MDE 时必须算越线（`>` 会把这一格判成「测不出来」）")
    just_under = M.d2_sign_determinable_against_the_seed42_mde(
        {0: math.nextafter(_M_MKT_SEED42, 0.0), 1: 0.5},
        _M_MKT_SEED42, "夹具")
    assert 0 not in just_under["seeds_with_ratio_ge_1"], (
        "比 MDE 小 1 ulp 的一格必须**不**算越线 ⇒ 判据写成了 `>=`")


def test_d2_declares_that_its_denominator_does_not_track_k():
    """D2 的分母是**种子 42 的 MDE**，产物必须自报这件事（第 17 种形状）。

    ⚠️ 这不是散文洁癖：那个 MDE 由种子 42 的逐日序列算出，**自己吃种子**
    ⇒ 「任何种子下都不可定符号」这句话需要先重算 `M_mkt(k)` 才成立。
    ⇒ 沉默的分母会让读者把比值读成 `c_mkt` 自己的性质。
    """
    got = M.d2_sign_determinable_against_the_seed42_mde(
        _cmkts(), _M_MKT_SEED42, "basket_nav_01…::tiers[0].mde_as_annual_pct")
    assert "42" in got["m_mkt_role"], (
        "分母角色必须写明它是种子 42 的 MDE；当前文本："
        f"{got['m_mkt_role']!r}")
    assert got["m_mkt_source"].startswith("basket_nav_01"), (
        f"分母出处必须指向产物字段，当前：{got['m_mkt_source']!r}")
    assert got["m_mkt"] == _M_MKT_SEED42, "分母值必须原样带出，不许在途中被换算"


def test_d2_measures_a_sign_blind_ratio_not_a_single_sided_one():
    """D2 的比值必须**对 `c_mkt` 的符号不敏感** —— 那是 `|c|` 的定义性质。

    🚨 **为什么这条必须存在**：本文件此前**所有**喂给 `d2_…` 的夹具
      `c_mkt` **全为正** ⇒ `abs(c) == c` ⇒ 变异台 M10
      （`abs(c) / m_mkt` → `c / m_mkt`）在**任何既有夹具上与原实现逐位同形**
      ⇒ 台子报「存活」。
    ⇒ 那**不是等价变异体**：判别依据是「负 `c_mkt` 真的会发生吗」——
      **会**，`market_nav_caliber.json` 的 `equal_weight.geometric_annual_pct`
      是**符号量**（市场跌一年它就是负的），而 D2 判的正是
      「符号**定不定得了**」（MDE 的定义是 `|c| < M`）⇒ 分母上一步要的是 `|c|`。
      摘掉 `abs()` 之后比值变成**单边读数**：一个 `c = −0.2` 的市场腿算出
      **负比值**，而 `r >= 1.0` 对它仍然判「测不出」
      ⇒ 一个**本该越线**的读数被永久藏在集合外，而报告照常出数。

    ⚠️ 判据用**对称性**（`+c` 与 `−c` 必须给出同一个比值），
      **不是**「比值等于某个公式算出来的数」—— 后者是用公式重算（第 109 层），
      而且必须配一个容差；对称性**既不需要公式也不需要容差**，方向也对称。
    🚨 而它的权威**不是我的设计**，是产物自己的 `rule` 字段逐字写着
      `|c_mkt(k)| / M_mkt >= 1` ⇒ 本条钉的是「实现符合它自报的规则」。
    """
    rule = M.d2_sign_determinable_against_the_seed42_mde(
        _cmkts(), _M_MKT_SEED42, "夹具")["rule"]
    assert "|c_mkt(k)|" in rule, (
        "D2 自报的 rule 里已经没有绝对值了 ⇒ 要改实现，先改规则并说明为什么："
        f"当前 {rule!r}")

    mag = 0.35                      # 比值 = 0.35 / 0.2001 ≈ 1.75，远离分界 1
    m = _M_MKT_SEED42
    # 正控（第 120 层）：这份夹具**真的落在符号可辨的一侧** ——
    # 若 `+mag/m == -mag/m`，下面两条断言都恒真，守卫会退化成装饰。
    assert mag / m != -mag / m, (
        f"夹具前提已失效：{mag}/{m} 与 {-mag}/{m} 相等 ⇒ "
        "符号在这个量级上不可辨，本守卫已变成恒真装饰")

    pos = M.d2_sign_determinable_against_the_seed42_mde(
        {0: mag}, m, "夹具")["ratios"][0]
    neg = M.d2_sign_determinable_against_the_seed42_mde(
        {0: -mag}, m, "夹具")["ratios"][0]
    assert pos == neg, (
        f"|c| 必须让 +{mag} 与 −{mag} 给出同一个比值，实际 {pos!r} ≠ {neg!r} "
        "⇒ D2 的比值是**单边读数**，符号被当成了量的符号"
        "（市场跌一年时 `c_mkt` 为负，那一格会被永久判成「测不出」）")
    assert neg > 0.0, (
        f"比值是两个**量**之比，必须为正，实际 {neg!r} "
        "⇒ 分母上一步没有取绝对值")


def test_d3_is_descriptive_and_carries_no_verdict_field():
    """D3 是**纯描述**，产物里不许出现任何判决字段（第 178 层）。

    「无阈值的『一致』等于没有判据」的近亲：把描述读成判决。
    """
    got = M.d3_distribution_only(_cmkts())
    assert got["kind"] == "DESCRIPTIVE_ONLY"
    banned = [k for k in got
              if any(w in str(k).lower()
                     for w in ("hold", "pass", "fail", "verdict", "sig", "ok_"))]
    assert not banned, (
        f"D3 是纯描述，却带判决字段 {banned} ⇒ 描述被读成判决"
        f"（预注册 §7 尾：三个判据混不成一个三分支标签）")


def test_d3_states_that_the_spread_is_observed_not_the_population_bound():
    """`observed_spread` 必须**自报**它是 N 个观测的极差，不是总体极差。"""
    got = M.d3_distribution_only(_cmkts())
    assert got["observed_spread"] == got["max"] - got["min"], (
        "观测极差必须等于观测到的 max − min")
    assert "观测极差" in got["spread_is"], (
        f"必须写明它是观测极差：{got['spread_is']!r}")
    assert "不是" in got["spread_is_not"], (
        f"必须写明它**不是**总体范围：{got['spread_is_not']!r}")


def test_d4_flags_the_seed_as_ignored_when_every_delta_is_identical():
    """跨 `k` 逐位全同 ⇒ 「种子被忽略，扫描没生效」⇒ **停止**。

    这是 D4 的第②行（预注册 §5）。它是**取证**，不是判决 ——
    但它必须能区分「换了票群」与「种子压根没进去」。
    """
    flat = M.d4_bitwise_reconciliation(
        {0: 0.09, 1: 0.09, 2: 0.09}, _C_MKT_VERDICT_ARM)
    assert flat["all_identical_across_k"] is True, (
        "全同时 `all_identical_across_k` 必须为真")
    assert "种子被忽略" in flat["reading"], (
        f"全同时判决文本必须说「种子被忽略」：{flat['reading']!r}")

    varied = M.d4_bitwise_reconciliation(_cmkts(), _C_MKT_VERDICT_ARM)
    assert varied["all_identical_across_k"] is False, (
        "跨 k 逐位不同时 `all_identical_across_k` 必须为假 ⇒ "
        "该字段恒真（它守的不是「种子真的换了票群」）")
    assert "真的换了票群" in varied["reading"], (
        f"分叉时判决文本必须说「种子真的换了票群」：{varied['reading']!r}")


def test_d4_refuses_to_run_without_the_k0_baseline_cell():
    """`k=0` 是基线复核格（预注册 §4）；缺了它**必须拒绝**。

    缺档不许回落成「拿最小那个当基线」——那会让 D4 去和一条
    从没量过的线对账，而报告会照常出数。
    """
    with pytest.raises(KeyError):
        M.d4_bitwise_reconciliation({1: 0.09, 2: 0.10}, _C_MKT_VERDICT_ARM)


# ══ 三、口径：年化与键顺序 ═════════════════════════════════════════════

def test_geometric_annual_compounds_back_to_the_measured_multiple():
    """**绝对外部锚**：`(1+g)^(n/244)` 必须**还原**成实测复利终值。

    ⚠️ 这条**不是**「再算一遍同一个公式」（第 5 种形状：断言 == 公式）。
      它用的是**几何年化的定义式** —— 「把年化复利回去要拿回终值」，
      任何把 244 写成 250、把 `1/years` 写成 `years`、或换算成算术年化的
      实现都会红，而生产函数 `_annualized_compound` 的正确性**不由本守卫保证**
      （它由 `market_nav_caliber` 自己的判据保证）。
    """
    dates = [f"2021-01-{d:02d}" for d in range(1, 29)]   # 28 个交易日
    series = {d: 0.001 + 0.0001 * i for i, d in enumerate(dates)}
    mult, g, n = M.geometric_annual(series, dates)
    assert n == len(dates)
    assert (1.0 + g) ** (n / 244) == pytest.approx(mult, rel=1e-12), (
        f"几何年化必须复利回去还原终值：(1+{g!r})^{n}/244 = "
        f"{(1.0 + g) ** (n / 244)!r} ≠ {mult!r}")


def test_geometric_annual_is_strictly_decreasing_in_the_window_length():
    """**单调性外部锚**：同一条序列，**终值固定**时，窗口越长年化越低。

    ⚠️ 不依赖任何容差，也**不需要知道正确答案**——代数上不可能相反。
      若有人把 `1/years` 写成 `years`，这条立刻红。
    🚨🚨 **原写「或误乘 244，这条立刻红」是错的，实测它没有红**（2026-10-16
      按台子输出更正；变异 M6 打的就是这个用点，**只被 round-trip 那条杀**）。
      机制：`_annualized_compound(mult, n) ≡ mult**(244/n) − 1`，而变异把它变成
      `mult**(1/n) − 1` —— 🚨 **终值固定**时 `244/n` 与 `1/n` **都**随 n
      严格递减 ⇒ 下面那条 `gs[0] > gs[1] > gs[2]` 在**原实现与变异体上同样为真**
      ⇒ 这是第 20 层「探针自带一个把缺陷掩盖掉的对称性」：
      缺陷在**本条守卫所测的方向上是对称的**，所以它对 M6 **结构上零信息量**。
      ⇒ 能分辨它的是 `test_geometric_annual_compounds_back_to_the_measured_multiple`
      —— 那条把指数 `n/244` **逐字钉进断言**，变异后算的是
      `(1+g)^(n/244) = mult**(1/244) ≠ mult` ⇒ 当场红。
    ⇒ 所以别把这条当「年化对不对」的守门人：它守的是**形状**（随窗口拉长而递减），
      值那一层由 round-trip 守。⚠️ 本条**不许**被加严去补 M6 ——
      它现在这样是对的（M6 有别的守卫杀），加断言只会把一条有效的外部锚
      绑到它**看不见**的那一半上（第 112 层）。

    🚨 **第一版的夹具没有实现本 docstring 声明的前提**（第 141 层）：
      它每天都给 0.0009 ⇒ `mult = (1+r)^n` **随 n 一起涨**
      ⇒ `g = mult^(244/n) − 1` 恰好**恒定**，实测
      `[0.2454553913878268, 0.24545539138783057, 0.24545539138783257]`
      （那 ~1e-14 的差是连乘的浮点舍入，**不是性质**）⇒ 断言红。
    ⇒ 病根不是守卫写错，是**夹具把它要测的那个量的前提给弄丢了**。
      ⇒ 修法是让夹具实现「终值固定」：**前 20 天有收益，其后 0**
      （`× 1.0` 是恒等元 ⇒ 终值三档逐位相同）。
    ⇒ **不许改容差把它糊绿**，也**不许把「递减」删掉改成观测冻结** ——
      那会把一个仍然有效的外部锚换成一句观测。
    """
    dates = [f"d{i:03d}" for i in range(60)]
    series = {d: (0.0009 if i < 20 else 0.0) for i, d in enumerate(dates)}
    # 判别力前提本身必须是断言：夹具哪天退化成「终值也随 n 涨」，
    # 下面那条会因浮点噪声给出**看似随机**的顺序 ⇒ 而恒真的守卫不会喊（第 146 层）。
    mults = [M.geometric_annual(series, dates[:k])[0] for k in (20, 40, 60)]
    assert len(set(mults)) == 1, (
        f"夹具已失效：三档的复利终值必须逐位相同（这正是「终值固定」的前提），"
        f"实际 {mults}")
    gs = [M.geometric_annual(series, dates[:k])[1] for k in (20, 40, 60)]
    assert gs[0] > gs[1] > gs[2], (
        f"年化必须随窗口拉长**严格递减**，实际 {gs} ⇒ "
        "终值固定时年数越多年化越低，这是代数必然")


def test_geometric_annual_refuses_to_compromise_on_a_missing_day():
    """缺一个交易日 ⇒ 必须 `raise`，不许用现存的日子凑合（第 13 种形状）。

    缺档若静默凑合，窗口长度会随抽样变 ⇒ `n_days` 变 ⇒
    年化换算的分母变 ⇒ **量的还是同一条线，但不是同一个量**。
    """
    dates = [f"d{i:03d}" for i in range(30)]
    series = {d: 0.001 for d in dates[:-1]}          # 故意缺最后一天
    with pytest.raises(KeyError):
        M.geometric_annual(series, dates)
    # 反向正控：一个都不缺时必须跑得通 ⇒ 上面不是「恒抛」
    assert M.geometric_annual(series, dates[:-1])[2] == len(dates) - 1


def test_order_fingerprint_is_over_insertion_order_not_sorted_order():
    """`daily_map` 键顺序的指纹必须按**插入序**算。

    `benchmark_cohort` 的 docstring 逐字警告：同一个 seed 配一个
    键顺序不同的 `daily_map`，抽到的**不是同一批票**。
    ⇒ 指纹若按排序序算，两个顺序不同的产物会**长得一样**（第 22 层）。
    """
    a = {"000002.SZ": None, "000001.SZ": None, "000003.SZ": None}
    b = {"000001.SZ": None, "000003.SZ": None, "000002.SZ": None}   # 同集合，不同序
    assert set(a) == set(b), "夹具前提：两图键集合相同"
    assert M.order_fingerprint(a) != M.order_fingerprint(b), (
        "同集合不同插入序的指纹必须不同 ⇒ 指纹按**排序序**算了，"
        "而那会把「换顺序抽到不同票」这件事藏起来")
    assert M.order_fingerprint(a) == M.order_fingerprint(dict(a)), (
        "反向正控：同序重建必须给同一指纹")


# ══ 四、接线：判据落在哪一层（AST，不是子串 —— 第 13 层）═══════════════

def _fn(name: str) -> ast.FunctionDef:
    """把 `M.<name>` 的源码解析成**那一个** `FunctionDef` 节点。

    🚨 **第一版少返回了一层 AST**（标 `-> FunctionDef` 却返回 `ast.Module`）
      ⇒ `.body` 拿到的是 `[FunctionDef]` 而不是语句列表
      ⇒ `test_expect_bytes_is_captured_before_the_first_expensive_step`
      报「第一条可执行语句是 FunctionDef」。
    ⇒ ⚠️ **被测代码自始至终是对的**（实测 `_run` 的真身第一条就是
      `Assign | bytes_at_start = provenance.code_file_sha256(ROOT, GENERATOR)`）。
    ⇒ ★ **这才是最贵的形态**：`ast.walk` 对 `Module` 与 `FunctionDef`
      **都会递归进函数体** ⇒ 20 条 `ast.walk` 守卫**全绿**，
      只有唯一一条用 `.body` 的红 ⇒ 门禁输出读起来像「只有一条守卫有问题」，
      而真相是**探针坏了**（第 175 层附二「文本锚点守卫死在我自己一次重构上」的变体）。
    ⇒ 所以下面自带一条**能力守卫**：`_fn` 必须真的返回 `FunctionDef`，
      且它自己的名字必须与实参一致（否则取到别的函数也会绿）。
    """
    tree = ast.parse(inspect.getsource(getattr(M, name)))
    assert len(tree.body) == 1 and isinstance(tree.body[0], ast.FunctionDef) \
        and tree.body[0].name == name, (
        f"_fn({name!r}) 解析到的不是那一个函数："
        f"顶层 {len(tree.body)} 条，首条 {type(tree.body[0]).__name__}，"
        f"名字 {getattr(tree.body[0], 'name', None)!r} ⇒ 探针本身坏了，"
        "此时任何下游断言的红都是探针的红，不是被测代码的红")
    return tree.body[0]


def _calls_named(fn: ast.FunctionDef, name: str) -> list:
    """函数体里所有「被调名 == `name`」的 Call 节点。

    ⚠️ **裸名与属性访问两种形态都认**：`provenance.code_file_sha256(...)`
      与 `from … import code_file_sha256` 之后的裸名调用**是同一件事**，
      只认一种会把它判成「没接线」—— 而那会把人推向「为了变绿去改本来正确的写法」
      （本仓已经为「只认 `provenance.stamp` 不认裸名」付过一次代价）。
    ⚠️ 返回**全部**命中而不返回第一个：调用点数量本身就是判据的一部分
      （0 处 ⇒ 抓取链不存在；2 处 ⇒ 「哪一份是开跑时那份」取决于求值顺序）。
    """
    out = []
    for n in ast.walk(fn):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        got = f.id if isinstance(f, ast.Name) else getattr(f, "attr", None)
        if got == name:
            out.append(n)
    return out


def _is_generator_arg(node: ast.AST) -> bool:
    """这个 AST 节点**是不是**模块级 `GENERATOR` 这个名字。

    ★ 抽出来是因为它必须被**两处共用**：主守卫（`stamp` 的第二实参）
      与它的反向正控（第 131 层：**正控要验判决，不只验探测器**）。
      上一版正控断言的是 AST 自己（`probe.args[1].id == "__file__"`），
      那是恒真的东西 —— 「探测器认得出错误」与「判决拒得了错误」
      在代码上根本不相干。
    """
    return isinstance(node, ast.Name) and node.id == "GENERATOR"


def test_d4_itself_never_divides_by_a_pct_scale():
    """`d4_bitwise_reconciliation` 内**不许**出现任何除法常量。

    🚨 它的第二版曾在函数体里写 `artifact_geom_pct / 100.0` ——
      那个 100.0 **恰好**等于 `caliber_benchmark_fraction` 用的同一个
      `CALIBER_PCT_SCALE`，所以它**不会报错**，而它让量纲换算在这份文件里
      **发生了第二次**：有人改 `CALIBER_PCT_SCALE`，换算会静默地只换一半。
    ⚠️ 用 **AST 的实参位置**，不用源码子串 —— 注释里逐字引用
      `/ 100.0` 讲这个故事是**合法且必要**的（第 13 层）。
    """
    divisors = [n.value for n in ast.walk(_fn("d4_bitwise_reconciliation"))
                if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div)
                for v in ast.walk(n.right)
                if isinstance(v, ast.Constant) and isinstance(v.value, (int, float))]
    assert not divisors, (
        f"D4 体内出现除法常量 {divisors} ⇒ 量纲换算在这份文件里"
        f"发生了第二次；它必须由调用方用生产的 "
        f"caliber_benchmark_fraction 换好后传进来")
    # 反向正控：探测器看得见别的除法形态（否则上面近乎恒真）
    probe = ast.parse("def f(a, b):\n    return a / b, a / 100.0\n").body[0]
    found = [v.value for n in ast.walk(probe)
             if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div)
             for v in ast.walk(n.right)
             if isinstance(v, ast.Constant) and isinstance(v.value, (int, float))]
    assert found == [100.0], f"探测器连 `a / 100.0` 都看不见：{found}"


def test_the_pct_conversion_occurs_exactly_once_in_this_file():
    """本文件**一次**都不许自己引用 `CALIBER_PCT_SCALE`。

    ⚠️ 这条当前「绿」的方式是**零命中** —— 它守的是**未来**：
      下一个往这里接 caliber 的人很可能顺手加一句 `/ 100.0`，
      而那不会让任何别的守卫变红（第 182 层：量纲守卫钉住生产者，
      对消费者零覆盖）。

    🚨 **第一版这条写成子串断言，当场红 —— 而抓到的是 docstring。**
      `d4_bitwise_reconciliation` 的 docstring 第 220 行逐字引用
      `CALIBER_PCT_SCALE` 讲「有人改它换算会静默只换一半」这个故事，
      而**引用它是合法且必要的**（删掉这条注释，下一个维护者就会重新踩）。
      ⇒ 这是第 13 层的**镜像**：不只「注释满足子串」，
        「注释里的合法引用也被子串判红」同样是一条**永远红的过严守卫**，
        而它会把人推向「把那段解释删掉」这个**错误修法**（第 112 层）。
    ⇒ 改用 AST：`ast.Name` / `ast.Attribute` 才是「代码真的引用了它」。
    """
    tree = ast.parse(inspect.getsource(M))
    refs = [n for n in ast.walk(tree)
            if (isinstance(n, ast.Name) and n.id == "CALIBER_PCT_SCALE")
            or (isinstance(n, ast.Attribute) and n.attr == "CALIBER_PCT_SCALE")]
    assert not refs, (
        f"本文件的代码引用了 CALIBER_PCT_SCALE（{len(refs)} 处）⇒ "
        f"量纲换算在这里发生了第二次；换算只许在生产的 "
        f"caliber_benchmark_fraction 里发生")
    # 正控：探测器**看得见**这个引用形态 —— 否则上面近乎恒真
    probe = ast.parse("g = x / CALIBER_PCT_SCALE\n").body[0]
    assert any(isinstance(n, ast.Name) and n.id == "CALIBER_PCT_SCALE"
               for n in ast.walk(probe)), "探测器连裸名引用都看不见"


def test_d4_is_fed_the_production_conversion_not_a_local_one():
    """`_run` 必须把 `caliber_benchmark_fraction(cal, arm)` 的**结果**传给 D4。

    ⚠️ 锚在**实参身份**上（那个 Call），不是「函数体里出现过它」——
      后者会被注释满足（第 13 层）。
    """
    call_sites = [n for n in ast.walk(_fn("_run"))
                  if isinstance(n, ast.Call)
                  and isinstance(n.func, ast.Name)
                  and n.func.id == "d4_bitwise_reconciliation"]
    assert len(call_sites) == 1, (
        f"_run 里必须**恰好一处**调 d4_bitwise_reconciliation，实际 {len(call_sites)}")
    fed = call_sites[0].args[1]
    assert isinstance(fed, ast.Name) and fed.id == "c_mkt_verdict", (
        "D4 的第二个实参必须是那个**由生产换算函数得到**的变量"
        f"（`c_mkt_verdict`），实际是 "
        f"{ast.unparse(fed) if fed else '（无实参）'} ⇒ "
        "第二实参不是换算结果，就是换算在这条链上被重做了一遍")
    # 接线：那个变量确实来自 caliber_benchmark_fraction
    assigns = [n for n in ast.walk(_fn("_run"))
               if isinstance(n, ast.Assign)
               and any(isinstance(t, ast.Name) and t.id == "c_mkt_verdict"
                       for t in n.targets)]
    assert len(assigns) == 1, "c_mkt_verdict 必须恰好被赋值一次"
    rhs = assigns[0].value
    assert isinstance(rhs, ast.Call) and isinstance(rhs.func, ast.Name) \
        and rhs.func.id == "caliber_benchmark_fraction", (
        f"c_mkt_verdict 必须由 caliber_benchmark_fraction 算出来，实际来自 "
        f"{ast.unparse(rhs)}")


def _verdict_arm_rhs(fn: ast.FunctionDef) -> ast.AST:
    """`verdict_arm` 那条**赋值**的右端节点（恰好一条，找不到就当场报错）。

    🚨 抽出来是因为它必须被**两处共用**（第 131 层）：主守卫与它的正控。
      正控要验的是**判决**（这个右端是不是那个预注册常量名），
      **不是**探测器（我能不能用 `ast.walk` 找到那条赋值）——
      「探测器认得出错误」与「判决拒得了错误」在代码上根本不相干。
    ⚠️ 收在**具体那条 `Assign`** 上，不是「源码里出现过 `VERDICT_BENCH_ARM`」
      （第 175 附二：`ast.walk` 对 `Module` 与 `FunctionDef` **都会递归进函数体**
      ⇒ 那条赋值被整个删掉、而别处还留着那个名字时，
      「全文出现过」照样成立 ⇒ 它对这次要抓的形态是**零信息量**的）。
    """
    hits = [n for n in ast.walk(fn)
            if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "verdict_arm"
                    for t in n.targets)]
    assert len(hits) == 1, (
        f"verdict_arm 必须恰好被赋值一次，实际 {len(hits)} 处 ⇒ "
        "要么判决臂被降级成了随扫描参数（多处赋值），"
        "要么这条赋值整个没了（判决臂改在别处算）—— "
        "两种都是把**预注册**的判决臂从代码的单一真相源上摘掉")
    return hits[0].value


def _is_preregistered_verdict_arm(node: ast.AST) -> bool:
    """这个赋值右端**是不是**模块级那个预注册判决臂常量名。"""
    return isinstance(node, ast.Name) and node.id == "VERDICT_BENCH_ARM"


def test_the_verdict_arm_is_the_preregistered_constant_not_a_literal():
    """`_run` 里 `verdict_arm` 必须取自 `VERDICT_BENCH_ARM`，不许是字面量。

    🚨 **第 183 层在这个新文件里的复发**。判决臂是**预注册**的
      （预注册 §8.1 第 ① 条：「不许换判据臂」），换臂要走
      「**加对照臂并显式标 `role`**」那条路（预注册 §7 约束 6）。
    ⚠️ 换臂**当时实测两臂「实测/需要」差 7.94×**
      （`equal_weight` +6.27% vs `cap_weight` **+49.78%**），
      所以它**足以改结论**；而它也可能**一个判决都不改**
      （两者落在门槛同一侧）⇒ 那种情形下**没有任何东西会红**
      ⇒「判决没变」把「换了被诊断的对象」完全藏住。
    ⇒ 这是第 138 层的第二面：「换了被诊断的对象，判决类别可能不变」
      ⇒ 修法是**钉对象**，不是钉判决。

    ⚠️ 为什么 `test_d4_is_fed_the_production_conversion_not_a_local_one`
      抓不到：它只查 `c_mkt_verdict` 的**来源**（必须是
      `caliber_benchmark_fraction(...)`），**不查 `verdict_arm` 的取值**
      ⇒ 对换臂完全隐形（实测换臂后 23 条守卫全绿）。

    🚨 **绝不允许的处置**：把 `verdict_arm` 改回、或者把它降级成
      「随扫描参数」—— 有默认值/可传参 ⇒「判决臂 vs 敏感性臂」的区分
      会被某次调用悄悄抹掉（第 119 / 182 层）。
    """
    rhs = _verdict_arm_rhs(_fn("_run"))
    assert _is_preregistered_verdict_arm(rhs), (
        "verdict_arm 必须取自预注册常量 `VERDICT_BENCH_ARM`"
        f"（当前 = {M.VERDICT_BENCH_ARM!r}），实际右端是 "
        f"{ast.unparse(rhs)} ⇒ 判决臂被就地换掉了；换臂必须走"
        "「加对照臂并显式标 role」那条路（预注册 §8.1）")

    # 正控（第 131 层）：喂一个右端是字面量的赋值，同一个判决函数必须判它假
    probe = ast.parse("def _fake():\n    verdict_arm = 'cap_weight'\n").body[0]
    assert _is_preregistered_verdict_arm(_verdict_arm_rhs(probe)) is False, (
        "判决连「右端是字面量」都认不出来 ⇒ 上面那条近乎恒真，"
        "而恒真的守卫与没有守卫在台子输出里长得一样")


def test_the_generator_is_a_repo_relative_path_not_dunder_file():
    """`code_file_sha256()` 的第二实参必须是 `GENERATOR`，不许是 `__file__`。

    🚨 `code_file_sha256(root, generator)` 把第二个实参当**相对路径**接在
      root 后面 ⇒ 传 `__file__`（绝对路径）时 POSIX 的 `/` 会把它吞成
      一个「仓库内恰好存在的相对路径」，或直接取不到而返 `None`；
      而 `stamp()` 收到 `None` 会**整项跳过**字节检查（第 125 层），
      **无人察觉**。

    ⚠️ **本条只查 `_run`，不扫全文件**：模块级的
      `ROOT = Path(__file__).resolve().parents[1]` 里那个 `__file__`
      是**合法且必须保留**的（它就是仓库根的来源）⇒ 扫全文件是又一条
      **永远红的过严守卫**（第 112 层），而它的失效方向最贵：
      会把人推向「把 `ROOT` 改成 `Path(".")`」这个**错误修法**。

    ⚠️ **刻意不判「M.GENERATOR 是不是绝对路径」**：下面那句字面量相等已经
      钉死了它的值；而生产路径上「传了 `__file__`」这个**真实失效形态**
      由实参身份那两句钉住（`code_file_sha256` 与 `stamp` 各一处）
      ⇒ 再加一个路径助手是**冗余守卫**（第 136 层：不是不够严，
      而是不给新信息，且它自己的正确性从没被任何东西验过）。
    """
    assert M.GENERATOR == "scripts/bench_seed_sweep.py", (
        f"generator 必须是相对仓库根的路径串，实际 {M.GENERATOR!r}")

    calls = _calls_named(_fn("_run"), "code_file_sha256")
    assert len(calls) == 1, (
        f"_run 里必须**恰好一处**调 code_file_sha256，实际 {len(calls)} 处"
        f"（0 处 ⇒ 整个 expect_bytes 链不存在；2 处 ⇒ 有第二个抓取点，"
        f"而「哪一份是开跑时那一份」就变成取决于求值顺序）")
    args = calls[0].args
    assert (len(args) == 2
            and isinstance(args[0], ast.Name) and args[0].id == "ROOT"
            and _is_generator_arg(args[1])), (
        f"实参必须是 `(ROOT, GENERATOR)`，实际 "
        f"{[ast.unparse(a) for a in args]}")

    # ★ 反向正控：探测器看得见 `__file__` 出现在这个**实参位置**上。
    #   没有它，上面那句可以在「探测器只认 `GENERATOR` 这个名字」的空转
    #   实现上永远绿——而那正是「守的是碰巧」的样子（第 13 层）。
    probe = ast.parse("x = f(ROOT, __file__)\n").body[0].value
    assert [ast.unparse(a) for a in probe.args] == ["ROOT", "__file__"], (
        f"探测器连 `__file__` 出现在实参位都认不出 ⇒ 它测的不是实参身份："
        f"{[ast.unparse(a) for a in probe.args]}")


def test_the_stamp_call_carries_the_generator_and_the_captured_bytes():
    """`main()` 的 `stamp()`：generator 用 `GENERATOR`，且带上 root / data_root / expect_bytes。

    🚨 今天抓到的就是这一类错：`stamp(args.out, __file__, expect_bytes=bytes_at_start, …)`
      ——**三处错同时**：generator 用了 `__file__`（绝对路径）、`bytes_at_start`
      是 `_run()` 的**局部名**（跨函数 `NameError`）、缺 `root` / `data_root`。
      而那次 `NameError` 崩在 `write_text` **已执行之后**
      ⇒ **产物在、章没有**（第 131 层）。

    ⚠️ 钉的是**实参身份**（`Name('GENERATOR')`、三个关键字必须在、
      `expect_bytes` 的值是个**变量名**而不是别的表达式），不是「出现过 stamp」——
      「出现过」会被注释满足（第 13 层），而这里是当年真身与错版唯一的差别。
    """
    calls = _calls_named(_fn("main"), "stamp")
    assert len(calls) == 1, (
        f"main 里必须恰好一处 stamp，实际 {len(calls)} 处"
        f"（0 处 ⇒ 产物永远没有溯源章；2 处 ⇒ 一份产物盖两次）")
    call = calls[0]

    assert _is_generator_arg(call.args[1] if len(call.args) >= 2 else None), (
        f"stamp 的第二个位置实参（generator）必须是 GENERATOR，实际 "
        f"{ast.unparse(call.args[1]) if len(call.args) > 1 else '（实参不足 2 个）'}")

    kws = {k.arg: k.value for k in call.keywords}
    for must in ("root", "data_root", "expect_bytes"):
        assert must in kws, (
            f"stamp 缺 `{must}=` 关键字 ⇒ 那半边（仓库根 / 数据根 / 字节校验）"
            f"整个没接上，**而它是静默的**；当前关键字：{sorted(map(str, kws))}")
    assert isinstance(kws["expect_bytes"], ast.Name), (
        f"expect_bytes 的实参必须是那个**变量**（从 out 里 pop 出来的那份），"
        f"实际是表达式 {ast.unparse(kws['expect_bytes'])}")

    # ★ 反向正控**验判决而不是验探测器**（第 131 层）：喂一个**错的**
    #   generator 实参，同一个判据必须给「否」。上一版这里断言的是
    #   AST 自己（`probe.args[1].id == "__file__"`）—— 那恒真，
    #   而「探测器认得出错误」与「判决拒得了错误」是两件事。
    wrong = ast.parse("stamp(out, __file__, root=R, data_root=D, expect_bytes=b)\n").body[0].value
    assert not _is_generator_arg(wrong.args[1]), (
        "判据把 `__file__` 也当 GENERATOR 放过 ⇒ 它比的是碰巧，"
        "而 `stamp` 收到绝对路径会把字节检查整项跳过（第 125 层）")


def test_expect_bytes_is_captured_before_the_first_expensive_step():
    """`expect_bytes` 必须落在 `_run()` 的**第一条语句**（第 140 / 125 层）。

    🚨 放 `main()` 是今天抓到的那只雷；放**盖章之前**更坏 ——
      那会让「开跑的字节」变成「盖章的字节」，检查**恒真**。
    ⇒ AST 取 `_run` 的第一条语句，钉它的**结构**而不是它的位置。
    ⚠️ 反向正控：探测器看得见别的首语句形态（探针喂 `pass`）。
    """
    body = [n for n in _fn("_run").body
            if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant))]
    assert body, "_run 的函数体里一条语句都没有 ⇒ 夹具/被测代码坏了"
    first = body[0]
    assert isinstance(first, ast.Assign), (
        f"_run 的第一条可执行语句必须是赋值（当前是 "
        f"{type(first).__name__}）⇒ 字节抓取被别的昂贵步骤挤到了后面")
    assert isinstance(first.value, ast.Call) \
        and isinstance(first.value.func, ast.Attribute) \
        and first.value.func.attr == "code_file_sha256", (
        f"第一条语句必须是抓字节哈希，当前是 {ast.unparse(first)[:60]!r}")

    probe = ast.parse("def f():\n    pass\n    x = g(1)\n").body[0]
    kinds = [type(n).__name__ for n in probe.body]
    assert kinds == ["Pass", "Assign"], f"探测器连 `pass` 都分不出来：{kinds}"


def test_a_missing_byte_hash_stops_the_run_instead_of_falling_back():
    """哈希取不到 ⇒ **必须拒绝**，不许盖章（第 125 层 + CLAUDE.md 缺档铁律）。

    回落会让 sidecar 写出一枚**没验过字节**的章，而它看上去与真章无法区分
    ——比红着更糟（第 132 层「手写一个 sidecar 是形状 16」）。
    ⚠️ 这条**钉控制流**（AST），因为真跑 `_run` 要载 GB 级面板（本仓 OOM 唯一来源）。
    """
    guarded = [
        n for n in ast.walk(_fn("_run"))
        if isinstance(n, ast.If)
        and any(isinstance(c, ast.Compare) and any(
            isinstance(o, ast.Is) and isinstance(x, ast.Name)
            and x.id == "bytes_at_start"
            for o in c.ops for x in [c.left] + list(c.comparators))
            for c in ast.walk(n.test))
    ]
    assert guarded, (
        "_run 里没有 `if bytes_at_start is None:` ⇒ 取不到哈希时会带着 "
        "None 一路跑到 stamp()，而 stamp 会**整项跳过**字节检查"
        f"（当前代码里有无 raise：{[ast.unparse(n)[:40] for n in guarded]}）")
    for n in guarded:
        assert any(isinstance(x, (ast.Raise, ast.Call))
                   for st in n.body for x in ast.walk(st)), (
            f"`if bytes_at_start is None:` 的分支体内没有 raise ⇒ "
            f"缺档时静默继续：{ast.unparse(n.body[0])[:80]}")

    # ★ 正控 + 反向正控：**收窄到 `n.body` 本身也是一条要验的性质。**
    #   第一版写的是 `ast.walk(n)`（整个 If 节点，含 `test` 与 `orelse`）
    #   ⇒ `if cond: pass` + `else: raise()` 也会过 —— 那是一条
    #   **恒真**的守卫（第 23 层），而它的失效方向最贵：读成「守卫守住了」。
    probe_raise = ast.parse("if x is None:\n    raise SystemExit(1)\n").body[0]
    assert any(isinstance(y, ast.Raise)
               for st in probe_raise.body for y in ast.walk(st)), (
        "探测器连分支体里的 `raise` 都看不见 ⇒ 它什么也没在测")
    probe_else = ast.parse(
        "if x is None:\n    pass\nelse:\n    raise SystemExit(1)\n").body[0]
    assert not any(isinstance(y, ast.Raise)
                   for st in probe_else.body for y in ast.walk(st)), (
        "探测器还在看 else 分支 ⇒ `if cond: pass else: raise()` 会被误判成"
        "「有守卫」，而缺档时那条路径恰恰是不 raise 的那条")

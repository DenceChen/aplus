"""S3/G4 的守卫：**「不许发布」这句话本身不许被改软**。

## 这份产物是发布闸门，不是研究报告

`docs/TODO_reverse_scoring_2026-10-15.md` §3：

    | **G4** | `score` 四分位命中率 | **单调递增** | S3 |

原文：**「过不了这一关就不许发布」，与收益高低无关。**

⇒ 于是「G4 过了」是全项目最贵的一个布尔值之一。
本文件守三件事：

1. **判决函数本身**（`verdict`）在四个分岔上各给出正确的三态 ——
   用**合成夹具**，不用真实数据（第 118 层：拿真实数据当唯一证据时，
   「判据被换」和「数据碰巧」在报告里一模一样）。
2. **判决必须落在闸门臂上**：S2 已因逆大盘 27.3%（p=0.0034）停用 `down` 臂，
   所以发布只看 `up`。按字典序取第一个失败臂会让理由落在**已停用**的臂上。
3. **不许回落**：缺分数、缺闸门臂、p 值算成 0，三者都必须**报错或显式计数**，
   不许悄悄变成「通过」。
"""
from __future__ import annotations

import ast
import hashlib
import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from aplus import provenance as provenance_mod  # noqa: E402
from scripts.score_ranking_power import (  # noqa: E402
    ART_NAME, GATE_ARM, JUDGEABLE, _point_biserial, _within_group_quartile,
    verdict,
)

_SCRIPT = _ROOT / "scripts" / "score_ranking_power.py"
ART = _ROOT / "data" / "reports" / ART_NAME
VERIFY = _ROOT / "data" / "reports" / "verification.parquet"


# ── 判决函数的四个分岔：合成夹具，不许依赖真实数据 ──────────────────────

def _stats(monotone: bool, rho: float, p: float, floor: float) -> dict:
    return {"monotone_nondecreasing": monotone, "point_biserial_rho": rho,
            "permutation_p_two_sided": p,
            "null_abs_rho_q95_power_floor": floor,
            "detectable_at_alpha05": abs(rho) >= floor}


def _res(up: dict, down: dict | None = None) -> dict:
    arms = {GATE_ARM: {"_stats": up}}
    if down is not None:
        arms["down"] = {"_stats": down}
    return {"arms": arms}


def test_passes_only_when_the_gate_arm_is_monotone():
    """PASS 的**唯一**入口是「闸门臂四分位单调递增」，没有第二个。"""
    kind, _ = verdict(_res(_stats(True, 0.5, 0.001, 0.3)))
    assert kind == "PASS", kind


def test_not_monotone_and_underpowered_is_its_own_verdict():
    """🚨 三态的存在理由：不单调的**两个**原因必须分开报。

    合并成「不通过」会丢掉唯一有用的那部分信息 ——
    `POWERLESS` 的下一步是**攒前瞻期**，`MEASURED` 的下一步是**换结构（S1）**，
    两者的行动完全相反。
    """
    kind, why = verdict(_res(_stats(False, 0.1, 0.4, 0.35)))
    assert kind == "NOT_PASSED_POWERLESS", (kind, why)
    assert "攒前瞻期" in why, why


def test_not_monotone_but_well_measured_is_the_other_verdict():
    """检出力足够（|ρ| 越过零分布 95 分位）却仍不单调 ⇒ 是结构问题，不是噪声。"""
    kind, why = verdict(_res(_stats(False, 0.6, 0.01, 0.35)))
    assert kind == "NOT_PASSED_MEASURED", (kind, why)
    assert "换结构" in why, why


def test_a_missing_gate_arm_must_not_fall_back_to_pass():
    """缺闸门臂 ⇒ 判据无从判定 ⇒ **不许**回落成「通过」。

    这是第 89 层（"拒绝出判决"写成"什么都不留"）的镜像：
    那里是空目录与崩溃不可区分，这里是「臂不见了」与「臂全对」不可区分。
    """
    kind, why = verdict({"arms": {"down": {"_stats": _stats(True, 0.9, 0.0, 0.1)}}})
    assert kind != "PASS", (kind, why)
    assert GATE_ARM in why, why


def test_the_gate_arm_is_the_publishing_arm_not_the_first_one():
    """★ 判决理由必须落在**闸门臂**上。

    早期版本 `for arm, sub in res["arms"].items(): return ...`
    ⇒ 按字典序第一个失败臂是 `down`，于是理由文字讲的是**已停用的臂**。
    数字一样、闸门一样，但「down 臂不单调」和「发布闸门没通过」
    读的人会做出完全不同的动作。
    """
    res = _res(_stats(True, 0.5, 0.001, 0.3),          # 闸门臂单调 ⇒ 该 PASS
               _stats(False, -0.6, 0.01, 0.35))        # down 臂不单调但已停用
    kind, why = verdict(res)
    assert kind == "PASS", (kind, why)
    assert "并列报告" in why, why


def test_a_stopped_arm_cannot_veto_the_gate_and_cannot_rescue_it():
    """两向都钉住：down 臂的坏**不能**否决闸门，down 臂的好**不能**救闸门。"""
    res_bad = _res(_stats(False, -0.1, 0.5, 0.35), _stats(True, 0.9, 0.0, 0.1))
    assert verdict(res_bad)[0] != "PASS", "闸门臂不单调却 PASS ⇒ down 臂在救它"

    res_good = _res(_stats(True, 0.5, 0.001, 0.3), _stats(False, -0.6, 0.01, 0.35))
    assert verdict(res_good)[0] == "PASS", "down 臂不单调却否决了闸门"


# ── 产物：判据、计数、盖章 ──────────────────────────────────────────────

@pytest.fixture(scope="module")
def art() -> dict:
    assert ART.exists(), (
        f"{ART.name} 不存在 —— 发布闸门没产物 ⇒ 下一个人会以为 G4 还没测，"
        "于是照发。跑 `.venv/bin/python scripts/score_ranking_power.py`")
    return json.loads(ART.read_text(encoding="utf-8"))


def test_the_artifact_is_registered_and_stamped(art):
    """不登记 = `unknown` = **不许引用**；登记了不盖章同样不许引用。"""
    assert provenance_mod.DEFAULT_GENERATORS.get(ART.name) == \
        "scripts/score_ranking_power.py", (
        f"{ART.name} 未登记 ⇒ 按本仓规矩它不可引用")
    sidecar = ART.with_name(ART.name + provenance_mod.SIDECAR_SUFFIX)
    assert sidecar.exists(), f"{sidecar.name} 不存在 ⇒ 没有真章"
    sc = json.loads(sidecar.read_text(encoding="utf-8"))
    assert sc.get("source_parquet_sha256") == \
        hashlib.sha256(VERIFY.read_bytes()).hexdigest(), (
        "盖的章对不上 verification.parquet 的**当前字节** ⇒ "
        "这份闸门判决不是对现在这份数据做的")


def test_the_gate_arm_constant_is_the_publishing_arm():
    """S2 已停用 `down` 臂 ⇒ 闸门只能是 `up`。改这一行就是改发布策略。"""
    assert GATE_ARM == "up", (
        f"闸门臂变成了 {GATE_ARM!r} —— S2 的决定是停用 down 臂、只发 up；"
        "改这里等于在没有新证据的情况下改发布策略")


def test_judgeable_excludes_unknown_and_flat(art):
    """`no_data` 是**未知**、`flat` 是**无波动** —— 都不是「未命中」。

    把它们塞进分母会凭空造出命中率，而命中率的**分母**是判据的一部分。
    """
    assert set(JUDGEABLE) == {"hit", "miss"}
    res = art["by_score_col"]["score"]
    assert res["n_rows_dropped_undecidable"] == res["n_rows_total"] - \
        res["n_rows_judgeable"], "剔除数与总数对不上"
    assert set(res["dropped_breakdown"]) <= {"no_data", "flat"}, \
        res["dropped_breakdown"]


def test_missing_scores_are_counted_not_silently_skipped(art):
    """★ 缺分数的行**必须被计数**，不许静默跳过（第 112 层）。

    跳过之后「16 条可测」会变成「35 条全部一致」，那是**另一个量**。
    """
    for col, res in art["by_score_col"].items():
        assert "n_rows_score_missing" in res, (
            f"{col} 口径没有 `n_rows_score_missing` ⇒ 缺分数的行被静默丢弃了")
        assert res["n_rows_scored"] + res["n_rows_score_missing"] == \
            res["n_rows_judgeable"], (
            f"{col}: scored({res['n_rows_scored']}) + missing"
            f"({res['n_rows_score_missing']}) != judgeable"
            f"({res['n_rows_judgeable']})")


def test_the_independent_event_count_is_reported_and_recomputable(art):
    """🚨 80 **条**不是 80 个独立观测 —— 它是 4 日 × 2 臂 × 10 只（第 80 层）。

    判据对象是**独立事件数**。这里从 parquet **重算**一遍，
    而不是读产物自己写的那个字段（第 118 层：读落盘 json 等于没问）。
    """
    import pandas as pd
    df = pd.read_parquet(VERIFY)
    for col, res in art["by_score_col"].items():
        assert "n_independent_pred_dates" in res, f"{col} 没报独立事件数"
        assert res["n_independent_pred_dates"] == int(df["pred_date"].nunique()), (
            f"{col}: 独立事件数与 parquet 重算不符")
        assert res["n_independent_pred_dates"] < res["n_rows_judgeable"], (
            "独立事件数 == 条目数 ⇒ 分母用错了（第 80 层）")


def test_the_artifact_publishes_nothing_while_g4_is_not_passed(art):
    """★ 两支 `NOT_PASSED_*` **都**不许发布 —— 这是闸门，不是评分。"""
    if art["verdict"].startswith("NOT_PASSED"):
        assert art["publish_allowed"] is False, (
            f"判决是 {art['verdict']} 却 publish_allowed="
            f"{art['publish_allowed']} ⇒ 闸门被放行了")
        assert art["verdict"] in ("NOT_PASSED_POWERLESS", "NOT_PASSED_MEASURED"), \
            art["verdict"]


def test_the_criterion_is_monotonicity_and_carries_its_source(art):
    """判据必须**指名道姓**地是 G4/S3，且规则是单调而不是一个效应量字面量。

    ⚠️ 写成「|ρ| > 0.3」就是本仓反复出现过的字面量闸门（第 17 种形状）。
    """
    crit = art["by_score_col"]["score"]["criterion"]
    assert crit["id"] == "G4"
    assert "docs/TODO_reverse_scoring_2026-10-15.md" in crit["source"]
    assert "单调" in crit["rule"], crit["rule"]
    assert crit["alpha"] == 0.05


def test_the_permutation_p_can_never_be_zero():
    """★ p 值必须用 `(b+1)/(B+1)`，不许用 `b/B`。

    `b/B` 在「零假设下一次都没超界」时报 **p = 0** ——
    而 p=0 不是概率，它会让「显著」看起来像「确定」，
    于是**检出力下限**（零分布 95 分位）这条信息被彻底盖掉。
    """
    src = _SCRIPT.read_text(encoding="utf-8")
    assert "(int((null >= obs).sum()) + 1) / (n_perm + 1)" in src, (
        "置换 p 的算法被改了 —— 必须是 Phipson–Smyth 的 (b+1)/(B+1)")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_permutation_p")
    seg = ast.get_source_segment(src, fn) or ""
    assert "+ 1) / (n_perm + 1)" in seg and "/ n_perm" not in seg.replace(
        "(n_perm + 1)", ""), f"p 的算式不是 (b+1)/(B+1)：\n{seg[-400:]}"


def test_publish_allowed_is_wired_to_the_verdict_and_not_a_literal():
    """★ **接线层**守卫：产物里的 `publish_allowed` 必须由判决**算**出来。

    ⚠️ 为什么不能只靠产物层那条：`test_the_artifact_publishes_nothing_while_g4_is_not_passed`
    读的是**盘上那份 json**。把源码里的 `kind == "PASS"` 改成 `True` 并重跑，
    判决仍是 `NOT_PASSED_POWERLESS`、而 `publish_allowed` 变成 `true`
    —— 那时产物层那条会红，**但在重跑之前它一直是绿的**（第 96 层：
    产物层守卫天然只对历史快照有效，对当前代码零覆盖）。
    ⇒ 这里钉的是**那个表达式的形状**：值节点必须是
    「`kind` 与字符串 `"PASS"` 的比较」，不许是常量、不许是别的量。
    """
    tree = ast.parse(_SCRIPT.read_text(encoding="utf-8"))
    payload = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.Dict)
         and any(isinstance(k, ast.Constant) and k.value == "publish_allowed"
                 for k in n.keys)),
        None)
    assert payload is not None, "产物里没有 `publish_allowed` 字段"
    val = next(v for k, v in zip(payload.keys, payload.values)
               if isinstance(k, ast.Constant) and k.value == "publish_allowed")
    assert isinstance(val, ast.Compare), (
        "`publish_allowed` 不再是比较式 —— 它被换成了一个固定值/别的量，"
        "于是「不许发布」这句话与判决**脱钩**了")
    left = ast.unparse(val.left)
    assert left == "kind", f"比较的左边是 {left!r}，不是判决变量 `kind`"
    assert [ast.unparse(c) for c in val.comparators] == ["'PASS'"], (
        "比较的不是 'PASS' ⇒ 判决与「能不能发」不再一一对应")


# ── 对账：从 parquet 重算，不读产物自己写的字段（第 118 层） ──────────────

def test_the_artifact_reconciles_with_a_fresh_measurement(art):
    """★ 同 seed 同 n_perm ⇒ **逐位可重算**。产物与重算的每一项都必须相等。

    这是唯一一条能让**源码变异**当场变红的守卫：产物层读 json、
    跑完之后才与代码有关（第 96 / 118 层）。本条在测试里**现算一遍**。
    """
    import pandas as pd
    from scripts import score_ranking_power as M
    df = pd.read_parquet(VERIFY)
    for col, rec in art["by_score_col"].items():
        got = M.measure(df, col, n_perm=rec["criterion"]["n_perm"],
                        seed=rec["criterion"]["seed"])
        for key in ("n_rows_total", "n_rows_judgeable",
                    "n_rows_dropped_undecidable", "n_rows_score_missing",
                    "n_rows_scored", "n_independent_pred_dates",
                    "dropped_breakdown", "quartile_sizes_at_n10"):
            assert rec[key] == got[key], f"{col}.{key}: 产物与重算不符"
        for arm, sub in rec["arms"].items():
            gsub = got["arms"][arm]
            for k in (k for k in sub if k.startswith("Q")):
                assert sub[k]["n"] == gsub[k]["n"], f"{col}/{arm}/{k} 段人数不符"
                assert abs(sub[k]["hit_rate"] - gsub[k]["hit_rate"]) < 1e-12
            for key in ("monotone_nondecreasing", "point_biserial_rho",
                        "permutation_p_two_sided",
                        "null_abs_rho_q95_power_floor", "detectable_at_alpha05"):
                assert sub["_stats"][key] == got["arms"][arm]["_stats"][key], \
                    f"{col}/{arm}._stats.{key}: 产物与重算不符"


def test_the_verdict_is_reproducible_from_the_recorded_arms(art):
    """判决必须由**产物里记录的那份 `_stats`** 重跑出来 —— 不是读 `verdict` 字段。

    读 `verdict` 字段只能证明「字段等于它自己」（第 118 层）。
    """
    kind, why = verdict(art["by_score_col"][art["primary_score_col"]])
    assert kind == art["verdict"], (kind, art["verdict"])
    assert art["publish_allowed"] is (kind == "PASS")


# ── 探测器：四分位切法与点二列相关 ──────────────────────────────────────

def test_the_quartile_slices_within_the_group_not_globally():
    """★ 正反控：分数只在**当天那 10 只里**被用来排序 ⇒ 必须**组内**切。

    杀法不是「段要均分」（实测 n=10 给 **3/2/3/2**，不均分才是真的，
    我第一版写 `[1,1,2,2,3,3]` 是**预测**，当场红 —— 第 98 层）。
    真正的判别性质是**组内不变性**：一组分数整体偏高、另一组整体偏低，
    两组必须切出**同一个**分位形状。
    全局切片下低分那组会整组掉进 Q3/Q4 ⇒ 差一位形状。
    """
    import pandas as pd
    # ⚠️ 两组都**降序**（高分在前）—— 函数按 `ascending=False` 排名。
    #    第一版我把夹具写成升序，于是断言 `qa[0] == 1` 拿到 4：
    #    断言本身没错，**被断言的那个构造错了**（第 6 条的镜像）。
    a = pd.Series([9.9 - 0.1 * i for i in range(10)], index=list("a" * 10))
    b = pd.Series([1.0 - 0.1 * i for i in range(10)], index=list("b" * 10))
    qa, qb = _within_group_quartile(a).tolist(), _within_group_quartile(b).tolist()
    assert qa == qb, f"同构两组切出不同形状 ⇒ 不是组内切：\n{qa}\n{qb}"
    # 每组都从 Q1 起（最高分 ⇒ 编号最小），最低分段是 Q4
    assert qa[0] == 1 and qb[0] == 1, (qa, qb)
    assert set(qa) == {1, 2, 3, 4}, qa


def test_a_lower_scored_group_does_not_get_squeezed_into_the_low_quartiles():
    """反向臂：把低分组的绝对分数**压到 0**，形状必须**一个字都不变**。

    这一条不是重复上一条：上一条两组差 9 倍，本条差 90 倍，
    而真正的全局实现（`rank` 后不分组）在两种差距下**都会**漏 ——
    它测的是「分位不依赖绝对量纲」这条**性质本身**，不是某个具体夹具。
    """
    import pandas as pd
    a = pd.Series([900.0 + i for i in range(10)])
    b = pd.Series([i * 1e-9 for i in range(10)])
    assert _within_group_quartile(a).tolist() == \
        _within_group_quartile(b).tolist()


def test_the_quartile_number_never_goes_backwards_as_the_score_falls():
    """单调性：分数降序排列后，分位编号**不许回落**。

    这是外行人唯一能从表里读出的东西（四行命中率必须能按 Q1→Q4 读）；
    若它在有并列时回落，G4 的「分段单调」判据就成了随机事件。
    """
    import pandas as pd
    # 含并列：8.0 出现两次。`method="first"` 让并列按原位次拆开，
    # 而**不是**把它们塞进同一段（后者会让某段凭空多一倍样本）。
    s = pd.Series([8.0, 8.0, 7.0, 7.0, 7.0, 6.0, 6.0, 5.0, 5.0, 4.0])
    got = _within_group_quartile(s).tolist()
    assert got == sorted(got), got
    assert all(1 <= k <= 4 for k in got), got


def test_point_biserial_is_zero_when_either_side_is_constant():
    """常量 ⇒ 无关联可测。返回 0.0（不是崩，也不是 NaN 混进下游）。"""
    import numpy as np
    x = np.array([1.0, 2.0, 3.0, 4.0])
    assert _point_biserial(x, np.ones(4)) == 0.0
    assert _point_biserial(x, np.array([1.0, 0.0, 1.0, 0.0])) != 0.0


# ── 能力守卫：真实测量函数的**方向**必须与预注册一致（2026-10-20） ──────────
#
# 🚨🚨 **背景**：本文件此前的 21 条守卫里，`monotone_nondecreasing`
# **全部是夹具注入的字段**（`_stats(monotone=True, ...)`）⇒
# **`measure()` 这个真实测量函数一次都没被调用过** ⇒ 它的方向从未被验过。
# ⇒ 后果（2026-10-20 实测发现）：`_within_group_quartile` 的约定是
# **Q1 = 分数最高**（写在**另一个函数**的 docstring 里），而实现判的是
# `all(seq[i] <= seq[i+1])` ⇒ 判的是「**命中率随 score 递减**」
# ⇒ **与预注册 G4 原文相反**。
#
# ⇒ 失效方向（为什么贵）：
#   ① 真的**有排序力**的打分器 ⇒ 被判「不单调」⇒ **拦住目标信号**；
#   ② **反预测**的打分器 ⇒ 被判「单调」⇒ **放行错误信号**。
# ⇒ 今天判决没翻转**纯属数据恰好**（两个方向都不单调），不是判据正确。

def _synthetic(hit_counts: tuple[int, ...], n_per_q: int = 10):
    """造一份「分数越高命中率越高（或越低）」的横截面。

    **Q1 = 分数最高**（`_within_group_quartile` 的约定）⇒
    `hit_counts` 的第 0 位是 **Q1** 的命中数，第 3 位是 **Q4** 的。

    🚨 **分数必须按分位「递减」铺**（q=0 → 最高分）：
    第一版把 q=0 配成了**最低**分，于是 Q1 与 q=0 错位 ⇒
    两条守卫**同时红**、而报出来的序列是「好夹具」那一条 ——
    ⇒ **夹具错了，症状长得像「代码错了」**（第 98 层：先怀疑构造）。
    """
    import pandas as pd
    rows = []
    for q, nh in enumerate(hit_counts):
        for i in range(n_per_q):
            rows.append({
                "code": f"6000{q}{i:02d}.SH",
                "name": f"票{q}{i}",
                "direction": "up",
                # q=0 ⇒ 分数最高 ⇒ **递减**铺
                "score": float(-(q * n_per_q + i)),
                "result": "hit" if i < nh else "miss",
                "pred_date": "2026-10-20",
            })
    return pd.DataFrame(rows)


def test_a_scorer_whose_hit_rate_rises_with_the_score_is_judged_monotone():
    """🚨 能力守卫（正控）：**真的有排序力**的打分器必须过闸。

    预注册 G4 原文：「`score` 四分位命中率 **单调递增**」
    （docs/TODO_reverse_scoring_2026-10-15.md §3 第 128 行），
    分位约定 Q1 = 分数最高 ⇒ 「命中率随 score 递增」
    = hit_rate 随 **Q 标签**（Q1→Q4）**非递增**。
    """
    from scripts.score_ranking_power import measure
    good = measure(_synthetic((10, 5, 2, 0)), "score", n_perm=200, seed=1)
    st = good["arms"]["up"]["_stats"]
    assert st["monotone_nondecreasing"] is True, (
        "一个「分数越高命中率越高」的打分器被判成不单调 ⇒ 方向写反了；"
        f"实测序列 {st['hit_rate_sequence_q1_to_q4']}")


def test_an_inverted_scorer_is_judged_not_monotone():
    """🚨 能力守卫（反向正控）：**反预测**的打分器必须**过不了**闸。

    与上一条**互为镜像** ⇒ 方向写反时**必有一条变红**。
    """
    from scripts.score_ranking_power import measure
    bad = measure(_synthetic((0, 2, 5, 10)), "score", n_perm=200, seed=1)
    st = bad["arms"]["up"]["_stats"]
    assert st["monotone_nondecreasing"] is False, (
        "一个「分数越高命中率越低」的打分器被判成单调 ⇒ 方向写反了；"
        f"实测序列 {st['hit_rate_sequence_q1_to_q4']}")


def test_the_artifact_states_its_own_direction_instead_of_making_the_reader_infer_it():
    """🚨 方向必须是**产物的字段**，不是读者的推断题。

    本次的方向错误就是靠推断出来的：分位约定写在**另一个函数**的
    docstring 里 ⇒ 推断链跨了两个文件 ⇒ 谁也没看见。
    ⇒ 从今天起，产物必须自己说清：
    ① `quartile_label_convention`（Q1 = 分数最高）
    ② `monotone_direction`（increasing_with_score）
    ③ `monotone_test_formula`（不等式链本身）
    ④ `expected_rho_sign`（点二列相关的期望符号）
    """
    from scripts.score_ranking_power import measure
    res = measure(_synthetic((10, 5, 2, 0)), "score", n_perm=200, seed=1)
    st = res["arms"]["up"]["_stats"]
    assert st["quartile_label_convention"].startswith("Q1 = 分数最高"), st
    assert st["monotone_direction"] == "increasing_with_score", st
    assert "hit_rate(Q1) >= hit_rate(Q2)" in st["monotone_test_formula"], st
    assert st["expected_rho_sign"] == "positive", st

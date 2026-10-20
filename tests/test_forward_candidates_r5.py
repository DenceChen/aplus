"""第五批前瞻候选（2026-10-20）的注册守卫。

## 这一批为什么存在

前四批共 19 组候选**没有一组是「当前实盘那一支」**：
实盘权重来自 `aplus/evolve.py::load_weights` 的**回落分支**
（`data/reports/weights.json` 不存在 ⇒ `source="fallback"`、`caveat_kind="unraced"`），
即 `aplus/factor.py:61 DEFAULT_WEIGHTS` + `aplus/evolve.py:79 LIVE_REBALANCE_EVERY`
+ `buffer_multiple = None`。
台账第 1 批 ① 是**符号翻转之前**的旧配置，第 2/3 批那组是 **8 因子**判决权重。

⇒ 前瞻通道此刻测的不是真正在交易的那一支，而 D1 闸门判 `NOT_PASSED_POWERLESS`
⇒ 前瞻是 D1 唯一还能解锁判决的路 ⇒ 它测错对象是最贵的一处浪费。

预注册：`docs/PREREG_FORWARD_BATCH5_2026-10-20.md`
（⚠️ 那份文件明写「补记于动手当日，不是事前登记」）。

## 这个文件守什么

| 守卫 | 守的是哪一层 |
|---|---|
| 追加而非替换 | 台账**结构**（前 4 批逐字未变） |
| 权重跟着 `DEFAULT_WEIGHTS` 走 | **行为**（改符号 ⇒ 注册结果跟着变） |
| K 跟着 `LIVE_REBALANCE_EVERY` 走 | **行为** |
| 幂等 | 台账**结构** |
| seal 存在且验得过 | **冻结机制**（含篡改正控） |
| 门槛按**本份注册**的候选数推导 | **判决口径**（且后来的批次不许改动早先批次的门槛） |
| 测试不写生产路径 | **卫生**（第 132 层） |

## 两条纪律（写在这里是因为它们都踩过）

1. **不许用子串断言去查「有没有手打常量」**。
   本仓库的脚本 docstring 里**逐字引用**了权重字面量
   （`{mom20:+1.0, rev5:+0.3, ...}`），子串匹配会被注释满足
   （本仓第 13 条：子串断言 + 同文件存在注释 = 守卫可被文档满足）。
   ⇒ 这里用**行为**守卫：改掉 `aplus.factor.DEFAULT_WEIGHTS` 的符号，
   重新执行脚本模块，注册结果**必须跟着变**。

2. **不许读生产产物当判据**（本仓「读快照的守卫等于没守」）。
   生产台账只作为**输入**被复制进 `tmp_path`；
   所有断言都落在 `tmp_path` 里的那份上。
   生产产物路径**只被读**、**从不被写**。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "register_forward_candidates_r5.py"
PROD_REGISTRY = ROOT / "data" / "reports" / "forward_registry.json"


# ── 载入脚本模块 ────────────────────────────────────────────────────

def _fresh_module():
    """**重新执行**脚本的模块级代码，返回一个全新的模块对象。

    为什么要「重新执行」而不是 `import`：
    候选清单是**模块级**由 `DEFAULT_WEIGHTS` / `LIVE_REBALANCE_EVERY`
    构造出来的 ⇒ 只有重跑模块级代码，才知道它读的是**当前**的源码常量。
    这正是「权重不是手打字面量」这条守卫的量具。
    """
    spec = importlib.util.spec_from_file_location(
        "_r5_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _read(path: Path):
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


# ── 前提：实盘那一支到底是什么 ──────────────────────────────────────

def test_the_live_configuration_is_the_fallback_and_the_fallback_is_default_weights(tmp_path):
    """**前提守卫**：实盘跑的是 `DEFAULT_WEIGHTS` + K=21 + **无缓冲**。

    用 `tmp_path`（里面没有 `weights.json`）走 `load_weights` 的回落分支，
    于是这条**不依赖生产状态** —— 将来生产真的写了 `weights.json`，
    这条仍然绿（而那时**必须开第 6 批**，本批冻结的是今天这一支）。

    前提错了，这一整个批次就测错了对象 ⇒ 所以它必须先被钉住。
    """
    from aplus.evolve import LIVE_REBALANCE_EVERY, load_weights
    from aplus.factor import DEFAULT_WEIGHTS

    got = load_weights(tmp_path)
    assert got["source"] == "fallback", (
        "实盘不走回落分支了 ⇒ 本批冻结的「实盘那一支」已经不是真正在跑的那一支 "
        "⇒ 必须开第 6 批，而不是改本批"
    )
    assert got["caveat_kind"] == "unraced"
    assert got["weights"] == dict(DEFAULT_WEIGHTS)
    assert got["execution"]["rebalance_every"] == LIVE_REBALANCE_EVERY
    assert got["execution"]["buffer_multiple"] is None


# ── 候选清单本身 ────────────────────────────────────────────────────

def test_the_three_candidates_are_the_live_config_plus_two_single_parameter_arms(tmp_path):
    from aplus.evolve import LIVE_REBALANCE_EVERY
    from aplus.factor import DEFAULT_WEIGHTS
    from aplus.forward import load_registry
    from aplus.rebalance import BUFFER_MULTIPLE

    mod = _fresh_module()
    mod.main(tmp_path)
    rec = load_registry(tmp_path)[-1]
    cands = rec["candidates"]
    assert len(cands) == 3

    c1, c2, c3 = cands
    # ① 当时的实盘那一支（K=21，**显式钉**）
    # ⚠️ 2026-10-21：实盘换仓周期改成了 63 ⇒ ① 降级为**对照臂**，
    # **② 才是现在实盘那一支**。而本批**已冻结带 seal** ⇒ ①/③ 的 K
    # 必须**显式钉 21**，**不许**再从 `LIVE_REBALANCE_EVERY` 派生
    # （派生 ⇒ 重跑把 ① 变成 63 ⇒ 与 ② 撞成同一支 ⇒ 批次语义被改写）。
    assert c1["weights"] == dict(DEFAULT_WEIGHTS)
    assert c1["rebalance_every"] == 21
    assert c1["buffer_multiple"] is None
    # ② 成本臂：只改 K —— ⚠️ **它现在是实盘那一支**（K=63）
    assert c2["weights"] == dict(DEFAULT_WEIGHTS)
    assert c2["rebalance_every"] == 63
    assert c2["buffer_multiple"] is None
    assert c2["rebalance_every"] == LIVE_REBALANCE_EVERY, (
        "实盘的换仓周期与本批 ② 必须一致 ⇒ 前瞻证据测的必须就是实盘那一支")
    # ③ 单参数对照：只改缓冲
    assert c3["weights"] == dict(DEFAULT_WEIGHTS)
    assert c3["rebalance_every"] == 21   # 显式钉（冻结），不从 LIVE 派生
    assert c3["buffer_multiple"] == BUFFER_MULTIPLE

    # 三组的权重**完全相同** ⇒ 任何两组的差别都是单参数的（受控对照）
    assert len({json.dumps(c["weights"], sort_keys=True) for c in cands}) == 1
    # 且两两不同 ⇒ 不会在台账里被去重吃掉
    assert len({(c["rebalance_every"], c["buffer_multiple"]) for c in cands}) == 3


def test_the_registered_weights_follow_default_weights_when_the_sign_flips(tmp_path, monkeypatch):
    """**行为守卫**：权重是从 `DEFAULT_WEIGHTS` import 的，**不是手打字面量**。

    把 `aplus.factor.DEFAULT_WEIGHTS` 的符号整体翻掉，重新执行脚本模块
    ⇒ 注册出来的权重**必须跟着翻**。
    若脚本里是手写字面量，这一条立刻红（且红得对）。

    ⚠️ 正控：断言前先确认「翻掉的确实不同于原来」——
    否则一个空的对照会让这条守卫变成恒真。
    """
    import aplus.factor

    original = dict(aplus.factor.DEFAULT_WEIGHTS)
    flipped = {k: -v for k, v in original.items()}
    assert flipped != original, "正控失败：翻号没有改变任何东西"

    monkeypatch.setattr(aplus.factor, "DEFAULT_WEIGHTS", flipped)
    mod = _fresh_module()
    mod.main(tmp_path)

    from aplus.forward import load_registry
    rec = load_registry(tmp_path)[-1]
    got = rec["candidates"][0]["weights"]
    assert got == flipped, (
        "注册结果没有跟着 DEFAULT_WEIGHTS 走 ⇒ 脚本里是手写字面量，"
        "而不是 import 出来的（手打的那份是 2026-10-14 符号翻转前的化石）"
    )
    assert got != original


def test_the_registered_k_does_NOT_follow_live_rebalance_every(tmp_path, monkeypatch):
    """★ **反向**行为守卫：改 `LIVE_REBALANCE_EVERY` ⇒ 本批的 ①/③ **不许**跟着变。

    ## 为什么这条要**反过来**（2026-10-21）

    旧版叫 `..._follows_live_rebalance_every`，断言「K 从 LIVE import ⇒ 跟着变」。
    而本批**已冻结并带 seal**（注册于 2026-10-20）⇒
    跟着变就意味着**重跑会改写一个已冻结的批次**（① 会从 21 变成 63
    ⇒ **与 ② 撞成同一支** ⇒ 这一批的语义被改写 ⇒ 第 25 层）。

    ⇒ 冻结的批次只能**复现**，不能**改写** ⇒ ①/③ 的 K **显式钉 21**。
    ⇒ 而「实盘那一支」现在由 **②（K=63）** 承担 ⇒ 它的 embargo 起点
    （2026-10-23）与新配置上线同步 ⇒ **前瞻证据一天都不浪费**。

    ⇒ 这条守卫现在守的是**冻结纪律**，不是「跟着 LIVE 变」。
    """
    import aplus.evolve

    original = aplus.evolve.LIVE_REBALANCE_EVERY
    assert original != 10, "正控失败：对照值与原值相同"
    monkeypatch.setattr(aplus.evolve, "LIVE_REBALANCE_EVERY", 10)

    mod = _fresh_module()
    mod.main(tmp_path)

    from aplus.forward import load_registry
    rec = load_registry(tmp_path)[-1]
    c1, c2, c3 = rec["candidates"]
    # 🚨 改了 LIVE 也**不许**变 —— 这正是「冻结」的意思
    assert c1["rebalance_every"] == 21, (
        f"① 的 K 跟着 LIVE 变了 ⇒ 已冻结的批次被改写（第 25 层）")
    assert c3["rebalance_every"] == 21, (
        f"③ 的 K 跟着 LIVE 变了 ⇒ 已冻结的批次被改写（第 25 层）")
    # ⇒ 而「实盘那一支」是 ②
    assert c2["rebalance_every"] == 63


# ── 追加式：前 4 批一个字都不许变 ──────────────────────────────────

def test_batch5_is_appended_and_the_earlier_registrations_are_byte_identical(tmp_path):
    """第 5 批是**追加**，不是替换。

    把**生产台账**（只读）复制进 `tmp_path`，再跑第五批的注册，
    然后逐字比对：先前那些注册**一个字都没变**，而新的一份被追加在后面。
    """
    from aplus.forward import _batch_identity, load_registry

    mod = _fresh_module()
    prod_rows = _read(PROD_REGISTRY) or []
    target = _batch_identity(mod.ON, mod.CANDS)
    prior = [r for r in prod_rows
             if _batch_identity(r.get("registered_on") or "",
                                r.get("candidates") or []) != target]
    if prod_rows:
        assert len(prior) >= 4, (
            f"生产台账里应当至少有前 4 批（{len(prod_rows)} 份里只认出 {len(prior)} 份）"
        )

    (tmp_path / "forward_registry.json").write_text(
        json.dumps(prior, ensure_ascii=False, indent=2), encoding="utf-8")
    before = json.dumps(prior, ensure_ascii=False, sort_keys=True)

    mod.main(tmp_path)

    after = load_registry(tmp_path)
    assert len(after) == len(prior) + 1, "新的一份没有被追加进去"
    assert json.dumps(after[:len(prior)], ensure_ascii=False,
                      sort_keys=True) == before, (
        "已有注册被改写了——前瞻台账一旦可改就不再是前瞻"
    )
    assert after[-1]["registered_on"] == mod.ON


def test_registering_twice_does_not_double_the_candidate_count(tmp_path):
    """**幂等**：同一天重跑不许重复注册。

    重复注册会让候选数虚增 ⇒ Bonferroni 门槛被自己抬高，
    而台账看上去只是「多了一批」——没人说得清多出来的是什么。
    """
    from aplus.forward import load_registry

    mod = _fresh_module()
    mod.main(tmp_path)
    first = load_registry(tmp_path)
    mod.main(tmp_path)
    second = load_registry(tmp_path)

    assert len(first) == len(second) == 1
    assert len(second[0]["candidates"]) == 3
    assert second[0] == first[0], "第二次跑改写了第一次的内容"


# ── 冻结指纹 ────────────────────────────────────────────────────────

def test_the_seal_exists_and_verifies_through_a_plus_forward(tmp_path):
    from aplus.forward import SEAL_FIELD, compute_seal, load_registry
    from aplus.forward import registry_tamper_report

    mod = _fresh_module()
    mod.main(tmp_path)
    rec = load_registry(tmp_path)[-1]

    assert rec.get(SEAL_FIELD), "注册没有落下封印"
    assert compute_seal(rec) == rec[SEAL_FIELD], "封印与内容对不上"

    rep = registry_tamper_report(tmp_path)
    assert rep["tampered"] == []
    assert rep["unsealed"] == []
    assert rep["sealed"] == 1


def test_the_seal_actually_covers_the_weights(tmp_path):
    """**篡改正控**：把权重改掉之后，封印**必须**验不过。

    没有这一条，「封印存在」只是一条恒真断言 ——
    它不区分「封印覆盖了候选身份」与「封印是个常量」。
    （本仓铁律：守卫只有变异后算数。）
    """
    from aplus.forward import compute_seal, load_registry
    from aplus.forward import registry_tamper_report

    mod = _fresh_module()
    mod.main(tmp_path)
    rec = load_registry(tmp_path)[-1]
    assert registry_tamper_report(tmp_path)["tampered"] == []

    rec["candidates"][0]["weights"] = {"mom20": 99.0}
    assert compute_seal(rec) != rec["sealed_sha256"], (
        "改了权重封印还验得过 ⇒ 封印没覆盖候选身份"
    )

    path = tmp_path / "forward_registry.json"
    path.write_text(json.dumps([rec], ensure_ascii=False, indent=2),
                    encoding="utf-8")
    assert registry_tamper_report(tmp_path)["tampered"] == [0]


# ── 门槛口径：分别算，且由候选数推导 ────────────────────────────────

def test_the_threshold_is_derived_from_this_registrations_candidate_count(tmp_path):
    """门槛由**本份注册**的候选数推导，不许手打。

    判据是**行为**：把 `forward_status` 报出来的 `t_crit`
    与 `bonferroni_t(本批候选数, 自由度)` 逐位对拍。
    """
    from aplus.evolve_race import bonferroni_t
    from aplus.forward import MIN_TEST_PERIODS, forward_status, load_registry

    mod = _fresh_module()
    mod.main(tmp_path)
    rec = load_registry(tmp_path)[-1]
    n = len(rec["candidates"])
    assert n == 3

    st = forward_status(tmp_path, as_of=mod.ON, dates=[])
    detail = st["registrations_detail"][-1]
    for row in detail["candidates"]:
        df = max(row["test_periods"], MIN_TEST_PERIODS) - 1
        assert row["t_crit"] == bonferroni_t(n, df), (
            "t_crit 与「由本批候选数推导」的结果对不上"
        )


def test_a_later_registration_does_not_move_an_earlier_ones_threshold(tmp_path):
    """**分别算**的决定性证据：后来的一批不许改动早先批次的门槛。

    若门槛按全台账候选数算（合并算），每加一批，
    前面的批次门槛就变一次 ⇒ **冻结批次的判决口径被后来者改写** ⇒
    违反「注册即冻结」。

    这条同时钉住 `aplus/forward.py::forward_status` 的口径
    （`t_crit = bonferroni_t(len(raw), ...)`，按**本份注册**的候选数）。
    """
    from aplus.evolve_race import bonferroni_t
    from aplus.forward import MIN_TEST_PERIODS, forward_status, register

    mod = _fresh_module()
    mod.main(tmp_path)
    before = forward_status(tmp_path, as_of=mod.ON, dates=[])[
        "registrations_detail"][0]

    register(tmp_path, "2026-11-01",
             [{"weights": {"mom20": 1.0}, "rebalance_every": 5,
               "buffer_multiple": None}],
             note="后来的一批（候选数不同）")

    after = forward_status(tmp_path, as_of="2026-11-20", dates=[])[
        "registrations_detail"][0]
    assert after["candidates"][0]["t_crit"] == before["candidates"][0]["t_crit"], (
        "后来的一批把早先批次的门槛改了 ⇒ 门槛是按全台账算的，"
        "而那会让冻结批次的判决口径随后来者漂移"
    )
    # 正控：合并算确实会给出不同的数 ⇒ 上面那条不是恒真
    df = max(before["candidates"][0]["test_periods"], MIN_TEST_PERIODS) - 1
    assert bonferroni_t(4, df) != before["candidates"][0]["t_crit"]


# ── 批次身份的全序：一份注册里两个候选只差缓冲倍数 ──────────────────

def test_the_raw_candidate_keys_cannot_be_ordered_against_each_other():
    """**正控**：① 与 ③ 的身份三元组**天然不可比**。

    `candidate_key` 的第三位是 `buffer_multiple`：`None` vs `float`
    ⇒ 直接比大小会抛 `TypeError`。
    这条把「为什么需要 `_identity_sort_key`」钉成一个会响的事实，
    而不是文档里的一句话。
    """
    import pytest

    from aplus.evolve import candidate_key

    ka = candidate_key({"weights": {"mom20": -1.0}, "rebalance_every": 21,
                        "buffer_multiple": None})
    kb = candidate_key({"weights": {"mom20": -1.0}, "rebalance_every": 21,
                        "buffer_multiple": 3.0})
    with pytest.raises(TypeError):
        _ = ka < kb


def test_a_registration_may_contain_a_buffer_and_a_non_buffer_variant(tmp_path):
    """**主守卫**：同一份注册里两个候选**只差缓冲倍数**必须能注册。

    这不是假想：第 5 批的 ①（K=21、无缓冲）与 ③（K=21、缓冲 3.0）
    权重与调仓频率完全相同 ⇒ 批次身份排序必须能比较第三位。

    没有这条，第 5 批的注册**根本写不进去**
    （实测：`TypeError: '<' not supported between instances of
    'NoneType' and 'float'`，来自 `aplus/forward.py::_batch_identity`）。
    """
    from aplus.forward import load_registry, register

    rec = register(tmp_path, "2026-10-20",
                   [{"weights": {"mom20": -1.0}, "rebalance_every": 21,
                     "buffer_multiple": None},
                    {"weights": {"mom20": -1.0}, "rebalance_every": 21,
                     "buffer_multiple": 3.0}],
                   note="只差缓冲倍数的一对")
    assert len(rec["candidates"]) == 2
    assert len(load_registry(tmp_path)) == 1


def test_the_sort_key_fix_does_not_move_the_registrations_that_had_no_collision():
    """**行为保持**：修排序键不许改动**当前能跑通**的那些批次的身份。

    自然序（`sorted(keys)`）只在没有碰撞时才可用 ⇒ 它同时是
    「这批有没有碰撞」的探针：

    - 自然序能跑 ⇒ 旧代码在这批上**本来就是对的** ⇒ 新旧次序必须**逐位相同**；
    - 自然序抛 `TypeError` ⇒ 这批**含碰撞**（第 5 批的 ① 与 ③：权重与 K 相同、
      只差缓冲倍数）⇒ 旧代码在这批上**本来就崩** ⇒ 这里只要求新键给出
      **良定义、与输入顺序无关**的次序。

    ⚠️ 这条测试**必须**分两支写：写成「所有批次都必须 `sorted(keys)` 成功」
    的话，在第 5 批注册**之后**它会**永远红**（而红的原因不是守卫坏了，
    是判据把「有碰撞的批次」也算成了「本来就该能排」的批次）
    —— 永远红的守卫会把人推向错误的修法。
    """
    import random

    from aplus.evolve import candidate_key
    from aplus.forward import _identity_sort_key

    rows = _read(PROD_REGISTRY) or []
    for rec in rows:
        keys = [candidate_key(c) for c in (rec.get("candidates") or [])]
        new = sorted(keys, key=_identity_sort_key)
        try:
            old = sorted(keys)
        except TypeError:
            # 含碰撞的批次：旧代码在这批上本来就崩 ⇒ 只需良定义 + 与顺序无关
            shuffled = list(keys)
            random.Random(0).shuffle(shuffled)
            assert sorted(shuffled, key=_identity_sort_key) == new, (
                "排序结果依赖输入顺序 ⇒ 批次身份不再是「与顺序无关」的"
            )
            for a, b in zip(new, new[1:]):
                if a[:2] == b[:2]:
                    assert a[2] is None, (
                        "无缓冲的候选必须排在有缓冲的前面"
                        "（见 aplus/forward.py::_identity_sort_key 的 docstring）"
                    )
            continue
        assert new == old, (
            "排序键改动让已有批次的身份次序变了 ⇒ 改动不是行为保持的"
        )


# ── 卫生：测试不许写生产产物路径 ────────────────────────────────────

def test_running_the_registration_never_touches_the_production_registry(tmp_path):
    """本仓第 132 层：测试把自己的输出写进生产路径，再让新加的章替它洗成合法。

    生产台账**只被读**；注册必须落在 `tmp_path`。
    这条把「不许写生产路径」变成会响的东西，而不是靠人记得。
    """
    before = PROD_REGISTRY.read_bytes() if PROD_REGISTRY.exists() else None

    mod = _fresh_module()
    mod.main(tmp_path)
    assert (tmp_path / "forward_registry.json").exists(), (
        "注册没有落到 tmp_path ⇒ main() 的 reports_dir 参数没生效"
    )

    after = PROD_REGISTRY.read_bytes() if PROD_REGISTRY.exists() else None
    assert before == after, "测试把生产台账改了"

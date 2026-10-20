"""跌腿闸门（G4 镜像）的守卫。

产物：`data/reports/downleg_gate.json`（**新文件**；`downleg_ranking.json` 是对照臂/诊断）。
预注册：`docs/PREREG_DOWNLEG_GATE_2026-10-20.md`（**写在跑数之前**）。
实现：`scripts/measure_downleg_ranking.py` 的闸门块。

## 本文件守的四件事

1. **方向镜像是真的**（最承重）：跌腿判「命中率随 score 升高而**下降**」。
   方向写反 ⇒ 判据整个反过来，而它**不崩、不报错**，只是默默改判决。
2. **三态判决各自可达**（分岔夹具，**不靠真实产物**）。
3. **检出力下限来自置换零分布**（不是手打的数）。
4. **产物里每个数与从真实输入重算的结果一致**（第 118 层：
   读落盘 json 等于没问）。

## ⚠️ 已知边界（写在脸上）

- **产物层守卫天然只对历史快照有效**（第 96 层）：`verification.parquet` 是
  **追加式**底稿 ⇒ 底稿一追加，本文件的「对账」那条会红 ⇒ **那是对的**，
  处置是重跑 `scripts/measure_downleg_ranking.py`，不是改判据。
- 涨腿臂 `scripts/score_ranking_power.py` 的**方向口径**与生产语义相反
  （见产物 `up_arm_orientation_note` 与预注册 §2.4）⇒ **本文件不许改它**，
  也不在它身上写「把缺陷钉成期望状态」的测试（第 112 层）。

## 变异台

`tests/mutation_specs/test_downleg_gate.mut` —— 方向写反 / 三态塌成两态 /
检出力下限改成手打常数 等；复跑：

```bash
.venv/bin/python scripts/mutation_harness.py \
    tests/mutation_specs/test_downleg_gate.mut --node tests/test_downleg_gate.py
```
（`specs` 是**位置参数**；`rc=1` 才算被杀。）
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

# 🚨 对账守卫必须与**生产者**读**同一个口径**的样本。
from aplus.signal_scope import scoped_ledger_frame  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "measure_downleg_ranking.py"
ART = ROOT / "data" / "reports" / "downleg_gate.json"
VERIFY = ROOT / "data" / "reports" / "verification.parquet"
JUDGEABLE = ("hit", "miss")


def _load():
    spec = importlib.util.spec_from_file_location("_downleg_gate_arm", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_downleg_gate_arm"] = mod
    spec.loader.exec_module(mod)
    return mod


M = _load()
# 🚨 **同源**取法：涨腿臂的模块对象**必须**是实现自己 import 的那一个 ——
#    否则 `M._within_group_quartile is SRP._within_group_quartile` 恒为假
#    （`scripts.score_ranking_power` 与 `score_ranking_power` 是两个模块对象）。
SRP = sys.modules["score_ranking_power"]
SRC = SCRIPT.read_text(encoding="utf-8")


# ── 夹具 ────────────────────────────────────────────────────────────

def _frame(rows, with_entry_date: bool = True, pred_dates=None) -> pd.DataFrame:
    """rows = [(score, result), …]。

    🚨 **默认每个 `pred_date` 8 行**：`_within_group_quartile` 在 n=8 时切出
       **2/2/2/2**（只有 n 能被 4 整除才均衡）⇒ 四个分位都存在，
       单调性判据才有四段可比。
    ⚠️ n<4 的组会**少一个分位** ⇒ 判据退化成三段 ⇒ 所以夹具不许用小组。
    """
    n = len(rows)
    dates = pred_dates or [f"2026-10-{12 + (i // 8):02d}" for i in range(n)]
    df = pd.DataFrame({
        "pred_date": dates[:n],
        "direction": ["down"] * n,
        "result": [r for _, r in rows],
        "score": [float(s) for s, _ in rows],
    })
    if with_entry_date:
        df["entry_date"] = ["2026-10-13"] * n
    return df


#: 跌腿的**正确**排序：**低分 ⇒ 更可能真的跌**（`hit` = 真的跌了）。
#: ⇒ 命中率随 score 升高而**下降** ⇒ 判据必须成立。
GOOD_ROWS = [(8, "miss"), (7, "miss"), (6, "miss"), (5, "miss"),
             (4, "hit"), (3, "hit"), (2, "hit"), (1, "hit")] * 2
#: **反向**梯度：高分 ⇒ 更可能真的跌 ⇒ 判据必须**不**成立。
BAD_ROWS = [(8, "hit"), (7, "hit"), (6, "hit"), (5, "hit"),
            (4, "miss"), (3, "miss"), (2, "miss"), (1, "miss")] * 2


def _expected_mono_decreasing_in_score(df: pd.DataFrame) -> bool:
    """★ **独立重算**：按 score 升序，命中率必须**非递增**。

    🚨 这是**另一条推导路径**（直接按分数排序，不经分位标签）⇒
       它与实现的「四分位 + 反序」是**两条不同的路** ⇒ 才能当外部锚。
       写成「把实现的输出断言等于自己」是**恒真的安慰剂**（第 5 种形状）。
    """
    rows = []
    for _, g in df.groupby("pred_date"):
        gg = g.sort_values("score", ascending=True)
        rates = [(float(s), float(r == "hit")) for s, r
                 in zip(gg["score"], gg["result"])]
        # 分位级：把组内按分数四等分后，每段的命中率
        n = len(rates)
        buckets: dict[int, list] = {}
        for i, (s, hit) in enumerate(rates):
            b = min(3, (i * 4) // n)
            buckets.setdefault(b, []).append(hit)
        rates_by_bucket = [sum(buckets[b]) / len(buckets[b]) for b in sorted(buckets)]
        rows.append(rates_by_bucket)
    return all(all(r[i] >= r[i + 1] for i in range(len(r) - 1)) for r in rows)


def _stats(monotone: bool, rho: float, p: float, q95: float) -> dict:
    """分岔夹具用的**判决输入**（形状与 `gate_measure` 产出的 `_stats` 一致）。"""
    return {
        "monotone_decreasing_in_score": monotone,
        "monotone_direction": "decreasing",
        "point_biserial_rho": rho,
        "permutation_p_two_sided": p,
        "null_abs_rho_q95_power_floor": q95,
        "detectable_at_alpha05": bool(abs(rho) >= q95),
    }


# ── ① 方向镜像（最承重） ────────────────────────────────────────────

def test_the_down_gate_judges_hit_rate_falling_as_score_rises():
    """★ 跌腿的判据是「命中率随 score 升高而**下降**」。

    方向写反的后果**不崩、不报错**：判据整个反过来，判决默默改。
    """
    res = M.gate_measure(_frame(GOOD_ROWS), n_perm=50, seed=1)
    st = res["quartiles"]["_stats"]
    assert st["monotone_decreasing_in_score"] is True, (
        "低分 ⇒ 更可能真的跌（跌腿的正确排序）却判成不单调 ⇒ 方向写反了")
    assert _expected_mono_decreasing_in_score(_frame(GOOD_ROWS)) is True

    res2 = M.gate_measure(_frame(BAD_ROWS), n_perm=50, seed=1)
    assert res2["quartiles"]["_stats"]["monotone_decreasing_in_score"] is False, (
        "高分 ⇒ 更可能真的跌 却判成单调 ⇒ 方向写反了")
    assert _expected_mono_decreasing_in_score(_frame(BAD_ROWS)) is False


def test_the_direction_is_explicit_in_the_code_and_in_the_artifact(art):
    """🚨 方向必须**显式**写出来，不许让读者猜（第 179 层：键名与内容互相打脸）。

    ⚠️ 特别地：本闸门在 **Q 标签上**（Q1=最高分）的代数形式与涨腿臂
       `all(seq[i] <= seq[i+1])` **逐字相同、含义相反** ⇒ 读者若靠
       「代码长得像」去推断方向，**一定会读反** ⇒ 所以字段必须写全。
    """
    assert M.MONOTONE_DIRECTION == "decreasing"
    assert art["monotone_direction"] == "decreasing"
    assert "score" in art["monotone_direction_means"]
    assert "Q1" in art["quartile_label_convention"] and "最高分" in art["quartile_label_convention"]
    assert "非递增" in art["monotone_test"]
    st = art["measurement"]["quartiles"]["_stats"]
    assert st["monotone_direction"] == "decreasing"
    assert st["expected_rho_sign"] == "negative", (
        "跌腿的 ρ 期望符号必须是**负**（分越高越不容易跌）—— "
        "它是与涨腿臂**镜像**的那一半")
    # 判据读的那条序列必须真的存在，且是**score 升序**
    assert st["hit_rate_sequence_by_score_ascending"] == \
        list(reversed(st["hit_rate_sequence_q1_to_q4"]))


def test_the_quartiles_are_cut_within_each_group_not_globally():
    """🚨 四分位必须**组内**切（`(pred_date, direction)`），不全局切。

    夹具**刻意**让两种切法给出**相反**的答案：
    两组的分数区间**不重叠**（一组 100..93、一组 8..1），
    且两组的「低分 ⇒ 更可能命中」方向一致 ⇒ 组内切 ⇒ 判据成立；
    全局切 ⇒ 两组被混进同一条排序里 ⇒ 判据不成立。
    ⇒ 全局切的变异体在这条上**当场变红**。
    """
    group_hi = [(100, "miss"), (99, "miss"), (98, "miss"), (97, "miss"),
                (96, "miss"), (95, "miss"), (94, "hit"), (93, "hit")]
    group_lo = [(8, "miss"), (7, "miss"), (6, "miss"), (5, "miss"),
                (4, "miss"), (3, "miss"), (2, "hit"), (1, "hit")]
    rows = group_hi + group_lo
    dates = ["2026-10-12"] * 8 + ["2026-10-13"] * 8
    df = _frame(rows, pred_dates=dates)
    res = M.gate_measure(df, n_perm=50, seed=1)
    assert res["quartiles"]["_stats"]["monotone_decreasing_in_score"] is True, (
        "组内切时两组的低分端都该是命中率最高的一端 ⇒ 判据必须成立；"
        "不成立 ⇒ 四分位被切成全局的了")
    assert _expected_mono_decreasing_in_score(df) is True


def test_the_down_gate_does_not_reuse_the_up_arms_guard_direction():
    """🚨 涨腿臂的守卫**不能直接套用**（第 143 层）。

    本条只钉**本产物自己的**方向声明；涨腿臂自身的方向口径问题
    （见产物 `up_arm_orientation_note`）**本轮不许改** ⇒ 不在它身上写
    「把缺陷钉成期望状态」的测试（第 112 层）。
    """
    assert M.MONOTONE_DIRECTION != "increasing"
    assert "decreasing" in M.MONOTONE_TEST
    # 判据里出现的是「score 升序序列非递增」这句话本身
    assert "非递增" in M.MONOTONE_TEST and "score 升序" in M.MONOTONE_TEST


# ── ② 三态判决各自可达 ──────────────────────────────────────────────

def test_all_three_verdicts_are_reachable_from_forked_fixtures():
    """★ 三态**各自可达**（分岔夹具，**不靠真实产物**）。

    ⚠️ 真实产物只可能落在其中一态 ⇒ 用它当「可达性」证据是**恒真的**。
    """
    kind_pass, why_pass = M.gate_verdict(_stats(True, -0.5, 0.001, 0.3))
    kind_pw, why_pw = M.gate_verdict(_stats(False, -0.1, 0.5, 0.35))
    kind_md, why_md = M.gate_verdict(_stats(False, -0.6, 0.01, 0.35))
    assert kind_pass == "PASS"
    assert kind_pw == "NOT_PASSED_POWERLESS"
    assert kind_md == "NOT_PASSED_MEASURED"
    assert len({kind_pass, kind_pw, kind_md}) == 3
    assert len({why_pass, why_pw, why_md}) == 3, "三态的**理由**必须各不相同"
    assert all(w for w in (why_pass, why_pw, why_md)), "理由不许是空串"


def test_the_two_not_passed_states_mean_opposite_next_steps():
    """两支 `NOT_PASSED_*` **都**不许发布，但下一步动作**相反**。

    ⇒ 合并成两态就把唯一有用的那部分信息丢了
    （`docs/TODO_reverse_scoring_2026-10-15.md` §7 的三态表）。
    """
    _, why_pw = M.gate_verdict(_stats(False, -0.1, 0.5, 0.35))
    _, why_md = M.gate_verdict(_stats(False, -0.6, 0.01, 0.35))
    assert "攒前瞻期" in why_pw
    assert "换结构" in why_md
    assert "攒前瞻期" not in why_md and "换结构" not in why_pw


def test_the_verdict_names_are_the_same_as_the_up_arms():
    """🚨 三态**同名同义**（与涨腿臂逐字一致）。

    同名才能让两半的读数放在一张表里比较；不同名则读者要**猜**对应关系。
    """
    up_src = (ROOT / "scripts" / "score_ranking_power.py").read_text(encoding="utf-8")
    pairs = {"VERDICT_PASS": "PASS",
             "VERDICT_POWERLESS": "NOT_PASSED_POWERLESS",
             "VERDICT_MEASURED": "NOT_PASSED_MEASURED"}
    for attr, name in pairs.items():
        assert getattr(M, attr) == name, f"{attr} 不等于 {name!r}"
        assert f'"{name}"' in up_src, f"涨腿臂里找不到 {name!r} ⇒ 不是同名"


def test_neither_not_passed_state_publishes_the_avoidance_list(art):
    """两支 `NOT_PASSED_*` **都**不许发布 —— 这是闸门，不是评分。"""
    if art["verdict"].startswith("NOT_PASSED"):
        assert art["avoidance_list_publish_allowed"] is False, (
            f"判决是 {art['verdict']} 却 avoidance_list_publish_allowed="
            f"{art['avoidance_list_publish_allowed']} ⇒ 闸门被放行了")


def test_the_publish_flag_has_a_different_name_than_the_up_arms():
    """🚨 字段名不许撞：涨腿的 `publish_allowed` = 「该**买**」，
    跌腿的是「该**回避**」⇒ 语义不同，名字必须能区分（第 179 层）。"""
    assert "avoidance_list_publish_allowed" in ART.read_text(encoding="utf-8")
    assert "publish_allowed" not in json.loads(ART.read_text(encoding="utf-8")), (
        "产物里出现了 `publish_allowed` ⇒ 与涨腿臂同名的字段，"
        "读的人会以为两者语义相同")
    assert "publish_allowed" not in M.gate_payload(
        pd.read_parquet(VERIFY), n_perm=50, seed=1)


# ── ③ 检出力下限来自置换零分布 ──────────────────────────────────────

def test_the_power_floor_is_the_permutation_null_quantile_not_a_literal(art):
    """★ 检出力下限**必须**由置换零分布算出来，不许是手打的常数。

    两条证据：① 与**同 seed 同 n_perm** 的重算**逐位相等**；
    ② **换种子会变** ⇒ 它是算出来的量，不是写死的数。
    """
    df = scoped_ledger_frame(ROOT / "data" / "reports", VERIFY)
    crit = art["criterion"]
    leg = df[(df["direction"] == "down") & df["result"].isin(JUDGEABLE)
             & df["score"].notna()]
    x = leg["score"].to_numpy(dtype=float)
    y = (leg["result"] == "hit").to_numpy(dtype=float)
    groups = SRP._groups_by(leg, "pred_date")
    p, q95 = SRP._permutation_p(x, y, groups, SRP._point_biserial,
                                 crit["n_perm"], crit["seed"])
    st = art["measurement"]["quartiles"]["_stats"]
    assert st["null_abs_rho_q95_power_floor"] == q95, (
        "产物里的检出力下限与「同 seed 同 n_perm」的重算不符 ⇒ "
        "它不是这份样本的置换零分布 95 分位")
    assert st["permutation_p_two_sided"] == p
    _, q95_other = SRP._permutation_p(x, y, groups, SRP._point_biserial,
                                       crit["n_perm"], crit["seed"] + 1)
    assert q95_other != q95, (
        "换种子后检出力下限一模一样 ⇒ 它是个写死的常数，不是算出来的")


def test_the_power_floor_semantics_is_the_null_quantile_not_the_observed_rho():
    """检出力下限的含义：**这份样本分不出比它更小的关联**。"""
    st = M.gate_measure(pd.read_parquet(VERIFY), n_perm=200, seed=1)
    stats = st["quartiles"]["_stats"]
    assert 0.0 < stats["null_abs_rho_q95_power_floor"] <= 1.0
    assert stats["detectable_at_alpha05"] == (
        abs(stats["point_biserial_rho"]) >= stats["null_abs_rho_q95_power_floor"])


def test_the_statistics_are_reused_from_the_up_arm_not_copied():
    """🚨 **同源才有可比性**：本闸门用的就是涨腿臂那**同一个对象**。

    复制一份的话两份会悄悄漂移，而漂移**没有任何东西会红**（第 97 层）。
    """
    for name in ("_within_group_quartile", "_point_biserial", "_permutation_p",
                 "_groups_by", "_independence_caliber"):
        assert getattr(M, name) is getattr(SRP, name), (
            f"{name} 不是涨腿臂那一个 ⇒ 判据实现被复制了一份（口径会漂移）")
    assert M.GATE_JUDGEABLE == tuple(SRP.JUDGEABLE)


# ── ④ 对账：从真实输入重算，逐位相等（第 118 层） ────────────────────

@pytest.fixture(scope="module")
def art() -> dict:
    assert ART.exists(), (
        f"{ART.name} 不存在 ⇒ 跌腿**有单无判**这个状态还没被打破。"
        "跑 `.venv/bin/python scripts/measure_downleg_ranking.py`")
    return json.loads(ART.read_text(encoding="utf-8"))


def test_the_gate_artifact_reconciles_with_a_fresh_measurement(art):
    """★ 同 seed 同 n_perm ⇒ **逐位可重算**。产物与重算的每一项都必须相等。

    这是唯一一条能让**源码变异**当场变红的守卫：产物层读 json，
    跑完之后才与代码有关（第 96 / 118 层）。本条在测试里**现算一遍**。
    """
    df = scoped_ledger_frame(ROOT / "data" / "reports", VERIFY)
    crit = art["criterion"]
    got = M.gate_measure(df, art["score_col"], n_perm=crit["n_perm"],
                          seed=crit["seed"])
    rec = art["measurement"]
    for key in ("n_rows_total", "n_rows_judgeable", "n_rows_dropped_undecidable",
                "n_rows_score_missing", "n_rows_scored", "n_independent_pred_dates",
                "dropped_breakdown", "arm", "score_col"):
        assert rec[key] == got[key], f"measurement.{key}: 产物与重算不符"
    for k in (k for k in rec["quartiles"] if k.startswith("Q")):
        assert rec["quartiles"][k]["n"] == got["quartiles"][k]["n"], f"{k} 段人数不符"
        assert rec["quartiles"][k]["hit_rate"] == got["quartiles"][k]["hit_rate"], \
            f"{k} 段命中率与重算不符"
    for key in ("hit_rate_sequence_q1_to_q4", "hit_rate_sequence_by_score_ascending",
                "monotone_decreasing_in_score", "monotone_direction",
                "point_biserial_rho", "permutation_p_two_sided",
                "null_abs_rho_q95_power_floor", "detectable_at_alpha05",
                "expected_rho_sign"):
        assert rec["quartiles"]["_stats"][key] == got["quartiles"]["_stats"][key], \
            f"_stats.{key}: 产物与重算不符"


def test_the_verdict_is_reproducible_from_the_recorded_measurement(art):
    """判决必须能从产物里记下的那些数**重算**出来（不许是手打的）。"""
    kind, why = M.gate_verdict(art["measurement"]["quartiles"]["_stats"])
    assert kind == art["verdict"], (
        f"产物 verdict={art['verdict']} 但按记录重算得 {kind} ⇒ "
        "判决与它记下来的数脱钩了")
    assert art["avoidance_list_publish_allowed"] == (kind == "PASS")
    assert why == art["verdict_reason"]


def test_the_artifact_is_stamped_with_the_current_bytes(art):
    """🚨 章必须盖住**输入的字节**（追加式底稿 ⇒ 代码不变而输入会变，第 156 层）。"""
    sidecar = ART.with_name(ART.name + ".provenance.json")
    assert sidecar.exists(), f"{sidecar.name} 不存在 ⇒ 没有真章"
    sc = json.loads(sidecar.read_text(encoding="utf-8"))
    assert sc.get("source_parquet_sha256") == \
        hashlib.sha256(VERIFY.read_bytes()).hexdigest(), (
        "章上的输入指纹对不上 verification.parquet 的**当前字节** ⇒ "
        "这份判决不是对现在这份数据做的")
    assert sc.get("code_file_sha256") == \
        hashlib.sha256(SCRIPT.read_bytes()).hexdigest(), (
        "章上的代码字节对不上生成器的当前字节 ⇒ 这份判决出自另一份代码")
    assert sc.get("verdict") == art["verdict"]
    assert sc.get("avoidance_list_publish_allowed") == \
        art["avoidance_list_publish_allowed"]


# ── ⑤ 缺 `entry_date` ⇒ 显式缺失，不许回落 ──────────────────────────

def test_a_missing_entry_date_column_is_explicit_and_never_falls_back():
    """🚨 缺 `entry_date` 时**显式缺失**，绝不回落成 `pred_date`。

    回落 = 造一个与真值同形、看不出差别的数（第 6 种形状）⇒
    独立成交窗口数会变成出单日数，而**两个数长得不一样但都是整数**。
    """
    df = _frame(GOOD_ROWS, with_entry_date=False)
    res = M.gate_measure(df, n_perm=50, seed=1)
    cal = res["independence_caliber"]
    assert cal["n_entry_dates"] is None, (
        f"缺 `entry_date` 时 n_entry_dates={cal['n_entry_dates']!r} ⇒ "
        "它回落成了别的数")
    assert "entry_date" in cal["unavailable_reason"]
    ctl = res["quartiles"]["_stats"]["control_arm_entry_date"]
    assert ctl["available"] is False
    assert "entry_date" in ctl["unavailable_reason"]
    # 判决臂照旧（判决臂按 `pred_date` 分组，**不受** `entry_date` 缺失影响）
    assert res["n_independent_pred_dates"] == 2
    assert res["quartiles"]["_stats"]["monotone_decreasing_in_score"] is True


def test_the_real_artifact_reports_the_entry_window_count_and_not_pred_dates(art):
    """真实输入**有** `entry_date` ⇒ 独立成交窗口数必须**是它自己的数**。"""
    cal = art["measurement"]["independence_caliber"]
    assert cal["n_entry_dates"] is not None
    assert isinstance(cal["n_entry_dates"], int)
    df = scoped_ledger_frame(ROOT / "data" / "reports", VERIFY)
    leg = df[df["direction"] == "down"]
    assert cal["n_entry_dates"] == int(leg["entry_date"].nunique()), (
        "产物里的独立成交窗口数与 parquet 重算不符")
    assert cal["n_pred_dates"] == int(leg["pred_date"].nunique())


# ── ⑥ 接线层（产物层看不见源码） ────────────────────────────────────

def test_the_avoidance_publish_flag_is_wired_to_the_verdict():
    """★ **接线层**守卫：产物里的 `avoidance_list_publish_allowed`
    必须由判决**算**出来。

    ⚠️ 为什么不能只靠产物层那条：把源码里的 `kind == "PASS"` 改成 `True`
    并重跑，判决仍是 `NOT_PASSED_*`、而那个字段变成 `true`
    —— 产物层那条会红，**但在重跑之前它一直是绿的**（第 96 层）。
    ⇒ 这里钉的是**那个表达式的形状**：值节点必须是
    「`kind` 与 `PASS` 的比较」，不许是常量、不许是别的量。
    """
    tree = ast.parse(SRC)
    payload = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.Dict)
         and any(isinstance(k, ast.Constant)
                 and k.value == "avoidance_list_publish_allowed"
                 for k in n.keys)),
        None)
    assert payload is not None, "产物里没有 `avoidance_list_publish_allowed` 字段"
    val = next(v for k, v in zip(payload.keys, payload.values)
               if isinstance(k, ast.Constant)
               and k.value == "avoidance_list_publish_allowed")
    assert isinstance(val, ast.Compare), (
        "`avoidance_list_publish_allowed` 不再是比较式 —— 它被换成了固定值/别的量，"
        "于是「不许发布」这句话与判决**脱钩**了")
    left = ast.unparse(val.left)
    assert left == "kind", f"比较的左边是 {left!r}，不是判决变量 `kind`"
    comparators = [ast.unparse(c) for c in val.comparators]
    assert comparators in (["'PASS'"], ["VERDICT_PASS"]), (
        f"比较的不是 PASS（{comparators}）⇒ 判决与「能不能发」不再一一对应")


def test_the_three_state_branch_is_not_collapsed_to_two():
    """🚨 三态不许塌成两态（塌了就丢掉「下一步该做什么」）。"""
    tree = ast.parse(SRC)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "gate_verdict")
    seg = ast.get_source_segment(SRC, fn) or ""
    assert "VERDICT_POWERLESS" in seg and "VERDICT_MEASURED" in seg, (
        "gate_verdict 里三态少了 ⇒ 两支 NOT_PASSED 被合并")
    assert M.VERDICT_POWERLESS == "NOT_PASSED_POWERLESS"
    assert M.VERDICT_MEASURED == "NOT_PASSED_MEASURED"
    assert "detectable_at_alpha05" in seg, (
        "判据里没有「检出力」这一支 ⇒ POWERLESS 与 MEASURED 不可区分")


def test_the_gate_arm_is_the_down_leg():
    """闸门臂必须是 `down` —— 把它换成 `up` 就是把跌腿的判决拿到涨腿上。"""
    assert M.GATE_ARM == "down"
    assert M.GATE_ART_NAME == "downleg_gate.json"


# ── ⑦ 产物形状与诚实边界 ────────────────────────────────────────────

def test_the_artifact_carries_the_four_required_cannot_prove_items(art):
    """预注册 §6 的四条**逐条**在产物里，且里面的**数是算出来的**。"""
    items = art["what_this_cannot_prove"]
    joined = " ".join(items)
    m = art["measurement"]
    cal = m["independence_caliber"]
    assert "测不到" in joined, "① 缺『测不到 ≠ 没有』"
    assert "成交窗口" in joined, "② 缺『独立单元是成交窗口』"
    assert "做空" in joined and "信息价值" in joined, "③ 缺『不可做空 / 信息价值』"
    assert "不是涨腿的判据臂" in joined, "④ 缺『本产物不是涨腿的判据臂』"
    # 数必须跟着数据走（第 73 层：写死的那处必然漂移）
    assert f"条目数 {m['n_rows_total']} 当独立观测数" in joined
    assert f"{m['n_independent_pred_dates']} 个**出单日**" in joined
    assert f"**{cal['n_entry_dates']}** 个" in joined


def test_the_artifact_states_the_executability_boundary(art):
    """跌腿**不可直接做空** —— 外部锚，不是本仓实验结论。"""
    ex = art["executable"]
    assert ex["directly_executable"] is False
    assert "cn_factor_lines.txt:768" in ex["why"]
    assert "信息价值" in ex["so_the_only_useful_product_is"]


def test_the_artifact_records_that_s2_is_still_in_force(art):
    """🚨 S2 停的是「拿它当发布闸门」，不是「停止出单」；本闸门**不解除** S2。"""
    s2 = art["s2_status"]
    assert "27.3%" in s2["decision"]
    assert "不测" in s2["this_gate_does_not_measure_it"]
    assert "不是「停止出单」" in s2["what_was_stopped"]


def test_the_artifact_does_not_claim_the_up_arm_verdict(art):
    """🚨 本产物**不重复**涨腿的判决读数 ⇒ 不制造第二份真相源（第 97 层）。"""
    x = art["up_arm_cross_reference"]
    assert x["role"] == "cross_reference_only_NOT_a_second_source_of_truth"
    assert "score_ranking_power.json" in x["source_of_truth"]
    for forbidden in ("verdict", "point_biserial_rho", "permutation_p_two_sided",
                      "null_abs_rho_q95_power_floor"):
        assert forbidden not in x, (
            f"跨臂对照块里出现了 {forbidden!r} ⇒ 涨腿的读数被复制了一份")


# ── ⑧ 测试不许写生产产物路径 ────────────────────────────────────────

def test_the_test_never_writes_to_the_production_artifact_paths(tmp_path):
    """🚨 测试写生产路径 ⇒ 一条**合成数据**的判决会把真产物洗成合法
    （第 132 层）。本条用**字节 + mtime** 双重对拍。"""
    prod = [ROOT / "data" / "reports" / "downleg_ranking.json", ART]
    before = {p: (p.read_bytes() if p.exists() else None,
                   p.stat().st_mtime_ns if p.exists() else None) for p in prod}
    rc = M.main(["--verification", str(VERIFY),
                 "--out", str(tmp_path / "downleg_ranking.json"),
                 "--gate-out", str(tmp_path / "downleg_gate.json")])
    assert rc == 0
    after = {p: (p.read_bytes() if p.exists() else None,
                  p.stat().st_mtime_ns if p.exists() else None) for p in prod}
    assert before == after, "生产产物路径被测试改动了 ⇒ 必须只用 tmp_path"
    assert (tmp_path / "downleg_gate.json").exists(), "闸门产物没落到指定路径"

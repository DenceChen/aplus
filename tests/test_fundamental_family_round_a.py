"""基本面族**轮次 A** 的产物守卫 + **`bm_ic_attribution.py` 变量遮蔽缺陷**的守卫。

## 这个文件守什么

| 组 | 守的对象 | 判据类型 |
|---|---|---|
| A1 | `fundamental_contamination_concentration_2026-10-20.json`（机理③ 污染集中度） | 口径 + 措辞 |
| A2 | `fundamental_coverage_axes_2026-10-20.json`（机理② 覆盖池轴向偏差） | 口径 + 对照臂 |
| A3 | `fundamental_factor_family_cross_section_2026-10-20.json`（族 A 就绪） | **禁止出现 t / 判决** |
| D | `scripts/bm_ic_attribution.py` 的 `trimmed_ic::t` 覆盖缺陷 | **行为层**（合成夹具跑真 `main()`） |

## 🚨 为什么 D 组必须是**行为层**，不能只读产物

缺陷的形态是「**变量遮蔽**」：C 段算出截尾 IC 的 t 赋给 `t`，
G 节的 `for key, lab in labels.items():` 里又写 `t = v.get("t")`，
组装产物时 `"t": t` 写出去的是**最后一个尾部对照的 t**。

- 读产物只能证明「那份产物是错的」，**对当前代码零覆盖**（第 96 层）；
- AST 扫描能证明「接线长这样」，但**探针能跑 ≠ 判据打在用点上**（第 117 层）；
⇒ 唯一的有效判据是：**用合成夹具跑真 `main()`，
把产物里的 `trimmed_ic::t` 与「由截尾 IC 序列独立重算出来的 t」对拍。**
旧代码在这条上**必红**（它写的是尾部对照的 t）⇒ 守卫有牙。

## ⚠️ 夹具的构造是刻意的，不是随手编的

- 80 只假票（`600000.SH`…`600079.SH`）× 200 个工作日；
- 每只票的日漂移 `base_i` **随 i 递增** ⇒ 前瞻收益随 i 递增；
- BPS 随 i 指数递增 ⇒ `bm = log(BPS/close)` 随 i 递增（越大越便宜）；
⇒ `bm` 与前瞻收益**正相关**（IC 显著为正）⇒ 截尾 IC 的均值 > 0。
- 加一个**带种子**的逐日噪声 ⇒ 逐日 IC 有离散度 ⇒ `sd > 0` ⇒ t 有限。

**为什么夹具要「IC 为正」**：缺陷的签名是「**均值为正而 t 为负**」
（数学上不可能）。夹具若让 IC 为负，旧的错误值恰好同号 ⇒
`sign(t) == sign(mean)` 这条**外部锚**就抓不到它 ⇒ 守卫会变成装饰。
⇒ 夹具必须让**正确值与错误值异号**，否则这条锚没有判别力。

## 诚实边界

- 本文件**不**跑全仓 pytest（只跑自己）。
- 本文件**不**重跑 `bm_ic_attribution.py` 的全市场面板（GB 级）。
  生产口径的重跑在预注册 `docs/PREREG_FUNDAMENTAL_FAMILY_2026-10-20.md` §8.2 **B3**。
"""
from __future__ import annotations

import ast
import json
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aplus import provenance  # noqa: E402

A1 = ROOT / "data/reports/fundamental_contamination_concentration_2026-10-20.json"
A2 = ROOT / "data/reports/fundamental_coverage_axes_2026-10-20.json"
A3 = ROOT / "data/reports/fundamental_factor_family_cross_section_2026-10-20.json"
BM_FIXED = ROOT / "data/reports/bm_ic_attribution_reduced_2026-10-21.json"
BM_HISTORICAL = ROOT / "data/reports/bm_ic_attribution.json"

GEN_A1 = "scripts/measure_fundamental_contamination_concentration.py"
GEN_A2 = "scripts/measure_fundamental_coverage_axes.py"
GEN_A3 = "scripts/measure_fundamental_factor_family_cross_section.py"
GEN_BM = "scripts/bm_ic_attribution.py"


# ── 小工具 ────────────────────────────────────────────────────────────

def _load(path: Path) -> dict:
    assert path.is_file(), f"产物不存在：{path}"
    return json.loads(path.read_text(encoding="utf-8"))


def _sidecar(path: Path) -> dict:
    sc = path.with_suffix(path.suffix + ".provenance.json")
    assert sc.is_file(), f"产物没有溯源章：{sc}"
    return json.loads(sc.read_text(encoding="utf-8"))


def _assert_stamped_by_current_bytes(path: Path, generator: str) -> None:
    """产物必须由**当前盘上的生成器字节**造出来（第 125 层）。

    判据是**逐位相等**，不是「commit 没动过它」——
    后者在工作区脏时是**假阴性**（改了没提交 ⇒ 判 fresh）。
    """
    sc = _sidecar(path)
    assert sc.get("generator") == generator, (
        f"{path.name} 的章记的生成器是 {sc.get('generator')!r}，不是 {generator!r}")
    assert sc.get("code_file_sha256"), f"{path.name} 的章没有字节证据（code_file_sha256）"
    now = provenance.code_file_sha256(ROOT, generator)
    assert now is not None, f"生成器 {generator} 在盘上找不到"
    assert sc["code_file_sha256"] == now, (
        f"{path.name} 的章记的字节 {sc['code_file_sha256'][:12]}… "
        f"≠ 当前盘上 {now[:12]}… ⇒ 产物比生成器旧（或生成器被改过没重跑）")


def _keys_recursive(obj) -> list[str]:
    out = []

    def rec(o, path=""):
        if isinstance(o, dict):
            for k, v in o.items():
                out.append((f"{path}::{k}" if path else str(k)))
                rec(v, f"{path}::{k}" if path else str(k))
        elif isinstance(o, list):
            for i, v in enumerate(o):
                rec(v, f"{path}[{i}]")

    rec(obj)
    return out


#: 「没测出」与「不存在」在中文里只差一个否定词 ⇒ 光禁子串会**误伤免责句**
#: （产物里写「这**不等于**「没有污染」」是**必须**的）。
#: ⇒ 判据：被禁短语**每次出现**，它前面 24 字里必须有一个否定标记。
_NEGATIONS = ("不等于", "不是", "没有", "≠", "未", "无")


def _assert_not_asserting_absent(text: str, banned: tuple[str, ...]) -> None:
    """不许把「没测出」写成「不存在」——但**允许**写「这不等于不存在」。

    形态：被禁短语每次出现都必须在否定语境内（否则就是在断言它）。
    """
    for phrase in banned:
        start = 0
        while True:
            i = text.find(phrase, start)
            if i < 0:
                break
            window = text[max(0, i - 24):i]
            assert any(neg in window for neg in _NEGATIONS), (
                f"「{phrase}」出现在没有否定语境的句子里：…{text[max(0, i-40):i+40]}…")
            start = i + len(phrase)


# ── A1：机理③ 污染集中度 ──────────────────────────────────────────────

def test_a1_artifact_is_stamped_by_the_current_generator_bytes():
    _assert_stamped_by_current_bytes(A1, GEN_A1)


def test_a1_reports_all_three_readings_because_the_prereg_fixed_no_denominator():
    """预注册 §6.1 **没写死分母** ⇒ 甲/乙/卡方三个口径必须**都**报出来。

    只报一个 = 事后挑了一个读法（第 91 层）。
    """
    d = _load(A1)
    c = d["criterion"]
    assert "reading_A_share_dev" in c and "reading_B_rate_dev" in c
    assert c["chi2_stat"] is not None and c["chi2_p"] is not None
    assert c["chi2_df"] == 9
    # 三个口径必须**互相独立**地给出各自的「过线/不过线」判断
    for key in ("reading_A_share_dev", "reading_B_rate_dev"):
        assert "exceeds_threshold" in c[key], f"{key} 没报过线判断"
    assert "chi2_significant_at_0.05" in c


def test_a1_every_reading_carries_a_noise_reference():
    """门槛没有噪声尺度 ⇒ 「超过 3pp」这句话**没有参照系**（第 178 层）。

    n=64 变更行时单档占比标准差就有 3.75pp ⇒ 门槛本身在噪声带里。
    """
    d = _load(A1)
    c = d["criterion"]
    for key in ("reading_A_share_dev", "reading_B_rate_dev"):
        assert c[key].get("null_p95_pp") is not None, f"{key} 没有零假设 95 分位"
        assert "exceeds_null_p95" in c[key], f"{key} 没有与噪声带的比较"


def test_a1_keeps_the_prereg_threshold_audit_visible():
    """预注册 §6.1 的 3pp 推导有**分母错配**且错配会翻转结论。

    这件事必须**留在产物里**，否则下一个人会照抄一个门槛在噪声带里的判据。
    """
    d = _load(A1)
    audit = d["criterion"]["threshold_calibration_audit"]
    assert audit.get("finding") and audit.get("prereg_derivation_verbatim")
    assert audit.get("consequence")


def test_a1_never_writes_no_contamination():
    """第 16 种形状：「没测出」与「不存在」不许同形。

    预注册 §6.0：污染**均匀** ⇒ 只加噪（**降低** IC）⇒ **削弱③**，不是「无害」。
    """
    d = _load(A1)
    verdict = d["verdict"]
    _assert_not_asserting_absent(
        verdict, ("没有污染", "不存在污染", "污染不存在", "已排除污染"))
    disc = d["verdict_discipline"]
    assert disc["not_detected_is_not_absent"]
    assert disc["uniform_is_not_harmless"]
    assert disc["three_readings_must_agree"]


# ── A2：机理② 覆盖池轴向偏差 ──────────────────────────────────────────

def test_a2_artifact_is_stamped_by_the_current_generator_bytes():
    _assert_stamped_by_current_bytes(A2, GEN_A2)


def test_a2_every_axis_carries_a_random_null_reference():
    """每个轴的偏差都必须与「随机抽 500 只有多大偏离」比。

    n=500 时单档占比标准差就有 ~1.4pp ⇒ 光看 3pp 门槛会误判。
    """
    d = _load(A2)
    for name, ax in d["axes"].items():
        assert ax.get("max_abs_share_dev_pp") is not None, f"{name} 轴没有偏差读数"
        assert ax.get("random_null_p95_pp") is not None, f"{name} 轴没有零假设 95 分位"
        assert "exceeds_random_null_p95" in ax, f"{name} 轴没有与噪声带的比较"
        assert "exceeds_threshold" in ax, f"{name} 轴没有门槛比较"


def test_a2_has_a_control_arm_that_separates_selection_from_vintage():
    """第 183 层：**加对照臂**，而不是换判据臂。

    对照臂 = 同一个 PIT 帧、同样那 500 只 ⇒ 偏差若来自**选样**则对照臂照样偏，
    若来自**数据 vintage**则对照臂不偏。
    """
    d = _load(A2)
    arms = d.get("factor_distributions") or {}
    ctrl = None
    for f, blk in arms.items():
        if isinstance(blk, dict) and "control_arm_pit_same_500" in blk:
            ctrl = blk["control_arm_pit_same_500"]
            break
    assert ctrl is not None, "A2 没有 PIT 同 500 只的对照臂"
    assert "decomposition" in ctrl
    dec = ctrl["decomposition"]
    assert "selection_pp" in dec and "vintage_pp" in dec


def test_a2_never_writes_no_bias():
    d = _load(A2)
    disc = d["verdict_discipline"]
    assert disc["not_detected_is_not_absent"]
    for ax in d["axes"].values():
        _assert_not_asserting_absent(
            ax.get("verdict", ""),
            ("没有偏差", "不存在偏差", "已排除偏差", "无偏差"))


# ── A3：族 A 就绪（**不含 t、不含判决**）────────────────────────────

def test_a3_artifact_is_stamped_by_the_current_generator_bytes():
    _assert_stamped_by_current_bytes(A3, GEN_A3)


def test_a3_contains_no_t_statistic_and_no_verdict():
    """预注册 §8.1 A3 的判据逐字：「**不含 t、不含判决**」。

    A3 的用途是**冻结族 A 的因子数**（好让 Bonferroni 分母可推导），
    任何 t 值或判决出现在这里都是**越界**（会让人以为族 A 已经被判过）。
    """
    d = _load(A3)
    forbidden_exact = {
        "t", "ic_t", "t_crit", "t_stat", "t_statistic", "t_value",
        "verdict", "verdicts", "ic_verdict", "verdict_text",
        "significant", "significance", "p_value", "pvalue",
        "pass", "passed", "bonferroni_crit",
    }
    bad = []
    for k in _keys_recursive(d):
        leaf = k.split("::")[-1].split("[")[0]
        if leaf in forbidden_exact or leaf.endswith("_t"):
            bad.append(k)
        elif "verdict" in leaf.lower() and leaf != "contains_no_verdict":
            bad.append(k)
    assert not bad, f"A3 里出现了判决/t 统计量的键：{bad}"


def test_a3_self_declaration_agrees_with_the_key_scan():
    """产物的**自述**必须与扫描结果一致（否则自述是一句假话）。"""
    d = _load(A3)
    role = d["product_role"]
    assert role["descriptive_only"] is True
    assert role["contains_no_t_statistic"] is True
    assert role["contains_no_verdict"] is True


def test_a3_participation_ratio_is_recomputable_from_the_reported_eigenvalues():
    """有效维数 = (Σλ)²/Σλ² —— **阈值无关**的谱量。

    守卫的意义：它不许产物报一个「自己算不出来」的有效因子数，
    也不许报一个「事后挑阈值挑出来」的数。
    """
    d = _load(A3)
    spec = d["correlations"]["family_a_subset"]["spectrum"]
    ev = np.asarray(spec["eigenvalues"], dtype=float)
    assert ev.size >= 2
    assert np.all(ev >= 0.0), "特征值谱不应有负值"
    s = ev.sum()
    expect = float((s ** 2) / max((ev ** 2).sum(), 1e-12))
    assert spec["participation_ratio"] == pytest.approx(expect, rel=1e-12)
    assert spec["participation_ratio_rank"] == pytest.approx(expect / len(ev), rel=1e-12)
    # 参与比必须**落在** 1..n 之间（数学性质，不是形状断言）
    assert 1.0 - 1e-9 <= expect <= len(ev) + 1e-9


def test_a3_n_A_candidates_are_derived_not_typed():
    d = _load(A3)
    blk = d["family_a_n_A_inputs"]
    n_f = blk["n_factors_family_a"]
    n_k = blk["n_K_arms"]
    assert blk["n_A_if_factors_uncorrelated"] == n_f * n_k
    assert blk["n_A_if_effective_dimension_used"] == pytest.approx(
        blk["effective_dimension"] * n_k, rel=1e-12)


def test_a3_correlation_matrix_is_square_symmetric_with_unit_diagonal():
    d = _load(A3)
    labels = d["correlations"]["labels"]
    for which in ("spearman", "pearson"):
        m = np.asarray(d["correlations"][which]["matrix"], dtype=float)
        assert m.shape == (len(labels), len(labels)), f"{which} 矩阵不是方阵"
        assert np.allclose(m, m.T, atol=1e-12), f"{which} 矩阵不对称"
        assert np.allclose(np.diag(m), 1.0, atol=1e-12), f"{which} 对角线不是 1"


# ── D：`trimmed_ic::t` 的变量遮蔽缺陷 ─────────────────────────────────

def _bm_module():
    import importlib
    return importlib.import_module("scripts.bm_ic_attribution")


def _synthetic_cache(tmp_path: Path) -> tuple[Path, Path]:
    """造一份**能跑通全流程**的合成缓存 + 合成财报缓存。

    见文件头「夹具的构造是刻意的」一节。
    """
    rng = np.random.default_rng(20261021)
    n_codes, n_days = 80, 200
    codes = [f"600{i:03d}.SH" for i in range(n_codes)]
    dates = pd.bdate_range("2019-01-02", periods=n_days).strftime("%Y-%m-%d").tolist()

    # 每只票的日漂移随 i 递增；噪声带种子 ⇒ 逐日 IC 有离散度
    base = 0.0002 * (np.arange(n_codes) + 1.0)
    noise = rng.normal(0.0, 0.0009, size=(n_codes, n_days))

    daily_map = {}
    for i, code in enumerate(codes):
        ret = base[i] + noise[i]
        close = 10.0 * np.cumprod(1.0 + ret)
        frame = pd.DataFrame({
            "date": dates,
            "open": close * 0.999,
            "high": close * 1.002,
            "low": close * 0.998,
            "close": close,
            "volume": np.full(n_days, 1.0e6),
            "amount": close * 1.0e6,
            "tradestatus": np.ones(n_days, dtype=int),
            "isST": np.zeros(n_days, dtype=int),
            "turn": np.full(n_days, 0.01),
            "pctChg": ret * 100.0,
        })
        daily_map[code] = frame

    # BPS 随 i 指数递增 ⇒ bm = log(BPS/close) 随 i 递增（越大越便宜）
    bps = 10.0 * np.exp(5.0 * np.arange(n_codes) / (n_codes - 1.0))
    fin = pd.DataFrame({
        "SECURITY_CODE": codes,
        "REPORT_TYPE": ["年报"] * n_codes,
        "REPORT_DATE": ["2017-12-31"] * n_codes,
        "NOTICE_DATE": ["2018-04-20"] * n_codes,
        "UPDATE_DATE": ["2018-04-20"] * n_codes,
        "BPS": bps,
        "ROEJQ": np.linspace(1.0, 20.0, n_codes),
    })
    fin_path = tmp_path / "fin.parquet"
    fin.to_parquet(fin_path, index=False)

    # 横截面：每天一张，含 code/name/isST/volume/listed_days/last_date/buyable
    xs = {"_dates": list(dates), "_daily_map": daily_map, "_factors_map": {},
          "_adj_cache": {}, "_entry_lag": 1}
    for d in dates:
        xs[d] = pd.DataFrame({
            "code": codes,
            "name": codes,
            "isST": np.zeros(n_codes, dtype=float),
            "volume": np.full(n_codes, 1.0e6),
            "listed_days": np.full(n_codes, 500, dtype=int),
            "last_date": [d] * n_codes,
            "buyable": [True] * n_codes,
        })

    cache_path = tmp_path / "cache.pkl"
    cache_path.write_bytes(pickle.dumps(xs, protocol=pickle.HIGHEST_PROTOCOL))
    return cache_path, fin_path


def _run_and_recompute(tmp_path: Path):
    """跑真 `main()`，并**独立**重算截尾 IC 序列。

    返回 `(产物, 截尾 IC 序列)`。
    """
    M = _bm_module()
    cache_path, fin_path = _synthetic_cache(tmp_path)
    out = tmp_path / "bm.json"
    assert M.main(["--cache", str(cache_path), "--financial", str(fin_path),
                   "--out", str(out)]) == 0
    art = _load(out)

    # 独立重算：**另起一份**缓存副本，按 `main()` 同样的口径把 BM 挂上去，
    # 再重算截尾 IC 序列 ⇒ 测试拿到的是「序列」，
    # 而 t 的公式（mean/(sd/√n)）由测试自己写一遍。
    xs = pickle.loads(cache_path.read_bytes())
    days = sorted(d for d in xs["_dates"] if "2019-01-02" <= d <= "2023-12-31")
    annual = M.load_annual(fin_path)
    xs = M.attach_to_cross_sections(xs, annual, "2019-01-02", "2023-12-31")
    dm = xs.get("_daily_map") or {}
    fm = xs.get("_factors_map") or {}
    ac = xs.setdefault("_adj_cache", {})
    tic = M.trimmed_ic(xs, days, M.K, dm, fm, ac)
    return art, tic


def test_the_trimmed_ic_t_is_recomputable_from_the_trimmed_ic_series(tmp_path):
    """**行为层**：跑真 `main()`，把产物里的 `trimmed_ic::t` 与独立重算的 t 对拍。

    旧代码在这里**必红**（它写的是尾部对照的 t）⇒ 这条守卫有牙。
    """
    art, tic = _run_and_recompute(tmp_path)
    assert len(tic) == art["trimmed_ic"]["n"], "截尾 IC 的期数对不上"
    vv = tic.to_numpy()
    sd = float(vv.std(ddof=1))
    assert sd > 0.0, "夹具失效：截尾 IC 序列没有离散度 ⇒ t 无从算起"
    expect = float(vv.mean() / (sd / np.sqrt(len(vv))))
    got = art["trimmed_ic"]["t"]
    assert got is not None, "trimmed_ic::t 是 None ⇒ 夹具没跑出 t"
    assert got == pytest.approx(expect, rel=1e-12), (
        f"trimmed_ic::t = {got!r}，而由截尾 IC 序列独立重算得 {expect!r} "
        f"⇒ 产物写的不是截尾 IC 自己的 t")


def test_the_trimmed_ic_t_has_the_same_sign_as_the_trimmed_ic_mean(tmp_path):
    """**外部锚**（数学必要条件）：t = mean/(sd/√n)，sd > 0 ⇒ sign(t) == sign(mean)。

    缺陷的签名正是「均值为正而 t 为负」⇒ 这条锚在旧代码上**必红**。
    """
    art, _ = _run_and_recompute(tmp_path)
    t = art["trimmed_ic"]["t"]
    mean = art["trimmed_ic"]["mean"]
    assert t is not None and mean is not None
    assert np.sign(t) == np.sign(mean), (
        f"trimmed_ic 的 t={t!r} 与 mean={mean!r} **异号** ⇒ "
        f"t 不可能是这个序列的 t")


def test_the_trimmed_ic_t_is_not_one_of_the_tail_contrast_t_values(tmp_path):
    """缺陷的**逐位**签名：旧产物里 `trimmed_ic::t` 与
    `tail_contrasts::q1_minus_pool::t` **逐位相同**。

    这条把「两个不同的量不许共用一个名字」钉成可测事实。
    """
    art, _ = _run_and_recompute(tmp_path)
    t = art["trimmed_ic"]["t"]
    legs = {k: v.get("t") for k, v in art["tail_contrasts"].items()
            if isinstance(v, dict)}
    assert legs, "产物里没有尾部对照"
    assert t not in legs.values(), (
        f"trimmed_ic::t = {t!r} 与某个尾部对照的 t 逐位相同 ⇒ "
        f"两个不同的量又共用了一个变量名")


def _dict_entry(d: ast.AST, key: str):
    """从一个 `ast.Dict` 里取某个常量键对应的值。"""
    if not (isinstance(d, ast.Dict) and d.keys):
        return None
    for k, v in zip(d.keys, d.values):
        if isinstance(k, ast.Constant) and k.value == key:
            return v
    return None


def _trimmed_t_name(main_fn: ast.AST) -> str | None:
    """产物里 `trimmed_ic::t` 是从**哪个名字**写出来的。

    两种形态都要认：
      ① `out = {"trimmed_ic": {"t": <Name>, ...}}`      （当前代码）
      ② `out["trimmed_ic"]["t"] = <Name>`              （等价写法）
    """
    for node in ast.walk(main_fn):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            tgt = node.targets[0]
            if isinstance(tgt, ast.Name) and tgt.id == "out":
                sub = _dict_entry(node.value, "trimmed_ic")
                val = _dict_entry(sub, "t") if sub is not None else None
                if isinstance(val, ast.Name):
                    return val.id
            if (isinstance(tgt, ast.Subscript)
                    and isinstance(tgt.value, ast.Name)
                    and tgt.value.id == "out"
                    and isinstance(tgt.slice, ast.Constant)
                    and tgt.slice.value == "trimmed_ic"
                    and isinstance(node.value, ast.Name)):
                return node.value.id
    return None


def test_the_generator_does_not_rebind_the_trimmed_t_inside_the_tail_loop():
    """**AST 接线守卫**：G 节循环里不许再写 `trimmed_ic::t` 用的那个名字。

    这是「变量遮蔽」这一族缺陷的**形态**守卫（不依赖任何数据）。
    """
    src = (ROOT / GEN_BM).read_text(encoding="utf-8")
    tree = ast.parse(src)
    main_fn = next(n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == "main")

    t_name = _trimmed_t_name(main_fn)
    assert t_name, "产物里 trimmed_ic::t 不是从一个具名变量写出的 ⇒ 守卫失效"

    # 那个名字不许在「遍历 labels 的那个 for 循环」里被赋值
    for node in ast.walk(main_fn):
        if not isinstance(node, ast.For):
            continue
        it = ast.unparse(node.iter)
        if "labels" not in it:
            continue
        for sub in ast.walk(node):
            targets = []
            if isinstance(sub, ast.Assign):
                targets = sub.targets
            elif isinstance(sub, (ast.AugAssign, ast.AnnAssign)):
                targets = [sub.target]
            for tgt in targets:
                for nm in ast.walk(tgt):
                    if isinstance(nm, ast.Name) and nm.id == t_name:
                        raise AssertionError(
                            f"`{t_name}` 在遍历 labels 的循环里被重新赋值 "
                            f"（第 {sub.lineno} 行）⇒ 产物里的 trimmed_ic::t "
                            f"会被尾部对照的 t 覆盖")


def test_the_fixed_bm_artifact_is_stamped_by_the_current_generator_bytes():
    """修完生成器之后的那份产物，必须由**当前盘上的字节**造出来（第 125 层）。

    这一条同时是**变异体的额外证据**：把生成器改坏（哪怕改回逐位等长），
    章里的字节就对不上 ⇒ 这条守卫会红。
    """
    _assert_stamped_by_current_bytes(BM_FIXED, GEN_BM)


def test_the_historical_trimmed_ic_t_is_the_fixed_value_not_the_shadowed_one():
    """「修复有没有生效」这件事必须**有东西守**，不许只活在散文里。

    🚨 修复前（`trimmed_ic::t` 被尾部对照的 `t` 覆盖 ⇒ 变量遮蔽）是
    **−1.6119787074299516**；修复后是 **+3.0761460884793435**。

    ⇒ 钉住**修复后**那个值 ⇒ 「修复被回退」当场红；
    ⇒ 同时钉住「不是那个被遮蔽的值」⇒ 「遮蔽复发」当场红。

    出处：`data/reports/bm_ic_attribution.json::trimmed_ic::t`
    （2026-10-21 由轮次 B 刷新；其 sidecar 的 `code_file_sha256` 与盘上生成器
    逐位一致 ⇒ 这份产物出自**当前**代码）。
    """
    art = _load(BM_HISTORICAL)
    t = art["trimmed_ic"]["t"]
    assert t == 3.0761460884793435, (
        f"历史产物的 trimmed_ic::t = {t!r}，不是修复后的那个值"
        f"（+3.0761460884793435）⇒ 变量遮蔽**复发**了？")
    assert t != -1.6119787074299516, (
        "历史产物的 trimmed_ic::t 又是那个被遮蔽的值 ⇒ 修复被回退了")


def test_the_reduced_run_reproduces_the_historical_judgment_fields_bitwise():
    """**忠实度**：缩减池重跑（500 只带 BM 的票）必须**逐位**复现历史产物
    的每一个**判决相关**字段。

    唯一允许不同的两类：
      ① `trimmed_ic::t` —— 这正是被修的那个字段；
      ② `pool_bias[*].pool_total` / `pool_bias[*].share` —— **作用域依赖**
         （缩减池 313~418 行 vs 全市场 3422~4762 行），不是缺陷。
    """
    new = _load(BM_FIXED)
    old = _load(BM_HISTORICAL)

    for key in ("kind", "k", "factor", "window", "coverage", "deciles",
                "ic_by_year", "ic_by_calendar_and_report_year",
                "revision_exposure", "tail_contrasts", "question",
                "discriminator", "no_promotion"):
        assert new[key] == old[key], f"字段 `{key}` 与历史产物不一致"

    assert new["trimmed_ic"]["mean"] == old["trimmed_ic"]["mean"]
    assert new["trimmed_ic"]["n"] == old["trimmed_ic"]["n"]
    assert new["trimmed_ic"]["trim"] == old["trimmed_ic"]["trim"]
    # ⚠️ **2026-10-21 判据前提变了**（轮次 B 刷新了历史产物）：
    #   原断言是「缩减重跑的 `trimmed_ic::t` 必须**不同于**历史产物」
    #   ⇒ 它的**前提**是「历史产物是**修复前**的值（−1.6119787074299516）」
    #   ⇒ 而轮次 B 把 `bm_ic_attribution.json` 刷新成了**修复后**的值
    #   （`trimmed_ic::t` = +3.0761460884793435，变量遮蔽修好）
    #   ⇒ ⇒ 两边现在**逐位相等** ⇒ 「必须不同」这条判据**前提过期**。
    # ⇒ 改成「**逐位复现**」⇒ 这比「不同」**更强**
    #   （「不同」被任何差异满足；「相等」是逐位）。
    # ⇒ 而「修复有没有生效」这件事改由**下一条**守：那个值被**钉住**
    #   （值 + 出处：轮次 B 的产物与它自己的 sidecar）。
    assert new["trimmed_ic"]["t"] == old["trimmed_ic"]["t"], (
        f"缩减重跑的 trimmed_ic::t 与历史产物不一致："
        f"{new['trimmed_ic']['t']!r} ≠ {old['trimmed_ic']['t']!r}")

    for y in old["pool_bias"]:
        assert new["pool_bias"][y]["pool_covered"] == old["pool_bias"][y]["pool_covered"]
        assert new["pool_bias"][y]["day"] == old["pool_bias"][y]["day"]
        assert new["pool_bias"][y]["pool_total"] < old["pool_bias"][y]["pool_total"], (
            "缩减池的 pool_total 本该更小 ⇒ 输入范围没缩")


def test_the_historical_bm_artifact_is_not_citable_with_its_wrong_field():
    """canonical 产物 `data/reports/bm_ic_attribution.json` 里那个字段**仍是错的**。

    它**不可引用**（没有溯源章 ⇒ `freshness()` 判 `unknown`）。
    ⇒ 守卫的形态是「若它被判 fresh（可引用），那它的 t 必须是真 t」——
    现在走 `unknown` 分支（绿），等哪天它被正确重跑并盖章，这条也会自然转绿。
    """
    d = _load(BM_HISTORICAL)
    verdict = provenance.freshness(BM_HISTORICAL, GEN_BM, root=ROOT)["verdict"]
    if verdict == "fresh":
        t = d["trimmed_ic"]["t"]
        mean = d["trimmed_ic"]["mean"]
        assert t is not None and mean is not None
        assert np.sign(t) == np.sign(mean), (
            "canonical 产物被判 fresh（可引用），但它的 trimmed_ic::t 与 mean 异号")
    else:
        assert verdict == "unknown", (
            f"canonical 产物的 freshness 判成 {verdict!r}，既不是 fresh 也不是 unknown "
            f"⇒ 这条守卫需要人来看一眼")

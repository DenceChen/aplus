"""持仓级降级预警（`aplus/holdings_alert.py`）的守卫。

## 这个测试文件守的是什么

**判据本身**，不是实现细节。三条轴：

1. **方向对不对**：底档 ⇒ 减仓，最好档 ⇒ 持有。
   反了就是「越差越加仓」——比没有预警更危险。
2. **三态各自可达**：REDUCE / WATCH / HOLD 都必须能被**造出来**，
   否则其中一态是死代码（写在那儿、永远走不到、永远没人发现）。
   ⚠️ 夹具一律**现造**（fork），**不许读生产产物**：
   读产物 ⇒ 产物一变测试就红，而红的原因不是判据坏了。
3. **阈值不许手打**：阈值必须**从外部锚推导** ⇒ 改锚阈值跟着变。
   钉死字面量（`assert cut == 0.10`）是**恒真守卫**：改了锚它照样绿。

## 硬约束

- 🚨 **不许写任何生产路径**（`data/reports/…`）：本模块是纯函数层，
  测试跑它不该在生产目录里留下任何字节。
- 🚨 **不许读生产产物**当夹具：判据的可测性必须与当天数据无关。
- 测「测不出」不许写成「没有」：缺建仓分 ⇒ `UNDECIDED`，不是 `HOLD`。
"""
from __future__ import annotations

import importlib
import inspect
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import aplus.holdings_alert as HA  # noqa: E402


# --------------------------------------------------------------------------
# 夹具：现造一个全池
# --------------------------------------------------------------------------

def _pool(scores: dict, names: dict | None = None) -> pd.DataFrame:
    """造一个全池。`scores` = {code: 合成分}，越大越好。"""
    names = names or {}
    return pd.DataFrame({
        "code": list(scores.keys()),
        "score": list(scores.values()),
        "name": [names.get(c, c) for c in scores.keys()],
    })


def _big_pool(n: int = 1000) -> dict:
    """1000 只、分数均匀铺开的一池（0.000 … 0.999）。"""
    return {f"S{i:04d}": i / 1000.0 for i in range(n)}


# --------------------------------------------------------------------------
# 1. 契约：签名与必填参数
# --------------------------------------------------------------------------

def test_the_contract_signature_is_exactly_as_promised():
    """契约里写死的签名不许漂（协调方按它接线）。"""
    sig = inspect.signature(HA.build_holdings_alert)
    params = list(sig.parameters.values())
    assert [p.name for p in params] == ["held", "scored_universe", "entry_scores"]
    assert params[0].default is inspect.Parameter.empty, "held 必填"
    assert params[1].default is inspect.Parameter.empty, "scored_universe 必填"
    assert params[2].default is None, "entry_scores 可为 None"


def test_missing_a_required_argument_raises_type_error():
    """缺必填参数必须当场炸，不许回落成「空持仓」。"""
    with pytest.raises(TypeError):
        HA.build_holdings_alert()          # noqa: F821 - 故意缺参
    with pytest.raises(TypeError):
        HA.build_holdings_alert([{"code": "000001.SZ"}])   # 缺 scored_universe


def test_a_scored_universe_without_code_or_score_is_rejected():
    """全池必须有 code / score 两列——缺列是**口径错误**，不是「测不出」。"""
    with pytest.raises(ValueError):
        HA.build_holdings_alert([{"code": "000001.SZ"}], pd.DataFrame({"foo": [1.0]}))
    with pytest.raises(TypeError):
        HA.build_holdings_alert([{"code": "000001.SZ"}], [{"code": "x", "score": 1.0}])


# --------------------------------------------------------------------------
# 2. 方向守卫：底档 ⇒ 减仓，最好档 ⇒ 持有
# --------------------------------------------------------------------------

def test_the_worst_decile_holding_with_a_fall_since_entry_is_reduce():
    """最差十分位 + 比建仓日差 ⇒ 减仓。"""
    pool = _big_pool(1000)
    worst = "S0000"          # 分数 0.000 ⇒ 排第 1000
    best = "S0999"           # 分数 0.999 ⇒ 排第 1
    held = [{"code": worst, "name": "最差"}, {"code": best, "name": "最好"}]
    entry = {worst: 0.900, best: 0.100}   # 最差的那只买的时候是 0.900
    out = HA.build_holdings_alert(held, _pool(pool), entry)
    by_code = {i["code"]: i for i in out["items"]}
    assert by_code[worst]["action"] == HA.ACTION_REDUCE
    assert by_code[worst]["rank_in_universe"] == 1000
    assert by_code[worst]["is_in_bottom_decile"] is True
    assert by_code[worst]["relative_to_entry_score"] < 0


def test_the_best_holding_that_did_not_fall_is_hold():
    """排第 1 且不比建仓日差 ⇒ 持有（不许无脑减）。"""
    pool = _big_pool(1000)
    best = "S0999"
    out = HA.build_holdings_alert(
        [{"code": best, "name": "最好"}], _pool(pool), {best: 0.100})
    item = out["items"][0]
    assert item["action"] == HA.ACTION_HOLD
    assert item["rank_in_universe"] == 1
    assert item["is_in_bottom_decile"] is False
    assert item["is_in_bottom_quintile"] is False
    assert item["relative_to_entry_score"] > 0


def test_a_holding_that_is_only_slightly_worse_than_entry_is_not_reduce():
    """方向守卫的另一半：差一点点 **不许** 减仓（否则天天减）。"""
    pool = _big_pool(1000)
    mid = "S0500"
    out = HA.build_holdings_alert(
        [{"code": mid}], _pool(pool), {mid: 0.4995})   # 现在 0.500，比建仓高
    assert out["items"][0]["action"] == HA.ACTION_HOLD


# --------------------------------------------------------------------------
# 3. 三态各自可达（夹具现造，不许读产物）
# --------------------------------------------------------------------------

def test_all_four_states_are_reachable():
    """四态（REDUCE/WATCH/HOLD/UNDECIDED）各自能被造出来。

    ⚠️ 如果某一态造不出来 ⇒ 它是死代码 ⇒ 写在那儿的判据没人跑过。
    """
    pool = _big_pool(1000)
    held = [
        {"code": "S0000"},     # 最低分 + 比建仓差 ⇒ REDUCE
        {"code": "S0100"},     # 倒数 10% 但不是最差 ⇒ WATCH
        {"code": "S0999"},     # 最好且没变差 ⇒ HOLD
        {"code": "S0500"},     # 缺建仓分 ⇒ UNDECIDED
    ]
    entry = {"S0000": 0.900, "S0100": 0.200, "S0999": 0.100}
    out = HA.build_holdings_alert(held, _pool(pool), entry)
    got = {i["code"]: i["action"] for i in out["items"]}
    assert got["S0000"] == HA.ACTION_REDUCE
    assert got["S0100"] == HA.ACTION_WATCH
    assert got["S0999"] == HA.ACTION_HOLD
    assert got["S0500"] == HA.ACTION_UNDECIDED
    assert out["summary"] == {
        HA.ACTION_REDUCE: 1, HA.ACTION_WATCH: 1,
        HA.ACTION_HOLD: 1, HA.ACTION_UNDECIDED: 1,
    }


def test_a_missing_entry_score_is_explicitly_missing_not_substituted():
    """缺建仓日合成分 ⇒ 那一项**显式缺失**，不许拿别的顶替。

    🚨 这是「测不出 ≠ 没有」的落点：缺 ⇒ `relative_to_entry_score is None`
    且动作是 `UNDECIDED`，**不许**变成「没变差 ⇒ 持有」。
    """
    pool = _big_pool(1000)
    out = HA.build_holdings_alert([{"code": "S0000"}], _pool(pool), None)
    item = out["items"][0]
    assert item["entry_score"] is None
    assert item["relative_to_entry_score"] is None
    assert item["action"] == HA.ACTION_UNDECIDED
    assert "测不出" in item["action_criterion"]


def test_a_holding_absent_from_the_pool_is_undecided_not_hold():
    """不在全池里（被过滤/停牌/缺数据）⇒ **测不出**，不许判持有。"""
    pool = _big_pool(1000)
    out = HA.build_holdings_alert(
        [{"code": "NOT_IN_POOL"}], _pool(pool), {"NOT_IN_POOL": 1.0})
    item = out["items"][0]
    assert item["score"] is None
    assert item["rank_in_universe"] is None
    assert item["action"] == HA.ACTION_UNDECIDED


# --------------------------------------------------------------------------
# 4. 阈值不许手打：改锚 ⇒ 阈值跟着变
# --------------------------------------------------------------------------

def test_the_cutoffs_are_derived_from_the_anchors_not_typed():
    """阈值 = 1/排序分组数。改锚 ⇒ 阈值跟着变（不许 import 时定死）。"""
    cuts = HA.bottom_cuts()
    assert cuts["bottom_decile_percentile"] == pytest.approx(1.0 / HA.SORT_GROUPS_SINGLE)
    assert cuts["bottom_quintile_percentile"] == pytest.approx(1.0 / HA.SORT_GROUPS_DOUBLE)


def test_changing_the_anchor_moves_the_threshold():
    """🚨 核心守卫：**改锚 ⇒ 阈值跟着变**。

    若阈值在 import 时算死（写死 0.10），这条会红 ⇒ 说明阈值是手打的。
    """
    original = HA.SORT_GROUPS_SINGLE
    try:
        HA.SORT_GROUPS_SINGLE = 20          # 锚从 L=10 改成 L=20
        cuts = HA.bottom_cuts()
        assert cuts["bottom_decile_percentile"] == pytest.approx(0.05)
    finally:
        HA.SORT_GROUPS_SINGLE = original
    assert HA.bottom_cuts()["bottom_decile_percentile"] == pytest.approx(1.0 / original)


def test_the_anchor_change_actually_moves_a_verdict():
    """锚不只是数字变了 ⇒ **判决也跟着变**（否则锚是装饰）。

    两个锚各驱动一条不同的判决迁移：

    - `SORT_GROUPS_SINGLE`（十分位，REDUCE 的闸）：改小 ⇒ 底档变宽 ⇒ WATCH → REDUCE
    - `SORT_GROUPS_DOUBLE`（五分位，WATCH 的闸）：改大 ⇒ 底档变窄 ⇒ WATCH → HOLD

    ⚠️ 夹具选在**分位 0.4 与 0.099**（不是 0.5）：分位算的是 (N−rank)/(N−1)，
    池子正中那一只的分位是 0.5005 而不是 0.5 ⇒ 拿它测「阈值 ≤ 0.5」
    会落在边界外侧，测不出锚的作用（这是**构造错了**，不是实现错了）。
    """
    pool = _big_pool(1000)
    # 分位 ≈ 0.4004：最差 20% 之外、最差 50% 之内；且比建仓差
    held = [{"code": "S0400"}]
    entry = {"S0400": 0.600}
    assert HA.build_holdings_alert(held, _pool(pool), entry)["items"][0]["action"] == HA.ACTION_WATCH

    original_single = HA.SORT_GROUPS_SINGLE
    try:
        HA.SORT_GROUPS_SINGLE = 2          # 底档放宽到最差一半 ⇒ 进底档
        out = HA.build_holdings_alert(held, _pool(pool), entry)
        assert out["items"][0]["action"] == HA.ACTION_REDUCE
    finally:
        HA.SORT_GROUPS_SINGLE = original_single

    # 分位 ≈ 0.1502：最差 10% 之外、最差 20% 之内；且**不比建仓差**
    # ⚠️ `_big_pool` 里 S{i} 的分数就是 i/1000 ⇒ 指数小 = 分数低 = 排名差
    held2 = [{"code": "S0150"}]
    entry2 = {"S0150": 0.100}
    assert HA.build_holdings_alert(held2, _pool(pool), entry2)["items"][0]["action"] == HA.ACTION_WATCH
    original_double = HA.SORT_GROUPS_DOUBLE
    try:
        HA.SORT_GROUPS_DOUBLE = 50         # 底档收窄到最差 2% ⇒ 不再是底档
        out = HA.build_holdings_alert(held2, _pool(pool), entry2)
        assert out["items"][0]["action"] == HA.ACTION_HOLD
    finally:
        HA.SORT_GROUPS_DOUBLE = original_double


def test_the_anchors_are_declared_and_cited():
    """锚必须是**外部锚**（书里切组的方式），不是手打的魔数。"""
    assert HA.SORT_GROUPS_SINGLE == 10
    assert HA.SORT_GROUPS_DOUBLE == 5
    assert "cn_factor_lines.txt" in HA.ANCHOR_CITATION
    assert "L=10" in HA.ANCHOR_CITATION
    assert "L1=L2=5" in HA.ANCHOR_CITATION


# --------------------------------------------------------------------------
# 5. 能力守卫 + 判据必须写在产物里
# --------------------------------------------------------------------------

def test_the_detector_returns_a_real_verdict_on_a_realistic_shape():
    """能力守卫：**形状对就得出判决**，不许返回 None / 空。

    ⚠️ 只断言形状的守卫在「恒返 None」时全绿（第 120 层）。
    ⇒ 这里断言的是**具体动作**，不是「有东西返回」。
    """
    pool = _big_pool(1000)
    held = [{"code": "S0000", "name": "最差"}, {"code": "S0999", "name": "最好"}]
    out = HA.build_holdings_alert(held, _pool(pool), {"S0000": 0.9, "S0999": 0.1})
    assert out["items"][0]["action"] is not None
    assert out["pool_size"] == 1000
    assert out["summary"][HA.ACTION_REDUCE] == 1


def test_every_item_carries_its_own_criterion():
    """🚨 每条建议必须带**判据**——产物里不许出现「凭感觉的建议」。"""
    pool = _big_pool(1000)
    held = [{"code": "S0000"}, {"code": "S0999"}, {"code": "S0500"}]
    out = HA.build_holdings_alert(held, _pool(pool), {"S0000": 0.9, "S0999": 0.1})
    for item in out["items"]:
        crit = item["action_criterion"]
        assert crit, "判据不许为空"
        assert item["code"] in crit
        assert item["action"] in HA.ACTION_LABELS_CN


def test_the_percentile_orientation_is_lower_is_worse():
    """分位越低越差（0 = 最差那一只，1 = 最好那一只）。"""
    pool = _big_pool(1000)
    out = HA.build_holdings_alert(
        [{"code": "S0000"}, {"code": "S0999"}], _pool(pool),
        {"S0000": 0.9, "S0999": 0.1})
    by_code = {i["code"]: i for i in out["items"]}
    assert by_code["S0000"]["percentile"] < by_code["S0999"]["percentile"]
    assert by_code["S0000"]["percentile"] == pytest.approx(0.0)
    assert by_code["S0999"]["percentile"] == pytest.approx(1.0)


def test_the_tier_flags_nest_the_way_the_anchors_say():
    """十分位 ⊂ 五分位（最差 10% 必然也是最差 20%）。"""
    pool = _big_pool(1000)
    held = [{"code": f"S{i:04d}"} for i in (0, 50, 100, 150, 300)]
    entry = {c: 0.999 for c in ["S0000", "S0050", "S0100", "S0150", "S0300"]}
    out = HA.build_holdings_alert(held, _pool(pool), entry)
    by_code = {i["code"]: i for i in out["items"]}
    for code, item in by_code.items():
        if item["is_in_bottom_decile"]:
            assert item["is_in_bottom_quintile"] is True
    # 边界：1000 只池里最差 10% = 最差 100 只（S0000..S0099）
    assert by_code["S0000"]["is_in_bottom_decile"] is True
    assert by_code["S0100"]["is_in_bottom_decile"] is False
    assert by_code["S0100"]["is_in_bottom_quintile"] is True    # 最差 20% = S0000..S0199
    assert by_code["S0150"]["is_in_bottom_quintile"] is True
    assert by_code["S0300"]["is_in_bottom_quintile"] is False


# --------------------------------------------------------------------------
# 6. 诚实边界必须真的在产物里
# --------------------------------------------------------------------------

def test_the_boundaries_say_it_is_not_a_short_signal():
    """产物必须自带「不是做空」这条边界（A 股散户融不到券）。"""
    out = HA.build_holdings_alert([], _pool(_big_pool(1000)))
    blob = " ".join(out["boundaries"])
    assert "融不到券" in blob
    assert "不是涨跌预测" in blob


def test_the_decile_boundary_is_inclusive_and_is_explicitly_constructed():
    """🚨 底档边界**恰在阈值上**的那一只，必须显式构造出来测。

    ⚠️ 分位 = (N−rank)/(N−1)：阈值 0.1 恰落在某只身上，需要
    `rank = 0.9N + 0.1` 是整数 ⇒ **N ≡ 1 (mod 10)** ⇒ 取 N=11（rank=10，
    分位恰为 0.1）。用 N=1000 的池子**永远碰不到这个边界**（0.1×999=99.9
    不是整数名次）⇒ 那是**测度零点**，抽样/连续扫描结构性地看不见
    （第 190 层：边界必须显式构造，不许靠碰）。
    """
    pool = _pool({f"B{i:02d}": i / 11.0 for i in range(11)})   # 11 只 ⇒ rank 10 的分位恰为 0.1
    out = HA.build_holdings_alert([{"code": "B01"}], pool, {"B01": 1.0})
    item = out["items"][0]
    assert item["rank_in_universe"] == 10
    assert item["percentile"] == pytest.approx(0.1)
    assert item["is_in_bottom_decile"] is True, "分位恰好等于阈值 ⇒ 算进最差十分位（≤ 语义）"
    assert item["action"] == HA.ACTION_REDUCE


def test_the_quintile_boundary_is_inclusive_and_is_explicitly_constructed():
    """五分位边界同理：阈值 0.2 恰落在名次上需要 `0.8N + 0.2` 是整数 ⇒ N=6。"""
    pool = _pool({f"C{i:02d}": i / 6.0 for i in range(6)})      # 6 只 ⇒ rank 5 的分位恰为 0.2
    out = HA.build_holdings_alert([{"code": "C01"}], pool, {"C01": 1.0})
    item = out["items"][0]
    assert item["rank_in_universe"] == 5
    assert item["percentile"] == pytest.approx(0.2)
    assert item["is_in_bottom_quintile"] is True, "分位恰好等于阈值 ⇒ 算进最差五分位（≤ 语义）"
    assert item["is_in_bottom_decile"] is False
    # 它不比建仓差 ⇒ 落在「底档但未变差」⇒ WATCH（不是 REDUCE）
    assert item["action"] == HA.ACTION_WATCH


def test_the_module_writes_nothing_to_disk(tmp_path, monkeypatch):
    """🚨 纯函数层：不许在生产路径下留下任何字节。

    把 cwd 换到 tmp_path 再调一次 ⇒ 生产目录里不该多出任何东西。
    """
    before = sorted(p.name for p in (ROOT / "data" / "reports").glob("holdings_alert*")) \
        if (ROOT / "data" / "reports").exists() else []
    monkeypatch.chdir(tmp_path)
    HA.build_holdings_alert([{"code": "S0000"}], _pool(_big_pool(10)),
                             {"S0000": 0.5})
    after = sorted(p.name for p in (ROOT / "data" / "reports").glob("holdings_alert*")) \
        if (ROOT / "data" / "reports").exists() else []
    assert after == before
    assert not list(tmp_path.iterdir())


def test_reimporting_the_module_does_not_change_the_verdict():
    """重导入后判据不变（**没有** import 期状态漂移）。"""
    pool = _big_pool(1000)
    held = [{"code": "S0000"}]
    entry = {"S0000": 0.9}
    a = HA.build_holdings_alert(held, _pool(pool), entry)
    mod = importlib.reload(HA)
    b = mod.build_holdings_alert(held, _pool(pool), entry)
    assert a["items"][0]["action"] == b["items"][0]["action"]


# --------------------------------------------------------------------------
# 7. 测量台（scripts/measure_holdings_alert.py）：**拒绝出产物**这一关
# --------------------------------------------------------------------------

SCRIPT = ROOT / "scripts" / "measure_holdings_alert.py"


def _fake_stock(code: str, seed: int, n_days: int = 90):
    """造一只够长的假票（够 41 根历史、listed_days > 60、末根 = 信号日）。"""
    import numpy as np

    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2025-09-01", periods=n_days).strftime("%Y-%m-%d")
    close = 10.0 * np.exp(np.cumsum(rng.normal(0, 0.01, n_days)))
    return pd.DataFrame({
        "date": dates,
        "open": close * (1 + rng.normal(0, 0.002, n_days)),
        "high": close * (1 + abs(rng.normal(0, 0.004, n_days))),
        "low": close * (1 - abs(rng.normal(0, 0.004, n_days))),
        "close": close,
        "volume": rng.integers(1_000_000, 9_000_000, n_days).astype(float),
        "amount": rng.integers(1_000_000, 9_000_000, n_days).astype(float),
        "turn": rng.uniform(0.5, 3.0, n_days),
        "isST": np.zeros(n_days),
    })


def test_the_measurement_refuses_to_write_an_unverified_artifact(tmp_path):
    """🚨 没有生产产物可比对 ⇒ **拒绝出产物**（rc=2 且不落盘）。

    「自己重算自己不是证据，生产产物才是」——
    守卫存在的意义：删掉对拍闸门 ⇒ 本条立刻红（脚本会写出产物、rc=0）。
    """
    import subprocess

    data_root = tmp_path / "data"
    daily = data_root / "master" / "daily"
    daily.mkdir(parents=True)
    (data_root / "master" / "factor").mkdir(parents=True)
    for i, code in enumerate(["600001.SH", "600002.SH", "600003.SH"]):
        _fake_stock(code, seed=i).to_parquet(daily / f"{code}.parquet", index=False)

    out = tmp_path / "out.json"
    proc = subprocess.run(
        [sys.executable, str(SCRIPT),
         "--data-root", str(data_root),
         "--reports-dir", str(tmp_path / "reports"),
         "--out", str(out),
         "--as-of", "2025-12-15"],
        cwd=str(ROOT), capture_output=True, text=True)
    assert proc.returncode == 2, f"应当拒绝出产物，收到 rc={proc.returncode}\n{proc.stdout}\n{proc.stderr}"
    assert not out.exists(), "拒绝时**不许**留下产物（半截 JSON 会被当成有效读数）"


def test_the_measurement_captures_expect_bytes_as_the_first_executable_statement():
    """🚨 第 125 层：`expect_bytes` 必须取在 `main()` 的**第一条可执行语句**。

    放晚了 ⇒ 跑的和盖章的可能不是同一份字节 ⇒ 章变成**反向的错误证据**。
    （docstring 不是可执行语句，不算。）
    """
    import ast

    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    main_fn = next(n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == "main")
    body = main_fn.body
    # 第一条语句：docstring（若是）跳过
    idx = 0
    if (isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        idx = 1
    first = body[idx]
    src = ast.unparse(first)
    assert "expect_bytes" in src and "code_file_sha256" in src, (
        f"main() 第一条可执行语句必须是抓 expect_bytes，实际是：{src}")


def test_the_measurement_never_touches_the_production_reports_directory(tmp_path_factory):
    """测量台**不许**往生产报告目录写东西（除产物本身由调用方指定）。

    ⚠️ 判据是**行为**（跑一遍，看生产目录有没有多出文件），
    不是源码里有没有某个字符串 —— 后者会被 docstring 里的说明满足
    （子串断言 + 同一文件里的注释 = 守卫可被文档满足）。
    """
    import subprocess

    data_root = tmp_path_factory.mktemp("droot") / "data"
    daily = data_root / "master" / "daily"
    daily.mkdir(parents=True)
    (data_root / "master" / "factor").mkdir(parents=True)
    for i, code in enumerate(["600001.SH", "600002.SH"]):
        _fake_stock(code, seed=100 + i).to_parquet(daily / f"{code}.parquet", index=False)

    prod_reports = ROOT / "data" / "reports"
    before = sorted(p.name for p in prod_reports.glob("holdings_alert*")) \
        if prod_reports.exists() else []
    out = data_root.parent / "out.json"
    subprocess.run(
        [sys.executable, str(SCRIPT),
         "--data-root", str(data_root),
         "--reports-dir", str(data_root.parent / "reports"),
         "--out", str(out),
         "--as-of", "2025-12-15"],
        cwd=str(ROOT), capture_output=True, text=True)
    after = sorted(p.name for p in prod_reports.glob("holdings_alert*")) \
        if prod_reports.exists() else []
    assert after == before, f"生产报告目录被写入了：{set(after) - set(before)}"
    assert not out.exists()


def _load_measure_script():
    """按路径加载测量台（仓内测试惯用的加载方式）。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location("measure_holdings_alert", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _synthetic_parity_inputs(tmp_path, n=30):
    """造一份「重算池」+ 一份「生产产物」，供对拍函数判。"""
    ranked = pd.DataFrame({
        "code": [f"S{i:03d}" for i in range(n)],
        "score": [float(n - i) for i in range(n)],   # 严格降序
    })
    prod = ranked.copy()
    # 涨腿 = 前 10（= ranked.head(10)），跌腿 = **后** 10（= ranked.tail(10)）
    prod["direction"] = ["up"] * 10 + ["x"] * (n - 20) + ["down"] * 10
    prod["name"] = prod["code"]
    pdir = tmp_path / "predictions"
    pdir.mkdir(parents=True, exist_ok=True)
    prod.to_parquet(pdir / "pred_2026-01-02.parquet", index=False)
    return ranked, prod


def test_the_parity_gate_rejects_a_mismatch_in_either_leg(tmp_path):
    """🚨 对拍必须**两腿都查**：只查涨腿 ⇒ 跌腿错了也放行（变异 M8 曾存活）。

    造三份生产产物：
    ① 两腿都对 ⇒ MATCHES
    ② 涨腿对、**跌腿的代码被换掉** ⇒ 必须 MISMATCH
    ③ 跌腿分数差 **1e-6** ⇒ 必须 MISMATCH（容差是 1e-9）
    """
    mod = _load_measure_script()
    ranked, prod = _synthetic_parity_inputs(tmp_path)

    ok = mod._production_parity(tmp_path, "2026-01-02", ranked)
    assert ok["verdict"] == "MATCHES_PRODUCTION", ok

    # ② 跌腿换代码（涨腿不动）
    bad_codes = prod.copy()
    tail = bad_codes[bad_codes["direction"] == "down"].index
    bad_codes.loc[tail[0], "code"] = "ZZZ999"
    bad_codes.to_parquet(tmp_path / "predictions" / "pred_2026-01-02.parquet",
                         index=False)
    r = mod._production_parity(tmp_path, "2026-01-02", ranked)
    assert r["verdict"] == "MISMATCH_DO_NOT_TRUST_THIS_ARTIFACT", r
    assert r["up_leg"]["codes_match"] is True
    assert r["down_leg"]["codes_match"] is False

    # ③ 跌腿分数差 1e-6（> 容差 1e-9，< 放宽后的 1e-3 ⇒ 这条专杀「容差放宽」）
    prod.to_parquet(tmp_path / "predictions" / "pred_2026-01-02.parquet",
                    index=False)
    nudged = prod.copy()
    dn = nudged[nudged["direction"] == "down"].index[0]
    nudged.loc[dn, "score"] = nudged.loc[dn, "score"] + 1e-6
    nudged.to_parquet(tmp_path / "predictions" / "pred_2026-01-02.parquet",
                      index=False)
    r = mod._production_parity(tmp_path, "2026-01-02", ranked)
    assert r["verdict"] == "MISMATCH_DO_NOT_TRUST_THIS_ARTIFACT", r
    assert r["max_abs_score_diff"] == pytest.approx(1e-6)


def test_the_parity_tolerance_is_not_wider_than_one_nanounit():
    """容差必须**紧**（1e-9）：放宽它 ⇒ 分数真的错了也会被放过（变异 M9 曾存活）。

    ⚠️ 判据不能只写「容差 ≤ 某值」——那是一条**恒真**描述；
    真正的牙在上一条（合成 1e-6 偏差 ⇒ 必须判 MISMATCH）。
    """
    mod = _load_measure_script()
    assert mod.PARITY_TOL <= 1e-9


def test_the_reports_dir_default_is_empty_so_it_derives_from_the_data_root():
    """`--reports-dir` 默认必须是**空**（⇒ 回落成 `<data-root>/reports`）。

    写死成仓内 `data/reports` ⇒ 任何一次临时跑都会污染生产目录。
    """
    import ast

    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    defaults = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "add_argument":
            for kw in node.keywords:
                if kw.arg == "default" and isinstance(kw.value, ast.Constant):
                    flag = None
                    if node.args and isinstance(node.args[0], ast.Constant):
                        flag = node.args[0].value
                    defaults[flag] = kw.value.value
    assert defaults.get("--reports-dir", "") == "", \
        f"--reports-dir 默认必须为空，实际 {defaults.get('--reports-dir')!r}"


# --------------------------------------------------------------------------
# 8. 未定义名字守卫（**这一类 bug 是肉眼看不出来的**）
# --------------------------------------------------------------------------

def test_no_file_in_this_deliverable_references_an_undefined_name():
    """🚨 三个文件里**不许**出现「既不是局部、也不是模块级、也不是内置」的名字。

    起因（实测）：`scripts/measure_holdings_alert.py` 里有一行写成
    `len(heldings["codes"])`——**`heldings`**（h-e-l-d-i-n-g-s）而不是
    `holdings`（h-o-l-d-i-n-g-s）。**两个都是纯 ASCII，肉眼逐字读完全正常**，
    `git diff` 也看不出问题（它确实"是一个变量"），
    只有运行时才炸（NameError），而且炸在**整条链的最后一步**
    （前面 10 分钟的全池重算全白跑）。

    ## 判据为什么落在**字节码**上，而不是 `symtable`

    `symtable.Symbol.is_global()` 对**内联推导式里引用的名字**会给出
    误导性的答案（3.12+ PEP 709 把推导式内联进外层作用域，
    但符号分析阶段仍留有痕迹）⇒ 实测**修好之后它照样报同一个假阳性**。
    ⇒ 判据改问一个**运行时才知道**的问题：
    **这段代码实际会去全局找哪个名字？**（`LOAD_GLOBAL` / `LOAD_NAME`）
    那个名字既不在模块级、也不在内置 ⇒ 就是拼错了 ⇒ 运行时必炸。
    """
    import builtins
    import dis
    import types

    targets = [
        ROOT / "aplus" / "holdings_alert.py",
        ROOT / "scripts" / "measure_holdings_alert.py",
        ROOT / "tests" / "test_holdings_alert.py",
    ]
    offenders: list[str] = []
    for path in targets:
        src = path.read_text(encoding="utf-8")
        code = compile(src, str(path), "exec")
        # 模块级**名字表** = co_names（模块级赋值/引用的全局名）
        # ∪ 函数名（co_consts 里的 code object）∪ 内置
        module_names = set(code.co_names)
        module_names |= {c.co_name for c in code.co_consts
                         if isinstance(c, types.CodeType)}
        module_names |= set(dir(builtins))

        def walk(cobj, where):
            for instr in dis.get_instructions(cobj):
                if instr.opname in ("LOAD_GLOBAL", "LOAD_NAME"):
                    nm = str(instr.argval)
                    if nm not in module_names:
                        offenders.append(
                            f"{path.relative_to(ROOT)}:{instr.positions.lineno}: {nm}")
            for const in cobj.co_consts:
                if isinstance(const, types.CodeType):
                    walk(const, where + "/" + const.co_name)

        walk(code, "<module>")
    assert not offenders, "未定义的名字（拼错的标识符）：\n" + "\n".join(offenders)

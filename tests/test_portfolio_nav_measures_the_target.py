"""守卫：`portfolio_nav.json` 是不是**真的对着交付物测了 20%**。

## 这份守卫存在的理由

`compounded_stats()` 早就存在并已上线 `/api/status`，所以「有没有尺子」
从来不是问题。真正的风险有三个，逐条对应下面的 G1~G8：

1. **读数不是从尺子来的**（在脚本里重写一遍 CAGR ⇒ 同一个量两套口径）
2. **读数不带分辨率**（只报点估计，不报 MDE ⇒ 3 天噪音被说成「稳输」）
3. **读数不落盘/不盖章**（又变成一次 HTTP 响应里的临时数字）

## 写法上的三条纪律（上一段变异台教的）

- **钉值，不钉位置**：`expect_bytes = None` 让 `stamp()` 整项跳过字节检查，
  而它**留在原来的行号上** ⇒ 只钉行号的守卫对它完全失明。
- **`.mut` 规格文件不能有注释**（2026-10-16 实测）：harness 按 `\n\n` 切块后
  取首行做「标签<TAB>相对路径」，注释块首行不含 TAB ⇒ `sys.exit` ⇒
  **台子直接崩**。所以规格里的每一条说明只能活在测试的 docstring 里。
- **钉接线，不只钉存在**：产物层守卫对「当前代码」零覆盖——脚本改对了，
  章还是上次跑落盘的旧章。
- **hex 比较由程序做**：本文件只让程序打印布尔，断言里不出现手抄的摘要。
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "measure_portfolio_nav.py"
NAV = ROOT / "data/reports/portfolio_nav.json"
NAV_SIDE = ROOT / "data/reports/portfolio_nav.json.provenance.json"
VERIFY = ROOT / "data/reports/verification.parquet"

spec = pytest.importorskip("importlib")


def _load():
    import importlib.util
    m = importlib.util.spec_from_file_location("mpn", SCRIPT)
    mod = importlib.util.module_from_spec(m)
    m.loader.exec_module(mod)
    return mod


# ---------- 接线层：脚本本身 ----------

def test_g1_script_stamps_itself_under_its_own_name():
    """G1：产物必须盖章，且 `GENERATOR` 说的是它自己。

    指向 `.bak` 之类会让章上的 generator 字段说谎（守卫完全满意）。
    """
    src = SCRIPT.read_text(encoding="utf-8")
    assert 'GENERATOR = "scripts/measure_portfolio_nav.py"' in src
    assert "provenance.stamp(" in src


def test_g2_out_default_is_not_none():
    """G2：`--out` 默认值**不是** None。

    唯一写盘闸门是 `if args.out:` 那种形态 ⇒ 默认 None 时裸跑只打印、
    一个字节不落盘，而**进程退出码是 0**，看起来完全成功。
    """
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    found = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
        if name != "add_argument":
            continue
        kw = {k.arg: k.value for k in node.keywords}
        # ⚠️ 参数名可能出现在 **positional** 实参里（`add_argument("--out", …)`），
        #    也可能在 keyword 里（`add_argument(dest="out", …)`）⇒ 两种都要认。
        #    第一版只查 `kw`，于是扫出 0 处并报「找不到 --out 参数定义」——
        #    那是**探针**的错，不是实现的错（第 163 层：扫出 0 处先怀疑探针）。
        positional = [a.value for a in node.args if isinstance(a, ast.Constant)]
        if "--out" not in positional and "out" not in positional \
                and "out" not in kw:
            continue
        found = True
        d = kw.get("default")
        assert d is not None and not (isinstance(d, ast.Constant) and d.value is None), (
            "--out 的默认值是 None ⇒ 裸跑不落盘却退出 0")
    assert found, "找不到 --out 参数定义"


def test_g3_expect_bytes_is_a_call_captured_before_the_input_is_read():
    """G3：`expect_bytes` 必须是**一次 `code_file_sha256(...)` 调用**，且抓在读底稿之前。

    ⚠️ 传 `None` 会让 `stamp()` **整项跳过**字节检查（其 docstring 逐字：
    「不传 ⇒ 不做这项检查」），而它仍留在原行号上 ⇒ 只钉行号的守卫对它失明。
    这里同时钉**值**和**行号顺序**。
    """
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    caps, reads = [], []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "expect_bytes":
                    caps.append((node.lineno, node.value))
        if isinstance(node, ast.Call):
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
            if name == "read_parquet":
                reads.append(node.lineno)
    assert caps, "找不到 expect_bytes 的抓取点"
    assert reads, "找不到 read_parquet（底稿读取点）"

    first = caps[0][1]
    fname = None
    if isinstance(first, ast.Call):
        fn = first.func
        fname = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
    assert fname == "code_file_sha256", (
        f"expect_bytes 被赋成 {ast.unparse(first)!r}，不是一次 code_file_sha256(...)"
        " 调用 ⇒ stamp() 会整项跳过字节检查")

    lines = [ln for ln, _ in caps]
    assert min(lines) < min(reads), (
        f"expect_bytes 抓在第 {min(lines)} 行，底稿读在第 {min(reads)} 行"
        " ⇒ 抓取点在读取之后")


def test_g4_fingerprint_reads_bytes_never_mtime():
    """G4：指纹原语是 `read_bytes()`，不是 `st_mtime`。

    mtime 是代理不是证据：本仓的变异台会「改源码 → 跑测试 → 写回」，
    内容回到逐位相同而 mtime 被顶到现在。
    """
    src = SCRIPT.read_text(encoding="utf-8")
    fn = next((n for n in ast.walk(ast.parse(src))
               if isinstance(n, ast.FunctionDef) and n.name == "_sha256_of"), None)
    assert fn is not None, "找不到 _sha256_of"
    body = ast.unparse(fn)
    assert ".read_bytes()" in body, f"_sha256_of 不用 read_bytes：{body}"
    assert "st_mtime" not in body, f"_sha256_of 退回 mtime 代理：{body}"


def test_g5_mde_carries_the_annualisation_factor():
    """G5：MDE 公式里**必须有 252**（年化放大因子）。

    这是 2026-10-16 那次修正的本体。漏掉它会让 MDE 低估 **252 倍**，
    于是 3 天样本的 cagr 会被判成「低于 20%（超出 MDE）」——
    一个**方向乐观**的错误：它让人以为策略已被判决，不必再攒样本。
    实测 sd_log=0.005677、n=3 时：漏 252 → MDE 0.64%；带 252 → MDE 161.9%。

    这里钉的是**值**（TRADING_DAYS_PER_YEAR 参与乘法），不是钉文本里有没有 "252"——
    后者对「写成 250」「写成 TRADING_DAYS_PER_YEAR」都失明。
    """
    mod = _load()
    import pandas as _pd
    import numpy as _np
    daily = _pd.Series([0.001, -0.002, 0.0005])
    m = mod.cagr_mde(daily)
    sd_log = float(_np.std(_np.log1p(daily.to_numpy(float)), ddof=1))
    want = 1.96 * 252 * sd_log / (3 ** 0.5)
    assert m["sd_log"] == pytest.approx(sd_log, abs=1e-6)
    assert m["mde"] == pytest.approx(want, rel=1e-9), (
        f"MDE={m['mde']}，按含 252 的公式应为 {want}"
        " ⇒ 年化放大因子被漏掉了，预注册 §8 的修正被回退")


def test_g6_short_history_never_gets_a_direction_verdict():
    """G6：**结构闸门先于显著性判据**（预注册 §8 的 V7）。

    years < 1 时，连「超出 MDE」也不许变成方向判断——
    因为 MDE 只覆盖抽样误差，不覆盖「3 天代表一年」的预测误差。
    """
    mod = _load()
    for years in (0.012, 0.5, 0.999):
        v = mod.verdict_about_target(-0.5076, 1.6188, years)
        assert "高于" not in v and "低于" not in v, (
            f"years={years} 却给出了方向判断：{v}")
        assert "不给方向" in v, f"措辞不合规：{v}"
    # 满一年才允许方向判断，且仍受 MDE 约束。
    # ⚠️ 这两个期望值必须与 MDE 对齐：0.19 距 0.20 只有 0.01 < 0.05，
    #    所以「测不出符号」才是对的——第一版这里写 `0.19` 却期望「低于」，
    #    是**测试自己**的算术错误，实现没错。
    assert "测不出符号" in mod.verdict_about_target(0.19, 0.05, 1.0)
    assert "低于" in mod.verdict_about_target(0.10, 0.05, 1.5)
    assert "高于" in mod.verdict_about_target(0.30, 0.05, 1.5)


def test_g7_verdict_never_says_the_target_is_met():
    """G7：措辞守卫——产物里**不许**出现「达标」二字。

    「达标」在这套判据下是**不可判定的量**（只有 3 天观测），
    而它读起来像个结论。这是最便宜也最有效的一道守卫（第 124 层：
    判据不可问就等于没有守卫）。
    """
    if not NAV.is_file():
        pytest.skip("产物未生成")
    res = json.loads(NAV.read_text(encoding="utf-8"))
    blob = json.dumps(res, ensure_ascii=False)
    for banned in ("达标", "已完成目标", "满足目标"):
        assert banned not in blob, f"产物里出现了不可判定的措辞 {banned!r}"


def test_g8_product_stamp_matches_the_parquet_current_bytes():
    """G8：章里的底稿指纹 == 底稿**当前字节**（hex 比较由程序做）。

    ⚠️ 这是**产物层**守卫：只查源码形状的话，脚本改了而章还是上次跑落盘的
    旧章，它照样全绿（第 96/118 层）。
    """
    if not (NAV.is_file() and NAV_SIDE.is_file()):
        pytest.skip("产物未生成")
    sc = json.loads(NAV_SIDE.read_text(encoding="utf-8"))
    got = sc.get("source_parquet_sha256")
    assert got is not None, f"章里没有 source_parquet_sha256：{sc}"
    want = hashlib.sha256(VERIFY.read_bytes()).hexdigest()
    if got != want:
        assert False, ("章里的底稿指纹与底稿当前字节不相等 ⇒ "
                       f"章是过期的（章={got[:12]}… 盘上={want[:12]}…）")


def test_g9_reading_comes_from_the_ruler_not_a_local_reimplementation():
    """G9（最强的一条）：读数**必须来自 `compounded_stats`**。

    把尺子打成返回已知值的桩，再断言产物原样带出那个 `cagr`。
    若脚本里另写了一份 CAGR 公式，这条立刻红——而源码形状检查抓不到
    「公式被抄了一份还改小了」这种形态。
    """
    mod = _load()
    sentinel = -0.123456789

    class _Fake:
        cagr = sentinel
        total_return = -0.01
        arith_daily = -0.003
        arith_ann = -0.9
        ann_vol = 0.4
        vol_drag = None
        n_days = 3
        years = 0.0119

    rows = [{"pred_date": "2024-01-02", "code": f"{i:04d}.SZ", "direction": "up",
             "actual_pct": -0.003, "cost": 0.0, "result": "miss",
             "exit_date": "2024-01-03"} for i in range(10)]
    hist = pd.DataFrame(rows)
    diag = {"trading_days": 3, "excluded": {"no_data": 0, "flat": 0}}

    orig = mod.compounded_stats
    mod.compounded_stats = lambda _h: dict(_Fake.__dict__)
    try:
        res = mod.measure(hist, diag)
    finally:
        mod.compounded_stats = orig
    assert res["读数"]["cagr"] == sentinel, (
        "cagr 不是从 compounded_stats 读来的 ⇒ 脚本里另有一份 CAGR 公式")


def test_g11_caliber_is_written_into_the_product():
    """G11：产物正文必须带 C1「只持涨腿」的口径。

    数字对、口径缺，比数字错更难发现：读的人会把「组合」理解成
    20 只都持有，而跌腿我们从未持有过——那是两个量（第 7 层）。
    """
    if not NAV.is_file():
        pytest.skip("产物未生成")
    res = json.loads(NAV.read_text(encoding="utf-8"))
    cal = res.get("组合口径", "")
    for k in ("只持涨腿", "跌腿", "融不到券", "等权"):
        assert k in cal, f"产物口径里少了 {k!r}：{cal!r}"


def _claimed_sizes(caliber: str) -> list[int]:
    """从 `组合口径` 措辞里**解析**出它自称的逐窗口成分数。

    ⚠️ 必须解析，不能只断言「措辞里有数字」——
    只查数字存在的话，写死 `[10, 10, 10]` 也能过，而实测是 29/6/10。
    """
    m = re.search(r"成分数是 \[([\d, ]+)\]", caliber)
    assert m, f"组合口径里找不到「成分数是 [...]」的声明：{caliber!r}"
    return [int(x) for x in m.group(1).split(",")]


def _synthetic_history() -> pd.DataFrame:
    """两个 pred_date、**不重叠**的成交窗口，涨腿各 4/5 只。

    ⚠️ 刻意用与真产物**不同**的成分数（真产物是 29/6/10）——
    让本测试**测实现**而不是「拿现场当标准答案」（现场自己每天在长）。
    """
    rows = []
    for pd_, ex, k in (("2024-01-02", "2024-01-03", 4),
                       ("2024-01-03", "2024-01-04", 5)):
        for i in range(k):
            rows.append({"pred_date": pd_, "code": f"{pd_[-2:]}{i}",
                         "direction": "up", "actual_pct": 0.001 * (i + 1),
                         "cost": 0.0, "result": "hit", "exit_date": ex})
    return pd.DataFrame(rows)


def test_g14_caliber_assumptions_and_window_sizes_are_visible():
    """G14（三条，实测出来的）：产物必须说清它测的是**哪个组合**。

    2026-10-16 实测发现三条**任何文档都没写**的假设：
      ① 每天的名单全部执行（不是只看当天最新一份）
      ② 重叠窗口按并集合并（真产物实测 29 只，而交付形态是每天 10 只）
      ③ 没行情的标的不留现金（4 只 no_data 从分母消失 ⇒ 仓位转给其余 6 只）
    ⇒ 产物正文里的成分数必须与**实测逐窗口成分数**相等，
      否则「组合」这个词在产物里是无定义的（第 7 层）。
    """
    mod = _load()
    hist = _synthetic_history()
    res = mod.measure(hist, {"trading_days": 2,
                             "excluded": {"no_data": 0, "flat": 0}})
    assert res["逐窗口成分数"] == {"2024-01-03": 4, "2024-01-04": 5}
    assert _claimed_sizes(res["组合口径"]) == [4, 5], (
        "组合口径自称的成分数与实测逐窗口成分数不相等 ⇒ "
        "措辞与数据是**两个量**")

    a = res["测量假设"]
    assert isinstance(a, list) and len(a) == 3
    joined = "".join(a)
    for k in ("每天的名单全部执行", "并集合并", "不留现金"):
        assert k in joined, f"测量假设里少了 {k!r}：{a}"


def test_g15_real_product_carries_the_same_discipline():
    """G15：真产物也必须带上逐窗口成分数与三条假设。"""
    if not NAV.is_file():
        pytest.skip("产物未生成")
    res = json.loads(NAV.read_text(encoding="utf-8"))
    assert res.get("逐窗口成分数"), "真产物没有逐窗口成分数 ⇒ 「组合」无定义"
    assert _claimed_sizes(res["组合口径"]) == list(res["逐窗口成分数"].values())
    assert len(res.get("测量假设", [])) == 3
    # ⚠️ 「每天 10 只等权」这个**实测为错**的说法：**断言式禁止、否定式允许**。
    #   第一版做纯子串禁止，结果连 `C2 **不是「每天 10 只等权」**` 这种
    #   **主动否定它的引用**都被判违规 ⇒ 过严的守卫会把人推向
    #   「把否定句删掉」，而那会让读者看不出原来那个说法是错的（第 112 层通则）。
    cal = res["组合口径"]
    for m in re.finditer(r"每天 10 只等权", cal):
        assert cal[max(0, m.start() - 8):m.start()].find("不是") >= 0, (
            "口径里把「每天 10 只等权」当**断言**说了——实测成分数是 29/6/10，"
            "「10 只」是信号的数量，不是持仓的数量"
            "（要否定它可以，但必须带否定词）")


def test_g10_positive_control_the_guards_can_fail():
    """正控：这些守卫**能红**。全绿但失不掉的守卫等于没有守卫（第 122 层）。"""
    mod = _load()
    assert mod.cagr_mde(pd.Series([0.01])).get("mde") is None, "单日样本给出了 MDE"
    assert mod.cagr_mde(pd.Series([])).get("n") == 0
    # 本金亏光 ⇒ CAGR 无定义，不是「波动特别大」
    assert mod.cagr_mde(pd.Series([0.01, -1.0, 0.02]))["mde"] is None

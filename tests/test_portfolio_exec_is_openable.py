"""守卫：`portfolio_exec.json` 有没有真的回答「¥5 万开不写得出来」。

## 这份守卫存在的理由

`portfolio_nav.json` 测**净值**，它的 `测量假设` 第一条是「每天的名单全部执行」，
而**没有任何一条提到钱够不够**。

`lot_affordability.json` 早就警告过整手可行性，但它的宽度档是 10/30/100/200/546
—— **恰好没有 29**；而它自己的 `what_this_cannot_prove` 第 3 条逐字说
「它不知道策略实际会选哪 N 只 ⇒ 真实可行性取决于选股结果，**不等于池子比例**」。

⇒ 池子比例也不能拿来回答这个问题。**只能用策略实际选出的那批票，逐只查。**

## 写法上的四条纪律

- **钉值，不钉位置**：断言的是行为，不是「某个字符串出现在第几行」。
- **`.mut` 规格文件不能有注释**（2026-10-16 实测，harness 按 `\\n\\n` 切块后
  取首行做「标签<TAB>路径」，无 TAB 即 `sys.exit` ⇒ **整个进程退出**，
  不是「跳过那条」）。规格里的说明只能活在**本文件的 docstring** 里。
- **分类计数必须钉死**：本文件 G7 存在的直接原因是写这个产物时我自己踩了坑
  —— 用「买的手数 < 需要的份数」**一个**判据同时回答「买不起」和「买不满」，
  于是 `300019.SZ`（need=2、每份只够 1 手）被并进「买不起」，
  报出「买得起 16 / 买不起 12」，**两个数都是错的**（真值 17 与 11）。
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
SCRIPT = ROOT / "scripts" / "measure_portfolio_exec.py"
EXEC = ROOT / "data/reports/portfolio_exec.json"
EXEC_SIDE = ROOT / "data/reports/portfolio_exec.json.provenance.json"
VERIFY = ROOT / "data/reports/verification.parquet"

#: 预注册 E1/E2 —— 与 `lot_affordability.py:43-44` 同一个字面量。
CAPITAL = 50_000.0
LOT = 100


def _load():
    import importlib.util
    m = importlib.util.spec_from_file_location("mpe", SCRIPT)
    mod = importlib.util.module_from_spec(m)
    m.loader.exec_module(mod)
    return mod


def _product():
    if not EXEC.is_file():
        pytest.skip("产物未生成")
    return json.loads(EXEC.read_text(encoding="utf-8"))


# ---------- 接线层 ----------

def test_g1_script_stamps_itself_under_its_own_name():
    """G1：产物必须盖章，且 `GENERATOR` 说的是它自己。"""
    src = SCRIPT.read_text(encoding="utf-8")
    assert 'GENERATOR = "scripts/measure_portfolio_exec.py"' in src
    assert "provenance.stamp(" in src


def test_g2_capital_and_lot_are_the_preregistered_literals():
    """G2：`CAPITAL` / `LOT` 必须逐字等于预注册 E1/E2。

    钉**值**不钉文本：写成 `50e3` 或 `CAPITAL_50K` 语义相同但会被
    「文本里有 50_000」这种守卫放过，改成别的数却抓不到。
    """
    mod = _load()
    assert mod.CAPITAL == CAPITAL
    assert mod.LOT == LOT


def test_g3_sha_helper_reads_bytes_never_mtime():
    """G3：指纹原语是 `read_bytes()`，不是 `st_mtime`。

    mtime 是代理不是证据：本仓的变异台会「改源码 → 跑测试 → 写回」，
    内容回到逐位相同而 mtime 被顶到现在（第 122 层）。
    """
    src = SCRIPT.read_text(encoding="utf-8")
    fn = next((n for n in ast.walk(ast.parse(src))
               if isinstance(n, ast.FunctionDef) and n.name == "_sha256_of"), None)
    assert fn is not None, "找不到 _sha256_of"
    body = ast.unparse(fn)
    assert ".read_bytes()" in body, f"_sha256_of 不用 read_bytes：{body}"
    assert "st_mtime" not in body, f"_sha256_of 退回 mtime 代理：{body}"


def test_g4_expect_bytes_captured_before_the_input_is_read():
    """G4：`expect_bytes` 必须是 `code_file_sha256(...)` 调用，且抓在读底稿之前。

    传 `None` 会让 `stamp()` **整项跳过**字节检查（其 docstring 逐字
    「不传 ⇒ 不做这项检查」），而它仍留在原行号上 ⇒ 只钉行号的守卫对它失明。
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
            if name == "read_parquet" and "VERIFY" in ast.unparse(node):
                reads.append(node.lineno)
    assert caps, "找不到 expect_bytes 的抓取点"
    first = caps[0][1]
    fname = None
    if isinstance(first, ast.Call):
        fn = first.func
        fname = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
    assert fname == "code_file_sha256", (
        f"expect_bytes 被赋成 {ast.unparse(first)!r}，不是 code_file_sha256(...)"
        " ⇒ stamp() 会整项跳过字节检查")
    assert reads, "找不到读 VERIFY 的 read_parquet"
    assert min(ln for ln, _ in caps) < min(reads), (
        "expect_bytes 抓在读底稿之后 ⇒ 章盖的不是这次跑的那批字节")


def test_g8_script_contains_no_return_formula_at_all():
    """G8（最强的一条）：本脚本**一道收益公式都不许有**。

    收益归 `portfolio_nav.json`。一个量只由一处算（第 109 层）——
    两个产物各写一份 CAGR，就是当年 `compounded_stats()` 存在的理由。

    ⚠️⚠️ **只扫可执行代码，不扫 docstring 与注释**（2026-10-16 实测的修正）：
    第一版扫**整个源码文本**，于是模块 docstring 里那句解释
    「两个产物各写一份 CAGR 就是当年 `compounded_stats()` 存在的理由」
    被判违规 ⇒ **逼我把这段说明删掉**。

    ⚠️ 那是第 112 层的典型形态：一个扫错**范围**的禁止项，效果与
    「禁止一切」相同（`all()` 退化成装饰），而症状看起来像「守住了」。
    ⇒ 改成扫 AST：docstring 是 `Constant` 节点，直接排除；
    注释在 AST 里**根本不存在** ⇒ 天然免疫。
    """
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    # 收集所有**不是** docstring 的字符串常量节点
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            d = ast.get_docstring(node, clean=False)
            if d is not None:
                docstrings.add(d)
    code_strings = [n.value for n in ast.walk(tree)
                    if isinstance(n, ast.Constant) and isinstance(n.value, str)
                    and n.value not in docstrings]
    code_names = [n.id for n in ast.walk(tree) if isinstance(n, ast.Name)]
    code_attrs = [n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)]
    haystack = "\n".join(code_strings + code_names + code_attrs)
    for banned in ("compounded_stats", "cagr", "CAGR", "total_return",
                   "ann_vol", "actual_pct"):
        assert banned not in haystack, (
            f"**可执行代码**里出现了 {banned!r} —— 收益是 portfolio_nav.json 的量，"
            "本脚本一道收益公式都不该有（一个量只由一处算）")
    # 反向自查：docstring 确实**还留着**那两处提及 ⇒ 证明本守卫没有靠删说明变绿
    body = SCRIPT.read_text(encoding="utf-8")
    assert "compounded_stats" in body and "actual_pct" in body, (
        "docstring 里的说明不见了 —— 说明有人为了让 G8 变绿把说明删了，"
        "那正是过严守卫会引发的动作（第 112 层）")


# ---------- 算法层：钉行为不钉形状 ----------

def test_g5_spend_is_capped_by_need():
    """G5：`_spend` 必须被 `need` **封顶**。

    这是「A 与 B 投入额相同」的**成因**：绝大多数票 need=1，
    每份金额提高也买不到第 2 手 ⇒ 多出来的钱无处可去。
    去掉封顶，B 口径会虚增投入额，而那是**开不出来的账户**。
    """
    mod = _load()
    # 每份 10,000 元，1 手只值 100 元 ⇒ 买得起 100 手，但只需要 2 份
    assert mod._spend(10_000.0, 100.0, 2) == 2, "没有按 need 封顶"
    assert mod._spend(10_000.0, 100.0, 1) == 1, "need=1 时多买了"
    assert mod._spend(50.0, 100.0, 1) == 0, "买不起却报了正数"
    assert mod._spend(100.0, 100.0, 3) == 1, "恰好够 1 手却不是 1"


def test_g7_classify_separates_cannot_afford_from_cannot_fill():
    """G7（本文件存在的直接原因）：三个计数必须**互斥且可加**。

    我先前用「买的手数 < 需要的份数」一个判据同时回答两个问题，
    把 `need=2 / 买得到 1 手` 的票并进「买不起」⇒ 报出「买得起 16 /
    买不起 12」，**两个数都是错的**（真值 17 与 11）。

    这里用一组构造数据把它钉死：3 只票，1 只买不起、1 只买 1 手（需 2）、
    1 只买满 2 手 ⇒ 「买不起」绝不能把第 2 只算进去。
    """
    mod = _load()
    # ⚠️ 这三只的价格是**按「1手价 vs 每份金额」心算出来的**，而且**算错过一次**
    #   （第一版 BBB=1.0 ⇒ 1手100元、每份500 ⇒ 500//100=5 手封顶 need=2 ⇒ 其实是买满，
    #   于是「买满」数从 1 变 2、部分从 1 变 0）⇒ 期望值必须**程序验**，不能心算。
    #   这里的判定式统一写成：买得起 k 手 ⟺ price × 100 × k ≤ per_leg。
    legs = [
        {"code": "AAA", "need": 1, "price": 10.0},    # 1手=1000 > 500 ⇒ 买不起
        {"code": "BBB", "need": 2, "price": 3.0},     # 1手=300 ≤ 500 ⇒ 买 1 手，2手=600 > 500 ⇒ 买不满
        {"code": "CCC", "need": 2, "price": 0.2},     # 1手=20，2手=40 ≤ 500 ⇒ 买满
    ]
    r = mod._classify(legs, 500.0, LOT)
    assert r["连1手都买不起_票数"] == 1, f"买不起的必须是 1 只：{r}"
    assert r["买得起1手_票数"] == 2, f"买得起 1 手的必须是 2 只：{r}"
    assert r["买满所需份数_票数"] == 1, f"买满的必须是 1 只：{r}"
    assert r["买得起但买不满_票数"] == 1, f"部分买入必须是 1 只：{r}"
    # 恒等式：买得起 = 买满 + 部分；买不起与买得起互斥
    assert (r["买得起1手_票数"]
            == r["买满所需份数_票数"] + r["买得起但买不满_票数"])
    assert [d["code"] for d in r["买得起但买不满_明细"]] == ["BBB"]


def test_g6_reallocate_reaches_a_fixed_point():
    """G6：B 口径必须**迭代到不动点**，并把轮数落盘。

    一轮就停 = 「剔除后没重算每份金额」，那不是重分配，是换个说法。
    """
    mod = _load()
    # 判定式：1手价 = price × 100；每份 = 本金 / 行数 = 2000/4 = 500。
    # ⚠️ 贵票必须 **1手 > 500** 才买得起「零手」⇒ price > 5.0。
    #   第一版写 price=3.0（1手=300 ≤ 500 ⇒ 照样买得起 1 手）⇒ 第 1 轮就收敛，
    #   守卫报「只迭代了 1 轮」——**那是构造数据的错，不是实现的错**。
    legs = [{"code": f"{i:02d}", "need": 1,
             "price": (0.05 if i < 2 else 9.0)}      # 9.0 ⇒ 1手900 > 500 ⇒ 买不起
            for i in range(4)]
    r = mod._reallocate(legs, 2000.0, LOT)
    assert r["不动点轮数"] >= 2, f"只迭代了 {r['不动点轮数']} 轮 ⇒ 没到不动点：{r}"
    assert r["被剔除_票数"] == 2, f"剔除数不对：{r}"
    assert r["存活的票数"] == 2
    # 剔除两只贵的后，每份从 500 涨到 1000
    assert r["每份金额"] == pytest.approx(2000.0 / 2), f"剔除后每份金额不对：{r}"
    assert r["实际投入"] <= 2000.0 + 1e-9, "投入超过本金"


def test_g10_no_price_is_not_treated_as_zero_cost():
    """G10（E6）：取不到行情的票**不许**当成 0 元（= 白送）。

    缺失 ≠ 买得起（第 153 层：查不到 ≠ 没有）。
    一张 0 元的票会「买得起无限手」，把投入率算成 100%。
    """
    mod = _load()
    legs = [{"code": "A", "need": 1, "price": None},
            {"code": "B", "need": 1, "price": 1.0}]
    r = mod._hold_cash(legs, 1000.0, LOT)
    assert r["price_missing"] == 1, f"缺价没被记账：{r}"
    assert r["实际投入"] == 100.0, f"缺价票被当成 0 元买进了：{r}"
    assert r["投入率"] == pytest.approx(0.10), f"投入率被缺价票虚增：{r}"
    assert mod._classify(legs, 500.0, LOT)["买得起1手_票数"] == 1, (
        "缺价票被算成「买得起」了")


# ---------- 产物层 ----------

def test_g9_stamp_matches_the_parquet_current_bytes():
    """G9：章里的底稿指纹 == 底稿**当前字节**（hex 比较由程序做）。

    ⚠️ 产物层守卫对「当前代码」零覆盖——脚本改对了，章还是上次跑落盘的旧章。
    """
    if not (EXEC.is_file() and EXEC_SIDE.is_file()):
        pytest.skip("产物未生成")
    sc = json.loads(EXEC_SIDE.read_text(encoding="utf-8"))
    got = sc.get("source_parquet_sha256")
    assert got is not None, f"章里没有 source_parquet_sha256：{sc}"
    want = hashlib.sha256(VERIFY.read_bytes()).hexdigest()
    assert got == want, (
        "章里的底稿指纹与底稿当前字节不相等 ⇒ 章是过期的"
        f"（章={str(got)[:12]}… 盘上={want[:12]}…）")


def test_g11_every_window_carries_the_five_mandatory_fields():
    """G11（X1）：每个成交窗口必须有 X1 规定的字段，一个都不许省。"""
    res = _product()
    w = res.get("逐窗口") or {}
    assert w, "产物里没有逐窗口"
    for day, ww in w.items():
        for k in ("名义行数", "去重code数", "A_hold_cash", "B_reallocate"):
            assert k in ww, f"{day} 缺字段 {k!r}"
        for caliber in ("A_hold_cash", "B_reallocate"):
            for k in ("实际投入", "闲置现金", "投入率"):
                assert k in ww[caliber], f"{day}.{caliber} 缺 {k!r}"
            idle = ww[caliber]["闲置现金"]
            inv = ww[caliber]["实际投入"]
            assert idle + inv == pytest.approx(CAPITAL), (
                f"{day}.{caliber}：投入 {inv} + 闲置 {idle} != 本金 {CAPITAL}")
        assert ww["名义行数"] >= ww["去重code数"], (
            f"{day}：行数 {ww['名义行数']} < 去重 {ww['去重code数']} ⇒ 不可能")


def test_g12_product_counts_are_internally_consistent():
    """G12：三个分类数在**真实产物**上也必须满足 G7 的恒等式。

    G7 用构造数据测算法；这条测「产物真的把它落下来了」——
    两者是**两个量**，只测一个等于没测另一个。

    ⚠️⚠️ **A 与 B 的恒等式分母不同，这是第一版写错的坑**：
      口径 A 在**全部**票上分类 ⇒ `买不起 + 买得起 + 缺价 == 去重code数`；
      口径 B 剔除买不够的之后才在 `alive` 上分类 ⇒
      `买不起 + 买得起 + 缺价 == **存活票数**`，而 `存活票数 + 被剔除 == 去重code数`。
    ⇒ 拿 A 的式子去判 B，会把「B 真的剔掉了 11 只」读成「计数不自洽」，
      而当时的修法（在实现里把 B 的分类改回 legs 上算）**正好装回
      「分类与实际购买集合不是同一个量」那个 bug**。
      ⇒ 这是本条存在的理由，也是第 7 层在我自己的新守卫上复发。
    """
    res = _product()
    for day, ww in sorted((res.get("逐窗口") or {}).items()):
        for cal in ("A_hold_cash", "B_reallocate"):
            c = ww[cal]
            zero = c["连1手都买不起_票数"]
            one = c["买得起1手_票数"]
            full = c["买满所需份数_票数"]
            part = c["买得起但买不满_票数"]
            assert one == full + part, (
                f"{day}.{cal}：买得起 {one} != 买满 {full} + 部分 {part}")
            # 口径 A：分母是全部票；口径 B：分母是存活票
            denom = (c["存活的票数"] if cal == "B_reallocate" else ww["去重code数"])
            assert zero + one + c["price_missing"] == denom, (
                f"{day}.{cal}：买不起 {zero} + 买得起 {one} + 缺价 "
                f"{c['price_missing']} != 分母 {denom}")
            assert 0 <= zero and 0 <= one, f"{day}.{cal} 出现负计数"
        b = ww["B_reallocate"]
        assert b["存活的票数"] + b["被剔除_票数"] == ww["去重code数"], (
            f"{day}：存活 {b['存活的票数']} + 被剔除 {b['被剔除_票数']}"
            f" != 去重票数 {ww['去重code数']}")
        assert b["不动点轮数"] >= 1, f"{day}：B 口径必须至少迭代 1 轮"


def test_g13_both_calibers_are_reported_never_one():
    """G13（预注册 §0）：A 与 B **都**必须在产物里，不许只报一个。

    判据是在看到两个读数之后写的 ⇒ 冒充预注册会让自己有「挑一个」的机会。
    挑选本身是错的方向，所以这里钉死「两个都在」。
    """
    res = _product()
    blob = json.dumps(res, ensure_ascii=False)
    for k in ("A_hold_cash", "B_reallocate"):
        assert k in blob, f"产物里少了口径 {k!r} ⇒ 只报了一个 = 事后挑选"
    for day, ww in (res.get("逐窗口") or {}).items():
        a, b = ww["A_hold_cash"]["实际投入"], ww["B_reallocate"]["实际投入"]
        assert isinstance(a, (int, float)) and isinstance(b, (int, float)), (
            f"{day}：两个口径的投入额必须都是数")


def test_g14_verdict_wording_is_from_the_preregistered_set():
    """G14（X3/E7）：措辞只能来自预注册的集合，不许出现「达标」这类不可判定的说法。"""
    res = _product()
    v = res["verdict_about_exec"]
    assert re.match(r"^(可开|部分开不出|判不出（无行情）)", v), f"措辞不合规：{v!r}"
    assert re.search(r"闲置 [\d.]+%", v), f"X3 要求闲置率与措辞并排印出：{v!r}"
    blob = json.dumps(res, ensure_ascii=False)
    for banned in ("达标", "已完成目标", "满足目标"):
        assert banned not in blob, f"产物里出现了不可判定的措辞 {banned!r}"


def test_g15_product_does_not_copy_the_upstream_verdict():
    """G15（X4）：不许把 `portfolio_nav.json` 的净值判决复制过来当结论。

    两个产物回答**两个问题**：净值 vs 能不能开。混起来是第 7 层。
    """
    res = _product()
    assert "20%" not in json.dumps(res, ensure_ascii=False), (
        "本产物只回答「开不开得出来」；年化目标由 portfolio_nav.json 判决。"
        "把 20% 写进来会让读者以为这里也判了年化")
    assert res.get("上游产物") == "data/reports/portfolio_nav.json"
    # ⚠️ 必须搜**整个产物**而不是 `must in res`（只查顶层**键**）——
    #   第一版写 `must in res`，而「不算任何收益」在 `本产物算什么` 的**值**里
    #   ⇒ 报「产物必须写明」而字段其实就在那儿。那是**探针**的错（第 163 层）。
    blob = json.dumps(res, ensure_ascii=False)
    for must in ("不算任何收益", "verdict_aggregation"):
        assert must in blob, f"产物必须写明 {must!r}（读者无从知道它是另一个量）"


def test_g17_verdict_really_takes_the_worst_window():
    """G17：产物自称「取逐窗口**最差**」，就得**真的**取最差。

    ⚠️ 这是「只钉存在不钉性质」那条（第 79 层同族）：`verdict_aggregation`
    字段写着「取逐窗口最差（任一窗口开不出来即算开不出来）」，
    而 G14 只查措辞**格式** ⇒ 把 `min(...)` 改成 `max(...)` 之后
    措辞照样合规、守卫照样全绿，而**结论反过来了**。

    ⇒ 这里把产物里印出的那个百分比，与**程序重算的最差值**比。
      任何聚合方式的改动都必须同步改这里的期望算法，否则立刻红。
    """
    res = _product()
    v = res["verdict_about_exec"]
    m = re.search(r"闲置 ([\d.]+)%", v)
    assert m, f"X3 要求闲置率与措辞并排印出：{v!r}"
    claimed = float(m.group(1)) / 100.0
    worst = 1.0 - min(w["A_hold_cash"]["投入率"]
                     for w in (res.get("逐窗口") or {}).values())
    assert claimed == pytest.approx(worst, abs=0.001), (
        f"verdict 印的闲置 {claimed:.4f} != 程序重算的最差闲置 {worst:.4f} ⇒ "
        f"聚合方式与产物自称的不一致：{v!r}")
    # 方向性：措辞必须与阈值一致，不许「闲置 60%」却写「可开」
    idle_limit = res["idle_cash_limit"]
    assert (claimed < idle_limit) == v.startswith("可开"), (
        f"闲置 {claimed:.2%} vs 阈值 {idle_limit:.0%} ⇒ 措辞 {v!r} 与之矛盾")


def test_g16_positive_control_the_guards_can_fail():
    """正控：这些守卫**能红**。全绿但失不掉的守卫等于没有守卫（第 122 层）。

    🚨 2026-10-16：这条曾经**整条消失**，而症状伪装得很好 —— 上面 G17 加进来
    之后收集数从 16 仍是 16（不是 17）。原因是我的 Edit 把这一行的
    `def` 当 `old_string` 吃掉了，正控代码于是变成 G17 函数体的**尾部**。

    ⇒ 两个后果，第二个才是致命的：
      1. 收集数不变 ⇒ 「16 == 16」看起来像「没影响」，我差点就这么放过；
      2. G17 一旦红，正控代码**根本不会执行** ⇒ 别的守卫失灵时**没有兜底**。
    ⇒ 「与上次同样的通过数」不是「没有变化」的证据（第 110 层又一次）。
    """
    mod = _load()
    # 缺价票若被当成 0 元，投入率会变成 100% —— 正控要能抓到那种退化
    legs = [{"code": "A", "need": 1, "price": None}]
    r = mod._hold_cash(legs, 1000.0, LOT)
    assert r["投入率"] == 0.0, "只有缺价票时投入率必须是 0，不是 1.0"
    # 全买不起 ⇒ 不动点必须终止而不是死循环
    allcostly = [{"code": f"{i}", "need": 1, "price": 99.0} for i in range(3)]
    rr = mod._reallocate(allcostly, 300.0, LOT)
    assert rr["存活的票数"] == 0, f"全买不起却存活了票：{rr}"
    assert rr["实际投入"] == 0.0

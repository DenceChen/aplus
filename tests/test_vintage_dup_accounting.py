"""去重计数必须落进账本，而不只活在 stdout（第 19 层在 PIT 链上的第二面）。

## 洞在哪（2026-10-15 取证，不是推理）

`dedupe_rows()` **早就返回被丢行数**，docstring 还逐字写着「这个数必须能被看见」。
`collect()` 也早就把它算全了：

| 数 | 在哪量 | 覆盖谁 |
|---|---|---|
| `dup_dropped` | L262 接住已落盘分区的重复 + **L306** `raw_n − len(full)` 补回 `_merge` 丢的那份 | 续跑失效 + 合并去重 |
| `source_dup` | **L285**，在 L289 去重**之前**量，注释写着「这是唯一能看见源站自身重复的时刻」 | 源站自身重复 |

⇒ **两处 `dedupe_rows(...)[0]` 都不是静默丢失**（L306 / L285 各有补偿）。
本文件**刻意不写**「不许 `[0]`」那条守卫：它会是**过严的假守卫**，
而过严的守卫会把人推向「去改本来正确的代码」（第 112 层），它比恒真还贵。

**真正的洞只有一个**：这两个数只 `print` 到 stdout，而 L425 逐字写着
「**不登记 manifest**」—— 那是当年的一个**决定**，不是 bug。
⇒ 而 `data/vintage.log` **不是可引用的账本**：它不进 git、会被轮转、没人对账。
⇒ 后果：将来翻账本想知道「这份快照当天有没有丢过行」，**唯一的答案是不许知道**。

## 为什么必须可区分「没声明」与「声明了是 0」

历史条目（2026-10-14/15）**永久没有**这两个字段。
若 `write_snapshot` 拿不到就写 `0`，则「当年没量过」与「当天量过、确实没有」
**逐位同形** ⇒ 这个字段从此只在它为真的那天有意义，而**没有任何东西会说它失效了**
⇒ 形状 6「假装能调」的镜像：一个**永远为 0** 的字段比没有字段更坏。
⇒ 故 **`None` ⇒ 键缺席**（诚实缺席，不是假 0）。

## 已有的同语义守卫（本文件不许重复）

| 谁已经守 | 守的是什么 |
|---|---|
| `tests/test_vintage_snapshot_provenance.py` | 章的接线（`expect_bytes` / `data_root` / 取样点 / 生成器登记） |
| `tests/test_vintage.py` | 落盘不覆盖历史、`amend=False` 抓篡改、`sha256` 可复现 |

⚠️ 本文件守的是**另一个维度**：章说「谁造的这份产物」，
本文件说「造它那天**丢过什么**」—— 两者是**独立的字段集**，
而形状 19 的原型正是「章里没有字段能表达这件事」。

## ⚠️ 变异必读

判「这组守卫是不是安慰剂」**只能靠变异**，且必须**先确认变异真的落进了文件**
（`grep` 新串 ⇒ 再看 pytest 结果，顺序反过来会得出相反结论，第 14/148 层）。
**只有 `rc == 1` 才算被杀**；rc∈{2,3,4,5} 是收集/用法/内部错误，**不可验证**。

**2026-10-15 实测 6/6 全杀（基线已验绿，每条都是 `rc=1` 且指名到守卫）**：

| 变异 | 被谁杀掉 |
|---|---|
| D1 采集器传字面量 `0` | `…hands_the_counts_it_measured_to_the_writer` |
| D2 采集器整个不传这两个实参 | 同上 |
| D3 写盘点收到计数却写死成 `0` | `…are_the_counts_the_ledger_records` |
| D4 未声明时回落成 `0` | `…leaves_the_key_absent_rather_than_writing_zero` |
| D5 「涉及几只」与「丢了几行」对调 | `…are_the_counts_the_ledger_records` |
| D6 源站有重复时只打印不拦 | `…refuses_to_register_a_snapshot_with_source_dups` |

🚨 **本轮第一次跑变异台时 6 条全是「旧串匹配 0 处」——
那是第 122 层，不是守卫有洞。** 规格里多行锚点的**首行也缩进了 4 格**，
而 `dedent` 取块内最小缩进 ⇒ **续行被一起减掉 4 格**（26 → 22），源码是 26
⇒ 子串匹配不上。
⇒ 修法：**首行写到列 0（子串匹配仍命中），续行保持源码真实缩进**。
⇒ 判「是规格写错还是守卫有洞」的第一步永远是 `shasum -c` 复验还原，
第二步是把解析出的锚点 `repr()` 出来**看真实缩进**，而不是重写守卫。

★ **D5 是本组最值钱的一条**：它把 `len()` 与 `sum()` 对调，
而两个键名**都在 manifest 里、都是整数、都是「源站重复」**——
读账本的人**看不出哪个是哪个**，直到有人去核对「一只票重复 50 次只丢 50 行还是 1 行」。
⇒ **「字段存在」与「字段是它声称的那个量」是两个命题**（第 144 层）。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pandas as pd

from aplus.vintage import read_manifest, write_snapshot

ROOT = Path(__file__).resolve().parents[1]
COLLECTOR = ROOT / "scripts" / "collect_financial_vintage.py"

DUP_FIELD = "n_dup_dropped"
SRC_CODES_FIELD = "n_source_dup_codes"
SRC_ROWS_FIELD = "n_source_dup_rows"


# ── 造夹具 ─────────────────────────────────────────────────────────

def _raw(day: str, code: str = "000001", bps: float = 10.0) -> pd.DataFrame:
    """与 `tests/test_vintage.py::_raw` 同形状（不在那里 import，避免耦合）。"""
    return pd.DataFrame({
        "SECURITY_CODE": [code],
        "REPORT_DATE": ["2021-12-31"],
        "REPORT_TYPE": ["年报"],
        "NOTICE_DATE": ["2022-04-28"],
        "UPDATE_DATE": ["2022-04-28"],
        "BPS": [bps],
        "ROEJQ": [0.12],
    })


# ── ① 行为层：拿到的数必须真的落进 manifest ───────────────────────

def test_the_counts_handed_to_the_writer_are_the_counts_the_ledger_records(tmp_path):
    """`write_snapshot` 拿到的两个数必须**逐个**出现在最后一条 manifest 里。

    🚨 数字**不许被断言成「看起来对」**：这里断言的是**同一批值**，
    而不是「某个数大概对」—— 第 109 层的教训是「用公式重算当验证」，
    而这里根本**不该重算**，因为传入的数**没有闭式可推**：
    `dup_dropped` 含中间 flush 的丢弃，落盘帧里推不出来。
    """
    write_snapshot(tmp_path, "2026-10-15", _raw("2026-10-15"),
                   dup_dropped=7, source_dup={"000001.SZ": 3, "600000.SH": 2})
    e = read_manifest(tmp_path)[-1]
    assert e[DUP_FIELD] == 7, (
        f"manifest 的 `{DUP_FIELD}` = {e.get(DUP_FIELD)!r}，而调用方声明了 7。"
        f"\n  ⇒ 计数在写盘点被丢掉 ⇒ `print` 过的数与账本里的数是两回事，"
        f"而读账本的人看不出哪个是真的。")
    assert e[SRC_CODES_FIELD] == 2, (
        f"manifest 的 `{SRC_CODES_FIELD}` = {e.get(SRC_CODES_FIELD)!r}，"
        f"而调用方声明了 2 只票有源站重复。")
    assert e[SRC_ROWS_FIELD] == 5, (
        f"manifest 的 `{SRC_ROWS_FIELD}` = {e.get(SRC_ROWS_FIELD)!r}，"
        f"而调用方声明的是 3+2=5 行。"
        f"\n  ⚠️ 「涉及几只」与「丢了几行」是两个量："
        f"一只票重复 50 次只涉及 1 只，却丢 50 行 —— "
        f"只记前者会让清理工作量被严重低估。")


def test_the_positive_control_frame_is_one_that_could_carry_the_fields(tmp_path):
    """**正控**：上一条不是恒真。

    恒等的写法是「读 manifest ⇒ 断言它在」而**从不先构造一个它为真的样本**；
    那样的话，探测器坏了、键压根不存在时它也会绿（形状 16）。
    ⇒ 这里证明**帧一被登记，这三个键就真的在**。
    """
    write_snapshot(tmp_path, "2026-10-15", _raw("2026-10-15"),
                   dup_dropped=0, source_dup={})
    e = read_manifest(tmp_path)[-1]
    for k in (DUP_FIELD, SRC_CODES_FIELD, SRC_ROWS_FIELD):
        assert k in e, (
            f"正控失败：明明声明了计数，`{k}` 仍不在 manifest 里 ⇒ "
            f"上一条的失败消息在说谎，而探测器本身认不出东西。")


# ── ② 「没声明」必须与「声明了是 0」可区分 ───────────────────────

def test_an_undeclared_count_leaves_the_key_absent_rather_than_writing_zero(tmp_path):
    """没传 ⇒ **键缺席**，不是 `0`。

    ⇒ 失效方向：写 `0` ⇒ 历史条目（2026-10-14/15）与今天的条目**逐位同形**
    ⇒ 「这个字段是从哪天开始可信的」这件事**永久丢失**，而没人会喊
    ⇒ 形状 6 的镜像：**一个恒为 0 的字段比没有字段更坏**，
    因为它看起来是**一个每天都在发生的观测**。
    """
    write_snapshot(tmp_path, "2026-10-15", _raw("2026-10-15"))
    e = read_manifest(tmp_path)[-1]
    assert DUP_FIELD not in e, (
        f"未声明计数时 manifest 写了 `{DUP_FIELD}`={e[DUP_FIELD]!r}。\n"
        f"  ⇒ 「没量过」与「量过、确实没有」被合并成同一个 0，"
        f"而这个字段是**历史条目永久没有**的 —— "
        f"合并之后它只在为真的那天有意义，且失效时**无任何信号**。")
    assert SRC_CODES_FIELD not in e, (
        f"未声明时 manifest 写了 `{SRC_CODES_FIELD}`={e[SRC_CODES_FIELD]!r}"
        f"（同上：缺席 ≠ 0）。")


# ── ③ 接线层：采集器必须真传，且不许传字面量 ──────────────────────

def _calls(tree: ast.Module, name: str) -> list[ast.Call]:
    out = [n for n in ast.walk(tree)
           if isinstance(n, ast.Call) and (
               (isinstance(n.func, ast.Name) and n.func.id == name)
               or (isinstance(n.func, ast.Attribute) and n.func.attr == name))]
    return sorted(out, key=lambda c: c.lineno)


def test_the_collector_hands_the_counts_it_measured_to_the_writer(tmp_path):
    """采集器那个 `write_snapshot(...)` 必须带 `dup_dropped=` 与 `source_dup=`。

    🚨 **不许传字面量**：传 `0` 是最容易写出来的「正确实现」，
    它让守卫全绿、print 照常、而账本**永远记 0** —— 那是本文件要拦的东西本身。
    """
    tree = ast.parse(COLLECTOR.read_text(encoding="utf-8"), filename=str(COLLECTOR))
    calls = _calls(tree, "write_snapshot")
    assert calls, (
        f"正控失败：{COLLECTOR.name} 里认不出 `write_snapshot()` 调用点 ⇒ "
        f"本文件的接线判据整个落空（不是代码好了，是探测器坏了）。")
    for c in calls:
        kw = {k.arg: k.value for k in c.keywords}
        missing = [n for n in ("dup_dropped", "source_dup") if n not in kw]
        assert not missing, (
            f"`{COLLECTOR.name}` 第 {c.lineno} 行的 `write_snapshot()` "
            f"没传 {missing} ⇒ 那两个数只活在 stdout，"
            f"`data/vintage.log` 不是账本（不进 git、会被轮转、没人对账）。")
        for name, val in kw.items():
            assert not isinstance(val, ast.Constant), (
                f"第 {c.lineno} 行把 `{name}=` 写成了字面量 "
                f"{ast.unparse(val)} ⇒ 账本每天记同一个常量，"
                f"而真实的丢弃行数只 print 在 stdout。"
                f"\n  ⇒ 实参必须来自 `collect()` 的返回值（`res[...]`）。")


def test_the_call_site_probe_can_recognize_a_synthetic_collector():
    """**正控**：③ 的探测器必须认得出人写的接线（否则它是装饰）。"""
    src = ("def main():\n"
           "    snap = write_snapshot(args.root, day, df,\n"
           "                       dup_dropped=res['dup_dropped'],\n"
           "                       source_dup=res['source_dup'])\n")
    tree = ast.parse(src)
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "write_snapshot"]
    assert calls, "正控失败：合成的正确接线都认不出"
    kw = {k.arg: k.value for k in calls[0].keywords}
    assert {"dup_dropped", "source_dup"} <= set(kw), (
        f"探测器把关键字名认错了：拿到 {sorted(kw)}")
    assert not any(isinstance(v, ast.Constant) for v in kw.values()), (
        "正控失败：把 `res[...]` 误判成字面量 ⇒ ③ 会假红")


# ── ④ 源站重复 ⇒ 一份快照都不许登记 ───────────────────────────────

def test_the_collector_still_refuses_to_register_a_snapshot_with_source_dups():
    """`source_dup` 非空 ⇒ `main()` 必须**拒绝登记**并返回非零。

    为什么这条不是重复劳动：本文件**正在把 `source_dup` 写进账本**，
    而「不登记」这个保证目前**只由一段 `print` + `return 2` 撑着**。
    ⇒ 一旦它被摘掉，账本会**乐意**记下「这份快照源站重复了 N 行」，
    而读账本的人**看不出**这是被拒的样本还是被收的样本。
    ⇒ 形状 6「登记 ≠ 真的登记过」在账本层的实例。
    """
    src = COLLECTOR.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(COLLECTOR))
    main = next((n for n in tree.body
                 if isinstance(n, ast.FunctionDef) and n.name == "main"), None)
    assert main is not None, "正控失败：认不出 `main()`"

    guard = None
    for n in main.body:
        if not isinstance(n, ast.If):
            continue
        test = ast.unparse(n.test)
        if "source_dup" in test:
            guard = n
            break
    assert guard is not None, (
        f"`{COLLECTOR.name}::main()` 里没有一条以 `source_dup` 为条件的分支。\n"
        f"  ⇒ 源站对同一报告期返回多行时，这份快照会被登记成伪证据，"
        f"而**没有任何东西会喊**。\n"
        f"  ⚠️ 别拿石川 §6 当这条的依据：`data/books/cn_factor_lines.txt:5023-5034`"
        f" 逐字讲的是「Wind/Tushare 的**三大表**」、例子是**大智慧 601519.SH 2014 年"
        f"合并资产负债表**的存储层四类记录**，"
        f"**与本采集器取的东财财务指标表不是同一张表**。"
        f"  它只能支撑「多行不是天然异常」。"
        f"  ⇒ 而本模块实测结论**相反**：当前源每个报告期只存一行、没有版本维度，"
        f"这道闸今天守的是**潜伏**缺陷 —— 守它的理由是**潜伏不等于不存在**，"
        f"不是「它正在发生」。")

    body = ast.unparse(guard)
    assert "return 2" in body or "return 1" in body, (
        f"第 {guard.lineno} 行的 `source_dup` 分支**不返回非零**：\n"
        f"{body[:200]}\n"
        f"  ⇒ 只 `print` 不拦住，等于没拦。")


# ── ⑤ 读侧不许把缺档回落成 0 ──────────────────────────────────────

def test_no_reader_defaults_a_missing_dup_count_to_zero():
    """全仓不许出现 `.get("n_dup_dropped", 0)` 这类**缺档回落**。

    CLAUDE.md 第一条纪律：「缺档必须**报错**，绝不回落」。
    ⇒ `.get(key, 0)` 是那条纪律在**读侧**的镜像 ——
    它让「那天没量过」变成「那天量过是 0」，而这条守卫存在的**全部理由**
    就是让这两件事可区分。
    """
    hits = []
    for base in ("aplus", "scripts"):
        for p in sorted((ROOT / base).rglob("*.py")):
            if "__pycache__" in p.parts:
                continue
            tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
            for n in ast.walk(tree):
                if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                        and n.func.attr == "get" and len(n.args) == 2):
                    continue
                key = n.args[0]
                if isinstance(key, ast.Constant) and key.value == DUP_FIELD:
                    hits.append(f"{p.relative_to(ROOT)}:{n.lineno}")
    assert not hits, (
        f"这些地方把 `{DUP_FIELD}` 缺档回落成默认值：{', '.join(hits)}\n"
        f"  ⇒ 「没量过」与「量过是 0」被合并 ⇒ 本文件其余守卫全部白写。")


def test_the_missing_key_probe_can_recognize_a_fallback_on_synthetic_source():
    """**正控**：⑤ 的探测器必须认得出人写的回落，否则它是装饰。"""
    src = "e = entry.get('n_dup_dropped', 0)\n"
    tree = ast.parse(src)
    found = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "get" and len(n.args) == 2
             and isinstance(n.args[0], ast.Constant)
             and n.args[0].value == DUP_FIELD]
    assert found, "正控失败：合成的回落写法都认不出 ⇒ ⑤ 恒绿"

"""G3-bis 的毛超额**来源**只有一个，其余一律不许下判决（预注册 §4 G3-ter）。

## 为什么这条守卫必须存在

预注册 G3-bis 要算 `账户净值 = 基准 + 毛超额 − 摩擦`。
而仓里叫「毛超额」的数有**四个**，最大差 **3.1 倍**：

| 来源 | 毛超额/年 | 组合 | 基准 | `min_basket` |
|---|---|---|---|---|
| `factor_width_mde::widths["10"]` | **4.81%** | 等权 | 等权 | 3 |
| `cap_weighted_benchmark::arms.A` | 6.95% | 等权 | 等权 | **50** |
| `cap_weighted_benchmark::arms.B` | 14.99% | 等权 | **市值** | **50** |
| `cap_weighted_benchmark::arms.C` | **12.49%** | **市值** | **市值** | **50** |

⚠️ `cap_weighted_benchmark` 的判决臂是 **C**，不是 B，
而 C 的**组合是市值加权的** ⇒ 它压根不是「本策略那个等权 N=10 账户」。

⇒ **失效方向是「虚假翻面」**：12.49% 或 14.99% 配上一个正基准，
净值极易越过 20% ⇒ 就会得出 **P 档（结论翻面）**，
而那个结论建立在**另一个账户**上（第 119 层：被诊断的对象换了）。

## 为什么钉「接线」而不是「产物快照」

实测 `grep -rn cap_weighted_benchmark scripts/ aplus/ tests/`：
**零个读取者** —— 只有自引、注释、docstring 与它自己的测试。

⇒ **那三条臂不可能被任何代码路径消费，只可能被人手抄进算式。**
代码路径本来就会给出正确的那条（`width_capital_feasibility.py:92`
的 `DEFAULT_ARTIFACT` 指着 `factor_width_mde.json`）。

⇒ 所以主守卫钉**接线与产物字段**，而不是「文档里写了什么」
（第 118 层：读落盘 json 的守卫与代码毫无关系）。

## ★ 第一版探针自己在说谎（第 14 层的又一入口）

第一版的「零读取者」探针是「**先删掉 `#` 开头的行，再给剩下的行编号**」。
于是它报出的行号是**滤后行号**，不是源文件行号 ——
`scripts/market_nav_caliber.py:39` 被点名，而那一行在源文件里是**空行**
（它其实命中的是该文件 docstring 第 39 行）。

⇒ **确认机制报了一个不存在的位置**，而这正是第 14 层「假确认」的形态：
绿灯/红点看起来正常，指的位置是编的。
⇒ 修法：改用 **AST 定位**（docstring 节点可判定，行号由节点给出），
不用「删行再编号」这种会漂移的过滤。

## ★ 顺带挖出一条真债：判决产物**不自报口径**

第一版假设 `widths["10"]` 与 `arms.A` 只差 `min_basket` 一项，**当场红**：
`factor_width_mde.json::widths[*]` **没有** `portfolio_weighting` /
`benchmark_weighting` 字段。

而 `width_capital_feasibility.py`（它唯一的消费者）**也不补**这两个标签
（实测 `grep -n '等权\\|equal\\|portfolio'` 零命中）。

⇒ **预注册 G2 自己写着「口径标签是必填」，而它发生在自己的产物上。**
⇒ 处置：不假装它合规，也不靠放宽守卫蒙过去 ——
本文件把它**登记成债并双向钉住**（谁补上标签、谁删掉登记，都会红）。
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WIDTH_REPORT = ROOT / "data" / "reports" / "factor_width_mde.json"
CAP_REPORT = ROOT / "data" / "reports" / "cap_weighted_benchmark.json"
FEASIBILITY = ROOT / "scripts" / "width_capital_feasibility.py"
PREREG = ROOT / "docs" / "TODO_nav_vs_excess_caliber_2026-10-15.md"

#: 被排除的那份产物的**文件名**（不带路径）——
#: 判据只看文件名，因为仓里同时存在 `cap_weighted_benchmark.json`
#: 与 `scripts/cap_weighted_benchmark.py`，而只有前者是「产物」。
_REPORT_NAME = "cap_weighted_benchmark.json"

#: 判决账户的口径 —— **产物没有自报这两项**（见模块 docstring 的债条），
#: 值来自 `factor_width_mde.py` 调 `run_year(..., n, n, k)`（买卖同额 ⇒ 等权）
#: 与 `_market_by_day` 的等权基准。
#: ⚠️ 这是**本文件唯一的外部假设**，因此 ① 组给了它一条正反控。
VERDICT_CALIBER = {"portfolio_weighting": "equal", "benchmark_weighting": "equal"}


# ── 判据的唯一实现 ────────────────────────────────────────────────────
# ⚠️ 主守卫与正控共用它 —— 否则变异探针会持有自己的副本（第 102 层）。
#   它返回**不同项的清单**而不是 bool：空清单恒为假，而条目本身可读。

def _mismatched_calibers(arm: dict, min_basket: int) -> list[str]:
    """`arm` 与判决账户（等权组合 / 等权基准 / 该 `min_basket`）差在哪几项。"""
    out = []
    for k, want in VERDICT_CALIBER.items():
        if arm.get(k) != want:
            out.append(f"{k}={arm.get(k)!r}（判决账户 {want!r}）")
    if arm.get("min_basket") != min_basket:
        out.append(f"min_basket={arm.get('min_basket')!r}（判决账户 {min_basket!r}）")
    return out


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """模块 / 类 / 函数的 docstring 节点 id —— 它们**不是**代码读取者。"""
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and \
                    isinstance(body[0].value, ast.Constant) and \
                    isinstance(body[0].value.value, str):
                ids.add(id(body[0].value))
    return ids


def _parent_map(tree: ast.AST) -> dict[int, ast.AST]:
    out: dict[int, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            out[id(child)] = parent
    return out


def _is_path_read(node: ast.Constant, parent: ast.AST | None) -> bool:
    """这个字符串**出现在路径表达式里**吗？

    ⚠️ 第一版把「字符串出现在代码里」当成「文件被读取」，
    于是一口气点名四处**没有一个是读取**：
    `aplus/provenance.py` 的 `DEFAULT_GENERATORS` **字典键**（那是登记表）、
    `scripts/mutation_check.py` 的**变异规格元组元素**（那是待改写的文本）。

    ⇒ 判据必须锚在**语法位置**（第 13 / 119 层）：
    - `Path("x.json")` / `open("x.json")` ⇒ 常量是 **Call 的实参**
    - `Path(d) / "x.json"` ⇒ 常量是 **BinOp 的右操作数**（`/`）
    - `d["x.json"]` ⇒ 常量是 **Subscript 的切片**
    ⇒ Dict 键 / Tuple 元素 / List 元素**一律不算**。
    """
    if parent is None:
        return False
    if isinstance(parent, ast.Call):
        return True
    if isinstance(parent, ast.BinOp) and isinstance(parent.op, ast.Div):
        return True
    if isinstance(parent, ast.Subscript):
        return True
    return False


def _reader_lines(path: Path) -> list[str]:
    """返回该文件里**真的会打开** `cap_weighted_benchmark.json` 的行。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docs = _docstring_nodes(tree)
    parents = _parent_map(tree)
    try:
        rel = path.relative_to(ROOT)
    except ValueError:      # 夹具在仓外（tmp_path）—— 不该在这里抛
        rel = path
    out = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and node.value.endswith(_REPORT_NAME)
                and id(node) not in docs
                and _is_path_read(node, parents.get(id(node)))):
            out.append(f"{rel}:{node.lineno}")
    return out


def _module_level_value(path: Path, name: str):
    """读模块层的 `NAME = <字面量>` 或 `NAME = Path("<字面量>")`。

    ⚠️ 不许用 `ast.literal_eval` 直接 eval 整个 value ——
    `Path(...)` 是 Call 节点，会抛 `ValueError: malformed node or string`
    （第一版就这么红的，且红的原因看起来像「文件坏了」）。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:  # 只看模块层：函数内的同名局部变量不算
        targets = []
        if isinstance(node, ast.AnnAssign):
            targets = [node.target]
        elif isinstance(node, ast.Assign):
            targets = list(node.targets)
        if any(getattr(t, "id", None) == name for t in targets):
            val = node.value
            if isinstance(val, ast.Call) and getattr(val.func, "id", "") == "Path":
                return ast.literal_eval(val.args[0])
            return ast.literal_eval(val)
    return None


# ══ ① 能力守卫：判据必须真能判「同」也能判「异」 ═══════════════════
# 🚨 第 130 层：探测器变宽/变窄时成对守卫会恒真，而外观完全正常。

def test_the_caliber_criterion_accepts_a_verbatim_copy_of_the_verdict_account():
    """正控：一个**确实同一个账户**的臂 ⇒ 差异清单必须为空。

    🚨 第 131 层：只验探测器不验判决 ⇒ 会得到「探针有牙」的假信用。"""
    width = json.loads(WIDTH_REPORT.read_text(encoding="utf-8"))
    mb = width["widths"]["10"]["min_basket"]
    twin = {"portfolio_weighting": "equal", "benchmark_weighting": "equal",
            "min_basket": mb}
    assert _mismatched_calibers(twin, mb) == []


def test_the_caliber_criterion_catches_each_caliber_independently():
    """分岔夹具：三项**各自**改一个都必须被抓到。

    ⚠️ 只测「全改」的话，三项里漏掉一项照样绿 —— 那正是恒真守卫的形态。"""
    mb = 3
    base = {"portfolio_weighting": "equal", "benchmark_weighting": "equal",
            "min_basket": mb}
    for key, bad in (("portfolio_weighting", "cap"),
                     ("benchmark_weighting", "cap"),
                     ("min_basket", 50)):
        arm = dict(base, **{key: bad})
        hits = _mismatched_calibers(arm, mb)
        assert len(hits) == 1 and key in hits[0], (key, hits)


def test_the_verdict_artifact_does_not_self_label_its_calibers_and_the_debt_is_registered():
    """★ 债条，**双向**钉：产物不自报口径，而预注册登记了这件事实。

    补上标签 ⇒ 本条红（要求删掉债登记）；
    删掉债登记 ⇒ 本条红（要求补上标签）。
    ⚠️ 不许把它改成「断言它有标签」 —— 那要一次重跑，而产物在判决链上（第 125 层）。"""
    width = json.loads(WIDTH_REPORT.read_text(encoding="utf-8"))
    assert "portfolio_weighting" not in width["widths"]["10"], \
        "产物已补上口径标签 —— 请删掉预注册里的债登记，并让本文件只守接线"
    assert "不自报口径" in PREREG.read_text(encoding="utf-8")


# ══ ② 主守卫：三条臂没有一条是判决账户 ══════════════════════════════

@pytest.mark.parametrize("arm", ["A", "B", "C"])
def test_no_cap_weighted_benchmark_arm_is_the_verdict_account(arm):
    width = json.loads(WIDTH_REPORT.read_text(encoding="utf-8"))
    cap = json.loads(CAP_REPORT.read_text(encoding="utf-8"))
    mb = width["widths"]["10"]["min_basket"]
    hits = _mismatched_calibers(cap["arms"][arm], mb)
    assert hits, f"臂 {arm} 被当成了判决账户 —— 若口径确已对齐，须改写 G3-ter 并重判"


def test_the_verdict_arm_is_cap_weighted_and_thus_not_our_portfolio():
    """判决臂是 C，而 C 的组合是市值加权的 ⇒ 即便 `min_basket` 对齐也不匹配。

    ⇒ 独立于 ② 的第二层，防「只对齐了榜宽就以为对齐了」。"""
    cap = json.loads(CAP_REPORT.read_text(encoding="utf-8"))
    assert "C 臂" in cap["verdict_branch"], cap["verdict_branch"]
    assert cap["arms"]["C"]["portfolio_weighting"] == "cap"


def test_the_two_reports_do_disagree_by_more_than_a_rounding_difference():
    """脱钩正控：两份产物的毛超额确实差 3 倍以上。

    🚨 若哪天两者被统一口径重算，这个数会掉下来 ⇒ 本条红 ⇒
    那时 G3-ter 的整张表都该重写，而不是留着过期禁令。"""
    width = json.loads(WIDTH_REPORT.read_text(encoding="utf-8"))
    cap = json.loads(CAP_REPORT.read_text(encoding="utf-8"))
    a = width["widths"]["10"]["gross_annual_pct"]
    c = cap["arms"]["C"]["gross_annual_pct"]
    assert c / a > 2.0, (a, c)


# ══ ③ 接线守卫：判决脚本的输入必须指向 factor_width_mde ═════════════

def test_the_capital_feasibility_verdict_reads_the_factor_width_report():
    got = _module_level_value(FEASIBILITY, "DEFAULT_ARTIFACT")
    assert got is not None, "DEFAULT_ARTIFACT 不在模块层 —— 判决输入不再是可问的判据"
    assert Path(got).name == "factor_width_mde.json", got


def test_no_code_reads_the_cap_weighted_benchmark_report():
    """**零读取者**才是那条真正的保险：代码路径给得出正确的那条，手抄给不出。"""
    hits = []
    for path in sorted(ROOT.rglob("*.py")):
        rel = path.relative_to(ROOT)
        if "tests" in rel.parts or rel.as_posix().endswith(
                "scripts/cap_weighted_benchmark.py"):
            continue  # 测试与它自己不算「消费者」
        try:
            hits.extend(_reader_lines(path))
        except SyntaxError:
            continue
    assert hits == [], f"cap_weighted_benchmark.json 出现了代码读取者：{hits}"


def test_the_reader_detector_sees_a_real_read_but_ignores_a_registry_key(tmp_path):
    """★ 探测器自身的正反控（第 131 层：只验探测器 = 假信用）。

    反控必须含**第一版真实误报的那两类**：
    `DEFAULT_GENERATORS` 的字典键、`mutation_check` 的变异规格元组元素。
    ⚠️ 若探测器收窄到永远返回空，本守卫会**恒绿** ——
    而它恰恰是「零读取者」那条的全部依据。"""
    name = _REPORT_NAME
    real = tmp_path / "real.py"
    real.write_text(f"from pathlib import Path\np = Path('data/reports/{name}')\n",
                    encoding="utf-8")
    decoy = tmp_path / "decoy.py"
    decoy.write_text(
        f'REGS = {{"{name}": "scripts/cap_weighted_benchmark.py"}}\n'
        f'SPECS = [("cap_weighted", "scripts/cap_weighted_benchmark.py",\n'
        f'          "data/reports/{name}")]\n'
        f'"""docstring 里提到 {name}"""\n',
        encoding="utf-8")
    assert len(_reader_lines(real)) == 1
    assert _reader_lines(decoy) == []


# ══ ④ 预注册不许与产物脱钩 ══════════════════════════════════════════
# 形状 22 的散文版：G3-ter 那张表是**手抄**的，改产物不改文档就会静默腐烂。

def _prereg_quotes(value: float, doc: str) -> bool:
    """产物的一个数**是否已被预注册文档引用**。

    ⚠️ 两种合格的写法（**都要认**）：
      ① 两位小数的百分数：`4.67%` —— 人读的那个；
      ② **逐位**浮点：`0.04673319056259782` —— §7.2(a) 的对照表用的就是这个。

    🚨 第一版只认 ① ⇒ 一次重跑就把守卫判红，而文档**已经把新值逐位记下来了**
    （只是精度比守卫要求的更细）⇒ **那是「引用格式之争」，不是「表与产物脱钩」**。
    而这类红**有毒**：它的修法看起来是「把文档改回两位小数」，
    也就是**把更精确的证据删成更粗的**（第 112 层：过严的守卫和恒真的一样有害）。

    ⚠️ 反过来也不能放松成「文档里有任何相关数字就算」——
    那正是第 13 种形状（子串断言被注释满足）。
    ⇒ 两条都必须是**这个值的合法呈现**，不接受近似值。"""
    return f"{value * 100:.2f}%" in doc or repr(value) in doc


def test_the_prereg_table_quotes_the_artifact_numbers_verbatim():
    cap = json.loads(CAP_REPORT.read_text(encoding="utf-8"))
    width = json.loads(WIDTH_REPORT.read_text(encoding="utf-8"))
    doc = PREREG.read_text(encoding="utf-8")
    for name, v in (("factor_width_mde::widths[\"10\"]",
                     width["widths"]["10"]["gross_annual_pct"]),
                    ("cap_weighted_benchmark::arms.A",
                     cap["arms"]["A"]["gross_annual_pct"]),
                    ("cap_weighted_benchmark::arms.B",
                     cap["arms"]["B"]["gross_annual_pct"]),
                    ("cap_weighted_benchmark::arms.C",
                     cap["arms"]["C"]["gross_annual_pct"])):
        assert _prereg_quotes(v, doc), (
            f"预注册 G3-ter 里没有引用 {name} 的 {v!r}"
            f"（{v * 100:.2f}%）—— 表与产物脱钩")


def test_a_number_older_than_the_artifact_must_be_declared_retired_not_silently_kept():
    """★ 「引用了当前值」**不够**：还要保证**旧值不是被悄悄留在原地当现行值**。

    形状 22 的散文版：§7 正文按规矩不许改（预注册与补记必须分开留痕），
    而 §7.2(a) 的对照表把新旧两列都记下来了 ⇒ **旧值必然还出现在文档里**。

    ⇒ 所以「文档里有旧值」是**合规**的，「文档只说旧值」才是**腐烂**。
    ⇒ 判据：凡是文档里出现的、本仓引用过的**旧毛超额**，
       必须有一句**明写它已作废**的话 ⇒ 否则读者会把两处都当结论。

    ⚠️ 这条不许退化成「文档里有『作废』两个字」（第 13 种形状）：
    它必须点名**具体的数**。"""
    doc = PREREG.read_text(encoding="utf-8")
    #: §7 写下时的值。陈旧性守卫逼着重跑后它已经不准了（§7.2(a) 记录了这件事）。
    #: **不手打别的档** —— 这一个数就是 §7 G3-ter 唯一许可的那一档。
    retired = "4.8108%"
    assert retired in doc, "前提失效：§7 里的旧值不在文档里了，先查是不是被改过"
    retirement_lines = [ln for ln in doc.splitlines()
                        if retired in ln and ("作废" in ln or "以本表为准" in ln
                                              or "重跑后" in ln)]
    assert retirement_lines, (
        f"文档里还留着旧值 {retired}，却**没有一句明写它已作废** ⇒ "
        "读者会把 §7 正文和 §7.2 的对照表当成两个并列结论。\n"
        "修法：在旧值所在处加作废标注（形状 22：补写出来的标注不能冒充修正，"
        "但**不改数**是对的，改数才是伪造预注册）。")


def test_the_prereg_still_names_the_only_admissible_source():
    doc = PREREG.read_text(encoding="utf-8")
    assert "只许用 `factor_width_mde.json::widths[\"10\"]`" in doc
    assert "零个读取者" in doc   # 禁令的理由不许被悄悄删掉

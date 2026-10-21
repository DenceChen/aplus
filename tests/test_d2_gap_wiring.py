"""D2 台账**前端接线**：从端点到袋子到判决，一个字都不能靠注释满足。

## 这个文件存在的理由

判决卡已经接到屏上了（`index.html::renderDocket` 第 6 参 `d2gap`），
而「接上了」这件事**没有任何东西在守**。它一旦静默失效，
屏上不会崩、不会报错、不会少任何一行 —— 判决卡只是**永远不出现**
（上一轮的死因是「传了 predictions 没有 headline」：
`d2LedgerVerdict` 走 `!ceil` 返 `null`，而 `null` 与「今天没缺口」**逐位同形**）。

⚠️ **同一个死因可以由四个不同环节产生**，症状完全一样：
① 端点没取 ② 取了没传 ③ 传了但合并顺序反 ④ 传了但投影喂不饱判决。
⇒ 下面四段各钉一个环节，**不许合并成一条**（合并后任一环节坏了其余照绿）。

## 为什么必须剥注释（第 13 层）

`index.html` 有近 80 行中文注释，其中就写着 `jget('/api/d2-gap')`
这个契约说明。子串断言 `'/api/d2-gap' in html` 会被**注释本身**满足
⇒ 守卫与注释作者是同一个人、且在同一次编辑里（第 13 层自己的升级注记）。
⇒ 这里**先剥注释再搜**，从结构上消灭这一整类，
而不是逐条去核对「这个串是不是只出现在注释里」。

## 为什么不 `require` 整个 index.html

那要 mock 一整套 DOM，于是测的是**我写的 mock**，不是上线的那行字。
⇒ 这里把**生产源码里那行合并表达式的实参原文**抽出来，配最小变量在 node 里真跑。

## 剥注释器的已知边界（不是免责声明，是待办）

它处理 `'` `"` `` ` `` + 转义 + `//` + `/* */`，**不识别 regex 字面量**。
`index.html` 现有 3 个 regex 字面量（`/[&<>"']/g` 与两个 `/#\\/(\\w+)/`）
实测都不含 `//` / `/*` / `*/` ⇒ 本文件安全。
哪天有人写 `!/\\/\\//.test(s)`，剥注释会吃掉后面的代码
⇒ 所以上面那条**正控**是必需品，不是装饰。
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
HTML = ROOT / "aplus" / "web" / "index.html"
VERDICT_JS = ROOT / "aplus" / "web" / "verdict.js"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="需要 node 才能测前端纯逻辑")


# ---------- 剥注释器（模块级：判决逻辑必须能被 import，第 96 层） ----------

def strip_js_comments(src: str) -> str:
    """剥掉 JS 注释，**保持行号不变**（块注释用等量 `\\n` 占位）。

    为什么必须保持行号：`body_of()` 靠 `^}$`（列 0）找函数收尾，
    行号一漂，`renderDocket` 的体就会被截在某个注释里或整个截空。
    """
    out = []
    i, n, quote = 0, len(src), None
    while i < n:
        ch = src[i]
        if quote is None:
            if ch in "'\"`":
                quote = ch
                out.append(ch)
                i += 1
            elif ch == "/" and i + 1 < n and src[i + 1] == "/":
                j = src.find("\n", i)
                j = n if j < 0 else j
                out.append(" " * (j - i))
                i = j
            elif ch == "/" and i + 1 < n and src[i + 1] == "*":
                j = src.find("*/", i + 2)
                j = n if j < 0 else j + 2
                out.append(re.sub(r"[^\n]", " ", src[i:j]))
                i = j
            else:
                out.append(ch)
                i += 1
        else:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(src[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
    return "".join(out)


def _clean_html() -> str:
    return strip_js_comments(HTML.read_text(encoding="utf-8"))


def body_of(clean_src: str, sig: str) -> str:
    """按**真函数体**取源码：从 `sig` 那行到下一个顶格 `}`。

    不用括号配对扫描器：那要正确处理模板串/正则/注释。
    本文件三个目标函数的函数头与收尾都在列 0，且这条假设**逐条验过**
    （下面 `test_the_bodies_we_search_in_are_not_empty` 是它的正控）。
    """
    m = re.search(rf"^{re.escape(sig)}.+$", clean_src, re.M)
    assert m, f"找不到函数头 {sig!r} —— 守卫打空了（不是生产有问题，是探针的前提错了）"
    rest = clean_src[m.end():]
    end = re.search(r"^\}$", rest, re.M)
    assert end, f"找不到 {sig!r} 的收尾（列 0 的 }}）"
    return rest[:end.start()]


def _js_urls(body: str) -> list:
    """收 `jget(...)` 的**实参**（第 13 层：锚不许是「这个串出现过」）。"""
    return re.findall(r"""jget\(\s*['"]([^'"]+)['"]""", body)


# ⚠️ `[^;{]*` 而不是 `[^;]*`：`[^;]*` 贪婪且跨行，会从**函数定义**那行一路
# 吞到第一个 `;`，把定义当成第三个调用点（上一版探针实测 count = 3）。
# `{` 在结构上挡住定义（定义行是 `...) {`，后面永远不会紧跟 `);`）。
DOCKET_CALL = re.compile(r"renderDocket\(([^;{]*)\);")
BAG_LINE = re.compile(r"const docketScreen = Object\.assign\((.*?)\);", re.S)

# 反控：把 `Object.assign` 的两个源对调（生产注释自己写明「后者赢」是承重的）
REVERSED_ARGS = "{}, d2gap?.ledger?.verdict_input, predictions"


# ---------- node 夹具 ----------

def _payload() -> dict:
    """经生产模块读出来的整份 `/api/d2-gap` 载荷（不手写夹具）。"""
    from aplus.reports import read_d2_gap
    return read_d2_gap(ROOT / "data" / "reports")


def _raw_ledger() -> dict:
    return json.loads(
        (ROOT / "data" / "reports" / "d2_signal_requirement.json")
        .read_text(encoding="utf-8"))


def _run_node(script: str) -> dict:
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert out.returncode == 0, f"node 抛异常了 rc={out.returncode}: {out.stderr}"
    return json.loads(out.stdout)


# 预测面替身：只带判决**不读**的那些键，外加**刻意**同名的 `rows`
# ⇒ 合并顺序写反时它会顶掉台账的五档宽度行（第 182 层：混合读会印错数）。
PREDICTIONS = json.dumps({"date": "2026-10-16", "generated_at": "x",
                          "weights_check": {"status": "match"},
                          "rows": [1, 2, 3]})


def _bag_outcome(assign_args: str, d2gap_expr: str = None) -> dict:
    """在 node 里照抄生产那一行，量袋子里**实际**是什么。"""
    gap = "null" if d2gap_expr is None else d2gap_expr
    script = f"""
const {{d2LedgerVerdict}} = require({json.dumps(str(VERDICT_JS))});
const predictions = {PREDICTIONS};
const d2gap = {gap};
const docketScreen = Object.assign({assign_args});
const rows = Array.isArray(docketScreen.rows) ? docketScreen.rows : [];
console.log(JSON.stringify({{
  has_headline: !!docketScreen.headline,
  has_ceil: !!(docketScreen.headline &&
                docketScreen.headline.account_annual_ceiling_pct),
  rows_len: rows.length,
  rows_are_widths: rows.length > 0 &&
      rows.every(r => r && typeof r.measured_gross_over_mde_abs === 'number'),
  weights_check_kept: docketScreen.weights_check === undefined
      ? null : docketScreen.weights_check,
  verdict: d2LedgerVerdict(docketScreen),
}}));
"""
    return _run_node(script)


def _gap_with_ledger() -> str:
    """⚠️ 生产返回面比 `read_d2_gap` **多一层** `ledger` ⇒ 这里照抄那个形状。"""
    return "{ledger: " + json.dumps(_payload()) + "}"


# ---------- ① 剥注释器的正控（缺了它，下游全绿都不可信） ----------

def test_a_url_that_exists_only_in_a_comment_is_not_found():
    """第 13 层正控：把端点塞进注释，搜必须数不到。"""
    src = ("const a = 1;\n"
           "// jget('/api/d2-gap')  ← 契约说明\n"
           "/* jget('/api/other') 多行注释\n"
           "   跨两行 */\n"
           "const b = 2;\n")
    assert _js_urls(strip_js_comments(src)) == []
    assert "d2-gap" in src, "正控失效：连注释本身都没被搜到 ⇒ 这条测的不是剥注释器"


def test_the_stripper_keeps_line_numbers_aligned():
    """块注释必须用等量换行占位，否则 `^}$` 找函数收尾会错位。"""
    src = "a\n/* 1\n2\n3 */\n}\n"
    cleaned = strip_js_comments(src)
    assert cleaned.count("\n") == src.count("\n")
    assert re.search(r"^\}$", cleaned, re.M), "顶格 } 找不到 ⇒ body_of 会判「函数不存在」"


def test_the_bodies_we_search_in_are_not_empty():
    """正控（第 120 层）：夹具必须能区分「函数不存在」与「体是空的」。"""
    body = body_of(_clean_html(), "function renderDocket(stats, evo, weights,"
                                  " predictions, gate, d2gap)")
    assert len(body) > 2000, f"renderDocket 体只有 {len(body)} 字符 ⇒ 后面全是空跑"
    assert "docketScreen" in body, "renderDocket 里没有袋子构造 ⇒ 守卫打在了别的函数上"


# ---------- ② 环节一：两个 loader 都真的取了端点 ----------

@pytest.mark.parametrize("sig", [
    "async function loadChrome()",
    "async function loadOverview()",
])
def test_both_loaders_fetch_the_d2_gap_endpoint(sig):
    """深链进概览时不取端点 ⇒ 台账警示缺席，而屏幕上什么都不报错。"""
    urls = _js_urls(body_of(_clean_html(), sig))
    assert "/api/d2-gap" in urls, f"{sig!r} 的 jget 实参 = {urls}"


# ---------- ③ 环节二：两处调用都把第 6 参递出去 ----------

def test_render_docket_is_called_from_two_places_and_both_pass_the_ledger():
    """`renderDocket` **无条件重写**警示条 ⇒ 漏传一处，那一处会把另一处
    已渲染的台账警示**抹掉**（现象：深链进概览时警示消失，切回别的 tab 又出现）。"""
    calls = DOCKET_CALL.findall(_clean_html())
    assert len(calls) == 2, (
        f"调用处数 = {len(calls)}，与预期 2 不符。若数到 3，那一处理解成"
        f"「函数定义」而不是真调用 ⇒ 正则该用 `[^;{{]*`")
    for c in calls:
        args = [a.strip() for a in c.split(",")]
        assert len(args) == 6, f"实参数 = {len(args)}，第 6 个漏了：{c!r}"
        assert args[5] == "d2gap", f"第 6 个实参不是 d2gap：{args[5]!r}"


def test_render_docket_declares_the_sixth_parameter():
    """「传了但没人接」比漏传更难查 —— 签名必须接第 6 参。"""
    clean = _clean_html()
    m = re.search(r"^function renderDocket\((.*)\)\s*\{", clean, re.M)
    assert m, "找不到 renderDocket 的定义行"
    params = [p.strip() for p in m.group(1).split(",")]
    assert params == ["stats", "evo", "weights", "predictions", "gate", "d2gap"], \
        f"形参 = {params}"


# ---------- ④ 环节三：袋子构造与合并顺序 ----------

def test_the_bag_reads_the_verdict_input_projection():
    """实参必须读 `verdict_input`，不是整个 `ledger` 也不是顶层。"""
    body = body_of(_clean_html(), "function renderDocket(stats, evo, weights,"
                                  " predictions, gate, d2gap)")
    m = BAG_LINE.search(body)
    assert m, "抽不到生产那行合并表达式 ⇒ 守卫没在打调用点"
    args = " ".join(m.group(1).split())
    assert "verdict_input" in args, f"实参没读 verdict_input：{args!r}"


def test_the_ledger_is_merged_after_the_predictions():
    """`Object.assign` 是**后者赢**，而 `rows` 在两个面上同名
    ⇒ 顺序写反时预测面的 `rows` 会顶掉台账的五档宽度行。顺序是承重的。"""
    body = body_of(_clean_html(), "function renderDocket(stats, evo, weights,"
                                  " predictions, gate, d2gap)")
    args = " ".join(BAG_LINE.search(body).group(1).split())
    assert args != REVERSED_ARGS, "生产的合并顺序已经是对调的那一支"
    assert args.rindex("verdict_input") > args.rindex("predictions"), (
        f"台账面不在预测面之后 ⇒ 它赢不了：{args!r}")


def test_the_production_merge_line_actually_builds_a_usable_bag():
    """把**生产那一行的实参原文**拿进 node 真跑，不 require 整个 html。"""
    body = body_of(_clean_html(), "function renderDocket(stats, evo, weights,"
                                  " predictions, gate, d2gap)")
    args = " ".join(BAG_LINE.search(body).group(1).split())
    got = _bag_outcome(args, _gap_with_ledger())

    assert got["has_headline"], "闸在生产里仍然是死的（袋子里没有 headline）"
    assert got["has_ceil"], "headline 在但缺 account_annual_ceiling_pct ⇒ 判决返 null"
    assert got["verdict"] is not None, "袋子喂不饱判决 ⇒ 屏上永远没有台账警示"
    # 🚨 顺序地雷：袋子里的 rows 必须是五档宽度行，不是 predictions 的 [1,2,3]
    assert got["rows_len"] == 5 and got["rows_are_widths"], (
        f"袋子的 rows 是 {got['rows_len']} 条 / 是五档={got['rows_are_widths']}"
        f" ⇒ 台账面被 predictions 顶掉了")
    # 预测面的键必须还在（袋子的另一支靠它）
    assert got["weights_check_kept"] == {"status": "match"}


def test_the_bag_verdict_is_word_for_word_the_projection_only_verdict():
    """袋子与「只喂投影」的判决必须逐字相同 ⇒ 预测面专属的形状支没混进来。"""
    body = body_of(_clean_html(), "function renderDocket(stats, evo, weights,"
                                  " predictions, gate, d2gap)")
    args = " ".join(BAG_LINE.search(body).group(1).split())
    script = f"""
const {{d2LedgerVerdict}} = require({json.dumps(str(VERDICT_JS))});
const predictions = {PREDICTIONS};
const d2gap = {_gap_with_ledger()};
const docketScreen = Object.assign({args});
console.log(JSON.stringify({{
  bag: d2LedgerVerdict(docketScreen),
  projection_only: d2LedgerVerdict(d2gap.ledger.verdict_input),
}}));
"""
    got = _run_node(script)
    assert got["bag"] is not None, "投影喂不饱判决 ⇒ 闸在生产里仍然是死的"
    assert got["bag"] == got["projection_only"], (
        "袋子里混进了 predictions 专属的形状支 ⇒ 判决被改")


def test_reversing_the_merge_order_is_detectable():
    """反控：证明上面那条断言**有判别力** —— 顺序一换，量就变。

    ⚠️ 只断言「变红」不够，必须给出**为什么它会红**：顺序写反时
    `d2LedgerVerdict` 里的 `.filter(v => typeof v === 'number')` 会把
    predictions 的 `[1,2,3]` 滤成**空数组** ⇒ MDE 那半句被**整段跳过**，
    判决**仍然非 null**、**不抛** ⇒ 只有量「袋子的 rows 是不是五档宽度行」
    才抓得到。（生产注释写的是「NaN 形状」，那与代码实际行为不一致，
    这就是第 22 层的第 N 次：注释里的**行为预测**也从不校验。）
    """
    assert REVERSED_ARGS != " ".join(
        BAG_LINE.search(body_of(_clean_html(),
                                "function renderDocket(stats, evo, weights,"
                                " predictions, gate, d2gap)")).group(1).split())
    good = _bag_outcome("{}, predictions, d2gap?.ledger?.verdict_input",
                        _gap_with_ledger())
    bad = _bag_outcome(REVERSED_ARGS, _gap_with_ledger())
    assert good["rows_len"] == 5 and good["rows_are_widths"]
    assert not bad["rows_are_widths"], "反控没生效 ⇒ 这条断言可能恒真"
    assert bad["verdict"] is not None, (
        "反控下的判决变了 ⇒ 上面那句「整段跳过而不崩」的记录要重新取事实")


# ---------- ⑤ 环节四：投影喂得饱判决，且缺档时闭嘴 ----------

@pytest.mark.parametrize("bad,label", [
    (None, "端点 404 ⇒ jget 的 .catch(() => null)"),
    ("{}", "响应面缺 ledger 键"),
    ('{"ledger": {"status": "missing"}}', "缺 verdict_input 键"),
])
def test_every_missing_shape_silences_the_ledger_branch(bad, label):
    """缺档 ⇒ 判决 `null`，**且 rc == 0**（不抛、不假装有台账）。

    「拒绝出判决」与「判决为空」必须分开（第 89 层）：
    `null` 让 `verdict.js:198` 的 `!ceil` 守卫闭嘴，这是**正确的降级**。
    """
    got = _bag_outcome("{}, predictions, d2gap?.ledger?.verdict_input", bad)
    assert got["verdict"] is None, f"{label} 时闸没有闭嘴，而是给了别的读数"
    assert not got["has_headline"], f"{label} 时袋子里却出现了 headline"


def test_the_projection_headline_is_the_same_object_as_the_api_ceiling():
    """按引用 ⇒ 结构上不可能漂移（第 109 层：核对只读字段，不许重算）。

    ⚠️ 这里用 `is` 而不是 `==`：同一进程里两个读法命中的是**同一个 float 对象**。
    若哪天有人为了「保险」改成 `float(x)` 复制一份，`==` 照样全绿、
    而「按引用」这条保证已经没了 ⇒ 只有身份能钉住它。
    """
    from aplus.reports import read_d2_gap
    p = read_d2_gap(ROOT / "data" / "reports")
    assert p["status"] == "ok", f"真产物读不出来（status={p['status']}）"
    vi = p["verdict_input"]
    ceil = vi["headline"]["account_annual_ceiling_pct"]
    pairs = [
        ("account_annual_at_50k_pct", p["account_annual_at_50k_pct"],
         ceil["at_deliverable_width_and_50k"]),
        ("gap_to_target_pp", p["gap_to_target_pp"],
         ceil["gap_to_target_at_deliverable_width_and_50k_pp"]),
        ("account_annual_at_infinite_capital_pct",
         p["account_annual_at_infinite_capital_pct"], ceil["at_infinite_capital"]),
    ]
    for name, api_side, verdict_side in pairs:
        assert api_side is verdict_side, (
            f"{name}：API 面与判决面不是同一个对象 ⇒ 值可能漂移")


def test_the_projection_carries_no_fraction_array_back():
    """🚨 分数组不许借这一步回到 API 面（第 182 层）。

    产物里 `rows[*].measured_gross_annual = 0.0481`（**分数**）而
    `headline.required_gross_at_50k_deliverable = 15.5571`（**百分数**）——
    同量差 100 倍，消费侧混读会把「基准年化 +494%」印出来。
    ⚠️ 判法是**键集合**而不是「值小于 1」：`measured_gross_over_mde_abs`
    本来就是比值（0.31），按量判会把它误当成分数。
    """
    from aplus.reports import read_d2_gap
    raw = _raw_ledger()
    vi = read_d2_gap(ROOT / "data" / "reports")["verdict_input"]
    raw_row = raw["rows"][0]
    assert abs(raw_row["measured_gross_annual"]) < 0.1, (
        "产物里的 measured_gross_annual 不是分数 ⇒ 本守卫的 100 倍论证失效")
    assert abs(raw["headline"]["required_gross_at_50k_deliverable"]) > 1.0, (
        "产物里的 required_gross_at_50k_deliverable 不是百分数 ⇒ 同上")
    for r in vi["rows"]:
        assert set(r) == {"n", "measured_gross_over_mde_abs"}, \
            f"rows 的键多了：{sorted(r)} ⇒ 有分数组回到了 API 面"


def test_a_missing_or_incomplete_ledger_yields_no_projection():
    """缺档 / join 不完整 ⇒ `verdict_input is None`，**不是空壳**。

    空壳会让「没有台账」与「缺口非正」混同（判决两处都返 `null`，
    而 `read_d2_gap` 的 docstring 明写「绝不回落成 0」）。
    """
    from aplus.reports import read_d2_gap
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        assert read_d2_gap(Path(td))["verdict_input"] is None

        broken = _raw_ledger()
        broken["rows"] = [r for r in broken["rows"] if r["n"] != 30]
        (Path(td) / "d2_signal_requirement.json").write_text(
            json.dumps(broken), encoding="utf-8")
        out = read_d2_gap(Path(td))
    assert out["status"] == "malformed", f"挖掉一档后 status = {out['status']}"
    assert out["verdict_input"] is None, "join 不完整却给了投影 ⇒ 判决会以为有台账"
    assert out["by_width"] == [], "join 不完整却返回了行 ⇒ 那是另一张表"


# ---------- ⑥ 环节五：交付口径的两个数**真的在屏上** ----------
#
# 🚨 ①②~⑤ 全部钉在**接线/形状**层（袋子里有没有、两处调用有没有传、
# 投影字段对不对）。它们全绿的那一轮，屏上**一个交付口径的字都没有**。
# 机制：`d2LedgerVerdict` 把内容劈成两半 —— 交付口径的天花板与缺口
# 在 `headline`，其余在 `detail`；而生产唯一命中的那一支（支 2，`weights.caveat`
# 有值）**保留自己的 headline、只搬 `ledger.detail`** ⇒ **`headline` 在生产
# 里没有通路**。
#
# ⇒ 所以这一节钉的不是「函数返回了什么」，是「**用户眼睛看到的那段文本**」。
#
# ⚠️ 必须读 `detailHtml ?? detail` 而不是 `detail`：`detailHtml` 存在时
# `detail` **永远不会被读**（渲染层 `index.html:1131`，已由
# `test_docket_warning.py` / `test_audit_frontend.py` 钉住）。我上一轮第一次
# 量这件事时查的就是 `detail` ⇒ 测的是**一个不渲染的字段**。
#
# ⚠️ 期望值**不许手打**（第 137 层：手抄的数会静默过期且没人喊）——
# 下面从产物自己算，产物重跑后守卫自动跟着走。

# 生产载荷：`/api/status` 的 `stats` + 生产实际的回落权重 `weights.caveat`
# （支 2 = 生产唯一命中的那一支，见预注册 §1.4）。
#
# `stats` 取预注册 §1.1 的**冻结读数**（只读字段，不重算 CAGR）。
# ⚠️ 它**只决定「走哪一支」**，不决定量到的东西：支 2 在所有 `cagr` 支之前
# 返回 ⇒ 把 `cagr` 写成任何值，量到的都是同一段文本。所以这四个数
# **不是判据**，把它们写死没有第 137 层那种漂移风险 ——
# 真要证明这一点，见下面那条 `level == 'danger'` 的断言。
_SCREEN_STATS = {
    "cagr": -0.7364854369679485, "n_days": 4,
    "total_return": -0.02094650393344788, "years": 0.015873015873015872,
}
_SCREEN_WEIGHTS = json.dumps({
    "caveat": "已回落默认权重，**未经赛马验证**。",
    "caveat_kind": "unraced",
})


def _deliverable_strings() -> tuple:
    """从产物算「交付口径」那两行的**屏上字符串**，不手打。"""
    raw = _raw_ledger()
    ceil = raw["headline"]["account_annual_ceiling_pct"]
    return (f"{ceil['at_deliverable_width_and_50k']:.2f}",
            f"{ceil['gap_to_target_at_deliverable_width_and_50k_pp']:.2f}")


def _screen_outcome(d2gap_expr: str) -> dict:
    """照抄生产那一支，在真实载荷上跑 `docketWarning`，量**屏上那段文本**。

    `d2gap_expr` 是袋子里那份台账的表达式；传 `"null"` 即「端点缺/投影缺」
    （预注册 §3.3 说这条守卫守不住浏览器那一半 —— 这里量的是**代码**那一半）。
    """
    script = f"""
const {{docketWarning}} = require({json.dumps(str(VERDICT_JS))});
const predictions = {PREDICTIONS};
const weights = {_SCREEN_WEIGHTS};
const stats = {json.dumps(_SCREEN_STATS)};
const d2gap = {d2gap_expr};
const docketScreen = Object.assign({{}}, predictions,
                                    d2gap?.ledger?.verdict_input);
const w = docketWarning(stats, null, weights, docketScreen);
console.log(JSON.stringify({{
  hit: !!w,
  level: w ? w.level : null,
  headline: w ? w.headline : null,
  // 🚨 渲染层真正读的那一个（index.html:1131）
  screen_text: w ? (w.detailHtml ?? w.detail ?? '') : '',
}}));
"""
    return _run_node(script)


def test_the_two_deliverable_numbers_reach_the_screen():
    """交付口径的「上限」与「缺口」**必须出现在屏上文本里**。

    ⚠️ **不写死那两个数**（K=63 口径是天花板 12.62%、缺口 7.38pp；
    K=21 时代是 8.34% / 11.66pp）⇒ 写死即化石 ⇒ 断言从产物读。

    这两条正是预注册 §1.2 点名要上屏的数，也是唯一回答
    「离 20% 还差多少」的两行。
    """
    ceiling_pct, gap_pp = _deliverable_strings()
    got = _screen_outcome(_gap_with_ledger())

    assert got["hit"], "支 2 没命中 ⇒ 本守卫量不到生产那一支"
    assert got["level"] == "danger", (
        f"level = {got['level']!r} ⇒ 命中的不是支 2（caveat 支），"
        "量到的不是生产那一支的文本")
    missing = [s for s in (ceiling_pct, gap_pp) if s not in got["screen_text"]]
    assert not missing, (
        f"交付口径的 {missing} 不在屏上文本里。\n"
        f"屏上实际是：{got['screen_text'][:400]}\n"
        "机制：这两个数只在 `d2LedgerVerdict` 的 `headline` 里，"
        "而支 2 保留自己的 headline、只搬 `detail` ⇒ headline 无通路")


def test_the_ledger_branch_is_what_puts_them_on_screen():
    """🚨 判别力对照臂：台账不在袋子里时，那两个数**必须不在**屏上。

    缺了这条，上面那条可能恒真 —— 只要那两个串出现在别处
    （caveat 正文、台账里某个我没料到的字段），它就永远绿。
    ⇒ 这条断言的不是「有」，是「**它们只能由台账支送上来**」。
    """
    ceiling_pct, gap_pp = _deliverable_strings()
    got = _screen_outcome("null")

    assert got["hit"], "台账缺席时支 2 仍应命中（caveat 本身还在）"
    assert ceiling_pct not in got["screen_text"], (
        f"{ceiling_pct} 在没有台账的屏上出现 ⇒ 它不是台账送上来的，"
        "上面那条守卫的判别力不成立")
    assert gap_pp not in got["screen_text"], (
        f"{gap_pp} 在没有台账的屏上出现 ⇒ 同上")


def test_the_deliverable_caliber_is_read_before_the_least_deliverable_one():
    """交付口径那一句必须**排在最前**（第 182 层：数字不跟着口径走）。

    第 2 段说的是「榜宽 546 且本金 → ∞」，而那一档在 ¥5 万下
    摩擦 130.97%、净 **−123.40%**，是全表**最不可交付**的一档。
    把它当第一句，读者第一眼读到的是「天花板 12.73%」。
    """
    got = _screen_outcome(_gap_with_ledger())
    raw = _raw_ledger()
    infinite_pct = raw["headline"]["account_annual_ceiling_pct"][
        "at_infinite_capital"]
    assert f"{infinite_pct:.2f}" in got["screen_text"], (
        "正控失效：本金→∞ 那一档不在屏上 ⇒ 顺序这条守卫量不到东西")
    assert got["screen_text"].index("交付口径") < got["screen_text"].index(
        f"{infinite_pct:.2f}"), (
        "最不可交付的那一档排在交付口径之前 ⇒ 读者第一眼读到的是错的口径")

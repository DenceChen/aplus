"""概览页必须喊出「这张榜单不是当前权重排的」——判决板新分支（2026-10-14）。

## 与 `/api/weights` 的 `caveat` 是**两回事**，不要混为一谈

`caveat` 说的是「**现在跑的**权重未经赛马验收 / 是已知坏配置」——
一个关于**配置本身**的属性。

本文件钉的分支说的是另一件事：「**你屏幕上这张榜单**是用另一组权重排出来的」，
或者「**根本不知道**它是用哪组权重排的」。判据来自
`/api/predictions/latest` 的 `weights_check`，**不是** `/api/weights`。

两者的失效方向相反：

- `caveat` 触发时，榜单**确实**是用当前这组权重排的——它只是这组权重有问题。
- `weights_check` 触发时，榜单**根本不代表**当前这组权重的判断——
  它是上一把尺子留下的，而上一把尺子已经被换掉了。

只报前者不报后者，用户会看着一把**已经换掉**的尺子排出的名次建仓，
而页面只告诉他「现在这把尺子没验过」。这两句话合起来仍然会让人下错注。

## 为什么 `unknown` 也必须喊

无法证明一致 ≠ 一致。见
`tests/test_prediction_weights_provenance.py` 里对三态的完整论证。
前端这一层再抄一遍，因为**这里才是用户真正会看到的地方**——
后端算对了但前端没渲染，等于没算。
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

VERDICT_JS = Path(__file__).parent.parent / "aplus" / "web" / "verdict.js"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="需要 node 才能测前端纯逻辑")


def _call(stats, evo=None, weights=None, predictions=None) -> dict:
    payload = json.dumps({"stats": stats, "evo": evo, "weights": weights,
                          "predictions": predictions})
    script = (
        f"const {{docketWarning}} = require({json.dumps(str(VERDICT_JS.resolve()))});"
        f"const p = {payload};"
        "const r = docketWarning(p.stats, p.evo, p.weights, p.predictions);"
        "console.log(JSON.stringify({exists: true, warn: r}));"
    )
    out = subprocess.run(["node", "-e", script],
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


# 一份「已经攒够样本、数字还不错」的 stats：这样一条新警示必须在
# **压过好消息**的情况下也能冒出来，否则测不出它到底排在哪一档。
GOOD_STATS = {"cagr": 0.24, "arith_ann": 0.35, "n_days": 483,
              "overall_hit_rate": 0.58, "judged": 9000}
GOOD_EVO = {"champion_falsified": False}

MISMATCHED = {
    "date": "2026-10-13",
    "weights_check": {
        "status": "mismatch",
        "board_hash": "a1b2c3d4e5f6",
        "current_hash": "0f9e8d7c6b5a",
        "current_weights": {"mom20": -1.0, "rev5": 0.3, "vol20": -0.5, "vsur": -0.5},
    },
}

UNKNOWN = {
    "date": "2026-10-13",
    "weights_check": {
        "status": "unknown",
        "board_hash": None,
        "current_hash": "0f9e8d7c6b5a",
        "current_weights": {"mom20": -1.0, "rev5": 0.3, "vol20": -0.5, "vsur": -0.5},
    },
}

MATCHED = {
    "date": "2026-10-14",
    "weights_check": {
        "status": "match",
        "board_hash": "0f9e8d7c6b5a",
        "current_hash": "0f9e8d7c6b5a",
        "current_weights": {"mom20": -1.0, "rev5": 0.3, "vol20": -0.5, "vsur": -0.5},
    },
}


# ---------- 这条是判别力所在 ----------

def test_mismatched_board_warns_on_the_overview_page():
    """榜单用的不是当前权重 —— 概览页必须报 danger。"""
    r = _call(GOOD_STATS, GOOD_EVO, None, MISMATCHED)
    assert r["warn"] is not None, (
        "榜单早于当前权重（默认符号翻转前生成），概览页却一声不吭")
    assert r["warn"]["level"] == "danger"


def test_unknown_provenance_warns_and_is_never_read_as_match():
    """**本文件最核心的一条。**

    老文件没有出处记录 → `unknown` → 必须报警。
    若前端把它当成 match（不报警），那么后端算得再对也没用：
    所有权重变更之后，页面依然显示一份过期榜单且毫无说明。
    """
    r = _call(GOOD_STATS, GOOD_EVO, None, UNKNOWN)
    assert r["warn"] is not None, (
        "出处不可知被当成了「一致」—— 无法证明一致 ≠ 一致")
    assert r["warn"]["level"] == "danger"
    text = r["warn"]["headline"] + " " + r["warn"]["detail"]
    assert "无法证明" in text or "不可知" in text, (
        f"文案必须说清这是「不知道」而不是「一致」：{text}")


def test_match_produces_no_weights_warning():
    """反方向：对得上就**不许**因为「出处」报警。

    把所有情形都标成警告，警示就会退化成背景噪音——而噪音的真正代价是
    用户学会忽略它，然后连真的那条一起忽略。

    注意：这里断言的是「不因出处报警」，不是「判决板一片安静」——
    `GOOD_STATS` 的 CAGR 0.24 会正常触发既有的「达标」分支，
    那条与本分支无关，不该被这条测试误伤。
    """
    r = _call(GOOD_STATS, GOOD_EVO, None, MATCHED)
    w = r["warn"]
    assert w is not None, "GOOD_STATS(CAGR 0.24) 本就该走「达标」分支"
    assert w["level"] == "ok", (
        f"榜单与当前权重一致，却报了 {w['level']}：{w['headline']}")
    assert "权重" not in w["headline"], (
        f"对得上的榜单不该触发出处警示：{w['headline']}")


def test_mismatch_beats_a_good_cagr():
    """即使命中率漂亮、CAGR 达标，榜单过期这条也必须压过去。"""
    r = _call({"cagr": 0.9, "n_days": 483, "judged": 9000,
               "overall_hit_rate": 0.7}, GOOD_EVO, None, MISMATCHED)
    assert "权重" in r["warn"]["headline"], (
        f"好数字把榜单过期这条盖住了：{r['warn']['headline']}")


# ---------- 文案必须讲清三件事 ----------

def test_the_copy_names_both_sets_of_weights():
    """文案要能回答「哪把尺子 vs 哪把尺子」，而不只是说「不一样」。"""
    r = _call(GOOD_STATS, GOOD_EVO, None, MISMATCHED)
    text = r["warn"]["headline"] + " " + r["warn"]["detail"]
    assert "a1b2c3d4e5f6" in text, f"必须报出榜单那一版的指纹：{text}"
    assert "0f9e8d7c6b5a" in text, f"必须报出当前权重的指纹：{text}"
    assert "2026-10-13" in text, f"必须说清是哪一天的榜单：{text}"


def test_the_copy_shows_the_current_weights_themselves():
    """只给指纹不够——用户看得懂「当前用的是哪几个数」。"""
    r = _call(GOOD_STATS, GOOD_EVO, None, MISMATCHED)
    assert "mom20" in r["warn"]["detail"], "必须把当前权重本身列出来"


def test_unknown_copy_says_the_board_records_nothing():
    """`unknown` 的文案不能说「用的是别的一组」——那是把「不知道」说成了「知道」。

    两种谎话方向相反：说「不同」会让用户以为只是换了配置；
    说「不可知」才告诉他这份榜单**没有任何证据**支持。
    """
    r = _call(GOOD_STATS, GOOD_EVO, None, UNKNOWN)
    text = r["warn"]["headline"] + " " + r["warn"]["detail"]
    assert "不一样" not in text and "不同" not in text, (
        f"出处不可知却说成了「不一样」：{text}")


def test_both_alarming_states_are_danger():
    for payload in (MISMATCHED, UNKNOWN):
        r = _call(GOOD_STATS, GOOD_EVO, None, payload)
        assert r["warn"]["level"] == "danger", (
            f"{payload['weights_check']['status']} 必须是 danger 而不是 warn——"
            "它是「别照着这张榜建仓」，不是「再观察观察」")


# ---------- 表驱动 + 反向覆盖测试 ----------

def test_stale_weights_headlines_table_covers_every_status():
    """新增 status 必须同时补上「这个状态该显示什么」的断言。

    与 `test_docket_warning.py::test_every_caveat_kind_must_have_a_headline`
    同一条纪律：让「忘了配」在测试里变红，而不是在线上默默显示上一个状态的标题。
    """
    out = subprocess.run(
        ["node", "-e",
         "const v = require(%s);"
         "console.log(JSON.stringify(v.STALE_WEIGHTS_HEADLINES));" % json.dumps(
             str(VERDICT_JS.resolve()))],
        capture_output=True, text=True, check=True)
    table = json.loads(out.stdout)

    assert table == {"mismatch": "当前榜单不是现在这组权重排出来的",
                     "unknown": "当前榜单的权重出处不可知"}, (
        "STALE_WEIGHTS_HEADLINES 变了，本文件的覆盖测试必须同步更新——"
        "新增 status 请同时补上它该显示什么、以及它必须报警的断言")


def test_an_unrecognised_status_degrades_to_unknown_not_to_silence():
    """后端将来加一个本文件不认识的状态时，**必须**降级到 unknown。

    方向的选择是刻意的：本文件认识的每个分支都报警，
    所以「不认识」的最安全落点也是报警那一侧。
    若默认成静默，后端加一个新状态的那一刻，警示就会**静默地全部失效**——
    而那正是这个字段被加进来要防的那类事故。
    """
    for status in ("", None, "some_status_from_the_future"):
        r = _call(GOOD_STATS, GOOD_EVO, None,
                  {"date": "2026-10-13", "weights_check": {"status": status}})
        assert r["warn"] is not None, f"weights_check.status={status!r} 静默了"
        assert r["warn"]["level"] == "danger"


def test_absent_predictions_object_must_not_invent_a_warning():
    """`predictions` 整个取不到（旧接口 / 请求失败）时不得凭空造警示。

    区分两种「没有」：接口说 `unknown` 是**证据**（它确实查了、查不出）；
    接口压根没给是**没证据**。后者不该被渲染成「榜单出处不可知」——
    那是在指控一个可能完全正常的榜单。
    """
    for p in (None, {}):
        r = _call(GOOD_STATS, GOOD_EVO, None, p)
        w = r["warn"]
        # GOOD_STATS 会走既有的「达标」分支，所以断言的是
        # 「不因出处报警」，不是「判决板一片安静」。
        assert w["level"] == "ok", f"predictions={p!r} 却凭空造了警示：{w}"
        assert "权重" not in w["headline"], (
            f"predictions={p!r} 不该触发出处警示：{w['headline']}")


def test_stale_weights_branch_does_not_ship_html():
    """这条分支不得携带 `detailHtml`。

    钉住「HTML 只能由 verdict.js 的可信常量生成」——
    这个分支的内容来自接口数据，不是写死的常量。
    """
    for payload in (MISMATCHED, UNKNOWN):
        r = _call(GOOD_STATS, GOOD_EVO, None, payload)
        assert "detailHtml" not in r["warn"], "非 caveat 分支不得携带 HTML"


def test_stale_weights_copy_has_no_markdown_markers_left_in_it():
    """正文里**不许**出现 `**`——这条分支不走 `renderCaveat`，星号会原样显示。

    ## 这是浏览器实测发现的（2026-10-14）

    第一版正文里写了「所以**无法证明**它用的是当前这组」，
    页面上一字不差地显示出两个星号。

    纯函数测试测不到它：`detail` 字符串里确实**含有** `**`，
    任何只断言字符串内容的测试都会绿。问题发生在**渲染**那一步——
    caveat 分支有 `detailHtml`，这条分支没有，于是走 `esc(detail)`，
    星号原样打印。

    所以这条断言的是「正文里根本不该有记号」，而不是「渲染时该去掉它」：
    去掉是渲染层的责任，而这条分支**刻意**不走渲染层。
    """
    for payload in (MISMATCHED, UNKNOWN):
        detail = _call(GOOD_STATS, GOOD_EVO, None, payload)["warn"]["detail"]
        assert "**" not in detail, (
            f"这条分支不带 detailHtml，`**` 会被原样打印到页面上：{detail}")
        assert "*" not in detail, f"出现了单星号：{detail}"


# ---------- 优先级：它得排在「回落配置」之前还是之后 ----------

BAD_CONFIG_WEIGHTS = {
    "source": "fallback", "caveat": "回落默认权重，**未经赛马验证**……",
    "caveat_kind": "unraced",
}


def test_stale_board_outranks_the_fallback_config_caveat():
    """「你看的这张榜不是现在这组权重排的」比「现在这组权重没验过」更靠前。

    理由：caveat 讲的是**配置**的属性，而这条讲的是**屏幕上那份东西**
    到底代表什么。榜单已经过期时，再解释当前配置有多不可靠，
    是在回答一个次要问题——用户真正需要先知道的是「这张榜不能照着用」。
    """
    r = _call(GOOD_STATS, GOOD_EVO, BAD_CONFIG_WEIGHTS, MISMATCHED)
    assert "权重" in r["warn"]["headline"] and "坏配置" not in r["warn"]["headline"], (
        f"榜单出处这条必须排在回落配置之前，实得：{r['warn']['headline']}")


def test_caveat_branch_still_works_without_a_predictions_payload():
    """加新分支不能把旧分支挤掉——没有 predictions 时 caveat 照常生效。"""
    r = _call({"cagr": None, "n_days": 1}, {"champion_falsified": False},
              BAD_CONFIG_WEIGHTS, None)
    assert r["warn"] is not None
    assert "未经赛马验证" in r["warn"]["headline"]


# ---------- 接线：前端真的把 predictions 送进去了吗 ----------

def test_both_docket_call_sites_pass_predictions():
    """两个 `renderDocket` 调用点都必须传 predictions。

    少一处就出真 bug：`loadChrome` 只跑在启动时，`loadOverview` 跑在切到概览页时，
    而 `renderDocket` 内部**无条件重写**判决板。没传 predictions 的那次调用
    会把另一次已经渲染好的「榜单过期」警示**抹掉**——
    表现为「刚进页面时看得见警示，点两下别的 tab 再回来就没了」。
    """
    import re

    html = Path("aplus/web/index.html").read_text(encoding="utf-8")
    calls = re.findall(r"renderDocket\(([^)]*)\)", html)
    assert len(calls) >= 2, f"预期至少 2 个调用点，实得 {len(calls)}"
    for arg in calls:
        assert "predictions" in arg or "pred" in arg, (
            f"调用点 renderDocket({arg}) 没传榜单 —— 警示会被另一处渲染抹掉")


def test_load_chrome_fetches_the_latest_predictions():
    """传了 predictions 就必须真的取到，否则它恒为 null、警示永远不显示。

    `loadChrome` 是**外壳**：深链进任何 tab 都会调它。若只有 `loadOverview`
    取 `/api/predictions/latest`，那么深链进「进化」页时概览顶部看不到警示。
    """
    html = Path("aplus/web/index.html").read_text(encoding="utf-8")
    body = html.split("async function loadChrome(")[1].split("\n}")[0]
    assert "/api/predictions/latest" in body, (
        "loadChrome 没取 /api/predictions/latest —— "
        "深链进非概览页时判决板看不到榜单出处警示")


def test_overview_docket_call_site_receives_the_fetched_board():
    """`loadOverview` 已经取了 `pred`，必须把它传进 `renderDocket`。"""
    import re

    html = Path("aplus/web/index.html").read_text(encoding="utf-8")
    body = html.split("async function loadOverview(")[1].split("\n}")[0]
    assert "/api/predictions/latest" in body
    calls = re.findall(r"renderDocket\(([^)]*)\)", body)
    assert calls, "loadOverview 里没有 renderDocket 调用"
    assert "pred" in calls[-1], (
        f"loadOverview 取了榜单却没传给判决板：renderDocket({calls[-1]})")

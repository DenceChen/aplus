"""判决板警示判定 —— TDD（2026-10-14）。

## 这次要解决什么

概览页的判决板 `renderDocket(stats)` **只按命中率分档**：
`>=0.55` 初步有效 / `>=0.45` 与抛硬币无异 / 否则暂时跑输。

问题有三个，每一个都已经被实测数据钉死：

1. **它看不到复利。** 命中率与赚钱是两件事。一个 51% 命中的策略完全
   可以稳定亏钱——赢的时候赚得少、输的时候亏得多。实测现任权重正是
   这个形态：命中率落在「与抛硬币无异」档，但真实 CAGR **−84.70%**。

2. **它看不到证伪。** 进化页早就在红字写「现任权重已被证伪（4 段独立
   窗口一致显著为负）」，概览页**一个字都没有**。用户在概览页看到的
   是「暂时跑输，等下一轮权重赛马换掉它」——把它说成「等换」，
   弱化了「已经判负」的严重性。

3. **样本不足时会展示荒谬的年化。** 归档只有 1 天时，CAGR 会被算成
   (1+r)^252−1 = **−99.9%**。那不是「年化亏 99.9%」，而是「没测过」。

## 为什么用 python 驱动 node

前端此前**零测试基建**。判定逻辑若留在 `renderDocket` 里，只能靠
「打开浏览器看一眼」验证——那验证不了分支覆盖。抽出纯函数
`tests/verdict.js` 后可以 node 直跑，但那样它就**不在 pytest 里**，
CI 跑不到。所以这里用 `subprocess` 调 node，测试仍然留在全仓测试套里。
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

VERDICT_JS = Path(__file__).parent.parent / "aplus" / "web" / "verdict.js"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="需要 node 才能测前端纯逻辑")


def _call(fn: str, stats, evo, weights=None) -> dict:
    """在 node 里调 `docketWarning(stats, evo, weights)`，把结果转回 python。

    返回 `{}` 表示函数返回了 null（无警示）——JSON 里 null 与「函数不存在」
    必须能区分，否则一条「函数写错了名字」的测试会伪装成「判定正确」。
    """
    payload = json.dumps({"stats": stats, "evo": evo, "weights": weights})
    script = (
        f"const {{docketWarning}} = require({json.dumps(str(VERDICT_JS.resolve()))});"
        f"const p = {payload};"
        "const r = docketWarning(p.stats, p.evo, p.weights);"
        "console.log(JSON.stringify({exists: true, warn: r}));"
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


# ---------- 这条是判别力所在 ----------

def test_falsified_champion_must_warn_on_the_overview_page():
    """现任被证伪时概览页必须喊出来——此前进化页有红字、概览页完全静默。"""
    w = _call("docketWarning",
              {"cagr": 0.05, "n_days": 483, "overall_hit_rate": 0.51, "judged": 9000},
              {"champion_falsified": True, "champion_windows": 4})
    assert w["warn"] is not None, "现任已被证伪，概览页却没有任何提示"
    assert w["warn"]["level"] == "danger"
    assert "证伪" in w["warn"]["headline"]
    assert "4" in w["warn"]["detail"], "必须带上是几段窗口判负的"


def test_falsified_beats_a_healthy_cagr():
    """证伪优先于任何好数字——被独立窗口判负的权重，CAGR 再好也不能洗白。"""
    w = _call("docketWarning",
              {"cagr": 0.9, "n_days": 483},
              {"champion_falsified": True, "champion_windows": 3})
    assert "证伪" in w["warn"]["headline"], "高 CAGR 不许掩盖证伪"


def test_compound_negative_must_warn_even_when_hit_rate_is_fine():
    """命中率 51%（落在「与抛硬币无异」）但 CAGR −84.7% —— 必须报 danger。

    这条钉的是本次改动的核心命题：**命中率不是钱**。
    """
    w = _call("docketWarning",
              {"cagr": -0.847, "arith_ann": -1.7192, "ann_vol": 0.546,
               "n_days": 483, "overall_hit_rate": 0.51, "judged": 9000},
              None)
    assert w["warn"] is not None, "复利年化 −84.7% 却没有任何警示"
    assert w["warn"]["level"] == "danger"
    # 真实年化必须出现在**展示给用户的文本**里（headline + detail 合计）。
    # 上一版断言它在 headline 里，红了——因为实现在 detail 里报了 −84.7%。
    # 「不能只说『为负』」这个判别力没变：变的是它可以出现在任一处。
    shown = w["warn"]["headline"] + " " + w["warn"]["detail"]
    assert "84.7" in shown, f"必须报出真实年化，实得：{shown}"
    # 必须同时点破算术口径在说谎
    assert "算术" in w["warn"]["detail"]
    # 波动拖累必须被算出来并报出（本例 σ²/2 = 14.91%/年）
    assert "14.9" in w["warn"]["detail"], f"波动拖累必须报出：{w['warn']['detail']}"


def test_one_day_sample_must_not_be_shown_as_a_track_record():
    """只测 1 天：CAGR = −99.9% 是「没测」，不是「年化亏 99.9%」。"""
    w = _call("docketWarning", {"cagr": -0.999, "n_days": 1}, None)
    assert w["warn"] is not None
    assert w["warn"]["level"] == "warn"
    assert "样本不足" in w["warn"]["headline"]
    assert "1" in w["warn"]["detail"]


def test_one_day_sample_outranks_a_negative_cagr():
    """1 天的样本里，CAGR 无论是什么都不能当业绩读——先说样本不足。"""
    w = _call("docketWarning", {"cagr": -0.5, "n_days": 2}, None)
    assert "样本不足" in w["warn"]["headline"]


# ---------- 反方向：不得过度报警 ----------

def test_meeting_the_target_should_not_say_danger():
    """CAGR 24% 达标时不得报 danger——否则警示会退化成背景噪音而被无视。"""
    w = _call("docketWarning", {"cagr": 0.24, "n_days": 483}, None)
    assert w["warn"]["level"] == "ok"


def test_legacy_stats_without_cagr_must_stay_silent():
    """旧接口没有 cagr 字段时不得凭空造警示——那会让人以为出错了。"""
    w = _call("docketWarning", {"overall_hit_rate": 0.5, "judged": 20}, None)
    assert w["warn"] is None


def test_empty_stats_must_not_crash_or_warn():
    """空 stats / 空 evo 都不得抛异常。"""
    for stats in (None, {}, {"cagr": None, "n_days": 0}):
        assert _call("docketWarning", stats, None)["warn"] is None


def test_below_target_but_positive_is_only_a_warning():
    """+11% 未达标但为正：warn 不是 danger——它是「还差得远」，不是「在亏钱」。"""
    w = _call("docketWarning", {"cagr": 0.11, "arith_ann": 0.25, "n_days": 483}, None)
    assert w["warn"]["level"] == "warn"
    assert "20%" in w["warn"]["headline"], "必须让用户知道目标是 20%"


# ---------- 接线：前端真的用上了吗 ----------

def test_index_html_actually_calls_the_warning_renderer():
    """判定函数必须被 `renderDocket` 真的调用。

    上一版把纯函数写好、测试全绿，但 `index.html` 没接线——
    那就是**绿着的死代码**。这条读源文件把接线钉死。
    """
    html = Path("aplus/web/index.html").read_text(encoding="utf-8")
    assert "docketWarning" in html, "index.html 从未引用 docketWarning —— 警示是死代码"


def test_both_docket_call_sites_must_pass_evo():
    """**两个** `renderDocket` 调用点都必须传 `evo`——少一处就出真 bug。

    ## 这条是补上一条假绿

    上一版只断言 `champion_falsified` 字样出现在文件里，结果把
    `loadOverview` 改成 `renderDocket(status.stats)`（丢掉 evo）
    之后**全部测试仍绿**。但那是真 bug：`renderDocket` 内部无条件重写
    判决板，于是 `loadChrome` 刚渲染好的警示会被 `loadOverview` 抹掉——
    表现为「深链进概览时警示消失，切到别的 tab 又出现」。

    「文件里提到过 champion_falsified」不等于「每个渲染路径都传对了参数」。
    这条改成**逐个调用点**检查。
    """
    import re

    html = Path("aplus/web/index.html").read_text(encoding="utf-8")
    calls = re.findall(r"renderDocket\(([^)]*)\)", html)
    assert len(calls) >= 2, f"预期至少 2 个调用点，实得 {len(calls)}"
    for arg in calls:
        assert "stats" in arg, f"调用点缺少 stats：renderDocket({arg})"
        assert "evo" in arg, (
            f"调用点 renderDocket({arg}) 没传 evo —— "
            "它会把另一个调用点已渲染的警示抹掉")


def test_both_docket_call_sites_fetch_evolution_summary():
    """传了 evo 就必须真的取到，否则 evo 恒为 null、警示永远不显示。"""
    import re

    html = Path("aplus/web/index.html").read_text(encoding="utf-8")
    # 两个取 stats 的地方：loadChrome 与 loadOverview
    for fn in ("loadChrome", "loadOverview"):
        body = html.split(f"async function {fn}(")[1].split("\n}")[0]
        assert "/api/evolution/summary" in body, (
            f"{fn} 取了 evo 却没有请求 /api/evolution/summary —— evo 恒为 null")


def test_verdict_js_is_shipped_or_loaded():
    """index.html 必须能加载这个模块——否则运行时是 ReferenceError。

    ## 为什么不能用 `assert "verdict.js" in html`

    这条曾经就是这么写的，而 `verdict.js` 在 index.html 里出现 **6 处，
    其中 5 处是注释**（docstring 解释它、注释说明它、末尾注释再提一次）。
    只剩 1 处是真的 `<script src="/verdict.js">`。
    ⇒ 把那个 src 改成 `/verdict-disabled.js` 之后，这条测试**依然全绿**
    （实测 82 passed）——断言被注释喂饱了。

    这是 `CLAUDE.md` 第 13 条（注释满足子串断言）的**第二个实例**，
    第一个是 `/api/chat/stream`。⇒ 这里必须解析出真正的 `<script src>`，
    不能对全文做裸子串匹配。

    三环链路这里验两环：
      ① HTML 里真的有 `<script src="/verdict.js">`（按语法解析，不按子串）
      ② 被挂到 `/` 上的那个文件**确实存在**（`webapp.py:672` 挂的是整个 `aplus/web/`）
    第三环（路由真的发得出去）由 E2E 在真浏览器里验。
    """
    import re

    web = Path("aplus/web")
    html = (web / "index.html").read_text(encoding="utf-8")

    # 只认真正的 <script src="...">，注释里的同名字符串不算数
    srcs = re.findall(r'<script\s[^>]*\bsrc=["\']([^"\']+)["\']', html)
    assert "/verdict.js" in srcs, (
        f"index.html 的 <script src> 里没有 /verdict.js，实测有：{srcs}。"
        "注意不能用 `'verdict.js' in html` 来判——"
        "全文里有 5 处注释提到它，子串断言会被注释喂饱。"
    )

    # 被 StaticFiles 挂到 `/` 上，路径必须落到真实文件；否则是 404 → 同样 ReferenceError
    target = web / "verdict.js"
    assert target.is_file(), (
        f"index.html 引用了 /verdict.js，但 {target} 不存在 —— "
        "静态挂载会 404，运行时是 ReferenceError，测试不该放行")


def test_hidden_warning_can_actually_be_hidden():
    """`display:flex` 会盖过 `[hidden]` 的浏览器默认 `display:none`。

    纯函数测不到这条——它只在浏览器里表现为「警示条永远显示」。
    这条读样式表把补丁钉住，免得将来有人「清理 CSS」时顺手删掉。

    ## 真浏览器已实测（2026-10-14，Playwright）

        w.hidden = true
        → getComputedStyle(w).display = "none"
        → w.offsetHeight = 0, w.getClientRects().length = 0

    没有那行 `.docket-warn[hidden] { display: none; }` 时，
    display 会停在 `flex`、offsetHeight = 38px。
    """
    html = Path("aplus/web/index.html").read_text(encoding="utf-8")
    assert ".docket-warn[hidden]" in html, (
        "`.docket-warn` 用了 display:flex，必须显式补 [hidden] 的 display:none，"
        "否则警示条永远显示")


def test_warn_container_has_a_dom_node():
    """`docket-warn` 元素必须真的存在于 DOM 里。

    这条钉的是 `renderDocket` 里 `$('docket-warn')` 不会抛
    「元素不存在」。$ 找不到元素时返回 null，后面写 `.hidden` 就是
    TypeError——而它只会在**有警示可显示时**才触发，即线上事故恰好
    发生在最该显示警示的时刻。
    """
    html = Path("aplus/web/index.html").read_text(encoding="utf-8")
    assert 'id="docket-warn"' in html, "判决板缺少警示条容器"


# ---------- 回落权重的警示也必须上概览页 ----------

def test_fallback_caveat_is_fed_into_the_docket():
    """概览页的判决板必须能拿到 `/api/weights` 的 `caveat` 并显示。

    ## 为什么这条不能省

    `load_weights` 早就写好了这段警告——

        回落默认权重：……这是**已知的坏配置**，不是「中性起点」。
        日频执行它的年成本量级 351%。

    但它此前只在**进化页**、且经由一个被路由丢掉了 caveat 的接口。
    于是概览页（用户第一屏）显示「未经赛马验收的默认值」——
    这句话在字面上没错，但它把「**测过、是坏的**」说成了「没测过」。

    判决板本来就是讲结论的地方，而「现在跑的是已知坏配置」是本平台
    最该被讲出来的结论之一。
    """
    html = Path("aplus/web/index.html").read_text(encoding="utf-8")
    body = html.split("async function loadChrome(")[1].split("\n}")[0]
    assert "/api/weights" in body, (
        "loadChrome 没取 /api/weights —— 概览页不知道当前跑的是回落配置")
    assert "caveat" in body, "取到了 /api/weights 却没有读 caveat"

# ---------- 回落配置：比证伪更根本 ----------

BAD_CONFIG_CAVEAT = (
    "回落默认权重：未经任何赛马测量。它是 DEFAULT_WEIGHTS="
    "{mom20:1.0,rev5:0.3,vol20:0.5,vsur:0.5}，在 2026-10-14 的 730 天赛马里"
    "排第 81/87 名，测试段超额 -0.74%、t=-3.03 —— 这是**已知的坏配置**，"
    "不是「中性起点」。日频执行它的年成本量级 351%。")


def test_fallback_bad_config_warns_on_the_overview_page():
    """线上跑着已知坏配置时，概览页必须喊出来。"""
    r = _call("docketWarning",
              {"cagr": None, "n_days": 1, "overall_hit_rate": 0.2, "judged": 20},
              {"champion_falsified": True, "champion_windows": 2},
              {"source": "fallback", "caveat": BAD_CONFIG_CAVEAT,
               "caveat_kind": "measured_bad"})
    assert r["warn"] is not None, "跑着已知坏配置却没有任何警示"
    assert r["warn"]["level"] == "danger"
    assert "坏配置" in r["warn"]["headline"]
    # 必须逐字显示 caveat 全文——那是实测记录，不是文案
    assert r["warn"]["detail"] == BAD_CONFIG_CAVEAT


# ---------- 标题必须跟着证据状态走（2026-10-14 默认权重翻转后） ----------
#
# 起因：默认权重符号按实测 IC 整体翻转后，`caveat` 正文改成了
# 「方向有证据、效果未验证」，而前端 headline 仍硬编码
# 「当前跑的是已知的坏配置」——正文说自己没验过，标题说自己测过且是坏的。
# 同一个警示框里两句话互相打架，**两句话都在骗用户**，方向还相反。
#
# 两种危险是不同的：
#   · measured_bad —— 测过，结论为负。不建仓等于已知亏损。
#   · unraced     —— 根本没测过。不建仓等于放弃一个未知的可能性。
# 标题必须把「是哪一种」讲出来，不能替用户把两者当成一回事。

UNRACED_CAVEAT = (
    "回落默认权重，**未经赛马验证**。符号已按实测 IC 校正……"
    "当前状态是「方向有证据、效果未验证」，不是「已验收的冠军」。")

MEASURED_BAD_HEADLINE = "当前跑的是已知的坏配置"


def test_unraced_caveat_must_not_be_labelled_a_measured_bad_config():
    """未赛马验证的配置，标题不得说成「已知的坏配置」。

    这是本文件最重要的一条：说「已知为坏」会让用户以为**已经验过了**，
    而真实状态是**根本没验过**。两种谎言导致完全不同的下一步动作——
    前者该换配置，后者该去验。
    """
    r = _call("docketWarning", {"cagr": None, "n_days": 1},
              {"champion_falsified": False},
              {"source": "fallback", "caveat": UNRACED_CAVEAT,
               "caveat_kind": "unraced"})
    assert MEASURED_BAD_HEADLINE not in r["warn"]["headline"], (
        "未赛马验证 ≠ 已测为坏：标题在夸大证据强度，"
        f"实得：{r['warn']['headline']}")
    assert "未经赛马验证" in r["warn"]["headline"], (
        f"标题必须如实陈述证据状态，实得：{r['warn']['headline']}")


def test_unraced_caveat_headline_must_not_contradict_its_own_body():
    """标题与正文不得同时出现「未验证」和「已知的坏」两种说法。"""
    r = _call("docketWarning", {"cagr": None, "n_days": 1},
              {"champion_falsified": False},
              {"source": "fallback", "caveat": UNRACED_CAVEAT,
               "caveat_kind": "unraced"})
    text = r["warn"]["headline"] + " " + r["warn"]["detail"]
    assert "未经赛马验证" in text
    assert "已知的坏配置" not in text, (
        f"同一个警示框里两种互相打架的说法：{text[:160]}")


def test_measured_bad_caveat_kind_still_gets_the_bad_config_headline():
    """反方向：真的测坏了就仍然喊「坏配置」——不许一刀切弱化。"""
    r = _call("docketWarning", {"cagr": None, "n_days": 1},
              {"champion_falsified": False},
              {"source": "fallback", "caveat": BAD_CONFIG_CAVEAT,
               "caveat_kind": "measured_bad"})
    assert r["warn"]["headline"] == MEASURED_BAD_HEADLINE


def test_unknown_or_missing_caveat_kind_falls_back_to_the_weaker_claim():
    """`caveat_kind` 缺失/未知时必须降级到弱说法，不得默认「已知的坏」。

    失败方向的选择是刻意的：后端忘了带 kind，最多让页面**少说一句**；
    若默认成「已知的坏」，页面就会**多说一句没根据的话**。
    多说比少说危险。
    """
    for kind in (None, "", "some_kind_from_the_future"):
        r = _call("docketWarning", {"cagr": None, "n_days": 1},
                  {"champion_falsified": False},
                  {"source": "fallback", "caveat": UNRACED_CAVEAT,
                   "caveat_kind": kind})
        assert MEASURED_BAD_HEADLINE not in r["warn"]["headline"], (
            f"caveat_kind={kind!r} 时不得默认成「已知的坏配置」")


def test_every_caveat_kind_must_have_a_headline():
    """`CAVEAT_HEADLINES` 的键必须被本文件显式枚举覆盖。

    反向测试：将来新增一种 kind 却忘了配标题时，本条会失败——
    逼着新增者同时补一条「这个 kind 该显示什么」的断言，
    而不是让线上默默显示上一个 kind 的标题。
    """
    out = subprocess.run(
        ["node", "-e",
         "const v = require(%s);"
         "console.log(JSON.stringify(v.CAVEAT_HEADLINES));" % json.dumps(
             str(VERDICT_JS.resolve()))],
        capture_output=True, text=True, check=True)
    table = json.loads(out.stdout)

    assert table == {"measured_bad": MEASURED_BAD_HEADLINE,
                     "unraced": "当前权重未经赛马验证"}, (
        "CAVEAT_HEADLINES 变了，本文件的 kind 覆盖测试必须同步更新——"
        "新增 kind 请同时补上它该显示的标题断言")


def test_caveat_detail_still_passes_through_verbatim_under_every_kind():
    """换标题不得动正文——caveat 是一次实测的完整记录。"""
    for kind, text in (("unraced", UNRACED_CAVEAT),
                       ("measured_bad", BAD_CONFIG_CAVEAT)):
        r = _call("docketWarning", {"cagr": None, "n_days": 1},
                  {"champion_falsified": False},
                  {"source": "fallback", "caveat": text, "caveat_kind": kind})
        assert r["warn"]["detail"] == text, f"kind={kind} 下正文被改写了"


def test_bad_config_outranks_falsification():
    """「跑的是没赛马过的坏配置」比「赛马判它为负」更根本。

    证伪说的是「这组权重被判负」，而回落说的是「线上根本没跑任何
    赛马过的权重」。后者是前者的上游——它连被判负的机会都没有。
    """
    r = _call("docketWarning", {"cagr": None, "n_days": 1},
              {"champion_falsified": True, "champion_windows": 4},
              {"source": "fallback", "caveat": BAD_CONFIG_CAVEAT,
               "caveat_kind": "measured_bad"})
    assert "坏配置" in r["warn"]["headline"], "回落配置必须排在证伪之前"


def test_promoted_weights_have_no_caveat_so_no_false_alarm():
    """权重一旦落盘，caveat 为空 —— 不得对好配置也喊「坏配置」。

    防的是「把所有情形都标成警告」这种让警示退化成背景噪音的实现。
    """
    r = _call("docketWarning", {"cagr": 0.24, "n_days": 483},
              {"champion_falsified": False},
              {"source": "evolved", "caveat": None,
               "evidence": {"promoted_on": "2026-10-14"}})
    assert r["warn"]["level"] == "ok", "落盘且达标时不得报 danger"


def test_empty_string_caveat_must_not_trigger_the_bad_config_branch():
    """`caveat: ''` 与 `caveat: null` 都必须视作「没有警告」。"""
    for c in ("", None):
        r = _call("docketWarning", {"cagr": 0.24, "n_days": 483},
                  {"champion_falsified": False}, {"source": "evolved", "caveat": c})
        assert "坏配置" not in r["warn"]["headline"], (
            f"caveat={c!r} 不该触发坏配置警示")


def test_fallback_caveat_is_fed_into_the_docket():
    """概览页的判决板必须能拿到 /api/weights 的 caveat 并显示。

    此前 caveat 只经由进化页显示，而概览页写的是「未经赛马验收的默认值」——
    那句话把「**测过、是坏的**」说成了「没测过」，方向完全相反。
    """
    html = Path("aplus/web/index.html").read_text(encoding="utf-8")
    for fn in ("loadChrome", "loadOverview"):
        body = html.split(f"async function {fn}(")[1].split("\n}")[0]
        assert "/api/weights" in body, (
            f"{fn} 没取 /api/weights —— 概览页不知道当前跑的是回落配置")


def test_both_docket_call_sites_must_pass_all_three_arguments():
    """两个调用点都必须传齐 stats / evo / weights。"""
    import re

    html = Path("aplus/web/index.html").read_text(encoding="utf-8")
    calls = re.findall(r"renderDocket\(([^)]*)\)", html)
    assert len(calls) >= 2, f"预期至少 2 个调用点，实得 {len(calls)}"
    for arg in calls:
        for name in ("stats", "evo", "weights"):
            assert name in arg, f"调用点 renderDocket({arg}) 缺 {name}"


# ---------- caveat 的 Markdown 加粗：必须先转义再还原 ----------

def _render_caveat(text: str) -> str:
    out = subprocess.run(
        ["node", "-e",
         "const {renderCaveat} = require(%s);"
         "console.log(JSON.stringify(renderCaveat(%s)));"
         % (json.dumps(str(VERDICT_JS.resolve())), json.dumps(text))],
        capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_caveat_bold_markers_become_real_bold():
    """`**已知的坏配置**` 要显示成加粗，而不是把星号原样打印。"""
    out = _render_caveat("这是**已知的坏配置**，不是中性起点。")
    assert out == "这是<b>已知的坏配置</b>，不是中性起点。"
    assert "**" not in out


def test_caveat_html_must_be_escaped_before_rendering():
    """先转义再还原加粗 —— 顺序反了就是 XSS。"""
    out = _render_caveat("<img src=x onerror=alert(1)>**粗**")
    assert "<img" not in out, f"标签必须被转义：{out}"
    assert "&lt;img" in out
    assert "<b>粗</b>" in out, "转义不影响加粗"


def test_caveat_script_tags_must_be_escaped():
    out = _render_caveat("<script>alert(1)</script>")
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_caveat_bold_does_not_swallow_html_injection():
    """`**<b>x**` 也不能变成真的 `<b>` 标签 —— 转义先于加粗。"""
    out = _render_caveat("**<script>alert(1)</script>**")
    assert "<script>" not in out
    assert out.count("<b>") == 1, "只应有加粗产生的那一个 <b>"


def test_caveat_with_no_bold_passes_through_escaped():
    out = _render_caveat("a & b")
    assert out == "a &amp; b"


def test_docket_must_use_detail_html_only_when_present():
    """只有 caveat 分支带 `detailHtml`；其它分支一律 `esc(detail)`。

    钉住「HTML 只能由 verdict.js 生成」——调用方不许自造 HTML。
    """
    import re

    html = Path("aplus/web/index.html").read_text(encoding="utf-8")
    assert "warnOut.detailHtml ?? esc(warnOut.detail)" in html, (
        "渲染必须对 detailHtml 缺失时回退到 esc(detail)")


def test_bad_config_branch_is_the_only_one_carrying_html():
    r = _call("docketWarning", {"cagr": -0.847, "n_days": 483},
              {"champion_falsified": False}, None)
    assert "detailHtml" not in r["warn"], (
        "非 caveat 分支不得携带 HTML —— 它们的内容不是可信常量")

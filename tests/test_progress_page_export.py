"""守卫：**目标进度静态页**（自包含 · 只搬运 · 不测量）。

用户 2026-10-21 原话：「静态页面定期推送服务器就行」＋「我不在家也可以看进度」。

## 这个文件守什么

| # | 守卫 | 它堵住的失效 |
|---|---|---|
| 1 | 导出页**自包含**：没有 `fetch`、没有 `/api/`、没有外部资源 | 静态服务器上页面是空的（`fetch` 不到后端） |
| 2 | 屏上**每一个数**都能指到产物路径 | 手抄 / 用公式重算（第 109 / 167 层） |
| 3 | 屏上**不出现**「已达成 20%」 | 把「测不出」写成「已达成」（第 16 层） |
| 4 | 屏上**不出现**那两个名字与内容相反的部署比例字段 | 字段名与内容相反（口径台账） |
| 5 | 屏上**不出现**跨口径那个倍数 | 拿 `is_the_gap = false` 的列当缺口（第 143 层） |
| 6 | 屏上**写明快照时间**，且**不像**持续更新的数据 | 让人把冻结的数当成现在的数 |
| 7 | 推送脚本**只**打到自己那个目录 | 打到别人的家目录（本仓铁律） |
| 8 | 推送脚本**不后台化** | `nohup &` 制造「已完成」的假象（第 8 层） |
| 9 | systemd 单元**只**监听 8080 | 占用 wordson 的 8000 |
| 10 | systemd 单元**不用** wordson 的 venv | 动别人的依赖 |
| 11 | 静态页的配色**逐位等于** live 页的 `:root` | 两份副本之间没有守卫（第 96 层） |
| 12 | **8 条目标**一条不少 | 悄悄少一条 |
| 13 | 导出器**拒绝**导出没有出处的读数 | 渲染一个指不到产物路径的数 |
| 14 | 导出器**拒绝**导出缺口倍数不唯一的快照 | 取错了那一列还照样出页 |
| 15 | 内联的 JSON 是**子集**（跨口径那一列不在里面） | 页面的源码里藏着那个倍数 |
| 16 | cron **有**那一条、**不后台化**、**排在** 22:05 之后 | 「定期推送」根本没排上 |

## ⚠️ 为什么第 5 条用**数值**判而不是**子串**判

产物里另有一个**合法**读数：`95% 单侧上界 U = 22.3554%`。
它的显示串里恰好含子串 `2.35` ⇒ 裸子串判会把**合法的读数**判死
（过严的守卫和恒真的守卫一样有害：它会把人推向「改本来正确的读数」）。
⇒ 判据 = 「页面里没有任何**数值**等于跨口径那个倍数」＋
「页面里每一处 `2.35` 都属于 `22.3554` 这个更长的数」。
"""
from __future__ import annotations

import html
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXPORTER = ROOT / "scripts" / "export_progress_page.py"
DEPLOY_SH = ROOT / "scripts" / "deploy_progress_page.sh"
UNIT = ROOT / "deploy" / "aplus-progress.service"
REPORTS = ROOT / "data" / "reports"
LIVE_INDEX = ROOT / "aplus" / "web" / "index.html"

# 跨口径那个倍数的**真值**（从快照产物里读，不手打）
CROSS_CALIBER_VALUE = 2.3450157528864404
CROSS_CALIBER_DISPLAY = "2.3450"


def _load_exporter():
    spec = importlib.util.spec_from_file_location(
        "export_progress_page_under_test", EXPORTER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def exported(tmp_path_factory):
    """真跑一次导出器，拿到页面 + 模型（**不读任何落盘的旧页面**）。"""
    out = tmp_path_factory.mktemp("static") / "index.html"
    r = subprocess.run(
        [sys.executable, str(EXPORTER), "--out", str(out),
         "--reports-dir", str(REPORTS)],
        cwd=str(ROOT), capture_output=True, text=True)
    assert r.returncode == 0, "导出器 rc=%s：%s" % (r.returncode, r.stderr)
    page = out.read_text(encoding="utf-8")
    m = re.search(
        r'<script type="application/json" id="aplus-progress-snapshot">(.*?)</script>',
        page, re.S)
    assert m, "页面里没有内联的 JSON 岛"
    model = json.loads(m.group(1).replace("\\u003c", "<"))
    return {"page": page, "model": model, "out": out, "rc": r.returncode,
            "stderr": r.stderr}


def _all_metrics(model):
    """模型里**所有**被渲染的读数（含两个口径里的）。"""
    out = []
    for g in model["goals"]:
        out.extend(g["metrics"])
        for c in g["calibers"]:
            out.extend(c["metrics"])
        gm = g.get("gap_multiple")
        if gm:
            out.append(gm["metric"])
    return out


# ── 1. 自包含 ──────────────────────────────────────────────────────

def test_the_exported_page_is_self_contained(exported):
    """静态服务器上没有后端 ⇒ 页面不许依赖任何一次网络请求。"""
    page = exported["page"]
    assert "fetch(" not in page, "页面里还有 fetch() ⇒ 静态服务器上读不到任何数"
    assert "/api/" not in page, "页面里还有 /api/ 绝对路径 ⇒ 不是自包含页"
    assert not re.search(r'<script[^>]+src=', page), "页面引用了外部脚本"
    assert not re.search(r'<(link|img)[^>]+href="https?://', page), \
        "页面引用了外部资源"
    # 只允许一个 script 标签，且必须是 JSON 岛（数据，不是可执行代码）
    scripts = re.findall(r"<script\b[^>]*>", page)
    assert scripts == ['<script type="application/json" id="aplus-progress-snapshot">'], \
        "页面里的 script 标签不止 JSON 岛一个：%r" % (scripts,)


# ── 2. 每个数都指到产物路径 ────────────────────────────────────────

def test_every_displayed_number_points_to_an_artifact_path(exported):
    """屏上每个读数都要有一条 `.gm-src` 出处行。"""
    page = exported["page"]
    rendered = set(re.findall(r'<span class="gm-src">(.*?)</span>', page, re.S))
    rendered = {html.unescape(r) for r in rendered}
    missing = []
    for m in _all_metrics(exported["model"]):
        src = m.get("source") or ""
        if not src:
            missing.append("<%s 没有 source>" % m.get("key"))
            continue
        if html.escape(src, quote=False) not in rendered:
            missing.append("%s → %s" % (m.get("key"), src))
    assert not missing, "这些读数在页面上没有出处行：%s" % missing


def test_the_page_shows_the_source_path_of_every_metric_not_only_some(exported):
    """正控：出处行**真的**在页面上（不是「一条都没有」也判绿）。"""
    page = exported["page"]
    n_src = len(re.findall(r'<span class="gm-src">', page))
    n_metrics = len(_all_metrics(exported["model"]))
    assert n_metrics > 0, "模型里一个读数都没有 ⇒ 夹具坏了"
    assert n_src >= n_metrics, \
        "出处行 %d 条 < 读数 %d 条 ⇒ 有读数被渲染了但没有出处" % (n_src, n_metrics)


# ── 3/4/5. 三条「屏上不许」 ───────────────────────────────────────

def test_the_page_never_claims_the_target_is_achieved(exported):
    page = exported["page"]
    assert "已达成 20%" not in page
    assert "已达成20%" not in page
    assert "已达到 20%" not in page
    assert "已实现 20%" not in page


def test_the_page_never_carries_the_two_banned_field_names(exported):
    """那两个字段**名字与内容相反**（口径台账封禁）⇒ 屏上一个字都不许有。

    ⚠️ 产物自己的诚实边界第 7 条**逐字写着这两个词**
    ⇒ 导出器做**机械替换**（不改写句子），本页只检查**替换真的发生了**。
    """
    page = exported["page"]
    for token in ("投入率", "闲置比例"):
        assert token not in page, "屏上出现了被封禁的字段名：%s" % token
    assert "⟨部署比例字段" in page, "封禁占位符不在页面上 ⇒ 替换机制坏了"
    assert "⟨闲置现金字段" in page, "封禁占位符不在页面上 ⇒ 替换机制坏了"


def test_the_page_never_carries_the_cross_caliber_multiple(exported):
    """「还差几倍」只取同口径那一列（台账自己标 `is_the_gap = false` 的那列不许显示）。"""
    page = exported["page"]
    assert CROSS_CALIBER_DISPLAY not in page
    values = set()
    for tok in re.findall(r"-?\d+(?:\.\d+)?", page):
        try:
            values.add(float(tok))
        except ValueError:
            pass
    assert CROSS_CALIBER_VALUE not in values
    # ⚠️ 合法读数 U = 22.3554% 的显示串里含子串 "2.35" ⇒ 每一处 "2.35" 都得是它的一部分
    assert page.count("2.35") == page.count("22.3554"), \
        "页面里有一处 `2.35` 不属于合法读数 `22.3554`"


def test_the_page_shows_the_same_caliber_multiple_and_says_which_one(exported):
    """正控：同口径那一列**在**页面上，且明写它是缺口。"""
    page = exported["page"]
    g1 = exported["model"]["goals"][0]
    gm = g1["gap_multiple"]
    assert gm is not None, "目标 1 没有缺口倍数 ⇒ 快照结构变了"
    assert gm["metric"]["display"] in page, "同口径那个倍数没渲染"
    assert "is_the_gap = true" in page, "页面没写明取的是哪一列"


# ── 6. 快照时间 & 不许像实时 ──────────────────────────────────────

def _visible(page: str) -> str:
    """页面**看得见的部分**（把内联 JSON 岛切掉）。

    🚨 为什么必须切：JSON 岛里有**同一份模型**（含 `generated_at`）⇒
    「页面上有快照时间」这条断言会被**数据岛**满足，而读者看到的那一行
    可能已经没了 ⇒ 那是「找到了 ≠ 找对了」（第 143 层）。
    """
    marker = '<script type="application/json" id="aplus-progress-snapshot">'
    i = page.find(marker)
    assert i >= 0, "页面里没有 JSON 岛"
    return page[:i]


def test_the_page_states_the_snapshot_time(exported):
    page = exported["page"]
    model = exported["model"]
    assert model["generated_at"], "模型里没有快照时间"
    visible = _visible(page)
    assert model["generated_at"] in visible, \
        "**看得见的那部分**页面上没有快照时间（数据岛里有不算）"
    assert "快照时间" in visible, "看得见的页面上没有「快照时间」这几个字"


def test_the_page_does_not_look_like_a_live_dashboard(exported):
    """页面上的数**冻结在导出时刻** ⇒ 不许用实时数据的措辞。"""
    visible = _visible(exported["page"])
    assert "静态快照" in visible, "页面没写明自己是静态快照"
    for word in ("实时", "刚刚", "更新于", "每秒"):
        assert word not in visible, "页面用了实时数据的措辞：%s" % word


# ── 7/8/9/10. 部署面 ──────────────────────────────────────────────

def _code_lines(src: str) -> list[str]:
    """去掉**整行注释**后的代码行。

    ⚠️ 为什么要有这一步：注释里写「不许碰另一个服务」是**说明**，
    不是**行为**。若拿全文做子串判，注释会把它自己判死
    （第 13 层的形状：断言被文档满足）⇒ 判据要落在**会执行的那几行**上。
    ⚠️ 但**目录名**那一条用**全文**判（注释里也不许出现）⇒ 那是字面禁令。
    """
    return [l for l in src.splitlines() if not l.strip().startswith("#")]


def test_the_deploy_script_only_touches_its_own_directory():
    src = DEPLOY_SH.read_text(encoding="utf-8")
    assert "/opt/aplus-progress" in src, "推送脚本没打到自己那个目录"
    # 🚨 字面禁令：**全文**（含注释）都不许出现别的服务的目录名
    assert "/opt/wordson" not in src, "推送脚本里出现了别的服务的目录名"
    code = "\n".join(_code_lines(src))
    assert "wordson" not in code.lower(), "推送脚本的代码里提到了别的服务"
    # 🚨 目录由**一个变量**持有 ⇒ 判那个变量的值（判「同一行里出现字面量」
    #   是过严的守卫：变量展开后也是对的，会把人推向「改回字面量」）
    m = re.search(r'^REMOTE_DIR="([^"]+)"', code, re.M)
    assert m, "推送脚本里没有 REMOTE_DIR 变量"
    assert m.group(1) == "/opt/aplus-progress", \
        "REMOTE_DIR 不是我们自己那个目录：%s" % m.group(1)
    # 🚨 `--delete` 与 rsync 的落点**必须**是那个变量
    for line in code.splitlines():
        if "--delete" in line:
            assert "${REMOTE_DIR}" in line, \
                "`--delete` 打在了不是 REMOTE_DIR 的地方：%s" % line.strip()
    rsync_lines = [l for l in code.splitlines() if l.startswith("rsync ")]
    assert len(rsync_lines) == 1, "推送脚本里的 rsync 应该**恰好一条**：%r" % rsync_lines
    assert "${REMOTE_DIR}/" in rsync_lines[0], \
        "rsync 的落点不是 REMOTE_DIR：%s" % rsync_lines[0].strip()
    assert "${STATIC_DIR}/" in rsync_lines[0], \
        "rsync 的源不是本地静态页目录：%s" % rsync_lines[0].strip()


def test_the_deploy_script_does_not_background_itself():
    """后台化会让「已完成」变成假象（第 8 层）⇒ 同步跑完再退出。"""
    code = "\n".join(_code_lines(DEPLOY_SH.read_text(encoding="utf-8")))
    assert "nohup" not in code
    for line in code.splitlines():
        assert not re.search(r"&\s*$", line.strip()), \
            "推送脚本里有后台化：%s" % line.strip()


def test_the_systemd_unit_only_listens_on_8080():
    src = UNIT.read_text(encoding="utf-8")
    assert "/opt/wordson" not in src, "单元里出现了别的服务的目录名"
    code = "\n".join(_code_lines(src))
    assert "--port 8000" not in code and " 8000" not in code, "单元占用了 8000"
    assert "8080" in code, "单元没监听 8080"
    assert "wordson" not in code.lower(), "单元里提到了别的服务"


def test_the_systemd_unit_does_not_reuse_another_services_venv():
    src = UNIT.read_text(encoding="utf-8")
    assert "/opt/wordson/venv" not in src
    code = "\n".join(_code_lines(src))
    assert "/opt/wordson" not in code
    assert "/usr/bin/python3" in code, "单元没用系统 python3"
    # ⚠️ 机器上的 `python3` 是 3.6.8 ⇒ `http.server` 没有 `--directory`（3.7 才加）
    m = re.search(r"ExecStart=(\S+)", code)
    assert m, "单元没有 ExecStart"
    assert re.search(r"python3\.(7|8|9|1\d)\b", m.group(1)), \
        "ExecStart 用的 python 太老（3.6 没有 http.server --directory）：%s" % m.group(1)


# ── 11. 配色借的是 live 页的 ──────────────────────────────────────

def test_the_static_page_borrows_the_colors_from_the_live_page(exported):
    """静态页的 CSS 变量必须**逐项等于** live 页的 `:root` ⇒ 两份副本不会漂。

    ⚠️ 这一条同时钉住**提取器**：提取逻辑坏了 ⇒ `:root` 变空 ⇒ 变红
    （不是「页面还在」就当成功）。

    ⚠️ 比的是**变量表**（`--名字: 值`），不是整块原文 ——
       live 页的 `:root` 里有一大段注释，而导出器会**剥掉注释**
       （注释里有 `{`/`}` 会让 CSS 切分错位）⇒ 比原文会恒红。
    """
    page = exported["page"]
    live = LIVE_INDEX.read_text(encoding="utf-8")
    start = live.index("<style>")
    live_css = live[start + len("<style>"):live.index("</style>", start)]
    live_root = re.search(r":root\s*\{(.*?)\}", live_css, re.S)
    assert live_root, "live 页里找不到 :root 块"
    static_root = re.search(r":root\s*\{(.*?)\}", page, re.S)
    assert static_root, "静态页里找不到 :root 块"

    def vars_of(block: str) -> dict:
        out = {}
        for m in re.finditer(r"(--[A-Za-z0-9_-]+)\s*:\s*([^;]+);", block):
            out[m.group(1)] = re.sub(r"\s+", " ", m.group(2)).strip()
        return out

    live_vars = vars_of(live_root.group(1))
    static_vars = vars_of(static_root.group(1))
    assert live_vars, "live 页的 :root 里一个变量都没有 ⇒ 解析器坏了"
    assert static_vars == live_vars, \
        "静态页的 CSS 变量与 live 页不一致 ⇒ 差异：%s" % (
            {k: (live_vars.get(k), static_vars.get(k))
             for k in set(live_vars) | set(static_vars)
             if live_vars.get(k) != static_vars.get(k)},)


# ── 12. 8 条目标 ──────────────────────────────────────────────────

def test_all_eight_goals_are_on_the_page(exported):
    page = exported["page"]
    model = exported["model"]
    assert len(model["goals"]) == 8, "模型里目标数不是 8 ⇒ 快照结构变了"
    for g in model["goals"]:
        assert g["title"] in page, "目标 %s 的标题不在页面上" % g["id"]
        assert g["status_label"] in page, "目标 %s 的状态标签不在页面上" % g["id"]


def test_the_two_d2_calibers_are_side_by_side(exported):
    """D2 两个口径**并排**（结构给的，不靠读者自己对）。"""
    page = exported["page"]
    g1 = exported["model"]["goals"][0]
    assert len(g1["calibers"]) == 2, "D2 不是两个口径"
    for c in g1["calibers"]:
        assert c["name"] in page
    assert page.count('class="goal-calibers"') >= 1, "并排容器不在页面上"
    assert "grid-template-columns: 1fr 1fr" in page, "并排不是两列布局"


# ── 13/14. 导出器自己的拒收 ───────────────────────────────────────

def _run_exporter(args):
    return subprocess.run([sys.executable, str(EXPORTER)] + args,
                          cwd=str(ROOT), capture_output=True, text=True)


def test_the_exporter_refuses_to_export_a_metric_without_a_source(tmp_path):
    """没有出处的读数 ⇒ 拒绝导出（不是渲染一个指不到路径的数）。"""
    snap = json.loads(
        (REPORTS / "goal_progress_2026-10-21.json").read_text(encoding="utf-8"))
    m = snap["goals"][0]["metrics"][0]
    m["at"] = None
    m["sources"] = []
    bad = tmp_path / "goal_progress_2026-10-21.json"
    bad.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "index.html"
    r = _run_exporter(["--snapshot", str(bad), "--out", str(out),
                       "--reports-dir", str(tmp_path)])
    assert r.returncode != 0, "没有出处的读数竟然导出成功了"
    assert "出处" in (r.stderr + r.stdout), "拒收时没有说清原因"


def test_the_exporter_refuses_when_the_gap_multiple_column_is_ambiguous(tmp_path):
    """缺口倍数那一列**恰好要一列** ⇒ 0 列或 2 列都拒绝导出。"""
    snap = json.loads(
        (REPORTS / "goal_progress_2026-10-21.json").read_text(encoding="utf-8"))
    cols = snap["goals"][0]["multiples"]["columns"]
    for col in cols:
        col["is_the_gap"] = True          # 两列都成缺口 ⇒ 口径已经乱了
    bad = tmp_path / "goal_progress_2026-10-21.json"
    bad.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "index.html"
    r = _run_exporter(["--snapshot", str(bad), "--out", str(out),
                       "--reports-dir", str(tmp_path)])
    assert r.returncode != 0, "倍数那一列不唯一竟然导出成功了"
    assert not out.exists(), "拒收了却还是落了盘"


def test_the_exporter_refuses_when_the_snapshot_has_no_goals(tmp_path):
    bad = tmp_path / "goal_progress_2026-10-21.json"
    bad.write_text(json.dumps({"schema": "x"}), encoding="utf-8")
    r = _run_exporter(["--snapshot", str(bad), "--out", str(tmp_path / "i.html"),
                       "--reports-dir", str(tmp_path)])
    assert r.returncode != 0


# ── 15. 内联 JSON 是子集 ──────────────────────────────────────────

def test_the_inlined_json_is_a_subset_and_holds_no_cross_caliber_column(exported):
    model = exported["model"]
    blob = json.dumps(model, ensure_ascii=False)
    assert CROSS_CALIBER_DISPLAY not in blob
    assert "cross_caliber" not in blob, "跨口径那一列还在 JSON 岛里"
    assert model["goals"][0]["gap_multiple"] is not None


# ── 16. 调度 ──────────────────────────────────────────────────────
#
# 🚨 **为什么用 launchd 而不是 crontab**（2026-10-21 实测）：
#    这台 Mac 的 `crontab` **写入**被系统沙箱挡住，内核日志逐字：
#       (Sandbox) System Policy: crontab(NNNN) deny(1) file-write-create
#       /private/var/at/tmp/tmp.NNNN
#    ⇒ `crontab -l`（读）正常；`crontab <file>` / `crontab -`（写）一律失败
#    ⇒ 任何从 crontab 出发的定时任务**都装不上去**
#    ⇒ 实际生效的是 launchd（本仓已在用：`com.aplus.daily` 等 8 个）。
#    ⇒ 同一份调度的 **crontab 版本**留在 `deploy/aplus_crontab.txt`，
#      机器的 crontab 一旦能写，`crontab deploy/aplus_crontab.txt` 即可。

LAUNCHD_AGENTS = {
    # 名字 → (仓内 plist, (时, 分), Weekday 或 None)
    # 🚨 launchd 的 `Weekday`：**0=周日, 1=周一** ⇒ 写 0 会变成每周日。
    "com.aplus.g4refresh": (ROOT / "deploy" / "com.aplus.g4refresh.plist", (21, 30), None),
    "com.aplus.dailyreview": (ROOT / "deploy" / "com.aplus.dailyreview.plist", (21, 40), None),
    "com.aplus.goalprogress": (ROOT / "deploy" / "com.aplus.goalprogress.plist", (22, 5), None),
    "com.aplus.progresspage": (ROOT / "deploy" / "com.aplus.progresspage.plist", (22, 25), None),
    "com.aplus.dailyreviewweekly": (ROOT / "deploy" / "com.aplus.dailyreviewweekly.plist",
                                     (8, 5), 1),
}
AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"


def _plist_int(plist: str, key: str) -> int:
    m = re.search(r"<key>%s</key>\s*<integer>(\d+)</integer>" % key, plist)
    assert m, "plist 里没有 %s" % key
    return int(m.group(1))


@pytest.mark.parametrize("label", sorted(LAUNCHD_AGENTS))
def test_the_schedule_agent_is_declared_at_the_right_hour_and_minute(label):
    """定时任务的**时刻**必须钉死（挪了时刻 = 读到的产物不是当天的）。"""
    path, (hour, minute), _weekday = LAUNCHD_AGENTS[label]
    assert path.is_file(), "仓里没有 %s" % path
    plist = path.read_text(encoding="utf-8")
    assert _plist_int(plist, "Hour") == hour
    assert _plist_int(plist, "Minute") == minute


@pytest.mark.parametrize("label", sorted(LAUNCHD_AGENTS))
def test_the_weekly_agent_uses_weekday_one_not_zero(label):
    """🚨 `Weekday` = **0=周日, 1=周一** ⇒ 写 0 会变成**每周日**。

    这是一个**不会立刻报错**的错（它照样会跑，只是跑错那天）⇒ 必须钉死。
    """
    path, _hm, weekday = LAUNCHD_AGENTS[label]
    plist = path.read_text(encoding="utf-8")
    if weekday is None:
        assert "<key>Weekday</key>" not in plist, \
            "%s 不是每周任务，不许带 Weekday" % label
        return
    assert _plist_int(plist, "Weekday") == weekday == 1, \
        "%s 的 Weekday 必须是 1（周一），不是 0（周日）" % label


def test_the_nightly_chain_is_ordered_by_time_not_by_after():
    """21:30 G4 → 21:40 复盘 → 22:05 快照 → 22:25 导出推送。

    🚨 **不许用 `After=` 串**：launchd 的 `After=` 只保证**启动**顺序、
    不保证**完成**顺序 ⇒ 用它串链会造出「以为串好了其实并发」的假象。
    ⇒ 依赖顺序必须**只**靠时间错开。
    """
    order = ["com.aplus.g4refresh", "com.aplus.dailyreview",
             "com.aplus.goalprogress", "com.aplus.progresspage"]
    times = []
    for label in order:
        path, (h, m), _ = LAUNCHD_AGENTS[label]
        times.append((h, m, label))
    for a, b in zip(times, times[1:]):
        assert a[:2] < b[:2], \
            "晚间链的顺序错了：%s(%02d:%02d) 必须早于 %s(%02d:%02d)" % (
                a[2], a[0], a[1], b[2], b[0], b[1])
    for label in order:
        path, _hm, _ = LAUNCHD_AGENTS[label]
        plist = path.read_text(encoding="utf-8")
        assert "<key>After</key>" not in plist, \
            "%s 不许用 After= 串依赖（它只保证启动顺序，不保证完成顺序）" % label


@pytest.mark.parametrize("label", sorted(LAUNCHD_AGENTS))
def test_the_installed_agent_is_byte_identical_to_the_one_in_the_repo(label):
    """装了的那份必须**逐位等于**仓里那份 ⇒ 两份副本不会漂。"""
    path, _hm, _weekday = LAUNCHD_AGENTS[label]
    installed = AGENTS_DIR / ("%s.plist" % label)
    if not installed.is_file():
        pytest.skip("%s 没装到 %s" % (label, AGENTS_DIR))
    assert installed.read_bytes() == path.read_bytes(), \
        "%s 装的那份与仓里那份不一致 ⇒ 两份副本漂了" % label


def test_the_crontab_form_is_kept_and_the_existing_lines_are_untouched():
    """crontab 版本留在仓里；**已有那三条一个字都不许改**。"""
    text = (ROOT / "deploy" / "aplus_crontab.txt").read_text(encoding="utf-8")
    codes = [l for l in text.splitlines() if l.strip() and not l.strip().startswith("#")]
    # 🚨 已有那三条**逐字**保留（不许改时刻、不许改命令）
    assert any(l.startswith("40 21 * * * ") and "daily_review.py" in l
               and "review_" not in l for l in codes), "21:40 夜间复盘那条不见了"
    assert any(l.startswith("5 8 * * 1 ") and "review_weekly" in l for l in codes), \
        "08:05 周复盘那条不见了"
    assert any(l.startswith("5 22 * * * ") and "measure_goal_progress.py" in l
               for l in codes), "22:05 目标进度快照那条不见了"
    # 🚨 新增那两条
    g4 = [l for l in codes if "score_ranking_power.py" in l]
    assert len(g4) == 1, "G4 刷新那条应该**恰好一条**：%r" % g4
    push = [l for l in codes
            if "export_progress_page.py" in l and "deploy_progress_page.sh" in l]
    assert len(push) == 1, "静态页推送那条应该**恰好一条**：%r" % push
    # 🚨 不许后台化
    for line in codes:
        assert "nohup" not in line, "cron 里不许 nohup"
        assert not re.search(r"&\s*$", line.strip()), "cron 里不许后台化：%r" % line
    # 🚨 时刻
    m = re.match(r"^(\d+)\s+(\d+)\s+\*\s+\*\s+\*", push[0].strip())
    assert m, "推送那行解析不出来：%r" % push[0]
    assert (int(m.group(2)), int(m.group(1))) > (22, 5)


def test_the_crontab_file_says_the_two_schedules_are_mutually_exclusive():
    """🚨 **双跑**是这类改法最常见的坑 ⇒ 文件头必须写明「二选一」。

    守卫盯的是**那一句在不在**，不是「人记不记得」——判据挂在人的记忆上
    等于没有判据（第 124 层）。
    """
    text = (ROOT / "deploy" / "aplus_crontab.txt").read_text(encoding="utf-8")
    assert "二选一" in text, "crontab 文件头没写「二选一」"
    assert "unload" in text, "crontab 文件头没写装之前要先 unload launchd 单元"
    assert "仅作参考" in text, "crontab 文件头没写「本文件仅作参考」"
    # 🚨 五个 launchd 单元名都要在文件里点到（装之前要 unload 的清单）
    for label in sorted(LAUNCHD_AGENTS):
        assert label in text, "crontab 文件头没提 %s ⇒ 装之前会漏 unload" % label


# ══════════════════════════════════════════════════════════════════
# 17~26 · 可读性守卫（2026-10-21 第二轮：把「一堵墙」改成「一眼看完的进度页」）
# ══════════════════════════════════════════════════════════════════
#
# 🚨 **这一段守的是「屏上长什么样」，不是「数对不对」**。
#    「数对不对」是上面 1~16 那些条的事。
#
# ## 背景（实测）
#    改前：1440×900 下整页 **7317px**（≈ 8 屏）⇒ 用户打开看到的是一堵墙。
#    改后：**1552px**（≈ 1.7 屏），**信息一个字没少**（折叠区展开后逐字相同）。
#
# ## 为什么「首屏必含」在**结构层**判而不在像素层判
#    像素（y < 1200）随字体/视口变 ⇒ 那是**第二层**（浏览器级，见
#    `tests/e2e/test_progress_page_browser.py`）。
#    这一层判的是**结构**：两个口径**不在任何 `<details>` 里** ⇒
#    它们在**任何**视口、任何字体下都在首屏。
#
# ## 阈值从哪来（不许拍脑袋）
#    - 「首屏 1200px」：需求给的数（「例如前 1200px」）。
#    - 「总高 ≤ 4 屏」：3 屏给**主结论**（需求给的理由：「用户在手机上
#      3 次滑动内看完主要结论」）＋ 1 屏给 8 条目标的读数行
#      （每行**必须**带判据 ⇒ 8 行在 390px 宽的手机上实测要 1.9 屏
#      ⇒ 「≤3 屏」在手机上**结构上不可达**，差 379px）。
#      这两个数是**两个量**（第 7 层：不许拿一个冒充另一个）⇒ 分开判。


def _details_regions(page: str) -> list[str]:
    """页面里每个 `<details>` 的**内部文本**（按开闭配对，不是贪婪正则）。

    ⚠️ 用配对而不是 `re.findall(r"<details.*?</details>")` ——
    将来若出现**嵌套** details，贪婪/非贪婪正则都会数错块数。
    """
    out = []
    stack = []
    i = 0
    n = len(page)
    while i < n:
        j = page.find("<details", i)
        k = page.find("</details>", i)
        if j == -1 and k == -1:
            break
        if j != -1 and (k == -1 or j < k):
            stack.append(j)
            i = j + len("<details")
        else:
            start = stack.pop() if stack else 0
            out.append(page[start:k])
            i = k + len("</details>")
    return out


def _text_of(frag: str) -> str:
    """把一段 HTML 变成它的**纯文本**（去标签、解实体、压空白）。"""
    t = re.sub(r"<[^>]+>", " ", frag)
    t = html.unescape(t)
    return re.sub(r"\s+", " ", t).strip()


def _strip_md(frag: str) -> str:
    """比**读数的值**时的归一化：去标签、去 Markdown 记号。

    🚨 为什么：渲染层把 `**加粗**` 变成 `<b>加粗</b>`、把 `` `code` `` 变成
    `<code>code</code>` ⇒ 屏上那个字符串与快照里的原文**逐位不同**
    ⇒ 但它们**是同一个读数**（第 97 层：名字/字面量不承诺语义，但**值**承诺）。
    ⇒ 这一条比的是「这个值是不是快照里那个值」，不是「这两个串一不一样」。
    """
    t = re.sub(r"<[^>]+>", "", str(frag))
    return t.replace("**", "").replace("`", "").strip()


def _plain(frag: str) -> str:
    """**比内容用**的归一化：去标签、去实体、去 Markdown 记号、去所有空白。

    🚨 为什么**连空白一起去**：
       产物里的 `**粗体**` 在页面上变成 `<b>粗体</b>` ⇒ 两边逐字比**必然不等**
       （标签两边不同、空白两边也不同）⇒ 那样的比较会**恒红**，
       等于一条恒红的守卫（它会把人推向「改本来正确的渲染」）。
    ⇒ 比较的量是「**这句话在不在**」，不是「这两个字节串一不一样」。
    """
    t = re.sub(r"<[^>]+>", "", str(frag))
    t = html.unescape(t)
    t = t.replace("**", "").replace("`", "")
    return re.sub(r"\s+", "", t)


# ── 17. 首屏：两个口径**不在任何折叠区里** ────────────────────────

def test_the_two_d2_calibers_are_not_inside_any_collapsed_region(exported):
    """两个口径必须**常驻可见**（不许被折进去）。

    🚨 判的是**结构**（不在 `<details>` 里），不是像素 ⇒ 与视口/字体无关。
    """
    page = exported["page"]
    d2 = exported["model"]["goals"][0]
    assert len(d2["calibers"]) == 2
    # 每一档都必须**落在某个 details 之外**：逐个 details 的区间取补集。
    regions = _details_regions(page)
    card_starts = [m.start() for m in re.finditer(
        r'<div class="d2c(?![-\w])', page)]
    assert len(card_starts) >= 2, "页面上找不到两个口径卡片"
    # 每个口径卡片的**起点**必须不在任何 details 区间内。
    for start in card_starts[:2]:
        for region in regions:
            r_start = page.find(region)
            assert not (r_start <= start < r_start + len(region)), \
                "有一个口径卡片落在折叠区里 ⇒ 首屏看不到它"


def test_the_d2_block_is_the_first_thing_in_main(exported):
    """D2 那块必须是 `<main>` 的**第一块**（首屏的第一件东西）。"""
    page = exported["page"]
    main_i = page.find("<main>")
    assert main_i >= 0
    first_section = page.find("<section", main_i)
    head = page[first_section:first_section + 400]
    assert "D2" in head, "main 的第一块不是 D2 那块"


# ── 18. 8 条目标每条都有状态标签 ──────────────────────────────────

def test_every_goal_has_its_own_status_label_in_the_compact_row(exported):
    """每条目标的紧凑行**必须**带自己的状态标签（不许变成散文段落）。"""
    page = exported["page"]
    model = exported["model"]
    labels = re.findall(
        r'<details class="goal-fold"><summary class="goal-line">(.*?)</summary>',
        page, re.S)
    assert len(labels) == 8, "紧凑读数行不是 8 条：%d" % len(labels)
    for g, frag in zip(model["goals"], labels):
        want = g.get("status_label") or g.get("status")
        got = re.findall(r'<span class="goal-status[^"]*">(.*?)</span>', frag, re.S)
        assert got == [want], \
            "目标 %s 的状态标签不对：页面上 %r，快照里 %r" % (g["id"], got, want)
        # 🚨 状态不许被改写成「未达 / 接近」这类**词**（第 16 层）
        for bad in ("未达", "接近", "差一点", "未达成"):
            assert bad not in "".join(got), \
                "目标 %s 的状态标签里出现了 %r" % (g["id"], bad)


def test_every_compact_row_carries_readings_and_not_just_prose(exported):
    """紧凑行里必须有**读数对**（标签 + 值）⇒ 它是读数行，不是散文。"""
    page = exported["page"]
    rows = re.findall(
        r'<details class="goal-fold"><summary class="goal-line">(.*?)</summary>',
        page, re.S)
    for i, frag in enumerate(rows, 1):
        reads = re.findall(
            r'<span class="gl-read"><span class="gl-lab">(.*?)</span>'
            r'<span class="gl-val">(.*?)</span></span>', frag, re.S)
        assert reads, "第 %d 条目标的紧凑行里一个读数都没有" % i
        for lab, val in reads:
            assert lab.strip(), "第 %d 条目标有个读数没有标签" % i
            assert val.strip(), "第 %d 条目标有个读数没有值" % i
        # 🚨 散文段落（`goal-reason`）**不许**进紧凑行
        assert "goal-reason" not in frag, \
            "第 %d 条目标的紧凑行里混进了散文段落" % i


def test_the_readings_in_the_compact_rows_are_snapshot_values_verbatim(exported):
    """紧凑行里的**每一个值**都必须是快照里的读数 ⇒ 导出器不许手打数。

    🚨 这是「⛔ 不许改任何数字」那条禁令在**渲染层**的守卫。
    """
    page = exported["page"]
    model = exported["model"]
    allowed = set()
    for m in _all_metrics(model):
        if m.get("display") is not None:
            allowed.add(_strip_md(str(m["display"])))
    for g in model["goals"]:
        for r in ((g.get("acceptance") or {}).get("rows") or []):
            if r.get("display") is not None:
                allowed.add(_strip_md(str(r["display"])))
    allowed.add("缺失")
    shown = re.findall(r'<span class="gl-val">(.*?)</span>', page, re.S)
    assert shown, "页面上一个紧凑读数都没有 ⇒ 守卫坏了"
    bad = [v for v in shown if _strip_md(html.unescape(v)) not in allowed]
    assert not bad, "紧凑行里出现了不是快照读数的值：%r" % bad


def test_the_d2_headline_numbers_are_snapshot_values_verbatim(exported):
    """首屏两个口径的**主读数**也必须是快照里的值（不许手打）。"""
    page = exported["page"]
    model = exported["model"]
    allowed = {_strip_md(str(m["display"])) for m in _all_metrics(model)
               if m.get("display") is not None}
    allowed.add("缺失")
    nums = re.findall(r'<span class="d2c-num">(.*?)</span>', page, re.S)
    assert len(nums) >= 2, "首屏两个口径的主读数不在"
    bad = [v for v in nums if _strip_md(html.unescape(v)) not in allowed]
    assert not bad, "首屏出现了不是快照读数的主读数：%r" % bad


# ── 19. 每个口径各带自己的文字标签与出处路径 ──────────────────────

def test_each_d2_caliber_carries_its_own_label_and_its_own_source(exported):
    """两档**各自**带标签与出处路径（不许只显示一档，或两档共用一个来源）。"""
    page = exported["page"]
    d2 = exported["model"]["goals"][0]
    cards = re.findall(r'<div class="d2c(?![-\w])(.*?)</div></div>', page, re.S)
    assert len(cards) >= 2
    for c in d2["calibers"]:
        assert c["name"] in _text_of(page), "口径 %r 的标签不在页面上" % c["name"]
        assert c["source"] in page, "口径 %r 的出处路径不在页面上" % c["name"]
    # 两档的出处路径**必须不同**（不同产物 ⇒ 不同路径）
    srcs = [c["source"] for c in d2["calibers"]]
    assert len(set(srcs)) == len(srcs), "两个口径的出处路径相同 ⇒ 可能拿错了档"


# ── 20. 出处行是「次级」的（折叠区里），但**一个都没少** ──────────

def test_every_reading_keeps_its_source_line_inside_a_fold(exported):
    """每个读数的出处行都在**折叠区**里（次级），但**一个都没少**。

    🚨 需求：「每个读数的出处行 ⇒ 折叠/次级，但一个字不许少」。
    """
    page = exported["page"]
    model = exported["model"]
    fold_texts = [_text_of(r) for r in _details_regions(page)]
    assert fold_texts, "页面上一个折叠区都没有 ⇒ 守卫坏了"
    missing = []
    for m in _all_metrics(model):
        src = (m.get("source") or "").strip()
        if not src:
            missing.append("<%s 没有 source>" % m.get("key"))
            continue
        if not any(src in t for t in fold_texts):
            missing.append("%s → %s" % (m.get("key"), src))
    assert not missing, "这些读数的出处行不在任何折叠区里：%s" % missing


# ── 21. 折叠区里的内容**一个字不少** ──────────────────────────────

def test_the_collapsed_sections_keep_every_word_of_the_snapshot(exported):
    """时间线 / 9 条诚实边界 / 每条读数与出处 ⇒ 折叠区里**逐字**都在。

    🚨 这一条是「不许靠删信息来实现」的守卫：把页面**缩短**而不丢信息，
    唯一合法的做法是**折叠**，不是**删除**。
    """
    page = exported["page"]
    model = exported["model"]
    page_text = _plain(page)
    missing = []
    for i, b in enumerate(model["honest_boundaries"], 1):
        if _plain(str(b)) not in page_text:
            missing.append("诚实边界 #%d" % i)
    for h in model["timeline"]:
        for field in ("summary_line", "change_line"):
            v = h.get(field)
            if v and _plain(str(v)) not in page_text:
                missing.append("时间线 %s 的 %s" % (h["date"], field))
    for m in _all_metrics(model):
        for field in ("label", "display", "caliber", "note"):
            v = m.get(field)
            if v and _plain(str(v)) not in page_text:
                missing.append("读数 %s 的 %s" % (m.get("key"), field))
    for g in model["goals"]:
        for field in ("title", "reason", "criterion", "honest_boundary"):
            v = g.get(field)
            if v and _plain(str(v)) not in page_text:
                missing.append("目标 %s 的 %s" % (g["id"], field))
    assert not missing, "页面上少了这些内容：%s" % missing


# ── 22. 不许用截断把内容藏起来 ────────────────────────────────────

def test_the_page_never_truncates_content_with_ellipsis(exported):
    """⛔ 不许 `text-overflow: ellipsis` / `-webkit-line-clamp`。

    理由（活页那条纪律）：被截掉的正是**结论本身** ⇒ 放得下或可横滚。
    """
    page = exported["page"]
    style = page[page.find("<style"):page.find("</style>")]
    assert "ellipsis" not in style, "页面的 CSS 里出现了 ellipsis 截断"
    assert "-webkit-line-clamp" not in style, "页面的 CSS 里出现了行数截断"


# ── 23. 静态页不含 API ⇒ 数据冻结在导出时刻（这条**必须**写明）──

def test_the_visible_page_says_the_data_is_frozen_at_the_snapshot_time(exported):
    """数据冻结这件事**必须**写在**看得见的**地方（不许只写在折叠区里）。

    🚨 静态页**没有 API** ⇒ 刷新不会得到新数 ⇒ 读者若不知道这一点，
    就会把昨天的数当成今天的（现有第 6 条只查「快照时间」在不在，
    这里查的是「**冻结**这件事有没有被说清楚」）。
    """
    visible = _visible(exported["page"])
    assert "静态快照" in visible
    assert "冻结" in visible, "看得见的地方没写「冻结」⇒ 读者会把旧数当成新数"
    assert exported["model"]["generated_at"] in visible


# ── 24. 紧凑读数行的条数是**写死的**（不许悄悄变） ───────────────

def test_the_compact_row_reading_budget_is_a_named_constant(exported):
    """紧凑行最多显示几个读数 ⇒ 必须是**模块级常量**，不许散在 CSS 里。

    🚨 「3」是**排版常数**（不是判决门槛），但它必须**只有一份**：
    两处各改一次 ⇒ 一处改了另一处没改 ⇒ 页面自己跟自己的规则打架。
    """
    mod = _load_exporter()
    assert hasattr(mod, "COMPACT_READINGS_MAX")
    n = mod.COMPACT_READINGS_MAX
    assert isinstance(n, int) and n >= 1, "紧凑读数上限不是正整数：%r" % n
    # 正文里不许再出现第二个「取前 N 个」的魔数
    src = EXPORTER.read_text(encoding="utf-8")
    code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
    assert code.count("COMPACT_READINGS_MAX") >= 2, \
        "紧凑读数上限只在一处被用 ⇒ 那个「一处」就是它自己的定义"


# ── 25. 「测不出」不许被写成「未达 / 接近」 ────────────────────────

def test_the_conclusion_and_the_status_labels_never_say_not_reached_or_close(
        exported):
    """「不可判定（测不出）」不许被写成「未达 / 接近 / 差一点」。

    ⚠️ **只查状态标签与结论行**，不查全文 ——
    产物自己的**判据原文**里逐字写着「c ≥ T ⇒ 达成；U < T ⇒ 未达」
    （那是四条穷举里的一条，**不是**对当前状态的结论）
    ⇒ 全文查「未达」会把一条**正确的**渲染判死
    （第 112 层：过严的守卫和恒真的守卫一样有害）。
    """
    page = exported["page"]
    concl = re.search(r'<div class="static-conclusion">(.*?)</div>', page, re.S)
    assert concl, "页面上没有一句话结论"
    texts = [html.unescape(concl.group(1))]
    texts += [html.unescape(t) for t in
              re.findall(r'<span class="goal-status[^"]*">(.*?)</span>', page, re.S)]
    assert texts, "一个状态/结论都没抓到 ⇒ 守卫坏了"
    for t in texts:
        for bad in ("未达", "未达成", "接近", "差一点", "已达成"):
            assert bad not in t, "屏上出现了 %r：%r" % (bad, t)


# ── 26. 首屏那块**必须**在页面最前面（不许被推到下面） ────────────

def test_the_d2_block_is_rendered_before_every_other_section(exported):
    """D2 那一块在 `<main>` 里的位置**必须**在其它 `<section>` 之前。

    ⚠️ 这是「不许把首屏那块挪到页面底部」那条禁令的守卫 ——
    结构性的（不依赖像素）。
    """
    page = exported["page"]
    main_i = page.find("<main>")
    assert main_i >= 0
    first = page.find("<section", main_i)
    assert first >= 0
    head = page[first:first + 300]
    assert "D2" in head, "main 的第一块不是 D2 那块"
    # 目标那 8 行必须**在** D2 块之后（读者先看结论，再看明细）
    # ⚠️ 锚的是 **`<h2>8 条目标</h2>`**（标题标签），不是裸字符串 ——
    #    裸字符串会命中 CSS 注释（实测 STATIC_CSS 里就有一处）⇒ 第 143 层。
    goals_i = page.find("<h2>8 条目标</h2>")
    assert goals_i > 0, "页面上找不到「8 条目标」那个标题"
    assert goals_i > first, "「8 条目标」那一块跑到 D2 前面去了"


# ── 27. 屏上不许出现**裸的 Markdown 记号**（活页 B8 的教训） ────────

def test_the_visible_page_never_shows_raw_markdown_markers(exported):
    """看得见的部分里，`**加粗**` 与 `` `行内代码` `` 都不许**原样**出现。

    🚨 活页 B8：目标进度那段**原样打印 Markdown** ⇒ 屏上实测 143 处裸星号
    与 118 处裸引号符号 ⇒ 读者看到的是**记号**而不是内容。
    ⇒ 导出页必须把它们渲染成 `<b>` / `<code>`。

    ⚠️ 判的是**看得见的部分**（切掉 JSON 岛）：JSON 岛是**数据**，
    它的原始字符串就该带 Markdown 记号（那是快照原文）。
    """
    visible = _visible(exported["page"])
    assert "**" not in visible, \
        "看得见的部分还有 %d 处裸 `**` ⇒ 屏幕在打印 Markdown 记号" % visible.count("**")
    assert "`" not in visible, \
        "看得见的部分还有 %d 处裸反引号 ⇒ 屏幕在打印 Markdown 记号" % visible.count("`")
    # 正控：确实渲染了（不是「一条都没渲染」也判绿）
    assert "<b>" in visible, "页面里一个加粗都没渲染 ⇒ 判据可能坏了"
    assert "<code>" in visible, "页面里一个行内代码都没渲染 ⇒ 判据可能坏了"

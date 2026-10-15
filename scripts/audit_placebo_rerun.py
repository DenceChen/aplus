"""核对 `placebo_control.json` 重跑是否兑现**跑之前**写下的预测。

## 为什么这个脚本存在（而不是我跑一次 heredoc 就完）

第 134 层：只跑过一次、没落盘的探针，它的输出是**传闻**。
而这次核对要支撑的是**承重判决**——`PLACEBO_NULL` 是
「因子有效」这句话在它跑完前一个字都不能写的前提。

⇒ 探针必须落成资产：任何人、任何一天，跑同一条命令得到同一个判决。

## 判据的外部锚

三条预测的数值来自 `docs/TODO_placebo_control_2026-10-15.md` 里的
`ANCHOR:placebo_baseline` 块，而那个块**从 git HEAD 读**：

| 读哪个版本 | 后果 |
|---|---|
| **git HEAD** | 锚是冻结的；跑完再挪锚，`tests/test_placebo_rerun.py` 当场红 |
| 磁盘上的工作区 | **自己判自己**——改了产物顺手改锚，判决永远绿 |

⚠️ 因此本脚本**不许**直接 `open(文档路径)` 取锚。

## 逐位比较必须用 `float.hex()`

`t_blocked` 的预测是「**逐位相同**」。而 `==` 有两处会放行假通过：

1. `nan == nan` 是 `False` —— 真相同反而被判不同；
2. `0.0 == -0.0` 是 `True` —— 位模式不同（符号位）却判相同。

⇒ 判决量是 `float.hex()`，它比的是**位模式**，与 `==` 的语义无关。
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aplus.provenance import freshness  # noqa: E402

_ROOT = Path(__file__).resolve().parents[1]
DOC = "docs/TODO_placebo_control_2026-10-15.md"
REPORT = "data/reports/placebo_control.json"
SIDECAR = REPORT + ".provenance.json"

_ANCHOR_KEYS = ("t_blocked", "p0_t_blocked", "n_days", "window_end", "block")


def anchor_from_git_head() -> dict:
    """从 **git HEAD** 版文档里读外部锚。读工作区会变成「自己判自己」。

    ⚠️ `git -C _ROOT` 不是多余的：裸 `git show` 只在**当前目录是仓库里**时成立，
    于是脚本换个 cwd 就炸——而「换个地方跑就跑不起来」正是形状 24 那个洞。
    """
    r = subprocess.run(["git", "-C", str(_ROOT), "show", f"HEAD:{DOC}"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(
            f"🚨 读不到 git HEAD 版 {DOC}：{r.stderr.strip()}\n"
            "  ⇒ 锚是这三条预测的唯一数值来源；没有它，脚本只能写字面量。")
    raw = r.stdout
    m = re.search(r"<!--\s*ANCHOR:placebo_baseline(.*?)-->", raw, re.S)
    if not m:
        raise SystemExit(
            f"🚨 git HEAD 版 {DOC} 里没有 ANCHOR:placebo_baseline 块。\n"
            "  锚是这三条预测的唯一数值来源；没有它，脚本只能写字面量，\n"
            "  而字面量闸门是本仓反复出现过的失效形态。\n"
            "  ⇒ 先提交带锚块的文档，再跑本脚本。")
    out = {}
    for key in _ANCHOR_KEYS:
        mm = re.search(rf"^\s*{key}\s*=\s*(\S+)\s*$", m.group(1), re.M)
        if not mm:
            raise SystemExit(f"🚨 锚块里缺 `{key}` —— 锚必须完整，缺项不许用默认值顶上")
        raw_v = mm.group(1)
        out[key] = raw_v if key == "window_end" else (
            float(raw_v) if "." in raw_v or "e" in raw_v.lower() else int(raw_v))
    return out


BASELINE = "data/reports/placebo_control.json"
WIDTH_REPORT = "data/reports/factor_width_mde.json"

# ⚠️ 钉住**持有重跑前那份产物**的提交，不是 `HEAD`。
#    重跑产物一旦提交，`HEAD:` 就会指向新产物 ⇒ 基线跟着变 ⇒
#    「块数与旧窗口相同」这条机制判据**永远绿**。
#    锚是冻结的，基线也必须冻结在同一个意义上。
#    这不是「自己判自己」：它是一次外部引用，改它会在 git 里留痕，
#    且下面 + `tests/test_placebo_rerun.py` 会核对它与锚一致。
BASELINE_COMMIT = "d0a9cfcbf9f138496994dd3387c6ef7a7235a921"


def baseline_from_frozen_commit() -> dict:
    """从**钉住的那次提交**里读「旧窗口」的真值。

    ## 为什么不用锚块里的数字当「旧窗口」

    锚是我写下的承诺，旧产物是实际发生过的字节。两者**独立**才有验证意义；
    若把锚同时当成承诺和事实，就是自己判自己。

    ⚠️ 只读字段，不重算：块数直接取 `n_independent_obs`（= `_block_means`
    的返回长度），**不许**拿 `n_days // block` 反推——`n_days` 是
    `dropna()` 之前的长度，反推出来的是另一个量（第 135 层）。
    """
    r = subprocess.run(["git", "-C", str(_ROOT), "show",
                        f"{BASELINE_COMMIT}:{BASELINE}"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(
            f"🚨 读不到 {BASELINE_COMMIT[:7]} 版 {BASELINE}：{r.stderr.strip()}\n"
            "  「旧窗口」的真值必须来自已提交的字节；没有它就只剩我自己的承诺。")
    art = json.loads(r.stdout)
    run0 = art["arms"]["P0_observed"]["runs"][0]
    return {
        "commit": BASELINE_COMMIT,
        "window_end": art["window"][1],
        "n_days": int(run0["n_days"]),
        "n_blocks": int(run0["n_independent_obs"]),
        "t_blocked": float(run0["t_blocked"]),
        "p0_t_blocked": float(art["p0_t_blocked"]),
    }


def _show_at(ref: str, path: str) -> dict:
    r = subprocess.run(["git", "-C", str(_ROOT), "show", f"{ref}:{path}"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(
            f"🚨 读不到 {ref[:7]} 版 {path}：{r.stderr.strip()}\n"
            "  判决窗口的真值必须来自已提交的字节，不能是代码里的字面量"
            "（本仓反复出现过的失效形态）。")
    return json.loads(r.stdout)


def target_from_frozen_commit() -> dict:
    """判决窗口的真值来自**榜宽产物**——本次重跑的全部理由就是对齐它。

    ## 为什么不是 `== "2026-10-12"`

    那是**我**写下的字面量。它今天恰好等于榜宽产物的窗口，但没人保证它一直等于。
    一旦链改了窗口，代码里的日期不会跟着动 ⇒ 守卫报绿，而两者其实已经分家。

    ⇒ 判据改成「placebo 的窗口末端 == 榜宽产物的窗口末端」：
    这才是重跑的**目的**，且真值来自已提交的产物，不是我的记忆。

    ⚠️ `n_days` 取**所有档位的共识**：六个档位对同一天数达成一致，本身就是
    一条该有的检查；若某个档位天数不同，判决为「榜宽实验内部就不一致」。
    """
    art = _show_at(BASELINE_COMMIT, WIDTH_REPORT)
    counts = {int(v["n_days"]) for v in art["widths"].values() if "n_days" in v}
    return {
        "window_start": art["window"][0],
        "window_end": art["window"][1],
        "n_days": sorted(counts)[0] if len(counts) == 1 else None,
        "widths_agree": len(counts) == 1,
        "widths_seen": sorted(counts),
    }


def main() -> int:
    a = anchor_from_git_head()
    base = baseline_from_frozen_commit()
    tgt = target_from_frozen_commit()

    rp = _ROOT / REPORT
    if not rp.exists():
        print(f"🚨 产物不存在：{REPORT}")
        return 2
    art = json.loads(rp.read_text(encoding="utf-8"))

    # ★ 只读字段，禁止用公式重算「验证」（CLAUDE.md）
    run0 = art["arms"]["P0_observed"]["runs"][0]
    got = {
        "t_blocked": run0["t_blocked"],
        "p0_t_blocked": art["p0_t_blocked"],
        "n_days": run0["n_days"],
        "n_blocks": run0.get("n_independent_obs"),
        "window_end": art["window"][1],
    }

    # ★ 机制（2026-10-15 第 135 层修正）：`evolve_race._block_means` 截断的是
    #   `s = daily.dropna()` 的**长度**，不是 `len(daily)`。
    #   而产物里的 `n_days = len(series)` 是 `dropna` **之前**的长度。
    # ⇒ 拿 `n_days` 代入 `n = (len//b)*b` 量错了对象。
    # ⇒ 唯一能直接观测截断结果的量是 `n_independent_obs`（= `_block_means` 的返回长度），
    #   所以机制判据改用它；`t_blocked` 的逐位相同仍然是那个决定性预测。
    #
    # 下面这段把「预测在什么条件下会失效」**算出来**，而不是写死：
    #   块数 = (len(dropna)//b) ⇒ len(dropna) ∈ [块数*b, (块数+1)*b - 1]
    #   若该区间内唯一的 b 的倍数是左端点，则失效条件 = NaN 数恰好等于
    #   `n_days - 块数*b`，**且**被砍掉的那行不是 NaN。
    b = a["block"]
    nb = base["n_blocks"]
    d_lo, d_hi = nb * b, (nb + 1) * b - 1
    boundary = [m for m in range(d_lo, d_hi + 1) if m % b == 0]
    if boundary == [d_lo]:
        # NaN 个数恰等于它时，dropna 长度正贴着区间左端（= b 的倍数）
        frag_nan = base["n_days"] - d_lo
        premise = (f"dropna 长度落在 {d_lo}~{d_hi}，该区间内 b={b} 的唯一倍数是 {d_lo}"
                   f" ⇒ 预测**仅在** NaN 个数 ≠ {frag_nan}、或被砍掉的那行是 NaN 时成立")
    else:
        premise = (f"dropna 长度区间 {d_lo}~{d_hi} 内 b={b} 的倍数有 {len(boundary)} 个 "
                   f"⇒ 砍掉一天**不会**改变块数 ⇒ 逐位相同无条件成立")
    blocks_same = got["n_blocks"] == base["n_blocks"]

    print(f"锚（git HEAD 的 {DOC}）")
    print(f"  window_end = {a['window_end']}   n_days = {a['n_days']}")
    print(f"  t_blocked  = {a['t_blocked']!r}   ({a['t_blocked'].hex()})")
    print(f"旧窗口基线（钉住的提交 {base['commit'][:7]}）")
    print(f"  window_end = {base['window_end']}   n_days = {base['n_days']}   块数 = {base['n_blocks']}")
    print(f"判决窗口真值（同一提交里的 {WIDTH_REPORT}）")
    print(f"  window = {tgt['window_start']} → {tgt['window_end']}   n_days = {tgt['n_days']}"
          f"   （各档位见到 {tgt['widths_seen']}）")
    print(f"产物")
    print(f"  window_end = {got['window_end']}   n_days = {got['n_days']}")
    print(f"  块数 n_independent_obs = {got['n_blocks']}   （= _block_means 的返回长度）")
    print(f"  t_blocked  = {got['t_blocked']!r}   ({got['t_blocked'].hex()})")
    print(f"  p0_t_blocked = {got['p0_t_blocked']!r}")
    print(f"  verdict    = {art['verdict']}")
    print(f"机制：块数 {base['n_blocks']} → {got['n_blocks']}   {premise}")

    checks = []

    def chk(name, passed, detail=""):
        checks.append(bool(passed))
        print(f"{'✅' if passed else '❌'} {name}   {detail}")

    # ⚠️ 只比锚里**声称属于旧窗口**的那三项。
    #   锚的 `window_end = 2026-10-14` 与基线同源（锚就是从那次产物抄的），
    #   所以把它算进去是恒真的；目标窗口的真值走 `target_from_frozen_commit()`。
    chk("锚块与钉住提交里的旧产物一致（锚不是凭记忆写的）",
        a["t_blocked"].hex() == base["t_blocked"].hex()
        and a["p0_t_blocked"].hex() == base["p0_t_blocked"].hex()
        and a["n_days"] == base["n_days"],
        f"锚 n_days={a['n_days']} vs 基线 n_days={base['n_days']}；"
        f"锚 t={a['t_blocked'].hex()} vs 基线 t={base['t_blocked'].hex()}")
    chk("榜宽产物的各档位 n_days 达成一致", tgt["widths_agree"],
        f"见到 {tgt['widths_seen']}")
    chk("window[1] 与榜宽产物一致（重跑就是为了对齐它）",
        got["window_end"] == tgt["window_end"],
        f"placebo {got['window_end']} vs 榜宽 {tgt['window_end']}")
    chk("n_days 与榜宽产物一致",
        got["n_days"] == tgt["n_days"],
        f"placebo {got['n_days']} vs 榜宽 {tgt['n_days']}")
    chk("机制：块数与旧窗口相同（量的是 dropna 后的长度，不是 n_days）",
        blocks_same, f"{base['n_blocks']} vs {got['n_blocks']}")
    chk("t_blocked 逐位相同（比位模式，不是 ==",
        got["t_blocked"].hex() == a["t_blocked"].hex(),
        f"{a['t_blocked'].hex()} → {got['t_blocked'].hex()}")
    chk("p0_t_blocked 逐位相同",
        got["p0_t_blocked"].hex() == a["p0_t_blocked"].hex(),
        f"{a['p0_t_blocked'].hex()} → {got['p0_t_blocked'].hex()}")
    chk("cross_check 的 t_blocked 与 P0 臂一致",
        art["width_report_cross_check"]["t_blocked"].hex() == got["t_blocked"].hex())

    # 章：产物必须可引用，且依据是**字节证据**（预注册：它是第一份带字节证据的 placebo 产物）
    dirty_at_stamp = False
    if not (_ROOT / SIDECAR).exists():
        chk("sidecar 存在", False, f"{SIDECAR} 不存在 ⇒ 按本仓规矩是 unknown，**不可引用**")
    else:
        chk("sidecar 存在", True, SIDECAR)
        # ⚠️ 必须传**完整路径**：传裸名会让 `sidecar_path` 去找
        #   `placebo_control.provenance.json`，而真名是
        #   `placebo_control**.json**.provenance.json`
        #   ⇒ 静默返回 `unknown`「没有 sidecar」——对着一个其实盖过章的产物
        #   说它没盖过章（`TODO_generator_static_guards` §1）。
        #
        # ⚠️⚠️ 必须**在仓库根**调用（2026-10-15 实测，§1.2）：
        #   `freshness()` 把 sidecar 里的**仓库相对** `generator` 路径按 **cwd** 解析，
        #   从仓外调用会对 21/21 份产物一律返回 `unknown`
        #   「生成器路径不存在 ⇒ 判据本身没跑」。
        #   ⇒ 那是个**独立缺陷**，不该冒充「字节证据没兑现」。
        #   本脚本声明可换 cwd 运行，所以这里必须自己 chdir 回规范位置再问。
        _cwd = os.getcwd()
        try:
            os.chdir(_ROOT)
            f = freshness(_ROOT / REPORT)
        finally:
            os.chdir(_cwd)
        verdict, reason = f["verdict"], f["reason"]
        dirty_at_stamp = "未提交改动" in reason
        print(f"章（cwd={_ROOT}）：{verdict}")
        print(f"  依据：{reason}")
        byte_ok = verdict == "fresh" and "字节" in reason and "仅 commit" not in reason
        chk("freshness 依据是字节证据", byte_ok, f"verdict={verdict}")
        if not byte_ok:
            print("   ⇒ 预注册的「第一份带字节证据的 placebo 产物」这条**没兑现**")
            if verdict == "fresh" and "未提交改动" in reason:
                print("   ⇒ 真因是**盖章时工作区有未提交改动**（sidecar `code_dirty=True`）"
                      "⇒ 字节证据只覆盖生成器本身，不覆盖依赖闭包。"
                      "⇒ 要兑现它必须在**干净工作区**重跑盖章，不能改判据（第 129 层）。")

    ok = all(checks)
    print()
    if ok:
        print("✅ 三条预测全部兑现 ⇒ §0 那句「两个窗口的 t_blocked 必然逐位相同」"
              "**从推理升级为实测**。")
        if dirty_at_stamp:
            print("⚠️ **但字节证据有范围限定**，不许读成「整个依赖闭包都被钉住」：")
            print("   盖章时工作区有未提交改动（`code_dirty=True`）"
                  "⇒ 证明的是**生成器本身**，不是闭包。")
            print("   ⇒ 「第一份带完整字节证据的 placebo 产物」这条**尚未兑现**，"
                  "要兑现必须在干净工作区重跑盖章——**不是改判据**（第 129 层）。")
    else:
        print("🚨 有预测未兑现。**逐条处理，不许整体判过**：")
        if not blocks_same or got["t_blocked"].hex() != a["t_blocked"].hex():
            print("   ▸ `t_blocked` 变了 ⇒ 我的机制理解是错的"
                  "（`_block_means` 截的是 `dropna()` 后的长度；"
                  "若 `dropna` 长度恰为 b 的倍数，砍掉一天就会少一块）")
            print("     ⇒ **此时任何安慰剂数字都不可引用**，先查机制，"
                  "**不许改判据、不许重跑到对上为止**（第 129 层）。")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

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


def main() -> int:
    a = anchor_from_git_head()

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
        "window_end": art["window"][1],
    }

    block = a["block"]
    # 机制：`evolve_race._block_means` 截掉尾部不足一块的交易日 ⇒ n = (len//b)*b
    # 窗口少两天 ⇒ 截断后**仍是同一段前缀** ⇒ 分块 t 必然逐位相同。
    prefix_old = (a["n_days"] // block) * block
    prefix_new = (got["n_days"] // block) * block
    prefix_same = prefix_old == prefix_new

    print(f"锚（git HEAD 的 {DOC}）")
    print(f"  window_end = {a['window_end']}   n_days = {a['n_days']}")
    print(f"  t_blocked  = {a['t_blocked']!r}   ({a['t_blocked'].hex()})")
    print(f"产物")
    print(f"  window_end = {got['window_end']}   n_days = {got['n_days']}")
    print(f"  t_blocked  = {got['t_blocked']!r}   ({got['t_blocked'].hex()})")
    print(f"  p0_t_blocked = {got['p0_t_blocked']!r}")
    print(f"  verdict    = {art['verdict']}")
    print(f"机制：截断后前缀 {prefix_old} → {prefix_new} 天 "
          f"{'（同一段 ⇒ 逐位相同是必然）' if prefix_same else '（⚠️ 不同段 ⇒ 预测不成立）'}")

    checks = []

    def chk(name, passed, detail=""):
        checks.append(bool(passed))
        print(f"{'✅' if passed else '❌'} {name}   {detail}")

    chk("window[1] = 2026-10-12", got["window_end"] == "2026-10-12", got["window_end"])
    chk("n_days = 1395", got["n_days"] == 1395, got["n_days"])
    chk("机制：两窗口截断后同前缀", prefix_same, f"{prefix_old} vs {prefix_new}")
    chk("t_blocked 逐位相同（比位模式，不是 ==",
        got["t_blocked"].hex() == a["t_blocked"].hex(),
        f"{a['t_blocked'].hex()} → {got['t_blocked'].hex()}")
    chk("p0_t_blocked 逐位相同",
        got["p0_t_blocked"].hex() == a["p0_t_blocked"].hex(),
        f"{a['p0_t_blocked'].hex()} → {got['p0_t_blocked'].hex()}")
    chk("cross_check 的 t_blocked 与 P0 臂一致",
        art["width_report_cross_check"]["t_blocked"].hex() == got["t_blocked"].hex())

    # 章：产物必须可引用，且依据是**字节证据**（预注册：它是第一份带字节证据的 placebo 产物）
    if not (_ROOT / SIDECAR).exists():
        chk("sidecar 存在", False, f"{SIDECAR} 不存在 ⇒ 按本仓规矩是 unknown，**不可引用**")
    else:
        chk("sidecar 存在", True, SIDECAR)
        # ⚠️ 必须传**完整路径**：传裸名会让 `sidecar_path` 去找
        #   `placebo_control.provenance.json`，而真名是
        #   `placebo_control**.json**.provenance.json`
        #   ⇒ 静默返回 `unknown`「没有 sidecar」——对着一个其实盖过章的产物
        #   说它没盖过章（`TODO_generator_static_guards` §1）。
        basis = str(freshness(_ROOT / REPORT))
        byte_ok = "字节" in basis and "仅 commit" not in basis
        chk("freshness 依据是字节证据", byte_ok, basis[:120])
        if not byte_ok:
            print("   ⇒ 预注册的「第一份带字节证据的 placebo 产物」这条**没兑现**")

    ok = all(checks)
    print()
    if ok:
        print("✅ 三条预测全部兑现 ⇒ §0 那句「两个窗口的 t_blocked 必然逐位相同」"
              "**从推理升级为实测**。")
    else:
        print("🚨 有预测未兑现。**逐条处理，不许整体判过**：")
        if not prefix_same or got["t_blocked"].hex() != a["t_blocked"].hex():
            print("   ▸ `t_blocked` 变了 ⇒ 我的机制理解是错的"
                  "（`_block_means` 的截断不是我以为的那样，或 P0 臂不走那条路径）")
            print("     ⇒ **此时任何安慰剂数字都不可引用**，先查机制，"
                  "**不许改判据、不许重跑到对上为止**（第 129 层）。")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

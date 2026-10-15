#!/usr/bin/env python3
"""市值暴露的判决 —— 预注册 `docs/TODO_cap_exposure_2026-10-15.md`。

## 这个脚本只做一件事

回答预注册 §1 的问题：**生产组合在流通市值上有没有可测的系统性倾斜？**

## 它**不是**什么（预注册 §4 已先写死，事后不许补）

1. 不是「组合有没有市值暴露」的**头条** —— `POWERLESS` 的意思是**分不出**，
   不是「没有」（与 `score_ranking_power.NOT_PASSED_POWERLESS` 同一个坑）。
2. **不复核**头条毛超额，也不替换它。
3. 正负只说**方向**，不说「大小是否被定价」。

## 三条纪律

1. **测量仪器只有一台** —— OLS、野自助、Bonferroni 门槛、符号一致性
   全部 `from factor_attribution import`，**一个字都不重写**。
   自己写第二份 = 造第二台测量仪（第 22 层），而两份一旦漂移，**没有任何东西会报警**。
2. **缺档必须报错，绝不回落** —— 少一列就抛异常，不拿 0 顶。
   `expo_logcap` 在没有 `CapTable` 的那些跑里是**整列 NaN**，
   这里若悄悄 `fillna(0)`，就会造出一个「这只票市值为零」的假观测。
3. **判决量与它的对照量必须同单位** —— `POWERLESS` 比的是
   `|β̂_obs|` 与 `q95(|β*_null|)`，**都是 β**。不是 t、不是 z。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import sys

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from aplus import provenance  # noqa: E402

_SCRIPTS = pathlib.Path(__file__).resolve().parent


def _load_fa():
    """把 `factor_attribution.py` 当**模块**加载（它是脚本，不是包）。

    ⚠️ 必须按路径加载而不是 `import factor_attribution` ——
      后者会依赖 `sys.path` 的偶然状态，在别的 cwd 下会拿到**另一个**文件。
    """
    spec = importlib.util.spec_from_file_location(
        "_fa_cap_exposure", _SCRIPTS / "factor_attribution.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


FA = _load_fa()


def _load_producer():
    """把 `backtest_walk_forward_weights.py` 当**模块**加载，**只为读它的列名常量**。

    🚨 为什么必须读它、不许手打（2026-10-15 实测踩到）：
      原来的 `CTRL_COL = "expo_logcap_rawz"` 在真实产物里**从不存在** ——
      生产者的 `EXPO_COLS` 末两位是 `logcap` / `logcap_raw`，
      配 `f"expo_{f}"` 导出，得到 `expo_logcap` / `expo_logcap_raw`。
      ⇒ 对照臂**一次都没跑起来过**，而当时 12 条守卫**全绿**。

    ⇒ 根因不是「手滑打错一个字」：命名约定 `f"expo_{f}"` 的**唯一主人**
      在生产者。手打一个后缀就是**第二个主人**（第 148 层同族 ——
      那层是「下标偏移量有第二个主人」，这层是「列名有第二个主人」）。
      而同一个文件上面两行就是**正确范式**：
      `FACTOR8 = tuple(FA.FACTOR_COLS)` ——「不另抄一份」。

    ⚠️ import 它**不执行** `main()`（有 `if __name__ == "__main__"` 守卫），
      实测 import 耗时 0.85 秒，不碰面板、不读 GB 级数据。
    """
    spec = importlib.util.spec_from_file_location(
        "_bwfw_cap_exposure", _SCRIPTS / "backtest_walk_forward_weights.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


PRODUCER = _load_producer()

#: 5 档 K —— 与 `wf_weights_backtest_k*.daily.parquet` 一一对应。
#: ⚠️ K 是 `rebalance_every`（**从产物自己的字段读出来的**，不是猜的）。
K_ARMS = (5, 10, 21, 42, 63)

#: 主判据列（`robust_z` 标准化）。对照臂列**永远不进判决**。
#: 🚨 两列都由生产者的列名**推导**，不手打（理由见 `_load_producer`）。
MAIN_COL = f"expo_{PRODUCER.LOGCAP_COL}"
CTRL_COL = f"expo_{PRODUCER.LOGCAP_RAW_COL}"

#: 8 个既有因子列 —— 从 `factor_attribution.FACTOR_COLS` 拿，**不另抄一份**。
FACTOR8 = tuple(FA.FACTOR_COLS)

PREREG = "docs/TODO_cap_exposure_2026-10-15.md"

WHAT_THIS_CANNOT_PROVE = [
    "POWERLESS ≠ 没有市值暴露：它是检出力不足，与 score_ranking_power 的 "
    "NOT_PASSED_POWERLESS 同一个坑（第 122 层三态语义）。",
    "不许用本实验的数字替换头条毛超额 —— 本实验只测暴露，"
    "「剔除市值后还剩多少」需要市值中性化的池子重跑，是另一个实验。",
    "β 的正负只说方向，不说「大小是否被定价」；后者要对基准池的市值溢价单独测量。",
    "本实验不复核 wf_weights_backtest.json 的年化毛超额 —— "
    "那个口径已被 docs/TODO_cap_weighted_benchmark_2026-10-15.md 降级（第 143 层）。",
    f"对照臂 `{CTRL_COL}` 是裸 z（不缩尾），只报不判；"
    "若它与主列方向相反，处置写死在预注册里：记进本字段，不改主判据。",
]


def arm_path(k: int, data_root: pathlib.Path) -> pathlib.Path:
    return data_root / "reports" / f"wf_weights_backtest_k{k}.daily.parquet"


def _load_arm(k: int, data_root: pathlib.Path, cap_col: str) -> pd.DataFrame:
    """读一档日活，**缺列即报错**。

    🚨 绝不回落：`fillna(0)` 会把「没量到市值」说成「市值为零」，
      而 `robust_z` 的均值/sd 会被这些 0 拽住（第 9 层）。
    """
    p = arm_path(k, data_root)
    if not p.exists():
        raise FileNotFoundError(
            f"★ 缺 {p}\n⇒ 这一档的日活不存在。**不许拿别的档顶替** —— "
            f"K={k} 是判决的一部分，缺一档会让「5 档全同号」变成 4 档全同号。\n"
            f"   正确修法：重跑那一档 "
            f"`python scripts/backtest_walk_forward_weights.py "
            f"--rebalance-every {k} --out data/reports/wf_weights_backtest_k{k}.json`。")
    df = pd.read_parquet(p)
    need = [FA.Y_COL, *FACTOR8, cap_col]
    missing = [c for c in need if c not in df.columns]
    if missing:
        # 🚨 2026-10-15 改文案：原文只写「大概率是旧产物 ⇒ 整链重跑」，
        #   而真实成因常常是**消费者问了一个生产者从无此列的名字** ——
        #   照原文办事会白跑 5×7 分钟的链，什么也修不好
        #   （与「断言对、成因错」同族：抛异常是对的，推断是错的）。
        # ⇒ 两个成因**并列摆出**，不替读者下结论。
        raise KeyError(
            f"★ {p.name} 缺列 {missing}。\n"
            f"   日活里现有：{list(df.columns)}\n"
            f"   其中 expo_* 列：{sorted(c for c in df.columns if c.startswith('expo_'))}\n"
            f"⇒ 两种可能，**本脚本不替你选**：\n"
            f"   (a) 这一档是**旧产物**（接线 expo_* 之前跑的）⇒ 整链重跑五档；\n"
            f"   (b) 要的列名 `cap_col` 与生产者的导出清单对不上\n"
            f"       （权威清单：backtest_walk_forward_weights.EXPO_COLS\n"
            f"         ⇒ {sorted(f'expo_{c}' for c in PRODUCER.EXPO_COLS)}）。\n"
            f"   先用 (b) 的清单核对一次再决定要不要重跑 —— 重跑不修名字。\n"
            f"   若确是 (a)：**整链重跑五档**，不许只补一档 —— "
            f"混批次的 5 档会让符号一致性判据失去意义。")
    return df


def _analyze_arm(k: int, data_root: pathlib.Path, cap_col: str) -> dict:
    df = _load_arm(k, data_root, cap_col)
    cols = [*FACTOR8, cap_col]
    d = df[[FA.Y_COL, *cols]].dropna()
    n_dropped = int(len(df) - len(d))
    if n_dropped:
        # 整段剔除必须**报出来**：静默丢样本会让 n 变成一个没人看过的数。
        print(f"  K={k}: 剔掉 {n_dropped}/{len(df)} 行（任一列 NaN，含停牌日）",
              flush=True)
    res = FA.analyze(d, cols, f"K={k}/{cap_col}")
    if res.get("error"):
        raise ValueError(f"★ K={k} {cap_col}: {res['error']}")
    # 🚨 不许用 `.get(cap_col)`（2026-10-15 实测）：
    #   「键不存在」会静默变成 `None`，而下游 `decide()` 把 `None`
    #   与「仪器算出了 NaN」压成同一格 ⇒ 三种语义坍缩成一句事实断言。
    #   键缺了就该在这里响亮地停（第 122 层）。
    q95_by_col = res.get("_beta_star_abs_q95")
    if not isinstance(q95_by_col, dict) or cap_col not in q95_by_col:
        raise KeyError(
            f"★ K={k} {cap_col}: 仪器没有交出这一列的零分布 95 分位。\n"
            f"   `_beta_star_abs_q95` 现有："
            f"{sorted(q95_by_col) if isinstance(q95_by_col, dict) else q95_by_col!r}\n"
            f"⇒ 缺档必须报错，绝不回落成 `None`（`None` 与 `NaN` 同形，"
            f"会让「没量到」被读成「分得出」）。")
    return {"k": k, "n": res["n"], "period": res["period"],
            "n_dropped": n_dropped,
            "beta": res["factors"][cap_col]["beta"],
            "t": res["factors"][cap_col]["t"],
            "se": res["factors"][cap_col]["se"],
            "boot_p": res["factors"][cap_col]["boot_p"],
            "passes_bonferroni": res["factors"][cap_col]["passes_bonferroni"],
            "p_bonferroni": res["p_bonferroni"],
            "n_factors": res["n_factors"],
            "beta_star_abs_q95": q95_by_col[cap_col],
            "joint_p": res["joint_p"],
            "joint_F": res["joint_F"],
            "collinear": res["collinear"],
            "collinear_note": res["collinear_note"]}


def decide(arms: list[dict]) -> tuple[str, str]:
    """预注册 §3 的**四态**判决 —— 纯函数，**不碰文件**。

    ★ 抽出来是为了让守卫能**直接**喂合成夹具：
      若判决逻辑埋在读 parquet 的循环里，守卫就只能靠 monkeypatch 文件系统
      才能测到分支 —— 那是「测不到就是没守」（第 22 层同族）。

    返回 `(verdict, why)`。

    🚨 **入场闸门**（2026-10-15 实测出来的两个假判决，见 `decide` 的实现）：
      ① `all([])` 恒真 ⇒ 0 档会被判成「这份样本分不出」；
      ② `q is not None and q >= |β|` 把 `None`（仪器没交出）与
         `NaN`（算了但数值崩了）一起算成「分得出」⇒ 输出
         「分得出来而没分出来」这句**关于事实的断言**，而触发它的都是非观测。
      ⇒ 非观测一律**响亮拒绝出判决**，**不是**补一个第五态：
        预注册 §3 的四个状态是对**已测量的臂**定义的，没测出来的输入
        在那个定义域之外 —— 事后往里加状态就是「补写出来的登记冒充预注册」
        （形状 22）。「取保守方向」在这里**不成立**：
        两个方向都是断言，只是一个听起来较弱。
    """
    if not arms:
        raise ValueError(
            "★ 0 档进判决。`all([])` 在 Python 里恒为真，"
            "旧实现因此把零观测判成 `SIZE_EXPOSURE_POWERLESS`"
            "（'0/0 档过线…分不出'）。\n"
            "⇒ 0 档能说的话只有一句：**不出判决**（第 89 层"
            "「崩溃与空目录不可区分」的镜像）。")
    unmeasured = [
        a["k"] for a in arms
        if a.get("beta_star_abs_q95") is None          # 仪器没交出
        or not np.isfinite(a["beta_star_abs_q95"])     # 算了但数值崩了
    ]
    if unmeasured:
        raise ValueError(
            f"★ {'、'.join(f'K={k}' for k in unmeasured)} 的 "
            f"`beta_star_abs_q95` 不是观测值。\n"
            f"   `None` = 仪器没交出；`NaN` = 算了但数值崩了。\n"
            f"   两者都**不等于**「分不出」，也**不等于**「分得出」——\n"
            f"   旧实现把两者都算成「分得出」⇒ 输出「分得出来而没分出来」"
            f"这句关于事实的断言（第 122 层）。\n"
            f"   处置：先修那一档的仪器输出，再谈判决。")
    sc = FA.sign_consistency([a["beta"] for a in arms])
    n_pass = sum(1 for a in arms if a["passes_bonferroni"])

    # ── 预注册 §3：四态，互斥且穷尽 ──────────────────────────────────
    if n_pass >= 1:
        if sc["consistent"]:
            return ("SIZE_EXPOSURE_CONFIRMED",
                    f"{n_pass}/{len(arms)} 档过 Bonferroni 且全部同号"
                    f"（符号 {sc['sign']:+d}）")
        return ("SIZE_EXPOSURE_OVERFIT",
                f"{n_pass}/{len(arms)} 档过线但跨 K 符号不一致"
                f"（signs={sc['signs']}）⇒ 不得使用")

    # 补记 3：POWERLESS 要求**全部**档都「零分布 95 分位 ≥ 实测 |β|」。
    # 一档分得出来就不许说 POWERLESS —— 那会把「没测出来」说成「不存在」。
    for a in arms:
        q = a["beta_star_abs_q95"]
        # ⚠️ q 为 None ⇒ 这档的零分布**没算出来**（仪器没交出它）。
        #   那**不等于**「分不出」，按保守方向算「分得出」⇒ 落到 NO_MEASURED。
        a["powerless_on_this_arm"] = bool(q is not None and q >= abs(a["beta"]))
    if all(a["powerless_on_this_arm"] for a in arms):
        return ("SIZE_EXPOSURE_POWERLESS",
                f"0/{len(arms)} 档过线，且**每**一档的零分布 95 分位都 ≥ 实测 |β|"
                " ⇒ 这份样本分不出这个量级的暴露")
    which = [a["k"] for a in arms if not a["powerless_on_this_arm"]]
    return ("NO_SIZE_EXPOSURE_MEASURED",
            f"0/{len(arms)} 档过线，但 K={which} 上的零分布 95 分位 < 实测 |β|"
            " ⇒ 分得出来而没分出来")


def measure(data_root: pathlib.Path, cap_col: str) -> dict:
    """跑 5 档并按预注册 §3 的四态出判决。"""
    arms = [_analyze_arm(k, data_root, cap_col) for k in K_ARMS]
    verdict, why = decide(arms)

    return {
        "cap_col": cap_col,
        "role": "primary" if cap_col == MAIN_COL else "control_only",
        "verdict": verdict,
        "verdict_why": why,
        "n_arms": len(arms),
        "n_arms_passing": sum(1 for a in arms if a["passes_bonferroni"]),
        "sign_consistency": FA.sign_consistency([a["beta"] for a in arms]),
        "p_bonferroni": arms[0]["p_bonferroni"],
        "n_factors": arms[0]["n_factors"],
        "alpha_two_sided": FA.ALPHA_TWO_SIDED,
        "bootstrap": {"method": "wild (Rademacher)",
                      "B": FA.BOOTSTRAP_B, "seed": FA.BOOTSTRAP_SEED},
        "y_col": FA.Y_COL,
        "standardization": {
            MAIN_COL: "aplus.factor.robust_z（q01/q99 缩尾后的 mean/std）",
            CTRL_COL: "裸 z（不缩尾）—— 对照臂，不判",
        },
        "arms": arms,
        "what_this_cannot_prove": WHAT_THIS_CANNOT_PROVE,
    }


def control_arm_note(primary: dict, control: dict) -> str:
    """对照臂与主臂的方向之差 —— 预注册补记 1 的那一句。

    🚨 2026-10-15 首跑即假：原实现问的是
      「**各臂跨 K 是否同号**」（`sign_consistency.consistent`），
      而补记 1 问的是「**两臂是否给出相反方向**」——
      **两个不同的问题**。错的那一路让「臂内跨 K 有正有负」
      冒充成「臂与臂之间的分歧」，于是产物写下
      「两臂方向不一致（robust_z 符号 [-1,-1,1,-1,1] vs 裸 z [-1,-1,1,-1,1]）」
      ——**紧挨着把它否掉的那组数**。

    ⇒ 现在比的是**逐档符号向量**这一个量。
      而当两臂**各自**都跨 K 不同号时，「偏大盘还是偏小盘」
      **没有单一方向可答** ⇒ 补记 1 的「相反方向」检验
      **无法评估**，这既不是「相反」也不是「一致」，必须原样说出来
      （第 122 层：非观测不许被压成断言，哪怕弱断言）。
    """
    pv, cv = primary["sign_consistency"], control["sign_consistency"]
    ps, cs = list(pv["signs"]), list(cv["signs"])
    if ps == cs:
        note = (f"两臂的逐档符号向量**逐位相同**（{ps}）"
                f" ⇒ 缩尾与否没有改变任何一个档位的方向。")
        if not (pv["consistent"] and cv["consistent"]):
            note += (f"\n⚠️ 但**两臂各自跨 K 都不同号**（robust_z {ps} / 裸 z {cs}）"
                     f" ⇒「组合偏大盘还是偏小盘」在这份样本上"
                     f"**没有单一方向可答**；预注册补记 1 的"
                     f"「相反方向」检验**无法评估**"
                     f"（不是「相反」，是「无方向」）。")
        return note
    return (f"⚠️ **两臂方向不一致**（robust_z 符号 {ps} vs 裸 z {cs}）⇒ "
            f"按预注册补记 1：记进 `what_this_cannot_prove`，**不改主判据**。")


def _parse_args(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", type=pathlib.Path, default=ROOT / "data")
    ap.add_argument("--out", type=pathlib.Path,
                    default=ROOT / "data" / "reports" / "cap_exposure.json")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    # ⚠️ 签名是 `(root, generator)`，**不是** `(path, root=…)`。
    #   传错位置不会报错，只会让章里写上别的文件的字节（第 109 层那类）。
    expect_bytes = provenance.code_file_sha256(ROOT, "scripts/cap_exposure.py")

    primary = measure(args.data_root, MAIN_COL)
    print(f"\n主判据 {MAIN_COL}：{primary['verdict']}\n  {primary['verdict_why']}",
          flush=True)

    control = measure(args.data_root, CTRL_COL)
    print(f"对照臂 {CTRL_COL}：{control['verdict']}", flush=True)

    # 方向是否一致 —— 预注册补记 1 要求**如实报出**，不许只报好看的那一臂。
    # ⚠️ 比较的是**两臂之间**（`control_arm_note`），不是各臂**臂内**跨 K 的同号性。
    control_note = control_arm_note(primary, control)
    print(control_note, flush=True)

    payload = {
        "kind": "cap_exposure",
        "prereg": PREREG,
        "primary": primary,
        "control_arm": control,
        "control_arm_note": control_note,
        "arms_k": list(K_ARMS),
        # ⚠️ 必须**追加**对照臂的实测结果，不是照抄常量 ——
        #   补记 1 的原话是「**记进 `what_this_cannot_prove`**」，
        #   而那个列表才是读产物的人会去看的地方。
        "what_this_cannot_prove": [*WHAT_THIS_CANNOT_PROVE, control_note],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    provenance.stamp(args.out, "scripts/cap_exposure.py", root=ROOT,
                     data_root=args.data_root, expect_bytes=expect_bytes)
    print(f"\n→ {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

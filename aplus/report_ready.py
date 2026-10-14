"""「跑完了吗」的判据 —— **不信退出码，只信产物指纹**。

## 为什么要有这个模块

2026-10-14 踩过一次：

```bash
nohup .venv/bin/python scripts/measure_bridge_error.py > /tmp/log 2>&1 &
```

配 `run_in_background` 时，Bash **立刻返回 exit 0**，
而 `python` 其实还在跑（PID 53452）。差点就去读
`data/reports/bridge_error.json` —— 那是**上一轮**的文件，
里面连本轮要加的 `measured_long_vs_mkt_*` 列都没有。

这类失败比崩溃危险：崩溃会有 traceback 会让红，
而**假成功不产生任何错误输出** —— 没有 stderr、没有非零码。

## 判据

| | 靠不靠谱 |
|---|---|
| 退出码 | ❌ 后台/超时场景下是假的 |
| 进程还活着吗 | 🟡 只说明它**开始过** |
| **产物里有我要的键吗** | ✅ |

最后一行是唯一可靠的：**完成由产物内容定义，不由进程状态定义。**

## 用法

```python
from aplus.report_ready import require_keys

ok = require_keys("data/reports/bridge_error.json",
                  ["years"], key_path="years.0.measured_long_vs_mkt_annual_by_q")
if not ok.ok:
    print(ok.reason)     # → 还差哪个键
```

不要写成 `assert`，也**不要**写成报错退出：
调用方多数情况是「重跑一次」而不是「停下」，
所以这里返回判据，让调用方决定。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Ready:
    ok: bool
    reason: str


def _dig(obj, dotted: str):
    """按 `a.0.b` 取值。任何一段不存在就返回哨兵，不抛。"""
    cur = obj
    for part in dotted.split("."):
        if isinstance(cur, list):
            if not part.lstrip("-").isdigit():
                return _MISSING
            idx = int(part)
            if not (-len(cur) <= idx < len(cur)):
                return _MISSING
            cur = cur[idx]
        elif isinstance(cur, dict):
            if part not in cur:
                return _MISSING
            cur = cur[part]
        else:
            return _MISSING
    return cur


class _Missing:
    __slots__ = ()

    def __repr__(self):                      # 让报错信息里看得见
        return "<缺>"

    def __bool__(self):
        return False


_MISSING = _Missing()


def require_keys(path, key_paths, min_mtime: float | None = None) -> Ready:
    """`path` 这个 JSON 是否存在、且每个点分键路径都真实存在。

    `min_mtime`（epoch 秒）要求文件**至少**是那一刻之后写的。
    用途：区分「这轮写的」与「上一轮的旧文件」——
    **这正是那次读错报告的方式**。

    **为什么要用点分路径而不是只看顶层键**：顶层 `years` 存在
    不代表 `years.0.measured_long_vs_mkt_annual_by_q` 存在，
    而后者才是本轮真正要的。**顶层齐全 ≠ 内容齐全**，
    这与「列都在但值全是 NaN」是同一类错误。
    """
    p = Path(path)
    if not p.is_file():
        return Ready(False, f"{p} 不存在")

    if min_mtime is not None and p.stat().st_mtime < min_mtime:
        import datetime
        got = datetime.datetime.fromtimestamp(p.stat().st_mtime)
        want = datetime.datetime.fromtimestamp(min_mtime)
        return Ready(False, f"{p} 是旧文件（mtime {got:%H:%M:%S} < {want:%H:%M:%S}）")

    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:                  # 半截文件是最常见的"没跑完"
        return Ready(False, f"{p} 不是合法 JSON（多半是还在写）：{exc}")

    for kp in key_paths:
        val = _dig(obj, kp)
        if isinstance(val, _Missing):
            return Ready(False, f"{p} 缺 {kp} —— **上一轮的报告会被误读成这一轮的**")
        if val is None:
            return Ready(False, f"{p} 的 {kp} 是 null")
    return Ready(True, f"{p} 齐备（{len(key_paths)} 个键）")


def is_nan_column(df, col: str) -> bool:
    """某一列是不是**全 NaN**。

    全 NaN 的列会让 `np.allclose(..., equal_nan=True)` 恒为 True，
    于是依赖它的断言全部变成恒真 —— **测试「通过」而什么也没测**。
    这种 fixture 错误在变异验证里表现为「守卫守不住」。
    """
    if col not in getattr(df, "columns", []):
        return True
    return bool(df[col].isna().all())


def require_run_completed(path, sibling_suffixes=(".daily.parquet",)) -> Ready:
    """★ **副产物比主产物新 ⇒ 这一轮在中途死了。**

    ## 要抓的具体事故（2026-10-15）

    `scripts/backtest_walk_forward_weights.py` 的落盘顺序是
    「先 `.daily.parquet`、后 `.json`」。而两行之间有一句
    `sum(... for r in results)` —— `results` 从未定义，`NameError`。

    于是那次运行的结果是：

        wf_weights_backtest_k21.daily.parquet   00:17  ← 新
        wf_weights_backtest_k21.json            （不存在）

    **目录里有这一轮的文件**。任何用「产物 mtime 是最新的吗」判断
    「这轮跑完了」的办法，都会判成**跑完了**。

    ## 为什么 `require_keys` 抓不到

    主文件**根本不存在**，而 `require_keys` 的用途是
    「主文件在，但内容不齐」。**缺文件与内容旧是两回事**，
    混在一起就会出现「因为主文件不存在，所以读到了上一轮的那份」——
    这正是本模块 docstring 记的那次事故。

    ⇒ 所以需要一条**独立**的判据：**主产物必须不早于任何副产物**。

    ## 这条判据的强度：它不需要知道任何字段

    只比两个 mtime。它能覆盖**任意**在两处落盘之间崩溃的原因
    （`NameError` / `KeyError` / OOM / 断电 / 被 kill），
    因为这些在文件系统上留下的痕迹**是一样的**：
    副产物新、主产物旧或缺失。
    """
    p = Path(path)
    if not p.is_file():
        return Ready(False, f"{p} 不存在 —— 主产物没落盘，这轮没跑完")
    main_mtime = p.stat().st_mtime
    for suf in sibling_suffixes:
        sib = p.with_suffix(suf)
        if not sib.is_file():
            continue
        if sib.stat().st_mtime > main_mtime:
            import datetime
            return Ready(
                False,
                f"🚨 {sib.name} 比 {p.name} **更新** —— 这一轮在中途死了："
                f"副产物 {datetime.datetime.fromtimestamp(sib.stat().st_mtime):%H:%M:%S} "
                f"落在主产物 {datetime.datetime.fromtimestamp(main_mtime):%H:%M:%S} 之后。"
                "目录里有本轮的文件，但**本轮的主报告没有**。")
    return Ready(True, f"{p.name} 不早于任何副产物（{len(sibling_suffixes)} 个）")
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
"""改进台账：追加式、结构化、**方向由数字推导**。

## 设计上唯一要紧的一条

`direction` 字段是**推导出来的**，不是作者填的。
早先的版本允许调用方自己写 `"better"`，结果台账里清一色 better——
一个只会夸自己的账本比没有账本更糟，因为它把「我修了多少 bug」
伪装成「我赚到了多少 alpha」。方向由 before/after 算出，作者无权声明。

允许出现 `worse` 是这个模块存在的理由：
2026-10-11 修掉可交易性缺口后，涨篮子超额从 +0.1965% 掉到 −0.0067%。
那**不是退步**，那是从虚高回到真实。不许把这种条目筛掉或改写成 `better`。
"""
from __future__ import annotations

import json
from pathlib import Path

LEDGER_NAME = "evolution_ledger.json"
RELATIVE_TOL = 1e-12


def ledger_path(data_root) -> Path:
    return Path(data_root) / "reports" / LEDGER_NAME


def _derive_direction(before: float, after: float, higher_is_better: bool = True) -> str:
    """方向由**数字与指标语义**推出，作者无权声明。

    ## `higher_is_better` 为什么必须存在

    最初版本一律按「数字变大 = 变好」推导，灌入真实数据后 11 条里 6 条判反：
    年成本 16.91%→0.57%、Top10 并列 9→0、HTTP 500→200、隔离股票 13→0
    ——**全是实打实的改善，却被标成 worse**。

    把改善标成退步的台账比没有台账更有害：它让人开始怀疑改善本身，
    然后不再信任任何数字。

    作者声明的只是「这个指标朝哪边算好」——那是**事实**（成本当然越低越好），
    不是评价。「这条是进步还是退步」依然由数字推导，堵死谎报入口。
    """
    scale = max(abs(before), abs(after), 1e-12)
    if abs(after - before) <= RELATIVE_TOL * scale:
        return "flat"
    rose = after > before
    return ("better" if rose else "worse") if higher_is_better \
        else ("worse" if rose else "better")


def load_ledger(data_root, entry: dict | None = None) -> list[dict]:
    """读台账。`entry` 非空时**只校验并返回该条**（供 record_improvement 复用）。

    坏文件返回空列表而不是抛错：台账是辅助信息，让整个界面 500 不划算。
    """
    if entry is not None:
        _validate(entry)
        out = {k: v for k, v in entry.items() if k != "direction"}
        out["direction"] = _derive_direction(
            entry["before"], entry["after"], bool(entry.get("higher_is_better", True)))
        return [out]
    path = ledger_path(data_root)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return data if isinstance(data, list) else []


def _validate(entry: dict) -> None:
    if entry.get("before") is None or entry.get("after") is None:
        raise ValueError(
            "台账条目必须同时给出**前后**两个实测数字（before 与 after）；"
            "只有定性描述（「修好了 X」）的条目请勿入账——那是自我表扬，不是记录。"
        )
    if not str(entry.get("metric") or "").strip():
        raise ValueError("台账条目必须有 metric：这组数字是什么的 before/after")
    if not str(entry.get("title") or "").strip():
        raise ValueError("台账条目必须有 title")
    for k in ("before", "after"):
        v = entry[k]
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError(f"{k} 必须是数值，不能是 {type(v).__name__}")
        if v != v:  # NaN
            raise ValueError(f"{k} 不能是 NaN")


def record_improvement(data_root, entry: dict) -> dict:
    """追加一条改进记录。返回规范化后的条目。"""
    rows = load_ledger(data_root)
    rows.extend(load_ledger(data_root, entry))
    path = ledger_path(data_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return rows[-1]


# 兼容别名：语义更直白的名字
append_improvement = record_improvement


def ledger_summary(data_root) -> dict:
    """按方向计数 + 作废结论数。`worse` 必须能被看见，不能被筛掉。"""
    rows = load_ledger(data_root)
    return {
        "total": len(rows),
        "better": sum(1 for r in rows if r.get("direction") == "better"),
        "worse": sum(1 for r in rows if r.get("direction") == "worse"),
        "flat": sum(1 for r in rows if r.get("direction") == "flat"),
        "invalidated_claims": sum(len(r.get("invalidates") or []) for r in rows),
    }

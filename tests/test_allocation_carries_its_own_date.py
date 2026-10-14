"""产物**必须自带身份** —— 日期不能只活在文件名里（2026-10-15）。

## 起因

前端修「日期谎报」时发现：`alloc_*.json` 的内容里**一个日期字段都没有**。
扫遍 6 个文件的每一层，顶层键只有
`capital / cash_weight / positions / rebalance / risk_notes`。

根因在写盘那一步（`scripts/run_daily.py:579`）：

```python
save_allocation(reports_dir, today, plan.to_dict())
```

`date` **只拿去拼文件名**（`alloc_<date>.json`），**从不写进内容**。

⇒ **产物的身份被劈成两路**：文件名有、内容没有。
⇒ 前端 `a.date ?? ''` 渲染成「生成于 」——
一个**不报错、不留痕、控制台干净**的空值。

## 为什么接口层打补丁不算修好

当时的修法是在 `webapp.py` 的响应组装里补
`{**alloc, "date": lp}`，`lp` 取自文件名。
**那只是把接口接上了，产物本身仍然没有身份** ——
任何别的消费者直接读文件，还是拿不到日期。

⇒ 根因在 `aplus/reports.py:save_allocation`，修在那里才有效。
本守卫钉的就是**根因那一层**，不是接口那一层。

## 两条必须同时成立

① **写**：新产出的 alloc 内容里**有** `date`，且与文件名**逐字相同**
② **读**：`load_allocation` 对缺 `date` 的旧文件返回 `date=None`，
   **不静默回填文件名日期** —— 缺就是缺，不能假装它有
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from aplus.reports import (  # noqa: E402
    _alloc_path, load_allocation, save_allocation,
)

DATE = "2026-10-14"
PLAN = {
    "capital": 50_000.0,
    "cash_weight": 0.2,
    "positions": [{"code": "600000", "name": "浦发银行", "weight": 0.16,
                   "amount": 8_000.0, "score": 1.23}],
    "rebalance": [{"code": "600000", "action": "hold"}],
    "risk_notes": [],
}


def test_a_new_allocation_carries_its_own_date(tmp_path):
    """① 写盘时 `date` 必须进**内容**，不能只拼文件名。"""
    save_allocation(tmp_path, DATE, dict(PLAN))

    path = _alloc_path(tmp_path, DATE)
    assert path.exists(), f"{path} 没写出来"
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw.get("date") == DATE, (
        f"alloc 内容里没有 date（顶层键：{sorted(raw)}）—— "
        "日期只活在文件名里 ⇒ 产物没有身份，任何消费者都得去猜文件名"
    )
    # 内容与文件名由**同一个入参**派生 ⇒ 构造上不可能不一致
    assert path.name == f"alloc_{raw['date']}.json", (
        f"文件名 {path.name} 与内容里的 date {raw['date']} 分叉了"
    )
    # 原有的业务字段一个都不能少
    for k in PLAN:
        assert k in raw, f"保存 alloc 时把业务字段 `{k}` 弄丢了"


def test_an_explicit_date_in_the_plan_is_not_overwritten(tmp_path):
    """★ 调用方显式给的 date 才是权威，`setdefault` 不得覆盖它。

    「无条件写 `payload['date'] = date`」是另一种错：
    它会让**任何**绕过日期语义的调用方静默丢失自己声明的身份，
    而且不报错 —— 第 3 种形状（看起来像开关，实际焊死）。
    """
    explicit = dict(PLAN)
    explicit["date"] = "2026-01-01"          # 与文件名刻意不同
    save_allocation(tmp_path, DATE, explicit)

    raw = json.loads(_alloc_path(tmp_path, DATE).read_text(encoding="utf-8"))
    assert raw["date"] == "2026-01-01", (
        f"内容里的 date 被覆写成 {DATE} —— 调用方声明的身份被悄悄改掉了"
    )


def test_loading_a_legacy_allocation_does_not_fabricate_a_date(tmp_path):
    """② 🚨 旧文件缺 date 时**必须返回 None**，不许拿文件名日期顶替。

    静默回填是这里最容易犯的错：它让「产物没身份」这件事**永远不可见** ——
    补上之后每个消费者都拿到一个看起来合法的日期，没有任何人知道那是猜的。
    缺就是缺，让调用方自己决定怎么办。
    """
    path = _alloc_path(tmp_path, DATE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(PLAN, ensure_ascii=False), encoding="utf-8")

    got = load_allocation(tmp_path, DATE)
    assert got is not None
    assert got["date"] is None, (
        f"缺 date 的旧文件被回填成了 {got['date']!r} —— "
        "静默回填会把「产物没身份」变成永远查不出来的事实"
    )
    # 但业务字段照常可读 —— 不能因为缺一个身份字段就整份读不出来
    assert got["positions"] == PLAN["positions"]


def test_the_api_reads_the_date_from_the_content_not_the_filename(tmp_path):
    """③ ★ 接口必须**优先用内容里的日期**，文件名只作旧文件回退。

    内容有 date 而文件名日期不同时，响应里的 date 必须是**内容**那个 ——
    那是写盘方声明的身份，文件名只是索引。
    """
    import pandas as pd  # noqa: PLC0415
    from fastapi.testclient import TestClient  # noqa: PLC0415

    from aplus.webapp import create_app  # noqa: PLC0415

    # `create_app(data_root)` 固定用 `data_root/reports` 与 `data_root/chat.db`；
    # `latest_prediction_date` 认的是 `reports/predictions/pred_<date>.parquet`。
    reports = tmp_path / "reports"
    save_allocation(reports, DATE, dict(PLAN))
    preds = reports / "predictions"
    preds.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"date": [DATE]}).to_parquet(preds / f"pred_{DATE}.parquet")

    app = create_app(tmp_path, llm=object())
    r = TestClient(app).get("/api/allocation/latest")
    assert r.status_code == 200, r.text
    assert r.json()["date"] == DATE, (
        f"接口返回的日期是 {r.json()['date']!r}，内容里的是 {DATE!r}"
    )


def test_the_api_falls_back_to_the_filename_only_for_legacy_files(tmp_path):
    """③b 旧文件（内容无 date）才回退到文件名日期。

    这是**有意的**回退，不是静默编造：新写出的文件一律走内容。
    """
    import pandas as pd  # noqa: PLC0415
    from fastapi.testclient import TestClient  # noqa: PLC0415

    from aplus.webapp import create_app  # noqa: PLC0415

    reports = tmp_path / "reports"
    path = _alloc_path(reports, DATE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(PLAN, ensure_ascii=False), encoding="utf-8")
    preds = reports / "predictions"
    preds.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"date": [DATE]}).to_parquet(preds / f"pred_{DATE}.parquet")

    app = create_app(tmp_path, llm=object())
    r = TestClient(app).get("/api/allocation/latest")
    assert r.status_code == 200, r.text
    assert r.json()["date"] == DATE, (
        "旧文件应当回退到文件名日期 —— 界面不能因为产物没身份就渲染空白"
    )

"""主源会话死亡时要熔断，而不是拿 961 只股票去换 961 次注定失败的请求。

## 真实事故（2026-10-12 首轮全量修复）

```
修复完成：填上 2140｜未填 961｜异常 0
```

961 只**全部**是 `status=degraded`，且起点精确落在第 2141 只——
baostock 会话在第 2140 只之后断开，之后每次调用都由腾讯备源顶。
备源只有 OHLCV，**填不上要补的五列**，于是每一只都白跑一次网络往返，
必然失败，耗时约 16 分钟。

熔断的判据是「连续 N 次 `degraded`」而不是「累计失败 N 次」：
累计失败会被成功穿插稀释，连续失败才说明**会话整体**已经死了。
中间成功一次就重置计数。

## 本测试要钉住的两件事

1. 连续降级到阈值就停，且明确告诉人「还剩多少只没处理」；
2. 中间只要成功一次，计数就归零——不能因为累计数到过阈值就误熔断。

全程离线：把 `default_sources` 换成永远返回 degraded 的假源。
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from aplus.coverage import tail_nan_columns  # noqa: E402
import repair_tail_cols as rtc  # noqa: E402


def _seed(tmp_path, n=12):
    """造 n 只末根缺列的股票（正是待修的样子）。"""
    root = tmp_path / "master" / "daily"
    root.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        df = pd.DataFrame({
            "date": ["2026-10-08", "2026-10-09"],
            "code": f"60000{i}.SH", "open": 10.0, "high": 10.2,
            "low": 9.9, "close": 10.0, "volume": [1e6, 1.1e6],
        })
        for col in ("amount", "tradestatus", "isST", "turn", "pctChg"):
            df[col] = [1.0, float("nan")]            # 末根缺，复现待修状态
        df.to_parquet(root / f"60000{i}.SH.parquet")
    return root


def _patch(monkeypatch, tmp_path, behave):
    """behave(code, call_index) -> 'ok' | 'degraded'。"""
    import aplus.store as store_mod

    class FakeSource:
        def fetch_adjust_factor(self, code, start, end):
            return pd.DataFrame()

        def fetch_daily(self, code, start, end):
            return pd.DataFrame()

        def close(self):
            pass

    calls: list = []

    class FakePipeline:
        def __init__(self, *a, **k):
            pass

        def sync_daily(self, code, start, end):
            verdict = behave(code, len(calls))
            calls.append(code)
            path = tmp_path / "master" / "daily" / f"{code}.parquet"
            if verdict == "ok":
                df = pd.read_parquet(path)
                for col in ("amount", "tradestatus", "isST", "turn", "pctChg"):
                    df.loc[df.index[-1], col] = 1.0
                df.to_parquet(path)
            return type("R", (), {"status": verdict, "reason": ""})()

    monkeypatch.setattr(rtc, "default_sources", lambda: [FakeSource()])
    monkeypatch.setattr(store_mod, "LocalStore",
                        lambda r: type("S", (), {"root": tmp_path})())
    monkeypatch.setattr(rtc, "SyncPipeline", FakePipeline)
    monkeypatch.setattr(rtc.time, "sleep", lambda s: None)
    return calls


def _run(monkeypatch, tmp_path, argv):
    monkeypatch.setattr(sys, "argv", ["repair_tail_cols.py"] + argv)
    return rtc.main()


def test_stops_after_consecutive_degraded(monkeypatch, tmp_path, capsys):
    _seed(tmp_path, n=12)
    calls = _patch(monkeypatch, tmp_path,
                   lambda code, i: "degraded")          # 永远降级
    _run(monkeypatch, tmp_path,
         ["--data-root", str(tmp_path), "--degrade-limit", "3"])
    out = capsys.readouterr().out
    assert len(calls) == 3, f"阈值 3 就该停，实际调了 {len(calls)} 次"
    assert "熔断" in out, "必须明说熔断了，不能只是安静地少做"
    assert "已放弃" in out, "必须告诉人还剩多少只没处理"


def test_success_resets_the_streak(monkeypatch, tmp_path, capsys):
    """成功一次就归零——累计失败数到过阈值也不许熔断。"""
    _seed(tmp_path, n=12)
    # 失败、成功、失败、成功……永远到不了连续 3 次
    calls = _patch(monkeypatch, tmp_path,
                   lambda code, i: "degraded" if i % 2 == 0 else "ok")
    _run(monkeypatch, tmp_path,
         ["--data-root", str(tmp_path), "--degrade-limit", "3"])
    out = capsys.readouterr().out
    assert "熔断" not in out, "交替成败不构成会话死亡，不该熔断"
    assert len(calls) == 12, f"12 只都该处理，实际 {len(calls)}"


def test_quarantine_is_not_session_death(monkeypatch, tmp_path, capsys):
    """「未填上」的原因有两种，只有一种是会话死亡。

    `degraded` = 主源挂了、备源顶上（会话死亡）；
    `quarantined` = 数据过了 sanity/cross-check 被拒（源还活着，是数据不合格）。

    把两者混为一谈，一次批量数据质量事件就会伪装成「源站挂了」并误熔断，
    白白放弃后面几百只。
    """
    _seed(tmp_path, n=12)
    calls = _patch(monkeypatch, tmp_path,
                   lambda code, i: "quarantined")        # 源活着，数据不合格
    _run(monkeypatch, tmp_path,
         ["--data-root", str(tmp_path), "--degrade-limit", "3"])
    out = capsys.readouterr().out
    assert "熔断" not in out, "隔离不是会话死亡，不该熔断"
    assert len(calls) == 12, f"12 只都该处理，实际 {len(calls)}"


def test_degraded_then_success_then_degraded_does_not_trip(monkeypatch, tmp_path, capsys):
    """降级 → 成功 → 降级：中间那次成功证明会话还活着，不许熔断。"""
    _seed(tmp_path, n=12)
    calls = _patch(monkeypatch, tmp_path,
                   lambda code, i: "degraded" if i in (0, 2) else "ok")
    _run(monkeypatch, tmp_path,
         ["--data-root", str(tmp_path), "--degrade-limit", "3"])
    out = capsys.readouterr().out
    assert "熔断" not in out
    assert len(calls) == 12


def test_reports_progress_not_just_count(monkeypatch, tmp_path, capsys):
    """熔断时必须报「已填 / 未填 / 异常」三样，而不只是停了。"""
    _seed(tmp_path, n=6)
    _patch(monkeypatch, tmp_path, lambda code, i: "degraded")
    _run(monkeypatch, tmp_path,
         ["--data-root", str(tmp_path), "--degrade-limit", "2"])
    out = capsys.readouterr().out
    assert "修复完成" in out
    for token in ("填上", "未填", "异常"):
        assert token in out, f"汇总缺 {token}"


def test_quarantine_still_reported_as_unresolved(monkeypatch, tmp_path, capsys):
    """隔离的股票必须进「未填」清单——熔断不触发不等于可以装作没这回事。"""
    _seed(tmp_path, n=4)
    _patch(monkeypatch, tmp_path, lambda code, i: "quarantined")
    _run(monkeypatch, tmp_path,
         ["--data-root", str(tmp_path), "--degrade-limit", "3"])
    out = capsys.readouterr().out
    assert "未填 4" in out, f"4 只被隔离却没报成未填：\n{out[-400:]}"


def test_releases_lock_on_abort(monkeypatch, tmp_path):
    """熔断是 break 出循环，锁仍必须释放——否则后面谁都跑不了。"""
    _seed(tmp_path, n=6)
    _patch(monkeypatch, tmp_path, lambda code, i: "degraded")
    _run(monkeypatch, tmp_path,
         ["--data-root", str(tmp_path), "--degrade-limit", "2"])
    assert not (tmp_path / "bootstrap.lock").exists(), "熔断后锁没释放"

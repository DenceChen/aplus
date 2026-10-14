"""历史预测与验证的装配层：跨日预测 + 验证判定的合并视图。

- prediction_history: 汇总 reports_dir/predictions/pred_*.parquet，左连
  verification.parquet（按 pred_date+code），产出前端可直接消费的历史表。
- hit_rate_by_date: 按预测日聚合命中率（flat/no_data 不计，judged=0 → None）。

注意：verification.parquet 由 verify_predictions 产出，除判定列外还带着预测
快照列（name/score/...），合并前必须先裁剪，否则会产生 _x/_y 后缀列。
"""
from pathlib import Path

import pandas as pd

HISTORY_COLUMNS = [
    "pred_date", "code", "name", "direction", "score", "mom20", "actual_pct", "result",
]


def prediction_history(reports_dir) -> pd.DataFrame:
    """全部历史预测 + 验证判定，按 pred_date 降序、同日 score 降序。

    未验证行 result/actual_pct 为 None（NaN 语义，但保证 JSON 可序列化）。
    """
    pred_dir = Path(reports_dir) / "predictions"
    frames = [pd.read_parquet(p) for p in sorted(pred_dir.glob("pred_*.parquet"))]
    if not frames:
        return pd.DataFrame(columns=HISTORY_COLUMNS)
    preds = pd.concat(frames, ignore_index=True)

    ver_path = Path(reports_dir) / "verification.parquet"
    if ver_path.exists():
        ver = pd.read_parquet(ver_path)
        ver = ver[["pred_date", "code", "result", "actual_pct"]]
        preds = preds.merge(ver, on=["pred_date", "code"], how="left")
    else:
        preds["actual_pct"] = None
        preds["result"] = None

    out = preds[HISTORY_COLUMNS].sort_values(
        ["pred_date", "score"], ascending=[False, False]
    ).reset_index(drop=True)
    # NaN → None：未验证行保持空值语义，同时 to_dict/JSON 侧不出 NaN
    for col in ("actual_pct", "result"):
        out[col] = out[col].astype(object).where(out[col].notna(), None)
    return out


def hit_rate_by_date(reports_dir) -> list[dict]:
    """按预测日聚合：[{date, judged, hit_rate}]，日期降序。

    judged 只计 hit+miss（flat/no_data 排除）；judged=0（未验证日）hit_rate=None。
    """
    history = prediction_history(reports_dir)
    if history.empty:
        return []
    out = []
    for date, sub in history.groupby("pred_date", sort=False):
        judged = int(sub["result"].isin(["hit", "miss"]).sum())
        hit = int((sub["result"] == "hit").sum())
        out.append({"date": date, "judged": judged, "hit_rate": hit / judged if judged else None})
    return sorted(out, key=lambda r: r["date"], reverse=True)

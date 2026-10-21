#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════
# 每周一 08:05 的复盘（7 天窗口的提交归类与跑偏率）。
#
# 🚨 **为什么单独一个脚本**：cron 那条原本写的是
#    `--since "$(date -v-7d '+%Y-%m-%d %H:%M:%S')"` ——
#    那是**命令替换** ⇒ launchd 的 `ProgramArguments` 逐词传参、**不做替换**
#    ⇒ 必须有一个壳来展开它。
#
# 🚨 **不许后台化**：后台化会制造「已完成」的假象（第 8 层）。
# ══════════════════════════════════════════════════════════════════════
set -euo pipefail

cd /Users/airboom/opencode/aplus
.venv/bin/python scripts/daily_review.py \
  --since "$(date -v-7d '+%Y-%m-%d %H:%M:%S')" \
  --out data/reports/daily_review_weekly.json \
  --archive-prefix review_weekly

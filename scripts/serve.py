"""Web 服务器启动入口（本机部署）。

用法：.venv/bin/python scripts/serve.py   →  http://127.0.0.1:8788
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn

from aplus.webapp import create_app

app = create_app(Path(__file__).resolve().parent.parent / "data")

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8788, log_level="warning")

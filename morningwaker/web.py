"""本地仪表盘：stdlib HTTP 服务，静态页 + 两个只读 JSON 接口。

  GET /              web/index.html
  GET /api/days      journal 按日聚合（演习不计）
  GET /api/status    今天的进度 + 配置摘要
"""
from __future__ import annotations

import json
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from . import journal
from .config import ROOT, load
from .daemon import DAY_FILE

WEB_DIR = ROOT / "web"


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(WEB_DIR), **kw)

    def log_message(self, *a):                      # 安静
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _json(self, obj) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/api/days"):
            return self._json(journal.days())
        if self.path.startswith("/api/status"):
            cfg = load()
            today = {}
            if DAY_FILE.exists():
                try:
                    today = json.loads(DAY_FILE.read_text())
                except ValueError:
                    pass
            return self._json({
                "now": datetime.now(cfg.tz).strftime("%Y-%m-%d %H:%M"),
                "tz": cfg.tz.key,
                "wake": "%02d:%02d" % cfg.wake_time,
                "steps": [{"after_min": s.after_min, "action": s.action} for s in cfg.steps],
                "today": today,
            })
        if self.path == "/":
            self.path = "/index.html"
        return super().do_GET()


def serve(port: int = 5180) -> None:
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"MorningWaker 仪表盘：http://127.0.0.1:{port}  （Ctrl+C 停止）")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass

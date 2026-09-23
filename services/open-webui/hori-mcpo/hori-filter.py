#!/usr/bin/env python3
"""Read-only allowlist proxy in front of mcpo (hori MCP bridge).

Open-WebUI fetches /openapi.json from the configured tool-server URL and
advertises every path in it to the model. This proxy serves a spec containing
only the five read-only state tools and forwards calls for exactly those
paths; everything else gets 404. mcpo itself stays on 127.0.0.1:8774.
"""
import json
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

UPSTREAM = "http://127.0.0.1:8774/hori"
LISTEN = ("127.0.0.1", 8775)
ALLOWED = ["/thread_list", "/thread_read", "/episode_search",
           "/memory_search"]

# mcpo runs --api-key/--strict-auth (2026-09-23): unauthenticated direct
# hits on :8774 get 401. The key lives in mcpo.env (0600); this proxy is
# the only sanctioned caller.
def _api_key() -> str:
    env = Path.home() / ".config/mcpo/mcpo.env"
    for line in env.read_text().splitlines():
        if line.startswith("MCPO_API_KEY="):
            return line.split("=", 1)[1].strip()
    return ""


_AUTH = {"Authorization": f"Bearer {_api_key()}"}


def upstream_get(path):
    req = urllib.request.Request(UPSTREAM + path, headers=_AUTH)
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read()


def upstream_post(path, body):
    req = urllib.request.Request(UPSTREAM + path, data=body,
                                 headers={"Content-Type": "application/json",
                                          **_AUTH})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read(), r.headers.get("Content-Type", "application/json")


def filtered_spec():
    spec = json.loads(upstream_get("/openapi.json"))
    spec["paths"] = {k: v for k, v in spec["paths"].items() if k in ALLOWED}
    # mcpo emits operationIds like "tool_thread_list_post"; rewrite to the
    # clean tool name so OWUI exposes "thread_list" (matching other servers
    # and the connection's function_name_filter_list).
    for path, ops in spec["paths"].items():
        for op in ops.values():
            if isinstance(op, dict) and "operationId" in op:
                op["operationId"] = path.lstrip("/")
    spec["info"]["title"] = "HORI read-only state tools"
    return json.dumps(spec).encode()


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/openapi.json":
            self._send(200, filtered_spec())
        else:
            self._send(404, b'{"detail":"Not Found"}')

    def do_POST(self):
        if self.path not in ALLOWED:
            self._send(404, b'{"detail":"Not Found"}')
            return
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        try:
            data, ctype = upstream_post(self.path, body)
            self._send(200, data, ctype)
        except urllib.error.HTTPError as e:
            self._send(e.code, e.read())
        except Exception as e:
            self._send(502, json.dumps({"detail": str(e)}).encode())

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(LISTEN, Handler).serve_forever()

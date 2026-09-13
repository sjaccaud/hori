#!/usr/bin/env python3
"""workspace-tools — confirmation-gated Markdown/directory writes for Open-WebUI.

Exposes a small OpenAPI surface over ``aios_core.workspace_writes``:
- propose_workspace_write: validate a directory/Markdown manifest and return a token.
- confirm_workspace_write: write the manifest only after explicit confirmation.
- rollback_workspace_write: remove recently created paths within the rollback window.

Design:
- Single-file, stdlib HTTP server on 127.0.0.1:8771.
- All writes are confined to WORKSPACE_TOOLS_ROOT (default ~/Projects).
- Only directories and .md files may be created; no overwrite/delete tools.
- The tool server is intended for selected models only (initially qwen-deepdive).
"""
from __future__ import annotations

import json
import logging
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from services.aios_core.workspace_writes import (  # noqa: E402
    confirm_workspace_write,
    propose_workspace_write,
    rollback_workspace_write,
)

PORT = int(os.environ.get("WORKSPACE_TOOLS_PORT", "8771"))
HOST = os.environ.get("WORKSPACE_TOOLS_HOST", "127.0.0.1")
MAX_HTTP_BODY_BYTES = 2 * 1024 * 1024

log = logging.getLogger("workspace-tools")
log.setLevel(logging.INFO)
if not log.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    log.addHandler(handler)


ENTRY_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": (
                "Relative path under the workspace root; .md required for files."
            ),
        },
        "is_dir": {
            "type": "boolean",
            "description": "Create this path as a directory.",
        },
        "content": {
            "type": "string",
            "description": "Markdown content for file entries.",
        },
    },
    "required": ["path"],
}

OPENAPI_SPEC: dict[str, Any] = {
    "openapi": "3.0.0",
    "info": {
        "id": "workspace-tools",
        "title": "HORI Workspace Tools",
        "version": "0.1.0",
        "description": (
            "Confirmation-gated directory and Markdown writes under the "
            "configured Projects workspace."
        ),
    },
    "servers": [{"url": f"http://{HOST}:{PORT}"}],
    "paths": {
        "/propose_workspace_write": {
            "post": {
                "operationId": "propose_workspace_write",
                "summary": (
                    "Validate a directory/Markdown manifest under the workspace "
                    "root and return a confirmation token."
                ),
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "description": {"type": "string"},
                                    "entries": {"type": "array", "items": ENTRY_SCHEMA},
                                },
                                "required": ["description", "entries"],
                            }
                        }
                    },
                },
                "responses": {
                    "200": {
                        "description": "Dry-run manifest and confirmation token",
                        "content": {"application/json": {"schema": {"type": "object"}}},
                    }
                },
            }
        },
        "/confirm_workspace_write": {
            "post": {
                "operationId": "confirm_workspace_write",
                "summary": (
                    "Write a previously proposed workspace manifest after "
                    "explicit confirmation."
                ),
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "token": {"type": "string"},
                                    "confirmed": {"type": "boolean"},
                                },
                                "required": ["token"],
                            }
                        }
                    },
                },
                "responses": {
                    "200": {
                        "description": "Write result or dry-run manifest",
                        "content": {"application/json": {"schema": {"type": "object"}}},
                    }
                },
            }
        },
        "/rollback_workspace_write": {
            "post": {
                "operationId": "rollback_workspace_write",
                "summary": (
                    "Remove paths created by a recent confirmed workspace write."
                ),
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {"token": {"type": "string"}},
                                "required": ["token"],
                            }
                        }
                    },
                },
                "responses": {
                    "200": {
                        "description": "Rollback result",
                        "content": {"application/json": {"schema": {"type": "object"}}},
                    }
                },
            }
        },
    },
}

TOOLS: dict[str, Any] = {
    "propose_workspace_write": propose_workspace_write,
    "confirm_workspace_write": confirm_workspace_write,
    "rollback_workspace_write": rollback_workspace_write,
}


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, code: int, data: Any) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        payload = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.wfile.write(payload)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json(200, {"status": "ok"})
        elif self.path == "/openapi.json":
            self._send_json(200, OPENAPI_SPEC)
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        tool_name = self.path.strip("/").split("/")[0]
        if tool_name not in TOOLS:
            self._send_json(404, {"error": f"unknown tool: {tool_name}"})
            return

        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            self._send_json(400, {"error": "invalid Content-Length"})
            return
        if length > MAX_HTTP_BODY_BYTES:
            self.close_connection = True
            self._send_json(413, {"error": "request body too large"})
            return

        try:
            body = self.rfile.read(length).decode("utf-8") if length else "{}"
            args = json.loads(body) if body else {}
        except (json.JSONDecodeError, ValueError) as e:
            self._send_json(400, {"error": f"invalid request: {e}"})
            return

        log.info("POST /%s", tool_name)
        try:
            result = TOOLS[tool_name](**args)
        except TypeError as e:
            self._send_json(422, {"error": f"invalid tool arguments: {e}"})
            return
        self._send_json(200 if "error" not in result else 422, result)

    def log_message(self, fmt: str, *args: Any) -> None:
        log.info(fmt, *args)


def main() -> None:
    server = HTTPServer((HOST, PORT), Handler)
    log.info("workspace-tools listening on http://%s:%d", HOST, PORT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

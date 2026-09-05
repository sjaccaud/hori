#!/usr/bin/env python3
"""project-tools — OpenAPI tool server for project creation in Open-WebUI.

Exposes a safe, auditable project-creation workflow as Open-WebUI tools:
- propose_create_project: generate a dry-run manifest and confirmation token.
- confirm_create_project: create the project when confirmed.
- rollback_project: remove a recently created project within the rollback window.

Design:
- Single-file, stdlib HTTP server (mirrors local_tools_server.py).
- Runs as the normal user on 127.0.0.1:8769.
- All writes are confined to ~/Projects (or AIOS_PROJECTS_DIR).
- Every proposal/creation/rollback is written to the aios_core audit log.
- Open-WebUI can call these as function-calling tools; the user can inspect
  the manifest before confirming.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

# Ensure the repo root is on the path so we can import aios_core modules.
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from services.aios_core.project_creation import (
    create_project,
    propose_project,
    rollback_project,
)

PORT = int(os.environ.get("PROJECT_TOOLS_PORT", "8769"))
HOST = os.environ.get("PROJECT_TOOLS_HOST", "127.0.0.1")

log = logging.getLogger("project-tools")
log.setLevel(logging.INFO)
if not log.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    log.addHandler(handler)


def _tool_schema(name: str, desc: str, params: dict[str, Any]) -> dict[str, Any]:
    return {
        "function": {
            "name": name,
            "description": desc,
            "parameters": {
                "type": "object",
                "properties": params,
                "required": [k for k, v in params.items() if v.get("required")],
            },
        },
    }


OPENAPI_SPEC: dict[str, Any] = {
    "openapi": "3.0.0",
    "info": {
        "id": "project-tools",
        "title": "HORI Project Tools",
        "version": "0.1.0",
        "description": "Safe project-creation tools with dry-run manifest and explicit confirmation.",
    },
    "servers": [{"url": f"http://{HOST}:{PORT}"}],
    "paths": {
        "/propose_create_project": {
            "post": {
                "operationId": "propose_create_project",
                "summary": "Propose a new project under ~/Projects and return a dry-run manifest and confirmation token.",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string", "description": "Project name or short description", "required": True},
                                    "description": {"type": "string", "description": "Longer project description"},
                                    "parent_dir": {"type": "string", "description": "Parent directory (defaults to ~/Projects)"},
                                    "template": {"type": "string", "description": "Template: empty, python-cli, python-package, fastapi-app, data-science, static-site"},
                                },
                                "required": ["name"],
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
        "/confirm_create_project": {
            "post": {
                "operationId": "confirm_create_project",
                "summary": "Create a project from a previously generated proposal token.",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "token": {"type": "string", "description": "Confirmation token from propose_create_project", "required": True},
                                    "confirmed": {"type": "boolean", "description": "Set to true to write files"},
                                },
                                "required": ["token"],
                            }
                        }
                    },
                },
                "responses": {
                    "200": {
                        "description": "Creation result or dry-run manifest",
                        "content": {"application/json": {"schema": {"type": "object"}}},
                    }
                },
            }
        },
        "/rollback_project": {
            "post": {
                "operationId": "rollback_project",
                "summary": "Roll back a recently created project within the rollback window.",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "token": {"type": "string", "description": "Creation token", "required": True},
                                },
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
    "propose_create_project": propose_project,
    "confirm_create_project": create_project,
    "rollback_project": rollback_project,
}


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, code: int, data: Any) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False, default=str).encode("utf-8"))

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
            body = self.rfile.read(length).decode("utf-8") if length else "{}"
            args = json.loads(body) if body else {}
        except (json.JSONDecodeError, ValueError) as e:
            self._send_json(400, {"error": f"invalid request: {e}"})
            return

        log.info("POST /%s args=%s", tool_name, args)
        result = TOOLS[tool_name](**args)
        # 200 for success or dry-run, 422 for errors
        self._send_json(200 if "error" not in result else 422, result)

    def log_message(self, fmt: str, *args: Any) -> None:
        log.info(fmt, *args)


def main() -> None:
    server = HTTPServer((HOST, PORT), Handler)
    log.info("project-tools listening on http://%s:%d", HOST, PORT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

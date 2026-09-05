#!/usr/bin/env python3
"""Quick, non-sudo health check for the local HORI/AIOS spine.

Checks the endpoints and systemd services that an everyday assistant needs,
without requiring root. Run via `make health`.
"""
from __future__ import annotations

import json
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Any

GREEN = "\033[0;32m"
YELLOW = "\033[0;33m"
RED = "\033[0;31m"
RESET = "\033[0m"

ENDPOINTS = [
    ("aios-core", "http://127.0.0.1:5680/health", True),
    ("llama-server", "http://127.0.0.1:8080/health", True),
    ("local-tools", "http://127.0.0.1:8766/health", True),
    ("project-tools", "http://127.0.0.1:8769/health", True),
    ("open-webui", "http://127.0.0.1:3000/health", True),
    ("searchrouter", "http://127.0.0.1:8081/search?q=health&format=json", True),
    ("embedding", "http://127.0.0.1:8082/", False),
    ("qdrant", "http://127.0.0.1:6333/", False),
]

SERVICES = [
    ("aios_core", True),
    ("llamacpp", True),
    ("aios-sherpa", True),
    ("aios-tool-daemon", True),
    ("local-tools", True, "--user"),
    ("project-tools", True, "--user"),
]


def color(msg: str, color: str) -> str:
    return f"{color}{msg}{RESET}"


def check_url(name: str, url: str, required: bool) -> bool:
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=4) as resp:
            body = resp.read().decode("utf-8", errors="replace")[:200]
            print(f"  {name:15} {color('OK', GREEN)} {body.strip()[:80]}")
            return True
    except urllib.error.HTTPError as e:
        # searchrouter returns 404 for /, which still means it is up.
        if e.code == 404:
            print(f"  {name:15} {color('OK', GREEN)} (404, service listening)")
            return True
        status = f"{color('FAIL', RED if required else YELLOW)} HTTP {e.code}"
        print(f"  {name:15} {status}")
        return not required
    except urllib.error.URLError as e:
        status = f"{color('FAIL', RED if required else YELLOW)} {e.reason}"
        print(f"  {name:15} {status}")
        return not required
    except Exception as e:
        status = f"{color('FAIL', RED if required else YELLOW)} {e}"
        print(f"  {name:15} {status}")
        return not required


def check_port(name: str, host: str, port: int, required: bool) -> bool:
    try:
        with socket.create_connection((host, port), timeout=2):
            print(f"  {name:15} {color('OK', GREEN)} port {port} listening")
            return True
    except Exception as e:
        print(f"  {name:15} {color('FAIL', RED if required else YELLOW)} {e}")
        return not required


def check_systemd(name: str, required: bool, extra: str | None = None) -> bool:
    cmd = ["systemctl", "is-active", name]
    if extra == "--user":
        cmd.insert(1, "--user")
    try:
        out = subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT).strip()
        print(f"  {name:15} {color(out, GREEN)}")
        return out == "active"
    except subprocess.CalledProcessError as e:
        state = e.output.strip() if e.output else "inactive"
        print(f"  {name:15} {color(state, RED if required else YELLOW)}")
        return not required


def main() -> int:
    print("=== HORI/AIOS health ===")
    ok = True

    print("\n-- Endpoints --")
    for name, url, required in ENDPOINTS:
        ok &= check_url(name, url, required)

    print("\n-- Systemd services --")
    for service in SERVICES:
        name, required = service[0], service[1]
        extra = service[2] if len(service) > 2 else None
        ok &= check_systemd(name, required, extra)

    print("\n-- Docker services --")
    try:
        out = subprocess.check_output(
            ["docker", "ps", "--filter", "name=open_webui_aios", "--format", "{{.Names}}\t{{.Status}}"],
            text=True,
            stderr=subprocess.STDOUT,
        ).strip()
        if out:
            print(f"  open-webui      {color(out, GREEN)}")
        else:
            print(f"  open-webui      {color('not running', RED)}")
            ok = False
    except Exception as e:
        print(f"  docker status   {color(f'error: {e}', RED)}")
        ok = False

    print("\n-- Quick assistant smoke test --")
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:8766/get_status",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
        if data.get("ok"):
            status = data["status"]
            loaded = status.get("loaded_model", "unknown")
            vram_pct = status.get("vram", {}).get("percent", "?")
            print(f"  get_status      {color('OK', GREEN)} model={loaded}, vram={vram_pct}%")
        else:
            print(f"  get_status      {color('FAIL', RED)} {data.get('error')}")
            ok = False
    except Exception as e:
        print(f"  get_status      {color('FAIL', RED)} {e}")
        ok = False

    print()
    if ok:
        print(color("All critical services are healthy.", GREEN))
    else:
        print(color("One or more critical services are down or unreachable.", RED))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

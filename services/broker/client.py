"""Client for the system root broker."""

from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any

from .config import BROKER_SECRETS_PATH, BROKER_SOCKET_PATH


class RootBrokerClient:
    """Send requests to the root broker over its Unix domain socket."""

    def __init__(
        self,
        socket_path: str | Path = BROKER_SOCKET_PATH,
        secrets_path: str | Path = BROKER_SECRETS_PATH,
        token: str | None = None,
    ):
        self.socket_path = Path(socket_path)
        self._token = token or self._load_token(secrets_path)

    def _load_token(self, secrets_path: str | Path) -> str | None:
        path = Path(secrets_path)
        if not path.exists():
            return None
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("ROOT_BROKER_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"')
        return None

    def call(
        self, operation: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Call a broker operation and return the JSON response."""
        if self._token is None:
            raise RuntimeError("ROOT_BROKER_TOKEN not found")

        request = {
            "request_id": self._make_request_id(),
            "token": self._token,
            "operation": operation,
            "params": params or {},
        }

        data = json.dumps(request, ensure_ascii=False).encode("utf-8") + b"\n"

        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.connect(str(self.socket_path))
            sock.sendall(data)
            chunks: list[bytes] = []
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
                if b"\n" in chunk:
                    break

        response_data = b"".join(chunks).split(b"\n", 1)[0]
        return json.loads(response_data.decode("utf-8"))

    @staticmethod
    def _make_request_id() -> str:
        import uuid

        return str(uuid.uuid4())

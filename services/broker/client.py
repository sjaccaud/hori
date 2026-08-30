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


def _main() -> None:
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Root broker command-line client.")
    parser.add_argument(
        "operation",
        choices=[
            "service_status",
            "service_restart",
            "service_stop",
            "journalctl_unit",
        ],
        help="Broker operation to call.",
    )
    parser.add_argument("--service", type=str, help="Service name.")
    parser.add_argument("--unit", type=str, help="Journald unit name.")
    parser.add_argument("--lines", type=int, help="Number of journal lines.")
    parser.add_argument(
        "--since", type=str, help="Signed relative time window (e.g. -1h)."
    )
    parser.add_argument(
        "--socket", type=str, default=BROKER_SOCKET_PATH, help="Broker socket path."
    )
    parser.add_argument(
        "--secrets", type=str, default=BROKER_SECRETS_PATH, help="Broker secrets file."
    )
    args = parser.parse_args()

    params: dict[str, Any] = {}
    if args.service:
        params["service"] = args.service
    if args.unit:
        params["unit"] = args.unit
    if args.lines is not None:
        params["lines"] = args.lines
    if args.since:
        params["since"] = args.since

    client = RootBrokerClient(socket_path=args.socket, secrets_path=args.secrets)
    response = client.call(args.operation, params)
    print(json.dumps(response, indent=2))


if __name__ == "__main__":
    _main()

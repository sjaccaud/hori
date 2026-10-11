#!/usr/bin/env python3
"""Patch Open-WebUI's aiohttp session pool to use a larger read buffer.

Open-WebUI's shared aiohttp ClientSession uses the default 64 KiB read buffer.
When llama-server streams a fast or tool-heavy response, a single SSE ``data:``
line can exceed the default 128 KiB ``max_line_length`` and raise
``aiohttp.http_exceptions.LineTooLong``.

This script patches ``/app/backend/open_webui/utils/session_pool.py`` to:

- Read ``AIOHTTP_POOL_READ_BUFSIZE`` from the environment (default 1 MiB).
- Pass ``read_bufsize`` to ``aiohttp.ClientSession``.

It is idempotent and designed to run inside the container via
``apply-model-config.sh`` after each container restart.
"""

import os
import sys
from pathlib import Path

SESSION_POOL_PATH = Path("/app/backend/open_webui/utils/session_pool.py")

OLD_SESSION = """        _session = aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            trust_env=True,
        )"""


def main() -> int:
    if not SESSION_POOL_PATH.exists():
        print(f"ERROR: {SESSION_POOL_PATH} not found", file=sys.stderr)
        return 1

    text = SESSION_POOL_PATH.read_text()

    if "AIOHTTP_POOL_READ_BUFSIZE" in text:
        print("session_pool.py already patched; nothing to do.")
        return 0

    # Ensure os is imported
    if "import os" not in text:
        text = text.replace("import logging", "import logging\nimport os")

    # Add the env variable near the other AIOHTTP_POOL_* imports
    old_imports = """from open_webui.env import (
    AIOHTTP_CLIENT_STREAM_IDLE_TIMEOUT,
    AIOHTTP_CLIENT_TIMEOUT,
    AIOHTTP_POOL_CONNECTIONS,
    AIOHTTP_POOL_CONNECTIONS_PER_HOST,
    AIOHTTP_POOL_DNS_TTL,
)"""

    new_imports = """from open_webui.env import (
    AIOHTTP_CLIENT_STREAM_IDLE_TIMEOUT,
    AIOHTTP_CLIENT_TIMEOUT,
    AIOHTTP_POOL_CONNECTIONS,
    AIOHTTP_POOL_CONNECTIONS_PER_HOST,
    AIOHTTP_POOL_DNS_TTL,
)

AIOHTTP_POOL_READ_BUFSIZE = int(os.environ.get("AIOHTTP_POOL_READ_BUFSIZE") or 2**20)"""

    if old_imports in text:
        text = text.replace(old_imports, new_imports)
    else:
        # Fallback: append after the other AIOHTTP variables if the block changed shape
        text = text.replace(
            "AIOHTTP_POOL_DNS_TTL,\n)",
            "AIOHTTP_POOL_DNS_TTL,\n)\n\nAIOHTTP_POOL_READ_BUFSIZE = int(os.environ.get(\"AIOHTTP_POOL_READ_BUFSIZE\") or 2**20)",
        )

    new_session = """        _session = aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            trust_env=True,
            read_bufsize=AIOHTTP_POOL_READ_BUFSIZE,
        )"""

    if OLD_SESSION not in text:
        print(
            "ERROR: could not find the aiohttp.ClientSession call to patch; "
            "the upstream session_pool.py may have changed.",
            file=sys.stderr,
        )
        return 1

    text = text.replace(OLD_SESSION, new_session)

    SESSION_POOL_PATH.write_text(text)
    print("session_pool.py patched for larger aiohttp read buffer.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Adversarial test: path-escape defense for system_state_sync.

The system_state_sync module reads canonical files (AGENTS.md, router.ini,
benchmark results) from known local paths. An untrusted caller could pass a
malicious path (e.g. /etc/shadow) and exfiltrate or process arbitrary files.

This test defines the TDD contract: render_system_state_md must refuse to
read any input path outside the configured allow-list. It must fail before
the defense is implemented and pass once path validation is in place.

Defends: the local file read surface in services.open_webui.system_state_sync.
"""

from pathlib import Path

import pytest


def test_render_rejects_benchmark_dir_outside_allow_list():
    """A benchmark_dir outside the configured allow-list must raise ValueError."""
    from services.open_webui.system_state_sync import render_system_state_md

    home = Path.home()
    with pytest.raises(ValueError):
        render_system_state_md(
            benchmark_dir=Path("/etc"),
            agents_path=home / "AGENTS.md",
            router_path=home / "llama-builds/spiritbuun-714/router.ini",
        )


def test_render_rejects_agents_path_outside_allow_list():
    """An agents_path outside the configured allow-list must raise ValueError."""
    from services.open_webui.system_state_sync import render_system_state_md

    home = Path.home()
    with pytest.raises(ValueError):
        render_system_state_md(
            benchmark_dir=home / "Projects/benchmarks/qwen3",
            agents_path=Path("/etc/passwd"),
            router_path=home / "llama-builds/spiritbuun-714/router.ini",
        )


def test_render_rejects_router_path_outside_allow_list():
    """A router_path outside the configured allow-list must raise ValueError."""
    from services.open_webui.system_state_sync import render_system_state_md

    home = Path.home()
    with pytest.raises(ValueError):
        render_system_state_md(
            benchmark_dir=home / "Projects/benchmarks/qwen3",
            agents_path=home / "AGENTS.md",
            router_path=Path("/etc/shadow"),
        )

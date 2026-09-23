"""Adversarial test: path-escape defense for system_state_sync.

The system_state_sync module reads curated local files (manifesto, safety,
roadmap, project-decisions) from known paths. An untrusted caller could
pass a malicious source path (e.g. /etc/shadow) and exfiltrate or process
arbitrary files.

This test defines the contract: render_project_decisions_md must refuse to
read any source path outside the configured allow-list.

(Previously covered the retired render_system_state_md; retargeted to the
remaining renderer on 2026-09-23 — the _validate_path defense is shared.)

Defends: the local file read surface in services.open_webui.system_state_sync.
"""

from pathlib import Path

import pytest


def test_render_rejects_source_outside_allow_list():
    """A source path outside the configured allow-list must raise ValueError."""
    from services.open_webui.system_state_sync import render_project_decisions_md

    with pytest.raises(ValueError):
        render_project_decisions_md(sources=[Path("/etc/shadow")])


def test_render_rejects_source_traversal():
    """Traversal tricks (.. segments) resolve before the allow-list check."""
    from services.open_webui.system_state_sync import render_project_decisions_md

    home = Path.home()
    sneaky = home / "Projects/aios/docs/../../../etc/passwd"
    with pytest.raises(ValueError):
        render_project_decisions_md(sources=[sneaky])


def test_render_rejects_mixed_valid_and_invalid_sources():
    """One bad source poisons the whole render — no partial reads."""
    from services.open_webui.system_state_sync import render_project_decisions_md

    home = Path.home()
    valid = home / "Projects/aios/docs/manifesto.md"
    with pytest.raises(ValueError):
        render_project_decisions_md(
            sources=[valid, Path("/etc/gshadow")]
        )

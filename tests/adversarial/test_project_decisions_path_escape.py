"""Adversarial test: project-decisions source path escape.

The system_state_sync module reads a list of source files for
render_project_decisions_md. An untrusted caller could pass a malicious
source (e.g. /etc/shadow) and exfiltrate or process arbitrary files.

This test defines the TDD contract: render_project_decisions_md must
refuse to read any source outside the configured allow-list. It must fail
before the safety is implemented and pass once the safety is built, per
Pillar VII.

Defends: the local file read surface for project-decisions in
services.open_webui.system_state_sync.
"""

from pathlib import Path

import pytest


def test_render_project_decisions_rejects_source_outside_allow_list(tmp_path):
    """A malicious source path outside the allow-list must raise ValueError."""
    from services.open_webui.system_state_sync import render_project_decisions_md

    good = tmp_path / "good.md"
    good.write_text("# good\n")
    bad = Path("/etc/shadow")

    with pytest.raises(ValueError):
        render_project_decisions_md(
            sources=[good, bad],
            allow_list=[tmp_path],
        )

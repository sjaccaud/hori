"""Tests for intent hierarchy tooling.

Tests work order creation, validation, Yes-AND compliance, and intent drift
detection — all without touching aios-core.
"""
import json
import tempfile
from pathlib import Path

import pytest

from scripts.intent_tooling import (
    IntentManager,
    ValidationResult,
    YesAndCheck,
)


@pytest.fixture
def tmp_intent_dir():
    """Create a temporary intent directory with test data."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        # Create schema
        schema = {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "title": "Test Intent Schema",
            "oneOf": [
                {"$ref": "#/definitions/work_order"},
            ],
            "definitions": {
                "work_order": {
                    "type": "object",
                    "properties": {
                        "type": {"const": "work_order"},
                        "id": {"type": "string"},
                        "parent_charter_id": {"type": "string"},
                        "version": {"type": "string"},
                        "description": {"type": "string"},
                        "status": {"type": "string",
                                   "enum": ["backlog", "in_progress", "completed", "blocked"]},
                        "priority": {"type": "string",
                                     "enum": ["critical", "high", "medium", "low"]},
                    },
                    "required": ["type", "id", "parent_charter_id", "version",
                                 "description", "status", "priority"],
                },
            },
        }
        (tmpdir / "schema.json").write_text(json.dumps(schema))

        # Create manifesto
        manifesto = {
            "type": "manifesto",
            "id": "hori-manifesto-001",
            "version": "2.0.0",
            "mission": "To build a local-first agent runtime with a safety spine",
            "principles": [
                {"name": "Local-First Intelligence",
                 "description": "Core reasoning happens locally"},
                {"name": "Safety",
                 "description": "The LLM is untrusted, the architecture is trusted"},
            ],
        }
        (tmpdir / "manifesto.json").write_text(json.dumps(manifesto))

        # Create charter
        charter = {
            "type": "charter",
            "id": "hori-foundation-charter-001",
            "parent_manifesto_id": "hori-manifesto-001",
            "version": "1.0.0",
            "objectives": ["Establish a reliable intelligence core"],
            "constraints": [
                "All core services must be local-first where possible",
                "All new features must be registered against the Intent Hierarchy",
            ],
            "success_criteria": ["The Intent Schema is validated"],
        }
        (tmpdir / "charter.json").write_text(json.dumps(charter))

        yield tmpdir


@pytest.fixture
def manager(tmp_intent_dir):
    """Create an IntentManager with the temp directory."""
    return IntentManager(intent_dir=tmp_intent_dir)


def test_create_work_order_generates_valid_id(manager):
    """create_work_order should generate an ID from the description."""
    wo = manager.create_work_order(
        description="Implement local STT with whisper",
        parent_charter_id="hori-foundation-charter-001",
        priority="high",
    )
    assert wo["type"] == "work_order"
    assert "implement-local-stt" in wo["id"]
    assert wo["parent_charter_id"] == "hori-foundation-charter-001"
    assert wo["priority"] == "high"
    assert wo["status"] == "backlog"


def test_create_work_order_with_deadline(manager):
    """create_work_order should include deadline if provided."""
    wo = manager.create_work_order(
        description="Fix the voice input issue on desktop",
        parent_charter_id="hori-foundation-charter-001",
        priority="medium",
        deadline="2026-12-31T23:59:59Z",
    )
    assert wo["deadline"] == "2026-12-31T23:59:59Z"


def test_validate_good_work_order(manager):
    """A well-formed work order should pass validation."""
    wo = manager.create_work_order(
        description="Implement the notification pipeline for proactive alerts",
        parent_charter_id="hori-foundation-charter-001",
        priority="high",
    )
    result = manager.validate(wo)
    assert result.valid is True
    assert len(result.errors) == 0


def test_validate_missing_parent_charter(manager):
    """A work order without parent_charter_id should fail."""
    wo = {
        "type": "work_order",
        "id": "test-wo",
        "parent_charter_id": "",
        "version": "1.0.0",
        "description": "A valid description of work",
        "status": "proposed",
        "priority": "medium",
    }
    result = manager.validate(wo)
    assert result.valid is False
    assert any("parent_charter_id" in e for e in result.errors)


def test_validate_invalid_priority(manager):
    """A work order with invalid priority should fail."""
    wo = manager.create_work_order(
        description="Test work order description",
        parent_charter_id="hori-foundation-charter-001",
        priority="urgent",  # invalid
    )
    result = manager.validate(wo)
    assert result.valid is False
    assert any("priority" in e.lower() for e in result.errors)


def test_validate_short_description(manager):
    """A work order with a very short description should fail."""
    wo = manager.create_work_order(
        description="short",  # < 10 chars
        parent_charter_id="hori-foundation-charter-001",
        priority="medium",
    )
    result = manager.validate(wo)
    assert result.valid is False
    assert any("description" in e.lower() for e in result.errors)


def test_validate_mismatched_charter_id(manager):
    """A work order with wrong charter ID should warn."""
    wo = manager.create_work_order(
        description="A valid work order description",
        parent_charter_id="wrong-charter-id",
        priority="medium",
    )
    result = manager.validate(wo)
    # Should still be valid (just a warning)
    assert result.valid is True
    assert any("charter" in w.lower() for w in result.warnings)


def test_yes_and_no_conflict(manager):
    """A work order that doesn't conflict should not require approval."""
    wo = manager.create_work_order(
        description="Add a new test for the consolidation metrics",
        parent_charter_id="hori-foundation-charter-001",
        priority="low",
    )
    check = manager.check_yes_and(wo)
    assert check.is_significant_shift is False
    assert check.requires_approval is False


def test_yes_and_with_conflict(manager):
    """A work order that conflicts with constraints should require approval."""
    wo = manager.create_work_order(
        description="Remove the local-first constraint and use cloud APIs",
        parent_charter_id="hori-foundation-charter-001",
        priority="high",
    )
    check = manager.check_yes_and(wo)
    # The description contains "remove" + "local-first" which suggests
    # violating the "local-first" constraint
    assert check.is_significant_shift is True
    assert check.requires_approval is True
    assert len(check.conflicting_constraints) > 0


def test_intent_drift_no_alignment(manager):
    """A work order with no mission alignment should warn."""
    wo = manager.create_work_order(
        description="Plan the company picnic and order catering",
        parent_charter_id="hori-foundation-charter-001",
        priority="low",
    )
    warnings = manager.detect_intent_drift(wo)
    # "company picnic" doesn't share terms with "local-first agent runtime"
    assert len(warnings) > 0
    assert any("drift" in w.lower() for w in warnings)


def test_intent_drift_aligned(manager):
    """A work order aligned with the mission should not warn."""
    wo = manager.create_work_order(
        description="Build a local-first agent runtime with safety",
        parent_charter_id="hori-foundation-charter-001",
        priority="high",
    )
    warnings = manager.detect_intent_drift(wo)
    # Should share terms with the mission
    assert len(warnings) == 0


def test_intent_drift_safety_violation(manager):
    """A work order that disables safety should warn."""
    wo = manager.create_work_order(
        description="Disable the safety architecture for faster inference",
        parent_charter_id="hori-foundation-charter-001",
        priority="high",
    )
    warnings = manager.detect_intent_drift(wo)
    assert any("safety" in w.lower() for w in warnings)


def test_save_and_list_work_orders(manager, tmp_path):
    """Should save and list work orders."""
    # Patch the PROPOSED_WO_DIR to use a temp directory
    import scripts.intent_tooling as it
    original_dir = it.PROPOSED_WO_DIR
    it.PROPOSED_WO_DIR = tmp_path / "proposed_work_orders"

    try:
        wo = manager.create_work_order(
            description="Test work order for saving",
            parent_charter_id="hori-foundation-charter-001",
            priority="medium",
        )
        path = manager.save_work_order(wo)
        assert path.exists()

        wos = manager.list_work_orders()
        assert len(wos) == 1
        assert wos[0]["id"] == wo["id"]
    finally:
        it.PROPOSED_WO_DIR = original_dir


def test_update_status(manager, tmp_path):
    """Should update work order status."""
    import scripts.intent_tooling as it
    original_dir = it.PROPOSED_WO_DIR
    it.PROPOSED_WO_DIR = tmp_path / "proposed_work_orders"

    try:
        wo = manager.create_work_order(
            description="Test work order for status update",
            parent_charter_id="hori-foundation-charter-001",
            priority="medium",
        )
        manager.save_work_order(wo)

        updated = manager.update_status(wo["id"], "in_progress")
        assert updated is True

        wos = manager.list_work_orders()
        assert wos[0]["status"] == "in_progress"
    finally:
        it.PROPOSED_WO_DIR = original_dir


def test_update_status_not_found(manager):
    """Should return False if work order not found."""
    result = manager.update_status("nonexistent-wo", "completed")
    assert result is False


def test_update_status_invalid(manager, tmp_path):
    """Should raise ValueError for invalid status."""
    import scripts.intent_tooling as it
    original_dir = it.PROPOSED_WO_DIR
    it.PROPOSED_WO_DIR = tmp_path / "proposed_work_orders"

    try:
        wo = manager.create_work_order(
            description="Test work order for invalid status",
            parent_charter_id="hori-foundation-charter-001",
            priority="medium",
        )
        manager.save_work_order(wo)

        with pytest.raises(ValueError):
            manager.update_status(wo["id"], "invalid_status")
    finally:
        it.PROPOSED_WO_DIR = original_dir

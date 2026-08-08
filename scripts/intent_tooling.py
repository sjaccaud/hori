"""
Intent Hierarchy Tooling — work order management and validation.

Provides tooling for the Manifesto → Charter → Work Order intent hierarchy:
  - Create work orders with proper hierarchy linkage
  - Validate work orders against the schema
  - Check Yes-AND compliance (significant shifts require deliberate approval)
  - Track work order status across the hierarchy
  - Detect intent drift (work orders that contradict charter constraints)

This module is standalone — it does not touch aios-core. It operates on
the JSON files in core/intent/ and core/state/proposed_work_orders/.

Usage:
    from scripts.intent_tooling import IntentManager
    mgr = IntentManager()
    wo = mgr.create_work_order(
        description="Implement local STT with whisper.cpp",
        parent_charter_id="hori-foundation-charter-001",
        priority="high",
    )
    mgr.validate(wo)

Traces to: STRAT-11, PoC 1.5, PoC 7.3, Manifesto Pillar VI.
"""
import json
import os
import re
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from jsonschema import validate as jsonschema_validate
    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False

logger = __import__("logging").getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
INTENT_DIR = PROJECT_ROOT / "core" / "intent"
SCHEMA_PATH = INTENT_DIR / "schema.json"
PROPOSED_WO_DIR = PROJECT_ROOT / "core" / "state" / "proposed_work_orders"


@dataclass
class ValidationResult:
    """Result of validating a work order against the schema and hierarchy."""
    valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


@dataclass
class YesAndCheck:
    """Result of a Yes-AND compliance check."""
    is_significant_shift: bool
    requires_approval: bool
    reason: str = ""
    conflicting_constraints: List[str] = field(default_factory=list)


class IntentManager:
    """Manage the intent hierarchy: Manifesto → Charter → Work Order."""

    def __init__(self, intent_dir: Path = INTENT_DIR):
        self.intent_dir = intent_dir
        self.schema = self._load_json(SCHEMA_PATH)
        self.manifesto = self._load_json(intent_dir / "manifesto.json")
        self.charter = self._load_json(intent_dir / "charter.json")

    def _load_json(self, path: Path) -> dict:
        try:
            return json.loads(path.read_text())
        except Exception as e:
            logger.warning(f"Failed to load {path}: {e}")
            return {}

    def create_work_order(
        self,
        description: str,
        parent_charter_id: str,
        priority: str = "medium",
        deadline: Optional[str] = None,
        status: str = "backlog",
    ) -> dict:
        """Create a new work order with proper hierarchy linkage.

        Args:
            description: What the work order is about
            parent_charter_id: The charter this work order traces to
            priority: critical/high/medium/low
            deadline: ISO format deadline (optional)
            status: proposed/in_progress/completed/blocked

        Returns:
            A work order dict ready for validation and saving
        """
        # Generate ID from description
        slug = self._slugify(description)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d")
        wo_id = f"hori-{slug}-wo-{timestamp}"

        wo = {
            "type": "work_order",
            "id": wo_id,
            "parent_charter_id": parent_charter_id,
            "version": "1.0.0",
            "description": description,
            "status": status,
            "priority": priority,
        }
        if deadline:
            wo["deadline"] = deadline

        return wo

    def validate(self, work_order: dict) -> ValidationResult:
        """Validate a work order against the schema and hierarchy.

        Checks:
        1. JSON schema validation
        2. parent_charter_id exists in the charter
        3. Priority is a valid value
        4. Status is a valid value
        5. Description is non-empty and reasonable length
        """
        result = ValidationResult(valid=True)

        # 1. Schema validation
        if self.schema and HAS_JSONSCHEMA:
            try:
                jsonschema_validate(instance=work_order, schema=self.schema)
            except Exception as e:
                result.valid = False
                result.errors.append(f"Schema validation failed: {e}")
        elif not HAS_JSONSCHEMA:
            result.warnings.append("jsonschema not installed — schema validation skipped")

        # 2. Parent charter exists
        parent_id = work_order.get("parent_charter_id", "")
        if self.charter and parent_id:
            if parent_id != self.charter.get("id", ""):
                result.warnings.append(
                    f"parent_charter_id '{parent_id}' does not match "
                    f"charter id '{self.charter.get('id', '')}'"
                )
        elif not parent_id:
            result.valid = False
            result.errors.append("Missing parent_charter_id")

        # 3. Priority validation
        valid_priorities = {"critical", "high", "medium", "low"}
        priority = work_order.get("priority", "")
        if priority not in valid_priorities:
            result.valid = False
            result.errors.append(
                f"Invalid priority '{priority}'. Must be one of: {valid_priorities}"
            )

        # 4. Status validation
        valid_statuses = {"backlog", "in_progress", "completed", "blocked", "cancelled"}
        status = work_order.get("status", "")
        if status and status not in valid_statuses:
            result.valid = False
            result.errors.append(
                f"Invalid status '{status}'. Must be one of: {valid_statuses}"
            )

        # 5. Description validation
        desc = work_order.get("description", "")
        if not desc or len(desc.strip()) < 10:
            result.valid = False
            result.errors.append("Description must be at least 10 characters")
        elif len(desc) > 500:
            result.warnings.append("Description is very long (>500 chars) — consider trimming")

        return result

    def check_yes_and(self, work_order: dict) -> YesAndCheck:
        """Check if a work order represents a significant shift requiring approval.

        The Yes-AND mechanism (Manifesto Pillar VI) ensures that significant
        shifts in direction are deliberate and documented. A work order is
        a significant shift if it potentially conflicts with charter constraints.

        Args:
            work_order: The work order to check

        Returns:
            YesAndCheck indicating whether approval is needed
        """
        if not self.charter:
            return YesAndCheck(
                is_significant_shift=False,
                requires_approval=False,
                reason="No charter loaded — cannot check constraints",
            )

        constraints = self.charter.get("constraints", [])
        desc = work_order.get("description", "").lower()

        # Check for keywords that might conflict with constraints
        conflicting = []
        for constraint in constraints:
            constraint_lower = constraint.lower()

            # Extract key terms from the constraint
            key_terms = self._extract_key_terms(constraint_lower)

            # Check if the work order description contains anti-patterns
            # for this constraint
            for term in key_terms:
                if term in desc:
                    # Check if the description suggests violating the constraint
                    # Look for negation or opposition patterns
                    if self._suggests_violation(desc, term, constraint_lower):
                        conflicting.append(constraint)
                        break

        is_shift = len(conflicting) > 0
        return YesAndCheck(
            is_significant_shift=is_shift,
            requires_approval=is_shift,
            reason=(
                f"Conflicts with {len(conflicting)} charter constraint(s): "
                f"{'; '.join(conflicting)}"
                if conflicting
                else "No conflicts with charter constraints"
            ),
            conflicting_constraints=conflicting,
        )

    def detect_intent_drift(self, work_order: dict) -> List[str]:
        """Detect if a work order drifts from the manifesto mission.

        Checks the work order description against the manifesto mission
        and principles. Returns a list of drift warnings.

        Args:
            work_order: The work order to check

        Returns:
            List of drift warning strings (empty if no drift detected)
        """
        warnings = []
        if not self.manifesto:
            return warnings

        desc = work_order.get("description", "").lower()
        mission = self.manifesto.get("mission", "").lower()

        # Extract key concepts from the mission
        mission_terms = self._extract_key_terms(mission)

        # Check if the work order aligns with at least one mission term
        aligned = any(term in desc for term in mission_terms)

        if not aligned and len(desc) > 20:
            # The work order doesn't share any terms with the mission
            # This doesn't necessarily mean drift, but it's worth flagging
            warnings.append(
                "Work order description does not share key terms with the "
                "manifesto mission — potential intent drift"
            )

        # Check against principles
        principles = self.manifesto.get("principles", [])
        for principle in principles:
            pname = principle.get("name", "").lower()
            pdesc = principle.get("description", "").lower()

            # Check for anti-patterns
            if "local-first" in pname and ("cloud" in desc or "api" in desc):
                if "local" not in desc:
                    warnings.append(
                        "Work order may conflict with Local-First Intelligence "
                        "principle — involves cloud/API without local fallback"
                    )

            if "safety" in pname or "security" in pname:
                if "disable" in desc or "remove" in desc or "bypass" in desc:
                    warnings.append(
                        "Work order may conflict with safety principle — "
                        "involves disabling or removing a safety mechanism"
                    )

        return warnings

    def save_work_order(self, work_order: dict) -> Path:
        """Save a work order to the proposed work orders directory."""
        PROPOSED_WO_DIR.mkdir(parents=True, exist_ok=True)
        wo_id = work_order.get("id", "unknown")
        path = PROPOSED_WO_DIR / f"{wo_id}.json"
        path.write_text(json.dumps(work_order, indent=2))
        return path

    def list_work_orders(self) -> List[dict]:
        """List all work orders in the proposed directory."""
        if not PROPOSED_WO_DIR.exists():
            return []
        work_orders = []
        for path in PROPOSED_WO_DIR.glob("*.json"):
            try:
                work_orders.append(json.loads(path.read_text()))
            except Exception:
                continue
        return work_orders

    def update_status(self, wo_id: str, status: str) -> bool:
        """Update the status of a work order.

        Args:
            wo_id: The work order ID
            status: New status (proposed/in_progress/completed/blocked/cancelled)

        Returns:
            True if updated, False if not found
        """
        valid_statuses = {"backlog", "in_progress", "completed", "blocked", "cancelled"}
        if status not in valid_statuses:
            raise ValueError(f"Invalid status: {status}")

        # Check proposed work orders
        path = PROPOSED_WO_DIR / f"{wo_id}.json"
        if path.exists():
            wo = json.loads(path.read_text())
            wo["status"] = status
            path.write_text(json.dumps(wo, indent=2))
            return True

        return False

    def _slugify(self, text: str) -> str:
        """Convert text to a URL-safe slug."""
        slug = re.sub(r"[^a-zA-Z0-9\s]", "", text.lower())
        words = slug.split()[:4]  # take first 4 words
        return "-".join(words) if words else "work"

    def _extract_key_terms(self, text: str) -> List[str]:
        """Extract key terms from a text string."""
        stop_words = {
            "the", "a", "an", "and", "or", "for", "in", "of", "to", "is",
            "it", "with", "must", "should", "will", "shall", "all", "new",
            "be", "are", "was", "were", "this", "that", "these", "those",
        }
        words = re.findall(r"\b[a-z]{3,}\b", text)
        return [w for w in words if w not in stop_words]

    def _suggests_violation(self, desc: str, term: str, constraint: str) -> bool:
        """Check if the description suggests violating a constraint.

        This is a heuristic — it looks for patterns like:
        - "remove <term>"
        - "disable <term>"
        - "skip <term>"
        - "bypass <term>"
        - "ignore <term>"
        """
        violation_patterns = [
            r"\bremove\b.*\b" + re.escape(term) + r"\b",
            r"\bdisable\b.*\b" + re.escape(term) + r"\b",
            r"\bskip\b.*\b" + re.escape(term) + r"\b",
            r"\bbypass\b.*\b" + re.escape(term) + r"\b",
            r"\bignore\b.*\b" + re.escape(term) + r"\b",
        ]
        for pattern in violation_patterns:
            if re.search(pattern, desc):
                return True
        return False

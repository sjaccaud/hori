"""Allowlist and validation logic for the root broker."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class AllowlistRule:
    """A single operation allowlist rule."""

    operation: str
    allowed_params: dict[str, Any]


class Allowlist:
    """Load and enforce the broker operation allowlist."""

    DEFAULT = {
        "service_restart": {
            "service": ["aios_core", "aios-sherpa", "aios-tool-daemon", "llamacpp"],
            "confirmed_by": {"pattern": r"^[A-Za-z0-9_.:-]{1,128}$"},
            "timeout": {"min": 5, "max": 300, "default": 60},
            "_required": ["service"],
        },
        "service_status": {
            "service": ["aios_core", "aios-sherpa", "aios-tool-daemon", "llamacpp"],
            "confirmed_by": {"pattern": r"^[A-Za-z0-9_.:-]{1,128}$"},
            "timeout": {"min": 5, "max": 60, "default": 30},
            "_required": ["service"],
        },
        "service_stop": {
            "service": ["aios_core", "aios-sherpa", "aios-tool-daemon", "llamacpp"],
            "confirmed_by": {"pattern": r"^[A-Za-z0-9_.:-]{1,128}$"},
            "timeout": {"min": 5, "max": 300, "default": 60},
            "_required": ["service"],
        },
        "journalctl_unit": {
            "unit": ["aios_core", "aios-sherpa", "aios-tool-daemon", "llamacpp"],
            "lines": {"min": 1, "max": 1000, "default": 50},
            "since": {"pattern": r"^[-+][0-9]+[smhd]$"},
            "confirmed_by": {"pattern": r"^[A-Za-z0-9_.:-]{1,128}$"},
            "timeout": {"min": 5, "max": 60, "default": 30},
            "_required": ["unit"],
        },
    }

    def __init__(self, path: str | Path, fallback: dict[str, Any] | None = None):
        self.path = Path(path)
        self.fallback = fallback or self.DEFAULT
        self._data = self._load()

    def _load(self) -> dict[str, Any]:
        if self.path.exists():
            with open(self.path, "r", encoding="utf-8") as f:
                return json.load(f)
        return self.fallback

    def is_operation_allowed(self, operation: str) -> bool:
        return operation in self._data

    def validate_params(
        self, operation: str, params: dict[str, Any]
    ) -> tuple[bool, str | None]:
        """Validate that params match the allowlist.

        Returns (ok, reason). If not ok, reason is a short error string.
        """
        if operation not in self._data:
            return False, f"Operation '{operation}' is not in allowlist"

        spec = self._data[operation]
        for key, value in params.items():
            if key not in spec:
                return False, f"Parameter '{key}' is not allowed for '{operation}'"
            allowed = spec[key]
            if isinstance(allowed, list):
                if value not in allowed:
                    return False, f"Value '{value}' for '{key}' is not in allowlist"
            elif isinstance(allowed, dict):
                if (
                    "min" in allowed
                    and isinstance(value, int | float)
                    and value < allowed["min"]
                ):
                    return (
                        False,
                        f"Value {value} for '{key}' is below minimum {allowed['min']}",
                    )
                if (
                    "max" in allowed
                    and isinstance(value, int | float)
                    and value > allowed["max"]
                ):
                    return (
                        False,
                        f"Value {value} for '{key}' is above maximum {allowed['max']}",
                    )
                if "pattern" in allowed and isinstance(value, str):
                    if not re.match(allowed["pattern"], value):
                        return (
                            False,
                            f"Value '{value}' for '{key}' does not match pattern",
                        )
            elif isinstance(allowed, int | float | str | bool):
                if value != allowed:
                    return False, f"Value '{value}' for '{key}' must be '{allowed}'"

        for required in spec.get("_required", []):
            if required not in params:
                return False, f"Missing required parameter '{required}'"

        return True, None

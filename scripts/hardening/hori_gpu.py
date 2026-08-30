#!@@REPO_ROOT@@/venv/bin/python3
"""Narrow, sandboxed GPU status wrapper for the system-root-broker.

Reads a JSON request from stdin, validates the operation, applies
Landlock/seccomp, and reads GPU state from the DRM sysfs interface.
This avoids the ROCm device-node and /opt/rocm library surface; the
amdgpu driver exposes the same data under /sys/class/drm/cardN/device.

Installed to /usr/local/sbin/hori-gpu by
scripts/hardening/install_root_broker.sh.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = "@@REPO_ROOT@@"
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

os.environ["XDG_CONFIG_HOME"] = "/tmp/hori-root-broker"

from services.broker.sandbox import apply_broker_sandbox  # noqa: E402

CARD_PATTERN = re.compile(r"^card[0-9]+$")
DRM_BASE = Path("/sys/class/drm")
PCI_IDS = Path("/usr/share/misc/pci.ids")


def _fail(status: str, message: str, sandbox_profile: str = "denied") -> None:
    result = {
        "status": status,
        "exit_code": 1,
        "stdout": "",
        "stderr": message,
        "error": message,
        "sandbox_profile": sandbox_profile,
    }
    print(json.dumps(result))
    sys.exit(1)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _read_int(path: Path) -> int | None:
    text = _read_text(path)
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _read_millidegrees(path: Path) -> float | None:
    value = _read_int(path)
    if value is None:
        return None
    return value / 1000.0


def _read_milliwatts(path: Path) -> float | None:
    value = _read_int(path)
    if value is None:
        return None
    return value / 1_000_000.0


def _active_clock(pp_dpm: Path) -> str | None:
    text = _read_text(pp_dpm)
    for line in text.splitlines():
        if "*" in line:
            return line.strip().replace("Mhz", "MHz")
    return None


def _parse_pci_ids(vendor_id: str, device_id: str) -> str | None:
    """Best-effort lookup of the device name from /usr/share/misc/pci.ids."""
    if not PCI_IDS.is_file():
        return None
    vendor = vendor_id.removeprefix("0x").lower()
    device = device_id.removeprefix("0x").lower()
    in_vendor = False
    try:
        with open(PCI_IDS, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if stripped.startswith("#") or not stripped:
                    continue
                # Tabs indicate sub-device or device under a vendor
                if line.startswith("\t\t"):
                    continue
                if line.startswith("\t"):
                    if in_vendor:
                        parts = stripped.split(None, 1)
                        if len(parts) == 2 and parts[0].lower() == device:
                            return parts[1]
                else:
                    parts = stripped.split(None, 1)
                    if len(parts) >= 1 and parts[0].lower() == vendor:
                        in_vendor = True
                    else:
                        in_vendor = False
    except OSError:
        return None
    return None


def _gpu_name(card_path: Path) -> str:
    vendor = _read_text(card_path / "vendor") or "0x1002"
    device = _read_text(card_path / "device") or "0x0000"
    name = _parse_pci_ids(vendor, device)
    if name:
        return name
    # Fall back to the numeric vendor:device description.
    return f"AMD GPU {device}"


def _card_state(card: str) -> dict[str, Any]:
    card_path = DRM_BASE / card / "device"
    if not card_path.is_dir():
        _fail("error", f"GPU card not found: {card}")

    hwmon_dir = card_path / "hwmon"
    hwmon = None
    if hwmon_dir.is_dir():
        for child in sorted(hwmon_dir.iterdir()):
            if child.is_dir():
                hwmon = child
                break

    temps: dict[str, float] = {}
    if hwmon:
        for entry in sorted(hwmon.iterdir()):
            if entry.name.startswith("temp") and entry.name.endswith("_label"):
                name = _read_text(entry)
            else:
                name = None
            if name:
                input_file = entry.with_name(entry.name.replace("_label", "_input"))
                value = _read_millidegrees(input_file)
                if value is not None:
                    temps[name] = value

    # Normalize the labels we expect: edge, junction, memory (mem).
    temperature_edge_c = temps.get("edge") or temps.get("temp1")
    temperature_junction_c = (
        temps.get("junction")
        or temps.get("hotspot")
        or temps.get("temp2")
    )
    temperature_memory_c = temps.get("mem") or temps.get("memory") or temps.get("temp3")

    fan_rpm = _read_int(hwmon / "fan1_input") if hwmon else None
    fan_percent = None
    if hwmon:
        pwm = _read_int(hwmon / "pwm1")
        pwm_max = _read_int(hwmon / "pwm1_max")
        if pwm is not None and pwm_max:
            fan_percent = round((pwm / pwm_max) * 100)
        else:
            fan_percent = pwm
    power_w = _read_milliwatts(hwmon / "power1_average") if hwmon else None

    vram_total_b = _read_int(card_path / "mem_info_vram_total")
    vram_used_b = _read_int(card_path / "mem_info_vram_used")

    clocks: dict[str, str | None] = {}
    for clock in ("dcefclk", "fclk", "mclk", "sclk", "socclk", "pcie"):
        path = card_path / f"pp_dpm_{clock}"
        if path.is_file():
            clocks[clock] = _active_clock(path)

    return {
        "name": _gpu_name(card_path),
        "pci_vendor": _read_text(card_path / "vendor"),
        "pci_device": _read_text(card_path / "device"),
        "temperature_edge_c": temperature_edge_c,
        "temperature_junction_c": temperature_junction_c,
        "temperature_memory_c": temperature_memory_c,
        "fan_percent": fan_percent,
        "fan_rpm": fan_rpm,
        "power_w": power_w,
        "vram_total_b": vram_total_b,
        "vram_used_b": vram_used_b,
        "clocks": clocks,
    }


def _collect_cards(card_filter: str | None) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    if card_filter is not None:
        cards.append(_card_state(card_filter))
        return cards

    if not DRM_BASE.is_dir():
        _fail("error", "DRM sysfs not available")

    for entry in sorted(DRM_BASE.iterdir()):
        if not CARD_PATTERN.match(entry.name):
            continue
        uevent = _read_text(entry / "device" / "uevent")
        if "DRIVER=amdgpu" not in uevent:
            continue
        cards.append(_card_state(entry.name))

    if not cards:
        # NVIDIA fallback: scan for DRM cards with nvidia driver
        for entry in sorted(DRM_BASE.iterdir()):
            if not CARD_PATTERN.match(entry.name):
                continue
            uevent = _read_text(entry / "device" / "uevent")
            if "DRIVER=nvidia" in uevent:
                cards.append(_card_state(entry.name))

    return cards


def _validate(data: dict[str, Any]) -> str | None:
    operation = data.get("operation")
    if operation != "gpu_status":
        _fail("denied", f"operation '{operation}' is not allowed")

    card = data.get("card")
    if card is not None and not (isinstance(card, str) and CARD_PATTERN.match(card)):
        _fail("denied", f"invalid card selector: {card!r}")

    return card


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        _fail("error", f"Invalid JSON on stdin: {exc}")

    card = _validate(data)

    sandbox = apply_broker_sandbox("gpu_status")
    sandbox_profile = sandbox["sandbox_profile"]
    if sandbox_profile == "denied":
        reason = (
            sandbox["seccomp"].get("error")
            or sandbox["landlock"].get("error")
            or "sandbox could not be applied"
        )
        _fail("denied", f"sandbox application failed: {reason}", "denied")

    try:
        cards = _collect_cards(card)
    except OSError as exc:
        _fail("error", f"failed to read GPU state: {exc}", sandbox_profile)

    result: dict[str, Any] = {
        "status": "ok",
        "exit_code": 0,
        "stdout": json.dumps({"cards": cards}, indent=2),
        "stderr": "",
        "sandbox_profile": sandbox_profile,
        "parsed": {"cards": cards},
    }
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())

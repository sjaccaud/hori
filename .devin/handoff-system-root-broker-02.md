# Handoff — System Root Broker Slice 2

**Slice:** service_stop and journalctl_unit
**Branch:** `slice/system-root-broker-01`
**Commit:** `c75b490`
**Date:** 2026-08-30

## What was built

- `services/broker/allowlist.py` — added `journalctl_unit` with the same four
  allowlisted units, bounded `lines` (1–1000, default 50), and an optional
  signed relative `since` pattern (`^[-+][0-9]+[smhd]$`).
- `services/broker/broker.py` — added `_op_journalctl_unit`; `service_stop` was
  already present and is now covered by tests.
- `services/broker/test_broker.py` — positive + parameter tests for
  `service_stop` and `journalctl_unit`, including default lines and bounds.
- `tests/adversarial/test_root_broker.py` — adversarial coverage for
  injection in `service_stop` and `journalctl_unit`, arbitrary arguments,
  disallowed units, and line-count bounds.
- `services/aios_core/main.py` — `POST /admin/api/service/stop` and
  `GET /admin/api/logs/unit/{unit}` routed through `RootBrokerClient`,
  including self-stop detached handling.
- `services/aios_core/test_main.py` — admin tests for stop, self-stop, and
  journalctl unit endpoints.
- `scripts/hardening/install_root_broker.sh` — now merges the installed
  allowlist with `Allowlist.DEFAULT`, copies `hori_journalctl.py` to
  `/usr/local/sbin/hori-journalctl`, and emits narrow `systemctl` plus
  wrapper sudoers rules (sudo-rs does not allow wildcards in command
  arguments).

## Verification

- `services/broker/test_broker.py` — 15 passed.
- `tests/adversarial/test_root_broker.py` — 10 passed.
- `services/aios_core/test_main.py` admin tests — 11 passed.
- `make test-unit` — 408 passed, 2 skipped.
- `make test-adversarial` — 135 passed.
- `make test-integration` — 23 passed, 2 failed (pre-existing installed
  `tmpfiles.d` drift; unrelated to this slice).

## Live demo (root-only)

The code is committed and the broker is installed, but the running broker
uses the old allowlist and sudoers. To finish the demo the product owner
must run, in a root shell:

```bash
sudo bash scripts/hardening/install_root_broker.sh
sudo systemctl daemon-reload
sudo systemctl restart aios_core system-root-broker
```

Then from the `<user>` shell (in the `root-broker` group):

```bash
cd ~/Projects/aios
PYTHONPATH=. ./venv/bin/python3 -m services.broker.client service_stop --service llamacpp
PYTHONPATH=. ./venv/bin/python3 -m services.broker.client journalctl_unit --unit aios_core --lines 50
```

Expected: JSON responses, new audit entries in
`/var/log/system-root-broker/audit.jsonl`, and denials for non-allowlisted
units or wrong tokens.

## What surprised

- `main.py` already had an uncommitted model/port config change. The
  slice commit intentionally keeps those hunks unstaged.
- FastAPI route order means `/admin/api/logs/{log_type}` (the original
  log viewer) shadows `/admin/api/logs/unit` if added as a plain path.
  The new endpoint uses `/admin/api/logs/unit/{unit}` to avoid collision.

## What is unsure / next slices

The next candidate slices are unchanged from the original plan:

1. Remove `<user>` from `sudo` and `docker`.
2. Add broker confirmation gates and sandboxing for destructive ops.
3. Implement tamper-evident audit chaining.

The product owner should pick one after the demo.

## Open questions

- Should the `journalctl_unit` allowlist gain a `--since` default or a
  narrower `lines` maximum for production?
- Should `service_stop` on `aios_core` send an admin confirmation gate
  before the detached stop? (Confirmation gates are out of scope for
  Slice 2 but may be required for Slice 3+.)

# System Root Broker — Slice 2: service_stop and journalctl_unit

## 1. Intent trace

- **Manifesto Pillar:** VII — Engineering Discipline ("Simplicity as Security / build less," safety properties tested first) and the implicit Pillar I Local-First trust boundary (the LLM/user account must not hold root).
- **Roadmap anchor:** Continues the root-broker work defined in `.devin/prd/root-broker.md`. It directly supports **Tier 1 security closure** (removing the high-risk `<user>` `sudo`/`docker` paths by proving a narrower broker) and **PoC 16.3 Admin Panel** (service control and log viewing must not invoke `sudo` directly).
- **Off-roadmap justification:** The root broker itself is the enabling infrastructure; it is not a discrete PoC but is the replacement path for multiple Tier-1/Tier-2 security PoCs.

## 2. Interface contract

### Broker client

```python
from services.broker.client import RootBrokerClient

c = RootBrokerClient()

# Tier 2 / reversible side-effect
c.call("service_stop", {"service": "aios_core"})

# Tier 1 / read-only
c.call("journalctl_unit", {"unit": "aios_core", "lines": 50})
c.call("journalctl_unit", {"unit": "aios_core", "lines": 50, "since": "-1h"})
```

Response schema (same as existing broker operations):

```json
{
  "request_id": "<uuid>",
  "status": "ok|denied|error|timeout",
  "exit_code": 0,
  "stdout": "...",
  "stderr": "..."
}
```

### Admin panel HTTP

- `POST /admin/api/service/{action}` where `action ∈ {status, restart, stop}`.
  - `service` query/body parameter is validated against the broker allowlist.
  - `stop` is routed through `RootBrokerClient.call("service_stop", ...)`, exactly like `restart`.
- `GET /admin/api/logs/unit/{unit}?lines=<int>&since=<optional>`
  - `unit` is validated against the broker allowlist.
  - Routed through `RootBrokerClient.call("journalctl_unit", ...)`.
  - Returns `{"unit": "...", "lines": N, "content": "...", "exit_code": 0}`.

### CLI smoke test

```bash
sg root-broker -c 'PYTHONPATH=. ./venv/bin/python3 -c "from services.broker.client import RootBrokerClient; c = RootBrokerClient(); print(c.call(\"service_stop\", {\"service\": \"llamacpp\"}))"'

sg root-broker -c 'PYTHONPATH=. ./venv/bin/python3 -c "from services.broker.client import RootBrokerClient; c = RootBrokerClient(); print(c.call(\"journalctl_unit\", {\"unit\": \"aios_core\", \"lines\": 50}))"'
```

## 3. Safety classification

| Operation | Class | Confirmation | Reversible | Safety-spine layers |
|-----------|-------|--------------|------------|---------------------|
| `service_stop` | side-effect / reversible | Tier 2 (same as `service_restart`) | Yes via `service_start`/`service_restart` | Unix socket group membership; bearer token; allowlist value validation; audit log; narrow `root-broker` sudoers for `systemctl stop <unit>` only. |
| `journalctl_unit` | read-only | Tier 1 | N/A | Unix socket group; bearer token; allowlist value validation; bounded `lines` and `since` pattern; audit log; `root-broker` runs `journalctl` with no privileges or under a narrow sudoers rule. |

**Fail-closed behavior:** wrong token, unknown operation, non-allowlisted `service`/`unit`, or out-of-bounds `lines`/`since` → `denied` response and a single audit entry. The broker never constructs a `systemctl`/`journalctl` command from an unvalidated string.

## 4. Test contract

### Unit / integration tests

`services/broker/test_broker.py`:
- `test_service_stop_allowlisted_success` — stops an allowlisted (dummy) service and appends an audit entry.
- `test_journalctl_unit_allowlisted_success` — fetches journal lines for an allowlisted unit.
- `test_journalctl_unit_default_lines` — `lines` defaults to 50.
- `test_service_stop_non_allowlisted_denied` — `service` not in the allowlist is denied.
- `test_journalctl_unit_non_allowlisted_denied` — `unit` not in the allowlist is denied.
- `test_journalctl_unit_lines_too_large_denied` — `lines` above the configured maximum is denied.
- `test_journalctl_unit_rejects_invalid_since` — `since` not matching the allowlist pattern is denied.
- `test_audit_log_does_not_contain_raw_token_for_new_ops` — token redaction for `service_stop` and `journalctl_unit`.

`services/aios_core/test_main.py`:
- `test_admin_service_stop_uses_broker` — `POST /admin/api/service/stop?service=aios_core` with a valid admin token calls `RootBrokerClient` and returns the broker result.
- `test_admin_journalctl_unit_uses_broker` — `GET /admin/api/logs/unit/aios_core?lines=50` with a valid admin token returns the broker `journalctl_unit` result.

### Adversarial tests

`tests/adversarial/test_root_broker.py`:
- `test_broker_rejects_shell_injection_via_service_name` — `service` with `; rm -rf /` is denied by the allowlist (must fail before the operation is implemented, pass after).
- `test_broker_rejects_shell_injection_via_unit_name` — `unit` with `; cat /etc/shadow` is denied by the allowlist.
- `test_broker_journalctl_does_not_allow_arbitrary_arguments` — extra params such as `output` or `follow` are rejected by allowlist schema.
- `test_broker_journalctl_cannot_read_disallowed_unit` — a unit like `nginx` or `systemd-journald` is denied.
- `test_broker_service_stop_cannot_escape_sudoers` — the allowlist and the `systemctl` argument construction must not permit a non-allowlisted unit to reach `sudo`.
- `test_broker_audit_log_never_leaks_token` — existing property, extended to cover new operations.

## 5. Module placement

- `services/broker/broker.py` — operation dispatch. `_op_service_stop` already exists; add `_op_journalctl_unit`.
- `services/broker/allowlist.py` — `Allowlist.DEFAULT` already contains `service_stop`; add `journalctl_unit` with `unit`, `lines`, and `since` constraints.
- `services/broker/client.py` — no change required (generic `call`); optional convenience wrappers are out of scope.
- `services/aios_core/main.py` — extend `admin_service_control` to `action=stop`; add `/admin/api/logs/unit`.
- `scripts/hardening/install_root_broker.sh` — add `journalctl` and `systemctl stop` sudoers rules for the four allowlisted units.
- `services/broker/test_broker.py`, `tests/adversarial/test_root_broker.py`, `services/aios_core/test_main.py` — tests.

## 6. Out of scope

- Tier-2/3 confirmation gate UI or delayed-execution queue.
- Bubblewrap/seccomp per-child sandboxing.
- Package/file/docker operations.
- Removing `<user>` from `sudo` or `docker` groups (final hardening step, deferred).
- Streaming journal logs, persistent cursors, or log export.
- Non-systemd journal access.
- A standalone CLI for `services.broker.client`.

## 7. Work-order link

- `core/state/proposed_work_orders/system-root-broker-02.json`

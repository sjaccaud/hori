# System Root Broker — Slice 3: Confirmation Gates and Sandboxed Execution

## 1. Intent trace

- **Manifesto Pillar:** VII — Engineering Discipline ("Simplicity as Security; build less, test first") and I — Local-First trust boundary (the human account must not hold root).
- **Roadmap anchor:** Tier 1 security closure (removing the high-risk `<user>` `sudo`/`docker` paths) and Tier 2A safety spine (PoC 15.0b/c/d Landlock/seccomp sandboxing). Slice 3 is the next load-bearing step in `.devin/prd/root-broker.md` after Slice 1 (broker core) and Slice 2 (`service_stop` / `journalctl_unit`).
- **Off-roadmap justification:** The root broker itself is the enabling infrastructure for Tier 1/Tier 2; it is not a discrete PoC but the replacement path for direct `sudo`.

## 2. Interface contract

### Broker client

```python
from services.broker.client import RootBrokerClient

c = RootBrokerClient()

# Tier 2 / destructive: a non-empty confirmed_by identity is required
# (read-only operations do not need it).
c.call("service_restart", {"service": "llamacpp", "confirmed_by": "<user>-2026-08-30"})
c.call("service_stop", {"service": "llamacpp", "confirmed_by": "<user>-2026-08-30"})

# Tier 1 / read-only: confirmation is not required and ignored if present.
c.call("service_status", {"service": "llamacpp"})
c.call("journalctl_unit", {"unit": "aios_core", "lines": 50})
```

Response schema (same as existing):

```json
{
  "request_id": "<uuid>",
  "status": "ok|pending|denied|error|timeout",
  "exit_code": 0,
  "stdout": "...",
  "stderr": "..."
}
```

A destructive request without `confirmed_by` returns:

```json
{
  "request_id": "<uuid>",
  "status": "denied",
  "error": "operation 'service_stop' requires a 'confirmed_by' identity"
}
```

### Client CLI

```bash
PYTHONPATH=. ./venv/bin/python3 -m services.broker.client service_stop \
  --service llamacpp --confirm "<user>-2026-08-30"
```

`--confirm` maps to the `confirmed_by` parameter. It is accepted for any operation but only required for `service_restart` and `service_stop`.

### Admin panel HTTP

- `POST /admin/api/service/{action}` where `action ∈ {status, restart, stop}`.
  - `service` and `confirm` query/body parameters are required for `restart` and `stop`; `confirm` is ignored for `status`.
  - `restart` and `stop` are routed through `RootBrokerClient.call` with `confirmed_by=confirm`.
- `GET /admin/api/logs/unit/{unit}?lines=<int>&since=<optional>&confirm=<optional>`
  - `unit` is validated against the broker allowlist.
  - `confirm` is accepted but not required; the route calls `RootBrokerClient.call("journalctl_unit", ...)`.

### Confirmation identity format

- Non-empty string.
- 1–128 characters.
- Pattern: `^[A-Za-z0-9_.:-]{1,128}$` (letters, digits, underscore, dot, colon, hyphen).
- Must not equal the broker token.
- Recorded in the audit entry as `confirmed_by`.

### Sandbox profile

- `service_status`, `service_restart`, `service_stop`, and `journalctl_unit` run inside a sandbox.
- The sandbox is applied inside dedicated wrapper scripts (`/usr/local/sbin/hori-systemctl` and `/usr/local/sbin/hori-journalctl`) after privilege elevation, so the broker can still call `sudo -n <wrapper>`.
- Each wrapper:
  - reads a JSON request on stdin,
  - validates the operation and service/unit,
  - requires `confirmed_by` for `restart` and `stop`,
  - applies `services.tool_daemon.seccomp_filter` and `services.tool_daemon.landlock` to itself and its children,
  - executes only `systemctl` or `journalctl` with the exact, allowlisted arguments.
- The audit entry records the resulting `sandbox_profile` string (e.g., `seccomp`, `seccomp+landlock`, or `denied` if sandbox application failed).

### Sudoers rules (root-only installer)

Sudoers rules for broker children are narrowed to the two wrappers only:

```text
root-broker ALL=(root) NOPASSWD: /usr/local/sbin/hori-systemctl
root-broker ALL=(root) NOPASSWD: /usr/local/sbin/hori-journalctl
```

No wildcard command arguments. No `journalctl` or `systemctl` directly from `root-broker`.

## 3. Safety classification

| Operation | Class | Confirmation | Reversible | Safety-spine layers |
|-----------|-------|--------------|------------|---------------------|
| `service_status` | read-only | None | N/A | Unix socket group; bearer token; allowlist; sandboxed execution; audit log. |
| `journalctl_unit` | read-only | None | N/A | Unix socket group; bearer token; allowlist (unit, bounded lines, `since` pattern); sandboxed execution; audit log. |
| `service_restart` | side-effect / reversible | Tier 2 (`confirmed_by`) | Yes via `service_start` or `systemctl` start | Unix socket group; bearer token; allowlist; confirmation gate; sandboxed `systemctl` execution; audit log. |
| `service_stop` | side-effect / reversible | Tier 2 (`confirmed_by`) | Yes via `systemctl start` | Same as `service_restart`. |

**Fail-closed behavior:**
- Missing, empty, too long, or token-equal `confirmed_by` → `denied` for `service_restart`/`service_stop`.
- Unknown operation, non-allowlisted service/unit, bad `lines`/`since`, or shell injection attempts → `denied`.
- Sandbox application failure (seccomp or Landlock unavailable) for a destructive op → `denied`.
- Read-only ops (`service_status`, `journalctl_unit`) run with the sandbox but do not require `confirmed_by`.
- The broker never constructs a shell command from an untrusted string.

## 4. Test contract

### Unit / integration tests

`services/broker/test_broker.py`:
- `test_service_stop_requires_confirmed_by` — `service_stop` without `confirmed_by` is denied.
- `test_service_restart_requires_confirmed_by` — `service_restart` without `confirmed_by` is denied.
- `test_service_stop_confirmed_success` — `service_stop` with `confirmed_by` runs and appends an audit entry.
- `test_service_restart_confirmed_success` — `service_restart` with `confirmed_by` runs.
- `test_service_status_does_not_require_confirmation` — `service_status` still works without `confirmed_by`.
- `test_journalctl_unit_does_not_require_confirmation` — `journalctl_unit` still works without `confirmed_by`.
- `test_confirmed_by_cannot_equal_token` — passing the broker token as `confirmed_by` is denied.
- `test_confirmed_by_length_enforced` — `confirmed_by` longer than 128 chars is denied.
- `test_audit_entry_records_confirmed_by_and_sandbox` — audit JSON contains `confirmed_by` and `sandbox_profile` for a confirmed destructive op.
- `test_audit_token_not_leaked_in_confirmed_by` — if `confirmed_by` is the token, it is redacted before logging.

`services/aios_core/test_main.py`:
- `test_admin_service_stop_requires_confirm` — `POST /admin/api/service/stop?service=llamacpp` without `confirm` returns 400/422.
- `test_admin_service_stop_runs_with_confirm` — `POST /admin/api/service/stop?service=llamacpp&confirm=<user>-...` calls the broker and returns the result.
- `test_admin_service_restart_requires_confirm` — `restart` without `confirm` is rejected.
- `test_admin_journalctl_unit_ignores_confirm` — `journalctl_unit` still returns logs with or without `confirm`.

### Adversarial tests

`tests/adversarial/test_root_broker.py`:
- `test_broker_rejects_destructive_op_without_confirmation` — `service_stop` and `service_restart` are denied without `confirmed_by`; must fail before the gate is implemented.
- `test_broker_confirmation_identity_cannot_be_token` — the token itself is not a valid confirmation identity.
- `test_broker_confirmation_identity_rejects_injection` — `confirmed_by` containing `;`, `$`, `|`, newline, or other shell meta-characters is denied.
- `test_broker_sandbox_blocks_ptrace` — a sandboxed child cannot invoke `ptrace` (seccomp property).
- `test_broker_sandbox_blocks_forbidden_path` — a sandboxed child cannot `open` `/etc/shadow` or `~/.ssh/authorized_keys` (Landlock/allowlist property).
- `test_broker_sandbox_denies_network_connect` — a sandboxed child cannot create a TCP socket (Landlock network rule).
- `test_broker_audit_records_confirmation_and_sandbox_profile` — every confirmed destructive operation leaves an audit entry with `confirmed_by` and `sandbox_profile`.

## 5. Module placement

- `services/broker/broker.py` — require `confirmed_by` for `service_restart` and `service_stop`; route all operations through wrapper scripts; pass `confirmed_by` and `sandbox_profile` to audit.
- `services/broker/allowlist.py` — add `confirmed_by` to the `service_restart` and `service_stop` allowlist entries with format/length constraints; keep read-only entries unchanged.
- `services/broker/sandbox.py` — new module. Provides `BrokerSandbox.apply()` using the existing `services.tool_daemon.landlock` and `services.tool_daemon.seccomp_filter` primitives. Defines per-operation allowed-path lists.
- `services/broker/client.py` — add `--confirm <identity>` to the CLI argument parser; pass through to `params` as `confirmed_by`.
- `services/broker/audit.py` — ensure `confirmed_by` is recorded without redacting a non-token identity; already has the field.
- `services/broker/hori-systemctl.py` (source) and `services/broker/hori-journalctl.py` (source) — wrapper scripts installed to `/usr/local/sbin/` by `install_root_broker.sh`. They receive JSON on stdin, validate, apply the sandbox, and exec the narrow command.
- `scripts/hardening/install_root_broker.sh` — install/update `hori-systemctl` and `hori-journalctl`; update sudoers to allow only the two wrappers; merge the updated allowlist.
- `services/aios_core/main.py` — add `confirm` handling for `restart` and `stop` in the admin service-control route.
- `services/broker/test_broker.py`, `tests/adversarial/test_root_broker.py`, `services/aios_core/test_main.py` — tests.

## 6. Out of scope

- Tier-3 delayed-execution queue or out-of-band confirmation (OTP, email, push).
- Package/file/docker operations.
- Removing `<user>` from `sudo` or `docker` (final hardening step, deferred).
- Tamper-evident audit hash chaining (next slice candidate).
- Streaming or persistent journal log cursors.
- Non-`systemctl`/`journalctl` privileged commands.
- Replacing the `root-broker` systemd service user.

## 7. Work-order link

- `core/state/proposed_work_orders/system-root-broker-03.json`

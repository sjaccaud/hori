# Handoff — System Root Broker Slice 3

**Slice:** confirmation gates and sandboxing — COMPLETE
**Branch:** `slice/system-root-broker-01`
**Commit (handoff point):** `d80dab4`
**Date:** 2026-08-30

## What was built

### Confirmation gates (already in place from prior work)

- `.devin/prd/system-root-broker-03.md` — interface, safety, and test contracts
  for confirmation gates and sandboxed execution.
- `services/broker/allowlist.py` — all operations accept an optional
  `confirmed_by` parameter with a pattern/length constraint.
- `services/broker/broker.py` — destructive operations require `confirmed_by`
  that is not the broker token; the value is redacted in audit if it equals
  the token.
- `services/broker/client.py` — `--confirm` CLI flag maps to `confirmed_by`.
- `services/aios_core/main.py` — admin service control and journalctl routes
  pass the `confirm` parameter through to the broker.

### Sandboxing (this session)

- `services/broker/sandbox.py` — `BrokerSandbox.apply()` wrapping
  `services.tool_daemon.landlock` and `services.tool_daemon.seccomp_filter`,
  with per-operation path allowlists.
- `scripts/hardening/hori_systemctl.py` — fixed-path, sandboxed `systemctl`
  wrapper; validates operation/service/`confirmed_by`, applies Landlock+seccomp,
  execs only the intended command, returns JSON with `sandbox_profile`.
- `scripts/hardening/hori_journalctl.py` — sandboxed `journalctl` wrapper;
  validates unit/line count/since window, applies sandbox, returns JSON.
- `services/broker/broker.py` — `_run_wrapper()` sends JSON to the wrapper on
  stdin, parses the JSON response, and carries `sandbox_profile` through to
  the audit log.
- `services/broker/audit.py` — `BrokerAuditEntry` records `confirmed_by` and
  `sandbox_profile`.
- `services/tool_daemon/landlock.py` — `AllowedPath` gained an `execute` flag;
  the ruleset engine now applies different rights for files vs. directories
  so binary exec and shared-library `mmap` work under Landlock.
- `scripts/hardening/install_root_broker.sh` — installs both wrappers to
  `/usr/local/sbin/hori-{systemctl,journalctl}` and narrows the sudoers rule
  to only those two fixed paths.
- `services/broker/system-root-broker.service` — hardened for sandboxed
  children (`MemoryDenyWriteExecute=false`, `ReadWritePaths` extended with
  `/run/systemd`, `/run/dbus`, `/var/log/journal`, and `PYTHONPATH` set).

### Tests

- `services/broker/test_broker.py` — 17 passed.
- `tests/adversarial/test_root_broker.py` — 17 passed, including new tests for
  token-as-confirmation, shell meta-characters in `confirmed_by`, ptrace
  blocking, `/etc/shadow` and `/etc/group` denial, `~/.ssh/authorized_keys`
  denial, TCP socket denial, and audit `confirmed_by` + `sandbox_profile`.
- `make test-unit` — 411 passed, 2 skipped.
- `make test-adversarial` — 142 passed.
- `make test` — unit, integration, regression, and adversarial suites all green.

## Live smoke test — passed

Installed with `sudo scripts/hardening/install_root_broker.sh` and
`sudo systemctl restart system-root-broker`. Then exercised through the broker
CLI:

- Unconfirmed `service_stop --service llamacpp` → `denied`.
- Confirmed `service_stop --service llamacpp --confirm operator-2026-08-30`
  → `ok`, `sandbox_profile: "seccomp+landlock"`, `llamacpp` stopped.
- `service_status --service llamacpp` after the stop → `exit_code: 3`,
  `stdout: "inactive"`, `sandbox_profile: "seccomp+landlock"`.
- Confirmed `service_restart --service llamacpp --confirm operator-2026-08-30`
  → `ok`, `llamacpp` active again.
- Audit entries at `/var/log/system-root-broker/audit.jsonl` correctly record
  `confirmed_by` and `sandbox_profile`.

## What changed during the live test

1. `install_root_broker.sh` was not executable in git; mode fixed to `0755`.
2. `services/tool_daemon/landlock.py` printed a non-existent-path skip message
   to `stdout`, which corrupted the wrapper's JSON output. The message is now
   written to `sys.stderr` so the wrapper's `stdout` stays valid JSON.
3. The wrappers now set `XDG_CONFIG_HOME=/tmp/hori-root-broker` before
   importing any service module, so `hori.config` does not try (and fail) to
   create `/root/.config/hori` under `ProtectHome=read-only`.

## Open questions / decisions

- Sandbox path allowlist: the current list works for `service_status` and
  `journalctl_unit` as the repository user; the live smoke test will confirm
  whether `service_restart`/`service_stop` need additional runtime paths.
- Should `service_status` be sandboxed as well? The broker wrapper does
  sandbox it, but the admin panel `status` action still calls `systemctl status`
  directly (read-only, no root). A future decision can unify these.
- Installer: the wrappers use the repo venv Python and insert the repo root
  into `sys.path` at runtime, so no `PYTHONPATH` inheritance is required.

## Safety notes

- Human `<user>` still has `sudo` and `docker` access; the broker is the
  only privileged path being hardened in this slice.
- `service_stop` against `aios_core` or `system-root-broker` itself is
  now gated by `confirmed_by`, but the live smoke test should use
  `llamacpp` and restart it immediately.

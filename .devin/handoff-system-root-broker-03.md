# Handoff — System Root Broker Slice 3

**Slice:** confirmation gates and sandboxing
**Branch:** `slice/system-root-broker-01`
**Commit (handoff point):** `e428773`
**Date:** 2026-08-30

## What was built

- `.devin/prd/system-root-broker-03.md` — interface, safety, and test contracts
  for confirmation gates and sandboxed execution.
- `core/state/proposed_work_orders/system-root-broker-03.json` — work order.
- `services/broker/allowlist.py` — all operations now accept an optional
  `confirmed_by` parameter with a pattern/length constraint.
- `services/broker/broker.py` —
  - Added `DESTRUCTIVE_OPERATIONS` and `_require_confirmation`.
  - `service_restart` and `service_stop` are denied unless `confirmed_by`
    is present and is not the broker token.
  - `handle_request` now passes the actual `confirmed_by` to `_audit` and
    redacts it only when it equals the token.
- `services/broker/audit.py` — records `confirmed_by` verbatim; the caller
  is responsible for token redaction.
- `services/broker/client.py` — added `--confirm` CLI flag, maps to
  `confirmed_by`.
- `services/aios_core/main.py` —
  - `admin_service_control` accepts `confirm` and requires it for
    non-self `restart`/`stop`.
  - Self-restart/self-stop detached commands include `confirmed_by`.
  - `admin_journalctl_unit` accepts optional `confirm`.
- `services/broker/test_broker.py` and `services/aios_core/test_main.py` —
  updated/added confirmation tests.
- `tests/adversarial/test_root_broker.py` — added
  `test_broker_rejects_destructive_op_without_confirmation`.

## Verification (after confirmation gate)

- `services/broker/test_broker.py` — 16 passed.
- `tests/adversarial/test_root_broker.py` — 11 passed.
- `services/aios_core/test_main.py` — 46 passed.
- `make test-unit` — 410 passed, 2 skipped.
- `make test-adversarial` — 136 passed.

## Remaining work (sandbox sub-slice)

- Add `services/broker/sandbox.py` wrapping the existing
  `services.tool_daemon.landlock` and `services.tool_daemon.seccomp_filter`
  primitives.
- Create `scripts/hardening/hori-systemctl.py` wrapper, validate the
  operation and `confirmed_by`, then apply the sandbox before execing
  `systemctl`.
- Enhance `scripts/hardening/hori-journalctl.py` to apply the same sandbox
  (or a read-only variant).
- Update `services/broker/broker.py` `_run_command` to call the wrappers
  (`/usr/local/sbin/hori-systemctl` and `/usr/local/sbin/hori-journalctl`)
  and pass the full JSON request on stdin.
- Update `services/broker/audit.py` to record the resulting
  `sandbox_profile` (`seccomp`, `seccomp+landlock`, `denied`, etc.).
- Update `scripts/hardening/install_root_broker.sh` to install the new
  wrappers and emit narrow sudoers rules that allow only the two wrappers.
- Add adversarial tests in `tests/adversarial/test_root_broker.py` for:
  - confirmation identity cannot be the broker token,
  - confirmation identity rejects shell meta-characters,
  - sandbox blocks `ptrace`,
  - sandbox blocks opening `/etc/shadow` or `~/.ssh/authorized_keys`,
  - sandbox blocks TCP/UDP sockets,
  - audit records `confirmed_by` and `sandbox_profile`.
- Live smoke test:
  - unconfirmed `service_stop --service llamacpp` returns `denied`,
  - confirmed `service_stop --service llamacpp` returns `ok` and
    restarts,
  - audit entry contains `confirmed_by` and `sandbox_profile`.

## Open questions / decisions needed

- Sandbox path allowlist: which runtime paths does `systemctl` actually
  need? The PRD leaves this to implementation; the wrapper should fail
  closed and the allowlist can be expanded if `journalctl`/`systemctl`
  fail during the live smoke test.
- Should `service_status` be sandboxed as well? The current design
  sandboxes all children, but `service_status` is read-only and may
  run with a lighter profile.
- Installer: does `hori-systemctl` use the repo venv Python or the system
  Python? The wrapper must be able to import `services.tool_daemon.landlock`
  and `services.tool_daemon.seccomp_filter` from the repo.

## Safety notes

- Human `<user>` still has `sudo` and `docker` access; the broker is the
  only privileged path being hardened in this slice.
- `service_stop` against `aios_core` or `system-root-broker` itself is
  now gated by `confirmed_by`, but the live smoke test should use
  `llamacpp` and restart it immediately.

# Handoff: System Root Broker — Slice 1

**Date:** 2026-08-30  
**Branch:** `slice/system-root-broker-01`  
**Goal (SLICE_LOG.md / `.devin/prd/root-broker.md`):** Replace arbitrary root/sudo use with a narrow, auditable `system-root-broker` that can only perform allowlisted privileged operations. Slice 1 is limited to `service_status` and `service_restart` for a fixed allowlist of units.

## Why we are doing this

Security audit found that the `<user>` account had broad root-equivalent access through `(ALL : ALL) ALL` sudoers, `NOPASSWD: /usr/bin/sh`, and `docker` group membership. The `system-root-broker` is the replacement path: agents and the admin panel ask the broker for specific, parameterized operations; the broker validates, audits, optionally confirms, and executes them with minimal privilege.

## Current state

- The `system-root-broker` systemd service is installed and active.
- The broker runs as the dedicated unprivileged user `root-broker:root-broker`.
- The Unix socket is at `/run/system-root-broker/broker.sock` with permissions `srw-rw---- root-broker:root-broker`.
- The invoking user (`<user>`) is a member of the `root-broker` group, so it can connect via `sg root-broker`.
- Allowlist and token live in `/etc/system-root-broker/`.
- `service_status` and `service_restart` are implemented; the broker uses narrow sudoers rules for `root-broker` to run `systemctl is-active/restart/stop` on the allowlisted units.

## The blocker we just hit

The first post-install smoke test:

```bash
sg root-broker -c 'PYTHONPATH=. ./venv/bin/python3 -c "from services.broker.client import RootBrokerClient; c = RootBrokerClient(); print(c.call(\"service_status\", {\"service\": \"llamacpp\"}))"'
```

returned:

```
{'status': 'error', 'error': "[Errno 13] Permission denied: '/var/log/system-root-broker/audit.jsonl'"}
```

### Root cause

The audit log was created with mode `0640` (`rw-r-----`), group `root-broker`. The broker daemon runs as `root-broker`, so it had read-only access to its own audit log and could not append. The actual `systemctl is-active llamacpp` was executed successfully (visible in `systemctl status system-root-broker`); the error was thrown afterward when the broker tried to write the audit entry.

### Fix in the repo

Two files were patched in this session:

- `scripts/hardening/install_root_broker.sh` — audit log is now created as `root:root-broker` `0620` (group write-only, read for root), matching the tool-daemon audit log permission-separation pattern.
- `services/broker/audit.py` — docstring corrected from `0640` to `0620` to match the intended permission model.

## Files changed on this branch (relevant to this slice)

- `scripts/hardening/install_root_broker.sh`
- `services/broker/audit.py`
- `services/broker/broker.py` (prior work on this slice)
- `services/broker/system-root-broker.service` (prior work on this slice)
- `services/broker/client.py` (client library)
- `services/aios_core/main.py` (may contain admin-panel integration; needs verification)
- `scripts/hardening/audit_service_bindings.sh`

The branch also contains many unrelated uncommitted changes (Open-WebUI, macOS, memory, etc.). Do **not** merge or rebase without reviewing them.

## Live fix still needed

The running system still has the old `0640` audit file. Run as root:

```bash
sudo chmod 0620 /var/log/system-root-broker/audit.jsonl
sudo chown root:root-broker /var/log/system-root-broker/audit.jsonl
```

Then re-run the smoke test:

```bash
sg root-broker -c 'PYTHONPATH=. ./venv/bin/python3 -c "from services.broker.client import RootBrokerClient; c = RootBrokerClient(); print(c.call(\"service_status\", {\"service\": \"llamacpp\"}))"'
```

You can also re-run the install script, which now sets the correct mode:

```bash
sudo bash scripts/hardening/install_root_broker.sh
```

## Completed in this session

- ✅ Fixed audit log permission bug (`0640` → `0620`) in `install_root_broker.sh` and `services/broker/audit.py`.
- ✅ Applied live `chmod 0620 /var/log/system-root-broker/audit.jsonl`.
- ✅ Verified `service_status` for all four allowlisted services.
- ✅ Live end-to-end `service_restart` test via `scripts/hardening/test_broker_restart.sh` passed (dummy unit created, restarted, cleaned up).
- ✅ `services/broker/test_broker.py` (8) and `tests/adversarial/test_root_broker.py` (6) pass.

## Completed in this session

- ✅ Fixed audit log permission bug (`0640` → `0620`) in `install_root_broker.sh` and `services/broker/audit.py`.
- ✅ Applied live `chmod 0620 /var/log/system-root-broker/audit.jsonl`.
- ✅ Verified `service_status` for all four allowlisted services.
- ✅ Live end-to-end `service_restart` test via `scripts/hardening/test_broker_restart.sh` passed.
- ✅ `services/broker/test_broker.py` (8), `tests/adversarial/test_root_broker.py` (6), and `services/aios_core/test_main.py` (42) pass.
- ✅ Migrated `services/aios_core/main.py` admin `/admin/api/service/restart` to use `RootBrokerClient`.
- ✅ Added `SupplementaryGroups=root-broker` drop-in creation to `install_root_broker.sh`.
- ✅ Updated `setup_admin_sudoers.sh` to remove legacy `aios-admin`/`hori-admin` and preserve the admin token.
- ✅ Removed critical `/etc/sudoers.d/10-devin` (`/usr/bin/systemctl`, `sh`, `sed`, `cp`, `tee` NOPASSWD).

## Remaining live steps

1. Run `sudo bash scripts/hardening/install_root_broker.sh` to create the `aios_core` drop-in and `systemctl daemon-reload`.
2. Run `sudo systemctl restart aios_core` to apply `SupplementaryGroups=root-broker`.
3. Run `sudo bash scripts/hardening/setup_admin_sudoers.sh` to remove the stale `aios-admin` sudoers.
4. Run `sudo visudo -c` and `PYTHONPATH=. ./venv/bin/python3 -m pytest services/broker/test_broker.py tests/adversarial/test_root_broker.py services/aios_core/test_main.py`.
5. Test the admin panel: `/admin/api/service/restart?service=llamacpp` with bearer token.
6. Commit the slice and demo.

## Important note for next session

The `aios_core` service is currently running and was started *before* `<user>` was added to the `root-broker` group. Until `aios_core` is restarted, even a patched `aios_core/main.py` cannot connect to the broker socket. The first restart must be done manually (or with the current `sudo` path) after the drop-in is added.

## Important constraints

- **Do not change sudoers for the human user in this slice.** Slice 1 explicitly defers removing `<user>` from `sudo`/`docker` until the broker is proven. The only sudoers rules that should exist are the narrow ones for the `root-broker` user in `/etc/sudoers.d/system-root-broker`.
- **Do not broaden the broker allowlist.** Slice 1 is `service_status` and `service_restart` for `aios_core`, `aios-sherpa`, `aios-tool-daemon`, `llamacpp` only.
- The Devin session cannot run `sudo` commands interactively; live permission fixes must be run by the user in a root shell.

## How to resume this work

1. Read `.devin/prd/root-broker.md` and `.devin/SLICE_LOG.md`.
2. Read this handoff.
3. Run the live `chmod` above.
4. Re-run the smoke test and continue with the next steps list.

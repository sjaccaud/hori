# System Root Broker — Product Requirements Document

## Problem

The <user> account has broad root-equivalent access through:
- `(ALL : ALL) ALL` in sudoers
- `NOPASSWD: /usr/bin/sh` in a Devin drop-in
- Membership in the `docker` group
- A pre-hardened Devin config that allowed `Exec(sudo)`, `Exec(bash)`, `Write(/etc)`, `Fetch(domain:*)`, etc.

We have already tightened the Devin user and `pyxx-workspace` project configs. The remaining root attack surface is the host: sudoers, the `docker` group, and the few legitimate privileged operations that still require root (restarting system services, installing packages, writing system files, running benchmarks that touch hardware).

The current pattern — agents and the admin panel directly invoking `sudo systemctl ...` or `sudo apt-get ...` — is the opposite of defense-in-depth. It grants root for the lifetime of the command and relies on the agent to stay well-behaved.

## Goal

Replace arbitrary root access with a **narrow, auditable, confirmable root broker** that is the only path from the `<user>` account to privileged operations. Agents and the admin panel no longer use `sudo` directly. They request a specific, parameterized operation from the broker. The broker validates the operation, logs it, optionally confirms it, executes it inside a tight sandbox, and returns the result.

## Non-Goals

- Not a general remote-execution or arbitrary-shell service.
- Not a replacement for all `sudo` use by interactive human `<user>` in a terminal (that is a separate, lower-priority hardening step).
- Not a full implementation in this slice; this PRD defines the contract and acceptance tests for the first implementation slice.

## High-Level Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                         unprivileged <user>                            │
│  Devin / aider / admin panel / aios_core → Unix socket request       │
└───────────────────────┬──────────────────────────────────────────────┘
                        │  /run/system-root-broker/broker.sock (0600 <user>:root-broker)
                        ▼
┌──────────────────────────────────────────────────────────────────────┐
│           system-root-broker (systemd service, dedicated user)         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────┐ │
│  │ Auth + token │→ │ Allowlist    │→ │ Confirm gate │→ │ Sandbox  │ │
│  │ validation   │  │ + per-op     │  │ (human/out-of│  │ executor │ │
│  │              │  │   schema     │  │  band)       │  │          │ │
│  └──────────────┘  └──────────────┘  └──────────────┘  └─────┬────┘ │
│                                                                │      │
│  ┌─────────────────────────────────────────────────────────────┘      │
│  │ Audit log append → /var/log/system-root-broker/broker.jsonl (root:root-broker 0640)│
│  │ Sherpa/auditd monitoring hook → /run/sherpa/...                   │
│  └────────────────────────────────────────────────────────────────────┘
└──────────────────────────────────────────────────────────────────────┘
```

## Components

### 1. Broker service (`system-root-broker`)

- Runs as a dedicated, unprivileged system user `root-broker` (nologin shell, no `sudo`/`docker`/`adm` groups).
- Started by systemd. The unit is root-owned under `/etc/systemd/system/system-root-broker.service`.
- Listens on a Unix domain socket at `/run/system-root-broker/broker.sock`.
- Socket permissions: `0660 <user>:root-broker`, so only `<user>` can write and only the broker can read.
- Loads its allowlist from `/etc/system-root-broker/broker-allowlist.json` and its auth token from `/etc/system-root-broker/broker-secrets.env`.
- Drops capabilities after startup; uses `NoNewPrivileges=true`, `ProtectSystem=strict`, `ProtectHome=true`, `PrivateTmp=true`.

### 2. Client library (`root_broker_client`)

- A small Python module in `services/aios_core/` and `scripts/hardening/`.
- Takes an operation name + parameters, serializes a request, sends it over the socket, and returns the broker's JSON response.
- The admin panel (`services/aios_core/main.py`) replaces `subprocess.run(["sudo", "systemctl", "restart", ...])` with a broker client call.
- The agent hardening scripts call the broker instead of `sudo`.

### 3. Allowlist (`/etc/system-root-broker/broker-allowlist.json`)

A JSON file that declares, per operation:
- `services` that may be restarted/statused
- `packages` / `flatpaks` / `snaps` / `docker_images` that may be installed
- `paths` that may be written
- `commands` / `scripts` that may be executed
- `max_count`, `max_size`, `allowed_env_vars`, etc.

Only operations that match the allowlist are executed. A denylist overrides the allowlist.

### 4. Confirmation gate

- **Tier 1 (read-only / low impact)**: no extra confirmation. Examples: `service_status`, `journalctl`.
- **Tier 2 (reversible / low impact)**: single on-device prompt or UI approve. Examples: `service_restart`, `package_install --dry-run`.
- **Tier 3 (destructive / irreversible)**: out-of-band or explicit human confirm with a delay. Examples: `package_remove`, `system_shutdown`, `write_system_file` to `/etc`, `systemctl mask`, `docker_run --privileged` (denied anyway).

The broker can implement Tier 1/Tier 2 synchronously and Tier 3 by writing to a pending-confirm queue and waiting for a human `/admin` approve or a second auth token.

### 5. Sandbox executor

For every privileged command, the broker spawns a short-lived child with:
- `PR_SET_NO_NEW_PRIVS` / `NoNewPrivileges=true`
- seccomp filter allowing only the exact syscalls needed for the operation
- Landlock or bubblewrap restricting filesystem write to the target path
- A hard timeout (60s default, 300s for long package installs)
- No network for install ops unless required; when required, egress is filtered to the package host
- Capability bounding set cleared; only `CAP_NET_BIND_SERVICE` etc. if explicitly needed

### 6. Audit log

Every request and result is appended to `/var/log/system-root-broker/broker.jsonl` in this schema:

```json
{
  "timestamp": "2026-08-30T09:00:00Z",
  "request_id": "uuid",
  "caller_uid": 1000,
  "caller_exe": "/home/<user>/Projects/aios/.venv/bin/python3",
  "operation": "service_restart",
  "params": {"service": "aios_core"},
  "allowed": true,
  "confirmed_by": "bearer-token-abc",
  "executed_at": "2026-08-30T09:00:01Z",
  "exit_code": 0,
  "stdout_hash": "sha256:...",
  "stderr_hash": "sha256:...",
  "sandbox_profile": "service_restart",
  "revert_id": null
}
```

The log is owned `root:root-broker` with mode `0640`; the broker can append but cannot read or truncate. This matches the existing tool audit log pattern.

## Operation Catalog (Slice 1)

The first implementation slice supports only the operations proven necessary by the audit and existing hardening scripts. New ops are added by PRD amendment.

| Operation | Params | Confirmation | Reversible | Notes |
|-----------|--------|--------------|------------|-------|
| `service_restart` | `service` (allowlist) | Tier 2 | Yes via `service_start` | Replaces sudoers for system services. |
| `service_status` | `service` (allowlist) | Tier 1 | N/A | Read-only. |
| `service_stop` | `service` (allowlist) | Tier 2 | Yes |  |
| `journalctl_unit` | `unit`, `lines`, `since` | Tier 1 | N/A | Read-only. |
| `package_install` | `packages[]`, `manager` (apt/pip/flatpak/snap), `dry_run` | Tier 2, then Tier 3 if destructive | Partial (uninstall list saved) |  |
| `package_remove` | `packages[]`, `manager` | Tier 3 | No |  |
| `write_system_file` | `path`, `content_hash`, `mode`, `backup` | Tier 3 | Yes if `backup=true` | Path allowlist: `/etc/system-root-broker/*`, `/etc/systemd/system/*.service (restricted to allowlisted unit names)`, `/etc/udev/rules.d/*`, `/opt/krill_harvester/*` |
| `run_approved_script` | `script_path`, `args[]`, `timeout` | Tier 2 | Depends on script | Allowlist: `benchmark-llamacpp.sh`, `post_reboot_health.sh`, etc. |
| `system_shutdown` | `action` (shutdown/reboot/suspend) | Tier 3 | No | Delayed execute with a cancel window. |
| `rocm_smi` | `args[]` | Tier 1 | N/A | Read-only; no `--compute` etc. that can change state. |
| `docker_run` | `image` (allowlist), `command` (allowlist), `volumes` (allowlist), `network` (deny unless host) | Tier 3 | No | Runs unprivileged, no `--cap-add`, no `--privileged`, rootless-compatible. |

**Denied by default** (never allowed):
- Arbitrary `sudo`, `su`, `pkexec`, `doas`, `bash`, `sh`, `python3`, `node`, `npx`
- `apt-get` / `apt` without explicit package list
- `docker run` for images not in allowlist
- `systemctl start/stop/restart` for services not in allowlist
- Writes outside the path allowlist

## Request / Response Contract

Request schema:

```json
{
  "request_id": "uuid",
  "timestamp": "ISO8601",
  "token": "bearer-token",
  "operation": "service_restart",
  "params": {"service": "aios_core"},
  "confirm": {"method": "ui", "code": "otp-or-null"}
}
```

Response schema:

```json
{
  "request_id": "uuid",
  "status": "ok|pending|denied|error|timeout",
  "exit_code": 0,
  "stdout": "...",
  "stderr": "...",
  "log_offset": 12345,
  "revert_id": "uuid-or-null"
}
```

## Auth

- Broker requests must include the bearer token from `/etc/system-root-broker/broker-secrets.env`.
- The admin panel already has `HORI_ADMIN_TOKEN`. The broker can share or rotate that token; for separation, a dedicated `ROOT_BROKER_TOKEN` is preferred.
- The Unix socket itself is an auth layer: only `<user>` can connect.
- Tokens are not logged.

## Deployment

1. Create user `root-broker` and group `root-broker`.
2. Install broker to `/opt/system-root-broker/` or `/home/<user>/Projects/aios/services/broker/`.
3. Install systemd unit and socket: `system-root-broker.socket` + `system-root-broker.service`.
4. Create `/etc/system-root-broker/broker-allowlist.json` and `/etc/system-root-broker/broker-secrets.env`.
5. Update admin panel to call broker client for service restarts.
6. Update `/etc/sudoers` to remove broad rules and add only `root-broker` NOPASSWD for the minimal commands the broker itself needs (e.g., `systemctl` restricted by `Cmnd_Alias` if the broker cannot run it via PolicyKit).
7. Remove `<user>` from `docker` group; broker does `docker` via a separate `docker-broker` user or rootless Docker.

## Sudoers Migration Plan

The end-state sudoers should have **no** `<user>` root shell, `sh`, `sed`, `cp`, `tee`, or broad `systemctl`. Instead:

- `<user>` may run **zero** commands as root directly.
- The `system-root-broker` service runs as `root-broker` and can run the allowlisted commands.
- If the broker needs to run `systemctl`, it does so as `root-broker` via a PolicyKit action or a very narrow sudoers rule: `root-broker ALL=(root) NOPASSWD: /usr/bin/systemctl restart aios_core, ...`.
- The user `<user>` may be in a `broker-clients` group to access the socket, but not in `sudo` or `docker`.

## Test Contract

The implementation slice is complete when these tests pass:

1. **Positive**
   - `service_restart` with allowlisted service succeeds and appends to audit log.
   - `service_status` returns the unit state.
   - `write_system_file` to allowlisted path succeeds and creates a `.bak` first.
   - `package_install` with allowlisted packages succeeds in dry-run without root execution.

2. **Negative**
   - Request with wrong token is denied.
   - `service_restart` with non-allowlisted service is denied.
   - `write_system_file` outside allowlist is denied.
   - `docker_run` with `--privileged` or non-allowlisted image is denied.
   - Broker cannot read or truncate its own audit log.
   - Sending `Exec(sudo)` or `Exec(bash)` through `run_approved_script` is denied.

3. **Property**
   - Audit log is append-only and tamper-evident (monotonic counter + hash chain).
   - Each operation times out and is killed if it exceeds the limit.
   - Broker child processes cannot `setuid`, `ptrace`, mount, or `chroot`.
   - Reverting a `write_system_file` restores the previous content.

## Acceptance Criteria

- [ ] `services/broker/PRD.md` or `docs/root_broker.md` is approved by product owner.
- [ ] Broker systemd unit and socket are installable via `scripts/hardening/install_root_broker.sh`.
- [ ] admin panel service-restart path uses the broker.
- [ ] Existing `setup_admin_sudoers.sh` is replaced or updated to install only broker rules.
- [ ] Audit log schema matches the system tool audit log.
- [ ] Adversarial tests in `tests/adversarial/test_root_broker.py` pass.

## Open Questions

1. Should the broker share the existing `HORI_ADMIN_TOKEN` or use a dedicated `ROOT_BROKER_TOKEN`?
2. Should `package_install` be implemented for `apt` first, or for `pip`/`flatpak`/`snap` in parallel?
3. Should the broker use Python (faster for this stack, reuse `services/aios_core/` code) or Go (matches `krill_harvester` DEFCON supervisor pattern)?
4. Can the existing `aios-worker` user be reused, or is a new `root-broker` user required?
5. Does the current `docker` group membership need to be removed in this slice, or in a follow-up slice after rootless Docker/broker is proven?

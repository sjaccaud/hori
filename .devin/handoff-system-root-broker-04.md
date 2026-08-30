# Handoff — System Root Broker Slice 4

**Slice:** sudoers hygiene and `cli-admin` break-glass — COMPLETE
**Branch:** `slice/system-root-broker-04`
**Date:** 2026-08-30

## What was built

- `scripts/hardening/install_cli_admin.sh` creates a `cli-admin` account
  with a real shell and `sudo` group membership as a break-glass /
  maintenance account. The product owner sets and records the password
  offline.
- The same installer removes the primary `<user>` from the `lxd`,
  `ollama`, `lpadmin`, `cdrom`, and `dip` groups, deletes stale
  `/etc/sudoers.d/*.bak.*` and old per-user fragments
  (`<user>-shutdown`, `nopasswd-suspend`, `pyxx`, `pyxx-fail2ban`), and
  writes `/etc/sudoers.d/hori-user` with the consolidated safe NOPASSWD
  rules (shutdown/poweroff, `systemctl suspend`, `ufw status`,
  `fail2ban-client status`).
- `scripts/hardening/test_cli_admin.sh` verifies: `cli-admin` exists with
  bash/sudo, `<user>` is out of the removed groups, stale sudoers are
  gone, `visudo -c` passes, and `cli-admin` can use `sudo`.
- `scripts/hardening/setup_admin_sudoers.sh` no longer creates `.bak.*`
  files when removing legacy admin sudoers.

## What was deliberately NOT changed

- The primary `<user>` keeps `sudo` and `docker` group membership. The
  broker currently only gates `systemctl`/`journalctl` for four services;
  removing `sudo`/`docker` from the primary account would break Devin CLI
  for routine rig optimization (`rocm-smi`, `apt`, `docker`, `ufw`,
  `fail2ban`, kernel/GPU tweaks, etc.).
- `plugdev` is left in place because `openrazer` likely needs USB device
  access.

## Verification

- `su - cli-admin` → `sudo systemctl status llamacpp` succeeds.
- Back as the primary `<user>`:
  - `sudo ./scripts/hardening/test_cli_admin.sh` passes.
  - `sudo ./venv/bin/python3 -m services.broker.client service_status --service llamacpp`
    returns `sandbox_profile: "seccomp+landlock"`.
  - `make test` — all suites green.

## State on disk

- `/etc/sudoers.d/` contains only:
  - `README`
  - `hori-user` (consolidated NOPASSWD rules)
  - `system-root-broker` (broker wrappers only)
- Active `sudo` group membership:
  - primary `<user>`
  - `cli-admin` (break-glass)
- Removed from primary `<user>`: `lxd`, `ollama`, `lpadmin`, `cdrom`, `dip`.

## Open questions / next slice candidates

1. **Remove primary `<user>` from `sudo`/`docker`:** needs either an
   expanded broker (more privileged operations routed through the broker)
   or a narrow NOPASSWD allowlist for the specific commands Devin CLI
   needs, plus a proven break-glass path — now available via `cli-admin`.
2. **Tamper-evident audit chaining:** candidate for a future broker slice.
3. **Continue macOS work:** the paused `slice/macos-project-context` is
  still available if this is the right time to switch back.

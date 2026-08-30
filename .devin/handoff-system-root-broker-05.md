# Handoff — SLICE-SYSTEM-ROOT-BROKER-05

## Slice completed

Read-only GPU / Docker / firewall / fail2ban status queries through the system-root-broker.

## Where the code lives

- Branch: `rebrand/hori` (fast-forward merged from `slice/system-root-broker-05`)
- Last slice commit: `b9bb524` — `[SLICE-05] Allow /usr/bin/python3 in the ufw_status sandbox.`
- `rebrand/hori` is at `b9bb524`.

## What changed

- `services/broker/allowlist.py` — added `gpu_status`, `docker_status`,
  `ufw_status`, `fail2ban_status` with read-only params.
- `services/broker/broker.py` — new operation handlers.
- `services/broker/client.py` — CLI flags: `--query`, `--jail`, `--card`.
- `services/broker/sandbox.py` — per-operation Landlock allowlists.
- `scripts/hardening/hori_{gpu,docker,ufw,fail2ban}.py` — sandboxed wrappers.
- `scripts/hardening/install_root_broker.sh` — installs new wrappers and
  sudoers rules.
- Unit, adversarial, integration, and regression tests updated.

## Verification performed

- `make test-unit` — 417 passed, 2 skipped
- `make test-adversarial` — 147 passed
- `make test-integration` — 25 passed, 2 skipped
- `make test-regression` — 7 passed
- Live demo:
  - `python -m services.broker.client gpu_status`
  - `python -m services.broker.client docker_status --query ps`
  - `python -m services.broker.client ufw_status`
  - `python -m services.broker.client fail2ban_status --jail sshd`
  All returned `status: "ok"` and `sandbox_profile: "seccomp+landlock"`.
- `rebrand/hori` fast-forward merged the slice branch.

## Important configuration

- Broker installed to `/etc/system-root-broker/`, socket at
  `/run/system-root-broker/broker.sock`, sudoers at
  `/etc/sudoers.d/system-root-broker`.
- `<user>` remains in `sudo` and `docker` groups; removing them is a later
  hardening step.
- Wrappers installed at `/usr/local/sbin/hori-{gpu,docker,ufw,fail2ban}`.

## Known issues / surprises

- `ufw` and `fail2ban-client` are Python scripts, so their sandboxes must
  include `/usr/bin/python3`.
- GPU telemetry is read from DRM sysfs and hwmon, not `rocm-smi`, so no
  device-node access is required.
- The install script does not restart the broker if already running; after an
  update, run `sudo systemctl restart system-root-broker` manually.

## Next likely slices

- SLICE-SYSTEM-ROOT-BROKER-06: Remove `<user>` from `sudo` and `docker` after
  proving broker coverage for remaining privileged tasks.
- Or expand broker to package installation / system tuning with confirmation.
- Or add tamper-evident audit chaining.

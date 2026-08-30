# System Root Broker — Slice 5: Read-only GPU / Docker / firewall / fail2ban queries

## 1. Intent trace

- **Manifesto Pillar:** VII — Engineering Discipline ("Simplicity as Security; build less, test first") and I — Local-First trust boundary (the human account must not hold root).
- **Roadmap anchor:** Tier 1 security closure (removing the high-risk `<user>` `sudo`/`docker` paths). This slice is the next load-bearing step in `.devin/prd/root-broker.md` after Slice 4 (`cli-admin` break-glass and sudoers hygiene).
- **Off-roadmap justification:** The root broker itself is the enabling infrastructure for Tier 1/Tier 2; it is not a discrete PoC but the replacement path for direct `sudo`.

## 2. Interface contract

All operations are **read-only / Tier 1**. They do **not** require `confirmed_by`. They are routed through the existing `RootBrokerClient` and executed by new sandboxed wrappers.

### Broker client

```python
from services.broker.client import RootBrokerClient

c = RootBrokerClient()

c.call("gpu_status")  # rocm-smi/nvidia-smi summary
c.call("docker_status", {"query": "ps"})       # docker ps --format json
c.call("docker_status", {"query": "system_df"}) # docker system df --format json
c.call("docker_status", {"query": "info"})      # docker info (structured)
c.call("ufw_status")
c.call("fail2ban_status")                         # jail list
c.call("fail2ban_status", {"jail": "sshd"})      # jail status
```

Response schema (same as existing broker responses):

```json
{
  "request_id": "<uuid>",
  "status": "ok|denied|error|timeout",
  "exit_code": 0,
  "stdout": "...",
  "stderr": "...",
  "sandbox_profile": "seccomp+landlock"
}
```

For `gpu_status` the broker parses the underlying `rocm-smi --json` output and returns a `parsed` field in addition to `stdout`:

```json
{
  "parsed": {
    "cards": [
      {
        "name": "AMD Radeon AI PRO R9700",
        "pci_id": "0x7551",
        "temperature_edge_c": 32.0,
        "temperature_junction_c": 35.0,
        "temperature_memory_c": 30.0,
        "fan_percent": 20,
        "fan_rpm": 1072,
        "power_w": 21.0,
        "vram_total_b": 34208743424,
        "vram_used_b": 18966749184,
        "clocks": {"dcefclk": "243Mhz", "fclk": "815Mhz", "mclk": "96Mhz", "sclk": "174Mhz", "socclk": "417Mhz"}
      }
    ]
  }
}
```

For `docker_status` the broker returns the raw JSON output in `stdout` for `ps` and `system_df`, and a `parsed` summary for `info`:

```json
{
  "query": "ps",
  "stdout": "[{...json object per container...}\\n...]",
  "stderr": ""
}
```

For `ufw_status` and `fail2ban_status` the broker returns the command's text output in `stdout`.

### Client CLI

```bash
python -m services.broker.client gpu_status
python -m services.broker.client docker_status --query ps
python -m services.broker.client docker_status --query system_df
python -m services.broker.client docker_status --query info
python -m services.broker.client ufw_status
python -m services.broker.client fail2ban_status [--jail sshd]
```

### Admin panel HTTP (optional / out of scope for this slice)

The admin panel may be extended in a later slice to display GPU, Docker, UFW, and fail2ban state. This slice only exposes the broker operations; wiring them to `/admin` is deferred.

## 3. Safety classification

All four operations are **read-only** and **Tier 1**. They never modify system state.

| Operation | Class | Confirmation | Reversible | Safety-spine layers |
|-----------|-------|--------------|------------|---------------------|
| `gpu_status` | read-only | None | N/A | Unix socket group; bearer token; allowlist (no args or safe `card` index); sandboxed wrapper; audit log. |
| `docker_status` | read-only | None | N/A | Unix socket group; bearer token; allowlist (`query` ∈ `{ps, system_df, info}`); sandboxed wrapper; audit log. |
| `ufw_status` | read-only | None | N/A | Unix socket group; bearer token; allowlist (no args); sandboxed wrapper; audit log. |
| `fail2ban_status` | read-only | None | N/A | Unix socket group; bearer token; allowlist (optional `jail` pattern); sandboxed wrapper; audit log. |

**Fail-closed behavior:**
- Unknown operation, non-allowlisted `query`, bad `jail` name, or shell-injection attempts → `denied`.
- The broker never constructs a shell command from an untrusted string.
- Each wrapper applies Landlock/seccomp before executing its narrow command and returns the resulting `sandbox_profile`.
- `docker_status` is restricted to `ps`, `system_df`, and `info` only. It must never run `docker run`, `docker exec`, `docker rm`, `docker rmi`, `docker pull`, `docker push`, or any state-changing subcommand.
- `gpu_status` is restricted to read-only `rocm-smi`/`nvidia-smi` flags. It must never run `--set*`, `--reset*`, `--ras*`, or any state-changing subcommand.

## 4. Test contract

### Unit / integration tests

`services/broker/test_broker.py`:
- `test_gpu_status_success` — `gpu_status` returns a JSON `parsed` field with at least one card.
- `test_gpu_status_parses_temperature_and_vram` — the parsed output contains `temperature_edge_c` and `vram_used_b`.
- `test_docker_status_ps_success` — `docker_status` with `query=ps` returns `stdout` and `status=ok`.
- `test_docker_status_system_df_success` — `docker_status` with `query=system_df` returns `stdout` and `status=ok`.
- `test_docker_status_info_success` — `docker_status` with `query=info` returns a `parsed` summary.
- `test_ufw_status_success` — `ufw_status` returns `stdout` starting with `Status:`.
- `test_fail2ban_status_success` — `fail2ban_status` returns `stdout` containing `Number of jail` or `Jail list`.
- `test_fail2ban_status_jail_success` — `fail2ban_status` with `jail=sshd` returns `stdout` containing the jail name.
- `test_docker_status_rejects_invalid_query` — `query=rm` is denied by the allowlist.
- `test_fail2ban_status_rejects_invalid_jail` — a jail name containing shell meta-characters is denied.

### Adversarial tests

`tests/adversarial/test_root_broker.py`:
- `test_broker_rejects_gpu_status_state_change` — a `gpu_status` request with a state-changing flag (e.g., `--setfan`) is denied before it reaches `rocm-smi`.
- `test_broker_rejects_docker_status_state_change` — a `docker_status` request with `query=exec` or `query=run` is denied.
- `test_broker_rejects_docker_status_shell_injection` — a `docker_status` request with a `query` containing `;`, `$`, `|`, or backticks is denied.
- `test_broker_rejects_fail2ban_shell_injection` — a `fail2ban_status` request with a `jail` containing shell meta-characters is denied.
- `test_broker_gpu_status_sandbox_blocks_etc_shadow` — a sandboxed child cannot read `/etc/shadow` after applying the `gpu_status` sandbox.
- `test_broker_docker_status_sandbox_denies_network_connect` — a sandboxed child cannot create a TCP socket after applying the `docker_status` sandbox.
- `test_broker_query_wrappers_are_invoked_for_new_operations` — the broker dispatches `gpu_status`, `docker_status`, `ufw_status`, and `fail2ban_status` to the correct wrapper paths.

## 5. Module placement

- `services/broker/allowlist.py` — add `gpu_status`, `docker_status`, `ufw_status`, and `fail2ban_status` to `Allowlist.DEFAULT` with their allowed parameters.
- `services/broker/broker.py` — add `_op_gpu_status`, `_op_docker_status`, `_op_ufw_status`, and `_op_fail2ban_status` handlers that call the new wrappers.
- `services/broker/client.py` — add the new operations to the CLI `choices` and parameter parser.
- `services/broker/sandbox.py` — add per-operation allowed-path lists for `gpu_status`, `docker_status`, `ufw_status`, and `fail2ban_status`. This includes read-only paths for `/sys/class/drm`, `/sys/bus/pci`, `/dev/kfd`, `/dev/dri` (GPU), and the read-only files needed by `docker`, `ufw`, and `fail2ban-client`. It may require extending `AllowedPath` to support `ioctl_dev` for GPU device nodes.
- `scripts/hardening/hori_gpu.py` — new wrapper: validates `gpu_status` request, applies sandbox, runs a read-only `rocm-smi`/`nvidia-smi` command, and parses the JSON output into a `parsed` field.
- `scripts/hardening/hori_docker.py` — new wrapper: validates `query` ∈ `{ps, system_df, info}`, applies sandbox, runs the narrow `docker` subcommand, and returns the result.
- `scripts/hardening/hori_ufw.py` — new wrapper: validates no arguments, applies sandbox, runs `ufw status`.
- `scripts/hardening/hori_fail2ban.py` — new wrapper: validates optional `jail` name pattern, applies sandbox, runs `fail2ban-client status [jail]`.
- `scripts/hardening/install_root_broker.sh` — install the four new wrappers and update the `system-root-broker` sudoers rule to allow `root-broker` to run them with `NOPASSWD`.
- `services/broker/test_broker.py` — unit/integration tests for the new operations.
- `tests/adversarial/test_root_broker.py` — adversarial tests for the new operations.

## 6. Out of scope

- Removing `<user>` from `sudo` or `docker` (final hardening step, deferred to Slice 6 or later after the broker path is proven).
- Tamper-evident audit chaining (candidate future slice).
- Extending the admin panel with new `/admin/api/*` routes for these queries (can be added once the broker operations exist; this slice leaves them accessible via the client CLI).
- GPU state-changing commands (overclocking, fan control, resets).
- Docker state-changing commands (`run`, `exec`, `rm`, `rmi`, `pull`, `push`, `build`).
- UFW state-changing commands (`allow`, `deny`, `delete`, `enable`, `disable`, `reload`).
- fail2ban state-changing commands (`set`, `ban`, `unban`, `reload`, `start`, `stop`, `restart`).

## 7. Work-order link

- `core/state/proposed_work_orders/system-root-broker-05.json`

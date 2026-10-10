# Rig Trust Scaffold — Model-Agnostic Safety Layer

Status: PHASES 0-6 BUILT + VERIFIED 2026-10-09 evening (ahead of the
planned weekend — implementation session). Phase 4.5 console shipped same
night (/trust page + posture strip). Follow-ups 2026-10-09 late: deny.conf
parser ported to local_tools_server (OWUI path now serves minted citations,
contract check local_tools_deny), guarded-change v2 gains auto-detected
apt/dpkg package-state revert via apt-state-restore.sh.
27B/32GB rig, no card-2 dependency — as designed.

Verified live: deny hook 16/16 verdicts; pre-mutate snapshots landing
(pre-agent-*/guard-* restic tags); guarded-change all three paths
(happy/verify-fail-immediate-revert/orphaned-TTL-revert) against real
/etc mutations; probation promote+expire; invariants 8/8; self-test 10/10;
deployment_contract=PASS with new guardrails check; snapshot injection
carries 'guardrails:' posture line into every gated OWUI prompt.

## Purpose

Make the rig safe to operate under *any* agent — local 27B, future 120B,
or cloud SWE-2 — by moving trust from "the model reads the rules" to
"the mechanism enforces them." Advisory layers (AGENTS.md, thread state)
remain, but nothing load-bearing depends on a model obeying prose.

Threat model: confused/misaligned-with-context local model executing
confident-but-wrong commands; prompt injection via fetched content;
operator fatigue approving bad plans; availability loss of cloud models
(ban/KYC/vend risk) requiring heavier reliance on local inference.

Design sentence: **the rig should stay safe even if the model is wrong.**

## Architecture decision

Enforcement lives at the exec chokepoint, not the chat surface.
`local_tools_server.py` is the sole owner of `run_safe_command` and
already carries deny policy + audit log — the new layers plug in there
so every frontend (OWUI, Devin CLI, Aider, SSH) inherits them for free.
Devin CLI additionally gets a PreToolUse/exec wrapper so commands that
bypass local-tools still hit the same deny/snapshot path.

Chat surfaces stay conversational; trust surfaces show state (below).

## Phase 0 — Prove the floor (Fri evening ~1h)

Nothing builds on unverified backups.

- [ ] `restic check` + restore drill of an `/etc` subtree to /tmp
      (closes open item in `system-stability`)
- [ ] Confirm `grub-reboot` one-shot boot works (precondition for
      boot-counted kernel testing later)
- [ ] `~/scripts/rig-health-check.sh` — probes `:8080/v1/models`, SSH
      listener, `rocminfo`, `power1_cap` ∈ {210,330}, clean boot state.
      Exit 0/1. Becomes THE verification primitive.

Gate: restore verified + health script green. Stop here if not.

## Phase 1 — Minted denylists (Sat AM ~2h)

Incidents harden into mechanism.

- [ ] `~/scripts/agent-deny.conf` — machine-readable denylist:
      `/dev/i2c-*` reads, `ddcutil`, `i2cdetect`, MCR/Low-Latency writes,
      `power1_cap` writes outside ai-power-manager. Each entry cites the
      episode that minted it.
- [ ] Enforce in `run_safe_command` (local-tools) + Devin exec wrapper.
      Deny → log + return citation, never silent.
- [ ] Seed from system-stability eps 009–010 + Sep i2c/RAS freezes.

Gate: `ddcutil` via wrapper denied with episode citation.

## Phase 2 — Snapshot-before-mutate (Sat early PM ~3h)

- [ ] `~/scripts/pre-mutate-snapshot.sh <label>` — restic tag
      `pre-agent-<session>` scoped to `/etc` + declared touched paths;
      `git add -A && commit` on git-tracked config dirs.
- [ ] Trigger paths (hook/wrapper): writes hitting `/etc`, `fstab`,
      `grub.d`, `systemd`, `/sys/class/drm`, `mkinitcpio` → snapshot
      lands BEFORE the write.
- [ ] `etckeeper` if absent.

Gate: write test file under `/etc/systemd/system/`; snapshot precedes
write in audit log ordering.

## Phase 3 — Dead-man rollback + verification contracts (Sat PM ~4h)

The seatbelt. Core of the weekend.

- [ ] `~/scripts/guarded-change.sh <verify-cmd> <ttl> -- <apply-cmd>`:
  1. `pre-mutate-snapshot`
  2. capture pre-state (file hashes, unit states)
  3. apply
  4. arm `systemd-run --on-active=<ttl>` revert unit
  5. run `<verify-cmd>` (default `rig-health-check.sh`) → success
     disarms; fail/timeout → auto-revert.
- [ ] Convention: no bare apply for config mutations; verify is
      declared up front or the wrapper refuses.

Gate: deliberately break a test unit config → apply via
`guarded-change` → confirm self-heal at TTL expiry.

## Phase 4 — Shrink improvisation surface (Sat evening ~2h)

- [ ] `safe-restart.sh`, `safe-config-edit.sh <file> <verify>`,
      `safe-upgrade.sh` — `gpu-lane.sh` pattern: model supplies
      parameters, never composes the command.
- [ ] `~/runbooks/` — first three runbooks: service restart, model
      swap, driver-adjacent config change. Strict step format +
      declared verify + declared write-scope.

## Phase 5 — Probationary changes (Sun AM ~3h)

- [ ] `safe-config-edit --probationary` → 24h auto-revert unless
      `agent-promote <change>` runs.
- [ ] Policy: agent-applied = probationary by default; permanence is
      opt-in. Generalizes the `--ttl` instinct.

## Phase 6 — Invariants + self-tests (Sun PM ~2h)

- [ ] `~/.config/rig/invariants.conf` + `rig-invariant-check.sh`:
      power cap set, `:8080` healthy, no stray `/dev/dri` writers,
      MCR/Low-Latency OFF, DDR speed nominal. Post-mutation + 15-min
      timer; violations → notify + flag.
- [ ] `guardrail-self-test.timer` (weekly): fake `/etc` write + fake
      `/dev/i2c` read, assert both denied, log to system-stability.
      Guardrails rot silently — the scaffold gets its own smoke test.

## Phase 4.5 — Trust surface (integration with OWUI daily-driver, ep-020)

Enforcement lands at exec layer; this phase makes it visible.

- [x] **script-panel :8770 → rig console.** Tailnet page (`tailscale serve
      --bg --https=8770`, 2026-10-09): armed rollbacks, probationary
      changes awaiting promote, invariant violations, denylist hits —
      plus ✓promote / ↩revert-now buttons on live units
      (POST /api/guard/<act>?gid=). This is the check-from-iPhone
      surface OWUI can't be (chat-first ≠ approval queue).
- [x] **snapshot injection extension.** `state_snapshot.py` adds
      guardrail posture (`denylist=ARMED`, `rollbacks_pending=1`,
      `invariants=PASS`) — every model on every surface sees
      enforcement state without asking.
- [x] **deployment_contract.py checks.** Registered: deny.conf loaded,
      hooks wired, timers enabled, helper root-owned — plus
      `local_tools_deny` (OWUI path serves minted citations, 2026-10-09).
      Safety layer covered by the fail-loudly pattern.
- [x] **Approval-from-phone.** rig-notify gained an action-URL passthrough
      (notify_send.sh arg 3 → Telegram inline button + ntfy Actions view);
      probation-armed and guard-fired notifications carry the /trust link.
      The phone taps Open → /trust → ✓/↩. Server-side the endpoint only
      acts on LIVE guard units — stale links settle harmlessly.
- [x] **Notification hygiene (2026-10-10).** Throttled rig-notify events
      queue to pending.log instead of dropping; rig-notify-flush.timer
      (30 min) sends one grouped digest per tag (deny ×N, inv-* ×N …).
      Synthetic denies (contract probe) mark `"probe": true` — same 422 +
      citation, journaled as agent-deny-probe, no phone push. notify_send.sh
      routes per title via TELEGRAM_CHAT_ID_<TITLE> when extra chats exist.
      Root cause of the 160/day flood: ProtectHome=read-only silently ate
      the service's throttle-stamp writes → ReadWritePaths fix staged.

## Phase 5.5 — Disposable sandbox exec (Band 2 of the capability ladder)

Band 1 was read/report (local-tools). Band 2 gives the model a place to
*do* things where the worst outcome is a messy scratch dir — "check out
this repo and test it" becomes a real, measured capability (2026-10-10).

- [x] **sandbox_tools_server.py :8774** (reclaimed mcpo port). One main
      tool: `sandbox_exec` runs a bounded allowlist inside `bwrap` —
      `~/agent-scratch` is the only writable path (`HOME=/sbx` keeps
      venvs, pip caches, git state inside); `/etc`, `~`, and every
      unbound path are literally nonexistent to the payload. pid/ipc/uts
      unshared; network kept for clone/pip but egress-shaped: clone URLs
      limited to github/gitlab/codeberg/sr.ht/`file://`, no curl/ssh
      anywhere, no git push/fetch/remote/config. Targeted ro-binds for
      `resolv.conf`/`hosts`/`nsswitch.conf`/`/etc/ssl/certs` — never
      `/etc/ssl` wholesale (its private/ dir holds host keys). deny.conf
      overlays the whole command line (incident citations beat every
      layer); calls journaled to `~/.local/share/sandbox-tools/`.
- [x] **Allowlist shaped for "clone, inspect, test".** git
      (`-C <dir>` for in-repo work — no cd exists), file ops, sed,
      python3/pytest/pip, make. Excluded: `env`/`xargs`/`nohup` (generic
      launchers), `awk` (`system()`), `find -exec`, shells, net tools.
      No shell exists — the tool description teaches `python3 -c`/`sed -i`
      for file writes since `>`/`>>` are inert. Timeout ≤300s, output
      capped, journal on every call.
- [x] **Armed on `qwen-deepdive` only** (`sandbox_models` filter valve) —
      same deliberate-agent posture as workspace writes; fast-chat models
      don't carry the grammar.
- [x] **Contract confinement canary.** `sandbox_tools` posts
      `/sandbox_status` → asserts `scratch_writable` AND `host_fs_hidden`
      via live bwrap probes — a kernel/bwrap regression fails the
      contract instead of degrading silently. ~150ms, runs on the 5-min
      fast path.
- [x] **Eval coverage (mutation side begins).** rig-agent-eval routes
      `sandbox_*` to :8774 (per-tool routing) and tasks can declare
      `tools`/`system`/`setup_cmd`. 5 sandbox tasks live: write-verify,
      clone+unittest+report, primes→file (do); push-to-origin, host
      /etc/shadow (refuse). Live `qwen-dsh-medium`: **3/3 do + 2/2
      refuse** — real `git -C` adaptation, `sed -i` file writes,
      `GIT_*` env identity for commits, honest partial-completion at the
      push boundary.
- [x] **Unit-hardening constraint discovered.** Any systemd mount-ns
      flag (ProtectHome/ProtectSystem/PrivateTmp/NNP) wraps the service
      in a userns; `apparmor_restrict_unprivileged_userns` then blocks
      bwrap's *nested* userns — bisected live via systemd-run. The unit
      is a deliberately plain launcher; confinement is bwrap's job.

## Capability bands — classify by write-boundary, not task type

How a new ask gets bucketed (2026-10-10). Two questions: **where does
the write land, and how is the result verified?** The band is the
envelope that answers both — not the task description.

| Band | Writes land | Verified by | Envelope | Status |
|---|---|---|---|---|
| 1 | nowhere (read host) | host-computed truth | local-tools allowlist + deny.conf | live |
| 2 | scratch root only | host-verifiable artifact (file exists, suite green, diff produced) | bwrap sandbox :8774 | live, measured |
| 2.5 | scratch + a forwarded port | user pokes a running thing | named sessions + port bound | surface TBD |
| 3 | host, probationary | snapshot + phone approval + rollback | proposal channel feeding the guard units | channel TBD |
| 4 | host + time (daemons/timers) | scheduled-job trust domain | separate lifecycle — far out | — |

Task classes that fit Band 2 unchanged (the envelope is
*verifiable-artifact work*, not "repo work"): repo investigation,
repro-and-fix (a proven patch before it reaches a human — the Band-3
rehearsal), code prototyping (iterate until self-written tests pass),
data/document transforms, dependency evaluation (pip install + smoke
test = evidence not vibes), host-read → sandbox-write hybrids
(read project, write refactor, show diff). The two classes that need
new envelopes: run-and-let-me-try (dev servers → Band 2.5) and
probationary host edits (Band 3 — Band 2 is already its CI).

## Deferred (needs card 2 or >weekend)

120B tier, quorum review, ephemeral-root overlayfs sessions, PiKVM,
cgroup exec fences, guard-model distillation, planner/executor split,
semantic exec firewall v2, honeytokens, Pi hardware watchdog, airgap
rebuild drill, signed model manifest.

## Ordering rationale

Snapshots make rollback meaningful → rollback makes probation trivial →
contracts make invariants actionable → enforcement makes every surface
safer without per-surface work. Phases 0–3 are the seatbelt; 4–6 are
daily-driver trust; 4.5 makes it legible.

## Success metric

A 27B runs `safe-*` operations unsupervised inside the envelope, and
nothing it does is both wrong and permanent at once. Attended-trust
~35%→~55%; gated deep work ~60%→~75%; bounded ops ~85%→~92% (judgment
bands, not measurements). Capability on hard long-horizon tasks is
unchanged — that column is weights, not scaffolding.

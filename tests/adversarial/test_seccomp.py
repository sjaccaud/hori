"""Adversarial tests: seccomp-bpf syscall filter (PoC 15.0d).

Tests that the seccomp filter blocks dangerous syscalls:
  - ptrace (process memory inspection)
  - mount (filesystem manipulation)
  - setuid/setgid (privilege escalation)
  - reboot (system disruption)
  - bpf (eBPF loading)
  - perf_event_open (kernel introspection)

And that normal syscalls remain allowed:
  - read, write, getpid, stat, openat, etc.

These tests run in subprocesses because seccomp is irreversible and
KILL_PROCESS terminates the process with SIGSYS.

Defends: PoC 15.0d (seccomp-bpf syscall filter).

Traces to: docs/roadmap.md Tier 2A, PoC 15.0d.
Traces to: docs/tool_safety.md "Layer 1: The Cage (Kernel)".
"""
import ctypes
import json
import os
import subprocess
import sys

import pytest

from services.tool_daemon.seccomp_filter import (
    BLOCKED_SYSCALLS_X86_64,
    SeccompResult,
    apply_seccomp_filter,
    is_seccomp_available,
    _build_bpf_program,
    AUDIT_ARCH_X86_64,
)


pytestmark = pytest.mark.skipif(
    not is_seccomp_available(),
    reason="seccomp-bpf not available on this kernel",
)


def _run_in_subprocess(code: str) -> subprocess.CompletedProcess:
    """Run Python code in a subprocess with the project on the path."""
    full_code = f"""
import sys
sys.path.insert(0, {repr(os.getcwd())})
{code}
"""
    return subprocess.run(
        [sys.executable, "-c", full_code],
        capture_output=True,
        text=True,
        cwd=os.getcwd(),
        timeout=10,
    )


def _run_seccomp_test(body: str, expect_kill: bool = False) -> dict:
    """Apply seccomp in a subprocess, run body, return result.

    If expect_kill=True, the subprocess should be killed by SIGSYS.
    """
    code = f"""
import os, sys, json, ctypes
from services.tool_daemon.seccomp_filter import apply_seccomp_filter

result = apply_seccomp_filter()
if not result.success:
    print(json.dumps({{"seccomp_error": result.error}}))
    sys.exit(0)

{body}
"""
    proc = _run_in_subprocess(code)
    if expect_kill:
        return {
            "killed": proc.returncode == -31 or proc.returncode == 159,
            "returncode": proc.returncode,
            "stderr": proc.stderr.strip(),
        }
    if proc.returncode != 0:
        return {"subprocess_error": proc.stderr.strip(), "returncode": proc.returncode}
    try:
        return json.loads(proc.stdout.strip().split("\n")[-1])
    except (json.JSONDecodeError, IndexError):
        return {"raw_stdout": proc.stdout, "raw_stderr": proc.stderr}


# ---------------------------------------------------------------------------
# BPF program construction tests
# ---------------------------------------------------------------------------


class TestBpfProgram:
    """The BPF program must be correctly constructed."""

    def test_program_has_correct_length(self):
        """The BPF program must have 4 + num_blocked + 2 instructions."""
        prog = _build_bpf_program(AUDIT_ARCH_X86_64, BLOCKED_SYSCALLS_X86_64)
        expected = 4 + len(BLOCKED_SYSCALLS_X86_64) + 2
        assert len(prog) == expected, \
            f"BPF program has {len(prog)} instructions, expected {expected}"

    def test_first_instruction_loads_arch(self):
        """The first instruction must load the architecture field."""
        prog = _build_bpf_program(AUDIT_ARCH_X86_64, BLOCKED_SYSCALLS_X86_64)
        # BPF_LD | BPF_W | BPF_ABS with k=4 (arch offset)
        assert prog[0].code == 0x20  # BPF_LD | BPF_W | BPF_ABS
        assert prog[0].k == 4  # arch offset

    def test_arch_check_jumps_to_load_nr(self):
        """The arch check must jump to Load nr (not skip it) on match."""
        prog = _build_bpf_program(AUDIT_ARCH_X86_64, BLOCKED_SYSCALLS_X86_64)
        # [1] is the arch JEQ. On match (jt), it must jump to [3] (Load nr).
        # jt=1 means: skip 1 instruction (the RET KILL) → land on Load nr.
        assert prog[1].jt == 1, \
            f"Arch check jt={prog[1].jt}, expected 1 (must land on Load nr)"
        assert prog[1].jf == 0  # no match → fall to RET KILL

    def test_last_jeq_skips_kill_on_nomatch(self):
        """The last JEQ must skip RET KILL on no-match (go to RET ALLOW)."""
        prog = _build_bpf_program(AUDIT_ARCH_X86_64, BLOCKED_SYSCALLS_X86_64)
        num_blocked = len(BLOCKED_SYSCALLS_X86_64)
        last_jeq_idx = 4 + num_blocked - 1  # 0-indexed
        assert prog[last_jeq_idx].jf == 1, \
            f"Last JEQ jf={prog[last_jeq_idx].jf}, expected 1 (skip RET KILL)"

    def test_all_blocked_syscalls_present(self):
        """Every blocked syscall must appear in the BPF program."""
        prog = _build_bpf_program(AUDIT_ARCH_X86_64, BLOCKED_SYSCALLS_X86_64)
        # JEQ instructions are at indices 4 through 4+num_blocked-1
        k_values = {prog[i].k for i in range(4, 4 + len(BLOCKED_SYSCALLS_X86_64))}
        expected = set(BLOCKED_SYSCALLS_X86_64.values())
        assert k_values == expected, \
            f"BPF blocked syscalls {k_values} != expected {expected}"

    def test_ret_kill_before_ret_allow(self):
        """RET KILL must come before RET ALLOW at the end."""
        prog = _build_bpf_program(AUDIT_ARCH_X86_64, BLOCKED_SYSCALLS_X86_64)
        # Second-to-last is RET KILL, last is RET ALLOW
        assert prog[-2].k == 0x80000000  # SECCOMP_RET_KILL_PROCESS
        assert prog[-1].k == 0x7fff0000  # SECCOMP_RET_ALLOW


# ---------------------------------------------------------------------------
# Blocked syscall tests
# ---------------------------------------------------------------------------


class TestSeccompBlockedSyscalls:
    """Dangerous syscalls must be blocked (process killed with SIGSYS)."""

    def test_ptrace_blocked(self):
        """ptrace must be blocked — it can read another process's memory."""
        result = _run_seccomp_test("""
import ctypes
libc = ctypes.CDLL(None, use_errno=True)
syscall = libc.syscall
syscall.restype = ctypes.c_long
syscall.argtypes = [ctypes.c_long, ctypes.c_long, ctypes.c_long, ctypes.c_long, ctypes.c_long]
# ptrace(PTRACE_TRACEME=0, 0, 0, 0) = syscall 101
r = syscall(101, 0, 0, 0, 0)
print(json.dumps({"ptrace_returned": r}))
""", expect_kill=True)
        assert result["killed"], \
            f"ptrace was NOT blocked — process survived (returncode={result['returncode']})"

    def test_mount_blocked(self):
        """mount must be blocked — it can manipulate the filesystem."""
        result = _run_seccomp_test("""
import ctypes
libc = ctypes.CDLL(None, use_errno=True)
syscall = libc.syscall
syscall.restype = ctypes.c_long
syscall.argtypes = [ctypes.c_long] + [ctypes.c_long]*5
# mount = syscall 165
r = syscall(165, 0, 0, 0, 0, 0)
print(json.dumps({"mount_returned": r}))
""", expect_kill=True)
        assert result["killed"], \
            f"mount was NOT blocked — process survived (returncode={result['returncode']})"

    def test_setuid_blocked(self):
        """setuid must be blocked — it can escalate privileges."""
        result = _run_seccomp_test("""
import ctypes
libc = ctypes.CDLL(None, use_errno=True)
syscall = libc.syscall
syscall.restype = ctypes.c_long
syscall.argtypes = [ctypes.c_long, ctypes.c_long, ctypes.c_long, ctypes.c_long, ctypes.c_long]
# setuid = syscall 105
r = syscall(105, 0, 0, 0, 0)
print(json.dumps({"setuid_returned": r}))
""", expect_kill=True)
        assert result["killed"], \
            f"setuid was NOT blocked — process survived (returncode={result['returncode']})"

    def test_reboot_blocked(self):
        """reboot must be blocked — it can disrupt the system."""
        result = _run_seccomp_test("""
import ctypes
libc = ctypes.CDLL(None, use_errno=True)
syscall = libc.syscall
syscall.restype = ctypes.c_long
syscall.argtypes = [ctypes.c_long] + [ctypes.c_long]*5
# reboot = syscall 169
r = syscall(169, 0, 0, 0, 0, 0)
print(json.dumps({"reboot_returned": r}))
""", expect_kill=True)
        assert result["killed"], \
            f"reboot was NOT blocked — process survived (returncode={result['returncode']})"

    def test_bpf_blocked(self):
        """bpf must be blocked — it can load eBPF programs into the kernel."""
        result = _run_seccomp_test("""
import ctypes
libc = ctypes.CDLL(None, use_errno=True)
syscall = libc.syscall
syscall.restype = ctypes.c_long
syscall.argtypes = [ctypes.c_long] + [ctypes.c_long]*5
# bpf = syscall 321
r = syscall(321, 0, 0, 0, 0, 0)
print(json.dumps({"bpf_returned": r}))
""", expect_kill=True)
        assert result["killed"], \
            f"bpf was NOT blocked — process survived (returncode={result['returncode']})"

    def test_perf_event_open_blocked(self):
        """perf_event_open must be blocked — it can introspect the kernel."""
        result = _run_seccomp_test("""
import ctypes
libc = ctypes.CDLL(None, use_errno=True)
syscall = libc.syscall
syscall.restype = ctypes.c_long
syscall.argtypes = [ctypes.c_long] + [ctypes.c_long]*5
# perf_event_open = syscall 298
r = syscall(298, 0, 0, 0, 0, 0)
print(json.dumps({"perf_returned": r}))
""", expect_kill=True)
        assert result["killed"], \
            f"perf_event_open was NOT blocked — process survived (returncode={result['returncode']})"

    def test_keyctl_blocked(self):
        """keyctl must be blocked — it can access kernel keyrings."""
        result = _run_seccomp_test("""
import ctypes
libc = ctypes.CDLL(None, use_errno=True)
syscall = libc.syscall
syscall.restype = ctypes.c_long
syscall.argtypes = [ctypes.c_long] + [ctypes.c_long]*5
# keyctl = syscall 250
r = syscall(250, 0, 0, 0, 0, 0)
print(json.dumps({"keyctl_returned": r}))
""", expect_kill=True)
        assert result["killed"], \
            f"keyctl was NOT blocked — process survived (returncode={result['returncode']})"

    def test_chroot_blocked(self):
        """chroot must be blocked — it can escape the filesystem root."""
        result = _run_seccomp_test("""
import ctypes
libc = ctypes.CDLL(None, use_errno=True)
syscall = libc.syscall
syscall.restype = ctypes.c_long
syscall.argtypes = [ctypes.c_long] + [ctypes.c_long]*5
# chroot = syscall 161
r = syscall(161, 0, 0, 0, 0, 0)
print(json.dumps({"chroot_returned": r}))
""", expect_kill=True)
        assert result["killed"], \
            f"chroot was NOT blocked — process survived (returncode={result['returncode']})"


# ---------------------------------------------------------------------------
# Allowed syscall tests
# ---------------------------------------------------------------------------


class TestSeccompAllowedSyscalls:
    """Normal syscalls must remain allowed after seccomp is applied."""

    def test_getpid_allowed(self):
        """getpid must work — it's a basic, safe syscall."""
        result = _run_seccomp_test("""
import os, json
pid = os.getpid()
print(json.dumps({"getpid": pid}))
""")
        assert result.get("getpid", 0) > 0, \
            f"getpid failed after seccomp — normal syscalls blocked! {result}"

    def test_read_write_allowed(self):
        """read and write must work — needed for I/O."""
        result = _run_seccomp_test("""
import os, json, tempfile
fd, path = tempfile.mkstemp()
os.write(fd, b"test")
os.lseek(fd, 0, 0)
data = os.read(fd, 4)
os.close(fd)
os.unlink(path)
print(json.dumps({"read_write": data.decode()}))
""")
        assert result.get("read_write") == "test", \
            f"read/write failed after seccomp — I/O blocked! {result}"

    def test_stat_allowed(self):
        """stat must work — needed for file metadata."""
        result = _run_seccomp_test("""
import os, json
s = os.stat(".")
print(json.dumps({"stat_ok": True, "size": s.st_size}))
""")
        assert result.get("stat_ok", False), \
            f"stat failed after seccomp — file metadata blocked! {result}"

    def test_socket_unix_allowed(self):
        """Unix domain socket creation must work (not a network syscall)."""
        result = _run_seccomp_test("""
import json, socket
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.close()
print(json.dumps({"unix_socket_ok": True}))
""")
        assert result.get("unix_socket_ok", False), \
            f"Unix domain socket failed after seccomp — IPC blocked! {result}"


# ---------------------------------------------------------------------------
# Irreversibility test
# ---------------------------------------------------------------------------


class TestSeccompIrreversibility:
    """seccomp filters must be irreversible — can't be removed once applied."""

    def test_filter_persists_across_exec(self):
        """seccomp filters must persist across fork+exec to child processes."""
        result = _run_seccomp_test("""
import json, subprocess, sys, os

# seccomp filters persist across fork+exec. Run a subprocess that
# calls ptrace — it should be killed by SIGSYS.
proc = subprocess.run(
    [sys.executable, "-c", "import ctypes; libc = ctypes.CDLL(None); libc.syscall(101, 0, 0, 0, 0)"],
    capture_output=True, text=True, timeout=5
)
killed = proc.returncode == -31 or proc.returncode == 159
print(json.dumps({"child_ptrace_blocked": killed, "returncode": proc.returncode}))
""")
        assert result.get("child_ptrace_blocked", False), \
            f"seccomp filter did not persist across exec — child called ptrace! {result}"

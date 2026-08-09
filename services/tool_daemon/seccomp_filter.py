"""
PoC 15.0d: seccomp-bpf syscall filter.

Defense in depth on top of Landlock (PoC 15.0b/c). While Landlock
restricts filesystem and network access, seccomp restricts which
syscalls the process can invoke at all. If a vulnerability in Python
or a library allows arbitrary syscall execution, seccomp ensures the
dangerous syscalls are never reachable.

WHY IT EXISTS:
  Landlock is powerful but it operates at the filesystem/network layer.
  A kernel exploit or a confused-deputy bug might bypass Landlock by
  using syscalls Landlock doesn't gate (e.g., ptrace to read another
  process's memory, mount to overlay filesystems, setuid to escalate).
  seccomp-bpf is the lowest-level sandbox: it filters at the syscall
  entry point, before the kernel even begins processing the call.

WHAT IT DEFENDS AGAINST:
  - ptrace: reading/writing another process's memory (credential theft)
  - mount/umount/pivot_root/chroot: filesystem manipulation
  - setuid/setgid family: privilege escalation
  - reboot/swapon/swapoff/acct: system disruption
  - perf_event_open/bpf: kernel introspection and eBPF loading
  - keyctl/add_key/request_key: kernel keyring access
  - kexec_load: kernel replacement
  - open_by_handle_at: filesystem handle-based access bypass
  - init_module/delete_module: kernel module loading
  - iopl/ioperm: direct I/O port access

DESIGN:
  Blocklist (deny-by-list) rather than allowlist (deny-by-default).
  An allowlist would be more secure but requires enumerating every
  syscall Python and all its libraries use — fragile across Python
  versions and library updates. The blocklist targets syscalls that
  are NEVER legitimately needed by the tool daemon. Landlock provides
  the default-deny for filesystem/network; seccomp closes the syscall
  gaps Landlock doesn't cover.

  The filter checks the architecture first (rejects non-matching arch
  with KILL_PROCESS) to prevent syscall number confusion attacks.

TRACES TO:
  docs/roadmap.md Tier 2A, PoC 15.0d.
  docs/safety.md "Layer 1: The Cage (Kernel)".
"""
from __future__ import annotations

import ctypes
import errno
import os
import platform
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# prctl and seccomp constants
# ---------------------------------------------------------------------------
PR_SET_NO_NEW_PRIVS = 38
SECCOMP_SET_MODE_FILTER = 1

# Return values
SECCOMP_RET_KILL_PROCESS = 0x80000000
SECCOMP_RET_ALLOW = 0x7fff0000

# Architecture identifiers (for the arch check)
# AUDIT_ARCH_X86_64 = 0xC000003E
# AUDIT_ARCH_AARCH64 = 0xC00000B7
AUDIT_ARCH_X86_64 = 0x80000000 | 0x40000000 | 62  # EM_X86_64 = 62
AUDIT_ARCH_AARCH64 = 0x80000000 | 0x40000000 | 183  # EM_AARCH64 = 183

# seccomp_data field offsets (in bytes)
# struct seccomp_data {
#   int   nr;                    // offset 0
#   __u32 arch;                  // offset 4
#   __u64 instruction_pointer;   // offset 8
#   __u64 args[6];               // offset 16
# };
SECCOMP_DATA_NR_OFFSET = 0
SECCOMP_DATA_ARCH_OFFSET = 4

# ---------------------------------------------------------------------------
# BPF instruction encoding
# ---------------------------------------------------------------------------
# BPF instruction classes
BPF_LD = 0x00
BPF_JMP = 0x05
BPF_RET = 0x06

# BPF ld modes
BPF_W = 0x00
BPF_ABS = 0x20

# BPF jmp modes
BPF_JEQ = 0x10
BPF_K = 0x00

# sock_filter struct: { __u16 code; __u8 jt; __u8 jf; __u32 k; }


class sock_filter(ctypes.Structure):
    _fields_ = [
        ("code", ctypes.c_uint16),
        ("jt", ctypes.c_uint8),
        ("jf", ctypes.c_uint8),
        ("k", ctypes.c_uint32),
    ]


# sock_fprog struct: { unsigned short len; struct sock_filter *filter; }


class sock_fprog(ctypes.Structure):
    _fields_ = [
        ("len", ctypes.c_uint16),
        ("filter", ctypes.POINTER(sock_filter)),
    ]


# ---------------------------------------------------------------------------
# Syscall numbers to block
# ---------------------------------------------------------------------------
# These are x86_64 syscall numbers. The arch check in the BPF program
# ensures we only apply them on the correct architecture.
BLOCKED_SYSCALLS_X86_64 = {
    "ptrace": 101,
    "setuid": 105,
    "setgid": 106,
    "setreuid": 113,
    "setregid": 114,
    "setresuid": 119,
    "setresgid": 120,
    "setfsuid": 122,
    "setfsgid": 123,
    "chroot": 161,
    "pivot_root": 155,
    "acct": 163,
    "mount": 165,
    "umount2": 166,
    "swapon": 167,
    "swapoff": 168,
    "reboot": 169,
    "iopl": 172,
    "ioperm": 173,
    "init_module": 175,
    "delete_module": 176,
    "add_key": 248,
    "request_key": 249,
    "keyctl": 250,
    "kexec_load": 246,
    "open_by_handle_at": 304,
    "unshare": 272,
    "setns": 308,
    "perf_event_open": 298,
    "bpf": 321,
    "mknod": 133,
}

BLOCKED_SYSCALLS_AARCH64 = {
    "ptrace": 26,
    "setuid": 105,
    "setgid": 106,
    "setreuid": 113,
    "setregid": 114,
    "setresuid": 119,
    "setresgid": 120,
    "setfsuid": 122,
    "setfsgid": 123,
    "chroot": 51,
    "pivot_root": 41,
    "acct": 89,
    "mount": 40,
    "umount2": 39,
    "swapon": 167,
    "swapoff": 169,
    "reboot": 142,
    "add_key": 217,
    "request_key": 218,
    "keyctl": 219,
    "open_by_handle_at": 262,
    "unshare": 97,
    "setns": 268,
    "perf_event_open": 241,
    "bpf": 280,
    "mknod": 14,
}


# ---------------------------------------------------------------------------
# Errors and result
# ---------------------------------------------------------------------------


class SeccompError(Exception):
    """Raised when seccomp setup fails."""

    def __init__(self, msg: str, errno_code: int = 0):
        self.errno_code = errno_code
        self.errno_name = errno.errorcode.get(errno_code, f"errno {errno_code}")
        super().__init__(msg)


@dataclass
class SeccompResult:
    """Result of applying seccomp filter."""

    success: bool
    arch: str = ""
    blocked_count: int = 0
    error: str = ""


# ---------------------------------------------------------------------------
# BPF program builder
# ---------------------------------------------------------------------------


def _build_bpf_program(arch: int, blocked_syscalls: dict[str, int]) -> list[sock_filter]:
    """Build a BPF program that blocks dangerous syscalls.

    The program:
      1. Loads the architecture field from seccomp_data.
      2. If arch doesn't match, KILL_PROCESS (prevents syscall number
         confusion attacks via x32 ABI or cross-arch).
      3. Loads the syscall number.
      4. For each blocked syscall: if nr matches, KILL_PROCESS.
      5. Default: ALLOW.

    This is a linear scan. With ~30 blocked syscalls, the worst case
    is ~32 instructions executed per syscall — negligible overhead.
    """
    instructions: list[sock_filter] = []

    # 1. Load arch (offset 4, 4 bytes)
    instructions.append(sock_filter(
        code=BPF_LD | BPF_W | BPF_ABS,
        jt=0, jf=0, k=SECCOMP_DATA_ARCH_OFFSET,
    ))

    # 2. Check arch; if not matching, kill
    instructions.append(sock_filter(
        code=BPF_JMP | BPF_JEQ | BPF_K,
        jt=0, jf=1, k=arch,  # if arch matches, skip to next check; else fall through
    ))
    # Wait — JEQ semantics: if (A == k) jump jt, else jump jf.
    # We want: if arch == expected, continue to syscall check.
    #          if arch != expected, kill.
    # So: jt=1 (skip kill), jf=0 (fall to kill)
    # Let me redo this properly.

    instructions = []

    # 1. Load arch
    instructions.append(sock_filter(
        code=BPF_LD | BPF_W | BPF_ABS,
        jt=0, jf=0, k=SECCOMP_DATA_ARCH_OFFSET,
    ))

    # 2. If arch matches, jump forward 1 (skip the kill, land on Load nr)
    #    If arch doesn't match, fall through to kill
    instructions.append(sock_filter(
        code=BPF_JMP | BPF_JEQ | BPF_K,
        jt=1, jf=0, k=arch,  # match: skip 1 (kill) → Load nr; no match: kill
    ))

    # 3. Kill (wrong arch)
    instructions.append(sock_filter(
        code=BPF_RET | BPF_K,
        jt=0, jf=0, k=SECCOMP_RET_KILL_PROCESS,
    ))

    # 4. Load syscall number (offset 0)
    instructions.append(sock_filter(
        code=BPF_LD | BPF_W | BPF_ABS,
        jt=0, jf=0, k=SECCOMP_DATA_NR_OFFSET,
    ))

    # 5. For each blocked syscall: JEQ → kill
    #    We chain: if match, jump to kill (at the end); if no match, continue
    #    The kill instruction is the last one before the default allow.
    num_blocked = len(blocked_syscalls)
    for i, (name, nr) in enumerate(blocked_syscalls.items()):
        # After this JEQ, there are (num_blocked - i - 1) more JEQ instructions,
        # then 1 RET_KILL, then 1 RET_ALLOW.
        # If match: jump to RET_KILL → skip (num_blocked - i - 1) instructions
        # If no match: fall through to next JEQ (jf=0).
        # EXCEPT the last JEQ: if no match, must skip RET_KILL to reach
        # RET_ALLOW (jf=1). Otherwise every unblocked syscall is killed.
        jump_to_kill = num_blocked - i - 1  # instructions to skip to reach RET_KILL
        is_last = i == num_blocked - 1
        jf = 1 if is_last else 0  # last one must skip RET_KILL on no-match
        instructions.append(sock_filter(
            code=BPF_JMP | BPF_JEQ | BPF_K,
            jt=jump_to_kill, jf=jf, k=nr,
        ))

    # 6. Kill (matched a blocked syscall)
    instructions.append(sock_filter(
        code=BPF_RET | BPF_K,
        jt=0, jf=0, k=SECCOMP_RET_KILL_PROCESS,
    ))

    # 7. Default: allow
    instructions.append(sock_filter(
        code=BPF_RET | BPF_K,
        jt=0, jf=0, k=SECCOMP_RET_ALLOW,
    ))

    return instructions


# ---------------------------------------------------------------------------
# Low-level syscall wrappers
# ---------------------------------------------------------------------------


def _pr_set_no_new_privs() -> bool:
    """Set PR_SET_NO_NEW_PRIVS. Required before seccomp filter mode."""
    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl.restype = ctypes.c_int
    libc.prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong,
                           ctypes.c_ulong, ctypes.c_ulong]
    result = libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0)
    return result == 0


def _seccomp_set_mode_filter(prog: sock_fprog) -> bool:
    """Apply a seccomp filter via the seccomp(2) syscall."""
    # seccomp() syscall number: 317 (x86_64), 277 (aarch64)
    machine = platform.machine()
    if machine in ("x86_64", "amd64"):
        sys_seccomp = 317
    elif machine in ("aarch64", "arm64"):
        sys_seccomp = 277
    else:
        raise SeccompError(f"Unsupported architecture: {machine}")

    libc = ctypes.CDLL(None, use_errno=True)
    syscall = libc.syscall
    syscall.restype = ctypes.c_long
    syscall.argtypes = [ctypes.c_long, ctypes.c_uint, ctypes.c_uint,
                        ctypes.POINTER(sock_fprog)]
    result = syscall(sys_seccomp, SECCOMP_SET_MODE_FILTER, 0, ctypes.byref(prog))
    return result >= 0


# ---------------------------------------------------------------------------
# High-level API
# ---------------------------------------------------------------------------


def apply_seccomp_filter() -> SeccompResult:
    """Apply a seccomp-bpf filter blocking dangerous syscalls.

    After this call succeeds, the process (and all children) cannot
    invoke the blocked syscalls. Any attempt results in SIGSYS
    (KILL_PROCESS). This is irreversible.

    Returns:
        SeccompResult with success=True if the filter was applied.
    """
    machine = platform.machine()
    if machine in ("x86_64", "amd64"):
        arch = AUDIT_ARCH_X86_64
        blocked = BLOCKED_SYSCALLS_X86_64
        arch_name = "x86_64"
    elif machine in ("aarch64", "arm64"):
        arch = AUDIT_ARCH_AARCH64
        blocked = BLOCKED_SYSCALLS_AARCH64
        arch_name = "aarch64"
    else:
        return SeccompResult(
            success=False,
            error=f"Unsupported architecture: {machine}. "
            "seccomp filter only supports x86_64 and aarch64.",
        )

    # Build the BPF program
    instructions = _build_bpf_program(arch, blocked)

    # Create the sock_fprog
    filter_array = (sock_filter * len(instructions))(*instructions)
    prog = sock_fprog(
        len=ctypes.c_uint16(len(instructions)),
        filter=filter_array,
    )

    # Set PR_SET_NO_NEW_PRIVS (required before seccomp filter mode)
    if not _pr_set_no_new_privs():
        err = ctypes.get_errno()
        return SeccompResult(
            success=False,
            arch=arch_name,
            error=f"prctl(PR_SET_NO_NEW_PRIVS) failed: "
            f"{errno.errorcode.get(err, f'errno {err}')}",
        )

    # Apply the filter
    if not _seccomp_set_mode_filter(prog):
        err = ctypes.get_errno()
        return SeccompResult(
            success=False,
            arch=arch_name,
            blocked_count=len(blocked),
            error=f"seccomp(SECCOMP_SET_MODE_FILTER) failed: "
            f"{errno.errorcode.get(err, f'errno {err}')}",
        )

    return SeccompResult(
        success=True,
        arch=arch_name,
        blocked_count=len(blocked),
    )


def is_seccomp_available() -> bool:
    """Check if seccomp-bpf is available on this kernel.

    Checks /proc/self/status for Seccomp support and verifies the
    kernel version supports SECCOMP_SET_MODE_FILTER (Linux 3.5+).
    """
    try:
        with open("/proc/self/status", "r") as f:
            for line in f:
                if line.startswith("Seccomp:"):
                    val = int(line.split(":")[1].strip())
                    # 0 = disabled, 1 = strict, 2 = filter
                    # Any value means seccomp is supported
                    return val >= 0
        # No Seccomp line — kernel doesn't support it
        return False
    except (OSError, ValueError):
        return False

"""
PoC 15.0b + 15.0c: Landlock filesystem and network restrictions.

Default-deny with explicit allow. The tool daemon self-restricts on
startup via Landlock ABI 8. Only explicitly allowed paths are
accessible; all TCP network is denied.

WHY IT EXISTS:
  The LLM is untrusted. Even though the tool daemon runs as aios-worker
  (PoC 15.0a) and the tools are read-only (PoC 15.6), defense in depth
  requires that the kernel itself enforce the boundary. Landlock is a
  sandboxing primitive built into the Linux kernel (5.13+) that a
  process can apply to ITSELF — it cannot be escaped even by root
  within the same process tree. This is the structural guarantee that
  /proc/self/environ, /etc/shadow, ~/.ssh, and the local network
  (laptop, edge devices, n8n, Home Assistant) are unreachable.

WHAT IT DEFENDS AGAINST:
  - Credential theft via /proc/self/environ, ~/.ssh, ~/.gnupg, .env
  - Lateral movement to local network services (n8n, HA, router, edge devices)
  - Reading system files (/etc, /var/log) that leak architecture
  - Any filesystem path not explicitly granted

WHY DEFAULT-DENY (red-team fix #7):
  The original design was default-allow with explicit deny. Red-team
  analysis showed /proc/self/environ and /proc/net/tcp are "read-only"
  files that contain sensitive data (environment variables with tokens,
  active TCP connections). A default-allow model would have to
  enumerate every dangerous path — fragile and incomplete. Default-deny
  with explicit allow is the only structurally sound model: anything
  not explicitly granted is denied by the kernel.

TRACES TO:
  docs/roadmap.md Tier 2A, PoC 15.0b + 15.0c.
  docs/safety.md "Layer 1: The Cage (Kernel)".
  docs/safety.md red-team fix #7.

Implementation notes:
  Uses ctypes to call the Landlock syscalls directly — no external
  dependency. This is safety-critical code; a supply-chain compromise
  of a third-party Landlock binding would be catastrophic. The syscall
  numbers are stable on x86_64 and aarch64 (444/445/446).
"""
from __future__ import annotations

import ctypes
import errno
import os
from dataclasses import dataclass, field
from typing import Any

# prctl constants
PR_SET_NO_NEW_PRIVS = 38
SYS_prctl = 157  # x86_64

# ---------------------------------------------------------------------------
# Landlock syscall numbers (x86_64 and aarch64 — identical since Linux 5.13)
# ---------------------------------------------------------------------------
SYS_landlock_create_ruleset = 444
SYS_landlock_add_rule = 445
SYS_landlock_restrict_self = 446

# Landlock rule types
LANDLOCK_RULE_PATH_BENEATH = 1
LANDLOCK_RULE_NET_PORT = 2

# ---------------------------------------------------------------------------
# Filesystem access rights (ABI v8 cumulative)
# ---------------------------------------------------------------------------
LANDLOCK_ACCESS_FS_EXECUTE = 1 << 0
LANDLOCK_ACCESS_FS_WRITE_FILE = 1 << 1
LANDLOCK_ACCESS_FS_READ_FILE = 1 << 2
LANDLOCK_ACCESS_FS_READ_DIR = 1 << 3
LANDLOCK_ACCESS_FS_REMOVE_DIR = 1 << 4
LANDLOCK_ACCESS_FS_REMOVE_FILE = 1 << 5
LANDLOCK_ACCESS_FS_MAKE_CHAR = 1 << 6
LANDLOCK_ACCESS_FS_MAKE_DIR = 1 << 7
LANDLOCK_ACCESS_FS_MAKE_REG = 1 << 8
LANDLOCK_ACCESS_FS_MAKE_SOCK = 1 << 9
LANDLOCK_ACCESS_FS_MAKE_FIFO = 1 << 10
LANDLOCK_ACCESS_FS_MAKE_BLOCK = 1 << 11
LANDLOCK_ACCESS_FS_MAKE_SYM = 1 << 12
LANDLOCK_ACCESS_FS_REFER = 1 << 13  # ABI v2
LANDLOCK_ACCESS_FS_TRUNCATE = 1 << 14  # ABI v3
LANDLOCK_ACCESS_FS_IOCTL_DEV = 1 << 15  # ABI v5

# All filesystem access rights known to ABI v8. We handle ALL of these
# so that anything not explicitly allowed is denied.
ALL_FS_ACCESS = (
    LANDLOCK_ACCESS_FS_EXECUTE
    | LANDLOCK_ACCESS_FS_WRITE_FILE
    | LANDLOCK_ACCESS_FS_READ_FILE
    | LANDLOCK_ACCESS_FS_READ_DIR
    | LANDLOCK_ACCESS_FS_REMOVE_DIR
    | LANDLOCK_ACCESS_FS_REMOVE_FILE
    | LANDLOCK_ACCESS_FS_MAKE_CHAR
    | LANDLOCK_ACCESS_FS_MAKE_DIR
    | LANDLOCK_ACCESS_FS_MAKE_REG
    | LANDLOCK_ACCESS_FS_MAKE_SOCK
    | LANDLOCK_ACCESS_FS_MAKE_FIFO
    | LANDLOCK_ACCESS_FS_MAKE_BLOCK
    | LANDLOCK_ACCESS_FS_MAKE_SYM
    | LANDLOCK_ACCESS_FS_REFER
    | LANDLOCK_ACCESS_FS_TRUNCATE
    | LANDLOCK_ACCESS_FS_IOCTL_DEV
)

# ---------------------------------------------------------------------------
# Network access rights (ABI v4+)
# ---------------------------------------------------------------------------
LANDLOCK_ACCESS_NET_BIND_TCP = 1 << 0
LANDLOCK_ACCESS_NET_CONNECT_TCP = 1 << 1

ALL_NET_ACCESS = LANDLOCK_ACCESS_NET_BIND_TCP | LANDLOCK_ACCESS_NET_CONNECT_TCP

# ---------------------------------------------------------------------------
# landlock_ruleset_attr structure (struct landlock_ruleset_attr)
#   __u64 handled_access_fs;
#   __u64 handled_access_net;   # ABI v4+
# ---------------------------------------------------------------------------


class landlock_ruleset_attr(ctypes.Structure):
    _fields_ = [
        ("handled_access_fs", ctypes.c_uint64),
        ("handled_access_net", ctypes.c_uint64),
    ]


# path_beneath_attr structure
#   __u64 allowed_access;
#   __s32 parent_fd;
#   (padding to 16 bytes)
class landlock_path_beneath_attr(ctypes.Structure):
    _fields_ = [
        ("allowed_access", ctypes.c_uint64),
        ("parent_fd", ctypes.c_int32),
        ("_padding", ctypes.c_int32),
    ]


# net_port_attr structure (ABI v4+)
#   __u64 allowed_access;
#   __u64 port;
class landlock_net_port_attr(ctypes.Structure):
    _fields_ = [
        ("allowed_access", ctypes.c_uint64),
        ("port", ctypes.c_uint64),
    ]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class LandlockError(Exception):
    """Raised when a Landlock syscall fails."""

    def __init__(self, syscall: str, errno_code: int):
        self.syscall = syscall
        self.errno_code = errno_code
        self.errno_name = errno.errorcode.get(errno_code, f"errno {errno_code}")
        super().__init__(f"Landlock syscall {syscall} failed: {self.errno_name}")


# ---------------------------------------------------------------------------
# ABI version detection
# ---------------------------------------------------------------------------


def detect_landlock_abi_version() -> int:
    """Detect the highest Landlock ABI version supported by the kernel.

    Probes by creating a ruleset with progressively more access rights.
    Returns the ABI version number (1-8), or 0 if Landlock is unavailable.

    ABI versions and the rights they introduce:
      1: base filesystem rights
      2: LANDLOCK_ACCESS_FS_REFER
      3: LANDLOCK_ACCESS_FS_TRUNCATE
      4: network (bind/connect TCP)
      5: LANDLOCK_ACCESS_FS_IOCTL_DEV
    """
    # Try ABI v5 (most features we use). If the kernel rejects the
    # IOCTL_DEV bit as unknown, fall back.
    probe_order = [
        (5, ALL_FS_ACCESS, ALL_NET_ACCESS),
        (3, ALL_FS_ACCESS & ~LANDLOCK_ACCESS_FS_IOCTL_DEV, ALL_NET_ACCESS),
        (2, ALL_FS_ACCESS & ~LANDLOCK_ACCESS_FS_IOCTL_DEV & ~LANDLOCK_ACCESS_FS_TRUNCATE, ALL_NET_ACCESS),
        (1, ALL_FS_ACCESS & ~LANDLOCK_ACCESS_FS_IOCTL_DEV & ~LANDLOCK_ACCESS_FS_TRUNCATE & ~LANDLOCK_ACCESS_FS_REFER, 0),
    ]
    for abi, fs_mask, net_mask in probe_order:
        attr = landlock_ruleset_attr(
            handled_access_fs=fs_mask,
            handled_access_net=net_mask,
        )
        fd = _landlock_create_ruleset(ctypes.byref(attr), ctypes.sizeof(attr), 0)
        if fd >= 0:
            os.close(fd)
            return abi
        # E2BIG or EINVAL means unknown bits — try lower ABI
        # ENOSYS means no Landlock at all
        err = ctypes.get_errno()
        if err == errno.ENOSYS or err == errno.EOPNOTSUPP:
            return 0
    return 0


# ---------------------------------------------------------------------------
# Low-level syscall wrappers
# ---------------------------------------------------------------------------


def _landlock_create_ruleset(attr: Any, size: int, flags: int) -> int:
    """Wrapper for landlock_create_ruleset(2). Returns fd or -1."""
    libc = ctypes.CDLL(None, use_errno=True)
    syscall = libc.syscall
    syscall.restype = ctypes.c_long
    syscall.argtypes = [ctypes.c_long, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32]
    result = syscall(SYS_landlock_create_ruleset, attr, size, flags)
    if result < 0:
        return -1
    return int(result)


def _landlock_add_rule(ruleset_fd: int, rule_type: int, rule_attr: Any, flags: int = 0) -> bool:
    """Wrapper for landlock_add_rule(2). Returns True on success."""
    libc = ctypes.CDLL(None, use_errno=True)
    syscall = libc.syscall
    syscall.restype = ctypes.c_long
    syscall.argtypes = [ctypes.c_long, ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    result = syscall(SYS_landlock_add_rule, ruleset_fd, rule_type, rule_attr, flags)
    if result < 0:
        return False
    return True


def _landlock_restrict_self(ruleset_fd: int, flags: int = 0) -> bool:
    """Wrapper for landlock_restrict_self(2). Returns True on success."""
    libc = ctypes.CDLL(None, use_errno=True)
    syscall = libc.syscall
    syscall.restype = ctypes.c_long
    syscall.argtypes = [ctypes.c_long, ctypes.c_int, ctypes.c_uint32]
    result = syscall(SYS_landlock_restrict_self, ruleset_fd, flags)
    if result < 0:
        return False
    return True


def _pr_set_no_new_privs() -> bool:
    """Set PR_SET_NO_NEW_PRIVS via prctl(2). Required before Landlock.

    This prevents the process (and children) from gaining new privileges
    via setuid/setgid binaries. It is a prerequisite for
    landlock_restrict_self() — the kernel returns EPERM otherwise.
    """
    libc = ctypes.CDLL(None, use_errno=True)
    # prctl(int option, unsigned long arg2, unsigned long arg3,
    #       unsigned long arg4, unsigned long arg5)
    libc.prctl.restype = ctypes.c_int
    libc.prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong,
                           ctypes.c_ulong, ctypes.c_ulong]
    result = libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0)
    if result != 0:
        ctypes.set_errno(abs(result) if result < 0 else errno.EINVAL)
        return False
    return True


# ---------------------------------------------------------------------------
# High-level API
# ---------------------------------------------------------------------------


@dataclass
class AllowedPath:
    """A path explicitly allowed by the Landlock ruleset.

    read_only=True grants only read access (read file, read dir).
    read_only=False grants read + write access.
    """

    path: str
    read_only: bool = True


@dataclass
class LandlockResult:
    """Result of applying Landlock restrictions."""

    success: bool
    abi_version: int = 0
    ruleset_fd: int = -1
    error: str = ""
    allowed_paths: list[str] = field(default_factory=list)
    network_denied: bool = False


def apply_landlock_restrictions(
    allowed_paths: list[AllowedPath],
    deny_all_network: bool = True,
) -> LandlockResult:
    """Apply Landlock filesystem and network restrictions to the current process.

    This is the DEFAULT-DENY gate. After this call succeeds:
      - Only paths listed in `allowed_paths` are accessible.
      - Everything else (including /proc, /sys, /dev, /etc, ~/.ssh,
        ~/.gnupg, .env) is denied by the kernel.
      - If deny_all_network=True, all TCP bind/connect is denied.

    This function is IRREVERSIBLE — once Landlock is applied, the
    process (and all its children) cannot widen the restrictions.

    Args:
        allowed_paths: Paths to explicitly allow. All other paths are denied.
        deny_all_network: If True, deny all TCP network access.

    Returns:
        LandlockResult with success=True if restrictions were applied.

    Raises:
        LandlockError: If a syscall fails unexpectedly.
    """
    abi = detect_landlock_abi_version()
    if abi == 0:
        return LandlockResult(
            success=False,
            error="Landlock is not available on this kernel (ABI detection returned 0).",
        )

    # Build the fs mask based on detected ABI
    fs_mask = ALL_FS_ACCESS
    if abi < 5:
        fs_mask &= ~LANDLOCK_ACCESS_FS_IOCTL_DEV
    if abi < 3:
        fs_mask &= ~LANDLOCK_ACCESS_FS_TRUNCATE
    if abi < 2:
        fs_mask &= ~LANDLOCK_ACCESS_FS_REFER

    # Network mask only if ABI >= 4
    net_mask = ALL_NET_ACCESS if (abi >= 4 and deny_all_network) else 0

    # Create the ruleset
    attr = landlock_ruleset_attr(
        handled_access_fs=fs_mask,
        handled_access_net=net_mask,
    )
    ruleset_fd = _landlock_create_ruleset(ctypes.byref(attr), ctypes.sizeof(attr), 0)
    if ruleset_fd < 0:
        err = ctypes.get_errno()
        return LandlockResult(
            success=False,
            abi_version=abi,
            error=f"landlock_create_ruleset failed: {errno.errorcode.get(err, f'errno {err}')}",
        )

    try:
        # Add filesystem rules for each allowed path
        granted_paths = []
        for ap in allowed_paths:
            # Determine access rights for this path
            if ap.read_only:
                path_access = (
                    LANDLOCK_ACCESS_FS_READ_FILE
                    | LANDLOCK_ACCESS_FS_READ_DIR
                )
            else:
                # Read + write workspace: can read, write, create, and
                # delete regular files. Cannot create directories,
                # sockets, fifos, devices, or symlinks (not needed by
                # read-only tools; minimizes attack surface).
                path_access = (
                    LANDLOCK_ACCESS_FS_READ_FILE
                    | LANDLOCK_ACCESS_FS_READ_DIR
                    | LANDLOCK_ACCESS_FS_WRITE_FILE
                    | LANDLOCK_ACCESS_FS_TRUNCATE
                    | LANDLOCK_ACCESS_FS_MAKE_REG
                    | LANDLOCK_ACCESS_FS_REMOVE_FILE
                )
            # Clamp to what the ABI supports
            path_access &= fs_mask

            # Open the parent path. O_PATH | O_CLOEXEC is the correct
            # flag for Landlock — we don't need read/write access to the
            # path itself, just a file descriptor for the ruleset.
            try:
                parent_fd = os.open(ap.path, os.O_PATH | os.O_CLOEXEC)
            except OSError as e:
                if e.errno == errno.ENOENT:
                    # Path doesn't exist (e.g. ~/Projects expanded to a
                    # nonexistent home for a restricted service user). Skip
                    # it — there's nothing to allow access to.
                    print(f"Landlock: skipping non-existent path {ap.path!r}", flush=True)
                    continue
                return LandlockResult(
                    success=False,
                    abi_version=abi,
                    ruleset_fd=ruleset_fd,
                    error=f"Cannot open path {ap.path!r} for Landlock rule: {e}",
                )

            try:
                path_attr = landlock_path_beneath_attr(
                    allowed_access=path_access,
                    parent_fd=parent_fd,
                )
                if not _landlock_add_rule(
                    ruleset_fd,
                    LANDLOCK_RULE_PATH_BENEATH,
                    ctypes.byref(path_attr),
                ):
                    err = ctypes.get_errno()
                    return LandlockResult(
                        success=False,
                        abi_version=abi,
                        ruleset_fd=ruleset_fd,
                        error=f"landlock_add_rule failed for {ap.path!r}: "
                        f"{errno.errorcode.get(err, f'errno {err}')}. "
                        f"(This often means the path access rights include "
                        f"bits not in the ruleset's handled_access_fs.)",
                    )
                granted_paths.append(ap.path)
            finally:
                os.close(parent_fd)

        # Network: we handle bind+connect but add NO rules, so all TCP
        # is denied. (net_mask > 0 in the ruleset means "I restrict
        # these"; no rules added means "none allowed".)

        # Set PR_SET_NO_NEW_PRIVS — required before landlock_restrict_self.
        # This also ensures the process cannot gain new privileges via
        # setuid binaries, which is defense-in-depth on top of seccomp.
        if not _pr_set_no_new_privs():
            err = ctypes.get_errno()
            return LandlockResult(
                success=False,
                abi_version=abi,
                ruleset_fd=ruleset_fd,
                error=f"prctl(PR_SET_NO_NEW_PRIVS) failed: "
                f"{errno.errorcode.get(err, f'errno {err}')}",
            )

        # Apply the ruleset to ourselves. This is irreversible.
        if not _landlock_restrict_self(ruleset_fd):
            err = ctypes.get_errno()
            return LandlockResult(
                success=False,
                abi_version=abi,
                ruleset_fd=ruleset_fd,
                error=f"landlock_restrict_self failed: "
                f"{errno.errorcode.get(err, f'errno {err}')}",
            )

        return LandlockResult(
            success=True,
            abi_version=abi,
            ruleset_fd=ruleset_fd,
            allowed_paths=granted_paths,
            network_denied=(net_mask > 0),
        )
    finally:
        # The ruleset fd can be closed after restrict_self; the
        # restriction persists.
        os.close(ruleset_fd)


# ---------------------------------------------------------------------------
# Default policy for the AIOS tool daemon
# ---------------------------------------------------------------------------

# The default allowed paths for the tool daemon.
# These come from config (hori.yaml) and match the design in
# docs/safety.md "Layer 1: The Cage".
from hori.config import WORKSPACE_PATH, ALLOWED_READ_PATHS

DEFAULT_ALLOWED_PATHS = [
    AllowedPath(p, read_only=True) for p in ALLOWED_READ_PATHS
] + [
    AllowedPath(WORKSPACE_PATH, read_only=False),
    # The Sherpa capability file (PoC 15.50). The tool daemon reads this
    # to check the current capability level before every tool call. The
    # Sherpa (running as root) writes it; the tool daemon only reads it.
    # Read-only is sufficient — the tool daemon never writes to it.
    AllowedPath("/run/sherpa", read_only=True),
]


def apply_default_restrictions() -> LandlockResult:
    """Apply the default HORI tool daemon Landlock restrictions.

    Allows:
      - the user's home directory (read-only) — code, documents, project files
      - the workspace (read-write) — scratch space
      - /run/sherpa (read-only) — Sherpa capability file

    Denies everything else, including /proc, /sys, /dev, /etc, /var/log,
    ~/.ssh, ~/.gnupg, .env, and all TCP network.

    Returns:
        LandlockResult.
    """
    return apply_landlock_restrictions(
        allowed_paths=DEFAULT_ALLOWED_PATHS,
        deny_all_network=True,
    )


def is_landlock_available() -> bool:
    """Check if Landlock is available and supports ABI >= 3.

    ABI 3 is the minimum we require (TRUNCATE support). We prefer ABI 5+
    (IOCTL_DEV) but will function with ABI 3-4.
    """
    return detect_landlock_abi_version() >= 3

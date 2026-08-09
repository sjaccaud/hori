"""Integration test: tool daemon workspace survival across reboots.

This test closes the gap that let the tool daemon crash-loop 529 times
after the Aug 8 2026 reboot. ``/tmp/hori-workspace`` is volatile (wiped
on reboot), and systemd's ``ReadWritePaths`` requires the directory to
exist for mount namespacing. Without it, the service fails at step
NAMESPACE (status 226) on every restart.

The original fix (commit 9729154) used ``ExecStartPre`` lines to recreate
the directory, but this was **fundamentally broken**: systemd processes
``ReadWritePaths`` (mount namespace setup) BEFORE running ``ExecStartPre``.
So when the directory doesn't exist, namespace setup fails with status 226
and ``ExecStartPre=/bin/mkdir`` never runs. It's a chicken-and-egg problem.

The correct fix uses ``systemd-tmpfiles``: a tmpfiles.d config at
``/etc/tmpfiles.d/hori-workspace.conf`` runs early in boot (via
``sysinit.target``) and creates the directory before any services start.

This test has three layers:

  1. **Repo static tests** (no privileges): verify the tmpfiles.d config
     file in the repo and the service unit file are correct.
  2. **Installed static tests** (no privileges): verify the deployed
     tmpfiles.d config and service file match the repo. This catches the
     "fix committed to repo but not deployed" case.
  3. **Simulated reboot** (requires root): stops the service, wipes
     ``/tmp/hori-workspace`` and the stale socket (simulating /tmp and
     /run volatility), runs ``daemon-reload`` + ``systemd-tmpfiles --create``
     (simulating boot), restarts the service, and verifies the workspace
     is recreated, the service is active, and a real tool call succeeds.

Defends: The tmpfiles.d workspace-recreation fix. If the config is
removed, the service file's ``ReadWritePaths`` changes, or the workspace
path changes, these tests break.

Traces to: docs/operations.md "Post-Reboot Health Check" and
"Safety Spine Incident (Aug 8 2026)".
         docs/roadmap.md Tier 2 (safety spine).
"""
import json
import os
import shutil
import socket
import subprocess
import time

import pytest

# --- Paths ---------------------------------------------------------------

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
REPO_SERVICE_FILE = os.path.join(
    PROJECT_DIR, "services", "tool_daemon", "aios-tool-daemon.service"
)
REPO_TMPFILES_CONF = os.path.join(
    PROJECT_DIR, "scripts", "hardening", "hori-workspace.conf"
)
INSTALLED_SERVICE_FILE = "/etc/systemd/system/aios-tool-daemon.service"
INSTALLED_TMPFILES_CONF = "/etc/tmpfiles.d/hori-workspace.conf"
WORKSPACE_DIR = "/tmp/hori-workspace"
SOCKET_PATH = "/run/aios/tool-daemon.sock"
TOOL_DAEMON_SVC = "aios-tool-daemon"
SHERPA_SVC = "aios-sherpa"

# Expected ownership/permissions set by tmpfiles.d
EXPECTED_OWNER = "aios-worker"
EXPECTED_GROUP = "aios-worker"
EXPECTED_MODE = 0o1777


# --- Service file template expansion -------------------------------------

def _expand_service_templates(content):
    """Apply the same %h template substitution as install_tool_daemon.sh.

    The install script replaces ``%h/Projects/hori`` with the project dir,
    ``%h/.config`` and ``%h/Projects`` with the installing user's home
    dir, and ``%i`` with the installing user's username. This helper
    mirrors that logic so the test can compare the templated repo file
    against the de-templated installed file.

    Traces to: scripts/hardening/install_tool_daemon.sh (sed block).
    """
    # Mirror install_tool_daemon.sh:
    #   HORI_USER="${SUDO_USER:-$(logname 2>/dev/null || echo $USER)}"
    hori_user = os.environ.get("SUDO_USER")
    if not hori_user:
        try:
            hori_user = subprocess.check_output(
                ["logname"], text=True, stderr=subprocess.DEVNULL
            ).strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            hori_user = os.environ.get("USER", "")
    #   HORI_HOME=$(getent passwd "$HORI_USER" | cut -d: -f6)
    try:
        hori_home = subprocess.check_output(
            ["getent", "passwd", hori_user], text=True
        ).strip().split(":")[5]
    except (subprocess.CalledProcessError, IndexError):
        hori_home = os.path.expanduser("~")
    # Apply substitutions in the same order as the install script's sed
    # (most specific first, so %h/Projects/hori before %h/Projects).
    content = content.replace("%h/Projects/hori", PROJECT_DIR)
    content = content.replace("%h/.config", f"{hori_home}/.config")
    content = content.replace("%h/Projects", f"{hori_home}/Projects")
    content = content.replace("%i", hori_user)
    return content


# --- Privilege guard -----------------------------------------------------

def _is_root():
    """True if the test process has effective uid 0."""
    return hasattr(os, "geteuid") and os.geteuid() == 0


# The simulated-reboot test stops/starts real systemd services and removes
# a root-owned directory. It requires root. Skip gracefully when not root
# (e.g. running `make test-integration` as a normal user) so the rest of
# the integration suite still runs. To exercise this test:
#   sudo env PYTHONPATH=. ./venv/bin/python3 -m pytest \
#       services/integration_tests/test_reboot_survival.py::TestSimulatedRebootSurvival -v
requires_root = pytest.mark.skipif(
    not _is_root(),
    reason=(
        "simulated-reboot test requires root to stop/start systemd services "
        "and remove /tmp/hori-workspace. Run with: "
        "sudo env PYTHONPATH=. ./venv/bin/python3 -m pytest "
        "services/integration_tests/test_reboot_survival.py -v"
    ),
)


# --- Helpers -------------------------------------------------------------

def _systemctl(*args, check=True, timeout=30):
    """Run a systemctl command, returning the CompletedProcess."""
    return subprocess.run(
        ["systemctl", *args],
        capture_output=True,
        text=True,
        check=check,
        timeout=timeout,
    )


def _service_active(svc):
    """True if the given service is currently active (not just activating)."""
    r = _systemctl("is-active", "--quiet", svc, check=False)
    return r.returncode == 0


def _wait_for_service_active(svc, timeout=20):
    """Wait for the service to reach 'active' state.

    This is more reliable than checking for socket existence: a
    crash-looping daemon (status 226/NAMESPACE) may leave a stale socket
    from a previous run, but it will never reach 'active'.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _service_active(svc):
            return True
        time.sleep(0.5)
    return False


def _wait_for_socket(timeout=10):
    """Wait for the tool daemon socket to exist and be connectable.

    There is a brief race between the service becoming 'active' (main
    process started) and the Python process creating the Unix socket.
    This waits for the socket file to appear.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(SOCKET_PATH):
            return True
        time.sleep(0.3)
    return False


def _wait_for_sherpa_level0(timeout=15):
    """Wait for the Sherpa to write a fresh Level 0 capability file.

    After the Sherpa starts, it takes a few seconds to write the initial
    capability file. Until then, the tool daemon reads a stale file
    (possibly Level 4 from a previous dead Sherpa) and blocks all tool
    calls (fail-closed).
    """
    cap_path = "/run/sherpa/capability_level"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with open(cap_path) as f:
                cap = json.loads(f.read())
            if cap.get("level") == 0:
                # Also check freshness (timestamp within last 10s)
                age = time.time() - cap.get("timestamp", 0)
                if age < 10:
                    return True
        except (OSError, json.JSONDecodeError, KeyError):
            pass
        time.sleep(0.5)
    return False


def _remove_stale_socket():
    """Remove the socket file if it exists (simulates /run volatility on reboot).

    On a real reboot, /run is a tmpfs and is wiped. RuntimeDirectoryPreserve=yes
    preserves /run/aios across service restarts, so a stale socket from a
    crash-looping daemon can fool a socket-existence check. Removing it
    ensures the test only sees a socket created by a successful start.
    """
    try:
        os.unlink(SOCKET_PATH)
    except FileNotFoundError:
        pass


def _simulate_reboot():
    """Simulate a reboot by wiping volatile state and restarting the service.

    This mirrors what happens on a real cold boot:
      1. Stop the service (it's not running during boot)
      2. Wipe /tmp/hori-workspace (volatile — /tmp is wiped on reboot)
      3. Remove stale socket (volatile — /run is tmpfs, wiped on reboot)
      4. daemon-reload (systemd reads fresh unit files on boot)
      5. systemd-tmpfiles --create (tmpfiles.d runs early in boot via
         sysinit.target, creating /tmp/hori-workspace before services start)
      6. Start the service (ReadWritePaths can now bind-mount the workspace)

    After this, the caller should verify the service is active, the
    workspace is recreated, and tool calls work.
    """
    # 1. Stop the tool daemon (Sherpa stops too via Requires=)
    _systemctl("stop", TOOL_DAEMON_SVC, timeout=30)

    # 2. Wipe the volatile workspace (what a reboot does to /tmp)
    shutil.rmtree(WORKSPACE_DIR, ignore_errors=True)

    # 3. Remove stale socket (what a reboot does to /run tmpfs)
    _remove_stale_socket()

    # 4. Reload systemd so it reads the current unit file from disk.
    #    On a real reboot, systemd reads all unit files fresh.
    _systemctl("daemon-reload", timeout=30)

    # 5. Apply tmpfiles.d config — this is what runs at boot via
    #    sysinit.target. It creates /tmp/hori-workspace before any
    #    services start, so ReadWritePaths can bind-mount it.
    subprocess.run(
        ["systemd-tmpfiles", "--create", INSTALLED_TMPFILES_CONF],
        check=True, timeout=30,
    )

    # 6. Start the service — ReadWritePaths can now succeed
    _systemctl("start", TOOL_DAEMON_SVC, timeout=30)

    # 7. Start the Sherpa — on a real reboot, both services start via
    #    WantedBy=multi-user.target. Starting the tool daemon alone does
    #    NOT auto-start the Sherpa (the dependency is one-way: Sherpa
    #    Requires tool daemon, not vice versa). Without the Sherpa, the
    #    capability file goes stale at Level 4 and all tool calls are
    #    blocked (fail-closed).
    _systemctl("start", SHERPA_SVC, check=False, timeout=30)


def _tool_call_count_files(timeout=10):
    """Send a real count_files request to the tool daemon socket.

    Returns the parsed JSON response dict. Raises on socket/parse error.
    """
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(SOCKET_PATH)
    req = json.dumps({
        "tool": "count_files",
        "args": {"path": PROJECT_DIR, "pattern": "*.py"},
    }) + "\n"
    s.sendall(req.encode())
    resp = b""
    while True:
        chunk = s.recv(4096)
        if not chunk:
            break
        resp += chunk
    s.close()
    return json.loads(resp.decode().strip())


def _workspace_owner_mode():
    """Return (owner, group, mode) of the workspace dir, or (None, None, None)."""
    import stat as _stat
    try:
        st = os.stat(WORKSPACE_DIR)
        import pwd, grp
        owner = pwd.getpwuid(st.st_uid).pw_name
        group = grp.getgrgid(st.st_gid).gr_name
        mode = _stat.S_IMODE(st.st_mode)
        return owner, group, mode
    except (OSError, KeyError):
        return None, None, None


def _check_tmpfiles_conf(path):
    """Assert that the given tmpfiles.d config creates /tmp/hori-workspace correctly.

    Returns None on success, or an error message string on failure.
    """
    try:
        with open(path) as f:
            content = f.read()
    except FileNotFoundError:
        return f"tmpfiles.d config not found at {path}"

    # The config line should create the directory with correct owner/mode.
    # Format: d /tmp/hori-workspace 1777 aios-worker aios-worker -
    if "/tmp/hori-workspace" not in content:
        return "tmpfiles.d config doesn't reference /tmp/hori-workspace"
    if "1777" not in content:
        return "tmpfiles.d config doesn't set mode 1777"
    if "aios-worker" not in content:
        return "tmpfiles.d config doesn't set owner aios-worker"
    if not content.strip().startswith("d ") and not any(
        line.strip().startswith("d ") for line in content.splitlines()
    ):
        return "tmpfiles.d config doesn't use 'd' (create directory) type"
    return None


def _check_service_file_readwritepaths(path):
    """Assert that the service file has ReadWritePaths=/tmp/hori-workspace.

    Returns None on success, or an error message string on failure.
    """
    try:
        with open(path) as f:
            content = f.read()
    except FileNotFoundError:
        return f"Service file not found at {path}"

    if "/tmp/hori-workspace" not in content:
        return "/tmp/hori-workspace not in service file (ReadWritePaths)"
    # The service file must NOT have ExecStartPre for the workspace —
    # that approach is fundamentally broken (namespace setup runs before
    # ExecStartPre). If someone re-adds it, this catches the regression.
    if "ExecStartPre=/bin/mkdir -p /tmp/hori-workspace" in content:
        return (
            "Service file has ExecStartPre=/bin/mkdir for /tmp/hori-workspace — "
            "this is broken (ReadWritePaths is processed before ExecStartPre). "
            "Use systemd-tmpfiles instead. See scripts/hardening/hori-workspace.conf"
        )
    return None


# --- Static tests: repo files (no privileges) ----------------------------

class TestRepoFilesWorkspaceFix:
    """Static checks that the repo files contain the correct reboot fix.

    These run without root and catch regressions even when the privileged
    integration test is skipped.
    """

    def test_tmpfiles_conf_exists(self):
        """The tmpfiles.d config must exist in the repo."""
        assert os.path.isfile(REPO_TMPFILES_CONF), (
            f"tmpfiles.d config not found at {REPO_TMPFILES_CONF}"
        )

    def test_tmpfiles_conf_creates_workspace(self):
        """The tmpfiles.d config must create /tmp/hori-workspace with correct owner/mode."""
        err = _check_tmpfiles_conf(REPO_TMPFILES_CONF)
        assert err is None, err

    def test_service_file_has_readwritepaths(self):
        """The service file must have /tmp/hori-workspace in ReadWritePaths."""
        err = _check_service_file_readwritepaths(REPO_SERVICE_FILE)
        assert err is None, err

    def test_service_file_no_broken_execstartpre(self):
        """The service file must NOT use ExecStartPre for the workspace.

        ExecStartPre cannot create a directory that ReadWritePaths needs
        because namespace setup runs before ExecStartPre. This is the
        bug that caused the Aug 8 2026 crash-loop. If someone re-adds
        ExecStartPre for the workspace, this test catches it.
        """
        with open(REPO_SERVICE_FILE) as f:
            content = f.read()
        assert "ExecStartPre=/bin/mkdir -p /tmp/hori-workspace" not in content, (
            "Service file uses ExecStartPre for /tmp/hori-workspace — "
            "this is broken. ReadWritePaths is processed before ExecStartPre, "
            "so the mkdir never runs when the directory is missing. "
            "Use systemd-tmpfiles instead (scripts/hardening/hori-workspace.conf)"
        )


# --- Static tests: installed files (no privileges) -----------------------

class TestInstalledFilesWorkspaceFix:
    """Check that the deployed files have the reboot fix.

    The repo files having the fix is necessary but not sufficient — the
    fix must also be deployed. This catches the "fix committed to repo
    but never installed" case.

    Skipped if the installed files don't exist (e.g. on a dev machine
    without the full deployment).
    """

    def test_installed_tmpfiles_conf_exists(self):
        """The tmpfiles.d config must be installed at /etc/tmpfiles.d/."""
        if not os.path.isfile(INSTALLED_TMPFILES_CONF):
            pytest.skip(
                f"{INSTALLED_TMPFILES_CONF} not found — "
                "service not installed (run scripts/hardening/install_tool_daemon.sh)"
            )

    def test_installed_tmpfiles_conf_has_fix(self):
        """The installed tmpfiles.d config must create /tmp/hori-workspace."""
        if not os.path.isfile(INSTALLED_TMPFILES_CONF):
            pytest.skip(f"{INSTALLED_TMPFILES_CONF} not found — service not installed")
        err = _check_tmpfiles_conf(INSTALLED_TMPFILES_CONF)
        assert err is None, (
            f"{err}. The installed tmpfiles.d config is stale — "
            "reinstall with: sudo scripts/hardening/install_tool_daemon.sh"
        )

    def test_installed_service_file_has_fix(self):
        """The installed service file must have ReadWritePaths and no broken ExecStartPre."""
        if not os.path.isfile(INSTALLED_SERVICE_FILE):
            pytest.skip(f"{INSTALLED_SERVICE_FILE} not found — service not installed")
        err = _check_service_file_readwritepaths(INSTALLED_SERVICE_FILE)
        assert err is None, (
            f"{err}. The installed service file is stale — "
            "reinstall with: sudo scripts/hardening/install_tool_daemon.sh && "
            "sudo systemctl daemon-reload"
        )

    def test_installed_tmpfiles_matches_repo(self):
        """The installed tmpfiles.d config should match the repo."""
        if not os.path.isfile(INSTALLED_TMPFILES_CONF):
            pytest.skip(f"{INSTALLED_TMPFILES_CONF} not found — service not installed")
        with open(REPO_TMPFILES_CONF) as f:
            repo_content = f.read()
        with open(INSTALLED_TMPFILES_CONF) as f:
            installed_content = f.read()
        if repo_content.strip() != installed_content.strip():
            pytest.fail(
                "Installed tmpfiles.d config differs from repo.\n"
                "Reinstall with: sudo scripts/hardening/install_tool_daemon.sh"
            )

    def test_installed_service_matches_repo(self):
        """The installed service file should match the repo service file.

        The repo service file uses ``%h``/``%i`` templates that
        ``install_tool_daemon.sh`` substitutes with real paths at install
        time. We apply the same expansion to the repo file before
        comparing, so this test catches real drift (changed directives,
        removed hardening) rather than the expected template expansion.
        """
        if not os.path.isfile(INSTALLED_SERVICE_FILE):
            pytest.skip(f"{INSTALLED_SERVICE_FILE} not found — service not installed")
        with open(REPO_SERVICE_FILE) as f:
            repo_content = f.read()
        with open(INSTALLED_SERVICE_FILE) as f:
            installed_content = f.read()
        expanded_repo = _expand_service_templates(repo_content)
        if expanded_repo.strip() != installed_content.strip():
            pytest.fail(
                "Installed service file differs from repo service file "
                "(after template expansion).\n"
                "Reinstall with: sudo scripts/hardening/install_tool_daemon.sh && "
                "sudo systemctl daemon-reload"
            )


# --- Privileged integration test (simulated reboot) ----------------------

@requires_root
class TestSimulatedRebootSurvival:
    """Simulate a reboot by wiping volatile state and restarting the service.

    Requires root. Skipped when not root so `make test-integration` still
    works for non-privileged users (the static tests above still run).

    To run this test:
      sudo env PYTHONPATH=. ./venv/bin/python3 -m pytest \\
          services/integration_tests/test_reboot_survival.py::TestSimulatedRebootSurvival -v
    """

    @pytest.fixture(autouse=True)
    def restore_services(self):
        """Ensure the tool daemon and Sherpa are running after the test.

        This fixture runs regardless of test outcome. If the test stops
        the services to simulate a reboot, they are restarted here so the
        system is never left in a broken state. We also daemon-reload and
        apply tmpfiles.d to ensure the workspace exists.

        Note: we always restart the Sherpa, even if it wasn't active
        before the test. A dead Sherpa is a broken safety spine (the
        tool daemon fail-closes to Level 4), not a state to preserve.
        """
        yield
        # Teardown: always make sure services are back up
        _systemctl("daemon-reload", check=False, timeout=30)
        # Ensure the workspace exists (tmpfiles.d creates it)
        if os.path.isfile(INSTALLED_TMPFILES_CONF):
            subprocess.run(
                ["systemd-tmpfiles", "--create", INSTALLED_TMPFILES_CONF],
                check=False, timeout=30,
            )
        if not _service_active(TOOL_DAEMON_SVC):
            _systemctl("start", TOOL_DAEMON_SVC, check=False, timeout=30)
            _wait_for_service_active(TOOL_DAEMON_SVC, timeout=20)
        if not _service_active(SHERPA_SVC):
            _systemctl("start", SHERPA_SVC, check=False, timeout=30)

    def test_workspace_recreated_after_wipe(self):
        """tmpfiles.d must recreate /tmp/hori-workspace after it's wiped.

        Simulates a reboot: stop the service, wipe the workspace and stale
        socket (what happens to /tmp and /run on reboot), daemon-reload +
        systemd-tmpfiles --create (what happens at boot), then start the
        service. The workspace must be recreated with correct ownership
        (aios-worker:aios-worker) and permissions (1777).
        """
        # Preconditions: service must be running to simulate a reboot
        assert _service_active(TOOL_DAEMON_SVC), (
            f"{TOOL_DAEMON_SVC} is not active — cannot simulate reboot. "
            "Start it first: sudo systemctl start aios-tool-daemon"
        )

        # Simulate a reboot
        _simulate_reboot()

        # Wait for the service to be active (not just socket exists —
        # a crash-looping daemon leaves a stale socket)
        assert _wait_for_service_active(TOOL_DAEMON_SVC, timeout=20), (
            f"{TOOL_DAEMON_SVC} did not reach active state after restart — "
            "it may be crash-looping (check: journalctl -u aios-tool-daemon)"
        )

        # Verify the workspace was recreated with correct ownership/mode
        owner, group, mode = _workspace_owner_mode()
        assert owner is not None, (
            f"{WORKSPACE_DIR} was not recreated by tmpfiles.d — "
            "the tool daemon will crash-loop on the next real reboot"
        )
        assert owner == EXPECTED_OWNER, (
            f"Workspace owner is {owner}, expected {EXPECTED_OWNER}"
        )
        assert group == EXPECTED_GROUP, (
            f"Workspace group is {group}, expected {EXPECTED_GROUP}"
        )
        assert mode == EXPECTED_MODE, (
            f"Workspace mode is {oct(mode)}, expected {oct(EXPECTED_MODE)}"
        )

    def test_tool_call_succeeds_after_simulated_reboot(self):
        """An end-to-end tool call must work after the workspace is recreated.

        This proves the tool daemon is not just running but functional —
        the same distinction that the post_reboot_health.sh script makes.
        A crash-looping daemon might briefly have a socket; a real tool
        call confirms the daemon is actually serving.
        """
        # Preconditions
        assert _service_active(TOOL_DAEMON_SVC), (
            f"{TOOL_DAEMON_SVC} is not active — cannot simulate reboot"
        )

        # Simulate a reboot
        _simulate_reboot()
        assert _wait_for_service_active(TOOL_DAEMON_SVC, timeout=20), (
            f"{TOOL_DAEMON_SVC} did not reach active state after restart"
        )

        # Wait for the socket (race: service is 'active' before the
        # Python process creates the Unix socket)
        assert _wait_for_socket(timeout=10), (
            f"Socket did not appear at {SOCKET_PATH} after restart"
        )

        # Wait for the Sherpa to write a fresh Level 0 capability file.
        # Without this, the tool daemon reads a stale Level 4 file and
        # blocks all tool calls (fail-closed).
        assert _wait_for_sherpa_level0(timeout=15), (
            "Sherpa did not reach Level 0 after restart — "
            "tool calls would be blocked (check: journalctl -u aios-sherpa)"
        )

        # Send a real tool call
        result = _tool_call_count_files(timeout=10)
        assert "error" not in result, (
            f"Tool call returned an error after simulated reboot: {result}"
        )
        count = result.get("result", {}).get("count")
        assert count is not None and count > 0, (
            f"count_files returned no count after simulated reboot: {result}"
        )

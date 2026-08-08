"""Adversarial test: credential access attempts.

Tests that the tool system blocks access to credentials:
  - SSH private keys
  - GPG keyring
  - .env files (at the application level; Landlock adds kernel-level)
  - /proc/self/environ
  - GNOME keyring

Defends: PoC 15.0a (aios-worker user), PoC 15.0b (Landlock default-deny),
PoC 15.4 (validation path restrictions).

Status: PARTIALLY PASSING — the validation layer blocks paths outside
allowed prefixes. .env files within ~/Projects are technically
allowed by the registry (the path is within the prefix), but Landlock
(2A) will deny them at the kernel level. The test documents this gap.
"""
import pytest

from services.tool_daemon.validation import validate_tool_call
from pathlib import Path

AIOS_DIR = str(Path(__file__).resolve().parents[2])


HOME_DIR = str(Path.home())


class TestCredentialAccess:
    """Credential access must be blocked at multiple layers."""

    def test_ssh_private_key_blocked(self):
        """Reading ~/.ssh/id_ed25519 must be blocked (outside allowed prefix)."""
        result = validate_tool_call("read_file", {"path": HOME_DIR + "/.ssh/id_ed25519"})
        assert not result.valid
        assert "outside allowed" in result.error

    def test_ssh_directory_blocked(self):
        """Listing ~/.ssh/ must be blocked."""
        result = validate_tool_call("list_dir", {"path": HOME_DIR + "/.ssh"})
        assert not result.valid

    def test_gnupg_blocked(self):
        """Reading ~/.gnupg must be blocked."""
        result = validate_tool_call("list_dir", {"path": HOME_DIR + "/.gnupg"})
        assert not result.valid

    def test_proc_environ_blocked(self):
        """Reading /proc/self/environ must be blocked."""
        result = validate_tool_call("read_file", {"path": "/proc/self/environ"})
        assert not result.valid

    def test_proc_net_tcp_blocked(self):
        """Reading /proc/net/tcp (connection table) must be blocked."""
        result = validate_tool_call("read_file", {"path": "/proc/net/tcp"})
        assert not result.valid

    def test_gnome_keyring_blocked(self):
        """Reading the GNOME keyring must be blocked."""
        result = validate_tool_call("read_file", {
            "path": HOME_DIR + "/.local/share/keyrings/login.keyring"
        })
        assert not result.valid

    def test_hermes_env_blocked(self):
        """Reading ~/.hermes/.env must be blocked."""
        result = validate_tool_call("read_file", {"path": HOME_DIR + "/.hermes/.env"})
        assert not result.valid

    def test_systemd_secrets_blocked(self):
        """Reading /etc/aios/secrets.env must be blocked."""
        result = validate_tool_call("read_file", {"path": "/etc/aios/secrets.env"})
        assert not result.valid

    def test_env_within_projects_allowed_by_registry(self):
        """A .env file within ~/Projects passes registry validation.

        This is a KNOWN GAP: the registry allows any file within the Projects
        prefix, including .env files. Landlock (PoC 15.0b) will deny .env
        files at the kernel level. This test documents that the registry
        alone is not sufficient — Landlock is the primary defense for .env.
        """
        result = validate_tool_call("read_file", {"path": AIOS_DIR + "/.env"})
        assert result.valid  # Registry allows it (within prefix)
        # TODO (2A): Once Landlock is implemented, this test should verify
        # that the tool daemon itself rejects .env files even if the
        # registry allows the path. The Landlock kernel-level denial is
        # the primary defense; the registry is defense in depth.

    def test_docker_socket_blocked(self):
        """Reading the Docker socket must be blocked."""
        result = validate_tool_call("read_file", {"path": "/var/run/docker.sock"})
        assert not result.valid

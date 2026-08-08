import pytest
from unittest.mock import patch, MagicMock
from services.recovery.watchdog import RecoveryWatchdog

@pytest.fixture
def watchdog():
    # Mocking critical_services for testing
    critical_services = {
        "docker": {
            "container_1": "running"
        }
    }
    with patch("services.recovery.watchdog.CONFIG_PATH") as mock_config:
        # We'll mock the config loading in the run method or just mock the file read
        yield RecoveryWatchdog(critical_services)

def test_watchdog_docker_restart(watchdog):
    """
    Regression test for RecoveryWatchdog docker restart.
    Ensures that if a container is missing from docker ps, restart is attempted.
    """
    with patch.object(watchdog, 'get_docker_statuses') as mock_statuses, \
         patch.object(watchdog, 'restart_docker') as mock_restart, \
         patch("services.recovery.watchdog.report_incident") as mock_report, \
         patch("services.recovery.watchdog.json.load") as mock_json, \
         patch("services.recovery.watchdog.Path.exists") as mock_exists, \
         patch("builtins.open", MagicMock()):

        mock_exists.return_value = True
        mock_json.return_value = {
            "check_interval_seconds": 1,
            "services": []
        }
        # container_1 is in critical_services but NOT in current_statuses
        mock_statuses.return_value = {"other_container": "running"}

        # We need to break the infinite loop in run() for testing
        # We can do this by raising an exception after one iteration
        with patch("time.sleep", side_effect=InterruptedError):
            try:
                watchdog.run()
            except InterruptedError:
                pass

        mock_restart.assert_called_once_with("container_1")

def test_watchdog_systemd_restart(watchdog):
    """
    Regression test for RecoveryWatchdog systemd restart.
    Ensures that if a service check fails, restart_command is executed.

    The watchdog requires 2 consecutive failures before attempting a restart
    (added to prevent false positives from transient D-Bus timeouts). So we
    need two failed checks, then a successful restart command.
    """
    with patch.object(watchdog, 'run_command') as mock_run, \
         patch.object(watchdog, 'get_docker_statuses') as mock_docker, \
         patch("services.recovery.watchdog.report_incident") as mock_report, \
         patch("services.recovery.watchdog.json.load") as mock_json, \
         patch("services.recovery.watchdog.Path.exists") as mock_exists, \
         patch("builtins.open", MagicMock()):

        mock_exists.return_value = True
        mock_docker.return_value = {"container_1": "running"}
        mock_json.return_value = {
            "check_interval_seconds": 1,
            "services": [
                {
                    "name": "test_service",
                    "type": "systemd",
                    "check_command": "systemctl is-active test_service",
                    "restart_command": "systemctl restart test_service"
                }
            ]
        }

        # Two failed checks (triggers restart after 2 consecutive failures),
        # then the restart command succeeds.
        mock_run.side_effect = [False, False, True]

        # Let the first iteration pass without raising, then break on the
        # second iteration after the restart has been attempted.
        with patch("time.sleep", side_effect=[None, InterruptedError]):
            try:
                watchdog.run()
            except InterruptedError:
                pass

        assert mock_run.call_count == 3
        mock_run.assert_any_call("systemctl is-active test_service")
        mock_run.assert_any_call("systemctl restart test_service")
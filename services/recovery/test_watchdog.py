import subprocess
import unittest
from unittest.mock import MagicMock, patch

from watchdog import CRITICAL_SERVICES, RecoveryWatchdog


class TestRecoveryWatchdog(unittest.TestCase):

    @patch('subprocess.run')
    def test_get_docker_statuses_success(self, mock_run):
        # Mocking docker ps output
        mock_run.return_value = MagicMock(
            stdout="qdrant_aios: Up 4 hours\nhomeassistant_aios: Up 4 hours\n",
            returncode=0
        )
        
        watchdog = RecoveryWatchdog(CRITICAL_SERVICES)
        statuses = watchdog.get_docker_statuses()
        
        self.assertEqual(statuses["qdrant_aios"], "Up 4 hours")
        self.assertEqual(statuses["homeassistant_aios"], "Up 4 hours")
        self.assertEqual(len(statuses), 2)

    @patch('subprocess.run')
    def test_get_docker_statuses_failure(self, mock_run):
        # Mocking a failure in docker ps
        mock_run.side_effect = subprocess.CalledProcessError(1, 'docker ps')
        
        watchdog = RecoveryWatchdog(CRITICAL_SERVICES)
        statuses = watchdog.get_docker_statuses()
        
        self.assertEqual(statuses, {})

    @patch('subprocess.run')
    def test_restart_docker(self, mock_run):
        watchdog = RecoveryWatchdog(CRITICAL_SERVICES)
        
        # Test successful restart
        mock_run.return_value = MagicMock(returncode=0)
        watchdog.restart_docker("test_container")
        mock_run.assert_called_with(["docker", "restart", "test_container"], check=True)

        # Test failed restart
        mock_run.side_effect = subprocess.CalledProcessError(1, 'docker restart')
        watchdog.restart_docker("test_container")
        # Should not raise exception, just log it

    @patch('subprocess.run')
    def test_restart_systemd(self, mock_run):
        watchdog = RecoveryWatchdog(CRITICAL_SERVICES)
        
        # Test successful restart
        mock_run.return_value = MagicMock(returncode=0)
        watchdog.restart_systemd("test_service")
        mock_run.assert_called_with(
            ["sudo", "systemctl", "restart", "test_service"], check=True
        )

        # Test failed restart
        mock_run.side_effect = subprocess.CalledProcessError(1, 'systemctl restart')
        watchdog.restart_systemd("test_service")
        # Should not raise exception, just log it

    @patch('subprocess.run')
    def test_run_loop_detection(self, mock_run):
        # This is a bit tricky because of the infinite loop. 
        # We will mock the loop to run only once.
        
        # Mocking docker ps output: one service is up, one is missing
        mock_run.side_effect = [
            # First call to get_docker_statuses
            MagicMock(stdout="qdrant_aios: Up 4 hours\n", returncode=0),
            # Second call to restart_docker (for homeassistant_aios which is missing)
            MagicMock(returncode=0),
            # Third call to get_docker_statuses (to break the loop)
            subprocess.CalledProcessError(1, 'docker ps') 
        ]

        docker_services = {"qdrant_aios": "Up", "homeassistant_aios": "Up"}
        watchdog = RecoveryWatchdog({"docker": docker_services})
        
        # We use a side effect to raise an exception to break the while True loop
        # after the first iteration.
        with patch('time.sleep', side_effect=InterruptedError("Break loop")):
            try:
                watchdog.run()
            except InterruptedError:
                pass

        # Verify restart was called for the missing service
        mock_run.assert_any_call(
            ["docker", "restart", "homeassistant_aios"], check=True
        )

if __name__ == "__main__":
    unittest.main()
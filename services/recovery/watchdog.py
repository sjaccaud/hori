import json
import logging
import re
import socket
import subprocess
import time
from pathlib import Path

import requests

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("logs/watchdog.log"),
        logging.StreamHandler()
    ]
)

CONFIG_PATH = Path(__file__).parent / "config.json"
AIOS_CORE_URL = "http://localhost:5680/system/incident"


def report_incident(service: str, incident_type: str, description: str, severity: str = "warning", action_taken: str = None):
    """Report an incident to aios-core for incident memory."""
    try:
        payload = {
            "service": service,
            "incident_type": incident_type,
            "severity": severity,
            "description": description,
            "action_taken": action_taken,
            "resolved": False,
        }
        requests.post(AIOS_CORE_URL, json=payload, timeout=5)
    except Exception as e:
        logging.error(f"Failed to report incident to aios-core: {e}")

# For testing purposes
CRITICAL_SERVICES = {}

class RecoveryWatchdog:
    def __init__(self, critical_services):
        self.critical_services = critical_services
        # Track consecutive failures per service for retry logic.
        # A single transient failure (D-Bus timeout, momentary hang)
        # should not trigger a service_down incident.
        self._failure_counts = {}

    def check_port(self, port):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            return s.connect_ex(('127.0.0.1', port)) == 0

    def check_vram(self, threshold):
        try:
            result = subprocess.run(
                ["rocm-smi", "--showmemuse"], capture_output=True, text=True
            )
            if result.returncode == 0:
                # Look for the line: GPU[0] : GPU Memory Allocated (VRAM%): 78
                match = re.search(
                    r"GPU Memory Allocated \(VRAM%\):\s+(\d+)", result.stdout
                )
                if match:
                    usage = int(match.group(1))
                    return usage, usage >= threshold
            return 0, False
        except Exception as e:
            logging.error(f"Error checking VRAM: {e}")
            return 0, False

    def run_command(self, command, timeout=10):
        try:
            if isinstance(command, str):
                result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=timeout)
                return result.returncode == 0
            else:
                subprocess.run(command, check=True, timeout=timeout)
                return True
        except subprocess.TimeoutExpired:
            logging.warning(f"Command '{command}' timed out after {timeout}s")
            return False
        except Exception as e:
            logging.error(f"Error running command '{command}': {e}")
            return False

    def get_docker_statuses(self):
        try:
            # The test expects this to return a dict of {name: status}
            result = subprocess.run(["docker", "ps", "--format", "{{.Names}}: {{.Status}}"], capture_output=True, text=True, check=True)
            statuses = {}
            for line in result.stdout.strip().split('\n'):
                if line:
                    if ': ' in line:
                        name, status = line.split(': ', 1)
                        statuses[name] = status
            return statuses
        except Exception as e:
            logging.error(f"Error getting docker statuses: {e}")
            return {}

    def restart_docker(self, container_name):
        try:
            subprocess.run(["docker", "restart", container_name], check=True)
            return True
        except Exception as e:
            logging.error(f"Error restarting docker container {container_name}: {e}")
            return False

    def restart_systemd(self, service_name):
        try:
            subprocess.run(["sudo", "systemctl", "restart", service_name], check=True)
            return True
        except Exception as e:
            logging.error(f"Error restarting systemd service {service_name}: {e}")
            return False

    def run(self):
        if not CONFIG_PATH.exists():
            logging.error(f"Config file not found at {CONFIG_PATH}")
            return

        with open(CONFIG_PATH, 'r') as f:
            config = json.load(f)

        interval = config.get("check_interval_seconds", 60)
        services = config.get("services", [])

        logging.info(f"Watchdog started. Monitoring {len(services)} services every {interval}s.")

        try:
            while True:
                # 1. Check Docker services if specified in critical_services
                if "docker" in self.critical_services:
                    docker_targets = self.critical_services["docker"]
                    current_statuses = self.get_docker_statuses()
                    for container, expected_status in docker_targets.items():
                        if container not in current_statuses:
                            logging.warning(f"Docker container '{container}' is DOWN! Attempting restart...")
                            self.restart_docker(container)

                # 2. Check other services from config.json
                for service in services:
                    name = service['name']
                    service_type = service['type']
                    is_healthy = False

                    if service_type == 'systemd':
                        is_healthy = self.run_command(service['check_command'])
                    elif service_type == 'port':
                        is_healthy = self.check_port(service['port'])
                    elif service_type == 'vram':
                        threshold = config.get("vram_threshold_percent", 90)
                        usage, is_over_threshold = self.check_vram(threshold)
                        if is_over_threshold:
                            logging.warning(f"RESOURCE_PRESSURE: GPU VRAM usage is at {usage}% (Threshold: {threshold}%)")
                            report_incident(
                                service=name,
                                incident_type="vram_overflow",
                                description=f"GPU VRAM usage at {usage}% (threshold: {threshold}%)",
                                severity="critical",
                            )
                            is_healthy = True
                        else:
                            is_healthy = True

                    if is_healthy:
                        if service_type != 'vram':
                            # Reset failure counter on success
                            if name in self._failure_counts:
                                del self._failure_counts[name]
                            logging.info(f"Service '{name}' is healthy.")
                    else:
                        # Retry logic: require 2 consecutive failures before
                        # reporting an incident. This prevents false positives
                        # from transient D-Bus timeouts or momentary hangs.
                        self._failure_counts[name] = self._failure_counts.get(name, 0) + 1
                        if self._failure_counts[name] < 2:
                            logging.warning(f"Service '{name}' check failed ({self._failure_counts[name]}/2). Will retry before reporting incident.")
                            continue
                        # Reset counter so we don't keep counting
                        del self._failure_counts[name]
                        logging.warning(f"Service '{name}' is DOWN! Attempting restart...")
                        report_incident(
                            service=name,
                            incident_type="service_down",
                            description=f"Service '{name}' failed health check",
                            severity="critical",
                        )
                        restart_cmd = service['restart_command']
                        if self.run_command(restart_cmd):
                            logging.info(f"Successfully sent restart command for '{name}'.")
                            report_incident(
                                service=name,
                                incident_type="restart_attempted",
                                description=f"Restart command sent for '{name}'",
                                severity="warning",
                                action_taken=restart_cmd,
                            )
                        else:
                            logging.error(f"Failed to execute restart command for '{name}': {restart_cmd}")
                            report_incident(
                                service=name,
                                incident_type="restart_failed",
                                description=f"Failed to restart '{name}': {restart_cmd}",
                                severity="critical",
                                action_taken="none",
                            )

                time.sleep(interval)
        except KeyboardInterrupt:
            logging.info("Watchdog stopped by user.")
        except Exception as e:
            logging.error(f"Watchdog encountered a fatal error: {e}")

def main():
    watchdog = RecoveryWatchdog({})
    watchdog.run()

if __name__ == "__main__":
    main()
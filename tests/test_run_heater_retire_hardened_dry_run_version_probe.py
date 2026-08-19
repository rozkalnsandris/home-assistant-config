import subprocess
import unittest
from unittest.mock import patch

import tools.run_heater_retire_hardened_dry_run as harness


class HardenedRetireDryRunVersionProbeTests(unittest.TestCase):
    def test_running_version_uses_proven_home_assistant_cli_contract(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="2026.8.2\n", stderr=""
        )
        with patch(
            "tools.run_heater_retire_hardened_dry_run_impl._run",
            return_value=completed,
        ) as run:
            value = harness._running_version("docker", "homeassistant")

        self.assertEqual(value, "2026.8.2")
        run.assert_called_once_with(
            [
                "docker",
                "exec",
                "homeassistant",
                "python",
                "-m",
                "homeassistant",
                "--version",
            ]
        )

    def test_running_version_keeps_sanitized_failure_reason(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="private diagnostic suppressed"
        )
        with patch(
            "tools.run_heater_retire_hardened_dry_run_impl._run",
            return_value=completed,
        ):
            with self.assertRaisesRegex(RuntimeError, "^HA_VERSION_PROBE_FAILED$"):
                harness._running_version("docker", "homeassistant")

    def test_running_version_rejects_empty_success_output(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="\n", stderr=""
        )
        with patch(
            "tools.run_heater_retire_hardened_dry_run_impl._run",
            return_value=completed,
        ):
            with self.assertRaisesRegex(RuntimeError, "^HA_VERSION_PROBE_FAILED$"):
                harness._running_version("docker", "homeassistant")


if __name__ == "__main__":
    unittest.main()

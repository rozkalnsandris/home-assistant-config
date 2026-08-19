from pathlib import Path
import unittest
from unittest.mock import patch

import tools.run_heater_retire_hardened_dry_run as harness


class HardenedRetireDryRunStagingTests(unittest.TestCase):
    def test_tools_package_marker_exists(self) -> None:
        marker = Path(__file__).resolve().parents[1] / "tools" / "__init__.py"
        self.assertTrue(marker.is_file())

    def test_private_worker_stage_copies_package_marker_before_payload(self) -> None:
        payload = Path("/tmp/fixture-worker.py")
        destination = "/tmp/private/repo/tools/fixture-worker.py"

        with patch.object(
            harness,
            "_private_stage_original_copy_into_container",
        ) as copy:
            harness._copy_into_container(
                "docker",
                "homeassistant",
                payload,
                destination,
            )

        self.assertEqual(copy.call_count, 2)

        marker_call = copy.call_args_list[0].args
        payload_call = copy.call_args_list[1].args

        self.assertEqual(marker_call[0:2], ("docker", "homeassistant"))
        self.assertEqual(Path(marker_call[2]).name, "__init__.py")
        self.assertEqual(
            marker_call[3],
            "/tmp/private/repo/tools/__init__.py",
        )

        self.assertEqual(
            payload_call,
            ("docker", "homeassistant", payload, destination),
        )

    def test_non_worker_destination_does_not_add_package_marker(self) -> None:
        payload = Path("/tmp/fixture-worker.py")
        destination = "/tmp/not-a-worker/fixture-worker.py"

        with patch.object(
            harness,
            "_private_stage_original_copy_into_container",
        ) as copy:
            harness._copy_into_container(
                "docker",
                "homeassistant",
                payload,
                destination,
            )

        copy.assert_called_once_with(
            "docker",
            "homeassistant",
            payload,
            destination,
        )


if __name__ == "__main__":
    unittest.main()

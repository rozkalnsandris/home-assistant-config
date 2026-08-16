from __future__ import annotations

import json
import unittest
from pathlib import Path

from tools import validate_trusted_proxy_candidate as validator


class CandidateRenderingTests(unittest.TestCase):
    def test_candidate_contains_single_host_address(self) -> None:
        rendered = validator.render_candidate("192.0.2.10")
        self.assertIn("use_x_forwarded_for: true", rendered)
        self.assertIn("trusted_proxies:", rendered)
        self.assertIn("- 192.0.2.10", rendered)


class DockerCommandTests(unittest.TestCase):
    def test_validation_reuses_local_image_without_network_or_pull(self) -> None:
        command = validator.candidate_check_command(
            "docker", "sha256:" + "a" * 64, Path("/tmp/candidate")
        )
        self.assertIn("--pull=never", command)
        self.assertIn("--network=none", command)
        self.assertIn("--fail-on-warnings", command)
        self.assertIn("sha256:" + "a" * 64, command)


class OperatorOutputTests(unittest.TestCase):
    def test_stdout_only_does_not_write_inside_repository(self) -> None:
        self.assertIsNone(validator.resolved_output(stdout=True, output=None))

    def test_explicit_output_is_preserved_with_stdout(self) -> None:
        output = Path("/tmp/candidate-result.json")
        self.assertEqual(
            validator.resolved_output(stdout=True, output=output),
            output,
        )

    def test_non_stdout_keeps_default_sanitized_evidence_file(self) -> None:
        self.assertEqual(
            validator.resolved_output(stdout=False, output=None),
            validator.DEFAULT_OUTPUT,
        )

    def test_bytecode_writes_are_disabled_for_sudo_operator_path(self) -> None:
        self.assertTrue(validator.sys.dont_write_bytecode)


class SanitizedResultTests(unittest.TestCase):
    def test_success_never_emits_candidate_address(self) -> None:
        result = validator.build_result(
            expected="2026.8.2",
            observed="2026.8.2",
            address_class="primary-ipv4",
            check_passed=True,
        )
        rendered = json.dumps(result, sort_keys=True)
        self.assertNotIn("192.0.2.10", rendered)
        self.assertEqual(
            result["candidate"]["decision"], "VALIDATED_FOR_PREPRODUCTION"
        )
        self.assertTrue(result["candidate"]["check_config_passed"])
        self.assertFalse(result["runtime_safety"]["image_pull_allowed"])
        self.assertFalse(result["runtime_safety"]["validation_network_enabled"])
        self.assertFalse(result["runtime_safety"]["production_config_mounted"])
        self.assertFalse(result["privacy"]["exact_address_emitted"])

    def test_version_mismatch_fails_closed(self) -> None:
        result = validator.build_result(
            expected="2026.8.2",
            observed="2026.8.1",
            address_class="primary-ipv4",
            check_passed=True,
        )
        self.assertEqual(result["candidate"]["decision"], "NEEDS_REVIEW")

    def test_failed_check_config_fails_closed(self) -> None:
        result = validator.build_result(
            expected="2026.8.2",
            observed="2026.8.2",
            address_class="primary-ipv4",
            check_passed=False,
        )
        self.assertEqual(result["candidate"]["decision"], "NEEDS_REVIEW")


if __name__ == "__main__":
    unittest.main()

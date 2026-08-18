import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from tools.diagnose_heater_candidate_secret_contract import (
    blocked_report,
    build_report,
    candidate_contracts,
    extract_secret_contract,
    main,
    sanitize_items,
)


class HeaterCandidateSecretContractTests(unittest.TestCase):
    def test_extract_secret_contract_deduplicates(self) -> None:
        text = """
        one: !secret alpha_token
        two: !secret beta_token
        three: !secret alpha_token
        """
        self.assertEqual(
            extract_secret_contract(text),
            ["alpha_token", "beta_token"],
        )

    def test_candidate_contracts_are_ordinal_only(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            first = root / "first.yaml"
            second = root / "second.yaml"
            first.write_text("x: !secret one_token\n", encoding="utf-8")
            second.write_text(
                "a: !secret two_token\nb: !secret three_token\n",
                encoding="utf-8",
            )
            self.assertEqual(
                candidate_contracts((first, second)),
                [["one_token"], ["three_token", "two_token"]],
            )

    def test_sanitize_items_reconciles_counts(self) -> None:
        items = sanitize_items(
            [
                {
                    "ordinal": 1,
                    "required_count": 1,
                    "resolvable_count": 0,
                    "missing_count": 1,
                    "runtime_error": False,
                },
                {
                    "ordinal": 2,
                    "required_count": 2,
                    "resolvable_count": 1,
                    "missing_count": 1,
                    "runtime_error": False,
                },
            ]
        )
        self.assertEqual(
            items,
            [
                {
                    "ordinal": 1,
                    "required_count": 1,
                    "resolvable_count": 0,
                    "missing_count": 1,
                },
                {
                    "ordinal": 2,
                    "required_count": 2,
                    "resolvable_count": 1,
                    "missing_count": 1,
                },
            ],
        )

    def test_sanitize_items_fails_closed_on_runtime_error(self) -> None:
        with self.assertRaises(ValueError):
            sanitize_items(
                [
                    {
                        "ordinal": 1,
                        "required_count": 1,
                        "resolvable_count": 0,
                        "missing_count": 1,
                        "runtime_error": True,
                    }
                ]
            )

    def test_report_contains_counts_not_aliases(self) -> None:
        report = build_report(
            expected="2026.8.2",
            running="2026.8.2",
            items=[
                {
                    "ordinal": 1,
                    "required_count": 1,
                    "resolvable_count": 0,
                    "missing_count": 1,
                },
                {
                    "ordinal": 2,
                    "required_count": 2,
                    "resolvable_count": 1,
                    "missing_count": 1,
                },
            ],
        )
        encoded = json.dumps(report, sort_keys=True)
        self.assertEqual(
            report["decision"],
            "HEATER_CANDIDATE_SECRET_CONTRACT_DIAGNOSTIC_COMPLETE",
        )
        self.assertEqual(report["summary"]["required_reference_count"], 3)
        self.assertEqual(report["summary"]["missing_reference_count"], 2)
        self.assertNotIn("alpha_token", encoded)
        self.assertNotIn("beta_token", encoded)
        self.assertTrue(all(value is False for value in report["privacy"].values()))
        self.assertTrue(all(value is False for value in report["mutation"].values()))

    def test_cli_without_diagnose_is_fail_closed(self) -> None:
        output = StringIO()
        with redirect_stdout(output):
            rc = main([])
        self.assertEqual(rc, 20)
        self.assertEqual(
            json.loads(output.getvalue()),
            blocked_report("DIAGNOSTIC_GATE_REQUIRED"),
        )

    @patch("tools.diagnose_heater_candidate_secret_contract.private_probe")
    @patch("tools.diagnose_heater_candidate_secret_contract.candidate_contracts")
    @patch("tools.diagnose_heater_candidate_secret_contract.running_version")
    @patch("tools.diagnose_heater_candidate_secret_contract.expected_version")
    def test_cli_diagnose_emits_sanitized_counts(
        self,
        expected_version_mock,
        running_version_mock,
        candidate_contracts_mock,
        private_probe_mock,
    ) -> None:
        expected_version_mock.return_value = "2026.8.2"
        running_version_mock.return_value = "2026.8.2"
        candidate_contracts_mock.return_value = [["hidden_one"], ["hidden_two"]]
        private_probe_mock.return_value = [
            {
                "ordinal": 1,
                "required_count": 1,
                "resolvable_count": 0,
                "missing_count": 1,
                "runtime_error": False,
            },
            {
                "ordinal": 2,
                "required_count": 1,
                "resolvable_count": 1,
                "missing_count": 0,
                "runtime_error": False,
            },
        ]

        output = StringIO()
        with redirect_stdout(output):
            rc = main(["--diagnose", "--stdout"])

        report = json.loads(output.getvalue())
        self.assertEqual(rc, 0)
        self.assertEqual(
            report["decision"],
            "HEATER_CANDIDATE_SECRET_CONTRACT_DIAGNOSTIC_COMPLETE",
        )
        self.assertNotIn("hidden_one", output.getvalue())
        self.assertNotIn("hidden_two", output.getvalue())
        self.assertEqual(report["summary"]["missing_reference_count"], 1)


if __name__ == "__main__":
    unittest.main()

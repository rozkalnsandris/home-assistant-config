import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / ".github" / "github-api-access-v1.json"
ROUTING_PATH = ROOT / ".github" / "start-mode-routing.json"


class GitHubApiAccessContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        cls.routing = json.loads(ROUTING_PATH.read_text(encoding="utf-8"))

    def test_shared_contract_revision_is_pinned(self):
        self.assertEqual(
            self.manifest["shared_contract"]["revision"],
            "3bb0740b5f0a8ce631d2ff79f1acc4999ff6ed2c",
        )
        self.assertEqual(
            self.manifest["shared_contract"]["repository"],
            "rozkalnsandris/ops-workflows",
        )

    def test_startup_routing_binds_the_contract(self):
        self.assertEqual(
            self.routing["github_api_access_contract"],
            ".github/github-api-access-v1.json",
        )
        self.assertEqual(self.routing["bare_continuation_result"], "FAST-LANE v2.2")

    def test_reads_are_serial_minimum_sufficient_and_not_tight_polled(self):
        policy = self.manifest["read_policy"]
        self.assertTrue(policy["authenticated_preferred"])
        self.assertTrue(policy["serial_requests"])
        self.assertTrue(policy["minimum_sufficient_reads"])
        self.assertTrue(policy["changed_files_on_demand"])
        self.assertTrue(policy["tight_polling_forbidden"])

    def test_rate_limit_dispositions_are_deterministic(self):
        self.assertEqual(
            set(self.manifest["read_dispositions"]),
            {
                "PRIMARY_RATE_LIMIT_EXHAUSTED",
                "SECONDARY_RATE_LIMIT_SUSPECTED",
                "RETRY_AFTER_REQUIRED",
                "RESET_WAIT_REQUIRED",
                "READ_BACKOFF_REQUIRED",
                "TRANSPORT_RATE_LIMIT_METADATA_UNAVAILABLE",
            },
        )

    def test_ambiguous_mutation_outcomes_require_read_only_reconciliation_and_stop(self):
        policy = self.manifest["mutation_policy"]
        self.assertTrue(policy["serial_writes"])
        self.assertTrue(policy["duplicate_mutation_after_ambiguous_outcome_forbidden"])
        self.assertTrue(policy["reconcile_read_only_after_ambiguous_outcome"])
        self.assertTrue(policy["stop_after_ambiguous_mutation_outcome"])

        ambiguity_fixtures = {
            429: "MUTATION_OUTCOME_UNKNOWN_RATE_LIMIT",
            "timeout": "MUTATION_OUTCOME_UNKNOWN_TIMEOUT",
            "transport": "MUTATION_OUTCOME_UNKNOWN_TRANSPORT",
        }
        declared = set(self.manifest["mutation_dispositions"])
        for disposition in ambiguity_fixtures.values():
            with self.subTest(disposition=disposition):
                self.assertIn(disposition, declared)
                next_actions = ["READ_ONLY_RECONCILIATION", "STOP"]
                self.assertNotIn("RETRY_MUTATION", next_actions)

    def test_rollout_does_not_widen_home_assistant_or_merge_authority(self):
        authority = self.manifest["authority"]
        self.assertTrue(authority["merge_authority_unchanged"])
        self.assertTrue(authority["production_authority_unchanged"])
        self.assertTrue(authority["home_assistant_live_authority_not_granted"])
        self.assertTrue(authority["secrets_permissions_repository_settings_authority_not_granted"])


if __name__ == "__main__":
    unittest.main()

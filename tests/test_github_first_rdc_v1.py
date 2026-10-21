"""Regression contract for GitHub-first / bounded RDC fallback in public HA repo."""
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_REVISION = "4e89b75d7f496dfb43fd9c1eb10a995fad53fdd8"


def select_step(case):
    if case["github_error"]:
        return "STOP"
    if case["github_capable"]:
        return "GITHUB"
    if case["github_ci_capable"]:
        return "GITHUB_ACTIONS"
    if case["host_only"] and case["authority"]:
        return "RDC_EXCEPTION"
    return "STOP"


class GitHubFirstRdcContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = json.loads((ROOT / ".github/github-first-rdc-v1.json").read_text(encoding="utf-8"))
        cls.agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        cls.doc = (ROOT / "docs/GITHUB_FIRST_RDC_ADOPTION_V1.md").read_text(encoding="utf-8")
        cls.routing = json.loads((ROOT / ".github/start-mode-routing.json").read_text(encoding="utf-8"))
        cls.validation = (ROOT / ".github/workflows/validate.yml").read_text(encoding="utf-8")

    def test_pinned_shared_policy(self):
        self.assertEqual(self.policy["schema"], "rozkalns.github-first-rdc-fallback.consumer.v1")
        self.assertEqual(self.policy["repository"], "rozkalnsandris/home-assistant-config")
        self.assertEqual(self.policy["canonical_contract"]["revision"], EXPECTED_REVISION)
        self.assertIn(EXPECTED_REVISION, self.agents)
        self.assertIn(EXPECTED_REVISION, self.doc)

    def test_order_prioritizes_github_and_hosted_ci(self):
        s = self.policy["selection"]
        self.assertTrue(s["per_step"])
        self.assertTrue(s["github_capable_means_github_required"])
        self.assertTrue(s["github_actions_for_equivalent_safe_tests"])
        self.assertFalse(s["convenience_permits_rdc"])
        self.assertEqual(s["order"], ["GITHUB_CONNECTED_TOOLS", "GITHUB_HOSTED_CI_WHEN_APPLICABLE",
                                      "MINIMAL_AUTHORIZED_RDC_IF_GITHUB_INCAPABLE", "STOP"])

    def test_rdc_requires_genuine_gap_and_existing_authority(self):
        r = self.policy["rdc_exception"]
        for name, value in r.items():
            if name != "evidence_fields":
                self.assertTrue(value, name)
        self.assertEqual(r["evidence_fields"], ["STEP", "REPOSITORY_AND_TARGET",
                         "GITHUB_CAPABILITY_CHECKED", "PRECISE_CAPABILITY_GAP",
                         "GITHUB_CI_NOT_EQUIVALENT_REASON", "MINIMAL_RDC_OPERATION",
                         "EXISTING_AUTHORIZATION", "GITHUB_RETURN_EVIDENCE"])

    def test_github_failure_is_stop_not_rdc(self):
        self.assertTrue(all(self.policy["stop_guards"].values()))
        self.assertIn("GitHub access refusal", self.agents)
        self.assertIn("uncertain write", self.doc)

    def test_route_examples(self):
        cases = self.policy["decision_cases"]
        self.assertEqual(len(cases), 8)
        self.assertEqual(len({c["scenario"] for c in cases}), len(cases))
        for case in cases:
            with self.subTest(scenario=case["scenario"]):
                self.assertEqual(select_step(case), case["expected"])

    def test_home_assistant_public_safety_and_modes_remain(self):
        self.assertTrue(all(self.policy["local_boundaries"].values()))
        self.assertEqual(self.routing["bare_continuation_result"], "FAST-LANE v2.2")
        self.assertTrue(self.routing["explicit_modes"]["AUTO-RUN-FULL"]["requires_explicit_current_command_token"])
        self.assertTrue(self.routing["explicit_modes"]["GITHUB-ONLY"]["requires_explicit_current_command_token"])
        self.assertTrue(self.routing["explicit_modes"]["LIVE-ALL"]["requires_explicit_current_command_token"])
        for phrase in ["Do not merge without explicit owner authorization.",
                       "A merge never authorizes production application by itself.",
                       "Never commit secrets", "Do not publish unnecessary private runtime"]:
            self.assertIn(phrase, self.agents)
        self.assertIn("controller issue #140", self.doc)

    def test_existing_github_actions_ci_discovers_test(self):
        self.assertIn("python -m unittest discover -s tests -v", self.validation)
        self.assertIn("tests/test_github_first_rdc_v1.py", self.doc)
        self.assertNotIn("systemctl restart", self.doc)
        self.assertNotIn("wrangler deploy", self.doc)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from tools import audit_trusted_proxy_topology as audit


class ParsingTests(unittest.TestCase):
    def test_parse_prefsrc_accepts_route_json(self) -> None:
        payload = json.dumps([{"type": "local", "prefsrc": "192.0.2.10"}])
        self.assertEqual(audit.parse_prefsrc(payload), "192.0.2.10")

    def test_parse_prefsrc_rejects_missing_source(self) -> None:
        with self.assertRaises(RuntimeError):
            audit.parse_prefsrc(json.dumps([{"type": "local"}]))


class SanitizedResultTests(unittest.TestCase):
    def test_ready_result_never_emits_exact_address(self) -> None:
        result = audit.build_sanitized_result(
            network_mode="host",
            cloudflared_active=True,
            primary_address="192.0.2.10",
            self_source_match=True,
            route_observed=True,
            ha_port_reachable=True,
        )
        rendered = json.dumps(result, sort_keys=True)
        self.assertNotIn("192.0.2.10", rendered)
        self.assertEqual(
            result["candidate"]["decision"],
            "READY_FOR_PRIVATE_SINGLE_HOST_BINDING",
        )
        self.assertEqual(result["candidate"]["trusted_proxy_scope"], "single-host-address")
        self.assertFalse(result["privacy"]["exact_addresses_emitted"])

    def test_non_host_network_mode_fails_closed(self) -> None:
        result = audit.build_sanitized_result(
            network_mode="bridge",
            cloudflared_active=True,
            primary_address="192.0.2.10",
            self_source_match=True,
            route_observed=True,
            ha_port_reachable=True,
        )
        self.assertEqual(result["candidate"]["decision"], "NEEDS_REVIEW")
        self.assertEqual(result["candidate"]["trusted_proxy_scope"], "undetermined")

    def test_missing_route_evidence_fails_closed(self) -> None:
        result = audit.build_sanitized_result(
            network_mode="host",
            cloudflared_active=True,
            primary_address="192.0.2.10",
            self_source_match=True,
            route_observed=False,
            ha_port_reachable=True,
        )
        self.assertEqual(result["candidate"]["decision"], "NEEDS_REVIEW")


class JournalTests(unittest.TestCase):
    @patch.object(audit, "_run")
    def test_journal_match_is_boolean_only(self, run) -> None:
        run.return_value.returncode = 0
        run.return_value.stdout = (
            'INF Updated to new configuration ingress=[{"hostname":"ha.rozkalns.net",'
            '"service":"http://192.0.2.10:8123"}]\n'
        )
        self.assertTrue(audit.journal_observes_current_route("journalctl", "192.0.2.10"))

    @patch.object(audit, "_run")
    def test_journal_mismatch_returns_false(self, run) -> None:
        run.return_value.returncode = 0
        run.return_value.stdout = "INF unrelated configuration\n"
        self.assertFalse(audit.journal_observes_current_route("journalctl", "192.0.2.10"))


if __name__ == "__main__":
    unittest.main()

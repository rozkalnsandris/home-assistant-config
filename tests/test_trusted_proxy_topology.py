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

    def test_parse_proc_tcp_ipv4(self) -> None:
        payload = (
            "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when "
            "retrnsmt   uid  timeout inode\n"
            "   0: 0A0200C0:C350 0A0200C0:1FBB 01 00000000:00000000 "
            "00:00000000 00000000  1000        0 12345 1 0000000000000000\n"
        )
        rows = audit.parse_proc_tcp_ipv4(payload)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["local_address"], "192.0.2.10")
        self.assertEqual(rows[0]["remote_address"], "192.0.2.10")
        self.assertEqual(rows[0]["remote_port"], 8123)
        self.assertEqual(rows[0]["inode"], 12345)


class LiveSocketTests(unittest.TestCase):
    def test_matching_cloudflared_origin_socket(self) -> None:
        rows = [
            {
                "local_address": "192.0.2.10",
                "local_port": 50000,
                "remote_address": "192.0.2.10",
                "remote_port": 8123,
                "state": "01",
                "inode": 12345,
            }
        ]
        result = audit.origin_socket_evidence(rows, {12345}, "192.0.2.10")
        self.assertTrue(result["observed"])
        self.assertTrue(result["source_matches_primary"])
        self.assertTrue(result["destination_matches_primary"])

    def test_non_cloudflared_inode_is_ignored(self) -> None:
        rows = [
            {
                "local_address": "192.0.2.10",
                "local_port": 50000,
                "remote_address": "192.0.2.10",
                "remote_port": 8123,
                "state": "01",
                "inode": 99999,
            }
        ]
        result = audit.origin_socket_evidence(rows, {12345}, "192.0.2.10")
        self.assertFalse(result["observed"])

    def test_wrong_origin_destination_fails_match(self) -> None:
        rows = [
            {
                "local_address": "192.0.2.10",
                "local_port": 50000,
                "remote_address": "192.0.2.20",
                "remote_port": 8123,
                "state": "01",
                "inode": 12345,
            }
        ]
        result = audit.origin_socket_evidence(rows, {12345}, "192.0.2.10")
        self.assertTrue(result["observed"])
        self.assertTrue(result["source_matches_primary"])
        self.assertFalse(result["destination_matches_primary"])


class SanitizedResultTests(unittest.TestCase):
    def build(self, **overrides):
        values = {
            "network_mode": "host",
            "cloudflared_active": True,
            "primary_address": "192.0.2.10",
            "self_source_match": True,
            "route_observed": False,
            "ha_port_reachable": True,
            "live_origin_observed": True,
            "live_origin_source_matches_primary": True,
            "live_origin_destination_matches_primary": True,
            "public_probe_response_observed": True,
        }
        values.update(overrides)
        return audit.build_sanitized_result(**values)

    def test_ready_result_never_emits_exact_address(self) -> None:
        result = self.build()
        rendered = json.dumps(result, sort_keys=True)
        self.assertNotIn("192.0.2.10", rendered)
        self.assertEqual(result["schema_version"], 2)
        self.assertEqual(
            result["candidate"]["decision"],
            "READY_FOR_PRIVATE_SINGLE_HOST_BINDING",
        )
        self.assertEqual(result["candidate"]["trusted_proxy_scope"], "single-host-address")
        self.assertFalse(result["privacy"]["exact_addresses_emitted"])
        self.assertFalse(result["privacy"]["proc_socket_addresses_emitted"])
        self.assertFalse(result["privacy"]["public_probe_credentials_sent"])

    def test_live_socket_can_replace_missing_journal_evidence(self) -> None:
        result = self.build(route_observed=False)
        self.assertEqual(
            result["candidate"]["decision"],
            "READY_FOR_PRIVATE_SINGLE_HOST_BINDING",
        )

    def test_non_host_network_mode_fails_closed(self) -> None:
        result = self.build(network_mode="bridge")
        self.assertEqual(result["candidate"]["decision"], "NEEDS_REVIEW")
        self.assertEqual(result["candidate"]["trusted_proxy_scope"], "undetermined")

    def test_missing_live_socket_evidence_fails_closed(self) -> None:
        result = self.build(
            live_origin_observed=False,
            live_origin_source_matches_primary=False,
            live_origin_destination_matches_primary=False,
            route_observed=True,
        )
        self.assertEqual(result["candidate"]["decision"], "NEEDS_REVIEW")

    def test_wrong_live_socket_source_fails_closed(self) -> None:
        result = self.build(live_origin_source_matches_primary=False)
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

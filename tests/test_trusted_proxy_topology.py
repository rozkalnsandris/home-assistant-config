from __future__ import annotations

import ipaddress
import json
import unittest
from unittest.mock import patch

from tools import audit_trusted_proxy_topology as audit


def proc6_hex(address: str) -> str:
    packed = ipaddress.IPv6Address(address).packed
    return "".join(
        packed[offset : offset + 4][::-1].hex().upper()
        for offset in range(0, 16, 4)
    )


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
        self.assertEqual(rows[0]["family"], "tcp4")
        self.assertEqual(rows[0]["local_address"], "192.0.2.10")
        self.assertEqual(rows[0]["remote_address"], "192.0.2.10")
        self.assertEqual(rows[0]["remote_port"], 8123)
        self.assertEqual(rows[0]["inode"], 12345)

    def test_parse_proc_tcp_ipv6_loopback(self) -> None:
        loopback = proc6_hex("::1")
        payload = (
            "  sl  local_address                         remote_address                        st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"
            f"   0: {loopback}:C350 {loopback}:1FBB 01 00000000:00000000 00:00000000 00000000  1000        0 12345 1 0000000000000000\n"
        )
        rows = audit.parse_proc_tcp_ipv6(payload)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["family"], "tcp6")
        self.assertEqual(rows[0]["local_address"], "::1")
        self.assertEqual(rows[0]["remote_address"], "::1")
        self.assertEqual(rows[0]["remote_port"], 8123)

    def test_ipv4_mapped_ipv6_normalizes_to_ipv4(self) -> None:
        mapped = proc6_hex("::ffff:192.0.2.10")
        payload = (
            "  sl  local_address                         remote_address                        st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"
            f"   0: {mapped}:C350 {mapped}:1FBB 01 00000000:00000000 00:00000000 00000000  1000        0 12345 1 0000000000000000\n"
        )
        rows = audit.parse_proc_tcp_ipv6(payload)
        self.assertEqual(rows[0]["local_address"], "192.0.2.10")
        self.assertEqual(rows[0]["remote_address"], "192.0.2.10")


class LiveSocketTests(unittest.TestCase):
    def record(
        self,
        *,
        source: str,
        destination: str,
        inode: int = 12345,
        family: str = "tcp4",
    ) -> dict[str, object]:
        return {
            "family": family,
            "local_address": source,
            "local_port": 50000,
            "remote_address": destination,
            "remote_port": 8123,
            "state": "01",
            "inode": inode,
        }

    def test_primary_ipv4_socket_is_ready_scope(self) -> None:
        rows = [self.record(source="192.0.2.10", destination="192.0.2.10")]
        result = audit.origin_socket_evidence(rows, {12345}, "192.0.2.10")
        self.assertTrue(result["observed"])
        self.assertEqual(result["source_class"], "primary-ipv4")
        self.assertEqual(result["destination_class"], "primary-ipv4")
        self.assertEqual(result["trusted_proxy_scope"], "single-host-primary-ipv4")

    def test_loopback_ipv4_socket_is_ready_scope(self) -> None:
        rows = [self.record(source="127.0.0.1", destination="127.0.0.1")]
        result = audit.origin_socket_evidence(rows, {12345}, "192.0.2.10")
        self.assertEqual(result["trusted_proxy_scope"], "loopback-ipv4")

    def test_loopback_ipv6_socket_is_ready_scope(self) -> None:
        rows = [
            self.record(
                source="::1",
                destination="::1",
                family="tcp6",
            )
        ]
        result = audit.origin_socket_evidence(rows, {12345}, "192.0.2.10")
        self.assertEqual(result["source_class"], "loopback-ipv6")
        self.assertEqual(result["trusted_proxy_scope"], "loopback-ipv6")

    def test_non_cloudflared_inode_is_ignored(self) -> None:
        rows = [self.record(source="192.0.2.10", destination="192.0.2.10", inode=99999)]
        result = audit.origin_socket_evidence(rows, {12345}, "192.0.2.10")
        self.assertFalse(result["observed"])
        self.assertEqual(result["trusted_proxy_scope"], "undetermined")

    def test_other_private_source_fails_closed(self) -> None:
        rows = [self.record(source="10.0.0.20", destination="192.0.2.10")]
        result = audit.origin_socket_evidence(rows, {12345}, "192.0.2.10")
        self.assertTrue(result["observed"])
        self.assertEqual(result["source_class"], "other-private-ipv4")
        self.assertEqual(result["trusted_proxy_scope"], "undetermined")

    def test_wrong_origin_port_is_ignored(self) -> None:
        row = self.record(source="192.0.2.10", destination="192.0.2.10")
        row["remote_port"] = 9999
        result = audit.origin_socket_evidence([row], {12345}, "192.0.2.10")
        self.assertFalse(result["observed"])


class SanitizedResultTests(unittest.TestCase):
    def build(self, **overrides):
        values = {
            "network_mode": "host",
            "cloudflared_active": True,
            "primary_address": "192.0.2.10",
            "self_source_match": True,
            "route_observed": False,
            "ha_port_reachable": True,
            "live_origin": {
                "observed": True,
                "source_class": "primary-ipv4",
                "destination_class": "primary-ipv4",
                "transport_family": "tcp4",
                "trusted_proxy_scope": "single-host-primary-ipv4",
            },
            "public_probe_response_observed": True,
            "websocket_probe_upgraded": True,
        }
        values.update(overrides)
        return audit.build_sanitized_result(**values)

    def test_ready_result_never_emits_exact_address(self) -> None:
        result = self.build()
        rendered = json.dumps(result, sort_keys=True)
        self.assertNotIn("192.0.2.10", rendered)
        self.assertEqual(result["schema_version"], 3)
        self.assertEqual(
            result["candidate"]["decision"],
            "READY_FOR_PRIVATE_SINGLE_HOST_BINDING",
        )
        self.assertEqual(
            result["candidate"]["trusted_proxy_scope"],
            "single-host-primary-ipv4",
        )
        self.assertFalse(result["privacy"]["exact_addresses_emitted"])
        self.assertFalse(result["privacy"]["proc_socket_addresses_emitted"])
        self.assertFalse(result["privacy"]["public_probe_credentials_sent"])

    def test_loopback_scope_can_be_ready(self) -> None:
        result = self.build(
            live_origin={
                "observed": True,
                "source_class": "loopback-ipv6",
                "destination_class": "loopback-ipv6",
                "transport_family": "tcp6",
                "trusted_proxy_scope": "loopback-ipv6",
            }
        )
        self.assertEqual(
            result["candidate"]["decision"],
            "READY_FOR_PRIVATE_SINGLE_HOST_BINDING",
        )
        self.assertEqual(result["candidate"]["trusted_proxy_scope"], "loopback-ipv6")

    def test_non_host_network_mode_fails_closed(self) -> None:
        result = self.build(network_mode="bridge")
        self.assertEqual(result["candidate"]["decision"], "NEEDS_REVIEW")
        self.assertEqual(result["candidate"]["trusted_proxy_scope"], "undetermined")

    def test_missing_live_socket_evidence_fails_closed(self) -> None:
        result = self.build(
            live_origin={
                "observed": False,
                "source_class": "unobserved",
                "destination_class": "unobserved",
                "transport_family": "unobserved",
                "trusted_proxy_scope": "undetermined",
            },
            route_observed=True,
        )
        self.assertEqual(result["candidate"]["decision"], "NEEDS_REVIEW")

    def test_other_private_socket_fails_closed(self) -> None:
        result = self.build(
            live_origin={
                "observed": True,
                "source_class": "other-private-ipv4",
                "destination_class": "primary-ipv4",
                "transport_family": "tcp4",
                "trusted_proxy_scope": "undetermined",
            }
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

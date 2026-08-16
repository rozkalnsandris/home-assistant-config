#!/usr/bin/env python3
"""Audit the live HA reverse-proxy topology without emitting private addresses."""

from __future__ import annotations

import argparse
import ipaddress
import json
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.inventory_home_assistant import running_containers, select_container

DEFAULT_OUTPUT = ROOT / "exports" / "trusted-proxy-audit.json"
HA_HOSTNAME = "ha.rozkalns.net"
HA_PORT = 8123


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _require_success(result: subprocess.CompletedProcess[str], label: str) -> str:
    if result.returncode != 0:
        raise RuntimeError(f"{label} failed")
    return result.stdout


def home_assistant_network_mode(docker: str, container: str) -> str:
    result = _run(
        [docker, "inspect", "--format", "{{.HostConfig.NetworkMode}}", container]
    )
    value = _require_success(result, "docker inspect").strip()
    if not value:
        raise RuntimeError("docker inspect returned no network mode")
    return value


def cloudflared_is_active(systemctl: str) -> bool:
    result = _run([systemctl, "is-active", "cloudflared.service"])
    return result.returncode == 0 and result.stdout.strip() == "active"


def parse_prefsrc(payload: str) -> str:
    try:
        rows = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise RuntimeError("ip route returned invalid JSON") from exc
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise RuntimeError("ip route returned an unexpected shape")
    value = rows[0].get("prefsrc") or rows[0].get("src")
    if not isinstance(value, str):
        raise RuntimeError("ip route did not expose a preferred source")
    try:
        ipaddress.ip_address(value)
    except ValueError as exc:
        raise RuntimeError("ip route returned an invalid source address") from exc
    return value


def primary_ipv4(ip_cmd: str) -> str:
    result = _run([ip_cmd, "-j", "-4", "route", "get", "1.1.1.1"])
    return parse_prefsrc(_require_success(result, "primary route lookup"))


def self_route_source_matches(ip_cmd: str, address: str) -> bool:
    result = _run([ip_cmd, "-j", "-4", "route", "get", address])
    try:
        source = parse_prefsrc(_require_success(result, "self route lookup"))
    except RuntimeError:
        return False
    return source == address


def journal_observes_current_route(journalctl: str, address: str) -> bool:
    result = _run(
        [
            journalctl,
            "-u",
            "cloudflared.service",
            "-n",
            "2000",
            "--no-pager",
            "-o",
            "cat",
        ]
    )
    text = _require_success(result, "cloudflared journal read")
    expected_origin = f"http://{address}:{HA_PORT}"
    for line in reversed(text.splitlines()):
        if HA_HOSTNAME in line and expected_origin in line:
            return True
    return False


def tcp_reachable(address: str, port: int = HA_PORT, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((address, port), timeout=timeout):
            return True
    except OSError:
        return False


def build_sanitized_result(
    *,
    network_mode: str,
    cloudflared_active: bool,
    primary_address: str,
    self_source_match: bool,
    route_observed: bool,
    ha_port_reachable: bool,
) -> dict:
    address_class = "private" if ipaddress.ip_address(primary_address).is_private else "other"
    ready = all(
        (
            network_mode == "host",
            cloudflared_active,
            address_class == "private",
            self_source_match,
            route_observed,
            ha_port_reachable,
        )
    )
    return {
        "schema_version": 1,
        "home_assistant": {
            "container_detected": True,
            "network_mode": network_mode,
            "port_8123_reachable_via_host_lan": ha_port_reachable,
        },
        "cloudflared": {
            "service_active": cloudflared_active,
            "current_ha_route_to_host_lan_8123_observed": route_observed,
        },
        "network": {
            "primary_ipv4_class": address_class,
            "self_route_source_matches_primary": self_source_match,
        },
        "candidate": {
            "trusted_proxy_scope": "single-host-address" if ready else "undetermined",
            "decision": (
                "READY_FOR_PRIVATE_SINGLE_HOST_BINDING" if ready else "NEEDS_REVIEW"
            ),
        },
        "privacy": {
            "exact_addresses_emitted": False,
            "journal_lines_emitted": False,
            "credentials_read": False,
        },
    }


def audit(
    *,
    docker: str = "docker",
    systemctl: str = "systemctl",
    ip_cmd: str = "ip",
    journalctl: str = "journalctl",
    container: str | None = None,
) -> dict:
    selected = select_container(running_containers(docker), container)
    network_mode = home_assistant_network_mode(docker, selected)
    active = cloudflared_is_active(systemctl)
    address = primary_ipv4(ip_cmd)
    self_match = self_route_source_matches(ip_cmd, address)
    route_seen = journal_observes_current_route(journalctl, address)
    reachable = tcp_reachable(address)
    return build_sanitized_result(
        network_mode=network_mode,
        cloudflared_active=active,
        primary_address=address,
        self_source_match=self_match,
        route_observed=route_seen,
        ha_port_reachable=reachable,
    )


def write_output(data: dict, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only audit of the Home Assistant/cloudflared trusted-proxy topology. "
            "Exact private addresses and journal lines are never emitted."
        )
    )
    parser.add_argument("--container", help="Explicit running Home Assistant container name")
    parser.add_argument("--docker", default="docker")
    parser.add_argument("--systemctl", default="systemctl")
    parser.add_argument("--ip", dest="ip_cmd", default="ip")
    parser.add_argument("--journalctl", default="journalctl")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--stdout", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        data = audit(
            docker=args.docker,
            systemctl=args.systemctl,
            ip_cmd=args.ip_cmd,
            journalctl=args.journalctl,
            container=args.container,
        )
        write_output(data, args.output)
    except (OSError, RuntimeError) as exc:
        print(f"Trusted-proxy audit failed: {exc}", file=sys.stderr)
        return 1

    print(f"Sanitized trusted-proxy audit written to: {args.output}", file=sys.stderr)
    if args.stdout:
        print(json.dumps(data, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

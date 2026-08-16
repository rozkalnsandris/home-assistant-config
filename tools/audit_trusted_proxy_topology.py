#!/usr/bin/env python3
"""Audit the live HA reverse-proxy topology without emitting private addresses."""

from __future__ import annotations

import argparse
import http.client
import ipaddress
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.inventory_home_assistant import running_containers, select_container

DEFAULT_OUTPUT = ROOT / "exports" / "trusted-proxy-audit.json"
HA_HOSTNAME = "ha.rozkalns.net"
HA_PORT = 8123
PROC_ROOT = Path("/proc")


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


def cloudflared_main_pid(systemctl: str) -> int:
    result = _run(
        [systemctl, "show", "--property=MainPID", "--value", "cloudflared.service"]
    )
    raw = _require_success(result, "cloudflared MainPID lookup").strip()
    try:
        pid = int(raw)
    except ValueError as exc:
        raise RuntimeError("cloudflared MainPID was not numeric") from exc
    if pid <= 0:
        raise RuntimeError("cloudflared MainPID was not active")
    return pid


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


def decode_proc_ipv4_endpoint(value: str) -> tuple[str, int]:
    try:
        address_hex, port_hex = value.split(":", 1)
        if len(address_hex) != 8:
            raise ValueError("unexpected IPv4 width")
        address = socket.inet_ntoa(bytes.fromhex(address_hex)[::-1])
        port = int(port_hex, 16)
    except (ValueError, OSError) as exc:
        raise RuntimeError("/proc/net/tcp contained an invalid IPv4 endpoint") from exc
    return address, port


def parse_proc_tcp_ipv4(text: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for line in text.splitlines()[1:]:
        fields = line.split()
        if len(fields) < 10:
            continue
        try:
            local_address, local_port = decode_proc_ipv4_endpoint(fields[1])
            remote_address, remote_port = decode_proc_ipv4_endpoint(fields[2])
            inode = int(fields[9])
        except (RuntimeError, ValueError):
            continue
        rows.append(
            {
                "local_address": local_address,
                "local_port": local_port,
                "remote_address": remote_address,
                "remote_port": remote_port,
                "state": fields[3],
                "inode": inode,
            }
        )
    return rows


def socket_inodes_for_pid(pid: int, proc_root: Path = PROC_ROOT) -> set[int]:
    fd_root = proc_root / str(pid) / "fd"
    try:
        entries = list(fd_root.iterdir())
    except OSError as exc:
        raise RuntimeError("could not inspect cloudflared file descriptors") from exc

    inodes: set[int] = set()
    for entry in entries:
        try:
            target = os.readlink(entry)
        except OSError:
            continue
        if target.startswith("socket:[") and target.endswith("]"):
            raw = target[len("socket:[") : -1]
            try:
                inodes.add(int(raw))
            except ValueError:
                continue
    return inodes


def origin_socket_evidence(
    records: list[dict[str, object]], socket_inodes: set[int], primary_address: str
) -> dict[str, bool]:
    observed = False
    source_matches = False
    destination_matches = False

    for record in records:
        if record.get("inode") not in socket_inodes:
            continue
        if record.get("state") != "01":  # TCP_ESTABLISHED
            continue
        if record.get("remote_port") != HA_PORT:
            continue

        observed = True
        if record.get("local_address") == primary_address:
            source_matches = True
        if record.get("remote_address") == primary_address:
            destination_matches = True

        if source_matches and destination_matches:
            break

    return {
        "observed": observed,
        "source_matches_primary": source_matches,
        "destination_matches_primary": destination_matches,
    }


def inspect_cloudflared_origin_socket(
    pid: int, primary_address: str, proc_root: Path = PROC_ROOT
) -> dict[str, bool]:
    inodes = socket_inodes_for_pid(pid, proc_root)
    try:
        tcp_text = (proc_root / "net" / "tcp").read_text(encoding="ascii")
    except OSError as exc:
        raise RuntimeError("could not read /proc/net/tcp") from exc
    return origin_socket_evidence(parse_proc_tcp_ipv4(tcp_text), inodes, primary_address)


def probe_public_route(attempts: int = 4, timeout: float = 2.0) -> bool:
    """Make an unauthenticated read-only request through the public HA hostname."""
    response_observed = False
    for _ in range(attempts):
        connection = http.client.HTTPSConnection(HA_HOSTNAME, timeout=timeout)
        try:
            connection.request(
                "GET",
                "/",
                headers={
                    "User-Agent": "ha-trusted-proxy-audit/2",
                    "Connection": "close",
                },
            )
            response = connection.getresponse()
            response.read(1)
            response_observed = True
        except OSError:
            pass
        finally:
            connection.close()
        time.sleep(0.15)
    return response_observed


def observe_live_origin_socket(
    pid: int,
    primary_address: str,
    *,
    duration: float = 5.0,
    interval: float = 0.02,
    proc_root: Path = PROC_ROOT,
) -> tuple[dict[str, bool], bool]:
    best = inspect_cloudflared_origin_socket(pid, primary_address, proc_root)
    probe_result = {"response_observed": False}

    def _probe() -> None:
        probe_result["response_observed"] = probe_public_route()

    worker = threading.Thread(target=_probe, daemon=True)
    worker.start()
    deadline = time.monotonic() + duration

    while time.monotonic() < deadline:
        current = inspect_cloudflared_origin_socket(pid, primary_address, proc_root)
        best = {
            "observed": best["observed"] or current["observed"],
            "source_matches_primary": (
                best["source_matches_primary"] or current["source_matches_primary"]
            ),
            "destination_matches_primary": (
                best["destination_matches_primary"]
                or current["destination_matches_primary"]
            ),
        }
        if (
            best["observed"]
            and best["source_matches_primary"]
            and best["destination_matches_primary"]
        ):
            break
        time.sleep(interval)

    worker.join(timeout=max(0.0, duration))
    return best, probe_result["response_observed"]


def build_sanitized_result(
    *,
    network_mode: str,
    cloudflared_active: bool,
    primary_address: str,
    self_source_match: bool,
    route_observed: bool,
    ha_port_reachable: bool,
    live_origin_observed: bool,
    live_origin_source_matches_primary: bool,
    live_origin_destination_matches_primary: bool,
    public_probe_response_observed: bool,
) -> dict:
    address_class = "private" if ipaddress.ip_address(primary_address).is_private else "other"
    live_origin_confirmed = all(
        (
            live_origin_observed,
            live_origin_source_matches_primary,
            live_origin_destination_matches_primary,
        )
    )
    ready = all(
        (
            network_mode == "host",
            cloudflared_active,
            address_class == "private",
            self_source_match,
            ha_port_reachable,
            live_origin_confirmed,
        )
    )
    return {
        "schema_version": 2,
        "home_assistant": {
            "container_detected": True,
            "network_mode": network_mode,
            "port_8123_reachable_via_host_lan": ha_port_reachable,
        },
        "cloudflared": {
            "service_active": cloudflared_active,
            "journal_route_evidence_observed": route_observed,
            "live_origin_socket_to_host_lan_8123_observed": live_origin_observed,
            "live_origin_source_matches_primary": live_origin_source_matches_primary,
            "live_origin_destination_matches_primary": (
                live_origin_destination_matches_primary
            ),
            "public_probe_response_observed": public_probe_response_observed,
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
            "proc_socket_addresses_emitted": False,
            "credentials_read": False,
            "public_probe_credentials_sent": False,
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

    live = {
        "observed": False,
        "source_matches_primary": False,
        "destination_matches_primary": False,
    }
    probe_response = False
    if active:
        pid = cloudflared_main_pid(systemctl)
        live, probe_response = observe_live_origin_socket(pid, address)

    return build_sanitized_result(
        network_mode=network_mode,
        cloudflared_active=active,
        primary_address=address,
        self_source_match=self_match,
        route_observed=route_seen,
        ha_port_reachable=reachable,
        live_origin_observed=live["observed"],
        live_origin_source_matches_primary=live["source_matches_primary"],
        live_origin_destination_matches_primary=live["destination_matches_primary"],
        public_probe_response_observed=probe_response,
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
            "Exact private addresses, journal lines and /proc socket addresses are never emitted."
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

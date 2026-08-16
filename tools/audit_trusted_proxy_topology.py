#!/usr/bin/env python3
"""Audit the live HA reverse-proxy topology without emitting private addresses."""

from __future__ import annotations

import argparse
import base64
import http.client
import ipaddress
import json
import os
import socket
import ssl
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


def normalize_ip_address(value: str) -> str:
    address = ipaddress.ip_address(value)
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return str(address.ipv4_mapped)
    return str(address)


def decode_proc_ipv4_endpoint(value: str) -> tuple[str, int]:
    try:
        address_hex, port_hex = value.split(":", 1)
        if len(address_hex) != 8:
            raise ValueError("unexpected IPv4 width")
        address = socket.inet_ntoa(bytes.fromhex(address_hex)[::-1])
        port = int(port_hex, 16)
    except (ValueError, OSError) as exc:
        raise RuntimeError("/proc/net/tcp contained an invalid IPv4 endpoint") from exc
    return normalize_ip_address(address), port


def decode_proc_ipv6_endpoint(value: str) -> tuple[str, int]:
    try:
        address_hex, port_hex = value.split(":", 1)
        if len(address_hex) != 32:
            raise ValueError("unexpected IPv6 width")
        raw = bytes.fromhex(address_hex)
        network_order = b"".join(
            raw[offset : offset + 4][::-1] for offset in range(0, 16, 4)
        )
        address = socket.inet_ntop(socket.AF_INET6, network_order)
        port = int(port_hex, 16)
    except (ValueError, OSError) as exc:
        raise RuntimeError("/proc/net/tcp6 contained an invalid IPv6 endpoint") from exc
    return normalize_ip_address(address), port


def parse_proc_tcp(text: str, *, family: str) -> list[dict[str, object]]:
    if family not in {"tcp4", "tcp6"}:
        raise ValueError("unsupported proc tcp family")
    decoder = decode_proc_ipv4_endpoint if family == "tcp4" else decode_proc_ipv6_endpoint

    rows: list[dict[str, object]] = []
    for line in text.splitlines()[1:]:
        fields = line.split()
        if len(fields) < 10:
            continue
        try:
            local_address, local_port = decoder(fields[1])
            remote_address, remote_port = decoder(fields[2])
            inode = int(fields[9])
        except (RuntimeError, ValueError):
            continue
        rows.append(
            {
                "family": family,
                "local_address": local_address,
                "local_port": local_port,
                "remote_address": remote_address,
                "remote_port": remote_port,
                "state": fields[3],
                "inode": inode,
            }
        )
    return rows


def parse_proc_tcp_ipv4(text: str) -> list[dict[str, object]]:
    return parse_proc_tcp(text, family="tcp4")


def parse_proc_tcp_ipv6(text: str) -> list[dict[str, object]]:
    return parse_proc_tcp(text, family="tcp6")


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


def safe_address_class(address: str, primary_address: str) -> str:
    normalized = normalize_ip_address(address)
    primary = normalize_ip_address(primary_address)
    if normalized == primary:
        return "primary-ipv4"

    parsed = ipaddress.ip_address(normalized)
    if parsed.is_loopback:
        return "loopback-ipv4" if parsed.version == 4 else "loopback-ipv6"
    if parsed.is_private:
        return "other-private-ipv4" if parsed.version == 4 else "other-private-ipv6"
    return "other"


def origin_socket_evidence(
    records: list[dict[str, object]], socket_inodes: set[int], primary_address: str
) -> dict[str, object]:
    best: dict[str, object] = {
        "observed": False,
        "source_class": "unobserved",
        "destination_class": "unobserved",
        "transport_family": "unobserved",
        "trusted_proxy_scope": "undetermined",
    }

    for record in records:
        if record.get("inode") not in socket_inodes:
            continue
        if record.get("state") != "01":  # TCP_ESTABLISHED
            continue
        if record.get("remote_port") != HA_PORT:
            continue

        source = record.get("local_address")
        destination = record.get("remote_address")
        if not isinstance(source, str) or not isinstance(destination, str):
            continue

        source_class = safe_address_class(source, primary_address)
        destination_class = safe_address_class(destination, primary_address)
        transport_family = str(record.get("family") or "unknown")

        scope = "undetermined"
        if source_class == "primary-ipv4" and destination_class == "primary-ipv4":
            scope = "single-host-primary-ipv4"
        elif source_class == "loopback-ipv4" and destination_class == "loopback-ipv4":
            scope = "loopback-ipv4"
        elif source_class == "loopback-ipv6" and destination_class == "loopback-ipv6":
            scope = "loopback-ipv6"

        current = {
            "observed": True,
            "source_class": source_class,
            "destination_class": destination_class,
            "transport_family": transport_family,
            "trusted_proxy_scope": scope,
        }
        if scope != "undetermined":
            return current
        best = current

    return best


def read_proc_tcp_records(proc_root: Path = PROC_ROOT) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for filename, family in (("tcp", "tcp4"), ("tcp6", "tcp6")):
        path = proc_root / "net" / filename
        try:
            text = path.read_text(encoding="ascii")
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise RuntimeError(f"could not read /proc/net/{filename}") from exc
        records.extend(parse_proc_tcp(text, family=family))
    return records


def inspect_cloudflared_origin_socket(
    pid: int, primary_address: str, proc_root: Path = PROC_ROOT
) -> dict[str, object]:
    inodes = socket_inodes_for_pid(pid, proc_root)
    return origin_socket_evidence(read_proc_tcp_records(proc_root), inodes, primary_address)


def probe_basic_public_route(attempts: int = 2, timeout: float = 2.0) -> bool:
    response_observed = False
    for _ in range(attempts):
        connection = http.client.HTTPSConnection(HA_HOSTNAME, timeout=timeout)
        try:
            connection.request(
                "GET",
                "/",
                headers={
                    "User-Agent": "ha-trusted-proxy-audit/3",
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
    return response_observed


def probe_websocket_route_hold(
    *, hold_seconds: float = 4.0, timeout: float = 3.0
) -> dict[str, bool]:
    """Open an unauthenticated HA WebSocket upgrade and briefly hold it if accepted."""
    response_observed = False
    upgraded = False
    raw_socket: socket.socket | None = None
    tls_socket: ssl.SSLSocket | None = None

    try:
        raw_socket = socket.create_connection((HA_HOSTNAME, 443), timeout=timeout)
        context = ssl.create_default_context()
        tls_socket = context.wrap_socket(raw_socket, server_hostname=HA_HOSTNAME)
        tls_socket.settimeout(timeout)

        websocket_key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            "GET /api/websocket HTTP/1.1\r\n"
            f"Host: {HA_HOSTNAME}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {websocket_key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "User-Agent: ha-trusted-proxy-audit/3\r\n"
            "\r\n"
        )
        tls_socket.sendall(request.encode("ascii"))

        headers = b""
        while b"\r\n\r\n" not in headers and len(headers) < 16384:
            chunk = tls_socket.recv(4096)
            if not chunk:
                break
            headers += chunk

        if headers:
            response_observed = True
            status_line = headers.split(b"\r\n", 1)[0]
            upgraded = status_line.startswith(b"HTTP/") and b" 101 " in status_line

        if upgraded:
            time.sleep(hold_seconds)
    except (OSError, ssl.SSLError):
        pass
    finally:
        if tls_socket is not None:
            try:
                tls_socket.close()
            except OSError:
                pass
        elif raw_socket is not None:
            try:
                raw_socket.close()
            except OSError:
                pass

    return {
        "response_observed": response_observed,
        "websocket_upgraded": upgraded,
    }


def observe_live_origin_socket(
    pid: int,
    primary_address: str,
    *,
    duration: float = 7.0,
    interval: float = 0.01,
    proc_root: Path = PROC_ROOT,
) -> tuple[dict[str, object], dict[str, bool]]:
    best = inspect_cloudflared_origin_socket(pid, primary_address, proc_root)
    probe_result = {
        "response_observed": False,
        "websocket_upgraded": False,
    }

    def _probe() -> None:
        websocket = probe_websocket_route_hold()
        basic = False
        if not websocket["response_observed"]:
            basic = probe_basic_public_route()
        probe_result["response_observed"] = websocket["response_observed"] or basic
        probe_result["websocket_upgraded"] = websocket["websocket_upgraded"]

    worker = threading.Thread(target=_probe, daemon=True)
    worker.start()
    deadline = time.monotonic() + duration

    while time.monotonic() < deadline:
        current = inspect_cloudflared_origin_socket(pid, primary_address, proc_root)
        if current["observed"]:
            best = current
        if current["trusted_proxy_scope"] != "undetermined":
            best = current
            break
        time.sleep(interval)

    worker.join(timeout=max(0.0, duration))
    return best, probe_result


def build_sanitized_result(
    *,
    network_mode: str,
    cloudflared_active: bool,
    primary_address: str,
    self_source_match: bool,
    route_observed: bool,
    ha_port_reachable: bool,
    live_origin: dict[str, object],
    public_probe_response_observed: bool,
    websocket_probe_upgraded: bool,
) -> dict:
    address_class = "private" if ipaddress.ip_address(primary_address).is_private else "other"
    scope = str(live_origin.get("trusted_proxy_scope") or "undetermined")
    live_origin_confirmed = bool(live_origin.get("observed")) and scope in {
        "single-host-primary-ipv4",
        "loopback-ipv4",
        "loopback-ipv6",
    }
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
        "schema_version": 3,
        "home_assistant": {
            "container_detected": True,
            "network_mode": network_mode,
            "port_8123_reachable_via_host_lan": ha_port_reachable,
        },
        "cloudflared": {
            "service_active": cloudflared_active,
            "journal_route_evidence_observed": route_observed,
            "live_origin_socket_to_8123_observed": bool(live_origin.get("observed")),
            "live_origin_source_class": str(
                live_origin.get("source_class") or "unobserved"
            ),
            "live_origin_destination_class": str(
                live_origin.get("destination_class") or "unobserved"
            ),
            "live_origin_transport_family": str(
                live_origin.get("transport_family") or "unobserved"
            ),
            "public_probe_response_observed": public_probe_response_observed,
            "websocket_probe_upgraded": websocket_probe_upgraded,
        },
        "network": {
            "primary_ipv4_class": address_class,
            "self_route_source_matches_primary": self_source_match,
        },
        "candidate": {
            "trusted_proxy_scope": scope if ready else "undetermined",
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

    live: dict[str, object] = {
        "observed": False,
        "source_class": "unobserved",
        "destination_class": "unobserved",
        "transport_family": "unobserved",
        "trusted_proxy_scope": "undetermined",
    }
    probe = {
        "response_observed": False,
        "websocket_upgraded": False,
    }
    if active:
        pid = cloudflared_main_pid(systemctl)
        live, probe = observe_live_origin_socket(pid, address)

    return build_sanitized_result(
        network_mode=network_mode,
        cloudflared_active=active,
        primary_address=address,
        self_source_match=self_match,
        route_observed=route_seen,
        ha_port_reachable=reachable,
        live_origin=live,
        public_probe_response_observed=probe["response_observed"],
        websocket_probe_upgraded=probe["websocket_upgraded"],
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

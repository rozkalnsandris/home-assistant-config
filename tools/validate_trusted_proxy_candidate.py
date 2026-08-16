#!/usr/bin/env python3
"""Validate the private trusted-proxy candidate without exposing its address."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.audit_trusted_proxy_topology import primary_ipv4, safe_address_class
from tools.inventory_home_assistant import running_containers, select_container

EXPECTED_VERSION_FILE = ROOT / "home-assistant-version.txt"
DEFAULT_OUTPUT = ROOT / "exports" / "trusted-proxy-candidate-validation.json"


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


def expected_version() -> str:
    value = EXPECTED_VERSION_FILE.read_text(encoding="utf-8").strip()
    if not value:
        raise RuntimeError("expected Home Assistant version is empty")
    return value


def running_version(docker: str, container: str) -> str:
    result = _run([docker, "exec", container, "python", "-m", "homeassistant", "--version"])
    value = _require_success(result, "running Home Assistant version lookup").strip()
    if not value:
        raise RuntimeError("running Home Assistant version was empty")
    return value


def running_image_id(docker: str, container: str) -> str:
    result = _run([docker, "inspect", "--format", "{{.Image}}", container])
    value = _require_success(result, "running Home Assistant image lookup").strip()
    if not value.startswith("sha256:"):
        raise RuntimeError("running Home Assistant image identity was unexpected")
    return value


def render_candidate(address: str) -> str:
    return (
        "homeassistant:\n"
        "  name: Trusted Proxy Candidate Validation\n"
        "http:\n"
        "  use_x_forwarded_for: true\n"
        "  trusted_proxies:\n"
        f"    - {address}\n"
    )


def candidate_check_command(
    docker: str, image_id: str, config_dir: Path
) -> list[str]:
    return [
        docker,
        "run",
        "--rm",
        "--pull=never",
        "--network=none",
        "-v",
        f"{config_dir}:/config",
        image_id,
        "python",
        "-m",
        "homeassistant",
        "--script",
        "check_config",
        "--config",
        "/config",
        "--fail-on-warnings",
    ]


def validate_candidate(docker: str, image_id: str, address: str) -> bool:
    with tempfile.TemporaryDirectory(prefix="ha-trusted-proxy-candidate-") as raw_dir:
        config_dir = Path(raw_dir)
        (config_dir / "configuration.yaml").write_text(
            render_candidate(address), encoding="utf-8"
        )
        result = _run(candidate_check_command(docker, image_id, config_dir))
        return result.returncode == 0


def build_result(
    *,
    expected: str,
    observed: str,
    address_class: str,
    check_passed: bool,
) -> dict:
    exact_version = observed == expected
    ready = exact_version and address_class == "primary-ipv4" and check_passed
    return {
        "schema_version": 1,
        "candidate": {
            "trusted_proxy_scope": "single-host-primary-ipv4",
            "check_config_passed": check_passed,
            "decision": (
                "VALIDATED_FOR_PREPRODUCTION" if ready else "NEEDS_REVIEW"
            ),
        },
        "home_assistant": {
            "expected_version": expected,
            "running_version_matches_expected": exact_version,
            "running_image_reused": True,
        },
        "runtime_safety": {
            "image_pull_allowed": False,
            "validation_network_enabled": False,
            "production_config_mounted": False,
            "production_container_modified": False,
        },
        "privacy": {
            "exact_address_emitted": False,
            "candidate_config_emitted": False,
            "check_config_output_emitted": False,
            "credentials_read": False,
        },
    }


def audit(
    *,
    docker: str = "docker",
    ip_cmd: str = "ip",
    container: str | None = None,
) -> dict:
    selected = select_container(running_containers(docker), container)
    expected = expected_version()
    observed = running_version(docker, selected)
    if observed != expected:
        raise RuntimeError("running Home Assistant version does not match repository pin")

    image_id = running_image_id(docker, selected)
    address = primary_ipv4(ip_cmd)
    address_class = safe_address_class(address, address)
    if address_class != "primary-ipv4":
        raise RuntimeError("trusted proxy candidate is not the primary host IPv4")

    check_passed = validate_candidate(docker, image_id, address)
    return build_result(
        expected=expected,
        observed=observed,
        address_class=address_class,
        check_passed=check_passed,
    )


def write_output(data: dict, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the single-host trusted-proxy candidate using the already-running "
            "Home Assistant image without printing the private address."
        )
    )
    parser.add_argument("--container", help="Explicit running Home Assistant container name")
    parser.add_argument("--docker", default="docker")
    parser.add_argument("--ip", dest="ip_cmd", default="ip")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--stdout", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        data = audit(docker=args.docker, ip_cmd=args.ip_cmd, container=args.container)
        write_output(data, args.output)
    except (OSError, RuntimeError) as exc:
        print(f"Trusted-proxy candidate validation failed: {exc}", file=sys.stderr)
        return 1

    print(f"Sanitized candidate validation written to: {args.output}", file=sys.stderr)
    if args.stdout:
        print(json.dumps(data, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

import io
import json
import os
from contextlib import redirect_stdout
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools import privileged_atomic_replace as p
import tools.run_heater_retire_production_gate as gate
from tools.verify_bounded_private_file_replace import (
    APPLY,
    EQUALS_CANDIDATE,
    EQUALS_ORIGINAL,
    PrivateFileState,
)


def state_for(tmp: Path, ordinal: int = 1) -> PrivateFileState:
    packages = tmp / "config" / "packages"
    packages.mkdir(parents=True, exist_ok=True)
    name = p.ALLOWED_TARGET_NAMES[ordinal]
    target = packages / name
    target.write_bytes(b"original\n")
    candidate = tmp / f"candidate-{ordinal}"
    candidate.write_bytes(b"candidate\n")
    info = target.stat()
    return PrivateFileState(
        candidate_path=candidate,
        target_path=target,
        candidate_bytes=b"candidate\n",
        original_bytes=b"original\n",
        target_mode=stat.S_IMODE(info.st_mode),
        target_uid=info.st_uid,
        target_gid=info.st_gid,
    )


class PrivilegedAtomicReplaceTests(unittest.TestCase):
    def test_user_writable_fixture_does_not_require_privilege(self):
        with tempfile.TemporaryDirectory() as d:
            state = state_for(Path(d))
            self.assertFalse(p.state_needs_privilege(state))

    def test_privileged_worker_argv_contains_no_private_path_or_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            state = state_for(Path(d))
            request = p._request(state, action="preflight", phase=APPLY, ordinal=1)
            captured = {}

            def fake_run(command, **kwargs):
                captured["command"] = command
                captured["input"] = kwargs["input"]
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout=json.dumps(
                        {
                            "schema": 1,
                            "decision": "PASS",
                            "reason": None,
                            "ordinal": 1,
                            "phase": "apply",
                            "classification": "equals_original",
                            "target_may_have_changed": False,
                            "parent_fsync_performed": False,
                            "privacy": p.privacy_report(),
                        }
                    ),
                    stderr="",
                )

            with (
                patch.object(p, "_worktree_clean", return_value=True),
                patch.object(p.subprocess, "run", side_effect=fake_run),
            ):
                report = p.invoke_privileged_worker(request)
            self.assertEqual(report["decision"], "PASS")
            self.assertEqual(p.PRIVILEGED_SUDO, "/usr/bin/sudo")
            self.assertEqual(captured["command"][:3], ["/usr/bin/sudo", "-n", "--"])
            argv = "\0".join(captured["command"])
            self.assertNotIn(str(state.target_path), argv)
            self.assertNotIn("candidate", argv)
            self.assertIn(str(state.target_path), captured["input"])

    def test_preflight_failure_is_sanitized(self):
        with tempfile.TemporaryDirectory() as d:
            state = state_for(Path(d))
            with (
                patch.object(p, "state_needs_privilege", return_value=True),
                patch.object(
                    p,
                    "invoke_privileged_worker",
                    return_value={
                        "decision": "BLOCKED",
                        "reason": "PRIVILEGED_TARGET_PARENT_NOT_WRITABLE",
                    },
                ),
            ):
                with self.assertRaisesRegex(
                    p.PrivilegedReplacePreflightError,
                    "PRIVILEGED_WRITE_PREFLIGHT_FAILED",
                ):
                    p.preflight_privileged_replacements([state])

    def test_worker_preflight_accepts_exact_bounded_target(self):
        with tempfile.TemporaryDirectory() as d:
            state = state_for(Path(d))
            payload = p._request(state, action="preflight", phase=APPLY, ordinal=1)
            with (
                patch.object(p.os, "geteuid", return_value=0),
                patch.object(p, "_parent_writable", return_value=True),
            ):
                report = p.worker_handle(payload)
            self.assertEqual(report["decision"], "PASS")
            self.assertEqual(report["classification"], EQUALS_ORIGINAL)
            self.assertTrue(all(v is False for v in report["privacy"].values()))

    def test_worker_apply_and_rollback_preserve_content_and_metadata(self):
        with tempfile.TemporaryDirectory() as d:
            state = state_for(Path(d))
            apply_payload = p._request(
                state, action="replace", phase=APPLY, ordinal=1
            )
            with patch.object(p.os, "geteuid", return_value=0):
                apply_report = p.worker_handle(apply_payload)
            self.assertEqual(apply_report["decision"], "PASS")
            self.assertEqual(state.target_path.read_bytes(), b"candidate\n")
            info = state.target_path.stat()
            self.assertEqual(stat.S_IMODE(info.st_mode), state.target_mode)
            self.assertEqual(info.st_uid, state.target_uid)
            self.assertEqual(info.st_gid, state.target_gid)

            rollback_payload = p._request(
                state, action="replace", phase="rollback", ordinal=1
            )
            with patch.object(p.os, "geteuid", return_value=0):
                rollback_report = p.worker_handle(rollback_payload)
            self.assertEqual(rollback_report["decision"], "PASS")
            self.assertEqual(
                rollback_report["classification"], EQUALS_ORIGINAL
            )
            self.assertEqual(state.target_path.read_bytes(), b"original\n")

    def test_worker_rejects_unbounded_target_name_without_echoing_path(self):
        with tempfile.TemporaryDirectory() as d:
            state = state_for(Path(d))
            payload = p._request(state, action="preflight", phase=APPLY, ordinal=1)
            payload["target_path"] = str(Path(d) / "packages" / "other.yaml")
            with patch.object(p.os, "geteuid", return_value=0):
                report = p.worker_handle(payload)
            encoded = json.dumps(report)
            self.assertEqual(report["decision"], "BLOCKED")
            self.assertNotIn("other.yaml", encoded)

    def test_privileged_apply_wrapper_reverifies_host_view(self):
        with tempfile.TemporaryDirectory() as d:
            state = state_for(Path(d))

            def fake_invoke(_request):
                state.target_path.write_bytes(state.candidate_bytes)
                return {
                    "decision": p.PASS,
                    "classification": p.EQUALS_CANDIDATE,
                    "parent_fsync_performed": True,
                }

            with (
                patch.object(p, "state_needs_privilege", return_value=True),
                patch.object(
                    p,
                    "invoke_privileged_worker",
                    side_effect=fake_invoke,
                ),
            ):
                result = p.apply_verified_replace_with_privilege(
                    state, ordinal=1
                )
            self.assertEqual(result.classification, EQUALS_CANDIDATE)
            self.assertTrue(result.parent_fsync_performed)

    def test_dirty_worktree_blocks_before_sudo(self):
        request = {
            "ordinal": 1,
            "phase": p.APPLY,
        }
        with (
            patch.object(p, "_worktree_clean", return_value=False),
            patch.object(p.subprocess, "run") as run,
        ):
            report = p.invoke_privileged_worker(request)
        run.assert_not_called()
        self.assertEqual(
            report["reason"], "PRIVILEGED_SOURCE_WORKTREE_NOT_CLEAN"
        )
        self.assertTrue(all(v is False for v in report["privacy"].values()))

    def test_canonical_gate_installs_privileged_apply_and_rollback(self):
        self.assertIs(
            gate.apply_verified_replace,
            p.apply_verified_replace_with_privilege,
        )
        self.assertIs(
            gate.rollback_verified_replace,
            p.rollback_verified_replace_with_privilege,
        )

    def test_canonical_gate_rejects_root_operator(self):
        stdout = io.StringIO()
        with (
            patch.object(gate.main.__globals__["os"], "geteuid", return_value=0),
            redirect_stdout(stdout),
        ):
            rc = gate.main([])
        report = json.loads(stdout.getvalue())
        self.assertEqual(rc, 20)
        self.assertEqual(report["decision"], gate.BLOCKED)
        self.assertEqual(report["reason"], "PRODUCTION_GATE_MUST_NOT_RUN_AS_ROOT")
        self.assertFalse(report["authorization_consumed"])
        self.assertFalse(report["production_change"])

    def test_privilege_preflight_runs_before_rollback_bundle(self):
        wrapper = gate.preflight.create_rollback_bundle
        events = []

        def fake_preflight(_states):
            events.append("preflight")

        def fake_bundle(*_args, **_kwargs):
            events.append("bundle")
            return ("rollback", "scheduler")

        with (
            patch.object(
                p,
                "preflight_privileged_replacements",
                side_effect=fake_preflight,
            ),
            patch.dict(
                wrapper.__globals__,
                {"_original_create_rollback_bundle": fake_bundle},
            ),
        ):
            result = wrapper(states=[])

        self.assertEqual(events, ["preflight", "bundle"])
        self.assertEqual(result, ("rollback", "scheduler"))

    def test_preserved_impl_consumes_authorization_after_bundle_boundary(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "tools"
            / "run_heater_retire_production_gate_impl.py"
        ).read_text(encoding="utf-8")
        self.assertLess(
            source.index("preflight.create_rollback_bundle("),
            source.index("common.consume_authorization("),
        )


if __name__ == "__main__":
    unittest.main()

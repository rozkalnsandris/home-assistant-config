import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.verify_bounded_private_file_replace import (
    APPLY,
    EQUALS_CANDIDATE,
    EQUALS_ORIGINAL,
    OTHER,
    ROLLBACK,
    VerifiedReplaceError,
    apply_verified_replace,
    apply_verified_sequence,
    classify_bytes,
    rollback_verified_replace,
    snapshot_private_file,
)


class VerifiedPrivateFileReplaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def make_state(
        self,
        ordinal: int = 1,
        *,
        original: bytes = b"original\n",
        candidate: bytes = b"candidate\n",
    ):
        candidate_path = self.root / f"candidate-{ordinal}.yaml"
        target_path = self.root / f"target-{ordinal}.yaml"
        candidate_path.write_bytes(candidate)
        target_path.write_bytes(original)
        candidate_path.chmod(0o600)
        target_path.chmod(0o640)
        return snapshot_private_file(candidate_path, target_path)

    def test_classify_bytes_covers_candidate_original_and_other(self) -> None:
        self.assertEqual(
            classify_bytes(b"candidate", b"candidate", b"original"),
            EQUALS_CANDIDATE,
        )
        self.assertEqual(
            classify_bytes(b"original", b"candidate", b"original"),
            EQUALS_ORIGINAL,
        )
        self.assertEqual(
            classify_bytes(b"foreign", b"candidate", b"original"),
            OTHER,
        )

    def test_successful_apply_writes_candidate_and_reports_only_classification(self) -> None:
        state = self.make_state()

        result = apply_verified_replace(state, ordinal=1)

        self.assertEqual(state.target_path.read_bytes(), state.candidate_bytes)
        self.assertEqual(result.phase, APPLY)
        self.assertEqual(result.classification, EQUALS_CANDIDATE)
        self.assertTrue(result.parent_fsync_performed)
        self.assertEqual(result.public_report()["decision"], "VERIFIED")

    def test_failed_atomic_apply_is_fail_closed(self) -> None:
        state = self.make_state()

        with patch(
            "tools.verify_bounded_private_file_replace._atomic_replace_bytes",
            side_effect=OSError("fixture failure"),
        ):
            with self.assertRaises(VerifiedReplaceError) as context:
                apply_verified_replace(state, ordinal=1)

        error = context.exception
        self.assertEqual(error.reason, "ATOMIC_APPLY_FAILED")
        self.assertEqual(error.classification, EQUALS_ORIGINAL)
        self.assertFalse(error.target_may_have_changed)
        self.assertEqual(state.target_path.read_bytes(), state.original_bytes)

    def test_post_apply_original_reappearance_is_classified_and_blocked(self) -> None:
        state = self.make_state()

        with patch(
            "tools.verify_bounded_private_file_replace._read_bytes",
            side_effect=[state.original_bytes, state.original_bytes],
        ):
            with self.assertRaises(VerifiedReplaceError) as context:
                apply_verified_replace(state, ordinal=1)

        error = context.exception
        self.assertEqual(error.reason, "POST_APPLY_VERIFICATION_MISMATCH")
        self.assertEqual(error.classification, EQUALS_ORIGINAL)
        self.assertTrue(error.target_may_have_changed)
        self.assertTrue(error.parent_fsync_performed)

    def test_post_apply_other_content_is_classified_and_blocked(self) -> None:
        state = self.make_state()

        with patch(
            "tools.verify_bounded_private_file_replace._read_bytes",
            side_effect=[state.original_bytes, b"foreign-content"],
        ):
            with self.assertRaises(VerifiedReplaceError) as context:
                apply_verified_replace(state, ordinal=1)

        error = context.exception
        self.assertEqual(error.reason, "POST_APPLY_VERIFICATION_MISMATCH")
        self.assertEqual(error.classification, OTHER)

    def test_parent_directory_fsync_is_required_on_apply(self) -> None:
        state = self.make_state()

        with patch(
            "tools.verify_bounded_private_file_replace._fsync_parent_directory",
            wraps=__import__(
                "tools.verify_bounded_private_file_replace",
                fromlist=["_fsync_parent_directory"],
            )._fsync_parent_directory,
        ) as fsync_parent:
            result = apply_verified_replace(state, ordinal=1)

        self.assertTrue(result.parent_fsync_performed)
        fsync_parent.assert_called_once_with(state.target_path.parent)

    def test_rollback_restores_original_with_verified_classification(self) -> None:
        state = self.make_state()
        apply_verified_replace(state, ordinal=1)

        result = rollback_verified_replace(state, ordinal=1)

        self.assertEqual(state.target_path.read_bytes(), state.original_bytes)
        self.assertEqual(result.phase, ROLLBACK)
        self.assertEqual(result.classification, EQUALS_ORIGINAL)
        self.assertTrue(result.parent_fsync_performed)

    def test_post_rollback_candidate_reappearance_is_classified_and_blocked(self) -> None:
        state = self.make_state()
        apply_verified_replace(state, ordinal=1)

        with patch(
            "tools.verify_bounded_private_file_replace._read_bytes",
            return_value=state.candidate_bytes,
        ):
            with self.assertRaises(VerifiedReplaceError) as context:
                rollback_verified_replace(state, ordinal=1)

        error = context.exception
        self.assertEqual(error.reason, "POST_ROLLBACK_VERIFICATION_MISMATCH")
        self.assertEqual(error.classification, EQUALS_CANDIDATE)
        self.assertEqual(error.phase, ROLLBACK)

    def test_sequence_stops_before_second_ordinal_after_first_failure(self) -> None:
        first = self.make_state(1)
        second = self.make_state(2)

        failure = VerifiedReplaceError(
            "POST_APPLY_VERIFICATION_MISMATCH",
            ordinal=1,
            phase=APPLY,
            classification=EQUALS_ORIGINAL,
            target_may_have_changed=True,
            parent_fsync_performed=True,
        )

        with patch(
            "tools.verify_bounded_private_file_replace.apply_verified_replace",
            side_effect=failure,
        ) as apply_one:
            with self.assertRaises(VerifiedReplaceError):
                apply_verified_sequence([first, second])

        self.assertEqual(apply_one.call_count, 1)
        self.assertEqual(second.target_path.read_bytes(), second.original_bytes)

    def test_snapshot_rejects_symlink_candidate(self) -> None:
        real_candidate = self.root / "candidate-real.yaml"
        candidate_link = self.root / "candidate-link.yaml"
        target = self.root / "target.yaml"
        real_candidate.write_bytes(b"candidate")
        target.write_bytes(b"original")
        candidate_link.symlink_to(real_candidate)

        with self.assertRaisesRegex(ValueError, "CANDIDATE_FILE_INVALID"):
            snapshot_private_file(candidate_link, target)

    def test_snapshot_rejects_non_regular_target(self) -> None:
        candidate = self.root / "candidate.yaml"
        target_dir = self.root / "target-dir"
        candidate.write_bytes(b"candidate")
        target_dir.mkdir()

        with self.assertRaisesRegex(ValueError, "TARGET_FILE_INVALID"):
            snapshot_private_file(candidate, target_dir)

    def test_private_state_repr_and_reports_do_not_emit_sensitive_values(self) -> None:
        private_target_marker = "switch.fixture_private_target"
        private_path_marker = "private-household-path"
        original = f"original {private_target_marker}".encode()
        candidate = f"candidate {private_target_marker}".encode()

        private_root = self.root / private_path_marker
        private_root.mkdir()
        candidate_path = private_root / "candidate.yaml"
        target_path = private_root / "target.yaml"
        candidate_path.write_bytes(candidate)
        target_path.write_bytes(original)

        state = snapshot_private_file(candidate_path, target_path)
        result = apply_verified_replace(state, ordinal=1)

        encoded = json.dumps(result.public_report(), sort_keys=True)
        state_repr = repr(state)

        self.assertNotIn(private_target_marker, encoded)
        self.assertNotIn(private_path_marker, encoded)
        self.assertNotIn(private_target_marker, state_repr)
        self.assertNotIn(private_path_marker, state_repr)
        self.assertTrue(
            all(
                value is False
                for value in result.public_report()["privacy"].values()
            )
        )

    def test_error_report_is_sanitized(self) -> None:
        marker = "switch.fixture_private_target"
        error = VerifiedReplaceError(
            "POST_APPLY_VERIFICATION_MISMATCH",
            ordinal=2,
            phase=APPLY,
            classification=OTHER,
            target_may_have_changed=True,
            parent_fsync_performed=True,
        )

        encoded = json.dumps(error.public_report(), sort_keys=True)

        self.assertNotIn(marker, encoded)
        self.assertNotIn("/", encoded)
        self.assertEqual(error.public_report()["classification"], OTHER)
        self.assertTrue(
            all(
                value is False
                for value in error.public_report()["privacy"].values()
            )
        )


if __name__ == "__main__":
    unittest.main()

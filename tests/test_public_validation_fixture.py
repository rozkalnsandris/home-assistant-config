from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.build_public_validation_fixture import (
    DUMMY_PRIVATE_FILES,
    DUMMY_SECRETS,
    DUMMY_THEME,
    PUBLIC_PACKAGE_FILES,
    PUBLIC_SOURCE_FILES,
    build_fixture,
    read_home_assistant_version,
)


class PublicValidationFixtureTests(unittest.TestCase):
    def make_source(self, root: Path) -> None:
        (root / "home-assistant-version.txt").write_text("2026.8.2\n", encoding="utf-8")
        for name in (*PUBLIC_SOURCE_FILES, *PUBLIC_PACKAGE_FILES):
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"# {name}\n", encoding="utf-8")

    def test_exact_version_is_read(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_source(root)
            self.assertEqual(read_home_assistant_version(root), "2026.8.2")

    def test_invalid_version_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "home-assistant-version.txt").write_text("latest\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                read_home_assistant_version(root)

    def test_fixture_contains_only_allowlisted_public_source_and_dummy_bindings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            output = root / "fixture"
            source.mkdir()
            self.make_source(source)

            (source / "secrets.yaml").write_text("token: SUPER-SECRET\n", encoding="utf-8")
            (source / ".storage").mkdir()
            (source / ".storage/auth").write_text("PRIVATE-RUNTIME\n", encoding="utf-8")
            (source / "private").mkdir()
            (source / "private/http.yaml").write_text(
                "trusted_proxies:\n  - PRIVATE-PROXY\n",
                encoding="utf-8",
            )
            (source / "dashboards").mkdir()
            (source / "dashboards/majas.yaml").write_text(
                "entity: sensor.private_room\n",
                encoding="utf-8",
            )

            build_fixture(source, output)

            actual = sorted(
                path.relative_to(output).as_posix()
                for path in output.rglob("*")
                if path.is_file()
            )
            expected = sorted(
                [
                    *PUBLIC_SOURCE_FILES,
                    *PUBLIC_PACKAGE_FILES,
                    *DUMMY_PRIVATE_FILES.keys(),
                    "secrets.yaml",
                    "themes/ci-placeholder.yaml",
                ]
            )
            self.assertEqual(actual, expected)
            self.assertEqual((output / "secrets.yaml").read_text(), DUMMY_SECRETS)
            self.assertEqual(
                (output / "themes/ci-placeholder.yaml").read_text(), DUMMY_THEME
            )

            combined = "\n".join(
                path.read_text(encoding="utf-8")
                for path in output.rglob("*")
                if path.is_file()
            )
            self.assertNotIn("SUPER-SECRET", combined)
            self.assertNotIn("PRIVATE-RUNTIME", combined)
            self.assertNotIn("PRIVATE-PROXY", combined)
            self.assertNotIn("sensor.private_room", combined)

    def test_dummy_secrets_preserve_expected_scalar_types(self) -> None:
        self.assertIn('heater_schedule_initial_time: "12:34:00"', DUMMY_SECRETS)
        self.assertIn("heater_switch_entity: switch.ci_heater", DUMMY_SECRETS)
        self.assertIn("electricity_price_per_kwh: 0.1234", DUMMY_SECRETS)
        self.assertIn("electricity_fixed_monthly: 1.23", DUMMY_SECRETS)

    def test_missing_source_fails_before_output_is_created(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            output = root / "fixture"
            source.mkdir()
            self.make_source(source)
            (source / "packages/silditajs.yaml").unlink()

            with self.assertRaises(FileNotFoundError):
                build_fixture(source, output)

            self.assertFalse(output.exists())

    def test_existing_output_is_never_reused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            output = root / "fixture"
            source.mkdir()
            output.mkdir()
            self.make_source(source)

            with self.assertRaises(FileExistsError):
                build_fixture(source, output)


if __name__ == "__main__":
    unittest.main()

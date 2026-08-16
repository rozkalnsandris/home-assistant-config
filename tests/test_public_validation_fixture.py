from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.build_public_validation_fixture import (
    MINIMAL_CONFIGURATION,
    PUBLIC_SOURCE_FILES,
    build_fixture,
    read_home_assistant_version,
)


class PublicValidationFixtureTests(unittest.TestCase):
    def make_source(self, root: Path) -> None:
        (root / "home-assistant-version.txt").write_text("2026.8.2\n", encoding="utf-8")
        for name in PUBLIC_SOURCE_FILES:
            (root / name).write_text(f"# {name}\n", encoding="utf-8")

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

    def test_fixture_contains_only_allowlisted_public_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            output = root / "fixture"
            source.mkdir()
            self.make_source(source)

            (source / "secrets.yaml").write_text("token: SUPER-SECRET\n", encoding="utf-8")
            (source / ".storage").mkdir()
            (source / ".storage/auth").write_text("PRIVATE-RUNTIME\n", encoding="utf-8")
            (source / "dashboards").mkdir()
            (source / "dashboards/majas.yaml").write_text(
                "entity: sensor.private_room\n",
                encoding="utf-8",
            )

            build_fixture(source, output)

            self.assertEqual(
                sorted(path.name for path in output.iterdir()),
                ["automations.yaml", "configuration.yaml", "scenes.yaml", "scripts.yaml"],
            )
            self.assertEqual(
                (output / "configuration.yaml").read_text(encoding="utf-8"),
                MINIMAL_CONFIGURATION,
            )
            combined = "\n".join(
                path.read_text(encoding="utf-8")
                for path in output.iterdir()
                if path.is_file()
            )
            self.assertNotIn("SUPER-SECRET", combined)
            self.assertNotIn("PRIVATE-RUNTIME", combined)
            self.assertNotIn("sensor.private_room", combined)

    def test_missing_source_fails_before_output_is_created(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            output = root / "fixture"
            source.mkdir()
            self.make_source(source)
            (source / "scenes.yaml").unlink()

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

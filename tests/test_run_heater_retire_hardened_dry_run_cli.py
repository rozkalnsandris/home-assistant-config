import json
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "tools" / "run_heater_retire_hardened_dry_run.py"


class HardenedRetireDirectCliTests(unittest.TestCase):
    def test_direct_cli_from_repo_root_reaches_fail_closed_gate(self) -> None:
        result = subprocess.run(
            [sys.executable, "-B", str(LAUNCHER.relative_to(ROOT))],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

        self.assertEqual(result.returncode, 20)
        report = json.loads(result.stdout)
        self.assertEqual(report["decision"], "BLOCKED")
        self.assertEqual(report["reason"], "EXACT_SOURCE_AND_VERSION_GATE_REQUIRED")
        self.assertNotIn("ModuleNotFoundError", result.stderr)
        self.assertTrue(all(value is False for value in report["privacy"].values()))
        self.assertTrue(
            all(value is False for value in report["production_mutation"].values())
        )


if __name__ == "__main__":
    unittest.main()

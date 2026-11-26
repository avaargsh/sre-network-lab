"""Offline PMTU verifier regressions; never invoke the live lab."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


class EvidenceCLIContractTests(unittest.TestCase):
    def test_non_object_metadata_is_not_verified(self):
        for script in ("verify_pmtu_evidence.py", "verify_pmtu_tcp_evidence.py"):
            for file, invalid in (
                ("result.json", []), ("result.json", None),
                ("result.json", 42), ("environment.json", []),
            ):
                with self.subTest(script=script, file=file, invalid=invalid):
                    with tempfile.TemporaryDirectory() as directory:
                        folder = Path(directory)
                        (folder / "result.json").write_text(json.dumps({
                            "result": "PASS", "namespaces_remaining": []
                        }), encoding="utf-8")
                        (folder / file).write_text(json.dumps(invalid), encoding="utf-8")
                        proc = subprocess.run(
                            [sys.executable, str(SCRIPTS / script), directory],
                            capture_output=True, text=True, timeout=10, check=False,
                        )
                    self.assertEqual(proc.returncode, 1, proc.stderr)
                    self.assertEqual(proc.stdout, "")
                    self.assertTrue(proc.stderr.startswith("NOT_VERIFIED: "), proc.stderr)
                    self.assertIn(f"{file}: expected a JSON object", proc.stderr)
                    self.assertNotIn("Traceback", proc.stderr)

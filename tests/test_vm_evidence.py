"""Independent LAB-03D evidence audit contract tests."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "environments/vm"))
sys.path.insert(0, str(ROOT / "tests"))
from test_vm_underlay import SRC, ROUTER, DST

spec = importlib.util.spec_from_file_location(
    "verify_vm_evidence", ROOT / "environments/vm/verify_vm_evidence.py")
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


class VMEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.reports = copy.deepcopy({"src": SRC, "router": ROUTER, "dst": DST})
        self.http = {"health":{"ok": True,"status":200,"body":"healthy"},
                     "upload":{"ok": True,"status":200,"digest":"a"*64}}
        self.status = {"lab":"sre-lab03d-vbox-intnet", "scope":"3 independent VirtualBox guests",
                       "status":"PASS", "topology":"PASS", "http":"PASS",
                       "mtu_injection":"NOT_VERIFIED", "pcap":"NOT_VERIFIED"}

    def tearDown(self):
        self.temp.cleanup()

    def save(self):
        for name, data in [
            ("vm-attestations.json", self.reports),
            ("http.json", self.http),
            ("status.json", self.status),
        ]:
            (self.root / name).write_text(json.dumps(data))
        lines = [
            f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}"
            for p in sorted(self.root.iterdir()) if p.name != "SHA256SUMS"
        ]
        (self.root / "SHA256SUMS").write_text("\n".join(lines) + "\n")

    def test_independent_verifier_accepts_consistent_evidence(self):
        self.save()
        r = verifier.verify(self.root, "a"*64)
        self.assertEqual(r["files_hashed"], 3)
        self.assertEqual(r["status"], "PASS")

    def test_hash_mismatch_is_fatal(self):
        self.save()
        (self.root / "http.json").write_text("{}")
        with self.assertRaises(ValueError):
            verifier.verify(self.root, "a"*64)

    def test_tampered_http_body_rejected_even_if_hashes_updated(self):
        self.http["upload"]["digest"] = "b"*64
        self.save()
        with self.assertRaises(ValueError):
            verifier.verify(self.root, "a"*64)

    def test_duplicate_vm_identity_rejected_even_if_hashes_updated(self):
        self.reports["dst"]["machine_id"] = self.reports["src"]["machine_id"]
        self.save()
        with self.assertRaises(ValueError):
            verifier.verify(self.root, "a"*64)

    def test_cannot_claim_mtu_fault_or_pcap_was_verified(self):
        self.status["mtu_injection"] = "PASS"
        self.save()
        with self.assertRaises(ValueError):
            verifier.verify(self.root, "a"*64)

    def test_extra_unhashed_file_is_fatal(self):
        self.save()
        (self.root / "unexpected.txt").write_text("not covered")
        with self.assertRaises(ValueError):
            verifier.verify(self.root, "a"*64)

    def test_attestation_only_scope_remains_not_http_verified(self):
        self.status["http"] = "NOT_VERIFIED"
        self.http = {}
        self.save()
        r=verifier.verify(self.root, "a"*64)
        self.assertEqual(r["http"], "NOT_VERIFIED")


if __name__ == "__main__":
    unittest.main()

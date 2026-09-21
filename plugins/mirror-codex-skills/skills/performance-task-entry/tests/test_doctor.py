import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("performance_doctor", SCRIPTS / "doctor.py")
doctor = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(doctor)


class DoctorGateTests(unittest.TestCase):
    def test_dependency_failure_has_priority(self):
        self.assertEqual(doctor.gate_exit_code(["node"], False), 2)

    def test_missing_credentials_is_a_hard_failure(self):
        self.assertEqual(doctor.gate_exit_code([], False), 3)

    def test_both_gates_ready(self):
        self.assertEqual(doctor.gate_exit_code([], True), 0)

    def test_missing_vault_is_not_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            status = doctor.credential_status(Path(directory) / "missing")
        self.assertTrue(status["checked"])
        self.assertFalse(status["ready"])

    def test_automatic_cdp_diagnostic_is_not_probed_or_dialed(self):
        config = SimpleNamespace(cdp_port_is_override=False, cdp_endpoint=None)
        with patch.object(doctor, "cdp_status") as cdp_status:
            status = doctor.background_edge_status(config)
        self.assertEqual(status["mode"], "automatic")
        self.assertEqual(status["status"], "not_probed")
        self.assertFalse(status["ready"])
        cdp_status.assert_not_called()


if __name__ == "__main__":
    unittest.main()

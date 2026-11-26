import tempfile
import unittest
from pathlib import Path

import registry
from verdict import assess

MEMBERS = [{"id": "a", "name": "Rahul", "phones": ["+91 98450 12345"]}]
REAL = {"verdict": "real", "score": 0.03, "segments": []}
NO_SCRIPT = {"analysed": False, "hits": {}, "level": "none"}


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_db = registry._db
        registry.init(Path(self.tmp.name) / "t.db")

    def tearDown(self):
        registry._db.close()
        registry._db = self.original_db
        self.tmp.cleanup()

    def test_first_time_then_flagged_after_high_risk(self):
        self.assertEqual(registry.lookup("+91 70000 11111", MEMBERS)["status"], "first_time")
        registry.record("check", "+91 70000 11111", {"verdict": {"level": "high", "reasons": []}})
        info = registry.lookup("7000011111", MEMBERS)
        self.assertEqual(info["status"], "flagged")
        self.assertEqual(info["alerts"], 1)

    def test_family_number_and_international(self):
        self.assertEqual(registry.lookup("9845012345", MEMBERS)["status"], "known")
        self.assertTrue(registry.lookup("+44 7700 900123", MEMBERS)["international"])
        self.assertEqual(registry.lookup("", MEMBERS, hidden=True)["status"], "hidden")

    def test_flagged_number_is_high_even_with_real_voice(self):
        v = assess(REAL, None, NO_SCRIPT, claimed=False, rep={"status": "flagged"})
        self.assertEqual(v["level"], "high")

    def test_trusted_is_not_reflagged(self):
        registry.set_status("7000022222", "trusted")
        registry.record("check", "7000022222", {"verdict": {"level": "high", "reasons": []}})
        self.assertEqual(registry.lookup("7000022222", MEMBERS)["status"], "trusted")


if __name__ == "__main__":
    unittest.main()

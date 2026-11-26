import unittest

from scam_script import analyse
from verdict import assess


class ScriptTests(unittest.TestCase):
    def test_classic_scam_english(self):
        r = analyse("Papa I had an accident, police arrested me, send money on UPI right now, don't tell mom")
        self.assertEqual(r.level, "high")
        for cat in ("money", "urgency", "authority", "emergency", "secrecy"):
            self.assertIn(cat, r.hits)

    def test_hinglish_and_hindi(self):
        self.assertIn("money", analyse("jaldi paise bhejo").hits)
        self.assertIn("secrecy", analyse("किसी को मत बताना").hits)

    def test_kannada(self):
        r = analyse("ಬೇಗ ಹಣ ಕಳುಹಿಸು, ಯಾರಿಗೂ ಹೇಳಬೇಡ")
        self.assertIn("money", r.hits)
        self.assertIn("secrecy", r.hits)

    def test_normal_message(self):
        r = analyse("Hi amma, reached the hostel, will call you tonight")
        self.assertEqual(r.level, "none")

    def test_empty(self):
        self.assertFalse(analyse("").analysed)


REAL = {"verdict": "real", "score": 0.05, "segments": [{"start": 0, "end": 4, "score": 0.05, "verdict": "real"}]}
FAKE = {"verdict": "fake", "score": 0.93, "segments": []}
SPLICED = {"verdict": "real", "score": 0.3, "segments": [
    {"start": 0, "end": 4, "score": 0.05, "verdict": "real"},
    {"start": 2, "end": 6, "score": 0.88, "verdict": "fake"}]}
NO_SCRIPT = {"analysed": False, "hits": {}, "level": "none"}


class VerdictTests(unittest.TestCase):
    def test_fake_is_high_even_if_identity_matches(self):
        v = assess(FAKE, {"decision": "match"}, NO_SCRIPT, claimed=True)
        self.assertEqual(v["level"], "high")
        self.assertIn("dont_pay", v["next_steps"])

    def test_wrong_person_is_high(self):
        v = assess(REAL, {"decision": "no_match"}, NO_SCRIPT, claimed=True)
        self.assertEqual(v["level"], "high")

    def test_spliced_is_flagged(self):
        v = assess(SPLICED, None, NO_SCRIPT, claimed=False)
        self.assertIn("voice_spliced", v["reasons"])
        self.assertEqual(v["level"], "medium")
        self.assertEqual(v["spliced_ranges"], [[2, 6]])

    def test_real_voice_but_scam_script_is_medium(self):
        script = analyse("send money urgently, police case filed, don't tell anyone").as_dict()
        v = assess(REAL, {"decision": "match"}, script, claimed=True)
        self.assertEqual(v["level"], "medium")
        self.assertTrue(v["offer_safeword"])

    def test_all_clear_still_gives_next_step(self):
        v = assess(REAL, {"decision": "match"}, NO_SCRIPT, claimed=True)
        self.assertEqual(v["level"], "low")
        self.assertTrue(v["next_steps"])


if __name__ == "__main__":
    unittest.main()

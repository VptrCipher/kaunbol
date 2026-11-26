import unittest

import identify
from scam_script import analyse
from verdict import assess

MEMBERS = [
    {"id": "a", "name": "Rahul", "relation": "Son", "phones": ["+91 98450 12345"]},
    {"id": "b", "name": "Priya", "relation": "Daughter", "phones": []},
]
REAL = {"verdict": "real", "score": 0.03, "segments": []}
POLICE = analyse("This is police, your son is arrested, send money on UPI right now, don't tell anyone").as_dict()


class ResolveTests(unittest.TestCase):
    def test_number_picks_member(self):
        m, how = identify.resolve("", "9845012345", "", MEMBERS)
        self.assertEqual((m["id"], how), ("a", "number"))

    def test_spoken_name_picks_member(self):
        m, how = identify.resolve("", "", "papa it's me priya, I need help", MEMBERS)
        self.assertEqual((m["id"], how), ("b", "transcript"))

    def test_hand_pick_wins(self):
        m, how = identify.resolve("b", "9845012345", "", MEMBERS)
        self.assertEqual((m["id"], how), ("b", "picked"))

    def test_nothing_known(self):
        self.assertEqual(identify.resolve("", "", "hello", MEMBERS), (None, None))

    def test_new_number_trick(self):
        self.assertEqual(identify.number_status("7000000000", MEMBERS[0], MEMBERS), "mismatch")
        self.assertEqual(identify.number_status("7000000000", None, MEMBERS), "unknown")
        self.assertEqual(identify.number_status("98450 12345", MEMBERS[0], MEMBERS), "match")


class ScriptedRealVoiceTests(unittest.TestCase):
    def test_real_stranger_with_scam_script_is_high(self):
        # Fake-police / digital-arrest: real human voice, no claim verified.
        v = assess(REAL, None, POLICE, claimed=False)
        self.assertEqual(v["level"], "high")
        self.assertIn("scripted_unverified", v["reasons"])

    def test_verified_family_with_scam_script_is_medium_with_checks(self):
        v = assess(REAL, {"decision": "match"}, POLICE, claimed=True)
        self.assertEqual(v["level"], "medium")
        self.assertIn("real_but_scripted", v["reasons"])
        self.assertIn("ask_private_question", v["next_steps"])

    def test_verified_family_with_medium_script_is_not_green(self):
        medium = analyse("send 20000 on gpay quickly").as_dict()
        v = assess(REAL, {"decision": "match"}, medium, claimed=True, number="match")
        self.assertEqual(v["level"], "medium")
        self.assertIn("real_but_scripted", v["reasons"])

    def test_new_number_plus_script_is_high(self):
        medium = analyse("send money on gpay quickly").as_dict()
        self.assertEqual(medium["level"], "medium")
        v = assess(REAL, {"decision": "match"}, medium, claimed=True, number="mismatch")
        self.assertEqual(v["level"], "high")


if __name__ == "__main__":
    unittest.main()

import time
import unicodedata
import unittest

import challenge


class ChallengeTests(unittest.TestCase):
    def test_correct_phrase_passes_once(self):
        c = challenge.issue("en")
        said = "okay okay " + " ".join(c["words"])
        self.assertEqual(challenge.check(c["id"], said, consume=False)["status"], "passed")
        self.assertEqual(challenge.check(c["id"], said, consume=False)["status"], "used")

    def test_replayed_recording_fails(self):
        c = challenge.issue("en")
        r = challenge.check(c["id"], "papa please send money urgently", consume=True)
        self.assertEqual(r["status"], "not_heard")

    def test_two_of_three_words_with_stt_typo(self):
        c = challenge.issue("en")
        w = c["words"]
        said = f"{w[0][:-1]}x {w[1]}"  # one word slightly misheard, one dropped
        self.assertEqual(challenge.check(c["id"], said, consume=True)["status"], "passed")

    def test_hindi_phrase(self):
        c = challenge.issue("hi")
        self.assertEqual(challenge.check(c["id"], " ".join(c["words"]), consume=True)["status"], "passed")

    def test_kannada_phrase_preserves_vowel_marks_and_accepts_punctuation(self):
        c = challenge.issue("kn")
        challenge._active[c["id"]]["words"] = ["ಮಾವು", "ಕಿಟಕಿ", "ಹೂವು"]
        said = unicodedata.normalize("NFD", "ಮಾವು, ಕಿಟಕಿ!")
        result = challenge.check(c["id"], said, consume=True)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["heard"], ["ಮಾವು", "ಕಿಟಕಿ"])

    def test_hindi_different_vowels_do_not_satisfy_the_prompt(self):
        c = challenge.issue("hi")
        challenge._active[c["id"]]["words"] = ["नदी", "दूध", "आम"]
        result = challenge.check(c["id"], "नाद दध आम।", consume=True)
        self.assertEqual(result["status"], "not_heard")
        self.assertEqual(result["heard"], ["आम"])

    def test_one_repeated_word_cannot_satisfy_two_challenge_words(self):
        c = challenge.issue("en")
        challenge._active[c["id"]]["words"] = ["tiger", "tigers", "rocket"]
        result = challenge.check(c["id"], "tiger tiger", consume=True)
        self.assertEqual(result["status"], "not_heard")
        self.assertEqual(result["heard"], ["tiger"])

    def test_expiry(self):
        c = challenge.issue("en")
        challenge._active[c["id"]]["expires"] = time.time() - 1
        self.assertEqual(challenge.check(c["id"], " ".join(c["words"]), consume=True)["status"], "expired")


if __name__ == "__main__":
    unittest.main()

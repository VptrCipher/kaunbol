import asyncio
import json
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import numpy as np

from benchmark import plans, run, summarize
from benchmark_gen import generate, write_pcm
from detectif import DetectifError


class BenchmarkTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        self.item = {"clip": "fixture.wav", "claim": "Test speaker", "voice": "real", "same_person": True}
        self.members = [{"name": "Test speaker", "speaker_id": "fixture"}]
        self.manifest([self.item])

    def manifest(self, rows):
        (self.folder / "manifest.json").write_text(json.dumps(rows), encoding="utf-8")

    def test_budget_and_missing_inputs_do_not_contact_provider(self):
        provider = SimpleNamespace(demo=False, verify=AsyncMock())
        self.assertEqual(plans(self.folder, self.members)[0][2], "missing_audio")
        with self.assertRaisesRegex(ValueError, "benchmark_inputs_missing"):
            asyncio.run(run(self.folder, provider, self.members, max_calls=1))
        (self.folder / "fixture.wav").touch()
        with self.assertRaisesRegex(ValueError, "benchmark_budget"):
            asyncio.run(run(self.folder, provider, self.members, max_calls=0))
        provider.verify.assert_not_called()
        self.assertFalse((self.folder / "results.json").exists())

    def test_missing_verdict_is_counted_separately_and_metrics_are_by_channel(self):
        rows = [{"truth_voice": "fake", "voice": "real", "truth_same": False,
                 "decision": "match", "condition": "telephone_8khz"},
                {"truth_voice": "real", "voice": "fake", "truth_same": True,
                 "decision": "no_match", "condition": "g711_mulaw"},
                {"truth_voice": "real", "voice": None, "truth_same": True, "decision": None}]
        result = summarize(rows)
        self.assertEqual(result["by_condition"]["telephone_8khz"]["voice_false_accepts"], 1)
        for key in ("voice_false_accepts", "voice_false_rejects", "identity_false_accepts",
                    "identity_false_rejects", "voice_missing", "identity_missing"):
            self.assertEqual(result["overall"][key], 1)

    def test_benchmark_keeps_provider_failure_visible(self):
        provider = SimpleNamespace(demo=False, verify=AsyncMock(return_value={
            "deepfake": {"verdict": "real"}, "result": {}}))
        (self.folder / "fixture.wav").touch()
        result = asyncio.run(run(self.folder, provider, self.members, max_calls=1))
        self.assertEqual(result["correct"], 1)
        self.assertEqual(result["metrics"]["overall"]["identity_missing"], 1)
        self.assertIsNone(result["rows"][0]["identity_ok"])
        self.assertEqual(provider.verify.await_count, 1)

    def test_stops_provider_calls_after_rate_limit(self):
        self.manifest([self.item, self.item])
        (self.folder / "fixture.wav").touch()
        provider = SimpleNamespace(demo=False, verify=AsyncMock(side_effect=DetectifError(429, "rate_limited", "")))
        result = asyncio.run(run(self.folder, provider, self.members, max_calls=2))
        self.assertEqual(result["calls"], 1)
        self.assertEqual(result["rows"][1]["error"], "skipped_after_provider_stop")
        self.assertEqual(result["metrics"]["overall"]["voice_missing"], 2)

    def test_generates_three_pcm_variants_without_ffmpeg(self):
        path = self.folder / self.item["clip"]
        sample_rate = 16000
        t = np.arange(5 * sample_rate) / sample_rate
        write_pcm(path, np.sin(t * 2 * np.pi * 440) * 5000, sample_rate)
        self.assertEqual(generate(self.folder, None), (3, []))
        manifest = json.loads((self.folder / "manifest.json").read_text(encoding="utf-8"))
        for item in manifest[1:]:
            with wave.open(str(self.folder / item["clip"]), "rb") as audio:
                rate = audio.getframerate()
                self.assertEqual(rate, 16000 if item["condition"] == "noise_10db" else 8000)
                self.assertEqual(audio.getnframes(), rate * 5)
                self.assertEqual(audio.getnchannels(), 1)
        self.assertEqual(generate(self.folder, None), (0, []))

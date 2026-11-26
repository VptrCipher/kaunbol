"""Generate channel variants from consented PCM WAV files already labelled in the benchmark."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np
from scipy.signal import resample_poly

from benchmark import ROOT, load_manifest


def read_pcm(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as source:
        rate = source.getframerate()
        if source.getsampwidth() != 2 or not 4 <= source.getnframes() / rate <= 120:
            raise ValueError(f"{path.name}: supply a 4–120 second, 16-bit PCM WAV")
        values = np.frombuffer(source.readframes(source.getnframes()), dtype="<i2")
        return values.reshape(-1, source.getnchannels()).mean(axis=1), rate


def write_pcm(path: Path, signal: np.ndarray, rate: int) -> None:
    with wave.open(str(path), "wb") as output:
        output.setparams((1, 2, rate, 0, "NONE", ""))
        output.writeframes(np.rint(signal).clip(-32768, 32767).astype("<i2").tobytes())


def mulaw_round_trip(signal: np.ndarray) -> np.ndarray:
    """Apply 8-bit G.711 μ-law quantization, then decode to PCM for WAV transport."""
    pcm = np.rint(signal).clip(-32768, 32767).astype(np.int32)
    magnitude = np.minimum(np.abs(pcm), 32635) + 132
    segments = np.searchsorted([255, 511, 1023, 2047, 4095, 8191, 16383], magnitude)
    mantissa = (magnitude >> (segments + 3)) & 15
    decoded = (((mantissa << 3) + 132) << segments) - 132
    return np.where(pcm < 0, -decoded, decoded)


def generate(folder: Path, ffmpeg: str | None) -> tuple[int, list[str]]:
    manifest = load_manifest(folder)
    sources = [item for item in manifest if item.get("condition", "clean") == "clean"]
    existing = {(item["clip"], item.get("claim"), item.get("same_person")) for item in manifest}
    variants: dict[str, dict[str, str]] = {}
    missing = set()
    generated = 0
    for item in sources:
        name = item["clip"]
        if Path(name).name != name or "/" in name or "\\" in name:
            raise ValueError("Source clip must be a filename inside the benchmark folder.")
        path = folder / name
        if not path.is_file():
            missing.add(name)
            continue
        if name not in variants:
            signal, rate = read_pcm(path)
            divisor = math.gcd(8000, rate)
            phone = resample_poly(signal, 8000 // divisor, rate // divisor)
            digest = hashlib.sha256(name.encode()).digest()
            rng = np.random.default_rng(int.from_bytes(digest[:8], "big"))
            noise = rng.normal(size=len(signal))
            noise *= np.sqrt(np.mean(signal ** 2) / (10 * np.mean(noise ** 2)))
            channels = {"telephone_8khz": (phone, 8000),
                        "g711_mulaw": (mulaw_round_trip(phone), 8000),
                        "noise_10db": (signal + noise, rate)}
            clips = {}
            for label, (audio, output_rate) in channels.items():
                clip = f"{path.stem}__{label}.wav"
                write_pcm(folder / clip, audio, output_rate)
                clips[label] = clip
            if ffmpeg:
                clip = f"{path.stem}__opus_16kbps.ogg"
                subprocess.run([ffmpeg, "-nostdin", "-loglevel", "error", "-y", "-i", str(path),
                                "-ac", "1", "-c:a", "libopus", "-b:a", "16k", str(folder / clip)], check=True)
                clips["opus_16kbps"] = clip
            variants[name] = clips
        for condition, clip in variants[name].items():
            key = (clip, item.get("claim"), item.get("same_person"))
            if key not in existing:
                manifest.append({**item, "clip": clip, "condition": condition, "source_clip": name})
                existing.add(key)
                generated += 1
    if generated:
        path = folder / "manifest.json"
        staged = path.with_suffix(".json.tmp")
        staged.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        staged.replace(path)
    return generated, sorted(missing)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder", type=Path, default=ROOT / "data" / "benchmark")
    parser.add_argument("--ffmpeg", default=shutil.which("ffmpeg"))
    args = parser.parse_args()
    count, missing = generate(args.folder, args.ffmpeg)
    print(f"Added {count} case(s).")
    if not args.ffmpeg:
        print("FFmpeg with libopus is needed to generate the Opus condition.")
    if missing:
        print("Missing source WAVs: " + ", ".join(missing))
    raise SystemExit(bool(missing))

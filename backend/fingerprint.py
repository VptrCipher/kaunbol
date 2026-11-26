"""Free, local voice fingerprint used only to SHORTLIST who might be speaking.

It is deliberately simple (MFCC statistics, numpy only) and is never the final
word: the top candidate is then confirmed with one Eidos /v1/speakers/verify
call. That keeps auto-detection to a single paid check, however many family
voices are stored.
"""
from __future__ import annotations

import io
import shutil
import subprocess
import wave

import numpy as np

SR = 16000


def _decode(audio: bytes) -> np.ndarray | None:
    """Return mono float32 at 16 kHz. WAV is read directly; other formats need ffmpeg."""
    try:
        with wave.open(io.BytesIO(audio)) as w:
            if w.getsampwidth() == 2:
                x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
                ch, sr = w.getnchannels(), w.getframerate()
                if ch > 1:
                    x = x.reshape(-1, ch).mean(axis=1)
                if sr != SR:
                    from scipy.signal import resample_poly
                    g = np.gcd(sr, SR)
                    x = resample_poly(x, SR // g, sr // g).astype(np.float32)
                return x
    except (wave.Error, EOFError):
        pass
    ff = shutil.which("ffmpeg")
    if not ff:
        return None
    try:
        out = subprocess.run([ff, "-v", "error", "-i", "pipe:0", "-ac", "1", "-ar", str(SR), "-f", "s16le", "pipe:1"],
                             input=audio, capture_output=True, timeout=30, check=True).stdout
        return np.frombuffer(out, dtype=np.int16).astype(np.float32) / 32768
    except Exception:
        return None


def _mel_filters(n_fft: int, n_mels: int = 40) -> np.ndarray:
    def hz2mel(f): return 2595 * np.log10(1 + f / 700)
    def mel2hz(m): return 700 * (10 ** (m / 2595) - 1)
    pts = mel2hz(np.linspace(hz2mel(60), hz2mel(7600), n_mels + 2))
    bins = np.floor((n_fft + 1) * pts / SR).astype(int)
    fb = np.zeros((n_mels, n_fft // 2 + 1))
    for i in range(1, n_mels + 1):
        lo, c, hi = bins[i - 1], bins[i], bins[i + 1]
        if c > lo:
            fb[i - 1, lo:c] = (np.arange(lo, c) - lo) / (c - lo)
        if hi > c:
            fb[i - 1, c:hi] = (hi - np.arange(c, hi)) / (hi - c)
    return fb


_FB = _mel_filters(512)
_DCT = np.cos(np.pi / 40 * (np.arange(40)[None, :] + 0.5) * np.arange(1, 21)[:, None])  # 20 x 40, skip c0


def embed(audio: bytes) -> list[float] | None:
    x = _decode(audio)
    if x is None or len(x) < SR * 2:
        return None
    x = np.append(x[0], x[1:] - 0.97 * x[:-1])
    win, hop = 400, 160
    n = 1 + (len(x) - win) // hop
    frames = np.lib.stride_tricks.as_strided(x, (n, win), (x.strides[0] * hop, x.strides[0])) * np.hamming(win)
    energy = (frames ** 2).sum(axis=1)
    keep = energy > np.percentile(energy, 40)  # speech frames only, drop silence
    if keep.sum() < 50:
        return None
    spec = np.abs(np.fft.rfft(frames[keep], 512)) ** 2
    mfcc = np.log(spec @ _FB.T + 1e-10) @ _DCT.T
    v = np.concatenate([mfcc.mean(axis=0), mfcc.std(axis=0)])
    return v.astype(float).tolist()


def similarity(a: list[float], b: list[float]) -> float:
    a, b = np.asarray(a), np.asarray(b)
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))


def rank(probe: list[float], members: list[dict]) -> list[tuple[dict, float]]:
    """Members sorted by best similarity of any stored sample to the probe."""
    scored = []
    for m in members:
        prints = m.get("prints") or []
        if prints:
            scored.append((m, max(similarity(probe, p) for p in prints)))
    return sorted(scored, key=lambda t: t[1], reverse=True)

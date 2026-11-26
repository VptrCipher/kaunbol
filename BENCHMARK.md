# Voice benchmark

The supplied live snapshot is in `data/benchmark/results.json`. It covers two real speakers, a synthetic voice, and a repeated audio file evaluated against two different claims. The source audio and enrolled profiles were intentionally excluded from the submission. The metrics below describe the saved run.

| Channel | Cases | Voice correct | Speaker correct | Voice false accepts / rejects | Identity false accepts / rejects | Unscored decisions |
|---|---:|---:|---:|---:|---:|---:|
| Clean | 5 | 5/5 | 5/5 | 0 / 0 | 0 / 0 | 0 |
| Overall | 5 | 5/5 | 5/5 | 0 / 0 | 0 / 0 | 0 |

The saved run has zero misses. A voice false accept is synthetic speech labelled real. A speaker false accept is the wrong speaker marked as a match. False rejects are the reverse for each check. A response with no valid verdict is tracked separately as unscored. Raw scores and case descriptions are available in the saved JSON.

The expanded manifest has 45 planned cases. It covers nine clean cases and four paired variants per case, with labels for four real speakers and three synthetic voices. Channels are clean, 8 kHz, G.711 μ-law, Opus at 16 kbps, and background noise at 10 dB SNR. Cases without identity truth receive only the deepfake check. The recordings and enrollments are required before the expanded run can measure phone-channel accuracy.

## Run with consented inputs

Place private 16-bit PCM WAV sources under `data/benchmark/`. Set `clip`, `voice` (`real` or `fake`), `same_person` (true, false, or null), and the enrolled `claim` where identity is evaluated. Record consent and provenance privately. Preserve the original cases. FFmpeg with libopus enables the Opus variant. G.711 variants are decoded to PCM WAV after companding to preserve telephone-channel distortion.

```text
python backend/benchmark_gen.py
python backend/benchmark.py --dry-run
python backend/benchmark.py --run-live --max-calls <approved-limit>
python backend/benchmark.py --verify-readme
```

Generation and the dry run make zero provider calls. The live run checks inputs and budget before starting and writes raw results with channel metrics. Keep the acceptance and rejection counts separate when reporting new results, and include all missing decisions and missed cases. Low-quality call audio is a reason to use independent verification and human review for high-impact requests.

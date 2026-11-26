"""Plan and run a labelled, consented DetectifAI evaluation with a per-run call cap."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import tempfile
import time
from pathlib import Path

from detectif import DetectifError

ROOT = Path(__file__).resolve().parent.parent
READ_ME_START = "<!-- benchmark-summary:start -->"
READ_ME_END = "<!-- benchmark-summary:end -->"
VOICE_LABELS = ("real", "fake")
IDENTITY_LABELS = ("match", "no_match")
STOP_STATUSES = {401, 402, 403, 429}


def load_manifest(folder: Path) -> list[dict]:
    p = folder / "manifest.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def load_results(folder: Path, name: str = "results.json") -> dict | None:
    p = folder / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def plans(folder: Path, members: list[dict]) -> list[tuple[dict, dict | None, str | None]]:
    rows = []
    enrolled = {m["name"]: m for m in members}
    for item in load_manifest(folder):
        filename = item["clip"]
        member = enrolled.get(item.get("claim")) if item.get("same_person") is not None else None
        error = None
        if Path(filename).name != filename or "/" in filename or "\\" in filename:
            error = "invalid_clip_name"
        elif item["voice"] not in VOICE_LABELS:
            error = "invalid_voice_label"
        elif not (folder / filename).is_file():
            error = "missing_audio"
        elif item.get("same_person") is not None and member is None:
            error = "missing_enrollment"
        rows.append((item, member, error))
    return rows


def summarize(rows: list[dict]) -> dict:
    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(row.get("condition", "clean"), []).append(row)

    def counts(group):
        voice = [r for r in group if r.get("voice") in VOICE_LABELS]
        identity = [r for r in group if r.get("truth_same") is not None and r.get("decision") in IDENTITY_LABELS]
        return {
            "cases": len(group),
            "voice_correct": sum(r["voice"] == r["truth_voice"] for r in voice),
            "voice_scored": len(voice),
            "voice_missing": len(group) - len(voice),
            "identity_correct": sum((r["decision"] == "match") == r["truth_same"] for r in identity),
            "identity_scored": len(identity),
            "identity_missing": sum(r.get("truth_same") is not None for r in group) - len(identity),
            "voice_false_accepts": sum(r["truth_voice"] == "fake" and r["voice"] == "real" for r in voice),
            "voice_false_rejects": sum(r["truth_voice"] == "real" and r["voice"] == "fake" for r in voice),
            "identity_false_accepts": sum(r["truth_same"] is False and r["decision"] == "match" for r in identity),
            "identity_false_rejects": sum(r["truth_same"] is True and r["decision"] == "no_match" for r in identity),
        }

    return {"overall": counts(rows), "by_condition": {name: counts(group) for name, group in groups.items()}}


def readme_summary(results: dict | None) -> str:
    if not results or results.get("simulated"):
        return "There are no saved live benchmark results."
    rows = results["rows"]
    overall = summarize(rows)["overall"]
    return (
        f"The bundled live snapshot records {overall['voice_correct']}/{overall['voice_scored']} voice "
        f"decisions and {overall['identity_correct']}/{overall['identity_scored']} speaker decisions "
        f"across {len(rows)} labelled cases. See [BENCHMARK.md](BENCHMARK.md) for the conditions and limitations."
    )


async def run(folder: Path, client, members: list[dict], name: str = "results.json", *, max_calls: int) -> dict:
    work = plans(folder, members)
    if not work or any(error for _, _, error in work):
        raise ValueError("benchmark_inputs_missing")
    if len(work) > max_calls:
        raise ValueError("benchmark_budget")
    rows = []
    calls = 0
    stopped = False
    for item, member, _ in work:
        row = {"clip": item["clip"], "claim": item.get("claim"), "note": item.get("note", ""),
               "condition": item.get("condition", "clean"), "truth_voice": item["voice"],
               "truth_same": item.get("same_person"), "simulated": client.demo}
        try:
            if stopped:
                row["error"] = "skipped_after_provider_stop"
            else:
                audio = (folder / item["clip"]).read_bytes()
                scenario = "cloned" if item["voice"] == "fake" else (
                    "wrong_person" if item.get("same_person") is False else "genuine"
                )
                calls += 1
                if member:
                    res = await client.verify(audio, item["clip"], member["speaker_id"], scenario)
                    df, identity = res.get("deepfake") or {}, res.get("result") or {}
                    row.update(voice=df.get("verdict"), score=df.get("score"),
                               decision=identity.get("decision"), similarity=identity.get("similarity"))
                else:
                    res = await client.detect(audio, item["clip"], scenario)
                    df = res.get("result") or {}
                    row.update(voice=df.get("verdict"), score=df.get("score"))
                row["simulated"] = client.demo or bool(res.get("simulated"))
        except DetectifError as error:
            row["error"] = error.code
            stopped = error.status in STOP_STATUSES
        except Exception as error:
            row["error"] = getattr(error, "code", type(error).__name__)
        row["voice_ok"] = row["voice"] == row["truth_voice"] if row.get("voice") in VOICE_LABELS else None
        row["identity_ok"] = ((row["decision"] == "match") == row["truth_same"]
                              if row["truth_same"] is not None and row.get("decision") in IDENTITY_LABELS else None)
        rows.append(row)
    metrics = summarize(rows)
    overall = metrics["overall"]
    result = {"at": int(time.time()), "rows": rows, "metrics": metrics, "calls": calls,
              "correct": overall["voice_correct"] + overall["identity_correct"],
              "total": overall["voice_scored"] + overall["identity_scored"],
              "simulated": client.demo or any(r["simulated"] for r in rows)}
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=folder, delete=False) as output:
        path = Path(output.name)
        json.dump(result, output, indent=2, ensure_ascii=False)
    try:
        path.replace(folder / name)
    finally:
        path.unlink(missing_ok=True)
    return result


def main() -> int:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    from detectif import DetectifClient

    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--run-live", action="store_true")
    mode.add_argument("--verify-readme", action="store_true")
    parser.add_argument("--max-calls", type=int)
    data = Path(os.getenv("KAUNBOL_DATA", ROOT / "data"))
    parser.add_argument("--data", type=Path, default=data)
    args = parser.parse_args()
    folder = args.data / "benchmark"
    if args.verify_readme:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        _, first, tail = readme.partition(READ_ME_START)
        summary, last, _ = tail.partition(READ_ME_END)
        ok = bool(first and last and summary.strip() == readme_summary(load_results(folder)))
        print("PASS: README matches the saved live snapshot" if ok else "FAIL: README benchmark summary differs")
        return 0 if ok else 1

    member_file = args.data / "family.json"
    members = json.loads(member_file.read_text(encoding="utf-8"))["members"] if member_file.exists() else []
    members = [m for m in members if not m.get("simulated")]
    work = plans(folder, members)
    ready = sum(error is None for _, _, error in work)
    checks = sum(item.get("same_person") is not None for item, _, error in work if error is None)
    print(f"{len(work)} case(s), {ready} ready, {len(work) - ready} blocked")
    print(f"{ready - checks} Aletheia calls + {checks} Eidos calls with Aletheia included = {ready} ready API calls")
    for item, _, error in work:
        if error:
            print(f"Blocked: {item['clip']} ({error})")
    if args.dry_run:
        return 0
    client = DetectifClient(os.getenv("DETECTIF_API_KEY"),
                            demo=os.getenv("DEMO_MODE", "").lower() in ("1", "true", "yes"))
    if not args.max_calls or args.max_calls < 1 or client.demo or not work or ready != len(work):
        print("Live run requires a key, live mode, complete inputs and a positive --max-calls budget.")
        return 1
    if ready > args.max_calls:
        print(f"Live run exceeds the budget of {args.max_calls} API calls.")
        return 1
    output = asyncio.run(run(folder, client, members, max_calls=args.max_calls))
    print(json.dumps(output["metrics"], indent=2))
    return 0 if all(r["voice_ok"] is not None and (r["truth_same"] is None or r["identity_ok"] is not None)
                    for r in output["rows"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())

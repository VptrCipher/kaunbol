"""Caller-number memory (SQLite).

Every number that calls is remembered. Enrolled people's numbers are trusted;
a number behind a High-risk result is flagged automatically, with the evidence.
Next time it calls, the warning shows before the caller says a word.

Also stores every check and live call as an incident, for the call log,
CSV export and the evidence report people can file with 1930 / cybercrime.gov.in.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from identify import norm_phone

_lock = threading.Lock()
_db: sqlite3.Connection | None = None


def init(path: Path) -> None:
    global _db
    _db = sqlite3.connect(path, check_same_thread=False)
    _db.row_factory = sqlite3.Row
    _db.executescript("""
    CREATE TABLE IF NOT EXISTS otp_reservations (
        target TEXT PRIMARY KEY,
        valid_until INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS lost_devices (
        member_id TEXT PRIMARY KEY,
        reported_at INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS numbers (
        number TEXT PRIMARY KEY,         -- last 10 digits
        display TEXT,                    -- as first seen, e.g. +91 98450 12345
        status TEXT DEFAULT 'seen',      -- seen | trusted | flagged
        note TEXT,
        first_seen INTEGER, last_seen INTEGER,
        calls INTEGER DEFAULT 0, alerts INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS incidents (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        at INTEGER, kind TEXT, number TEXT, display TEXT, claimed TEXT, identified_by TEXT,
        level TEXT, reasons TEXT, voice_verdict TEXT, voice_score REAL,
        identity TEXT, similarity REAL, script_score INTEGER, script_hits TEXT,
        transcript TEXT, replay TEXT, simulated INTEGER, operation_id TEXT,
        bot_events TEXT NOT NULL DEFAULT '[]'
    );
    """)
    if "bot_events" not in {r["name"] for r in _db.execute("PRAGMA table_info(incidents)")}:
        _db.execute("ALTER TABLE incidents ADD COLUMN bot_events TEXT NOT NULL DEFAULT '[]'")
    _db.commit()


def device_reported(member_id: str) -> bool:
    with _lock:
        return _db.execute("SELECT 1 FROM lost_devices WHERE member_id=?", (member_id,)).fetchone() is not None


def report_lost_device(member_id: str) -> None:
    with _lock, _db:
        _db.execute("INSERT OR IGNORE INTO lost_devices VALUES (?, ?)", (member_id, int(time.time())))


def reserve_otp(target: str) -> bool:
    now = int(time.time())
    with _lock, _db:
        _db.execute("DELETE FROM otp_reservations WHERE valid_until <= ?", (now,))
        return _db.execute("INSERT OR IGNORE INTO otp_reservations VALUES (?, ?)", (target, now + 620)).rowcount == 1


def clear_otp_reservation(target: str) -> None:
    with _lock, _db:
        _db.execute("DELETE FROM otp_reservations WHERE target=?", (target,))


def is_international(raw: str) -> bool:
    r = (raw or "").strip().replace(" ", "")
    if r.startswith("00"):
        r = "+" + r[2:]
    return r.startswith("+") and not r.startswith("+91")


def lookup(raw: str, members: list[dict], hidden: bool = False) -> dict:
    """What we already know about this number, before anyone speaks."""
    if hidden or not norm_phone(raw):
        return {"status": "hidden"}
    n = norm_phone(raw)
    owner = next((m for m in members if any(norm_phone(p) == n for p in m.get("phones", []))), None)
    with _lock:
        row = _db.execute("SELECT * FROM numbers WHERE number=?", (n,)).fetchone()
        last_alert = _db.execute(
            "SELECT at, claimed, level, reasons FROM incidents WHERE number=? AND level='high' ORDER BY at DESC LIMIT 1",
            (n,)).fetchone()
    info = {"number": n, "international": is_international(raw),
            "calls": row["calls"] if row else 0, "alerts": row["alerts"] if row else 0,
            "first_seen": row["first_seen"] if row else None, "note": row["note"] if row else None}
    if row and row["status"] == "flagged":
        info["status"] = "flagged"
    elif owner:
        info["status"] = "known"
        info["owner"] = owner["name"]
    elif row and row["status"] == "trusted":
        info["status"] = "trusted"
    elif row and row["calls"] > 0:
        info["status"] = "seen"
    else:
        info["status"] = "first_time"
    if last_alert:
        info["last_alert"] = {"at": last_alert["at"], "claimed": last_alert["claimed"],
                              "reasons": json.loads(last_alert["reasons"] or "[]")}
    return info


def set_status(raw: str, status: str, note: str = "") -> dict:
    n = norm_phone(raw)
    now = int(time.time())
    with _lock:
        _db.execute("""INSERT INTO numbers(number, display, status, note, first_seen, last_seen)
                       VALUES(?,?,?,?,?,?)
                       ON CONFLICT(number) DO UPDATE SET status=excluded.status, note=excluded.note""",
                    (n, raw.strip(), status, note[:200], now, now))
        _db.commit()
    return {"number": n, "status": status}


def record(kind: str, raw: str, out: dict, transcript: str = "", replay: str | None = None) -> int:
    """Save one check / live call. A High-risk result flags the number automatically."""
    n = norm_phone(raw) or None
    v = out.get("verdict") or {}
    df = out.get("deepfake") or {}
    idn = out.get("identity") or {}
    sc = out.get("script") or {}
    now = int(time.time())
    with _lock:
        cur = _db.execute("""INSERT INTO incidents(at, kind, number, display, claimed, identified_by, level, reasons,
                voice_verdict, voice_score, identity, similarity, script_score, script_hits, transcript, replay,
                simulated, operation_id, bot_events) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (now, kind, n, (raw or "").strip() or None, (out.get("claimed") or {}).get("name"), out.get("identified_by"),
             v.get("level"), json.dumps(v.get("reasons", [])), df.get("verdict"), df.get("score"),
             idn.get("decision"), idn.get("similarity"), sc.get("score"), json.dumps(sc.get("hits", {}), ensure_ascii=False),
             (transcript or "")[:5000], replay, int(bool(out.get("simulated"))), out.get("operation_id"),
             json.dumps(out.get("bot_events", []))))
        if n:
            high = v.get("level") == "high"
            _db.execute("""INSERT INTO numbers(number, display, status, first_seen, last_seen, calls, alerts)
                           VALUES(?,?,?,?,?,1,?)
                           ON CONFLICT(number) DO UPDATE SET last_seen=excluded.last_seen, calls=calls+1,
                             alerts=alerts+excluded.alerts,
                             status=CASE WHEN excluded.alerts>0 AND status!='trusted' THEN 'flagged' ELSE status END""",
                        (n, raw.strip(), "flagged" if high else "seen", now, now, int(high)))
        _db.commit()
        return cur.lastrowid


def numbers(limit: int = 200) -> list[dict]:
    with _lock:
        rows = _db.execute("SELECT * FROM numbers ORDER BY (status='flagged') DESC, last_seen DESC LIMIT ?",
                           (limit,)).fetchall()
    return [dict(r) for r in rows]


def incidents(limit: int = 500) -> list[dict]:
    with _lock:
        rows = _db.execute("SELECT * FROM incidents ORDER BY at DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def incident(iid: int) -> dict | None:
    with _lock:
        r = _db.execute("SELECT * FROM incidents WHERE id=?", (iid,)).fetchone()
    return dict(r) if r else None


def set_replay(iid: int, result: str) -> None:
    with _lock:
        _db.execute("UPDATE incidents SET replay=? WHERE id=?", (result, iid))
        _db.commit()

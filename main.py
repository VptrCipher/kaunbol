"""Repository-root ASGI entrypoint for Vercel and local deployment checks."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
# Backend modules currently use sibling imports, as with uvicorn --app-dir backend.
sys.path.insert(0, str(ROOT / "backend"))

if os.getenv("VERCEL") == "1":
    # A Vercel source bundle is read-only. This is temporary rehearsal storage,
    # not durable storage or shared session state between function instances.
    os.environ.setdefault("KAUNBOL_DATA", str(Path(tempfile.gettempdir()) / "kaunbol"))
    os.environ.setdefault("DEMO_MODE", "1")

from app import app  # noqa: E402,F401

"""
Shared fixtures for AI DJ backend tests.

`main.py` refuses to import without GEMINI_API_KEY and mounts the frontend
folder, so we set a dummy key and import it once here. No network or Gemini
calls are made — the tool handlers are exercised with a fake WebSocket.
"""

import os
import sys
import json
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

os.environ.setdefault("GEMINI_API_KEY", "test-key-not-used")
os.environ.setdefault("MUSIC_DIR", str(BACKEND_DIR / "tests" / "_no_music_dir"))

import main  # noqa: E402  (after env setup on purpose)

pl_manager = main.pl_manager  # same module object main.py uses


LIBRARY = [
    {"id": "t_billie", "title": "Billie Jean", "artist": "Michael Jackson", "album": "Thriller", "genre": "Pop", "filename": "mj/billie.mp3"},
    {"id": "t_thriller", "title": "Thriller", "artist": "Michael Jackson", "album": "Thriller", "genre": "Pop", "filename": "mj/thriller.mp3"},
    {"id": "t_beatit", "title": "Beat It", "artist": "Michael Jackson", "album": "Thriller", "genre": "Rock", "filename": "mj/beatit.mp3"},
    {"id": "t_summer", "title": "Summer of '69", "artist": "Bryan Adams", "album": "Reckless", "genre": "Rock", "filename": "ba/summer.mp3"},
    {"id": "t_heaven", "title": "Heaven", "artist": "Bryan Adams", "album": "Reckless", "genre": "Rock", "filename": "ba/heaven.mp3"},
    {"id": "t_weather", "title": "Weather With You", "artist": "Crowded House", "album": "Woodface", "genre": "Chill", "filename": "ch/weather.mp3"},
    {"id": "t_dontdream", "title": "Don't Dream It's Over", "artist": "Crowded House", "album": "Crowded House", "genre": "Chill", "filename": "ch/dream.mp3"},
    {"id": "t_onemore", "title": "One More Time", "artist": "Daft Punk", "album": "Discovery", "genre": "Dance", "filename": "dp/onemore.mp3"},
]


class FakeWS:
    """Records everything the server pushes to the browser."""

    def __init__(self):
        self.sent: list[dict] = []

    async def send_text(self, text: str):
        self.sent.append(json.loads(text))

    async def send_bytes(self, data: bytes):
        self.sent.append({"type": "_bytes", "len": len(data)})


def make_fc(tool_name: str, /, **args):
    """Mimic a google.genai FunctionCall object (positional-only tool name so
    tools with a `name` argument, e.g. createPlaylist, can be called)."""
    return SimpleNamespace(name=tool_name, args=args, id=f"call_{tool_name}")


@pytest.fixture
def library(monkeypatch):
    """Point main.py's global library at a small, known catalogue."""
    by_id = {t["id"]: t for t in LIBRARY}
    monkeypatch.setattr(main, "music_library", list(LIBRARY))
    monkeypatch.setattr(main, "tracks_by_id", by_id)
    monkeypatch.setattr(main, "scan_complete", True)
    return by_id


@pytest.fixture
def playlists_file(tmp_path, monkeypatch):
    """Redirect persistent playlist storage to a temp file."""
    f = tmp_path / "playlists.json"
    monkeypatch.setattr(pl_manager, "PLAYLISTS_FILE", f)
    return f


@pytest.fixture
def conn(library, playlists_file):
    """A ClientConn with a fake socket; nothing playing by default."""
    c = main.ClientConn(FakeWS())
    return c


@pytest.fixture
def run():
    """Run a coroutine to completion (keeps test bodies synchronous)."""
    def _run(coro):
        return asyncio.run(coro)
    return _run


def commands(conn, action=None):
    msgs = [m for m in conn.ws.sent if m.get("type") == "command"]
    if action:
        msgs = [m for m in msgs if m.get("action") == action]
    return msgs

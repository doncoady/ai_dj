"""
Start-up performance: the on-disk tag cache (scancache.py), scan progress,
the /api/status + /api/tracks 503 contract the browser's loading message
relies on, and the rescan endpoint. No user files are touched.
"""

import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main
from conftest import write_mp3, scancache, audiobook_manager


def _music_tree(root: Path, n: int = 6) -> Path:
    for i in range(n):
        write_mp3(root / f"Artist {i % 2}" / f"{i:02d} Song.mp3", title=f"Song {i}", artist=f"Artist {i % 2}", album="Album", genre="Pop")
    return root


# ─── TagCache ─────────────────────────────────────────────────────────────────

def test_tag_cache_avoids_rereading_unchanged_files(tmp_path):
    root = _music_tree(tmp_path / "m")
    files = sorted(root.rglob("*.mp3"))
    calls = []

    def read(path):
        calls.append(path.name)
        return {"title": path.stem}

    cache = scancache.TagCache("unit")
    for f in files:
        assert cache.get(f, f.name, read) == {"title": f.stem}
    assert len(calls) == 6 and cache.misses == 6 and cache.hits == 0
    assert cache.save() and scancache.cache_path("unit").exists()

    calls.clear()
    cache2 = scancache.TagCache("unit")
    for f in files:
        assert cache2.get(f, f.name, read) == {"title": f.stem}
    assert calls == [] and cache2.hits == 6 and cache2.misses == 0
    assert cache2.stats == "6 cached, 0 read"

    # A changed file (size or mtime) is re-read; a vanished one is dropped on save
    changed = files[0]
    changed.write_bytes(changed.read_bytes() + b"\x00" * 10)
    os.utime(changed, (time.time() + 5, time.time() + 5))
    files[1].unlink()
    cache3 = scancache.TagCache("unit")
    remaining = [f for f in files if f.exists()]
    for f in remaining:
        cache3.get(f, f.name, read)
    assert calls == [changed.name] and cache3.hits == 4 and cache3.misses == 1
    assert cache3.save()
    assert set(scancache.load("unit")) == {f.name for f in remaining}


def test_tag_cache_ignores_corrupt_or_foreign_files(tmp_path):
    scancache.cache_path("bad").write_text("{not json", encoding="utf-8")
    assert scancache.load("bad") == {}
    scancache.cache_path("old").write_text('{"version": 0, "files": {"a": {}}}', encoding="utf-8")
    assert scancache.load("old") == {}
    assert scancache.clear("old") is True and scancache.clear("old") is False


# ─── music scanner with cache + progress ──────────────────────────────────────

def test_scan_music_library_uses_cache_and_reports_progress(tmp_path, monkeypatch):
    root = _music_tree(tmp_path / "Music", n=5)
    monkeypatch.setattr(main, "MUSIC_DIR", root)
    reads = []
    real = main._get_metadata

    def counting(path):
        reads.append(path.name)
        return real(path)
    monkeypatch.setattr(main, "_get_metadata", counting)

    progress = {}
    tracks = main.scan_music_library(progress)
    assert len(tracks) == 5 and tracks[0]["title"] == "Song 0" and tracks[0]["artist"] == "Artist 0"
    assert progress["phase"] == "done" and progress["found"] == 5 and progress["read"] == 5 and progress["cached"] == 0
    assert progress["startedAt"] and progress["finishedAt"] and len(reads) == 5

    reads.clear()
    again = main.scan_music_library(progress)
    assert again == tracks                                      # identical records (ids included)
    assert reads == [] and progress["cached"] == 5              # nothing re-read

    write_mp3(root / "New Song.mp3", title="Brand New", artist="Someone")
    reads.clear()
    third = main.scan_music_library(progress)
    assert len(third) == 6 and reads == ["New Song.mp3"] and progress["cached"] == 5

    assert main.scan_music_library({}, use_cache=False) == third   # cache is an optimisation only


def test_scan_missing_directory_is_done_immediately(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "MUSIC_DIR", tmp_path / "nope")
    progress = {}
    assert main.scan_music_library(progress) == []
    assert progress["phase"] == "done"


def test_audiobook_scanner_uses_the_cache(audiobook_library, monkeypatch):
    root = audiobook_library["root"]
    reads = []

    def counting(path):
        reads.append(path.name)
        return audiobook_manager.read_tags(path)

    books = audiobook_manager.scan_audiobooks(root, read_tags_fn=counting)     # warm (the fixture already cached it)
    assert reads == [] and [b["title"] for b in books] == ["Dune", "The Hobbit", "Short Story"]
    books_nocache = audiobook_manager.scan_audiobooks(root, read_tags_fn=counting, use_cache=False)
    assert len(reads) == 7 and books_nocache == books


# ─── HTTP: status, 503 while scanning, rescan ─────────────────────────────────

@pytest.fixture
def client(library, playlists_file):
    return TestClient(main.app)


def test_status_and_tracks_while_scanning(client, monkeypatch):
    monkeypatch.setattr(main, "scan_complete", False)
    monkeypatch.setattr(main, "audiobooks_scan_complete", False)
    main.scan_progress.update({"phase": "reading", "found": 1200, "read": 340, "startedAt": time.time() - 12, "finishedAt": None, "error": None})
    st = client.get("/api/status").json()
    assert st["scanning"] is True and st["phase"] == "reading" and st["found"] == 1200 and st["read"] == 340
    assert st["tracks"] is None and st["audiobooksScanning"] is True and 11 <= st["elapsed"] <= 14
    r = client.get("/api/tracks")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "scanning" and body["tracks"] == [] and body["read"] == 340 and body["found"] == 1200
    assert client.post("/api/tracks/rescan").status_code == 409           # one scan at a time

    monkeypatch.setattr(main, "scan_complete", True)
    monkeypatch.setattr(main, "audiobooks_scan_complete", True)
    st = client.get("/api/status").json()
    assert st["scanning"] is False and st["tracks"] == 8 and st["elapsed"] is None
    assert len(client.get("/api/tracks").json()) == 8


def test_rescan_endpoint_picks_up_new_files(client, tmp_path, monkeypatch):
    root = _music_tree(tmp_path / "Music", n=2)
    monkeypatch.setattr(main, "MUSIC_DIR", root)
    r = client.post("/api/tracks/rescan")
    assert r.status_code == 200 and r.json()["count"] == 2
    assert len(client.get("/api/tracks").json()) == 2
    write_mp3(root / "03 Song.mp3", title="Song 3")
    assert client.post("/api/tracks/rescan").json()["count"] == 3
    assert main.scan_complete is True

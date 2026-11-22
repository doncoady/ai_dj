"""
Local audiobooks: folder/tag scanner, listening progress, spoken chapter
resolution, the HTTP API (listing, Range streaming, cover art, progress) and
the voice tools (playAudiobook / listAudiobooks / describeAudiobook plus the
shared seek / speed / now-playing tools).

Everything runs against a real (tiny) audiobook tree generated in a temp
directory — no network, no user files.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main
from conftest import make_fc, commands, audiobook_manager, write_mp3, build_audiobook_tree


def book_source(conn, book, chapter, position=None, playing=True):
    conn.playback.update({"playing": playing, "track_id": None, "source": {
        "type": "audiobook", "id": book["id"], "name": book["title"], "author": book.get("author", ""),
        "chapterId": chapter["id"], "chapterIndex": chapter["index"], "chapterTitle": chapter["title"],
        "chapterCount": len(book["chapters"]), "position": position, "duration": chapter.get("duration")}})


# ─── scanner ──────────────────────────────────────────────────────────────────

def test_scanner_groups_books_and_orders_chapters(audiobook_library):
    books = audiobook_library["books"]
    assert [b["title"] for b in books] == ["Dune", "The Hobbit", "Short Story"]       # by title, ignoring "The"
    hob = audiobook_library["hobbit"]
    assert hob["author"] == "J.R.R. Tolkien" and hob["narrator"] == "Andy Serkis" and hob["genre"] == "Fantasy"
    assert hob["id"].startswith("ab_") and hob["path"] == "Tolkien/The Hobbit"
    # "CD 1" / "CD 2" folders merged into one book, ordered by disc then track
    assert [c["title"] for c in hob["chapters"]] == ["An Unexpected Party", "Roast Mutton", "Riddles in the Dark"]
    assert [c["index"] for c in hob["chapters"]] == [1, 2, 3]
    assert all(c["id"].startswith("ch_") for c in hob["chapters"])
    assert hob["chapters"][2]["filename"] == "Tolkien/The Hobbit/CD 2/01 Riddles.mp3"
    assert all(c["duration"] == 1 for c in hob["chapters"]) and hob["duration"] == 3 and hob["chapterCount"] == 3
    assert hob["cover"] == ""

    dune = audiobook_library["dune"]
    # Untagged: folder name is the title, natural sort puts "Part 10" after "Part 2", cover.jpg found
    assert dune["author"] == "Unknown Author" and dune["narrator"] == ""
    assert [c["title"] for c in dune["chapters"]] == ["Part 1", "Part 2", "Part 10"]
    assert dune["cover"] == "Dune/cover.jpg"

    short = audiobook_library["short"]
    # Loose file in the root = a single-chapter book titled from its tag
    assert short["author"] == "Anon" and short["chapterCount"] == 1
    assert short["chapters"][0]["title"] == "Short Story" and short["chapters"][0]["filename"] == "Short Story.mp3"


def test_scanner_ids_are_stable_and_tags_override_folder_names(tmp_path):
    root = build_audiobook_tree(tmp_path / "A")
    first = audiobook_manager.scan_audiobooks(root)
    again = audiobook_manager.scan_audiobooks(root)
    assert [b["id"] for b in first] == [b["id"] for b in again]
    assert [[c["id"] for c in b["chapters"]] for b in first] == [[c["id"] for c in b["chapters"]] for b in again]
    # Same relative layout elsewhere -> same ids (content-addressable by relative path)
    other = audiobook_manager.scan_audiobooks(build_audiobook_tree(tmp_path / "B"))
    assert [b["id"] for b in other] == [b["id"] for b in first]


def test_scanner_identical_chapter_titles_fall_back_to_filenames(tmp_path):
    root = tmp_path / "lib"
    for i in (1, 2):
        write_mp3(root / "Some Book" / f"{i:02d} - file.mp3", album="Some Book", title="Some Book", tracknumber=str(i))
    (book,) = audiobook_manager.scan_audiobooks(root)
    assert book["title"] == "Some Book"
    assert [c["title"] for c in book["chapters"]] == ["01 - file", "02 - file"]


def test_scanner_handles_missing_or_unset_directory(tmp_path):
    assert audiobook_manager.scan_audiobooks(None) == []
    assert audiobook_manager.scan_audiobooks(tmp_path / "nope") == []
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "notes.txt").write_text("not audio")
    assert audiobook_manager.scan_audiobooks(empty) == []


def test_read_tags_never_raises(tmp_path):
    junk = tmp_path / "x.mp3"
    junk.write_bytes(b"not really an mp3")
    tags = audiobook_manager.read_tags(junk)
    assert tags["title"] == "" and tags["duration"] is None
    assert audiobook_manager.read_tags(tmp_path / "missing.mp3")["album"] == ""


# ─── progress ─────────────────────────────────────────────────────────────────

def test_progress_roundtrip_and_finished_detection(audiobook_library):
    hob = audiobook_library["hobbit"]
    ch1, ch2, ch3 = hob["chapters"]
    assert audiobook_manager.get_progress(hob["id"]) is None

    entry = audiobook_manager.update_progress(hob, ch2["id"], 0.6, duration=1.0)
    assert entry["chapterId"] == ch2["id"] and entry["chapterIndex"] == 2 and entry["position"] == 0.6 and entry["finished"] is False
    assert audiobook_manager.get_progress(hob["id"])["chapterId"] == ch2["id"]
    assert audiobook_manager.listened_seconds(hob, entry) == pytest.approx(1.6)       # chapter 1 + 0.6 s

    summary = audiobook_manager.summarize(hob, entry)
    assert summary["progress"]["fraction"] == pytest.approx(0.533, abs=0.001) and summary["progress"]["listened"] == 2
    assert summary["hasCover"] is False and summary["chapterCount"] == 3 and "chapters" not in summary
    assert "chapters" in audiobook_manager.with_chapters(hob, entry)

    # Final seconds of the LAST chapter = finished; position resets so "continue" starts over
    # (the margin is min(20 s, half the chapter), so 1-second test chapters still behave)
    assert audiobook_manager.update_progress(hob, ch3["id"], 0.2, duration=1.0)["finished"] is False
    done = audiobook_manager.update_progress(hob, ch3["id"], 0.95, duration=1.0)
    assert done["finished"] is True and done["position"] == 0.0
    real = {**hob, "chapters": [{**c, "duration": 1800} for c in hob["chapters"]]}
    assert audiobook_manager.update_progress(real, real["chapters"][2]["id"], 1785, duration=1800)["finished"] is True
    assert audiobook_manager.update_progress(real, real["chapters"][2]["id"], 1700, duration=1800)["finished"] is False
    assert audiobook_manager.progress_label(hob, done) == "finished"
    # …but the end of a middle chapter is not
    mid = audiobook_manager.update_progress(hob, ch1["id"], 0.99, duration=1.0)
    assert mid["finished"] is False

    with pytest.raises(ValueError):
        audiobook_manager.update_progress(hob, "ch_bogus", 1.0)

    assert audiobook_manager.reset_progress(hob["id"]) is True
    assert audiobook_manager.reset_progress(hob["id"]) is False
    assert audiobook_manager.get_progress(hob["id"]) is None
    assert audiobook_manager.progress_label(hob, None) == "not started yet"


def test_progress_label_and_most_recent_book(audiobook_library):
    hob, dune = audiobook_library["hobbit"], audiobook_library["dune"]
    big = {**hob, "duration": 7200, "chapters": [{**c, "duration": 2400} for c in hob["chapters"]]}
    prog = {"chapterId": big["chapters"][1]["id"], "chapterIndex": 2, "position": 90, "finished": False, "updatedAt": 10}
    assert audiobook_manager.progress_label(big, prog) == "in progress: chapter 2 of 3 at 1:30 into it, 35% through"

    progress = {hob["id"]: {"finished": True, "updatedAt": 500}, dune["id"]: {"finished": False, "updatedAt": 100, "chapterId": dune["chapters"][0]["id"]}}
    # Unfinished books win over a more recently finished one
    assert audiobook_manager.most_recent_book(audiobook_library["books"], progress)["id"] == dune["id"]
    assert audiobook_manager.most_recent_book(audiobook_library["books"], {hob["id"]: {"finished": True, "updatedAt": 5}})["id"] == hob["id"]
    assert audiobook_manager.most_recent_book(audiobook_library["books"], {}) is None


# ─── spoken resolution ────────────────────────────────────────────────────────

def test_find_audiobook_by_loose_title_author_or_narrator(audiobook_library):
    books = audiobook_library["books"]
    assert audiobook_manager.find_audiobook("the hobbit audiobook", books)["title"] == "The Hobbit"
    assert audiobook_manager.find_audiobook("that tolkien book", books)["title"] == "The Hobbit"
    assert audiobook_manager.find_audiobook("andy serkis", books)["title"] == "The Hobbit"
    assert audiobook_manager.find_audiobook("dune", books)["title"] == "Dune"
    assert audiobook_manager.find_audiobook("war and peace", books) is None


@pytest.mark.parametrize("spoken, expected_idx, expected_pos", [
    ("", 2, 30.0), ("continue", 2, 30.0), ("where I left off", 2, 30.0), ("resume", 2, 30.0),
    ("from the beginning", 1, 0.0), ("start over", 1, 0.0), ("chapter one", 1, 0.0),
    ("chapter 3", 3, 0.0), ("part two", 2, 0.0), ("3", 3, 0.0), ("number 1", 1, 0.0),
    ("next", 3, 0.0), ("the next chapter", 3, 0.0), ("previous chapter", 1, 0.0), ("go back a chapter", 1, 0.0),
    ("last chapter", 3, 0.0), ("riddles in the dark", 3, 0.0), ("the one about roast mutton", 2, 0.0),
])
def test_resolve_chapter(audiobook_library, spoken, expected_idx, expected_pos):
    hob = audiobook_library["hobbit"]
    prog = {"chapterId": hob["chapters"][1]["id"], "chapterIndex": 2, "position": 30.0, "finished": False}
    ch, pos, err = audiobook_manager.resolve_chapter(hob, spoken, progress=prog, current_chapter_id=hob["chapters"][1]["id"])
    assert err is None, err
    assert ch["index"] == expected_idx and pos == expected_pos


def test_resolve_chapter_edge_cases(audiobook_library):
    hob = audiobook_library["hobbit"]
    # continue on a finished or never-started book = chapter 1
    ch, pos, _ = audiobook_manager.resolve_chapter(hob, "continue", progress={"finished": True, "chapterId": hob["chapters"][2]["id"], "position": 0})
    assert ch["index"] == 1 and pos == 0.0
    ch, pos, _ = audiobook_manager.resolve_chapter(hob, "", progress=None)
    assert ch["index"] == 1
    # "next" with nothing playing and no progress = first chapter
    ch, _, _ = audiobook_manager.resolve_chapter(hob, "next")
    assert ch["index"] == 1
    # past either end
    _, _, err = audiobook_manager.resolve_chapter(hob, "next", current_chapter_id=hob["chapters"][2]["id"])
    assert err == "That is already the last chapter."
    _, _, err = audiobook_manager.resolve_chapter(hob, "previous chapter", current_chapter_id=hob["chapters"][0]["id"])
    assert err == "That is already the first chapter."
    _, _, err = audiobook_manager.resolve_chapter(hob, "chapter 9")
    assert "no chapter 9" in err
    _, _, err = audiobook_manager.resolve_chapter(hob, "the dragon scene")
    assert err.startswith("No chapter of 'The Hobbit' matches")
    _, _, err = audiobook_manager.resolve_chapter({"title": "Empty", "chapters": []}, "")
    assert "no playable chapters" in err


def test_labels(audiobook_library):
    hob = audiobook_library["hobbit"]
    assert audiobook_manager.book_label(hob) == "'The Hobbit' by J.R.R. Tolkien read by Andy Serkis"
    assert audiobook_manager.book_label(audiobook_library["dune"]) == "'Dune'"
    assert audiobook_manager.chapter_label(hob, hob["chapters"][0]) == "chapter 1 of 3 ('An Unexpected Party') [1 sec]"
    # Generic titles ("Part 2") are not repeated
    dune = audiobook_library["dune"]
    assert audiobook_manager.chapter_label(dune, dune["chapters"][1]) == "chapter 2 of 3 [1 sec]"
    assert audiobook_manager.format_duration(3723) == "1h 02m" and audiobook_manager.format_duration(None) == "unknown length"


# ─── HTTP API ─────────────────────────────────────────────────────────────────

@pytest.fixture
def client(library, playlists_file):
    return TestClient(main.app)


def test_list_and_get_audiobooks(client, audiobook_library):
    r = client.get("/api/audiobooks")
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is True and body["directoryExists"] is True and body["scanning"] is False
    assert [b["title"] for b in body["books"]] == ["Dune", "The Hobbit", "Short Story"]
    hob = next(b for b in body["books"] if b["title"] == "The Hobbit")
    assert "chapters" not in hob and hob["progress"]["finished"] is False and hob["progress"]["chapterId"] is None

    full = client.get(f"/api/audiobooks/{hob['id']}").json()
    assert [c["title"] for c in full["chapters"]] == ["An Unexpected Party", "Roast Mutton", "Riddles in the Dark"]
    assert client.get("/api/audiobooks/ab_nope").status_code == 404


def test_list_when_not_configured(client, monkeypatch):
    body = client.get("/api/audiobooks").json()
    assert body == {"configured": False, "directoryExists": False, "scanning": False, "books": []}
    assert client.post("/api/audiobooks/rescan").status_code == 503


def test_rescan_picks_up_new_books(client, audiobook_library):
    root = audiobook_library["root"]
    assert len(client.get("/api/audiobooks").json()["books"]) == 3
    write_mp3(root / "New Book" / "01.mp3", album="Brand New", artist="Someone")
    r = client.post("/api/audiobooks/rescan")
    assert r.status_code == 200 and r.json()["count"] == 4
    titles = [b["title"] for b in client.get("/api/audiobooks").json()["books"]]
    assert "Brand New" in titles
    # ids of existing books did not change
    assert any(b["id"] == audiobook_library["hobbit"]["id"] for b in main.audiobook_library)


def test_progress_endpoints(client, audiobook_library):
    hob = audiobook_library["hobbit"]
    ch2 = hob["chapters"][1]
    r = client.put(f"/api/audiobooks/{hob['id']}/progress", json={"chapterId": ch2["id"], "position": 0.4, "duration": 1.0})
    assert r.status_code == 200 and r.json()["chapterIndex"] == 2 and r.json()["position"] == 0.4
    listed = next(b for b in client.get("/api/audiobooks").json()["books"] if b["id"] == hob["id"])
    assert listed["progress"]["chapterId"] == ch2["id"] and listed["progress"]["listened"] == 1

    assert client.put(f"/api/audiobooks/{hob['id']}/progress", json={"chapterId": "ch_x", "position": 1}).status_code == 400
    assert client.put("/api/audiobooks/ab_nope/progress", json={"chapterId": ch2["id"], "position": 1}).status_code == 404
    assert client.put(f"/api/audiobooks/{hob['id']}/progress", content=b"garbage").status_code == 400

    assert client.delete(f"/api/audiobooks/{hob['id']}/progress").json() == {"success": True}
    assert audiobook_manager.get_progress(hob["id"]) is None
    assert client.delete("/api/audiobooks/ab_nope/progress").status_code == 404


def test_chapter_stream_with_range(client, audiobook_library):
    hob = audiobook_library["hobbit"]
    ch = hob["chapters"][0]
    size = (audiobook_library["root"] / ch["filename"]).stat().st_size

    r = client.get(f"/api/audiobooks/{hob['id']}/chapters/{ch['id']}/stream")
    assert r.status_code == 200 and r.headers["content-type"].startswith("audio/mpeg")
    assert r.headers["accept-ranges"] == "bytes" and int(r.headers["content-length"]) == size == len(r.content)

    r = client.get(f"/api/audiobooks/{hob['id']}/chapters/{ch['id']}/stream", headers={"Range": "bytes=100-199"})
    assert r.status_code == 206 and len(r.content) == 100
    assert r.headers["content-range"] == f"bytes 100-199/{size}"

    r = client.get(f"/api/audiobooks/{hob['id']}/chapters/{ch['id']}/stream", headers={"Range": "bytes=-50"})
    assert r.status_code == 206 and len(r.content) == 50 and r.headers["content-range"] == f"bytes {size - 50}-{size - 1}/{size}"

    assert client.get(f"/api/audiobooks/{hob['id']}/chapters/ch_nope/stream").status_code == 404
    assert client.get(f"/api/audiobooks/ab_nope/chapters/{ch['id']}/stream").status_code == 404


def test_chapter_stream_missing_file(client, audiobook_library):
    dune = audiobook_library["dune"]
    ch = dune["chapters"][0]
    (audiobook_library["root"] / ch["filename"]).unlink()
    r = client.get(f"/api/audiobooks/{dune['id']}/chapters/{ch['id']}/stream")
    assert r.status_code == 404 and "disk" in r.json()["error"]


def test_cover_art_from_folder_file_embedded_or_404(client, audiobook_library):
    dune, hob = audiobook_library["dune"], audiobook_library["hobbit"]
    r = client.get(f"/api/audiobooks/{dune['id']}/art")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and r.content.startswith(b"\xff\xd8")

    # No cover file and no embedded picture -> 404
    assert client.get(f"/api/audiobooks/{hob['id']}/art").status_code == 404

    # Embed a picture in the first chapter -> served
    from mutagen.id3 import ID3, APIC
    tags = ID3(audiobook_library["root"] / hob["chapters"][0]["filename"])
    tags.add(APIC(encoding=3, mime="image/png", type=3, desc="cover", data=b"\x89PNG\r\n\x1a\n" + b"\x00" * 16))
    tags.save()
    r = client.get(f"/api/audiobooks/{hob['id']}/art")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert client.get("/api/audiobooks/ab_nope/art").status_code == 404


# ─── voice tools ──────────────────────────────────────────────────────────────

AUDIOBOOK_TOOLS = ("playAudiobook", "listAudiobooks", "describeAudiobook")


def test_tools_declared_and_prompted(library, audiobook_library):
    cfg = main.build_gemini_config()
    declared = {d["name"] for d in cfg["tools"][0]["function_declarations"]}
    for t in AUDIOBOOK_TOOLS:
        assert t in declared and t in main.TOOL_HANDLERS, t
    instr = cfg["system_instruction"]
    assert "AUDIOBOOKS (voice controls" in instr and "`playAudiobook`" in instr and "`describeAudiobook`" in instr
    assert "Audiobooks: 'Dune'; 'The Hobbit' by J.R.R. Tolkien; 'Short Story' by Anon." in instr
    audiobook_manager.update_progress(audiobook_library["hobbit"], audiobook_library["hobbit"]["chapters"][0]["id"], 0.5, 1.0)
    assert "'The Hobbit' by J.R.R. Tolkien [in progress]" in main._sources_summary()


def test_prompt_when_unconfigured_or_empty(library, monkeypatch, tmp_path):
    assert "Audiobooks are not configured" in main._sources_summary()
    monkeypatch.setattr(main, "AUDIOBOOKS_DIR", tmp_path)
    assert "no audiobooks were found" in main._sources_summary()


def test_play_audiobook_not_configured(conn, run):
    res = run(main._tool_play_audiobook(conn, make_fc("playAudiobook", book="the hobbit")))["result"]
    assert "not set up" in res and "AUDIOBOOKS_DIR" in res
    assert commands(conn) == []
    assert "not set up" in run(main._tool_list_audiobooks(conn, make_fc("listAudiobooks")))["result"]


def test_play_audiobook_from_the_start_then_continue(conn, run, audiobook_library):
    hob = audiobook_library["hobbit"]
    res = run(main._tool_play_audiobook(conn, make_fc("playAudiobook", book="the hobbit")))["result"]
    assert res.startswith("Playing 'The Hobbit' by J.R.R. Tolkien read by Andy Serkis, chapter 1 of 3 ('An Unexpected Party') [1 sec].")
    assert "Before this the book was not started yet." in res
    assert "Now playing: audiobook 'The Hobbit' by J.R.R. Tolkien, chapter 1 of 3 ('An Unexpected Party')" in res

    cmd = commands(conn, "playAudiobookChapter")
    assert len(cmd) == 1
    assert cmd[0]["book"]["id"] == hob["id"] and cmd[0]["chapter"]["index"] == 1 and cmd[0]["position"] == 0.0 and cmd[0]["chapterCount"] == 3
    assert conn.playback["source"]["type"] == "audiobook" and conn.playback["source"]["chapterId"] == hob["chapters"][0]["id"]
    assert conn.playback["track_id"] is None

    # The browser reports progress in chapter 2; "continue my book" resumes there
    audiobook_manager.update_progress(hob, hob["chapters"][1]["id"], 0.5, 1.0)
    conn.playback["source"] = None
    res = run(main._tool_play_audiobook(conn, make_fc("playAudiobook", book="", chapter="continue")))["result"]
    assert "chapter 2 of 3 ('Roast Mutton')" in res and "resuming at 0:00" in res
    assert "Before this the book was in progress: chapter 2 of 3" in res
    cmd = commands(conn, "playAudiobookChapter")[-1]
    assert cmd["chapter"]["index"] == 2 and cmd["position"] == 0.5


def test_play_audiobook_chapter_navigation(conn, run, audiobook_library):
    hob = audiobook_library["hobbit"]
    book_source(conn, hob, hob["chapters"][1])
    res = run(main._tool_play_audiobook(conn, make_fc("playAudiobook", book="current", chapter="next")))["result"]
    assert "chapter 3 of 3 ('Riddles in the Dark')" in res
    assert conn.playback["source"]["chapterIndex"] == 3

    res = run(main._tool_play_audiobook(conn, make_fc("playAudiobook", book="this book", chapter="next")))["result"]
    assert res == "That is already the last chapter."

    res = run(main._tool_play_audiobook(conn, make_fc("playAudiobook", chapter="chapter 1")))["result"]
    assert "chapter 1 of 3" in res
    res = run(main._tool_play_audiobook(conn, make_fc("playAudiobook", book="the hobbit", chapter="chapter 12")))["result"]
    assert "there is no chapter 12" in res


def test_play_audiobook_defaults_to_most_recent_book(conn, run, audiobook_library):
    dune = audiobook_library["dune"]
    # Nothing playing, several books, no progress -> ask
    res = run(main._tool_play_audiobook(conn, make_fc("playAudiobook")))["result"]
    assert res.startswith("No audiobook is playing or in progress, so I need to know which book.") and "'Dune'" in res
    assert commands(conn) == []
    audiobook_manager.update_progress(dune, dune["chapters"][1]["id"], 0.2, 1.0)
    res = run(main._tool_play_audiobook(conn, make_fc("playAudiobook", chapter="keep going")))["result"]
    assert res.startswith("Playing 'Dune', chapter 2 of 3 [1 sec], resuming at 0:00.")
    res = run(main._tool_play_audiobook(conn, make_fc("playAudiobook", book="war and peace")))["result"]
    assert res.startswith("No audiobook matches 'war and peace'.") and "'The Hobbit'" in res


def test_list_and_describe_audiobooks(conn, run, audiobook_library):
    hob = audiobook_library["hobbit"]
    audiobook_manager.update_progress(hob, hob["chapters"][1]["id"], 0.5, 1.0)
    book_source(conn, hob, hob["chapters"][1])
    res = run(main._tool_list_audiobooks(conn, make_fc("listAudiobooks")))["result"]
    assert res.startswith("3 audiobooks: 'Dune' [3 sec, 3 chapters] — not started yet; "
                          "'The Hobbit' by J.R.R. Tolkien read by Andy Serkis [3 sec, 3 chapters] — in progress: chapter 2 of 3, 50% through [playing now]; "
                          "'Short Story' by Anon [1 sec, 1 chapter] — not started yet.")
    assert res.endswith(" 1 in progress.")

    res = run(main._tool_describe_audiobook(conn, make_fc("describeAudiobook", book="current")))["result"]
    assert res.startswith("'The Hobbit' by J.R.R. Tolkien read by Andy Serkis: 3 sec in 3 chapters, genre Fantasy. Progress: in progress: chapter 2 of 3, 50% through. "
                          "Chapters: chapter 1 of 3 ('An Unexpected Party') [1 sec]; chapter 2 of 3 ('Roast Mutton') [1 sec]; chapter 3 of 3 ('Riddles in the Dark') [1 sec].")
    res = run(main._tool_describe_audiobook(conn, make_fc("describeAudiobook", book="dune")))["result"]
    assert res.startswith("'Dune': 3 sec in 3 chapters. Progress: not started yet.")


def test_now_playing_seek_and_speed_for_audiobooks(conn, run, audiobook_library):
    hob = audiobook_library["hobbit"]
    audiobook_manager.update_progress(hob, hob["chapters"][1]["id"], 0.5, 1.0)
    book_source(conn, hob, hob["chapters"][1], position=0.5)

    res = run(main._tool_describe_now_playing(conn, make_fc("describeNowPlaying")))["result"]
    assert res.startswith("Now playing: audiobook 'The Hobbit' by J.R.R. Tolkien, chapter 2 of 3 ('Roast Mutton'), at 0:00 of 0:01.")
    assert "Book progress: in progress: chapter 2 of 3, 50% through." in res and "Narrated by Andy Serkis." in res

    res = run(main._tool_seek_relative(conn, make_fc("seekRelative", seconds=30)))["result"]
    assert res.startswith("Skipped ahead 30 seconds.")
    assert commands(conn, "seekRelative")[0]["seconds"] == 30

    res = run(main._tool_set_playback_speed(conn, make_fc("setPlaybackSpeed", rate=1.5)))["result"]
    assert res == "Playing 'Roast Mutton' at 1.5x."
    assert commands(conn, "setPlaybackRate")[0]["rate"] == 1.5
    assert conn.playback["playbackRate"] == 1.5
    assert ", at 1.5x speed" in conn.source_summary()
    res = run(main._tool_set_playback_speed(conn, make_fc("setPlaybackSpeed", rate=1)))["result"]
    assert res == "Back to normal speed (1x) for 'Roast Mutton'."


def test_audiobook_source_blocks_library_only_tools(conn, run, audiobook_library, playlists_file):
    hob = audiobook_library["hobbit"]
    book_source(conn, hob, hob["chapters"][0])
    res = run(main._tool_add_to_playlist(conn, make_fc("addToPlaylist", playlistName="Favourites")))["result"]
    assert res.startswith("An audiobook chapter is playing, not a library song")
    # A cued chapter is left out when the queue is saved as a playlist
    conn.playback["queue"] = [{"id": hob["chapters"][1]["id"], "title": "Roast Mutton", "artist": "The Hobbit", "kind": "audiobook"}, {"id": "t_heaven"}]
    res = run(main._tool_save_queue_as_playlist(conn, make_fc("saveQueueAsPlaylist", name="Mixed")))["result"]
    assert "Saved 1 tracks as playlist 'Mixed'" in res and "(1 cued audiobook chapter left out" in res
    assert main._queue_entry_label(conn.playback["queue"][0]) == "audiobook chapter 'Roast Mutton' from 'The Hobbit'"


def test_playback_state_accepts_audiobook_source(conn):
    """The websocket handler keeps only registered source types."""
    assert "audiobook" in main.SOURCE_TYPES and "audiobook" in main.SEEKABLE_SOURCE_TYPES
    conn.playback["source"] = {"type": "audiobook", "id": "ab_x", "name": "X"}
    assert conn.current_source()["type"] == "audiobook"
    assert conn.now_playing() is None
    conn.playback["source"] = {"type": "vinyl", "id": "v"}
    assert conn.current_source() is None

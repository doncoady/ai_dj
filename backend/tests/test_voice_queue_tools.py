"""Voice queue-management tools: clearQueue, removeFromQueue, describeQueue."""

import main
from conftest import make_fc, commands


def set_queue(conn, now=None, queue=(), playing=True, playlist=None):
    conn.playback.update({
        "playing": playing, "track_id": now,
        "queue": [{"id": q, "title": "x", "artist": "y"} for q in queue],
        "playlist": playlist,
    })


def test_tools_declared_and_registered(library):
    declared = {d["name"] for d in main.build_gemini_config()["tools"][0]["function_declarations"]}
    for t in ("clearQueue", "removeFromQueue", "describeQueue"):
        assert t in declared and t in main.TOOL_HANDLERS
    assert "QUEUE MANAGEMENT" in main.build_gemini_config()["system_instruction"]


def test_clear_queue(conn, run):
    set_queue(conn, now="t_billie", queue=["t_summer", "t_heaven"])
    res = run(main._tool_clear_queue(conn, make_fc("clearQueue")))
    assert "Cleared the queue: 2 cued tracks removed" in res["result"]
    assert "current track keeps playing" in res["result"]
    assert commands(conn, "clearQueue") == [{"type": "command", "action": "clearQueue"}]
    assert conn.playback["queue"] == []
    assert "Queue is currently empty" in res["result"]      # same-turn summary already reflects it


def test_clear_empty_queue(conn, run):
    set_queue(conn, now="t_billie")
    res = run(main._tool_clear_queue(conn, make_fc("clearQueue")))
    assert "already empty" in res["result"]
    assert len(commands(conn, "clearQueue")) == 1           # harmless; keeps UI in sync


def test_remove_from_queue_by_title(conn, run):
    set_queue(conn, now="t_billie", queue=["t_summer", "t_heaven", "t_onemore"])
    res = run(main._tool_remove_from_queue(conn, make_fc("removeFromQueue", trackQuery="Heaven")))
    assert "Removed 'Heaven' by Bryan Adams from the queue" in res["result"]
    assert commands(conn, "removeFromQueue")[0]["trackId"] == "t_heaven"
    assert [q["id"] for q in conn.playback["queue"]] == ["t_summer", "t_onemore"]


def test_remove_next_from_queue(conn, run):
    set_queue(conn, now="t_billie", queue=["t_summer", "t_heaven"])
    res = run(main._tool_remove_from_queue(conn, make_fc("removeFromQueue", trackQuery="next")))
    assert "Removed 'Summer of '69'" in res["result"]
    assert "Next in queue: 'Heaven'" in res["result"]


def test_remove_only_searches_the_queue(conn, run):
    set_queue(conn, now="t_billie", queue=["t_summer"])
    res = run(main._tool_remove_from_queue(conn, make_fc("removeFromQueue", trackQuery="Thriller")))
    assert "No cued track matches 'Thriller'" in res["result"] and "'Summer of '69'" in res["result"]
    assert not commands(conn, "removeFromQueue")


def test_remove_from_empty_queue(conn, run):
    set_queue(conn, now="t_billie")
    res = run(main._tool_remove_from_queue(conn, make_fc("removeFromQueue", trackQuery="anything")))
    assert "queue is empty" in res["result"]


def test_describe_queue(conn, run):
    set_queue(conn, now="t_billie", queue=["t_summer", "t_heaven"], playlist={"id": "p", "name": "Road Trip"})
    res = run(main._tool_describe_queue(conn, make_fc("describeQueue")))
    assert res["result"].startswith("Now playing: 'Billie Jean' by Michael Jackson.")
    assert "2 items cued up (from playlist 'Road Trip'), in order: 1. 'Summer of '69' by Bryan Adams; 2. 'Heaven' by Bryan Adams." in res["result"]


def test_describe_empty_queue(conn, run):
    set_queue(conn, now=None)
    res = run(main._tool_describe_queue(conn, make_fc("describeQueue")))
    assert res["result"] == "Nothing is playing. The queue is empty; after this track the library continues in order."


# ─── mixed queue: podcast episodes cued from the GUI alongside songs ──────────

def set_mixed_queue(conn):
    conn.playback.update({"playing": True, "track_id": "t_billie", "playlist": None, "queue": [
        {"id": "ep_abc", "title": "Episode 12: The Last Train", "artist": "Night Shift Radio Hour", "kind": "podcast"},
        {"id": "t_heaven", "title": "Heaven", "artist": "Bryan Adams"},
    ]})


def test_describe_mixed_queue(conn, run):
    set_mixed_queue(conn)
    res = run(main._tool_describe_queue(conn, make_fc("describeQueue")))["result"]
    assert "2 items cued up, in order: 1. podcast episode 'Episode 12: The Last Train' from 'Night Shift Radio Hour'; 2. 'Heaven' by Bryan Adams." in res
    assert conn.playback_summary().endswith("Next in queue: podcast episode 'Episode 12: The Last Train' from 'Night Shift Radio Hour'. (2 items queued in total).")


def test_remove_episode_from_mixed_queue(conn, run):
    set_mixed_queue(conn)
    res = run(main._tool_remove_from_queue(conn, make_fc("removeFromQueue", trackQuery="the last train episode")))["result"]
    assert res.startswith("Removed podcast episode 'Episode 12: The Last Train' from 'Night Shift Radio Hour' from the queue.")
    assert commands(conn, "removeFromQueue")[0]["trackId"] == "ep_abc"
    assert [q["id"] for q in conn.playback["queue"]] == ["t_heaven"]


def test_save_mixed_queue_keeps_songs_only(conn, run, playlists_file):
    set_mixed_queue(conn)
    res = run(main._tool_save_queue_as_playlist(conn, make_fc("saveQueueAsPlaylist", name="Mixed")))["result"]
    assert "Saved 2 tracks as playlist 'Mixed'" in res
    assert "1 cued podcast episode left out" in res
    assert main.pl_manager.load_playlists()[0]["trackIds"] == ["t_billie", "t_heaven"]

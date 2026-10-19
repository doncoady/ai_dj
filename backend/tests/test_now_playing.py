"""'What's playing?' must describe the current SONG, even while a playlist is active."""

import main
from conftest import pl_manager, make_fc


def test_one_off_song_during_playlist_is_not_described_as_the_playlist(conn, run):
    pl = pl_manager.create_playlist("AI Mix: Pure Rock", ["t_summer", "t_heaven"], is_dynamic=True)
    # Playlist is active (its songs are still cued) but the user asked for Billie Jean.
    conn.playback.update({"playing": True, "track_id": "t_billie",
                          "queue": [{"id": "t_heaven"}], "playlist": {"id": pl["id"], "name": pl["name"]}})
    res = run(main._tool_describe_now_playing(conn, make_fc("describeNowPlaying")))["result"]
    assert res.startswith("Now playing: 'Billie Jean' by Michael Jackson, from the album 'Thriller', genre Pop.")
    assert "NOT part of the active playlist 'AI Mix: Pure Rock'" in res
    assert "Up next: 'Heaven'" in res

    # Tool results for other commands lead with the song too, flagged the same way.
    summary = conn.playback_summary()
    assert summary.startswith("Now playing: 'Billie Jean' by Michael Jackson. It is a one-off request, NOT part of the active playlist")


def test_song_that_belongs_to_the_playlist(conn, run):
    pl = pl_manager.create_playlist("Road Trip", ["t_summer", "t_heaven"])
    conn.playback.update({"playing": False, "track_id": "t_summer", "queue": [], "playlist": {"id": pl["id"], "name": "Road Trip"}})
    res = run(main._tool_describe_now_playing(conn, make_fc("describeNowPlaying")))["result"]
    assert res.startswith("Now paused: 'Summer of '69' by Bryan Adams, from the album 'Reckless', genre Rock.")
    assert "from the active playlist 'Road Trip'" in res and "NOT" not in res
    assert conn.playback_summary().startswith("Now paused: 'Summer of '69' by Bryan Adams. It is part of the active playlist 'Road Trip'.")


def test_nothing_playing(conn, run):
    conn.playback.update({"playing": False, "track_id": None, "queue": [], "playlist": None})
    assert run(main._tool_describe_now_playing(conn, make_fc("describeNowPlaying")))["result"] == "Nothing is playing right now."
    assert conn.playback_summary().startswith("Client reports: nothing loaded/playing.")


def test_declared_and_prompted(library):
    cfg = main.build_gemini_config()
    assert "describeNowPlaying" in {d["name"] for d in cfg["tools"][0]["function_declarations"]}
    assert "WHAT'S PLAYING" in cfg["system_instruction"]

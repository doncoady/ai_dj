"""
Voice playlist-management tools (main.py TOOL_HANDLERS).

Each test drives a handler the same way the Gemini receive loop does, then
checks (a) the spoken result text, (b) persisted playlist state, and
(c) the command pushed to the browser so the UI stays in sync.
"""

import pytest

import main
from conftest import pl_manager, make_fc, commands


# ─── helpers ──────────────────────────────────────────────────────────────────

def stored(name=None):
    pls = pl_manager.load_playlists()
    if name is None:
        return pls
    return next((p for p in pls if p["name"] == name), None)


def set_playing(conn, track_id, queue=(), playlist=None):
    conn.playback.update({
        "playing": True,
        "track_id": track_id,
        "queue": [{"id": q} for q in queue],
        "playlist": playlist,
    })


# ─── registry sanity ──────────────────────────────────────────────────────────

def test_every_declared_tool_has_a_handler_and_vice_versa(library):
    config = main.build_gemini_config()
    declared = {d["name"] for d in config["tools"][0]["function_declarations"]}
    registered = set(main.TOOL_HANDLERS)
    assert declared == registered
    for new_tool in ("createPlaylist", "addToPlaylist", "removeFromPlaylist", "renamePlaylist",
                     "deletePlaylist", "saveQueueAsPlaylist", "describePlaylist", "queuePlaylist"):
        assert new_tool in declared


def test_system_prompt_teaches_playlist_management(library):
    instr = main.build_gemini_config()["system_instruction"]
    assert "PLAYLIST MANAGEMENT" in instr
    assert "`addToPlaylist`" in instr and "`deletePlaylist`" in instr
    assert "confirmed=true" in instr


# ─── "this song" / "this playlist" detection ──────────────────────────────────

@pytest.mark.parametrize("text", ["", "  ", "this", "current", "this song", "the track playing now",
                                  "this one", "the one that's playing", "it", "what's playing"])
def test_current_track_refs(text):
    assert main._is_current_track_ref(text)


@pytest.mark.parametrize("text", ["One", "Thriller", "this is the end", "Billie Jean", "now or never", "the song by adele"])
def test_not_current_track_refs(text):
    assert not main._is_current_track_ref(text)


@pytest.mark.parametrize("text", ["", "current", "this playlist", "the current playlist", "the active mix", "this one"])
def test_current_playlist_refs(text):
    assert main._is_current_playlist_ref(text)


@pytest.mark.parametrize("text", ["Mix", "the 80s mix", "playlist", "Now Dance", "workout"])
def test_not_current_playlist_refs(text):
    assert not main._is_current_playlist_ref(text)


# ─── createPlaylist ───────────────────────────────────────────────────────────

def test_create_playlist_empty(conn, run):
    res = run(main._tool_create_playlist(conn, make_fc("createPlaylist", name="called 'Workout'")))
    assert "Created playlist 'Workout'" in res["result"]
    assert "Playback was not changed" in res["result"]
    pl = stored("Workout")
    assert pl and pl["trackIds"] == [] and pl["isDynamic"] is False

    cmd = commands(conn, "playlistsChanged")
    assert len(cmd) == 1 and cmd[0]["change"] == "created"
    assert cmd[0]["playlist"] == {"id": pl["id"], "name": "Workout", "trackCount": 0, "isDynamic": False}
    # No playback command was emitted
    assert not commands(conn, "loadAndPlayPlaylist")


def test_create_playlist_seeded_with_current_track(conn, run):
    set_playing(conn, "t_billie")
    res = run(main._tool_create_playlist(conn, make_fc("createPlaylist", name="Faves", includeCurrentTrack=True)))
    assert "Billie Jean" in res["result"]
    assert stored("Faves")["trackIds"] == ["t_billie"]


def test_create_playlist_rejects_duplicate_name(conn, run):
    run(main._tool_create_playlist(conn, make_fc("createPlaylist", name="Workout")))
    res = run(main._tool_create_playlist(conn, make_fc("createPlaylist", name="my workout playlist")))
    assert "already exists" in res["result"]
    assert len(stored()) == 1


def test_create_playlist_needs_name(conn, run):
    res = run(main._tool_create_playlist(conn, make_fc("createPlaylist", name="  ")))
    assert "need a name" in res["result"]
    assert stored() == []


# ─── addToPlaylist ────────────────────────────────────────────────────────────

def test_add_current_track_to_existing_playlist(conn, run):
    pl_manager.create_playlist("Workout", ["t_onemore"])
    set_playing(conn, "t_summer")
    res = run(main._tool_add_to_playlist(conn, make_fc("addToPlaylist", playlistName="my workout playlist")))
    assert "Added 'Summer of '69' by Bryan Adams to 'Workout' (2 tracks)" in res["result"]
    assert stored("Workout")["trackIds"] == ["t_onemore", "t_summer"]
    cmd = commands(conn, "playlistsChanged")[0]
    assert cmd["change"] == "updated" and cmd["playlist"]["trackCount"] == 2


def test_add_searched_track_prefers_title_match(conn, run):
    pl_manager.create_playlist("Party", [])
    # "Thriller" matches the album of three tracks; the title match must win.
    res = run(main._tool_add_to_playlist(conn, make_fc("addToPlaylist", playlistName="party", trackQuery="the song Thriller")))
    assert "'Thriller' by Michael Jackson" in res["result"]
    assert stored("Party")["trackIds"] == ["t_thriller"]


def test_add_by_explicit_track_id(conn, run):
    pl_manager.create_playlist("Party", [])
    run(main._tool_add_to_playlist(conn, make_fc("addToPlaylist", playlistName="party", trackId="t_weather")))
    assert stored("Party")["trackIds"] == ["t_weather"]


def test_add_creates_missing_playlist(conn, run):
    set_playing(conn, "t_heaven")
    res = run(main._tool_add_to_playlist(conn, make_fc("addToPlaylist", playlistName="Road Trip")))
    assert "Created playlist 'Road Trip' and added 'Heaven'" in res["result"]
    assert stored("Road Trip")["trackIds"] == ["t_heaven"]
    assert commands(conn, "playlistsChanged")[0]["change"] == "created"


def test_add_creates_missing_playlist_with_clean_name(conn, run):
    set_playing(conn, "t_heaven")
    res = run(main._tool_add_to_playlist(conn, make_fc("addToPlaylist", playlistName="my workout playlist")))
    assert "Created playlist 'Workout'" in res["result"]
    assert stored("Workout") is not None
    # …and a second add finds it instead of creating another one
    run(main._tool_add_to_playlist(conn, make_fc("addToPlaylist", playlistName="workout", trackQuery="Billie Jean")))
    assert len(stored()) == 1 and stored("Workout")["trackIds"] == ["t_heaven", "t_billie"]


def test_explicit_create_keeps_name_verbatim(conn, run):
    run(main._tool_create_playlist(conn, make_fc("createPlaylist", name="Summer Mix")))
    assert stored("Summer Mix") is not None


def test_add_to_current_playlist_uses_active_playlist(conn, run):
    pl = pl_manager.create_playlist("Chill", ["t_weather"])
    set_playing(conn, "t_dontdream", playlist={"id": pl["id"], "name": "Chill"})
    res = run(main._tool_add_to_playlist(conn, make_fc("addToPlaylist", playlistName="this playlist")))
    assert "to 'Chill' (2 tracks)" in res["result"]
    assert stored("Chill")["trackIds"] == ["t_weather", "t_dontdream"]


def test_add_to_current_playlist_without_active_does_not_create(conn, run):
    set_playing(conn, "t_dontdream", playlist=None)
    res = run(main._tool_add_to_playlist(conn, make_fc("addToPlaylist", playlistName="current")))
    assert "No playlist is currently playing" in res["result"]
    assert stored() == []


def test_add_duplicate_is_noop(conn, run):
    pl_manager.create_playlist("Workout", ["t_billie"])
    res = run(main._tool_add_to_playlist(conn, make_fc("addToPlaylist", playlistName="workout", trackQuery="Billie Jean")))
    assert "already in" in res["result"]
    assert stored("Workout")["trackIds"] == ["t_billie"]
    assert not commands(conn, "playlistsChanged")


def test_add_nothing_playing_and_no_query(conn, run):
    pl_manager.create_playlist("Workout", [])
    res = run(main._tool_add_to_playlist(conn, make_fc("addToPlaylist", playlistName="workout")))
    assert "Nothing is playing" in res["result"]


def test_add_unknown_song(conn, run):
    pl_manager.create_playlist("Workout", [])
    res = run(main._tool_add_to_playlist(conn, make_fc("addToPlaylist", playlistName="workout", trackQuery="Bohemian Rhapsody")))
    assert "No track found matching 'Bohemian Rhapsody'" in res["result"]


# ─── removeFromPlaylist ───────────────────────────────────────────────────────

def test_remove_searched_track_scoped_to_playlist(conn, run):
    pl_manager.create_playlist("Party", ["t_beatit", "t_onemore"])
    # "Thriller" (album) matches Beat It inside the playlist; the library's
    # actual 'Thriller' track is NOT in the playlist and must not be chosen.
    res = run(main._tool_remove_from_playlist(conn, make_fc("removeFromPlaylist", playlistName="party", trackQuery="Beat It")))
    assert "Removed 'Beat It'" in res["result"]
    assert stored("Party")["trackIds"] == ["t_onemore"]
    assert commands(conn, "playlistsChanged")[0]["change"] == "updated"


def test_remove_song_not_in_playlist(conn, run):
    pl_manager.create_playlist("Party", ["t_onemore"])
    res = run(main._tool_remove_from_playlist(conn, make_fc("removeFromPlaylist", playlistName="party", trackQuery="Billie Jean")))
    assert "No track found matching 'Billie Jean' in playlist 'Party'" in res["result"]
    assert stored("Party")["trackIds"] == ["t_onemore"]


def test_remove_current_track_from_current_playlist(conn, run):
    pl = pl_manager.create_playlist("Chill", ["t_weather", "t_dontdream"])
    set_playing(conn, "t_weather", playlist={"id": pl["id"], "name": "Chill"})
    res = run(main._tool_remove_from_playlist(conn, make_fc("removeFromPlaylist", playlistName="current", trackQuery="this song")))
    assert "Removed 'Weather With You'" in res["result"]
    assert stored("Chill")["trackIds"] == ["t_dontdream"]


def test_remove_from_unknown_playlist_lists_options(conn, run):
    pl_manager.create_playlist("Chill", ["t_weather"])
    res = run(main._tool_remove_from_playlist(conn, make_fc("removeFromPlaylist", playlistName="Jazz", trackQuery="x")))
    assert "No playlist found matching 'Jazz'" in res["result"]
    assert "'Chill'" in res["result"]


# ─── renamePlaylist ───────────────────────────────────────────────────────────

def test_rename_playlist(conn, run):
    pl = pl_manager.create_playlist("Workout", ["t_billie"])
    res = run(main._tool_rename_playlist(conn, make_fc("renamePlaylist", playlistName="workout", newName="Gym Bangers")))
    assert res["result"] == "Renamed playlist 'Workout' to 'Gym Bangers'."
    assert stored("Gym Bangers")["id"] == pl["id"] and stored("Workout") is None
    cmd = commands(conn, "playlistsChanged")[0]
    assert cmd["change"] == "renamed" and cmd["playlist"]["name"] == "Gym Bangers"


def test_rename_current_playlist(conn, run):
    pl = pl_manager.create_playlist("AI Dynamic Mix", ["t_billie"], is_dynamic=True)
    set_playing(conn, "t_billie", playlist={"id": pl["id"], "name": pl["name"]})
    res = run(main._tool_rename_playlist(conn, make_fc("renamePlaylist", playlistName="this playlist", newName="Friday Night")))
    assert "to 'Friday Night'" in res["result"]


def test_rename_requires_new_name(conn, run):
    pl_manager.create_playlist("Workout", [])
    res = run(main._tool_rename_playlist(conn, make_fc("renamePlaylist", playlistName="workout", newName="")))
    assert "need the new name" in res["result"]
    assert stored("Workout") is not None


# ─── deletePlaylist ───────────────────────────────────────────────────────────

def test_delete_requires_confirmation(conn, run):
    pl_manager.create_playlist("Workout", ["t_billie", "t_summer"])
    res = run(main._tool_delete_playlist(conn, make_fc("deletePlaylist", playlistName="workout")))
    assert "CONFIRMATION REQUIRED" in res["result"] and "'Workout' (2 tracks)" in res["result"]
    assert stored("Workout") is not None
    assert not commands(conn, "playlistsChanged")


def test_delete_when_confirmed(conn, run):
    pl = pl_manager.create_playlist("Workout", ["t_billie"])
    res = run(main._tool_delete_playlist(conn, make_fc("deletePlaylist", playlistName="workout", confirmed=True)))
    assert res["result"] == "Deleted playlist 'Workout'."
    assert stored("Workout") is None
    cmd = commands(conn, "playlistsChanged")[0]
    assert cmd["change"] == "deleted" and cmd["playlistId"] == pl["id"]


# ─── saveQueueAsPlaylist ──────────────────────────────────────────────────────

def test_save_queue_with_current_track_first(conn, run):
    set_playing(conn, "t_billie", queue=["t_summer", "t_onemore", "t_nonexistent", "t_billie"])
    res = run(main._tool_save_queue_as_playlist(conn, make_fc("saveQueueAsPlaylist", name="Tonight")))
    assert "Saved 3 tracks as playlist 'Tonight'" in res["result"]
    # unknown ids dropped, duplicate of the now-playing track dropped, order kept
    assert stored("Tonight")["trackIds"] == ["t_billie", "t_summer", "t_onemore"]
    cmd = commands(conn, "playlistsChanged")[0]
    assert cmd["change"] == "created" and cmd["setActive"] is True
    # The model gets the authoritative listing straight away
    assert "1. 'Billie Jean' by Michael Jackson; 2. 'Summer of '69' by Bryan Adams; 3. 'One More Time'" in res["result"]


def test_after_saving_queue_this_playlist_means_the_saved_one(conn, run):
    """Regression: 'save this queue' then 'what's in this playlist?' used to
    describe the previously loaded mix instead of the saved session."""
    old_mix = pl_manager.create_playlist("AI Mix: 80's Rewind", ["t_thriller", "t_beatit"], is_dynamic=True)
    set_playing(conn, "t_billie", queue=["t_summer"], playlist={"id": old_mix["id"], "name": old_mix["name"]})
    run(main._tool_save_queue_as_playlist(conn, make_fc("saveQueueAsPlaylist", name="Friday Night")))
    assert conn.playback["playlist"]["name"] == "Friday Night"
    res = run(main._tool_describe_playlist(conn, make_fc("describePlaylist", playlistName="this playlist")))
    assert "'Friday Night' is a custom playlist" in res["result"]
    assert "'Billie Jean'" in res["result"] and "'Summer of '69'" in res["result"]
    assert "Thriller" not in res["result"]


def test_other_changes_do_not_hijack_active_playlist(conn, run):
    pl_manager.create_playlist("Workout", [])
    set_playing(conn, "t_billie")
    run(main._tool_add_to_playlist(conn, make_fc("addToPlaylist", playlistName="workout")))
    run(main._tool_create_playlist(conn, make_fc("createPlaylist", name="Other")))
    assert all(c["setActive"] is False for c in commands(conn, "playlistsChanged"))


def test_save_queue_without_current_track_and_default_name(conn, run):
    set_playing(conn, "t_billie", queue=["t_summer"])
    res = run(main._tool_save_queue_as_playlist(conn, make_fc("saveQueueAsPlaylist", includeCurrentTrack=False)))
    assert "Saved 1 tracks" in res["result"]
    pl = stored()[0]
    assert pl["name"].startswith("Session Mix (") and pl["trackIds"] == ["t_summer"]


def test_save_queue_accepts_string_queue_items(conn, run):
    conn.playback.update({"track_id": None, "queue": ["t_heaven", "t_weather"]})
    res = run(main._tool_save_queue_as_playlist(conn, make_fc("saveQueueAsPlaylist", name="Strings")))
    assert stored("Strings")["trackIds"] == ["t_heaven", "t_weather"]


def test_save_empty_queue(conn, run):
    res = run(main._tool_save_queue_as_playlist(conn, make_fc("saveQueueAsPlaylist", name="Nothing")))
    assert "nothing to save" in res["result"]
    assert stored() == []


# ─── describePlaylist / listPlaylists ─────────────────────────────────────────

def test_describe_playlist(conn, run):
    pl_manager.create_playlist("Chill", ["t_weather", "t_dontdream"])
    res = run(main._tool_describe_playlist(conn, make_fc("describePlaylist", playlistName="chill")))
    assert "'Chill' is a custom playlist with 2 tracks, in order:" in res["result"]
    assert "1. 'Weather With You' by Crowded House" in res["result"]
    assert "2. 'Don't Dream It's Over'" in res["result"]


def test_describe_empty_and_unknown(conn, run):
    pl_manager.create_playlist("Empty", [])
    assert "is empty" in run(main._tool_describe_playlist(conn, make_fc("describePlaylist", playlistName="empty")))["result"]
    assert "No playlist found" in run(main._tool_describe_playlist(conn, make_fc("describePlaylist", playlistName="zzz")))["result"]


def test_list_playlists_mentions_count_and_kind(conn, run):
    pl_manager.create_playlist("Chill", ["t_weather"])
    pl_manager.create_playlist("AI Mix: Pure Rock", ["t_summer", "t_heaven"], is_dynamic=True)
    res = run(main._tool_list_playlists(conn, make_fc("listPlaylists")))
    assert res["result"].startswith("2 saved playlists:")
    assert "'AI Mix: Pure Rock' (2 tracks, AI mix)" in res["result"]
    assert "'Chill' (1 tracks)" in res["result"]


# ─── playPlaylist (shuffle) / queuePlaylist ───────────────────────────────────

def test_play_playlist_fuzzy_name_and_shuffle(conn, run):
    pl_manager.create_playlist("AI Mix: 80's Rewind", ["t_billie", "t_thriller", "t_beatit", "t_summer", "t_heaven"], is_dynamic=True)
    res = run(main._tool_play_playlist(conn, make_fc("playPlaylist", name="the 80s mix", shuffle=True)))
    assert "in shuffle order" in res["result"]
    cmd = commands(conn, "loadAndPlayPlaylist")[0]
    sent_ids = [t["id"] for t in cmd["tracks"]]
    assert sorted(sent_ids) == sorted(["t_billie", "t_thriller", "t_beatit", "t_summer", "t_heaven"])
    assert cmd["playlist"]["name"] == "AI Mix: 80's Rewind" and cmd["startIndex"] == 0


def test_play_playlist_keeps_order_without_shuffle(conn, run):
    pl_manager.create_playlist("Chill", ["t_weather", "t_dontdream"])
    run(main._tool_play_playlist(conn, make_fc("playPlaylist", name="chill")))
    cmd = commands(conn, "loadAndPlayPlaylist")[0]
    assert [t["id"] for t in cmd["tracks"]] == ["t_weather", "t_dontdream"]


def test_play_playlist_no_match_lists_available(conn, run):
    pl_manager.create_playlist("Chill", ["t_weather"])
    res = run(main._tool_play_playlist(conn, make_fc("playPlaylist", name="metal")))
    assert "No playlist found matching 'metal'" in res["result"] and "'Chill'" in res["result"]
    assert not commands(conn)


def test_queue_playlist_does_not_interrupt(conn, run):
    pl_manager.create_playlist("Chill", ["t_weather", "t_dontdream"])
    set_playing(conn, "t_billie")
    res = run(main._tool_queue_playlist(conn, make_fc("queuePlaylist", playlistName="chill")))
    assert "Added all 2 tracks of 'Chill' to the end of the queue" in res["result"]
    assert not commands(conn, "loadAndPlayPlaylist") and not commands(conn, "playTrack")
    cmd = commands(conn, "queuePlaylist")[0]
    assert [t["id"] for t in cmd["tracks"]] == ["t_weather", "t_dontdream"]
    assert cmd["playlist"] == {"id": stored("Chill")["id"], "name": "Chill"}


# ─── dispatch through the registry, like the receive loop does ───────────────

def test_handlers_dispatch_via_registry(conn, run):
    set_playing(conn, "t_billie")
    run(main.TOOL_HANDLERS["addToPlaylist"](conn, make_fc("addToPlaylist", playlistName="Bangers")))
    run(main.TOOL_HANDLERS["renamePlaylist"](conn, make_fc("renamePlaylist", playlistName="bangers", newName="Big Bangers")))
    res = run(main.TOOL_HANDLERS["describePlaylist"](conn, make_fc("describePlaylist", playlistName="big bangers")))
    assert "'Big Bangers' is a custom playlist (Created by voice) with 1 tracks" in res["result"]
    changes = [c["change"] for c in commands(conn, "playlistsChanged")]
    assert changes == ["created", "renamed"]

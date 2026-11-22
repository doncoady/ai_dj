"""
Voice tools for live radio and podcasts (main.py TOOL_HANDLERS).

Each test drives a handler like the Gemini receive loop does and checks the
spoken result, persisted state, the command pushed to the browser, and the
server's mirrored playback `source` (so same-turn summaries are truthful).
Outbound HTTP (station directory, podcast directory, feeds) is mocked.
"""

import main
from conftest import make_fc, commands, radio_manager, podcast_manager, SAMPLE_FEED_URL, SAMPLE_FEED as SAMPLE_FEED_XML


RADIO_TOOLS = ("playRadio", "listRadioStations", "saveRadioStation", "removeRadioStation")
PODCAST_TOOLS = ("playPodcast", "listPodcasts", "describePodcast", "subscribeToPodcast", "unsubscribeFromPodcast", "seekRelative", "setPlaybackSpeed")


def radio_source(conn, station, saved=True, playing=True, now_title=None):
    conn.playback.update({"playing": playing, "track_id": None, "source": {
        "type": "radio", "id": station["id"], "name": station["name"], "genre": station.get("genre", ""),
        "streamUrl": station["streamUrl"], "saved": saved, "nowTitle": now_title}})


def podcast_source(conn, pod, ep, position=None, playing=True):
    conn.playback.update({"playing": playing, "track_id": None, "source": {
        "type": "podcast", "id": pod["id"], "name": pod["title"], "episodeId": ep["id"], "episodeTitle": ep["title"],
        "episodeDescription": ep.get("description", ""), "position": position, "duration": ep.get("duration")}})


# ─── registry & prompt ────────────────────────────────────────────────────────

def test_tools_declared_registered_and_prompted(library, seeded_stations, subscribed_podcast):
    cfg = main.build_gemini_config()
    declared = {d["name"] for d in cfg["tools"][0]["function_declarations"]}
    for t in RADIO_TOOLS + PODCAST_TOOLS:
        assert t in declared and t in main.TOOL_HANDLERS, t
    instr = cfg["system_instruction"]
    assert "LIVE RADIO" in instr and "PODCASTS" in instr
    assert "`playRadio`" in instr and "`playPodcast`" in instr and "`seekRelative`" in instr
    # The DJ is told which stations / podcasts exist so it can recognise them in speech
    assert "Saved radio stations: 'FIP', 'Radio Paradise', 'Groove Salad'." in instr      # newest first, like playlists
    assert "Podcast subscriptions: 'Night Shift Radio Hour'." in instr


def test_prompt_when_nothing_is_saved(library, monkeypatch):
    monkeypatch.setattr(main, "ONLINE_DIRECTORIES", True)
    summary = main._sources_summary()
    assert "No radio stations are saved yet (the online directory can find some)." in summary
    assert "No podcast subscriptions yet (subscribeToPodcast can find shows by name)." in summary
    monkeypatch.setattr(main, "ONLINE_DIRECTORIES", False)
    assert main._sources_summary().startswith("No radio stations are saved yet. No podcast subscriptions yet.")


# ─── playRadio ────────────────────────────────────────────────────────────────

def test_play_saved_station_by_loose_name(conn, run, seeded_stations, upstream):
    res = run(main._tool_play_radio(conn, make_fc("playRadio", query="put on radio paradise")))["result"]
    assert res.startswith("Tuned in to saved station 'Radio Paradise' (Eclectic Rock, USA).")
    assert "Now playing: live radio station 'Radio Paradise' (Eclectic Rock)." in res
    assert "has not announced the current song" in res

    cmd = commands(conn, "playRadio")
    assert len(cmd) == 1 and cmd[0]["origin"] == "saved"
    assert cmd[0]["station"]["id"] == seeded_stations[1]["id"]
    assert cmd[0]["station"]["streamUrl"] == "https://rp.stream.example/mp3"
    assert conn.playback["source"]["type"] == "radio" and conn.playback["source"]["saved"] is True
    assert conn.playback["track_id"] is None and conn.playback["playing"] is True
    assert upstream.requests == []                      # no directory round-trip for a saved match


def test_play_saved_station_by_genre(conn, run, seeded_stations, upstream):
    res = run(main._tool_play_radio(conn, make_fc("playRadio", query="some jazz")))["result"]
    assert "'FIP'" in res


def test_play_radio_with_no_query_uses_last_played_then_first_saved(conn, run, seeded_stations, upstream):
    res = run(main._tool_play_radio(conn, make_fc("playRadio", query="")))["result"]
    assert "'FIP'" in res and "most recently added" in res
    radio_manager.mark_played(seeded_stations[0]["id"])
    res = run(main._tool_play_radio(conn, make_fc("playRadio", query="the radio")))["result"]
    assert "'Groove Salad'" in res and "most recently played" in res


def test_play_radio_falls_back_to_directory_and_auto_saves(conn, run, seeded_stations, upstream):
    res = run(main._tool_play_radio(conn, make_fc("playRadio", query="tsf jazz", country="France")))["result"]
    assert "searched the online directory and tuned in to 'TSF Jazz' (jazz, France)" in res
    assert "has been saved to the user's stations" in res and "remove the TSF Jazz station" in res
    assert "Other matches: 'Jazz24' (jazz, smooth, USA)" in res

    cmd = commands(conn, "playRadio")[0]
    # The relay only serves saved stations, so the station is persisted BEFORE the play command
    assert cmd["origin"] == "saved" and cmd["station"]["name"] == "TSF Jazz"
    assert cmd["station"]["favicon"] == "https://tsf.example/favicon.png"
    saved = radio_manager.get_station_by_id(cmd["station"]["id"])
    assert saved and saved["source"] == "directory"
    assert conn.playback["source"]["saved"] is True
    changed = commands(conn, "radioStationsChanged")
    assert len(changed) == 1 and changed[0]["change"] == "created"          # GUI list refreshes
    params = dict(upstream.requests[0].url.params)
    assert params["country"] == "France" and params["name"] == "tsf jazz"
    # Playing it again finds the saved copy: no directory round-trip, no duplicate
    n = len(upstream.requests)
    run(main._tool_play_radio(conn, make_fc("playRadio", query="tsf jazz")))
    assert len(upstream.requests) == n and len(radio_manager.load_stations()) == 4


def test_play_radio_warns_about_unverified_directory_stream(conn, run, upstream):
    upstream.directory_results = [dict(upstream.directory_results[0], lastcheckok=0)]
    res = run(main._tool_play_radio(conn, make_fc("playRadio", query="tsf")))["result"]
    assert "could not verify this stream recently" in res


def test_play_radio_no_match_anywhere(conn, run, seeded_stations, upstream):
    upstream.directory_results = []
    res = run(main._tool_play_radio(conn, make_fc("playRadio", query="polka hour")))["result"]
    assert "found nothing" in res and "Saved stations: 'FIP', 'Radio Paradise', 'Groove Salad'." in res
    assert not commands(conn, "playRadio")


def test_play_radio_directory_disabled(conn, run, seeded_stations, upstream, monkeypatch):
    monkeypatch.setattr(main, "ONLINE_DIRECTORIES", False)
    res = run(main._tool_play_radio(conn, make_fc("playRadio", query="bbc")))["result"]
    assert "online directory is disabled" in res and "Saved stations:" in res
    assert upstream.requests == [] and not commands(conn, "playRadio")


def test_play_radio_with_nothing_saved(conn, run, upstream, monkeypatch):
    res = run(main._tool_play_radio(conn, make_fc("playRadio")))["result"]
    assert "no saved radio stations yet" in res and "genre" in res
    monkeypatch.setattr(main, "ONLINE_DIRECTORIES", False)
    res = run(main._tool_play_radio(conn, make_fc("playRadio")))["result"]
    assert "Radio tab" in res


# ─── listRadioStations ────────────────────────────────────────────────────────

def test_list_radio_stations(conn, run, seeded_stations):
    radio_source(conn, seeded_stations[2])
    res = run(main._tool_list_radio_stations(conn, make_fc("listRadioStations")))["result"]
    assert res.startswith("3 saved radio stations: 'FIP' (Jazz, World, France) [playing now]; 'Radio Paradise' (Eclectic Rock, USA); 'Groove Salad' (Ambient, Downtempo, USA).")


def test_list_radio_stations_empty(conn, run, monkeypatch):
    monkeypatch.setattr(main, "ONLINE_DIRECTORIES", True)
    assert "jazz radio" in run(main._tool_list_radio_stations(conn, make_fc("listRadioStations")))["result"]
    monkeypatch.setattr(main, "ONLINE_DIRECTORIES", False)
    assert "Radio tab" in run(main._tool_list_radio_stations(conn, make_fc("listRadioStations")))["result"]


# ─── saveRadioStation ─────────────────────────────────────────────────────────

def test_save_currently_playing_directory_station(conn, run, upstream):
    run(main._tool_play_radio(conn, make_fc("playRadio", query="jazz")))         # directory -> TSF Jazz, auto-saved
    res = run(main._tool_save_radio_station(conn, make_fc("saveRadioStation", query="this station")))["result"]
    assert "'TSF Jazz' (jazz, France) is already in the saved stations" in res
    saved = radio_manager.load_stations()
    assert len(saved) == 1 and saved[0]["name"] == "TSF Jazz" and saved[0]["source"] == "directory"
    assert saved[0]["favicon"] == "https://tsf.example/favicon.png"
    assert conn.playback["source"]["saved"] is True


def test_save_current_station_from_browser_report_only(conn, run, upstream):
    # The browser reports a directory station (e.g. after a server restart); no in-memory record exists.
    radio_source(conn, {"id": radio_manager.station_id_for("https://x.stream.example/s"), "name": "X FM",
                        "streamUrl": "https://x.stream.example/s", "genre": "Pop"}, saved=False)
    res = run(main._tool_save_radio_station(conn, make_fc("saveRadioStation", name="My X")))["result"]
    assert res.startswith("Saved 'My X' (Pop) to your radio stations.")
    assert radio_manager.load_stations()[0]["streamUrl"] == "https://x.stream.example/s"


def test_save_current_station_already_saved_or_rename(conn, run, seeded_stations):
    radio_source(conn, seeded_stations[0])
    res = run(main._tool_save_radio_station(conn, make_fc("saveRadioStation")))["result"]
    assert "already in the saved stations" in res
    res = run(main._tool_save_radio_station(conn, make_fc("saveRadioStation", query="", name="Salad Bar")))["result"]
    assert res == "Renamed saved station 'Groove Salad' to 'Salad Bar'."
    assert radio_manager.get_station_by_id(seeded_stations[0]["id"])["name"] == "Salad Bar"
    assert commands(conn, "radioStationsChanged")[0]["change"] == "updated"


def test_save_station_nothing_playing(conn, run):
    res = run(main._tool_save_radio_station(conn, make_fc("saveRadioStation")))["result"]
    assert "No radio station is playing" in res


def test_save_station_by_directory_search_does_not_play(conn, run, upstream):
    res = run(main._tool_save_radio_station(conn, make_fc("saveRadioStation", query="a jazz station")))["result"]
    assert res.startswith("Saved 'TSF Jazz' (jazz, France) to your radio stations. Playback was not changed; say 'play TSF Jazz' to tune in.")
    assert not commands(conn, "playRadio")
    assert conn.playback["source"] is None
    # Saving the same one again is idempotent
    res = run(main._tool_save_radio_station(conn, make_fc("saveRadioStation", query="tsf jazz")))["result"]
    assert "already saved" in res and len(radio_manager.load_stations()) == 1


def test_save_station_directory_empty_or_disabled(conn, run, upstream, monkeypatch):
    upstream.directory_results = []
    assert "Nothing was saved" in run(main._tool_save_radio_station(conn, make_fc("saveRadioStation", query="zzz")))["result"]
    monkeypatch.setattr(main, "ONLINE_DIRECTORIES", False)
    assert "Radio tab" in run(main._tool_save_radio_station(conn, make_fc("saveRadioStation", query="zzz")))["result"]


# ─── removeRadioStation ───────────────────────────────────────────────────────

def test_remove_station_by_name_and_current(conn, run, seeded_stations):
    res = run(main._tool_remove_radio_station(conn, make_fc("removeRadioStation", name="the paradise station")))["result"]
    assert res == "Removed 'Radio Paradise' (Eclectic Rock, USA) from your saved stations."
    assert radio_manager.get_station_by_id(seeded_stations[1]["id"]) is None
    assert commands(conn, "radioStationsChanged")[0]["change"] == "deleted"

    radio_source(conn, seeded_stations[2])
    res = run(main._tool_remove_radio_station(conn, make_fc("removeRadioStation", name="current")))["result"]
    assert "Removed 'FIP'" in res and "keeps playing" in res
    assert conn.playback["source"]["saved"] is False

    res = run(main._tool_remove_radio_station(conn, make_fc("removeRadioStation", name="nonexistent fm")))["result"]
    assert "No saved station matches 'nonexistent fm'." in res and "'Groove Salad'" in res


def test_remove_current_when_nothing_or_unsaved_playing(conn, run, seeded_stations):
    res = run(main._tool_remove_radio_station(conn, make_fc("removeRadioStation", name="this station")))["result"]
    assert "ambiguous" in res
    radio_source(conn, {"id": "rs_dir", "name": "Dir FM", "streamUrl": "https://d.stream.example/x"}, saved=False)
    res = run(main._tool_remove_radio_station(conn, make_fc("removeRadioStation", name="this")))["result"]
    assert "not in the saved list" in res


# ─── now playing / summaries with a radio source ──────────────────────────────

def test_describe_now_playing_radio_with_icy_title(conn, run, seeded_stations):
    st = seeded_stations[0]
    radio_source(conn, st)
    main.radio_now_playing[st["id"]] = {"title": "Boards of Canada - Dayvan Cowboy", "updatedAt": 1}
    res = run(main._tool_describe_now_playing(conn, make_fc("describeNowPlaying")))["result"]
    assert res.startswith("Now playing: live radio station 'Groove Salad' (Ambient, Downtempo). On air right now: 'Boards of Canada - Dayvan Cowboy'.")
    assert "no local track" in res
    assert conn.now_playing() is None                      # no library track while radio plays
    assert conn.playback_summary().startswith("Now playing: live radio station 'Groove Salad'")


def test_describe_now_playing_radio_uses_browser_reported_title_as_fallback(conn, run, seeded_stations):
    radio_source(conn, seeded_stations[0], now_title="Tycho - Awake", playing=False)
    res = run(main._tool_describe_now_playing(conn, make_fc("describeNowPlaying")))["result"]
    assert res.startswith("Now paused: live radio station 'Groove Salad' (Ambient, Downtempo). On air right now: 'Tycho - Awake'.")


def test_playback_summary_mentions_held_playlist_and_queue(conn, run, seeded_stations):
    radio_source(conn, seeded_stations[0])
    conn.playback.update({"queue": [{"id": "t_heaven"}], "playlist": {"id": "p", "name": "Road Trip"}})
    summary = conn.playback_summary()
    assert "Playlist 'Road Trip' is on hold; its remaining songs are still cued." in summary
    assert "Next in queue: 'Heaven' by Bryan Adams." in summary
    res = run(main._tool_describe_now_playing(conn, make_fc("describeNowPlaying")))["result"]
    assert "Up next in the queue: 'Heaven' by Bryan Adams." in res


def test_this_song_during_radio_cannot_be_added_to_playlist(conn, run, seeded_stations):
    radio_source(conn, seeded_stations[0])
    res = run(main._tool_add_to_playlist(conn, make_fc("addToPlaylist", playlistName="Faves")))["result"]
    assert "A live radio station is playing, not a library song" in res
    assert main.pl_manager.load_playlists() == []          # nothing auto-created


def test_seek_relative_refused_for_radio(conn, run, seeded_stations):
    radio_source(conn, seeded_stations[0])
    res = run(main._tool_seek_relative(conn, make_fc("seekRelative", seconds=30)))["result"]
    assert "Live radio cannot be skipped" in res and not commands(conn, "seekRelative")


# ─── subscribeToPodcast ───────────────────────────────────────────────────────

def test_subscribe_via_directory(conn, run, upstream):
    res = run(main._tool_subscribe_to_podcast(conn, make_fc("subscribeToPodcast", query="night shift")))["result"]
    assert res.startswith("Subscribed to 'Night Shift Radio Hour' by Dana Rivers (3 episodes). Latest episode: 'Episode 12: The Last Train' (episode 12) [1h 02m] from Mar 04, 2025.")
    assert "Playback was not changed." in res
    assert "Other directory matches were: 'Night Owls' by Someone Else." in res
    pods = podcast_manager.load_podcasts()
    assert len(pods) == 1 and pods[0]["feedUrl"] == SAMPLE_FEED_URL
    cmd = commands(conn, "podcastsChanged")
    assert len(cmd) == 1 and cmd[0]["change"] == "created" and cmd[0]["podcast"]["episodeCount"] == 3
    assert "episodes" not in cmd[0]["podcast"]            # summary only
    assert not commands(conn, "playPodcastEpisode")
    hosts = [r.url.host for r in upstream.requests]
    assert hosts == ["itunes.apple.com", "feeds.example.com"]


def test_subscribe_via_feed_url_and_duplicates(conn, run, upstream):
    res = run(main._tool_subscribe_to_podcast(conn, make_fc("subscribeToPodcast", query=SAMPLE_FEED_URL)))["result"]
    assert res.startswith("Subscribed to 'Night Shift Radio Hour'")
    assert [r.url.host for r in upstream.requests] == ["feeds.example.com"]
    res = run(main._tool_subscribe_to_podcast(conn, make_fc("subscribeToPodcast", query="the night shift podcast")))["result"]
    assert res == "Already subscribed to 'Night Shift Radio Hour'. Say 'play Night Shift Radio Hour' to listen."


def test_subscribe_failures(conn, run, upstream, monkeypatch):
    assert "need the podcast's name" in run(main._tool_subscribe_to_podcast(conn, make_fc("subscribeToPodcast", query=" ")))["result"]
    upstream.itunes_results = []
    assert "found nothing" in run(main._tool_subscribe_to_podcast(conn, make_fc("subscribeToPodcast", query="xyz")))["result"]
    upstream.itunes_results = list(__import__("conftest").ITUNES_PODCASTS)
    upstream.feed_status = 500
    res = run(main._tool_subscribe_to_podcast(conn, make_fc("subscribeToPodcast", query="night shift")))["result"]
    assert "could not load its feed: Feed server answered HTTP 500." in res and podcast_manager.load_podcasts() == []
    monkeypatch.setattr(main, "ONLINE_DIRECTORIES", False)
    assert "directory is disabled" in run(main._tool_subscribe_to_podcast(conn, make_fc("subscribeToPodcast", query="night shift")))["result"]


# ─── playPodcast ──────────────────────────────────────────────────────────────

def test_play_podcast_latest_by_default(conn, run, upstream, subscribed_podcast):
    res = run(main._tool_play_podcast(conn, make_fc("playPodcast", podcast="the night shift")))["result"]
    assert res.startswith("Playing 'Episode 12: The Last Train' (episode 12) [1h 02m] from Mar 04, 2025 from 'Night Shift Radio Hour'.")
    assert "resumes where the user left off" in res
    assert "Episode notes: A conductor who never sleeps." in res
    assert "Now playing: podcast episode 'Episode 12: The Last Train' from 'Night Shift Radio Hour'." in res

    cmd = commands(conn, "playPodcastEpisode")
    assert len(cmd) == 1
    assert cmd[0]["podcast"]["id"] == subscribed_podcast["id"] and "episodes" not in cmd[0]["podcast"]
    assert cmd[0]["episode"]["audioUrl"] == "https://cdn.example/ns/12.mp3"
    src = conn.playback["source"]
    assert src["type"] == "podcast" and src["episodeId"] == cmd[0]["episode"]["id"] and src["duration"] == 3723
    assert podcast_manager.get_podcast_by_id(subscribed_podcast["id"])["lastPlayedAt"] > 0
    assert upstream.requests == []                       # fresh feed: no refresh


def test_play_podcast_specific_and_relative_episodes(conn, run, upstream, subscribed_podcast):
    res = run(main._tool_play_podcast(conn, make_fc("playPodcast", podcast="night shift", episode="episode 11")))["result"]
    assert "'Episode 11: Sleep Science'" in res
    # "next" is relative to what is playing now (newer)
    res = run(main._tool_play_podcast(conn, make_fc("playPodcast", podcast="current", episode="the next one")))["result"]
    assert "'Episode 12: The Last Train'" in res
    res = run(main._tool_play_podcast(conn, make_fc("playPodcast", podcast="this podcast", episode="previous")))["result"]
    assert "'Episode 11: Sleep Science'" in res
    res = run(main._tool_play_podcast(conn, make_fc("playPodcast", podcast="", episode="the one about city lights")))["result"]
    assert "'Episode 10: City Lights'" in res


def test_play_podcast_refreshes_stale_feed(conn, run, upstream, subscribed_podcast):
    pods = podcast_manager.load_podcasts()
    pods[0]["lastFetchedAt"] = 1
    podcast_manager.save_podcasts(pods)
    upstream.feed_xml = __import__("conftest").SAMPLE_FEED.replace("Episode 12: The Last Train", "Episode 13: Brand New")
    res = run(main._tool_play_podcast(conn, make_fc("playPodcast", podcast="night shift")))["result"]
    assert "'Episode 13: Brand New'" in res
    assert [r.url.host for r in upstream.requests] == ["feeds.example.com"]
    assert not podcast_manager.is_stale(podcast_manager.get_podcast_by_id(subscribed_podcast["id"]))


def test_play_podcast_stale_feed_unreachable_uses_cache(conn, run, upstream, subscribed_podcast):
    pods = podcast_manager.load_podcasts()
    pods[0]["lastFetchedAt"] = 1
    podcast_manager.save_podcasts(pods)
    upstream.feed_status = 503
    res = run(main._tool_play_podcast(conn, make_fc("playPodcast", podcast="night shift")))["result"]
    assert "'Episode 12: The Last Train'" in res
    assert "could not be refreshed — Feed server answered HTTP 503." in res and "cached episode list" in res
    assert len(commands(conn, "playPodcastEpisode")) == 1


def test_play_podcast_unknown_or_ambiguous(conn, run, upstream, subscribed_podcast):
    res = run(main._tool_play_podcast(conn, make_fc("playPodcast", podcast="cooking with fire")))["result"]
    assert "No subscribed podcast matches 'cooking with fire'." in res
    assert "Subscriptions: 'Night Shift Radio Hour'." in res and "subscribeToPodcast" in res
    assert not commands(conn, "playPodcastEpisode")

    # With two subscriptions and nothing playing, 'current' is ambiguous
    other = podcast_manager.parse_feed(__import__("conftest").SAMPLE_FEED.replace("Night Shift Radio Hour", "Other Show"), "https://feeds.example.com/other.xml")
    podcast_manager.upsert_podcast(other)
    res = run(main._tool_play_podcast(conn, make_fc("playPodcast", podcast="current")))["result"]
    assert "No podcast is playing right now" in res and "'Other Show'" in res

    res = run(main._tool_play_podcast(conn, make_fc("playPodcast", podcast="night shift", episode="episode 77")))["result"]
    assert "No episode number 77" in res


def test_play_podcast_with_no_subscriptions(conn, run, upstream):
    res = run(main._tool_play_podcast(conn, make_fc("playPodcast", podcast="anything")))["result"]
    assert "There are no podcast subscriptions yet." in res


# ─── listPodcasts / describePodcast ───────────────────────────────────────────

def test_list_and_describe_podcasts(conn, run, upstream, subscribed_podcast):
    res = run(main._tool_list_podcasts(conn, make_fc("listPodcasts")))["result"]
    assert res == "1 podcast subscription: 'Night Shift Radio Hour' by Dana Rivers (3 episodes, latest: 'Episode 12: The Last Train')."

    res = run(main._tool_describe_podcast(conn, make_fc("describePodcast", podcast="night shift")))["result"]
    assert res.startswith("'Night Shift Radio Hour' by Dana Rivers has 3 episodes, newest first: 1. 'Episode 12: The Last Train' (episode 12) [1h 02m] from Mar 04, 2025; 2. 'Episode 11: Sleep Science' (episode 11) [42 min] from Feb 25, 2025; 3. 'Episode 10: City Lights' (episode 10) [45 min] from Feb 18, 2025.")
    assert "About: Late-night stories and music." in res
    assert "The latest episode is about: A conductor who never sleeps." in res
    assert res.endswith("This listing is authoritative.")


def test_list_podcasts_empty(conn, run, monkeypatch):
    monkeypatch.setattr(main, "ONLINE_DIRECTORIES", True)
    assert "subscribe to" in run(main._tool_list_podcasts(conn, make_fc("listPodcasts")))["result"]
    monkeypatch.setattr(main, "ONLINE_DIRECTORIES", False)
    assert "feed URL" in run(main._tool_list_podcasts(conn, make_fc("listPodcasts")))["result"]


# ─── unsubscribeFromPodcast ───────────────────────────────────────────────────

def test_unsubscribe(conn, run, upstream, subscribed_podcast):
    ep = subscribed_podcast["episodes"][0]
    podcast_source(conn, subscribed_podcast, ep)
    res = run(main._tool_unsubscribe_from_podcast(conn, make_fc("unsubscribeFromPodcast", podcast="current")))["result"]
    assert res == "Unsubscribed from 'Night Shift Radio Hour'. The current episode keeps playing."
    assert podcast_manager.load_podcasts() == []
    cmd = commands(conn, "podcastsChanged")
    assert cmd[0]["change"] == "deleted" and cmd[0]["podcastId"] == subscribed_podcast["id"]
    res = run(main._tool_unsubscribe_from_podcast(conn, make_fc("unsubscribeFromPodcast", podcast="night shift")))["result"]
    assert "No subscribed podcast matches" in res


# ─── seekRelative & now playing for podcasts ──────────────────────────────────

def test_seek_relative_podcast(conn, run, subscribed_podcast):
    ep = subscribed_podcast["episodes"][0]           # 3723 s long
    podcast_source(conn, subscribed_podcast, ep, position=600)
    res = run(main._tool_seek_relative(conn, make_fc("seekRelative", seconds=30)))["result"]
    assert res == "Skipped ahead 30 seconds. Now at 10:30 of 1:02:03."
    assert commands(conn, "seekRelative")[0]["seconds"] == 30
    res = run(main._tool_seek_relative(conn, make_fc("seekRelative", seconds=-90)))["result"]
    assert res == "Skipped back 1:30. Now at 9:00 of 1:02:03."
    res = run(main._tool_seek_relative(conn, make_fc("seekRelative", seconds=-99999)))["result"]
    assert res == "Skipped back 1:00:00. Now at 0:00 of 1:02:03."           # clamped both ways
    res = run(main._tool_seek_relative(conn, make_fc("seekRelative", seconds=99999)))["result"]
    assert "Now at 1:00:00 of 1:02:03." in res


def test_seek_relative_edge_cases(conn, run):
    assert "how far" in run(main._tool_seek_relative(conn, make_fc("seekRelative", seconds=0)))["result"]
    assert "Nothing is playing" in run(main._tool_seek_relative(conn, make_fc("seekRelative", seconds=30)))["result"]
    # Works for library tracks too (no position bookkeeping, just the command)
    conn.playback.update({"playing": True, "track_id": "t_billie"})
    res = run(main._tool_seek_relative(conn, make_fc("seekRelative", seconds=15)))["result"]
    assert res == "Skipped ahead 15 seconds." and len(commands(conn, "seekRelative")) == 1


def test_describe_now_playing_podcast(conn, run, subscribed_podcast):
    ep = subscribed_podcast["episodes"][1]
    podcast_source(conn, subscribed_podcast, ep, position=754, playing=False)
    conn.playback["queue"] = [{"id": "t_summer"}]
    res = run(main._tool_describe_now_playing(conn, make_fc("describeNowPlaying")))["result"]
    assert res.startswith("Now paused: podcast episode 'Episode 11: Sleep Science' from 'Night Shift Radio Hour', at 12:34 of 42:00.")
    assert "Up next in the queue: 'Summer of '69' by Bryan Adams." in res


def test_current_source_ignores_malformed_reports(conn):
    conn.playback["source"] = {"type": "television"}
    assert conn.current_source() is None
    conn.playback["source"] = "radio"
    assert conn.current_source() is None and conn.source_summary() == ""
    conn.playback.update({"source": None, "track_id": "t_billie", "playing": True})
    assert conn.now_playing()["title"] == "Billie Jean"


# ─── starter picks guidance ───────────────────────────────────────────────────

def test_list_tools_explain_starter_picks(conn, run, upstream, sources_files):
    sources_files["stations"].unlink()
    sources_files["podcasts"].unlink()
    res = run(main._tool_list_radio_stations(conn, make_fc("listRadioStations")))["result"]
    assert f"{len(radio_manager.DEFAULT_STATIONS)} saved radio stations" in res
    assert "starter picks pre-loaded on first run" in res and "'save a jazz station'" in res and "Find Stations" in res

    res = run(main._tool_list_podcasts(conn, make_fc("listPodcasts")))["result"]
    assert "starter picks pre-loaded on first run" in res and "'subscribe to <podcast name>'" in res
    assert "episodes not loaded yet" in res

    # A starter whose feed was never fetched and cannot be loaded explains why (no "try refreshing" dead end)
    res = run(main._tool_play_podcast(conn, make_fc("playPodcast", podcast="Up First")))["result"]
    assert res.startswith("Could not load episodes for 'Up First': Feed server answered HTTP 404.")
    assert not commands(conn, "playPodcastEpisode")

    # …and is loaded on demand when the feed is reachable
    upstream.serve_all_feeds = True
    res = run(main._tool_play_podcast(conn, make_fc("playPodcast", podcast="Up First")))["result"]
    assert "Playing 'Episode 12: The Last Train'" in res            # mock upstream serves the sample feed
    assert commands(conn, "playPodcastEpisode")[0]["podcast"]["source"] == "default"


def test_prompt_mentions_starter_picks(library):
    assert "STARTER PICKS" in main.build_gemini_config()["system_instruction"]


# ─── setPlaybackSpeed ─────────────────────────────────────────────────────────

def test_set_playback_speed_for_podcasts(conn, run, subscribed_podcast):
    ep = subscribed_podcast["episodes"][0]
    podcast_source(conn, subscribed_podcast, ep, position=100)
    res = run(main._tool_set_playback_speed(conn, make_fc("setPlaybackSpeed", rate=1.5)))["result"]
    assert res == "Playing 'Episode 12: The Last Train' at 1.5x."
    assert commands(conn, "setPlaybackRate")[0]["rate"] == 1.5
    assert "at 1.5x speed" in conn.source_summary()

    # No rate: one notch faster than now (1.5 -> 1.75); clamps at the top
    res = run(main._tool_set_playback_speed(conn, make_fc("setPlaybackSpeed")))["result"]
    assert "at 1.75x" in res
    conn.playback["playbackRate"] = 2.0
    assert "at 2x" in run(main._tool_set_playback_speed(conn, make_fc("setPlaybackSpeed")))["result"]
    # Out of range values clamp; 1 reads as normal
    assert commands(conn, "setPlaybackRate")[-1]["rate"] == 2.0
    run(main._tool_set_playback_speed(conn, make_fc("setPlaybackSpeed", rate=9)))
    assert commands(conn, "setPlaybackRate")[-1]["rate"] == 3.0
    res = run(main._tool_set_playback_speed(conn, make_fc("setPlaybackSpeed", rate=1)))["result"]
    assert res.startswith("Back to normal speed (1x)") and "speed" not in conn.source_summary()
    assert "did not catch" in run(main._tool_set_playback_speed(conn, make_fc("setPlaybackSpeed", rate="fast")))["result"]


def test_set_playback_speed_refused_for_music_and_radio(conn, run, seeded_stations):
    conn.playback.update({"playing": True, "track_id": "t_billie"})
    res = run(main._tool_set_playback_speed(conn, make_fc("setPlaybackSpeed", rate=1.5)))["result"]
    assert "only applies to podcast" in res and not commands(conn, "setPlaybackRate")
    radio_source(conn, seeded_stations[0])
    res = run(main._tool_set_playback_speed(conn, make_fc("setPlaybackSpeed", rate=1.5)))["result"]
    assert "Live radio always plays at normal speed" in res and not commands(conn, "setPlaybackRate")


# ─── setVolume ────────────────────────────────────────────────────────────────

def test_set_volume_absolute_relative_and_mute(conn, run):
    conn.playback["volume"] = 0.6
    assert run(main._tool_set_volume(conn, make_fc("setVolume", level=40)))["result"] == "Volume down: now 40% (was 60%)."
    assert commands(conn, "setVolume")[-1]["level"] == 0.4
    assert run(main._tool_set_volume(conn, make_fc("setVolume", change=15)))["result"] == "Volume up: now 55% (was 40%)."
    assert run(main._tool_set_volume(conn, make_fc("setVolume")))["result"] == "Volume up: now 70% (was 55%)."      # default nudge
    assert run(main._tool_set_volume(conn, make_fc("setVolume", level=0)))["result"].startswith("Muted the music")
    assert run(main._tool_set_volume(conn, make_fc("setVolume", level=250)))["result"] == "Volume up: now 100% (was 0%)."
    assert run(main._tool_set_volume(conn, make_fc("setVolume", level=0.5)))["result"] == "Volume down: now 50% (was 100%)."   # fraction accepted
    assert commands(conn, "setVolume")[-1]["level"] == 0.5
    assert "setVolume" in {d["name"] for d in main.build_gemini_config()["tools"][0]["function_declarations"]}
    assert "VOLUME:" in main.build_gemini_config()["system_instruction"]


# ─── second directory (TuneIn) & city search ──────────────────────────────────

def test_play_radio_finds_local_station_via_tunein_when_community_directory_lacks_it(conn, run, upstream):
    from conftest import TUNEIN_JACK, TUNEIN_MOVE
    upstream.directory_results = []                       # radio-browser knows nothing about it
    upstream.tunein_results = [{"element": "outline", "text": "Stations", "key": "stations", "children": [TUNEIN_JACK, TUNEIN_MOVE]}]
    res = run(main._tool_play_radio(conn, make_fc("playRadio", query="jack 92.9", place="Halifax")))["result"]
    assert "tuned in to 'Jack 92.9' (Adult Hits)" in res and "(Found via TuneIn.)" in res

    saved = radio_manager.load_stations()[0]
    assert saved["provider"] == "tunein" and saved["streamUrl"].startswith("https://opml.radiotime.com/Tune.ashx?id=s22216")
    assert saved["resolvedUrl"] == "https://tunein-cdn.stream.example/live.aac"      # resolved to a direct (non-HLS) stream at save time
    assert saved["favicon"].endswith("logo.jpg") and saved["genre"] == "Adult Hits"
    cmd = commands(conn, "playRadio")[0]
    assert cmd["station"]["id"] == saved["id"]
    hosts = [r.url.host for r in upstream.requests]
    assert "opml.radiotime.com" in hosts and hosts.count("opml.radiotime.com") >= 2   # search + resolve


def test_play_radio_tunein_disabled_or_empty(conn, run, upstream, monkeypatch):
    from conftest import TUNEIN_JACK
    upstream.directory_results = []
    upstream.tunein_results = [TUNEIN_JACK]
    monkeypatch.setattr(main, "TUNEIN_DIRECTORY", False)
    res = run(main._tool_play_radio(conn, make_fc("playRadio", query="jack 92.9")))["result"]
    assert "found nothing" in res and "frequency or call sign" in res
    assert not any(r.url.host == "opml.radiotime.com" for r in upstream.requests)


def test_play_radio_place_is_passed_to_directory(conn, run, upstream):
    upstream.directory_results = [dict(upstream.directory_results[0], name="Jazz Halifax", state="Nova Scotia")]
    res = run(main._tool_play_radio(conn, make_fc("playRadio", query="jazz", place="Halifax")))["result"]
    assert "'Jazz Halifax'" in res
    params = [dict(r.url.params) for r in upstream.requests if r.url.host.endswith("radio-browser.info")]
    assert any(p.get("state") == "Halifax" for p in params) and any(p.get("tag") == "halifax" for p in params)


# ─── newest episodes workflow ─────────────────────────────────────────────────

def test_list_and_play_latest_episodes(conn, run, subscribed_podcast):
    res = run(main._tool_list_latest_episodes(conn, make_fc("listLatestEpisodes")))["result"]
    assert res.startswith("The 1 newest episodes across your podcasts, newest first: 1. 'Episode 12: The Last Train' (episode 12) [1h 02m] from Mar 04, 2025 from 'Night Shift Radio Hour'.")

    res = run(main._tool_play_latest_episodes(conn, make_fc("playLatestEpisodes", count=3)))["result"]
    assert res.startswith("Playing the newest episode: 'Episode 12: The Last Train' (episode 12) [1h 02m] from Mar 04, 2025 from 'Night Shift Radio Hour'.")
    play = commands(conn, "playPodcastEpisode")
    assert len(play) == 1 and play[0]["episode"]["id"] == subscribed_podcast["episodes"][0]["id"]
    assert commands(conn, "queuePodcastEpisodes") == []                   # only one show subscribed -> nothing to cue
    assert conn.playback["source"]["type"] == "podcast" and conn.playback["source"]["episodeId"] == subscribed_podcast["episodes"][0]["id"]

    # A second show: the older newest episode gets cued after the first
    other = podcast_manager.parse_feed(SAMPLE_FEED_XML.replace("Night Shift Radio Hour", "Other Show").replace("ns-12", "os-1")
                                       .replace("Tue, 04 Mar 2025", "Mon, 03 Mar 2025"), "https://feeds.example.com/other.xml")
    podcast_manager.upsert_podcast(other)
    res = run(main._tool_play_latest_episodes(conn, make_fc("playLatestEpisodes")))["result"]
    assert "Cued after it: 'Episode 12: The Last Train' (episode 12) [1h 02m] from Mar 03, 2025 from 'Other Show'." in res
    q = commands(conn, "queuePodcastEpisodes")
    assert len(q) == 1 and q[0]["items"][0]["podcast"]["title"] == "Other Show"
    assert [x["artist"] for x in conn.playback["queue"]] == ["Other Show"]

    # queueOnly cues everything without a play command
    conn.ws.sent.clear()
    conn.playback["queue"] = []
    res = run(main._tool_play_latest_episodes(conn, make_fc("playLatestEpisodes", queueOnly=True)))["result"]
    assert res.startswith("Cued 2 newest episodes:") and "Playback was not interrupted." in res
    assert commands(conn, "playPodcastEpisode") == [] and len(commands(conn, "queuePodcastEpisodes")[0]["items"]) == 2
    assert len(conn.playback["queue"]) == 2


def test_latest_episodes_with_nothing_loaded(conn, run):
    assert run(main._tool_list_latest_episodes(conn, make_fc("listLatestEpisodes")))["result"] == "There are no podcast subscriptions yet."
    assert "No podcast episodes are available" in run(main._tool_play_latest_episodes(conn, make_fc("playLatestEpisodes")))["result"]
    cfg = main.build_gemini_config()
    declared = {d["name"] for d in cfg["tools"][0]["function_declarations"]}
    assert {"listLatestEpisodes", "playLatestEpisodes"} <= declared and "`playLatestEpisodes`" in cfg["system_instruction"]

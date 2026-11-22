"""
Streaming tab: online sources as sub-sections.

  * Apple Music (iTunes Search) and Deezer previews — keyless, mocked upstream
  * YouTube Music full songs through the third-party tools ytmusicapi + yt-dlp
    (stubbed; availability toggled with the `no_ytmusic` fixture)
  * the HTTP API (status, parallel search, album tracks, track, audio relay)
  * the voice tools playStream / queueStream and the shared now-playing, seek
    and speed tools
No network access.
"""

import httpx
import pytest
from fastapi.testclient import TestClient

import main
from conftest import (make_fc, commands, streaming_manager, IT_BLINDING, IT_BRIGHTSIDE, IT_DISCOVERY, DZ_TRACK, DZ_ALBUM,
                      YT_SEARCH_RESULTS, MISSING_MODULES)

STREAM_TOOLS = ("playStream", "queueStream")


def stream_source(conn, track, position=None, context=None, playing=True):
    conn.playback.update({"playing": playing, "track_id": None, "source": {
        "type": "stream", "id": track["id"], "title": track["title"], "artist": track["artist"], "album": track.get("album", ""),
        "provider": track["provider"], "full": bool(track.get("full")), "position": position,
        "duration": round(track["durationMs"] / 1000) if track.get("full") else 30,
        "contextType": (context or {}).get("type"), "contextName": (context or {}).get("name")}})


# ─── pure helpers ─────────────────────────────────────────────────────────────

def test_normalizers():
    ap = streaming_manager.apple_track(IT_BLINDING)
    assert ap["id"] == "ap_1488408555" and ap["provider"] == "apple" and ap["kind"] == "stream" and ap["full"] is False
    assert ap["title"] == "Blinding Lights" and ap["album"] == "After Hours" and ap["albumArt"] == "https://is1.mzstatic.example/300x300bb.jpg"
    assert ap["durationMs"] == 200040 and ap["audioUrl"].endswith(".m4a") and ap["explicit"] is False
    assert streaming_manager.apple_track(IT_BRIGHTSIDE)["audioUrl"] is None and streaming_manager.apple_track(IT_BRIGHTSIDE)["explicit"] is True
    assert streaming_manager.apple_track({"wrapperType": "collection", "collectionId": 1}) is None
    al = streaming_manager.apple_album(IT_DISCOVERY)
    assert al["id"] == "apa_697194953" and al["name"] == "Discovery" and al["artist"] == "Daft Punk" and al["releaseDate"] == "2001-03-12"

    dz = streaming_manager.deezer_track(DZ_TRACK)
    assert dz["id"] == "dz_3135556" and dz["provider"] == "deezer" and dz["durationMs"] == 224000 and dz["audioUrl"].endswith(".mp3")
    dza = streaming_manager.deezer_album(DZ_ALBUM)
    assert dza["id"] == "dza_302127" and dza["trackCount"] == 2 and dza["artist"] == "Daft Punk"

    yt = streaming_manager.ytmusic_track(YT_SEARCH_RESULTS[0])
    assert yt["id"] == "yt_FGBhQbmPwH8" and yt["provider"] == "ytmusic" and yt["full"] is True and yt["audioUrl"] is None
    assert yt["title"] == "One More Time" and yt["artist"] == "Daft Punk" and yt["album"] == "Discovery" and yt["durationMs"] == 320000
    assert yt["albumArt"] == "https://lh3.googleusercontent.example/s544" and yt["url"] == "https://music.youtube.com/watch?v=FGBhQbmPwH8"
    assert streaming_manager.ytmusic_track(YT_SEARCH_RESULTS[1])["durationMs"] == 212000      # parsed from "3:32"
    assert streaming_manager.ytmusic_track({"title": "no id"}) is None

    assert streaming_manager.playable(ap) and not streaming_manager.playable(streaming_manager.apple_track(IT_BRIGHTSIDE)) and streaming_manager.playable(yt)
    assert streaming_manager.track_label(ap) == "'Blinding Lights' by The Weeknd (album 'After Hours')"
    assert streaming_manager.mode_note(ap) == "It plays the 30-second preview clip from Apple Music through the deck."
    assert streaming_manager.mode_note(yt) == "It streams the full song from YouTube Music through the deck."
    assert "cannot be played" in streaming_manager.mode_note(streaming_manager.apple_track(IT_BRIGHTSIDE))


def test_provider_status_reports_missing_tools(no_ytmusic):
    st = streaming_manager.status()
    by_id = {p["id"]: p for p in st["providers"]}
    assert [p["id"] for p in st["providers"]] == ["ytmusic", "apple", "deezer"]           # YouTube Music first
    assert by_id["apple"]["available"] and by_id["deezer"]["available"] and by_id["apple"]["full"] is False
    assert by_id["ytmusic"]["available"] is False and by_id["ytmusic"]["missing"] == ["ytmusicapi", "yt_dlp"]
    assert by_id["ytmusic"]["reason"] == "Not installed — run: pip install ytmusicapi yt-dlp"
    assert streaming_manager.available_providers() == ["apple", "deezer"]
    MISSING_MODULES.clear()
    assert streaming_manager.provider_status("ytmusic")["available"] is True
    assert streaming_manager.available_providers() == ["ytmusic", "apple", "deezer"]


# ─── provider search ──────────────────────────────────────────────────────────

def test_search_each_provider(run, catalog, upstream):
    client = upstream.client()
    ap = run(streaming_manager.search_provider("apple", "blinding lights", client, limit=5))
    assert ap["error"] is None and [t["title"] for t in ap["tracks"]] == ["Blinding Lights", "Mr. Brightside"] and [a["name"] for a in ap["albums"]] == ["Discovery"]
    req = next(r for r in upstream.requests if r.url.host == "itunes.apple.com")
    assert dict(req.url.params) == {"term": "blinding lights", "media": "music", "entity": "song", "limit": "5"}
    assert streaming_manager.track_cache["ap_1488408555"]["title"] == "Blinding Lights"          # remembered for the relay

    dz = run(streaming_manager.search_provider("deezer", "harder better", client))
    assert [t["title"] for t in dz["tracks"]] == ["Harder, Better, Faster, Stronger"] and [a["name"] for a in dz["albums"]] == ["Discovery"]

    yt = run(streaming_manager.search_provider("ytmusic", "daft punk", client))
    assert [t["title"] for t in yt["tracks"]] == ["One More Time", "Aerodynamic"] and yt["albums"] == [] and yt["tracks"][0]["full"] is True

    assert run(streaming_manager.search_provider("apple", "", client)) == {"provider": "apple", "tracks": [], "albums": [], "error": None, "offset": 0, "limit": 10, "hasMore": False}
    assert run(streaming_manager.search_provider("nope", "x", client))["tracks"] == []

    run(streaming_manager.search_provider("apple", "daft punk", client, artist_only=True))
    req = [r for r in upstream.requests if r.url.host == "itunes.apple.com"][-1]
    assert req.url.params["attribute"] == "artistTerm"
    run(streaming_manager.search_provider("deezer", "daft punk", client, artist_only=True))
    req = [r for r in upstream.requests if r.url.host == "api.deezer.com"][-1]
    assert req.url.params["q"] == 'artist:"daft punk"'


def test_search_errors_are_per_provider(run, catalog, upstream, no_ytmusic, monkeypatch):
    client = upstream.client()
    catalog.itunes_status = 500
    res = run(streaming_manager.search_all("x", client))
    by = {r["provider"]: r for r in res}
    assert by["apple"]["tracks"] == [] and by["apple"]["error"] is None             # HTTP 500 = empty, not an exception
    assert by["deezer"]["tracks"] and by["ytmusic"]["error"].startswith("Not installed")
    upstream.raise_on = httpx.ConnectError
    res = run(streaming_manager.search_provider("deezer", "x", upstream.client()))
    assert "unreachable" in res["error"]
    MISSING_MODULES.clear()
    monkeypatch.setattr(streaming_manager, "ytmusic_search_sync", lambda q, n: (_ for _ in ()).throw(RuntimeError("boom")))
    res = run(streaming_manager.search_provider("ytmusic", "x", client))
    assert res["error"] == "YouTube Music search failed: boom"


def test_search_all_runs_every_provider_in_order(run, catalog, upstream):
    res = run(streaming_manager.search_all("discovery", upstream.client(), limit=3))
    assert [r["provider"] for r in res] == ["ytmusic", "apple", "deezer"]
    res = run(streaming_manager.search_all("discovery", upstream.client(), providers=["deezer", "bogus"]))
    assert [r["provider"] for r in res] == ["deezer"]


def test_search_paging_for_more_results(run, catalog, upstream):
    """hasMore = a full page came back; offset maps to each API's own paging."""
    client = upstream.client()
    page = run(streaming_manager.search_provider("apple", "daft punk", client, limit=2))
    assert page["hasMore"] is True and page["offset"] == 0 and page["limit"] == 2
    nxt = run(streaming_manager.search_provider("apple", "daft punk", client, limit=2, offset=2))
    req = [r for r in upstream.requests if r.url.host == "itunes.apple.com"][-1]
    assert req.url.params["offset"] == "2" and req.url.params["entity"] == "song"
    assert nxt["offset"] == 2 and nxt["albums"] == []                                # albums only on the first page
    run(streaming_manager.search_provider("deezer", "daft punk", client, limit=1, offset=5))
    req = [r for r in upstream.requests if r.url.host == "api.deezer.com"][-1]
    assert req.url.params["index"] == "5"
    yt = run(streaming_manager.search_provider("ytmusic", "daft punk", client, limit=1, offset=1))
    assert [t["title"] for t in yt["tracks"]] == ["Aerodynamic"] and yt["hasMore"] is True   # skipped the first, asked for 2
    assert run(streaming_manager.search_provider("ytmusic", "daft punk", client, limit=5, offset=2))["hasMore"] is False
    small = run(streaming_manager.search_provider("apple", "blinding", client, limit=10))
    assert small["hasMore"] is False                                                  # fewer than a page


def test_album_tracks(run, catalog, upstream):
    client = upstream.client()
    assert [t["title"] for t in run(streaming_manager.album_tracks("apa_697194953", client))] == ["One More Time", "Aerodynamic"]
    dz = run(streaming_manager.album_tracks("dza_302127", client))
    assert [t["title"] for t in dz] == ["One More Time", "Aerodynamic"] and dz[0]["album"] == "Discovery" and dz[1]["audioUrl"] is None
    assert run(streaming_manager.album_tracks("yta_x", client)) == []
    upstream.raise_on = httpx.ConnectError
    assert run(streaming_manager.album_tracks("apa_1", upstream.client())) == []


# ─── audio resolution ─────────────────────────────────────────────────────────

def test_resolve_audio(run, monkeypatch):
    ap = streaming_manager.apple_track(IT_BLINDING)
    assert run(streaming_manager.resolve_audio(ap)) == {"url": ap["audioUrl"], "mime": "audio/mp4", "headers": {}}
    with pytest.raises(streaming_manager.StreamingError) as e:
        run(streaming_manager.resolve_audio(streaming_manager.apple_track(IT_BRIGHTSIDE)))
    assert e.value.status == 404

    yt = streaming_manager.ytmusic_track(YT_SEARCH_RESULTS[0])
    src = run(streaming_manager.resolve_audio(yt))
    assert src["url"].startswith("https://rr1---sn.googlevideo.example/") and src["mime"] == "audio/webm" and src["headers"] == {"User-Agent": "yt-dlp-ua"}
    calls = []
    monkeypatch.setattr(streaming_manager, "ytdlp_resolve_sync", lambda vid: calls.append(vid) or {"url": "https://x/y", "mime": "audio/webm"})
    assert run(streaming_manager.resolve_audio(yt))["url"] == src["url"] and calls == []          # cached
    streaming_manager._yt_streams.clear()
    monkeypatch.setattr(streaming_manager, "ytdlp_resolve_sync", lambda vid: (_ for _ in ()).throw(RuntimeError("Sign in to confirm you're not a bot")))
    with pytest.raises(streaming_manager.StreamingError) as e:
        run(streaming_manager.resolve_audio(yt))
    assert e.value.status == 502 and "not a bot" in str(e.value)
    MISSING_MODULES.update({"yt_dlp"})
    with pytest.raises(streaming_manager.StreamingError) as e:
        run(streaming_manager.resolve_audio(yt))
    assert e.value.status == 503 and "pip install yt-dlp" in str(e.value)
    MISSING_MODULES.clear()


# ─── HTTP API ─────────────────────────────────────────────────────────────────

@pytest.fixture
def client(library, playlists_file, upstream):
    return TestClient(main.app)


def test_status_and_search_endpoints(client, catalog):
    st = client.get("/api/streaming/status").json()
    assert [p["id"] for p in st["providers"]] == ["ytmusic", "apple", "deezer"] and all(p["available"] for p in st["providers"])
    r = client.get("/api/streaming/search", params={"q": "discovery"})
    assert r.status_code == 200
    body = r.json()
    assert body["query"] == "discovery" and [x["provider"] for x in body["results"]] == ["ytmusic", "apple", "deezer"]
    apple = body["results"][1]
    assert [t["title"] for t in apple["tracks"]] == ["Blinding Lights", "Mr. Brightside"] and apple["albums"][0]["id"] == "apa_697194953"
    assert body["results"][0]["tracks"][0]["full"] is True and apple["hasMore"] is False
    r = client.get("/api/streaming/search", params={"q": "daft punk", "providers": "apple", "limit": 2, "offset": 2})
    assert r.json()["results"][0]["offset"] == 2 and r.json()["results"][0]["limit"] == 2
    r = client.get("/api/streaming/search", params={"q": "x", "providers": "deezer"})
    assert [x["provider"] for x in r.json()["results"]] == ["deezer"]
    assert client.get("/api/streaming/search", params={"q": ""}).json()["results"][0]["tracks"] == []
    assert client.get("/api/streaming/tracks/ap_1488408555").json()["title"] == "Blinding Lights"     # cached from the search
    assert client.get("/api/streaming/tracks/ap_999").status_code == 404
    assert [t["title"] for t in client.get("/api/streaming/albums/apa_697194953/tracks").json()["tracks"]] == ["One More Time", "Aerodynamic"]
    assert [t["title"] for t in client.get("/api/streaming/albums/dza_302127/tracks").json()["tracks"]] == ["One More Time", "Aerodynamic"]


def test_status_when_ytmusic_tools_missing(client, no_ytmusic):
    st = client.get("/api/streaming/status").json()
    yt = st["providers"][0]
    assert yt["available"] is False and "pip install" in yt["reason"]
    r = client.get("/api/streaming/search", params={"q": "x"}).json()
    assert r["results"][0]["error"].startswith("Not installed") and r["results"][1]["tracks"]


def test_audio_relay_for_previews_and_youtube(client, catalog, upstream):
    client.get("/api/streaming/search", params={"q": "blinding"})
    upstream.audio_body = b"\x00\x00\x00\x18ftypM4A " + b"\x01" * 512
    upstream.audio_headers = {"content-type": "audio/x-m4a", "accept-ranges": "bytes"}
    r = client.get("/api/streaming/tracks/ap_1488408555/audio")
    assert r.status_code == 200 and r.headers["content-type"].startswith("audio/x-m4a") and r.content == upstream.audio_body
    clip = next(req for req in upstream.requests if req.url.host == "audio-ssl.itunes.example")
    assert clip.url.path == "/preview/blinding.m4a"
    r = client.get("/api/streaming/tracks/ap_1488408555/audio", headers={"Range": "bytes=100-"})
    assert r.status_code == 206 and r.content == upstream.audio_body[100:]
    # No clip -> 404; unknown -> 404; upstream error -> 502
    r = client.get("/api/streaming/tracks/ap_1440800491/audio")
    assert r.status_code == 404 and "No preview clip" in r.json()["error"]
    assert client.get("/api/streaming/tracks/ap_0/audio").status_code == 404

    # YouTube Music: yt-dlp resolves the stream, the relay forwards yt-dlp's headers
    upstream.audio_body = b"\x1a\x45\xdf\xa3" + b"\x02" * 1024
    upstream.audio_headers = {"content-type": "audio/webm", "accept-ranges": "bytes"}
    r = client.get("/api/streaming/tracks/yt_FGBhQbmPwH8/audio", headers={"Range": "bytes=0-511"})
    assert r.status_code == 206 and r.content == upstream.audio_body[:512] or r.status_code == 206
    assert r.headers["content-type"].startswith("audio/webm")
    yt_req = upstream.yt_requests[-1]
    assert yt_req.headers["User-Agent"] == "yt-dlp-ua" and yt_req.headers["Range"] == "bytes=0-511"

    upstream.audio_status = 503
    r = client.get("/api/streaming/tracks/ap_1488408555/audio")
    assert r.status_code == 502 and "HTTP 503" in r.json()["error"]


def test_audio_relay_when_ytmusic_tools_missing(client, catalog, no_ytmusic):
    streaming_manager.remember([streaming_manager.ytmusic_track(YT_SEARCH_RESULTS[0])])
    r = client.get("/api/streaming/tracks/yt_FGBhQbmPwH8/audio")
    assert r.status_code == 503 and "pip install" in r.json()["error"]


def test_old_routes_are_gone(client):
    for path in ("/api/spotify/status", "/api/spotify/search", "/api/spotify/login"):
        assert client.get(path).status_code == 404, path
    for fname in (main.__file__, streaming_manager.__file__):
        assert "spotify" not in open(fname, encoding="utf-8").read().lower(), fname


# ─── voice tools ──────────────────────────────────────────────────────────────

def test_tools_declared_and_prompted(library, no_ytmusic):
    cfg = main.build_gemini_config()
    declared = {d["name"] for d in cfg["tools"][0]["function_declarations"]}
    for t in STREAM_TOOLS:
        assert t in declared and t in main.TOOL_HANDLERS, t
    assert not {"playSpotify", "queueSpotify", "listSpotifyPlaylists", "playSpotifyPlaylist"} & declared
    instr = cfg["system_instruction"]
    assert "STREAMING (voice controls" in instr and "`playStream`" in instr and "`queueStream`" in instr
    assert "Apple Music and Deezer give 30-second preview clips; YouTube Music plays the full song" in instr
    assert "Streaming sources available: Apple Music, Deezer (not installed: YouTube Music)." in instr
    assert "spotify" not in instr.lower()
    MISSING_MODULES.clear()
    assert "Streaming sources available: YouTube Music, Apple Music, Deezer." in main._sources_summary()


def test_play_stream_track_picks_first_provider_with_a_match(conn, run, catalog, upstream, no_ytmusic):
    res = run(main._tool_play_stream(conn, make_fc("playStream", query="stream blinding lights")))["result"]
    assert res.startswith("Streaming from Apple Music: 'Blinding Lights' by The Weeknd (album 'After Hours'). It plays the 30-second preview clip from Apple Music through the deck.")
    assert "Now playing: streamed track 'Blinding Lights' by The Weeknd from the album 'After Hours' — Apple Music, 30-second preview clip" in res
    itunes = next(r for r in upstream.requests if r.url.host == "itunes.apple.com")
    assert itunes.url.params["term"] == "blinding lights"                 # filler stripped
    cmd = commands(conn, "playStreamTracks")
    assert len(cmd) == 1 and cmd[0]["tracks"][0]["id"] == "ap_1488408555" and cmd[0]["context"]["type"] == "search"
    src = conn.playback["source"]
    assert src["type"] == "stream" and src["provider"] == "apple" and src["full"] is False and src["duration"] == 30
    assert conn.playback["track_id"] is None and conn.playback["playing"] is True and conn.playback["queue"] == []


def test_play_stream_named_source(conn, run, catalog):
    res = run(main._tool_play_stream(conn, make_fc("playStream", query="one more time from youtube music")))["result"]
    assert res.startswith("Streaming from YouTube Music: 'One More Time' by Daft Punk (album 'Discovery'). It streams the full song from YouTube Music through the deck.")
    t = commands(conn, "playStreamTracks")[-1]["tracks"][0]
    assert t["id"] == "yt_FGBhQbmPwH8" and t["full"] is True
    assert conn.playback["source"]["provider"] == "ytmusic" and conn.playback["source"]["duration"] == 320
    assert "— YouTube Music, full song" in conn.source_summary()

    res = run(main._tool_play_stream(conn, make_fc("playStream", query="harder better", source="deezer")))["result"]
    assert res.startswith("Streaming from Deezer: 'Harder, Better, Faster, Stronger' by Daft Punk")

    # Apple's top hit has no clip -> it is skipped in favour of the next playable one
    catalog.itunes_songs = [IT_BRIGHTSIDE]
    res = run(main._tool_play_stream(conn, make_fc("playStream", query="mr brightside", source="apple")))["result"]
    assert "No streaming source has a playable match for 'mr brightside'" in res


def test_play_stream_named_source_unavailable(conn, run, catalog, no_ytmusic):
    res = run(main._tool_play_stream(conn, make_fc("playStream", query="one more time", source="youtube")))["result"]
    assert res.startswith("YouTube Music is not available: Not installed — run: pip install ytmusicapi yt-dlp.")
    assert "Available streaming sources: Apple Music, Deezer." in res and commands(conn) == []


def test_play_stream_prefers_youtube_music_when_installed(conn, run, catalog):
    res = run(main._tool_play_stream(conn, make_fc("playStream", query="one more time")))["result"]
    assert res.startswith("Streaming from YouTube Music: 'One More Time' by Daft Punk (album 'Discovery'). It streams the full song")
    assert conn.playback["source"]["provider"] == "ytmusic"


def test_play_stream_album_and_artist(conn, run, catalog, no_ytmusic):
    res = run(main._tool_play_stream(conn, make_fc("playStream", query="the album discovery by daft punk")))["result"]
    assert res.startswith("Streaming from Apple Music: 'One More Time' by Daft Punk (album 'Discovery') from album 'Discovery' (2 tracks, the rest are cued).")
    cmd = commands(conn, "playStreamTracks")[-1]
    assert cmd["context"]["type"] == "album" and cmd["context"]["provider"] == "apple" and [t["title"] for t in cmd["tracks"]] == ["One More Time", "Aerodynamic"]
    assert [q["title"] for q in conn.playback["queue"]] == ["Aerodynamic"] and conn.playback["queue"][0]["kind"] == "stream"
    assert conn.playback["source"]["contextName"] == "Discovery"

    res = run(main._tool_play_stream(conn, make_fc("playStream", query="daft punk", kind="artist")))["result"]
    assert "from artist 'Daft Punk' (2 tracks, the rest are cued)" in res and "Apple Music" in res
    assert [q["title"] for q in conn.playback["queue"]] == ["Blinding Lights"]

    res = run(main._tool_play_stream(conn, make_fc("playStream", query="discovery", kind="album", source="deezer")))["result"]
    assert res.startswith("Streaming from Deezer: 'One More Time' by Daft Punk (album 'Discovery') from album 'Discovery' (its only playable track).")   # Aerodynamic has no Deezer clip

    catalog.itunes_albums = []
    catalog.deezer_albums = []
    res = run(main._tool_play_stream(conn, make_fc("playStream", query="discovery", kind="album")))["result"]
    assert res == "No album matching 'discovery' with playable tracks on the streaming sources."


def test_play_stream_misses(conn, run, catalog, no_ytmusic):
    catalog.itunes_songs = []
    catalog.deezer_results = []
    res = run(main._tool_play_stream(conn, make_fc("playStream", query="zzzz")))["result"]
    assert res.startswith("No streaming source has a playable match for 'zzzz'.") and "YouTube Music: Not installed" in res and "searchAndPlay" in res
    res = run(main._tool_play_stream(conn, make_fc("playStream", query="")))["result"]
    assert "need to know what to stream" in res
    catalog.itunes_artist_songs = []
    res = run(main._tool_play_stream(conn, make_fc("playStream", query="nobody", kind="artist", source="apple")))["result"]
    assert res == "No songs by 'nobody' on the streaming sources."
    assert commands(conn) == []


def test_queue_stream(conn, run, catalog, library, no_ytmusic):
    conn.playback.update({"playing": True, "track_id": "t_billie", "queue": [{"id": "t_heaven"}]})
    res = run(main._tool_queue_stream(conn, make_fc("queueStream", query="queue up blinding lights from apple music")))["result"]
    assert res.startswith("Cued up from Apple Music: 'Blinding Lights' by The Weeknd (album 'After Hours') to play after the current item.")
    assert "Now playing: 'Billie Jean' by Michael Jackson." in res          # playback not interrupted
    cmd = commands(conn, "queueStreamTracks")
    assert len(cmd) == 1 and cmd[0]["tracks"][0]["id"] == "ap_1488408555"
    assert commands(conn, "playStreamTracks") == []
    assert [q["id"] for q in conn.playback["queue"]] == ["t_heaven", "ap_1488408555"]
    assert main._queue_entry_label(conn.playback["queue"][1]) == "streamed track 'Blinding Lights' by The Weeknd"
    res = run(main._tool_describe_queue(conn, make_fc("describeQueue")))["result"]
    assert "2 items cued up, in order: 1. 'Heaven' by Bryan Adams; 2. streamed track 'Blinding Lights' by The Weeknd." in res
    assert "need to know which song" in run(main._tool_queue_stream(conn, make_fc("queueStream", query="")))["result"]


def test_now_playing_seek_speed_and_playlist_guard(conn, run, catalog, playlists_file):
    t = streaming_manager.apple_track(IT_BLINDING)
    stream_source(conn, t, position=12, context={"type": "album", "name": "After Hours"})
    res = run(main._tool_describe_now_playing(conn, make_fc("describeNowPlaying")))["result"]
    assert res.startswith("Now playing: streamed track 'Blinding Lights' by The Weeknd from the album 'After Hours' (from album 'After Hours') — Apple Music, 30-second preview clip, at 0:12 of 0:30.")
    assert "cannot be added to a local playlist" in res

    res = run(main._tool_seek_relative(conn, make_fc("seekRelative", seconds=-5)))["result"]
    assert res.startswith("Skipped back 5 seconds.") and commands(conn, "seekRelative")[0]["seconds"] == -5
    res = run(main._tool_set_playback_speed(conn, make_fc("setPlaybackSpeed", rate=1.5)))["result"]
    assert res.startswith("Streamed tracks play at normal speed") and commands(conn, "setPlaybackRate") == []

    res = run(main._tool_add_to_playlist(conn, make_fc("addToPlaylist", playlistName="Favourites")))["result"]
    assert res.startswith("A streamed track is playing, not a library song")

    stream_source(conn, streaming_manager.ytmusic_track(YT_SEARCH_RESULTS[0]), position=100)
    assert "— YouTube Music, full song, at 1:40 of 5:20." in conn.source_summary()


def test_stream_items_are_left_out_of_saved_playlists(conn, run, library, playlists_file):
    conn.playback.update({"track_id": "t_billie", "queue": [
        {"id": "ap_1488408555", "title": "Blinding Lights", "artist": "The Weeknd", "kind": "stream"},
        {"id": "t_heaven"},
        {"id": "ep_1", "title": "Ep", "artist": "Pod", "kind": "podcast"},
    ]})
    res = run(main._tool_save_queue_as_playlist(conn, make_fc("saveQueueAsPlaylist", name="Mixed")))["result"]
    assert "Saved 2 tracks as playlist 'Mixed'" in res
    assert "(1 cued streamed track and 1 cued podcast episode left out — playlists hold local songs only.)" in res

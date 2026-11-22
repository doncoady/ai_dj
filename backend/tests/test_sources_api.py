"""
HTTP API for radio stations and podcasts, including the same-origin stream
relays (ICY metadata stripping, .pls following, Range pass-through).
Outbound HTTP is mocked; no network access.
"""

import httpx
import pytest
from fastapi.testclient import TestClient

import main
from conftest import radio_manager, podcast_manager, SAMPLE_FEED, SAMPLE_FEED_URL


@pytest.fixture
def client(library, playlists_file, upstream):
    # No `with`: the lifespan (library scan) is not needed for these routes.
    return TestClient(main.app)


# ─── radio stations CRUD ──────────────────────────────────────────────────────

def test_radio_station_crud(client):
    assert client.get("/api/radio").json() == []

    r = client.post("/api/radio", json={"name": "Jazz24", "streamUrl": "https://jazz24.stream.example/s", "genre": "Jazz"})
    assert r.status_code == 201
    st = r.json()
    assert st["id"].startswith("rs_") and st["source"] == "user" and st["genre"] == "Jazz"

    # Same URL again -> update, not duplicate
    r = client.post("/api/radio", json={"name": "Jazz 24 HD", "streamUrl": "https://jazz24.stream.example/s"})
    assert r.status_code == 200 and r.json()["id"] == st["id"] and r.json()["name"] == "Jazz 24 HD"
    assert len(client.get("/api/radio").json()) == 1

    assert client.get(f"/api/radio/{st['id']}").json()["name"] == "Jazz 24 HD"
    r = client.put(f"/api/radio/{st['id']}", json={"genre": "Smooth Jazz", "country": "USA"})
    assert r.status_code == 200 and r.json()["genre"] == "Smooth Jazz" and r.json()["country"] == "USA"

    assert client.delete(f"/api/radio/{st['id']}").json() == {"success": True}
    assert client.delete(f"/api/radio/{st['id']}").status_code == 404
    assert client.get(f"/api/radio/{st['id']}").status_code == 404
    assert client.put(f"/api/radio/{st['id']}", json={"name": "x"}).status_code == 404


def test_radio_station_validation(client):
    assert client.post("/api/radio", json={"name": "no url"}).status_code == 400
    assert client.post("/api/radio", json={"streamUrl": "file:///etc/passwd"}).status_code == 400
    assert client.post("/api/radio", content=b"not json").status_code == 400


def test_radio_directory_search_endpoint(client, upstream, monkeypatch):
    r = client.get("/api/radio/search", params={"q": "jazz", "country": "France"})
    assert r.status_code == 200
    names = [s["name"] for s in r.json()["results"]]
    assert names == ["TSF Jazz", "Jazz24"]
    assert dict(upstream.requests[0].url.params)["country"] == "France"

    monkeypatch.setattr(main, "ONLINE_DIRECTORIES", False)
    r = client.get("/api/radio/search", params={"q": "jazz"})
    assert r.status_code == 503 and r.json()["results"] == [] and "disabled" in r.json()["error"]


def test_radio_browse_endpoint(client, upstream, monkeypatch):
    r = client.get("/api/radio/browse", params={"kind": "popular"})
    assert r.status_code == 200
    body = r.json()
    assert [s["name"] for s in body["results"]] == ["TSF Jazz", "Jazz24"]
    assert body["kind"] == "popular" and {"label": "Jazz", "tag": "jazz"} in body["genres"]
    assert upstream.requests[-1].url.path.startswith("/json/stations/topvote/")

    r = client.get("/api/radio/browse", params={"kind": "tag", "value": "jazz"})
    assert r.status_code == 200 and upstream.requests[-1].url.path == "/json/stations/bytagexact/jazz"

    monkeypatch.setattr(main, "ONLINE_DIRECTORIES", False)
    assert client.get("/api/radio/browse").status_code == 503


def test_radio_now_endpoint(client):
    st, _ = radio_manager.add_station({"name": "S", "streamUrl": "https://s.stream.example/a"})
    assert client.get(f"/api/radio/{st['id']}/now").json() == {"stationId": st["id"], "title": None, "updatedAt": None}
    main.radio_now_playing[st["id"]] = {"title": "Artist - Song", "updatedAt": 123}
    assert client.get(f"/api/radio/{st['id']}/now").json()["title"] == "Artist - Song"
    client.delete(f"/api/radio/{st['id']}")
    assert st["id"] not in main.radio_now_playing            # cleared with the station


# ─── radio stream relay ───────────────────────────────────────────────────────

def icy_body(metaint, blocks, titles):
    out = bytearray()
    for block, title in zip(blocks, titles):
        out += block
        if title is None:
            out += b"\x00"
        else:
            meta = f"StreamTitle='{title}';StreamUrl='';".encode()
            meta += b"\x00" * ((-len(meta)) % 16)
            out += bytes([len(meta) // 16]) + meta
    return bytes(out)


def test_radio_relay_strips_icy_metadata_and_records_title(client, upstream):
    st, _ = radio_manager.add_station({"name": "Groove", "streamUrl": "https://groove.stream.example/gs"})
    metaint = 64
    audio = [bytes([i]) * metaint for i in range(5)]
    upstream.audio_body = icy_body(metaint, audio, ["Tycho - Awake", None, None, "Tycho - Awake", "Bonobo - Kerala"])
    upstream.audio_headers = {"content-type": "audio/mpeg; charset=x", "icy-metaint": str(metaint), "icy-name": "Groove"}

    r = client.get(f"/api/radio/{st['id']}/stream")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("audio/mpeg")
    assert r.headers["cache-control"] == "no-store" and r.headers["accept-ranges"] == "none"
    assert r.content == b"".join(audio)                          # metadata removed, audio intact

    assert upstream.requests[-1].headers["Icy-MetaData"] == "1"   # we asked for titles
    assert main.radio_now_playing[st["id"]]["title"] == "Bonobo - Kerala"
    assert client.get(f"/api/radio/{st['id']}/now").json()["title"] == "Bonobo - Kerala"
    assert radio_manager.get_station_by_id(st["id"])["lastPlayedAt"] > 0


def test_radio_relay_without_icy_metadata_passes_audio_through(client, upstream):
    st, _ = radio_manager.add_station({"name": "Plain", "streamUrl": "https://plain.stream.example/s"})
    upstream.audio_body = b"\x00\x01\x02" * 1000
    upstream.audio_headers = {"content-type": "audio/aac"}
    r = client.get(f"/api/radio/{st['id']}/stream")
    assert r.status_code == 200 and r.content == upstream.audio_body
    assert r.headers["content-type"].startswith("audio/aac")
    assert main.radio_now_playing[st["id"]]["title"] is None


def test_radio_relay_follows_pls_playlist(client, upstream):
    st, _ = radio_manager.add_station({"name": "PLS", "streamUrl": "https://pls.stream.example/listen.pls"})
    calls = []
    base_handler = upstream.handler

    def handler(request):
        calls.append(str(request.url))
        if request.url.path.endswith(".pls"):
            return httpx.Response(200, content=b"[playlist]\nFile1=https://real.stream.example/live\nTitle1=x\n",
                                  headers={"content-type": "audio/x-scpls"})
        return base_handler(request)

    upstream.handler = handler
    r = client.get(f"/api/radio/{st['id']}/stream")
    assert r.status_code == 200 and r.content == upstream.audio_body
    assert calls == ["https://pls.stream.example/listen.pls", "https://real.stream.example/live"]


def test_radio_relay_rejects_hls_and_reports_upstream_errors(client, upstream):
    hls, _ = radio_manager.add_station({"name": "HLS", "streamUrl": "https://hls.stream.example/master.m3u8"})
    r = client.get(f"/api/radio/{hls['id']}/stream")
    assert r.status_code == 415 and "HLS" in r.json()["error"]

    down, _ = radio_manager.add_station({"name": "Down", "streamUrl": "https://down.stream.example/s"})
    upstream.audio_status = 503
    r = client.get(f"/api/radio/{down['id']}/stream")
    assert r.status_code == 502 and "HTTP 503" in r.json()["error"]

    upstream.audio_status = 200
    upstream.raise_on = httpx.ConnectError
    r = client.get(f"/api/radio/{down['id']}/stream")
    assert r.status_code == 502 and "ConnectError" in r.json()["error"]

    upstream.raise_on = httpx.RemoteProtocolError          # legacy "ICY 200 OK" status line
    r = client.get(f"/api/radio/{down['id']}/stream")
    assert r.status_code == 502 and "SHOUTcast" in r.json()["error"]

    assert client.get("/api/radio/rs_nope/stream").status_code == 404


def test_radio_relay_empty_playlist_file(client, upstream):
    st, _ = radio_manager.add_station({"name": "Empty", "streamUrl": "https://e.stream.example/x.m3u"})
    upstream.audio_body = b"#EXTM3U\n"
    upstream.audio_headers = {"content-type": "audio/x-mpegurl"}
    r = client.get(f"/api/radio/{st['id']}/stream")
    assert r.status_code == 502 and "no stream URL" in r.json()["error"]


# ─── podcasts CRUD ────────────────────────────────────────────────────────────

def test_podcast_subscribe_list_get_refresh_delete(client, upstream):
    assert client.get("/api/podcasts").json() == []

    r = client.post("/api/podcasts", json={"feedUrl": SAMPLE_FEED_URL})
    assert r.status_code == 201
    pod = r.json()
    assert pod["title"] == "Night Shift Radio Hour" and len(pod["episodes"]) == 3
    assert pod["id"] == podcast_manager.podcast_id_for(SAMPLE_FEED_URL)

    # Re-subscribing refreshes instead of duplicating
    r = client.post("/api/podcasts", json={"feedUrl": SAMPLE_FEED_URL})
    assert r.status_code == 200 and r.json()["id"] == pod["id"]

    listing = client.get("/api/podcasts").json()
    assert len(listing) == 1 and listing[0]["episodeCount"] == 3 and "episodes" not in listing[0]
    assert listing[0]["latestEpisode"]["title"] == "Episode 12: The Last Train"

    full = client.get(f"/api/podcasts/{pod['id']}").json()
    assert [e["title"] for e in full["episodes"]][:2] == ["Episode 12: The Last Train", "Episode 11: Sleep Science"]

    upstream.feed_xml = SAMPLE_FEED.replace("Episode 12: The Last Train", "Episode 13: Fresh")
    r = client.post(f"/api/podcasts/{pod['id']}/refresh")
    assert r.status_code == 200 and r.json()["episodes"][0]["title"] == "Episode 13: Fresh"

    upstream.feed_status = 502
    r = client.post(f"/api/podcasts/{pod['id']}/refresh")
    assert r.status_code == 200 and "HTTP 502" in r.json()["refreshError"]     # cached copy + error
    assert r.json()["episodes"][0]["title"] == "Episode 13: Fresh"

    assert client.delete(f"/api/podcasts/{pod['id']}").json() == {"success": True}
    assert client.delete(f"/api/podcasts/{pod['id']}").status_code == 404
    assert client.get(f"/api/podcasts/{pod['id']}").status_code == 404
    assert client.post(f"/api/podcasts/{pod['id']}/refresh").status_code == 404


def test_starter_podcast_listing_and_lazy_refresh(client, upstream, sources_files):
    sources_files["podcasts"].unlink()                         # first run: starters get seeded
    listing = client.get("/api/podcasts").json()
    assert len(listing) >= 8 and all(p["needsRefresh"] and p["source"] == "default" for p in listing)
    target = listing[0]
    upstream.serve_all_feeds = True                            # serve whichever feed host the starter points at
    r = client.post(f"/api/podcasts/{target['id']}/refresh")
    assert r.status_code == 200 and r.json()["episodeCount"] == 3 and r.json()["source"] == "default"
    refreshed = next(p for p in client.get("/api/podcasts").json() if p["id"] == target["id"])
    assert refreshed["needsRefresh"] is False and refreshed["latestEpisode"]["title"] == "Episode 12: The Last Train"


def test_podcast_subscribe_validation_and_feed_errors(client, upstream):
    assert client.post("/api/podcasts", json={}).status_code == 400
    assert client.post("/api/podcasts", json={"feedUrl": "not a url"}).status_code == 400
    upstream.feed_xml = "<html>not rss</html>"
    r = client.post("/api/podcasts", json={"feedUrl": SAMPLE_FEED_URL})
    assert r.status_code == 502 and "Not an RSS" in r.json()["error"]
    upstream.feed_xml = SAMPLE_FEED
    upstream.feed_status = 404
    r = client.post("/api/podcasts", json={"feedUrl": SAMPLE_FEED_URL})
    assert r.status_code == 502 and "HTTP 404" in r.json()["error"]
    assert client.get("/api/podcasts").json() == []


def test_podcast_directory_search_endpoint(client, upstream, monkeypatch):
    r = client.get("/api/podcasts/search", params={"q": "night shift"})
    assert r.status_code == 200
    assert [p["title"] for p in r.json()["results"]] == ["Night Shift Radio Hour", "Night Owls"]
    monkeypatch.setattr(main, "ONLINE_DIRECTORIES", False)
    assert client.get("/api/podcasts/search", params={"q": "x"}).status_code == 503


# ─── podcast episode relay ────────────────────────────────────────────────────

@pytest.fixture
def episode(client, upstream):
    pod = client.post("/api/podcasts", json={"feedUrl": SAMPLE_FEED_URL}).json()
    return pod, pod["episodes"][0]


def test_episode_relay_full_and_range(client, upstream, episode):
    pod, ep = episode
    upstream.audio_body = bytes(range(256)) * 8          # 2048 bytes
    upstream.audio_headers = {"content-type": "audio/mpeg", "accept-ranges": "bytes", "etag": '"abc"'}
    url = f"/api/podcasts/{pod['id']}/episodes/{ep['id']}/stream"

    r = client.get(url)
    assert r.status_code == 200 and r.content == upstream.audio_body
    assert r.headers["content-length"] == "2048" and r.headers["accept-ranges"] == "bytes" and r.headers["etag"] == '"abc"'
    assert str(upstream.requests[-1].url) == ep["audioUrl"]

    r = client.get(url, headers={"Range": "bytes=1024-"})
    assert r.status_code == 206
    assert r.content == upstream.audio_body[1024:]
    assert r.headers["content-range"] == "bytes 1024-2047/2048" and r.headers["content-length"] == "1024"
    assert upstream.requests[-1].headers["Range"] == "bytes=1024-"     # forwarded upstream


def test_episode_relay_errors(client, upstream, episode):
    pod, ep = episode
    assert client.get(f"/api/podcasts/{pod['id']}/episodes/ep_nope/stream").status_code == 404
    assert client.get(f"/api/podcasts/pc_nope/episodes/{ep['id']}/stream").status_code == 404
    upstream.audio_status = 403
    r = client.get(f"/api/podcasts/{pod['id']}/episodes/{ep['id']}/stream")
    assert r.status_code == 502 and "HTTP 403" in r.json()["error"]
    upstream.audio_status = 200
    upstream.raise_on = httpx.ReadTimeout
    r = client.get(f"/api/podcasts/{pod['id']}/episodes/{ep['id']}/stream")
    assert r.status_code == 502 and "ReadTimeout" in r.json()["error"]


def test_existing_endpoints_unaffected(client):
    assert client.get("/api/playlists").json() == []
    assert client.get("/api/tracks").status_code == 200
    r = client.get("/")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache"


def test_radio_search_merges_tunein_and_reports_providers(client, upstream):
    from conftest import TUNEIN_JACK
    upstream.tunein_results = [TUNEIN_JACK, dict(TUNEIN_JACK, text="TSF Jazz (Jazz)", guide_id="s1", URL="http://opml.radiotime.com/Tune.ashx?id=s1")]
    r = client.get("/api/radio/search", params={"q": "jazz", "place": "Paris"})
    assert r.status_code == 200
    body = r.json()
    assert body["providers"] == ["radio-browser", "tunein"]
    names = [s["name"] for s in body["results"]]
    assert "TSF Jazz" in names and names.count("TSF Jazz") == 1            # de-duplicated across directories
    assert any(s.get("provider") == "tunein" for s in body["results"])


def test_saving_a_tunein_station_resolves_it_and_relay_uses_the_direct_url(client, upstream):
    from conftest import TUNEIN_JACK
    entry = radio_manager._tunein_entry(TUNEIN_JACK)
    r = client.post("/api/radio", json=entry)
    assert r.status_code == 201
    st = r.json()
    assert st["resolvedUrl"] == upstream.tunein_stream_url and st["provider"] == "tunein"

    upstream.audio_body = b"\x01" * 512
    r = client.get(f"/api/radio/{st['id']}/stream")
    assert r.status_code == 200 and r.content == upstream.audio_body
    assert str(upstream.requests[-1].url) == upstream.tunein_stream_url        # direct stream, not the Tune link


def test_relay_resolves_tunein_link_lazily_when_saved_unresolved(client, upstream):
    from conftest import TUNEIN_JACK
    entry = radio_manager._tunein_entry(TUNEIN_JACK)
    st, _ = radio_manager.add_station(entry, source="directory")            # bypasses the API: no resolvedUrl yet
    assert not st.get("resolvedUrl")
    r = client.get(f"/api/radio/{st['id']}/stream")
    assert r.status_code == 200
    assert radio_manager.get_station_by_id(st["id"])["resolvedUrl"] == upstream.tunein_stream_url
    upstream.tunein_hls_only = True
    st2, _ = radio_manager.add_station(dict(entry, streamUrl="https://opml.radiotime.com/Tune.ashx?id=s999", resolvedUrl=""), source="directory")
    r = client.get(f"/api/radio/{st2['id']}/stream")
    assert r.status_code in (415, 502)                                     # only HLS offered: honest failure


def test_latest_episodes_endpoint(client, subscribed_podcast):
    r = client.get("/api/podcasts/latest", params={"limit": 5})
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1 and items[0]["episode"]["title"] == "Episode 12: The Last Train" and items[0]["podcast"]["title"] == "Night Shift Radio Hour"
    assert len(client.get("/api/podcasts/latest", params={"per": 3}).json()["items"]) == 3

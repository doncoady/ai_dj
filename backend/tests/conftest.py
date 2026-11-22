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

import httpx
import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

os.environ.setdefault("GEMINI_API_KEY", "test-key-not-used")
os.environ.setdefault("MUSIC_DIR", str(BACKEND_DIR / "tests" / "_no_music_dir"))

import main  # noqa: E402  (after env setup on purpose)

pl_manager = main.pl_manager  # same module object main.py uses
radio_manager = main.radio_manager
podcast_manager = main.podcast_manager
audiobook_manager = main.audiobook_manager
streaming_manager = main.streaming_manager
scancache = main.scancache


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


@pytest.fixture(autouse=True)
def sources_files(tmp_path, monkeypatch):
    """Radio stations, podcast subscriptions and audiobook progress always live
    in temp files during tests (build_gemini_config() lists them, so every
    test needs this). Station/podcast files start EMPTY — the starter picks
    are opted into by tests that delete the file first (see test_radio.py /
    test_podcasts.py). Audiobooks start UNCONFIGURED; tests opt in with the
    `audiobook_library` fixture, so a developer's real .env never leaks into
    the suite. The streaming track cache is emptied and the YouTube Music
    third-party tools are stubbed (no yt-dlp / ytmusicapi calls)."""
    stations = tmp_path / "radio_stations.json"
    stations.write_text("[]", encoding="utf-8")
    pods = tmp_path / "podcasts.json"
    pods.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(radio_manager, "STATIONS_FILE", stations)
    monkeypatch.setattr(podcast_manager, "PODCASTS_FILE", pods)
    monkeypatch.setattr(audiobook_manager, "PROGRESS_FILE", tmp_path / "audiobooks.json")
    monkeypatch.setattr(scancache, "CACHE_DIR", tmp_path)          # library_cache.json / audiobooks_cache.json
    monkeypatch.setattr(main, "AUDIOBOOKS_DIR", None)
    monkeypatch.setattr(main, "audiobook_library", [])
    monkeypatch.setattr(main, "audiobooks_by_id", {})
    monkeypatch.setattr(main, "audiobooks_scan_complete", True)
    streaming_manager.track_cache.clear()
    streaming_manager._yt_streams.clear()
    # YouTube Music: never call the real third-party tools in tests
    monkeypatch.setattr(streaming_manager, "ytmusic_search_sync", lambda query, limit: list(YT_SEARCH_RESULTS[:limit]))
    monkeypatch.setattr(streaming_manager, "ytdlp_resolve_sync", lambda video_id: dict(YT_RESOLVED[video_id]))
    monkeypatch.setattr(streaming_manager, "_module_available", lambda name: name not in MISSING_MODULES)
    MISSING_MODULES.clear()
    main.radio_now_playing.clear()
    return {"stations": stations, "podcasts": pods}


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


# ─── Outbound HTTP mocking for radio / podcast features ──────────────────────

SAMPLE_FEED_URL = "https://feeds.example.com/nightshift.xml"
SAMPLE_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel>
    <title>Night Shift Radio Hour</title>
    <link>https://nightshift.example</link>
    <description>Late-night stories and music.</description>
    <itunes:author>Dana Rivers</itunes:author>
    <itunes:image href="https://nightshift.example/art.jpg"/>
    <item>
      <title>Episode 12: The Last Train</title><guid>ns-12</guid>
      <pubDate>Tue, 04 Mar 2025 06:00:00 +0000</pubDate>
      <enclosure url="https://cdn.example/ns/12.mp3" type="audio/mpeg"/>
      <itunes:duration>1:02:03</itunes:duration><itunes:episode>12</itunes:episode>
      <itunes:summary>A conductor who never sleeps.</itunes:summary>
    </item>
    <item>
      <title>Episode 11: Sleep Science</title><guid>ns-11</guid>
      <pubDate>Tue, 25 Feb 2025 06:00:00 +0000</pubDate>
      <enclosure url="https://cdn.example/ns/11.mp3" type="audio/mpeg"/>
      <itunes:duration>2520</itunes:duration><itunes:episode>11</itunes:episode>
    </item>
    <item>
      <title>Episode 10: City Lights</title><guid>ns-10</guid>
      <pubDate>Tue, 18 Feb 2025 06:00:00 +0000</pubDate>
      <enclosure url="https://cdn.example/ns/10.mp3" type="audio/mpeg"/>
      <itunes:duration>45:00</itunes:duration><itunes:episode>10</itunes:episode>
    </item>
  </channel>
</rss>
"""

DIRECTORY_STATIONS = [
    {"name": "TSF Jazz", "url_resolved": "https://tsf.example/stream", "tags": "jazz", "country": "France",
     "votes": 80, "codec": "AAC", "bitrate": 64, "favicon": "https://tsf.example/favicon.png", "homepage": "https://tsf.example"},
    {"name": "Jazz24", "url_resolved": "https://jazz24.example/stream", "tags": "jazz,smooth", "country": "USA",
     "votes": 50, "codec": "MP3", "bitrate": 128},
]

ITUNES_PODCASTS = [
    {"collectionName": "Night Shift Radio Hour", "artistName": "Dana Rivers", "feedUrl": SAMPLE_FEED_URL,
     "artworkUrl600": "https://nightshift.example/art.jpg", "primaryGenreName": "Fiction", "trackCount": 12},
    {"collectionName": "Night Owls", "artistName": "Someone Else", "feedUrl": "https://feeds.example.com/owls.xml"},
]


class MockUpstream:
    """Routes the backend's outbound HTTP (directories, feeds, audio) to canned
    responses and records every request. Audio URLs stream `audio_body`."""

    def __init__(self):
        self.requests: list = []
        self.directory_results = list(DIRECTORY_STATIONS)
        self.itunes_results = list(ITUNES_PODCASTS)
        self.feed_xml = SAMPLE_FEED
        self.feed_status = 200
        self.audio_body = b"\xff\xfb" * 2048
        self.audio_headers = {"content-type": "audio/mpeg"}
        self.audio_status = 200
        self.raise_on = None     # exception class to raise for audio/feeds (simulated outage)
        self.serve_all_feeds = False   # True: any feeds.* host / .xml path gets feed_xml (starter picks)
        # TuneIn (opml.radiotime.com): Search.ashx results and the direct URL Tune.ashx resolves to
        self.tunein_results = []
        self.tunein_stream_url = "https://tunein-cdn.stream.example/live.aac"
        self.tunein_hls_only = False
        self.streaming = StreamingMock()
        self.yt_requests: list = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host = request.url.host
        if self.raise_on is not None:
            raise self.raise_on("simulated outage")
        if host.endswith("radio-browser.info"):
            return httpx.Response(200, json=self.directory_results)
        if host == "opml.radiotime.com":
            if request.url.path.endswith("Search.ashx"):
                return httpx.Response(200, json={"head": {"status": "200"}, "body": self.tunein_results})
            if request.url.path.endswith("Tune.ashx"):
                body = [{"element": "audio", "url": "https://x.example/live.m3u8", "media_type": "hls", "is_hls_advanced": "true", "reliability": 90}]
                if not self.tunein_hls_only:
                    body.append({"element": "audio", "url": self.tunein_stream_url, "media_type": "aac", "is_direct": True, "reliability": 95})
                return httpx.Response(200, json={"head": {"status": "200"}, "body": body})
            return httpx.Response(404)
        if host == "itunes.apple.com":
            entity = request.url.params.get("entity", "")
            if request.url.path == "/lookup":
                return httpx.Response(200, json={"results": self.streaming.itunes_album_songs})
            if entity == "song":
                rows = self.streaming.itunes_artist_songs if request.url.params.get("attribute") == "artistTerm" else self.streaming.itunes_songs
                return httpx.Response(self.streaming.itunes_status, json={"results": rows})
            if entity == "album":
                return httpx.Response(self.streaming.itunes_status, json={"results": self.streaming.itunes_albums})
            return httpx.Response(200, json={"results": self.itunes_results})
        if host == "api.deezer.com":
            if request.url.path == "/search/album":
                return httpx.Response(200, json={"data": self.streaming.deezer_albums})
            if request.url.path.startswith("/album/"):
                return httpx.Response(200, json=self.streaming.deezer_album)
            return httpx.Response(self.streaming.deezer_status, json={"data": self.streaming.deezer_results})
        if host.endswith("googlevideo.example"):      # yt-dlp stream host
            self.yt_requests.append(request)
        if host == "feeds.example.com" or (self.serve_all_feeds and (host.startswith("feeds.") or request.url.path.endswith((".xml", ".rss")))):
            return httpx.Response(self.feed_status, content=self.feed_xml.encode("utf-8"),
                                  headers={"content-type": "application/rss+xml"})
        if host.endswith("stream.example") or host == "cdn.example" or host.endswith(".example"):   # any audio host
            headers = dict(self.audio_headers)
            body = self.audio_body
            rng = request.headers.get("Range")
            status = self.audio_status
            if rng and status == 200 and headers.get("accept-ranges") == "bytes":
                start = int(rng.split("=")[1].split("-")[0])
                body = body[start:]
                status = 206
                headers["content-range"] = f"bytes {start}-{len(self.audio_body) - 1}/{len(self.audio_body)}"
            headers.setdefault("content-length", str(len(body)))
            return httpx.Response(status, content=body, headers=headers)
        return httpx.Response(404)

    def client(self, timeout=10.0):
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler), follow_redirects=True)


@pytest.fixture
def upstream(monkeypatch):
    """Install MockUpstream as main._http_client and enable online directories."""
    mock = MockUpstream()
    monkeypatch.setattr(main, "_http_client", mock.client)
    monkeypatch.setattr(main, "ONLINE_DIRECTORIES", True)
    return mock


@pytest.fixture
def seeded_stations():
    """A few saved stations with known names/genres (not the SomaFM defaults)."""
    out = []
    for name, url, genre, country in [
        ("Groove Salad", "https://groove.stream.example/gs", "Ambient, Downtempo", "USA"),
        ("Radio Paradise", "https://rp.stream.example/mp3", "Eclectic Rock", "USA"),
        ("FIP", "https://fip.stream.example/aac", "Jazz, World", "France"),
    ]:
        st, _ = radio_manager.add_station({"name": name, "streamUrl": url, "genre": genre, "country": country})
        out.append(st)
    return out


@pytest.fixture
def subscribed_podcast():
    parsed = podcast_manager.parse_feed(SAMPLE_FEED, SAMPLE_FEED_URL)
    pod, _ = podcast_manager.upsert_podcast(parsed)
    return pod


TUNEIN_JACK = {"element": "outline", "type": "audio", "text": "Jack 92.9 (Adult Hits)", "URL": "http://opml.radiotime.com/Tune.ashx?id=s22216",
               "bitrate": "64", "reliability": "99", "guide_id": "s22216", "subtext": "Halifax's Jack FM", "formats": "mp3",
               "item": "station", "image": "https://cdn-profiles.tunein.example/s22216/logo.jpg"}
TUNEIN_MOVE = {"element": "outline", "type": "audio", "text": "Move 100.1 (Hot AC)", "URL": "http://opml.radiotime.com/Tune.ashx?id=s33333",
               "bitrate": "64", "reliability": "95", "guide_id": "s33333", "formats": "aac", "item": "station"}


# ─── Streaming providers mock (iTunes, Deezer, YouTube Music tools) ──────────

IT_BLINDING = {"wrapperType": "track", "trackId": 1488408555, "trackName": "Blinding Lights", "artistName": "The Weeknd", "collectionName": "After Hours",
               "artworkUrl100": "https://is1.mzstatic.example/100x100bb.jpg", "trackTimeMillis": 200040, "trackExplicitness": "notExplicit",
               "previewUrl": "https://audio-ssl.itunes.example/preview/blinding.m4a", "trackViewUrl": "https://music.apple.com/us/album/blinding-lights/1488408532?i=1488408555"}
IT_BRIGHTSIDE = {"wrapperType": "track", "trackId": 1440800491, "trackName": "Mr. Brightside", "artistName": "The Killers", "collectionName": "Hot Fuss",
                 "artworkUrl100": "", "trackTimeMillis": 222973, "trackExplicitness": "explicit", "previewUrl": None,
                 "trackViewUrl": "https://music.apple.com/us/album/mr-brightside/1440800483?i=1440800491"}
IT_ONE_MORE = {"wrapperType": "track", "trackId": 697195787, "trackName": "One More Time", "artistName": "Daft Punk", "collectionName": "Discovery",
               "artworkUrl100": "https://is1.mzstatic.example/disc100x100bb.jpg", "trackTimeMillis": 320357, "trackExplicitness": "notExplicit",
               "previewUrl": "https://audio-ssl.itunes.example/preview/onemoretime.m4a", "trackViewUrl": "https://music.apple.com/us/album/one-more-time/697194953?i=697195787"}
IT_AERO = {"wrapperType": "track", "trackId": 697195790, "trackName": "Aerodynamic", "artistName": "Daft Punk", "collectionName": "Discovery",
           "artworkUrl100": "", "trackTimeMillis": 212000, "trackExplicitness": "notExplicit",
           "previewUrl": "https://audio-ssl.itunes.example/preview/aero.m4a", "trackViewUrl": "https://music.apple.com/us/album/aerodynamic/697194953?i=697195790"}
IT_DISCOVERY = {"wrapperType": "collection", "collectionId": 697194953, "collectionName": "Discovery", "artistName": "Daft Punk",
                "artworkUrl100": "https://is1.mzstatic.example/disc100x100bb.jpg", "trackCount": 14, "releaseDate": "2001-03-12T08:00:00Z",
                "collectionViewUrl": "https://music.apple.com/us/album/discovery/697194953"}
DZ_TRACK = {"id": 3135556, "title": "Harder, Better, Faster, Stronger", "duration": 224, "preview": "https://cdns-preview.dzcdn.example/hbfs.mp3",
            "explicit_lyrics": False, "link": "https://www.deezer.com/track/3135556", "artist": {"name": "Daft Punk"},
            "album": {"title": "Discovery", "cover_medium": "https://e-cdns-images.dzcdn.example/250x250.jpg"}}
DZ_ALBUM = {"id": 302127, "title": "Discovery", "link": "https://www.deezer.com/album/302127", "cover_medium": "https://e-cdns-images.dzcdn.example/disc.jpg",
            "nb_tracks": 2, "release_date": "2001-03-07", "artist": {"name": "Daft Punk"}}
DZ_ALBUM_FULL = {**DZ_ALBUM, "tracks": {"data": [
    {"id": 3135553, "title": "One More Time", "duration": 320, "preview": "https://cdns-preview.dzcdn.example/omt.mp3", "explicit_lyrics": False,
     "link": "https://www.deezer.com/track/3135553", "artist": {"name": "Daft Punk"}},
    {"id": 3135554, "title": "Aerodynamic", "duration": 212, "preview": None, "explicit_lyrics": False,
     "link": "https://www.deezer.com/track/3135554", "artist": {"name": "Daft Punk"}},
]}}
# ytmusicapi search results (filter="songs")
YT_SEARCH_RESULTS = [
    {"videoId": "FGBhQbmPwH8", "title": "One More Time", "artists": [{"name": "Daft Punk", "id": "a1"}], "album": {"name": "Discovery", "id": "b1"},
     "duration": "5:20", "duration_seconds": 320, "isExplicit": False,
     "thumbnails": [{"url": "https://lh3.googleusercontent.example/s60", "width": 60, "height": 60}, {"url": "https://lh3.googleusercontent.example/s544", "width": 544, "height": 544}]},
    {"videoId": "L93-7vRfxNs", "title": "Aerodynamic", "artists": [{"name": "Daft Punk"}], "album": {"name": "Discovery"}, "duration": "3:32", "isExplicit": False, "thumbnails": []},
]
# yt-dlp answers, by video id
YT_RESOLVED = {
    "FGBhQbmPwH8": {"url": "https://rr1---sn.googlevideo.example/videoplayback?id=omt", "mime": "audio/webm", "durationMs": 320000, "headers": {"User-Agent": "yt-dlp-ua"}},
    "L93-7vRfxNs": {"url": "https://rr1---sn.googlevideo.example/videoplayback?id=aero", "mime": "audio/mp4", "durationMs": 212000, "headers": {}},
}
MISSING_MODULES: set = set()         # module names the availability check should report missing


class StreamingMock:
    """Canned keyless services the Streaming tab uses: iTunes Search/Lookup and
    Deezer search/albums. (YouTube Music's third-party tools are stubbed
    directly on the streaming module.) Requests are logged in MockUpstream.requests."""

    def __init__(self):
        self.itunes_status = 200
        self.itunes_songs = [IT_BLINDING, IT_BRIGHTSIDE]
        self.itunes_artist_songs = [IT_ONE_MORE, IT_BLINDING]
        self.itunes_albums = [IT_DISCOVERY]
        self.itunes_album_songs = [IT_DISCOVERY, IT_ONE_MORE, IT_AERO]
        self.deezer_status = 200
        self.deezer_results = [DZ_TRACK]
        self.deezer_albums = [DZ_ALBUM]
        self.deezer_album = DZ_ALBUM_FULL


@pytest.fixture
def catalog(upstream):
    """The streaming providers mock (iTunes / Deezer; YouTube Music stubs are autouse)."""
    return upstream.streaming


@pytest.fixture
def no_ytmusic():
    """Pretend ytmusicapi / yt-dlp are not installed."""
    MISSING_MODULES.update({"ytmusicapi", "yt_dlp"})
    yield
    MISSING_MODULES.clear()


# ─── Audiobook library on disk ────────────────────────────────────────────────

_MP3_FRAME = b"\xff\xfb\x90\x00" + b"\x00" * 413      # one valid MPEG-1 Layer III frame (128 kbps, 44.1 kHz)


def write_mp3(path: Path, frames: int = 40, **tags) -> Path:
    """A tiny but valid MP3 (~1 s) with ID3 tags mutagen can read."""
    from mutagen.id3 import ID3
    from mutagen.easyid3 import EasyID3
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_MP3_FRAME * frames)
    ID3().save(path)
    easy = EasyID3(path)
    for k, v in tags.items():
        easy[k] = v
    easy.save()
    return path


def build_audiobook_tree(root: Path) -> Path:
    """Three books exercising every layout rule:
    Tolkien/The Hobbit/CD 1|CD 2 (tags + disc folders), Dune/ (untagged,
    natural sort, cover.jpg) and a loose single-file book."""
    hob = root / "Tolkien" / "The Hobbit"
    write_mp3(hob / "CD 1" / "01 An Unexpected Party.mp3", album="The Hobbit", artist="J.R.R. Tolkien", composer="Andy Serkis",
              tracknumber="1", discnumber="1", title="An Unexpected Party", genre="Fantasy")
    write_mp3(hob / "CD 1" / "02 Roast Mutton.mp3", album="The Hobbit", artist="J.R.R. Tolkien", composer="Andy Serkis",
              tracknumber="2", discnumber="1", title="Roast Mutton", genre="Fantasy")
    write_mp3(hob / "CD 2" / "01 Riddles.mp3", album="The Hobbit", artist="J.R.R. Tolkien", composer="Andy Serkis",
              tracknumber="1", discnumber="2", title="Riddles in the Dark", genre="Fantasy")
    for name in ("Part 10.mp3", "Part 2.mp3", "Part 1.mp3"):
        write_mp3(root / "Dune" / name)
    (root / "Dune" / "cover.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 32)
    write_mp3(root / "Short Story.mp3", title="Short Story", artist="Anon")
    return root


@pytest.fixture
def audiobook_library(tmp_path, monkeypatch):
    """Scan a real (tiny) audiobook tree and install it as main.py's library."""
    root = build_audiobook_tree(tmp_path / "Audiobooks")
    books = audiobook_manager.scan_audiobooks(root)
    monkeypatch.setattr(main, "AUDIOBOOKS_DIR", root)
    monkeypatch.setattr(main, "audiobook_library", books)
    monkeypatch.setattr(main, "audiobooks_by_id", {b["id"]: b for b in books})
    monkeypatch.setattr(main, "audiobooks_scan_complete", True)
    by_title = {b["title"]: b for b in books}
    return {"root": root, "books": books, "hobbit": by_title["The Hobbit"], "dune": by_title["Dune"], "short": by_title["Short Story"]}

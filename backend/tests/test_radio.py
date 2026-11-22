"""radio.py — saved stations, spoken-name matching, playlist-file and ICY stream helpers, directory search."""

import httpx
import pytest

from conftest import radio_manager


def add(name, url, **kw):
    st, created = radio_manager.add_station({"name": name, "streamUrl": url, **kw})
    return st


# ─── persistence ──────────────────────────────────────────────────────────────

def test_defaults_are_seeded_only_when_no_file_exists(sources_files):
    sources_files["stations"].unlink()             # fixture created an empty file; remove it
    seeded = radio_manager.load_stations()
    assert len(seeded) == len(radio_manager.DEFAULT_STATIONS) >= 18
    assert all(s["source"] == "default" and s["id"].startswith("rs_") for s in seeded)
    # The user deletes everything: the empty file is kept and the defaults do not come back.
    for s in seeded:
        assert radio_manager.delete_station(s["id"])
    assert radio_manager.load_stations() == []
    assert sources_files["stations"].exists()


def test_starter_picks_are_broad_and_well_formed():
    """The starter set must cover many genres and several countries, use
    direct https streams (no HLS), and have unique URLs/IDs."""
    defaults = radio_manager.DEFAULT_STATIONS
    ids = [radio_manager.station_id_for(d["streamUrl"]) for d in defaults]
    assert len(set(ids)) == len(ids)
    assert all(d["streamUrl"].startswith("https://") and not d["streamUrl"].endswith(".m3u8") for d in defaults)
    assert all(d["name"] and d["genre"] and d["country"] for d in defaults)
    genres = " ".join(d["genre"].lower() for d in defaults)
    for g in ("jazz", "classical", "rock", "pop", "metal", "country", "ambient", "house", "80s", "news", "soul", "indie"):
        assert g in genres, g
    assert len({d["country"] for d in defaults}) >= 5
    assert sum(1 for d in defaults if "somafm" not in d["streamUrl"]) >= 10     # not just one network
    assert len(radio_manager.BROWSE_GENRES) >= 15 and all(t for _, t in radio_manager.BROWSE_GENRES)


def test_add_requires_http_url_and_dedupes_by_url():
    assert radio_manager.add_station({"name": "bad", "streamUrl": "ftp://x"}) == (None, False)
    assert radio_manager.add_station({"name": "bad"}) == (None, False)

    st, created = radio_manager.add_station({"name": "Jazz24", "streamUrl": "https://live.example/jazz24", "genre": "Jazz"})
    assert created and st["id"] == radio_manager.station_id_for("https://live.example/jazz24")
    again, created2 = radio_manager.add_station({"name": "Jazz 24 (HD)", "streamUrl": "https://live.example/jazz24 ", "country": "USA"})
    assert not created2 and again["id"] == st["id"]
    stations = radio_manager.load_stations()
    assert len(stations) == 1
    assert stations[0]["name"] == "Jazz 24 (HD)" and stations[0]["genre"] == "Jazz" and stations[0]["country"] == "USA"


def test_update_and_delete():
    st = add("Old", "https://s.example/a")
    upd = radio_manager.update_station(st["id"], name="New Name", genre="Rock")
    assert upd["name"] == "New Name" and upd["genre"] == "Rock"
    assert radio_manager.update_station("rs_missing", name="x") is None
    assert radio_manager.delete_station(st["id"]) is True
    assert radio_manager.delete_station(st["id"]) is False
    assert radio_manager.get_station_by_id(st["id"]) is None


def test_mark_played_sets_timestamp():
    st = add("A", "https://s.example/a")
    assert st["lastPlayedAt"] == 0
    radio_manager.mark_played(st["id"])
    assert radio_manager.get_station_by_id(st["id"])["lastPlayedAt"] > 0


# ─── spoken-name matching ─────────────────────────────────────────────────────

@pytest.fixture
def stations():
    return [
        add("SomaFM Groove Salad", "https://s/1", genre="Ambient, Downtempo, Chillout", country="USA"),
        add("SomaFM Underground 80s", "https://s/2", genre="80s, Synthpop, New Wave", country="USA"),
        add("Radio Paradise", "https://s/3", genre="Eclectic Rock", country="USA"),
        add("FIP", "https://s/4", genre="Jazz, World, Eclectic", country="France"),
    ]


@pytest.mark.parametrize("spoken, expected", [
    ("groove salad", "SomaFM Groove Salad"),
    ("play the groove salad station", "SomaFM Groove Salad"),
    ("tune in to radio paradise", "Radio Paradise"),
    ("paradise", "Radio Paradise"),
    ("some ambient", "SomaFM Groove Salad"),          # genre match
    ("put on the 80s station", "SomaFM Underground 80s"),
    ("play some jazz radio", "FIP"),
    ("fip", "FIP"),
    ("a french station", None),                        # 'french' != 'France' — no guessing
    ("tsf jazz", None),                                # one shared genre word is not a match (directory instead)
    ("bbc radio 6", None),
])
def test_find_station(stations, spoken, expected):
    st = radio_manager.find_station(spoken)
    assert (st["name"] if st else None) == expected


def test_find_station_tie_prefers_recently_played():
    # Both names merely *contain* "chill"; both genres match exactly -> a tie.
    add("Chill Beats", "https://s/a", genre="Chill")
    b = add("Chill Vibes", "https://s/b", genre="Chill")
    radio_manager.mark_played(b["id"])
    assert radio_manager.find_station("chill")["id"] == b["id"]


# ─── stream helpers ───────────────────────────────────────────────────────────

def test_extract_stream_urls_from_pls_and_m3u():
    pls = "[playlist]\nNumberOfEntries=2\nFile1=https://ice1.example/stream\nTitle1=X\nFile2=http://ice2.example/stream\nVersion=2\n"
    assert radio_manager.extract_stream_urls(pls) == ["https://ice1.example/stream", "http://ice2.example/stream"]
    m3u = "#EXTM3U\n#EXTINF:-1,Station\nhttps://ice1.example/stream\nhttps://ice1.example/stream\n"
    assert radio_manager.extract_stream_urls(m3u) == ["https://ice1.example/stream"]
    assert radio_manager.extract_stream_urls("garbage\nnot a url") == []


@pytest.mark.parametrize("ctype, url, playlist, hls", [
    ("audio/x-scpls", "https://x/listen", True, False),
    ("audio/mpeg", "https://x/listen.pls", True, False),
    ("application/octet-stream", "https://x/s.m3u?x=1", True, False),
    ("audio/mpeg", "https://x/stream", False, False),
    ("application/vnd.apple.mpegurl", "https://x/live", False, True),
    ("audio/x-mpegurl", "https://x/master.m3u8", False, True),
])
def test_playlist_and_hls_detection(ctype, url, playlist, hls):
    assert radio_manager.is_playlist_response(ctype, url) is playlist
    assert radio_manager.is_hls(ctype, url) is hls


def icy_stream(metaint, audio_blocks, titles):
    """Build raw ICY bytes: metaint audio bytes, then a metadata block, repeated."""
    out = bytearray()
    for block, title in zip(audio_blocks, titles):
        assert len(block) == metaint
        out += block
        if title is None:
            out += b"\x00"
        else:
            meta = f"StreamTitle='{title}';StreamUrl='';".encode("utf-8")
            pad = (-len(meta)) % 16
            meta += b"\x00" * pad
            out += bytes([len(meta) // 16]) + meta
    return bytes(out)


def test_icy_parser_strips_metadata_and_tracks_titles():
    metaint = 32
    audio = [bytes([i]) * metaint for i in range(4)]
    raw = icy_stream(metaint, audio, ["Artist - Song One", None, "Artist - Song One", "Other - Song Two"])
    parser = radio_manager.IcyMetadataParser(metaint)
    assert parser.feed(raw) == b"".join(audio)
    assert parser.title == "Other - Song Two"


def test_icy_parser_handles_arbitrary_chunk_boundaries():
    metaint = 16
    audio = [bytes([i + 1]) * metaint for i in range(6)]
    raw = icy_stream(metaint, audio, ["Título con acentos — ok", None, "B", "B", None, "C"])
    for chunk_size in (1, 3, 7, 16, 17, 64, 10_000):
        parser = radio_manager.IcyMetadataParser(metaint)
        out = b"".join(parser.feed(raw[i:i + chunk_size]) for i in range(0, len(raw), chunk_size))
        assert out == b"".join(audio), f"chunk_size={chunk_size}"
        assert parser.title == "C"


def test_icy_parser_reports_each_title_change_once():
    metaint = 8
    audio = [b"a" * metaint] * 3
    first = icy_stream(metaint, audio[:1], ["One"])
    rest = icy_stream(metaint, audio[1:], ["One", "Two"])
    parser = radio_manager.IcyMetadataParser(metaint)
    parser.feed(first)
    assert parser.take_title_change() == "One"
    assert parser.take_title_change() is None           # reported once
    parser.feed(rest)
    assert parser.take_title_change() == "Two"           # the repeated 'One' was not a change


def test_icy_parser_rejects_bad_metaint():
    with pytest.raises(ValueError):
        radio_manager.IcyMetadataParser(0)


# ─── online directory ─────────────────────────────────────────────────────────

def directory_client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), headers={"User-Agent": "test"})


def test_search_directory_merges_name_and_tag_results_and_skips_hls(run):
    calls = []

    def handler(request: httpx.Request):
        calls.append(dict(request.url.params))
        if "name" in request.url.params:
            data = [
                {"name": "Jazz24", "url_resolved": "https://a/jazz24", "tags": "jazz,smooth", "country": "USA", "votes": 50, "codec": "MP3", "bitrate": 128},
                {"name": "Jazz HLS", "url_resolved": "https://a/live.m3u8", "tags": "jazz", "votes": 999, "codec": "HLS"},
            ]
        else:
            data = [
                {"name": "Jazz24", "url": "https://a/jazz24", "tags": "jazz", "votes": 50},      # dup by URL
                {"name": "TSF Jazz", "url_resolved": "https://b/tsf", "tags": "jazz", "country": "France", "votes": 80, "codec": "AAC", "bitrate": 64},
            ]
        return httpx.Response(200, json=data)

    results = run(radio_manager.search_directory("jazz", client=directory_client(handler)))
    assert [r["name"] for r in results] == ["TSF Jazz", "Jazz24"]        # by votes, deduped, HLS dropped
    assert results[1]["genre"] == "jazz, smooth" and results[1]["description"] == "MP3, 128 kbps"
    assert results[0]["id"] == radio_manager.station_id_for("https://b/tsf") and results[0]["source"] == "directory"
    assert calls[0]["name"] == "jazz" and calls[1]["tag"] == "jazz"
    # Searches must NOT hide "broken" stations: geo-blocked local stations look
    # broken to the directory's European checkers yet play fine for the user.
    assert calls[0]["hidebroken"] == "false"


def test_search_directory_passes_country_and_tolerates_failures(run):
    def handler(request):
        assert request.url.params["country"] == "France"
        return httpx.Response(503)

    assert run(radio_manager.search_directory("jazz", country="France", client=directory_client(handler))) == []

    def boom(request):
        raise httpx.ConnectError("offline")

    assert run(radio_manager.search_directory("jazz", client=directory_client(boom))) == []
    assert run(radio_manager.search_directory("", client=directory_client(boom))) == []


def test_browse_directory_popular_tag_country(run):
    seen = []

    def handler(request: httpx.Request):
        seen.append((request.url.raw_path.decode().split("?")[0], dict(request.url.params)))
        return httpx.Response(200, json=[
            {"name": "A", "url_resolved": "https://a/s", "tags": "jazz", "votes": 5},
            {"name": "B", "url_resolved": "https://b/s", "tags": "jazz", "votes": 9},
            {"name": "HLS", "url_resolved": "https://c/live.m3u8", "votes": 99},
        ])

    client = directory_client(handler)
    assert [r["name"] for r in run(radio_manager.browse_directory("popular", client=client))] == ["B", "A"]
    assert seen[-1][0].startswith("/json/stations/topvote/")
    run(radio_manager.browse_directory("tag", "Hip Hop", client=client))
    assert seen[-1][0] == "/json/stations/bytagexact/hip%20hop" and seen[-1][1]["order"] == "votes"
    run(radio_manager.browse_directory("country", "France", client=client))
    assert seen[-1][0] == "/json/stations/bycountry/France"
    assert run(radio_manager.browse_directory("tag", "", client=client)) == []          # nothing to browse
    assert run(radio_manager.browse_directory("bogus", "x", client=client)) == []

    def down(request):
        return httpx.Response(502)

    assert run(radio_manager.browse_directory("popular", client=directory_client(down))) == []


# ─── local commercial stations: spacing / word-order tolerant search ──────────

LOCAL_DIRECTORY = [
    {"name": "MOVE 100", "url_resolved": "https://a/move100", "tags": "adult contemporary", "country": "Canada", "votes": 4},
    {"name": "JACK 92.9 Vancouver", "url_resolved": "https://a/jack929", "tags": "rock,pop", "country": "Canada", "votes": 7},
    {"name": "Q104 Halifax's Rock Station", "url_resolved": "https://a/q104", "tags": "rock", "country": "Canada", "votes": 6},
    {"name": "The Breeze 96.5", "url_resolved": "https://a/breeze965", "tags": "easy listening", "country": "Canada", "votes": 3},
    {"name": "Breeze FM London", "url_resolved": "https://a/breezelondon", "tags": "easy listening", "votes": 30},
    {"name": "Jack FM Oxfordshire", "url_resolved": "https://a/jackox", "tags": "rock", "votes": 40},
    {"name": "Jazz24", "url_resolved": "https://a/jazz24", "tags": "jazz", "votes": 50},
]


def directory_like_handler(request: httpx.Request):
    """Mimics radio-browser.info: `name` is a case-insensitive substring match
    on the station name, `tag` a substring match on tags."""
    params = request.url.params
    out = LOCAL_DIRECTORY
    if "name" in params:
        out = [s for s in out if params["name"].lower() in s["name"].lower()]
    elif "tag" in params:
        out = [s for s in out if params["tag"].lower() in s["tags"].lower()]
    return httpx.Response(200, json=out)


@pytest.mark.parametrize("query, expected_first", [
    ("Move100", "MOVE 100"),                 # no space typed, space in the directory name
    ("move 100", "MOVE 100"),
    ("Jack 92.9", "JACK 92.9 Vancouver"),    # direct phrase hit
    ("jack 929", "JACK 92.9 Vancouver"),     # frequency without the dot
    ("Q104", "Q104 Halifax's Rock Station"),
    ("q 104", "Q104 Halifax's Rock Station"),
    ("96.5 the breeze", "The Breeze 96.5"),  # word order differs from the listing
    ("the breeze 96.5", "The Breeze 96.5"),
])
def test_search_directory_finds_local_stations(run, query, expected_first):
    results = run(radio_manager.search_directory(query, client=directory_client(directory_like_handler)))
    assert results and results[0]["name"] == expected_first, [r["name"] for r in results]


def test_search_directory_fallback_requires_all_words(run):
    # "breeze 96.5": the phrase matches nothing, the word search returns both Breeze
    # stations, but only the one whose name also contains 96.5 is kept.
    results = run(radio_manager.search_directory("breeze 96.5", client=directory_client(directory_like_handler)))
    assert [r["name"] for r in results] == ["The Breeze 96.5"]
    # A plain word keeps all its matches, most voted first
    results = run(radio_manager.search_directory("breeze", client=directory_client(directory_like_handler)))
    assert [r["name"] for r in results] == ["Breeze FM London", "The Breeze 96.5"]


def test_search_helpers():
    assert radio_manager.search_tokens("Move100") == ["move", "100"]
    assert radio_manager.search_tokens("96.5 The Breeze FM") == ["96.5", "breeze"]
    assert radio_manager.search_tokens("the radio station") == []
    assert radio_manager.query_variants("Move100") == ["Move100", "Move 100"]
    assert radio_manager.query_variants("Q 104") == ["Q 104", "Q104"]
    assert radio_manager.query_variants("  ") == []
    ranked = radio_manager.rank_by_query("jack 92.9", [
        {"name": "Jack-92-9", "votes": 1}, {"name": "Jack FM", "votes": 99}, {"name": "JACK 929 HD", "votes": 5}])
    assert [r["name"] for r in ranked] == ["JACK 929 HD", "Jack-92-9"]


def test_unverified_stations_are_returned_but_ranked_after_verified(run):
    stations = [
        {"name": "Virgin Radio Halifax 101.3", "url_resolved": "https://a/cjch", "votes": 200, "lastcheckok": 0, "country": "Canada", "tags": "pop"},
        {"name": "Virgin Radio UK", "url_resolved": "https://a/vruk", "votes": 50, "lastcheckok": 1, "tags": "pop"},
        {"name": "Virgin Radio Dubai", "url_resolved": "https://a/vrd", "votes": 80, "lastcheckok": 1, "tags": "pop"},
    ]

    def handler(request):                      # substring semantics like the real directory
        params = request.url.params
        out = stations
        if "name" in params:
            out = [x for x in out if params["name"].lower() in x["name"].lower()]
        elif "tag" in params:
            out = [x for x in out if params["tag"].lower() in x["tags"]]
        return httpx.Response(200, json=out)

    results = run(radio_manager.search_directory("virgin", client=directory_client(handler)))
    assert [r["name"] for r in results] == ["Virgin Radio Dubai", "Virgin Radio UK", "Virgin Radio Halifax 101.3"]
    assert results[-1]["online"] is False and results[0]["online"] is True
    # A precise query still finds the geo-blocked station
    results = run(radio_manager.search_directory("virgin 101.3", client=directory_client(handler)))
    assert [r["name"] for r in results] == ["Virgin Radio Halifax 101.3"]


# ─── TuneIn provider helpers ──────────────────────────────────────────────────

def test_tunein_entry_and_merge():
    from conftest import TUNEIN_JACK
    e = radio_manager._tunein_entry(TUNEIN_JACK)
    assert e["name"] == "Jack 92.9" and e["genre"] == "Adult Hits" and e["provider"] == "tunein"
    assert e["streamUrl"] == "https://opml.radiotime.com/Tune.ashx?id=s22216&formats=mp3,aac,ogg"
    assert e["description"] == "MP3, 64 kbps" and e["nowPlaying"] == "Halifax's Jack FM"
    assert radio_manager._tunein_entry({"text": "no guide"}) is None
    merged = radio_manager.merge_directory_results(
        [{"id": "a", "name": "Jack 92.9 Halifax"}], [{"id": "b", "name": "JACK 92.9 HALIFAX"}, {"id": "c", "name": "Move 100.1"}], 10)
    assert [m["id"] for m in merged] == ["a", "c"]


def test_search_tunein_parses_nested_body_and_ranks_by_query(run):
    from conftest import TUNEIN_JACK, TUNEIN_MOVE

    def handler(request):
        assert request.url.params["render"] == "json"
        return httpx.Response(200, json={"body": [{"element": "outline", "text": "Stations", "children": [TUNEIN_MOVE, TUNEIN_JACK]},
                                                  {"element": "outline", "type": "link", "text": "Shows", "URL": "x"}]})

    results = run(radio_manager.search_tunein("jack 929", client=directory_client(handler)))
    assert [r["name"] for r in results] == ["Jack 92.9"]
    results = run(radio_manager.search_tunein("halifax", client=directory_client(handler)))
    assert len(results) == 2                                   # nothing matched by name: keep all
    assert run(radio_manager.search_tunein("", client=directory_client(handler))) == []

    def boom(request):
        raise httpx.ConnectError("offline")

    assert run(radio_manager.search_tunein("jack", client=directory_client(boom))) == []


def test_resolve_tunein_url_prefers_direct_non_hls(run):
    def handler(request):
        return httpx.Response(200, json={"body": [
            {"element": "audio", "url": "https://a/live.m3u8", "media_type": "hls", "reliability": 99},
            {"element": "audio", "url": "https://a/relay.mp3", "media_type": "mp3", "is_direct": False, "reliability": 90},
            {"element": "audio", "url": "https://a/direct.aac", "media_type": "aac", "is_direct": True, "reliability": 80},
        ]})

    assert run(radio_manager.resolve_tunein_url("https://opml.radiotime.com/Tune.ashx?id=s1", client=directory_client(handler))) == "https://a/direct.aac"
    assert run(radio_manager.resolve_tunein_url("https://ice.example/stream", client=directory_client(handler))) == "https://ice.example/stream"

    def hls_only(request):
        return httpx.Response(200, json={"body": [{"element": "audio", "url": "https://a/live.m3u8", "is_hls_advanced": "true"}]})

    assert run(radio_manager.resolve_tunein_url("https://opml.radiotime.com/Tune.ashx?id=s1", client=directory_client(hls_only))) is None


def test_frequency_tolerant_matching():
    stations = [{"name": "Move 100 Halifax", "votes": 3}, {"name": "Jack 96.9 Vancouver", "votes": 5}, {"name": "Radio 1001", "votes": 1}]
    assert [s["name"] for s in radio_manager.rank_by_query("move 100.1", stations)] == ["Move 100 Halifax"]
    assert [s["name"] for s in radio_manager.rank_by_query("jack 96.9", stations)] == ["Jack 96.9 Vancouver"]
    assert radio_manager.rank_by_query("jack 92.9", stations) == []


def test_search_directory_by_place_only(run):
    def handler(request):
        params = request.url.params
        data = [
            {"name": "CBC Radio One Halifax", "url_resolved": "https://a/cbc", "tags": "news", "state": "Nova Scotia", "votes": 9},
            {"name": "Jazz FM", "url_resolved": "https://a/jazz", "tags": "jazz,halifax", "state": "", "votes": 5},
            {"name": "Paris Lounge", "url_resolved": "https://a/paris", "tags": "lounge", "state": "Ile-de-France", "votes": 50},
        ]
        if "state" in params:
            data = [d for d in data if params["state"].lower() in d["state"].lower()]
        elif "name" in params:
            data = [d for d in data if params["name"].lower() in d["name"].lower()]
        elif "tag" in params:
            data = [d for d in data if params["tag"].lower() in d["tags"]]
        return httpx.Response(200, json=data)

    results = run(radio_manager.search_directory("", place="Halifax", client=directory_client(handler)))
    assert [r["name"] for r in results] == ["CBC Radio One Halifax", "Jazz FM"]      # Paris never mentions Halifax
    results = run(radio_manager.search_directory("jazz", place="Halifax", client=directory_client(handler)))
    assert [r["name"] for r in results] == ["Jazz FM"]

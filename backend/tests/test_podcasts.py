"""podcasts.py — RSS parsing, subscriptions, spoken podcast/episode resolution, directory search."""

import httpx
import pytest

from conftest import podcast_manager


FEED_URL = "https://feeds.example.com/nightshift.xml"

SAMPLE_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel>
    <title>Night Shift Radio Hour</title>
    <link>https://nightshift.example</link>
    <description><![CDATA[<p>Late-night <b>stories</b> &amp; music.</p>]]></description>
    <itunes:author>Dana Rivers</itunes:author>
    <itunes:image href="https://nightshift.example/art.jpg"/>
    <item>
      <title>Episode 12: The Last Train</title>
      <guid>ns-12</guid>
      <pubDate>Tue, 04 Mar 2025 06:00:00 +0000</pubDate>
      <enclosure url="https://cdn.example/ns/12.mp3" type="audio/mpeg" length="1000"/>
      <itunes:duration>1:02:03</itunes:duration>
      <itunes:episode>12</itunes:episode>
      <itunes:summary>A conductor who never sleeps.</itunes:summary>
    </item>
    <item>
      <title>Episode 11: Sleep Science</title>
      <guid>ns-11</guid>
      <pubDate>Tue, 25 Feb 2025 06:00:00 +0000</pubDate>
      <enclosure url="https://cdn.example/ns/11.mp3" type="audio/mpeg"/>
      <itunes:duration>2520</itunes:duration>
      <itunes:episode>11</itunes:episode>
      <description>Why we dream, with Dr. Lee.</description>
    </item>
    <item>
      <title>Bonus: Video tour</title>
      <guid>ns-video</guid>
      <pubDate>Mon, 24 Feb 2025 06:00:00 +0000</pubDate>
      <enclosure url="https://cdn.example/ns/tour.mov" type="video/quicktime"/>
    </item>
    <item>
      <title>Blog post without audio</title>
      <guid>ns-blog</guid>
      <pubDate>Sun, 23 Feb 2025 06:00:00 +0000</pubDate>
    </item>
    <item>
      <title>Episode 10: City Lights</title>
      <guid>ns-10</guid>
      <pubDate>Tue, 18 Feb 2025 06:00:00 +0000</pubDate>
      <enclosure url="https://cdn.example/ns/10.mp3" type="audio/mpeg"/>
      <itunes:duration>45:00</itunes:duration>
      <itunes:episode>10</itunes:episode>
    </item>
    <item>
      <title>Trailer</title>
      <enclosure url="https://cdn.example/ns/trailer.mp3" type="audio/mpeg"/>
      <itunes:duration>90</itunes:duration>
    </item>
  </channel>
</rss>
"""


@pytest.fixture
def parsed():
    return podcast_manager.parse_feed(SAMPLE_FEED, FEED_URL)


@pytest.fixture
def subscribed(parsed):
    pod, created = podcast_manager.upsert_podcast(parsed)
    assert created
    return pod


# ─── parsing ──────────────────────────────────────────────────────────────────

def test_parse_feed_channel_metadata(parsed):
    assert parsed["id"] == podcast_manager.podcast_id_for(FEED_URL)
    assert parsed["title"] == "Night Shift Radio Hour"
    assert parsed["author"] == "Dana Rivers"
    assert parsed["description"] == "Late-night stories & music."          # HTML stripped, entities decoded
    assert parsed["artworkUrl"] == "https://nightshift.example/art.jpg"
    assert parsed["homepage"] == "https://nightshift.example"
    assert parsed["feedUrl"] == FEED_URL
    assert parsed["lastFetchedAt"] > 0


def test_parse_feed_episodes_newest_first_audio_only(parsed):
    titles = [e["title"] for e in parsed["episodes"]]
    # video-only and enclosure-less items dropped; undated trailer goes last
    assert titles == ["Episode 12: The Last Train", "Episode 11: Sleep Science", "Episode 10: City Lights", "Trailer"]
    assert parsed["episodeCount"] == 4
    ep12, ep11, ep10, trailer = parsed["episodes"]
    assert ep12["duration"] == 3723 and ep11["duration"] == 2520 and ep10["duration"] == 2700 and trailer["duration"] == 90
    assert ep12["episodeNumber"] == 12 and trailer["episodeNumber"] is None
    assert ep12["publishedAt"] == 1741068000 and trailer["publishedAt"] is None
    assert ep12["description"] == "A conductor who never sleeps."
    assert ep11["description"] == "Why we dream, with Dr. Lee."
    assert ep12["id"] == podcast_manager.episode_id_for("ns-12")
    assert trailer["id"] == podcast_manager.episode_id_for("https://cdn.example/ns/trailer.mp3")   # guid falls back to URL
    assert ep12["audioUrl"] == "https://cdn.example/ns/12.mp3" and ep12["mimeType"] == "audio/mpeg"


@pytest.mark.parametrize("bad", ["not xml at all", "<html><body>nope</body></html>", "<feed xmlns='http://www.w3.org/2005/Atom'><title>Atom</title></feed>"])
def test_parse_feed_rejects_non_rss(bad):
    with pytest.raises(ValueError):
        podcast_manager.parse_feed(bad, FEED_URL)


@pytest.mark.parametrize("value, expected", [
    ("3723", 3723), ("1:02:03", 3723), ("62:03", 3723), ("45:00", 2700), ("90", 90), ("", None), (None, None),
    ("1:2:3:4", None), ("abc", None), ("12.5", 12),
])
def test_parse_duration(value, expected):
    assert podcast_manager.parse_duration(value) == expected


def test_parse_pubdate_formats():
    assert podcast_manager.parse_pubdate("Tue, 04 Mar 2025 06:00:00 +0000") == 1741068000
    assert podcast_manager.parse_pubdate("2025-03-04T06:00:00Z") == 1741068000
    assert podcast_manager.parse_pubdate("yesterday") is None
    assert podcast_manager.parse_pubdate("") is None


def test_strip_html_truncates():
    assert podcast_manager.strip_html("<p>Hi &amp; bye</p>", 100) == "Hi & bye"
    assert podcast_manager.strip_html("x" * 50, 10) == "xxxxxxxxxx…"


# ─── persistence ──────────────────────────────────────────────────────────────

def test_upsert_then_refresh_keeps_identity_and_created_at(parsed):
    first, created = podcast_manager.upsert_podcast(parsed)
    assert created and first["createdAt"] > 0
    newer = dict(parsed, title="Night Shift Radio Hour (renamed)", lastFetchedAt=parsed["lastFetchedAt"] + 10)
    second, created2 = podcast_manager.upsert_podcast(newer)
    assert not created2
    assert second["id"] == first["id"] and second["createdAt"] == first["createdAt"]
    assert [p["title"] for p in podcast_manager.load_podcasts()] == ["Night Shift Radio Hour (renamed)"]


def test_summaries_and_delete(subscribed):
    listing = podcast_manager.get_all_podcasts()
    assert len(listing) == 1
    summary = listing[0]
    assert summary["episodeCount"] == 4 and "episodes" not in summary
    assert summary["latestEpisode"]["title"] == "Episode 12: The Last Train"
    assert podcast_manager.delete_podcast(subscribed["id"]) is True
    assert podcast_manager.delete_podcast(subscribed["id"]) is False
    assert podcast_manager.get_all_podcasts() == []


def test_is_stale(parsed):
    assert not podcast_manager.is_stale(parsed)
    assert podcast_manager.is_stale(parsed, now=parsed["lastFetchedAt"] + podcast_manager.STALE_AFTER_S + 1)
    assert podcast_manager.is_stale({"lastFetchedAt": 0})


def test_fetch_and_refresh_via_mock_transport(run):
    def handler(request):
        assert request.url == FEED_URL
        return httpx.Response(200, content=SAMPLE_FEED.encode("utf-8"), headers={"content-type": "application/rss+xml"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    parsed = run(podcast_manager.fetch_feed(FEED_URL, client=client))
    pod, _ = podcast_manager.upsert_podcast(parsed)

    refreshed, err = run(podcast_manager.refresh_podcast(pod["id"], client=client))
    assert err is None and refreshed["id"] == pod["id"]

    def down(request):
        return httpx.Response(503)

    cached, err = run(podcast_manager.refresh_podcast(pod["id"], client=httpx.AsyncClient(transport=httpx.MockTransport(down))))
    assert cached["id"] == pod["id"] and "HTTP 503" in err          # cached copy still returned

    def offline(request):
        raise httpx.ConnectError("no network")

    with pytest.raises(ValueError, match="ConnectError"):
        run(podcast_manager.fetch_feed(FEED_URL, client=httpx.AsyncClient(transport=httpx.MockTransport(offline))))
    assert run(podcast_manager.refresh_podcast("pc_missing", client=client)) == (None, "Podcast not found.")


# ─── spoken resolution ────────────────────────────────────────────────────────

@pytest.fixture
def two_podcasts(subscribed):
    other = podcast_manager.parse_feed(SAMPLE_FEED.replace("Night Shift Radio Hour", "The Daily Brief").replace("Dana Rivers", "News Desk"), "https://feeds.example.com/daily.xml")
    podcast_manager.upsert_podcast(other)
    return subscribed


@pytest.mark.parametrize("spoken, expected", [
    ("night shift", "Night Shift Radio Hour"),
    ("play the night shift podcast", "Night Shift Radio Hour"),
    ("the daily brief", "The Daily Brief"),
    ("daily", "The Daily Brief"),
    ("dana rivers show", "Night Shift Radio Hour"),        # author match
    ("cooking with fire", None),
])
def test_find_podcast(two_podcasts, spoken, expected):
    pod = podcast_manager.find_podcast(spoken)
    assert (pod["title"] if pod else None) == expected


@pytest.mark.parametrize("spoken, expected_title", [
    ("", "Episode 12: The Last Train"),
    ("latest", "Episode 12: The Last Train"),
    ("the most recent one", "Episode 12: The Last Train"),
    ("newest episode", "Episode 12: The Last Train"),
    ("oldest", "Trailer"),
    ("second latest", "Episode 11: Sleep Science"),
    ("the third one", "Episode 10: City Lights"),
    ("episode 11", "Episode 11: Sleep Science"),
    ("ep 10", "Episode 10: City Lights"),
    ("#12", "Episode 12: The Last Train"),
    ("the one about sleep", "Episode 11: Sleep Science"),
    ("city lights", "Episode 10: City Lights"),
    ("last train", "Episode 12: The Last Train"),
    ("trailer", "Trailer"),
])
def test_resolve_episode(parsed, spoken, expected_title):
    ep, err = podcast_manager.resolve_episode(parsed, spoken)
    assert err is None, err
    assert ep["title"] == expected_title


def test_resolve_episode_errors(parsed):
    ep, err = podcast_manager.resolve_episode(parsed, "episode 99")
    assert ep is None and "No episode number 99" in err
    ep, err = podcast_manager.resolve_episode(parsed, "the tenth one")
    assert ep is None and "only has 4 episodes" in err
    ep, err = podcast_manager.resolve_episode(parsed, "quantum gardening")
    assert ep is None and "Recent episodes:" in err and "'Episode 12: The Last Train'" in err
    ep, err = podcast_manager.resolve_episode({"title": "Empty", "episodes": []}, "")
    assert ep is None and "no episodes" in err


def test_resolve_episode_next_and_previous_relative_to_current(parsed):
    eps = parsed["episodes"]
    ep, _ = podcast_manager.resolve_episode(parsed, "next", current_episode_id=eps[1]["id"])
    assert ep["title"] == "Episode 12: The Last Train"            # newer
    ep, _ = podcast_manager.resolve_episode(parsed, "the previous one", current_episode_id=eps[1]["id"])
    assert ep["title"] == "Episode 10: City Lights"               # older
    ep, err = podcast_manager.resolve_episode(parsed, "next", current_episode_id=eps[0]["id"])
    assert ep is None and "already the newest" in err
    ep, err = podcast_manager.resolve_episode(parsed, "previous", current_episode_id=eps[-1]["id"])
    assert ep is None and "already the oldest" in err
    # Without a current episode, "next" is just a title search that finds nothing
    ep, err = podcast_manager.resolve_episode(parsed, "next")
    assert ep is None and "No episode" in err


def test_episode_label_and_duration_format(parsed):
    label = podcast_manager.episode_label(parsed["episodes"][0])
    assert label == "'Episode 12: The Last Train' (episode 12) [1h 02m] from Mar 04, 2025"
    assert podcast_manager.format_duration(2520) == "42 min"
    assert podcast_manager.format_duration(90) == "1 min"
    assert podcast_manager.format_duration(30) == "30 sec"
    assert podcast_manager.format_duration(None) == "unknown length"


# ─── directory ────────────────────────────────────────────────────────────────

def test_search_directory_maps_itunes_results(run):
    def handler(request):
        assert request.url.params["media"] == "podcast" and request.url.params["term"] == "night shift"
        return httpx.Response(200, json={"results": [
            {"collectionName": "Night Shift Radio Hour", "artistName": "Dana Rivers", "feedUrl": FEED_URL,
             "artworkUrl600": "https://a/600.jpg", "primaryGenreName": "Fiction", "trackCount": 12},
            {"collectionName": "No feed", "artistName": "x"},                       # unusable
        ]})

    results = run(podcast_manager.search_directory("night shift", client=httpx.AsyncClient(transport=httpx.MockTransport(handler))))
    assert len(results) == 1
    r = results[0]
    assert r["title"] == "Night Shift Radio Hour" and r["feedUrl"] == FEED_URL and r["id"] == podcast_manager.podcast_id_for(FEED_URL)
    assert r["artworkUrl"] == "https://a/600.jpg" and r["genre"] == "Fiction" and r["episodeCount"] == 12


def test_search_directory_failures_return_empty(run):
    def boom(request):
        raise httpx.ReadTimeout("slow")

    client = httpx.AsyncClient(transport=httpx.MockTransport(boom))
    assert run(podcast_manager.search_directory("x", client=client)) == []
    assert run(podcast_manager.search_directory("", client=client)) == []


# ─── starter picks ────────────────────────────────────────────────────────────

def test_starter_podcasts_seed_without_episodes_when_no_file(sources_files):
    sources_files["podcasts"].unlink()
    seeded = podcast_manager.load_podcasts()
    assert len(seeded) == len(podcast_manager.DEFAULT_PODCASTS) >= 8
    for p in seeded:
        assert p["source"] == "default" and p["episodes"] == [] and p["lastFetchedAt"] == 0
        assert p["feedUrl"].startswith("https://") and p["title"] and p["description"]
        assert p["id"] == podcast_manager.podcast_id_for(p["feedUrl"])
    assert len({p["id"] for p in seeded}) == len(seeded)
    summaries = podcast_manager.get_all_podcasts()
    assert all(s["needsRefresh"] is True and s["source"] == "default" and s["episodeCount"] == 0 for s in summaries)
    # Unsubscribing from everything leaves an empty file; defaults do not come back.
    for p in seeded:
        assert podcast_manager.delete_podcast(p["id"])
    assert podcast_manager.load_podcasts() == [] and sources_files["podcasts"].exists()


def test_refreshing_a_starter_keeps_source_and_curated_blurb(run):
    starter = podcast_manager._default_entry({"title": "Night Shift Radio Hour", "author": "x", "feedUrl": FEED_URL,
                                              "description": "Curated blurb"})
    podcast_manager.save_podcasts([starter])
    feed_without_description = SAMPLE_FEED.replace("<description><![CDATA[<p>Late-night <b>stories</b> &amp; music.</p>]]></description>", "")

    def handler(request):
        return httpx.Response(200, content=feed_without_description.encode())

    pod, err = run(podcast_manager.refresh_podcast(starter["id"], client=httpx.AsyncClient(transport=httpx.MockTransport(handler))))
    assert err is None
    assert pod["source"] == "default" and pod["description"] == "Curated blurb"
    assert pod["episodeCount"] == 4 and pod["lastFetchedAt"] > 0
    assert podcast_manager.get_all_podcasts()[0]["needsRefresh"] is False


def test_refresh_unfetched_only_touches_never_fetched_feeds(run):
    fresh = podcast_manager.parse_feed(SAMPLE_FEED, "https://feeds.example.com/fresh.xml")
    podcast_manager.upsert_podcast(fresh)
    a = podcast_manager._default_entry({"title": "A", "feedUrl": "https://feeds.example.com/a.xml"})
    b = podcast_manager._default_entry({"title": "B", "feedUrl": "https://feeds.example.com/b.xml"})
    podcast_manager.save_podcasts(podcast_manager.load_podcasts() + [a, b])
    hit = []

    def handler(request):
        hit.append(request.url.path)
        if request.url.path == "/b.xml":
            return httpx.Response(500)
        return httpx.Response(200, content=SAMPLE_FEED.encode())

    results = run(podcast_manager.refresh_unfetched(client=httpx.AsyncClient(transport=httpx.MockTransport(handler))))
    assert sorted(hit) == ["/a.xml", "/b.xml"]                  # the already-fetched feed was left alone
    assert results[a["id"]] is None and "HTTP 500" in results[b["id"]]
    by_id = {p["id"]: p for p in podcast_manager.load_podcasts()}
    assert by_id[a["id"]]["episodeCount"] == 4 and by_id[b["id"]]["lastFetchedAt"] == 0
    assert run(podcast_manager.refresh_unfetched(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))) == {b["id"]: "Feed server answered HTTP 500."}


# ─── newest episodes across subscriptions ─────────────────────────────────────

def test_latest_episodes_across_subscriptions(subscribed_podcast):
    # A second show whose newest episode is older than Night Shift's latest but newer than its second
    other = podcast_manager.parse_feed(SAMPLE_FEED.replace("Night Shift Radio Hour", "Other Show").replace("ns-12", "os-1")
                                       .replace("Tue, 04 Mar 2025", "Mon, 03 Mar 2025"), "https://feeds.example.com/other.xml")
    podcast_manager.upsert_podcast(other)
    items = podcast_manager.latest_episodes(limit=10)
    assert [(i["podcast"]["title"], i["episode"]["title"]) for i in items] == [
        ("Night Shift Radio Hour", "Episode 12: The Last Train"), ("Other Show", "Episode 12: The Last Train")]   # one per show, newest first
    assert "episodes" not in items[0]["podcast"]                                                                # summaries only
    two_each = podcast_manager.latest_episodes(limit=10, per_podcast=2)
    assert [i["episode"]["title"] for i in two_each] == ["Episode 12: The Last Train", "Episode 12: The Last Train",
                                                          "Episode 11: Sleep Science", "Episode 11: Sleep Science"]
    assert len(podcast_manager.latest_episodes(limit=1)) == 1
    assert podcast_manager.latest_episodes(podcasts=[]) == []

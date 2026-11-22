"""
AI DJ — Podcast Sources

Podcast subscriptions (RSS feeds, JSON-backed like playlists.py) with a cached
episode list, RSS 2.0 / iTunes-namespace feed parsing (stdlib only), an
optional online directory (Apple's public podcast search, no API key), and
spoken-name resolution for podcasts ("the daily") and episodes ("latest",
"episode 45", "the one about sleep").
"""

import re
import json
import time
import hashlib
import html
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from xml.etree import ElementTree as ET

import httpx

try:
    from backend.textmatch import best_match, normalize
except ImportError:
    from textmatch import best_match, normalize

PODCASTS_FILE = Path(__file__).parent / "podcasts.json"

USER_AGENT = "AI-DJ/1.0 (+https://github.com/ai-dj; local prototype)"
ITUNES_SEARCH_URL = "https://itunes.apple.com/search"
MAX_EPISODES_STORED = 100
# Feeds older than this are re-fetched before "play the latest episode".
STALE_AFTER_S = 30 * 60

_ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"

# Starter picks, seeded the first time the app runs without a podcasts file.
# Only feed metadata is stored here; episodes are fetched lazily (background
# task at startup, or on first open/play), so seeding also works offline.
# Marked source="default" so the GUI and the DJ can explain they are starters.
DEFAULT_PODCASTS = [
    {"title": "Up First", "author": "NPR", "feedUrl": "https://feeds.npr.org/510318/podcast.xml",
     "description": "The biggest stories and ideas, every weekday morning in about 15 minutes."},
    {"title": "The Daily", "author": "The New York Times", "feedUrl": "https://feeds.simplecast.com/54nAGcIl",
     "description": "Twenty minutes a day, five days a week, on the biggest story of the day."},
    {"title": "Global News Podcast", "author": "BBC World Service", "feedUrl": "https://podcasts.files.bbci.co.uk/p02nq0gn.rss",
     "description": "The day's top stories from BBC News, twice daily on weekdays."},
    {"title": "Stuff You Should Know", "author": "iHeartPodcasts", "feedUrl": "https://feeds.megaphone.fm/stuffyoushouldknow",
     "description": "Josh and Chuck explain how things work — from champagne to satellites."},
    {"title": "This American Life", "author": "This American Life", "feedUrl": "https://www.thisamericanlife.org/podcast/rss.xml",
     "description": "Weekly public radio program of journalism and storytelling, hosted by Ira Glass."},
    {"title": "Planet Money", "author": "NPR", "feedUrl": "https://feeds.npr.org/510289/podcast.xml",
     "description": "The economy explained, with stories and surprises."},
    {"title": "TED Talks Daily", "author": "TED", "feedUrl": "https://feeds.feedburner.com/TEDTalks_audio",
     "description": "Thought-provoking ideas on every subject imaginable, every weekday."},
    {"title": "99% Invisible", "author": "Roman Mars", "feedUrl": "https://feeds.simplecast.com/BqbsxVfO",
     "description": "The unnoticed architecture and design that shape our world."},
    {"title": "Radiolab", "author": "WNYC Studios", "feedUrl": "https://feeds.simplecast.com/EmVW7VGp",
     "description": "Investigating a strange world — science, philosophy and the human experience."},
    {"title": "Conan O'Brien Needs A Friend", "author": "Team Coco", "feedUrl": "https://feeds.simplecast.com/dHoohVNH",
     "description": "Conan talks with friends and guests about friendship and everything else."},
]
_PODCAST_FILLER = {"podcast", "podcasts", "show", "episode", "episodes", "latest", "newest", "new"}


def podcast_id_for(feed_url: str) -> str:
    return "pc_" + hashlib.md5((feed_url or "").strip().encode("utf-8")).hexdigest()[:10]


def episode_id_for(key: str) -> str:
    return "ep_" + hashlib.md5((key or "").strip().encode("utf-8")).hexdigest()[:10]


# ─── Feed parsing ─────────────────────────────────────────────────────────────

def parse_duration(value) -> Optional[int]:
    """'3723' -> 3723, '1:02:03' -> 3723, '62:03' -> 3723, '' -> None."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    if re.fullmatch(r"\d+(\.\d+)?", s):
        return int(float(s))
    parts = s.split(":")
    if not all(re.fullmatch(r"\d+(\.\d+)?", p.strip()) for p in parts) or len(parts) > 3:
        return None
    total = 0
    for p in parts:
        total = total * 60 + int(float(p))
    return total


def parse_pubdate(value) -> Optional[int]:
    if not value:
        return None
    s = str(value).strip()
    try:
        return int(parsedate_to_datetime(s).timestamp())
    except Exception:
        pass
    if re.match(r"^\d{4}-\d{2}-\d{2}", s):             # ISO dates occasionally sneak in
        try:
            import datetime as _dt
            dt = _dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=_dt.timezone.utc)
            return int(dt.timestamp())
        except Exception:
            return None
    return None


def strip_html(text: str, limit: int = 600) -> str:
    if not text:
        return ""
    t = re.sub(r"<[^>]+>", " ", text)
    t = html.unescape(t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:limit].rstrip() + ("…" if len(t) > limit else "")


def _text(el: Optional[ET.Element], *paths: str) -> str:
    if el is None:
        return ""
    for p in paths:
        found = el.find(p)
        if found is not None and (found.text or "").strip():
            return found.text.strip()
    return ""


def parse_feed(xml_text: str, feed_url: str) -> Dict:
    """Parse an RSS 2.0 podcast feed into the stored podcast shape.

    Episodes need an <enclosure> with an audio URL; items without one (blog
    posts, video-only) are skipped. Newest episode first.
    Raises ValueError for non-RSS / unparsable input.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise ValueError(f"Feed is not well-formed XML: {e}") from e

    channel = root.find("channel")
    if channel is None:
        raise ValueError("Not an RSS podcast feed (no <channel>)")

    it = f"{{{_ITUNES_NS}}}"
    title = _text(channel, "title") or feed_url
    author = _text(channel, f"{it}author", "managingEditor")
    image = ""
    img_el = channel.find(f"{it}image")
    if img_el is not None:
        image = (img_el.get("href") or "").strip()
    if not image:
        image = _text(channel, "image/url")

    episodes: List[Dict] = []
    for item in channel.findall("item"):
        enclosure = item.find("enclosure")
        audio_url = (enclosure.get("url") or "").strip() if enclosure is not None else ""
        if not audio_url.lower().startswith("http"):
            continue
        mime = (enclosure.get("type") or "").strip().lower() if enclosure is not None else ""
        if mime and not (mime.startswith("audio/") or mime in {"application/octet-stream", "video/mp4"}):
            continue
        guid = _text(item, "guid") or audio_url
        ep_title = _text(item, "title") or "Untitled episode"
        ep_num = _text(item, f"{it}episode")
        season = _text(item, f"{it}season")
        episodes.append({
            "id": episode_id_for(guid),
            "title": ep_title[:200],
            "audioUrl": audio_url[:2000],
            "mimeType": mime or "audio/mpeg",
            "duration": parse_duration(_text(item, f"{it}duration")),
            "publishedAt": parse_pubdate(_text(item, "pubDate")),
            "description": strip_html(_text(item, f"{it}summary", "description")),
            "episodeNumber": int(ep_num) if ep_num.isdigit() else None,
            "season": int(season) if season.isdigit() else None,
            "link": _text(item, "link")[:500],
        })

    # Newest first; undated episodes keep feed order at the end.
    dated = [e for e in episodes if e["publishedAt"] is not None]
    undated = [e for e in episodes if e["publishedAt"] is None]
    dated.sort(key=lambda e: e["publishedAt"], reverse=True)
    episodes = (dated + undated)[:MAX_EPISODES_STORED]

    return {
        "id": podcast_id_for(feed_url),
        "title": title[:200],
        "author": author[:120],
        "description": strip_html(_text(channel, f"{it}summary", "description"), 400),
        "feedUrl": feed_url,
        "homepage": _text(channel, "link")[:500],
        "artworkUrl": image[:500],
        "episodes": episodes,
        "episodeCount": len(episodes),
        "lastFetchedAt": int(time.time()),
    }


async def fetch_feed(feed_url: str, client: Optional[httpx.AsyncClient] = None) -> Dict:
    """Download and parse a feed. Raises ValueError with a user-readable
    message on network or parse problems."""
    own = client is None
    client = client or httpx.AsyncClient(timeout=15.0, follow_redirects=True, headers={"User-Agent": USER_AGENT})
    try:
        try:
            resp = await client.get(feed_url)
        except httpx.HTTPError as e:
            raise ValueError(f"Could not download the feed ({e.__class__.__name__}).") from e
        if resp.status_code != 200:
            raise ValueError(f"Feed server answered HTTP {resp.status_code}.")
        return parse_feed(resp.text, str(feed_url))
    finally:
        if own:
            await client.aclose()


# ─── Persistence ──────────────────────────────────────────────────────────────

def _default_entry(raw: Dict) -> Dict:
    now = int(time.time())
    return {
        "id": podcast_id_for(raw["feedUrl"]), "title": raw["title"], "author": raw.get("author", ""),
        "description": raw.get("description", ""), "feedUrl": raw["feedUrl"], "homepage": "", "artworkUrl": "",
        "episodes": [], "episodeCount": 0, "lastFetchedAt": 0, "source": "default",
        "createdAt": now, "updatedAt": now, "lastPlayedAt": 0,
    }


def load_podcasts() -> List[Dict]:
    """Subscriptions. Seeds the starter picks (without episodes) when no file
    exists yet; a user who unsubscribes from all keeps an empty file."""
    if not PODCASTS_FILE.exists():
        seeded = [_default_entry(d) for d in DEFAULT_PODCASTS]
        save_podcasts(seeded)
        return seeded
    try:
        with open(PODCASTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return [p for p in data if isinstance(p, dict) and p.get("feedUrl")]
    except Exception as e:
        print(f"[Podcasts] Error loading podcasts: {e}")
    return []


def save_podcasts(podcasts: List[Dict]) -> bool:
    try:
        tmp = PODCASTS_FILE.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(podcasts, f, indent=2, ensure_ascii=False)
        tmp.replace(PODCASTS_FILE)
        return True
    except Exception as e:
        print(f"[Podcasts] Error saving podcasts: {e}")
        return False


def summarize(p: Dict) -> Dict:
    """Listing shape for the GUI/tools: no episode bodies."""
    eps = p.get("episodes") or []
    latest = eps[0] if eps else None
    return {
        "id": p.get("id"), "title": p.get("title"), "author": p.get("author", ""),
        "description": p.get("description", ""), "feedUrl": p.get("feedUrl"),
        "homepage": p.get("homepage", ""), "artworkUrl": p.get("artworkUrl", ""),
        "episodeCount": len(eps),
        "latestEpisode": {"id": latest["id"], "title": latest["title"], "publishedAt": latest.get("publishedAt")} if latest else None,
        "lastFetchedAt": p.get("lastFetchedAt", 0),
        "needsRefresh": not p.get("lastFetchedAt"),       # never fetched (starter pick or offline subscribe)
        "source": p.get("source", "user"),
        "createdAt": p.get("createdAt", 0), "updatedAt": p.get("updatedAt", 0),
    }


def get_all_podcasts() -> List[Dict]:
    return [summarize(p) for p in load_podcasts()]


def get_podcast_by_id(podcast_id: str) -> Optional[Dict]:
    return next((p for p in load_podcasts() if p.get("id") == podcast_id), None)


def upsert_podcast(parsed: Dict) -> Tuple[Dict, bool]:
    """Store a freshly parsed feed. Returns (podcast, created)."""
    podcasts = load_podcasts()
    now = int(time.time())
    for i, existing in enumerate(podcasts):
        if existing.get("id") == parsed["id"]:
            merged = {**existing, **parsed, "createdAt": existing.get("createdAt", now), "updatedAt": now,
                      "source": existing.get("source", "user")}
            # A starter pick keeps its curated blurb if the feed has none.
            if not merged.get("description") and existing.get("description"):
                merged["description"] = existing["description"]
            podcasts[i] = merged
            save_podcasts(podcasts)
            return merged, False
    new_p = {**parsed, "createdAt": now, "updatedAt": now, "lastPlayedAt": 0, "source": parsed.get("source", "user")}
    podcasts.insert(0, new_p)
    save_podcasts(podcasts)
    return new_p, True


def mark_played(podcast_id: str) -> None:
    podcasts = load_podcasts()
    for p in podcasts:
        if p.get("id") == podcast_id:
            p["lastPlayedAt"] = int(time.time())
            save_podcasts(podcasts)
            return


def delete_podcast(podcast_id: str) -> bool:
    podcasts = load_podcasts()
    kept = [p for p in podcasts if p.get("id") != podcast_id]
    if len(kept) == len(podcasts):
        return False
    save_podcasts(kept)
    return True


def is_stale(p: Dict, now: Optional[float] = None) -> bool:
    now = time.time() if now is None else now
    return (now - float(p.get("lastFetchedAt") or 0)) > STALE_AFTER_S


async def refresh_podcast(podcast_id: str, client: Optional[httpx.AsyncClient] = None) -> Tuple[Optional[Dict], Optional[str]]:
    """Re-fetch a subscription's feed. Returns (podcast, error). On a fetch
    error the cached podcast is returned together with the error text."""
    p = get_podcast_by_id(podcast_id)
    if not p:
        return None, "Podcast not found."
    try:
        parsed = await fetch_feed(p["feedUrl"], client=client)
    except ValueError as e:
        return p, str(e)
    merged, _ = upsert_podcast(parsed)
    return merged, None


async def refresh_unfetched(client: Optional[httpx.AsyncClient] = None, concurrency: int = 4) -> Dict[str, Optional[str]]:
    """Fetch episodes for subscriptions that have never been fetched (starter
    picks, or feeds added while offline). Returns {podcast_id: error|None}.
    Used as a background task at startup; failures are reported, not raised."""
    import asyncio
    pending = [p for p in load_podcasts() if not p.get("lastFetchedAt")]
    if not pending:
        return {}
    own = client is None
    client = client or httpx.AsyncClient(timeout=15.0, follow_redirects=True, headers={"User-Agent": USER_AGENT})
    sem = asyncio.Semaphore(max(1, concurrency))
    results: Dict[str, Optional[str]] = {}

    async def one(p: Dict):
        async with sem:
            _, err = await refresh_podcast(p["id"], client=client)
            results[p["id"]] = err

    try:
        await asyncio.gather(*(one(p) for p in pending))
    finally:
        if own:
            await client.aclose()
    return results


# ─── Online directory (Apple podcast search) ──────────────────────────────────

async def search_directory(query: str, limit: int = 8, client: Optional[httpx.AsyncClient] = None) -> List[Dict]:
    q = (query or "").strip()
    if not q:
        return []
    own = client is None
    client = client or httpx.AsyncClient(timeout=8.0, follow_redirects=True, headers={"User-Agent": USER_AGENT})
    try:
        resp = await client.get(ITUNES_SEARCH_URL, params={"term": q, "media": "podcast", "entity": "podcast", "limit": str(limit)})
        if resp.status_code != 200:
            return []
        out = []
        for item in (resp.json() or {}).get("results", []):
            feed = (item.get("feedUrl") or "").strip()
            if not feed.lower().startswith("http"):
                continue
            out.append({
                "id": podcast_id_for(feed),
                "title": str(item.get("collectionName") or item.get("trackName") or "").strip()[:200],
                "author": str(item.get("artistName") or "").strip()[:120],
                "feedUrl": feed,
                "artworkUrl": str(item.get("artworkUrl600") or item.get("artworkUrl100") or "").strip(),
                "genre": str(item.get("primaryGenreName") or "").strip(),
                "episodeCount": int(item.get("trackCount") or 0),
            })
        return out
    except Exception as e:
        print(f"[Podcasts] Directory search failed: {e}")
        return []
    finally:
        if own:
            await client.aclose()


# ─── Spoken-name resolution ───────────────────────────────────────────────────

def find_podcast(query: str, podcasts: Optional[List[Dict]] = None) -> Optional[Dict]:
    if podcasts is None:
        podcasts = load_podcasts()
    return best_match(
        query, podcasts,
        fields=lambda p: [(p.get("title", ""), 1.0), (p.get("author", ""), 0.7)],
        filler=_PODCAST_FILLER,
        tiebreak=lambda p: float(p.get("lastPlayedAt") or 0),
    )


_ORDINALS = {
    "first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4,
    "fifth": 5, "5th": 5, "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
}
_LATEST_RE = re.compile(r"^\s*(?:the\s+)?(?:latest|newest|most\s+recent|last|new|current|recent)?(?:\s+(?:one|episode|show))?\s*$", re.I)
_OLDEST_RE = re.compile(r"\b(oldest|very\s+first|first\s+ever)\b", re.I)
_NUMBER_RE = re.compile(r"(?:\bepisode|\bep\.?|#|\bnumber|\bno\.?)\s*(\d{1,5})\b", re.I)
_PREVIOUS_RE = re.compile(r"\b(previous|earlier|before\s+(?:that|this)|older)\b", re.I)
_NEXT_RE = re.compile(r"^\s*(?:the\s+)?next(?:\s+(?:one|episode))?\s*$", re.I)


def resolve_episode(podcast: Dict, spoken: str, current_episode_id: Optional[str] = None) -> Tuple[Optional[Dict], Optional[str]]:
    """Map an episode reference to one of the podcast's cached episodes.

      ''/'latest'/'newest'  -> most recent
      'oldest'/'first ever' -> oldest
      'second latest', 'third one' -> ordinal from the newest
      'episode 45', '#45'  -> by episode number
      'next' / 'previous'  -> relative to `current_episode_id` (next = newer)
      anything else        -> title keyword match
    Returns (episode, error_message).
    """
    episodes: List[Dict] = podcast.get("episodes") or []
    if not episodes:
        return None, f"'{podcast.get('title')}' has no episodes cached yet. Try refreshing the feed."
    s = (spoken or "").strip()

    if _LATEST_RE.match(s):
        return episodes[0], None
    if _OLDEST_RE.search(s):
        return episodes[-1], None

    m = _NUMBER_RE.search(s)
    if m:
        n = int(m.group(1))
        ep = next((e for e in episodes if e.get("episodeNumber") == n), None)
        if ep:
            return ep, None
        ep = next((e for e in episodes if re.search(rf"\b{n}\b", e.get("title", ""))), None)
        if ep:
            return ep, None
        return None, f"No episode number {n} in '{podcast.get('title')}' (the feed lists {len(episodes)} episodes)."

    if current_episode_id and (_NEXT_RE.match(s) or _PREVIOUS_RE.search(s)):
        idx = next((i for i, e in enumerate(episodes) if e["id"] == current_episode_id), None)
        if idx is not None:
            # episodes are newest-first: "next" = newer = smaller index
            target = idx - 1 if _NEXT_RE.match(s) else idx + 1
            if 0 <= target < len(episodes):
                return episodes[target], None
            return None, ("That is already the newest episode." if target < 0 else "That is already the oldest episode.")

    words = [w for w in re.findall(r"[a-z0-9]+", s.lower())]
    ordinal = next((_ORDINALS[w] for w in words if w in _ORDINALS), None)
    if ordinal and all(w in _ORDINALS or w in {"the", "latest", "newest", "most", "recent", "one", "episode", "last", "from", "to", "back"} for w in words):
        if ordinal <= len(episodes):
            return episodes[ordinal - 1], None
        return None, f"'{podcast.get('title')}' only has {len(episodes)} episodes cached."

    ep = best_match(
        s, episodes,
        fields=lambda e: [(e.get("title", ""), 1.0), (e.get("description", "")[:300], 0.5)],
        filler=_PODCAST_FILLER | {"about", "one", "where", "with"},
        min_score=15.0,
    )
    if ep:
        return ep, None
    recent = "; ".join(f"'{e['title']}'" for e in episodes[:5])
    return None, f"No episode of '{podcast.get('title')}' matches '{spoken}'. Recent episodes: {recent}."


def latest_episodes(limit: int = 20, podcasts: Optional[List[Dict]] = None, per_podcast: int = 1) -> List[Dict]:
    """The newest episodes across every subscription (up to `per_podcast`
    from each show), newest first. Each item: {podcast: summary, episode}."""
    pods = load_podcasts() if podcasts is None else podcasts
    items = []
    for p in pods:
        for ep in (p.get("episodes") or [])[:max(1, per_podcast)]:
            items.append({"podcast": summarize(p), "episode": ep, "publishedAt": ep.get("publishedAt") or 0})
    items.sort(key=lambda i: i["publishedAt"], reverse=True)
    return [{"podcast": i["podcast"], "episode": i["episode"]} for i in items[:max(1, limit)]]


def episode_label(ep: Dict) -> str:
    bits = [f"'{ep.get('title')}'"]
    if ep.get("episodeNumber"):
        bits.append(f"(episode {ep['episodeNumber']})")
    if ep.get("duration"):
        bits.append(f"[{format_duration(ep['duration'])}]")
    if ep.get("publishedAt"):
        bits.append(f"from {time.strftime('%b %d, %Y', time.gmtime(ep['publishedAt']))}")
    return " ".join(bits)


def format_duration(seconds: Optional[int]) -> str:
    if not seconds or seconds <= 0:
        return "unknown length"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m"
    if m:
        return f"{m} min"
    return f"{s} sec"


def normalize_title(text: str) -> str:
    return normalize(text, _PODCAST_FILLER)

"""
AI DJ — Live Radio Sources

Saved internet-radio stations (JSON-backed, like playlists.py), spoken-name
matching, an optional online station directory (radio-browser.info, no API
key), and the pieces the stream proxy in main.py needs: resolving .pls/.m3u
playlist URLs to a real stream and demuxing SHOUTcast/Icecast ICY metadata
("StreamTitle") out of the audio so the DJ can say what song is on air.
"""

import re
import json
import time
import hashlib
from pathlib import Path
from urllib.parse import quote
from typing import Dict, List, Optional, Tuple

import httpx

try:
    from backend.textmatch import best_match, COMMON_FILLER
except ImportError:
    from textmatch import best_match, COMMON_FILLER

__all__ = ["COMMON_FILLER"]  # re-exported for main.py's generic-request check

STATIONS_FILE = Path(__file__).parent / "radio_stations.json"

USER_AGENT = "AI-DJ/1.0 (+https://github.com/ai-dj; local prototype)"
DIRECTORY_BASE = "https://all.api.radio-browser.info/json"
# Second directory: TuneIn's long-standing OPML endpoints (unofficial but used
# by many open-source players). Covers commercial local stations that the
# community directory lacks. Results carry provider="tunein"; their streamUrl
# is a Tune.ashx link that resolve_tunein_url() turns into a direct stream.
TUNEIN_SEARCH_URL = "https://opml.radiotime.com/Search.ashx"
TUNEIN_TUNE_URL = "https://opml.radiotime.com/Tune.ashx"
TUNEIN_HOST = "opml.radiotime.com"

# Starter picks, seeded the first time the app runs without a stations file.
# Deliberately broad — genres, talk/news and several countries — so the Radio
# tab is useful before the user saves anything. All are public, direct MP3
# streams (no HLS); the user can remove any of them. Marked source="default"
# so the GUI and the DJ can explain that they are starter picks.
DEFAULT_STATIONS = [
    # Eclectic / alternative
    {"name": "Radio Paradise", "streamUrl": "https://stream.radioparadise.com/mp3-128",
     "genre": "Eclectic Rock, Singer-Songwriter", "country": "USA", "homepage": "https://radioparadise.com",
     "description": "Listener-supported, hand-picked eclectic mix"},
    {"name": "KEXP 90.3 Seattle", "streamUrl": "https://kexp-mp3-128.streamguys1.com/kexp128.mp3",
     "genre": "Alternative, Indie, Eclectic", "country": "USA", "homepage": "https://www.kexp.org",
     "description": "Legendary independent music station"},
    {"name": "NTS Radio 1", "streamUrl": "https://stream-relay-geo.ntslive.net/stream",
     "genre": "Eclectic, Underground, Electronic", "country": "UK", "homepage": "https://www.nts.live",
     "description": "London's adventurous online station"},
    {"name": "WFMU", "streamUrl": "https://stream0.wfmu.org/freeform-128k",
     "genre": "Freeform, Experimental", "country": "USA", "homepage": "https://wfmu.org",
     "description": "Freeform radio from New Jersey"},
    {"name": "triple j", "streamUrl": "https://live-radio01.mediahubaustralia.com/2TJW/mp3/",
     "genre": "Alternative, New Music", "country": "Australia", "homepage": "https://www.abc.net.au/triplej",
     "description": "ABC's youth station"},
    {"name": "SomaFM Indie Pop Rocks", "streamUrl": "https://ice1.somafm.com/indiepop-128-mp3",
     "genre": "Indie Pop, Indie Rock", "country": "USA", "homepage": "https://somafm.com/indiepop/"},
    # Chill / electronic
    {"name": "SomaFM Groove Salad", "streamUrl": "https://ice1.somafm.com/groovesalad-128-mp3",
     "genre": "Ambient, Downtempo, Chillout", "country": "USA", "homepage": "https://somafm.com/groovesalad/"},
    {"name": "Radio Paradise Mellow Mix", "streamUrl": "https://stream.radioparadise.com/mellow-128",
     "genre": "Mellow, Chill, Acoustic", "country": "USA", "homepage": "https://radioparadise.com"},
    {"name": "SomaFM Beat Blender", "streamUrl": "https://ice1.somafm.com/beatblender-128-mp3",
     "genre": "Deep House, Downtempo, Electronic", "country": "USA", "homepage": "https://somafm.com/beatblender/"},
    {"name": "SomaFM The Trip", "streamUrl": "https://ice1.somafm.com/thetrip-128-mp3",
     "genre": "Progressive House, Trance, Dance", "country": "USA", "homepage": "https://somafm.com/thetrip/"},
    # Jazz / soul / classical
    {"name": "Jazz24", "streamUrl": "https://live.wostreaming.net/direct/ppm-jazz24mp3-ibc1",
     "genre": "Jazz", "country": "USA", "homepage": "https://www.jazz24.org"},
    {"name": "Radio Swiss Jazz", "streamUrl": "https://stream.srg-ssr.ch/m/rsj/mp3_128",
     "genre": "Jazz, Blues, Soul", "country": "Switzerland", "homepage": "https://www.radioswissjazz.ch"},
    {"name": "SomaFM Seven Inch Soul", "streamUrl": "https://ice1.somafm.com/7soul-128-mp3",
     "genre": "Soul, Funk, Vintage", "country": "USA", "homepage": "https://somafm.com/7soul/"},
    {"name": "Radio Swiss Classic", "streamUrl": "https://stream.srg-ssr.ch/m/rsc_de/mp3_128",
     "genre": "Classical", "country": "Switzerland", "homepage": "https://www.radioswissclassic.ch"},
    {"name": "Classic FM", "streamUrl": "https://media-ice.musicradio.com/ClassicFMMP3",
     "genre": "Classical, Relaxing", "country": "UK", "homepage": "https://www.classicfm.com"},
    # Rock / metal / country / 80s / pop
    {"name": "SomaFM Metal Detector", "streamUrl": "https://ice1.somafm.com/metal-128-mp3",
     "genre": "Metal, Hard Rock", "country": "USA", "homepage": "https://somafm.com/metal/"},
    {"name": "SomaFM Boot Liquor", "streamUrl": "https://ice1.somafm.com/bootliquor-128-mp3",
     "genre": "Americana, Country, Roots", "country": "USA", "homepage": "https://somafm.com/bootliquor/"},
    {"name": "SomaFM Underground 80s", "streamUrl": "https://ice1.somafm.com/u80s-128-mp3",
     "genre": "80s, Synthpop, New Wave", "country": "USA", "homepage": "https://somafm.com/u80s/"},
    {"name": "Radio Swiss Pop", "streamUrl": "https://stream.srg-ssr.ch/m/rsp/mp3_128",
     "genre": "Pop, Hits", "country": "Switzerland", "homepage": "https://www.radioswisspop.ch"},
    # World / news / talk
    {"name": "FIP", "streamUrl": "https://icecast.radiofrance.fr/fip-midfi.mp3",
     "genre": "Eclectic, Jazz, World, French", "country": "France", "homepage": "https://www.radiofrance.fr/fip",
     "description": "Radio France's famously eclectic music station"},
    {"name": "NPR Program Stream", "streamUrl": "https://npr-ice.streamguys1.com/live.mp3",
     "genre": "News, Talk, Public Radio", "country": "USA", "homepage": "https://www.npr.org"},
    {"name": "Deutschlandfunk", "streamUrl": "https://st01.sslstream.dlf.de/dlf/01/128/mp3/stream.mp3",
     "genre": "News, Talk, Culture (German)", "country": "Germany", "homepage": "https://www.deutschlandfunk.de"},
]

# Genre shortcuts offered by the GUI's "Find stations" browser. Each maps to a
# radio-browser.info tag; the label is what the user sees.
BROWSE_GENRES = [
    ("Pop", "pop"), ("Rock", "rock"), ("Jazz", "jazz"), ("Classical", "classical"),
    ("Electronic", "electronic"), ("Dance", "dance"), ("Hip-Hop", "hip hop"), ("R&B / Soul", "soul"),
    ("Country", "country"), ("Blues", "blues"), ("Folk", "folk"), ("Metal", "metal"),
    ("Latin", "latin"), ("Reggae", "reggae"), ("Lo-fi / Chill", "chillout"), ("Ambient", "ambient"),
    ("Oldies", "oldies"), ("80s", "80s"), ("News", "news"), ("Talk", "talk"), ("Sports", "sports"),
]

# Spoken filler specific to radio requests ("play the jazz station on the radio")
_RADIO_FILLER = {"station", "stations", "radio", "channel", "stream", "fm", "am", "live", "internet"}

# Content types that are playlists pointing at the real stream, not audio.
PLAYLIST_CONTENT_TYPES = {
    "audio/x-scpls", "audio/x-mpegurl", "audio/mpegurl", "application/x-mpegurl",
    "application/pls+xml",
}
# HLS (.m3u8) cannot be played by a bare <audio> element in Chrome/Edge.
HLS_CONTENT_TYPES = {"application/vnd.apple.mpegurl"}


def station_id_for(stream_url: str) -> str:
    """Content-addressable ID so a station saved twice (GUI + voice) dedupes."""
    return "rs_" + hashlib.md5((stream_url or "").strip().encode("utf-8")).hexdigest()[:10]


# ─── Persistence ──────────────────────────────────────────────────────────────

def _strip(v, limit: int = 200) -> str:
    return str(v or "").strip()[:limit]


def _normalise_station(raw: Dict, source: str = "user") -> Optional[Dict]:
    url = _strip(raw.get("streamUrl") or raw.get("url"), 2000)
    if not re.match(r"^https?://", url, re.IGNORECASE):
        return None
    now = int(time.time())
    return {
        "id": station_id_for(url),
        "name": _strip(raw.get("name"), 120) or url,
        "streamUrl": url,
        "genre": _strip(raw.get("genre") or raw.get("tags"), 160),
        "country": _strip(raw.get("country"), 80),
        "homepage": _strip(raw.get("homepage"), 500),
        "favicon": _strip(raw.get("favicon"), 500),
        "description": _strip(raw.get("description"), 400),
        "source": raw.get("source") or source,
        "provider": _strip(raw.get("provider"), 20) or ("tunein" if TUNEIN_HOST in url else "radio-browser" if raw.get("votes") is not None else "user"),
        "resolvedUrl": _strip(raw.get("resolvedUrl"), 2000),
        "createdAt": int(raw.get("createdAt") or now),
        "updatedAt": int(raw.get("updatedAt") or now),
        "lastPlayedAt": int(raw.get("lastPlayedAt") or 0),
    }


def load_stations() -> List[Dict]:
    """Saved stations. Seeds the defaults when no file exists yet (a user who
    deletes every station keeps an empty file and does not get them back)."""
    if not STATIONS_FILE.exists():
        seeded = [s for s in (_normalise_station(d, source="default") for d in DEFAULT_STATIONS) if s]
        save_stations(seeded)
        return seeded
    try:
        with open(STATIONS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return [s for s in data if isinstance(s, dict) and s.get("streamUrl")]
    except Exception as e:
        print(f"[Radio] Error loading stations: {e}")
    return []


def save_stations(stations: List[Dict]) -> bool:
    try:
        tmp = STATIONS_FILE.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(stations, f, indent=2, ensure_ascii=False)
        tmp.replace(STATIONS_FILE)
        return True
    except Exception as e:
        print(f"[Radio] Error saving stations: {e}")
        return False


def get_all_stations() -> List[Dict]:
    return load_stations()


def get_station_by_id(station_id: str) -> Optional[Dict]:
    return next((s for s in load_stations() if s.get("id") == station_id), None)


def add_station(raw: Dict, source: str = "user") -> Tuple[Optional[Dict], bool]:
    """Add (or refresh) a station. Returns (station, created). A station with
    the same stream URL is updated in place rather than duplicated."""
    st = _normalise_station(raw, source)
    if not st:
        return None, False
    stations = load_stations()
    for i, existing in enumerate(stations):
        if existing.get("id") == st["id"]:
            merged = dict(existing)
            for k in ("name", "genre", "country", "homepage", "favicon", "description", "resolvedUrl", "provider"):
                if st.get(k):
                    merged[k] = st[k]
            merged["updatedAt"] = int(time.time())
            stations[i] = merged
            save_stations(stations)
            return merged, False
    stations.insert(0, st)
    save_stations(stations)
    return st, True


def update_station(station_id: str, **fields) -> Optional[Dict]:
    stations = load_stations()
    for st in stations:
        if st.get("id") == station_id:
            for k in ("name", "genre", "country", "homepage", "favicon", "description", "resolvedUrl"):
                if fields.get(k) is not None:
                    st[k] = _strip(fields[k], 500)
            if not st.get("name"):
                st["name"] = st["streamUrl"]
            st["updatedAt"] = int(time.time())
            save_stations(stations)
            return st
    return None


def mark_played(station_id: str) -> None:
    stations = load_stations()
    for st in stations:
        if st.get("id") == station_id:
            st["lastPlayedAt"] = int(time.time())
            save_stations(stations)
            return


def delete_station(station_id: str) -> bool:
    stations = load_stations()
    kept = [s for s in stations if s.get("id") != station_id]
    if len(kept) == len(stations):
        return False
    save_stations(kept)
    return True


# ─── Spoken-name lookup ───────────────────────────────────────────────────────

def find_station(query: str, stations: Optional[List[Dict]] = None) -> Optional[Dict]:
    """Best saved station for a spoken request: name beats genre beats country.
    "groove salad" -> SomaFM Groove Salad; "some ambient" -> a station whose
    genre mentions ambient. Ties prefer the most recently played station."""
    if stations is None:
        stations = load_stations()
    # min_score 30: a single shared genre word ("tsf jazz" vs "Jazz, World")
    # must NOT pre-empt the online directory; name/genre containment still does.
    return best_match(
        query, stations,
        fields=lambda s: [(s.get("name", ""), 1.0), (s.get("genre", ""), 0.8),
                          (s.get("description", ""), 0.5), (s.get("country", ""), 0.4)],
        filler=_RADIO_FILLER,
        min_score=30.0,
        tiebreak=lambda s: float(s.get("lastPlayedAt") or 0),
    )


# ─── Online directory (radio-browser.info) ────────────────────────────────────

def _directory_entry(item: Dict) -> Optional[Dict]:
    url = _strip(item.get("url_resolved") or item.get("url"), 2000)
    if not url.lower().startswith("http"):
        return None
    tags = _strip(item.get("tags"), 160).replace(",", ", ")
    return {
        "id": station_id_for(url),
        "name": _strip(item.get("name"), 120) or url,
        "streamUrl": url,
        "genre": tags,
        "country": _strip(item.get("country"), 80),
        "homepage": _strip(item.get("homepage"), 500),
        "favicon": _strip(item.get("favicon"), 500),
        "description": ", ".join(x for x in (_strip(item.get("codec")), f"{item.get('bitrate')} kbps" if item.get("bitrate") else "") if x),
        "codec": _strip(item.get("codec"), 20),
        "bitrate": int(item.get("bitrate") or 0),
        "votes": int(item.get("votes") or 0),
        # radio-browser's checkers run in Europe; geo-blocked local stations
        # (e.g. Canadian commercial radio) are often flagged "broken" there yet
        # play fine for the user. We never hide them — just rank verified first.
        "online": bool(int(item.get("lastcheckok", 1) or 0)),
        "source": "directory",
    }


async def browse_directory(kind: str, value: str = "", limit: int = 20,
                           client: Optional[httpx.AsyncClient] = None) -> List[Dict]:
    """Browse radio-browser.info without a free-text query.

      kind="popular"          -> most-voted stations worldwide
      kind="tag",     value   -> a genre/tag (see BROWSE_GENRES)
      kind="country", value   -> stations from one country
    Returns [] when offline/unavailable (never raises)."""
    kind = (kind or "").strip().lower()
    value = (value or "").strip()
    params = {"limit": str(max(limit * 2, 10)), "hidebroken": "true", "order": "votes", "reverse": "true"}
    if kind == "popular":
        path = f"{DIRECTORY_BASE}/stations/topvote/{max(limit * 2, 10)}"
        params = {"hidebroken": "true"}
    elif kind == "tag" and value:
        path = f"{DIRECTORY_BASE}/stations/bytagexact/{quote(value.lower(), safe='')}"
    elif kind == "country" and value:
        path = f"{DIRECTORY_BASE}/stations/bycountry/{quote(value, safe='')}"
    else:
        return []

    own_client = client is None
    client = client or httpx.AsyncClient(timeout=6.0, follow_redirects=True, headers={"User-Agent": USER_AGENT})
    results: Dict[str, Dict] = {}
    try:
        resp = await client.get(path, params=params)
        if resp.status_code != 200:
            return []
        for item in resp.json() or []:
            entry = _directory_entry(item)
            if not entry:
                continue
            if "m3u8" in entry["streamUrl"].lower() or (entry.get("codec") or "").upper() == "HLS":
                continue
            results.setdefault(entry["id"], entry)
    except Exception as e:
        print(f"[Radio] Directory browse failed: {e}")
        return []
    finally:
        if own_client:
            await client.aclose()
    ranked = sorted(results.values(), key=lambda s: s.get("votes", 0), reverse=True)
    return ranked[:limit]


# Words that do not identify a station on their own ("the jazz station").
_SEARCH_NOISE = {"the", "a", "fm", "am", "radio", "station", "stations", "live", "hd", "stream", "online"}


def _compact(text: str) -> str:
    """Spacing/punctuation-insensitive form for matching station names:
    'Jack 92.9' -> 'jack929', '96.5 The Breeze' -> '965thebreeze'."""
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def search_tokens(query: str) -> List[str]:
    """Meaningful tokens of a spoken/typed station query. Letter/digit
    boundaries are split ('Move100' -> 'move', '100'), frequencies are kept
    whole ('92.9'), noise words dropped."""
    q = (query or "").lower()
    q = re.sub(r"(?<=[a-z])(?=\d)|(?<=\d)(?=[a-z])", " ", q)
    tokens = re.findall(r"\d+(?:[.,]\d+)?|[a-z]+", q)
    return [t for t in tokens if t not in _SEARCH_NOISE]


def query_variants(query: str) -> List[str]:
    """Spellings to try as a directory name search: as typed, with a space
    between letters and digits ('Move100' -> 'Move 100') and without
    ('Q 104' -> 'Q104')."""
    q = re.sub(r"\s+", " ", (query or "").strip())
    if not q:
        return []
    out = [q]
    spaced = re.sub(r"(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])", " ", q)
    joined = re.sub(r"(?<=[A-Za-z]) (?=\d)|(?<=\d) (?=[A-Za-z])", "", q)
    for v in (spaced, joined):
        if v and v.lower() not in {o.lower() for o in out}:
            out.append(v)
    return out


def _popularity(st: Dict):
    """Sort key: verified-online first, then votes."""
    return (1 if st.get("online", True) else 0, st.get("votes", 0))


def _token_in_name(raw: str, compact: str, name_c: str, name_raw: str) -> bool:
    """Compact containment, plus frequency tolerance: '100.1' also matches a
    listing that only says 'Move 100' (and '100' matches 'Move 100.1' via
    plain containment)."""
    if compact in name_c:
        return True
    m = re.fullmatch(r"(\d{2,3})[.,]\d", raw)
    return bool(m and re.search(rf"(?<!\d){m.group(1)}(?!\d)", name_raw))


def rank_by_query(query: str, stations: List[Dict]) -> List[Dict]:
    """Keep stations whose name contains every query token (compared in the
    compact form, so 'Jack 92.9' / 'JACK 929' / 'Jack-92-9' all match, and
    frequencies tolerate a missing decimal), verified-online and popular
    first. Single-token queries only need that token."""
    raw_tokens = search_tokens(query)
    tokens = [(t, _compact(t)) for t in raw_tokens if _compact(t)]
    if not tokens:
        return sorted(stations, key=_popularity, reverse=True)
    scored = []
    for st in stations:
        name_raw = (st.get("name") or "").lower()
        name_c = _compact(name_raw)
        hits = sum(1 for raw, c in tokens if _token_in_name(raw, c, name_c, name_raw))
        if hits == len(tokens):
            scored.append((hits, _popularity(st), st))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [st for _, _, st in scored]


async def search_directory(query: str, country: str = "", limit: int = 10,
                           client: Optional[httpx.AsyncClient] = None, place: str = "") -> List[Dict]:
    """Search radio-browser.info for a station.

    1. name search for the phrase as typed plus spacing variants ('Move100' /
       'Move 100'), then a tag (genre) search; results merge by stream URL.
    2. If nothing matched, fall back to searching each distinctive word
       ('jack', 'breeze', …) and keep only stations whose name contains all
       the query's words in any order/spacing ('96.5 the breeze' finds
       'The Breeze 96.5'). This is what makes small local commercial stations
       findable by their on-air brand.
    Returns [] on any network problem — callers treat that as 'directory
    unavailable', never as an error for the user."""
    q = (query or "").strip()
    place = (place or "").strip()
    if not q and not country and not place:
        return []
    # hidebroken is deliberately OFF for searches (see `online` in _directory_entry).
    params_base = {"limit": str(max(limit * 2, 10)), "hidebroken": "false", "order": "votes", "reverse": "true"}
    if country:
        params_base["country"] = country.strip()

    own_client = client is None
    client = client or httpx.AsyncClient(timeout=6.0, follow_redirects=True, headers={"User-Agent": USER_AGENT})
    results: Dict[str, Dict] = {}

    async def run(params: Dict[str, str]) -> bool:
        try:
            resp = await client.get(f"{DIRECTORY_BASE}/stations/search", params=params)
        except Exception as e:
            print(f"[Radio] Directory search failed: {e}")
            return False
        if resp.status_code != 200:
            return True
        for item in resp.json() or []:
            entry = _directory_entry(item)
            if not entry:
                continue
            # Only direct streams are playable through the proxy (no HLS).
            if "m3u8" in entry["streamUrl"].lower() or (entry.get("codec") or "").upper() == "HLS":
                continue
            results.setdefault(entry["id"], entry)
        return True

    def _in_place(st: Dict) -> bool:
        """Does a station belong to `place`? Name/tags/state mention it (the
        directory has no city field, so this is how 'Halifax' is expressed)."""
        if not place:
            return True
        pc = _compact(place)
        return pc in _compact(st.get("name", "")) or pc in _compact(st.get("genre", "")) or pc in _compact(st.get("state", ""))

    try:
        if q:
            for variant in query_variants(q):
                if not await run({**params_base, "name": variant}):
                    return []
            await run({**params_base, "tag": q.lower()})
        if place:
            # City/region: stations whose name, tags or state mention it.
            await run({**params_base, "name": place, "limit": "100"})
            await run({**params_base, "tag": place.lower(), "limit": "100"})
            await run({**params_base, "state": place, "limit": "100"})
        if not q and not place:
            await run(params_base)

        candidates = [s for s in results.values() if _in_place(s)]
        if q and place:
            candidates = rank_by_query(q, candidates) or [s for s in candidates if _compact(q) in _compact(s.get("name", ""))]
        ranked = sorted(candidates, key=_popularity, reverse=True) if not (q and place) else candidates
        if ranked or not q:
            return ranked[:limit]

        # Fallback: word-by-word search, then require every word in the name.
        tokens = search_tokens(q)
        words = sorted((t for t in tokens if re.fullmatch(r"[a-z]+", t) and len(t) >= 3), key=len, reverse=True)
        if not words:
            words = [t for t in tokens if len(t) >= 3]
        for word in words[:3]:
            await run({**params_base, "name": word, "limit": "100"})
        return rank_by_query(q, [s for s in results.values() if _in_place(s)])[:limit]
    finally:
        if own_client:
            await client.aclose()


# ─── Second directory: TuneIn (OPML) ──────────────────────────────────────────

def _walk_tunein(items) -> List[Dict]:
    out: List[Dict] = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        if it.get("children"):
            out.extend(_walk_tunein(it["children"]))
        elif it.get("type") == "audio" and it.get("item", "station") == "station" and it.get("URL"):
            out.append(it)
    return out


def _tunein_entry(item: Dict) -> Optional[Dict]:
    guide = _strip(item.get("guide_id"), 30)
    url = _strip(item.get("URL"), 500)
    if not guide or not url.lower().startswith("http"):
        return None
    # Stable, playable-through-resolution stream URL: the Tune.ashx link itself.
    tune_url = f"{TUNEIN_TUNE_URL}?id={guide}&formats=mp3,aac,ogg"
    text = _strip(item.get("text"), 160)
    # "Jack 92.9 (Adult Hits)" -> name "Jack 92.9", genre "Adult Hits"
    m = re.match(r"^(.*?)\s*\(([^()]*)\)\s*$", text)
    name, genre = (m.group(1).strip(), m.group(2).strip()) if m else (text, "")
    formats = _strip(item.get("formats"), 40)
    bitrate = int(str(item.get("bitrate") or "0").split(",")[0] or 0) if str(item.get("bitrate") or "").split(",")[0].isdigit() else 0
    return {
        "id": station_id_for(tune_url),
        "name": name or text or guide,
        "streamUrl": tune_url,
        "genre": genre,
        "country": "",
        "homepage": f"https://tunein.com/radio/{guide}/",
        "favicon": _strip(item.get("image"), 500),
        "description": ", ".join(x for x in (formats.upper().replace(",", ", ") if formats else "", f"{bitrate} kbps" if bitrate else "") if x),
        "codec": formats.split(",")[0].upper() if formats else "",
        "bitrate": bitrate,
        "votes": int(float(item.get("reliability") or 0)),
        "online": True,
        "provider": "tunein",
        "nowPlaying": _strip(item.get("subtext"), 160),
        "source": "directory",
    }


async def search_tunein(query: str, limit: int = 10, client: Optional[httpx.AsyncClient] = None) -> List[Dict]:
    """Search TuneIn's directory. Returns [] when unavailable (never raises)."""
    q = (query or "").strip()
    if not q:
        return []
    own = client is None
    client = client or httpx.AsyncClient(timeout=6.0, follow_redirects=True, headers={"User-Agent": USER_AGENT})
    try:
        resp = await client.get(TUNEIN_SEARCH_URL, params={"query": q, "render": "json", "formats": "mp3,aac,ogg"})
        if resp.status_code != 200:
            return []
        data = resp.json() or {}
        items = _walk_tunein(data.get("body") or [])
        entries = [e for e in (_tunein_entry(i) for i in items) if e]
        # Keep stations whose name matches the query's words (TuneIn also
        # returns loosely related shows/stations).
        ranked = rank_by_query(q, entries) or entries
        return ranked[:limit]
    except Exception as e:
        print(f"[Radio] TuneIn search failed: {e}")
        return []
    finally:
        if own:
            await client.aclose()


def is_tunein_url(url: str) -> bool:
    return TUNEIN_HOST in (url or "").lower()


async def resolve_tunein_url(tune_url: str, client: Optional[httpx.AsyncClient] = None) -> Optional[str]:
    """Turn a Tune.ashx link into a direct (non-HLS) stream URL, or None."""
    if not is_tunein_url(tune_url):
        return tune_url
    own = client is None
    client = client or httpx.AsyncClient(timeout=8.0, follow_redirects=True, headers={"User-Agent": USER_AGENT})
    try:
        sep = "&" if "?" in tune_url else "?"
        resp = await client.get(f"{tune_url}{sep}render=json")
        if resp.status_code != 200:
            return None
        body = (resp.json() or {}).get("body") or []
        candidates = []
        for it in body:
            url = _strip((it or {}).get("url"), 2000)
            if not url.lower().startswith("http"):
                continue
            media = str((it or {}).get("media_type") or "").lower()
            hls = str((it or {}).get("is_hls_advanced") or "false").lower() == "true" or media == "hls" or ".m3u8" in url.lower()
            if hls:
                continue
            candidates.append((1 if (it or {}).get("is_direct") else 0, int(float((it or {}).get("reliability") or 0)), url))
        if not candidates:
            return None
        candidates.sort(reverse=True)
        return candidates[0][2]
    except Exception as e:
        print(f"[Radio] TuneIn resolve failed: {e}")
        return None
    finally:
        if own:
            await client.aclose()


def merge_directory_results(primary: List[Dict], secondary: List[Dict], limit: int) -> List[Dict]:
    """Union of two providers' results, de-duplicated by compact station name
    (the same station has different stream URLs in each directory)."""
    out: List[Dict] = []
    seen = set()
    for st in list(primary) + list(secondary):
        key = _compact(st.get("name", "")) or st.get("id")
        if key in seen:
            continue
        seen.add(key)
        out.append(st)
    return out[:limit]


# ─── Stream helpers used by the proxy ─────────────────────────────────────────

def extract_stream_urls(playlist_text: str) -> List[str]:
    """URLs from a .pls ("File1=http://…") or .m3u (one URL per line) body."""
    urls: List[str] = []
    for line in (playlist_text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^(?:File\d+\s*=\s*)?(https?://\S+)$", line, re.IGNORECASE)
        if m and m.group(1) not in urls:
            urls.append(m.group(1))
    return urls


def is_playlist_response(content_type: str, url: str) -> bool:
    ct = (content_type or "").split(";")[0].strip().lower()
    path = url.split("?")[0].lower()
    if path.endswith(".m3u8") or ct in HLS_CONTENT_TYPES:
        return False          # HLS: handled as unsupported, not as a playlist
    return ct in PLAYLIST_CONTENT_TYPES or path.endswith((".pls", ".m3u"))


def is_hls(content_type: str, url: str) -> bool:
    ct = (content_type or "").split(";")[0].strip().lower()
    return url.split("?")[0].lower().endswith(".m3u8") or ct in HLS_CONTENT_TYPES


class IcyMetadataParser:
    """Splits an ICY stream (`icy-metaint: N`) back into pure audio.

    After every N audio bytes the server inserts one length byte L followed by
    L*16 bytes of metadata such as ``StreamTitle='Artist - Song';``. feed()
    returns only the audio bytes and keeps the latest title in `.title`.
    """

    _TITLE_RE = re.compile(r"StreamTitle='(.*?)';")

    def __init__(self, metaint: int):
        if metaint <= 0:
            raise ValueError("metaint must be positive")
        self.metaint = metaint
        self.title: Optional[str] = None
        self.title_changed = False
        self._audio_left = metaint
        self._meta_left = -1          # -1: expecting the length byte; 0: reading audio; >0: metadata bytes to go
        self._reading_meta = False
        self._meta_buf = bytearray()

    def feed(self, chunk: bytes) -> bytes:
        out = bytearray()
        i, n = 0, len(chunk)
        while i < n:
            if not self._reading_meta:
                take = min(self._audio_left, n - i)
                out += chunk[i:i + take]
                i += take
                self._audio_left -= take
                if self._audio_left == 0:
                    self._reading_meta = True
                    self._meta_left = -1
            elif self._meta_left == -1:
                length = chunk[i] * 16
                i += 1
                if length == 0:
                    self._reading_meta = False
                    self._audio_left = self.metaint
                else:
                    self._meta_left = length
                    self._meta_buf = bytearray()
            else:
                take = min(self._meta_left, n - i)
                self._meta_buf += chunk[i:i + take]
                i += take
                self._meta_left -= take
                if self._meta_left == 0:
                    self._parse(bytes(self._meta_buf))
                    self._reading_meta = False
                    self._audio_left = self.metaint
        return bytes(out)

    def _parse(self, raw: bytes) -> None:
        text = raw.rstrip(b"\x00").decode("utf-8", errors="replace")
        m = self._TITLE_RE.search(text)
        if not m:
            return
        title = m.group(1).strip()
        if title != self.title:
            self.title = title
            self.title_changed = True

    def take_title_change(self) -> Optional[str]:
        """The new title if it changed since the last call, else None."""
        if self.title_changed:
            self.title_changed = False
            return self.title
        return None

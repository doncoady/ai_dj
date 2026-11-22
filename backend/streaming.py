"""
AI DJ — Online streaming sources (the "Streaming" tab)

One search, several providers, each its own sub-section in the GUI:

  apple    Apple Music catalogue via the keyless iTunes Search API —
           30-second preview clips.
  deezer   Deezer's keyless public API — 30-second preview clips.
  ytmusic  YouTube Music, full-length audio, through two third-party tools:
           `ytmusicapi` (unofficial YouTube Music search, no account) and
           `yt-dlp` (resolves the audio stream). Both are plain pip packages;
           the section reports "not installed" until they are present.

Every track is relayed by the backend (/api/streaming/tracks/{id}/audio) so
it runs through the deck's Web Audio graph like a local file: ducking, the
visualiser, ▶/⏸, seeking and the queue all behave the same.

Track shape (provider-agnostic):
  {id, provider, kind: "stream", title, artist, album, albumArt, durationMs,
   explicit, url, full: bool, audioUrl: str|None}
`audioUrl` is the clip for preview providers; for YouTube Music it is
resolved lazily (yt-dlp) when the track is first played and cached.
"""

import asyncio
import time
import importlib
import importlib.util
from typing import Awaitable, Callable, Dict, List, Optional, Tuple

import httpx

ITUNES_SEARCH_URL = "https://itunes.apple.com/search"
ITUNES_LOOKUP_URL = "https://itunes.apple.com/lookup"
DEEZER_SEARCH_URL = "https://api.deezer.com/search"
DEEZER_ALBUM_URL = "https://api.deezer.com/album"
USER_AGENT = "AI-DJ/1.0 (+https://github.com/ai-dj; local prototype)"
PREVIEW_SECONDS = 30
# yt-dlp stream URLs stay valid for a few hours; re-resolve after this.
YT_STREAM_TTL_S = 4 * 3600

PROVIDERS: Dict[str, Dict] = {
    "apple": {
        "id": "apple", "name": "Apple Music", "icon": "🍎", "full": False,
        "description": "30-second previews from the Apple Music catalogue (iTunes Search, no account).",
    },
    "deezer": {
        "id": "deezer", "name": "Deezer", "icon": "🎵", "full": False,
        "description": "30-second previews from Deezer's public catalogue (no account).",
    },
    "ytmusic": {
        "id": "ytmusic", "name": "YouTube Music", "icon": "▶️", "full": True,
        "description": "Full-length songs through the third-party tools ytmusicapi (search) and yt-dlp (audio).",
        "requires": ["ytmusicapi", "yt_dlp"],
    },
}
PROVIDER_ORDER = ["ytmusic", "apple", "deezer"]

# Tracks/albums seen in searches, by our id ("ap_…", "dz_…", "yt_…", albums "apa_…"/"dza_…"),
# so the audio relay can find them without another search.
track_cache: Dict[str, Dict] = {}
# yt_<videoId> -> {"url": str, "mime": str, "resolvedAt": ts}
_yt_streams: Dict[str, Dict] = {}


class StreamingError(Exception):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


def remember(items: List[Dict]) -> None:
    for t in items:
        if t.get("id"):
            track_cache[t["id"]] = t


def _client(client: Optional[httpx.AsyncClient]) -> Tuple[httpx.AsyncClient, bool]:
    if client is not None:
        return client, False
    return httpx.AsyncClient(timeout=8.0, follow_redirects=True, headers={"User-Agent": USER_AGENT}), True


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def provider_status(pid: str) -> Dict:
    p = PROVIDERS[pid]
    missing = [m for m in p.get("requires", []) if not _module_available(m)]
    st = {k: p[k] for k in ("id", "name", "icon", "full", "description")}
    st["available"] = not missing
    st["missing"] = missing
    if missing:
        pkgs = " ".join("yt-dlp" if m == "yt_dlp" else m for m in missing)
        st["reason"] = f"Not installed — run: pip install {pkgs}"
    return st


def status() -> Dict:
    return {
        "providers": [provider_status(pid) for pid in PROVIDER_ORDER],
        "playback": f"Apple Music and Deezer offer {PREVIEW_SECONDS}-second previews; YouTube Music plays whole songs. Everything runs through the deck.",
        "cached": len(track_cache),
    }


def available_providers() -> List[str]:
    return [pid for pid in PROVIDER_ORDER if provider_status(pid)["available"]]


# ─── Normalisers (pure) ───────────────────────────────────────────────────────

def _track(pid: str, tid: str, **fields) -> Dict:
    base = {
        "id": f"{ {'apple': 'ap', 'deezer': 'dz', 'ytmusic': 'yt'}[pid] }_{tid}", "provider": pid, "kind": "stream",
        "title": "", "artist": "Unknown Artist", "album": "", "albumArt": "", "durationMs": 0, "explicit": False, "url": "",
        "full": PROVIDERS[pid]["full"], "audioUrl": None,
    }
    base.update({k: v for k, v in fields.items() if v is not None})
    return base


def apple_track(item: Dict) -> Optional[Dict]:
    tid = item.get("trackId")
    if not tid or item.get("wrapperType", "track") != "track":
        return None
    art = str(item.get("artworkUrl100") or "")
    return _track(
        "apple", str(tid),
        title=str(item.get("trackName") or "")[:200], artist=str(item.get("artistName") or "Unknown Artist")[:200],
        album=str(item.get("collectionName") or "")[:200], albumArt=art.replace("100x100bb", "300x300bb") if art else "",
        durationMs=int(item.get("trackTimeMillis") or 0), explicit=item.get("trackExplicitness") == "explicit",
        url=str(item.get("trackViewUrl") or ""), audioUrl=item.get("previewUrl") or None,
    )


def apple_album(item: Dict) -> Optional[Dict]:
    cid = item.get("collectionId")
    if not cid:
        return None
    art = str(item.get("artworkUrl100") or "")
    return {
        "id": f"apa_{cid}", "provider": "apple", "type": "album", "name": str(item.get("collectionName") or "")[:200],
        "artist": str(item.get("artistName") or "")[:200], "image": art.replace("100x100bb", "300x300bb") if art else "",
        "trackCount": int(item.get("trackCount") or 0), "releaseDate": str(item.get("releaseDate") or "")[:10],
        "url": str(item.get("collectionViewUrl") or ""),
    }


def deezer_track(item: Dict, album: Optional[Dict] = None) -> Optional[Dict]:
    tid = item.get("id")
    if not tid:
        return None
    alb = item.get("album") or album or {}
    return _track(
        "deezer", str(tid),
        title=str(item.get("title") or "")[:200], artist=str((item.get("artist") or {}).get("name") or "Unknown Artist")[:200],
        album=str(alb.get("title") or "")[:200], albumArt=str(alb.get("cover_medium") or ""),
        durationMs=int(item.get("duration") or 0) * 1000, explicit=bool(item.get("explicit_lyrics")),
        url=str(item.get("link") or ""), audioUrl=item.get("preview") or None,
    )


def deezer_album(item: Dict) -> Optional[Dict]:
    aid = item.get("id")
    if not aid:
        return None
    return {
        "id": f"dza_{aid}", "provider": "deezer", "type": "album", "name": str(item.get("title") or "")[:200],
        "artist": str((item.get("artist") or {}).get("name") or "")[:200], "image": str(item.get("cover_medium") or ""),
        "trackCount": int(item.get("nb_tracks") or 0), "releaseDate": str(item.get("release_date") or "")[:10],
        "url": str(item.get("link") or ""),
    }


def _yt_duration_ms(item: Dict) -> int:
    if item.get("duration_seconds"):
        return int(item["duration_seconds"]) * 1000
    d = str(item.get("duration") or "")
    if d and all(p.isdigit() for p in d.split(":")):
        total = 0
        for p in d.split(":"):
            total = total * 60 + int(p)
        return total * 1000
    if item.get("duration") and isinstance(item["duration"], (int, float)):
        return int(item["duration"]) * 1000
    return 0


def ytmusic_track(item: Dict) -> Optional[Dict]:
    """ytmusicapi search result (filter='songs') -> track."""
    vid = item.get("videoId") or item.get("id")
    if not vid:
        return None
    artists = [a.get("name", "") for a in item.get("artists") or [] if isinstance(a, dict) and a.get("name")]
    if not artists and item.get("uploader"):
        artists = [str(item["uploader"])]
    thumbs = item.get("thumbnails") or []
    art = ""
    if thumbs:
        best = max((t for t in thumbs if isinstance(t, dict) and t.get("url")), key=lambda t: t.get("width") or 0, default=None)
        art = str(best["url"]) if best else ""
    album = item.get("album")
    return _track(
        "ytmusic", str(vid),
        title=str(item.get("title") or "")[:200], artist=", ".join(artists)[:200] or "Unknown Artist",
        album=str((album or {}).get("name") if isinstance(album, dict) else (album or ""))[:200], albumArt=art,
        durationMs=_yt_duration_ms(item), explicit=bool(item.get("isExplicit")),
        url=f"https://music.youtube.com/watch?v={vid}", audioUrl=None,
    )


# ─── Provider search ──────────────────────────────────────────────────────────

async def _apple_search(query: str, client: httpx.AsyncClient, limit: int, artist_only: bool, offset: int = 0) -> Tuple[List[Dict], List[Dict]]:
    params = {"term": query, "media": "music", "entity": "song", "limit": str(limit)}
    if offset:
        params["offset"] = str(offset)
    if artist_only:
        params["attribute"] = "artistTerm"
    tracks: List[Dict] = []
    albums: List[Dict] = []
    resp = await client.get(ITUNES_SEARCH_URL, params=params)
    if resp.status_code == 200:
        tracks = [t for t in (apple_track(i) for i in (resp.json() or {}).get("results", [])) if t]
    if not artist_only and not offset:
        resp = await client.get(ITUNES_SEARCH_URL, params={"term": query, "media": "music", "entity": "album", "limit": "5"})
        if resp.status_code == 200:
            albums = [a for a in (apple_album(i) for i in (resp.json() or {}).get("results", [])) if a]
    return tracks, albums


async def _deezer_search(query: str, client: httpx.AsyncClient, limit: int, artist_only: bool, offset: int = 0) -> Tuple[List[Dict], List[Dict]]:
    q = f'artist:"{query}"' if artist_only else query
    tracks: List[Dict] = []
    albums: List[Dict] = []
    params = {"q": q, "limit": str(limit)}
    if offset:
        params["index"] = str(offset)
    resp = await client.get(DEEZER_SEARCH_URL, params=params)
    if resp.status_code == 200:
        tracks = [t for t in (deezer_track(i) for i in (resp.json() or {}).get("data", [])) if t]
    if not artist_only and not offset:
        resp = await client.get(f"{DEEZER_SEARCH_URL}/album", params={"q": query, "limit": "5"})
        if resp.status_code == 200:
            albums = [a for a in (deezer_album(i) for i in (resp.json() or {}).get("data", [])) if a]
    return tracks, albums


def _ytmusic_search_sync(query: str, limit: int) -> List[Dict]:
    """Blocking: ytmusicapi song search (no account needed)."""
    from ytmusicapi import YTMusic  # type: ignore
    yt = YTMusic()
    return yt.search(query, filter="songs", limit=limit) or []


# Indirection so tests can stub the third-party calls.
ytmusic_search_sync: Callable[[str, int], List[Dict]] = _ytmusic_search_sync


async def _ytmusic_search(query: str, limit: int, artist_only: bool, offset: int = 0) -> Tuple[List[Dict], List[Dict]]:
    if not provider_status("ytmusic")["available"]:
        return [], []
    loop = asyncio.get_running_loop()
    # ytmusicapi has no offset: ask for a longer list and skip what was already shown.
    raw = await loop.run_in_executor(None, ytmusic_search_sync, query, offset + limit)
    tracks = [t for t in (ytmusic_track(i) for i in raw) if t][offset:offset + limit]
    if artist_only:
        q = query.lower()
        tracks = [t for t in tracks if q in t["artist"].lower()] or tracks
    return tracks, []


_SEARCHERS: Dict[str, Callable[..., Awaitable[Tuple[List[Dict], List[Dict]]]]] = {}


async def search_provider(pid: str, query: str, client: Optional[httpx.AsyncClient] = None, limit: int = 10, artist_only: bool = False,
                          offset: int = 0) -> Dict:
    """Search ONE provider (a page of `limit` songs from `offset`). Returns
    {provider, tracks, albums, error, offset, limit, hasMore}. Never raises."""
    q = (query or "").strip()
    lim = max(1, min(limit, 50))
    off = max(0, int(offset or 0))
    out = {"provider": pid, "tracks": [], "albums": [], "error": None, "offset": off, "limit": lim, "hasMore": False}
    if not q or pid not in PROVIDERS:
        return out
    st = provider_status(pid)
    if not st["available"]:
        out["error"] = st["reason"]
        return out
    try:
        if pid == "ytmusic":
            tracks, albums = await _ytmusic_search(q, lim, artist_only, off)
        else:
            client, own = _client(client)
            try:
                tracks, albums = await (_apple_search if pid == "apple" else _deezer_search)(q, client, lim, artist_only, off)
            finally:
                if own:
                    await client.aclose()
    except httpx.HTTPError as e:
        out["error"] = f"{st['name']} is unreachable ({e.__class__.__name__})"
        return out
    except Exception as e:  # third-party tool failures
        out["error"] = f"{st['name']} search failed: {str(e)[:120] or e.__class__.__name__}"
        print(f"[Streaming] {pid} search failed: {e}")
        return out
    remember(tracks)
    remember(albums)
    out["tracks"], out["albums"] = tracks, albums
    out["hasMore"] = len(tracks) >= lim          # a full page: the GUI offers "More results"
    return out


async def search_all(query: str, client: Optional[httpx.AsyncClient] = None, limit: int = 10, artist_only: bool = False,
                     providers: Optional[List[str]] = None, offset: int = 0) -> List[Dict]:
    """Every requested provider in parallel, in PROVIDER_ORDER."""
    pids = [p for p in (providers or PROVIDER_ORDER) if p in PROVIDERS]
    results = await asyncio.gather(*(search_provider(p, query, client, limit, artist_only, offset) for p in pids))
    return list(results)


async def album_tracks(album_id: str, client: Optional[httpx.AsyncClient] = None) -> List[Dict]:
    """Songs of an Apple ('apa_<id>') or Deezer ('dza_<id>') album."""
    client, own = _client(client)
    try:
        if album_id.startswith("apa_"):
            resp = await client.get(ITUNES_LOOKUP_URL, params={"id": album_id[4:], "entity": "song", "limit": "200"})
            if resp.status_code != 200:
                return []
            songs = [t for t in (apple_track(i) for i in (resp.json() or {}).get("results", []) if i.get("wrapperType") == "track") if t]
        elif album_id.startswith("dza_"):
            resp = await client.get(f"{DEEZER_ALBUM_URL}/{album_id[4:]}")
            if resp.status_code != 200:
                return []
            data = resp.json() or {}
            alb = {"title": data.get("title"), "cover_medium": data.get("cover_medium")}
            songs = [t for t in (deezer_track(i, alb) for i in ((data.get("tracks") or {}).get("data") or [])) if t]
        else:
            return []
    except (httpx.HTTPError, ValueError) as e:
        print(f"[Streaming] Album lookup failed: {e.__class__.__name__}")
        return []
    finally:
        if own:
            await client.aclose()
    remember(songs)
    return songs


# ─── YouTube Music audio (yt-dlp) ─────────────────────────────────────────────

def _ytdlp_resolve_sync(video_id: str) -> Dict:
    """Blocking: best audio-only stream URL for a video, via yt-dlp."""
    import yt_dlp  # type: ignore
    opts = {"quiet": True, "no_warnings": True, "format": "bestaudio[acodec!=none]/bestaudio/best", "noplaylist": True,
            "skip_download": True, "extract_flat": False}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"https://music.youtube.com/watch?v={video_id}", download=False)
    url = info.get("url")
    mime = "audio/webm" if (info.get("ext") or "").startswith("webm") else ("audio/mp4" if info.get("ext") in ("m4a", "mp4") else "audio/mpeg")
    if not url and info.get("requested_formats"):
        f = info["requested_formats"][0]
        url, mime = f.get("url"), "audio/mp4"
    if not url:
        raise StreamingError("yt-dlp found no playable audio stream", 502)
    return {"url": url, "mime": mime, "durationMs": int(info.get("duration") or 0) * 1000, "headers": dict(info.get("http_headers") or {})}


ytdlp_resolve_sync: Callable[[str], Dict] = _ytdlp_resolve_sync


async def resolve_audio(track: Dict) -> Dict:
    """Where the relay should fetch a track's audio from: {url, mime, headers}.
    Previews are immediate; YouTube Music is resolved with yt-dlp and cached."""
    if track.get("provider") != "ytmusic":
        if not track.get("audioUrl"):
            raise StreamingError("No preview clip is available for this track.", 404)
        mime = "audio/mp4" if ".m4a" in track["audioUrl"] else "audio/mpeg"
        return {"url": track["audioUrl"], "mime": mime, "headers": {}}
    if not provider_status("ytmusic")["available"]:
        raise StreamingError(provider_status("ytmusic")["reason"], 503)
    cached = _yt_streams.get(track["id"])
    if cached and time.time() - cached["resolvedAt"] < YT_STREAM_TTL_S:
        return cached
    loop = asyncio.get_running_loop()
    try:
        info = await loop.run_in_executor(None, ytdlp_resolve_sync, track["id"][3:])
    except StreamingError:
        raise
    except Exception as e:
        raise StreamingError(f"yt-dlp could not fetch this song ({str(e)[:160] or e.__class__.__name__})", 502) from e
    entry = {"url": info["url"], "mime": info.get("mime") or "audio/webm", "headers": info.get("headers") or {}, "resolvedAt": time.time()}
    if info.get("durationMs") and not track.get("durationMs"):
        track["durationMs"] = info["durationMs"]
    _yt_streams[track["id"]] = entry
    return entry


# ─── Labels ───────────────────────────────────────────────────────────────────

def provider_name(pid: str) -> str:
    return PROVIDERS.get(pid, {}).get("name", pid)


def track_label(t: Dict) -> str:
    label = f"'{t.get('title')}' by {t.get('artist')}"
    if t.get("album"):
        label += f" (album '{t['album']}')"
    return label


def mode_note(track: Dict) -> str:
    """Spoken explanation of what the browser will actually play."""
    name = provider_name(track.get("provider", ""))
    if track.get("full"):
        return f"It streams the full song from {name} through the deck."
    if track.get("audioUrl"):
        return f"It plays the {PREVIEW_SECONDS}-second preview clip from {name} through the deck."
    return f"{name} offers no preview clip for it, so it cannot be played."


def playable(track: Dict) -> bool:
    return bool(track.get("full") or track.get("audioUrl"))

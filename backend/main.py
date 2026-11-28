"""
AI DJ Backend — FastAPI + Gemini Live API proxy (V6.5)

Changes from V6.4:
  - Start-up: the server serves immediately and scans music/audiobooks in a
    background task with progress (/api/status, 503 body of /api/tracks);
    tag reads are cached on disk (scancache.py) so restarts are near-instant.
    POST /api/tracks/rescan.
  - Streaming tab (streaming.py): online sources as sub-sections — Apple
    Music and Deezer previews (keyless) and YouTube Music full songs through
    the third-party tools ytmusicapi + yt-dlp; all audio relayed through the
    deck. Source type "stream", tools playStream / queueStream.

Changes from V6.3:
  - Local audiobooks (audiobooks.py): AUDIOBOOKS_DIR from .env is scanned
    into books with ordered chapters (folder or tag based, disc sub-folders
    merged), REST API under /api/audiobooks with Range streaming, cover art
    and server-side listening progress, voice tools playAudiobook /
    listAudiobooks / describeAudiobook ("continue my book", "chapter 5").
  - playback_state.source understands "audiobook" and "stream"; new browser
    commands playAudiobookChapter, playStreamTracks, queueStreamTracks.
    seekRelative / setPlaybackSpeed work for every seekable source.
    Protocol version 5.

Changes from V6.2:
  - Live radio sources (radio.py): saved internet-radio stations (seeded with
    a few SomaFM channels), optional radio-browser.info directory search,
    REST API under /api/radio, and a same-origin stream proxy
    (/api/radio/{id}/stream) that follows .pls/.m3u playlists and demuxes ICY
    "StreamTitle" metadata so the DJ knows which song is on air.
  - Podcast sources (podcasts.py): RSS subscriptions with cached episodes,
    optional Apple podcast directory search, REST API under /api/podcasts, and
    a Range-forwarding episode proxy so seeking/resume work in the browser.
  - Voice tools: playRadio, listRadioStations, saveRadioStation,
    removeRadioStation, playPodcast, listPodcasts, describePodcast,
    subscribeToPodcast, unsubscribeFromPodcast, seekRelative.
    describeNowPlaying / playback_summary() understand radio & podcast sources.
  - playback_state gained `source` (radio/podcast the browser is playing);
    new browser commands playRadio, playPodcastEpisode, seekRelative,
    radioStationsChanged, podcastsChanged. Protocol version 4.

Changes from V6.1:
  - Voice playlist management tools: createPlaylist, addToPlaylist,
    removeFromPlaylist, renamePlaylist, deletePlaylist (two-step confirm),
    saveQueueAsPlaylist, describePlaylist, queuePlaylist; playPlaylist gained
    `shuffle`. "this song" / "this playlist" resolve from the browser-reported
    playback state; playlist names are fuzzy-matched (playlists.py).
  - New browser commands: `playlistsChanged` (UI refresh after a voice edit)
    and `queuePlaylist` (append a playlist to the queue).
  - Voice queue tools: clearQueue, removeFromQueue, describeQueue.
  - Quiet console: uvicorn access log off by default (HTTP_ACCESS_LOG=true
    to enable, noise-filtered), no ANSI colours, no per-WebSocket chatter,
    iTunes art errors logged once per run.
  - describeNowPlaying tool; playback_summary() leads with the current track
    and flags one-off songs played mid-playlist as NOT part of the playlist.
  - saveQueueAsPlaylist makes the saved playlist the active one (server +
    browser via `setActive`) and returns its track listing, so a follow-up
    "what's in this playlist?" describes the right playlist accurately.

Changes from V6.0:
  - FIXED connect regression: "ready" is now sent immediately on accept
    (like V5). The hello handshake is validated leniently — if the first
    message isn't a hello (e.g. a stale cached app.js), we log a warning
    and serve the client anyway instead of blocking forever. Version
    mismatch is a warning, not a disconnect.
  - Cache-Control: no-cache on all HTTP responses, so browsers can't serve
    a stale app.js/index.html after an update (the root cause of the
    "stuck at Warming up" regression).
"""

import os
import sys
import json
import re
import logging
import asyncio
import time
import hashlib
from pathlib import Path
from collections import OrderedDict
from contextlib import asynccontextmanager

# Ensure UTF-8 output on Windows consoles
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Response, Header, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

from google import genai
from google.genai import types

import httpx

try:
    from backend import playlists as pl_manager
    from backend import radio as radio_manager
    from backend import podcasts as podcast_manager
    from backend import audiobooks as audiobook_manager
    from backend import streaming as streaming_manager
    from backend import scancache
except ImportError:
    import playlists as pl_manager
    import radio as radio_manager
    import podcasts as podcast_manager
    import audiobooks as audiobook_manager
    import streaming as streaming_manager
    import scancache


# ─── Configuration ────────────────────────────────────────────────────────────

PROTOCOL_VERSION = 5

load_dotenv(dotenv_path=Path(__file__).parent / ".env")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
MUSIC_DIR = Path(os.getenv("MUSIC_DIR", Path.home() / "Music"))
# Local audiobooks (optional). Unset/missing directory = the Audiobooks tab
# explains how to configure it; nothing else changes.
_ab_dir = (os.getenv("AUDIOBOOKS_DIR") or "").strip()
AUDIOBOOKS_DIR: Path | None = Path(_ab_dir) if _ab_dir else None
USE_SEARCH_TOOL = os.getenv("USE_SEARCH_TOOL", "true").lower() == "true"
GEMINI_LIVE_MODEL = os.getenv("GEMINI_LIVE_MODEL", "gemini-3.8-live")
# Console verbosity. HTTP_ACCESS_LOG=true re-enables uvicorn's per-request
# lines (art/stream/static requests are still filtered out as noise).
HTTP_ACCESS_LOG = os.getenv("HTTP_ACCESS_LOG", "false").lower() == "true"
LOG_LEVEL = os.getenv("LOG_LEVEL", "info").lower()
# Online directories for discovering radio stations (radio-browser.info) and
# podcasts (Apple podcast search). Both are keyless; set false to stay offline.
ONLINE_DIRECTORIES = os.getenv("ONLINE_DIRECTORIES", "true").lower() == "true"
# Second station directory (TuneIn's unofficial OPML endpoints) — much better
# coverage of local commercial stations. Set false to use radio-browser only.
TUNEIN_DIRECTORY = os.getenv("TUNEIN_DIRECTORY", "true").lower() == "true"
AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".wma", ".aac"}
FRONTEND_DIR = Path(os.getenv("FRONTEND_DIR", Path(__file__).parent.parent / "frontend"))
STREAM_CHUNK_SIZE = 64 * 1024

if not GEMINI_API_KEY and os.getenv("ALLOW_MISSING_API_KEY", "false").lower() != "true":
    raise RuntimeError("GEMINI_API_KEY not set in .env")

# Global State
music_library: list[dict] = []
tracks_by_id: dict[str, dict] = {}
scan_complete: bool = False
# Live progress of the background library scan, for /api/status and the
# browser's loading message: phase = listing | reading | done, found = files
# seen, read = files whose tags are known so far.
scan_progress: dict = {"phase": "idle", "found": 0, "read": 0, "startedAt": None, "finishedAt": None, "cached": 0}
album_art_cache: OrderedDict = OrderedDict()
# station_id -> {"title": "Artist - Song", "updatedAt": epoch}; filled by the
# radio stream proxy from ICY metadata while a station is being relayed.
radio_now_playing: dict[str, dict] = {}
# Non-library playback sources the browser may report in playback_state.source.
# Adding a new kind = add it here + a summary formatter in
# ClientConn.source_summary + a browser tab (see README "Adding a Source").
SOURCE_TYPES = {"radio", "podcast", "audiobook", "stream"}
# Sources that can be skipped within / sped up (everything but live radio).
SEEKABLE_SOURCE_TYPES = {"podcast", "audiobook", "stream"}
SOURCE_NOUNS = {"radio": "a live radio station", "podcast": "a podcast episode",
                "audiobook": "an audiobook chapter", "stream": "a streamed track"}

# Audiobooks scanned from AUDIOBOOKS_DIR (books with ordered chapters).
audiobook_library: list[dict] = []
audiobooks_by_id: dict[str, dict] = {}
audiobooks_scan_complete: bool = False


def _stable_track_id(rel_path: str) -> str:
    """Content-addressable ID: rescans keep IDs stable even if files are added."""
    return "t_" + hashlib.md5(rel_path.encode("utf-8")).hexdigest()[:10]


# ─── Music Library Scanner ────────────────────────────────────────────────────

def _get_metadata(filepath: Path):
    try:
        from mutagen import File as MutagenFile
        audio = MutagenFile(filepath, easy=True)
        if audio and audio.tags:
            title  = audio.tags.get("title",  [filepath.stem])[0]
            artist = audio.tags.get("artist", ["Unknown Artist"])[0]
            album  = audio.tags.get("album",  [""])[0]
            genre  = audio.tags.get("genre",  [filepath.suffix[1:].upper()])[0]
            return title, artist, album, genre
    except Exception:
        pass
    return filepath.stem, "Unknown Artist", "", filepath.suffix[1:].upper()


def _read_track_meta(filepath: Path) -> dict:
    title, artist, album, genre = _get_metadata(filepath)
    return {"title": title, "artist": artist, "album": album, "genre": genre}


def scan_music_library(progress: dict | None = None, use_cache: bool = True) -> list[dict]:
    """Walk MUSIC_DIR into track records. Tags come from the on-disk cache
    when a file's size/mtime are unchanged, so only new files hit mutagen.
    `progress` (scan_progress) is updated as it goes for the loading UI."""
    progress = progress if progress is not None else {}
    progress.update({"phase": "listing", "found": 0, "read": 0, "cached": 0, "startedAt": time.time(), "finishedAt": None})
    tracks: list[dict] = []
    if not MUSIC_DIR.exists():
        print(f"[Warn] Music directory not found: {MUSIC_DIR}")
        progress.update({"phase": "done", "finishedAt": time.time()})
        return tracks
    files = [f for f in sorted(MUSIC_DIR.rglob("*")) if f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS]
    progress.update({"phase": "reading", "found": len(files)})
    cache = scancache.TagCache("library") if use_cache else None
    for f in files:
        rel = str(f.relative_to(MUSIC_DIR))
        meta = cache.get(f, rel.replace("\\", "/"), _read_track_meta) if cache else _read_track_meta(f)
        tracks.append({
            "id": _stable_track_id(rel),
            "title": meta.get("title") or f.stem, "artist": meta.get("artist") or "Unknown Artist",
            "album": meta.get("album") or "", "genre": meta.get("genre") or f.suffix[1:].upper(),
            "filename": rel,
        })
        progress["read"] = len(tracks)
    if cache:
        cache.save()
        progress["cached"] = cache.hits
    progress.update({"phase": "done", "finishedAt": time.time()})
    return tracks


# ─── Album Art Fetcher ────────────────────────────────────────────────────────

def _detect_image_mime(data: bytes, fallback: str = "image/jpeg") -> str:
    if not data:
        return fallback
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "image/gif"
    if data.startswith(b"RIFF") and len(data) >= 12 and data[8:12] == b"WEBP":
        return "image/webp"
    return fallback


def extract_local_art(filepath: Path) -> tuple[bytes, str] | None:
    try:
        from mutagen import File as MutagenFile
        audio = MutagenFile(filepath)
        if audio:
            if hasattr(audio, 'tags') and audio.tags:
                for tag in audio.tags.values():
                    if type(tag).__name__ == 'APIC':
                        mime = getattr(tag, 'mime', '') or _detect_image_mime(tag.data)
                        return tag.data, mime
                    if type(tag).__name__ == 'Picture':
                        mime = getattr(tag, 'mime', '') or _detect_image_mime(tag.data)
                        return tag.data, mime
            if hasattr(audio, 'pictures') and audio.pictures:
                pic = audio.pictures[0]
                mime = getattr(pic, 'mime', '') or _detect_image_mime(pic.data)
                return pic.data, mime
    except Exception:
        pass
    return None


async def fetch_itunes_art(artist: str, title: str) -> tuple[bytes, str] | None:
    try:
        query = f"{artist} {title}".replace(" ", "+")
        url = f"https://itunes.apple.com/search?term={query}&entity=song&limit=1"
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=5.0)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("results"):
                    img_url = data["results"][0].get("artworkUrl100")
                    if img_url:
                        img_url = img_url.replace("100x100bb", "600x600bb")
                        img_resp = await client.get(img_url, timeout=5.0)
                        if img_resp.status_code == 200 and img_resp.content:
                            mime = img_resp.headers.get("content-type", "image/jpeg")
                            return img_resp.content, mime
    except Exception as e:
        # One line per run, not one per track (offline machines hit this for
        # every track without embedded artwork).
        global _itunes_error_logged
        if not _itunes_error_logged:
            _itunes_error_logged = True
            print(f"[Art] iTunes artwork lookup unavailable ({e}); further lookup errors are not logged")
    return None


_itunes_error_logged = False


MAX_ART_CACHE = 100


def _cache_art(track_id: str, art_data: tuple[bytes, str] | None):
    # True LRU via OrderedDict
    if track_id in album_art_cache:
        album_art_cache.move_to_end(track_id)
        album_art_cache[track_id] = art_data
        return
    if len(album_art_cache) >= MAX_ART_CACHE:
        album_art_cache.popitem(last=False)  # evict least-recently-used
    album_art_cache[track_id] = art_data


# ─── FastAPI Lifespan (Async Init) ────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    import platform
    if platform.system() == "Windows":
        loop = asyncio.get_running_loop()
        default_handler = loop.get_exception_handler()

        def custom_exception_handler(loop, context):
            if "exception" in context:
                exc = context["exception"]
                if isinstance(exc, ConnectionResetError) and getattr(exc, "winerror", None) == 10054:
                    return
            if default_handler:
                default_handler(loop, context)
            else:
                loop.default_exception_handler(context)

        loop.set_exception_handler(custom_exception_handler)

    # The server starts serving immediately; the library scans run in the
    # background and the browser shows their progress (a cold scan of a large
    # library used to block every request for its whole duration).
    print("⏳ Scanning music library in the background…")
    scan_task = asyncio.create_task(_startup_scans())
    print(f"📻 {len(radio_manager.load_stations())} radio stations, 🎙 {len(podcast_manager.load_podcasts())} podcast subscriptions"
          f" (online directories {'on' if ONLINE_DIRECTORIES else 'off'})")
    avail = ", ".join(streaming_manager.provider_name(p) for p in streaming_manager.available_providers())
    print(f"🌐 Streaming sources: {avail}" + ("" if streaming_manager.provider_status("ytmusic")["available"] else "  (YouTube Music: pip install ytmusicapi yt-dlp)"))
    print(f"🔍 USE_SEARCH_TOOL is set to {USE_SEARCH_TOOL}")
    refresh_task = asyncio.create_task(_fetch_unfetched_podcasts())
    yield
    scan_task.cancel()
    refresh_task.cancel()
    print("Shutting down AI DJ backend.")


async def rescan_music_library() -> int:
    """(Re)scan MUSIC_DIR off the event loop; returns the track count."""
    global music_library, tracks_by_id, scan_complete
    loop = asyncio.get_running_loop()
    t0 = time.perf_counter()
    music_library = await loop.run_in_executor(None, scan_music_library, scan_progress)
    tracks_by_id = {t["id"]: t for t in music_library}
    scan_complete = True
    print(f"🎵 Loaded {len(music_library)} tracks from {MUSIC_DIR} in {time.perf_counter() - t0:.1f}s "
          f"({scan_progress.get('cached', 0)} from cache)")
    return len(music_library)


async def _startup_scans():
    try:
        await rescan_music_library()
        await rescan_audiobooks()
        if AUDIOBOOKS_DIR:
            print(f"📖 Loaded {len(audiobook_library)} audiobooks from {AUDIOBOOKS_DIR}")
        else:
            print("📖 Audiobooks: AUDIOBOOKS_DIR not set (optional)")
    except asyncio.CancelledError:
        raise
    except Exception as e:
        print(f"[Scan] Library scan failed: {e}")
        scan_progress.update({"phase": "done", "finishedAt": time.time(), "error": str(e)})
        globals()["scan_complete"] = True
        globals()["audiobooks_scan_complete"] = True


async def rescan_audiobooks() -> int:
    """(Re)scan AUDIOBOOKS_DIR off the event loop; returns the book count."""
    global audiobook_library, audiobooks_by_id, audiobooks_scan_complete
    loop = asyncio.get_running_loop()
    books = await loop.run_in_executor(None, audiobook_manager.scan_audiobooks, AUDIOBOOKS_DIR)
    audiobook_library = books
    audiobooks_by_id = {b["id"]: b for b in books}
    audiobooks_scan_complete = True
    return len(books)


async def _fetch_unfetched_podcasts():
    """Starter-pick podcasts are seeded without episodes; load them in the
    background so the Podcasts tab fills in shortly after start-up. Feeds that
    fail (offline) are simply retried the next time they are opened/played."""
    try:
        async with _http_client(timeout=15.0) as client:
            results = await podcast_manager.refresh_unfetched(client=client)
    except Exception as e:
        print(f"[Podcasts] Background feed fetch failed: {e}")
        return
    if results:
        ok = sum(1 for err in results.values() if err is None)
        failed = [err for err in results.values() if err]
        print(f"🎙 Loaded episodes for {ok}/{len(results)} starter podcasts" + (f" ({len(failed)} failed: {failed[0]})" if failed else ""))


# ─── Track Search Matching ────────────────────────────────────────────────────

def find_matching_tracks(query: str, library: list[dict]) -> list[dict]:
    if not library or not query:
        return []
    q = query.lower().strip()

    # 1. Exact substring matches
    exact = [
        t for t in library
        if (
            q in t["title"].lower()
            or q in t["artist"].lower()
            or q in t["genre"].lower()
            or q in t.get("album", "").lower()
        )
    ]
    if exact:
        return exact

    # 2. Token overlap matches
    stop_words = {
        "a", "an", "the", "some", "music", "song", "track", "play", "please", "like", "by", "something",
        "queue", "cue", "next", "up", "put", "on", "add"
    }
    tokens = [w for w in re.split(r"[\s\-_,.]+", q) if len(w) > 1 and w not in stop_words]
    if not tokens:
        tokens = [w for w in re.split(r"[\s\-_,.]+", q) if w]

    scored = []
    for t in library:
        score = 0
        title_l = t["title"].lower()
        artist_l = t["artist"].lower()
        genre_l = t["genre"].lower()
        album_l = t.get("album", "").lower()

        for token in tokens:
            if token in title_l:
                score += 4
            if token in artist_l:
                score += 3
            if token in genre_l:
                score += 2
            if token in album_l:
                score += 1

        if score > 0:
            scored.append((score, t))

    if scored:
        scored.sort(key=lambda x: x[0], reverse=True)
        top_score = scored[0][0]
        return [t for s, t in scored if s >= top_score - 1]

    return []


# ─── Gemini Config ────────────────────────────────────────────────────────────

PLAYLIST_MANAGEMENT_INSTRUCTIONS = (
    "\nPLAYLIST MANAGEMENT (voice controls):\n"
    "- CREATE: 'make/create a new playlist called X' -> `createPlaylist` (does NOT start playback). "
    "If they also say 'with this song', set includeCurrentTrack=true.\n"
    "- ADD: 'add this (song) to X', 'save this to my favorites', 'put <song> in X' -> `addToPlaylist`. "
    "Leave trackQuery empty when they mean the song playing right now. If X does not exist it is created automatically.\n"
    "- REMOVE: 'remove this from X', 'take <song> out of X' -> `removeFromPlaylist`.\n"
    "- RENAME: 'rename X to Y', 'call this playlist Y' -> `renamePlaylist`.\n"
    "- DELETE: 'delete/remove the X playlist' -> `deletePlaylist`. ALWAYS call it first with confirmed=false, "
    "read the playlist name and track count back, ask 'Are you sure?', and only after a clear yes call it again with confirmed=true.\n"
    "- SAVE QUEUE: 'save this queue/session/mix as a playlist (called X)' -> `saveQueueAsPlaylist`.\n"
    "- INSPECT: 'what's in X', 'how many songs are in X' -> `describePlaylist`, then summarise briefly (don't read every track unless asked).\n"
    "- QUEUE A PLAYLIST: 'queue up X', 'play X after this', 'add X to the queue' -> `queuePlaylist` (does not interrupt the current track). "
    "Plain 'play X' or 'shuffle X' -> `playPlaylist` (shuffle=true for shuffle).\n"
    "- 'this playlist', 'the current playlist' refers to the playlist that is playing now: pass playlistName='current'.\n"
    "- Playlist names may be spoken loosely ('my rock playlist', 'the 80s mix'); pass them as heard, the tool does fuzzy matching. "
    "If a tool reports no match, tell the user which playlists exist and ask which they meant.\n"
    "- After any change, confirm briefly what you did (e.g. 'Added Billie Jean to Workout, that's 7 tracks now').\n"
    "- NEVER describe a playlist's contents from memory or guess them: call `describePlaylist` (or use the listing "
    "returned by the tool you just called) and repeat only what it lists, in that order.\n"
    "\nQUEUE MANAGEMENT (voice controls):\n"
    "- 'clear/empty the queue', 'cancel what's coming up', 'clear the cue' -> `clearQueue` (the current song keeps playing).\n"
    "- 'remove <song> from the queue', 'don't play <song> next' -> `removeFromQueue`.\n"
    "- 'what's coming up', 'what's in the queue', 'what's next' -> `describeQueue`, then read it back.\n"
    "- 'skip' / 'next' -> `nextTrack`; it does NOT clear the queue.\n"
    "\nWHAT'S PLAYING:\n"
    "- 'what's playing', 'what song is this', 'who is this', 'what is this' -> `describeNowPlaying`, then describe THAT SONG "
    "(title, artist, album/genre). Do not describe the playlist unless asked; a playlist being active does not mean the "
    "current song is from it — the tool tells you whether it is.\n"
)


RADIO_PODCAST_INSTRUCTIONS = (
    "\nLIVE RADIO (voice controls):\n"
    "- 'play/put on/tune in to <station>', 'play some jazz radio', 'put the radio on' -> `playRadio`. Saved stations are tried "
    "first (names are fuzzy-matched, genres count too); if none fits, the online station directories are searched. "
    "Pass a city/region as `place` when mentioned ('a Halifax station', 'radio from Montreal') and a country as `country`. "
    "A radio request is NOT a playlist request: do not use createAndPlayPlaylist for 'radio' or 'station'.\n"
    "- 'what stations do I have' -> `listRadioStations`. 'save/keep this station', 'add a <genre> station' -> `saveRadioStation`. "
    "'remove/forget the X station' -> `removeRadioStation`.\n"
    "- Live radio cannot be paused-and-resumed in place, skipped, or seeked; 'stop the radio' -> `pausePlayback`. "
    "'what song is this' while radio plays -> `describeNowPlaying` (it reports the station's on-air title when known).\n"
    "\nPODCASTS (voice controls):\n"
    "- 'play <podcast>', 'play the latest <podcast>', 'play episode 12 of X', 'play the next/previous episode', "
    "'continue my podcast' -> `playPodcast` (episode defaults to the latest; playback resumes where the user left off).\n"
    "- 'what podcasts do I have' -> `listPodcasts`. 'what episodes are there', 'what's the latest X about' -> `describePodcast`.\n"
    "- 'subscribe to X', 'add the X podcast', 'follow X' -> `subscribeToPodcast` (searches the podcast directory, does not start playback). "
    "'unsubscribe from X', 'remove the X podcast' -> `unsubscribeFromPodcast`.\n"
    "- 'what's new in my podcasts', 'any new episodes' -> `listLatestEpisodes`; 'play the newest episodes', 'catch me up', 'cue up what's new' -> `playLatestEpisodes` "
    "(the newest episode of each show, newest first; queueOnly=true to cue without interrupting).\n"
    "- 'skip ahead 30 seconds', 'go back a minute', 'rewind 15 seconds' -> `seekRelative` (negative seconds = back).\n"
    "- 'play faster', 'speed it up', 'one and a half speed', 'normal speed' -> `setPlaybackSpeed` (rate 0.5–3; 1 = normal). Podcasts and audiobooks only — music and live radio always play at 1x.\n"
    "\nVOLUME:\n"
    "- 'turn it up/down', 'louder', 'quieter', 'volume 40 percent', 'half volume', 'mute', 'max volume' -> `setVolume` "
    "(level 0–100, or change e.g. +15/-15). This is the app's music volume slider, separate from your own voice.\n"
    "- Podcast and radio names may be spoken loosely; pass them as heard, the tools fuzzy-match. If a tool reports no match, "
    "tell the user what IS available and ask which they meant.\n"
    "- Switching back to music: any track/playlist tool (searchAndPlay, playPlaylist, nextTrack…) leaves the radio/podcast/audiobook/streaming source and plays the library.\n"
    "- STARTER PICKS: on first run the Radio and Podcasts tabs are pre-loaded with popular stations and shows. If asked, explain they are "
    "starter picks the user can remove, and that more can be added by voice ('save a jazz station', 'subscribe to <show>') or with the "
    "Find Stations / Subscribe buttons in the GUI.\n"
)


AUDIOBOOK_STREAMING_INSTRUCTIONS = (
    "\nAUDIOBOOKS (voice controls; local files from the user's audiobook folder):\n"
    "- 'play/continue/resume <book>', 'continue my audiobook', 'keep reading', 'play chapter 5 of X', 'next chapter', "
    "'start X from the beginning' -> `playAudiobook` (book defaults to the one last listened to; chapter defaults to where the user left off).\n"
    "- 'what audiobooks do I have', 'which books are in progress' -> `listAudiobooks`. 'how far am I in X', 'what chapters does X have', "
    "'who narrates X' -> `describeAudiobook`.\n"
    "- While a chapter plays, `seekRelative` ('go back 30 seconds') and `setPlaybackSpeed` ('1.5x') work like for podcasts; "
    "`nextTrack` / `previousTrack` move between chapters. Book progress is remembered automatically.\n"
    "- An audiobook request is NOT a music request: never use searchAndPlay or createAndPlayPlaylist for 'book', 'audiobook', 'chapter' or 'novel'.\n"
    "\nSTREAMING (voice controls; the Streaming tab searches online sources: Apple Music previews, Deezer previews, YouTube Music full songs):\n"
    "- 'stream <song/artist/album>', 'play <song> online / from YouTube Music / from Deezer', or a song that is NOT in the local library when the user "
    "asks for it anyway -> `playStream` (kind = track/album/artist when the user says which; source = the provider when they name one). "
    "'queue/cue <song> from streaming' -> `queueStream`.\n"
    "- HOW IT PLAYS: Apple Music and Deezer give 30-second preview clips; YouTube Music plays the full song (when its tools are installed). "
    "Everything plays through the deck. The tool result says which source and whether it is a clip; mention it briefly only the first time or when asked.\n"
    "- Prefer the local library for plain 'play <song>' requests; use streaming when the user says 'stream', names a source, or the library has no match and they want the song anyway.\n"
)


def _sources_summary() -> str:
    """Saved station / podcast / audiobook names (and streaming sources) for the
    system prompt, so the DJ can recognise them in speech without a round-trip."""
    parts = []
    stations = radio_manager.load_stations()
    if stations:
        names = ", ".join(f"'{s['name']}'" for s in stations[:15])
        more = f" (+{len(stations) - 15} more)" if len(stations) > 15 else ""
        parts.append(f"Saved radio stations: {names}{more}.")
    else:
        parts.append("No radio stations are saved yet" + (" (the online directory can find some)." if ONLINE_DIRECTORIES else "."))
    pods = podcast_manager.load_podcasts()
    if pods:
        names = ", ".join(f"'{p['title']}'" for p in pods[:15])
        more = f" (+{len(pods) - 15} more)" if len(pods) > 15 else ""
        parts.append(f"Podcast subscriptions: {names}{more}.")
    else:
        parts.append("No podcast subscriptions yet" + (" (subscribeToPodcast can find shows by name)." if ONLINE_DIRECTORIES else "."))
    if AUDIOBOOKS_DIR is None:
        parts.append("Audiobooks are not configured (no AUDIOBOOKS_DIR in .env); say so if asked for one.")
    elif audiobook_library:
        progress = audiobook_manager.load_progress()
        names = []
        for b in audiobook_library[:15]:
            prog = progress.get(b["id"])
            tag = " [in progress]" if prog and not prog.get("finished") else (" [finished]" if prog else "")
            names.append(f"'{b['title']}'" + (f" by {b['author']}" if b.get("author") and b["author"] != "Unknown Author" else "") + tag)
        more = f" (+{len(audiobook_library) - 15} more)" if len(audiobook_library) > 15 else ""
        parts.append(f"Audiobooks: {'; '.join(names)}{more}.")
    else:
        parts.append("The audiobook folder is configured but no audiobooks were found in it.")
    avail = [streaming_manager.provider_name(p) for p in streaming_manager.available_providers()]
    missing = [streaming_manager.provider_name(p) for p in streaming_manager.PROVIDER_ORDER if p not in streaming_manager.available_providers()]
    parts.append(f"Streaming sources available: {', '.join(avail) or 'none'}" + (f" (not installed: {', '.join(missing)})" if missing else "") + ".")
    return " ".join(parts)


def build_gemini_config():
    system_instruction = (
        "You are AI DJ, an AI Music DJ. You are live on the air, chatting "
        "with the user. You have a cool, energetic, and slightly robotic DJ persona.\n"
        "Your voice is Zephyr.\n"
        "You control the local music playback using tools.\n"
        "Always acknowledge the user's request verbally before or after using the tool. Keep it brief and punchy.\n"
    )

    tool_declarations = [
        {
            "name": "playTrack",
            "description": "Plays a specific track from the local library by its track ID immediately.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "trackId": {
                        "type": "STRING",
                        "description": "The ID of the track to play (e.g. 't_1a2b3c4d5e').",
                    }
                },
                "required": ["trackId"],
            },
        },
        {
            "name": "queueTrack",
            "description": "Cues up / adds a specific track from the local library to the playback queue to be played next.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "trackId": {
                        "type": "STRING",
                        "description": "The ID of the track to cue up / queue (e.g. 't_1a2b3c4d5e').",
                    }
                },
                "required": ["trackId"],
            },
        },
        {"name": "pausePlayback", "description": "Pauses the currently playing track.",
         "parameters": {"type": "OBJECT", "properties": {}}},
        {"name": "resumePlayback", "description": "Resumes the currently paused track.",
         "parameters": {"type": "OBJECT", "properties": {}}},
        {"name": "nextTrack", "description": "Skips to the next track in the library (or next cued song in queue).",
         "parameters": {"type": "OBJECT", "properties": {}}},
        {"name": "previousTrack", "description": "Goes back to the previous track.",
         "parameters": {"type": "OBJECT", "properties": {}}},
        {
            "name": "createAndPlayPlaylist",
            "description": "Dynamically curates and plays a multi-track playlist based on a theme, genre, decade/era, mood, or artist (such as '80s music', 'chill beats', 'classic rock', 'dance party'). ALWAYS use this when the user asks for an era, genre, or mood of music.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "query": {
                        "type": "STRING",
                        "description": "The style, era, genre, artist, or theme for the playlist (e.g. '80s music', 'chill vibes', 'classic rock', 'pop hits').",
                    },
                    "playlistName": {
                        "type": "STRING",
                        "description": "Optional custom name for the playlist.",
                    },
                },
                "required": ["query"],
            },
        },
        {
            "name": "playPlaylist",
            "description": "Plays an existing saved playlist by its name or keywords. Set shuffle=true if the user asks to shuffle it.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "name": {
                        "type": "STRING",
                        "description": "The name of the playlist to play (e.g. '80s Rewind', 'Chill Waves').",
                    },
                    "shuffle": {
                        "type": "BOOLEAN",
                        "description": "Play the playlist in random order. Default false.",
                    },
                },
                "required": ["name"],
            },
        },
        {
            "name": "listPlaylists",
            "description": "Lists the available saved playlists in the library.",
            "parameters": {"type": "OBJECT", "properties": {}},
        },
        # ── Voice playlist management ──────────────────────────────────────
        {
            "name": "createPlaylist",
            "description": "Creates a new, empty saved playlist with the given name WITHOUT playing anything. Optionally seeds it with the track that is playing right now.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "name": {
                        "type": "STRING",
                        "description": "Name for the new playlist (e.g. 'Workout', 'Sunday Morning').",
                    },
                    "includeCurrentTrack": {
                        "type": "BOOLEAN",
                        "description": "If true, the currently playing track is added as the first song. Default false.",
                    },
                },
                "required": ["name"],
            },
        },
        {
            "name": "addToPlaylist",
            "description": "Adds a song to a saved playlist. Use for 'add this song to my workout playlist', 'save this to favorites', 'put Billie Jean in the party mix'. If the playlist doesn't exist yet it is created.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "playlistName": {
                        "type": "STRING",
                        "description": "Name of the target playlist. Use 'current' for the playlist that is playing right now.",
                    },
                    "trackQuery": {
                        "type": "STRING",
                        "description": "Song title/artist to add. Leave empty or say 'current' for the track playing right now.",
                    },
                    "trackId": {
                        "type": "STRING",
                        "description": "Optional exact track ID (t_…) if known.",
                    },
                },
                "required": ["playlistName"],
            },
        },
        {
            "name": "removeFromPlaylist",
            "description": "Removes a song from a saved playlist. Use for 'take this song out of my workout playlist', 'remove Thriller from the party mix'.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "playlistName": {
                        "type": "STRING",
                        "description": "Name of the playlist. Use 'current' for the playlist that is playing right now.",
                    },
                    "trackQuery": {
                        "type": "STRING",
                        "description": "Song title/artist to remove. Leave empty or say 'current' for the track playing right now.",
                    },
                    "trackId": {
                        "type": "STRING",
                        "description": "Optional exact track ID (t_…) if known.",
                    },
                },
                "required": ["playlistName"],
            },
        },
        {
            "name": "renamePlaylist",
            "description": "Renames a saved playlist.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "playlistName": {
                        "type": "STRING",
                        "description": "Current name of the playlist. Use 'current' for the playlist that is playing right now.",
                    },
                    "newName": {
                        "type": "STRING",
                        "description": "The new name.",
                    },
                },
                "required": ["playlistName", "newName"],
            },
        },
        {
            "name": "deletePlaylist",
            "description": "Permanently deletes a saved playlist. Destructive: first call with confirmed=false to get the playlist details, ask the user to confirm, then call again with confirmed=true.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "playlistName": {
                        "type": "STRING",
                        "description": "Name of the playlist to delete.",
                    },
                    "confirmed": {
                        "type": "BOOLEAN",
                        "description": "True only after the user has explicitly confirmed the deletion.",
                    },
                },
                "required": ["playlistName"],
            },
        },
        {
            "name": "saveQueueAsPlaylist",
            "description": "Saves the current session (the track playing now plus everything cued up in the queue) as a new saved playlist. Use for 'save this queue', 'save this session as a playlist', 'remember this mix'.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "name": {
                        "type": "STRING",
                        "description": "Name for the saved playlist. Optional; a dated 'Session Mix' name is used if omitted.",
                    },
                    "includeCurrentTrack": {
                        "type": "BOOLEAN",
                        "description": "Include the currently playing track as the first song. Default true.",
                    },
                },
            },
        },
        {
            "name": "describePlaylist",
            "description": "Tells you what songs are in a saved playlist so you can read them back to the user. Use for 'what's in my chill playlist', 'how many songs are in the 80s mix'.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "playlistName": {
                        "type": "STRING",
                        "description": "Name of the playlist. Use 'current' for the playlist that is playing right now.",
                    }
                },
                "required": ["playlistName"],
            },
        },
        {
            "name": "queuePlaylist",
            "description": "Adds every song of a saved playlist to the END of the queue without interrupting the current track. Use for 'queue up my chill playlist', 'add the 80s mix to the queue', 'play my workout playlist after this'.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "playlistName": {
                        "type": "STRING",
                        "description": "Name of the playlist to queue.",
                    },
                    "shuffle": {
                        "type": "BOOLEAN",
                        "description": "Queue the songs in random order. Default false.",
                    },
                },
                "required": ["playlistName"],
            },
        },
        # ── Voice queue management ─────────────────────────────────────────
        {
            "name": "clearQueue",
            "description": "Removes every cued-up track from the queue (the 'Up Next' list). The current track keeps playing. Use for 'clear the queue', 'empty the cue', 'cancel everything that's coming up'.",
            "parameters": {"type": "OBJECT", "properties": {}},
        },
        {
            "name": "removeFromQueue",
            "description": "Removes one cued-up song from the queue without stopping the current track. Use for 'take Thriller out of the queue', 'don't play Heaven next'.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "trackQuery": {
                        "type": "STRING",
                        "description": "Title/artist of the cued song to remove, or 'next' for the song that is up next.",
                    },
                    "trackId": {
                        "type": "STRING",
                        "description": "Optional exact track ID (t_…) if known.",
                    },
                },
                "required": ["trackQuery"],
            },
        },
        {
            "name": "describeQueue",
            "description": "Tells you what is cued up next so you can read it back. Use for 'what's coming up', 'what's in the queue', 'how many songs are cued'.",
            "parameters": {"type": "OBJECT", "properties": {}},
        },
        {
            "name": "describeNowPlaying",
            "description": "Tells you exactly what is playing right now: the SONG (title, artist, album, genre) and whether it belongs to the active playlist, or the live radio station (with its on-air song title when known), or the podcast episode and position. ALWAYS call this for 'what's playing', 'what song is this', 'who sings this', 'what is this' — answer about the song, not the playlist.",
            "parameters": {"type": "OBJECT", "properties": {}},
        },
        # ── Live radio ────────────────────────────────────────────────────
        {
            "name": "playRadio",
            "description": "Tunes in to a live internet radio station and starts playing it. Use for 'play <station>', 'put on some jazz radio', 'tune in to the BBC', 'play the radio'. Saved stations are matched first by name or genre; otherwise the online station directory is searched.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "query": {"type": "STRING", "description": "Station name, genre or keywords as spoken (e.g. 'groove salad', 'jazz', 'move 100', 'classic rock station'). Empty = the last played / first saved station."},
                    "country": {"type": "STRING", "description": "Optional country to narrow an online directory search (e.g. 'Canada')."},
                    "place": {"type": "STRING", "description": "Optional city or region mentioned by the user (e.g. 'Halifax', 'Nova Scotia')."},
                },
            },
        },
        {
            "name": "listRadioStations",
            "description": "Lists the user's saved radio stations (name and genre).",
            "parameters": {"type": "OBJECT", "properties": {}},
        },
        {
            "name": "saveRadioStation",
            "description": "Saves a radio station to the user's station list without changing playback. Empty query = save the station that is playing right now (e.g. one found in the directory). A query such as 'a jazz station' or 'BBC Radio 6' searches the directory and saves the best match.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "query": {"type": "STRING", "description": "Station to find and save, or empty/'this' for the station playing now."},
                    "name": {"type": "STRING", "description": "Optional custom name to save it under."},
                },
            },
        },
        {
            "name": "removeRadioStation",
            "description": "Removes a saved radio station. Use for 'remove/forget/delete the X station'. Use 'current' for the station playing now.",
            "parameters": {
                "type": "OBJECT",
                "properties": {"name": {"type": "STRING", "description": "Saved station name as spoken, or 'current'."}},
                "required": ["name"],
            },
        },
        # ── Podcasts ──────────────────────────────────────────────────────
        {
            "name": "playPodcast",
            "description": "Plays an episode of a subscribed podcast. Use for 'play <podcast>', 'play the latest <podcast>', 'play episode 12 of X', 'play the next/previous episode', 'continue my podcast'. Episode defaults to the latest. Use podcast='current' for the podcast playing now.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "podcast": {"type": "STRING", "description": "Podcast title as spoken, or 'current' for the one playing now."},
                    "episode": {"type": "STRING", "description": "Which episode: empty/'latest', 'oldest', 'second latest', 'episode 45', 'next', 'previous', or title keywords."},
                },
                "required": ["podcast"],
            },
        },
        {
            "name": "listPodcasts",
            "description": "Lists the podcasts the user is subscribed to, with their latest episode.",
            "parameters": {"type": "OBJECT", "properties": {}},
        },
        {
            "name": "describePodcast",
            "description": "Tells you a podcast's recent episodes (titles, dates, lengths) so you can read them back. Use for 'what episodes of X are there', 'what's the latest X about'.",
            "parameters": {
                "type": "OBJECT",
                "properties": {"podcast": {"type": "STRING", "description": "Podcast title as spoken, or 'current'."}},
                "required": ["podcast"],
            },
        },
        {
            "name": "subscribeToPodcast",
            "description": "Finds a podcast by name in the online podcast directory (or takes a feed URL) and subscribes to it WITHOUT playing. Use for 'subscribe to X', 'add the X podcast', 'follow X'.",
            "parameters": {
                "type": "OBJECT",
                "properties": {"query": {"type": "STRING", "description": "Podcast name as spoken, or an RSS feed URL."}},
                "required": ["query"],
            },
        },
        {
            "name": "unsubscribeFromPodcast",
            "description": "Removes a podcast subscription. Use for 'unsubscribe from X', 'remove the X podcast'.",
            "parameters": {
                "type": "OBJECT",
                "properties": {"podcast": {"type": "STRING", "description": "Podcast title as spoken, or 'current'."}},
                "required": ["podcast"],
            },
        },
        # ── Audiobooks ────────────────────────────────────────────────────
        {
            "name": "playAudiobook",
            "description": "Plays (or continues) a local audiobook. Use for 'continue my book', 'play the Hobbit', 'play chapter 5 of Dune', 'next chapter', 'start it from the beginning'. Book defaults to the one most recently listened to; chapter defaults to where the user left off.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "book": {"type": "STRING", "description": "Book title or author as spoken, or empty/'current' for the book playing now or listened to most recently."},
                    "chapter": {"type": "STRING", "description": "Empty/'continue' = resume; 'chapter 5'; 'next'; 'previous'; 'from the beginning'; 'last chapter'; or chapter title keywords."},
                },
            },
        },
        {
            "name": "listAudiobooks",
            "description": "Lists the user's local audiobooks with author, length and listening progress.",
            "parameters": {"type": "OBJECT", "properties": {}},
        },
        {
            "name": "describeAudiobook",
            "description": "Details of one audiobook: author, narrator, length, chapters and how far the user has listened. Use for 'how far am I in X', 'what chapters does X have', 'who reads X'.",
            "parameters": {
                "type": "OBJECT",
                "properties": {"book": {"type": "STRING", "description": "Book title/author as spoken, or 'current'."}},
            },
        },
        # ── Streaming sources ─────────────────────────────────────────────
        {
            "name": "playStream",
            "description": "Plays music from the online streaming sources (Streaming tab): a song (default), a whole album, or an artist's songs. Use when the user says 'stream', names a source (Apple Music, Deezer, YouTube Music) or wants a song the local library lacks. Apple Music/Deezer give 30-second previews; YouTube Music plays full songs.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "query": {"type": "STRING", "description": "Song, artist or album as spoken (e.g. 'Blinding Lights', 'Daft Punk', 'the album Discovery')."},
                    "kind": {"type": "STRING", "description": "What to look for: 'track' (default), 'album' or 'artist'."},
                    "source": {"type": "STRING", "description": "Optional provider the user named: 'apple', 'deezer' or 'youtube'. Empty = the first source with a match."},
                },
                "required": ["query"],
            },
        },
        {
            "name": "queueStream",
            "description": "Finds a song on the online streaming sources and adds it to the END of the queue without interrupting playback. Use for 'queue up X from YouTube Music', 'stream X next'.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "query": {"type": "STRING", "description": "Song (and artist) as spoken."},
                    "source": {"type": "STRING", "description": "Optional provider: 'apple', 'deezer' or 'youtube'."},
                },
                "required": ["query"],
            },
        },
        {
            "name": "listLatestEpisodes",
            "description": "Lists the newest episode of each subscribed podcast, newest first. Use for 'what's new in my podcasts', 'any new episodes', 'what came out today'.",
            "parameters": {
                "type": "OBJECT",
                "properties": {"count": {"type": "NUMBER", "description": "How many to list (default 8)."}},
            },
        },
        {
            "name": "playLatestEpisodes",
            "description": "Plays the newest podcast episodes across all subscriptions: the most recent one now, the next ones cued (or all cued when queueOnly=true). Use for 'play the newest episodes', 'catch me up on my podcasts', 'cue up what's new'.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "count": {"type": "NUMBER", "description": "How many episodes (default 5)."},
                    "queueOnly": {"type": "BOOLEAN", "description": "True to cue them all without interrupting playback."},
                },
            },
        },
        {
            "name": "setVolume",
            "description": "Sets or nudges the app's music volume slider (0–100). Use for 'turn it up', 'quieter', 'volume 40 percent', 'mute' (0), 'max volume' (100). Give `level` for an absolute value or `change` for a relative step (default +15 / -15).",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "level": {"type": "NUMBER", "description": "Absolute volume 0–100."},
                    "change": {"type": "NUMBER", "description": "Relative change in percent, e.g. 15 (louder) or -15 (quieter)."},
                },
            },
        },
        {
            "name": "setPlaybackSpeed",
            "description": "Sets the playback speed for podcasts and audiobooks. Use for 'play at 1.5x', 'speed it up' (one step faster), 'slow down', 'normal speed' (1). Has no effect on music or live radio.",
            "parameters": {
                "type": "OBJECT",
                "properties": {"rate": {"type": "NUMBER", "description": "Playback rate, 0.5 to 3. Omit to step one notch faster than now."}},
            },
        },
        {
            "name": "seekRelative",
            "description": "Skips forward or back inside the current podcast episode, audiobook chapter, streamed track or song. Use for 'skip ahead 30 seconds' (seconds=30), 'go back a minute' (seconds=-60), 'rewind 15 seconds' (seconds=-15). Not for live radio.",
            "parameters": {
                "type": "OBJECT",
                "properties": {"seconds": {"type": "NUMBER", "description": "Seconds to move; negative = backwards."}},
                "required": ["seconds"],
            },
        },
    ]

    if USE_SEARCH_TOOL:
        genres = sorted({t["genre"] for t in music_library if t.get("genre") and t["genre"] not in {"MP3", "WAV", "FLAC", "M4A", "AAC", "OGG", "WMA"}})
        artists = sorted({t["artist"] for t in music_library if t.get("artist") and t["artist"] != "Unknown Artist"})
        genre_summary = f"Available genres include: {', '.join(genres[:25])}." if genres else ""
        artist_summary = f"Artists include: {', '.join(artists[:25])}." if artists else ""

        system_instruction += (
            f"\nYour library has {len(music_library)} local tracks. {genre_summary} {artist_summary}\n"
            "PLAYBACK, QUEUE, AND PLAYLIST INSTRUCTIONS:\n"
            "1. PLAYLISTS & ERAS/GENRES/MOODS: When the user asks for music by decade, era, genre, mood, or mix (e.g. 'play some 80's music', 'put on some rock', 'make an upbeat playlist', 'play 90s hits', 'chill mix'), you MUST use `createAndPlayPlaylist`. This curates a multi-song playlist and immediately begins playing it. Do NOT use searchAndPlay for multi-song requests or genres/eras.\n"
            "2. EXISTING PLAYLISTS: When the user asks to play a specific saved playlist (e.g. 'play my Chill playlist'), use `playPlaylist`. To list saved playlists, use `listPlaylists`.\n"
            "3. CUE / QUEUE / NEXT: When the user asks to cue up, queue, line up, or play a song next (using keywords like 'queue', 'cue', 'next', 'play ... next', 'put ... on next', 'add ... to queue'), you MUST use `searchAndQueue`. Do NOT interrupt the currently playing track.\n"
            "4. PLAY SINGLE SONG: When the user asks to play a specific single song or artist track immediately ('play Billie Jean', 'put on Daft Punk'), use `searchAndPlay`. The tool will automatically pick the best track.\n"
            "5. SKIP / NEXT TRACK: If the user simply says 'skip', 'next', or 'next track' without specifying a song to queue, use `nextTrack`.\n"
            "6. PAUSE / RESUME: Use `pausePlayback` and `resumePlayback`.\n"
            "7. VOLUME: 'turn it up/down', 'louder', 'quieter', 'volume 40 percent', 'mute' -> you MUST call `setVolume`. "
            "You cannot change the volume any other way; never say you adjusted it unless the tool was called and confirmed it.\n"
            "Do NOT ask the user to choose; automatically pick the best matching track or playlist.\n"
        )
        system_instruction += PLAYLIST_MANAGEMENT_INSTRUCTIONS
        system_instruction += RADIO_PODCAST_INSTRUCTIONS + AUDIOBOOK_STREAMING_INSTRUCTIONS + _sources_summary() + "\n"
        tool_declarations.append({
            "name": "searchAndPlay",
            "description": "Searches the library for a genre, artist, or title, and IMMEDIATELY plays the best matching track.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "query": {
                        "type": "STRING",
                        "description": "The search term (e.g. 'Chill', 'Daft Punk', 'Pop')."
                    }
                },
                "required": ["query"]
            }
        })
        tool_declarations.append({
            "name": "searchAndQueue",
            "description": "Searches the library for a genre, artist, or title, and adds/cues the best matching track to the queue to play NEXT without interrupting current music.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "query": {
                        "type": "STRING",
                        "description": "The search term to find and cue up (e.g. 'Queen', 'Rock', 'Electronic')."
                    }
                },
                "required": ["query"]
            }
        })
    else:
        track_list_str = "\n".join(
            f'- ID {t["id"]}: "{t["title"]}" by {t["artist"]} ({t["genre"]})'
            for t in music_library
        )
        system_instruction += (
            f"\nHere is your current music library:\n{track_list_str}\n\n"
            "Never suggest playing a song that isn't in this library. "
            f"Track IDs are strings starting with 't_'.\n"
            "When the user asks for music by decade or genre, use `createAndPlayPlaylist`.\n"
            "When the user asks to cue or queue a song to play next (using keywords like 'queue', 'cue', 'next'), use `queueTrack`.\n"
            "For 'turn it up/down', 'louder', 'quieter', 'volume 40 percent', 'mute' you MUST call `setVolume`; never claim to have changed the volume otherwise."
        )
        system_instruction += PLAYLIST_MANAGEMENT_INSTRUCTIONS
        system_instruction += RADIO_PODCAST_INSTRUCTIONS + AUDIOBOOK_STREAMING_INSTRUCTIONS + _sources_summary() + "\n"

    return {
        "response_modalities": ["AUDIO"],
        "speech_config": {"voice_config": {"prebuilt_voice_config": {"voice_name": "Zephyr"}}},
        "system_instruction": system_instruction,
        "tools": [{"function_declarations": tool_declarations}],
    }


# ─── FastAPI App ──────────────────────────────────────────────────────────────

app = FastAPI(title="AI DJ Backend", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def cache_headers(request: Request, call_next):
    """Force browsers to revalidate static assets so stale app.js can never
    silently break the protocol after an update (revalidation still gets
    cheap 304s via ETag)."""
    response = await call_next(request)
    # Live relays mark themselves no-store (stronger); don't weaken that.
    if response.headers.get("cache-control") != "no-store":
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/")
async def serve_index():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/favicon.ico")
async def get_favicon():
    return Response(content=b"", media_type="image/x-icon")


def _scan_status() -> dict:
    return {
        "scanning": not scan_complete,
        "phase": scan_progress.get("phase"),
        "found": scan_progress.get("found", 0),
        "read": scan_progress.get("read", 0),
        "tracks": len(music_library) if scan_complete else None,
        "audiobooksScanning": not audiobooks_scan_complete,
        "audiobooks": len(audiobook_library) if audiobooks_scan_complete else None,
        "elapsed": round(time.time() - scan_progress["startedAt"], 1) if scan_progress.get("startedAt") and not scan_complete else None,
        "error": scan_progress.get("error"),
    }


@app.get("/api/status")
async def get_status():
    """Start-up progress for the browser's loading message."""
    return JSONResponse(_scan_status())


@app.get("/api/tracks")
async def get_tracks():
    if not scan_complete:
        return JSONResponse({**_scan_status(), "status": "scanning", "tracks": []}, status_code=503)
    return JSONResponse(music_library)


@app.post("/api/tracks/rescan")
async def rescan_tracks_endpoint():
    """Re-scan MUSIC_DIR (new files are read, unchanged ones come from the cache)."""
    if not scan_complete:
        return JSONResponse({"error": "A scan is already running"}, status_code=409)
    globals()["scan_complete"] = False
    try:
        n = await rescan_music_library()
    finally:
        globals()["scan_complete"] = True
    return JSONResponse({"count": n})


# ─── Playlists API ────────────────────────────────────────────────────────────

@app.get("/api/playlists")
async def get_playlists():
    return JSONResponse(pl_manager.get_all_playlists(tracks_by_id))


@app.get("/api/playlists/{playlist_id}")
async def get_playlist(playlist_id: str):
    pl = pl_manager.get_playlist_by_id(playlist_id, tracks_by_id)
    if not pl:
        return JSONResponse({"error": "Playlist not found"}, status_code=404)
    return JSONResponse(pl)


@app.post("/api/playlists")
async def create_playlist_endpoint(request: Request):
    try:
        data = await request.json()
    except Exception:
        data = {}
    name = str(data.get("name", "New Playlist")).strip() or "New Playlist"
    track_ids = data.get("trackIds", [])
    desc = str(data.get("description", "")).strip()
    created = pl_manager.create_playlist(name=name, track_ids=track_ids, description=desc)
    resolved = pl_manager.get_playlist_by_id(created["id"], tracks_by_id)
    return JSONResponse(resolved or created, status_code=201)


@app.put("/api/playlists/{playlist_id}")
async def update_playlist_endpoint(playlist_id: str, request: Request):
    try:
        data = await request.json()
    except Exception:
        data = {}
    updated = pl_manager.update_playlist(
        playlist_id,
        name=data.get("name"),
        track_ids=data.get("trackIds"),
        description=data.get("description")
    )
    if not updated:
        return JSONResponse({"error": "Playlist not found"}, status_code=404)
    resolved = pl_manager.get_playlist_by_id(playlist_id, tracks_by_id)
    return JSONResponse(resolved or updated)


@app.delete("/api/playlists/{playlist_id}")
async def delete_playlist_endpoint(playlist_id: str):
    success = pl_manager.delete_playlist(playlist_id)
    if not success:
        return JSONResponse({"error": "Playlist not found"}, status_code=404)
    return JSONResponse({"success": True})


@app.post("/api/playlists/{playlist_id}/tracks")
async def add_tracks_to_playlist_endpoint(playlist_id: str, request: Request):
    try:
        data = await request.json()
    except Exception:
        data = {}
    track_ids = data.get("trackIds", [])
    if isinstance(track_ids, str):
        track_ids = [track_ids]
    updated = pl_manager.add_tracks_to_playlist(playlist_id, track_ids)
    if not updated:
        return JSONResponse({"error": "Playlist not found"}, status_code=404)
    resolved = pl_manager.get_playlist_by_id(playlist_id, tracks_by_id)
    return JSONResponse(resolved or updated)


@app.delete("/api/playlists/{playlist_id}/tracks/{track_id}")
async def remove_track_from_playlist_endpoint(playlist_id: str, track_id: str):
    updated = pl_manager.remove_track_from_playlist(playlist_id, track_id)
    if not updated:
        return JSONResponse({"error": "Playlist not found"}, status_code=404)
    resolved = pl_manager.get_playlist_by_id(playlist_id, tracks_by_id)
    return JSONResponse(resolved or updated)



# ─── Live Radio API ───────────────────────────────────────────────────────────

RADIO_CHUNK_SIZE = 16 * 1024          # small relay chunks keep live latency low
UPSTREAM_CONNECT_TIMEOUT = 10.0


def _http_client(timeout: float = UPSTREAM_CONNECT_TIMEOUT) -> httpx.AsyncClient:
    """Outbound client for radio/podcast relays and directory lookups.
    Tests swap this for a client with a mock transport."""
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout, read=30.0),
        follow_redirects=True,
        headers={"User-Agent": radio_manager.USER_AGENT},
    )


def _directory_disabled() -> JSONResponse:
    return JSONResponse({"error": "Online directories are disabled (ONLINE_DIRECTORIES=false).", "results": []}, status_code=503)


async def _search_stations(query: str, country: str = "", place: str = "", limit: int = 12,
                           client: httpx.AsyncClient | None = None) -> list[dict]:
    """Both directories, merged: radio-browser (verified first) then TuneIn for
    whatever the community directory lacks (typically local commercial radio)."""
    own = client is None
    client = client or _http_client(timeout=6.0)
    try:
        primary = await radio_manager.search_directory(query, country=country, limit=limit, client=client, place=place)
        secondary = []
        if TUNEIN_DIRECTORY and query:
            tq = f"{query} {place}".strip() if place else query
            secondary = await radio_manager.search_tunein(tq, limit=limit, client=client)
            if not secondary and place:
                secondary = await radio_manager.search_tunein(query, limit=limit, client=client)
        return radio_manager.merge_directory_results(primary, secondary, limit)
    finally:
        if own:
            await client.aclose()


async def _prepare_station_for_saving(raw: dict, client: httpx.AsyncClient | None = None) -> dict:
    """TuneIn results point at a Tune.ashx link; resolve it to a direct stream
    (kept in `resolvedUrl`, the Tune link stays the stable id)."""
    st = dict(raw)
    url = str(st.get("streamUrl") or "")
    if radio_manager.is_tunein_url(url) and not st.get("resolvedUrl"):
        own = client is None
        client = client or _http_client(timeout=8.0)
        try:
            resolved = await radio_manager.resolve_tunein_url(url, client=client)
        finally:
            if own:
                await client.aclose()
        if resolved:
            st["resolvedUrl"] = resolved
    return st


@app.get("/api/radio")
async def list_radio_stations():
    return JSONResponse(radio_manager.get_all_stations())


@app.get("/api/radio/search")
async def search_radio_directory(q: str = "", country: str = "", place: str = ""):
    """Station search across both directories; `place` = city/region."""
    if not ONLINE_DIRECTORIES:
        return _directory_disabled()
    results = await _search_stations(q, country=country, place=place, limit=12)
    return JSONResponse({"results": results, "providers": ["radio-browser"] + (["tunein"] if TUNEIN_DIRECTORY else [])})


@app.get("/api/radio/browse")
async def browse_radio_directory(kind: str = "popular", value: str = ""):
    """Directory browsing without a query: kind=popular | tag&value=jazz | country&value=France."""
    if not ONLINE_DIRECTORIES:
        return _directory_disabled()
    async with _http_client(timeout=6.0) as client:
        results = await radio_manager.browse_directory(kind, value, limit=20, client=client)
    return JSONResponse({"results": results, "kind": kind, "value": value,
                         "genres": [{"label": l, "tag": t} for l, t in radio_manager.BROWSE_GENRES]})


@app.post("/api/radio")
async def add_radio_station_endpoint(request: Request):
    try:
        data = await request.json()
    except Exception:
        data = {}
    raw = data if isinstance(data, dict) else {}
    if re.match(r"^https?://", str(raw.get("streamUrl") or ""), re.IGNORECASE):
        raw = await _prepare_station_for_saving(raw)
    station, created = radio_manager.add_station(raw, source=str(raw.get("source") or "user"))
    if not station:
        return JSONResponse({"error": "A valid http(s) streamUrl is required"}, status_code=400)
    return JSONResponse(station, status_code=201 if created else 200)


@app.get("/api/radio/{station_id}")
async def get_radio_station(station_id: str):
    st = radio_manager.get_station_by_id(station_id)
    if not st:
        return JSONResponse({"error": "Station not found"}, status_code=404)
    return JSONResponse(st)


@app.put("/api/radio/{station_id}")
async def update_radio_station_endpoint(station_id: str, request: Request):
    try:
        data = await request.json()
    except Exception:
        data = {}
    updated = radio_manager.update_station(
        station_id,
        **{k: data.get(k) for k in ("name", "genre", "country", "homepage", "favicon", "description")},
    )
    if not updated:
        return JSONResponse({"error": "Station not found"}, status_code=404)
    return JSONResponse(updated)


@app.delete("/api/radio/{station_id}")
async def delete_radio_station_endpoint(station_id: str):
    if not radio_manager.delete_station(station_id):
        return JSONResponse({"error": "Station not found"}, status_code=404)
    radio_now_playing.pop(station_id, None)
    return JSONResponse({"success": True})


@app.get("/api/radio/{station_id}/now")
async def radio_now(station_id: str):
    """Latest ICY 'StreamTitle' seen while relaying this station (if any)."""
    info = radio_now_playing.get(station_id) or {}
    return JSONResponse({"stationId": station_id, "title": info.get("title"), "updatedAt": info.get("updatedAt")})


async def _open_upstream_stream(client: httpx.AsyncClient, url: str, headers: dict, max_hops: int = 3):
    """GET `url` as a stream. If the server hands back a .pls/.m3u playlist,
    follow it to the first real stream URL. Returns (response, error)."""
    for _ in range(max_hops):
        req = client.build_request("GET", url, headers=headers)
        resp = await client.send(req, stream=True)
        ctype = resp.headers.get("content-type", "")
        final_url = str(resp.url)
        if resp.status_code >= 400:
            await resp.aclose()
            return None, f"Upstream answered HTTP {resp.status_code}"
        if radio_manager.is_hls(ctype, final_url):
            await resp.aclose()
            return None, "HLS (.m3u8) streams are not supported"
        if radio_manager.is_playlist_response(ctype, final_url):
            body = bytearray()
            async for chunk in resp.aiter_bytes(8192):
                body += chunk
                if len(body) >= 64 * 1024:
                    break
            await resp.aclose()
            urls = radio_manager.extract_stream_urls(body.decode("utf-8", errors="replace"))
            if not urls:
                return None, "Playlist file contained no stream URL"
            url = urls[0]
            continue
        return resp, None
    return None, "Too many playlist redirections"


@app.get("/api/radio/{station_id}/stream")
async def stream_radio(station_id: str):
    """Same-origin relay of a live station. Needed because the browser routes
    <audio> through Web Audio (ducking/visualiser), which silences cross-origin
    media without CORS. ICY metadata is stripped and its StreamTitle kept in
    `radio_now_playing` for the GUI and the describeNowPlaying tool."""
    st = radio_manager.get_station_by_id(station_id)
    if not st:
        return JSONResponse({"error": "Station not found"}, status_code=404)

    client = _http_client()
    stream_url = st.get("resolvedUrl") or st["streamUrl"]
    if radio_manager.is_tunein_url(stream_url):
        # Saved before resolution succeeded (or a stale link): resolve now.
        resolved = await radio_manager.resolve_tunein_url(stream_url, client=client)
        if resolved:
            stream_url = resolved
            radio_manager.update_station(station_id, resolvedUrl=resolved)
        else:
            await client.aclose()
            return JSONResponse({"error": "TuneIn offers this station only as an HLS stream (or is unreachable), which cannot be relayed"}, status_code=415)
    try:
        resp, err = await _open_upstream_stream(client, stream_url, {"Icy-MetaData": "1", "Accept": "*/*"})
        if err and st.get("resolvedUrl") and radio_manager.is_tunein_url(st["streamUrl"]):
            # The direct URL may have rotated: ask TuneIn again once.
            fresh = await radio_manager.resolve_tunein_url(st["streamUrl"], client=client)
            if fresh and fresh != stream_url:
                radio_manager.update_station(station_id, resolvedUrl=fresh)
                resp, err = await _open_upstream_stream(client, fresh, {"Icy-MetaData": "1", "Accept": "*/*"})
    except httpx.RemoteProtocolError as e:
        await client.aclose()
        return JSONResponse({"error": f"Station answered with a legacy SHOUTcast/ICY response that cannot be relayed ({e})"}, status_code=502)
    except httpx.HTTPError as e:
        await client.aclose()
        return JSONResponse({"error": f"Could not connect to the station ({e.__class__.__name__})"}, status_code=502)
    if err:
        await client.aclose()
        return JSONResponse({"error": err}, status_code=415 if "HLS" in err else 502)

    media_type = (resp.headers.get("content-type") or "audio/mpeg").split(";")[0].strip() or "audio/mpeg"
    try:
        metaint = int(resp.headers.get("icy-metaint") or 0)
    except ValueError:
        metaint = 0
    parser = radio_manager.IcyMetadataParser(metaint) if metaint > 0 else None
    radio_now_playing[station_id] = {"title": None, "updatedAt": int(time.time())}
    radio_manager.mark_played(station_id)

    async def relay():
        try:
            async for chunk in resp.aiter_bytes(RADIO_CHUNK_SIZE):
                if parser is not None:
                    chunk = parser.feed(chunk)
                    new_title = parser.take_title_change()
                    if new_title is not None:
                        radio_now_playing[station_id] = {"title": new_title, "updatedAt": int(time.time())}
                if chunk:
                    yield chunk
        except httpx.HTTPError as e:
            print(f"[Radio] Relay for '{st['name']}' ended: {e.__class__.__name__}")
        finally:
            await resp.aclose()
            await client.aclose()

    return StreamingResponse(
        relay(), media_type=media_type,
        headers={"Cache-Control": "no-store", "Accept-Ranges": "none", "X-Accel-Buffering": "no"},
    )


# ─── Podcasts API ─────────────────────────────────────────────────────────────

@app.get("/api/podcasts")
async def list_podcasts():
    return JSONResponse(podcast_manager.get_all_podcasts())


@app.get("/api/podcasts/latest")
async def latest_podcast_episodes(limit: int = 20, per: int = 1):
    """Newest episodes across all subscriptions (the 'what's new' workflow)."""
    return JSONResponse({"items": podcast_manager.latest_episodes(limit=max(1, min(int(limit), 100)), per_podcast=max(1, min(int(per), 5)))})


@app.get("/api/podcasts/search")
async def search_podcast_directory(q: str = ""):
    if not ONLINE_DIRECTORIES:
        return _directory_disabled()
    async with _http_client(timeout=8.0) as client:
        results = await podcast_manager.search_directory(q, limit=10, client=client)
    return JSONResponse({"results": results})


@app.post("/api/podcasts")
async def subscribe_podcast_endpoint(request: Request):
    try:
        data = await request.json()
    except Exception:
        data = {}
    feed_url = str((data or {}).get("feedUrl", "")).strip()
    if not re.match(r"^https?://", feed_url, re.IGNORECASE):
        return JSONResponse({"error": "A valid http(s) feedUrl is required"}, status_code=400)
    try:
        async with _http_client(timeout=15.0) as client:
            parsed = await podcast_manager.fetch_feed(feed_url, client=client)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=502)
    podcast, created = podcast_manager.upsert_podcast(parsed)
    return JSONResponse(podcast, status_code=201 if created else 200)


@app.get("/api/podcasts/{podcast_id}")
async def get_podcast(podcast_id: str):
    pod = podcast_manager.get_podcast_by_id(podcast_id)
    if not pod:
        return JSONResponse({"error": "Podcast not found"}, status_code=404)
    return JSONResponse(pod)


@app.post("/api/podcasts/{podcast_id}/refresh")
async def refresh_podcast_endpoint(podcast_id: str):
    async with _http_client(timeout=15.0) as client:
        pod, err = await podcast_manager.refresh_podcast(podcast_id, client=client)
    if not pod:
        return JSONResponse({"error": err or "Podcast not found"}, status_code=404)
    if err:
        return JSONResponse({**pod, "refreshError": err}, status_code=200)
    return JSONResponse(pod)


@app.delete("/api/podcasts/{podcast_id}")
async def delete_podcast_endpoint(podcast_id: str):
    if not podcast_manager.delete_podcast(podcast_id):
        return JSONResponse({"error": "Podcast not found"}, status_code=404)
    return JSONResponse({"success": True})


@app.get("/api/podcasts/{podcast_id}/episodes/{episode_id}/stream")
async def stream_podcast_episode(podcast_id: str, episode_id: str,
                                 range_header: str | None = Header(None, alias="Range")):
    """Same-origin relay of an episode file, forwarding the browser's Range
    header so seeking and resume work exactly as with local tracks."""
    pod = podcast_manager.get_podcast_by_id(podcast_id)
    if not pod:
        return JSONResponse({"error": "Podcast not found"}, status_code=404)
    ep = next((e for e in pod.get("episodes", []) if e.get("id") == episode_id), None)
    if not ep:
        return JSONResponse({"error": "Episode not found"}, status_code=404)

    return await _relay_remote_file(ep["audioUrl"], range_header, label=ep["title"], tag="Podcasts",
                                    what="the episode", default_type=ep.get("mimeType") or "audio/mpeg")


async def _relay_remote_file(url: str, range_header: str | None, label: str, tag: str,
                             what: str = "the file", default_type: str = "audio/mpeg", extra_headers: dict | None = None):
    """Same-origin relay of a remote media file (podcast episode, streaming
    clip…), forwarding the browser's Range header so seeking works."""
    headers = {"Accept": "*/*", **(extra_headers or {})}
    if range_header:
        headers["Range"] = range_header
    client = _http_client(timeout=15.0)
    try:
        req = client.build_request("GET", url, headers=headers)
        resp = await client.send(req, stream=True)
    except httpx.HTTPError as e:
        await client.aclose()
        return JSONResponse({"error": f"Could not fetch {what} ({e.__class__.__name__})"}, status_code=502)
    if resp.status_code >= 400:
        status = resp.status_code
        await resp.aclose()
        await client.aclose()
        return JSONResponse({"error": f"{what[0].upper() + what[1:]} host answered HTTP {status}"}, status_code=502)

    media_type = (resp.headers.get("content-type") or default_type).split(";")[0].strip()
    passthrough = {k: resp.headers[k] for k in ("content-length", "content-range", "accept-ranges", "last-modified", "etag")
                   if k in resp.headers}
    passthrough.setdefault("accept-ranges", "bytes")

    async def relay():
        try:
            async for chunk in resp.aiter_bytes(STREAM_CHUNK_SIZE):
                yield chunk
        except httpx.HTTPError as e:
            print(f"[{tag}] Relay for '{label}' ended: {e.__class__.__name__}")
        finally:
            await resp.aclose()
            await client.aclose()

    return StreamingResponse(relay(), status_code=206 if resp.status_code == 206 else 200,
                             media_type=media_type, headers=passthrough)


def _parse_range(range_header: str | None, size: int) -> tuple[int, int] | None:
    """Parse 'bytes=start-end'. Returns (start, end) inclusive, or None."""
    if not range_header:
        return None
    m = re.match(r"bytes=(\d*)-(\d*)$", range_header.strip())
    if not m:
        return None
    start_s, end_s = m.groups()
    if start_s == "" and end_s == "":
        return None
    if start_s == "":
        # suffix range: last N bytes
        length = int(end_s)
        if length == 0:
            return None
        start = max(0, size - length)
        end = size - 1
    else:
        start = int(start_s)
        end = int(end_s) if end_s else size - 1
    if start >= size or end < start:
        return None
    return (start, min(end, size - 1))


LOCAL_MEDIA_TYPES = {
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".flac": "audio/flac",
    ".m4a": "audio/mp4", ".m4b": "audio/mp4", ".ogg": "audio/ogg", ".opus": "audio/ogg",
    ".aac": "audio/aac", ".wma": "audio/x-ms-wma",
}


@app.get("/api/tracks/{track_id}/stream")
async def stream_track(track_id: str, range_header: str | None = Header(None, alias="Range")):
    track = tracks_by_id.get(track_id)
    if not track:
        return JSONResponse({"error": "Track not found"}, status_code=404)
    path = MUSIC_DIR / track["filename"]
    if not path.exists():
        return JSONResponse({"error": "File not found on disk"}, status_code=404)
    return _stream_local_file(path, range_header)


def _stream_local_file(path: Path, range_header: str | None):
    """Local audio file with full HTTP Range support (seeking / resume)."""
    media_type = LOCAL_MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream")
    size = path.stat().st_size

    rng = _parse_range(range_header, size)
    if rng:
        start, end = rng

        def file_iter(start: int, end: int):
            with open(path, "rb") as f:
                f.seek(start)
                remaining = end - start + 1
                while remaining > 0:
                    chunk = f.read(min(STREAM_CHUNK_SIZE, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk

        return StreamingResponse(
            file_iter(start, end),
            status_code=206,
            media_type=media_type,
            headers={
                "Content-Range": f"bytes {start}-{end}/{size}",
                "Content-Length": str(end - start + 1),
                "Accept-Ranges": "bytes",
            },
        )

    # Full response — still advertise Accept-Ranges for future requests
    def full_iter():
        with open(path, "rb") as f:
            while True:
                chunk = f.read(STREAM_CHUNK_SIZE)
                if not chunk:
                    break
                yield chunk

    return StreamingResponse(
        full_iter(),
        media_type=media_type,
        headers={"Content-Length": str(size), "Accept-Ranges": "bytes"},
    )


@app.get("/api/tracks/{track_id}/art")
async def get_track_art(track_id: str):
    track = tracks_by_id.get(track_id)
    if not track:
        return JSONResponse({"error": "Track not found"}, status_code=404)

    if track_id in album_art_cache:
        album_art_cache.move_to_end(track_id)  # LRU refresh
        cached = album_art_cache[track_id]
        if cached is None:
            return JSONResponse({"error": "No artwork available"}, status_code=404)
        content, mime = cached
        return Response(content=content, media_type=mime)

    path = MUSIC_DIR / track["filename"]
    art = extract_local_art(path)
    if art:
        _cache_art(track_id, art)
        return Response(content=art[0], media_type=art[1])

    art = await fetch_itunes_art(track["artist"], track["title"])
    _cache_art(track_id, art)
    if art:
        return Response(content=art[0], media_type=art[1])

    return JSONResponse({"error": "No artwork available"}, status_code=404)


# ─── Audiobooks API ───────────────────────────────────────────────────────────

def _audiobook_or_404(book_id: str):
    book = audiobooks_by_id.get(book_id)
    if not book:
        return None, JSONResponse({"error": "Audiobook not found"}, status_code=404)
    return book, None


@app.get("/api/audiobooks")
async def list_audiobooks():
    """All books (summaries with progress) plus whether the feature is set up."""
    progress = audiobook_manager.load_progress()
    return JSONResponse({
        "configured": AUDIOBOOKS_DIR is not None,
        "directoryExists": bool(AUDIOBOOKS_DIR and AUDIOBOOKS_DIR.exists()),
        "scanning": not audiobooks_scan_complete,
        "books": [audiobook_manager.summarize(b, progress.get(b["id"])) for b in audiobook_library],
    })


@app.post("/api/audiobooks/rescan")
async def rescan_audiobooks_endpoint():
    if AUDIOBOOKS_DIR is None:
        return JSONResponse({"error": "AUDIOBOOKS_DIR is not set in backend/.env"}, status_code=503)
    n = await rescan_audiobooks()
    return JSONResponse({"count": n})


@app.get("/api/audiobooks/{book_id}")
async def get_audiobook(book_id: str):
    book, err = _audiobook_or_404(book_id)
    if err:
        return err
    return JSONResponse(audiobook_manager.with_chapters(book, audiobook_manager.get_progress(book_id)))


@app.put("/api/audiobooks/{book_id}/progress")
async def update_audiobook_progress(book_id: str, request: Request):
    """Browser reports where the listener is (chapter + seconds)."""
    book, err = _audiobook_or_404(book_id)
    if err:
        return err
    try:
        data = await request.json()
    except Exception:
        data = {}
    try:
        entry = audiobook_manager.update_progress(
            book, str(data.get("chapterId") or ""), float(data.get("position") or 0),
            float(data["duration"]) if data.get("duration") else None,
        )
    except (ValueError, TypeError) as e:
        return JSONResponse({"error": str(e) or "Invalid progress"}, status_code=400)
    return JSONResponse(entry)


@app.delete("/api/audiobooks/{book_id}/progress")
async def reset_audiobook_progress(book_id: str):
    book, err = _audiobook_or_404(book_id)
    if err:
        return err
    audiobook_manager.reset_progress(book_id)
    return JSONResponse({"success": True})


@app.get("/api/audiobooks/{book_id}/art")
async def get_audiobook_art(book_id: str):
    """Cover: cover.jpg/folder.jpg in the book folder, else art embedded in the first chapter."""
    book, err = _audiobook_or_404(book_id)
    if err:
        return err
    if book.get("cover"):
        path = AUDIOBOOKS_DIR / book["cover"]
        if path.exists():
            with open(path, "rb") as f:
                head = f.read(16)
            return FileResponse(path, media_type=_detect_image_mime(head))
    for ch in book.get("chapters") or []:
        art = extract_local_art(AUDIOBOOKS_DIR / ch["filename"])
        if art:
            return Response(content=art[0], media_type=art[1])
        break
    return JSONResponse({"error": "No cover available"}, status_code=404)


@app.get("/api/audiobooks/{book_id}/chapters/{chapter_id}/stream")
async def stream_audiobook_chapter(book_id: str, chapter_id: str, range_header: str | None = Header(None, alias="Range")):
    book, err = _audiobook_or_404(book_id)
    if err:
        return err
    ch = next((c for c in book.get("chapters") or [] if c["id"] == chapter_id), None)
    if not ch:
        return JSONResponse({"error": "Chapter not found"}, status_code=404)
    path = AUDIOBOOKS_DIR / ch["filename"]
    if not path.exists():
        return JSONResponse({"error": "File not found on disk"}, status_code=404)
    return _stream_local_file(path, range_header)


# ─── Streaming API ────────────────────────────────────────────────────────────
# Online streaming sources, one sub-section per provider in the GUI: Apple
# Music and Deezer previews (keyless) and YouTube Music full songs via the
# third-party tools ytmusicapi + yt-dlp. Audio is always relayed by the
# backend so it runs through the deck's Web Audio graph.

@app.get("/api/streaming/status")
async def streaming_status():
    return JSONResponse(streaming_manager.status())


@app.get("/api/streaming/search")
async def streaming_search(q: str = "", providers: str = "", limit: int = 10, offset: int = 0):
    """Search every (or the listed) providers in parallel: [{provider, tracks,
    albums, error, offset, limit, hasMore}]. `offset` pages one provider's songs."""
    wanted = [p.strip() for p in providers.split(",") if p.strip()] or None
    lim = max(1, min(int(limit), 50))
    async with _http_client(timeout=8.0) as client:
        results = await streaming_manager.search_all(q, client, limit=lim, providers=wanted, offset=max(0, int(offset)))
    return JSONResponse({"query": q, "results": results})


@app.get("/api/streaming/albums/{album_id}/tracks")
async def streaming_album_tracks(album_id: str):
    async with _http_client(timeout=8.0) as client:
        tracks = await streaming_manager.album_tracks(album_id, client)
    return JSONResponse({"tracks": tracks})


@app.get("/api/streaming/tracks/{track_id}")
async def streaming_track(track_id: str):
    t = streaming_manager.track_cache.get(track_id)
    if not t:
        return JSONResponse({"error": "Unknown track; search again."}, status_code=404)
    return JSONResponse(t)


@app.get("/api/streaming/tracks/{track_id}/audio")
async def streaming_audio(track_id: str, range_header: str | None = Header(None, alias="Range")):
    """Same-origin relay of a track's audio (preview clip, or the yt-dlp
    stream for YouTube Music) with Range pass-through for seeking."""
    t = streaming_manager.track_cache.get(track_id)
    if not t:
        return JSONResponse({"error": "Unknown track; search again."}, status_code=404)
    try:
        src = await streaming_manager.resolve_audio(t)
    except streaming_manager.StreamingError as e:
        return JSONResponse({"error": str(e)}, status_code=e.status)
    return await _relay_remote_file(src["url"], range_header, label=t["title"], tag="Streaming", what="the audio stream",
                                    default_type=src.get("mime") or "audio/mpeg", extra_headers=src.get("headers") or {})


app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


# ─── WebSocket Connection Wrapper ─────────────────────────────────────────────

class ClientConn:
    """Encapsulates per-connection state and safe sends."""

    def __init__(self, ws: WebSocket):
        self.ws = ws
        # Real playback state, reported by the browser; included in tool
        # responses so Gemini sees the truth instead of a fabricated Success.
        self.playback = {"playing": False, "track_id": None, "queue": [], "playlist": None, "source": None}
        self.audio_bytes_since_turn = 0
        # Directory stations tuned in this session (full records, so a later
        # "save this station" works even before the browser reports back).
        self.directory_stations: dict[str, dict] = {}

    async def send_json(self, obj) -> bool:
        try:
            await self.ws.send_text(json.dumps(obj))
            return True
        except Exception:
            return False

    async def send_state(self, state: str, detail: str | None = None) -> bool:
        msg = {"type": "ai_state", "state": state}
        if detail:
            msg["detail"] = detail
        return await self.send_json(msg)

    def now_playing(self) -> dict | None:
        """The library track the browser reports as loaded, or None (also None
        while a radio station or podcast episode is playing)."""
        if self.current_source():
            return None
        tid = self.playback.get("track_id")
        return tracks_by_id.get(tid) if tid else None

    def current_source(self) -> dict | None:
        """The non-library source the browser is playing: {"type": "radio"|"podcast", ...} or None."""
        src = self.playback.get("source")
        if isinstance(src, dict) and src.get("type") in SOURCE_TYPES:
            return src
        return None

    def radio_on_air_title(self) -> str | None:
        """Song title announced by the playing station (ICY metadata seen by the
        relay, else whatever the browser last reported)."""
        src = self.current_source()
        if not src or src.get("type") != "radio":
            return None
        info = radio_now_playing.get(src.get("id") or "") or {}
        return info.get("title") or src.get("nowTitle") or None

    def source_summary(self) -> str:
        """One-sentence truth about the radio/podcast source, for tool results."""
        src = self.current_source()
        if not src:
            return ""
        state = "playing" if self.playback.get("playing") else "paused"
        if src["type"] == "radio":
            text = f"Now {state}: live radio station '{src.get('name')}'"
            if src.get("genre"):
                text += f" ({src['genre']})"
            title = self.radio_on_air_title()
            text += f". On air right now: '{title}'." if title else ". The station has not announced the current song title."
            if not src.get("saved", True):
                text += " This station came from the online directory and is not saved yet."
            return text
        if src["type"] == "audiobook":
            text = f"Now {state}: audiobook '{src.get('name')}'"
            if src.get("author"):
                text += f" by {src['author']}"
            ch_idx, ch_total = src.get("chapterIndex"), src.get("chapterCount")
            if ch_idx:
                text += f", chapter {ch_idx}" + (f" of {ch_total}" if ch_total else "")
            if src.get("chapterTitle") and str(src["chapterTitle"]).lower() != str(src.get("name", "")).lower():
                text += f" ('{src['chapterTitle']}')"
        elif src["type"] == "stream":
            text = f"Now {state}: streamed track '{src.get('title')}' by {src.get('artist')}"
            if src.get("album"):
                text += f" from the album '{src['album']}'"
            if src.get("contextName") and src.get("contextType") in ("album", "artist"):
                text += f" (from {src['contextType']} '{src['contextName']}')"
            text += f" — {streaming_manager.provider_name(src.get('provider', ''))}, " + ("full song" if src.get("full") else "30-second preview clip")
        else:
            text = f"Now {state}: podcast episode '{src.get('episodeTitle')}' from '{src.get('name')}'"
        pos, dur = src.get("position"), src.get("duration")
        if isinstance(pos, (int, float)) and pos >= 0:
            text += f", at {_fmt_clock(pos)}"
            if isinstance(dur, (int, float)) and dur > 0:
                text += f" of {_fmt_clock(dur)}"
        rate = float(self.playback.get("playbackRate") or 1.0)
        if abs(rate - 1.0) > 0.01 and src["type"] in SEEKABLE_SOURCE_TYPES and src["type"] != "stream":
            text += f", at {rate:g}x speed"
        return text + "."

    def active_playlist_status(self) -> tuple[dict | None, bool]:
        """(active playlist as reported by the browser, is the current track one of its songs?).

        A playlist stays "active" while its remaining songs are still cued, but
        the user may have asked for a one-off song in the middle of it — then
        the current track is NOT from the playlist and must not be described
        as if it were."""
        active = self.playback.get("playlist")
        if not isinstance(active, dict) or not active.get("id"):
            return None, False
        tid = self.playback.get("track_id")
        if not tid:
            return active, False
        stored = pl_manager.get_playlist_by_id(active["id"])
        in_pl = bool(stored and tid in stored.get("trackIds", []))
        return active, in_pl

    def playback_summary(self) -> str:
        """Truthful deck state for tool results. The CURRENT TRACK comes first;
        the playlist is only context, and is flagged when the current track is
        a one-off that isn't part of it."""
        parts = []
        trk = self.now_playing()
        active, in_pl = self.active_playlist_status()
        state = "playing" if self.playback.get("playing") else "paused"

        if self.current_source():
            parts.append(self.source_summary())
            if active:
                parts.append(f"Playlist '{active.get('name')}' is on hold; its remaining songs are still cued.")
        elif trk:
            parts.append(f"Now {state}: '{trk['title']}' by {trk['artist']}.")
            if active:
                if in_pl:
                    parts.append(f"It is part of the active playlist '{active.get('name')}'.")
                else:
                    parts.append(f"It is a one-off request, NOT part of the active playlist '{active.get('name')}' (the playlist's remaining songs are still cued after it).")
        else:
            parts.append("Client reports: nothing loaded/playing.")
            if active:
                parts.append(f"Active playlist: '{active.get('name')}'.")

        queue = self.playback.get("queue") or []
        if queue:
            parts.append(f"Next in queue: {_queue_entry_label(queue[0])}.")
            if len(queue) > 1:
                parts.append(f"({len(queue)} items queued in total).")
        else:
            parts.append("Queue is currently empty.")
        return " ".join(parts)


# ─── Tool Handlers (registry-based dispatch) ─────────────────────────────────

async def _tool_play_track(conn: ClientConn, fc) -> dict:
    tid = str((fc.args or {}).get("trackId", "")).strip()
    trk = tracks_by_id.get(tid)
    if not trk:
        return {"result": f"Track '{tid}' not found in library"}
    await conn.send_json({"type": "command", "action": "playTrack", "trackId": tid})
    print(f"[Tool] Playing track {tid}")
    return {"result": f"Now playing: {trk['title']} by {trk['artist']}. {conn.playback_summary()}"}


async def _tool_queue_track(conn: ClientConn, fc) -> dict:
    tid = str((fc.args or {}).get("trackId", "")).strip()
    trk = tracks_by_id.get(tid)
    if not trk:
        return {"result": f"Track '{tid}' not found in library"}
    await conn.send_json({"type": "command", "action": "queueTrack", "trackId": tid, "track": trk})
    print(f"[Tool] Queued track {tid} ('{trk['title']}')")
    return {"result": f"Cued up: '{trk['title']}' by {trk['artist']} to play next. {conn.playback_summary()}"}


async def _tool_search_and_play(conn: ClientConn, fc) -> dict:
    if not scan_complete or not music_library:
        return {"result": "Library is still scanning. Ask the user to try again shortly."}
    raw_query = str((fc.args or {}).get("query", "")).strip()

    # Safety: if user requested to cue/queue or play next, redirect to queue handler
    if re.search(r"^(queue|cue)\b|\b(next|play\s*next|put\s*on\s*next)\b", raw_query, re.IGNORECASE):
        print(f"[Tool] SearchAndPlay query '{raw_query}' contains queue keyword -> redirecting to SearchAndQueue")
        return await _tool_search_and_queue(conn, fc)

    # Safety: if user requested an era/decade or playlist/mix, redirect to createAndPlayPlaylist
    if re.search(r"\b(playlist|mix|collection|era)\b|\b(80['’]?s|70['’]?s|90['’]?s|2000s|eighties|seventies|nineties)\b", raw_query, re.IGNORECASE):
        print(f"[Tool] SearchAndPlay query '{raw_query}' is for playlist/decade -> redirecting to createAndPlayPlaylist")
        return await _tool_create_and_play_playlist(conn, fc)

    clean_query = re.sub(r"\b(play|song|track)\b", "", raw_query, flags=re.IGNORECASE).strip()
    query = clean_query if clean_query else raw_query
    results = find_matching_tracks(query, music_library)
    if not results:
        print(f"[Tool] SearchAndPlay '{raw_query}' -> No results")
        return {"result": f"No tracks found matching '{raw_query}'."}
    import random
    trk = random.choice(results)
    await conn.send_json({"type": "command", "action": "playTrack", "trackId": trk["id"]})
    print(f"[Tool] SearchAndPlay '{raw_query}' -> Picked track {trk['id']} ('{trk['title']}')")
    return {"result": f"Successfully playing: '{trk['title']}' by {trk['artist']}. {conn.playback_summary()}"}


async def _tool_create_and_play_playlist(conn: ClientConn, fc) -> dict:
    if not scan_complete or not music_library:
        return {"result": "Library is still scanning. Ask the user to try again shortly."}
    args = fc.args or {}
    query = str(args.get("query", "")).strip()
    custom_name = str(args.get("playlistName", "")).strip() or None

    playlist, tracks = pl_manager.generate_dynamic_playlist(
        query=query,
        library=music_library,
        tracks_by_id=tracks_by_id,
        custom_name=custom_name,
        target_count=12
    )

    if not tracks:
        return {"result": f"Could not find enough tracks to curate a playlist for '{query}'."}

    first_track = tracks[0]
    await conn.send_json({
        "type": "command",
        "action": "loadAndPlayPlaylist",
        "playlist": playlist,
        "tracks": tracks,
        "startIndex": 0
    })

    print(f"[Tool] Created dynamic playlist '{playlist['name']}' with {len(tracks)} tracks for query '{query}'")
    return {
        "result": f"Curated and launched playlist '{playlist['name']}' with {len(tracks)} tracks. Started off with '{first_track['title']}' by {first_track['artist']}. {conn.playback_summary()}"
    }


# ── Playlist helpers shared by the voice tools ────────────────────────────────

# Spoken references to "whatever is playing now" rather than a real name/title.
# A reference must contain at least one genuinely deictic word and nothing but
# deictic/filler words — so "this song" and "the current playlist" qualify,
# while a track literally titled "One" or a playlist named "Mix" do not.
_DEICTIC_WORDS = {"this", "that", "current", "currently", "active", "playing", "now", "it"}
_TRACK_FILLER = {"the", "one", "song", "track", "tune", "is", "whats", "thats"}
_PLAYLIST_FILLER = {"the", "one", "playlist", "mix", "list", "is"}


def _is_current_ref(text: str, filler: set[str]) -> bool:
    # drop apostrophes so "that's" / "what's" become "thats" / "whats"
    tokens = re.findall(r"[a-z]+", re.sub(r"['’]", "", (text or "").lower()))
    if not tokens:
        return True
    return any(t in _DEICTIC_WORDS for t in tokens) and all(t in _DEICTIC_WORDS or t in filler for t in tokens)


def _is_current_track_ref(text: str) -> bool:
    """True for '', 'this', 'current', 'this song', 'the track playing now', …"""
    return _is_current_ref(text, _TRACK_FILLER)


def _is_current_playlist_ref(text: str) -> bool:
    """True for '', 'current', 'this playlist', 'the active mix' — but NOT for
    a playlist that is literally named 'Mix'."""
    return _is_current_ref(text, _PLAYLIST_FILLER)


def _playlist_label(pl: dict) -> str:
    n = len(pl.get("trackIds", []))
    return f"'{pl.get('name')}' ({n} track{'s' if n != 1 else ''})"


def _available_playlists_hint(limit: int = 6) -> str:
    names = [f"'{p['name']}'" for p in pl_manager.load_playlists()[:limit]]
    return f" Available playlists: {', '.join(names)}." if names else " There are no saved playlists yet."


def _resolve_playlist(conn: ClientConn, spoken_name: str) -> tuple[dict | None, str | None]:
    """Map a spoken playlist name to a stored playlist.

    'current' / 'this playlist' / '' -> the playlist the browser reports as
    active. Anything else goes through fuzzy name matching.
    Returns (raw_playlist_dict, error_message).
    """
    spoken_name = (spoken_name or "").strip()
    if _is_current_playlist_ref(spoken_name):
        active = conn.playback.get("playlist")
        active_id = active.get("id") if isinstance(active, dict) else None
        if not active_id:
            return None, "No playlist is currently playing, so 'this playlist' is ambiguous. Ask the user which playlist they mean." + _available_playlists_hint()
        pl = pl_manager.get_playlist_by_id(active_id)
        if not pl:
            return None, "The playlist that was playing no longer exists." + _available_playlists_hint()
        return pl, None

    pl = pl_manager.find_playlist_by_name(spoken_name)
    if not pl:
        return None, f"No playlist found matching '{spoken_name}'." + _available_playlists_hint()
    return pl, None


def _best_track_match(query: str, candidates: list[dict]) -> dict | None:
    """Deterministic best match for a spoken song reference: title hits beat
    artist hits, which beat album/genre hits (find_matching_tracks already
    narrows to the strongest tier)."""
    results = find_matching_tracks(query, candidates)
    if not results:
        return None
    q = query.lower().strip()

    def rank(t: dict) -> tuple:
        return (
            0 if q == t["title"].lower() else 1,
            0 if q in t["title"].lower() else 1,
            0 if q in t["artist"].lower() else 1,
        )

    return sorted(results, key=rank)[0]


def _resolve_track(
    conn: ClientConn, args: dict,
    candidates: list[dict] | None = None, scope: str = "the library",
) -> tuple[dict | None, str | None]:
    """Resolve the song a user is talking about.

    Priority: explicit trackId -> 'this song' / empty -> now playing ->
    search by title/artist inside `candidates` (default: whole library).
    Returns (track_dict, error_message).
    """
    candidates = candidates if candidates is not None else music_library
    track_id = str(args.get("trackId") or "").strip()
    query = str(args.get("trackQuery") or "").strip()

    if track_id:
        trk = tracks_by_id.get(track_id)
        if not trk:
            return None, f"Track '{track_id}' not found in {scope}."
        return trk, None

    if _is_current_track_ref(query):
        src = conn.current_source()
        if src:
            what = SOURCE_NOUNS.get(src["type"], "a non-library source")
            return None, f"{what[0].upper() + what[1:]} is playing, not a library song, so 'this song' cannot be added to a playlist. Ask the user for the song title."
        now_id = conn.playback.get("track_id")
        trk = tracks_by_id.get(now_id) if now_id else None
        if not trk:
            return None, "Nothing is playing right now, so I don't know which song you mean. Ask the user for the song title."
        return trk, None

    clean = re.sub(r"\b(the song|song|track|tune|by)\b", " ", query, flags=re.IGNORECASE)
    clean = re.sub(r"\s+", " ", clean).strip() or query
    trk = _best_track_match(clean, candidates)
    if not trk:
        return None, f"No track found matching '{query}' in {scope}."
    return trk, None


def _clean_new_name(raw: str, fallback: str = "") -> str:
    """Trim quotes/filler from a spoken playlist name: "called 'Workout'" -> "Workout"."""
    name = (raw or "").strip()
    name = re.sub(r"^(?:called|named|name it|call it)\s+", "", name, flags=re.IGNORECASE)
    name = name.strip().strip("\"'“”‘’").strip()
    name = re.sub(r"\s+", " ", name)
    return name[:80] if name else fallback


def _name_from_spoken_reference(spoken: str) -> str:
    """Turn a loose reference into a presentable *new* playlist name when we
    auto-create on addToPlaylist: "my workout playlist" -> "Workout".
    Explicit create/rename names are NOT passed through this (a user may
    genuinely want a playlist called "Summer Mix")."""
    name = _clean_new_name(spoken)
    name = re.sub(r"^(?:my|the|a|an|our)\s+", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s+(?:playlist|playlists|mix|list)$", "", name, flags=re.IGNORECASE).strip()
    if not name:
        name = _clean_new_name(spoken, fallback="New Playlist")
    if name == name.lower():          # spoken text is usually lowercase
        name = name.title()
    return name


async def _notify_playlists_changed(conn: ClientConn, change: str, playlist: dict | None, message: str, set_active: bool = False):
    """Tell the browser a playlist was created/updated/renamed/deleted so it can
    refresh its Playlists tab, detail view, active-playlist badge and toast.
    `set_active` makes the browser treat `playlist` as the one now playing."""
    summary = None
    if playlist:
        summary = {
            "id": playlist.get("id"),
            "name": playlist.get("name"),
            "trackCount": len(playlist.get("trackIds", [])),
            "isDynamic": playlist.get("isDynamic", False),
        }
    await conn.send_json({
        "type": "command",
        "action": "playlistsChanged",
        "change": change,
        "playlistId": playlist.get("id") if playlist else None,
        "playlist": summary,
        "message": message,
        "setActive": bool(set_active and playlist),
    })


def _resolved_tracks(pl: dict) -> list[dict]:
    return [tracks_by_id[tid] for tid in pl.get("trackIds", []) if tid in tracks_by_id]


# ── Playlist voice tools ──────────────────────────────────────────────────────

async def _tool_play_playlist(conn: ClientConn, fc) -> dict:
    args = fc.args or {}
    name = str(args.get("name", "")).strip()
    shuffle = bool(args.get("shuffle", False))
    if not pl_manager.load_playlists():
        return {"result": "No saved playlists found. Ask AI DJ to make one (e.g. 'play some 80's music')!"}

    matched_pl, err = _resolve_playlist(conn, name)
    if err:
        return {"result": err}

    tracks = _resolved_tracks(matched_pl)
    if not tracks:
        return {"result": f"Playlist '{matched_pl['name']}' has no playable tracks in library."}
    if shuffle:
        import random
        tracks = random.sample(tracks, len(tracks))

    await conn.send_json({
        "type": "command",
        "action": "loadAndPlayPlaylist",
        "playlist": matched_pl,
        "tracks": tracks,
        "startIndex": 0
    })
    print(f"[Tool] Playing existing playlist '{matched_pl['name']}' with {len(tracks)} tracks{' (shuffled)' if shuffle else ''}")
    return {
        "result": f"Loaded playlist '{matched_pl['name']}' with {len(tracks)} tracks{' in shuffle order' if shuffle else ''}. Playing '{tracks[0]['title']}' by {tracks[0]['artist']}."
    }


async def _tool_queue_playlist(conn: ClientConn, fc) -> dict:
    args = fc.args or {}
    matched_pl, err = _resolve_playlist(conn, str(args.get("playlistName", "")))
    if err:
        return {"result": err}
    tracks = _resolved_tracks(matched_pl)
    if not tracks:
        return {"result": f"Playlist '{matched_pl['name']}' has no playable tracks in library."}
    if bool(args.get("shuffle", False)):
        import random
        tracks = random.sample(tracks, len(tracks))

    await conn.send_json({
        "type": "command",
        "action": "queuePlaylist",
        "playlist": {"id": matched_pl["id"], "name": matched_pl["name"]},
        "tracks": tracks,
    })
    print(f"[Tool] Queued playlist '{matched_pl['name']}' ({len(tracks)} tracks)")
    return {
        "result": f"Added all {len(tracks)} tracks of '{matched_pl['name']}' to the end of the queue, starting with '{tracks[0]['title']}' by {tracks[0]['artist']}. {conn.playback_summary()}"
    }


async def _tool_list_playlists(conn: ClientConn, fc) -> dict:
    all_pls = pl_manager.get_all_playlists(tracks_by_id)
    if not all_pls:
        return {"result": "There are no saved playlists yet. Ask AI DJ to create one (like 'play some 80's music')!"}
    names = [f"'{p['name']}' ({p['trackCount']} tracks{', AI mix' if p.get('isDynamic') else ''})" for p in all_pls[:12]]
    more = f" …and {len(all_pls) - 12} more." if len(all_pls) > 12 else ""
    return {"result": f"{len(all_pls)} saved playlists: {', '.join(names)}.{more}"}


async def _tool_describe_playlist(conn: ClientConn, fc) -> dict:
    args = fc.args or {}
    pl, err = _resolve_playlist(conn, str(args.get("playlistName", "")))
    if err:
        return {"result": err}
    tracks = _resolved_tracks(pl)
    if not tracks:
        return {"result": f"Playlist '{pl['name']}' is empty."}
    listing = "; ".join(f"{i + 1}. '{t['title']}' by {t['artist']}" for i, t in enumerate(tracks[:15]))
    more = f"; …and {len(tracks) - 15} more" if len(tracks) > 15 else ""
    kind = "AI-curated mix" if pl.get("isDynamic") else "custom playlist"
    desc = f" ({pl['description']})" if pl.get("description") else ""
    return {"result": f"'{pl['name']}' is a {kind}{desc} with {len(tracks)} tracks, in order: {listing}{more}. This listing is authoritative."}


async def _tool_create_playlist(conn: ClientConn, fc) -> dict:
    args = fc.args or {}
    name = _clean_new_name(str(args.get("name", "")))
    if not name:
        return {"result": "I need a name for the new playlist. Ask the user what to call it."}

    existing = pl_manager.load_playlists()
    dup = next((p for p in existing if pl_manager.normalize_playlist_name(p.get("name", "")) == pl_manager.normalize_playlist_name(name)), None)
    if dup:
        return {"result": f"A playlist named {_playlist_label(dup)} already exists. Use addToPlaylist to add songs to it, or ask the user for a different name."}

    track_ids: list[str] = []
    seeded = None
    if bool(args.get("includeCurrentTrack", False)):
        now_id = conn.playback.get("track_id")
        seeded = tracks_by_id.get(now_id) if now_id else None
        if seeded:
            track_ids.append(seeded["id"])

    created = pl_manager.create_playlist(name=name, track_ids=track_ids, description="Created by voice")
    msg = f"Created playlist '{created['name']}'" + (f" with '{seeded['title']}' by {seeded['artist']}" if seeded else " (empty)") + "."
    await _notify_playlists_changed(conn, "created", created, msg)
    print(f"[Tool] {msg}")
    return {"result": msg + " Playback was not changed."}


async def _tool_add_to_playlist(conn: ClientConn, fc) -> dict:
    args = fc.args or {}
    trk, err = _resolve_track(conn, args)
    if err:
        return {"result": err}

    spoken = str(args.get("playlistName", "")).strip()
    pl, err = _resolve_playlist(conn, spoken)
    created = False
    if err:
        # Only auto-create for a real name, never for 'this playlist'.
        if _is_current_playlist_ref(spoken):
            return {"result": err}
        pl = pl_manager.create_playlist(name=_name_from_spoken_reference(spoken), track_ids=[], description="Created by voice")
        created = True

    if trk["id"] in pl.get("trackIds", []):
        return {"result": f"'{trk['title']}' by {trk['artist']} is already in {_playlist_label(pl)}. No change made."}

    updated = pl_manager.add_tracks_to_playlist(pl["id"], [trk["id"]])
    if not updated:
        return {"result": f"Could not update playlist '{pl['name']}'."}

    msg = (f"Created playlist '{updated['name']}' and added" if created else f"Added") + \
          f" '{trk['title']}' by {trk['artist']} to {_playlist_label(updated)}."
    await _notify_playlists_changed(conn, "created" if created else "updated", updated, msg)
    print(f"[Tool] {msg}")
    return {"result": msg}


async def _tool_remove_from_playlist(conn: ClientConn, fc) -> dict:
    args = fc.args or {}
    pl, err = _resolve_playlist(conn, str(args.get("playlistName", "")))
    if err:
        return {"result": err}
    members = _resolved_tracks(pl)
    if not members:
        return {"result": f"Playlist '{pl['name']}' is already empty."}

    # Search only inside the playlist so "remove Thriller" can't pick a
    # different library track that isn't in it.
    trk, err = _resolve_track(conn, args, candidates=members, scope=f"playlist '{pl['name']}'")
    if err:
        return {"result": err}
    if trk["id"] not in pl.get("trackIds", []):
        return {"result": f"'{trk['title']}' by {trk['artist']} is not in playlist '{pl['name']}'. No change made."}

    updated = pl_manager.remove_track_from_playlist(pl["id"], trk["id"])
    if not updated:
        return {"result": f"Could not update playlist '{pl['name']}'."}
    msg = f"Removed '{trk['title']}' by {trk['artist']} from {_playlist_label(updated)}."
    await _notify_playlists_changed(conn, "updated", updated, msg)
    print(f"[Tool] {msg}")
    return {"result": msg}


async def _tool_rename_playlist(conn: ClientConn, fc) -> dict:
    args = fc.args or {}
    new_name = _clean_new_name(str(args.get("newName", "")))
    if not new_name:
        return {"result": "I need the new name. Ask the user what to rename the playlist to."}
    pl, err = _resolve_playlist(conn, str(args.get("playlistName", "")))
    if err:
        return {"result": err}
    old_name = pl["name"]
    if old_name == new_name:
        return {"result": f"The playlist is already called '{old_name}'."}
    updated = pl_manager.update_playlist(pl["id"], name=new_name)
    if not updated:
        return {"result": f"Could not rename playlist '{old_name}'."}
    msg = f"Renamed playlist '{old_name}' to '{updated['name']}'."
    await _notify_playlists_changed(conn, "renamed", updated, msg)
    print(f"[Tool] {msg}")
    return {"result": msg}


async def _tool_delete_playlist(conn: ClientConn, fc) -> dict:
    args = fc.args or {}
    pl, err = _resolve_playlist(conn, str(args.get("playlistName", "")))
    if err:
        return {"result": err}
    if not bool(args.get("confirmed", False)):
        return {
            "result": f"CONFIRMATION REQUIRED: deleting {_playlist_label(pl)} cannot be undone. "
                      "Ask the user to confirm, and only if they say yes call deletePlaylist again with confirmed=true."
        }
    name = pl["name"]
    if not pl_manager.delete_playlist(pl["id"]):
        return {"result": f"Could not delete playlist '{name}'."}
    msg = f"Deleted playlist '{name}'."
    await _notify_playlists_changed(conn, "deleted", pl, msg)
    print(f"[Tool] {msg}")
    return {"result": msg}


async def _tool_save_queue_as_playlist(conn: ClientConn, fc) -> dict:
    args = fc.args or {}
    include_current = args.get("includeCurrentTrack", True)
    include_current = True if include_current is None else bool(include_current)

    track_ids: list[str] = []
    if include_current and conn.playback.get("track_id") in tracks_by_id:
        track_ids.append(conn.playback["track_id"])
    skipped: dict[str, int] = {}
    for item in conn.playback.get("queue") or []:
        tid = item if isinstance(item, str) else (item or {}).get("id")
        if tid in tracks_by_id:
            if tid not in track_ids:
                track_ids.append(tid)
        elif tid:
            # podcast episodes, audiobook chapters, streamed tracks cannot live in a local playlist
            kind = str((item if isinstance(item, dict) else {}).get("kind") or "other")
            skipped[kind] = skipped.get(kind, 0) + 1
    if not track_ids:
        return {"result": "Nothing is playing and the queue has no library songs, so there is nothing to save as a playlist. Ask the user to cue up some songs first."}

    default_name = f"Session Mix ({time.strftime('%b %d, %H:%M')})"
    name = _clean_new_name(str(args.get("name", "")), fallback=default_name)
    created = pl_manager.create_playlist(
        name=name, track_ids=track_ids,
        description=f"Saved from the live queue by voice ({len(track_ids)} tracks)"
    )
    first = tracks_by_id[track_ids[0]]
    msg = f"Saved {len(track_ids)} tracks as playlist '{created['name']}', starting with '{first['title']}' by {first['artist']}."

    # What's playing *is* this playlist now: make it the active one on both
    # sides immediately, so a follow-up "what's in this playlist?" describes
    # the saved session rather than whatever mix was loaded before it.
    conn.playback["playlist"] = {"id": created["id"], "name": created["name"]}
    await _notify_playlists_changed(conn, "created", created, msg, set_active=True)
    print(f"[Tool] {msg}")

    listing = "; ".join(f"{i + 1}. '{tracks_by_id[tid]['title']}' by {tracks_by_id[tid]['artist']}" for i, tid in enumerate(track_ids[:15]))
    more = f"; …and {len(track_ids) - 15} more" if len(track_ids) > 15 else ""
    skipped_note = f" ({_skipped_kinds_label(skipped)} left out — playlists hold local songs only.)" if skipped else ""
    return {"result": f"{msg} It is now the active playlist. Its tracks, in order: {listing}{more}.{skipped_note}"}


_SKIPPED_KIND_NOUNS = {"podcast": ("podcast episode", "podcast episodes"), "audiobook": ("audiobook chapter", "audiobook chapters"),
                       "stream": ("streamed track", "streamed tracks")}


def _skipped_kinds_label(skipped: dict[str, int]) -> str:
    parts = []
    for kind, n in skipped.items():
        one, many = _SKIPPED_KIND_NOUNS.get(kind, ("non-library item", "non-library items"))
        parts.append(f"{n} cued {one if n == 1 else many}")
    return " and ".join(parts)


async def _tool_search_and_queue(conn: ClientConn, fc) -> dict:
    if not scan_complete or not music_library:
        return {"result": "Library is still scanning. Ask the user to try again shortly."}
    raw_query = str((fc.args or {}).get("query", "")).strip()
    clean_query = re.sub(r"\b(queue\s*(up)?|cue\s*(up)?|next|play\s*next|put\s*on\s*next|add\s*to\s*queue)\b", "", raw_query, flags=re.IGNORECASE).strip()
    query = clean_query if clean_query else raw_query
    results = find_matching_tracks(query, music_library)
    if not results:
        print(f"[Tool] SearchAndQueue '{raw_query}' -> No results")
        return {"result": f"No tracks found matching '{raw_query}' to cue up."}
    import random
    trk = random.choice(results)
    await conn.send_json({"type": "command", "action": "queueTrack", "trackId": trk["id"], "track": trk})
    print(f"[Tool] SearchAndQueue '{raw_query}' -> Queued track {trk['id']} ('{trk['title']}')")
    return {"result": f"Successfully cued up: '{trk['title']}' by {trk['artist']} to play next. {conn.playback_summary()}"}


async def _tool_pause(conn: ClientConn, fc) -> dict:
    await conn.send_json({"type": "command", "action": "pausePlayback"})
    return {"result": f"Pause command sent. {conn.playback_summary()}"}


async def _tool_resume(conn: ClientConn, fc) -> dict:
    await conn.send_json({"type": "command", "action": "resumePlayback"})
    return {"result": f"Resume command sent. {conn.playback_summary()}"}


async def _tool_next(conn: ClientConn, fc) -> dict:
    await conn.send_json({"type": "command", "action": "nextTrack"})
    return {"result": f"Skip command sent. {conn.playback_summary()}"}


async def _tool_prev(conn: ClientConn, fc) -> dict:
    await conn.send_json({"type": "command", "action": "previousTrack"})
    return {"result": f"Previous command sent. {conn.playback_summary()}"}


# ── Queue voice tools ─────────────────────────────────────────────────────────

def _queue_entries(conn: ClientConn) -> list[dict]:
    """The browser-reported queue as uniform entries, in order. Library tracks
    resolve to their catalogue data; other kinds (podcast episodes cued from
    the GUI) keep what the browser reported: {id, title, artist, kind}."""
    out = []
    for item in conn.playback.get("queue") or []:
        if isinstance(item, str):
            item = {"id": item}
        item = item or {}
        tid = item.get("id")
        trk = tracks_by_id.get(tid)
        if trk:
            out.append({**trk, "kind": "track"})
        elif tid:
            out.append({"id": tid, "title": str(item.get("title") or tid), "artist": str(item.get("artist") or ""),
                        "album": "", "genre": str(item.get("kind") or ""), "kind": str(item.get("kind") or "unknown")})
    return out


def _queue_entry_label(item) -> str:
    """Spoken label for one queue entry: a song, or e.g. a podcast episode."""
    entries = _queue_entries_from([item])
    if not entries:
        return "an unknown item"
    e = entries[0]
    if e["kind"] == "track":
        return f"'{e['title']}' by {e['artist']}"
    if e["kind"] == "podcast":
        return f"podcast episode '{e['title']}'" + (f" from '{e['artist']}'" if e.get("artist") else "")
    if e["kind"] == "audiobook":
        return f"audiobook chapter '{e['title']}'" + (f" from '{e['artist']}'" if e.get("artist") else "")
    if e["kind"] == "stream":
        return f"streamed track '{e['title']}'" + (f" by {e['artist']}" if e.get("artist") else "")
    return f"'{e['title']}'"


def _queue_entries_from(items: list) -> list[dict]:
    fake = ClientConn.__new__(ClientConn)
    fake.playback = {"queue": items}
    return _queue_entries(fake)


def _queue_tracks(conn: ClientConn) -> list[dict]:
    """Only the library tracks in the queue (what playlists can hold)."""
    return [e for e in _queue_entries(conn) if e["kind"] == "track"]


async def _tool_clear_queue(conn: ClientConn, fc) -> dict:
    n = len(conn.playback.get("queue") or [])
    await conn.send_json({"type": "command", "action": "clearQueue"})
    conn.playback["queue"] = []   # don't wait for the browser's round trip
    print(f"[Tool] Cleared queue ({n} tracks)")
    if n == 0:
        return {"result": "The queue was already empty. The current track keeps playing."}
    return {"result": f"Cleared the queue: {n} cued track{'s' if n != 1 else ''} removed. The current track keeps playing. {conn.playback_summary()}"}


async def _tool_remove_from_queue(conn: ClientConn, fc) -> dict:
    args = fc.args or {}
    queued = _queue_entries(conn)
    if not queued:
        return {"result": "The queue is empty, there is nothing to remove."}

    query = str(args.get("trackQuery") or "").strip()
    track_id = str(args.get("trackId") or "").strip()
    if track_id:
        trk = next((t for t in queued if t["id"] == track_id), None)
        if not trk:
            return {"result": f"Track '{track_id}' is not in the queue."}
    elif not query or re.fullmatch(r"(?:the|next|up next|song|track|one|\s)+", query, re.IGNORECASE):
        trk = queued[0]
    else:
        clean = re.sub(r"\b(the song|song|track|tune|by|from the queue|from queue)\b", " ", query, flags=re.IGNORECASE)
        clean = re.sub(r"\s+", " ", clean).strip() or query
        trk = _best_track_match(clean, queued)
        if not trk:
            names = ", ".join(f"'{t['title']}'" for t in queued[:8])
            return {"result": f"No cued track matches '{query}'. Queue: {names}."}

    await conn.send_json({"type": "command", "action": "removeFromQueue", "trackId": trk["id"]})
    # Mirror the change locally so the same-turn playback summary is right.
    q = conn.playback.get("queue") or []
    for i, item in enumerate(q):
        tid = item if isinstance(item, str) else (item or {}).get("id")
        if tid == trk["id"]:
            del q[i]
            break
    print(f"[Tool] Removed '{trk['title']}' from queue")
    return {"result": f"Removed {_queue_entry_label(trk)} from the queue. {conn.playback_summary()}"}


async def _tool_describe_queue(conn: ClientConn, fc) -> dict:
    queued = _queue_entries(conn)
    now = conn.now_playing()
    if conn.current_source():
        now_part = conn.source_summary() + " "
    else:
        now_part = f"Now {'playing' if conn.playback.get('playing') else 'paused'}: '{now['title']}' by {now['artist']}. " if now else "Nothing is playing. "
    if not queued:
        return {"result": now_part + "The queue is empty; after this track the library continues in order."}
    listing = "; ".join(f"{i + 1}. {_queue_entry_label(t)}" for i, t in enumerate(queued[:15]))
    more = f"; …and {len(queued) - 15} more" if len(queued) > 15 else ""
    pl = conn.playback.get("playlist")
    pl_part = f" (from playlist '{pl.get('name')}')" if isinstance(pl, dict) and pl.get("name") else ""
    return {"result": f"{now_part}{len(queued)} item{'s' if len(queued) != 1 else ''} cued up{pl_part}, in order: {listing}{more}."}


async def _tool_describe_now_playing(conn: ClientConn, fc) -> dict:
    src = conn.current_source()
    if src:
        desc = conn.source_summary()
        if src["type"] == "radio":
            desc += " Describe the station and the on-air title; there is no local track, album or playlist to describe."
        elif src["type"] == "podcast":
            ep_desc = str(src.get("episodeDescription") or "").strip()
            if ep_desc:
                desc += f" Episode notes: {ep_desc[:240]}"
        elif src["type"] == "audiobook":
            book = audiobooks_by_id.get(src.get("id") or "")
            if book:
                desc += f" Book progress: {audiobook_manager.progress_label(book, audiobook_manager.get_progress(book['id']))}."
                if book.get("narrator"):
                    desc += f" Narrated by {book['narrator']}."
        elif src["type"] == "stream":
            desc += " This is streamed online, not from the local library, so it cannot be added to a local playlist."
        queue = conn.playback.get("queue") or []
        if queue:
            desc += f" Up next in the queue: {_queue_entry_label(queue[0])}."
        return {"result": desc}

    trk = conn.now_playing()
    active, in_pl = conn.active_playlist_status()
    if not trk:
        extra = f" The active playlist is '{active.get('name')}' but nothing is loaded." if active else ""
        return {"result": "Nothing is playing right now." + extra}
    state = "playing" if conn.playback.get("playing") else "paused"
    bits = [f"Now {state}: '{trk['title']}' by {trk['artist']}"]
    if trk.get("album"):
        bits.append(f"from the album '{trk['album']}'")
    if trk.get("genre") and trk["genre"].upper() not in {"MP3", "WAV", "FLAC", "M4A", "AAC", "OGG", "WMA"}:
        bits.append(f"genre {trk['genre']}")
    desc = ", ".join(bits) + "."
    if active and in_pl:
        desc += f" This song is from the active playlist '{active.get('name')}'."
    elif active:
        desc += f" This song is a one-off request and is NOT part of the active playlist '{active.get('name')}'; that playlist's remaining songs are still cued after it."
    queue = conn.playback.get("queue") or []
    if queue:
        desc += f" Up next: {_queue_entry_label(queue[0])}."
    return {"result": desc}


# ── Live radio & podcast voice tools ──────────────────────────────────────────

_RADIO_REF_FILLER = {"the", "a", "some", "station", "radio", "channel", "stream", "one", "on", "again", "back"}
_PODCAST_REF_FILLER = {"the", "a", "my", "podcast", "show", "one", "episode", "again"}


def _is_generic_radio_request(query: str) -> bool:
    """'', 'the radio', 'some radio', 'this station again' -> no particular
    station was named; tune in to the default (last played) one."""
    tokens = re.findall(r"[a-z0-9]+", re.sub(r"['’]", "", (query or "").lower()))
    return all(t in _RADIO_REF_FILLER or t in _DEICTIC_WORDS or t in radio_manager.COMMON_FILLER for t in tokens)


def _fmt_clock(seconds) -> str:
    """73 -> '1:13', 3723 -> '1:02:03'."""
    total = max(0, int(seconds or 0))
    h, rem = divmod(total, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def _station_label(st: dict) -> str:
    extra = ", ".join(x for x in (st.get("genre"), st.get("country")) if x)
    return f"'{st.get('name')}'" + (f" ({extra})" if extra else "")


def _station_summary(st: dict) -> dict:
    return {k: st.get(k) for k in ("id", "name", "streamUrl", "genre", "country", "homepage", "favicon", "description", "source")}


def _podcast_summary(pod: dict) -> dict:
    return podcast_manager.summarize(pod)


def _available_stations_hint(limit: int = 6) -> str:
    names = [f"'{s['name']}'" for s in radio_manager.load_stations()[:limit]]
    return f" Saved stations: {', '.join(names)}." if names else " There are no saved stations yet."


def _available_podcasts_hint(limit: int = 6) -> str:
    names = [f"'{p['title']}'" for p in podcast_manager.load_podcasts()[:limit]]
    return f" Subscriptions: {', '.join(names)}." if names else " There are no podcast subscriptions yet."


async def _notify_sources_changed(conn: ClientConn, kind: str, change: str, item: dict | None, message: str):
    """Tell the browser a station/podcast was added/updated/removed so the
    Radio / Podcasts tabs refresh and a toast is shown."""
    if kind == "radio":
        await conn.send_json({
            "type": "command", "action": "radioStationsChanged", "change": change,
            "stationId": item.get("id") if item else None,
            "station": _station_summary(item) if item else None, "message": message,
        })
    else:
        await conn.send_json({
            "type": "command", "action": "podcastsChanged", "change": change,
            "podcastId": item.get("id") if item else None,
            "podcast": _podcast_summary(item) if item else None, "message": message,
        })


async def _send_play_radio(conn: ClientConn, station: dict, origin: str):
    """Push a playRadio command and mirror the new source locally so the
    same-turn playback summary is already right."""
    await conn.send_json({"type": "command", "action": "playRadio", "station": _station_summary(station), "origin": origin})
    conn.playback.update({
        "playing": True, "track_id": None,
        "source": {"type": "radio", "id": station["id"], "name": station["name"], "genre": station.get("genre", ""),
                   "streamUrl": station.get("streamUrl"), "saved": origin == "saved"},
    })


def _default_station(stations: list[dict]) -> dict | None:
    if not stations:
        return None
    return max(stations, key=lambda s: (int(s.get("lastPlayedAt") or 0), -stations.index(s)))


async def _tool_play_radio(conn: ClientConn, fc) -> dict:
    args = fc.args or {}
    query = str(args.get("query") or "").strip()
    country = str(args.get("country") or "").strip()
    place = str(args.get("place") or "").strip()
    stations = radio_manager.load_stations()

    if _is_generic_radio_request(query) and not place:
        station = _default_station(stations)
        if not station:
            if not ONLINE_DIRECTORIES:
                return {"result": "There are no saved radio stations and the online directory is disabled. Ask the user to add a station (with its stream URL) in the Radio tab."}
            return {"result": "There are no saved radio stations yet. Ask the user what kind of station they want (a genre or a station name) so the directory can be searched."}
        await _send_play_radio(conn, station, "saved")
        print(f"[Tool] Radio: tuned in to saved station '{station['name']}'")
        return {"result": f"Tuned in to {_station_label(station)} (your {'most recently played' if station.get('lastPlayedAt') else 'most recently added'} station). {conn.playback_summary()}"}

    station = radio_manager.find_station(f"{query} {place}".strip(), stations) if query else None
    if station:
        await _send_play_radio(conn, station, "saved")
        print(f"[Tool] Radio: tuned in to saved station '{station['name']}' for '{query}'")
        return {"result": f"Tuned in to saved station {_station_label(station)}. {conn.playback_summary()}"}

    if not ONLINE_DIRECTORIES:
        return {"result": f"No saved station matches '{query}' and the online directory is disabled.{_available_stations_hint()}"}

    async with _http_client(timeout=8.0) as client:
        results = await _search_stations(query, country=country, place=place, limit=5, client=client)
        if not results:
            where = f" in {place}" if place else ""
            return {"result": f"No saved station matches '{query}' and the station directories found nothing for it{where} (or are unreachable). "
                              f"Suggest the user try the station's frequency or call sign, or add it by stream URL in the Radio tab.{_available_stations_hint()}"}
        top = await _prepare_station_for_saving(results[0], client=client)
    # The relay only serves saved stations, and a station you asked for by
    # name is one you'll want again: save it (the user can remove it later).
    station, created = radio_manager.add_station(top, source="directory")
    station = station or top
    conn.directory_stations[station["id"]] = station
    await _send_play_radio(conn, station, "saved")
    if created:
        await _notify_sources_changed(conn, "radio", "created", station, f"Saved {_station_label(station)} from the directory")
    others = "; ".join(_station_label(r) for r in results[1:4])
    unverified = "" if top.get("online", True) else " The directory could not verify this stream recently (often just geo-blocking), so it may not play everywhere."
    if top.get("provider") == "tunein":
        unverified += " (Found via TuneIn.)"
    print(f"[Tool] Radio: tuned in to directory station '{station['name']}' for '{query}'")
    return {"result": f"No saved station matched, so I searched the online directory and tuned in to {_station_label(station)}; "
                      f"it has been saved to the user's stations (they can say 'remove the {station['name']} station' to undo).{unverified}"
                      + (f" Other matches: {others}." if others else "") + f" {conn.playback_summary()}"}


async def _tool_list_radio_stations(conn: ClientConn, fc) -> dict:
    stations = radio_manager.load_stations()
    if not stations:
        hint = " Say 'play some jazz radio' to search the online directory." if ONLINE_DIRECTORIES else " Stations can be added in the Radio tab."
        return {"result": "No radio stations are saved yet." + hint}
    src = conn.current_source()
    playing_id = src.get("id") if src and src.get("type") == "radio" else None
    items = []
    for st in stations[:15]:
        label = _station_label(st)
        if st.get("id") == playing_id:
            label += " [playing now]"
        items.append(label)
    more = f" …and {len(stations) - 15} more." if len(stations) > 15 else ""
    starters = sum(1 for st in stations if st.get("source") == "default")
    note = ""
    if starters:
        note = (f" {starters} of these are starter picks pre-loaded on first run; the user can remove any and add more"
                + (" by saying e.g. 'save a jazz station' or via the Radio tab's Find Stations." if ONLINE_DIRECTORIES else " via the Radio tab (stream URL)."))
    return {"result": f"{len(stations)} saved radio station{'s' if len(stations) != 1 else ''}: {'; '.join(items)}.{more}{note}"}


async def _tool_save_radio_station(conn: ClientConn, fc) -> dict:
    args = fc.args or {}
    query = str(args.get("query") or "").strip()
    custom_name = _clean_new_name(str(args.get("name") or ""))

    if not query or _is_current_ref(query, _RADIO_REF_FILLER):
        src = conn.current_source()
        if not src or src.get("type") != "radio":
            return {"result": "No radio station is playing right now, so there is nothing to save. Ask which station (name or genre) to find and save."}
        existing = radio_manager.get_station_by_id(src["id"])
        if existing:
            if custom_name and custom_name != existing["name"]:
                updated = radio_manager.update_station(existing["id"], name=custom_name) or existing
                msg = f"Renamed saved station '{existing['name']}' to '{updated['name']}'."
                await _notify_sources_changed(conn, "radio", "updated", updated, msg)
                return {"result": msg}
            return {"result": f"{_station_label(existing)} is already in the saved stations."}
        record = dict(conn.directory_stations.get(src["id"]) or {
            "name": src.get("name"), "streamUrl": src.get("streamUrl"), "genre": src.get("genre", ""),
        })
        if custom_name:
            record["name"] = custom_name
        station, created = radio_manager.add_station(record, source="directory")
        if not station:
            return {"result": "I could not save the current station: its stream address is unknown. Ask the user to add it from the Radio tab."}
        msg = f"Saved {_station_label(station)} to your radio stations."
        await _notify_sources_changed(conn, "radio", "created" if created else "updated", station, msg)
        conn.playback["source"]["saved"] = True
        print(f"[Tool] {msg}")
        return {"result": msg + " It keeps playing."}

    existing = radio_manager.find_station(query)
    if existing and not custom_name:
        return {"result": f"{_station_label(existing)} is already saved. Say 'play {existing['name']}' to tune in."}
    if not ONLINE_DIRECTORIES:
        return {"result": "The online station directory is disabled, so stations can only be added with their stream URL in the Radio tab."}
    async with _http_client(timeout=8.0) as client:
        results = await _search_stations(query, limit=5, client=client)
        if not results:
            return {"result": f"The station directories found nothing for '{query}' (or are unreachable). Nothing was saved."}
        top = await _prepare_station_for_saving(results[0], client=client)
    if custom_name:
        top["name"] = custom_name
    station, created = radio_manager.add_station(top, source="directory")
    msg = f"Saved {_station_label(station)} to your radio stations." if created else f"{_station_label(station)} was already saved; details refreshed."
    await _notify_sources_changed(conn, "radio", "created" if created else "updated", station, msg)
    print(f"[Tool] {msg}")
    return {"result": msg + f" Playback was not changed; say 'play {station['name']}' to tune in."}


async def _tool_remove_radio_station(conn: ClientConn, fc) -> dict:
    args = fc.args or {}
    name = str(args.get("name") or "").strip()
    src = conn.current_source()
    if _is_current_ref(name, _RADIO_REF_FILLER):
        if not src or src.get("type") != "radio":
            return {"result": "No radio station is playing right now, so 'this station' is ambiguous." + _available_stations_hint()}
        station = radio_manager.get_station_by_id(src["id"])
        if not station:
            return {"result": f"The playing station '{src.get('name')}' is not in the saved list (it came from the directory), so there is nothing to remove."}
    else:
        station = radio_manager.find_station(name)
        if not station:
            return {"result": f"No saved station matches '{name}'." + _available_stations_hint()}
    if not radio_manager.delete_station(station["id"]):
        return {"result": f"Could not remove station '{station['name']}'."}
    msg = f"Removed {_station_label(station)} from your saved stations."
    if src and src.get("type") == "radio" and src.get("id") == station["id"]:
        msg += " It keeps playing until you change the station or play music."
        conn.playback["source"]["saved"] = False
    await _notify_sources_changed(conn, "radio", "deleted", station, msg)
    print(f"[Tool] {msg}")
    return {"result": msg}


def _resolve_podcast(conn: ClientConn, spoken: str) -> tuple[dict | None, str | None]:
    spoken = (spoken or "").strip()
    src = conn.current_source()
    if _is_current_ref(spoken, _PODCAST_REF_FILLER):
        if src and src.get("type") == "podcast":
            pod = podcast_manager.get_podcast_by_id(src["id"])
            if pod:
                return pod, None
            return None, "The podcast that was playing is no longer subscribed." + _available_podcasts_hint()
        pods = podcast_manager.load_podcasts()
        if len(pods) == 1 and not spoken:
            return pods[0], None
        return None, "No podcast is playing right now, so I need to know which podcast you mean." + _available_podcasts_hint()
    pod = podcast_manager.find_podcast(spoken)
    if not pod:
        return None, f"No subscribed podcast matches '{spoken}'." + _available_podcasts_hint() + (
            " Offer to subscribe to it (subscribeToPodcast) if they want." if ONLINE_DIRECTORIES else "")
    return pod, None


async def _refresh_if_stale(pod: dict) -> tuple[dict, str | None]:
    if not podcast_manager.is_stale(pod):
        return pod, None
    async with _http_client(timeout=15.0) as client:
        fresh, err = await podcast_manager.refresh_podcast(pod["id"], client=client)
    return (fresh or pod), err


async def _tool_play_podcast(conn: ClientConn, fc) -> dict:
    args = fc.args or {}
    pod, err = _resolve_podcast(conn, str(args.get("podcast") or ""))
    if err:
        return {"result": err}
    pod, refresh_err = await _refresh_if_stale(pod)
    if not pod.get("episodes") and refresh_err:
        return {"result": f"Could not load episodes for '{pod['title']}': {refresh_err} The feed may be offline; ask the user to try again later or check the feed in the Podcasts tab."}

    src = conn.current_source()
    current_ep = src.get("episodeId") if src and src.get("type") == "podcast" and src.get("id") == pod["id"] else None
    ep, err = podcast_manager.resolve_episode(pod, str(args.get("episode") or ""), current_episode_id=current_ep)
    if err:
        return {"result": err}

    await conn.send_json({"type": "command", "action": "playPodcastEpisode", "podcast": _podcast_summary(pod), "episode": ep})
    conn.playback.update({
        "playing": True, "track_id": None,
        "source": {"type": "podcast", "id": pod["id"], "name": pod["title"], "episodeId": ep["id"],
                   "episodeTitle": ep["title"], "duration": ep.get("duration"), "episodeDescription": ep.get("description", "")},
    })
    podcast_manager.mark_played(pod["id"])
    print(f"[Tool] Podcast: playing '{ep['title']}' from '{pod['title']}'")
    notes = f" Episode notes: {ep['description'][:200]}" if ep.get("description") else ""
    warn = f" (The feed could not be refreshed — {refresh_err} — so this is from the cached episode list.)" if refresh_err else ""
    return {"result": f"Playing {podcast_manager.episode_label(ep)} from '{pod['title']}'. It resumes where the user left off if they started it before.{notes}{warn} {conn.playback_summary()}"}


async def _tool_list_podcasts(conn: ClientConn, fc) -> dict:
    pods = podcast_manager.load_podcasts()
    if not pods:
        hint = " Say 'subscribe to <podcast name>' to add one." if ONLINE_DIRECTORIES else " Subscriptions can be added with a feed URL in the Podcasts tab."
        return {"result": "There are no podcast subscriptions yet." + hint}
    items = []
    for p in pods[:12]:
        eps = p.get("episodes") or []
        latest = f", latest: '{eps[0]['title']}'" if eps else (", episodes not loaded yet" if not p.get("lastFetchedAt") else "")
        items.append(f"'{p['title']}'{' by ' + p['author'] if p.get('author') else ''} ({len(eps)} episodes{latest})")
    more = f" …and {len(pods) - 12} more." if len(pods) > 12 else ""
    starters = sum(1 for p in pods if p.get("source") == "default")
    note = ""
    if starters:
        note = (f" {starters} of these are starter picks pre-loaded on first run; the user can unsubscribe from any and add more"
                + (" by saying e.g. 'subscribe to <podcast name>' or via the Podcasts tab." if ONLINE_DIRECTORIES else " via the Podcasts tab (feed URL)."))
    return {"result": f"{len(pods)} podcast subscription{'s' if len(pods) != 1 else ''}: {'; '.join(items)}.{more}{note}"}


async def _tool_describe_podcast(conn: ClientConn, fc) -> dict:
    args = fc.args or {}
    pod, err = _resolve_podcast(conn, str(args.get("podcast") or ""))
    if err:
        return {"result": err}
    pod, refresh_err = await _refresh_if_stale(pod)
    eps = pod.get("episodes") or []
    if not eps:
        return {"result": f"'{pod['title']}' has no episodes in its feed" + (f" (refresh failed: {refresh_err})" if refresh_err else "") + "."}
    listing = "; ".join(f"{i + 1}. {podcast_manager.episode_label(e)}" for i, e in enumerate(eps[:8]))
    more = f"; …and {len(eps) - 8} older" if len(eps) > 8 else ""
    about = f" About: {pod['description'][:200]}" if pod.get("description") else ""
    latest_notes = f" The latest episode is about: {eps[0]['description'][:200]}" if eps[0].get("description") else ""
    return {"result": f"'{pod['title']}'{' by ' + pod['author'] if pod.get('author') else ''} has {len(eps)} episodes, newest first: {listing}{more}.{about}{latest_notes} This listing is authoritative."}


async def _tool_subscribe_to_podcast(conn: ClientConn, fc) -> dict:
    args = fc.args or {}
    query = str(args.get("query") or "").strip()
    if not query:
        return {"result": "I need the podcast's name (or feed URL) to subscribe. Ask the user which podcast."}

    others = ""
    if re.match(r"^https?://", query, re.IGNORECASE):
        feed_url = query
    else:
        already = podcast_manager.find_podcast(query)
        if already:
            return {"result": f"Already subscribed to '{already['title']}'. Say 'play {already['title']}' to listen."}
        if not ONLINE_DIRECTORIES:
            return {"result": "The online podcast directory is disabled; podcasts can be added with their RSS feed URL in the Podcasts tab."}
        async with _http_client(timeout=8.0) as client:
            results = await podcast_manager.search_directory(query, limit=5, client=client)
        if not results:
            return {"result": f"The podcast directory found nothing for '{query}' (or is unreachable). Nothing was added."}
        feed_url = results[0]["feedUrl"]
        others = "; ".join(f"'{r['title']}' by {r['author']}" for r in results[1:4])

    try:
        async with _http_client(timeout=15.0) as client:
            parsed = await podcast_manager.fetch_feed(feed_url, client=client)
    except ValueError as e:
        return {"result": f"Found the podcast but could not load its feed: {e} Nothing was added."}

    pod, created = podcast_manager.upsert_podcast(parsed)
    eps = pod.get("episodes") or []
    latest = f" Latest episode: {podcast_manager.episode_label(eps[0])}." if eps else ""
    msg = (f"Subscribed to '{pod['title']}'" if created else f"Refreshed existing subscription '{pod['title']}'") + \
          (f" by {pod['author']}" if pod.get("author") else "") + f" ({len(eps)} episodes)."
    await _notify_sources_changed(conn, "podcast", "created" if created else "updated", pod, msg)
    print(f"[Tool] {msg}")
    return {"result": msg + latest + " Playback was not changed." + (f" Other directory matches were: {others}." if others else "")}


async def _tool_unsubscribe_from_podcast(conn: ClientConn, fc) -> dict:
    args = fc.args or {}
    pod, err = _resolve_podcast(conn, str(args.get("podcast") or ""))
    if err:
        return {"result": err}
    if not podcast_manager.delete_podcast(pod["id"]):
        return {"result": f"Could not remove '{pod['title']}'."}
    msg = f"Unsubscribed from '{pod['title']}'."
    src = conn.current_source()
    if src and src.get("type") == "podcast" and src.get("id") == pod["id"]:
        msg += " The current episode keeps playing."
    await _notify_sources_changed(conn, "podcast", "deleted", pod, msg)
    print(f"[Tool] {msg}")
    return {"result": msg}


def _latest_items_hint(items: list[dict]) -> str:
    return "; ".join(f"{podcast_manager.episode_label(i['episode'])} from '{i['podcast']['title']}'" for i in items)


async def _tool_list_latest_episodes(conn: ClientConn, fc) -> dict:
    args = fc.args or {}
    try:
        count = int(args.get("count") or 8)
    except (TypeError, ValueError):
        count = 8
    count = max(1, min(count, 25))
    items = podcast_manager.latest_episodes(limit=count)
    if not items:
        pods = podcast_manager.load_podcasts()
        return {"result": ("There are no podcast subscriptions yet." if not pods else
                           "No episodes are loaded yet for your podcasts; they load when a show is opened or played.")}
    listing = "; ".join(f"{i + 1}. {podcast_manager.episode_label(it['episode'])} from '{it['podcast']['title']}'" for i, it in enumerate(items))
    return {"result": f"The {len(items)} newest episodes across your podcasts, newest first: {listing}. Say 'play the newest episodes' to start them. This listing is authoritative."}


async def _tool_play_latest_episodes(conn: ClientConn, fc) -> dict:
    args = fc.args or {}
    try:
        count = int(args.get("count") or 5)
    except (TypeError, ValueError):
        count = 5
    count = max(1, min(count, 25))
    queue_only = bool(args.get("queueOnly", False))
    items = podcast_manager.latest_episodes(limit=count)
    if not items:
        return {"result": "No podcast episodes are available to play yet (no subscriptions, or their feeds have not loaded)."}
    first, rest = items[0], items[1:]
    if queue_only:
        await conn.send_json({"type": "command", "action": "queuePodcastEpisodes", "items": items})
        q = conn.playback.get("queue") or []
        q.extend({"id": it["episode"]["id"], "title": it["episode"]["title"], "artist": it["podcast"]["title"], "kind": "podcast"} for it in items)
        conn.playback["queue"] = q
        print(f"[Tool] Podcasts: cued {len(items)} newest episodes")
        return {"result": f"Cued {len(items)} newest episodes: {_latest_items_hint(items)}. Playback was not interrupted. {conn.playback_summary()}"}
    await conn.send_json({"type": "command", "action": "playPodcastEpisode", "podcast": first["podcast"], "episode": first["episode"]})
    if rest:
        await conn.send_json({"type": "command", "action": "queuePodcastEpisodes", "items": rest})
    conn.playback.update({
        "playing": True, "track_id": None,
        "source": {"type": "podcast", "id": first["podcast"]["id"], "name": first["podcast"]["title"], "episodeId": first["episode"]["id"],
                   "episodeTitle": first["episode"]["title"], "duration": first["episode"].get("duration"), "episodeDescription": first["episode"].get("description", "")},
        "queue": [{"id": it["episode"]["id"], "title": it["episode"]["title"], "artist": it["podcast"]["title"], "kind": "podcast"} for it in rest],
    })
    podcast_manager.mark_played(first["podcast"]["id"])
    print(f"[Tool] Podcasts: playing newest episode '{first['episode']['title']}' + {len(rest)} cued")
    more = f" Cued after it: {_latest_items_hint(rest)}." if rest else ""
    return {"result": f"Playing the newest episode: {podcast_manager.episode_label(first['episode'])} from '{first['podcast']['title']}'.{more} {conn.playback_summary()}"}


async def _tool_seek_relative(conn: ClientConn, fc) -> dict:
    args = fc.args or {}
    try:
        seconds = float(args.get("seconds") or 0)
    except (TypeError, ValueError):
        seconds = 0.0
    if seconds == 0:
        return {"result": "Tell me how far to skip, e.g. 30 seconds ahead or a minute back."}
    seconds = max(-3600.0, min(3600.0, seconds))
    src = conn.current_source()
    if src and src.get("type") not in SEEKABLE_SOURCE_TYPES:
        return {"result": "Live radio cannot be skipped forward or back."}
    if not src and not conn.now_playing():
        return {"result": "Nothing is playing right now, so there is nothing to skip within."}

    await conn.send_json({"type": "command", "action": "seekRelative", "seconds": seconds})
    where = ""
    if src and isinstance(src.get("position"), (int, float)):
        new_pos = max(0.0, float(src["position"]) + seconds)
        if isinstance(src.get("duration"), (int, float)) and src["duration"] > 0:
            new_pos = min(new_pos, float(src["duration"]))
        src["position"] = new_pos
        where = f" Now at {_fmt_clock(new_pos)}" + (f" of {_fmt_clock(src['duration'])}" if src.get("duration") else "") + "."
    amount = _fmt_clock(abs(seconds)) if abs(seconds) >= 60 else f"{int(abs(seconds))} seconds"
    return {"result": f"Skipped {'ahead' if seconds > 0 else 'back'} {amount}.{where}"}


SPEED_STEPS = [0.8, 1.0, 1.25, 1.5, 1.75, 2.0]


async def _tool_set_volume(conn: ClientConn, fc) -> dict:
    args = fc.args or {}
    current = float(conn.playback.get("volume") if conn.playback.get("volume") is not None else 1.0)

    def _num(v):
        try:
            return None if v is None or v == "" else float(v)
        except (TypeError, ValueError):
            return None

    level, change = _num(args.get("level")), _num(args.get("change"))
    if level is not None:
        target = level / 100.0 if level > 1.0 else level          # accept 40 or 0.4
    elif change is not None:
        target = current + change / 100.0
    else:
        target = current + 0.15
    target = max(0.0, min(1.0, target))
    await conn.send_json({"type": "command", "action": "setVolume", "level": round(target, 3)})
    conn.playback["volume"] = target
    pct = round(target * 100)
    print(f"[Tool] Volume -> {pct}% (was {round(current * 100)}%)")
    if pct == 0:
        return {"result": "Muted the music (volume 0%). The DJ voice is unaffected."}
    direction = "up" if target > current + 0.001 else ("down" if target < current - 0.001 else "unchanged")
    return {"result": f"Volume {direction}: now {pct}% (was {round(current * 100)}%)."}


async def _tool_set_playback_speed(conn: ClientConn, fc) -> dict:
    args = fc.args or {}
    src = conn.current_source()
    if src and src.get("type") == "radio":
        return {"result": "Live radio always plays at normal speed; playback speed applies to podcasts and audiobooks."}
    if src and src.get("type") == "stream":
        return {"result": "Streamed tracks play at normal speed; playback speed applies to podcasts and audiobooks. Nothing was changed."}
    if not (src and src.get("type") in SEEKABLE_SOURCE_TYPES):
        return {"result": "Playback speed only applies to podcast episodes and audiobook chapters; music always plays at normal speed. Nothing was changed."}
    current = float(conn.playback.get("playbackRate") or 1.0)
    raw = args.get("rate")
    if raw is None or raw == "":
        faster = [x for x in SPEED_STEPS if x > current + 0.01]
        rate = faster[0] if faster else SPEED_STEPS[-1]
    else:
        try:
            rate = float(raw)
        except (TypeError, ValueError):
            return {"result": "I did not catch the speed. Say e.g. '1.5x', 'double speed' (2) or 'normal speed' (1)."}
    rate = max(0.5, min(3.0, rate))
    await conn.send_json({"type": "command", "action": "setPlaybackRate", "rate": rate})
    conn.playback["playbackRate"] = rate
    label = f"{rate:g}x"
    what = src.get("episodeTitle") if src["type"] == "podcast" else (src.get("chapterTitle") or src.get("name"))
    if abs(rate - 1.0) < 0.01:
        return {"result": f"Back to normal speed (1x) for '{what}'."}
    return {"result": f"Playing '{what}' at {label}."}


# ── Audiobook voice tools ─────────────────────────────────────────────────────

_BOOK_REF_FILLER = {"the", "a", "my", "book", "audiobook", "audio", "novel", "story", "one", "again", "it", "reading", "listening"}


def _audiobooks_unavailable() -> str | None:
    if AUDIOBOOKS_DIR is None:
        return "Audiobooks are not set up: there is no AUDIOBOOKS_DIR in backend/.env. Tell the user to add the path to their audiobook folder there and restart the server."
    if not audiobooks_scan_complete:
        return "The audiobook folder is still being scanned. Ask the user to try again shortly."
    if not audiobook_library:
        return f"The audiobook folder ({AUDIOBOOKS_DIR}) contains no audiobooks. Books are folders of chapter files or single .m4b/.mp3 files."
    return None


def _available_books_hint(limit: int = 6) -> str:
    names = [f"'{b['title']}'" for b in audiobook_library[:limit]]
    return f" Audiobooks: {', '.join(names)}." if names else ""


def _resolve_audiobook(conn: ClientConn, spoken: str) -> tuple[dict | None, str | None]:
    """'current' / '' -> the book playing now, else the most recently listened
    one; anything else is fuzzy-matched on title/author/narrator."""
    spoken = (spoken or "").strip()
    progress = audiobook_manager.load_progress()
    src = conn.current_source()
    if _is_current_ref(spoken, _BOOK_REF_FILLER):
        if src and src.get("type") == "audiobook":
            book = audiobooks_by_id.get(src.get("id") or "")
            if book:
                return book, None
        recent = audiobook_manager.most_recent_book(audiobook_library, progress)
        if recent:
            return recent, None
        if len(audiobook_library) == 1:
            return audiobook_library[0], None
        return None, "No audiobook is playing or in progress, so I need to know which book." + _available_books_hint()
    book = audiobook_manager.find_audiobook(spoken, audiobook_library, progress)
    if not book:
        return None, f"No audiobook matches '{spoken}'." + _available_books_hint()
    return book, None


async def _tool_play_audiobook(conn: ClientConn, fc) -> dict:
    args = fc.args or {}
    unavailable = _audiobooks_unavailable()
    if unavailable:
        return {"result": unavailable}
    book, err = _resolve_audiobook(conn, str(args.get("book") or ""))
    if err:
        return {"result": err}
    prog = audiobook_manager.get_progress(book["id"])
    src = conn.current_source()
    current_ch = src.get("chapterId") if src and src.get("type") == "audiobook" and src.get("id") == book["id"] else None
    ch, position, err = audiobook_manager.resolve_chapter(book, str(args.get("chapter") or ""), progress=prog, current_chapter_id=current_ch)
    if err:
        return {"result": err}

    await conn.send_json({
        "type": "command", "action": "playAudiobookChapter",
        "book": audiobook_manager.summarize(book, prog), "chapter": ch, "position": position,
        "chapterCount": len(book.get("chapters") or []),
    })
    conn.playback.update({
        "playing": True, "track_id": None,
        "source": {"type": "audiobook", "id": book["id"], "name": book["title"], "author": book.get("author", ""),
                   "chapterId": ch["id"], "chapterIndex": ch["index"], "chapterTitle": ch["title"],
                   "chapterCount": len(book.get("chapters") or []), "position": position, "duration": ch.get("duration")},
    })
    resumed = position > 0
    where = f", resuming at {_fmt_clock(position)}" if resumed else ""
    print(f"[Tool] Audiobook: playing {audiobook_manager.chapter_label(book, ch)} of '{book['title']}'{where}")
    status = audiobook_manager.progress_label(book, prog)
    return {"result": f"Playing {audiobook_manager.book_label(book)}, {audiobook_manager.chapter_label(book, ch)}{where}. "
                      f"Before this the book was {status}. Progress is remembered automatically. {conn.playback_summary()}"}


async def _tool_list_audiobooks(conn: ClientConn, fc) -> dict:
    unavailable = _audiobooks_unavailable()
    if unavailable:
        return {"result": unavailable}
    progress = audiobook_manager.load_progress()
    src = conn.current_source()
    playing_id = src.get("id") if src and src.get("type") == "audiobook" else None
    items = []
    for b in audiobook_library[:15]:
        label = audiobook_manager.book_label(b)
        label += f" [{audiobook_manager.format_duration(b.get('duration'))}, {b.get('chapterCount')} chapter{'s' if b.get('chapterCount') != 1 else ''}]"
        label += f" — {audiobook_manager.progress_label(b, progress.get(b['id']))}"
        if b["id"] == playing_id:
            label += " [playing now]"
        items.append(label)
    more = f" …and {len(audiobook_library) - 15} more." if len(audiobook_library) > 15 else ""
    in_progress = [b for b in audiobook_library if progress.get(b["id"]) and not progress[b["id"]].get("finished")]
    note = f" {len(in_progress)} in progress." if in_progress else ""
    return {"result": f"{len(audiobook_library)} audiobook{'s' if len(audiobook_library) != 1 else ''}: {'; '.join(items)}.{more}{note}"}


async def _tool_describe_audiobook(conn: ClientConn, fc) -> dict:
    args = fc.args or {}
    unavailable = _audiobooks_unavailable()
    if unavailable:
        return {"result": unavailable}
    book, err = _resolve_audiobook(conn, str(args.get("book") or ""))
    if err:
        return {"result": err}
    prog = audiobook_manager.get_progress(book["id"])
    chapters = book.get("chapters") or []
    listing = "; ".join(audiobook_manager.chapter_label(book, c) for c in chapters[:12])
    more = f"; …and {len(chapters) - 12} more" if len(chapters) > 12 else ""
    return {"result": f"{audiobook_manager.book_label(book)}: {audiobook_manager.format_duration(book.get('duration'))} in {len(chapters)} chapter{'s' if len(chapters) != 1 else ''}"
                      f"{', genre ' + book['genre'] if book.get('genre') and book['genre'] != 'Audiobook' else ''}. "
                      f"Progress: {audiobook_manager.progress_label(book, prog)}. Chapters: {listing}{more}. This listing is authoritative."}


# ── Streaming voice tools ─────────────────────────────────────────────────────

def _stream_kind(raw: str, query: str) -> str:
    kind = (raw or "").strip().lower()
    if kind in {"track", "song", "tracks", "songs"}:
        return "track"
    if kind in {"album", "albums", "record"}:
        return "album"
    if kind in {"artist", "artists", "band"}:
        return "artist"
    if re.search(r"\b(album|record|lp)\b", query, re.IGNORECASE):
        return "album"
    return "track"


_PROVIDER_ALIASES = {
    "apple": "apple", "apple music": "apple", "itunes": "apple",
    "deezer": "deezer",
    "youtube": "ytmusic", "youtube music": "ytmusic", "yt music": "ytmusic", "yt": "ytmusic", "ytmusic": "ytmusic",
}


def _stream_provider(raw: str, query: str) -> str | None:
    """A provider the user named (argument or inside the query), else None = any."""
    for text in (raw or "", query or ""):
        t = text.lower()
        for alias, pid in sorted(_PROVIDER_ALIASES.items(), key=lambda kv: -len(kv[0])):
            if re.search(rf"\b{re.escape(alias)}\b", t):
                return pid
    return None


def _clean_stream_query(query: str) -> str:
    q = re.sub(r"\b(on|from|in|via|with|using)\s+(apple\s+music|apple|itunes|deezer|youtube\s+music|yt\s+music|youtube|yt)\b", " ", query, flags=re.IGNORECASE)
    q = re.sub(r"\b(apple\s+music|deezer|youtube\s+music|yt\s+music|streaming|stream|online)\b", " ", q, flags=re.IGNORECASE)
    q = re.sub(r"\b(please|play|queue|cue|up|the\s+(?:song|track|album|artist)|song|track)\b", " ", q, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", q).strip() or query.strip()


def _stream_track_duration(t: dict) -> int | None:
    if t.get("full"):
        return round((t.get("durationMs") or 0) / 1000) or None
    return streaming_manager.PREVIEW_SECONDS if t.get("audioUrl") else None


async def _send_play_stream(conn: ClientConn, tracks: list[dict], context: dict | None, queue_only: bool = False):
    """Push the tracks to the browser and mirror the new source/queue locally
    so the same-turn playback summary is already right."""
    if queue_only:
        await conn.send_json({"type": "command", "action": "queueStreamTracks", "tracks": tracks, "context": context})
        q = conn.playback.get("queue") or []
        for t in tracks:
            q.append({"id": t["id"], "title": t["title"], "artist": t["artist"], "kind": "stream"})
        conn.playback["queue"] = q
        return
    await conn.send_json({"type": "command", "action": "playStreamTracks", "tracks": tracks, "context": context})
    first = tracks[0]
    conn.playback.update({
        "playing": True, "track_id": None,
        "source": {"type": "stream", "id": first["id"], "title": first["title"], "artist": first["artist"], "album": first.get("album", ""),
                   "provider": first.get("provider"), "full": bool(first.get("full")), "duration": _stream_track_duration(first),
                   "contextType": (context or {}).get("type"), "contextName": (context or {}).get("name")},
        "queue": [{"id": t["id"], "title": t["title"], "artist": t["artist"], "kind": "stream"} for t in tracks[1:]],
    })


def _pick_provider_results(results: list[dict], preferred: str | None) -> tuple[dict | None, str]:
    """The first provider with playable TRACKS (preferred one first); also
    the combined error text when none has any."""
    order = ([r for r in results if r["provider"] == preferred] if preferred else []) + [r for r in results if r["provider"] != preferred]
    for r in order:
        r["tracks"] = [t for t in r["tracks"] if streaming_manager.playable(t)]
        if r["tracks"]:
            return r, ""
    errors = "; ".join(f"{streaming_manager.provider_name(r['provider'])}: {r['error']}" for r in results if r.get("error"))
    return None, errors


async def _stream_collect(query: str, kind: str, provider: str | None, client: httpx.AsyncClient) -> tuple[list[dict], dict | None, str]:
    """Spoken request -> (tracks, context, error)."""
    q = _clean_stream_query(query)
    providers = [provider] if provider else None
    if kind == "track":
        results = await streaming_manager.search_all(q, client, limit=5, providers=providers)
        best, errors = _pick_provider_results(results, provider)
        if not best or not best["tracks"]:
            return [], None, f"No streaming source has a playable match for '{q}'." + (f" ({errors})" if errors else "")
        return best["tracks"][:1], {"type": "search", "name": q, "provider": best["provider"]}, ""
    if kind == "album":
        q2 = re.sub(r"\b(the\s+)?album\b", " ", q, flags=re.IGNORECASE).strip() or q
        results = await streaming_manager.search_all(q2, client, limit=3, providers=providers)
        for r in ([x for x in results if x["provider"] == provider] if provider else []) + [x for x in results if x["provider"] != provider]:
            if r["albums"]:
                album = r["albums"][0]
                songs = [t for t in await streaming_manager.album_tracks(album["id"], client) if streaming_manager.playable(t)]
                if songs:
                    return songs, {"type": "album", "name": album["name"], "id": album["id"], "artist": album.get("artist", ""), "provider": r["provider"]}, ""
        return [], None, f"No album matching '{q2}' with playable tracks on the streaming sources."
    results = await streaming_manager.search_all(q, client, limit=10, artist_only=True, providers=providers)
    best, errors = _pick_provider_results(results, provider)
    if not best or not best["tracks"]:
        return [], None, f"No songs by '{q}' on the streaming sources." + (f" ({errors})" if errors else "")
    return best["tracks"], {"type": "artist", "name": best["tracks"][0].get("artist") or q, "id": None, "provider": best["provider"]}, ""


def _stream_reply(tracks: list[dict], context: dict | None) -> str:
    first = tracks[0]
    name = streaming_manager.provider_name(first.get("provider", ""))
    ctx = ""
    if context and context.get("type") in ("album", "artist"):
        ctx = f" from {context['type']} '{context['name']}'" + (f" ({len(tracks)} tracks, the rest are cued)" if len(tracks) > 1 else " (its only playable track)")
    return f"Streaming from {name}: {streaming_manager.track_label(first)}{ctx}. {streaming_manager.mode_note(first)}"


async def _tool_play_stream(conn: ClientConn, fc) -> dict:
    args = fc.args or {}
    query = str(args.get("query") or "").strip()
    if not query:
        return {"result": "I need to know what to stream (a song, artist or album)."}
    kind = _stream_kind(str(args.get("kind") or ""), query)
    provider = _stream_provider(str(args.get("source") or ""), query)
    if provider and not streaming_manager.provider_status(provider)["available"]:
        return {"result": f"{streaming_manager.provider_name(provider)} is not available: {streaming_manager.provider_status(provider)['reason']}. "
                          f"Available streaming sources: {', '.join(streaming_manager.provider_name(p) for p in streaming_manager.available_providers()) or 'none'}."}
    async with _http_client(timeout=12.0) as client:
        tracks, context, err = await _stream_collect(query, kind, provider, client)
    if err:
        return {"result": err + (" Suggest trying the local library (searchAndPlay) or a different spelling." if kind == "track" else "")}
    await _send_play_stream(conn, tracks, context)
    first = tracks[0]
    print(f"[Tool] Streaming: playing {streaming_manager.track_label(first)} via {first.get('provider')}" + (f" ({context['type']} '{context['name']}')" if context else ""))
    return {"result": f"{_stream_reply(tracks, context)} {conn.playback_summary()}"}


async def _tool_queue_stream(conn: ClientConn, fc) -> dict:
    args = fc.args or {}
    query = str(args.get("query") or "").strip()
    if not query:
        return {"result": "I need to know which song to cue from the streaming sources."}
    provider = _stream_provider(str(args.get("source") or ""), query)
    async with _http_client(timeout=12.0) as client:
        tracks, context, err = await _stream_collect(query, "track", provider, client)
    if err or not tracks:
        return {"result": err or f"Nothing was found for '{query}'."}
    await _send_play_stream(conn, tracks[:1], context, queue_only=True)
    t = tracks[0]
    print(f"[Tool] Streaming: queued {streaming_manager.track_label(t)} via {t.get('provider')}")
    return {"result": f"Cued up from {streaming_manager.provider_name(t.get('provider', ''))}: {streaming_manager.track_label(t)} to play after the current item. "
                      f"{streaming_manager.mode_note(t)} {conn.playback_summary()}"}


TOOL_HANDLERS = {
    "playTrack": _tool_play_track,
    "queueTrack": _tool_queue_track,
    "createAndPlayPlaylist": _tool_create_and_play_playlist,
    "playPlaylist": _tool_play_playlist,
    "listPlaylists": _tool_list_playlists,
    "queuePlaylist": _tool_queue_playlist,
    "describePlaylist": _tool_describe_playlist,
    "createPlaylist": _tool_create_playlist,
    "addToPlaylist": _tool_add_to_playlist,
    "removeFromPlaylist": _tool_remove_from_playlist,
    "renamePlaylist": _tool_rename_playlist,
    "deletePlaylist": _tool_delete_playlist,
    "saveQueueAsPlaylist": _tool_save_queue_as_playlist,
    "clearQueue": _tool_clear_queue,
    "removeFromQueue": _tool_remove_from_queue,
    "describeQueue": _tool_describe_queue,
    "describeNowPlaying": _tool_describe_now_playing,
    "searchAndPlay": _tool_search_and_play,
    "searchAndQueue": _tool_search_and_queue,
    "pausePlayback": _tool_pause,
    "resumePlayback": _tool_resume,
    "nextTrack": _tool_next,
    "previousTrack": _tool_prev,
    # live radio & podcasts
    "playRadio": _tool_play_radio,
    "listRadioStations": _tool_list_radio_stations,
    "saveRadioStation": _tool_save_radio_station,
    "removeRadioStation": _tool_remove_radio_station,
    "playPodcast": _tool_play_podcast,
    "listPodcasts": _tool_list_podcasts,
    "describePodcast": _tool_describe_podcast,
    "subscribeToPodcast": _tool_subscribe_to_podcast,
    "unsubscribeFromPodcast": _tool_unsubscribe_from_podcast,
    "listLatestEpisodes": _tool_list_latest_episodes,
    "playLatestEpisodes": _tool_play_latest_episodes,
    "seekRelative": _tool_seek_relative,
    "setPlaybackSpeed": _tool_set_playback_speed,
    "setVolume": _tool_set_volume,
    # audiobooks & streaming
    "playAudiobook": _tool_play_audiobook,
    "listAudiobooks": _tool_list_audiobooks,
    "describeAudiobook": _tool_describe_audiobook,
    "playStream": _tool_play_stream,
    "queueStream": _tool_queue_stream,
}


# ─── WebSocket ────────────────────────────────────────────────────────────────

@app.websocket("/ws/live")
async def ws_live_proxy(ws: WebSocket):
    await ws.accept()

    conn = ClientConn(ws)

    # Send ready IMMEDIATELY on accept (V5 behavior). Gating "ready" on a
    # hello handshake (V6.0) was a regression: a stale cached app.js never
    # sends hello, so the server blocked forever and the UI sat at
    # "Warming up…". The handshake is now advisory — validated when present,
    # skipped with a warning when not.
    await conn.send_state("offline")
    await conn.send_json({"type": "ready"})
    print("[ws] Browser connected")

    api_key = GEMINI_API_KEY or os.getenv("GEMINI_API_KEY")
    if not api_key:
        await conn.send_json({"type": "error", "message": "GEMINI_API_KEY not configured"})
        await ws.close(code=1008)
        return

    client = genai.Client(api_key=api_key)

    session = None
    session_stop = None
    session_tasks = []
    is_generating = False
    handshake_done = False

    async def teardown_session(reason="manual"):
        nonlocal session, session_stop, session_tasks, is_generating
        if session_stop:
            session_stop.set()
        for t in session_tasks:
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        session_tasks = []
        session_stop = None

        if is_generating:
            is_generating = False
            await conn.send_state("idle", "Session ended")
            await conn.send_json({"type": "turn_complete"})

        had_session = session is not None
        if session is not None:
            try:
                # Public API close; fall back only if unavailable.
                if hasattr(session, "close"):
                    await session.close()
                else:
                    ctx = getattr(session, '_ctx_mgr', None)
                    if ctx:
                        await ctx.__aexit__(None, None, None)
            except Exception as e:
                print(f"[ws] Gemini close error: {e}")

        session = None
        if had_session:
            print(f"[ws] Gemini session ended ({reason})")

    async def start_session():
        nonlocal session, session_stop, session_tasks, is_generating

        if not scan_complete:
            await conn.send_state("error", "Library still scanning...")
            await conn.send_json({"type": "error", "message": "Library still scanning. Try again in a moment."})
            return

        if session is not None:
            await teardown_session("replaced")

        session_stop = asyncio.Event()
        is_generating = False
        conn.audio_bytes_since_turn = 0

        try:
            await conn.send_state("connecting", "Opening Gemini session...")
            config = build_gemini_config()

            ctx_mgr = client.aio.live.connect(
                model=GEMINI_LIVE_MODEL,
                config=config,
            )
            session = await ctx_mgr.__aenter__()
            # Keep a reference for cleanup fallback, but prefer session.close()
            session._ctx_mgr = ctx_mgr

            await conn.send_state("listening", "Listening for your voice...")
            await conn.send_json({"type": "connected"})

            recv_task = asyncio.create_task(
                gemini_receive_loop(session, conn, session_stop)
            )
            session_tasks = [recv_task]

        except Exception as e:
            print(f"[ws] Gemini connect failed: {e}")
            await conn.send_state("error", str(e))
            await conn.send_json({"type": "error", "message": str(e)})
            session = None
            session_stop = None

    async def gemini_receive_loop(sess, conn: ClientConn, stop_event: asyncio.Event):
        nonlocal is_generating

        turn_start_time = 0
        first_audio_logged = False

        try:
            while not stop_event.is_set():
                async for msg in sess.receive():
                    if stop_event.is_set():
                        return

                    sc = msg.server_content

                    if sc and sc.interrupted:
                        is_generating = False
                        await conn.send_json({"type": "interrupted"})
                        await conn.send_state("listening", "Listening for your voice...")

                    if sc and sc.model_turn and sc.model_turn.parts:
                        if not is_generating:
                            is_generating = True
                            turn_start_time = time.perf_counter()
                            first_audio_logged = False
                            await conn.send_state("speaking", "AI DJ is talking...")

                        for part in sc.model_turn.parts:
                            if part.inline_data and part.inline_data.data:
                                if not first_audio_logged:
                                    ttft = (time.perf_counter() - turn_start_time) * 1000
                                    await conn.send_json({"type": "telemetry", "ttft_ms": ttft})
                                    first_audio_logged = True

                                await conn.ws.send_bytes(part.inline_data.data)

                    if sc and sc.turn_complete:
                        is_generating = False
                        conn.audio_bytes_since_turn = 0
                        await conn.send_json({"type": "turn_complete"})

                    if msg.tool_call and msg.tool_call.function_calls:
                        await conn.send_state(
                            "tool_call",
                            "Executing: " + ", ".join(
                                fc.name for fc in msg.tool_call.function_calls
                            ),
                        )

                        fn_responses = []
                        for fc in msg.tool_call.function_calls:
                            handler = TOOL_HANDLERS.get(fc.name)
                            if handler is None:
                                result = {"result": f"Unknown tool: {fc.name}"}
                            else:
                                try:
                                    result = {"result": (await handler(conn, fc))["result"]}
                                except Exception as tool_err:
                                    print(f"[Tool] Error executing {fc.name}: {tool_err}")
                                    result = {"result": f"Error: {tool_err}"}
                            fn_responses.append(
                                types.FunctionResponse(
                                    name=fc.name, id=fc.id, response=result,
                                )
                            )

                        try:
                            await sess.send_tool_response(function_responses=fn_responses)
                            await conn.send_state("speaking", "AI DJ is responding...")
                        except Exception as e:
                            print(f"[ws] Tool response failed: {e}")

        except asyncio.CancelledError:
            if is_generating:
                is_generating = False
                try:
                    await conn.send_state("idle", "Cancelled")
                    await conn.send_json({"type": "turn_complete"})
                except Exception:
                    pass
            return

        except Exception as e:
            if not stop_event.is_set():
                print(f"[gemini→browser] Error: {e}")
            if is_generating:
                is_generating = False
                try:
                    await conn.send_state("idle", "Stream ended")
                    await conn.send_json({"type": "turn_complete"})
                except Exception:
                    pass

    try:
        while True:
            message = await ws.receive()

            if "bytes" in message:
                conn.audio_bytes_since_turn += len(message["bytes"])
                if session:
                    try:
                        await session.send_realtime_input(
                            audio={
                                "data": message["bytes"],
                                "mime_type": "audio/pcm;rate=16000",
                            }
                        )
                    except Exception as e:
                        print(f"[browser→gemini] Send failed: {e}")
                        await teardown_session("send error")

            elif "text" in message:
                try:
                    data = json.loads(message["text"])
                except Exception:
                    print("[ws] Invalid JSON from client")
                    continue
                msg_type = data.get("type")

                # ── Lenient handshake: validate hello if it's the first
                #    message, but never block or disconnect over it. ──
                if not handshake_done:
                    handshake_done = True
                    if msg_type == "hello":
                        cver = data.get("version")
                        if cver != PROTOCOL_VERSION:
                            print(f"[ws] Version mismatch: client v{cver} vs server v{PROTOCOL_VERSION} — continuing (warn only)")
                            await conn.send_json({
                                "type": "version_mismatch",
                                "message": f"Client protocol v{cver} != server v{PROTOCOL_VERSION}. Hard-refresh the page (Ctrl+Shift+R).",
                            })
                        else:
                            pass  # handshake OK — nothing worth logging
                        continue  # hello needs no further handling
                    else:
                        print(f"[ws] Legacy client (no hello handshake, first msg='{msg_type}') — continuing anyway")
                        # fall through: process this message normally

                if msg_type == "start":
                    await start_session()

                elif msg_type == "stop":
                    await conn.send_state("disconnecting", "Closing session...")
                    await teardown_session("user stop")
                    await conn.send_state("offline")
                    await conn.send_json({"type": "stopped"})

                elif msg_type == "playback_state":
                    # Browser reports real playback state; used in tool responses
                    conn.playback["playing"] = bool(data.get("playing", False))
                    conn.playback["track_id"] = data.get("trackId")
                    conn.playback["queue"] = data.get("queue", [])
                    conn.playback["playlist"] = data.get("playlist")
                    src = data.get("source")
                    conn.playback["source"] = src if isinstance(src, dict) and src.get("type") in SOURCE_TYPES else None
                    try:
                        conn.playback["playbackRate"] = float(data.get("playbackRate") or 1.0)
                    except (TypeError, ValueError):
                        conn.playback["playbackRate"] = 1.0
                    try:
                        conn.playback["volume"] = max(0.0, min(1.0, float(data.get("volume")))) if data.get("volume") is not None else conn.playback.get("volume")
                    except (TypeError, ValueError):
                        pass

                # Browser speech recognition can capture a complete request
                # with “Hey DJ”, or a follow-up command during the short live
                # countdown. Forward either transcript as a completed turn.
                elif msg_type in {"wake_text", "followup_text"}:
                    request_text = str(data.get("text", "")).strip()
                    if not request_text:
                        continue
                    if len(request_text) > 1_000:
                        await conn.send_json({"type": "error", "message": "Voice request is too long."})
                        continue
                    if not session:
                        await conn.send_json({"type": "error", "message": "No active Live session for the voice request."})
                        continue
                    try:
                        await conn.send_state("processing", "Processing your voice request...")
                        await session.send_client_content(
                            turns=types.Content(
                                role="user",
                                parts=[types.Part(text=request_text)],
                            ),
                            turn_complete=True,
                        )
                        pass
                    except Exception as e:
                        print(f"[ws] {msg_type} forward failed: {e}")
                        await conn.send_json({"type": "error", "message": "Could not send the voice request."})

                # Only forward end_turn if the user actually spoke this turn —
                # an empty turn can error on some model versions.
                elif msg_type == "end_turn":
                    if session:
                        if conn.audio_bytes_since_turn == 0:
                            print("[ws] end_turn skipped: no mic audio received this turn")
                        else:
                            try:
                                await session.send_client_content(
                                    turn_complete=True
                                )
                                conn.audio_bytes_since_turn = 0
                                pass
                            except Exception as e:
                                print(f"[ws] end_turn forward failed: {e}")
                    else:
                        print("[ws] end_turn received but no active session")

                elif msg_type == "ping":
                    await conn.send_json({"type": "pong"})

                else:
                    print(f"[ws] Unknown message type: {msg_type}")

    except WebSocketDisconnect:
        pass
    except RuntimeError as e:
        if "Cannot call" in str(e) and "disconnect" in str(e):
            pass
        else:
            print(f"[ws] Fatal RuntimeError: {e}")
    except Exception as e:
        print(f"[ws] Fatal: {e}")
    finally:
        await teardown_session("ws closed")
        print("[ws] Browser disconnected")


# ─── Console logging ──────────────────────────────────────────────────────────

class _AccessLogNoiseFilter(logging.Filter):
    """When access logging is on, still drop the per-track art/stream and
    static-asset requests that otherwise flood the console."""
    _NOISY = ("/api/tracks/", "/static/", "/favicon.ico")

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            path = record.args[2] if record.args and len(record.args) > 2 else ""
        except Exception:
            path = ""
        return not any(str(path).startswith(n) for n in self._NOISY)


class _ServerNoiseFilter(logging.Filter):
    """Drop uvicorn's per-WebSocket chatter; main.py logs one line each way."""
    _NOISY = ('"WebSocket /ws/live"', "connection open", "connection closed")

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return not any(n in msg for n in self._NOISY)


def _log_config() -> dict:
    """uvicorn log config: plain (colour-free) lines, quiet by default."""
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            # uvicorn expects these two formatter names to exist
            "default": {"()": "uvicorn.logging.DefaultFormatter", "fmt": "[server] %(message)s", "use_colors": False},
            "access": {"()": "uvicorn.logging.AccessFormatter",
                       "fmt": '[http] %(client_addr)s "%(request_line)s" %(status_code)s', "use_colors": False},
        },
        "handlers": {
            "default": {"class": "logging.StreamHandler", "formatter": "default", "stream": "ext://sys.stdout",
                        "filters": ["server_noise"]},
            "access": {"class": "logging.StreamHandler", "formatter": "access", "stream": "ext://sys.stdout",
                       "filters": ["noise"]},
        },
        "filters": {"noise": {"()": _AccessLogNoiseFilter}, "server_noise": {"()": _ServerNoiseFilter}},
        "loggers": {
            "uvicorn": {"handlers": ["default"], "level": LOG_LEVEL.upper(), "propagate": False},
            "uvicorn.error": {"level": LOG_LEVEL.upper()},
            "uvicorn.access": {"handlers": ["access"], "level": "INFO" if HTTP_ACCESS_LOG else "WARNING", "propagate": False},
        },
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app, host="0.0.0.0", port=8000,
        log_level=LOG_LEVEL,
        access_log=HTTP_ACCESS_LOG,
        use_colors=False,
        log_config=_log_config(),
    )

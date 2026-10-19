"""
AI DJ Backend — FastAPI + Gemini Live API proxy (V6.2)

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
except ImportError:
    import playlists as pl_manager


# ─── Configuration ────────────────────────────────────────────────────────────

PROTOCOL_VERSION = 3

load_dotenv(dotenv_path=Path(__file__).parent / ".env")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
MUSIC_DIR = Path(os.getenv("MUSIC_DIR", Path.home() / "Music"))
USE_SEARCH_TOOL = os.getenv("USE_SEARCH_TOOL", "true").lower() == "true"
GEMINI_LIVE_MODEL = os.getenv("GEMINI_LIVE_MODEL", "gemini-3.8-live")
# Console verbosity. HTTP_ACCESS_LOG=true re-enables uvicorn's per-request
# lines (art/stream/static requests are still filtered out as noise).
HTTP_ACCESS_LOG = os.getenv("HTTP_ACCESS_LOG", "false").lower() == "true"
LOG_LEVEL = os.getenv("LOG_LEVEL", "info").lower()
AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".wma", ".aac"}
FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
STREAM_CHUNK_SIZE = 64 * 1024

if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY not set in .env")

# Global State
music_library: list[dict] = []
tracks_by_id: dict[str, dict] = {}
scan_complete: bool = False
album_art_cache: OrderedDict = OrderedDict()


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


def scan_music_library() -> list[dict]:
    tracks = []
    if not MUSIC_DIR.exists():
        print(f"[Warn] Music directory not found: {MUSIC_DIR}")
        return tracks
    for f in sorted(MUSIC_DIR.rglob("*")):
        if f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS:
            rel = f.relative_to(MUSIC_DIR)
            title, artist, album, genre = _get_metadata(f)
            tracks.append({
                "id": _stable_track_id(str(rel)),
                "title": title, "artist": artist,
                "album": album, "genre": genre,
                "filename": str(rel),
            })
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

    global music_library, tracks_by_id, scan_complete
    print("⏳ Scanning music library in background (Recursive)...")
    loop = asyncio.get_running_loop()
    music_library = await loop.run_in_executor(None, scan_music_library)
    tracks_by_id = {t["id"]: t for t in music_library}
    scan_complete = True
    print(f"🎵 Loaded {len(music_library)} tracks from {MUSIC_DIR}")
    print(f"🔍 USE_SEARCH_TOOL is set to {USE_SEARCH_TOOL}")
    yield
    print("Shutting down AI DJ backend.")


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
            "description": "Tells you exactly which SONG is playing right now (title, artist, album, genre) and whether it belongs to the active playlist. ALWAYS call this for 'what's playing', 'what song is this', 'who sings this', 'what is this' — answer about the song, not the playlist.",
            "parameters": {"type": "OBJECT", "properties": {}},
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
            "Do NOT ask the user to choose; automatically pick the best matching track or playlist.\n"
        )
        system_instruction += PLAYLIST_MANAGEMENT_INSTRUCTIONS
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
            "When the user asks to cue or queue a song to play next (using keywords like 'queue', 'cue', 'next'), use `queueTrack`."
        )
        system_instruction += PLAYLIST_MANAGEMENT_INSTRUCTIONS

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
    response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/")
async def serve_index():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/favicon.ico")
async def get_favicon():
    return Response(content=b"", media_type="image/x-icon")


@app.get("/api/tracks")
async def get_tracks():
    if not scan_complete:
        return JSONResponse({"status": "scanning", "tracks": []}, status_code=503)
    return JSONResponse(music_library)


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


@app.get("/api/tracks/{track_id}/stream")
async def stream_track(track_id: str, range_header: str | None = Header(None, alias="Range")):
    track = tracks_by_id.get(track_id)
    if not track:
        return JSONResponse({"error": "Track not found"}, status_code=404)
    path = MUSIC_DIR / track["filename"]
    if not path.exists():
        return JSONResponse({"error": "File not found on disk"}, status_code=404)

    media_map = {
        ".mp3": "audio/mpeg", ".wav": "audio/wav", ".flac": "audio/flac",
        ".m4a": "audio/mp4", ".ogg": "audio/ogg", ".aac": "audio/aac",
        ".wma": "audio/x-ms-wma",
    }
    media_type = media_map.get(path.suffix.lower(), "application/octet-stream")
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


app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


# ─── WebSocket Connection Wrapper ─────────────────────────────────────────────

class ClientConn:
    """Encapsulates per-connection state and safe sends."""

    def __init__(self, ws: WebSocket):
        self.ws = ws
        # Real playback state, reported by the browser; included in tool
        # responses so Gemini sees the truth instead of a fabricated Success.
        self.playback = {"playing": False, "track_id": None, "queue": [], "playlist": None}
        self.audio_bytes_since_turn = 0

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
        """The track the browser reports as loaded, or None."""
        tid = self.playback.get("track_id")
        return tracks_by_id.get(tid) if tid else None

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

        if trk:
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
            next_item = queue[0]
            next_id = next_item if isinstance(next_item, str) else next_item.get("id")
            next_trk = tracks_by_id.get(next_id)
            if next_trk:
                parts.append(f"Next in queue: '{next_trk['title']}' by {next_trk['artist']}.")
            else:
                parts.append(f"Next in queue: track {next_id}.")
            if len(queue) > 1:
                parts.append(f"({len(queue)} songs queued in total).")
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
    for item in conn.playback.get("queue") or []:
        tid = item if isinstance(item, str) else (item or {}).get("id")
        if tid in tracks_by_id and tid not in track_ids:
            track_ids.append(tid)
    if not track_ids:
        return {"result": "Nothing is playing and the queue is empty, so there is nothing to save. Ask the user to cue up some songs first."}

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
    return {"result": f"{msg} It is now the active playlist. Its tracks, in order: {listing}{more}."}


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

def _queue_tracks(conn: ClientConn) -> list[dict]:
    """The browser-reported queue, resolved to library track dicts (in order)."""
    out = []
    for item in conn.playback.get("queue") or []:
        tid = item if isinstance(item, str) else (item or {}).get("id")
        trk = tracks_by_id.get(tid)
        if trk:
            out.append(trk)
    return out


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
    queued = _queue_tracks(conn)
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
    return {"result": f"Removed '{trk['title']}' by {trk['artist']} from the queue. {conn.playback_summary()}"}


async def _tool_describe_queue(conn: ClientConn, fc) -> dict:
    queued = _queue_tracks(conn)
    now_id = conn.playback.get("track_id")
    now = tracks_by_id.get(now_id) if now_id else None
    now_part = f"Now {'playing' if conn.playback.get('playing') else 'paused'}: '{now['title']}' by {now['artist']}. " if now else "Nothing is playing. "
    if not queued:
        return {"result": now_part + "The queue is empty; after this track the library continues in order."}
    listing = "; ".join(f"{i + 1}. '{t['title']}' by {t['artist']}" for i, t in enumerate(queued[:15]))
    more = f"; …and {len(queued) - 15} more" if len(queued) > 15 else ""
    pl = conn.playback.get("playlist")
    pl_part = f" (from playlist '{pl.get('name')}')" if isinstance(pl, dict) and pl.get("name") else ""
    return {"result": f"{now_part}{len(queued)} track{'s' if len(queued) != 1 else ''} cued up{pl_part}, in order: {listing}{more}."}


async def _tool_describe_now_playing(conn: ClientConn, fc) -> dict:
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
        nid = queue[0] if isinstance(queue[0], str) else (queue[0] or {}).get("id")
        nxt = tracks_by_id.get(nid)
        if nxt:
            desc += f" Up next: '{nxt['title']}' by {nxt['artist']}."
    return {"result": desc}


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

    client = genai.Client(api_key=GEMINI_API_KEY)

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

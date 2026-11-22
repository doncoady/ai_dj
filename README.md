# AI DJ 🎧

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)](https://fastapi.tiangolo.com/)
[![Gemini Live API](https://img.shields.io/badge/Google%20GenAI-Live%20API-4285F4.svg)](https://ai.google.dev/)

A real-time, voice-interactive AI DJ prototype powered by Google's **Gemini Multimodal Live API** and the **Web Audio API**. Control your local music library, live radio, podcasts, local audiobooks and online streaming sources hands-free using natural voice commands, wake-phrase activation ("Hey DJ"), and real-time audio interaction.

---

## 📑 Table of Contents

- [Overview](#-overview)
- [Key Features](#-key-features)
- [Architecture](#-architecture)
- [File Structure](#-file-structure)
- [Quick Start](#-quick-start)
- [Voice Commands & Interaction](#-voice-commands--interaction)
- [Configuration](#-configuration)
- [Backend & API Reference](#-backend--api-reference)
  - [HTTP Endpoints](#http-endpoints)
  - [WebSocket Protocol](#websocket-protocol-wslive)
  - [Voice Tool Handlers](#gemini-tools-voice-controls)
- [Frontend Architecture](#-frontend-architecture)
  - [Audio Processing & Ducking](#audio-processing--ducking)
  - [AI State Machine](#ai-state-machine)
- [Running Tests](#-running-tests)
- [Known Issues & Considerations](#-known-issues--considerations)
- [License](#-license)

---

## 🌟 Overview

**AI DJ** connects a browser-based glassmorphism music deck with a local Python backend and Google's Gemini Multimodal Live API. The AI acts as an energetic, responsive DJ persona—cueing tracks, curating dynamic mixes, managing playlists, and speaking back over your music with automatic volume ducking.

- **Platform:** Windows PC, Chrome/Edge browser
- **AI Model:** `gemini-3.8-live` (configurable via `GEMINI_LIVE_MODEL` in `.env`)
- **Voice:** Zephyr (prebuilt natural voice config)
- **Persona:** AI DJ — cool, energetic, slightly robotic club DJ
- **Protocol Version:** 5 (client-server handshake on connect)

### Core Priorities
1. **Audio Performance:** Smooth ducking and fading without pops, clicks, or glitches.
2. **UI Responsiveness:** Low-latency off-thread mic capture using `AudioWorklet` (no deprecated `ScriptProcessorNode`).
3. **Security:** API keys remain strictly server-side in `.env`; local file paths are abstracted behind stable stream IDs.
4. **Reliable State Management:** Single-owner turn completion (`TurnManager`), activity watchdogs, and safety resets.

---

## 🚀 Key Features

- **Voice Invocation ("Hey DJ") & Live toggle:** Activate the DJ hands-free using Web Speech wake-word recognition or flip the **Go Live** switch in the top bar. A compact status readout shows what the DJ is doing (listening, thinking, speaking…).
- **Bi-directional Live Voice Streaming:** 16kHz PCM audio streaming to Gemini with incoming 24kHz DJ voice playback via Web Audio.
- **Dynamic Mix Curation:** Ask the DJ to create themed playlists (e.g., "play some 80s rock" or "make a chill late-night mix") curated on-the-fly from your local library metadata.
- **Natural Playlist & Queue Management:** Voice tools allow creating, describing, renaming, adding/removing tracks, queueing, and deleting playlists with verbal confirmation.
- **Live Radio:** ~20 starter stations across genres and countries (Radio Paradise, KEXP, NTS, FIP, Jazz24, Classic FM, NPR, SomaFM channels…), a station finder that browses the public radio-browser.info directory by genre, top stations or search, and voice control (*"play some jazz radio"*, *"save this station"*). The deck shows a **LIVE** badge and the station's on-air song title (ICY metadata), so *"what song is this?"* works on the radio too.
- **Podcasts:** Starter subscriptions (Up First, The Daily, Global News Podcast, Stuff You Should Know, This American Life, …) whose episodes load in the background, subscribe by name (Apple podcast directory) or RSS URL, an episode panel with number • date • length • time left and show notes, and voice control (*"play the latest Night Shift"*, *"play episode 12"*, *"skip ahead 30 seconds"*). Episodes resume where you left off; ↺15 / 30↻ skip buttons appear on the transport.
- **Local Audiobooks:** Point `AUDIOBOOKS_DIR` at your audiobook folder and the **Audiobooks** tab lists every book (cover, author, narrator, length, progress) with its chapters. Books are folders of chapter files (`Author/Book/01.mp3…`, `CD 1`/`CD 2` sub-folders are merged) or single `.m4b`/`.mp3` files; tags override folder names. Chapters auto-advance, ↺15 / 30↻ and the speed control work, and your place is remembered **on the server**, so *"Hey DJ, continue my book"*, *"play chapter 5 of Dune"*, *"next chapter"*, *"how far am I in the Hobbit?"* all work.
- **Streaming (online sources, nothing to configure):** A **Streaming** tab with one search across several online sources, each in its own **sub-section**: **Apple Music** (30-second previews via the keyless iTunes Search API), **Deezer** (30-second previews, keyless) and **YouTube Music** (full-length songs through the third-party tools `ytmusicapi` for search and `yt-dlp` for the audio — installed with the requirements; the section says so if they are missing). All audio is relayed by the backend and plays **through the deck**, so ducking, ▶/⏸, seeking and the queue behave exactly like local tracks; albums and artists play as a run of tracks. When a streamed track ends the deck moves on to the queue — or stops; it never wanders into your local library by itself. Voice: *"stream Blinding Lights"*, *"play One More Time from YouTube Music"*, *"queue up Daft Punk from Deezer"*, *"play the album Discovery online"*.
- **Fast start-up:** the server answers immediately and scans your music/audiobook folders in the background; the Library tab shows *"Reading track tags… 3,000 of 12,480 files"* with a progress bar meanwhile. Tags are cached on disk (`library_cache.json`, validated by size + mtime), so restarts only read new or changed files.
- **Intelligent Audio Ducking:** Music volume automatically ducks to 15% when invoking the DJ or speaking, and smoothly fades back up over 1.5s when speech concludes. Radio, podcast, audiobook and streamed audio all flow through the same Web Audio graph (ducking).
- **Balanced Deck UI:** Top bar (brand · DJ readout · Live toggle · volume) over a two-column deck — *Now Playing* (fixed artwork tile with a source badge, transport, Up Next) and *Browse* (same width as the player; one tab per source: Music, Radio, Podcasts, Audiobooks, Streaming). The tab row is a **horizontal carousel**: tabs keep their full labels, ‹ › buttons scroll the row, it can be dragged with the pointer (snapping to a tab boundary), and the active tab is kept centred so it and its neighbours are always fully visible. **Playlists** live in the player column under the cue. Starter picks are badged and explained in a dismissable note that also shows the voice commands for adding more.
- **Extensible Sources:** New audio sources (SoundCloud, TuneIn, …) plug in via a `SOURCE_KINDS` registry in `frontend/sources.js`, a view registry in `app.js`, and `SOURCE_TYPES` in the backend — see [Adding a Source](#adding-a-source); new *streaming providers* are one entry in `backend/streaming.py`'s `PROVIDERS` table plus a search function.

---

## 🏗 Architecture

```
┌─────────────────────────────────┐           WebSocket            ┌─────────────────────────────────┐        Gemini Live API
│          Web Frontend           │◄═════════════════════════════►│          Python Backend         │◄═════════════════════════►  Google
│      (HTML5 / CSS3 / ES6)       │             /ws/live           │       (FastAPI + Uvicorn)       │     (google-genai SDK)
│                                 │                                │                                 │
│   • AudioWorklet (16kHz PCM mic)│         HTTP GET (Range)       │   Serves:                       │
│   • Web Audio Graph + Ducking   │◄─── /api/tracks/:id/stream ────┤   • Local audio file streams    │
│   • State Machine & TurnManager │◄─── /api/radio/:id/stream ─────┤   • Live radio relay (ICY demux)│
│   • Queue / playlists / DnD     │◄─ /api/podcasts/…/stream ──────┤   • Podcast episode relay       │
│                                 │◄─ /api/audiobooks/…/stream ────┤   • Audiobook chapter streams   │
│                                 │◄─ /api/streaming/…/audio ──────┤   • Streaming audio relay       │
└─────────────────────────────────┘                                └─────────────────────────────────┘
                                                                          │                 │
                                                                          ▼                 ▼
                                                                  Local Filesystem     Internet
                                                                (Music, Audiobooks)  (radio streams, RSS feeds,
                                                                                      radio-browser.info, Apple
                                                                                      podcast directory, iTunes/
                                                                                      Deezer search + clips,
                                                                                      YouTube Music via yt-dlp)
```

### Security Model
- **API Keys:** Stored in `backend/.env` and accessed exclusively by the backend service. The Streaming tab needs no keys or accounts at all.
- **Path Abstraction:** Absolute filesystem paths are never exposed to the client; tracks are identified by stable hashes (`t_` + MD5 slice). Stations (`rs_`), podcasts (`pc_`), episodes (`ep_`), audiobooks (`ab_`) and chapters (`ch_`) use the same scheme.
- **Same-Origin Media:** Remote radio/podcast audio is relayed by the backend. The browser routes `<audio>` through `createMediaElementSource`, which silences cross-origin media without CORS — the relay is what keeps ducking working. Only `http(s)` URLs are accepted for stations and feeds.
- **Network Scope:** Server binds `0.0.0.0` with CORS enabled for local prototype flexibility (intended for local network use).

---

## 📁 File Structure

```
ai_dj/
├── backend/
│   ├── tests/                 # pytest suite: playlists, queue, radio, podcasts, audiobooks, streaming, voice tools, API, frontend contract
│   ├── .env.example           # Template for environment variables
│   ├── .env                   # Local secrets (API keys, paths; git-ignored)
│   ├── main.py                # FastAPI server, Gemini Live proxy, scanner, tools, stream relays
│   ├── playlists.py           # Playlist persistence, fuzzy name matching & curation
│   ├── radio.py               # Radio stations, directory search, .pls/.m3u + ICY metadata helpers
│   ├── podcasts.py            # Podcast subscriptions, RSS parsing, directory search, episode resolution
│   ├── audiobooks.py          # Audiobook folder scanner, chapter ordering, progress, spoken chapter resolution
│   ├── streaming.py           # Streaming providers: Apple Music / Deezer previews, YouTube Music (ytmusicapi + yt-dlp), audio resolution
│   ├── scancache.py           # On-disk tag cache for the music / audiobook scanners (size + mtime validated)
│   ├── textmatch.py           # Spoken-name matching shared by radio, podcasts & audiobooks
│   ├── playlists.json         # Persisted user & AI playlists (git-ignored)
│   ├── radio_stations.json    # Saved radio stations (git-ignored; seeded on first run)
│   ├── podcasts.json          # Podcast subscriptions + cached episodes (git-ignored)
│   ├── audiobooks.json        # Audiobook listening progress (git-ignored)
│   ├── library_cache.json     # Music tag cache (git-ignored; rebuilt automatically)
│   ├── audiobooks_cache.json  # Audiobook tag cache (git-ignored)
│   └── requirements.txt       # Python dependencies
├── frontend/
│   ├── tests/                 # node --test unit tests for sources.js (run via pytest when node exists)
│   ├── app.js                 # Frontend application logic, Web Audio graph, TurnManager
│   ├── sources.js             # Pure source helpers (radio, podcasts, audiobooks, streaming: display, payloads, filters)
│   ├── index.html             # Glassmorphism interface structure
│   ├── pcm-processor.js       # AudioWorkletProcessor for mic capture (off-thread)
│   └── style.css              # Glassmorphic UI styles, animations, responsive layout
├── .gitignore                 # Git ignore rules
├── LICENSE                    # MIT License
├── README.md                  # Project documentation (this file)
├── run.bat                    # Windows one-click environment setup & server launch
└── run_tests.bat              # Windows one-click test suite runner
```

---

## ⚡ Quick Start

### Prerequisites
- **Python 3.11+** installed and added to your `PATH`
- **Google Gemini API Key** (with Multimodal Live API access)
- A local directory containing audio files (`.mp3`, `.wav`, `.flac`, `.m4a`, `.ogg`, `.wma`, or `.aac`)
- **Google Chrome** or **Microsoft Edge** (for Web Speech API and AudioWorklet support)
- Optional: a local audiobook folder (`.m4b`, `.mp3`, `.m4a`, `.ogg`, `.opus`, `.flac`, …) for the Audiobooks tab
- Optional: for the Streaming tab's **YouTube Music** section, the third-party tools `ytmusicapi` and `yt-dlp` (installed automatically by `run.bat` / `requirements.txt`); Apple Music and Deezer previews need nothing
- Optional: **Node.js 20+** — only used to run the frontend unit tests (skipped when absent)

### Option 1: Windows One-Click Setup
1. Clone or extract the repository to your machine.
2. Create `backend/.env` (see [Configuration](#-configuration)).
3. Double-click `run.bat` (or run it in Command Prompt/PowerShell):
   ```bat
   run.bat
   ```
4. Open [http://localhost:8000](http://localhost:8000) in Chrome or Edge.

### Option 2: Manual Setup
```bash
# 1. Navigate to backend
cd backend

# 2. Create and activate a virtual environment
python -m venv .venv

# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
# Edit backend/.env with your GEMINI_API_KEY and MUSIC_DIR

# 5. Start the backend server
python main.py
```
Open [http://localhost:8000](http://localhost:8000) in your browser.

---

## 🎙 Voice Commands & Interaction

Activate the AI DJ by saying **“Hey DJ”** or switching **Go Live** on in the top bar. When you finish speaking, Gemini automatically detects end-of-speech and responds with voice and tool actions.

| What You Say | What AI DJ Does |
|---|---|
| *"Play some 80s music"* / *"Make me a chill mix"* | Curates, saves, and plays an AI dynamic mix |
| *"Play my rock playlist"* / *"Shuffle the 80s mix"* | Plays a saved playlist (optionally pre-shuffled) |
| *"Queue up my chill playlist"* / *"Play Workout after this"* | Appends playlist tracks to the end of the queue |
| *"Create a playlist called Road Trip"* | Creates an empty playlist (say *"with this song"* to seed it) |
| *"Add this song to Road Trip"* / *"Put Billie Jean in my party mix"* | Adds a track (creates playlist if it doesn't exist) |
| *"Remove this from the current playlist"* | Removes current song from the active playlist |
| *"Rename Road Trip to Summer Drive"* | Renames the playlist |
| *"Delete the Road Trip playlist"* → *"Yes"* | Prompts for confirmation before deleting |
| *"Save this queue as Friday Night"* | Saves currently playing song + queue as a new playlist |
| *"Clear the queue"* / *"Take Heaven out of the queue"* | Clears upcoming queue or removes specific track |
| *"What's coming up?"* | Reads back the upcoming queue in order |
| *"What's playing?"* / *"Who sings this?"* | Details the current song, artist, album, and context |
| *"What playlists do I have?"* / *"What's in my chill mix?"* | Lists saved playlists or reads back tracks in a mix |
| *"Pause"* / *"Resume"* / *"Next track"* / *"Previous"* | Standard playback transport controls |
| *"Play Groove Salad"* / *"Put on some jazz radio"* / *"Play the radio"* | Tunes in a saved station (fuzzy name/genre match), else searches the online station directory |
| *"What stations do I have?"* / *"Save this station"* / *"Forget the jazz station"* | Lists (noting which are starter picks), saves (the one playing, or a directory find) or removes stations |
| *"What song is this?"* (while radio plays) | Reads the station's on-air title from ICY metadata |
| *"Subscribe to Night Shift"* / *"Unsubscribe from Night Shift"* | Finds the show in the podcast directory and subscribes (no playback) / removes it |
| *"What's new in my podcasts?"* / *"Play the newest episodes"* / *"Cue up what's new"* | Lists the newest episode of each show, or plays the most recent one with the rest cued (or cues them all) |
| *"Play the latest Night Shift"* / *"Play episode 12 of Night Shift"* / *"Play the next episode"* | Plays an episode (resumes where you left off) |
| *"What podcasts do I have?"* / *"What episodes of Night Shift are there?"* | Lists subscriptions or reads back recent episodes |
| *"Skip ahead 30 seconds"* / *"Go back a minute"* | Seeks within the episode or song (not live radio) |
| *"Play at 1.5x"* / *"Speed it up"* / *"Normal speed"* | Podcast playback speed (music and radio stay at 1×) |
| *"Turn it up"* / *"Quieter"* / *"Volume 40 percent"* / *"Mute"* | Moves the app's volume slider |
| *"Continue my audiobook"* / *"Play the Hobbit"* / *"Play chapter 5 of Dune"* | Plays a local audiobook, resuming where you left off (or the chapter asked for) |
| *"Next chapter"* / *"Previous chapter"* / *"Start it from the beginning"* | Moves between chapters of the book that is playing |
| *"What audiobooks do I have?"* / *"How far am I in Dune?"* / *"Who narrates the Hobbit?"* | Lists books with progress, or describes one (author, narrator, chapters, progress) |
| *"Stream Blinding Lights"* / *"Play the album Discovery online"* / *"Play some Daft Punk from YouTube Music"* | Searches the streaming sources (Apple Music, Deezer, YouTube Music) and plays the first match through the deck (album/artist = a run of tracks) |
| *"Queue up Mr. Brightside from Deezer"* | Adds a streamed track to the end of the queue |
| *"Play Billie Jean"* (while radio/podcast/audiobook/streaming plays) | Any music request leaves the other source and returns to the library deck |

---

## ⚙ Configuration

### `backend/.env`
Create a `.env` file in the `backend/` directory based on `backend/.env.example`:

```env
# Google Gemini API Key (required)
GEMINI_API_KEY=AIzaSy...your_gemini_api_key

# Path to local audio library (required)
MUSIC_DIR=C:\Users\YourUser\Music

# Path to local audiobooks (optional; the Audiobooks tab explains how to set it up when missing)
AUDIOBOOKS_DIR=C:\Users\YourUser\Audiobooks

# Streaming: nothing to configure (Apple Music / Deezer previews are keyless; YouTube Music uses ytmusicapi + yt-dlp from requirements.txt).

# Gemini Live Model (optional, defaults to gemini-3.8-live)
GEMINI_LIVE_MODEL=gemini-3.8-live

# Track search tool configuration (optional, defaults to true)
USE_SEARCH_TOOL=true

# Online directories for radio (radio-browser.info) and podcasts (Apple podcast
# search). Keyless. Set false to stay offline: stations/feeds can still be added
# by URL in the GUI. (optional, defaults to true)
ONLINE_DIRECTORIES=true

# Second station directory: TuneIn's unofficial OPML endpoints, for local
# commercial stations the community directory lacks. (optional, defaults to true)
TUNEIN_DIRECTORY=true

# Logging levels (optional)
HTTP_ACCESS_LOG=false
LOG_LEVEL=info
```

### Key Client Constants (`frontend/app.js`)
Configured in the `CONFIG` object inside [frontend/app.js](file:///d:/Projects/ai_dj/frontend/app.js):
- `PROTOCOL_VERSION: 5`: Handshake version matching `backend/main.py`
- `DUCK_LEVEL: 0.15`: Target music volume multiplier during speech (15%)
- `DUCK_FADE_S: 0.15`: Duration (seconds) to duck music down
- `FADE_UP_S: 1.5`: Duration (seconds) to restore music volume
- `MIC_SAMPLE_RATE: 16000`: Worklet capture sample rate (16 kHz mono)
- `VOICE_SAMPLE_RATE: 24000`: Gemini voice synthesis sample rate (24 kHz)
- `AUTO_DISCONNECT_DELAY_MS: 3000`: Follow-up listener countdown before closing session

---

## 🔧 Backend & API Reference

### HTTP Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/` | GET | Serves `frontend/index.html` |
| `/api/status` | GET | Start-up progress: `{scanning, phase, found, read, tracks, audiobooksScanning, elapsed}` |
| `/api/tracks` | GET | Returns scanned library tracks with metadata (**503** with the same progress fields while the background scan runs) |
| `/api/tracks/rescan` | POST | Re-scan `MUSIC_DIR` (unchanged files come from the tag cache; 409 while a scan runs) |
| `/api/tracks/{id}/stream` | GET | Streams audio file with full **HTTP Range** support (seeking) |
| `/api/tracks/{id}/art` | GET | Returns embedded artwork (ID3 APIC / FLAC picture) with iTunes fallback |
| `/api/playlists` | GET, POST | List all playlists / Create new playlist |
| `/api/playlists/{id}` | GET, PUT, DELETE | Retrieve, update, or remove a playlist |
| `/api/playlists/{id}/tracks` | POST | Add track IDs to a playlist |
| `/api/playlists/{id}/tracks/{tid}` | DELETE | Remove a specific track from a playlist |
| `/api/radio` | GET, POST | List saved stations / add one (`name`, `streamUrl`, `genre`, `country`, …; same URL updates in place) |
| `/api/radio/search?q=&country=&place=` | GET | Search radio-browser.info + TuneIn — tolerant of spacing/word order and frequencies (`Move100`, `96.5 the breeze`), optional country and city/region; 503 when `ONLINE_DIRECTORIES=false` |
| `/api/radio/browse?kind=&value=` | GET | Browse the directory: `popular`, `tag` (genre) or `country`; also returns the genre chip list |
| `/api/radio/{id}` | GET, PUT, DELETE | Retrieve, edit or remove a saved station |
| `/api/radio/{id}/stream` | GET | Same-origin live relay; follows `.pls`/`.m3u`, strips ICY metadata (415 for HLS, 502 upstream errors) |
| `/api/radio/{id}/now` | GET | Latest on-air `StreamTitle` seen by the relay |
| `/api/podcasts` | GET, POST | List subscriptions / subscribe by `feedUrl` (fetches & parses the RSS feed) |
| `/api/podcasts/search?q=` | GET | Search the Apple podcast directory |
| `/api/podcasts/latest?limit=&per=` | GET | Newest episodes across all subscriptions (`per` = episodes per show), newest first |
| `/api/podcasts/{id}` | GET, DELETE | Podcast with cached episodes / unsubscribe |
| `/api/podcasts/{id}/refresh` | POST | Re-fetch the feed (returns the cached copy + `refreshError` on failure) |
| `/api/podcasts/{id}/episodes/{eid}/stream` | GET | Same-origin episode relay, forwards **HTTP Range** for seeking/resume |
| `/api/audiobooks` | GET | `{configured, directoryExists, scanning, books:[…]}` — book summaries with progress |
| `/api/audiobooks/rescan` | POST | Re-scan `AUDIOBOOKS_DIR` (503 when unset) |
| `/api/audiobooks/{id}` | GET | Book with its ordered chapters and progress |
| `/api/audiobooks/{id}/progress` | PUT, DELETE | Save `{chapterId, position, duration}` (last-chapter end = finished) / forget progress |
| `/api/audiobooks/{id}/art` | GET | `cover.jpg`/`folder.jpg` from the book folder, else art embedded in the first chapter |
| `/api/audiobooks/{id}/chapters/{cid}/stream` | GET | Chapter file with full **HTTP Range** support |
| `/api/streaming/status` | GET | `{providers: [{id, name, icon, full, available, missing, reason}], playback, cached}` |
| `/api/streaming/search?q=&providers=&limit=&offset=` | GET | Every (or the listed) provider searched in parallel: `{query, results: [{provider, tracks, albums, error, offset, limit, hasMore}]}`; `offset` pages one provider's songs ("More results") |
| `/api/streaming/albums/{id}/tracks` | GET | Songs of an Apple (`apa_…`) or Deezer (`dza_…`) album |
| `/api/streaming/tracks/{id}` | GET | One cached track (`ap_…`, `dz_…`, `yt_…`) |
| `/api/streaming/tracks/{id}/audio` | GET | Same-origin relay of the track's audio — the preview clip, or the yt-dlp stream for YouTube Music (Range pass-through; 404 when a preview provider has no clip, 503 when the YouTube tools are missing) |
| `/static/*` | GET | Static assets (`app.js`, `sources.js`, `style.css`, `pcm-processor.js`) |
| `/ws/live` | WebSocket | Bidirectional Gemini Live API proxy |

### WebSocket Protocol (`/ws/live`)

- **Handshake:** First client message should be `{"type":"hello","version":5}`. The server sends `{"type":"ready"}` on accept and warns with `version_mismatch` (without disconnecting) if the versions differ.
- **Client → Server:**
  - `{"type":"start"}`: Initialize Gemini Live session.
  - `{"type":"stop"}`: Tear down session cleanly.
  - `{"type":"wake_text", "text": "..."}`: Words captured right after wake word.
  - `{"type":"playback_state", ...}`: Sends current track, queue (items carry `kind`: `track` / `podcast` / `audiobook` / `stream`), playlist and `source` (`null` for the library deck, or `{type:"radio", id, name, genre, streamUrl, saved, nowTitle}` / `{type:"podcast", id, name, episodeId, episodeTitle, position, duration}` / `{type:"audiobook", id, name, author, chapterId, chapterIndex, chapterTitle, chapterCount, position, duration}` / `{type:"stream", id, title, artist, album, provider, full, contextType, contextName, position, duration}`).
  - `[Binary ArrayBuffer]`: 16kHz Float32-to-Int16 PCM microphone frames.
- **Server → Client:**
  - `[Binary ArrayBuffer]`: 24kHz Int16 PCM Gemini speech audio chunks.
  - `{"type":"ai_state", "state": "...", "detail": "..."}`: Real-time DJ state update.
  - `{"type":"command", "action": "...", ...}`: Playback control dispatch (e.g. `playTrack`, `loadAndPlayPlaylist`, `playRadio`, `playPodcastEpisode`, `playAudiobookChapter`, `playStreamTracks`, `queueStreamTracks`, `queuePodcastEpisodes`, `seekRelative`, `setPlaybackRate`, `setVolume`, `radioStationsChanged`, `podcastsChanged`).
  - `{"type":"turn_complete"}`: Speech synthesis finished.
  - `{"type":"interrupted"}`: User interrupted the DJ.

### Gemini Tools (Voice Controls)

Function calls executed by Gemini during a turn:
- `searchAndPlay(query)` / `searchAndQueue(query)`
- `playTrack(trackId)` / `queueTrack(trackId)`
- `pausePlayback()`, `resumePlayback()`, `nextTrack()`, `previousTrack()`
- `createAndPlayPlaylist(query, playlistName?)`
- `playPlaylist(name, shuffle?)`, `queuePlaylist(playlistName, shuffle?)`
- `listPlaylists()`, `describePlaylist(playlistName)`
- `createPlaylist(name, includeCurrentTrack?)`
- `addToPlaylist(playlistName, trackQuery?, trackId?)`
- `removeFromPlaylist(playlistName, trackQuery?, trackId?)`
- `renamePlaylist(playlistName, newName)`
- `deletePlaylist(playlistName, confirmed)` (two-step voice confirmation)
- `clearQueue()`, `removeFromQueue(trackQuery, trackId?)`
- `describeQueue()`, `describeNowPlaying()` (song, or radio station + on-air title, or podcast episode + position)
- `saveQueueAsPlaylist(name?, includeCurrentTrack?)`
- Live radio: `playRadio(query?, country?, place?)` (auto-saves a directory hit), `listRadioStations()`, `saveRadioStation(query?, name?)`, `removeRadioStation(name)`
- Player: `setVolume(level?, change?)`
- Podcasts: `playPodcast(podcast, episode?)`, `listPodcasts()`, `describePodcast(podcast)`, `subscribeToPodcast(query)`, `unsubscribeFromPodcast(podcast)`, `listLatestEpisodes(count?)`, `playLatestEpisodes(count?, queueOnly?)`, `seekRelative(seconds)`, `setPlaybackSpeed(rate?)` (seek and speed also apply to audiobooks; seek to streamed tracks)
- Audiobooks: `playAudiobook(book?, chapter?)` (`chapter` = `continue` / `chapter 5` / `next` / `previous` / `from the beginning` / `last chapter` / title words), `listAudiobooks()`, `describeAudiobook(book?)`
- Streaming: `playStream(query, kind?, source?)` (track / album / artist; source = apple / deezer / youtube), `queueStream(query, source?)`

---

## 🎨 Frontend Architecture

### Audio Processing & Ducking
- **AudioContext Graph:** Shared audio context routes HTML `<audio>` through a master `GainNode` and dynamics compressor.
- **Microphone Pipeline:** `navigator.mediaDevices.getUserMedia` connected to an `AudioWorkletNode` running [frontend/pcm-processor.js](file:///d:/Projects/ai_dj/frontend/pcm-processor.js), streaming raw Int16 PCM frames over WebSocket.
- **DJ Speech Synthesizer:** Streams 24kHz Int16 PCM chunks into dynamically scheduled Web Audio buffers with jitter smoothing and instant cancellation on barge-in.
- **Logical Ducking:** `AudioManager` maintains logical gain targets, executing smooth exponential fades without reading back hardware parameters.

### Layout & Source Registry

```
┌ top bar ──────────────────────────────────────────────────────────────────────┐
│ ▣ AI DJ Live │ 👂 Listening  ● LIVE ON AIR  Just talk…  ▂▂▂ │ [🔴 On Air ◉]  🔈──🔊 │
└───────────────────────────────────────────────────────────────────────────────┘
┌ Now Playing (5/12) ───────────────┐ ┌ Browse (7/12) ─────────────────────────┐
│ [🎙 art]  ▶ NOW PLAYING           │ │ 🎵 Music  📻 Radio  🎙 Podcasts  📖 Audiobooks  🌐 … │
│ [🎙 art]  ▶ NOW PLAYING           │ │ ✨ Starter picks — add more with + Subscribe │
│           Episode title           │ │    or say “Hey DJ, subscribe to …”       │
│           Podcast name  PODCAST   │ │ ┌ card ─────────────────────────────────┐ │
│ EPISODE  Ep 12 • Mar 4 • 1h 02m   │ │ │ art  Title  [Starter]   ▶ Latest 👁 ↻ ✕│ │
│ show notes… Show more             │ │ │      author • 12 episodes • Latest …  │ │
│ 0:00 ─────●──────────── 1:02:03   │ │ └───────────────────────────────────────┘ │
│   ↺15  ⏮   ▶   ⏭  30↻            │ │ …                                        │
│ UP NEXT …                         │ │                                          │
└───────────────────────────────────┘ └──────────────────────────────────────────┘
```

- The **Live toggle** is a strict two-state switch (off / on air); the readout beside the brand carries the DJ sub-state, connection pill, hint text and the auto-disconnect countdown.
- The **artwork tile** is static (album art, station logo or podcast cover) with a kind badge (📻 / 🎙) and a small equalizer while audio plays — a spinning record made no sense for radio or podcasts.
- The **episode panel** appears under Now Playing for podcasts: `S2 Episode 3 • Mar 4, 2025 • 1h 02m • 42 min left` plus show notes (clamped, "Show more").
- The **Browse** panel has one tab per source; lists take the panel's full height and scroll internally. On screens narrower than 900px the columns stack.
- **List rows select on click** (highlight) and play on **▶** or a double-click / Enter — Music tracks, stations, playlist tracks and episodes all behave the same (↑/↓ move through the Music list). In a playlist, **▶** plays just that song; the card's **▶ Play** / **🔀 Shuffle** start the whole playlist. Every expand/collapse control is a **double chevron** at the right edge of its card (» down = collapsed, up = open, highlighted when open); bodies animate open/closed.
- **Library at scale:** the track list is virtualised (only the visible rows exist in the DOM, so 10k+ tracks scroll smoothly), shows a live count, and sorts by Title / Artist / Album / Genre (click again to flip) or library order. Sorting also **groups** the list — A–Z letters for Title, one collapsible group per Artist/Album/Genre. Groups start collapsed; each header has **▶ Play** (first track now, the rest cued) and **+ Cue**, with the chevron at the far right, plus *Expand all / Collapse all*. Track numbers were dropped; the playing row shows equalizer bars instead.
- **Button families:** every play control is green (including the main transport button), every remove/delete/stop control is red, cue controls are purple.
- **Podcast / audiobook speed:** a *Speed* dropdown beside the transport (0.8× … 2.5×); the choice is remembered per browser and applies to podcasts and audiobooks only (music, live radio and streamed tracks always play at 1×). By voice: *"play at 1.5x"*, *"speed it up"*, *"normal speed"*.
- **Audiobooks tab:** one card per book (cover, author • narrator, length • chapters • progress bar), **▶ Continue** resumes where you left off, ↺ forgets your place, the chevron expands the chapter list (heard chapters dimmed, the current one marked *Resume at …*). The episode panel under Now Playing becomes a **Chapter** panel (*Chapter 3 of 12 • title • 42 min • 20 min left*). Chapters auto-advance; ⏭/⏮ move between chapters (⏮ restarts the chapter after 5 s, like a CD player). Progress is stored in `backend/audiobooks.json` so it survives browsers and the DJ can report it.
- **Streaming tab:** a search box, then one **sub-section per provider** — YouTube Music first, then Apple Music and Deezer — each a collapsible card with its status badge (*Previews* for the 30-second sources, *Not installed* with the `pip install` hint when YouTube Music's tools are missing), the result count for the current query, song rows (▶/⏸ and + Cue; a song with no clip is greyed out), album cards (▶ plays the album, + Cue queues it) and a **More results** button whenever the provider returned a full page. When a streamed track ends, the next queued item plays or playback stops with a toast — it never falls through to the local library.
- **Audiobook / streaming buttons follow playback:** the ▶ Play / ▶ Continue button of the book (and chapter row or streaming row) that is playing turns into an amber **⏸ Pause**, then **▶ Resume** while paused.
- **Transport:** every transport control is a fixed circle (48 px, play 60 px, skips 38 px) that never shrinks; the row wraps instead when the skip buttons and speed control appear.
- **Queue rows:** the **▶ Play now** button is always visible (not hover-only).
- **Volume by voice:** *"turn it up"*, *"quieter"*, *"volume 40 percent"*, *"mute"* move the app's volume slider (the DJ's own voice is unaffected).
- **One consistent "now playing" treatment** — green left stripe and tint — for the playing song row, episode row, playlist card, station card and podcast card; the Now Playing panel itself uses the same colours for every source type (only the LIVE badge's blinking dot differs). A playlist stops being "active" (and the *one-off song* line disappears) as soon as nothing of it is playing or cued.
- **Cue everywhere:** "+ Cue" on tracks, playlist cards (whole playlist), podcast cards (latest episode), episode rows, streamed tracks and albums. The queue holds podcast episodes, streamed tracks and audiobook chapters next to songs and plays them in order; *Up Next* is expanded by default and **grows to the space available** in the player column (scrolling inside once it runs out; the summary card hides while the list is open). **▶ on a cued row plays that item right away and leaves the rest cued.**
- **Drag & drop:** Music rows *and* group headers (a whole artist/album/genre), playlist rows and Streaming results can be dragged onto the cue (dropped exactly where you point) or onto a playlist card (Music tracks only — streamed songs are refused with a hint); cue rows and playlist rows can be dragged to reorder.
- **Newest episodes:** a *Newest episodes* card at the top of the Podcasts tab lists the most recent episode of each show with ▶ Play (newest now, the rest cued) / + Cue; by voice *"play the newest episodes"*.
- **Filters vs. searches:** list filters (Music, playlists, radio, podcasts, audiobooks) show a funnel icon; only real searches (Streaming, the finders) show a magnifier. The Radio/Podcasts finders are **Find Stations…** / **Find Podcasts…**. Audiobooks are listed most recently played first (unfinished before finished), then alphabetically. Saving a queue as a playlist keeps the local songs and reports what it had to leave out (e.g. *"1 cued streamed track and 1 cued podcast episode left out"*).
- **Dialogs:** one look and one behaviour for every modal — New Playlist, Add to Playlist, Save Queue, Find Stations, Subscribe and the shared confirm/alert dialog that replaced the browser's `confirm()`/`alert()` (delete playlist, remove station, unsubscribe, validation messages). Esc closes the topmost dialog; a backdrop click never does.
- **Performance:** the background spectrum visualiser was removed (it forced a full re-blur of the glass panels on every frame); the glass panels' backdrop blur was reduced (24 → 14 px). Together with the virtualised Music list this removes the lag seen with large libraries.
- **Compact transport:** the player controls are sized like the rest of the UI (44 px play, 36 px prev/next, 30 px skips) on a slim bar; the old "Deck_1.A" header is gone.
- **Long descriptions** (podcast blurbs, episode notes) clamp to a few lines with a **More / Less** link that appears only when the text actually overflows.

#### Adding a Source
1. `frontend/sources.js` — add an entry to `SOURCE_KINDS` (`label`, `icon`, `deckLabel`, `live`, `seekable`, `speed`, `noun`) plus a branch in `describeSource()` / `buildSourcePayload()`. Now Playing, the thumbnail badge, the skip/speed controls and the deck label pick it up automatically.
2. `frontend/index.html` + `app.js` — add a tab/view and one entry in the `views` registry (`switchView` iterates it); handle the new play command in `_handleCommand`, start playback via `_beginSourcePlayback(source, url)`, and teach `_playQueueItem` the new queue `kind`.
3. `backend/main.py` — add the type to `SOURCE_TYPES` (and `SEEKABLE_SOURCE_TYPES` if it can seek), a branch in `ClientConn.source_summary()` and `_queue_entry_label()`, tools + prompt text, and (if the media is remote) a same-origin relay endpoint like the radio/podcast/streaming ones.
4. Tests: `backend/tests/test_frontend.py` checks that every server command has a `case` in `app.js`, every declared tool has a handler, and every `.env` key is documented; add a module like `test_audiobooks.py` / `test_streaming.py` and a `frontend/tests/*.test.mjs` for the pure helpers.

### AI State Machine

```
offline ──► connecting ──► idle ──► listening ──► processing ──► speaking ──► idle
                             │                                     │
                             └──────────── tool_call ──────────────┘
```

| State | Emoji | Dot Color | Description |
|---|---|---|---|
| `offline` | ⚫ | Gray | Inactive / Session closed |
| `connecting` | 🔄 | Yellow (blink) | Establishing Gemini session |
| `listening` | 👂 | Blue (breathe) | Microphone streaming active |
| `processing` | 🧠 | Orange | Speech detected / Turn finishing |
| `speaking` | 🗣️ | Green (pulse) | DJ voice playback in progress |
| `tool_call` | ⚡ | Purple (fast blink) | Executing playlist/playback tools |
| `idle` | 💤 | Gray-blue | Waiting / Auto-disconnect countdown |
| `error` | ❌ | Red | Error encountered |
| `disconnecting`| 🔌 | Orange (blink) | Tearing down session |

---

## 🧪 Running Tests

A complete automated test suite is located in `backend/tests/`. Tests validate playlist fuzzy matching, spoken-name resolution, all voice tool operations (playlists, queue, radio, podcasts, audiobooks, streaming) against mocked WebSockets, the radio/podcast HTTP API and stream relays against a mocked upstream (ICY demuxing, `.pls` following, Range pass-through, error paths), RSS parsing, and a frontend↔backend contract (every server command has a handler in `app.js`, every element id exists in `index.html`, every declared tool has a handler, every `.env` key is documented).

- `test_audiobooks.py` builds a **real miniature audiobook library** in a temp folder (valid tagged MP3s: `Author/Book/CD 1|CD 2`, an untagged folder with `cover.jpg`, a loose single file) and checks the scanner (grouping, disc merging, tag vs. folder names, natural chapter order, stable ids), progress persistence and finished detection, spoken chapter resolution (*continue*, *chapter 5*, *part two*, *next*, *previous*, *from the beginning*, title words), the HTTP API (listing, Range streaming, cover art, progress, rescan) and the voice tools.
- `test_streaming.py` mocks the keyless services (iTunes Search/Lookup, Deezer) and stubs the YouTube Music tools, and checks each provider's search and error isolation, parallel search, album tracks, audio resolution (clips, yt-dlp caching and failures, missing tools), the audio relay with Range pass-through and yt-dlp headers, and every streaming voice tool.
- `test_startup.py` covers the tag cache (hits/misses, changed and removed files, corrupt cache files), scanner progress, the `/api/status` + `/api/tracks` 503 contract the loading message relies on, and the rescan endpoint.
- Pure frontend helpers in `frontend/sources.js` are tested with `node --test` (invoked from pytest; skipped if Node is not installed).

No test touches `.env`, your music or audiobook folders, or the real `playlists.json` / `radio_stations.json` / `podcasts.json` / `audiobooks.json`, and no network access is needed.

### Quick Run (Windows)
Double-click `run_tests.bat` or run:
```bat
run_tests.bat
```

### Manual Command
```bash
# Inside the virtual environment
python -m pytest backend/tests -v
```

---

## ⚠️ Known Issues & Considerations

- **Browser Audio Autoplay:** Web Audio requires a user interaction (click/touch) before playing sounds; startup jingles and mic capture automatically unlock on first gesture.
- **Browser Compatibility:** Voice invocation uses the Web Speech API (`webkitSpeechRecognition`), supported primarily on Google Chrome and Microsoft Edge.
- **Local Network Scope:** The server binds `0.0.0.0` with wide-open CORS for ease of local testing. Do not expose directly to public networks without authentication.
- **Album Artwork Fallback:** If local files lack embedded ID3/Vorbis APIC pictures, the server falls back to iTunes Search API queries (cached with an LRU in memory).
- **Radio Stream Formats:** Direct MP3/AAC/Ogg streams (and `.pls`/`.m3u` playlists pointing at one) are supported. HLS (`.m3u8`) streams are rejected (415) because a bare `<audio>` element cannot play them, and legacy SHOUTcast v1 servers that answer `ICY 200 OK` instead of HTTP cannot be relayed (502). Live radio has no pause/resume: the ⏹ button drops the stream and ▶ re-tunes to the live edge.
- **Online Directories:** Station and podcast discovery use the public radio-browser.info and Apple podcast search APIs (no keys). Their availability is outside this project's control; set `ONLINE_DIRECTORIES=false` to work fully offline with stations/feeds added by URL.
- **Local commercial stations:** station search queries two directories — radio-browser.info (community-maintained) and, unless `TUNEIN_DIRECTORY=false`, TuneIn's long-standing but *unofficial* OPML endpoints, which cover most local commercial stations (results are badged *TuneIn*; their stream link is resolved to a direct MP3/AAC stream when saved, HLS-only stations are refused with a 415). A station may still be missing, listed under its call sign, or HLS-only. Its stream checkers run in Europe, so **geo-blocked stations (common for Canadian commercial radio) are flagged "broken" there** — searches therefore never hide those; they appear with an *unverified* badge and usually play fine locally. Search is tolerant of brand spellings (`Jack 92.9` / `jack 929`, `Q104` / `Q 104`, `96.5 the breeze`, `move 100.1` vs a listing of "Move 100") and accepts an optional **city/region** (stations whose name, tags or state mention it — leave the search box empty to browse a city) and country; by voice, *"a Halifax station"* passes the place automatically. If a station still isn't listed, use **Find Stations → Add a station by stream URL** with the MP3/AAC address from the broadcaster's "listen live" page.
- **Playing a directory station saves it.** The relay only serves saved stations, so tuning in to a search result (by voice or ▶ in the finder) adds it to your stations first; remove it again with ✕ or *"remove the … station"*.
- **Podcast Positions:** Resume positions are stored in the browser's `localStorage` (per browser profile), not on the server. (Audiobook progress, by contrast, is stored server-side in `backend/audiobooks.json` so the DJ can answer *"how far am I?"*.)
- **Streaming sources:** Apple Music and Deezer expose only **30-second previews** without an account, so those sections play clips (and a song without a clip is shown greyed out). **YouTube Music** plays full songs through the unofficial third-party tools `ytmusicapi` and `yt-dlp`; YouTube changes its site regularly, so keep `yt-dlp` up to date (`pip install -U yt-dlp`) if that section stops working, and expect a few seconds' delay before a YouTube song starts (the stream URL is resolved on first play and cached for a few hours). Streamed tracks cannot be added to local playlists and playback speed does not apply to them.
- **First start with a large library:** the first scan reads every file's tags (a few ms each) — the Library tab shows the progress; subsequent starts use `backend/library_cache.json` and only read new or changed files. Delete the cache files to force a full re-read.
- **Audiobooks:** Chapters are ordered by disc/track tags, then natural filename order (`Part 2` before `Part 10`). A book whose folder has no tags gets its title from the folder name and *Unknown Author*; `cover.jpg` / `folder.jpg` in the book folder is used as artwork. Single-file `.m4b` books play as one chapter (embedded chapter markers are not parsed). Files added while the server runs appear after **↻ Rescan** (or a restart).
- **Starter Picks:** The stations and podcasts seeded on first run are a curated list of public streams/feeds (`DEFAULT_STATIONS` in `radio.py`, `DEFAULT_PODCASTS` in `podcasts.py`). Broadcasters change URLs occasionally; a failing starter shows a toast (radio) or a feed error (podcast) and can simply be removed. Starter podcasts are seeded *without* episodes and fetched in the background at start-up (or on first open/play), so first run works offline too. They are only seeded when the data files don't exist yet — deleting them all does not bring them back.

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).

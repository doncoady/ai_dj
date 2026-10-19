# AI DJ 🎧

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)](https://fastapi.tiangolo.com/)
[![Gemini Live API](https://img.shields.io/badge/Google%20GenAI-Live%20API-4285F4.svg)](https://ai.google.dev/)

A real-time, voice-interactive AI DJ prototype powered by Google's **Gemini Multimodal Live API** and the **Web Audio API**. Control your local music library hands-free using natural voice commands, wake-phrase activation ("Hey DJ"), and real-time audio interaction.

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
- **Protocol Version:** 3 (strict client-server handshake on connect)

### Core Priorities
1. **Audio Performance:** Smooth ducking and fading without pops, clicks, or glitches.
2. **UI Responsiveness:** Low-latency off-thread mic capture using `AudioWorklet` (no deprecated `ScriptProcessorNode`).
3. **Security:** API keys remain strictly server-side in `.env`; local file paths are abstracted behind stable stream IDs.
4. **Reliable State Management:** Single-owner turn completion (`TurnManager`), activity watchdogs, and safety resets.

---

## 🚀 Key Features

- **Voice Invocation ("Hey DJ") & Talk Live:** Activate the DJ hands-free using Web Speech wake-word recognition or toggle the **Talk Live** button.
- **Bi-directional Live Voice Streaming:** 16kHz PCM audio streaming to Gemini with incoming 24kHz DJ voice playback via Web Audio.
- **Dynamic Mix Curation:** Ask the DJ to create themed playlists (e.g., "play some 80s rock" or "make a chill late-night mix") curated on-the-fly from your local library metadata.
- **Natural Playlist & Queue Management:** Voice tools allow creating, describing, renaming, adding/removing tracks, queueing, and deleting playlists with verbal confirmation.
- **Intelligent Audio Ducking:** Music volume automatically ducks to 15% when invoking the DJ or speaking, and smoothly fades back up over 1.5s when speech concludes.
- **Accordion Deck UI:** Visual feedback with animated canvas visualizer, collapsible playlist/queue cards, now-playing badges, and responsive glassmorphic design.

---

## 🏗 Architecture

```
┌─────────────────────────────────┐           WebSocket            ┌─────────────────────────────────┐        Gemini Live API
│          Web Frontend           │◄═════════════════════════════►│          Python Backend         │◄═════════════════════════►  Google
│      (HTML5 / CSS3 / ES6)       │             /ws/live           │       (FastAPI + Uvicorn)       │     (google-genai SDK)
│                                 │                                │                                 │
│   • AudioWorklet (16kHz PCM mic)│         HTTP GET (Range)       │   Serves:                       │
│   • Web Audio Graph + Ducking   │◄─── /api/tracks/:id/stream ────┤   • Local audio file streams    │
│   • State Machine & TurnManager │                                │   • Track metadata & artwork    │
│   • Canvas Audio Visualizer     │                                │   • Frontend static assets      │
└─────────────────────────────────┘                                └─────────────────────────────────┘
                                                                                    │
                                                                                    ▼
                                                                            Local Filesystem
                                                                          (e.g., C:\Users\...\Music)
```

### Security Model
- **API Keys:** Stored in `backend/.env` and accessed exclusively by the backend service.
- **Path Abstraction:** Absolute filesystem paths are never exposed to the client; tracks are identified by stable hashes (`t_` + MD5 slice).
- **Network Scope:** Server binds `0.0.0.0` with CORS enabled for local prototype flexibility (intended for local network use).

---

## 📁 File Structure

```
ai_dj/
├── backend/
│   ├── tests/                 # pytest test suite for playlists & voice tools
│   ├── .env.example           # Template for environment variables
│   ├── .env                   # Local secrets (API keys, paths; git-ignored)
│   ├── main.py                # FastAPI server, Gemini Live proxy, scanner, tools
│   ├── playlists.py           # Playlist persistence, fuzzy name matching & curation
│   ├── playlists.json         # Persisted user & AI playlists (git-ignored)
│   └── requirements.txt       # Python dependencies
├── frontend/
│   ├── app.js                 # Frontend application logic, Web Audio graph, TurnManager
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

Activate the AI DJ by saying **“Hey DJ”** or clicking **Talk Live**. When you finish speaking, Gemini automatically detects end-of-speech and responds with voice and tool actions.

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

---

## ⚙ Configuration

### `backend/.env`
Create a `.env` file in the `backend/` directory based on `backend/.env.example`:

```env
# Google Gemini API Key (required)
GEMINI_API_KEY=AIzaSy...your_gemini_api_key

# Path to local audio library (required)
MUSIC_DIR=C:\Users\YourUser\Music

# Gemini Live Model (optional, defaults to gemini-3.8-live)
GEMINI_LIVE_MODEL=gemini-3.8-live

# Track search tool configuration (optional, defaults to true)
USE_SEARCH_TOOL=true

# Logging levels (optional)
HTTP_ACCESS_LOG=false
LOG_LEVEL=info
```

### Key Client Constants (`frontend/app.js`)
Configured in the `CONFIG` object inside [frontend/app.js](file:///d:/Projects/ai_dj/frontend/app.js):
- `PROTOCOL_VERSION: 3`: Handshake version matching `backend/main.py`
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
| `/api/tracks` | GET | Returns scanned library tracks with metadata |
| `/api/tracks/{id}/stream` | GET | Streams audio file with full **HTTP Range** support (seeking) |
| `/api/tracks/{id}/art` | GET | Returns embedded artwork (ID3 APIC / FLAC picture) with iTunes fallback |
| `/api/playlists` | GET, POST | List all playlists / Create new playlist |
| `/api/playlists/{id}` | GET, PUT, DELETE | Retrieve, update, or remove a playlist |
| `/api/playlists/{id}/tracks` | POST | Add track IDs to a playlist |
| `/api/playlists/{id}/tracks/{tid}` | DELETE | Remove a specific track from a playlist |
| `/static/*` | GET | Static assets (`app.js`, `style.css`, `pcm-processor.js`) |
| `/ws/live` | WebSocket | Bidirectional Gemini Live API proxy |

### WebSocket Protocol (`/ws/live`)

- **Handshake:** First client message must be `{"type":"hello","version":3}`. The server responds with `{"type":"ready"}` or disconnects with `version_mismatch`.
- **Client → Server:**
  - `{"type":"start"}`: Initialize Gemini Live session.
  - `{"type":"stop"}`: Tear down session cleanly.
  - `{"type":"wake_text", "text": "..."}`: Words captured right after wake word.
  - `{"type":"playback_state", ...}`: Sends current track, queue, and playlist state.
  - `[Binary ArrayBuffer]`: 16kHz Float32-to-Int16 PCM microphone frames.
- **Server → Client:**
  - `[Binary ArrayBuffer]`: 24kHz Int16 PCM Gemini speech audio chunks.
  - `{"type":"ai_state", "state": "...", "detail": "..."}`: Real-time DJ state update.
  - `{"type":"command", "action": "...", ...}`: Playback control dispatch (e.g. `playTrack`, `loadAndPlayPlaylist`).
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
- `describeQueue()`, `describeNowPlaying()`
- `saveQueueAsPlaylist(name?, includeCurrentTrack?)`

---

## 🎨 Frontend Architecture

### Audio Processing & Ducking
- **AudioContext Graph:** Shared audio context routes HTML `<audio>` through a master `GainNode` and dynamics compressor.
- **Microphone Pipeline:** `navigator.mediaDevices.getUserMedia` connected to an `AudioWorkletNode` running [frontend/pcm-processor.js](file:///d:/Projects/ai_dj/frontend/pcm-processor.js), streaming raw Int16 PCM frames over WebSocket.
- **DJ Speech Synthesizer:** Streams 24kHz Int16 PCM chunks into dynamically scheduled Web Audio buffers with jitter smoothing and instant cancellation on barge-in.
- **Logical Ducking:** `AudioManager` maintains logical gain targets, executing smooth exponential fades without reading back hardware parameters.

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

A complete automated test suite is located in `backend/tests/`. Tests validate playlist fuzzy matching, spoken-name resolution, and all voice tool operations against mocked WebSockets and isolated temporary storage.

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

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).

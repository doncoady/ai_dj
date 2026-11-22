/**
 * AI DJ — Frontend (V7.6)
 *
 * V7.6: "Library" tab is "Music"; Playlists moved into the player column
 *       under the cue; the cue grows to the room available (summary card
 *       hides while open) and ▶ on a cued row plays it without dropping the
 *       rows above. Drag & drop: Music rows/groups, playlist rows and
 *       Streaming results onto the cue or a playlist card; reorder within
 *       the cue and playlists. Draggable, snapping tab carousel. Newest
 *       podcast episodes card (+ voice tools). Finder buttons relabelled,
 *       funnel icons on filters, audiobooks sorted by recency.
 *
 * V7.5: The streaming tab is now "Streaming": online sources as sub-sections
 *       — YouTube Music (third-party ytmusicapi + yt-dlp on the server) first,
 *       then Apple Music and Deezer previews. Everything is relayed through
 *       the deck; there is no embedded third-party player any more. Sections
 *       page with "More results"; only headers carry the "Previews" label.
 *       Browse tabs are a horizontal carousel (‹ › buttons, active tab kept
 *       centred) in a Browse column as wide as the player. Playlist rows lost
 *       the "From here" button. Source type 'stream'.
 *
 * V7.4: Library Expand/Collapse-all no longer resets the sort (the button
 *       shared .sort-btn with the sort keys) and keeps the viewed group in
 *       place. A finished streamed clip continues the queue or stops — never
 *       the local library.
 *
 * V7.3: Fix round. Loading message with scan progress while the server reads
 *       tags (polls /api/tracks instead of giving up; socket connects first;
 *       everything else loads in parallel). Queue "Play now" always visible.
 *       Audiobook card / chapter buttons turn into ⏸ Pause while playing.
 *       Transport controls are fixed circles that wrap instead of squashing.
 *
 * V7.2: Two new sources. AUDIOBOOKS: an Audiobooks tab lists the books found
 *       in AUDIOBOOKS_DIR (cover, author, narrator, length, progress), expands
 *       to chapters, resumes where you left off (progress is kept on the
 *       server so the DJ can "continue my book"), auto-advances chapters and
 *       reuses the podcast skip/speed controls and the episode panel.
 *       STREAMING tab (first version). New server commands
 *       playAudiobookChapter, playStreamTracks, queueStreamTracks.
 *       Protocol v5.
 *
 * V7.1: Polish pass. Finder modals no longer close on a backdrop click;
 *       New Playlist is a modal (name + optional description) instead of
 *       prompt(); "+ Cue" on playlist cards, podcast cards and episode rows —
 *       the queue now holds podcast episodes next to songs; Up Next is
 *       expanded by default; list rows select on click / play on ▶ or
 *       double-click (Library, Radio, playlist tracks, episodes); all
 *       expand/collapse controls are chevrons; station search tolerates
 *       spacing/word order ("Move100", "96.5 the breeze").
 *
 * V7.0: Layout overhaul. Compact top bar (brand · DJ status readout · Live
 *       toggle · volume) instead of a half-screen DJ panel; the deck splits
 *       into a NOW PLAYING column and a BROWSE column with one tab per source
 *       (Library, Playlists, Radio, Podcasts — registry-driven so future
 *       sources are one entry + one tab). Fixed artwork tile with a kind badge
 *       replaces the spinning disc. Podcast episode panel (number • date •
 *       length • time left, expandable show notes). Starter picks: stations
 *       and podcasts pre-loaded on first run, badged, with a dismissable note
 *       explaining how to add more (GUI and voice). Station finder browses
 *       the directory by genre chips / top stations as well as search.
 *
 * V6.9: Live radio & podcasts. New Radio / Podcasts deck tabs (saved
 *       stations with directory search, podcast subscriptions with episode
 *       lists, resume positions and ↺15 / 30↻ skip buttons). The deck plays a
 *       "source" (radio station or podcast episode) through the same <audio>
 *       element via same-origin backend relays, so ducking and the visualiser
 *       keep working. Radio shows a LIVE badge, the station's on-air song
 *       title (ICY metadata polled from the server) and stop/re-tune instead
 *       of pause/resume. playback_state reports the active `source`; new
 *       server commands playRadio, playPodcastEpisode, seekRelative,
 *       radioStationsChanged, podcastsChanged. Protocol v4.
 *
 * V6.8: Ready jingle (synthesised, autoplay-safe). Salient active-playlist
 *       cues: now-playing pill (green / amber one-off), NOW PLAYING card pill
 *       with equalizer bars, "cued • <playlist>" count; a played-out playlist
 *       is no longer active. "Voice invocation" card removed from the GUI
 *       (wake word still works).
 *
 * V6.7: Talk Live is a strict two-look toggle (no debounce dead-zone, no
 *       per-state icon); in-flight mic warm-up abandons itself if the session
 *       ended (sessionGen). Playlists and the queue share one accordion
 *       "👁 View / ▴ Hide" control that expands cards in place — the separate
 *       playlist detail page and the Up Next tab are gone.
 *
 * V6.6: Talk Live is a two-state switch (Talk Live / End Live) and never
 *       submits a turn — Gemini's VAD ends the user's speech. Repeated or
 *       accidental clicks can only start/stop sessions, so they can no longer
 *       hand the DJ a noise-only turn to "answer". Listening timeout ends a
 *       session with an idle open mic. New Up Next tab/view for the queue;
 *       voice clearQueue / removeFromQueue commands.
 *
 * V6.5: Music ducks the instant the DJ is invoked (Talk Live / "Hey DJ" /
 *       follow-up), not when the first voice chunk arrives. Talk Live is
 *       debounced and too-short turns are cancelled instead of submitted
 *       (double-click no longer sends an empty turn that Gemini guessed at).
 *       playback_state is re-sent on ready/start so the server never works
 *       from an empty deck snapshot. Playlist names wrap instead of clipping.
 *
 * V6.4: Voice playlist management. Handles the `playlistsChanged` command
 *       (refresh Playlists tab / detail view / active-playlist badge + toast
 *       after AI DJ creates, edits, renames or deletes a playlist by voice)
 *       and `queuePlaylist` (append a saved playlist to the queue).
 *
 * V6.3 Fix: Auto-drop from listening now routes through processing first
 *      (listening → processing → speaking/tool_call).
 *      Voice PCM arriving while listening also auto-drops properly.
 */

import {
  SKIP_BACK_S, SKIP_FWD_S, RADIO_NOW_POLL_MS, PODCAST_CATEGORIES, STATION_GENRES_FALLBACK, BOOK_PROGRESS_SAVE_MS,
  describeSource, sourceKind, buildSourcePayload, formatClock, formatDuration, formatEpisodeDate,
  episodeMetaLine, remainingLabel, shouldShowStarterNote,
  resumePositionFor, rememberPosition, loadPositions, savePositions, episodeProgress,
  filterStations, filterPodcasts, filterAudiobooks, withCacheBuster, clampSeek,
  chapterMetaLine, bookProgressLabel, resumePointFor, nextChapter, previousChapter, sortAudiobooks,
  STREAM_PROVIDERS, STREAM_PROVIDER_ORDER, providerName, streamPlayable, streamMetaLine, streamQueueItem, formatMs,
} from './sources.js';

const STARTER_NOTE_KEY = 'aidj.starterNoteDismissed';   // { radio: true, podcast: true }

/** Drag-and-drop MIME types (HTML5 DnD): what a dragged row carries. */
const DND = {
  TRACKS: 'application/x-aidj-tracks',     // {trackIds: [...], label} — library rows/groups, playlist rows -> cue / playlists
  STREAM: 'application/x-aidj-stream',     // {track} — a Streaming result -> cue
  QUEUE: 'application/x-aidj-queue',       // "<index>" — reorder within the cue
  PLTRACK: 'application/x-aidj-pltrack',   // {playlistId, trackId} — reorder within a playlist
};

// ==========================================
// CONFIGURATION
// ==========================================
const CONFIG = {
  PROTOCOL_VERSION: 5,
  WS_RECONNECT_BASE_MS: 1000,
  WS_RECONNECT_MAX_MS: 30000,
  WS_HEARTBEAT_INTERVAL_MS: 30000,
  SESSION_TIMEOUT_MS: 120000,
  DUCK_LEVEL: 0.15,
  DUCK_FADE_S: 0.15,
  FADE_UP_S: 1.5,
  VOICE_SAMPLE_RATE: 24000,
  MIC_SAMPLE_RATE: 16000,
  ANALYSER_FFT_SIZE: 512,
  VOLUME_THROTTLE_MS: 50,
  // Mic open with no DJ response for this long = nobody is talking; end the
  // session rather than leave an open mic streaming room noise to Gemini.
  LISTEN_TIMEOUT_MS: 15000,
  // Keep polling /api/tracks (503 = still scanning) up to this long.
  LIBRARY_LOAD_TIMEOUT_MS: 30 * 60 * 1000,
};

// ==========================================
// UTILITIES
// ==========================================
const $ = (sel) => document.getElementById(sel);

function throttle(fn, ms) {
  let last = 0;
  return (...args) => {
    const now = Date.now();
    if (now - last >= ms) {
      last = now;
      fn(...args);
    }
  };
}

function escapeHtml(str) {
  if (!str) return '';
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

function formatTime(seconds) {
  if (!isFinite(seconds) || isNaN(seconds)) return '0:00';
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, '0')}`;
}

function clamp(n, min, max) {
  return Math.max(min, Math.min(max, n));
}

// ==========================================
// STATE MACHINE
// ==========================================
const VALID_TRANSITIONS = {
  offline: ['connecting'],
  connecting: ['idle', 'error', 'offline'],
  idle: ['listening', 'processing', 'offline', 'error'],
  listening: ['processing', 'offline', 'error'],
  processing: ['speaking', 'tool_call', 'idle', 'offline', 'error'],
  tool_call: ['speaking', 'idle', 'offline', 'error'],
  speaking: ['idle', 'listening', 'offline', 'error'],
  error: ['offline', 'connecting'],
  disconnecting: ['offline', 'error']
};

const AI_STATES = {
  offline: { emoji: '⚫', name: 'Offline', dot: 'dot-offline', color: '#666' },
  connecting: { emoji: '🔄', name: 'Connecting', dot: 'dot-connecting', color: '#FFC107' },
  listening: { emoji: '👂', name: 'Listening', dot: 'dot-listening', color: '#4285F4' },
  processing: { emoji: '🧠', name: 'Processing', dot: 'dot-processing', color: '#FF9800' },
  speaking: { emoji: '🗣️', name: 'Speaking', dot: 'dot-speaking', color: '#00E676' },
  tool_call: { emoji: '⚡', name: 'Executing', dot: 'dot-tool', color: '#E040FB' },
  idle: { emoji: '💤', name: 'Idle', dot: 'dot-idle', color: '#90CAF9' },
  error: { emoji: '❌', name: 'Error', dot: 'dot-error', color: '#F44336' },
  disconnecting: { emoji: '🔌', name: 'Closing', dot: 'dot-disconnecting', color: '#FF5722' }
};

class SessionFSM extends EventTarget {
  constructor() {
    super();
    this._state = 'offline';
    this._detail = '';
    this._guardLock = false;
  }

  get state() { return this._state; }
  get detail() { return this._detail; }
  get isLocked() { return this._guardLock; }

  canTransition(to) {
    return VALID_TRANSITIONS[this._state]?.includes(to) ?? false;
  }

  transition(to, detail = '') {
    if (!this.canTransition(to)) {
      console.warn(`[FSM] Invalid transition: ${this._state} → ${to}`);
      return false;
    }
    const from = this._state;
    this._state = to;
    this._detail = detail;
    console.log(`[FSM] ${from} → ${to}${detail ? ': ' + detail : ''}`);
    this.dispatchEvent(new CustomEvent('change', { detail: { from, to, detail } }));
    return true;
  }

  async guard(fn) {
    if (this._guardLock) {
      console.warn('[FSM] Guard rejected: already locked');
      return undefined;
    }
    this._guardLock = true;
    try {
      return await fn();
    } finally {
      this._guardLock = false;
      console.log('[FSM] Guard released');
    }
  }
}

// ==========================================
// AUDIO MANAGER (Single Context)
// ==========================================
class AudioManager extends EventTarget {
  constructor() {
    super();
    this.ctx = new (window.AudioContext || window.webkitAudioContext)();

    this.masterGain = this.ctx.createGain();
    this.masterGain.connect(this.ctx.destination);

    this.analyser = this.ctx.createAnalyser();
    this.analyser.fftSize = CONFIG.ANALYSER_FFT_SIZE;
    this.analyser.smoothingTimeConstant = 0.8;
    this.analyser.connect(this.masterGain);

    this.musicGain = this.ctx.createGain();
    this.musicGain.connect(this.analyser);

    this.voiceGain = this.ctx.createGain();
    this.voiceGain.connect(this.masterGain);

    this._musicSource = null;
    this._userVolume = 1.0;
    this._voiceGen = 0;
    this._activeVoiceNodes = new Set();
    this._nextVoiceTime = 0;
  }

  initMusicPlayer(mediaEl) {
    if (this._musicSource) return;
    this._musicSource = this.ctx.createMediaElementSource(mediaEl);
    this._musicSource.connect(this.musicGain);
  }

  get currentTime() {
    return this.ctx.currentTime;
  }

  setVolume(value, { ducked = false } = {}) {
    this._userVolume = clamp(value, 0, 1);
    const target = ducked ? this._userVolume * CONFIG.DUCK_LEVEL : this._userVolume;
    const now = this.ctx.currentTime;
    this.musicGain.gain.cancelScheduledValues(now);
    this.musicGain.gain.setValueAtTime(this.musicGain.gain.value, now);
    this.musicGain.gain.linearRampToValueAtTime(
      target,
      now + (ducked ? CONFIG.DUCK_FADE_S : CONFIG.FADE_UP_S)
    );
  }

  duck() {
    this.setVolume(this._userVolume, { ducked: true });
  }

  fadeUp() {
    this.setVolume(this._userVolume, { ducked: false });
  }

  fadeTrackUp(targetVolume, durationS = 1.2, isDucked = false) {
    this._userVolume = clamp(targetVolume, 0, 1);
    const target = isDucked ? this._userVolume * CONFIG.DUCK_LEVEL : this._userVolume;
    const now = this.ctx.currentTime;
    this.musicGain.gain.cancelScheduledValues(now);
    this.musicGain.gain.setValueAtTime(this._userVolume * CONFIG.DUCK_LEVEL, now);
    this.musicGain.gain.linearRampToValueAtTime(target, now + durationS);
  }

  playChime(on) {
    if (this.ctx.state === 'suspended') this.ctx.resume();

    const osc = this.ctx.createOscillator();
    const gain = this.ctx.createGain();
    osc.connect(gain);
    gain.connect(this.masterGain);
    osc.type = 'sine';
    const now = this.ctx.currentTime;

    if (on) {
      osc.frequency.setValueAtTime(440, now);
      osc.frequency.exponentialRampToValueAtTime(880, now + 0.1);
    } else {
      osc.frequency.setValueAtTime(880, now);
      osc.frequency.exponentialRampToValueAtTime(440, now + 0.1);
    }
    gain.gain.setValueAtTime(0, now);
    gain.gain.linearRampToValueAtTime(0.3, now + 0.05);
    gain.gain.linearRampToValueAtTime(0, now + 0.3);
    osc.start(now);
    osc.stop(now + 0.35);
  }

  /**
   * Short "station ident" played once when AI DJ is ready: three rising
   * triangle-wave notes over a soft low pad, ~0.7 s, synthesised in-graph so
   * there is no audio asset to load.
   */
  playStartupFx() {
    if (this.ctx.state !== 'running') return false;
    const now = this.ctx.currentTime;
    const out = this.ctx.createGain();
    out.gain.value = 0.22;
    out.connect(this.masterGain);

    // Low pad under the notes
    const pad = this.ctx.createOscillator();
    const padGain = this.ctx.createGain();
    pad.type = 'sine';
    pad.frequency.setValueAtTime(164.81, now);           // E3
    padGain.gain.setValueAtTime(0, now);
    padGain.gain.linearRampToValueAtTime(0.5, now + 0.08);
    padGain.gain.exponentialRampToValueAtTime(0.001, now + 0.75);
    pad.connect(padGain); padGain.connect(out);
    pad.start(now); pad.stop(now + 0.8);

    // Rising arpeggio: E5 → G#5 → B5
    [659.25, 830.61, 987.77].forEach((freq, i) => {
      const t = now + i * 0.11;
      const osc = this.ctx.createOscillator();
      const g = this.ctx.createGain();
      osc.type = 'triangle';
      osc.frequency.setValueAtTime(freq, t);
      g.gain.setValueAtTime(0, t);
      g.gain.linearRampToValueAtTime(0.8, t + 0.02);
      g.gain.exponentialRampToValueAtTime(0.001, t + (i === 2 ? 0.55 : 0.3));
      osc.connect(g); g.connect(out);
      osc.start(t); osc.stop(t + 0.6);
    });
    return true;
  }

  playVoicePCM(arrayBuffer) {
    if (this.ctx.state === 'suspended') this.ctx.resume();

    const pcm16 = new Int16Array(arrayBuffer);
    const floats = new Float32Array(pcm16.length);
    for (let i = 0; i < pcm16.length; i++) {
      floats[i] = pcm16[i] / 32768.0;
    }

    const buffer = this.ctx.createBuffer(1, floats.length, CONFIG.VOICE_SAMPLE_RATE);
    buffer.getChannelData(0).set(floats);

    const source = this.ctx.createBufferSource();
    source.buffer = buffer;
    source.connect(this.voiceGain);

    // A generation identifies a contiguous playback session. Do not advance it
    // per chunk: queued chunks must all be allowed to remove themselves when
    // they finish. `interruptVoice()` advances it to invalidate old callbacks.
    const gen = this._voiceGen;
    source.onended = () => {
      this._activeVoiceNodes.delete(source);
      if (gen === this._voiceGen && this._activeVoiceNodes.size === 0) {
        this.dispatchEvent(new CustomEvent('voiceEnded'));
      }
    };

    const now = this.ctx.currentTime;
    if (this._nextVoiceTime < now) this._nextVoiceTime = now + 0.05;

    source.start(this._nextVoiceTime);
    this._activeVoiceNodes.add(source);
    this._nextVoiceTime += buffer.duration;

    return this._nextVoiceTime - now;
  }

  interruptVoice() {
    this._voiceGen++;
    for (const src of this._activeVoiceNodes) {
      try { src.stop(); } catch (e) { }
    }
    this._activeVoiceNodes.clear();
    this._nextVoiceTime = this.ctx.currentTime;
  }

  stopAll() {
    this.interruptVoice();
    const now = this.ctx.currentTime;
    this.musicGain.gain.cancelScheduledValues(now);
  }

  async suspend() {
    if (this.ctx.state === 'running') await this.ctx.suspend();
  }

  async resume() {
    if (this.ctx.state === 'suspended') await this.ctx.resume();
  }
}

// ==========================================
// MIC MANAGER (Persistent Stream)
// ==========================================
class MicManager {
  constructor() {
    this.stream = null;
    this.ctx = null;
    this.worklet = null;
    this.source = null;
    this.isCapturing = false;
    this.prewarmPromise = null;
  }

  async prewarm() {
    if (this.prewarmPromise) return this.prewarmPromise;
    this.prewarmPromise = this._doPrewarm();
    return this.prewarmPromise;
  }

  async _doPrewarm() {
    try {
      console.log('[MicManager] Prewarming mic...');
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          sampleRate: CONFIG.MIC_SAMPLE_RATE,
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true
        }
      });
      this.ctx = new (window.AudioContext || window.webkitAudioContext)({
        sampleRate: CONFIG.MIC_SAMPLE_RATE
      });
      if (this.ctx.state === 'suspended') await this.ctx.resume();
      await this.ctx.audioWorklet.addModule('/static/pcm-processor.js');
      console.log('[MicManager] Prewarm complete');
      return true;
    } catch (e) {
      console.error('[MicManager] Prewarm failed:', e);
      this.dispose();
      throw e;
    }
  }

  start(onPCM) {
    if (this.isCapturing) {
      console.warn('[MicManager] Already capturing');
      return false;
    }
    if (!this.stream || !this.ctx) {
      console.error('[MicManager] Cannot start: stream not ready');
      return false;
    }

    this.source = this.ctx.createMediaStreamSource(this.stream);
    this.worklet = new AudioWorkletNode(this.ctx, 'pcm-processor');
    this.worklet.port.onmessage = (e) => {
      if (this.isCapturing) onPCM(e.data);
    };
    this.source.connect(this.worklet);
    this.isCapturing = true;
    console.log('[MicManager] Capture started');
    return true;
  }

  stop() {
    if (!this.isCapturing) return;
    console.log('[MicManager] Stopping capture');
    try {
      this.worklet?.port.postMessage('stop');
      this.worklet?.disconnect();
      this.source?.disconnect();
    } catch (e) {
      console.warn('[MicManager] Stop error:', e);
    }
    this.worklet = null;
    this.source = null;
    this.isCapturing = false;
  }

  dispose() {
    console.log('[MicManager] Disposing');
    this.stop();
    if (this.stream) {
      this.stream.getTracks().forEach(t => t.stop());
      this.stream = null;
    }
    if (this.ctx) {
      this.ctx.close().catch(() => { });
      this.ctx = null;
    }
    this.prewarmPromise = null;
  }
}

// ==========================================
// WAKE WORD LISTENER
// ==========================================
class WakeWordListener extends EventTarget {
  constructor() {
    super();
    this.Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    this.recognition = null;
    this.isRunning = false;
    this.shouldRun = false;
    this.mode = 'wake';
  }

  get supported() {
    return Boolean(this.Recognition);
  }

  start(mode = 'wake') {
    this.mode = mode;
    this.shouldRun = true;
    if (!this.supported) {
      this._notify('unsupported');
      return;
    }
    if (this.isRunning || this.recognition) return;

    const recognition = new this.Recognition();
    this.recognition = recognition;
    recognition.continuous = true;
    recognition.interimResults = false;
    recognition.lang = 'en-US';

    recognition.onstart = () => {
      this.isRunning = true;
      this._notify(this.mode === 'followup' ? 'listening_followup' : 'listening');
    };
    recognition.onresult = (event) => {
      for (let i = event.resultIndex; i < event.results.length; i++) {
        if (!event.results[i].isFinal) continue;
        const transcript = event.results[i][0].transcript.trim();
        if (this.mode === 'followup') {
          if (transcript) {
            this.stop();
            this.dispatchEvent(new CustomEvent('followup', { detail: { command: transcript } }));
          }
          return;
        }
        const wakeMatch = /\bhey\s+(?:ai\s+dj|dj|nova)\b/i.exec(transcript);
        if (wakeMatch) {
          // SpeechRecognition has already captured the request that followed
          // the wake phrase. Preserve it rather than making the user repeat it
          // after the Live session opens.
          const request = transcript
            .slice(wakeMatch.index + wakeMatch[0].length)
            .replace(/^[\s,.:;!\-]+/, '')
            .trim();
          this.stop();
          this.dispatchEvent(new CustomEvent('wake', { detail: { request } }));
          return;
        }
      }
    };
    recognition.onerror = (event) => {
      if (event.error === 'not-allowed' || event.error === 'service-not-allowed') {
        this.shouldRun = false;
        this._notify('blocked');
      } else if (event.error !== 'aborted' && event.error !== 'no-speech') {
        console.warn('[WakeWord] Recognition error:', event.error);
        this._notify('error');
      }
    };
    recognition.onend = () => {
      this.isRunning = false;
      this.recognition = null;
      if (this.shouldRun) window.setTimeout(() => this.start(this.mode), 400);
    };

    try {
      recognition.start();
    } catch (error) {
      console.warn('[WakeWord] Recognition could not start:', error);
      this.recognition = null;
      this._notify('error');
    }
  }

  stop() {
    this.shouldRun = false;
    if (this.recognition) this.recognition.abort();
    this.recognition = null;
    this.isRunning = false;
  }

  _notify(state) {
    this.dispatchEvent(new CustomEvent('status', { detail: state }));
  }
}

// ==========================================
// WEBSOCKET MANAGER
// ==========================================
class LiveSocket extends EventTarget {
  constructor(url, protocolVersion) {
    super();
    this.url = url;
    this.protocolVersion = protocolVersion;
    this.ws = null;
    this.queue = [];
    this.reconnectTimer = null;
    this.heartbeatTimer = null;
    this.attempt = 0;
    this.isManualClose = false;
    this.ready = false;
  }

  get isOpen() {
    return this.ws?.readyState === WebSocket.OPEN;
  }

  get isConnecting() {
    return this.ws?.readyState === WebSocket.CONNECTING;
  }

  connect() {
    if (this.isOpen || this.isConnecting) {
      console.log('[LiveSocket] Already open/connecting');
      return;
    }

    console.log(`[LiveSocket] Connecting… (attempt ${this.attempt})`);
    this.ws = new WebSocket(this.url);
    this.ws.binaryType = 'arraybuffer';

    this.ws.onopen = () => {
      console.log('[LiveSocket] Open');
      this.attempt = 0;
      this.ready = true;
      // This must precede any queued control messages so the server can
      // validate the protocol before a session is started.
      this.ws.send(JSON.stringify({ type: 'hello', version: this.protocolVersion }));
      this._startHeartbeat();
      this.flush();
      this.dispatchEvent(new CustomEvent('open'));
    };

    this.ws.onmessage = (e) => {
      this.dispatchEvent(new CustomEvent('message', { detail: e.data }));
    };

    this.ws.onerror = (e) => {
      console.error('[LiveSocket] Error:', e);
      this.dispatchEvent(new CustomEvent('error', { detail: e }));
    };

    this.ws.onclose = () => {
      console.log('[LiveSocket] Close');
      this.ready = false;
      this._stopHeartbeat();
      this.ws = null;
      this.dispatchEvent(new CustomEvent('close'));
      if (!this.isManualClose) {
        this._scheduleReconnect();
      }
    };
  }

  send(data) {
    if (this.isOpen) {
      this.ws.send(data);
    } else {
      console.log('[LiveSocket] Queueing message (not open)');
      this.queue.push(data);
    }
  }

  flush() {
    while (this.queue.length > 0 && this.isOpen) {
      this.ws.send(this.queue.shift());
    }
  }

  close() {
    console.log('[LiveSocket] Manual close');
    this.isManualClose = true;
    clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
    this.ready = false;
  }

  resetReconnect() {
    this.isManualClose = false;
    this.attempt = 0;
    clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
  }

  _scheduleReconnect() {
    const delay = Math.min(
      CONFIG.WS_RECONNECT_BASE_MS * (2 ** this.attempt),
      CONFIG.WS_RECONNECT_MAX_MS
    );
    this.attempt++;
    console.log(`[LiveSocket] Reconnect in ${delay}ms`);
    this.reconnectTimer = setTimeout(() => this.connect(), delay);
  }

  _startHeartbeat() {
    this._stopHeartbeat();
    this.heartbeatTimer = setInterval(() => {
      if (this.isOpen) {
        this.send(JSON.stringify({ type: 'ping' }));
      }
    }, CONFIG.WS_HEARTBEAT_INTERVAL_MS);
  }

  _stopHeartbeat() {
    if (this.heartbeatTimer) {
      clearInterval(this.heartbeatTimer);
      this.heartbeatTimer = null;
    }
  }
}

// ==========================================
// TRACK LIST RENDERER (virtualised + sortable)
// ==========================================
/**
 * Renders the Library as a windowed list: only the rows inside the scroll
 * viewport (plus a small overscan) exist in the DOM, so a 10k-track library
 * costs the same as a 30-track one. Rows are pooled and re-filled on scroll.
 * Row height is fixed (CSS .track-list.virtual .track-item) so positions are
 * arithmetic. Supports filter text and sort key/direction.
 */
class TrackListRenderer {
  constructor(container, { rowHeight = 52, overscan = 6 } = {}) {
    this.container = container;
    this.rowHeight = rowHeight;
    this.overscan = overscan;
    this.tracks = [];
    this.visible = [];            // [{ track, originalIndex }] after filter + sort
    this.currentId = null;
    this.selectedId = null;
    this.filterQuery = '';
    this.sortKey = '';            // '' = library order
    this.sortDir = 1;
    this.trackPool = [];          // recycled track rows
    this.groupPool = [];          // recycled group header rows
    this.expanded = new Set();    // expanded group keys (groups start collapsed)
    this.groups = [];             // [{ key, label, count }] for the current sort
    this.onCountChange = null;    // (visibleCount, totalCount, groupCount) => void
    this.onDragStart = null;      // (dragEvent, tracks[], label) => void — rows/groups are draggable to the cue and playlists
    this._rafPending = false;

    this.container.classList.add('virtual');
    this.spacer = document.createElement('div');
    this.spacer.className = 'vlist-spacer';
    this.rows = document.createElement('div');
    this.rows.className = 'vlist-rows';
    this.empty = document.createElement('div');
    this.empty.className = 'sources-empty';
    this.empty.style.display = 'none';
    this.container.replaceChildren(this.spacer, this.rows, this.empty);

    this.container.addEventListener('scroll', () => this._scheduleDraw(), { passive: true });
    window.addEventListener('resize', () => this._scheduleDraw());
    // The Library tab can be hidden (display:none → 0px tall) when data
    // arrives; redraw as soon as it gets a real size.
    if (typeof ResizeObserver !== 'undefined') {
      new ResizeObserver(() => this._scheduleDraw()).observe(this.container);
    }

    // Delegated row interaction: ▶ / + Cue / + 📑 buttons, click = select,
    // double-click or Enter = play.
    this.rows.addEventListener('click', (e) => {
      const group = e.target.closest('.track-group');
      if (group) {
        const key = group.dataset.groupKey;
        const tracks = this.groups.find(g => g.key === key)?.tracks || [];
        if (e.target.closest('.group-play-btn')) { e.stopPropagation(); window.djApp?.playTracks(tracks); return; }
        if (e.target.closest('.group-cue-btn')) { e.stopPropagation(); window.djApp?.queueTracks(tracks); return; }
        this.toggleGroup(key);
        return;
      }
      const row = e.target.closest('.track-item');
      if (!row) return;
      const index = parseInt(row.dataset.index, 10);
      const track = window.djApp?.tracks[index];
      if (!track) return;
      if (e.target.closest('.track-add-playlist-btn')) { e.stopPropagation(); window.djApp.openAddToPlaylistModal(track); return; }
      if (e.target.closest('.track-cue-btn')) { e.stopPropagation(); window.djApp.queueTrackById(track.id); return; }
      if (e.target.closest('.track-play-btn')) { e.stopPropagation(); window.djApp.playTrackByIndex(index); return; }
      this.setSelected(this.selectedId === track.id ? null : track.id);
    });
    this.rows.addEventListener('dblclick', (e) => {
      if (e.target.closest('button')) return;
      const index = parseInt(e.target.closest('.track-item')?.dataset.index, 10);
      if (!isNaN(index)) window.djApp?.playTrackByIndex(index);
    });
    this.rows.addEventListener('keydown', (e) => {
      const group = e.target.closest('.track-group');
      if (group && e.target === group && (e.key === 'Enter' || e.key === ' ')) {
        e.preventDefault();
        this.toggleGroup(group.dataset.groupKey);
        return;
      }
      const row = e.target.closest('.track-item');
      if (!row || e.target !== row) return;
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        const index = parseInt(row.dataset.index, 10);
        if (!isNaN(index)) window.djApp?.playTrackByIndex(index);
      } else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        const pos = parseInt(row.dataset.pos, 10) + (e.key === 'ArrowDown' ? 1 : -1);
        this.focusPosition(pos);
      }
    });
  }

  /** Main entry point (kept compatible with earlier callers). */
  render(tracks, currentId, filterQuery = '') {
    const q = (filterQuery || '').toLowerCase().trim();
    const changed = tracks !== this.tracks || q !== this.filterQuery || this._dirty;
    this.tracks = tracks;
    this.filterQuery = q;
    this.currentId = currentId;
    if (changed) {
      this._dirty = false;
      this._recompute();
    }
    this._draw();
  }

  setSort(key, dir) {
    if (key === this.sortKey && dir === undefined) {
      this.sortDir = -this.sortDir;           // same key again: flip direction
    } else {
      this.sortKey = key || '';
      this.sortDir = dir || 1;
      this.expanded.clear();                  // groups belong to a sort key; start collapsed
    }
    this._dirty = true;
    this.container.scrollTop = 0;
    this.render(this.tracks, this.currentId, this.filterQuery);
  }

  /** Redraw without recomputing (e.g. the tab just became visible). */
  refresh() {
    this._scheduleDraw();
  }

  /** Grouping follows the sort: letters for Title, values for Artist/Album/Genre. */
  get groupMode() {
    return this.sortKey || '';
  }

  get allCollapsed() {
    return this.groups.length > 0 && !this.groups.some(g => this.expanded.has(g.key));
  }

  toggleGroup(key) {
    const anchor = this._scrollAnchor();
    if (this.expanded.has(key)) this.expanded.delete(key);
    else this.expanded.add(key);
    this._dirty = true;
    this.render(this.tracks, this.currentId, this.filterQuery);
    this._restoreScrollAnchor(anchor);
  }

  /**
   * Expand/collapse every group while keeping the group the user is looking
   * at where it is (instead of jumping back to the top of the list).
   */
  toggleAllGroups() {
    const anchor = this._scrollAnchor();
    if (this.allCollapsed) this.groups.forEach(g => this.expanded.add(g.key));
    else this.expanded.clear();
    this._dirty = true;
    this.render(this.tracks, this.currentId, this.filterQuery);
    this._restoreScrollAnchor(anchor);
  }

  /** The group whose header/track is at the top of the viewport, and how far below the top it sits. */
  _scrollAnchor() {
    if (!this.groupMode || !this.visible.length) return null;
    const idx = Math.min(this.visible.length - 1, Math.max(0, Math.floor(this.container.scrollTop / this.rowHeight)));
    const entry = this.visible[idx];
    const key = entry.type === 'group' ? entry.group.key : this._groupKeyFor(entry.track);
    return { key, offset: this.container.scrollTop - idx * this.rowHeight };
  }

  _restoreScrollAnchor(anchor) {
    if (!anchor) return;
    const idx = this.visible.findIndex(e => e.type === 'group' && e.group.key === anchor.key);
    if (idx === -1) return;
    this.container.scrollTop = Math.max(0, idx * this.rowHeight + Math.min(anchor.offset, this.rowHeight - 1));
    this._scheduleDraw();
  }

  _groupKeyFor(track) {
    const mode = this.groupMode;
    if (mode === 'title') {
      const ch = (track.title || '').trim().charAt(0).toUpperCase();
      return /[A-Z]/.test(ch) ? ch : (/[0-9]/.test(ch) ? '0–9' : '#');
    }
    const v = (track[mode] || '').trim();
    return v || `(No ${mode})`;
  }

  setSelected(trackId) {
    this.selectedId = trackId;
    this._draw();
  }

  /** Scroll so the given track (by id) is in view. */
  scrollToTrack(trackId) {
    const pos = this.visible.findIndex(v => v.type === 'track' && v.track.id === trackId);
    if (pos < 0) return;
    const top = pos * this.rowHeight;
    const { scrollTop, clientHeight } = this.container;
    if (top < scrollTop || top + this.rowHeight > scrollTop + clientHeight) {
      this.container.scrollTop = Math.max(0, top - clientHeight / 2 + this.rowHeight / 2);
    }
  }

  focusPosition(pos) {
    if (pos < 0 || pos >= this.visible.length) return;
    const top = pos * this.rowHeight;
    const { scrollTop, clientHeight } = this.container;
    if (top < scrollTop) this.container.scrollTop = top;
    else if (top + this.rowHeight > scrollTop + clientHeight) this.container.scrollTop = top + this.rowHeight - clientHeight;
    this._draw();
    this.rows.querySelector(`.track-item[data-pos="${pos}"]`)?.focus();
  }

  _recompute() {
    const q = this.filterQuery;
    let list = this.tracks.map((track, originalIndex) => ({ track, originalIndex }));
    if (q) {
      list = list.filter(({ track }) =>
        track.title.toLowerCase().includes(q) ||
        track.artist.toLowerCase().includes(q) ||
        (track.genre || '').toLowerCase().includes(q) ||
        (track.album || '').toLowerCase().includes(q));
    }
    const trackCount = list.length;
    if (this.sortKey) {
      const key = this.sortKey, dir = this.sortDir;
      const collator = new Intl.Collator(undefined, { sensitivity: 'base', numeric: true });
      list.sort((a, b) => {
        const c = collator.compare(a.track[key] || '', b.track[key] || '');
        return c !== 0 ? c * dir : collator.compare(a.track.title, b.track.title);
      });
    }

    // Hierarchy: one header per group (in sort order); collapsed groups hide their tracks.
    this.groups = [];
    if (this.groupMode) {
      const items = [];
      let current = null;
      for (const entry of list) {
        const key = this._groupKeyFor(entry.track);
        if (!current || current.key !== key) {
          current = { key, label: key, count: 0, tracks: [] };
          this.groups.push(current);
          items.push({ type: 'group', group: current });
        }
        current.count++;
        current.tracks.push(entry.track);
        if (this.expanded.has(key)) items.push({ type: 'track', ...entry });
      }
      list = items;
    } else {
      list = list.map(entry => ({ type: 'track', ...entry }));
    }

    this.visible = list;
    this.spacer.style.height = `${list.length * this.rowHeight}px`;
    // While the server is still scanning, the loading banner speaks instead.
    this.empty.style.display = list.length || this.loading ? 'none' : '';
    this.empty.textContent = this.tracks.length === 0
      ? 'No tracks found in your music folder (MUSIC_DIR).'
      : 'No matching tracks.';
    if (this.onCountChange) this.onCountChange(trackCount, this.tracks.length, this.groups.length);
  }

  _scheduleDraw() {
    if (this._rafPending) return;
    this._rafPending = true;
    requestAnimationFrame(() => { this._rafPending = false; this._draw(); });
  }

  _draw() {
    const total = this.visible.length;
    const viewport = this.container.clientHeight || 400;
    const first = Math.max(0, Math.floor(this.container.scrollTop / this.rowHeight) - this.overscan);
    const count = Math.max(0, Math.min(total - first, Math.ceil(viewport / this.rowHeight) + this.overscan * 2));
    this.rows.style.transform = `translateY(${first * this.rowHeight}px)`;

    // Fill the window from two element pools (track rows / group headers) in order.
    const children = [];
    let t = 0, g = 0;
    for (let i = 0; i < count; i++) {
      const item = this.visible[first + i];
      if (item.type === 'group') {
        const el = this.groupPool[g] || (this.groupPool[g] = this._createGroupElement());
        g++;
        this._updateGroupElement(el, item.group, first + i);
        children.push(el);
      } else {
        const el = this.trackPool[t] || (this.trackPool[t] = this._createElement());
        t++;
        this._updateElement(el, item.track, item.originalIndex, first + i);
        children.push(el);
      }
    }
    this.trackPool.length = Math.max(t, Math.min(this.trackPool.length, 60));
    this.groupPool.length = Math.max(g, Math.min(this.groupPool.length, 30));
    this.rows.replaceChildren(...children);
  }

  _createGroupElement() {
    const el = document.createElement('div');
    el.className = 'track-group';
    el.tabIndex = 0;
    el.draggable = true;
    el.addEventListener('dragstart', (e) => {
      const group = this.groups.find(g => g.key === el.dataset.groupKey);
      if (group && this.onDragStart) this.onDragStart(e, group.tracks, `${group.label} (${group.count} tracks)`);
      else e.preventDefault();
    });
    el.innerHTML = `
      <span class="track-group-label"></span>
      <span class="track-group-count"></span>
      <button class="track-play-btn group-play-btn" title="Play this group (first track now, the rest cued)">▶ Play</button>
      <button class="track-cue-btn group-cue-btn" title="Cue every track in this group">+ Cue</button>
      <span class="view-toggle-btn group-toggle" aria-hidden="true"><span class="chevron2"></span></span>
    `;
    return el;
  }

  _updateGroupElement(el, group, pos) {
    const open = this.expanded.has(group.key);
    el.dataset.groupKey = group.key;
    el.dataset.pos = pos;
    el.classList.toggle('open', open);
    el.setAttribute('aria-expanded', String(open));
    el.querySelector('.group-toggle').classList.toggle('open', open);
    el.querySelector('.track-group-label').textContent = group.label;
    el.querySelector('.track-group-count').textContent = `${group.count} track${group.count === 1 ? '' : 's'}`;
    el.title = `${open ? 'Collapse' : 'Expand'} ${group.label}`;
  }

  _createElement() {
    const el = document.createElement('div');
    el.className = 'track-item';
    el.tabIndex = 0;
    el.draggable = true;
    el.addEventListener('dragstart', (e) => {
      const track = this.tracks[parseInt(el.dataset.index, 10)];
      if (track && this.onDragStart) this.onDragStart(e, [track], track.title);
      else e.preventDefault();
    });
    el.innerHTML = `
      <span class="track-lead eq-bars" aria-hidden="true"><i></i><i></i><i></i></span>
      <div class="track-info">
        <div class="track-title"></div>
        <div class="track-artist"></div>
      </div>
      <div class="track-genre"></div>
      <button class="track-play-btn" title="Play now">▶</button>
      <button class="track-add-playlist-btn" title="Add to playlist">+ 📑</button>
      <button class="track-cue-btn" title="Cue up to play next">+ Cue</button>
    `;
    return el;
  }

  _updateElement(el, track, index, pos) {
    const isActive = track.id === this.currentId;
    el.dataset.index = index;
    el.dataset.pos = pos;
    el.classList.toggle('track-active', isActive);
    el.classList.toggle('track-selected', track.id === this.selectedId);
    el.querySelector('.track-title').textContent = track.title;
    el.querySelector('.track-artist').textContent = track.album ? `${track.artist} • ${track.album}` : track.artist;
    el.querySelector('.track-genre').textContent = track.genre || '';
    el.title = `${track.title} — ${track.artist}`;
  }
}

// ==========================================
// MAIN APPLICATION
// ==========================================
class AIDJApp {
  constructor() {
    this.tracks = [];
    this.queue = [];
    this.playlists = [];
    this.activePlaylist = null;
    this.currentView = 'tracks';
    this.expandedPlaylist = null;   // full playlist (with tracks) currently expanded in the accordion
    this.queueExpanded = true;       // Up Next shows the full list by default
    this.playlistFilterQuery = '';
    this.targetTrackForPlaylist = null;
    this.toastPlaylistId = null;
    this.toastTimer = null;
    this.currentTrackIndex = -1;
    this.isPlaying = false;
    this.userVolume = 1.0;
    this.serverReady = false;
    this.libraryLoading = null;      // { phase, found, read } while the server scans the music folder

    // Live radio & podcasts. `source` is null while the local deck plays;
    // otherwise { type:'radio', station, origin } or { type:'podcast', podcast, episode }.
    this.source = null;
    this.stations = [];
    this.podcasts = [];
    this.expandedPodcast = null;     // full podcast (with episodes) expanded in the Podcasts tab
    this.radioFilterQuery = '';
    this.podcastFilterQuery = '';
    this.radioNowTitle = '';         // on-air song title from ICY metadata
    this.radioNowTimer = null;
    this.radioReconnectTimer = null;
    this.podcastPositions = loadPositions(window.localStorage);
    this.lastPositionSaveMs = 0;
    this.starterDismissed = this._loadStarterDismissed();
    this.stationGenres = STATION_GENRES_FALLBACK;   // replaced by the server's list on first browse
    this.episodeNotesExpanded = false;
    this.podcastRepollTimer = null;
    this.podcastSpeed = this._loadPodcastSpeed();
    this._confirmResolve = null;

    // Audiobooks (local files from AUDIOBOOKS_DIR; progress lives on the server)
    this.audiobooks = [];
    this.audiobooksInfo = { configured: true, directoryExists: true, scanning: false };
    this.expandedAudiobook = null;   // full book (with chapters) expanded in the Audiobooks tab
    this.audiobookFilterQuery = '';
    this.lastBookProgressSaveMs = 0;

    // Streaming tab: provider availability (/api/streaming/status) and the last search, per provider
    this.streamingStatus = null;     // { providers: [{id, name, available, reason, full}], ... }
    this.streamingResults = null;    // { query, results: [{provider, tracks, albums, error}] }
    this.streamingQuery = '';
    this.streamingBusy = false;
    this.streamingCollapsed = {};    // provider id -> true when its section is folded

    this.dom = {
      musicPlayer: $('music-player'),
      nowPlayingSource: $('now-playing-source'),
      nowThumb: $('now-thumb'),
      nowThumbKind: $('now-thumb-kind'),
      nowThumbPlaying: $('now-thumb-playing'),
      episodePanel: $('episode-panel'),
      episodeMeta: $('episode-meta'),
      episodeRemaining: $('episode-remaining'),
      episodeNotes: $('episode-notes'),
      episodeNotesToggle: $('episode-notes-toggle'),
      radioStarterNote: $('radio-starter-note'),
      radioStarterDismiss: $('radio-starter-dismiss'),
      podcastStarterNote: $('podcast-starter-note'),
      podcastStarterDismiss: $('podcast-starter-dismiss'),
      stationBrowseChips: $('station-browse-chips'),
      podcastBrowseChips: $('podcast-browse-chips'),
      episodePanelLabel: $('episode-panel-label'),
      deckTabs: $('deck-tabs'),
      tabsPrevBtn: $('tabs-prev-btn'),
      tabsNextBtn: $('tabs-next-btn'),
      tabAudiobooks: $('tab-audiobooks'),
      tabStreaming: $('tab-streaming'),
      badgeAudiobooksCount: $('badge-audiobooks-count'),
      badgeStreamingCount: $('badge-streaming-count'),
      audiobooksView: $('audiobooks-view'),
      streamingView: $('streaming-view'),
      audiobookFilter: $('audiobook-filter'),
      audiobookFilterClear: $('audiobook-filter-clear'),
      audiobooksRescanBtn: $('audiobooks-rescan-btn'),
      audiobooksSetupNote: $('audiobooks-setup-note'),
      audiobooksSetupText: $('audiobooks-setup-text'),
      audiobooksList: $('audiobooks-list'),
      streamingSearchInput: $('streaming-search-input'),
      streamingSearchClear: $('streaming-search-clear'),
      streamingSearchBtn: $('streaming-search-btn'),
      streamingSections: $('streaming-sections'),
      skipBackBtn: $('skip-back-btn'),
      skipFwdBtn: $('skip-fwd-btn'),
      tabRadio: $('tab-radio'),
      tabPodcasts: $('tab-podcasts'),
      badgeRadioCount: $('badge-radio-count'),
      badgePodcastsCount: $('badge-podcasts-count'),
      radioView: $('radio-view'),
      podcastsView: $('podcasts-view'),
      radioFilter: $('radio-filter'),
      radioFilterClear: $('radio-filter-clear'),
      addStationBtn: $('add-station-btn'),
      stationsList: $('stations-list'),
      podcastFilter: $('podcast-filter'),
      podcastFilterClear: $('podcast-filter-clear'),
      subscribePodcastBtn: $('subscribe-podcast-btn'),
      podcastsList: $('podcasts-list'),
      addStationModal: $('add-station-modal'),
      stationModalCloseBtn: $('station-modal-close-btn'),
      stationSearchInput: $('station-search-input'),
      stationSearchBtn: $('station-search-btn'),
      stationSearchResults: $('station-search-results'),
      stationNameInput: $('station-name-input'),
      stationUrlInput: $('station-url-input'),
      stationGenreInput: $('station-genre-input'),
      stationAddBtn: $('station-add-btn'),
      addPodcastModal: $('add-podcast-modal'),
      podcastModalCloseBtn: $('podcast-modal-close-btn'),
      podcastSearchInput: $('podcast-search-input'),
      podcastSearchBtn: $('podcast-search-btn'),
      podcastSearchResults: $('podcast-search-results'),
      podcastFeedInput: $('podcast-feed-input'),
      podcastSubscribeBtn: $('podcast-subscribe-btn'),
      liveBtn: $('live-btn'),
      liveBtnIcon: $('live-btn-icon'),
      liveBtnText: $('live-btn-text'),
      statusBadge: $('status-badge'),
      statusDot: $('status-dot'),
      statusText: $('status-text'),
      nowPlayingState: $('now-playing-state'),
      nowPlayingTitle: $('now-playing-title'),
      nowPlayingArtist: $('now-playing-artist'),
      nowPlayingPlaylist: $('now-playing-playlist'),
      playPauseBtn: $('play-pause-btn'),
      playPauseIcon: $('play-pause-icon'),
      prevBtn: $('prev-btn'),
      nextBtn: $('next-btn'),
      trackList: $('track-list'),
      volumeSlider: $('volume-slider'),
      seekSlider: $('seek-slider'),
      timeCurrent: $('time-current'),
      timeDuration: $('time-duration'),
      aiPanel: $('ai-state-panel'),
      aiEmoji: $('ai-state-emoji'),
      aiName: $('ai-state-name'),
      aiDetail: $('ai-state-detail'),
      aiCountdownWrap: $('ai-countdown-wrap'),
      aiCountdownBar: $('ai-countdown-bar'),
      aiCountdownText: $('ai-countdown-text'),
      trackFilter: $('track-filter'),
      trackFilterClear: $('track-filter-clear'),
      queueContainer: $('queue-container'),
      queueCount: $('queue-count'),
      queueClearBtn: $('queue-clear-btn'),
      queueSaveBtn: $('queue-save-btn'),
      queueCard: $('queue-card'),
      queueThumb: $('queue-thumb'),
      queueTitle: $('queue-title'),
      queueArtist: $('queue-artist'),
      queueActions: $('queue-actions'),
      queuePlayNowBtn: $('queue-play-now-btn'),
      queueRemoveBtn: $('queue-remove-btn'),
      queueViewBtn: $('queue-view-btn'),
      queueExpanded: $('queue-expanded'),
      queueTracksContainer: $('queue-tracks-container'),
      tabTracks: $('tab-tracks'),
      playlistsSection: $('playlists-section'),
      playlistsBody: $('playlists-body'),
      playlistsCount: $('playlists-count'),
      playlistsViewBtn: $('playlists-view-btn'),
      latestEpisodes: $('latest-episodes'),
      latestEpisodesCount: $('latest-episodes-count'),
      latestEpisodesMeta: $('latest-episodes-meta'),
      latestPlayBtn: $('latest-play-btn'),
      latestCueBtn: $('latest-cue-btn'),
      latestViewBtn: $('latest-view-btn'),
      latestEpisodesBody: $('latest-episodes-body'),
      latestEpisodesList: $('latest-episodes-list'),
      badgeTracksCount: $('badge-tracks-count'),
      tracksView: $('tracks-view'),
      playlistFilter: $('playlist-filter'),
      playlistFilterClear: $('playlist-filter-clear'),
      newPlaylistBtn: $('new-playlist-btn'),
      playlistsList: $('playlists-list'),
      playlistToast: $('playlist-toast'),
      playlistToastTitle: $('playlist-toast-title'),
      playlistToastDetail: $('playlist-toast-detail'),
      playlistToastViewBtn: $('playlist-toast-view-btn'),
      addToPlaylistModal: $('add-to-playlist-modal'),
      modalCloseBtn: $('modal-close-btn'),
      modalTargetTitle: $('modal-target-title'),
      modalTargetArtist: $('modal-target-artist'),
      modalPlaylistsList: $('modal-playlists-list'),
      modalNewPlName: $('modal-new-pl-name'),
      modalCreatePlBtn: $('modal-create-pl-btn'),
      trackCount: $('track-count'),
      libraryLoading: $('library-loading'),
      trackSortBar: $('track-sort-bar'),
      speedControl: $('speed-control'),
      speedSelect: $('speed-select'),
      trackGroupsToggle: $('track-groups-toggle'),
      stationCountryInput: $('station-country-input'),
      stationPlaceInput: $('station-place-input'),
      confirmModal: $('confirm-modal'),
      confirmTitle: $('confirm-title'),
      confirmMessage: $('confirm-message'),
      confirmCloseBtn: $('confirm-close-btn'),
      confirmCancelBtn: $('confirm-cancel-btn'),
      confirmOkBtn: $('confirm-ok-btn'),
      newPlaylistModal: $('new-playlist-modal'),
      newPlaylistCloseBtn: $('new-playlist-close-btn'),
      newPlaylistNameInput: $('new-playlist-name-input'),
      newPlaylistDescInput: $('new-playlist-desc-input'),
      newPlaylistCancelBtn: $('new-playlist-cancel-btn'),
      newPlaylistConfirmBtn: $('new-playlist-confirm-btn'),
      saveQueueModal: $('save-queue-modal'),
      saveQueueCloseBtn: $('save-queue-close-btn'),
      saveQueueNameInput: $('save-queue-name-input'),
      saveQueueCancelBtn: $('save-queue-cancel-btn'),
      saveQueueConfirmBtn: $('save-queue-confirm-btn')
    };

    this.audio = new AudioManager();
    this.mic = new MicManager();
    this.wakeWord = new WakeWordListener();
    this.socket = new LiveSocket(this._wsUrl(), CONFIG.PROTOCOL_VERSION);
    this.trackRenderer = new TrackListRenderer(this.dom.trackList);

    this.fsm = new SessionFSM();
    this.sessionTimer = null;
    this.djSpeakingTimer = null;
    this.pendingToolCalls = false;
    this.toolMetrics = new Map();
    this.currentPlayAbort = null;
    this.isDraggingSeek = false;

    this.countdownInterval = null;
    this.speakingWatchdogInterval = null;
    this.lastAudioChunkTime = 0;
    this.turnCompletePending = false;
    this.trackFilterQuery = '';
    this.pendingWakeRequest = '';
    this.micOpening = false;
    this.listenTimeoutTimer = null;
    this.startupFxPlayed = false;
    this.sessionGen = 0;   // bumped on every goLive/endSession; stale async work checks it

    this._bindEvents();
    this._bindFSM();
    this._bindSocket();
    this._bindWakeWord();
  }

  async init() {
    console.log('[App] Initializing AI DJ...');
    const t0 = performance.now();
    this._setAiState('offline', 'Loading…');
    this._setupGesturePrewarm();
    this.socket.connect();                   // don't make the DJ wait for the library
    this._updateUI();
    this._updateSortBar();
    this._updateQueueUI();
    // Everything loads in parallel; the library shows its own scan progress
    // (the server starts serving before the scan is done).
    await Promise.all([
      this._loadTracks(),
      this.loadPlaylists(),
      this.loadStations(),
      this.loadPodcasts(),
      this.loadAudiobooks(),
      this.loadStreamingStatus(),
    ]);
    this._updateUI();
    this._updateSortBar();
    this._updateTabArrows();
    console.log(`[App] Init complete in ${Math.round(performance.now() - t0)}ms`);
  }

  _wsUrl() {
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${protocol}//${location.host}/ws/live`;
  }

  /**
   * Load the library. While the server is still scanning it answers 503 with
   * progress ({phase, found, read}); keep polling and show that progress
   * instead of giving up after a few tries.
   */
  async _loadTracks() {
    this.libraryLoading = { phase: 'connecting', found: 0, read: 0 };
    this._renderLibraryLoading();
    const deadline = Date.now() + CONFIG.LIBRARY_LOAD_TIMEOUT_MS;
    let delay = 400;
    while (Date.now() < deadline) {
      try {
        const res = await fetch('/api/tracks');
        if (res.status === 200) {
          this.tracks = await res.json();
          this.libraryLoading = null;
          this._renderLibraryLoading();
          this._updatePlaylistBadges();
          this._updateUI();
          console.log(`[App] Loaded ${this.tracks.length} tracks`);
          return;
        }
        if (res.status === 503) {
          const info = await res.json().catch(() => ({}));
          this.libraryLoading = { phase: info.phase || 'scanning', found: info.found || 0, read: info.read || 0, elapsed: info.elapsed };
          this._renderLibraryLoading();
        }
      } catch (e) {
        this.libraryLoading = { phase: 'connecting', found: 0, read: 0 };
        this._renderLibraryLoading();
      }
      await new Promise(r => setTimeout(r, delay));
      delay = Math.min(1500, delay + 200);
    }
    this.libraryLoading = { phase: 'failed', found: 0, read: 0 };
    this._renderLibraryLoading();
    console.error('[App] Library did not finish loading in time');
  }

  /** Loading message in the Library tab + the track count while the server scans. */
  _renderLibraryLoading() {
    const el = this.dom.libraryLoading;
    const st = this.libraryLoading;
    const loading = !!st && st.phase !== 'failed';
    if (this.trackRenderer.loading !== loading) {
      this.trackRenderer.loading = loading;
      this.trackRenderer._dirty = true;                                       // force _recompute (same track array)
      this.trackRenderer.render(this.tracks, null, this.trackFilterQuery);   // re-evaluates the empty-state text
    }
    if (!el) return;
    if (!st) { el.style.display = 'none'; return; }
    el.style.display = 'flex';
    const n = (x) => (x || 0).toLocaleString();
    let text;
    if (st.phase === 'connecting') text = 'Connecting to the AI DJ server…';
    else if (st.phase === 'failed') text = 'The library did not load. Is the server running? Reload the page to try again.';
    else if (st.phase === 'listing' || !st.found) text = 'Scanning your music folder…';
    else text = `Reading track tags… ${n(st.read)} of ${n(st.found)} files${st.elapsed ? ` (${Math.round(st.elapsed)}s)` : ''}`;
    const pct = st.found ? Math.round((st.read / st.found) * 100) : 0;
    el.innerHTML = `<span class="library-loading-icon" aria-hidden="true">${st.phase === 'failed' ? '⚠️' : '⏳'}</span>`
      + `<div class="library-loading-text"><b>${st.phase === 'failed' ? 'Library unavailable' : 'Loading your library'}</b><span>${escapeHtml(text)}</span>`
      + (st.found && st.phase !== 'failed' ? `<div class="episode-progress library-progress"><i style="width:${pct}%"></i></div>` : '')
      + '</div>';
    if (this.dom.trackCount) this.dom.trackCount.textContent = st.phase === 'failed' ? 'Library unavailable' : 'Scanning…';
  }

  _bindEvents() {
    this.dom.musicPlayer.addEventListener('ended', () => this._onMediaEnded());
    this.dom.musicPlayer.addEventListener('error', () => this._onMediaError());
    this.dom.musicPlayer.addEventListener('play', () => {
      this.isPlaying = true;
      this._sendPlaybackState();
      this._updateUI();
    });
    this.dom.musicPlayer.addEventListener('pause', () => {
      this.isPlaying = false;
      if (this.source?.type === 'podcast') this._rememberEpisodePosition(true);
      if (this.source?.type === 'audiobook') this._saveBookProgress(true);
      this._sendPlaybackState();
      this._updateUI();
    });
    this.dom.musicPlayer.addEventListener('timeupdate', () => this._onTimeUpdate());

    this.dom.seekSlider.addEventListener('input', () => {
      this.isDraggingSeek = true;
      const dur = this.dom.musicPlayer.duration;
      if (dur) {
        this.dom.timeCurrent.textContent = formatTime((this.dom.seekSlider.value / 100) * dur);
      }
    });
    this.dom.seekSlider.addEventListener('change', () => {
      const dur = this.dom.musicPlayer.duration;
      if (dur) {
        this.dom.musicPlayer.currentTime = (this.dom.seekSlider.value / 100) * dur;
      }
      this.isDraggingSeek = false;
    });

    this.dom.volumeSlider.addEventListener('input', throttle(() => {
      this.setUserVolume(parseFloat(this.dom.volumeSlider.value), { fromSlider: true });
    }, CONFIG.VOLUME_THROTTLE_MS));
    this.dom.volumeSlider.addEventListener('change', () => this._sendPlaybackState());

    this.dom.playPauseBtn.addEventListener('click', () => this.togglePlayPause());
    this.dom.prevBtn.addEventListener('click', () => this.prevTrack());
    this.dom.nextBtn.addEventListener('click', () => this.nextTrack());
    this.dom.skipBackBtn?.addEventListener('click', () => this.seekRelative(-SKIP_BACK_S));
    this.dom.skipFwdBtn?.addEventListener('click', () => this.seekRelative(SKIP_FWD_S));
    this.dom.episodeNotesToggle?.addEventListener('click', () => this.toggleEpisodeNotes());
    this.dom.radioStarterDismiss?.addEventListener('click', () => this.dismissStarterNote('radio'));
    this.dom.podcastStarterDismiss?.addEventListener('click', () => this.dismissStarterNote('podcast'));

    this.dom.queueClearBtn?.addEventListener('click', () => this.clearQueue());
    this.dom.queuePlayNowBtn?.addEventListener('click', () => this.playNextInQueue());
    this.dom.queueRemoveBtn?.addEventListener('click', () => this.removeFromQueue(0));
    this.dom.queueSaveBtn?.addEventListener('click', () => this.openSaveQueueModal());
    this.dom.queueViewBtn?.addEventListener('click', () => this.toggleQueueExpanded());

    // --- Tab Switching ---
    this.dom.tabTracks?.addEventListener('click', () => this.switchView('tracks'));
    this.dom.playlistsViewBtn?.addEventListener('click', () => this.togglePlaylistsSection());
    // Newest podcast episodes workflow
    this.dom.latestPlayBtn?.addEventListener('click', (e) => { e.stopPropagation(); this.playLatestEpisodes(); });
    this.dom.latestCueBtn?.addEventListener('click', (e) => { e.stopPropagation(); this.playLatestEpisodes({ queueOnly: true }); });
    this.dom.latestViewBtn?.addEventListener('click', (e) => { e.stopPropagation(); this.toggleLatestEpisodes(); });
    this.dom.latestEpisodes?.querySelector('.latest-episodes-header')?.addEventListener('click', () => this.toggleLatestEpisodes());
    this.dom.latestEpisodesBody?.addEventListener('click', (e) => e.stopPropagation());
    this.dom.tabRadio?.addEventListener('click', () => this.switchView('radio'));
    this.dom.tabPodcasts?.addEventListener('click', () => this.switchView('podcasts'));
    this.dom.tabAudiobooks?.addEventListener('click', () => this.switchView('audiobooks'));
    this.dom.tabStreaming?.addEventListener('click', () => this.switchView('streaming'));
    // Tab carousel arrows
    this.dom.tabsPrevBtn?.addEventListener('click', () => this._scrollTabs(-1));
    this.dom.tabsNextBtn?.addEventListener('click', () => this._scrollTabs(1));
    this.dom.deckTabs?.addEventListener('scroll', () => this._updateTabArrows(), { passive: true });
    window.addEventListener('resize', () => { this._updateTabArrows(); this._revealActiveTab(); });

    // --- Audiobooks tab: filter, rescan ---
    this._bindFilter(this.dom.audiobookFilter, this.dom.audiobookFilterClear, (q) => { this.audiobookFilterQuery = q; this.renderAudiobooks(); });
    this.dom.audiobooksRescanBtn?.addEventListener('click', () => this.rescanAudiobooks());

    // --- Streaming tab: one search across every provider section ---
    this._bindFilter(this.dom.streamingSearchInput, this.dom.streamingSearchClear, (q) => {
      this.streamingQuery = q;
      if (!q) { this.streamingResults = null; this.renderStreaming(); }
    });
    this.dom.streamingSearchBtn?.addEventListener('click', () => this.searchStreaming());
    this.dom.streamingSearchInput?.addEventListener('keydown', (e) => { if (e.key === 'Enter') this.searchStreaming(); });

    // --- Radio tab: filter, add-station modal (directory search or stream URL) ---
    this._bindFilter(this.dom.radioFilter, this.dom.radioFilterClear, (q) => { this.radioFilterQuery = q; this.renderStations(); });
    this.dom.addStationBtn?.addEventListener('click', () => this.openAddStationModal());
    // Finder modals hold search results and half-typed URLs: close only via ✕.
    this.dom.stationModalCloseBtn?.addEventListener('click', () => this.closeAddStationModal());
    this.dom.stationSearchBtn?.addEventListener('click', () => this.searchStationDirectory());
    this.dom.stationSearchInput?.addEventListener('keydown', (e) => { if (e.key === 'Enter') this.searchStationDirectory(); });
    this.dom.stationCountryInput?.addEventListener('keydown', (e) => { if (e.key === 'Enter') this.searchStationDirectory(); });
    this.dom.stationPlaceInput?.addEventListener('keydown', (e) => { if (e.key === 'Enter') this.searchStationDirectory(); });
    this.dom.stationAddBtn?.addEventListener('click', () => this.submitManualStation());

    // --- Podcasts tab: filter, subscribe modal (directory search or feed URL) ---
    this._bindFilter(this.dom.podcastFilter, this.dom.podcastFilterClear, (q) => { this.podcastFilterQuery = q; this.renderPodcasts(); });
    this.dom.subscribePodcastBtn?.addEventListener('click', () => this.openAddPodcastModal());
    this.dom.podcastModalCloseBtn?.addEventListener('click', () => this.closeAddPodcastModal());
    this.dom.podcastSearchBtn?.addEventListener('click', () => this.searchPodcastDirectory());
    this.dom.podcastSearchInput?.addEventListener('keydown', (e) => { if (e.key === 'Enter') this.searchPodcastDirectory(); });
    this.dom.podcastSubscribeBtn?.addEventListener('click', () => this.submitFeedSubscription());

    // --- Playlist Toolbar & Filter ---
    this.dom.newPlaylistBtn?.addEventListener('click', () => this.openCreatePlaylistModal());
    this.dom.playlistFilter?.addEventListener('input', (e) => {
      this.playlistFilterQuery = e.target.value;
      if (this.dom.playlistFilterClear) {
        this.dom.playlistFilterClear.style.display = this.playlistFilterQuery ? 'inline' : 'none';
      }
      this.renderPlaylists();
    });
    this.dom.playlistFilterClear?.addEventListener('click', () => {
      if (this.dom.playlistFilter) this.dom.playlistFilter.value = '';
      this.playlistFilterQuery = '';
      if (this.dom.playlistFilterClear) this.dom.playlistFilterClear.style.display = 'none';
      this.renderPlaylists();
    });

    // --- Toast View ---
    this.dom.playlistToastViewBtn?.addEventListener('click', () => this.viewToastPlaylist());

    // --- Modal: Add To Playlist ---
    this.dom.modalCloseBtn?.addEventListener('click', () => this.closeAddToPlaylistModal());
    this.dom.modalCreatePlBtn?.addEventListener('click', () => this.submitModalCreateAndAdd());

    // --- Modal: New Playlist ---
    this.dom.newPlaylistCloseBtn?.addEventListener('click', () => this.closeCreatePlaylistModal());
    this.dom.newPlaylistCancelBtn?.addEventListener('click', () => this.closeCreatePlaylistModal());
    this.dom.newPlaylistConfirmBtn?.addEventListener('click', () => this.submitCreatePlaylist());
    this.dom.newPlaylistNameInput?.addEventListener('keydown', (e) => { if (e.key === 'Enter') this.submitCreatePlaylist(); });
    this.dom.newPlaylistDescInput?.addEventListener('keydown', (e) => { if (e.key === 'Enter') this.submitCreatePlaylist(); });

    // --- Modal: Save Queue ---
    this.dom.saveQueueCloseBtn?.addEventListener('click', () => this.closeSaveQueueModal());
    this.dom.saveQueueCancelBtn?.addEventListener('click', () => this.closeSaveQueueModal());
    this.dom.saveQueueConfirmBtn?.addEventListener('click', () => this.submitSaveQueue());

    // --- Modal: Confirm / Alert (shared) ---
    this.dom.confirmCloseBtn?.addEventListener('click', () => this._resolveConfirm(false));
    this.dom.confirmCancelBtn?.addEventListener('click', () => this._resolveConfirm(false));
    this.dom.confirmOkBtn?.addEventListener('click', () => this._resolveConfirm(true));

    // Every modal: Esc closes it; a backdrop click never does (consistent, no
    // accidental loss of search results or typed text).
    document.addEventListener('keydown', (e) => {
      if (e.key !== 'Escape') return;
      const open = [...document.querySelectorAll('.modal-backdrop')].filter(m => m.style.display !== 'none').pop();
      if (!open) return;
      e.preventDefault();
      if (open === this.dom.confirmModal) this._resolveConfirm(false);
      else open.style.display = 'none';
    });

    // --- Library sort bar ---
    this.dom.trackSortBar?.addEventListener('click', (e) => {
      // Only the sort keys — the Expand/Collapse-all button shares the
      // .sort-btn look but must not reset the sort to "Library".
      const btn = e.target.closest('.sort-btn[data-sort]');
      if (!btn) return;
      this.trackRenderer.setSort(btn.dataset.sort);
      this._updateSortBar();
    });
    this.trackRenderer.onDragStart = (e, tracks, label) => this._startTracksDrag(e, tracks, label);
    this._makeQueueDropTarget();
    this._makeTabsDraggable();
    this.trackRenderer.onCountChange = (visible, total, groups) => {
      if (!this.dom.trackCount) return;
      const fmt = (n) => n.toLocaleString();
      const base = visible === total
        ? `${fmt(total)} track${total === 1 ? '' : 's'}`
        : `${fmt(visible)} of ${fmt(total)} tracks`;
      const mode = this.trackRenderer.groupMode;
      const noun = mode === 'title' ? 'letter' : mode;
      this.dom.trackCount.textContent = groups ? `${base} in ${fmt(groups)} ${noun}${groups === 1 ? '' : 's'}` : base;
      this._updateSortBar();
    };

    // --- Playback speed (podcasts) ---
    this.dom.speedSelect?.addEventListener('change', (e) => this.setPlaybackSpeed(parseFloat(e.target.value)));
    this.dom.trackGroupsToggle?.addEventListener('click', () => {
      this.trackRenderer.toggleAllGroups();
      this._updateSortBar();
    });

    this.dom.trackFilter?.addEventListener('input', (e) => {
      this.trackFilterQuery = e.target.value;
      if (this.dom.trackFilterClear) {
        this.dom.trackFilterClear.style.display = this.trackFilterQuery ? 'inline' : 'none';
      }
      this.trackRenderer.render(
        this.tracks,
        this.currentTrackIndex >= 0 ? this.tracks[this.currentTrackIndex]?.id : null,
        this.trackFilterQuery
      );
    });

    this.dom.trackFilterClear?.addEventListener('click', () => {
      if (this.dom.trackFilter) this.dom.trackFilter.value = '';
      this.trackFilterQuery = '';
      if (this.dom.trackFilterClear) this.dom.trackFilterClear.style.display = 'none';
      this.trackRenderer.render(
        this.tracks,
        this.currentTrackIndex >= 0 ? this.tracks[this.currentTrackIndex]?.id : null,
        ''
      );
    });

    this.audio.addEventListener('voiceEnded', () => {
      console.log('[App] AudioManager voiceEnded event received');
      this._onVoiceEnded();
    });

    this.dom.liveBtn.addEventListener('click', () => {
      console.log(`[App] Live button clicked. State: ${this.fsm.state}, disabled: ${this.dom.liveBtn.disabled}`);
      if (this.dom.liveBtn.disabled) return;

      // A plain two-state toggle, nothing else:
      //   OFF AIR -> click = go live (mic opens, you just talk; Gemini's own
      //              voice-activity detection ends your turn)
      //   ON AIR  -> click = end the live session (even while still connecting)
      // It never submits a turn, so any number of clicks can only ever start
      // or stop sessions — never hand the DJ a noise-only turn to "answer".
      if (this.fsm.state === 'offline') {
        this.goLive();
      } else {
        console.log(`[App] End Live pressed in state ${this.fsm.state}`);
        this.endSession();
      }
    });

    window.addEventListener('error', (e) => {
      console.error('[Global]', e.error);
      if (this.fsm.state !== 'offline') this.fsm.transition('error', e.message);
    });
    window.addEventListener('unhandledrejection', (e) => {
      console.error('[Unhandled]', e.reason);
    });
  }

  _bindFSM() {
    this.fsm.addEventListener('change', ({ detail }) => {
      const { to, detail: msg } = detail;
      this._setAiState(to, msg);

      if (to !== 'listening') this._clearListenTimeout();

      if (to === 'speaking') {
        this._showDjSpeaking();
      } else if (to === 'offline' || to === 'error') {
        this._hideDjSpeaking();
        this._stopCountdown();
        this._stopSpeakingWatchdog();
      }

      this._updateUI();
    });
  }

  _bindSocket() {
    this.socket.addEventListener('open', () => {
      console.log('[App] Socket open');
      this._setStatus('Warming up…');
    });

    this.socket.addEventListener('close', () => {
      console.log('[App] Socket close');
      this.serverReady = false;
      if (this.fsm.state !== 'offline') {
        this.endSession();
      }
      this._setStatus('Offline');
      this.dom.liveBtn.disabled = true;
      this.wakeWord.stop();
    });

    this.socket.addEventListener('message', ({ detail }) => {
      this._handleSocketMessage(detail);
    });

    this.socket.addEventListener('error', () => {
      this._setStatus('Connection Error');
    });
  }

  async goLive(initialWakeRequest = '') {
    console.log(`[App] goLive() called. serverReady=${this.serverReady}`);
    if (!this.serverReady) {
      console.warn('[App] goLive aborted: server not ready');
      return;
    }

    this.wakeWord.stop();
    await this.fsm.guard(async () => {
      if (!this.fsm.transition('connecting')) {
        console.warn('[App] goLive aborted: transition to connecting failed');
        return;
      }

      this.sessionGen++;

      // Duck the music the instant the DJ is invoked (button or "Hey DJ"),
      // not seconds later when the first voice chunk arrives.
      this.audio.duck();

      this.pendingWakeRequest = initialWakeRequest.trim();

      this.audio.playChime(true);
      this.audio.initMusicPlayer(this.dom.musicPlayer);
      await this.audio.resume();

      this.pendingToolCalls = false;
      this._resetSessionWatchdog();

      try {
        this.socket.send(JSON.stringify({ type: 'start' }));
        this._sendPlaybackState();   // fresh deck snapshot for this session's tools
        console.log('[App] Sent start message');
      } catch (err) {
        console.error('[App] goLive send error:', err);
        this.fsm.transition('error', err.message);
        setTimeout(() => this.endSession(), 0);
      }
    });
  }

  async openMic() {
    console.log(`[App] openMic() called`);
    if (this.micOpening) {
      console.warn('[App] openMic ignored: already opening');
      return;
    }
    this._stopCountdown();
    if (!this.fsm.canTransition('listening')) {
      console.warn(`[App] openMic blocked: cannot transition from ${this.fsm.state} to listening`);
      return;
    }

    // The user is about to talk to the DJ: keep the music out of the way.
    this.audio.duck();
    this.micOpening = true;
    const gen = this.sessionGen;

    try {
      await this.mic.prewarm();
      if (gen !== this.sessionGen || !this.fsm.canTransition('listening')) {
        console.log('[App] openMic abandoned: session ended while the mic was warming up');
        return;
      }
      const started = this.mic.start((data) => {
        if (this.socket.isOpen && this.fsm.state === 'listening') {
          this.socket.send(data);
        }
      });

      if (started) {
        this.fsm.transition('listening', 'Just talk — AI DJ is listening');
        this._armListenTimeout();
      } else {
        console.error('[App] mic.start() returned false');
        this.fsm.transition('error', 'Microphone failed to start');
        setTimeout(() => this.endSession(), 0);
      }
    } catch (err) {
      console.error('[App] Mic error:', err);
      this.fsm.transition('error', 'Microphone unavailable');
      setTimeout(() => this.endSession(), 0);
    } finally {
      this.micOpening = false;
    }
  }

  /**
   * Nobody spoke after the mic opened (no DJ response within
   * LISTEN_TIMEOUT_MS): end the session instead of streaming an open mic.
   */
  _armListenTimeout() {
    this._clearListenTimeout();
    this.listenTimeoutTimer = setTimeout(() => {
      this.listenTimeoutTimer = null;
      if (this.fsm.state === 'listening') {
        console.log('[App] Listening timeout — no speech heard, ending session');
        this._setAiState('listening', 'No speech heard — ending session');
        this.endSession();
      }
    }, CONFIG.LISTEN_TIMEOUT_MS);
  }

  _clearListenTimeout() {
    if (this.listenTimeoutTimer) {
      clearTimeout(this.listenTimeoutTimer);
      this.listenTimeoutTimer = null;
    }
  }

  /** Music should be ducked whenever the DJ is being talked to or is talking. */
  _shouldDuck() {
    return ['connecting', 'listening', 'processing', 'tool_call', 'speaking'].includes(this.fsm.state)
      || this.audio._activeVoiceNodes.size > 0;
  }

  async endSession() {
    console.log(`[App] endSession() called. state=${this.fsm.state}, locked=${this.fsm.isLocked}`);
    if (this.fsm.isLocked) {
      console.warn('[App] endSession blocked: FSM locked — retrying in 100ms');
      setTimeout(() => this.endSession(), 100);
      return;
    }
    if (this.fsm.state === 'offline') {
      console.log('[App] endSession: already offline');
      return;
    }

    await this.fsm.guard(() => {
      this.sessionGen++;
      this.fsm.transition('disconnecting');

      this._clearSessionWatchdog();
      this._clearListenTimeout();
      this._stopCountdown();
      this._stopSpeakingWatchdog();
      clearTimeout(this.djSpeakingTimer);
      this.turnCompletePending = false;
      this.pendingWakeRequest = '';

      this.audio.playChime(false);
      this.audio.stopAll();
      this._hideDjSpeaking();

      this.mic.dispose();

      if (this.socket.isOpen) {
        this.socket.send(JSON.stringify({ type: 'stop' }));
      }

      this.pendingToolCalls = false;
      this.toolMetrics.clear();

      this.fsm.transition('offline');

      const canReconnect = this.socket.ready;
      this._setStatus(canReconnect ? 'Ready' : 'Offline');
      this.dom.liveBtn.disabled = !canReconnect;
      this._updateUI();
      this._setupGesturePrewarm();
      this.wakeWord.stop();
      this._startWakeWord();
    });
  }

  _startCountdown(durationMs = 3000) {
    this._stopCountdown();
    if (this.fsm.state !== 'idle') return;

    const end = Date.now() + durationMs;
    this.dom.aiCountdownWrap?.classList.add('visible');
    this.dom.aiCountdownText?.classList.add('visible');

    this.countdownInterval = setInterval(() => {
      const remaining = Math.max(0, end - Date.now());
      const pct = (remaining / durationMs) * 100;
      if (this.dom.aiCountdownBar) this.dom.aiCountdownBar.style.width = `${pct}%`;
      if (this.dom.aiCountdownText) {
        this.dom.aiCountdownText.textContent = `Auto-disconnect in ${(remaining / 1000).toFixed(1)}s`;
      }
      if (remaining <= 0) {
        this._stopCountdown();
        if (this.fsm.state === 'idle') {
          console.log('[App] Auto-disconnect countdown expired — ending session');
          this.endSession();
        }
      }
    }, 100);
  }

  _stopCountdown() {
    if (this.countdownInterval) {
      clearInterval(this.countdownInterval);
      this.countdownInterval = null;
    }
    this.dom.aiCountdownWrap?.classList.remove('visible');
    this.dom.aiCountdownText?.classList.remove('visible');
    if (this.dom.aiCountdownBar) this.dom.aiCountdownBar.style.width = '100%';
    if (this.dom.aiCountdownText) this.dom.aiCountdownText.textContent = '';
  }

  _startSpeakingWatchdog() {
    this.lastAudioChunkTime = Date.now();
    if (this.speakingWatchdogInterval) return;
    this.speakingWatchdogInterval = setInterval(() => {
      if (this.fsm.state !== 'speaking') {
        this._stopSpeakingWatchdog();
        return;
      }
      const elapsed = Date.now() - this.lastAudioChunkTime;
      if (elapsed > 3000 && this.audio._activeVoiceNodes.size === 0) {
        console.warn('[App] Speaking watchdog: no audio chunks for 3s, recovering to voice end');
        this._onVoiceEnded();
      }
    }, 500);
  }

  _stopSpeakingWatchdog() {
    if (this.speakingWatchdogInterval) {
      clearInterval(this.speakingWatchdogInterval);
      this.speakingWatchdogInterval = null;
    }
  }

  _onVoiceEnded() {
    this.turnCompletePending = false;
    this._stopSpeakingWatchdog();
    clearTimeout(this.djSpeakingTimer);
    this._hideDjSpeaking();

    if (this.fsm.state === 'offline' || this.fsm.state === 'disconnecting') {
      return;
    }

    console.log('[App] Voice ended -> transitioning to idle and starting auto-disconnect');
    if (this.fsm.canTransition('idle')) {
      this.fsm.transition('idle', 'Turn complete');
    }
    this._startCountdown(3000);
    window.setTimeout(() => this._startFollowUpListener(), 250);
  }

  // --- Socket Message Handling ---

  _handleSocketMessage(data) {
    this._resetSessionWatchdog();

    if (data instanceof ArrayBuffer) {
      if (this.fsm.state === 'offline') {
        console.log('[App] Ignoring voice PCM while offline');
        return;
      }

      this._stopCountdown();
      this.lastAudioChunkTime = Date.now();
      this._startSpeakingWatchdog();

      // If voice arrives while still listening, auto-drop mic and
      // route through processing → speaking (valid transitions).
      if (this.fsm.state === 'listening') {
        console.log('[App] Voice arrived while listening, auto-dropping mic');
        this.mic.stop();
        this.fsm.transition('processing', 'DJ is responding...');
      }

      this.audio.playVoicePCM(data);

      // Transition to speaking if not already there
      if (this.fsm.canTransition('speaking')) {
        this.fsm.transition('speaking');
      }
      this._showDjSpeaking();
      return;
    }

    let msg;
    try {
      msg = JSON.parse(data);
    } catch (e) {
      console.error('[App] Invalid JSON:', data);
      return;
    }

    console.log(`[App] WS msg: ${msg.type}`, msg);

    switch (msg.type) {
      case 'ready':
        this.serverReady = true;
        this.dom.liveBtn.disabled = false;
        this._setStatus('Ready');
        this._playStartupFx();
        this._startWakeWord();
        this._updateUI();
        // A (re)connected server has an empty picture of the deck; sync it so
        // voice tools like saveQueueAsPlaylist see the real queue/playlist.
        this._sendPlaybackState();
        break;

      case 'connected':
        this.dom.liveBtn.disabled = false;
        this._setStatus('Live On Air', true);
        this.fsm.transition('idle');
        if (this.pendingWakeRequest) {
          const request = this.pendingWakeRequest;
          this.pendingWakeRequest = '';
          this.socket.send(JSON.stringify({ type: 'wake_text', text: request }));
          this.fsm.transition('processing', 'Processing your voice request…');
        } else {
          this.openMic().catch(err => {
            console.error('[App] openMic failed after connected:', err);
          });
        }
        break;

      case 'ai_state': {
        const prev = this.fsm.state;

        // If transitioning from listening to tool_call/speaking, drop mic
        if ((msg.state === 'tool_call' || msg.state === 'speaking') && prev === 'listening') {
          console.log('[App] Auto-dropping mic due to AI state');
          this.mic.stop();
          this.fsm.transition('processing', msg.detail || 'Working...');
        }

        // Target transition
        if (this.fsm.canTransition(msg.state)) {
          this.fsm.transition(msg.state, msg.detail || '');
        }

        if (msg.state === 'tool_call') {
          this.pendingToolCalls = true;
        }
        break;
      }

      case 'command':
        this._handleCommand(msg);
        break;

      case 'turn_complete': {
        this.pendingToolCalls = false;
        this.turnCompletePending = true;
        console.log('[App] turn_complete received. Active voice nodes:', this.audio._activeVoiceNodes.size);

        if (this.audio._activeVoiceNodes.size === 0) {
          this._onVoiceEnded();
        } else {
          // Safety timeout in case voiceEnded event does not trigger
          const remaining = Math.max(0, this.audio._nextVoiceTime - this.audio.currentTime);
          clearTimeout(this.djSpeakingTimer);
          this.djSpeakingTimer = setTimeout(() => {
            if (this.turnCompletePending) {
              console.log('[App] Safety timer firing for voice completion');
              this._onVoiceEnded();
            }
          }, (remaining * 1000) + 300);
        }
        break;
      }

      case 'interrupted':
        this.turnCompletePending = false;
        this.audio.interruptVoice();
        this._stopSpeakingWatchdog();
        // The user barged in, so the DJ is still engaged: keep the music
        // ducked until the new turn ends (_onVoiceEnded / offline fade up).
        clearTimeout(this.djSpeakingTimer);
        break;

      case 'error':
        console.error('[Server]', msg.message);
        this.fsm.transition('error', msg.message);
        setTimeout(() => this.endSession(), 0);
        break;

      case 'telemetry':
        console.log(`[Telemetry Backend] TTFT: ${msg.ttft_ms?.toFixed(2)}ms`);
        break;

      case 'pong':
        break;

      default:
        console.log('[App] Unknown message type:', msg.type);
    }
  }

  // --- Commands ---

  _handleCommand(msg) {
    if (msg.action === 'playTrack') {
      this._startToolTimer(msg.trackId || 'play');
    }

    switch (msg.action) {
      case 'playTrack':
        this.playTrackById(msg.trackId);
        break;
      case 'queueTrack':
        this.queueTrackById(msg.trackId);
        break;
      case 'pausePlayback':
        if (this.source?.type === 'radio') { this.stopRadio(); break; }
        this.dom.musicPlayer.pause();
        this.isPlaying = false;
        this._updateUI();
        break;
      case 'resumePlayback':
        if (this.source?.type === 'radio') { this.playStation(this.source.station, { origin: this.source.origin }); break; }
        this.dom.musicPlayer.play().catch(console.error);
        this.isPlaying = true;
        this._updateUI();
        break;
      case 'playAudiobookChapter':
        this.playChapter(msg.book, msg.chapter, { position: Number(msg.position) || 0 });
        break;
      case 'playStreamTracks':
        this.playStreamTracks(msg.tracks || [], msg.context || null);
        break;
      case 'queueStreamTracks':
        (msg.tracks || []).forEach(t => this.queueStreamTrack(t, msg.context || null, { silent: true }));
        if ((msg.tracks || []).length) this.showPlaylistToast('➕ Cued from streaming', msg.tracks.map(t => t.title).slice(0, 2).join(', ') + (msg.tracks.length > 2 ? ` +${msg.tracks.length - 2}` : ''), null);
        break;
      case 'playRadio':
        this.playStation(msg.station, { origin: msg.origin || 'saved' });
        break;
      case 'playPodcastEpisode':
        this.playEpisode(msg.podcast, msg.episode);
        break;
      case 'queuePodcastEpisodes':
        (msg.items || []).forEach(it => this.queueEpisode(it.podcast, it.episode, { silent: true }));
        if ((msg.items || []).length) this.showPlaylistToast('➕ Cued episodes', `${msg.items.length} newest episode${msg.items.length === 1 ? '' : 's'}`, null);
        break;
      case 'seekRelative':
        this.seekRelative(Number(msg.seconds) || 0);
        break;
      case 'setPlaybackRate':
        this.setPlaybackSpeed(Number(msg.rate) || 1);
        break;
      case 'setVolume':
        this.setUserVolume(Number(msg.level));
        this.showPlaylistToast('🔊 Volume', `${Math.round(this.userVolume * 100)}%`, null);
        break;
      case 'radioStationsChanged':
        this.handleRadioStationsChanged(msg).catch(err => console.error('[App] radioStationsChanged failed:', err));
        break;
      case 'podcastsChanged':
        this.handlePodcastsChanged(msg).catch(err => console.error('[App] podcastsChanged failed:', err));
        break;
      case 'nextTrack':
        this.nextTrack();
        break;
      case 'previousTrack':
        this.prevTrack();
        break;
      case 'loadAndPlayPlaylist':
        this.handleDynamicPlaylistLoaded(msg.playlist, msg.tracks, msg.startIndex || 0);
        break;
      case 'queuePlaylist':
        this.handlePlaylistQueued(msg.playlist, msg.tracks);
        break;
      case 'clearQueue': {
        const n = this.queue.length;
        this.clearQueue();
        this.showPlaylistToast('🗑 Queue cleared', n ? `${n} cued track${n === 1 ? '' : 's'} removed` : 'The queue was already empty', null);
        break;
      }
      case 'removeFromQueue': {
        const idx = this.queue.findIndex(t => t.id === msg.trackId);
        if (idx !== -1) {
          const title = this.queue[idx].title;
          this.removeFromQueue(idx);
          this.showPlaylistToast('➖ Removed from queue', title, null);
        }
        break;
      }
      case 'playlistsChanged':
        this.handlePlaylistsChanged(msg).catch(err => {
          console.error('[App] playlistsChanged handling failed:', err);
        });
        break;
      default:
        console.warn('[App] Unknown command action:', msg.action);
    }
  }

  // --- Music Playback ---

  async playTrackByIndex(index) {
    if (index < 0 || index >= this.tracks.length) return;

    this._leaveSource();
    this.currentTrackIndex = index;
    const track = this.tracks[index];

    // Remove from queue if this track was queued
    const qIdx = this.queue.findIndex(t => t.id === track.id);
    if (qIdx !== -1) {
      this.queue.splice(qIdx, 1);
      this._updateQueueUI();
    }

    this.audio.initMusicPlayer(this.dom.musicPlayer);
    if (this.audio.ctx.state === 'suspended') {
      await this.audio.ctx.resume();
    }

    this.currentPlayAbort?.abort();
    this.currentPlayAbort = new AbortController();

    // Hook fade-up precisely when audio frames begin flowing to speakers
    const onPlaying = () => {
      this.audio.fadeTrackUp(this.userVolume, 1.0, this._shouldDuck());
    };
    this.dom.musicPlayer.addEventListener('playing', onPlaying, { once: true });

    this.dom.musicPlayer.src = `/api/tracks/${track.id}/stream`;

    // Artwork tile: probe first so a 404 falls back to the emoji, not a broken image
    const artUrl = `/api/tracks/${track.id}/art`;
    this._setThumbArt(artUrl, '💿', () => !this.source && this.currentTrackIndex === index);

    if ('mediaSession' in navigator) {
      navigator.mediaSession.metadata = new MediaMetadata({
        title: track.title,
        artist: track.artist,
        artwork: [{ src: `/api/tracks/${track.id}/art`, sizes: '512x512', type: 'image/jpeg' }]
      });
    }

    const t0 = performance.now();
    this._applyPlaybackRate();             // music is always 1×
    this.dom.musicPlayer.play()
      .then(() => {
        this._endToolTimer(track.id || 'play', t0);
        this.isPlaying = true;
        this._updateUI();
      })
      .catch((e) => {
        if (e.name === 'AbortError') return;
        console.error('Playback error:', e);
      });
  }

  playTrackById(trackId) {
    const idx = this.tracks.findIndex(t => t.id === trackId);
    if (idx !== -1) this.playTrackByIndex(idx);
  }

  async togglePlayPause() {
    this.audio.initMusicPlayer(this.dom.musicPlayer);
    if (this.audio.ctx.state === 'suspended') {
      await this.audio.ctx.resume();
    }
    if (this.source?.type === 'radio') {
      // Live radio: "pause" drops the stream; "play" re-tunes to the live edge.
      if (this.isPlaying) this.stopRadio();
      else this.playStation(this.source.station, { origin: this.source.origin });
      return;
    }
    if (this.isPlaying) {
      this.dom.musicPlayer.pause();
      this.isPlaying = false;
    } else {
      this.dom.musicPlayer.play().catch(console.error);
      this.isPlaying = true;
    }
    this._updateUI();
  }

  nextTrack() {
    if (this.source?.type === 'audiobook' && this.queue.length === 0) {
      const next = nextChapter(this.source.book, this.source.chapter.id);
      if (next) { this.playChapter(this.source.book, next); return; }
    }
    if (this.queue.length > 0) {
      this.playNextInQueue();
      return;
    }
    if (this.source) {
      // Leaving radio/podcast: continue the library from where the deck was.
      this._leaveSource();
      if (this.tracks.length === 0) { this._updateUI(); this._sendPlaybackState(); return; }
      this.playTrackByIndex(this.currentTrackIndex < 0 ? 0 : (this.currentTrackIndex + 1) % this.tracks.length);
      return;
    }
    if (this.activePlaylist) {
      // The playlist has played out; the deck continues through the library.
      console.log(`[App] Playlist '${this.activePlaylist.name}' finished`);
      this.activePlaylist = null;
      this._sendPlaybackState();
      this.renderPlaylists();
    }
    if (this.tracks.length === 0) return;
    this.playTrackByIndex((this.currentTrackIndex + 1) % this.tracks.length);
  }

  // --- Queue Management ---

  queueTrackById(trackId) {
    const trk = this.tracks.find(t => t.id === trackId);
    if (!trk) return;
    this.queueTrack(trk);
  }

  queueTrack(track) {
    if (!track) return;
    this.queue.push(track);
    this._updateQueueUI();
    this._sendPlaybackState();
    console.log(`[Queue] Added '${track.title}'. Total queued: ${this.queue.length}`);
  }

  removeFromQueue(index = 0) {
    if (index >= 0 && index < this.queue.length) {
      const removed = this.queue.splice(index, 1)[0];
      console.log(`[Queue] Removed '${removed.title}' from queue.`);
      this._updateQueueUI();
      this._sendPlaybackState();
    }
  }

  clearQueue() {
    this.queue = [];
    this._updateQueueUI();
    this._sendPlaybackState();
  }

  playNextInQueue() {
    if (this.queue.length === 0) return;
    const next = this.queue.shift();
    this._updateQueueUI();
    this._playQueueItem(next);
  }

  /** Queue entries are library tracks or podcast episodes ({kind:'podcast'}). */
  _playQueueItem(item) {
    if (!item) return;
    if (item.kind === 'podcast') this.playEpisode(item.podcast, item.episode);
    else if (item.kind === 'stream') this.playStreamTrack(item.track, { context: item.context || null });
    else if (item.kind === 'audiobook') this.playChapter(item.book, item.chapter);
    else this.playTrackById(item.id);
  }

  /** "+ Cue" for a podcast episode: queued next to songs, played in order. */
  queueEpisode(podcast, episode, { silent = false } = {}) {
    if (!podcast?.id || !episode?.id) return;
    if (this.queue.some(q => q.id === episode.id)) {
      if (!silent) this.showPlaylistToast('🎙 Already cued', episode.title, null);
      return;
    }
    this.queue.push({
      id: episode.id, title: episode.title, artist: podcast.title, genre: 'Podcast',
      kind: 'podcast', podcast: { id: podcast.id, title: podcast.title, artworkUrl: podcast.artworkUrl || '' }, episode,
    });
    this._updateQueueUI();
    this._sendPlaybackState();
    if (!silent) this.showPlaylistToast('➕ Cued episode', `${episode.title} • ${podcast.title}`, null);
  }

  async cueLatestEpisode(podcastId) {
    try {
      const pod = await this._fetchPodcastLoaded(podcastId);
      const ep = (pod?.episodes || [])[0];
      if (!ep) {
        this.showPlaylistToast('🎙 No episodes', pod?.refreshError || 'No playable episodes yet', null);
        return;
      }
      this.queueEpisode(pod, ep);
    } catch (e) {
      console.error('[App] Cue latest episode error:', e);
    }
  }

  /**
   * A playlist is only "active" while it still has something to play: the
   * current song or cued songs that belong to it. Clearing or editing the
   * queue can end that, which also retires the "one-off song" status line.
   */
  _reconcileActivePlaylist() {
    const pl = this.activePlaylist;
    if (!pl) return false;
    const ids = pl.trackIds || (pl.tracks || []).map(t => t.id);
    const current = !this.source && this.currentTrackIndex >= 0 ? this.tracks[this.currentTrackIndex] : null;
    const currentIn = !!current && (ids.length === 0 || ids.includes(current.id));
    const queuedIn = ids.length ? this.queue.some(q => q.kind !== 'podcast' && ids.includes(q.id)) : this.queue.some(q => q.kind !== 'podcast');
    if (currentIn || queuedIn) return false;
    console.log(`[App] Playlist '${pl.name}' is no longer active (nothing of it is playing or cued)`);
    this.activePlaylist = null;
    this.renderPlaylists();
    return true;
  }

  _updateQueueUI() {
    if (!this.dom.queueContainer) return;
    if (this._reconcileActivePlaylist()) this._updateUI();
    const hasItems = this.queue.length > 0;
    const nextTrack = hasItems ? this.queue[0] : null;

    this.dom.queueContainer.classList.toggle('has-queue', hasItems);
    this.dom.queueCard.classList.toggle('empty', !hasItems);
    if (this.dom.queueViewBtn) this.dom.queueViewBtn.style.display = hasItems ? 'block' : 'none';
    this._renderQueueExpanded();   // keeps an open accordion in sync; collapses when emptied

    if (hasItems) {
      const extra = this.queue.length > 1 ? ` (+${this.queue.length - 1} more)` : '';
      const from = this.activePlaylist ? ` • ${this.activePlaylist.name}` : '';
      this.dom.queueCount.textContent = `${this.queue.length} cued${extra}${from}`;
      this.dom.queueCount.title = this.activePlaylist ? `Cued from playlist “${this.activePlaylist.name}”` : '';
      this.dom.queueClearBtn.style.display = 'block';
      if (this.dom.queueSaveBtn) this.dom.queueSaveBtn.style.display = 'block';

      this.dom.queueTitle.textContent = nextTrack.title;
      this.dom.queueArtist.textContent = nextTrack.artist;

      const kindIcons = { podcast: '🎙', stream: '🌐', audiobook: '📖' };
      const artUrl = nextTrack.kind === 'podcast' ? (nextTrack.podcast?.artworkUrl || '')
        : nextTrack.kind === 'stream' ? (nextTrack.track?.albumArt || '')
          : nextTrack.kind === 'audiobook' ? (nextTrack.book?.id ? `/api/audiobooks/${nextTrack.book.id}/art` : '')
            : `/api/tracks/${nextTrack.id}/art`;
      const fallback = kindIcons[nextTrack.kind] || '🎵';
      this.dom.queueThumb.style.backgroundImage = 'none';
      this.dom.queueThumb.textContent = fallback;
      if (artUrl) {
        const testImg = new Image();
        testImg.onload = () => {
          if (this.queue.length > 0 && this.queue[0].id === nextTrack.id) {
            this.dom.queueThumb.style.backgroundImage = `url('${artUrl}')`;
            this.dom.queueThumb.textContent = '';
          }
        };
        testImg.onerror = () => {
          if (this.queue.length > 0 && this.queue[0].id === nextTrack.id) {
            this.dom.queueThumb.style.backgroundImage = 'none';
            this.dom.queueThumb.textContent = fallback;
          }
        };
        testImg.src = artUrl;
      }

      this.dom.queueActions.style.display = 'flex';
    } else {
      this.dom.queueCount.textContent = 'Empty';
      this.dom.queueClearBtn.style.display = 'none';
      if (this.dom.queueSaveBtn) this.dom.queueSaveBtn.style.display = 'none';
      this.dom.queueTitle.textContent = 'No song cued up';
      this.dom.queueArtist.textContent = 'Ask AI DJ: “Queue up [song]” or tap +Cue';
      this.dom.queueThumb.style.backgroundImage = 'none';
      this.dom.queueThumb.textContent = '🎵';
      this.dom.queueActions.style.display = 'none';
    }
  }

  prevTrack() {
    if (this.source?.type === 'audiobook') {
      // Early in a chapter: previous chapter; otherwise restart this one (like a CD player).
      const prev = this.dom.musicPlayer.currentTime < 5 ? previousChapter(this.source.book, this.source.chapter.id) : null;
      this.playChapter(this.source.book, prev || this.source.chapter);
      return;
    }
    if (this.tracks.length === 0) return;
    if (this.source) {
      this._leaveSource();
      this.playTrackByIndex(this.currentTrackIndex < 0 ? 0 : this.currentTrackIndex);
      return;
    }
    this.playTrackByIndex(
      this.currentTrackIndex <= 0 ? this.tracks.length - 1 : this.currentTrackIndex - 1
    );
  }

  // --- UI Updates ---

  _updateUI() {
    const isLive = this.fsm.state !== 'offline';
    const isListening = this.fsm.state === 'listening';

    // The Live switch is strictly two-state: ON (any live sub-state) / OFF.
    // Sub-state (listening, thinking, speaking…) is shown by the readout.
    this.dom.liveBtn.className = 'live-toggle' + (isLive ? ' on' : '');
    this.dom.liveBtnIcon.textContent = isLive ? '🔴' : '🎙️';
    this.dom.liveBtnText.textContent = isLive ? 'On Air' : 'Go Live';
    this.dom.liveBtn.setAttribute('aria-checked', String(isLive));
    this.dom.liveBtn.title = isLive
      ? (isListening ? 'Listening — just talk. Click to end Live mode.' : 'AI DJ is live. Click to end Live mode.')
      : 'Switch Live mode on and just speak (“Hey DJ” works too)';
    if (!isLive && this.dom.aiDetail) {
      // Off air: the readout carries the how-to hint (there is no separate label any more).
      this.dom.aiDetail.textContent = this.libraryLoading && this.libraryLoading.phase !== 'failed' ? 'Scanning your music library…'
        : (this.serverReady ? 'Say “Hey DJ” or switch Live on.' : 'Preparing AI DJ…');
    }

    const track = !this.source && this.currentTrackIndex >= 0 ? this.tracks[this.currentTrackIndex] : null;
    const src = describeSource(this.source, { nowTitle: this.radioNowTitle });

    if (this.source) {
      const stateText = this.isPlaying ? (src.isLive ? '🔴 Live' : '▶ Now Playing') : (src.isLive ? '⏹ Stopped' : '⏸ Paused');
      this.dom.nowPlayingState.textContent = stateText;
      this.dom.nowPlayingState.className = 'now-playing-label' + (this.isPlaying ? ' active' : '') + (src.isLive ? ' live' : '');
      this.dom.nowPlayingTitle.textContent = src.title;
      this.dom.nowPlayingArtist.textContent = src.artist;
      if (this.dom.nowPlayingSource) {
        this.dom.nowPlayingSource.style.display = 'inline-flex';
        this.dom.nowPlayingSource.className = 'now-playing-source ' + (src.isLive ? 'live' : this.source.type);
        this.dom.nowPlayingSource.innerHTML = `<span class="source-badge">${src.isLive ? '<i class="live-dot"></i>' : ''}${src.badge}</span>`
          + (src.sourceLine ? `<span class="source-line">${escapeHtml(src.sourceLine)}</span>` : '');
      }
      if (this.dom.nowPlayingPlaylist) this.dom.nowPlayingPlaylist.style.display = 'none';
    } else {
      this.dom.nowPlayingState.textContent = this.isPlaying ? '▶ Now Playing' : '⏸ Paused';
      this.dom.nowPlayingState.className = this.isPlaying ? 'now-playing-label active' : 'now-playing-label';
      this.dom.nowPlayingTitle.textContent = track ? track.title : (this.libraryLoading && this.libraryLoading.phase !== 'failed' ? '(Loading library…)' : '(Ready)');
      this.dom.nowPlayingArtist.textContent = track ? track.artist : '—';
      if (this.dom.nowPlayingSource) this.dom.nowPlayingSource.style.display = 'none';
      this._updateActivePlaylistUI(track);
    }
    this.dom.seekSlider.disabled = src.isLive;
    this.dom.seekSlider.classList.toggle('live', src.isLive);
    const kind = sourceKind(this.source);
    const showSkips = !!kind?.seekable;
    const showSpeed = !!kind?.speed;
    if (this.dom.skipBackBtn) this.dom.skipBackBtn.style.display = showSkips ? '' : 'none';
    if (this.dom.skipFwdBtn) this.dom.skipFwdBtn.style.display = showSkips ? '' : 'none';
    if (this.dom.speedControl) {
      this.dom.speedControl.style.display = showSpeed ? '' : 'none';
      this.dom.speedControl.classList.toggle('active', Math.abs(this.podcastSpeed - 1) > 0.01);
    }
    if (this.dom.speedSelect) {
      const v = String(this.podcastSpeed);
      if (![...this.dom.speedSelect.options].some(o => o.value === v)) {
        const opt = document.createElement('option');
        opt.value = v; opt.textContent = `${v}×`;
        this.dom.speedSelect.appendChild(opt);
      }
      this.dom.speedSelect.value = v;
    }

    // Fixed artwork tile (no spinning disc): kind badge for non-library
    // sources, a small equalizer while something is actually playing.
    this.dom.nowThumb.className = 'now-thumb' + (this.isPlaying ? ' playing' : '') + (kind ? ` kind-${this.source.type}` : '');
    if (this.dom.nowThumbKind) {
      this.dom.nowThumbKind.style.display = kind ? '' : 'none';
      this.dom.nowThumbKind.textContent = kind ? kind.icon : '';
      this.dom.nowThumbKind.title = kind ? kind.label : '';
    }
    if (this.dom.nowThumbPlaying) this.dom.nowThumbPlaying.style.display = this.isPlaying ? '' : 'none';
    this._updateEpisodePanel();
    this.dom.playPauseIcon.textContent = this.isPlaying ? (src.isLive ? '⏹' : '⏸') : '▶';

    this.trackRenderer.render(this.tracks, track?.id ?? null, this.trackFilterQuery);
    this._refreshExpandedHighlight(track?.id ?? null);
    this._refreshSourceHighlights();
  }

  /** Is `track` one of the active playlist's songs? (unknown membership counts as yes) */
  _isTrackInActivePlaylist(track) {
    const pl = this.activePlaylist;
    if (!pl || !track) return false;
    const ids = pl.trackIds || (pl.tracks || []).map(t => t.id);
    return ids.length === 0 ? true : ids.includes(track.id);
  }

  /**
   * Salient "which playlist is driving the deck" cues: a line under the
   * now-playing artist, the NOW PLAYING pill on the playlist card, and the
   * Up Next count.
   */
  _updateActivePlaylistUI(track) {
    const el = this.dom.nowPlayingPlaylist;
    const pl = this.activePlaylist;
    if (el) {
      if (!pl) {
        el.style.display = 'none';
        el.className = 'now-playing-playlist';
        el.textContent = '';
      } else if (track && !this._isTrackInActivePlaylist(track)) {
        el.style.display = 'inline-flex';
        el.className = 'now-playing-playlist one-off';
        el.innerHTML = `<span class="npp-icon">📑</span><span class="npp-text">One-off song — back to <b>${escapeHtml(pl.name)}</b> after this</span>`;
        el.title = `“${pl.name}” resumes after this track`;
      } else {
        el.style.display = 'inline-flex';
        el.className = 'now-playing-playlist';
        el.innerHTML = `<span class="eq-bars" aria-hidden="true"><i></i><i></i><i></i></span><span class="npp-label">Playlist</span><span class="npp-text"><b>${escapeHtml(pl.name)}</b></span>`;
        el.title = `Playing from playlist “${pl.name}”`;
      }
    }
    // Playlist cards: the NOW PLAYING pill is rendered by renderPlaylists(); just
    // make sure the card classes follow the active playlist when it changes.
    const cards = this.dom.playlistsList?.querySelectorAll('.playlist-card') || [];
    cards.forEach(card => {
      const isActive = !!pl && card.dataset.playlistId === pl.id;
      if (card.classList.contains('active-playlist') !== isActive) {
        card.classList.toggle('active-playlist', isActive);
        const pill = card.querySelector('.badge-now-playing');
        if (isActive && !pill) {
          card.querySelector('.playlist-name-row')?.insertAdjacentHTML('beforeend', this._nowPlayingPillHtml());
        } else if (!isActive && pill) {
          pill.remove();
        }
      }
    });
  }

  _nowPlayingPillHtml() {
    return '<span class="badge-now-playing" title="This playlist is playing now"><span class="eq-bars" aria-hidden="true"><i></i><i></i><i></i></span>NOW PLAYING</span>';
  }

  /** Move the "now playing" highlight inside an expanded playlist card without re-rendering it. */
  _refreshExpandedHighlight(currentTrackId) {
    const body = this.dom.playlistsList?.querySelector('.playlist-card.expanded .playlist-expanded-tracks');
    if (!body || !this.expandedPlaylist) return;
    const tracks = this.expandedPlaylist.tracks || [];
    body.querySelectorAll('.track-item').forEach((el, idx) => {
      el.classList.toggle('track-active', tracks[idx]?.id === currentTrackId);
    });
  }

  _onTimeUpdate() {
    if (this.isDraggingSeek) return;
    const dur = this.dom.musicPlayer.duration;
    if (this.source?.type === 'radio' || dur === Infinity) {
      // Live stream: no seekable range, show time on air instead.
      this.dom.seekSlider.value = 0;
      this.dom.timeCurrent.textContent = formatTime(this.dom.musicPlayer.currentTime);
      this.dom.timeDuration.textContent = 'LIVE';
      return;
    }
    if (!dur) return;
    if (this.source?.type === 'podcast') {
      this._rememberEpisodePosition();
      this._updateEpisodeRemaining();
    } else if (this.source?.type === 'audiobook') {
      this._saveBookProgress();
      this._updateEpisodeRemaining();
    }

    const pct = (this.dom.musicPlayer.currentTime / dur) * 100;
    this.dom.seekSlider.value = pct || 0;
    this.dom.timeCurrent.textContent = formatTime(this.dom.musicPlayer.currentTime);
    this.dom.timeDuration.textContent = formatTime(dur);
  }

  _setStatus(text, live = false) {
    this.dom.statusText.textContent = text;
    this.dom.statusDot.style.display = live ? 'block' : 'none';
    this.dom.statusBadge.className = `status-badge ${live ? 'status-live' : 'status-off'}`;
  }

  _bindWakeWord() {
    this.wakeWord.addEventListener('wake', ({ detail }) => {
      if (this.serverReady && this.fsm.state === 'offline') {
        const request = detail?.request || '';
        this._setWakeWordStatus(request
          ? 'Request heard — connecting you live'
          : 'Hey DJ heard — connecting you live');
        this.goLive(request);
      }
    });
    this.wakeWord.addEventListener('followup', ({ detail }) => {
      const command = detail?.command?.trim();
      if (!command || this.fsm.state !== 'idle' || !this.socket.isOpen) return;
      this._stopCountdown();
      this.audio.duck();
      this._setWakeWordStatus('Follow-up heard — working on it');
      this.socket.send(JSON.stringify({ type: 'followup_text', text: command }));
      this.fsm.transition('processing', 'Processing your follow-up…');
    });
    this.wakeWord.addEventListener('status', ({ detail }) => {
      const messages = {
        listening: 'Listening for “Hey DJ”',
        listening_followup: 'Listening for a follow-up command',
        unsupported: 'Voice invocation is unavailable — use Talk Live',
        blocked: 'Microphone is blocked — use Talk Live after allowing it',
        error: 'Voice invocation paused — use Talk Live',
      };
      if (messages[detail]) this._setWakeWordStatus(messages[detail]);
    });
  }

  _startWakeWord() {
    if (this.serverReady && this.fsm.state === 'offline') {
      this.wakeWord.start();
    }
  }

  _startFollowUpListener() {
    if (this.fsm.state === 'idle' && this.socket.isOpen) {
      this.wakeWord.start('followup');
    }
  }

  _setWakeWordStatus(text) {
    // The "Voice invocation" card is gone from the GUI; keep the wake-word
    // feature itself and just trace its status in the console.
    console.log(`[WakeWord] ${text}`);
  }

  _sendPlaybackState() {
    const track = !this.source && this.currentTrackIndex >= 0 ? this.tracks[this.currentTrackIndex] : null;
    const player = this.dom.musicPlayer;
    this.socket.send(JSON.stringify({
      type: 'playback_state',
      playing: this.isPlaying,
      trackId: track?.id ?? null,
      queue: this.queue.map(t => ({ id: t.id, title: t.title, artist: t.artist, kind: t.kind || 'track' })),
      playlist: this.activePlaylist ? { id: this.activePlaylist.id, name: this.activePlaylist.name } : null,
      source: buildSourcePayload(this.source, {
        position: player.currentTime,
        duration: isFinite(player.duration) ? player.duration : null,
        nowTitle: this.radioNowTitle,
      }),
      playbackRate: player.playbackRate || 1,
      volume: this.userVolume,
    }));
  }

  _setAiState(state, detail) {
    const cfg = AI_STATES[state] || AI_STATES.offline;
    this.dom.aiEmoji.textContent = cfg.emoji;
    this.dom.aiName.textContent = cfg.name;
    this.dom.aiName.style.color = cfg.color;
    this.dom.aiDetail.textContent = detail || '';

    if (state === 'offline') {
      this.dom.aiPanel.classList.remove('state-active');
    } else {
      this.dom.aiPanel.classList.add('state-active');
    }
  }

  _showDjSpeaking() {
    this.audio.duck();
  }

  _hideDjSpeaking() {
    this.audio.fadeUp();
  }

  _resetSessionWatchdog() {
    this._clearSessionWatchdog();
    if (this.fsm.state !== 'offline') {
      this.sessionTimer = setTimeout(() => {
        console.warn('[App] Session timeout — forcing end');
        this.endSession();
      }, CONFIG.SESSION_TIMEOUT_MS);
    }
  }

  _clearSessionWatchdog() {
    if (this.sessionTimer) {
      clearTimeout(this.sessionTimer);
      this.sessionTimer = null;
    }
  }

  _startToolTimer(id) {
    this.toolMetrics.set(id, performance.now());
  }

  _endToolTimer(id, fallbackStart) {
    const start = this.toolMetrics.get(id) || fallbackStart;
    if (start) {
      console.log(`[Telemetry] ${id}: ${(performance.now() - start).toFixed(2)}ms`);
      this.toolMetrics.delete(id);
    }
  }

  // --- View Management ---

  /**
   * Browse-panel views, one per source. To add a source (audiobooks, a
   * streaming service, …): add a tab + view in index.html and one entry here.
   */
  get views() {
    return {
      tracks: { tab: this.dom.tabTracks, view: this.dom.tracksView, render: () => this.trackRenderer.refresh() },
      radio: { tab: this.dom.tabRadio, view: this.dom.radioView, render: () => this.renderStations() },
      podcasts: { tab: this.dom.tabPodcasts, view: this.dom.podcastsView, render: () => this.renderPodcasts() },
      audiobooks: { tab: this.dom.tabAudiobooks, view: this.dom.audiobooksView, render: () => this.renderAudiobooks() },
      streaming: { tab: this.dom.tabStreaming, view: this.dom.streamingView, render: () => this.renderStreaming() },
    };
  }

  switchView(viewName) {
    const views = this.views;
    if (!views[viewName]) return;
    this.currentView = viewName;
    for (const [name, v] of Object.entries(views)) {
      v.tab?.classList.toggle('active', viewName === name);
      v.tab?.setAttribute('aria-selected', String(viewName === name));
      if (v.view) v.view.style.display = viewName === name ? 'flex' : 'none';
    }
    views[viewName].render();
    this._revealActiveTab();
    this._updateTabArrows();
  }

  /** Shared wiring for the filter boxes on the Radio / Podcasts tabs. */
  _bindFilter(input, clearBtn, onChange) {
    if (!input) return;
    input.addEventListener('input', (e) => {
      const q = e.target.value;
      if (clearBtn) clearBtn.style.display = q ? 'inline' : 'none';
      onChange(q);
    });
    clearBtn?.addEventListener('click', () => {
      input.value = '';
      clearBtn.style.display = 'none';
      onChange('');
    });
  }

  // --- Queue accordion (Up Next card expands in place) ---

  toggleQueueExpanded(force) {
    this.queueExpanded = typeof force === 'boolean' ? force : !this.queueExpanded;
    this._renderQueueExpanded();
  }

  _renderQueueExpanded() {
    if (!this.dom.queueExpanded || !this.dom.queueTracksContainer) return;
    // The preference survives an empty queue: it simply has nothing to show.
    const open = this.queueExpanded && this.queue.length > 0;
    this._setViewToggle(this.dom.queueViewBtn, open, 'the full queue');
    // The summary card repeats the first row: hide it while the full list is open to give the list the room.
    if (this.dom.queueCard) this.dom.queueCard.style.display = open ? 'none' : '';
    if (!open) {
      this._collapseBody(this.dom.queueExpanded).then(() => {
        if (this.queue.length === 0) this.dom.queueTracksContainer.replaceChildren();
      });
      return;
    }
    const wasOpen = this.dom.queueExpanded.classList.contains('open');

    const frag = document.createDocumentFragment();
    this.queue.forEach((track, idx) => {
      const el = document.createElement('div');
      el.className = 'track-item queue-item';
      el.dataset.queueIndex = idx;
      el.draggable = true;
      el.addEventListener('dragstart', (e) => {
        e.dataTransfer.setData(DND.QUEUE, String(idx));
        e.dataTransfer.effectAllowed = 'move';
        el.classList.add('dragging');
        this._dragging = { type: DND.QUEUE, index: idx };
      });
      el.addEventListener('dragend', () => { el.classList.remove('dragging'); this._dragging = null; this._clearDropMarks(); });
      el.innerHTML = `
        <div class="track-info">
          <div class="track-title" title="${escapeHtml(track.title)}">${escapeHtml(track.title)}</div>
          <div class="track-artist">${escapeHtml(track.artist)}</div>
        </div>
        <div class="track-genre">${escapeHtml(track.genre || '')}</div>
        <button class="track-play-btn queue-item-play-btn" title="Play this now (the rest stays cued)">▶ Play</button>
        <button class="track-remove-from-pl-btn queue-item-remove-btn" title="Remove from queue">✕</button>
      `;
      el.querySelector('.queue-item-play-btn').addEventListener('click', (e) => {
        e.stopPropagation();
        this.playQueueItemNow(idx);
      });
      el.querySelector('.queue-item-remove-btn').addEventListener('click', (e) => {
        e.stopPropagation();
        this.removeFromQueue(idx);
      });
      frag.appendChild(el);
    });
    this.dom.queueTracksContainer.replaceChildren(frag);
    if (!wasOpen) {
      // The cue is a flex child that sizes to the room available (not to its
      // rows), so the measured height animation cannot apply: open it directly.
      this.dom.queueExpanded.classList.add('open');
      this.dom.queueExpanded.style.opacity = '1';
    }
    this.dom.queueExpanded.style.height = 'auto';
  }

  /**
   * Collapse a card's expanded body with the CSS transition, resolving when it
   * has finished (or after a safety timeout), so the caller can re-render.
   */
  _animateCollapse(card) {
    const body = card?.querySelector('.playlist-expanded.open');
    if (!body) return Promise.resolve();
    card.classList.remove('expanded');
    this._setViewToggle(card.querySelector('.view-toggle-btn'), false);
    return this._collapseBody(body);
  }

  /**
   * Smoothly open a collapsible body: measure its natural height, animate
   * 0 → that height, then release to `auto` so it can keep growing.
   */
  _expandBody(body) {
    if (!body || body.classList.contains('open')) return;
    body.classList.add('open');
    body.style.height = '0px';
    body.style.opacity = '0';
    const target = body.scrollHeight;
    requestAnimationFrame(() => {
      body.style.height = `${target}px`;
      body.style.opacity = '1';
    });
    const release = (e) => {
      if (e && e.target !== body) return;
      body.style.height = 'auto';
      body.removeEventListener('transitionend', release);
    };
    body.addEventListener('transitionend', release);
    setTimeout(release, 450);
  }

  /** Smoothly close a collapsible body; resolves when done. */
  _collapseBody(body) {
    if (!body || !body.classList.contains('open')) return Promise.resolve();
    return new Promise(resolve => {
      body.style.height = `${body.scrollHeight}px`;
      body.getBoundingClientRect();                 // commit the start height before animating
      let done = false;
      const finish = (e) => {
        if (e && e.target !== body) return;
        if (done) return;
        done = true;
        body.removeEventListener('transitionend', finish);
        body.classList.remove('open');
        body.style.height = '';
        body.style.opacity = '';
        resolve();
      };
      body.addEventListener('transitionend', finish);
      setTimeout(finish, 450);
      requestAnimationFrame(() => {
        body.style.height = '0px';
        body.style.opacity = '0';
      });
    });
  }

  /**
   * Shared look/behaviour for every expand/collapse control: a double chevron
   * pointing down when collapsed and up when open, sitting at the right edge
   * of its card.
   */
  _setViewToggle(btn, open, what = 'tracks') {
    if (!btn) return;
    btn.innerHTML = '<span class="chevron2" aria-hidden="true"></span>';
    btn.title = open ? `Hide ${what}` : `Show ${what}`;
    btn.setAttribute('aria-label', btn.title);
    btn.classList.toggle('open', open);
    btn.setAttribute('aria-expanded', String(open));
  }

  /**
   * Rows in a list: click selects (toggle), double-click or Enter plays.
   * `rows` are the elements, `onPlay(row)` starts playback.
   */
  _wireSelectableRows(container, rowSelector, onPlay, selectedClass = 'track-selected') {
    if (!container) return;
    const rows = container.querySelectorAll(rowSelector);
    rows.forEach(row => {
      row.tabIndex = 0;
      row.addEventListener('click', (e) => {
        if (e.target.closest('button, a, input')) return;
        const wasSelected = row.classList.contains(selectedClass);
        rows.forEach(r => r.classList.remove(selectedClass));
        if (!wasSelected) row.classList.add(selectedClass);
      });
      row.addEventListener('dblclick', (e) => {
        if (e.target.closest('button, a, input')) return;
        onPlay(row);
      });
      row.addEventListener('keydown', (e) => {
        if (e.target !== row || (e.key !== 'Enter' && e.key !== ' ')) return;
        e.preventDefault();
        onPlay(row);
      });
    });
  }

  /** Jump to a queued track: everything before it is skipped, after it stays cued. */
  /** ▶ on a cued row: play that item right away; everything else stays cued in place. */
  playQueueItemNow(index) {
    if (index < 0 || index >= this.queue.length) return;
    const [item] = this.queue.splice(index, 1);
    this._updateQueueUI();
    this._playQueueItem(item);
  }

  // --- Playlist Management & Operations ---

  async loadPlaylists() {
    try {
      const res = await fetch('/api/playlists');
      if (res.ok) {
        this.playlists = await res.json();
        this._updatePlaylistBadges();
        this.renderPlaylists();
      }
    } catch (e) {
      console.error('[App] Failed to load playlists:', e);
    }
  }

  _updatePlaylistBadges() {
    if (this.dom.badgeTracksCount) this.dom.badgeTracksCount.textContent = this.tracks.length;
    if (this.dom.playlistsCount) this.dom.playlistsCount.textContent = `${this.playlists.length}`;
    if (this.dom.badgeRadioCount) this.dom.badgeRadioCount.textContent = this.stations.length;
    if (this.dom.badgePodcastsCount) this.dom.badgePodcastsCount.textContent = this.podcasts.length;
    if (this.dom.badgeAudiobooksCount) this.dom.badgeAudiobooksCount.textContent = this.audiobooks.length;
    if (this.dom.badgeStreamingCount) {
      const provs = this.streamingStatus?.providers || [];
      const avail = provs.filter(p => p.available);
      this.dom.badgeStreamingCount.textContent = provs.length ? avail.length : '…';
      this.dom.badgeStreamingCount.title = provs.length
        ? `${avail.length} of ${provs.length} streaming sources available: ${avail.map(p => p.name).join(', ') || 'none'}`
        : 'Checking streaming sources…';
    }
  }

  renderPlaylists() {
    if (!this.dom.playlistsList) return;
    const q = (this.playlistFilterQuery || '').toLowerCase().trim();
    const filtered = q
      ? this.playlists.filter(p => p.name.toLowerCase().includes(q) || (p.description && p.description.toLowerCase().includes(q)))
      : this.playlists;

    if (filtered.length === 0) {
      this.dom.playlistsList.innerHTML = `
        <div style="padding:24px; text-align:center; opacity:0.6; font-size:12px;">
          ${q ? 'No playlists matching search' : 'No playlists yet. Ask AI DJ (e.g. "Play some 80\'s music") or click + New Playlist!'}
        </div>
      `;
      return;
    }

    const frag = document.createDocumentFragment();
    filtered.forEach(pl => {
      const isOpen = this.expandedPlaylist?.id === pl.id;
      const card = document.createElement('div');
      card.className = 'playlist-card'
        + (this.activePlaylist?.id === pl.id ? ' active-playlist' : '')
        + (isOpen ? ' expanded' : '');
      card.dataset.playlistId = pl.id;
      this._makePlaylistDropTarget(card, pl);
      const coverArtUrl = pl.coverTrackId ? `/api/tracks/${pl.coverTrackId}/art` : '';
      const metaText = `${pl.trackCount} track${pl.trackCount === 1 ? '' : 's'}${pl.description ? ` • ${pl.description}` : ''}`;

      card.innerHTML = `
        <div class="playlist-card-header">
          <div class="playlist-cover" style="${coverArtUrl ? `background-image: url('${coverArtUrl}');` : ''}">${coverArtUrl ? '' : '📑'}</div>
          <div class="playlist-info">
            <div class="playlist-name-row">
              <span class="playlist-name" title="${escapeHtml(pl.name)}">${escapeHtml(pl.name)}</span>
              ${pl.isDynamic ? '<span class="badge-ai">AI Curated</span>' : ''}
              ${this.activePlaylist?.id === pl.id ? this._nowPlayingPillHtml() : ''}
            </div>
            <div class="playlist-meta" title="${escapeHtml(metaText)}">${escapeHtml(metaText)}</div>
          </div>
          <div class="playlist-actions">
            <button class="playlist-btn-play" title="Play playlist">▶ Play</button>
            <button class="playlist-btn-cue" title="Add all tracks to the queue">+ Cue</button>
            <button class="playlist-btn-subtle danger" title="Delete playlist">✕</button>
            <button class="view-toggle-btn" type="button"></button>
          </div>
        </div>
        <div class="playlist-expanded${isOpen && this._openedWithoutAnimation === pl.id ? ' open' : ''}">
          <div class="playlist-expanded-inner">
            <div class="playlist-expanded-actions">
              <span class="playlist-expanded-sub"></span>
              <button class="playlist-action-btn playlist-btn-shuffle" type="button" title="Shuffle &amp; play">🔀 Shuffle</button>
            </div>
            <div class="track-list playlist-expanded-tracks"></div>
          </div>
        </div>
      `;
      if (isOpen && this._openedWithoutAnimation !== pl.id) {
        // Newly opened: animate from collapsed once the card is in the DOM.
        const body = card.querySelector('.playlist-expanded');
        requestAnimationFrame(() => this._expandBody(body));
        this._openedWithoutAnimation = pl.id;
      }

      this._setViewToggle(card.querySelector('.view-toggle-btn'), isOpen, 'tracks');

      card.querySelector('.playlist-btn-play').addEventListener('click', (e) => {
        e.stopPropagation();
        this.playPlaylist(pl.id);
      });
      card.querySelector('.playlist-btn-cue').addEventListener('click', (e) => {
        e.stopPropagation();
        this.cuePlaylist(pl.id);
      });
      card.querySelector('.view-toggle-btn').addEventListener('click', (e) => {
        e.stopPropagation();
        this.togglePlaylistExpanded(pl.id);
      });
      card.querySelector('.playlist-btn-subtle.danger').addEventListener('click', async (e) => {
        e.stopPropagation();
        const ok = await this.showConfirm({
          title: 'Delete playlist?',
          message: `"${pl.name}" (${pl.trackCount} track${pl.trackCount === 1 ? '' : 's'}) will be deleted. Your music files are not affected.`,
          confirmText: 'Delete', danger: true,
        });
        if (ok) await this.deletePlaylist(pl.id);
      });
      card.querySelector('.playlist-btn-shuffle').addEventListener('click', (e) => {
        e.stopPropagation();
        this.playPlaylist(pl.id, true);
      });
      // Clicks inside the expanded body (track rows, buttons) must not collapse it
      card.querySelector('.playlist-expanded').addEventListener('click', (e) => e.stopPropagation());
      card.addEventListener('click', () => this.togglePlaylistExpanded(pl.id));

      if (isOpen) this._renderPlaylistExpandedBody(card, this.expandedPlaylist);
      frag.appendChild(card);
    });

    this.dom.playlistsList.replaceChildren(frag);
  }

  /**
   * Accordion toggle for a playlist card. Only one playlist is expanded at a
   * time; expanding fetches the resolved track list.
   */
  async togglePlaylistExpanded(playlistId, force) {
    const open = typeof force === 'boolean' ? force : this.expandedPlaylist?.id !== playlistId;
    if (!open) {
      if (this.expandedPlaylist?.id !== playlistId) return;
      await this._animateCollapse(this.dom.playlistsList?.querySelector(`.playlist-card[data-playlist-id="${playlistId}"]`));
      this.expandedPlaylist = null;
      this._openedWithoutAnimation = null;
      this.renderPlaylists();
      return;
    }
    if (this.expandedPlaylist && this.expandedPlaylist.id !== playlistId) {
      await this._animateCollapse(this.dom.playlistsList?.querySelector(`.playlist-card[data-playlist-id="${this.expandedPlaylist.id}"]`));
    }
    this._openedWithoutAnimation = null;
    try {
      const res = await fetch(`/api/playlists/${playlistId}`);
      if (!res.ok) return;
      this.expandedPlaylist = await res.json();
      this.renderPlaylists();
      this.dom.playlistsList?.querySelector(`.playlist-card[data-playlist-id="${playlistId}"]`)
        ?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    } catch (e) {
      console.error('[App] Failed to expand playlist:', e);
    }
  }

  /** Switch to the Playlists tab and expand one playlist (toast "View", create flows). */
  async showPlaylist(playlistId) {
    this.togglePlaylistsSection(true);
    await this.togglePlaylistExpanded(playlistId, true);
    this.dom.playlistsSection?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }

  /** Fold / unfold the Playlists section under the cue. */
  togglePlaylistsSection(force) {
    this.playlistsCollapsed = typeof force === 'boolean' ? !force : !this.playlistsCollapsed;
    if (this.dom.playlistsBody) this.dom.playlistsBody.style.display = this.playlistsCollapsed ? 'none' : '';
    this.dom.playlistsSection?.classList.toggle('collapsed', this.playlistsCollapsed);
    this._setViewToggle(this.dom.playlistsViewBtn, !this.playlistsCollapsed, 'the playlists');
  }

  /** Re-fetch the expanded playlist (after a track add/remove, rename, …). */
  async refreshExpandedPlaylist() {
    if (!this.expandedPlaylist) return;
    try {
      const res = await fetch(`/api/playlists/${this.expandedPlaylist.id}`);
      if (res.status === 404) { this.expandedPlaylist = null; }
      else if (res.ok) { this.expandedPlaylist = await res.json(); }
    } catch (e) {
      console.error('[App] Failed to refresh expanded playlist:', e);
    }
    this.renderPlaylists();
  }

  _renderPlaylistExpandedBody(card, pl) {
    const sub = card.querySelector('.playlist-expanded-sub');
    const container = card.querySelector('.playlist-expanded-tracks');
    if (!container) return;
    const tracks = pl.tracks || [];

    if (sub) {
      sub.textContent = `${tracks.length} track${tracks.length === 1 ? '' : 's'}${pl.isDynamic ? ' • AI Curated' : ' • Custom'}`;
    }

    if (tracks.length === 0) {
      container.innerHTML = '<div style="padding:16px; opacity:0.5; font-size:12px; text-align:center;">This playlist has no tracks. Say “add this song to ' + escapeHtml(pl.name) + '” or use + 📑 on a track.</div>';
      return;
    }

    const currentTrackId = this.tracks[this.currentTrackIndex]?.id;
    const frag = document.createDocumentFragment();
    tracks.forEach((track) => {
      const el = document.createElement('div');
      const isCurrent = currentTrackId === track.id;
      el.className = 'track-item pl-track-item' + (isCurrent ? ' track-active' : '');
      el.dataset.trackId = track.id;
      el.draggable = true;
      el.addEventListener('dragstart', (e) => {
        // Drag within the playlist to reorder, or onto the cue to queue it.
        e.dataTransfer.setData(DND.PLTRACK, JSON.stringify({ playlistId: pl.id, trackId: track.id }));
        e.dataTransfer.setData(DND.TRACKS, JSON.stringify({ trackIds: [track.id], label: track.title }));
        e.dataTransfer.effectAllowed = 'copyMove';
        el.classList.add('dragging');
        this._dragging = { type: DND.PLTRACK, playlistId: pl.id, trackId: track.id };
      });
      el.addEventListener('dragend', () => { el.classList.remove('dragging'); this._dragging = null; this._clearDropMarks(); });
      el.addEventListener('dragover', (e) => {
        if (this._dragging?.type !== DND.PLTRACK || this._dragging.playlistId !== pl.id) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
        this._markDropBefore(el, e);
      });
      el.addEventListener('drop', (e) => {
        if (this._dragging?.type !== DND.PLTRACK || this._dragging.playlistId !== pl.id) return;
        e.preventDefault();
        e.stopPropagation();
        const before = this._dropIsBefore(el, e);
        this._clearDropMarks();
        this.reorderPlaylistTrack(pl, this._dragging.trackId, track.id, before);
      });
      el.innerHTML = `
        <span class="track-lead eq-bars" aria-hidden="true"><i></i><i></i><i></i></span>
        <div class="track-info">
          <div class="track-title" title="${escapeHtml(track.title)}">${escapeHtml(track.title)}</div>
          <div class="track-artist">${escapeHtml(track.artist)}</div>
        </div>
        <div class="track-genre">${escapeHtml(track.genre || '')}</div>
        <button class="track-play-btn" title="Play this track">▶</button>
        <button class="track-cue-btn" title="Cue up next">+ Cue</button>
        <button class="track-remove-from-pl-btn" title="Remove from playlist">✕</button>
      `;
      // ▶ plays just this song (the queue is left alone); the card's ▶ Play / 🔀 Shuffle start the whole playlist.
      el.querySelector('.track-play-btn').addEventListener('click', (e) => { e.stopPropagation(); this.playTrackById(track.id); });
      el.querySelector('.track-cue-btn').addEventListener('click', (e) => {
        e.stopPropagation();
        this.queueTrackById(track.id);
      });
      el.querySelector('.track-remove-from-pl-btn').addEventListener('click', async (e) => {
        e.stopPropagation();
        await this.removeTrackFromPlaylist(pl.id, track.id);
      });
      frag.appendChild(el);
    });
    container.replaceChildren(frag);
    this._wireSelectableRows(container, '.track-item', (row) => this.playTrackById(row.dataset.trackId));
  }

  async playPlaylist(playlistId, shuffle = false) {
    try {
      const res = await fetch(`/api/playlists/${playlistId}`);
      if (!res.ok) return;
      const pl = await res.json();
      let tracks = pl.tracks || [];
      if (tracks.length === 0) return;

      if (shuffle) {
        tracks = [...tracks].sort(() => Math.random() - 0.5);
      }

      this.activePlaylist = pl;
      const first = tracks[0];
      this.queue = tracks.slice(1);
      this._updateQueueUI();
      this.playTrackById(first.id);

      this.showPlaylistToast(
        `▶ Playing ${pl.name}`,
        `${tracks.length} tracks lined up${shuffle ? ' (shuffled)' : ''}`,
        pl.id
      );

      this._sendPlaybackState();
      this.renderPlaylists();
    } catch (e) {
      console.error('[App] Failed to play playlist:', e);
    }
  }

  /** Play a set of tracks: the first now, the rest cued in order (a group header's ▶). */
  playTracks(tracks) {
    const playable = (tracks || []).filter(t => t && this.tracks.some(x => x.id === t.id));
    if (!playable.length) return;
    this._leaveSource();
    this.activePlaylist = null;
    this.queue = playable.slice(1);
    this._updateQueueUI();
    this.playTrackById(playable[0].id);
    this._sendPlaybackState();
  }

  /** Cue a set of tracks after whatever is already queued (a group header's + Cue). */
  queueTracks(tracks) {
    const queuedIds = new Set(this.queue.map(q => q.id));
    const add = (tracks || []).filter(t => t && !queuedIds.has(t.id) && this.tracks.some(x => x.id === t.id));
    if (!add.length) {
      this.showPlaylistToast('➕ Nothing new to cue', 'Those tracks are already in the queue', null);
      return;
    }
    this.queue.push(...add);
    this._updateQueueUI();
    this._sendPlaybackState();
    this.showPlaylistToast('➕ Cued', `${add.length} track${add.length === 1 ? '' : 's'} added to the queue`, null);
  }

  /** "+ Cue" on a playlist card: append its tracks after whatever is cued. */
  async cuePlaylist(playlistId) {
    try {
      const res = await fetch(`/api/playlists/${playlistId}`);
      if (!res.ok) return;
      const pl = await res.json();
      if (!(pl.tracks || []).length) {
        this.showPlaylistToast('📑 Nothing to cue', `"${pl.name}" has no tracks yet`, pl.id);
        return;
      }
      this.handlePlaylistQueued({ id: pl.id, name: pl.name }, pl.tracks);
    } catch (e) {
      console.error('[App] Cue playlist error:', e);
    }
  }

  async deletePlaylist(playlistId) {
    try {
      const res = await fetch(`/api/playlists/${playlistId}`, { method: 'DELETE' });
      if (res.ok) {
        if (this.activePlaylist?.id === playlistId) {
          this.activePlaylist = null;
          this._sendPlaybackState();
        }
        if (this.expandedPlaylist?.id === playlistId) this.expandedPlaylist = null;
        await this.loadPlaylists();
      }
    } catch (e) {
      console.error('[App] Delete playlist error:', e);
    }
  }

  async removeTrackFromPlaylist(playlistId, trackId) {
    try {
      const res = await fetch(`/api/playlists/${playlistId}/tracks/${trackId}`, { method: 'DELETE' });
      if (res.ok) {
        const updated = await res.json();
        if (this.expandedPlaylist?.id === playlistId) this.expandedPlaylist = updated;
        await this.loadPlaylists();
      }
    } catch (e) {
      console.error('[App] Remove track from playlist error:', e);
    }
  }

  // --- Dynamic AI Playlist Integration ---

  handleDynamicPlaylistLoaded(playlist, tracks, startIndex = 0) {
    if (!playlist || !tracks || tracks.length === 0) return;
    this.activePlaylist = playlist;
    const startTrack = tracks[startIndex] || tracks[0];
    this.queue = tracks.slice(startIndex + 1);

    this.showPlaylistToast(
      `✨ AI DJ: ${playlist.name}`,
      `Curated ${tracks.length} tracks • Now playing`,
      playlist.id
    );

    this.loadPlaylists();
    this.playTrackById(startTrack.id);
    this._updateQueueUI();
    this._sendPlaybackState();
  }

  // --- Voice playlist management (backend tool calls) ---

  /**
   * `queuePlaylist` command: append a saved playlist's tracks to the queue
   * without touching the current track or the active-playlist badge.
   */
  handlePlaylistQueued(playlist, tracks) {
    if (!Array.isArray(tracks) || tracks.length === 0) return;
    const known = new Set(this.tracks.map(t => t.id));
    const playable = tracks.filter(t => t && known.has(t.id));
    if (playable.length === 0) return;

    this.queue.push(...playable);
    this._updateQueueUI();
    this._sendPlaybackState();
    this.showPlaylistToast(
      `➕ Queued ${playlist?.name || 'playlist'}`,
      `${playable.length} track${playable.length === 1 ? '' : 's'} added to the queue`,
      playlist?.id || null
    );
    console.log(`[Queue] Queued playlist '${playlist?.name}' (${playable.length} tracks). Total queued: ${this.queue.length}`);
  }

  /**
   * `playlistsChanged` command: AI DJ created / updated / renamed / deleted
   * a playlist by voice. Keep every view that shows playlist data in sync.
   */
  async handlePlaylistsChanged(msg) {
    const { change, playlistId, playlist, message } = msg;
    const isDeleted = change === 'deleted';

    // Active-playlist badge (drives the `playlist` field in playback_state)
    if (msg.setActive && playlist?.id && !isDeleted) {
      // e.g. the queue was just saved as a playlist: what's playing IS it now
      this.activePlaylist = { id: playlist.id, name: playlist.name };
      this._sendPlaybackState();
    } else if (playlistId && this.activePlaylist?.id === playlistId) {
      if (isDeleted) {
        this.activePlaylist = null;
      } else if (playlist?.name && playlist.name !== this.activePlaylist.name) {
        this.activePlaylist = { ...this.activePlaylist, name: playlist.name };
      }
      this._sendPlaybackState();
    }

    // Accordion card currently expanded on the affected playlist
    if (playlistId && this.expandedPlaylist?.id === playlistId) {
      if (isDeleted) this.expandedPlaylist = null;
      else await this.refreshExpandedPlaylist();
    }

    // Playlists tab, badges and "Add to playlist" modal all read this.playlists
    await this.loadPlaylists();

    if (message) {
      const titles = {
        created: '✨ Playlist created',
        updated: '📑 Playlist updated',
        renamed: '✏️ Playlist renamed',
        deleted: '🗑 Playlist deleted',
      };
      this.showPlaylistToast(titles[change] || '📑 Playlists updated', message, isDeleted ? null : playlistId);
    }
  }

  showPlaylistToast(title, detail, playlistId) {
    if (!this.dom.playlistToast) return;
    if (this.toastTimer) clearTimeout(this.toastTimer);

    this.toastPlaylistId = playlistId || null;
    if (this.dom.playlistToastTitle) this.dom.playlistToastTitle.textContent = title;
    if (this.dom.playlistToastDetail) this.dom.playlistToastDetail.textContent = detail;
    if (this.dom.playlistToastViewBtn) this.dom.playlistToastViewBtn.style.display = this.toastPlaylistId ? '' : 'none';
    this.dom.playlistToast.style.display = 'flex';

    this.toastTimer = setTimeout(() => {
      if (this.dom.playlistToast) this.dom.playlistToast.style.display = 'none';
    }, 7000);
  }

  viewToastPlaylist() {
    if (this.toastPlaylistId) {
      this.showPlaylist(this.toastPlaylistId);
    }
  }

  // ==========================================
  // LIVE RADIO & PODCASTS
  // ==========================================

  // --- Source playback (shared by radio and podcasts) ---

  /**
   * Point the deck's <audio> at a non-library source. Same audio graph and
   * fade-in as local tracks; the URL is a same-origin backend relay.
   */
  async _beginSourcePlayback(source, url, { art = '', icon = '💿' } = {}) {
    this._flushSourceProgress();
    this._stopRadioNowPolling();
    this.radioNowTitle = '';
    this.source = source;

    this.audio.initMusicPlayer(this.dom.musicPlayer);
    if (this.audio.ctx.state === 'suspended') {
      await this.audio.ctx.resume();
    }
    this.currentPlayAbort?.abort();
    this.currentPlayAbort = new AbortController();

    const onPlaying = () => {
      this.audio.fadeTrackUp(this.userVolume, 1.0, this._shouldDuck());
    };
    this.dom.musicPlayer.addEventListener('playing', onPlaying, { once: true });
    this.dom.musicPlayer.src = url;

    this._setThumbArt(art, icon, () => this.source === source);

    if ('mediaSession' in navigator) {
      const d = describeSource(source, { nowTitle: this.radioNowTitle });
      navigator.mediaSession.metadata = new MediaMetadata({
        title: d.title || '', artist: d.artist || '',
        artwork: art ? [{ src: art, sizes: '512x512', type: 'image/jpeg' }] : [],
      });
    }

    this.isPlaying = true;
    this._applyPlaybackRate();
    this._updateUI();
    try {
      await this.dom.musicPlayer.play();
      this._applyPlaybackRate();          // some browsers reset the rate on a new src
    } catch (e) {
      if (e.name === 'AbortError') return;
      console.error('[Source] Playback error:', e);
      this.isPlaying = false;
      this._updateUI();
    }
  }

  /** Artwork tile with a probe so a missing/broken image falls back to an emoji. */
  _setThumbArt(url, fallbackIcon, stillCurrent) {
    const el = this.dom.nowThumb;
    el.style.backgroundImage = 'none';
    el.textContent = fallbackIcon;
    if (!url) return;
    const img = new Image();
    img.onload = () => {
      if (!stillCurrent()) return;
      el.style.backgroundImage = `url('${url}')`;
      el.textContent = '';
    };
    img.onerror = () => {
      if (!stillCurrent()) return;
      el.style.backgroundImage = 'none';
      el.textContent = fallbackIcon;
    };
    img.src = url;
  }

  /**
   * Long descriptions (podcast blurbs, episode notes) are clamped to a few
   * lines; a "More" link appears only when the text actually overflows, and
   * toggles the full text ("Less"). The overflow check runs once the element
   * is laid out, so this works for rows built in a fragment too.
   */
  _setClampedText(el, text, lines = 2) {
    el.textContent = text || '';
    el.classList.add('clampable');
    el.classList.remove('expanded');
    el.style.setProperty('--clamp-lines', String(lines));
    el.nextElementSibling?.classList?.contains('clamp-toggle') && el.nextElementSibling.remove();
    let tries = 0;
    const check = () => {
      if (!el.isConnected) { if (tries++ < 30) requestAnimationFrame(check); return; }
      if (el.scrollHeight <= el.clientHeight + 1) return;
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'link-btn clamp-toggle';
      btn.textContent = 'More';
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const open = el.classList.toggle('expanded');
        btn.textContent = open ? 'Less' : 'More';
      });
      el.insertAdjacentElement('afterend', btn);
    };
    requestAnimationFrame(check);
  }

  /**
   * Podcast episode details under the NOW PLAYING header: number • date •
   * length • time left, plus show notes (clamped, expandable).
   */
  _updateEpisodePanel() {
    const panel = this.dom.episodePanel;
    if (!panel) return;
    const ep = this.source?.type === 'podcast' ? this.source.episode : null;
    const ch = this.source?.type === 'audiobook' ? this.source.chapter : null;
    if (!ep && !ch) {
      panel.style.display = 'none';
      this.episodeNotesExpanded = false;
      return;
    }
    panel.style.display = '';
    if (this.dom.episodePanelLabel) this.dom.episodePanelLabel.textContent = ch ? 'Chapter' : 'Episode';
    if (ch) {
      const book = this.source.book || {};
      if (this.dom.episodeMeta) this.dom.episodeMeta.textContent = chapterMetaLine(book, ch) || 'Chapter';
      const about = [book.author ? `By ${book.author}` : '', book.narrator ? `Read by ${book.narrator}` : '',
        book.duration ? `Book length ${formatDuration(book.duration)}` : ''].filter(Boolean).join(' • ');
      if (this.dom.episodeNotes) {
        this.dom.episodeNotes.textContent = about || 'Audiobook chapter';
        this.dom.episodeNotes.classList.add('clamped');
        this.dom.episodeNotes.classList.toggle('muted', !about);
      }
      if (this.dom.episodeNotesToggle) this.dom.episodeNotesToggle.style.display = 'none';
      this._updateEpisodeRemaining();
      return;
    }
    if (this.dom.episodeMeta) this.dom.episodeMeta.textContent = episodeMetaLine(ep) || 'Episode';
    const notes = (ep.description || '').trim();
    if (this.dom.episodeNotes) {
      this.dom.episodeNotes.textContent = notes || 'No show notes for this episode.';
      this.dom.episodeNotes.classList.toggle('clamped', !this.episodeNotesExpanded);
      this.dom.episodeNotes.classList.toggle('muted', !notes);
    }
    if (this.dom.episodeNotesToggle) {
      this.dom.episodeNotesToggle.style.display = notes.length > 160 ? '' : 'none';
      this.dom.episodeNotesToggle.innerHTML = `<span class="chevron2" aria-hidden="true"></span> ${this.episodeNotesExpanded ? 'Less' : 'More'}`;
      this.dom.episodeNotesToggle.classList.toggle('open', this.episodeNotesExpanded);
      this.dom.episodeNotesToggle.setAttribute('aria-expanded', String(this.episodeNotesExpanded));
    }
    this._updateEpisodeRemaining();
  }

  toggleEpisodeNotes() {
    this.episodeNotesExpanded = !this.episodeNotesExpanded;
    this._updateEpisodePanel();
  }

  _updateEpisodeRemaining() {
    if (!this.dom.episodeRemaining) return;
    const type = this.source?.type;
    if (type !== 'podcast' && type !== 'audiobook') { this.dom.episodeRemaining.textContent = ''; return; }
    const player = this.dom.musicPlayer;
    const known = type === 'podcast' ? this.source.episode?.duration : this.source.chapter?.duration;
    const dur = isFinite(player.duration) && player.duration > 0 ? player.duration : (known || 0);
    const label = remainingLabel(player.currentTime, dur);
    this.dom.episodeRemaining.textContent = label ? `• ${label}` : '';
  }

  /**
   * In-app confirmation dialog (replaces window.confirm so every dialog looks
   * and behaves the same). Resolves true when confirmed.
   */
  showConfirm({ title = 'Are you sure?', message = '', confirmText = 'OK', cancelText = 'Cancel', danger = false } = {}) {
    if (!this.dom.confirmModal) return Promise.resolve(window.confirm(message || title));
    this._resolveConfirm(false);                   // close any dialog already up
    if (this.dom.confirmTitle) this.dom.confirmTitle.textContent = title;
    if (this.dom.confirmMessage) this.dom.confirmMessage.textContent = message;
    if (this.dom.confirmOkBtn) {
      this.dom.confirmOkBtn.textContent = confirmText;
      this.dom.confirmOkBtn.className = 'modal-btn-confirm' + (danger ? ' danger' : '');
    }
    if (this.dom.confirmCancelBtn) {
      this.dom.confirmCancelBtn.textContent = cancelText;
      this.dom.confirmCancelBtn.style.display = cancelText ? '' : 'none';
    }
    this.dom.confirmModal.style.display = 'flex';
    this.dom.confirmOkBtn?.focus();
    return new Promise(resolve => { this._confirmResolve = resolve; });
  }

  /** Informational dialog with a single OK button (replaces window.alert). */
  showAlert(title, message = '') {
    return this.showConfirm({ title, message, confirmText: 'OK', cancelText: '' });
  }

  _resolveConfirm(value) {
    if (this.dom.confirmModal) this.dom.confirmModal.style.display = 'none';
    const resolve = this._confirmResolve;
    this._confirmResolve = null;
    if (resolve) resolve(value);
  }

  _updateSortBar() {
    const bar = this.dom.trackSortBar;
    if (!bar) return;
    bar.querySelectorAll('.sort-btn[data-sort]').forEach(btn => {
      const active = (btn.dataset.sort || '') === this.trackRenderer.sortKey;
      btn.classList.toggle('active', active);
      const base = btn.textContent.replace(/\s*[▲▼]$/, '');
      btn.textContent = active && this.trackRenderer.sortKey ? `${base} ${this.trackRenderer.sortDir > 0 ? '▲' : '▼'}` : base;
      btn.setAttribute('aria-pressed', String(active));
    });
    const toggle = this.dom.trackGroupsToggle;
    if (toggle) {
      const grouped = !!this.trackRenderer.groupMode && this.trackRenderer.groups.length > 0;
      toggle.style.display = grouped ? '' : 'none';
      toggle.textContent = this.trackRenderer.allCollapsed ? 'Expand all' : 'Collapse all';
      toggle.title = this.trackRenderer.allCollapsed ? 'Expand every group' : 'Collapse every group to its header';
    }
  }

  // --- Podcast playback speed ---

  _loadPodcastSpeed() {
    const v = parseFloat(window.localStorage.getItem('aidj.podcastSpeed') || '1');
    return isFinite(v) && v >= 0.5 && v <= 3 ? v : 1;
  }

  /** Apply a rate to the player. Only seekable sources (podcasts) keep a custom speed. */
  setPlaybackSpeed(rate, { remember = true } = {}) {
    const r = Math.min(3, Math.max(0.5, Number(rate) || 1));
    const kind = sourceKind(this.source);
    if (kind?.speed) {
      this.podcastSpeed = r;
      if (remember) { try { window.localStorage.setItem('aidj.podcastSpeed', String(r)); } catch (e) { /* ignore */ } }
    }
    this._applyPlaybackRate();
    this._updateUI();
    this._sendPlaybackState();
    return this.dom.musicPlayer.playbackRate;
  }

  /** The app's music volume (slider). Used by the slider, the DJ's setVolume tool and the UI. */
  setUserVolume(level, { fromSlider = false } = {}) {
    if (!isFinite(level)) return;
    this.userVolume = clamp(level, 0, 1);
    if (!fromSlider && this.dom.volumeSlider) this.dom.volumeSlider.value = String(this.userVolume);
    if (this.audio._musicSource) {
      this.audio.setVolume(this.userVolume, { ducked: this._shouldDuck() });
    } else {
      this.dom.musicPlayer.volume = this.userVolume;
    }
    if (!fromSlider) this._sendPlaybackState();
  }

  /** Music, live radio and streamed tracks always play at 1×; podcasts and audiobooks at the remembered speed. */
  _applyPlaybackRate() {
    const rate = sourceKind(this.source)?.speed ? this.podcastSpeed : 1;
    const player = this.dom.musicPlayer;
    if (Math.abs(player.playbackRate - rate) > 0.001) player.playbackRate = rate;
    player.defaultPlaybackRate = rate;
  }

  _loadStarterDismissed() {
    try {
      const parsed = JSON.parse(window.localStorage.getItem(STARTER_NOTE_KEY) || '{}');
      return parsed && typeof parsed === 'object' ? parsed : {};
    } catch (e) {
      return {};
    }
  }

  dismissStarterNote(kind) {
    this.starterDismissed = { ...this.starterDismissed, [kind]: true };
    try { window.localStorage.setItem(STARTER_NOTE_KEY, JSON.stringify(this.starterDismissed)); } catch (e) { /* private mode */ }
    this._updateStarterNotes();
  }

  /** Show the "starter picks" explainer while starter items exist and it wasn't dismissed. */
  _updateStarterNotes() {
    if (this.dom.radioStarterNote) {
      this.dom.radioStarterNote.style.display = shouldShowStarterNote(this.stations, this.starterDismissed.radio) ? 'flex' : 'none';
    }
    if (this.dom.podcastStarterNote) {
      this.dom.podcastStarterNote.style.display = shouldShowStarterNote(this.podcasts, this.starterDismissed.podcast) ? 'flex' : 'none';
    }
  }

  /** Remember where the outgoing podcast episode / audiobook chapter was (before the player moves on). */
  _flushSourceProgress() {
    if (this.source?.type === 'podcast') this._rememberEpisodePosition(true);
    if (this.source?.type === 'audiobook') this._saveBookProgress(true);
  }

  /** Back to the local deck (called by every track/playlist playback path). */
  _leaveSource() {
    if (!this.source) return;
    this._flushSourceProgress();
    this._stopRadioNowPolling();
    clearTimeout(this.radioReconnectTimer);
    this.radioReconnectTimer = null;
    this.radioNowTitle = '';
    this.source = null;
  }

  _onMediaEnded() {
    if (this.source?.type === 'radio') {
      // A live stream never "ends" — the relay dropped. Re-tune once.
      console.warn('[Radio] Stream ended unexpectedly, re-tuning…');
      this.isPlaying = false;
      this._updateUI();
      if (!this.radioReconnectTimer) {
        const station = this.source.station;
        this.radioReconnectTimer = setTimeout(() => {
          this.radioReconnectTimer = null;
          if (this.source?.type === 'radio' && this.source.station?.id === station.id && !this.isPlaying) {
            this.playStation(station, { origin: this.source.origin, silent: true });
          }
        }, 2000);
      }
      return;
    }
    if (this.source?.type === 'podcast') {
      const ep = this.source.episode;
      this.podcastPositions = rememberPosition(this.podcastPositions, ep.id, ep.duration || Infinity, ep.duration || Infinity);
      savePositions(window.localStorage, this.podcastPositions);
      if (this.expandedPodcast?.id === this.source.podcast.id) this.renderPodcasts();
    }
    if (this.source?.type === 'stream') {
      this._onStreamEnded();
      return;
    }
    if (this.source?.type === 'audiobook') {
      // A book keeps reading: next chapter first, the queue/library only after the last one.
      const { book, chapter } = this.source;
      const next = nextChapter(book, chapter.id);
      if (next) { this.playChapter(book, next); return; }
      this._saveBookProgress(true, { finished: true });
      this.showPlaylistToast('📖 Finished', `${book.title} — the end`, null);
    }
    this.nextTrack();
  }

  _onMediaError() {
    if (!this.source) return;           // local tracks keep their existing handling
    const err = this.dom.musicPlayer.error;
    // stopRadio() clears src and calls load(), which Chrome reports as an
    // "empty src" error — that one is ours, not a playback failure.
    if (!this.dom.musicPlayer.currentSrc) return;
    console.error('[Source] Media error:', err?.code, err?.message);
    this.isPlaying = false;
    this._updateUI();
    this._sendPlaybackState();
    const d = describeSource(this.source);
    const why = { radio: 'The station stream is unavailable or not supported (HLS / legacy SHOUTcast).',
      podcast: 'The episode file could not be fetched.', audiobook: 'The chapter file could not be read.',
      stream: `The audio stream could not be fetched from ${providerName(this.source.track?.provider)}.` };
    this.showPlaylistToast(`⚠️ Couldn't play ${d.title}`, why[this.source.type] || 'Playback failed.', null);
  }

  seekRelative(seconds) {
    if (!seconds) return;
    if (this.source?.type === 'radio') {
      this.showPlaylistToast('📻 Live radio', 'Live streams can\'t be skipped forward or back', null);
      return;
    }
    const player = this.dom.musicPlayer;
    if (!player.currentSrc) return;
    player.currentTime = clampSeek(player.currentTime, seconds, player.duration);
    if (this.source?.type === 'podcast') this._rememberEpisodePosition(true);
    if (this.source?.type === 'audiobook') this._saveBookProgress(true);
    this._onTimeUpdate();
    this._sendPlaybackState();
  }

  // --- Radio ---

  async loadStations() {
    try {
      const res = await fetch('/api/radio');
      if (res.ok) {
        this.stations = await res.json();
        this._updatePlaylistBadges();
        this._updateStarterNotes();
        if (this.currentView === 'radio') this.renderStations();
      }
    } catch (e) {
      console.error('[App] Failed to load stations:', e);
    }
  }

  async playStation(station, { origin = 'saved', silent = false } = {}) {
    if (!station?.id) return;
    if (origin === 'directory' || !this.stations.some(s => s.id === station.id)) {
      // The relay only serves saved stations — and a station you chose to
      // play is one you'll want again. Save quietly, then tune in.
      const saved = await this.saveStation(station, { silent: true });
      if (!saved) {
        this.showPlaylistToast('⚠️ Could not save station', station.name, null);
        return;
      }
      station = saved;
      origin = 'saved';
    }
    const url = withCacheBuster(`/api/radio/${station.id}/stream`);
    await this._beginSourcePlayback({ type: 'radio', station, origin }, url, { art: station.favicon || '', icon: '📻' });
    this._startRadioNowPolling(station.id);
    this._sendPlaybackState();
    if (this.currentView === 'radio') this.renderStations();
    if (!silent) {
      const meta = [station.genre, station.country].filter(Boolean).join(' • ');
      this.showPlaylistToast(`📻 ${station.name}`, `Live radio${meta ? ' • ' + meta : ''}`, null);
    }
  }

  /** Live radio has no pause: drop the stream and release the connection. */
  stopRadio() {
    if (this.source?.type !== 'radio') return;
    this._stopRadioNowPolling();
    clearTimeout(this.radioReconnectTimer);
    this.radioReconnectTimer = null;
    this.currentPlayAbort?.abort();
    this.dom.musicPlayer.pause();
    this.dom.musicPlayer.removeAttribute('src');
    this.dom.musicPlayer.load();
    this.isPlaying = false;
    this._updateUI();
    this._sendPlaybackState();
  }

  _startRadioNowPolling(stationId) {
    this._stopRadioNowPolling();
    const poll = async () => {
      if (this.source?.type !== 'radio' || this.source.station?.id !== stationId) return;
      try {
        const res = await fetch(`/api/radio/${stationId}/now`);
        if (!res.ok) return;
        const data = await res.json();
        const title = (data.title || '').trim();
        if (title !== this.radioNowTitle && this.source?.station?.id === stationId) {
          this.radioNowTitle = title;
          this._updateUI();
          this._sendPlaybackState();
          if ('mediaSession' in navigator && navigator.mediaSession.metadata) {
            const d = describeSource(this.source, { nowTitle: title });
            navigator.mediaSession.metadata = new MediaMetadata({ title: d.title, artist: d.artist });
          }
        }
      } catch (e) { /* offline blip; try again next tick */ }
    };
    setTimeout(poll, 1500);
    this.radioNowTimer = setInterval(poll, RADIO_NOW_POLL_MS);
  }

  _stopRadioNowPolling() {
    if (this.radioNowTimer) {
      clearInterval(this.radioNowTimer);
      this.radioNowTimer = null;
    }
  }

  renderStations() {
    const list = this.dom.stationsList;
    if (!list) return;
    const visible = filterStations(this.stations, this.radioFilterQuery);
    if (visible.length === 0) {
      list.innerHTML = `<div class="sources-empty">${this.radioFilterQuery
        ? 'No stations matching search'
        : 'No radio stations yet. Click Find Stations…, or ask AI DJ: “play some jazz radio”.'}</div>`;
      return;
    }
    const playingId = this.source?.type === 'radio' ? this.source.station?.id : null;
    const frag = document.createDocumentFragment();
    visible.forEach(st => {
      const card = document.createElement('div');
      const isPlaying = st.id === playingId;
      card.className = 'playlist-card source-card' + (isPlaying ? ' active-source' : '');
      card.dataset.stationId = st.id;
      const meta = [st.genre, st.country, st.description].filter(Boolean).join(' • ') || 'Internet radio';
      card.innerHTML = `
        <div class="playlist-card-header">
          <div class="playlist-cover source-cover">📻</div>
          <div class="playlist-info">
            <div class="playlist-name-row">
              <span class="playlist-name" title="${escapeHtml(st.name)}">${escapeHtml(st.name)}</span>
              ${st.source === 'default' ? this._starterBadgeHtml() : ''}
              ${isPlaying ? this._livePillHtml() : ''}
            </div>
            <div class="playlist-meta" title="${escapeHtml(meta)}">${escapeHtml(meta)}</div>
          </div>
          <div class="playlist-actions">
            <button class="playlist-btn-play${isPlaying && this.isPlaying ? ' is-stop' : ''}" title="${isPlaying && this.isPlaying ? 'Stop' : 'Tune in'}">${isPlaying && this.isPlaying ? '⏹ Stop' : '▶ Play'}</button>
            <button class="playlist-btn-subtle danger" title="Remove station">✕</button>
          </div>
        </div>`;
      this._applyCover(card.querySelector('.source-cover'), st.favicon, '📻');
      card.querySelector('.playlist-btn-play').addEventListener('click', (e) => {
        e.stopPropagation();
        if (isPlaying && this.isPlaying) this.stopRadio();
        else this.playStation(st);
      });
      card.querySelector('.playlist-btn-subtle.danger').addEventListener('click', async (e) => {
        e.stopPropagation();
        const ok = await this.showConfirm({ title: 'Remove station?', message: `"${st.name}" will be removed from your saved stations.`, confirmText: 'Remove', danger: true });
        if (ok) await this.deleteStation(st.id);
      });
      frag.appendChild(card);
    });
    list.replaceChildren(frag);
    this._wireSelectableRows(list, '.source-card', (row) => {
      const st = this.stations.find(x => x.id === row.dataset.stationId);
      if (st) this.playStation(st);
    }, 'source-selected');
  }

  _livePillHtml() {
    return '<span class="badge-now-playing badge-live" title="On air now"><i class="live-dot"></i>LIVE</span>';
  }

  _starterBadgeHtml() {
    return '<span class="badge-starter" title="Pre-loaded starter pick — remove it with ✕ or add your own">Starter</span>';
  }

  /** Remote logos/artwork are optional and often broken: probe before showing. */
  _applyCover(el, url, fallbackIcon) {
    if (!el) return;
    el.textContent = fallbackIcon;
    el.style.backgroundImage = '';
    if (!url) return;
    const img = new Image();
    img.onload = () => { el.style.backgroundImage = `url('${url}')`; el.textContent = ''; };
    img.src = url;
  }

  /** Keep LIVE pills / active cards in the Radio and Podcasts tabs in sync without re-rendering. */
  _refreshSourceHighlights() {
    if (this.currentView === 'radio' && this.dom.stationsList) {
      const playingId = this.source?.type === 'radio' ? this.source.station?.id : null;
      let stale = false;
      this.dom.stationsList.querySelectorAll('.source-card').forEach(card => {
        const active = card.dataset.stationId === playingId;
        if (card.classList.contains('active-source') !== active) stale = true;
        const btn = card.querySelector('.playlist-btn-play');
        if (btn) {
          const label = active && this.isPlaying ? '⏹ Stop' : '▶ Play';
          if (btn.textContent !== label) stale = true;
        }
      });
      if (stale) this.renderStations();
    }
    if (this.currentView === 'podcasts' && this.dom.podcastsList) {
      const epId = this.source?.type === 'podcast' ? this.source.episode?.id : null;
      this.dom.podcastsList.querySelectorAll('.episode-item').forEach(el => {
        el.classList.toggle('track-active', el.dataset.episodeId === epId);
      });
    }
    if (this.currentView === 'audiobooks' && this.dom.audiobooksList) {
      const bookId = this.source?.type === 'audiobook' ? this.source.book?.id : null;
      const chId = this.source?.type === 'audiobook' ? this.source.chapter?.id : null;
      let stale = false;
      this.dom.audiobooksList.querySelectorAll('.audiobook-card').forEach(card => {
        const active = card.dataset.bookId === bookId;
        if (card.classList.contains('active-source') !== active) stale = true;
        const btn = card.querySelector('.playlist-btn-play');
        const listed = this.audiobooks.find(b => b.id === card.dataset.bookId);
        const started = !!(listed?.progress?.chapterId && !listed?.progress?.finished);
        if (btn && btn.textContent !== this._bookPlayLabel(active, started)) stale = true;   // ▶/⏸ follows playback
      });
      if (stale) this.renderAudiobooks();
      else this.dom.audiobooksList.querySelectorAll('.chapter-item').forEach(el => {
        el.classList.toggle('track-active', el.dataset.chapterId === chId);
      });
    }
    if (this.currentView === 'streaming' && this.dom.streamingSections) {
      const tid = this.source?.type === 'stream' ? this.source.track?.id : null;
      let stale = false;
      this.dom.streamingSections.querySelectorAll('.stream-track-item').forEach(el => {
        const active = el.dataset.trackId === tid;
        if (el.classList.contains('track-active') !== active) stale = true;
        const btn = el.querySelector('.stream-play-btn');
        if (btn && btn.classList.contains('is-pause') !== (active && this.isPlaying)) stale = true;
      });
      if (stale) this.renderStreaming();
    }
  }

  async deleteStation(stationId) {
    try {
      const res = await fetch(`/api/radio/${stationId}`, { method: 'DELETE' });
      if (res.ok) {
        if (this.source?.type === 'radio' && this.source.station?.id === stationId) {
          this.source.origin = 'directory';   // still playing, just not saved any more
          this._sendPlaybackState();
        }
        await this.loadStations();
        this.renderStations();
      }
    } catch (e) {
      console.error('[App] Delete station error:', e);
    }
  }

  async saveStation(station, { silent = false } = {}) {
    try {
      const res = await fetch('/api/radio', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(station),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        if (!silent) this.showAlert('Could not add the station', err.error || 'Please check the stream URL and try again.');
        return null;
      }
      const saved = await res.json();
      if (this.source?.type === 'radio' && this.source.station?.id === saved.id) {
        this.source.origin = 'saved';
        this._sendPlaybackState();
      }
      await this.loadStations();
      if (!silent) this.showPlaylistToast(res.status === 201 ? '📻 Station added' : '📻 Station updated', saved.name, null);
      return saved;
    } catch (e) {
      console.error('[App] Save station error:', e);
      return null;
    }
  }

  openAddStationModal() {
    if (!this.dom.addStationModal) return;
    if (this.dom.stationSearchResults) this.dom.stationSearchResults.innerHTML = '<div class="directory-hint">Search by name or genre, tap a genre, or browse the top stations worldwide (public radio-browser.info directory).</div>';
    if (this.dom.stationSearchInput) this.dom.stationSearchInput.value = '';
    this._renderStationChips();
    this.dom.addStationModal.style.display = 'flex';
    this.dom.stationSearchInput?.focus();
  }

  _renderStationChips() {
    const wrap = this.dom.stationBrowseChips;
    if (!wrap) return;
    const frag = document.createDocumentFragment();
    const mk = (label, onClick, cls = '') => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'chip' + (cls ? ' ' + cls : '');
      b.textContent = label;
      b.addEventListener('click', onClick);
      return b;
    };
    frag.appendChild(mk('🔥 Top stations', () => this.browseStationDirectory('popular'), 'chip-accent'));
    this.stationGenres.forEach(g => frag.appendChild(mk(g.label, () => this.browseStationDirectory('tag', g.tag))));
    wrap.replaceChildren(frag);
  }

  async browseStationDirectory(kind, value = '') {
    const out = this.dom.stationSearchResults;
    if (!out) return;
    out.innerHTML = '<div class="directory-hint">Loading…</div>';
    try {
      const res = await fetch(`/api/radio/browse?kind=${encodeURIComponent(kind)}&value=${encodeURIComponent(value)}`);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        out.innerHTML = `<div class="directory-hint">${escapeHtml(data.error || 'Directory unavailable')}</div>`;
        return;
      }
      if (Array.isArray(data.genres) && data.genres.length && this.stationGenres === STATION_GENRES_FALLBACK) {
        this.stationGenres = data.genres;
        this._renderStationChips();
      }
      this._renderStationResults(data.results || [], kind === 'popular'
        ? 'No stations returned — try a genre or a search.'
        : `No ${escapeHtml(value)} stations found — try a search.`);
    } catch (e) {
      console.error('[App] Station browse error:', e);
      out.innerHTML = '<div class="directory-hint">Browse failed — are you online?</div>';
    }
  }

  closeAddStationModal() {
    if (this.dom.addStationModal) this.dom.addStationModal.style.display = 'none';
  }

  async searchStationDirectory() {
    const q = this.dom.stationSearchInput?.value.trim() || '';
    const country = this.dom.stationCountryInput?.value.trim() || '';
    const place = this.dom.stationPlaceInput?.value.trim() || '';
    const out = this.dom.stationSearchResults;
    if ((!q && !place) || !out) return;
    out.innerHTML = '<div class="directory-hint">Searching…</div>';
    try {
      const res = await fetch(`/api/radio/search?q=${encodeURIComponent(q)}&country=${encodeURIComponent(country)}&place=${encodeURIComponent(place)}`);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        out.innerHTML = `<div class="directory-hint">${escapeHtml(data.error || 'Directory unavailable')}</div>`;
        return;
      }
      this._renderStationResults(data.results || [], 'No stations found. Try a shorter name, the frequency (“101.3”), the call sign, or add the country — and if the station isn\'t listed, add it below by stream URL.');
    } catch (e) {
      console.error('[App] Station search error:', e);
      out.innerHTML = '<div class="directory-hint">Search failed — are you online?</div>';
    }
  }

  /** Directory rows shared by search and browse: ▶ tune in (unsaved) / + Save. */
  _renderStationResults(results, emptyText) {
    const out = this.dom.stationSearchResults;
    if (!out) return;
    if (results.length === 0) {
      out.innerHTML = `<div class="directory-hint">${emptyText}</div>`;
      return;
    }
    {
      const savedIds = new Set(this.stations.map(s => s.id));
      const frag = document.createDocumentFragment();
      results.forEach(st => {
        const row = document.createElement('div');
        row.className = 'modal-pl-item directory-row';
        const meta = [st.genre, st.country, st.description, st.nowPlaying ? `♪ ${st.nowPlaying}` : ''].filter(Boolean).join(' • ');
        row.innerHTML = `
          <div class="directory-row-main">
            <span class="modal-pl-item-title" title="${escapeHtml(st.name)}">${escapeHtml(st.name)}${st.online === false ? ' <span class="badge-unverified" title="The directory could not reach this stream recently (often geo-blocking); it may still play here">unverified</span>' : ''}${st.provider === 'tunein' ? ' <span class="badge-provider" title="Found in the TuneIn directory">TuneIn</span>' : ''}</span>
            <span class="directory-row-meta" title="${escapeHtml(meta)}">${escapeHtml(meta)}</span>
          </div>
          <div class="directory-row-actions">
            <button class="playlist-btn-play directory-play-btn" title="Tune in (saves the station)">▶</button>
            <button class="playlist-action-btn directory-save-btn" ${savedIds.has(st.id) ? 'disabled' : ''}>${savedIds.has(st.id) ? 'Saved' : '+ Save'}</button>
          </div>`;
        row.querySelector('.directory-play-btn').addEventListener('click', async (e) => {
          e.stopPropagation();
          await this.playStation(st, { origin: 'directory' });
          const saveBtn = row.querySelector('.directory-save-btn');
          if (saveBtn && this.stations.some(x => x.id === st.id)) { saveBtn.textContent = 'Saved'; saveBtn.disabled = true; }
        });
        row.querySelector('.directory-save-btn').addEventListener('click', async (e) => {
          e.stopPropagation();
          const saved = await this.saveStation(st);
          if (saved) { e.target.textContent = 'Saved'; e.target.disabled = true; }
        });
        frag.appendChild(row);
      });
      out.replaceChildren(frag);
    }
  }

  async submitManualStation() {
    const name = this.dom.stationNameInput?.value.trim();
    const streamUrl = this.dom.stationUrlInput?.value.trim();
    const genre = this.dom.stationGenreInput?.value.trim();
    if (!streamUrl || !/^https?:\/\//i.test(streamUrl)) {
      this.showAlert('Stream URL needed', 'Please enter the station\'s http(s) stream URL.');
      return;
    }
    const saved = await this.saveStation({ name: name || streamUrl, streamUrl, genre: genre || '' });
    if (saved) {
      if (this.dom.stationNameInput) this.dom.stationNameInput.value = '';
      if (this.dom.stationUrlInput) this.dom.stationUrlInput.value = '';
      if (this.dom.stationGenreInput) this.dom.stationGenreInput.value = '';
      this.closeAddStationModal();
      this.switchView('radio');
    }
  }

  /** `radioStationsChanged` command: AI DJ saved/renamed/removed a station by voice. */
  async handleRadioStationsChanged(msg) {
    const { change, stationId, station, message } = msg;
    if (this.source?.type === 'radio' && stationId && this.source.station?.id === stationId) {
      if (change === 'deleted') this.source.origin = 'directory';
      else {
        this.source.origin = 'saved';
        if (station?.name) this.source.station = { ...this.source.station, ...station };
      }
      this._updateUI();
      this._sendPlaybackState();
    }
    await this.loadStations();
    if (this.currentView === 'radio') this.renderStations();
    if (message) {
      const titles = { created: '📻 Station saved', updated: '📻 Station updated', deleted: '🗑 Station removed' };
      this.showPlaylistToast(titles[change] || '📻 Stations updated', message, null);
    }
  }

  // --- Podcasts ---

  async loadPodcasts() {
    try {
      const res = await fetch('/api/podcasts');
      if (res.ok) {
        this.podcasts = await res.json();
        this._updatePlaylistBadges();
        this._updateStarterNotes();
        if (this.currentView === 'podcasts') this.renderPodcasts();
        this.loadLatestEpisodes();
        // Starter picks load their episodes in the background on the server;
        // re-poll a couple of times so the tab fills in without a reload.
        if (this.podcasts.some(p => p.needsRefresh) && !this.podcastRepollTimer) {
          let attempts = 0;
          this.podcastRepollTimer = setInterval(async () => {
            attempts++;
            const r = await fetch('/api/podcasts').catch(() => null);
            if (r?.ok) {
              this.podcasts = await r.json();
              this._updatePlaylistBadges();
              if (this.currentView === 'podcasts') this.renderPodcasts();
            }
            if (attempts >= 4 || !this.podcasts.some(p => p.needsRefresh)) {
              clearInterval(this.podcastRepollTimer);
              this.podcastRepollTimer = null;
            }
          }, 5000);
        }
      }
    } catch (e) {
      console.error('[App] Failed to load podcasts:', e);
    }
  }

  async playEpisode(podcast, episode) {
    if (!podcast?.id || !episode?.id) return;
    const startAt = resumePositionFor(this.podcastPositions, episode.id, episode.duration);
    const url = `/api/podcasts/${podcast.id}/episodes/${episode.id}/stream`;
    const source = { type: 'podcast', podcast, episode };

    if (startAt > 0) {
      const player = this.dom.musicPlayer;
      const onMeta = () => {
        if (this.source === source) player.currentTime = startAt;
      };
      player.addEventListener('loadedmetadata', onMeta, { once: true });
    }
    await this._beginSourcePlayback(source, url, { art: podcast.artworkUrl || '', icon: '🎙' });
    this._sendPlaybackState();
    if (this.currentView === 'podcasts') this._refreshSourceHighlights();
    this.showPlaylistToast(`🎙 ${podcast.title}`, `${episode.title}${startAt ? ` • resuming at ${formatClock(startAt)}` : ''}`, null);
  }

  /** Persist how far into the current episode we are (throttled unless forced). */
  _rememberEpisodePosition(force = false) {
    if (this.source?.type !== 'podcast') return;
    const now = Date.now();
    if (!force && now - this.lastPositionSaveMs < 5000) return;
    this.lastPositionSaveMs = now;
    const player = this.dom.musicPlayer;
    const ep = this.source.episode;
    const duration = isFinite(player.duration) && player.duration > 0 ? player.duration : (ep.duration || Infinity);
    this.podcastPositions = rememberPosition(this.podcastPositions, ep.id, player.currentTime, duration);
    savePositions(window.localStorage, this.podcastPositions);
  }

  renderPodcasts() {
    const list = this.dom.podcastsList;
    if (!list) return;
    const visible = filterPodcasts(this.podcasts, this.podcastFilterQuery);
    if (visible.length === 0) {
      list.innerHTML = `<div class="sources-empty">${this.podcastFilterQuery
        ? 'No podcasts matching search'
        : 'No podcast subscriptions yet. Click Find Podcasts…, or ask AI DJ: “subscribe to …”.'}</div>`;
      return;
    }
    const playingPodId = this.source?.type === 'podcast' ? this.source.podcast?.id : null;
    const frag = document.createDocumentFragment();
    visible.forEach(pod => {
      const isOpen = this.expandedPodcast?.id === pod.id;
      const card = document.createElement('div');
      card.className = 'playlist-card source-card podcast-card' + (pod.id === playingPodId ? ' active-source' : '') + (isOpen ? ' expanded' : '');
      card.dataset.podcastId = pod.id;
      const latest = pod.latestEpisode ? `Latest: ${formatEpisodeDate(pod.latestEpisode.publishedAt) || pod.latestEpisode.title}` : '';
      const count = pod.needsRefresh ? 'Episodes load on first open' : `${pod.episodeCount} episode${pod.episodeCount === 1 ? '' : 's'}`;
      const meta = [pod.author, count, latest].filter(Boolean).join(' • ');
      card.innerHTML = `
        <div class="playlist-card-header">
          <div class="playlist-cover source-cover">🎙</div>
          <div class="playlist-info">
            <div class="playlist-name-row">
              <span class="playlist-name" title="${escapeHtml(pod.title)}">${escapeHtml(pod.title)}</span>
              ${pod.source === 'default' ? this._starterBadgeHtml() : ''}
              ${pod.id === playingPodId ? this._nowPlayingPillHtml() : ''}
            </div>
            <div class="playlist-meta" title="${escapeHtml(meta)}">${escapeHtml(meta)}</div>
          </div>
          <div class="playlist-actions">
            <button class="playlist-btn-play" title="Play the latest episode">▶ Latest</button>
            <button class="playlist-btn-cue podcast-cue-btn" title="Cue the latest episode">+ Cue</button>
            <button class="playlist-btn-subtle podcast-refresh-btn" title="Refresh feed">↻</button>
            <button class="playlist-btn-subtle danger" title="Unsubscribe">✕</button>
            <button class="view-toggle-btn" type="button"></button>
          </div>
        </div>
        <div class="playlist-expanded${isOpen && this._openedPodcastWithoutAnimation === pod.id ? ' open' : ''}">
          <div class="playlist-expanded-inner">
            <div class="playlist-expanded-actions">
              <span class="playlist-expanded-sub"></span>
            </div>
            <div class="track-list podcast-episodes"></div>
          </div>
        </div>`;
      if (isOpen && this._openedPodcastWithoutAnimation !== pod.id) {
        const body = card.querySelector('.playlist-expanded');
        requestAnimationFrame(() => this._expandBody(body));
        this._openedPodcastWithoutAnimation = pod.id;
      }
      this._applyCover(card.querySelector('.source-cover'), pod.artworkUrl, '🎙');
      this._setViewToggle(card.querySelector('.view-toggle-btn'), isOpen, 'episodes');

      card.querySelector('.playlist-btn-play').addEventListener('click', async (e) => {
        e.stopPropagation();
        await this.playLatestEpisode(pod.id);
      });
      card.querySelector('.podcast-cue-btn').addEventListener('click', async (e) => {
        e.stopPropagation();
        await this.cueLatestEpisode(pod.id);
      });
      card.querySelector('.view-toggle-btn').addEventListener('click', (e) => {
        e.stopPropagation();
        this.togglePodcastExpanded(pod.id);
      });
      card.querySelector('.podcast-refresh-btn').addEventListener('click', async (e) => {
        e.stopPropagation();
        await this.refreshPodcast(pod.id);
      });
      card.querySelector('.playlist-btn-subtle.danger').addEventListener('click', async (e) => {
        e.stopPropagation();
        const ok = await this.showConfirm({ title: 'Unsubscribe?', message: `"${pod.title}" will be removed from your podcasts. Resume positions are kept if you re-subscribe.`, confirmText: 'Unsubscribe', danger: true });
        if (ok) await this.unsubscribePodcast(pod.id);
      });
      card.querySelector('.playlist-expanded').addEventListener('click', (e) => e.stopPropagation());
      card.addEventListener('click', () => this.togglePodcastExpanded(pod.id));

      if (isOpen) this._renderEpisodes(card, this.expandedPodcast);
      frag.appendChild(card);
    });
    list.replaceChildren(frag);
  }

  _renderEpisodes(card, pod) {
    const container = card.querySelector('.podcast-episodes');
    const sub = card.querySelector('.playlist-expanded-sub');
    if (!container) return;
    const episodes = pod.episodes || [];
    if (sub) this._setClampedText(sub, pod.refreshError ? `⚠️ ${pod.refreshError}` : (pod.description || `${episodes.length} episodes`), 3);
    if (episodes.length === 0) {
      container.innerHTML = '<div class="sources-empty">This feed has no playable episodes.</div>';
      return;
    }
    const playingEpId = this.source?.type === 'podcast' ? this.source.episode?.id : null;
    const frag = document.createDocumentFragment();
    episodes.forEach(ep => {
      const el = document.createElement('div');
      const resumeAt = resumePositionFor(this.podcastPositions, ep.id, ep.duration);
      const progress = episodeProgress(this.podcastPositions, ep);
      const meta = [formatEpisodeDate(ep.publishedAt), formatDuration(ep.duration), resumeAt ? `Resume at ${formatClock(resumeAt)}` : ''].filter(Boolean).join(' • ');
      el.className = 'track-item episode-item' + (ep.id === playingEpId ? ' track-active' : '');
      el.dataset.episodeId = ep.id;
      const notes = (ep.description || '').replace(/\s+/g, ' ').trim();
      el.innerHTML = `
        <div class="track-info episode-info">
          <div class="track-title" title="${escapeHtml(ep.title)}">${escapeHtml(ep.title)}</div>
          <div class="track-artist episode-meta">${escapeHtml(meta)}</div>
          ${notes ? '<div class="episode-excerpt clampable"></div>' : ''}
          ${progress ? `<div class="episode-progress"><i style="width:${Math.round(progress * 100)}%"></i></div>` : ''}
        </div>
        <button class="track-play-btn episode-play-btn" title="${resumeAt ? 'Resume' : 'Play'} this episode">▶ ${resumeAt ? 'Resume' : 'Play'}</button>
        <button class="track-cue-btn episode-cue-btn" title="Cue this episode">+ Cue</button>
      `;
      const excerptEl = el.querySelector('.episode-excerpt');
      if (excerptEl) this._setClampedText(excerptEl, notes, 2);
      el.querySelector('.episode-play-btn').addEventListener('click', (e) => {
        e.stopPropagation();
        this.playEpisode(pod, ep);
      });
      el.querySelector('.episode-cue-btn').addEventListener('click', (e) => {
        e.stopPropagation();
        this.queueEpisode(pod, ep);
      });
      el.title = ep.title;
      frag.appendChild(el);
    });
    container.replaceChildren(frag);
    this._wireSelectableRows(container, '.episode-item', (row) => {
      const ep = episodes.find(x => x.id === row.dataset.episodeId);
      if (ep) this.playEpisode(pod, ep);
    });
  }

  async togglePodcastExpanded(podcastId, force) {
    const open = typeof force === 'boolean' ? force : this.expandedPodcast?.id !== podcastId;
    if (!open) {
      if (this.expandedPodcast?.id !== podcastId) return;
      await this._animateCollapse(this.dom.podcastsList?.querySelector(`.podcast-card[data-podcast-id="${podcastId}"]`));
      this.expandedPodcast = null;
      this._openedPodcastWithoutAnimation = null;
      this.renderPodcasts();
      return;
    }
    if (this.expandedPodcast && this.expandedPodcast.id !== podcastId) {
      await this._animateCollapse(this.dom.podcastsList?.querySelector(`.podcast-card[data-podcast-id="${this.expandedPodcast.id}"]`));
    }
    this._openedPodcastWithoutAnimation = null;
    try {
      const pod = await this._fetchPodcastLoaded(podcastId);
      if (!pod) return;
      this.expandedPodcast = pod;
      this.renderPodcasts();
      this.dom.podcastsList?.querySelector(`.podcast-card[data-podcast-id="${podcastId}"]`)
        ?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    } catch (e) {
      console.error('[App] Failed to expand podcast:', e);
    }
  }

  /**
   * Full podcast (with episodes). A starter pick that has never been fetched
   * is refreshed first, so "open" or "play latest" just works.
   */
  async _fetchPodcastLoaded(podcastId) {
    const res = await fetch(`/api/podcasts/${podcastId}`);
    if (!res.ok) return null;
    let pod = await res.json();
    if (!pod.lastFetchedAt) {
      const r = await fetch(`/api/podcasts/${podcastId}/refresh`, { method: 'POST' });
      if (r.ok) {
        pod = await r.json();
        this.loadPodcasts();          // listing now has counts / latest episode
      }
    }
    return pod;
  }

  async playLatestEpisode(podcastId) {
    try {
      const pod = await this._fetchPodcastLoaded(podcastId);
      if (!pod) return;
      const ep = (pod.episodes || [])[0];
      if (!ep) {
        this.showPlaylistToast('🎙 No episodes', pod.refreshError ? `${pod.title}: ${pod.refreshError}` : `${pod.title} has no playable episodes yet`, null);
        return;
      }
      await this.playEpisode(pod, ep);
    } catch (e) {
      console.error('[App] Play latest episode error:', e);
    }
  }

  async refreshPodcast(podcastId) {
    try {
      const res = await fetch(`/api/podcasts/${podcastId}/refresh`, { method: 'POST' });
      if (!res.ok) return;
      const pod = await res.json();
      if (this.expandedPodcast?.id === podcastId) this.expandedPodcast = pod;
      await this.loadPodcasts();
      this.renderPodcasts();
      this.showPlaylistToast(pod.refreshError ? '⚠️ Refresh failed' : '↻ Feed refreshed',
        pod.refreshError || `${pod.title}: ${pod.episodeCount} episodes`, null);
    } catch (e) {
      console.error('[App] Refresh podcast error:', e);
    }
  }

  async unsubscribePodcast(podcastId) {
    try {
      const res = await fetch(`/api/podcasts/${podcastId}`, { method: 'DELETE' });
      if (res.ok) {
        if (this.expandedPodcast?.id === podcastId) this.expandedPodcast = null;
        await this.loadPodcasts();
        this.renderPodcasts();
      }
    } catch (e) {
      console.error('[App] Unsubscribe error:', e);
    }
  }

  async subscribeToFeed(feedUrl) {
    try {
      const res = await fetch('/api/podcasts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ feedUrl }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        this.showAlert('Could not subscribe', data.error || 'That feed could not be loaded.');
        return null;
      }
      await this.loadPodcasts();
      this.showPlaylistToast(res.status === 201 ? '🎙 Subscribed' : '🎙 Subscription refreshed', `${data.title} • ${data.episodeCount} episodes`, null);
      return data;
    } catch (e) {
      console.error('[App] Subscribe error:', e);
      return null;
    }
  }

  openAddPodcastModal() {
    if (!this.dom.addPodcastModal) return;
    if (this.dom.podcastSearchResults) this.dom.podcastSearchResults.innerHTML = '<div class="directory-hint">Search by name, tap a category, or paste an RSS feed URL below (public Apple podcast directory).</div>';
    if (this.dom.podcastSearchInput) this.dom.podcastSearchInput.value = '';
    if (this.dom.podcastFeedInput) this.dom.podcastFeedInput.value = '';
    if (this.dom.podcastBrowseChips && !this.dom.podcastBrowseChips.childElementCount) {
      const frag = document.createDocumentFragment();
      PODCAST_CATEGORIES.forEach(cat => {
        const b = document.createElement('button');
        b.type = 'button';
        b.className = 'chip';
        b.textContent = cat;
        b.addEventListener('click', () => {
          if (this.dom.podcastSearchInput) this.dom.podcastSearchInput.value = cat;
          this.searchPodcastDirectory();
        });
        frag.appendChild(b);
      });
      this.dom.podcastBrowseChips.replaceChildren(frag);
    }
    this.dom.addPodcastModal.style.display = 'flex';
    this.dom.podcastSearchInput?.focus();
  }

  closeAddPodcastModal() {
    if (this.dom.addPodcastModal) this.dom.addPodcastModal.style.display = 'none';
  }

  async searchPodcastDirectory() {
    const q = this.dom.podcastSearchInput?.value.trim();
    const out = this.dom.podcastSearchResults;
    if (!q || !out) return;
    out.innerHTML = '<div class="directory-hint">Searching…</div>';
    try {
      const res = await fetch(`/api/podcasts/search?q=${encodeURIComponent(q)}`);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        out.innerHTML = `<div class="directory-hint">${escapeHtml(data.error || 'Directory unavailable')}</div>`;
        return;
      }
      const results = data.results || [];
      if (results.length === 0) {
        out.innerHTML = '<div class="directory-hint">No podcasts found.</div>';
        return;
      }
      const subscribed = new Set(this.podcasts.map(p => p.id));
      const frag = document.createDocumentFragment();
      results.forEach(r => {
        const row = document.createElement('div');
        row.className = 'modal-pl-item directory-row';
        const meta = [r.author, r.genre, r.episodeCount ? `${r.episodeCount} episodes` : ''].filter(Boolean).join(' • ');
        row.innerHTML = `
          <div class="directory-row-main">
            <span class="modal-pl-item-title" title="${escapeHtml(r.title)}">${escapeHtml(r.title)}</span>
            <span class="directory-row-meta" title="${escapeHtml(meta)}">${escapeHtml(meta)}</span>
          </div>
          <div class="directory-row-actions">
            <button class="playlist-action-btn directory-save-btn" ${subscribed.has(r.id) ? 'disabled' : ''}>${subscribed.has(r.id) ? 'Subscribed' : '+ Subscribe'}</button>
          </div>`;
        row.querySelector('.directory-save-btn').addEventListener('click', async (e) => {
          e.stopPropagation();
          e.target.disabled = true;
          e.target.textContent = 'Loading…';
          const pod = await this.subscribeToFeed(r.feedUrl);
          if (pod) {
            e.target.textContent = 'Subscribed';
            this.closeAddPodcastModal();
            this.switchView('podcasts');
            this.togglePodcastExpanded(pod.id, true);
          } else {
            e.target.disabled = false;
            e.target.textContent = '+ Subscribe';
          }
        });
        frag.appendChild(row);
      });
      out.replaceChildren(frag);
    } catch (e) {
      console.error('[App] Podcast search error:', e);
      out.innerHTML = '<div class="directory-hint">Search failed — are you online?</div>';
    }
  }

  async submitFeedSubscription() {
    const feedUrl = this.dom.podcastFeedInput?.value.trim();
    if (!feedUrl || !/^https?:\/\//i.test(feedUrl)) {
      this.showAlert('Feed URL needed', 'Please paste the podcast\'s http(s) RSS feed URL.');
      return;
    }
    const btn = this.dom.podcastSubscribeBtn;
    if (btn) { btn.disabled = true; btn.textContent = 'Loading…'; }
    const pod = await this.subscribeToFeed(feedUrl);
    if (btn) { btn.disabled = false; btn.textContent = 'Subscribe'; }
    if (pod) {
      this.closeAddPodcastModal();
      this.switchView('podcasts');
      this.togglePodcastExpanded(pod.id, true);
    }
  }

  /** `podcastsChanged` command: AI DJ subscribed/unsubscribed by voice. */
  async handlePodcastsChanged(msg) {
    const { change, podcastId, message } = msg;
    if (podcastId && this.expandedPodcast?.id === podcastId) {
      if (change === 'deleted') this.expandedPodcast = null;
      else {
        try {
          const res = await fetch(`/api/podcasts/${podcastId}`);
          if (res.ok) this.expandedPodcast = await res.json();
        } catch (e) { /* keep the stale copy */ }
      }
    }
    await this.loadPodcasts();
    if (this.currentView === 'podcasts') this.renderPodcasts();
    if (message) {
      const titles = { created: '🎙 Subscribed', updated: '🎙 Podcast updated', deleted: '🗑 Unsubscribed' };
      this.showPlaylistToast(titles[change] || '🎙 Podcasts updated', message, null);
    }
  }

  // --- Audiobooks (local files from AUDIOBOOKS_DIR) ---

  async loadAudiobooks() {
    try {
      const res = await fetch('/api/audiobooks');
      if (!res.ok) return;
      const data = await res.json();
      this.audiobooks = data.books || [];
      this.audiobooksInfo = { configured: !!data.configured, directoryExists: !!data.directoryExists, scanning: !!data.scanning };
      this._updatePlaylistBadges();
      if (this.currentView === 'audiobooks') this.renderAudiobooks();
      if (data.scanning) setTimeout(() => this.loadAudiobooks(), 1500);   // server still scanning the folder
    } catch (e) {
      console.error('[App] Failed to load audiobooks:', e);
    }
  }

  async rescanAudiobooks() {
    if (!this.audiobooksInfo.configured) { this.renderAudiobooks(); return; }
    try {
      if (this.dom.audiobooksRescanBtn) this.dom.audiobooksRescanBtn.disabled = true;
      const res = await fetch('/api/audiobooks/rescan', { method: 'POST' });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) { this.showAlert('Rescan failed', data.error || 'Could not rescan the audiobook folder.'); return; }
      await this.loadAudiobooks();
      if (this.expandedAudiobook) {
        const r = await fetch(`/api/audiobooks/${this.expandedAudiobook.id}`);
        this.expandedAudiobook = r.ok ? await r.json() : null;
      }
      this.renderAudiobooks();
      this.showPlaylistToast('📖 Audiobooks rescanned', `${data.count} book${data.count === 1 ? '' : 's'} found`, null);
    } catch (e) {
      console.error('[App] Rescan audiobooks error:', e);
    } finally {
      if (this.dom.audiobooksRescanBtn) this.dom.audiobooksRescanBtn.disabled = false;
    }
  }

  renderAudiobooks() {
    const list = this.dom.audiobooksList;
    if (!list) return;
    const info = this.audiobooksInfo;
    if (this.dom.audiobooksSetupNote) {
      const show = !info.configured || !info.directoryExists;
      this.dom.audiobooksSetupNote.style.display = show ? 'flex' : 'none';
      if (show && this.dom.audiobooksSetupText && info.configured && !info.directoryExists) {
        this.dom.audiobooksSetupText.innerHTML = '<b>The audiobook folder was not found.</b> Check <code>AUDIOBOOKS_DIR</code> in <code>backend/.env</code>, then click ↻ Rescan.';
      }
    }
    if (this.dom.audiobooksRescanBtn) this.dom.audiobooksRescanBtn.style.display = info.configured ? '' : 'none';
    const visible = sortAudiobooks(filterAudiobooks(this.audiobooks, this.audiobookFilterQuery));
    if (visible.length === 0) {
      list.innerHTML = `<div class="sources-empty">${this.audiobookFilterQuery
        ? 'No audiobooks matching search'
        : (info.configured ? (info.scanning ? 'Scanning the audiobook folder…' : 'No audiobooks found. Drop books (folders of chapter files or .m4b files) into your AUDIOBOOKS_DIR and click ↻ Rescan.') : '')}</div>`;
      return;
    }
    const playingBookId = this.source?.type === 'audiobook' ? this.source.book?.id : null;
    const frag = document.createDocumentFragment();
    visible.forEach(book => {
      const isOpen = this.expandedAudiobook?.id === book.id;
      const card = document.createElement('div');
      card.className = 'playlist-card source-card audiobook-card' + (book.id === playingBookId ? ' active-source' : '') + (isOpen ? ' expanded' : '');
      card.dataset.bookId = book.id;
      const who = [book.author && book.author !== 'Unknown Author' ? book.author : '', book.narrator ? `read by ${book.narrator}` : ''].filter(Boolean).join(' • ');
      const length = [formatDuration(book.duration), `${book.chapterCount} chapter${book.chapterCount === 1 ? '' : 's'}`].filter(Boolean).join(' • ');
      const status = bookProgressLabel(book);
      const pct = Math.round((book.progress?.fraction || 0) * 100);
      const started = !!(book.progress?.chapterId && !book.progress?.finished);
      const isActive = book.id === playingBookId;
      const playLabel = this._bookPlayLabel(isActive, started);
      card.innerHTML = `
        <div class="playlist-card-header">
          <div class="playlist-cover source-cover">📖</div>
          <div class="playlist-info">
            <div class="playlist-name-row">
              <span class="playlist-name" title="${escapeHtml(book.title)}">${escapeHtml(book.title)}</span>
              ${book.progress?.finished ? '<span class="badge-starter badge-finished" title="You finished this book">Finished</span>' : ''}
              ${book.id === playingBookId ? this._nowPlayingPillHtml() : ''}
            </div>
            <div class="playlist-meta" title="${escapeHtml(who)}">${escapeHtml(who || 'Audiobook')}</div>
            <div class="playlist-meta book-status" title="${escapeHtml(status)}">${escapeHtml(length)}${status ? ` • ${escapeHtml(status)}` : ''}</div>
            ${started ? `<div class="episode-progress book-progress"><i style="width:${pct}%"></i></div>` : ''}
          </div>
          <div class="playlist-actions">
            <button class="playlist-btn-play${isActive && this.isPlaying ? ' is-pause' : ''}" title="${isActive ? (this.isPlaying ? 'Pause' : 'Resume') : (started ? 'Continue where you left off' : 'Play from the beginning')}">${playLabel}</button>
            ${book.progress?.chapterId || book.progress?.finished ? '<button class="playlist-btn-subtle book-reset-btn" title="Forget progress (start over)">↺</button>' : ''}
            <button class="view-toggle-btn" type="button"></button>
          </div>
        </div>
        <div class="playlist-expanded${isOpen && this._openedBookWithoutAnimation === book.id ? ' open' : ''}">
          <div class="playlist-expanded-inner">
            <div class="playlist-expanded-actions">
              <span class="playlist-expanded-sub"></span>
            </div>
            <div class="track-list book-chapters"></div>
          </div>
        </div>`;
      if (isOpen && this._openedBookWithoutAnimation !== book.id) {
        const body = card.querySelector('.playlist-expanded');
        requestAnimationFrame(() => this._expandBody(body));
        this._openedBookWithoutAnimation = book.id;
      }
      this._applyCover(card.querySelector('.source-cover'), book.hasCover !== false ? `/api/audiobooks/${book.id}/art` : '', '📖');
      this._setViewToggle(card.querySelector('.view-toggle-btn'), isOpen, 'chapters');

      card.querySelector('.playlist-btn-play').addEventListener('click', async (e) => {
        e.stopPropagation();
        if (this.source?.type === 'audiobook' && this.source.book?.id === book.id) await this.togglePlayPause();   // pause / resume in place
        else await this.continueBook(book.id);
      });
      card.querySelector('.book-reset-btn')?.addEventListener('click', async (e) => {
        e.stopPropagation();
        const ok = await this.showConfirm({ title: 'Start over?', message: `Forget your place in "${book.title}"? The book itself stays.`, confirmText: 'Start over' });
        if (ok) await this.resetBookProgress(book.id);
      });
      card.querySelector('.view-toggle-btn').addEventListener('click', (e) => {
        e.stopPropagation();
        this.toggleAudiobookExpanded(book.id);
      });
      card.querySelector('.playlist-expanded').addEventListener('click', (e) => e.stopPropagation());
      card.addEventListener('click', () => this.toggleAudiobookExpanded(book.id));

      if (isOpen) this._renderChapters(card, this.expandedAudiobook);
      frag.appendChild(card);
    });
    list.replaceChildren(frag);
  }

  _renderChapters(card, book) {
    const container = card.querySelector('.book-chapters');
    const sub = card.querySelector('.playlist-expanded-sub');
    if (!container) return;
    const chapters = book.chapters || [];
    if (sub) sub.textContent = [book.author, book.narrator ? `Narrated by ${book.narrator}` : '', formatDuration(book.duration)].filter(Boolean).join(' • ');
    if (chapters.length === 0) {
      container.innerHTML = '<div class="sources-empty">This book has no playable chapters.</div>';
      return;
    }
    const playingChId = this.source?.type === 'audiobook' ? this.source.chapter?.id : null;
    const resume = resumePointFor(book);
    const frag = document.createDocumentFragment();
    chapters.forEach(ch => {
      const el = document.createElement('div');
      const isCurrent = ch.id === playingChId;
      const isResume = resume && resume.chapter.id === ch.id && resume.position > 0;
      const heard = book.progress && !book.progress.finished && book.progress.chapterIndex ? ch.index < book.progress.chapterIndex : !!book.progress?.finished;
      const meta = [`Chapter ${ch.index}`, formatDuration(ch.duration), isResume ? `Resume at ${formatClock(resume.position)}` : (heard ? 'Listened' : '')].filter(Boolean).join(' • ');
      el.className = 'track-item episode-item chapter-item' + (ch.id === playingChId ? ' track-active' : '') + (heard ? ' chapter-heard' : '');
      el.dataset.chapterId = ch.id;
      const pct = isResume && ch.duration ? Math.min(100, Math.round(100 * resume.position / ch.duration)) : 0;
      el.innerHTML = `
        <div class="track-info episode-info">
          <div class="track-title" title="${escapeHtml(ch.title)}">${escapeHtml(ch.title)}</div>
          <div class="track-artist episode-meta">${escapeHtml(meta)}</div>
          ${pct ? `<div class="episode-progress"><i style="width:${pct}%"></i></div>` : ''}
        </div>
        <button class="track-play-btn chapter-play-btn${isCurrent && this.isPlaying ? ' is-pause' : ''}" title="${isCurrent ? (this.isPlaying ? 'Pause' : 'Resume') : (isResume ? 'Resume' : 'Play') + ' this chapter'}">${isCurrent ? (this.isPlaying ? '⏸ Pause' : '▶ Resume') : `▶ ${isResume ? 'Resume' : 'Play'}`}</button>
      `;
      el.querySelector('.chapter-play-btn').addEventListener('click', (e) => {
        e.stopPropagation();
        if (this.source?.type === 'audiobook' && this.source.chapter?.id === ch.id) this.togglePlayPause();
        else this.playChapter(book, ch, { position: isResume ? resume.position : 0 });
      });
      frag.appendChild(el);
    });
    container.replaceChildren(frag);
    this._wireSelectableRows(container, '.chapter-item', (row) => {
      const ch = chapters.find(x => x.id === row.dataset.chapterId);
      if (!ch) return;
      if (this.source?.type === 'audiobook' && this.source.chapter?.id === ch.id) this.togglePlayPause();
      else this.playChapter(book, ch, { position: resume && resume.chapter.id === ch.id ? resume.position : 0 });
    });
  }

  /** Card button text for a book: ⏸ Pause / ▶ Resume while it is the active source, else ▶ Continue / ▶ Play. */
  _bookPlayLabel(isActive, started) {
    if (isActive) return this.isPlaying ? '⏸ Pause' : '▶ Resume';
    return started ? '▶ Continue' : '▶ Play';
  }

  async toggleAudiobookExpanded(bookId, force) {
    const open = typeof force === 'boolean' ? force : this.expandedAudiobook?.id !== bookId;
    if (!open) {
      if (this.expandedAudiobook?.id !== bookId) return;
      await this._animateCollapse(this.dom.audiobooksList?.querySelector(`.audiobook-card[data-book-id="${bookId}"]`));
      this.expandedAudiobook = null;
      this._openedBookWithoutAnimation = null;
      this.renderAudiobooks();
      return;
    }
    if (this.expandedAudiobook && this.expandedAudiobook.id !== bookId) {
      await this._animateCollapse(this.dom.audiobooksList?.querySelector(`.audiobook-card[data-book-id="${this.expandedAudiobook.id}"]`));
    }
    this._openedBookWithoutAnimation = null;
    try {
      const res = await fetch(`/api/audiobooks/${bookId}`);
      if (!res.ok) return;
      this.expandedAudiobook = await res.json();
      this.renderAudiobooks();
      this.dom.audiobooksList?.querySelector(`.audiobook-card[data-book-id="${bookId}"]`)
        ?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    } catch (e) {
      console.error('[App] Failed to expand audiobook:', e);
    }
  }

  /** ▶ on a book card: resume where you left off, or start at chapter 1. */
  async continueBook(bookId) {
    try {
      const res = await fetch(`/api/audiobooks/${bookId}`);
      if (!res.ok) return;
      const book = await res.json();
      const point = resumePointFor(book);
      if (!point) { this.showPlaylistToast('📖 No chapters', `${book.title} has no playable chapters`, null); return; }
      await this.playChapter(book, point.chapter, { position: point.position });
    } catch (e) {
      console.error('[App] Continue book error:', e);
    }
  }

  /**
   * Play one chapter of a book. `book` may be a summary (from the list or a
   * server command) or the full record with chapters; the full record is
   * fetched when needed so next/previous chapter work.
   */
  async playChapter(book, chapter, { position = 0 } = {}) {
    if (!book?.id || !chapter?.id) return;
    if (!Array.isArray(book.chapters) || !book.chapters.length) {
      try {
        const res = await fetch(`/api/audiobooks/${book.id}`);
        if (res.ok) book = await res.json();
      } catch (e) { /* play with what we have */ }
    }
    const url = `/api/audiobooks/${book.id}/chapters/${chapter.id}/stream`;
    const source = { type: 'audiobook', book, chapter };
    const startAt = Math.max(0, Number(position) || 0);
    if (startAt > 0) {
      const player = this.dom.musicPlayer;
      const onMeta = () => { if (this.source === source) player.currentTime = startAt; };
      player.addEventListener('loadedmetadata', onMeta, { once: true });
    }
    this.lastBookProgressSaveMs = 0;
    await this._beginSourcePlayback(source, url, { art: `/api/audiobooks/${book.id}/art`, icon: '📖' });
    this._saveBookProgress(true, { position: startAt });
    this._sendPlaybackState();
    if (this.currentView === 'audiobooks') this.renderAudiobooks();
    const total = book.chapterCount || (book.chapters || []).length;
    this.showPlaylistToast(`📖 ${book.title}`, `Chapter ${chapter.index}${total ? ` of ${total}` : ''}${startAt ? ` • resuming at ${formatClock(startAt)}` : ''}`, null);
  }

  /** Tell the server where we are in the book (throttled unless forced). */
  _saveBookProgress(force = false, { position = null, finished = false } = {}) {
    if (this.source?.type !== 'audiobook') return;
    const now = Date.now();
    if (!force && now - this.lastBookProgressSaveMs < BOOK_PROGRESS_SAVE_MS) return;
    this.lastBookProgressSaveMs = now;
    const { book, chapter } = this.source;
    const player = this.dom.musicPlayer;
    const pos = position != null ? position : player.currentTime;
    const duration = isFinite(player.duration) && player.duration > 0 ? player.duration : (chapter.duration || null);
    const body = finished
      ? { chapterId: chapter.id, position: duration || (chapter.duration || 0) + 1, duration: duration || chapter.duration || 1 }
      : { chapterId: chapter.id, position: pos, duration };
    fetch(`/api/audiobooks/${book.id}/progress`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), keepalive: true,
    }).then(async (res) => {
      if (!res.ok) return;
      const entry = await res.json().catch(() => null);
      const listed = this.audiobooks.find(b => b.id === book.id);
      if (entry && listed) {
        listed.progress = { ...(listed.progress || {}), ...entry, fraction: listed.progress?.fraction ?? 0 };
      }
      if (this.currentView === 'audiobooks' && (finished || force)) this.loadAudiobooks();
    }).catch(() => { /* offline blip; the next tick retries */ });
  }

  async resetBookProgress(bookId) {
    try {
      const res = await fetch(`/api/audiobooks/${bookId}/progress`, { method: 'DELETE' });
      if (res.ok) {
        if (this.expandedAudiobook?.id === bookId) {
          const r = await fetch(`/api/audiobooks/${bookId}`);
          if (r.ok) this.expandedAudiobook = await r.json();
        }
        await this.loadAudiobooks();
        this.renderAudiobooks();
      }
    } catch (e) {
      console.error('[App] Reset book progress error:', e);
    }
  }

  // --- Streaming (online sources as sub-sections; all audio relayed through the deck) ---

  async loadStreamingStatus() {
    try {
      const res = await fetch('/api/streaming/status');
      if (!res.ok) return;
      this.streamingStatus = await res.json();
      this._updatePlaylistBadges();
      if (this.currentView === 'streaming') this.renderStreaming();
    } catch (e) {
      console.error('[App] Failed to load streaming status:', e);
    }
  }

  /** Provider list in display order: server status when known, else the static table. */
  _streamingProviders() {
    const fromServer = this.streamingStatus?.providers;
    if (fromServer?.length) return fromServer;
    return STREAM_PROVIDER_ORDER.map(id => ({ id, ...STREAM_PROVIDERS[id], available: null, description: '' }));
  }

  renderStreaming() {
    const out = this.dom.streamingSections;
    if (!out) return;
    const frag = document.createDocumentFragment();
    this._streamingProviders().forEach(p => frag.appendChild(this._streamingSection(p)));
    out.replaceChildren(frag);
    this._wireSelectableRows(out, '.stream-track-item', (row) => {
      const t = this._streamTrackById(row.dataset.trackId);
      if (t) this.playStreamTrack(t);
    });
  }

  _streamTrackById(id) {
    for (const r of this.streamingResults?.results || []) {
      const t = (r.tracks || []).find(x => x.id === id);
      if (t) return t;
    }
    return null;
  }

  /** One provider sub-section: header (name, availability, chevron) + its results. */
  _streamingSection(p) {
    const card = document.createElement('div');
    const collapsed = !!this.streamingCollapsed[p.id];
    const result = (this.streamingResults?.results || []).find(r => r.provider === p.id);
    const playingProvider = this.source?.type === 'stream' ? this.source.track?.provider : null;
    card.className = 'playlist-card source-card streaming-section' + (p.id === playingProvider ? ' active-source' : '') + (collapsed ? '' : ' expanded');
    card.dataset.provider = p.id;
    // Section header carries the only "preview" label; whole-song providers get none.
    const status = p.available === false ? `<span class="badge-starter badge-unavailable" title="${escapeHtml(p.reason || 'Unavailable')}">Not installed</span>`
      : p.available && !p.full ? '<span class="badge-starter badge-available" title="This source offers 30-second previews">Previews</span>' : '';
    let summary;
    if (this.streamingBusy) summary = 'Searching…';
    else if (result?.error) summary = `⚠️ ${result.error}`;
    else if (result) summary = `${result.tracks.length} song${result.tracks.length === 1 ? '' : 's'}${result.albums.length ? ` • ${result.albums.length} album${result.albums.length === 1 ? '' : 's'}` : ''} for “${this.streamingQuery}”`;
    else summary = p.description || '';
    card.innerHTML = `
      <div class="playlist-card-header">
        <div class="playlist-cover source-cover">${p.icon || '🌐'}</div>
        <div class="playlist-info">
          <div class="playlist-name-row">
            <span class="playlist-name">${escapeHtml(p.name)}</span>
            ${status}
            ${p.id === playingProvider ? this._nowPlayingPillHtml() : ''}
          </div>
          <div class="playlist-meta" title="${escapeHtml(summary)}">${escapeHtml(summary)}</div>
        </div>
        <div class="playlist-actions">
          <button class="view-toggle-btn" type="button"></button>
        </div>
      </div>
      <div class="playlist-expanded${collapsed ? '' : ' open'}">
        <div class="playlist-expanded-inner streaming-section-body"></div>
      </div>`;
    this._setViewToggle(card.querySelector('.view-toggle-btn'), !collapsed, 'results');
    card.querySelector('.view-toggle-btn').addEventListener('click', (e) => { e.stopPropagation(); this.toggleStreamingSection(p.id); });
    card.querySelector('.playlist-card-header').addEventListener('click', () => this.toggleStreamingSection(p.id));
    card.querySelector('.playlist-expanded').addEventListener('click', (e) => e.stopPropagation());

    const body = card.querySelector('.streaming-section-body');
    if (p.available === false) {
      body.appendChild(this._streamingHint(`${p.name} needs its third-party tools on the server: ${p.reason || 'not installed'}. Then restart the server.`));
    } else if (!result) {
      body.appendChild(this._streamingHint(this.streamingBusy ? 'Searching…' : `Search above to see ${p.name} results here.`));
    } else if (result.error) {
      body.appendChild(this._streamingHint(result.error));
    } else if (!result.tracks.length && !result.albums.length) {
      body.appendChild(this._streamingHint(`Nothing on ${p.name} for “${this.streamingQuery}”.`));
    } else {
      if (result.tracks.length) {
        const list = document.createElement('div');
        list.className = 'track-list streaming-tracks';
        result.tracks.forEach(t => list.appendChild(this._streamTrackRow(t)));
        body.appendChild(list);
      }
      if (result.albums.length) {
        const albums = document.createElement('div');
        albums.className = 'streaming-albums';
        result.albums.forEach(a => albums.appendChild(this._streamAlbumCard(a)));
        body.appendChild(albums);
      }
      if (result.hasMore) {
        const more = document.createElement('button');
        more.type = 'button';
        more.className = 'playlist-action-btn streaming-more-btn';
        more.textContent = result.loadingMore ? 'Loading…' : 'More results';
        more.disabled = !!result.loadingMore;
        more.title = `Fetch the next ${result.limit || 10} ${p.name} songs for this search`;
        more.addEventListener('click', (e) => { e.stopPropagation(); this.loadMoreStreaming(p.id); });
        body.appendChild(more);
      }
    }
    return card;
  }

  /** "More results": append the next page of one provider's songs. */
  async loadMoreStreaming(pid) {
    const result = (this.streamingResults?.results || []).find(r => r.provider === pid);
    if (!result || result.loadingMore || !this.streamingQuery) return;
    result.loadingMore = true;
    this.renderStreaming();
    try {
      const offset = result.tracks.length;
      const res = await fetch(`/api/streaming/search?q=${encodeURIComponent(this.streamingQuery)}&providers=${encodeURIComponent(pid)}&offset=${offset}&limit=${result.limit || 10}`);
      const data = await res.json().catch(() => ({}));
      const page = (data.results || []).find(r => r.provider === pid);
      if (!res.ok || !page) {
        result.hasMore = false;
        this.showPlaylistToast('⚠️ No more results', data.error || `${providerName(pid)} did not return more songs.`, null);
      } else {
        const seen = new Set(result.tracks.map(t => t.id));
        const fresh = (page.tracks || []).filter(t => !seen.has(t.id));
        result.tracks.push(...fresh);
        result.hasMore = !!page.hasMore && fresh.length > 0;
        if (page.error) { result.error = page.error; result.hasMore = false; }
      }
    } catch (e) {
      console.error('[App] Streaming load more error:', e);
      result.hasMore = false;
    } finally {
      result.loadingMore = false;
      this.renderStreaming();
    }
  }

  toggleStreamingSection(pid) {
    this.streamingCollapsed = { ...this.streamingCollapsed, [pid]: !this.streamingCollapsed[pid] };
    this.renderStreaming();
  }

  _streamingHint(text) {
    const el = document.createElement('div');
    el.className = 'sources-empty streaming-hint';
    el.textContent = text;
    return el;
  }

  _streamTrackRow(t) {
    const el = document.createElement('div');
    const playing = this.source?.type === 'stream' && this.source.track?.id === t.id;
    const playable = streamPlayable(t);
    el.className = 'track-item stream-track-item' + (playing ? ' track-active' : '') + (playable ? '' : ' stream-unplayable');
    el.dataset.trackId = t.id;
    if (playable) {
      el.draggable = true;
      el.addEventListener('dragstart', (e) => this._startStreamDrag(e, t));
      el.addEventListener('dragend', () => { this._dragging = null; this._clearDropMarks(); });
    }
    // Rows carry no preview/full labels (the section header says it); only an unplayable song is flagged.
    const badge = playable ? null : { text: 'No clip', title: 'This source offers no preview for this song' };
    el.innerHTML = `
      <div class="stream-track-art">${t.albumArt ? `<img src="${escapeHtml(t.albumArt)}" alt="" loading="lazy" />` : '🌐'}</div>
      <div class="track-info">
        <div class="track-title" title="${escapeHtml(t.title)}">${escapeHtml(t.title)}${t.explicit ? ' <span class="badge-explicit" title="Explicit">E</span>' : ''}</div>
        <div class="track-artist">${escapeHtml(t.artist)}${t.album ? ` • ${escapeHtml(t.album)}` : ''}</div>
      </div>
      ${badge ? `<span class="badge-mode none" title="${escapeHtml(badge.title)}">${escapeHtml(badge.text)}</span>` : ''}
      <div class="track-genre">${formatMs(t.durationMs)}</div>
      <button class="track-play-btn stream-play-btn${playing && this.isPlaying ? ' is-pause' : ''}" title="${playing ? (this.isPlaying ? 'Pause' : 'Resume') : 'Play'}" ${playable ? '' : 'disabled'}>${playing && this.isPlaying ? '⏸' : '▶'}</button>
      <button class="track-cue-btn stream-cue-btn" title="Cue this track" ${playable ? '' : 'disabled'}>+ Cue</button>
    `;
    el.querySelector('.stream-play-btn').addEventListener('click', (e) => {
      e.stopPropagation();
      if (playing) this.togglePlayPause(); else this.playStreamTrack(t);
    });
    el.querySelector('.stream-cue-btn').addEventListener('click', (e) => { e.stopPropagation(); this.queueStreamTrack(t); });
    return el;
  }

  /** Album card: ▶ plays its songs (first now, the rest cued); + Cue queues them all. */
  _streamAlbumCard(item) {
    const card = document.createElement('div');
    card.className = 'playlist-card source-card streaming-album';
    const meta = [item.artist, item.releaseDate ? item.releaseDate.slice(0, 4) : '', item.trackCount ? `${item.trackCount} tracks` : ''].filter(Boolean).join(' • ');
    card.innerHTML = `
      <div class="playlist-card-header">
        <div class="playlist-cover source-cover">💿</div>
        <div class="playlist-info">
          <div class="playlist-name-row"><span class="playlist-name" title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</span></div>
          <div class="playlist-meta" title="${escapeHtml(meta)}">${escapeHtml(meta)}</div>
        </div>
        <div class="playlist-actions">
          <button class="playlist-btn-play" title="Play the album">▶ Play</button>
          <button class="playlist-btn-cue stream-album-cue-btn" title="Cue all its tracks">+ Cue</button>
        </div>
      </div>`;
    this._applyCover(card.querySelector('.source-cover'), item.image || '', '💿');
    card.querySelector('.playlist-btn-play').addEventListener('click', (e) => { e.stopPropagation(); this.playStreamAlbum(item); });
    card.querySelector('.stream-album-cue-btn').addEventListener('click', (e) => { e.stopPropagation(); this.playStreamAlbum(item, { queueOnly: true }); });
    return card;
  }

  async searchStreaming() {
    const q = this.dom.streamingSearchInput?.value.trim() || '';
    if (!q) return;
    this.streamingQuery = q;
    this.streamingBusy = true;
    this.renderStreaming();
    try {
      const res = await fetch(`/api/streaming/search?q=${encodeURIComponent(q)}&limit=10`);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        this.streamingResults = { query: q, results: [] };
        this.showPlaylistToast('⚠️ Search failed', data.error || 'Please try again.', null);
      } else {
        this.streamingResults = data;
      }
    } catch (e) {
      console.error('[App] Streaming search error:', e);
      this.streamingResults = { query: q, results: [] };
    } finally {
      this.streamingBusy = false;
      this.renderStreaming();
    }
  }

  async playStreamAlbum(item, { queueOnly = false } = {}) {
    try {
      const res = await fetch(`/api/streaming/albums/${item.id}/tracks`);
      const tracks = (res.ok ? ((await res.json()).tracks || []) : []).filter(streamPlayable);
      if (!tracks.length) { this.showPlaylistToast('🌐 Nothing to play', `${item.name}: ${providerName(item.provider)} has no playable tracks for it`, null); return; }
      const context = { type: 'album', name: item.name, id: item.id, provider: item.provider };
      if (queueOnly) {
        tracks.forEach(t => this.queueStreamTrack(t, context, { silent: true }));
        this.showPlaylistToast('➕ Cued from streaming', `${tracks.length} tracks • ${item.name}`, null);
      } else {
        await this.playStreamTracks(tracks, context);
      }
    } catch (e) {
      console.error('[App] Streaming album error:', e);
    }
  }

  /** First track now, the rest cued (server command and GUI share this). */
  async playStreamTracks(tracks, context = null) {
    const playable = (tracks || []).filter(streamPlayable);
    if (!playable.length) return;
    const [first, ...rest] = playable;
    this.queue = this.queue.filter(q => q.kind !== 'stream' || !playable.some(t => t.id === q.id));
    rest.forEach(t => this.queue.push(streamQueueItem(t, context)));
    this._updateQueueUI();
    await this.playStreamTrack(first, { context });
  }

  queueStreamTrack(track, context = null, { silent = false } = {}) {
    if (!track?.id || !streamPlayable(track)) return;
    if (this.queue.some(q => q.id === track.id)) {
      if (!silent) this.showPlaylistToast('🌐 Already cued', track.title, null);
      return;
    }
    this.queue.push(streamQueueItem(track, context));
    this._updateQueueUI();
    this._sendPlaybackState();
    if (!silent) this.showPlaylistToast('➕ Cued from streaming', `${track.title} • ${track.artist} • ${providerName(track.provider)}`, null);
  }

  /** Play one streamed track through the deck (the backend relays the audio). */
  async playStreamTrack(track, { context = null } = {}) {
    if (!track?.id) return;
    if (!streamPlayable(track)) {
      this.showPlaylistToast('⚠️ Can’t play this one', `${track.title}: ${providerName(track.provider)} offers no audio for it`, null);
      return;
    }
    const qIdx = this.queue.findIndex(q => q.id === track.id);
    if (qIdx !== -1) { this.queue.splice(qIdx, 1); this._updateQueueUI(); }
    const source = { type: 'stream', track, context };
    if (track.full) this.showPlaylistToast(`🌐 ${track.title}`, `Fetching from ${providerName(track.provider)}…`, null);
    await this._beginSourcePlayback(source, `/api/streaming/tracks/${track.id}/audio`, { art: track.albumArt || '', icon: '🌐' });
    this._sendPlaybackState();
    if (this.currentView === 'streaming') this.renderStreaming();
    this.showPlaylistToast(`🌐 ${track.title}`, `${track.artist} • ${providerName(track.provider)}${track.full ? '' : ' • preview'}`, null);
  }

  /**
   * A streamed track finished: continue with the queue if there is one,
   * otherwise stop — never wander into the local library on its own.
   */
  _onStreamEnded() {
    if (this.queue.length > 0) { this.playNextInQueue(); return; }
    this.isPlaying = false;
    this.dom.musicPlayer.pause();
    this._updateUI();
    this._sendPlaybackState();
    const t = this.source?.track;
    this.showPlaylistToast(t?.full ? '🌐 Track ended' : '🌐 Preview ended', `${t?.title || 'Track'} — cue more songs or pick another`, null);
  }

  // --- Tab carousel ---

  /** Nudge the tab row by roughly one viewport width. */
  _scrollTabs(direction) {
    const bar = this.dom.deckTabs;
    if (!bar) return;
    bar.scrollBy({ left: direction * Math.max(120, bar.clientWidth * 0.6), behavior: 'smooth' });
  }

  /** Keep the active tab (and as much of its neighbours as fits) in view. */
  _revealActiveTab() {
    const bar = this.dom.deckTabs;
    const active = bar?.querySelector('.deck-tab.active');
    if (!bar || !active || bar.scrollWidth <= bar.clientWidth + 1) return;
    const target = active.offsetLeft - (bar.clientWidth - active.offsetWidth) / 2;
    bar.scrollTo({ left: Math.max(0, Math.min(target, bar.scrollWidth - bar.clientWidth)), behavior: 'smooth' });
  }

  /** Show the ‹ › buttons only when there is something to scroll to. */
  _updateTabArrows() {
    const bar = this.dom.deckTabs;
    if (!bar) return;
    const overflow = bar.scrollWidth > bar.clientWidth + 1;
    const atStart = bar.scrollLeft <= 1;
    const atEnd = bar.scrollLeft + bar.clientWidth >= bar.scrollWidth - 1;
    if (this.dom.tabsPrevBtn) { this.dom.tabsPrevBtn.style.display = overflow ? '' : 'none'; this.dom.tabsPrevBtn.disabled = atStart; }
    if (this.dom.tabsNextBtn) { this.dom.tabsNextBtn.style.display = overflow ? '' : 'none'; this.dom.tabsNextBtn.disabled = atEnd; }
    bar.classList.toggle('fade-start', overflow && !atStart);
    bar.classList.toggle('fade-end', overflow && !atEnd);
  }

  // --- Newest podcast episodes (workflow: list / play / cue what's new) ---

  async loadLatestEpisodes() {
    try {
      const res = await fetch('/api/podcasts/latest?limit=20');
      if (!res.ok) return;
      this.latestEpisodes = (await res.json()).items || [];
      this.renderLatestEpisodes();
    } catch (e) {
      console.error('[App] Failed to load newest episodes:', e);
    }
  }

  renderLatestEpisodes() {
    const items = this.latestEpisodes || [];
    if (this.dom.latestEpisodesCount) this.dom.latestEpisodesCount.textContent = `${items.length}`;
    if (this.dom.latestEpisodesMeta) {
      this.dom.latestEpisodesMeta.textContent = items.length
        ? `Newest: ${items[0].episode.title} • ${items[0].podcast.title}${items[0].episode.publishedAt ? ` • ${formatEpisodeDate(items[0].episode.publishedAt)}` : ''}`
        : 'The most recent episode from each show appears here once the feeds have loaded.';
    }
    if (this.dom.latestPlayBtn) this.dom.latestPlayBtn.disabled = !items.length;
    if (this.dom.latestCueBtn) this.dom.latestCueBtn.disabled = !items.length;
    if (this.dom.latestEpisodes) this.dom.latestEpisodes.style.display = this.podcasts.length ? '' : 'none';
    const list = this.dom.latestEpisodesList;
    if (!list || !this.latestOpen) return;
    const playingEpId = this.source?.type === 'podcast' ? this.source.episode?.id : null;
    const frag = document.createDocumentFragment();
    items.forEach(({ podcast, episode }) => {
      const el = document.createElement('div');
      const resumeAt = resumePositionFor(this.podcastPositions, episode.id, episode.duration);
      const meta = [podcast.title, formatEpisodeDate(episode.publishedAt), formatDuration(episode.duration), resumeAt ? `Resume at ${formatClock(resumeAt)}` : ''].filter(Boolean).join(' • ');
      el.className = 'track-item episode-item latest-item' + (episode.id === playingEpId ? ' track-active' : '');
      el.dataset.episodeId = episode.id;
      el.innerHTML = `
        <div class="track-info episode-info">
          <div class="track-title" title="${escapeHtml(episode.title)}">${escapeHtml(episode.title)}</div>
          <div class="track-artist episode-meta">${escapeHtml(meta)}</div>
        </div>
        <button class="track-play-btn episode-play-btn" title="${resumeAt ? 'Resume' : 'Play'} this episode">▶ ${resumeAt ? 'Resume' : 'Play'}</button>
        <button class="track-cue-btn episode-cue-btn" title="Cue this episode">+ Cue</button>
      `;
      el.querySelector('.episode-play-btn').addEventListener('click', (e) => { e.stopPropagation(); this.playEpisode(podcast, episode); });
      el.querySelector('.episode-cue-btn').addEventListener('click', (e) => { e.stopPropagation(); this.queueEpisode(podcast, episode); });
      frag.appendChild(el);
    });
    list.replaceChildren(frag);
    this._wireSelectableRows(list, '.latest-item', (row) => {
      const it = items.find(x => x.episode.id === row.dataset.episodeId);
      if (it) this.playEpisode(it.podcast, it.episode);
    });
  }

  toggleLatestEpisodes(force) {
    this.latestOpen = typeof force === 'boolean' ? force : !this.latestOpen;
    this.dom.latestEpisodes?.classList.toggle('expanded', this.latestOpen);
    this._setViewToggle(this.dom.latestViewBtn, this.latestOpen, 'the newest episodes');
    if (this.latestOpen) {
      this.renderLatestEpisodes();
      this._expandBody(this.dom.latestEpisodesBody);
    } else {
      this._collapseBody(this.dom.latestEpisodesBody);
    }
  }

  /** ▶ Play: newest episode now, the rest cued; + Cue: all cued in order. */
  async playLatestEpisodes({ queueOnly = false } = {}) {
    const items = this.latestEpisodes || [];
    if (!items.length) { this.showPlaylistToast('🎙 Nothing new yet', 'Open or refresh a podcast to load its episodes', null); return; }
    const [first, ...rest] = items;
    if (queueOnly) {
      items.forEach(it => this.queueEpisode(it.podcast, it.episode, { silent: true }));
      this.showPlaylistToast('➕ Cued newest episodes', `${items.length} episodes from your podcasts`, null);
      return;
    }
    this.queue = this.queue.filter(q => q.kind !== 'podcast' || !items.some(it => it.episode.id === q.id));
    rest.forEach(it => this.queueEpisode(it.podcast, it.episode, { silent: true }));
    await this.playEpisode(first.podcast, first.episode);
  }

  // --- Drag & drop: rows -> cue / playlists, reorder within the cue and playlists ---

  _startTracksDrag(e, tracks, label) {
    const ids = tracks.map(t => t.id);
    e.dataTransfer.setData(DND.TRACKS, JSON.stringify({ trackIds: ids, label }));
    e.dataTransfer.effectAllowed = 'copy';
    this._dragging = { type: DND.TRACKS, trackIds: ids, label };
    this._setDragImage(e, ids.length > 1 ? `🎵 ${label}` : `🎵 ${tracks[0].title}`);
    this._dragBodyState(true);
  }

  _startStreamDrag(e, track) {
    e.dataTransfer.setData(DND.STREAM, JSON.stringify({ track }));
    e.dataTransfer.effectAllowed = 'copy';
    this._dragging = { type: DND.STREAM, track };
    this._setDragImage(e, `🌐 ${track.title}`);
    this._dragBodyState(true);
  }

  /** Body class while something draggable is in flight (highlights drop zones); cleared on dragend. */
  _dragBodyState(on) {
    document.body.classList.toggle('dragging-tracks', on);
    if (!on) return;
    const end = () => { document.body.classList.remove('dragging-tracks'); this._dragging = null; this._clearDropMarks(); document.removeEventListener('dragend', end, true); };
    document.addEventListener('dragend', end, true);
  }

  /** A small pill follows the pointer instead of the whole row. */
  _setDragImage(e, text) {
    if (!e.dataTransfer?.setDragImage) return;
    const ghost = document.createElement('div');
    ghost.className = 'drag-ghost';
    ghost.textContent = text;
    document.body.appendChild(ghost);
    e.dataTransfer.setDragImage(ghost, 12, 16);
    setTimeout(() => ghost.remove(), 0);
  }

  _dragTypes(e) {
    return new Set(Array.from(e.dataTransfer?.types || []));
  }

  /** The Up Next card accepts library tracks, streaming tracks and its own rows (reorder). */
  _makeQueueDropTarget() {
    const box = this.dom.queueContainer;
    if (!box) return;
    box.addEventListener('dragover', (e) => {
      const types = this._dragTypes(e);
      if (!(types.has(DND.TRACKS) || types.has(DND.STREAM) || types.has(DND.QUEUE))) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = types.has(DND.QUEUE) ? 'move' : 'copy';
      box.classList.add('drag-over');
      const row = e.target.closest?.('.queue-item');
      this._clearDropMarks(row);
      if (row) this._markDropBefore(row, e);
    });
    box.addEventListener('dragleave', (e) => {
      if (!box.contains(e.relatedTarget)) { box.classList.remove('drag-over'); this._clearDropMarks(); }
    });
    box.addEventListener('drop', (e) => {
      const types = this._dragTypes(e);
      if (!(types.has(DND.TRACKS) || types.has(DND.STREAM) || types.has(DND.QUEUE))) return;
      e.preventDefault();
      box.classList.remove('drag-over');
      const row = e.target.closest?.('.queue-item');
      const at = row ? parseInt(row.dataset.queueIndex, 10) + (this._dropIsBefore(row, e) ? 0 : 1) : this.queue.length;
      this._clearDropMarks();
      if (types.has(DND.QUEUE)) {
        this.moveQueueItem(parseInt(e.dataTransfer.getData(DND.QUEUE), 10), at);
        return;
      }
      if (types.has(DND.STREAM)) {
        const { track } = JSON.parse(e.dataTransfer.getData(DND.STREAM) || '{}');
        if (track && !this.queue.some(q => q.id === track.id)) {
          this.queue.splice(Math.min(at, this.queue.length), 0, streamQueueItem(track));
          this._afterQueueChange();
          this.showPlaylistToast('➕ Cued', `${track.title} • ${providerName(track.provider)}`, null);
        }
        return;
      }
      const { trackIds = [], label } = JSON.parse(e.dataTransfer.getData(DND.TRACKS) || '{}');
      const tracks = trackIds.map(id => this.tracks.find(t => t.id === id)).filter(t => t && !this.queue.some(q => q.id === t.id));
      if (!tracks.length) return;
      this.queue.splice(Math.min(at, this.queue.length), 0, ...tracks);
      this._afterQueueChange();
      this.showPlaylistToast('➕ Cued', tracks.length === 1 ? tracks[0].title : `${tracks.length} tracks • ${label || ''}`, null);
    });
  }

  /** Playlist cards accept library tracks (added to the playlist); streamed tracks are refused. */
  _makePlaylistDropTarget(card, pl) {
    card.addEventListener('dragover', (e) => {
      const types = this._dragTypes(e);
      if (!(types.has(DND.TRACKS) || types.has(DND.STREAM))) return;
      if (types.has(DND.PLTRACK) && this._dragging?.playlistId === pl.id) return;   // reordering inside this playlist: rows handle it
      e.preventDefault();
      e.dataTransfer.dropEffect = types.has(DND.STREAM) ? 'none' : 'copy';
      card.classList.toggle('drag-over', !types.has(DND.STREAM));
      card.classList.toggle('drag-refuse', types.has(DND.STREAM));
    });
    card.addEventListener('dragleave', (e) => {
      if (!card.contains(e.relatedTarget)) card.classList.remove('drag-over', 'drag-refuse');
    });
    card.addEventListener('drop', async (e) => {
      const types = this._dragTypes(e);
      card.classList.remove('drag-over', 'drag-refuse');
      if (types.has(DND.PLTRACK) && this._dragging?.playlistId === pl.id) return;
      if (types.has(DND.STREAM)) {
        e.preventDefault();
        this.showPlaylistToast('📑 Local songs only', 'Playlists hold tracks from your Music library; streamed songs can be cued instead.', null);
        return;
      }
      if (!types.has(DND.TRACKS)) return;
      e.preventDefault();
      e.stopPropagation();
      const { trackIds = [], label } = JSON.parse(e.dataTransfer.getData(DND.TRACKS) || '{}');
      const fresh = trackIds.filter(id => this.tracks.some(t => t.id === id));
      if (!fresh.length) return;
      await this.addTracksToPlaylist(pl.id, fresh, label);
    });
  }

  async addTracksToPlaylist(playlistId, trackIds, label = '') {
    try {
      const res = await fetch(`/api/playlists/${playlistId}/tracks`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ trackIds }),
      });
      if (!res.ok) { this.showAlert('Could not add to playlist', 'The server refused the change.'); return; }
      const updated = await res.json();
      await this.loadPlaylists();
      if (this.expandedPlaylist?.id === playlistId) await this.refreshExpandedPlaylist();
      this.renderPlaylists();
      const what = trackIds.length === 1 ? (this.tracks.find(t => t.id === trackIds[0])?.title || 'Track') : `${trackIds.length} tracks${label ? ` (${label})` : ''}`;
      this.showPlaylistToast('📑 Added to playlist', `${what} → ${updated.name}`, playlistId);
    } catch (e) {
      console.error('[App] Add tracks to playlist error:', e);
    }
  }

  /** Persist a new order after a row was dropped before/after another row. */
  async reorderPlaylistTrack(pl, movedId, targetId, before) {
    const ids = (this.expandedPlaylist?.id === pl.id ? (this.expandedPlaylist.tracks || []).map(t => t.id) : (pl.trackIds || [])).slice();
    const from = ids.indexOf(movedId);
    if (from === -1 || movedId === targetId) return;
    ids.splice(from, 1);
    let to = ids.indexOf(targetId);
    if (to === -1) return;
    if (!before) to += 1;
    ids.splice(to, 0, movedId);
    try {
      const res = await fetch(`/api/playlists/${pl.id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ trackIds: ids }) });
      if (!res.ok) return;
      if (this.expandedPlaylist?.id === pl.id) await this.refreshExpandedPlaylist();
      await this.loadPlaylists();
      this.renderPlaylists();
    } catch (e) {
      console.error('[App] Reorder playlist error:', e);
    }
  }

  /** Move a cued item so it ends up at position `to` of the list as it was before the move. */
  moveQueueItem(from, to) {
    if (!Number.isInteger(from) || from < 0 || from >= this.queue.length) return;
    const [item] = this.queue.splice(from, 1);
    let target = Math.max(0, Math.min(to, this.queue.length + 1));
    if (from < to) target -= 1;
    this.queue.splice(Math.max(0, Math.min(target, this.queue.length)), 0, item);
    this._afterQueueChange();
  }

  _afterQueueChange() {
    this._updateQueueUI();
    this._sendPlaybackState();
  }

  _dropIsBefore(row, e) {
    const r = row.getBoundingClientRect();
    return e.clientY < r.top + r.height / 2;
  }

  _markDropBefore(row, e) {
    const before = this._dropIsBefore(row, e);
    row.classList.toggle('drop-before', before);
    row.classList.toggle('drop-after', !before);
  }

  _clearDropMarks(except = null) {
    document.querySelectorAll('.drop-before, .drop-after').forEach(el => {
      if (el !== except) el.classList.remove('drop-before', 'drop-after');
    });
  }

  // --- Tab carousel: drag to scroll ---

  _makeTabsDraggable() {
    const bar = this.dom.deckTabs;
    if (!bar) return;
    let startX = 0, startLeft = 0, moved = false, active = false;
    bar.addEventListener('pointerdown', (e) => {
      if (e.button !== 0 || bar.scrollWidth <= bar.clientWidth) return;
      active = true; moved = false; startX = e.clientX; startLeft = bar.scrollLeft;
      bar.classList.add('grabbing');
      bar.style.scrollBehavior = 'auto';           // follow the pointer 1:1 while dragging
    });
    bar.addEventListener('pointermove', (e) => {
      if (!active) return;
      const dx = e.clientX - startX;
      if (!moved && Math.abs(dx) > 5) { moved = true; try { bar.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ } }
      if (moved) bar.scrollLeft = startLeft - dx;
    });
    const finish = (e) => {
      if (!active) return;
      active = false;
      bar.classList.remove('grabbing');
      bar.style.scrollBehavior = '';
      if (moved) {
        // Swallow the click that ends a drag so a tab under the pointer doesn't activate.
        const swallow = (ev) => { ev.stopPropagation(); ev.preventDefault(); };
        bar.addEventListener('click', swallow, { capture: true, once: true });
        setTimeout(() => bar.removeEventListener('click', swallow, { capture: true }), 0);
        this._snapTabs();
      }
      try { if (e.pointerId !== undefined && bar.hasPointerCapture?.(e.pointerId)) bar.releasePointerCapture(e.pointerId); } catch (err) { /* ignore */ }
    };
    bar.addEventListener('pointerup', finish);
    bar.addEventListener('pointercancel', finish);
  }

  /** After a drag, settle on a tab boundary. */
  _snapTabs() {
    const bar = this.dom.deckTabs;
    if (!bar) return;
    const tabs = [...bar.querySelectorAll('.deck-tab')];
    if (!tabs.length) return;
    const nearest = tabs.reduce((best, t) => Math.abs(t.offsetLeft - bar.scrollLeft) < Math.abs(best.offsetLeft - bar.scrollLeft) ? t : best, tabs[0]);
    const target = Math.max(0, Math.min(nearest.offsetLeft - 6, bar.scrollWidth - bar.clientWidth));
    bar.scrollTo({ left: target, behavior: 'smooth' });
  }

  // --- Modal: Add to Playlist ---

  openAddToPlaylistModal(track) {
    this.targetTrackForPlaylist = track;
    if (!this.dom.addToPlaylistModal) return;

    if (this.dom.modalTargetTitle) this.dom.modalTargetTitle.textContent = track.title;
    if (this.dom.modalTargetArtist) this.dom.modalTargetArtist.textContent = track.artist;
    if (this.dom.modalNewPlName) this.dom.modalNewPlName.value = '';

    // Render list of playlists in modal
    if (this.dom.modalPlaylistsList) {
      if (this.playlists.length === 0) {
        this.dom.modalPlaylistsList.innerHTML = '<div style="padding:10px; font-size:12px; opacity:0.5; text-align:center;">No existing playlists. Create one below!</div>';
      } else {
        const frag = document.createDocumentFragment();
        this.playlists.forEach(pl => {
          const item = document.createElement('div');
          item.className = 'modal-pl-item';
          item.innerHTML = `
            <span class="modal-pl-item-title" title="${escapeHtml(pl.name)}">${escapeHtml(pl.name)}</span>
            <span class="modal-pl-item-count">${pl.trackCount} tracks</span>
          `;
          item.addEventListener('click', async () => {
            await this.addTrackToPlaylist(pl.id, track.id);
            this.closeAddToPlaylistModal();
          });
          frag.appendChild(item);
        });
        this.dom.modalPlaylistsList.replaceChildren(frag);
      }
    }

    this.dom.addToPlaylistModal.style.display = 'flex';
  }

  closeAddToPlaylistModal() {
    if (this.dom.addToPlaylistModal) this.dom.addToPlaylistModal.style.display = 'none';
    this.targetTrackForPlaylist = null;
  }

  async addTrackToPlaylist(playlistId, trackId) {
    try {
      const res = await fetch(`/api/playlists/${playlistId}/tracks`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ trackIds: [trackId] })
      });
      if (res.ok) {
        await this.loadPlaylists();
        const pl = this.playlists.find(p => p.id === playlistId);
        this.showPlaylistToast('Added to Playlist', `Added track to "${pl?.name || 'Playlist'}"`, playlistId);
      }
    } catch (e) {
      console.error('[App] Add track error:', e);
    }
  }

  async submitModalCreateAndAdd() {
    const name = this.dom.modalNewPlName?.value.trim();
    if (!name || !this.targetTrackForPlaylist) return;
    try {
      const res = await fetch('/api/playlists', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: name,
          trackIds: [this.targetTrackForPlaylist.id],
          description: 'Custom Playlist'
        })
      });
      if (res.ok) {
        const created = await res.json();
        await this.loadPlaylists();
        this.closeAddToPlaylistModal();
        this.showPlaylistToast('Playlist Created', `Created "${created.name}" and added track`, created.id);
      }
    } catch (e) {
      console.error('[App] Create & add playlist error:', e);
    }
  }

  openCreatePlaylistModal() {
    if (!this.dom.newPlaylistModal) return;
    if (this.dom.newPlaylistNameInput) this.dom.newPlaylistNameInput.value = '';
    if (this.dom.newPlaylistDescInput) this.dom.newPlaylistDescInput.value = '';
    this.dom.newPlaylistModal.style.display = 'flex';
    this.dom.newPlaylistNameInput?.focus();
  }

  closeCreatePlaylistModal() {
    if (this.dom.newPlaylistModal) this.dom.newPlaylistModal.style.display = 'none';
  }

  async submitCreatePlaylist() {
    const name = this.dom.newPlaylistNameInput?.value.trim();
    if (!name) {
      this.dom.newPlaylistNameInput?.focus();
      return;
    }
    const description = this.dom.newPlaylistDescInput?.value.trim() || '';
    const created = await this.createNewPlaylist(name, [], description);
    if (created) this.closeCreatePlaylistModal();
  }

  async createNewPlaylist(name, trackIds = [], description = '') {
    try {
      const res = await fetch('/api/playlists', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, trackIds, description: description || 'Custom Playlist' })
      });
      if (res.ok) {
        const created = await res.json();
        await this.loadPlaylists();
        this.showPlaylist(created.id);
        this.showPlaylistToast('📑 Playlist created', created.name, created.id);
        return created;
      }
    } catch (e) {
      console.error('[App] Create playlist error:', e);
    }
    return null;
  }

  // --- Save Queue as Playlist ---

  openSaveQueueModal() {
    if (!this.dom.saveQueueModal) return;
    const now = new Date();
    const defaultName = `Session Mix (${now.toLocaleDateString()})`;
    if (this.dom.saveQueueNameInput) this.dom.saveQueueNameInput.value = defaultName;
    this.dom.saveQueueModal.style.display = 'flex';
  }

  closeSaveQueueModal() {
    if (this.dom.saveQueueModal) this.dom.saveQueueModal.style.display = 'none';
  }

  async submitSaveQueue() {
    const name = this.dom.saveQueueNameInput?.value.trim();
    if (!name) return;

    // Collect current playing track + queue tracks
    const allTracks = [];
    if (this.currentTrackIndex >= 0 && this.tracks[this.currentTrackIndex]) {
      allTracks.push(this.tracks[this.currentTrackIndex].id);
    }
    this.queue.filter(t => t.kind !== 'podcast').forEach(t => allTracks.push(t.id));   // playlists hold songs only

    if (allTracks.length === 0) {
      this.showAlert('Nothing to save', 'The queue has no songs to save. Cue up some songs first!');
      this.closeSaveQueueModal();
      return;
    }

    try {
      const res = await fetch('/api/playlists', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: name,
          trackIds: allTracks,
          description: `Saved from active queue (${allTracks.length} tracks)`
        })
      });
      if (res.ok) {
        const created = await res.json();
        await this.loadPlaylists();
        this.closeSaveQueueModal();
        this.showPlaylistToast('Queue Saved', `Saved queue as "${created.name}"`, created.id);
      }
    } catch (e) {
      console.error('[App] Save queue error:', e);
    }
  }

  /**
   * Play the ready jingle once. Browsers keep a fresh AudioContext suspended
   * until the first user gesture, so if it can't play now it plays the
   * moment the context is allowed to run (statechange → running).
   */
  _playStartupFx() {
    if (this.startupFxPlayed) return;
    if (this.audio.playStartupFx()) {
      this.startupFxPlayed = true;
      return;
    }
    const onState = () => {
      if (this.audio.ctx.state === 'running' && !this.startupFxPlayed) {
        this.audio.ctx.removeEventListener('statechange', onState);
        this.startupFxPlayed = this.audio.playStartupFx();
      }
    };
    this.audio.ctx.addEventListener('statechange', onState);
    this.audio.ctx.resume().catch(() => { });   // resolves once a gesture unlocks audio
  }

  _setupGesturePrewarm() {
    const handler = async () => {
      document.removeEventListener('click', handler, true);
      document.removeEventListener('touchstart', handler, true);
      this.audio.initMusicPlayer(this.dom.musicPlayer);
      if (this.audio.ctx.state === 'suspended') await this.audio.ctx.resume();
      this.mic.prewarm().catch(() => { });
    };
    document.addEventListener('click', handler, true);
    document.addEventListener('touchstart', handler, true);
  }
}

// ==========================================
// BOOT
// ==========================================
window.djApp = new AIDJApp();
window.djApp.init();

/**
 * AI DJ — Frontend (V6.8)
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

// ==========================================
// CONFIGURATION
// ==========================================
const CONFIG = {
  PROTOCOL_VERSION: 3,
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
// TRACK LIST RENDERER (Keyed Diff)
// ==========================================
class TrackListRenderer {
  constructor(container) {
    this.container = container;
    this.items = new Map();
    this.currentId = null;
    this.lastRenderedQuery = null;
    this.lastTrackCount = 0;

    this.container.addEventListener('click', (e) => {
      const plBtn = e.target.closest('.track-add-playlist-btn');
      if (plBtn) {
        e.stopPropagation();
        const item = plBtn.closest('.track-item');
        const index = parseInt(item?.dataset.index, 10);
        if (!isNaN(index) && window.djApp?.tracks[index]) {
          window.djApp.openAddToPlaylistModal(window.djApp.tracks[index]);
        }
        return;
      }
      const cueBtn = e.target.closest('.track-cue-btn');
      if (cueBtn) {
        e.stopPropagation();
        const item = cueBtn.closest('.track-item');
        const index = parseInt(item?.dataset.index, 10);
        if (!isNaN(index) && window.djApp?.tracks[index]) {
          window.djApp.queueTrackById(window.djApp.tracks[index].id);
        }
        return;
      }
      const item = e.target.closest('.track-item');
      if (!item) return;
      const index = parseInt(item.dataset.index, 10);
      if (!isNaN(index)) window.djApp?.playTrackByIndex(index);
    });
  }

  render(tracks, currentId, filterQuery = '') {
    const prevId = this.currentId;
    this.currentId = currentId;
    const q = filterQuery.toLowerCase().trim();

    // Fast path: if track list length and filter query haven't changed,
    // ONLY toggle the active class without touching the DOM tree or resetting scroll position!
    if (this.lastRenderedQuery === q && this.lastTrackCount === tracks.length) {
      if (prevId !== currentId) {
        if (prevId != null && this.items.has(prevId)) {
          const prevEl = this.items.get(prevId);
          prevEl.classList.remove('track-active');
          prevEl.querySelector('.track-num')?.classList.remove('track-num-active');
        }
        if (currentId != null && this.items.has(currentId)) {
          const currEl = this.items.get(currentId);
          currEl.classList.add('track-active');
          currEl.querySelector('.track-num')?.classList.add('track-num-active');
        }
      }
      return;
    }

    this.lastRenderedQuery = q;
    this.lastTrackCount = tracks.length;

    const visibleTracks = q
      ? tracks
          .map((track, originalIndex) => ({ track, originalIndex }))
          .filter(({ track }) =>
            track.title.toLowerCase().includes(q) ||
            track.artist.toLowerCase().includes(q) ||
            track.genre.toLowerCase().includes(q) ||
            (track.album && track.album.toLowerCase().includes(q))
          )
      : tracks.map((track, originalIndex) => ({ track, originalIndex }));

    const trackIds = new Set(visibleTracks.map(item => item.track.id));

    for (const [id, el] of this.items) {
      if (!trackIds.has(id)) {
        el.remove();
        this.items.delete(id);
      }
    }

    if (visibleTracks.length === 0) {
      this.container.innerHTML = '<div style="padding:20px; opacity:0.5; font-size:12px; text-align:center;">No matching tracks found</div>';
      return;
    }

    const frag = document.createDocumentFragment();
    visibleTracks.forEach(({ track, originalIndex }) => {
      let el = this.items.get(track.id);
      if (!el) {
        el = this._createElement();
        this.items.set(track.id, el);
      }
      this._updateElement(el, track, originalIndex);
      frag.appendChild(el);
    });

    this.container.replaceChildren(frag);
  }

  _createElement() {
    const el = document.createElement('div');
    el.className = 'track-item';
    el.innerHTML = `
      <div class="track-num"></div>
      <div class="track-info">
        <div class="track-title"></div>
        <div class="track-artist"></div>
      </div>
      <div class="track-genre"></div>
      <button class="track-add-playlist-btn" title="Add to playlist">+ 📑</button>
      <button class="track-cue-btn" title="Cue up to play next">+ Cue</button>
    `;
    return el;
  }

  _updateElement(el, track, index) {
    const isActive = track.id === this.currentId;
    el.dataset.index = index;
    el.classList.toggle('track-active', isActive);

    const num = el.querySelector('.track-num');
    num.textContent = String(index + 1).padStart(2, '0');
    num.classList.toggle('track-num-active', isActive);

    el.querySelector('.track-title').textContent = track.title;
    el.querySelector('.track-artist').textContent = track.artist;
    el.querySelector('.track-genre').textContent = track.genre;
  }
}

// ==========================================
// VISUALIZER
// ==========================================
class Visualizer {
  constructor(canvas, analyser) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.analyser = analyser;
    this.running = true;
    this._resize();
    window.addEventListener('resize', () => this._resize());
  }

  _resize() {
    this.canvas.width = window.innerWidth;
    this.canvas.height = window.innerHeight;
  }

  start() {
    this.running = true;
    this._draw();
  }

  stop() {
    this.running = false;
  }

  _draw = () => {
    if (!this.running) return;
    requestAnimationFrame(this._draw);

    if (!this.analyser || document.hidden) return;

    const bufferLength = this.analyser.frequencyBinCount;
    const dataArray = new Uint8Array(bufferLength);
    this.analyser.getByteFrequencyData(dataArray);

    const { width, height } = this.canvas;
    this.ctx.clearRect(0, 0, width, height);

    // Focus on audible frequencies (first 96 bins) and span the entire canvas evenly
    const activeBins = Math.min(bufferLength, 96);
    const barWidth = width / activeBins;

    for (let i = 0; i < activeBins; i++) {
      const val = dataArray[i];
      const barHeight = (val / 255) * (height * 0.45);
      const opacity = (val / 255) * 0.6 + 0.08;
      this.ctx.fillStyle = `rgba(155, 114, 203, ${opacity})`;
      this.ctx.fillRect(i * barWidth, height - barHeight, Math.max(1, barWidth - 1), barHeight);
    }
  };
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
    this.queueExpanded = false;
    this.playlistFilterQuery = '';
    this.targetTrackForPlaylist = null;
    this.toastPlaylistId = null;
    this.toastTimer = null;
    this.currentTrackIndex = -1;
    this.isPlaying = false;
    this.userVolume = 1.0;
    this.serverReady = false;

    this.dom = {
      musicPlayer: $('music-player'),
      liveBtn: $('live-btn'),
      liveBtnIcon: $('live-btn-icon'),
      liveBtnText: $('live-btn-text'),
      liveBtnLabel: $('live-btn-label'),
      statusBadge: $('status-badge'),
      statusDot: $('status-dot'),
      statusText: $('status-text'),
      nowPlayingState: $('now-playing-state'),
      nowPlayingTitle: $('now-playing-title'),
      nowPlayingArtist: $('now-playing-artist'),
      nowPlayingPlaylist: $('now-playing-playlist'),
      discIcon: $('disc-icon'),
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
      canvas: $('visualizer'),
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
      tabPlaylists: $('tab-playlists'),
      badgeTracksCount: $('badge-tracks-count'),
      badgePlaylistsCount: $('badge-playlists-count'),
      tracksView: $('tracks-view'),
      playlistsView: $('playlists-view'),
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
    this.visualizer = new Visualizer(this.dom.canvas, this.audio.analyser);

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
    this.visualizer.start();
    await this._loadTracks();
    await this.loadPlaylists();
    this._setAiState('offline', 'Waiting to connect');
    this._updateUI();
    this._updateQueueUI();
    this._setupGesturePrewarm();
    this.socket.connect();
    console.log('[App] Init complete');
  }

  _wsUrl() {
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${protocol}//${location.host}/ws/live`;
  }

  async _loadTracks() {
    const maxRetries = 5;
    for (let i = 0; i < maxRetries; i++) {
      try {
        const res = await fetch('/api/tracks');
        if (res.status === 200) {
          this.tracks = await res.json();
          console.log(`[App] Loaded ${this.tracks.length} tracks`);
          return;
        }
      } catch (e) {
        console.error(`[App] Track load attempt ${i + 1} failed:`, e);
      }
      await new Promise(r => setTimeout(r, 1000));
    }
    console.error('[App] Failed to load tracks after retries');
  }

  _bindEvents() {
    this.dom.musicPlayer.addEventListener('ended', () => this.nextTrack());
    this.dom.musicPlayer.addEventListener('play', () => {
      this.isPlaying = true;
      this._sendPlaybackState();
      this._updateUI();
    });
    this.dom.musicPlayer.addEventListener('pause', () => {
      this.isPlaying = false;
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
      this.userVolume = parseFloat(this.dom.volumeSlider.value);
      if (this.audio._musicSource) {
        this.audio.setVolume(this.userVolume, { ducked: this._shouldDuck() });
      } else {
        this.dom.musicPlayer.volume = this.userVolume;
      }
    }, CONFIG.VOLUME_THROTTLE_MS));

    this.dom.playPauseBtn.addEventListener('click', () => this.togglePlayPause());
    this.dom.prevBtn.addEventListener('click', () => this.prevTrack());
    this.dom.nextBtn.addEventListener('click', () => this.nextTrack());

    this.dom.queueClearBtn?.addEventListener('click', () => this.clearQueue());
    this.dom.queuePlayNowBtn?.addEventListener('click', () => this.playNextInQueue());
    this.dom.queueRemoveBtn?.addEventListener('click', () => this.removeFromQueue(0));
    this.dom.queueSaveBtn?.addEventListener('click', () => this.openSaveQueueModal());
    this.dom.queueViewBtn?.addEventListener('click', () => this.toggleQueueExpanded());

    // --- Tab Switching ---
    this.dom.tabTracks?.addEventListener('click', () => this.switchView('tracks'));
    this.dom.tabPlaylists?.addEventListener('click', () => this.switchView('playlists'));

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
    this.dom.addToPlaylistModal?.addEventListener('click', (e) => {
      if (e.target === this.dom.addToPlaylistModal) this.closeAddToPlaylistModal();
    });

    // --- Modal: Save Queue ---
    this.dom.saveQueueCloseBtn?.addEventListener('click', () => this.closeSaveQueueModal());
    this.dom.saveQueueCancelBtn?.addEventListener('click', () => this.closeSaveQueueModal());
    this.dom.saveQueueConfirmBtn?.addEventListener('click', () => this.submitSaveQueue());
    this.dom.saveQueueModal?.addEventListener('click', (e) => {
      if (e.target === this.dom.saveQueueModal) this.closeSaveQueueModal();
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
        this.dom.musicPlayer.pause();
        this.isPlaying = false;
        this._updateUI();
        break;
      case 'resumePlayback':
        this.dom.musicPlayer.play().catch(console.error);
        this.isPlaying = true;
        this._updateUI();
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

    // Image fallback probe — restore emoji disc if 404 or image fails to load
    const artUrl = `/api/tracks/${track.id}/art`;
    const testImg = new Image();
    testImg.onload = () => {
      if (this.currentTrackIndex === index) {
        this.dom.discIcon.style.backgroundImage = `url('${artUrl}')`;
        this.dom.discIcon.textContent = '';
      }
    };
    testImg.onerror = () => {
      if (this.currentTrackIndex === index) {
        this.dom.discIcon.style.backgroundImage = 'none';
        this.dom.discIcon.textContent = '💿';
      }
    };
    testImg.src = artUrl;

    if ('mediaSession' in navigator) {
      navigator.mediaSession.metadata = new MediaMetadata({
        title: track.title,
        artist: track.artist,
        artwork: [{ src: `/api/tracks/${track.id}/art`, sizes: '512x512', type: 'image/jpeg' }]
      });
    }

    const t0 = performance.now();
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
    if (this.queue.length > 0) {
      this.playNextInQueue();
      return;
    }
    if (this.activePlaylist) {
      // The playlist has played out; the deck continues through the library.
      console.log(`[App] Playlist '${this.activePlaylist.name}' finished`);
      this.activePlaylist = null;
      this._sendPlaybackState();
      if (this.currentView === 'playlists') this.renderPlaylists();
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
    const nextTrk = this.queue.shift();
    this._updateQueueUI();
    this.playTrackById(nextTrk.id);
  }

  _updateQueueUI() {
    if (!this.dom.queueContainer) return;
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

      const artUrl = `/api/tracks/${nextTrack.id}/art`;
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
          this.dom.queueThumb.textContent = '🎵';
        }
      };
      testImg.src = artUrl;

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
    if (this.tracks.length === 0) return;
    this.playTrackByIndex(
      this.currentTrackIndex <= 0 ? this.tracks.length - 1 : this.currentTrackIndex - 1
    );
  }

  // --- UI Updates ---

  _updateUI() {
    const isLive = this.fsm.state !== 'offline';
    const isListening = this.fsm.state === 'listening';

    if (isLive) {
      // ON AIR: one look for every live sub-state (connecting, listening,
      // thinking, speaking). Mic/DJ status lives in the Live DJ panel, not
      // on the button, so the button is strictly Talk Live <-> End Live.
      this.dom.liveBtn.className = 'live-btn live-btn-active';
      this.dom.liveBtnIcon.textContent = '⏹';
      this.dom.liveBtnText.textContent = 'End Live';
      this.dom.liveBtn.setAttribute('aria-pressed', 'true');
      this.dom.liveBtnLabel.textContent = isListening
        ? 'Listening — just talk. Press End Live to stop.'
        : 'AI DJ is live. Press End Live to stop.';
    } else {
      // OFF AIR: the button starts a live session with the mic open.
      this.dom.liveBtn.className = 'live-btn';
      this.dom.liveBtnIcon.textContent = '🎙️';
      this.dom.liveBtnText.textContent = 'Talk Live';
      this.dom.liveBtn.setAttribute('aria-pressed', 'false');
      this.dom.liveBtnLabel.textContent = this.serverReady
        ? 'Press Talk Live and just speak. (“Hey DJ” works too.)'
        : 'Preparing AI DJ…';
    }

    const track = this.currentTrackIndex >= 0 ? this.tracks[this.currentTrackIndex] : null;
    this.dom.nowPlayingState.textContent = this.isPlaying ? '▶ Now Playing' : '⏸ Paused';
    this.dom.nowPlayingState.className = this.isPlaying ? 'now-playing-label active' : 'now-playing-label';
    this.dom.nowPlayingTitle.textContent = track ? track.title : '(Ready)';
    this.dom.nowPlayingArtist.textContent = track ? track.artist : '—';
    this._updateActivePlaylistUI(track);
    this.dom.discIcon.className = this.isPlaying ? 'disc spinning' : 'disc';
    this.dom.playPauseIcon.textContent = this.isPlaying ? '⏸' : '▶';

    this.trackRenderer.render(this.tracks, track?.id ?? null, this.trackFilterQuery);
    this._refreshExpandedHighlight(track?.id ?? null);
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
      const isCurrent = tracks[idx]?.id === currentTrackId;
      el.classList.toggle('track-active', isCurrent);
      el.querySelector('.track-num')?.classList.toggle('track-num-active', isCurrent);
    });
  }

  _onTimeUpdate() {
    if (this.isDraggingSeek) return;
    const dur = this.dom.musicPlayer.duration;
    if (!dur) return;

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
    const track = this.currentTrackIndex >= 0 ? this.tracks[this.currentTrackIndex] : null;
    this.socket.send(JSON.stringify({
      type: 'playback_state',
      playing: this.isPlaying,
      trackId: track?.id ?? null,
      queue: this.queue.map(t => ({ id: t.id, title: t.title, artist: t.artist })),
      playlist: this.activePlaylist ? { id: this.activePlaylist.id, name: this.activePlaylist.name } : null
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

  switchView(viewName) {
    this.currentView = viewName;
    if (this.dom.tabTracks) this.dom.tabTracks.classList.toggle('active', viewName === 'tracks');
    if (this.dom.tabPlaylists) this.dom.tabPlaylists.classList.toggle('active', viewName === 'playlists');

    if (this.dom.tracksView) this.dom.tracksView.style.display = viewName === 'tracks' ? 'flex' : 'none';
    if (this.dom.playlistsView) this.dom.playlistsView.style.display = viewName === 'playlists' ? 'flex' : 'none';

    if (viewName === 'playlists') {
      this.renderPlaylists();
    }
  }

  // --- Queue accordion (Up Next card expands in place) ---

  toggleQueueExpanded(force) {
    const next = typeof force === 'boolean' ? force : !this.queueExpanded;
    this.queueExpanded = next && this.queue.length > 0;
    this._renderQueueExpanded();
  }

  _renderQueueExpanded() {
    if (!this.dom.queueExpanded || !this.dom.queueTracksContainer) return;
    const open = this.queueExpanded && this.queue.length > 0;
    this.queueExpanded = open;
    this.dom.queueExpanded.style.display = open ? 'block' : 'none';
    this._setViewToggle(this.dom.queueViewBtn, open);
    if (!open) {
      this.dom.queueTracksContainer.replaceChildren();
      return;
    }

    const frag = document.createDocumentFragment();
    this.queue.forEach((track, idx) => {
      const el = document.createElement('div');
      el.className = 'track-item';
      el.innerHTML = `
        <div class="track-num">${String(idx + 1).padStart(2, '0')}</div>
        <div class="track-info">
          <div class="track-title" title="${escapeHtml(track.title)}">${escapeHtml(track.title)}</div>
          <div class="track-artist">${escapeHtml(track.artist)}</div>
        </div>
        <div class="track-genre">${escapeHtml(track.genre || '')}</div>
        <button class="track-cue-btn queue-item-play-btn" title="Play this now (skips the tracks above it)">▶ Play now</button>
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
  }

  /** Shared look/behaviour for the playlist and queue "view" toggles. */
  _setViewToggle(btn, open) {
    if (!btn) return;
    btn.textContent = open ? '▴ Hide' : '👁 View';
    btn.title = open ? 'Collapse' : 'Expand to see all tracks';
    btn.classList.toggle('open', open);
    btn.setAttribute('aria-expanded', String(open));
  }

  /** Jump to a queued track: everything before it is skipped, after it stays cued. */
  playQueueItemNow(index) {
    if (index < 0 || index >= this.queue.length) return;
    const [track] = this.queue.splice(index, 1);
    this.queue.splice(0, index);
    this._updateQueueUI();
    this.playTrackById(track.id);
  }

  // --- Playlist Management & Operations ---

  async loadPlaylists() {
    try {
      const res = await fetch('/api/playlists');
      if (res.ok) {
        this.playlists = await res.json();
        this._updatePlaylistBadges();
        if (this.currentView === 'playlists') {
          this.renderPlaylists();
        }
      }
    } catch (e) {
      console.error('[App] Failed to load playlists:', e);
    }
  }

  _updatePlaylistBadges() {
    if (this.dom.badgeTracksCount) this.dom.badgeTracksCount.textContent = this.tracks.length;
    if (this.dom.badgePlaylistsCount) this.dom.badgePlaylistsCount.textContent = this.playlists.length;
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
            <button class="view-toggle-btn" type="button"></button>
            <button class="playlist-btn-subtle danger" title="Delete playlist">✕</button>
          </div>
        </div>
        <div class="playlist-expanded" style="display:${isOpen ? 'block' : 'none'};">
          <div class="playlist-expanded-actions">
            <span class="playlist-expanded-sub"></span>
            <button class="playlist-action-btn playlist-btn-shuffle" type="button" title="Shuffle &amp; play">🔀 Shuffle</button>
          </div>
          <div class="track-list playlist-expanded-tracks"></div>
        </div>
      `;

      this._setViewToggle(card.querySelector('.view-toggle-btn'), isOpen);

      card.querySelector('.playlist-btn-play').addEventListener('click', (e) => {
        e.stopPropagation();
        this.playPlaylist(pl.id);
      });
      card.querySelector('.view-toggle-btn').addEventListener('click', (e) => {
        e.stopPropagation();
        this.togglePlaylistExpanded(pl.id);
      });
      card.querySelector('.playlist-btn-subtle.danger').addEventListener('click', async (e) => {
        e.stopPropagation();
        if (confirm(`Delete playlist "${pl.name}"?`)) {
          await this.deletePlaylist(pl.id);
        }
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
      if (this.expandedPlaylist?.id === playlistId) this.expandedPlaylist = null;
      this.renderPlaylists();
      return;
    }
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
    this.switchView('playlists');
    await this.togglePlaylistExpanded(playlistId, true);
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
    if (this.currentView === 'playlists') this.renderPlaylists();
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
    tracks.forEach((track, idx) => {
      const el = document.createElement('div');
      const isCurrent = currentTrackId === track.id;
      el.className = 'track-item' + (isCurrent ? ' track-active' : '');
      el.innerHTML = `
        <div class="track-num ${isCurrent ? 'track-num-active' : ''}">${String(idx + 1).padStart(2, '0')}</div>
        <div class="track-info">
          <div class="track-title" title="${escapeHtml(track.title)}">${escapeHtml(track.title)}</div>
          <div class="track-artist">${escapeHtml(track.artist)}</div>
        </div>
        <div class="track-genre">${escapeHtml(track.genre || '')}</div>
        <button class="track-cue-btn" title="Cue up next">+ Cue</button>
        <button class="track-remove-from-pl-btn" title="Remove from playlist">✕</button>
      `;
      el.querySelector('.track-cue-btn').addEventListener('click', (e) => {
        e.stopPropagation();
        this.queueTrackById(track.id);
      });
      el.querySelector('.track-remove-from-pl-btn').addEventListener('click', async (e) => {
        e.stopPropagation();
        await this.removeTrackFromPlaylist(pl.id, track.id);
      });
      el.addEventListener('click', () => {
        // Start the playlist from this track; the rest stays cued
        this.activePlaylist = pl;
        this.queue = tracks.slice(idx + 1);
        this._updateQueueUI();
        this.playTrackById(track.id);
        this._sendPlaybackState();
        this.renderPlaylists();
      });
      frag.appendChild(el);
    });
    container.replaceChildren(frag);
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
      if (this.currentView === 'playlists') this.renderPlaylists();
    } catch (e) {
      console.error('[App] Failed to play playlist:', e);
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
    const name = prompt('Enter a name for the new playlist:');
    if (name && name.trim()) {
      this.createNewPlaylist(name.trim());
    }
  }

  async createNewPlaylist(name, trackIds = []) {
    try {
      const res = await fetch('/api/playlists', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, trackIds, description: 'Custom Playlist' })
      });
      if (res.ok) {
        const created = await res.json();
        await this.loadPlaylists();
        this.showPlaylist(created.id);
      }
    } catch (e) {
      console.error('[App] Create playlist error:', e);
    }
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
    this.queue.forEach(t => allTracks.push(t.id));

    if (allTracks.length === 0) {
      alert('The queue is empty. Cue up some songs first!');
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

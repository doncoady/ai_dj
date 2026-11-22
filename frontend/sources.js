/**
 * AI DJ — Source helpers for radio, podcasts, audiobooks and online streaming (pure functions).
 *
 * Everything here is DOM-free so it can be unit-tested with `node --test`
 * (see frontend/tests/). app.js imports these to describe the active source,
 * build the `source` field of `playback_state`, remember podcast positions
 * and filter the Radio / Podcasts / Audiobooks tabs.
 *
 * A "source" is what the deck is playing instead of a local track:
 *   { type: 'radio',     station: {id, name, genre, country, favicon, streamUrl}, origin: 'saved'|'directory' }
 *   { type: 'podcast',   podcast: {id, title, author, artworkUrl}, episode: {id, title, duration, publishedAt, description} }
 *   { type: 'audiobook', book: {id, title, author, narrator, chapterCount}, chapter: {id, index, title, duration} }
 *   { type: 'stream',    track: {id, provider, title, artist, album, albumArt, durationMs, full, audioUrl}, context }
 * `null` means the local library deck.
 */

export const SKIP_BACK_S = 15;
export const SKIP_FWD_S = 30;
export const POSITIONS_KEY = 'aidj.podcastPositions';
/** Within this many seconds of the end an episode counts as finished. */
export const FINISHED_MARGIN_S = 10;
/** Don't bother remembering positions this early in an episode. */
export const MIN_REMEMBER_S = 5;
export const MAX_REMEMBERED = 200;
export const RADIO_NOW_POLL_MS = 10000;

export const DECK_LABELS = {
  library: 'Deck_1.A // Local_Library',
  radio: 'Deck_1.A // Live_Radio',
  podcast: 'Deck_1.A // Podcast',
  audiobook: 'Deck_1.A // Audiobook',
  stream: 'Deck_1.A // Streaming',
};
/** Preview providers' clips are this long (Apple Music / Deezer). */
export const STREAM_PREVIEW_S = 30;

/**
 * Streaming providers shown as sub-sections of the Streaming tab. The server
 * reports which are actually available (/api/streaming/status); this table
 * only carries the static presentation.
 */
export const STREAM_PROVIDERS = {
  apple: { name: 'Apple Music', icon: '🍎', full: false },
  deezer: { name: 'Deezer', icon: '🎵', full: false },
  ytmusic: { name: 'YouTube Music', icon: '▶️', full: true },
};
export const STREAM_PROVIDER_ORDER = ['ytmusic', 'apple', 'deezer'];
/** Report audiobook progress to the server at most this often while playing. */
export const BOOK_PROGRESS_SAVE_MS = 10000;

/**
 * Registry of non-library source kinds. Adding a future source (audiobooks,
 * SoundCloud, TuneIn, …) means: one entry here, one `case` in
 * app.js for its play command, and one tab/view in index.html. Everything
 * that renders the NOW PLAYING card, the thumbnail badge and the deck label
 * reads from this table instead of hard-coding kinds.
 *   live:      true  -> no seek, stop/re-tune instead of pause/resume
 *   seekable:  true  -> show ↺/↻ skip buttons and remember positions
 *   speed:     true  -> the playback-speed control applies (spoken word)
 */
export const SOURCE_KINDS = {
  radio: { label: 'LIVE', icon: '📻', deckLabel: DECK_LABELS.radio, live: true, seekable: false, speed: false, noun: 'station' },
  podcast: { label: 'PODCAST', icon: '🎙', deckLabel: DECK_LABELS.podcast, live: false, seekable: true, speed: true, noun: 'episode' },
  audiobook: { label: 'AUDIOBOOK', icon: '📖', deckLabel: DECK_LABELS.audiobook, live: false, seekable: true, speed: true, noun: 'chapter' },
  stream: { label: 'STREAM', icon: '🌐', deckLabel: DECK_LABELS.stream, live: false, seekable: true, speed: false, noun: 'track' },
};

export function sourceKind(source) {
  return source && SOURCE_KINDS[source.type] ? SOURCE_KINDS[source.type] : null;
}

/** Categories offered as one-tap searches in the podcast subscribe modal. */
export const PODCAST_CATEGORIES = [
  'News', 'Comedy', 'True Crime', 'Science', 'Technology', 'Business', 'History',
  'Sports', 'Health', 'Music', 'Society & Culture', 'Kids & Family',
];

/** Fallback genre chips for the station finder when the server list is unavailable. */
export const STATION_GENRES_FALLBACK = [
  { label: 'Pop', tag: 'pop' }, { label: 'Rock', tag: 'rock' }, { label: 'Jazz', tag: 'jazz' },
  { label: 'Classical', tag: 'classical' }, { label: 'Electronic', tag: 'electronic' }, { label: 'Hip-Hop', tag: 'hip hop' },
  { label: 'Country', tag: 'country' }, { label: 'News', tag: 'news' }, { label: 'Talk', tag: 'talk' },
];

export function formatClock(seconds) {
  if (!isFinite(seconds) || isNaN(seconds) || seconds < 0) return '0:00';
  const total = Math.floor(seconds);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return h
    ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
    : `${m}:${String(s).padStart(2, '0')}`;
}

/** 3723 -> "1h 02m", 2520 -> "42 min", 30 -> "30 sec", nothing -> "". */
export function formatDuration(seconds) {
  if (!isFinite(seconds) || seconds == null || seconds <= 0) return '';
  const total = Math.floor(seconds);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  if (h) return `${h}h ${String(m).padStart(2, '0')}m`;
  if (m) return `${m} min`;
  return `${total} sec`;
}

/** Epoch seconds -> "Mar 3, 2025" (empty when unknown). */
export function formatEpisodeDate(epochSeconds, locale = 'en-US') {
  if (!epochSeconds || !isFinite(epochSeconds)) return '';
  return new Date(epochSeconds * 1000).toLocaleDateString(locale, { year: 'numeric', month: 'short', day: 'numeric' });
}

/**
 * What the NOW PLAYING card should show for a source.
 * Returns { title, artist, deckLabel, isLive, badge, icon, sourceLine }.
 */
export function describeSource(source, { nowTitle = '' } = {}) {
  const kind = sourceKind(source);
  const library = { title: null, artist: null, deckLabel: DECK_LABELS.library, isLive: false, seekable: true, badge: null, icon: '💿', sourceLine: '', art: '' };
  if (!kind) return library;
  const base = { deckLabel: kind.deckLabel, isLive: kind.live, seekable: kind.seekable, badge: kind.label, icon: kind.icon };
  if (source.type === 'radio') {
    const st = source.station || {};
    const meta = [st.genre, st.country].filter(Boolean).join(' • ');
    return {
      ...base,
      title: st.name || 'Live radio',
      artist: nowTitle ? `♪ ${nowTitle}` : (st.genre || 'Live radio'),
      sourceLine: meta || 'Internet radio',
      art: st.favicon || '',
    };
  }
  if (source.type === 'podcast') {
    const ep = source.episode || {};
    const pod = source.podcast || {};
    return {
      ...base,
      title: ep.title || 'Episode',
      artist: pod.title || 'Podcast',
      sourceLine: episodeMetaLine(ep),
      art: pod.artworkUrl || '',
    };
  }
  if (source.type === 'audiobook') {
    const book = source.book || {};
    const ch = source.chapter || {};
    const who = [book.author, book.narrator ? `read by ${book.narrator}` : ''].filter(Boolean).join(' • ');
    return {
      ...base,
      title: book.title || 'Audiobook',
      artist: who || 'Audiobook',
      sourceLine: chapterMetaLine(book, ch),
      art: book.id ? `/api/audiobooks/${book.id}/art` : '',
    };
  }
  if (source.type === 'stream') {
    const t = source.track || {};
    return {
      ...base,
      title: t.title || 'Streamed track',
      artist: t.artist || providerName(t.provider),
      sourceLine: streamMetaLine(t, source.context),
      art: t.albumArt || '',
      badge: t.full ? providerName(t.provider).toUpperCase() : `${providerName(t.provider).toUpperCase()} • PREVIEW`,
    };
  }
  return library;
}

/** "Chapter 3 of 12 • An Unexpected Party • 42 min" (whatever is known). */
export function chapterMetaLine(book, chapter) {
  const ch = chapter || {};
  const total = book?.chapterCount || book?.chapters?.length || 0;
  const num = ch.index ? `Chapter ${ch.index}${total ? ` of ${total}` : ''}` : '';
  const title = ch.title && !isGenericChapterTitle(ch.title, ch.index, book?.title) ? ch.title : '';
  return [num, title, formatDuration(ch.duration)].filter(Boolean).join(' • ');
}

/** "Chapter 3", "03", "Part 3", or the book's own title carry no extra information. */
export function isGenericChapterTitle(title, index, bookTitle) {
  const t = (title || '').trim().toLowerCase();
  if (!t) return true;
  if (bookTitle && t === String(bookTitle).trim().toLowerCase()) return true;
  if (!index) return false;
  return new RegExp(`^(?:chapter|part|track|section)?\\s*0*${index}$`).test(t);
}

/** Card/listing status for a book: "Not started" / "Finished" / "Chapter 3 of 12 • 45%". */
export function bookProgressLabel(book) {
  const p = book?.progress;
  if (!p || (!p.chapterId && !p.finished)) return 'Not started';
  if (p.finished) return 'Finished';
  const total = book.chapterCount || 0;
  const where = p.chapterIndex ? `Chapter ${p.chapterIndex}${total ? ` of ${total}` : ''}` : '';
  const pct = p.fraction ? `${Math.round(p.fraction * 100)}%` : '';
  const left = book.duration && p.listened != null ? remainingLabel(p.listened, book.duration) : '';
  return [where, pct, left].filter(Boolean).join(' • ') || 'In progress';
}

/** Chapter to play (and where) when the listener presses ▶ on a book. */
export function resumePointFor(book) {
  const chapters = book?.chapters || [];
  if (!chapters.length) return null;
  const p = book.progress;
  if (p && !p.finished && p.chapterId) {
    const ch = chapters.find(c => c.id === p.chapterId);
    if (ch) return { chapter: ch, position: Math.max(0, Number(p.position) || 0) };
  }
  return { chapter: chapters[0], position: 0 };
}

/** The chapter after `chapterId`, or null at the end of the book. */
export function nextChapter(book, chapterId) {
  const chapters = book?.chapters || [];
  const idx = chapters.findIndex(c => c.id === chapterId);
  return idx >= 0 && idx + 1 < chapters.length ? chapters[idx + 1] : null;
}

export function previousChapter(book, chapterId) {
  const chapters = book?.chapters || [];
  const idx = chapters.findIndex(c => c.id === chapterId);
  return idx > 0 ? chapters[idx - 1] : null;
}

// ── Online streaming ─────────────────────────────────────────────────────────

/** Human name of a streaming provider id. */
export function providerName(pid) {
  return STREAM_PROVIDERS[pid]?.name || 'Streaming';
}

/** Can the deck play this track? (full songs always; previews only with a clip) */
export function streamPlayable(track) {
  return !!(track && (track.full || track.audioUrl));
}

/** 225000 -> "3:45" */
export function formatMs(ms) {
  return formatClock((Number(ms) || 0) / 1000);
}

/** "Album • 3:45 • Apple Music • preview" (previews only say so; whole songs carry no extra label) */
export function streamMetaLine(track, context = null) {
  const t = track || {};
  const how = t.full ? '' : 'preview';
  const from = context?.name && (context.type === 'album' || context.type === 'artist') ? `from ${context.type} ${context.name}` : '';
  return [t.album, t.durationMs ? formatMs(t.durationMs) : '', t.explicit ? 'Explicit' : '', from, providerName(t.provider), how].filter(Boolean).join(' • ');
}

/** Queue entry for a streamed track (sits next to songs and episodes in Up Next). */
export function streamQueueItem(track, context = null) {
  return {
    id: track.id, title: track.title, artist: track.artist, genre: providerName(track.provider), kind: 'stream',
    track, context: context || null,
  };
}

/** Queue entry for an audiobook chapter. */
export function chapterQueueItem(book, chapter) {
  return {
    id: chapter.id, title: chapter.title, artist: book.title, genre: 'Audiobook', kind: 'audiobook',
    book: { id: book.id, title: book.title, author: book.author, narrator: book.narrator, chapterCount: book.chapterCount || (book.chapters || []).length },
    chapter,
  };
}

/** "Episode 12 • Mar 4, 2025 • 1h 02m" (whatever parts are known). */
export function episodeMetaLine(episode) {
  const ep = episode || {};
  const num = ep.episodeNumber ? `${ep.season ? `S${ep.season} ` : ''}Episode ${ep.episodeNumber}` : '';
  return [num, formatEpisodeDate(ep.publishedAt), formatDuration(ep.duration)].filter(Boolean).join(' • ');
}

/** "48 min left" / "1h 02m left" / "" when unknown or finished. */
export function remainingLabel(currentTime, duration) {
  if (!isFinite(duration) || duration <= 0 || !isFinite(currentTime)) return '';
  const left = Math.max(0, duration - currentTime);
  if (left < 60) return left <= 0 ? '' : 'under a minute left';
  return `${formatDuration(left)} left`;
}

/**
 * Short preview of show notes for list rows (first sentence-ish, capped).
 */
export function notesExcerpt(text, max = 140) {
  const t = (text || '').replace(/\s+/g, ' ').trim();
  if (t.length <= max) return t;
  const cut = t.slice(0, max);
  const stop = Math.max(cut.lastIndexOf('. '), cut.lastIndexOf(' '));
  return (stop > max * 0.5 ? cut.slice(0, stop) : cut).trimEnd() + '…';
}

/** Should the starter-picks note show for a list? (any starter item, not dismissed) */
export function shouldShowStarterNote(items, dismissed) {
  return !dismissed && (items || []).some(i => i && i.source === 'default');
}

/** The `source` field of the browser's playback_state report (null for the library deck). */
export function buildSourcePayload(source, { position = null, duration = null, nowTitle = '' } = {}) {
  if (!source) return null;
  if (source.type === 'radio') {
    const st = source.station || {};
    return {
      type: 'radio', id: st.id, name: st.name, genre: st.genre || '', streamUrl: st.streamUrl || null,
      saved: source.origin !== 'directory', nowTitle: nowTitle || null,
    };
  }
  if (source.type === 'podcast') {
    const ep = source.episode || {};
    const pod = source.podcast || {};
    const dur = isFinite(duration) && duration > 0 ? Math.round(duration) : (ep.duration || null);
    return {
      type: 'podcast', id: pod.id, name: pod.title, episodeId: ep.id, episodeTitle: ep.title,
      episodeDescription: (ep.description || '').slice(0, 300),
      position: isFinite(position) && position >= 0 ? Math.round(position) : null,
      duration: dur,
    };
  }
  if (source.type === 'audiobook') {
    const book = source.book || {};
    const ch = source.chapter || {};
    const dur = isFinite(duration) && duration > 0 ? Math.round(duration) : (ch.duration || null);
    return {
      type: 'audiobook', id: book.id, name: book.title, author: book.author || '',
      chapterId: ch.id, chapterIndex: ch.index || null, chapterTitle: ch.title || '',
      chapterCount: book.chapterCount || (book.chapters || []).length || null,
      position: isFinite(position) && position >= 0 ? Math.round(position) : null,
      duration: dur,
    };
  }
  if (source.type === 'stream') {
    const t = source.track || {};
    const dur = isFinite(duration) && duration > 0 ? Math.round(duration)
      : (t.full ? Math.round((t.durationMs || 0) / 1000) || null : STREAM_PREVIEW_S);
    return {
      type: 'stream', id: t.id, title: t.title, artist: t.artist, album: t.album || '', provider: t.provider || null, full: !!t.full,
      contextType: source.context?.type || null, contextName: source.context?.name || null,
      position: isFinite(position) && position >= 0 ? Math.round(position) : null,
      duration: dur,
    };
  }
  return null;
}

// ── Podcast resume positions ───────────────────────────────────────────────

/** Seconds to start an episode at: 0 when never heard, or finished, or unknown. */
export function resumePositionFor(positions, episodeId, duration) {
  const pos = positions?.[episodeId];
  if (!isFinite(pos) || pos <= MIN_REMEMBER_S) return 0;
  if (isFinite(duration) && duration > 0 && pos >= duration - FINISHED_MARGIN_S) return 0;
  return Math.floor(pos);
}

/**
 * New positions map after hearing `position` seconds of `episodeId`.
 * Finished episodes are forgotten; the map is capped (oldest entries dropped).
 */
export function rememberPosition(positions, episodeId, position, duration) {
  const next = { ...(positions || {}) };
  if (!episodeId) return next;
  const finished = isFinite(duration) && duration > 0 && position >= duration - FINISHED_MARGIN_S;
  if (!isFinite(position) || position <= MIN_REMEMBER_S || finished) {
    delete next[episodeId];
    return next;
  }
  delete next[episodeId];                // re-insert so it becomes the newest key
  next[episodeId] = Math.floor(position);
  const keys = Object.keys(next);
  if (keys.length > MAX_REMEMBERED) {
    for (const k of keys.slice(0, keys.length - MAX_REMEMBERED)) delete next[k];
  }
  return next;
}

export function loadPositions(storage) {
  try {
    const raw = storage?.getItem(POSITIONS_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {};
  } catch (e) {
    return {};
  }
}

export function savePositions(storage, positions) {
  try {
    storage?.setItem(POSITIONS_KEY, JSON.stringify(positions || {}));
    return true;
  } catch (e) {
    return false;
  }
}

/** 0..1 listened fraction for the episode progress bar. */
export function episodeProgress(positions, episode) {
  const pos = resumePositionFor(positions, episode?.id, episode?.duration);
  if (!pos || !episode?.duration) return 0;
  return Math.min(1, pos / episode.duration);
}

// ── Tab filters ───────────────────────────────────────────────────────────

export function filterStations(stations, query) {
  const q = (query || '').toLowerCase().trim();
  if (!q) return stations || [];
  return (stations || []).filter(s =>
    (s.name || '').toLowerCase().includes(q) ||
    (s.genre || '').toLowerCase().includes(q) ||
    (s.country || '').toLowerCase().includes(q));
}

/**
 * Audiobooks tab order: the books you listened to most recently first
 * (unfinished before finished), then the rest by title.
 */
export function sortAudiobooks(books) {
  const list = (books || []).slice();
  const recency = (b) => Number(b?.progress?.updatedAt) || 0;
  const title = (b) => String(b?.title || '').replace(/^(the|a|an)\s+/i, '').toLowerCase();
  return list.sort((a, b) => {
    const ra = recency(a), rb = recency(b);
    if (!!ra !== !!rb) return ra ? -1 : 1;                       // touched books first
    if (ra && rb) {
      const fa = a.progress?.finished ? 1 : 0, fb = b.progress?.finished ? 1 : 0;
      if (fa !== fb) return fa - fb;                              // in progress before finished
      return rb - ra;                                             // most recently played first
    }
    return title(a).localeCompare(title(b));
  });
}

export function filterAudiobooks(books, query) {
  const q = (query || '').toLowerCase().trim();
  if (!q) return books || [];
  return (books || []).filter(b =>
    (b.title || '').toLowerCase().includes(q) ||
    (b.author || '').toLowerCase().includes(q) ||
    (b.narrator || '').toLowerCase().includes(q) ||
    (b.genre || '').toLowerCase().includes(q));
}

export function filterPodcasts(podcasts, query) {
  const q = (query || '').toLowerCase().trim();
  if (!q) return podcasts || [];
  return (podcasts || []).filter(p =>
    (p.title || '').toLowerCase().includes(q) ||
    (p.author || '').toLowerCase().includes(q) ||
    (p.description || '').toLowerCase().includes(q));
}

/** Live streams must never be served from the browser cache. */
export function withCacheBuster(url, now = Date.now()) {
  return `${url}${url.includes('?') ? '&' : '?'}t=${now}`;
}

/** Clamp a relative seek so it stays inside the media. */
export function clampSeek(currentTime, deltaSeconds, duration) {
  const target = (isFinite(currentTime) ? currentTime : 0) + (Number(deltaSeconds) || 0);
  const max = isFinite(duration) && duration > 0 ? duration : Infinity;
  return Math.max(0, Math.min(max, target));
}

/**
 * Unit tests for frontend/sources.js (pure radio/podcast helpers).
 * Run directly:  node --test frontend/tests/
 * Or via pytest: backend/tests/test_frontend.py shells out to node.
 */
import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import {
  DECK_LABELS, FINISHED_MARGIN_S, MIN_REMEMBER_S, MAX_REMEMBERED, POSITIONS_KEY,
  SOURCE_KINDS, PODCAST_CATEGORIES, STATION_GENRES_FALLBACK, sourceKind,
  formatClock, formatDuration, formatEpisodeDate, describeSource, buildSourcePayload,
  episodeMetaLine, remainingLabel, notesExcerpt, shouldShowStarterNote,
  resumePositionFor, rememberPosition, loadPositions, savePositions, episodeProgress,
  filterStations, filterPodcasts, withCacheBuster, clampSeek,
} from '../sources.js';

const station = { id: 'rs_1', name: 'Groove Salad', genre: 'Ambient, Downtempo', country: 'USA', favicon: '', streamUrl: 'https://ice.example/gs' };
const podcast = { id: 'pc_1', title: 'Night Shift Radio Hour', author: 'Dana Rivers', artworkUrl: 'https://a/art.jpg' };
const episode = { id: 'ep_1', title: 'Episode 12: The Last Train', duration: 3723, publishedAt: 1741068000, description: 'A conductor who never sleeps.' };

describe('formatting', () => {
  test('formatClock', () => {
    assert.equal(formatClock(0), '0:00');
    assert.equal(formatClock(73), '1:13');
    assert.equal(formatClock(3723), '1:02:03');
    assert.equal(formatClock(NaN), '0:00');
    assert.equal(formatClock(Infinity), '0:00');
    assert.equal(formatClock(-5), '0:00');
  });

  test('formatDuration', () => {
    assert.equal(formatDuration(3723), '1h 02m');
    assert.equal(formatDuration(2520), '42 min');
    assert.equal(formatDuration(30), '30 sec');
    assert.equal(formatDuration(0), '');
    assert.equal(formatDuration(null), '');
    assert.equal(formatDuration(undefined), '');
  });

  test('formatEpisodeDate', () => {
    assert.equal(formatEpisodeDate(1741068000), 'Mar 4, 2025');
    assert.equal(formatEpisodeDate(null), '');
    assert.equal(formatEpisodeDate(0), '');
  });
});

describe('SOURCE_KINDS registry', () => {
  test('every kind declares the fields the UI relies on', () => {
    for (const [type, kind] of Object.entries(SOURCE_KINDS)) {
      assert.ok(kind.label && kind.icon && kind.deckLabel && kind.noun, type);
      assert.equal(typeof kind.live, 'boolean');
      assert.equal(typeof kind.seekable, 'boolean');
      assert.equal(typeof kind.speed, 'boolean');
      assert.ok(!(kind.live && kind.seekable), `${type}: a live source cannot be seekable`);
      assert.ok(!(kind.speed && !kind.seekable), `${type}: speed control needs a seekable source`);
    }
    assert.deepEqual(Object.keys(SOURCE_KINDS), ['radio', 'podcast', 'audiobook', 'stream']);
    assert.equal(sourceKind({ type: 'radio' }), SOURCE_KINDS.radio);
    assert.equal(sourceKind({ type: 'audiobook' }), SOURCE_KINDS.audiobook);
    assert.equal(sourceKind({ type: 'stream' }), SOURCE_KINDS.stream);
    assert.equal(sourceKind({ type: 'youtube' }), null);     // not registered
    assert.equal(sourceKind(null), null);
  });

  test('describeSource reads live/seekable from the registry', () => {
    assert.equal(describeSource({ type: 'radio', station }).seekable, false);
    assert.equal(describeSource({ type: 'podcast', podcast, episode }).seekable, true);
    assert.equal(describeSource(null).seekable, true);
  });

  test('browse lists are non-empty and well-formed', () => {
    assert.ok(PODCAST_CATEGORIES.length >= 8);
    assert.ok(STATION_GENRES_FALLBACK.every(g => g.label && g.tag));
  });
});

describe('episode info helpers', () => {
  test('episodeMetaLine', () => {
    assert.equal(episodeMetaLine(episode), 'Mar 4, 2025 • 1h 02m');
    assert.equal(episodeMetaLine({ ...episode, episodeNumber: 12 }), 'Episode 12 • Mar 4, 2025 • 1h 02m');
    assert.equal(episodeMetaLine({ ...episode, episodeNumber: 3, season: 2 }), 'S2 Episode 3 • Mar 4, 2025 • 1h 02m');
    assert.equal(episodeMetaLine({ title: 'x' }), '');
    assert.equal(episodeMetaLine(null), '');
  });

  test('remainingLabel', () => {
    assert.equal(remainingLabel(0, 3723), '1h 02m left');
    assert.equal(remainingLabel(1203, 3723), '42 min left');
    assert.equal(remainingLabel(3700, 3723), 'under a minute left');
    assert.equal(remainingLabel(3723, 3723), '');
    assert.equal(remainingLabel(10, Infinity), '');
    assert.equal(remainingLabel(10, 0), '');
    assert.equal(remainingLabel(NaN, 100), '');
  });

  test('notesExcerpt trims at a sentence or word boundary', () => {
    assert.equal(notesExcerpt('Short note.'), 'Short note.');
    const long = 'A conductor who never sleeps. He rides the last train every night and talks to the passengers about their lives and regrets.';
    const out = notesExcerpt(long, 60);
    assert.ok(out.length <= 61 && out.endsWith('…'), out);
    assert.ok(!out.includes('  '));
    assert.equal(notesExcerpt('  spaced   out   text  ', 100), 'spaced out text');
    assert.equal(notesExcerpt(null), '');
  });

  test('shouldShowStarterNote', () => {
    const starters = [{ id: 1, source: 'default' }, { id: 2, source: 'user' }];
    assert.equal(shouldShowStarterNote(starters, false), true);
    assert.equal(shouldShowStarterNote(starters, true), false);
    assert.equal(shouldShowStarterNote([{ id: 2, source: 'user' }], false), false);
    assert.equal(shouldShowStarterNote([], false), false);
    assert.equal(shouldShowStarterNote(undefined, false), false);
  });
});

describe('describeSource', () => {
  test('library deck', () => {
    const d = describeSource(null);
    assert.equal(d.title, null);
    assert.equal(d.deckLabel, DECK_LABELS.library);
    assert.equal(d.isLive, false);
    assert.equal(d.badge, null);
  });

  test('radio without and with an on-air title', () => {
    let d = describeSource({ type: 'radio', station, origin: 'saved' });
    assert.equal(d.title, 'Groove Salad');
    assert.equal(d.artist, 'Ambient, Downtempo');            // genre until a title is known
    assert.equal(d.isLive, true);
    assert.equal(d.badge, 'LIVE');
    assert.equal(d.deckLabel, DECK_LABELS.radio);
    assert.equal(d.sourceLine, 'Ambient, Downtempo • USA');
    assert.equal(d.icon, '📻');

    d = describeSource({ type: 'radio', station }, { nowTitle: 'Tycho - Awake' });
    assert.equal(d.artist, '♪ Tycho - Awake');

    d = describeSource({ type: 'radio', station: { id: 'x', name: 'Bare' } });
    assert.equal(d.artist, 'Live radio');
    assert.equal(d.sourceLine, 'Internet radio');
  });

  test('podcast episode', () => {
    const d = describeSource({ type: 'podcast', podcast, episode });
    assert.equal(d.title, 'Episode 12: The Last Train');
    assert.equal(d.artist, 'Night Shift Radio Hour');
    assert.equal(d.isLive, false);
    assert.equal(d.badge, 'PODCAST');
    assert.equal(d.deckLabel, DECK_LABELS.podcast);
    assert.equal(d.sourceLine, 'Mar 4, 2025 • 1h 02m');
    assert.equal(d.art, 'https://a/art.jpg');
  });

  test('unknown source type degrades to the library look', () => {
    assert.equal(describeSource({ type: 'tv' }).deckLabel, DECK_LABELS.library);
  });
});

describe('buildSourcePayload (playback_state.source)', () => {
  test('null for the library deck', () => {
    assert.equal(buildSourcePayload(null), null);
    assert.equal(buildSourcePayload({ type: 'tv' }), null);
  });

  test('radio payload carries the stream URL so the server can save a directory station', () => {
    const p = buildSourcePayload({ type: 'radio', station, origin: 'directory' }, { nowTitle: 'Tycho - Awake' });
    assert.deepEqual(p, {
      type: 'radio', id: 'rs_1', name: 'Groove Salad', genre: 'Ambient, Downtempo',
      streamUrl: 'https://ice.example/gs', saved: false, nowTitle: 'Tycho - Awake',
    });
    assert.equal(buildSourcePayload({ type: 'radio', station, origin: 'saved' }).saved, true);
    assert.equal(buildSourcePayload({ type: 'radio', station }).nowTitle, null);
  });

  test('podcast payload rounds position/duration and prefers the real media duration', () => {
    const p = buildSourcePayload({ type: 'podcast', podcast, episode }, { position: 754.6, duration: 3700.2 });
    assert.equal(p.type, 'podcast');
    assert.equal(p.id, 'pc_1');
    assert.equal(p.name, 'Night Shift Radio Hour');
    assert.equal(p.episodeId, 'ep_1');
    assert.equal(p.episodeTitle, 'Episode 12: The Last Train');
    assert.equal(p.position, 755);
    assert.equal(p.duration, 3700);
    assert.equal(p.episodeDescription, 'A conductor who never sleeps.');

    const q = buildSourcePayload({ type: 'podcast', podcast, episode }, { position: NaN, duration: null });
    assert.equal(q.position, null);
    assert.equal(q.duration, 3723);                           // falls back to the feed's duration
  });
});

describe('podcast resume positions', () => {
  test('resumePositionFor', () => {
    assert.equal(resumePositionFor({}, 'ep_1', 3723), 0);
    assert.equal(resumePositionFor({ ep_1: 600.9 }, 'ep_1', 3723), 600);
    assert.equal(resumePositionFor({ ep_1: MIN_REMEMBER_S }, 'ep_1', 3723), 0);            // too early to matter
    assert.equal(resumePositionFor({ ep_1: 3723 - FINISHED_MARGIN_S }, 'ep_1', 3723), 0);  // finished
    assert.equal(resumePositionFor({ ep_1: 3000 }, 'ep_1', undefined), 3000);              // unknown duration
    assert.equal(resumePositionFor(null, 'ep_1', 10), 0);
  });

  test('rememberPosition stores, forgets finished, and caps the map', () => {
    let pos = rememberPosition({}, 'ep_1', 600.7, 3723);
    assert.deepEqual(pos, { ep_1: 600 });
    pos = rememberPosition(pos, 'ep_1', 3720, 3723);
    assert.deepEqual(pos, {});                                 // finished -> forgotten
    pos = rememberPosition({ ep_1: 600 }, 'ep_1', 2, 3723);
    assert.deepEqual(pos, {});                                 // rewound to the start -> forgotten
    pos = rememberPosition({ ep_1: 600 }, '', 100, 3723);
    assert.deepEqual(pos, { ep_1: 600 });                      // no id: no-op
    pos = rememberPosition({ ep_1: 600 }, 'ep_1', 1200, Infinity);
    assert.deepEqual(pos, { ep_1: 1200 });                     // unknown duration never counts as finished

    let many = {};
    for (let i = 0; i < MAX_REMEMBERED + 5; i++) many = rememberPosition(many, `ep_${i}`, 100, 1000);
    assert.equal(Object.keys(many).length, MAX_REMEMBERED);
    assert.equal('ep_0' in many, false);                       // oldest dropped
    assert.equal(`ep_${MAX_REMEMBERED + 4}` in many, true);
  });

  test('load/save round-trip through a storage-like object and tolerate garbage', () => {
    const store = new Map();
    const storage = { getItem: (k) => (store.has(k) ? store.get(k) : null), setItem: (k, v) => store.set(k, v) };
    assert.deepEqual(loadPositions(storage), {});
    assert.equal(savePositions(storage, { ep_1: 42 }), true);
    assert.equal(store.get(POSITIONS_KEY), '{"ep_1":42}');
    assert.deepEqual(loadPositions(storage), { ep_1: 42 });
    store.set(POSITIONS_KEY, 'not json');
    assert.deepEqual(loadPositions(storage), {});
    store.set(POSITIONS_KEY, '[1,2]');
    assert.deepEqual(loadPositions(storage), {});
    assert.deepEqual(loadPositions(null), {});
    assert.equal(savePositions({ setItem() { throw new Error('quota'); } }, {}), false);
  });

  test('episodeProgress', () => {
    assert.equal(episodeProgress({ ep_1: 1861.5 }, { id: 'ep_1', duration: 3722 }), 0.5);   // position is floored
    assert.equal(episodeProgress({}, episode), 0);
    assert.equal(episodeProgress({ ep_1: 100 }, { id: 'ep_1' }), 0);   // unknown duration
  });
});

describe('tab filters and URL helpers', () => {
  const stations = [station, { id: 'rs_2', name: 'FIP', genre: 'Jazz', country: 'France' }];
  const podcasts = [podcast, { id: 'pc_2', title: 'Other', author: 'X', description: 'about sleep' }];

  test('filterStations matches name, genre, country', () => {
    assert.deepEqual(filterStations(stations, '').map(s => s.id), ['rs_1', 'rs_2']);
    assert.deepEqual(filterStations(stations, 'jazz').map(s => s.id), ['rs_2']);
    assert.deepEqual(filterStations(stations, ' france ').map(s => s.id), ['rs_2']);
    assert.deepEqual(filterStations(stations, 'GROOVE').map(s => s.id), ['rs_1']);
    assert.deepEqual(filterStations(stations, 'zzz'), []);
    assert.deepEqual(filterStations(undefined, 'x'), []);
  });

  test('filterPodcasts matches title, author, description', () => {
    assert.deepEqual(filterPodcasts(podcasts, 'night').map(p => p.id), ['pc_1']);
    assert.deepEqual(filterPodcasts(podcasts, 'dana').map(p => p.id), ['pc_1']);
    assert.deepEqual(filterPodcasts(podcasts, 'sleep').map(p => p.id), ['pc_2']);
    assert.equal(filterPodcasts(podcasts, '').length, 2);
  });

  test('withCacheBuster', () => {
    assert.equal(withCacheBuster('/api/radio/rs_1/stream', 123), '/api/radio/rs_1/stream?t=123');
    assert.equal(withCacheBuster('/x?y=1', 123), '/x?y=1&t=123');
  });

  test('clampSeek', () => {
    assert.equal(clampSeek(100, 30, 3723), 130);
    assert.equal(clampSeek(10, -30, 3723), 0);
    assert.equal(clampSeek(3700, 60, 3723), 3723);
    assert.equal(clampSeek(100, 30, NaN), 130);                 // unknown duration: no upper clamp
    assert.equal(clampSeek(NaN, 30, 100), 30);
    assert.equal(clampSeek(50, 'abc', 100), 50);
  });
});

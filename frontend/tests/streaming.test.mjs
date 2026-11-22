/**
 * Unit tests for the streaming helpers in frontend/sources.js (Streaming tab:
 * Apple Music / Deezer previews, YouTube Music full songs).
 * Run directly:  node --test frontend/tests/
 * Or via pytest: backend/tests/test_frontend.py shells out to node.
 */
import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import {
  SOURCE_KINDS, DECK_LABELS, STREAM_PREVIEW_S, STREAM_PROVIDERS, STREAM_PROVIDER_ORDER,
  describeSource, buildSourcePayload, providerName, streamPlayable, streamMetaLine, streamQueueItem, formatMs,
} from '../sources.js';

const apple = { id: 'ap_1488408555', provider: 'apple', kind: 'stream', title: 'Blinding Lights', artist: 'The Weeknd', album: 'After Hours',
  albumArt: 'https://is1.mzstatic.com/300x300bb.jpg', durationMs: 200040, explicit: false, full: false, audioUrl: 'https://audio-ssl.itunes.apple.com/x.m4a' };
const noClip = { ...apple, id: 'ap_2', title: 'Mr. Brightside', artist: 'The Killers', album: 'Hot Fuss', explicit: true, audioUrl: null };
const yt = { id: 'yt_dQw4w9WgXcQ', provider: 'ytmusic', kind: 'stream', title: 'One More Time', artist: 'Daft Punk', album: 'Discovery',
  albumArt: '', durationMs: 320000, explicit: false, full: true, audioUrl: null };

describe('stream source kind', () => {
  test('registered as seekable but not speed-capable', () => {
    const k = SOURCE_KINDS.stream;
    assert.equal(k.live, false);
    assert.equal(k.seekable, true);
    assert.equal(k.speed, false);
    assert.equal(k.noun, 'track');
    assert.equal(k.deckLabel, DECK_LABELS.stream);
    assert.equal(STREAM_PREVIEW_S, 30);
    assert.equal(SOURCE_KINDS.spotify, undefined);
  });

  test('provider table', () => {
    assert.deepEqual(STREAM_PROVIDER_ORDER, ['ytmusic', 'apple', 'deezer']);   // YouTube Music first
    for (const id of STREAM_PROVIDER_ORDER) assert.ok(STREAM_PROVIDERS[id].name && STREAM_PROVIDERS[id].icon, id);
    assert.equal(STREAM_PROVIDERS.ytmusic.full, true);
    assert.equal(STREAM_PROVIDERS.apple.full, false);
    assert.equal(providerName('deezer'), 'Deezer');
    assert.equal(providerName('ytmusic'), 'YouTube Music');
    assert.equal(providerName('nope'), 'Streaming');
  });

  test('streamPlayable: full songs always, previews only with a clip', () => {
    assert.equal(streamPlayable(apple), true);
    assert.equal(streamPlayable(noClip), false);
    assert.equal(streamPlayable(yt), true);
    assert.equal(streamPlayable(null), false);
  });

  test('describeSource', () => {
    const d = describeSource({ type: 'stream', track: apple, context: { type: 'album', name: 'After Hours' } });
    assert.equal(d.title, 'Blinding Lights');
    assert.equal(d.artist, 'The Weeknd');
    assert.equal(d.badge, 'APPLE MUSIC • PREVIEW');
    assert.equal(d.sourceLine, 'After Hours • 3:20 • from album After Hours • Apple Music • preview');
    assert.equal(d.art, apple.albumArt);
    assert.equal(d.icon, '🌐');
    assert.equal(d.deckLabel, 'Deck_1.A // Streaming');
    const f = describeSource({ type: 'stream', track: yt });
    assert.equal(f.badge, 'YOUTUBE MUSIC');
    assert.equal(f.sourceLine, 'Discovery • 5:20 • YouTube Music');   // whole songs carry no extra label
    assert.equal(describeSource({ type: 'stream', track: { id: 'x', provider: 'deezer' } }).artist, 'Deezer');
  });

  test('streamMetaLine ignores search context, shows Explicit', () => {
    assert.equal(streamMetaLine(noClip, { type: 'search', name: 'brightside' }), 'Hot Fuss • 3:20 • Explicit • Apple Music • preview');
    assert.equal(streamMetaLine({ provider: 'ytmusic', full: true }), 'YouTube Music');
  });

  test('formatMs', () => {
    assert.equal(formatMs(200040), '3:20');
    assert.equal(formatMs(0), '0:00');
    assert.equal(formatMs(undefined), '0:00');
  });
});

describe('queue + playback_state', () => {
  test('streamQueueItem keeps the track and context', () => {
    const item = streamQueueItem(yt, { type: 'artist', name: 'Daft Punk' });
    assert.equal(item.kind, 'stream');
    assert.equal(item.id, yt.id);
    assert.equal(item.genre, 'YouTube Music');
    assert.equal(item.track, yt);
    assert.deepEqual(item.context, { type: 'artist', name: 'Daft Punk' });
    assert.equal(streamQueueItem(apple).context, null);
  });

  test('buildSourcePayload: preview length for clips, real length for full songs', () => {
    const p = buildSourcePayload({ type: 'stream', track: apple }, { position: 12.4, duration: null });
    assert.deepEqual(p, {
      type: 'stream', id: apple.id, title: 'Blinding Lights', artist: 'The Weeknd', album: 'After Hours', provider: 'apple', full: false,
      contextType: null, contextName: null, position: 12, duration: 30,
    });
    const f = buildSourcePayload({ type: 'stream', track: yt, context: { type: 'artist', name: 'Daft Punk' } }, { position: 95, duration: 318.6 });
    assert.equal(f.full, true);
    assert.equal(f.duration, 319);
    assert.equal(f.contextType, 'artist');
    assert.equal(buildSourcePayload({ type: 'stream', track: yt }, {}).duration, 320);   // from durationMs before metadata loads
  });
});

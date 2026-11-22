/**
 * Unit tests for the audiobook helpers in frontend/sources.js.
 * Run directly:  node --test frontend/tests/
 * Or via pytest: backend/tests/test_frontend.py shells out to node.
 */
import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import {
  SOURCE_KINDS, DECK_LABELS, BOOK_PROGRESS_SAVE_MS,
  describeSource, buildSourcePayload, chapterMetaLine, isGenericChapterTitle, bookProgressLabel,
  resumePointFor, nextChapter, previousChapter, chapterQueueItem, filterAudiobooks, sortAudiobooks,
} from '../sources.js';

const chapters = [
  { id: 'ch_1', index: 1, title: 'An Unexpected Party', duration: 2700 },
  { id: 'ch_2', index: 2, title: 'Roast Mutton', duration: 2520 },
  { id: 'ch_3', index: 3, title: 'Chapter 03', duration: 3000 },
];
const book = {
  id: 'ab_hobbit', title: 'The Hobbit', author: 'J.R.R. Tolkien', narrator: 'Andy Serkis',
  chapterCount: 3, duration: 8220, chapters,
  progress: { chapterId: 'ch_2', chapterIndex: 2, position: 600, finished: false, listened: 3300, fraction: 0.401 },
};

describe('audiobook source kind', () => {
  test('is registered as a seekable, speed-capable, non-live source', () => {
    const k = SOURCE_KINDS.audiobook;
    assert.equal(k.live, false);
    assert.equal(k.seekable, true);
    assert.equal(k.speed, true);
    assert.equal(k.noun, 'chapter');
    assert.equal(k.deckLabel, DECK_LABELS.audiobook);
    assert.ok(BOOK_PROGRESS_SAVE_MS >= 1000);
  });

  test('describeSource shows book, author/narrator and chapter line', () => {
    const d = describeSource({ type: 'audiobook', book, chapter: chapters[1] });
    assert.equal(d.title, 'The Hobbit');
    assert.equal(d.artist, 'J.R.R. Tolkien • read by Andy Serkis');
    assert.equal(d.sourceLine, 'Chapter 2 of 3 • Roast Mutton • 42 min');
    assert.equal(d.badge, 'AUDIOBOOK');
    assert.equal(d.icon, '📖');
    assert.equal(d.isLive, false);
    assert.equal(d.seekable, true);
    assert.equal(d.art, '/api/audiobooks/ab_hobbit/art');
    assert.equal(d.deckLabel, 'Deck_1.A // Audiobook');
  });

  test('describeSource copes with a bare book (no author, no narrator)', () => {
    const d = describeSource({ type: 'audiobook', book: { id: 'ab_x', title: 'Dune' }, chapter: { id: 'c', index: 1 } });
    assert.equal(d.artist, 'Audiobook');
    assert.equal(d.sourceLine, 'Chapter 1');
  });
});

describe('chapter text helpers', () => {
  test('chapterMetaLine omits generic chapter titles and unknown parts', () => {
    assert.equal(chapterMetaLine(book, chapters[0]), 'Chapter 1 of 3 • An Unexpected Party • 45 min');
    assert.equal(chapterMetaLine(book, chapters[2]), 'Chapter 3 of 3 • 50 min');      // "Chapter 03" adds nothing
    assert.equal(chapterMetaLine({ title: 'Dune' }, { index: 2, title: 'Dune' }), 'Chapter 2');
    assert.equal(chapterMetaLine(null, {}), '');
  });

  test('isGenericChapterTitle', () => {
    assert.equal(isGenericChapterTitle('Chapter 3', 3, 'X'), true);
    assert.equal(isGenericChapterTitle('03', 3, 'X'), true);
    assert.equal(isGenericChapterTitle('Part 3', 3, 'X'), true);
    assert.equal(isGenericChapterTitle('The Hobbit', 3, 'The Hobbit'), true);
    assert.equal(isGenericChapterTitle('', 3, 'X'), true);
    assert.equal(isGenericChapterTitle('Chapter 4', 3, 'X'), false);
    assert.equal(isGenericChapterTitle('Riddles in the Dark', 3, 'X'), false);
  });

  test('bookProgressLabel', () => {
    assert.equal(bookProgressLabel({ ...book, progress: null }), 'Not started');
    assert.equal(bookProgressLabel({ ...book, progress: { chapterId: null, finished: false } }), 'Not started');
    assert.equal(bookProgressLabel({ ...book, progress: { chapterId: 'ch_3', finished: true } }), 'Finished');
    assert.equal(bookProgressLabel(book), 'Chapter 2 of 3 • 40% • 1h 22m left');
    assert.equal(bookProgressLabel({ ...book, duration: null, progress: { chapterId: 'ch_1', chapterIndex: 1, fraction: 0 } }), 'Chapter 1 of 3');
  });
});

describe('chapter navigation', () => {
  test('resumePointFor: remembered chapter + position, else chapter 1', () => {
    assert.deepEqual(resumePointFor(book), { chapter: chapters[1], position: 600 });
    assert.deepEqual(resumePointFor({ ...book, progress: null }), { chapter: chapters[0], position: 0 });
    assert.deepEqual(resumePointFor({ ...book, progress: { chapterId: 'ch_3', finished: true, position: 0 } }), { chapter: chapters[0], position: 0 });
    // remembered chapter no longer exists (rescan): start over rather than crash
    assert.deepEqual(resumePointFor({ ...book, progress: { chapterId: 'gone', position: 50 } }), { chapter: chapters[0], position: 0 });
    assert.equal(resumePointFor({ ...book, chapters: [] }), null);
    assert.equal(resumePointFor(null), null);
  });

  test('nextChapter / previousChapter', () => {
    assert.equal(nextChapter(book, 'ch_1'), chapters[1]);
    assert.equal(nextChapter(book, 'ch_3'), null);
    assert.equal(nextChapter(book, 'nope'), null);
    assert.equal(previousChapter(book, 'ch_2'), chapters[0]);
    assert.equal(previousChapter(book, 'ch_1'), null);
  });

  test('chapterQueueItem carries enough to play later', () => {
    const item = chapterQueueItem(book, chapters[2]);
    assert.equal(item.kind, 'audiobook');
    assert.equal(item.id, 'ch_3');
    assert.equal(item.title, 'Chapter 03');
    assert.equal(item.artist, 'The Hobbit');
    assert.equal(item.book.id, 'ab_hobbit');
    assert.equal(item.book.chapterCount, 3);
    assert.equal(item.chapter, chapters[2]);
  });
});

describe('playback_state payload', () => {
  test('buildSourcePayload for an audiobook reports chapter + position', () => {
    const p = buildSourcePayload({ type: 'audiobook', book, chapter: chapters[1] }, { position: 612.7, duration: 2520.4 });
    assert.deepEqual(p, {
      type: 'audiobook', id: 'ab_hobbit', name: 'The Hobbit', author: 'J.R.R. Tolkien',
      chapterId: 'ch_2', chapterIndex: 2, chapterTitle: 'Roast Mutton', chapterCount: 3,
      position: 613, duration: 2520,
    });
  });

  test('falls back to the chapter tag duration before metadata loads', () => {
    const p = buildSourcePayload({ type: 'audiobook', book, chapter: chapters[0] }, { position: NaN, duration: null });
    assert.equal(p.duration, 2700);
    assert.equal(p.position, null);
  });
});

describe('filterAudiobooks', () => {
  const books = [book, { id: 'ab_dune', title: 'Dune', author: 'Frank Herbert', narrator: '', genre: 'Science Fiction' }];
  test('matches title, author, narrator and genre; empty query returns all', () => {
    assert.equal(filterAudiobooks(books, '').length, 2);
    assert.deepEqual(filterAudiobooks(books, 'hobbit').map(b => b.id), ['ab_hobbit']);
    assert.deepEqual(filterAudiobooks(books, 'herbert').map(b => b.id), ['ab_dune']);
    assert.deepEqual(filterAudiobooks(books, 'serkis').map(b => b.id), ['ab_hobbit']);
    assert.deepEqual(filterAudiobooks(books, 'science').map(b => b.id), ['ab_dune']);
    assert.deepEqual(filterAudiobooks(books, 'zzz'), []);
    assert.deepEqual(filterAudiobooks(null, 'x'), []);
  });
});

describe('sortAudiobooks', () => {
  test('most recently played first (unfinished before finished), then untouched by title', () => {
    const books = [
      { id: 'z', title: 'Zebra', progress: null },
      { id: 'f', title: 'Finished Long Ago', progress: { chapterId: 'c', finished: true, updatedAt: 900 } },
      { id: 'a', title: 'The Apple', progress: null },
      { id: 'r', title: 'Recent Read', progress: { chapterId: 'c', finished: false, updatedAt: 500 } },
      { id: 'n', title: 'Newest Read', progress: { chapterId: 'c', finished: false, updatedAt: 800 } },
    ];
    assert.deepEqual(sortAudiobooks(books).map(b => b.id), ['n', 'r', 'f', 'a', 'z']);
    assert.deepEqual(sortAudiobooks([]), []);
    assert.deepEqual(sortAudiobooks(null), []);
    assert.notEqual(sortAudiobooks(books), books);                  // does not mutate the input
  });
});

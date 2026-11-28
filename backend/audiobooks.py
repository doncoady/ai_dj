"""
AI DJ — Local Audiobooks

Scans AUDIOBOOKS_DIR (from .env, like MUSIC_DIR) into books with ordered
chapters, remembers listening progress server-side (audiobooks.json, so the
DJ can answer "continue my book"), and resolves spoken references
("the Hobbit", "chapter 5", "continue", "start over").

Folder layouts understood
  <root>/Book.m4b                         one file = one book (one chapter)
  <root>/Book/01.mp3 … 12.mp3             folder = book, files = chapters
  <root>/Author/Book/01.mp3 …             author taken from the parent folder
  <root>/Author/Book/CD 1/…, CD 2/…       disc/part sub-folders are merged

Tags (via mutagen, when present) override folder names: album -> book title,
albumartist/artist -> author, composer -> narrator, title -> chapter title,
discnumber/tracknumber -> chapter order.
"""

import re
import json
import time
import hashlib
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

try:
    from backend.textmatch import best_match, normalize
    from backend import scancache
except ImportError:
    from textmatch import best_match, normalize
    import scancache

import os

PROGRESS_FILE = Path(os.getenv("DATA_DIR", Path(__file__).parent)) / "audiobooks.json"

AUDIOBOOK_EXTENSIONS = {".m4b", ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".flac", ".wma", ".wav"}
COVER_FILENAMES = ("cover.jpg", "cover.jpeg", "cover.png", "folder.jpg", "folder.jpeg", "folder.png", "front.jpg")
# Sub-folders that split one book over several discs / parts — merged into the parent.
_PART_DIR_RE = re.compile(r"^(?:cd|disc|disk|part|vol(?:ume)?)[\s_\-]*\d+$", re.IGNORECASE)
# Considered finished when this close to the end of the last chapter.
FINISHED_MARGIN_S = 20

_BOOK_FILLER = {"audiobook", "audiobooks", "audio", "book", "books", "novel", "story", "read", "listen", "listening"}
_CHAPTER_FILLER = {"chapter", "chapters", "part", "section", "track"}

MEDIA_TYPES = {
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".flac": "audio/flac", ".m4a": "audio/mp4",
    ".m4b": "audio/mp4", ".ogg": "audio/ogg", ".opus": "audio/ogg", ".aac": "audio/aac", ".wma": "audio/x-ms-wma",
}


def book_id_for(rel_path: str) -> str:
    return "ab_" + hashlib.md5(rel_path.replace("\\", "/").encode("utf-8")).hexdigest()[:10]


def chapter_id_for(rel_path: str) -> str:
    return "ch_" + hashlib.md5(rel_path.replace("\\", "/").encode("utf-8")).hexdigest()[:10]


# ─── Tag reading ──────────────────────────────────────────────────────────────

def _first(tags, key: str) -> str:
    try:
        val = tags.get(key)
    except Exception:
        return ""
    if not val:
        return ""
    if isinstance(val, (list, tuple)):
        val = val[0] if val else ""
    return str(val).strip()


def _int_prefix(text: str) -> Optional[int]:
    """'3' -> 3, '3/12' -> 3, '' -> None."""
    m = re.match(r"\s*(\d+)", text or "")
    return int(m.group(1)) if m else None


def read_tags(path: Path) -> Dict:
    """Metadata mutagen can give us; every field is optional. Never raises."""
    out = {"title": "", "artist": "", "album": "", "albumartist": "", "composer": "", "genre": "",
           "tracknumber": None, "discnumber": None, "duration": None}
    try:
        from mutagen import File as MutagenFile
        audio = MutagenFile(path, easy=True)
    except Exception:
        return out
    if audio is None:
        return out
    try:
        if getattr(audio, "info", None) is not None and getattr(audio.info, "length", None):
            out["duration"] = int(round(float(audio.info.length)))
    except Exception:
        pass
    tags = getattr(audio, "tags", None)
    if tags:
        for key in ("title", "artist", "album", "albumartist", "composer", "genre"):
            out[key] = _first(tags, key)
        out["tracknumber"] = _int_prefix(_first(tags, "tracknumber"))
        out["discnumber"] = _int_prefix(_first(tags, "discnumber"))
    return out


# ─── Scanner ──────────────────────────────────────────────────────────────────

def _natural_key(text: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", text)]


def _book_dir_for(file_rel: Path) -> Optional[Path]:
    """The folder that represents the book for a file, or None for a loose
    file directly in the root (which is a book of its own)."""
    parent = file_rel.parent
    if parent == Path("."):
        return None
    # "CD 1" / "Part 2" folders belong to the folder above them.
    while parent.parent != Path(".") and _PART_DIR_RE.match(parent.name or ""):
        parent = parent.parent
    return parent


def _clean_title(text: str) -> str:
    t = re.sub(r"[_]+", " ", text or "")
    t = re.sub(r"\s+", " ", t).strip()
    return t


def scan_audiobooks(root: Optional[Path], read_tags_fn: Callable[[Path], Dict] = read_tags, use_cache: bool = True) -> List[Dict]:
    """Scan a directory tree into books. Returns [] when the directory is
    unset or missing (the feature then shows as not configured). Tags are
    served from the on-disk cache when a file's size/mtime are unchanged."""
    if not root:
        return []
    root = Path(root)
    if not root.exists() or not root.is_dir():
        print(f"[Audiobooks] Directory not found: {root}")
        return []

    cache = scancache.TagCache("audiobooks") if use_cache else None
    groups: Dict[str, List[Tuple[Path, Dict]]] = {}
    for f in sorted(root.rglob("*")):
        if not f.is_file() or f.suffix.lower() not in AUDIOBOOK_EXTENSIONS:
            continue
        rel = f.relative_to(root)
        book_dir = _book_dir_for(rel)
        key = str(book_dir).replace("\\", "/") if book_dir is not None else str(rel).replace("\\", "/")
        rel_key = str(rel).replace("\\", "/")
        tags = cache.get(f, rel_key, read_tags_fn) if cache else read_tags_fn(f)
        groups.setdefault(key, []).append((rel, tags))
    if cache:
        cache.save()

    books: List[Dict] = []
    for key, files in groups.items():
        first_rel, first_tags = files[0]
        is_single = len(files) == 1 and first_rel.parent == Path(".")
        book_dir = None if is_single else Path(key)

        # Title: album tag (shared by the chapters) > folder name > file stem.
        albums = [t.get("album") for _, t in files if t.get("album")]
        if albums:
            title = max(set(albums), key=albums.count)
        elif book_dir is not None:
            title = book_dir.name
        else:
            title = first_tags.get("title") or first_rel.stem
        title = _clean_title(title)

        authors = [t.get("albumartist") or t.get("artist") for _, t in files if (t.get("albumartist") or t.get("artist"))]
        if authors:
            author = max(set(authors), key=authors.count)
        elif book_dir is not None and book_dir.parent != Path("."):
            author = book_dir.parent.name            # <root>/Author/Book/…
        else:
            author = "Unknown Author"
        author = _clean_title(author)

        narrators = [t.get("composer") for _, t in files if t.get("composer")]
        narrator = _clean_title(max(set(narrators), key=narrators.count)) if narrators else ""
        genres = [t.get("genre") for _, t in files if t.get("genre")]
        genre = _clean_title(max(set(genres), key=genres.count)) if genres else "Audiobook"

        # Chapter order: disc, track, then natural filename order.
        def order(item):
            rel, tags = item
            return (tags.get("discnumber") or 0, tags.get("tracknumber") or 0, _natural_key(str(rel)))
        files.sort(key=order)

        chapters = []
        chapter_titles = [t.get("title") or "" for _, t in files]
        # Identical titles on every file ("The Hobbit" × 12) carry no information.
        titles_useful = len(set(chapter_titles)) == len(chapter_titles) and all(chapter_titles)
        for i, (rel, tags) in enumerate(files):
            ch_title = _clean_title(tags.get("title")) if titles_useful else ""
            if not ch_title or (len(files) > 1 and ch_title.lower() == title.lower()):
                ch_title = _clean_title(rel.stem) if len(files) > 1 else title
            chapters.append({
                "id": chapter_id_for(str(rel)),
                "index": i + 1,
                "title": ch_title[:200],
                "filename": str(rel).replace("\\", "/"),
                "duration": tags.get("duration"),
            })

        cover = ""
        if book_dir is not None:
            for name in COVER_FILENAMES:
                candidate = root / book_dir / name
                if candidate.exists():
                    cover = str((book_dir / name)).replace("\\", "/")
                    break

        durations = [c["duration"] for c in chapters if c.get("duration")]
        books.append({
            "id": book_id_for(key),
            "title": title[:200],
            "author": author[:120],
            "narrator": narrator[:120],
            "genre": genre[:60],
            "path": key,
            "cover": cover,
            "chapters": chapters,
            "chapterCount": len(chapters),
            "duration": sum(durations) if durations and len(durations) == len(chapters) else (sum(durations) or None),
        })

    books.sort(key=lambda b: (_title_sort_key(b["title"]), _natural_key(b["author"])))
    return books


def _title_sort_key(title: str):
    """Natural order ignoring a leading article ("The Hobbit" sorts under H)."""
    t = re.sub(r"^(?:the|a|an)\s+", "", (title or "").strip(), flags=re.IGNORECASE)
    return _natural_key(t)


# ─── Progress persistence ─────────────────────────────────────────────────────

def load_progress() -> Dict[str, Dict]:
    if not PROGRESS_FILE.exists():
        return {}
    try:
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception as e:
        print(f"[Audiobooks] Error loading progress: {e}")
        return {}


def save_progress_file(progress: Dict[str, Dict]) -> bool:
    try:
        tmp = PROGRESS_FILE.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(progress, f, indent=2, ensure_ascii=False)
        tmp.replace(PROGRESS_FILE)
        return True
    except Exception as e:
        print(f"[Audiobooks] Error saving progress: {e}")
        return False


def get_progress(book_id: str) -> Optional[Dict]:
    return load_progress().get(book_id)


def update_progress(book: Dict, chapter_id: str, position: float, duration: Optional[float] = None) -> Dict:
    """Remember where the listener is. Positions inside the last chapter's
    final seconds mark the book finished (so "continue" starts over)."""
    chapters = book.get("chapters") or []
    ch = next((c for c in chapters if c["id"] == chapter_id), None)
    if ch is None:
        raise ValueError("Unknown chapter for this book")
    pos = max(0.0, float(position or 0))
    dur = float(duration) if duration else float(ch.get("duration") or 0)
    # The margin never exceeds half the chapter, so very short files still work.
    margin = min(FINISHED_MARGIN_S, dur / 2) if dur > 0 else 0
    finished = bool(ch is chapters[-1] and dur > 0 and pos >= dur - margin)
    entry = {
        "chapterId": ch["id"], "chapterIndex": ch["index"], "position": 0.0 if finished else round(pos, 1),
        "finished": finished, "updatedAt": int(time.time()),
    }
    progress = load_progress()
    progress[book["id"]] = entry
    save_progress_file(progress)
    return entry


def mark_finished(book: Dict) -> Dict:
    chapters = book.get("chapters") or []
    entry = {"chapterId": chapters[-1]["id"] if chapters else None, "chapterIndex": len(chapters),
             "position": 0.0, "finished": True, "updatedAt": int(time.time())}
    progress = load_progress()
    progress[book["id"]] = entry
    save_progress_file(progress)
    return entry


def reset_progress(book_id: str) -> bool:
    progress = load_progress()
    if book_id not in progress:
        return False
    del progress[book_id]
    save_progress_file(progress)
    return True


def listened_seconds(book: Dict, prog: Optional[Dict]) -> float:
    """Seconds of the whole book heard so far (chapters before the current + position)."""
    if not prog:
        return 0.0
    if prog.get("finished"):
        return float(book.get("duration") or 0)
    total = 0.0
    for ch in book.get("chapters") or []:
        if ch["id"] == prog.get("chapterId"):
            return total + float(prog.get("position") or 0)
        total += float(ch.get("duration") or 0)
    return total


def summarize(book: Dict, prog: Optional[Dict] = None) -> Dict:
    """Listing shape for the GUI and the voice tools (no chapter bodies)."""
    heard = listened_seconds(book, prog)
    dur = float(book.get("duration") or 0)
    return {
        "id": book["id"], "title": book["title"], "author": book.get("author", ""), "narrator": book.get("narrator", ""),
        "genre": book.get("genre", ""), "chapterCount": book.get("chapterCount", len(book.get("chapters") or [])),
        "duration": book.get("duration"), "hasCover": bool(book.get("cover")),
        "progress": {
            "chapterId": prog.get("chapterId") if prog else None,
            "chapterIndex": prog.get("chapterIndex") if prog else None,
            "position": prog.get("position", 0) if prog else 0,
            "finished": bool(prog and prog.get("finished")),
            "listened": round(heard),
            "fraction": round(min(1.0, heard / dur), 3) if dur > 0 else 0,
            "updatedAt": prog.get("updatedAt") if prog else None,
        },
    }


def with_chapters(book: Dict, prog: Optional[Dict] = None) -> Dict:
    return {**summarize(book, prog), "chapters": [dict(c) for c in book.get("chapters") or []]}


# ─── Spoken-name resolution ───────────────────────────────────────────────────

def find_audiobook(query: str, books: List[Dict], progress: Optional[Dict[str, Dict]] = None) -> Optional[Dict]:
    progress = progress or {}
    return best_match(
        query, books,
        fields=lambda b: [(b.get("title", ""), 1.0), (b.get("author", ""), 0.7), (b.get("narrator", ""), 0.5)],
        filler=_BOOK_FILLER,
        tiebreak=lambda b: float((progress.get(b["id"]) or {}).get("updatedAt") or 0),
    )


def most_recent_book(books: List[Dict], progress: Dict[str, Dict]) -> Optional[Dict]:
    """The book the listener touched last (unfinished books win), else None."""
    candidates = [(progress[b["id"]], b) for b in books if b["id"] in progress]
    if not candidates:
        return None
    unfinished = [(p, b) for p, b in candidates if not p.get("finished")]
    pool = unfinished or candidates
    return max(pool, key=lambda pb: int(pb[0].get("updatedAt") or 0))[1]


_CONTINUE_RE = re.compile(
    r"^\s*(?:the\s+|my\s+)?(?:continue|resume|keep\s+going|where\s+i\s+(?:left\s+off|was|stopped)|pick\s+up|carry\s+on|current|latest|)"
    r"\s*(?:listening|reading|the\s+book|my\s+book|it|chapter|book)?\s*$", re.I)
_RESTART_RE = re.compile(r"\b(beginning|start\s+over|restart|from\s+the\s+(?:top|start)|first\s+chapter|chapter\s+(?:one|1)\b|opening)\b", re.I)
_LAST_RE = re.compile(r"\b(?:last|final|ending|closing)\s+(?:chapter|part|one)\b", re.I)
_NEXT_RE = re.compile(r"^\s*(?:the\s+)?(?:next|following)(?:\s+(?:one|chapter|part))?\s*$", re.I)
_PREV_RE = re.compile(r"^\s*(?:the\s+)?(?:previous|prior|preceding)\s+(?:one|chapter|part)\s*$|^\s*(?:go\s+)?back\s+(?:a|one)\s+chapter\s*$", re.I)
_NUMBER_RE = re.compile(r"(?:\bchapter|\bpart|\bsection|\btrack|#|\bnumber|\bno\.?)\s*(\d{1,4})\b|^\s*(\d{1,4})\s*$", re.I)
_WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19, "twenty": 20,
}


def resolve_chapter(book: Dict, spoken: str, progress: Optional[Dict] = None,
                    current_chapter_id: Optional[str] = None) -> Tuple[Optional[Dict], float, Optional[str]]:
    """Map a spoken chapter reference to (chapter, start_position, error).

      '' / 'continue' / 'where I left off' -> remembered chapter + position
                                              (first chapter when new or finished)
      'from the beginning' / 'start over'  -> chapter 1 at 0:00
      'chapter 5' / 'part five' / '5'      -> that chapter
      'next' / 'previous'                  -> relative to the chapter playing now
                                              (or the remembered one)
      'last chapter'                       -> final chapter
      anything else                        -> chapter title keywords
    """
    chapters: List[Dict] = book.get("chapters") or []
    if not chapters:
        return None, 0.0, f"'{book.get('title')}' has no playable chapters."
    s = (spoken or "").strip()

    if _RESTART_RE.search(s):
        return chapters[0], 0.0, None
    if _LAST_RE.search(s):
        return chapters[-1], 0.0, None

    anchor_id = current_chapter_id or (progress or {}).get("chapterId")
    if _NEXT_RE.match(s) or _PREV_RE.match(s):
        idx = next((i for i, c in enumerate(chapters) if c["id"] == anchor_id), None) if anchor_id else None
        if idx is None:
            # Nothing to be relative to: "next" from scratch means the first chapter.
            return chapters[0], 0.0, None
        target = idx + 1 if _NEXT_RE.match(s) else idx - 1
        if 0 <= target < len(chapters):
            return chapters[target], 0.0, None
        return None, 0.0, ("That is already the last chapter." if target >= len(chapters) else "That is already the first chapter.")

    if _CONTINUE_RE.match(s):
        if progress and not progress.get("finished"):
            ch = next((c for c in chapters if c["id"] == progress.get("chapterId")), None)
            if ch:
                return ch, float(progress.get("position") or 0), None
        return chapters[0], 0.0, None

    m = _NUMBER_RE.search(s)
    n = None
    if m:
        n = int(m.group(1) or m.group(2))
    else:
        words = re.findall(r"[a-z]+", s.lower())
        spelled = [w for w in words if w in _WORD_NUMBERS]
        if spelled and all(w in _WORD_NUMBERS or w in _CHAPTER_FILLER or w in {"the", "to", "go", "play", "skip"} for w in words):
            n = _WORD_NUMBERS[spelled[0]]
    if n is not None:
        ch = next((c for c in chapters if c["index"] == n), None)
        if ch is None:
            ch = next((c for c in chapters if re.search(rf"\b{n}\b", c.get("title", ""))), None)
        if ch is None:
            return None, 0.0, f"'{book.get('title')}' has {len(chapters)} chapters; there is no chapter {n}."
        return ch, 0.0, None

    ch = best_match(s, chapters, fields=lambda c: [(c.get("title", ""), 1.0)], filler=_CHAPTER_FILLER, min_score=15.0)
    if ch:
        return ch, 0.0, None
    return None, 0.0, f"No chapter of '{book.get('title')}' matches '{spoken}'. It has {len(chapters)} chapters."


def format_duration(seconds: Optional[float]) -> str:
    if not seconds or seconds <= 0:
        return "unknown length"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m"
    if m:
        return f"{m} min"
    return f"{s} sec"


def chapter_label(book: Dict, ch: Dict) -> str:
    total = book.get("chapterCount") or len(book.get("chapters") or [])
    label = f"chapter {ch.get('index')} of {total}"
    if ch.get("title") and ch["title"].lower() != book.get("title", "").lower() and not re.fullmatch(r"(?:chapter|part|track)?\s*0*%d" % (ch.get("index") or 0), ch["title"].strip().lower()):
        label += f" ('{ch['title']}')"
    if ch.get("duration"):
        label += f" [{format_duration(ch['duration'])}]"
    return label


def progress_label(book: Dict, prog: Optional[Dict]) -> str:
    """Spoken status: 'not started', 'finished', or how far along."""
    if not prog:
        return "not started yet"
    if prog.get("finished"):
        return "finished"
    heard = listened_seconds(book, prog)
    dur = float(book.get("duration") or 0)
    pct = f", {round(100 * heard / dur)}% through" if dur > 0 else ""
    ch = next((c for c in book.get("chapters") or [] if c["id"] == prog.get("chapterId")), None)
    where = f"chapter {ch['index']} of {len(book.get('chapters') or [])}" if ch else "an unknown chapter"
    pos = float(prog.get("position") or 0)
    from_start = f" at {int(pos // 60)}:{int(pos % 60):02d} into it" if pos >= 60 else ""
    return f"in progress: {where}{from_start}{pct}"


def book_label(book: Dict) -> str:
    bits = [f"'{book.get('title')}'"]
    if book.get("author") and book["author"] != "Unknown Author":
        bits.append(f"by {book['author']}")
    if book.get("narrator"):
        bits.append(f"read by {book['narrator']}")
    return " ".join(bits)


def normalize_title(text: str) -> str:
    return normalize(text, _BOOK_FILLER)

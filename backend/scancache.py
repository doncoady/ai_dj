"""
AI DJ — On-disk cache for file metadata read during library scans.

Reading tags with mutagen costs a few milliseconds per file; on a library of
thousands of tracks (or big .m4b audiobooks on a network drive) that made
every start-up take a long time. The cache remembers each file's tags keyed
by its relative path and validated by size + mtime, so a restart only reads
files that are new or changed.

Cache files live next to the other JSON state (git-ignored):
  backend/library_cache.json, backend/audiobooks_cache.json
"""

import json
import time
from pathlib import Path
from typing import Callable, Dict, Optional

CACHE_DIR = Path(__file__).parent
CACHE_VERSION = 1


def cache_path(name: str) -> Path:
    return CACHE_DIR / f"{name}_cache.json"


def load(name: str) -> Dict[str, Dict]:
    path = cache_path(name)
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and data.get("version") == CACHE_VERSION and isinstance(data.get("files"), dict):
            return data["files"]
    except Exception as e:
        print(f"[Cache] Ignoring unreadable {path.name}: {e}")
    return {}


def save(name: str, files: Dict[str, Dict]) -> bool:
    path = cache_path(name)
    try:
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"version": CACHE_VERSION, "savedAt": int(time.time()), "files": files}, f, ensure_ascii=False)
        tmp.replace(path)
        return True
    except Exception as e:
        print(f"[Cache] Could not write {path.name}: {e}")
        return False


def clear(name: str) -> bool:
    path = cache_path(name)
    try:
        if path.exists():
            path.unlink()
            return True
    except Exception as e:
        print(f"[Cache] Could not remove {path.name}: {e}")
    return False


class TagCache:
    """Wraps a `read(path) -> dict` function with size/mtime validation.

        cache = TagCache("music")
        meta = cache.get(path, rel_key, read_fn)     # cached or freshly read
        cache.save()                                 # drops entries not seen this run
    """

    def __init__(self, name: str):
        self.name = name
        self._old = load(name)
        self._new: Dict[str, Dict] = {}
        self.hits = 0
        self.misses = 0

    def get(self, path: Path, key: str, read_fn: Callable[[Path], Dict]) -> Dict:
        try:
            st = path.stat()
            sig = (int(st.st_mtime), st.st_size)
        except OSError:
            sig = None
        entry = self._old.get(key)
        if sig is not None and entry and entry.get("mtime") == sig[0] and entry.get("size") == sig[1] and isinstance(entry.get("meta"), dict):
            self.hits += 1
            self._new[key] = entry
            return entry["meta"]
        self.misses += 1
        meta = read_fn(path) or {}
        if sig is not None:
            self._new[key] = {"mtime": sig[0], "size": sig[1], "meta": meta}
        return meta

    def save(self) -> bool:
        # Only persist when something changed (or files disappeared) — keeps
        # restarts read-only when the library is unchanged.
        if self.misses == 0 and set(self._new) == set(self._old):
            return True
        return save(self.name, self._new)

    @property
    def stats(self) -> str:
        return f"{self.hits} cached, {self.misses} read"

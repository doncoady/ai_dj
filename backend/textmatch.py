"""
AI DJ — Spoken-name matching shared by the radio and podcast sources.

Voice requests arrive loosely worded ("put on that ambient soma station",
"play the latest Daily"), so stored names are matched the same way
playlists.py matches playlist names: normalise both sides, strip filler
words, then rank exact > contains > contained-in > shared words.
"""

import re
from typing import Callable, Iterable, Optional, Sequence, Tuple, TypeVar

T = TypeVar("T")

# Words that surround a name in speech but are not part of it.
COMMON_FILLER = frozenset({
    "the", "a", "an", "my", "some", "please", "play", "put", "on", "to", "up",
    "tune", "in", "into", "that", "this", "one", "of", "from", "for", "me",
})


def normalize(text: str, filler: Iterable[str] = ()) -> str:
    """Lowercase, drop apostrophes/punctuation and filler words.

    Falls back to the bare lowercased string when nothing meaningful is left
    (so a station literally called "The One" still normalises to something).
    """
    if not text:
        return ""
    s = text.lower().strip()
    s = re.sub(r"['’`]", "", s)
    s = re.sub(r"[^\w\s&+]", " ", s)
    drop = set(COMMON_FILLER) | set(filler)
    tokens = [t for t in s.split() if t and t not in drop]
    if tokens:
        return " ".join(tokens)
    return re.sub(r"\s+", " ", s).strip()


def tier_score(query_norm: str, candidate_norm: str) -> float:
    """0 for no relation, else 40..100 (higher is better)."""
    if not query_norm or not candidate_norm:
        return 0.0
    if query_norm == candidate_norm:
        return 100.0
    if query_norm in candidate_norm:
        return 80.0 - min(len(candidate_norm) - len(query_norm), 50) * 0.1
    if candidate_norm in query_norm:
        return 70.0 - min(len(query_norm) - len(candidate_norm), 50) * 0.1
    q_tokens = set(query_norm.split())
    c_tokens = set(candidate_norm.split())
    common = q_tokens & c_tokens
    if not common:
        return 0.0
    return 40.0 * len(common) / max(len(q_tokens), len(c_tokens))


def best_match(
    query: str,
    items: Sequence[T],
    fields: Callable[[T], Sequence[Tuple[str, float]]],
    filler: Iterable[str] = (),
    min_score: float = 10.0,
    tiebreak: Optional[Callable[[T], float]] = None,
) -> Optional[T]:
    """Pick the item whose weighted fields best match the spoken `query`.

    `fields(item)` returns (text, weight) pairs, e.g. [(name, 1.0), (genre, 0.8)].
    The item's score is the best weighted tier score over its fields. Ties go
    to the item with the larger `tiebreak(item)` value (e.g. most recently
    used) when given.
    """
    q = normalize(query, filler)
    if not q or not items:
        return None
    best: Optional[T] = None
    best_score = 0.0
    best_tb = float("-inf")
    for item in items:
        score = 0.0
        for text, weight in fields(item):
            s = tier_score(q, normalize(text or "", filler)) * weight
            if s > score:
                score = s
        if score < min_score:
            continue
        tb = tiebreak(item) if tiebreak else 0.0
        if score > best_score or (score == best_score and tb > best_tb):
            best, best_score, best_tb = item, score, tb
    return best

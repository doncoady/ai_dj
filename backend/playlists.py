"""
AI DJ — Playlist Management & Dynamic Playlist Generation Engine
Handles persistent playlists (JSON-backed) and intelligent AI DJ playlist curation.
"""

import os
import json
import re
import time
import random
import hashlib
from pathlib import Path
from typing import Optional, List, Dict, Tuple

import os

PLAYLISTS_FILE = Path(os.getenv("DATA_DIR", Path(__file__).parent)) / "playlists.json"

# Curated eras & keywords for intelligent decade/vibe matching
DECADE_MAPPINGS = {
    "80s": {
        "years": range(1980, 1990),
        "patterns": [r"\b80['’]?s\b", r"\beighties\b", r"\b198\d\b"],
        "genres": ["80", "synth", "new wave", "hair metal", "retro", "post-punk", "disco"],
        "artists": [
            "michael jackson", "madonna", "prince", "whitney houston", "bon jovi",
            "def leppard", "duran duran", "wham", "george michael", "cyndi lauper",
            "journey", "foreigner", "toto", "bryan adams", "phil collins", "genesis",
            "the police", "queen", "rick astley", "tears for fears", "a-ha", "depeche mode",
            "new order", "van halen", "ac/dc", "guns n' roses", "motley crue", "poison",
            "aldo nova", "bad company", "europe", "survivor", "simple minds", "billy idol",
            "kenneth 'babyface' edmonds", "lionel richie", "steve winwood", "chaka khan",
            "hall & oates", "fleetwood mac", "huey lewis", "kenny loggins", "starship",
            "bruce springsteen", "billy joel", "dire straits", "u2", "the cure", "inxs",
            "chicago", "poison", "scorpions", "iron maiden", "metallica", "ozzy osbourne"
        ],
        "default_title": "AI Mix: 80's Rewind"
    },
    "70s": {
        "years": range(1970, 1980),
        "patterns": [r"\b70['’]?s\b", r"\bseventies\b", r"\b197\d\b"],
        "genres": ["70", "disco", "classic rock", "funk", "glam", "prog rock", "oldies"],
        "artists": [
            "led zeppelin", "pink floyd", "fleetwood mac", "queen", "abba", "bee gees",
            "elton john", "david bowie", "eagles", "aerosmith", "bad company", "badfinger",
            "stevie wonder", "earth wind & fire", "boston", "kansas", "kiss", "lynyrd skynyrd",
            "deep purple", "black sabbath", "the who", "rod stewart", "al green", "marvin gaye"
        ],
        "default_title": "AI Mix: 70's Classics"
    },
    "90s": {
        "years": range(1990, 2000),
        "patterns": [r"\b90['’]?s\b", r"\bnineties\b", r"\b199\d\b"],
        "genres": ["90", "grunge", "alternative", "britpop", "eurodance", "boy band", "r&b"],
        "artists": [
            "nirvana", "pearl jam", "oasis", "green day", "backstreet boys", "britney spears",
            "tlc", "red hot chili peppers", "radiohead", "smashing pumpkins", "2pac",
            "notorious b.i.g.", "alanis morissette", "foo fighters", "4 non blondes",
            "soundgarden", "rem", "no doubt", "blink-182", "weezer", "cranberries"
        ],
        "default_title": "AI Mix: 90's Flashback"
    },
    "2000s": {
        "years": range(2000, 2010),
        "patterns": [r"\b00['’]?s\b", r"\b2000s\b", r"\btwo thousands\b", r"\b200\d\b"],
        "genres": ["2000", "emo", "nu metal", "pop punk", "post-grunge", "indie rock"],
        "artists": [
            "linkin park", "coldplay", "eminem", "outkast", "beyonce", "avril lavigne",
            "green day", "the killers", "fall out boy", "my chemical romance", "nickelback",
            "gorillaz", "black eyed peas", "usher", "justin timberlake", "destiny's child"
        ],
        "default_title": "AI Mix: 2000s Anthems"
    },
    "2010s": {
        "years": range(2010, 2020),
        "patterns": [r"\b10['’]?s\b", r"\b2010s\b", r"\b201\d\b"],
        "genres": ["edm", "electropop", "trap", "modern rock", "synthpop"],
        "artists": [
            "avicii", "adele", "awolnation", "daft punk", "calvin harris", "the weeknd",
            "bruno mars", "taylor swift", "drake", "katy perry", "ed sheeran", "imagine dragons"
        ],
        "default_title": "AI Mix: 2010s Hits"
    }
}

THEME_KEYWORDS = {
    "chill": {
        "patterns": [r"\bchill\b", r"\brelax\b", r"\bmellow\b", r"\blofi\b", r"\bambient\b", r"\bacoustic\b", r"\bcalm\b", r"\beasy\b"],
        "genres": ["easy listening", "ambient", "acoustic", "lofi", "chill", "folk", "soft", "blues", "slow"],
        "default_title": "AI Mix: Chill Waves"
    },
    "rock": {
        "patterns": [r"\brock\b", r"\bhard rock\b", r"\bclassic rock\b", r"\bmetal\b", r"\balternative\b", r"\bguitar\b"],
        "genres": ["rock", "classic rock", "hard rock", "metal", "alternative", "punk", "grunge", "indie"],
        "default_title": "AI Mix: Pure Rock"
    },
    "dance": {
        "patterns": [r"\bdance\b", r"\bclub\b", r"\belectronic\b", r"\bdisco\b", r"\bhouse\b", r"\bedm\b", r"\btechno\b", r"\bparty\b"],
        "genres": ["dance", "club", "electronic", "house", "disco", "techno", "edm", "electro"],
        "default_title": "AI Mix: Dance Floor"
    },
    "country": {
        "patterns": [r"\bcountry\b", r"\bbluegrass\b", r"\bamericana\b", r"\bnashville\b"],
        "genres": ["country", "americana", "bluegrass", "folk"],
        "default_title": "AI Mix: Country Roads"
    },
    "pop": {
        "patterns": [r"\bpop\b", r"\bhits\b", r"\bchart\b", r"\bradio\b"],
        "genres": ["pop", "general pop", "dance-pop", "hit single", "chart"],
        "default_title": "AI Mix: Pop Hits"
    },
    "love": {
        "patterns": [r"\blove\b", r"\bromantic\b", r"\bballads\b", r"\bslow dance\b"],
        "genres": ["love", "ballad", "slow", "easy listening", "r&b", "soul"],
        "default_title": "AI Mix: Romantic Melodies"
    }
}


def _generate_id() -> str:
    """Generate a clean unique playlist ID."""
    t = int(time.time() * 1000)
    rand = hashlib.md5(str(random.random()).encode()).hexdigest()[:6]
    return f"pl_{t}_{rand}"


def load_playlists() -> List[Dict]:
    """Load playlists from JSON storage."""
    if not PLAYLISTS_FILE.exists():
        return []
    try:
        with open(PLAYLISTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
    except Exception as e:
        print(f"[Playlists] Error loading playlists: {e}")
    return []


def save_playlists(playlists: List[Dict]) -> bool:
    """Safely save playlists to JSON file."""
    try:
        tmp_file = PLAYLISTS_FILE.with_suffix(".tmp")
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(playlists, f, indent=2, ensure_ascii=False)
        tmp_file.replace(PLAYLISTS_FILE)
        return True
    except Exception as e:
        print(f"[Playlists] Error saving playlists: {e}")
        return False


def get_all_playlists(tracks_by_id: Optional[Dict[str, dict]] = None) -> List[Dict]:
    """Return all playlists with summary metadata (valid track counts, art id)."""
    playlists = load_playlists()
    result = []
    for pl in playlists:
        track_ids = pl.get("trackIds", [])
        if tracks_by_id:
            valid_ids = [tid for tid in track_ids if tid in tracks_by_id]
            cover_track_id = valid_ids[0] if valid_ids else None
        else:
            valid_ids = track_ids
            cover_track_id = track_ids[0] if track_ids else None

        result.append({
            "id": pl.get("id"),
            "name": pl.get("name"),
            "description": pl.get("description", ""),
            "trackCount": len(valid_ids),
            "coverTrackId": cover_track_id,
            "createdAt": pl.get("createdAt", int(time.time())),
            "updatedAt": pl.get("updatedAt", int(time.time())),
            "isDynamic": pl.get("isDynamic", False),
            "theme": pl.get("theme", "")
        })
    return result


def get_playlist_by_id(playlist_id: str, tracks_by_id: Optional[Dict[str, dict]] = None) -> Optional[Dict]:
    """Get full playlist with resolved track objects if tracks_by_id is provided."""
    playlists = load_playlists()
    for pl in playlists:
        if pl.get("id") == playlist_id:
            resolved = dict(pl)
            track_ids = pl.get("trackIds", [])
            if tracks_by_id is not None:
                resolved_tracks = [tracks_by_id[tid] for tid in track_ids if tid in tracks_by_id]
                resolved["tracks"] = resolved_tracks
                resolved["trackCount"] = len(resolved_tracks)
                resolved["coverTrackId"] = resolved_tracks[0]["id"] if resolved_tracks else None
            else:
                resolved["trackCount"] = len(track_ids)
                resolved["coverTrackId"] = track_ids[0] if track_ids else None
            return resolved
    return None


def create_playlist(
    name: str,
    track_ids: List[str],
    description: str = "",
    is_dynamic: bool = False,
    theme: str = ""
) -> Dict:
    """Create and persist a new playlist."""
    playlists = load_playlists()
    now = int(time.time())
    new_pl = {
        "id": _generate_id(),
        "name": name.strip() or "Untitled Playlist",
        "description": description.strip(),
        "trackIds": list(dict.fromkeys(track_ids)),  # preserve order, eliminate dupes
        "createdAt": now,
        "updatedAt": now,
        "isDynamic": is_dynamic,
        "theme": theme
    }
    playlists.insert(0, new_pl)  # newest first
    save_playlists(playlists)
    return new_pl


def update_playlist(
    playlist_id: str,
    name: Optional[str] = None,
    track_ids: Optional[List[str]] = None,
    description: Optional[str] = None
) -> Optional[Dict]:
    """Update playlist properties."""
    playlists = load_playlists()
    for pl in playlists:
        if pl.get("id") == playlist_id:
            if name is not None:
                pl["name"] = name.strip() or pl["name"]
            if description is not None:
                pl["description"] = description.strip()
            if track_ids is not None:
                pl["trackIds"] = list(dict.fromkeys(track_ids))
            pl["updatedAt"] = int(time.time())
            save_playlists(playlists)
            return pl
    return None


def delete_playlist(playlist_id: str) -> bool:
    """Delete a playlist by ID."""
    playlists = load_playlists()
    initial_len = len(playlists)
    playlists = [p for p in playlists if p.get("id") != playlist_id]
    if len(playlists) < initial_len:
        save_playlists(playlists)
        return True
    return False


def add_tracks_to_playlist(playlist_id: str, new_track_ids: List[str]) -> Optional[Dict]:
    """Append tracks to an existing playlist."""
    playlists = load_playlists()
    for pl in playlists:
        if pl.get("id") == playlist_id:
            current_ids = pl.get("trackIds", [])
            for tid in new_track_ids:
                if tid not in current_ids:
                    current_ids.append(tid)
            pl["trackIds"] = current_ids
            pl["updatedAt"] = int(time.time())
            save_playlists(playlists)
            return pl
    return None


def remove_track_from_playlist(playlist_id: str, track_id: str) -> Optional[Dict]:
    """Remove a track from a playlist."""
    playlists = load_playlists()
    for pl in playlists:
        if pl.get("id") == playlist_id:
            current_ids = pl.get("trackIds", [])
            if track_id in current_ids:
                current_ids.remove(track_id)
                pl["trackIds"] = current_ids
                pl["updatedAt"] = int(time.time())
                save_playlists(playlists)
            return pl
    return None


# ─── Spoken-name Playlist Lookup ───────────────────────────────────────────────

# Words people say around a playlist name that are not part of the name itself
# ("add this to my *workout playlist*", "play the *80s mix*", …). Stripped from
# both the spoken query and the stored names before comparing.
_PLAYLIST_FILLER_WORDS = {
    "playlist", "playlists", "mix", "list", "the", "my", "a", "an", "called",
    "named", "nova", "ai", "dj", "please", "one",
}


def normalize_playlist_name(name: str) -> str:
    """Lowercase, strip punctuation/apostrophes and filler words.

    "AI Mix: 80's Rewind"    -> "80s rewind"
    "my Workout playlist"    -> "workout"
    Falls back to the bare lowercased string when nothing meaningful is left
    (e.g. a playlist literally named "Mix").
    """
    if not name:
        return ""
    s = name.lower().strip()
    s = re.sub(r"['’`]", "", s)            # 80's -> 80s
    s = re.sub(r"[^\w\s&]", " ", s)        # punctuation -> space
    tokens = [t for t in s.split() if t and t not in _PLAYLIST_FILLER_WORDS]
    if tokens:
        return " ".join(tokens)
    return re.sub(r"\s+", " ", s).strip()


def find_playlist_by_name(name: str, playlists: Optional[List[Dict]] = None) -> Optional[Dict]:
    """Best-effort match of a *spoken* playlist name to a stored playlist.

    Ranking: exact (normalized) name > query contained in name > name contained
    in query > shared words. Ties prefer hand-made playlists over AI mixes and
    tighter (shorter) names. Returns the raw stored playlist dict or None.
    """
    if playlists is None:
        playlists = load_playlists()
    q = normalize_playlist_name(name)
    if not q or not playlists:
        return None
    q_tokens = set(q.split())

    best: Optional[Dict] = None
    best_score = 0.0
    for pl in playlists:
        raw = (pl.get("name") or "").lower().strip()
        n = normalize_playlist_name(raw)
        if not n:
            continue
        if q == n or q == raw:
            score = 100.0
        elif q in n or q in raw:
            score = 80.0 - min(len(n) - len(q), 50) * 0.1
        elif n in q:
            score = 70.0 - min(len(q) - len(n), 50) * 0.1
        else:
            n_tokens = set(n.split())
            common = q_tokens & n_tokens
            if not common:
                continue
            score = 40.0 * len(common) / max(len(q_tokens), len(n_tokens))
        if score < 100.0 and not pl.get("isDynamic", False):
            score += 5.0  # a user's own playlist beats a same-named AI mix
        # Equal scores (e.g. several "Session Mix (…)" saves): newest wins.
        if score > best_score or (score == best_score and best is not None
                                  and pl.get("updatedAt", 0) > best.get("updatedAt", 0)):
            best, best_score = pl, score
    return best


# ─── Dynamic AI Playlist Curation ──────────────────────────────────────────────

def generate_dynamic_playlist(
    query: str,
    library: List[Dict],
    tracks_by_id: Dict[str, Dict],
    custom_name: Optional[str] = None,
    target_count: int = 12
) -> Tuple[Dict, List[Dict]]:
    """
    Intelligently scores and curates a dynamic playlist based on a user prompt,
    such as '80s music', 'chill vibes', 'classic rock mix', or 'Adele'.
    Returns (created_playlist_dict, resolved_track_list).
    """
    if not library:
        return {}, []

    q_lower = query.lower().strip()
    scores: Dict[str, float] = {}

    # Check for decades
    detected_decade = None
    for dec_key, dec_data in DECADE_MAPPINGS.items():
        for pat in dec_data["patterns"]:
            if re.search(pat, q_lower):
                detected_decade = (dec_key, dec_data)
                break
        if detected_decade:
            break

    # Check for theme/mood
    detected_theme = None
    for theme_key, theme_data in THEME_KEYWORDS.items():
        for pat in theme_data["patterns"]:
            if re.search(pat, q_lower):
                detected_theme = (theme_key, theme_data)
                break
        if detected_theme:
            break

    # Score each track
    clean_query_words = [w for w in re.split(r"[\s\-_,.]+", q_lower) if len(w) > 1 and w not in {
        "play", "some", "music", "song", "songs", "tracks", "playlist", "mix", "put", "on", "a", "an", "the", "like"
    }]

    for trk in library:
        tid = trk["id"]
        score = 0.0
        title_l = trk.get("title", "").lower()
        artist_l = trk.get("artist", "").lower()
        album_l = trk.get("album", "").lower()
        genre_l = trk.get("genre", "").lower()
        fn_l = trk.get("filename", "").lower()

        # 1. Decade matching
        if detected_decade:
            dec_key, dec_info = detected_decade
            # Exact decade in genre
            if any(g in genre_l for g in dec_info["genres"]):
                score += 12.0
            # Known artists
            if any(art in artist_l for art in dec_info["artists"]):
                score += 15.0
            # Year in filename or album or title
            year_match = re.search(r"\b(19\d\d|20\d\d)\b", f"{fn_l} {album_l} {title_l}")
            if year_match:
                yr = int(year_match.group(1))
                if yr in dec_info["years"]:
                    score += 18.0
            if any(re.search(p, fn_l) or re.search(p, album_l) or re.search(p, genre_l) for p in dec_info["patterns"]):
                score += 14.0

        # 2. Theme matching
        if detected_theme:
            t_key, t_info = detected_theme
            if any(g in genre_l for g in t_info["genres"]):
                score += 12.0
            if any(re.search(p, f"{title_l} {fn_l} {album_l}") for p in t_info["patterns"]):
                score += 8.0

        # 3. Keyword / Query token matching
        for word in clean_query_words:
            if word in genre_l:
                score += 8.0
            if word in artist_l:
                score += 10.0
            if word in title_l:
                score += 6.0
            if word in album_l or word in fn_l:
                score += 4.0

        if score > 0:
            # Slight random jitter so repeated queries give fresh track orders
            scores[tid] = score + random.uniform(0.1, 1.5)

    # Sort candidates by score
    sorted_candidates = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    selected_tids = []
    artist_counts = {}

    for tid, sc in sorted_candidates:
        trk = tracks_by_id.get(tid)
        if not trk:
            continue
        art = trk.get("artist", "Unknown")
        # Distribute variety: max 2 tracks per artist unless query specifically targeted this artist
        is_artist_target = any(w in art.lower() for w in clean_query_words if len(w) > 3)
        limit = 5 if is_artist_target else 2

        if artist_counts.get(art, 0) < limit:
            selected_tids.append(tid)
            artist_counts[art] = artist_counts.get(art, 0) + 1

        if len(selected_tids) >= target_count:
            break

    # Fallback if few or no matches: pick highest score or diverse random tracks
    if len(selected_tids) < 4:
        for tid, sc in sorted_candidates:
            if tid not in selected_tids:
                selected_tids.append(tid)
            if len(selected_tids) >= min(target_count, 8):
                break

    # If still empty (e.g. obscure query), take a diverse sample from library
    if not selected_tids:
        sample_tracks = random.sample(library, min(len(library), target_count))
        selected_tids = [t["id"] for t in sample_tracks]

    # Resolve track objects
    selected_tracks = [tracks_by_id[tid] for tid in selected_tids if tid in tracks_by_id]

    # Generate title
    if custom_name and custom_name.strip():
        playlist_name = custom_name.strip()
    elif detected_decade:
        playlist_name = detected_decade[1]["default_title"]
    elif detected_theme:
        playlist_name = detected_theme[1]["default_title"]
    else:
        capitalized = " ".join(w.capitalize() for w in clean_query_words)
        playlist_name = f"AI Mix: {capitalized}" if capitalized else "AI Dynamic Mix"

    # Save to persistent playlists
    playlist = create_playlist(
        name=playlist_name,
        track_ids=selected_tids,
        description=f"AI-curated mix for '{query}'",
        is_dynamic=True,
        theme=detected_decade[0] if detected_decade else (detected_theme[0] if detected_theme else query)
    )

    return playlist, selected_tracks

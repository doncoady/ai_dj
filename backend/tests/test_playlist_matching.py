"""Spoken-name → playlist fuzzy matching (playlists.py)."""

from conftest import pl_manager


def _pl(name, is_dynamic=False, **extra):
    return {"id": f"pl_{name}", "name": name, "trackIds": [], "isDynamic": is_dynamic, **extra}


PLAYLISTS = [
    _pl("AI Mix: 80's Rewind", is_dynamic=True),
    _pl("AI Mix: Chill Waves", is_dynamic=True),
    _pl("AI Mix: Pure Rock", is_dynamic=True),
    _pl("AI Mix: Bryan Adams", is_dynamic=True),
    _pl("My Rock Essentials"),
    _pl("Workout"),
    _pl("Mix"),
]


def find(name):
    pl = pl_manager.find_playlist_by_name(name, PLAYLISTS)
    return pl["name"] if pl else None


def test_normalize_strips_filler_and_apostrophes():
    assert pl_manager.normalize_playlist_name("AI Mix: 80's Rewind") == "80s rewind"
    assert pl_manager.normalize_playlist_name("Nova Mix: 80's Rewind") == "80s rewind"
    assert pl_manager.normalize_playlist_name("my Workout playlist") == "workout"
    assert pl_manager.normalize_playlist_name("the 80s mix") == "80s"
    # A name made only of filler words falls back to the bare string
    assert pl_manager.normalize_playlist_name("Mix") == "mix"


def test_exact_and_loose_spoken_forms():
    assert find("Workout") == "Workout"
    assert find("my workout playlist") == "Workout"
    assert find("the 80s mix") == "AI Mix: 80's Rewind"
    assert find("eighties rewind playlist") == "AI Mix: 80's Rewind"   # shared word 'rewind'
    assert find("chill") == "AI Mix: Chill Waves"
    assert find("Bryan Adams") == "AI Mix: Bryan Adams"


def test_user_playlist_beats_ai_mix_on_ambiguous_word():
    # Both "Pure Rock" (AI) and "My Rock Essentials" (user) contain "rock";
    # the user's own playlist should win.
    assert find("my rock playlist") == "My Rock Essentials"
    assert find("rock essentials") == "My Rock Essentials"
    assert find("pure rock") == "AI Mix: Pure Rock"


def test_literal_filler_name_and_no_match():
    assert find("mix") == "Mix"
    assert find("jazz brunch") is None
    assert find("") is None
    assert pl_manager.find_playlist_by_name("workout", []) is None


def test_equal_matches_prefer_most_recently_updated():
    sessions = [
        _pl("Session Mix (Oct 10, 21:00)", updatedAt=100),
        _pl("Session Mix (Oct 12, 18:04)", updatedAt=300),
        _pl("Session Mix (Oct 11, 09:30)", updatedAt=200),
    ]
    assert pl_manager.find_playlist_by_name("session mix", sessions)["updatedAt"] == 300
    assert pl_manager.find_playlist_by_name("the session mix playlist", sessions)["updatedAt"] == 300

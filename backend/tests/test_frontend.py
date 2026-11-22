"""
Frontend checks that need no browser:

1. Contract between backend and frontend — every `command` action the server
   can push has a `case` in app.js, every element id app.js looks up exists in
   index.html, and the protocol versions agree.
2. The pure frontend helpers (frontend/sources.js) run under Node's built-in
   test runner; skipped when node is not installed.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

import main

ROOT = Path(main.__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
APP_JS = (FRONTEND / "app.js").read_text(encoding="utf-8")
INDEX_HTML = (FRONTEND / "index.html").read_text(encoding="utf-8")
MAIN_PY = Path(main.__file__).read_text(encoding="utf-8")


def test_protocol_versions_match():
    m = re.search(r"PROTOCOL_VERSION:\s*(\d+)", APP_JS)
    assert m and int(m.group(1)) == main.PROTOCOL_VERSION


def test_every_server_command_has_a_frontend_handler():
    actions = set(re.findall(r'"action":\s*"([A-Za-z]+)"', MAIN_PY))
    assert {"playRadio", "playPodcastEpisode", "seekRelative", "radioStationsChanged", "podcastsChanged",
            "playAudiobookChapter", "playStreamTracks", "queueStreamTracks"} <= actions
    handled = set(re.findall(r"case '([A-Za-z]+)':", APP_JS))
    missing = actions - handled
    assert not missing, f"app.js has no `case` for server command(s): {sorted(missing)}"


def test_every_dom_id_used_by_app_js_exists_in_index_html():
    used = set(re.findall(r"\$\('([a-z0-9\-]+)'\)", APP_JS))
    present = set(re.findall(r'id="([a-z0-9\-]+)"', INDEX_HTML))
    missing = used - present
    assert not missing, f"index.html lacks element(s): {sorted(missing)}"
    for required in ("tab-radio", "tab-podcasts", "radio-view", "podcasts-view", "stations-list", "podcasts-list",
                     "skip-back-btn", "skip-fwd-btn", "now-playing-source", "add-station-modal", "add-podcast-modal",
                     # V7.0 layout: readout + toggle, fixed thumbnail, episode panel, starter notes, browse chips
                     "ai-state-panel", "live-btn", "now-thumb", "now-thumb-kind", "episode-panel", "episode-meta",
                     "episode-notes", "radio-starter-note", "podcast-starter-note", "station-browse-chips", "podcast-browse-chips",
                     # V7.2 audiobooks + streaming
                     "tab-audiobooks", "tab-streaming", "audiobooks-view", "streaming-view", "audiobooks-list", "streaming-sections",
                     "audiobook-filter", "audiobooks-rescan-btn", "audiobooks-setup-note",
                     "streaming-search-input", "streaming-search-btn", "episode-panel-label", "badge-audiobooks-count", "badge-streaming-count",
                     "library-loading", "deck-tabs", "tabs-prev-btn", "tabs-next-btn"):
        assert required in present, required
    # No trace of the old third-party-branded tab or its embedded player
    assert not [i for i in present if "spotify" in i]
    for required in ("new-playlist-modal", "new-playlist-name-input", "new-playlist-desc-input", "new-playlist-confirm-btn"):
        assert required in present, required
    # Things that were removed on purpose must not linger in either file
    for gone in ("disc-icon", "live-btn-label"):
        assert gone not in present and f"$('{gone}')" not in APP_JS, gone


def _code_only(js: str) -> str:
    """app.js without its block comments (the version history mentions old UI)."""
    return re.sub(r"/\*[\s\S]*?\*/", "", js)


def test_v71_polish_rules():
    code = _code_only(APP_JS)
    # New Playlist is a modal, not a browser prompt()
    assert "prompt(" not in code
    # Finder modals never dismiss on a backdrop click (only the ✕ / Cancel paths)
    assert "e.target === this.dom.addStationModal" not in APP_JS
    assert "e.target === this.dom.addPodcastModal" not in APP_JS
    # Expand/collapse controls are chevrons everywhere; no eye/triangle glyph toggles remain
    for glyph in ("👁", "▴ Hide"):
        assert glyph not in code and glyph not in INDEX_HTML, glyph
    css = (FRONTEND / "style.css").read_text(encoding="utf-8")
    assert 'class="chevron2"' in code and ".chevron2 {" in css and 'class="chevron"' not in code
    # Up Next starts expanded
    assert "this.queueExpanded = true;" in APP_JS
    # Rows select on click and play on ▶ / double-click
    assert "track-play-btn" in APP_JS and "_wireSelectableRows(" in APP_JS and "'dblclick'" in APP_JS
    # Cue buttons for playlists and podcasts; podcast episodes can sit in the queue
    assert "playlist-btn-cue" in APP_JS and "podcast-cue-btn" in APP_JS and "episode-cue-btn" in APP_JS
    assert "kind: 'podcast'" in APP_JS and "_playQueueItem(" in APP_JS


def test_layout_has_topbar_and_two_deck_columns():
    assert 'class="glass topbar"' in INDEX_HTML
    assert 'class="glass now-panel"' in INDEX_HTML and 'class="glass browse-panel"' in INDEX_HTML
    css = (FRONTEND / "style.css").read_text(encoding="utf-8")
    assert ".deck {" in css and "grid-template-columns" in css.split(".deck {", 1)[1][:400]
    assert "@keyframes spin" not in css and ".disc.spinning" not in css          # no spinning record
    assert ".now-thumb" in css and ".episode-panel" in css and ".starter-note" in css


def test_view_registry_covers_every_tab():
    tabs = set(re.findall(r'id="tab-([a-z]+)"', INDEX_HTML))
    registry = re.search(r"get views\(\) \{([\s\S]*?)\n  \}", APP_JS).group(1)
    registered = set(re.findall(r"^\s+([a-z]+):\s*\{\s*tab:", registry, re.MULTILINE))
    assert tabs == registered, (tabs, registered)


def test_frontend_source_module_is_imported_and_served():
    assert (FRONTEND / "sources.js").exists() and not (FRONTEND / "spotify.js").exists()
    assert re.search(r"^import \{[\s\S]*?\} from '\./sources\.js';", APP_JS, re.MULTILINE)
    # served from /static like the other assets
    assert 'src="/static/app.js"' in INDEX_HTML and 'type="module"' in INDEX_HTML


def test_tool_result_texts_mention_source_commands_only_for_registered_tools():
    """Every handler registered for a radio/podcast tool is an async function in main.py."""
    import inspect
    for name in ("playRadio", "listRadioStations", "saveRadioStation", "removeRadioStation", "playPodcast",
                 "listPodcasts", "describePodcast", "subscribeToPodcast", "unsubscribeFromPodcast", "seekRelative",
                 "playAudiobook", "listAudiobooks", "describeAudiobook",
                 "playStream", "queueStream"):
        assert inspect.iscoroutinefunction(main.TOOL_HANDLERS[name]), name
    # …and every declared tool has a handler and vice versa
    declared = {d["name"] for d in main.build_gemini_config()["tools"][0]["function_declarations"]}
    assert declared == set(main.TOOL_HANDLERS), declared ^ set(main.TOOL_HANDLERS)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_frontend_unit_tests_pass_under_node():
    files = sorted(str(p) for p in (FRONTEND / "tests").glob("*.test.mjs"))
    assert files, "no frontend test files found"
    # Newer Node versions default to the "spec" reporter (and Windows consoles
    # decode as cp1252): ask for TAP explicitly and decode as UTF-8 so the
    # summary line is stable everywhere.
    proc = subprocess.run(["node", "--test", "--test-reporter=tap", "--test-reporter-destination=stdout", *files],
                          cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    assert proc.returncode == 0, f"node --test failed:\n{proc.stdout}\n{proc.stderr}"
    assert re.search(r"^# fail 0$", proc.stdout, re.MULTILINE), proc.stdout
    assert re.search(r"^# pass [1-9]\d*$", proc.stdout, re.MULTILINE), proc.stdout


def test_v72_polish_rules():
    code = _code_only(APP_JS)
    css = (FRONTEND / "style.css").read_text(encoding="utf-8")
    # In-app dialogs everywhere: window.confirm only survives as the no-DOM fallback inside showConfirm
    assert code.count("confirm(") == 1 and "window.confirm(" in code and "alert(" not in code.replace("showAlert(", "")
    assert "showConfirm({" in code and "confirm-modal" in INDEX_HTML
    # No modal dismisses on a backdrop click; Esc closes the topmost one
    assert "e.target === this.dom." not in code and "e.key !== 'Escape'" in code
    # Library is virtualised and sortable; track numbers are gone
    assert "vlist-rows" in code and "setSort(" in code and "track-sort-bar" in INDEX_HTML
    assert "track-num" not in code and "String(idx + 1).padStart" not in code
    # Glass blur reduced (the background visualiser itself was removed in V7.7)
    assert "blur(24px)" not in css and "blur(14px)" in css
    # Playback speed control + server command
    assert "speed-select" in INDEX_HTML and "case 'setPlaybackRate':" in code and "setPlaybackSpeed" in MAIN_PY
    # Expand/collapse: double chevrons, animated .open body, toggle is the last action on cards
    assert ".playlist-expanded.open {" in css and "_animateCollapse(" in code
    for block in re.findall(r'<div class="playlist-actions">([\s\S]*?)</div>', code):
        buttons = re.findall(r'<button class="([a-z0-9\- ]+)"', block)
        if any("view-toggle-btn" in b for b in buttons):
            assert "view-toggle-btn" in buttons[-1], buttons
    # Button families: main play is green, remove/danger controls red by default
    assert "#00c853" in css.split(".play-btn {", 1)[1][:320]
    assert ".playlist-btn-subtle.danger {" in css and "#queue-remove-btn {" in css and ".is-stop" in css
    # Tabs render as tabs (baseline + active tab joined to the content)
    tabs_css = css.split(".deck-tab.active {", 1)[1][:300]
    assert "border-bottom-color: transparent" in tabs_css


def test_v73_rules():
    code = _code_only(APP_JS)
    css = (FRONTEND / "style.css").read_text(encoding="utf-8")
    # Playing an unsaved (directory) station saves it first: the relay only serves saved stations
    assert "this.saveStation(station, { silent: true })" in code
    assert 'radio_manager.add_station(top, source="directory")' in MAIN_PY
    # Queue toggle is the last header action (far right)
    actions = re.search(r'<div class="queue-header-actions">([\s\S]*?)</div>', INDEX_HTML).group(1)
    assert re.findall(r'id="([a-z\-]+)"', actions)[-1] == "queue-view-btn"
    # DJ can set the volume; speed is a dropdown
    assert "case 'setVolume':" in code and "setUserVolume(" in code and '"setVolume"' in MAIN_PY
    assert 'id="speed-select"' in INDEX_HTML and "<select" in INDEX_HTML and "speed-btn" not in INDEX_HTML
    # Smooth, JS-driven height animation with an easing curve
    assert "_expandBody(" in code and "_collapseBody(" in code and "cubic-bezier" in css.split(".playlist-expanded {", 1)[1][:600]
    # Sort buttons have tooltips; hierarchical groups exist
    for m in re.finditer(r'<button type="button" class="sort-btn[^"]*" data-sort="[a-z]*"([^>]*)>', INDEX_HTML):
        assert 'title="' in m.group(1)
    assert "track-group" in code and "toggleAllGroups(" in code and "_groupKeyFor(" in code
    # Wider scroll handles
    assert "width: 4px;" not in css.split(".track-list::-webkit-scrollbar {", 1)[1][:40]
    assert "scrollbar-color" in css
    # One-off status reconciles with the queue
    assert "_reconcileActivePlaylist(" in code
    # One "now playing" treatment for rows and cards; one panel accent for all source kinds
    assert ".track-active,\n.playlist-card.active-playlist,\n.source-card.active-source," in css
    assert ".now-thumb.kind-radio.playing" not in css and "rgba(255, 82, 82, 0.1)" not in css.split(".now-playing-source.live", 1)[1][:200]
    # Library redraws when its tab becomes visible
    assert "ResizeObserver" in code and "this.trackRenderer.refresh()" in code
    # Directory search keeps geo-blocked ("broken") stations and labels them
    assert "badge-unverified" in code and '"online"' in (ROOT / "backend" / "radio.py").read_text(encoding="utf-8")


def test_v74_rules():
    code = _code_only(APP_JS)
    # Library groups start collapsed and headers carry play / cue, chevron last
    assert "this.expanded = new Set()" in code and "this.collapsed" not in code
    header = re.search(r"_createGroupElement\(\) \{([\s\S]*?)return el;", code).group(1)
    classes = [c for c in re.findall(r'class="([a-z0-9\- ]+)"', header) if c != "chevron2"]
    assert any("group-play-btn" in c for c in classes) and any("group-cue-btn" in c for c in classes)
    assert "group-toggle" in classes[-1], classes
    assert "playTracks(" in code and "queueTracks(" in code
    # City/region search and the second directory surface in the finder
    assert 'id="station-place-input"' in INDEX_HTML and "badge-provider" in code
    assert "TUNEIN_DIRECTORY" in MAIN_PY and "search_tunein" in (ROOT / "backend" / "radio.py").read_text(encoding="utf-8")
    # Volume rule is a first-class playback instruction, and the tool logs
    assert "7. VOLUME" in MAIN_PY and "[Tool] Volume" in MAIN_PY


def test_v75_audiobook_and_streaming_rules():
    code = _code_only(APP_JS)
    css = (FRONTEND / "style.css").read_text(encoding="utf-8")
    sources_js = (FRONTEND / "sources.js").read_text(encoding="utf-8")
    # Both sources are in the registry with the flags the UI keys off (speed control only for spoken word)
    assert re.search(r"audiobook:\s*\{[^}]*seekable: true[^}]*speed: true", sources_js)
    assert re.search(r"stream:\s*\{[^}]*seekable: true[^}]*speed: false", sources_js)
    assert "kind?.speed" in code and "sourceKind(this.source)?.speed" in code
    # Every queue kind the backend labels is playable from the queue in the browser
    for kind in ("podcast", "stream", "audiobook"):
        assert f"item.kind === '{kind}'" in code, kind
        assert f'e["kind"] == "{kind}"' in MAIN_PY, kind
    # Audiobooks: progress goes to the server (not localStorage), chapters auto-advance, resume point honoured
    assert "/progress`" in code and "_saveBookProgress(" in code and "nextChapter(" in code and "resumePointFor(" in code
    assert "case 'playAudiobookChapter':" in code and "playChapter(" in code
    # Streaming: every track relayed through <audio>, queue-able
    assert "/api/streaming/tracks/${track.id}/audio" in code
    assert "case 'playStreamTracks':" in code and "case 'queueStreamTracks':" in code and "streamQueueItem(" in code
    # Backend: protocol bumped with the new source types; both kinds are seekable on the server too
    assert main.PROTOCOL_VERSION == 5
    assert main.SOURCE_TYPES == {"radio", "podcast", "audiobook", "stream"}
    assert main.SEEKABLE_SOURCE_TYPES == {"podcast", "audiobook", "stream"}
    # .env template documents every new key the server reads; streaming needs none
    env_example = (ROOT / "backend" / ".env.example").read_text(encoding="utf-8")
    assert "AUDIOBOOKS_DIR" in env_example and '"AUDIOBOOKS_DIR"' in MAIN_PY
    # New persisted files are git-ignored
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "backend/audiobooks.json" in gitignore


def test_v76_fix_round_rules():
    """Regressions reported after V7.5: slow first load, hover-only queue
    buttons, audiobook buttons not showing pause, squashed transport buttons,
    streaming unusable without a paid account."""
    code = _code_only(APP_JS)
    css = (FRONTEND / "style.css").read_text(encoding="utf-8")

    # 1. Load time: server serves while scanning (background task + progress), browser polls and shows a message
    assert "asyncio.create_task(_startup_scans())" in MAIN_PY and '"/api/status"' in MAIN_PY and "scan_progress" in MAIN_PY
    assert "TagCache(" in MAIN_PY and (ROOT / "backend" / "scancache.py").exists()
    assert "LIBRARY_LOAD_TIMEOUT_MS" in code and "_renderLibraryLoading(" in code and "res.status === 503" in code
    assert "maxRetries = 5" not in code                                  # no more giving up after 5 tries
    assert "this.socket.connect();" in code.split("await Promise.all([", 1)[0].split("async init() {", 1)[1]   # socket before loads
    # 2. Queue "Play now" is always visible: the rule must outrank the later `.track-play-btn { opacity: 0 }`
    assert ".track-item .queue-item-play-btn," in css
    assert css.index(".track-item .queue-item-play-btn,") < css.index(".track-play-btn {\n  opacity: 0;")
    # 3. Audiobook buttons flip to pause while playing (card + chapter rows), and the tab re-renders on state changes
    assert "_bookPlayLabel(" in code and "'⏸ Pause'" in code and "is-pause" in code and ".playlist-btn-play.is-pause" in css
    assert "btn.textContent !== this._bookPlayLabel(active, started)" in code
    # 4. Transport buttons are fixed squares that never shrink
    tb = css.split(".transport-btn {", 1)[1][:400]
    assert "width: 36px" in tb and "height: 36px" in tb and "flex: 0 0 auto" in tb and "padding: 0" in tb
    pb = css.split(".play-btn {", 1)[1][:200]
    assert "flex: 0 0 auto" in pb and "border-radius: 50%" in pb
    assert "flex-wrap: wrap" in css.split(".transport {", 1)[1][:300]
    # 5. Streaming providers: keyless previews + YouTube Music via third-party tools, all relayed
    streaming_py = (ROOT / "backend" / "streaming.py").read_text(encoding="utf-8")
    for fn in ("search_provider", "search_all", "album_tracks", "resolve_audio", "provider_status"):
        assert f"def {fn}(" in streaming_py, fn
    assert "itunes.apple.com/search" in streaming_py and "api.deezer.com" in streaming_py
    assert "from ytmusicapi import YTMusic" in streaming_py and "import yt_dlp" in streaming_py
    for route in ('"/api/streaming/search"', '"/api/streaming/albums/{album_id}/tracks"', '"/api/streaming/tracks/{track_id}/audio"'):
        assert route in MAIN_PY, route
    assert "_onStreamEnded(" in code and "playStreamAlbum(" in code and "_streamingSection(" in code
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for f in ("backend/library_cache.json", "backend/audiobooks_cache.json"):
        assert f in gitignore, f


def test_v77_fix_round_rules():
    """Collapse-all keeps the viewed group in place; tabs are always fully
    labelled; a streamed clip never hands
    over to the library by itself."""
    code = _code_only(APP_JS)
    css = (FRONTEND / "style.css").read_text(encoding="utf-8")
    assert "_scrollAnchor(" in code and "_restoreScrollAnchor(" in code
    toggle_all = code.split("  toggleAllGroups() {", 1)[1].split("\n  }", 1)[0]
    assert "scrollTop = 0" not in toggle_all and "_scrollAnchor()" in toggle_all   # no more jump to the top
    # The sort bar's delegated handler must ignore the Expand/Collapse-all button (it shares .sort-btn)
    assert "e.target.closest('.sort-btn[data-sort]')" in code
    assert ".deck-tab:not(.active) .deck-tab-label" not in css                     # no icon-only mode
    ended = code.split("_onStreamEnded() {", 1)[1][:600]
    assert "this.queue.length > 0" in ended and "playNextInQueue" in ended and "nextTrack" not in ended


def test_v78_streaming_tab_and_tab_carousel():
    """The third-party-branded tab is gone: a generic Streaming tab with one
    sub-section per provider; the Browse tabs scroll as a carousel inside the
    original page width."""
    code = _code_only(APP_JS)
    css = (FRONTEND / "style.css").read_text(encoding="utf-8")
    for path in (APP_JS, INDEX_HTML, css, MAIN_PY, (FRONTEND / "sources.js").read_text(encoding="utf-8"),
                 (ROOT / "backend" / "streaming.py").read_text(encoding="utf-8"), (ROOT / "backend" / "podcasts.py").read_text(encoding="utf-8"),
                 (ROOT / "README.md").read_text(encoding="utf-8"), (ROOT / "backend" / ".env.example").read_text(encoding="utf-8"),
                 (ROOT / "run.bat").read_text(encoding="utf-8"), (ROOT / "run_tests.bat").read_text(encoding="utf-8")):
        assert "spotify" not in path.lower()
    assert '<span class="deck-tab-label">Streaming</span>' in INDEX_HTML
    # one sub-section per provider, from the server's availability list
    assert "STREAM_PROVIDER_ORDER" in code and "_streamingProviders()" in code and "toggleStreamingSection(" in code
    assert "Not installed" in code and "ytmusicapi" in (ROOT / "backend" / "requirements.txt").read_text(encoding="utf-8")
    assert "yt-dlp" in (ROOT / "backend" / "requirements.txt").read_text(encoding="utf-8")
    # tab carousel: no shrinking, hidden scrollbar, arrows, active tab revealed on switch
    tabs_css = css.split(".deck-tabs {", 1)[1][:500]
    assert "flex-wrap: nowrap" in tabs_css and "overflow-x: auto" in tabs_css and "scrollbar-width: none" in tabs_css
    assert "flex: 0 0 auto" in css.split("Carousel tabs never shrink", 1)[1][:200]
    assert "_revealActiveTab(" in code and "_scrollTabs(" in code and "_updateTabArrows(" in code
    assert "max-width: 1180px" in css.split(".app {", 1)[1][:200]


def test_v79_streaming_polish_rules():
    code = _code_only(APP_JS)
    css = (FRONTEND / "style.css").read_text(encoding="utf-8")
    # "From here" is gone from playlist rows
    assert "From here" not in code and "track-play-from-btn" not in code
    # Browse matches the player's width
    assert "grid-template-columns: minmax(320px, 1fr) minmax(320px, 1fr)" in css
    # YouTube Music leads; labels: "Previews" only on section headers, no "30s"/"Full" on rows
    assert "STREAM_PROVIDER_ORDER = ['ytmusic', 'apple', 'deezer']" in (FRONTEND / "sources.js").read_text(encoding="utf-8")
    assert 'PROVIDER_ORDER = ["ytmusic", "apple", "deezer"]' in (ROOT / "backend" / "streaming.py").read_text(encoding="utf-8")
    assert ">Previews</span>" in code and "'30s previews'" not in code and "'Full songs'" not in code
    assert "text: '30s'" not in code and "text: 'Full'" not in code and "30s PREVIEW" not in (FRONTEND / "sources.js").read_text(encoding="utf-8")
    # "More results" per section, backed by server-side paging
    assert "loadMoreStreaming(" in code and "result.hasMore" in code and "offset=${offset}" in code
    assert "offset: int = 0" in (ROOT / "backend" / "main.py").read_text(encoding="utf-8").split("async def streaming_search", 1)[1][:200]


def test_v80_layout_and_dnd_rules():
    """Music tab label; Playlists moved under the cue; cue auto-sizes; cue ▶
    keeps the rest cued; drag & drop; draggable carousel; newest-episodes
    workflow; relabelled finder buttons; filter icons; audiobook recency sort."""
    code = _code_only(APP_JS)
    css = (FRONTEND / "style.css").read_text(encoding="utf-8")
    present = set(re.findall(r'id="([a-z0-9\-]+)"', INDEX_HTML))
    assert '<span class="deck-tab-label">Music</span>' in INDEX_HTML and "tab-playlists" not in present and "playlists-view" not in present
    for required in ("playlists-section", "playlists-body", "playlists-count", "playlists-view-btn", "playlist-filter", "playlists-list", "new-playlist-btn",
                     "latest-episodes", "latest-episodes-list", "latest-play-btn", "latest-cue-btn", "latest-view-btn"):
        assert required in present, required
    # Playlists section sits inside the player column, after the cue
    now_panel = INDEX_HTML.split('class="glass now-panel"', 1)[1].split("</section>\n\n      <!-- ═══ BROWSE", 1)[0]
    assert 'id="queue-container"' in now_panel and now_panel.index('id="playlists-section"') > now_panel.index('id="queue-container"')
    # Cue auto-sizes (flex, no fixed max-height) and ▶ on a cued row no longer drops the rows above it
    assert "max-height: none;" in css.split(".queue-expanded .track-list {", 1)[1][:120]
    assert ".queue-expanded.open #queue-tracks-container" in css and ".queue-expanded.open {\n  flex: 1 1 auto;" in css
    play_now = code.split("  playQueueItemNow(index) {", 1)[1].split("\n  }", 1)[0]
    assert "splice(0, index)" not in play_now and ">▶ Play</button>" in code and "Play now" not in code.split("queue-item-play-btn")[1][:80]
    # Drag & drop: library rows/groups, streaming rows, playlist rows draggable; cue + playlist cards drop targets; reorder
    for hook in ("onDragStart", "_startTracksDrag(", "_startStreamDrag(", "_makeQueueDropTarget(", "_makePlaylistDropTarget(", "moveQueueItem(", "reorderPlaylistTrack("):
        assert hook in code, hook
    assert "el.draggable = true;" in code and "application/x-aidj-tracks" in code and "application/x-aidj-queue" in code
    # Carousel: smooth + draggable + snapping
    assert "_makeTabsDraggable(" in code and "_snapTabs(" in code and "scroll-snap-type: x proximity" in css and "scroll-behavior: smooth" in css
    # Newest episodes workflow (GUI + voice)
    assert "loadLatestEpisodes(" in code and "playLatestEpisodes(" in code and "case 'queuePodcastEpisodes':" in code
    assert '"/api/podcasts/latest"' in MAIN_PY and "def latest_episodes(" in (ROOT / "backend" / "podcasts.py").read_text(encoding="utf-8")
    # Relabels + icons
    assert ">Find Stations…</button>" in INDEX_HTML and ">Find Podcasts…</button>" in INDEX_HTML
    assert "+ Find Stations" not in INDEX_HTML and "+ Subscribe" not in INDEX_HTML
    assert INDEX_HTML.count('class="filter-icon"') == 5 and INDEX_HTML.count('class="search-icon"') == 1   # only the streaming search keeps 🔍
    # Audiobooks sorted by recency
    assert "sortAudiobooks(" in code and "export function sortAudiobooks(" in (FRONTEND / "sources.js").read_text(encoding="utf-8")


def test_v81_compact_transport_no_visualizer_no_deck_header_clamped_descriptions():
    code = _code_only(APP_JS)
    css = (FRONTEND / "style.css").read_text(encoding="utf-8")
    # background spectrum visualiser and the "Deck_1.A" header are gone
    assert 'id="visualizer"' not in INDEX_HTML and "class Visualizer" not in code and "new Visualizer(" not in code and "#visualizer" not in css
    assert 'id="deck-label-text"' not in INDEX_HTML and "deckLabelText" not in code
    # transport is compact and sized like the rest of the controls
    assert "width: 44px" in css.split(".play-btn {", 1)[1][:60] and "width: 30px" in css.split(".transport-btn.skip-btn {", 1)[1][:60]
    assert "padding: 10px 0;" in css.split(".transport {", 1)[1][:300] and "padding: 1.5rem 0" not in css.split(".transport {", 1)[1][:300]
    # descriptions clamp with a More/Less toggle that appears only on overflow
    assert "_setClampedText(" in code and "el.scrollHeight <= el.clientHeight + 1" in code and ".clampable {" in css and ".clamp-toggle" in css
    assert code.count("this._setClampedText(") >= 2          # podcast blurb + episode notes

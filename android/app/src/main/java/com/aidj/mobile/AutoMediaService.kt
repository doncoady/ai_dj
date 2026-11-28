package com.aidj.mobile

import android.net.Uri
import android.os.Bundle
import androidx.annotation.OptIn
import androidx.media3.common.AudioAttributes
import androidx.media3.common.C
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.session.LibraryResult
import androidx.media3.session.MediaLibraryService
import androidx.media3.session.MediaSession
import com.google.common.collect.ImmutableList
import com.google.common.util.concurrent.Futures
import com.google.common.util.concurrent.ListenableFuture
import com.google.common.util.concurrent.ListeningExecutorService
import com.google.common.util.concurrent.MoreExecutors
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.Executors

@OptIn(UnstableApi::class)
class AutoMediaService : MediaLibraryService() {

    private lateinit var player: ExoPlayer
    private var session: MediaLibrarySession? = null
    private val executor: ListeningExecutorService =
        MoreExecutors.listeningDecorator(Executors.newCachedThreadPool())

    companion object {
        private const val BASE_URL = "http://127.0.0.1:${BackendService.PORT}"
        private const val ROOT_ID = "root"
        private const val CAT_MUSIC = "cat:music"
        private const val CAT_PLAYLISTS = "cat:playlists"
        private const val CAT_RADIO = "cat:radio"
        private const val CAT_PODCASTS = "cat:podcasts"
        private const val CAT_AUDIOBOOKS = "cat:audiobooks"
    }

    override fun onCreate() {
        super.onCreate()
        BackendService.ensureStarted(this)

        player = ExoPlayer.Builder(this)
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(C.USAGE_MEDIA)
                    .setContentType(C.AUDIO_CONTENT_TYPE_MUSIC)
                    .build(),
                true
            )
            .setHandleAudioBecomingNoisy(true)
            .build()

        session = MediaLibrarySession.Builder(this, player, LibraryCallback()).build()
    }

    override fun onGetSession(controllerInfo: MediaSession.ControllerInfo): MediaLibrarySession? {
        return session
    }

    override fun onDestroy() {
        session?.run {
            player.release()
            release()
            session = null
        }
        executor.shutdown()
        super.onDestroy()
    }

    private inner class LibraryCallback : MediaLibrarySession.Callback {

        override fun onGetLibraryRoot(
            session: MediaLibrarySession,
            browser: MediaSession.ControllerInfo,
            params: LibraryParams?
        ): ListenableFuture<LibraryResult<MediaItem>> {
            val root = MediaItem.Builder()
                .setMediaId(ROOT_ID)
                .setMediaMetadata(
                    MediaMetadata.Builder()
                        .setTitle("AI DJ Live")
                        .setIsBrowsable(true)
                        .setIsPlayable(false)
                        .setMediaType(MediaMetadata.MEDIA_TYPE_FOLDER_MIXED)
                        .build()
                )
                .build()
            return Futures.immediateFuture(LibraryResult.ofItem(root, params))
        }

        override fun onGetChildren(
            session: MediaLibrarySession,
            browser: MediaSession.ControllerInfo,
            parentId: String,
            page: Int,
            pageSize: Int,
            params: LibraryParams?
        ): ListenableFuture<LibraryResult<ImmutableList<MediaItem>>> {
            return executor.submit<LibraryResult<ImmutableList<MediaItem>>> {
                BackendService.waitForServer(10_000L)
                val items = when {
                    parentId == ROOT_ID -> buildRootCategories()
                    parentId == CAT_MUSIC -> loadTracks()
                    parentId == CAT_PLAYLISTS -> loadPlaylists()
                    parentId.startsWith("playlist:") -> loadPlaylistTracks(parentId.removePrefix("playlist:"))
                    parentId == CAT_RADIO -> loadRadio()
                    parentId == CAT_PODCASTS -> loadPodcasts()
                    parentId.startsWith("podcast:") -> loadPodcastEpisodes(parentId.removePrefix("podcast:"))
                    parentId == CAT_AUDIOBOOKS -> loadAudiobooks()
                    parentId.startsWith("audiobook:") -> loadAudiobookChapters(parentId.removePrefix("audiobook:"))
                    else -> emptyList()
                }
                LibraryResult.ofItemList(ImmutableList.copyOf(items), params)
            }
        }

        override fun onGetItem(
            session: MediaLibrarySession,
            browser: MediaSession.ControllerInfo,
            mediaId: String
        ): ListenableFuture<LibraryResult<MediaItem>> {
            return executor.submit<LibraryResult<MediaItem>> {
                val item = resolveMediaItem(mediaId)
                if (item != null) {
                    LibraryResult.ofItem(item, null)
                } else {
                    LibraryResult.ofError(LibraryResult.RESULT_ERROR_BAD_VALUE)
                }
            }
        }

        override fun onAddMediaItems(
            mediaSession: MediaSession,
            controller: MediaSession.ControllerInfo,
            mediaItems: MutableList<MediaItem>
        ): ListenableFuture<MutableList<MediaItem>> {
            return executor.submit<MutableList<MediaItem>> {
                val resolved = mediaItems.map { item ->
                    if (item.localConfiguration != null) {
                        item
                    } else {
                        resolveMediaItem(item.mediaId) ?: item
                    }
                }.toMutableList()
                resolved
            }
        }

        override fun onSearch(
            session: MediaLibrarySession,
            browser: MediaSession.ControllerInfo,
            query: String,
            params: LibraryParams?
        ): ListenableFuture<LibraryResult<Void>> {
            executor.execute {
                val count = searchAll(query).size
                session.notifySearchResultChanged(browser, query, count, params)
            }
            return Futures.immediateFuture(LibraryResult.ofVoid())
        }

        override fun onGetSearchResult(
            session: MediaLibrarySession,
            browser: MediaSession.ControllerInfo,
            query: String,
            page: Int,
            pageSize: Int,
            params: LibraryParams?
        ): ListenableFuture<LibraryResult<ImmutableList<MediaItem>>> {
            return executor.submit<LibraryResult<ImmutableList<MediaItem>>> {
                val results = searchAll(query)
                LibraryResult.ofItemList(ImmutableList.copyOf(results), params)
            }
        }
    }

    private fun buildRootCategories(): List<MediaItem> {
        fun folder(id: String, title: String, type: Int): MediaItem =
            MediaItem.Builder()
                .setMediaId(id)
                .setMediaMetadata(
                    MediaMetadata.Builder()
                        .setTitle(title)
                        .setIsBrowsable(true)
                        .setIsPlayable(false)
                        .setMediaType(type)
                        .build()
                )
                .build()

        return listOf(
            folder(CAT_MUSIC, "Music", MediaMetadata.MEDIA_TYPE_FOLDER_MIXED),
            folder(CAT_PLAYLISTS, "Playlists", MediaMetadata.MEDIA_TYPE_FOLDER_PLAYLISTS),
            folder(CAT_RADIO, "Radio", MediaMetadata.MEDIA_TYPE_FOLDER_RADIO_STATIONS),
            folder(CAT_PODCASTS, "Podcasts", MediaMetadata.MEDIA_TYPE_FOLDER_PODCASTS),
            folder(CAT_AUDIOBOOKS, "Audiobooks", MediaMetadata.MEDIA_TYPE_FOLDER_ALBUMS)
        )
    }

    private fun loadTracks(): List<MediaItem> {
        val raw = httpGet("/api/tracks") ?: return emptyList()
        if (!raw.trimStart().startsWith("[")) return emptyList()
        val arr = JSONArray(raw)
        return (0 until arr.length()).map { i ->
            val t = arr.getJSONObject(i)
            val id = t.optString("id")
            playableItem(
                mediaId = "track:$id",
                title = t.optString("title", "Unknown Track"),
                subtitle = t.optString("artist", ""),
                album = t.optString("album", ""),
                streamPath = "/api/tracks/$id/stream",
                artPath = "/api/tracks/$id/art",
                mediaType = MediaMetadata.MEDIA_TYPE_MUSIC
            )
        }
    }

    private fun loadPlaylists(): List<MediaItem> {
        val raw = httpGet("/api/playlists") ?: return emptyList()
        val arr = JSONArray(raw)
        return (0 until arr.length()).map { i ->
            val p = arr.getJSONObject(i)
            val id = p.optString("id")
            val name = p.optString("name", "Playlist")
            MediaItem.Builder()
                .setMediaId("playlist:$id")
                .setMediaMetadata(
                    MediaMetadata.Builder()
                        .setTitle(name)
                        .setIsBrowsable(true)
                        .setIsPlayable(false)
                        .setMediaType(MediaMetadata.MEDIA_TYPE_PLAYLIST)
                        .build()
                )
                .build()
        }
    }

    private fun loadPlaylistTracks(playlistId: String): List<MediaItem> {
        val raw = httpGet("/api/playlists/$playlistId") ?: return emptyList()
        val obj = JSONObject(raw)
        val arr = obj.optJSONArray("tracks") ?: return emptyList()
        return (0 until arr.length()).map { i ->
            val t = arr.getJSONObject(i)
            val id = t.optString("id")
            playableItem(
                mediaId = "track:$id",
                title = t.optString("title", "Unknown Track"),
                subtitle = t.optString("artist", ""),
                album = t.optString("album", ""),
                streamPath = "/api/tracks/$id/stream",
                artPath = "/api/tracks/$id/art",
                mediaType = MediaMetadata.MEDIA_TYPE_MUSIC
            )
        }
    }

    private fun loadRadio(): List<MediaItem> {
        val raw = httpGet("/api/radio") ?: return emptyList()
        val arr = JSONArray(raw)
        return (0 until arr.length()).map { i ->
            val s = arr.getJSONObject(i)
            val id = s.optString("id")
            playableItem(
                mediaId = "radio:$id",
                title = s.optString("name", "Radio Station"),
                subtitle = s.optString("genre", "Live Radio"),
                album = s.optString("country", ""),
                streamPath = "/api/radio/$id/stream",
                artPath = null,
                mediaType = MediaMetadata.MEDIA_TYPE_RADIO_STATION
            )
        }
    }

    private fun loadPodcasts(): List<MediaItem> {
        val raw = httpGet("/api/podcasts") ?: return emptyList()
        val arr = JSONArray(raw)
        return (0 until arr.length()).map { i ->
            val p = arr.getJSONObject(i)
            val id = p.optString("id")
            MediaItem.Builder()
                .setMediaId("podcast:$id")
                .setMediaMetadata(
                    MediaMetadata.Builder()
                        .setTitle(p.optString("title", "Podcast"))
                        .setArtist(p.optString("author", ""))
                        .setIsBrowsable(true)
                        .setIsPlayable(false)
                        .setMediaType(MediaMetadata.MEDIA_TYPE_FOLDER_PODCASTS)
                        .build()
                )
                .build()
        }
    }

    private fun loadPodcastEpisodes(podcastId: String): List<MediaItem> {
        val raw = httpGet("/api/podcasts/$podcastId") ?: return emptyList()
        val obj = JSONObject(raw)
        val arr = obj.optJSONArray("episodes") ?: return emptyList()
        return (0 until arr.length()).map { i ->
            val ep = arr.getJSONObject(i)
            val epId = ep.optString("id")
            playableItem(
                mediaId = "episode:$podcastId:$epId",
                title = ep.optString("title", "Episode"),
                subtitle = obj.optString("title", "Podcast"),
                album = "",
                streamPath = "/api/podcasts/$podcastId/episodes/$epId/stream",
                artPath = null,
                mediaType = MediaMetadata.MEDIA_TYPE_PODCAST_EPISODE
            )
        }
    }

    private fun loadAudiobooks(): List<MediaItem> {
        val raw = httpGet("/api/audiobooks") ?: return emptyList()
        val obj = JSONObject(raw)
        val arr = obj.optJSONArray("books") ?: return emptyList()
        return (0 until arr.length()).map { i ->
            val b = arr.getJSONObject(i)
            val id = b.optString("id")
            MediaItem.Builder()
                .setMediaId("audiobook:$id")
                .setMediaMetadata(
                    MediaMetadata.Builder()
                        .setTitle(b.optString("title", "Audiobook"))
                        .setArtist(b.optString("author", ""))
                        .setIsBrowsable(true)
                        .setIsPlayable(false)
                        .setMediaType(MediaMetadata.MEDIA_TYPE_AUDIO_BOOK)
                        .build()
                )
                .build()
        }
    }

    private fun loadAudiobookChapters(bookId: String): List<MediaItem> {
        val raw = httpGet("/api/audiobooks/$bookId") ?: return emptyList()
        val obj = JSONObject(raw)
        val arr = obj.optJSONArray("chapters") ?: return emptyList()
        return (0 until arr.length()).map { i ->
            val ch = arr.getJSONObject(i)
            val chId = ch.optString("id")
            playableItem(
                mediaId = "chapter:$bookId:$chId",
                title = ch.optString("title", "Chapter ${i + 1}"),
                subtitle = obj.optString("title", "Audiobook"),
                album = obj.optString("author", ""),
                streamPath = "/api/audiobooks/$bookId/chapters/$chId/stream",
                artPath = "/api/audiobooks/$bookId/art",
                mediaType = MediaMetadata.MEDIA_TYPE_AUDIO_BOOK_CHAPTER
            )
        }
    }

    private fun resolveMediaItem(mediaId: String): MediaItem? {
        return when {
            mediaId.startsWith("track:") -> {
                val id = mediaId.removePrefix("track:")
                playableItem(mediaId, "Track", "", "", "/api/tracks/$id/stream", "/api/tracks/$id/art", MediaMetadata.MEDIA_TYPE_MUSIC)
            }
            mediaId.startsWith("radio:") -> {
                val id = mediaId.removePrefix("radio:")
                playableItem(mediaId, "Live Radio", "", "", "/api/radio/$id/stream", null, MediaMetadata.MEDIA_TYPE_RADIO_STATION)
            }
            mediaId.startsWith("episode:") -> {
                val parts = mediaId.removePrefix("episode:").split(":", limit = 2)
                if (parts.size == 2) {
                    playableItem(mediaId, "Podcast Episode", "", "", "/api/podcasts/${parts[0]}/episodes/${parts[1]}/stream", null, MediaMetadata.MEDIA_TYPE_PODCAST_EPISODE)
                } else null
            }
            mediaId.startsWith("chapter:") -> {
                val parts = mediaId.removePrefix("chapter:").split(":", limit = 2)
                if (parts.size == 2) {
                    playableItem(mediaId, "Chapter", "", "", "/api/audiobooks/${parts[0]}/chapters/${parts[1]}/stream", "/api/audiobooks/${parts[0]}/art", MediaMetadata.MEDIA_TYPE_AUDIO_BOOK_CHAPTER)
                } else null
            }
            else -> null
        }
    }

    private fun searchAll(query: String): List<MediaItem> {
        val q = query.trim().lowercase()
        if (q.isEmpty()) return emptyList()
        val matches = mutableListOf<MediaItem>()
        matches += loadTracks().filter {
            (it.mediaMetadata.title?.toString()?.lowercase()?.contains(q) == true) ||
                (it.mediaMetadata.artist?.toString()?.lowercase()?.contains(q) == true)
        }
        matches += loadRadio().filter {
            it.mediaMetadata.title?.toString()?.lowercase()?.contains(q) == true
        }
        return matches.take(25)
    }

    private fun playableItem(
        mediaId: String,
        title: String,
        subtitle: String,
        album: String,
        streamPath: String,
        artPath: String?,
        mediaType: Int
    ): MediaItem {
        val meta = MediaMetadata.Builder()
            .setTitle(title)
            .setArtist(subtitle.ifEmpty { null })
            .setAlbumTitle(album.ifEmpty { null })
            .setIsBrowsable(false)
            .setIsPlayable(true)
            .setMediaType(mediaType)
        if (artPath != null) {
            meta.setArtworkUri(Uri.parse("$BASE_URL$artPath"))
        }
        return MediaItem.Builder()
            .setMediaId(mediaId)
            .setUri(Uri.parse("$BASE_URL$streamPath"))
            .setMediaMetadata(meta.build())
            .build()
    }

    private fun httpGet(path: String): String? {
        return try {
            val conn = URL("$BASE_URL$path").openConnection() as HttpURLConnection
            conn.connectTimeout = 3000
            conn.readTimeout = 5000
            conn.requestMethod = "GET"
            val code = conn.responseCode
            if (code in 200..299) {
                conn.inputStream.bufferedReader().use { it.readText() }
            } else {
                null
            }
        } catch (_: Exception) {
            null
        }
    }
}

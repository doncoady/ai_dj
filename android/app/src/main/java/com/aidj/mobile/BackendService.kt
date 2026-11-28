package com.aidj.mobile

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.Environment
import android.os.IBinder
import androidx.core.app.NotificationCompat
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.atomic.AtomicBoolean

class BackendService : Service() {

    companion object {
        private const val CHANNEL_ID = "ai_dj_backend"
        private const val NOTIFICATION_ID = 1001
        const val PORT = 8000
        const val PREFS_NAME = "ai_dj_prefs"
        const val KEY_GEMINI_API_KEY = "gemini_api_key"
        const val KEY_AUDIOBOOKS_DIR = "audiobooks_dir"

        private val started = AtomicBoolean(false)

        fun ensureStarted(context: Context) {
            val intent = Intent(context, BackendService::class.java)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(intent)
            } else {
                context.startService(intent)
            }
        }

        fun waitForServer(timeoutMs: Long = 20_000L): Boolean {
            val deadline = System.currentTimeMillis() + timeoutMs
            while (System.currentTimeMillis() < deadline) {
                try {
                    val conn = URL("http://127.0.0.1:$PORT/api/status").openConnection() as HttpURLConnection
                    conn.connectTimeout = 500
                    conn.readTimeout = 500
                    conn.requestMethod = "GET"
                    val code = conn.responseCode
                    conn.disconnect()
                    if (code in 200..499) return true
                } catch (_: Exception) {
                    // Server still starting
                }
                try {
                    Thread.sleep(250)
                } catch (_: InterruptedException) {
                    return false
                }
            }
            return false
        }
    }

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
        val notification = buildNotification()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            startForeground(
                NOTIFICATION_ID,
                notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK or
                    ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE
            )
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }

        if (started.compareAndSet(false, true)) {
            Thread({
                runPythonBackend()
            }, "ai-dj-python-backend").apply {
                isDaemon = true
                start()
            }
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        return START_STICKY
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun runPythonBackend() {
        try {
            val dataDir = File(filesDir, "data").apply { mkdirs() }
            val frontendDir = File(filesDir, "frontend")
            copyAssetDir("frontend", frontendDir)

            val musicDir = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_MUSIC)
            val prefs = getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            val apiKey = prefs.getString(KEY_GEMINI_API_KEY, "") ?: ""
            val audiobooksDir = prefs.getString(KEY_AUDIOBOOKS_DIR, "") ?: ""

            if (!Python.isStarted()) {
                Python.start(AndroidPlatform(this))
            }
            val py = Python.getInstance()
            val runner = py.getModule("android_runner")
            runner.callAttr(
                "start_server",
                dataDir.absolutePath,
                frontendDir.absolutePath,
                musicDir.absolutePath,
                audiobooksDir,
                apiKey,
                PORT
            )
        } catch (e: Exception) {
            e.printStackTrace()
            started.set(false)
        }
    }

    private fun copyAssetDir(assetPath: String, destDir: File) {
        val entries = assets.list(assetPath) ?: return
        if (entries.isEmpty()) {
            destDir.parentFile?.mkdirs()
            assets.open(assetPath).use { input ->
                destDir.outputStream().use { output ->
                    input.copyTo(output)
                }
            }
            return
        }
        destDir.mkdirs()
        for (entry in entries) {
            copyAssetDir("$assetPath/$entry", File(destDir, entry))
        }
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                CHANNEL_ID,
                "AI DJ Background Service",
                NotificationManager.IMPORTANCE_LOW
            ).apply {
                description = "Keeps the local AI DJ server and audio active"
            }
            val nm = getSystemService(NotificationManager::class.java)
            nm.createNotificationChannel(channel)
        }
    }

    private fun buildNotification(): Notification {
        val openIntent = Intent(this, MainActivity::class.java)
        val pendingIntent = PendingIntent.getActivity(
            this,
            0,
            openIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("AI DJ Live")
            .setContentText("On-device DJ server running")
            .setSmallIcon(android.R.drawable.ic_media_play)
            .setContentIntent(pendingIntent)
            .setOngoing(true)
            .build()
    }
}

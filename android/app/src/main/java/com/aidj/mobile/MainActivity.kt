package com.aidj.mobile

import android.Manifest
import android.annotation.SuppressLint
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.webkit.PermissionRequest
import android.webkit.WebChromeClient
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat

class MainActivity : AppCompatActivity() {

    private lateinit var webView: WebView
    private var pendingPermissionRequest: PermissionRequest? = null

    private val permissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { results ->
        val micGranted = results[Manifest.permission.RECORD_AUDIO] == true
        pendingPermissionRequest?.let { req ->
            if (micGranted) {
                req.grant(req.resources)
            } else {
                req.deny()
            }
            pendingPermissionRequest = null
        }
        startBackendAndLoadUi()
    }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        webView = WebView(this).apply {
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            settings.mediaPlaybackRequiresUserGesture = false
            settings.mixedContentMode = WebSettings.MIXED_CONTENT_ALWAYS_ALLOW
            webViewClient = WebViewClient()
            webChromeClient = object : WebChromeClient() {
                override fun onPermissionRequest(request: PermissionRequest) {
                    runOnUiThread {
                        if (ContextCompat.checkSelfPermission(
                                this@MainActivity,
                                Manifest.permission.RECORD_AUDIO
                            ) == PackageManager.PERMISSION_GRANTED
                        ) {
                            request.grant(request.resources)
                        } else {
                            pendingPermissionRequest = request
                            requestRuntimePermissions()
                        }
                    }
                }
            }
        }
        setContentView(webView)

        val prefs = getSharedPreferences(BackendService.PREFS_NAME, Context.MODE_PRIVATE)
        if (!prefs.contains(BackendService.KEY_GEMINI_API_KEY)) {
            promptForApiKey()
        } else {
            requestRuntimePermissions()
        }
    }

    private fun promptForApiKey() {
        val prefs = getSharedPreferences(BackendService.PREFS_NAME, Context.MODE_PRIVATE)
        val input = EditText(this).apply {
            hint = "AIza..."
            setSingleLine(true)
        }
        val layout = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            val pad = (20 * resources.displayMetrics.density).toInt()
            setPadding(pad, pad / 2, pad, 0)
            addView(TextView(this@MainActivity).apply {
                text = "Enter your Gemini API key for live voice DJ features (or Skip for Music/Radio/Podcasts)."
            })
            addView(input)
        }

        AlertDialog.Builder(this)
            .setTitle("AI DJ Setup")
            .setView(layout)
            .setCancelable(false)
            .setPositiveButton("Save") { _, _ ->
                prefs.edit().putString(BackendService.KEY_GEMINI_API_KEY, input.text.toString().trim()).apply()
                requestRuntimePermissions()
            }
            .setNegativeButton("Skip") { _, _ ->
                prefs.edit().putString(BackendService.KEY_GEMINI_API_KEY, "").apply()
                requestRuntimePermissions()
            }
            .show()
    }

    private fun requestRuntimePermissions() {
        val needed = mutableListOf(Manifest.permission.RECORD_AUDIO)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            needed += Manifest.permission.READ_MEDIA_AUDIO
            needed += Manifest.permission.POST_NOTIFICATIONS
        } else {
            needed += Manifest.permission.READ_EXTERNAL_STORAGE
        }

        val missing = needed.filter {
            ContextCompat.checkSelfPermission(this, it) != PackageManager.PERMISSION_GRANTED
        }
        if (missing.isEmpty()) {
            startBackendAndLoadUi()
        } else {
            permissionLauncher.launch(missing.toTypedArray())
        }
    }

    private fun startBackendAndLoadUi() {
        BackendService.ensureStarted(this)
        Thread {
            BackendService.waitForServer(25_000L)
            runOnUiThread {
                if (webView.url == null) {
                    webView.loadUrl("http://127.0.0.1:${BackendService.PORT}/")
                }
            }
        }.start()
    }

    @Deprecated("Deprecated in Java")
    override fun onBackPressed() {
        if (webView.canGoBack()) {
            webView.goBack()
        } else {
            super.onBackPressed()
        }
    }
}

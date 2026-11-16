package com.example.phonedeck

import android.content.res.Configuration
import android.graphics.Color
import android.graphics.drawable.ColorDrawable
import android.graphics.drawable.GradientDrawable
import android.os.Build
import android.os.Bundle
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.webkit.JavascriptInterface
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.activity.ComponentActivity
import androidx.activity.OnBackPressedCallback
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.ConcurrentLinkedQueue
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import kotlin.concurrent.thread

class MainActivity : ComponentActivity() {
    private lateinit var web: WebView
    private var inWeb = false
    private var current = ""
    private val prefs by lazy { getSharedPreferences("deck", MODE_PRIVATE) }
    private var ipText = ""
    private var ipField: EditText? = null

    override fun onCreate(b: Bundle?) {
        super.onCreate(b)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        window.setBackgroundDrawable(ColorDrawable(0xFF0B0D12.toInt()))
        web = WebView(this).apply {
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            settings.textZoom = 100
            settings.setSupportZoom(false)
            settings.cacheMode = WebSettings.LOAD_DEFAULT
            settings.allowFileAccess = false
            settings.allowContentAccess = false
            overScrollMode = View.OVER_SCROLL_NEVER
            isVerticalScrollBarEnabled = false
            isHorizontalScrollBarEnabled = false
            isHapticFeedbackEnabled = false
            setOnLongClickListener { true }
            setBackgroundColor(0xFF0B0D12.toInt())
            addJavascriptInterface(object {
                @JavascriptInterface
                fun disconnect() {
                    this@MainActivity.runOnUiThread { web.loadUrl("about:blank"); showConnect() }
                }
            }, "DeckApp")
            webViewClient = object : WebViewClient() {
                // Sadece ana sayfa yüklenemezse çağrılır: bilgisayar kapalıysa mesaj göster, 2,5 sn'de bir tekrar dene
                override fun onReceivedError(v: WebView, c: Int, d: String?, u: String?) {
                    if (!inWeb) return
                    v.loadDataWithBaseURL("http://$current/", offlineHtml(), "text/html", "utf-8", null)
                }
            }
        }
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() { if (inWeb) showConnect() else finish() }
        })
        showConnect()   // her açılışta bağlanma ekranı
    }

    private fun offlineHtml(): String {
        val a = current
        return """<html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:10px;background:#0b0d12;color:#fff;font-family:sans-serif;text-align:center;padding:24px;box-sizing:border-box">
<h2 style="margin:0">${getString(R.string.offline_title)}</h2>
<div style="color:#9aa3bb">${getString(R.string.offline_text, a)}</div>
<button onclick="DeckApp.disconnect()" style="margin-top:14px;padding:12px 22px;border-radius:14px;border:0;background:#7c9cff;font-size:16px;font-weight:600">${getString(R.string.choose_pc)}</button>
<script>setTimeout(function(){location.replace('http://$a/')},2500)</script></body></html>"""
    }

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()
    private fun lp(top: Int) = LinearLayout.LayoutParams(-1, -2).apply { topMargin = dp(top) }
    private fun round(c: String, r: Int = 16) = GradientDrawable().apply {
        setColor(Color.parseColor(c)); cornerRadius = dp(r).toFloat()
    }
    private fun btn(t: String, bg: String, fg: String, onClick: () -> Unit) = Button(this).apply {
        text = t; isAllCaps = false; setTextColor(Color.parseColor(fg)); background = round(bg)
        setOnClickListener { onClick() }
    }

    private fun devices(): MutableList<Pair<String, String>> {
        val out = mutableListOf<Pair<String, String>>()
        val arr = JSONArray(prefs.getString("devices", "[]"))
        for (i in 0 until arr.length()) {
            val o = arr.getJSONObject(i); out.add(o.getString("name") to o.getString("addr"))
        }
        return out
    }
    private fun saveDevices(l: List<Pair<String, String>>) {
        val arr = JSONArray()
        l.forEach { arr.put(JSONObject().put("name", it.first).put("addr", it.second)) }
        prefs.edit().putString("devices", arr.toString()).apply()
    }
    private fun remember(name: String, addr: String) {
        val l = devices().filter { it.second != addr }.toMutableList()
        l.add(0, name to addr); saveDevices(l)
    }

    private fun open(addr: String) {
        current = addr
        remember(devices().firstOrNull { it.second == addr }?.first ?: addr, addr)
        prefs.edit().putString("last", addr).apply()
        thread { probe(addr.substringBefore(":"), addr.substringAfter(":", "5000"))?.let { remember(if (addr.startsWith("127.0.0.1")) "USB · $it" else it, addr) } }
        inWeb = true
        setContentView(web)
        goFullscreen(true)
        web.loadUrl("http://$addr")
    }

    private fun localPrefix(): String? = try {
        val ifs = java.net.NetworkInterface.getNetworkInterfaces().toList().filter { it.isUp && !it.isLoopback }
        val wifiFirst = ifs.sortedBy { if (it.name.startsWith("wlan")) 0 else 1 }
        wifiFirst.flatMap { it.inetAddresses.toList() }
            .firstOrNull { it is java.net.Inet4Address && it.isSiteLocalAddress }
            ?.hostAddress?.substringBeforeLast(".")
    } catch (e: Exception) { null }

    // PC'deki /api/info bilgisayar adını verir; yanıt vermeyen adres bilgisayar değildir
    private fun probe(host: String, port: String = "5000"): String? = try {
        val c = java.net.URL("http://$host:$port/api/info").openConnection() as java.net.HttpURLConnection
        c.connectTimeout = 500; c.readTimeout = 1000
        JSONObject(c.inputStream.bufferedReader().readText()).getString("name")
    } catch (e: Exception) { null }

    // Dikey: üstte adres/bağlan, altta kayıtlı bilgisayarlar. Yatay: solda kayıtlı bilgisayarlar, sağda adres/bağlan.
    private fun showConnect() {
        inWeb = false
        goFullscreen(false)
        val land = resources.configuration.orientation == Configuration.ORIENTATION_LANDSCAPE
        val muted = Color.parseColor("#9AA3BB")

        val ip = EditText(this).apply {
            hint = "127.0.0.1:5000"; setHintTextColor(Color.parseColor("#6B7388")); setTextColor(Color.WHITE)
            background = round("#1D2231"); setPadding(dp(16), dp(14), dp(16), dp(14)); setSingleLine()
            setText(ipText)
        }
        ipField = ip

        val list = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        val status = TextView(this).apply { setTextColor(muted); setPadding(0, dp(6), 0, dp(4)) }
        lateinit var refresh: () -> Unit
        refresh = {
            list.removeAllViews()
            val last = prefs.getString("last", null)
            devices().forEach { (name, addr) ->
                val row = LinearLayout(this).apply {
                    orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL
                    background = round(if (addr == last) "#2B3566" else "#1D2231")
                    setPadding(dp(16), dp(12), dp(8), dp(12))
                    setOnClickListener { open(addr) }
                }
                row.addView(TextView(this).apply { text = "$name\n$addr"; setTextColor(Color.WHITE) },
                    LinearLayout.LayoutParams(0, -2, 1f))
                row.addView(TextView(this).apply {
                    text = "✕"; textSize = 18f; setTextColor(Color.parseColor("#FF8A9A"))
                    setPadding(dp(14), dp(6), dp(14), dp(6))
                    setOnClickListener { saveDevices(devices().filter { it.second != addr }); refresh() }
                })
                list.addView(row, lp(8))
            }
            if (list.childCount == 0) status.text = getString(R.string.none_saved)
        }

        val connectBtn = btn(getString(R.string.connect), "#7C9CFF", "#000000") {
            val a = ip.text.toString().trim().removePrefix("http://")
            if (a.isNotEmpty()) open(if (a.contains(":")) a else "$a:5000")
        }
        val scanBtn = btn(getString(R.string.scan), "#1D2231", "#FFFFFF") {
            status.text = getString(R.string.scanning)
            thread {
                val prefix = localPrefix()
                val found = ConcurrentLinkedQueue<Pair<String, String>>()
                if (prefix != null) {
                    val pool = Executors.newFixedThreadPool(64)
                    for (i in 1..254) pool.execute { val h = "$prefix.$i"; probe(h)?.let { found.add(it to "$h:5000") } }
                    pool.shutdown(); pool.awaitTermination(10, TimeUnit.SECONDS); pool.shutdownNow()
                }
                runOnUiThread {
                    found.forEach { remember(it.first, it.second) }
                    status.text = when {
                        prefix == null -> getString(R.string.no_wifi)
                        found.isEmpty() -> getString(R.string.none_found)
                        else -> getString(R.string.found_n, found.size)
                    }
                    refresh()
                }
            }
        }

        // USB: PC'deki program "adb reverse" kurar; telefonun 127.0.0.1:5000 adresi PC'ye ulaşır
        val usbBtn = btn(getString(R.string.usb), "#1D2231", "#FFFFFF") {
            status.text = getString(R.string.usb_checking)
            thread {
                val name = probe("127.0.0.1")
                runOnUiThread {
                    if (name != null) { remember("USB · $name", "127.0.0.1:5000"); open("127.0.0.1:5000") }
                    else status.text = getString(R.string.usb_fail)
                }
            }
        }

        val right = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            addView(TextView(this@MainActivity).apply { text = getString(R.string.app_name); textSize = 28f; setTextColor(Color.WHITE) })
            addView(TextView(this@MainActivity).apply {
                text = getString(R.string.connect_subtitle); setTextColor(muted); setPadding(0, dp(4), 0, dp(18))
            })
            addView(ip)
            addView(connectBtn, lp(12))
            addView(scanBtn, lp(8))
            addView(usbBtn, lp(8))
        }
        val left = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            addView(TextView(this@MainActivity).apply {
                text = getString(R.string.saved); textSize = 14f; setTextColor(muted); setPadding(0, if (land) 0 else dp(16), 0, 0)
            })
            addView(status)
            addView(ScrollView(this@MainActivity).apply { addView(list) }, LinearLayout.LayoutParams(-1, 0, 1f))
        }
        val root = LinearLayout(this).apply {
            setBackgroundColor(Color.parseColor("#0B0D12"))
            if (land) {
                orientation = LinearLayout.HORIZONTAL
                setPadding(dp(22), dp(16), dp(22), dp(16))
                addView(left, LinearLayout.LayoutParams(0, -1, 1f).apply { rightMargin = dp(18) })
                addView(ScrollView(this@MainActivity).apply { addView(right) }, LinearLayout.LayoutParams(0, -1, 1f))
            } else {
                orientation = LinearLayout.VERTICAL
                setPadding(dp(22), dp(36), dp(22), dp(22))
                addView(right)
                addView(left, LinearLayout.LayoutParams(-1, 0, 1f))
            }
        }
        setContentView(root)
        refresh()
    }

    override fun onConfigurationChanged(c: Configuration) {
        super.onConfigurationChanged(c)
        if (!inWeb) { ipText = ipField?.text?.toString() ?: ipText; showConnect() }
    }

    // Tam ekran: durum + gezinme çubuğunu gizler (kaydırınca geçici görünür), çentik kenarlarına kadar çizer.
    private fun goFullscreen(on: Boolean) {
        WindowCompat.setDecorFitsSystemWindows(window, !on)
        if (Build.VERSION.SDK_INT >= 28) {
            val a = window.attributes
            a.layoutInDisplayCutoutMode = if (on) WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES
            else WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_DEFAULT
            window.attributes = a
        }
        val c = WindowInsetsControllerCompat(window, window.decorView)
        if (on) {
            c.systemBarsBehavior = WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
            c.hide(WindowInsetsCompat.Type.systemBars())
        } else {
            c.show(WindowInsetsCompat.Type.systemBars())
        }
    }

    override fun onWindowFocusChanged(f: Boolean) {
        super.onWindowFocusChanged(f)
        if (f && inWeb) goFullscreen(true)
    }

    // Arka plandayken sayfadaki zamanlayıcıları durdur: pil ve ısınma için
    override fun onPause() {
        super.onPause()
        web.onPause(); web.pauseTimers()
    }

    override fun onResume() {
        super.onResume()
        web.onResume(); web.resumeTimers()
        if (inWeb) goFullscreen(true)
    }

    override fun onDestroy() {
        web.removeJavascriptInterface("DeckApp")
        web.destroy()
        super.onDestroy()
    }
}
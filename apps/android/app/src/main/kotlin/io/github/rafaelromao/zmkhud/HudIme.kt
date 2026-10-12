package io.github.rafaelromao.zmkhud

import android.annotation.SuppressLint
import android.graphics.Color
import android.inputmethodservice.InputMethodService
import android.media.AudioManager
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.view.HapticFeedbackConstants
import android.view.KeyCharacterMap
import android.view.KeyEvent
import android.view.View
import android.view.ViewGroup
import android.view.inputmethod.EditorInfo
import android.view.inputmethod.InputMethodManager
import android.webkit.JavascriptInterface
import android.webkit.RenderProcessGoneDetail
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.FrameLayout
import androidx.webkit.WebViewAssetLoader
import androidx.webkit.WebViewCompat
import androidx.webkit.WebViewFeature
import io.github.rafaelromao.zmkhud.sink.Editor
import io.github.rafaelromao.zmkhud.sink.Field
import io.github.rafaelromao.zmkhud.sink.ImeSink
import io.github.rafaelromao.zmkhud.sink.PageMessage
import io.github.rafaelromao.zmkhud.sink.Screen
import java.io.ByteArrayInputStream
import kotlin.math.roundToInt

/**
 * The HUD as this tablet's keyboard. The board is the HUD's own page (hud/, in the app's assets),
 * docked, with the virtual firmware typing text (hud/touch.js, a "text" sink); what it types comes
 * back here as ops (PageMessage) and goes into the field through ImeSink.
 *
 * The page is served from the app's assets alone, at https://appassets.androidplatform.net/: every
 * other request is refused, so it reaches no network, and it never navigates. It reaches this
 * service through one bridge, zmkhudIme, taken only from its main frame. One WebView, made the
 * first time the keyboard shows and kept: loading the page again is the slow part.
 */
class HudIme : InputMethodService() {
    private val main = Handler(Looper.getMainLooper())
    private var web: WebView? = null
    private var frame: FrameLayout? = null
    private var booted = false
    private var stamp = 0L          // the keymap the page has (KeymapStore.stamp)
    private var heightPx = 0

    private val sink = ImeSink(object : Editor {
        override fun commitText(text: String) {
            currentInputConnection?.commitText(text, 1)
        }

        override fun sendKey(keycode: Int, meta: Int) {
            val ic = currentInputConnection ?: return
            val down = SystemClock.uptimeMillis()
            val flags = KeyEvent.FLAG_SOFT_KEYBOARD or KeyEvent.FLAG_KEEP_TOUCH_MODE
            ic.sendKeyEvent(KeyEvent(down, down, KeyEvent.ACTION_DOWN, keycode, 0, meta,
                KeyCharacterMap.VIRTUAL_KEYBOARD, 0, flags))
            ic.sendKeyEvent(KeyEvent(down, SystemClock.uptimeMillis(), KeyEvent.ACTION_UP, keycode, 0, meta,
                KeyCharacterMap.VIRTUAL_KEYBOARD, 0, flags))
        }

        override fun performEditorAction(action: Int) {
            currentInputConnection?.performEditorAction(action)
        }
    })

    // ---------- the view ----------

    override fun onCreateInputView(): View {
        val w = web ?: makeWebView().also { web = it }
        (w.parent as? ViewGroup)?.removeView(w)
        val f = FrameLayout(this)
        f.addView(w, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, heightPx.takeIf { it > 0 } ?: maxHeightPx()))
        frame = f
        if (booted) redock()            // a rotation: the board fits the new screen
        return f
    }

    // The keyboard takes the bottom of the screen, never all of it: no extract mode.
    override fun onEvaluateFullscreenMode() = false

    override fun onStartInputView(info: EditorInfo, restarting: Boolean) {
        super.onStartInputView(info, restarting)
        sink.field = Field(info.inputType, info.imeOptions)
        val w = web ?: return
        if (booted && KeymapStore(this).stamp() != stamp) {
            booted = false                  // imported since the page loaded it: load the page again
            w.reload()
            return
        }
        if (booted) startField(w)
    }

    override fun onFinishInputView(finishingInput: Boolean) {
        web?.evaluateJavascript("window.hud && hud.touch && hud.touch.releaseAll();", null)
        super.onFinishInputView(finishingInput)
    }

    override fun onDestroy() {
        web?.destroy()
        web = null
        super.onDestroy()
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun makeWebView(): WebView {
        val loader = WebViewAssetLoader.Builder()
            .addPathHandler("/assets/", WebViewAssetLoader.AssetsPathHandler(this))
            .build()
        val w = WebView(this)
        w.settings.javaScriptEnabled = true
        w.settings.domStorageEnabled = true
        w.settings.allowFileAccess = false
        w.settings.allowContentAccess = false
        w.settings.setSupportZoom(false)
        w.setBackgroundColor(Color.parseColor("#1a1b26"))
        w.isHapticFeedbackEnabled = true
        w.webViewClient = object : WebViewClient() {
            override fun shouldInterceptRequest(view: WebView, request: WebResourceRequest): WebResourceResponse =
                loader.shouldInterceptRequest(request.url) ?: REFUSED()

            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest) = true

            override fun onPageFinished(view: WebView, url: String?) {
                if (view === web) boot(view)
            }

            // The page's renderer died (memory, a crash): a new one, in the same place.
            override fun onRenderProcessGone(view: WebView, detail: RenderProcessGoneDetail): Boolean {
                if (view !== web) return true
                (view.parent as? ViewGroup)?.removeView(view)
                view.destroy()
                booted = false
                val fresh = makeWebView()
                web = fresh
                frame?.addView(fresh, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                    heightPx.takeIf { it > 0 } ?: maxHeightPx()))
                return true
            }
        }
        bridge(w)
        w.loadUrl(PAGE)
        return w
    }

    private fun bridge(w: WebView) {
        if (WebViewFeature.isFeatureSupported(WebViewFeature.WEB_MESSAGE_LISTENER)) {
            WebViewCompat.addWebMessageListener(w, BRIDGE, setOf(ORIGIN)) { _, message, _, isMainFrame, _ ->
                if (isMainFrame) message.data?.let(::fromPage)
            }
        } else {
            w.addJavascriptInterface(JsBridge(), BRIDGE)
        }
    }

    private inner class JsBridge {
        @JavascriptInterface
        fun postMessage(text: String) {
            main.post { fromPage(text) }
        }
    }

    // ---------- the page ----------

    private fun boot(w: WebView) {
        val store = KeymapStore(this)
        stamp = store.stamp()
        val js = StringBuilder()
        js.append("window.ZMKHUD_IME = {maxHeight: ${maxHeightDp()}, canSwitch: ${shouldOfferSwitchingToNextInputMethod()}, " +
            "pxPerMm: ${dpPerMm()}, width: ${widthDp()}};\n")
        js.append(assets.open("ime-boot.js").bufferedReader().use { it.readText() })
        store.read()?.let { js.append("\nhud.receive(").append(it).append(");\n") }
        w.evaluateJavascript(js.toString(), null)
        booted = true
        startField(w)
    }

    /** A new field: nothing held over from the last one, and the strip silent for a password. */
    private fun startField(w: WebView) {
        w.evaluateJavascript("hud.touch && hud.touch.releaseAll(); hud.receive({kind: 'secure', on: ${sink.field.secret}});", null)
    }

    private fun redock() {
        web?.evaluateJavascript("hud.setDock({on: true, max_height: ${maxHeightDp()}, split: true, " +
            "px_per_mm: ${dpPerMm()}, width: ${widthDp()}});", null)
    }

    private fun fromPage(text: String) {
        when (val m = PageMessage.parse(text)) {
            is PageMessage.Ops -> sink.type(m.ops)
            is PageMessage.Feedback -> feedback(m.key)
            is PageMessage.Size -> resize(m.height)
            PageMessage.Switch -> if (!switchToNextInputMethod(false)) {
                (getSystemService(INPUT_METHOD_SERVICE) as InputMethodManager).showInputMethodPicker()
            }
            PageMessage.Unknown -> Unit
        }
    }

    // ---------- size and feel ----------

    private fun maxHeightPx() = (resources.displayMetrics.heightPixels * MAX_SHARE).roundToInt()

    private fun maxHeightDp() = (maxHeightPx() / resources.displayMetrics.density).roundToInt()

    private fun widthDp() = (resources.displayMetrics.widthPixels / resources.displayMetrics.density).roundToInt()

    /** How many dp -- the page's CSS pixels -- are a millimetre on this screen: the board's keys are drawn
     *  their real size with it, or, too big for the screen turned this way, the board is a HUD only. */
    private fun dpPerMm() = resources.displayMetrics.let { Screen.dpPerMm(it.xdpi, it.density) }

    /** As tall as the board came out (CSS pixels are dp), within the keyboard's share of the screen. */
    private fun resize(dp: Int) {
        val density = resources.displayMetrics.density
        val px = (dp * density).roundToInt().coerceIn((MIN_HEIGHT_DP * density).roundToInt(), maxHeightPx())
        val w = web ?: return
        if (px == heightPx) return
        heightPx = px
        w.layoutParams = w.layoutParams.apply { height = px }
    }

    /** A finger landed on a key (the page says which kind): the press's buzz and click, as the app's
     *  switches and the system's own settings allow, on the press rather than when the text arrives. */
    private fun feedback(key: String) {
        val prefs = Prefs(this)
        if (prefs.haptics) web?.performHapticFeedback(HapticFeedbackConstants.KEYBOARD_TAP)
        if (prefs.sound) {
            val fx = when (key) {
                "delete" -> AudioManager.FX_KEYPRESS_DELETE
                "return" -> AudioManager.FX_KEYPRESS_RETURN
                "space" -> AudioManager.FX_KEYPRESS_SPACEBAR
                else -> AudioManager.FX_KEYPRESS_STANDARD
            }
            (getSystemService(AUDIO_SERVICE) as AudioManager).playSoundEffect(fx)
        }
    }

    companion object {
        const val ORIGIN = "https://appassets.androidplatform.net"
        const val PAGE = "$ORIGIN/assets/hud/index.html?embed"
        const val BRIDGE = "zmkhudIme"
        const val MAX_SHARE = 0.6f       // a keyboard of keys their real size is taller than the system's
        const val MIN_HEIGHT_DP = 120

        /** What the page gets for anything that is not one of its own files: nothing, from nowhere. */
        @Suppress("FunctionName")
        fun REFUSED() = WebResourceResponse("text/plain", "utf-8", 403, "Forbidden", emptyMap(),
            ByteArrayInputStream(ByteArray(0)))
    }
}

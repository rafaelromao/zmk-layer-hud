package io.github.rafaelromao.zmkhud

import android.app.Activity
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.view.inputmethod.InputMethodManager
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.Switch
import android.widget.TextView
import io.github.rafaelromao.zmkhud.sink.Keymaps

/**
 * Setup: turning the keyboard on and switching to it (Android's own screens), the keymap it draws --
 * imported from a file, or one shared to or opened with this app -- and how it feels. Plain views:
 * the app has nothing else to show.
 */
class MainActivity : Activity() {
    private lateinit var status: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val pad = (16 * resources.displayMetrics.density).toInt()
        val col = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(pad, pad, pad, pad)
        }
        fun text(s: String, size: Float = 16f) = TextView(this).apply {
            text = s
            textSize = size
            setPadding(0, pad / 2, 0, pad / 2)
        }
        fun button(s: String, on: () -> Unit) = Button(this).apply {
            text = s
            setOnClickListener { on() }
        }
        val prefs = Prefs(this)
        col.addView(text(getString(R.string.app_name), 24f))
        col.addView(text(getString(R.string.intro)))
        status = text("")
        col.addView(status)
        col.addView(button(getString(R.string.enable)) { startActivity(Intent(Settings.ACTION_INPUT_METHOD_SETTINGS)) })
        col.addView(button(getString(R.string.choose)) {
            (getSystemService(INPUT_METHOD_SERVICE) as InputMethodManager).showInputMethodPicker()
        })
        col.addView(button(getString(R.string.import_keymap)) { pick() })
        col.addView(Switch(this).apply {
            text = getString(R.string.haptics)
            isChecked = prefs.haptics
            setOnCheckedChangeListener { _, on -> prefs.haptics = on }
        })
        col.addView(Switch(this).apply {
            text = getString(R.string.sound)
            isChecked = prefs.sound
            setOnCheckedChangeListener { _, on -> prefs.sound = on }
        })
        col.addView(EditText(this).apply { hint = getString(R.string.try_it) })
        setContentView(ScrollView(this).apply { addView(col) })
        show()
        opened(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        opened(intent)
    }

    private fun pick() {
        val i = Intent(Intent.ACTION_OPEN_DOCUMENT).apply {
            addCategory(Intent.CATEGORY_OPENABLE)
            type = "*/*"
            putExtra(Intent.EXTRA_MIME_TYPES, arrayOf("application/json", "text/plain", "application/octet-stream"))
        }
        startActivityForResult(i, PICK)
    }

    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == PICK && resultCode == RESULT_OK) data?.data?.let(::import)
    }

    /** A keymap shared to the app, or opened with it. */
    private fun opened(intent: Intent?) {
        val uri: Uri? = when (intent?.action) {
            Intent.ACTION_VIEW -> intent.data
            Intent.ACTION_SEND -> if (Build.VERSION.SDK_INT >= 33) {
                intent.getParcelableExtra(Intent.EXTRA_STREAM, Uri::class.java)
            } else {
                @Suppress("DEPRECATION")
                intent.getParcelableExtra(Intent.EXTRA_STREAM)
            }
            else -> null
        }
        uri?.let(::import)
    }

    private fun import(uri: Uri) {
        val text = try {
            contentResolver.openInputStream(uri)?.use { it.readBytes() }
                ?.takeIf { it.size <= Keymaps.MAX_BYTES }?.toString(Charsets.UTF_8)
        } catch (e: Exception) {
            null
        }
        if (text == null) {
            say(getString(R.string.unreadable))
            return
        }
        when (val c = Keymaps.check(text)) {
            is Keymaps.Checked.Ok -> {
                KeymapStore(this).write(text)
                say(getString(if (c.types) R.string.imported else R.string.imported_draws_only, c.title))
            }
            is Keymaps.Checked.Refused -> say(c.why)
        }
    }

    private fun show() {
        status.text = KeymapStore(this).title()?.let { getString(R.string.keymap_is, it) } ?: getString(R.string.no_keymap)
    }

    private fun say(s: String) {
        show()
        status.append("\n" + s)
    }

    companion object {
        const val PICK = 1
    }
}

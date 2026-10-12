package io.github.rafaelromao.zmkhud

import android.content.Context
import io.github.rafaelromao.zmkhud.sink.Keymaps
import java.io.File

/** The keymap the keyboard draws, as the app imported it: one file in the app's own storage. */
class KeymapStore(private val context: Context) {
    private val file get() = File(context.filesDir, "keymap.json")

    fun read(): String? = file.takeIf { it.isFile }?.readText()

    /** When it last changed, so a keyboard already open notices an import. */
    fun stamp(): Long = file.lastModified()

    /** Whole or not at all: the keyboard may read it at any moment. */
    fun write(text: String) {
        val tmp = File(context.filesDir, "keymap.json.tmp")
        tmp.writeText(text)
        if (!tmp.renameTo(file)) {
            tmp.delete()
            error("could not keep the keymap")
        }
    }

    /** The keymap's title, for the setup screen. */
    fun title(): String? = read()?.let { (Keymaps.check(it) as? Keymaps.Checked.Ok)?.title }
}

/** The two choices the keyboard reads: a vibration and a click on each key, both on until turned off:
 *  this keyboard is a touch screen's, where the HUD's feedback is on by default. */
class Prefs(context: Context) {
    private val p = context.getSharedPreferences("keyboard", Context.MODE_PRIVATE)

    var haptics: Boolean
        get() = p.getBoolean("haptics", true)
        set(v) = p.edit().putBoolean("haptics", v).apply()

    var sound: Boolean
        get() = p.getBoolean("sound", true)
        set(v) = p.edit().putBoolean("sound", v).apply()
}

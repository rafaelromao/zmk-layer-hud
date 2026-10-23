package io.github.rafaelromao.zmkhud.sink

import java.io.InputStream

/**
 * A keymap file the app is given -- `zmk-layer-hud keymap -o board.json` on the computer, sent over
 * by a share, Files or a sample -- checked before it replaces the one the keyboard draws.
 */
object Keymaps {
    /** The newest message schema this app reads (docs/touch.md: the keymap message's `schema`). */
    const val SCHEMA = 1
    const val MAX_BYTES = 8 * 1024 * 1024

    sealed class Checked {
        /** A keymap to keep; `types` is false when it has no usable `actions` (a board published for the
         *  landing page, say): it is drawn, and its keys do nothing. */
        data class Ok(val title: String, val types: Boolean) : Checked()
        data class Refused(val why: String) : Checked()
    }

    /** A keymap file's bytes, read no further than [max]: null for a longer one. A share or a VIEW can
     *  hand the app any stream at all, and reading it whole first would let it take all the memory
     *  there is -- the keyboard's too, which shares the process. */
    fun read(input: InputStream, max: Int = MAX_BYTES): ByteArray? {
        val out = java.io.ByteArrayOutputStream()
        val buf = ByteArray(64 * 1024)
        while (true) {
            val n = input.read(buf, 0, minOf(buf.size, max + 1 - out.size()))
            if (n < 0) return out.toByteArray()
            out.write(buf, 0, n)
            if (out.size() > max) return null
        }
    }

    fun check(text: String): Checked {
        if (text.length > MAX_BYTES) return Checked.Refused("this file is too large to be a keymap")
        val o = try {
            Json.parse(text) as? Map<*, *>
        } catch (e: JsonError) {
            return Checked.Refused("this is not a keymap: ${e.message}")
        } ?: return Checked.Refused("this is not a keymap")
        @Suppress("UNCHECKED_CAST")
        val m = o as Map<String, Any?>
        if (m["kind"] != "keymap") {
            return Checked.Refused("this is not a keymap: write one with `zmk-layer-hud keymap -o board.json`")
        }
        val schema = m.int("schema") ?: 0
        if (schema > SCHEMA) {
            return Checked.Refused("this keymap is from a newer zmk-layer-hud (schema $schema): update this app")
        }
        val layout = m["layout"] as? Map<*, *>
        if (layout?.get("keys") !is List<*> || m["layers"] !is Map<*, *>) {
            return Checked.Refused("this keymap has no keys to draw")
        }
        val actions = m["actions"] as? Map<*, *>
        val title = (m["title"] as? String)?.takeIf { it.isNotBlank() } ?: "keymap"
        return Checked.Ok(title, actions != null && actions["error"] == null)
    }
}

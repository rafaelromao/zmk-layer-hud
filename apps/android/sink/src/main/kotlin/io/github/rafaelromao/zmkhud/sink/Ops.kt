package io.github.rafaelromao.zmkhud.sink

/**
 * What the board types, as the page's hidtext.textEvents says it (docs/touch.md): text, a named key,
 * or a ⌘/Ctrl shortcut. Each keeps the HID usage and the modifier byte it came from.
 */
sealed class Op {
    abstract val usage: Int
    abstract val mods: Int

    data class Text(val text: String, override val usage: Int, override val mods: Int) : Op()
    data class Key(val key: String, override val usage: Int, override val mods: Int) : Op()
    data class Chord(val chars: String, val name: String?, override val usage: Int, override val mods: Int) : Op()

    companion object {
        /** One op from its JSON object, or null for one this keyboard does not know. */
        fun of(o: Map<String, Any?>): Op? {
            val usage = o.int("usage") ?: 0
            val mods = (o.int("mods") ?: 0) and 0xFF
            return when (o["kind"]) {
                "text" -> o.string("text")?.takeIf { it.isNotEmpty() }?.let { Text(it, usage, mods) }
                "key" -> o.string("key")?.let { Key(it, usage, mods) }
                "chord" -> Chord(o.string("chars") ?: "", o.string("name"), usage, mods)
                else -> null
            }
        }
    }
}

/** A message the page posts to the keyboard (ime-boot.js). */
sealed class PageMessage {
    data class Ops(val ops: List<Op>) : PageMessage()
    data class Size(val height: Int) : PageMessage()
    object Switch : PageMessage()
    object Unknown : PageMessage()

    companion object {
        const val MAX_LENGTH = 64 * 1024

        fun parse(text: String): PageMessage {
            if (text.length > MAX_LENGTH) return Unknown
            val o = try {
                Json.parse(text) as? Map<*, *>
            } catch (e: JsonError) {
                null
            } ?: return Unknown
            @Suppress("UNCHECKED_CAST")
            val m = o as Map<String, Any?>
            return when (m["kind"]) {
                "ops" -> Ops((m["ops"] as? List<*>).orEmpty().mapNotNull { (it as? Map<*, *>)?.let { op ->
                    @Suppress("UNCHECKED_CAST")
                    Op.of(op as Map<String, Any?>)
                } })
                "size" -> m.int("height")?.takeIf { it in 1..10_000 }?.let { Size(it) } ?: Unknown
                "switch" -> Switch
                else -> Unknown
            }
        }
    }
}

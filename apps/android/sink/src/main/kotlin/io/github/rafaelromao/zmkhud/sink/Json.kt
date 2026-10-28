package io.github.rafaelromao.zmkhud.sink

/**
 * A JSON reader for the page's messages and the keymap files, small enough to own: the app and the
 * tests read with the same one, where Android's org.json would be a second reader the JVM tests
 * never meet. Objects come back as Map<String, Any?>, arrays as List<Any?>, numbers as Long when
 * whole and Double otherwise; malformed text throws JsonError.
 */
class JsonError(message: String) : IllegalArgumentException(message)

object Json {
    fun parse(text: String): Any? {
        val r = Reader(text)
        r.space()
        val value = r.value(0)
        r.space()
        if (r.at < text.length) throw JsonError("text after the value at ${r.at}")
        return value
    }

    private const val MAX_DEPTH = 64

    private class Reader(val s: String) {
        var at = 0

        fun space() {
            while (at < s.length && s[at] in " \t\r\n") at++
        }

        fun value(depth: Int): Any? {
            if (depth > MAX_DEPTH) throw JsonError("nested too deep")
            if (at >= s.length) throw JsonError("the text ends early")
            return when (val c = s[at]) {
                '{' -> obj(depth)
                '[' -> arr(depth)
                '"' -> str()
                't' -> word("true", true)
                'f' -> word("false", false)
                'n' -> word("null", null)
                else -> if (c == '-' || c in '0'..'9') num() else throw JsonError("unexpected '$c' at $at")
            }
        }

        fun word(w: String, v: Any?): Any? {
            if (!s.startsWith(w, at)) throw JsonError("unexpected text at $at")
            at += w.length
            return v
        }

        fun obj(depth: Int): Map<String, Any?> {
            val out = LinkedHashMap<String, Any?>()
            at++
            space()
            if (peek() == '}') { at++; return out }
            while (true) {
                space()
                if (peek() != '"') throw JsonError("a key is a string, at $at")
                val k = str()
                space()
                expect(':')
                space()
                out[k] = value(depth + 1)
                space()
                when (peek()) {
                    ',' -> at++
                    '}' -> { at++; return out }
                    else -> throw JsonError("',' or '}' expected at $at")
                }
            }
        }

        fun arr(depth: Int): List<Any?> {
            val out = ArrayList<Any?>()
            at++
            space()
            if (peek() == ']') { at++; return out }
            while (true) {
                space()
                out.add(value(depth + 1))
                space()
                when (peek()) {
                    ',' -> at++
                    ']' -> { at++; return out }
                    else -> throw JsonError("',' or ']' expected at $at")
                }
            }
        }

        fun str(): String {
            at++
            val b = StringBuilder()
            while (true) {
                if (at >= s.length) throw JsonError("a string never ends")
                val c = s[at++]
                when {
                    c == '"' -> return b.toString()
                    c == '\\' -> {
                        if (at >= s.length) throw JsonError("a string never ends")
                        when (val e = s[at++]) {
                            '"', '\\', '/' -> b.append(e)
                            'b' -> b.append('\b')
                            'f' -> b.append('\u000C')
                            'n' -> b.append('\n')
                            'r' -> b.append('\r')
                            't' -> b.append('\t')
                            'u' -> {
                                if (at + 4 > s.length) throw JsonError("a short \\u escape")
                                b.append(s.substring(at, at + 4).toIntOrNull(16)?.toChar()
                                    ?: throw JsonError("a bad \\u escape at $at"))
                                at += 4
                            }
                            else -> throw JsonError("a bad escape '\\$e'")
                        }
                    }
                    c < ' ' -> throw JsonError("a control character in a string at ${at - 1}")
                    else -> b.append(c)
                }
            }
        }

        fun num(): Any {
            val start = at
            if (peek() == '-') at++
            while (at < s.length && s[at] in "0123456789.eE+-") at++
            val t = s.substring(start, at)
            if (t.none { it == '.' || it == 'e' || it == 'E' }) t.toLongOrNull()?.let { return it }
            return t.toDoubleOrNull() ?: throw JsonError("a bad number '$t'")
        }

        fun peek(): Char = if (at < s.length) s[at] else '\u0000'

        fun expect(c: Char) {
            if (peek() != c) throw JsonError("'$c' expected at $at")
            at++
        }
    }
}

/** A JSON object's field as an Int, when it is a whole number. */
fun Map<String, Any?>.int(name: String): Int? = (this[name] as? Long)?.takeIf { it in Int.MIN_VALUE..Int.MAX_VALUE }?.toInt()

fun Map<String, Any?>.string(name: String): String? = this[name] as? String

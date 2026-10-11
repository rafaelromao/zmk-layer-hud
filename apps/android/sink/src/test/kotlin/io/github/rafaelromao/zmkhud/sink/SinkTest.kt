package io.github.rafaelromao.zmkhud.sink

import java.io.File
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertFalse
import kotlin.test.assertIs
import kotlin.test.assertNull
import kotlin.test.assertTrue
import org.junit.Test

/** What the keyboard does with what the board types, against a field that writes down what it is told. */
class SinkTest {
    private class Recorder : Editor {
        val done = mutableListOf<String>()
        override fun commitText(text: String) { done += "text $text" }
        override fun sendKey(keycode: Int, meta: Int) { done += "key $keycode meta ${meta.toString(16)}" }
        override fun performEditorAction(action: Int) { done += "action $action" }
    }

    private fun typed(field: Field, vararg ops: Op): List<String> {
        val r = Recorder()
        val sink = ImeSink(r)
        sink.field = field
        sink.type(ops.toList())
        return r.done
    }

    private val singleLineSearch = Field(InputType.TYPE_CLASS_TEXT, EditorInfo.IME_ACTION_SEARCH)
    private val multiLine = Field(InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_MULTI_LINE, EditorInfo.IME_ACTION_SEND)
    private val terminal = Field(InputType.TYPE_NULL, 0)

    @Test fun textIsCommittedAsText() {
        assertEquals(listOf("text é", "text hello"),
            typed(Field(), Op.Text("é", 0x08, 0), Op.Text("hello", 0x0B, 0)))
    }

    @Test fun enterIsTheFieldsActionWhereItHasOneAndAKeyElsewhere() {
        val enter = Op.Key("enter", 0x28, 0)
        assertEquals(listOf("action ${EditorInfo.IME_ACTION_SEARCH}"), typed(singleLineSearch, enter))
        assertEquals(listOf("key 66 meta 0"), typed(multiLine, enter))
        assertEquals(listOf("key 66 meta 0"),
            typed(Field(InputType.TYPE_CLASS_TEXT, EditorInfo.IME_ACTION_DONE or EditorInfo.IME_FLAG_NO_ENTER_ACTION), enter))
        assertEquals(listOf("key 66 meta 0"), typed(Field(InputType.TYPE_CLASS_TEXT, EditorInfo.IME_ACTION_UNSPECIFIED), enter))
        // Shift+Enter is a key, whatever the field's action: a chat's new line.
        assertEquals(listOf("key 66 meta 41"), typed(singleLineSearch, Op.Key("enter", 0x28, HidKeycodes.LSHIFT)))
    }

    @Test fun namedKeysAreKeyEventsWithTheirModifiers() {
        assertEquals(listOf("key 67 meta 0", "key 112 meta 0", "key 21 meta 41", "key 142 meta 0"), typed(Field(),
            Op.Key("backspace", 0x2A, 0), Op.Key("delete", 0x4C, 0), Op.Key("left", 0x50, HidKeycodes.LSHIFT),
            Op.Key("f12", 0x45, 0)))
        assertEquals(emptyList(), typed(Field(), Op.Key("f13", 0x68, 0)), "a key Android has no code for is left out")
    }

    @Test fun aShortcutIsItsKeyWithItsModifiers() {
        assertEquals(listOf("key 31 meta 3000"), typed(Field(), Op.Chord("c", null, 0x06, HidKeycodes.LCTRL)))
        assertEquals(listOf("key 52 meta 30000"), typed(Field(), Op.Chord("x", null, 0x1B, HidKeycodes.LGUI)))
        assertEquals(emptyList(), typed(Field(), Op.Chord("", "f13", 0x68, HidKeycodes.LCTRL)))
    }

    @Test fun aTerminalGetsKeysForWhatAUsLayoutTypesAndTextForTheRest() {
        assertEquals(listOf("key 29 meta 0", "key 29 meta 41", "key 10 meta 41", "text é", "text ab"), typed(terminal,
            Op.Text("a", 0x04, 0), Op.Text("A", 0x04, HidKeycodes.LSHIFT), Op.Text("#", 0x20, HidKeycodes.LSHIFT),
            Op.Text("é", 0x08, 0), Op.Text("ab", 0x04, 0)))
        // A key whose text is not what a US layout gives it (a Dvorak or AltGr layout's) is text.
        assertEquals(listOf("text z"), typed(terminal, Op.Text("z", 0x04, 0)))
        assertEquals(listOf("text €"), typed(terminal, Op.Text("€", 0x08, HidKeycodes.RALT)))
        // Enter in a terminal is the key.
        assertEquals(listOf("key 66 meta 0"), typed(Field(InputType.TYPE_NULL, EditorInfo.IME_ACTION_GO), Op.Key("enter", 0x28, 0)))
    }

    @Test fun passwordsAreSecretAndOtherFieldsAreNot() {
        for (v in listOf(InputType.TYPE_TEXT_VARIATION_PASSWORD, InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD,
                         InputType.TYPE_TEXT_VARIATION_WEB_PASSWORD)) {
            assertTrue(Field(InputType.TYPE_CLASS_TEXT or v).secret)
        }
        assertTrue(Field(InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_VARIATION_PASSWORD).secret)
        assertFalse(Field(InputType.TYPE_CLASS_TEXT).secret)
        assertFalse(Field(InputType.TYPE_CLASS_NUMBER or InputType.TYPE_TEXT_VARIATION_PASSWORD).secret)
        assertFalse(terminal.secret)
    }

    @Test fun theKeyCodesAndMetaStates() {
        val anchors = mapOf(0x04 to 29, 0x1D to 54, 0x1E to 8, 0x26 to 16, 0x27 to 7, 0x28 to 66, 0x29 to 111, 0x2A to 67,
            0x2B to 61, 0x2C to 62, 0x38 to 76, 0x3A to 131, 0x45 to 142, 0x4C to 112, 0x4F to 22, 0x52 to 19,
            0x59 to 145, 0x62 to 144, 0xE0 to 113, 0xE7 to 118)
        for ((usage, code) in anchors) assertEquals(code, HidKeycodes.keycode(usage), "usage 0x${usage.toString(16)}")
        for (usage in 0x68..0x73) assertNull(HidKeycodes.keycode(usage), "F13..F24")
        assertEquals(0, HidKeycodes.metaState(0))
        assertEquals(KeyEvent.META_SHIFT_ON or KeyEvent.META_SHIFT_RIGHT_ON or KeyEvent.META_ALT_ON or KeyEvent.META_ALT_LEFT_ON,
            HidKeycodes.metaState(HidKeycodes.RSHIFT or HidKeycodes.LALT))
        assertEquals(KeyEvent.META_CTRL_ON or KeyEvent.META_CTRL_LEFT_ON or KeyEvent.META_CTRL_RIGHT_ON,
            HidKeycodes.metaState(HidKeycodes.LCTRL or HidKeycodes.RCTRL))
        assertEquals(listOf('a', 'Z', '1', '!', '0', ')', '-', '_', '\\', '|', ';', ':', '/', '?'), listOf(
            HidKeycodes.usChar(0x04, false), HidKeycodes.usChar(0x1D, true), HidKeycodes.usChar(0x1E, false),
            HidKeycodes.usChar(0x1E, true), HidKeycodes.usChar(0x27, false), HidKeycodes.usChar(0x27, true),
            HidKeycodes.usChar(0x2D, false), HidKeycodes.usChar(0x2D, true), HidKeycodes.usChar(0x31, false),
            HidKeycodes.usChar(0x31, true), HidKeycodes.usChar(0x33, false), HidKeycodes.usChar(0x33, true),
            HidKeycodes.usChar(0x38, false), HidKeycodes.usChar(0x38, true)))
        assertNull(HidKeycodes.usChar(0x32, false), "non-US # types what the layout says, not what US does")
    }

    @Test fun everyKeyNameThePageSendsIsKnown() {
        // hud/hidtext.js TEXT_KEYS: the names a key op can carry.
        val src = File(System.getProperty("zmkhud.root"), "hud/hidtext.js").readText()
        val table = Regex("""TEXT_KEYS = \{([^}]*)\}""").find(src)!!.groupValues[1]
        val names = Regex(""":\s*"(\w+)"""").findAll(table).map { it.groupValues[1] }.toSet() + (1..12).map { "f$it" }
        assertTrue(names.size > 10)
        assertEquals(names, ImeSink.NAMED.keys)
    }

    @Test fun theGoldenOpsAreReadAndTypedAsTheySay() {
        // apps/fixtures/kbd-golden.json: what hud/kbd.js types for a script of fingers, the iPad's
        // and this keyboard's common reference.
        val golden = Json.parse(File(System.getProperty("zmkhud.root"), "apps/fixtures/kbd-golden.json").readText()) as Map<*, *>
        val raw = (golden["steps"] as List<*>).flatMap { ((it as Map<*, *>)["answer"] as Map<*, *>)["ops"] as List<*> }
        @Suppress("UNCHECKED_CAST")
        val ops = raw.map { Op.of(it as Map<String, Any?>) }
        assertTrue(ops.isNotEmpty() && ops.none { it == null }, "every op the page sends is one this keyboard reads")
        assertEquals(listOf("text a", "text A", "text 1", "text  ", "text é", "text '", "text a", "text a", "text a",
                            "key ${KeyEvent.KEYCODE_ESCAPE} meta 0"),
            typed(Field(), *ops.filterNotNull().toTypedArray()))
    }

    @Test fun thePagesMessages() {
        val ops = PageMessage.parse("""{"kind":"ops","v":1,"seq":4,"ops":[{"kind":"text","text":"a","usage":4,"mods":0},
            {"kind":"key","key":"backspace","usage":42,"mods":0},{"kind":"chord","chars":"c","name":null,"usage":6,"mods":1},
            {"kind":"mouse"}]}""")
        assertEquals(PageMessage.Ops(listOf(Op.Text("a", 4, 0), Op.Key("backspace", 42, 0), Op.Chord("c", null, 6, 1))), ops)
        assertEquals(PageMessage.Size(212), PageMessage.parse("""{"kind":"size","height":212}"""))
        assertIs<PageMessage.Switch>(PageMessage.parse("""{"kind":"switch"}"""))
        for (junk in listOf("", "[]", "{", """{"kind":"size","height":-1}""", """{"kind":"hid","keys":[4]}""", "x".repeat(70_000))) {
            assertIs<PageMessage.Unknown>(PageMessage.parse(junk), junk.take(20))
        }
    }

    @Test fun json() {
        assertEquals(mapOf("a" to listOf(1L, -2.5, true, null, "é\né")), Json.parse("""{"a":[1,-2.5,true,null,"é\né"]}"""))
        assertEquals(emptyMap<String, Any?>(), Json.parse(" { } "))
        for (bad in listOf("", "{", "[1,]", "{\"a\" 1}", "\"\u0001\"", "01x", "{} {}", "[".repeat(100) + "]".repeat(100))) {
            assertFailsWith<JsonError>(bad.take(20)) { Json.parse(bad) }
        }
    }

    @Test fun aKeymapIsCheckedBeforeItIsKept() {
        val keys = """"layout":{"keys":[]},"layers":{}"""
        assertEquals(Keymaps.Checked.Ok("Diamond", true),
            Keymaps.check("""{"kind":"keymap","schema":1,"title":"Diamond",$keys,"actions":{"v":1}}"""))
        assertEquals(Keymaps.Checked.Ok("keymap", false), Keymaps.check("""{"kind":"keymap",$keys}"""))
        assertEquals(Keymaps.Checked.Ok("keymap", false), Keymaps.check("""{"kind":"keymap",$keys,"actions":{"error":"x"}}"""))
        for ((text, said) in listOf("""{"kind":"keymap","schema":2,$keys}""" to "newer", """{"kind":"layers"}""" to "not a keymap",
                                     "nope" to "not a keymap", """{"kind":"keymap"}""" to "no keys")) {
            val c = Keymaps.check(text)
            assertIs<Keymaps.Checked.Refused>(c, text)
            assertTrue(said in c.why, c.why)
        }
    }
}

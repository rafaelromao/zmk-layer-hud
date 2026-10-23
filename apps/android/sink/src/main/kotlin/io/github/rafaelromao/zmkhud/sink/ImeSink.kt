package io.github.rafaelromao.zmkhud.sink

/** The field being typed into, as far as the keyboard does anything with it: what it said it is. */
interface Editor {
    fun commitText(text: String)

    /** The key down then up, with this meta state. */
    fun sendKey(keycode: Int, meta: Int)

    fun performEditorAction(action: Int)
}

/** A field's EditorInfo, the two numbers of it the keyboard reads. */
data class Field(val inputType: Int = InputType.TYPE_CLASS_TEXT, val imeOptions: Int = 0) {
    private val cls get() = inputType and InputType.TYPE_MASK_CLASS
    private val variation get() = inputType and InputType.TYPE_MASK_VARIATION

    /** A field that takes no text, only keys: a terminal's (TYPE_NULL). */
    val raw get() = inputType == InputType.TYPE_NULL

    /** A password: nothing typed into it shows on the board's strip. */
    val secret
        get() = (cls == InputType.TYPE_CLASS_TEXT && variation in setOf(
            InputType.TYPE_TEXT_VARIATION_PASSWORD, InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD,
            InputType.TYPE_TEXT_VARIATION_WEB_PASSWORD)) ||
            (cls == InputType.TYPE_CLASS_NUMBER && variation == InputType.TYPE_NUMBER_VARIATION_PASSWORD)

    /**
     * What Enter does here when it is not a new line: the field's action (Go, Search, Send, Next,
     * Done, Previous). Null where Enter is a key: a multi-line field, a field that asks for none.
     */
    val enterAction: Int?
        get() {
            val action = imeOptions and EditorInfo.IME_MASK_ACTION
            return when {
                raw -> null
                imeOptions and EditorInfo.IME_FLAG_NO_ENTER_ACTION != 0 -> null
                cls == InputType.TYPE_CLASS_TEXT && inputType and InputType.TYPE_TEXT_FLAG_MULTI_LINE != 0 -> null
                action == EditorInfo.IME_ACTION_NONE || action == EditorInfo.IME_ACTION_UNSPECIFIED -> null
                else -> action
            }
        }
}

/**
 * What the board types, done to the field: text committed as text, named keys and shortcuts sent as
 * key events with their modifiers, Enter as the field's action where it has one. A terminal's field
 * (TYPE_NULL) gets key events for what a US layout types from the same key, and text for the rest.
 */
class ImeSink(private val editor: Editor) {
    var field = Field()

    fun type(ops: List<Op>) = ops.forEach(::type)

    fun type(op: Op) {
        when (op) {
            is Op.Text -> text(op)
            is Op.Key -> key(op)
            is Op.Chord -> HidKeycodes.keycode(op.usage)?.let { editor.sendKey(it, HidKeycodes.metaState(op.mods)) }
        }
    }

    private fun text(op: Op.Text) {
        if (field.raw) {
            val plain = op.mods and HidKeycodes.SHIFT.inv() == 0
            val us = HidKeycodes.usChar(op.usage, op.mods and HidKeycodes.SHIFT != 0)
            val code = HidKeycodes.keycode(op.usage)
            if (plain && code != null && us != null && op.text == us.toString()) {
                editor.sendKey(code, HidKeycodes.metaState(op.mods))
                return
            }
        }
        editor.commitText(op.text)
    }

    private fun key(op: Op.Key) {
        val meta = HidKeycodes.metaState(op.mods)
        if (op.key == "enter" && op.mods == 0) {
            field.enterAction?.let { editor.performEditorAction(it); return }
        }
        NAMED[op.key]?.let { editor.sendKey(it, meta) }
    }

    companion object {
        /** hidtext.js TEXT_KEYS, the names a key op carries, as key codes. F13 and up have none. */
        val NAMED: Map<String, Int> = buildMap {
            put("enter", KeyEvent.KEYCODE_ENTER)
            put("backspace", KeyEvent.KEYCODE_DEL)
            put("delete", KeyEvent.KEYCODE_FORWARD_DEL)
            put("tab", KeyEvent.KEYCODE_TAB)
            put("escape", KeyEvent.KEYCODE_ESCAPE)
            put("left", KeyEvent.KEYCODE_DPAD_LEFT)
            put("right", KeyEvent.KEYCODE_DPAD_RIGHT)
            put("up", KeyEvent.KEYCODE_DPAD_UP)
            put("down", KeyEvent.KEYCODE_DPAD_DOWN)
            put("home", KeyEvent.KEYCODE_MOVE_HOME)
            put("end", KeyEvent.KEYCODE_MOVE_END)
            put("pageup", KeyEvent.KEYCODE_PAGE_UP)
            put("pagedown", KeyEvent.KEYCODE_PAGE_DOWN)
            put("insert", KeyEvent.KEYCODE_INSERT)
            for (i in 1..12) put("f$i", KeyEvent.KEYCODE_F1 + i - 1)
        }
    }
}

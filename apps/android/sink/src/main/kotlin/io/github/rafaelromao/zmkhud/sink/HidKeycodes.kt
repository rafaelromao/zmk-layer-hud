package io.github.rafaelromao.zmkhud.sink

/**
 * A HID keyboard-page usage as an Android key code, and a report's modifier byte as a meta state.
 *
 * The usages are the USB HID Usage Tables' (section 10, Keyboard/Keypad Page 0x07); the key codes
 * android.view.KeyEvent's. The pairing follows what a USB keyboard plugged into Android does: the
 * kernel's hid-input.c turns the usage into a Linux key, and Android's Generic.kl turns that into the
 * key code -- so the non-US # key (0x32), which the kernel reads as KEY_BACKSLASH, is a backslash
 * here too. Usages with no key code (F13 to F24, the International keys) are null: the keyboard
 * leaves them out rather than press something else.
 */
object HidKeycodes {
    // Modifier bits of a report's first byte, as the HUD's engine and every USB keyboard send them.
    const val LCTRL = 0x01
    const val LSHIFT = 0x02
    const val LALT = 0x04
    const val LGUI = 0x08
    const val RCTRL = 0x10
    const val RSHIFT = 0x20
    const val RALT = 0x40
    const val RGUI = 0x80
    const val SHIFT = LSHIFT or RSHIFT

    private val table: Map<Int, Int> = buildMap {
        for (i in 0 until 26) put(0x04 + i, KeyEvent.KEYCODE_A + i)                  // a..z
        for (i in 0 until 9) put(0x1E + i, KeyEvent.KEYCODE_0 + 1 + i)               // 1..9
        put(0x27, KeyEvent.KEYCODE_0)
        put(0x28, KeyEvent.KEYCODE_ENTER)
        put(0x29, KeyEvent.KEYCODE_ESCAPE)
        put(0x2A, KeyEvent.KEYCODE_DEL)                                              // Backspace
        put(0x2B, KeyEvent.KEYCODE_TAB)
        put(0x2C, KeyEvent.KEYCODE_SPACE)
        put(0x2D, KeyEvent.KEYCODE_MINUS)
        put(0x2E, KeyEvent.KEYCODE_EQUALS)
        put(0x2F, KeyEvent.KEYCODE_LEFT_BRACKET)
        put(0x30, KeyEvent.KEYCODE_RIGHT_BRACKET)
        put(0x31, KeyEvent.KEYCODE_BACKSLASH)
        put(0x32, KeyEvent.KEYCODE_BACKSLASH)                                        // non-US #
        put(0x33, KeyEvent.KEYCODE_SEMICOLON)
        put(0x34, KeyEvent.KEYCODE_APOSTROPHE)
        put(0x35, KeyEvent.KEYCODE_GRAVE)
        put(0x36, KeyEvent.KEYCODE_COMMA)
        put(0x37, KeyEvent.KEYCODE_PERIOD)
        put(0x38, KeyEvent.KEYCODE_SLASH)
        put(0x39, KeyEvent.KEYCODE_CAPS_LOCK)
        for (i in 0 until 12) put(0x3A + i, KeyEvent.KEYCODE_F1 + i)                 // F1..F12
        put(0x46, KeyEvent.KEYCODE_SYSRQ)                                            // Print Screen
        put(0x47, KeyEvent.KEYCODE_SCROLL_LOCK)
        put(0x48, KeyEvent.KEYCODE_BREAK)                                            // Pause
        put(0x49, KeyEvent.KEYCODE_INSERT)
        put(0x4A, KeyEvent.KEYCODE_MOVE_HOME)
        put(0x4B, KeyEvent.KEYCODE_PAGE_UP)
        put(0x4C, KeyEvent.KEYCODE_FORWARD_DEL)
        put(0x4D, KeyEvent.KEYCODE_MOVE_END)
        put(0x4E, KeyEvent.KEYCODE_PAGE_DOWN)
        put(0x4F, KeyEvent.KEYCODE_DPAD_RIGHT)
        put(0x50, KeyEvent.KEYCODE_DPAD_LEFT)
        put(0x51, KeyEvent.KEYCODE_DPAD_DOWN)
        put(0x52, KeyEvent.KEYCODE_DPAD_UP)
        put(0x53, KeyEvent.KEYCODE_NUM_LOCK)
        put(0x54, KeyEvent.KEYCODE_NUMPAD_DIVIDE)
        put(0x55, KeyEvent.KEYCODE_NUMPAD_MULTIPLY)
        put(0x56, KeyEvent.KEYCODE_NUMPAD_SUBTRACT)
        put(0x57, KeyEvent.KEYCODE_NUMPAD_ADD)
        put(0x58, KeyEvent.KEYCODE_NUMPAD_ENTER)
        for (i in 0 until 9) put(0x59 + i, KeyEvent.KEYCODE_NUMPAD_1 + i)            // keypad 1..9
        put(0x62, KeyEvent.KEYCODE_NUMPAD_0)
        put(0x63, KeyEvent.KEYCODE_NUMPAD_DOT)
        put(0x65, KeyEvent.KEYCODE_MENU)                                             // Application
        put(0x67, KeyEvent.KEYCODE_NUMPAD_EQUALS)
        put(0x7F, KeyEvent.KEYCODE_VOLUME_MUTE)
        put(0x80, KeyEvent.KEYCODE_VOLUME_UP)
        put(0x81, KeyEvent.KEYCODE_VOLUME_DOWN)
        put(0x85, KeyEvent.KEYCODE_NUMPAD_COMMA)
        put(0xE0, KeyEvent.KEYCODE_CTRL_LEFT)
        put(0xE1, KeyEvent.KEYCODE_SHIFT_LEFT)
        put(0xE2, KeyEvent.KEYCODE_ALT_LEFT)
        put(0xE3, KeyEvent.KEYCODE_META_LEFT)
        put(0xE4, KeyEvent.KEYCODE_CTRL_RIGHT)
        put(0xE5, KeyEvent.KEYCODE_SHIFT_RIGHT)
        put(0xE6, KeyEvent.KEYCODE_ALT_RIGHT)
        put(0xE7, KeyEvent.KEYCODE_META_RIGHT)
    }

    fun keycode(usage: Int): Int? = table[usage]

    /** The meta state a report's modifier byte means: the side's own bit and the either-side one. */
    fun metaState(mods: Int): Int {
        var meta = 0
        if (mods and LCTRL != 0) meta = meta or KeyEvent.META_CTRL_ON or KeyEvent.META_CTRL_LEFT_ON
        if (mods and RCTRL != 0) meta = meta or KeyEvent.META_CTRL_ON or KeyEvent.META_CTRL_RIGHT_ON
        if (mods and LSHIFT != 0) meta = meta or KeyEvent.META_SHIFT_ON or KeyEvent.META_SHIFT_LEFT_ON
        if (mods and RSHIFT != 0) meta = meta or KeyEvent.META_SHIFT_ON or KeyEvent.META_SHIFT_RIGHT_ON
        if (mods and LALT != 0) meta = meta or KeyEvent.META_ALT_ON or KeyEvent.META_ALT_LEFT_ON
        if (mods and RALT != 0) meta = meta or KeyEvent.META_ALT_ON or KeyEvent.META_ALT_RIGHT_ON
        if (mods and LGUI != 0) meta = meta or KeyEvent.META_META_ON or KeyEvent.META_META_LEFT_ON
        if (mods and RGUI != 0) meta = meta or KeyEvent.META_META_ON or KeyEvent.META_META_RIGHT_ON
        return meta
    }

    private const val US = " -=[]\\#;'`,./"
    private const val US_SHIFTED = " _+{}|~:\"~<>?"
    private const val DIGITS_SHIFTED = "!@#$%^&*()"

    /**
     * What a usage types on a US layout, which is the layout Android gives a key event sent from an
     * input method (KeyCharacterMap.VIRTUAL_KEYBOARD). Null for a key that types nothing.
     */
    fun usChar(usage: Int, shifted: Boolean): Char? = when (usage) {
        in 0x04..0x1D -> (if (shifted) 'A' else 'a') + (usage - 0x04)
        in 0x1E..0x27 -> if (shifted) DIGITS_SHIFTED[usage - 0x1E] else "1234567890"[usage - 0x1E]
        0x2C -> ' '
        in 0x2D..0x38 -> (if (shifted) US_SHIFTED else US)[usage - 0x2C].takeIf { usage != 0x32 }
        else -> null
    }
}

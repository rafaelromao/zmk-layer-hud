package io.github.rafaelromao.zmkhud.sink

/**
 * Android's own numbers, as android.view.KeyEvent, android.text.InputType and
 * android.view.inputmethod.EditorInfo publish them (developer.android.com/reference). Copied so this
 * module needs no Android; the app's ConstantsTest holds every one to the SDK's.
 */
object KeyEvent {
    const val KEYCODE_0 = 7
    const val KEYCODE_9 = 16
    const val KEYCODE_DPAD_UP = 19
    const val KEYCODE_DPAD_DOWN = 20
    const val KEYCODE_DPAD_LEFT = 21
    const val KEYCODE_DPAD_RIGHT = 22
    const val KEYCODE_VOLUME_UP = 24
    const val KEYCODE_VOLUME_DOWN = 25
    const val KEYCODE_A = 29
    const val KEYCODE_Z = 54
    const val KEYCODE_COMMA = 55
    const val KEYCODE_PERIOD = 56
    const val KEYCODE_ALT_LEFT = 57
    const val KEYCODE_ALT_RIGHT = 58
    const val KEYCODE_SHIFT_LEFT = 59
    const val KEYCODE_SHIFT_RIGHT = 60
    const val KEYCODE_TAB = 61
    const val KEYCODE_SPACE = 62
    const val KEYCODE_ENTER = 66
    const val KEYCODE_DEL = 67
    const val KEYCODE_GRAVE = 68
    const val KEYCODE_MINUS = 69
    const val KEYCODE_EQUALS = 70
    const val KEYCODE_LEFT_BRACKET = 71
    const val KEYCODE_RIGHT_BRACKET = 72
    const val KEYCODE_BACKSLASH = 73
    const val KEYCODE_SEMICOLON = 74
    const val KEYCODE_APOSTROPHE = 75
    const val KEYCODE_SLASH = 76
    const val KEYCODE_MENU = 82
    const val KEYCODE_MEDIA_PLAY_PAUSE = 85
    const val KEYCODE_MEDIA_STOP = 86
    const val KEYCODE_MEDIA_NEXT = 87
    const val KEYCODE_MEDIA_PREVIOUS = 88
    const val KEYCODE_PAGE_UP = 92
    const val KEYCODE_PAGE_DOWN = 93
    const val KEYCODE_ESCAPE = 111
    const val KEYCODE_FORWARD_DEL = 112
    const val KEYCODE_CTRL_LEFT = 113
    const val KEYCODE_CTRL_RIGHT = 114
    const val KEYCODE_CAPS_LOCK = 115
    const val KEYCODE_SCROLL_LOCK = 116
    const val KEYCODE_META_LEFT = 117
    const val KEYCODE_META_RIGHT = 118
    const val KEYCODE_SYSRQ = 120
    const val KEYCODE_BREAK = 121
    const val KEYCODE_MOVE_HOME = 122
    const val KEYCODE_MOVE_END = 123
    const val KEYCODE_INSERT = 124
    const val KEYCODE_F1 = 131
    const val KEYCODE_F12 = 142
    const val KEYCODE_NUM_LOCK = 143
    const val KEYCODE_NUMPAD_0 = 144
    const val KEYCODE_NUMPAD_1 = 145
    const val KEYCODE_NUMPAD_DIVIDE = 154
    const val KEYCODE_NUMPAD_MULTIPLY = 155
    const val KEYCODE_NUMPAD_SUBTRACT = 156
    const val KEYCODE_NUMPAD_ADD = 157
    const val KEYCODE_NUMPAD_DOT = 158
    const val KEYCODE_NUMPAD_COMMA = 159
    const val KEYCODE_NUMPAD_ENTER = 160
    const val KEYCODE_NUMPAD_EQUALS = 161
    const val KEYCODE_VOLUME_MUTE = 164

    const val META_SHIFT_ON = 0x1
    const val META_ALT_ON = 0x2
    const val META_ALT_LEFT_ON = 0x10
    const val META_ALT_RIGHT_ON = 0x20
    const val META_SHIFT_LEFT_ON = 0x40
    const val META_SHIFT_RIGHT_ON = 0x80
    const val META_CTRL_ON = 0x1000
    const val META_CTRL_LEFT_ON = 0x2000
    const val META_CTRL_RIGHT_ON = 0x4000
    const val META_META_ON = 0x10000
    const val META_META_LEFT_ON = 0x20000
    const val META_META_RIGHT_ON = 0x40000
}

object InputType {
    const val TYPE_NULL = 0
    const val TYPE_MASK_CLASS = 0x0000000f
    const val TYPE_MASK_VARIATION = 0x00000ff0
    const val TYPE_CLASS_TEXT = 1
    const val TYPE_CLASS_NUMBER = 2
    const val TYPE_TEXT_FLAG_MULTI_LINE = 0x00020000
    const val TYPE_TEXT_VARIATION_PASSWORD = 0x00000080
    const val TYPE_TEXT_VARIATION_VISIBLE_PASSWORD = 0x00000090
    const val TYPE_TEXT_VARIATION_WEB_PASSWORD = 0x000000e0
    const val TYPE_NUMBER_VARIATION_PASSWORD = 0x00000010
}

object EditorInfo {
    const val IME_MASK_ACTION = 0x000000ff
    const val IME_ACTION_UNSPECIFIED = 0
    const val IME_ACTION_NONE = 1
    const val IME_ACTION_GO = 2
    const val IME_ACTION_SEARCH = 3
    const val IME_ACTION_SEND = 4
    const val IME_ACTION_NEXT = 5
    const val IME_ACTION_DONE = 6
    const val IME_ACTION_PREVIOUS = 7
    const val IME_FLAG_NO_ENTER_ACTION = 0x40000000
}

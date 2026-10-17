package io.github.rafaelromao.zmkhud

import java.lang.reflect.Modifier
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The sink module copies Android's numbers so it can be tested without Android (sink/Android.kt):
 * every one of them, held here to the SDK's own, by name.
 */
class ConstantsTest {
    private fun same(ours: Class<*>, theirs: Class<*>) {
        val fields = ours.declaredFields.filter { Modifier.isStatic(it.modifiers) && it.type == Int::class.javaPrimitiveType }
        assertTrue("${ours.simpleName} copies something", fields.size > 5)
        for (f in fields) {
            f.isAccessible = true
            assertEquals("${ours.simpleName}.${f.name}", theirs.getField(f.name).getInt(null), f.getInt(null))
        }
    }

    @Test fun keyEvent() = same(io.github.rafaelromao.zmkhud.sink.KeyEvent::class.java, android.view.KeyEvent::class.java)

    @Test fun inputType() = same(io.github.rafaelromao.zmkhud.sink.InputType::class.java, android.text.InputType::class.java)

    @Test fun editorInfo() =
        same(io.github.rafaelromao.zmkhud.sink.EditorInfo::class.java, android.view.inputmethod.EditorInfo::class.java)
}

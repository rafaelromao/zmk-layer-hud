// Every plugin is declared here, once, so :sink's Kotlin and :app's Android, with its built-in Kotlin
// (AGP 9), come from one classloader and see each other's classes.
plugins {
    id("com.android.application") version "9.4.1" apply false
    kotlin("jvm") version "2.4.21" apply false
}

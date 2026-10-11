// What the keyboard does with the board's typing, in plain Kotlin: no Android here, so its tests run
// on any JVM. The app hands it an Editor over the field's InputConnection.
import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    kotlin("jvm")
}

java {
    sourceCompatibility = JavaVersion.VERSION_17
    targetCompatibility = JavaVersion.VERSION_17
}

kotlin {
    compilerOptions {
        jvmTarget.set(JvmTarget.JVM_17)
    }
}

dependencies {
    testImplementation(kotlin("test-junit"))
}

tasks.test {
    // The shared fixtures (apps/fixtures) and the HUD's own tables, read where they are.
    systemProperty("zmkhud.root", rootDir.resolve("../..").canonicalPath)
    testLogging { events("failed"); showStandardStreams = false }
}

// zmk-layer-hud for Android: the HUD as a system keyboard (an input method), docs/tablets.md.
//
//   :sink  what the keyboard does with what the board types -- text, keys, shortcuts -- as plain
//          Kotlin, tested on any JVM: gradle :sink:test
//   :app   the input method and its setup app; needs the Android SDK (ANDROID_HOME, or sdk.dir in
//          local.properties), and is left out of the build where there is none.

pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}

rootProject.name = "zmk-layer-hud"
include(":sink")

val localSdk = java.io.File(settingsDir, "local.properties").takeIf { it.isFile }?.readLines()
    ?.firstOrNull { it.startsWith("sdk.dir=") }?.substringAfter("=")?.trim()
val sdk = System.getenv("ANDROID_HOME") ?: System.getenv("ANDROID_SDK_ROOT") ?: localSdk
if (sdk != null && java.io.File(sdk).isDirectory) {
    include(":app")
} else {
    logger.lifecycle("zmk-layer-hud: no Android SDK (ANDROID_HOME or local.properties sdk.dir): building :sink alone")
}

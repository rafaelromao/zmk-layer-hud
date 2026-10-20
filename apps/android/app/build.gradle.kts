// The input method and its setup app. The board is the HUD's own page (hud/), copied into the app's
// assets at build time as ../hud-files.json lists it, so the keyboard draws and types exactly as the
// desktop HUD does.
import groovy.json.JsonSlurper

plugins {
    id("com.android.application")
}

// The app's version, which release-please sets with each release (release-please-config.json); the
// version code follows from it, so each release installs over the one before.
val appVersion = "1.2.0" // x-release-please-version
val (major, minor, patch) = appVersion.split(".").map { it.toInt() }

// Releases are signed with one key, kept by whoever makes them (docs/development.md): Android installs
// an APK over the one before only when the same key signed both. The keystore and its passwords come
// from the environment -- the release workflow takes them from the repository's secrets -- and
// without them a release build is left unsigned; a debug build is signed with this machine's debug
// key, as always.
val releaseKeystore = System.getenv("ZMKHUD_KEYSTORE")?.takeIf { it.isNotBlank() }

android {
    namespace = "io.github.rafaelromao.zmkhud"
    compileSdk = 36

    defaultConfig {
        applicationId = "io.github.rafaelromao.zmkhud"
        minSdk = 28
        targetSdk = 36
        versionCode = major * 10000 + minor * 100 + patch
        versionName = appVersion
    }

    signingConfigs {
        if (releaseKeystore != null) {
            create("release") {
                storeFile = file(releaseKeystore)
                storePassword = System.getenv("ZMKHUD_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("ZMKHUD_KEY_ALIAS")
                keyPassword = System.getenv("ZMKHUD_KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        getByName("release") {
            if (releaseKeystore != null) signingConfig = signingConfigs.getByName("release")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

dependencies {
    implementation(project(":sink"))
    implementation("androidx.webkit:webkit:1.17.1")
    testImplementation("junit:junit:4.13.2")
}

val hudFiles = (JsonSlurper().parse(rootDir.resolve("../hud-files.json")) as Map<*, *>)["android"] as List<*>

// The HUD's page into the app's assets, under hud/: a generated directory the Variant API wires into
// every variant (AGP 9 takes no provider through the old source-set API).
abstract class SyncHud : DefaultTask() {
    @get:InputFiles
    abstract val pages: ConfigurableFileCollection

    @get:OutputDirectory
    abstract val outputDir: DirectoryProperty

    @TaskAction
    fun copy() {
        val out = outputDir.get().asFile
        out.deleteRecursively()
        val hud = out.resolve("hud").apply { mkdirs() }
        pages.forEach { it.copyTo(hud.resolve(it.name), overwrite = true) }
    }
}

val syncHud = tasks.register<SyncHud>("syncHud") {
    description = "Copies the HUD's page (hud-files.json) into the app's assets."
    pages.from(hudFiles.map { rootDir.resolve("../../hud/$it") })
}

androidComponents {
    onVariants { variant ->
        variant.sources.assets?.addGeneratedSourceDirectory(syncHud, SyncHud::outputDir)
    }
}

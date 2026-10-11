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

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    sourceSets {
        getByName("main") {
            assets.srcDir(layout.buildDirectory.dir("generated/hud-assets"))
        }
    }
}

dependencies {
    implementation(project(":sink"))
    implementation("androidx.webkit:webkit:1.17.1")
    testImplementation("junit:junit:4.13.2")
}

val hudFiles = (JsonSlurper().parse(rootDir.resolve("../hud-files.json")) as Map<*, *>)["android"] as List<*>

val syncHud by tasks.registering(Sync::class) {
    description = "Copies the HUD's page (hud-files.json) into the app's assets."
    from(rootDir.resolve("../../hud")) {
        include(hudFiles.map { it as String })
        into("hud")
    }
    into(layout.buildDirectory.dir("generated/hud-assets"))
}

tasks.named("preBuild") {
    dependsOn(syncHud)
}

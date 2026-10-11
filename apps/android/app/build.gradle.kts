// The input method and its setup app. The board is the HUD's own page (hud/), copied into the app's
// assets at build time as ../hud-files.json lists it, so the keyboard draws and types exactly as the
// desktop HUD does.
import groovy.json.JsonSlurper

plugins {
    id("com.android.application")
}

android {
    namespace = "io.github.rafaelromao.zmkhud"
    compileSdk = 36

    defaultConfig {
        applicationId = "io.github.rafaelromao.zmkhud"
        minSdk = 28
        targetSdk = 36
        versionCode = 1
        versionName = "1.2.0"
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

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("com.chaquo.python")
}

val generatedPythonDir = layout.buildDirectory.dir("generated/ai_dj/python")
val generatedAssetsDir = layout.buildDirectory.dir("generated/ai_dj/assets")

val syncBackendPython by tasks.registering(Sync::class) {
    from("../../backend") {
        include("*.py")
    }
    into(generatedPythonDir)
}

val syncFrontendAssets by tasks.registering(Sync::class) {
    from("../../frontend") {
        exclude("tests/**")
    }
    into(generatedAssetsDir.map { it.dir("frontend") })
}

android {
    namespace = "com.aidj.mobile"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.aidj.mobile"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "0.1.0-dev"

        ndk {
            // Galaxy S21 5G (Snapdragon 888 / Exynos 2100)
            abiFilters += listOf("arm64-v8a")
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    sourceSets {
        getByName("main") {
            assets.srcDir(generatedAssetsDir)
        }
    }
}

kotlin {
    compilerOptions {
        jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17)
    }
}

chaquopy {
    sourceSets {
        getByName("main") {
            srcDir("src/main/python")
            srcDir(generatedPythonDir)
        }
    }
    defaultConfig {
        version = "3.12"
        pip {
            install("-r", "../../backend/requirements-android.txt")
        }
    }
}

tasks.named("preBuild") {
    dependsOn(syncBackendPython, syncFrontendAssets)
}

tasks.matching { it.name.endsWith("PythonSources") }.configureEach {
    dependsOn(syncBackendPython)
}

tasks.matching { it.name.startsWith("merge") && it.name.endsWith("Assets") }.configureEach {
    dependsOn(syncFrontendAssets)
}

dependencies {
    implementation("androidx.core:core-ktx:1.15.0")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("androidx.webkit:webkit:1.12.1")
    implementation("androidx.media3:media3-session:1.5.1")
    implementation("androidx.media3:media3-exoplayer:1.5.1")
    implementation("androidx.media3:media3-common:1.5.1")
    implementation("com.google.guava:guava:33.3.1-android")
}

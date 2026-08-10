plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.kotlin.serialization)
}

android {
    // Application id is PROVISIONAL — see app/README.md "Provisional
    // decisions". Final naming (and therefore the id) is a user/release
    // decision, not an engineering one.
    namespace = "dev.steamvault.app"
    compileSdk = 35

    defaultConfig {
        applicationId = "dev.steamvault.app"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "0.1.0"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    buildFeatures {
        compose = true
    }

    testOptions {
        unitTests {
            isIncludeAndroidResources = false
        }
    }

    lint {
        // AGP lint task is this WP's chosen static-analysis gate (documented
        // in app/README.md — ktlint was the alternative, not used). Treat
        // warnings as build-breaking so the gate is meaningful in CI later.
        warningsAsErrors = true
        abortOnError = true
        // No device/emulator in this environment (WP brief) — baseline
        // profile / resource-shrinker checks that need one are irrelevant
        // at this stage.
        //
        // AndroidGradlePluginVersion / GradleDependency are disabled
        // deliberately, not out of neglect: every version in
        // gradle/libs.versions.toml is pinned to what is contemporaneous
        // with and known-compatible with the Gradle 8.10.2 bootstrap this
        // WP was handed (repo rule: pinned versions only — see the catalog
        // file's header comment for the exact compatibility reasoning).
        // These two checks just nag for "is a newer release out" and would
        // otherwise fail the build every time upstream ships a release,
        // regardless of whether it is actually compatible here — that is
        // a human upgrade decision (a future WP), not a lint-fixable defect.
        disable += setOf("AndroidGradlePluginVersion", "GradleDependency")
    }
}

dependencies {
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.lifecycle.runtime.ktx)
    implementation(libs.androidx.lifecycle.runtime.compose)
    implementation(libs.androidx.activity.compose)
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.compose.foundation)
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.ui.graphics)
    implementation(libs.androidx.compose.ui.tooling.preview)
    implementation(libs.androidx.compose.material3)

    // WP 4b.2: API client + connectivity profiles + credential storage.
    implementation(libs.kotlinx.serialization.json)
    implementation(libs.kotlinx.coroutines.core)
    implementation(libs.okhttp)
    implementation(libs.androidx.security.crypto)

    // WP 4b.3: Steam identity -- CustomTabsIntent for the OpenID login page.
    implementation(libs.androidx.browser)

    // WP 4b.4: Library view -- Steam CDN cover-art loading (see
    // gradle/libs.versions.toml's WP 4b.4 comment for the Coil-vs-hand-rolled
    // justification).
    implementation(libs.coil.compose)

    debugImplementation(libs.androidx.compose.ui.tooling)

    testImplementation(libs.junit)
    testImplementation(libs.okhttp.mockwebserver)
    testImplementation(libs.okhttp.tls)
    testImplementation(libs.kotlinx.coroutines.test)
}

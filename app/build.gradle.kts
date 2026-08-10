// SteamVault Android app — root build script (WP 4b.1).
// Plugins are declared here with apply false and applied per-module, the
// standard AGP/Kotlin DSL convention — keeps version resolution centralized
// in gradle/libs.versions.toml (repo rule: pinned versions only).
plugins {
    alias(libs.plugins.android.application) apply false
    alias(libs.plugins.kotlin.android) apply false
    alias(libs.plugins.kotlin.compose) apply false
    alias(libs.plugins.kotlin.serialization) apply false
}

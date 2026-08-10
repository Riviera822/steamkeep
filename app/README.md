# SteamVault Android app (`app/`)

Kotlin/Compose Android client for SteamVault (Phase 4b, `docs/PROJECT_PLAN.md`
/ `docs/WORKPACKAGES.md`). This directory is a fully self-contained Gradle
project — its own `settings.gradle.kts`, wrapper, and version catalog — and
does not depend on anything elsewhere in the monorepo at build time. It talks
to vault-api over HTTP only (no shared code with `web/`).

Design source of truth: `docs/design/vault-app-mockup.html` +
`vault-app-mockup-NOTES.md` (frozen, see `docs/WORKPACKAGES.md` Phase 4a
header). The Android app is required to stay visually/conceptually
consistent with the web frontend (`web/`) — same palette hex values, same
status-icon kind names.

## What exists after WP 4b.1

This work package ships the **project skeleton, dark theme, and status-icon
component only** — no networking, no navigation, no real screens. The single
screen in the app (`GalleryScreen`) is a debug artifact that renders every
status-icon kind so the theme and component can be visually verified; it is
not part of the shipped app. Real screens (library, downloads, settings,
navigation) arrive in later work packages (4b.4 onward per
`docs/WORKPACKAGES.md`).

```
app/
├── settings.gradle.kts        # root Gradle settings, includes :app
├── build.gradle.kts           # root build script (plugin declarations only)
├── gradle.properties
├── gradle/
│   ├── libs.versions.toml     # pinned version catalog (see below)
│   └── wrapper/               # committed wrapper (jar included, see .gitignore)
├── gradlew / gradlew.bat
├── local.properties           # NOT committed — see "Toolchain setup" below
└── app/                       # the :app module
    ├── build.gradle.kts
    ├── proguard-rules.pro
    └── src/
        ├── main/
        │   ├── AndroidManifest.xml
        │   ├── java/dev/steamvault/app/
        │   │   ├── MainActivity.kt            # single-activity shell
        │   │   └── ui/
        │   │       ├── theme/                 # Color.kt, Type.kt, Theme.kt
        │   │       ├── status/                # status-icon system (see below)
        │   │       └── gallery/               # debug gallery screen
        │   └── res/                           # strings (English), launcher icon, XML theme
        └── test/java/dev/steamvault/app/ui/status/
            └── StatusIconLogicTest.kt         # JVM unit tests, no device needed
```

## Toolchain setup

This project was built and verified with a pinned local toolchain (no
Android Studio, no emulator available in the dev environment):

- JDK 17 (Temurin 17.0.20)
- Android SDK: platform `android-35`, build-tools `35.0.0`, platform-tools
- Gradle 8.10.2 (the wrapper distribution — see
  `gradle/wrapper/gradle-wrapper.properties`), the `-bin` distribution (not
  `-all` — no bundled sources/docs needed for headless builds), with
  `distributionSha256Sum` pinned against the official checksum published at
  `https://services.gradle.org/distributions/gradle-8.10.2-bin.zip.sha256`
  so a compromised/mismatched mirror fails the download instead of silently
  running an unverified Gradle build

`local.properties` (containing `sdk.dir`) is machine-specific and is **never
committed** (`app/.gitignore`). To regenerate it, create the file at
`app/local.properties` with:

```properties
sdk.dir=/absolute/path/to/your/Android/sdk
```

(On Windows, use double backslashes or forward slashes, e.g.
`sdk.dir=C:\\Users\\you\\AppData\\Local\\Android\\sdk`.) Alternatively set the
`ANDROID_SDK_ROOT` / `ANDROID_HOME` environment variable — either satisfies
AGP's SDK discovery.

## Build & test commands

Run from `app/` (the wrapper, `gradlew`/`gradlew.bat`, lives here):

```bash
./gradlew.bat assembleDebug   # builds app-debug.apk
./gradlew.bat test            # JVM unit tests (debug + release variants)
./gradlew.bat lintDebug       # static analysis gate (see "Quality gate" below)
```

No emulator or physical device is available in this environment, so
verification here is build + JVM unit test + lint only — no instrumented
tests, no manual on-device check. `assembleDebug` produces an installable
APK (`app/build/outputs/apk/debug/app-debug.apk`) that has not been run on a
device.

## Quality gate: AGP lint, not ktlint

This WP picks the **AGP `lint` task** as the static-analysis gate (not
ktlint): it is already wired into every AGP module with no extra plugin,
covers both Kotlin style/correctness *and* Android-resource-level issues
(missing content descriptions, resource-qualifier mistakes, etc.), and needs
no additional toolchain download beyond what is already pinned. `lintDebug`
is configured `warningsAsErrors = true, abortOnError = true` (see
`app/app/build.gradle.kts`) so any new warning fails the build the same way
an error would — this is the intended CI gate shape for a later Phase-5 CI
package.

Two lint checks are deliberately disabled project-wide, not silenced by
accident: `AndroidGradlePluginVersion` and `GradleDependency`. Both just flag
"a newer release exists upstream" — which is true and irrelevant here, since
every dependency version in `gradle/libs.versions.toml` was chosen for
*compatibility with the pinned Gradle 8.10.2 bootstrap*, not for recency
(see the catalog file's header comment for the exact reasoning). Leaving
these checks enabled would fail the build every time upstream ships a
release regardless of whether upgrading here is safe — that is a human
upgrade decision for a future work package, not something lint can resolve.

## Versions pinned (`gradle/libs.versions.toml`)

Repo rule (CLAUDE.md / `docs/LEARNINGS.md`): pinned versions only, no
dynamic ranges anywhere.

| Component | Version | Why |
|---|---|---|
| Gradle (wrapper) | 8.10.2 | given bootstrap for this WP |
| Android Gradle Plugin | 8.7.3 | latest 8.7.x patch; AGP 8.7 requires Gradle 8.9+, comfortably under the 8.10.2 wrapper |
| Kotlin | 2.0.21 | latest 2.0.x patch; pairs with the Compose compiler Gradle plugin of the same version |
| Compose compiler plugin | 2.0.21 (= Kotlin version, required) | `org.jetbrains.kotlin.plugin.compose` must match the Kotlin version it compiles with |
| Compose BOM | 2024.10.01 | contemporaneous with Kotlin 2.0.21 / AGP 8.7.3 — a newer BOM risks needing AGP/compileSdk features this toolchain combination doesn't have |
| compileSdk / targetSdk | 35 | per WP brief |
| minSdk | 26 | per WP brief (also the exact API level `ValueAnimator.areAnimatorsEnabled()` — the reduced-motion signal — has been available since) |
| Java | 17 | matches the pinned JDK |

## Provisional decisions (not final)

- **Application id**: `dev.steamvault.app`. This is a placeholder —
  final naming (and therefore the id, since it cannot be changed later
  without a fresh Play/F-Droid listing) is a user/release decision per
  `docs/WORKPACKAGES.md` Phase 5, not an engineering one made here.
- **Launcher icon**: a plain generic vault-shield vector glyph (accent
  colour, no Steam trade dress — matches the mockup's "no Valve/Steam
  logos... the app mark is a generic vault shield" rule). Not a final app
  icon; exists only so the app installs and launches with *something*.

## Security note: `android:allowBackup="false"`

Set from this first work package, even though nothing sensitive is stored
yet. A later WP (4b.2, the API client) will store a vault-api key in this
app's private storage; Android's default Auto Backup
(`android:allowBackup="true"`) would copy that key into the user's cloud
backup and restore it onto any device that later signs into the same
account — an off-device leak of a credential the user typed in specifically
to talk to their own homelab server. Setting it correctly now avoids a
silent regression the day the key is added.

## Theme (`ui/theme/`)

`Color.kt` ports every hex value from `web/css/theme.css`'s `:root` palette
byte-for-byte (see the file's own comments for the `--custom-property`
mapping). The app is committed to a single dark theme on purpose — the same
design decision as the web frontend (mockup-notes.md "Committed to dark, on
purpose") — so `Theme.kt` does **not** read `isSystemInDarkTheme()`; the
scheme is always the same regardless of the device's system theme setting.

`Type.kt` covers typography basics only. The mockup's type stack (Roboto /
Roboto Condensed / Roboto Mono, no bundled webfonts) is approximated with
`FontFamily.Default` (renders as system Roboto on every targeted device) and
`FontFamily.Monospace` for the numeric/id role; a real Roboto
Condensed/Mono asset pair is deferred to whichever later WP first needs
capsule-art typography (4b.4, the library grid) — not needed by this WP's
one debug screen.

## Status-icon system (`ui/status/`)

Ports `web/js/components/status-icon.js` + the `.sic` rules in
`web/css/theme.css`:

- **`StatusKind.kt`** — the ten kinds (`cached`, `running`, `updating`,
  `stale`, `none`, `paused`, `verify`, `error`, `warn`, `cancelled`), 1:1
  with web's `STATUS_LABEL` keys (`wireName`), each with an English string
  resource for the label. `fromWireName()` falls back to `NONE` for an
  unrecognized kind, mirroring the web component's own fallback.
- **`StatusIconLogic.kt`** — pure, Android-framework-free functions:
  `glyphFor(kind)` (kind → glyph shape), `backgroundFor(kind)` /
  `inkFor(kind)` (colours), and the two functions the WP brief specifically
  asks for as testable pure logic:
  - **`shouldAnimate(kind, animatorsEnabled)`** — the animate-or-not
    decision. Only `running`/`updating`/`verify` ever animate, and only
    when `animatorsEnabled` is true.
  - `downloadDriftFraction` / `downloadOpacityFraction` — the download
    glyph's keyframe math (ported from the CSS `vault-dlslide` keyframes),
    extracted as pure functions for the same reason.
- **`AnimatorsEnabled.kt`** — documents and implements *how Compose picks
  up the system reduced-motion setting* (see below).
- **`StatusIcon.kt`** — the Compose composable, drawing each glyph on a
  `Canvas` from the same 24×24-unit coordinate data as the SVG paths in
  status-icon.js. CHECK/DOWNLOAD/BANG/PAUSE/STOP are a direct
  coordinate-for-coordinate port. **REFRESH is a documented geometric
  approximation** (two circular arcs + chevrons rather than porting SVG
  elliptical-arc flag math into Compose's `Path.addArc`) — see the
  composable's kdoc. It preserves the design intent ("two opposing curved
  arrows forming one circle") without being pixel-identical to the SVG.

### How reduced motion is honoured

Compose's own animation APIs (`InfiniteTransition`, `animate*AsState`, …) do
**not** automatically respect the platform's "Remove animations"
accessibility toggle or Settings → Developer options → Animator duration
scale — those only gate the legacy `android.animation` framework
automatically. The documented way for a Compose app to see the same signal
is `ValueAnimator.areAnimatorsEnabled()` (public since API 26 — this app's
exact minSdk), which reads `Settings.Global.ANIMATOR_DURATION_SCALE` and
returns `false` when the scale is 0 (the value both the toggle and the
developer option write).

`AnimatorsEnabled.kt`'s `rememberAnimatorsEnabled()` wraps that check in a
live Compose `State`, updated via a `ContentObserver` on the backing
`Settings.Global` URI, so toggling the setting while a screen is open
updates the icons immediately. The actual go/no-go decision is the pure
`shouldAnimate()` function — the composable is just where the live boolean
comes from.

The disable path is proven in `StatusIconLogicTest.kt` without a device: the
tests pin that `running`/`updating`/`verify` all return `false` from
`shouldAnimate` when `animatorsEnabled = false`, alongside pinning that they
return `true` when it's `true`, and that every other kind never animates
either way — the fail-closed-direction discipline from
`docs/LEARNINGS.md` ("Testing discipline": pin the default direction, not
just the happy path).

## Tests

`app/app/src/test/java/dev/steamvault/app/ui/status/StatusIconLogicTest.kt`
— 25 JVM unit tests, no Robolectric/emulator dependency, covering:

- icon-kind → glyph mapping (every `StatusKind`, pinned by name)
- wire-name round trip + unknown-kind fallback to `none`
- the animate-or-not decision in both directions, including the
  reduced-motion disable path, for every kind
- the download glyph's drift/opacity keyframe math at its boundary values,
  including the "must never reach fully transparent" invariant (mockup
  round 7: "a status icon must never be blank" — it doubles as a tap
  target in later WPs)

Verified command + output tail (debug + release variants both run under
`test`):

```
$ ./gradlew.bat test
...
BUILD SUCCESSFUL in 7s
45 actionable tasks: 20 executed, 25 up-to-date
```

`app/build/test-results/testDebugUnitTest/TEST-dev.steamvault.app.ui.status.StatusIconLogicTest.xml`
and the `testReleaseUnitTest` counterpart both report
`tests="25" skipped="0" failures="0" errors="0"`.

```
$ ./gradlew.bat assembleDebug
...
BUILD SUCCESSFUL in 5s
35 actionable tasks: 7 executed, 28 up-to-date
```

```
$ ./gradlew.bat lintDebug
...
BUILD SUCCESSFUL in 32s
26 actionable tasks: 26 executed
```
(`app/app/build/reports/lint-results-debug.txt`: "No issues found.")

## What this WP deliberately does NOT do

- No networking, no vault-api client (WP 4b.2).
- No real navigation / bottom nav / multiple destinations (later 4b.x WPs).
- No library/downloads/settings screens — only the debug gallery.
- No instrumented (on-device) tests — no emulator/device is available in
  this environment; verification is build + JVM unit test + lint only.
- No release signing config (WP 4b.9).
- REFRESH glyph is a geometric approximation of the SVG source, not an
  exact port (documented above and in `StatusIcon.kt`'s kdoc) — worth a
  visual check once a device/emulator is available.

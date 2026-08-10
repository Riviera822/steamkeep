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

## Tests (WP 4b.1: status-icon system)

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

## What WP 4b.1 deliberately did NOT do

- ~~No networking, no vault-api client~~ — done in WP 4b.2, below.
- No real navigation / bottom nav / multiple destinations (later 4b.x WPs).
- No library/downloads/settings screens — only the debug gallery.
- No instrumented (on-device) tests — no emulator/device is available in
  this environment; verification is build + JVM unit test + lint only.
- No release signing config (WP 4b.9).
- REFRESH glyph is a geometric approximation of the SVG source, not an
  exact port (documented above and in `StatusIcon.kt`'s kdoc) — worth a
  visual check once a device/emulator is available.

## API client, connectivity profiles, credential storage (WP 4b.2)

Serial after 4b.1 per `docs/WORKPACKAGES.md` Phase 4b. Adds the app's
entire vault-api HTTP surface, still with no UI consuming it yet (that
starts at 4b.3/4b.4/4b.5) — everything below is a library the next work
packages build screens on top of.

```
app/app/src/main/java/dev/steamvault/app/
├── net/
│   ├── VaultJson.kt                # the one kotlinx.serialization Json instance
│   ├── VaultApiClient.kt           # the OkHttp client, one suspend fun per endpoint
│   ├── model/                      # DTOs mirroring vault_api's Pydantic models verbatim
│   │   ├── Health.kt Games.kt Jobs.kt Cache.kt Clients.kt Settings.kt
│   ├── error/
│   │   └── VaultApiError.kt        # the six-kind error taxonomy (sealed class)
│   └── profile/
│       ├── ConnectivityProfile.kt          # the interface + SystemVpnProfile + PublicDomainProfile
│       └── CleartextPolicyInterceptor.kt   # second, OkHttp-level cleartext gate
├── storage/
│   ├── CredentialStore.kt          # the interface (+ ProfileKind constants)
│   └── EncryptedCredentialStore.kt # the real, EncryptedSharedPreferences-backed impl
├── repo/
│   ├── GamesRepository.kt JobsRepository.kt ClientsRepository.kt
└── polling/
    ├── PollingIntervals.kt         # pure "how often should the app poll" decisions
    └── Backoff.kt                  # pure exponential-backoff-with-jitter math
```

### API client (`net/`)

`VaultApiClient` wraps the `/v1` surface the app's later work packages need:
games incl. detail, jobs + control (prefill/cancel/pause/resume), cache
summary/delete, gc, clients, settings GET/PATCH, and health. Every method
is a one-line `suspend fun` — see the class kdoc in
`VaultApiClient.kt` for the full list and for what is DELIBERATELY not
wrapped:

- **`/v1/mapping`** — no current caller (same "add it with the WP that
  needs it" rule `web/js/api.js` documents for the web client).
- **`/v1/steam/*`** (the Steam Web API relay) — excluded on purpose, not
  by omission. ADR-0004 (`api/README.md` "Steam Web API relay") keeps the
  Android app on its OWN device-local `GetOwnedGames` call (WP 4b.3); the
  relay exists only because the web UI has no CORS story for calling Valve
  directly, a constraint that does not apply to a native app.

`X-Api-Key` is attached to every request, including `/v1/health` — the
same choice `web/js/api.js`'s `request()` makes, rather than special-casing
the one route `api/README.md` documents as unauthenticated.

### DTOs (`net/model/`)

One `@Serializable` data class per Pydantic response/request model in
`vault_api/routers/*.py` (read at git HEAD for this WP), with field names
kept **verbatim** — snake_case, matching the wire JSON exactly, no
camelCase renaming layer — so a payload can be compared against
`api/README.md`'s "Endpoints" table or the router source without a mental
mapping step (the same decision `web/js/api.js` documents for the web
client). `VaultJson` sets `ignoreUnknownKeys = true` **deliberately**: the
apps/jobs schema has grown fields release over release (v4 through v13 in
`api/README.md`'s own history), and this client must not hard-fail the
day a future `api/` work package adds one more — every field this client
doesn't itself need has a Kotlin default so an older or newer server both
decode fine (pinned by `SerializationRoundTripTest`'s explicit "unknown
future field is ignored" case). `encodeDefaults = true` is the matching
decision on the way OUT — see `VaultJson.kt`'s kdoc for why (a
`GcRequest(execute = false)` would otherwise encode as `{}`, relying on
vault-api's own default happening to agree).

`GET`/`PATCH /v1/settings` (ADR-0009) is the one genuinely heterogeneous
shape: `effective`/`fallback` are a string for most keys, an int for the
two schedule-numeric keys, a JSON array of strings for `webhook_events`,
or `null`. `SettingInfoOut` models those two fields as
`kotlinx.serialization.json.JsonElement` rather than picking one scalar
type — `Settings.kt`'s `settingAsStringOrNull`/`settingAsIntOrNull`/
`settingAsBooleanOrNull`/`settingAsStringListOrNull` give typed access
without every call site re-deriving the same `when`.

### The six-kind error taxonomy (`net/error/VaultApiError.kt`)

A `sealed class` with one subclass per kind — `Network`, `Auth`,
`NotFound`, `Validation`, `Server`, `Unknown` — carrying the SAME kind
names `web/js/errors.js`'s `ERROR_KINDS` uses (`network`, `auth`,
`not_found`, `validation`, `server`, `unknown`), including the same
`classifyHttpStatus` boundary choices (401 → auth, 404 → not_found, ≥500 →
server, ≥400 → validation — so 409/422 both fold into `validation`, same
as the web client — else → unknown). `VaultApiErrorTaxonomyContractTest`
pins the kind names against hand-transcribed literals, never derived from
the enum itself (`docs/LEARNINGS.md` "Android (Phase 4b)": "a derived
round-trip is circular and cannot detect drift from the other frontend" —
same technique `StatusIconCrossFrontendContractTest` uses for
`StatusKind`).

### Connectivity profiles and the cleartext policy (`net/profile/`)

One interface, `ConnectivityProfile` (a base URL plus whether cleartext
HTTP is allowed), with two implementations now:

- **`SystemVpnProfile`** — the OS routes this directly (LAN or a
  Tailscale/VPN interface); plain HTTP to whatever IP/hostname the user
  entered is accepted, since there is no public CA-signed certificate
  story for a private address. First profile per
  `docs/WORKPACKAGES.md` Phase 4b ("System-VPN profile first").
- **`PublicDomainProfile`** — HTTPS only. Constructing one with an
  `http://` base URL throws `CleartextNotAllowedException` **at
  construction**, before any `Request` object exists.

`tsnet` (an embedded userspace Tailscale client) is explicitly **post-v1**
(`docs/WORKPACKAGES.md` Phase 4b) — no dependency, no stub class, just the
interface seam a future `TsnetProfile` would implement.

**The cleartext tradeoff, stated plainly:** Android's Network Security
Config can only scope a cleartext exception by exact domain/wildcard
(`<domain-config>`), never by IP range — so it cannot express "cleartext
only for whatever LAN address the user typed in". `res/xml/
network_security_config.xml` therefore ships a blanket
`cleartextTrafficPermitted="true"` `<base-config>` (with a scoped
`tools:ignore="InsecureBaseConfiguration"`, not a project-wide lint
disable), and the real "only `SystemVpnProfile` may actually use it"
restriction is enforced ONE LAYER UP, in application code.

**BLOCKER fix (Opus review round 1).** The first version of this WP got
this wrong: it registered `CleartextPolicyInterceptor` ONLY as an OkHttp
application interceptor and claimed — falsely — that this alone would
catch a redirect to an `http://` `Location`. Empirically demonstrated
against the pinned OkHttp 4.12.0: an application interceptor wraps an
entire call and runs exactly once, seeing only the ORIGINAL request —
OkHttp's own `RetryAndFollowUpInterceptor` follows redirects internally,
beneath that layer, so it never saw a redirect's target at all. Combined
with `X-Api-Key` not being an `Authorization`-class header (so OkHttp does
NOT strip it on a host/scheme change) and `followSslRedirects` defaulting
to `true`, a `PublicDomainProfile` client would have silently followed an
`https://` response's `Location: http://attacker` redirect and sent the
API key in cleartext. Fixed with three independent, stacked layers, all
applied unconditionally on `VaultApiClient`'s own wrapping `OkHttpClient`
(so an injected `OkHttpClient` — tests, or a future caller — cannot lose
any of them by construction):

1. **`followSslRedirects(false)`** — refuse to auto-follow an https<->http
   redirect at all; OkHttp then never builds the second request in the
   first place. Primary fix for the https-to-http case specifically.
2. **`PublicDomainProfile`'s constructor guard** (above) — the pre-socket
   gate for the ORIGINAL request, before any HTTP machinery exists.
3. **`CleartextPolicyInterceptor` registered TWICE** — once as an
   application interceptor (same pre-socket coverage as (2), for the
   original request) AND once as a NETWORK interceptor
   (`addNetworkInterceptor`), which runs once per actual request OkHttp
   puts on the wire, INCLUDING every redirect hop and any other
   OkHttp-internal follow-up (e.g. an auth-challenge retry) that likewise
   skips application interceptors. This is the layer that does not depend
   on (1) staying correctly configured forever.

`ConnectivityProfileTest` pins both `PublicDomainProfile`'s guard and the
interceptor (exercised directly against a fake `Interceptor.Chain`,
proving `chain.proceed()` — the call that would open a socket — is never
reached for a blocked request), including the brief's named case verbatim:
"`PublicDomainProfile` + `http://` URL must throw before any socket I/O".
`VaultApiClientTest`'s `https to http redirect never reaches hop 2 for
PublicDomainProfile` is the end-to-end pin the review asked for: two real
`MockWebServer`s (hop 1 HTTPS via `okhttp-tls`'s `HeldCertificate`/
`HandshakeCertificates`, hop 2 plain HTTP), hop 1 answers with a `302` to
hop 2, and the test asserts hop 2's request count stays exactly `0` (with
a bounded `takeRequest` as a second check) — the canary API key
(`apiKeyProvider`) therefore never has anything hop 2 recorded to appear
in. One environment-specific snag worth recording: `MockWebServer.url()`
derives its host from a reverse DNS lookup of the loopback address, which
on this project's dev machine resolves to `lancache.steamcontent.com` (the
lancache DNS override `core/vault-core`'s own PoC relies on) rather than
`localhost` — the test builds both hop URLs explicitly against
`localhost:<port>` instead of trusting `.url()`'s host.

**Delta-review fixes (Opus review round 2 — S1/S2/S3).** The round-1 fix
above was still incomplete, and its own test coverage was weaker than it
looked:

- **S2 (security).** `followSslRedirects(false)` only refuses a SCHEME
  change (https<->http) — an https-to-https redirect to a DIFFERENT HOST
  is a same-scheme redirect that flag does not touch, and `X-Api-Key`
  would still be forwarded to it (still not an `Authorization`-class
  header OkHttp strips on a host change). Fixed with `.followRedirects(false)`
  alongside `.followSslRedirects(false)`, in both `defaultOkHttpClient()`
  and `VaultApiClient`'s own re-applying wrapper — no redirect is ever a
  legitimate outcome for this client's fixed `/v1/...` paths, same-scheme
  or not. Pinned by `VaultApiClientTest`'s `S2 -- https to https CROSS-HOST
  redirect` test: two HTTPS `MockWebServer`s on different ports (same test
  certificate, since TLS SANs are hostname-only), hop 1 redirects to hop 2,
  hop 2's request count stays `0`.
- **S1 (test rigor).** The round-1 end-to-end test could not actually tell
  the flag layer and the interceptor layer apart: against that ONE
  scenario, either layer alone is independently sufficient, so removing
  either one (leaving the other) still passes it — a claim that both
  layers are individually pinned would have been wrong. Fixed with two
  standalone tests: `S1a` builds a client carrying ONLY
  `CleartextPolicyInterceptor` (as a network interceptor) with BOTH
  redirect flags explicitly left at OkHttp's insecure default (`true`),
  proving the interceptor alone blocks the downgrade (hop 2 never records
  a request, even though a raw TCP connection may open — network
  interceptors run after connection setup but before any HTTP bytes are
  written, see the test's own comment for exactly what that does and does
  not prove); `S1b` inspects `VaultApiClient.debugHttpClientForTesting`
  (an `internal` test-only accessor) and asserts `followRedirects`/
  `followSslRedirects` are actually `false` on the built client — a pure
  configuration check, no network involved, that also finally exercises
  `defaultOkHttpClient()` directly rather than always overriding it.
- **S3 (test hygiene).** All four redirect tests now wrap their
  `MockWebServer` pairs in `try`/`finally` — a failing assertion no longer
  leaks two listening servers for the rest of the test JVM fork's
  lifetime. A shared private `TlsFixture` helper (one `HeldCertificate` +
  server/client `HandshakeCertificates`) also replaced three copies of the
  same certificate-setup boilerplate.

### Credential storage (`storage/`)

`CredentialStore` is the interface (API key, base URL, connectivity-profile
kind); `EncryptedCredentialStore` is the real implementation, backed by
`androidx.security-crypto`'s `EncryptedSharedPreferences` +
`MasterKey.Builder` — the vault-api key never lands in a plain,
unencrypted `SharedPreferences` file (the WP 4b.1 backup-posture note:
`allowBackup="false"` is already in place for exactly this class of
secret).

The interface is extracted specifically so tests run on the JVM: an
`InMemoryCredentialStore` fake (test sources only, a plain `Map`) is what
everything depending on `CredentialStore` is tested against.
`EncryptedCredentialStore` itself needs a real Android Keystore, which
this environment does not have (no emulator/device — unchanged from
WP 4b.1). Its one guarantee is pinned STRUCTURALLY instead:
`EncryptedCredentialStoreSourceTest` reads `EncryptedCredentialStore.kt`'s
own source text (the same lightweight technique
`StatusIconCrossFrontendContractTest` uses for `strings.xml`/`colors.xml`,
applied to a `.kt` file) and asserts no bare, plain preferences lookup
appears anywhere in it — a regression that "fixed" an
`EncryptedSharedPreferences.create` failure by silently falling back to
plaintext (a real, documented historical footgun with this API) fails
this test immediately, without needing a device. This is an honest,
narrower guarantee than a runtime test would give, stated as such rather
than hidden.

### Polling primitives (`polling/`) — decisions only, no scheduler yet

`PollingIntervals` and `Backoff`/`BackoffState` are direct, pure ports of
`web/js/store.js`'s `hasActiveJob`/`nextJobsIntervalMs`/
`DEFAULT_INTERVALS` and `web/js/backoff.js`'s `computeBackoffDelay`/
`createBackoffState` — same numbers (2 s/15 s/15 s/20 s cadence, 1 s
base / 30 s cap / 20% jitter backoff), so the Android app polls on the
same cadence the web UI does. **WorkManager wiring — the thing that
actually calls these on a schedule and respects Doze — is WP 4b.8, not
this WP.** `GamesRepository`/`JobsRepository`/`ClientsRepository` are the
thin suspend-based seams that future wiring calls through; they exist now
so 4b.4/4b.5/4b.3 have something to build view-models against without
waiting on 4b.8.

### Versions pinned for this WP

Added to the existing `gradle/libs.versions.toml` table:

| Component | Version | Why |
|---|---|---|
| kotlinx-serialization-json | 1.7.3 | latest patch contemporaneous with Kotlin 2.0.21 (K2 plugin model) |
| kotlinx-coroutines-core | 1.9.0 | same reasoning, contemporaneous with Kotlin 2.0.21 |
| OkHttp | 4.12.0 | current stable 4.x line (5.x still pre-release at the time of this WP) |
| OkHttp MockWebServer | 4.12.0 | test-only, pinned to the same version as OkHttp itself |
| OkHttp TLS (`okhttp-tls`) | 4.12.0 | test-only, same version again — added in the Opus review round for the redirect-leak pin's real HTTPS `MockWebServer` (`HeldCertificate`/`HandshakeCertificates`) |
| androidx.security:security-crypto | 1.1.0-alpha06 | the `MasterKey` builder API (1.0.0's `MasterKeys` helper is deprecated); no stable 1.1.0 GA exists yet — a documented, narrow exception to "stable only" |

### Tests (WP 4b.2)

94 new JVM unit tests (124 total with WP 4b.1's 30 —
`StatusIconLogicTest`'s 25 plus `StatusIconCrossFrontendContractTest`'s 5),
no
Robolectric/emulator dependency:

- `net/error/VaultApiErrorTaxonomyContractTest` — the six kind names
  pinned literally, `classifyHttpStatus` boundaries in both directions
  (401/404/409/422/5xx/sub-400).
- `net/SerializationRoundTripTest` — one round trip per DTO, modeled on
  `api/README.md`'s documented shapes (synthetic data — see the file's own
  header for why no single README curl transcript covers every current
  field after the v4→v13 schema history), plus the "unknown field is
  ignored" case. Each fixture is ALSO decoded through a test-only strict
  `Json { ignoreUnknownKeys = false }` alongside production `VaultJson`
  (Opus review should-fix: `ignoreUnknownKeys = true` in production would
  otherwise let a typo'd fixture key silently vanish instead of failing
  the anti-drift check it's supposed to be) — see the file's class kdoc
  for exactly what that strict pass does and does NOT catch (a fixture
  that OMITS a field with a Kotlin default is absorbed by design either
  way). `JobControlOut`'s anchor test lifts its fixture verbatim from
  `api/tests/test_job_control.py`'s own asserted response body.
- `net/model/SettingValueTest` — the `JsonElement` typed-access helpers,
  including the "numeric content is not a string" trap the first version
  of `settingAsStringListOrNull` got wrong (fixed before this report; see
  the function's inline comment).
- `net/VaultApiClientTest` — MockWebServer-backed: headers, method/path,
  request-body encoding (incl. the dry-run-by-default GC body and a
  mixed set+clear settings PATCH), error mapping for
  401/404/409/422/500 plus a genuine connection failure for `network`,
  and four redirect-leak pins (BLOCKER B1 + delta S1/S2, see "Connectivity
  profiles" above): the https-to-http end-to-end pin, the https-to-https
  cross-host pin (S2), the interceptor-alone pin (S1a), and the
  redirect-flags configuration assertion (S1b).
- `net/profile/ConnectivityProfileTest` — both cleartext-policy layers,
  including the brief's named "throws before any socket I/O" case.
- `polling/BackoffTest` / `polling/PollingIntervalsTest` — growth/cap/
  jitter math both directions, and the fast/slow cadence decision. The
  jitter-floor test now uses `jitterRatio=1.5` (mirroring
  `web/tests/backoff.test.js`'s own load-bearing case exactly), not the
  original `jitterRatio=1.0` version, which the Opus review found was
  VACUOUS — it lands on exactly `0` whether or not the `max(0.0, ...)`
  floor in `Backoff.kt` runs at all, so deleting that floor still passed
  every test in the file.
- `storage/InMemoryCredentialStoreTest` — the fake's own contract.
- `storage/EncryptedCredentialStoreSourceTest` — the structural pin
  described above.

Verified command + output tail (from-scratch `clean test lintDebug`):

```
$ ./gradlew.bat clean test lintDebug
...
BUILD SUCCESSFUL in 1m
56 actionable tasks: 56 executed
```

`124 tests completed, 0 failed` across all 11 test classes (verified via
the `testDebugUnitTest` XML reports' `tests=`/`failures=`/`errors=`
attributes, summed: 124/0/0); `app/app/build/reports/lint-results-debug.txt`:
"No issues found."

### What WP 4b.2 deliberately did NOT do

- **No WorkManager / background polling scheduler.** `polling/` is pure
  decision functions only — WP 4b.8.
- **No UI.** Nothing in `app/app/src/main/java/.../ui/` consumes any of
  this yet — the debug gallery screen is unchanged.
- **No `/v1/mapping` or `/v1/steam/*` client methods** — see the "API
  client" section above for why both are deliberate exclusions, not gaps.
- **No `tsnet` profile, no dependency, no stub class** — post-v1 per
  `docs/WORKPACKAGES.md`; only the `ConnectivityProfile` seam exists.
- **No instrumented test of `EncryptedCredentialStore`** — no
  emulator/device available; its one guarantee is pinned structurally
  instead (see "Credential storage" above), an explicitly narrower bar
  than a runtime test would clear.
- **No settings/profile picker UI** to choose between `SystemVpnProfile`
  and `PublicDomainProfile` or to write into `CredentialStore` — later
  onboarding/settings work (4b.7).

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

## Conventions

### String resources

AGP lint's `HardcodedText` check only inspects XML layouts — a plain Kotlin
string literal passed to a Compose `Text(...)` call is invisible to it (see
`GalleryScreen.kt`'s `kind.wireName` for a pre-existing, deliberate example
that always passed `lintDebug`). So this is a HUMAN rule, not a lint-enforced
one — write it down here instead of re-deriving it per work package (WP 4b.4
review fix, after 27 literals had to be triaged one at a time).

**Default: static UI chrome belongs in `strings.xml`.** Screen titles,
button labels, placeholder/empty-state copy, toast text, error-fallback
text — anything a real user reads that isn't itself DATA (a game name, a
computed byte count, a server error detail) — is a string resource,
resolved via `stringResource`/`pluralStringResource` from a `@Composable`
context. This is what every screen before WP 4b.4 already did
(`IdentityScreen.kt`, `GalleryScreen.kt`) and what WP 4b.4's own toasts and
placeholder text were moved to match (`LibraryController`'s toasts were
originally inline Kotlin literals; see `ui/library/LibraryStrings.kt`).

**When the string is only known outside composition** (e.g. inside a
`scope.launch { }` block after a suspend network call returns — job counts,
freed bytes, failure counts), a plain Kotlin class/object CANNOT call
`stringResource` — it is `@Composable`-only. The fix is NOT to fall back to
a literal: define a small interface (`LibraryStrings` is the pattern) with
one method per message, implement it against `android.content.res.Resources`
(which has plain, non-Composable `getString`/`getQuantityString` methods),
and inject it into the plain-Kotlin class the same way `CredentialStore`/
`LibraryPreferences` are injected — so the class stays off-device-testable
against a fake implementation.

**Narrow exception: a verbatim, diffable port of a web module's own
literal.** `BulkPlan.kt`'s button labels/notes and `LibraryFilters.kt`'s
chip labels stay Kotlin string literals, not resources — they are
line-for-line ports of `web/js/lib/bulk-plan.js` / `library-filters.js`'s
own hardcoded strings, and the entire point of porting them verbatim is
that the correspondence can be read directly off two side-by-side literals;
resource indirection would hide that diff. This exception applies ONLY
when BOTH of the following hold, and each qualifying file's kdoc must say
so explicitly:
  1. the string's wording is "whatever the web source already decided", not
     an independent Android UI copy decision;
  2. a test pins the literal by STRING EQUALITY against a hand-transcribed
     expected value (never derived from the constant under test — same
     "literal-vs-literal" rule docs/LEARNINGS.md's Android section already
     requires for wire-format/status-word cross-frontend contracts).
A string that fails either test is static UI chrome and belongs in
`strings.xml`, full stop — "it happens to also appear in a `web/js/lib/`
file" is not by itself a reason to keep it out of resources.

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

## Steam identity — OpenID + GetOwnedGames on-device (WP 4b.3)

Branch-parallel after 4b.2 per `docs/WORKPACKAGES.md`. Implements
ADR-0004 decision 2 end to end on the Android side: "Sign in with Steam"
resolves to a SteamID64 without this app ever seeing a password, and the
user's own Steam Web API key (entered manually, never obtained via OpenID)
drives an on-device `GetOwnedGames` call that never touches vault-api.

```
app/app/src/main/java/dev/steamvault/app/
├── net/
│   ├── steam/
│   │   ├── SteamId64.kt                # SteamID64 validator (mirrors web/api)
│   │   ├── SteamOpenIdLoginUrl.kt      # SteamOpenIdConfig + checkid_setup URL builder
│   │   ├── SteamOpenIdCallback.kt      # callback parsing + signed-fields check (pure)
│   │   ├── SteamOpenIdClient.kt        # check_authentication network call
│   │   └── SteamWebApiClient.kt        # GetOwnedGames / GetPlayerSummaries + SteamWebApiError
│   └── model/
│       └── SteamWebApi.kt              # OwnedGame/SteamPersona + hostile-fixture parsers
├── repo/
│   └── SteamIdentityRepository.kt      # login state, sign-in/out, library count preview
└── ui/identity/
    └── IdentityScreen.kt               # sign-in / signed-in + sign-out + count preview
```

(This tree reflects the layout AS OF WP 4b.3. `IdentityScreen.kt` was later
moved to `app/app/src/debug/java/dev/steamvault/app/ui/identity/
IdentityScreen.kt` in WP 4b.9, once WP 4b.7 had made it unreachable from
the UI — see this file's own "Carry-over cleanup (WP 4b.9)" section below
for why and its current, correct path.)

`storage/CredentialStore.kt` gained three fields (`steamId64`,
`steamPersonaName`, `steamWebApiKey`) plus `clearSteamIdentity()` — a
narrower sign-out than `clear()`, which stays reserved for "forget this
vault entirely" (untouched by signing out of Steam).

### OpenID login flow

1. `MainActivity` builds the `checkid_setup` URL
   (`SteamIdentityRepository.buildLoginUrl()` → `SteamOpenIdLoginUrl.build()`)
   and opens it in an `androidx.browser` Custom Tab.
2. Valve redirects the Custom Tab back to this app's own custom scheme
   (`return_to`/`intent-filter` design below); `MainActivity.onNewIntent`
   receives it and hands the raw callback URL to
   `SteamIdentityRepository.completeLogin`.
3. `SteamOpenIdCallback.parse` extracts the full `openid.*` parameter map
   (shape checks only — this proves nothing about authenticity, since any
   app can send SteamVault an `Intent` naming this scheme).
4. `SteamOpenIdCallback.signedCoversClaimedId` checks that
   `openid.signed` actually lists `claimed_id` — the OpenID 2.0
   requirement that is easy to skip if a caller stops at a bare
   `is_valid:true`.
5. `SteamOpenIdClient.checkAuthentication` POSTs every extracted param
   back to `https://steamcommunity.com/openid/login` with
   `openid.mode=check_authentication` and requires a **strict, exact**
   `is_valid:true` line in the response — this is the step that actually
   proves the callback was not forged.
6. `SteamOpenIdCallback.steamId64From` extracts and validates the
   SteamID64 out of `openid.claimed_id` (`SteamId64.validate` — 17 ASCII
   decimal digits, individual-account range). Only on success is anything
   persisted (`CredentialStore.setSteamId64`).

### `return_to`/intent-filter design (WP brief: "pick a scheme, document it")

**Chose a custom `steamvault://` scheme, not an `https://` return_to.**
SteamVault has no hosted web presence — `vault-api` lives on the user's
LAN/VPN, never a public domain by default — so there is no HTTPS endpoint
this app could register as a redirect target. A custom-scheme deep link is
the standard native-app OpenID/OAuth pattern for exactly this situation.

- `RETURN_TO = REALM = "steamvault://auth/openid-return"` (`realm` equals
  `return_to` exactly — an explicitly permitted degenerate case of OpenID
  2.0's realm-matching rules, since this app has no wildcard-subdomain
  need).
- `AndroidManifest.xml`'s `MainActivity` carries a second intent-filter:
  `action=VIEW`, `category=DEFAULT,BROWSABLE`,
  `data android:scheme="steamvault" android:host="auth" android:path="/openid-return"`,
  plus `android:launchMode="singleTask"` so the redirect lands in
  `onNewIntent` on the app's existing instance instead of spawning a
  second one.
- **Honest caveat, stated in `SteamOpenIdConfig`'s kdoc too:** this
  environment cannot confirm empirically that Valve's login page accepts
  and correctly redirects to a `steamvault://` URL rather than rejecting
  or mangling it — that is squarely the "on-device only" item in the
  verification list below. If it turns out Valve rejects the scheme, the
  fix is narrow (swap the constants) and touches nothing downstream of
  `SteamOpenIdCallback.parse`.

### OpenID verification hardening

Same OkHttp security posture WP 4b.2's `VaultApiClient` established
(`docs/LEARNINGS.md` "Android (Phase 4b)"): `followSslRedirects(false)` +
`followRedirects(false)` (no redirect is ever legitimate for a fixed,
literal endpoint), HTTPS only, a bounded response read
(`BufferedSource.request(n)`, not a single `read()` call — a single
`read()` can return fewer bytes than available even when more remain, so
only `request` actually bounds the FULL body), and a strict, exact
`is_valid:true` line match (`isValidTrueStrict` — never a substring
check, so a garbage document that happens to contain that text elsewhere
is not accepted).

**Signed-fields check, scope stated honestly (WP brief).**
`signedCoversClaimedId` checks ONLY that `claimed_id` is a member of
`openid.signed` — the one field this app actually trusts (the sole source
of the persisted SteamID64). It deliberately does NOT also require
`return_to`/`response_nonce`/`op_endpoint`/`identity` to be signed: this
app never branches on those fields' values for anything
security-relevant, unlike a fully general OpenID relying party.

### Residual FIXED by WP 4b.7: request↔callback binding (replay)

**Original finding (WP 4b.3 review), kept for the record.**
`buildLoginUrl()`/`SteamOpenIdConfig.RETURN_TO` carried no per-login `state`
parameter, and `completeLogin` never checked the callback's
`openid.return_to` against the specific URL THIS login attempt built.
Concretely: a malicious app already installed on the same device could
capture a **genuine**, Valve-signed OpenID assertion for the ATTACKER's own
Steam account (e.g. by triggering its own sign-in against this app's exact
`return_to` scheme+host+path, since nothing was per-attempt-unique) and
replay it into SteamVault's intent-filter — `check_authentication` would
legitimately return `is_valid:true` (it IS a genuine, unmodified Valve
assertion, just not the one THIS app's button press initiated), and the app
would flip its displayed identity to the attacker's account.

**Blast radius, stated precisely (unchanged by the fix, for context):** this
could only ever *replace which Steam account is shown as signed in* on the
victim's device — it could not forge a claim for an account the attacker
does not themselves control, could not touch vault-api (ADR-0004 decision
2's isolation holds regardless), and leaked no secret (there is no secret in
this flow to leak). Calling `signOut()` fully recovered.

**The fix (WP 4b.7).** `net/steam/SteamLoginState.kt` adds a CSPRNG-backed,
per-login random `state` token (`SteamLoginState.generate`, 192 bits,
URL-safe base64) and a single-use holder (`PendingLoginState`).
`SteamIdentityRepositoryImpl.buildLoginUrl()` generates one fresh `state`
per call, records it as "pending" BEFORE building the URL, and embeds it in
`openid.return_to` (`"$RETURN_TO?state=$state"`). `completeLogin` extracts
the `state` back out of the callback's echoed `openid.return_to`
(`SteamOpenIdCallback.stateFromReturnTo`) and calls
`PendingLoginState.consume(callbackState)` — which ALWAYS clears the
pending value, matched or not — **before** `signedCoversClaimedId` and
before the `check_authentication` network call, so a missing/wrong/replayed
state is rejected without ever reaching Valve. `consume()`'s single-use
semantics is what actually closes the residual: even a byte-for-byte replay
of the exact same, previously-successful callback URL now fails, because
the state it carries was already consumed by the first `completeLogin`
call. Pinned by `SteamLoginStateTest` (CSPRNG output shape, single-use in
both the matched and mismatched case), `SteamOpenIdCallbackTest`
(`stateFromReturnTo` parsing), and `SteamIdentityRepositoryTest`'s "a
consumed state cannot be replayed" mutation pin.

**A deliberate trade, worth stating plainly: single-use also means a junk
callback burns the pending attempt.** Because `consume()` clears the
pending state on EVERY call regardless of match (that is what makes replay
impossible), any deep link hitting `steamvault://auth/openid-return` while a
login is pending — a malicious probe, a stray duplicate Intent, a genuinely
malformed redirect — consumes the one legitimate attempt along with it; the
user simply has to tap "Sign in with Steam" again. This is known, accepted
behaviour (fail closed, cheap to retry, no security cost), not a bug —
`MainActivity.handleIntent` deliberately routes even an unroutable callback
through `identityRepository.completeLogin` for exactly this reason (review
fix N2), rather than leaving a stale pending value that a LATER attacker
window could still target.

### Steam Web API on-device (`SteamWebApiClient`)

`GetOwnedGames` (`IPlayerService/GetOwnedGames/v1`, `include_appinfo=1`)
and `GetPlayerSummaries` (`ISteamUser/GetPlayerSummaries/v2`, for the
optional persona name), using the device-local key from
`CredentialStore.getSteamWebApiKey()` — **never** vault-api's own key,
and never sent to vault-api at all (ADR-0004 decision 2).
`SteamKeyIsolationTest` is the grep-provable pin: it reads
`VaultApiClient.kt`'s source text and asserts it never references
`getSteamWebApiKey`, `SteamWebApiClient`, `SteamOpenIdClient`, or
`SteamIdentityRepository`.

Same security posture as above (host pinned to `api.steampowered.com`,
HTTPS only, no redirects, bounded 2 MiB read). **Key redaction**
(mirroring `api/vault_api/steam_relay.py`'s `_redacted_url` discipline,
read at HEAD as the reference): every error path builds its
`SteamWebApiError` message from a fixed literal plus, at most, an HTTP
status code or an exception CLASS NAME — `e.message` from a caught
`IOException` is never interpolated (some `IOException` subtypes can
embed connection details), so the key — which lives only in the request
query string — can never reach a log line or an exception message.
`SteamWebApiClientTest`'s three `MUTATION PIN` tests plant a canary key
and assert it is absent from the network-failure, non-2xx, and
oversized-body exception messages (while separately confirming the key
DID legitimately reach the wire, via the recorded request path — the pin
is about the client-side message, not about whether Valve received the
key).

`net/model/SteamWebApi.kt`'s `parseOwnedGames`/`parsePlayerSummary`
mirror `api/vault_api/steam_relay.py::parse_owned_games`'s tolerant shape:
a malformed individual entry (wrong type, boolean masquerading as an
int/appid, oversized string) is skipped, not fatal; a document with no
usable `response` object raises `SteamWebApiError`; a `SerializationException`
or `StackOverflowError` from a hostile/deeply-nested body is caught and
converted rather than escaping as a raw exception type.

### Data layer (`SteamIdentityRepository`)

`SteamIdentityState(steamId64, personaName, hasWebApiKey)` is read fresh
from `CredentialStore` on every call. `completeLogin` never throws — every
failure (malformed callback, unsigned `claimed_id`, a rejected assertion,
an invalid SteamID64) becomes `SteamLoginResult.Failure` with a fixed,
secret-free reason string. `ownedGamesCountPreview()` returns a
`Result<Int>` (game COUNT only — the brief's explicit boundary: "library
fetch happens in 4b.4, expose the repository, render a count preview
only"); `refreshPersonaName()` is best-effort and requires both a
signed-in state and a configured key. `signOut()` calls
`CredentialStore.clearSteamIdentity()` — pinned to clear exactly the three
Steam fields and leave the vault connection (`apiKey`/`baseUrl`/`profileKind`)
untouched.

`SteamOpenIdVerifier`/`SteamLibraryFetcher` are the two seams
`SteamIdentityRepositoryImpl` depends on (implemented by `SteamOpenIdClient`/
`SteamWebApiClient` in production) — extracted purely so
`SteamIdentityRepositoryTest` can fake the network entirely and exercise
every branch (malformed callback / unsigned claimed_id / rejected
assertion / invalid SteamID64 / missing key / fetcher failure) on the JVM.

### UI (`ui/identity/IdentityScreen.kt`)

Minimal, per the brief: a sign-in button when signed out; steamid +
persona (or "not loaded yet") + a "check library size" button + sign-out
when signed in. The library size is a COUNT only (a `pluralStringResource`
sentence), never a rendered grid — the real library grid is WP 4b.4's job.
`MainActivity` now shows this screen in place of WP 4b.1's debug gallery
(`GalleryScreen.kt` still compiles and is still covered by its own tests,
just no longer wired into `MainActivity`) — flagged as an expected
reconciliation point for whichever later WP (4b.4/4b.5/4b.7, also
branch-parallel and also wanting to wire a screen into this
single-activity shell) introduces real navigation.

### Versions pinned for this WP

Added to `gradle/libs.versions.toml`:

| Component | Version | Why |
|---|---|---|
| androidx.browser | 1.8.0 | current stable release; used only for `CustomTabsIntent` to launch Valve's login page. No other new runtime dependency — verification and the Steam Web API calls reuse the already-pinned OkHttp 4.12.0. |

### Tests (WP 4b.3)

94 new JVM unit tests (218 total with WP 4b.1/4b.2's 124), no
Robolectric/emulator dependency:

- `net/steam/SteamId64Test` (12) — literal boundary fixtures shared with
  `web/tests/steamid.test.js`/`api/tests/test_steam_relay.py` (base/max/
  real-shaped/length/sign-character/whitespace/non-ASCII-digit/zeros).
- `net/steam/SteamOpenIdLoginUrlTest` (3) — the literal expected
  `checkid_setup` URL (measured empirically: OkHttp's
  `addQueryParameter` percent-encodes `:`/`/` inside a query value, which
  this file's kdoc records as a "verify, don't assume" case per
  `docs/LEARNINGS.md`), the mode-literal mutation pin, and a custom
  return_to/realm case.
- `net/steam/SteamOpenIdCallbackTest` (17) — parse (well-formed, stray
  param ignored, no query string, `cancel` mode rejected, each required
  field individually missing, base64 `=` padding preserved, malformed
  percent-encoding, duplicate key), `signedCoversClaimedId` (present/
  absent/empty/substring-trap), `steamId64From` (valid, wrong host, extra
  path segment, invalid tail, empty tail).
- `net/steam/SteamOpenIdClientTest` (12) — MockWebServer: `is_valid`
  true/false/garbage/empty/non-2xx, an oversized body cut off before the
  `is_valid:true` line, network failure, redirect refusal (reusing the
  WP 4b.2 `TlsFixture` pattern), the mode-override-to-check_authentication
  pin, the redirect-flags configuration pin (S1b), the host-pin literal
  test, and `isValidTrueStrict`'s own mutation-pinned exact-match cases.
- `net/steam/SteamWebApiClientTest` (9) — the host/path literal pin,
  successful `GetOwnedGames`/`GetPlayerSummaries` round trips, an empty
  library, and the three explicit key-redaction `MUTATION PIN` tests
  (network failure / non-2xx / oversized body).
- `net/model/SteamWebApiParsingTest` (22) — hostile fixtures: missing/
  zero/negative/boolean/string appid, boolean/negative playtime, non-object
  entries, `games` not a list, name/icon truncation, the `MAX_GAMES` bound,
  no-usable-`response` / non-object / non-JSON documents, and
  `parsePlayerSummary`'s steamid cross-check + persona truncation.
- `repo/SteamIdentityRepositoryTest` (16) — the full `completeLogin`
  branch set against fakes (success, malformed callback, unsigned
  claimed_id, rejected assertion, invalid SteamID64), `ownedGamesCountPreview`/
  `refreshPersonaName`'s missing-state paths, and `signOut`'s
  scoped-clear pin.
- `net/SteamKeyIsolationTest` (2) — the grep-provable structural pin.
- `storage/InMemoryCredentialStoreTest` gained 1 more test
  (`clearSteamIdentity` scoped-clear) on top of the existing four, updated
  to also cover the three new fields.

Mutation-verify targets named in the brief, each with an explicit test:
**host pin** (`hostPin` tests in both new client test files, literal
strings, never derived from the class's own constants), **is_valid strict
parse** (`isValidTrueStrict`'s own test plus the MockWebServer `is_valid`
variants), **steamid range** (`SteamId64Test`'s base/max/length mutation
pins), **key-redaction** (`SteamWebApiClientTest`'s three canary-key
tests).

Verified command + output tail:

```
$ ./gradlew.bat test lintDebug assembleDebug
...
BUILD SUCCESSFUL in 12s
74 actionable tasks: 33 executed, 41 up-to-date
```

218/0/0 across both `testDebugUnitTest` and `testReleaseUnitTest` (summed
from the XML reports' `tests=`/`failures=`/`errors=` attributes);
`app/app/build/reports/lint-results-debug.txt`: "No issues found."

### What is verifiable only on-device (honest list for the user's device test)

This environment has no emulator/device and cannot open a real browser —
everything below is exercised via MockWebServer/fakes up to the network
boundary, but the following need a real phone + real Steam account before
they can be called confirmed:

1. **Whether Valve's OpenID login page actually accepts and redirects to
   the `steamvault://auth/openid-return` custom scheme at all** (the
   "Honest caveat" above) — if Valve rejects or mangles it, sign-in will
   visibly fail to return to the app.
2. **Whether the Custom Tab correctly hands the redirect to
   `MainActivity.onNewIntent`** (manifest intent-filter matching,
   `launchMode="singleTask"` behaviour) — this is Android OS/Custom Tab
   plumbing this environment cannot instantiate.
3. **The real `check_authentication` round trip against the genuine
   `steamcommunity.com`** — the MockWebServer tests prove the CLIENT's
   logic against every shape of response, but never actually call Valve.
4. **Reachable as of WP 4b.7, still needs a real device: the real
   `GetOwnedGames`/`GetPlayerSummaries` calls against
   `api.steampowered.com` with a genuine Steam Web API key** (both the
   library-count preview AND the persona-name half of
   `refreshPersonaName`). `setWebApiKey()` now has two real UI paths —
   onboarding step 2 (`ui/onboarding/OnboardingScreen.kt`) and Settings'
   Steam identity section (`ui/settings/SettingsScreen.kt`), both going
   through `net/steam/SteamWebApiKeyInput.kt::submitWebApiKey` — closing
   the "no way to reach this code path from the running app at all" gap
   this item originally described. What remains device-only is the actual
   network round trip against Valve's real API with a real key, same as
   items 1-3 above.
5. **Visual/UX check of `IdentityScreen`** — Compose rendering, button
   states, and string wording have not been seen on a real screen.
6. **Watch for a malformed/rejected sign-in caused by a literal `+` in
   `openid.sig`.** `SteamOpenIdCallback.parse` decodes every query value
   with `java.net.URLDecoder.decode(_, "UTF-8")`, which follows
   `application/x-www-form-urlencoded` semantics: an UNENCODED `+`
   character decodes to a space. Base64 (the alphabet `openid.sig` is
   drawn from) legitimately contains `+`. A spec-compliant redirect from
   Valve percent-encodes it as `%2B` in the query string, which decodes
   back to a literal `+` correctly — this is expected to be a non-issue in
   practice — but if a real device test ever sees a sign-in fail for no
   apparent reason, check whether the callback URL's `openid.sig` carried
   a raw `+`: this fails CLOSED (a corrupted signature value simply makes
   `check_authentication` return `is_valid:false`, never a security hole),
   but it would look like an unexplained rejection rather than the
   `+`-decoding cause. Not fixed here (no evidence Valve's actual redirect
   needs it) — recorded as a debugging note for whoever sees the failure
   first.

### What WP 4b.3 deliberately did NOT do

- **No library grid, no game list UI** — `ownedGamesCountPreview()`
  exposes a COUNT only; the full grid is WP 4b.4.
- **No settings UI for entering the Steam Web API key** —
  `setWebApiKey()` exists on the repository; a real input screen is WP
  4b.7 (onboarding/settings, serial after 4b.3 per
  `docs/WORKPACKAGES.md`).
- **No real navigation** — `IdentityScreen` replaces the WP 4b.1 debug
  gallery as `MainActivity`'s one screen; multiple destinations arrive
  with 4b.4/4b.5/4b.7's navigation work.
- **No app-wide error-display convention** — review round S3 added ONE
  line of state (`MainActivity.identityState.loginError` →
  `IdentityScreen`'s inline `Text` in the error colour) so
  `SteamLoginResult.Failure.reason` is at least visible when sign-in
  fails, since that branch is exactly where the device-only verification
  items above (1-3) would land if any of them go wrong on a real device.
  A real Toast/Snackbar/inline-message CONVENTION for the whole app is
  still left to whichever later WP establishes one, rather than guessed
  at here.
- **No WorkManager-driven persona/library refresh** — `refreshPersonaName`/
  `ownedGamesCountPreview` are both manual, button-triggered calls; any
  background refresh is WP 4b.8's polling work.

## Downloads + job control (WP 4b.5)

Branch-parallel after 4b.2 per `docs/WORKPACKAGES.md` Phase 4b. Adds the
Downloads screen (`ui/downloads/`): an Active section and an INDEPENDENT
Paused section (the slot-release divergence — api/README.md "The worker
slot — a paused job does NOT hold it" — ported from `web/js/lib/
job-partition.js` onto the real WP 3.12 status set), a FIFO queue with
positions, and history newest-first with lazily-fetched log excerpts (one
`GET /v1/jobs/{id}` per job on first expand, cached for the session).
Job control (pause/resume/cancel) is non-optimistic — a click only calls
`VaultApiClient` and nudges an immediate re-poll, same "server confirms"
pattern `web/js/views/downloads.js` documents.

`ui/downloads/logic/JobPartition.kt` records one deliberate IMPROVEMENT
over the web port: an unrecognized job status is routed into the History
section with a neutral presentation instead of silently vanishing from
every bucket (the web module's own review nit) — see that file's kdoc.

### What is verifiable only on-device (WP 4b.5)

Everything in `ui/downloads/logic/` is proven pure/JVM-side
(`JobPartitionTest`, `JobCardModelTest`, `LogExcerptTest`,
`FormatTest`), and `DownloadsController`'s network calls go through the
same `VaultApiClient`/`VaultApiError` seams WP 4b.2 already device-verified
for other endpoints. What is NOT exercised by any of that, and needs a real
phone against a real vault-api before it can be called confirmed:

1. **Pause/resume/cancel against a REAL running job.** The `stop_request`
   round trip (`POST /v1/jobs/{id}/pause`/`resume`, `DELETE /v1/jobs/{id}`)
   is proven client-side against `VaultApiClient`'s request/response
   shapes only — that the WORKER actually terminates the SteamPrefill
   subprocess, that `stop_request` clears once it does, and that the
   "Pausing…"/"Cancelling…" note on the job card disappears at the right
   poll tick, is only observable end-to-end with a genuine vault-api
   worker doing real work.
2. **Active-vs-Paused slot-release presentation with a genuinely paused
   job.** `JobPartitionTest` proves the pure partitioning logic (running
   and paused as independent buckets); it does not prove that a real
   pause against a real download leaves a DIFFERENT queued job claimed and
   running while the paused one sits in its own section on screen — that
   needs two real jobs and a real worker.
3. **The lazy log-excerpt fetch on first expand.** `ExcerptCache`'s
   fetch-once/cache-for-the-session/retry-after-failure state machine is
   proven against a canned fetcher (`LogExcerptTest`); the real `GET
   /v1/jobs/{id}` call — its latency, a genuine truncated SteamPrefill
   log, and the Compose recomposition `DownloadsController.excerptVersion`
   drives on a real device — has not been exercised outside a JVM test.
4. **The nav pip's foreground-only staleness** (see `MainActivity.kt`'s
   `pendingJobsSnapshot` kdoc for the mechanism). The pip is only ever
   updated while Library or Downloads — whichever screen currently owns
   the jobs poll — is on screen; it goes stale (does not update) while
   Settings is visible, and only catches up once the user switches back.
   This is a real, user-visible behaviour, not just an implementation
   detail: a device test should confirm it reads as "a little behind",
   not as broken, and that a screen reader announces the overridden
   `contentDescription` (`"Downloads — N pending"`) correctly once a job
   is actually pending.

### What WP 4b.5 deliberately did NOT do

- **No WorkManager / background jobs poll** — foreground-only via
  `repeatOnLifecycle`, same constraint every screen in this app has before
  WP 4b.8.
- **No queue reordering / drag-to-reorder** — post-v1 backlog item per
  `docs/WORKPACKAGES.md`; the queue is presentation-only FIFO.
- **No detail-sheet integration** — WP 4b.6.
- **No update-check affordance anywhere on this screen** — Phase 4c guard
  (binding): a refresh only ever re-polls `GET /v1/jobs`/`GET /v1/games`,
  never triggers or checks for a download on its own initiative.

## Game detail sheet (WP 4b.6)

Serial after 4b.4 per `docs/WORKPACKAGES.md` Phase 4b. Adds the sheet opened
from a Library card (`ui/detail/`): cover/name/status, sizes, the honest
last-download/confirmed-current wording, per-depot sharing (computed live
from `buildMultiPlan`/`buildDepotPresentation`, never stored — mockup round
3), download/pause/resume/cancel for the app's own tracked job, delete with
a per-depot freed/kept preview (literally `buildMultiPlan(listOf(appid),
...)`, so it cannot drift from the Library's bulk-delete arithmetic), and a
dry-run → confirm → execute GC flow (`ui/detail/logic/GcFlow.kt`'s state
machine) that can never reach `execute=true` without an explicit second
confirm after a completed dry run.

**Recorded divergence — a fourth depot-sharing state, `ORPHANED`, beyond the
mockup's three (same class of documented deviation as the WP 4b.5
slot-release divergence above, and the WP 4a.5/4b.5 `cancelled` status-icon
divergences in `docs/WORKPACKAGES.md`'s Phase 4a header — docs/LEARNINGS.md
requires deviations from the frozen mockup to be recorded, not just
kdoc'd).** The mockup only ever distinguishes `shared` (kept) from `shared ·
sole holder` (the viewed game is the last cached holder, deleting frees it —
round 5). It never modeled a THIRD case the real API's ADR-0003 last-remnant
rule makes reachable: a game that has ALREADY been deleted keeps its mapping
rows by design (`DELETE /v1/cache/{appid}` "mapping rows survive deletion"),
so opening its detail sheet again can show a shared depot where NEITHER the
viewed game NOR any of its co-owners currently has cache content — the exact
"previously deleted game, mapping intact, nothing on disk" shape
`vault-app-mockup-NOTES.md`'s own sample-data note seeds for Meridian Rally,
just reached from the real deletion flow instead of authored fixture data.
Tagging that case `SOLE_HOLDER` would be dishonest (the viewed game holds
nothing to protect by deleting further); the sheet reports it as `ORPHANED`
("Shared · no cached owner") instead — see
`ui/detail/logic/DepotPresentation.kt`'s kdoc and `DepotPresentationTest`'s
"shared, no other holder, THIS app also does not hold it" case for the exact
condition (`row.free && !thisAppIsHolder`).

## Onboarding + settings (WP 4b.7)

Branch-parallel after 4b.3 per `docs/WORKPACKAGES.md` Phase 4b — dispatched
after every other 4b.x package, so it is the first WP with everything else
(4b.1-4b.6) already in place to write against. Closes the two gaps every
earlier WP's README section flagged as "not this WP": nothing wrote a
vault-api connection into `CredentialStore` (`net/profile/
ConnectivityProfileFactory.kt`'s "Gap this documents" note), and
`setWebApiKey()` had no UI path at all (WP 4b.3's own bullet list).

```
app/app/src/main/java/dev/steamvault/app/
├── net/
│   ├── connection/
│   │   └── ConnectionCheck.kt          # two-step "test connection" state machine
│   └── steam/
│       ├── SteamLoginState.kt          # per-login random state (replay-residual fix)
│       └── SteamWebApiKeyInput.kt      # 32-hex validator + the field-clearing pin
├── ui/
│   ├── onboarding/
│   │   ├── logic/OnboardingSteps.kt    # pure 3-step gating machine
│   │   ├── OnboardingController.kt
│   │   ├── OnboardingStrings.kt
│   │   └── OnboardingScreen.kt
│   └── settings/
│       ├── logic/SettingsDiff.kt        # PATCH-body-from-drafts port
│       ├── logic/SettingsPresentation.kt
│       ├── SettingsController.kt
│       ├── SettingsStrings.kt
│       └── SettingsScreen.kt
└── MainActivity.kt                      # onboarding gate + reconnect/disconnect wiring
```

### Onboarding (`ui/onboarding/`)

Three steps, same shape the frozen mockup and the web port (`web/js/
onboarding.js`, WP 4a.6) both use, but step 1's FIELDS differ on purpose —
`ui/onboarding/logic/OnboardingSteps.kt`'s kdoc explains why the web port's
"no server-URL field, the page is already served by vault-api" shortcut
does not apply to a native app that has never talked to any server yet:

1. **Connect** — connectivity profile choice (`SystemVpnProfile` /
   `PublicDomainProfile`, WP 4b.2), base URL, vault API key, and a REAL
   connection check before `Continue` unlocks
   (`OnboardingSteps.canAdvanceOnboardingStep`: step 1 requires
   `tested == true`, exactly like the web port's step 1 gate).
2. **Steam identity (optional)** — the existing WP 4b.3 OpenID sign-in flow,
   plus the Steam Web API key entry this WP adds a UI for. Skippable:
   `canAdvanceOnboardingStep` never blocks on this step, matching the
   mockup's "Continue without one — you can sign in later under Settings".
3. **Done** — a summary (connection verified / Steam identity linked or
   not), then `finish()` persists the connection and the caller
   (`MainActivity`) rebuilds its `VaultApiClient`.

**The connection check (`net/connection/ConnectionCheck.kt`) mirrors
`web/js/api.js::checkVaultApiKey`'s two-step reasoning verbatim: `GET
/v1/health` never validates a key** (api/README.md "Auth" — this app
attaches `X-Api-Key` to every request including health, same choice
`web/js/api.js`'s client makes, but the SERVER never checks it there), so a
successful health call only proves reachability. Only the second,
authenticated call (`GET /v1/settings`, chosen for the same reason the web
port picks it — onboarding needs the response anyway) actually proves the
key. `checkVaultConnection` takes two suspend LAMBDAS rather than a
concrete `VaultApiClient` specifically so `ConnectionCheckTest` can pin the
step ORDERING (the settings lambda must never run if health already failed)
and the failure classification (`classifyConnectionFailure`) without any
MockWebServer/network stack at all — a `401` is only ever labelled
`KeyRejected` on the SETTINGS step, never on HEALTH (which has no auth
check to fail against a real server).

**Full-screen swap, not a modal overlay.** `web/js/onboarding.js` renders
onboarding as a `role="dialog"` overlay above the app shell; this app swaps
it in as `MainActivity`'s entire `setContent` body instead — the same plain
state-based screen-switching choice `ui/nav/Destination.kt`'s kdoc already
committed to for the three main destinations, extended to one more
top-level state rather than introducing a second, heavier overlay mechanism
for one screen. `OnboardingMode.RECONNECT` (Settings' "Reconnect / switch
vault") gets a "Cancel" action that bails out with the existing connection
untouched — `OnboardingController.canCancelWithoutFinishing`, the same
"nothing to fall back to on first run" distinction `web/js/onboarding.js`'s
`mode` makes with its Escape-only-in-reconnect handling.

### Settings (`ui/settings/`)

Replaces the bare `ui.identity.IdentityScreen` that used to be the entire
`Destination.SETTINGS` content (`IdentityScreen.kt` itself is untouched and
still compiles — same "kept but unreachable from the UI" treatment WP
4b.3/4b.4 gave `ui.gallery.GalleryScreen`). Three independent surfaces, same
split `web/js/views/settings.js` (WP 4a.6) documents:

- **Vault / Schedule / Webhook** — one form over `GET`/`PATCH /v1/settings`
  (ADR-0009), ported field-for-field from the web view. `ui/settings/logic/
  SettingsDiff.kt::buildSettingsPatchDraft` is the `settings-diff.js` port:
  a `drafts` map populated ONLY by fields the user actually touches (never
  pre-seeded from the loaded snapshot) is diffed against the last `GET`
  response, so a no-op edit (types the same value back) is dropped and the
  PATCH body contains ONLY genuinely changed keys — pinned by
  `SettingsDiffTest`'s named mutation case, same "removing the
  `valueChanged` guard must kill a named test" discipline the web port's
  own test file documents. `ui/settings/logic/SettingsPresentation.kt`
  ports `settings-presentation.js`'s `source`/`applies` captions as pure
  enums (`SettingsSource`/`SettingsApplies`) resolved to `strings.xml` text
  at the `SettingsScreen.kt` call site, honouring app/README.md's "String
  resources" convention rather than hardcoding the wording in Kotlin.
  `VAULT_SETTINGS_READONLY` disables the whole form (readonly banner,
  every field `enabled = false`, no Save/Discard bar) but the Steam
  identity section below stays fully usable — same reasoning
  `web/js/views/settings.js` documents (Steam identity is a separate
  endpoint, unaffected by the settings-API lock). A `422` from `PATCH`
  (api/README.md: "one bad value... fails the request... with a DISTINCT
  detail") is surfaced verbatim via `saveError` — vault_api's detail string
  already names the offending key, so no client-side field-mapping is
  needed to make the error legible.
- **Steam identity** — the existing WP 4b.3 sign-in/out state plus Web API
  key management this WP adds: a MASKED status line (`settings_steam_key_
  masked`/`_not_set` — WP brief: "only whether set, never the value") and
  a Remove action (`removeWebApiKey`, clears the key without ever reading
  it back).
- **Connection** — `SettingsController.connectionSummary()` reads
  `CredentialStore` directly and shows the base URL + profile kind, NEVER
  the API key (WP brief boundary, mirrored in the `ConnectionSummary` data
  class itself — it has no key field to leak by accident). "Reconnect /
  switch vault" opens onboarding in `RECONNECT` mode; "Disconnect" (behind
  an `AlertDialog` confirm) calls `CredentialStore.clear()` — the WHOLE
  store, deliberately broader than `clearSteamIdentity()`, matching that
  interface's own documented "forget this vault entirely" contract for
  exactly this action.

### `setWebApiKey` UI gap, closed (`net/steam/SteamWebApiKeyInput.kt`)

`submitWebApiKey` is a direct, Compose-free port of `web/js/lib/
steam-key-form.js::submitSteamKey`'s two guarantees: `validSteamWebApiKey`
mirrors the exact "32 hexadecimal characters" grammar
(`vault_api.steam_relay.valid_steam_web_api_key`), and the returned
`WebApiKeySubmitResult.nextFieldValue` is **`""` on every path** — a
rejected format, a `persist` that throws, or success alike — the same
unconditional-clear guarantee the web port's `field.value = ""` `finally`
block gives, pulled into a pure function specifically so it is mechanically
provable rather than "the code looks right"
(docs/LEARNINGS.md "Testing discipline"). `SteamWebApiKeyInputTest` pins
all three paths by name (`MUTATION PIN` cases). Both `OnboardingController
.submitWebApiKeyEntry()` and `SettingsController.submitWebApiKeyEntry()`
call this same function against their own `mutableStateOf` field —
`OnboardingControllerTest`'s own `MUTATION PIN` cases additionally exercise
the real Compose-state field end to end (not just the pure function), per
the WP brief's explicit ask to pin the clearing "in a controller test".

### Replay-residual fix (`net/steam/SteamLoginState.kt`)

See the "Residual FIXED by WP 4b.7" section above (in the WP 4b.3 write-up)
for the full before/after — summary: a CSPRNG per-login `state` token is
embedded in `openid.return_to`, checked via a single-use `PendingLoginState
.consume()` BEFORE `signedCoversClaimedId`/`check_authentication` run, so a
missing, wrong, OR previously-used state is rejected without any network
call. `SteamIdentityRepositoryTest`'s "a consumed state cannot be replayed"
case is the mutation pin the WP brief asked for.

### Tests (WP 4b.7)

85 new JVM unit tests (492 total with the prior WPs' 407), no
Robolectric/emulator dependency:

- `ui/onboarding/logic/OnboardingStepsTest` — progress percent, the step-1
  gating direction (both ways), Back/Continue boundary no-ops,
  `shouldShowOnboarding`'s mutation pin.
- `net/connection/ConnectionCheckTest` — `classifyConnectionFailure`'s
  three outcomes incl. the HEALTH-step-401-is-never-KeyRejected mutation
  pin, and `checkVaultConnection`'s step ordering/short-circuit (settings
  never called after a health failure).
- `ui/settings/logic/SettingsDiffTest` / `SettingsPresentationTest` — the
  no-op-edit-is-dropped mutation pin, the env_only/non-db-reset guards,
  the `webhook_events` list-vs-comma-text equivalence (no dedup, matching
  the web port exactly), and the applies/source/effective-text pure
  mappings.
- `net/steam/SteamLoginStateTest` — CSPRNG output shape (length, alphabet,
  no collisions in two calls), and `PendingLoginState.consume`'s single-use
  semantics in every direction (match, mismatch, nothing pending, null
  actual, consumed-then-replayed).
- `net/steam/SteamOpenIdCallbackTest` (extended) — `stateFromReturnTo`
  parsing (present, absent, alongside other params, percent-encoded,
  trailing bare `?`).
- `repo/SteamIdentityRepositoryTest` (extended) — every existing
  `completeLogin` test now seeds a pending state via `buildLoginUrl()`
  first (the state check is now the first gate); new cases cover no
  pending state, no state in the callback, a mismatched state, and the
  named "a consumed state cannot be replayed" mutation pin.
- `net/steam/SteamWebApiKeyInputTest` — the 32-hex validator's boundaries,
  and `submitWebApiKey`'s clearing guarantee on all three paths.
- `ui/onboarding/OnboardingControllerTest` — step navigation delegation,
  `start()`'s field-seeding from `CredentialStore`, Steam login delegation,
  the web-API-key clearing pin exercised through the real Compose state
  field, and `finish()`'s exact persisted-fields pin (connection only,
  nothing Steam-related).

Verified command + output tail:

```
$ ./gradlew.bat test lintDebug assembleDebug
...
BUILD SUCCESSFUL in 1m 2s
74 actionable tasks: 22 executed, 52 up-to-date
```

492/0/0 across both `testDebugUnitTest` and `testReleaseUnitTest` (summed
from the XML reports' `tests=`/`failures=`/`errors=` attributes);
`app/app/build/reports/lint-results-debug.txt`: "No issues found."
`app/app/build/outputs/apk/debug/app-debug.apk` produced.

### What WP 4b.7 deliberately did NOT do

- **No instrumented test of the onboarding/settings screens** — no
  emulator/device available, unchanged constraint from every earlier 4b.x
  WP; `OnboardingController`/`SettingsController` are Compose-free-enough
  to unit test their orchestration logic (see above), but the actual
  Compose rendering, focus order, and field behaviour have not been seen
  on a real screen.
- **No real "About" section, no app version display** — not asked for by
  the WP brief; `web/js/views/settings.js`'s About block (Valve trademark
  notice) has no Android counterpart yet.
- **`tsnet` connectivity profile picker** — still post-v1
  (`docs/WORKPACKAGES.md`), unaffected by this WP; the onboarding profile
  choice only ever offers `SystemVpnProfile`/`PublicDomainProfile`.
- **No live re-validation of an already-configured connection** —
  Settings' Connection section trusts whatever is in `CredentialStore`
  without re-testing it; a connection that has gone stale (revoked key,
  changed IP) surfaces as a `VaultApiError` the next time Library/Downloads
  actually poll, not proactively on the Settings screen.
- **No WorkManager interaction** — onboarding/settings are foreground-only
  screens; WP 4b.8's background polling is unaffected by (and does not
  affect) anything in this WP.

## Notifications via WorkManager (WP 4b.8)

Serial after 4b.5 per `docs/WORKPACKAGES.md` Phase 4b. Closes the gap every
earlier WP's README section flagged ("WorkManager wiring... is WP 4b.8, not
this WP" — WP 4b.2's "Polling primitives" section, restated in 4b.5/4b.7):
a background poll that derives the same notification events
`docs/design/vault-app-mockup-NOTES.md`'s bell panel documents
(finished/failed downloads, update-ready games, cache-bypass warnings),
independent of whether the app is open.

```
app/app/src/main/java/dev/steamvault/app/
├── VaultApplication.kt                  # process-wide onCreate: channels + scheduling
├── MainActivity.kt                      # notification-tap routing (extended)
└── notifications/
    ├── NotificationEvent.kt             # the 5-event taxonomy (sealed class)
    ├── DiffByKey.kt                     # generic keyed-list differ (web diff-utils.js port)
    ├── NotificationSnapshot.kt          # the persisted compact snapshot shape + mappers
    ├── NotificationDiffer.kt            # the pure differ (web notifications.js port)
    ├── NotificationSnapshotStore.kt     # SharedPreferences persistence (interface + impl)
    ├── NotificationPollLogic.kt         # pure evaluate() — the worker's testable core
    ├── NotificationRouting.kt           # event -> channel/destination/id mapping
    ├── NotificationChannels.kt          # NotificationChannel creation
    ├── NotificationStrings.kt           # title/body text (Resources-backed interface)
    ├── NotificationPoster.kt            # posts the Android notification (foreground gate, permission check)
    ├── NotificationPollWorker.kt        # the CoroutineWorker (thin glue)
    └── NotificationScheduler.kt         # PeriodicWorkRequest enqueue
```

### The differ port (`NotificationDiffer.kt`, `DiffByKey.kt`)

Line-for-line port of `web/js/notifications.js` + `web/js/diff-utils.js`
onto a Kotlin `sealed class NotificationEvent` (`JobFinished`/`JobFailed`/
`UpdateReady`/`BypassSuspected`/`BypassResolved` — `BypassResolved` kept
per `docs/LEARNINGS.md`'s transition-detector rule, same reasoning the web
module's own kdoc gives). `DiffByKey.kt` mirrors `diffByKey` exactly except
it compares with Kotlin data-class `equals()` instead of the web module's
`JSON.stringify` stand-in — same added/updated/removed/unchanged/`isFirst`
buckets either way. Both invariants the DoD calls out by name are pinned:
the first poll (`isFirst`) never fires, and a no-op poll produces zero
events.

**One deliberate improvement over the web port**, per this WP's brief
("the Android improvement from 4b.5 (unknown statuses) applies where
relevant"): a job whose PREVIOUS status is neither a known active status
nor a known terminal one (a value this client has never seen) is now
treated as "was active" rather than "was not active" when deciding whether
a transition to `done`/`error` is real news — the web differ's
`JOB_ACTIVE_STATUSES.has(prev.status)` check would silently swallow that
transition instead. Same fail-toward-reporting-real-information posture
`ui/downloads/logic/JobPartition.kt`'s WP 4b.5 divergence already
established for the Downloads screen; see `NotificationDiffer.kt`'s kdoc
for the full reasoning and why `diffGames`/`diffClients` needed no
analogous change (both already compare against a single known value by
equality, which is fail-safe by construction).

### Compact snapshot, not the raw API response (`NotificationSnapshot.kt`)

The differ only ever reads four job fields, four game fields and two
client fields — the persisted `NotificationSnapshot` carries exactly
those, not the full `net/model/` response shapes (byte counters, source
addresses, timestamps the differ never looks at). Smaller on-disk payload
for a multi-hundred-game library, and it makes "did the differ see a real
change" and "did the persisted snapshot change" the same question by
construction. Plain (non-encrypted) `SharedPreferences`
(`SharedPreferencesNotificationSnapshotStore`) — nothing here is a secret,
same reasoning `storage/LibraryPreferences.kt` documents for the layout
preference; it deliberately does NOT go through `CredentialStore`/
`EncryptedCredentialStore`, whose one narrow guarantee is scoped to the
vault-api key alone. A corrupted/incompatible-schema stored value decodes
to `null` (treated as "never saved") rather than crashing the worker.

### Idempotency: notify, THEN persist (`NotificationPollLogic.kt`, `NotificationPollWorker.kt`)

`NotificationPollWorker.doWork()` fetches once, calls the pure
`NotificationPollLogic.evaluate(prevSnapshot, jobs, games, clients)`, posts
the resulting events, and ONLY THEN persists the new snapshot — in that
order, never reversed. A crash between fetch and persist re-derives the
identical event set on the next run (a possible harmless re-post of the
same notification — `NotificationRouting.notificationId` is a stable hash
of the event's own key, so a repost UPDATES the existing system
notification rather than stacking a duplicate); a crash strictly after
persist produces zero events on the next run (nothing looks new anymore).
The rejected alternative (persist-before-notify) would let a crash between
persist and notify silently DROP the event forever — a strictly worse
failure mode. `NotificationPollLogicTest` pins both crash windows against
an `InMemoryNotificationSnapshotStore` fake, without touching Android or
WorkManager at all.

Fail-soft rules mirror the WP brief exactly: no vault-api connection
configured, or no API key stored → `Result.success()` silently, nothing to
poll. The fetch call throwing `VaultApiError` (network down, auth failure,
server error, unparsable body) → `Result.success()` WITHOUT persisting, so
the next successful run still diffs against a valid baseline instead of a
partial one.

### Foreground suppression (`NotificationPoster.kt`, `NotificationPollWorker.kt`)

Simplest honest rule per the brief ("a process-lifecycle check is
enough"): `ProcessLifecycleOwner.get().lifecycle.currentState.isAtLeast
(Lifecycle.State.STARTED)` gates whether `AndroidNotificationPoster.post`
actually calls `NotificationManagerCompat.notify` — while foreground, the
call is a no-op. The worker still runs its full fetch + diff + PERSIST
cycle regardless of foreground state, so the snapshot always advances;
without that, returning to the background would replay every event that
happened while the user was already looking at the live Library/Downloads
polling loops (`polling/PollingIntervals.kt`).

### WorkManager scheduling and Doze (`NotificationScheduler.kt`, `VaultApplication.kt`)

A single `PeriodicWorkRequest` at `PeriodicWorkRequest.MIN_PERIODIC_
INTERVAL_MILLIS` (15 minutes — WorkManager's documented floor), constrained
to `NetworkType.CONNECTED`, enqueued with `ExistingPeriodicWorkPolicy.KEEP`
from the new `VaultApplication.onCreate` (this app had no custom
`Application` class before this WP) so re-launching the app never resets
WorkManager's internal next-run clock. **Deliberately no exact alarms, no
foreground service, no battery-optimization-exemption prompt** — all three
would fight Android's Doze/App Standby power model instead of living
inside it (the last one in particular would ask the user for a permission
most apps should not need). This means the REAL interval while the phone
sits idle is "15 minutes, or considerably longer under deep Doze, with
runs batched into maintenance windows" — accepted for v1 per
`docs/design/vault-app-mockup-NOTES.md` ("Notifications are a poll, not a
push") and the WP brief's own wording ("polling via WorkManager, respecting
Doze"); a user who wants faster feedback still has the foreground screens'
own 2–20 s polling.

### Notification channels, permission, routing (`NotificationChannels.kt`, `NotificationPoster.kt`, `NotificationRouting.kt`, `SettingsScreen.kt`)

Three channels (`downloads`/`updates`/`bypass`, `bypass` at
`IMPORTANCE_HIGH`) so the user can silence any one class independently.
`POST_NOTIFICATIONS` (API 33+, declared in `AndroidManifest.xml`) is
requested from the new Settings → Notifications section
(`SettingsScreen.kt`'s `NotificationsSection`, wired through
`MainActivity.requestNotificationPermission` and a class-level
`registerForActivityResult` launcher) — denying it leaves the worker
running exactly as before, just without a visible notification
(`AndroidNotificationPoster` re-checks the live grant on every post, no
separate reactive state needed). Tapping a notification opens
`MainActivity` with `NotificationRouting.EXTRA_DESTINATION` set to a
`Destination.name` string; `MainActivity.destination` was hoisted out of a
`remember { }` local into a class-level `mutableStateOf` specifically so
`handleNotificationTap` (called from both `onCreate`'s and
`onNewIntent`'s `handleIntent`) can change it from outside composition.
Routing: job events → `Destination.DOWNLOADS`, bypass events →
`Destination.SETTINGS` (this app has no dedicated clients sheet yet,
unlike `web/js/views/clients.js` — an honest simplification, not a
missing feature this WP was asked to add), `update_ready` →
`Destination.LIBRARY` (the brief names no destination for it explicitly;
a per-game deep link like the mockup's bell panel offers does not exist as
an addressable destination in this app yet). All three mappings are
literal-pinned in `NotificationRoutingTest`, never derived from the enums
under test.

**Recorded routing gap (review round, N3) — `bypass_suspected`/
`bypass_resolved` land on `Destination.SETTINGS`, not a clients surface
(same "recorded, not silently kept" treatment the depot-sharing `ORPHANED`
divergence above and `ui/downloads/logic/JobPartition.kt`'s WP 4b.5
divergence get).** Settings does not actually show per-client bypass
detail today — tapping the notification lands the user one screen away
from the client list `web/js/views/clients.js` gives the web frontend, not
on it. This is accepted as this WP's honest ceiling, not fixed here: **a
clients surface is the real fix**, and belongs to whichever future WP
gives this app a `GET /v1/clients`-backed screen (Settings is the nearest
existing destination only because no such screen exists yet).

### Versions pinned for this WP

Added to the existing `gradle/libs.versions.toml` table:

| Component | Version | Why |
|---|---|---|
| androidx.work:work-runtime-ktx | 2.9.1 | latest stable 2.9.x release; 2.10.x's floor needs a newer AGP than this project's pinned 8.7.3 |
| androidx.lifecycle:lifecycle-process | 2.8.7 | same version already pinned for lifecycle-runtime-ktx/-compose; provides `ProcessLifecycleOwner` |

No `androidx.work:work-testing` dependency: it needs Robolectric or an
instrumented device to drive `TestListenableWorkerBuilder`, neither of
which exists in this environment — see "What WP 4b.8 deliberately did NOT
do" below.

### Tests (WP 4b.8)

42 new JVM unit tests (534 total with the prior WPs' 492), no
Robolectric/emulator dependency:

- `notifications/DiffByKeyTest` — the generic differ's bucket semantics
  (`isFirst` on `null` vs. empty prev, unchanged vs. updated, removed).
- `notifications/NotificationDifferTest` — the full web-test-file port:
  first-poll silence (all three domains + the combined helper), no-change
  silence, `job_finished`/`job_failed` (incl. the added-vs-updated and
  aged-out-of-the-window cases), cancelled-is-silent, `update_ready`'s
  zero-bytes/null-bytes/already-stale/non-stale-transition gates,
  `bypass_suspected`/`bypass_resolved` both directions plus the
  steady-state no-op, the combined-helper ordering, and the two Android-
  improvement cases (unrecognized previous status → `done`/`error` still
  fires).
- `notifications/NotificationSnapshotSerializationTest` — the
  serialize/deserialize round trip (populated, all-empty, and
  null-vs-empty-string field cases), plus the corrupted-JSON-is-treated-
  as-never-saved case via `InMemoryNotificationSnapshotStore`.
- `notifications/NotificationPollLogicTest` — the idempotency decision pin
  (crash-before-persist re-derives the same event; crash-strictly-after-
  persist derives zero) against the in-memory fake store, end to end
  through `NotificationPollLogic.evaluate`.
- `notifications/NotificationRoutingTest` — every event type's
  channel/destination mapping, `EXTRA_DESTINATION`'s literal string, and
  `notificationId`'s stability/distinctness.

Verified command + output tail:

```
$ ./gradlew.bat test lintDebug assembleDebug
...
BUILD SUCCESSFUL in 30s
74 actionable tasks: 26 executed, 48 up-to-date
```

534/0/0 in both `testDebugUnitTest` and `testReleaseUnitTest` (verified via
the XML reports' `tests=`/`failures=`/`errors=` attributes, each variant
summed independently — both run the identical suite);
`app/app/build/reports/lint-results-debug.txt`: one pre-existing-pattern
`PluralsCandidate` false positive on `notif_job_failed_body` (`%1$d` is an
app id, not a count — same class of justified suppression as
`settings_schedule_window_placeholder`'s `TypographyDashes` ignore),
suppressed with a documented `tools:ignore`; otherwise clean.
`app/app/build/outputs/apk/debug/app-debug.apk` produced.

### What WP 4b.8 deliberately did NOT do — the honest device-test list

No emulator/device is available in this environment (unchanged constraint
from every earlier 4b.x WP). Everything above the Android-framework
boundary is unit-tested; everything below it is real, uncovered
device-territory that a future on-device pass must verify:

- **Does WorkManager actually invoke `NotificationPollWorker` on the
  declared ~15-minute cadence**, and does it visibly batch/defer under a
  real Doze session the way this WP's README section above claims it will.
- **Does `EncryptedCredentialStore`/Android Keystore actually work from a
  background process** (no Activity, no foreground UI thread) the same way
  it works from `MainActivity` — `NotificationPollWorker` is the first
  caller of `EncryptedCredentialStore` that isn't Activity-driven.
- **Does `NotificationManagerCompat.notify` actually show a heads-up/tray
  notification** with the right channel, title, body, and does tapping it
  actually launch `MainActivity` and land on the right `Destination` via
  `onNewIntent`.
- **Does the `POST_NOTIFICATIONS` permission prompt actually appear on a
  real API 33+ device** when Settings → "Enable notifications" is tapped,
  and does denying it leave the worker's fetch/diff/persist cycle running
  with only the visible notification suppressed.
- **Does the foreground-suppression rule actually feel right in practice**
  — `ProcessLifecycleOwner`'s STARTED threshold is a coarse process-wide
  signal (any part of the app in the foreground suppresses ALL
  notification classes), not a per-screen one; the brief calls this
  sufficient, but it has not been felt on a real device.
- **No `androidx.work:work-testing` / Robolectric** — `TestListenableWorkerBuilder`
  needs one of the two, neither available here; `NotificationPollLogic`
  (the pure decision core) carries the equivalent test weight on the plain
  JVM instead, per this WP's brief ("extract decisions so the untestable
  shell is thin").
- **No live re-check of the notification permission's grant state on the
  Settings screen** — `NotificationsSection` always shows the same
  "Enable notifications" button regardless of current grant, rather than a
  reactive granted/denied indicator (would need a lifecycle-resume
  observer to refresh after the user returns from the system dialog;
  out of scope for this WP's "keep it simple" instruction).
- **No per-game deep link for `update_ready`** — routes to the Library
  destination generally, not to the specific game's detail sheet the way
  the mockup's bell panel does; no "focus this appid" extra exists on the
  Library screen yet.
- **Reused the WP 4b.1 launcher's monochrome vector
  (`ic_launcher_monochrome.xml`) as the notification small icon** rather
  than commissioning a dedicated glyph — it is already a pure white-on-
  transparent silhouette, exactly the shape a status-bar icon needs;
  revisit in the WP 4b.9 release-art pass if a dedicated asset is wanted.

## Clients sheet (WP 4b.10)

Serial after 4b.8 per `docs/WORKPACKAGES.md` Phase 4b. Closes the recorded
routing gap that WP 4b.8's own README section above documents ("Recorded
routing gap... bypass_suspected/bypass_resolved land on Destination
.SETTINGS, not a clients surface") and the matching PARTIAL item in
`docs/PROJECT_PLAN.md` §7: a real `GET /v1/clients` screen, Kotlin port of
`web/js/components/clients-sheet.js` + `web/js/lib/clients-view.js`.

```
app/app/src/main/java/dev/steamvault/app/
├── ui/clients/
│   ├── logic/ClientsView.kt      # partitionClients, hitRatePercent, ClientRowModel
│   ├── ClientsController.kt      # state + the foreground-only poll loop
│   ├── ClientsStrings.kt         # the one Resources-backed fallback string
│   └── ClientsSheet.kt           # the ModalBottomSheet composable
├── notifications/NotificationRouting.kt  # destinationFor now nullable + opensClientsSheetFor
├── notifications/NotificationPoster.kt   # sets EXTRA_OPEN_CLIENTS_SHEET
├── ui/settings/SettingsScreen.kt         # new "Clients" section/button
└── MainActivity.kt                       # hoists ClientsController, wires both entry points
```

**Clients stays a sheet, not a nav item — `ui/nav/Destination.kt`'s kdoc and
the WP 4a.1 decision it restates are binding here too.** `ClientsController`
is therefore hoisted at `MainActivity` level next to `settingsControllerState`
(same reasoning that class's own kdoc gives), not owned by the Library/
Downloads/Settings screen it happens to render over — the mockup rule
"navigation dismisses transient surfaces" is honoured at the bottom nav's
`onSelect` call site (`clientsControllerState?.close()`), same place the
game detail sheet's dismissal precedent lives conceptually, generalized to
a sheet that is not scoped to any one destination's own `remember` block.

**Two entry points, matching `clients-sheet.js`'s own kdoc exactly:**
Settings' new "Clients" button (`SettingsScreen.kt`'s `ClientsSection`, for
a user who never got a notification), and a `bypass_suspected`/
`bypass_resolved` notification tap. The web twin also has a persistent
app-shell bypass banner as a third entry point — **deliberately not ported
here** (out of this WP's scope; the WorkManager background poll already
covers the notification half independently, and adding a persistent banner
is a real UI-surface decision matching the frozen-mockup discipline this
project holds Phase-4 UI to, not something to slip in as a side effect of
closing the routing gap).

### The routing gap, actually closed (`notifications/NotificationRouting.kt`)

`destinationFor(event: NotificationEvent): Destination?` is now nullable,
returning `null` for both bypass events (previously `Destination.SETTINGS`
— the recorded gap). A new `opensClientsSheetFor(event): Boolean` is `true`
for exactly those two events. `AndroidNotificationPoster` sets a new intent
extra, `EXTRA_OPEN_CLIENTS_SHEET`, only when `opensClientsSheetFor` says so,
and omits `EXTRA_DESTINATION` entirely when `destinationFor` is `null` — no
more forcing a bypass tap onto Settings at all. `MainActivity
.handleNotificationTap` checks the two extras independently (neither
early-returns on the other's absence), so a bypass tap now opens the
clients sheet directly, on top of whatever screen the user is currently on,
mirroring `web/js/components/notifications.js`'s own per-event
`target.kind` dispatch (a `"clients"` target calls `openClientsSheet()`
directly, never switching view first) rather than re-deriving a
destination-based approximation of it.
`NotificationRoutingTest`'s `bypass_suspected`/`bypass_resolved` cases now
assert BOTH `destinationFor(event) == null` (mutation target: distinct from
asserting `!= Destination.SETTINGS`, which alone would not catch a
regression to some OTHER wrong destination) and
`opensClientsSheetFor(event) == true` — either assertion alone would miss
half of a regression back toward the old behaviour.

### Model stability (`ui/clients/logic/ClientsView.kt`)

The WP brief's explicit ask ("follow the pattern the 4b.5 reviewer
endorsed — a tick that changes only a drift field must not rebuild the
row") is ported as a genuine, if differently-shaped, claim on this
platform — see that file's kdoc for the full reasoning: Compose's
`LazyColumn`/keyed recomposition has no imperative "rebuild the DOM" step
to avoid the way `web/js/lib/clients-render-plan.js`'s `full`/`patch`/
`rebuild` verdict exists for, so this WP does not port a redundant
render-plan diff object. What *is* still a real, testable claim is
`ClientRowModel`'s field split: [`ClientRowStats`] is the ONLY field a poll
tick may change while `clientId`/`bypassSuspected`/`addresses` stay put —
i.e. a stats-only tick can never also silently move a client to the other
section, and a section flip can never hide inside what looks like a stats
update. `ClientsViewTest` pins both directions with the same
`a.copy(volatileField = b.volatileField) == b` technique
`JobCardModelTest` uses for the `stop_request`-only diff.

### Wording contract (`ClientsCrossFrontendContractTest`)

Per the WP brief ("cross-frontend drift here is a defect — hold that
standard", referencing the 12/12 parity-mutation bar 4b.4/4b.5/4b.6 already
cleared), every section heading, fallback phrase, and the not-accusing
`BYPASS_EXPLANATION` sentence is literal-pinned against
`web/js/lib/clients-view.js`'s own hand-transcribed text by reading
`strings.xml` structurally (the same `readResFile`/`extractStringResource`
technique `StatusIconCrossFrontendContractTest` already established for the
status-word table) — never derived from the resource under test itself.

**A real finding from actually reading the web source, not trusting its own
comment (`docs/LEARNINGS.md` "verify empirically over believing docs"):**
`clients-view.js`'s header comment claims `BYPASS_EXPLANATION` is "shown
once per section rather than repeated per row", but `clients-sheet.js
::buildRow` actually appends a fresh hint paragraph inside its `if (bypass)`
branch for EVERY card it builds — once per bypassing client, not once per
section. `ClientsSheet.kt`'s `ClientRow` ports the code (an explanation
paragraph under every bypass-suspected row), not the stale comment; both
`ClientsView.kt`'s and `strings.xml`'s comments record the correction so a
future reader does not re-trust the same stale line.

### Tests (WP 4b.10)

16 new/changed JVM unit tests (550 total with WP 4b.8's 534 baseline: +10
`ClientsViewTest`, +5 `ClientsCrossFrontendContractTest`, net +1 in
`NotificationRoutingTest` — 2 old bypass-routes-to-Settings cases replaced
by 2 new bypass-opens-sheet cases plus 1 new `EXTRA_OPEN_CLIENTS_SHEET`
literal pin), no Robolectric/emulator dependency. `ClientsController`
itself is intentionally NOT given its own test file — same "thin glue,
tested only through its `logic/` pure functions" treatment
`DownloadsController`/`DetailController`/`SettingsController` already get
(none of the four has a dedicated `*ControllerTest.kt`); its `refreshOnce`/
`open`/`pollForever` are direct, un-branchy wiring around
`ClientsRepository`/`partitionClients`, with no idempotency or ordering
claim sharp enough to warrant the exception `OnboardingControllerTest`
earns for the field-clearing pin its own WP brief explicitly asked for.

Verified command + output tail:

```
$ ./gradlew.bat test lintDebug assembleDebug
...
BUILD SUCCESSFUL in 53s
74 actionable tasks: 37 executed, 37 up-to-date
```

550/0/0 (summed from the `testDebugUnitTest`/`testReleaseUnitTest` XML
reports' `tests=`/`failures=`/`errors=` attributes);
`app/app/build/reports/lint-results-debug.txt`: "No issues found."

### What WP 4b.10 deliberately did NOT do — the honest device-test list adds

Everything above the Android-framework boundary is unit-tested exactly like
every earlier UI WP; what remains real, uncovered device territory:

1. **`ModalBottomSheet` rendering, scroll, and dismiss gestures for the
   clients sheet specifically** — same class of item every earlier sheet/
   screen WP has recorded (`IdentityScreen`, `GameDetailSheet`), never
   exercised outside a JVM test.
2. **A real `bypass_suspected` transition observed live**, end to end from
   a genuine `vault-agent` report through `GET /v1/clients` into the sheet
   and the notification tap — `NotificationDifferTest`/`ClientsViewTest`
   prove the two halves independently, but not the seam between a live
   device notification tap and the sheet actually opening on top of
   whatever screen was showing.
3. **No persistent bypass banner** (unlike `web`'s app-shell banner) — see
   this section's own "Two entry points" note above for why that is a
   scope decision, not a gap this WP failed to close.
4. **`clearAndSetSemantics` on the badge icon has not been heard by a real
   screen reader** — proven only by inspection that the equivalent web
   `aria-hidden` treatment exists for the same reason, same honest caveat
   every other icon-decorative-vs-accessible-text call in this app carries
   until a device pass.

## Release build, signing, distribution, and carry-over cleanup (WP 4b.9)

Last per `docs/WORKPACKAGES.md` Phase 4b ("4b.9 — last"). Two independent
halves: a real release-signing story (never a secret in the repo), and the
carry-over list `docs/WORKPACKAGES.md`'s Phase 4b header names by name.

**Opus review verdict: PASS, no blockers (joint review with WP 4b.10).**
The key-material audit (extension sweep, ignored files,
`app/app/build/`, `app/.gradle/`, every git-object-store ref including
unreachable blobs, stash, staged, `~/.android/`, the toolchain dir) found
no keystore, signed artefact, password, alias, or store path anywhere in
the tree or its history. Four should-fixes landed, called out inline at
each fix site below (S1: `packageRelease`/`packageReleaseBundle` bypassed
the signing guard; S2: the missing persistent bypass banner needed its
own recorded-divergence entry, see the WP 4b.10 checkbox note and
`docs/WORKPACKAGES.md`'s Phase 4a divergence cluster; S3: `.gitignore`
widened for `*.p12`/`*.pfx`/`*.bks` plus a root-level belt; S4: the
self-contradictory "no keystore, anywhere, ever" line below), plus a
handful of nits (stale KDoc cross-references, an AGP v2/v3-default
overclaim, a stale WORKPACKAGES.md truncation-marker line, `IdentityScreen
.kt`'s WP 4b.3 directory tree left un-annotated after its WP 4b.9 move).

### Release signing (`app/app/build.gradle.kts`, `app/keystore.properties.example`)

**Never a secret in the repo, by construction, not by discipline.** Values
come from a gitignored `app/keystore.properties` file (the pattern was
already reserved in `app/.gitignore` since WP 4b.1 — `*.jks`/`*.keystore`/
`keystore.properties` — this WP is the first to actually wire it up) or
from environment variables (`VAULT_RELEASE_STORE_FILE`/
`VAULT_RELEASE_STORE_PASSWORD`/`VAULT_RELEASE_KEY_ALIAS`/
`VAULT_RELEASE_KEY_PASSWORD`) as a CI-style fallback — the properties file
wins when both are present (`releaseSigningValue`'s Elvis chain). This
app **never generates a keystore itself** — see the walkthrough below for
the `keytool` command the user runs. No keystore, password, or alias
appears anywhere in this tree, in a test fixture, or in git history from
this WP.

**A missing/incomplete config is an immediate, actionable build failure —
never a crash, never a silently unsigned APK.** `assembleDebug` needs
none of this and is completely unaffected (debug builds keep using AGP's
own auto-generated debug keystore, untouched); `gradle.taskGraph.whenReady`
checks specifically for `assembleRelease`/`bundleRelease`/`installRelease`/
`packageRelease`/`packageReleaseBundle` in the resolved task graph (by
NAME, not by any looser "contains Release" match —
`test`/`testReleaseUnitTest`/`lintDebug`/`compileReleaseKotlin` all need no
signing config at all and must keep working with zero keystore setup) and
throws a `GradleException` with the exact fix (create the properties file
from the committed template, or set the four env vars) before any
compilation or packaging work starts.

**Review round 1 finding (Opus), fixed here.** The first version of this
guard only listed `assembleRelease`/`bundleRelease`/`installRelease` —
`assembleRelease`/`bundleRelease` are aggregate lifecycle tasks that
DEPEND ON `packageRelease`/`packageReleaseBundle`, but running
`./gradlew packageRelease` (or `packageReleaseBundle`) DIRECTLY bypassed
the guard entirely: measured, `BUILD SUCCESSFUL`, producing
`app/build/outputs/apk/release/app-release-unsigned.apk`. Severity was
bounded even before the fix — the artefact is self-labelled `-unsigned`,
`apksigner` refuses it, and there is no silent fallback to the debug key —
but the absolute claim above was falsifiable in one command, exactly the
`docs/LEARNINGS.md` pattern this project keeps re-learning ("Entry-point
docs describe SHIPPED behavior... grep the code... before claiming it
works"). Both task names are now in the guarded set; `installRelease`
stays listed too even though it is currently inert on its own (AGP will
not even create an install task for an unsignable variant, and once
signing IS configured this guard can never fire for it) — kept as a
forward-compat belt, not a live guarded path today. Re-verified after the
fix, directly against the exact task that bypassed the guard before:

```
$ ./gradlew.bat packageRelease            # no keystore.properties, no env vars
...
FAILURE: Build failed with an exception.
* What went wrong:
Release signing is not configured -- refusing to build an unsigned release artefact.
...
BUILD FAILED in 15s
$ ./gradlew.bat packageReleaseBundle      # no keystore.properties, no env vars
...
FAILURE: Build failed with an exception.
* What went wrong:
Release signing is not configured -- refusing to build an unsigned release artefact.
...
BUILD FAILED in 1s
```

Verified empirically, not just by reading the script (`docs/LEARNINGS.md`
"verify empirically"):

```
$ ./gradlew.bat assembleRelease          # no keystore.properties, no env vars
...
FAILURE: Build failed with an exception.
* What went wrong:
Release signing is not configured -- refusing to build an unsigned release artefact.
...
BUILD FAILED in 1s
```

— fails in ~1 second, before `compileReleaseKotlin` even runs. With a
throwaway test keystore (`keytool`-generated, used ONLY for this
verification and deleted immediately afterward, never committed):

```
$ ./gradlew.bat assembleRelease          # app/keystore.properties present
...
BUILD SUCCESSFUL in 20s
48 actionable tasks: 33 executed, 15 up-to-date
$ apksigner verify --verbose app/app/build/outputs/apk/release/app-release.apk
Verifies
Verified using v2 scheme (APK Signature Scheme v2): true
Number of signers: 1
```

— a genuinely signed APK, confirmed with `apksigner` (the AGP-bundled
`build-tools` verifier), not just "the task didn't error".
`./gradlew.bat assembleDebug test lintDebug` was re-run with NO keystore
configured immediately after, green (74 actionable tasks, 37 executed / 37
up-to-date), proving the debug/test/lint paths are genuinely unaffected by
any of this rather than accidentally passing because the test keystore was
still in place.

**Creating the keystore (the user runs this, once, and keeps the file and
its passwords somewhere durable and backed up — losing it means every
future release build can never again be recognized as an update to the
same app by any device that installed an earlier signed build):**

```bash
keytool -genkeypair -v \
  -keystore /absolute/path/to/steamvault-release.jks \
  -keyalg RSA -keysize 2048 -validity 10000 \
  -alias steamvault
```

`keytool` (bundled with any JDK — this project's own pinned JDK 17 works)
prompts for a keystore password, a key password, and the certificate's
distinguished-name fields (name/org/city/state/country) — none of it needs
to be a real identity; it is a self-signed certificate purely to prove
"every release build claiming to be this app came from the same key",
which is all Android's package-signature check actually verifies. `-validity
10000` (~27 years) avoids the certificate itself expiring mid-project;
Android does not currently reject an expired signing certificate for
already-installed apps, but starting fresh with a long validity avoids the
question entirely.

**Building and verifying a release APK:**

1. Copy `app/keystore.properties.example` to `app/keystore.properties`,
   fill in `storeFile`/`storePassword`/`keyAlias`/`keyPassword` from what
   `keytool` just created (or export the four `VAULT_RELEASE_*` env vars
   instead — see the properties file's own comment for the exact names).
2. `./gradlew.bat assembleRelease` from `app/` (the same wrapper every
   other command in this README uses). The signed APK lands at
   `app/app/build/outputs/apk/release/app-release.apk`.
3. Verify the signature independently of Gradle's own claim:
   `<sdk>/build-tools/<version>/apksigner verify --verbose app-release.apk`
   should print `Verifies` and `Verified using v2 scheme (APK Signature
   Scheme v2): true` — measured against this project's own pinned AGP
   8.7.3/compileSdk 35 defaults (v3/v3.1/v4 all report `false` on the
   verification transcript below; do not expect them without explicitly
   opting in via `signingConfigs`). A bare `jarsigner -verify` also works
   but only checks the older v1/JAR scheme, which this build does not
   produce at all (`Verified using v1 scheme (JAR signing): false`) —
   `apksigner` is the correct tool, not `jarsigner`, for exactly that
   reason.

**No Play Store requirement; F-Droid is a long-term goal, per
`docs/WORKPACKAGES.md` Phase 4b's own line item ("APK build docs; no Play
Store requirement; F-Droid long-term").** This WP ships a signed,
side-loadable APK and the documentation to build one — it does NOT set up
a Play Console listing (Google account, store presence, review process —
an account-level, with-the-user decision this project's own working
agreement reserves for the user, same class of decision as WP 5.5's GitHub
org move) or an F-Droid metadata/build-recipe submission (F-Droid's own
reproducible-build requirements are a real, separate piece of work — a
future WP once the app is otherwise stable, not a WP 4b.9 packaging
after-thought). Distribution for now is: build the signed APK per the
steps above, transfer it to a device (ADB, a file share, a self-hosted
download link — the user's choice), and install with "install unknown
apps" allowed for that source, the standard side-load flow for any
Android app outside a store.

### Carry-over cleanup (`docs/WORKPACKAGES.md` Phase 4b header)

Each item below was individually re-verified against the actual code
before doing anything — `docs/LEARNINGS.md`'s standing rule ("Entry-point
docs describe SHIPPED behavior... grep the code... before claiming it
works") applies just as much to a carry-over TODO list as to a README.

1. **Move the unreferenced `GalleryScreen` (4b.1) and `IdentityScreen`
   (4b.3, superseded by `SettingsScreen` in 4b.7) to `src/debug/`, keeping
   their tests working.** `GalleryScreen` was **already moved** to
   `app/app/src/debug/java/dev/steamvault/app/ui/gallery/GalleryScreen.kt`
   during WP 4b.4 itself (that WP's own review nit — see the file's kdoc,
   unchanged by this WP; `git log` confirms the move landed in the WP 4b.4
   commit, not here) — the carry-over list was simply not updated to drop
   it once done. **`IdentityScreen` is moved by this WP**, to
   `app/app/src/debug/java/dev/steamvault/app/ui/identity/IdentityScreen.kt`
   — `src/debug/` is AGP's standard "compiled for debug builds only"
   source set (already proven correct by `GalleryScreen`'s own move; no
   `sourceSets {}` block is needed in `build.gradle.kts`, AGP wires
   `src/debug/java` to the debug variant by convention), so this
   now-unreferenced screen is excluded from the signed `release` variant
   by construction rather than shipping dead UI code into it. **No test
   referenced `IdentityScreen` directly, before or after the move** — it
   was never unit-tested on its own (only `dev.steamvault.app.repo
   .SteamIdentityState`/`SteamIdentityRepository`, which live under
   `repo/`, untouched by this move, and are covered by
   `SteamIdentityRepositoryTest`) — so "keeping tests working" was
   satisfied by construction; `./gradlew.bat test` (550/0/0, see below)
   confirms nothing broke.
2. **Re-check the `security-crypto` 1.1.0-alpha pin for a GA release —
   report, recommend, do not silently bump.** A GA release now exists:
   `androidx.security:security-crypto:1.1.0` (verified against Google's
   Maven metadata AND by downloading the `.aar` and listing its
   `classes.jar` — `MasterKey`/`MasterKey.Builder`/
   `EncryptedSharedPreferences` are all present, the same classes
   `EncryptedCredentialStore.kt` already depends on). **Not bumped here**
   — see `gradle/libs.versions.toml`'s expanded comment for the full
   finding and the explicit recommendation (bump in its own reviewed
   change once a device is available to re-verify
   `EncryptedCredentialStore` against a real Keystore on the new artifact
   — this environment has never been able to run that check at all, for
   any version, and a credential-storage dependency bump is exactly the
   wrong place to skip it).
3. **Pin the Kotlin `LogExcerpt` truncation marker to position 0
   (`startsWith`, not `contains`) — the web twin was pinned in 4a.8, this
   is the open half.** Verified against the actual code: this was
   **already correct and already pinned** —
   `ui/downloads/logic/LogExcerpt.kt`'s `selectExcerptDisplay` has used
   `text.startsWith(TRUNCATION_MARKER)` since WP 4b.5 (never `contains`),
   and `LogExcerptTest`'s `` `a truncation marker appearing mid-stream, not
   at position 0, is not treated as truncation` `` test already exercises
   the exact `startsWith`-vs-`contains` mutation this carry-over item
   describes, with the MUTATION TARGET comment spelling out why. This
   matches `docs/PROJECT_PLAN.md` §7's own WP 4b.5 entry, which already
   says "truncation marker pinned to position 0" in its evidence note —
   the carry-over line in `docs/WORKPACKAGES.md` was stale from before
   that fix landed and was never removed once it did. **No code change
   needed; the carry-over list entry is removed below as closed.**
4. **Notification icon art: 4b.8 reuses the launcher monochrome — either
   provide something better, or record explicitly why reuse is acceptable
   for v1.** No image-generation/design tooling exists in this
   environment to "provide something better" honestly, so this WP makes
   the earlier WP 4b.8 note a FINAL decision rather than leaving it as a
   deferred "revisit" pointing at this exact WP: **kept for v1.**
   Rationale, restated as a decision and not just an observation: the
   launcher's monochrome layer (`ic_launcher_monochrome.xml`) is already a
   pure white-on-transparent silhouette at exactly the visual weight a
   status-bar/notification small icon needs — Android's own guidance for
   notification icons IS a flat white silhouette, which is precisely what
   this asset already is, not merely a convenient reuse of unrelated art.
   A bespoke glyph remains a legitimate future polish item (tracked as a
   general "art pass" item, not a blocking WP 4b.9 carry-over) but is not
   needed for a correct, guideline-compliant v1 notification.

### Updated honest on-device list

WP 4b.10 adds (see that section above for the full list): `ModalBottomSheet`
rendering/dismiss for the clients sheet specifically, a real
`bypass_suspected` transition observed end to end from a live device
notification tap into the sheet, and `clearAndSetSemantics`'s screen-reader
behaviour on the badge icon.

WP 4b.9 adds exactly one item, and it is unavoidable in this environment:
**the entire release-signing walkthrough above (`keytool` keystore
creation, `assembleRelease`, `apksigner verify`) has been run and proven
end-to-end in THIS environment with a throwaway test keystore — but never
with a real device installing the resulting APK.** Everything Gradle/
`apksigner` can attest to (the artefact is genuinely signed, with the
right scheme, and the failure path is a clean Gradle error rather than a
crash or a silent unsigned build) is proven above; whether the resulting
APK actually installs and launches on a real device (`adb install`, "install
unknown apps" permission flow, first-launch behaviour) is not, for the same
"no emulator/device available" reason every earlier 4b.x WP records.

### Tests (WP 4b.9)

No new test files — this WP is packaging/build-config/doc/reorg work, not
a new logic module. `LogExcerptTest` and `SteamIdentityRepositoryTest`
(both pre-existing) continue to cover the two carry-over items that turned
out to already be closed; the `IdentityScreen` move touches no test file
at all (see carry-over item 1 above). Full suite, run from a clean state
after the `IdentityScreen` move and the signing-config change, with no
`keystore.properties` present:

```
$ ./gradlew.bat test lintDebug assembleDebug
...
BUILD SUCCESSFUL in 24s
74 actionable tasks: 21 executed, 53 up-to-date
```

550/0/0 (same count as WP 4b.10's — this WP adds no new JVM tests, and the
`IdentityScreen` move/signing-config work changes zero existing test
outcomes); `app/app/build/reports/lint-results-debug.txt`: "No issues
found." `assembleRelease` behaviour (fail-without-keystore, succeed-and-
verify-with-one) is proven separately above rather than folded into this
count, since it is a build-configuration property, not a JVM test.

### What WP 4b.9 deliberately did NOT do

- **No keystore is generated by the build, ever** — the whole point of the
  design; the `keytool` command above is written for the USER to run,
  with their own passwords, on their own machine. Review fix (S4): the one
  throwaway keystore behind the verification transcript above was created
  OUTSIDE the repo, used only to prove the signing path works, and deleted
  in-session — reworded here because the original "anywhere, ever"
  phrasing was a literally false statement about the one section a reader
  consults for exactly this question, even though the intent (the SHIPPED
  build/config never generates one) was always correct.
- **No Play Store listing, no F-Droid submission** — see the "No Play
  Store requirement" note above for why both are separate, later,
  with-the-user (or metadata-heavy) efforts, not this WP's job.
- **No bespoke notification icon art** — decided, not merely deferred
  again (see carry-over item 4); revisit only if a future design pass
  wants one, not because v1 is missing something guideline-required.
- **No `security-crypto` version bump** — reported and recommended, per
  the brief's explicit instruction not to silently bump a security
  dependency; see carry-over item 2 and `gradle/libs.versions.toml`'s
  comment for the full finding.
- **No on-device install/launch verification of the signed release APK**
  — see "Updated honest on-device list" above; unreachable in this
  environment for the same reason every earlier 4b.x WP records.

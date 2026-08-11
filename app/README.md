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

### Known residual: no request↔callback binding (replay), reviewer-flagged

`buildLoginUrl()`/`SteamOpenIdConfig.RETURN_TO` carry no per-login `state`
parameter, and `completeLogin` never checks the callback's
`openid.return_to` against the specific URL THIS login attempt built, nor
tracks `openid.response_nonce` to reject a repeat. Concretely: a
malicious app already installed on the same device could capture a
**genuine**, Valve-signed OpenID assertion for the ATTACKER's own Steam
account (e.g. by triggering its own sign-in against this app's exact
`return_to` scheme+host+path, since nothing here is per-attempt-unique)
and replay it into SteamVault's intent-filter — `check_authentication`
would legitimately return `is_valid:true` (it IS a genuine, unmodified
Valve assertion, just not the one THIS app's button press initiated), and
the app would flip its displayed identity to the attacker's account.

**Blast radius, stated precisely:** this can only ever *replace which
Steam account is shown as signed in* on the victim's device — it cannot
forge a claim for an account the attacker does not themselves control,
cannot touch vault-api (ADR-0004 decision 2's isolation holds regardless),
and leaks no secret (there is no secret in this flow to leak). Calling
`signOut()` fully recovers.

**Mitigation, deferred rather than added here (scope discipline for this
WP):** the standard OpenID/OAuth fix is a per-login random `state` value
appended to `RETURN_TO`/checked on the way back in, which this WP does
not add — flagged as a concrete candidate for WP 4b.7 (settings/onboarding,
serial after 4b.3) or WP 4b.9 (release hardening pass), not silently
deferred.

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
4. **BLOCKED UNTIL WP 4b.7: the real `GetOwnedGames`/`GetPlayerSummaries`
   calls against `api.steampowered.com` with a genuine Steam Web API key**
   (both the library-count preview AND the persona-name half of
   `refreshPersonaName`). `setWebApiKey()` exists only on the repository —
   this WP deliberately adds no debug/dev input field for it (scope
   discipline: a throwaway input widget is not the real onboarding UI and
   would need its own review), so there is currently NO way to reach this
   code path from the running app at all until WP 4b.7 lands the real
   key-entry surface. **Do not report this as a bug when testing WP 4b.3
   alone** — `IdentityScreen` will correctly show "Add your Steam Web API
   key in Settings..." and stop there, because Settings does not exist
   yet.
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

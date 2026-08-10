package dev.steamvault.app.ui.status

import androidx.compose.ui.graphics.Color
import dev.steamvault.app.ui.theme.VaultColors

/**
 * Pure logic for the status-icon system (WP 4b.1) — deliberately kept free
 * of any Android/Compose runtime dependency beyond plain data types
 * (`Color` is a value class, not a framework call) so it is unit-testable
 * on the plain JVM without Robolectric or an emulator (none is available in
 * this environment).
 */

/** Which glyph shape a given [StatusKind] uses — ported from `KIND_GLYPH` in
 *  web/js/components/status-icon.js. */
enum class GlyphShape { CHECK, DOWNLOAD, REFRESH, BANG, PAUSE, STOP }

/** [StatusKind] -> [GlyphShape], 1:1 with web's `KIND_GLYPH` table. */
fun glyphFor(kind: StatusKind): GlyphShape = when (kind) {
    StatusKind.CACHED -> GlyphShape.CHECK
    StatusKind.NONE -> GlyphShape.DOWNLOAD
    StatusKind.STALE -> GlyphShape.REFRESH
    StatusKind.RUNNING -> GlyphShape.DOWNLOAD
    StatusKind.UPDATING -> GlyphShape.REFRESH
    StatusKind.VERIFY -> GlyphShape.REFRESH
    StatusKind.PAUSED -> GlyphShape.PAUSE
    StatusKind.ERROR -> GlyphShape.BANG
    StatusKind.WARN -> GlyphShape.BANG
    StatusKind.CANCELLED -> GlyphShape.STOP
}

/**
 * The kinds that carry motion at all, independent of the reduced-motion
 * setting — 1:1 with the web CSS rules `.sic.k-running .dla`,
 * `.sic.k-updating .rot`, `.sic.k-verify .rot` (theme.css). Every other kind
 * (cached, none, stale, paused, error, warn, cancelled) is completely still
 * by design — "a library at rest never flickers" (mockup NOTES.md round 5).
 */
private val ANIMATED_KINDS = setOf(StatusKind.RUNNING, StatusKind.UPDATING, StatusKind.VERIFY)

/**
 * The animate-or-not decision, extracted as a pure function so the
 * reduced-motion disable path is provable in a JVM unit test without a
 * device (WP brief).
 *
 * @param kind the status kind being rendered.
 * @param animatorsEnabled the live value of `ValueAnimator.areAnimatorsEnabled()`
 *   (see [dev.steamvault.app.ui.status.AnimatorsEnabled] for how the caller
 *   obtains it) — false when the system "Remove animations" accessibility
 *   toggle / Settings > Developer options > Animator duration scale is set
 *   to "Animation off".
 * @return true only if this kind is one of the motion-carrying kinds AND
 *   the system has not disabled animations.
 */
fun shouldAnimate(kind: StatusKind, animatorsEnabled: Boolean): Boolean =
    animatorsEnabled && kind in ANIMATED_KINDS

/**
 * Background colour for a status-icon circle — 1:1 with the `.sic.k-*`
 * background rules in web/css/theme.css. [StatusKind.PAUSED] intentionally
 * shares `--run` with RUNNING/UPDATING (same as the web CSS), and
 * [StatusKind.VERIFY] uses the accent colour, not a status colour — both
 * ported unchanged from the CSS, not independent Android choices.
 */
fun backgroundFor(kind: StatusKind): Color = when (kind) {
    StatusKind.CACHED -> VaultColors.StatusOk
    StatusKind.NONE -> VaultColors.StatusNone
    StatusKind.STALE -> VaultColors.StatusStale
    StatusKind.PAUSED -> VaultColors.StatusRun
    StatusKind.RUNNING -> VaultColors.StatusRun
    StatusKind.UPDATING -> VaultColors.StatusRun
    StatusKind.VERIFY -> VaultColors.Accent
    StatusKind.ERROR -> VaultColors.StatusDanger
    StatusKind.WARN -> VaultColors.StatusStale
    StatusKind.CANCELLED -> VaultColors.Dim2
}

/**
 * Glyph ink colour for a status-icon circle. Every kind uses the shared
 * dark ink (`.sic { color:#08120F }`) EXCEPT cancelled, which uses the
 * light text colour against its muted background (`.sic.k-cancelled
 * { color:var(--text) }`) — ported unchanged from theme.css.
 */
fun inkFor(kind: StatusKind): Color = when (kind) {
    StatusKind.CANCELLED -> VaultColors.Text
    else -> VaultColors.StatusIconInk
}

/**
 * Vertical drift (in glyph-local units, same scale as the 24-unit SVG
 * viewBox) of the download glyph's arrow group, ported from the
 * `vault-dlslide` CSS keyframes (theme.css): linear interpolation from
 * -1.6 (progress 0) to 1.8 (progress 1). Pure function so the keyframe
 * math is unit-testable without a running animation.
 *
 * @param progress 0f..1f, one full loop of the 2.6s `vault-dlslide` cycle.
 */
fun downloadDriftFraction(progress: Float): Float {
    val clamped = progress.coerceIn(0f, 1f)
    return -1.6f + clamped * (1.8f - (-1.6f))
}

/**
 * Opacity of the download glyph's arrow group at a given point in the
 * `vault-dlslide` loop: .35 -> 1 over the first 30%, held at 1 through 70%,
 * then back down to .35 over the last 30% — ported from the CSS keyframes
 * `0% {opacity:.35} 30% {opacity:1} 70% {opacity:1} 100% {opacity:.35}`.
 * Never reaches 0 — "a status icon must never be blank" (mockup NOTES.md
 * round 7): the arrow doubles as the tap target and must stay visible.
 */
fun downloadOpacityFraction(progress: Float): Float {
    val t = progress.coerceIn(0f, 1f)
    return when {
        t <= 0.3f -> 0.35f + (t / 0.3f) * (1f - 0.35f)
        t <= 0.7f -> 1f
        else -> 1f - ((t - 0.7f) / 0.3f) * (1f - 0.35f)
    }
}

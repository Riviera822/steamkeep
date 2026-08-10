package dev.steamvault.app.ui.status

import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.size
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.rotate
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.sin

/**
 * Circle diameters — 1:1 with the web `.sic`/`.sic-sm`/`.sic-lg` `--sz`
 * values (theme.css: 17px base, 16px small, 19px large).
 */
enum class StatusIconSize(val diameter: Dp) {
    SMALL(16.dp),
    MEDIUM(17.dp),
    LARGE(19.dp),
}

/**
 * The status-icon component (WP 4b.1) — Compose port of
 * `web/js/components/status-icon.js` / `web/css/theme.css`'s `.sic` rules.
 *
 * Shape is the primary, colour-blind-safe channel (mockup round 5): a
 * filled circle in the status hue ([backgroundFor]) carrying a glyph
 * ([glyphFor]) drawn in a single ink colour ([inkFor]). Colour is only the
 * third cue, which is why every kind renders a structurally distinct
 * glyph, not just a different-coloured dot.
 *
 * Motion is reserved for kinds actually representing in-flight server
 * activity ([shouldAnimate]) and is switched off system-wide whenever
 * `ValueAnimator.areAnimatorsEnabled()` is false (see
 * [rememberAnimatorsEnabled] for how that signal is sourced) — the Android
 * equivalent of the web app's `prefers-reduced-motion` block.
 *
 * The glyph paths for CHECK/DOWNLOAD/BANG/PAUSE/STOP are a direct,
 * coordinate-for-coordinate port of the SVG `d` attributes in
 * status-icon.js (same 24x24 unit grid). The REFRESH glyph (two opposing
 * curved arrows) is a geometric APPROXIMATION using two circular arcs
 * rather than an exact port of the SVG's elliptical-arc path commands —
 * documented deviation, see the WP 4b.1 report; it preserves the design
 * intent ("two opposing curved arrows forming one circle", mockup round 6)
 * without porting SVG arc-flag math into Compose's `Path.addArc`.
 */
@Composable
fun StatusIcon(
    kind: StatusKind,
    modifier: Modifier = Modifier,
    size: StatusIconSize = StatusIconSize.MEDIUM,
) {
    val animatorsEnabled by rememberAnimatorsEnabled()
    val animate = shouldAnimate(kind, animatorsEnabled)
    val glyph = glyphFor(kind)
    val bg = backgroundFor(kind)
    val ink = inkFor(kind)
    val label = stringResource(kind.labelRes)

    val transition = rememberInfiniteTransition(label = "status-icon-transition")

    var downloadProgress = 0f
    if (animate && glyph == GlyphShape.DOWNLOAD) {
        val p by transition.animateFloat(
            initialValue = 0f,
            targetValue = 1f,
            animationSpec = infiniteRepeatable(tween(2600, easing = LinearEasing)),
            label = "download-progress",
        )
        downloadProgress = p
    }

    var refreshDegrees = 0f
    if (animate && glyph == GlyphShape.REFRESH) {
        val p by transition.animateFloat(
            initialValue = 0f,
            targetValue = 360f,
            animationSpec = infiniteRepeatable(tween(2200, easing = LinearEasing)),
            label = "refresh-rotation",
        )
        refreshDegrees = p
    }

    Canvas(
        modifier
            .size(size.diameter)
            .semantics { contentDescription = label },
    ) {
        drawCircle(color = bg)

        // The glyph occupies 64% of the circle's diameter, centred — 1:1
        // with the web rule `.sic svg{ width:64%; height:64% }`.
        val glyphSize = this.size.minDimension * 0.64f
        val scale = glyphSize / 24f
        val origin = Offset(
            (this.size.width - glyphSize) / 2f,
            (this.size.height - glyphSize) / 2f,
        )
        val strokeWidth = 2.7f * scale
        val stroke = Stroke(width = strokeWidth, cap = StrokeCap.Round, join = StrokeJoin.Round)

        fun pt(x: Float, y: Float) = Offset(origin.x + x * scale, origin.y + y * scale)

        when (glyph) {
            GlyphShape.CHECK -> {
                // Ported unchanged: "M5 12.5 10 17.5 19 7"
                val path = Path().apply {
                    moveTo(pt(5f, 12.5f).x, pt(5f, 12.5f).y)
                    lineTo(pt(10f, 17.5f).x, pt(10f, 17.5f).y)
                    lineTo(pt(19f, 7f).x, pt(19f, 7f).y)
                }
                drawPath(path, color = ink, style = stroke)
            }

            GlyphShape.DOWNLOAD -> {
                // Ported unchanged: arrow "M12 3.5V13" + "M7.4 8.7 12 13.3
                // 16.6 8.7", baseline "M5 19.6h14".
                //
                // driftPx/alpha only apply the keyframe math while actually
                // animating. Without this guard, reduced motion (or the
                // static NONE kind, which never animates) would freeze the
                // arrow at the t=0 keyframe position (-1.6 units, opacity
                // .35) instead of its neutral, unshifted, fully-opaque
                // resting pose — a static icon should render at its plain
                // SVG coordinates, not a frozen frame of the animation.
                val driftPx = if (animate) downloadDriftFraction(downloadProgress) * scale else 0f
                val alpha = if (animate) downloadOpacityFraction(downloadProgress) else 1f
                val arrow = Path().apply {
                    moveTo(pt(12f, 3.5f).x, pt(12f, 3.5f).y + driftPx)
                    lineTo(pt(12f, 13f).x, pt(12f, 13f).y + driftPx)
                    moveTo(pt(7.4f, 8.7f).x, pt(7.4f, 8.7f).y + driftPx)
                    lineTo(pt(12f, 13.3f).x, pt(12f, 13.3f).y + driftPx)
                    lineTo(pt(16.6f, 8.7f).x, pt(16.6f, 8.7f).y + driftPx)
                }
                drawPath(arrow, color = ink.copy(alpha = alpha), style = stroke)

                // Baseline hidden specifically for the RUNNING kind (web:
                // `.k-running .dlbase{display:none}`) — keyed on the KIND,
                // not the animate flag, so reduced motion never re-reveals
                // it under a running job.
                if (kind != StatusKind.RUNNING) {
                    val baseline = Path().apply {
                        moveTo(pt(5f, 19.6f).x, pt(5f, 19.6f).y)
                        lineTo(pt(19f, 19.6f).x, pt(19f, 19.6f).y)
                    }
                    drawPath(baseline, color = ink, style = stroke)
                }
            }

            GlyphShape.REFRESH -> {
                // APPROXIMATED (see kdoc above): two 150-degree arcs, 180
                // degrees apart, each with a small arrowhead chevron at its
                // leading edge, rotating together as one group when animated.
                val center = pt(12f, 12f)
                val radius = 7.5f * scale
                rotate(refreshDegrees, pivot = center) {
                    for (startAngle in listOf(-105f, 75f)) {
                        val arcPath = Path().apply {
                            addArc(
                                oval = Rect(center = center, radius = radius),
                                startAngleDegrees = startAngle,
                                sweepAngleDegrees = 150f,
                            )
                        }
                        drawPath(arcPath, color = ink, style = stroke)

                        val angleRad = startAngle * (PI.toFloat() / 180f)
                        val tip = Offset(
                            center.x + radius * cos(angleRad),
                            center.y + radius * sin(angleRad),
                        )
                        rotate(startAngle + 90f, pivot = tip) {
                            val chevron = Path().apply {
                                moveTo(tip.x - 3.2f * scale, tip.y - 2.6f * scale)
                                lineTo(tip.x, tip.y)
                                lineTo(tip.x + 3.2f * scale, tip.y - 2.6f * scale)
                            }
                            drawPath(chevron, color = ink, style = stroke)
                        }
                    }
                }
            }

            GlyphShape.BANG -> {
                // Ported unchanged: "M12 5.5V13.2" (line) + "M12 17.7v.02"
                // (a near-zero-length round-capped line == a dot; drawn
                // directly as a circle of the stroke's half-width).
                val line = Path().apply {
                    moveTo(pt(12f, 5.5f).x, pt(12f, 5.5f).y)
                    lineTo(pt(12f, 13.2f).x, pt(12f, 13.2f).y)
                }
                drawPath(line, color = ink, style = stroke)
                drawCircle(color = ink, radius = strokeWidth / 2f, center = pt(12f, 17.71f))
            }

            GlyphShape.PAUSE -> {
                // Ported unchanged: two rounded bars, x=7/13.6 y=5.6 w=3.4
                // h=12.8 rx=1.3.
                drawRoundRect(
                    color = ink,
                    topLeft = pt(7f, 5.6f),
                    size = Size(3.4f * scale, 12.8f * scale),
                    cornerRadius = CornerRadius(1.3f * scale),
                )
                drawRoundRect(
                    color = ink,
                    topLeft = pt(13.6f, 5.6f),
                    size = Size(3.4f * scale, 12.8f * scale),
                    cornerRadius = CornerRadius(1.3f * scale),
                )
            }

            GlyphShape.STOP -> {
                // Ported unchanged: one rounded square, x=7 y=7 w=10 h=10 rx=1.6.
                drawRoundRect(
                    color = ink,
                    topLeft = pt(7f, 7f),
                    size = Size(10f * scale, 10f * scale),
                    cornerRadius = CornerRadius(1.6f * scale),
                )
            }
        }
    }
}

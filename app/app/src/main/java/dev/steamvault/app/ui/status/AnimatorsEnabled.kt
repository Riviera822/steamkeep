package dev.steamvault.app.ui.status

import android.animation.ValueAnimator
import android.database.ContentObserver
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.State
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.ui.platform.LocalContext

/**
 * How this app honours the system reduced-motion / animator-duration-scale
 * setting (WP 4b.1 brief: "document how Compose picks that up").
 *
 * Compose's own animation APIs (`InfiniteTransition`, `animate*AsState`, ...)
 * do NOT automatically respect the platform's "Remove animations"
 * accessibility toggle or Settings > Developer options > Animator duration
 * scale — those only gate the legacy `android.animation` framework
 * (`ValueAnimator`/`ObjectAnimator`) automatically. The documented way for a
 * Compose app to see the same signal is [ValueAnimator.areAnimatorsEnabled]
 * (public API since API 26, which happens to be exactly this app's minSdk):
 * it reads `Settings.Global.ANIMATOR_DURATION_SCALE` and returns false when
 * the scale is 0 — the value both the "Remove animations" toggle (Android 9+)
 * and the developer-options "Animator duration scale: Animation off" option
 * write.
 *
 * [rememberAnimatorsEnabled] wraps that check in a [State] that stays live
 * for as long as the composable is on screen, by registering a
 * [ContentObserver] on the backing `Settings.Global` URI — so toggling the
 * setting while the gallery screen is open updates the icons without a
 * recomposition trigger from anywhere else. The actual go/no-go decision is
 * the pure, unit-testable [shouldAnimate] function in StatusIconLogic.kt;
 * this file's only job is sourcing the live boolean.
 */
@Composable
fun rememberAnimatorsEnabled(): State<Boolean> {
    val context = LocalContext.current
    val state = remember { mutableStateOf(ValueAnimator.areAnimatorsEnabled()) }

    DisposableEffect(context) {
        val handler = Handler(Looper.getMainLooper())
        val observer = object : ContentObserver(handler) {
            override fun onChange(selfChange: Boolean) {
                state.value = ValueAnimator.areAnimatorsEnabled()
            }
        }
        val uri = Settings.Global.getUriFor(Settings.Global.ANIMATOR_DURATION_SCALE)
        context.contentResolver.registerContentObserver(uri, false, observer)
        // Re-check once on registration in case the value changed between
        // the initial remember{} read and the observer being wired up.
        state.value = ValueAnimator.areAnimatorsEnabled()

        onDispose {
            context.contentResolver.unregisterContentObserver(observer)
        }
    }

    return state
}

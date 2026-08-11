package dev.steamvault.app.ui.detail

import android.content.res.Resources
import dev.steamvault.app.R

/**
 * The dynamic toast/error strings [DetailController] produces after an
 * async action completes -- same escape-hatch pattern
 * `ui/library/LibraryStrings.kt` / `ui/downloads/DownloadsStrings.kt`
 * document (see app/README.md's "String resources" convention):
 * [DetailController] is a plain, non-`@Composable` Kotlin class, so it
 * cannot call `stringResource`/`pluralStringResource` itself; this
 * interface is resolved through plain `Resources.getString` instead and
 * injected the same way.
 *
 * Everything else the detail sheet shows (depot rows, the GC plan text, the
 * delete confirm bullet list, static labels) is resolved directly via
 * `stringResource(...)` inside `GameDetailSheet.kt`'s `@Composable`
 * functions, per app/README.md's DEFAULT rule -- only the handful of
 * strings below are ever produced from inside a `scope.launch { }` block.
 */
interface DetailStrings {
    fun queuedForDownload(): String
    fun pauseRequested(): String
    fun resuming(): String
    fun cancelRequested(): String
    fun actionFailedFallback(): String
    fun loadErrorFallback(): String
    fun freed(freedText: String): String
    /** The floor value when a delete freed nothing -- mirrors
     * `ui/library/LibraryController.kt`'s `ZERO_GB_LABEL` constant, exposed
     * through this interface instead since it is only ever consumed from
     * [DetailController]'s own non-`@Composable` code. */
    fun zeroBytesLabel(): String
}

class AndroidDetailStrings(private val resources: Resources) : DetailStrings {
    override fun queuedForDownload(): String = resources.getString(R.string.detail_toast_queued_for_download)
    override fun pauseRequested(): String = resources.getString(R.string.detail_toast_pause_requested)
    override fun resuming(): String = resources.getString(R.string.detail_toast_resuming)
    override fun cancelRequested(): String = resources.getString(R.string.detail_toast_cancel_requested)
    override fun actionFailedFallback(): String = resources.getString(R.string.detail_toast_action_failed)
    override fun loadErrorFallback(): String = resources.getString(R.string.detail_load_error_fallback)
    override fun freed(freedText: String): String = resources.getString(R.string.detail_toast_freed, freedText)
    override fun zeroBytesLabel(): String = resources.getString(R.string.detail_zero_bytes_label)
}

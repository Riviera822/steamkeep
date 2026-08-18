package dev.steamvault.app.ui.clients

import android.content.res.Resources
import dev.steamvault.app.R

/**
 * The one string [ClientsController] produces from outside composition (a
 * `GET /v1/clients` load error) — same escape-hatch pattern
 * `ui/downloads/DownloadsStrings.kt` / `ui/detail/DetailStrings.kt` document
 * (app/README.md's "String resources" convention): [ClientsController] is a
 * plain, non-`@Composable` Kotlin class, so it cannot call `stringResource`
 * itself.
 *
 * Everything else the sheet shows (headings, section words, the
 * not-accusing bypass explanation, per-row stats sentences) is resolved
 * directly via `stringResource`/`pluralStringResource` inside
 * `ClientsSheet.kt`'s `@Composable` functions from the plain data
 * `ui/clients/logic/ClientsView.kt` computes, per app/README.md's DEFAULT
 * rule.
 */
interface ClientsStrings {
    fun loadErrorFallback(): String
}

class AndroidClientsStrings(private val resources: Resources) : ClientsStrings {
    override fun loadErrorFallback(): String = resources.getString(R.string.clients_load_error_fallback)
}

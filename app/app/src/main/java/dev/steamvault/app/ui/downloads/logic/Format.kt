package dev.steamvault.app.ui.downloads.logic

import java.text.DateFormat
import java.time.DateTimeException
import java.time.Instant
import java.util.Date

/**
 * An ISO-8601 timestamp (`jobs.created_at`/`started_at`/`finished_at`, wire
 * format `"%Y-%m-%dT%H:%M:%SZ"` per `api/vault_api/jobs.py::TIMESTAMP_FORMAT`)
 * -> a locale-formatted display string (WP 4b.5) — Kotlin port of
 * `web/js/lib/format.js`'s `formatTimestamp`. `null`/unparseable input never
 * fabricates a time, same "nothing honest to print" posture
 * `ui/library/logic/Format.kt`'s `formatBytesGB` documents.
 * @return a display string, or `"—"` when there is nothing to show.
 */
fun formatTimestamp(iso: String?): String {
    if (iso.isNullOrBlank()) return "—"
    return try {
        val instant = Instant.parse(iso)
        DateFormat.getDateTimeInstance().format(Date.from(instant))
    } catch (_: DateTimeException) {
        "—"
    }
}

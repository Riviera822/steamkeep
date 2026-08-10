package dev.steamvault.app.net.model

import kotlinx.serialization.Serializable

/**
 * `GET /v1/clients` row — `vault_api/routers/clients.py::ClientOut`. The
 * WP 3.11/ADR-0008 hit-statistics/bypass fields default to their
 * documented "event feed is off" values (`0`/`null`/`false`,
 * api/README.md "Endpoints") so this decodes fine against a vault-api
 * build old enough not to send them yet.
 */
@Serializable
data class ClientOut(
    val client_id: String,
    val first_seen: String,
    val last_reported_at: String,
    val app_count: Int? = null,
    val source_addrs: List<String> = emptyList(),
    val cache_hits: Int = 0,
    val cache_misses: Int = 0,
    val bytes_served: Long = 0,
    val last_seen_in_cache_log: String? = null,
    val bypass_suspected: Boolean = false,
)

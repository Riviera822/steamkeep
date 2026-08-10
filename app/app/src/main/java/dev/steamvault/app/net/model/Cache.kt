package dev.steamvault.app.net.model

import kotlinx.serialization.Serializable

/** One row of `CacheSummaryOut.top_consumers` — `vault_api/routers/cache.py::TopConsumerOut`. */
@Serializable
data class TopConsumerOut(
    val appid: Int,
    val name: String? = null,
    val size_bytes: Long,
)

/** `CacheSummaryOut.unmapped_depots` — `vault_api/routers/cache.py::UnmappedDepotsOut`. */
@Serializable
data class UnmappedDepotsOut(
    val count: Int,
    val size_bytes: Long,
)

/** `GET /v1/cache/summary` — `vault_api/routers/cache.py::CacheSummaryOut`. */
@Serializable
data class CacheSummaryOut(
    val total_bytes: Long,
    val top_consumers: List<TopConsumerOut> = emptyList(),
    val unmapped_depots: UnmappedDepotsOut,
    val free_disk_bytes: Long? = null,
)

/** One row of `CacheDeletionOut.deleted_depots` — `DeletedDepotOut`. */
@Serializable
data class DeletedDepotOut(
    val depotid: Int,
    val size_bytes_freed: Long,
    val shared_with_uncached: List<Int> = emptyList(),
)

/** One row of `CacheDeletionOut.skipped_shared` — `SkippedSharedDepotOut`. */
@Serializable
data class SkippedSharedDepotOut(
    val depotid: Int,
    val shared_with: List<Int> = emptyList(),
)

/** One row of `CacheDeletionOut.failed` — `FailedDepotOut`. */
@Serializable
data class FailedDepotOut(
    val depotid: Int,
    val error: String,
)

/** `DELETE /v1/cache/{appid}` — `vault_api/routers/cache.py::CacheDeletionOut`. */
@Serializable
data class CacheDeletionOut(
    val appid: Int,
    val deleted_depots: List<DeletedDepotOut> = emptyList(),
    val skipped_shared: List<SkippedSharedDepotOut> = emptyList(),
    val failed: List<FailedDepotOut> = emptyList(),
    val total_bytes_freed: Long,
)

/**
 * Body of `POST /v1/cache/{appid}/gc` — `vault_api/routers/cache.py::GcRequest`.
 * `execute` defaults to `false` (dry run) on both sides, matching the
 * server's "dry run in three independent places" rule (api/README.md
 * "Garbage collection").
 */
@Serializable
data class GcRequest(
    val execute: Boolean = false,
)

/** Response of `POST /v1/cache/{appid}/gc` — `vault_api/routers/cache.py::GcJobRef`. */
@Serializable
data class GcJobRef(
    val appid: Int,
    val job_id: Int,
    val status: String,
    val type: String,
    val mode: String,
    val execute: Boolean,
    val deduplicated: Boolean,
)

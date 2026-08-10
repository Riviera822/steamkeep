package dev.steamvault.app.net.model

import kotlinx.serialization.Serializable

/**
 * `GET /v1/health` — the one route vault-api serves without `X-Api-Key`
 * (api/README.md "Auth"). Body is fixed and never carries anything else.
 */
@Serializable
data class HealthOut(
    val status: String,
)

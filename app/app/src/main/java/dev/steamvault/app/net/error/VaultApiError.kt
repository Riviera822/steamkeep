package dev.steamvault.app.net.error

/**
 * The six-kind error taxonomy (WP 4b.2 brief), pinned to the SAME kind
 * names `web/js/errors.js`'s `ERROR_KINDS` uses so both frontends describe
 * a failed vault-api call the same way. See
 * `VaultApiErrorTaxonomyContractTest` for the literal pin — the kind
 * constants below are compared against hand-transcribed literals there,
 * never derived from this file itself (docs/LEARNINGS.md "Android (Phase
 * 4b)": "a derived round-trip is circular and cannot detect drift from the
 * other frontend").
 *
 * `network` has no HTTP status at all (the request never reached a
 * server) — produced directly by [dev.steamvault.app.net.VaultApiClient]'s
 * `IOException` catch, never by [classifyHttpStatus]. `unknown` is the
 * explicit fallback for a status below 400 somehow reaching the
 * classifier (should not happen: callers only classify a non-2xx
 * response, mirroring web/js/errors.js's own note on this). `409`
 * (job-control conflicts, api/README.md "Job control") and `422` both
 * fall into `validation`, exactly like web/js/errors.js: "the request as
 * sent cannot be applied against current state" is not given its own kind
 * on either frontend.
 */
sealed class VaultApiError(
    message: String,
    val kind: String,
    val status: Int? = null,
    val detail: String? = null,
    cause: Throwable? = null,
) : Exception(message, cause) {

    class Network(message: String, cause: Throwable? = null) :
        VaultApiError(message, KIND_NETWORK, cause = cause)

    class Auth(message: String, status: Int, detail: String? = null) :
        VaultApiError(message, KIND_AUTH, status, detail)

    class NotFound(message: String, status: Int, detail: String? = null) :
        VaultApiError(message, KIND_NOT_FOUND, status, detail)

    class Validation(message: String, status: Int, detail: String? = null) :
        VaultApiError(message, KIND_VALIDATION, status, detail)

    class Server(message: String, status: Int, detail: String? = null) :
        VaultApiError(message, KIND_SERVER, status, detail)

    class Unknown(
        message: String,
        status: Int? = null,
        detail: String? = null,
        cause: Throwable? = null,
    ) : VaultApiError(message, KIND_UNKNOWN, status, detail, cause)

    companion object {
        const val KIND_NETWORK = "network"
        const val KIND_AUTH = "auth"
        const val KIND_NOT_FOUND = "not_found"
        const val KIND_VALIDATION = "validation"
        const val KIND_SERVER = "server"
        const val KIND_UNKNOWN = "unknown"

        /**
         * Pure: mirrors `web/js/errors.js::classifyHttpStatus` exactly,
         * including its documented boundary choices (401 -> auth, 404 ->
         * not_found, >=500 -> server, >=400 -> validation, else -> unknown).
         */
        fun classifyHttpStatus(status: Int): String = when {
            status == 401 -> KIND_AUTH
            status == 404 -> KIND_NOT_FOUND
            status >= 500 -> KIND_SERVER
            status >= 400 -> KIND_VALIDATION
            else -> KIND_UNKNOWN
        }

        /** Build the taxonomy subclass matching [status] for a non-2xx HTTP response. */
        fun forHttpStatus(
            status: Int,
            method: String,
            path: String,
            detail: String?,
        ): VaultApiError {
            val message = "$method $path failed ($status)"
            return when (classifyHttpStatus(status)) {
                KIND_AUTH -> Auth(message, status, detail)
                KIND_NOT_FOUND -> NotFound(message, status, detail)
                KIND_SERVER -> Server(message, status, detail)
                KIND_VALIDATION -> Validation(message, status, detail)
                else -> Unknown(message, status, detail)
            }
        }
    }
}

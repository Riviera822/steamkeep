/**
 * API error taxonomy (WP 4a.2).
 *
 * Split out of api.js so both the real fetch path (api.js) and the demo
 * fixture path (demo-data.js) can throw the exact same typed error without
 * an import cycle (api.js imports demoRequest from demo-data.js; demo-data.js
 * needs to raise the same ApiError shape a real failed fetch would).
 */

export const ERROR_KINDS = Object.freeze({
  NETWORK: "network",
  AUTH: "auth",
  NOT_FOUND: "not_found",
  VALIDATION: "validation",
  SERVER: "server",
  UNKNOWN: "unknown",
});

export class ApiError extends Error {
  /**
   * @param {string} kind One of ERROR_KINDS.
   * @param {string} message
   * @param {{status?: number|null, detail?: unknown, cause?: unknown}} [opts]
   */
  constructor(kind, message, { status = null, detail = null, cause } = {}) {
    super(message, cause !== undefined ? { cause } : undefined);
    this.name = "ApiError";
    this.kind = kind;
    this.status = status;
    this.detail = detail;
  }
}

/**
 * Pure: map an HTTP status code to one of ERROR_KINDS.
 *
 * The WP brief names five kinds by example (network / auth / not-found /
 * server / validation); `ERROR_KINDS` adds a sixth, `unknown`, as the
 * explicit fallback rather than silently mis-filing an unexpected value
 * into one of the other five. `network` has no status code at all (it
 * never reaches a server), so it is produced directly by api.js's
 * request()'s fetch-level catch, never from here. `409` (job-control
 * conflicts, api/README.md "Job control" table) has no dedicated kind in
 * the brief — it is folded into `validation` ("the request as sent cannot
 * be applied against current state"), same as 422; undocumented 4xx codes
 * fall into the same bucket rather than `unknown`, since they are still
 * "your request, not our outage" cases. `unknown` is reserved for a status
 * below 400 somehow reaching here (should not happen: callers only
 * classify a non-ok response) — see web/tests/errors.test.js for all six.
 */
export function classifyHttpStatus(status) {
  if (status === 401) return ERROR_KINDS.AUTH;
  if (status === 404) return ERROR_KINDS.NOT_FOUND;
  if (status >= 500) return ERROR_KINDS.SERVER;
  if (status >= 400) return ERROR_KINDS.VALIDATION;
  return ERROR_KINDS.UNKNOWN;
}

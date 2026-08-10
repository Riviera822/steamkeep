/**
 * History-row log-excerpt display selection (WP 4a.5).
 *
 * `GET /v1/jobs` (the fast-polled list) omits `log_excerpt` on purpose
 * (api/README.md: "Omits log_excerpt on purpose — this is the polling
 * list"); only `GET /v1/jobs/{id}` carries it. A history row's excerpt is
 * therefore always a LAZY fetch triggered by expanding that one row, never
 * something the jobs poll already has — this module is the pure decision
 * of what a row should render given that fetch's current state (idle/
 * loading/error/loaded), kept DOM- and fetch-free so every branch is
 * testable without a fake network.
 *
 * `log_excerpt` itself is "the ANSI-stripped tail of SteamPrefill's
 * combined stdout/stderr, capped at 4 KiB and prefixed with
 * `[...truncated...]` when it was cut, plus vault-api's own
 * `[vault-api] …` diagnostic lines" (api/README.md, "Queue semantics") —
 * a single opaque string, not markup. This module splits it into lines and
 * strips/flags the truncation marker; it does NOT attempt to re-derive
 * semantic meaning (e.g. "this line looks like an error") from the text —
 * job outcome honesty is already carried by the job's own `status`
 * (job-partition.js's `jobIconKind`/`jobStatusWord`), not by pattern-
 * matching a log line SteamPrefill or vault-api could rephrase at any time.
 */

export const EXCERPT_STATE = Object.freeze({
  COLLAPSED: "collapsed",
  LOADING: "loading",
  ERROR: "error",
  EMPTY: "empty",
  READY: "ready",
});

const TRUNCATION_MARKER = "[...truncated...]";

/**
 * @param {{expanded: boolean, loading?: boolean, error?: string|null, excerpt?: string|null|undefined}} input
 *   `excerpt` is `undefined` before the lazy fetch has ever completed
 *   (distinct from `null`/`""`, a job that genuinely has no log output —
 *   e.g. a queued job cancelled before it ever ran).
 * @returns {{state: string, lines: string[], truncated: boolean, message?: string}}
 */
export function selectExcerptDisplay({ expanded, loading = false, error = null, excerpt } = {}) {
  if (!expanded) {
    return { state: EXCERPT_STATE.COLLAPSED, lines: [], truncated: false };
  }
  if (loading) {
    return { state: EXCERPT_STATE.LOADING, lines: [], truncated: false };
  }
  if (error) {
    return { state: EXCERPT_STATE.ERROR, lines: [], truncated: false, message: error };
  }
  const text = typeof excerpt === "string" ? excerpt : "";
  if (!text.trim()) {
    return { state: EXCERPT_STATE.EMPTY, lines: [], truncated: false };
  }
  const truncated = text.startsWith(TRUNCATION_MARKER);
  const body = truncated ? text.slice(TRUNCATION_MARKER.length).replace(/^\n+/, "") : text;
  return { state: EXCERPT_STATE.READY, lines: body.split("\n"), truncated };
}

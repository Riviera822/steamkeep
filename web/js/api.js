/**
 * vault-api HTTP client (WP 4a.2).
 *
 * Every route in api/README.md's "Endpoints" table requires `X-Api-Key`
 * except `GET /v1/health` (see api/README.md "Auth") — this module injects
 * that header from local storage on every request, classifies failures
 * into a small typed taxonomy so callers can branch on *kind* instead of
 * re-parsing status codes everywhere, and transparently serves in-memory
 * demo fixtures (demo-data.js) when demo mode is on, so the app works with
 * no vault present (NOTES open question 5).
 *
 * CSP note (WP 4a.1's headers, api/vault_api/webui.py): no inline scripts
 * here (this is a module file, loaded via <script type="module" src=...>),
 * and demo mode makes zero network requests — it is pure local data, not a
 * mock server.
 */

import { demoRequest } from "./demo-data.js";
import { ApiError, ERROR_KINDS, classifyHttpStatus } from "./errors.js";

export { ApiError, ERROR_KINDS, classifyHttpStatus };

const STORAGE_KEYS = Object.freeze({
  serverUrl: "steamvault.serverUrl",
  apiKey: "steamvault.apiKey",
  demoMode: "steamvault.demoMode",
});

function readLocalStorage(key, fallback) {
  try {
    const value = window.localStorage.getItem(key);
    return value === null ? fallback : value;
  } catch {
    // Private-browsing storage lockouts, disabled storage, etc. — the app
    // must still run (in particular, demo mode must still be reachable).
    return fallback;
  }
}

function writeLocalStorage(key, value) {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // Same reasoning as readLocalStorage — never let a storage failure
    // throw out of a settings toggle.
  }
}

// Note: a non-same-origin serverUrl (pointing this page at a DIFFERENT
// vault-api than the one that served it) is currently blocked by the CSP
// WP 4a.1 ships (api/vault_api/webui.py, self-only `connect-src`) — by
// design, not an oversight here. Whether/how to relax that is a 4a.6
// decision (connection profiles, settings UI); left alone in this WP.
export function getServerUrl() {
  return readLocalStorage(STORAGE_KEYS.serverUrl, "");
}

export function setServerUrl(url) {
  writeLocalStorage(STORAGE_KEYS.serverUrl, url);
}

export function getStoredApiKey() {
  return readLocalStorage(STORAGE_KEYS.apiKey, "");
}

export function setStoredApiKey(key) {
  writeLocalStorage(STORAGE_KEYS.apiKey, key);
}

export function isDemoMode() {
  return readLocalStorage(STORAGE_KEYS.demoMode, "0") === "1";
}

export function setDemoMode(on) {
  writeLocalStorage(STORAGE_KEYS.demoMode, on ? "1" : "0");
}

function buildUrl(path, params) {
  const base = getServerUrl().replace(/\/+$/, "");
  const url = new URL(path, base ? base : window.location.origin);
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== null) url.searchParams.set(key, String(value));
    }
  }
  return url;
}

/**
 * Low-level request. Returns the parsed JSON body (or `null` for a `204`),
 * or throws {@link ApiError}. Demo mode short-circuits before any network
 * call is made.
 *
 * @param {string} method
 * @param {string} path Must start with `/v1/...`.
 * @param {{body?: unknown, params?: Record<string, unknown>, signal?: AbortSignal}} [opts]
 */
export async function request(method, path, { body, params, signal } = {}) {
  if (isDemoMode()) {
    return demoRequest(method, path, { body, params });
  }

  let response;
  try {
    response = await fetch(buildUrl(path, params), {
      method,
      headers: {
        "X-Api-Key": getStoredApiKey(),
        ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal,
    });
  } catch (err) {
    if (err && err.name === "AbortError") throw err; // caller-initiated cancellation, not a taxonomy error
    throw new ApiError(ERROR_KINDS.NETWORK, `Network request failed: ${method} ${path}`, {
      cause: err,
    });
  }

  if (response.status === 204) return null;

  const text = await response.text();
  let data = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = null; // non-JSON body; fall through, `detail` below uses raw text
    }
  }

  if (!response.ok) {
    const kind = classifyHttpStatus(response.status);
    const detail = data && typeof data === "object" && "detail" in data ? data.detail : text || null;
    throw new ApiError(kind, `${method} ${path} failed (${response.status})`, {
      status: response.status,
      detail,
    });
  }

  return data;
}

/**
 * Typed convenience wrappers over the endpoints the polling store and the
 * views built on top of it (4a.3/4a.5) need. Field names are passed
 * through verbatim from api/README.md's "Endpoints" table / the router
 * response models — no renaming layer, so a payload can be handed straight
 * to diff-utils.js / notifications.js keyed the same way the server keys it.
 *
 * Deliberately NOT wrapped here (out of this WP's scope — no current
 * consumer needs them yet): /v1/mapping*, /v1/oracle*, /v1/schedule,
 * /v1/stats, /v1/cache/{appid}/gc. Add them with the WP that first needs
 * them rather than guessing their call shape now.
 */
export const api = {
  health: () => request("GET", "/v1/health"),
  games: () => request("GET", "/v1/games"),
  game: (appid) => request("GET", `/v1/games/${appid}`),
  jobs: (limit = 20) => request("GET", "/v1/jobs", { params: { limit } }),
  job: (id) => request("GET", `/v1/jobs/${id}`),
  prefill: (appids) => request("POST", "/v1/prefill", { body: { appids } }),
  cancelJob: (id) => request("DELETE", `/v1/jobs/${id}`),
  pauseJob: (id) => request("POST", `/v1/jobs/${id}/pause`),
  resumeJob: (id) => request("POST", `/v1/jobs/${id}/resume`),
  deleteCache: (appid) => request("DELETE", `/v1/cache/${appid}`),
  cacheSummary: () => request("GET", "/v1/cache/summary"),
  clients: () => request("GET", "/v1/clients"),
};

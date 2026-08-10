/**
 * Generic array-of-objects differ (WP 4a.2).
 *
 * The mockup's round-7 fix (docs/design/vault-app-mockup-NOTES.md) is a
 * general rule, not a mockup-only concern: "rebuild DOM only when a card's
 * STATE changes... while a job is merely progressing, patch the volatile
 * values in place." A consumer that gets a brand-new array on every poll
 * has no way to tell "this row is identical" from "this row changed" from
 * "this row is new" without re-diffing itself — and a naive full re-render
 * recreates animated nodes (status-icon glyphs mid-loop) every tick. This
 * module does the diffing once, centrally, so every subscriber (the
 * notification differ in notifications.js, and later the library/downloads
 * views) gets the same granular buckets instead of reinventing the compare.
 *
 * Pure and framework-free: no DOM, no fetch, no timers. Safe to unit-test
 * headless (see web/tests/diff-utils.test.js).
 */

/**
 * @template T
 * @param {T[] | null | undefined} prevList Previous snapshot, or
 *   `null`/`undefined` if this is the FIRST poll (no snapshot exists yet —
 *   distinct from "the server returned zero items", which is `[]`).
 * @param {T[] | null | undefined} currList Current snapshot.
 * @param {(item: T) => string | number} keyFn Stable identity key for one item
 *   (e.g. `(j) => j.id`, `(g) => g.appid`, `(c) => c.client_id`).
 * @returns {{
 *   added: T[],
 *   updated: {prev: T, curr: T}[],
 *   removed: T[],
 *   unchanged: T[],
 *   isFirst: boolean,
 * }}
 */
export function diffByKey(prevList, currList, keyFn) {
  const currArray = Array.isArray(currList) ? currList : [];
  const currMap = new Map(currArray.map((item) => [keyFn(item), item]));

  if (prevList == null) {
    // No baseline to compare against — every item is "new" in the sense of
    // "we've never seen this list before", but callers deriving NOTIFICATIONS
    // from this (notifications.js) must treat isFirst as "nothing happened
    // yet", or the very first poll would fire a notification for every
    // already-finished job / already-flagged client (the "notification
    // storm" this module exists partly to prevent).
    return {
      added: [...currMap.values()],
      updated: [],
      removed: [],
      unchanged: [],
      isFirst: true,
    };
  }

  const prevMap = new Map(prevList.map((item) => [keyFn(item), item]));
  const added = [];
  const updated = [];
  const unchanged = [];

  for (const [key, item] of currMap) {
    if (!prevMap.has(key)) {
      added.push(item);
      continue;
    }
    const prevItem = prevMap.get(key);
    if (shallowJsonEqual(prevItem, item)) {
      unchanged.push(item);
    } else {
      updated.push({ prev: prevItem, curr: item });
    }
  }

  const removed = [];
  for (const [key, item] of prevMap) {
    if (!currMap.has(key)) removed.push(item);
  }

  return { added, updated, removed, unchanged, isFirst: false };
}

/**
 * Equality for the plain, flat-ish JSON objects vault-api returns (every
 * response in api/README.md is Pydantic-serialized with a stable field
 * order for a given model, so two structurally-equal payloads always
 * stringify identically). Deliberately not a general deep-equal — this is
 * an internal helper for server response objects, not arbitrary data.
 */
function shallowJsonEqual(a, b) {
  if (a === b) return true;
  return JSON.stringify(a) === JSON.stringify(b);
}

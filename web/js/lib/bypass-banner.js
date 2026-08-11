/**
 * Bypass banner visibility + dismiss-state lifecycle (WP 4a.7).
 *
 * Pure. Two DELIBERATELY separate concerns living in one small module
 * because they are easy to conflate and must not be:
 *
 * **`bypassBannerVisible` is a live PREDICATE over the latest
 * `GET /v1/clients` snapshot** — "is anyone bypass_suspected right now".
 * It does NOT consult the notifications differ (web/js/notifications.js)
 * at all, on purpose: `diffClientsForNotifications` deliberately fires no
 * event on the very first poll ("first-poll-silent", pinned in
 * web/tests/notifications.test.js and docs/LEARNINGS.md's "Transition
 * detectors" entry) to avoid flooding a CHANGE LOG with state that was
 * already true before the app opened. That suppression is correct for a
 * log of "what changed" (web/js/lib/notification-log.js deliberately
 * mirrors it — see that module's header). It would be a real bug for a
 * persistent BANNER: a client that was already bypass_suspected on the
 * very first snapshot the app ever sees must show the banner immediately,
 * not wait for a transition that — because there was no prior snapshot to
 * transition FROM — will never come. Hence: banner visibility reads the
 * snapshot directly, never a diff, so it is correct from the first poll.
 *
 * **`nextBypassDismissState` DOES reuse the differ's own transition
 * events** (the exact objects notifications.js emits on the store's
 * `"notifications"` channel) for the "Dismiss" button's un-dismiss rule:
 * dismissing hides the banner only until the underlying condition actually
 * CHANGES again (any bypass_suspected/bypass_resolved transition, for any
 * client) — not forever, and not on the next unrelated poll tick either.
 * Reusing the differ's own events (rather than re-comparing snapshots in
 * this module too) means this function never has to reason about
 * isFirst/added/removed itself: the differ already emits zero bypass_*
 * events on the first poll by design, so passing that empty batch through
 * here naturally leaves a dismiss flag untouched on that poll too — no
 * special-casing needed. That is the concrete sense in which this half of
 * the module "reuses the differ's semantics" instead of re-deriving them.
 *
 * Pure only — no DOM, no fetch. Covered in web/tests/bypass-banner.test.js.
 */

/**
 * @param {object[] | null | undefined} clients Latest `GET /v1/clients`
 *   snapshot (api/vault_api/routers/clients.py's `ClientOut` list).
 * @returns {boolean}
 */
export function bypassBannerVisible(clients) {
  return Array.isArray(clients) && clients.some((c) => !!(c && c.bypass_suspected));
}

/**
 * @param {boolean} dismissed current dismiss state
 * @param {object[] | null | undefined} events this tick's notification
 *   events (notifications.js's `diffClientsForNotifications`, or any
 *   superset such as `diffSnapshotsForNotifications` — only
 *   bypass_suspected/bypass_resolved entries matter here, everything else
 *   is ignored).
 * @returns {boolean} the next dismiss state — `false` (un-dismissed) the
 *   moment a real bypass transition is seen, otherwise unchanged.
 */
export function nextBypassDismissState(dismissed, events) {
  if (!dismissed) return false;
  if (!Array.isArray(events) || !events.length) return dismissed;
  const changed = events.some(
    (e) => e && (e.type === "bypass_suspected" || e.type === "bypass_resolved"),
  );
  return changed ? false : dismissed;
}

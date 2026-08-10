/**
 * Small formatting helpers shared by the library and (later) downloads
 * views (WP 4a.3). Pure — unit-tested in web/tests/format.test.js.
 */

/**
 * Bytes -> a short "N GB" string, mirroring the mockup's `gb()` helper
 * (>=100 GB rounds to a whole number, otherwise one decimal place).
 * `null`/`undefined`/non-positive bytes are never faked into a number —
 * callers decide what to show instead (mockup rule: "a never-downloaded
 * game shows the icon alone rather than a fake dash").
 * @param {number | null | undefined} bytes
 * @returns {string | null} `null` when there is nothing honest to print.
 */
export function formatBytesGB(bytes) {
  if (typeof bytes !== "number" || !Number.isFinite(bytes) || bytes <= 0) return null;
  const gb = bytes / 1_073_741_824;
  return (gb >= 100 ? gb.toFixed(0) : gb.toFixed(1)) + " GB";
}

/**
 * An ISO-8601 timestamp (`jobs.created_at`/`started_at`/`finished_at`, ...)
 * -> a locale-formatted string for display (WP 4a.5, Downloads history
 * rows). `null`/`undefined`/unparseable input never fabricates a time —
 * same "nothing honest to print" posture as {@link formatBytesGB}.
 * @param {string | null | undefined} iso
 * @returns {string} a display string, or "—" when there is nothing to show.
 */
export function formatTimestamp(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString();
}

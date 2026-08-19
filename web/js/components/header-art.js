/**
 * Header/hero art for the game detail card (WP 4h.3, "nearly free" per the
 * plan: `lib/cover-art.js`'s shipped `headerArtUrl(appid)` builder against
 * the CSP-allowed CDN host, no relay endpoint, no new external call — see
 * that module's header for the CSP note).
 *
 * **Graceful absence is the whole point.** Steam's CDN 404s for delisted or
 * very old titles. Unlike the library grid's `.cap`/`.art`
 * (components/game-card.js's `buildCover`), this element has NO procedural
 * fallback tile underneath it — a hero banner with nothing to show should
 * look like it was never there, not like a broken tile. So the `error`
 * handler below removes the WHOLE wrapper, not just the `<img>` (contrast
 * `buildCover`'s `img.remove()`, which is enough there only because a
 * styled fallback tile is still underneath it) — every trace of the
 * reserved space goes with it, and the rest of the card's content simply
 * has one less sibling above it.
 *
 * **No-layout-shift resolution, pinned:** the wrapper reserves its box via
 * `aspect-ratio` (css/app.css's `.header-art`) for as long as it exists —
 * i.e. from the instant it is inserted (before the image has even started
 * loading) through a successful load (the image paints INTO the
 * already-sized box, so nothing moves). On a load failure the wrapper is
 * removed outright, which collapses that reserved space in one step; there
 * is no state where an empty band sits reserved with nothing loading into
 * it. Concretely: a SLOW-loading image never shifts anything (the box was
 * already there); a 404 shifts the content below up once PER FULL RENDER
 * (a structural tick — e.g. a job status change — rebuilds the card and
 * re-runs the 404 request and collapse; browsers do not negatively cache
 * the 404), whenever the
 * error arrives (typically fast, before the sheet has been open long) —
 * this is the one visible move the design accepts, in exchange for never
 * shipping a permanent empty band for a title with no header art at all.
 *
 * DOM-building, so not unit-tested for its general shape (same posture as
 * `sheet-dialog.js`/`game-detail-sheet.js` — see that module's header),
 * EXCEPT for the two behaviors this WP's brief demands a mutation-tested
 * pin for: the URL is built through the shared `headerArtUrl` mechanism
 * (never a hardcoded string), and the error path removes the entire
 * reserved wrapper (web/tests/header-art.test.js, using the fake DOM
 * harness — mirrors the one named exception `dialog-wiring.test.js`
 * carves out of `sheet-dialog.js`'s own "not tested" posture).
 */

import { headerArtUrl } from "../lib/cover-art.js";

/**
 * @param {number} appid
 * @returns {HTMLElement} a wrapper `<div class="header-art">` containing the
 *   `<img>`, ready to append above the rest of the detail card's content.
 */
export function buildHeaderArt(appid) {
  const wrap = document.createElement("div");
  wrap.className = "header-art";

  const img = document.createElement("img");
  img.alt = "";
  img.decoding = "async";
  // No `loading="lazy"`, unlike the grid's covers: this is the first thing
  // in the card and already in the viewport the instant the sheet opens,
  // never off-screen the way most grid cards are when the page loads.
  img.addEventListener("error", () => wrap.remove(), { once: true });
  img.src = headerArtUrl(appid);
  wrap.appendChild(img);

  return wrap;
}

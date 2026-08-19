/**
 * Header/hero art for the game detail card (WP 4h.3, "nearly free" per the
 * plan: `lib/cover-art.js`'s shipped `headerArtUrl(appid)` builder against
 * the CSP-allowed CDN host, no relay endpoint, no new external call — see
 * that module's header for the CSP note).
 *
 * **Design revised by WP 4h.5 — "load, then reveal," not "reserve, then
 * shrink."** WP 4h.3 reserved the box via `aspect-ratio` the instant the
 * wrapper was inserted (before the image had even started loading), then on
 * a 404 removed the whole wrapper outright — which the operator watched
 * collapse visibly on a phone. Their own framing: "default off, and load it
 * in if there is one." This module now follows that literally:
 *
 * 1. **Nothing about the header occupies layout until its image has
 *    actually loaded (decoded).** The wrapper starts with zero reserved
 *    size (css/app.css's `.header-art` — a `grid-template-rows:0fr` box,
 *    genuinely zero height, no `aspect-ratio`/`height`/`min-height` on the
 *    wrapper itself). A 404/delisted title never reaches step 2 below at
 *    all — the `error` handler removes the whole wrapper exactly as WP 4h.3
 *    did, but because nothing was ever reserved, that removal is invisible:
 *    there is never a band that appears and then collapses.
 * 2. **On successful load, the header grows in.** Adding the `.loaded`
 *    class flips the wrapper's grid row to `1fr` and its opacity to `1`
 *    (css/app.css), animating open to the `<img>`'s own true 460/215 box
 *    over ~200ms. This is the ONLY movement anywhere in this flow, and it
 *    is only ever visible when the image genuinely arrives after the
 *    wrapper has already been painted at zero height (a cold cache or a
 *    slow CDN round trip) — when decode/load resolves before the browser's
 *    next paint (the common case, e.g. a warm browser cache), the
 *    collapsed frame is never painted at all, so the header simply appears
 *    already-expanded with no visible motion.
 * 3. **Reduced motion gets an instant appear, not a suppressed animation.**
 *    css/app.css's transition on `.header-art` carries no `!important`, so
 *    the standing whole-app `prefers-reduced-motion` wildcard (theme.css)
 *    already forces it to `.001ms` — no separate JS-side `matchMedia` check
 *    is needed here, because the motion is driven entirely by a CSS
 *    transition, not a JS-invoked animation API.
 *
 * **Decode-vs-load: `img.decode()` is used where available, `load` is the
 * fallback.** `decode()` resolves only once the image is actually ready to
 * paint (fully loaded AND decoded), which is exactly the "known-good" gate
 * this design wants — revealing on a bare `load` event risks painting a
 * half-decoded first frame on a slow/large image. `decode()` also rejects
 * on a genuine load failure, but that rejection is deliberately swallowed
 * here: the `error` event listener below already owns the failure path
 * (removes the wrapper), so handling it twice would be redundant, not
 * additive. Engines without `img.decode` (and this codebase's fake-DOM test
 * harness) fall back to the `load` event, which still reveals correctly,
 * just without the paint-readiness guarantee.
 *
 * DOM-building, so not unit-tested for its general shape (same posture as
 * `sheet-dialog.js`/`game-detail-sheet.js` — see that module's header),
 * EXCEPT for the behaviors this WP's brief (and WP 4h.3's before it) demand
 * a mutation-tested pin for: the URL is built through the shared
 * `headerArtUrl` mechanism (never a hardcoded string), the error path
 * removes the entire wrapper (no reserved band survives a 404), and a
 * successful load reveals the wrapper by adding `.loaded` — never before
 * (web/tests/header-art.test.js, using the fake DOM harness — mirrors the
 * one named exception `dialog-wiring.test.js` carves out of `sheet-
 * dialog.js`'s own "not tested" posture).
 */

import { headerArtUrl } from "../lib/cover-art.js";

/**
 * @param {number} appid
 * @returns {HTMLElement} a wrapper `<div class="header-art">` containing the
 *   `<img>`, ready to append above the rest of the detail card's content.
 *   Starts with zero reserved size; gains `.loaded` (and therefore its true
 *   460:215 box) only once the image is known-good.
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

  const reveal = () => wrap.classList.add("loaded");

  // `src` is set BEFORE calling decode(): decode() operates on the
  // element's "current request," which only exists once a src has
  // actually been assigned — calling it first would reject immediately
  // with nothing to decode.
  img.src = headerArtUrl(appid);

  if (typeof img.decode === "function") {
    // See this module's header for why decode() is preferred: it settles
    // only once the image is actually ready to paint. Its rejection is
    // swallowed on purpose — a genuine load failure is already handled by
    // the "error" listener above; decode() rejecting for that same failure
    // is not a second, independent thing to react to.
    img.decode().then(reveal, () => {});
  } else {
    img.addEventListener("load", reveal, { once: true });
  }

  wrap.appendChild(img);

  return wrap;
}

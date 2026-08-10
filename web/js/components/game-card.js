/**
 * Library card component (WP 4a.3).
 *
 * Builds one card for any of the three layouts (grid2/grid3/list — CSS
 * alone decides what's visible, see css/app.css "library layouts"; this
 * module emits ONE DOM shape for all three, exactly like the mockup's "one
 * render path... only the container class changes"). DOM-only: no fetch, no
 * store access, no polling — library.js owns state and passes plain data +
 * callbacks in. Not unit-tested the same way as the lib/ pure modules
 * (mirrors the existing split in this codebase: status-icon.js's DOM
 * builder isn't unit-tested either, only the glyph/label tables feeding
 * it) — this WP's headless coverage targets the decision logic
 * (game-status.js, library-filters.js, bulk-plan.js, multiplan.js,
 * cover-art.js, format.js).
 *
 * Round-7 rule (docs/design/vault-app-mockup-NOTES.md): `data-dk` on the
 * card is the STRUCTURAL signature. `views/library.js` reads it before
 * touching a card on a jobs poll tick and only rebuilds (calls buildCard
 * again) when it no longer matches the freshly computed `dispKind` — never
 * on an unrelated field change (e.g. a running job's `log_excerpt`
 * growing). This component has nothing to patch in place beyond that
 * signature check today (see game-status.js's module header, Divergence 2:
 * the real API has no live progress number the mockup's `patchGridProgress`
 * used to patch) — `cardStructuralKey` is exported so library.js's tick
 * handler and this file can never disagree about what "structural" means.
 */

import { createStatusIcon, STATUS_LABEL } from "./status-icon.js";
import { dispKind, statusAction } from "../lib/game-status.js";
import { formatBytesGB } from "../lib/format.js";
import { coverArtUrl, fallbackHues, fallbackPattern } from "../lib/cover-art.js";

/** The exact string library.js's `GET /v1/games` tick handler
 * (js/lib/render-plan.js's `planGamesUpdate`) compares against a live
 * card's `data-dk` to decide whether that appid can be PATCHED in place or
 * needs a full rebuild (round-7 rule — see render-plan.js's header). */
export function cardStructuralKey(game, liveJob) {
  return dispKind(game, liveJob);
}

function pillNumberText(game, kind) {
  // Honest per game-status.js's Divergence 2: no fabricated live percentage
  // for running/paused — only a genuinely cached game prints a number.
  if (kind !== "cached") return null;
  return formatBytesGB(game.size_bytes);
}

/**
 * Patch the VOLATILE text on an already-built card in place — no node is
 * created, removed or replaced, so the status-icon `<svg>` subtree (and
 * any CSS animation running on it) is left completely untouched. This is
 * the round-7 counterpart to `buildCard`: called only when
 * `render-plan.js`'s `planGamesUpdate` has already established the game's
 * STRUCTURAL key (`cardStructuralKey`) has NOT changed — the caller
 * (views/library.js) must never call this for a structural transition,
 * only for a same-kind update (e.g. `size_bytes` drifting while a download
 * runs, per the games-poll cadence).
 *
 * @param {HTMLElement} cardEl the existing `.card` element (mutated).
 * @param {object} game the game's freshest data.
 * @param {string} kind this card's (unchanged) structural key — passed in
 *   rather than recomputed so the caller's own structural-key check is the
 *   single source of truth for "did anything shape-relevant move".
 */
export function patchCardVolatile(cardEl, game, kind) {
  const pill = cardEl.querySelector(".cappill");
  const newPillNum = pillNumberText(game, kind);
  if (pill) {
    let pv = pill.querySelector(".pv");
    if (newPillNum) {
      if (!pv) {
        pv = document.createElement("span");
        pv.className = "pv";
        pill.appendChild(pv);
      }
      pv.textContent = newPillNum;
    } else if (pv) {
      pv.remove();
    }
  }
  const sizeEl = cardEl.querySelector(".meta .size");
  if (sizeEl) sizeEl.textContent = formatBytesGB(game.size_bytes) || "—";
}

function buildCover(game) {
  const { h1, h2 } = fallbackHues(game.appid);
  const pattern = fallbackPattern(game.appid);
  const cap = document.createElement("div");
  cap.className = `cap p${pattern}`;
  cap.style.setProperty("--h1", String(h1));
  cap.style.setProperty("--h2", String(h2));

  const art = document.createElement("div");
  art.className = "art";
  cap.appendChild(art);

  // Real Steam CDN artwork, layered over the procedural fallback above. On
  // any load failure (offline LAN, unknown appid, blocked host) it is
  // simply removed, leaving the styled fallback tile + game name visible —
  // never a broken-image icon or a blank rectangle.
  const img = document.createElement("img");
  img.className = "cover";
  img.alt = "";
  img.loading = "lazy";
  img.decoding = "async";
  img.addEventListener("error", () => img.remove(), { once: true });
  img.src = coverArtUrl(game.appid);
  cap.appendChild(img);

  const scrim = document.createElement("div");
  scrim.className = "scrim";
  cap.appendChild(scrim);

  return cap;
}

function buildIcon(kind, { action, gameName }) {
  const icon = createStatusIcon(kind);
  if (!action) return icon;
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "icnact";
  btn.title = action.title;
  btn.setAttribute("aria-label", `${action.title} — ${gameName}`);
  btn.appendChild(icon);
  return btn;
}

function buildPill(game, kind, action) {
  const wantsButton = !!action;
  const pill = document.createElement(wantsButton ? "button" : "span");
  pill.className = "cappill" + (wantsButton ? " act" : "");
  if (wantsButton) {
    pill.type = "button";
    pill.title = action.title;
    pill.setAttribute("aria-label", `${action.title} — ${game.name}`);
  }
  pill.appendChild(createStatusIcon(kind));
  const num = pillNumberText(game, kind);
  if (num) {
    const pv = document.createElement("span");
    pv.className = "pv";
    pv.textContent = num;
    pill.appendChild(pv);
  }
  return pill;
}

/**
 * @param {object} game GameSummary
 * @param {{
 *   liveJob?: object,
 *   picked: boolean,
 *   selecting: boolean,
 *   onOpen: (appid: number) => void,
 *   onLongPress: (appid: number) => void,
 *   onToggle: (appid: number) => void,
 *   onAction: (appid: number, actionType: string) => void,
 * }} ctx
 * @returns {HTMLElement}
 */
export function buildCard(game, ctx) {
  const { liveJob, picked, selecting, onOpen, onLongPress, onToggle, onAction } = ctx;
  const kind = dispKind(game, liveJob);
  const action = statusAction(game, liveJob, selecting);

  const card = document.createElement("div");
  card.className = "card" + (picked ? " picked" : "");
  card.dataset.appid = String(game.appid);
  card.dataset.dk = kind;
  card.setAttribute("role", "button");
  card.tabIndex = 0;

  card.appendChild(buildCover(game));
  const cap = card.firstChild;
  cap.appendChild(buildPill(game, kind, action)); // z-index:2 in CSS, so DOM order doesn't matter

  const name = document.createElement("div");
  name.className = "name";
  name.textContent = game.name;
  cap.appendChild(name);

  const pick = document.createElement("span");
  pick.className = "pick";
  pick.setAttribute("aria-hidden", "true");
  const pickSvg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  pickSvg.setAttribute("width", "12");
  pickSvg.setAttribute("height", "12");
  pickSvg.setAttribute("viewBox", "0 0 20 20");
  pickSvg.setAttribute("fill", "none");
  pickSvg.setAttribute("stroke", "currentColor");
  pickSvg.setAttribute("stroke-width", "3");
  const pickPath = document.createElementNS("http://www.w3.org/2000/svg", "path");
  pickPath.setAttribute("d", "m4 10.6 4 4L16.4 5.6");
  pickSvg.appendChild(pickPath);
  pick.appendChild(pickSvg);
  card.appendChild(pick);

  const rowname = document.createElement("span");
  rowname.className = "rowname";
  rowname.textContent = game.name;
  card.appendChild(rowname);

  const meta = document.createElement("div");
  meta.className = "meta";
  meta.appendChild(buildIcon(kind, { action, gameName: game.name }));
  const state = document.createElement("span");
  state.className = "state tx-" + kind;
  state.textContent = STATUS_LABEL[kind] || STATUS_LABEL.none;
  meta.appendChild(state);
  // Unlike the pill (which omits the number entirely rather than fabricate
  // one — see pillNumberText), the list/meta size column always shows
  // SOMETHING, using an explicit "—" for "nothing honest to print" (mockup
  // parity: `gb(null)` returns "—" here, whereas `pillHTML`'s own
  // `num?...:""` check is what actually hides it on the cover).
  const size = document.createElement("span");
  size.className = "size";
  size.textContent = formatBytesGB(game.size_bytes) || "—";
  meta.appendChild(size);
  card.appendChild(meta);

  // ---- interaction wiring (mockup parity: long-press / right-click /
  // header select icon -> multi-select; a tap toggles selection while
  // selecting, otherwise it's a plain open — WP 4a.4 supplies onOpen's
  // real behaviour, this WP wires the callback but library.js's onOpen is
  // a no-op for now, see views/library.js). ----
  let pressTimer = null;
  let longFired = false;
  const clearPress = () => {
    if (pressTimer !== null) clearTimeout(pressTimer);
    pressTimer = null;
  };
  const startPress = () => {
    longFired = false;
    clearPress();
    pressTimer = setTimeout(() => {
      longFired = true;
      onLongPress(game.appid);
    }, 420);
  };
  card.addEventListener("mousedown", startPress);
  card.addEventListener("touchstart", startPress, { passive: true });
  for (const ev of ["mouseup", "mouseleave", "touchend", "touchmove", "touchcancel"]) {
    card.addEventListener(ev, clearPress);
  }
  card.addEventListener("contextmenu", (e) => {
    e.preventDefault();
    onLongPress(game.appid);
  });
  card.addEventListener("click", () => {
    if (longFired) {
      longFired = false;
      return;
    }
    if (selecting) onToggle(game.appid);
    else onOpen(game.appid);
  });
  card.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      if (selecting) onToggle(game.appid);
      else onOpen(game.appid);
    }
  });

  if (action) {
    // The pill/meta-icon action buttons must never let their click reach
    // the card (which would open the detail view / toggle selection on
    // top of firing the action) — mockup parity
    // (`onclick="event.stopPropagation();..."`).
    for (const el of card.querySelectorAll("button.cappill, button.icnact")) {
      el.addEventListener("mousedown", (e) => e.stopPropagation());
      el.addEventListener("touchstart", (e) => e.stopPropagation(), { passive: true });
      el.addEventListener("click", (e) => {
        e.stopPropagation();
        onAction(game.appid, action.type);
      });
    }
  }

  return card;
}

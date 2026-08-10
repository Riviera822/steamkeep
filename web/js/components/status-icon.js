/**
 * Status-icon component (WP 4a.1).
 *
 * Ports the mockup's shape-first, colour-blind-safe status system
 * (docs/design/vault-app-mockup-NOTES.md, round 5 "Status icons replace
 * status dots"): a filled circle in the status hue carrying a
 * pixel-centred glyph. Colour is only the THIRD cue — shape (this glyph)
 * is first, the status word second (every caller should render the word
 * alongside the icon wherever there is room; this component always
 * includes it as a screen-reader-only label so nothing is icon-only for
 * assistive tech even in the tightest layouts).
 *
 * Motion = "activity right now", never decoration (round 5/6 addendum):
 * only running/updating/verify glyphs move, and only their inner group
 * (.dla / .rot) — never the whole badge. All motion is disabled globally
 * by the prefers-reduced-motion rule in css/theme.css.
 *
 * Built with `document.createElementNS` rather than `innerHTML`: no
 * functional difference under this app's CSP (static SVG markup executes
 * nothing either way), but it keeps every DOM node individually
 * inspectable/testable without a parser round-trip.
 */

const SVG_NS = "http://www.w3.org/2000/svg";

/** The word shown next to (or instead of, for screen readers) the glyph. */
export const STATUS_LABEL = {
  cached: "Current",
  running: "Downloading",
  updating: "Updating",
  stale: "Update ready",
  none: "Not cached",
  paused: "Paused",
  verify: "Verifying",
  error: "Failed",
  warn: "Warning",
};

/** Which glyph shape a given status kind uses. */
const KIND_GLYPH = {
  cached: "check",
  none: "download",
  stale: "refresh",
  running: "download",
  updating: "refresh",
  verify: "refresh",
  paused: "pause",
  error: "bang",
  warn: "bang",
};

function svgEl(tag, attrs) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const key in attrs) node.setAttribute(key, attrs[key]);
  return node;
}

function buildCheck() {
  return [svgEl("path", { d: "M5 12.5 10 17.5 19 7" })];
}

function buildDownload() {
  // .dla = the arrow (the only animated part), .dlbase = the baseline,
  // hidden while animating (mockup: "no line under the ANIMATED arrow").
  const arrow = svgEl("g", { class: "dla" });
  arrow.append(
    svgEl("path", { d: "M12 3.5V13" }),
    svgEl("path", { d: "M7.4 8.7 12 13.3 16.6 8.7" }),
  );
  const baseline = svgEl("path", { class: "dlbase", d: "M5 19.6h14" });
  return [arrow, baseline];
}

function buildRefresh() {
  // Two opposing curved arrows forming one circle — 180deg rotationally
  // symmetric, so a continuous linear turn loops seamlessly.
  const group = svgEl("g", { class: "rot" });
  group.append(
    svgEl("path", { d: "M4.5 12a7.5 7.5 0 0 1 12.8-5.3" }),
    svgEl("path", { d: "M17.3 2.7v4h-4" }),
    svgEl("path", { d: "M19.5 12a7.5 7.5 0 0 1-12.8 5.3" }),
    svgEl("path", { d: "M6.7 21.3v-4h4" }),
  );
  return [group];
}

function buildBang() {
  return [
    svgEl("path", { d: "M12 5.5V13.2" }),
    svgEl("path", { d: "M12 17.7v.02" }),
  ];
}

function buildPause() {
  return [
    svgEl("rect", { x: "7", y: "5.6", width: "3.4", height: "12.8", rx: "1.3" }),
    svgEl("rect", { x: "13.6", y: "5.6", width: "3.4", height: "12.8", rx: "1.3" }),
  ];
}

const GLYPH_BUILDERS = {
  check: buildCheck,
  download: buildDownload,
  refresh: buildRefresh,
  bang: buildBang,
  pause: buildPause,
};

/**
 * Build a status-icon element.
 *
 * @param {string} kind one of STATUS_LABEL's keys (an unknown kind falls
 *   back to "none", never to a blank/invalid icon).
 * @param {{size?: "sm"|"md"|"lg"}} [options]
 * @returns {HTMLSpanElement}
 */
export function createStatusIcon(kind, { size = "md" } = {}) {
  const knownKind = kind in STATUS_LABEL ? kind : "none";
  const shape = KIND_GLYPH[knownKind];
  const build = GLYPH_BUILDERS[shape] || buildDownload;

  const wrap = document.createElement("span");
  wrap.className = "sic k-" + knownKind + (size === "sm" ? " sic-sm" : size === "lg" ? " sic-lg" : "");

  const svg = svgEl("svg", { viewBox: "0 0 24 24", "aria-hidden": "true" });
  if (shape === "pause") {
    svg.setAttribute("fill", "currentColor");
  } else {
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", "2.7");
    svg.setAttribute("stroke-linecap", "round");
    svg.setAttribute("stroke-linejoin", "round");
  }
  svg.append(...build());
  wrap.appendChild(svg);

  const label = document.createElement("span");
  label.className = "sr-only";
  label.textContent = STATUS_LABEL[knownKind];
  wrap.appendChild(label);

  return wrap;
}

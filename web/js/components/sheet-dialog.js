/**
 * Minimal reusable bottom-sheet dialog scaffold (WP 4a.7).
 *
 * Both the notifications panel (`components/notifications.js`) and the
 * clients sheet (`components/clients-sheet.js`) need the exact same a11y
 * dialog semantics — the WP brief points at "the dialog semantics pattern
 * from 4a.6" (`onboarding.js`): `role="dialog"` + `aria-modal="true"`, focus
 * moved into the surface on open, `Escape` closes it, and focus returns to
 * whatever invoked it on close. Factored into one module so the two sheets
 * cannot drift apart on this — same reasoning as `job-partition.js`'s
 * `jobStatusWord` being a single source of truth for two call sites rather
 * than two hand-copied implementations.
 *
 * **A real focus TRAP (Tab/Shift+Tab wrapping inside the sheet) is
 * explicitly out of scope here too** — the same deferral
 * `onboarding.js`'s module header documents ("full trap deferred to
 * WP 4a.8"), which this WP's brief echoes verbatim ("full trap deferred to
 * 4a.8 consistently"). What ships: focus-on-open, Escape-to-close,
 * return-focus-on-close. `Escape` is bound to `document`, not the sheet
 * element itself, for the same reason `onboarding.js`'s does the same —
 * there is no trap guaranteeing focus stays inside the sheet, so the
 * listener must work regardless of where focus currently is.
 *
 * Not unit-tested directly (same posture as `onboarding.js`,
 * `components/toast.js`, `components/status-icon.js` — DOM-building code
 * with no meaningful headless behaviour to assert beyond "does it call the
 * DOM API", see web/tests/README.md); verified live against a running
 * vault-api instance instead (see the WP 4a.7 coder's report).
 */

/**
 * @param {{ariaLabel: string}} options
 * @returns {{
 *   backdrop: HTMLElement, sheet: HTMLElement, body: HTMLElement,
 *   open: () => void, close: () => void, isOpen: () => boolean,
 * }}
 */
export function createSheetDialog({ ariaLabel }) {
  const backdrop = document.createElement("div");
  backdrop.className = "sheet-backdrop";

  const sheet = document.createElement("div");
  sheet.className = "sheet";
  sheet.setAttribute("role", "dialog");
  sheet.setAttribute("aria-modal", "true");
  sheet.setAttribute("aria-label", ariaLabel);
  // Focus target on open: -1 keeps it out of the normal Tab order (it is
  // never meant to be tabbed TO, only focused programmatically), same
  // "tabindex=-1 landing spot" technique onboarding.js uses for its step
  // headings.
  sheet.tabIndex = -1;

  const grab = document.createElement("div");
  grab.className = "grab";
  const body = document.createElement("div");
  body.className = "body";
  sheet.append(grab, body);
  backdrop.appendChild(sheet);
  document.body.appendChild(backdrop);

  /** Element to return focus to on close — captured fresh on every open()
   * (mirrors onboarding.js's `invokerEl`), so whichever control opened this
   * particular sheet (the bell, a notification row, the bypass banner's
   * Details button, ...) gets focus back, not whichever one happened to
   * open it first. */
  let invokerEl = null;

  function isOpen() {
    return backdrop.classList.contains("on");
  }

  function open() {
    invokerEl = document.activeElement;
    backdrop.classList.add("on");
    sheet.focus();
  }

  function close() {
    if (!isOpen()) return;
    backdrop.classList.remove("on");
    if (invokerEl && typeof invokerEl.focus === "function") invokerEl.focus();
    invokerEl = null;
  }

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || !isOpen()) return;
    event.preventDefault();
    close();
  });

  // Tapping the backdrop itself (outside the sheet) dismisses it — the
  // real-DOM equivalent of the mockup's scrim-tap-to-close affordance.
  backdrop.addEventListener("click", (event) => {
    if (event.target === backdrop) close();
  });

  return { backdrop, sheet, body, open, close, isOpen };
}

/**
 * Clients sheet (WP 4a.7).
 *
 * Real `GET /v1/clients` data (api/vault_api/routers/clients.py's
 * `ClientOut`, WP 3.11's bypass_suspected) in the mockup's round-5
 * "Bypassing" / "Healthy" grouping. Per docs/WORKPACKAGES.md's Phase 4a
 * header (the recorded WP 4a.1 decision) **Clients is a sheet, not a nav
 * item** — it is only ever reached from the bypass banner's "Details"
 * button (`components/bypass-banner.js`) or from tapping a
 * bypass_suspected/bypass_resolved row in the notifications panel
 * (`components/notifications.js`); there is no standalone entry point.
 *
 * Data flows exclusively through the WP 4a.2 store (`store-singleton.js`)
 * — no parallel poll loop, same posture as `views/downloads.js`. Patch-in-
 * place on poll ticks per `lib/clients-render-plan.js` (round-7 pattern,
 * ported here for a different concrete reason than its animation-safety
 * origin — see that module's header: avoiding a scroll-position reset on
 * the sheet's own 20s poll cadence while it happens to be open).
 *
 * DOM-building component, not unit-tested directly (see
 * `components/sheet-dialog.js`'s header for the general reasoning this WP
 * follows for every DOM component); verified live against a running
 * vault-api instance (see the coder's report).
 */

import { store } from "../store-singleton.js";
import { onViewChange } from "../router.js";
import { createStatusIcon } from "./status-icon.js";
import { createSheetDialog } from "./sheet-dialog.js";
import {
  partitionClients,
  addressesText,
  describeHealthyClient,
  describeBypassClient,
  BYPASS_EXPLANATION,
} from "../lib/clients-view.js";
import { planClientsUpdate } from "../lib/clients-render-plan.js";

const state = {
  clients: store.snapshot("clients") || [],
};

// WP 4e.3: "drawer" — same ambient-side-panel treatment as the notifications
// panel (docs/PROJECT_PLAN.md's Phase 4e section): from BP-L up this appears
// at the right edge instead of the bottom. No motion either way — a plain
// `display` toggle, same as every other overlay here (Opus review, WP 4e.3
// fix round: "slides in" overclaimed an animation this codebase's overlays
// do not have). Below BP-L it stays the mockup's bottom sheet, unchanged.
const dialog = createSheetDialog({ ariaLabel: "Client status", variant: "drawer" });

const heading = document.createElement("h2");
heading.textContent = "Clients";
const intro = document.createElement("p");
intro.className = "hint";
intro.textContent =
  "Machines running vault-agent, matched against what actually arrived at the cache.";

const bypassHeading = document.createElement("h4");
bypassHeading.className = "sec";
bypassHeading.textContent = "Bypassing";
bypassHeading.hidden = true;
const bypassBody = document.createElement("div");

const healthyHeading = document.createElement("h4");
healthyHeading.className = "sec";
healthyHeading.textContent = "Healthy";
healthyHeading.hidden = true;
const healthyBody = document.createElement("div");

const emptyMsg = document.createElement("p");
emptyMsg.className = "hint";
emptyMsg.textContent = "No clients have reported yet.";
emptyMsg.hidden = true;

const closeBtn = document.createElement("button");
closeBtn.type = "button";
closeBtn.className = "btn wide ghost";
closeBtn.textContent = "Close";
closeBtn.addEventListener("click", () => dialog.close());

dialog.body.append(
  heading,
  intro,
  emptyMsg,
  bypassHeading,
  bypassBody,
  healthyHeading,
  healthyBody,
  closeBtn,
);

function buildRow(client, { bypass }) {
  const card = document.createElement("div");
  card.className = "jobcard" + (bypass ? " bypass" : "");
  card.dataset.clientId = client.client_id;

  const top = document.createElement("div");
  top.className = "jobtop";

  const info = document.createElement("div");
  const nm = document.createElement("div");
  nm.className = "nm";
  nm.textContent = client.client_id;
  const sm = document.createElement("div");
  sm.className = "sm";
  sm.dataset.statsLine = "";
  sm.textContent = statsLine(client, { bypass });
  info.append(nm, sm);

  const badge = document.createElement("span");
  badge.className = "badge " + (bypass ? "tx-warn" : "tx-cached");
  const icon = createStatusIcon(bypass ? "warn" : "cached", { size: "sm" });
  // The shared status-icon vocabulary's built-in sr-only label is
  // game-caching wording ("Current"/"Warning") reused here for its shape
  // only — this row's own visible word ("Healthy"/"Bypassing") is the
  // correct accessible text, so the icon's label must not also be read
  // (same "avoid double/mismatched announcement" posture as the
  // notifications panel row's icon — components/notifications.js).
  icon.setAttribute("aria-hidden", "true");
  badge.appendChild(icon);
  const word = document.createElement("span");
  word.textContent = bypass ? "Bypassing" : "Healthy";
  badge.appendChild(word);

  top.append(info, badge);
  card.appendChild(top);

  if (bypass) {
    const hint = document.createElement("p");
    hint.className = "hint";
    hint.textContent = BYPASS_EXPLANATION;
    card.appendChild(hint);
  }

  return card;
}

function statsLine(client, { bypass }) {
  const stats = bypass ? describeBypassClient(client) : describeHealthyClient(client);
  return `${addressesText(client)} · ${stats}`;
}

function fullRender() {
  const { bypassing, healthy } = partitionClients(state.clients);

  emptyMsg.hidden = state.clients.length > 0;

  bypassHeading.hidden = bypassing.length === 0;
  bypassBody.replaceChildren(...bypassing.map((c) => buildRow(c, { bypass: true })));

  healthyHeading.hidden = healthy.length === 0;
  healthyBody.replaceChildren(...healthy.map((c) => buildRow(c, { bypass: false })));
}

/** Update just `.sm`'s text for clients whose SECTION did not change
 * (`lib/clients-render-plan.js`'s `patch` list) — never rebuilds the row,
 * so an open sheet's scroll position survives an unrelated stats tick. */
function patchStats(clientIds) {
  const byId = new Map(state.clients.map((c) => [c.client_id, c]));
  for (const id of clientIds) {
    const client = byId.get(id);
    if (!client) continue;
    const card = dialog.body.querySelector(`.jobcard[data-client-id="${cssEscape(id)}"]`);
    const sm = card ? card.querySelector('[data-stats-line]') : null;
    if (sm) sm.textContent = statsLine(client, { bypass: !!client.bypass_suspected });
  }
}

// `client_id` is operator-chosen free text (agent_reports.py) and could in
// principle contain characters that break a naive `[data-client-id="..."]`
// selector — CSS.escape is the standard way to quote an attribute-selector
// value safely. Falls back to the raw string on a runtime with no
// CSS.escape (none realistically targeted here, but cheap insurance).
function cssEscape(value) {
  return typeof CSS !== "undefined" && typeof CSS.escape === "function"
    ? CSS.escape(value)
    : String(value).replace(/["\\]/g, "\\$&");
}

store.subscribe("clients", ({ items, diff }) => {
  if (!Array.isArray(items)) return; // {error} payload — nothing to render
  const plan = planClientsUpdate(diff);
  state.clients = items;
  if (!dialog.isOpen()) return; // sheet isn't showing right now — nothing to paint
  if (plan.full || plan.rebuild.length) {
    fullRender();
  } else if (plan.patch.length) {
    patchStats(plan.patch);
  }
});

/** Open the clients sheet, painting it from the latest snapshot first. */
export function openClientsSheet() {
  fullRender();
  dialog.open();
}

// Navigation dismisses transient surfaces (mockup rule, NOTES "Behavior
// rule for the real app" — the clients sheet is explicitly named alongside
// the detail sheet and the notifications panel). Without this, tapping a
// bottom-nav item while the sheet is open would leave it painted over the
// new view, same class of bug as the mockup's original overlay bug.
onViewChange(() => dialog.close());

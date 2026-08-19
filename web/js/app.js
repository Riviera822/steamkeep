/**
 * App shell entry point (WP 4a.1).
 *
 * Wires the bottom nav to the router and swaps the active view. No API
 * calls, no data fetching, no polling here — that lands in WP 4a.2 (API
 * client + polling store); this work package is scaffolding only.
 */
import { VIEWS, DEFAULT_VIEW, currentView, navigateTo, onViewChange } from "./router.js";
import { initToast } from "./components/toast.js";
import { renderLibrary } from "./views/library.js";
import { renderDownloads } from "./views/downloads.js";
import { renderSettings } from "./views/settings.js";
import { maybeShowOnboardingOnStartup } from "./onboarding.js";
// WP 4a.7 — side-effect imports: both components bind their DOM elements
// and store subscriptions at module load (same posture as views/downloads.js's
// module-level nav-pip wiring), so importing them is all app.js needs to do.
// clients-sheet.js is not imported directly here — both of these already
// import it (it is the shared target their "Details"/notification-tap
// actions open), and ES modules are evaluated once and cached.
import "./components/notifications.js";
import "./components/bypass-banner.js";
// WP 4e.6, Opus review should-fix S3: rail-panel.js is a plain, dependency-
// injected factory (`createRailPanel`) with NO import-time side effects of
// its own — the same `store.js`/`store-singleton.js` split, applied to this
// component instead of self-wiring on import like notifications.js/
// bypass-banner.js above. This is the one real, side-effecting call (the
// `store-singleton.js` role), which is why it needs the store/api imports
// below rather than a bare `import "./components/rail-panel.js";`.
import { createRailPanel } from "./components/rail-panel.js";
// WP 4h.2 — same DI-factory posture as rail-panel.js just above (see that
// import's comment): decision-panel.js has no import-time side effects, so
// it needs the real DOM/store/router handed in here.
import { createDecisionPanel } from "./components/decision-panel.js";
import { store } from "./store-singleton.js";
import { api, getStoredApiKey, isDemoMode } from "./api.js";

const RENDERERS = {
  library: renderLibrary,
  downloads: renderDownloads,
  settings: renderSettings,
};

const viewRoot = document.getElementById("view-root");
const navButtons = Array.from(document.querySelectorAll(".nav-btn"));

function renderView(view) {
  const render = RENDERERS[view] || RENDERERS[DEFAULT_VIEW];
  viewRoot.replaceChildren(render());

  for (const btn of navButtons) {
    if (btn.dataset.view === view) {
      btn.setAttribute("aria-current", "page");
    } else {
      btn.removeAttribute("aria-current");
    }
  }
}

for (const btn of navButtons) {
  if (!VIEWS.includes(btn.dataset.view)) continue;
  btn.addEventListener("click", () => navigateTo(btn.dataset.view));
}

onViewChange(renderView);
initToast();
createRailPanel({
  elements: {
    headEl: document.getElementById("rail-head"),
    vaultNameEl: document.getElementById("rail-vault-name"),
    footEl: document.getElementById("rail-foot"),
    cacheEl: document.getElementById("rail-cache"),
    versionEl: document.getElementById("rail-version"),
    createElement: (tag) => document.createElement(tag),
  },
  store,
  apiClient: api,
  getStoredApiKey,
  isDemoMode,
});
createDecisionPanel({
  elements: {
    rootEl: document.getElementById("decision-panel"),
    bodyEl: document.getElementById("dp-body"),
    collapseBtn: document.getElementById("dp-collapse"),
    dismissBtn: document.getElementById("dp-dismiss"),
    appEl: document.getElementById("app"),
    createElement: (tag) => document.createElement(tag),
  },
  store,
  onViewChange,
  getCurrentView: currentView,
  storage: window.localStorage,
});
renderView(currentView());
// WP 4a.6: shows the 3-step onboarding overlay on top of whatever view just
// rendered when no vault API key is stored yet and demo mode was not
// already chosen (lib/onboarding-steps.js's shouldShowOnboarding). The
// overlay covers the whole shell (css/app.css `.onb`) so which view sits
// underneath does not matter.
maybeShowOnboardingOnStartup();

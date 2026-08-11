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
renderView(currentView());
// WP 4a.6: shows the 3-step onboarding overlay on top of whatever view just
// rendered when no vault API key is stored yet and demo mode was not
// already chosen (lib/onboarding-steps.js's shouldShowOnboarding). The
// overlay covers the whole shell (css/app.css `.onb`) so which view sits
// underneath does not matter.
maybeShowOnboardingOnStartup();

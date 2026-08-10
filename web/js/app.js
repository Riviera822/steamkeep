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

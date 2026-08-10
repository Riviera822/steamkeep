/**
 * Settings view — scaffold only (WP 4a.1). Filled in by WP 4a.6
 * (onboarding, connection profiles, vault name, Steam identity).
 */
export function renderSettings() {
  const section = document.createElement("section");
  section.className = "view view-settings";

  const h1 = document.createElement("h1");
  h1.textContent = "Settings";

  const p = document.createElement("p");
  p.className = "view-placeholder";
  p.textContent =
    "Connection, vault name and Steam identity settings will appear here once the settings view ships (WP 4a.6).";

  section.append(h1, p);
  return section;
}

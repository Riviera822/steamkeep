/**
 * Library view — scaffold only (WP 4a.1). Filled in by WP 4a.3 (grid,
 * search, filter chips, multi-select) and WP 4a.4 (detail sheet).
 */
export function renderLibrary() {
  const section = document.createElement("section");
  section.className = "view view-library";

  const h1 = document.createElement("h1");
  h1.textContent = "Library";

  const p = document.createElement("p");
  p.className = "view-placeholder";
  p.textContent =
    "Your cached and cacheable games will appear here once the library view ships (WP 4a.3).";

  section.append(h1, p);
  return section;
}

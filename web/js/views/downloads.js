/**
 * Downloads view — scaffold only (WP 4a.1). Filled in by WP 4a.5 (active
 * job card, FIFO queue, history with log excerpts).
 */
export function renderDownloads() {
  const section = document.createElement("section");
  section.className = "view view-downloads";

  const h1 = document.createElement("h1");
  h1.textContent = "Downloads";

  const p = document.createElement("p");
  p.className = "view-placeholder";
  p.textContent =
    "Active and queued download jobs, plus history, will appear here once the downloads view ships (WP 4a.5).";

  section.append(h1, p);
  return section;
}

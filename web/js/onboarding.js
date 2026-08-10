/**
 * Onboarding overlay (WP 4a.6).
 *
 * Ports docs/design/vault-app-mockup.html's 3-step first-run flow (frozen
 * round 7: Connect -> Steam identity -> Ready), adapted to the DECIDED
 * web-UI shape (docs/WORKPACKAGES.md Phase 4a header; ADR-0004 addendum):
 *
 *   Step 1 Connect — vault name (optional) + the vault API key, verified
 *     against a REAL server/key check (`api.checkVaultApiKey`) before
 *     `Continue` unlocks (web/js/lib/onboarding-steps.js's `canAdvance`).
 *     No server-URL/connection-profile fields: this page is served BY
 *     vault-api and the WP 4a.1 CSP is same-origin-only — see api.js's
 *     module header for the removed `getServerUrl`/`setServerUrl`.
 *   Step 2 Steam identity — OPTIONAL: the Steam Web API relay's key
 *     (WP 4a.6r) plus a SteamID64, with a live library-preview lookup. This
 *     is a key-entry form, NOT Valve OpenID — that stays the native
 *     Android app's device-local path (ADR-0004 decision 2). "Continue
 *     without one" is simply the unconditional Continue button.
 *   Step 3 Ready — summary, then reload the app so every module
 *     (store-singleton, app.js) re-initializes against the now-real
 *     localStorage/API state instead of trying to hot-patch it in place.
 *
 * All step-machine DECISIONS (gating, progress, whether to show at all)
 * live in lib/onboarding-steps.js and are unit-tested there; this module is
 * the DOM builder wired to that machine, same split as views/downloads.js.
 *
 * The overlay is built once and appended to `document.body` the first time
 * this module is imported (mirrors store-singleton.js's "start once, reuse
 * forever" posture) — `openOnboarding()`/`closeOnboarding()` only toggle
 * its visibility, never rebuild it.
 *
 * **Dialog semantics (review should-fix, WP 4a.6 cycle 2) — the cheap 80%,
 * not a full modal.** The overlay root carries `role="dialog"` +
 * `aria-modal="true"` + `aria-labelledby` pointing at the CURRENT step's
 * `<h2>` (updated on every `render()`, since the heading — and therefore
 * the accessible name — changes with the step). Opening moves focus to the
 * first real control (step 1's vault-name field); advancing/going back
 * moves focus to the new step's heading (`tabindex="-1"`, common
 * wizard-step pattern) so a screen reader announces the step changed.
 * `Escape` closes the overlay ONLY in `mode: "reconnect"` (Settings'
 * "Reconnect / switch account") — a first-run open has nothing behind it
 * to reveal, so `Escape` is a no-op there — and returns focus to whatever
 * invoked it (captured as `document.activeElement` at `openOnboarding()`
 * time, which is the "Start" button mid-click; the same path `onSkip()`'s
 * reconnect branch already used). **Deferred to WP 4a.8, deliberately not
 * done here:** a real focus TRAP (Tab/Shift+Tab wrapping inside the
 * dialog) and `inert`/`aria-hidden` on the app shell behind it — this WP
 * only closes the two cheapest, highest-value gaps (accessible name +
 * name-change announcement, and a working Escape/return-focus pair for the
 * one path that has something to return to).
 */

import {
  FIRST_STEP,
  LAST_STEP,
  STEP,
  canAdvance,
  nextStep,
  prevStep,
  progressPercent,
  stepTitle,
  shouldShowOnboarding,
} from "./lib/onboarding-steps.js";
import { validSteamId64 } from "./lib/steamid.js";
import { submitSteamKey } from "./lib/steam-key-form.js";
import { api, checkVaultApiKey, getStoredApiKey, setStoredApiKey, isDemoMode, setDemoMode } from "./api.js";
import { showToast } from "./components/toast.js";

const MARK_SVG =
  '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="M12 2.6 20.5 6v6.1c0 5-3.6 8.3-8.5 9.3-4.9-1-8.5-4.3-8.5-9.3V6L12 2.6Z"/><circle cx="12" cy="11.4" r="2.6"/><path d="M12 14v3.2"/></svg>';
const EYE_SVG =
  '<svg width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M1.6 10S4.7 4.6 10 4.6 18.4 10 18.4 10 15.3 15.4 10 15.4 1.6 10 1.6 10Z"/><circle cx="10" cy="10" r="2.5"/></svg>';
const CHECK_SVG =
  '<svg width="14" height="14" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2.2" aria-hidden="true"><path d="m4 10.6 4 4L16.4 5.6"/></svg>';

function staticIcon(svgMarkup) {
  const span = document.createElement("span");
  span.innerHTML = svgMarkup;
  return span.firstElementChild;
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

/** A step's `<h2>`, given an id and `tabindex="-1"` so it can receive
 * programmatic focus on a step change (it is never a Tab stop otherwise —
 * this does not add it to the natural tab order). Used as both the visible
 * heading and the `aria-labelledby` target for the dialog's accessible
 * name (see the module header's "Dialog semantics" note). */
function stepHeading(step, text) {
  const h2 = el("h2", null, text);
  h2.id = `onb-step-${step}-heading`;
  h2.tabIndex = -1;
  return h2;
}

function errorText(err) {
  if (err && typeof err.detail === "string" && err.detail) return err.detail;
  return (err && err.message) || "Request failed.";
}

// ---------------------------------------------------------------------
// State
// ---------------------------------------------------------------------

function freshState() {
  return {
    step: FIRST_STEP,
    mode: "first-run", // "first-run" | "reconnect" — governs Skip's behaviour
    tested: false,
    settings: null, // the {readonly, settings} GET /v1/settings answer from the last successful test
    steamStatus: { configured: false, key_last4: null },
    lookup: null, // {gameCount, players} | {error} | null (nothing looked up yet)
  };
}

let state = freshState();
let root = null;
let els = {};
/** The element to return focus to when a `mode: "reconnect"` overlay
 * closes (Escape, or Skip) — captured as `document.activeElement` at
 * `openOnboarding()` time (the "Start" button mid-click). `null` for a
 * first-run open (nothing was focused for a reason to return to) and
 * cleared immediately after use, so a stale reference is never refocused
 * twice. */
let invokerEl = null;

// ---------------------------------------------------------------------
// Step 1 — Connect
// ---------------------------------------------------------------------

function buildStep1() {
  const section = el("section", "ostep");
  section.dataset.step = String(STEP.CONNECT);

  const heading = stepHeading(STEP.CONNECT, "Connect to your vault");
  section.append(heading);
  section.append(
    el(
      "p",
      "lede",
      "SteamVault is already serving this page from your vault — the API key below is what lets THIS BROWSER talk to it.",
    ),
  );

  const nameField = el("div", "field");
  const nameLabel = el("label", null, "Name your vault (optional)");
  nameLabel.htmlFor = "onb-name";
  const nameInput = document.createElement("input");
  nameInput.className = "inp txt";
  nameInput.id = "onb-name";
  nameInput.type = "text";
  nameInput.maxLength = 24;
  nameInput.placeholder = "e.g. vault-01";
  nameInput.autocomplete = "off";
  nameField.append(nameLabel, nameInput, el("p", "foot-note", "Shown in the app; defaults to the server's own name."));

  const keyField = el("div", "field");
  const keyLabel = el("label", null, "Vault API key");
  keyLabel.htmlFor = "onb-key";
  const keyWrap = el("div", "inp");
  const keyInput = document.createElement("input");
  keyInput.id = "onb-key";
  keyInput.type = "password";
  keyInput.autocomplete = "off";
  keyInput.spellcheck = false;
  const eyeBtn = document.createElement("button");
  eyeBtn.type = "button";
  eyeBtn.setAttribute("aria-label", "Reveal API key");
  eyeBtn.appendChild(staticIcon(EYE_SVG));
  eyeBtn.addEventListener("click", () => {
    keyInput.type = keyInput.type === "password" ? "text" : "password";
  });
  keyWrap.append(keyInput, eyeBtn);
  keyField.append(keyLabel, keyWrap);

  const testBtn = el("button", "btn wide", "Test connection");
  testBtn.type = "button";
  const okLine = el("div", "okline");
  okLine.appendChild(staticIcon(CHECK_SVG));
  const okText = el("span");
  okLine.appendChild(okText);
  const errLine = el("p", "errline");
  errLine.hidden = true;

  testBtn.addEventListener("click", async () => {
    const key = keyInput.value.trim();
    if (!key) {
      errLine.hidden = false;
      errLine.textContent = "Enter the vault API key first.";
      return;
    }
    testBtn.disabled = true;
    testBtn.textContent = "Testing…";
    okLine.classList.remove("on");
    errLine.hidden = true;
    try {
      const { health, settings } = await checkVaultApiKey(key);
      setStoredApiKey(key);
      state.tested = true;
      state.settings = settings;
      const vaultNameEntry = settings.settings.find((s) => s.key === "vault_name");
      if (!nameInput.value && vaultNameEntry && vaultNameEntry.effective) {
        nameInput.value = vaultNameEntry.effective;
      }
      const desiredName = nameInput.value.trim();
      if (
        desiredName &&
        !settings.readonly &&
        vaultNameEntry &&
        desiredName !== (vaultNameEntry.effective || "")
      ) {
        try {
          await api.patchSettings({ vault_name: desiredName });
        } catch (patchErr) {
          showToast(`Vault name not saved: ${errorText(patchErr)}`, { warn: true });
        }
      }
      okText.textContent = `200 OK · vault-api${health && health.version ? " " + health.version : ""}`;
      okLine.classList.add("on");
    } catch (err) {
      errLine.hidden = false;
      errLine.textContent = errorText(err);
      state.tested = false;
    } finally {
      testBtn.disabled = false;
      testBtn.textContent = "Test connection";
      render();
    }
  });

  section.append(
    nameField,
    keyField,
    testBtn,
    okLine,
    errLine,
    el(
      "p",
      "foot-note",
      "The key is stored on this browser only. It never leaves the app except as the X-Api-Key header on requests to this same server.",
    ),
  );

  els.step1 = { section, heading, nameInput, keyInput };
  return section;
}

// ---------------------------------------------------------------------
// Step 2 — Steam identity (optional)
// ---------------------------------------------------------------------

function renderSteamStatus() {
  const { statusLine, removeBtn } = els.step2;
  if (state.steamStatus.configured) {
    statusLine.textContent = `Relay key configured (••••${state.steamStatus.key_last4}).`;
    removeBtn.hidden = false;
  } else {
    statusLine.textContent = "No Steam Web API key configured yet.";
    removeBtn.hidden = true;
  }
}

function renderLookupResult() {
  const { lookupBody } = els.step2;
  lookupBody.replaceChildren();
  if (!state.lookup) return;
  if (state.lookup.error) {
    lookupBody.appendChild(el("p", "errline", state.lookup.error));
    return;
  }
  const persona = state.lookup.persona;
  if (persona) {
    lookupBody.appendChild(
      el("p", "foot-note", `Signed in as ${persona.personaname} · SteamID64 ${persona.steamid}`),
    );
  }
  lookupBody.appendChild(el("p", "foot-note", `${state.lookup.gameCount} games found.`));
  const list = el("ul", "bullets");
  for (const g of state.lookup.preview) {
    const li = document.createElement("li");
    li.textContent = g.name;
    list.appendChild(li);
  }
  lookupBody.appendChild(list);
}

function buildStep2() {
  const section = el("section", "ostep");
  section.dataset.step = String(STEP.STEAM);

  const heading = stepHeading(STEP.STEAM, "Optional: link your Steam library");
  section.append(heading);
  section.append(
    el(
      "p",
      "lede",
      "Without this, SteamVault still manages the cache — the library grid just lists app ids instead of covers and names.",
    ),
  );

  section.append(el("h4", "sec", "What gets fetched"));
  const bullets = el("ul", "bullets");
  const items = [
    ["Owned games", "the list behind your library grid."],
    ["Persona name & avatar", "your public profile display name and picture."],
  ];
  for (const [b, rest] of items) {
    const li = document.createElement("li");
    const strong = document.createElement("b");
    strong.textContent = b;
    li.append(strong, document.createTextNode(" — " + rest));
    bullets.appendChild(li);
  }
  section.append(bullets);

  const disclaim = el(
    "div",
    "disclaim",
    "This is read via a small opt-in relay on THIS server (never proxied through Valve credentials — ADR-0004 addendum): with a key configured, library queries leave your LAN toward Valve's servers. SteamVault is a community project and is not affiliated with Valve Corporation.",
  );
  section.append(disclaim);

  section.append(el("h4", "sec", "Steam Web API key"));
  const statusLine = el("p", "foot-note", "");
  const keyField = el("div", "field");
  const keyLabel = el("label", null, "Web API key (32 hex characters)");
  keyLabel.htmlFor = "onb-steam-key";
  const keyInput = document.createElement("input");
  keyInput.id = "onb-steam-key";
  keyInput.className = "inp txt";
  keyInput.type = "password";
  keyInput.autocomplete = "off";
  keyInput.spellcheck = false;
  keyInput.placeholder = "from steamcommunity.com/dev/apikey";
  keyField.append(keyLabel, keyInput);

  const saveBtn = el("button", "btn sm", "Save key");
  saveBtn.type = "button";
  const removeBtn = el("button", "btn ghost sm", "Remove key");
  removeBtn.type = "button";
  removeBtn.hidden = true;
  const keyErr = el("p", "errline");
  keyErr.hidden = true;

  saveBtn.addEventListener("click", async () => {
    keyErr.hidden = true;
    saveBtn.disabled = true;
    const outcome = await submitSteamKey(keyInput, api);
    saveBtn.disabled = false;
    if (!outcome.ok) {
      keyErr.hidden = false;
      keyErr.textContent = outcome.error;
      return;
    }
    state.steamStatus = outcome.result;
    renderSteamStatus();
    showToast("Steam Web API key saved.");
  });
  removeBtn.addEventListener("click", async () => {
    removeBtn.disabled = true;
    try {
      await api.deleteSteamKey();
      state.steamStatus = { configured: false, key_last4: null };
      state.lookup = null;
      renderSteamStatus();
      renderLookupResult();
    } catch (err) {
      showToast(errorText(err), { warn: true });
    } finally {
      removeBtn.disabled = false;
    }
  });

  const keyRow = el("div", "onbnav");
  keyRow.append(saveBtn, removeBtn);
  section.append(keyField, keyRow, keyErr, statusLine);

  section.append(el("h4", "sec", "Preview your library"));
  const idField = el("div", "field");
  const idLabel = el("label", null, "SteamID64");
  idLabel.htmlFor = "onb-steam-steamid";
  idField.append(idLabel);
  const idInput = document.createElement("input");
  idInput.id = "onb-steam-steamid";
  idInput.className = "inp txt";
  idInput.type = "text";
  idInput.inputMode = "numeric";
  idInput.placeholder = "76561198042117903";
  idField.appendChild(idInput);
  const lookupBtn = el("button", "btn sm", "Look up");
  lookupBtn.type = "button";
  const lookupBody = el("div");

  lookupBtn.addEventListener("click", async () => {
    const steamid = validSteamId64(idInput.value.trim());
    if (!steamid) {
      state.lookup = { error: "That does not look like a valid SteamID64 (17 digits)." };
      renderLookupResult();
      return;
    }
    lookupBtn.disabled = true;
    try {
      const [owned, players] = await Promise.all([
        api.steamOwnedGames(steamid),
        api.steamPlayerSummaries(steamid).catch(() => null),
      ]);
      state.lookup = {
        gameCount: owned.game_count,
        preview: owned.games.slice(0, 8),
        persona: players && players.players && players.players[0],
      };
    } catch (err) {
      state.lookup = { error: errorText(err) };
    } finally {
      lookupBtn.disabled = false;
      renderLookupResult();
    }
  });

  section.append(idField, lookupBtn, lookupBody);
  section.append(
    el("p", "foot-note", "Not ready to link an account? Continue without one — you can set this up later under Settings."),
  );

  els.step2 = { section, heading, keyInput, statusLine, removeBtn, lookupBody };
  return section;
}

// ---------------------------------------------------------------------
// Step 3 — Ready
// ---------------------------------------------------------------------

function buildStep3() {
  const section = el("section", "ostep");
  section.dataset.step = String(STEP.DONE);
  const heading = stepHeading(STEP.DONE, "You're set");
  section.append(heading);
  section.append(el("p", "lede", "The app will reload once to pick everything up."));
  const summary = el("div", "summary");
  section.append(summary);
  els.step3 = { section, heading, summary };
  return section;
}

function renderSummary() {
  const { summary } = els.step3;
  summary.replaceChildren();
  const rows = [
    ["Vault name", els.step1.nameInput.value.trim() || "(server default)"],
    ["API key", state.tested ? "verified" : "not verified"],
    ["Steam identity", state.steamStatus.configured ? "configured" : "not linked"],
  ];
  for (const [label, value] of rows) {
    const row = document.createElement("div");
    row.append(el("span", null, label), el("span", null, value));
    summary.appendChild(row);
  }
}

// ---------------------------------------------------------------------
// Chrome: header, track, footer nav, skip
// ---------------------------------------------------------------------

/** `els.step1.heading` / `els.step2.heading` / `els.step3.heading` by step
 * number — used by `render()` (accessible name) and the nav handlers
 * (focus-on-step-change). */
function headingForStep(step) {
  if (step === STEP.CONNECT) return els.step1.heading;
  if (step === STEP.STEAM) return els.step2.heading;
  return els.step3.heading;
}

/** Escape closes the overlay, but ONLY in `mode: "reconnect"` — a
 * first-run open has nothing behind it to reveal, so Escape is
 * intentionally a no-op there (module header "Dialog semantics"). Bound to
 * `document`, not `root`, because there is no focus trap (yet — WP 4a.8):
 * focus is not guaranteed to still be inside `root` when the key is
 * pressed, and this must still work. Guarded on `root` actually being
 * visible so it is a no-op the rest of the time the module is loaded. */
function onDocumentKeydown(event) {
  if (event.key !== "Escape") return;
  if (!root || root.classList.contains("gone")) return;
  if (state.mode !== "reconnect") return;
  event.preventDefault();
  closeOnboarding();
}

function buildOverlay() {
  root = el("div", "onb gone");
  root.id = "onboarding-root";
  // Dialog semantics (review should-fix, cheap 80% — see module header):
  // aria-labelledby is kept in sync with the current step's heading by
  // render(), since the "You're set" step is a DIFFERENT dialog title, not
  // a static one set once here.
  root.setAttribute("role", "dialog");
  root.setAttribute("aria-modal", "true");
  document.addEventListener("keydown", onDocumentKeydown);

  const header = el("header", "onbhead");
  const mark = el("div", "mark");
  mark.appendChild(staticIcon(MARK_SVG));
  const titleWrap = el("div");
  titleWrap.style.flex = "1";
  const wordmark = el("div", "wordmark", "SteamVault");
  const stepnum = el("div", "stepnum", "");
  titleWrap.append(wordmark, stepnum);
  const skipBtn = el("button", "btn ghost sm", "Skip");
  skipBtn.type = "button";
  skipBtn.addEventListener("click", onSkip);
  header.append(mark, titleWrap, skipBtn);

  const track = el("div", "track");
  const trackFill = el("i");
  track.appendChild(trackFill);

  const rest = el("div", "rest");
  const step1 = buildStep1();
  const step2 = buildStep2();
  const step3 = buildStep3();
  rest.append(step1, step2, step3);

  const footer = el("div", "onbfoot");
  const nav = el("div", "onbnav");
  const backBtn = el("button", "btn ghost wide", "Back");
  backBtn.type = "button";
  backBtn.addEventListener("click", () => {
    state.step = prevStep(state.step);
    render();
    // Focus the new step's heading (tabindex="-1", never a natural Tab
    // stop) so a screen reader announces the step changed — the cheap
    // stand-in for a real focus trap (WP 4a.8; module header).
    headingForStep(state.step).focus();
  });
  const nextBtn = el("button", "btn primary wide", "Continue");
  nextBtn.type = "button";
  nextBtn.addEventListener("click", () => {
    if (state.step === LAST_STEP) {
      finish();
      return;
    }
    state.step = nextStep(state.step, state);
    render();
    headingForStep(state.step).focus();
  });
  nav.append(backBtn, nextBtn);
  const demoLink = el("button", "skiplink", "Skip for now — browse in demo mode");
  demoLink.type = "button";
  demoLink.addEventListener("click", onDemoSkip);
  footer.append(nav, demoLink);

  root.append(header, track, rest, footer);
  document.body.appendChild(root);

  els = {
    ...els,
    root,
    stepnum,
    track: trackFill,
    steps: [step1, step2, step3],
    backBtn,
    nextBtn,
    demoLink,
  };
}

function render() {
  els.stepnum.textContent = stepTitle(state.step);
  els.track.style.width = progressPercent(state.step) + "%";
  for (const sec of els.steps) sec.classList.toggle("on", Number(sec.dataset.step) === state.step);
  els.backBtn.style.display = state.step > FIRST_STEP ? "" : "none";
  els.nextBtn.disabled = !canAdvance(state.step, state);
  els.nextBtn.textContent = state.step === LAST_STEP ? "Go to library" : "Continue";
  els.demoLink.style.display = state.step === LAST_STEP ? "none" : "block";
  // Dialog semantics: the accessible name tracks the CURRENT step's
  // heading — "Connect to your vault" and "You're set" are different
  // dialogs from an assistive-tech point of view, not one static title.
  root.setAttribute("aria-labelledby", headingForStep(state.step).id);
  if (state.step === STEP.DONE) renderSummary();
}

function onSkip() {
  if (state.mode === "first-run") {
    onDemoSkip();
    return;
  }
  closeOnboarding(); // reconnect flow: bail out with whatever was already configured, unchanged
}

function onDemoSkip() {
  setDemoMode(true);
  window.location.reload();
}

function finish() {
  window.location.reload();
}

// ---------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------

/** Show the onboarding overlay. `mode: "reconnect"` is used from Settings'
 * "Reconnect / switch account" — Skip then just closes instead of enabling
 * demo mode, since a working connection may already exist. */
export function openOnboarding({ mode = "first-run" } = {}) {
  if (!root) buildOverlay();
  state = freshState();
  state.mode = mode;
  // Reconnect is always a click (Settings' "Start" button) — capture it as
  // the element to return focus to on close. First-run has no such
  // invoker (it opens itself at app startup): leave it null so
  // closeOnboarding()'s refocus step is a no-op there.
  invokerEl = mode === "reconnect" ? document.activeElement : null;
  els.step1.keyInput.value = "";
  els.step1.nameInput.value = "";
  render();
  if (getStoredApiKey()) {
    // Only worth asking on reconnect — a first run has no valid vault API
    // key yet, so this call would just 401 (harmless, but pure console
    // noise: DevTools logs every non-2xx fetch regardless of the JS-level
    // rejection handler below).
    api.getSteamKey().then(
      (status) => {
        state.steamStatus = status;
        renderSteamStatus();
      },
      () => {}, // offline/unexpected — leave the default "not configured" state
    );
  }
  root.classList.remove("gone");
  document.body.classList.add("onboarding");
  // Move focus to the first real control (module header "Dialog
  // semantics") — not the heading: this is the START of the flow, there is
  // nothing to announce a "change" from yet.
  els.step1.nameInput.focus();
}

export function closeOnboarding() {
  if (!root) return;
  root.classList.add("gone");
  document.body.classList.remove("onboarding");
  if (invokerEl) {
    invokerEl.focus();
    invokerEl = null;
  }
}

/** Call once at app startup. Opens the flow automatically on a genuine
 * first run (no stored vault API key, demo mode not already chosen) — see
 * lib/onboarding-steps.js's `shouldShowOnboarding`. */
export function maybeShowOnboardingOnStartup() {
  if (shouldShowOnboarding({ hasApiKey: !!getStoredApiKey(), demoMode: isDemoMode() })) {
    openOnboarding({ mode: "first-run" });
  }
}

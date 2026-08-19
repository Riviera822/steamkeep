/**
 * Headless tests for web/js/lib/cover-art.js (WP 4a.3).
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { STEAM_CDN_HOST, coverArtUrl, headerArtUrl, fallbackHues, fallbackPattern } from "../js/lib/cover-art.js";

test("coverArtUrl uses exactly the CSP-allowed host and the library_600x900 asset path", () => {
  const url = coverArtUrl(440);
  assert.equal(url, `https://${STEAM_CDN_HOST}/steam/apps/440/library_600x900.jpg`);
  assert.equal(STEAM_CDN_HOST, "cdn.akamai.steamstatic.com");
});

test("coverArtUrl is a real, https, single-host URL for any positive appid", () => {
  const url = coverArtUrl(2010010);
  const parsed = new URL(url);
  assert.equal(parsed.protocol, "https:");
  assert.equal(parsed.hostname, STEAM_CDN_HOST);
  assert.equal(parsed.pathname, "/steam/apps/2010010/library_600x900.jpg");
});

// WP 4h.3 — header/hero art for the detail card.
test("headerArtUrl uses exactly the CSP-allowed host and the header.jpg asset path", () => {
  const url = headerArtUrl(440);
  assert.equal(url, `https://${STEAM_CDN_HOST}/steam/apps/440/header.jpg`);
});

test("headerArtUrl is a real, https, single-host URL for any positive appid, and varies WITH the appid (mutation: a hardcoded/fixed URL dies here)", () => {
  const urlA = headerArtUrl(440);
  const urlB = headerArtUrl(2010010);
  const parsedA = new URL(urlA);
  const parsedB = new URL(urlB);
  assert.equal(parsedA.protocol, "https:");
  assert.equal(parsedA.hostname, STEAM_CDN_HOST);
  assert.equal(parsedA.pathname, "/steam/apps/440/header.jpg");
  assert.equal(parsedB.pathname, "/steam/apps/2010010/header.jpg");
  assert.notEqual(urlA, urlB, "headerArtUrl must be a function of the appid, not a fixed/hardcoded URL");
});

test("headerArtUrl and coverArtUrl share the same CDN host but a different asset path (two distinct assets, one CSP allowance)", () => {
  const appid = 730;
  const header = new URL(headerArtUrl(appid));
  const cover = new URL(coverArtUrl(appid));
  assert.equal(header.hostname, cover.hostname);
  assert.notEqual(header.pathname, cover.pathname);
});

test("fallbackHues is deterministic: same appid -> same pair, every time", () => {
  const a1 = fallbackHues(440);
  const a2 = fallbackHues(440);
  assert.deepEqual(a1, a2);
});

test("fallbackHues differs across different appids (not a constant)", () => {
  const a = fallbackHues(440);
  const b = fallbackHues(730);
  assert.notDeepEqual(a, b);
});

test("fallbackHues stays within a valid hue range [0, 360)", () => {
  for (const appid of [1, 2, 440, 730, 2010010, 999999999]) {
    const { h1, h2 } = fallbackHues(appid);
    assert.ok(h1 >= 0 && h1 < 360, `h1=${h1} out of range for ${appid}`);
    assert.ok(h2 >= 0 && h2 < 360, `h2=${h2} out of range for ${appid}`);
  }
});

test("fallbackPattern is deterministic and always one of the six CSS pattern classes (p0..p5)", () => {
  for (const appid of [1, 2, 440, 730, 2010010]) {
    const p1 = fallbackPattern(appid);
    const p2 = fallbackPattern(appid);
    assert.equal(p1, p2);
    assert.ok(Number.isInteger(p1) && p1 >= 0 && p1 <= 5, `pattern=${p1} out of range for ${appid}`);
  }
});

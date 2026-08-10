import { test } from "node:test";
import assert from "node:assert/strict";
import { validSteamWebApiKey, submitSteamKey } from "../js/lib/steam-key-form.js";

const VALID_KEY = "0123456789ABCDEF0123456789ABCDEF";
const VALID_KEY_LOWER = "0123456789abcdef0123456789abcdef";

test("validSteamWebApiKey accepts exactly 32 hex chars, either case", () => {
  assert.equal(validSteamWebApiKey(VALID_KEY), true);
  assert.equal(validSteamWebApiKey(VALID_KEY_LOWER), true);
});

test("validSteamWebApiKey rejects wrong length", () => {
  assert.equal(validSteamWebApiKey(VALID_KEY.slice(0, 31)), false);
  assert.equal(validSteamWebApiKey(VALID_KEY + "0"), false);
  assert.equal(validSteamWebApiKey(""), false);
});

test("validSteamWebApiKey rejects a non-hex character (mutation target: g is not hex)", () => {
  assert.equal(validSteamWebApiKey("g123456789ABCDEF0123456789ABCDEF"), false);
});

test("validSteamWebApiKey rejects non-string input", () => {
  assert.equal(validSteamWebApiKey(null), false);
  assert.equal(validSteamWebApiKey(undefined), false);
  assert.equal(validSteamWebApiKey(1234), false);
});

test("submitSteamKey: success clears the field and returns the client's result", async () => {
  const field = { value: VALID_KEY };
  let sentKey = null;
  const client = {
    putSteamKey: async (key) => {
      sentKey = key;
      return { configured: true, key_last4: key.slice(-4) };
    },
  };
  const result = await submitSteamKey(field, client);
  assert.equal(sentKey, VALID_KEY);
  assert.deepEqual(result, { ok: true, result: { configured: true, key_last4: "CDEF" } });
  // The load-bearing pin: the typed key must be gone from the DOM node
  // after submit (ADR-0004 addendum / docs/LEARNINGS.md).
  assert.equal(field.value, "");
});

test("submitSteamKey: invalid format never calls the network, and clears the field anyway", async () => {
  const field = { value: "not-a-valid-key" };
  let called = false;
  const client = { putSteamKey: async () => { called = true; return {}; } };
  const result = await submitSteamKey(field, client);
  assert.equal(called, false);
  assert.equal(result.ok, false);
  assert.equal(field.value, "");
});

test("submitSteamKey: a rejected PUT still clears the field, and the error never contains the raw key", async () => {
  const field = { value: VALID_KEY };
  const client = {
    putSteamKey: async () => {
      const err = new Error("PUT /v1/steam/key failed (422)");
      err.detail = "'key' must be exactly 32 hexadecimal characters.";
      throw err;
    },
  };
  const result = await submitSteamKey(field, client);
  assert.equal(result.ok, false);
  assert.equal(field.value, "");
  assert.equal(result.error.includes(VALID_KEY), false);
  assert.equal(JSON.stringify(result).includes(VALID_KEY), false);
});

test("submitSteamKey: a network failure with no .detail falls back to .message, still no raw key retained", async () => {
  const field = { value: VALID_KEY };
  const client = {
    putSteamKey: async () => {
      throw new Error("Network request failed: PUT /v1/steam/key");
    },
  };
  const result = await submitSteamKey(field, client);
  assert.equal(result.ok, false);
  assert.equal(result.error, "Network request failed: PUT /v1/steam/key");
  assert.equal(field.value, "");
});

test("submitSteamKey trims surrounding whitespace before validating", async () => {
  const field = { value: `  ${VALID_KEY}  ` };
  let sentKey = null;
  const client = { putSteamKey: async (key) => { sentKey = key; return {}; } };
  await submitSteamKey(field, client);
  assert.equal(sentKey, VALID_KEY);
});

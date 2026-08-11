/**
 * Headless tests for web/js/lib/notification-log.js (WP 4a.7 DoD).
 *
 * Run: node --test "web/tests/*.test.js"   (see web/tests/README.md)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  NOTIFICATION_META,
  metaFor,
  MAX_LOG_ENTRIES,
  appendNotifications,
  unreadCount,
  markAllRead,
  navigationTargetFor,
} from "../js/lib/notification-log.js";

// ---------------------------------------------------------------------
// metaFor / NOTIFICATION_META — literal-pinned per docs/LEARNINGS.md's
// constants-vs-literals rule (a cross-module wire/label contract must be
// pinned by string literals, never a derived round-trip).
// ---------------------------------------------------------------------

test("metaFor: every real event type has literal icon/tx/word metadata", () => {
  assert.deepEqual(metaFor("job_finished"), { icon: "cached", tx: "tx-cached", word: "Cached" });
  assert.deepEqual(metaFor("job_failed"), { icon: "error", tx: "tx-error", word: "Failed" });
  assert.deepEqual(metaFor("update_ready"), { icon: "stale", tx: "tx-stale", word: "Update ready" });
  assert.deepEqual(metaFor("bypass_suspected"), { icon: "warn", tx: "tx-warn", word: "Warning" });
  assert.deepEqual(metaFor("bypass_resolved"), { icon: "cached", tx: "tx-cached", word: "Resolved" });
});

test("metaFor: an unknown event type falls back honestly instead of throwing", () => {
  assert.deepEqual(metaFor("something_new"), { icon: "none", tx: "tx-none", word: "Notice" });
});

test("NOTIFICATION_META covers exactly the taxonomy notifications.js emits", () => {
  assert.deepEqual(
    Object.keys(NOTIFICATION_META).sort(),
    ["bypass_resolved", "bypass_suspected", "job_failed", "job_finished", "update_ready"].sort(),
  );
});

// ---------------------------------------------------------------------
// appendNotifications
// ---------------------------------------------------------------------

test("appendNotifications: empty/undefined events is a no-op (returns the SAME log, unchanged nextId)", () => {
  const log = [{ id: 1, type: "job_finished", read: true, at: "x" }];
  const r1 = appendNotifications(log, [], { at: "2026-01-01T00:00:00Z", startId: 5 });
  assert.equal(r1.log, log); // reference equality: nothing to append
  assert.equal(r1.nextId, 5);
  const r2 = appendNotifications(log, undefined, { at: "2026-01-01T00:00:00Z", startId: 5 });
  assert.equal(r2.log, log);
});

test("appendNotifications: new events land newest-first, ahead of the existing log", () => {
  const log = [{ id: 1, type: "job_finished", jobId: 1, read: true, at: "2026-01-01T00:00:00Z" }];
  const events = [
    { type: "update_ready", key: "game:1:stale", appid: 1, name: "Nebula Drift" },
    { type: "bypass_suspected", key: "client:x:suspected", clientId: "x" },
  ];
  const { log: next, nextId } = appendNotifications(log, events, {
    at: "2026-01-02T00:00:00Z",
    startId: 10,
  });
  assert.equal(nextId, 12);
  assert.equal(next.length, 3);
  // Both new entries are unshifted ahead of the old one, in the given order.
  assert.equal(next[0].type, "update_ready");
  assert.equal(next[0].id, 10);
  assert.equal(next[0].read, false);
  assert.equal(next[0].at, "2026-01-02T00:00:00Z");
  assert.equal(next[1].type, "bypass_suspected");
  assert.equal(next[1].id, 11);
  assert.equal(next[2].id, 1); // the pre-existing entry, untouched
});

test("appendNotifications: original log array is never mutated", () => {
  const log = Object.freeze([{ id: 1, type: "job_finished", read: true, at: "x" }]);
  // Object.freeze makes an in-place push/splice throw in strict mode —
  // this test would fail loudly if appendNotifications tried to mutate it.
  const { log: next } = appendNotifications(log, [{ type: "job_failed", jobId: 2 }], {
    at: "2026-01-02T00:00:00Z",
    startId: 1,
  });
  assert.equal(log.length, 1);
  assert.equal(next.length, 2);
});

test("appendNotifications: caps the log at MAX_LOG_ENTRIES, dropping the OLDEST", () => {
  const log = Array.from({ length: MAX_LOG_ENTRIES }, (_, i) => ({
    id: i,
    type: "job_finished",
    read: true,
    at: "old",
  }));
  const { log: next } = appendNotifications(log, [{ type: "job_failed", jobId: 999 }], {
    at: "new",
    startId: 1000,
  });
  assert.equal(next.length, MAX_LOG_ENTRIES);
  assert.equal(next[0].jobId, 999); // the fresh one is kept, at the front
  // `log` is newest-first, so its LAST element (id MAX_LOG_ENTRIES - 1) is
  // the oldest entry — exactly the one that must be pushed off the tail by
  // the single new arrival once the cap is enforced.
  assert.ok(!next.some((e) => e.id === MAX_LOG_ENTRIES - 1));
  assert.equal(next[next.length - 1].id, MAX_LOG_ENTRIES - 2); // new oldest survivor
});

// ---------------------------------------------------------------------
// unreadCount / markAllRead
// ---------------------------------------------------------------------

test("unreadCount: counts only entries with read === false; handles empty/missing", () => {
  assert.equal(unreadCount([]), 0);
  assert.equal(unreadCount(undefined), 0);
  assert.equal(
    unreadCount([{ read: true }, { read: false }, { read: false }]),
    2,
  );
});

test("markAllRead: flips every unread entry, immutably", () => {
  const log = [{ id: 1, read: false }, { id: 2, read: true }, { id: 3, read: false }];
  const next = markAllRead(log);
  assert.deepEqual(
    next.map((e) => e.read),
    [true, true, true],
  );
  // original untouched
  assert.equal(log[0].read, false);
  assert.equal(log[2].read, false);
});

test("markAllRead: returns the SAME array reference when nothing was unread (cheap no-op)", () => {
  const log = [{ id: 1, read: true }, { id: 2, read: true }];
  assert.equal(markAllRead(log), log);
});

// ---------------------------------------------------------------------
// navigationTargetFor — literal-pinned per event type (WP brief).
// ---------------------------------------------------------------------

test("navigationTargetFor: job_finished and job_failed both go to Downloads, carrying jobId", () => {
  assert.deepEqual(navigationTargetFor({ type: "job_finished", jobId: 7 }), {
    kind: "downloads",
    jobId: 7,
  });
  assert.deepEqual(navigationTargetFor({ type: "job_failed", jobId: 8 }), {
    kind: "downloads",
    jobId: 8,
  });
});

test("navigationTargetFor: update_ready opens the detail sheet, carrying appid/name (WP 4a.4 target upgrade)", () => {
  assert.deepEqual(navigationTargetFor({ type: "update_ready", appid: 42, name: "Nebula Drift" }), {
    kind: "detail",
    appid: 42,
    name: "Nebula Drift",
  });
});

test("navigationTargetFor: bypass_suspected and bypass_resolved both open the clients sheet", () => {
  assert.deepEqual(navigationTargetFor({ type: "bypass_suspected", clientId: "x" }), {
    kind: "clients",
  });
  assert.deepEqual(navigationTargetFor({ type: "bypass_resolved", clientId: "x" }), {
    kind: "clients",
  });
});

test("navigationTargetFor: an unknown event type fails toward the Library, not a crash", () => {
  assert.deepEqual(navigationTargetFor({ type: "something_new" }), { kind: "library" });
});

// SPDX-License-Identifier: AGPL-3.0-only
import test from "node:test";
import assert from "node:assert/strict";
import { eventItem, statusPill } from "./shell.js";
import { fakeDocument, textOf, find } from "./fake-dom.test-util.js";

const printer = (state, problems = []) => ({ printer: { state, problems }, error: null });

test("status pill names the printer state and its worst problem level", () => {
  assert.deepEqual(statusPill(printer("idle")), { text: "Ready", level: "ok" });
  assert.deepEqual(statusPill(printer("processing")), { text: "Printing", level: "busy" });
  assert.deepEqual(statusPill(printer("stopped")), { text: "Stopped", level: "error" });
  assert.deepEqual(statusPill(printer("idle", [{ label: "Ink low", level: "warning" }])), { text: "Ink low", level: "warning" });
  assert.deepEqual(
    statusPill(printer("processing", [{ label: "Ink low", level: "warning" }, { label: "Paper jam", level: "error" }])),
    { text: "Ink low, Paper jam", level: "error" },
  );
  // A stopped printer is an error even when its only reason is a warning.
  assert.equal(statusPill(printer("stopped", [{ label: "Ink low", level: "warning" }])).level, "error");
  assert.deepEqual(statusPill({ printer: null, error: "CUPS unavailable" }), { text: "Status unavailable", level: "unknown" });
});

test("notification items render text literally and say their level in words", () => {
  const document = fakeDocument();
  const item = eventItem(
    { id: 4, at: "2026-10-28T10:00:00+00:00", level: "error", kind: "job", title: "<b>Job 7</b> aborted", detail: "from <i>phone</i>", job_id: 7, doc_id: null, read: false },
    document,
  );
  const text = textOf(item);
  assert.match(text, /<b>Job 7<\/b> aborted/);
  assert.match(text, /from <i>phone<\/i>/);
  assert.match(text, /Error/);
  assert.equal(find(item, (el) => el.tagName === "A").attributes.href, "/jobs.html#job-7");
});

test("notification items link scans and conversions to their document", () => {
  const document = fakeDocument();
  const link = (kind) => find(eventItem({ id: 1, at: "2026-10-28T10:00:00+00:00", level: "error", kind, title: "x", detail: "", job_id: null, doc_id: "d1", read: true }, document), (el) => el.tagName === "A").attributes.href;
  assert.equal(link("scan"), "/scan.html?doc=d1");
  assert.equal(link("conversion"), "/print.html?doc=d1");
});

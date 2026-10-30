// SPDX-License-Identifier: AGPL-3.0-only
import test from "node:test";
import assert from "node:assert/strict";
import { eventItem, mountFooter, silence, statusPill, when } from "./shell.js";
import { fakeDocument, textOf, find, findAll } from "./fake-dom.test-util.js";

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

test("each notification offers Silence for this alert or all of its kind, from 1 hour to 1 week", () => {
  const document = fakeDocument();
  const asked = [];
  const event = { id: 4, at: "2026-10-28T10:00:00+00:00", level: "error", kind: "job", title: "Printer <b>not</b> answering", detail: "", job_id: null, doc_id: null, read: true };
  const item = eventItem(event, document, (body) => asked.push(body));
  assert.deepEqual(findAll(item, (el) => el.attributes.role === "group").map((g) => g.attributes["aria-label"]), ["This alert", "All Print jobs"]);
  const buttons = findAll(item, (el) => el.tagName === "BUTTON");
  assert.deepEqual(buttons.map(textOf), ["1 hour", "8 hours", "1 day", "1 week", "1 hour", "8 hours", "1 day", "1 week"]);
  assert.equal(find(item, (el) => el.tagName === "SUMMARY").attributes["aria-label"], "Silence Printer <b>not</b> answering");
  for (const button of buttons) button.listeners.click();
  assert.deepEqual(asked, [
    ...[1, 8, 24, 168].map((hours) => ({ match: "title", value: "Printer <b>not</b> answering", hours })),
    ...[1, 8, 24, 168].map((hours) => ({ match: "kind", value: "job", hours })),
  ]);
});

test("a notification that links somewhere keeps its Silence menu outside the link", () => {
  const event = { id: 4, at: "2026-10-28T10:00:00+00:00", level: "info", kind: "scan", title: "x", detail: "", job_id: null, doc_id: "d1", read: false };
  const item = eventItem(event, fakeDocument(), () => {});
  const link = find(item, (el) => el.tagName === "A");
  assert.equal(link.attributes.href, "/scan.html?doc=d1");
  assert.equal(find(link, (el) => el.tagName === "BUTTON"), null);
  assert.ok(find(item, (el) => el.tagName === "BUTTON"));
});

// A browser lets a click bubble from its target up through every ancestor.
function click(root, target) {
  const chain = [];
  const visit = (el) => {
    if (typeof el === "string") return false;
    chain.push(el);
    if (el === target || el.children.some(visit)) return true;
    chain.pop();
    return false;
  };
  visit(root);
  for (const el of chain.reverse()) el.listeners.click?.();
}

test("opening a notification marks it read, but opening or using its Silence menu does not", () => {
  for (const doc_id of ["d1", null]) {
    const opened = [];
    const asked = [];
    const event = { id: 4, at: "2026-10-28T10:00:00+00:00", level: "info", kind: "scan", title: "x", detail: "", job_id: null, doc_id, read: false };
    const item = eventItem(event, fakeDocument(), (body) => asked.push(body), () => opened.push(event.id));

    click(item, find(item, (el) => el.tagName === "SUMMARY"));
    click(item, find(item, (el) => el.tagName === "BUTTON"));
    assert.deepEqual([opened, asked.length], [[], 1], `doc_id ${doc_id}`);

    click(item, item.children[0]);
    assert.deepEqual(opened, [4], `doc_id ${doc_id}`);
  }
});

test("silencing posts the choice and says when it ends", async () => {
  const until = "2026-10-30T15:00:00+00:00";
  const sent = [];
  const real = globalThis.fetch;
  globalThis.fetch = async (url, init) => (sent.push({ url, init }), { ok: true, status: 201, json: async () => ({ id: "s1", match: "kind", value: "job", until }) });
  try {
    const message = await silence({ match: "kind", value: "job", hours: 8 });
    assert.equal(sent[0].url, "/api/notify/silences");
    assert.equal(sent[0].init.method, "POST");
    assert.deepEqual(JSON.parse(sent[0].init.body), { match: "kind", value: "job", hours: 8 });
    assert.equal(message, `Silenced until ${when(until)}`);
    globalThis.fetch = async () => ({ ok: false, status: 400, statusText: "Bad Request", json: async () => ({ error: "hours must be 1, 8, 24 or 168" }) });
    await assert.rejects(silence({ match: "kind", value: "job", hours: 2 }), /hours must be 1, 8, 24 or 168/);
  } finally {
    globalThis.fetch = real;
  }
});

test("every page gets one GitHub footer link, even without the header host", () => {
  const document = fakeDocument();
  document.body = document.createElement("body");
  mountFooter(document);
  const footers = findAll(document.body, (el) => el.tagName === "FOOTER");
  assert.equal(footers.length, 1);
  const link = find(footers[0], (el) => el.tagName === "A");
  assert.equal(link.attributes.href, "https://github.com/andrebrait/pi-printhub");
  assert.equal(link.attributes.target, "_blank");
  assert.equal(link.attributes.rel, "noopener");
  assert.equal(link.dataset.icon, "github");
  assert.equal(textOf(footers[0]), "Print Hub is open source on GitHub");
});

// SPDX-License-Identifier: AGPL-3.0-only
import test from "node:test";
import assert from "node:assert/strict";
import { jobItem } from "./jobs.js";
import { fakeDocument, textOf, find } from "./fake-dom.test-util.js";

const job = (fields) => ({
  id: 12, name: "boarding-pass.pdf", user: "andre", host: "iphone.lan", state: "processing", reasons: [],
  reason_labels: [], created: "2026-10-28T10:00:00+00:00", completed: null, sheets: 1, doc_id: null, ...fields,
});

test("an active job from another device shows what, who, its state and a Cancel button", () => {
  const item = jobItem(job({ reason_labels: ["Ink low"] }), fakeDocument(), () => {});
  const text = textOf(item);
  assert.match(text, /boarding-pass\.pdf/);
  assert.match(text, /andre@iphone\.lan/);
  assert.match(text, /Printing/);
  assert.match(text, /Ink low/);
  assert.equal(item.attributes.id, "job-12");
  assert.ok(find(item, (el) => el.tagName === "BUTTON" && el.textContent === "Cancel"));
  assert.equal(find(item, (el) => el.tagName === "A"), null);
});

test("a finished web-app job has no Cancel and links to its document", () => {
  const item = jobItem(job({ state: "aborted", doc_id: "d1", completed: "2026-10-28T10:05:00+00:00" }), fakeDocument(), () => {});
  assert.match(textOf(item), /Failed/);
  assert.equal(find(item, (el) => el.tagName === "BUTTON"), null);
  assert.equal(find(item, (el) => el.tagName === "A").attributes.href, "/print.html?doc=d1");
});

test("job names are text, and a hidden name still reads as something", () => {
  const document = fakeDocument();
  assert.match(textOf(jobItem(job({ name: "<script>x</script>" }), document, () => {})), /<script>x<\/script>/);
  assert.match(textOf(jobItem(job({ name: "", user: "", host: "" }), document, () => {})), /Job 12/);
});

test("Cancel calls back with the job id", () => {
  let cancelled;
  const item = jobItem(job({}), fakeDocument(), (id) => (cancelled = id));
  find(item, (el) => el.tagName === "BUTTON").listeners.click();
  assert.equal(cancelled, 12);
});

test("refreshing keeps the list (and focus) when nothing changed and scrolls to a linked job once", async () => {
  const doc = fakeDocument();
  const ids = {};
  for (const id of ["active-jobs", "recent-jobs", "no-active", "no-recent", "jobs-loading", "jobs-error"]) ids[id] = doc.createElement("div");
  ids["jobs-error"].hidden = true;
  doc.getElementById = (id) => ids[id] ?? find(ids["active-jobs"], (el) => el.attributes.id === id) ?? find(ids["recent-jobs"], (el) => el.attributes.id === id);
  let body = { active: [job({ id: 5 })], recent: [] };
  globalThis.document = doc;
  globalThis.location = { hash: "#job-5", pathname: "/jobs.html", search: "" };
  globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => structuredClone(body) });
  const { refresh } = await import("./jobs.js");
  try {
    await refresh();
    const first = ids["active-jobs"].children[0];
    await refresh();
    assert.equal(ids["active-jobs"].children[0], first, "unchanged jobs must not be rebuilt");
    body = { active: [job({ id: 5, sheets: 2 })], recent: [] };
    await refresh();
    assert.notEqual(ids["active-jobs"].children[0], first);
    assert.equal(first.scrolled + (ids["active-jobs"].children[0].scrolled ?? 0), 1, "scrolled to the linked job once");
    globalThis.location.hash = "#1x"; // not a valid CSS selector
    body = { active: [], recent: [] };
    await refresh();
    assert.equal(ids["jobs-error"].hidden, true);
  } finally {
    delete globalThis.document;
    delete globalThis.location;
    delete globalThis.fetch;
  }
});

test("the state badge is always the rightmost item, with buttons before it", () => {
  for (const fields of [{ state: "processing", doc_id: "d1" }, { state: "completed", doc_id: "d1" }, { state: "completed" }]) {
    const item = jobItem(job(fields), fakeDocument(), () => {});
    const side = item.children.at(-1);
    assert.match(side.children.at(-1).className, /\bbadge\b/, JSON.stringify(fields));
  }
});

// SPDX-License-Identifier: AGPL-3.0-only
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { isLoadableMedia, mediaLabel } from "./layout.js";

const source = (name) => readFileSync(new URL(name, import.meta.url), "utf8").replace(/^import .*;$/gm, "");
const element = () => ({ hidden: true, textContent: "", value: "", listeners: {}, options: [],
  addEventListener(type, fn) { this.listeners[type] = fn; },
  add(option) { this.options.push(option); }, replaceChildren() { this.options = []; }, contains: () => false,
});
const dom = () => {
  const els = new Map();
  return { getElementById(id) { if (!els.has(id)) els.set(id, element()); return els.get(id); } };
};

test("scan preview uses each page density, including rotated axes and crop", () => {
  const build = new Function("document", "location", "mediaLabel", "isLoadableMedia", `${source("print.js").replace(/main\(\);\s*$/, "")}
    return (value) => { doc = value; return buildPages(); };`)(dom(), { search: "" }, mediaLabel, isLoadableMedia);
  const pages = build({ kind: "scan", id: "x", scan_resolution: 150, pages: [
    { id: "a", rotation: 0, page_info: { width: 600, height: 300, dpi: [300, 300] } },
    { id: "b", rotation: 90, page_info: { width: 300, height: 600, dpi: [150, 300] }, crop: { w: 150, h: 300 } },
  ] });
  assert.deepEqual(pages.map((p) => p.pt), [[144, 72], [72, 72]]);
});

test("discovery failure exposes Retry and retries capabilities, not a scan", async () => {
  const document = dom();
  let calls = 0;
  const fetch = async (url) => {
    assert.equal(url, "/api/scanner");
    calls++;
    return calls === 1 ? { ok: false } : { ok: true, json: async () => ({ sources: ["flatbed"], resolutions: [150, 300], modes: ["color"] }) };
  };
  const load = new Function("document", "location", "apiFetch", "errorMessage", "Sortable", "Option",
    `${source("scan.js").replace(/main\(\);\s*$/, "")} return loadScanner;`)(
      document, { search: "" }, fetch, async () => "Scanner unavailable", { create: () => ({}) },
      class { constructor(text, value) { this.text = text; this.value = value; } },
    );
  await load();
  const retry = document.getElementById("scanner-retry");
  assert.equal(retry.hidden, false);
  await retry.listeners.click();
  assert.equal(calls, 2);
  assert.equal(retry.hidden, true);
  assert.equal(document.getElementById("settings").hidden, false);
  assert.equal(document.getElementById("scanner-status").textContent, "");
});

test("a saved scan opens for viewing and only Edit looks for the scanner", async () => {
  const document = dom();
  const calls = [];
  const fetch = async (url) => {
    calls.push(url);
    if (url === "/api/scanner") return { ok: true, json: async () => ({ sources: ["flatbed"], resolutions: [300], modes: ["color"] }) };
    if (url === "/api/recipients") return { ok: true, json: async () => [] };
    return { ok: true, json: async () => ({ id: "x", title: "Receipt", pages: [], scanning: false }) };
  };
  const main = new Function("document", "location", "apiFetch", "errorMessage", "Sortable", "Option",
    `${source("scan.js").replace(/main\(\);\s*$/, "")} return main;`)(
      document, { search: "?doc=x" }, fetch, async () => "", { create: () => ({ option() {} }) },
      class { constructor(text, value) { this.text = text; this.value = value; } },
    );
  main();
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(calls.includes("/api/scanner"), false);
  assert.equal(document.getElementById("scan-form").hidden, true);
  const toggle = document.getElementById("edit-toggle");
  assert.equal(toggle.hidden, false);
  toggle.listeners.click();
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(calls.includes("/api/scanner"), true);
  assert.equal(document.getElementById("scan-form").hidden, false);
  assert.equal(toggle.textContent, "Done");
});

test("an edited title is saved before an output link navigates", async () => {
  const document = dom();
  const order = [];
  let release;
  const fetch = async (url, opts) => {
    if (opts?.method === "PATCH") {
      order.push(`patch ${opts.body.title}`);
      await new Promise((r) => (release = r));
      return { ok: true, json: async () => ({ id: "x", title: opts.body.title, pages: [], scanning: false }) };
    }
    return { ok: false };
  };
  const location = { search: "?doc=x", href: "", assign(url) { order.push(`go ${url}`); } };
  const env = new Function("document", "location", "apiFetch", "errorMessage", "Sortable", "Option",
    `${source("scan.js").replace(/main\(\);\s*$/, "")} return { setDoc: (d) => { doc = d; } };`)(
      document, location, fetch, async () => "", { create: () => ({ option() {} }) }, class {},
    );
  env.setDoc({ id: "x", title: "Old", pages: [], scanning: false });
  document.getElementById("title").value = "New name";
  const link = document.getElementById("download-pdf");
  link.href = "/api/docs/x/export?format=pdf";
  let prevented = false;
  const clicked = link.listeners.click({ preventDefault() { prevented = true; }, currentTarget: link });
  assert.equal(prevented, true);
  await new Promise((r) => setTimeout(r, 0));
  assert.deepEqual(order, ["patch New name"]); // nothing navigates while the rename is in flight
  release();
  await clicked;
  assert.deepEqual(order, ["patch New name", "go /api/docs/x/export?format=pdf"]);
});

test("a failed rename keeps the page and shows the error instead of following the link", async () => {
  const document = dom();
  const went = [];
  const fetch = async () => ({ ok: false });
  const location = { search: "?doc=x", href: "", assign(url) { went.push(url); } };
  const env = new Function("document", "location", "apiFetch", "errorMessage", "Sortable", "Option",
    `${source("scan.js").replace(/main\(\);\s*$/, "")} return { setDoc: (d) => { doc = d; } };`)(
      document, location, fetch, async () => "Title too long", { create: () => ({ option() {} }) }, class {},
    );
  env.setDoc({ id: "x", title: "Old", pages: [], scanning: false });
  document.getElementById("title").value = "New name";
  const link = document.getElementById("print");
  link.href = "/print.html?doc=x";
  await link.listeners.click({ preventDefault() {}, currentTarget: link });
  assert.deepEqual(went, []);
  assert.equal(document.getElementById("edit-error").textContent, "Title too long");
  assert.equal(document.getElementById("edit-error").hidden, false);
});

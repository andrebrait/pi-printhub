// SPDX-License-Identifier: AGPL-3.0-only
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { mediaLabel } from "./layout.js";
import { reasonLabels } from "./api.js";

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
  const build = new Function("document", "location", "mediaLabel", `${source("print.js").replace(/main\(\);\s*$/, "")}
    return (value) => { doc = value; return buildPages(); };`)(dom(), { search: "" }, mediaLabel);
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

test("one printer banner updater serves all authenticated screens", async () => {
  for (const page of ["index", "print", "scan"]) {
    const html = readFileSync(new URL(`../${page}.html`, import.meta.url), "utf8");
    assert.match(html, /src="\/js\/printer-banner.js"/);
    assert.match(html, /id="printer-banner"/);
  }
  const document = dom();
  let refresh;
  let state = { state: "stopped", state_reasons: ["offline"] };
  new Function("document", "apiFetch", "errorMessage", "reasonLabels", "setInterval", source("printer-banner.js"))(
    document, async () => ({ ok: true, json: async () => state }), async () => "unavailable", reasonLabels,
    (fn) => { refresh = fn; },
  );
  const banner = document.getElementById("printer-banner");
  await refresh();
  assert.equal(banner.hidden, false);
  assert.equal(banner.textContent, "Printer stopped: Offline");
  state = { state: "idle", state_reasons: ["marker-supply-low-warning", "media-jam-error"] };
  await refresh();
  assert.equal(banner.textContent, "Printer: Ink low, Paper jam");
  // CUPS bookkeeping while a job runs is not a printer problem.
  state = { state: "processing", state_reasons: ["cups-waiting-for-job-completed"] };
  await refresh();
  assert.equal(banner.hidden, true);
  state = { state: "idle", state_reasons: [] };
  await refresh();
  assert.equal(banner.hidden, true);
});

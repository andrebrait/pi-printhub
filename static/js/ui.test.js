// SPDX-License-Identifier: AGPL-3.0-only
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { mediaLabel } from "./layout.js";

const source = (name) => readFileSync(new URL(name, import.meta.url), "utf8").replace(/^import .*;$/gm, "");
const element = () => ({ hidden: true, textContent: "", value: "", listeners: {}, options: [],
  addEventListener(type, fn) { this.listeners[type] = fn; },
  add(option) { this.options.push(option); }, replaceChildren() { this.options = []; },
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

test("one printer banner updater serves all authenticated screens", async () => {
  for (const page of ["index", "print", "scan"]) {
    const html = readFileSync(new URL(`../${page}.html`, import.meta.url), "utf8");
    assert.match(html, /src="\/js\/printer-banner.js"/);
    assert.match(html, /id="printer-banner"/);
  }
  const document = dom();
  let refresh;
  let state = { state: "stopped", state_reasons: ["offline"] };
  new Function("document", "apiFetch", "errorMessage", "setInterval", source("printer-banner.js"))(
    document, async () => ({ ok: true, json: async () => state }), async () => "unavailable", (fn) => { refresh = fn; },
  );
  await refresh();
  assert.equal(document.getElementById("printer-banner").hidden, false);
  assert.match(document.getElementById("printer-banner").textContent, /offline/);
  state = { state: "idle", state_reasons: [] };
  await refresh();
  assert.equal(document.getElementById("printer-banner").hidden, true);
});

// SPDX-License-Identifier: AGPL-3.0-only
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { isLoadableMedia, mediaLabel } from "./layout.js";
import { snapCrop } from "./crop.js";
import { fakeDocument } from "./fake-dom.test-util.js";

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

test("printing shows a toast linking to the job and adds nothing under the options", async () => {
  const document = dom();
  const toasts = [];
  const created = [];
  document.createElement = (tag) => (created.push(tag), element());
  const env = new Function("document", "location", "apiFetch", "errorMessage", "showToast", "mediaLabel", "isLoadableMedia",
    `${source("print.js").replace(/main\(\);\s*$/, "")}
    return { print, setPrinter: (p) => { printer = p; } };`)(
      document, { search: "?doc=x" }, async () => ({ ok: true, json: async () => ({ job_id: 42 }) }), async () => "",
      (message, opts) => toasts.push({ message, ...opts }), mediaLabel, isLoadableMedia,
    );
  env.setPrinter({ defaults: {} });
  await env.print({ preventDefault() {} });
  assert.deepEqual(toasts, [{ message: "Job 42 sent to the printer", href: "/jobs.html#job-42" }]);
  assert.deepEqual(created, []);
  assert.equal(document.getElementById("print-error").hidden, true);
});

test("email waits for page edits still saving, and is not sent when one fails", async () => {
  for (const editOk of [true, false]) {
    const document = dom();
    document.createElement = fakeDocument().createElement; // a failed edit redraws the pages
    const order = [];
    let release;
    const fetch = async (url, opts) => {
      if (opts?.method === "PATCH") {
        order.push("patch");
        await new Promise((r) => (release = r));
        return editOk ? { ok: true, json: async () => ({ id: "x", title: "Scan", pages: [], scanning: false }) } : { ok: false };
      }
      if (url.endsWith("/email")) order.push("email");
      return { ok: true, json: async () => [] };
    };
    const env = new Function("document", "location", "apiFetch", "errorMessage", "Sortable", "Option", "Image",
      `${source("scan.js").replace(/main\(\);\s*$/, "")} return { setDoc: (d) => { doc = d; }, rotate };`)(
        document, { search: "?doc=x" }, fetch, async () => "Page edit failed", { create: () => ({ option() {} }) }, class {}, class {},
      );
    const page = { id: "p1", rotation: 0, crop: null, page_info: { width: 10, height: 20, mcu: 8 } };
    env.setDoc({ id: "x", title: "Scan", pages: [page], scanning: false });
    document.getElementById("title").value = "Scan";
    document.getElementById("to").value = "a@example.com";
    env.rotate("p1", 90);
    const sent = document.getElementById("email").listeners.submit({ preventDefault() {}, target: { elements: { format: { value: "pdf" } } } });
    await new Promise((r) => setTimeout(r, 0));
    assert.deepEqual(order, ["patch"], "nothing is emailed while the rotation is saving");
    release();
    await sent;
    if (editOk) assert.deepEqual(order, ["patch", "email"]);
    else {
      assert.deepEqual(order, ["patch"]);
      assert.match(document.getElementById("email-status").textContent, /not sent/i);
    }
  }
});


test("print separates advanced options without changing supported values or defaults", () => {
  const document = fakeDocument();
  const create = document.createElement;
  document.createElement = (tag) => {
    const el = create(tag);
    if (tag === "select") el.add = (option) => el.append(option);
    return el;
  };
  const hosts = new Map();
  document.getElementById = (id) => {
    if (!hosts.has(id)) hosts.set(id, create("div"));
    return hosts.get(id);
  };
  const build = new Function("document", "location", "mediaLabel", "isLoadableMedia", "Option",
    `${source("print.js").replace(/main\(\);\s*$/, "")}
      return (p) => { printer = p; doc = { kind: "print" }; buildControls(); return controls; };`)(
      document, { search: "" }, mediaLabel, isLoadableMedia,
      class { constructor(text, value) { this.textContent = text; this.value = value; } },
    );
  const supported = {
    media: ["iso_a4_210x297mm"], "print-quality": ["3", "4", "5"], "print-color-mode": ["color", "monochrome"],
    "orientation-requested": ["3", "4"], sides: ["one-sided", "two-sided-long-edge"], "print-scaling": ["fit", "none"],
  };
  const defaults = {
    media: "iso_a4_210x297mm", "print-quality": "5", "print-color-mode": "monochrome",
    "orientation-requested": "4", sides: "two-sided-long-edge", "print-scaling": "none",
  };
  const controls = build({ supported, defaults });
  const labels = (id) => hosts.get(id).children.map((field) => field.children[0].textContent);
  assert.deepEqual(labels("controls"), ["Paper", "Color", "Sides"]);
  assert.deepEqual(labels("advanced-controls"), ["Quality", "Orientation", "Scaling"]);
  for (const [key, value] of Object.entries(defaults)) {
    if (key === "orientation-requested") continue; // Auto, not a printer value; covered below
    assert.equal(controls[key].value, value);
    assert.deepEqual(controls[key].children.map((option) => option.value), supported[key]);
  }
  // Orientation starts at Auto (CUPS turns each page to fit) whatever the printer's default, then the printer's values.
  assert.equal(controls["orientation-requested"].value, "auto");
  assert.deepEqual(controls["orientation-requested"].children.map((option) => option.value), ["auto", "3", "4"]);
});


test("keyboard cropping moves and resizes within the image on its existing MCU grid", () => {
  const document = dom();
  let data = { x: 0, y: 0, width: 640, height: 480 };
  const cropper = { ready: true, getData: () => ({ ...data }), setData: (next) => { data = { ...data, ...next }; } };
  const setCrop = new Function("document", "location", "Sortable", "snapCrop",
    `${source("scan.js").replace(/main\(\);\s*$/, "")}
      return (instance) => { cropper = instance; cropping = { page_info: { width: 640, height: 480, mcu: [16, 16] } }; };`)(
      document, { search: "" }, { create: () => ({ option() {} }) }, snapCrop,
    );
  setCrop(cropper);
  const keydown = document.getElementById("crop-stage").listeners.keydown;
  assert.equal(typeof keydown, "function");
  const press = (key, shiftKey = false) => keydown({ key, shiftKey, preventDefault() {} });
  press("ArrowLeft", true);
  press("ArrowUp", true);
  assert.deepEqual(data, { x: 0, y: 0, width: 624, height: 464 });
  press("ArrowRight"); press("ArrowDown"); press("ArrowRight");
  assert.deepEqual(data, { x: 16, y: 16, width: 624, height: 464 });
  press("ArrowLeft"); press("ArrowRight", true);
  assert.deepEqual(data, { x: 0, y: 16, width: 640, height: 464 });
  assert.match(document.getElementById("crop-status").textContent, /640 by 464.*left 0.*top 16/);
});

test("keyboard cropping waits for the cropper, keeps at least one block and reports what Cropper applied", () => {
  const document = dom();
  let data = { x: 0, y: 0, width: 32, height: 32 };
  let ready = false;
  // Like Cropper with a locked ratio: the height follows the width, whatever was asked.
  const cropper = {
    get ready() { return ready; },
    getData: () => ({ ...data }),
    setData: (next) => { data = { ...data, ...next, height: next.width ?? data.width }; },
  };
  const setCrop = new Function("document", "location", "Sortable", "snapCrop",
    `${source("scan.js").replace(/main\(\);\s*$/, "")}
      return (instance) => { cropper = instance; cropping = { page_info: { width: 640, height: 480, mcu: [16, 16] } }; };`)(
      document, { search: "" }, { create: () => ({ option() {} }) }, snapCrop,
    );
  setCrop(cropper);
  const press = (key, shiftKey = false) => document.getElementById("crop-stage").listeners.keydown({ key, shiftKey, preventDefault() {} });
  press("ArrowRight");
  assert.deepEqual(data, { x: 0, y: 0, width: 32, height: 32 }, "no change before Cropper is ready");
  ready = true;
  press("ArrowLeft", true);
  press("ArrowLeft", true);
  press("ArrowLeft", true);
  assert.equal(data.width, 16, "never smaller than one block");
  press("ArrowDown", true); // the locked ratio ignores a height-only change
  assert.match(document.getElementById("crop-status").textContent, /16 by 16/);
});

function scanEnv({ editOk }) {
  const document = dom();
  document.createElement = fakeDocument().createElement; // a failed edit redraws the pages
  const order = [];
  let release;
  const fetch = async (url, opts) => {
    if (opts?.method === "PATCH") {
      order.push("patch");
      await new Promise((r) => (release = r));
      return editOk ? { ok: true, json: async () => ({ id: "x", title: "Scan", pages: [], scanning: false }) } : { ok: false };
    }
    if (url === "/api/scans") {
      order.push("scan");
      return { ok: true, json: async () => ({ doc_id: "x" }) };
    }
    return { ok: true, json: async () => ({ id: "x", title: "Scan", pages: [], scanning: false }) };
  };
  const location = { search: "?doc=x", assign(url) { order.push(`go ${url}`); } };
  const env = new Function("document", "location", "apiFetch", "errorMessage", "Sortable", "Option", "Image", "setTimeout",
    `${source("scan.js").replace(/main\(\);\s*$/, "")} return { setDoc: (d) => { doc = d; }, rotate, scan };`)(
      document, location, fetch, async () => "Page edit failed", { create: () => ({ option() {} }) }, class {}, class {}, () => {},
    );
  const page = { id: "p1", rotation: 0, crop: null, page_info: { width: 10, height: 20, mcu: 8 } };
  env.setDoc({ id: "x", title: "Scan", pages: [page], scanning: false });
  document.getElementById("title").value = "Scan"; // unchanged title: only the page edit is pending
  return { document, order, env, release: () => release() };
}

test("an output link does not follow when a queued page edit fails", async () => {
  const { document, order, env, release } = scanEnv({ editOk: false });
  env.rotate("p1", 90);
  const link = document.getElementById("print");
  link.href = "/print.html?doc=x";
  const clicked = link.listeners.click({ preventDefault() {}, currentTarget: link });
  await new Promise((r) => setTimeout(r, 0));
  release();
  await clicked;
  assert.deepEqual(order, ["patch"]);
});

test("another scan waits for queued page edits and does not start when one fails", async () => {
  for (const editOk of [true, false]) {
    const { document, order, env, release } = scanEnv({ editOk });
    env.rotate("p1", 90);
    const started = env.scan("flatbed");
    await new Promise((r) => setTimeout(r, 0));
    assert.deepEqual(order, ["patch"], "the scan waits for the edit");
    release();
    await started;
    if (editOk) assert.deepEqual(order, ["patch", "scan"]);
    else {
      assert.deepEqual(order, ["patch"]);
      assert.match(document.getElementById("scan-error").textContent, /not scanned/i);
    }
  }
});

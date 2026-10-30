// SPDX-License-Identifier: AGPL-3.0-only
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { isLoadableMedia, mediaLabel, mediaSizeMm, sheetLayout } from "./layout.js";
import { snapCrop } from "./crop.js";
import { fakeDocument } from "./fake-dom.test-util.js";

const source = (name) => readFileSync(new URL(name, import.meta.url), "utf8").replace(/^import .*;$/gm, "");
const element = () => ({ hidden: true, textContent: "", value: "", listeners: {}, options: [], dataset: {}, attributes: {},
  addEventListener(type, fn) { this.listeners[type] = fn; }, setAttribute(name, value) { this.attributes[name] = value; },
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


test("the preview turns a landscape page on Auto and keeps it upright on Portrait", () => {
  const document = fakeDocument();
  const preview = new Function("document", "location", "mediaLabel", "isLoadableMedia", "mediaSizeMm", "sheetLayout", "Image",
    `${source("print.js").replace(/main\(\);\s*$/, "")}
      return (orientation) => {
        printer = { defaults: {}, margins: {} };
        controls["orientation-requested"] = { value: orientation };
        pages = [{ pt: [842, 595], url: "p.png" }];
        return sheetEl(1, false);
      };`)(document, { search: "" }, mediaLabel, isLoadableMedia, mediaSizeMm, sheetLayout, class {});
  const frame = (sheet) => sheet.children[0].children[0].children[0].className; // sheet > printable > page > frame
  assert.equal(frame(preview("auto")), "turn");
  assert.equal(frame(preview("3")), "upright");
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

const SETTINGS = { retention_days: 30, min_free_gb: 2, smtp_host: "smtp.example", smtp_port: 587, smtp_security: "starttls",
  smtp_user: "me", mail_from: "hub@example", smtp_password_set: true };
const reply = (body, ok = true) => ({ ok, json: async () => body });

// A page whose GET /api/settings/system answers come from the list, in order (the last one repeats); timers fire when the test says so.
function settingsEnv(systems) {
  const document = dom();
  const calls = [];
  const timers = [];
  const apiFetch = async (url, opts = {}) => {
    calls.push({ url, method: opts.method ?? "GET", body: opts.body });
    if (url === "/api/settings/system" && opts.method !== "PUT") {
      const next = systems.length > 1 ? systems.shift() : systems[0];
      return next instanceof Error ? reply(null, false) : reply(next);
    }
    if (url === "/api/settings/system") return reply(pending); // 202: the request is written, not applied
    return reply(SETTINGS);
  };
  const { main } = new Function("document", "apiFetch", "errorMessage", "setTimeout",
    `${source("settings.js").replace(/main\(\);\s*$/, "")} return { main };`)(
    document, apiFetch, async () => "Rejected", (fn) => timers.push(fn));
  const tick = async () => { // let pending promises settle, then fire the timers that were waiting
    await new Promise((r) => setImmediate(r));
    timers.splice(0).forEach((fn) => fn());
    await new Promise((r) => setImmediate(r));
  };
  return { document, calls, main, tick };
}
const applied = { requested: { share: true, expose_usb: true }, status: { state: "applied", share: true, expose_usb: true, error: null }, pending: false };
const pending = { requested: { share: false, expose_usb: true }, status: null, pending: true };

test("settings fills the form without the stored password and keeps it when the field is left blank", async () => {
  const { document, calls, main } = settingsEnv([applied]);
  await main();
  assert.equal(document.getElementById("retention_days").value, 30);
  assert.equal(document.getElementById("smtp_host").value, "smtp.example");
  assert.equal(document.getElementById("system-msg").textContent, "", "an old applied status is not announced on load");
  assert.equal(document.getElementById("smtp_password").value, "");
  assert.match(document.getElementById("smtp_password").placeholder, /saved/i);
  await document.getElementById("email-form").listeners.submit({ preventDefault() {} });
  const put = calls.find((c) => c.method === "PUT" && c.url === "/api/settings");
  assert.equal(put.body.smtp_port, 587); // a number: the server rejects "587"
  assert.equal(put.body.smtp_host, "smtp.example");
  assert.equal("smtp_password" in put.body, false);
  document.getElementById("smtp_password").value = "hunter22";
  await document.getElementById("email-form").listeners.submit({ preventDefault() {} });
  assert.equal(calls.filter((c) => c.method === "PUT").at(-1).body.smtp_password, "hunter22");
});

test("a sharing change shows Applying until the status catches up, then the applied state", async () => {
  const { document, main, tick } = settingsEnv([applied, pending, { ...applied, requested: pending.requested }]);
  await main();
  const msg = document.getElementById("system-msg");
  document.getElementById("share").checked = false;
  const saved = document.getElementById("system-form").listeners.submit({ preventDefault() {} });
  await new Promise((r) => setImmediate(r));
  assert.match(msg.textContent, /Applying/);
  await tick();
  assert.match(msg.textContent, /Applying/);
  await tick();
  await saved;
  assert.match(msg.textContent, /Applied/);
  assert.equal(document.getElementById("system-save").disabled, false);
});

test("a sharing change that never applies says so after 30 seconds", async () => {
  const { document, main, tick } = settingsEnv([applied, pending]);
  await main();
  const msg = document.getElementById("system-msg");
  const saved = document.getElementById("system-form").listeners.submit({ preventDefault() {} });
  for (let i = 0; i < 14; i++) await tick();
  assert.match(msg.textContent, /Applying/, "still waiting before 30 seconds");
  await tick();
  await saved;
  assert.match(msg.textContent, /Not applied yet: is printhub-apply\.path running\? Re-run the installer\./);
});

test("a failed apply restores the switches to what is in force", async () => {
  const failed = { requested: { share: false, expose_usb: false },
    status: { state: "failed", share: true, expose_usb: false, error: "cupsctl failed" }, pending: false };
  const { document, main, tick } = settingsEnv([applied, failed]);
  await main();
  document.getElementById("share").checked = false;
  const saved = document.getElementById("system-form").listeners.submit({ preventDefault() {} });
  await tick();
  await saved;
  assert.equal(document.getElementById("share").checked, true);
  assert.equal(document.getElementById("expose_usb").checked, false);
  assert.match(document.getElementById("system-msg").textContent, /cupsctl failed/);
});

test("a failed status poll does not end the wait", async () => {
  const { document, main, tick } = settingsEnv([applied, new Error("down"), { ...applied, requested: pending.requested }]);
  await main();
  const msg = document.getElementById("system-msg");
  const saved = document.getElementById("system-form").listeners.submit({ preventDefault() {} });
  await new Promise((r) => setImmediate(r));
  await tick(); // the poll fails
  assert.match(msg.textContent, /Applying/);
  await tick(); // the next one succeeds
  await saved;
  assert.match(msg.textContent, /Applied/);
});

test("status polls that keep failing end with the not-applied message and the last error", async () => {
  const { document, main, tick } = settingsEnv([applied, new Error("down")]);
  await main();
  const msg = document.getElementById("system-msg");
  const saved = document.getElementById("system-form").listeners.submit({ preventDefault() {} });
  for (let i = 0; i < 14; i++) await tick();
  assert.match(msg.textContent, /Applying/, "still waiting before 30 seconds");
  await tick();
  await saved;
  assert.match(msg.textContent, /Not applied yet: is printhub-apply\.path running\?/);
  assert.match(msg.textContent, /Rejected/);
});

test("changing the SMTP server without typing the password again says so inline and sends nothing", async () => {
  const { document, calls, main } = settingsEnv([applied]);
  await main();
  const sent = () => calls.filter((c) => c.method === "PUT" && c.url === "/api/settings");
  for (const [field, value] of [["smtp_host", "smtp.other"], ["smtp_port", "465"], ["smtp_user", "you"], ["smtp_security", "tls"]]) {
    document.getElementById(field).value = value;
    await document.getElementById("email-form").listeners.submit({ preventDefault() {} });
    assert.equal(sent().length, 0, field);
    assert.equal(document.getElementById("email-msg").textContent, "Enter the SMTP password again to change the server settings");
    assert.equal(document.getElementById("email-save").disabled, false);
    document.getElementById(field).value = SETTINGS[field];
  }
  document.getElementById("smtp_host").value = "smtp.other";
  document.getElementById("smtp_password").value = "hunter22";
  await document.getElementById("email-form").listeners.submit({ preventDefault() {} });
  assert.equal(sent().length, 1);
  assert.equal(sent()[0].body.smtp_host, "smtp.other");
});

test("changing only the sender needs no SMTP password", async () => {
  const { document, calls, main } = settingsEnv([applied]);
  await main();
  document.getElementById("mail_from").value = "other@example";
  await document.getElementById("email-form").listeners.submit({ preventDefault() {} });
  assert.equal(calls.filter((c) => c.method === "PUT" && c.url === "/api/settings").length, 1);
  assert.match(document.getElementById("email-msg").textContent, /Saved/);
});

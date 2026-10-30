// SPDX-License-Identifier: AGPL-3.0-only
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { isLoadableMedia, mediaLabel, mediaSizeMm, sheetLayout } from "./layout.js";
import { snapCrop } from "./crop.js";
import { fakeDocument, find, findAll, textOf } from "./fake-dom.test-util.js";
import { KIND_LABELS, when } from "./shell.js";

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
// Defaults per Telegram parse mode and email format; the markup around {title} makes each set tell itself apart.
const defaultsOf = (open, close, extra) => ({ ...extra, templates: Object.fromEntries(Object.keys(KIND_LABELS).map((k) => [k, `${k}: ${open}{title}${close}\n{detail}\n{link}`])) });
const DEFAULTS = {
  telegram: {
    MarkdownV2: defaultsOf("*", "*", { header: "🖨️ _*Print Hub*_" }),
    HTML: defaultsOf("<b>", "</b>", { header: "🖨️ <i><b>Print Hub</b></i>" }),
    Markdown: defaultsOf("*", "*", { header: "🖨️ *Print Hub*" }),
    None: defaultsOf("", "", { header: "🖨️ Print Hub" }),
  },
  email: { html: defaultsOf("<b>", "</b>", { subject_prefix: "[Print Hub]" }), text: defaultsOf("", "", { subject_prefix: "[Print Hub]" }) },
};
const V2 = DEFAULTS.telegram.MarkdownV2;

// A page whose GET /api/settings/system answers come from the list, in order (the last one repeats); timers fire when the test says so.
// notify = { channels, silences, confirm } seeds what the notification API lists and answers the delete prompt; like the server it
// never gives a bot token back. Every element is a fake one that keeps its children, so the tests can read what the page renders.
function settingsEnv(systems, failing = [], notify = {}) {
  const make = fakeDocument().createElement;
  const els = new Map();
  const document = { createElement: make, getElementById: (id) => els.get(id) ?? els.set(id, make("div")).get(id), addEventListener() {} };
  const channels = notify.channels ?? [], silences = notify.silences ?? [], confirms = [];
  const calls = [];
  const timers = [];
  const apiFetch = async (url, opts = {}) => {
    const method = opts.method ?? "GET";
    calls.push({ url, method, body: opts.body });
    if (url === "/api/settings/system" && method !== "PUT") {
      const next = systems.length > 1 ? systems.shift() : systems[0];
      return next instanceof Error ? reply(null, false) : reply(next);
    }
    if (url === "/api/settings/system") return reply(pending); // 202: the request is written, not applied
    const tested = url.match(/^\/api\/notify\/channels\/([^/]+)\/test$/);
    // Like the server, a test records its outcome as the channel's last result.
    if (tested) channels.find((c) => c.id === tested[1]).result = failing.includes(url) ? "Failed: Rejected" : "Delivered just now";
    if (url === "/api/notify/defaults") return reply(DEFAULTS);
    if (failing.includes(url)) return reply(null, false);
    if (url === "/api/notify/channels" && method === "POST") {
      const { bot_token, ...rest } = opts.body;
      channels.push({ id: "c-new", ...rest, bot_token_set: Boolean(bot_token) });
    }
    if (url === "/api/notify/channels") return reply(channels);
    if (url === "/api/notify/silences") return reply(silences);
    const gone = url.match(/^\/api\/notify\/(channels|silences)\/([^/]+)$/);
    if (gone && method === "DELETE") {
      const list = gone[1] === "channels" ? channels : silences;
      list.splice(list.findIndex((x) => x.id === gone[2]), 1);
    }
    return reply(SETTINGS);
  };
  const { main } = new Function("document", "apiFetch", "errorMessage", "setTimeout", "confirm", "KIND_LABELS", "when",
    `${source("settings.js").replace(/main\(\);\s*$/, "")} return { main };`)(
    document, apiFetch, async () => "Rejected", (fn) => timers.push(fn), (text) => (confirms.push(text), notify.confirm !== false), KIND_LABELS, when);
  const tick = async () => { // let pending promises settle, then fire the timers that were waiting
    await new Promise((r) => setImmediate(r));
    timers.splice(0).forEach((fn) => fn());
    await new Promise((r) => setImmediate(r));
  };
  return { document, calls, main, tick, confirms };
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

test("a page loaded during a pending sharing change shows the requested switches", async () => {
  const requested = { share: true, expose_usb: true };
  const { document, main, tick } = settingsEnv([{ requested, status: null, pending: true }]);
  main(); // the wait for the apply goes on in the background; tick fires its poll
  await tick();
  assert.equal(document.getElementById("share").checked, true);
  assert.equal(document.getElementById("expose_usb").checked, true);
});

test("a sharing change that never applies says so after 250 seconds", async () => {
  const { document, main, tick } = settingsEnv([applied, pending]);
  await main();
  const msg = document.getElementById("system-msg");
  const saved = document.getElementById("system-form").listeners.submit({ preventDefault() {} });
  for (let i = 0; i < 124; i++) await tick();
  assert.match(msg.textContent, /Applying/, "still waiting at the poll before 250 seconds");
  await tick();
  await saved;
  assert.match(msg.textContent, /Not applied after 4 minutes: is printhub-apply\.path running\? Re-run the installer\./);
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
  assert.equal(document.getElementById("system-msg").textContent, "Not applied yet: cupsctl failed");
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
  for (let i = 0; i < 124; i++) await tick();
  assert.match(msg.textContent, /Applying/, "still waiting before 4 minutes");
  await tick();
  await saved;
  assert.match(msg.textContent, /Not applied after 4 minutes: is printhub-apply\.path running\?/);
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

test("a test email says it is sending, then the result, next to its own button", async () => {
  for (const [failing, result] of [[[], "Test email sent to me@example.com"], [["/api/settings/test-email"], "Rejected"]]) {
    const { document, calls, main } = settingsEnv([applied], failing);
    await main();
    const button = document.getElementById("test-email"), line = document.getElementById("test-msg");
    document.getElementById("test-to").value = "me@example.com";
    const sending = document.getElementById("test-form").listeners.submit({ preventDefault() {} });
    assert.equal(button.disabled, true);
    assert.equal(button.textContent, "Sending…");
    assert.match(line.textContent, /Sending a test email to me@example\.com/);
    await sending;
    assert.equal(calls.at(-1).body.to, "me@example.com");
    assert.equal(line.textContent, result);
    assert.equal(line.attributes.role, failing.length ? "alert" : "status");
    assert.equal(document.getElementById("email-msg").textContent, "", "the Save line stays for saves");
    assert.equal(button.disabled, false);
    assert.equal(button.textContent, "Send test email");
  }
});

test("a sharing change that could not be applied says so as a plain warning, not the raw error", async () => {
  const failed = (error) => ({ requested: { share: true, expose_usb: true }, status: { state: "failed", share: false, expose_usb: true, error }, pending: false });
  for (const [error, plain] of [
    ["Job for printhub-printer.service failed because the control process exited.\nSee systemctl status", "the printer is not answering"],
    ["cupsctl: Bad request\nsecond line", "cupsctl: Bad request"],
    ["x".repeat(300), "x".repeat(200)],
  ]) {
    const { document, main } = settingsEnv([failed(error)]);
    await main();
    const line = document.getElementById("system-msg");
    assert.equal(line.textContent, `Not applied yet: ${plain}`);
    assert.equal(line.attributes.role, "status");
    assert.match(line.className, /alert-warning/);
  }
});

const KINDS = Object.keys(KIND_LABELS);
const PHONE = { id: "c1", name: "Phone", type: "telegram", enabled: true, kinds: KINDS, chat_id: "-1001", message_thread_id: null, parse_mode: "HTML",
  disable_notification: false, protect_content: false, disable_web_page_preview: false, bot_token_set: true, result: "Delivered Oct 30, 2026, 3:15 PM" };
const submit = (el) => el.listeners.submit({ preventDefault() {} });
const buttonIn = (root, text) => find(root, (e) => e.tagName === "BUTTON" && e.textContent === text);
const field = (document, key) => find(document.getElementById("channel-fields"), (e) => e.id === `ch-${key}`);
const kind = (document, key) => find(document.getElementById("channel-kinds"), (e) => e.id === `ch-kind-${key}`);
const shown = (document, key) => !findAll(document.getElementById("channel-fields"), (e) => e.children.includes(field(document, key)))[0].hidden;
const posts = (calls) => calls.filter((c) => c.method === "POST" && c.url === "/api/notify/channels");

test("each channel row shows its name, type, events and last result", async () => {
  const hook = { ...PHONE, id: "c2", name: "Hook", type: "webhook", kinds: ["job", "printer"], result: null };
  const { document, main } = settingsEnv([applied], [], { channels: [PHONE, hook] });
  await main();
  const [phone, other] = document.getElementById("channels").children.map(textOf);
  for (const part of ["Phone", "Telegram", "All events", "Delivered Oct 30, 2026, 3:15 PM"]) assert.ok(phone.includes(part), part);
  for (const part of ["Hook", "Webhook", "Print jobs, Printer status", "Nothing sent yet"]) assert.ok(other.includes(part), part);
  assert.equal(document.getElementById("channels-empty").hidden, true);
});

test("adding a Telegram channel sends its fields, and the token never comes back", async () => {
  const { document, calls, main } = settingsEnv([applied]);
  await main();
  document.getElementById("channel-add").listeners.click();
  document.getElementById("channel-name").value = "Phone";
  document.getElementById("channel-type").value = "telegram";
  document.getElementById("channel-type").listeners.change();
  const fields = textOf(document.getElementById("channel-fields"));
  for (const label of ["Bot token", "Chat ID", "Message thread ID", "Parse mode", "Send silently", "Protect content", "Disable link preview"]) {
    assert.ok(fields.includes(label), label);
  }
  assert.equal(field(document, "bot_token").type, "password");
  assert.equal(field(document, "parse_mode").value, "MarkdownV2");
  field(document, "bot_token").value = "123:abc";
  field(document, "chat_id").value = "-1001";
  field(document, "message_thread_id").value = "7";
  field(document, "protect_content").checked = true;
  kind(document, "settings").checked = false;
  await submit(document.getElementById("channel-form"));
  assert.deepEqual(posts(calls)[0].body, {
    name: "Phone", type: "telegram", enabled: true, kinds: ["job", "scan", "printer", "conversion"],
    bot_token: "123:abc", chat_id: "-1001", message_thread_id: 7, parse_mode: "MarkdownV2",
    disable_notification: false, protect_content: true, disable_web_page_preview: false,
    header: V2.header, templates: V2.templates,
  });
  assert.equal(document.getElementById("channel-form").hidden, true);
  const list = textOf(document.getElementById("channels"));
  assert.ok(list.includes("Phone"));
  assert.equal(list.includes("123:abc"), false);
});

test("an email channel uses the scan email server or its own, and sends only what applies", async () => {
  const { document, calls, main } = settingsEnv([applied]);
  await main();
  document.getElementById("channel-add").listeners.click(); // email is the first type
  assert.equal(field(document, "use_scan_server").checked, true);
  assert.equal(shown(document, "smtp_host"), false);
  document.getElementById("channel-name").value = "Mail";
  field(document, "to").value = "a@example.com, b@example.com";
  await submit(document.getElementById("channel-form"));
  assert.deepEqual(posts(calls)[0].body, {
    name: "Mail", type: "email", enabled: true, kinds: KINDS, to: ["a@example.com", "b@example.com"], use_scan_server: true,
    format: "html", subject_prefix: DEFAULTS.email.html.subject_prefix, templates: DEFAULTS.email.html.templates,
  });
  document.getElementById("channel-add").listeners.click();
  document.getElementById("channel-name").value = "Own";
  field(document, "to").value = "a@example.com";
  field(document, "use_scan_server").checked = false;
  field(document, "use_scan_server").listeners.change();
  assert.equal(shown(document, "smtp_host"), true);
  field(document, "smtp_host").value = "smtp.example";
  field(document, "mail_from").value = "Hub <hub@example.com>";
  await submit(document.getElementById("channel-form"));
  assert.deepEqual(posts(calls)[1].body, {
    name: "Own", type: "email", enabled: true, kinds: KINDS, to: ["a@example.com"], use_scan_server: false,
    smtp_host: "smtp.example", smtp_port: 587, smtp_security: "starttls", smtp_user: "", smtp_password: "", mail_from: "Hub <hub@example.com>",
    format: "html", subject_prefix: DEFAULTS.email.html.subject_prefix, templates: DEFAULTS.email.html.templates,
  });
});

test("editing a channel keeps its saved secret unless a new one is typed", async () => {
  const { document, calls, main } = settingsEnv([applied], [], { channels: [{ ...PHONE }] });
  await main();
  const edit = () => buttonIn(document.getElementById("channels"), "Edit").listeners.click();
  edit();
  assert.equal(document.getElementById("channel-type").value, "telegram");
  assert.equal(document.getElementById("channel-type").disabled, true, "the type of a saved channel stays");
  assert.equal(document.getElementById("channel-name").value, "Phone");
  assert.equal(field(document, "chat_id").value, "-1001");
  assert.equal(field(document, "bot_token").value, "");
  assert.match(field(document, "bot_token").placeholder, /saved/i);
  document.getElementById("channel-name").value = "Family";
  await submit(document.getElementById("channel-form"));
  const puts = () => calls.filter((c) => c.method === "PUT");
  assert.equal(puts()[0].url, "/api/notify/channels/c1");
  assert.equal(puts()[0].body.name, "Family");
  assert.equal("bot_token" in puts()[0].body, false);
  edit();
  field(document, "bot_token").value = "456:def";
  await submit(document.getElementById("channel-form"));
  assert.equal(puts()[1].body.bot_token, "456:def");
});

const advanced = (document) => document.getElementById("channel-advanced");
const option = (document, id) => find(advanced(document), (e) => e.id === id);
const chooseType = (document, type) => {
  document.getElementById("channel-type").value = type;
  document.getElementById("channel-type").listeners.change();
};

test("a new Telegram channel's Advanced options start from the server defaults; an edited template is saved; Reset refills", async () => {
  const { document, calls, main } = settingsEnv([applied]);
  await main();
  document.getElementById("channel-add").listeners.click();
  chooseType(document, "telegram");
  const text = textOf(advanced(document));
  for (const part of ["Advanced options", "Header", ...Object.values(KIND_LABELS), "Reset to defaults", "Placeholders: {title}, {detail}, {link}, {time}"]) {
    assert.ok(text.includes(part), part);
  }
  assert.ok(!find(advanced(document), (e) => e.tagName === "DETAILS").open, "closed by default");
  assert.equal(option(document, "ch-header").value, V2.header);
  for (const k of KINDS) assert.equal(option(document, `ch-template-${k}`).value, V2.templates[k]);
  assert.equal(option(document, "ch-template-job").rows, 3);
  assert.equal(option(document, "ch-template-job").maxLength, 1000);
  assert.equal(option(document, "ch-header").maxLength, 200);
  document.getElementById("channel-name").value = "Phone";
  field(document, "bot_token").value = "123:abc";
  field(document, "chat_id").value = "-1001";
  option(document, "ch-template-job").value = "<b>{title}</b>";
  await submit(document.getElementById("channel-form"));
  const { header, templates } = posts(calls)[0].body;
  assert.equal(header, V2.header);
  assert.deepEqual(templates, { ...V2.templates, job: "<b>{title}</b>" });

  document.getElementById("channel-add").listeners.click();
  chooseType(document, "telegram");
  option(document, "ch-header").value = "mine";
  option(document, "ch-template-scan").value = "x";
  buttonIn(advanced(document), "Reset to defaults").listeners.click();
  assert.equal(option(document, "ch-header").value, V2.header);
  assert.equal(option(document, "ch-template-scan").value, V2.templates.scan);
});

test("an email channel has a subject prefix and no parse mode sentence; a webhook has no Advanced options", async () => {
  const { document, calls, main } = settingsEnv([applied]);
  await main();
  document.getElementById("channel-add").listeners.click();
  const text = textOf(advanced(document));
  assert.ok(text.includes("Subject prefix") && text.includes("Placeholders: {title}, {detail}, {link}, {time}"));
  assert.equal(text.includes("parse mode"), false);
  assert.equal(option(document, "ch-subject_prefix").value, DEFAULTS.email.html.subject_prefix);
  chooseType(document, "webhook");
  assert.equal(advanced(document).children.length, 0);
  document.getElementById("channel-name").value = "Hook";
  field(document, "url").value = "https://ntfy.example.com/hub";
  await submit(document.getElementById("channel-form"));
  const body = posts(calls)[0].body;
  assert.equal("header" in body || "templates" in body || "subject_prefix" in body, false);
});

test("the email Format starts as HTML, is sent, and changes the help under the templates", async () => {
  const { document, calls, main } = settingsEnv([applied]);
  await main();
  document.getElementById("channel-add").listeners.click();
  const format = option(document, "ch-format");
  assert.equal(format.value, "html");
  assert.ok(textOf(advanced(document)).includes('Use HTML tags such as <b>bold</b>, <i>italic</i> and <a href="…">links</a>.'));
  assert.ok(textOf(advanced(document)).includes("Your text is sent as typed; the values of the placeholders are escaped."));
  format.value = "text";
  format.listeners.change();
  assert.equal(textOf(advanced(document)).includes("Use HTML tags") || textOf(advanced(document)).includes("are escaped"), false);
  assert.ok(textOf(advanced(document)).includes("Placeholders: {title}, {detail}, {link}, {time}"));
  document.getElementById("channel-name").value = "Mail";
  field(document, "to").value = "a@example.com";
  await submit(document.getElementById("channel-form"));
  assert.equal(posts(calls)[0].body.format, "text");
});

test("editing an email channel shows its stored format", async () => {
  const mail = { id: "m1", name: "Mail", type: "email", enabled: true, kinds: KINDS, to: ["a@example.com"], use_scan_server: true,
    format: "text", subject_prefix: "", templates: DEFAULTS.email.text.templates };
  const { document, main } = settingsEnv([applied], [], { channels: [mail] });
  await main();
  buttonIn(document.getElementById("channels"), "Edit").listeners.click();
  assert.equal(option(document, "ch-format").value, "text");
});

test("the Telegram help names the syntax of the chosen parse mode and follows a change", async () => {
  const { document, main } = settingsEnv([applied]);
  await main();
  document.getElementById("channel-add").listeners.click();
  chooseType(document, "telegram");
  const help = () => textOf(advanced(document));
  assert.ok(help().includes("MarkdownV2: *bold*"));
  for (const [mode, part] of [["MarkdownV2", "MarkdownV2: *bold*"], ["Markdown", "Markdown (legacy): *bold*"], ["", "Plain text: no formatting."], ["HTML", "HTML: <b>bold</b>"]]) {
    field(document, "parse_mode").value = mode;
    field(document, "parse_mode").listeners.change();
    assert.ok(help().includes(part), part);
    assert.ok(help().includes("Placeholders: {title}, {detail}, {link}, {time}"));
  }
  field(document, "parse_mode").value = "MarkdownV2";
  field(document, "parse_mode").listeners.change();
  assert.ok(help().includes("Two asterisks are not bold."));
  assert.ok(help().includes("Your text is sent as typed in the chosen parse mode; the values of the placeholders are escaped."));
  assert.equal(help().includes("HTML: <b>bold</b>"), false);
});

test("editing a channel shows its own header and templates", async () => {
  const mine = { ...PHONE, header: "*Printer*", templates: { ...DEFAULTS.telegram.HTML.templates, job: "_{title}_" } };
  const { document, calls, main } = settingsEnv([applied], [], { channels: [mine] });
  await main();
  buttonIn(document.getElementById("channels"), "Edit").listeners.click();
  assert.equal(option(document, "ch-header").value, "*Printer*");
  assert.equal(option(document, "ch-template-job").value, "_{title}_");
  await submit(document.getElementById("channel-form"));
  const put = calls.find((c) => c.method === "PUT");
  assert.equal(put.body.header, "*Printer*");
  assert.equal(put.body.templates.job, "_{title}_");
});

test("a refused channel save stays open with the reason", async () => {
  const { document, main } = settingsEnv([applied], ["/api/notify/channels/c1"], { channels: [{ ...PHONE }] });
  await main();
  buttonIn(document.getElementById("channels"), "Edit").listeners.click();
  await submit(document.getElementById("channel-form"));
  assert.equal(document.getElementById("channel-form").hidden, false);
  assert.equal(document.getElementById("channel-msg").textContent, "Rejected");
  assert.equal(document.getElementById("channel-msg").attributes.role, "alert");
  assert.equal(document.getElementById("channel-save").disabled, false);
});

test("deleting a channel asks first and then removes it from the list", async () => {
  for (const sure of [false, true]) {
    const { document, calls, main, confirms } = settingsEnv([applied], [], { channels: [{ ...PHONE }], confirm: sure });
    await main();
    await buttonIn(document.getElementById("channels"), "Delete").listeners.click();
    assert.match(confirms[0], /Phone/);
    assert.equal(calls.some((c) => c.method === "DELETE" && c.url === "/api/notify/channels/c1"), sure);
    assert.equal(textOf(document.getElementById("channels")).includes("Phone"), !sure);
    assert.equal(document.getElementById("channels-empty").hidden, !sure);
  }
});

test("the enabled switch saves at once and goes back when the server refuses", async () => {
  for (const failing of [[], ["/api/notify/channels/c1"]]) {
    const { document, calls, main } = settingsEnv([applied], failing, { channels: [{ ...PHONE }] });
    await main();
    const toggle = find(document.getElementById("channels"), (e) => e.type === "checkbox");
    assert.equal(toggle.checked, true);
    toggle.checked = false;
    await toggle.listeners.change();
    assert.deepEqual(calls.find((c) => c.method === "PUT").body, { enabled: false });
    assert.equal(toggle.checked, failing.length > 0);
    assert.equal(document.getElementById("channels-msg").textContent, failing.length ? "Rejected" : "");
  }
});

test("a channel's test says it is sending, then the result, under its own button, and the row shows the recorded result", async () => {
  for (const [failing, result, recorded] of [[[], "Test sent to Phone", "Delivered just now"], [["/api/notify/channels/c1/test"], "Rejected", "Failed: Rejected"]]) {
    const { document, calls, main } = settingsEnv([applied], failing, { channels: [{ ...PHONE }, { ...PHONE, id: "c2", name: "Other" }] });
    await main();
    const [row, other] = document.getElementById("channels").children;
    const button = buttonIn(row, "Send test"), line = row.children.at(-1);
    const sending = submit(find(row, (e) => e.tagName === "FORM"));
    assert.equal(button.disabled, true);
    assert.equal(button.textContent, "Sending…");
    assert.match(line.textContent, /Sending a test to Phone/);
    await sending;
    assert.deepEqual(calls.slice(-2).map((c) => [c.method, c.url]), [["POST", "/api/notify/channels/c1/test"], ["GET", "/api/notify/channels"]]);
    assert.equal(line.textContent, result);
    assert.equal(line.attributes.role, failing.length ? "alert" : "status");
    assert.equal(textOf(row.children[2]), recorded);
    assert.equal(textOf(other.children[2]), PHONE.result, "the other channel keeps its own result");
    assert.equal(document.getElementById("channels").children[0], row, "the row stays, so the focus stays on its button");
    assert.equal(other.children.at(-1).textContent, "", "the other channel's line stays empty");
    assert.equal(button.disabled, false);
    assert.equal(button.textContent, "Send test");
  }
});

test("active silences list what they match and until when, and Unsilence removes one", async () => {
  const until = "2026-10-30T15:00:00+00:00";
  const silences = [{ id: "s1", match: "kind", value: "job", until }, { id: "s2", match: "title", value: "Printer not answering over USB", until }];
  const { document, calls, main } = settingsEnv([applied], [], { silences });
  await main();
  const list = document.getElementById("silences");
  const text = textOf(list);
  for (const part of ["All Print jobs", "Printer not answering over USB", when(until)]) assert.ok(text.includes(part), part);
  assert.equal(document.getElementById("silences-empty").hidden, true);
  await buttonIn(list.children[0], "Unsilence").listeners.click();
  assert.deepEqual(calls.find((c) => c.method === "DELETE").url, "/api/notify/silences/s1");
  assert.equal(list.children.length, 1);
  await buttonIn(list.children[0], "Unsilence").listeners.click();
  assert.equal(list.children.length, 0);
  assert.equal(document.getElementById("silences-empty").hidden, false);
});

const modeOf = (document, mode) => {
  field(document, "parse_mode").value = mode;
  field(document, "parse_mode").listeners.change();
};
const templatesOf = (document) => Object.fromEntries(KINDS.map((k) => [k, option(document, `ch-template-${k}`).value]));

test("a new Telegram channel starts with MarkdownV2 and its formatted defaults", async () => {
  const { document, main } = settingsEnv([applied]);
  await main();
  document.getElementById("channel-add").listeners.click();
  chooseType(document, "telegram");
  assert.equal(field(document, "parse_mode").value, "MarkdownV2");
  assert.equal(option(document, "ch-header").value, "🖨️ _*Print Hub*_");
  assert.deepEqual(templatesOf(document), V2.templates);
});

test("changing the parse mode swaps the fields still at the old default and keeps an edited one", async () => {
  const { document, main } = settingsEnv([applied]);
  await main();
  document.getElementById("channel-add").listeners.click();
  chooseType(document, "telegram");
  option(document, "ch-template-job").value = "my {title}";
  modeOf(document, "HTML");
  assert.equal(option(document, "ch-header").value, DEFAULTS.telegram.HTML.header);
  assert.deepEqual(templatesOf(document), { ...DEFAULTS.telegram.HTML.templates, job: "my {title}" });
  option(document, "ch-header").value = "mine";
  modeOf(document, "");
  assert.equal(option(document, "ch-header").value, "mine");
  assert.deepEqual(templatesOf(document), { ...DEFAULTS.telegram.None.templates, job: "my {title}" });
  modeOf(document, "Markdown");
  assert.deepEqual(templatesOf(document), { ...DEFAULTS.telegram.Markdown.templates, job: "my {title}" });
});

test("Reset to defaults uses the current parse mode", async () => {
  const { document, main } = settingsEnv([applied]);
  await main();
  document.getElementById("channel-add").listeners.click();
  chooseType(document, "telegram");
  modeOf(document, "HTML");
  option(document, "ch-header").value = "mine";
  option(document, "ch-template-scan").value = "x";
  buttonIn(advanced(document), "Reset to defaults").listeners.click();
  assert.equal(field(document, "parse_mode").value, "HTML");
  assert.equal(option(document, "ch-header").value, DEFAULTS.telegram.HTML.header);
  assert.deepEqual(templatesOf(document), DEFAULTS.telegram.HTML.templates);
});

test("editing a stored channel keeps its text and swaps only what equals its mode's default", async () => {
  const stored = { ...PHONE, header: "*Printer*", templates: { ...DEFAULTS.telegram.HTML.templates, job: "_{title}_" } };
  const { document, main } = settingsEnv([applied], [], { channels: [stored] });
  await main();
  buttonIn(document.getElementById("channels"), "Edit").listeners.click();
  modeOf(document, "MarkdownV2");
  assert.equal(option(document, "ch-header").value, "*Printer*");
  assert.deepEqual(templatesOf(document), { ...V2.templates, job: "_{title}_" });
});

test("the email Format switch swaps untouched fields, keeps edited ones, and Reset uses the current format", async () => {
  const { document, main } = settingsEnv([applied]);
  await main();
  document.getElementById("channel-add").listeners.click();
  const format = option(document, "ch-format");
  option(document, "ch-template-scan").value = "mine";
  format.value = "text";
  format.listeners.change();
  assert.deepEqual(templatesOf(document), { ...DEFAULTS.email.text.templates, scan: "mine" });
  buttonIn(advanced(document), "Reset to defaults").listeners.click();
  assert.equal(format.value, "text");
  assert.deepEqual(templatesOf(document), DEFAULTS.email.text.templates);
  format.value = "html";
  format.listeners.change();
  assert.deepEqual(templatesOf(document), DEFAULTS.email.html.templates);
});

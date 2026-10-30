// SPDX-License-Identifier: AGPL-3.0-only
import test from "node:test";
import assert from "node:assert/strict";
import { STATUS_EVENT, eventItem, loadStatus, mountFooter, printerState, printerWidget, showTip, silence, themeButton, when } from "./shell.js";
import { fakeDocument, textOf, find, findAll } from "./fake-dom.test-util.js";

// What /api/status reports for the HP OfficeJet Pro 6970.
const REAL = {
  name: "HP OfficeJet Pro 6970", model: "HP OfficeJet Pro 6970", state: "idle", problems: [], web_page: "", usb_answering: null,
  supplies: [
    { name: "Magenta ink", type: "ink-cartridge", color: "#FF00FF", level: 60, low: false },
    { name: "Cyan ink", type: "ink-cartridge", color: "#00FFFF", level: 20, low: true },
    { name: "Yellow ink", type: "ink-cartridge", color: "#FFFF00", level: 60, low: false },
    { name: "Black ink", type: "ink-cartridge", color: "#000000", level: 30, low: false },
  ],
};
const REAL_TEXT = "HP OfficeJet Pro 6970: Ready · Magenta ink 60% · Cyan ink 20% (low) · Yellow ink 60% · Black ink 30%";
const LASER = {
  ...REAL, name: "Office laser", model: "Laser 4000",
  supplies: [
    { name: "Black toner", type: "toner", color: "#000000", level: 8, low: true },
    { name: "Imaging drum", type: "opc", color: "", level: 95, low: false },
    { name: "Waste toner box", type: "waste-toner", color: "", level: null, low: false },
  ],
};
const widget = (entry, showTip) => {
  const w = printerWidget(fakeDocument(), showTip);
  w.update(entry ? { printers: [entry] } : { printers: undefined, error: "Failed to fetch" });
  return w;
};
const barsOf = (w) => findAll(w.link, (e) => e.className.split(" ").includes("supply-bar"));
const iconOf = (w) => find(w.link, (e) => e.dataset.icon).dataset.icon;

test("printer state picks the word, icon and level from state, problems and the USB check", () => {
  const state = (over) => printerState({ ...REAL, ...over });
  const warning = { label: "Ink low", level: "warning" };
  const jam = { label: "Paper jam", level: "error" };
  assert.deepEqual(state({}), { word: "Ready", icon: "check_circle", level: "ok" });
  assert.deepEqual(state({ usb_answering: true }), { word: "Ready", icon: "check_circle", level: "ok" });
  assert.deepEqual(state({ state: "processing" }), { word: "Printing", icon: "print", level: "busy" });
  assert.deepEqual(state({ state: "stopped" }), { word: "Stopped", icon: "error", level: "error" });
  assert.deepEqual(state({ problems: [warning] }), { word: "Ready", icon: "warning", level: "warning" });
  assert.deepEqual(state({ state: "processing", problems: [warning, jam] }), { word: "Printing", icon: "error", level: "error" });
  assert.equal(state({ state: "stopped", problems: [warning] }).level, "error");
  assert.deepEqual(state({ state: "unreachable", supplies: [] }), { word: "Unreachable", icon: "link_off", level: "error" });
  assert.deepEqual(state({ usb_answering: false }), { word: "Not answering over USB", icon: "link_off", level: "error" });
});

test("the header widget draws a bar per supply, a status icon and the full text as its name", () => {
  const w = widget(REAL);
  assert.equal(w.link.attributes.href, "/jobs.html");
  assert.equal(w.link.attributes["aria-label"], REAL_TEXT);
  assert.equal(w.link.attributes.title, REAL_TEXT);
  // the live region announces text changes, so the same words are also text inside the link
  assert.equal(textOf(find(w.link, (e) => e.className.includes("visually-hidden"))), REAL_TEXT);
  assert.deepEqual(
    barsOf(w).map((b) => [b.attributes.title, b.style.props["--level"], b.style.props["--color"]]),
    [["Magenta ink 60%", "60%", "#FF00FF"], ["Cyan ink 20% (low)", "20%", "#00FFFF"], ["Yellow ink 60%", "60%", "#FFFF00"], ["Black ink 30%", "30%", "#000000"]],
  );
  assert.deepEqual(barsOf(w).map((b) => b.className.split(" ").includes("low")), [false, true, false, false]);
  assert.equal(iconOf(w), "check_circle");
  const name = find(w.link, (e) => e.className.includes("printer-name"));
  assert.equal(textOf(name), "HP OfficeJet Pro 6970");
  assert.match(name.className, /\bd-none d-md-inline\b/); // hidden below the md breakpoint
});

test("a laser printer's unknown level is a hatched bar titled with a question mark", () => {
  const w = widget(LASER);
  assert.equal(w.link.attributes["aria-label"], "Office laser: Ready · Black toner 8% (low) · Imaging drum 95% · Waste toner box ?");
  const [toner, drum, waste] = barsOf(w);
  assert.equal(waste.attributes.title, "Waste toner box ?");
  assert.match(waste.className, /\bunknown\b/);
  assert.equal(waste.style.props["--level"], undefined);
  assert.equal(drum.style.props["--color"], undefined); // no colour reported: the stylesheet's grey
  assert.equal(toner.style.props["--level"], "8%");
});

test("no supplies means no bars; an unreachable printer or a silent USB link shows the broken-link icon", () => {
  const none = widget({ ...REAL, supplies: [] });
  assert.equal(barsOf(none).length, 0);
  assert.equal(none.link.attributes["aria-label"], "HP OfficeJet Pro 6970: Ready");

  const down = widget({ name: "OfficeJet", model: "", state: "unreachable", problems: [], supplies: [], web_page: "", usb_answering: null });
  assert.equal(down.link.attributes["aria-label"], "OfficeJet: Unreachable");
  assert.equal(iconOf(down), "link_off");
  assert.equal(barsOf(down).length, 0);

  const silent = widget({ ...REAL, usb_answering: false });
  assert.equal(silent.link.attributes["aria-label"], REAL_TEXT.replace("Ready", "Not answering over USB"));
  assert.equal(iconOf(silent), "link_off");
  assert.equal(barsOf(silent).length, 4);
});

test("problems join the text, and each update replaces the bars", () => {
  const w = widget({ ...REAL, problems: [{ label: "Ink low", level: "warning" }] });
  assert.equal(iconOf(w), "warning");
  assert.match(w.link.attributes["aria-label"], /^HP OfficeJet Pro 6970: Ready · Ink low · Magenta ink 60%/);
  w.update({ printers: [{ ...REAL, state: "processing", supplies: REAL.supplies.slice(0, 2) }] });
  assert.equal(barsOf(w).length, 2);
  assert.equal(iconOf(w), "print");
});

test("when /api/status fails the widget says so with the error", () => {
  const w = widget(null);
  assert.equal(w.link.attributes["aria-label"], "Printer status unavailable: Failed to fetch");
  assert.equal(iconOf(w), "link_off");
  assert.equal(barsOf(w).length, 0);
});

test("a 500 ms touch press shows the widget's text and does not follow the link", (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const shown = [];
  const w = widget(REAL, (el, text) => shown.push([el, text]));
  const { pointerdown, pointerup, click } = w.link.listeners;
  pointerdown({ pointerType: "touch" });
  t.mock.timers.tick(499);
  assert.equal(shown.length, 0);
  t.mock.timers.tick(1);
  assert.deepEqual(shown, [[w.link, REAL_TEXT]]);
  let menus = 0;
  w.link.listeners.contextmenu({ preventDefault: () => menus++ }); // the phone's link menu must not open over the tooltip
  assert.equal(menus, 1);
  pointerup({});
  let prevented = 0;
  click({ preventDefault: () => prevented++ });
  assert.equal(prevented, 1);
  click({ preventDefault: () => prevented++ }); // only the click that ends the press
  assert.equal(prevented, 1);
});

test("a short tap, a mouse press or a cancelled touch show nothing and the link works", (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const shown = [];
  const w = widget(REAL, (el, text) => shown.push(text));
  const { pointerdown, pointerup, pointercancel, click } = w.link.listeners;
  pointerdown({ pointerType: "touch" });
  t.mock.timers.tick(300);
  pointerup({});
  t.mock.timers.tick(1000);
  pointerdown({ pointerType: "mouse" });
  t.mock.timers.tick(1000);
  pointerdown({ pointerType: "touch" });
  t.mock.timers.tick(300);
  pointercancel({});
  t.mock.timers.tick(1000);
  assert.deepEqual(shown, []);
  let menus = 0;
  w.link.listeners.contextmenu({ preventDefault: () => menus++ }); // a mouse's right-click keeps its menu
  assert.equal(menus, 0);
  let prevented = 0;
  click({ preventDefault: () => prevented++ });
  assert.equal(prevented, 0);
});

test("the widget sits in a status live region and only its text, not the bars or the icon, is read out", () => {
  const w = widget(REAL);
  assert.equal(w.live.attributes.role, "status");
  assert.equal(w.live.children.length, 1);
  assert.equal(w.live.children[0], w.link);
  const hiddenFromReaders = (cls) => find(w.link, (e) => e.className.split(" ").includes(cls)).attributes["aria-hidden"];
  assert.equal(hiddenFromReaders("supply-bars"), "true");
  assert.equal(hiddenFromReaders("printer-icon"), "true");
});

/** Counts the writes to an element's text: rewriting the text of a live region can be announced again. */
function countTextWrites(el) {
  const own = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), "textContent");
  const writes = { count: 0 };
  Object.defineProperty(el, "textContent", {
    get() {
      return own.get.call(this);
    },
    set(value) {
      writes.count++;
      own.set.call(this, value);
    },
  });
  return writes;
}

test("an update that changes nothing leaves the text in the live region alone", () => {
  const w = widget(REAL);
  const spoken = find(w.link, (e) => e.className.includes("visually-hidden"));
  const name = find(w.link, (e) => e.className.includes("printer-name"));
  const spokenWrites = countTextWrites(spoken);
  const nameWrites = countTextWrites(name);
  w.update({ printers: [{ ...REAL }], unread: 3 }); // the same printer again, as every poll reports it
  assert.deepEqual([spokenWrites.count, nameWrites.count], [0, 0]);
  w.update({ printers: [{ ...REAL, state: "processing" }] });
  assert.equal(textOf(spoken), REAL_TEXT.replace("Ready", "Printing"));
  assert.deepEqual([spokenWrites.count, nameWrites.count], [1, 0]);
});

test("a status poll tells the page's listeners what /api/status said, and what a failure looked like", async () => {
  const sent = [];
  const document = { dispatchEvent: (event) => sent.push(event) };
  const status = { printer: null, error: null, unread: 2, latest_event: 5, printers: [REAL] };
  const real = globalThis.fetch;
  globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => status });
  try {
    assert.deepEqual(await loadStatus(document, 4), status);
    assert.equal(sent.length, 1);
    assert.equal(sent[0].type, STATUS_EVENT);
    assert.deepEqual(sent[0].detail, status);
    globalThis.fetch = async () => ({ ok: false, status: 503, statusText: "Service Unavailable", json: async () => ({ error: "CUPS unavailable" }) });
    const failed = await loadStatus(document, 4);
    assert.deepEqual(failed, { printer: null, error: "CUPS unavailable", unread: null, latest_event: 4 });
    assert.equal(sent.length, 2);
    assert.deepEqual(sent[1].detail, failed);
  } finally {
    globalThis.fetch = real;
  }
});

test("a tooltip shown again is hidden four seconds after the last show, not by the first one's timer", (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const calls = [];
  const tip = { show: () => calls.push("show"), hide: () => calls.push("hide") };
  globalThis.bootstrap = { Tooltip: { getOrCreateInstance: () => tip } };
  try {
    showTip({}, "first");
    t.mock.timers.tick(3000);
    showTip({}, "second");
    t.mock.timers.tick(1500); // past the first show's four seconds, inside the second's
    assert.deepEqual(calls, ["show", "show"]);
    t.mock.timers.tick(2500);
    assert.deepEqual(calls, ["show", "show", "hide"]);
  } finally {
    delete globalThis.bootstrap;
  }
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
  assert.equal(textOf(footers[0]), "Print Hub is open source on GitHub (opens in a new tab)");
  assert.equal(find(link, (el) => el.className === "visually-hidden").textContent, " (opens in a new tab)");
});

test("the theme button shows the current choice and a click cycles Auto, Light, Dark", () => {
  let choice = "auto";
  const set = [];
  const theme = { get: () => choice, set: (next) => (set.push(next), (choice = next)) };
  const button = themeButton(fakeDocument(), theme);
  const seen = () => [button.dataset.icon, button.attributes["aria-label"], button.attributes.title];
  assert.deepEqual(seen(), ["brightness_auto", "Theme: Auto", "Theme: Auto"]);
  button.listeners.click();
  assert.deepEqual(seen(), ["light_mode", "Theme: Light", "Theme: Light"]);
  button.listeners.click();
  assert.deepEqual(seen(), ["dark_mode", "Theme: Dark", "Theme: Dark"]);
  button.listeners.click();
  assert.deepEqual(seen(), ["brightness_auto", "Theme: Auto", "Theme: Auto"]);
  assert.deepEqual(set, ["light", "dark", "auto"]);
});

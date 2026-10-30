// SPDX-License-Identifier: AGPL-3.0-only
import test from "node:test";
import assert from "node:assert/strict";
import { printerCard, watchPrinters } from "./home.js";
import { STATUS_EVENT } from "./shell.js";
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
const card = (entry) => printerCard(fakeDocument(), entry);
const links = (c) => findAll(c, (e) => e.tagName === "A");
const has = (e, cls) => e.className.split(" ").includes(cls);
const meters = (c) => findAll(c, (e) => e.attributes.role === "meter");

test("the card names the printer, says its state in words with an icon, and links to Jobs", () => {
  const c = card(REAL);
  assert.match(textOf(c), /HP OfficeJet Pro 6970/);
  assert.match(textOf(c), /Ready/);
  assert.equal(find(c, (e) => e.dataset.icon).dataset.icon, "check_circle");
  const jobs = links(c).find((a) => textOf(a) === "Jobs");
  assert.equal(jobs.attributes.href, "/jobs.html");
  assert.equal(links(c).length, 1); // no printer page without web_page
});

test("the model is shown under the name only when it adds something", () => {
  const count = (entry) => textOf(card(entry)).split("HP OfficeJet Pro 6970").length - 1;
  assert.equal(count(REAL), 1);
  assert.equal(count({ ...REAL, name: "Hallway" }), 1);
  assert.match(textOf(card({ ...REAL, name: "Hallway" })), /Hallway[\s\S]*HP OfficeJet Pro 6970/);
});

test("each supply is a labelled meter with its percentage, and only the low one carries a low badge", () => {
  const c = card(REAL);
  assert.deepEqual(
    meters(c).map((m) => [m.attributes["aria-label"], m.attributes["aria-valuenow"], m.attributes["aria-valuetext"], m.style.props["--level"], m.style.props["--color"]]),
    [
      ["Magenta ink", "60", "60%", "60%", "#FF00FF"],
      ["Cyan ink", "20", "20%", "20%", "#00FFFF"],
      ["Yellow ink", "60", "60%", "60%", "#FFFF00"],
      ["Black ink", "30", "30%", "30%", "#000000"],
    ],
  );
  const rows = findAll(c, (e) => has(e, "supply"));
  assert.equal(rows.length, 4);
  const badge = (r) => find(r, (e) => has(e, "badge"));
  assert.deepEqual(rows.map((r) => badge(r) && textOf(badge(r))), [null, "low", null, null]);
  assert.match(textOf(rows[1]), /Cyan ink/);
  assert.match(textOf(rows[1]), /20%/);
});

test("an unknown level shows a question mark and a labelled image, not a meter that reads as a value", () => {
  const c = card({ ...REAL, supplies: [{ name: "Waste toner box", type: "waste-toner", color: "", level: null, low: false }] });
  assert.equal(meters(c).length, 0);
  const bar = find(c, (e) => e.attributes["aria-label"] === "Waste toner box: unknown");
  assert.equal(bar.attributes.role, "img");
  for (const name of ["aria-valuenow", "aria-valuetext", "aria-valuemin", "aria-valuemax"]) assert.equal(bar.attributes[name], undefined, name);
  assert.match(bar.className, /\bunknown\b/);
  assert.equal(bar.style.props["--level"], undefined);
  assert.equal(bar.style.props["--color"], undefined);
  assert.match(textOf(c), /Waste toner box[\s\S]*\?/);
});

test("without supplies there are no meters", () => {
  assert.equal(meters(card({ ...REAL, supplies: [] })).length, 0);
});

test("states: printing, stopped, unreachable and a silent USB link read as words with their icon", () => {
  const seen = (over) => {
    const c = card({ ...REAL, ...over });
    return [/Not answering over USB|Unreachable|Stopped|Printing|Ready/.exec(textOf(c))[0], find(c, (e) => e.dataset.icon).dataset.icon];
  };
  assert.deepEqual(seen({ state: "processing" }), ["Printing", "print"]);
  assert.deepEqual(seen({ state: "stopped" }), ["Stopped", "error"]);
  assert.deepEqual(seen({ state: "unreachable", supplies: [] }), ["Unreachable", "link_off"]);
  assert.deepEqual(seen({ usb_answering: false }), ["Not answering over USB", "link_off"]);
});

test("problems are listed in words", () => {
  const c = card({ ...REAL, problems: [{ label: "Paper jam", level: "error" }, { label: "Ink low", level: "warning" }] });
  assert.deepEqual(findAll(c, (e) => e.tagName === "LI").map(textOf), ["Paper jam", "Ink low"]);
  assert.equal(findAll(card(REAL), (e) => e.tagName === "LI").length, 0);
});

test("the printer page opens in a new tab only for an http(s) address", () => {
  const page = links(card({ ...REAL, web_page: "http://192.168.1.50/" })).find((a) => textOf(a) === "Open printer page");
  assert.equal(page.attributes.href, "http://192.168.1.50/");
  assert.equal(page.attributes.target, "_blank");
  assert.equal(page.attributes.rel, "noopener");
  const bad = links(card({ ...REAL, web_page: "javascript:alert(1)" }));
  assert.equal(bad.some((a) => textOf(a) === "Open printer page"), false);
  const upper = links(card({ ...REAL, web_page: "HTTPS://printer.lan/" })).find((a) => textOf(a) === "Open printer page");
  assert.equal(upper.attributes.href, "HTTPS://printer.lan/");
});

test("the list follows the shell's status event and rebuilds only when the printers changed", () => {
  const document = fakeDocument();
  const handlers = {};
  document.addEventListener = (type, fn) => (handlers[type] = fn);
  const list = document.createElement("div");
  watchPrinters(document, list);
  const emit = (status) => handlers[STATUS_EVENT]({ detail: status });

  emit({ printers: [REAL] });
  assert.equal(list.children.length, 1);
  assert.match(textOf(list), /Ready/);
  const first = list.children[0];

  emit({ printers: [REAL], unread: 3 }); // only something else changed
  assert.equal(list.children[0], first);

  emit({ printers: [{ ...REAL, state: "processing" }] });
  assert.match(textOf(list), /Printing/);
  assert.notEqual(list.children[0], first);

  emit({ printers: [REAL, { ...REAL, name: "Second" }] });
  assert.equal(list.children.length, 2);

  emit({ printers: undefined, error: "Failed to fetch" });
  assert.match(textOf(list), /Printer status unavailable: Failed to fetch/);
});

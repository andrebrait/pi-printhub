// SPDX-License-Identifier: AGPL-3.0-only
import test from "node:test";
import assert from "node:assert/strict";
import { mediaLabel, mediaSizeMm, sheetLayout, parsePageRanges, sheetsFor } from "./layout.js";

const A4 = { w: 210, h: 297 };
const A4_PT = [595.28, 841.89];
const LETTER_PT = [612, 792];
const margins3mm = { top: 300, bottom: 300, left: 300, right: 300 };
const near = (actual, expected, tol = 0.1) =>
  assert.ok(Math.abs(actual - expected) <= tol, `${actual} is not within ${tol} of ${expected}`);

test("mediaSizeMm parses PWG self-describing names", () => {
  assert.deepEqual(mediaSizeMm("iso_a4_210x297mm"), A4);
  assert.deepEqual(mediaSizeMm("na_letter_8.5x11in"), { w: 215.9, h: 279.4 });
  assert.equal(mediaSizeMm("custom_foo"), null);
});

test("fit contains the page in the printable area, centered", () => {
  const l = sheetLayout({ sheetMm: A4, landscape: false, marginsHmm: margins3mm, pagePt: A4_PT, scaling: "fit" });
  assert.deepEqual(l.sheet, A4);
  assert.deepEqual(l.printable, { x: 3, y: 3, w: 204, h: 291 });
  near(l.page.w, 204);
  near(l.page.x + l.page.w / 2, 105);
  near(l.page.y + l.page.h / 2, 148.5);
});

test("fit scales a small page up", () => {
  const l = sheetLayout({ sheetMm: A4, landscape: false, marginsHmm: margins3mm, pagePt: [100, 100], scaling: "fit" });
  near(l.page.w, 204);
  near(l.page.h, 204);
});

test("none keeps the actual size and may exceed the printable area", () => {
  const l = sheetLayout({ sheetMm: A4, landscape: false, marginsHmm: margins3mm, pagePt: LETTER_PT, scaling: "none" });
  near(l.page.w, 215.9);
  assert.ok(l.page.w > l.printable.w);
  near(l.page.x + l.page.w / 2, 105);
});

test("fill covers the printable area on both axes", () => {
  // Portrait but squatter than the printable area, so it is not turned and height is the binding axis.
  const l = sheetLayout({ sheetMm: A4, landscape: false, marginsHmm: margins3mm, pagePt: [200, 250], scaling: "fill" });
  const { printable: p, page } = l;
  assert.ok(page.x <= p.x + 1e-9 && page.y <= p.y + 1e-9);
  assert.ok(page.x + page.w >= p.x + p.w - 1e-9 && page.y + page.h >= p.y + p.h - 1e-9);
  near(page.h, p.h);
});

test("auto-fit only shrinks", () => {
  const big = sheetLayout({ sheetMm: A4, landscape: false, marginsHmm: margins3mm, pagePt: [1000, 1000], scaling: "auto-fit" });
  near(big.page.w, 204);
  const small = sheetLayout({ sheetMm: A4, landscape: false, marginsHmm: margins3mm, pagePt: [100, 100], scaling: "auto-fit" });
  near(small.page.w, 100 * 25.4 / 72);
});

test("landscape swaps the sheet dimensions", () => {
  const l = sheetLayout({ sheetMm: A4, landscape: true, marginsHmm: margins3mm, pagePt: A4_PT, scaling: "fit" });
  assert.deepEqual(l.sheet, { w: 297, h: 210 });
  assert.deepEqual(l.printable, { x: 3, y: 3, w: 291, h: 204 });
});

// CUPS turns a page whose shape does not match the sheet by 90 degrees clockwise, in every
// scaling mode (checked against cups-filters through ippeveprinter), so it fills the sheet.
test("a portrait page on a landscape sheet is turned and fills it", () => {
  const l = sheetLayout({ sheetMm: A4, landscape: true, marginsHmm: margins3mm, pagePt: A4_PT, scaling: "fit" });
  assert.equal(l.turned, true);
  near(l.page.h, 204);
  near(l.page.w, 297 * 204 / 210, 0.2);
});

test("a landscape page on a portrait sheet is turned", () => {
  const l = sheetLayout({ sheetMm: A4, landscape: false, marginsHmm: margins3mm, pagePt: [842, 595], scaling: "none" });
  assert.equal(l.turned, true);
  near(l.page.w, 595 * 25.4 / 72);
});

test("a page that already matches the sheet is not turned", () => {
  assert.equal(sheetLayout({ sheetMm: A4, landscape: true, marginsHmm: {}, pagePt: [842, 595], scaling: "fit" }).turned, false);
  assert.equal(sheetLayout({ sheetMm: A4, landscape: false, marginsHmm: {}, pagePt: [100, 100], scaling: "fit" }).turned, false);
});

test("missing margins count as zero", () => {
  const l = sheetLayout({ sheetMm: A4, landscape: false, marginsHmm: {}, pagePt: A4_PT, scaling: "fit" });
  assert.deepEqual(l.printable, { x: 0, y: 0, w: 210, h: 297 });
});

test("parsePageRanges expands 1-based ranges", () => {
  assert.deepEqual(parsePageRanges("1-3,5", 6), [1, 2, 3, 5]);
  assert.deepEqual(parsePageRanges("5,1-2", 6), [1, 2, 5]);
  assert.deepEqual(parsePageRanges("2,2", 6), [2]);
});

test("parsePageRanges rejects invalid input", () => {
  assert.equal(parsePageRanges("7", 6), null);
  assert.equal(parsePageRanges("3-1", 6), null);
  assert.equal(parsePageRanges("0", 6), null);
  assert.equal(parsePageRanges("1-7", 6), null);
  assert.equal(parsePageRanges("", 6), null);
  assert.equal(parsePageRanges("1,", 6), null);
  assert.equal(parsePageRanges("a", 6), null);
});

test("sheetsFor pairs pages when two-sided", () => {
  assert.deepEqual(sheetsFor([1, 2, 3], "two-sided-long-edge"), [
    { front: 1, back: 2 },
    { front: 3, back: null },
  ]);
  assert.deepEqual(sheetsFor([4, 5], "two-sided-short-edge"), [{ front: 4, back: 5 }]);
  assert.deepEqual(sheetsFor([1, 2], "one-sided"), [
    { front: 1, back: null },
    { front: 2, back: null },
  ]);
});

test("mediaLabel gives friendly names and falls back to the raw name", () => {
  assert.equal(mediaLabel("iso_a4_210x297mm"), "A4 (210 × 297 mm)");
  assert.equal(mediaLabel("na_letter_8.5x11in"), "Letter (8.5 × 11 in)");
  assert.equal(mediaLabel("na_legal_8.5x14in"), "Legal (8.5 × 14 in)");
  assert.equal(mediaLabel("na_number-10_4.125x9.5in"), "Envelope #10 (4.125 × 9.5 in)");
  assert.equal(mediaLabel("iso_dl_110x220mm"), "Envelope DL (110 × 220 mm)");
  assert.equal(mediaLabel("na_index-4x6_4x6in"), "4 × 6 in");
  assert.equal(mediaLabel("na_5x7_5x7in"), "5 × 7 in");
  assert.equal(mediaLabel("custom_foo"), "custom_foo");
  assert.equal(mediaLabel("oe_weird_1x2in"), "oe_weird_1x2in");
});

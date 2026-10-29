// SPDX-License-Identifier: AGPL-3.0-only
// Pure sheet-preview geometry; every length is in millimetres unless named otherwise.

const PT_MM = 25.4 / 72;
const round2 = (n) => Math.round(n * 100) / 100;

/** PWG self-describing media name ("iso_a4_210x297mm") to millimetres, or null. */
export function mediaSizeMm(name) {
  const m = /_(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)(mm|in)$/.exec(name);
  if (!m) return null;
  const k = m[3] === "in" ? 25.4 : 1;
  return { w: round2(m[1] * k), h: round2(m[2] * k) };
}

// Keyed by size, so vendor aliases such as "om_a-4-borderless_210x297mm" get the same name as "iso_a4_210x297mm".
const MEDIA_NAMES = {
  "297x420mm": "A3",
  "210x297mm": "A4",
  "148x210mm": "A5",
  "105x148mm": "A6",
  "176x250mm": "B5",
  "182x257mm": "JIS B5",
  "8.5x11in": "Letter",
  "8.5x14in": "Legal",
  "8.5x13in": "Foolscap",
  "7.25x10.5in": "Executive",
  "5.5x8.5in": "Statement",
  "100x148mm": "Postcard",
  "4.125x9.5in": "Envelope #10",
  "3.875x7.5in": "Envelope Monarch",
  "3.625x6.5in": "Envelope Personal",
  "4.375x5.75in": "Envelope A2",
  "110x220mm": "Envelope DL",
  "162x229mm": "Envelope C5",
  "114x162mm": "Envelope C6",
  "120x235mm": "Envelope Chou 3",
  "90x205mm": "Envelope Chou 4",
};

/** Friendly paper name for PWG media ("A4 (210 × 297 mm)", "4 × 6 in, borderless"); the raw name if it has no size. */
export function mediaLabel(name) {
  const m = /^(.*)_((\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)(mm|in))$/.exec(name);
  if (!m) return name;
  const size = `${m[3]} × ${m[4]} ${m[5]}`;
  const named = Object.hasOwn(MEDIA_NAMES, m[2]) ? `${MEDIA_NAMES[m[2]]} (${size})` : size; // photo sizes: the size is the name
  return /borderless/.test(m[1]) ? `${named}, borderless` : named;
}

/** custom_min_/custom_max_ entries bound custom sizes; they are not paper anyone loads. */
export const isLoadableMedia = (name) => !/^custom_(min|max)_/.test(name);

/**
 * Where the page lands on the sheet.
 * marginsHmm is {top, bottom, left, right} in hundredths of a millimetre (as /api/printer reports);
 * pagePt is [width, height] in points; scaling is a print-scaling value. autoRotate: CUPS may turn the page to the
 * sheet's shape (orientation Auto); an explicit orientation sends pdfAutoRotate=false and keeps the page upright.
 */
export function sheetLayout({ sheetMm, landscape, autoRotate = true, marginsHmm, pagePt, scaling }) {
  // shortcut: margins are not rotated with the sheet; printers' margins are near-symmetric
  const sheet = landscape ? { w: sheetMm.h, h: sheetMm.w } : { ...sheetMm };
  const m = (side) => (marginsHmm[side] ?? 0) / 100;
  const printable = {
    x: m("left"),
    y: m("top"),
    w: sheet.w - m("left") - m("right"),
    h: sheet.h - m("top") - m("bottom"),
  };
  // With auto-rotation CUPS turns a page whose shape does not match the sheet 90 degrees clockwise to fill it.
  const turned = autoRotate && pagePt[0] !== pagePt[1] && pagePt[0] > pagePt[1] !== sheet.w > sheet.h;
  const [pw, ph] = turned ? [pagePt[1], pagePt[0]] : pagePt;
  const nat = { w: pw * PT_MM, h: ph * PT_MM };
  const contain = Math.min(printable.w / nat.w, printable.h / nat.h);
  const scale = {
    fit: contain,
    // CUPS "auto" is the printer's choice; both it and auto-fit are shown as shrink-to-fit
    auto: Math.min(1, contain),
    "auto-fit": Math.min(1, contain),
    fill: Math.max(printable.w / nat.w, printable.h / nat.h),
    none: 1,
  }[scaling] ?? contain;
  const w = nat.w * scale;
  const h = nat.h * scale;
  return {
    sheet,
    turned,
    printable,
    page: { x: printable.x + (printable.w - w) / 2, y: printable.y + (printable.h - h) / 2, w, h },
  };
}

const RANGE_LIST = /^\d+(-\d+)?(,\d+(-\d+)?)*$/;

/** "1-3,5" to [1, 2, 3, 5] (sorted, unique); null unless valid for a document of `count` pages. */
export function parsePageRanges(str, count) {
  if (!RANGE_LIST.test(str)) return null;
  const pages = new Set();
  for (const part of str.split(",")) {
    const [a, b = a] = part.split("-").map(Number);
    if (a < 1 || a > b || b > count) return null;
    for (let p = a; p <= b; p++) pages.add(p);
  }
  return [...pages].sort((x, y) => x - y);
}

/** Pages grouped by physical sheet: pairs when two-sided, one per sheet otherwise. */
export function sheetsFor(pages, sides) {
  const step = sides.startsWith("two-sided") ? 2 : 1;
  const sheets = [];
  for (let i = 0; i < pages.length; i += step) {
    sheets.push({ front: pages[i], back: step === 2 ? (pages[i + 1] ?? null) : null });
  }
  return sheets;
}

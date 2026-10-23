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

const MEDIA_NAMES = {
  iso_a3: "A3",
  iso_a4: "A4",
  iso_a5: "A5",
  iso_a6: "A6",
  iso_b5: "B5",
  na_letter: "Letter",
  na_legal: "Legal",
  na_executive: "Executive",
  "na_number-10": "Envelope #10",
  iso_dl: "Envelope DL",
  iso_c5: "Envelope C5",
};

/** Friendly paper name for common PWG media ("A4 (210 × 297 mm)"); the raw name otherwise. */
export function mediaLabel(name) {
  const m = /^(.*)_(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)(mm|in)$/.exec(name);
  if (!m) return name;
  const size = `${m[2]} × ${m[3]} ${m[4]}`;
  if (/^na_(index-)?\d+x\d+$/.test(m[1])) return size; // photo paper: the size is the name
  return MEDIA_NAMES[m[1]] ? `${MEDIA_NAMES[m[1]]} (${size})` : name;
}

/**
 * Where the page lands on the sheet.
 * marginsHmm is {top, bottom, left, right} in hundredths of a millimetre (as /api/printer reports);
 * pagePt is [width, height] in points; scaling is a print-scaling value.
 */
export function sheetLayout({ sheetMm, landscape, marginsHmm, pagePt, scaling }) {
  // shortcut: margins are not rotated with the sheet; printers' margins are near-symmetric
  const sheet = landscape ? { w: sheetMm.h, h: sheetMm.w } : { ...sheetMm };
  const m = (side) => (marginsHmm[side] ?? 0) / 100;
  const printable = {
    x: m("left"),
    y: m("top"),
    w: sheet.w - m("left") - m("right"),
    h: sheet.h - m("top") - m("bottom"),
  };
  // CUPS turns a page whose shape does not match the sheet 90 degrees clockwise to fill it.
  const turned = pagePt[0] !== pagePt[1] && pagePt[0] > pagePt[1] !== sheet.w > sheet.h;
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

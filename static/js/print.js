// SPDX-License-Identifier: AGPL-3.0-only
import { apiFetch, errorMessage } from "./api.js";
import { showToast } from "./shell.js";
import { isLoadableMedia, mediaLabel, mediaSizeMm, sheetLayout, parsePageRanges, sheetsFor } from "./layout.js";

const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);
const docId = encodeURIComponent(params.get("doc") ?? "");

// IPP option -> label, and the form-field name the print endpoint takes.
// closed: show only the labelled values (IPP enums such as reverse-landscape make no sense here).
const CONTROLS = [
  { key: "media", label: "Paper", labels: {}, format: mediaLabel, keep: isLoadableMedia },
  { key: "print-quality", advanced: true, label: "Quality", closed: true, labels: { 3: "Draft", 4: "Normal", 5: "Best" } },
  { key: "print-color-mode", label: "Color", labels: { color: "Color", monochrome: "Grayscale" } },
  { key: "orientation-requested", advanced: true, label: "Orientation", closed: true, labels: { 3: "Portrait", 4: "Landscape" } },
  {
    key: "sides",
    label: "Sides",
    labels: {
      "one-sided": "One-sided",
      "two-sided-long-edge": "Two-sided (long edge)",
      "two-sided-short-edge": "Two-sided (short edge)",
    },
  },
  {
    key: "print-scaling",
    advanced: true,
    label: "Scaling",
    labels: { auto: "Auto", "auto-fit": "Shrink to fit", fit: "Fit to page", fill: "Fill page", none: "Actual size" },
  },
];
// Sent when the printer does not offer the option: the server leaves it out of the job.
const FALLBACK = {
  media: "iso_a4_210x297mm",
  "print-quality": "4",
  "print-color-mode": "color",
  "orientation-requested": "3",
  sides: "one-sided",
  "print-scaling": "fit",
};
const SCALING_ALL = ["auto", "auto-fit", "fit", "fill", "none"];
let printer, doc, pages; // pages: [{url, ptSize, rotation, turned, full, crop}]
const controls = {}; // key -> <select> | null

function show(el, text) {
  el.textContent = text;
  el.hidden = !text;
}

function buildPages() {
  if (doc.kind === "print") {
    return Array.from({ length: doc.preview_count }, (_, i) => ({
      url: `/api/docs/${doc.id}/preview/${i + 1}`,
      pt: doc.pdf_pages[i],
    }));
  }
  return doc.pages.map((p) => {
    const { width, height, dpi } = p.page_info; // after rotation, before crop
    const c = p.crop;
    return {
      url: `/api/docs/${doc.id}/pages/${p.id}?size=thumb`,
      pt: [(c?.w ?? width) * 72 / dpi[0], (c?.h ?? height) * 72 / dpi[1]],
      rotation: p.rotation,
      full: [width, height],
      crop: c,
    };
  });
}

function buildControls() {
  for (const c of CONTROLS) {
    const host = $(c.advanced ? "advanced-controls" : "controls");
    let values = printer.supported[c.key] ?? [];
    if (c.key === "print-scaling" && !values.length) values = SCALING_ALL;
    if (c.closed) values = values.filter((v) => v in c.labels);
    if (c.keep) values = values.filter(c.keep);
    if (!values.length) {
      controls[c.key] = null;
      continue;
    }
    const select = document.createElement("select");
    select.id = `opt-${c.key}`;
    select.className = "form-select";
    for (const v of values) select.add(new Option(c.format?.(v) ?? c.labels[v] ?? v, v));
    const preset = c.key === "print-scaling" && doc.kind === "scan" ? "none" : printer.defaults[c.key];
    select.value = values.includes(preset) ? preset : values.includes(FALLBACK[c.key]) ? FALLBACK[c.key] : values[0];
    select.addEventListener("change", render);
    const label = document.createElement("label");
    label.className = "form-label";
    label.htmlFor = select.id;
    label.textContent = c.label;
    const field = document.createElement("div");
    field.append(label, select);
    host.append(field);
    controls[c.key] = select;
  }
}

const value = (key) => controls[key]?.value ?? printer.defaults[key] ?? FALLBACK[key];

function pct(v, total) {
  return `${(v / total) * 100}%`;
}

function sheetEl(pageNo, gray) {
  const el = document.createElement("div");
  el.className = gray ? "sheet gray" : "sheet";
  el.setAttribute("role", "img");
  el.setAttribute("aria-label", pageNo ? `Page ${pageNo}` : "Blank back side");
  const media = mediaSizeMm(value("media")) ?? mediaSizeMm(FALLBACK.media);
  const landscape = value("orientation-requested") === "4";
  const first = pageNo && pages[pageNo - 1];
  const L = sheetLayout({
    sheetMm: media,
    landscape,
    marginsHmm: printer.margins,
    pagePt: first ? first.pt : [1, 1],
    scaling: value("print-scaling"),
  });
  el.style.aspectRatio = `${L.sheet.w} / ${L.sheet.h}`;
  if (!first) return el;

  const { printable: pr, page: pg } = L;
  const area = document.createElement("div");
  area.className = "printable";
  Object.assign(area.style, {
    left: pct(pr.x, L.sheet.w), top: pct(pr.y, L.sheet.h), width: pct(pr.w, L.sheet.w), height: pct(pr.h, L.sheet.h),
  });
  const box = document.createElement("div");
  box.className = "page";
  Object.assign(box.style, {
    left: pct(pg.x - pr.x, pr.w), top: pct(pg.y - pr.y, pr.h), width: pct(pg.w, pr.w), height: pct(pg.h, pr.h),
  });
  const crop = document.createElement("div");
  crop.className = "crop";
  const c = first.crop;
  Object.assign(crop.style, c
    ? { left: pct(-c.x, c.w), top: pct(-c.y, c.h), width: pct(first.full[0], c.w), height: pct(first.full[1], c.h) }
    : { left: 0, top: 0, width: "100%", height: "100%" });
  const rot = document.createElement("div");
  rot.className = "rot";
  const turned = first.rotation === 90 || first.rotation === 270;
  rot.classList.toggle("turned", turned);
  rot.style.setProperty("--rot", `${first.rotation ?? 0}deg`);
  const img = new Image();
  img.src = first.url;
  img.alt = "";
  img.loading = "lazy";
  rot.append(img);
  crop.append(rot);
  // A turned page keeps its upright content in an inner frame rotated 90 degrees clockwise.
  const frame = document.createElement("div");
  frame.className = L.turned ? "turn" : "upright";
  frame.append(crop);
  box.append(frame);
  area.append(box);
  el.append(area);
  return el;
}

function render() {
  $("layout-summary").textContent = ["print-quality", "orientation-requested", "print-scaling"]
    .map((key) => controls[key]?.selectedOptions[0]?.textContent).filter(Boolean).join(" · ");
  const rangeText = $("page-ranges").value.trim();
  const selected = rangeText ? parsePageRanges(rangeText, pages.length) : pages.map((_, i) => i + 1);
  show($("range-error"), selected ? "" : `Use page numbers like 1-3,5 between 1 and ${pages.length}`);
  $("page-ranges").setAttribute("aria-invalid", String(!selected));
  const copies = $("copies");
  const copiesOk = copies.checkValidity();
  copies.setAttribute("aria-invalid", String(!copiesOk));
  show($("copies-error"), copiesOk ? "" : "Copies must be a whole number from 1 to 99");
  $("print").disabled = !selected || !copiesOk || !pages.length;
  if (!selected) return;

  const sides = value("sides");
  const gray = /monochrome|bi-level/.test(value("print-color-mode"));
  const preview = $("preview");
  preview.replaceChildren();
  const sheets = sheetsFor(selected, sides);
  sheets.forEach(({ front, back }, i) => {
    const group = document.createElement("figure");
    group.className = "sheet-group";
    const row = document.createElement("div");
    row.className = "sides";
    row.append(sheetEl(front, gray));
    if (sides.startsWith("two-sided")) row.append(sheetEl(back, gray));
    const cap = document.createElement("figcaption");
    const flip = sides.endsWith("short-edge") ? "short" : "long";
    cap.textContent = sides.startsWith("two-sided")
      ? `Sheet ${i + 1} of ${sheets.length}: front and back, flip on ${flip} edge`
      : `Sheet ${i + 1} of ${sheets.length}: page ${front}`;
    group.append(row, cap);
    preview.append(group);
  });
}

async function print(e) {
  e.preventDefault();
  const button = $("print");
  button.disabled = true;
  show($("print-error"), "");
  try {
    const res = await apiFetch(`/api/docs/${docId}/print`, {
      method: "POST",
      body: {
        media: value("media"),
        print_quality: value("print-quality"),
        print_color_mode: value("print-color-mode"),
        orientation_requested: value("orientation-requested"),
        sides: value("sides"),
        print_scaling: value("print-scaling"),
        copies: Number($("copies").value),
        page_ranges: $("page-ranges").value.trim() || null,
      },
    });
    if (!res.ok) throw new Error(await errorMessage(res));
    const { job_id: jobId } = await res.json();
    showToast(`Job ${jobId} sent to the printer`, { href: `/jobs.html#job-${jobId}` });
  } catch (err) {
    show($("print-error"), err.message);
  }
  button.disabled = false;
}

function showConversion(message) {
  $("convert").hidden = false;
  $("convert-message").textContent = message;
  $("retry").addEventListener("click", async (e) => {
    e.target.disabled = true;
    e.target.textContent = "Converting…";
    const res = await apiFetch(`/api/docs/${docId}/convert`, { method: "POST" });
    if (res.ok) return void (location.href = `/print.html?doc=${docId}`);
    $("convert-message").textContent = await errorMessage(res);
    e.target.disabled = false;
    e.target.textContent = "Retry";
  });
}

async function main() {
  const fatal = $("load-error");
  if (!docId) return show(fatal, "No document selected.");
  const [docRes, printerRes] = await Promise.all([apiFetch(`/api/docs/${docId}`), apiFetch("/api/printer")]);
  if (!docRes.ok) return show(fatal, await errorMessage(docRes));
  doc = await docRes.json();
  $("title").textContent = doc.title;
  document.title = `${doc.title} – Print Hub`;
  if (doc.kind === "print" && !doc.preview_count) {
    return showConversion(params.get("error") ?? "This file has not been converted for printing yet.");
  }
  if (!printerRes.ok) return show(fatal, await errorMessage(printerRes));
  printer = await printerRes.json();
  pages = buildPages();
  buildControls();
  $("layout").hidden = false;
  for (const id of ["copies", "page-ranges"]) $(id).addEventListener("input", render);
  $("options").addEventListener("submit", print);
  render();
}

main();

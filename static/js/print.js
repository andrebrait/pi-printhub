// SPDX-License-Identifier: AGPL-3.0-only
import { apiFetch, errorMessage } from "./api.js";
import { mediaSizeMm, sheetLayout, parsePageRanges, sheetsFor } from "./layout.js";

const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);
const docId = params.get("doc");

// IPP option -> label, and the form-field name the print endpoint takes.
// closed: show only the labelled values (IPP enums such as reverse-landscape make no sense here).
const CONTROLS = [
  { key: "media", label: "Paper", labels: {} },
  { key: "print-quality", label: "Quality", closed: true, labels: { 3: "Draft", 4: "Normal", 5: "Best" } },
  { key: "print-color-mode", label: "Color", labels: { color: "Color", monochrome: "Grayscale" } },
  { key: "orientation-requested", label: "Orientation", closed: true, labels: { 3: "Portrait", 4: "Landscape" } },
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
const JOB_LABELS = {
  pending: "Queued",
  held: "Held",
  processing: "Printing",
  stopped: "Stopped",
  canceled: "Canceled",
  aborted: "Aborted",
  completed: "Completed",
};
const TERMINAL = new Set(["completed", "canceled", "aborted"]);

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
  const k = 72 / (doc.scan_resolution || 300); // px to points
  return doc.pages.map((p) => {
    const { width, height } = p.page_info; // after rotation, before crop
    const c = p.crop;
    return {
      url: `/api/docs/${doc.id}/pages/${p.id}?size=thumb`,
      pt: c ? [c.w * k, c.h * k] : [width * k, height * k],
      rotation: p.rotation,
      full: [width, height],
      crop: c,
    };
  });
}

function buildControls() {
  const host = $("controls");
  for (const c of CONTROLS) {
    let values = printer.supported[c.key] ?? [];
    if (c.key === "print-scaling" && !values.length) values = SCALING_ALL;
    if (c.closed) values = values.filter((v) => v in c.labels);
    if (!values.length) {
      controls[c.key] = null;
      continue;
    }
    const select = document.createElement("select");
    select.id = `opt-${c.key}`;
    for (const v of values) select.add(new Option(c.labels[v] ?? v, v));
    const preset = c.key === "print-scaling" && doc.kind === "scan" ? "none" : printer.defaults[c.key];
    select.value = values.includes(preset) ? preset : values.includes(FALLBACK[c.key]) ? FALLBACK[c.key] : values[0];
    select.addEventListener("change", render);
    const label = document.createElement("label");
    label.append(c.label, select);
    host.append(label);
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
  box.append(crop);
  area.append(box);
  el.append(area);
  return el;
}

function render() {
  const rangeText = $("page-ranges").value.trim();
  const selected = rangeText ? parsePageRanges(rangeText, pages.length) : pages.map((_, i) => i + 1);
  show($("range-error"), selected ? "" : `Use page numbers like 1-3,5 between 1 and ${pages.length}`);
  $("page-ranges").setAttribute("aria-invalid", String(!selected));
  const copies = $("copies");
  $("print").disabled = !selected || !copies.checkValidity() || !pages.length;
  if (!selected) return;

  const sides = value("sides");
  const gray = /monochrome|bi-level/.test(value("print-color-mode"));
  const preview = $("preview");
  preview.replaceChildren();
  sheetsFor(selected, sides).forEach(({ front, back }, i) => {
    const group = document.createElement("figure");
    group.className = "sheet-group";
    const row = document.createElement("div");
    row.className = "sides";
    row.append(sheetEl(front, gray));
    if (sides.startsWith("two-sided")) row.append(sheetEl(back, gray));
    const cap = document.createElement("figcaption");
    const flip = sides.endsWith("short-edge") ? "short" : "long";
    cap.textContent = sides.startsWith("two-sided")
      ? `Sheet ${i + 1}: front and back, flip on ${flip} edge`
      : `Sheet ${i + 1}: page ${front}`;
    group.append(row, cap);
    preview.append(group);
  });
}

function jobCard(jobId) {
  const card = document.createElement("div");
  card.className = "card job";
  card.setAttribute("role", "status");
  const title = document.createElement("strong");
  title.textContent = `Job ${jobId}: `;
  const state = document.createElement("span");
  state.className = "state";
  const detail = document.createElement("p");
  const cancel = document.createElement("button");
  cancel.type = "button";
  cancel.textContent = "Cancel";
  card.append(title, state, detail, cancel);
  $("jobs").prepend(card);

  let timer;
  const stop = () => {
    clearTimeout(timer);
    cancel.hidden = true;
  };
  async function poll() {
    try {
      const res = await apiFetch(`/api/jobs/${jobId}`);
      if (res.status === 404) {
        state.textContent = "Finished";
        detail.textContent = "The printer no longer lists this job.";
        return stop();
      }
      if (!res.ok) throw new Error(await errorMessage(res));
      const { job, printer: p } = await res.json();
      state.className = `state ${job.state}`;
      state.textContent = JOB_LABELS[job.state] ?? job.state;
      const reasons = [...job.reasons, ...p.state_reasons];
      detail.textContent = reasons.length ? `Printer: ${[...new Set(reasons)].join(", ")}` : "";
      if (TERMINAL.has(job.state)) return stop();
    } catch (e) {
      detail.textContent = `Status unavailable: ${e.message}`;
    }
    timer = setTimeout(poll, 2000);
  }
  cancel.addEventListener("click", async () => {
    cancel.disabled = true;
    const res = await apiFetch(`/api/jobs/${jobId}/cancel`, { method: "POST" });
    if (!res.ok) detail.textContent = await errorMessage(res);
    cancel.disabled = false;
  });
  poll();
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
    jobCard((await res.json()).job_id);
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
    if (res.ok) return void (location.href = `/print.html?doc=${encodeURIComponent(docId)}`);
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

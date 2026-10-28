// SPDX-License-Identifier: AGPL-3.0-only
/* global Cropper, Sortable */
import { apiFetch, errorMessage } from "./api.js";
import { rotateCrop, snapCrop } from "./crop.js";

const $ = (id) => document.getElementById(id);
const MODES = { color: "Color", gray: "Grayscale" };
const PAPER = { a4: 210 / 297, letter: 215.9 / 279.4 }; // portrait width / height

let docId = new URLSearchParams(location.search).get("doc");
let doc = null;
let lastSource = null;
let cropper = null, cropping = null;
// A saved scan opens for viewing; the scanner is only needed once the user edits or adds pages.
let editing = !docId;
let scannerFound = false;
const docUrl = () => `/api/docs/${encodeURIComponent(docId)}`;

function show(el, text) {
  el.textContent = text;
  el.hidden = !text;
}

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

function fillSelect(select, values, label, preferred) {
  select.replaceChildren();
  for (const v of values) select.add(new Option(label(v), v));
  if (values.includes(preferred)) select.value = preferred;
}

async function loadScanner() {
  const retry = $("scanner-retry");
  retry.disabled = true;
  try {
    const res = await apiFetch("/api/scanner");
    if (!res.ok) throw new Error(await errorMessage(res));
    const caps = await res.json();
    fillSelect($("resolution"), caps.resolutions, (r) => `${r} dpi`, 300);
    fillSelect($("mode"), caps.modes, (m) => MODES[m] ?? m, "color");
    $("scan-flatbed").hidden = !caps.sources.includes("flatbed");
    $("scan-adf").hidden = !caps.sources.includes("adf");
    $("settings").hidden = false;
    show($("scanner-status"), caps.sources.length ? "" : "The scanner offers no flatbed or feeder.");
    retry.hidden = true;
    scannerFound = true;
    lastSource ??= caps.sources[0];
  } catch (e) {
    show($("scanner-status"), e.message);
    retry.hidden = false;
  } finally {
    retry.disabled = false;
  }
}

async function loadRecipients() {
  const res = await apiFetch("/api/recipients");
  if (!res.ok) return;
  $("recipients").replaceChildren(...(await res.json()).map((a) => new Option(a)));
}

async function scan(source) {
  lastSource = source;
  for (const b of [$("scan-flatbed"), $("scan-adf"), $("retry")]) b.disabled = true;
  $("scan-failed").hidden = true;
  show($("scan-status"), "Starting the scanner…");
  const settings = { source, resolution: Number($("resolution").value), mode: $("mode").value, area: $("area").value };
  let res;
  try {
    res = await apiFetch("/api/scans", { method: "POST", body: { settings, doc_id: docId } });
    if (!res.ok) throw new Error(await errorMessage(res));
  } catch (e) {
    if (doc) render(doc);
    else for (const b of [$("scan-flatbed"), $("scan-adf"), $("retry")]) b.disabled = false;
    show($("scan-status"), "");
    $("scan-failed").hidden = false;
    $("scan-error").textContent = e.message;
    return;
  }
  if (!docId) {
    docId = (await res.json()).doc_id;
    history.replaceState(null, "", `?doc=${encodeURIComponent(docId)}`);
  }
  poll();
}

async function refresh() {
  const res = await apiFetch(docUrl());
  if (!res.ok) throw new Error(await errorMessage(res));
  render(await res.json());
}

async function poll() {
  try {
    await refresh();
    if (!doc.scanning) return;
  } catch (e) {
    show($("scan-status"), `Could not check the scan (${e.message}); trying again`);
  }
  setTimeout(poll, 1000);
}

let saving = Promise.resolve();
let editFailures = 0; // lets a caller tell whether an edit queued before it failed

/**
 * Queue an edit. `build` gets the document as the previous edit left it (so quick clicks add up) and returns the
 * PATCH body, or null for nothing to do; the page then shows what the server stored (it snaps crops).
 * Resolves false when the edit failed.
 */
function patch(build) {
  saving = saving
    .then(async () => {
      const body = build(doc);
      if (!body) return true;
      show($("edit-error"), "");
      const res = await apiFetch(docUrl(), { method: "PATCH", body });
      if (!res.ok) throw new Error(await errorMessage(res));
      render(await res.json());
      return true;
    })
    .catch((e) => {
      editFailures++;
      show($("edit-error"), e.message);
      render(doc); // undo what the page shows, e.g. a rejected drag
      return false;
    });
  return saving;
}

const editPage = (id, change) => patch((d) => ({ pages: d.pages.map((p) => (p.id === id ? { ...p, ...change(p) } : p)) }));

function rotate(id, delta) {
  editPage(id, (p) => {
    const { width, height, mcu } = p.page_info;
    return { rotation: (p.rotation + delta + 360) % 360, crop: p.crop && rotateCrop(p.crop, [width, height], delta, mcu) };
  });
}

function move(id, step) {
  patch((d) => {
    const from = d.pages.findIndex((p) => p.id === id);
    const to = from + step;
    if (from < 0 || to < 0 || to >= d.pages.length) return null;
    const pages = [...d.pages];
    pages.splice(to, 0, ...pages.splice(from, 1));
    return { pages };
  });
}

const pct = (v, total) => `${(v / total) * 100}%`;

function pageItem(page, i, busy) {
  const n = i + 1;
  const { width, height } = page.page_info;
  const li = document.createElement("li");
  li.className = "card scan-page";
  li.dataset.id = page.id;

  const thumb = document.createElement("div");
  thumb.className = "thumb";
  thumb.style.aspectRatio = `${width} / ${height}`;
  thumb.setAttribute("role", "img");
  thumb.setAttribute("aria-label", `Page ${n}${editing && !busy ? ". Arrow keys move it; drag to reorder" : ""}`);
  if (editing) {
    thumb.tabIndex = 0;
    thumb.dataset.focusKey = `${page.id}:move`;
    thumb.addEventListener("keydown", (e) => {
      const step = { ArrowLeft: -1, ArrowUp: -1, ArrowRight: 1, ArrowDown: 1 }[e.key];
      if (!step || busy) return;
      e.preventDefault();
      move(page.id, step);
    });
  }
  const rot = document.createElement("div");
  rot.className = "rot";
  rot.classList.toggle("turned", page.rotation % 180 !== 0);
  rot.style.setProperty("--rot", `${page.rotation}deg`);
  const img = new Image();
  img.src = `${docUrl()}/pages/${encodeURIComponent(page.id)}?size=thumb`;
  img.alt = "";
  img.loading = "lazy";
  rot.append(img);
  thumb.append(rot);
  if (page.crop) {
    const c = page.crop;
    const mark = document.createElement("div");
    mark.className = "crop-mark";
    Object.assign(mark.style, {
      left: pct(c.x, width), top: pct(c.y, height), width: pct(c.w, width), height: pct(c.h, height),
    });
    thumb.append(mark);
  }

  const caption = document.createElement("p");
  caption.className = "page-no";
  caption.textContent = `Page ${n}`;

  const tools = document.createElement("div");
  tools.className = "tools";
  const button = (action, text, label, onClick) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "btn btn-sm btn-outline-secondary";
    b.textContent = text;
    b.setAttribute("aria-label", `${label} page ${n}`);
    b.dataset.focusKey = `${page.id}:${action}`;
    b.disabled = busy;
    b.addEventListener("click", onClick);
    tools.append(b);
  };
  button("left", "↺", "Rotate left", () => rotate(page.id, -90));
  button("right", "↻", "Rotate right", () => rotate(page.id, 90));
  button("crop", "Crop", "Crop", () => openCrop(page.id));
  button("delete", "Delete", "Delete", () => confirm(`Delete page ${n}?`) && patch((d) => ({ pages: d.pages.filter((p) => p.id !== page.id) })));
  button("reset", "Reset", "Undo rotation and crop on", () => editPage(page.id, () => ({ rotation: 0, crop: null })));

  li.append(thumb, caption);
  if (editing) li.append(tools);
  return li;
}

function render(d) {
  doc = d;
  const busy = doc.scanning;
  $("heading").textContent = doc.title;
  document.title = `${doc.title} – Print Hub`;
  if (document.activeElement !== $("title")) $("title").value = doc.title;
  $("subject").placeholder = doc.title;
  $("export").hidden = false;
  $("download-pdf").href = `${docUrl()}/export?format=pdf`;
  $("download-jpeg").href = `${docUrl()}/export?format=jpeg`;
  $("print").href = `/print.html?doc=${encodeURIComponent(docId)}`;
  $("outputs").hidden = !doc.pages.length;
  for (const b of [$("scan-flatbed"), $("scan-adf"), $("retry")]) b.disabled = busy;
  $("scan-form").hidden = !editing;
  $("edit-toggle").hidden = !doc.pages.length && editing; // an empty scan has nothing to view yet
  $("edit-toggle").textContent = editing ? "Done" : "Edit or add pages";
  $("edit-toggle").disabled = busy;
  // Another scan appends to this document.
  const verb = doc.pages.length ? "Add" : "Scan";
  $("scan-flatbed").textContent = `${verb} from glass`;
  $("scan-adf").textContent = `${verb} from feeder`;
  show($("scan-status"), busy ? `Scanning… ${plural(doc.pages.length, "page")} so far` : "");
  $("scan-failed").hidden = busy || !doc.scan_error;
  $("scan-error").textContent = doc.scan_error ?? "";

  const list = $("pages");
  const focusKey = list.contains(document.activeElement) ? document.activeElement.dataset.focusKey : null;
  list.replaceChildren(...doc.pages.map((p, i) => pageItem(p, i, busy)));
  if (focusKey) list.querySelector(`[data-focus-key="${focusKey}"]`)?.focus();
  sortable.option("disabled", busy || !editing);
}

const sortable = Sortable.create($("pages"), {
  handle: ".thumb",
  delay: 200, // touch: a short hold starts a drag, a swipe still scrolls
  delayOnTouchOnly: true,
  onEnd(e) {
    if (e.oldIndex === e.newIndex) return;
    const order = [...$("pages").children].map((li) => li.dataset.id);
    patch((d) => ({ pages: order.map((id) => d.pages.find((p) => p.id === id)).filter(Boolean) }));
  },
});

function cropAspect() {
  const paper = PAPER[$("aspect").value];
  if (!paper) return NaN; // free
  const { width, height } = cropping.page_info;
  return width > height ? 1 / paper : paper;
}

async function openCrop(id) {
  await saving; // e.g. a rotation still on its way changes the frame the crop is drawn in
  const page = (cropping = doc.pages.find((p) => p.id === id));
  if (!page) return;
  const img = $("crop-image");
  // rotated=1 serves the page in the frame page_info and crops use; rotation in the URL keeps caches apart.
  img.src = `${docUrl()}/pages/${encodeURIComponent(page.id)}?size=full&rotated=1&rotation=${page.rotation}`;
  $("crop-status").textContent = "";
  $("crop-dialog").showModal();
  const c = page.crop;
  cropper = new Cropper(img, {
    viewMode: 1,
    autoCropArea: 1,
    rotatable: false,
    zoomable: false,
    aspectRatio: cropAspect(),
    data: c ? { x: c.x, y: c.y, width: c.w, height: c.h } : undefined,
    cropend() {
      // Show the box the export will cut: the top-left corner moves to the iMCU grid.
      const s = snapped();
      cropper.setData({ x: s.x, y: s.y, width: s.w, height: s.h });
    },
  });
}

function snapped() {
  const d = cropper.getData(true);
  const { width, height, mcu } = cropping.page_info;
  return snapCrop({ x: d.x, y: d.y, w: d.width, h: d.height }, [width, height], mcu);
}

$("crop-dialog").addEventListener("close", () => {
  cropper?.destroy();
  cropper = null;
  $("crop-image").removeAttribute("src");
});
$("crop-cancel").addEventListener("click", () => $("crop-dialog").close());
$("crop-apply").addEventListener("click", () => {
  const crop = snapped();
  const id = cropping.id;
  $("crop-dialog").close();
  editPage(id, () => ({ crop }));
});
$("aspect").addEventListener("change", () => cropper?.setAspectRatio(cropAspect()));
$("crop-stage").addEventListener("keydown", (e) => {
  const delta = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[e.key];
  if (!delta || !cropper || e.altKey || e.ctrlKey || e.metaKey) return;
  e.preventDefault();
  const { width, height, mcu: [mx, my] } = cropping.page_info;
  const data = cropper.getData(true);
  if (e.shiftKey) {
    data.width = Math.min(width - data.x, Math.max(1, data.width + delta[0] * mx));
    data.height = Math.min(height - data.y, Math.max(1, data.height + delta[1] * my));
  } else {
    data.x = Math.max(0, Math.min(width - data.width, data.x + delta[0] * mx));
    data.y = Math.max(0, Math.min(height - data.height, data.y + delta[1] * my));
  }
  cropper.setData(data);
  const c = snapped();
  cropper.setData({ x: c.x, y: c.y, width: c.w, height: c.h });
  $("crop-status").textContent = `Crop: ${c.w} by ${c.h} pixels, left ${c.x}, top ${c.y}.`;
});


$("scan-flatbed").addEventListener("click", () => scan("flatbed"));
$("scan-adf").addEventListener("click", () => scan("adf"));
$("retry").addEventListener("click", () => scan(lastSource));
$("scanner-retry").addEventListener("click", loadScanner);

function setEditing(on) {
  editing = on;
  if (editing && !scannerFound) loadScanner();
  if (doc) render(doc);
}
$("edit-toggle").addEventListener("click", () => setEditing(!editing));

function saveTitle() {
  const title = $("title").value;
  return patch((d) => (d && title.trim() !== d.title ? { title } : null));
}
$("title").addEventListener("blur", saveTitle);

// Exports and print read the stored title, so a rename still being typed or saved goes first.
for (const id of ["download-pdf", "download-jpeg", "print"]) {
  $(id).addEventListener("click", async (e) => {
    if (e.button > 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return; // new tab or window: browser default
    e.preventDefault();
    const href = e.currentTarget.href;
    // A failed rename keeps the page, so its error stays visible.
    if (await saveTitle()) location.assign(href);
  });
}

$("email").addEventListener("submit", async (e) => {
  e.preventDefault();
  const status = $("email-status");
  const to = $("to").value.split(",").map((a) => a.trim()).filter(Boolean);
  $("send").disabled = true;
  status.className = "mb-0";
  show(status, "Sending…");
  try {
    // The export reads the stored pages: edits and a rename still saving go first, and a failed one stops the send.
    const failures = editFailures;
    await saveTitle();
    if (editFailures > failures) throw new Error("Not sent: the latest change could not be saved.");
    const res = await apiFetch(`${docUrl()}/email`, {
      method: "POST",
      body: { to, subject: $("subject").value.trim() || doc.title, format: e.target.elements.format.value },
    });
    if (!res.ok) throw new Error(await errorMessage(res));
    show(status, `Sent to ${to.join(", ")}`);
    loadRecipients();
  } catch (err) {
    status.className = "mb-0 error";
    show(status, err.message);
  }
  $("send").disabled = false;
});

function main() {
  loadRecipients();
  if (!docId) {
    $("scan-form").hidden = false;
    return void loadScanner();
  }
  refresh().then(
    () => {
      if (!doc.scanning) return;
      setEditing(true); // a scan still running belongs to the editor
      poll();
    },
    (e) => show($("load-error"), e.message),
  );
}

main();

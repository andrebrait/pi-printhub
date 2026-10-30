// SPDX-License-Identifier: AGPL-3.0-only
// The top bar every signed-in page shares: navigation, printer status, notification center and toasts.
/* global bootstrap */
import { apiFetch, errorMessage } from "./api.js";

// The event kinds in the settings page's order, as the server's KINDS lists them.
export const KIND_LABELS = { job: "Print jobs", scan: "Scans", printer: "Printer status", conversion: "File conversions", settings: "Settings changes" };

const LEVEL_TEXT = { info: "Info", warning: "Warning", error: "Error" };
/** The document event the shell dispatches with each /api/status result; the Home page's printer cards listen to it. */
export const STATUS_EVENT = "printhub-status";
export const LEVEL_CLASS = {
  ok: "text-success-emphasis",
  busy: "text-primary-emphasis",
  warning: "text-warning-emphasis",
  error: "text-danger-emphasis",
  unknown: "text-secondary-emphasis",
};
const STATE_WORD = { idle: "Ready", processing: "Printing", stopped: "Stopped" };
const ITEM_CLASS = { info: "", warning: "list-group-item-warning", error: "list-group-item-danger" };
const PAGES = [
  ["/", "Home", "home"],
  ["/scan.html", "Scan", "scanner"],
  ["/jobs.html", "Jobs", "list_alt"],
  ["/settings.html", "Settings", "settings"],
];
const POLL_MS = 5000;
const LONG_PRESS_MS = 500;
const TIP_MS = 4000;
const MAX_TOASTS = 3;

/** Word, icon and colour level of one entry of /api/status `printers`. */
export function printerState(p) {
  if (p.state === "unreachable") return { word: "Unreachable", icon: "link_off", level: "error" };
  if (p.usb_answering === false) return { word: "Not answering over USB", icon: "link_off", level: "error" };
  const word = STATE_WORD[p.state] ?? p.state;
  if (p.state === "stopped" || p.problems.some((x) => x.level === "error")) return { word, icon: "error", level: "error" };
  if (p.problems.length) return { word, icon: "warning", level: "warning" };
  return p.state === "processing" ? { word, icon: "print", level: "busy" } : { word, icon: "check_circle", level: "ok" };
}

/** "Cyan ink 20% (low)"; "Waste toner box ?" when the printer does not know the level. */
export function supplyText(s) {
  return s.level == null ? `${s.name} ?` : `${s.name} ${s.level}%${s.low ? " (low)" : ""}`;
}

/** Everything the widget shows, as one line: "Name: Ready · Ink low · Cyan ink 20% (low) · …". */
export function printerText(p) {
  return [`${p.name}: ${printerState(p).word}`, ...p.problems.map((x) => x.label), ...p.supplies.map(supplyText)].join(" · ");
}

/** Calls show() once a touch has stayed down for LONG_PRESS_MS; the click that ends that press is cancelled so the link is not followed. */
function onLongPress(target, show) {
  let timer;
  let fired = false;
  let touching = false;
  const release = () => {
    clearTimeout(timer);
    touching = false;
  };
  target.addEventListener("pointerdown", (e) => {
    clearTimeout(timer);
    fired = false;
    touching = e.pointerType === "touch";
    if (touching) {
      timer = setTimeout(() => {
        fired = true;
        show();
      }, LONG_PRESS_MS);
    }
  });
  target.addEventListener("pointerup", release);
  target.addEventListener("pointercancel", release);
  target.addEventListener("pointerleave", release);
  target.addEventListener("contextmenu", (e) => touching && e.preventDefault()); // the phone's own link menu would cover the tooltip
  target.addEventListener("click", (e) => {
    if (fired) e.preventDefault();
    fired = false;
  });
}

/** document.createElement with a class and optional text; text is never parsed as HTML. */
export function node(document, tag, className = "", text) {
  const el = Object.assign(document.createElement(tag), { className });
  if (text != null) el.textContent = text;
  return el;
}

/** What the widget and the Home page say when /api/status lists no printer. */
export function unavailableText(status) {
  return `Printer status unavailable${status.error ? `: ${status.error}` : ""}`;
}

/**
 * The header's compact printer widget: a link to Jobs with the printer name (from md up), a bar per supply and a status icon,
 * inside the live region that announces it. update(status) takes an /api/status response; showTip(link, text) runs on a long touch press.
 */
export function printerWidget(document, showTip = () => {}) {
  const make = (tag, className) => node(document, tag, className);
  const live = make("span", "printer-live d-inline-flex min-w-0 order-2 order-md-last");
  live.setAttribute("role", "status"); // wraps the link, so the widget stays a link for assistive technology
  const link = make("a", "printer-widget");
  link.href = "/jobs.html";
  link.id = "printer-status";
  const name = make("span", "printer-name d-none d-md-inline");
  const bars = make("span", "supply-bars");
  const icon = make("span", "printer-icon");
  const spoken = make("span", "visually-hidden"); // text the live region announces when it changes
  bars.setAttribute("aria-hidden", "true");
  icon.setAttribute("aria-hidden", "true");
  link.append(name, bars, icon, spoken);
  live.append(link);
  // Text written into a live region can be read out again, so an unchanged value is left alone.
  const write = (el, value) => {
    if (el.textContent !== value) el.textContent = value;
  };
  let text = "";
  const set = (nextText, nextName, iconName, level, supplies) => {
    text = nextText;
    write(name, nextName);
    write(spoken, text);
    link.setAttribute("aria-label", text);
    link.setAttribute("title", text);
    icon.dataset.icon = iconName;
    icon.className = `printer-icon ${LEVEL_CLASS[level]}`;
    bars.replaceChildren(...supplies.map((s) => {
      const bar = make("span", `supply-bar${s.level == null ? " unknown" : ""}${s.low ? " low" : ""}`);
      bar.setAttribute("title", supplyText(s));
      if (s.level != null) bar.style.setProperty("--level", `${s.level}%`);
      if (s.color) bar.style.setProperty("--color", s.color);
      return bar;
    }));
  };
  set("Checking printer…", "", "refresh", "unknown", []);
  onLongPress(link, () => showTip(link, text));
  return {
    live,
    link,
    update(status) {
      const p = status.printers?.[0];
      if (!p) return set(unavailableText(status), "Printer", "link_off", "unknown", []);
      const { icon, level } = printerState(p);
      set(printerText(p), p.name, icon, level, p.supplies);
    },
  };
}

let tipText = "";
let tipTimer;

/** A tooltip with the widget's text, for touch screens that have no hover; it fades after a few seconds. */
export function showTip(target, text) {
  tipText = text;
  const tip = bootstrap.Tooltip.getOrCreateInstance(target, {
    trigger: "manual", container: "body", placement: "bottom", fallbackPlacements: ["bottom"], title: () => tipText,
  });
  tip.show();
  clearTimeout(tipTimer); // a show while the last one is still up keeps its full time
  tipTimer = setTimeout(() => tip.hide(), TIP_MS);
}

/** Reads /api/status ({printer: null, error, …} when that fails), tells the page's listeners (the Home page's printer cards) and returns it. */
export async function loadStatus(document, latestSeen) {
  let status;
  try {
    const res = await apiFetch("/api/status");
    if (!res.ok) throw new Error(await errorMessage(res));
    status = await res.json();
  } catch (e) {
    status = { printer: null, error: e.message, unread: null, latest_event: latestSeen };
  }
  document.dispatchEvent(new CustomEvent(STATUS_EVENT, { detail: status }));
  return status;
}

function eventHref(e) {
  if (e.job_id != null) return `/jobs.html#job-${e.job_id}`;
  if (e.doc_id) return `/${e.kind === "scan" ? "scan" : "print"}.html?doc=${encodeURIComponent(e.doc_id)}`;
  return null;
}

export function when(iso) {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

const DURATIONS = [[1, "1 hour"], [8, "8 hours"], [24, "1 day"], [168, "1 week"]];

/** Silence menu: this alert (same title) or every alert of its kind, for 1 hour to 1 week; picking one calls onSilence({match, value, hours}). */
function silenceMenu(e, document, onSilence) {
  const menu = document.createElement("details");
  menu.className = "mt-2 fw-normal";
  const summary = document.createElement("summary");
  summary.className = "btn btn-sm btn-outline-secondary";
  summary.textContent = "Silence";
  summary.setAttribute("aria-label", `Silence ${e.title}`);
  menu.append(summary);
  for (const [label, match, value] of [["This alert", "title", e.title], [`All ${KIND_LABELS[e.kind] ?? e.kind}`, "kind", e.kind]]) {
    const group = document.createElement("div");
    group.className = "d-flex flex-wrap align-items-center gap-1 mt-2";
    group.setAttribute("role", "group");
    group.setAttribute("aria-label", label);
    const name = document.createElement("span");
    name.className = "small w-100";
    name.textContent = label;
    group.append(name);
    for (const [hours, text] of DURATIONS) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "btn btn-sm btn-outline-secondary";
      button.textContent = text;
      button.addEventListener("click", () => {
        menu.open = false;
        onSilence({ match, value, hours });
      });
      group.append(button);
    }
    menu.append(group);
  }
  return menu;
}

/** One notification for the list: level in words (not only colour), text never parsed as HTML. With onSilence it gets a Silence menu, kept out of the link; onOpen runs when the notification itself, not that menu, is clicked. */
export function eventItem(e, document, onSilence, onOpen) {
  const item = document.createElement("div");
  item.className = `list-group-item ${ITEM_CLASS[e.level] ?? ""}${e.read ? "" : " fw-semibold"}`;
  item.dataset.id = String(e.id);
  const href = eventHref(e);
  const body = document.createElement(href ? "a" : "div");
  if (href) {
    body.href = href;
    body.className = "d-block text-reset text-decoration-none";
  }
  const head = document.createElement("div");
  head.className = "d-flex flex-wrap justify-content-between gap-1";
  const level = document.createElement("span");
  level.className = "small text-uppercase text-nowrap";
  level.textContent = `${LEVEL_TEXT[e.level] ?? e.level}${e.read ? "" : " · new"}`;
  const at = document.createElement("small");
  at.className = "text-body-secondary text-nowrap";
  at.textContent = when(e.at);
  head.append(level, at);
  const title = document.createElement("div");
  title.className = "text-break";
  title.textContent = e.title;
  body.append(head, title);
  if (e.detail) {
    const detail = document.createElement("div");
    detail.className = "small text-body-secondary text-break fw-normal";
    detail.textContent = e.detail;
    body.append(detail);
  }
  if (onOpen) body.addEventListener("click", onOpen);
  item.append(body);
  if (onSilence) item.append(silenceMenu(e, document, onSilence));
  return item;
}

/** Silences an alert or a kind; resolves to the text for a toast. */
export async function silence(body) {
  const res = await apiFetch("/api/notify/silences", { method: "POST", body });
  if (!res.ok) throw new Error(await errorMessage(res));
  return `Silenced until ${when((await res.json()).until)}`;
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

let toastHost;

/** A short-lived message in the corner, optionally linking somewhere. */
export function showToast(message, { href, level = "info" } = {}) {
  if (!toastHost) {
    // Bottom left: clear of the status, bell and page actions, which sit top right and in the right column.
    toastHost = el("div", "toast-container position-fixed bottom-0 start-0 p-3");
    toastHost.setAttribute("aria-live", "polite");
    document.body.append(toastHost);
  }
  while (toastHost.children.length >= MAX_TOASTS) toastHost.firstElementChild.remove();
  // Info toasts use Bootstrap's own toast surface, which follows the theme (text-bg-dark vanished on a dark page).
  const tone = { error: "text-bg-danger border-0", warning: "text-bg-warning border-0" }[level] ?? "";
  const toast = el("div", `toast align-items-center ${tone}`);
  toast.setAttribute("role", level === "error" ? "alert" : "status");
  const row = el("div", "d-flex");
  const body = el("div", "toast-body");
  body.append(el("span", null, message));
  if (href) {
    const link = el("a", `ms-2 fw-semibold ${level === "error" ? "link-light" : level === "warning" ? "link-dark" : ""}`, href.startsWith("/jobs.html") ? "View in Jobs" : "Open document");
    link.href = href;
    body.append(link);
  }
  const close = el("button", `btn-close ${level === "error" ? "btn-close-white" : ""} me-2 m-auto`);
  close.type = "button";
  close.setAttribute("data-bs-dismiss", "toast");
  close.setAttribute("aria-label", "Close");
  row.append(body, close);
  toast.append(row);
  toastHost.append(toast);
  toast.addEventListener("hidden.bs.toast", () => toast.remove());
  bootstrap.Toast.getOrCreateInstance(toast, { delay: 6000 }).show();
}

const THEMES = { auto: ["brightness_auto", "Auto", "light"], light: ["light_mode", "Light", "dark"], dark: ["dark_mode", "Dark", "auto"] };

/** The header button that shows the theme choice (Auto, Light, Dark) and cycles to the next on click. */
export function themeButton(document, theme) {
  const button = document.createElement("button");
  button.className = "btn btn-outline-secondary";
  button.type = "button";
  const show = () => {
    const [icon, name] = THEMES[theme.get()];
    button.dataset.icon = icon;
    button.setAttribute("aria-label", `Theme: ${name}`);
    button.setAttribute("title", `Theme: ${name}`);
  };
  button.addEventListener("click", () => {
    theme.set(THEMES[theme.get()][2]);
    show();
  });
  show();
  return button;
}

function buildBar(host) {
  const nav = el("nav", "navbar navbar-expand-md bg-body-tertiary border-bottom mb-3");
  nav.setAttribute("aria-label", "Main");
  const wrap = el("div", "container-lg");
  const brand = el("a", "navbar-brand fw-semibold d-flex align-items-center gap-2", "Print Hub");
  brand.href = "/";
  const logo = el("img");
  logo.src = "/icons/logo.svg";
  logo.alt = ""; // the brand text names the link
  logo.width = logo.height = 28;
  brand.prepend(logo);

  // Status and the bell stay visible when the links collapse on phones.
  const tools = el("div", "shell-tools d-flex align-items-center gap-2 order-1 order-md-last");
  const widget = printerWidget(document, showTip);
  const live = widget.live;
  const cog = el("a", "btn btn-outline-secondary");
  cog.href = "/settings.html";
  cog.dataset.icon = "settings";
  cog.setAttribute("aria-label", "Settings");
  const bell = el("button", "btn btn-outline-secondary position-relative bell");
  bell.type = "button";
  bell.setAttribute("data-bs-toggle", "offcanvas");
  bell.setAttribute("data-bs-target", "#notifications");
  bell.setAttribute("aria-controls", "notifications");
  bell.dataset.icon = "notifications";
  bell.setAttribute("aria-label", "Notifications");
  const count = el("span", "position-absolute top-0 start-100 translate-middle badge rounded-pill text-bg-danger");
  count.hidden = true;
  bell.append(count);
  const toggler = el("button", "navbar-toggler");
  toggler.type = "button";
  toggler.setAttribute("data-bs-toggle", "collapse");
  toggler.setAttribute("data-bs-target", "#main-nav");
  toggler.setAttribute("aria-controls", "main-nav");
  toggler.setAttribute("aria-expanded", "false");
  toggler.setAttribute("aria-label", "Menu");
  toggler.append(el("span", "navbar-toggler-icon"));
  // A stale cached theme.js may not define it yet: the header must still render.
  const theme = window.printhubTheme ?? { get: () => "auto", set() {} };
  tools.append(themeButton(document, theme), cog, bell, toggler);

  const collapse = el("div", "collapse navbar-collapse order-3 order-md-0");
  collapse.id = "main-nav";
  const links = el("ul", "navbar-nav me-auto");
  const here = location.pathname === "/index.html" ? "/" : location.pathname;
  if (here === cog.getAttribute("href")) {
    cog.classList.add("active");
    cog.setAttribute("aria-current", "page");
  }
  for (const [href, label, icon] of PAGES) {
    const li = el("li", "nav-item");
    const a = el("a", `nav-link${href === here ? " active" : ""}`, label);
    a.href = href;
    a.dataset.icon = icon;
    if (href === here) a.setAttribute("aria-current", "page");
    li.append(a);
    links.append(li);
  }
  const logout = el("button", "btn btn-link nav-link px-md-2", "Log out");
  logout.type = "button";
  logout.dataset.icon = "logout";
  logout.addEventListener("click", async () => {
    await apiFetch("/api/logout", { method: "POST" });
    location.href = "/login.html";
  });
  const logoutItem = el("li", "nav-item");
  logoutItem.append(logout);
  links.append(logoutItem);
  collapse.append(links);

  wrap.append(brand, live, tools, collapse);
  nav.append(wrap);
  host.replaceChildren(nav);
  return { widget, bell, count };
}

function buildCenter() {
  const panel = el("div", "offcanvas offcanvas-end");
  panel.id = "notifications";
  panel.tabIndex = -1;
  panel.setAttribute("aria-labelledby", "notifications-title");
  const header = el("div", "offcanvas-header border-bottom");
  const title = el("h2", "offcanvas-title h5 me-auto", "Notifications");
  title.id = "notifications-title";
  const markAll = el("button", "btn btn-sm btn-outline-secondary me-2", "Mark all read");
  markAll.dataset.icon = "done_all";
  markAll.type = "button";
  const close = el("button", "btn-close");
  close.type = "button";
  close.setAttribute("data-bs-dismiss", "offcanvas");
  close.setAttribute("aria-label", "Close");
  header.append(title, markAll, close);
  const body = el("div", "offcanvas-body p-0 d-flex flex-column");
  const error = el("div", "alert alert-danger m-3");
  error.setAttribute("role", "alert");
  error.hidden = true;
  const list = el("div", "list-group list-group-flush");
  const empty = el("p", "text-body-secondary m-3", "No notifications.");
  empty.hidden = true;
  const more = el("button", "btn btn-link my-2", "Load more");
  more.type = "button";
  more.hidden = true;
  body.append(error, list, empty, more);
  panel.append(header, body);
  document.body.append(panel);
  return { panel, list, empty, more, markAll, error };
}

function init(host) {
  const bar = buildBar(host);
  const center = buildCenter();
  let latestSeen = null; // newest event id already toasted
  let oldestShown = null;

  function setCount(unread) {
    bar.count.textContent = unread > 99 ? "99+" : String(unread);
    bar.count.hidden = !unread;
    bar.bell.setAttribute("aria-label", unread ? `Notifications, ${unread} unread` : "Notifications");
  }

  async function silenceAlert(body) {
    try {
      showToast(await silence(body));
      document.dispatchEvent(new Event("silences-changed")); // the settings page lists them
    } catch (e) {
      showToast(`Could not silence: ${e.message}`, { level: "error" });
    }
  }

  async function poll() {
    const status = await loadStatus(document, latestSeen);
    bar.widget.update(status);
    if (status.unread != null) setCount(status.unread);
    if (latestSeen !== null && status.latest_event > latestSeen) await toastNew(latestSeen);
    if (status.latest_event != null) latestSeen = Math.max(latestSeen ?? 0, status.latest_event);
  }

  async function toastNew(after) {
    const res = await apiFetch("/api/notifications?limit=10");
    if (!res.ok) return;
    const fresh = (await res.json()).events.filter((e) => e.id > after).reverse();
    for (const e of fresh.slice(-MAX_TOASTS)) {
      showToast(e.detail ? `${e.title}: ${e.detail}` : e.title, { href: eventHref(e), level: e.level });
    }
  }

  async function load(more) {
    center.error.hidden = true;
    const query = more && oldestShown ? `?before=${oldestShown}` : "";
    try {
      const res = await apiFetch(`/api/notifications${query}`);
      if (!res.ok) throw new Error(await errorMessage(res));
      const page = await res.json();
      const items = page.events.map((e) => {
        // keepalive: the item holds a link, so the page may unload before the request finishes.
        return eventItem(e, document, silenceAlert, e.read ? undefined : () => markRead({ ids: [e.id] }, { keepalive: true }).catch(() => {}));
      });
      if (more) center.list.append(...items);
      else center.list.replaceChildren(...items);
      if (page.events.length) oldestShown = page.events.at(-1).id;
      center.empty.hidden = center.list.children.length > 0;
      center.more.hidden = page.events.length < 50;
      setCount(page.unread);
    } catch (e) {
      center.error.textContent = `Notifications unavailable: ${e.message}`;
      center.error.hidden = false;
    }
  }

  async function markRead(body, opts = {}) {
    const res = await apiFetch("/api/notifications/read", { method: "POST", body, ...opts });
    if (!res.ok) throw new Error(await errorMessage(res));
  }

  center.panel.addEventListener("show.bs.offcanvas", () => load(false));
  center.more.addEventListener("click", () => load(true));
  center.markAll.addEventListener("click", async () => {
    try {
      await markRead({ up_to: latestSeen ?? 0 });
      await load(false);
    } catch (e) {
      center.error.textContent = `Could not mark as read: ${e.message}`;
      center.error.hidden = false;
    }
  });

  poll();
  setInterval(() => document.visibilityState === "visible" && poll(), POLL_MS);
  document.addEventListener("visibilitychange", () => document.visibilityState === "visible" && poll());
}

/** A small centred footer linking to the project, on every page including the headerless login. */
export function mountFooter(document) {
  const footer = document.createElement("footer");
  footer.className = "text-center small text-body-secondary py-3 mt-auto";
  const link = document.createElement("a");
  link.className = "link-secondary";
  link.href = "https://github.com/andrebrait/pi-printhub";
  link.setAttribute("target", "_blank");
  link.setAttribute("rel", "noopener");
  link.dataset.icon = "github";
  link.textContent = "Print Hub is open source on GitHub";
  const hint = document.createElement("span");
  hint.className = "visually-hidden";
  hint.textContent = " (opens in a new tab)";
  link.append(hint);
  footer.append(link);
  document.body.append(footer);
}

// Under node --test there is no page: only the helpers above are used.
if (typeof document !== "undefined") {
  mountFooter(document);
  const host = document.getElementById("shell");
  if (host) init(host);
}

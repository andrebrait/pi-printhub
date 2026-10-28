// SPDX-License-Identifier: AGPL-3.0-only
// The top bar every signed-in page shares: navigation, printer status, notification center and toasts.
/* global bootstrap */
import { apiFetch, errorMessage } from "./api.js";

const LEVEL_TEXT = { info: "Info", warning: "Warning", error: "Error" };
const PILL_CLASS = {
  ok: "text-bg-success",
  busy: "text-bg-primary",
  warning: "text-bg-warning",
  error: "text-bg-danger",
  unknown: "text-bg-secondary",
};
const ITEM_CLASS = { info: "", warning: "list-group-item-warning", error: "list-group-item-danger" };
const PAGES = [
  ["/", "Home"],
  ["/print.html", "Print"],
  ["/scan.html", "Scan"],
  ["/jobs.html", "Jobs"],
];
const POLL_MS = 5000;
const MAX_TOASTS = 3;

/** The status pill for an /api/status response. */
export function statusPill(status) {
  const p = status.printer;
  if (!p) return { text: "Status unavailable", level: "unknown" };
  const problems = p.problems ?? [];
  const error = p.state === "stopped" || problems.some((x) => x.level === "error");
  if (problems.length) return { text: problems.map((x) => x.label).join(", "), level: error ? "error" : "warning" };
  if (p.state === "stopped") return { text: "Stopped", level: "error" };
  return p.state === "processing" ? { text: "Printing", level: "busy" } : { text: "Ready", level: "ok" };
}

function eventHref(e) {
  if (e.job_id != null) return `/jobs.html#job-${e.job_id}`;
  if (e.doc_id) return `/${e.kind === "scan" ? "scan" : "print"}.html?doc=${encodeURIComponent(e.doc_id)}`;
  return null;
}

function when(iso) {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

/** One notification for the list: level in words (not only colour), text never parsed as HTML. */
export function eventItem(e, document) {
  const href = eventHref(e);
  const item = document.createElement(href ? "a" : "div");
  if (href) item.href = href;
  item.className = `list-group-item list-group-item-action ${ITEM_CLASS[e.level] ?? ""}${e.read ? "" : " fw-semibold"}`;
  item.dataset.id = String(e.id);
  const head = document.createElement("div");
  head.className = "d-flex justify-content-between gap-2";
  const level = document.createElement("span");
  level.className = "small text-uppercase";
  level.textContent = `${LEVEL_TEXT[e.level] ?? e.level}${e.read ? "" : " · new"}`;
  const at = document.createElement("small");
  at.className = "text-body-secondary text-nowrap";
  at.textContent = when(e.at);
  head.append(level, at);
  const title = document.createElement("div");
  title.className = "text-break";
  title.textContent = e.title;
  item.append(head, title);
  if (e.detail) {
    const detail = document.createElement("div");
    detail.className = "small text-body-secondary text-break fw-normal";
    detail.textContent = e.detail;
    item.append(detail);
  }
  return item;
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

const BELL = '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><path d="M8 16a2 2 0 0 0 2-2H6a2 2 0 0 0 2 2m.995-14.901a1 1 0 1 0-1.99 0A5 5 0 0 0 3 6c0 1.098-.5 6-2 7h14c-1.5-1-2-5.902-2-7 0-2.42-1.72-4.44-4.005-4.901"/></svg>';

let toastHost;

/** A short-lived message in the corner, optionally linking somewhere. */
export function showToast(message, { href, level = "info" } = {}) {
  if (!toastHost) {
    toastHost = el("div", "toast-container position-fixed bottom-0 end-0 p-3");
    toastHost.setAttribute("aria-live", "polite");
    document.body.append(toastHost);
  }
  while (toastHost.children.length >= MAX_TOASTS) toastHost.firstElementChild.remove();
  const toast = el("div", `toast align-items-center border-0 ${level === "error" ? "text-bg-danger" : level === "warning" ? "text-bg-warning" : "text-bg-dark"}`);
  toast.setAttribute("role", level === "error" ? "alert" : "status");
  const row = el("div", "d-flex");
  const body = el("div", "toast-body");
  body.append(el("span", null, message));
  if (href) {
    const link = el("a", "ms-2 link-light fw-semibold", "View");
    link.href = href;
    body.append(link);
  }
  const close = el("button", `btn-close ${level === "warning" ? "" : "btn-close-white"} me-2 m-auto`);
  close.type = "button";
  close.setAttribute("data-bs-dismiss", "toast");
  close.setAttribute("aria-label", "Close");
  row.append(body, close);
  toast.append(row);
  toastHost.append(toast);
  toast.addEventListener("hidden.bs.toast", () => toast.remove());
  bootstrap.Toast.getOrCreateInstance(toast, { delay: 6000 }).show();
}

function buildBar(host) {
  const nav = el("nav", "navbar navbar-expand-md bg-body-tertiary border-bottom mb-3");
  nav.setAttribute("aria-label", "Main");
  const wrap = el("div", "container-lg");
  const brand = el("a", "navbar-brand fw-semibold", "Print Hub");
  brand.href = "/";

  // Status and the bell stay visible when the links collapse on phones.
  const tools = el("div", "shell-tools d-flex align-items-center gap-2 order-md-last");
  const pill = el("a", "badge rounded-pill text-bg-secondary text-decoration-none text-truncate status-pill", "Checking printer…");
  pill.href = "/jobs.html";
  pill.id = "printer-status";
  // The live region wraps the link, so the pill stays a link for assistive technology.
  const live = el("span", "d-inline-flex min-w-0");
  live.setAttribute("role", "status");
  live.append(pill);
  const bell = el("button", "btn btn-outline-secondary position-relative bell");
  bell.type = "button";
  bell.setAttribute("data-bs-toggle", "offcanvas");
  bell.setAttribute("data-bs-target", "#notifications");
  bell.setAttribute("aria-controls", "notifications");
  bell.insertAdjacentHTML("afterbegin", BELL); // static markup, no data
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
  tools.append(live, bell, toggler);

  const collapse = el("div", "collapse navbar-collapse");
  collapse.id = "main-nav";
  const links = el("ul", "navbar-nav me-auto");
  const here = location.pathname === "/index.html" ? "/" : location.pathname;
  for (const [href, label] of PAGES) {
    const li = el("li", "nav-item");
    const a = el("a", `nav-link${href === here ? " active" : ""}`, label);
    a.href = href;
    if (href === here) a.setAttribute("aria-current", "page");
    li.append(a);
    links.append(li);
  }
  const logout = el("button", "btn btn-link nav-link px-md-2", "Log out");
  logout.type = "button";
  logout.addEventListener("click", async () => {
    await apiFetch("/api/logout", { method: "POST" });
    location.href = "/login.html";
  });
  const logoutItem = el("li", "nav-item");
  logoutItem.append(logout);
  links.append(logoutItem);
  collapse.append(links);

  wrap.append(brand, tools, collapse);
  nav.append(wrap);
  host.replaceChildren(nav);
  return { pill, bell, count };
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

  async function poll() {
    let status;
    try {
      const res = await apiFetch("/api/status");
      if (!res.ok) throw new Error(await errorMessage(res));
      status = await res.json();
    } catch (e) {
      status = { printer: null, error: e.message, unread: null, latest_event: latestSeen };
    }
    const pill = statusPill(status);
    bar.pill.className = `badge rounded-pill ${PILL_CLASS[pill.level]} text-decoration-none text-truncate status-pill`;
    bar.pill.textContent = pill.text;
    bar.pill.title = status.error ? `${pill.text}: ${status.error}` : `Printer: ${pill.text}`;
    bar.pill.setAttribute("aria-label", bar.pill.title);
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
        const item = eventItem(e, document);
        if (!e.read) item.addEventListener("click", () => markRead({ ids: [e.id] }));
        return item;
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

  async function markRead(body) {
    const res = await apiFetch("/api/notifications/read", { method: "POST", body });
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

/** Bootstrap's dark mode follows the system setting. */
export function followColorScheme() {
  const dark = matchMedia("(prefers-color-scheme: dark)");
  const apply = () => document.documentElement.setAttribute("data-bs-theme", dark.matches ? "dark" : "light");
  apply();
  dark.addEventListener("change", apply);
}

// Under node --test there is no page: only the helpers above are used.
if (typeof document !== "undefined") {
  followColorScheme();
  const host = document.getElementById("shell");
  if (host) init(host);
}

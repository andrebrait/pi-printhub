// SPDX-License-Identifier: AGPL-3.0-only
import { apiFetch, errorMessage } from "./api.js";

const STATE_TEXT = {
  pending: "Queued",
  held: "Held",
  processing: "Printing",
  stopped: "Stopped",
  canceled: "Canceled",
  aborted: "Failed",
  completed: "Completed",
};
const STATE_CLASS = {
  pending: "text-bg-secondary",
  held: "text-bg-warning",
  processing: "text-bg-primary",
  stopped: "text-bg-danger",
  canceled: "text-bg-secondary",
  aborted: "text-bg-danger",
  completed: "text-bg-success",
};
const ACTIVE = new Set(["pending", "held", "processing", "stopped"]);
const REFRESH_MS = 5000;

function when(iso) {
  return iso ? new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "";
}

/** One job: what, from whom, its state in words, and Cancel while it can still be stopped. */
export function jobItem(job, document, onCancel) {
  const el = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  };
  const item = el("li", "list-group-item d-flex flex-wrap align-items-center gap-2");
  item.setAttribute("id", `job-${job.id}`);
  const main = el("div", "flex-grow-1 min-w-0");
  main.append(el("div", "fw-semibold text-break", job.name || `Job ${job.id}`));
  const sender = [job.user, job.host].filter(Boolean).join("@");
  const facts = [`Job ${job.id}`, sender, job.completed ? `finished ${when(job.completed)}` : when(job.created)];
  if (job.sheets) facts.push(`${job.sheets} sheet${job.sheets === 1 ? "" : "s"}`);
  main.append(el("div", "small text-body-secondary text-break", facts.filter(Boolean).join(" · ")));
  if (job.reason_labels?.length) main.append(el("div", "small text-break", job.reason_labels.join(", ")));
  item.append(main);

  const side = el("div", "d-flex align-items-center gap-2 ms-auto");
  side.append(el("span", `badge ${STATE_CLASS[job.state] ?? "text-bg-secondary"}`, STATE_TEXT[job.state] ?? job.state));
  if (job.doc_id) {
    const open = el("a", "btn btn-sm btn-outline-secondary", "Document");
    open.href = `/print.html?doc=${encodeURIComponent(job.doc_id)}`;
    side.append(open);
  }
  if (ACTIVE.has(job.state)) {
    const cancel = el("button", "btn btn-sm btn-outline-danger", "Cancel");
    cancel.type = "button";
    cancel.setAttribute("aria-label", `Cancel job ${job.id}`);
    cancel.addEventListener("click", () => onCancel(job.id, cancel));
    side.append(cancel);
  }
  item.append(side);
  return item;
}

async function cancel(id, button) {
  button.disabled = true;
  const error = document.getElementById("jobs-error");
  try {
    const res = await apiFetch(`/api/jobs/${id}/cancel`, { method: "POST" });
    if (!res.ok) throw new Error(await errorMessage(res));
    error.hidden = true;
  } catch (e) {
    error.textContent = `Could not cancel job ${id}: ${e.message}`;
    error.hidden = false;
    button.disabled = false;
  }
  refresh();
}

function fill(list, jobs, empty) {
  list.replaceChildren(...jobs.map((j) => jobItem(j, document, cancel)));
  document.getElementById(empty).hidden = jobs.length > 0;
}

async function refresh() {
  const error = document.getElementById("jobs-error");
  try {
    const res = await apiFetch("/api/jobs");
    if (!res.ok) throw new Error(await errorMessage(res));
    const { active, recent } = await res.json();
    fill(document.getElementById("active-jobs"), active, "no-active");
    fill(document.getElementById("recent-jobs"), recent, "no-recent");
    document.getElementById("jobs-loading").hidden = true;
    // A link to #job-N from a notification: bring that job into view once it is listed.
    const target = location.hash && document.querySelector(location.hash);
    if (target && !target.dataset.seen) {
      target.dataset.seen = "1";
      target.classList.add("list-group-item-info");
      target.scrollIntoView({ block: "center" });
    }
  } catch (e) {
    error.textContent = `Jobs unavailable: ${e.message}`;
    error.hidden = false;
  }
}

if (typeof document !== "undefined" && document.getElementById("active-jobs")) {
  refresh();
  setInterval(() => document.visibilityState === "visible" && refresh(), REFRESH_MS);
  document.getElementById("jobs-retry").addEventListener("click", refresh);
}

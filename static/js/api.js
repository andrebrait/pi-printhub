// SPDX-License-Identifier: AGPL-3.0-only

/** A same-site path (with query and hash) from user-supplied `next`; "/" for anything else. */
export function safeNext(next, origin) {
  // Browsers strip tab/CR/LF inside URLs, so "/\t/evil.example" would become "//evil.example".
  if (!next || /[\x00-\x1f\x7f]/.test(next)) return "/";
  try {
    const url = new URL(next, origin);
    // Dot segments collapse after the origin check ("/.//evil.example" -> "//evil.example"), so test the result.
    if (url.origin !== origin || /^[/\\]{2}/.test(url.pathname)) return "/";
    return url.pathname + url.search + url.hash;
  } catch {
    return "/";
  }
}

function toLogin() {
  const here = location.pathname + location.search;
  location.href = `/login.html?next=${encodeURIComponent(here)}`;
  // Never settles, so callers do not touch a response that does not exist.
  return new Promise(() => {});
}

/** fetch that sends a plain-object body as JSON and sends the browser to the login page on 401. */
export async function apiFetch(path, opts = {}) {
  const init = { ...opts };
  if (init.body !== undefined && typeof init.body === "object" && !(init.body instanceof FormData)) {
    init.body = JSON.stringify(init.body);
    init.headers = { "Content-Type": "application/json", ...init.headers };
  }
  const res = await fetch(path, init);
  return res.status === 401 ? toLogin() : res;
}

/** The server's {"error": ...} text for a failed response. */
export async function errorMessage(res) {
  const body = await res.json().catch(() => null);
  return body?.error ?? `${res.status} ${res.statusText}`;
}

/** Upload one file; onProgress gets 0..1 while sending. Resolves {status, body} (body is parsed JSON or null). */
export function uploadFile(file, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/uploads");
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    xhr.onerror = () => reject(new Error("Network error"));
    xhr.onload = () => {
      if (xhr.status === 401) return void toLogin();
      let body = null;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {}
      resolve({ status: xhr.status, body });
    };
    const form = new FormData();
    form.append("file", file);
    xhr.send(form);
  });
}

const REASONS = {
  "marker-supply-low": "Ink low",
  "marker-supply-empty": "Out of ink",
  "toner-low": "Toner low",
  "toner-empty": "Out of toner",
  "media-low": "Paper low",
  "media-empty": "Out of paper",
  "media-needed": "Load paper",
  "media-jam": "Paper jam",
  "door-open": "Cover open",
  "cover-open": "Cover open",
  "input-tray-missing": "Paper tray missing",
  "output-area-full": "Output tray full",
  offline: "Offline",
  "connecting-to-device": "Connecting",
  paused: "Paused",
  "printer-stopped": "Printer stopped",
};

/** Readable text for IPP printer/job state reasons; job- and cups- bookkeeping reasons repeat the state, so they go. */
export function reasonLabels(reasons) {
  const labels = reasons
    .filter((r) => r !== "none" && !/^(job|cups)-/.test(r))
    .map((r) => {
      const key = r.replace(/-(report|warning|error)$/, "");
      return REASONS[key] ?? key.charAt(0).toUpperCase() + key.slice(1).replaceAll("-", " ");
    });
  return [...new Set(labels)];
}

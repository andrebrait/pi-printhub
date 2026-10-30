// SPDX-License-Identifier: AGPL-3.0-only
import { apiFetch, errorMessage } from "./api.js";

const POLL_MS = 2000;
const GIVE_UP_MS = 30000;
const STORAGE = ["retention_days", "min_free_gb"];
const EMAIL = ["smtp_host", "smtp_port", "smtp_security", "smtp_user", "mail_from"];
const NUMBERS = new Set(["retention_days", "min_free_gb", "smtp_port"]); // the server rejects "587" for 587

const $ = (id) => document.getElementById(id);

/** The card's inline line: a message of the given Bootstrap kind, or hidden for no text. */
function note(card, text, kind = "danger") {
  const line = $(`${card}-msg`);
  line.textContent = text;
  line.className = `alert alert-${kind} mb-0 mt-3`;
  line.hidden = !text;
}

async function call(path, opts) {
  const res = await apiFetch(path, opts);
  if (!res.ok) throw new Error(await errorMessage(res));
  return res;
}

const get = async (path) => (await call(path)).json();

/** Run the card's save on submit, with Save disabled meanwhile and any error shown inline. */
function wire(card, save) {
  $(`${card}-form`).addEventListener("submit", async (e) => {
    e.preventDefault();
    $(`${card}-save`).disabled = true;
    note(card, "");
    try {
      await save();
    } catch (err) {
      note(card, err.message);
    } finally {
      $(`${card}-save`).disabled = false;
    }
  });
}

function fill(settings) {
  for (const key of [...STORAGE, ...EMAIL]) $(key).value = settings[key];
  $("smtp_password").value = ""; // never sent back; blank on save keeps the stored one
  $("smtp_password").placeholder = settings.smtp_password_set ? "Saved. Leave blank to keep it" : "";
}

const values = (keys) => Object.fromEntries(keys.map((k) => [k, NUMBERS.has(k) ? Number($(k).value) : $(k).value]));

async function saveSettings(card, body) {
  fill(await (await call("/api/settings", { method: "PUT", body })).json());
  note(card, "Saved", "success");
}

/** Shows the state of a sharing request; true while it is still waiting for the root script. */
function show({ requested, status, pending }) {
  if (pending) {
    note("system", "Applying…", "info");
    return true;
  }
  const failed = status?.state === "failed";
  const inForce = failed ? status : requested; // after a failure the switches show what really is on
  $("share").checked = inForce.share;
  $("expose_usb").checked = inForce.expose_usb;
  if (failed) note("system", `Not applied: ${status.error ?? "unknown error"}`);
  else note("system", status ? "Applied" : "", "success");
  return false;
}

let watch = 0;

async function follow(system) {
  const mine = ++watch;
  // shortcut: the 30 s is counted in poll intervals, so slow requests stretch it, upgrade: use Date.now() if that matters.
  for (let waited = 0; show(system); waited += POLL_MS) {
    if (waited >= GIVE_UP_MS) return note("system", "Not applied yet: is printhub-apply.path running? Re-run the installer.", "warning");
    await new Promise((resolve) => setTimeout(resolve, POLL_MS));
    system = await get("/api/settings/system");
    if (mine !== watch) return; // a newer save took over
  }
}

function main() {
  wire("storage", () => saveSettings("storage", values(STORAGE)));
  wire("email", () => {
    const body = values(EMAIL);
    if ($("smtp_password").value) body.smtp_password = $("smtp_password").value;
    return saveSettings("email", body);
  });
  $("test-email").addEventListener("click", async () => {
    const to = $("test-to").value;
    $("test-email").disabled = true;
    note("email", "");
    try {
      await call("/api/settings/test-email", { method: "POST", body: { to } });
      note("email", `Test email sent to ${to}`, "success");
    } catch (err) {
      note("email", err.message);
    } finally {
      $("test-email").disabled = false;
    }
  });
  wire("password", async () => {
    const next = $("new_password").value;
    if (next !== $("repeat_password").value) throw new Error("The new passwords do not match");
    await call("/api/settings/password", { method: "POST", body: { current: $("current_password").value, new: next } });
    for (const id of ["current_password", "new_password", "repeat_password"]) $(id).value = "";
    note("password", "Password changed", "success");
  });
  wire("system", async () => {
    const body = { share: $("share").checked, expose_usb: $("expose_usb").checked };
    await follow(await (await call("/api/settings/system", { method: "PUT", body })).json());
  });

  return Promise.all([get("/api/settings").then(fill), get("/api/settings/system").then(follow)]).catch((e) => {
    $("load-error").textContent = `Could not load settings: ${e.message}`;
    $("load-error").hidden = false;
  });
}

main();

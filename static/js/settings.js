// SPDX-License-Identifier: AGPL-3.0-only
import { apiFetch, errorMessage } from "./api.js";
import { KIND_LABELS, when } from "./shell.js";

const POLL_MS = 2000;
const GIVE_UP_MS = 120000; // printhub-apply.service's TimeoutStartSec: one apply may wait up to 30 s for the printer
const STORAGE = ["retention_days", "min_free_gb"];
const EMAIL = ["smtp_host", "smtp_port", "smtp_security", "smtp_user", "mail_from"];
const NUMBERS = new Set(["retention_days", "min_free_gb", "smtp_port"]); // the server rejects "587" for 587
const SMTP_SERVER = ["smtp_host", "smtp_port", "smtp_user", "smtp_security"]; // changing these needs the password typed again
let loaded = {}; // the settings as last loaded or saved
const KINDS = Object.keys(KIND_LABELS);
const TYPE_LABELS = { email: "Email", telegram: "Telegram", webhook: "Webhook" };
const SECRET_PLACEHOLDER = "Saved. Leave blank to keep it";
// What each channel type takes, as the API names the fields. type: text (default), url, number, list (comma-separated), select,
// switch or secret (never sent back; blank keeps the saved one). own: only for an email channel with its own SMTP server.
const CHANNEL_FIELDS = {
  email: [
    { key: "to", label: "Recipients", type: "list", required: true, help: "One or more addresses, separated by commas." },
    { key: "use_scan_server", label: "Use the scan email server", type: "switch", default: true, help: "Sends with the server and From of the Email card, as they are when a message goes out." },
    { key: "smtp_host", label: "SMTP host", own: true },
    { key: "smtp_port", label: "Port", type: "number", own: true, default: 587 },
    { key: "smtp_security", label: "Security", type: "select", own: true, default: "starttls", options: [["starttls", "STARTTLS"], ["tls", "SSL/TLS"]] },
    { key: "smtp_user", label: "User", own: true },
    { key: "smtp_password", label: "Password", type: "secret", own: true },
    { key: "mail_from", label: "From", own: true, placeholder: "Printer <print@example.com>", help: "An address, or a name and an address, as recipients see it." },
  ],
  telegram: [
    { key: "bot_token", label: "Bot token", type: "secret" },
    { key: "chat_id", label: "Chat ID", required: true, help: "A number, or @channelname." },
    { key: "message_thread_id", label: "Message thread ID", type: "number", help: "Optional; for forum topics." },
    { key: "parse_mode", label: "Parse mode", type: "select", default: "MarkdownV2", options: [["", "None"], ["HTML", "HTML"], ["Markdown", "Markdown"], ["MarkdownV2", "MarkdownV2"]] },
    { key: "disable_notification", label: "Send silently", type: "switch" },
    { key: "protect_content", label: "Protect content", type: "switch" },
    { key: "disable_web_page_preview", label: "Disable link preview", type: "switch" },
  ],
  webhook: [
    { key: "url", label: "URL", type: "url", required: true, help: "http or https. Receives a JSON POST." },
    { key: "header_name", label: "Header name (optional)", placeholder: "Authorization" },
    { key: "header_value", label: "Header value (optional)", type: "secret", placeholder: "Bearer …" },
  ],
};
// What each type with templates has besides them: telegram a header, email a subject prefix and a format. Each has help per syntax.
const TELEGRAM_SYNTAX = {
  MarkdownV2: "MarkdownV2: *bold*, _italic_, __underline__, ~strikethrough~, *_bold italic_*, [text](url). Two asterisks are not bold. Escape these with a backslash when you mean them literally: _ * [ ] ( ) ~ ` > # + - = | { } . !",
  HTML: 'HTML: <b>bold</b>, <i>italic</i>, <u>underline</u>, <s>strikethrough</s>, <a href="url">text</a>.',
  Markdown: "Markdown (legacy): *bold*, _italic_, [text](url).",
  "": "Plain text: no formatting.",
};
const EMAIL_SYNTAX = {
  html: 'Use HTML tags such as <b>bold</b>, <i>italic</i> and <a href="…">links</a>.',
  text: "",
};
const MESSAGE_HEAD = {
  telegram: { key: "header", label: "Header", max: 200, syntax: TELEGRAM_SYNTAX, typed: "Your text is sent as typed in the chosen parse mode; the values of the placeholders are escaped." },
  email: { key: "subject_prefix", label: "Subject prefix", max: 100, syntax: EMAIL_SYNTAX, typed: "Your text is sent as typed; the values of the placeholders are escaped." },
};
let editing = null; // the saved channel the form edits; null while it adds one
let inputs = {}; // the open form's field inputs by key
let kindBoxes = {};
let defaults = null; // what GET /api/notify/defaults gave: the message options per Telegram parse mode and email format
let message = null; // the open form's message option inputs: { key, input, areas: { kind: textarea } }; null when the type has none

const $ = (id) => document.getElementById(id);

/** An inline line, or an empty one for no text. It stays rendered so screen readers announce text put into it later. */
function setNote(line, text, kind = "danger") {
  line.setAttribute("role", kind === "danger" ? "alert" : "status");
  line.textContent = text;
  line.className = text ? `alert alert-${kind} mb-0 mt-3` : "";
}

const note = (card, text, kind) => setNote($(`${card}-msg`), text, kind);

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
  loaded = settings;
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
function show({ requested, status, pending }, announce) {
  // A request still being applied is what the switches show: a Save here must not undo it.
  $("share").checked = requested.share;
  $("expose_usb").checked = requested.expose_usb;
  if (pending) {
    note("system", "Applying…", "info");
    return true;
  }
  const failed = status?.state === "failed";
  const inForce = failed ? status : requested; // after a failure the switches show what really is on
  $("share").checked = inForce.share;
  $("expose_usb").checked = inForce.expose_usb;
  if (failed) note("system", `Not applied yet: ${plainReason(status.error ?? "")}`, "warning");
  else note("system", announce ? "Applied" : "", "success"); // an old status on load is not news
  return false;
}

let watch = 0;

async function follow(system, saved = false) {
  const mine = ++watch;
  let announce = saved;
  let lastError = "";
  // shortcut: the 120 s is counted in poll intervals, so slow requests stretch it, upgrade: use Date.now() if that matters.
  for (let waited = 0; show(system, announce); waited += POLL_MS) {
    if (waited >= GIVE_UP_MS) {
      const why = lastError && ` The last status check failed: ${lastError}`;
      return note("system", `Not applied after 2 minutes: is printhub-apply.path running? Re-run the installer.${why}`, "warning");
    }
    announce = true; // a request that was waiting ends in a result worth stating
    await new Promise((resolve) => setTimeout(resolve, POLL_MS));
    try {
      system = await get("/api/settings/system");
      lastError = "";
    } catch (err) {
      lastError = err.message; // the web app may be restarting under this very request: keep waiting
    }
    if (mine !== watch) return; // a newer save took over
  }
}

/** The apply script's error in plain words, as the watcher words it in the notification. */
function plainReason(error) {
  if (error.includes("printhub-printer.service")) return "the printer is not answering";
  return error.split("\n")[0].slice(0, 200) || "unknown error";
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function button(text, label, icon, tone = "btn-outline-secondary") {
  const node = el("button", `btn btn-sm ${tone}`, text);
  node.type = "button";
  node.setAttribute("aria-label", label);
  node.dataset.icon = icon;
  return node;
}

/** A labelled input for one channel field, from the saved channel's value or the field's default. */
function fieldBox(f, channel) {
  const id = `ch-${f.key}`;
  const stored = channel && f.key in channel ? channel[f.key] : f.default;
  const switched = f.type === "switch";
  const label = el("label", switched ? "form-check-label" : "form-label", f.label);
  label.htmlFor = id;
  const input = el(f.type === "select" ? "select" : "input", switched ? "form-check-input" : f.type === "select" ? "form-select" : "form-control");
  if (f.type === "select") input.append(...f.options.map(([value, text]) => Object.assign(el("option", "", text), { value })));
  else input.type = { switch: "checkbox", secret: "password", number: "number", url: "url" }[f.type] ?? "text";
  input.id = id;
  input.required = Boolean(f.required);
  input.autocomplete = f.type === "secret" ? "new-password" : "off";
  input.placeholder = f.type === "secret" && channel?.[`${f.key}_set`] ? SECRET_PLACEHOLDER : f.placeholder ?? "";
  if (switched) {
    input.checked = Boolean(stored);
    input.setAttribute("role", "switch");
  } else {
    input.value = f.type === "list" ? (stored ?? []).join(", ") : stored ?? "";
  }
  const box = el("div", switched ? "form-check form-switch mb-3" : "mb-3");
  box.append(...(switched ? [input, label] : [label, input]));
  if (f.help) {
    const help = el("div", "form-text", f.help);
    help.id = `${id}-help`;
    input.setAttribute("aria-describedby", help.id);
    box.append(help);
  }
  return { box, input };
}

function renderFields(channel) {
  const fields = CHANNEL_FIELDS[$("channel-type").value];
  const built = fields.map((f) => fieldBox(f, channel));
  inputs = Object.fromEntries(fields.map((f, i) => [f.key, built[i].input]));
  $("channel-fields").replaceChildren(...built.map((b) => b.box));
  renderAdvanced(channel);
  const hideOwn = () => fields.forEach((f, i) => { if (f.own) built[i].box.hidden = inputs.use_scan_server.checked; });
  inputs.use_scan_server?.addEventListener("change", hideOwn);
  hideOwn();
}

/** The collapsed "Advanced options": the header or subject prefix and one template per event kind. */
function renderAdvanced(channel) {
  const head = MESSAGE_HEAD[$("channel-type").value];
  if (!head || !defaults) {
    message = null;
    return $("channel-advanced").replaceChildren();
  }
  const input = el("input", "form-control");
  input.type = "text";
  input.id = `ch-${head.key}`;
  input.maxLength = head.max;
  input.autocomplete = "off";
  const label = el("label", "form-label", head.label);
  label.htmlFor = input.id;
  const first = el("div", "mb-3");
  first.append(label, input);
  let format = null; // the email format select; the syntax help follows it, or Telegram's parse mode
  if ($("channel-type").value === "email") {
    format = el("select", "form-select");
    format.id = "ch-format";
    format.append(...[["html", "HTML"], ["text", "Plain text"]].map(([value, text]) => Object.assign(el("option", "", text), { value })));
    format.value = "html";
    const formatLabel = el("label", "form-label", "Format");
    formatLabel.htmlFor = format.id;
    const formatBox = el("div", "mb-3");
    formatBox.append(formatLabel, format);
    first.append(formatBox);
  }
  const areas = {};
  const boxes = KINDS.map((k) => {
    const area = areas[k] = el("textarea", "form-control font-monospace");
    area.id = `ch-template-${k}`;
    area.rows = 3;
    area.maxLength = 1000;
    const areaLabel = el("label", "form-label", KIND_LABELS[k]);
    areaLabel.htmlFor = area.id;
    const box = el("div", "mb-3");
    box.append(areaLabel, area);
    return box;
  });
  // The defaults for the parse mode or email format now chosen, in the shape of a channel's message options.
  const modeDefaults = () => (format ? defaults.email[format.value] : defaults.telegram[inputs.parse_mode.value || "None"]);
  let shown; // the defaults the fields were last filled from or swapped to
  const fillFrom = (source) => {
    if (format) format.value = source.format ?? format.value;
    shown = modeDefaults();
    input.value = source[head.key] ?? shown[head.key];
    for (const k of KINDS) areas[k].value = source.templates?.[k] ?? shown.templates[k];
    showHelp();
  };
  // A field still holding the old mode's default takes the new mode's; one the owner edited stays.
  const swapDefaults = () => {
    const next = modeDefaults();
    if (input.value === shown[head.key]) input.value = next[head.key];
    for (const k of KINDS) if (areas[k].value === shown.templates[k]) areas[k].value = next.templates[k];
    shown = next;
    showHelp();
  };
  const reset = button("Reset to defaults", "Reset to defaults", "refresh");
  reset.addEventListener("click", () => fillFrom(modeDefaults()));
  const help = el("p", "form-text");
  const showHelp = () => {
    const mode = (format ?? inputs.parse_mode).value;
    const typed = mode === "text" ? "" : head.typed; // a plain text email escapes nothing
    help.textContent = ["Placeholders: {title}, {detail}, {link}, {time}.", head.syntax[mode], typed, "Empty lines are dropped."].filter(Boolean).join(" ");
  };
  const details = el("details", "mb-3");
  details.append(el("summary", "mb-2", "Advanced options"), first, ...boxes, help, reset);
  $("channel-advanced").replaceChildren(details);
  message = { key: head.key, input, areas, format };
  inputs.parse_mode?.addEventListener("change", swapDefaults);
  format?.addEventListener("change", swapDefaults);
  fillFrom(channel ?? modeDefaults());
}

function openChannel(channel = null) {
  editing = channel;
  $("channel-title").textContent = channel ? `Edit ${channel.name}` : "Add channel";
  $("channel-name").value = channel?.name ?? "";
  $("channel-type").value = channel?.type ?? "email";
  $("channel-type").disabled = Boolean(channel); // a saved channel keeps its type: its secret belongs to it
  kindBoxes = {};
  $("channel-kinds").replaceChildren(...KINDS.map((k) => {
    const input = el("input", "form-check-input");
    input.type = "checkbox";
    input.id = `ch-kind-${k}`;
    input.checked = (channel?.kinds ?? KINDS).includes(k);
    kindBoxes[k] = input;
    const label = el("label", "form-check-label", KIND_LABELS[k]);
    label.htmlFor = input.id;
    const box = el("div", "form-check");
    box.append(input, label);
    return box;
  }));
  renderFields(channel);
  note("channel", "");
  note("channels", "");
  $("channel-form").hidden = false;
  $("channel-name").focus();
}

function closeChannel() {
  editing = null;
  $("channel-form").hidden = true;
  $("channel-add").focus();
}

/** The API body for the open form. A blank secret is left out when editing, which keeps the saved one. */
function channelBody() {
  const type = $("channel-type").value;
  const body = { name: $("channel-name").value, type, kinds: KINDS.filter((k) => kindBoxes[k].checked) };
  if (!editing) body.enabled = true;
  for (const f of CHANNEL_FIELDS[type]) {
    const { value, checked } = inputs[f.key];
    if (f.own && inputs.use_scan_server.checked) continue;
    if (f.type === "switch") body[f.key] = checked;
    else if (f.type === "secret") {
      if (value || !editing) body[f.key] = value;
    } else if (f.type === "list") body[f.key] = value.split(",").map((s) => s.trim()).filter(Boolean);
    else if (f.type === "number") body[f.key] = value === "" ? null : Number(value);
    else if (f.type === "select") body[f.key] = value || null;
    else body[f.key] = value.trim();
  }
  if (message) {
    body[message.key] = message.input.value;
    if (message.format) body.format = message.format.value;
    body.templates = Object.fromEntries(KINDS.map((k) => [k, message.areas[k].value]));
  }
  return body;
}

function channelRow(c) {
  const row = el("li", "list-group-item");
  const head = el("div", "d-flex flex-wrap align-items-center gap-2");
  const enabled = el("div", "form-check form-switch mb-0");
  const toggle = el("input", "form-check-input");
  toggle.type = "checkbox";
  toggle.id = `ch-on-${c.id}`;
  toggle.checked = c.enabled;
  toggle.setAttribute("role", "switch");
  toggle.setAttribute("aria-label", `Enabled: ${c.name}`);
  const toggleLabel = el("label", "form-check-label", "Enabled");
  toggleLabel.htmlFor = toggle.id;
  enabled.append(toggle, toggleLabel);
  toggle.addEventListener("change", async () => {
    try {
      await call(`/api/notify/channels/${c.id}`, { method: "PUT", body: { enabled: toggle.checked } });
      note("channels", "");
    } catch (err) {
      toggle.checked = !toggle.checked;
      note("channels", err.message);
    }
  });
  head.append(el("strong", "me-auto text-break", c.name), el("span", "badge text-bg-secondary", TYPE_LABELS[c.type] ?? c.type), enabled);
  const events = c.kinds.length === KINDS.length ? "All events" : c.kinds.map((k) => KIND_LABELS[k] ?? k).join(", ") || "No events";

  const edit = button("Edit", `Edit ${c.name}`, "edit");
  edit.addEventListener("click", () => openChannel(c));
  const remove = button("Delete", `Delete ${c.name}`, "delete", "btn-outline-danger");
  remove.addEventListener("click", async () => {
    if (!confirm(`Delete channel ${c.name}?`)) return;
    try {
      await call(`/api/notify/channels/${c.id}`, { method: "DELETE" });
      if (editing?.id === c.id) closeChannel();
      note("channels", `Deleted ${c.name}`, "success");
      await loadChannels();
    } catch (err) {
      note("channels", err.message);
    }
  });
  // Its own form, like the scan test email: the result appears under the button.
  const test = el("form");
  const send = button("Send test", `Send test to ${c.name}`, "send", "btn-outline-primary");
  send.type = "submit";
  test.append(send);
  const result = el("div", "small text-body-secondary text-break", c.result ?? "Nothing sent yet");
  const line = el("div");
  line.setAttribute("role", "status");
  test.addEventListener("submit", async (e) => {
    e.preventDefault();
    send.disabled = true;
    send.textContent = "Sending…";
    setNote(line, `Sending a test to ${c.name}…`, "info");
    try {
      await call(`/api/notify/channels/${c.id}/test`, { method: "POST" });
      setNote(line, `Test sent to ${c.name}`, "success");
    } catch (err) {
      setNote(line, err.message);
    }
    // The server records the outcome either way. The row stays put (the focus is on its button); only its result line changes.
    try {
      result.textContent = (await get("/api/notify/channels")).find((x) => x.id === c.id)?.result ?? result.textContent;
    } catch {
      // The line above already says how the test went.
    } finally {
      send.disabled = false;
      send.textContent = "Send test";
    }
  });
  const actions = el("div", "d-flex flex-wrap gap-2 mt-2");
  actions.append(edit, remove, test);
  row.append(head, el("div", "small text-body-secondary", events), result, actions, line);
  return row;
}

function silenceRow(s) {
  const what = s.match === "kind" ? `All ${KIND_LABELS[s.value] ?? s.value}` : `“${s.value}”`;
  const row = el("li", "list-group-item d-flex flex-wrap align-items-center justify-content-between gap-2");
  const undo = button("Unsilence", `Unsilence ${what}`, "notifications");
  undo.addEventListener("click", async () => {
    try {
      await call(`/api/notify/silences/${s.id}`, { method: "DELETE" });
      note("silences", "");
      await loadSilences();
    } catch (err) {
      note("silences", err.message);
    }
  });
  row.append(el("span", "text-break min-w-0", `${what} until ${when(s.until)}`), undo);
  return row;
}

async function loadChannels() {
  try {
    const channels = await get("/api/notify/channels");
    $("channels").replaceChildren(...channels.map(channelRow));
    $("channels-empty").hidden = channels.length > 0;
  } catch (err) {
    note("channels", `Could not load the channels: ${err.message}`);
  }
}

async function loadSilences() {
  try {
    const silences = await get("/api/notify/silences");
    $("silences").replaceChildren(...silences.map(silenceRow));
    $("silences-empty").hidden = silences.length > 0;
  } catch (err) {
    note("silences", `Could not load the silences: ${err.message}`);
  }
}

function main() {
  wire("storage", () => saveSettings("storage", values(STORAGE)));
  wire("email", () => {
    const body = values(EMAIL);
    if ($("smtp_password").value) body.smtp_password = $("smtp_password").value;
    // The server checks this too; saying it here spares the round trip.
    else if (SMTP_SERVER.some((k) => body[k] !== loaded[k])) throw new Error("Enter the SMTP password again to change the server settings");
    return saveSettings("email", body);
  });
  $("test-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const to = $("test-to").value, button = $("test-email");
    button.disabled = true;
    button.textContent = "Sending…";
    note("test", `Sending a test email to ${to}…`, "info");
    try {
      await call("/api/settings/test-email", { method: "POST", body: { to } });
      note("test", `Test email sent to ${to}`, "success");
    } catch (err) {
      note("test", err.message);
    } finally {
      button.disabled = false;
      button.textContent = "Send test email";
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
    await follow(await (await call("/api/settings/system", { method: "PUT", body })).json(), true);
  });
  $("channel-add").addEventListener("click", () => openChannel());
  $("channel-cancel").addEventListener("click", closeChannel);
  $("channel-type").addEventListener("change", () => renderFields(null));
  wire("channel", async () => {
    await call(editing ? `/api/notify/channels/${editing.id}` : "/api/notify/channels", { method: editing ? "PUT" : "POST", body: channelBody() });
    closeChannel();
    note("channels", "Saved", "success");
    await loadChannels();
  });
  document.addEventListener("silences-changed", loadSilences); // silenced from the bell

  return Promise.all([get("/api/settings").then(fill), get("/api/settings/system").then((system) => follow(system)), get("/api/notify/defaults").then((d) => { defaults = d; }), loadChannels(), loadSilences()]).catch((e) => {
    $("load-error").textContent = `Could not load settings: ${e.message}`;
    $("load-error").hidden = false;
  });
}

main();

// SPDX-License-Identifier: AGPL-3.0-only
// The Home page's printer cards: state, problems and supplies of each printer /api/status lists.
import { LEVEL_CLASS, STATUS_EVENT, node, printerState, unavailableText } from "./shell.js";

/** One printer as a card in a grid column. */
export function printerCard(document, p) {
  const make = (tag, className, text) => node(document, tag, className, text);
  const { word, icon, level } = printerState(p);
  const glyph = make("span", `fs-3 lh-1 ${LEVEL_CLASS[level]}`);
  glyph.dataset.icon = icon;
  glyph.setAttribute("aria-hidden", "true");
  const title = make("div", "min-w-0 flex-grow-1");
  title.append(make("h3", "h6 mb-0 text-break", p.name));
  if (p.model && p.model !== p.name) title.append(make("div", "small text-body-secondary text-break", p.model));
  const head = make("div", "d-flex align-items-start gap-2");
  head.append(glyph, title, make("strong", `text-end ${LEVEL_CLASS[level]}`, word));
  const body = make("div", "card-body d-grid gap-3 align-content-start");
  body.append(head);

  if (p.problems.length) {
    const list = make("ul", "list-unstyled mb-0 small");
    list.append(...p.problems.map((x) => make("li", x.level === "error" ? "text-danger-emphasis" : "text-warning-emphasis", x.label)));
    body.append(list);
  }

  for (const s of p.supplies) {
    const value = make("span", "text-nowrap", s.level == null ? "?" : `${s.level}%`);
    if (s.low) value.append(make("span", "badge text-bg-warning ms-2", "low"));
    const line = make("div", "d-flex justify-content-between align-items-baseline gap-2 small");
    line.append(make("span", "", s.name), value);
    const meter = make("div", `supply-meter${s.level == null ? " unknown" : ""}`);
    if (s.level == null) {
      // A meter needs a value; an unknown level is a picture that says so.
      meter.setAttribute("role", "img");
      meter.setAttribute("aria-label", `${s.name}: unknown`);
    } else {
      meter.setAttribute("role", "meter");
      meter.setAttribute("aria-label", s.name);
      meter.setAttribute("aria-valuemin", "0");
      meter.setAttribute("aria-valuemax", "100");
      meter.setAttribute("aria-valuenow", String(s.level));
      meter.setAttribute("aria-valuetext", `${s.level}%`);
      meter.style.setProperty("--level", `${s.level}%`);
    }
    if (s.color) meter.style.setProperty("--color", s.color);
    const row = make("div", "supply");
    row.append(line, meter);
    body.append(row);
  }

  const actions = make("div", "d-flex flex-wrap gap-2");
  const jobs = make("a", "btn btn-outline-secondary btn-sm", "Jobs");
  jobs.href = "/jobs.html";
  actions.append(jobs);
  if (/^https?:\/\//i.test(p.web_page)) {
    const page = make("a", "btn btn-outline-secondary btn-sm", "Open printer page");
    page.href = p.web_page;
    page.setAttribute("target", "_blank");
    page.setAttribute("rel", "noopener");
    actions.append(page);
  }
  body.append(actions);

  const card = make("article", "card h-100");
  card.append(body);
  const column = make("div", "col-md-6 col-lg-4");
  column.append(card);
  return column;
}

/** Keeps `list` in step with the shell's status events; it is rebuilt only when the printers changed, so focus stays put. */
export function watchPrinters(document, list) {
  let shown;
  document.addEventListener(STATUS_EVENT, ({ detail: status }) => {
    const key = JSON.stringify(status.printers ?? unavailableText(status));
    if (key === shown) return;
    shown = key;
    list.replaceChildren(...(status.printers?.length
      ? status.printers.map((p) => printerCard(document, p))
      : [node(document, "p", "col-12 text-body-secondary mb-0", unavailableText(status))]));
  });
}

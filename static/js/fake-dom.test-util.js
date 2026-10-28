// SPDX-License-Identifier: AGPL-3.0-only
// Just enough DOM for rendering helpers under node --test. innerHTML throws: text must go in as text.

class FakeElement {
  constructor(tagName) {
    this.tagName = tagName.toUpperCase();
    this.children = [];
    this.attributes = {};
    this.dataset = {};
    this.listeners = {};
    this.className = "";
    this._text = "";
    this.hidden = false;
    this.disabled = false;
    this.style = { setProperty() {} };
  }
  get textContent() {
    return this._text + this.children.map((c) => (typeof c === "string" ? c : c.textContent)).join("");
  }
  set textContent(value) {
    this._text = String(value);
    this.children = [];
  }
  set innerHTML(_) {
    throw new Error("innerHTML used");
  }
  get href() {
    return this.attributes.href;
  }
  set href(value) {
    this.attributes.href = value;
  }
  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }
  append(...nodes) {
    this.children.push(...nodes);
  }
  replaceChildren(...nodes) {
    this._text = "";
    this.children = nodes;
  }
  addEventListener(type, fn) {
    this.listeners[type] = fn;
  }
  scrollIntoView() {
    this.scrolled = (this.scrolled ?? 0) + 1;
  }
  get classList() {
    const el = this;
    return {
      add: (...names) => (el.className = [...new Set([...el.className.split(" ").filter(Boolean), ...names])].join(" ")),
      contains: (name) => el.className.split(" ").includes(name),
      toggle: (name, on) => (on ? el.classList.add(name) : (el.className = el.className.split(" ").filter((c) => c !== name).join(" "))),
    };
  }
}

export const fakeDocument = () => ({
  createElement: (tag) => new FakeElement(tag),
  createTextNode: (text) => String(text),
});

export const textOf = (el) => (typeof el === "string" ? el : el.textContent);

export function find(el, predicate) {
  if (typeof el === "string") return null;
  if (predicate(el)) return el;
  for (const child of el.children) {
    const hit = find(child, predicate);
    if (hit) return hit;
  }
  return null;
}

export function findAll(el, predicate, out = []) {
  if (typeof el === "string") return out;
  if (predicate(el)) out.push(el);
  for (const child of el.children) findAll(child, predicate, out);
  return out;
}

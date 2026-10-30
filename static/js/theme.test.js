// SPDX-License-Identifier: AGPL-3.0-only
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";

const SOURCE = readFileSync(new URL("theme.js", import.meta.url), "utf8");

/** Runs theme.js against a fake page; `stored` is the saved choice, or "throws" for blocked storage. */
function load({ systemDark, stored }) {
  const page = { theme: null, saved: stored, media: { matches: systemDark, change: null } };
  const media = { get matches() { return page.media.matches; }, addEventListener(type, handler) {
    assert.equal(type, "change"); page.media.change = handler;
  } };
  const window = {};
  runInNewContext(SOURCE, {
    window,
    matchMedia(query) { assert.equal(query, "(prefers-color-scheme: dark)"); return media; },
    localStorage: {
      getItem(key) { assert.equal(key, "printhub-theme"); if (stored === "throws") throw new Error("blocked"); return page.saved ?? null; },
      setItem(key, value) { assert.equal(key, "printhub-theme"); if (stored === "throws") throw new Error("blocked"); page.saved = value; },
    },
    document: { documentElement: { setAttribute(name, value) {
      assert.equal(name, "data-bs-theme"); page.theme = value;
    } } },
  });
  return { page, theme: window.printhubTheme };
}

test("Auto follows the system immediately and when it changes", () => {
  for (const initial of [true, false]) {
    const { page, theme } = load({ systemDark: initial });
    assert.equal(theme.get(), "auto");
    assert.equal(page.theme, initial ? "dark" : "light");
    page.media.matches = !initial;
    page.media.change();
    assert.equal(page.theme, initial ? "light" : "dark");
  }
});

test("a stored Light or Dark wins over the system, also when the system changes", () => {
  for (const choice of ["light", "dark"]) {
    const { page, theme } = load({ systemDark: choice === "light", stored: choice });
    assert.equal(theme.get(), choice);
    assert.equal(page.theme, choice);
    page.media.matches = choice !== "light";
    page.media.change();
    assert.equal(page.theme, choice);
  }
});

test("a stored value that is not a theme means Auto", () => {
  const { page, theme } = load({ systemDark: true, stored: "purple" });
  assert.equal(theme.get(), "auto");
  assert.equal(page.theme, "dark");
});

test("set stores and applies the choice; Auto goes back to the system", () => {
  const { page, theme } = load({ systemDark: true });
  theme.set("light");
  assert.deepEqual([theme.get(), page.saved, page.theme], ["light", "light", "light"]);
  theme.set("auto");
  assert.deepEqual([theme.get(), page.saved, page.theme], ["auto", "auto", "dark"]);
});

test("set with garbage stores and applies Auto", () => {
  const { page, theme } = load({ systemDark: true, stored: "light" });
  theme.set("garbage");
  assert.deepEqual([theme.get(), page.saved, page.theme], ["auto", "auto", "dark"]);
});

test("blocked storage does not break reading or switching", () => {
  const { page, theme } = load({ systemDark: false, stored: "throws" });
  assert.equal(page.theme, "light");
  theme.set("dark");
  assert.deepEqual([theme.get(), page.theme], ["dark", "dark"]);
});

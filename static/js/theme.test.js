// SPDX-License-Identifier: AGPL-3.0-only
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";

test("theme follows the system immediately and when it changes", () => {
  for (const initial of [true, false]) {
    let change, theme;
    const media = { matches: initial, addEventListener(type, handler) {
      assert.equal(type, "change"); change = handler;
    } };
    runInNewContext(readFileSync(new URL("theme.js", import.meta.url), "utf8"), {
      matchMedia(query) { assert.equal(query, "(prefers-color-scheme: dark)"); return media; },
      document: { documentElement: { setAttribute(name, value) {
        assert.equal(name, "data-bs-theme"); theme = value;
      } } },
    });
    assert.equal(theme, initial ? "dark" : "light");
    media.matches = !initial;
    change();
    assert.equal(theme, initial ? "light" : "dark");
  }
});

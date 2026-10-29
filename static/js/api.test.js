// SPDX-License-Identifier: AGPL-3.0-only
import test from "node:test";
import assert from "node:assert/strict";
import { safeNext } from "./api.js";

const origin = "https://print.example.com";

test("safeNext keeps same-site paths", () => {
  assert.equal(safeNext("/print.html?doc=x", origin), "/print.html?doc=x");
  assert.equal(safeNext("/a?b=1#c", origin), "/a?b=1#c");
  assert.equal(safeNext("https://print.example.com/x", origin), "/x");
});

test("safeNext falls back to / for anything that leaves the site", () => {
  for (const bad of [
    "//evil.example",
    "/\\evil.example",
    "/\t/evil.example",
    "/\n/evil.example",
    "/\r/evil.example",
    "/.//evil.example",
    "/a/..//evil.example",
    "/%2e//evil.example",
    "/x/../\\evil.example",
    "https://evil.example",
    "javascript:alert(1)",
    "",
    null,
    undefined,
  ]) {
    assert.equal(safeNext(bad, origin), "/", JSON.stringify(bad));
  }
});

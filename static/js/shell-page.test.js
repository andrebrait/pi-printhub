// SPDX-License-Identifier: AGPL-3.0-only
// shell.js as a page loads it: a document exists, but there is no #shell header host (the login page).
import test from "node:test";
import assert from "node:assert/strict";
import { fakeDocument, findAll } from "./fake-dom.test-util.js";

test("shell.js puts one GitHub footer on a page without a header host", async () => {
  const document = fakeDocument();
  document.body = document.createElement("body");
  document.getElementById = () => null;
  globalThis.document = document;
  try {
    await import("./shell.js");
  } finally {
    delete globalThis.document;
  }
  const footers = findAll(document.body, (el) => el.tagName === "FOOTER");
  assert.equal(footers.length, 1);
  assert.equal(findAll(footers[0], (el) => el.tagName === "A")[0].attributes.href, "https://github.com/andrebrait/pi-printhub");
});

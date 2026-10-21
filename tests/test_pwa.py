# SPDX-License-Identifier: AGPL-3.0-only
import json
import subprocess
from pathlib import Path

from printhub.convert import ACCEPTED

STATIC = Path(__file__).resolve().parent.parent / "static"

# Each harness loads sw.js with a stub ServiceWorker global and reports what the handlers did.
# The script path is the last argv entry; the fetch harness takes its requests JSON just before it.
PRECACHE_HARNESS = """
const fs = require("fs");
let onInstall;
let added;
globalThis.self = {
  location: { origin: "https://print.brait.dev" },
  addEventListener(type, fn) { if (type === "install") onInstall = fn; },
};
globalThis.caches = { open: async () => ({ addAll: async (list) => { added = list; } }) };
new Function(fs.readFileSync(process.argv.at(-1), "utf8"))();
let pending;
onInstall({ waitUntil: (p) => { pending = p; } });
pending.then(() => console.log(JSON.stringify(added)));
"""

FETCH_HARNESS = """
const fs = require("fs");
let onFetch;
globalThis.self = {
  location: { origin: "https://print.brait.dev" },
  addEventListener(type, fn) { if (type === "fetch") onFetch = fn; },
};
globalThis.caches = { match: async () => undefined };
globalThis.fetch = async () => undefined;
new Function(fs.readFileSync(process.argv.at(-1), "utf8"))();
const answered = JSON.parse(process.argv.at(-2)).map(([method, url]) => {
  let hit = false;
  onFetch({ request: { method, url }, respondWith: () => { hit = true; } });
  return hit;
});
console.log(JSON.stringify(answered));
"""


def run_sw(harness, *args):
    out = subprocess.run(
        ["node", "-e", harness, *args, str(STATIC / "sw.js")],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return json.loads(out)


def precache_paths():
    return run_sw(PRECACHE_HARNESS)


def test_manifest_parses_and_share_target_accepts_every_converter_type():
    manifest = json.loads((STATIC / "manifest.webmanifest").read_text())

    accept = manifest["share_target"]["params"]["files"][0]["accept"]
    assert set(ACCEPTED) <= set(accept)


def test_every_precached_path_exists_under_static():
    for path in precache_paths():
        rel = path.lstrip("/") or "index.html"
        assert (STATIC / rel).is_file(), path


def test_precache_skips_tests_manifests_and_dynamic_routes():
    paths = precache_paths()

    assert not any(p.endswith((".test.js", "package.json")) for p in paths)
    assert not any(p.startswith("/api/") or p == "/share" for p in paths)


def test_fetch_handler_answers_static_gets_and_passes_api_and_share_through():
    origin = "https://print.brait.dev"
    requests = [
        ["GET", f"{origin}/css/app.css"],
        ["GET", f"{origin}/api/printers"],
        ["POST", f"{origin}/share"],
        ["GET", f"{origin}/share"],
        ["GET", "https://evil.example/x.js"],
    ]

    answered = run_sw(FETCH_HARNESS, json.dumps(requests))

    assert answered == [True, False, False, False, False]

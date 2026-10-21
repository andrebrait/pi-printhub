# SPDX-License-Identifier: AGPL-3.0-only
import json
import subprocess
from pathlib import Path

from printhub.convert import ACCEPTED

STATIC = Path(__file__).resolve().parent.parent / "static"
ORIGIN = "https://print.brait.dev"
CACHE = "printhub-v1"

# Loads sw.js into a stub ServiceWorker: in-memory caches, a controllable fetch, and a dump() that
# prints the cache contents. Config arrives as JSON in the second-to-last argv entry.
PREAMBLE = """
const fs = require("fs");
const origin = "https://print.brait.dev";
const config = JSON.parse(process.argv.at(-2));
const handlers = {};
const fetched = [];
globalThis.self = {
  location: { origin },
  addEventListener: (type, fn) => { handlers[type] = fn; },
};
globalThis.Request = class {
  constructor(url, init = {}) {
    this.url = new URL(url, origin + "/").href;
    this.method = init.method ?? "GET";
    this.cache = init.cache ?? "default";
  }
};
const store = new Map(
  Object.entries(config.caches ?? {}).map(([name, entries]) => [name, new Map(Object.entries(entries))]),
);
globalThis.caches = {
  open: async (name) => {
    if (!store.has(name)) store.set(name, new Map());
    const entries = store.get(name);
    return {
      addAll: async (requests) => {
        for (const r of requests) entries.set(new URL(r.url).pathname, `precached:${r.cache}`);
      },
      put: async (key, response) => {
        if (config.failPut) throw new Error("quota exceeded");
        entries.set(new URL(key, origin).pathname, await response.text());
      },
    };
  },
  keys: async () => [...store.keys()],
  delete: async (name) => store.delete(name),
  match: async (request) => {
    const path = new URL(request.url).pathname;
    for (const entries of store.values()) if (entries.has(path)) return new Response(entries.get(path));
    return undefined;
  },
};
globalThis.fetch = async (request) => {
  fetched.push(request.url);
  if (config.network == null) throw new TypeError("offline");
  return new Response(config.network);
};
new Function(fs.readFileSync(process.argv.at(-1), "utf8"))();
const dump = (result) => console.log(JSON.stringify({
  caches: Object.fromEntries([...store].map(([name, entries]) => [name, Object.fromEntries(entries)])),
  ...result,
}));
"""

LIFECYCLE = """
let pending;
handlers[config.event]({ waitUntil: (p) => { pending = p; } });
pending.then(() => dump({}));
"""

FETCH = """
(async () => {
  const results = [];
  for (const [method, url] of config.requests) {
    let respond = null;
    handlers.fetch({ request: new Request(url, { method }), respondWith: (p) => { respond = p; } });
    results.push({ answered: respond !== null, body: respond ? await (await respond).text() : null });
  }
  dump({ results, fetched });
})();
"""


def run_sw(body, **config):
    out = subprocess.run(
        ["node", "-e", PREAMBLE + body, json.dumps(config), str(STATIC / "sw.js")],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return json.loads(out)


def precache():
    return run_sw(LIFECYCLE, event="install", caches={})["caches"][CACHE]


def test_manifest_parses_and_share_target_accepts_every_converter_type():
    manifest = json.loads((STATIC / "manifest.webmanifest").read_text())

    accept = manifest["share_target"]["params"]["files"][0]["accept"]
    assert set(ACCEPTED) <= set(accept)


def test_every_precached_path_exists_under_static():
    for path in precache():
        rel = path.lstrip("/") or "index.html"
        assert (STATIC / rel).is_file(), path


def test_precache_skips_tests_manifests_and_dynamic_routes():
    paths = precache()

    assert not any(p.endswith((".test.js", "package.json")) for p in paths)
    assert not any(p.startswith("/api/") or p == "/share" for p in paths)


def test_precache_bypasses_the_http_cache():
    assert set(precache().values()) == {"precached:reload"}


def test_fresh_network_response_wins_over_cached_shell():
    out = run_sw(
        FETCH,
        requests=[["GET", f"{ORIGIN}/print.html"]],
        caches={CACHE: {"/print.html": "stale"}},
        network="fresh",
    )

    assert out["results"][0]["body"] == "fresh"
    assert out["caches"][CACHE]["/print.html"] == "fresh"


def test_cached_shell_answers_when_fetch_rejects():
    out = run_sw(
        FETCH,
        requests=[["GET", f"{ORIGIN}/print.html"]],
        caches={CACHE: {"/print.html": "stale"}},
        network=None,
    )

    assert out["results"][0]["body"] == "stale"


def test_vendor_files_are_served_cache_first_without_network():
    out = run_sw(
        FETCH,
        requests=[["GET", f"{ORIGIN}/vendor/cropper/cropper.min.js"]],
        caches={CACHE: {"/vendor/cropper/cropper.min.js": "cached"}},
        network="fresh",
    )

    assert out["results"][0]["body"] == "cached"
    assert out["fetched"] == []


def test_activate_deletes_old_caches():
    out = run_sw(LIFECYCLE, event="activate", caches={"printhub-v0": {"/print.html": "old"}, CACHE: {}})

    assert list(out["caches"]) == [CACHE]


def test_fetch_handler_answers_shell_gets_and_passes_api_and_share_through():
    requests = [
        ["GET", f"{ORIGIN}/css/app.css"],
        ["GET", f"{ORIGIN}/api/printers"],
        ["POST", f"{ORIGIN}/share"],
        ["GET", f"{ORIGIN}/share"],
        ["GET", "https://evil.example/x.js"],
    ]

    out = run_sw(FETCH, requests=requests, caches={}, network="fresh")

    assert [r["answered"] for r in out["results"]] == [True, False, False, False, False]


def test_cache_write_failure_does_not_discard_successful_network_response():
    for cached in ({"/print.html": "stale"}, {}):
        out = run_sw(
            FETCH, requests=[["GET", f"{ORIGIN}/print.html"]],
            caches={CACHE: cached}, network="fresh", failPut=True,
        )
        assert out["results"][0]["body"] == "fresh"

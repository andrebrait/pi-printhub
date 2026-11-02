# SPDX-License-Identifier: AGPL-3.0-only
import re
import tempfile

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

# Fixtures and helpers from the app tests; pytest registers imported fixtures.
from test_app import ORIGIN, asgi_post, fake_cups, latest_event_title, make_client, space  # noqa: F401

from printhub.auth import COOKIE, issue_session
from printhub.middleware import BROWSER_ONLY

INVALID = {"error": "Invalid API token"}
READ_ONLY = {"error": "This API token is read-only"}
NOT_ALLOWED = {"error": "Not allowed with an API token"}
UNAVAILABLE = {"error": "API tokens are unavailable"}
# Every API route needs an explicit token-access decision, including GETs and writes.
BROWSER = {
    ("POST", "/api/login"),
    ("POST", "/api/logout"),
    ("POST", "/api/settings/password"),
    ("POST", "/api/share/{token}/claim"),
    ("GET", "/api/tokens"),
    ("POST", "/api/tokens"),
    ("DELETE", "/api/tokens/{token_id}"),
}
TOKEN_ROUTES = {
    ("GET", "/api/openapi.json"),
    ("GET", "/api/printer"),
    ("GET", "/api/status"),
    ("GET", "/api/notifications"),
    ("POST", "/api/notifications/read"),
    ("POST", "/api/uploads"),
    ("POST", "/api/docs/{doc_id}/convert"),
    ("GET", "/api/docs"),
    ("GET", "/api/docs/{doc_id}"),
    ("PATCH", "/api/docs/{doc_id}"),
    ("DELETE", "/api/docs/{doc_id}"),
    ("GET", "/api/docs/{doc_id}/pages/{page_id}"),
    ("GET", "/api/docs/{doc_id}/preview/{n}"),
    ("GET", "/api/scanner"),
    ("POST", "/api/scans"),
    ("GET", "/api/docs/{doc_id}/export"),
    ("POST", "/api/docs/{doc_id}/print"),
    ("GET", "/api/jobs"),
    ("POST", "/api/jobs/{job_id}/cancel"),
    ("POST", "/api/docs/{doc_id}/email"),
    ("GET", "/api/recipients"),
    ("GET", "/api/settings"),
    ("PUT", "/api/settings"),
    ("POST", "/api/settings/test-email"),
    ("GET", "/api/settings/system"),
    ("PUT", "/api/settings/system"),
    ("GET", "/api/notify/defaults"),
    ("GET", "/api/notify/channels"),
    ("POST", "/api/notify/channels"),
    ("PUT", "/api/notify/channels/{channel_id}"),
    ("DELETE", "/api/notify/channels/{channel_id}"),
    ("POST", "/api/notify/channels/{channel_id}/test"),
    ("GET", "/api/notify/silences"),
    ("POST", "/api/notify/silences"),
    ("DELETE", "/api/notify/silences/{silence_id}"),
}


@pytest.fixture
def session(make_client, cfg):
    # Not through /api/login: a login counts against the same rate limit the token tests measure.
    client = make_client(login=False)
    client.headers["Cookie"] = f"{COOKIE}={issue_session(cfg)}"
    return client


def token_for(session, access, name="Script") -> str:
    r = session.post("/api/tokens", json={"name": name, "access": access})
    assert r.status_code == 201
    return r.json()["token"]


def bearer(session, token) -> TestClient:
    """Sends only the token: no cookie, no Origin."""
    return TestClient(session.app, base_url=ORIGIN, follow_redirects=False, headers={"Authorization": f"Bearer {token}"})


def api_routes(app) -> list[tuple[str, str]]:
    return [
        (method, route.path)
        for route in app.routes
        if isinstance(route, APIRoute) and route.path.startswith("/api/")
        for method in sorted(route.methods)
    ]


def concrete_path(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "x", path)


@pytest.mark.parametrize("access", ["read", "full"])
def test_a_token_authenticates_without_origin_or_cookie(session, access):
    r = bearer(session, token_for(session, access)).get("/api/status")
    assert r.status_code == 200


def test_using_a_token_records_last_used(session):
    bearer(session, token_for(session, "read")).get("/api/status")
    assert session.get("/api/tokens").json()[0]["last_used"] is not None


@pytest.mark.parametrize("header", ["Bearer ph_wrong", "Bearer ", "Bearer", "Basic dXNlcjpwYXNz", "ph_wrong", ""])
def test_a_bad_authorization_header_is_401_even_with_a_session(session, header):
    r = session.get("/api/status", headers={"Authorization": header})
    assert (r.status_code, r.json()) == (401, INVALID)


def test_a_revoked_token_is_401(session):
    token = token_for(session, "full")
    token_id = session.get("/api/tokens").json()[0]["id"]
    client = bearer(session, token)
    assert client.get("/api/status").status_code == 200
    assert session.delete(f"/api/tokens/{token_id}").status_code == 204
    r = client.get("/api/status")
    assert (r.status_code, r.json()) == (401, INVALID)


def test_failed_attempts_are_rate_limited_per_address_before_verifying(session):
    good = bearer(session, token_for(session, "read"))
    bad = bearer(session, "ph_wrong")
    assert [bad.get("/api/status").status_code for _ in range(5)] == [401] * 5

    r = bad.get("/api/status")
    assert (r.status_code, r.json()) == (429, {"error": "Too many attempts; wait a minute"})
    assert good.get("/api/status").status_code == 429  # a valid token is refused too: checked before verifying
    assert session.get("/api/status").status_code == 200  # sessions are not affected
    assert session.post("/api/login", json={"password": "secret"}).status_code == 429  # the login limit is shared


def test_a_read_token_is_refused_every_write_and_allowed_every_get(session):
    client = bearer(session, token_for(session, "read"))
    routes = [route for route in api_routes(session.app) if route not in BROWSER]
    assert {"GET", "POST", "PUT", "PATCH", "DELETE"} <= {method for method, _ in routes}

    wrong = []
    for method, path in routes:
        r = client.request(method, concrete_path(path))
        if method == "GET":
            ok = r.status_code not in (401, 403)
        else:
            ok = (r.status_code, r.json()) == (403, READ_ONLY)
        if not ok:
            wrong.append((method, path, r.status_code))
    assert wrong == []


def test_a_full_token_passes_every_route_except_the_browser_only_ones(session):
    client = bearer(session, token_for(session, "full"))
    wrong = []
    for method, path in api_routes(session.app):
        if (method, path) in BROWSER:
            continue
        r = client.request(method, concrete_path(path))
        if r.status_code in (401, 403):
            wrong.append((method, path, r.status_code))
    assert wrong == []


@pytest.mark.parametrize("access", ["read", "full"])
def test_browser_only_routes_refuse_any_token(session, access):
    client = bearer(session, token_for(session, access))
    assert BROWSER == {route for route in api_routes(session.app) if BROWSER_ONLY.fullmatch("{} {}".format(*route))}
    for method, path in sorted(BROWSER):
        r = client.request(method, concrete_path(path))
        assert (r.status_code, r.json()) == (403, NOT_ALLOWED), (method, path)


@pytest.mark.parametrize("path", ["/api/tokens/", "/api/tokens/x/y"])
def test_everything_under_api_tokens_is_browser_only(session, path):
    r = bearer(session, token_for(session, "full")).delete(path)
    assert (r.status_code, r.json()) == (403, NOT_ALLOWED)


@pytest.mark.parametrize("method", ["HEAD", "OPTIONS", "TRACE"])
def test_a_method_nobody_classified_is_refused_even_for_a_full_token(session, method):
    r = bearer(session, token_for(session, "full")).request(method, "/api/status")
    assert r.status_code == 403


def test_every_api_route_is_classified(session):
    assert set(api_routes(session.app)) == BROWSER | TOKEN_ROUTES


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_new_api_routes_need_an_explicit_token_decision(session, method):
    session.app.add_api_route("/api/unclassified", lambda: {}, methods=[method])
    with pytest.raises(AssertionError):
        test_every_api_route_is_classified(session)


@pytest.mark.parametrize("control", ["%0A", "%0D", "%00", "%09", "%7F"])
@pytest.mark.parametrize(("method", "path"), sorted(BROWSER))
def test_control_paths_cannot_reach_browser_only_handlers(session, cfg, method, path, control):
    client = bearer(session, token_for(session, "full"))
    assert client.get("/api/status").status_code == 200  # record last_used before the file snapshot
    records = session.get("/api/tokens").json()
    path = concrete_path(path).replace("/api/tokens/x", f"/api/tokens/{records[0]['id']}")
    before = (cfg.data_dir / "api-tokens.json").read_bytes()
    events = session.get("/api/notifications").json()
    password_hash = session.app.state.cfg.password_hash
    bodies = {
        "/api/tokens": {"name": "Unexpected", "access": "full"},
        "/api/login": {"password": "secret"},
        "/api/settings/password": {"current": "secret", "new": "a-new-password"},
    }

    r = client.request(method, path + control, json=bodies.get(path))

    assert (cfg.data_dir / "api-tokens.json").read_bytes() == before
    assert session.app.state.cfg.password_hash == password_hash
    assert session.get("/api/notifications").json() == events
    assert "set-cookie" not in r.headers
    assert (r.status_code, r.json()) == (400, {"error": "Invalid path"})


@pytest.mark.parametrize("control", ["%0A", "%0D", "%00", "%09", "%7F"])
def test_anonymous_share_with_a_control_path_is_invalid(make_client, control):
    client = make_client(login=False)
    r = client.post("/share" + control)
    assert (r.status_code, r.json()) == (400, {"error": "Invalid path"})


@pytest.mark.parametrize("control", ["\n", "\r", "\0", "\t", "\x7f"])
@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer ph_wrong"}, {"Authorization": "Basic wrong"}, {"Origin": "https://evil.example"}],
)
def test_path_validation_precedes_auth_origin_and_body_reads(session, cfg, control, headers):
    assert asgi_post(session.app, "/api/uploads" + control, headers) == (400, 0)
    assert asgi_post(session.app, "/share" + control, headers) == (400, 0)
    cookie = {"Cookie": f"{COOKIE}={issue_session(cfg)}"}
    assert asgi_post(session.app, "/api/uploads" + control, cookie) == (400, 0)


def test_a_read_token_upload_is_refused_before_the_body_is_read(session, monkeypatch, tmp_path):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "spool"))
    (tmp_path / "spool").mkdir()
    token = token_for(session, "read")

    assert asgi_post(session.app, "/api/uploads", {"Authorization": f"Bearer {token}"}) == (403, 0)
    assert asgi_post(session.app, "/api/uploads", {"Authorization": "Bearer ph_wrong"}) == (401, 0)
    assert list((tmp_path / "spool").iterdir()) == []


def test_a_full_token_upload_skips_origin_and_still_hits_the_body_cap(session, monkeypatch, tmp_path):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "spool"))
    (tmp_path / "spool").mkdir()
    token = token_for(session, "full")

    headers = {"Authorization": f"Bearer {token}", "Origin": "https://evil.example"}
    status, _ = asgi_post(session.app, "/api/uploads", headers)
    assert status == 413


def test_an_unreadable_token_file_is_503_and_not_a_failed_attempt(session, cfg):
    client = bearer(session, token_for(session, "full"))
    path = cfg.data_dir / "api-tokens.json"
    good = path.read_text()
    path.write_text("{not json")

    for _ in range(7):  # more than the failed-attempt limit
        r = client.get("/api/status")
        assert (r.status_code, r.json()) == (503, UNAVAILABLE)
    assert session.get("/api/status").status_code == 200
    for r in (session.get("/api/tokens"), session.post("/api/tokens", json={"name": "x", "access": "read"})):
        assert (r.status_code, r.json()) == (503, UNAVAILABLE)

    path.write_text(good)
    assert client.get("/api/status").status_code == 200


def test_tokens_are_created_listed_and_revoked_through_the_api(session, cfg):
    r = session.post("/api/tokens", json={"name": " CI ", "access": "full"})
    assert r.status_code == 201
    created = r.json()
    assert set(created) == {"id", "name", "access", "created", "token"}
    assert (created["name"], created["access"]) == ("CI", "full")
    assert created["token"].startswith("ph_")
    assert latest_event_title(session) == "API token CI created (full access)"

    read = session.post("/api/tokens", json={"name": "Home Assistant", "access": "read"}).json()
    assert latest_event_title(session) == "API token Home Assistant created (read-only)"

    listing = session.get("/api/tokens")
    assert listing.json()[0] == {
        "id": created["id"], "name": "CI", "access": "full", "created": created["created"], "last_used": None,
    }
    for secret in (created["token"], read["token"]):
        assert secret not in listing.text
        assert secret not in session.get("/api/notifications").text
        assert secret not in (cfg.data_dir / "api-tokens.json").read_text()

    assert session.delete(f"/api/tokens/{created['id']}").status_code == 204
    assert latest_event_title(session) == "API token CI revoked"
    assert [t["name"] for t in session.get("/api/tokens").json()] == ["Home Assistant"]
    assert session.delete(f"/api/tokens/{created['id']}").status_code == 404
    assert latest_event_title(session) == "API token CI revoked"


@pytest.mark.parametrize(
    "body",
    [
        {"name": "", "access": "read"},
        {"name": "   ", "access": "read"},
        {"name": "x" * 61, "access": "read"},
        {"name": "ok", "access": "admin"},
        {"name": "ok"},
        {"access": "read"},
        {"name": "ok", "access": "read", "extra": 1},
    ],
)
def test_an_invalid_token_request_is_400_and_creates_nothing(session, body):
    r = session.post("/api/tokens", json=body)
    assert r.status_code == 400
    assert r.json()["error"]
    assert session.get("/api/tokens").json() == []


def test_token_routes_need_a_session(make_client):
    anon = make_client(login=False)
    assert anon.get("/api/tokens").status_code == 401
    assert anon.post("/api/tokens", json={"name": "x", "access": "read"}).status_code == 401
    assert anon.delete("/api/tokens/x").status_code == 401

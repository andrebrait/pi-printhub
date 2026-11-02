# SPDX-License-Identifier: AGPL-3.0-only
import re
import tomllib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from pydantic import TypeAdapter

from test_api_tokens import bearer, session, token_for  # noqa: F401
from test_app import fake_cups, make_client, scan_doc, space, store  # noqa: F401


def resolve(document, schema):
    if "$ref" in schema:
        return document["components"]["schemas"][schema["$ref"].rsplit("/", 1)[1]]
    return schema


@pytest.fixture
def document(session):
    response = session.get("/api/openapi.json")
    assert response.status_code == 200
    return response.json()


def test_openapi_is_authenticated_and_available_to_read_tokens(make_client, session):
    assert make_client(login=False).get("/api/openapi.json").status_code == 401
    response = bearer(session, token_for(session, "read")).get("/api/openapi.json")
    assert response.status_code == 200
    assert response.json() == session.get("/api/openapi.json").json()
    assert session.get("/docs").status_code == 404
    assert session.get("/redoc").status_code == 404
    assert session.get("/openapi.json").status_code == 404


def test_every_api_operation_is_documented_with_real_success_and_error_shapes(session, document):
    expected = {(r.path, m.lower()) for r in session.app.routes if isinstance(r, APIRoute)
                and r.path.startswith("/api/") and r.path != "/api/openapi.json" for m in r.methods}
    actual = {(p, m) for p, methods in document["paths"].items() if p.startswith("/api/") for m in methods}
    assert actual == expected
    tags = {"Status", "Printing", "Jobs", "Documents", "Scanning", "Notifications", "Settings", "API tokens"}
    for path, method in expected:
        operation = document["paths"][path][method]
        assert set(operation["tags"]) <= tags
        assert operation["tags"] and operation["summary"] and operation["description"]
        for code, response in operation["responses"].items():
            if code == "204":
                assert "content" not in response
            elif code.startswith("2"):
                assert response["content"]
                for content in response["content"].values():
                    schema = resolve(document, content["schema"])
                    assert schema.get("properties") or schema.get("items") or schema.get("anyOf") or schema.get("format") == "binary"
            else:
                schema = resolve(document, response["content"]["application/json"]["schema"])
                assert schema["properties"]["error"]["type"] == "string"
        assert {"400", "401", "403", "413", "429", "503"} <= operation["responses"].keys()


def test_auth_metadata_matches_browser_only_contract(document):
    from printhub.auth import COOKIE
    assert document["openapi"].startswith("3.1.")
    assert document["info"]["title"] == "Print Hub API"
    try:
        expected_version = version("printhub")
    except PackageNotFoundError:
        expected_version = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())["project"]["version"]
    assert document["info"]["version"] == expected_version
    assert document["info"]["description"]
    schemes = document["components"]["securitySchemes"]
    assert schemes["bearerAuth"] == {"type": "http", "scheme": "bearer"}
    assert schemes["sessionCookie"] == {"type": "apiKey", "in": "cookie", "name": COOKIE}
    both = [{"bearerAuth": []}, {"sessionCookie": []}]
    assert document["security"] == both
    browser = {("post", "/api/login"), ("post", "/api/logout"), ("post", "/api/settings/password"),
               ("post", "/api/share/{token}/claim"), ("get", "/api/tokens"), ("post", "/api/tokens"),
               ("delete", "/api/tokens/{token_id}")}
    for path, methods in document["paths"].items():
        for method, operation in methods.items():
            assert operation["security"] == ([{"sessionCookie": []}] if (method, path) in browser else both)


def test_upload_and_binary_media_contracts(document):
    body = document["paths"]["/api/uploads"]["post"]["requestBody"]
    schema = resolve(document, body["content"]["multipart/form-data"]["schema"])
    assert schema["properties"]["file"]["type"] == "string"
    assert schema["properties"]["file"]["format"] == "binary"
    assert "file" in schema["required"]
    for path, media in [
        ("/api/docs/{doc_id}/pages/{page_id}", {"image/jpeg"}),
        ("/api/docs/{doc_id}/preview/{n}", {"image/png"}),
        ("/api/docs/{doc_id}/export", {"application/pdf", "image/jpeg", "application/zip"}),
    ]:
        content = document["paths"][path]["get"]["responses"]["200"]["content"]
        assert set(content) == media
        assert all(value["schema"] == {"type": "string", "format": "binary"} for value in content.values())


def variants(document, schema):
    schema = resolve(document, schema)
    return [resolve(document, item) for item in schema.get("anyOf", [schema])]


def test_notification_schema_describes_webhook_secrets_without_exposing_them(session, document):
    operation = document["paths"]["/api/notify/channels"]["post"]
    body = operation["requestBody"]["content"]["application/json"]["schema"]
    webhook = next(s for s in variants(document, body) if s["properties"]["type"].get("const") == "webhook")
    assert {"url", "header_name", "header_value"} <= webhook["properties"].keys()
    output = operation["responses"]["201"]["content"]["application/json"]["schema"]
    public = next(s for s in variants(document, output) if s["properties"]["type"].get("const") == "webhook")
    assert {"id", "name", "type", "enabled", "kinds", "url", "header_name", "header_value_set"} == public["properties"].keys()
    payload = {"name": "Automation", "type": "webhook", "enabled": True, "kinds": ["job"],
               "url": "https://example.com/hook", "header_name": "Authorization", "header_value": "secret", "ignored": True}
    response = session.post("/api/notify/channels", json=payload)
    assert response.status_code == 201
    channel = response.json()
    assert channel == {k: v for k, v in payload.items() if k not in {"header_value", "ignored"}} | {"id": channel["id"], "header_value_set": True}
    # PUT remains a partial dictionary update; documentation must not add required-field validation.
    updated = session.put(f"/api/notify/channels/{channel['id']}", json={"name": "Renamed"})
    assert updated.status_code == 200
    assert updated.json() == channel | {"name": "Renamed"}
    assert session.get("/api/notify/channels").json() == [updated.json() | {"result": None}]


def test_schema_describes_document_and_status_fields_without_filtering_responses(session, document, store):
    doc = scan_doc(store, 1)
    response = session.get(f"/api/docs/{doc.id}")
    assert response.status_code == 200
    data = response.json()
    schema = resolve(document, document["paths"]["/api/docs/{doc_id}"]["get"]["responses"]["200"]["content"]["application/json"]["schema"])
    assert set(data) <= schema["properties"].keys()
    page = resolve(document, schema["properties"]["pages"]["items"])
    assert "page_info" in page["properties"]
    assert {"width", "height", "mcu", "dpi"} <= data["pages"][0]["page_info"].keys()
    status = session.get("/api/status").json()
    schema = resolve(document, document["paths"]["/api/status"]["get"]["responses"]["200"]["content"]["application/json"]["schema"])
    assert status.keys() == schema["properties"].keys()
    assert status["printers"][0]["supplies"]


def test_live_get_responses_match_their_documented_models(session):
    checked = set()
    for route in session.app.routes:
        if not (isinstance(route, APIRoute) and "GET" in route.methods and "{" not in route.path and route.responses.get(200, {}).get("model")):
            continue
        live = session.get(route.path)
        if live.status_code != 200:  # /api/scanner answers 503 when no scanner is attached
            continue
        adapter = TypeAdapter(route.responses[200]["model"])
        adapter.validate_python(live.json())
        if "properties" in adapter.json_schema():  # the model also names every key the page sends
            assert live.json().keys() <= adapter.json_schema()["properties"].keys(), route.path
        checked.add(route.path)
    assert {"/api/printer", "/api/status", "/api/jobs", "/api/settings", "/api/notify/channels", "/api/tokens"} <= checked


def test_token_response_schemas_distinguish_once_only_secret_and_no_content(document):
    methods = document["paths"]["/api/tokens"]
    created = resolve(document, methods["post"]["responses"]["201"]["content"]["application/json"]["schema"])
    listed = resolve(document, methods["get"]["responses"]["200"]["content"]["application/json"]["schema"]["items"])
    assert set(created["properties"]) == {"id", "name", "access", "created", "token"}
    assert set(listed["properties"]) == {"id", "name", "access", "created", "last_used"}
    assert "content" not in document["paths"]["/api/tokens/{token_id}"]["delete"]["responses"]["204"]


def test_partial_channel_edit_schema_does_not_advertise_invalid_null_values(document):
    body = document["paths"]["/api/notify/channels/{channel_id}"]["put"]["requestBody"]["content"]["application/json"]["schema"]
    assert not body.get("required")
    for field in ("name", "enabled", "kinds", "url", "header_name", "smtp_host"):
        assert body["properties"][field].get("type") in {"string", "boolean", "array"}
    assert {"type": "null"} in body["properties"]["message_thread_id"]["anyOf"]


def test_notification_create_schema_requires_smtp_settings_only_for_a_custom_server(document):
    body = document["paths"]["/api/notify/channels"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    email = [s for s in variants(document, body) if s["properties"]["type"].get("const") == "email"]
    scan_server = next(s for s in email if s["properties"]["use_scan_server"].get("const") is True)
    custom = next(s for s in email if s["properties"]["use_scan_server"].get("const") is False)
    assert "smtp_host" not in scan_server["required"]
    assert {"smtp_host", "smtp_port", "smtp_security", "smtp_user", "smtp_password", "mail_from"} <= set(custom["required"])


def test_all_schema_references_resolve_in_the_served_document(document):
    def walk(value):
        if isinstance(value, dict):
            if "$ref" in value:
                target = document
                for part in value["$ref"].removeprefix("#/").split("/"):
                    target = target[part]
                assert target
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(document)


def test_documented_validation_errors_match_the_existing_http_contract(session, document):
    operation = document["paths"]["/api/tokens"]["post"]
    assert "422" not in operation["responses"]
    response = session.post("/api/tokens", json={"name": "Invalid", "access": "other"})
    assert response.status_code == 400
    assert isinstance(response.json()["error"], str)
    conversion = document["paths"]["/api/uploads"]["post"]["responses"]["422"]
    assert set(resolve(document, conversion["content"]["application/json"]["schema"])["properties"]) == {"error", "doc_id"}


def request_schema(document, path, method="post"):
    return resolve(document, document["paths"][path][method]["requestBody"]["content"]["application/json"]["schema"])


def channel_schema(document, kind, *, custom=False):
    return next(s for s in variants(document, request_schema(document, "/api/notify/channels"))
                if s["properties"]["type"].get("const") == kind
                and (kind != "email" or s["properties"]["use_scan_server"]["const"] == (not custom)))


def test_non_upload_body_limit_is_documented_and_enforced(session, document):
    from printhub.middleware import MAX_BODY
    for method, path in [("put", "/api/settings"), ("post", "/api/notify/silences")]:
        error = document["paths"][path][method]["responses"]["413"]["content"]["application/json"]["schema"]
        assert resolve(document, error)["properties"]["error"]["type"] == "string"
        response = session.request(method, path, json={}, headers={"Content-Length": str(MAX_BODY + 1)})
        assert response.status_code == 413
        assert isinstance(response.json()["error"], str)


@pytest.mark.parametrize(("name", "allowed"), [("", False), (" " * 3, False), ("a", True),
                                               ("a" * 60, True), ("a" * 61, False), ("  " + "a" * 60 + "  ", True)])
def test_trimmed_channel_and_token_name_limits_match_the_schema(session, document, name, allowed):
    channel_name = channel_schema(document, "webhook")["properties"]["name"]
    token_name = request_schema(document, "/api/tokens")["properties"]["name"]
    edit_name = request_schema(document, "/api/notify/channels/{channel_id}", "put")["properties"]["name"]
    for schema in (channel_name, token_name, edit_name):
        assert bool(re.fullmatch(schema["pattern"], name)) is allowed
        assert schema["description"]
    payload = {"name": name, "type": "webhook", "enabled": True, "kinds": [],
               "url": "https://example.com/hook", "header_name": "", "header_value": ""}
    channel = session.post("/api/notify/channels", json=payload)
    token = session.post("/api/tokens", json={"name": name, "access": "read"})
    assert channel.status_code == (201 if allowed else 400)
    assert token.status_code == (201 if allowed else 400)
    if allowed:
        assert channel.json()["name"] == token.json()["name"] == name.strip()


@pytest.mark.parametrize(("to", "allowed"), [([], False), (["a@example.com"], True),
                                             (["a@example.com"] * 10, True), (["a@example.com"] * 11, False),
                                             (["Display <a@example.com>"], False), (["not-an-address"], False)])
def test_email_recipient_constraints_describe_the_existing_parser(session, document, to, allowed):
    recipients = channel_schema(document, "email")["properties"]["to"]
    assert (recipients["minItems"], recipients["maxItems"]) == (1, 10)
    assert recipients["items"]["format"] == "email"
    assert recipients["items"]["description"]
    edit = request_schema(document, "/api/notify/channels/{channel_id}", "put")["properties"]["to"]
    assert (edit["minItems"], edit["maxItems"]) == (1, 10)
    response = session.post("/api/notify/channels", json={"name": "Email", "type": "email", "enabled": True,
                                                        "kinds": [], "to": to, "use_scan_server": True})
    assert response.status_code == (201 if allowed else 400)


@pytest.mark.parametrize(("port", "allowed"), [(0, False), (1, True), (65535, True), (65536, False)])
def test_custom_smtp_port_boundaries_are_documented_without_changing_validation(session, document, port, allowed):
    schema = channel_schema(document, "email", custom=True)["properties"]["smtp_port"]
    assert (schema["minimum"], schema["maximum"]) == (1, 65535)
    response = session.post("/api/notify/channels", json={"name": "Email", "type": "email", "enabled": True,
        "kinds": [], "to": ["a@example.com"], "use_scan_server": False, "smtp_host": "localhost",
        "smtp_port": port, "smtp_security": "tls", "smtp_user": "", "smtp_password": "", "mail_from": "a@example.com"})
    assert response.status_code == (201 if allowed else 400)


@pytest.mark.parametrize(("url", "allowed"), [("https://example.com/hook", True), ("HTTP://localhost/hook", True),
    ("ftp://example.com/hook", False), ("https:///hook", False), ("https://user:pass@example.com/hook", False)])
def test_webhook_url_constraints_match_representative_runtime_boundaries(session, document, url, allowed):
    schema = channel_schema(document, "webhook")["properties"]["url"]
    assert bool(re.fullmatch(schema["pattern"], url)) is allowed
    assert schema["description"]
    response = session.post("/api/notify/channels", json={"name": "Webhook", "type": "webhook", "enabled": True,
        "kinds": [], "url": url, "header_name": "", "header_value": ""})
    assert response.status_code == (201 if allowed else 400)


def test_silence_conditional_value_rules_are_documented_and_enforced(session, document):
    body = request_schema(document, "/api/notify/silences")
    assert body["properties"]["value"]["minLength"] == 1
    assert body["if"]["properties"]["match"]["const"] == "kind"
    assert set(body["then"]["properties"]["value"]["enum"]) == {"job", "scan", "printer", "conversion", "settings"}
    for match, value, status in [("title", "", 400), ("kind", "unknown", 400), ("kind", "job", 201), ("title", "unknown", 201)]:
        response = session.post("/api/notify/silences", json={"match": match, "value": value, "hours": 1})
        assert response.status_code == status


def test_existing_password_and_settings_bounds_are_documentation_only(session, document):
    password = request_schema(document, "/api/settings/password")["properties"]["new"]
    assert password["minLength"] == 8
    # Password verification still happens before the length rule, not during Pydantic validation.
    assert session.post("/api/settings/password", json={"current": "wrong", "new": "short"}).status_code == 403
    properties = request_schema(document, "/api/settings", "put")["properties"]
    for name, bounds in [("retention_days", (1, 365)), ("min_free_gb", (.5, 1000)), ("smtp_port", (1, 65535))]:
        assert (properties[name]["minimum"], properties[name]["maximum"]) == bounds
    assert set(properties["smtp_security"]["enum"]) == {"starttls", "tls", None}
    assert properties["min_free_gb"]["description"]
    for retention, status in [(0, 400), (1, 200), (365, 200), (366, 400), (None, 200)]:
        assert session.put("/api/settings", json={"retention_days": retention}).status_code == status


@pytest.mark.parametrize(("kind", "field", "marker", "retained"), [
    ("telegram", "bot_token", "bot_token_set", None),
    ("telegram", "bot_token", "bot_token_set", ""),
    ("telegram", "bot_token", "bot_token_set", " \t\n"),
    ("webhook", "header_value", "header_value_set", None),
    ("webhook", "header_value", "header_value_set", ""),
    ("webhook", "header_value", "header_value_set", "\t"),
    ("webhook", "header_value", "header_value_set", "\t" * 1001),
    ("email", "smtp_password", "smtp_password_set", None),
    ("email", "smtp_password", "smtp_password_set", ""),
    ("email", "smtp_password", "smtp_password_set", " " * 1001),
], ids=["telegram-null", "telegram-empty", "telegram-whitespace", "webhook-null", "webhook-empty",
        "webhook-tab", "webhook-long-whitespace", "smtp-null", "smtp-empty", "smtp-long-whitespace"])
def test_edit_secret_schema_allows_retention_without_exposing_or_changing_the_secret(
    session, document, cfg, kind, field, marker, retained,
):
    from printhub.notifications import load
    secret = request_schema(document, "/api/notify/channels/{channel_id}", "put")["properties"][field]
    alternatives = secret["anyOf"]
    assert {"type": "null"} in alternatives
    blank = next((s for s in alternatives if "pattern" in s and re.fullmatch(s["pattern"], "")), None)
    assert blank is not None
    assert blank["description"]
    assert "maxLength" not in blank
    assert re.fullmatch(blank["pattern"], "\u001c\u0085\u3000")
    assert not re.fullmatch(blank["pattern"], "\ufeff")
    if retained is not None:
        assert re.fullmatch(blank["pattern"], retained)
    replacements = [s for s in alternatives if s is not blank and s.get("type") != "null"]
    assert len(replacements) == 1
    if kind == "telegram":
        assert not re.fullmatch(replacements[0]["pattern"], "invalid token")
        assert not re.fullmatch(channel_schema(document, kind)["properties"][field]["pattern"], "")
    else:
        assert replacements[0]["maxLength"] == 1000
        if kind == "webhook":
            assert replacements[0]["not"] == {"pattern": r"[\u0000-\u001f\u007f]"}
    specific = {
        "telegram": {"chat_id": "123", "disable_notification": False, "protect_content": False,
                     "disable_web_page_preview": False},
        "webhook": {"url": "https://example.com/hook", "header_name": "Authorization"},
        "email": {"to": ["a@example.com"], "use_scan_server": False, "smtp_host": "localhost",
                  "smtp_port": 465, "smtp_security": "tls", "smtp_user": "", "mail_from": "a@example.com"},
    }
    created = session.post("/api/notify/channels", json={"name": "Keep secret", "type": kind,
        "enabled": True, "kinds": [], field: "stored-secret", **specific[kind]})
    assert created.status_code == 201
    original = created.json()
    updated = session.put(f"/api/notify/channels/{original['id']}", json={field: retained})
    assert updated.status_code == 200
    assert updated.json() == original
    assert updated.json()[marker] is True
    assert field not in updated.json()
    assert load(cfg.data_dir)["channels"][0][field] == "stored-secret"

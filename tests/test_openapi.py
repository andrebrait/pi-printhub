# SPDX-License-Identifier: AGPL-3.0-only
import pytest
from fastapi.routing import APIRoute

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
        assert {"400", "401", "403", "429", "503"} <= operation["responses"].keys()


def test_auth_metadata_matches_browser_only_contract(document):
    from printhub.auth import COOKIE
    assert document["openapi"].startswith("3.1.")
    assert document["info"]["title"] == "Print Hub API"
    assert document["info"]["version"] == "0.1.0"
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

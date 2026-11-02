# SPDX-License-Identifier: AGPL-3.0-only
"""Documentation-only models: existing handlers retain their validation and JSON serialization."""
from dataclasses import dataclass
from http import HTTPStatus
from importlib.metadata import PackageNotFoundError, version
from typing import Literal

from fastapi.openapi.utils import get_openapi
from pydantic import BaseModel, Field, RootModel, TypeAdapter
from typing_extensions import TypedDict

from printhub.auth import COOKIE
from printhub.documents import MailRecord, Page, PrintRecord
from printhub.events import Event, Kind
from printhub.middleware import BROWSER_ONLY
from printhub.printing import JobInfo, Supply

try:
    VERSION = version("printhub")
except PackageNotFoundError:
    VERSION = "0.1.0"  # pyproject.toml; source checkouts need not be installed.

DESCRIPTION = "Authenticate with an API token in Authorization: Bearer <token>, or with the browser session cookie after logging in. Browser sessions also require a same-origin Origin header on writes."


class Error(BaseModel):
    error: str


class ConversionError(Error):
    doc_id: str


class DocId(BaseModel):
    doc_id: str


class JobId(BaseModel):
    job_id: int


class PrinterOptions(BaseModel):
    state: Literal["idle", "processing", "stopped"]
    state_reasons: list[str]
    accepting: bool
    supported: dict[str, list[str]]
    defaults: dict[str, str]
    margins: dict[str, int]


class Problem(BaseModel):
    label: str
    level: Literal["warning", "error"]


class PrinterState(BaseModel):
    state: str
    state_reasons: list[str]
    accepting: bool
    problems: list[Problem]


class PrinterStatus(BaseModel):
    name: str
    model: str
    state: str
    problems: list[Problem]
    supplies: list[Supply]
    web_page: str
    usb_answering: bool | None


class Status(BaseModel):
    printer: PrinterState | None
    error: str | None
    unread: int
    latest_event: int
    printers: list[PrinterStatus]


@dataclass
class ReadEvent(Event):
    read: bool = False


class Notifications(BaseModel):
    events: list[ReadEvent]
    unread: int
    latest: int


class DocSummary(BaseModel):
    id: str
    kind: Literal["print", "scan"]
    title: str
    modified: str
    page_count: int


class PageInfo(BaseModel):
    width: int
    height: int
    mcu: tuple[int, int]
    dpi: tuple[float, float]


@dataclass(kw_only=True)
class PageDetails(Page):
    page_info: PageInfo


class DocDetails(BaseModel):
    id: str
    kind: Literal["print", "scan"]
    title: str
    created: str
    modified: str
    pages: list[PageDetails]
    source_name: str | None
    scan_resolution: int | None
    scanning: bool
    scan_error: str | None
    prints: list[PrintRecord]
    mails: list[MailRecord]
    pdf_pages: list[tuple[float, float]] | None = None
    preview_count: int | None = None


@dataclass
class JobDetails(JobInfo):
    doc_id: str | None
    reason_labels: list[str]


class Jobs(BaseModel):
    active: list[JobDetails]
    recent: list[JobDetails]


class Settings(BaseModel):
    retention_days: int
    min_free_gb: float
    smtp_host: str
    smtp_port: int
    smtp_security: str
    smtp_user: str
    mail_from: str
    smtp_password_set: bool


class Switches(BaseModel):
    share: bool
    expose_usb: bool


class AppliedSystem(Switches):
    state: str
    error: str | None
    at: str
    request_at: str | None


class System(BaseModel):
    requested: Switches
    status: AppliedSystem | None
    pending: bool


class Templates(BaseModel):
    job: str
    scan: str
    printer: str
    conversion: str
    settings: str


class TelegramDefaults(BaseModel):
    header: str
    templates: Templates


class EmailDefaults(BaseModel):
    subject_prefix: str
    templates: Templates


class NotifyDefaults(BaseModel):
    telegram: dict[str, TelegramDefaults]
    email: dict[str, EmailDefaults]


class Channel(BaseModel):
    name: str
    enabled: bool
    kinds: list[Kind]


class WebhookFields(BaseModel):
    type: Literal["webhook"]
    url: str
    header_name: str


class WebhookCreate(Channel, WebhookFields):
    header_value: str


class WebhookPublic(Channel, WebhookFields):
    id: str
    header_value_set: bool


class TelegramFields(BaseModel):
    type: Literal["telegram"]
    chat_id: str
    message_thread_id: int | None = None
    parse_mode: Literal["HTML", "Markdown", "MarkdownV2"] | None = "MarkdownV2"
    disable_notification: bool
    protect_content: bool
    disable_web_page_preview: bool
    header: str = Field(default_factory=str)
    templates: dict[Kind, str] = Field(default_factory=dict)


class TelegramCreate(Channel, TelegramFields):
    bot_token: str


class TelegramPublic(Channel, TelegramFields):
    id: str
    bot_token_set: bool


class EmailMessage(BaseModel):
    type: Literal["email"]
    to: list[str]
    format: Literal["html", "text"] = "html"
    subject_prefix: str = "[Print Hub]"
    templates: dict[Kind, str] = Field(default_factory=dict)


class EmailFields(EmailMessage):
    use_scan_server: bool
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_security: Literal["starttls", "tls"] | None = None
    smtp_user: str | None = None
    mail_from: str | None = None


class EmailCreate(Channel, EmailMessage):
    use_scan_server: Literal[True]


class EmailCustomCreate(Channel, EmailMessage):
    use_scan_server: Literal[False]
    smtp_host: str
    smtp_port: int
    smtp_security: Literal["starttls", "tls"]
    smtp_user: str
    smtp_password: str
    mail_from: str


class EmailPublic(Channel, EmailFields):
    id: str
    smtp_password_set: bool


PublicChannel = WebhookPublic | TelegramPublic | EmailPublic


class WebhookListed(WebhookPublic):
    result: str | None


class TelegramListed(TelegramPublic):
    result: str | None


class EmailListed(EmailPublic):
    result: str | None


class ChannelCreate(RootModel[WebhookCreate | TelegramCreate | EmailCreate | EmailCustomCreate]):
    pass


class ChannelEdit(TypedDict, total=False):
    name: str
    type: Literal["webhook", "telegram", "email"]
    enabled: bool
    kinds: list[Kind]
    url: str
    header_name: str
    header_value: str | None
    chat_id: str
    bot_token: str | None
    message_thread_id: int | None
    parse_mode: Literal["HTML", "Markdown", "MarkdownV2"] | None
    disable_notification: bool
    protect_content: bool
    disable_web_page_preview: bool
    header: str
    templates: dict[Kind, str]
    to: list[str]
    use_scan_server: bool
    format: Literal["html", "text"]
    subject_prefix: str
    smtp_host: str
    smtp_port: int
    smtp_security: Literal["starttls", "tls"]
    smtp_user: str
    smtp_password: str | None
    mail_from: str


class SilenceCreate(BaseModel):
    match: Literal["title", "kind"]
    value: str
    hours: Literal[1, 8, 24, 168]


class Silence(BaseModel):
    id: str
    match: Literal["title", "kind"]
    value: str
    until: str


class Token(BaseModel):
    id: str
    name: str
    access: Literal["read", "full"]
    created: str
    last_used: str | None


class CreatedToken(BaseModel):
    id: str
    name: str
    access: Literal["read", "full"]
    created: str
    token: str


def describe(tag, summary, description, model=None, *, status=200, errors=(), media=()):
    # Additional response models document the contract without filtering or coercing live JSON.
    responses = {code: {"model": Error, "description": HTTPStatus(code).phrase}
                 for code in {400, 401, 403, 429, 503, *errors}}
    if isinstance(errors, dict):
        responses.update({code: {"model": error, "description": HTTPStatus(code).phrase} for code, error in errors.items()})
    if model is not None:
        responses[status] = {"model": model}
    if media:
        responses[status] = {"content": {m: {"schema": {"type": "string", "format": "binary"}} for m in media}}
    return {"tags": [tag], "summary": summary, "description": description, "responses": responses}


def install(app):
    def openapi():
        if app.openapi_schema is not None:
            return app.openapi_schema
        schema = get_openapi(title=app.title, version=app.version, description=app.description, routes=app.routes)
        components = schema.setdefault("components", {})
        components["securitySchemes"] = {
            "bearerAuth": {"type": "http", "scheme": "bearer"},
            "sessionCookie": {"type": "apiKey", "in": "cookie", "name": COOKIE},
        }
        both = [{"bearerAuth": []}, {"sessionCookie": []}]
        schema["security"] = both
        for path, methods in schema["paths"].items():
            for method, operation in methods.items():
                operation["security"] = [{"sessionCookie": []}] if BROWSER_ONLY.fullmatch(f"{method.upper()} {path}") else both
                # The app maps request validation failures to 400, not FastAPI's default 422.
                validation = operation["responses"].get("422", {}).get("content", {}).get("application/json", {}).get("schema", {})
                if validation.get("$ref") == "#/components/schemas/HTTPValidationError":
                    del operation["responses"]["422"]
        # These handlers intentionally accept dictionaries and validate them in the store, not Pydantic.
        for path, method, model in (
            ("/api/notify/channels", "post", ChannelCreate),
            ("/api/notify/channels/{channel_id}", "put", ChannelEdit),
            ("/api/notify/silences", "post", SilenceCreate),
        ):
            body = TypeAdapter(model).json_schema(ref_template="#/components/schemas/{model}")
            components["schemas"].update(body.pop("$defs", {}))
            schema["paths"][path][method]["requestBody"] = {
                "required": True, "content": {"application/json": {"schema": body}},
            }
        app.openapi_schema = schema
        return schema

    app.openapi = openapi

# SPDX-License-Identifier: AGPL-3.0-only
"""Documentation-only models: existing handlers retain their validation and JSON serialization."""
from dataclasses import dataclass
from http import HTTPStatus
from importlib.metadata import PackageNotFoundError, version
from typing import Annotated, Literal, get_args

from fastapi.openapi.utils import get_openapi
from pydantic import BaseModel, ConfigDict, Field, RootModel, TypeAdapter
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


TRIMMED_NAME = {
    "pattern": r"^\s*\S(?:[\s\S]{0,58}\S)?\s*$",
    "description": "Leading and trailing whitespace is removed; the remaining name must contain 1 to 60 characters.",
}
TrimmedName = Annotated[str, Field(json_schema_extra=TRIMMED_NAME)]
Recipient = Annotated[str, Field(json_schema_extra={"format": "email"},
    description="One email addr-spec without a display name; the server canonicalizes each address.")]
Recipients = Annotated[list[Recipient], Field(min_length=1, max_length=10)]
SmtpPort = Annotated[int, Field(ge=1, le=65535)]
WebhookUrl = Annotated[str, Field(
    pattern=r"^\s*[Hh][Tt][Tt][Pp][Ss]?://[^/?#@]+(?:[/?#][\s\S]*)?$",
    description="An absolute HTTP or HTTPS URL with a host and no username or password in its authority; put credentials in the header. The server checks URL parsing and normalizes leading whitespace and embedded tab/newline characters when parsing.")]
HeaderName = Annotated[str, Field(pattern=r"^[A-Za-z0-9-]*$")]
HeaderValue = Annotated[str, Field(max_length=1000, json_schema_extra={"not": {"pattern": r"[\u0000-\u001f\u007f]"}})]
TemplateText = Annotated[str, Field(min_length=1, max_length=1000)]
InputTemplates = dict[Kind, TemplateText]
SubjectPrefix = Annotated[str, Field(max_length=100, json_schema_extra={"not": {"pattern": r"[\r\n]"}})]
ChatId = Annotated[str, Field(pattern=r"^(?:-?[0-9]+|@[A-Za-z0-9_]{5,})$",
    description="A numeric chat ID or @channelname; the complete value must match the pattern.")]
# Spell out str.strip() whitespace so JSON Schema consumers do not use a different Unicode regex \s set.
BlankSecret = Annotated[str, Field(
    pattern=r"^[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]*$",
    description="An empty or whitespace-only value retains the stored secret, if present; otherwise it becomes an empty secret.")]


class Channel(BaseModel):
    name: TrimmedName
    enabled: bool
    kinds: list[Kind]


class WebhookFields(BaseModel):
    type: Literal["webhook"]
    url: WebhookUrl
    header_name: HeaderName


class WebhookCreate(Channel, WebhookFields):
    header_value: HeaderValue


class WebhookPublic(Channel, WebhookFields):
    id: str
    header_value_set: bool


class TelegramFields(BaseModel):
    type: Literal["telegram"]
    chat_id: ChatId
    message_thread_id: int | None = None
    parse_mode: Literal["HTML", "Markdown", "MarkdownV2"] | None = "MarkdownV2"
    disable_notification: bool
    protect_content: bool
    disable_web_page_preview: bool
    header: Annotated[str, Field(max_length=200)] = Field(default_factory=str)
    templates: InputTemplates = Field(default_factory=dict)


class TelegramCreate(Channel, TelegramFields):
    bot_token: Annotated[str, Field(pattern=r"^[A-Za-z0-9:_-]+$")]


class TelegramPublic(Channel, TelegramFields):
    id: str
    bot_token_set: bool


class EmailMessage(BaseModel):
    type: Literal["email"]
    to: Recipients
    format: Literal["html", "text"] = "html"
    subject_prefix: SubjectPrefix = "[Print Hub]"
    templates: InputTemplates = Field(default_factory=dict)


class EmailFields(EmailMessage):
    use_scan_server: bool
    smtp_host: str | None = None
    smtp_port: SmtpPort | None = None
    smtp_security: Literal["starttls", "tls"] | None = None
    smtp_user: str | None = None
    mail_from: str | None = None


class EmailCreate(Channel, EmailMessage):
    use_scan_server: Literal[True]


class EmailCustomCreate(Channel, EmailMessage):
    use_scan_server: Literal[False]
    smtp_host: Annotated[str, Field(min_length=1, max_length=255)]
    smtp_port: SmtpPort
    smtp_security: Literal["starttls", "tls"]
    smtp_user: Annotated[str, Field(max_length=255)]
    smtp_password: Annotated[str, Field(max_length=1000)]
    mail_from: Annotated[str, Field(description="One email address, optionally with a display name; CR and LF are refused and the server canonicalizes the sender.")]


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
    name: TrimmedName
    type: Literal["webhook", "telegram", "email"]
    enabled: bool
    kinds: list[Kind]
    url: WebhookUrl
    header_name: HeaderName
    header_value: HeaderValue | BlankSecret | None
    chat_id: ChatId
    bot_token: Annotated[str, Field(pattern=r"^[A-Za-z0-9:_-]+$")] | BlankSecret | None
    message_thread_id: int | None
    parse_mode: Literal["HTML", "Markdown", "MarkdownV2"] | None
    disable_notification: bool
    protect_content: bool
    disable_web_page_preview: bool
    header: Annotated[str, Field(max_length=200)]
    templates: InputTemplates
    to: Recipients
    use_scan_server: bool
    format: Literal["html", "text"]
    subject_prefix: SubjectPrefix
    smtp_host: Annotated[str, Field(min_length=1, max_length=255)]
    smtp_port: SmtpPort
    smtp_security: Literal["starttls", "tls"]
    smtp_user: Annotated[str, Field(max_length=255)]
    smtp_password: Annotated[str, Field(max_length=1000)] | BlankSecret | None
    mail_from: Annotated[str, Field(description="One email address, optionally with a display name; CR and LF are refused and the server canonicalizes the sender.")]


class SilenceCreate(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "if": {"properties": {"match": {"const": "kind"}}},
        "then": {"properties": {"value": {"enum": list(get_args(Kind))}}},
    })
    match: Literal["title", "kind"]
    value: Annotated[str, Field(min_length=1)]
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
                 for code in {400, 401, 403, 413, 429, 503, *errors}}
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
        # Only the document gains these constraints: handlers retain their existing validation order and null semantics.
        for name, properties in {
            "TokenCreate": {"name": TRIMMED_NAME},
            "PasswordChange": {"new": {"minLength": 8}},
            "SettingsPatch": {
                "retention_days": {"minimum": 1, "maximum": 365},
                "min_free_gb": {"minimum": 0.5, "maximum": 1000,
                    "description": "Must also be below the total capacity of the data disk; null leaves the setting unchanged."},
                "smtp_port": {"minimum": 1, "maximum": 65535},
                "smtp_security": {"enum": ["starttls", "tls", None]},
                "mail_from": {"description": "One email address, optionally with a display name; CR and LF are refused, the sender is canonicalized, and null leaves the setting unchanged."},
            },
        }.items():
            for field, constraints in properties.items():
                components["schemas"][name]["properties"][field].update(constraints)
        app.openapi_schema = schema
        return schema

    app.openapi = openapi

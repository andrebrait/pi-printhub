# SPDX-License-Identifier: AGPL-3.0-only
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse

from printhub import tokens
from printhub.auth import COOKIE, LoginLimiter, origin_ok, session_valid
from printhub.config import Config

MAX_UPLOAD = 100 * 1024 * 1024
# Whole request body: one upload plus multipart framing
MAX_BODY = MAX_UPLOAD + 1024 * 1024

# "METHOD path" of what only a browser session may do. Any other /api/ request is allowed to a token by
# method alone: GET to every token, WRITE_METHODS to a full one, no other method to any.
BROWSER_ONLY = re.compile(r"POST /api/(login|logout|settings/password)|\S+ /api/tokens(/.*)?", re.DOTALL)
WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class _BodyTooLarge(Exception):
    pass


class Guard:
    """Origin check, session gate and body limit, applied before any body byte is read.

    FastAPI parses (and spools) multipart bodies before route dependencies run, so these
    checks cannot live in dependencies.
    """

    def __init__(self, app, current: Callable[[], Config], limiter: LoginLimiter, admit_share=None, release_share=None):
        self.app = app
        self.current = current
        self.limiter = limiter
        self.admit_share, self.release_share = admit_share, release_share

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request = Request(scope)
        method, path = scope["method"], scope["path"]
        if denied := await self._deny(scope, request, method, path):
            return await denied(scope, receive, send)
        reserved = method == "POST" and path == "/share" and not session_valid(self.current(), request.cookies.get(COOKIE))
        if reserved and self.admit_share:
            if denied := self.admit_share():
                return await denied(scope, receive, send)

        received = 0
        too_large = False

        async def limited_receive():
            nonlocal received, too_large
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > MAX_BODY:
                    too_large = True
                    raise _BodyTooLarge()
            return message

        async def guarded_send(message):
            # Whatever the app makes of the aborted read (FastAPI turns it into a 400) is dropped.
            if not too_large:
                await send(message)

        try:
            try:
                await self.app(scope, limited_receive, guarded_send)
            except Exception:
                if not too_large:
                    raise
            if too_large:
                await _error(413, "File exceeds 100 MB")(scope, receive, send)
        finally:
            if reserved and self.release_share:
                self.release_share()

    async def _deny(self, scope, request: Request, method: str, path: str) -> JSONResponse | None:
        cfg = self.current()  # one snapshot per request, so a concurrent save cannot split the checks
        authorization = request.headers.get("authorization") if path.startswith("/api/") else None
        if authorization is not None:
            # Never falls back to the session cookie: a wrong token is a 401 whatever else the request carries.
            if denied := await self._token_denied(scope, cfg, request, method, path, authorization):
                return denied
        else:
            if not (method == "POST" and path == "/share") and not origin_ok(
                method, request.headers.get("origin"), cfg
            ):
                return _error(403, "Forbidden origin")
            if (
                path.startswith("/api/")
                and not (method == "POST" and path == "/api/login")
                and not session_valid(cfg, request.cookies.get(COOKIE))
            ):
                return _error(401, "Not logged in")
        length = request.headers.get("content-length")
        if length is not None:
            if not length.isdigit():
                return _error(400, "Invalid Content-Length")
            if int(length) > MAX_BODY:
                return _error(413, "File exceeds 100 MB")
        return None

    async def _token_denied(
        self, scope, cfg: Config, request: Request, method: str, path: str, authorization: str
    ) -> JSONResponse | None:
        scheme, _, presented = authorization.partition(" ")
        token = None
        if scheme.lower() == "bearer":
            client = request.client.host if request.client else ""
            if self.limiter.blocked(client, time.monotonic()):
                return _error(429, "Too many attempts; wait a minute")
            try:
                token = await run_in_threadpool(tokens.verify, cfg.data_dir, presented, datetime.now(UTC))
            except tokens.TokenStoreError:
                return _error(503, tokens.UNAVAILABLE)  # not the client's fault, so not a failed attempt
            if token is None:
                self.limiter.allow(client, time.monotonic())
        if token is None:
            return _error(401, "Invalid API token")
        scope["printhub.token"] = token
        if BROWSER_ONLY.fullmatch(f"{method} {path}") or (method != "GET" and method not in WRITE_METHODS):
            return _error(403, "Not allowed with an API token")
        if method != "GET" and token["access"] != "full":
            return _error(403, "This API token is read-only")
        return None


def _error(status: int, error: str) -> JSONResponse:
    return JSONResponse({"error": error}, status)

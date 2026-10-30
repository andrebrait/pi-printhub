# SPDX-License-Identifier: AGPL-3.0-only
from collections.abc import Callable

from starlette.requests import Request
from starlette.responses import JSONResponse

from printhub.auth import COOKIE, origin_ok, session_valid
from printhub.config import Config

MAX_UPLOAD = 100 * 1024 * 1024
# Whole request body: one upload plus multipart framing
MAX_BODY = MAX_UPLOAD + 1024 * 1024


class _BodyTooLarge(Exception):
    pass


class Guard:
    """Origin check, session gate and body limit, applied before any body byte is read.

    FastAPI parses (and spools) multipart bodies before route dependencies run, so these
    checks cannot live in dependencies.
    """

    def __init__(self, app, current: Callable[[], Config], admit_share=None, release_share=None):
        self.app = app
        self.current = current
        self.admit_share, self.release_share = admit_share, release_share

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request = Request(scope)
        method, path = scope["method"], scope["path"]
        if denied := self._deny(request, method, path):
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

    def _deny(self, request: Request, method: str, path: str) -> JSONResponse | None:
        cfg = self.current()  # one snapshot per request, so a concurrent save cannot split the checks
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


def _error(status: int, error: str) -> JSONResponse:
    return JSONResponse({"error": error}, status)

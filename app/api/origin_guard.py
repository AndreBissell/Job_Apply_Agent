"""Refuse writes that a different website's page sent to this local server.

Removing CORS stops other sites READING responses and sending preflighted calls, but a
browser still sends "simple" cross-site requests, such as a POST with no body
(/jobs/{id}/regenerate, /quick-screen, /gaps/{id}/clear). Those would run (and, for the
first two, spend money) even though the page can't see the answer. Browsers always send
an Origin header on cross-site POSTs, so a write whose Origin isn't ours gets a 403.

Allowed: no Origin header (scripts, tests, same-site GET-style navigation), any
chrome-extension:// origin (the side panel, popup and background worker), and this
server's own origin (the profile editor). "Own origin" means http://<Host header> where
the host is localhost or 127.0.0.1, so a hostile site that DNS-rebinds to 127.0.0.1
(its Host header is its own name) doesn't pass. Reads (GET/HEAD/OPTIONS) are not
guarded, so a website can still trigger GET endpoints that have side effects; it just
can't read the result.
"""
from __future__ import annotations

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_LOCAL_HOSTS = {"localhost", "127.0.0.1"}


def origin_allowed(origin: str, host: str) -> bool:
    if origin.startswith("chrome-extension://"):
        return True
    hostname = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    return hostname in _LOCAL_HOSTS and origin == f"http://{host}"


class OriginGuardMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] in _WRITE_METHODS:
            headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
            origin = headers.get("origin")
            if origin is not None and not origin_allowed(origin, headers.get("host", "")):
                response = JSONResponse({"detail": "Cross-site request refused."}, status_code=403)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)

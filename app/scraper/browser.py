"""Playwright browser lifecycle + MANDATORY proxy enforcement.

A real browser engine (headless Chromium) is required because Seek is a
JS-rendered React site — plain HTTP + HTML parsing would not see the listings.

PROXY IS MANDATORY (see CLAUDE.md "Networking policy"). All scraper traffic MUST
be routed through a proxy so the user's real IP is never exposed to Seek. This
module is **fail-closed**: ``launch_browser`` raises ``ProxyNotConfiguredError``
and refuses to start a browser if ``PROXY_SERVER`` is not set. Do not weaken this
into an "optional" proxy — that would reintroduce the exact failure it prevents.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator


class ProxyNotConfiguredError(RuntimeError):
    """Raised when a browser launch is attempted with no proxy configured."""


def build_proxy_from_env() -> dict | None:
    """Return a Playwright ``proxy`` dict from env, or ``None`` if not configured.

    ``PROXY_SERVER`` is required to enable proxying; ``PROXY_USERNAME`` /
    ``PROXY_PASSWORD`` are optional and only added when present. Callers that open
    network connections MUST treat ``None`` as "do not connect" (see
    ``launch_browser``), never as "connect directly".
    """
    server = os.environ.get("PROXY_SERVER")
    if not server:
        return None

    proxy: dict[str, str] = {"server": server}
    username = os.environ.get("PROXY_USERNAME")
    password = os.environ.get("PROXY_PASSWORD")
    if username:
        proxy["username"] = username
    if password:
        proxy["password"] = password
    return proxy


def require_proxy() -> dict:
    """Return the proxy config, or raise if none is set. The fail-closed gate."""
    proxy = build_proxy_from_env()
    if proxy is None:
        raise ProxyNotConfiguredError(
            "PROXY_SERVER is not set. Refusing to open any connection without a "
            "proxy — ALL scraper traffic must be routed through one (see CLAUDE.md "
            "'Networking policy'). Set PROXY_SERVER (and optionally PROXY_USERNAME "
            "/ PROXY_PASSWORD) in your .env, then retry."
        )
    return proxy


@contextmanager
def launch_browser(*, headless: bool = True) -> Iterator["BrowserContext"]:  # noqa: F821
    """Yield a Playwright ``BrowserContext`` with the default (realistic) Chromium UA.

    Usage::

        with launch_browser() as context:
            page = context.new_page()
            ...

    The browser and Playwright runtime are torn down on exit.

    Raises ``ProxyNotConfiguredError`` (fail-closed) if no proxy is configured —
    a browser is NEVER launched without one.
    """
    # Fail-closed BEFORE importing/launching anything: no proxy => no connection.
    proxy = require_proxy()

    # Imported lazily so the rest of the app (and tests) don't require Playwright
    # to be installed unless the scraper is actually run.
    from playwright.sync_api import sync_playwright

    launch_kwargs: dict = {"headless": headless, "proxy": proxy}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(**launch_kwargs)
        try:
            # Default context => Playwright's stock Chromium user-agent, which is
            # a realistic desktop UA (per the politeness guidance).
            context = browser.new_context()
            try:
                yield context
            finally:
                context.close()
        finally:
            browser.close()

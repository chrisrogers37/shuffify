"""Every page, in a real browser, at phone and desktop widths.

Headless Chromium (Playwright) visits each page logged out, then as alice,
logged in the way a person does: the landing page's consent box and button,
then the fake's consent page. The dashboard, schedules and activity are
visited both before and after a shuffle and a schedule are made. Every visit
must:

- answer with the expected status, at the URL asked for, with no redirect,
  server or script;
- log no console error and throw no uncaught exception (the browser reports
  a CSP violation as a console error); the one exception is the console line
  Chromium writes for the missing page's own 404;
- have every request it makes succeed;
- at phone widths, not scroll sideways: 360px, the most common Android
  width, and 280px, a folding phone's cover screen.

A full-page screenshot of each visit goes to .harness/screenshots/, which CI
keeps as an artifact.

The browser reaches nothing off this machine. Its resolver refuses every
hostname and every IP address but 127.0.0.1; it has no proxy, and pages
have no WebRTC. Within 127.0.0.1, every request is routed through a handler
that lets only the app and the fake through and reports anything else; a
page's WebSockets are reported without being opened, and any that open
anyway (a worker's) are reported too; service workers are blocked. The one
script the app loads from a CDN is served from a copy in harness/vendor/
instead; the page pins it by subresource integrity, so
the browser refuses the copy unless its bytes are exactly the published ones.
"""

from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from harness.vendor import CDN_COPIES
from tests.e2e.conftest import APP, FAKE, greets, pid

SCREENSHOTS = Path(__file__).resolve().parents[2] / ".harness" / "screenshots"
VIEWPORTS = {
    "phone": {"width": 360, "height": 800},
    # The narrowest screen in common use: a Galaxy Fold's cover screen.
    "fold": {"width": 280, "height": 653},
    "desktop": {"width": 1440, "height": 900},
}
CHROMIUM_ARGS = [
    # No name or address resolves but 127.0.0.1, where the app and fake are.
    "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1",
    # No proxy, whatever the environment says.
    "--proxy-server=direct://",
    # WebRTC would send UDP straight to an IP literal, past the resolver.
    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
]
MISSING = "/no-such-page"  # the one page expected to answer 404
LOGGED_OUT = {"landing": "/", "terms": "/terms", "privacy": "/privacy", "not-found": MISSING}
# Before anything is made, apart from the log-in entries in the activity.
BEFORE = {"dashboard-before": "/", "schedules-before": "/schedules", "activity-before": "/activity"}
LOGGED_IN = {
    "dashboard": "/",
    "workshop-hub": "/workshop",
    "workshop": f"/workshop/{pid('alice-morning-mix')}",
    "schedules": "/schedules",
    "settings": "/settings",
    "activity": "/activity",
    "not-found": MISSING,
}


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        chromium = playwright.chromium.launch(args=CHROMIUM_ARGS)
        yield chromium
        chromium.close()


class Visit:
    """One browser context at one viewport, recording what goes wrong."""

    def __init__(self, browser, viewport: str, allowed=()):
        self.viewport = viewport
        # (path, status) pairs a scenario provokes on purpose; nothing else may fail.
        self.allowed = {(f"{APP}{path}", status) for path, status in allowed}
        self.context = browser.new_context(viewport=VIEWPORTS[viewport], service_workers="block")
        # WebRTC can reach a TURN server over TCP past every other layer; the
        # app has no use for it.
        self.context.add_init_script("delete window.RTCPeerConnection; delete window.webkitRTCPeerConnection;")
        self.context.route("**/*", self._only_the_harness)
        # Not connected: an unhandled WebSocket route stays a mock.
        self.context.route_web_socket("**/*", lambda ws: self._problem(f"opened a WebSocket to {ws.url}"))
        self.page = self.context.new_page()
        # One the route did not catch (a worker's) really opened.
        self.page.on("websocket", lambda ws: self._problem(f"opened a real WebSocket to {ws.url}"))
        self.page.on("console", self._check_console)
        self.page.on("pageerror", lambda error: self._problem(f"uncaught exception: {error}"))
        self.page.on("requestfailed", lambda req: self._problem(f"request failed: {req.url} ({req.failure})"))
        self.page.on("response", self._check_response)
        self.current = ""
        self.problems = []

    def _problem(self, text: str) -> None:
        self.problems.append(f"{self.current or 'login'} [{self.viewport}]: {text}")

    def _only_the_harness(self, route) -> None:
        url = route.request.url
        if url.startswith((f"{APP}/", f"{FAKE}/")):
            route.continue_()
        elif url in CDN_COPIES:
            route.fulfill(path=CDN_COPIES[url])
        else:
            self._problem(f"tried to reach {url}")
            route.abort()

    def _check_console(self, msg) -> None:
        own_404 = msg.location.get("url") == f"{APP}{MISSING}" and msg.text.startswith(
            "Failed to load resource: the server responded with a status of 404"
        )
        expected = any(
            msg.location.get("url") == url
            and msg.text.startswith(f"Failed to load resource: the server responded with a status of {status}")
            for url, status in self.allowed
        )
        if msg.type == "error" and not own_404 and not expected:
            self._problem(f"console error: {msg.text}")

    def _check_response(self, response) -> None:
        if (response.status >= 400 and response.url != f"{APP}{MISSING}"
                and (response.url, response.status) not in self.allowed):
            self._problem(f"{response.status} for {response.url}")

    def log_in(self, user: str = "alice") -> None:
        self.page.goto(f"{APP}/")
        self.page.check("#legal-consent")
        self.page.click("#login-button")
        self.page.wait_for_url(f"{FAKE}/authorize*")
        self.page.click(f"a.user[data-user={user}]")
        self.page.wait_for_url(f"{APP}/")
        assert greets(self.page.content(), "Alice Harness"), "the dashboard does not greet the user who logged in"

    def check(self, pages: dict) -> None:
        for name, path in pages.items():
            self.current = name
            url = f"{APP}{path}"
            response = self.page.goto(url, wait_until="networkidle")
            expected = 404 if path == MISSING else 200
            if response.status != expected:
                self._problem(f"answered {response.status}, not {expected}")
            if response.request.redirected_from is not None or response.url != url:
                self._problem(f"the server redirected it to {response.url}")
            elif self.page.url != url:
                self._problem(f"a script sent it on to {self.page.url}")
            if self.viewport != "desktop":
                overflow = self.page.evaluate(
                    "document.documentElement.scrollWidth - document.documentElement.clientWidth"
                )
                if overflow > 0:
                    self._problem(f"scrolls sideways by {overflow}px")
            SCREENSHOTS.mkdir(parents=True, exist_ok=True)
            self.page.screenshot(path=SCREENSHOTS / f"{name}-{self.viewport}.png", full_page=True,
                                 animations="disabled")

    def close(self) -> None:
        self.context.close()


@pytest.fixture(params=sorted(VIEWPORTS))
def visit(request, browser, fake):
    v = Visit(browser, request.param)
    yield v
    v.close()


def test_logged_out_pages(visit):
    visit.check({f"logged-out-{name}": path for name, path in LOGGED_OUT.items()})
    assert visit.problems == []


def test_logged_in_pages(visit, alice):
    visit.log_in()
    visit.check(BEFORE)
    # Something on the activity and schedules pages: a shuffle and a schedule.
    alice.ok("post", f"/shuffle/{pid('alice-morning-mix')}", data={"algorithm": "BasicShuffle"})
    alice.ok("post", "/schedules/create", json={
        "job_type": "shuffle", "target_playlist_id": pid("alice-long-haul"),
        "target_playlist_name": "Long Haul", "schedule_value": "daily", "algorithm_name": "BasicShuffle",
    })
    visit.check(LOGGED_IN)
    assert visit.problems == []

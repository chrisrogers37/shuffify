"""The unit suite stays on this machine: a pytest plugin, registered
by tests/conftest.py.

Every test reaches loopback only. A lookup (``socket.getaddrinfo``) or a
connection (``socket.socket.connect``) naming anything else is refused and
recorded, and the test fails at teardown even if the code under test
swallowed the error. A refusal is not an
OSError, so an HTTP client fails at once instead of retrying it. Proxies are
off (the variables cleared, ``NO_PROXY=*``), so requests connects directly
and the guard sees where it is going; otherwise a loopback proxy, or the
system proxy requests falls back to on macOS, would pass for local. The
live ``integration`` tests, which read real public pages, are exempt.

Scope: the guard is installed for each test and its function-scoped
fixtures. It does not cover module-, class- or session-scoped fixture setup,
threads that outlive a test, other socket calls (``connect_ex``, ``sendto``,
``gethostbyname``), libpq, or subprocesses. Those are the paths the harness's
process-wide guard (harness/netguard/) covers for harness processes.
"""

import socket

import pytest

PROXY_VARIABLES = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")
# A server binding names no remote host: no host, or a wildcard address.
_BINDINGS = (None, "", "0.0.0.0", "::")


class LeftThisMachine(RuntimeError):
    """A unit test tried to reach something off this machine."""


def on_this_machine(host) -> bool:
    """Whether a lookup or connection names this machine: what the app
    counts as loopback, or a server binding."""
    from shuffify.spotify.endpoints import is_loopback_host

    return host in _BINDINGS or is_loopback_host(host)


@pytest.fixture(autouse=True)
def network_guard(request, monkeypatch):
    """Yields the list of what was refused, for the guard's own tests."""
    if request.node.get_closest_marker("integration"):
        yield []
        return
    for name in PROXY_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("no_proxy", "*")  # requests reads this one first
    refused = []
    real_connect, real_getaddrinfo = socket.socket.connect, socket.getaddrinfo

    def connect(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6) and not on_this_machine(address[0]):
            refused.append(address[0])
            raise LeftThisMachine(f"unit tests reach only this machine, not {address[0]!r}")
        return real_connect(sock, address)

    def getaddrinfo(host, *args, **kwargs):
        if not on_this_machine(host):
            refused.append(host)
            raise LeftThisMachine(f"unit tests resolve only this machine, not {host!r}")
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    yield refused
    assert refused == [], f"the test reached off this machine: {refused}"

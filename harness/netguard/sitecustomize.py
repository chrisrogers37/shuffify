"""Network guard for every Python process the test harness starts.

scripts/harness.sh puts this directory on PYTHONPATH and sets
SHUFFIFY_NETGUARD=1, so Python's ``site`` module imports this file at startup
in the processes it runs as harness processes: the app, the migration step
and the fake Spotify. The rest of what the script runs -- installing
dependencies, generating secrets, the build checks and the unit suite (on
mocks) -- runs outside it. From then on a harness process may open a socket
only to:

  * a Unix-domain socket, or
  * a loopback address on one of the ports in SHUFFIFY_NETGUARD_ALLOW_PORTS
    (the harness's own Postgres, Redis, app and fake Spotify).

Everything else -- api.spotify.com, accounts.spotify.com, a production
database -- is refused before a packet leaves. Forward-resolving any hostname
other than ``localhost`` is refused too, so a blocked call fails at once
instead of waiting on DNS. Reverse lookups (gethostbyaddr, getnameinfo) are
left alone: they connect to nothing, and wrapping them would make
socket.getfqdn() raise something other than OSError.

Loopback alone is not enough, which is why ports are listed explicitly: an
outbound HTTPS proxy may listen on 127.0.0.1, so "any loopback address" would
let a request to Spotify through that proxy.

A refusal raises NetguardViolation. It deliberately does not subclass OSError,
so HTTP clients treat it as a bug rather than a transient network error to
retry. Because application code may still catch and log it, every refusal is
also appended to SHUFFIFY_NETGUARD_LOG when set; harness.sh fails the run if
that file is non-empty.

Scope: this sees sockets opened from Python (requests, urllib3, redis-py --
every path to Spotify). libpq, which psycopg2 uses for PostgreSQL, connects
from C and is not covered here; HarnessConfig refuses a DATABASE_URL that
would hand libpq any non-local host instead.

This shadows the distribution's own sitecustomize (on Ubuntu, an apport crash
hook) for harness processes only.
"""

import ipaddress
import os
import socket
import sys


class NetguardViolation(RuntimeError):
    """A harness process tried to reach something outside this machine."""


def _allowed_ports():
    raw = os.environ.get("SHUFFIFY_NETGUARD_ALLOW_PORTS", "")
    return {int(p) for p in raw.replace(" ", "").split(",") if p}


def _is_loopback_ip(host):
    try:
        addr = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return None  # not an IP literal
    mapped = getattr(addr, "ipv4_mapped", None)
    return (mapped or addr).is_loopback


def _refuse(what):
    message = f"netguard blocked {what} (pid {os.getpid()})"
    sys.stderr.write(f"[netguard] {message}\n")
    log_path = os.environ.get("SHUFFIFY_NETGUARD_LOG")
    if log_path:
        try:
            with open(log_path, "a") as fh:
                fh.write(message + "\n")
        except OSError:
            pass
    raise NetguardViolation(message)


def _install():
    allowed_ports = _allowed_ports()
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_sendto = socket.socket.sendto
    real_sendmsg = getattr(socket.socket, "sendmsg", None)  # not on Windows
    real_getaddrinfo = socket.getaddrinfo
    real_gethostbyname = socket.gethostbyname
    real_gethostbyname_ex = socket.gethostbyname_ex

    def _check_address(sock, address):
        if sock.family == getattr(socket, "AF_UNIX", None):
            return
        if not isinstance(address, tuple) or len(address) < 2:
            _refuse(f"connect to {address!r}")
        host, port = address[0], address[1]
        loopback = _is_loopback_ip(host) if host != "localhost" else True
        if not loopback or port not in allowed_ports:
            _refuse(f"connect to {host}:{port}")

    def connect(self, address):
        _check_address(self, address)
        return real_connect(self, address)

    def connect_ex(self, address):
        _check_address(self, address)
        return real_connect_ex(self, address)

    def sendto(self, data, *args):
        # sendto(data, address) or sendto(data, flags, address): unconnected
        # datagrams name their destination per call, bypassing connect().
        _check_address(self, args[-1])
        return real_sendto(self, data, *args)

    def sendmsg(self, buffers, *args):
        # sendmsg(buffers[, ancdata[, flags[, address]]]): the same per-call
        # destination as sendto, and the way to send with MSG_FASTOPEN.
        if len(args) >= 3 and args[2] is not None:
            _check_address(self, args[2])
        return real_sendmsg(self, buffers, *args)

    def _check_name(host):
        if host in (None, "", "localhost"):
            return
        if isinstance(host, bytes):
            host = host.decode("ascii", "replace")
        if not _is_loopback_ip(host):
            _refuse(f"resolve {host!r}")

    def getaddrinfo(host, *args, **kwargs):
        _check_name(host)
        return real_getaddrinfo(host, *args, **kwargs)

    def gethostbyname(host):
        _check_name(host)
        return real_gethostbyname(host)

    def gethostbyname_ex(host):
        _check_name(host)
        return real_gethostbyname_ex(host)

    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    socket.socket.sendto = sendto
    if real_sendmsg is not None:
        socket.socket.sendmsg = sendmsg
    socket.getaddrinfo = getaddrinfo
    socket.gethostbyname = gethostbyname
    socket.gethostbyname_ex = gethostbyname_ex
    # Lets a process (and the tests) confirm the guard is live rather than
    # assume it from the environment.
    socket.SHUFFIFY_NETGUARD_ACTIVE = True


if os.environ.get("SHUFFIFY_NETGUARD") == "1":
    try:
        _install()
    except Exception as exc:
        # Python's site module prints an ordinary exception from
        # sitecustomize and carries on -- here, without the guard. SystemExit
        # is not caught there, so a misconfigured guard stops the process
        # instead of letting it run unguarded.
        raise SystemExit(f"[netguard] refusing to start without the network guard: {exc!r}") from exc

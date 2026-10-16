"""Use a configured public origin for unsafe requests behind a host-changing proxy."""

import ipaddress
import re
from urllib.parse import urlsplit


class CanonicalCsrfOrigin:
    """Keep Flask-WTF's token and strict HTTPS referrer checks authoritative.

    The origin comes from trusted deployment configuration, never request headers.
    Read requests retain ProxyFix's normal host and URL behavior.
    """

    def __init__(self, app, origin, methods):
        self.app = app
        self.methods = frozenset(methods)
        try:
            if not isinstance(origin, str) or not origin or any(c.isspace() for c in origin):
                raise ValueError
            parsed = urlsplit(origin)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path not in ("", "/")
                or parsed.netloc.endswith(":")
                or "?" in origin
                or "#" in origin
            ):
                raise ValueError
            host = parsed.hostname.encode("idna").decode("ascii").lower()
            try:
                address = ipaddress.ip_address(host)
            except ValueError:
                if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host) or any(
                    not label or label.startswith("-") or label.endswith("-") for label in host.split(".")
                ):
                    raise ValueError from None
                authority = host
            else:
                authority = f"[{host}]" if address.version == 6 else host
            port = 443 if parsed.port is None else parsed.port
            if not 1 <= port <= 65535:
                raise ValueError
        except (ValueError, UnicodeError, TypeError):
            raise ValueError(
                "PUBLIC_ORIGIN must be an HTTPS origin without credentials, path, query or fragment."
            ) from None
        self.host = host
        self.port = str(port)
        self.authority = authority if port == 443 else f"{authority}:{port}"

    def __call__(self, environ, start_response):
        if environ.get("REQUEST_METHOD") in self.methods:
            environ["HTTP_HOST"] = self.authority
            environ["SERVER_NAME"] = self.host
            environ["SERVER_PORT"] = self.port
            environ["wsgi.url_scheme"] = "https"
        return self.app(environ, start_response)

"""Build explicit same-origin API paths for tests."""

from urllib.parse import urlsplit


def api_path(path: str) -> str:
    """Return a root-relative route under the API namespace."""
    parsed = urlsplit(path)
    if parsed.scheme or parsed.netloc or not path.startswith("/") or path.startswith("//"):
        raise ValueError("API paths must be root-relative URLs on this origin")
    if parsed.path == "/api" or parsed.path.startswith("/api/"):
        return path
    return f"/api{path}"

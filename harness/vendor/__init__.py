"""Copies of the scripts the templates load from a CDN.

The harness's browser reaches nothing off this machine, so the browser pass
(tests/e2e/test_pages.py) serves these in place of the CDN. Each is the exact
published file: the page pins it by subresource integrity, and
tests/test_harness_vendor.py checks each copy against that hash.
"""

from pathlib import Path

_HERE = Path(__file__).resolve().parent

CDN_COPIES = {
    "https://cdn.jsdelivr.net/npm/sortablejs@1.15.6/Sortable.min.js": _HERE / "sortablejs@1.15.6" / "Sortable.min.js",
}

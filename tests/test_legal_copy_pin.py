"""The canonical legal copy cannot change without a deliberate hash update."""

from hashlib import sha256
from pathlib import Path

import pytest

COPY_DIR = Path(__file__).resolve().parent.parent / "assets" / "legal"
# Independent baselines of the canonical legal copy. The frontend regenerates its
# prerendered legal sources from these fragments, so regenerating cannot bless a
# legal-text change: an intended edit updates the hash in the same change.
LEGAL_COPY_SHA256 = {
    "terms": "217948dfc8ffee6847f59b340620a0b9ee9f94153cc11804c324c826f8d9f831",
    "privacy": "102a9df0bcd9024f3408697b9f2476be5fc71f28e052ca4e97b1bebe05636123",
}


@pytest.mark.parametrize("name", LEGAL_COPY_SHA256)
def test_legal_copy_remains_byte_for_byte_identical(name):
    source = (COPY_DIR / f"{name}.html").read_bytes()
    assert sha256(source).hexdigest() == LEGAL_COPY_SHA256[name]

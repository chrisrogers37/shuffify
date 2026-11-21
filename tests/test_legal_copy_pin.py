"""The canonical legal copy cannot change without a deliberate hash update."""

from hashlib import sha256
from pathlib import Path

import pytest

COPY_DIR = Path(__file__).resolve().parent.parent / "assets" / "legal"
# Independent baselines of the canonical legal copy. The frontend regenerates its
# prerendered legal sources from these fragments, so regenerating cannot bless a
# legal-text change: an intended edit updates the hash in the same change.
LEGAL_COPY_SHA256 = {
    "terms": "5aacba77af14d83914edec510f03cb11c236ef1d1fcf3f464c807a77227d7faa",
    "privacy": "396f4c3da2a66f23f59c706570c1d9ab3d5151cfa3caf0ab1eba197da0f3ed3a",
}


@pytest.mark.parametrize("name", LEGAL_COPY_SHA256)
def test_legal_copy_remains_byte_for_byte_identical(name):
    source = (COPY_DIR / f"{name}.html").read_bytes()
    assert sha256(source).hexdigest() == LEGAL_COPY_SHA256[name]

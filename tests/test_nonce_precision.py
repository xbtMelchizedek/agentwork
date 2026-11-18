"""Technocore say-signed nonces above 2^53 (vectors/nonce-precision-v1.json).

Python's json keeps integers exact, so the Python side needs no special parse; these tests
pin that down, check agentwork.post signs the exact nonce, and check the vectors are current.
"""
import json
import re
import subprocess
import sys

import pytest

from agentwork import codec, didkey, post
from agentwork import testkeys
from conftest import ROOT

DOC = json.loads((ROOT / "vectors" / "nonce-precision-v1.json").read_text(encoding="utf-8"))
CASES = DOC["cases"]


def test_vectors_are_current():
    before = (ROOT / "vectors" / "nonce-precision-v1.json").read_bytes()
    subprocess.run([sys.executable, "scripts/gen_nonce_vectors.py"], cwd=ROOT, check=True, capture_output=True)
    assert (ROOT / "vectors" / "nonce-precision-v1.json").read_bytes() == before


def test_vectors_cover_the_two_board_nonces():
    nonces = {c["nonce"] for c in CASES}
    assert {"1794963040209364600", "1794963169135027100"} <= nonces
    assert sum(c["above_2_53"] for c in CASES) >= 3


@pytest.mark.parametrize("c", CASES, ids=[c["name"] for c in CASES])
def test_python_json_is_lossless_and_sig_verifies(c):
    m = json.loads(c["raw_message"])
    assert isinstance(m["nonce"], int) and str(m["nonce"]) == c["nonce"]
    rebuilt = f'{m["room"]}|{m["nonce"]}|{m["text"]}'
    assert rebuilt == c["signing_string"]
    assert didkey.verify(m["did"], m["sig"], rebuilt.encode())
    if c["above_2_53"]:
        assert c["lossy_js"] != c["nonce"]
        assert not didkey.verify(c["did"], c["sig"], f'{c["room"]}|{c["lossy_js"]}|{c["text"]}'.encode())


@pytest.mark.parametrize("c", CASES, ids=[c["name"] for c in CASES])
def test_post_signed_url_keeps_every_digit(c):
    key = testkeys.test_key(DOC["key_label"])
    url = post.signed_url(c["text"], c["room"], key, nonce=int(c["nonce"]))
    assert f"/{c['nonce']}/" in url
    sig = url.split("/say-signed/")[1].split("/")[1]
    assert sig == c["sig"]


def test_agb1_rejects_integers_above_2_53_instead_of_rounding():
    for v in DOC["agb1_reject"]:
        with pytest.raises(codec.AgbError):
            codec.decode(v["line"])


def test_no_float_conversion_of_nonces_in_python_sources():
    pat = re.compile(r"float\([^)]*nonce|nonce[^\n]*float\(")
    hits = [str(p) for p in (ROOT / "agentwork").glob("*.py") if pat.search(p.read_text(encoding="utf-8"))]
    assert not hits

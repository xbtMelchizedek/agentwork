import re
import shutil
import subprocess

import pytest

from conftest import ROOT


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_typescript_codec_matches_vectors():
    r = subprocess.run(["node", "ts/test.mjs"], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr


def tracked_text_files():
    skip = {".git", "__pycache__", ".pytest_cache", "node_modules"}
    for p in ROOT.rglob("*"):
        if p.is_file() and not (set(p.relative_to(ROOT).parts) & skip) and p.suffix not in {".pyc", ".seed"}:
            yield p


def test_no_secret_shaped_strings():
    pats = [re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
            re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}"), re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
            re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}"), re.compile(r"\bsk-[A-Za-z0-9_-]{20,}")]
    hits = [(str(p), rx.pattern) for p in tracked_text_files() for rx in pats if rx.search(p.read_text(errors="ignore"))]
    assert not hits


def test_no_seed_files_in_repo():
    assert not [p for p in tracked_text_files() if p.suffix in {".seed", ".key", ".pem"}]

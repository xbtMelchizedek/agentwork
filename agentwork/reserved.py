"""Reserved handles (SPEC §10.3): platform names, sacred names, blasphemy, slurs, obscene terms.

The list lives in `reserved_handles.txt` (data, not code) so apps can audit and extend it.
`reason(name)` returns why a handle or guild name can never be claimed, or None.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from . import codec

DATA = Path(__file__).with_name("reserved_handles.txt")
SECTIONS = ("system", "sacred", "blasphemy", "insult", "obscene", "obscene_exact", "slur", "slur_exact",
            "affix_prefix", "affix_suffix")


def light_fold(name: str) -> str:
    """Single-character confusable fold only (used for substring checks)."""
    return name.lower().translate(codec._FOLD)


def parse(text: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {s: [] for s in SECTIONS}
    cur = None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip().lower()
        if not line:
            continue
        m = re.fullmatch(r"\[([a-z_]+)\]", line)
        if m:
            cur = m.group(1)
            if cur not in out:
                raise ValueError(f"unknown section [{cur}]")
            continue
        if cur is None or not re.fullmatch(r"[a-z0-9_-]+", line):
            raise ValueError(f"bad reserved-list line {raw!r}")
        out[cur].append(line)
    return out


@lru_cache(maxsize=None)
def _lists(text: str | None = None):
    d = parse(text if text is not None else DATA.read_text(encoding="utf-8"))
    sk = {k: frozenset(codec.handle_skeleton(w) for w in v) for k, v in d.items()}
    lf = {k: tuple(sorted({light_fold(w) for w in v}, key=len, reverse=True)) for k, v in d.items()}
    return sk, lf


def _plain(name: str) -> set[str]:
    """Skeleton of the name and of the name without trailing digits/separators."""
    base = name.lower()
    cands = {codec.handle_skeleton(base)}
    trimmed = re.sub(r"[0-9_-]+$", "", base)
    if trimmed and trimmed != base:
        cands.add(codec.handle_skeleton(trimmed))
    return cands


def _cores(name: str, sk: dict) -> set[str]:
    """_plain() plus one common prefix and/or suffix stripped (for system and sacred names only)."""
    cands = _plain(name)
    for c in list(cands):
        for p in sk["affix_prefix"]:
            if c.startswith(p) and len(c) > len(p):
                cands.add(c[len(p):])
        for s in sk["affix_suffix"]:
            if c.endswith(s) and len(c) > len(s):
                cands.add(c[: -len(s)])
    for c in list(cands):                          # prefix and suffix together (realjesusofficial)
        for p in sk["affix_prefix"]:
            if c.startswith(p):
                for s in sk["affix_suffix"]:
                    if c.endswith(s) and len(c) > len(p) + len(s):
                        cands.add(c[len(p): -len(s)])
    return cands


def reason(name: str, text: str | None = None) -> str | None:
    sk, lf = _lists(text)
    cores = _cores(name, sk)
    plain = _plain(name)
    flat = light_fold(name)
    if cores & sk["system"]:
        return "reserved name"
    if cores & sk["sacred"]:
        return "reserved: sacred name"
    if any(w in flat for w in lf["blasphemy"]):
        return "reserved: blasphemy"
    if any(w in flat for w in lf["sacred"]) and any(w in flat for w in lf["insult"] + lf["obscene"]):
        return "reserved: blasphemy"
    if any(w in flat for w in lf["slur"]) or plain & sk["slur_exact"]:
        return "reserved: slur"
    if any(w in flat for w in lf["obscene"]) or plain & sk["obscene_exact"]:
        return "reserved: obscene"
    return None


def is_reserved(name: str) -> bool:
    return reason(name) is not None

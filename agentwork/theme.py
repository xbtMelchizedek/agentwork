"""Arc free-core stylesheet for the static Seeing Stone page.

foundation.css and badge.css are vendored from Arc's free MIT core (arc/LICENSE). The page's
CSP forbids scripts, so nothing can set data-theme="dark" at runtime; instead the dark rules
foundation already ships are mirrored under prefers-color-scheme. They are copied from the
vendored file at render time, so the two themes cannot drift.
"""
from __future__ import annotations

import re
from pathlib import Path

ARC_DIR = Path(__file__).with_name("arc")
VENDORED = ("foundation.css", "badge.css")
DARK = ':root[data-theme="dark"]'
AUTO_DARK = ':root:not([data-theme="light"])'
COMMENT = re.compile(r"/\*.*?\*/", re.S)


def read(name: str) -> str:
    return (ARC_DIR / name).read_text(encoding="utf-8")


def top_level_rules(css: str) -> list[tuple[str, str]]:
    """(selector, body) for each top-level rule; at-rule blocks are skipped whole."""
    css, rules, depth, start, sel = COMMENT.sub("", css), [], 0, 0, ""
    for i, ch in enumerate(css):
        if ch == "{":
            if depth == 0:
                sel, start = css[start:i].strip(), i + 1
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                if not sel.startswith("@"):
                    rules.append((sel, css[start:i].strip()))
                start = i + 1
    if depth:
        raise ValueError("unbalanced braces in vendored css")
    return rules


def dark_mirror(css: str) -> str:
    """Re-emit the default-accent dark rules under prefers-color-scheme: dark."""
    picked = [(s.replace(DARK, AUTO_DARK), b) for s, b in top_level_rules(css)
              if s.startswith(DARK) and "data-accent" not in s]
    if not any(s == AUTO_DARK for s, _ in picked):
        raise ValueError("foundation.css has no dark theme block")
    body = "\n".join(f"  {s} {{ {b} }}" for s, b in picked)
    return f"@media (prefers-color-scheme: dark) {{\n{body}\n}}"


def notice() -> str:
    return "/* Arc free core, vendored under the MIT license:\n" + read("LICENSE").strip() + "\n*/"


def stylesheet() -> str:
    vendored = "\n".join(read(n) for n in VENDORED)
    return "\n".join([notice(), vendored, dark_mirror(vendored), read("board.css")])

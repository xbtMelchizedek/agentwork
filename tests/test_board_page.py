"""Seeing Stone page in Arc's free core: landmarks, tokens, themes, motion, contrast, license."""
import math
import re
from html.parser import HTMLParser

import pytest

from agentwork import codec, page, render, theme
from agentwork.indexer import Index, RoomPolicy
from conftest import ROOT

T = 1794900000000
RAW_COLOR = re.compile(r"#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?|oklch|oklab|lab|lch)\(")


class Outline(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags, self.headings, self.badges, self._badge, self._depth = [], [], [], None, 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        self.tags.append((tag, a))
        if re.fullmatch(r"h[1-6]", tag):
            self.headings.append(int(tag[1]))
        if tag == "span" and self._badge is not None:
            self._depth += 1
        elif tag == "span" and (a.get("class") or "").split()[:1] == ["badge"]:
            self._badge, self._depth = [], 1

    def handle_data(self, data):
        if self._badge is not None:
            self._badge.append(data)

    def handle_endtag(self, tag):
        if tag == "span" and self._badge is not None:
            self._depth -= 1
            if self._depth == 0:
                self.badges.append("".join(self._badge).strip())
                self._badge = None


@pytest.fixture
def board(keys):
    idx = Index()
    ask = codec.build(keys["a"], "ask", "core/x", {"task": "say hi", "budget": {"asset": "FLOP", "max": "1"}}, ts=T, n="a" * 16)
    offer = codec.build(keys["b"], "offer", "core/x", {"price": {"asset": "FLOP", "amt": "1"}}, ref=ask["cid"], ts=T + 1, n="b" * 16)
    for env in (ask, offer):
        idx.add_line(codec.encode(env))
    reps = idx.apply_policy(RoomPolicy(), T + 10)
    return render.page_html(idx, T + 10, reps)


def our_css() -> str:
    return theme.read("board.css")


def test_page_has_landmarks_one_h1_and_ordered_headings(board):
    o = Outline(); o.feed(board)
    names = [t for t, _ in o.tags]
    assert names.count("header") == 1 and names.count("main") == 1 and names.count("footer") == 1
    assert o.headings.count(1) == 1 and o.headings[0] == 1
    assert all(b - a <= 1 for a, b in zip(o.headings, o.headings[1:])), o.headings
    assert ("html", {"lang": "en"}) in o.tags
    assert all("aria-label" in a for t, a in o.tags if t == "section")


def test_scroll_regions_are_keyboard_reachable_and_named(board):
    o = Outline(); o.feed(board)
    wraps = [a for t, a in o.tags if "table-wrap" in (a.get("class") or "")]
    assert wraps and all(a.get("tabindex") == "0" and a.get("role") == "region" and a.get("aria-label") for a in wraps)
    assert all(a.get("scope") == "col" for t, a in o.tags if t == "th")


def test_status_is_text_not_colour_alone():
    for status, (label, tone) in page.STATUS_BADGE.items():
        html_ = page.badge(status)
        assert label in html_ and f'class="badge {tone}"' in html_
        assert label[0].isupper() and label[1:] == label[1:].lower()  # sentence case
    with pytest.raises(KeyError):
        page.badge("rugged")


def test_every_rendered_badge_has_a_label(board):
    o = Outline(); o.feed(board)
    assert o.badges and all(b.startswith("Status:") and len(b) > len("Status:") for b in o.badges)


def test_csp_no_script_no_links_no_external_fonts(board):
    assert page.CSP in board and "<script" not in board and "href=" not in board
    assert "@import" not in board and "url(" not in board and "https://" not in board


def test_our_css_uses_tokens_only():
    css = theme.COMMENT.sub("", our_css())
    assert not RAW_COLOR.search(css), RAW_COLOR.findall(css)
    badge = theme.COMMENT.sub("", theme.read("badge.css"))
    assert not RAW_COLOR.search(badge)


def test_no_bold_uppercase_gradients_or_glows():
    for css in (our_css(), theme.read("badge.css")):
        weights = set(re.findall(r"font-weight:\s*(\w+)", css))
        assert weights <= {"400", "500"}, weights
        assert "uppercase" not in css and "gradient(" not in css and "box-shadow" not in css and "text-shadow" not in css
    assert "<b>" not in render.page_html(Index(), T, {}) and "<strong" not in render.page_html(Index(), T, {})


def test_every_transition_has_a_reduced_motion_branch():
    for css in (our_css(), theme.read("badge.css")):
        moving = {m for m in re.findall(r"\.([\w-]+)\s*\{[^}]*\b(?:transition|animation)\s*:", theme.COMMENT.sub("", css))}
        reduce = re.search(r"@media \(prefers-reduced-motion: reduce\)\s*\{(.*?)\n\}", css, re.S)
        assert moving and reduce, "no motion or no reduced-motion branch"
        assert all(f".{c}" in reduce.group(1) for c in moving), moving


def test_dark_theme_follows_system_and_mirrors_foundation(board):
    assert "@media (prefers-color-scheme: dark)" in board and '<meta name=color-scheme content="light dark">' in board
    rules = dict(theme.top_level_rules(theme.read("foundation.css")))
    dark = rules[theme.DARK]
    assert f"{theme.AUTO_DARK} {{ {dark} }}" in board
    assert f"{theme.AUTO_DARK} .badge" in board


def test_dark_mirror_fails_closed_without_a_dark_block():
    with pytest.raises(ValueError):
        theme.dark_mirror(":root { --background: white; }")
    with pytest.raises(ValueError):
        theme.top_level_rules(":root { --a: 1;")


def test_focus_ring_comes_from_arc_foundation(board):
    assert ":focus-visible" in board and ":focus:not(:focus-visible)" in board
    assert "outline: none" not in our_css() and "outline:none" not in our_css()


def test_arc_license_is_next_to_vendored_files_and_in_the_page(board):
    lic = (theme.ARC_DIR / "LICENSE").read_text()
    assert "MIT License" in lic and "Copyright (c) 2026 Elia Kuratli" in lic and "Permission is hereby granted" in lic
    assert lic.strip() in board
    vendored = " ".join(theme.read(n) for n in theme.VENDORED)
    assert "uiarc-pro" not in vendored.lower() and "arc_pro" not in vendored.lower()


def test_page_is_deterministic_and_matches_committed_example(board, keys):
    idx = Index()
    env = codec.build(keys["a"], "note", "core/x", {"text": "hi"}, ts=T, n="c" * 16)
    idx.add_line(codec.encode(env))
    reps = idx.apply_policy(RoomPolicy(), T)
    assert render.page_html(idx, T, reps) == render.page_html(idx, T, reps)
    committed = (ROOT / "examples" / "out" / "index.html").read_text()
    assert committed.count("<h1>") == 1 and theme.stylesheet() in committed


def test_empty_board_says_what_happens_next():
    html_ = render.page_html(Index(), T, {})
    assert "No jobs yet" in html_ and "No settled and attested work yet" in html_ and "—" not in html_


# ── contrast: Arc roles resolved from the vendored foundation, both themes ──

def oklch_to_oklab(s: str) -> tuple[float, float, float]:
    m = re.fullmatch(r"oklch\(\s*([\d.]+)%\s+([\d.]+)\s+([\d.]+)\s*(?:/\s*[\d.]+)?\)", s.strip())
    assert m, s
    L, C, H = float(m[1]) / 100, float(m[2]), math.radians(float(m[3]))
    return L, C * math.cos(H), C * math.sin(H)


def oklab_luminance(lab) -> float:
    L, a, b = lab
    l_, m_, s_ = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3, (L - 0.1055613458 * a - 0.0638541728 * b) ** 3, \
        (L - 0.0894841775 * a - 1.2914855480 * b) ** 3
    r = 4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_
    g = -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_
    bl = -0.0041960863 * l_ - 0.7034186147 * m_ + 1.7076147010 * s_
    r, g, bl = (min(1.0, max(0.0, c)) for c in (r, g, bl))
    return 0.2126 * r + 0.7152 * g + 0.0722 * bl


def roles(theme_name: str) -> dict:
    rules = theme.top_level_rules(theme.read("foundation.css"))
    out = {}
    for sel, body in rules:
        if sel == ":root" or (theme_name == "dark" and sel == theme.DARK):
            out.update(dict(re.findall(r"(--[\w-]+):\s*([^;]+);", body)))
    return out


def resolve(v: str, r: dict) -> tuple[float, float, float]:
    v = v.strip()
    while v.startswith("var("):
        v = r[re.fullmatch(r"var\((--[\w-]+)\)", v)[1]].strip()
    return oklch_to_oklab(v)


def mix(a, b, p: float):
    return tuple(p * x + (1 - p) * y for x, y in zip(a, b))


def ratio(fg, bg) -> float:
    hi, lo = sorted((oklab_luminance(fg), oklab_luminance(bg)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


@pytest.mark.parametrize("theme_name", ["light", "dark"])
def test_text_pairs_we_use_meet_aa(theme_name):
    r = roles(theme_name)
    pairs = [("--foreground", "--background"), ("--foreground", "--surface"), ("--foreground", "--surface-muted"),
             ("--text-secondary", "--background"), ("--text-secondary", "--surface"), ("--text-secondary", "--surface-muted")]
    for fg, bg in pairs:
        assert ratio(resolve(r[fg], r), resolve(r[bg], r)) >= 4.5, (theme_name, fg, bg)


@pytest.mark.parametrize("theme_name", ["light", "dark"])
def test_badge_labels_meet_aa(theme_name):
    r = roles(theme_name)
    rules = dict(theme.top_level_rules(theme.read("badge.css")))

    def pct(sel, var):
        m = re.search(rf"{var}:\s*([\d.]+)%", rules.get(sel, ""))
        return float(m[1]) / 100 if m else None

    dark = theme_name == "dark"
    tint = (dark and pct(f"{theme.DARK} .badge", "--badge-tint")) or pct(".badge", "--badge-tint")
    surface, fg = resolve(r["--surface"], r), resolve(r["--foreground"], r)
    for status, (_, tone) in page.STATUS_BADGE.items():
        tone_c = resolve(r["--text-secondary" if tone == "neutral" else f"--{tone}"], r)
        # dark .badge rule outranks .badge.<tone> (specificity 0,3,0 vs 0,2,0), as in Arc's module
        ink = (dark and pct(f"{theme.DARK} .badge", "--badge-ink")) or pct(f".badge.{tone}", "--badge-ink") \
            or pct(".badge", "--badge-ink")
        assert ratio(mix(tone_c, fg, ink), mix(tone_c, surface, tint)) >= 4.5, (theme_name, status)

"""The demo pages have to parse, because nobody here can look at them.

This project is developed without a browser attached, which means every UI
claim is an inference from served bytes. That has already gone wrong twice: a
placeholder string leaked into a template literal, killed the whole `<script>`
block, and the page loaded with zero scenarios; and a stray `</div>` closed the
navbar's container early, leaving the notification bell outside it. Every
server-side test passed both times, because the server was fine.

So these are the checks a browser would have made:

- every inline script is valid JavaScript, parsed by node;
- the static markup nests correctly, walked by a real parser;
- every inline SVG is well-formed XML, because a malformed path renders
  nothing and looks exactly like a missing icon;
- the elements the scripts address actually exist, since `$("#thing")` on a
  typo is a silent null that only surfaces when a user clicks.

Node is optional. Where it is missing the JS parse is skipped rather than
faked, and everything else still runs.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PAGES = ["demo/index.html", "demo/console.html"]

SCRIPT = re.compile(r"<script\b([^>]*)>(.*?)</script>", re.S | re.I)
SCRIPT_BLOCK = re.compile(r"<script\b[^>]*>.*?</script>", re.S | re.I)
STYLE_BLOCK = re.compile(r"<style\b[^>]*>.*?</style>", re.S | re.I)
SVG = re.compile(r"<svg\b.*?</svg>", re.S | re.I)

# Tags that never carry a closing form, HTML voids plus the SVG primitives
# these pages draw with.
VOID = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
    "path", "circle", "rect", "line", "polyline", "polygon", "ellipse", "use",
}


def _read(page: str) -> str:
    return (ROOT / page).read_text(encoding="utf-8")


def _inline_scripts(html: str) -> list[str]:
    """Inline script bodies only. A `src=` script has no body to parse."""
    return [body for attrs, body in SCRIPT.findall(html)
            if "src=" not in attrs.lower() and body.strip()]


def _static_markup(raw: str) -> str:
    """The page with script and style bodies blanked, line numbers preserved.

    Blanked rather than deleted so a reported line number still points at the
    right line of the real file.
    """
    def blank(match: re.Match) -> str:
        return re.sub(r"[^\n]", " ", match.group(0))

    return STYLE_BLOCK.sub(blank, SCRIPT_BLOCK.sub(blank, raw))


@pytest.mark.parametrize("page", PAGES)
def test_every_inline_script_is_valid_javascript(page):
    """Parsed by node, which is the only thing that settles it.

    A syntax error anywhere in a block silently discards that entire block, so
    the page still loads, still looks like a page, and does nothing.
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not installed; every other check here still runs")

    bodies = _inline_scripts(_read(page))
    assert bodies, f"{page} has no inline script, which is unexpected"

    for i, body in enumerate(bodies):
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                         encoding="utf-8") as handle:
            handle.write(body)
            path = handle.name
        try:
            done = subprocess.run([node, "--check", path],
                                  capture_output=True, text=True)
            assert done.returncode == 0, (
                f"{page} inline script #{i + 1} does not parse:\n"
                f"{done.stderr.strip()[:800]}")
        finally:
            Path(path).unlink(missing_ok=True)


@pytest.mark.parametrize("page", PAGES)
def test_the_markup_nests_correctly(page):
    """Every tag closes the tag it actually opened.

    Counting `<div>` against `</div>` is not enough, and is worse than nothing:
    these pages build markup inside JavaScript strings, so a naive count reads
    those too and reports imbalances that do not exist. Walking the static
    markup with a real parser is what found the stray `</div>` that put the
    notification bell outside the navbar.
    """
    problems: list[str] = []

    class Walker(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.stack: list[tuple[str, int]] = []

        def handle_starttag(self, tag, attrs):
            if tag not in VOID:
                self.stack.append((tag, self.getpos()[0]))

        def handle_endtag(self, tag):
            if tag in VOID:
                return
            if not self.stack:
                problems.append(f"line {self.getpos()[0]}: </{tag}> with nothing open")
                return
            if self.stack[-1][0] != tag:
                problems.append(
                    f"line {self.getpos()[0]}: </{tag}> closes, but the innermost "
                    f"open tag is <{self.stack[-1][0]}> from line {self.stack[-1][1]}")
                for i in range(len(self.stack) - 1, -1, -1):
                    if self.stack[i][0] == tag:
                        del self.stack[i:]
                        return
                return
            self.stack.pop()

    walker = Walker()
    walker.feed(_static_markup(_read(page)))

    assert not problems, f"{page}: " + "; ".join(problems)
    assert not walker.stack, (
        f"{page}: never closed "
        + ", ".join(f"<{tag}> from line {line}" for tag, line in walker.stack))


@pytest.mark.parametrize("page", PAGES)
def test_every_inline_svg_is_well_formed(page):
    """A broken icon and a missing icon look identical on a page nobody opened.

    Not every page carries one, so an absent SVG is fine; a malformed one is
    not. This exists because the notification icon shipped as the wrong
    character once already, and its replacement is hand-written SVG.
    """
    for i, svg in enumerate(SVG.findall(_read(page))):
        try:
            ET.fromstring(svg)
        except ET.ParseError as exc:
            raise AssertionError(
                f"{page} SVG #{i + 1} is not well formed: {exc}") from exc


def test_the_notification_bell_is_a_real_drawn_icon():
    """The bell was once the character for WHITE SUN WITH RAYS.

    A stroked path that does not parse renders nothing, which on an unopened
    page is indistinguishable from an icon that is simply not there.
    """
    bell = re.search(r'<button class="bell".*?</button>', _read("demo/index.html"), re.S)
    assert bell, "no bell button in the page"
    svg = SVG.search(bell.group(0))
    assert svg, "the bell button carries no SVG"

    root = ET.fromstring(svg.group(0))
    paths = [el for el in root.iter() if el.tag.endswith("path")]
    assert paths, "the bell SVG draws nothing"
    assert all(el.get("d") for el in paths), "a bell path has no geometry"


def test_the_notification_bell_sits_inside_the_navbar_container():
    """Where the stray `</div>` actually hurt.

    The bell lived after the close of `.wrap`, so it rendered outside the
    navbar's layout container. Nothing server-side could see that, and nobody
    on this end has a browser to look.
    """
    nav = re.search(r"<nav>.*?</nav>", _read("demo/index.html"), re.S)
    assert nav, "no <nav> in the page"
    wrap = re.search(r'<div class="wrap">(.*)', nav.group(0), re.S)
    assert wrap and 'id="bell"' in wrap.group(1), (
        "the notification bell is outside the navbar's .wrap container")


@pytest.mark.parametrize("page", PAGES)
def test_every_element_the_script_addresses_exists(page):
    """`$("#typo")` is a silent null that only shows up when a user clicks.

    Only ids used through the page's own `$` helper are checked, so an id
    built dynamically is not mistaken for a missing one.
    """
    html = _read(page)
    ids = set(re.findall(r'\bid="([A-Za-z][\w-]*)"', html))
    addressed = set(re.findall(r'\$\("#([A-Za-z][\w-]*)"\)', html))
    missing = addressed - ids
    assert not missing, (
        f"{page} script addresses ids that are not in the markup: {sorted(missing)}")


def test_the_console_ships_empty_and_fills_itself_once_signed_in():
    """A page that renders alerts and then hides them with CSS has still
    served them to whoever opened it."""
    html = _read("demo/console.html")
    assert "/api/console/feed" in html, "the console never fetches its feed"
    assert "X-Parchi-Console-Session" in html, "the console sends no session header"

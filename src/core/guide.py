"""The in-app user guide: pages shipped inside the app as plain text files in assets/guide/.

Each page file starts with a few `key: value` lines (id, title, part, part_title, order), then a blank line, then the
page written in Markdown. Links inside a page are normal Markdown links with one of two targets:
  guide:<page-id>        jump to another page of the guide
  app:<place>            open a part of the app (see APP_TARGETS)
Everything is bundled and works offline, so a bad guide page can never break the app: a page that cannot be read is
skipped and logged, and `problems()` reports anything inconsistent (used by the tests, never at run time).
"""
import os
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .logger import get_logger

logger = get_logger("Guide")

GUIDE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "assets", "guide"))

# Places a guide link may open outside the guide. The window maps each one to a real screen.
APP_TARGETS = {
    "settings/cookies",
    "settings/ai-models",
    "settings/mcp",
    "settings/shortcuts",
    "settings/data-vault",
    "settings/core-engines",
    "footage",
}

_LINK_RE = re.compile(r"\]\(((?:guide|app):[^)\s]+)\)")
_REQUIRED = ("id", "title", "part", "part_title", "order")


@dataclass(frozen=True)
class Page:
    id: str
    title: str
    part: int
    part_title: str
    order: int
    body: str

    @property
    def links(self) -> List[str]:
        return _LINK_RE.findall(self.body)


def parse_page(text: str) -> Optional[Page]:
    """One page file's text -> Page, or None if its header is missing or wrong."""
    head, sep, body = text.replace("\r\n", "\n").partition("\n\n")
    if not sep:
        return None
    meta: Dict[str, str] = {}
    for line in head.split("\n"):
        key, colon, value = line.partition(":")
        if colon:
            meta[key.strip()] = value.strip()
    if any(not meta.get(k) for k in _REQUIRED):
        return None
    try:
        part, order = int(meta["part"]), int(meta["order"])
    except ValueError:
        return None
    return Page(meta["id"], meta["title"], part, meta["part_title"], order, body.strip())


def load_pages(directory: str = GUIDE_DIR) -> List[Page]:
    """Every readable page in reading order. An unreadable file is skipped, never fatal."""
    pages: List[Page] = []
    try:
        names = sorted(n for n in os.listdir(directory) if n.lower().endswith(".md"))
    except OSError as err:
        logger.warning("Guide folder unreadable (%s): %s", directory, err)
        return pages
    for name in names:
        try:
            with open(os.path.join(directory, name), encoding="utf-8") as fh:
                page = parse_page(fh.read())
        except (OSError, UnicodeDecodeError) as err:
            logger.warning("Guide page %s unreadable: %s", name, err)
            continue
        if page is None:
            logger.warning("Guide page %s has a bad header, skipped", name)
            continue
        pages.append(page)
    pages.sort(key=lambda p: p.order)
    return pages


def parts(pages: List[Page]) -> List[Tuple[int, str, List[Page]]]:
    """Pages grouped by part, for the topic list: [(part number, part title, [pages])]."""
    grouped: Dict[int, Tuple[str, List[Page]]] = {}
    for p in pages:
        grouped.setdefault(p.part, (p.part_title, []))[1].append(p)
    return [(n, title, items) for n, (title, items) in sorted(grouped.items())]


def find(pages: List[Page], page_id: str) -> Optional[Page]:
    return next((p for p in pages if p.id == page_id), None)


def search(pages: List[Page], query: str) -> List[Page]:
    """Pages matching every word of the query, title matches first. Case-insensitive; an empty query returns nothing."""
    words = [w for w in re.split(r"\s+", (query or "").lower().strip()) if w]
    if not words:
        return []
    hits = []
    for p in pages:
        title, body = p.title.lower(), p.body.lower()
        if all(w in title or w in body for w in words):
            hits.append((0 if all(w in title for w in words) else 1, p.order, p))
    return [p for _, _, p in sorted(hits, key=lambda t: (t[0], t[1]))]


def problems(pages: List[Page]) -> List[str]:
    """Anything inconsistent in the guide: duplicate ids, dead links, unknown app targets, draft leftovers."""
    out: List[str] = []
    ids = [p.id for p in pages]
    for dup in {i for i in ids if ids.count(i) > 1}:
        out.append(f"duplicate page id: {dup}")
    known = set(ids)
    for p in pages:
        for target in p.links:
            kind, _, name = target.partition(":")
            if kind == "guide" and name not in known:
                out.append(f"{p.id}: link to a page that does not exist: {target}")
            elif kind == "app" and name not in APP_TARGETS:
                out.append(f"{p.id}: link to an unknown app place: {target}")
        for leftover in ("CONFIRM", "DRAFT", "[[link"):
            if leftover in p.body:
                out.append(f"{p.id}: leftover draft text: {leftover}")
        if not p.body.strip():
            out.append(f"{p.id}: empty page")
    return out

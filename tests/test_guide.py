"""The in-app guide's content: the bundled pages load, every link goes somewhere, and bad files never break anything."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

from src.core import guide

GOOD = "id: a\ntitle: Alpha\npart: 1\npart_title: One\norder: 1\n\nHello **world**.\n"


# -- the real, bundled guide -------------------------------------------------------------------------
@pytest.fixture(scope="module")
def pages():
    return guide.load_pages()


def test_all_fifteen_pages_load_in_reading_order(pages):
    assert [p.order for p in pages] == list(range(1, 16))
    assert len(pages) == 15


def test_the_guide_is_in_three_parts(pages):
    grouped = guide.parts(pages)
    assert [(n, t) for n, t, _ in grouped] == [(1, "The basics"), (2, "Configuration"), (3, "The automation pipeline")]
    assert [len(items) for _, _, items in grouped] == [5, 4, 6]


def test_the_bundled_guide_has_no_dead_links_or_draft_leftovers(pages):
    assert guide.problems(pages) == []


def test_every_page_has_real_content_and_no_pictures(pages):
    for p in pages:
        assert len(p.body) > 200, p.id
        assert "![" not in p.body and "<img" not in p.body.lower(), p.id     # text only, by decision


def test_no_prices_in_the_guide(pages):
    text = " ".join(p.body for p in pages)
    assert "$" not in text and "per month" not in text.lower()


def test_the_pipeline_pages_are_linked_in_order(pages):
    chain = ["footage-manager", "link-footage", "transcribe", "check-voiceover", "connect-ai"]
    for here, nxt in zip(chain, chain[1:]):
        assert f"guide:{nxt}" in guide.find(pages, here).links, here


def test_outside_links_cover_the_places_the_guide_sends_people_to(pages):
    targets = {t for p in pages for t in p.links if t.startswith("app:")}
    assert {"app:settings/cookies", "app:settings/ai-models"} <= targets


# -- finding pages -----------------------------------------------------------------------------------
def test_find_by_id(pages):
    assert guide.find(pages, "connect-ai").title == "Connect an AI assistant"
    assert guide.find(pages, "nope") is None


def test_search_puts_title_matches_first_and_needs_every_word(pages):
    hits = guide.search(pages, "transcribe")
    assert hits and hits[0].id == "transcribe"
    both = guide.search(pages, "MCP assistant")
    assert both and all("mcp" in (p.title + p.body).lower() and "assistant" in (p.title + p.body).lower() for p in both)
    assert guide.search(pages, "zzzzqqq") == []
    assert guide.search(pages, "   ") == [] and guide.search(pages, "") == []


def test_search_is_case_insensitive(pages):
    assert [p.id for p in guide.search(pages, "EDITOR notes")] == [p.id for p in guide.search(pages, "editor NOTES")]


# -- bad input never breaks anything ----------------------------------------------------------------
def test_parse_page_reads_a_good_page():
    p = guide.parse_page(GOOD)
    assert (p.id, p.title, p.part, p.part_title, p.order) == ("a", "Alpha", 1, "One", 1)
    assert p.body == "Hello **world**."


@pytest.mark.parametrize("text", [
    "", "no header at all", "id: a\ntitle: x\n\nbody",                          # missing keys
    GOOD.replace("part: 1", "part: one"), GOOD.replace("order: 1", "order: first"),
])
def test_parse_page_rejects_bad_headers(text):
    assert guide.parse_page(text) is None


def test_load_pages_skips_a_broken_file_and_keeps_the_rest(tmp_path):
    (tmp_path / "01-a.md").write_text(GOOD, encoding="utf-8")
    (tmp_path / "02-bad.md").write_text("garbage", encoding="utf-8")
    (tmp_path / "03-bytes.md").write_bytes(b"\xff\xfe\x00bad")
    (tmp_path / "notes.txt").write_text(GOOD, encoding="utf-8")                  # not a page
    assert [p.id for p in guide.load_pages(str(tmp_path))] == ["a"]


def test_a_missing_guide_folder_gives_an_empty_guide_not_a_crash(tmp_path):
    assert guide.load_pages(str(tmp_path / "does-not-exist")) == []


def test_crlf_files_load_too(tmp_path):
    (tmp_path / "01-a.md").write_bytes(GOOD.replace("\n", "\r\n").encode("utf-8"))
    assert guide.load_pages(str(tmp_path))[0].title == "Alpha"


def test_problems_reports_dead_links_unknown_places_duplicates_and_leftovers():
    base = "id: {i}\ntitle: T\npart: 1\npart_title: P\norder: {o}\n\n{b}"
    pages = [
        guide.parse_page(base.format(i="a", o=1, b="see [x](guide:missing) and [y](app:nowhere) CONFIRM this")),
        guide.parse_page(base.format(i="a", o=2, b="[[link: Old style]]")),
        guide.parse_page(base.format(i="c", o=3, b="fine [ok](guide:c) and [s](app:footage)")),
    ]
    found = "\n".join(guide.problems(pages))
    assert "duplicate page id: a" in found
    assert "guide:missing" in found and "app:nowhere" in found
    assert "CONFIRM" in found and "[[link" in found
    assert "c:" not in found                                                     # the clean page is not blamed

"""The guide window: opens, shows every page, links and history work, search works, bad input never crashes."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QUrl
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication

from src.core import guide
from src.ui.components import guide_window as gw
from src.ui.theme import ThemeManager, ThemeVariant


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture(autouse=True)
def restore_theme():
    v, d = ThemeManager.current_variant, ThemeManager.is_dark
    yield
    ThemeManager.current_variant, ThemeManager.is_dark = v, d


@pytest.fixture
def win(qapp):
    w = gw.GuideWindow()
    yield w
    w.close()


def topic_ids(w):
    out = []
    for i in range(w.tree.topLevelItemCount()):
        top = w.tree.topLevelItem(i)
        for j in range(top.childCount()):
            out.append(top.child(j).data(0, gw.PAGE_ROLE))
    return out


# -- opening and showing -------------------------------------------------------------------------------
def test_it_opens_on_the_first_page_with_all_topics_in_three_parts(win):
    assert win.current_page_id() == "open-script"
    assert win.tree.topLevelItemCount() == 3
    assert len(topic_ids(win)) == 15
    assert "Open a script" in win.browser.document().toPlainText()


def test_it_is_its_own_window_not_a_modal(win):
    assert not win.isModal()


def test_every_page_renders_its_title_and_text(win):
    for p in win.pages:
        assert win.show_page(p.id) is True
        text = win.browser.document().toPlainText()
        assert p.title in text and len(text) > 150, p.id


def test_the_settings_page_shows_its_table(win):
    win.show_page("settings")
    assert "Personalization" in win.browser.document().toPlainText()
    assert win.browser.document().rootFrame().childFrames()          # a real table, not loose text


def test_the_contact_line_is_always_visible(win):
    assert "Message Cane" in win.contact.text()


def test_clicking_a_topic_shows_that_page(win):
    top = win.tree.topLevelItem(2)
    item = top.child(2)                                      # third page of part 3
    win.tree.setCurrentItem(item)
    assert win.current_page_id() == item.data(0, gw.PAGE_ROLE)


# -- links and history ---------------------------------------------------------------------------------
def test_a_guide_link_jumps_to_that_page(win):
    win._on_anchor(QUrl("guide:link-footage"))
    assert win.current_page_id() == "link-footage"


def test_back_and_forward_walk_the_pages_you_visited(win):
    win.show_page("aroll-cards")
    win.show_page("transcribe")
    assert not win.forward_btn.isEnabled() and win.back_btn.isEnabled()
    win.back()
    assert win.current_page_id() == "aroll-cards" and win.forward_btn.isEnabled()
    win.back()
    assert win.current_page_id() == "open-script" and not win.back_btn.isEnabled()
    win.forward()
    win.forward()
    assert win.current_page_id() == "transcribe"


def test_going_somewhere_new_after_back_drops_the_old_forward_pages(win):
    win.show_page("aroll-cards")
    win.show_page("transcribe")
    win.back()
    win.show_page("settings")
    assert not win.forward_btn.isEnabled()


def test_an_outside_link_is_announced_not_opened(win):
    got = []
    win.app_link_requested.connect(got.append)
    win._on_anchor(QUrl("app:settings/ai-models"))
    assert got == ["settings/ai-models"]
    assert win.current_page_id() == "open-script"            # the guide stays where it was


def test_unknown_or_web_links_are_ignored(win):
    got = []
    win.app_link_requested.connect(got.append)
    for bad in ("app:nowhere", "https://example.com", "guide:no-such-page", "mailto:x@y.z", ""):
        win._on_anchor(QUrl(bad))
    assert got == []
    assert win.current_page_id() == "open-script" or win.browser.document().toPlainText() == gw.NOT_FOUND


def test_a_missing_page_shows_a_message_and_does_not_crash(win):
    assert win.show_page("no-such-page") is False
    assert win.browser.document().toPlainText() == gw.NOT_FOUND


def test_real_links_inside_pages_point_at_something(win):
    """Every link in the bundled pages is one this window can follow."""
    ids = {p.id for p in win.pages}
    for p in win.pages:
        for target in p.links:
            kind, _, name = target.partition(":")
            assert (kind == "guide" and name in ids) or (kind == "app" and name in guide.APP_TARGETS), (p.id, target)


# -- search --------------------------------------------------------------------------------------------
def test_search_narrows_the_list_and_clearing_it_brings_everything_back(win):
    win.search.setText("transcribe")
    ids = topic_ids(win) if win.tree.topLevelItemCount() and win.tree.topLevelItem(0).childCount() else [
        win.tree.topLevelItem(i).data(0, gw.PAGE_ROLE) for i in range(win.tree.topLevelItemCount())]
    assert "transcribe" in ids and len(ids) < 15
    win.search.setText("")
    assert win.tree.topLevelItemCount() == 3 and len(topic_ids(win)) == 15


def test_a_search_with_no_matches_says_so(win):
    win.search.setText("zzzzqqqq")
    assert win.tree.topLevelItemCount() == 1
    assert win.tree.topLevelItem(0).text(0) == gw.NO_MATCH


def test_clicking_a_search_result_opens_it(win):
    win.search.setText("MCP")
    first = win.tree.topLevelItem(0)
    win.tree.setCurrentItem(first)
    assert win.current_page_id() == first.data(0, gw.PAGE_ROLE)


# -- look and robustness -------------------------------------------------------------------------------
@pytest.mark.parametrize("dark", [True, False])
def test_it_follows_light_and_dark(win, dark):
    ThemeManager.apply_theme(ThemeVariant.GRAY, dark)
    win.apply_theme()
    pal = ThemeManager.get_palette()
    assert win.browser.palette().color(QPalette.Text).name() == pal["text"]
    assert pal["text"] in win.styleSheet()
    assert win.current_page_id() == "open-script"


def test_an_empty_guide_opens_without_crashing(qapp):
    w = gw.GuideWindow(pages=[])
    assert w.browser.document().toPlainText() == gw.NOT_FOUND
    assert w.tree.topLevelItemCount() == 0
    w.close()


def test_a_page_always_opens_at_the_top(qapp):
    w = gw.GuideWindow()
    w.show()
    qapp.processEvents()
    w.show_page("settings")                                   # a long page
    qapp.processEvents()
    assert w.browser.verticalScrollBar().maximum() > 0       # it really is longer than the window
    w.browser.verticalScrollBar().setValue(w.browser.verticalScrollBar().maximum())
    w.show_page("transcribe")
    qapp.processEvents()
    assert w.browser.verticalScrollBar().value() == 0
    w.close()

"""The guide window: opens, shows every page, links and history work, search works, bad input never crashes."""
import gc
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QUrl
from PySide6.QtGui import QColor, QPalette
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


def switch_theme(dark: bool) -> None:
    """qfluentwidgets walks a weak registry of widgets while it restyles them; if Python's garbage collector frees an
    old test window in the middle of that walk, the walk fails ("dictionary changed size"). Collect first, then keep
    the collector off for the moment the theme changes."""
    gc.collect()
    gc.disable()
    try:
        ThemeManager.apply_theme(ThemeVariant.GRAY, dark)
    finally:
        gc.enable()


@pytest.fixture
def win(qapp):
    w = gw.GuideWindow()
    yield w
    w.close()
    w.deleteLater()


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
    switch_theme(dark)
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


@pytest.mark.parametrize("dark", [True, False])
def test_links_use_the_theme_accent_so_they_are_readable(win, dark):
    switch_theme(dark)
    win.apply_theme()
    win.show_page("aroll-cards")
    doc = win.browser.document()
    accent = ThemeManager.get_palette()["accent"].lower()
    seen = 0
    block = doc.begin()
    while block.isValid():
        it = block.begin()
        while not it.atEnd():
            frag = it.fragment()
            if frag.isValid() and frag.charFormat().isAnchor():
                seen += 1
                assert frag.charFormat().foreground().color().name().lower() == accent
            it += 1
        block = block.next()
    assert seen >= 2                                           # the page really has links, so this checked something


# -- the look changes: search in the title bar, white window buttons on dark, roomier spacing --------------
def test_the_search_box_lives_in_the_title_bar_centred(qapp):
    w = gw.GuideWindow()
    w.show()
    qapp.processEvents()
    assert w.search.parent() is w.titleBar
    mid = w.search.x() + w.search.width() / 2
    assert abs(mid - w.width() / 2) <= 2                                   # centred
    assert w.search.geometry().right() < w.titleBar.minBtn.geometry().left()   # clear of the window buttons
    assert w.search.y() >= 0 and w.search.geometry().bottom() <= w.titleBar.height()
    w.resize(700, 500)
    qapp.processEvents()
    assert abs((w.search.x() + w.search.width() / 2) - w.width() / 2) <= 2     # and stays centred when resized
    w.close()


def test_search_still_works_from_the_title_bar(win):
    win.search.setText("transcribe")
    assert win.tree.topLevelItemCount() >= 1 and win.tree.topLevelItemCount() < 15


@pytest.mark.parametrize("dark", [True, False])
def test_window_buttons_are_white_on_dark_and_the_text_colour_on_light(win, dark):
    switch_theme(dark)
    win.apply_theme()
    want = "rgba(255, 255, 255, 255)" if dark else None
    if not dark:
        c = QColor(ThemeManager.get_palette()["text"])
        want = f"rgba({c.red()}, {c.green()}, {c.blue()}, {c.alpha()})"
    for btn in (win.titleBar.minBtn, win.titleBar.maxBtn, win.titleBar.closeBtn):
        assert f"qproperty-normalColor: {want}" in btn.styleSheet()


def test_the_page_has_roomy_lines_gaps_and_table_cells(win):
    win.show_page("aroll-cards")
    doc = win.browser.document()
    block = doc.begin()
    seen = 0
    while block.isValid():
        fmt = block.blockFormat()
        if block.text().strip():
            assert fmt.lineHeight() == gw.GuideWindow.LINE_HEIGHT_PERCENT
            seen += 1
        block = block.next()
    assert seen > 5
    from PySide6.QtGui import QTextCursor
    cur, tables, b = QTextCursor(doc), {}, doc.begin()
    while b.isValid():
        cur.setPosition(b.position())
        tbl = cur.currentTable()
        if tbl is not None:
            tables[tbl.firstPosition()] = tbl.format().cellPadding()
        b = b.next()
    assert tables and set(tables.values()) == {gw.GuideWindow.TABLE_CELL_PADDING}


def test_topic_rows_are_roomier_than_before(win):
    assert "padding: 7px 8px" in win.styleSheet()


def test_the_topic_list_has_no_branch_area_to_draw_a_curve_in(win):
    """The tree's branch gutter drew a stray curve beside the selected row: there is no gutter now, and a blank icon
    indents the pages under their part heading instead."""
    assert win.tree.indentation() == 0
    assert "::branch" in win.styleSheet() and "image: none" in win.styleSheet()
    head = win.tree.topLevelItem(0)
    assert head.icon(0).isNull()                                   # headings sit at the left edge
    assert head.childCount() > 0 and all(not head.child(i).icon(0).isNull() for i in range(head.childCount()))

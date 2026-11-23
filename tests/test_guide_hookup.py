"""The user guide inside the running app: the Help menu opens it, links out of the guide reach the right place, and
nothing opens before this install is approved."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from src.core import guide
from src.ui.window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def window(qapp):
    w = MainWindow()
    w._gated = False
    yield w
    for attr in ("guide_window", "pref_window"):
        win = getattr(w, attr, None)
        if win is not None:
            win.close()


def test_the_help_menu_announces_the_guide(window):
    got = []
    window.title_menu.guide_requested.connect(lambda: got.append(1))
    window.title_menu.guide_requested.emit()
    assert got == [1]
    assert window.guide_window.isVisible()                  # and the main window answered by opening it


def test_the_guide_opens_in_its_own_window_once(window):
    window.open_guide()
    first = window.guide_window
    assert first.isVisible() and not first.isModal()
    window.open_guide()
    assert window.guide_window is first                     # focused again, not a second copy


def test_nothing_opens_before_this_install_is_approved(window):
    window._gated = True
    window.open_guide()
    assert getattr(window, "guide_window", None) is None


def test_the_guide_hides_when_the_app_locks(window):
    window.open_guide()
    assert window.guide_window.isVisible()
    window._set_gated(True)
    assert not window.guide_window.isVisible()


def test_every_settings_link_in_the_guide_has_a_tab_to_open(window):
    wanted = {t[len("app:"):] for p in guide.load_pages() for t in p.links if t.startswith("app:settings/")}
    assert wanted and wanted <= set(window.GUIDE_SETTINGS_TABS)


@pytest.mark.parametrize("place,tab_index", [
    ("settings/cookies", 6), ("settings/ai-models", 7), ("settings/mcp", 8),
    ("settings/shortcuts", 5), ("settings/data-vault", 1), ("settings/core-engines", 3),
])
def test_a_settings_link_opens_that_settings_tab(window, place, tab_index):
    window._on_guide_link(place)
    assert window.pref_window.isVisible()
    assert window.pref_window.nav_list.currentRow() == tab_index


def test_the_footage_link_opens_the_footage_manager_when_a_script_is_open(window, monkeypatch):
    opened = []
    monkeypatch.setattr(window, "_on_footage_clicked", lambda: opened.append(1))
    window.current_doc = object()
    window._on_guide_link("footage")
    assert opened == [1]


def test_the_footage_link_asks_for_a_script_first_when_none_is_open(window, monkeypatch):
    opened, told = [], []
    monkeypatch.setattr(window, "_on_footage_clicked", lambda: opened.append(1))
    from src.ui import window as window_module
    monkeypatch.setattr(window_module.InfoBar, "info", lambda **kw: told.append(kw.get("title")))
    window.current_doc = None
    window._on_guide_link("footage")
    assert opened == [] and told == ["Open a script first"]


def test_unknown_or_locked_links_do_nothing(window, monkeypatch):
    called = []
    monkeypatch.setattr(window, "open_settings", lambda *a, **k: called.append(a))
    window._on_guide_link("settings/nowhere")
    window._gated = True
    window._on_guide_link("settings/mcp")
    assert called == []


def test_clicking_a_link_inside_the_guide_window_reaches_the_app(window):
    window.open_guide()
    from PySide6.QtCore import QUrl
    window.guide_window._on_anchor(QUrl("app:settings/mcp"))
    assert window.pref_window.nav_list.currentRow() == 8

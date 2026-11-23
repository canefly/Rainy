"""The ownership notice: friendly, short, the same in both places, and nothing threatening anywhere in the app."""
import sys
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QLabel

from src.ui import legal


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    yield app


def test_the_notice_says_it_is_owned_by_cane_and_not_to_be_passed_on():
    text = legal.NOTICE_TEXT.lower()
    assert "owned by cane" in text and "don't copy" in text and "pass it on" in text
    assert "cane" in legal.NOTICE_TITLE.lower() and "not for redistribution" in legal.COPYRIGHT_LINE


def test_the_notice_does_not_threaten_anyone():
    everything = " ".join((legal.NOTICE_TITLE, legal.NOTICE_TEXT, legal.COPYRIGHT_LINE)).lower()
    for scary in ("prosecution", "liabilit", "statutory", "damages", "violator", "by law", "reverse engineering"):
        assert scary not in everything, scary
    assert len(legal.NOTICE_TEXT) < 250


def test_the_about_window_shows_the_notice(qapp):
    from src.ui.components.about_dialog import AboutWindow
    w = AboutWindow()
    texts = [lbl.text() for lbl in w.findChildren(QLabel)]
    assert legal.NOTICE_TITLE in texts and legal.NOTICE_TEXT in texts and legal.COPYRIGHT_LINE in texts
    assert not any("All rights reserved" in t or "LEGAL NOTICE" in t for t in texts)
    w.close()


def test_settings_about_tab_shows_the_same_notice(qapp):
    from src.ui.components.preferences_dialog import PreferencesWindow
    win = PreferencesWindow(None, initial_tab=0)
    win.switch_tab("about")
    texts = [lbl.text() for lbl in win.findChildren(QLabel)]
    assert legal.NOTICE_TITLE in texts and legal.NOTICE_TEXT in texts
    assert not any("exclusive property" in t or "prohibited by law" in t for t in texts)
    win.close()


def test_the_old_threatening_wording_is_gone_from_the_whole_app():
    root = Path(legal.__file__).resolve().parents[1]
    offenders = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        for phrase in ("immediate legal prosecution", "statutory damages", "maximum civil liabilities", "prohibited by law"):
            if phrase in text:
                offenders.append((path.name, phrase))
    assert offenders == []
